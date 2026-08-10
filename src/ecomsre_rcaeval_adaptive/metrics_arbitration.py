"""Deterministic root-only Metrics M3 arbitration contracts."""

from __future__ import annotations

from enum import Enum
import re
from typing import Literal, Sequence

from pydantic import Field, StrictBool, StrictFloat, StrictInt, model_validator

from ecomsre_rcaeval.contracts import Diagnosis
from ecomsre_rcaeval_v2.contracts import V2Model


_METRIC_REFERENCE = re.compile(r"^metric:[0-9]{4}$")


class MetricsArbitrationAction(str, Enum):
    KEEP_INITIAL = "KEEP_INITIAL"
    OVERRIDE_METRICS_TOP1 = "OVERRIDE_METRICS_TOP1"


class DiagnosisProvenance(str, Enum):
    MODEL_INITIAL = "MODEL_INITIAL"
    DETERMINISTIC_METRICS_M3 = "DETERMINISTIC_METRICS_M3"


MetricsArbitrationReason = Literal[
    "M3_OVERRIDE_RANK_AND_MARGIN",
    "KEEP_INITIAL_TOP1_MATCH",
    "KEEP_INITIAL_RANK_CONDITION_NOT_MET",
    "KEEP_INITIAL_MARGIN_CONDITION_NOT_MET",
]


class MetricsArbitrationPolicy(V2Model):
    schema_version: Literal[
        "rcaeval-metrics-arbitration.policy.v1"
    ] = "rcaeval-metrics-arbitration.policy.v1"
    rule: Literal["M3"] = "M3"
    initial_rank_override_min_exclusive: Literal[2] = 2
    normalized_margin_min: StrictFloat = Field(default=0.25, ge=0.25, le=0.25)
    preserve_initial_indicator: Literal[True] = True
    semantic_model_calls: Literal[1] = 1
    specialists_enabled: Literal[False] = False
    fusion_model_enabled: Literal[False] = False


class MetricsServiceRank(V2Model):
    service: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,127}$")
    rank: StrictInt = Field(ge=1, le=6)
    score: StrictFloat
    supporting_metrics_evidence_refs: tuple[str, ...] = Field(
        max_length=64
    )

    @model_validator(mode="after")
    def require_metrics_references(self) -> MetricsServiceRank:
        if len(set(self.supporting_metrics_evidence_refs)) != len(
            self.supporting_metrics_evidence_refs
        ):
            raise ValueError("Metrics service rank contains duplicate references")
        if any(
            _METRIC_REFERENCE.fullmatch(reference) is None
            for reference in self.supporting_metrics_evidence_refs
        ):
            raise ValueError("Metrics service rank requires Metrics references")
        return self


class MetricsArbitrationDecision(V2Model):
    schema_version: Literal[
        "rcaeval-metrics-arbitration.decision.v1"
    ] = "rcaeval-metrics-arbitration.decision.v1"
    action: MetricsArbitrationAction
    initial_root_service: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,127}$")
    final_root_service: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,127}$")
    initial_metrics_rank_or_none: StrictInt | None = Field(default=None, ge=1, le=6)
    metrics_top1_service: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,127}$")
    metrics_top2_service_or_none: str | None = Field(
        default=None, pattern=r"^[a-z0-9][a-z0-9-]{0,127}$"
    )
    metrics_top1_score: StrictFloat
    metrics_top2_score_or_none: StrictFloat | None = None
    normalized_margin: StrictFloat
    rank_condition_passed: StrictBool
    margin_condition_passed: StrictBool
    reason_codes: tuple[MetricsArbitrationReason, ...] = Field(
        min_length=1, max_length=2
    )
    supporting_metrics_evidence_refs: tuple[str, ...] = Field(
        min_length=1, max_length=64
    )

    @model_validator(mode="after")
    def require_exact_m3_decision(self) -> MetricsArbitrationDecision:
        expected_rank = (
            self.initial_metrics_rank_or_none is None
            or self.initial_metrics_rank_or_none > 2
        )
        expected_margin = self.normalized_margin >= 0.25
        if self.rank_condition_passed is not expected_rank:
            raise ValueError("M3 rank condition differs from Initial rank")
        if self.margin_condition_passed is not expected_margin:
            raise ValueError("M3 margin condition differs from normalized margin")
        if self.metrics_top2_service_or_none is None:
            if self.metrics_top2_score_or_none is not None or self.normalized_margin != 1.0:
                raise ValueError("single-service Metrics margin must equal one")
        elif self.metrics_top2_score_or_none is None:
            raise ValueError("Metrics Top-2 service lacks its score")
        if len(set(self.supporting_metrics_evidence_refs)) != len(
            self.supporting_metrics_evidence_refs
        ) or any(
            _METRIC_REFERENCE.fullmatch(reference) is None
            for reference in self.supporting_metrics_evidence_refs
        ):
            raise ValueError("M3 decision references must be unique Metrics refs")
        override = self.action is MetricsArbitrationAction.OVERRIDE_METRICS_TOP1
        expected_override = bool(
            self.initial_root_service != self.metrics_top1_service
            and expected_rank
            and expected_margin
        )
        if override is not expected_override:
            raise ValueError("M3 action differs from exact rule")
        expected_final = (
            self.metrics_top1_service if override else self.initial_root_service
        )
        if self.final_root_service != expected_final:
            raise ValueError("M3 final Root differs from action")
        if override:
            expected_reasons: tuple[MetricsArbitrationReason, ...] = (
                "M3_OVERRIDE_RANK_AND_MARGIN",
            )
        elif self.metrics_top1_service == self.initial_root_service:
            expected_reasons = ("KEEP_INITIAL_TOP1_MATCH",)
        else:
            unmet_reasons: list[MetricsArbitrationReason] = []
            if not expected_rank:
                unmet_reasons.append("KEEP_INITIAL_RANK_CONDITION_NOT_MET")
            if not expected_margin:
                unmet_reasons.append("KEEP_INITIAL_MARGIN_CONDITION_NOT_MET")
            expected_reasons = tuple(unmet_reasons)
        if self.reason_codes != expected_reasons:
            raise ValueError("M3 reason codes differ from exact decision")
        return self


class MetricsArbitratedDiagnosis(V2Model):
    schema_version: Literal[
        "rcaeval-metrics-arbitration.diagnosis.v1"
    ] = "rcaeval-metrics-arbitration.diagnosis.v1"
    initial_diagnosis: Diagnosis
    arbitration_decision: MetricsArbitrationDecision
    final_diagnosis: Diagnosis
    final_root_service: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,127}$")
    final_indicator: Literal["cpu", "mem", "diskio", "latency", "socket"]
    root_provenance: DiagnosisProvenance
    indicator_provenance: DiagnosisProvenance
    initial_explanation: str = Field(min_length=1, max_length=2_000)
    arbitration_explanation: str = Field(min_length=1, max_length=2_000)

    @model_validator(mode="after")
    def require_root_only_arbitration(self) -> MetricsArbitratedDiagnosis:
        if self.arbitration_decision.initial_root_service != (
            self.initial_diagnosis.root_cause_service
        ):
            raise ValueError("M3 decision differs from Initial Diagnosis")
        if self.initial_explanation != self.initial_diagnosis.explanation:
            raise ValueError("M3 result did not preserve Initial explanation")
        if self.final_indicator != self.initial_diagnosis.root_cause_indicator:
            raise ValueError("M3 changed the Initial indicator")
        if self.indicator_provenance is not DiagnosisProvenance.MODEL_INITIAL:
            raise ValueError("M3 Indicator provenance must remain model Initial")
        if self.final_root_service != self.arbitration_decision.final_root_service:
            raise ValueError("M3 final Root differs from decision")
        if (
            self.final_diagnosis.root_cause_service != self.final_root_service
            or self.final_diagnosis.root_cause_indicator != self.final_indicator
        ):
            raise ValueError("M3 compatibility Diagnosis differs from final fields")
        override = (
            self.arbitration_decision.action
            is MetricsArbitrationAction.OVERRIDE_METRICS_TOP1
        )
        if not override:
            if self.final_diagnosis != self.initial_diagnosis:
                raise ValueError("M3 KEEP must preserve the exact Initial Diagnosis")
            if self.root_provenance is not DiagnosisProvenance.MODEL_INITIAL:
                raise ValueError("M3 KEEP Root provenance differs")
        else:
            if (
                self.root_provenance
                is not DiagnosisProvenance.DETERMINISTIC_METRICS_M3
                or self.final_diagnosis.evidence_refs
                != self.arbitration_decision.supporting_metrics_evidence_refs
                or self.final_diagnosis.explanation != self.arbitration_explanation
                or self.final_diagnosis.confidence is not None
            ):
                raise ValueError("M3 override evidence or provenance differs")
        return self


def normalized_metrics_margin(
    top1_score: float, top2_score_or_none: float | None, *, epsilon: float = 1e-12
) -> float:
    if top2_score_or_none is None:
        return 1.0
    if epsilon <= 0:
        raise ValueError("Metrics margin epsilon must be positive")
    return float(
        (top1_score - top2_score_or_none) / max(abs(top1_score), epsilon)
    )


def _validate_ranking(
    ranking: Sequence[MetricsServiceRank],
) -> tuple[MetricsServiceRank, ...]:
    values = tuple(ranking)
    if not values or len(values) > 6:
        raise ValueError("M3 requires one to six ranked Metrics services")
    if tuple(item.rank for item in values) != tuple(range(1, len(values) + 1)):
        raise ValueError("M3 Metrics service ranks must be contiguous")
    if len({item.service for item in values}) != len(values):
        raise ValueError("M3 Metrics service ranking contains duplicates")
    if any(left.score < right.score for left, right in zip(values, values[1:])):
        raise ValueError("M3 Metrics service ranking is not score ordered")
    return values


def decide_metrics_arbitration(
    *,
    initial_root_service: str,
    ranking: Sequence[MetricsServiceRank],
    policy: MetricsArbitrationPolicy,
) -> MetricsArbitrationDecision:
    values = _validate_ranking(ranking)
    top1 = values[0]
    top2 = values[1] if len(values) > 1 else None
    initial_rank = next(
        (item.rank for item in values if item.service == initial_root_service), None
    )
    margin = normalized_metrics_margin(
        top1.score, None if top2 is None else top2.score
    )
    rank_passed = initial_rank is None or initial_rank > (
        policy.initial_rank_override_min_exclusive
    )
    margin_passed = margin >= policy.normalized_margin_min
    override = bool(
        top1.service != initial_root_service and rank_passed and margin_passed
    )
    if not top1.supporting_metrics_evidence_refs:
        raise ValueError("Metrics Top-1 lacks legal supporting evidence")
    reasons: list[MetricsArbitrationReason] = []
    if override:
        reasons.append("M3_OVERRIDE_RANK_AND_MARGIN")
    elif top1.service == initial_root_service:
        reasons.append("KEEP_INITIAL_TOP1_MATCH")
    else:
        if not rank_passed:
            reasons.append("KEEP_INITIAL_RANK_CONDITION_NOT_MET")
        if not margin_passed:
            reasons.append("KEEP_INITIAL_MARGIN_CONDITION_NOT_MET")
    return MetricsArbitrationDecision(
        action=(
            MetricsArbitrationAction.OVERRIDE_METRICS_TOP1
            if override
            else MetricsArbitrationAction.KEEP_INITIAL
        ),
        initial_root_service=initial_root_service,
        final_root_service=top1.service if override else initial_root_service,
        initial_metrics_rank_or_none=initial_rank,
        metrics_top1_service=top1.service,
        metrics_top2_service_or_none=None if top2 is None else top2.service,
        metrics_top1_score=top1.score,
        metrics_top2_score_or_none=None if top2 is None else top2.score,
        normalized_margin=margin,
        rank_condition_passed=rank_passed,
        margin_condition_passed=margin_passed,
        reason_codes=tuple(reasons),
        supporting_metrics_evidence_refs=top1.supporting_metrics_evidence_refs,
    )


def arbitrate_diagnosis(
    initial: Diagnosis,
    ranking: Sequence[MetricsServiceRank],
    policy: MetricsArbitrationPolicy,
) -> MetricsArbitratedDiagnosis:
    decision = decide_metrics_arbitration(
        initial_root_service=initial.root_cause_service,
        ranking=ranking,
        policy=policy,
    )
    if decision.action is MetricsArbitrationAction.KEEP_INITIAL:
        final = initial
        root_provenance = DiagnosisProvenance.MODEL_INITIAL
        explanation = (
            "The deterministic M3 conditions were not both satisfied; the exact "
            "model Initial Diagnosis was preserved."
        )
    else:
        explanation = (
            "Root service changed by deterministic M3 arbitration because the model "
            "proposal was absent from the Metrics top two and the normalized "
            "top-one/top-two margin was at least 0.25. The Initial indicator was "
            "preserved."
        )
        final = Diagnosis(
            root_cause_service=decision.final_root_service,
            root_cause_indicator=initial.root_cause_indicator,
            confidence=None,
            evidence_refs=decision.supporting_metrics_evidence_refs,
            explanation=explanation,
        )
        root_provenance = DiagnosisProvenance.DETERMINISTIC_METRICS_M3
    return MetricsArbitratedDiagnosis(
        initial_diagnosis=initial,
        arbitration_decision=decision,
        final_diagnosis=final,
        final_root_service=decision.final_root_service,
        final_indicator=initial.root_cause_indicator,
        root_provenance=root_provenance,
        indicator_provenance=DiagnosisProvenance.MODEL_INITIAL,
        initial_explanation=initial.explanation,
        arbitration_explanation=explanation,
    )


__all__ = [
    "DiagnosisProvenance",
    "MetricsArbitratedDiagnosis",
    "MetricsArbitrationAction",
    "MetricsArbitrationDecision",
    "MetricsArbitrationPolicy",
    "MetricsServiceRank",
    "arbitrate_diagnosis",
    "decide_metrics_arbitration",
    "normalized_metrics_margin",
]
