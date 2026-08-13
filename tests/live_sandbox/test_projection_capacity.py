from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import random
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

import ecomsre_live_sandbox.fault_projection as fault_projection_module
from ecomsre_live_sandbox.contracts import canonical_sha256
from ecomsre_live_sandbox.e2e_telemetry import (
    LiveLogObservation,
    LiveMetricObservation,
    LiveTraceObservation,
    select_contract_bounded_projection_inputs,
)
from ecomsre_live_sandbox.fault_projection import (
    FaultProjectionUnavailable,
    build_fault_time_a0_context,
)
from ecomsre_live_sandbox.projection_capacity import (
    RCA100LiveProjectionCapacity,
    RCA100_LIVE_PROJECTION_CAPACITY,
    assert_live_projection_capacity_conforms,
    effective_projection_limits,
)
from ecomsre_rca100.projection import (
    RCA100BoundedEvidence,
    RCA100SourceProjection,
)


NOW = datetime(2026, 8, 14, 12, tzinfo=timezone.utc)


def _projection_policy() -> SimpleNamespace:
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
    )


def _r2_scale_metrics() -> tuple[LiveMetricObservation, ...]:
    return tuple(
        LiveMetricObservation(
            service_name=f"service-{index:02d}",
            baseline_requests=100,
            baseline_errors=1,
            fault_requests=100,
            fault_errors=20 - index,
            baseline_p95_ms=20,
            fault_p95_ms=60 - index,
            evidence_ref=f"metric:{index:04d}",
            first_anomaly_at=NOW + timedelta(seconds=index),
        )
        for index in range(1, 9)
    )


def _r2_scale_traces() -> tuple[LiveTraceObservation, ...]:
    return tuple(
        LiveTraceObservation(
            observed_at=NOW + timedelta(milliseconds=index),
            service_name=f"service-{((index - 1) % 4) + 1:02d}",
            operation=f"operation-{index:02d}",
            status="ERROR",
            duration_ms=200 - index,
            evidence_ref=f"trace:{index:04d}",
        )
        for index in range(1, 13)
    )


def _scale_logs() -> tuple[LiveLogObservation, ...]:
    return tuple(
        LiveLogObservation(
            observed_at=NOW + timedelta(milliseconds=index),
            service_name=f"service-{((index - 1) % 6) + 1:02d}",
            severity="ERROR",
            body=f"observed request error {index:02d}",
            evidence_ref=f"log:{index:04d}",
        )
        for index in range(1, 13)
    )


def _refs(*sources: tuple[object, ...]) -> frozenset[str]:
    return frozenset(
        str(item.evidence_ref)
        for source in sources
        for item in source
        if getattr(item, "evidence_ref", None)
    )


def _build(
    tmp_path,
    *,
    metrics: tuple[LiveMetricObservation, ...],
    logs: tuple[LiveLogObservation, ...],
    traces: tuple[LiveTraceObservation, ...],
    summary_name: str = "projection-input-summary.json",
):
    return build_fault_time_a0_context(
        window_start=NOW,
        window_end=NOW + timedelta(minutes=1),
        metrics=metrics,
        logs=logs,
        traces=traces,
        resolvable_refs=_refs(metrics, logs, traces),
        projection=_projection_policy(),
        summary_path=tmp_path / summary_name,
    )


def test_live_projection_capacity_conforms_to_rca100_json_schema() -> None:
    assert_live_projection_capacity_conforms(RCA100_LIVE_PROJECTION_CAPACITY)

    assert RCA100_LIVE_PROJECTION_CAPACITY.metrics_evidence == 6
    assert RCA100_LIVE_PROJECTION_CAPACITY.metrics_ranking == 6
    assert RCA100_LIVE_PROJECTION_CAPACITY.source_evidence == 6


def test_effective_projection_limits_preserve_raw_collection_limits() -> None:
    projection = SimpleNamespace(
        metric_candidate_limit=4,
        log_evidence_limit=16,
        trace_evidence_limit=20,
    )

    limits = effective_projection_limits(
        projection,
        capacity=RCA100_LIVE_PROJECTION_CAPACITY,
    )

    assert limits.metrics == 4
    assert limits.logs == 6
    assert limits.traces == 6
    assert projection.log_evidence_limit == 16
    assert projection.trace_evidence_limit == 20


def test_capacity_conformance_fails_closed_on_drift() -> None:
    with pytest.raises(RuntimeError, match="differs from the RCA100 typed schema"):
        assert_live_projection_capacity_conforms(
            RCA100LiveProjectionCapacity(
                metrics_evidence=7,
                metrics_ranking=6,
                source_evidence=6,
            )
        )


def test_exact_r2_scale_builds_a_contract_bounded_context(tmp_path) -> None:
    metrics = _r2_scale_metrics()
    traces = _r2_scale_traces()
    summary_path = tmp_path / "projection-input-summary.json"

    context = build_fault_time_a0_context(
        window_start=NOW,
        window_end=NOW + timedelta(minutes=1),
        metrics=metrics,
        logs=(),
        traces=traces,
        resolvable_refs=_refs(metrics, traces),
        projection=_projection_policy(),
        summary_path=summary_path,
    )

    assert len(context.metrics.evidence) == 4
    assert context.logs.status == "SOURCE_UNAVAILABLE"
    assert context.logs.reason == "NO_VISIBLE_LOG_ANOMALY"
    assert len(context.traces.evidence) == 6
    assert 3 <= len(context.visible_entities) <= 8
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    assert summary["metrics_selected_count"] == 4
    assert summary["logs_selected_count"] == 0
    assert summary["traces_selected_count"] == 6
    assert summary["metrics_dropped_for_capacity"] == 4
    assert summary["traces_dropped_for_capacity"] == 6
    assert summary["all_selected_refs_resolve"] is True


def test_metrics_and_logs_scale_builds_bounded_context(tmp_path) -> None:
    metrics = _r2_scale_metrics()
    logs = _scale_logs()

    context = _build(tmp_path, metrics=metrics, logs=logs, traces=())

    assert len(context.metrics.evidence) == 4
    assert len(context.logs.evidence) == 6
    assert context.traces.status == "SOURCE_UNAVAILABLE"
    assert context.traces.reason == "NO_VISIBLE_TRACE_DIAGNOSTIC"
    assert 3 <= len(context.visible_entities) <= 8


def test_all_source_scale_builds_bounded_context(tmp_path) -> None:
    metrics = _r2_scale_metrics()
    logs = _scale_logs()
    traces = _r2_scale_traces()

    context = _build(tmp_path, metrics=metrics, logs=logs, traces=traces)

    assert len(context.metrics.evidence) == 4
    assert len(context.logs.evidence) == 6
    assert len(context.traces.evidence) == 6
    assert 3 <= len(context.visible_entities) <= 8
    visible = {item.entity_ref for item in context.visible_entities}
    retained = {
        *(item.entity_ref for item in context.metrics.evidence),
        *(item.entity_ref for item in context.logs.evidence),
        *(item.entity_ref for item in context.traces.evidence),
    }
    assert retained.issubset(visible)
    assert visible.issubset(retained)


def test_diversity_first_selection_resists_single_service_concentration() -> None:
    metrics = _r2_scale_metrics()
    logs = tuple(
        LiveLogObservation(
            observed_at=NOW + timedelta(milliseconds=index),
            service_name="service-01" if index <= 10 else f"service-{index - 9:02d}",
            severity="ERROR",
            body=f"observed request error {index:02d}",
            evidence_ref=f"log:{index:04d}",
        )
        for index in range(1, 15)
    )
    traces = tuple(
        LiveTraceObservation(
            observed_at=NOW + timedelta(milliseconds=index),
            service_name="service-01" if index <= 10 else f"service-{index - 9:02d}",
            operation=f"operation-{index:02d}",
            status="ERROR",
            duration_ms=200 - index,
            evidence_ref=f"trace:{index:04d}",
        )
        for index in range(1, 15)
    )

    selected = select_contract_bounded_projection_inputs(
        window_start=NOW,
        window_end=NOW + timedelta(minutes=1),
        metrics=metrics,
        logs=logs,
        traces=traces,
        projection=_projection_policy(),
    )

    assert len({item.service_name for item in selected.logs}) == 5
    assert len({item.service_name for item in selected.traces}) == 5


def test_selection_and_context_are_permutation_stable(tmp_path) -> None:
    metrics = _r2_scale_metrics()
    logs = _scale_logs()
    traces = _r2_scale_traces()
    expected_context_sha: str | None = None
    expected_summary_sha: str | None = None

    for seed in range(5):
        generator = random.Random(seed)
        shuffled_metrics = list(metrics)
        shuffled_logs = list(logs)
        shuffled_traces = list(traces)
        generator.shuffle(shuffled_metrics)
        generator.shuffle(shuffled_logs)
        generator.shuffle(shuffled_traces)
        summary_name = f"projection-input-summary-{seed}.json"
        context = _build(
            tmp_path,
            metrics=tuple(shuffled_metrics),
            logs=tuple(shuffled_logs),
            traces=tuple(shuffled_traces),
            summary_name=summary_name,
        )
        summary = json.loads((tmp_path / summary_name).read_text(encoding="utf-8"))
        context_sha = canonical_sha256(context)
        summary_sha = str(summary["summary_sha256"])
        expected_context_sha = expected_context_sha or context_sha
        expected_summary_sha = expected_summary_sha or summary_sha
        assert context_sha == expected_context_sha
        assert summary_sha == expected_summary_sha


def test_log_selection_above_raw_hit_limit_is_permutation_stable() -> None:
    metrics = _r2_scale_metrics()
    logs = tuple(
        LiveLogObservation(
            observed_at=NOW + timedelta(milliseconds=index),
            service_name=f"service-{((index - 1) % 8) + 1:02d}",
            severity="ERROR",
            body=f"observed request error {index:02d}",
            evidence_ref=f"log:{index:04d}",
        )
        for index in range(1, 61)
    )
    expected_refs: tuple[str | None, ...] | None = None

    for seed in range(5):
        shuffled = list(logs)
        random.Random(seed).shuffle(shuffled)
        selected = select_contract_bounded_projection_inputs(
            window_start=NOW,
            window_end=NOW + timedelta(minutes=1),
            metrics=metrics,
            logs=tuple(shuffled),
            traces=(),
            projection=_projection_policy(),
        )
        selected_refs = tuple(item.evidence_ref for item in selected.logs)
        expected_refs = expected_refs or selected_refs
        assert selected_refs == expected_refs


def test_expected_root_name_is_never_inserted_and_renaming_is_stable() -> None:
    metrics = tuple(
        item.model_copy(
            update={"service_name": "payment" if index == 8 else item.service_name}
        )
        for index, item in enumerate(_r2_scale_metrics(), 1)
    )
    traces = _r2_scale_traces()
    selected = select_contract_bounded_projection_inputs(
        window_start=NOW,
        window_end=NOW + timedelta(minutes=1),
        metrics=metrics,
        logs=(),
        traces=traces,
        projection=_projection_policy(),
    )
    renamed_metrics = tuple(
        item.model_copy(update={"service_name": f"renamed-{index:02d}"})
        for index, item in enumerate(metrics, 1)
    )
    rename_map = {
        f"service-{index:02d}": f"renamed-{index:02d}" for index in range(1, 9)
    }
    renamed_traces = tuple(
        item.model_copy(update={"service_name": rename_map[item.service_name]})
        for item in traces
    )
    renamed = select_contract_bounded_projection_inputs(
        window_start=NOW,
        window_end=NOW + timedelta(minutes=1),
        metrics=renamed_metrics,
        logs=(),
        traces=renamed_traces,
        projection=_projection_policy(),
    )

    assert "payment" not in selected.visible_services
    assert [item.evidence_ref for item in renamed.metrics] == [
        item.evidence_ref for item in selected.metrics
    ]
    assert [item.evidence_ref for item in renamed.traces] == [
        item.evidence_ref for item in selected.traces
    ]


@pytest.mark.parametrize(
    ("metrics", "logs", "traces", "reason"),
    (
        ((), _scale_logs(), (), "NO_DIAGNOSTIC_METRICS"),
        (_r2_scale_metrics(), (), (), "NO_LOG_OR_TRACE_DIAGNOSTIC_EVIDENCE"),
        (
            _r2_scale_metrics()[:2],
            (),
            _r2_scale_traces()[:2],
            "VISIBLE_SERVICE_COUNT_BELOW_MINIMUM",
        ),
    ),
)
def test_invalid_projection_boundaries_have_typed_reasons(
    tmp_path,
    metrics,
    logs,
    traces,
    reason,
) -> None:
    with pytest.raises(FaultProjectionUnavailable) as captured:
        _build(tmp_path, metrics=metrics, logs=logs, traces=traces)

    assert captured.value.reason_code == reason


def test_ok_only_traces_are_not_diagnostic_evidence(tmp_path) -> None:
    metrics = _r2_scale_metrics()
    traces = tuple(
        item.model_copy(update={"status": "OK"}) for item in _r2_scale_traces()
    )

    with pytest.raises(FaultProjectionUnavailable) as captured:
        _build(tmp_path, metrics=metrics, logs=(), traces=traces)

    assert captured.value.reason_code == "NO_LOG_OR_TRACE_DIAGNOSTIC_EVIDENCE"


def test_unresolved_selected_ref_has_precise_typed_reason(tmp_path) -> None:
    metrics = _r2_scale_metrics()
    traces = _r2_scale_traces()

    with pytest.raises(FaultProjectionUnavailable) as captured:
        build_fault_time_a0_context(
            window_start=NOW,
            window_end=NOW + timedelta(minutes=1),
            metrics=metrics,
            logs=(),
            traces=traces,
            resolvable_refs=_refs(metrics, traces)
            - {str(traces[0].evidence_ref)},
            projection=_projection_policy(),
            summary_path=tmp_path / "projection-input-summary.json",
        )

    assert captured.value.reason_code == "SELECTED_EVIDENCE_REF_UNRESOLVED"


def test_control_truth_marker_has_precise_typed_reason(tmp_path) -> None:
    metrics = tuple(
        item.model_copy(update={"service_name": "paymentfailure"})
        for item in _r2_scale_metrics()[:3]
    )
    logs = tuple(
        item.model_copy(update={"service_name": "paymentfailure"})
        for item in _scale_logs()[:3]
    )

    with pytest.raises(FaultProjectionUnavailable) as captured:
        _build(tmp_path, metrics=metrics, logs=logs, traces=())

    assert captured.value.reason_code == "CONTROL_TRUTH_LEAK"


def test_pydantic_failure_retains_only_safe_validation_shape(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    metrics = _r2_scale_metrics()
    traces = _r2_scale_traces()

    def fail_with_typed_capacity_error(**_: object) -> object:
        evidence = tuple(
            RCA100BoundedEvidence(
                evidence_ref=f"trace:{index:04d}",
                entity_ref="apm|apm.service|service-01",
                name="safe-fixture",
                started_at=NOW.timestamp(),
                ended_at=NOW.timestamp(),
                score=1.0,
                summary="safe fixture summary",
            )
            for index in range(1, 8)
        )
        return RCA100SourceProjection(
            source="traces",
            status="AVAILABLE",
            evidence=evidence,
            total_rows=7,
            window_rows=7,
            mapped_rows=7,
            unmapped_rows=0,
        )

    monkeypatch.setattr(
        fault_projection_module,
        "build_live_a0_context",
        fail_with_typed_capacity_error,
    )
    summary_path = tmp_path / "projection-input-summary.json"

    with pytest.raises(ValidationError):
        build_fault_time_a0_context(
            window_start=NOW,
            window_end=NOW + timedelta(minutes=1),
            metrics=metrics,
            logs=(),
            traces=traces,
            resolvable_refs=_refs(metrics, traces),
            projection=_projection_policy(),
            summary_path=summary_path,
        )

    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    assert summary["validation_errors"] == [
        {
            "model": "RCA100SourceProjection",
            "field_location": "evidence",
            "error_type": "too_long",
            "input_count": 7,
            "contract_capacity": 6,
        }
    ]
    serialized = json.dumps(summary, sort_keys=True)
    assert "input_value" not in serialized
    assert "safe fixture summary" not in serialized
