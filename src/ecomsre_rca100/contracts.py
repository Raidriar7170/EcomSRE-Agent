"""Strict label-blind contracts for the RCA100 external holdout."""

from __future__ import annotations

from enum import Enum
import re
from typing import Literal

from pydantic import (
    ConfigDict,
    Field,
    StrictBool,
    StrictFloat,
    StrictInt,
    field_validator,
    model_validator,
)

from ecomsre_rcaeval_adaptive.metrics_arbitration import (
    MetricsArbitrationPolicy,
    MetricsServiceRank,
    decide_metrics_arbitration,
)
from ecomsre_rcaeval_v2.contracts import V2Model


EntityRef = str
EvidenceRef = str
_ENTITY_REF = re.compile(r"^(apm|k8s)\|[a-z0-9._-]+\|[^|\s]{1,512}$")
_EVIDENCE_REF = re.compile(r"^(metric|log|trace):[0-9]{4}$")
_METRIC_REF = re.compile(r"^metric:[0-9]{4}$")


class RCA100Model(V2Model):
    """RCA100 base model with immutable, extra-forbidden values."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class CanonicalRCA100Entity(RCA100Model):
    entity_ref: EntityRef = Field(min_length=5, max_length=768)
    domain: Literal["apm", "k8s"]
    type: str = Field(pattern=r"^(apm|k8s)\.[a-z0-9._-]+$", max_length=128)
    entity_id: str = Field(min_length=1, max_length=512)
    entity_name: str = Field(min_length=1, max_length=512)
    normalized_name: str = Field(min_length=1, max_length=512)
    parent_service_ref_or_none: EntityRef | None = Field(default=None, max_length=768)
    same_as_refs: tuple[EntityRef, ...] = Field(default=(), max_length=32)

    @model_validator(mode="after")
    def require_exact_reference(self) -> CanonicalRCA100Entity:
        expected = f"{self.domain}|{self.type}|{self.entity_id}"
        if self.entity_ref != expected or not self.type.startswith(f"{self.domain}."):
            raise ValueError("canonical entity reference differs from typed identity")
        if _ENTITY_REF.fullmatch(self.entity_ref) is None:
            raise ValueError("canonical entity reference has an invalid shape")
        if self.parent_service_ref_or_none is not None and _ENTITY_REF.fullmatch(
            self.parent_service_ref_or_none
        ) is None:
            raise ValueError("canonical entity parent reference is invalid")
        if len(set(self.same_as_refs)) != len(self.same_as_refs) or any(
            _ENTITY_REF.fullmatch(item) is None or item == self.entity_ref
            for item in self.same_as_refs
        ):
            raise ValueError("canonical entity same-as references are invalid")
        return self


class RCA100ReasoningStep(RCA100Model):
    claim: str = Field(min_length=1, max_length=1_000)
    entity_ref_or_none: EntityRef | None = Field(default=None, max_length=768)
    evidence_refs: tuple[EvidenceRef, ...] = Field(default=(), max_length=18)

    @model_validator(mode="after")
    def require_bounded_references(self) -> RCA100ReasoningStep:
        if self.entity_ref_or_none is not None and _ENTITY_REF.fullmatch(
            self.entity_ref_or_none
        ) is None:
            raise ValueError("reasoning step entity reference is invalid")
        if len(set(self.evidence_refs)) != len(self.evidence_refs) or any(
            _EVIDENCE_REF.fullmatch(item) is None for item in self.evidence_refs
        ):
            raise ValueError("reasoning step evidence references are invalid")
        return self


class RCA100InitialDiagnosis(RCA100Model):
    root_cause_entity_ref: EntityRef = Field(min_length=5, max_length=768)
    fault_type: str = Field(min_length=1, max_length=256)
    confidence: StrictFloat = Field(ge=0.0, le=1.0)
    evidence_refs: tuple[EvidenceRef, ...] = Field(min_length=1, max_length=18)
    reasoning_steps: tuple[RCA100ReasoningStep, ...] = Field(
        min_length=1, max_length=12
    )
    summary: str = Field(min_length=1, max_length=2_000)

    @field_validator("fault_type")
    @classmethod
    def normalize_fault_type_surface(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("fault type must not be blank")
        return stripped

    @model_validator(mode="after")
    def require_bounded_output(self) -> RCA100InitialDiagnosis:
        if _ENTITY_REF.fullmatch(self.root_cause_entity_ref) is None:
            raise ValueError("Initial root entity reference is invalid")
        if len(set(self.evidence_refs)) != len(self.evidence_refs) or any(
            _EVIDENCE_REF.fullmatch(item) is None for item in self.evidence_refs
        ):
            raise ValueError("Initial evidence references are invalid")
        return self


class RCA100FinalDiagnosis(RCA100Model):
    root_cause_entity_ref: EntityRef = Field(min_length=5, max_length=768)
    fault_type: str = Field(min_length=1, max_length=256)
    confidence: StrictFloat | None = Field(default=None, ge=0.0, le=1.0)
    evidence_refs: tuple[EvidenceRef, ...] = Field(min_length=1, max_length=18)
    reasoning_steps: tuple[RCA100ReasoningStep, ...] = Field(
        min_length=1, max_length=13
    )
    summary: str = Field(min_length=1, max_length=2_000)


class RCA100MetricsEntityRank(RCA100Model):
    entity_ref: EntityRef = Field(min_length=5, max_length=768)
    rank: StrictInt = Field(ge=1, le=6)
    score: StrictFloat
    supporting_metrics_evidence_refs: tuple[EvidenceRef, ...] = Field(
        min_length=1, max_length=18
    )

    @model_validator(mode="after")
    def require_metrics_references(self) -> RCA100MetricsEntityRank:
        if _ENTITY_REF.fullmatch(self.entity_ref) is None:
            raise ValueError("Metrics ranking entity reference is invalid")
        if len(set(self.supporting_metrics_evidence_refs)) != len(
            self.supporting_metrics_evidence_refs
        ) or any(
            _METRIC_REF.fullmatch(item) is None
            for item in self.supporting_metrics_evidence_refs
        ):
            raise ValueError("Metrics references are invalid")
        return self


class RCA100MetricsArbitrationAction(str, Enum):
    KEEP_INITIAL = "KEEP_INITIAL"
    OVERRIDE_METRICS_TOP1 = "OVERRIDE_METRICS_TOP1"


RCA100MetricsArbitrationReason = Literal[
    "M3_OVERRIDE_RANK_AND_MARGIN",
    "KEEP_INITIAL_TOP1_MATCH",
    "KEEP_INITIAL_RANK_CONDITION_NOT_MET",
    "KEEP_INITIAL_MARGIN_CONDITION_NOT_MET",
    "METRICS_PROJECTION_UNAVAILABLE",
]


class RCA100MetricsArbitrationDecision(RCA100Model):
    schema_version: Literal["rca100.metrics-arbitration-decision.v1"] = (
        "rca100.metrics-arbitration-decision.v1"
    )
    action: RCA100MetricsArbitrationAction
    initial_root_entity_ref: EntityRef
    final_root_entity_ref: EntityRef
    initial_metrics_rank_or_none: StrictInt | None = Field(default=None, ge=1, le=6)
    metrics_top1_entity_ref: EntityRef | None = None
    metrics_top2_entity_ref_or_none: EntityRef | None = None
    metrics_top1_score: StrictFloat | None = None
    metrics_top2_score_or_none: StrictFloat | None = None
    normalized_margin: StrictFloat | None = None
    rank_condition_passed: StrictBool
    margin_condition_passed: StrictBool
    reason_codes: tuple[RCA100MetricsArbitrationReason, ...] = Field(
        min_length=1, max_length=2
    )
    supporting_metrics_evidence_refs: tuple[EvidenceRef, ...] = Field(
        default=(), max_length=18
    )

    @model_validator(mode="after")
    def require_exact_m3(self) -> RCA100MetricsArbitrationDecision:
        unavailable = self.reason_codes == ("METRICS_PROJECTION_UNAVAILABLE",)
        if unavailable:
            if any(
                value is not None
                for value in (
                    self.initial_metrics_rank_or_none,
                    self.metrics_top1_entity_ref,
                    self.metrics_top2_entity_ref_or_none,
                    self.metrics_top1_score,
                    self.metrics_top2_score_or_none,
                    self.normalized_margin,
                )
            ) or self.supporting_metrics_evidence_refs:
                raise ValueError("unavailable Metrics decision retained ranking data")
            if (
                self.action is not RCA100MetricsArbitrationAction.KEEP_INITIAL
                or self.final_root_entity_ref != self.initial_root_entity_ref
                or self.rank_condition_passed
                or self.margin_condition_passed
            ):
                raise ValueError("unavailable Metrics decision did not keep Initial")
            return self
        if (
            self.metrics_top1_entity_ref is None
            or self.metrics_top1_score is None
            or self.normalized_margin is None
        ):
            raise ValueError("available Metrics decision lacks top-one data")
        rank_passed = (
            self.initial_metrics_rank_or_none is None
            or self.initial_metrics_rank_or_none > 2
        )
        margin_passed = self.normalized_margin >= 0.25
        if self.rank_condition_passed is not rank_passed:
            raise ValueError("M3 rank condition differs from Initial rank")
        if self.margin_condition_passed is not margin_passed:
            raise ValueError("M3 margin condition differs from normalized margin")
        override = bool(
            self.initial_root_entity_ref != self.metrics_top1_entity_ref
            and rank_passed
            and margin_passed
        )
        if (self.action is RCA100MetricsArbitrationAction.OVERRIDE_METRICS_TOP1) is not override:
            raise ValueError("M3 action differs from the frozen exact rule")
        expected_final = (
            self.metrics_top1_entity_ref if override else self.initial_root_entity_ref
        )
        if self.final_root_entity_ref != expected_final:
            raise ValueError("M3 final root differs from its action")
        if not self.supporting_metrics_evidence_refs or any(
            _METRIC_REF.fullmatch(item) is None
            for item in self.supporting_metrics_evidence_refs
        ):
            raise ValueError("M3 decision requires Metrics references")
        return self


class RCA100DiagnosisProvenance(str, Enum):
    MODEL_INITIAL = "MODEL_INITIAL"
    DETERMINISTIC_METRICS_M3 = "DETERMINISTIC_METRICS_M3"


class RCA100ArbitratedDiagnosis(RCA100Model):
    schema_version: Literal["rca100.arbitrated-diagnosis.v1"] = (
        "rca100.arbitrated-diagnosis.v1"
    )
    initial_diagnosis: RCA100InitialDiagnosis
    arbitration_decision: RCA100MetricsArbitrationDecision
    final_diagnosis: RCA100InitialDiagnosis | RCA100FinalDiagnosis
    root_provenance: RCA100DiagnosisProvenance
    fault_type_provenance: Literal["MODEL_INITIAL"] = "MODEL_INITIAL"

    @model_validator(mode="after")
    def require_root_only_change(self) -> RCA100ArbitratedDiagnosis:
        if self.final_diagnosis.fault_type != self.initial_diagnosis.fault_type:
            raise ValueError("M3 changed the Initial fault type")
        if (
            self.final_diagnosis.root_cause_entity_ref
            != self.arbitration_decision.final_root_entity_ref
        ):
            raise ValueError("final diagnosis root differs from M3")
        if self.arbitration_decision.action is RCA100MetricsArbitrationAction.KEEP_INITIAL:
            if self.final_diagnosis != self.initial_diagnosis:
                raise ValueError("M3 KEEP did not preserve the exact Initial")
            if self.root_provenance is not RCA100DiagnosisProvenance.MODEL_INITIAL:
                raise ValueError("M3 KEEP root provenance differs")
        elif self.root_provenance is not RCA100DiagnosisProvenance.DETERMINISTIC_METRICS_M3:
            raise ValueError("M3 override root provenance differs")
        return self


def _validate_ranking(
    ranking: tuple[RCA100MetricsEntityRank, ...],
) -> tuple[RCA100MetricsEntityRank, ...]:
    if not ranking or len(ranking) > 6:
        raise ValueError("RCA100 Metrics ranking must contain one to six entities")
    if tuple(item.rank for item in ranking) != tuple(range(1, len(ranking) + 1)):
        raise ValueError("RCA100 Metrics ranking ranks are not contiguous")
    if len({item.entity_ref for item in ranking}) != len(ranking):
        raise ValueError("RCA100 Metrics ranking contains duplicate entities")
    if any(left.score < right.score for left, right in zip(ranking, ranking[1:])):
        raise ValueError("RCA100 Metrics ranking is not score ordered")
    return ranking


def decide_rca100_metrics_arbitration(
    *,
    initial_root_entity_ref: EntityRef,
    ranking: tuple[RCA100MetricsEntityRank, ...],
) -> RCA100MetricsArbitrationDecision:
    """Apply the existing PR #21 M3 implementation through stable opaque aliases."""

    if not ranking:
        return RCA100MetricsArbitrationDecision(
            action=RCA100MetricsArbitrationAction.KEEP_INITIAL,
            initial_root_entity_ref=initial_root_entity_ref,
            final_root_entity_ref=initial_root_entity_ref,
            rank_condition_passed=False,
            margin_condition_passed=False,
            reason_codes=("METRICS_PROJECTION_UNAVAILABLE",),
            supporting_metrics_evidence_refs=(),
        )
    values = _validate_ranking(ranking)
    refs = tuple(sorted({initial_root_entity_ref, *(item.entity_ref for item in values)}))
    aliases = {entity_ref: f"e{index:04d}" for index, entity_ref in enumerate(refs, 1)}
    reverse = {alias: entity_ref for entity_ref, alias in aliases.items()}
    decision = decide_metrics_arbitration(
        initial_root_service=aliases[initial_root_entity_ref],
        ranking=tuple(
            MetricsServiceRank(
                service=aliases[item.entity_ref],
                rank=item.rank,
                score=item.score,
                supporting_metrics_evidence_refs=item.supporting_metrics_evidence_refs,
            )
            for item in values
        ),
        policy=MetricsArbitrationPolicy(),
    )
    return RCA100MetricsArbitrationDecision(
        action=RCA100MetricsArbitrationAction(decision.action.value),
        initial_root_entity_ref=reverse[decision.initial_root_service],
        final_root_entity_ref=reverse[decision.final_root_service],
        initial_metrics_rank_or_none=decision.initial_metrics_rank_or_none,
        metrics_top1_entity_ref=reverse[decision.metrics_top1_service],
        metrics_top2_entity_ref_or_none=(
            None
            if decision.metrics_top2_service_or_none is None
            else reverse[decision.metrics_top2_service_or_none]
        ),
        metrics_top1_score=decision.metrics_top1_score,
        metrics_top2_score_or_none=decision.metrics_top2_score_or_none,
        normalized_margin=decision.normalized_margin,
        rank_condition_passed=decision.rank_condition_passed,
        margin_condition_passed=decision.margin_condition_passed,
        reason_codes=decision.reason_codes,
        supporting_metrics_evidence_refs=decision.supporting_metrics_evidence_refs,
    )


def arbitrate_rca100_diagnosis(
    initial: RCA100InitialDiagnosis,
    ranking: tuple[RCA100MetricsEntityRank, ...],
) -> RCA100ArbitratedDiagnosis:
    decision = decide_rca100_metrics_arbitration(
        initial_root_entity_ref=initial.root_cause_entity_ref,
        ranking=ranking,
    )
    if decision.action is RCA100MetricsArbitrationAction.KEEP_INITIAL:
        final: RCA100InitialDiagnosis | RCA100FinalDiagnosis = initial
        provenance = RCA100DiagnosisProvenance.MODEL_INITIAL
    else:
        step = RCA100ReasoningStep(
            claim=(
                "Deterministic M3 changed only the root entity because the Initial "
                "entity was outside the Metrics top two and the normalized "
                "top-one/top-two margin was at least 0.25."
            ),
            entity_ref_or_none=decision.final_root_entity_ref,
            evidence_refs=decision.supporting_metrics_evidence_refs,
        )
        final = RCA100FinalDiagnosis(
            root_cause_entity_ref=decision.final_root_entity_ref,
            fault_type=initial.fault_type,
            confidence=None,
            evidence_refs=decision.supporting_metrics_evidence_refs,
            reasoning_steps=(*initial.reasoning_steps, step),
            summary=(
                "The Initial fault type was preserved; deterministic M3 changed "
                "only the root entity using run-visible Metrics evidence."
            ),
        )
        provenance = RCA100DiagnosisProvenance.DETERMINISTIC_METRICS_M3
    return RCA100ArbitratedDiagnosis(
        initial_diagnosis=initial,
        arbitration_decision=decision,
        final_diagnosis=final,
        root_provenance=provenance,
    )


__all__ = [
    "CanonicalRCA100Entity",
    "RCA100ArbitratedDiagnosis",
    "RCA100FinalDiagnosis",
    "RCA100InitialDiagnosis",
    "RCA100MetricsArbitrationDecision",
    "RCA100MetricsEntityRank",
    "RCA100ReasoningStep",
    "arbitrate_rca100_diagnosis",
    "decide_rca100_metrics_arbitration",
]
