"""Narrow legacy-replay admission clauses for the practical v2.2 successor.

The PR-C support policy remains frozen.  This module first evaluates that
policy unchanged, then permits only the declared legacy evidence shapes present
in the public v2/v2.1 replay captures.  It never fabricates a change, log,
restart, or truth label.
"""

from __future__ import annotations

from typing import Any

from ecomsre.dta_v2.v22.diagnosis import (
    AdmittedDiagnosisV22,
    DiagnosisAdmissionResultV22,
    DiagnosisTerminalV22,
    HypothesisDefinitionV22,
    RawSemanticDiagnosisProposalV22,
    admit_diagnosis_v22,
)
from ecomsre.dta_v2.v22.memory import (
    EvidencePredicateV22,
    MetricSalientPayloadV22,
    PredicateKindV22,
    SalientEvidenceMemoryV22,
)
from ecomsre.dta_v2.v22.predicates import EvidenceSupportPolicyV22, MechanismV22
from ecomsre.dta_v2.v22.read_contracts import (
    MetricKindV22,
    MetricSupportStatusV22,
    semantic_sha256_v22,
)


_PRACTICAL_CLAUSES_V22: dict[
    MechanismV22, tuple[str, tuple[PredicateKindV22, ...]]
] = {
    MechanismV22.CONFIGURATION_ERROR: (
        "configuration:error-metric-and-first-error-trace",
        (
            PredicateKindV22.METRIC_ERROR_RATE_STRONG,
            PredicateKindV22.TRACE_FIRST_ERROR,
        ),
    ),
    MechanismV22.MEMORY_LEAK: (
        "memory-leak:growth-and-healthy",
        (
            PredicateKindV22.RESOURCE_MEMORY_GROWTH_STRONG,
            PredicateKindV22.RUNTIME_HEALTHY,
        ),
    ),
}
_PRACTICAL_POLICY_PAYLOAD_V22 = {
    "schema_version": "dta-v22.practical-legacy-replay-clauses.v1",
    "clauses": {
        mechanism.value: {
            "clause_id": clause_id,
            "predicate_kinds": tuple(kind.value for kind in kinds),
        }
        for mechanism, (clause_id, kinds) in _PRACTICAL_CLAUSES_V22.items()
    },
}
PRACTICAL_POLICY_SHA256_V22 = semantic_sha256_v22(_PRACTICAL_POLICY_PAYLOAD_V22)


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


def _matching_predicate(
    *,
    predicates: tuple[EvidencePredicateV22, ...],
    kind: PredicateKindV22,
    hypothesis: HypothesisDefinitionV22,
) -> EvidencePredicateV22 | None:
    candidates = tuple(
        item
        for item in predicates
        if item.predicate_kind is kind
        and item.service == hypothesis.target_service
    )
    if not candidates:
        return None
    return min(candidates, key=lambda item: item.predicate_id)


def _admit_practical_clause(
    *,
    proposal: RawSemanticDiagnosisProposalV22,
    hypothesis: HypothesisDefinitionV22,
    memory: SalientEvidenceMemoryV22,
) -> DiagnosisAdmissionResultV22 | None:
    clause = _PRACTICAL_CLAUSES_V22.get(hypothesis.mechanism)
    if clause is None or proposal.contradicting_evidence_refs:
        return None
    clause_id, required_kinds = clause
    selected = tuple(
        _matching_predicate(
            predicates=memory.predicates,
            kind=kind,
            hypothesis=hypothesis,
        )
        for kind in required_kinds
    )
    if any(item is None for item in selected):
        return None
    predicates = tuple(item for item in selected if item is not None)
    required_refs = tuple(
        sorted({ref for predicate in predicates for ref in predicate.evidence_refs})
    )
    if proposal.supporting_evidence_refs != required_refs:
        return None
    support_payload = {
        "schema_version": "dta-v22.practical-support-decision.v1",
        "clause_id": clause_id,
        "hypothesis_id": hypothesis.hypothesis_id,
        "supporting_evidence_refs": required_refs,
        "predicate_sha256s": tuple(item.predicate_sha256 for item in predicates),
    }
    diagnosis_payload: dict[str, Any] = {
        "schema_version": "dta-v22.admitted-diagnosis.v1",
        "hypothesis_id": hypothesis.hypothesis_id,
        "root_service": hypothesis.root_service,
        "target_service": hypothesis.target_service,
        "parent_service": hypothesis.parent_service,
        "fault_domain": hypothesis.fault_domain,
        "mechanism": hypothesis.mechanism,
        "root_entity_ref": hypothesis.root_entity_ref,
        "matched_clause_id": clause_id,
        "supporting_evidence_refs": required_refs,
        "memory_sha256": memory.memory_sha256,
        "policy_sha256": PRACTICAL_POLICY_SHA256_V22,
        "support_decision_sha256": semantic_sha256_v22(support_payload),
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


def admit_practical_diagnosis_v22(
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
    """Evaluate frozen PR-C admission, then declared legacy replay clauses."""

    frozen = admit_diagnosis_v22(
        proposal=proposal,
        hypotheses=hypotheses,
        memory=memory,
        policy=policy,
        candidate_services=candidate_services,
        budget_exhausted=budget_exhausted,
        evidence_source_unavailable=evidence_source_unavailable,
        conflicting_evidence=conflicting_evidence,
    )
    if frozen.result_code == "NO_INCIDENT_COVERAGE_DENIED":
        no_incident_hypothesis = next(
            (
                item
                for item in hypotheses
                if item.hypothesis_id == proposal.hypothesis_id
            ),
            None,
        )
        anomaly_kinds = set(PredicateKindV22) - {
            PredicateKindV22.RUNTIME_HEALTHY,
            PredicateKindV22.CHANGE_RECENT_ROLLOUT,
        }
        runtime_healthy = {
            item.service
            for item in memory.predicates
            if item.predicate_kind is PredicateKindV22.RUNTIME_HEALTHY
        }
        metric_coverage: dict[str, set[MetricKindV22]] = {
            service: set() for service in candidate_services
        }
        for fact in memory.salient_facts:
            if (
                fact.service in metric_coverage
                and isinstance(fact.payload, MetricSalientPayloadV22)
                and fact.payload.support_status is MetricSupportStatusV22.SUPPORTED
                and fact.payload.metric_kind
                in {MetricKindV22.ERROR_RATE, MetricKindV22.REQUEST_SUPPORT}
            ):
                metric_coverage[fact.service].add(fact.payload.metric_kind)
        bounded_legacy_coverage = (
            no_incident_hypothesis is not None
            and no_incident_hypothesis.mechanism is MechanismV22.NO_INCIDENT
            and not proposal.supporting_evidence_refs
            and set(candidate_services).issubset(runtime_healthy)
            and all(
                {MetricKindV22.ERROR_RATE, MetricKindV22.REQUEST_SUPPORT}.issubset(
                    metric_coverage[service]
                )
                for service in candidate_services
            )
            and not any(
                item.predicate_kind in anomaly_kinds for item in memory.predicates
            )
        )
        if bounded_legacy_coverage:
            return _result(
                proposal=proposal,
                terminal=DiagnosisTerminalV22.NO_INCIDENT,
                admitted=None,
                result_code="NO_INCIDENT_ADMITTED",
            )
    if frozen.result_code != "NO_SUPPORT_CLAUSE_SATISFIED":
        return frozen
    hypothesis = next(
        (item for item in hypotheses if item.hypothesis_id == proposal.hypothesis_id),
        None,
    )
    if hypothesis is None:
        return frozen
    practical = _admit_practical_clause(
        proposal=proposal,
        hypothesis=hypothesis,
        memory=memory,
    )
    return frozen if practical is None else practical


__all__ = (
    "PRACTICAL_POLICY_SHA256_V22",
    "admit_practical_diagnosis_v22",
)
