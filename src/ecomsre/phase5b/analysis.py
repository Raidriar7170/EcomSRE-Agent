"""Preregistered paired analysis for Phase 5B reports and mock dry runs."""

from __future__ import annotations

from collections import defaultdict
import hashlib
import math
from typing import Any, Literal

from pydantic import Field, StrictBool, StrictInt

from ecomsre.phase5b.contracts import (
    ExecutionSchedule,
    Phase5BModel,
    SuiteRegistry,
    VariantName,
)


Population = Literal["HIDDEN", "PUBLIC", "SYNTHETIC"]
MetricName = Literal["decision_correct", "relative_tool_reduction"]
_PRIMARY_LEFT: VariantName = "DYNAMIC_MULTI_AGENT_V2"
_PRIMARY_RIGHT: VariantName = "SINGLE_AGENT_V2"
_PRIMARY_REPLICATES = 10_000
_PRIMARY_RNG_SEED = 20_260_804


class AnalysisRun(Phase5BModel):
    run_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    template_id: str = Field(min_length=1, max_length=128)
    seed_id: str = Field(min_length=1, max_length=64)
    population: Population
    variant: VariantName
    decision_correct: StrictBool
    tool_calls: StrictInt = Field(ge=0)
    failure_code: str | None = Field(default=None, min_length=1, max_length=128)

    @property
    def retained_decision_correct(self) -> bool:
        return self.failure_code is None and self.decision_correct


class BootstrapResult(Phase5BModel):
    metric: MetricName
    left_variant: VariantName
    right_variant: VariantName
    template_count: StrictInt = Field(gt=0)
    pairing_unit_count: StrictInt = Field(gt=0)
    replicates: StrictInt = Field(gt=0)
    rng_seed: StrictInt
    analysis_population: Literal["GENERIC", "HIDDEN_ONLY"]
    primary_eligible: StrictBool
    point_estimate: float
    ci_lower: float
    ci_upper: float


def _paired_observations(
    runs: tuple[AnalysisRun, ...],
    *,
    left_variant: VariantName,
    right_variant: VariantName,
    metric: MetricName,
) -> dict[str, tuple[tuple[AnalysisRun, AnalysisRun], ...]]:
    grouped: dict[tuple[str, str], dict[str, AnalysisRun]] = defaultdict(dict)
    for item in runs:
        key = (item.template_id, item.seed_id)
        if item.variant in grouped[key]:
            raise ValueError("paired analysis contains a duplicate architecture arm")
        grouped[key][item.variant] = item
    if not grouped:
        raise ValueError("paired analysis denominator is zero")
    by_template: dict[str, list[tuple[str, AnalysisRun, AnalysisRun]]] = defaultdict(list)
    for (template_id, seed_id), arms in grouped.items():
        if left_variant not in arms or right_variant not in arms:
            raise ValueError("paired analysis requires both comparison arms")
        left = arms[left_variant]
        right = arms[right_variant]
        if metric == "relative_tool_reduction" and right.tool_calls <= 0:
            raise ValueError("relative tool reduction denominator is zero")
        by_template[template_id].append((seed_id, left, right))
    return {
        template_id: tuple((left, right) for _, left, right in sorted(values))
        for template_id, values in sorted(by_template.items())
    }


def _percentile(values: list[float], probability: float) -> float:
    ordered = sorted(values)
    position = probability * (len(ordered) - 1)
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def _draw_index(
    *,
    rng_seed: int,
    replicate: int,
    scope: str,
    draw: int,
    upper_bound: int,
) -> int:
    if upper_bound <= 0:
        raise ValueError("bootstrap draw population is empty")
    material = b"\0".join(
        item.encode("utf-8")
        for item in (str(rng_seed), str(replicate), scope, str(draw))
    )
    return int.from_bytes(hashlib.sha256(material).digest()[:8], "big") % upper_bound


def _aggregate_metric(
    pairs: list[tuple[AnalysisRun, AnalysisRun]],
    metric: MetricName,
) -> float:
    if not pairs:
        raise ValueError("paired analysis denominator is zero")
    if metric == "decision_correct":
        values = [
            float(left.retained_decision_correct)
            - float(right.retained_decision_correct)
            for left, right in pairs
        ]
        return sum(values) / len(values)
    left_mean = sum(left.tool_calls for left, _ in pairs) / len(pairs)
    right_mean = sum(right.tool_calls for _, right in pairs) / len(pairs)
    if right_mean <= 0:
        raise ValueError("relative tool reduction denominator is zero")
    return (right_mean - left_mean) / right_mean


def hierarchical_paired_bootstrap(
    runs: tuple[AnalysisRun, ...],
    *,
    left_variant: VariantName,
    right_variant: VariantName,
    metric: MetricName,
    replicates: int = 10_000,
    rng_seed: int = 20_260_804,
) -> BootstrapResult:
    if replicates <= 0:
        raise ValueError("bootstrap replicate count must be positive")
    pairs_by_template = _paired_observations(
        runs,
        left_variant=left_variant,
        right_variant=right_variant,
        metric=metric,
    )
    templates = tuple(pairs_by_template)
    observed = [pair for pairs in pairs_by_template.values() for pair in pairs]
    if not observed:
        raise ValueError("paired analysis denominator is zero")
    replicate_values: list[float] = []
    for replicate in range(replicates):
        sampled_pairs: list[tuple[AnalysisRun, AnalysisRun]] = []
        for cluster_draw in range(len(templates)):
            template_index = _draw_index(
                rng_seed=rng_seed,
                replicate=replicate,
                scope="template",
                draw=cluster_draw,
                upper_bound=len(templates),
            )
            template_id = templates[template_index]
            seeds = pairs_by_template[template_id]
            for seed_draw in range(len(seeds)):
                seed_index = _draw_index(
                    rng_seed=rng_seed,
                    replicate=replicate,
                    scope=f"seed:{cluster_draw}:{template_id}",
                    draw=seed_draw,
                    upper_bound=len(seeds),
                )
                sampled_pairs.append(seeds[seed_index])
        replicate_values.append(_aggregate_metric(sampled_pairs, metric))
    return BootstrapResult(
        metric=metric,
        left_variant=left_variant,
        right_variant=right_variant,
        template_count=len(templates),
        pairing_unit_count=len(observed),
        replicates=replicates,
        rng_seed=rng_seed,
        analysis_population="GENERIC",
        primary_eligible=False,
        point_estimate=_aggregate_metric(observed, metric),
        ci_lower=_percentile(replicate_values, 0.025),
        ci_upper=_percentile(replicate_values, 0.975),
    )


def superiority_claim(result: BootstrapResult) -> bool:
    return (
        result.analysis_population == "HIDDEN_ONLY"
        and result.primary_eligible
        and result.metric == "decision_correct"
        and result.left_variant == _PRIMARY_LEFT
        and result.right_variant == _PRIMARY_RIGHT
        and result.template_count == 6
        and result.pairing_unit_count == 30
        and result.replicates == _PRIMARY_REPLICATES
        and result.rng_seed == _PRIMARY_RNG_SEED
        and result.ci_lower > 0
    )


def cost_quality_claim(
    accuracy: BootstrapResult,
    tool_reduction: BootstrapResult,
) -> bool:
    return (
        accuracy.analysis_population == "HIDDEN_ONLY"
        and tool_reduction.analysis_population == "HIDDEN_ONLY"
        and accuracy.primary_eligible
        and tool_reduction.primary_eligible
        and accuracy.metric == "decision_correct"
        and tool_reduction.metric == "relative_tool_reduction"
        and accuracy.left_variant == tool_reduction.left_variant == _PRIMARY_LEFT
        and accuracy.right_variant == tool_reduction.right_variant == _PRIMARY_RIGHT
        and accuracy.template_count == tool_reduction.template_count == 6
        and accuracy.pairing_unit_count == tool_reduction.pairing_unit_count == 30
        and accuracy.replicates == tool_reduction.replicates == _PRIMARY_REPLICATES
        and accuracy.rng_seed == tool_reduction.rng_seed == _PRIMARY_RNG_SEED
        and accuracy.ci_lower >= -0.05
        and tool_reduction.point_estimate >= 0.20
        and tool_reduction.ci_lower > 0
    )


def _population_summary(runs: tuple[AnalysisRun, ...]) -> dict[str, Any]:
    pairing_units = {(item.template_id, item.seed_id) for item in runs}
    if not pairing_units:
        return {"pairing_units": 0}
    summary: dict[str, Any] = {"pairing_units": len(pairing_units)}
    for variant in (
        "SINGLE_AGENT_V2",
        "FIXED_SPECIALIST_V2",
        "DYNAMIC_MULTI_AGENT_V2",
    ):
        selected = tuple(item for item in runs if item.variant == variant)
        summary[variant] = {
            "correct": sum(item.retained_decision_correct for item in selected),
            "denominator": len(selected),
        }
    return summary


def _validate_scheduled_analysis_runs(
    runs: tuple[AnalysisRun, ...],
    *,
    suite: SuiteRegistry,
    schedule: ExecutionSchedule,
) -> None:
    validate_complete_results(schedule, tuple(item.run_id for item in runs))
    by_id = {item.run_id: item for item in runs}
    if len(by_id) != len(runs):
        raise ValueError("analysis contains duplicate run identifiers")
    hidden_ids = {item.template_id for item in suite.hidden_slots}
    public_ids = {item.template_id for item in suite.public_anchors}
    for scheduled in schedule.runs:
        observed = by_id[scheduled.run_id]
        if (
            observed.template_id != scheduled.template_id
            or observed.seed_id != scheduled.seed_id
            or observed.variant != scheduled.variant
        ):
            raise ValueError("analysis run mapping differs from the frozen schedule")
        expected_population: Population
        if scheduled.template_id in hidden_ids:
            expected_population = "HIDDEN"
        elif scheduled.template_id in public_ids:
            expected_population = "PUBLIC"
        else:
            raise ValueError("scheduled template is absent from the frozen suite")
        if observed.population != expected_population:
            raise ValueError("analysis population differs from the frozen suite")


def analyze_populations(
    runs: tuple[AnalysisRun, ...],
    *,
    suite: SuiteRegistry,
    schedule: ExecutionSchedule,
) -> dict[str, Any]:
    _validate_scheduled_analysis_runs(runs, suite=suite, schedule=schedule)
    hidden_ids = {item.template_id for item in suite.hidden_slots}
    hidden = tuple(item for item in runs if item.template_id in hidden_ids)
    public = tuple(item for item in runs if item.template_id not in hidden_ids)
    return {
        "hidden_only_primary": _population_summary(hidden),
        "full_suite_secondary": _population_summary(runs),
        "public_anchor_descriptive": _population_summary(public),
    }


def analyze_mock_population(runs: tuple[AnalysisRun, ...]) -> dict[str, Any]:
    if any(item.population != "SYNTHETIC" for item in runs):
        raise ValueError("mock analysis accepts only synthetic runs")
    return {"synthetic_only": _population_summary(runs)}


def hidden_primary_bootstrap(
    runs: tuple[AnalysisRun, ...],
    *,
    suite: SuiteRegistry,
    schedule: ExecutionSchedule,
    metric: MetricName,
) -> BootstrapResult:
    _validate_scheduled_analysis_runs(runs, suite=suite, schedule=schedule)
    hidden_ids = {item.template_id for item in suite.hidden_slots}
    hidden = tuple(item for item in runs if item.template_id in hidden_ids)
    result = hierarchical_paired_bootstrap(
        hidden,
        left_variant=_PRIMARY_LEFT,
        right_variant=_PRIMARY_RIGHT,
        metric=metric,
        replicates=_PRIMARY_REPLICATES,
        rng_seed=_PRIMARY_RNG_SEED,
    )
    return result.model_copy(
        update={"analysis_population": "HIDDEN_ONLY", "primary_eligible": True}
    )


def validate_complete_results(
    schedule: ExecutionSchedule,
    observed_run_ids: tuple[str, ...],
) -> None:
    expected = tuple(item.run_id for item in schedule.runs)
    if len(set(observed_run_ids)) != len(observed_run_ids):
        raise ValueError("execution results contain duplicate run identifiers")
    expected_set = set(expected)
    observed_set = set(observed_run_ids)
    missing = expected_set - observed_set
    extra = observed_set - expected_set
    if extra:
        raise ValueError("execution results contain extra run identifiers")
    if missing:
        raise ValueError("execution results are missing scheduled run identifiers")
