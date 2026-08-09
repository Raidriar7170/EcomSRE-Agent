from __future__ import annotations

from ecomsre_rcaeval_v2.contracts import OperationStatus
from ecomsre_rcaeval_v2.dev2_evidence import (
    _dynamic_route_costs,
    _family_damage_rescue,
)
from ecomsre_rcaeval_v2.evaluation import PrivateRunOutcome
from ecomsre_rcaeval_v2.schedule import SplitName, Variant


def _outcome(
    variant: Variant,
    *,
    pair_correct: bool,
    sources: tuple[str, ...] = (),
) -> PrivateRunOutcome:
    return PrivateRunOutcome.model_validate(
        {
            "schema_version": "rcaeval-re2-v2-dev1.private-run-outcome.v1",
            "system": "RE2-OB",
            "root_cause_service": "checkoutservice",
            "fault": "mem",
            "instance": "1",
            "split": SplitName.DESIGN,
            "variant": variant,
            "terminal_status": OperationStatus.COMPLETED,
            "predicted_service": "checkoutservice" if pair_correct else "currencyservice",
            "predicted_indicator": "mem",
            "tool_calls": 3,
            "model_calls": 4,
            "total_tokens": 100,
            "token_usage_known": True,
            "latency_ms": 10.0,
            "failure_operation_type": None,
            "failure_stage": None,
            "specialists": (),
            "commander_selected_sources": sources,
            "indicator_candidate_pairs": (),
            "indicator_disposition": "RESOLVED",
        }
    )


def test_dev2_damage_rescue_reports_both_architecture_families() -> None:
    outcomes = tuple(
        _outcome(variant, pair_correct=correct)
        for variant, correct in (
            (Variant.SINGLE_V1_REFERENCE, True),
            (Variant.FIXED_V1_REFERENCE, False),
            (Variant.DYNAMIC_V1_REFERENCE, True),
            (Variant.SINGLE_V2, False),
            (Variant.FIXED_V2, True),
            (Variant.DYNAMIC_V2, False),
        )
    )
    result = _family_damage_rescue(outcomes)
    assert result["v1_reference"]["single_correct_fixed_wrong"] == 1  # type: ignore[index]
    assert result["v1_reference"]["all_correct"] == 0  # type: ignore[index]
    assert result["v2_dev2"]["single_wrong_fixed_correct"] == 1  # type: ignore[index]
    assert result["v2_dev2"]["all_wrong"] == 0  # type: ignore[index]


def test_dev2_dynamic_routes_include_completion_accuracy_and_cost() -> None:
    result = _dynamic_route_costs(
        (_outcome(Variant.DYNAMIC_V2, pair_correct=True, sources=("logs",)),)
    )
    logs = result["logs_only"]
    assert logs["terminal_count"] == 1  # type: ignore[index]
    assert logs["completed"] == {"numerator": 1, "denominator": 1, "value": 1.0}  # type: ignore[index]
    assert logs["root_service_ac_at_1"] == {"numerator": 1, "denominator": 1, "value": 1.0}  # type: ignore[index]
    assert logs["tool_calls_mean"] == 3.0  # type: ignore[index]
    assert logs["model_calls_median"] == 4.0  # type: ignore[index]
