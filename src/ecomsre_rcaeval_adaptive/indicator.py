"""Hybrid model/deterministic Indicator Resolver."""

from __future__ import annotations

from pydantic import Field, StrictFloat

from ecomsre_rcaeval_adaptive.contracts import (
    HybridIndicatorResolution,
    IndicatorResolutionAction,
    InitialDiagnosis,
    V2Model,
)
from ecomsre_rcaeval_v2.indicator import MetricIndicatorCandidate


class IndicatorPolicy(V2Model):
    deterministic_margin_threshold: StrictFloat = Field(default=0.6, ge=0.0)


def _margin(candidates: tuple[MetricIndicatorCandidate, ...]) -> float | None:
    if not candidates:
        return None
    if len(candidates) == 1:
        return 1.0
    return max(
        0.0,
        (candidates[0].score - candidates[1].score)
        / max(abs(candidates[0].score), 1e-12),
    )


def resolve_hybrid_indicator(
    final_root_service: str,
    initial: InitialDiagnosis,
    candidates: tuple[MetricIndicatorCandidate, ...],
    policy: IndicatorPolicy,
) -> HybridIndicatorResolution:
    """Apply the three-action policy without a model call."""

    service_candidates = tuple(
        sorted(
            (item for item in candidates if item.service == final_root_service),
            key=lambda item: item.rank_within_service,
        )
    )
    top_two = service_candidates[:2]
    model_rank = next(
        (
            index
            for index, item in enumerate(top_two, start=1)
            if item.canonical_indicator == initial.model_proposed_indicator
        ),
        None,
    )
    top1 = service_candidates[0] if service_candidates else None
    margin = _margin(service_candidates)
    if model_rank is not None:
        selected = top_two[model_rank - 1]
        return HybridIndicatorResolution(
            selected_service=final_root_service,
            model_indicator=initial.model_proposed_indicator,
            deterministic_top1=(None if top1 is None else top1.canonical_indicator),
            final_indicator=initial.model_proposed_indicator,
            action=IndicatorResolutionAction.KEEP_MODEL_INDICATOR,
            model_candidate_rank=model_rank,
            deterministic_margin=margin,
            evidence_ref=selected.evidence_ref,
        )
    if top1 is not None and margin is not None and margin >= policy.deterministic_margin_threshold:
        return HybridIndicatorResolution(
            selected_service=final_root_service,
            model_indicator=initial.model_proposed_indicator,
            deterministic_top1=top1.canonical_indicator,
            final_indicator=top1.canonical_indicator,
            action=IndicatorResolutionAction.USE_DETERMINISTIC_TOP1,
            model_candidate_rank=None,
            deterministic_margin=margin,
            evidence_ref=top1.evidence_ref,
        )
    return HybridIndicatorResolution(
        selected_service=final_root_service,
        model_indicator=initial.model_proposed_indicator,
        deterministic_top1=(None if top1 is None else top1.canonical_indicator),
        final_indicator=initial.model_proposed_indicator,
        action=IndicatorResolutionAction.KEEP_MODEL_INDICATOR_WITH_UNCERTAINTY,
        model_candidate_rank=None,
        deterministic_margin=margin,
        evidence_ref=None,
    )


__all__ = ["IndicatorPolicy", "resolve_hybrid_indicator"]
