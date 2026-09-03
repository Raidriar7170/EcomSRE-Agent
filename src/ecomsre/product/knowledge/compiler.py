"""Product adapter into the existing bounded v2.3.4 extension runtime."""

from __future__ import annotations

import re

from ecomsre.dta_v2.v22.memory import PredicateKindV22
from ecomsre.dta_v2.v22.predicates import RequirementServiceBindingV22
from ecomsre.dta_v2.v22.read_contracts import EvidenceSourceV22, semantic_sha256_v22
from ecomsre.dta_v2.v23.contracts import ProvisionalFaultDomainV23
from ecomsre.dta_v2.v23.core_ontology_snapshot_v234 import (
    build_core_ontology_schema_snapshot_v234,
)
from ecomsre.dta_v2.v23.generic_anomalies import GenericAnomalyKindV23
from ecomsre.dta_v2.v23.registration_compiler_v234 import (
    CompiledFaultRegistrationV234,
    ExtensionPredicateDefinitionV234,
    ExtensionSupportClauseV234,
)
from ecomsre.dta_v2.v23.registration_contracts_v234 import (
    CorePredicateReferenceRuleV234,
    ExtensionPredicateRuleV234,
    GenericAnomalyKindRuleV234,
    MechanismProposalV234,
    PredicateImplementationModeV234,
    PredicateRequirementDraftV234,
    RegistrationTestPlanV234,
    hashed_model_v234,
    mechanism_distinguishing_summary_v234,
    mechanism_display_name_v234,
    mechanism_human_definition_v234,
    predicate_semantic_definition_v234,
    support_clause_rationale_v234,
)
from ecomsre.product.knowledge.contracts import (
    CandidateClauseV1,
    FamilyRegistrationDraftV1,
    ProductRegistrationValidationV1,
    ShadowEvaluationV1,
    ShadowEvaluationStratumV1,
)


_ANOMALY_SOURCE = {
    GenericAnomalyKindV23.METRIC_QUEUE_LAG_OUTLIER: EvidenceSourceV22.METRICS,
    GenericAnomalyKindV23.METRIC_ERROR_OUTLIER: EvidenceSourceV22.METRICS,
    GenericAnomalyKindV23.METRIC_LATENCY_OUTLIER: EvidenceSourceV22.METRICS,
    GenericAnomalyKindV23.RUNTIME_NOT_RUNNING: EvidenceSourceV22.RUNTIME,
    GenericAnomalyKindV23.RUNTIME_UNHEALTHY: EvidenceSourceV22.RUNTIME,
    GenericAnomalyKindV23.RUNTIME_RESTART_ANOMALY: EvidenceSourceV22.RUNTIME,
    GenericAnomalyKindV23.RESOURCE_CPU_OUTLIER: EvidenceSourceV22.RESOURCES,
    GenericAnomalyKindV23.RESOURCE_MEMORY_TREND: EvidenceSourceV22.RESOURCES,
    GenericAnomalyKindV23.TRACE_ERROR_LOCALIZATION: EvidenceSourceV22.TRACES,
    GenericAnomalyKindV23.TRACE_LATENCY_OUTLIER: EvidenceSourceV22.TRACES,
    GenericAnomalyKindV23.LOG_ERROR_CLUSTER: EvidenceSourceV22.LOGS,
    GenericAnomalyKindV23.LOG_UNKNOWN_ERROR_PATTERN: EvidenceSourceV22.LOGS,
    GenericAnomalyKindV23.RECENT_CHANGE_CORRELATION: EvidenceSourceV22.CHANGES,
    GenericAnomalyKindV23.SOURCE_COVERAGE_GAP: EvidenceSourceV22.RUNTIME,
}
_CORE_SOURCE = {
    PredicateKindV22.RUNTIME_HEALTHY: EvidenceSourceV22.RUNTIME,
    PredicateKindV22.RUNTIME_NOT_RUNNING: EvidenceSourceV22.RUNTIME,
    PredicateKindV22.RUNTIME_UNHEALTHY: EvidenceSourceV22.RUNTIME,
    PredicateKindV22.RUNTIME_RESTART_PRESSURE: EvidenceSourceV22.RUNTIME,
    PredicateKindV22.METRIC_ERROR_RATE_STRONG: EvidenceSourceV22.METRICS,
    PredicateKindV22.METRIC_LATENCY_STRONG: EvidenceSourceV22.METRICS,
    PredicateKindV22.TRACE_FIRST_ERROR: EvidenceSourceV22.TRACES,
    PredicateKindV22.TRACE_DEPENDENCY_LATENCY: EvidenceSourceV22.TRACES,
    PredicateKindV22.RESOURCE_CPU_STRONG: EvidenceSourceV22.RESOURCES,
    PredicateKindV22.RESOURCE_MEMORY_GROWTH_STRONG: EvidenceSourceV22.RESOURCES,
    PredicateKindV22.LOG_CONFIGURATION_ERROR: EvidenceSourceV22.LOGS,
    PredicateKindV22.LOG_DEPENDENCY_TIMEOUT: EvidenceSourceV22.LOGS,
    PredicateKindV22.LOG_MEMORY_PRESSURE: EvidenceSourceV22.LOGS,
    PredicateKindV22.CHANGE_RECENT_ROLLOUT: EvidenceSourceV22.CHANGES,
}


def _slug(value: str) -> str:
    result = re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")
    return result[:80] or "observed-family"


def _test_reference(value: str) -> str:
    return re.sub(r"[^a-z0-9-]+", "-", value.casefold()).strip("-")[:120]


def _predicate_parts(
    predicate_id: str,
) -> tuple[
    PredicateImplementationModeV234,
    EvidenceSourceV22,
    ExtensionPredicateRuleV234,
]:
    namespace, value = predicate_id.split(":", 1)
    if namespace == "ga":
        anomaly_kind = GenericAnomalyKindV23(value)
        return (
            PredicateImplementationModeV234.DECLARATIVE_EXTENSION_PREDICATE,
            _ANOMALY_SOURCE[anomaly_kind],
            GenericAnomalyKindRuleV234(
                kind="GENERIC_ANOMALY_KIND", anomaly_kind=anomaly_kind
            ),
        )
    if namespace == "core":
        core_kind = PredicateKindV22(value)
        return (
            PredicateImplementationModeV234.REUSE_CORE_PREDICATE,
            _CORE_SOURCE[core_kind],
            CorePredicateReferenceRuleV234(
                kind="CORE_PREDICATE_REFERENCE", predicate_kind=core_kind
            ),
        )
    raise ValueError("candidate predicate cannot be compiled by the bounded DSL")


def _compiled_payload_v1(
    *,
    draft: FamilyRegistrationDraftV1,
    selected: CandidateClauseV1,
    source_validation_sha256: str,
    test_plan: RegistrationTestPlanV234,
) -> CompiledFaultRegistrationV234:
    mechanism_slug = _slug(draft.human_canonical_label)
    enum_name = mechanism_slug.upper().replace("-", "_")
    mechanism = MechanismProposalV234(
        mechanism_enum_name=enum_name,
        mechanism_slug=mechanism_slug,
        display_name=mechanism_display_name_v234(enum_name),
        broad_fault_domain=ProvisionalFaultDomainV23(draft.broad_domain),
        human_definition=mechanism_human_definition_v234(mechanism_slug),
        distinguishing_summary=mechanism_distinguishing_summary_v234(mechanism_slug),
        confusable_core_mechanisms=(),
        confusable_extension_mechanisms=(),
    )
    definitions = []
    requirements = []
    for index, predicate_id in enumerate(selected.predicate_ids, start=1):
        mode, source, rule = _predicate_parts(predicate_id)
        predicate_slug = f"{mechanism_slug}-signal-{index}"
        predicate_name = f"{enum_name}_SIGNAL_{index}"
        definitions.append(
            hashed_model_v234(
                ExtensionPredicateDefinitionV234,
                {
                    "schema_version": "dta-v234.extension-predicate-definition.v1",
                    "predicate_name": predicate_name,
                    "predicate_slug": predicate_slug,
                    "implementation_mode": mode,
                    "evidence_source": source,
                    "service_binding": RequirementServiceBindingV22.TARGET,
                    "require_exact_parent": False,
                    "semantic_definition": predicate_semantic_definition_v234(predicate_slug),
                    "extraction_rule": rule,
                    "threshold_rule": None,
                },
                "predicate_sha256",
            )
        )
        requirements.append(
            PredicateRequirementDraftV234(
                predicate_name=predicate_name,
                service_binding=RequirementServiceBindingV22.TARGET,
                require_exact_parent=False,
            )
        )
    clause = hashed_model_v234(
        ExtensionSupportClauseV234,
        {
            "schema_version": "dta-v234.extension-support-clause.v1",
            "clause_id": f"{mechanism_slug}:primary",
            "mechanism_slug": mechanism_slug,
            "requirements": tuple(sorted(requirements, key=lambda item: item.predicate_name)),
            "rationale": support_clause_rationale_v234(),
        },
        "clause_sha256",
    )
    snapshot = build_core_ontology_schema_snapshot_v234()
    registration_id = "registration-v234-" + semantic_sha256_v22(
        {
            "source_draft_sha256": draft.draft_sha256,
            "source_validation_sha256": source_validation_sha256,
            "mechanism_slug": mechanism_slug,
        }
    )[:16]
    payload = {
        "schema_version": "dta-v234.compiled-fault-registration.v1",
        "registration_id": registration_id,
        "source_draft_id": draft.registration_id,
        "source_draft_sha256": draft.draft_sha256,
        "source_validation_sha256": source_validation_sha256,
        "core_ontology_snapshot_sha256": snapshot.snapshot_sha256,
        "implementation_mode": "DECLARATIVE_READY",
        "mechanism": mechanism,
        "predicates": tuple(definitions),
        "support_clauses": (clause,),
        "test_plan": test_plan,
        "remediation_registration": "NOT_INCLUDED",
        "action_authority": "NONE",
        "repository_write_authority": "NONE",
    }
    return hashed_model_v234(
        CompiledFaultRegistrationV234,
        payload,
        "compiled_sha256",
    )


def build_product_shadow_candidate_v1(
    *,
    draft: FamilyRegistrationDraftV1,
    selected: CandidateClauseV1,
) -> CompiledFaultRegistrationV234:
    """Compile the exact bounded DSL candidate used by the runtime shadow."""

    source_validation_sha256 = semantic_sha256_v22(
        {
            "schema_version": "ecomsre.product.shadow-candidate-binding.v1",
            "draft_sha256": draft.draft_sha256,
            "selected_candidate_id": selected.candidate_id,
            "selected_predicate_ids": selected.predicate_ids,
        }
    )
    test_plan = RegistrationTestPlanV234(
        positive_report_ids=draft.positive_incident_ids,
        positive_case_ids=tuple(
            f"shadow-positive:{incident_id}"
            for incident_id in draft.positive_incident_ids
        ),
        confusable_core_mechanisms=(),
        required_known_controls=tuple(
            f"shadow-control-{incident_id}"
            for incident_id in draft.negative_incident_ids
        ),
        required_no_incident_controls=("shadow-no-incident-runtime-derived",),
        required_counterfactuals=("shadow-counterfactual-runtime-derived",),
        required_source_failure_tests=("shadow-source-failure-runtime-derived",),
        required_clause_binding_tests=tuple(
            f"shadow-clause-binding-{incident_id}"
            for incident_id in draft.positive_incident_ids
        ),
    )
    return _compiled_payload_v1(
        draft=draft,
        selected=selected,
        source_validation_sha256=source_validation_sha256,
        test_plan=test_plan,
    )


def compile_product_registration_v1(
    *,
    draft: FamilyRegistrationDraftV1,
    selected: CandidateClauseV1,
    shadow: ShadowEvaluationV1,
) -> tuple[CompiledFaultRegistrationV234, ProductRegistrationValidationV1]:
    if not shadow.gate_passed or shadow.registration_id != draft.registration_id:
        raise ValueError("Product compilation requires the bound passing shadow result")
    snapshot = build_core_ontology_schema_snapshot_v234()
    validation_payload = {
        "schema_version": "ecomsre.product.registration-validation.v1",
        "registration_id": draft.registration_id,
        "draft_sha256": draft.draft_sha256,
        "predicate_matrix_sha256": draft.predicate_matrix_sha256,
        "shadow_evaluation_sha256": shadow.evaluation_sha256,
        "core_ontology_snapshot_sha256": snapshot.snapshot_sha256,
        "selected_candidate_id": selected.candidate_id,
        "selected_predicate_ids": selected.predicate_ids,
        "status": "VALID",
        "error_codes": (),
        "action_authority": "NONE",
    }
    validation = ProductRegistrationValidationV1.model_validate(
        {
            **validation_payload,
            "validation_sha256": semantic_sha256_v22(validation_payload),
        }
    )
    by_stratum = {
        stratum: tuple(
            item.case_id for item in shadow.outcomes if item.stratum is stratum
        )
        for stratum in ShadowEvaluationStratumV1
    }
    test_plan = RegistrationTestPlanV234(
        positive_report_ids=draft.positive_incident_ids,
        positive_case_ids=by_stratum[ShadowEvaluationStratumV1.POSITIVE_INCIDENT],
        confusable_core_mechanisms=(),
        required_known_controls=tuple(
            sorted(
                _test_reference(item)
                for item in (
                    by_stratum[ShadowEvaluationStratumV1.CONFUSABLE_CORE_KNOWN]
                    + by_stratum[ShadowEvaluationStratumV1.OTHER_EXTENSION]
                    + by_stratum[ShadowEvaluationStratumV1.INSUFFICIENT_OR_CONFLICT]
                )
            )
        ),
        required_no_incident_controls=tuple(
            _test_reference(item)
            for item in by_stratum[ShadowEvaluationStratumV1.NO_INCIDENT]
        ),
        required_counterfactuals=tuple(
            _test_reference(item)
            for item in by_stratum[ShadowEvaluationStratumV1.TARGET_COUNTERFACTUAL]
        ),
        required_source_failure_tests=tuple(
            _test_reference(item)
            for item in by_stratum[ShadowEvaluationStratumV1.SOURCE_FAILURE]
        ),
        required_clause_binding_tests=tuple(
            _test_reference(item)
            for item in by_stratum[ShadowEvaluationStratumV1.POSITIVE_INCIDENT]
        ),
    )
    return (
        _compiled_payload_v1(
            draft=draft,
            selected=selected,
            source_validation_sha256=validation.validation_sha256,
            test_plan=test_plan,
        ),
        validation,
    )


__all__ = (
    "build_product_shadow_candidate_v1",
    "compile_product_registration_v1",
)
