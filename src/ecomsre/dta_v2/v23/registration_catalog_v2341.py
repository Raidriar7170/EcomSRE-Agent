"""Runtime-owned registration option catalog for DTA v2.3.4.1.

The catalog contains the complete mechanical predicate and clause objects.  Its
Provider projection intentionally exposes only opaque aliases and short
semantic summaries.
"""

from __future__ import annotations

from enum import Enum
from itertools import combinations
from typing import Any, Literal

from pydantic import Field, StrictBool, StrictInt, model_validator

from ecomsre.dta_v2.v22.memory import PredicateKindV22
from ecomsre.dta_v2.v22.predicates import (
    MechanismV22,
    RequirementServiceBindingV22,
)
from ecomsre.dta_v2.v22.read_contracts import (
    DtaModelV22,
    EvidenceSourceV22,
    semantic_sha256_v22,
)
from ecomsre.dta_v2.v23.contracts import ProvisionalFaultDomainV23
from ecomsre.dta_v2.v23.core_ontology_snapshot_v234 import (
    CoreOntologySchemaSnapshotV234,
    build_core_ontology_schema_snapshot_v234,
)
from ecomsre.dta_v2.v23.generic_anomalies import GenericAnomalyKindV23
from ecomsre.dta_v2.v23.registration_contracts_v234 import (
    CorePredicateReferenceRuleV234,
    FormalPredicateDraftV234,
    GenericAnomalyKindRuleV234,
    PredicateImplementationModeV234,
    PredicateRequirementDraftV234,
    RegistrationImplementationModeV234,
    predicate_negative_example_v234,
    predicate_positive_example_v234,
    predicate_semantic_definition_v234,
    support_clause_rationale_v234,
)
from ecomsre.dta_v2.v23.registration_provider_v234 import (
    RegistrationDraftProviderRequestV234,
)


_CORE_KIND_BY_ANOMALY_V2341: dict[GenericAnomalyKindV23, PredicateKindV22] = {
    GenericAnomalyKindV23.METRIC_ERROR_OUTLIER: (
        PredicateKindV22.METRIC_ERROR_RATE_STRONG
    ),
    GenericAnomalyKindV23.METRIC_LATENCY_OUTLIER: (
        PredicateKindV22.METRIC_LATENCY_STRONG
    ),
    GenericAnomalyKindV23.RUNTIME_NOT_RUNNING: PredicateKindV22.RUNTIME_NOT_RUNNING,
    GenericAnomalyKindV23.RUNTIME_UNHEALTHY: PredicateKindV22.RUNTIME_UNHEALTHY,
    GenericAnomalyKindV23.RUNTIME_RESTART_ANOMALY: (
        PredicateKindV22.RUNTIME_RESTART_PRESSURE
    ),
    GenericAnomalyKindV23.RESOURCE_CPU_OUTLIER: PredicateKindV22.RESOURCE_CPU_STRONG,
    GenericAnomalyKindV23.RESOURCE_MEMORY_TREND: (
        PredicateKindV22.RESOURCE_MEMORY_GROWTH_STRONG
    ),
    GenericAnomalyKindV23.TRACE_ERROR_LOCALIZATION: PredicateKindV22.TRACE_FIRST_ERROR,
    GenericAnomalyKindV23.TRACE_LATENCY_OUTLIER: (
        PredicateKindV22.TRACE_DEPENDENCY_LATENCY
    ),
    GenericAnomalyKindV23.RECENT_CHANGE_CORRELATION: (
        PredicateKindV22.CHANGE_RECENT_ROLLOUT
    ),
}


class CatalogFeasibilityStatusV2341(str, Enum):
    PASS = "PASS"
    CATALOG_COVERAGE_FAILURE = "CATALOG_COVERAGE_FAILURE"


class RegistrationDispositionOptionV2341(DtaModelV22):
    disposition_alias: str = Field(pattern=r"^D0[0-3]$")
    disposition: RegistrationImplementationModeV234
    semantic_summary: str = Field(min_length=1, max_length=240)


class RegistrationPredicateOptionV2341(DtaModelV22):
    predicate_alias: str = Field(pattern=r"^P[0-9]{2}$")
    draft: FormalPredicateDraftV234
    semantic_summary: str = Field(min_length=1, max_length=500)
    evidence_summary: str = Field(min_length=1, max_length=500)


class RegistrationClauseOptionV2341(DtaModelV22):
    clause_alias: str = Field(pattern=r"^C[0-9]{2}$")
    predicate_aliases: tuple[str, ...] = Field(min_length=1, max_length=3)
    requirements: tuple[PredicateRequirementDraftV234, ...] = Field(
        min_length=1,
        max_length=3,
    )
    source_count: StrictInt = Field(ge=1, le=6)
    rationale: str = Field(min_length=1, max_length=1200)
    cross_source_corroboration: StrictBool
    authoritative_single_predicate: StrictBool
    semantic_summary: str = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def require_canonical_clause(self) -> "RegistrationClauseOptionV2341":
        if self.predicate_aliases != tuple(sorted(set(self.predicate_aliases))):
            raise ValueError("registration clause predicate aliases are not canonical")
        keys = tuple(
            (
                item.predicate_name,
                item.service_binding.value,
                item.require_exact_parent,
            )
            for item in self.requirements
        )
        if keys != tuple(sorted(set(keys))):
            raise ValueError("registration clause requirements are not canonical")
        if len(self.predicate_aliases) != len(self.requirements):
            raise ValueError("registration clause alias and requirement counts differ")
        if self.authoritative_single_predicate != (len(self.requirements) == 1):
            raise ValueError("registration clause authoritative-single marker differs")
        if self.cross_source_corroboration != (self.source_count >= 2):
            raise ValueError("registration clause corroboration marker differs")
        if self.rationale != support_clause_rationale_v234():
            raise ValueError("registration clause rationale differs from Runtime template")
        return self


class RegistrationConfusableOptionV2341(DtaModelV22):
    confusable_alias: str = Field(pattern=r"^M[0-9]{2}$")
    mechanism_kind: Literal["CORE", "EXTENSION"]
    core_mechanism: MechanismV22 | None = None
    extension_mechanism_slug: str | None = None
    semantic_summary: str = Field(min_length=1, max_length=300)

    @model_validator(mode="after")
    def require_one_mechanism(self) -> "RegistrationConfusableOptionV2341":
        if (self.core_mechanism is None) == (self.extension_mechanism_slug is None):
            raise ValueError("confusable option must bind exactly one mechanism")
        if self.mechanism_kind == "CORE" and self.core_mechanism is None:
            raise ValueError("core confusable lacks a core mechanism")
        if self.mechanism_kind == "EXTENSION" and self.extension_mechanism_slug is None:
            raise ValueError("extension confusable lacks an extension mechanism")
        return self


class RegistrationEngineeringGapOptionV2341(DtaModelV22):
    engineering_gap_alias: str = Field(pattern=r"^G[0-9]{2}$")
    gap_slug: str = Field(pattern=r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")
    missing_capability: str = Field(min_length=1, max_length=300)
    evidence_source: EvidenceSourceV22
    supporting_evidence_refs: tuple[str, ...] = Field(min_length=1)
    evidence_summary: str = Field(min_length=1, max_length=500)
    engineering_question: str = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def require_canonical_gap(self) -> "RegistrationEngineeringGapOptionV2341":
        if self.supporting_evidence_refs != tuple(
            sorted(set(self.supporting_evidence_refs))
        ):
            raise ValueError("engineering gap evidence refs are not canonical")
        expected = f"Define the bounded engineering gap for {self.gap_slug}."
        if self.engineering_question != expected:
            raise ValueError("engineering gap question differs from Runtime template")
        return self


class RegistrationOptionCatalogV2341(DtaModelV22):
    schema_version: Literal["dta-v2341.registration-option-catalog.v1"]
    authorization_id: str
    registration_seed_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_request_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    human_canonical_label: str
    broad_fault_domain: ProvisionalFaultDomainV23
    disposition_options: tuple[RegistrationDispositionOptionV2341, ...]
    predicate_options: tuple[RegistrationPredicateOptionV2341, ...] = Field(
        max_length=12
    )
    clause_options: tuple[RegistrationClauseOptionV2341, ...] = Field(max_length=24)
    confusable_options: tuple[RegistrationConfusableOptionV2341, ...]
    engineering_gap_options: tuple[RegistrationEngineeringGapOptionV2341, ...]
    provider_calls: Literal[0]
    catalog_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_catalog(self) -> "RegistrationOptionCatalogV2341":
        alias_sets = (
            tuple(item.disposition_alias for item in self.disposition_options),
            tuple(item.predicate_alias for item in self.predicate_options),
            tuple(item.clause_alias for item in self.clause_options),
            tuple(item.confusable_alias for item in self.confusable_options),
            tuple(item.engineering_gap_alias for item in self.engineering_gap_options),
        )
        prefixes = ("D", "P", "C", "M", "G")
        for aliases, prefix in zip(alias_sets, prefixes, strict=True):
            if aliases != tuple(f"{prefix}{ordinal:02d}" for ordinal in range(len(aliases))):
                raise ValueError(f"registration catalog {prefix} aliases are not canonical")
        predicate_aliases = set(alias_sets[1])
        if any(
            not set(item.predicate_aliases).issubset(predicate_aliases)
            for item in self.clause_options
        ):
            raise ValueError("registration clause references an unknown predicate alias")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"catalog_sha256"})
        )
        if self.catalog_sha256 != expected:
            raise ValueError("registration option catalog digest differs")
        return self

    def provider_projection(self) -> dict[str, Any]:
        """Return the only catalog fields that may cross the Provider boundary."""

        return {
            "broad_fault_domain": self.broad_fault_domain.value,
            "dispositions": tuple(
                {
                    "alias": item.disposition_alias,
                    "summary": item.semantic_summary,
                }
                for item in self.disposition_options
            ),
            "predicates": tuple(
                {
                    "alias": item.predicate_alias,
                    "summary": item.semantic_summary,
                    "source": item.draft.evidence_source.value,
                    "implementation_mode": item.draft.implementation_mode.value,
                    "evidence_summary": item.evidence_summary,
                }
                for item in self.predicate_options
            ),
            "clauses": tuple(
                {
                    "alias": item.clause_alias,
                    "predicate_aliases": item.predicate_aliases,
                    "source_count": item.source_count,
                    "cross_source_corroboration": item.cross_source_corroboration,
                    "authoritative_single_predicate": (
                        item.authoritative_single_predicate
                    ),
                    "summary": (
                        f"Combine {len(item.predicate_aliases)} Runtime-bound evidence "
                        f"signals across {item.source_count} source(s)."
                    ),
                }
                for item in self.clause_options
            ),
            "confusables": tuple(
                {
                    "alias": item.confusable_alias,
                    "summary": item.semantic_summary,
                }
                for item in self.confusable_options
            ),
            "engineering_gaps": tuple(
                {
                    "alias": item.engineering_gap_alias,
                    "source": item.evidence_source.value,
                    "missing_capability": item.missing_capability,
                    "evidence_summary": item.evidence_summary,
                }
                for item in self.engineering_gap_options
            ),
        }


class RegistrationCatalogFeasibilityDecisionV2341(DtaModelV22):
    schema_version: Literal["dta-v2341.registration-catalog-feasibility.v1"]
    catalog_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    expected_disposition: RegistrationImplementationModeV234 | None
    evidence_supported_predicate_count: StrictInt = Field(ge=0, le=12)
    compile_valid_clause_count: StrictInt = Field(ge=0, le=24)
    engineering_gap_count: StrictInt = Field(ge=0)
    status: CatalogFeasibilityStatusV2341
    issue_codes: tuple[str, ...]
    terminal: Literal[
        "DTA_V2341_CATALOG_FEASIBILITY_PASS",
        "CATALOG_COVERAGE_FAILURE",
    ]
    decision_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_decision(self) -> "RegistrationCatalogFeasibilityDecisionV2341":
        if self.issue_codes != tuple(sorted(set(self.issue_codes))):
            raise ValueError("catalog feasibility issue codes are not canonical")
        passed = self.status is CatalogFeasibilityStatusV2341.PASS
        if passed != (self.terminal == "DTA_V2341_CATALOG_FEASIBILITY_PASS"):
            raise ValueError("catalog feasibility status and terminal differ")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"decision_sha256"})
        )
        if self.decision_sha256 != expected:
            raise ValueError("catalog feasibility digest differs")
        return self


def _predicate_from_evidence_v2341(
    *,
    anomaly_kind: GenericAnomalyKindV23,
    source: EvidenceSourceV22,
    evidence_refs: tuple[str, ...],
    visible_core_predicates: set[PredicateKindV22],
    core_service_bindings: dict[
        PredicateKindV22, tuple[RequirementServiceBindingV22, bool]
    ],
) -> FormalPredicateDraftV234:
    core_kind = _CORE_KIND_BY_ANOMALY_V2341.get(anomaly_kind)
    if core_kind is not None and core_kind in visible_core_predicates:
        name = core_kind.value
        rule: CorePredicateReferenceRuleV234 | GenericAnomalyKindRuleV234 = (
            CorePredicateReferenceRuleV234(
                kind="CORE_PREDICATE_REFERENCE",
                predicate_kind=core_kind,
            )
        )
        implementation_mode = PredicateImplementationModeV234.REUSE_CORE_PREDICATE
    else:
        name = f"{anomaly_kind.value}_SIGNAL"
        rule = GenericAnomalyKindRuleV234(
            kind="GENERIC_ANOMALY_KIND",
            anomaly_kind=anomaly_kind,
        )
        implementation_mode = (
            PredicateImplementationModeV234.DECLARATIVE_EXTENSION_PREDICATE
        )
    slug = name.casefold().replace("_", "-")
    service_binding, require_exact_parent = (
        core_service_bindings.get(
            core_kind,
            (RequirementServiceBindingV22.TARGET, False),
        )
        if core_kind is not None
        else (RequirementServiceBindingV22.TARGET, False)
    )
    return FormalPredicateDraftV234(
        predicate_name=name,
        predicate_slug=slug,
        implementation_mode=implementation_mode,
        evidence_source=source,
        service_binding=service_binding,
        require_exact_parent=require_exact_parent,
        semantic_definition=predicate_semantic_definition_v234(slug),
        extraction_rule=rule,
        threshold_rule=None,
        supporting_report_evidence_refs=evidence_refs,
        positive_examples=(predicate_positive_example_v234(slug),),
        negative_examples=(predicate_negative_example_v234(slug),),
    )


def _visible_core_signatures_v2341(
    request: RegistrationDraftProviderRequestV234,
) -> set[tuple[tuple[str, str, bool], ...]]:
    return {
        tuple(
            sorted(
                (
                    requirement.predicate_kind.value,
                    requirement.service_binding.value,
                    requirement.require_exact_parent,
                )
                for requirement in clause.requirements
            )
        )
        for clause in request.core_ontology_view.runtime_known_support_clauses
    }


def _catalog_payload_v2341(
    request: RegistrationDraftProviderRequestV234,
    *,
    core_snapshot: CoreOntologySchemaSnapshotV234,
) -> dict[str, Any]:
    disposition_options = tuple(
        RegistrationDispositionOptionV2341(
            disposition_alias=f"D{ordinal:02d}",
            disposition=disposition,
            semantic_summary=summary,
        )
        for ordinal, (disposition, summary) in enumerate(
            (
                (
                    RegistrationImplementationModeV234.DECLARATIVE_READY,
                    "Evidence supports at least one bounded declarative clause.",
                ),
                (
                    RegistrationImplementationModeV234.ENGINEERING_REQUIRED,
                    "Evidence is meaningful but a required extraction capability is absent.",
                ),
                (
                    RegistrationImplementationModeV234.DUPLICATE_EXISTING,
                    "The accepted evidence matches a visible existing registration.",
                ),
                (
                    RegistrationImplementationModeV234.INSUFFICIENT_EVIDENCE,
                    "The accepted evidence cannot support formal registration.",
                ),
            )
        )
    )
    evidence_by_kind: dict[
        tuple[GenericAnomalyKindV23, EvidenceSourceV22],
        list[tuple[str, str]],
    ] = {}
    for report in request.accepted_reports:
        for evidence in report.evidence_summaries:
            evidence_by_kind.setdefault(
                (evidence.anomaly_kind, evidence.source), []
            ).append((evidence.evidence_ref, evidence.summary))
    runtime_core_predicates = {
        item.predicate_kind for item in core_snapshot.predicate_source_bindings
    }
    core_binding_candidates: dict[
        PredicateKindV22, set[tuple[RequirementServiceBindingV22, bool]]
    ] = {}
    for clause in core_snapshot.core_support_clauses:
        for requirement in clause.requirements:
            core_binding_candidates.setdefault(requirement.predicate_kind, set()).add(
                (requirement.service_binding, requirement.require_exact_parent)
            )
    core_service_bindings = {
        kind: next(iter(bindings))
        for kind, bindings in core_binding_candidates.items()
        if len(bindings) == 1
    }
    predicate_candidates: list[tuple[FormalPredicateDraftV234, str, str]] = []
    for (kind, source), values in sorted(
        evidence_by_kind.items(), key=lambda item: (item[0][0].value, item[0][1].value)
    ):
        refs = tuple(sorted({ref for ref, _summary in values}))
        summaries = tuple(sorted({summary for _ref, summary in values}))
        draft = _predicate_from_evidence_v2341(
            anomaly_kind=kind,
            source=source,
            evidence_refs=refs,
            visible_core_predicates=runtime_core_predicates,
            core_service_bindings=core_service_bindings,
        )
        predicate_candidates.append(
            (
                draft,
                f"{kind.value.replace('_', ' ').title()} at the accepted root service.",
                " ".join(summaries)[:500],
            )
        )
    predicate_candidates.sort(key=lambda item: item[0].predicate_name)
    predicate_options = tuple(
        RegistrationPredicateOptionV2341(
            predicate_alias=f"P{ordinal:02d}",
            draft=draft,
            semantic_summary=summary,
            evidence_summary=evidence_summary,
        )
        for ordinal, (draft, summary, evidence_summary) in enumerate(
            predicate_candidates[:12]
        )
    )
    source_by_alias = {
        item.predicate_alias: item.draft.evidence_source for item in predicate_options
    }
    requirement_by_alias = {
        item.predicate_alias: PredicateRequirementDraftV234(
            predicate_name=item.draft.predicate_name,
            service_binding=item.draft.service_binding,
            require_exact_parent=item.draft.require_exact_parent,
        )
        for item in predicate_options
    }
    core_kind_by_name = {item.value: item for item in PredicateKindV22}
    allowlist = set(
        request.core_ontology_view.authoritative_single_predicate_allowlist
    )
    visible_signatures = _visible_core_signatures_v2341(request)
    clause_candidates: list[
        tuple[int, int, tuple[str, ...], tuple[PredicateRequirementDraftV234, ...]]
    ] = []
    aliases = tuple(requirement_by_alias)
    for size in (1, 2, 3):
        for selected in combinations(aliases, size):
            requirements = tuple(
                sorted(
                    (requirement_by_alias[alias] for alias in selected),
                    key=lambda item: (
                        item.predicate_name,
                        item.service_binding.value,
                        item.require_exact_parent,
                    ),
                )
            )
            if size == 1:
                core_kind = core_kind_by_name.get(requirements[0].predicate_name)
                if core_kind not in allowlist:
                    continue
            signature = tuple(
                (
                    requirement.predicate_name,
                    requirement.service_binding.value,
                    requirement.require_exact_parent,
                )
                for requirement in requirements
            )
            if signature in visible_signatures:
                continue
            source_count = len({source_by_alias[alias] for alias in selected})
            if size > 1 and source_count < 2:
                continue
            evidence_count = sum(
                len(
                    next(
                        item.draft.supporting_report_evidence_refs
                        for item in predicate_options
                        if item.predicate_alias == alias
                    )
                )
                for alias in selected
            )
            clause_candidates.append(
                (-source_count, -evidence_count, tuple(sorted(selected)), requirements)
            )
    clause_candidates.sort(key=lambda item: (item[0], item[1], item[2]))
    clause_options = tuple(
        RegistrationClauseOptionV2341(
            clause_alias=f"C{ordinal:02d}",
            predicate_aliases=selected,
            requirements=requirements,
            source_count=-source_rank,
            rationale=support_clause_rationale_v234(),
            cross_source_corroboration=(-source_rank >= 2),
            authoritative_single_predicate=(len(requirements) == 1),
            semantic_summary=(
                "Require "
                + " and ".join(
                    requirement.predicate_name.replace("_", " ").casefold()
                    for requirement in requirements
                )
                + " at their Runtime-bound services."
            ),
        )
        for ordinal, (source_rank, _evidence_rank, selected, requirements) in enumerate(
            clause_candidates[:24]
        )
    )
    visible_mechanisms = tuple(
        mechanism
        for mechanism in request.core_ontology_view.runtime_known_mechanisms
        if mechanism not in {MechanismV22.NO_INCIDENT, MechanismV22.UNKNOWN}
    )
    confusable_options = tuple(
        RegistrationConfusableOptionV2341(
            confusable_alias=f"M{ordinal:02d}",
            mechanism_kind="CORE",
            core_mechanism=mechanism,
            extension_mechanism_slug=None,
            semantic_summary=(
                f"Visible core mechanism {mechanism.value.replace('_', ' ').title()}."
            ),
        )
        for ordinal, mechanism in enumerate(visible_mechanisms)
    )
    gaps: list[RegistrationEngineeringGapOptionV2341] = []
    gap_sources: dict[EvidenceSourceV22, list[tuple[str, str]]] = {}
    for report in request.accepted_reports:
        for evidence in report.evidence_summaries:
            if (
                evidence.anomaly_kind is GenericAnomalyKindV23.SOURCE_COVERAGE_GAP
                or (
                    request.shadow_fault.broad_fault_domain
                    is ProvisionalFaultDomainV23.NETWORK
                    and evidence.anomaly_kind
                    is GenericAnomalyKindV23.LOG_UNKNOWN_ERROR_PATTERN
                )
            ):
                gap_sources.setdefault(evidence.source, []).append(
                    (evidence.evidence_ref, evidence.summary)
                )
    for ordinal, (source, values) in enumerate(
        sorted(gap_sources.items(), key=lambda item: item[0].value)
    ):
        gap_slug = f"{request.shadow_fault.broad_fault_domain.value.casefold()}-{source.value.casefold()}-extraction"
        gaps.append(
            RegistrationEngineeringGapOptionV2341(
                engineering_gap_alias=f"G{ordinal:02d}",
                gap_slug=gap_slug,
                missing_capability=(
                    "A bounded typed extractor for the accepted semantic signal."
                ),
                evidence_source=source,
                supporting_evidence_refs=tuple(sorted({item[0] for item in values})),
                evidence_summary=" ".join(sorted({item[1] for item in values}))[:500],
                engineering_question=(
                    f"Define the bounded engineering gap for {gap_slug}."
                ),
            )
        )
    return {
        "schema_version": "dta-v2341.registration-option-catalog.v1",
        "authorization_id": request.authorization_id,
        "registration_seed_sha256": request.registration_seed_sha256,
        "source_request_sha256": request.request_sha256,
        "human_canonical_label": request.shadow_fault.canonical_label,
        "broad_fault_domain": request.shadow_fault.broad_fault_domain,
        "disposition_options": disposition_options,
        "predicate_options": predicate_options,
        "clause_options": clause_options,
        "confusable_options": confusable_options,
        "engineering_gap_options": tuple(gaps),
        "provider_calls": 0,
    }


def build_registration_option_catalog_v2341(
    *,
    request: RegistrationDraftProviderRequestV234,
    core_snapshot: CoreOntologySchemaSnapshotV234 | None = None,
) -> RegistrationOptionCatalogV2341:
    snapshot = core_snapshot or build_core_ontology_schema_snapshot_v234()
    if (
        request.core_ontology_view.authoritative_snapshot_sha256
        != snapshot.snapshot_sha256
    ):
        raise ValueError("registration catalog Core Snapshot binding differs")
    payload = _catalog_payload_v2341(request, core_snapshot=snapshot)
    rendered = RegistrationOptionCatalogV2341.model_construct(
        **payload,
        catalog_sha256="0" * 64,
    ).model_dump(mode="json", exclude={"catalog_sha256"})
    return RegistrationOptionCatalogV2341.model_validate(
        {
            **payload,
            "catalog_sha256": semantic_sha256_v22(rendered),
        }
    )


def evaluate_catalog_feasibility_v2341(
    *,
    catalog: RegistrationOptionCatalogV2341,
    expected_disposition: RegistrationImplementationModeV234 | None = None,
) -> RegistrationCatalogFeasibilityDecisionV2341:
    issues: set[str] = set()
    predicate_count = sum(
        bool(item.draft.supporting_report_evidence_refs)
        for item in catalog.predicate_options
    )
    clause_count = sum(
        bool(item.requirements)
        and all(
            requirement.predicate_name
            in {option.draft.predicate_name for option in catalog.predicate_options}
            for requirement in item.requirements
        )
        for item in catalog.clause_options
    )
    gap_count = len(catalog.engineering_gap_options)
    if expected_disposition is RegistrationImplementationModeV234.DECLARATIVE_READY:
        if predicate_count == 0:
            issues.add("NO_EVIDENCE_SUPPORTED_PREDICATE")
        if clause_count == 0:
            issues.add("NO_COMPILE_VALID_CLAUSE")
    elif expected_disposition is RegistrationImplementationModeV234.ENGINEERING_REQUIRED:
        if gap_count == 0:
            issues.add("NO_ENGINEERING_GAP_OPTION")
    elif expected_disposition is None and clause_count == 0 and gap_count == 0:
        issues.add("NO_REPRESENTABLE_REGISTRATION_DISPOSITION")
    status = (
        CatalogFeasibilityStatusV2341.PASS
        if not issues
        else CatalogFeasibilityStatusV2341.CATALOG_COVERAGE_FAILURE
    )
    payload: dict[str, Any] = {
        "schema_version": "dta-v2341.registration-catalog-feasibility.v1",
        "catalog_sha256": catalog.catalog_sha256,
        "expected_disposition": expected_disposition,
        "evidence_supported_predicate_count": predicate_count,
        "compile_valid_clause_count": clause_count,
        "engineering_gap_count": gap_count,
        "status": status,
        "issue_codes": tuple(sorted(issues)),
        "terminal": (
            "DTA_V2341_CATALOG_FEASIBILITY_PASS"
            if status is CatalogFeasibilityStatusV2341.PASS
            else "CATALOG_COVERAGE_FAILURE"
        ),
    }
    return RegistrationCatalogFeasibilityDecisionV2341.model_validate(
        {
            **payload,
            "decision_sha256": semantic_sha256_v22(payload),
        }
    )


__all__ = (
    "CatalogFeasibilityStatusV2341",
    "RegistrationCatalogFeasibilityDecisionV2341",
    "RegistrationClauseOptionV2341",
    "RegistrationConfusableOptionV2341",
    "RegistrationDispositionOptionV2341",
    "RegistrationEngineeringGapOptionV2341",
    "RegistrationOptionCatalogV2341",
    "RegistrationPredicateOptionV2341",
    "build_registration_option_catalog_v2341",
    "evaluate_catalog_feasibility_v2341",
)
