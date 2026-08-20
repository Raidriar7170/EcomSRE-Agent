"""Applicability-aware scoring for the practical v2.2 replay comparison."""

from __future__ import annotations

from enum import Enum
from statistics import fmean
from typing import Literal

from pydantic import Field, StrictBool, StrictFloat, StrictInt, model_validator

from ecomsre.dta_v2.v22.controller_inputs import ControllerArmV22
from ecomsre.dta_v2.v22.practical_runner import (
    PracticalCaseRunV22,
    PracticalRunStatusV22,
)
from ecomsre.dta_v2.v22.read_contracts import DtaModelV22


class ScoredOutcomeV22(str, Enum):
    COMPLETED_CORRECT = "COMPLETED_CORRECT"
    SEMANTICALLY_WRONG = "SEMANTICALLY_WRONG"
    PROTOCOL_FAILED = "PROTOCOL_FAILED"
    TRANSPORT_FAILED = "TRANSPORT_FAILED"
    RUNNER_EXCEPTION = "RUNNER_EXCEPTION"


class PracticalTruthV22(DtaModelV22):
    case_id: str
    expected_terminal: Literal["DIAGNOSED", "NO_INCIDENT", "ABSTAIN"]
    expected_root_service: str | None
    expected_mechanism: str | None
    evidence_applicable: StrictBool

    @model_validator(mode="after")
    def require_applicability(self) -> "PracticalTruthV22":
        incident = self.expected_terminal == "DIAGNOSED"
        if incident != (
            self.expected_root_service is not None
            and self.expected_mechanism is not None
            and self.evidence_applicable
        ):
            raise ValueError("practical truth applicability differs from terminal")
        return self


class ScoredPracticalRunV22(DtaModelV22):
    case_id: str
    arm: ControllerArmV22
    outcome: ScoredOutcomeV22
    terminal_correct: StrictBool
    root_correct: StrictBool | None
    mechanism_correct: StrictBool | None
    evidence_ref_valid: StrictBool | None
    semantic_clause_valid: StrictBool | None


class PracticalScoreReportV22(DtaModelV22):
    schema_version: str = Field(pattern=r"^dta-v22\.practical-score-report\.v1$")
    arm: ControllerArmV22
    scored_runs: tuple[ScoredPracticalRunV22, ...]
    total_runs: StrictInt = Field(ge=1)
    incident_denominator: StrictInt = Field(ge=0)
    no_incident_denominator: StrictInt = Field(ge=0)
    abstention_denominator: StrictInt = Field(ge=0)
    evidence_denominator: StrictInt = Field(ge=0)
    run_completion_rate: StrictFloat = Field(ge=0, le=1)
    first_pass_protocol_success: StrictFloat = Field(ge=0, le=1)
    post_repair_protocol_success: StrictFloat = Field(ge=0, le=1)
    repair_rate: StrictFloat = Field(ge=0, le=1)
    root_service_accuracy: StrictFloat = Field(ge=0, le=1)
    mechanism_accuracy: StrictFloat = Field(ge=0, le=1)
    mechanism_macro_f1: StrictFloat = Field(ge=0, le=1)
    no_incident_accuracy: StrictFloat = Field(ge=0, le=1)
    abstention_accuracy: StrictFloat = Field(ge=0, le=1)
    evidence_ref_validity: StrictFloat = Field(ge=0, le=1)
    semantic_evidence_clause_validity: StrictFloat = Field(ge=0, le=1)
    mean_adaptive_reads: StrictFloat = Field(ge=0)
    duplicate_read_attempts: StrictInt = Field(ge=0)
    mean_provider_turns: StrictFloat = Field(ge=0)
    input_tokens: StrictInt = Field(ge=0)
    output_tokens: StrictInt = Field(ge=0)
    total_tokens: StrictInt = Field(ge=0)
    mean_latency_ms: StrictFloat = Field(ge=0)
    transport_retry_count: StrictInt = Field(ge=0)
    uncaught_exceptions: StrictInt = Field(ge=0)
    agent_writes: StrictInt = Field(ge=0, le=0)


def _ratio(numerator: int, denominator: int) -> float:
    return 0.0 if denominator == 0 else numerator / denominator


def _outcome(
    *,
    run: PracticalCaseRunV22,
    terminal_correct: bool,
    root_correct: bool | None,
    mechanism_correct: bool | None,
) -> ScoredOutcomeV22:
    if run.status is PracticalRunStatusV22.TRANSPORT_FAILED:
        return ScoredOutcomeV22.TRANSPORT_FAILED
    if run.status is PracticalRunStatusV22.PROTOCOL_FAILED:
        return ScoredOutcomeV22.PROTOCOL_FAILED
    if run.status is PracticalRunStatusV22.RUNNER_EXCEPTION:
        return ScoredOutcomeV22.RUNNER_EXCEPTION
    exact = terminal_correct and root_correct is not False and mechanism_correct is not False
    if exact and run.evidence_ref_valid and run.semantic_clause_valid:
        return ScoredOutcomeV22.COMPLETED_CORRECT
    return ScoredOutcomeV22.SEMANTICALLY_WRONG


def _macro_f1(
    *,
    runs: tuple[PracticalCaseRunV22, ...],
    truths: tuple[PracticalTruthV22, ...],
) -> float:
    truth_by_id = {item.case_id: item for item in truths}
    labels = tuple(
        sorted(
            {
                item.expected_mechanism
                for item in truths
                if item.expected_terminal == "DIAGNOSED"
                and item.expected_mechanism is not None
            }
        )
    )
    if not labels:
        return 0.0
    scores: list[float] = []
    for label in labels:
        true_positive = false_positive = false_negative = 0
        for run in runs:
            truth = truth_by_id[run.case_id]
            expected = truth.expected_mechanism == label
            predicted = (
                run.status is PracticalRunStatusV22.VALID_TERMINAL
                and run.terminal == "DIAGNOSED"
                and run.mechanism == label
            )
            true_positive += int(expected and predicted)
            false_positive += int(not expected and predicted)
            false_negative += int(expected and not predicted)
        denominator = 2 * true_positive + false_positive + false_negative
        scores.append(0.0 if denominator == 0 else 2 * true_positive / denominator)
    return fmean(scores)


def score_practical_runs_v22(
    *,
    runs: tuple[PracticalCaseRunV22, ...],
    truths: tuple[PracticalTruthV22, ...],
) -> PracticalScoreReportV22:
    if not runs or len({item.arm for item in runs}) != 1:
        raise ValueError("score report requires one nonempty controller arm")
    if len({item.case_id for item in runs}) != len(runs):
        raise ValueError("score report contains duplicate case runs")
    truth_by_id = {item.case_id: item for item in truths}
    if len(truth_by_id) != len(truths) or set(truth_by_id) != {
        item.case_id for item in runs
    }:
        raise ValueError("score report truth bindings differ from runs")
    scored: list[ScoredPracticalRunV22] = []
    for run in runs:
        truth = truth_by_id[run.case_id]
        terminal_correct = (
            run.status is PracticalRunStatusV22.VALID_TERMINAL
            and run.terminal == truth.expected_terminal
        )
        incident = truth.expected_terminal == "DIAGNOSED"
        root_correct = (
            run.root_service == truth.expected_root_service if incident else None
        )
        mechanism_correct = (
            run.mechanism == truth.expected_mechanism if incident else None
        )
        scored.append(
            ScoredPracticalRunV22(
                case_id=run.case_id,
                arm=run.arm,
                outcome=_outcome(
                    run=run,
                    terminal_correct=terminal_correct,
                    root_correct=root_correct,
                    mechanism_correct=mechanism_correct,
                ),
                terminal_correct=terminal_correct,
                root_correct=root_correct,
                mechanism_correct=mechanism_correct,
                evidence_ref_valid=run.evidence_ref_valid if incident else None,
                semantic_clause_valid=(
                    run.semantic_clause_valid if incident else None
                ),
            )
        )
    incident_runs = tuple(
        run for run in runs if truth_by_id[run.case_id].expected_terminal == "DIAGNOSED"
    )
    no_incident_runs = tuple(
        run
        for run in runs
        if truth_by_id[run.case_id].expected_terminal == "NO_INCIDENT"
    )
    abstention_runs = tuple(
        run for run in runs if truth_by_id[run.case_id].expected_terminal == "ABSTAIN"
    )
    observed_logical_turns = tuple(
        max(
            item.provider_turns,
            item.first_pass_protocol_successes,
            item.post_repair_protocol_successes,
        )
        for item in runs
    )
    logical_turns = sum(observed_logical_turns)
    return PracticalScoreReportV22(
        schema_version="dta-v22.practical-score-report.v1",
        arm=runs[0].arm,
        scored_runs=tuple(scored),
        total_runs=len(runs),
        incident_denominator=len(incident_runs),
        no_incident_denominator=len(no_incident_runs),
        abstention_denominator=len(abstention_runs),
        evidence_denominator=len(incident_runs),
        run_completion_rate=_ratio(
            sum(item.status is PracticalRunStatusV22.VALID_TERMINAL for item in runs),
            len(runs),
        ),
        first_pass_protocol_success=_ratio(
            sum(item.first_pass_protocol_successes for item in runs), logical_turns
        ),
        post_repair_protocol_success=_ratio(
            sum(item.post_repair_protocol_successes for item in runs), logical_turns
        ),
        repair_rate=_ratio(sum(item.semantic_repairs for item in runs), logical_turns),
        root_service_accuracy=_ratio(
            sum(
                item.status is PracticalRunStatusV22.VALID_TERMINAL
                and item.root_service == truth_by_id[item.case_id].expected_root_service
                for item in incident_runs
            ),
            len(incident_runs),
        ),
        mechanism_accuracy=_ratio(
            sum(
                item.status is PracticalRunStatusV22.VALID_TERMINAL
                and item.mechanism == truth_by_id[item.case_id].expected_mechanism
                for item in incident_runs
            ),
            len(incident_runs),
        ),
        mechanism_macro_f1=_macro_f1(runs=runs, truths=truths),
        no_incident_accuracy=_ratio(
            sum(
                item.status is PracticalRunStatusV22.VALID_TERMINAL
                and item.terminal == "NO_INCIDENT"
                for item in no_incident_runs
            ),
            len(no_incident_runs),
        ),
        abstention_accuracy=_ratio(
            sum(
                item.status is PracticalRunStatusV22.VALID_TERMINAL
                and item.terminal == "ABSTAIN"
                for item in abstention_runs
            ),
            len(abstention_runs),
        ),
        evidence_ref_validity=_ratio(
            sum(item.evidence_ref_valid for item in incident_runs),
            len(incident_runs),
        ),
        semantic_evidence_clause_validity=_ratio(
            sum(item.semantic_clause_valid for item in incident_runs),
            len(incident_runs),
        ),
        mean_adaptive_reads=fmean(item.adaptive_reads for item in runs),
        duplicate_read_attempts=sum(item.duplicate_read_attempts for item in runs),
        mean_provider_turns=fmean(observed_logical_turns),
        input_tokens=sum(item.input_tokens for item in runs),
        output_tokens=sum(item.output_tokens for item in runs),
        total_tokens=sum(item.total_tokens for item in runs),
        mean_latency_ms=fmean(item.latency_ms for item in runs),
        transport_retry_count=sum(item.transport_retry_count for item in runs),
        uncaught_exceptions=sum(item.uncaught_exceptions for item in runs),
        agent_writes=sum(item.agent_writes for item in runs),
    )


__all__ = (
    "PracticalScoreReportV22",
    "PracticalTruthV22",
    "ScoredOutcomeV22",
    "ScoredPracticalRunV22",
    "score_practical_runs_v22",
)
