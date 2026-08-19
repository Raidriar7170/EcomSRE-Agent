"""Truth-independent evidence predicates and DNF admission policy for DTA v2.2."""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import Field, StrictBool, model_validator

from ecomsre.dta_v2.v22.memory import (
    ChangeSalientPayloadV22,
    EvidencePredicateV22,
    LogCategoryV22,
    LogSalientPayloadV22,
    MetricSalientPayloadV22,
    PredicateKindV22,
    PredicateThresholdsV22,
    ResourceSalientPayloadV22,
    RuntimeSalientPayloadV22,
    SalientEvidenceMemoryV22,
    SalientFactV22,
    SignalStrengthV22,
    TraceSalientPayloadV22,
)
from ecomsre.dta_v2.v22.read_contracts import (
    DtaModelV22,
    EvidenceSourceV22,
    MetricKindV22,
    MetricSupportStatusV22,
    RuntimeStateV22,
    Sha256V22,
    semantic_sha256_v22,
)


class MechanismV22(str, Enum):
    CONFIGURATION_ERROR = "CONFIGURATION_ERROR"
    SERVICE_UNAVAILABLE = "SERVICE_UNAVAILABLE"
    CPU_SATURATION = "CPU_SATURATION"
    MEMORY_LEAK = "MEMORY_LEAK"
    DEPENDENCY_LATENCY = "DEPENDENCY_LATENCY"
    NO_INCIDENT = "NO_INCIDENT"
    UNKNOWN = "UNKNOWN"


class RequirementServiceBindingV22(str, Enum):
    TARGET = "TARGET"
    TARGET_OR_PARENT = "TARGET_OR_PARENT"


_FROZEN_SUPPORT_POLICY_V22 = (
    (
        "configuration:change-and-error-metric",
        MechanismV22.CONFIGURATION_ERROR,
        (
            (PredicateKindV22.CHANGE_RECENT_ROLLOUT, RequirementServiceBindingV22.TARGET, False),
            (PredicateKindV22.METRIC_ERROR_RATE_STRONG, RequirementServiceBindingV22.TARGET, False),
        ),
    ),
    (
        "configuration:change-and-log",
        MechanismV22.CONFIGURATION_ERROR,
        (
            (PredicateKindV22.CHANGE_RECENT_ROLLOUT, RequirementServiceBindingV22.TARGET, False),
            (PredicateKindV22.LOG_CONFIGURATION_ERROR, RequirementServiceBindingV22.TARGET, False),
        ),
    ),
    (
        "cpu-saturation:resource-and-healthy",
        MechanismV22.CPU_SATURATION,
        (
            (PredicateKindV22.RESOURCE_CPU_STRONG, RequirementServiceBindingV22.TARGET, False),
            (PredicateKindV22.RUNTIME_HEALTHY, RequirementServiceBindingV22.TARGET, False),
        ),
    ),
    (
        "dependency-latency:trace-and-metric",
        MechanismV22.DEPENDENCY_LATENCY,
        (
            (PredicateKindV22.TRACE_DEPENDENCY_LATENCY, RequirementServiceBindingV22.TARGET, True),
            (PredicateKindV22.METRIC_LATENCY_STRONG, RequirementServiceBindingV22.TARGET_OR_PARENT, False),
        ),
    ),
    (
        "memory-leak:growth-and-log",
        MechanismV22.MEMORY_LEAK,
        (
            (PredicateKindV22.RESOURCE_MEMORY_GROWTH_STRONG, RequirementServiceBindingV22.TARGET, False),
            (PredicateKindV22.LOG_MEMORY_PRESSURE, RequirementServiceBindingV22.TARGET, False),
        ),
    ),
    (
        "memory-leak:growth-and-memory-metric",
        MechanismV22.MEMORY_LEAK,
        (
            (PredicateKindV22.RESOURCE_MEMORY_GROWTH_STRONG, RequirementServiceBindingV22.TARGET, False),
            (PredicateKindV22.METRIC_MEMORY_STRONG, RequirementServiceBindingV22.TARGET, False),
        ),
    ),
    (
        "memory-leak:growth-and-restarts",
        MechanismV22.MEMORY_LEAK,
        (
            (PredicateKindV22.RESOURCE_MEMORY_GROWTH_STRONG, RequirementServiceBindingV22.TARGET, False),
            (PredicateKindV22.RUNTIME_RESTART_PRESSURE, RequirementServiceBindingV22.TARGET, False),
        ),
    ),
    (
        "service-unavailable:not-running",
        MechanismV22.SERVICE_UNAVAILABLE,
        (
            (PredicateKindV22.RUNTIME_NOT_RUNNING, RequirementServiceBindingV22.TARGET, False),
        ),
    ),
    (
        "service-unavailable:unhealthy-error-metric",
        MechanismV22.SERVICE_UNAVAILABLE,
        (
            (PredicateKindV22.RUNTIME_UNHEALTHY, RequirementServiceBindingV22.TARGET, False),
            (PredicateKindV22.METRIC_ERROR_RATE_STRONG, RequirementServiceBindingV22.TARGET, False),
        ),
    ),
    (
        "service-unavailable:unhealthy-first-error",
        MechanismV22.SERVICE_UNAVAILABLE,
        (
            (PredicateKindV22.RUNTIME_UNHEALTHY, RequirementServiceBindingV22.TARGET, False),
            (PredicateKindV22.TRACE_FIRST_ERROR, RequirementServiceBindingV22.TARGET, False),
        ),
    ),
)


class PredicateRequirementV22(DtaModelV22):
    predicate_kind: PredicateKindV22
    service_binding: RequirementServiceBindingV22
    require_exact_parent: StrictBool


class SupportClauseV22(DtaModelV22):
    clause_id: str = Field(pattern=r"^[a-z-]+:[a-z-]+$")
    mechanism: MechanismV22
    requirements: tuple[PredicateRequirementV22, ...] = Field(min_length=1)


class EvidenceSupportPolicyV22(DtaModelV22):
    schema_version: Literal["dta-v22.evidence-support-policy.v1"]
    thresholds_sha256: Sha256V22
    clauses: tuple[SupportClauseV22, ...]
    policy_sha256: Sha256V22

    @model_validator(mode="after")
    def require_policy(self) -> EvidenceSupportPolicyV22:
        clause_ids = tuple(item.clause_id for item in self.clauses)
        if clause_ids != tuple(sorted(set(clause_ids))):
            raise ValueError("support clauses are not canonical and unique")
        if any(
            item.mechanism in {MechanismV22.NO_INCIDENT, MechanismV22.UNKNOWN}
            for item in self.clauses
        ):
            raise ValueError("No-Incident or UNKNOWN cannot use incident clauses")
        actual_policy = tuple(
            (
                clause.clause_id,
                clause.mechanism,
                tuple(
                    (
                        requirement.predicate_kind,
                        requirement.service_binding,
                        requirement.require_exact_parent,
                    )
                    for requirement in clause.requirements
                ),
            )
            for clause in self.clauses
        )
        if actual_policy != _FROZEN_SUPPORT_POLICY_V22:
            raise ValueError("evidence support policy differs from frozen clauses")
        if self.thresholds_sha256 != PredicateThresholdsV22.frozen().thresholds_sha256:
            raise ValueError("evidence support policy thresholds differ")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"policy_sha256"})
        )
        if self.policy_sha256 != expected:
            raise ValueError("evidence support policy digest differs")
        return self


class EvidenceSupportDecisionV22(DtaModelV22):
    schema_version: Literal["dta-v22.evidence-support-decision.v1"]
    mechanism: MechanismV22
    target_service: str
    parent_service: str | None
    accepted: StrictBool
    matched_clause_id: str | None
    supporting_predicate_ids: tuple[str, ...]
    supporting_evidence_refs: tuple[str, ...]
    decision_sha256: Sha256V22

    @model_validator(mode="after")
    def require_decision(self) -> EvidenceSupportDecisionV22:
        if self.accepted != (self.matched_clause_id is not None):
            raise ValueError("support decision acceptance differs from clause")
        for values in (
            self.supporting_predicate_ids,
            self.supporting_evidence_refs,
        ):
            if values != tuple(sorted(set(values))):
                raise ValueError("support decision bindings are not canonical")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"decision_sha256"})
        )
        if self.decision_sha256 != expected:
            raise ValueError("support decision digest differs")
        return self


class NoIncidentCoverageDecisionV22(DtaModelV22):
    schema_version: Literal["dta-v22.no-incident-coverage-decision.v1"]
    candidate_services: tuple[str, ...]
    accepted: StrictBool
    runtime_covered_services: tuple[str, ...]
    metric_covered_services: tuple[str, ...]
    denial_reasons: tuple[str, ...]
    decision_sha256: Sha256V22

    @model_validator(mode="after")
    def require_decision(self) -> NoIncidentCoverageDecisionV22:
        if self.accepted == bool(self.denial_reasons):
            raise ValueError("No-Incident acceptance and denials are inconsistent")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"decision_sha256"})
        )
        if self.decision_sha256 != expected:
            raise ValueError("No-Incident coverage decision digest differs")
        return self


def _build_predicate(
    *,
    kind: PredicateKindV22,
    source: EvidenceSourceV22,
    service: str,
    parent_service: str | None,
    evidence_refs: tuple[str, ...],
) -> EvidencePredicateV22:
    refs = tuple(sorted(set(evidence_refs)))
    identity = semantic_sha256_v22(
        {
            "kind": kind.value,
            "source": source.value,
            "service": service,
            "parent_service": parent_service,
            "evidence_refs": refs,
        }
    )
    payload: dict[str, Any] = {
        "schema_version": "dta-v22.evidence-predicate.v1",
        "predicate_id": f"p:{kind.value.casefold().replace('_', '-')}:{service}:{identity[:12]}",
        "predicate_kind": kind,
        "source": source,
        "service": service,
        "parent_service": parent_service,
        "evidence_refs": refs,
    }
    draft = EvidencePredicateV22.model_construct(
        **payload,
        predicate_sha256="0" * 64,
    )
    return EvidencePredicateV22.model_validate(
        {
            **payload,
            "predicate_sha256": semantic_sha256_v22(
                draft.model_dump(mode="json", exclude={"predicate_sha256"})
            ),
        }
    )


class PredicateExtractorV22:
    """Extract only source-local predicates from typed salient facts."""

    def __init__(self, *, thresholds: PredicateThresholdsV22) -> None:
        self.thresholds = PredicateThresholdsV22.model_validate(
            thresholds.model_dump(mode="python")
        )

    def extract(
        self,
        *,
        facts: tuple[SalientFactV22, ...],
    ) -> tuple[EvidencePredicateV22, ...]:
        predicates: list[EvidencePredicateV22] = []
        for fact in facts:
            kinds: list[tuple[PredicateKindV22, str | None]] = []
            payload = fact.payload
            if isinstance(payload, MetricSalientPayloadV22):
                if fact.signal_strength is SignalStrengthV22.STRONG:
                    if payload.metric_kind is MetricKindV22.ERROR_RATE:
                        kinds.append((PredicateKindV22.METRIC_ERROR_RATE_STRONG, None))
                    elif payload.metric_kind is MetricKindV22.LATENCY_P95_MS:
                        kinds.append((PredicateKindV22.METRIC_LATENCY_STRONG, None))
                    elif payload.metric_kind is MetricKindV22.MEMORY_BYTES:
                        kinds.append((PredicateKindV22.METRIC_MEMORY_STRONG, None))
            elif isinstance(payload, LogSalientPayloadV22):
                kind_by_category = {
                    LogCategoryV22.CONFIGURATION_ERROR: (
                        PredicateKindV22.LOG_CONFIGURATION_ERROR
                    ),
                    LogCategoryV22.DEPENDENCY_TIMEOUT: (
                        PredicateKindV22.LOG_DEPENDENCY_TIMEOUT
                    ),
                    LogCategoryV22.MEMORY_PRESSURE: (
                        PredicateKindV22.LOG_MEMORY_PRESSURE
                    ),
                }
                kind = kind_by_category.get(payload.category)
                if kind is not None:
                    kinds.append((kind, payload.downstream_service))
            elif isinstance(payload, TraceSalientPayloadV22):
                if payload.first_error_location:
                    kinds.append((PredicateKindV22.TRACE_FIRST_ERROR, payload.parent_service))
                if (
                    payload.parent_service is not None
                    and payload.baseline_ratio is not None
                    and payload.delta_ms is not None
                    and payload.baseline_ratio
                    >= self.thresholds.trace_latency_strong_ratio
                    and payload.delta_ms
                    >= self.thresholds.trace_latency_strong_delta_ms
                ):
                    kinds.append(
                        (
                            PredicateKindV22.TRACE_DEPENDENCY_LATENCY,
                            payload.parent_service,
                        )
                    )
            elif isinstance(payload, RuntimeSalientPayloadV22):
                if payload.state is not RuntimeStateV22.RUNNING:
                    kinds.append((PredicateKindV22.RUNTIME_NOT_RUNNING, None))
                if not payload.healthy:
                    kinds.append((PredicateKindV22.RUNTIME_UNHEALTHY, None))
                else:
                    kinds.append((PredicateKindV22.RUNTIME_HEALTHY, None))
                if payload.restart_count >= self.thresholds.restart_pressure_count:
                    kinds.append((PredicateKindV22.RUNTIME_RESTART_PRESSURE, None))
            elif isinstance(payload, ResourceSalientPayloadV22):
                if (
                    payload.cpu_p95_percent
                    >= self.thresholds.cpu_strong_p95_percent
                    and payload.cpu_baseline_ratio is not None
                    and payload.cpu_baseline_ratio
                    >= self.thresholds.cpu_strong_baseline_ratio
                ):
                    kinds.append((PredicateKindV22.RESOURCE_CPU_STRONG, None))
                if (
                    payload.memory_slope_bytes_per_second
                    >= self.thresholds.memory_growth_strong_bytes_per_second
                ):
                    kinds.append(
                        (PredicateKindV22.RESOURCE_MEMORY_GROWTH_STRONG, None)
                    )
            elif isinstance(payload, ChangeSalientPayloadV22):
                if payload.relative_seconds <= self.thresholds.recent_change_seconds:
                    kinds.append((PredicateKindV22.CHANGE_RECENT_ROLLOUT, None))
            for kind, parent in kinds:
                predicates.append(
                    _build_predicate(
                        kind=kind,
                        source=fact.source,
                        service=fact.service,
                        parent_service=parent,
                        evidence_refs=fact.evidence_refs,
                    )
                )
        return tuple(sorted(set(predicates), key=lambda item: item.predicate_id))


def _requirement(
    kind: PredicateKindV22,
    *,
    binding: RequirementServiceBindingV22 = RequirementServiceBindingV22.TARGET,
    exact_parent: bool = False,
) -> PredicateRequirementV22:
    return PredicateRequirementV22(
        predicate_kind=kind,
        service_binding=binding,
        require_exact_parent=exact_parent,
    )


def build_default_evidence_support_policy_v22() -> EvidenceSupportPolicyV22:
    clauses = tuple(
        sorted(
            (
                SupportClauseV22(
                    clause_id="configuration:change-and-log",
                    mechanism=MechanismV22.CONFIGURATION_ERROR,
                    requirements=(
                        _requirement(PredicateKindV22.CHANGE_RECENT_ROLLOUT),
                        _requirement(PredicateKindV22.LOG_CONFIGURATION_ERROR),
                    ),
                ),
                SupportClauseV22(
                    clause_id="configuration:change-and-error-metric",
                    mechanism=MechanismV22.CONFIGURATION_ERROR,
                    requirements=(
                        _requirement(PredicateKindV22.CHANGE_RECENT_ROLLOUT),
                        _requirement(PredicateKindV22.METRIC_ERROR_RATE_STRONG),
                    ),
                ),
                SupportClauseV22(
                    clause_id="service-unavailable:not-running",
                    mechanism=MechanismV22.SERVICE_UNAVAILABLE,
                    requirements=(
                        _requirement(PredicateKindV22.RUNTIME_NOT_RUNNING),
                    ),
                ),
                SupportClauseV22(
                    clause_id="service-unavailable:unhealthy-error-metric",
                    mechanism=MechanismV22.SERVICE_UNAVAILABLE,
                    requirements=(
                        _requirement(PredicateKindV22.RUNTIME_UNHEALTHY),
                        _requirement(PredicateKindV22.METRIC_ERROR_RATE_STRONG),
                    ),
                ),
                SupportClauseV22(
                    clause_id="service-unavailable:unhealthy-first-error",
                    mechanism=MechanismV22.SERVICE_UNAVAILABLE,
                    requirements=(
                        _requirement(PredicateKindV22.RUNTIME_UNHEALTHY),
                        _requirement(PredicateKindV22.TRACE_FIRST_ERROR),
                    ),
                ),
                SupportClauseV22(
                    clause_id="cpu-saturation:resource-and-healthy",
                    mechanism=MechanismV22.CPU_SATURATION,
                    requirements=(
                        _requirement(PredicateKindV22.RESOURCE_CPU_STRONG),
                        _requirement(PredicateKindV22.RUNTIME_HEALTHY),
                    ),
                ),
                SupportClauseV22(
                    clause_id="memory-leak:growth-and-memory-metric",
                    mechanism=MechanismV22.MEMORY_LEAK,
                    requirements=(
                        _requirement(PredicateKindV22.RESOURCE_MEMORY_GROWTH_STRONG),
                        _requirement(PredicateKindV22.METRIC_MEMORY_STRONG),
                    ),
                ),
                SupportClauseV22(
                    clause_id="memory-leak:growth-and-restarts",
                    mechanism=MechanismV22.MEMORY_LEAK,
                    requirements=(
                        _requirement(PredicateKindV22.RESOURCE_MEMORY_GROWTH_STRONG),
                        _requirement(PredicateKindV22.RUNTIME_RESTART_PRESSURE),
                    ),
                ),
                SupportClauseV22(
                    clause_id="memory-leak:growth-and-log",
                    mechanism=MechanismV22.MEMORY_LEAK,
                    requirements=(
                        _requirement(PredicateKindV22.RESOURCE_MEMORY_GROWTH_STRONG),
                        _requirement(PredicateKindV22.LOG_MEMORY_PRESSURE),
                    ),
                ),
                SupportClauseV22(
                    clause_id="dependency-latency:trace-and-metric",
                    mechanism=MechanismV22.DEPENDENCY_LATENCY,
                    requirements=(
                        _requirement(
                            PredicateKindV22.TRACE_DEPENDENCY_LATENCY,
                            exact_parent=True,
                        ),
                        _requirement(
                            PredicateKindV22.METRIC_LATENCY_STRONG,
                            binding=RequirementServiceBindingV22.TARGET_OR_PARENT,
                        ),
                    ),
                ),
            ),
            key=lambda item: item.clause_id,
        )
    )
    thresholds = PredicateThresholdsV22.frozen()
    payload: dict[str, Any] = {
        "schema_version": "dta-v22.evidence-support-policy.v1",
        "thresholds_sha256": thresholds.thresholds_sha256,
        "clauses": clauses,
    }
    draft = EvidenceSupportPolicyV22.model_construct(
        **payload,
        policy_sha256="0" * 64,
    )
    return EvidenceSupportPolicyV22.model_validate(
        {
            **payload,
            "policy_sha256": semantic_sha256_v22(
                draft.model_dump(mode="json", exclude={"policy_sha256"})
            ),
        }
    )


def _predicate_matches(
    *,
    predicate: EvidencePredicateV22,
    requirement: PredicateRequirementV22,
    target_service: str,
    parent_service: str | None,
) -> bool:
    if predicate.predicate_kind is not requirement.predicate_kind:
        return False
    allowed_services = {target_service}
    if (
        requirement.service_binding is RequirementServiceBindingV22.TARGET_OR_PARENT
        and parent_service is not None
    ):
        allowed_services.add(parent_service)
    if predicate.service not in allowed_services:
        return False
    if requirement.require_exact_parent and predicate.parent_service != parent_service:
        return False
    return True


def _support_decision(
    *,
    mechanism: MechanismV22,
    target_service: str,
    parent_service: str | None,
    matched_clause_id: str | None,
    predicates: tuple[EvidencePredicateV22, ...],
) -> EvidenceSupportDecisionV22:
    payload: dict[str, Any] = {
        "schema_version": "dta-v22.evidence-support-decision.v1",
        "mechanism": mechanism,
        "target_service": target_service,
        "parent_service": parent_service,
        "accepted": matched_clause_id is not None,
        "matched_clause_id": matched_clause_id,
        "supporting_predicate_ids": tuple(
            sorted(item.predicate_id for item in predicates)
        ),
        "supporting_evidence_refs": tuple(
            sorted({ref for item in predicates for ref in item.evidence_refs})
        ),
    }
    draft = EvidenceSupportDecisionV22.model_construct(
        **payload,
        decision_sha256="0" * 64,
    )
    return EvidenceSupportDecisionV22.model_validate(
        {
            **payload,
            "decision_sha256": semantic_sha256_v22(
                draft.model_dump(mode="json", exclude={"decision_sha256"})
            ),
        }
    )


def evaluate_support_v22(
    *,
    policy: EvidenceSupportPolicyV22,
    mechanism: MechanismV22,
    target_service: str,
    parent_service: str | None,
    predicates: tuple[EvidencePredicateV22, ...],
) -> EvidenceSupportDecisionV22:
    policy = EvidenceSupportPolicyV22.model_validate(policy.model_dump(mode="python"))
    canonical = tuple(sorted(predicates, key=lambda item: item.predicate_id))
    for clause in policy.clauses:
        if clause.mechanism is not mechanism:
            continue
        matched: list[EvidencePredicateV22] = []
        for requirement in clause.requirements:
            candidate = next(
                (
                    item
                    for item in canonical
                    if _predicate_matches(
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
            matched.append(candidate)
        else:
            return _support_decision(
                mechanism=mechanism,
                target_service=target_service,
                parent_service=parent_service,
                matched_clause_id=clause.clause_id,
                predicates=tuple(matched),
            )
    return _support_decision(
        mechanism=mechanism,
        target_service=target_service,
        parent_service=parent_service,
        matched_clause_id=None,
        predicates=(),
    )


_ANOMALY_PREDICATES = frozenset(PredicateKindV22) - {
    PredicateKindV22.CHANGE_RECENT_ROLLOUT,
    PredicateKindV22.RUNTIME_HEALTHY,
}


def evaluate_no_incident_v22(
    *,
    memory: SalientEvidenceMemoryV22,
    candidate_services: tuple[str, ...],
) -> NoIncidentCoverageDecisionV22:
    candidates = tuple(sorted(set(candidate_services)))
    if not candidates or candidates != candidate_services:
        raise ValueError("No-Incident candidates are not canonical")
    runtime_covered: set[str] = set()
    metric_kinds: dict[str, set[MetricKindV22]] = {item: set() for item in candidates}
    for fact in memory.salient_facts:
        if fact.service not in metric_kinds:
            continue
        if isinstance(fact.payload, RuntimeSalientPayloadV22):
            if fact.payload.state is RuntimeStateV22.RUNNING and fact.payload.healthy:
                runtime_covered.add(fact.service)
        elif (
            isinstance(fact.payload, MetricSalientPayloadV22)
            and fact.payload.support_status is MetricSupportStatusV22.SUPPORTED
            and fact.payload.sample_count > 0
        ):
            metric_kinds[fact.service].add(fact.payload.metric_kind)
    core = {
        MetricKindV22.ERROR_RATE,
        MetricKindV22.LATENCY_P95_MS,
        MetricKindV22.REQUEST_SUPPORT,
    }
    metric_covered = {
        service for service, kinds in metric_kinds.items() if core.issubset(kinds)
    }
    reasons: list[str] = []
    if runtime_covered != set(candidates):
        reasons.append("RUNTIME_COVERAGE_OR_HEALTH_INCOMPLETE")
    if metric_covered != set(candidates):
        reasons.append("METRIC_COVERAGE_INCOMPLETE")
    if any(
        item.service in set(candidates)
        and item.predicate_kind in _ANOMALY_PREDICATES
        for item in memory.predicates
    ):
        reasons.append("STRONG_ANOMALY_PRESENT")
    payload: dict[str, Any] = {
        "schema_version": "dta-v22.no-incident-coverage-decision.v1",
        "candidate_services": candidates,
        "accepted": not reasons,
        "runtime_covered_services": tuple(sorted(runtime_covered)),
        "metric_covered_services": tuple(sorted(metric_covered)),
        "denial_reasons": tuple(sorted(reasons)),
    }
    draft = NoIncidentCoverageDecisionV22.model_construct(
        **payload,
        decision_sha256="0" * 64,
    )
    return NoIncidentCoverageDecisionV22.model_validate(
        {
            **payload,
            "decision_sha256": semantic_sha256_v22(
                draft.model_dump(mode="json", exclude={"decision_sha256"})
            ),
        }
    )


__all__ = (
    "EvidenceSupportDecisionV22",
    "EvidenceSupportPolicyV22",
    "MechanismV22",
    "NoIncidentCoverageDecisionV22",
    "PredicateExtractorV22",
    "PredicateKindV22",
    "build_default_evidence_support_policy_v22",
    "evaluate_no_incident_v22",
    "evaluate_support_v22",
)
