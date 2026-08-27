"""Deterministic final-draft assembly and contextual validation for v2.3.4.1."""

from __future__ import annotations

from enum import Enum
import re
from typing import Any, Literal

from pydantic import Field, StrictBool, model_validator

from ecomsre.dta_v2.v22.read_contracts import DtaModelV22, semantic_sha256_v22
from ecomsre.dta_v2.v22.predicates import RequirementServiceBindingV22
from ecomsre.dta_v2.v23.ontology_expansion_v234 import (
    DraftGenerationAuthorizationResultV234,
)
from ecomsre.dta_v2.v23.registration_alias_provider_v2341 import (
    RegistrationAliasProviderResultV2341,
)
from ecomsre.dta_v2.v23.registration_catalog_v2341 import (
    RegistrationOptionCatalogV2341,
)
from ecomsre.dta_v2.v23.registration_contracts_v234 import (
    FormalFaultRegistrationDraftV234,
    FormalPredicateDraftV234,
    MechanismProposalV234,
    PredicateImplementationModeV234,
    RegistrationDraftContentV234,
    RegistrationImplementationModeV234,
    RegistrationTestPlanV234,
    SupportClauseDraftV234,
    build_alias_provider_trace_v2341,
    build_formal_registration_draft_v234,
    mechanism_display_name_v234,
    mechanism_distinguishing_summary_v234,
    mechanism_human_definition_v234,
    predicate_negative_example_v234,
    predicate_positive_example_v234,
    predicate_semantic_definition_v234,
    provider_authored_content_sha256_v234,
)
from ecomsre.dta_v2.v23.registration_validator_v234 import (
    DraftValidationStatusV234,
    RegistrationDraftValidationV234,
    validate_registration_draft_v234,
)
from ecomsre.dta_v2.v23.review_registry import ReviewQueueItemV23, ShadowFaultEntryV23


class RegistrationValidationContextV2341(str, Enum):
    PRODUCTION_REGISTRATION = "PRODUCTION_REGISTRATION"
    HIDDEN_KNOWN_RECONSTRUCTION = "HIDDEN_KNOWN_RECONSTRUCTION"
    SMOKE_ROLE_VALIDATION = "SMOKE_ROLE_VALIDATION"


class FormalRegistrationAssemblyV2341(DtaModelV22):
    schema_version: Literal["dta-v2341.formal-registration-assembly.v1"]
    catalog_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    alias_provider_result_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    validation_context: RegistrationValidationContextV2341
    formal_draft: FormalFaultRegistrationDraftV234
    canonical_order_failures: Literal[0]
    action_authority: Literal["NONE"]
    repository_write_authority: Literal["NONE"]
    remediation_registration: Literal["NOT_INCLUDED"]
    assembly_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_assembly(self) -> "FormalRegistrationAssemblyV2341":
        if self.formal_draft.action_authority != "NONE":
            raise ValueError("assembly carries action authority")
        if self.formal_draft.repository_write_authority != "NONE":
            raise ValueError("assembly carries repository-write authority")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"assembly_sha256"})
        )
        if self.assembly_sha256 != expected:
            raise ValueError("formal registration assembly digest differs")
        return self


class RegistrationContextualValidationV2341(DtaModelV22):
    schema_version: Literal["dta-v2341.contextual-registration-validation.v1"]
    context: RegistrationValidationContextV2341
    production_validation: RegistrationDraftValidationV234
    reconstruction_valid: StrictBool
    promotion_eligible: StrictBool
    context_pass: StrictBool
    collision_evidence_codes: tuple[str, ...]
    contextual_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_context(self) -> "RegistrationContextualValidationV2341":
        if self.collision_evidence_codes != tuple(
            sorted(set(self.collision_evidence_codes))
        ):
            raise ValueError("contextual collision codes are not canonical")
        if self.promotion_eligible and (
            self.context is not RegistrationValidationContextV2341.PRODUCTION_REGISTRATION
            or self.production_validation.status is not DraftValidationStatusV234.VALID
        ):
            raise ValueError("contextual validation grants invalid promotion eligibility")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"contextual_sha256"})
        )
        if self.contextual_sha256 != expected:
            raise ValueError("contextual registration validation digest differs")
        return self


def _mechanism_slug_v2341(
    *,
    context: RegistrationValidationContextV2341,
    human_label: str,
    mechanism_concept: str,
) -> str:
    source = (
        human_label
        if context is RegistrationValidationContextV2341.PRODUCTION_REGISTRATION
        else mechanism_concept
    )
    tokens = re.findall(r"[a-z0-9]+", source.casefold())[:8]
    if not tokens:
        raise ValueError("mechanism naming lacks a safe semantic token")
    return "-".join(tokens)


def _selected_options_v2341(
    *,
    catalog: RegistrationOptionCatalogV2341,
    provider_result: RegistrationAliasProviderResultV2341,
) -> tuple[
    RegistrationImplementationModeV234,
    tuple[FormalPredicateDraftV234, ...],
    tuple[Any, ...],
    tuple[Any, ...],
    tuple[Any, ...],
]:
    selection = provider_result.selection
    disposition = next(
        item.disposition
        for item in catalog.disposition_options
        if item.disposition_alias == selection.disposition_alias
    )
    clause_by_alias = {item.clause_alias: item for item in catalog.clause_options}
    selected_clauses = tuple(
        clause_by_alias[alias] for alias in selection.clause_aliases
    )
    selected_predicate_aliases = {
        alias for clause in selected_clauses for alias in clause.predicate_aliases
    }
    predicates = tuple(
        sorted(
            (
                item.draft
                for item in catalog.predicate_options
                if item.predicate_alias in selected_predicate_aliases
            ),
            key=lambda item: item.predicate_name,
        )
    )
    confusable_by_alias = {
        item.confusable_alias: item for item in catalog.confusable_options
    }
    selected_confusables = tuple(
        confusable_by_alias[alias] for alias in selection.confusable_aliases
    )
    gap_by_alias = {
        item.engineering_gap_alias: item
        for item in catalog.engineering_gap_options
    }
    selected_gaps = tuple(
        gap_by_alias[alias] for alias in selection.engineering_gap_aliases
    )
    return disposition, predicates, selected_clauses, selected_confusables, selected_gaps


def assemble_formal_registration_draft_v2341(
    *,
    authorization_context: DraftGenerationAuthorizationResultV234,
    shadow: ShadowFaultEntryV23,
    accepted_reports: tuple[ReviewQueueItemV23, ...],
    catalog: RegistrationOptionCatalogV2341,
    provider_result: RegistrationAliasProviderResultV2341,
    validation_context: RegistrationValidationContextV2341,
) -> FormalRegistrationAssemblyV2341:
    if catalog.authorization_id != authorization_context.authorization.authorization_id:
        raise ValueError("catalog differs from authorization context")
    if (
        provider_result.authorization_id != catalog.authorization_id
        or provider_result.catalog_sha256 != catalog.catalog_sha256
    ):
        raise ValueError("alias Provider result differs from catalog")
    if catalog.human_canonical_label != shadow.canonical_label:
        raise ValueError("catalog differs from human canonical label")
    (
        disposition,
        selected_predicates,
        selected_clause_options,
        selected_confusables,
        selected_gaps,
    ) = _selected_options_v2341(catalog=catalog, provider_result=provider_result)
    slug = _mechanism_slug_v2341(
        context=validation_context,
        human_label=shadow.canonical_label,
        mechanism_concept=provider_result.selection.mechanism_concept,
    )
    enum_name = slug.upper().replace("-", "_")
    core_confusables = tuple(
        sorted(
            {
                item.core_mechanism
                for item in selected_confusables
                if item.core_mechanism is not None
            },
            key=lambda item: item.value,
        )
    )
    extension_confusables = tuple(
        sorted(
            {
                item.extension_mechanism_slug
                for item in selected_confusables
                if item.extension_mechanism_slug is not None
            }
        )
    )
    mechanism = MechanismProposalV234(
        mechanism_enum_name=enum_name,
        mechanism_slug=slug,
        display_name=mechanism_display_name_v234(enum_name),
        broad_fault_domain=catalog.broad_fault_domain,
        human_definition=mechanism_human_definition_v234(slug),
        distinguishing_summary=mechanism_distinguishing_summary_v234(slug),
        confusable_core_mechanisms=core_confusables,
        confusable_extension_mechanisms=extension_confusables,
    )
    gap_predicates = tuple(
        FormalPredicateDraftV234(
            predicate_name=f"{item.gap_slug.upper().replace('-', '_')}_SIGNAL",
            predicate_slug=f"{item.gap_slug}-signal",
            implementation_mode=(
                PredicateImplementationModeV234.REQUIRES_CODE_IMPLEMENTATION
            ),
            evidence_source=item.evidence_source,
            service_binding=(
                selected_predicates[0].service_binding
                if selected_predicates
                else RequirementServiceBindingV22.TARGET
            ),
            require_exact_parent=False,
            semantic_definition=predicate_semantic_definition_v234(
                f"{item.gap_slug}-signal"
            ),
            extraction_rule=None,
            threshold_rule=None,
            supporting_report_evidence_refs=item.supporting_evidence_refs,
            positive_examples=(
                predicate_positive_example_v234(f"{item.gap_slug}-signal"),
            ),
            negative_examples=(
                predicate_negative_example_v234(f"{item.gap_slug}-signal"),
            ),
        )
        for item in selected_gaps
    )
    predicates = tuple(
        sorted(
            {item.predicate_name: item for item in (*selected_predicates, *gap_predicates)}.values(),
            key=lambda item: item.predicate_name,
        )
    )
    clauses = tuple(
        SupportClauseDraftV234(
            clause_id=f"{slug}:clause-{ordinal:02d}",
            mechanism_slug=slug,
            requirements=option.requirements,
            rationale=option.rationale,
        )
        for ordinal, option in enumerate(selected_clause_options, start=1)
    )
    reports = tuple(sorted(item.report.report_id for item in accepted_reports))
    cases = tuple(sorted(item.source_case_id for item in accepted_reports))
    known_controls = tuple(
        sorted(
            {
                f"known-{item.value.casefold().replace('_', '-')}-control"
                for item in core_confusables
            }
        )
    ) or ("known-core-control",)
    selected_sources = {item.evidence_source for item in predicates}
    source_failures = tuple(
        sorted(f"{source.value.casefold()}-unavailable-fails-closed" for source in selected_sources)
    ) or ("accepted-source-unavailable-fails-closed",)
    clause_tests = tuple(
        f"clause-{ordinal:02d}-binding-control"
        for ordinal in range(1, max(len(clauses), 1) + 1)
    )
    counterfactuals = tuple(
        f"clause-{ordinal:02d}-target-counterfactual"
        for ordinal in range(1, max(len(clauses), 1) + 1)
    )
    test_plan = RegistrationTestPlanV234(
        positive_report_ids=reports,
        positive_case_ids=cases,
        confusable_core_mechanisms=core_confusables,
        required_known_controls=known_controls,
        required_no_incident_controls=(
            f"no-incident-{catalog.broad_fault_domain.value.casefold()}-control",
        ),
        required_counterfactuals=counterfactuals,
        required_source_failure_tests=source_failures,
        required_clause_binding_tests=clause_tests,
    )
    content = RegistrationDraftContentV234(
        implementation_mode=disposition,
        mechanism=mechanism,
        predicates=predicates,
        support_clauses=clauses,
        test_plan=test_plan,
        unresolved_engineering_questions=tuple(
            sorted(item.engineering_question for item in selected_gaps)
        ),
        remediation_registration="NOT_INCLUDED",
    )
    assembled_content_sha256 = provider_authored_content_sha256_v234(content)
    selection_trace = provider_result.trace
    trace = build_alias_provider_trace_v2341(
        provider_mode=selection_trace.provider_mode,
        request_sha256=selection_trace.request_sha256,
        raw_response_sha256=selection_trace.raw_response_sha256,
        canonical_selection_sha256=selection_trace.canonical_selection_sha256,
        assembled_content_sha256=assembled_content_sha256,
        provider_calls=selection_trace.provider_calls,
        protocol_repairs=selection_trace.protocol_repairs,
        transport_retries=selection_trace.transport_retries,
    )
    draft = build_formal_registration_draft_v234(
        authorization_id=authorization_context.authorization.authorization_id,
        shadow_fault_id=shadow.shadow_fault_id,
        registration_seed_sha256=authorization_context.registration_seed.seed_sha256,
        core_ontology_snapshot_sha256=(
            authorization_context.core_ontology_snapshot.snapshot_sha256
        ),
        content=content,
        provider_trace=trace,
    )
    payload: dict[str, Any] = {
        "schema_version": "dta-v2341.formal-registration-assembly.v1",
        "catalog_sha256": catalog.catalog_sha256,
        "alias_provider_result_sha256": provider_result.result_sha256,
        "validation_context": validation_context,
        "formal_draft": draft,
        "canonical_order_failures": 0,
        "action_authority": "NONE",
        "repository_write_authority": "NONE",
        "remediation_registration": "NOT_INCLUDED",
    }
    return FormalRegistrationAssemblyV2341.model_validate(
        {**payload, "assembly_sha256": semantic_sha256_v22(
            FormalRegistrationAssemblyV2341.model_construct(
                **payload, assembly_sha256="0" * 64
            ).model_dump(mode="json", exclude={"assembly_sha256"})
        )}
    )


_RECONSTRUCTION_COLLISION_CODES_V2341 = frozenset(
    {
        "CORE_MECHANISM_COLLISION",
        "CORE_SEMANTIC_EQUIVALENCE",
        "CORE_CONTROL_ABSORPTION",
    }
)


def validate_registration_draft_in_context_v2341(
    *,
    draft: FormalFaultRegistrationDraftV234,
    authorization_context: DraftGenerationAuthorizationResultV234,
    shadow: ShadowFaultEntryV23,
    accepted_reports: tuple[ReviewQueueItemV23, ...],
    context: RegistrationValidationContextV2341,
    promoted_mechanism_slugs: tuple[str, ...],
    shadow_mechanism_slugs: tuple[str, ...],
    smoke_hidden_known: bool = False,
) -> RegistrationContextualValidationV2341:
    production = validate_registration_draft_v234(
        draft=draft,
        authorization_context=authorization_context,
        shadow=shadow,
        accepted_reports=accepted_reports,
        promoted_mechanism_slugs=promoted_mechanism_slugs,
        shadow_mechanism_slugs=shadow_mechanism_slugs,
    )
    collision_codes = tuple(
        sorted(set(production.error_codes) & _RECONSTRUCTION_COLLISION_CODES_V2341)
    )
    only_reconstruction_collisions = bool(collision_codes) and set(
        production.error_codes
    ).issubset(_RECONSTRUCTION_COLLISION_CODES_V2341)
    reconstruction_context = (
        context is RegistrationValidationContextV2341.HIDDEN_KNOWN_RECONSTRUCTION
        or (
            context is RegistrationValidationContextV2341.SMOKE_ROLE_VALIDATION
            and smoke_hidden_known
        )
    )
    reconstruction_valid = reconstruction_context and (
        production.status is DraftValidationStatusV234.VALID
        or only_reconstruction_collisions
    )
    if context is RegistrationValidationContextV2341.PRODUCTION_REGISTRATION:
        context_pass = production.status is not DraftValidationStatusV234.INVALID
        promotion_eligible = production.status is DraftValidationStatusV234.VALID
    elif reconstruction_context:
        context_pass = reconstruction_valid
        promotion_eligible = False
    else:
        context_pass = production.status is not DraftValidationStatusV234.INVALID
        promotion_eligible = False
    payload: dict[str, Any] = {
        "schema_version": "dta-v2341.contextual-registration-validation.v1",
        "context": context,
        "production_validation": production,
        "reconstruction_valid": reconstruction_valid,
        "promotion_eligible": promotion_eligible,
        "context_pass": context_pass,
        "collision_evidence_codes": collision_codes,
    }
    return RegistrationContextualValidationV2341.model_validate(
        {**payload, "contextual_sha256": semantic_sha256_v22(
            RegistrationContextualValidationV2341.model_construct(
                **payload, contextual_sha256="0" * 64
            ).model_dump(mode="json", exclude={"contextual_sha256"})
        )}
    )


__all__ = (
    "FormalRegistrationAssemblyV2341",
    "RegistrationContextualValidationV2341",
    "RegistrationValidationContextV2341",
    "assemble_formal_registration_draft_v2341",
    "validate_registration_draft_in_context_v2341",
)
