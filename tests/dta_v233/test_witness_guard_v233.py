from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import cast

from ecomsre.dta_v2.tool_contracts import EndpointState
from ecomsre.dta_v2.v22.memory import (
    ResourceSalientPayloadV22,
    RuntimeSalientPayloadV22,
    SalientEvidenceMemoryV22,
    SignalStrengthV22,
)
from ecomsre.dta_v2.v22.read_contracts import EvidenceSourceV22, RuntimeStateV22
from ecomsre.dta_v2.v23.contradiction_witness_v233 import (
    ContradictionKindV233,
    WitnessStrengthV233,
    build_contradiction_witnesses_v233,
)
from ecomsre.dta_v2.v23.evaluation_data_v232 import AdmissionStratumV232
from ecomsre.dta_v2.v23.generic_anomalies import (
    GenericAnomalyKindV23,
    _build_anomaly,
)
from ecomsre.dta_v2.v23.irreconcilable_guard_v233 import (
    IrreconcilableGuardDispositionV233,
    evaluate_irreconcilable_guard_v233,
)
from ecomsre.dta_v2.v23.residual_graph import ResidualEvidenceGraphV23
from ecomsre.dta_v2.v23.witness_audit_v233 import build_v232_witness_audit_v233


ROOT = Path(__file__).resolve().parents[2]


def _anomaly(
    kind: GenericAnomalyKindV23,
    *,
    service: str,
    source: EvidenceSourceV22,
    related: tuple[str, ...] = (),
    ref: str,
):
    return _build_anomaly(
        kind=kind,
        source=source,
        service=service,
        related_services=related,
        strength=SignalStrengthV22.STRONG,
        summary=f"{service} has {kind.value}",
        evidence_refs=(ref,),
        observed_values={"operation": f"op-{service}"},
    )


def _graph(
    *anomalies: object,
    healthy: tuple[str, ...] = (),
    complete_sources: tuple[EvidenceSourceV22, ...] = (),
) -> ResidualEvidenceGraphV23:
    services = tuple(sorted({getattr(item, "service") for item in anomalies}))
    return cast(
        ResidualEvidenceGraphV23,
        SimpleNamespace(
            candidate_services=services,
            generic_anomalies=tuple(anomalies),
            residual_anomaly_ids=tuple(
                sorted(getattr(item, "anomaly_id") for item in anomalies)
            ),
            source_coverage=tuple(
                SimpleNamespace(
                    source=source,
                    queried=source in complete_sources,
                    covered_services=services if source in complete_sources else (),
                    successful_observations=int(source in complete_sources),
                    failed_observations=0,
                )
                for source in EvidenceSourceV22
            ),
            healthy_runtime_services=healthy,
        ),
    )


def _memory(*facts: object) -> SalientEvidenceMemoryV22:
    return cast(
        SalientEvidenceMemoryV22,
        SimpleNamespace(salient_facts=tuple(facts), predicates=()),
    )


def test_same_service_runtime_contradiction_has_evidence_on_both_sides() -> None:
    anomaly = _anomaly(
        GenericAnomalyKindV23.RUNTIME_NOT_RUNNING,
        service="svc-a",
        source=EvidenceSourceV22.RUNTIME,
        ref="e:test:runtime:0:000000000000",
    )
    healthy = SimpleNamespace(
        source=EvidenceSourceV22.RUNTIME,
        service="svc-a",
        evidence_refs=("e:test:runtime:1:000000000000",),
        signal_strength=SignalStrengthV22.STRONG,
        payload=RuntimeSalientPayloadV22(
            schema_version="dta-v22.salient-runtime.v1",
            state=RuntimeStateV22.RUNNING,
            healthy=True,
            endpoint=EndpointState.READY,
            restart_count=0,
            exit_code=0,
        ),
    )

    witnesses = build_contradiction_witnesses_v233(
        graph=_graph(
            anomaly,
            healthy=("svc-a",),
            complete_sources=(EvidenceSourceV22.RUNTIME,),
        ),
        memory=_memory(healthy),
        observation_scope="case-a",
    )

    witness = witnesses[0]
    assert witness.kind is ContradictionKindV233.SAME_SERVICE_RUNTIME_STATE
    assert witness.strength is WitnessStrengthV233.STRONG
    assert witness.left_evidence_refs == anomaly.evidence_refs
    assert witness.right_evidence_refs == healthy.evidence_refs


def test_same_service_resource_contradiction_requires_complete_normality() -> None:
    anomaly = _anomaly(
        GenericAnomalyKindV23.RESOURCE_CPU_OUTLIER,
        service="svc-a",
        source=EvidenceSourceV22.RESOURCES,
        ref="e:test:resources:0:000000000000",
    )
    normal = SimpleNamespace(
        source=EvidenceSourceV22.RESOURCES,
        service="svc-a",
        evidence_refs=("e:test:resources:1:000000000000",),
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

    incomplete = build_contradiction_witnesses_v233(
        graph=_graph(anomaly),
        memory=_memory(normal),
        observation_scope="case-a",
    )
    complete = build_contradiction_witnesses_v233(
        graph=_graph(anomaly, complete_sources=(EvidenceSourceV22.RESOURCES,)),
        memory=_memory(normal),
        observation_scope="case-a",
    )

    assert incomplete[0].strength is WitnessStrengthV233.WEAK
    assert not incomplete[0].coverage_satisfied
    assert complete[0].kind is ContradictionKindV233.SAME_SERVICE_RESOURCE_STATE
    assert complete[0].strength is WitnessStrengthV233.STRONG


def test_disjoint_first_error_claims_build_one_typed_witness() -> None:
    left = _anomaly(
        GenericAnomalyKindV23.TRACE_ERROR_LOCALIZATION,
        service="svc-a",
        related=("svc-b",),
        source=EvidenceSourceV22.TRACES,
        ref="e:test:traces:0:000000000000",
    )
    right = _anomaly(
        GenericAnomalyKindV23.TRACE_ERROR_LOCALIZATION,
        service="svc-b",
        related=("svc-a",),
        source=EvidenceSourceV22.TRACES,
        ref="e:test:traces:1:000000000000",
    )

    witnesses = build_contradiction_witnesses_v233(
        graph=_graph(left, right, complete_sources=(EvidenceSourceV22.TRACES,)),
        memory=_memory(),
        observation_scope="case-a",
    )

    assert len(witnesses) == 1
    assert witnesses[0].kind is ContradictionKindV233.MUTUALLY_EXCLUSIVE_FIRST_ERROR
    assert witnesses[0].strength is WitnessStrengthV233.STRONG
    assert witnesses[0].services == ("svc-a", "svc-b")


def test_multi_service_or_multi_domain_evidence_alone_is_not_contradiction() -> None:
    left = _anomaly(
        GenericAnomalyKindV23.METRIC_ERROR_OUTLIER,
        service="svc-a",
        source=EvidenceSourceV22.METRICS,
        ref="e:test:metrics:0:000000000000",
    )
    right = _anomaly(
        GenericAnomalyKindV23.METRIC_LATENCY_OUTLIER,
        service="svc-b",
        source=EvidenceSourceV22.METRICS,
        ref="e:test:metrics:1:000000000000",
    )

    witnesses = build_contradiction_witnesses_v233(
        graph=_graph(left, right, complete_sources=(EvidenceSourceV22.METRICS,)),
        memory=_memory(),
        observation_scope="case-a",
    )

    assert witnesses == ()


def test_guard_open_insufficient_resolvable_and_irreconcilable_states() -> None:
    left = _anomaly(
        GenericAnomalyKindV23.TRACE_ERROR_LOCALIZATION,
        service="svc-a",
        related=("svc-b",),
        source=EvidenceSourceV22.TRACES,
        ref="e:test:traces:0:000000000000",
    )
    right = _anomaly(
        GenericAnomalyKindV23.TRACE_ERROR_LOCALIZATION,
        service="svc-b",
        related=("svc-a",),
        source=EvidenceSourceV22.TRACES,
        ref="e:test:traces:1:000000000000",
    )
    weak = build_contradiction_witnesses_v233(
        graph=_graph(left, right),
        memory=_memory(),
        observation_scope="case-a",
    )
    strong = build_contradiction_witnesses_v233(
        graph=_graph(left, right, complete_sources=(EvidenceSourceV22.TRACES,)),
        memory=_memory(),
        observation_scope="case-a",
    )

    opened = evaluate_irreconcilable_guard_v233(
        witnesses=(), legal_sources=(), remaining_reads=0, guard_read_used=False
    )
    insufficient = evaluate_irreconcilable_guard_v233(
        witnesses=weak, legal_sources=(), remaining_reads=0, guard_read_used=False
    )
    resolvable = evaluate_irreconcilable_guard_v233(
        witnesses=strong,
        legal_sources=(EvidenceSourceV22.LOGS,),
        remaining_reads=1,
        guard_read_used=False,
    )
    closed = evaluate_irreconcilable_guard_v233(
        witnesses=strong,
        legal_sources=(EvidenceSourceV22.LOGS,),
        remaining_reads=0,
        guard_read_used=True,
    )

    assert opened.disposition is IrreconcilableGuardDispositionV233.OPEN
    assert insufficient.disposition is (
        IrreconcilableGuardDispositionV233.INSUFFICIENT_COVERAGE
    )
    assert resolvable.disposition is IrreconcilableGuardDispositionV233.RESOLVABLE
    assert resolvable.required_additional_reads == (EvidenceSourceV22.LOGS,)
    assert closed.disposition is IrreconcilableGuardDispositionV233.IRRECONCILABLE


def test_v232_development_witness_gate_blocks_controls_not_novelty() -> None:
    audit = build_v232_witness_audit_v233(repository_root=ROOT)
    controls = tuple(
        entry
        for entry in audit.entries
        if entry.stratum is AdmissionStratumV232.INSUFFICIENT_IRRECONCILABLE
    )

    assert audit.irreconcilable_controls_blocked == 3
    assert audit.novelty_cases_blocked <= 1
    assert audit.registered_known_unchanged == 4
    assert audit.no_incident_unchanged == 3
    assert audit.maximum_discovery_reads <= 3
    assert audit.provider_calls == 0
    assert len(controls) == 3
    assert all(
        entry.decision.disposition
        is IrreconcilableGuardDispositionV233.IRRECONCILABLE
        and entry.report_generated is False
        and entry.guard_read_used
        and sum(read.guard_directed for read in entry.reads) == 1
        for entry in controls
    )
