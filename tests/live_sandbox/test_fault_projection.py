from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace

import pytest

from ecomsre_live_sandbox.e2e_telemetry import (
    LiveLogObservation,
    LiveMetricObservation,
    LiveTraceObservation,
)
from ecomsre_live_sandbox.fault_projection import (
    FaultProjectionUnavailable,
    build_fault_time_a0_context,
)


NOW = datetime(2026, 8, 12, 12, tzinfo=timezone.utc)


def _policy() -> SimpleNamespace:
    return SimpleNamespace(
        visible_entity_minimum=3,
        visible_entity_maximum=8,
        metric_candidate_limit=4,
        log_raw_hit_limit=50,
        log_evidence_limit=16,
        log_per_service_limit=4,
        trace_query_limit=3,
        trace_evidence_limit=20,
        trace_neighborhood_hops=2,
        maximum_serialized_context_bytes=98304,
        service_ordering_policy="SUPPORT_THEN_METRICS_THEN_EARLIEST_THEN_NAME",
        evidence_ordering_policy="SOURCE_SCORE_TIME_SERVICE_HASH",
        alert_title="Observed purchase-flow request error-rate increase",
        diagnostic_admission_policy="METRICS_PLUS_LOGS_OR_TRACES",
        metric_diagnostic_policy="POSITIVE_ERROR_OR_LATENCY_DELTA",
        log_diagnostic_policy="WARN_OR_HIGHER_OR_ERROR_TEXT",
        trace_diagnostic_policy="ERROR_ONLY",
    )


def _metrics() -> tuple[LiveMetricObservation, ...]:
    return tuple(
        LiveMetricObservation(
            service_name=service,
            baseline_requests=100,
            baseline_errors=1,
            fault_requests=100,
            fault_errors=errors,
            baseline_p95_ms=20,
            fault_p95_ms=40,
            evidence_ref=f"metric:{index:04d}",
        )
        for index, (service, errors) in enumerate(
            (("checkout", 30), ("currency", 20), ("frontend", 10)), 1
        )
    )


def _logs(*, anomalous: bool = True) -> tuple[LiveLogObservation, ...]:
    severity = "ERROR" if anomalous else "INFO"
    body = "observed request error" if anomalous else "request completed"
    return tuple(
        LiveLogObservation(
            observed_at=NOW,
            service_name=service,
            severity=severity,
            body=body,
            evidence_ref=f"log:{index:04d}",
        )
        for index, service in enumerate(("checkout", "currency", "frontend"), 1)
    )


def _traces(*, error: bool = True) -> tuple[LiveTraceObservation, ...]:
    return tuple(
        LiveTraceObservation(
            observed_at=NOW,
            service_name=service,
            operation="request",
            status="ERROR" if error else "OK",
            duration_ms=20,
            evidence_ref=f"trace:{index:04d}",
        )
        for index, service in enumerate(("checkout", "currency", "frontend"), 1)
    )


def _refs(*sources: tuple[object, ...]) -> frozenset[str]:
    return frozenset(
        str(item.evidence_ref)
        for source in sources
        for item in source
        if getattr(item, "evidence_ref", None)
    )


def _build(tmp_path, *, logs, traces):
    metrics = _metrics()
    return build_fault_time_a0_context(
        window_start=NOW,
        window_end=NOW + timedelta(seconds=60),
        metrics=metrics,
        logs=logs,
        traces=traces,
        resolvable_refs=_refs(metrics, logs, traces),
        projection=_policy(),
        summary_path=tmp_path / "projection-input-summary.json",
    )


def test_fault_time_metrics_plus_logs_allows_empty_trace_projection(tmp_path) -> None:
    context = _build(tmp_path, logs=_logs(), traces=())

    assert context.metrics.status == "AVAILABLE"
    assert context.logs.status == "AVAILABLE"
    assert context.traces.status == "SOURCE_UNAVAILABLE"
    assert context.traces.reason == "NO_VISIBLE_TRACE_DIAGNOSTIC"


def test_fault_time_metrics_plus_traces_allows_empty_log_projection(tmp_path) -> None:
    context = _build(tmp_path, logs=(), traces=_traces())

    assert context.metrics.status == "AVAILABLE"
    assert context.logs.status == "SOURCE_UNAVAILABLE"
    assert context.traces.status == "AVAILABLE"


def test_fault_time_metrics_only_retains_typed_projection_summary(tmp_path) -> None:
    summary_path = tmp_path / "projection-input-summary.json"

    with pytest.raises(FaultProjectionUnavailable) as captured:
        _build(tmp_path, logs=(), traces=())

    assert captured.value.reason_code == "NO_LOG_OR_TRACE_DIAGNOSTIC_EVIDENCE"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    assert summary["empty_model_streams"] == ["LOGS", "TRACES"]
    assert "NO_LOG_OR_TRACE_DIAGNOSTIC_EVIDENCE" in summary["reason_codes"]


def test_backend_records_can_exist_while_log_projection_is_empty(tmp_path) -> None:
    context = _build(tmp_path, logs=_logs(anomalous=False), traces=_traces())
    summary = json.loads(
        (tmp_path / "projection-input-summary.json").read_text(encoding="utf-8")
    )

    assert summary["broad_logs_count"] == 3
    assert summary["anomalous_log_count"] == 0
    assert context.logs.status == "SOURCE_UNAVAILABLE"
    assert context.traces.status == "AVAILABLE"


def test_unresolved_diagnostic_input_is_summarized_before_builder(tmp_path) -> None:
    metrics = _metrics()
    logs = _logs()
    summary_path = tmp_path / "projection-input-summary.json"

    with pytest.raises(FaultProjectionUnavailable) as captured:
        build_fault_time_a0_context(
            window_start=NOW,
            window_end=NOW + timedelta(seconds=60),
            metrics=metrics,
            logs=logs,
            traces=(),
            resolvable_refs=_refs(metrics),
            projection=_policy(),
            summary_path=summary_path,
        )

    assert captured.value.reason_code == "INSUFFICIENT_RESOLVABLE_EVIDENCE"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    assert summary["logs_with_resolver_ref"] == 0
    assert "INSUFFICIENT_RESOLVABLE_EVIDENCE" in summary["reason_codes"]


def test_control_truth_failure_is_recorded_before_builder(tmp_path) -> None:
    metrics = tuple(
        item.model_copy(update={"service_name": "paymentfailure"})
        for item in _metrics()
    )
    logs = tuple(
        item.model_copy(update={"service_name": "paymentfailure"})
        for item in _logs()
    )
    summary_path = tmp_path / "projection-input-summary.json"

    with pytest.raises(FaultProjectionUnavailable) as captured:
        build_fault_time_a0_context(
            window_start=NOW,
            window_end=NOW + timedelta(seconds=60),
            metrics=metrics,
            logs=logs,
            traces=(),
            resolvable_refs=_refs(metrics, logs),
            projection=_policy(),
            summary_path=summary_path,
        )

    assert captured.value.reason_code == "CONTROL_TRUTH_LEAK"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    assert "CONTROL_TRUTH_LEAK" in summary["reason_codes"]
