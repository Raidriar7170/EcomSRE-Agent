"""Deterministic semantic validator for DTA v2.3.4 registration drafts."""

from __future__ import annotations

from enum import Enum
import re
from typing import Any, Literal

from pydantic import Field, StrictFloat, model_validator

from ecomsre.dta_v2.v22.memory import (
    LogCategoryV22,
    PredicateKindV22,
    PredicateThresholdsV22,
)
from ecomsre.dta_v2.v22.predicates import RequirementServiceBindingV22
from ecomsre.dta_v2.v22.read_contracts import (
    DtaModelV22,
    EvidenceSourceV22,
    semantic_sha256_v22,
)
from ecomsre.dta_v2.v23.ontology_expansion_v234 import (
    DraftGenerationAuthorizationResultV234,
    OntologyExpansionStateV234,
)
from ecomsre.dta_v2.v23.registration_contracts_v234 import (
    CorePredicateReferenceRuleV234,
    FormalFaultRegistrationDraftV234,
    GenericAnomalyKindRuleV234,
    LogCategoryRuleV234,
    LogTemplateContainsAnyRuleV234,
    MetricBaselineRatioRuleV234,
    MetricThresholdRuleV234,
    PredicateImplementationModeV234,
    RecentChangeStateRuleV234,
    RegistrationImplementationModeV234,
    ResourceCpuThresholdRuleV234,
    ResourceMemorySlopeRuleV234,
    RuntimeStateRuleV234,
    TraceDurationThresholdRuleV234,
    TraceFirstErrorAtServiceRuleV234,
    TracePathContainsRuleV234,
    hashed_model_v234,
)
from ecomsre.dta_v2.v23.registration_provider_v234 import (
    AcceptedEvidenceSummaryV234,
    AcceptedReportProjectionV234,
    project_development_report_v234,
)
from ecomsre.dta_v2.v23.review_registry import ReviewQueueItemV23, ShadowFaultEntryV23


class DraftValidationStatusV234(str, Enum):
    VALID = "VALID"
    INVALID = "INVALID"
    ENGINEERING_REQUIRED = "ENGINEERING_REQUIRED"
    NON_REGISTRABLE = "NON_REGISTRABLE"


class DuplicateAbsorptionPolicyV234(DtaModelV22):
    schema_version: Literal["dta-v234.duplicate-absorption-policy.v1"]
    shadow_extension_similarity_threshold: StrictFloat = Field(ge=0.0, le=1.0)
    core_semantic_equivalence_mode: Literal["NORMALIZED_FROZEN_DNF"]
    core_control_absorption_mode: Literal["DRAFT_CLAUSE_SUBSET_OF_FROZEN_CLAUSE"]
    no_incident_absorption_mode: Literal["BROAD_GENERIC_ONLY_CLAUSE"]
    policy_sha256: str

    @model_validator(mode="after")
    def require_policy(self) -> "DuplicateAbsorptionPolicyV234":
        if self.shadow_extension_similarity_threshold != 0.75:
            raise ValueError("duplicate similarity threshold differs from frozen policy")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"policy_sha256"})
        )
        if self.policy_sha256 != expected:
            raise ValueError("duplicate absorption policy digest differs")
        return self


_DUPLICATE_POLICY_PAYLOAD_V234: dict[str, Any] = {
    "schema_version": "dta-v234.duplicate-absorption-policy.v1",
    "shadow_extension_similarity_threshold": 0.75,
    "core_semantic_equivalence_mode": "NORMALIZED_FROZEN_DNF",
    "core_control_absorption_mode": "DRAFT_CLAUSE_SUBSET_OF_FROZEN_CLAUSE",
    "no_incident_absorption_mode": "BROAD_GENERIC_ONLY_CLAUSE",
}


FROZEN_DUPLICATE_ABSORPTION_POLICY_V234 = DuplicateAbsorptionPolicyV234(
    **_DUPLICATE_POLICY_PAYLOAD_V234,
    policy_sha256=semantic_sha256_v22(_DUPLICATE_POLICY_PAYLOAD_V234),
)


class RegistrationDraftValidationV234(DtaModelV22):
    schema_version: Literal["dta-v234.registration-draft-validation.v1"]
    draft_id: str
    draft_sha256: str
    core_ontology_snapshot_sha256: str
    duplicate_absorption_policy_sha256: str
    status: DraftValidationStatusV234
    classification: RegistrationImplementationModeV234
    error_codes: tuple[str, ...]
    errors: tuple[str, ...]
    warning_codes: tuple[str, ...]
    validation_sha256: str

    @model_validator(mode="after")
    def require_validation(self) -> "RegistrationDraftValidationV234":
        for values, label in (
            (self.error_codes, "error codes"),
            (self.warning_codes, "warning codes"),
        ):
            if values != tuple(sorted(set(values))):
                raise ValueError(f"registration validation {label} are not canonical")
        if len(self.error_codes) != len(self.errors):
            raise ValueError("registration validation error details differ")
        if (
            self.duplicate_absorption_policy_sha256
            != FROZEN_DUPLICATE_ABSORPTION_POLICY_V234.policy_sha256
        ):
            raise ValueError("registration validation duplicate policy differs")
        if self.status is DraftValidationStatusV234.VALID and self.error_codes:
            raise ValueError("valid registration validation carries errors")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"validation_sha256"})
        )
        if self.validation_sha256 != expected:
            raise ValueError("registration validation digest differs")
        return self


_RULE_SOURCE_V234 = {
    GenericAnomalyKindRuleV234: None,
    LogCategoryRuleV234: EvidenceSourceV22.LOGS,
    LogTemplateContainsAnyRuleV234: EvidenceSourceV22.LOGS,
    TraceFirstErrorAtServiceRuleV234: EvidenceSourceV22.TRACES,
    TracePathContainsRuleV234: EvidenceSourceV22.TRACES,
    TraceDurationThresholdRuleV234: EvidenceSourceV22.TRACES,
    MetricThresholdRuleV234: EvidenceSourceV22.METRICS,
    MetricBaselineRatioRuleV234: EvidenceSourceV22.METRICS,
    ResourceCpuThresholdRuleV234: EvidenceSourceV22.RESOURCES,
    ResourceMemorySlopeRuleV234: EvidenceSourceV22.RESOURCES,
    RuntimeStateRuleV234: EvidenceSourceV22.RUNTIME,
    RecentChangeStateRuleV234: EvidenceSourceV22.CHANGES,
}


_GENERIC_ANOMALY_SOURCE_V234 = {
    "METRIC_ERROR_OUTLIER": EvidenceSourceV22.METRICS,
    "METRIC_LATENCY_OUTLIER": EvidenceSourceV22.METRICS,
    "RUNTIME_NOT_RUNNING": EvidenceSourceV22.RUNTIME,
    "RUNTIME_UNHEALTHY": EvidenceSourceV22.RUNTIME,
    "RUNTIME_RESTART_ANOMALY": EvidenceSourceV22.RUNTIME,
    "RESOURCE_CPU_OUTLIER": EvidenceSourceV22.RESOURCES,
    "RESOURCE_MEMORY_TREND": EvidenceSourceV22.RESOURCES,
    "TRACE_ERROR_LOCALIZATION": EvidenceSourceV22.TRACES,
    "TRACE_LATENCY_OUTLIER": EvidenceSourceV22.TRACES,
    "LOG_ERROR_CLUSTER": EvidenceSourceV22.LOGS,
    "LOG_UNKNOWN_ERROR_PATTERN": EvidenceSourceV22.LOGS,
    "RECENT_CHANGE_CORRELATION": EvidenceSourceV22.CHANGES,
}


def _add_error(
    errors: dict[str, str],
    code: str,
    message: str,
) -> None:
    errors.setdefault(code, message)


def _slug_similarity_v234(left: str, right: str) -> float:
    left_tokens = set(re.findall(r"[a-z0-9]+", left.casefold()))
    right_tokens = set(re.findall(r"[a-z0-9]+", right.casefold()))
    return (
        len(left_tokens & right_tokens) / len(left_tokens | right_tokens)
        if left_tokens or right_tokens
        else 0.0
    )


def _draft_core_clause_signature_v234(
    *,
    clause_requirements: tuple[Any, ...],
    predicate_by_name: dict[str, Any],
    require_equivalent: bool,
) -> frozenset[tuple[str, str, bool]] | None:
    values: set[tuple[str, str, bool]] = set()
    for requirement in clause_requirements:
        predicate = predicate_by_name.get(requirement.predicate_name)
        if predicate is None:
            return None
        alias = _core_predicate_alias_v234(predicate.extraction_rule)
        if alias is None or (require_equivalent and not alias[1]):
            return None
        values.add(
            (
                alias[0].value,
                requirement.service_binding.value,
                requirement.require_exact_parent,
            )
        )
    return frozenset(values)


_LOG_CATEGORY_CORE_KIND_V234 = {
    LogCategoryV22.CONFIGURATION_ERROR: PredicateKindV22.LOG_CONFIGURATION_ERROR,
    LogCategoryV22.DEPENDENCY_TIMEOUT: PredicateKindV22.LOG_DEPENDENCY_TIMEOUT,
    LogCategoryV22.MEMORY_PRESSURE: PredicateKindV22.LOG_MEMORY_PRESSURE,
}

_GENERIC_ANOMALY_CORE_KIND_V234 = {
    "METRIC_ERROR_OUTLIER": PredicateKindV22.METRIC_ERROR_RATE_STRONG,
    "METRIC_LATENCY_OUTLIER": PredicateKindV22.METRIC_LATENCY_STRONG,
    "RUNTIME_NOT_RUNNING": PredicateKindV22.RUNTIME_NOT_RUNNING,
    "RUNTIME_UNHEALTHY": PredicateKindV22.RUNTIME_UNHEALTHY,
    "RUNTIME_RESTART_ANOMALY": PredicateKindV22.RUNTIME_RESTART_PRESSURE,
    "RESOURCE_CPU_OUTLIER": PredicateKindV22.RESOURCE_CPU_STRONG,
    "RESOURCE_MEMORY_TREND": PredicateKindV22.RESOURCE_MEMORY_GROWTH_STRONG,
    "TRACE_ERROR_LOCALIZATION": PredicateKindV22.TRACE_FIRST_ERROR,
    "TRACE_LATENCY_OUTLIER": PredicateKindV22.TRACE_DEPENDENCY_LATENCY,
    "RECENT_CHANGE_CORRELATION": PredicateKindV22.CHANGE_RECENT_ROLLOUT,
}


def _core_predicate_alias_v234(
    rule: object,
) -> tuple[PredicateKindV22, bool] | None:
    """Return a core kind and whether the declarative rule is fully equivalent.

    A non-equivalent alias is still useful for fail-closed absorption detection:
    every observation satisfying the core predicate also satisfies that broader
    declarative rule.
    """

    if isinstance(rule, CorePredicateReferenceRuleV234):
        return rule.predicate_kind, True
    if isinstance(rule, LogCategoryRuleV234):
        kind = _LOG_CATEGORY_CORE_KIND_V234.get(rule.category)
        return (kind, True) if kind is not None else None
    if isinstance(rule, TraceFirstErrorAtServiceRuleV234):
        return PredicateKindV22.TRACE_FIRST_ERROR, True
    if isinstance(rule, GenericAnomalyKindRuleV234):
        kind = _GENERIC_ANOMALY_CORE_KIND_V234.get(rule.anomaly_kind.value)
        return (kind, False) if kind is not None else None

    thresholds = PredicateThresholdsV22.frozen()
    upper_tail = {
        "GREATER_THAN",
        "GREATER_THAN_OR_EQUAL",
    }
    if (
        isinstance(rule, ResourceCpuThresholdRuleV234)
        and rule.comparison.value in upper_tail
        and rule.percent <= thresholds.cpu_strong_p95_percent
    ):
        return PredicateKindV22.RESOURCE_CPU_STRONG, False
    if (
        isinstance(rule, ResourceMemorySlopeRuleV234)
        and rule.comparison in upper_tail
        and rule.bytes_per_second
        <= thresholds.memory_growth_strong_bytes_per_second
    ):
        return PredicateKindV22.RESOURCE_MEMORY_GROWTH_STRONG, False
    if isinstance(rule, RuntimeStateRuleV234):
        state_values = {item.value for item in rule.states}
        if "RUNNING" in state_values:
            return PredicateKindV22.RUNTIME_HEALTHY, False
        if {"EXITED", "ABSENT", "OTHER"}.issubset(state_values):
            return PredicateKindV22.RUNTIME_NOT_RUNNING, True
    return None


def _core_clause_signature_v234(
    requirements: tuple[Any, ...],
) -> frozenset[tuple[str, str, bool]]:
    return frozenset(
        (
            requirement.predicate_kind.value,
            requirement.service_binding.value,
            requirement.require_exact_parent,
        )
        for requirement in requirements
    )


def validate_registration_draft_v234(
    *,
    draft: FormalFaultRegistrationDraftV234,
    authorization_context: DraftGenerationAuthorizationResultV234,
    shadow: ShadowFaultEntryV23,
    accepted_reports: tuple[ReviewQueueItemV23, ...],
    promoted_mechanism_slugs: tuple[str, ...],
    shadow_mechanism_slugs: tuple[str, ...],
) -> RegistrationDraftValidationV234:
    """Validate semantics once; never trigger a Provider repair or retry."""

    errors: dict[str, str] = {}
    warnings: set[str] = set()
    snapshot = authorization_context.core_ontology_snapshot
    authorization = authorization_context.authorization
    seed = authorization_context.registration_seed
    transition = authorization_context.transition
    if draft.authorization_id != authorization.authorization_id:
        _add_error(
            errors,
            "AUTHORIZATION_BINDING_MISMATCH",
            "Draft authorization ID differs from the persisted authorization context.",
        )
    if draft.registration_seed_sha256 != seed.seed_sha256:
        _add_error(
            errors,
            "REGISTRATION_SEED_BINDING_MISMATCH",
            "Draft registration seed differs from the persisted authorization context.",
        )
    if draft.shadow_fault_id != shadow.shadow_fault_id or (
        seed.shadow_fault_id != shadow.shadow_fault_id
    ):
        _add_error(
            errors,
            "SHADOW_FAULT_BINDING_MISMATCH",
            "Draft, seed, and persisted Shadow Fault bindings differ.",
        )
    if (
        transition.to_state
        is not OntologyExpansionStateV234.DRAFT_GENERATION_AUTHORIZED
        or transition.authorization_sha256 != authorization.authorization_sha256
        or transition.registration_seed_sha256 != seed.seed_sha256
    ):
        _add_error(
            errors,
            "AUTHORIZATION_TRANSITION_INVALID",
            "Draft generation does not bind a complete authorized transition.",
        )
    if draft.core_ontology_snapshot_sha256 != snapshot.snapshot_sha256:
        _add_error(
            errors,
            "CORE_SNAPSHOT_BINDING_MISMATCH",
            "Draft core ontology snapshot binding differs from the validator snapshot.",
        )
    report_by_id = {item.report.report_id: item for item in accepted_reports}
    if tuple(sorted(report_by_id)) != draft.test_plan.positive_report_ids:
        _add_error(
            errors,
            "POSITIVE_REPORT_BINDING_MISMATCH",
            "Draft positive report IDs differ from accepted persisted reports.",
        )
    case_ids = tuple(sorted(item.source_case_id for item in accepted_reports))
    if case_ids != draft.test_plan.positive_case_ids:
        _add_error(
            errors,
            "POSITIVE_CASE_BINDING_MISMATCH",
            "Draft positive case IDs differ from accepted persisted reports.",
        )
    seed_bindings = {
        item.report_id: item for item in seed.accepted_report_bindings
    }
    for report_id, item in report_by_id.items():
        binding = seed_bindings.get(report_id)
        if binding is None or (
            binding.report_sha256 != item.report.report_sha256
            or binding.queue_item_sha256 != item.queue_item_sha256
            or binding.source_case_id != item.source_case_id
        ):
            _add_error(
                errors,
                f"ACCEPTED_REPORT_HASH_BINDING_MISMATCH:{report_id}",
                f"Accepted report {report_id} differs from its registration seed binding.",
            )
    accepted_evidence_refs = {
        ref
        for item in accepted_reports
        for ref in item.report.supporting_evidence_refs
    }
    evidence_binding_by_ref: dict[
        str,
        tuple[AcceptedReportProjectionV234, AcceptedEvidenceSummaryV234],
    ] = {}
    try:
        accepted_projections = tuple(
            project_development_report_v234(item)
            for item in sorted(
                accepted_reports,
                key=lambda value: value.report.report_id,
            )
        )
    except ValueError:
        accepted_projections = ()
        _add_error(
            errors,
            "ACCEPTED_REPORT_PROJECTION_INVALID",
            "Accepted reports cannot be projected into bound evidence summaries.",
        )
    for projection in accepted_projections:
        for summary in projection.evidence_summaries:
            existing = evidence_binding_by_ref.get(summary.evidence_ref)
            evidence_binding_pair = (projection, summary)
            if existing is not None and existing != evidence_binding_pair:
                _add_error(
                    errors,
                    f"AMBIGUOUS_EVIDENCE_REF:{summary.evidence_ref}",
                    f"Evidence ref {summary.evidence_ref} has multiple accepted-report bindings.",
                )
            else:
                evidence_binding_by_ref[summary.evidence_ref] = evidence_binding_pair
    core_enum_names = {item.value for item in snapshot.core_mechanisms}
    core_slugs = {item.value.casefold().replace("_", "-") for item in snapshot.core_mechanisms}
    if (
        draft.mechanism.mechanism_enum_name.casefold().replace("_", "-")
        != draft.mechanism.mechanism_slug
    ):
        _add_error(
            errors,
            "MECHANISM_NAME_SLUG_MISMATCH",
            "Proposed mechanism enum name and slug are not a canonical bijection.",
        )
    mechanism_collision = (
        draft.mechanism.mechanism_enum_name in core_enum_names
        or draft.mechanism.mechanism_slug in core_slugs
    )
    promoted_collision = draft.mechanism.mechanism_slug in set(
        promoted_mechanism_slugs
    )
    shadow_collision = draft.mechanism.mechanism_slug in set(
        shadow_mechanism_slugs
    )
    any_registration_collision = (
        mechanism_collision or promoted_collision or shadow_collision
    )
    if mechanism_collision and (
        draft.implementation_mode
        is not RegistrationImplementationModeV234.DUPLICATE_EXISTING
    ):
        _add_error(
            errors,
            "CORE_MECHANISM_COLLISION",
            "Proposed mechanism collides with an existing core mechanism.",
        )
    if draft.mechanism.mechanism_enum_name in {"NO_INCIDENT", "UNKNOWN"}:
        _add_error(
            errors,
            "NON_INCIDENT_MECHANISM_FORBIDDEN",
            "NO_INCIDENT and UNKNOWN cannot be extension incident mechanisms.",
        )
    if promoted_collision and (
        draft.implementation_mode
        is not RegistrationImplementationModeV234.DUPLICATE_EXISTING
    ):
        _add_error(
            errors,
            "PROMOTED_EXTENSION_COLLISION",
            "Proposed mechanism collides with a promoted extension.",
        )
    if shadow_collision and (
        draft.implementation_mode
        is not RegistrationImplementationModeV234.DUPLICATE_EXISTING
    ):
        _add_error(
            errors,
            "SHADOW_MECHANISM_COLLISION",
            "Proposed mechanism collides with another Shadow Fault.",
        )

    predicate_by_name = {item.predicate_name: item for item in draft.predicates}
    core_source_by_kind = {
        item.predicate_kind: item.evidence_source
        for item in snapshot.predicate_source_bindings
    }
    enabled_sources = set(core_source_by_kind.values())
    core_predicate_names = {item.value for item in snapshot.core_predicate_kinds}
    for predicate in draft.predicates:
        if (
            predicate.predicate_name.casefold().replace("_", "-")
            != predicate.predicate_slug
        ):
            _add_error(
                errors,
                f"PREDICATE_NAME_SLUG_MISMATCH:{predicate.predicate_name}",
                f"Predicate {predicate.predicate_name} name and slug are not a canonical bijection.",
            )
        if not predicate.supporting_report_evidence_refs:
            _add_error(
                errors,
                f"MISSING_PREDICATE_EVIDENCE:{predicate.predicate_name}",
                f"Predicate {predicate.predicate_name} lacks accepted-report evidence.",
            )
        if not set(predicate.supporting_report_evidence_refs).issubset(
            accepted_evidence_refs
        ):
            _add_error(
                errors,
                f"UNKNOWN_EVIDENCE_REF:{predicate.predicate_name}",
                f"Predicate {predicate.predicate_name} cites evidence absent from accepted reports.",
            )
        for evidence_ref in predicate.supporting_report_evidence_refs:
            evidence_binding = evidence_binding_by_ref.get(evidence_ref)
            if evidence_binding is None:
                _add_error(
                    errors,
                    f"UNBOUND_EVIDENCE_REF:{predicate.predicate_name}:{evidence_ref}",
                    f"Predicate {predicate.predicate_name} evidence ref lacks a typed report projection.",
                )
                continue
            projection, evidence = evidence_binding
            if evidence.source is not predicate.evidence_source:
                _add_error(
                    errors,
                    f"EVIDENCE_REF_SOURCE_MISMATCH:{predicate.predicate_name}:{evidence_ref}",
                    f"Predicate {predicate.predicate_name} source differs from evidence ref {evidence_ref}.",
                )
            if (
                predicate.service_binding is RequirementServiceBindingV22.TARGET
                and evidence.service != projection.selected_root_service
            ):
                _add_error(
                    errors,
                    f"EVIDENCE_REF_ROOT_MISMATCH:{predicate.predicate_name}:{evidence_ref}",
                    f"Predicate {predicate.predicate_name} target evidence is not at the selected root.",
                )
            if (
                predicate.service_binding
                is RequirementServiceBindingV22.TARGET_OR_PARENT
                and predicate.require_exact_parent
                and evidence.service == projection.selected_root_service
            ):
                _add_error(
                    errors,
                    f"EVIDENCE_REF_PARENT_MISMATCH:{predicate.predicate_name}:{evidence_ref}",
                    f"Predicate {predicate.predicate_name} parent evidence resolves to the selected root.",
                )
        rule = predicate.extraction_rule
        expected_source: EvidenceSourceV22 | None
        if isinstance(rule, CorePredicateReferenceRuleV234):
            expected_source = core_source_by_kind.get(rule.predicate_kind)
            expected_slug = rule.predicate_kind.value.casefold().replace("_", "-")
            if (
                predicate.predicate_name != rule.predicate_kind.value
                or predicate.predicate_slug != expected_slug
                or predicate.implementation_mode
                is not PredicateImplementationModeV234.REUSE_CORE_PREDICATE
            ):
                _add_error(
                    errors,
                    f"CORE_PREDICATE_NAME_MISMATCH:{predicate.predicate_name}",
                    f"Predicate {predicate.predicate_name} does not preserve its core predicate name.",
                )
        elif isinstance(rule, GenericAnomalyKindRuleV234):
            expected_source = _GENERIC_ANOMALY_SOURCE_V234.get(rule.anomaly_kind.value)
            if expected_source is None:
                _add_error(
                    errors,
                    f"UNSUPPORTED_GENERIC_ANOMALY_RULE:{predicate.predicate_name}",
                    f"Predicate {predicate.predicate_name} uses a non-extractable generic anomaly.",
                )
        elif rule is None:
            expected_source = None
            if (
                predicate.implementation_mode
                is not PredicateImplementationModeV234.REQUIRES_CODE_IMPLEMENTATION
            ):
                _add_error(
                    errors,
                    f"MISSING_EXTRACTION_RULE:{predicate.predicate_name}",
                    f"Predicate {predicate.predicate_name} lacks an extraction rule.",
                )
        else:
            expected_source = _RULE_SOURCE_V234[type(rule)]
        if expected_source is not None and predicate.evidence_source is not expected_source:
            _add_error(
                errors,
                f"UNREACHABLE_SOURCE_RULE:{predicate.predicate_name}",
                f"Predicate {predicate.predicate_name} source differs from its extraction rule.",
            )
        if predicate.evidence_source not in enabled_sources:
            _add_error(
                errors,
                f"SOURCE_CAPABILITY_UNREACHABLE:{predicate.predicate_name}",
                f"Predicate {predicate.predicate_name} lacks an enabled source capability.",
            )
        if (
            predicate.implementation_mode
            is PredicateImplementationModeV234.DECLARATIVE_EXTENSION_PREDICATE
            and predicate.predicate_name in core_predicate_names
        ):
            _add_error(
                errors,
                f"CORE_PREDICATE_COLLISION:{predicate.predicate_name}",
                f"Extension predicate {predicate.predicate_name} collides with a core predicate.",
            )

    direct_allowlist = set(snapshot.authoritative_single_predicate_allowlist)
    has_independently_corroborated_clause = False
    has_authoritative_direct_clause = False
    for clause in draft.support_clauses:
        sources: set[EvidenceSourceV22] = set()
        for requirement in clause.requirements:
            clause_predicate = predicate_by_name.get(requirement.predicate_name)
            if clause_predicate is None:
                _add_error(
                    errors,
                    f"UNRESOLVED_REQUIREMENT:{clause.clause_id}:{requirement.predicate_name}",
                    f"Clause {clause.clause_id} references an undeclared predicate.",
                )
                continue
            if (
                requirement.service_binding is not clause_predicate.service_binding
                or requirement.require_exact_parent
                != clause_predicate.require_exact_parent
            ):
                _add_error(
                    errors,
                    f"CLAUSE_BINDING_MISMATCH:{clause.clause_id}:{requirement.predicate_name}",
                    f"Clause {clause.clause_id} changes the predicate service binding.",
                )
            sources.add(clause_predicate.evidence_source)
        if len(clause.requirements) == 1:
            singleton_predicate = predicate_by_name.get(
                clause.requirements[0].predicate_name
            )
            core_kind = (
                singleton_predicate.extraction_rule.predicate_kind
                if singleton_predicate is not None
                and isinstance(
                    singleton_predicate.extraction_rule,
                    CorePredicateReferenceRuleV234,
                )
                else None
            )
            if core_kind not in direct_allowlist:
                _add_error(
                    errors,
                    f"NON_AUTHORITATIVE_SINGLETON_CLAUSE:{clause.clause_id}",
                    f"Clause {clause.clause_id} uses a non-authoritative singleton predicate.",
                )
            else:
                has_authoritative_direct_clause = True
        elif len(sources) >= 2:
            has_independently_corroborated_clause = True
        resolved_clause_predicates = tuple(
            predicate_by_name[requirement.predicate_name]
            for requirement in clause.requirements
            if requirement.predicate_name in predicate_by_name
        )
        if resolved_clause_predicates and len(resolved_clause_predicates) == len(
            clause.requirements
        ) and all(
            isinstance(
                predicate.extraction_rule,
                (GenericAnomalyKindRuleV234, LogCategoryRuleV234),
            )
            for predicate in resolved_clause_predicates
        ):
            _add_error(
                errors,
                f"NO_INCIDENT_ABSORPTION_RISK:{clause.clause_id}",
                f"Clause {clause.clause_id} contains only broad generic rules and can absorb No-Incident controls.",
            )
        clause_refs = {
            ref
            for requirement in clause.requirements
            for ref in (
                predicate_by_name[requirement.predicate_name].supporting_report_evidence_refs
                if requirement.predicate_name in predicate_by_name
                else ()
            )
        }
        clause_report_ids = {
            binding[0].accepted_seed_report_id
            for ref in clause_refs
            for binding in (evidence_binding_by_ref.get(ref),)
            if binding is not None
        }
        covered_by_one_report = (
            bool(clause_refs)
            and len(clause_report_ids) == 1
            and clause_refs.issubset(evidence_binding_by_ref)
        )
        if not covered_by_one_report:
            _add_error(
                errors,
                f"CLAUSE_CROSS_REPORT_OR_ROOT_STITCHING:{clause.clause_id}",
                f"Clause {clause.clause_id} is not jointly supported by one accepted report at its selected root.",
            )

    if draft.support_clauses and not (
        has_independently_corroborated_clause or has_authoritative_direct_clause
    ):
        _add_error(
            errors,
            "CLAUSE_LACKS_INDEPENDENT_CORROBORATION",
            "No support clause has two evidence sources or an authoritative direct-state predicate.",
        )

    core_signatures_by_mechanism: dict[
        str,
        frozenset[frozenset[tuple[str, str, bool]]],
    ] = {}
    for mechanism in snapshot.core_mechanisms:
        core_signatures_by_mechanism[mechanism.value] = frozenset(
            _core_clause_signature_v234(clause.requirements)
            for clause in snapshot.frozen_core_support_clauses
            if clause.mechanism is mechanism
        )
    draft_core_signatures = tuple(
        signature
        for clause in draft.support_clauses
        for signature in (
            _draft_core_clause_signature_v234(
                clause_requirements=clause.requirements,
                predicate_by_name=predicate_by_name,
                require_equivalent=True,
            ),
        )
        if signature is not None
    )
    semantic_core_equivalents = tuple(
        mechanism
        for mechanism, signatures in sorted(core_signatures_by_mechanism.items())
        if len(draft_core_signatures) == len(draft.support_clauses)
        and frozenset(draft_core_signatures) == signatures
    )
    core_absorbed_controls = tuple(
        sorted(
            {
                f"{mechanism}:{clause.clause_id}"
                for clause, draft_signature in (
                    (
                        clause,
                        _draft_core_clause_signature_v234(
                            clause_requirements=clause.requirements,
                            predicate_by_name=predicate_by_name,
                            require_equivalent=False,
                        ),
                    )
                    for clause in draft.support_clauses
                )
                if draft_signature is not None
                for mechanism, signatures in core_signatures_by_mechanism.items()
                for core_signature in signatures
                if draft_signature.issubset(core_signature)
            }
        )
    )
    similar_existing_slugs = tuple(
        slug
        for slug in sorted(set((*promoted_mechanism_slugs, *shadow_mechanism_slugs)))
        if _slug_similarity_v234(draft.mechanism.mechanism_slug, slug)
        >= FROZEN_DUPLICATE_ABSORPTION_POLICY_V234.shadow_extension_similarity_threshold
    )
    semantic_or_similarity_collision = bool(
        semantic_core_equivalents or similar_existing_slugs
    )
    any_registration_collision = (
        any_registration_collision or semantic_or_similarity_collision
    )
    if (
        semantic_core_equivalents
        and draft.implementation_mode
        is not RegistrationImplementationModeV234.DUPLICATE_EXISTING
    ):
        _add_error(
            errors,
            "CORE_SEMANTIC_EQUIVALENCE",
            "Draft normalized DNF is semantically equivalent to a frozen core mechanism.",
        )
    if core_absorbed_controls:
        _add_error(
            errors,
            "CORE_CONTROL_ABSORPTION",
            "A draft clause is satisfied by a frozen core mechanism control clause.",
        )
    if (
        similar_existing_slugs
        and draft.implementation_mode
        is not RegistrationImplementationModeV234.DUPLICATE_EXISTING
    ):
        _add_error(
            errors,
            "SHADOW_EXTENSION_SIMILARITY_DUPLICATE",
            "Draft matches an existing Shadow or extension slug above the frozen threshold.",
        )

    if draft.implementation_mode is RegistrationImplementationModeV234.DUPLICATE_EXISTING:
        if not any_registration_collision:
            _add_error(
                errors,
                "DUPLICATE_CLASSIFICATION_UNBOUND",
                "Duplicate classification does not bind an existing core mechanism.",
            )
        status = (
            DraftValidationStatusV234.INVALID
            if errors
            else DraftValidationStatusV234.NON_REGISTRABLE
        )
    elif (
        draft.implementation_mode
        is RegistrationImplementationModeV234.INSUFFICIENT_EVIDENCE
    ):
        accepted_sources = {
            anomaly.source
            for item in accepted_reports
            for anomaly in item.residual_anomalies
        }
        if len(accepted_sources) >= 2:
            _add_error(
                errors,
                "INSUFFICIENT_CLASSIFICATION_UNBOUND",
                "Insufficient-evidence classification conflicts with multi-source accepted evidence.",
            )
        status = (
            DraftValidationStatusV234.INVALID
            if errors
            else DraftValidationStatusV234.NON_REGISTRABLE
        )
    elif (
        draft.implementation_mode
        is RegistrationImplementationModeV234.ENGINEERING_REQUIRED
    ):
        status = (
            DraftValidationStatusV234.INVALID
            if errors
            else DraftValidationStatusV234.ENGINEERING_REQUIRED
        )
        warnings.add("AUTOMATIC_PROMOTION_FORBIDDEN")
    else:
        status = (
            DraftValidationStatusV234.INVALID
            if errors
            else DraftValidationStatusV234.VALID
        )
    sorted_codes = tuple(sorted(errors))
    payload: dict[str, Any] = {
        "schema_version": "dta-v234.registration-draft-validation.v1",
        "draft_id": draft.draft_id,
        "draft_sha256": draft.draft_sha256,
        "core_ontology_snapshot_sha256": snapshot.snapshot_sha256,
        "duplicate_absorption_policy_sha256": (
            FROZEN_DUPLICATE_ABSORPTION_POLICY_V234.policy_sha256
        ),
        "status": status,
        "classification": draft.implementation_mode,
        "error_codes": sorted_codes,
        "errors": tuple(errors[code] for code in sorted_codes),
        "warning_codes": tuple(sorted(warnings)),
    }
    return hashed_model_v234(
        RegistrationDraftValidationV234,
        payload,
        "validation_sha256",
    )


__all__ = (
    "DraftValidationStatusV234",
    "RegistrationDraftValidationV234",
    "validate_registration_draft_v234",
)
