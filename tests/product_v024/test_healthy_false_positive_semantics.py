from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

from ecomsre.dta_v2.v22.memory import BaselineProfileV22, build_memory_views_v22
from ecomsre.dta_v2.v22.read_contracts import (
    EvidenceSourceV22,
    LogRecordV22,
    MetricFactV22,
    MetricKindV22,
    MetricSupportStatusV22,
    MetricUnitV22,
    ReadRecordV22,
    ReadSourceStatusV22,
    ResourceSampleV22,
    ResourceUsageRecordV22,
    SpanStatusV22,
    TraceSpanV22,
    semantic_sha256_v22,
)
from ecomsre.dta_v2.v22.replay import ReadOutcomeV22
from ecomsre.dta_v2.v23.generic_anomalies import (
    GenericAnomalyKindV23,
    extract_generic_anomalies_v23,
)
from ecomsre.dta_v2.v23.known_admission import build_known_admission_state_v23
from ecomsre.dta_v2.v23.ontology_view import build_active_ontology_view_v23
from ecomsre.product.connectors.base import (
    ConnectorQueryResultV1,
    ConnectorWindowV1,
)
from ecomsre.product.pilot.nofault_acceptance_v023 import (
    _episode_result_is_fresh,
)
from ecomsre.product.incidents.diagnosis_bridge import _effective_admissions_v024


NOW = datetime(2026, 9, 2, 12, 10, 35, tzinfo=UTC)
WINDOW = ConnectorWindowV1(
    started_at=NOW - timedelta(seconds=300),
    ended_at=NOW,
)


def _outcome(
    action_id: str,
    source: EvidenceSourceV22,
    records: tuple[ReadRecordV22, ...],
) -> ReadOutcomeV22:
    payload: dict[str, Any] = {
        "schema_version": "dta-v22.read-outcome.v1",
        "action_id": action_id,
        "source": source,
        "request_sha256": semantic_sha256_v22({"action_id": action_id}),
        "status": ReadSourceStatusV22.SUCCESS_NONEMPTY,
        "records": records,
        "truncated": False,
    }
    draft = ReadOutcomeV22.model_construct(**payload, outcome_sha256="0" * 64)
    return ReadOutcomeV22.model_validate(
        {
            **payload,
            "outcome_sha256": semantic_sha256_v22(
                draft.model_dump(mode="json", exclude={"outcome_sha256"})
            ),
        }
    )


def _metric(kind: MetricKindV22, value: float) -> MetricFactV22:
    units = {
        MetricKindV22.ERROR_RATE: MetricUnitV22.RATIO,
        MetricKindV22.LATENCY_P95_MS: MetricUnitV22.MILLISECONDS,
        MetricKindV22.REQUEST_SUPPORT: MetricUnitV22.COUNT,
    }
    return MetricFactV22(
        schema_version="dta-v22.metric-fact.v1",
        service="checkout",
        metric_kind=kind,
        support_status=MetricSupportStatusV22.SUPPORTED,
        sample_count=21,
        value=value,
        unit=units[kind],
        window_started_at=WINDOW.started_at,
        window_ended_at=WINDOW.ended_at,
    )


def _resource(
    memory_values: tuple[int, ...],
    *,
    cpu_values: tuple[float, ...] | None = None,
) -> ResourceUsageRecordV22:
    cpu = cpu_values or tuple(7.0 + index * 0.05 for index in range(len(memory_values)))
    samples = tuple(
        ResourceSampleV22(
            offset_ms=index * 2500,
            cpu_percent=cpu[index],
            memory_bytes=value,
        )
        for index, value in enumerate(memory_values)
    )
    return ResourceUsageRecordV22(
        schema_version="dta-v22.resource-usage-record.v1",
        service="checkout",
        sampling_window_seconds=10,
        samples=samples,
        memory_slope_bytes_per_second=(
            samples[-1].memory_bytes - samples[0].memory_bytes
        )
        / 10,
    )


def _baseline(
    *,
    latency_ms: float = 96.8389830230388,
    memory_slope: float = -11659.946666666667,
) -> BaselineProfileV22:
    return BaselineProfileV22.build(
        metric_stats=(
            ("checkout", MetricKindV22.ERROR_RATE, 0.0, 0.0),
            ("checkout", MetricKindV22.LATENCY_P95_MS, latency_ms, 4.466501235697374),
            ("checkout", MetricKindV22.REQUEST_SUPPORT, 3.3225732196649864, 1.5),
        ),
        trace_stats=(),
        resource_stats=(("checkout", 8.048287157560816, memory_slope),),
    )


def _memory(
    *,
    metrics: tuple[MetricFactV22, ...],
    resource: ResourceUsageRecordV22,
    logs: tuple[LogRecordV22, ...] = (),
    traces: tuple[TraceSpanV22, ...] = (),
    baseline: BaselineProfileV22 | None = None,
):
    outcomes = [
        _outcome("a:metrics:checkout:core", EvidenceSourceV22.METRICS, metrics),
        _outcome("a:resources:checkout", EvidenceSourceV22.RESOURCES, (resource,)),
    ]
    if logs:
        outcomes.append(_outcome("a:logs:checkout", EvidenceSourceV22.LOGS, logs))
    if traces:
        outcomes.append(_outcome("a:traces:checkout", EvidenceSourceV22.TRACES, traces))
    memory, _ = build_memory_views_v22(
        outcomes=tuple(outcomes),
        baseline=_baseline() if baseline is None else baseline,
        observed_at=NOW,
        top_k=64,
    )
    return memory


def test_measured_healthy_values_do_not_become_fault_anomalies() -> None:
    memory = _memory(
        metrics=(
            _metric(MetricKindV22.ERROR_RATE, 0.0),
            _metric(MetricKindV22.LATENCY_P95_MS, 145.29307826172328),
            _metric(MetricKindV22.REQUEST_SUPPORT, 7.251817531936508),
        ),
        resource=_resource((14266368, 13615104, 11870208, 16711680, 14393344)),
    )

    anomalies = extract_generic_anomalies_v23(
        memory=memory,
        candidate_services=("checkout",),
        healthy_noise_guard_v024=True,
    )

    assert anomalies == ()


def test_near_zero_latency_and_request_growth_do_not_amplify_into_a_fault() -> None:
    memory = _memory(
        metrics=(
            _metric(MetricKindV22.ERROR_RATE, 0.0),
            _metric(MetricKindV22.LATENCY_P95_MS, 40.0),
            _metric(MetricKindV22.REQUEST_SUPPORT, 1000.0),
        ),
        resource=_resource((10_000_000,) * 5),
        baseline=_baseline(latency_ms=0.1, memory_slope=0.0),
    )

    anomalies = extract_generic_anomalies_v23(
        memory=memory,
        candidate_services=("checkout",),
        healthy_noise_guard_v024=True,
    )

    assert not any(
        item.kind is GenericAnomalyKindV23.METRIC_LATENCY_OUTLIER for item in anomalies
    )


def test_one_short_cpu_burst_is_not_a_strong_resource_fault() -> None:
    memory = _memory(
        metrics=(
            _metric(MetricKindV22.ERROR_RATE, 0.0),
            _metric(MetricKindV22.LATENCY_P95_MS, 96.0),
            _metric(MetricKindV22.REQUEST_SUPPORT, 3.0),
        ),
        resource=_resource(
            (10_000_000,) * 5,
            cpu_values=(3.0, 4.0, 99.0, 5.0, 4.0),
        ),
        baseline=_baseline(memory_slope=0.0),
    )

    anomalies = extract_generic_anomalies_v23(
        memory=memory,
        candidate_services=("checkout",),
        healthy_noise_guard_v024=True,
    )

    assert not any(
        item.kind is GenericAnomalyKindV23.RESOURCE_CPU_OUTLIER
        for item in anomalies
    )


def test_strong_known_fault_signals_remain_observable() -> None:
    memory = _memory(
        metrics=(
            _metric(MetricKindV22.ERROR_RATE, 0.2),
            _metric(MetricKindV22.LATENCY_P95_MS, 500.0),
            _metric(MetricKindV22.REQUEST_SUPPORT, 2.0),
        ),
        resource=_resource(
            (10_000_000, 15_000_000, 20_000_000, 25_000_000, 30_000_000)
        ),
        baseline=_baseline(latency_ms=100.0, memory_slope=0.0),
    )

    kinds = {
        item.kind
        for item in extract_generic_anomalies_v23(
            memory=memory,
            candidate_services=("checkout",),
            healthy_noise_guard_v024=True,
        )
    }

    assert GenericAnomalyKindV23.METRIC_ERROR_OUTLIER in kinds
    assert GenericAnomalyKindV23.METRIC_LATENCY_OUTLIER in kinds
    assert GenericAnomalyKindV23.RESOURCE_MEMORY_TREND in kinds


def test_baseline_known_warning_log_is_suppressed_but_error_is_visible() -> None:
    diagnostic = LogRecordV22(
        schema_version="dta-v22.log-record.v1",
        observed_at=NOW,
        service="checkout",
        severity="WARN",
        message="memory pressure warning",
    )
    error = diagnostic.model_copy(update={"severity": "ERROR"})
    memory = _memory(
        metrics=(
            _metric(MetricKindV22.ERROR_RATE, 0.0),
            _metric(MetricKindV22.LATENCY_P95_MS, 96.0),
            _metric(MetricKindV22.REQUEST_SUPPORT, 3.0),
        ),
        resource=_resource((10_000_000,) * 5),
        logs=(diagnostic, error),
        baseline=_baseline(memory_slope=0.0),
    )

    anomalies = extract_generic_anomalies_v23(
        memory=memory,
        candidate_services=("checkout",),
        baseline_known_log_templates=(("checkout", "memory pressure warning"),),
        healthy_noise_guard_v024=True,
    )

    log_anomalies = tuple(
        item for item in anomalies if item.source is EvidenceSourceV22.LOGS
    )
    assert len(log_anomalies) == 1
    assert log_anomalies[0].kind is GenericAnomalyKindV23.LOG_ERROR_CLUSTER


def test_guarded_benign_signals_cannot_admit_a_legacy_memory_leak() -> None:
    warning = LogRecordV22(
        schema_version="dta-v22.log-record.v1",
        observed_at=NOW,
        service="checkout",
        severity="WARN",
        message="memory pressure warning",
    )
    memory = _memory(
        metrics=(
            _metric(MetricKindV22.ERROR_RATE, 0.0),
            _metric(MetricKindV22.LATENCY_P95_MS, 96.0),
            _metric(MetricKindV22.REQUEST_SUPPORT, 3.0),
        ),
        resource=_resource((14266368, 13615104, 11870208, 16711680, 14393344)),
        logs=(warning,),
    )
    admission = build_known_admission_state_v23(
        view=build_active_ontology_view_v23(candidate_services=("checkout",)),
        memory=memory,
    )
    anomalies = extract_generic_anomalies_v23(
        memory=memory,
        candidate_services=("checkout",),
        baseline_known_log_templates=(("checkout", "memory pressure warning"),),
        healthy_noise_guard_v024=True,
    )

    assert len(admission.admitted_diagnoses) == 1
    assert anomalies == ()
    assert _effective_admissions_v024(
        admission=admission,
        memory=memory,
        anomalies=anomalies,
    ) == ()


def test_mixed_resource_ref_does_not_cross_validate_cpu_and_memory() -> None:
    error = LogRecordV22(
        schema_version="dta-v22.log-record.v1",
        observed_at=NOW,
        service="checkout",
        severity="ERROR",
        message="memory pressure warning",
    )
    memory = _memory(
        metrics=(
            _metric(MetricKindV22.ERROR_RATE, 0.0),
            _metric(MetricKindV22.LATENCY_P95_MS, 96.0),
            _metric(MetricKindV22.REQUEST_SUPPORT, 3.0),
        ),
        resource=_resource(
            (10_000_000, 10_000_000, 10_000_000, 10_000_000, 12_000_000),
            cpu_values=(3.0, 4.0, 99.0, 5.0, 4.0),
        ),
        logs=(error,),
        baseline=_baseline(memory_slope=0.0),
    )
    admission = build_known_admission_state_v23(
        view=build_active_ontology_view_v23(candidate_services=("checkout",)),
        memory=memory,
    )
    anomalies = extract_generic_anomalies_v23(
        memory=memory,
        candidate_services=("checkout",),
        healthy_noise_guard_v024=True,
    )

    assert {item.kind for item in anomalies} == {
        GenericAnomalyKindV23.LOG_ERROR_CLUSTER,
        GenericAnomalyKindV23.RESOURCE_MEMORY_TREND,
    }
    effective = _effective_admissions_v024(
        admission=admission,
        memory=memory,
        anomalies=anomalies,
    )
    assert len(effective) == 1
    assert effective[0].mechanism.value == "MEMORY_LEAK"


def test_moderate_guarded_latency_cannot_satisfy_strong_latency_clause() -> None:
    memory = _memory(
        metrics=(
            _metric(MetricKindV22.ERROR_RATE, 0.0),
            _metric(MetricKindV22.LATENCY_P95_MS, 90.0),
            _metric(MetricKindV22.REQUEST_SUPPORT, 3.0),
        ),
        resource=_resource((10_000_000,) * 5),
        traces=(
            TraceSpanV22(
                schema_version="dta-v22.trace-span.v1",
                observed_at=NOW,
                service_path=("checkout", "payment"),
                service="payment",
                parent_service="checkout",
                operation="Charge",
                status=SpanStatusV22.OK,
                duration_ms=50.0,
                first_error_location=False,
            ),
        ),
        baseline=BaselineProfileV22.build(
            metric_stats=(
                ("checkout", MetricKindV22.ERROR_RATE, 0.0, 0.0),
                ("checkout", MetricKindV22.LATENCY_P95_MS, 40.0, 1.0),
                ("checkout", MetricKindV22.REQUEST_SUPPORT, 3.0, 1.0),
            ),
            trace_stats=(("payment", "Charge", 20.0),),
            resource_stats=(("checkout", 8.0, 0.0),),
        ),
    )
    admission = build_known_admission_state_v23(
        view=build_active_ontology_view_v23(
            candidate_services=("checkout", "payment")
        ),
        memory=memory,
        topology_edges=(("checkout", "payment"),),
    )
    anomalies = extract_generic_anomalies_v23(
        memory=memory,
        candidate_services=("checkout", "payment"),
        healthy_noise_guard_v024=True,
    )

    latency = next(
        item
        for item in anomalies
        if item.kind is GenericAnomalyKindV23.METRIC_LATENCY_OUTLIER
    )
    assert latency.strength.value == "MODERATE"
    assert len(admission.admitted_diagnoses) == 1
    assert _effective_admissions_v024(
        admission=admission,
        memory=memory,
        anomalies=anomalies,
    ) == ()


def test_truncated_logs_are_bounded_fresh_observations_not_total_completeness() -> None:
    log = LogRecordV22(
        schema_version="dta-v22.log-record.v1",
        observed_at=NOW,
        service="checkout",
        severity="DIAGNOSTIC",
        message="order placed",
    )
    incident = SimpleNamespace(
        started_at=WINDOW.started_at,
        diagnosis_observed_at=WINDOW.ended_at,
    )
    logs = ConnectorQueryResultV1.build(
        source=EvidenceSourceV22.LOGS,
        status=ReadSourceStatusV22.SUCCESS_NONEMPTY,
        requested_services=("checkout",),
        covered_services=("checkout",),
        window=WINDOW,
        records=(log,),
        truncated=True,
        safe_error_code=None,
        latency_ms=1.0,
    )
    metrics = ConnectorQueryResultV1.build(
        source=EvidenceSourceV22.METRICS,
        status=ReadSourceStatusV22.SUCCESS_NONEMPTY,
        requested_services=("checkout",),
        covered_services=("checkout",),
        window=WINDOW,
        records=(_metric(MetricKindV22.ERROR_RATE, 0.0),),
        truncated=True,
        safe_error_code=None,
        latency_ms=1.0,
    )

    assert _episode_result_is_fresh(logs, incident=incident)
    assert not _episode_result_is_fresh(metrics, incident=incident)
