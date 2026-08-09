"""Single-first Adaptive v2: conservative Gate and deterministic Fusion."""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import Field, StrictBool, StrictFloat, StrictInt

from ecomsre_rcaeval.contracts import Diagnosis
from ecomsre_rcaeval_adaptive.contracts import (
    CausalRole,
    RankedHypothesis,
    V2Model,
)
from ecomsre_rcaeval_v2.indicator import MetricIndicatorCandidate


class AdaptiveV2Route(str, Enum):
    DIRECT_RETURN = "DIRECT_RETURN"
    VERIFY_LOGS = "VERIFY_LOGS"
    VERIFY_TRACES = "VERIFY_TRACES"
    VERIFY_BOTH = "VERIFY_BOTH"


class V2GatePolicy(V2Model):
    direct_confidence_threshold: StrictFloat = Field(default=0.9, ge=0.0, le=1.0)
    low_confidence_threshold: StrictFloat = Field(default=0.75, ge=0.0, le=1.0)
    metrics_conflict_rank: StrictInt = Field(default=3, ge=2, le=6)


class V2GateInputs(V2Model):
    initial_diagnosis: Diagnosis
    metrics_service_ranking: tuple[tuple[str, StrictFloat], ...] = Field(
        min_length=1, max_length=6
    )
    diagnosis_evidence_supports_service: StrictBool
    logs_explicitly_oppose_initial: StrictBool
    propagation_conflict: StrictBool
    trace_available: StrictBool
    indicator_candidate_available: StrictBool


class V2GateDecision(V2Model):
    route: AdaptiveV2Route
    reason_codes: tuple[str, ...] = Field(min_length=1, max_length=12)
    metrics_service_rank: StrictInt | None = Field(default=None, ge=1, le=6)
    metrics_top1_top2_margin: StrictFloat = Field(ge=0.0)
    initial_unstable: StrictBool
    trace_semantics_triggered: StrictBool


def _normalized_margin(ranking: tuple[tuple[str, float], ...]) -> float:
    if len(ranking) == 1:
        return 1.0
    top1, top2 = ranking[0][1], ranking[1][1]
    return max(0.0, (top1 - top2) / max(abs(top1), 1e-12))


def decide_v2_gate(inputs: V2GateInputs, policy: V2GatePolicy) -> V2GateDecision:
    """Route conservatively: Direct is default and Trace needs typed semantics."""

    initial = inputs.initial_diagnosis
    services = tuple(item[0] for item in inputs.metrics_service_ranking)
    rank = services.index(initial.root_cause_service) + 1 if initial.root_cause_service in services else None
    confidence = initial.confidence
    below_direct = confidence is None or confidence < policy.direct_confidence_threshold
    below_low = confidence is None or confidence < policy.low_confidence_threshold
    metrics_conflict = rank is None or rank >= policy.metrics_conflict_rank
    evidence_weak = not inputs.diagnosis_evidence_supports_service
    indicator_missing = not inputs.indicator_candidate_available
    trace_semantics = (
        inputs.trace_available
        and initial.root_cause_indicator in {"latency", "socket"}
        and inputs.propagation_conflict
    )
    initial_unstable = (
        below_low
        or metrics_conflict
        or evidence_weak
        or inputs.logs_explicitly_oppose_initial
    )
    severe_multi_source = (
        below_low
        and metrics_conflict
        and inputs.logs_explicitly_oppose_initial
        and trace_semantics
    )
    reasons: list[str] = []
    if below_direct:
        reasons.append("CONFIDENCE_BELOW_DIRECT_THRESHOLD")
    if below_low:
        reasons.append("CONFIDENCE_BELOW_LOW_THRESHOLD")
    if metrics_conflict:
        reasons.append("METRICS_CONFLICT")
    if inputs.logs_explicitly_oppose_initial:
        reasons.append("LOGS_OPPOSE_INITIAL")
    if evidence_weak:
        reasons.append("DIAGNOSIS_EVIDENCE_WEAK")
    if indicator_missing:
        reasons.append("INDICATOR_CANDIDATE_MISSING")
    if trace_semantics:
        reasons.append("LATENCY_SOCKET_PROPAGATION_CONFLICT")

    if severe_multi_source:
        route = AdaptiveV2Route.VERIFY_BOTH
    elif trace_semantics:
        route = AdaptiveV2Route.VERIFY_TRACES
    elif inputs.logs_explicitly_oppose_initial:
        route = AdaptiveV2Route.VERIFY_LOGS
    elif metrics_conflict and evidence_weak:
        route = AdaptiveV2Route.VERIFY_LOGS
    else:
        route = AdaptiveV2Route.DIRECT_RETURN
        reasons = ["CONSERVATIVE_DIRECT_DEFAULT"]
    return V2GateDecision(
        route=route,
        reason_codes=tuple(dict.fromkeys(reasons)),
        metrics_service_rank=rank,
        metrics_top1_top2_margin=_normalized_margin(
            tuple((service, float(score)) for service, score in inputs.metrics_service_ranking)
        ),
        initial_unstable=initial_unstable,
        trace_semantics_triggered=trace_semantics,
    )


class DeterministicFusionPolicy(V2Model):
    alternative_support_score_threshold: StrictFloat = Field(
        default=0.9, ge=0.0, le=1.0
    )


class DeterministicFusionDecision(V2Model):
    action: Literal["KEEP_INITIAL", "OVERRIDE_INITIAL"]
    final_root_service: str
    reason_codes: tuple[str, ...] = Field(min_length=1, max_length=12)
    supporting_sources: tuple[Literal["metrics", "logs", "traces"], ...] = ()
    contradicting_sources: tuple[Literal["logs", "traces"], ...] = ()


def deterministic_fusion(
    *,
    initial: Diagnosis,
    gate: V2GateDecision,
    metrics_service_ranking: tuple[tuple[str, float], ...],
    specialist_hypotheses: tuple[RankedHypothesis, ...],
    policy: DeterministicFusionPolicy,
) -> DeterministicFusionDecision:
    """Keep Initial unless one authorized alternative clears every rule."""

    def keep(reason: str) -> DeterministicFusionDecision:
        return DeterministicFusionDecision(
            action="KEEP_INITIAL",
            final_root_service=initial.root_cause_service,
            reason_codes=(reason,),
        )
    if gate.route is AdaptiveV2Route.DIRECT_RETURN or not gate.initial_unstable:
        return keep("INITIAL_NOT_UNSTABLE")
    metrics_top_two = {item[0] for item in metrics_service_ranking[:2]}
    root_candidates = tuple(
        item
        for item in specialist_hypotheses
        if item.causal_role is CausalRole.ROOT_CANDIDATE
        and item.service != initial.root_cause_service
        and item.service in metrics_top_two
        and item.score >= policy.alternative_support_score_threshold
        and item.supporting_evidence_refs
    )
    candidate_services = {item.service for item in root_candidates}
    if len(candidate_services) != 1:
        return keep(
            "NO_SINGLE_STRONG_ALTERNATIVE"
            if not candidate_services
            else "SPECIALIST_ALTERNATIVES_CONFLICT"
        )
    initial_contradictions = tuple(
        item
        for item in specialist_hypotheses
        if item.service == initial.root_cause_service
        and (
            item.contradicting_evidence_refs
            or (
                item.causal_role is CausalRole.PROPAGATED_SYMPTOM
                and item.supporting_evidence_refs
            )
        )
    )
    if not initial_contradictions:
        return keep("INITIAL_NOT_EXPLICITLY_CONTRADICTED")
    alternative = next(iter(candidate_services))
    supporting_sources = tuple(
        dict.fromkeys(
            ("metrics", *(item.source for item in root_candidates if item.service == alternative))
        )
    )
    contradicting_sources = tuple(dict.fromkeys(item.source for item in initial_contradictions))
    return DeterministicFusionDecision(
        action="OVERRIDE_INITIAL",
        final_root_service=alternative,
        reason_codes=("STRONG_AUTHORIZED_ALTERNATIVE",),
        supporting_sources=supporting_sources,  # type: ignore[arg-type]
        contradicting_sources=contradicting_sources,  # type: ignore[arg-type]
    )


class StrongSingleIndicatorAction(str, Enum):
    KEEP_STRONG_SINGLE_INDICATOR = "KEEP_STRONG_SINGLE_INDICATOR"
    DETERMINISTIC_OVERRIDE_STRONG_MARGIN = "DETERMINISTIC_OVERRIDE_STRONG_MARGIN"
    KEEP_WITH_UNCERTAINTY = "KEEP_WITH_UNCERTAINTY"


class StrongSingleIndicatorPolicy(V2Model):
    deterministic_override_margin: StrictFloat = Field(default=0.8, ge=0.0)


class StrongSingleIndicatorResolution(V2Model):
    action: StrongSingleIndicatorAction
    final_indicator: Literal["cpu", "mem", "diskio", "latency", "socket"]
    deterministic_top1: Literal["cpu", "mem", "diskio", "latency", "socket"] | None
    deterministic_margin: StrictFloat | None = Field(default=None, ge=0.0)


def resolve_strong_single_indicator(
    *,
    final_root_service: str,
    initial: Diagnosis,
    candidates: tuple[MetricIndicatorCandidate, ...],
    policy: StrongSingleIndicatorPolicy,
) -> StrongSingleIndicatorResolution:
    """Preserve Strong Single Indicator unless a conflicting Top-1 is decisive."""

    selected = tuple(
        sorted(
            (item for item in candidates if item.service == final_root_service),
            key=lambda item: item.rank_within_service,
        )
    )
    top1 = selected[0] if selected else None
    if not selected:
        margin = None
    elif len(selected) == 1:
        margin = 1.0
    else:
        margin = max(
            0.0,
            (selected[0].score - selected[1].score)
            / max(abs(selected[0].score), 1e-12),
        )
    if top1 is not None and top1.canonical_indicator == initial.root_cause_indicator:
        action = StrongSingleIndicatorAction.KEEP_STRONG_SINGLE_INDICATOR
        final = initial.root_cause_indicator
    elif top1 is not None and margin is not None and margin >= policy.deterministic_override_margin:
        action = StrongSingleIndicatorAction.DETERMINISTIC_OVERRIDE_STRONG_MARGIN
        final = top1.canonical_indicator
    else:
        action = StrongSingleIndicatorAction.KEEP_WITH_UNCERTAINTY
        final = initial.root_cause_indicator
    return StrongSingleIndicatorResolution(
        action=action,
        final_indicator=final,
        deterministic_top1=None if top1 is None else top1.canonical_indicator,
        deterministic_margin=margin,
    )


def expected_semantic_operations(route: AdaptiveV2Route) -> int:
    return {
        AdaptiveV2Route.DIRECT_RETURN: 1,
        AdaptiveV2Route.VERIFY_LOGS: 2,
        AdaptiveV2Route.VERIFY_TRACES: 2,
        AdaptiveV2Route.VERIFY_BOTH: 3,
    }[route]


def paired_arm_order(case_count: int) -> tuple[tuple[str, str], ...]:
    if type(case_count) is not int or case_count <= 0:
        raise ValueError("paired arm order requires a positive case count")
    return tuple(
        ("STRONG_SINGLE", "ADAPTIVE_V2")
        if index % 2 == 0
        else ("ADAPTIVE_V2", "STRONG_SINGLE")
        for index in range(case_count)
    )


__all__ = [
    "AdaptiveV2Route",
    "DeterministicFusionDecision",
    "DeterministicFusionPolicy",
    "StrongSingleIndicatorAction",
    "StrongSingleIndicatorPolicy",
    "StrongSingleIndicatorResolution",
    "V2GateDecision",
    "V2GateInputs",
    "V2GatePolicy",
    "decide_v2_gate",
    "deterministic_fusion",
    "expected_semantic_operations",
    "paired_arm_order",
    "resolve_strong_single_indicator",
]
