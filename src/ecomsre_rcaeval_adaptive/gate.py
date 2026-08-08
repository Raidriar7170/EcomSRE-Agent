"""Deterministic uncertainty and conflict routing gate."""

from __future__ import annotations

from pydantic import Field, StrictFloat

from ecomsre_rcaeval_adaptive.contracts import (
    EscalationDecision,
    EscalationRoute,
    GateFeatureSnapshot,
    GateReasonCode,
    InitialDiagnosis,
    ServiceName,
    UncertaintyFlag,
    V2Model,
)


class GatePolicy(V2Model):
    direct_confidence_threshold: StrictFloat = Field(default=0.75, ge=0.0, le=1.0)
    low_confidence_threshold: StrictFloat = Field(default=0.55, ge=0.0, le=1.0)
    metrics_margin_threshold: StrictFloat = Field(default=0.25, ge=0.0)


class GateInputs(V2Model):
    initial_diagnosis: InitialDiagnosis
    metrics_service_ranking: tuple[tuple[ServiceName, StrictFloat], ...] = Field(
        max_length=6
    )
    initial_evidence_supports_predicted_service: bool
    cross_source_service_disagreement: bool
    indicator_candidate_available: bool
    trace_available: bool


def _normalized_margin(ranking: tuple[tuple[str, float], ...]) -> float:
    if not ranking:
        return 0.0
    if len(ranking) == 1:
        return 1.0
    top1, top2 = ranking[0][1], ranking[1][1]
    return max(0.0, (top1 - top2) / max(abs(top1), 1e-12))


def decide_escalation(inputs: GateInputs, policy: GatePolicy) -> EscalationDecision:
    """Choose exactly one route from immutable, locally computed features."""

    initial = inputs.initial_diagnosis
    services = tuple(item[0] for item in inputs.metrics_service_ranking)
    rank = (
        services.index(initial.root_cause_service) + 1
        if initial.root_cause_service in services
        else None
    )
    margin = _normalized_margin(inputs.metrics_service_ranking)
    top1 = services[0] if services else None
    top2 = services[1] if len(services) > 1 else None
    ambiguity = (
        UncertaintyFlag.NETWORK_OR_TRACE_AMBIGUITY in initial.uncertainty_flags
        or initial.model_proposed_indicator in {"latency", "socket"}
    )
    rank_weak = rank is None or rank > 3
    strong_conflict_count = sum(
        (
            rank_weak,
            inputs.cross_source_service_disagreement,
            not inputs.initial_evidence_supports_predicted_service,
        )
    )
    snapshot = GateFeatureSnapshot(
        initial_confidence=initial.confidence,
        metrics_service_rank=rank,
        metrics_top1_service=top1,
        metrics_top2_service=top2,
        metrics_top1_top2_margin=margin,
        initial_equals_metrics_top1=initial.root_cause_service == top1,
        initial_evidence_supports_predicted_service=(
            inputs.initial_evidence_supports_predicted_service
        ),
        cross_source_service_disagreement=inputs.cross_source_service_disagreement,
        strong_conflict_count=strong_conflict_count,
        indicator_candidate_available=inputs.indicator_candidate_available,
        trace_available=inputs.trace_available,
        network_or_trace_ambiguity=ambiguity,
        uncertainty_flags=initial.uncertainty_flags,
    )
    direct = all(
        (
            initial.confidence >= policy.direct_confidence_threshold,
            rank is not None and rank <= 2,
            margin >= policy.metrics_margin_threshold,
            not inputs.cross_source_service_disagreement,
            inputs.initial_evidence_supports_predicted_service,
            inputs.indicator_candidate_available,
            not ambiguity,
        )
    )
    if direct:
        return EscalationDecision(
            route=EscalationRoute.DIRECT_RETURN,
            reason_codes=(GateReasonCode.DIRECT_CONFIDENT_METRICS_ALIGNED,),
            gate_feature_snapshot=snapshot,
        )

    reasons: list[GateReasonCode] = []
    if initial.confidence < policy.direct_confidence_threshold:
        reasons.append(GateReasonCode.CONFIDENCE_BELOW_DIRECT_THRESHOLD)
    if initial.confidence < policy.low_confidence_threshold:
        reasons.append(GateReasonCode.CONFIDENCE_BELOW_LOW_THRESHOLD)
    if rank_weak:
        reasons.append(GateReasonCode.METRICS_RANK_WEAK)
    if margin < policy.metrics_margin_threshold:
        reasons.append(GateReasonCode.METRICS_MARGIN_LOW)
    if inputs.cross_source_service_disagreement:
        reasons.append(GateReasonCode.CROSS_SOURCE_CONFLICT)
    if not inputs.initial_evidence_supports_predicted_service:
        reasons.append(GateReasonCode.PREDICTED_SERVICE_EVIDENCE_WEAK)
    if not inputs.indicator_candidate_available:
        reasons.append(GateReasonCode.INDICATOR_CANDIDATE_MISSING)
    if ambiguity:
        reasons.append(GateReasonCode.NETWORK_OR_TRACE_AMBIGUITY)

    if not inputs.trace_available:
        reasons.append(GateReasonCode.TRACE_UNAVAILABLE)
        route = EscalationRoute.ESCALATE_LOGS
    elif (
        initial.confidence < policy.low_confidence_threshold
        or rank_weak
        or strong_conflict_count >= 2
    ):
        route = EscalationRoute.ESCALATE_BOTH
    elif ambiguity and not inputs.cross_source_service_disagreement:
        route = EscalationRoute.ESCALATE_TRACES
    else:
        route = EscalationRoute.ESCALATE_LOGS
    return EscalationDecision(
        route=route,
        reason_codes=tuple(dict.fromkeys(reasons)),
        gate_feature_snapshot=snapshot,
    )


__all__ = ["GateInputs", "GatePolicy", "decide_escalation"]
