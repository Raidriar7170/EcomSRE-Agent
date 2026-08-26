from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import cast

from ecomsre.dta_v2.v22.memory import (
    LogCategoryV22,
    LogSalientPayloadV22,
    ResourceSalientPayloadV22,
    SalientEvidenceMemoryV22,
    SignalStrengthV22,
)
from ecomsre.dta_v2.v22.read_contracts import EvidenceSourceV22
from ecomsre.dta_v2.v23.contracts import ProvisionalFaultDomainV23
from ecomsre.dta_v2.v23.domain_projection_v233 import (
    DomainProjectionStatusV233,
    project_domain_v233,
)
from ecomsre.dta_v2.v23.domain_audit_v233 import build_v232_domain_audit_v233
from ecomsre.dta_v2.v23.generic_anomalies import (
    GenericAnomalyKindV23,
    _build_anomaly,
)
from ecomsre.dta_v2.v23.residual_graph import ResidualEvidenceGraphV23


ROOT = Path(__file__).resolve().parents[2]


def _anomaly(
    kind: GenericAnomalyKindV23,
    *,
    service: str = "svc-a",
    source: EvidenceSourceV22 = EvidenceSourceV22.METRICS,
    ref: str | None = None,
    template: str = "opaque observation",
):
    return _build_anomaly(
        kind=kind,
        source=source,
        service=service,
        related_services=(),
        strength=SignalStrengthV22.STRONG,
        summary=f"{service} has {kind.value}",
        evidence_refs=(ref or f"e:test:{source.value.casefold()}:0:000000000000",),
        observed_values={"template": template},
    )


def _graph(
    *anomalies: object,
    healthy: tuple[str, ...] = (),
    complete_sources: tuple[EvidenceSourceV22, ...] = (),
) -> ResidualEvidenceGraphV23:
    services = tuple(sorted({getattr(item, "service") for item in anomalies}))
    coverage = tuple(
        SimpleNamespace(
            source=source,
            queried=source in complete_sources,
            covered_services=services if source in complete_sources else (),
            successful_observations=int(source in complete_sources),
            failed_observations=0,
        )
        for source in EvidenceSourceV22
    )
    return cast(
        ResidualEvidenceGraphV23,
        SimpleNamespace(
            candidate_services=services,
            generic_anomalies=tuple(anomalies),
            residual_anomaly_ids=tuple(
                sorted(getattr(item, "anomaly_id") for item in anomalies)
            ),
            source_coverage=coverage,
            healthy_runtime_services=healthy,
        ),
    )


def _memory(*facts: object) -> SalientEvidenceMemoryV22:
    return cast(
        SalientEvidenceMemoryV22,
        SimpleNamespace(salient_facts=tuple(facts), predicates=()),
    )


def test_projection_is_deterministic_and_total_over_all_domains() -> None:
    anomaly = _anomaly(GenericAnomalyKindV23.RUNTIME_NOT_RUNNING)
    graph = _graph(anomaly)

    first = project_domain_v233(graph=graph, memory=_memory())
    second = project_domain_v233(graph=graph, memory=_memory())

    assert first == second
    assert tuple(item.domain for item in first.domain_scores) == tuple(
        sorted(ProvisionalFaultDomainV23, key=lambda item: item.value)
    )
    assert first.selected_root_service == "svc-a"
    assert first.selected_domain is ProvisionalFaultDomainV23.RUNTIME
    assert first.status is DomainProjectionStatusV233.RESOLVED


def test_configuration_combination_beats_runtime_symptom() -> None:
    change = _anomaly(
        GenericAnomalyKindV23.RECENT_CHANGE_CORRELATION,
        source=EvidenceSourceV22.CHANGES,
        ref="e:test:changes:0:000000000000",
    )
    error = _anomaly(GenericAnomalyKindV23.METRIC_ERROR_OUTLIER)

    projection = project_domain_v233(
        graph=_graph(change, error),
        memory=_memory(),
    )

    assert projection.selected_domain is ProvisionalFaultDomainV23.CONFIGURATION
    assert projection.score_margin >= 1.0


def test_bound_log_and_trace_dependency_combination_is_evidence_bound() -> None:
    log = _anomaly(
        GenericAnomalyKindV23.LOG_ERROR_CLUSTER,
        source=EvidenceSourceV22.LOGS,
        ref="e:test:logs:0:000000000000",
    )
    trace = _anomaly(
        GenericAnomalyKindV23.TRACE_LATENCY_OUTLIER,
        source=EvidenceSourceV22.TRACES,
        ref="e:test:traces:0:000000000000",
    )
    fact = SimpleNamespace(
        source=EvidenceSourceV22.LOGS,
        service="svc-a",
        evidence_refs=log.evidence_refs,
        signal_strength=SignalStrengthV22.STRONG,
        payload=LogSalientPayloadV22(
            schema_version="dta-v22.salient-log.v1",
            severity="ERROR",
            normalized_template="opaque downstream timeout",
            category=LogCategoryV22.DEPENDENCY_TIMEOUT,
            downstream_service=None,
            count=3,
        ),
    )

    projection = project_domain_v233(
        graph=_graph(log, trace),
        memory=_memory(fact),
    )

    assert projection.selected_domain is ProvisionalFaultDomainV23.DEPENDENCY
    assert set(projection.supporting_evidence_refs) == {
        *log.evidence_refs,
        *trace.evidence_refs,
    }


def test_concurrency_requires_fixed_lexicon_and_healthy_normal_context() -> None:
    log = _anomaly(
        GenericAnomalyKindV23.LOG_UNKNOWN_ERROR_PATTERN,
        source=EvidenceSourceV22.LOGS,
        ref="e:test:logs:0:000000000000",
        template="worker pool waiting for permit",
    )
    latency = _anomaly(GenericAnomalyKindV23.METRIC_LATENCY_OUTLIER)
    normal_resources = SimpleNamespace(
        source=EvidenceSourceV22.RESOURCES,
        service="svc-a",
        evidence_refs=("e:test:resources:0:000000000000",),
        signal_strength=SignalStrengthV22.NONE,
        payload=ResourceSalientPayloadV22(
            schema_version="dta-v22.salient-resource.v1",
            cpu_p50_percent=20.0,
            cpu_p95_percent=25.0,
            cpu_max_percent=30.0,
            memory_start_bytes=100,
            memory_end_bytes=100,
            memory_delta_bytes=0,
            memory_slope_bytes_per_second=0.0,
            sample_count=5,
            baseline_cpu_p95_percent=25.0,
            cpu_baseline_ratio=1.0,
            baseline_memory_slope_bytes_per_second=0.0,
        ),
    )

    projection = project_domain_v233(
        graph=_graph(
            log,
            latency,
            healthy=("svc-a",),
            complete_sources=(EvidenceSourceV22.RESOURCES,),
        ),
        memory=_memory(normal_resources),
    )

    assert projection.selected_domain is ProvisionalFaultDomainV23.CONCURRENCY
    assert "CONCURRENCY_CROSS_SOURCE_COMBINATION" in projection.reason_codes


def test_complete_normal_resource_coverage_subtracts_resource_support() -> None:
    error = _anomaly(GenericAnomalyKindV23.METRIC_ERROR_OUTLIER)
    normal_resources = SimpleNamespace(
        source=EvidenceSourceV22.RESOURCES,
        service="svc-a",
        evidence_refs=("e:test:resources:0:000000000000",),
        signal_strength=SignalStrengthV22.NONE,
        payload=ResourceSalientPayloadV22(
            schema_version="dta-v22.salient-resource.v1",
            cpu_p50_percent=20.0,
            cpu_p95_percent=25.0,
            cpu_max_percent=30.0,
            memory_start_bytes=100,
            memory_end_bytes=100,
            memory_delta_bytes=0,
            memory_slope_bytes_per_second=0.0,
            sample_count=5,
            baseline_cpu_p95_percent=25.0,
            cpu_baseline_ratio=1.0,
            baseline_memory_slope_bytes_per_second=0.0,
        ),
    )

    projection = project_domain_v233(
        graph=_graph(
            error,
            healthy=("svc-a",),
            complete_sources=(EvidenceSourceV22.RESOURCES,),
        ),
        memory=_memory(normal_resources),
    )

    resource = next(
        item
        for item in projection.domain_scores
        if item.domain is ProvisionalFaultDomainV23.RESOURCE
    )
    assert resource.score < 0.0
    assert normal_resources.evidence_refs[0] in projection.contradicting_evidence_refs


def test_ambiguous_projection_returns_unknown() -> None:
    latency = _anomaly(GenericAnomalyKindV23.METRIC_LATENCY_OUTLIER)

    projection = project_domain_v233(graph=_graph(latency), memory=_memory())

    assert projection.selected_domain is ProvisionalFaultDomainV23.UNKNOWN
    assert projection.status is DomainProjectionStatusV233.AMBIGUOUS


def test_v232_frozen_result_bytes_are_unchanged() -> None:
    expected = (
        "3977deb0192c3340ccf7ca391bbc9b85f003977cf3252933f9ae2fcc980e244a"
    )
    result = ROOT / "docs/results/dta-v232-conflict-aware-evaluation.json"

    import hashlib

    assert hashlib.sha256(result.read_bytes()).hexdigest() == expected


def test_v232_development_domain_gate_passes_without_provider() -> None:
    audit = build_v232_domain_audit_v233(repository_root=ROOT)

    assert audit.case_count == 14
    assert audit.provider_calls == 0
    assert audit.selected_root_correct >= 11
    assert audit.broad_domain_correct >= 9
    assert audit.evaluator_domain_top_two >= 13
    assert audit.evidence_ref_validity == 1.0
    assert audit.maximum_discovery_reads <= 3
