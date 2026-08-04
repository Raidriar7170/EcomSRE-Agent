from __future__ import annotations

from pathlib import Path

import pytest

from ecomsre.phase5b.analysis import (
    AnalysisRun,
    Population,
    analyze_populations,
    cost_quality_claim,
    hierarchical_paired_bootstrap,
    hidden_primary_bootstrap,
    superiority_claim,
    validate_complete_results,
)
from ecomsre.phase5b.contracts import VariantName
from ecomsre.phase5b.protocol import load_seed_policy, load_suite_registry
from ecomsre.phase5b.schedule import build_execution_schedule
from ecomsre.phase5b.protocol import load_analysis_plan


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _run(
    template: str,
    seed: str,
    variant: VariantName,
    *,
    population: Population = "HIDDEN",
    correct: bool,
    tools: int,
    failure: str | None = None,
    run_id: str | None = None,
) -> AnalysisRun:
    return AnalysisRun(
        run_id=run_id or ("a" * 32),
        template_id=template,
        seed_id=seed,
        population=population,
        variant=variant,
        decision_correct=correct,
        tool_calls=tools,
        failure_code=failure,
    )


def _paired_hidden_runs() -> tuple[AnalysisRun, ...]:
    runs: list[AnalysisRun] = []
    for template in ("hidden-01", "hidden-02"):
        for seed in ("seed-00", "seed-01"):
            runs.extend(
                (
                    _run(template, seed, "SINGLE_AGENT_V2", correct=False, tools=10),
                    _run(template, seed, "FIXED_SPECIALIST_V2", correct=True, tools=9),
                    _run(template, seed, "DYNAMIC_MULTI_AGENT_V2", correct=True, tools=6),
                )
            )
    return tuple(runs)


def test_analysis_plan_freezes_primary_bootstrap_and_claim_rules() -> None:
    plan = load_analysis_plan(
        PROJECT_ROOT / "config/phase5b/analysis-plan.v1.json"
    )
    assert plan["primary_population"] == "HIDDEN_ONLY"
    assert plan["primary_comparison"] == [
        "DYNAMIC_MULTI_AGENT_V2",
        "SINGLE_AGENT_V2",
    ]
    assert plan["primary_metric"] == "Decision Accuracy"
    assert plan["bootstrap_replicates"] == 10_000
    assert plan["bootstrap_rng_seed"] == 20_260_804
    assert plan["bootstrap_rng_engine"] == "sha256_counter_v1"
    assert plan["percentile_method"] == "linear_interpolation_at_(n-1)q"
    assert plan["confidence_interval"] == 0.95
    assert plan["superiority_rule"] == "paired_ci_lower_bound_gt_0"
    assert plan["accuracy_noninferiority_margin"] == -0.05
    assert plan["minimum_mean_tool_call_reduction"] == 0.2


def test_hierarchical_bootstrap_is_paired_deterministic_and_strict() -> None:
    runs = _paired_hidden_runs()
    first = hierarchical_paired_bootstrap(
        runs,
        left_variant="DYNAMIC_MULTI_AGENT_V2",
        right_variant="SINGLE_AGENT_V2",
        metric="decision_correct",
        replicates=10_000,
        rng_seed=20_260_804,
    )
    second = hierarchical_paired_bootstrap(
        runs,
        left_variant="DYNAMIC_MULTI_AGENT_V2",
        right_variant="SINGLE_AGENT_V2",
        metric="decision_correct",
        replicates=10_000,
        rng_seed=20_260_804,
    )

    assert first == second
    assert first.template_count == 2
    assert first.pairing_unit_count == 4
    assert first.point_estimate == 1.0
    assert first.ci_lower == 1.0
    assert first.ci_upper == 1.0
    assert superiority_claim(first) is False


def test_failures_remain_in_denominator_and_population_views_are_distinct() -> None:
    config_root = PROJECT_ROOT / "config/phase5b"
    suite = load_suite_registry(config_root / "suite-registry.v1.json")
    schedule = build_execution_schedule(
        suite,
        load_seed_policy(config_root / "seed-policy.v1.json"),
    )
    hidden_ids = {item.template_id for item in suite.hidden_slots}
    failure_id = next(
        item.run_id
        for item in schedule.runs
        if item.template_id in hidden_ids
        and item.variant == "DYNAMIC_MULTI_AGENT_V2"
    )
    runs = tuple(
        _run(
            item.template_id,
            item.seed_id,
            item.variant,
            population="HIDDEN" if item.template_id in hidden_ids else "PUBLIC",
            correct=True,
            tools=6,
            failure="PROVIDER_TIMEOUT" if item.run_id == failure_id else None,
            run_id=item.run_id,
        )
        for item in schedule.runs
    )
    report = analyze_populations(runs, suite=suite, schedule=schedule)

    assert report["hidden_only_primary"]["pairing_units"] == 30
    assert report["full_suite_secondary"]["pairing_units"] == 60
    assert report["public_anchor_descriptive"]["pairing_units"] == 30
    assert report["hidden_only_primary"]["DYNAMIC_MULTI_AGENT_V2"]["correct"] == 29
    assert report["hidden_only_primary"]["DYNAMIC_MULTI_AGENT_V2"]["denominator"] == 30


def test_cost_quality_claim_requires_both_accuracy_and_tool_call_gates() -> None:
    accuracy = hierarchical_paired_bootstrap(
        _paired_hidden_runs(),
        left_variant="DYNAMIC_MULTI_AGENT_V2",
        right_variant="SINGLE_AGENT_V2",
        metric="decision_correct",
        replicates=500,
        rng_seed=20_260_804,
    )
    tools = hierarchical_paired_bootstrap(
        _paired_hidden_runs(),
        left_variant="DYNAMIC_MULTI_AGENT_V2",
        right_variant="SINGLE_AGENT_V2",
        metric="relative_tool_reduction",
        replicates=500,
        rng_seed=20_260_804,
    )
    assert tools.point_estimate == 0.4
    assert cost_quality_claim(accuracy, tools) is False

    tied = accuracy.model_copy(update={"ci_lower": -0.051})
    assert cost_quality_claim(tied, tools) is False


def test_analysis_rejects_zero_denominator() -> None:
    with pytest.raises(ValueError, match="denominator|pair"):
        hierarchical_paired_bootstrap(
            (),
            left_variant="DYNAMIC_MULTI_AGENT_V2",
            right_variant="SINGLE_AGENT_V2",
            metric="decision_correct",
            replicates=100,
            rng_seed=20_260_804,
        )


def test_tool_reduction_uses_ratio_of_means_not_mean_of_pair_ratios() -> None:
    runs = (
        _run("hidden-01", "seed-00", "SINGLE_AGENT_V2", correct=True, tools=10),
        _run("hidden-01", "seed-00", "DYNAMIC_MULTI_AGENT_V2", correct=True, tools=5),
        _run("hidden-01", "seed-01", "SINGLE_AGENT_V2", correct=True, tools=2),
        _run("hidden-01", "seed-01", "DYNAMIC_MULTI_AGENT_V2", correct=True, tools=2),
    )
    result = hierarchical_paired_bootstrap(
        runs,
        left_variant="DYNAMIC_MULTI_AGENT_V2",
        right_variant="SINGLE_AGENT_V2",
        metric="relative_tool_reduction",
        replicates=50,
        rng_seed=20_260_804,
    )
    assert result.point_estimate == pytest.approx(5 / 12)


def test_analysis_requires_exact_scheduled_run_set() -> None:
    config_root = PROJECT_ROOT / "config/phase5b"
    schedule = build_execution_schedule(
        load_suite_registry(config_root / "suite-registry.v1.json"),
        load_seed_policy(config_root / "seed-policy.v1.json"),
    )
    run_ids = tuple(item.run_id for item in schedule.runs)
    validate_complete_results(schedule, run_ids)
    with pytest.raises(ValueError, match="missing"):
        validate_complete_results(schedule, run_ids[:-1])
    with pytest.raises(ValueError, match="duplicate"):
        validate_complete_results(schedule, run_ids[:-1] + (run_ids[0],))
    with pytest.raises(ValueError, match="extra"):
        validate_complete_results(schedule, run_ids + ("f" * 32,))


def test_hidden_primary_is_derived_from_frozen_schedule_and_rejects_mislabeling() -> None:
    config_root = PROJECT_ROOT / "config/phase5b"
    suite = load_suite_registry(config_root / "suite-registry.v1.json")
    schedule = build_execution_schedule(
        suite,
        load_seed_policy(config_root / "seed-policy.v1.json"),
    )
    hidden_ids = {item.template_id for item in suite.hidden_slots}
    runs = tuple(
        _run(
            item.template_id,
            item.seed_id,
            item.variant,
            population="HIDDEN" if item.template_id in hidden_ids else "PUBLIC",
            correct=item.variant != "SINGLE_AGENT_V2",
            tools=6 if item.variant == "DYNAMIC_MULTI_AGENT_V2" else 10,
            run_id=item.run_id,
        )
        for item in schedule.runs
    )
    result = hidden_primary_bootstrap(
        runs,
        suite=suite,
        schedule=schedule,
        metric="decision_correct",
    )
    assert result.analysis_population == "HIDDEN_ONLY"
    assert result.primary_eligible is True
    assert result.pairing_unit_count == 30
    assert superiority_claim(result) is True
    tool_result = hidden_primary_bootstrap(
        runs,
        suite=suite,
        schedule=schedule,
        metric="relative_tool_reduction",
    )
    assert tool_result.point_estimate == 0.4
    assert cost_quality_claim(result, tool_result) is True
    assert superiority_claim(result.model_copy(update={"replicates": 100})) is False
    assert cost_quality_claim(
        result,
        tool_result.model_copy(update={"rng_seed": 1}),
    ) is False

    public_index = next(
        index for index, item in enumerate(runs) if item.population == "PUBLIC"
    )
    polluted = list(runs)
    polluted[public_index] = polluted[public_index].model_copy(
        update={"population": "HIDDEN"}
    )
    with pytest.raises(ValueError, match="population"):
        hidden_primary_bootstrap(
            tuple(polluted),
            suite=suite,
            schedule=schedule,
            metric="decision_correct",
        )
