"""Typed contracts for the Product MVP environment knowledge loop."""

from __future__ import annotations

from datetime import datetime, timedelta
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22
from ecomsre.dta_v2.v23.registration_compiler_v234 import (
    CompiledFaultRegistrationV234,
    ExtensionPredicateDefinitionV234,
    ExtensionSupportClauseV234,
)


class ProductKnowledgeModelV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class FaultFamilyStatusV1(str, Enum):
    ACCUMULATING = "ACCUMULATING"
    REVIEW_READY = "REVIEW_READY"
    ACCEPTED_SHADOW = "ACCEPTED_SHADOW"
    MERGED = "MERGED"
    REJECTED = "REJECTED"
    REGISTRATION_DRAFTED = "REGISTRATION_DRAFTED"
    PROMOTED = "PROMOTED"


class ReviewDecisionV1(str, Enum):
    ACCEPT_AS_NEW = "ACCEPT_AS_NEW"
    MERGE_WITH_EXISTING = "MERGE_WITH_EXISTING"
    REQUEST_MORE_INCIDENTS = "REQUEST_MORE_INCIDENTS"
    REJECT_AS_NOISE = "REJECT_AS_NOISE"
    SAVE_AS_INCIDENT_FAMILY = "SAVE_AS_INCIDENT_FAMILY"


class PredicateCellStateV1(str, Enum):
    PRESENT = "PRESENT"
    ABSENT_WITH_COMPLETE_COVERAGE = "ABSENT_WITH_COMPLETE_COVERAGE"
    UNKNOWN = "UNKNOWN"
    SOURCE_FAILED = "SOURCE_FAILED"


class PredicateMatrixRowKindV1(str, Enum):
    POSITIVE_FAMILY = "POSITIVE_FAMILY"
    CORE_KNOWN_CONTROL = "CORE_KNOWN_CONTROL"
    NO_INCIDENT_CONTROL = "NO_INCIDENT_CONTROL"
    OTHER_ACCEPTED_FAMILY = "OTHER_ACCEPTED_FAMILY"
    INSUFFICIENT_OR_CONFLICT_CONTROL = "INSUFFICIENT_OR_CONFLICT_CONTROL"


class RegistrationImplementationModeV1(str, Enum):
    DECLARATIVE_READY = "DECLARATIVE_READY"
    ENGINEERING_REQUIRED = "ENGINEERING_REQUIRED"
    NEEDS_MORE_INCIDENTS = "NEEDS_MORE_INCIDENTS"
    NEEDS_MORE_NEGATIVES = "NEEDS_MORE_NEGATIVES"
    DUPLICATE_EXISTING = "DUPLICATE_EXISTING"


class ShadowEvaluationStratumV1(str, Enum):
    POSITIVE_INCIDENT = "POSITIVE_INCIDENT"
    CONFUSABLE_CORE_KNOWN = "CONFUSABLE_CORE_KNOWN"
    OTHER_EXTENSION = "OTHER_EXTENSION"
    NO_INCIDENT = "NO_INCIDENT"
    INSUFFICIENT_OR_CONFLICT = "INSUFFICIENT_OR_CONFLICT"
    TARGET_COUNTERFACTUAL = "TARGET_COUNTERFACTUAL"
    SOURCE_FAILURE = "SOURCE_FAILURE"


class ShadowCaseOriginV1(str, Enum):
    PERSISTED_INCIDENT = "PERSISTED_INCIDENT"
    DERIVED_COUNTERFACTUAL = "DERIVED_COUNTERFACTUAL"
    DERIVED_SOURCE_FAILURE = "DERIVED_SOURCE_FAILURE"
    NOT_AVAILABLE = "NOT_AVAILABLE"


class FingerprintObservationV1(ProductKnowledgeModelV1):
    environment_id: str = Field(min_length=1)
    incident_id: str = Field(min_length=1)
    root_service_ids: tuple[str, ...]
    broad_domain: str = Field(min_length=1)
    generic_anomaly_kinds: tuple[str, ...]
    evidence_sources: tuple[str, ...]
    topology_edges: tuple[tuple[str, str], ...]
    runtime_state_signature: tuple[str, ...]
    resource_state_signature: tuple[str, ...]
    normalized_log_tokens: tuple[str, ...]
    trace_first_error_roles: tuple[str, ...]
    source_coverage: tuple[str, ...]


class IncidentFingerprintV1(FingerprintObservationV1):
    schema_version: Literal["ecomsre.product.incident-fingerprint.v1"]
    fingerprint_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_digest(self) -> "IncidentFingerprintV1":
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"fingerprint_sha256"})
        )
        if self.fingerprint_sha256 != expected:
            raise ValueError("incident fingerprint digest differs")
        return self


class PredicateMatrixCellV1(ProductKnowledgeModelV1):
    predicate_id: str = Field(min_length=1)
    source: str = Field(min_length=1)
    state: PredicateCellStateV1


class PredicateMatrixRowV1(ProductKnowledgeModelV1):
    row_id: str = Field(min_length=1)
    incident_id: str = Field(min_length=1)
    row_kind: PredicateMatrixRowKindV1
    cells: tuple[PredicateMatrixCellV1, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def require_canonical_cells(self) -> "PredicateMatrixRowV1":
        keys = tuple((cell.predicate_id, cell.source) for cell in self.cells)
        if keys != tuple(sorted(set(keys))):
            raise ValueError("predicate-matrix cells are not canonical")
        return self


class PredicateMatrixV1(ProductKnowledgeModelV1):
    schema_version: Literal["ecomsre.product.predicate-matrix.v1"]
    environment_id: str
    family_id: str
    rows: tuple[PredicateMatrixRowV1, ...]
    predicate_matrix_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_matrix(self) -> "PredicateMatrixV1":
        keys = tuple(row.row_id for row in self.rows)
        if keys != tuple(sorted(set(keys))):
            raise ValueError("predicate-matrix rows are not canonical")
        sources_by_predicate: dict[str, set[str]] = {}
        for row in self.rows:
            for cell in row.cells:
                sources_by_predicate.setdefault(cell.predicate_id, set()).add(cell.source)
        if any(len(sources) != 1 for sources in sources_by_predicate.values()):
            raise ValueError("predicate-matrix column source differs across rows")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"predicate_matrix_sha256"})
        )
        if self.predicate_matrix_sha256 != expected:
            raise ValueError("predicate-matrix digest differs")
        return self


class CandidateClauseV1(ProductKnowledgeModelV1):
    candidate_id: str = Field(min_length=1)
    predicate_ids: tuple[str, ...] = Field(min_length=1, max_length=3)
    evidence_sources: tuple[str, ...] = Field(min_length=1)
    positive_recall: float = Field(ge=0.0, le=1.0)
    false_positive_rate: float = Field(ge=0.0, le=1.0)
    core_known_overlap_rate: float = Field(ge=0.0, le=1.0)
    no_incident_false_positive_rate: float = Field(ge=0.0, le=1.0)
    score: float
    action_authority: Literal["NONE"] = "NONE"

    @property
    def predicate_count(self) -> int:
        return len(self.predicate_ids)


class CandidateClauseSetV1(ProductKnowledgeModelV1):
    schema_version: Literal["ecomsre.product.candidate-clause-set.v1"]
    environment_id: str
    family_id: str
    beam_width: int = Field(ge=1)
    items: tuple[CandidateClauseV1, ...]
    clause_set_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_clause_set(self) -> "CandidateClauseSetV1":
        ids = tuple(item.candidate_id for item in self.items)
        expected_order = tuple(
            sorted(
                self.items,
                key=lambda item: (
                    -item.score,
                    abs(len(item.evidence_sources) - 2),
                    item.predicate_count,
                    item.candidate_id,
                ),
            )
        )
        if len(ids) != len(set(ids)) or self.items != expected_order:
            raise ValueError("candidate clause set is not canonical")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"clause_set_sha256"})
        )
        if self.clause_set_sha256 != expected:
            raise ValueError("candidate clause-set digest differs")
        return self


class ClauseMiningResultV1(ProductKnowledgeModelV1):
    status: Literal[
        "CANDIDATES_READY",
        "NEEDS_MORE_INCIDENTS",
        "NEEDS_MORE_NEGATIVES",
        "NO_ACCEPTABLE_CANDIDATE",
    ]
    candidate_set: CandidateClauseSetV1

    @property
    def candidates(self) -> tuple[CandidateClauseV1, ...]:
        return self.candidate_set.items

    @property
    def beam_width(self) -> int:
        return self.candidate_set.beam_width


class ShadowCaseOutcomeV1(ProductKnowledgeModelV1):
    schema_version: Literal["ecomsre.product.shadow-case-outcome.v1"]
    case_id: str = Field(min_length=1)
    incident_id: str | None
    stratum: ShadowEvaluationStratumV1
    origin: ShadowCaseOriginV1
    runtime_input_sha256: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    expected_match: bool | None
    matched: bool | None
    evaluated_target_services: tuple[str, ...]
    supporting_evidence_refs: tuple[str, ...]
    available_evidence_refs: tuple[str, ...]
    required_sources: tuple[str, ...]
    source_reachable: bool | None
    action_authority_violations: Literal[0] = 0
    reason_code: str | None = None
    outcome_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_outcome(self) -> "ShadowCaseOutcomeV1":
        for values in (
            self.supporting_evidence_refs,
            self.available_evidence_refs,
            self.required_sources,
            self.evaluated_target_services,
        ):
            if values != tuple(sorted(set(values))):
                raise ValueError("shadow case values are not canonical")
        unavailable = self.origin is ShadowCaseOriginV1.NOT_AVAILABLE
        if unavailable:
            if any(
                value is not None
                for value in (
                    self.runtime_input_sha256,
                    self.expected_match,
                    self.matched,
                    self.source_reachable,
                )
            ) or (
                self.supporting_evidence_refs
                or self.available_evidence_refs
                or self.evaluated_target_services
            ):
                raise ValueError("unavailable shadow case carries a runtime result")
            if not self.reason_code:
                raise ValueError("unavailable shadow case lacks a reason")
        elif (
            self.runtime_input_sha256 is None
            or self.expected_match is None
            or self.matched is None
            or self.source_reachable is None
            or not self.evaluated_target_services
            or self.reason_code is not None
        ):
            raise ValueError("evaluated shadow case is incomplete")
        if not set(self.supporting_evidence_refs).issubset(
            self.available_evidence_refs
        ):
            raise ValueError("shadow case support escapes available evidence")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"outcome_sha256"})
        )
        if self.outcome_sha256 != expected:
            raise ValueError("shadow case outcome digest differs")
        return self


class ShadowEvaluationV1(ProductKnowledgeModelV1):
    schema_version: Literal["ecomsre.product.shadow-evaluation.v1"]
    evaluation_id: str
    registration_id: str
    positive_recall: float = Field(ge=0.0, le=1.0)
    false_positive_rate: float = Field(ge=0.0, le=1.0)
    core_known_overlap_rate: float = Field(ge=0.0, le=1.0)
    no_incident_false_positives: int = Field(ge=0)
    other_extension_destructive_overlaps: int = Field(ge=0)
    evidence_ref_validity: float = Field(ge=0.0, le=1.0)
    source_reachability: float = Field(ge=0.0, le=1.0)
    counterfactual_consistency: float = Field(ge=0.0, le=1.0)
    source_failure_safe: bool
    action_authority_violations: int = Field(ge=0)
    outcomes: tuple[ShadowCaseOutcomeV1, ...] = Field(min_length=1)
    runtime_evaluation_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    gate_passed: bool
    reason_codes: tuple[str, ...]
    action_authority: Literal["NONE"] = "NONE"
    evaluation_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_evaluation(self) -> "ShadowEvaluationV1":
        ids = tuple(item.case_id for item in self.outcomes)
        if ids != tuple(sorted(set(ids))):
            raise ValueError("shadow case outcomes are not canonical")
        if {item.stratum for item in self.outcomes} != set(
            ShadowEvaluationStratumV1
        ):
            raise ValueError("shadow evaluation does not cover every stratum")
        actual_runtime_sha256 = semantic_sha256_v22(
            tuple(item.outcome_sha256 for item in self.outcomes)
        )
        if self.runtime_evaluation_sha256 != actual_runtime_sha256:
            raise ValueError("shadow runtime evaluation digest differs")
        evaluated = tuple(
            item
            for item in self.outcomes
            if item.origin is not ShadowCaseOriginV1.NOT_AVAILABLE
        )
        positives = tuple(
            item
            for item in evaluated
            if item.stratum is ShadowEvaluationStratumV1.POSITIVE_INCIDENT
        )
        negatives = tuple(
            item
            for item in evaluated
            if item.stratum is not ShadowEvaluationStratumV1.POSITIVE_INCIDENT
        )
        counterfactuals = tuple(
            item
            for item in evaluated
            if item.stratum is ShadowEvaluationStratumV1.TARGET_COUNTERFACTUAL
        )
        source_failures = tuple(
            item
            for item in evaluated
            if item.stratum is ShadowEvaluationStratumV1.SOURCE_FAILURE
        )
        if not positives or not negatives or not counterfactuals or not source_failures:
            raise ValueError("shadow evaluation lacks required runtime cases")
        actual_metrics = {
            "positive_recall": sum(item.matched is True for item in positives)
            / len(positives),
            "false_positive_rate": sum(item.matched is True for item in negatives)
            / len(negatives),
            "core_known_overlap_rate": (
                sum(
                    item.matched is True
                    for item in evaluated
                    if item.stratum
                    is ShadowEvaluationStratumV1.CONFUSABLE_CORE_KNOWN
                )
                / max(
                    1,
                    sum(
                        item.stratum
                        is ShadowEvaluationStratumV1.CONFUSABLE_CORE_KNOWN
                        for item in evaluated
                    ),
                )
            ),
            "no_incident_false_positives": sum(
                item.matched is True
                for item in evaluated
                if item.stratum is ShadowEvaluationStratumV1.NO_INCIDENT
            ),
            "other_extension_destructive_overlaps": sum(
                item.matched is True
                for item in evaluated
                if item.stratum is ShadowEvaluationStratumV1.OTHER_EXTENSION
            ),
            "evidence_ref_validity": sum(
                set(item.supporting_evidence_refs).issubset(
                    item.available_evidence_refs
                )
                for item in evaluated
            )
            / len(evaluated),
            "source_reachability": sum(item.source_reachable is True for item in positives)
            / len(positives),
            "counterfactual_consistency": sum(
                item.matched is False for item in counterfactuals
            )
            / len(counterfactuals),
            "source_failure_safe": all(
                item.matched is False for item in source_failures
            ),
            "action_authority_violations": sum(
                item.action_authority_violations for item in evaluated
            ),
        }
        for field, actual in actual_metrics.items():
            if getattr(self, field) != actual:
                raise ValueError(f"shadow {field} differs from runtime outcomes")
        if self.reason_codes != tuple(sorted(set(self.reason_codes))):
            raise ValueError("shadow reason codes are not canonical")
        if self.gate_passed != (not self.reason_codes):
            raise ValueError("shadow gate differs from reason codes")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"evaluation_sha256"})
        )
        if self.evaluation_sha256 != expected:
            raise ValueError("shadow evaluation digest differs")
        return self


class ShadowEvaluationCreateV1(ProductKnowledgeModelV1):
    """Empty, closed request: Runtime derives every promotion metric."""

    simulate_human_review: Literal[False] = False


class FaultFamilyV1(ProductKnowledgeModelV1):
    schema_version: Literal["ecomsre.product.fault-family.v1"]
    family_id: str
    environment_id: str
    status: FaultFamilyStatusV1
    member_incident_ids: tuple[str, ...]
    member_fingerprint_sha256s: tuple[str, ...]
    distinct_incident_windows: int = Field(ge=0)
    root_consistency: float = Field(ge=0.0, le=1.0)
    evidence_source_diversity: int = Field(ge=0)
    merged_into_family_id: str | None = None
    created_at: datetime
    updated_at: datetime
    family_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_family(self) -> "FaultFamilyV1":
        for values in (
            self.member_incident_ids,
            self.member_fingerprint_sha256s,
        ):
            if values != tuple(sorted(set(values))):
                raise ValueError("fault-family members are not canonical")
        for value in (self.created_at, self.updated_at):
            if value.tzinfo is None or value.utcoffset() != timedelta(0):
                raise ValueError("fault-family timestamp must be UTC")
        if (self.status is FaultFamilyStatusV1.MERGED) != bool(
            self.merged_into_family_id
        ):
            raise ValueError("fault-family merge target differs from status")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"family_sha256"})
        )
        if self.family_sha256 != expected:
            raise ValueError("fault-family digest differs")
        return self


class FaultFamilyListV1(ProductKnowledgeModelV1):
    items: tuple[FaultFamilyV1, ...]


class HumanReviewCreateV1(ProductKnowledgeModelV1):
    decision: ReviewDecisionV1
    reviewer: str = Field(min_length=1, max_length=160)
    note: str = Field(min_length=1, max_length=2000)
    reviewed_at: datetime
    merge_target_family_id: str | None = None

    @model_validator(mode="after")
    def require_review(self) -> "HumanReviewCreateV1":
        if self.reviewed_at.tzinfo is None or self.reviewed_at.utcoffset() != timedelta(0):
            raise ValueError("review timestamp must be UTC")
        if (self.decision is ReviewDecisionV1.MERGE_WITH_EXISTING) != bool(
            self.merge_target_family_id
        ):
            raise ValueError("merge review target differs from decision")
        return self


class HumanReviewV1(HumanReviewCreateV1):
    schema_version: Literal["ecomsre.product.human-review.v1"]
    review_id: str
    family_id: str
    review_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_review_digest(self) -> "HumanReviewV1":
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"review_sha256"})
        )
        if self.review_sha256 != expected:
            raise ValueError("human-review digest differs")
        return self


class FaultFamilyMergeV1(ProductKnowledgeModelV1):
    target_family_id: str
    reviewer: str = Field(min_length=1, max_length=160)
    note: str = Field(min_length=1, max_length=2000)
    merged_at: datetime

    @model_validator(mode="after")
    def require_merge_timestamp(self) -> "FaultFamilyMergeV1":
        if self.merged_at.tzinfo is None or self.merged_at.utcoffset() != timedelta(0):
            raise ValueError("family merge timestamp must be UTC")
        return self


class RegistrationDraftCreateV1(ProductKnowledgeModelV1):
    human_review_id: str
    human_canonical_label: str = Field(min_length=1, max_length=160)
    llm_explanation: str = Field(min_length=1, max_length=2000)
    unresolved_gaps: tuple[str, ...] = ()


class FamilyRegistrationDraftV1(ProductKnowledgeModelV1):
    schema_version: Literal["ecomsre.product.family-registration-draft.v1"]
    registration_id: str
    environment_id: str
    family_id: str
    human_review_id: str
    human_canonical_label: str
    broad_domain: str
    positive_incident_ids: tuple[str, ...]
    negative_incident_ids: tuple[str, ...]
    predicate_matrix_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    candidate_clauses: tuple[CandidateClauseV1, ...]
    selected_candidate_id: str | None
    llm_explanation: str
    unresolved_gaps: tuple[str, ...]
    implementation_mode: RegistrationImplementationModeV1
    remediation_registration: Literal["NOT_INCLUDED"] = "NOT_INCLUDED"
    action_authority: Literal["NONE"] = "NONE"
    provider_calls: Literal[0] = 0
    created_at: datetime
    draft_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_draft(self) -> "FamilyRegistrationDraftV1":
        for values in (
            self.positive_incident_ids,
            self.negative_incident_ids,
            self.unresolved_gaps,
        ):
            if values != tuple(sorted(set(values))):
                raise ValueError("registration draft sets are not canonical")
        selected = {item.candidate_id for item in self.candidate_clauses}
        if (self.selected_candidate_id in selected) != (
            self.implementation_mode is RegistrationImplementationModeV1.DECLARATIVE_READY
        ):
            raise ValueError("registration draft selection differs from mode")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"draft_sha256"})
        )
        if self.draft_sha256 != expected:
            raise ValueError("registration draft digest differs")
        return self


class ProductRegistrationValidationV1(ProductKnowledgeModelV1):
    """Product semantic binding before adapting into the frozen DTA runtime."""

    schema_version: Literal["ecomsre.product.registration-validation.v1"]
    registration_id: str
    draft_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    predicate_matrix_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    shadow_evaluation_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    core_ontology_snapshot_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    selected_candidate_id: str
    selected_predicate_ids: tuple[str, ...] = Field(min_length=1, max_length=3)
    status: Literal["VALID"]
    error_codes: tuple[str, ...] = ()
    action_authority: Literal["NONE"] = "NONE"
    validation_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_validation(self) -> "ProductRegistrationValidationV1":
        if self.selected_predicate_ids != tuple(
            sorted(set(self.selected_predicate_ids))
        ):
            raise ValueError("registration validation predicates are not canonical")
        if self.error_codes:
            raise ValueError("valid Product registration carries semantic errors")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"validation_sha256"})
        )
        if self.validation_sha256 != expected:
            raise ValueError("Product registration validation digest differs")
        return self


class PromotionCreateV1(ProductKnowledgeModelV1):
    shadow_evaluation_id: str
    reviewer: str = Field(min_length=1, max_length=160)
    note: str = Field(min_length=1, max_length=2000)
    promoted_at: datetime

    @model_validator(mode="after")
    def require_promotion_timestamp(self) -> "PromotionCreateV1":
        if self.promoted_at.tzinfo is None or self.promoted_at.utcoffset() != timedelta(0):
            raise ValueError("promotion timestamp must be UTC")
        return self


class PromotionRecordV1(PromotionCreateV1):
    schema_version: Literal["ecomsre.product.registration-promotion.v1"]
    promotion_id: str
    registration_id: str
    environment_id: str
    registry_version: int = Field(ge=1)
    status: Literal["ACTIVE"] = "ACTIVE"
    action_authority: Literal["NONE"] = "NONE"
    promotion_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_promotion_digest(self) -> "PromotionRecordV1":
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"promotion_sha256"})
        )
        if self.promotion_sha256 != expected:
            raise ValueError("promotion digest differs")
        return self


class RevocationCreateV1(ProductKnowledgeModelV1):
    reviewer: str = Field(min_length=1, max_length=160)
    note: str = Field(min_length=1, max_length=2000)
    revoked_at: datetime

    @model_validator(mode="after")
    def require_revocation_timestamp(self) -> "RevocationCreateV1":
        if self.revoked_at.tzinfo is None or self.revoked_at.utcoffset() != timedelta(0):
            raise ValueError("revocation timestamp must be UTC")
        return self


class RevocationRecordV1(RevocationCreateV1):
    schema_version: Literal["ecomsre.product.registration-revocation.v1"]
    revocation_id: str
    registration_id: str
    environment_id: str
    prior_registry_version: int = Field(ge=1)
    status: Literal["REVOKED"] = "REVOKED"
    action_authority: Literal["NONE"] = "NONE"
    revocation_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_revocation_digest(self) -> "RevocationRecordV1":
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"revocation_sha256"})
        )
        if self.revocation_sha256 != expected:
            raise ValueError("revocation digest differs")
        return self


class EnvironmentExtensionRegistryEntryV1(ProductKnowledgeModelV1):
    schema_version: Literal["ecomsre.product.environment-extension-registry-entry.v1"]
    registration_id: str
    compiled_registration_id: str
    environment_id: str
    family_id: str
    mechanism_enum_name: str
    mechanism_slug: str
    mechanism_display_name: str
    human_canonical_label: str
    broad_domain: str
    compiled_predicates: tuple[ExtensionPredicateDefinitionV234, ...]
    compiled_dnf_clauses: tuple[ExtensionSupportClauseV234, ...]
    compiled_registration: CompiledFaultRegistrationV234
    source_draft_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_human_review_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    shadow_evaluation_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    promotion_review: PromotionRecordV1
    revocation_review: RevocationRecordV1 | None = None
    registry_version: int = Field(ge=1)
    status: Literal["ACTIVE", "REVOKED"]
    action_authority: Literal["NONE"] = "NONE"
    remediation_authority: Literal["NONE"] = "NONE"
    created_at: datetime
    updated_at: datetime
    entry_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_registry_entry(self) -> "EnvironmentExtensionRegistryEntryV1":
        for value in (self.created_at, self.updated_at):
            if value.tzinfo is None or value.utcoffset() != timedelta(0):
                raise ValueError("registry timestamp must be UTC")
        compiled = self.compiled_registration
        mechanism = compiled.mechanism
        if (
            self.compiled_registration_id != compiled.registration_id
            or self.mechanism_enum_name != mechanism.mechanism_enum_name
            or self.mechanism_slug != mechanism.mechanism_slug
            or self.mechanism_display_name != mechanism.display_name
            or self.broad_domain != mechanism.broad_fault_domain.value
            or self.compiled_predicates != compiled.predicates
            or self.compiled_dnf_clauses != compiled.support_clauses
            or self.source_draft_sha256 != compiled.source_draft_sha256
            or compiled.action_authority != "NONE"
            or compiled.remediation_registration != "NOT_INCLUDED"
        ):
            raise ValueError("registry entry differs from compiled registration")
        if (
            self.promotion_review.registration_id != self.registration_id
            or self.promotion_review.environment_id != self.environment_id
            or self.promotion_review.registry_version > self.registry_version
        ):
            raise ValueError("registry entry differs from promotion review")
        revoked = self.status == "REVOKED"
        if revoked != (self.revocation_review is not None):
            raise ValueError("registry revocation review differs from status")
        if self.revocation_review is not None and (
            self.revocation_review.registration_id != self.registration_id
            or self.revocation_review.environment_id != self.environment_id
            or self.revocation_review.prior_registry_version
            != self.promotion_review.registry_version
        ):
            raise ValueError("registry entry differs from revocation review")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"entry_sha256"})
        )
        if self.entry_sha256 != expected:
            raise ValueError("registry entry digest differs")
        return self


__all__ = (
    "CandidateClauseV1",
    "CandidateClauseSetV1",
    "ClauseMiningResultV1",
    "EnvironmentExtensionRegistryEntryV1",
    "FaultFamilyStatusV1",
    "FaultFamilyListV1",
    "FaultFamilyMergeV1",
    "FaultFamilyV1",
    "FamilyRegistrationDraftV1",
    "FingerprintObservationV1",
    "IncidentFingerprintV1",
    "HumanReviewCreateV1",
    "HumanReviewV1",
    "PredicateCellStateV1",
    "PredicateMatrixCellV1",
    "PredicateMatrixRowKindV1",
    "PredicateMatrixRowV1",
    "PredicateMatrixV1",
    "RegistrationImplementationModeV1",
    "RegistrationDraftCreateV1",
    "PromotionCreateV1",
    "PromotionRecordV1",
    "ProductRegistrationValidationV1",
    "RevocationCreateV1",
    "RevocationRecordV1",
    "ReviewDecisionV1",
    "ShadowEvaluationV1",
    "ShadowEvaluationCreateV1",
    "ShadowCaseOriginV1",
    "ShadowCaseOutcomeV1",
    "ShadowEvaluationStratumV1",
)
