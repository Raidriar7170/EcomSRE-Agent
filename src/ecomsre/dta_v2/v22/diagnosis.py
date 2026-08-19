"""Closed semantic Diagnosis admission and predicate-aware candidate filtering."""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import Field, model_validator

from ecomsre.dta_v2.v22.memory import SalientEvidenceMemoryV22
from ecomsre.dta_v2.v22.predicates import (
    EvidenceSupportPolicyV22,
    MechanismV22,
    evaluate_no_incident_v22,
    evaluate_support_v22,
)
from ecomsre.dta_v2.v22.read_contracts import (
    DtaModelV22,
    Sha256V22,
    semantic_sha256_v22,
)


class FaultDomainV22(str, Enum):
    CONFIGURATION = "CONFIGURATION"
    RUNTIME = "RUNTIME"
    RESOURCE = "RESOURCE"
    DEPENDENCY = "DEPENDENCY"
    NO_INCIDENT = "NO_INCIDENT"
    UNKNOWN = "UNKNOWN"


class DiagnosisTerminalV22(str, Enum):
    DIAGNOSED = "DIAGNOSED"
    NO_INCIDENT = "NO_INCIDENT"
    ABSTAIN = "ABSTAIN"
    FAILED = "FAILED"


class HypothesisDefinitionV22(DtaModelV22):
    schema_version: Literal["dta-v22.hypothesis-definition.v1"]
    hypothesis_id: str = Field(pattern=r"^h:[a-z0-9-]+:[a-z0-9-]+$")
    target_service: str = Field(pattern=r"^[a-z][a-z0-9-]*$")
    parent_service: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9-]*$")
    root_service: str = Field(pattern=r"^[a-z][a-z0-9-]*$")
    fault_domain: FaultDomainV22
    mechanism: MechanismV22
    root_entity_ref: str = Field(min_length=1, max_length=160)
    hypothesis_sha256: Sha256V22

    @classmethod
    def build(
        cls,
        *,
        hypothesis_id: str,
        target_service: str,
        root_service: str,
        fault_domain: FaultDomainV22,
        mechanism: MechanismV22,
        root_entity_ref: str,
        parent_service: str | None = None,
    ) -> HypothesisDefinitionV22:
        payload: dict[str, Any] = {
            "schema_version": "dta-v22.hypothesis-definition.v1",
            "hypothesis_id": hypothesis_id,
            "target_service": target_service,
            "parent_service": parent_service,
            "root_service": root_service,
            "fault_domain": fault_domain,
            "mechanism": mechanism,
            "root_entity_ref": root_entity_ref,
        }
        draft = cls.model_construct(**payload, hypothesis_sha256="0" * 64)
        return cls.model_validate(
            {
                **payload,
                "hypothesis_sha256": semantic_sha256_v22(
                    draft.model_dump(mode="json", exclude={"hypothesis_sha256"})
                ),
            }
        )

    @model_validator(mode="after")
    def require_hypothesis(self) -> HypothesisDefinitionV22:
        domain_by_mechanism = {
            MechanismV22.CONFIGURATION_ERROR: FaultDomainV22.CONFIGURATION,
            MechanismV22.SERVICE_UNAVAILABLE: FaultDomainV22.RUNTIME,
            MechanismV22.CPU_SATURATION: FaultDomainV22.RESOURCE,
            MechanismV22.MEMORY_LEAK: FaultDomainV22.RESOURCE,
            MechanismV22.DEPENDENCY_LATENCY: FaultDomainV22.DEPENDENCY,
            MechanismV22.NO_INCIDENT: FaultDomainV22.NO_INCIDENT,
            MechanismV22.UNKNOWN: FaultDomainV22.UNKNOWN,
        }
        if self.fault_domain is not domain_by_mechanism[self.mechanism]:
            raise ValueError("hypothesis mechanism and fault domain differ")
        if (
            self.mechanism is MechanismV22.DEPENDENCY_LATENCY
        ) != (self.parent_service is not None):
            raise ValueError("dependency hypothesis parent binding differs")
        if self.root_service != self.target_service:
            raise ValueError("hypothesis root service differs from exact target")
        if self.root_entity_ref != f"service:{self.root_service}":
            raise ValueError("hypothesis root entity differs from root service")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"hypothesis_sha256"})
        )
        if self.hypothesis_sha256 != expected:
            raise ValueError("hypothesis digest differs")
        return self


class RawSemanticDiagnosisProposalV22(DtaModelV22):
    schema_version: Literal["dta-v22.raw-semantic-diagnosis-proposal.v1"]
    hypothesis_id: str
    supporting_evidence_refs: tuple[str, ...]
    contradicting_evidence_refs: tuple[str, ...]
    proposal_sha256: Sha256V22

    @classmethod
    def build(
        cls,
        *,
        hypothesis_id: str,
        supporting_evidence_refs: tuple[str, ...],
        contradicting_evidence_refs: tuple[str, ...],
    ) -> RawSemanticDiagnosisProposalV22:
        payload: dict[str, Any] = {
            "schema_version": "dta-v22.raw-semantic-diagnosis-proposal.v1",
            "hypothesis_id": hypothesis_id,
            "supporting_evidence_refs": tuple(sorted(set(supporting_evidence_refs))),
            "contradicting_evidence_refs": tuple(
                sorted(set(contradicting_evidence_refs))
            ),
        }
        draft = cls.model_construct(**payload, proposal_sha256="0" * 64)
        return cls.model_validate(
            {
                **payload,
                "proposal_sha256": semantic_sha256_v22(
                    draft.model_dump(mode="json", exclude={"proposal_sha256"})
                ),
            }
        )

    @model_validator(mode="after")
    def require_proposal(self) -> RawSemanticDiagnosisProposalV22:
        for values in (
            self.supporting_evidence_refs,
            self.contradicting_evidence_refs,
        ):
            if values != tuple(sorted(set(values))):
                raise ValueError("diagnosis proposal refs are not canonical")
        if set(self.supporting_evidence_refs).intersection(
            self.contradicting_evidence_refs
        ):
            raise ValueError("diagnosis proposal support and contradiction overlap")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"proposal_sha256"})
        )
        if self.proposal_sha256 != expected:
            raise ValueError("diagnosis proposal digest differs")
        return self


class AdmittedDiagnosisV22(DtaModelV22):
    schema_version: Literal["dta-v22.admitted-diagnosis.v1"]
    hypothesis_id: str
    root_service: str
    target_service: str
    parent_service: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9-]*$")
    fault_domain: FaultDomainV22
    mechanism: MechanismV22
    root_entity_ref: str
    matched_clause_id: str
    supporting_evidence_refs: tuple[str, ...]
    memory_sha256: Sha256V22
    policy_sha256: Sha256V22
    support_decision_sha256: Sha256V22
    diagnosis_sha256: Sha256V22

    @model_validator(mode="after")
    def require_diagnosis(self) -> AdmittedDiagnosisV22:
        if self.mechanism in {MechanismV22.UNKNOWN, MechanismV22.NO_INCIDENT}:
            raise ValueError("UNKNOWN or No-Incident cannot be an incident diagnosis")
        if (
            self.mechanism is MechanismV22.DEPENDENCY_LATENCY
        ) != (self.parent_service is not None):
            raise ValueError("admitted dependency parent binding differs")
        if self.root_service != self.target_service:
            raise ValueError("admitted root service differs from exact target")
        if self.root_entity_ref != f"service:{self.root_service}":
            raise ValueError("admitted root entity differs from root service")
        if self.supporting_evidence_refs != tuple(
            sorted(set(self.supporting_evidence_refs))
        ):
            raise ValueError("admitted diagnosis refs are not canonical")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"diagnosis_sha256"})
        )
        if self.diagnosis_sha256 != expected:
            raise ValueError("admitted diagnosis digest differs")
        return self


class DiagnosisAdmissionResultV22(DtaModelV22):
    schema_version: Literal["dta-v22.diagnosis-admission-result.v1"]
    raw_proposal: RawSemanticDiagnosisProposalV22
    terminal: DiagnosisTerminalV22
    admitted_diagnosis: AdmittedDiagnosisV22 | None
    result_code: str = Field(pattern=r"^[A-Z][A-Z0-9_]*$")
    result_sha256: Sha256V22

    @model_validator(mode="after")
    def require_result(self) -> DiagnosisAdmissionResultV22:
        if (self.terminal is DiagnosisTerminalV22.DIAGNOSED) != (
            self.admitted_diagnosis is not None
        ):
            raise ValueError("diagnosis terminal and admitted value differ")
        if self.terminal is DiagnosisTerminalV22.DIAGNOSED:
            if self.result_code != "DIAGNOSIS_ADMITTED":
                raise ValueError("admitted diagnosis result code differs")
        elif self.result_code == "DIAGNOSIS_ADMITTED":
            raise ValueError("non-admitted diagnosis uses admitted result code")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"result_sha256"})
        )
        if self.result_sha256 != expected:
            raise ValueError("diagnosis admission result digest differs")
        return self


class CandidateActionV22(DtaModelV22):
    action_candidate_id: str = Field(pattern=r"^candidate:[a-z0-9-]+:[a-z0-9-]+$")
    target_service: str = Field(pattern=r"^[a-z][a-z0-9-]*$")
    fault_domain: FaultDomainV22
    mechanism: MechanismV22
    runbook_id: str = Field(pattern=r"^runbook:[a-z0-9-]+$")
    source_runbook_sha256: Sha256V22
    backend_mode: Literal["REPLAY_ONLY"]


_V21_RUNBOOK_REGISTRY_SHA256 = (
    "02bbcddba67da53c10324624dc770c9f73056e0126469567c8e70a79710047e9"
)
_TRUSTED_CANDIDATE_SPECS_V22: tuple[
    tuple[str, str, FaultDomainV22, MechanismV22, str, str], ...
] = (
    (
        "candidate:cpu-saturation:ad",
        "ad",
        FaultDomainV22.RESOURCE,
        MechanismV22.CPU_SATURATION,
        "runbook:mitigate-cpu-saturation",
        "b779c6ed51a867702c54ea653429aeb993e8794a2ad987ac4370e089c36dc04c",
    ),
    (
        "candidate:memory-leak:email",
        "email",
        FaultDomainV22.RESOURCE,
        MechanismV22.MEMORY_LEAK,
        "runbook:mitigate-memory-leak",
        "52e77d58b8b331e36f51d74763031b374d5bb25b828a3763378120c7e07ec030",
    ),
    (
        "candidate:service-unavailable:recommendation",
        "recommendation",
        FaultDomainV22.RUNTIME,
        MechanismV22.SERVICE_UNAVAILABLE,
        "runbook:restart-service",
        "96946b9dedbc14983f5d12260811876e4807616e36775f8bf156c545cb61e3e7",
    ),
    (
        "candidate:dependency-latency:shipping",
        "shipping",
        FaultDomainV22.DEPENDENCY,
        MechanismV22.DEPENDENCY_LATENCY,
        "runbook:restore-dependency-latency",
        "1480ed6c94ee8ee912b359f554fdc46e39cd642e8401a16b15f96eac2a00d515",
    ),
    (
        "candidate:service-unavailable:email",
        "email",
        FaultDomainV22.RUNTIME,
        MechanismV22.SERVICE_UNAVAILABLE,
        "runbook:restore-service-availability",
        "b6155b97560a4fd4bb45557cbebd941f95fe4cf34c2bc75f06baaf6cc0a4d193",
    ),
    (
        "candidate:service-unavailable:product-catalog",
        "product-catalog",
        FaultDomainV22.RUNTIME,
        MechanismV22.SERVICE_UNAVAILABLE,
        "runbook:restore-service-availability",
        "b6155b97560a4fd4bb45557cbebd941f95fe4cf34c2bc75f06baaf6cc0a4d193",
    ),
    (
        "candidate:config:payment",
        "payment",
        FaultDomainV22.CONFIGURATION,
        MechanismV22.CONFIGURATION_ERROR,
        "runbook:rollback-configuration",
        "39b7928ecbdbe3acf23f676358cb2697268e9aa21b63dc9b28545770add84955",
    ),
)


def _trusted_candidates_v22() -> tuple[CandidateActionV22, ...]:
    return tuple(
        sorted(
            (
                CandidateActionV22(
                    action_candidate_id=action_candidate_id,
                    target_service=target_service,
                    fault_domain=fault_domain,
                    mechanism=mechanism,
                    runbook_id=runbook_id,
                    source_runbook_sha256=source_runbook_sha256,
                    backend_mode="REPLAY_ONLY",
                )
                for (
                    action_candidate_id,
                    target_service,
                    fault_domain,
                    mechanism,
                    runbook_id,
                    source_runbook_sha256,
                ) in _TRUSTED_CANDIDATE_SPECS_V22
            ),
            key=lambda item: item.action_candidate_id,
        )
    )


class TrustedCandidateRegistryV22(DtaModelV22):
    schema_version: Literal["dta-v22.trusted-candidate-registry.v1"]
    source_registry_sha256: Sha256V22
    candidates: tuple[CandidateActionV22, ...]
    registry_sha256: Sha256V22

    @classmethod
    def build(
        cls,
    ) -> TrustedCandidateRegistryV22:
        payload: dict[str, Any] = {
            "schema_version": "dta-v22.trusted-candidate-registry.v1",
            "source_registry_sha256": _V21_RUNBOOK_REGISTRY_SHA256,
            "candidates": _trusted_candidates_v22(),
        }
        draft = cls.model_construct(**payload, registry_sha256="0" * 64)
        return cls.model_validate(
            {
                **payload,
                "registry_sha256": semantic_sha256_v22(
                    draft.model_dump(mode="json", exclude={"registry_sha256"})
                ),
            }
        )

    @model_validator(mode="after")
    def require_registry(self) -> TrustedCandidateRegistryV22:
        ids = tuple(item.action_candidate_id for item in self.candidates)
        if ids != tuple(sorted(set(ids))):
            raise ValueError("candidate registry is not canonical and unique")
        if (
            self.source_registry_sha256 != _V21_RUNBOOK_REGISTRY_SHA256
            or self.candidates != _trusted_candidates_v22()
        ):
            raise ValueError("candidate registry differs from trusted v2.1 authority")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"registry_sha256"})
        )
        if self.registry_sha256 != expected:
            raise ValueError("candidate registry digest differs")
        return self


class CandidateSetV22(DtaModelV22):
    schema_version: Literal["dta-v22.candidate-set.v1"]
    diagnosis_sha256: Sha256V22
    registry_sha256: Sha256V22
    candidates: tuple[CandidateActionV22, ...]
    candidate_set_sha256: Sha256V22

    @model_validator(mode="after")
    def require_set(self) -> CandidateSetV22:
        ids = tuple(item.action_candidate_id for item in self.candidates)
        if ids != tuple(sorted(set(ids))):
            raise ValueError("candidate set is not canonical and unique")
        if any(item.backend_mode != "REPLAY_ONLY" for item in self.candidates):
            raise ValueError("candidate set contains a live action")
        trusted = set(_trusted_candidates_v22())
        if any(item not in trusted for item in self.candidates):
            raise ValueError("candidate set contains an action outside trusted authority")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"candidate_set_sha256"})
        )
        if self.candidate_set_sha256 != expected:
            raise ValueError("candidate set digest differs")
        return self


def _result(
    *,
    proposal: RawSemanticDiagnosisProposalV22,
    terminal: DiagnosisTerminalV22,
    admitted: AdmittedDiagnosisV22 | None,
    result_code: str,
) -> DiagnosisAdmissionResultV22:
    payload: dict[str, Any] = {
        "schema_version": "dta-v22.diagnosis-admission-result.v1",
        "raw_proposal": proposal,
        "terminal": terminal,
        "admitted_diagnosis": admitted,
        "result_code": result_code,
    }
    draft = DiagnosisAdmissionResultV22.model_construct(
        **payload,
        result_sha256="0" * 64,
    )
    return DiagnosisAdmissionResultV22.model_validate(
        {
            **payload,
            "result_sha256": semantic_sha256_v22(
                draft.model_dump(mode="json", exclude={"result_sha256"})
            ),
        }
    )


def admit_diagnosis_v22(
    *,
    proposal: RawSemanticDiagnosisProposalV22,
    hypotheses: tuple[HypothesisDefinitionV22, ...],
    memory: SalientEvidenceMemoryV22,
    policy: EvidenceSupportPolicyV22,
    candidate_services: tuple[str, ...],
    budget_exhausted: bool,
    evidence_source_unavailable: bool,
    conflicting_evidence: bool,
) -> DiagnosisAdmissionResultV22:
    """Admit evidence for a model-selected closed hypothesis; never select one."""

    if not 1 <= len(candidate_services) <= 4 or candidate_services != tuple(
        sorted(set(candidate_services))
    ):
        raise ValueError("candidate services must be canonical with cardinality one to four")

    by_id = {item.hypothesis_id: item for item in hypotheses}
    if len(by_id) != len(hypotheses):
        raise ValueError("hypothesis catalog contains duplicate IDs")
    known_refs = {item.evidence_ref for item in memory.evidence_refs}
    proposal_refs = set(proposal.supporting_evidence_refs) | set(
        proposal.contradicting_evidence_refs
    )
    if not proposal_refs.issubset(known_refs):
        return _result(
            proposal=proposal,
            terminal=DiagnosisTerminalV22.FAILED,
            admitted=None,
            result_code="UNRESOLVED_EVIDENCE_REF",
        )
    hypothesis = by_id.get(proposal.hypothesis_id)
    if hypothesis is None or hypothesis.target_service not in set(candidate_services):
        return _result(
            proposal=proposal,
            terminal=DiagnosisTerminalV22.FAILED,
            admitted=None,
            result_code="UNKNOWN_OR_OUT_OF_SCOPE_HYPOTHESIS",
        )
    if proposal.contradicting_evidence_refs:
        return _result(
            proposal=proposal,
            terminal=(
                DiagnosisTerminalV22.ABSTAIN
                if conflicting_evidence
                else DiagnosisTerminalV22.FAILED
            ),
            admitted=None,
            result_code="CONTRADICTING_EVIDENCE_PRESENT",
        )
    if hypothesis.mechanism is MechanismV22.UNKNOWN:
        return _result(
            proposal=proposal,
            terminal=(
                DiagnosisTerminalV22.ABSTAIN
                if budget_exhausted
                or evidence_source_unavailable
                or conflicting_evidence
                else DiagnosisTerminalV22.FAILED
            ),
            admitted=None,
            result_code="UNKNOWN_CANNOT_BE_DIAGNOSED",
        )
    if hypothesis.mechanism is MechanismV22.NO_INCIDENT:
        no_incident = evaluate_no_incident_v22(
            memory=memory,
            candidate_services=tuple(sorted(set(candidate_services))),
        )
        if no_incident.accepted:
            return _result(
                proposal=proposal,
                terminal=DiagnosisTerminalV22.NO_INCIDENT,
                admitted=None,
                result_code="NO_INCIDENT_ADMITTED",
            )
        return _result(
            proposal=proposal,
            terminal=(
                DiagnosisTerminalV22.ABSTAIN
                if budget_exhausted
                or evidence_source_unavailable
                or conflicting_evidence
                else DiagnosisTerminalV22.FAILED
            ),
            admitted=None,
            result_code="NO_INCIDENT_COVERAGE_DENIED",
        )
    support = evaluate_support_v22(
        policy=policy,
        mechanism=hypothesis.mechanism,
        target_service=hypothesis.target_service,
        parent_service=hypothesis.parent_service,
        predicates=memory.predicates,
    )
    if not support.accepted:
        return _result(
            proposal=proposal,
            terminal=(
                DiagnosisTerminalV22.ABSTAIN
                if budget_exhausted
                or evidence_source_unavailable
                or conflicting_evidence
                else DiagnosisTerminalV22.FAILED
            ),
            admitted=None,
            result_code="NO_SUPPORT_CLAUSE_SATISFIED",
        )
    proposed = set(proposal.supporting_evidence_refs)
    required = set(support.supporting_evidence_refs)
    if proposed - required:
        return _result(
            proposal=proposal,
            terminal=DiagnosisTerminalV22.ABSTAIN,
            admitted=None,
            result_code="IRRELEVANT_SUPPORTING_REF",
        )
    if required - proposed:
        return _result(
            proposal=proposal,
            terminal=DiagnosisTerminalV22.ABSTAIN,
            admitted=None,
            result_code="SUPPORTING_REFS_INCOMPLETE",
        )
    diagnosis_payload: dict[str, Any] = {
        "schema_version": "dta-v22.admitted-diagnosis.v1",
        "hypothesis_id": hypothesis.hypothesis_id,
        "root_service": hypothesis.root_service,
        "target_service": hypothesis.target_service,
        "parent_service": hypothesis.parent_service,
        "fault_domain": hypothesis.fault_domain,
        "mechanism": hypothesis.mechanism,
        "root_entity_ref": hypothesis.root_entity_ref,
        "matched_clause_id": support.matched_clause_id,
        "supporting_evidence_refs": support.supporting_evidence_refs,
        "memory_sha256": memory.memory_sha256,
        "policy_sha256": policy.policy_sha256,
        "support_decision_sha256": support.decision_sha256,
    }
    draft = AdmittedDiagnosisV22.model_construct(
        **diagnosis_payload,
        diagnosis_sha256="0" * 64,
    )
    admitted = AdmittedDiagnosisV22.model_validate(
        {
            **diagnosis_payload,
            "diagnosis_sha256": semantic_sha256_v22(
                draft.model_dump(mode="json", exclude={"diagnosis_sha256"})
            ),
        }
    )
    return _result(
        proposal=proposal,
        terminal=DiagnosisTerminalV22.DIAGNOSED,
        admitted=admitted,
        result_code="DIAGNOSIS_ADMITTED",
    )


def filter_candidates_v22(
    *,
    admission: DiagnosisAdmissionResultV22,
    registry: TrustedCandidateRegistryV22,
    memory: SalientEvidenceMemoryV22,
    policy: EvidenceSupportPolicyV22,
) -> CandidateSetV22:
    if (
        admission.terminal is not DiagnosisTerminalV22.DIAGNOSED
        or admission.admitted_diagnosis is None
    ):
        raise ValueError("candidate filtering requires an admitted diagnosis")
    diagnosis = admission.admitted_diagnosis
    if (
        diagnosis.memory_sha256 != memory.memory_sha256
        or diagnosis.policy_sha256 != policy.policy_sha256
    ):
        raise ValueError("candidate filter memory or policy binding differs")
    support = evaluate_support_v22(
        policy=policy,
        mechanism=diagnosis.mechanism,
        target_service=diagnosis.target_service,
        parent_service=diagnosis.parent_service,
        predicates=memory.predicates,
    )
    if (
        support.decision_sha256 != diagnosis.support_decision_sha256
        or support.matched_clause_id != diagnosis.matched_clause_id
        or support.supporting_evidence_refs != diagnosis.supporting_evidence_refs
    ):
        raise ValueError("candidate filter support binding differs")
    candidates = tuple(
        item
        for item in registry.candidates
        if item.backend_mode == "REPLAY_ONLY"
        and item.target_service == diagnosis.target_service
        and item.fault_domain is diagnosis.fault_domain
        and item.mechanism is diagnosis.mechanism
    )
    payload: dict[str, Any] = {
        "schema_version": "dta-v22.candidate-set.v1",
        "diagnosis_sha256": diagnosis.diagnosis_sha256,
        "registry_sha256": registry.registry_sha256,
        "candidates": candidates,
    }
    draft = CandidateSetV22.model_construct(
        **payload,
        candidate_set_sha256="0" * 64,
    )
    return CandidateSetV22.model_validate(
        {
            **payload,
            "candidate_set_sha256": semantic_sha256_v22(
                draft.model_dump(mode="json", exclude={"candidate_set_sha256"})
            ),
        }
    )


__all__ = (
    "CandidateActionV22",
    "CandidateSetV22",
    "DiagnosisAdmissionResultV22",
    "DiagnosisTerminalV22",
    "FaultDomainV22",
    "HypothesisDefinitionV22",
    "MechanismV22",
    "RawSemanticDiagnosisProposalV22",
    "TrustedCandidateRegistryV22",
    "admit_diagnosis_v22",
    "filter_candidates_v22",
)
