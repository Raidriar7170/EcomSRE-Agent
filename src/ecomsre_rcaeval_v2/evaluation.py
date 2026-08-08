"""Private outcome scoring and case-free public development aggregation."""

from __future__ import annotations

from collections import Counter, defaultdict
from statistics import median
from typing import Literal

from pydantic import Field, StrictFloat, StrictInt

from ecomsre_rcaeval_v2.contracts import (
    CanonicalIndicator,
    OperationStatus,
    OperationStage,
    OperationType,
    SourceName,
    V2Model,
)
from ecomsre_rcaeval_v2.schedule import SplitName, Variant
from ecomsre_rcaeval_v2.statistics import (
    BootstrapInterval,
    PairedObservation,
    hierarchical_paired_bootstrap,
)


class Rate(V2Model):
    numerator: StrictInt = Field(ge=0)
    denominator: StrictInt = Field(ge=0)
    value: StrictFloat


class PrivateSpecialistOutcome(V2Model):
    source: SourceName
    candidate_service: str | None
    candidate_indicator: CanonicalIndicator | None
    confidence: StrictFloat = Field(ge=0.0, le=1.0)


class PrivateRunOutcome(V2Model):
    schema_version: Literal["rcaeval-re2-v2-dev1.private-run-outcome.v1"]
    system: Literal["RE2-OB", "RE2-SS"]
    root_cause_service: str
    fault: Literal["cpu", "mem", "disk", "delay", "loss", "socket"]
    instance: str
    split: SplitName
    variant: Variant
    terminal_status: OperationStatus
    predicted_service: str | None
    predicted_indicator: CanonicalIndicator | None
    tool_calls: StrictInt = Field(ge=0)
    model_calls: StrictInt = Field(ge=0)
    total_tokens: StrictInt = Field(ge=0)
    token_usage_known: bool
    latency_ms: StrictFloat = Field(ge=0.0)
    failure_operation_type: OperationType | None
    failure_stage: OperationStage | None
    specialists: tuple[PrivateSpecialistOutcome, ...] = Field(max_length=3)
    commander_selected_sources: tuple[Literal["logs", "traces"], ...] = Field(
        max_length=2
    )
    indicator_candidate_pairs: tuple[
        tuple[str, CanonicalIndicator], ...
    ] = Field(max_length=6)
    indicator_disposition: Literal["RESOLVED", "NO_INDICATOR_CANDIDATE"] | None

    @property
    def identity(self) -> tuple[str, str, str, str]:
        return (self.system, self.root_cause_service, self.fault, self.instance)

    @property
    def truth_indicator(self) -> CanonicalIndicator:
        return {
            "cpu": "cpu",
            "mem": "mem",
            "disk": "diskio",
            "delay": "latency",
            "loss": "latency",
            "socket": "socket",
        }[self.fault]  # type: ignore[return-value]

    @property
    def service_correct(self) -> bool:
        return self.predicted_service == self.root_cause_service

    @property
    def pair_correct(self) -> bool:
        return self.service_correct and self.predicted_indicator == self.truth_indicator


def rate(numerator: int, denominator: int) -> Rate:
    if numerator < 0 or denominator < 0 or numerator > denominator:
        raise ValueError("rate counts are invalid")
    return Rate(
        numerator=numerator,
        denominator=denominator,
        value=float(numerator / denominator) if denominator else 0.0,
    )


def _architecture_summary(outcomes: tuple[PrivateRunOutcome, ...]) -> dict[str, object]:
    completed = tuple(
        item for item in outcomes if item.terminal_status is OperationStatus.COMPLETED
    )
    known_tokens = tuple(item for item in outcomes if item.token_usage_known)
    return {
        "scheduled_runs": len(outcomes),
        "completed_runs": rate(len(completed), len(outcomes)).model_dump(mode="json"),
        "root_service_ac_at_1": rate(
            sum(item.service_correct for item in outcomes), len(outcomes)
        ).model_dump(mode="json"),
        "root_cause_pair_ac_at_1": rate(
            sum(item.pair_correct for item in outcomes), len(outcomes)
        ).model_dump(mode="json"),
        "completed_only_root_service_ac_at_1": rate(
            sum(item.service_correct for item in completed), len(completed)
        ).model_dump(mode="json"),
        "completed_only_root_cause_pair_ac_at_1": rate(
            sum(item.pair_correct for item in completed), len(completed)
        ).model_dump(mode="json"),
        "cost": {
            "tool_calls_total": sum(item.tool_calls for item in outcomes),
            "tool_calls_mean": float(
                sum(item.tool_calls for item in outcomes) / len(outcomes)
            ) if outcomes else 0.0,
            "tool_calls_median": float(
                median(item.tool_calls for item in outcomes)
            ) if outcomes else 0.0,
            "model_calls_total": sum(item.model_calls for item in outcomes),
            "model_calls_mean": float(
                sum(item.model_calls for item in outcomes) / len(outcomes)
            ) if outcomes else 0.0,
            "model_calls_median": float(
                median(item.model_calls for item in outcomes)
            ) if outcomes else 0.0,
            "known_token_coverage": rate(
                len(known_tokens), len(outcomes)
            ).model_dump(mode="json"),
            "total_tokens_known": sum(item.total_tokens for item in known_tokens),
            "tokens_mean_known": float(
                sum(item.total_tokens for item in known_tokens) / len(known_tokens)
            ) if known_tokens else 0.0,
            "tokens_median_known": float(
                median(item.total_tokens for item in known_tokens)
            ) if known_tokens else 0.0,
            "latency_ms_total": float(sum(item.latency_ms for item in outcomes)),
            "latency_ms_mean": float(
                sum(item.latency_ms for item in outcomes) / len(outcomes)
            ) if outcomes else 0.0,
            "latency_ms_median": float(
                median(item.latency_ms for item in outcomes)
            ) if outcomes else 0.0,
        },
    }


def _specialist_attribution(
    outcomes: tuple[PrivateRunOutcome, ...],
) -> dict[str, object]:
    by_source: dict[str, list[tuple[PrivateRunOutcome, PrivateSpecialistOutcome]]] = (
        defaultdict(list)
    )
    for run in outcomes:
        for specialist in run.specialists:
            by_source[specialist.source].append((run, specialist))
    result: dict[str, object] = {}
    for source in ("metrics", "logs", "traces"):
        rows = by_source[source]
        missing = sum(item.candidate_service is None for _, item in rows)
        service_correct = sum(
            item.candidate_service == run.root_cause_service for run, item in rows
        )
        indicator_correct = sum(
            item.candidate_indicator == run.truth_indicator for run, item in rows
        )
        pair_correct = sum(
            item.candidate_service == run.root_cause_service
            and item.candidate_indicator == run.truth_indicator
            for run, item in rows
        )
        brier = (
            sum(
                (
                    item.confidence
                    - float(item.candidate_service == run.root_cause_service)
                )
                ** 2
                for run, item in rows
            )
            / len(rows)
            if rows
            else 0.0
        )
        result[source] = {
            "candidate_service_accuracy": rate(service_correct, len(rows)).model_dump(
                mode="json"
            ),
            "candidate_indicator_accuracy": rate(
                indicator_correct, len(rows)
            ).model_dump(mode="json"),
            "candidate_pair_accuracy": rate(pair_correct, len(rows)).model_dump(
                mode="json"
            ),
            "candidate_missing_rate": rate(missing, len(rows)).model_dump(mode="json"),
            "service_confidence_brier": float(brier),
        }
    return result


def _disagreement_and_judge(
    outcomes: tuple[PrivateRunOutcome, ...],
) -> tuple[dict[str, int], dict[str, object]]:
    disagreement = Counter(
        {
            "all_agree": 0,
            "two_vs_one": 0,
            "all_disagree": 0,
            "correct_minority": 0,
            "correct_majority": 0,
        }
    )
    follows = Counter({source: 0 for source in ("metrics", "logs", "traces")})
    judge = Counter(
        {
            "follows_correct_specialist_candidate": 0,
            "selects_no_specialist_candidate": 0,
            "overrides_correct_specialist_with_wrong_final": 0,
            "repairs_wrong_specialists": 0,
        }
    )
    judge_denominator = 0
    for run in outcomes:
        if run.predicted_service is None:
            continue
        judge_denominator += 1
        predicted_pair = (run.predicted_service, run.predicted_indicator)
        specialist_pairs = [
            (item.candidate_service, item.candidate_indicator)
            for item in run.specialists
            if item.candidate_service is not None
        ]
        for item in run.specialists:
            if (item.candidate_service, item.candidate_indicator) == predicted_pair:
                follows[item.source] += 1
                if run.pair_correct:
                    judge["follows_correct_specialist_candidate"] += 1
        if predicted_pair not in specialist_pairs:
            judge["selects_no_specialist_candidate"] += 1
        specialist_correct = [
            pair == (run.root_cause_service, run.truth_indicator)
            for pair in specialist_pairs
        ]
        if any(specialist_correct) and not run.pair_correct:
            judge["overrides_correct_specialist_with_wrong_final"] += 1
        if specialist_pairs and not any(specialist_correct) and run.pair_correct:
            judge["repairs_wrong_specialists"] += 1
        if len(specialist_pairs) == 3:
            counts = Counter(specialist_pairs)
            sizes = sorted(counts.values(), reverse=True)
            if sizes == [3]:
                disagreement["all_agree"] += 1
            elif sizes == [2, 1]:
                disagreement["two_vs_one"] += 1
                truth = (run.root_cause_service, run.truth_indicator)
                if counts[truth] == 1:
                    disagreement["correct_minority"] += 1
                elif counts[truth] == 2:
                    disagreement["correct_majority"] += 1
            elif sizes == [1, 1, 1]:
                disagreement["all_disagree"] += 1
    return dict(disagreement), {
        "denominator": judge_denominator,
        "follows": dict(follows),
        **dict(judge),
    }


def _routes(outcomes: tuple[PrivateRunOutcome, ...]) -> dict[str, object]:
    dynamic = tuple(item for item in outcomes if item.variant is Variant.DYNAMIC_V2)
    counts = Counter(
        {
            "logs_only": 0,
            "traces_only": 0,
            "both": 0,
            "route_failure": 0,
        }
    )
    route_rows: dict[str, list[PrivateRunOutcome]] = defaultdict(list)
    for item in dynamic:
        route = item.commander_selected_sources
        if route == ("logs",):
            counts["logs_only"] += 1
            route_name = "logs_only"
        elif route == ("traces",):
            counts["traces_only"] += 1
            route_name = "traces_only"
        elif set(route) == {"logs", "traces"}:
            counts["both"] += 1
            route_name = "both"
        else:
            counts["route_failure"] += 1
            route_name = "route_failure"
        route_rows[route_name].append(item)
    return {
        "distribution": dict(counts),
        "by_route": {
            name: {
                "scheduled_runs": len(rows),
                "completed_runs": rate(
                    sum(
                        item.terminal_status is OperationStatus.COMPLETED
                        for item in rows
                    ),
                    len(rows),
                ).model_dump(mode="json"),
                "root_service_ac_at_1": rate(
                    sum(item.service_correct for item in rows), len(rows)
                ).model_dump(mode="json"),
                "terminal_failures": sum(
                    item.terminal_status is not OperationStatus.COMPLETED
                    for item in rows
                ),
            }
            for name, rows in sorted(route_rows.items())
        },
    }


def _multi_agent_damage_rescue(
    outcomes: tuple[PrivateRunOutcome, ...],
) -> dict[str, object]:
    indexed = {(item.identity, item.variant): item for item in outcomes}
    result: dict[str, object] = {
        "evaluation_metric": "root_cause_pair_ac_at_1"
    }
    for label, candidate in (
        ("fixed", Variant.FIXED_V2),
        ("dynamic", Variant.DYNAMIC_V2),
    ):
        rows = tuple(
            (item, indexed.get((item.identity, candidate)))
            for item in outcomes
            if item.variant is Variant.SINGLE_V2
        )
        paired = tuple((single, other) for single, other in rows if other is not None)
        result[label] = {
            "paired_cases": len(paired),
            "single_correct_candidate_wrong": sum(
                single.pair_correct and not other.pair_correct
                for single, other in paired
            ),
            "single_wrong_candidate_correct": sum(
                not single.pair_correct and other.pair_correct
                for single, other in paired
            ),
        }
    return result


def _paired(
    outcomes: tuple[PrivateRunOutcome, ...],
    baseline: Variant,
    candidate: Variant,
    *,
    endpoint: Literal["service", "pair"],
) -> dict[str, object]:
    indexed = {(item.identity, item.variant): item for item in outcomes}
    identities = sorted(
        identity
        for identity, variant in indexed
        if variant is baseline and (identity, candidate) in indexed
    )
    observations = tuple(
        PairedObservation(
            system=identity[0],
            service=identity[1],
            fault=identity[2],
            baseline=float(
                indexed[(identity, baseline)].service_correct
                if endpoint == "service"
                else indexed[(identity, baseline)].pair_correct
            ),
            candidate=float(
                indexed[(identity, candidate)].service_correct
                if endpoint == "service"
                else indexed[(identity, candidate)].pair_correct
            ),
        )
        for identity in identities
    )
    interval: BootstrapInterval = hierarchical_paired_bootstrap(observations)
    return {
        "baseline": baseline.value,
        "candidate": candidate.value,
        "evaluation_metric": endpoint,
        "paired_cases": len(observations),
        "bootstrap": {
            "iterations": interval.iterations,
            "seed": interval.seed,
            "point_estimate": interval.point_estimate,
            "lower_95": interval.lower_95,
            "upper_95": interval.upper_95,
        },
    }


def aggregate_development_outcomes(
    outcomes: tuple[PrivateRunOutcome, ...],
    *,
    split: SplitName,
) -> dict[str, object]:
    """Return a public, case-free aggregate for exactly one development split."""

    selected = tuple(item for item in outcomes if item.split is split)
    if not selected:
        raise ValueError("development aggregation contains no selected outcomes")
    variants = tuple(sorted({item.variant for item in selected}, key=lambda item: item.value))
    architecture_summaries = {
        variant.value: _architecture_summary(
            tuple(item for item in selected if item.variant is variant)
        )
        for variant in variants
    }
    terminal_taxonomy = {
        variant.value: dict(
            sorted(
                Counter(
                    item.terminal_status.value
                    for item in selected
                    if item.variant is variant
                ).items()
            )
        )
        for variant in variants
    }
    failure_stage: dict[str, dict[str, int]] = {}
    for operation_type in OperationType:
        rows = tuple(
            item
            for item in selected
            if item.failure_operation_type is operation_type
            and item.failure_stage is not None
        )
        if rows:
            failure_stage[operation_type.value] = dict(
                sorted(
                    Counter(
                        item.failure_stage.value
                        for item in rows
                        if item.failure_stage is not None
                    ).items()
                )
            )
    per_fault = {
        fault: {
            variant.value: {
                "root_service_ac_at_1": rate(
                    sum(item.service_correct for item in rows), len(rows)
                ).model_dump(mode="json"),
                "root_cause_pair_ac_at_1": rate(
                    sum(item.pair_correct for item in rows), len(rows)
                ).model_dump(mode="json"),
            }
            for variant in variants
            if (
                rows := tuple(
                    item
                    for item in selected
                    if item.fault == fault and item.variant is variant
                )
            )
        }
        for fault in ("cpu", "mem", "disk", "delay", "loss", "socket")
    }
    specialist, judge = _disagreement_and_judge(selected)
    paired: dict[str, object] = {}
    available = set(variants)
    for name, baseline, candidate, endpoint in (
        (
            "single_v2_minus_single_v1_pair",
            Variant.SINGLE_V1_REFERENCE,
            Variant.SINGLE_V2,
            "pair",
        ),
        (
            "single_v2_minus_single_v1_service",
            Variant.SINGLE_V1_REFERENCE,
            Variant.SINGLE_V2,
            "service",
        ),
        (
            "fixed_v2_minus_single_v2_service",
            Variant.SINGLE_V2,
            Variant.FIXED_V2,
            "service",
        ),
        (
            "dynamic_v2_minus_single_v2_service",
            Variant.SINGLE_V2,
            Variant.DYNAMIC_V2,
            "service",
        ),
    ):
        if baseline in available and candidate in available:
            paired[name] = _paired(
                selected, baseline, candidate, endpoint=endpoint  # type: ignore[arg-type]
            )
    indicator_funnel = {
        fault: {
            "truth_pair_in_top_6": rate(
                sum(
                    (item.root_cause_service, item.truth_indicator)
                    in item.indicator_candidate_pairs
                    for item in rows
                ),
                len(rows),
            ).model_dump(mode="json"),
            "root_service_selected": rate(
                sum(item.service_correct for item in rows), len(rows)
            ).model_dump(mode="json"),
            "pair_resolved_correctly": rate(
                sum(item.pair_correct for item in rows), len(rows)
            ).model_dump(mode="json"),
        }
        for fault in ("cpu", "mem", "disk", "delay", "loss", "socket")
        if (
            rows := tuple(
                item
                for item in selected
                if item.fault == fault and item.variant is Variant.SINGLE_V2
            )
        )
    }
    return {
        "schema_version": "rcaeval-re2-v2-dev1.aggregate-split.v1",
        "classification": [
            "DEVELOPMENT_VISIBLE",
            "DESIGN_SET",
            "NOT_EXTERNAL_HOLDOUT",
            "NOT_PRIMARY_INFERENCE",
        ],
        "split": split.value,
        "run_accounting": {
            "scheduled": len(selected),
            "terminalized": len(selected),
            "semantic_retries": 0,
        },
        "architecture_summaries": architecture_summaries,
        "terminal_taxonomy": terminal_taxonomy,
        "failure_stage_taxonomy": failure_stage,
        "unattributed_terminal_failures": sum(
            item.terminal_status is not OperationStatus.COMPLETED
            and item.failure_stage is None
            for item in selected
        ),
        "specialist_candidate_accuracy": _specialist_attribution(selected),
        "specialist_disagreement": specialist,
        "judge_follow_override": judge,
        "dynamic_route_distribution": _routes(selected),
        "multi_agent_damage_rescue": _multi_agent_damage_rescue(selected),
        "indicator_funnel": indicator_funnel,
        "per_fault_aggregates": per_fault,
        "paired_development_comparisons": paired,
    }
