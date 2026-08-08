from __future__ import annotations

from ecomsre_rcaeval_adaptive.contracts import (
    IndicatorResolutionAction,
    InitialDiagnosis,
)
from ecomsre_rcaeval_adaptive.indicator import IndicatorPolicy, resolve_hybrid_indicator
from ecomsre_rcaeval_v2.indicator import FormulaId, MetricIndicatorCandidate


def _initial(indicator: str | None) -> InitialDiagnosis:
    return InitialDiagnosis.model_validate(
        {
            "root_cause_service": "checkoutservice",
            "model_proposed_indicator": indicator,
            "confidence": 0.8,
            "evidence_refs": ("metric:0001",),
            "explanation": "Bounded evidence.",
            "uncertainty_flags": (),
        }
    )


def _candidate(
    indicator: str, score: float, rank: int, evidence_ref: str
) -> MetricIndicatorCandidate:
    return MetricIndicatorCandidate.model_validate(
        {
            "service": "checkoutservice",
            "canonical_indicator": indicator,
            "metric_name": f"checkoutservice_{indicator}",
            "formula": FormulaId.F0,
            "score": score,
            "score_method": "F0",
            "rank_within_service": rank,
            "rank_global": rank,
            "pre_count": 10,
            "post_count": 10,
            "pre_location": 1.0,
            "post_location": 2.0,
            "pre_scale": 1.0,
            "absolute_shift": 1.0,
            "relative_shift": 1.0,
            "robust_shift": 1.0,
            "persistence": 1.0,
            "evidence_ref": evidence_ref,
            "config_sha256": "a" * 64,
        }
    )


def test_keep_model_indicator_when_it_is_in_service_top_two() -> None:
    result = resolve_hybrid_indicator(
        "checkoutservice",
        _initial("mem"),
        (
            _candidate("cpu", 1.0, 1, "indicator:0001"),
            _candidate("mem", 0.5, 2, "indicator:0002"),
        ),
        IndicatorPolicy(deterministic_margin_threshold=0.6),
    )

    assert result.action is IndicatorResolutionAction.KEEP_MODEL_INDICATOR
    assert result.final_indicator == "mem"
    assert result.model_candidate_rank == 2


def test_use_deterministic_top_one_only_on_strong_margin() -> None:
    result = resolve_hybrid_indicator(
        "checkoutservice",
        _initial("socket"),
        (
            _candidate("cpu", 1.0, 1, "indicator:0001"),
            _candidate("mem", 0.2, 2, "indicator:0002"),
        ),
        IndicatorPolicy(deterministic_margin_threshold=0.6),
    )

    assert result.action is IndicatorResolutionAction.USE_DETERMINISTIC_TOP1
    assert result.final_indicator == "cpu"
    assert result.deterministic_margin == 0.8


def test_weak_margin_keeps_model_with_uncertainty() -> None:
    result = resolve_hybrid_indicator(
        "checkoutservice",
        _initial("socket"),
        (
            _candidate("cpu", 1.0, 1, "indicator:0001"),
            _candidate("mem", 0.8, 2, "indicator:0002"),
        ),
        IndicatorPolicy(deterministic_margin_threshold=0.6),
    )

    assert (
        result.action
        is IndicatorResolutionAction.KEEP_MODEL_INDICATOR_WITH_UNCERTAINTY
    )
    assert result.final_indicator == "socket"
