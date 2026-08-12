"""Fault-time A0 projection admission and private input diagnostics."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, cast

from ecomsre_live_sandbox.contracts import canonical_sha256, write_private_json
from ecomsre_live_sandbox.e2e_telemetry import (
    LiveLogObservation,
    LiveMetricObservation,
    LiveTraceObservation,
    build_live_a0_context,
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
) -> dict[str, object]:
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
    if len(visible_candidates) < 3:
        reasons.append("VISIBLE_SERVICE_COUNT_BELOW_MINIMUM")
    payload: dict[str, object] = {
        "schema_version": "live-e2e.projection-input-summary.v5",
        "window_start": window_start.isoformat(),
        "window_end": window_end.isoformat(),
        "broad_metrics_count": len(metrics),
        "broad_logs_count": len(logs),
        "broad_traces_count": len(traces),
        "metric_service_count": len({item.service_name for item in metrics}),
        "log_service_count": len({item.service_name for item in logs}),
        "trace_service_count": len({item.service_name for item in traces}),
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
        "empty_model_streams": empty_streams,
        "reason_codes": tuple(reasons),
    }
    payload["summary_sha256"] = canonical_sha256(payload)
    write_private_json(path, payload, create_once=True)
    return payload


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
    summary = _write_projection_summary(
        path=summary_path,
        window_start=window_start,
        window_end=window_end,
        metrics=metrics,
        logs=logs,
        traces=traces,
        resolvable_refs=resolvable_refs,
    )
    reasons = cast(tuple[str, ...], summary["reason_codes"])
    if "NO_DIAGNOSTIC_METRICS" in reasons:
        raise FaultProjectionUnavailable("NO_DIAGNOSTIC_METRICS")
    if "NO_LOG_OR_TRACE_DIAGNOSTIC_EVIDENCE" in reasons:
        raise FaultProjectionUnavailable("NO_LOG_OR_TRACE_DIAGNOSTIC_EVIDENCE")
    if "INSUFFICIENT_RESOLVABLE_EVIDENCE" in reasons:
        raise FaultProjectionUnavailable("INSUFFICIENT_RESOLVABLE_EVIDENCE")
    if "VISIBLE_SERVICE_COUNT_BELOW_MINIMUM" in reasons:
        raise FaultProjectionUnavailable("VISIBLE_SERVICE_COUNT_BELOW_MINIMUM")
    return build_live_a0_context(
        window_start=window_start,
        window_end=window_end,
        metrics=tuple(item for item in metrics if _metric_is_diagnostic(item)),
        logs=tuple(item for item in logs if _log_is_diagnostic(item)),
        traces=tuple(item for item in traces if _trace_is_diagnostic(item)),
        resolvable_refs=resolvable_refs,
        projection=projection,
    )


__all__ = ["FaultProjectionUnavailable", "build_fault_time_a0_context"]
