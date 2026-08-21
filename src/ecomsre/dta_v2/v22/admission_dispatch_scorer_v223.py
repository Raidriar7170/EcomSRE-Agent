"""Admission, dispatch, post-read, quality, and factorial metrics for v2.2.3."""

from __future__ import annotations

from collections import Counter
from statistics import fmean
from typing import Literal

from pydantic import Field, StrictBool, StrictFloat, StrictInt

from ecomsre.dta_v2.v22.admission_dispatch_campaign_v223 import (
    AdmissionDispatchCaseRunV223,
    AdmissionDispatchRunStatusV223,
    StudyCombinationV223,
)
from ecomsre.dta_v2.v22.evidence_utility_audit_v222 import (
    EvidenceUtilityAuditReportV222,
)
from ecomsre.dta_v2.v22.negative_coverage_v222 import ReadUtilityClassV222
from ecomsre.dta_v2.v22.practical_scorer import PracticalTruthV22
from ecomsre.dta_v2.v22.predicates import MechanismV22
from ecomsre.dta_v2.v22.read_contracts import DtaModelV22


def _ratio(numerator: int | float, denominator: int | float) -> float:
    return 0.0 if denominator == 0 else numerator / denominator


class CombinationMetricsV223(DtaModelV22):
    schema_version: Literal["dta-v22.3.combination-metrics.v1"]
    combination: StudyCombinationV223
    total_runs: StrictInt = Field(ge=1)
    incident_denominator: StrictInt = Field(ge=0)
    no_incident_denominator: StrictInt = Field(ge=0)
    abstention_denominator: StrictInt = Field(ge=0)
    resource_silent_denominator: StrictInt = Field(ge=0)
    exact_completion_cases: StrictInt = Field(ge=0)
    exact_completion_rate: StrictFloat = Field(ge=0, le=1)
    root_service_accuracy: StrictFloat = Field(ge=0, le=1)
    mechanism_accuracy: StrictFloat = Field(ge=0, le=1)
    mechanism_macro_f1: StrictFloat = Field(ge=0, le=1)
    valid_terminal_rate: StrictFloat = Field(ge=0, le=1)
    no_incident_accuracy: StrictFloat = Field(ge=0, le=1)
    abstention_accuracy: StrictFloat = Field(ge=0, le=1)
    combined_control_accuracy: StrictFloat = Field(ge=0, le=1)
    resource_silent_accuracy: StrictFloat = Field(ge=0, le=1)
    premature_no_incident_rate: StrictFloat = Field(ge=0, le=1)
    no_incident_first_open_mean_turn: StrictFloat = Field(ge=0)
    no_incident_withheld_count: StrictInt = Field(ge=0)
    closure_required_count: StrictInt = Field(ge=0)
    closure_attempt_count: StrictInt = Field(ge=0)
    closure_outcome_distribution: dict[str, int]
    unnecessary_control_read_rate: StrictFloat = Field(ge=0, le=1)
    model_action_selections: StrictInt = Field(ge=0)
    automatic_top1_dispatches: StrictInt = Field(ge=0)
    oracle_path_action_hit_rate: StrictFloat = Field(ge=0, le=1)
    top4_oracle_path_recall: StrictFloat = Field(ge=0, le=1)
    empty_read_rate: StrictFloat = Field(ge=0, le=1)
    predicate_yield_rate: StrictFloat = Field(ge=0, le=1)
    gap_closure_rate: StrictFloat = Field(ge=0, le=1)
    source_distribution: dict[str, int]
    mean_adaptive_reads: StrictFloat = Field(ge=0)
    read_bearing_runs: StrictInt = Field(ge=0)
    diagnosis_after_read_rate: StrictFloat = Field(ge=0, le=1)
    correct_diagnosis_after_read: StrictInt = Field(ge=0)
    wrong_diagnosis_after_read: StrictInt = Field(ge=0)
    abstain_after_read: StrictInt = Field(ge=0)
    no_incident_after_read: StrictInt = Field(ge=0)
    terminal_candidate_availability_after_read: StrictFloat = Field(ge=0, le=1)
    terminal_selection_accuracy: StrictFloat = Field(ge=0, le=1)
    evidence_ref_validity: StrictFloat = Field(ge=0, le=1)
    semantic_clause_validity: StrictFloat = Field(ge=0, le=1)
    provider_calls: StrictInt = Field(ge=0)
    protocol_repairs: StrictInt = Field(ge=0)
    first_pass_protocol_success: StrictFloat = Field(ge=0, le=1)
    post_repair_protocol_success: StrictFloat = Field(ge=0, le=1)
    protocol_failure_rate: StrictFloat = Field(ge=0, le=1)
    input_tokens: StrictInt = Field(ge=0)
    output_tokens: StrictInt = Field(ge=0)
    total_tokens: StrictInt = Field(ge=0)
    tokens_per_correct_case: StrictFloat = Field(ge=0)
    latency_ms: StrictFloat = Field(ge=0)
    transport_retries: StrictInt = Field(ge=0)
    uncaught_exceptions: StrictInt = Field(ge=0)
    agent_writes: Literal[0]


class DevelopmentGateV223(DtaModelV22):
    schema_version: Literal["dta-v22.3.development-gate.v1"]
    auto_closed_resource_reads_before_no_incident: StrictBool
    premature_no_incident_rate: StrictFloat = Field(ge=0, le=1)
    oracle_path_action_hit_rate: StrictFloat = Field(ge=0, le=1)
    diagnosis_after_read_rate: StrictFloat = Field(ge=0, le=1)
    protocol_failure_rate: StrictFloat = Field(ge=0, le=1)
    control_accuracy: StrictFloat = Field(ge=0, le=1)
    exact_case_gain_over_model_legacy: StrictInt
    mechanism_macro_f1_gain_over_model_legacy: StrictFloat
    uncaught_exceptions: StrictInt = Field(ge=0)
    agent_writes: Literal[0]
    gate_passed: StrictBool


class AdmissionMainEffectV223(DtaModelV22):
    schema_version: Literal["dta-v22.3.admission-main-effect.v1"]
    resource_silent_accuracy_improvement: StrictFloat
    premature_no_incident_decrease: StrictFloat
    control_accuracy_change: StrictFloat
    extra_mean_reads: StrictFloat
    extra_tokens: StrictInt


class DispatchMainEffectV223(DtaModelV22):
    schema_version: Literal["dta-v22.3.dispatch-main-effect.v1"]
    oracle_path_hit_improvement: StrictFloat
    empty_read_rate_decrease: StrictFloat
    diagnosis_after_read_improvement: StrictFloat
    exact_completion_improvement: StrictFloat
    provider_call_change: StrictInt
    token_change: StrictInt
    protocol_failure_rate_change: StrictFloat


class FactorialInterpretationV223(DtaModelV22):
    schema_version: Literal["dta-v22.3.factorial-interpretation.v1"]
    admission_main_effect: AdmissionMainEffectV223
    dispatch_main_effect: DispatchMainEffectV223
    interaction_exact_rate: StrictFloat
    measured_result_terminal: Literal[
        "DTA_V22_3_COMBINED_FIX_QUALITY_EFFECT_OBSERVED",
        "DTA_V22_3_PARTIAL_FIX_EFFECT_OBSERVED",
        "DTA_V22_3_NO_FIX_EFFECT_OBSERVED",
    ]
    combined_quality_threshold_passed: StrictBool
    admission_only_threshold_passed: StrictBool
    dispatch_only_threshold_passed: StrictBool


class AdmissionDispatchScoreBundleV223(DtaModelV22):
    schema_version: Literal["dta-v22.3.admission-dispatch-score-bundle.v1"]
    combinations: tuple[CombinationMetricsV223, ...]
    development_gate: DevelopmentGateV223 | None
    interpretation: FactorialInterpretationV223 | None


def _exact(run: AdmissionDispatchCaseRunV223, truth: PracticalTruthV22) -> bool:
    if run.status is not AdmissionDispatchRunStatusV223.VALID_TERMINAL:
        return False
    if run.terminal != truth.expected_terminal:
        return False
    if truth.expected_terminal != "DIAGNOSED":
        return True
    return (
        run.root_service == truth.expected_root_service
        and run.mechanism == truth.expected_mechanism
        and bool(run.supporting_evidence_refs)
        and run.matched_clause_id is not None
    )


def _macro_f1(
    *, runs: tuple[AdmissionDispatchCaseRunV223, ...], truths: dict[str, PracticalTruthV22]
) -> float:
    labels = tuple(
        item.value
        for item in MechanismV22
        if item not in {MechanismV22.NO_INCIDENT, MechanismV22.UNKNOWN}
    )
    values: list[float] = []
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
        values.append(_ratio(2 * tp, 2 * tp + fp + fn))
    return fmean(values)


def _score_combination(
    *,
    combination: StudyCombinationV223,
    runs: tuple[AdmissionDispatchCaseRunV223, ...],
    truths: dict[str, PracticalTruthV22],
    oracle_actions: dict[str, frozenset[str]],
) -> CombinationMetricsV223:
    events = tuple(event for run in runs for event in run.adaptive_read_events)
    incident = tuple(run for run in runs if truths[run.case_id].expected_terminal == "DIAGNOSED")
    no_incident = tuple(run for run in runs if truths[run.case_id].expected_terminal == "NO_INCIDENT")
    abstention = tuple(run for run in runs if truths[run.case_id].expected_terminal == "ABSTAIN")
    controls = (*no_incident, *abstention)
    resource_silent = tuple(
        run
        for run in incident
        if truths[run.case_id].expected_mechanism
        in {MechanismV22.CPU_SATURATION.value, MechanismV22.MEMORY_LEAK.value}
        and run.legacy_no_incident_exposed_turn_zero
    )
    read_bearing = tuple(run for run in runs if run.adaptive_reads > 0)
    diagnosed_after = tuple(run for run in read_bearing if run.terminal == "DIAGNOSED")
    exact = sum(_exact(run, truths[run.case_id]) for run in runs)
    provider_turns = sum(run.provider_turns for run in runs)
    terminal_selected = tuple(run for run in runs if run.provider_terminal_selections > 0)
    first_action_hits = sum(
        bool(run.adaptive_read_events)
        and run.adaptive_read_events[0].action_id in oracle_actions.get(run.case_id, frozenset())
        for run in incident
    )
    top4_hits = sum(
        bool(set(run.turn_zero_top4_action_ids).intersection(oracle_actions.get(run.case_id, frozenset())))
        for run in incident
    )
    closure_outcomes = Counter(
        run.closure_state.closure_outcome_class.value
        for run in runs
        if run.closure_state.closure_outcome_class is not None
    )
    open_turns = tuple(
        run.no_incident_first_open_turn
        for run in runs
        if run.no_incident_first_open_turn is not None
    )
    return CombinationMetricsV223(
        schema_version="dta-v22.3.combination-metrics.v1",
        combination=combination,
        total_runs=len(runs),
        incident_denominator=len(incident),
        no_incident_denominator=len(no_incident),
        abstention_denominator=len(abstention),
        resource_silent_denominator=len(resource_silent),
        exact_completion_cases=exact,
        exact_completion_rate=_ratio(exact, len(runs)),
        root_service_accuracy=_ratio(
            sum(
                run.status is AdmissionDispatchRunStatusV223.VALID_TERMINAL
                and run.terminal == "DIAGNOSED"
                and run.root_service == truths[run.case_id].expected_root_service
                for run in incident
            ),
            len(incident),
        ),
        mechanism_accuracy=_ratio(
            sum(
                run.status is AdmissionDispatchRunStatusV223.VALID_TERMINAL
                and run.terminal == "DIAGNOSED"
                and run.mechanism == truths[run.case_id].expected_mechanism
                for run in incident
            ),
            len(incident),
        ),
        mechanism_macro_f1=_macro_f1(runs=runs, truths=truths),
        valid_terminal_rate=_ratio(
            sum(run.status is AdmissionDispatchRunStatusV223.VALID_TERMINAL for run in runs),
            len(runs),
        ),
        no_incident_accuracy=_ratio(
            sum(run.terminal == "NO_INCIDENT" for run in no_incident), len(no_incident)
        ),
        abstention_accuracy=_ratio(
            sum(run.terminal == "ABSTAIN" for run in abstention), len(abstention)
        ),
        combined_control_accuracy=_ratio(
            sum(_exact(run, truths[run.case_id]) for run in controls), len(controls)
        ),
        resource_silent_accuracy=_ratio(
            sum(_exact(run, truths[run.case_id]) for run in resource_silent),
            len(resource_silent),
        ),
        premature_no_incident_rate=_ratio(
            sum(run.terminal == "NO_INCIDENT" for run in resource_silent),
            len(resource_silent),
        ),
        no_incident_first_open_mean_turn=(
            0.0 if not open_turns else fmean(open_turns)
        ),
        no_incident_withheld_count=sum(run.no_incident_withheld_count for run in runs),
        closure_required_count=sum(run.closure_required_count for run in runs),
        closure_attempt_count=sum(run.closure_state.closure_attempted for run in runs),
        closure_outcome_distribution=dict(sorted(closure_outcomes.items())),
        unnecessary_control_read_rate=_ratio(
            sum(run.adaptive_reads > 0 for run in no_incident), len(no_incident)
        ),
        model_action_selections=sum(run.model_action_selections for run in runs),
        automatic_top1_dispatches=sum(run.automatic_top1_dispatches for run in runs),
        oracle_path_action_hit_rate=_ratio(first_action_hits, len(incident)),
        top4_oracle_path_recall=_ratio(top4_hits, len(incident)),
        empty_read_rate=_ratio(
            sum(event.outcome_class is ReadUtilityClassV222.EMPTY_CAPTURED for event in events),
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
        source_distribution=dict(sorted(Counter(event.source for event in events).items())),
        mean_adaptive_reads=fmean(run.adaptive_reads for run in runs),
        read_bearing_runs=len(read_bearing),
        diagnosis_after_read_rate=_ratio(len(diagnosed_after), len(read_bearing)),
        correct_diagnosis_after_read=sum(
            _exact(run, truths[run.case_id]) for run in diagnosed_after
        ),
        wrong_diagnosis_after_read=sum(
            not _exact(run, truths[run.case_id]) for run in diagnosed_after
        ),
        abstain_after_read=sum(run.terminal == "ABSTAIN" for run in read_bearing),
        no_incident_after_read=sum(run.terminal == "NO_INCIDENT" for run in read_bearing),
        terminal_candidate_availability_after_read=_ratio(
            sum(run.terminal_candidate_available_after_read for run in read_bearing),
            len(read_bearing),
        ),
        terminal_selection_accuracy=_ratio(
            sum(_exact(run, truths[run.case_id]) for run in terminal_selected),
            len(terminal_selected),
        ),
        evidence_ref_validity=_ratio(
            sum(
                run.terminal == "DIAGNOSED" and bool(run.supporting_evidence_refs)
                for run in incident
            ),
            len(incident),
        ),
        semantic_clause_validity=_ratio(
            sum(run.terminal == "DIAGNOSED" and run.matched_clause_id is not None for run in incident),
            len(incident),
        ),
        provider_calls=sum(run.provider_calls for run in runs),
        protocol_repairs=sum(run.protocol_repairs for run in runs),
        first_pass_protocol_success=_ratio(
            sum(run.first_pass_protocol_successes for run in runs), provider_turns
        ),
        post_repair_protocol_success=_ratio(
            sum(run.post_repair_protocol_successes for run in runs), provider_turns
        ),
        protocol_failure_rate=_ratio(
            sum(run.status is AdmissionDispatchRunStatusV223.PROTOCOL_FAILED for run in runs),
            len(runs),
        ),
        input_tokens=sum(run.input_tokens for run in runs),
        output_tokens=sum(run.output_tokens for run in runs),
        total_tokens=sum(run.total_tokens for run in runs),
        tokens_per_correct_case=_ratio(
            sum(run.total_tokens for run in runs),
            exact,
        ),
        latency_ms=sum(run.latency_ms for run in runs),
        transport_retries=sum(run.transport_retry_count for run in runs),
        uncaught_exceptions=sum(run.uncaught_exceptions for run in runs),
        agent_writes=0,
    )


def _pooled(metrics: tuple[CombinationMetricsV223, ...], names: set[StudyCombinationV223], field: str) -> float:
    return fmean(getattr(item, field) for item in metrics if item.combination in names)


def score_admission_dispatch_study_v223(
    *,
    runs: tuple[AdmissionDispatchCaseRunV223, ...],
    truths: tuple[PracticalTruthV22, ...],
    utility_audit: EvidenceUtilityAuditReportV222,
    include_development_gate: bool,
    include_interpretation: bool,
) -> AdmissionDispatchScoreBundleV223:
    truth_by_id = {item.case_id: item for item in truths}
    if {(run.case_id, run.combination) for run in runs} != {
        (truth.case_id, combination)
        for truth in truths
        for combination in StudyCombinationV223
    }:
        raise ValueError("v2.2.3 scorer factorial grid differs")
    oracle_actions = {
        item.case_id: frozenset(
            action.action_id
            for action in item.actions
            if action.support_clause_became_admissible
        )
        for item in utility_audit.cases
    }
    metrics = tuple(
        _score_combination(
            combination=combination,
            runs=tuple(run for run in runs if run.combination is combination),
            truths=truth_by_id,
            oracle_actions=oracle_actions,
        )
        for combination in StudyCombinationV223
    )
    by_name = {item.combination: item for item in metrics}
    auto = by_name[StudyCombinationV223.AUTO_CLOSED]
    legacy = by_name[StudyCombinationV223.MODEL_LEGACY]
    development: DevelopmentGateV223 | None = None
    if include_development_gate:
        resource_runs = tuple(
            run
            for run in runs
            if run.combination is StudyCombinationV223.AUTO_CLOSED
            and truth_by_id[run.case_id].expected_mechanism
            in {MechanismV22.CPU_SATURATION.value, MechanismV22.MEMORY_LEAK.value}
            and run.legacy_no_incident_exposed_turn_zero
        )
        reads_before = all(
            any(event.source == "RESOURCES" for event in run.adaptive_read_events)
            for run in resource_runs
        )
        exact_gain = auto.exact_completion_cases - legacy.exact_completion_cases
        macro_gain = auto.mechanism_macro_f1 - legacy.mechanism_macro_f1
        gate_passed = (
            reads_before
            and auto.premature_no_incident_rate <= 0.25
            and auto.oracle_path_action_hit_rate >= 0.60
            and auto.diagnosis_after_read_rate >= 0.25
            and auto.protocol_failure_rate <= 0.10
            and auto.combined_control_accuracy >= 0.80
            and auto.uncaught_exceptions == 0
            and auto.agent_writes == 0
            and (exact_gain >= 2 or macro_gain >= 0.15)
        )
        development = DevelopmentGateV223(
            schema_version="dta-v22.3.development-gate.v1",
            auto_closed_resource_reads_before_no_incident=reads_before,
            premature_no_incident_rate=auto.premature_no_incident_rate,
            oracle_path_action_hit_rate=auto.oracle_path_action_hit_rate,
            diagnosis_after_read_rate=auto.diagnosis_after_read_rate,
            protocol_failure_rate=auto.protocol_failure_rate,
            control_accuracy=auto.combined_control_accuracy,
            exact_case_gain_over_model_legacy=exact_gain,
            mechanism_macro_f1_gain_over_model_legacy=macro_gain,
            uncaught_exceptions=auto.uncaught_exceptions,
            agent_writes=0,
            gate_passed=gate_passed,
        )
    interpretation: FactorialInterpretationV223 | None = None
    if include_interpretation:
        closed = {StudyCombinationV223.MODEL_CLOSED, StudyCombinationV223.AUTO_CLOSED}
        legacy_names = {StudyCombinationV223.MODEL_LEGACY, StudyCombinationV223.AUTO_LEGACY}
        auto_names = {StudyCombinationV223.AUTO_LEGACY, StudyCombinationV223.AUTO_CLOSED}
        model_names = {StudyCombinationV223.MODEL_LEGACY, StudyCombinationV223.MODEL_CLOSED}
        admission = AdmissionMainEffectV223(
            schema_version="dta-v22.3.admission-main-effect.v1",
            resource_silent_accuracy_improvement=(
                _pooled(metrics, closed, "resource_silent_accuracy")
                - _pooled(metrics, legacy_names, "resource_silent_accuracy")
            ),
            premature_no_incident_decrease=(
                _pooled(metrics, legacy_names, "premature_no_incident_rate")
                - _pooled(metrics, closed, "premature_no_incident_rate")
            ),
            control_accuracy_change=(
                _pooled(metrics, closed, "combined_control_accuracy")
                - _pooled(metrics, legacy_names, "combined_control_accuracy")
            ),
            extra_mean_reads=(
                _pooled(metrics, closed, "mean_adaptive_reads")
                - _pooled(metrics, legacy_names, "mean_adaptive_reads")
            ),
            extra_tokens=sum(item.total_tokens for item in metrics if item.combination in closed)
            - sum(item.total_tokens for item in metrics if item.combination in legacy_names),
        )
        dispatch = DispatchMainEffectV223(
            schema_version="dta-v22.3.dispatch-main-effect.v1",
            oracle_path_hit_improvement=(
                _pooled(metrics, auto_names, "oracle_path_action_hit_rate")
                - _pooled(metrics, model_names, "oracle_path_action_hit_rate")
            ),
            empty_read_rate_decrease=(
                _pooled(metrics, model_names, "empty_read_rate")
                - _pooled(metrics, auto_names, "empty_read_rate")
            ),
            diagnosis_after_read_improvement=(
                _pooled(metrics, auto_names, "diagnosis_after_read_rate")
                - _pooled(metrics, model_names, "diagnosis_after_read_rate")
            ),
            exact_completion_improvement=(
                _pooled(metrics, auto_names, "exact_completion_rate")
                - _pooled(metrics, model_names, "exact_completion_rate")
            ),
            provider_call_change=sum(item.provider_calls for item in metrics if item.combination in auto_names)
            - sum(item.provider_calls for item in metrics if item.combination in model_names),
            token_change=sum(item.total_tokens for item in metrics if item.combination in auto_names)
            - sum(item.total_tokens for item in metrics if item.combination in model_names),
            protocol_failure_rate_change=(
                _pooled(metrics, auto_names, "protocol_failure_rate")
                - _pooled(metrics, model_names, "protocol_failure_rate")
            ),
        )
        combined = (
            (auto.exact_completion_cases >= legacy.exact_completion_cases + 3
             or auto.mechanism_macro_f1 >= legacy.mechanism_macro_f1 + 0.20)
            and auto.resource_silent_accuracy >= 0.50
            and auto.premature_no_incident_rate <= 0.25
            and auto.diagnosis_after_read_rate >= legacy.diagnosis_after_read_rate + 0.15
            and auto.combined_control_accuracy >= legacy.combined_control_accuracy - (1 / 6)
            and auto.agent_writes == 0
        )
        admission_only = (
            admission.resource_silent_accuracy_improvement >= 0.25
            and admission.premature_no_incident_decrease >= 0.50
            and admission.control_accuracy_change >= -0.167
        )
        dispatch_only = (
            dispatch.oracle_path_hit_improvement >= 0.25
            and dispatch.empty_read_rate_decrease >= 0.15
            and dispatch.diagnosis_after_read_improvement >= 0.10
            and dispatch.protocol_failure_rate_change <= 0.05
        )
        terminal: Literal[
            "DTA_V22_3_COMBINED_FIX_QUALITY_EFFECT_OBSERVED",
            "DTA_V22_3_PARTIAL_FIX_EFFECT_OBSERVED",
            "DTA_V22_3_NO_FIX_EFFECT_OBSERVED",
        ] = (
            "DTA_V22_3_COMBINED_FIX_QUALITY_EFFECT_OBSERVED"
            if combined
            else "DTA_V22_3_PARTIAL_FIX_EFFECT_OBSERVED"
            if admission_only or dispatch_only
            else "DTA_V22_3_NO_FIX_EFFECT_OBSERVED"
        )
        admission_exact = (
            by_name[StudyCombinationV223.MODEL_CLOSED].exact_completion_rate
            - legacy.exact_completion_rate
        )
        dispatch_exact = (
            by_name[StudyCombinationV223.AUTO_LEGACY].exact_completion_rate
            - legacy.exact_completion_rate
        )
        interaction = (
            auto.exact_completion_rate
            - legacy.exact_completion_rate
            - admission_exact
            - dispatch_exact
        )
        interpretation = FactorialInterpretationV223(
            schema_version="dta-v22.3.factorial-interpretation.v1",
            admission_main_effect=admission,
            dispatch_main_effect=dispatch,
            interaction_exact_rate=interaction,
            measured_result_terminal=terminal,
            combined_quality_threshold_passed=combined,
            admission_only_threshold_passed=admission_only,
            dispatch_only_threshold_passed=dispatch_only,
        )
    return AdmissionDispatchScoreBundleV223(
        schema_version="dta-v22.3.admission-dispatch-score-bundle.v1",
        combinations=metrics,
        development_gate=development,
        interpretation=interpretation,
    )


__all__ = (
    "AdmissionDispatchScoreBundleV223",
    "CombinationMetricsV223",
    "DevelopmentGateV223",
    "FactorialInterpretationV223",
    "score_admission_dispatch_study_v223",
)
