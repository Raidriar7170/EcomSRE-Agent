"""Four-combination process and quality scoring for DTA v2.2.1."""

from __future__ import annotations

from statistics import fmean
from typing import Literal, cast

from pydantic import Field, StrictBool, StrictFloat, StrictInt

from ecomsre.dta_v2.v22.controller_contracts import ControllerDecisionKindV22
from ecomsre.dta_v2.v22.controller_inputs import ControllerArmV22
from ecomsre.dta_v2.v22.evidence_acquisition_v221 import StudyCombinationV221
from ecomsre.dta_v2.v22.practical_runner import (
    PracticalCaseRunV22,
    PracticalCaseRunV221,
    PracticalRunStatusV22,
)
from ecomsre.dta_v2.v22.practical_scorer import (
    PracticalScoreReportV22,
    PracticalTruthV22,
    ScoredOutcomeV22,
    score_practical_runs_v22,
)
from ecomsre.dta_v2.v22.read_contracts import (
    DtaModelV22,
    ReadSourceStatusV22,
)


class EvidenceAcquisitionProcessMetricsV221(DtaModelV22):
    schema_version: Literal["dta-v22.1.evidence-acquisition-process-metrics.v1"]
    bootstrap_insufficient_cases: StrictInt = Field(ge=0)
    cases_with_at_least_one_adaptive_read: StrictInt = Field(ge=0)
    cases_with_at_least_two_adaptive_reads: StrictInt = Field(ge=0)
    premature_abstain_proposals: StrictInt = Field(ge=0)
    premature_abstain_redirects: StrictInt = Field(ge=0)
    redirect_to_read_conversions: StrictInt = Field(ge=0)
    redirect_to_valid_terminal_conversions: StrictInt = Field(ge=0)


class EvidenceAcquisitionScoreReportV221(DtaModelV22):
    schema_version: Literal["dta-v22.1.evidence-acquisition-score-report.v1"]
    combination: StudyCombinationV221
    total_runs: StrictInt = Field(ge=1)
    base_score: PracticalScoreReportV22
    end_to_end_exact_completion_count: StrictInt = Field(ge=0)
    end_to_end_exact_completion_rate: StrictFloat = Field(ge=0, le=1)
    valid_terminal_rate: StrictFloat = Field(ge=0, le=1)
    logical_decision_attempts: StrictInt = Field(ge=1)
    first_pass_protocol_success: StrictFloat = Field(ge=0, le=1)
    post_repair_protocol_success: StrictFloat = Field(ge=0, le=1)
    semantic_repair_rate: StrictFloat = Field(ge=0, le=1)
    policy_redirect_rate: StrictFloat = Field(ge=0, le=1)
    policy_redirect_compliance_rate: StrictFloat = Field(ge=0, le=1)
    repeated_premature_abstention_rate: StrictFloat = Field(ge=0, le=1)
    root_service_accuracy: StrictFloat = Field(ge=0, le=1)
    mechanism_accuracy: StrictFloat = Field(ge=0, le=1)
    mechanism_macro_f1: StrictFloat = Field(ge=0, le=1)
    no_incident_accuracy: StrictFloat = Field(ge=0, le=1)
    abstention_accuracy: StrictFloat = Field(ge=0, le=1)
    evidence_ref_validity: StrictFloat = Field(ge=0, le=1)
    semantic_evidence_clause_validity: StrictFloat = Field(ge=0, le=1)
    adaptive_read_rate: StrictFloat = Field(ge=0, le=1)
    mean_adaptive_reads: StrictFloat = Field(ge=0)
    read_source_distribution: dict[str, StrictInt]
    successful_read_rate: StrictFloat = Field(ge=0, le=1)
    diagnosis_after_read_rate: StrictFloat = Field(ge=0, le=1)
    duplicate_read_attempts: StrictInt = Field(ge=0)
    mean_provider_calls: StrictFloat = Field(ge=0)
    input_tokens: StrictInt = Field(ge=0)
    output_tokens: StrictInt = Field(ge=0)
    total_tokens: StrictInt = Field(ge=0)
    total_latency_ms: StrictFloat = Field(ge=0)
    mean_latency_ms: StrictFloat = Field(ge=0)
    transport_retry_count: StrictInt = Field(ge=0)
    uncaught_exceptions: StrictInt = Field(ge=0)
    agent_writes: StrictInt = Field(ge=0, le=0)
    process: EvidenceAcquisitionProcessMetricsV221


class ControlCostMetricsV221(DtaModelV22):
    schema_version: Literal["dta-v22.1.control-cost-metrics.v1"]
    arm: ControllerArmV22
    control_cases: StrictInt = Field(ge=1)
    legacy_unnecessary_read_rate: StrictFloat = Field(ge=0, le=1)
    unnecessary_read_rate: StrictFloat = Field(ge=0, le=1)
    unnecessary_read_rate_increase: StrictFloat = Field(ge=-1, le=1)
    no_incident_regression: StrictFloat = Field(ge=0, le=1)
    abstention_regression: StrictFloat = Field(ge=0, le=1)
    combined_control_accuracy_drop: StrictFloat = Field(ge=0, le=1)
    extra_provider_calls: StrictInt
    extra_tokens: StrictInt


class StudyInterpretationV221(DtaModelV22):
    schema_version: Literal["dta-v22.1.study-interpretation.v1"]
    policy_terminal: Literal[
        "DTA_V22_1_EVIDENCE_ACQUISITION_EFFECT_OBSERVED",
        "DTA_V22_1_NO_EVIDENCE_ACQUISITION_EFFECT_OBSERVED",
    ]
    evidence_acquisition_effect_observed: StrictBool
    planner_quality_improvement_observed: StrictBool
    planner_specific_interaction_observed: StrictBool
    quality_statement: str
    planner_interaction_statement: str


def _ratio(numerator: int, denominator: int) -> float:
    return 0.0 if denominator == 0 else numerator / denominator


def score_evidence_acquisition_runs_v221(
    *,
    combination: StudyCombinationV221,
    runs: tuple[PracticalCaseRunV221, ...],
    truths: tuple[PracticalTruthV22, ...],
    bootstrap_insufficient_case_ids: tuple[str, ...],
) -> EvidenceAcquisitionScoreReportV221:
    if not runs or any(
        run.arm is not combination.arm
        or run.terminal_exploration_policy is not combination.policy
        for run in runs
    ):
        raise ValueError("v2.2.1 score combination differs from case runs")
    if len(set(bootstrap_insufficient_case_ids)) != len(
        bootstrap_insufficient_case_ids
    ) or not set(bootstrap_insufficient_case_ids).issubset(
        {item.case_id for item in runs}
    ):
        raise ValueError("bootstrap-insufficient scorer labels differ from runs")
    base = score_practical_runs_v22(
        runs=cast(tuple[PracticalCaseRunV22, ...], runs),
        truths=truths,
    )
    exact_count = sum(
        item.outcome is ScoredOutcomeV22.COMPLETED_CORRECT
        for item in base.scored_runs
    )
    logical_attempts = sum(item.logical_decision_attempts for item in runs)
    redirects = sum(item.policy_redirects for item in runs)
    repeated = sum(item.repeated_premature_abstentions for item in runs)
    redirect_to_read = sum(
        item.policy_redirects == 1
        and item.redirect_response_kind is ControllerDecisionKindV22.READ
        for item in runs
    )
    redirect_to_valid_terminal = sum(
        item.policy_redirects == 1
        and item.adaptive_reads == 0
        and item.status is PracticalRunStatusV22.VALID_TERMINAL
        for item in runs
    )
    read_runs = tuple(item for item in runs if item.adaptive_reads > 0)
    read_events = tuple(event for item in runs for event in item.adaptive_read_events)
    source_distribution: dict[str, int] = {}
    for event in read_events:
        source_distribution[event.source.value] = (
            source_distribution.get(event.source.value, 0) + 1
        )
    successful_statuses = {
        ReadSourceStatusV22.SUCCESS_NONEMPTY,
        ReadSourceStatusV22.SUCCESS_EMPTY,
    }
    bootstrap_runs = tuple(
        item for item in runs if item.case_id in set(bootstrap_insufficient_case_ids)
    )
    process = EvidenceAcquisitionProcessMetricsV221(
        schema_version="dta-v22.1.evidence-acquisition-process-metrics.v1",
        bootstrap_insufficient_cases=len(bootstrap_runs),
        cases_with_at_least_one_adaptive_read=sum(
            item.adaptive_reads >= 1 for item in bootstrap_runs
        ),
        cases_with_at_least_two_adaptive_reads=sum(
            item.adaptive_reads >= 2 for item in bootstrap_runs
        ),
        premature_abstain_proposals=sum(
            item.premature_abstention_proposals for item in bootstrap_runs
        ),
        premature_abstain_redirects=sum(
            item.policy_redirects for item in bootstrap_runs
        ),
        redirect_to_read_conversions=sum(
            item.policy_redirects == 1
            and item.redirect_response_kind is ControllerDecisionKindV22.READ
            for item in bootstrap_runs
        ),
        redirect_to_valid_terminal_conversions=sum(
            item.policy_redirects == 1
            and item.adaptive_reads == 0
            and item.status is PracticalRunStatusV22.VALID_TERMINAL
            for item in bootstrap_runs
        ),
    )
    return EvidenceAcquisitionScoreReportV221(
        schema_version="dta-v22.1.evidence-acquisition-score-report.v1",
        combination=combination,
        total_runs=len(runs),
        base_score=base,
        end_to_end_exact_completion_count=exact_count,
        end_to_end_exact_completion_rate=_ratio(exact_count, len(runs)),
        valid_terminal_rate=_ratio(
            sum(item.status is PracticalRunStatusV22.VALID_TERMINAL for item in runs),
            len(runs),
        ),
        logical_decision_attempts=logical_attempts,
        first_pass_protocol_success=_ratio(
            sum(item.first_pass_protocol_successes for item in runs),
            logical_attempts,
        ),
        post_repair_protocol_success=_ratio(
            sum(item.post_repair_protocol_successes for item in runs),
            logical_attempts,
        ),
        semantic_repair_rate=_ratio(
            sum(item.semantic_repairs for item in runs), logical_attempts
        ),
        policy_redirect_rate=_ratio(redirects, len(runs)),
        policy_redirect_compliance_rate=_ratio(
            redirect_to_read + redirect_to_valid_terminal, redirects
        ),
        repeated_premature_abstention_rate=_ratio(repeated, redirects),
        root_service_accuracy=base.root_service_accuracy,
        mechanism_accuracy=base.mechanism_accuracy,
        mechanism_macro_f1=base.mechanism_macro_f1,
        no_incident_accuracy=base.no_incident_accuracy,
        abstention_accuracy=base.abstention_accuracy,
        evidence_ref_validity=base.evidence_ref_validity,
        semantic_evidence_clause_validity=base.semantic_evidence_clause_validity,
        adaptive_read_rate=_ratio(len(read_runs), len(runs)),
        mean_adaptive_reads=fmean(item.adaptive_reads for item in runs),
        read_source_distribution=dict(sorted(source_distribution.items())),
        successful_read_rate=_ratio(
            sum(item.status in successful_statuses for item in read_events),
            len(read_events),
        ),
        diagnosis_after_read_rate=_ratio(
            sum(
                item.status is PracticalRunStatusV22.VALID_TERMINAL
                and item.terminal == "DIAGNOSED"
                for item in read_runs
            ),
            len(read_runs),
        ),
        duplicate_read_attempts=sum(item.duplicate_read_attempts for item in runs),
        mean_provider_calls=fmean(item.provider_calls for item in runs),
        input_tokens=sum(item.input_tokens for item in runs),
        output_tokens=sum(item.output_tokens for item in runs),
        total_tokens=sum(item.total_tokens for item in runs),
        total_latency_ms=sum(item.latency_ms for item in runs),
        mean_latency_ms=fmean(item.latency_ms for item in runs),
        transport_retry_count=sum(item.transport_retry_count for item in runs),
        uncaught_exceptions=sum(item.uncaught_exceptions for item in runs),
        agent_writes=sum(item.agent_writes for item in runs),
        process=process,
    )


def _terminal_accuracy(
    *,
    runs: tuple[PracticalCaseRunV221, ...],
    truth_by_id: dict[str, PracticalTruthV22],
    expected_terminal: str,
) -> float:
    selected = tuple(
        item
        for item in runs
        if truth_by_id[item.case_id].expected_terminal == expected_terminal
    )
    return _ratio(
        sum(
            item.status is PracticalRunStatusV22.VALID_TERMINAL
            and item.terminal == expected_terminal
            for item in selected
        ),
        len(selected),
    )


def compute_control_cost_metrics_v221(
    *,
    arm: ControllerArmV22,
    legacy_runs: tuple[PracticalCaseRunV221, ...],
    gate_runs: tuple[PracticalCaseRunV221, ...],
    truths: tuple[PracticalTruthV22, ...],
) -> ControlCostMetricsV221:
    truth_by_id = {item.case_id: item for item in truths}
    control_ids = {
        item.case_id
        for item in truths
        if item.expected_terminal in {"NO_INCIDENT", "ABSTAIN"}
    }
    legacy_controls = tuple(item for item in legacy_runs if item.case_id in control_ids)
    gate_controls = tuple(item for item in gate_runs if item.case_id in control_ids)
    if (
        not control_ids
        or {item.case_id for item in legacy_controls} != control_ids
        or {item.case_id for item in gate_controls} != control_ids
        or any(item.arm is not arm for item in (*legacy_controls, *gate_controls))
    ):
        raise ValueError("control-cost run bindings differ")
    legacy_read_rate = _ratio(
        sum(item.adaptive_reads > 0 for item in legacy_controls), len(control_ids)
    )
    gate_read_rate = _ratio(
        sum(item.adaptive_reads > 0 for item in gate_controls), len(control_ids)
    )
    legacy_no_incident = _terminal_accuracy(
        runs=legacy_controls,
        truth_by_id=truth_by_id,
        expected_terminal="NO_INCIDENT",
    )
    gate_no_incident = _terminal_accuracy(
        runs=gate_controls,
        truth_by_id=truth_by_id,
        expected_terminal="NO_INCIDENT",
    )
    legacy_abstention = _terminal_accuracy(
        runs=legacy_controls,
        truth_by_id=truth_by_id,
        expected_terminal="ABSTAIN",
    )
    gate_abstention = _terminal_accuracy(
        runs=gate_controls,
        truth_by_id=truth_by_id,
        expected_terminal="ABSTAIN",
    )
    legacy_correct = sum(
        item.status is PracticalRunStatusV22.VALID_TERMINAL
        and item.terminal == truth_by_id[item.case_id].expected_terminal
        for item in legacy_controls
    )
    gate_correct = sum(
        item.status is PracticalRunStatusV22.VALID_TERMINAL
        and item.terminal == truth_by_id[item.case_id].expected_terminal
        for item in gate_controls
    )
    return ControlCostMetricsV221(
        schema_version="dta-v22.1.control-cost-metrics.v1",
        arm=arm,
        control_cases=len(control_ids),
        legacy_unnecessary_read_rate=legacy_read_rate,
        unnecessary_read_rate=gate_read_rate,
        unnecessary_read_rate_increase=gate_read_rate - legacy_read_rate,
        no_incident_regression=max(0.0, legacy_no_incident - gate_no_incident),
        abstention_regression=max(0.0, legacy_abstention - gate_abstention),
        combined_control_accuracy_drop=max(
            0.0,
            _ratio(legacy_correct, len(control_ids))
            - _ratio(gate_correct, len(control_ids)),
        ),
        extra_provider_calls=sum(item.provider_calls for item in gate_controls)
        - sum(item.provider_calls for item in legacy_controls),
        extra_tokens=sum(item.total_tokens for item in gate_controls)
        - sum(item.total_tokens for item in legacy_controls),
    )


def summarize_study_interpretation_v221(
    *,
    scores: tuple[EvidenceAcquisitionScoreReportV221, ...],
    control_costs: tuple[ControlCostMetricsV221, ...],
) -> StudyInterpretationV221:
    by_combination = {item.combination: item for item in scores}
    if set(by_combination) != set(StudyCombinationV221):
        raise ValueError("study interpretation requires all four combinations")
    controls_by_arm = {item.arm: item for item in control_costs}
    if set(controls_by_arm) != set(ControllerArmV22):
        raise ValueError("study interpretation requires both control-cost arms")

    def bootstrap_read_rate(score: EvidenceAcquisitionScoreReportV221) -> float:
        return _ratio(
            score.process.cases_with_at_least_one_adaptive_read,
            score.process.bootstrap_insufficient_cases,
        )

    flat_legacy = by_combination[StudyCombinationV221.FLAT_LEGACY]
    flat_gate = by_combination[StudyCombinationV221.FLAT_GATE]
    planner_legacy = by_combination[StudyCombinationV221.PLANNER_LEGACY]
    planner_gate = by_combination[StudyCombinationV221.PLANNER_GATE]
    acquisition_effect = all(
        all(
            (
                bootstrap_read_rate(gate) >= 0.50,
                bootstrap_read_rate(gate) - bootstrap_read_rate(legacy) >= 0.30,
                gate.policy_redirect_compliance_rate >= 0.75,
                gate.repeated_premature_abstention_rate <= 0.25,
                gate.agent_writes == 0,
            )
        )
        for gate, legacy in (
            (flat_gate, flat_legacy),
            (planner_gate, planner_legacy),
        )
    )
    planner_control = controls_by_arm[ControllerArmV22.PLANNER_LITE]
    quality_gain = (
        planner_gate.end_to_end_exact_completion_count
        - planner_legacy.end_to_end_exact_completion_count
        >= 1
        or planner_gate.mechanism_macro_f1 - planner_legacy.mechanism_macro_f1
        >= 0.10
    ) and planner_control.combined_control_accuracy_drop <= 0.25
    planner_interaction = (
        planner_gate.diagnosis_after_read_rate > flat_gate.diagnosis_after_read_rate
        and planner_gate.mechanism_macro_f1 > flat_gate.mechanism_macro_f1
    )
    return StudyInterpretationV221(
        schema_version="dta-v22.1.study-interpretation.v1",
        policy_terminal=(
            "DTA_V22_1_EVIDENCE_ACQUISITION_EFFECT_OBSERVED"
            if acquisition_effect
            else "DTA_V22_1_NO_EVIDENCE_ACQUISITION_EFFECT_OBSERVED"
        ),
        evidence_acquisition_effect_observed=acquisition_effect,
        planner_quality_improvement_observed=quality_gain,
        planner_specific_interaction_observed=planner_interaction,
        quality_statement=(
            "Planner-Lite Gate met the preregistered narrow quality-improvement rule."
            if quality_gain
            else (
                "The gate changed exploration behavior without demonstrating better "
                "RCA quality."
            )
        ),
        planner_interaction_statement=(
            "The preregistered Planner-specific interaction was established."
            if planner_interaction
            else "No Planner-specific interaction was established."
        ),
    )


__all__ = (
    "ControlCostMetricsV221",
    "EvidenceAcquisitionProcessMetricsV221",
    "EvidenceAcquisitionScoreReportV221",
    "StudyInterpretationV221",
    "compute_control_cost_metrics_v221",
    "score_evidence_acquisition_runs_v221",
    "summarize_study_interpretation_v221",
)
