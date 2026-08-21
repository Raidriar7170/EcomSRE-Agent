"""One effective v2.2.2 support policy shared by routing and admission."""

from __future__ import annotations

from typing import Literal

from pydantic import StrictBool, model_validator

from ecomsre.dta_v2.v22.memory import (
    EvidencePredicateV22,
    MetricSalientPayloadV22,
    PredicateKindV22,
    SalientEvidenceMemoryV22,
)
from ecomsre.dta_v2.v22.predicates import (
    MechanismV22,
    PredicateRequirementV22,
    RequirementServiceBindingV22,
    SupportClauseV22,
    build_default_evidence_support_policy_v22,
)
from ecomsre.dta_v2.v22.read_contracts import (
    DtaModelV22,
    MetricKindV22,
    MetricSupportStatusV22,
    semantic_sha256_v22,
)


class EffectiveSupportPolicyV222(DtaModelV22):
    schema_version: Literal["dta-v22.2.effective-support-policy.v1"]
    frozen_policy_sha256: str
    clauses: tuple[SupportClauseV22, ...]
    policy_sha256: str

    @model_validator(mode="after")
    def require_policy(self) -> "EffectiveSupportPolicyV222":
        frozen = build_default_evidence_support_policy_v22()
        if self.frozen_policy_sha256 != frozen.policy_sha256:
            raise ValueError("effective policy does not bind frozen policy")
        frozen_ids = {item.clause_id for item in frozen.clauses}
        actual_ids = tuple(item.clause_id for item in self.clauses)
        if actual_ids != tuple(sorted(set(actual_ids))):
            raise ValueError("effective policy clauses are not canonical")
        if not frozen_ids.issubset(actual_ids):
            raise ValueError("effective policy omits a frozen clause")
        if set(actual_ids) - frozen_ids != {
            "configuration:error-metric-and-first-error-trace",
            "memory-leak:growth-and-healthy",
        }:
            raise ValueError("effective policy practical clauses differ")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"policy_sha256"})
        )
        if self.policy_sha256 != expected:
            raise ValueError("effective policy digest differs")
        return self


class EffectiveSupportDecisionV222(DtaModelV22):
    schema_version: Literal["dta-v22.2.effective-support-decision.v1"]
    mechanism: MechanismV22
    target_service: str
    parent_service: str | None
    accepted: StrictBool
    matched_clause_id: str | None
    supporting_predicate_ids: tuple[str, ...]
    supporting_evidence_refs: tuple[str, ...]
    decision_sha256: str

    @model_validator(mode="after")
    def require_decision(self) -> "EffectiveSupportDecisionV222":
        if self.accepted != (self.matched_clause_id is not None):
            raise ValueError("effective support acceptance differs from clause")
        for values in (self.supporting_predicate_ids, self.supporting_evidence_refs):
            if values != tuple(sorted(set(values))):
                raise ValueError("effective support bindings are not canonical")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"decision_sha256"})
        )
        if self.decision_sha256 != expected:
            raise ValueError("effective support decision digest differs")
        return self


def _requirement(
    kind: PredicateKindV22,
) -> PredicateRequirementV22:
    return PredicateRequirementV22(
        predicate_kind=kind,
        service_binding=RequirementServiceBindingV22.TARGET,
        require_exact_parent=False,
    )


def build_effective_support_policy_v222() -> EffectiveSupportPolicyV222:
    frozen = build_default_evidence_support_policy_v22()
    clauses = tuple(
        sorted(
            (
                *frozen.clauses,
                SupportClauseV22(
                    clause_id="configuration:error-metric-and-first-error-trace",
                    mechanism=MechanismV22.CONFIGURATION_ERROR,
                    requirements=(
                        _requirement(PredicateKindV22.METRIC_ERROR_RATE_STRONG),
                        _requirement(PredicateKindV22.TRACE_FIRST_ERROR),
                    ),
                ),
                SupportClauseV22(
                    clause_id="memory-leak:growth-and-healthy",
                    mechanism=MechanismV22.MEMORY_LEAK,
                    requirements=(
                        _requirement(PredicateKindV22.RESOURCE_MEMORY_GROWTH_STRONG),
                        _requirement(PredicateKindV22.RUNTIME_HEALTHY),
                    ),
                ),
            ),
            key=lambda item: item.clause_id,
        )
    )
    digest_payload = {
        "schema_version": "dta-v22.2.effective-support-policy.v1",
        "frozen_policy_sha256": frozen.policy_sha256,
        "clauses": tuple(item.model_dump(mode="json") for item in clauses),
    }
    return EffectiveSupportPolicyV222(
        schema_version="dta-v22.2.effective-support-policy.v1",
        frozen_policy_sha256=frozen.policy_sha256,
        clauses=clauses,
        policy_sha256=semantic_sha256_v22(digest_payload),
    )


def predicate_matches_requirement_v222(
    *,
    predicate: EvidencePredicateV22,
    requirement: PredicateRequirementV22,
    target_service: str,
    parent_service: str | None,
) -> bool:
    if predicate.predicate_kind is not requirement.predicate_kind:
        return False
    allowed = {target_service}
    if requirement.service_binding is RequirementServiceBindingV22.TARGET_OR_PARENT:
        if parent_service is not None:
            allowed.add(parent_service)
    if predicate.service not in allowed:
        return False
    return not requirement.require_exact_parent or predicate.parent_service == parent_service


def evaluate_effective_support_v222(
    *,
    policy: EffectiveSupportPolicyV222,
    mechanism: MechanismV22,
    target_service: str,
    parent_service: str | None,
    predicates: tuple[EvidencePredicateV22, ...],
) -> EffectiveSupportDecisionV222:
    policy = EffectiveSupportPolicyV222.model_validate(policy.model_dump(mode="python"))
    canonical = tuple(sorted(predicates, key=lambda item: item.predicate_id))
    matched_clause: str | None = None
    matched_predicates: tuple[EvidencePredicateV22, ...] = ()
    for clause in policy.clauses:
        if clause.mechanism is not mechanism:
            continue
        selected: list[EvidencePredicateV22] = []
        for requirement in clause.requirements:
            candidate = next(
                (
                    item
                    for item in canonical
                    if predicate_matches_requirement_v222(
                        predicate=item,
                        requirement=requirement,
                        target_service=target_service,
                        parent_service=parent_service,
                    )
                ),
                None,
            )
            if candidate is None:
                break
            selected.append(candidate)
        else:
            matched_clause = clause.clause_id
            matched_predicates = tuple(selected)
            break
    supporting_predicate_ids = tuple(
        sorted(item.predicate_id for item in matched_predicates)
    )
    supporting_evidence_refs = tuple(
        sorted(
            {
                ref
                for item in matched_predicates
                for ref in item.evidence_refs
            }
        )
    )
    digest_payload = {
        "schema_version": "dta-v22.2.effective-support-decision.v1",
        "mechanism": mechanism.value,
        "target_service": target_service,
        "parent_service": parent_service,
        "accepted": matched_clause is not None,
        "matched_clause_id": matched_clause,
        "supporting_predicate_ids": supporting_predicate_ids,
        "supporting_evidence_refs": supporting_evidence_refs,
    }
    return EffectiveSupportDecisionV222(
        schema_version="dta-v22.2.effective-support-decision.v1",
        mechanism=mechanism,
        target_service=target_service,
        parent_service=parent_service,
        accepted=matched_clause is not None,
        matched_clause_id=matched_clause,
        supporting_predicate_ids=supporting_predicate_ids,
        supporting_evidence_refs=supporting_evidence_refs,
        decision_sha256=semantic_sha256_v22(digest_payload),
    )


def evaluate_replay_no_incident_coverage_v222(
    *, memory: SalientEvidenceMemoryV22, candidate_services: tuple[str, ...]
) -> bool:
    """Accept only complete healthy runtime and supported request/error coverage."""

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
    anomaly_kinds = set(PredicateKindV22) - {
        PredicateKindV22.RUNTIME_HEALTHY,
        PredicateKindV22.CHANGE_RECENT_ROLLOUT,
    }
    return (
        bool(candidate_services)
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


__all__ = (
    "EffectiveSupportDecisionV222",
    "EffectiveSupportPolicyV222",
    "build_effective_support_policy_v222",
    "evaluate_effective_support_v222",
    "evaluate_replay_no_incident_coverage_v222",
    "predicate_matches_requirement_v222",
)
