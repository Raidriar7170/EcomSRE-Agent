"""Deterministic replay fixtures and candidate-bound resolution for DTA v2.1."""

from __future__ import annotations

from typing import Literal

from pydantic import model_validator

from ecomsre.dta_v2.v21.candidate_filter import filter_runbook_candidates
from ecomsre.dta_v2.v21.contracts import (
    ActionDispositionV21,
    ActionParameterV21,
    ActionProposalV21,
    CandidateSetV21,
    DtaDiagnosisV21,
    DtaModelV21,
    EvidenceSourceV21,
    FaultDomainV21,
    FaultMechanismV21,
    ResolvedDiagnosisEvidenceViewV21,
    ResolvedEvidenceV21,
    Sha256V21,
    TerminalV21,
    build_resolved_diagnosis_evidence_view_v21,
    semantic_sha256,
)
from ecomsre.dta_v2.v21.registry import RunbookRegistryV21


class ReplayResolutionV21(DtaModelV21):
    schema_version: Literal["dta-v21.replay-resolution.v1"]
    run_id: str
    terminal: TerminalV21
    candidate_set: CandidateSetV21 | None
    proposal: ActionProposalV21 | None
    resolution_sha256: Sha256V21

    @model_validator(mode="after")
    def require_terminal_and_digest(self) -> ReplayResolutionV21:
        if self.terminal is TerminalV21.COMPLETED:
            if self.candidate_set is None or self.proposal is None:
                raise ValueError("completed replay resolution lacks an action decision")
        elif self.proposal is not None:
            raise ValueError("noncompleted replay resolution contains a proposal")
        expected = semantic_sha256(
            self.model_dump(mode="json", exclude={"resolution_sha256"})
        )
        if self.resolution_sha256 != expected:
            raise ValueError("replay resolution digest does not bind the result")
        return self


def build_replay_diagnosis(
    *,
    run_id: str,
    terminal: TerminalV21,
    root_service: str | None,
    fault_domain: FaultDomainV21 | None,
    mechanism: FaultMechanismV21 | None,
    evidence_sources: tuple[str, ...],
) -> tuple[DtaDiagnosisV21, ResolvedDiagnosisEvidenceViewV21]:
    """Build one hash-bound typed replay input without evaluator-control fields."""

    sources = tuple(
        sorted(
            (EvidenceSourceV21(item) for item in evidence_sources),
            key=list(EvidenceSourceV21).index,
        )
    )
    scope = (root_service,) if root_service is not None else ("bounded-system",)
    evidence = tuple(
        ResolvedEvidenceV21(
            evidence_ref=(
                f"evidence://{run_id}/{source.value.casefold()}/{ordinal:04d}"
            ),
            source=source,
            service_scope=scope,
            artifact_sha256=semantic_sha256(
                {
                    "run_id": run_id,
                    "source": source.value,
                    "service_scope": scope,
                    "ordinal": ordinal,
                }
            ),
        )
        for ordinal, source in enumerate(sources, start=1)
    )
    view = build_resolved_diagnosis_evidence_view_v21(
        run_id=run_id,
        evidence=evidence,
    )
    refs = tuple(item.evidence_ref for item in view.evidence)
    no_fault = terminal is TerminalV21.COMPLETED and root_service is None
    diagnosis = DtaDiagnosisV21(
        schema_version="dta-v21.diagnosis.v1",
        run_id=run_id,
        terminal=terminal,
        root_service=root_service,
        root_entity_ref=(None if root_service is None else f"service:{root_service}"),
        fault_domain=fault_domain,
        mechanism=mechanism,
        confidence=(0.9 if root_service is not None else None),
        supporting_evidence_refs=refs,
        contradicting_evidence_refs=(),
        evidence_source_types=sources,
        uncertainties=(
            ("Required evidence remains unresolved",)
            if terminal is not TerminalV21.COMPLETED
            else ()
        ),
        summary=(
            "Observed replay evidence supports no action"
            if no_fault
            else (
                "Available replay evidence is incomplete"
                if terminal is not TerminalV21.COMPLETED
                else "Typed replay evidence supports the bounded diagnosis"
            )
        ),
    )
    return diagnosis, view


def _build_action_proposal(
    *,
    diagnosis: DtaDiagnosisV21,
    candidate_set: CandidateSetV21,
    disposition: ActionDispositionV21,
) -> ActionProposalV21:
    candidate = (
        candidate_set.write_candidates[0]
        if disposition is ActionDispositionV21.EXECUTE_RUNBOOK
        else None
    )
    parameters = (
        tuple(
            ActionParameterV21(name=item.name, value=item.default_value)
            for item in candidate.parameters
            if item.default_value is not None
        )
        if candidate is not None
        else ()
    )
    evidence_refs = (
        diagnosis.supporting_evidence_refs
        if disposition is ActionDispositionV21.EXECUTE_RUNBOOK
        else ()
    )
    payload: dict[str, object] = {
        "schema_version": "dta-v21.action-proposal.v1",
        "run_id": diagnosis.run_id,
        "disposition": disposition,
        "candidate_set_sha256": candidate_set.candidate_set_sha256,
        "diagnosis_sha256": candidate_set.diagnosis_sha256,
        "resolved_evidence_sha256": candidate_set.resolved_evidence_sha256,
        "registry_sha256": candidate_set.registry_sha256,
        "runbook_id": None if candidate is None else candidate.runbook_id,
        "runbook_sha256": None if candidate is None else candidate.runbook_sha256,
        "target_service": None if candidate is None else candidate.target_service,
        "parameters": parameters,
        "supporting_evidence_refs": evidence_refs,
        "rationale": (
            "No bounded write is supported by the observed evidence"
            if candidate is None
            else "The unique trusted candidate matches the typed evidence"
        ),
    }
    digest_payload = {
        **payload,
        "disposition": disposition.value,
        "runbook_id": None if candidate is None else candidate.runbook_id.value,
        "parameters": [item.model_dump(mode="json") for item in parameters],
    }
    return ActionProposalV21.model_validate(
        {**payload, "proposal_sha256": semantic_sha256(digest_payload)}
    )


def _build_resolution(
    *,
    run_id: str,
    terminal: TerminalV21,
    candidate_set: CandidateSetV21 | None,
    proposal: ActionProposalV21 | None,
) -> ReplayResolutionV21:
    payload: dict[str, object] = {
        "schema_version": "dta-v21.replay-resolution.v1",
        "run_id": run_id,
        "terminal": terminal,
        "candidate_set": candidate_set,
        "proposal": proposal,
    }
    digest_payload = {
        **payload,
        "terminal": terminal.value,
        "candidate_set": (
            None if candidate_set is None else candidate_set.model_dump(mode="json")
        ),
        "proposal": None if proposal is None else proposal.model_dump(mode="json"),
    }
    return ReplayResolutionV21.model_validate(
        {**payload, "resolution_sha256": semantic_sha256(digest_payload)}
    )


def resolve_replay_case(
    *,
    diagnosis: DtaDiagnosisV21,
    diagnosis_evidence: ResolvedDiagnosisEvidenceViewV21,
    registry: RunbookRegistryV21,
    exact_target: str | None,
) -> ReplayResolutionV21:
    """Resolve a typed replay case without reading scenario or evaluator truth."""

    if diagnosis.terminal is not TerminalV21.COMPLETED:
        return _build_resolution(
            run_id=diagnosis.run_id,
            terminal=diagnosis.terminal,
            candidate_set=None,
            proposal=None,
        )
    candidate_set = filter_runbook_candidates(
        diagnosis=diagnosis,
        diagnosis_evidence=diagnosis_evidence,
        registry=registry,
        exact_target=exact_target,
    )
    if diagnosis.root_service is None:
        proposal = _build_action_proposal(
            diagnosis=diagnosis,
            candidate_set=candidate_set,
            disposition=ActionDispositionV21.NO_ACTION,
        )
        return _build_resolution(
            run_id=diagnosis.run_id,
            terminal=TerminalV21.COMPLETED,
            candidate_set=candidate_set,
            proposal=proposal,
        )
    if len(candidate_set.write_candidates) != 1:
        return _build_resolution(
            run_id=diagnosis.run_id,
            terminal=TerminalV21.ABSTAIN,
            candidate_set=candidate_set,
            proposal=None,
        )
    proposal = _build_action_proposal(
        diagnosis=diagnosis,
        candidate_set=candidate_set,
        disposition=ActionDispositionV21.EXECUTE_RUNBOOK,
    )
    return _build_resolution(
        run_id=diagnosis.run_id,
        terminal=TerminalV21.COMPLETED,
        candidate_set=candidate_set,
        proposal=proposal,
    )


__all__ = (
    "ReplayResolutionV21",
    "build_replay_diagnosis",
    "resolve_replay_case",
)
