"""Typed, non-executable registration contracts for DTA v2.3.4."""

from __future__ import annotations

from enum import Enum
import re
from typing import Annotated, Any, Literal, TypeAlias, TypeVar, cast

from pydantic import Field, StrictBool, StrictFloat, StrictInt, model_validator

from ecomsre.dta_v2.v22.memory import LogCategoryV22, PredicateKindV22
from ecomsre.dta_v2.v22.predicates import (
    MechanismV22,
    RequirementServiceBindingV22,
)
from ecomsre.dta_v2.v22.read_contracts import (
    ChangeCategoryV22,
    DtaModelV22,
    EvidenceSourceV22,
    METRIC_UNIT_BY_KIND_V22,
    MetricKindV22,
    MetricUnitV22,
    RuntimeStateV22,
    semantic_sha256_v22,
)
from ecomsre.dta_v2.v23.contracts import ProvisionalFaultDomainV23
from ecomsre.dta_v2.v23.generic_anomalies import GenericAnomalyKindV23


_PROVIDER_SENTENCE_V234 = re.compile(
    r"^[A-Z0-9][A-Za-z0-9 ,.'()%-]{2,1998}[.!?]$"
)
_PROVIDER_DISPLAY_NAME_V234 = re.compile(
    r"^(?:[A-Z][a-z0-9]+|[A-Z]{2,}|[0-9]+)"
    r"(?: (?:[A-Z][a-z0-9]+|[A-Z]{2,}|[0-9]+)){0,7}$"
)
_PROVIDER_REFERENCE_V234 = re.compile(r"^[a-z0-9][a-z0-9-]{2,119}$")
_PROVIDER_OPAQUE_IDENTIFIER_V234 = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9:._-]{2,239}$"
)
_PROVIDER_SENTENCE_LEADS_V234 = frozenset(
    {
        "a",
        "an",
        "any",
        "connection",
        "concurrent",
        "cpu",
        "define",
        "dependency",
        "distinguish",
        "downstream",
        "errors",
        "generic",
        "independent",
        "logs",
        "memory",
        "metrics",
        "queue",
        "requests",
        "require",
        "resource",
        "reuse",
        "runtime",
        "service",
        "strong",
        "target",
        "the",
        "this",
        "trace",
        "traffic",
        "use",
        "when",
    }
)
_PROVIDER_LOG_LITERALS_V234 = frozenset(
    {
        "connection lease wait",
        "connection pool exhausted",
        "pool capacity wait",
        "worker pool semaphore wait",
    }
)
_PROVIDER_EXECUTABLE_TOKEN_V234 = re.compile(
    r"(?:\b(?:chmod|chown|curl|eval|exec|import|lambda|nc|netcat|open|python|"
    r"raise|rm|runbook|sh|shell|socket|subprocess|sudo|systemexit|touch|wget)\b|"
    r"https?://|diff --git|--- a/|\+\+\+ b/|```)",
    re.IGNORECASE,
)
_PROVIDER_COMMAND_PREFIX_V234 = re.compile(
    r"^(?:awk|bash|cat|chmod|chown|cp|curl|dd|docker|find|git|grep|helm|"
    r"install|kill|killall|kubectl|launchctl|ln|mkdir|mv|nc|netcat|node|"
    r"perl|php|pkill|printf|python|rm|ruby|scp|sed|sftp|ssh|sudo|tar|tee|"
    r"touch|unzip|wget|xargs|zip|zsh)\b",
    re.IGNORECASE,
)


def _require_provider_sentence_v234(value: str, *, label: str) -> None:
    first_word = value.split(" ", 1)[0].casefold()
    if (
        not _PROVIDER_SENTENCE_V234.fullmatch(value)
        or len(re.findall(r"[A-Za-z0-9]+", value)) < 3
        or first_word not in _PROVIDER_SENTENCE_LEADS_V234
        or _PROVIDER_EXECUTABLE_TOKEN_V234.search(value)
        or _PROVIDER_COMMAND_PREFIX_V234.search(value)
    ):
        raise ValueError(f"{label} contains forbidden executable content")


def _require_provider_reference_v234(value: str, *, label: str) -> None:
    if not _PROVIDER_REFERENCE_V234.fullmatch(value):
        raise ValueError(f"{label} contains forbidden executable content")


def _require_provider_opaque_identifier_v234(value: str, *, label: str) -> None:
    if not _PROVIDER_OPAQUE_IDENTIFIER_V234.fullmatch(value):
        raise ValueError(f"{label} contains forbidden executable content")


def _slug_words_v234(slug: str) -> str:
    return slug.replace("-", " ")


def mechanism_display_name_v234(enum_name: str) -> str:
    acronyms = {"API", "CPU", "DB", "DNS", "HTTP", "IO", "TLS"}
    return " ".join(
        token if token in acronyms else token.capitalize()
        for token in enum_name.split("_")
    )


def mechanism_human_definition_v234(mechanism_slug: str) -> str:
    return (
        f"The {_slug_words_v234(mechanism_slug)} mechanism is defined by bounded "
        "evidence predicates and canonical support clauses."
    )


def mechanism_distinguishing_summary_v234(mechanism_slug: str) -> str:
    return (
        f"The {_slug_words_v234(mechanism_slug)} mechanism is distinguished by its "
        "canonical clauses and evidence bindings."
    )


def predicate_semantic_definition_v234(predicate_slug: str) -> str:
    return (
        f"The {_slug_words_v234(predicate_slug)} predicate is defined by its typed "
        "extraction rule and accepted evidence binding."
    )


def predicate_positive_example_v234(predicate_slug: str) -> str:
    return (
        f"The {_slug_words_v234(predicate_slug)} predicate is present in accepted "
        "evidence."
    )


def predicate_negative_example_v234(predicate_slug: str) -> str:
    return (
        f"The {_slug_words_v234(predicate_slug)} predicate is absent from accepted "
        "evidence."
    )


def support_clause_rationale_v234() -> str:
    return (
        "The clause requires its canonical predicates at their declared service "
        "bindings."
    )


class RegistrationImplementationModeV234(str, Enum):
    DECLARATIVE_READY = "DECLARATIVE_READY"
    ENGINEERING_REQUIRED = "ENGINEERING_REQUIRED"
    DUPLICATE_EXISTING = "DUPLICATE_EXISTING"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"


class PredicateImplementationModeV234(str, Enum):
    REUSE_CORE_PREDICATE = "REUSE_CORE_PREDICATE"
    DECLARATIVE_EXTENSION_PREDICATE = "DECLARATIVE_EXTENSION_PREDICATE"
    REQUIRES_CODE_IMPLEMENTATION = "REQUIRES_CODE_IMPLEMENTATION"


class ThresholdComparisonV234(str, Enum):
    GREATER_THAN = "GREATER_THAN"
    GREATER_THAN_OR_EQUAL = "GREATER_THAN_OR_EQUAL"
    LESS_THAN = "LESS_THAN"
    LESS_THAN_OR_EQUAL = "LESS_THAN_OR_EQUAL"


class CorePredicateReferenceRuleV234(DtaModelV22):
    kind: Literal["CORE_PREDICATE_REFERENCE"]
    predicate_kind: PredicateKindV22


class GenericAnomalyKindRuleV234(DtaModelV22):
    kind: Literal["GENERIC_ANOMALY_KIND"]
    anomaly_kind: GenericAnomalyKindV23


class LogCategoryRuleV234(DtaModelV22):
    kind: Literal["LOG_CATEGORY"]
    category: LogCategoryV22


class LogTemplateContainsAnyRuleV234(DtaModelV22):
    kind: Literal["LOG_TEMPLATE_CONTAINS_ANY"]
    literals: tuple[str, ...] = Field(min_length=1, max_length=8)
    case_sensitive: StrictBool

    @model_validator(mode="after")
    def require_bounded_literals(self) -> "LogTemplateContainsAnyRuleV234":
        if self.literals != tuple(sorted(set(self.literals))):
            raise ValueError("log template literals are not canonical")
        for literal in self.literals:
            if re.search(r"[\\.^$*+?{}\[\]()|]", literal):
                raise ValueError(
                    "log template regular expression syntax is forbidden; use literals"
                )
            if (
                len(literal) < 4
                or len(literal) > 120
                or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 -]*", literal)
                or not re.search(r"[A-Za-z0-9]{3,}", literal)
            ):
                raise ValueError("log template literal is not substantive")
            if literal not in _PROVIDER_LOG_LITERALS_V234:
                raise ValueError(
                    "log template literal contains forbidden executable content"
                )
        return self


class TraceFirstErrorAtServiceRuleV234(DtaModelV22):
    kind: Literal["TRACE_FIRST_ERROR_AT_SERVICE"]


class TracePathContainsRuleV234(DtaModelV22):
    kind: Literal["TRACE_PATH_CONTAINS"]
    required_service_role: Literal["TARGET", "PARENT", "TARGET_OR_PARENT"]


class TraceDurationThresholdRuleV234(DtaModelV22):
    kind: Literal["TRACE_DURATION_THRESHOLD"]
    comparison: ThresholdComparisonV234
    milliseconds: StrictFloat = Field(gt=0.0, le=3_600_000.0)


class MetricThresholdRuleV234(DtaModelV22):
    kind: Literal["METRIC_THRESHOLD"]
    metric_kind: MetricKindV22
    comparison: ThresholdComparisonV234
    threshold: StrictFloat = Field(ge=0.0)
    unit: MetricUnitV22

    @model_validator(mode="after")
    def require_metric_unit(self) -> "MetricThresholdRuleV234":
        if METRIC_UNIT_BY_KIND_V22[self.metric_kind] is not self.unit:
            raise ValueError("metric threshold unit differs from metric kind")
        if self.metric_kind is MetricKindV22.ERROR_RATE and self.threshold > 1.0:
            raise ValueError("error-rate threshold exceeds one")
        if self.metric_kind is MetricKindV22.CPU_PERCENT and self.threshold > 100.0:
            raise ValueError("CPU threshold exceeds one hundred percent")
        if self.comparison in {
            ThresholdComparisonV234.GREATER_THAN,
            ThresholdComparisonV234.GREATER_THAN_OR_EQUAL,
        } and self.threshold <= 0.0:
            raise ValueError("vacuous metric threshold can absorb No-Incident")
        return self


class MetricBaselineRatioRuleV234(DtaModelV22):
    kind: Literal["METRIC_BASELINE_RATIO"]
    metric_kind: MetricKindV22
    comparison: Literal["GREATER_THAN", "GREATER_THAN_OR_EQUAL"]
    ratio: StrictFloat = Field(gt=1.0, le=1000.0)
    minimum_samples: StrictInt = Field(ge=2, le=1000)


class ResourceCpuThresholdRuleV234(DtaModelV22):
    kind: Literal["RESOURCE_CPU_THRESHOLD"]
    comparison: ThresholdComparisonV234
    percent: StrictFloat = Field(gt=0.0, lt=100.0)

    @model_validator(mode="after")
    def require_anomalous_direction(self) -> "ResourceCpuThresholdRuleV234":
        if self.comparison not in {
            ThresholdComparisonV234.GREATER_THAN,
            ThresholdComparisonV234.GREATER_THAN_OR_EQUAL,
        }:
            raise ValueError("CPU threshold must express an anomalous upper tail")
        return self


class ResourceMemorySlopeRuleV234(DtaModelV22):
    kind: Literal["RESOURCE_MEMORY_SLOPE"]
    comparison: Literal["GREATER_THAN", "GREATER_THAN_OR_EQUAL"]
    bytes_per_second: StrictFloat = Field(gt=0.0)
    minimum_points: StrictInt = Field(ge=2, le=1000)


class RuntimeStateRuleV234(DtaModelV22):
    kind: Literal["RUNTIME_STATE"]
    states: tuple[RuntimeStateV22, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def require_states(self) -> "RuntimeStateRuleV234":
        if self.states != tuple(sorted(set(self.states), key=lambda item: item.value)):
            raise ValueError("runtime states are not canonical")
        if set(self.states) == set(RuntimeStateV22):
            raise ValueError("runtime-state rule cannot match every state")
        return self


class RecentChangeStateRuleV234(DtaModelV22):
    kind: Literal["RECENT_CHANGE_STATE"]
    categories: tuple[ChangeCategoryV22, ...] = Field(min_length=1)
    window_seconds: StrictInt = Field(ge=1, le=86_400)

    @model_validator(mode="after")
    def require_categories(self) -> "RecentChangeStateRuleV234":
        if self.categories != tuple(
            sorted(set(self.categories), key=lambda item: item.value)
        ):
            raise ValueError("recent-change categories are not canonical")
        if set(self.categories) == set(ChangeCategoryV22):
            raise ValueError("recent-change rule cannot match every category")
        return self


ExtensionPredicateRuleV234: TypeAlias = Annotated[
    CorePredicateReferenceRuleV234
    | GenericAnomalyKindRuleV234
    | LogCategoryRuleV234
    | LogTemplateContainsAnyRuleV234
    | TraceFirstErrorAtServiceRuleV234
    | TracePathContainsRuleV234
    | TraceDurationThresholdRuleV234
    | MetricThresholdRuleV234
    | MetricBaselineRatioRuleV234
    | ResourceCpuThresholdRuleV234
    | ResourceMemorySlopeRuleV234
    | RuntimeStateRuleV234
    | RecentChangeStateRuleV234,
    Field(discriminator="kind"),
]


class MechanismProposalV234(DtaModelV22):
    mechanism_enum_name: str = Field(pattern=r"^[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)*$")
    mechanism_slug: str = Field(pattern=r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")
    display_name: str = Field(min_length=1, max_length=120)
    broad_fault_domain: ProvisionalFaultDomainV23
    human_definition: str = Field(min_length=1, max_length=2000)
    distinguishing_summary: str = Field(min_length=1, max_length=2000)
    confusable_core_mechanisms: tuple[MechanismV22, ...]
    confusable_extension_mechanisms: tuple[str, ...]

    @model_validator(mode="after")
    def require_canonical_confusables(self) -> "MechanismProposalV234":
        if self.display_name != mechanism_display_name_v234(
            self.mechanism_enum_name
        ) or not _PROVIDER_DISPLAY_NAME_V234.fullmatch(self.display_name):
            raise ValueError("mechanism display name contains forbidden executable content")
        _require_provider_sentence_v234(
            self.human_definition,
            label="mechanism human definition",
        )
        _require_provider_sentence_v234(
            self.distinguishing_summary,
            label="mechanism distinguishing summary",
        )
        if self.human_definition != mechanism_human_definition_v234(
            self.mechanism_slug
        ) or self.distinguishing_summary != mechanism_distinguishing_summary_v234(
            self.mechanism_slug
        ):
            raise ValueError("mechanism prose contains forbidden executable content")
        if self.confusable_core_mechanisms != tuple(
            sorted(set(self.confusable_core_mechanisms), key=lambda item: item.value)
        ):
            raise ValueError("core mechanism confusables are not canonical")
        if self.confusable_extension_mechanisms != tuple(
            sorted(set(self.confusable_extension_mechanisms))
        ):
            raise ValueError("extension mechanism confusables are not canonical")
        for slug in self.confusable_extension_mechanisms:
            _require_provider_reference_v234(slug, label="extension mechanism confusable")
        return self


class FormalPredicateDraftV234(DtaModelV22):
    predicate_name: str = Field(pattern=r"^[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)*$")
    predicate_slug: str = Field(pattern=r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")
    implementation_mode: PredicateImplementationModeV234
    evidence_source: EvidenceSourceV22
    service_binding: RequirementServiceBindingV22
    require_exact_parent: StrictBool
    semantic_definition: str = Field(min_length=1, max_length=1200)
    extraction_rule: ExtensionPredicateRuleV234 | None
    threshold_rule: Literal["RULE_EMBEDS_TYPED_THRESHOLD"] | None = None
    supporting_report_evidence_refs: tuple[str, ...] = Field(min_length=1)
    positive_examples: tuple[str, ...] = Field(min_length=1)
    negative_examples: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def require_implementation_binding(self) -> "FormalPredicateDraftV234":
        _require_provider_sentence_v234(
            self.semantic_definition,
            label="predicate semantic definition",
        )
        for value in (*self.positive_examples, *self.negative_examples):
            _require_provider_sentence_v234(value, label="predicate example")
        if (
            self.semantic_definition
            != predicate_semantic_definition_v234(self.predicate_slug)
            or self.positive_examples
            != (predicate_positive_example_v234(self.predicate_slug),)
            or self.negative_examples
            != (predicate_negative_example_v234(self.predicate_slug),)
        ):
            raise ValueError("predicate prose contains forbidden executable content")
        for values, label in (
            (self.supporting_report_evidence_refs, "supporting evidence refs"),
            (self.positive_examples, "positive examples"),
            (self.negative_examples, "negative examples"),
        ):
            if values != tuple(sorted(set(values))):
                raise ValueError(f"predicate {label} are not canonical")
        for value in self.supporting_report_evidence_refs:
            _require_provider_opaque_identifier_v234(
                value,
                label="predicate supporting evidence ref",
            )
        is_core = isinstance(self.extraction_rule, CorePredicateReferenceRuleV234)
        if self.implementation_mode is PredicateImplementationModeV234.REUSE_CORE_PREDICATE:
            if not is_core:
                raise ValueError("reused core predicate lacks a core binding")
        elif (
            self.implementation_mode
            is PredicateImplementationModeV234.DECLARATIVE_EXTENSION_PREDICATE
        ):
            if self.extraction_rule is None or is_core:
                raise ValueError("declarative extension predicate lacks an extension rule")
        elif self.extraction_rule is not None:
            raise ValueError("code-required predicate carries a declarative rule")
        return self


class PredicateRequirementDraftV234(DtaModelV22):
    predicate_name: str = Field(pattern=r"^[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)*$")
    service_binding: RequirementServiceBindingV22
    require_exact_parent: StrictBool


class SupportClauseDraftV234(DtaModelV22):
    clause_id: str = Field(pattern=r"^[a-z][a-z0-9-]*:[a-z][a-z0-9-]*$")
    mechanism_slug: str = Field(pattern=r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")
    requirements: tuple[PredicateRequirementDraftV234, ...] = Field(min_length=1)
    rationale: str = Field(min_length=1, max_length=1200)

    @model_validator(mode="after")
    def require_canonical_requirements(self) -> "SupportClauseDraftV234":
        _require_provider_sentence_v234(self.rationale, label="support-clause rationale")
        if self.rationale != support_clause_rationale_v234():
            raise ValueError("support-clause prose contains forbidden executable content")
        keys = tuple(
            (
                item.predicate_name,
                item.service_binding.value,
                item.require_exact_parent,
            )
            for item in self.requirements
        )
        if keys != tuple(sorted(set(keys))):
            raise ValueError("support-clause requirements are not canonical")
        if not self.clause_id.startswith(f"{self.mechanism_slug}:"):
            raise ValueError("support-clause ID differs from mechanism slug")
        return self


class RegistrationTestPlanV234(DtaModelV22):
    positive_report_ids: tuple[str, ...]
    positive_case_ids: tuple[str, ...]
    confusable_core_mechanisms: tuple[MechanismV22, ...]
    required_known_controls: tuple[str, ...] = Field(min_length=1)
    required_no_incident_controls: tuple[str, ...] = Field(min_length=1)
    required_counterfactuals: tuple[str, ...] = Field(min_length=1)
    required_source_failure_tests: tuple[str, ...] = Field(min_length=1)
    required_clause_binding_tests: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def require_plan(self) -> "RegistrationTestPlanV234":
        if not self.positive_report_ids or not self.positive_case_ids:
            raise ValueError("registration test plan lacks positive bindings")
        for values, label in (
            (self.positive_report_ids, "positive reports"),
            (self.positive_case_ids, "positive cases"),
            (self.required_known_controls, "known controls"),
            (self.required_no_incident_controls, "no-incident controls"),
            (self.required_counterfactuals, "counterfactuals"),
            (self.required_source_failure_tests, "source-failure tests"),
            (self.required_clause_binding_tests, "clause-binding tests"),
        ):
            if values != tuple(sorted(set(values))):
                raise ValueError(f"registration test plan {label} are not canonical")
        for value in (
            *self.required_known_controls,
            *self.required_no_incident_controls,
            *self.required_counterfactuals,
            *self.required_source_failure_tests,
            *self.required_clause_binding_tests,
        ):
            _require_provider_reference_v234(value, label="registration test reference")
        for value in (*self.positive_report_ids, *self.positive_case_ids):
            _require_provider_opaque_identifier_v234(
                value,
                label="registration positive binding",
            )
        if self.confusable_core_mechanisms != tuple(
            sorted(set(self.confusable_core_mechanisms), key=lambda item: item.value)
        ):
            raise ValueError("registration test plan confusables are not canonical")
        return self


class RegistrationProviderModeV234(str, Enum):
    DETERMINISTIC_DEVELOPMENT = "DETERMINISTIC_DEVELOPMENT"
    OPENAI_COMPATIBLE = "OPENAI_COMPATIBLE"


class RegistrationProviderTraceV234(DtaModelV22):
    schema_version: Literal["dta-v234.registration-provider-trace.v1"]
    provider_mode: RegistrationProviderModeV234
    request_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    response_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    provider_calls: StrictInt = Field(ge=0, le=12)
    protocol_repairs: StrictInt = Field(ge=0, le=2)
    transport_retries: StrictInt = Field(ge=0, le=9)
    max_exact_request_retries: Literal[3]
    semantic_retries: Literal[0]
    raw_provider_artifacts_scope: Literal[".local/dta-v234/provider-raw"]
    trace_sha256: str

    @model_validator(mode="after")
    def require_trace(self) -> "RegistrationProviderTraceV234":
        if (
            self.provider_mode is RegistrationProviderModeV234.DETERMINISTIC_DEVELOPMENT
            and self.provider_calls != 0
        ):
            raise ValueError("deterministic development trace claims Provider calls")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"trace_sha256"})
        )
        if self.trace_sha256 != expected:
            raise ValueError("registration Provider trace digest differs")
        return self


class RegistrationDraftContentV234(DtaModelV22):
    """The only semantic content accepted from a registration Provider."""

    implementation_mode: RegistrationImplementationModeV234
    mechanism: MechanismProposalV234
    predicates: tuple[FormalPredicateDraftV234, ...]
    support_clauses: tuple[SupportClauseDraftV234, ...]
    test_plan: RegistrationTestPlanV234
    unresolved_engineering_questions: tuple[str, ...]
    remediation_registration: Literal["NOT_INCLUDED"]

    @model_validator(mode="after")
    def require_safe_provider_content(self) -> "RegistrationDraftContentV234":
        for question in self.unresolved_engineering_questions:
            _require_provider_sentence_v234(
                question,
                label="unresolved engineering question",
            )
            if not re.fullmatch(
                r"Define the bounded engineering gap for [a-z][a-z0-9-]{2,119}\.",
                question,
            ):
                raise ValueError(
                    "unresolved engineering question contains forbidden executable content"
                )
        assert_provider_authored_content_safe_v234(
            self.model_dump(mode="json", exclude={"remediation_registration"})
        )
        return self


_PROVIDER_AUTHORED_DRAFT_FIELDS_V234 = {
    "implementation_mode",
    "mechanism",
    "predicates",
    "support_clauses",
    "test_plan",
    "unresolved_engineering_questions",
}


def provider_authored_content_sha256_v234(value: DtaModelV22) -> str:
    payload = value.model_dump(
        mode="json",
        include=_PROVIDER_AUTHORED_DRAFT_FIELDS_V234,
    )
    if set(payload) != _PROVIDER_AUTHORED_DRAFT_FIELDS_V234:
        raise ValueError("Provider-authored content projection differs")
    return semantic_sha256_v22(payload)


class FormalFaultRegistrationDraftV234(DtaModelV22):
    schema_version: Literal["dta-v234.formal-fault-registration-draft.v1"]
    draft_id: str = Field(pattern=r"^draft-v234-[0-9a-f]{16}$")
    authorization_id: str = Field(pattern=r"^authorization-v234-[0-9a-f]{16}$")
    shadow_fault_id: str = Field(pattern=r"^shadow-v23-[0-9a-f]{16}$")
    registration_seed_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    core_ontology_snapshot_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    implementation_mode: RegistrationImplementationModeV234
    mechanism: MechanismProposalV234
    predicates: tuple[FormalPredicateDraftV234, ...]
    support_clauses: tuple[SupportClauseDraftV234, ...]
    test_plan: RegistrationTestPlanV234
    unresolved_engineering_questions: tuple[str, ...]
    remediation_registration: Literal["NOT_INCLUDED"]
    provider_trace: RegistrationProviderTraceV234
    action_authority: Literal["NONE"]
    repository_write_authority: Literal["NONE"]
    draft_sha256: str

    @model_validator(mode="after")
    def require_draft(self) -> "FormalFaultRegistrationDraftV234":
        predicate_names = tuple(item.predicate_name for item in self.predicates)
        predicate_slugs = tuple(item.predicate_slug for item in self.predicates)
        clause_ids = tuple(item.clause_id for item in self.support_clauses)
        if predicate_names != tuple(sorted(set(predicate_names))):
            raise ValueError("formal draft predicate names are not canonical")
        if predicate_slugs != tuple(sorted(set(predicate_slugs))):
            raise ValueError("formal draft predicate slugs are not canonical")
        if clause_ids != tuple(sorted(set(clause_ids))):
            raise ValueError("formal draft support clauses are not canonical")
        if self.unresolved_engineering_questions != tuple(
            sorted(set(self.unresolved_engineering_questions))
        ):
            raise ValueError("formal draft engineering questions are not canonical")
        if any(
            clause.mechanism_slug != self.mechanism.mechanism_slug
            for clause in self.support_clauses
        ):
            raise ValueError("formal draft clause mechanism bindings differ")
        assert_provider_authored_content_safe_v234(
            {
                "implementation_mode": self.implementation_mode,
                "mechanism": self.mechanism,
                "predicates": self.predicates,
                "support_clauses": self.support_clauses,
                "test_plan": self.test_plan,
                "unresolved_engineering_questions": (
                    self.unresolved_engineering_questions
                ),
            }
        )
        if self.implementation_mode is RegistrationImplementationModeV234.DECLARATIVE_READY:
            if (
                not self.predicates
                or not self.support_clauses
                or self.unresolved_engineering_questions
                or any(
                    item.implementation_mode
                    is PredicateImplementationModeV234.REQUIRES_CODE_IMPLEMENTATION
                    for item in self.predicates
                )
            ):
                raise ValueError("declarative-ready draft is structurally incomplete")
        elif self.implementation_mode is RegistrationImplementationModeV234.ENGINEERING_REQUIRED:
            if not self.unresolved_engineering_questions or not any(
                item.implementation_mode
                is PredicateImplementationModeV234.REQUIRES_CODE_IMPLEMENTATION
                for item in self.predicates
            ):
                raise ValueError("engineering-required draft lacks an engineering gap")
        elif self.support_clauses:
            raise ValueError("non-registrable draft carries support clauses")
        if self.provider_trace.response_sha256 != (
            provider_authored_content_sha256_v234(self)
        ):
            raise ValueError("Provider-authored content digest differs")
        expected_id = _draft_id_v234(
            authorization_id=self.authorization_id,
            shadow_fault_id=self.shadow_fault_id,
            registration_seed_sha256=self.registration_seed_sha256,
            core_ontology_snapshot_sha256=self.core_ontology_snapshot_sha256,
            mechanism_slug=self.mechanism.mechanism_slug,
            response_sha256=self.provider_trace.response_sha256,
        )
        if self.draft_id != expected_id:
            raise ValueError("formal draft identity differs")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"draft_sha256"})
        )
        if self.draft_sha256 != expected:
            raise ValueError("formal draft digest differs")
        return self


_FORBIDDEN_EXECUTABLE_CONTENT_V234 = re.compile(
    r"(?:\brunbook\b|\bremediation\b|https?://|\bcurl\b|\bwget\b|"
    r"\bsudo\b|\b(?:ba|z|k|c)?sh\s+-c\b|\bpython(?:3)?\s+-m\b|"
    r"\brm\s+-[a-z]*r|\b(?:from\s+[a-z_][\w.]*\s+import|import\s+"
    r"[a-z_][\w.]*)\b|\b(?:def|class)\s+[a-z_]\w*\s*[:(]|"
    r"\bsubprocess\b|\b(?:socket|urllib|httpx|requests|pathlib|os)\s*\.|"
    r"\b(?:print|open|exec|eval)\s*\(|\.write_(?:text|bytes)\s*\(|"
    r"\b(?:chmod|chown|lambda|nc|netcat|raise|systemexit|touch)\b|"
    r"diff --git|--- a/|\+\+\+ b/|```)",
    re.IGNORECASE,
)

_FORBIDDEN_PROVIDER_TEXT_BYTES_V234 = re.compile(r"[\r\n`{}\[\]\\/;$#|&=<>]")


def _all_strings_v234(value: object) -> tuple[str, ...]:
    strings: list[str] = []

    def walk(item: object) -> None:
        if isinstance(item, str):
            strings.append(item)
        elif isinstance(item, DtaModelV22):
            walk(item.model_dump(mode="python"))
        elif isinstance(item, dict):
            for child in item.values():
                walk(child)
        elif isinstance(item, (list, tuple)):
            for child in item:
                walk(child)

    walk(value)
    return tuple(strings)


def assert_provider_authored_content_safe_v234(value: object) -> None:
    for item in _all_strings_v234(value):
        if _FORBIDDEN_PROVIDER_TEXT_BYTES_V234.search(item) or (
            _FORBIDDEN_EXECUTABLE_CONTENT_V234.search(item)
        ) or (
            _PROVIDER_COMMAND_PREFIX_V234.search(item)
        ):
            raise ValueError("formal draft contains forbidden executable content")


def _draft_id_v234(
    *,
    authorization_id: str,
    shadow_fault_id: str,
    registration_seed_sha256: str,
    core_ontology_snapshot_sha256: str,
    mechanism_slug: str,
    response_sha256: str,
) -> str:
    identity = {
        "authorization_id": authorization_id,
        "shadow_fault_id": shadow_fault_id,
        "registration_seed_sha256": registration_seed_sha256,
        "core_ontology_snapshot_sha256": core_ontology_snapshot_sha256,
        "mechanism_slug": mechanism_slug,
        "response_sha256": response_sha256,
    }
    return f"draft-v234-{semantic_sha256_v22(identity)[:16]}"


_ModelT = TypeVar("_ModelT", bound=DtaModelV22)


def hashed_model_v234(
    model: type[_ModelT],
    payload: dict[str, Any],
    digest_field: str,
) -> _ModelT:
    factory = cast(Any, model)
    draft = factory.model_construct(**payload, **{digest_field: "0" * 64})
    return model.model_validate(
        {
            **payload,
            digest_field: semantic_sha256_v22(
                draft.model_dump(
                    mode="json",
                    exclude={digest_field},
                    warnings=False,
                )
            ),
        }
    )


def build_provider_trace_v234(
    *,
    provider_mode: RegistrationProviderModeV234,
    request_sha256: str,
    response_sha256: str,
    provider_calls: int,
    protocol_repairs: int,
    transport_retries: int,
) -> RegistrationProviderTraceV234:
    payload: dict[str, Any] = {
        "schema_version": "dta-v234.registration-provider-trace.v1",
        "provider_mode": provider_mode,
        "request_sha256": request_sha256,
        "response_sha256": response_sha256,
        "provider_calls": provider_calls,
        "protocol_repairs": protocol_repairs,
        "transport_retries": transport_retries,
        "max_exact_request_retries": 3,
        "semantic_retries": 0,
        "raw_provider_artifacts_scope": ".local/dta-v234/provider-raw",
    }
    return hashed_model_v234(
        RegistrationProviderTraceV234,
        payload,
        "trace_sha256",
    )


def build_formal_registration_draft_v234(
    *,
    authorization_id: str,
    shadow_fault_id: str,
    registration_seed_sha256: str,
    core_ontology_snapshot_sha256: str,
    content: RegistrationDraftContentV234,
    provider_trace: RegistrationProviderTraceV234,
) -> FormalFaultRegistrationDraftV234:
    draft_id = _draft_id_v234(
        authorization_id=authorization_id,
        shadow_fault_id=shadow_fault_id,
        registration_seed_sha256=registration_seed_sha256,
        core_ontology_snapshot_sha256=core_ontology_snapshot_sha256,
        mechanism_slug=content.mechanism.mechanism_slug,
        response_sha256=provider_trace.response_sha256,
    )
    payload: dict[str, Any] = {
        "schema_version": "dta-v234.formal-fault-registration-draft.v1",
        "draft_id": draft_id,
        "authorization_id": authorization_id,
        "shadow_fault_id": shadow_fault_id,
        "registration_seed_sha256": registration_seed_sha256,
        "core_ontology_snapshot_sha256": core_ontology_snapshot_sha256,
        **content.model_dump(mode="python"),
        "provider_trace": provider_trace,
        "action_authority": "NONE",
        "repository_write_authority": "NONE",
    }
    return hashed_model_v234(
        FormalFaultRegistrationDraftV234,
        payload,
        "draft_sha256",
    )


def rebuild_formal_registration_draft_v234(
    draft: FormalFaultRegistrationDraftV234,
    **updates: object,
) -> FormalFaultRegistrationDraftV234:
    payload = draft.model_dump(mode="python", exclude={"draft_id", "draft_sha256"})
    payload.update(updates)
    content = RegistrationDraftContentV234.model_validate(
        {
            field: payload[field]
            for field in RegistrationDraftContentV234.model_fields
        }
    )
    previous_trace = RegistrationProviderTraceV234.model_validate(
        payload["provider_trace"]
    )
    provider_trace = build_provider_trace_v234(
        provider_mode=previous_trace.provider_mode,
        request_sha256=previous_trace.request_sha256,
        response_sha256=provider_authored_content_sha256_v234(content),
        provider_calls=previous_trace.provider_calls,
        protocol_repairs=previous_trace.protocol_repairs,
        transport_retries=previous_trace.transport_retries,
    )
    return build_formal_registration_draft_v234(
        authorization_id=cast(str, payload["authorization_id"]),
        shadow_fault_id=cast(str, payload["shadow_fault_id"]),
        registration_seed_sha256=cast(str, payload["registration_seed_sha256"]),
        core_ontology_snapshot_sha256=cast(
            str, payload["core_ontology_snapshot_sha256"]
        ),
        content=content,
        provider_trace=provider_trace,
    )


__all__ = (
    "CorePredicateReferenceRuleV234",
    "ExtensionPredicateRuleV234",
    "FormalFaultRegistrationDraftV234",
    "FormalPredicateDraftV234",
    "GenericAnomalyKindRuleV234",
    "LogCategoryRuleV234",
    "LogTemplateContainsAnyRuleV234",
    "MechanismProposalV234",
    "MetricBaselineRatioRuleV234",
    "MetricThresholdRuleV234",
    "PredicateImplementationModeV234",
    "PredicateRequirementDraftV234",
    "RecentChangeStateRuleV234",
    "RegistrationDraftContentV234",
    "RegistrationImplementationModeV234",
    "RegistrationProviderModeV234",
    "RegistrationProviderTraceV234",
    "RegistrationTestPlanV234",
    "ResourceCpuThresholdRuleV234",
    "ResourceMemorySlopeRuleV234",
    "RuntimeStateRuleV234",
    "SupportClauseDraftV234",
    "ThresholdComparisonV234",
    "TraceDurationThresholdRuleV234",
    "TraceFirstErrorAtServiceRuleV234",
    "TracePathContainsRuleV234",
    "build_formal_registration_draft_v234",
    "build_provider_trace_v234",
    "hashed_model_v234",
    "mechanism_display_name_v234",
    "mechanism_distinguishing_summary_v234",
    "mechanism_human_definition_v234",
    "predicate_negative_example_v234",
    "predicate_positive_example_v234",
    "predicate_semantic_definition_v234",
    "provider_authored_content_sha256_v234",
    "rebuild_formal_registration_draft_v234",
    "assert_provider_authored_content_safe_v234",
    "support_clause_rationale_v234",
)
