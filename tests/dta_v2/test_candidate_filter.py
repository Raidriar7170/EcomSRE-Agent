from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from ecomsre.dta_v2.candidate_filter import (
    CandidateFilterError,
    filter_runbook_candidates,
)
from ecomsre.dta_v2.contracts import (
    ActionDisposition,
    DtaDiagnosis,
    EvidenceSource,
    FaultDomain,
    FaultMechanism,
    ResolvedEvidence,
    ResolvedEvidenceView,
    RunbookId,
    Terminal,
    build_resolved_evidence_view,
)
from ecomsre.dta_v2.registry import RunbookRegistry, load_runbook_registry


RUN_ID = "a" * 32
REPO_ROOT = Path(__file__).resolve().parents[2]
RUNBOOK_ROOT = REPO_ROOT / "config" / "dta-v2" / "runbooks"


def diagnosis(
    *,
    mechanism: FaultMechanism,
    domain: FaultDomain,
    service: str,
    sources: tuple[EvidenceSource, ...],
) -> DtaDiagnosis:
    refs = tuple(
        f"evidence://{RUN_ID}/{source.value.lower()}/{index:04d}"
        for index, source in enumerate(sources, start=1)
    )
    return DtaDiagnosis(
        schema_version="dta-v2.diagnosis.v1",
        run_id=RUN_ID,
        terminal=Terminal.COMPLETED,
        root_service=service,
        root_entity_ref=f"service:{service}",
        fault_domain=domain,
        mechanism=mechanism,
        confidence=0.9,
        supporting_evidence_refs=refs,
        contradicting_evidence_refs=(),
        evidence_source_types=sources,
        uncertainties=(),
        summary="Bounded evidence supports one compatible mechanism.",
    )


def resolved_evidence(input_diagnosis: DtaDiagnosis) -> ResolvedEvidenceView:
    refs = tuple(
        sorted(
            input_diagnosis.supporting_evidence_refs
            + input_diagnosis.contradicting_evidence_refs
        )
    )
    evidence = tuple(
        ResolvedEvidence(
            evidence_ref=reference,
            source=EvidenceSource(reference.split("/")[3].upper()),
            artifact_sha256=hashlib.sha256(reference.encode()).hexdigest(),
        )
        for reference in refs
    )
    return build_resolved_evidence_view(run_id=RUN_ID, evidence=evidence)


def filter_candidates(
    input_diagnosis: DtaDiagnosis,
    registry: RunbookRegistry,
):
    return filter_runbook_candidates(
        diagnosis=input_diagnosis,
        registry=registry,
        resolved_evidence=resolved_evidence(input_diagnosis),
    )


def test_filter_selects_only_payment_rollback_candidate() -> None:
    registry = load_runbook_registry(RUNBOOK_ROOT)
    input_diagnosis = diagnosis(
        mechanism=FaultMechanism.CONFIGURATION_ERROR,
        domain=FaultDomain.CONFIGURATION,
        service="payment",
        sources=(EvidenceSource.METRICS, EvidenceSource.TRACES),
    )
    result = filter_candidates(input_diagnosis, registry)

    assert tuple(item.runbook_id for item in result.write_candidates) == (
        RunbookId.ROLLBACK_CONFIGURATION,
    )
    assert result.write_candidates[0].target_service == "payment"
    assert result.allowed_nonwrite_dispositions == (
        ActionDisposition.ESCALATE_HUMAN,
        ActionDisposition.NO_ACTION,
    )
    assert result.candidate_set_sha256


def test_filter_fails_closed_when_required_evidence_is_missing() -> None:
    registry = load_runbook_registry(RUNBOOK_ROOT)
    input_diagnosis = diagnosis(
        mechanism=FaultMechanism.SERVICE_UNAVAILABLE,
        domain=FaultDomain.SERVICE_RUNTIME,
        service="recommendation",
        sources=(EvidenceSource.METRICS,),
    )
    result = filter_candidates(input_diagnosis, registry)

    assert result.write_candidates == ()


def test_filter_does_not_count_contradicting_or_unresolved_evidence() -> None:
    registry = load_runbook_registry(RUNBOOK_ROOT)
    metrics_ref = f"evidence://{RUN_ID}/metrics/0001"
    traces_ref = f"evidence://{RUN_ID}/traces/0002"
    input_diagnosis = DtaDiagnosis(
        schema_version="dta-v2.diagnosis.v1",
        run_id=RUN_ID,
        terminal=Terminal.COMPLETED,
        root_service="payment",
        root_entity_ref="service:payment",
        fault_domain=FaultDomain.CONFIGURATION,
        mechanism=FaultMechanism.CONFIGURATION_ERROR,
        confidence=0.8,
        supporting_evidence_refs=(metrics_ref,),
        contradicting_evidence_refs=(traces_ref,),
        evidence_source_types=(EvidenceSource.METRICS, EvidenceSource.TRACES),
        uncertainties=(),
        summary="Metrics support the hypothesis while traces contradict it.",
    )
    result = filter_candidates(input_diagnosis, registry)

    assert result.write_candidates == ()

    incomplete_view = build_resolved_evidence_view(
        run_id=RUN_ID,
        evidence=(
            ResolvedEvidence(
                evidence_ref=metrics_ref,
                source=EvidenceSource.METRICS,
                artifact_sha256="d" * 64,
            ),
        ),
    )
    with pytest.raises(CandidateFilterError, match="exactly resolved"):
        filter_runbook_candidates(
            diagnosis=input_diagnosis,
            registry=registry,
            resolved_evidence=incomplete_view,
        )


def test_filter_revalidates_resolved_evidence_objects() -> None:
    registry = load_runbook_registry(RUNBOOK_ROOT)
    input_diagnosis = diagnosis(
        mechanism=FaultMechanism.SERVICE_UNAVAILABLE,
        domain=FaultDomain.SERVICE_RUNTIME,
        service="recommendation",
        sources=(EvidenceSource.METRICS, EvidenceSource.LOGS),
    )
    evidence = resolved_evidence(input_diagnosis)
    spoofed_log = evidence.evidence[1].model_copy(
        update={"source": EvidenceSource.RUNTIME}
    )
    spoofed_view = evidence.model_copy(
        update={"evidence": (evidence.evidence[0], spoofed_log)}
    )

    with pytest.raises(ValueError, match="source differs"):
        filter_runbook_candidates(
            diagnosis=input_diagnosis,
            registry=registry,
            resolved_evidence=spoofed_view,
        )


def test_filter_fails_closed_on_wrong_domain_or_target_service() -> None:
    registry = load_runbook_registry(RUNBOOK_ROOT)

    wrong_domain_diagnosis = diagnosis(
        mechanism=FaultMechanism.CONFIGURATION_ERROR,
        domain=FaultDomain.LOCAL_RESOURCE,
        service="payment",
        sources=(EvidenceSource.METRICS, EvidenceSource.TRACES),
    )
    wrong_target_diagnosis = diagnosis(
        mechanism=FaultMechanism.CONFIGURATION_ERROR,
        domain=FaultDomain.CONFIGURATION,
        service="email",
        sources=(EvidenceSource.METRICS, EvidenceSource.TRACES),
    )
    wrong_domain = filter_candidates(wrong_domain_diagnosis, registry)
    wrong_target = filter_candidates(wrong_target_diagnosis, registry)

    assert wrong_domain.write_candidates == ()
    assert wrong_target.write_candidates == ()


def test_filter_returns_no_write_candidate_for_unknown_mechanism() -> None:
    registry = load_runbook_registry(RUNBOOK_ROOT)
    input_diagnosis = diagnosis(
        mechanism=FaultMechanism.UNKNOWN,
        domain=FaultDomain.UNKNOWN,
        service="checkout",
        sources=(EvidenceSource.METRICS, EvidenceSource.LOGS),
    )
    result = filter_candidates(input_diagnosis, registry)

    assert result.write_candidates == ()
    assert result.allowed_nonwrite_dispositions[0] is ActionDisposition.ESCALATE_HUMAN


def test_filter_rejects_noncompleted_diagnosis() -> None:
    registry = load_runbook_registry(RUNBOOK_ROOT)
    incomplete = DtaDiagnosis(
        schema_version="dta-v2.diagnosis.v1",
        run_id=RUN_ID,
        terminal=Terminal.NEED_MORE_EVIDENCE,
        root_service=None,
        root_entity_ref=None,
        fault_domain=None,
        mechanism=None,
        confidence=0.3,
        supporting_evidence_refs=(
            f"evidence://{RUN_ID}/metrics/0001",
        ),
        contradicting_evidence_refs=(),
        evidence_source_types=(EvidenceSource.METRICS,),
        uncertainties=("Runtime state is missing.",),
        summary="Additional bounded evidence is required.",
    )

    with pytest.raises(CandidateFilterError, match="completed diagnosis"):
        filter_runbook_candidates(
            diagnosis=incomplete,
            registry=registry,
            resolved_evidence=resolved_evidence(incomplete),
        )


def test_candidate_filter_is_deterministic_for_same_inputs() -> None:
    registry = load_runbook_registry(RUNBOOK_ROOT)
    input_diagnosis = diagnosis(
        mechanism=FaultMechanism.MEMORY_LEAK,
        domain=FaultDomain.LOCAL_RESOURCE,
        service="email",
        sources=(
            EvidenceSource.METRICS,
            EvidenceSource.RUNTIME,
            EvidenceSource.RESOURCES,
        ),
    )

    first = filter_candidates(input_diagnosis, registry)
    second = filter_candidates(input_diagnosis, registry)

    assert first == second
    assert tuple(item.runbook_id for item in first.write_candidates) == (
        RunbookId.MITIGATE_MEMORY_LEAK,
    )
