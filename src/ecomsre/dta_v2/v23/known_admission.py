"""Active-view bridge into the unchanged v2.2 Diagnosis admission boundary."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import StrictBool, model_validator

from ecomsre.dta_v2.v22.controller_contracts import NO_INCIDENT_HYPOTHESIS_ID_V22
from ecomsre.dta_v2.v22.diagnosis import (
    AdmittedDiagnosisV22,
    DiagnosisAdmissionResultV22,
    DiagnosisTerminalV22,
    HypothesisDefinitionV22,
    RawSemanticDiagnosisProposalV22,
    admit_diagnosis_v22,
)
from ecomsre.dta_v2.v22.memory import SalientEvidenceMemoryV22
from ecomsre.dta_v2.v22.predicates import (
    MechanismV22,
    build_default_evidence_support_policy_v22,
    evaluate_support_v22,
)
from ecomsre.dta_v2.v22.read_contracts import DtaModelV22, semantic_sha256_v22
from ecomsre.dta_v2.v23.ontology_view import ActiveOntologyViewV23


class KnownAdmissionStateV23(DtaModelV22):
    """Truth-independent result of running active hypotheses through v2.2."""

    schema_version: Literal["dta-v23.known-admission-state.v1"]
    admitted_diagnoses: tuple[AdmittedDiagnosisV22, ...]
    no_incident_admission: DiagnosisAdmissionResultV22
    conflicting_evidence: StrictBool
    memory_sha256: str
    support_policy_sha256: str
    state_sha256: str

    @model_validator(mode="after")
    def require_state(self) -> "KnownAdmissionStateV23":
        digests = tuple(item.diagnosis_sha256 for item in self.admitted_diagnoses)
        if digests != tuple(sorted(set(digests))):
            raise ValueError("known admissions are not canonical")
        if any(
            item.memory_sha256 != self.memory_sha256
            or item.policy_sha256 != self.support_policy_sha256
            for item in self.admitted_diagnoses
        ):
            raise ValueError("known admission binding differs")
        if self.conflicting_evidence != (len(self.admitted_diagnoses) > 1):
            raise ValueError("known admission conflict flag differs")
        if self.no_incident_admission.terminal not in {
            DiagnosisTerminalV22.NO_INCIDENT,
            DiagnosisTerminalV22.FAILED,
            DiagnosisTerminalV22.ABSTAIN,
        }:
            raise ValueError("No-Incident admission has an incident terminal")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"state_sha256"})
        )
        if self.state_sha256 != expected:
            raise ValueError("known admission state digest differs")
        return self

    @property
    def admitted_diagnosis(self) -> AdmittedDiagnosisV22 | None:
        return self.admitted_diagnoses[0] if len(self.admitted_diagnoses) == 1 else None

    @property
    def no_incident_admissible(self) -> bool:
        return self.no_incident_admission.terminal is DiagnosisTerminalV22.NO_INCIDENT


def _parent_for(
    *,
    target: str,
    mechanism: MechanismV22,
    topology_edges: tuple[tuple[str, str], ...],
) -> str | None:
    if mechanism is not MechanismV22.DEPENDENCY_LATENCY:
        return None
    return next(
        (
            right if left == target else left
            for left, right in topology_edges
            if target in {left, right}
        ),
        None,
    )


def build_known_admission_state_v23(
    *,
    view: ActiveOntologyViewV23,
    memory: SalientEvidenceMemoryV22,
    topology_edges: tuple[tuple[str, str], ...] = (),
    evidence_source_unavailable: bool = False,
) -> KnownAdmissionStateV23:
    """Use the active view for eligibility and v2.2 for every terminal decision."""

    policy = build_default_evidence_support_policy_v22()
    if policy.policy_sha256 != view.support_policy_sha256:
        raise ValueError("active ontology view is not bound to the frozen base policy")
    admitted: list[AdmittedDiagnosisV22] = []
    for entry in view.active_hypotheses:
        target = entry.target_service
        if target is None or entry.mechanism in {
            MechanismV22.NO_INCIDENT,
            MechanismV22.UNKNOWN,
        }:
            continue
        parent = _parent_for(
            target=target,
            mechanism=entry.mechanism,
            topology_edges=topology_edges,
        )
        if entry.mechanism is MechanismV22.DEPENDENCY_LATENCY and parent is None:
            continue
        definition = HypothesisDefinitionV22.build(
            hypothesis_id=entry.hypothesis_id,
            target_service=target,
            parent_service=parent,
            root_service=target,
            fault_domain=entry.fault_domain,
            mechanism=entry.mechanism,
            root_entity_ref=f"service:{target}",
        )
        support = evaluate_support_v22(
            policy=policy,
            mechanism=entry.mechanism,
            target_service=target,
            parent_service=parent,
            predicates=memory.predicates,
        )
        proposal = RawSemanticDiagnosisProposalV22.build(
            hypothesis_id=entry.hypothesis_id,
            supporting_evidence_refs=support.supporting_evidence_refs,
            contradicting_evidence_refs=(),
        )
        result = admit_diagnosis_v22(
            proposal=proposal,
            hypotheses=(definition,),
            memory=memory,
            policy=policy,
            candidate_services=view.candidate_services,
            budget_exhausted=False,
            evidence_source_unavailable=evidence_source_unavailable,
            conflicting_evidence=False,
        )
        if result.admitted_diagnosis is not None:
            admitted.append(result.admitted_diagnosis)

    target = view.candidate_services[0]
    no_incident_definition = HypothesisDefinitionV22.build(
        hypothesis_id="h:none:no-incident",
        target_service=target,
        root_service=target,
        fault_domain=next(
            item.fault_domain
            for item in view.active_hypotheses
            if item.hypothesis_id == NO_INCIDENT_HYPOTHESIS_ID_V22
        ),
        mechanism=MechanismV22.NO_INCIDENT,
        root_entity_ref=f"service:{target}",
    )
    no_incident_proposal = RawSemanticDiagnosisProposalV22.build(
        hypothesis_id=no_incident_definition.hypothesis_id,
        supporting_evidence_refs=(),
        contradicting_evidence_refs=(),
    )
    no_incident = admit_diagnosis_v22(
        proposal=no_incident_proposal,
        hypotheses=(no_incident_definition,),
        memory=memory,
        policy=policy,
        candidate_services=view.candidate_services,
        budget_exhausted=False,
        evidence_source_unavailable=evidence_source_unavailable,
        conflicting_evidence=len(admitted) > 1,
    )
    ordered = tuple(sorted(admitted, key=lambda item: item.diagnosis_sha256))
    payload: dict[str, Any] = {
        "schema_version": "dta-v23.known-admission-state.v1",
        "admitted_diagnoses": ordered,
        "no_incident_admission": no_incident,
        "conflicting_evidence": len(ordered) > 1,
        "memory_sha256": memory.memory_sha256,
        "support_policy_sha256": policy.policy_sha256,
    }
    draft = KnownAdmissionStateV23.model_construct(**payload, state_sha256="0" * 64)
    return KnownAdmissionStateV23.model_validate(
        {
            **payload,
            "state_sha256": semantic_sha256_v22(
                draft.model_dump(mode="json", exclude={"state_sha256"})
            ),
        }
    )


__all__ = ("KnownAdmissionStateV23", "build_known_admission_state_v23")
