"""Fault-time A0 projection admission and private input diagnostics."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, cast

from pydantic import ValidationError

from ecomsre_live_sandbox.contracts import canonical_sha256, write_private_json
from ecomsre_live_sandbox.e2e_telemetry import (
    ContractBoundedProjectionInputs,
    LiveLogObservation,
    LiveMetricObservation,
    LiveTraceObservation,
    build_live_a0_context,
    scan_model_projection,
    select_contract_bounded_projection_inputs,
)
from ecomsre_live_sandbox.projection_capacity import (
    RCA100_LIVE_PROJECTION_CAPACITY,
)
from ecomsre_rca100.projection import RCA100AgentContext


class FaultProjectionUnavailable(ValueError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


def _metric_is_diagnostic(item: LiveMetricObservation) -> bool:
    return (
        item.fault_errors / item.fault_requests
        > item.baseline_errors / item.baseline_requests
        or item.fault_p95_ms > item.baseline_p95_ms
    )


def _log_is_diagnostic(item: LiveLogObservation) -> bool:
    return item.severity.upper() in {"WARN", "WARNING", "ERROR", "FATAL"} or (
        "error" in item.body.casefold()
    )


def _trace_is_diagnostic(item: LiveTraceObservation) -> bool:
    return item.status.upper() == "ERROR"


def _write_projection_summary(
    *,
    path: Path,
    window_start: datetime,
    window_end: datetime,
    metrics: tuple[LiveMetricObservation, ...],
    logs: tuple[LiveLogObservation, ...],
    traces: tuple[LiveTraceObservation, ...],
    resolvable_refs: frozenset[str],
    projection: Any,
) -> tuple[dict[str, object], ContractBoundedProjectionInputs]:
    diagnostic_metrics = tuple(item for item in metrics if _metric_is_diagnostic(item))
    diagnostic_logs = tuple(item for item in logs if _log_is_diagnostic(item))
    diagnostic_traces = tuple(item for item in traces if _trace_is_diagnostic(item))
    empty_streams = tuple(
        name
        for name, values in (
            ("METRICS", diagnostic_metrics),
            ("LOGS", diagnostic_logs),
            ("TRACES", diagnostic_traces),
        )
        if not values
    )
    reasons: list[str] = []
    if not metrics:
        reasons.append("NO_BROAD_METRICS")
    if not diagnostic_metrics:
        reasons.append("NO_DIAGNOSTIC_METRICS")
    if not diagnostic_logs:
        reasons.append("NO_DIAGNOSTIC_LOGS")
    if not diagnostic_traces:
        reasons.append("NO_DIAGNOSTIC_TRACES")
    if not diagnostic_logs and not diagnostic_traces:
        reasons.append("NO_LOG_OR_TRACE_DIAGNOSTIC_EVIDENCE")
    diagnostic_count = (
        len(diagnostic_metrics) + len(diagnostic_logs) + len(diagnostic_traces)
    )
    resolvable_diagnostic_count = sum(
        item.evidence_ref in resolvable_refs for item in diagnostic_metrics
    ) + sum(item.evidence_ref in resolvable_refs for item in diagnostic_logs) + sum(
        item.evidence_ref in resolvable_refs for item in diagnostic_traces
    )
    if resolvable_diagnostic_count != diagnostic_count:
        reasons.append("INSUFFICIENT_RESOLVABLE_EVIDENCE")
    control_truth_findings = scan_model_projection(
        {
            "metrics": [item.model_dump(mode="json") for item in diagnostic_metrics],
            "logs": [item.model_dump(mode="json") for item in diagnostic_logs],
            "traces": [item.model_dump(mode="json") for item in diagnostic_traces],
        }
    )
    if control_truth_findings:
        reasons.append("CONTROL_TRUTH_LEAK")
    selection = select_contract_bounded_projection_inputs(
        window_start=window_start,
        window_end=window_end,
        metrics=diagnostic_metrics,
        logs=diagnostic_logs,
        traces=diagnostic_traces,
        projection=projection,
    )
    selected_refs = tuple(
        item.evidence_ref
        for source in (selection.metrics, selection.logs, selection.traces)
        for item in source
    )
    all_selected_refs_resolve = all(
        reference is not None and reference in resolvable_refs
        for reference in selected_refs
    )
    if not all_selected_refs_resolve:
        reasons.append("SELECTED_EVIDENCE_REF_UNRESOLVED")
    visible_candidates = {
        item.service_name
        for item in diagnostic_metrics
        if item.evidence_ref in resolvable_refs
    }
    visible_candidates.update(
        item.service_name
        for item in diagnostic_logs
        if item.evidence_ref in resolvable_refs
    )
    visible_candidates.update(
        item.service_name
        for item in diagnostic_traces
        if item.evidence_ref in resolvable_refs
    )
    if len(selection.visible_services) < 3:
        reasons.append("VISIBLE_SERVICE_COUNT_BELOW_MINIMUM")
    payload: dict[str, object] = {
        "schema_version": "live-e2e.projection-input-summary.v6-repro-3",
        "window_start": window_start.isoformat(),
        "window_end": window_end.isoformat(),
        "broad_metrics_count": len(metrics),
        "broad_logs_count": len(logs),
        "broad_traces_count": len(traces),
        "metric_service_count": len({item.service_name for item in metrics}),
        "log_service_count": len({item.service_name for item in logs}),
        "trace_service_count": len({item.service_name for item in traces}),
        "diagnostic_metrics_count": len(diagnostic_metrics),
        "diagnostic_logs_count": len(diagnostic_logs),
        "diagnostic_traces_count": len(diagnostic_traces),
        "anomalous_metric_count": len(diagnostic_metrics),
        "anomalous_log_count": len(diagnostic_logs),
        "error_trace_count": len(diagnostic_traces),
        "metrics_with_resolver_ref": sum(
            item.evidence_ref in resolvable_refs for item in diagnostic_metrics
        ),
        "logs_with_resolver_ref": sum(
            item.evidence_ref in resolvable_refs for item in diagnostic_logs
        ),
        "traces_with_resolver_ref": sum(
            item.evidence_ref in resolvable_refs for item in diagnostic_traces
        ),
        "cross_source_service_overlap_count": len(
            ({item.service_name for item in diagnostic_metrics})
            & (
                {item.service_name for item in diagnostic_logs}
                | {item.service_name for item in diagnostic_traces}
            )
        ),
        "visible_candidate_count": len(visible_candidates),
        "metrics_contract_capacity": (
            RCA100_LIVE_PROJECTION_CAPACITY.metrics_evidence
        ),
        "logs_contract_capacity": RCA100_LIVE_PROJECTION_CAPACITY.source_evidence,
        "traces_contract_capacity": (
            RCA100_LIVE_PROJECTION_CAPACITY.source_evidence
        ),
        "metrics_selected_count": len(selection.metrics),
        "logs_selected_count": len(selection.logs),
        "traces_selected_count": len(selection.traces),
        "metrics_dropped_for_capacity": len(diagnostic_metrics)
        - selection.metrics_capacity_selected_count,
        "logs_dropped_for_capacity": len(diagnostic_logs)
        - selection.logs_capacity_selected_count,
        "traces_dropped_for_capacity": len(diagnostic_traces)
        - selection.traces_capacity_selected_count,
        "services_before_capacity_filter": (
            selection.services_before_capacity_filter
        ),
        "services_after_capacity_filter": selection.services_after_capacity_filter,
        "visible_service_count": len(selection.visible_services),
        "empty_model_streams": empty_streams,
        "reason_codes": tuple(reasons),
        "all_selected_refs_resolve": all_selected_refs_resolve,
        "control_truth_findings": control_truth_findings,
    }
    payload["summary_sha256"] = canonical_sha256(payload)
    write_private_json(path, payload, create_once=True)
    return payload, selection


def _safe_validation_entries(
    error: ValidationError,
    *,
    selection: ContractBoundedProjectionInputs,
) -> tuple[dict[str, object], ...]:
    entries: list[dict[str, object]] = []
    for item in error.errors(include_url=False):
        location = ".".join(str(part) for part in item.get("loc", ()))
        input_value = item.get("input")
        input_count = (
            len(input_value) if isinstance(input_value, (list, tuple)) else None
        )
        context = item.get("ctx")
        contract_capacity = (
            context.get("max_length") if isinstance(context, dict) else None
        )
        if input_count is None:
            input_count = (
                len(selection.metrics)
                if error.title == "RCA100MetricsProjection"
                else max(len(selection.logs), len(selection.traces))
            )
        if contract_capacity is None:
            contract_capacity = (
                RCA100_LIVE_PROJECTION_CAPACITY.metrics_evidence
                if error.title == "RCA100MetricsProjection"
                else RCA100_LIVE_PROJECTION_CAPACITY.source_evidence
            )
        entries.append(
            {
                "model": error.title,
                "field_location": location,
                "error_type": str(item.get("type")),
                "input_count": input_count,
                "contract_capacity": contract_capacity,
            }
        )
    return tuple(entries)


def build_fault_time_a0_context(
    *,
    window_start: datetime,
    window_end: datetime,
    metrics: tuple[LiveMetricObservation, ...],
    logs: tuple[LiveLogObservation, ...],
    traces: tuple[LiveTraceObservation, ...],
    resolvable_refs: frozenset[str],
    projection: Any,
    summary_path: Path,
) -> RCA100AgentContext:
    summary, selection = _write_projection_summary(
        path=summary_path,
        window_start=window_start,
        window_end=window_end,
        metrics=metrics,
        logs=logs,
        traces=traces,
        resolvable_refs=resolvable_refs,
        projection=projection,
    )
    reasons = cast(tuple[str, ...], summary["reason_codes"])
    if "NO_DIAGNOSTIC_METRICS" in reasons:
        raise FaultProjectionUnavailable("NO_DIAGNOSTIC_METRICS")
    if "NO_LOG_OR_TRACE_DIAGNOSTIC_EVIDENCE" in reasons:
        raise FaultProjectionUnavailable("NO_LOG_OR_TRACE_DIAGNOSTIC_EVIDENCE")
    if "SELECTED_EVIDENCE_REF_UNRESOLVED" in reasons:
        raise FaultProjectionUnavailable("SELECTED_EVIDENCE_REF_UNRESOLVED")
    if "INSUFFICIENT_RESOLVABLE_EVIDENCE" in reasons:
        raise FaultProjectionUnavailable("INSUFFICIENT_RESOLVABLE_EVIDENCE")
    if "CONTROL_TRUTH_LEAK" in reasons:
        raise FaultProjectionUnavailable("CONTROL_TRUTH_LEAK")
    if "VISIBLE_SERVICE_COUNT_BELOW_MINIMUM" in reasons:
        raise FaultProjectionUnavailable("VISIBLE_SERVICE_COUNT_BELOW_MINIMUM")
    try:
        return build_live_a0_context(
            window_start=window_start,
            window_end=window_end,
            metrics=tuple(item for item in metrics if _metric_is_diagnostic(item)),
            logs=tuple(item for item in logs if _log_is_diagnostic(item)),
            traces=tuple(item for item in traces if _trace_is_diagnostic(item)),
            resolvable_refs=resolvable_refs,
            projection=projection,
            bounded_selection=selection,
        )
    except ValidationError as error:
        summary["validation_errors"] = _safe_validation_entries(
            error,
            selection=selection,
        )
        updated_reasons = tuple(
            dict.fromkeys((*reasons, "TYPED_PROJECTION_VALIDATION_FAILED"))
        )
        summary["reason_codes"] = updated_reasons
        summary.pop("summary_sha256", None)
        summary["summary_sha256"] = canonical_sha256(summary)
        write_private_json(summary_path, summary, create_once=False)
        raise


__all__ = ["FaultProjectionUnavailable", "build_fault_time_a0_context"]
