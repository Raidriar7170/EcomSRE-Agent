"""Truth-isolated deterministic Runbook candidate filtering for DTA v2.1."""

from __future__ import annotations

from ecomsre.dta_v2.v21.contracts import (
    ActionDispositionV21,
    CandidateRunbookV21,
    CandidateSetV21,
    DtaDiagnosisV21,
    ResolvedDiagnosisEvidenceViewV21,
    TerminalV21,
    semantic_sha256,
)
from ecomsre.dta_v2.v21.registry import RunbookRegistryV21


class CandidateFilterError(ValueError):
    """Fail-closed rejection before action selection."""


def _build_candidate_set(
    *,
    diagnosis: DtaDiagnosisV21,
    diagnosis_evidence: ResolvedDiagnosisEvidenceViewV21,
    registry: RunbookRegistryV21,
    exact_target: str | None,
    write_candidates: tuple[CandidateRunbookV21, ...],
) -> CandidateSetV21:
    nonwrite = (
        ActionDispositionV21.ESCALATE_HUMAN,
        ActionDispositionV21.NO_ACTION,
    )
    ordered = tuple(
        sorted(
            write_candidates,
            key=lambda item: (item.runbook_id.value, item.target_service),
        )
    )
    payload: dict[str, object] = {
        "schema_version": "dta-v21.candidate-set.v1",
        "run_id": diagnosis.run_id,
        "diagnosis_sha256": semantic_sha256(diagnosis.model_dump(mode="json")),
        "resolved_evidence_sha256": diagnosis_evidence.resolved_evidence_sha256,
        "registry_sha256": registry.registry_sha256,
        "exact_target": exact_target,
        "write_candidates": ordered,
        "allowed_nonwrite_dispositions": nonwrite,
    }
    digest_payload = {
        **payload,
        "write_candidates": [item.model_dump(mode="json") for item in ordered],
        "allowed_nonwrite_dispositions": [item.value for item in nonwrite],
    }
    return CandidateSetV21.model_validate(
        {**payload, "candidate_set_sha256": semantic_sha256(digest_payload)}
    )


def filter_runbook_candidates(
    *,
    diagnosis: DtaDiagnosisV21,
    diagnosis_evidence: ResolvedDiagnosisEvidenceViewV21,
    registry: RunbookRegistryV21,
    exact_target: str | None,
) -> CandidateSetV21:
    """Filter only on Diagnosis, resolved evidence, registry, and exact target."""

    diagnosis = DtaDiagnosisV21.model_validate(diagnosis.model_dump(mode="python"))
    diagnosis_evidence = ResolvedDiagnosisEvidenceViewV21.model_validate(
        diagnosis_evidence.model_dump(mode="python")
    )
    registry = RunbookRegistryV21.model_validate(registry.model_dump(mode="python"))
    if diagnosis.run_id != diagnosis_evidence.run_id:
        raise CandidateFilterError("resolved evidence belongs to another run")

    diagnosis_refs = set(
        diagnosis.supporting_evidence_refs + diagnosis.contradicting_evidence_refs
    )
    resolved_by_ref = {item.evidence_ref: item for item in diagnosis_evidence.evidence}
    if set(resolved_by_ref) != diagnosis_refs:
        raise CandidateFilterError("diagnosis evidence is not exactly resolved")

    if diagnosis.root_service != exact_target:
        raise CandidateFilterError("diagnosis differs from the trusted exact target")
    if diagnosis.terminal is not TerminalV21.COMPLETED:
        if exact_target is not None:
            raise CandidateFilterError(
                "noncompleted diagnosis cannot bind an exact target"
            )
        return _build_candidate_set(
            diagnosis=diagnosis,
            diagnosis_evidence=diagnosis_evidence,
            registry=registry,
            exact_target=None,
            write_candidates=(),
        )

    root = diagnosis.root_service
    domain = diagnosis.fault_domain
    mechanism = diagnosis.mechanism
    if root is None and domain is None and mechanism is None:
        return _build_candidate_set(
            diagnosis=diagnosis,
            diagnosis_evidence=diagnosis_evidence,
            registry=registry,
            exact_target=None,
            write_candidates=(),
        )
    if root is None or domain is None or mechanism is None:
        raise CandidateFilterError("completed diagnosis has an incomplete typed fault")
    if diagnosis.root_entity_ref != f"service:{root}":
        raise CandidateFilterError(
            "diagnosis root entity differs from the exact target"
        )

    support = tuple(
        resolved_by_ref[reference] for reference in diagnosis.supporting_evidence_refs
    )
    if any(root not in item.service_scope for item in support):
        raise CandidateFilterError("supporting evidence is outside the exact target")
    available_sources = {item.source for item in support}
    candidates = tuple(
        CandidateRunbookV21(
            schema_version="dta-v21.candidate-runbook.v1",
            runbook_id=runbook.runbook_id,
            runbook_sha256=runbook.semantic_sha256,
            target_service=root,
            risk_level=runbook.risk_level,
            backend=runbook.backend,
            parameters=runbook.parameters,
            required_evidence_sources=runbook.required_evidence_for_target(root),
        )
        for runbook in registry.runbooks
        if domain in runbook.supported_fault_domains
        and mechanism in runbook.supported_mechanisms
        and root in runbook.target_services
        and set(runbook.required_evidence_for_target(root)).issubset(available_sources)
    )
    return _build_candidate_set(
        diagnosis=diagnosis,
        diagnosis_evidence=diagnosis_evidence,
        registry=registry,
        exact_target=root,
        write_candidates=candidates,
    )


__all__ = ("CandidateFilterError", "filter_runbook_candidates")
