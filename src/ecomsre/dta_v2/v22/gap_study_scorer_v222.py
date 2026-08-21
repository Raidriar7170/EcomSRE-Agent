"""Routing, post-read, quality, protocol, and control metrics for v2.2.2."""

from __future__ import annotations

from collections import Counter
from statistics import fmean
from typing import Literal, cast

from pydantic import Field, StrictBool, StrictFloat, StrictInt

from ecomsre.dta_v2.v22.gap_router_v222 import GapRouterModeV222
from ecomsre.dta_v2.v22.evidence_utility_audit_v222 import (
    DevelopmentRoutingGateV222,
    EvidenceUtilityAuditReportV222,
)
from ecomsre.dta_v2.v22.gap_study_campaign_v222 import (
    StudyCombinationV222,
    combination_for_run_v222,
)
from ecomsre.dta_v2.v22.gap_study_runner_v222 import (
    GapStudyCaseRunV222,
    GapStudyRunStatusV222,
)
from ecomsre.dta_v2.v22.negative_coverage_v222 import ReadUtilityClassV222
from ecomsre.dta_v2.v22.practical_scorer import PracticalTruthV22
from ecomsre.dta_v2.v22.predicates import MechanismV22
from ecomsre.dta_v2.v22.read_contracts import DtaModelV22, ReadSourceStatusV22


def _ratio(numerator: int, denominator: int) -> float:
    return 0.0 if denominator == 0 else numerator / denominator


class CombinationMetricsV222(DtaModelV22):
    schema_version: Literal["dta-v22.2.combination-metrics.v1"]
    combination: StudyCombinationV222
    total_runs: StrictInt = Field(ge=1)
    incident_denominator: StrictInt = Field(ge=0)
    no_incident_denominator: StrictInt = Field(ge=0)
    abstention_denominator: StrictInt = Field(ge=0)
    adaptive_read_rate: StrictFloat = Field(ge=0, le=1)
    mean_adaptive_reads: StrictFloat = Field(ge=0)
    empty_read_rate: StrictFloat = Field(ge=0, le=1)
    nonempty_read_rate: StrictFloat = Field(ge=0, le=1)
    predicate_yield_rate: StrictFloat = Field(ge=0, le=1)
    gap_closure_rate: StrictFloat = Field(ge=0, le=1)
    clause_completion_rate: StrictFloat = Field(ge=0, le=1)
    source_distribution: dict[str, int]
    oracle_shortest_path_action_hit_rate: StrictFloat = Field(ge=0, le=1)
    negative_coverage_count: StrictInt = Field(ge=0)
    read_bearing_runs: StrictInt = Field(ge=0)
    read_bearing_runs_ending_diagnosis: StrictInt = Field(ge=0)
    diagnosis_after_read_rate: StrictFloat = Field(ge=0, le=1)
    correct_diagnosis_after_read: StrictInt = Field(ge=0)
    wrong_diagnosis_after_read: StrictInt = Field(ge=0)
    abstain_after_read: StrictInt = Field(ge=0)
    no_incident_after_read: StrictInt = Field(ge=0)
    protocol_failure_after_read: StrictInt = Field(ge=0)
    terminal_candidate_availability_after_read: StrictFloat = Field(ge=0, le=1)
    terminal_candidate_selection_accuracy: StrictFloat = Field(ge=0, le=1)
    end_to_end_exact_completion: StrictFloat = Field(ge=0, le=1)
    exact_completion_cases: StrictInt = Field(ge=0)
    valid_terminal_rate: StrictFloat = Field(ge=0, le=1)
    root_service_accuracy: StrictFloat = Field(ge=0, le=1)
    mechanism_accuracy: StrictFloat = Field(ge=0, le=1)
    mechanism_macro_f1: StrictFloat = Field(ge=0, le=1)
    no_incident_accuracy: StrictFloat = Field(ge=0, le=1)
    abstention_accuracy: StrictFloat = Field(ge=0, le=1)
    evidence_ref_validity: StrictFloat = Field(ge=0, le=1)
    semantic_clause_validity: StrictFloat = Field(ge=0, le=1)
    first_pass_protocol_success: StrictFloat = Field(ge=0, le=1)
    post_repair_protocol_success: StrictFloat = Field(ge=0, le=1)
    protocol_failure_rate: StrictFloat = Field(ge=0, le=1)
    repair_count: StrictInt = Field(ge=0)
    provider_calls: StrictInt = Field(ge=0)
    input_tokens: StrictInt = Field(ge=0)
    output_tokens: StrictInt = Field(ge=0)
    total_tokens: StrictInt = Field(ge=0)
    tokens_per_correct_case: StrictFloat = Field(ge=0)
    latency_ms: StrictFloat = Field(ge=0)
    transport_retries: StrictInt = Field(ge=0)
    uncaught_exceptions: StrictInt = Field(ge=0)
    agent_writes: Literal[0]
    unnecessary_read_rate: StrictFloat = Field(ge=0, le=1)
    combined_no_incident_abstention_accuracy: StrictFloat = Field(ge=0, le=1)


class DevelopmentUtilityGateV222(DtaModelV22):
    schema_version: Literal["dta-v22.2.development-utility-gate.v1"]
    gap_runs: StrictInt = Field(ge=1)
    predicate_yield_read_rate: StrictFloat = Field(ge=0, le=1)
    nonempty_or_predicate_yield_read_rate: StrictFloat = Field(ge=0, le=1)
    read_bearing_diagnosed_runs: StrictInt = Field(ge=0)
    protocol_failure_rate: StrictFloat = Field(ge=0, le=1)
    uncaught_exceptions: StrictInt = Field(ge=0)
    agent_writes: Literal[0]
    gate_passed: StrictBool


class ControlRegressionMetricsV222(DtaModelV22):
    schema_version: Literal["dta-v22.2.control-regression-metrics.v1"]
    arm: Literal["FLAT", "PLANNER"]
    no_incident_accuracy_regression: StrictFloat
    abstention_accuracy_regression: StrictFloat
    unnecessary_read_rate_delta: StrictFloat
    extra_control_provider_calls: StrictInt
    extra_control_tokens: StrictInt


class PooledRoutingEffectV222(DtaModelV22):
    schema_version: Literal["dta-v22.2.pooled-routing-effect.v1"]
    gap_predicate_yield_rate: StrictFloat = Field(ge=0, le=1)
    broad_predicate_yield_rate: StrictFloat = Field(ge=0, le=1)
    predicate_yield_improvement: StrictFloat
    gap_empty_read_rate: StrictFloat = Field(ge=0, le=1)
    broad_empty_read_rate: StrictFloat = Field(ge=0, le=1)
    empty_read_rate_decrease: StrictFloat
    gap_diagnosis_after_read_rate: StrictFloat = Field(ge=0, le=1)
    broad_diagnosis_after_read_rate: StrictFloat = Field(ge=0, le=1)
    diagnosis_after_read_improvement: StrictFloat
    gap_protocol_failure_rate: StrictFloat = Field(ge=0, le=1)
    broad_protocol_failure_rate: StrictFloat = Field(ge=0, le=1)


class GapStudyInterpretationV222(DtaModelV22):
    schema_version: Literal["dta-v22.2.gap-study-interpretation.v1"]
    engineering_terminal: Literal[
        "DTA_V22_2_GAP_ROUTING_QUALITY_EFFECT_OBSERVED",
        "DTA_V22_2_ROUTING_EFFECT_WITHOUT_QUALITY",
        "DTA_V22_2_NO_GAP_ROUTING_EFFECT_OBSERVED",
    ]
    quality_exact_case_condition: StrictBool
    quality_macro_f1_condition: StrictBool
    quality_diagnosis_after_read_condition: StrictBool
    quality_control_regression_condition: StrictBool
    routing_predicate_yield_condition: StrictBool
    routing_empty_read_condition: StrictBool
    routing_diagnosis_after_read_condition: StrictBool
    routing_protocol_condition: StrictBool
    planner_interaction_observed: StrictBool
    planner_diagnosis_improvement: StrictFloat
    flat_diagnosis_improvement: StrictFloat
    planner_macro_f1_improvement: StrictFloat
    flat_macro_f1_improvement: StrictFloat
    pooled_routing_effect: PooledRoutingEffectV222


class GapStudyScoreBundleV222(DtaModelV22):
    schema_version: Literal["dta-v22.2.gap-study-score-bundle.v1"]
    combinations: tuple[CombinationMetricsV222, ...]
    development_gate: DevelopmentUtilityGateV222
    top_k_useful_action_recall_turn_zero: StrictFloat = Field(ge=0, le=1)
    top_k_useful_action_recall_post_first_read: StrictFloat = Field(ge=0, le=1)
    control_regressions: tuple[ControlRegressionMetricsV222, ...]
    interpretation: GapStudyInterpretationV222 | None


def _exact(run: GapStudyCaseRunV222, truth: PracticalTruthV22) -> bool:
    if run.status is not GapStudyRunStatusV222.VALID_TERMINAL:
        return False
    if run.terminal != truth.expected_terminal:
        return False
    if truth.expected_terminal != "DIAGNOSED":
        return True
    return (
        run.root_service == truth.expected_root_service
        and run.mechanism == truth.expected_mechanism
    )


def _macro_f1(
    *, runs: tuple[GapStudyCaseRunV222, ...], truths: dict[str, PracticalTruthV22]
) -> float:
    values: list[float] = []
    labels = tuple(
        item.value
        for item in MechanismV22
        if item not in {MechanismV22.NO_INCIDENT, MechanismV22.UNKNOWN}
    )
    for label in labels:
        tp = sum(
            truths[run.case_id].expected_mechanism == label and run.mechanism == label
            for run in runs
        )
        fp = sum(
            truths[run.case_id].expected_mechanism != label and run.mechanism == label
            for run in runs
        )
        fn = sum(
            truths[run.case_id].expected_mechanism == label and run.mechanism != label
            for run in runs
        )
        precision = _ratio(tp, tp + fp)
        recall = _ratio(tp, tp + fn)
        values.append(
            0.0 if precision + recall == 0 else 2 * precision * recall / (precision + recall)
        )
    return fmean(values)


def _score_combination(
    *,
    combination: StudyCombinationV222,
    runs: tuple[GapStudyCaseRunV222, ...],
    truths: dict[str, PracticalTruthV22],
    shortest_paths: dict[str, frozenset[str]],
) -> CombinationMetricsV222:
    events = tuple(event for run in runs for event in run.adaptive_read_events)
    read_bearing = tuple(run for run in runs if run.adaptive_reads > 0)
    incident = tuple(run for run in runs if truths[run.case_id].expected_terminal == "DIAGNOSED")
    no_incident = tuple(run for run in runs if truths[run.case_id].expected_terminal == "NO_INCIDENT")
    abstention = tuple(run for run in runs if truths[run.case_id].expected_terminal == "ABSTAIN")
    exact = sum(_exact(run, truths[run.case_id]) for run in runs)
    diagnosed_after = tuple(
        run for run in read_bearing if run.terminal == "DIAGNOSED"
    )
    correct_after = sum(_exact(run, truths[run.case_id]) for run in diagnosed_after)
    failed_after = sum(
        run.status is GapStudyRunStatusV222.PROTOCOL_FAILED for run in read_bearing
    )
    candidate_after = tuple(
        run for run in read_bearing if run.terminal_candidate_available_after_read
    )
    incident_diagnosed = tuple(run for run in incident if run.terminal == "DIAGNOSED")
    support_valid = sum(
        bool(run.supporting_evidence_refs) and run.matched_clause_id is not None
        for run in incident_diagnosed
    )
    control = (*no_incident, *abstention)
    control_correct = sum(_exact(run, truths[run.case_id]) for run in control)
    provider_turns = sum(run.provider_turns for run in runs)
    sources = Counter(event.source for event in events)
    return CombinationMetricsV222(
        schema_version="dta-v22.2.combination-metrics.v1",
        combination=combination,
        total_runs=len(runs),
        incident_denominator=len(incident),
        no_incident_denominator=len(no_incident),
        abstention_denominator=len(abstention),
        adaptive_read_rate=_ratio(len(read_bearing), len(runs)),
        mean_adaptive_reads=fmean(run.adaptive_reads for run in runs),
        empty_read_rate=_ratio(
            sum(event.outcome_class is ReadUtilityClassV222.EMPTY_CAPTURED for event in events),
            len(events),
        ),
        nonempty_read_rate=_ratio(
            sum(event.status is ReadSourceStatusV22.SUCCESS_NONEMPTY for event in events),
            len(events),
        ),
        predicate_yield_rate=_ratio(
            sum(event.outcome_class is ReadUtilityClassV222.PREDICATE_YIELD for event in events),
            len(events),
        ),
        gap_closure_rate=_ratio(
            sum(event.minimum_gap_after < event.minimum_gap_before for event in events),
            len(events),
        ),
        clause_completion_rate=_ratio(
            sum(event.minimum_gap_after == 0 < event.minimum_gap_before for event in events),
            len(events),
        ),
        source_distribution=dict(sorted(sources.items())),
        oracle_shortest_path_action_hit_rate=_ratio(
            sum(
                bool(
                    shortest_paths.get(run.case_id, frozenset()).intersection(
                        event.action_id for event in run.adaptive_read_events
                    )
                )
                for run in incident
                if shortest_paths.get(run.case_id)
            ),
            sum(bool(shortest_paths.get(run.case_id)) for run in incident),
        ),
        negative_coverage_count=sum(run.negative_coverage_count for run in runs),
        read_bearing_runs=len(read_bearing),
        read_bearing_runs_ending_diagnosis=len(diagnosed_after),
        diagnosis_after_read_rate=_ratio(len(diagnosed_after), len(read_bearing)),
        correct_diagnosis_after_read=correct_after,
        wrong_diagnosis_after_read=len(diagnosed_after) - correct_after,
        abstain_after_read=sum(run.terminal == "ABSTAIN" for run in read_bearing),
        no_incident_after_read=sum(run.terminal == "NO_INCIDENT" for run in read_bearing),
        protocol_failure_after_read=failed_after,
        terminal_candidate_availability_after_read=_ratio(
            len(candidate_after), len(read_bearing)
        ),
        terminal_candidate_selection_accuracy=_ratio(
            sum(_exact(run, truths[run.case_id]) for run in candidate_after),
            len(candidate_after),
        ),
        end_to_end_exact_completion=_ratio(exact, len(runs)),
        exact_completion_cases=exact,
        valid_terminal_rate=_ratio(
            sum(run.status is GapStudyRunStatusV222.VALID_TERMINAL for run in runs),
            len(runs),
        ),
        root_service_accuracy=_ratio(
            sum(run.root_service == truths[run.case_id].expected_root_service for run in incident),
            len(incident),
        ),
        mechanism_accuracy=_ratio(
            sum(run.mechanism == truths[run.case_id].expected_mechanism for run in incident),
            len(incident),
        ),
        mechanism_macro_f1=_macro_f1(runs=runs, truths=truths),
        no_incident_accuracy=_ratio(
            sum(_exact(run, truths[run.case_id]) for run in no_incident),
            len(no_incident),
        ),
        abstention_accuracy=_ratio(
            sum(_exact(run, truths[run.case_id]) for run in abstention),
            len(abstention),
        ),
        evidence_ref_validity=_ratio(support_valid, len(incident_diagnosed)),
        semantic_clause_validity=_ratio(support_valid, len(incident_diagnosed)),
        first_pass_protocol_success=_ratio(
            sum(run.first_pass_protocol_successes for run in runs), provider_turns
        ),
        post_repair_protocol_success=_ratio(
            sum(run.post_repair_protocol_successes for run in runs), provider_turns
        ),
        protocol_failure_rate=_ratio(
            sum(run.status is GapStudyRunStatusV222.PROTOCOL_FAILED for run in runs),
            len(runs),
        ),
        repair_count=sum(run.protocol_repairs for run in runs),
        provider_calls=sum(run.provider_calls for run in runs),
        input_tokens=sum(run.input_tokens for run in runs),
        output_tokens=sum(run.output_tokens for run in runs),
        total_tokens=sum(run.total_tokens for run in runs),
        tokens_per_correct_case=_ratio(sum(run.total_tokens for run in runs), exact),
        latency_ms=sum(run.latency_ms for run in runs),
        transport_retries=sum(run.transport_retry_count for run in runs),
        uncaught_exceptions=sum(run.uncaught_exceptions for run in runs),
        agent_writes=0,
        unnecessary_read_rate=_ratio(
            sum(run.adaptive_reads > 0 for run in control), len(control)
        ),
        combined_no_incident_abstention_accuracy=_ratio(control_correct, len(control)),
    )


def _pooled_routing_effect(
    runs: tuple[GapStudyCaseRunV222, ...],
) -> PooledRoutingEffectV222:
    gap_runs = tuple(
        run for run in runs if run.router_mode is GapRouterModeV222.GAP_RANKED_TOP_K
    )
    broad_runs = tuple(
        run for run in runs if run.router_mode is GapRouterModeV222.BROAD_CATALOG
    )
    gap_events = tuple(event for run in gap_runs for event in run.adaptive_read_events)
    broad_events = tuple(event for run in broad_runs for event in run.adaptive_read_events)

    def predicate_rate(events: tuple[object, ...]) -> float:
        return _ratio(
            sum(
                getattr(event, "outcome_class")
                is ReadUtilityClassV222.PREDICATE_YIELD
                for event in events
            ),
            len(events),
        )

    def empty_rate(events: tuple[object, ...]) -> float:
        return _ratio(
            sum(
                getattr(event, "outcome_class")
                is ReadUtilityClassV222.EMPTY_CAPTURED
                for event in events
            ),
            len(events),
        )

    def diagnosis_after(runs: tuple[GapStudyCaseRunV222, ...]) -> float:
        bearing = tuple(run for run in runs if run.adaptive_reads > 0)
        return _ratio(sum(run.terminal == "DIAGNOSED" for run in bearing), len(bearing))

    gap_predicate = predicate_rate(gap_events)
    broad_predicate = predicate_rate(broad_events)
    gap_empty = empty_rate(gap_events)
    broad_empty = empty_rate(broad_events)
    gap_diagnosis = diagnosis_after(gap_runs)
    broad_diagnosis = diagnosis_after(broad_runs)
    gap_protocol = _ratio(
        sum(run.status is GapStudyRunStatusV222.PROTOCOL_FAILED for run in gap_runs),
        len(gap_runs),
    )
    broad_protocol = _ratio(
        sum(run.status is GapStudyRunStatusV222.PROTOCOL_FAILED for run in broad_runs),
        len(broad_runs),
    )
    return PooledRoutingEffectV222(
        schema_version="dta-v22.2.pooled-routing-effect.v1",
        gap_predicate_yield_rate=gap_predicate,
        broad_predicate_yield_rate=broad_predicate,
        predicate_yield_improvement=gap_predicate - broad_predicate,
        gap_empty_read_rate=gap_empty,
        broad_empty_read_rate=broad_empty,
        empty_read_rate_decrease=broad_empty - gap_empty,
        gap_diagnosis_after_read_rate=gap_diagnosis,
        broad_diagnosis_after_read_rate=broad_diagnosis,
        diagnosis_after_read_improvement=gap_diagnosis - broad_diagnosis,
        gap_protocol_failure_rate=gap_protocol,
        broad_protocol_failure_rate=broad_protocol,
    )


def _controls(
    *,
    metrics: dict[StudyCombinationV222, CombinationMetricsV222],
    runs: tuple[GapStudyCaseRunV222, ...],
    truths: dict[str, PracticalTruthV22],
) -> tuple[ControlRegressionMetricsV222, ...]:
    if set(metrics) != set(StudyCombinationV222):
        return ()
    result: list[ControlRegressionMetricsV222] = []
    for arm, broad, gap in (
        ("FLAT", StudyCombinationV222.FLAT_BROAD, StudyCombinationV222.FLAT_GAP),
        (
            "PLANNER",
            StudyCombinationV222.PLANNER_BROAD,
            StudyCombinationV222.PLANNER_GAP,
        ),
    ):
        broad_runs = tuple(
            run
            for run in runs
            if combination_for_run_v222(run) is broad
            and truths[run.case_id].expected_terminal != "DIAGNOSED"
        )
        gap_runs = tuple(
            run
            for run in runs
            if combination_for_run_v222(run) is gap
            and truths[run.case_id].expected_terminal != "DIAGNOSED"
        )
        broad_score = metrics[broad]
        gap_score = metrics[gap]
        result.append(
            ControlRegressionMetricsV222(
                schema_version="dta-v22.2.control-regression-metrics.v1",
                arm=cast(Literal["FLAT", "PLANNER"], arm),
                no_incident_accuracy_regression=(
                    broad_score.no_incident_accuracy - gap_score.no_incident_accuracy
                ),
                abstention_accuracy_regression=(
                    broad_score.abstention_accuracy - gap_score.abstention_accuracy
                ),
                unnecessary_read_rate_delta=(
                    gap_score.unnecessary_read_rate - broad_score.unnecessary_read_rate
                ),
                extra_control_provider_calls=(
                    sum(run.provider_calls for run in gap_runs)
                    - sum(run.provider_calls for run in broad_runs)
                ),
                extra_control_tokens=(
                    sum(run.total_tokens for run in gap_runs)
                    - sum(run.total_tokens for run in broad_runs)
                ),
            )
        )
    return tuple(result)


def _interpretation(
    *, metrics: dict[StudyCombinationV222, CombinationMetricsV222], pooled: PooledRoutingEffectV222
) -> GapStudyInterpretationV222 | None:
    if set(metrics) != set(StudyCombinationV222):
        return None
    flat_broad = metrics[StudyCombinationV222.FLAT_BROAD]
    flat_gap = metrics[StudyCombinationV222.FLAT_GAP]
    planner_broad = metrics[StudyCombinationV222.PLANNER_BROAD]
    planner_gap = metrics[StudyCombinationV222.PLANNER_GAP]
    exact_condition = (
        planner_gap.exact_completion_cases >= planner_broad.exact_completion_cases + 2
    )
    macro_condition = (
        planner_gap.mechanism_macro_f1 >= planner_broad.mechanism_macro_f1 + 0.15
    )
    diagnosis_condition = (
        planner_gap.diagnosis_after_read_rate
        >= planner_broad.diagnosis_after_read_rate + 0.15
    )
    control_condition = (
        planner_broad.combined_no_incident_abstention_accuracy
        - planner_gap.combined_no_incident_abstention_accuracy
        <= 0.125
    )
    routing_predicate = pooled.predicate_yield_improvement >= 0.20
    routing_empty = pooled.empty_read_rate_decrease >= 0.20
    routing_diagnosis = pooled.diagnosis_after_read_improvement >= 0.10
    routing_protocol = (
        pooled.gap_protocol_failure_rate <= pooled.broad_protocol_failure_rate + 0.05
    )
    terminal: Literal[
        "DTA_V22_2_GAP_ROUTING_QUALITY_EFFECT_OBSERVED",
        "DTA_V22_2_ROUTING_EFFECT_WITHOUT_QUALITY",
        "DTA_V22_2_NO_GAP_ROUTING_EFFECT_OBSERVED",
    ]
    if (exact_condition or macro_condition) and diagnosis_condition and control_condition:
        terminal = "DTA_V22_2_GAP_ROUTING_QUALITY_EFFECT_OBSERVED"
    elif routing_predicate and routing_empty and routing_diagnosis and routing_protocol:
        terminal = "DTA_V22_2_ROUTING_EFFECT_WITHOUT_QUALITY"
    else:
        terminal = "DTA_V22_2_NO_GAP_ROUTING_EFFECT_OBSERVED"
    planner_diagnosis = (
        planner_gap.diagnosis_after_read_rate - planner_broad.diagnosis_after_read_rate
    )
    flat_diagnosis = flat_gap.diagnosis_after_read_rate - flat_broad.diagnosis_after_read_rate
    planner_macro = planner_gap.mechanism_macro_f1 - planner_broad.mechanism_macro_f1
    flat_macro = flat_gap.mechanism_macro_f1 - flat_broad.mechanism_macro_f1
    interaction = (
        planner_gap.diagnosis_after_read_rate > flat_gap.diagnosis_after_read_rate
        and planner_gap.mechanism_macro_f1 > flat_gap.mechanism_macro_f1
        and planner_diagnosis > flat_diagnosis
        and planner_macro > flat_macro
    )
    return GapStudyInterpretationV222(
        schema_version="dta-v22.2.gap-study-interpretation.v1",
        engineering_terminal=terminal,
        quality_exact_case_condition=exact_condition,
        quality_macro_f1_condition=macro_condition,
        quality_diagnosis_after_read_condition=diagnosis_condition,
        quality_control_regression_condition=control_condition,
        routing_predicate_yield_condition=routing_predicate,
        routing_empty_read_condition=routing_empty,
        routing_diagnosis_after_read_condition=routing_diagnosis,
        routing_protocol_condition=routing_protocol,
        planner_interaction_observed=interaction,
        planner_diagnosis_improvement=planner_diagnosis,
        flat_diagnosis_improvement=flat_diagnosis,
        planner_macro_f1_improvement=planner_macro,
        flat_macro_f1_improvement=flat_macro,
        pooled_routing_effect=pooled,
    )


def score_gap_study_v222(
    *,
    runs: tuple[GapStudyCaseRunV222, ...],
    truths: tuple[PracticalTruthV22, ...],
    utility_audit: EvidenceUtilityAuditReportV222 | None = None,
    routing_gate: DevelopmentRoutingGateV222 | None = None,
    include_interpretation: bool = False,
) -> GapStudyScoreBundleV222:
    truth_by_id = {item.case_id: item for item in truths}
    if not set(run.case_id for run in runs).issubset(truth_by_id):
        raise ValueError("scorer run lacks evaluator truth")
    shortest_paths = (
        {}
        if utility_audit is None
        else {
            item.case_id: frozenset(item.shortest_action_ids or ())
            for item in utility_audit.cases
        }
    )
    combinations = tuple(
        _score_combination(
            combination=combination,
            runs=selected,
            truths=truth_by_id,
            shortest_paths=shortest_paths,
        )
        for combination in StudyCombinationV222
        if (
            selected := tuple(
                run for run in runs if combination_for_run_v222(run) is combination
            )
        )
    )
    gap_runs = tuple(
        run for run in runs if run.router_mode is GapRouterModeV222.GAP_RANKED_TOP_K
    )
    gap_events = tuple(event for run in gap_runs for event in run.adaptive_read_events)
    predicate_rate = _ratio(
        sum(event.outcome_class is ReadUtilityClassV222.PREDICATE_YIELD for event in gap_events),
        len(gap_events),
    )
    nonempty_rate = _ratio(
        sum(
            event.outcome_class
            in {
                ReadUtilityClassV222.PREDICATE_YIELD,
                ReadUtilityClassV222.NONEMPTY_NO_PREDICATE,
            }
            for event in gap_events
        ),
        len(gap_events),
    )
    diagnosed = sum(
        run.adaptive_reads > 0 and run.terminal == "DIAGNOSED" for run in gap_runs
    )
    protocol_rate = _ratio(
        sum(run.status is GapStudyRunStatusV222.PROTOCOL_FAILED for run in gap_runs),
        len(gap_runs),
    )
    uncaught = sum(run.uncaught_exceptions for run in gap_runs)
    gate = DevelopmentUtilityGateV222(
        schema_version="dta-v22.2.development-utility-gate.v1",
        gap_runs=len(gap_runs),
        predicate_yield_read_rate=predicate_rate,
        nonempty_or_predicate_yield_read_rate=nonempty_rate,
        read_bearing_diagnosed_runs=diagnosed,
        protocol_failure_rate=protocol_rate,
        uncaught_exceptions=uncaught,
        agent_writes=0,
        gate_passed=(
            predicate_rate >= 0.30
            and nonempty_rate >= 0.45
            and diagnosed >= 2
            and protocol_rate <= 0.25
            and uncaught == 0
        ),
    )
    metrics_by_combination = {item.combination: item for item in combinations}
    pooled = _pooled_routing_effect(runs)
    return GapStudyScoreBundleV222(
        schema_version="dta-v22.2.gap-study-score-bundle.v1",
        combinations=combinations,
        development_gate=gate,
        top_k_useful_action_recall_turn_zero=(
            0.0 if routing_gate is None else routing_gate.turn_zero_recall
        ),
        top_k_useful_action_recall_post_first_read=(
            0.0 if routing_gate is None else routing_gate.post_first_read_recall
        ),
        control_regressions=_controls(
            metrics=metrics_by_combination,
            runs=runs,
            truths=truth_by_id,
        ),
        interpretation=(
            _interpretation(metrics=metrics_by_combination, pooled=pooled)
            if include_interpretation
            else None
        ),
    )


__all__ = (
    "CombinationMetricsV222",
    "ControlRegressionMetricsV222",
    "DevelopmentUtilityGateV222",
    "GapStudyScoreBundleV222",
    "GapStudyInterpretationV222",
    "PooledRoutingEffectV222",
    "score_gap_study_v222",
)
