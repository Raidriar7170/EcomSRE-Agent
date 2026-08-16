from __future__ import annotations

from collections import Counter
from pathlib import Path

from ecomsre.dta_v2.evaluation_campaign import (
    build_mean_cost_metrics,
    build_public_development_schedule,
)
from ecomsre.dta_v2.evaluation_contracts import EvaluationArm
from ecomsre.dta_v2.evaluation_dataset import load_public_evaluation_dataset


ROOT = Path(__file__).resolve().parents[2]


def test_public_development_schedule_is_exact_two_arm_eighteen_entry_freeze() -> None:
    manifest, _ = load_public_evaluation_dataset(
        ROOT / "config/dta-v2/evaluation"
    )
    schedule = build_public_development_schedule(
        campaign_id="a" * 32,
        base_head="b" * 40,
        model_id="gpt-5.4-mini-2026-03-17",
        identity_sha256="c" * 64,
        dataset=manifest,
    )

    assert len(schedule.entries) == 18
    assert tuple(item.ordinal for item in schedule.entries) == tuple(range(1, 19))
    assert Counter(item.case_id for item in schedule.entries) == {
        **{f"dta-case-{index:03d}": 2 for index in range(1, 7)},
        **{f"dta-case-{index:03d}": 2 for index in range(10, 13)},
    }
    assert all(
        schedule.entries[index].arm is EvaluationArm.ONE_SHOT_FULL_CONTEXT
        and schedule.entries[index + 1].arm is EvaluationArm.ADAPTIVE_TOOL_USING
        for index in range(0, 18, 2)
    )


def test_mean_cost_metrics_are_derived_from_fixed_denominator_totals() -> None:
    means = build_mean_cost_metrics(
        entry_count=3,
        read_tool_dispatches_total=9,
        context_materialization_reads_total=0,
        provider_turns_total=15,
        input_tokens_total=42_986,
        output_tokens_total=1_422,
        latency_ms_total=27_481,
    )

    assert means.read_tool_dispatches == 3.0
    assert means.provider_turns == 5.0
    assert means.input_tokens == 42_986 / 3
    assert means.output_tokens == 474.0
    assert means.total_tokens == 44_408 / 3
    assert means.latency_ms == 27_481 / 3
