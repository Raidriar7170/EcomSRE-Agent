"""Deterministic Diagnosis-to-Action runbook candidate filtering."""

from __future__ import annotations

from ecomsre.dta_v2.contracts import (
    CandidateRunbook,
    CandidateSet,
    DtaDiagnosis,
    ResolvedDiagnosisEvidenceView,
    Terminal,
    build_candidate_set,
    semantic_sha256,
)
from ecomsre.dta_v2.registry import RunbookRegistry


class CandidateFilterError(ValueError):
    """Fail-closed rejection before action selection."""


def filter_runbook_candidates(
    *,
    diagnosis: DtaDiagnosis,
    registry: RunbookRegistry,
    diagnosis_evidence: ResolvedDiagnosisEvidenceView,
) -> CandidateSet:
    """Return only root-, mechanism-, and evidence-compatible trusted runbooks."""

    diagnosis = DtaDiagnosis.model_validate(diagnosis.model_dump(mode="python"))
    registry = RunbookRegistry.model_validate(registry.model_dump(mode="python"))
    diagnosis_evidence = ResolvedDiagnosisEvidenceView.model_validate(
        diagnosis_evidence.model_dump(mode="python")
    )
    if diagnosis.terminal is not Terminal.COMPLETED:
        raise CandidateFilterError("candidate filtering requires a completed diagnosis")
    root_service = diagnosis.root_service
    fault_domain = diagnosis.fault_domain
    mechanism = diagnosis.mechanism
    if root_service is None or fault_domain is None or mechanism is None:
        raise CandidateFilterError("completed diagnosis lacks a compatible root")
    if diagnosis.run_id != diagnosis_evidence.run_id:
        raise CandidateFilterError("resolved evidence belongs to another run")

    diagnosis_refs = set(
        diagnosis.supporting_evidence_refs + diagnosis.contradicting_evidence_refs
    )
    resolved_by_ref = {
        item.evidence_ref: item for item in diagnosis_evidence.evidence
    }
    if set(resolved_by_ref) != diagnosis_refs:
        raise CandidateFilterError("diagnosis evidence is not exactly resolved")
    available_sources = {
        resolved_by_ref[reference].source
        for reference in diagnosis.supporting_evidence_refs
    }
    candidates = tuple(
        CandidateRunbook(
            runbook_id=runbook.runbook_id,
            runbook_sha256=semantic_sha256(runbook.model_dump(mode="json")),
            target_service=root_service,
            risk_level=runbook.risk_level,
            parameter_names=tuple(parameter.name for parameter in runbook.parameters),
        )
        for runbook in registry.runbooks
        if mechanism in runbook.supported_mechanisms
        and fault_domain in runbook.supported_fault_domains
        and root_service in runbook.target_services
        and diagnosis.root_entity_ref == f"service:{root_service}"
        and set(runbook.required_evidence_sources).issubset(available_sources)
    )
    diagnosis_sha256 = semantic_sha256(diagnosis.model_dump(mode="json"))
    return build_candidate_set(
        run_id=diagnosis.run_id,
        diagnosis_sha256=diagnosis_sha256,
        resolved_evidence_sha256=diagnosis_evidence.resolved_evidence_sha256,
        registry_sha256=registry.registry_sha256,
        write_candidates=candidates,
    )
