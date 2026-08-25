"""Truth-late transfer and acquisition scorer for the DTA v2.2.6 study."""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal, cast

from pydantic import Field, StrictBool, StrictFloat, StrictInt, model_validator

from ecomsre.dta_v2.v22.read_contracts import DtaModelV22, semantic_sha256_v22
from ecomsre.dta_v2.v22.real_fault_comparison_contracts_v226 import (
    RealFaultArmRunV226,
    RealFaultArmStatusV226,
    RealFaultStudyArmV226,
)
from ecomsre.dta_v2.v22.real_fault_live_shadow_v226 import (
    RealFaultLiveShadowRunV226,
)
from ecomsre.dta_v2.v22.real_fault_study_v226 import (
    RealFaultCaseTruthV226,
    RealFaultStudyExecutionV226,
)


class RealFaultTransferTerminalV226(str, Enum):
    SUPPORTED = "DTA_V226_CURRENT_REAL_FAULT_TRANSFER_SUPPORTED"
    NOT_SUPPORTED = "DTA_V226_CURRENT_REAL_FAULT_TRANSFER_NOT_SUPPORTED"


class RealFaultComparisonDispositionV226(str, Enum):
    CURRENT_ADVANTAGE = "CURRENT_RUNTIME_ACQUISITION_ADVANTAGE"
    MODEL_ADVANTAGE = "MODEL_DIRECTED_ACQUISITION_ADVANTAGE"
    NO_ADVANTAGE = "NO_ACQUISITION_ADVANTAGE"


class RealFaultRunScoreV226(DtaModelV22):
    case_id: str
    arm: RealFaultStudyArmV226
    valid_terminal: StrictBool
    exact: StrictBool
    fault_root_correct: StrictBool
    mechanism_correct: StrictBool
    evidence_clause_valid: StrictBool
    correct_fault_target_covered: StrictBool
    premature_no_incident: StrictBool
    false_positive_fault_on_baseline: StrictBool


class RealFaultArmScoreV226(DtaModelV22):
    arm: RealFaultStudyArmV226
    valid_terminal_count: StrictInt = Field(ge=0, le=4)
    valid_terminal_rate: StrictFloat = Field(ge=0, le=1)
    exact_count: StrictInt = Field(ge=0, le=4)
    fault_exact_count: StrictInt = Field(ge=0, le=2)
    baseline_exact_count: StrictInt = Field(ge=0, le=2)
    root_correct_count: StrictInt = Field(ge=0, le=2)
    mechanism_correct_count: StrictInt = Field(ge=0, le=2)
    evidence_clause_valid_count: StrictInt = Field(ge=0, le=4)
    premature_no_incident_count: StrictInt = Field(ge=0, le=2)
    false_positive_fault_on_baseline_count: StrictInt = Field(ge=0, le=2)
    resources_selected_count: StrictInt = Field(ge=0, le=4)
    single_target_resource_read_count: StrictInt = Field(ge=0, le=4)
    multi_target_resource_read_count: StrictInt = Field(ge=0, le=4)
    all_candidates_covered_count: StrictInt = Field(ge=0, le=4)
    semantic_evidence_actions: StrictInt = Field(ge=0, le=16)
    target_equivalent_reads: StrictInt = Field(ge=0, le=16)
    duplicate_read_attempts: StrictInt = Field(ge=0, le=16)
    empty_read_rate: StrictFloat = Field(ge=0, le=1)
    predicate_yield_rate: StrictFloat = Field(ge=0)
    provider_calls: StrictInt = Field(ge=0)
    input_tokens: StrictInt = Field(ge=0)
    output_tokens: StrictInt = Field(ge=0)
    total_tokens: StrictInt = Field(ge=0)
    latency_ms: StrictFloat = Field(ge=0)
    transport_retries: StrictInt = Field(ge=0)
    first_pass_protocol_success_count: StrictInt = Field(ge=0, le=4)
    post_repair_protocol_success_count: StrictInt = Field(ge=0, le=4)
    protocol_repairs: StrictInt = Field(ge=0)
    protocol_failures: StrictInt = Field(ge=0, le=4)
    runner_failures: StrictInt = Field(ge=0, le=4)
    transport_failures: StrictInt = Field(ge=0, le=4)

    @model_validator(mode="after")
    def require_tokens(self) -> RealFaultArmScoreV226:
        if self.total_tokens != self.input_tokens + self.output_tokens:
            raise ValueError("v2.2.6 arm-score token accounting differs")
        return self


class RealFaultStudyScoreV226(DtaModelV22):
    schema_version: Literal["dta-v226-real-fault.study-score.v1"]
    execution_id: str
    run_scores: tuple[RealFaultRunScoreV226, ...] = Field(
        min_length=8, max_length=8
    )
    arm_scores: tuple[RealFaultArmScoreV226, RealFaultArmScoreV226]
    all_snapshot_runs_valid: StrictBool
    current_snapshot_exact_count: StrictInt = Field(ge=0, le=4)
    current_live_fault_exact: StrictBool
    current_live_baseline_exact: StrictBool
    snapshot_live_fault_terminal_agreement: StrictBool
    snapshot_live_baseline_terminal_agreement: StrictBool
    snapshot_live_fault_evidence_source_agreement: StrictBool
    snapshot_live_baseline_evidence_source_agreement: StrictBool
    premature_no_incident_count: StrictInt = Field(ge=0, le=4)
    false_positive_fault_on_baseline_count: StrictInt = Field(ge=0, le=4)
    failure_stage_distribution: dict[str, StrictInt]
    safe_error_code_distribution: dict[str, StrictInt]
    baseline_restored: StrictBool
    cleanup: Literal["CLEAN", "NOT_CLEAN"]
    non_owned_changes: StrictInt = Field(ge=0)
    transfer_terminal: RealFaultTransferTerminalV226
    comparison_admissible: StrictBool
    comparison_disposition: RealFaultComparisonDispositionV226 | None
    statistical_significance_testing_performed: Literal[False]
    score_driven_retries: Literal[0]
    agent_writes: Literal[0]
    action_proposals: Literal[0]
    runbook_executions: Literal[0]
    score_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_score(self) -> RealFaultStudyScoreV226:
        if self.comparison_admissible != (self.comparison_disposition is not None):
            raise ValueError("v2.2.6 comparison admission differs from disposition")
        if self.score_sha256 != self.recompute_sha256():
            raise ValueError("v2.2.6 score digest differs")
        return self

    def recompute_sha256(self) -> str:
        return semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"score_sha256"})
        )


def exact_run_v226(
    *, run: RealFaultArmRunV226, truth: RealFaultCaseTruthV226
) -> bool:
    if (
        run.case_id != truth.case_id
        or run.status is not RealFaultArmStatusV226.VALID_TERMINAL
    ):
        return False
    prediction = run.prediction
    if truth.case_kind == "AD_CPU_FAULT":
        return (
            prediction.terminal == "DIAGNOSED"
            and prediction.root_service_alias == truth.expected_root_alias
            and prediction.fault_domain == truth.expected_fault_domain
            and prediction.mechanism == truth.expected_mechanism
            and prediction.evidence_clause_valid
            and bool(prediction.supporting_evidence_refs)
        )
    return (
        prediction.terminal == "NO_INCIDENT"
        and prediction.root_service_alias is None
        and prediction.fault_domain is None
        and prediction.mechanism is None
        and prediction.evidence_clause_valid
        and bool(prediction.supporting_evidence_refs)
    )


def _run_score(
    *, run: RealFaultArmRunV226, truth: RealFaultCaseTruthV226
) -> RealFaultRunScoreV226:
    fault = truth.case_kind == "AD_CPU_FAULT"
    diagnosed = run.prediction.terminal == "DIAGNOSED"
    return RealFaultRunScoreV226(
        case_id=run.case_id,
        arm=run.arm,
        valid_terminal=run.status is RealFaultArmStatusV226.VALID_TERMINAL,
        exact=exact_run_v226(run=run, truth=truth),
        fault_root_correct=(
            fault and run.prediction.root_service_alias == truth.expected_root_alias
        ),
        mechanism_correct=(
            fault and run.prediction.mechanism == truth.expected_mechanism
        ),
        evidence_clause_valid=run.prediction.evidence_clause_valid,
        correct_fault_target_covered=(
            fault
            and run.resources_selected
            and (
                run.all_candidates_covered
                or run.prediction.root_service_alias == truth.expected_root_alias
            )
        ),
        premature_no_incident=fault and run.prediction.terminal == "NO_INCIDENT",
        false_positive_fault_on_baseline=(not fault and diagnosed),
    )


def _arm_score(
    *,
    arm: RealFaultStudyArmV226,
    runs: tuple[RealFaultArmRunV226, ...],
    run_scores: tuple[RealFaultRunScoreV226, ...],
) -> RealFaultArmScoreV226:
    selected_runs = tuple(run for run in runs if run.arm is arm)
    selected_scores = tuple(item for item in run_scores if item.arm is arm)
    semantic_actions = sum(run.semantic_evidence_actions for run in selected_runs)
    return RealFaultArmScoreV226(
        arm=arm,
        valid_terminal_count=sum(item.valid_terminal for item in selected_scores),
        valid_terminal_rate=sum(item.valid_terminal for item in selected_scores) / 4,
        exact_count=sum(item.exact for item in selected_scores),
        fault_exact_count=sum(
            item.exact and item.case_id.startswith("fault-") for item in selected_scores
        ),
        baseline_exact_count=sum(
            item.exact and item.case_id.startswith("baseline-")
            for item in selected_scores
        ),
        root_correct_count=sum(item.fault_root_correct for item in selected_scores),
        mechanism_correct_count=sum(
            item.mechanism_correct for item in selected_scores
        ),
        evidence_clause_valid_count=sum(
            item.evidence_clause_valid for item in selected_scores
        ),
        premature_no_incident_count=sum(
            item.premature_no_incident for item in selected_scores
        ),
        false_positive_fault_on_baseline_count=sum(
            item.false_positive_fault_on_baseline for item in selected_scores
        ),
        resources_selected_count=sum(run.resources_selected for run in selected_runs),
        single_target_resource_read_count=sum(
            run.resource_read_shape == "SINGLE_TARGET" for run in selected_runs
        ),
        multi_target_resource_read_count=sum(
            run.resource_read_shape == "MULTI_TARGET" for run in selected_runs
        ),
        all_candidates_covered_count=sum(
            run.all_candidates_covered for run in selected_runs
        ),
        semantic_evidence_actions=semantic_actions,
        target_equivalent_reads=sum(
            run.target_equivalent_reads for run in selected_runs
        ),
        duplicate_read_attempts=sum(
            run.duplicate_read_attempts for run in selected_runs
        ),
        empty_read_rate=(
            0.0
            if semantic_actions == 0
            else sum(run.empty_read_count for run in selected_runs) / semantic_actions
        ),
        predicate_yield_rate=(
            0.0
            if semantic_actions == 0
            else sum(run.predicate_yield_count for run in selected_runs)
            / semantic_actions
        ),
        provider_calls=sum(run.provider_calls for run in selected_runs),
        input_tokens=sum(run.input_tokens for run in selected_runs),
        output_tokens=sum(run.output_tokens for run in selected_runs),
        total_tokens=sum(run.total_tokens for run in selected_runs),
        latency_ms=float(sum(run.latency_ms for run in selected_runs)),
        transport_retries=sum(run.transport_retries for run in selected_runs),
        first_pass_protocol_success_count=sum(
            run.first_pass_protocol_success for run in selected_runs
        ),
        post_repair_protocol_success_count=sum(
            run.post_repair_protocol_success for run in selected_runs
        ),
        protocol_repairs=sum(run.protocol_repairs for run in selected_runs),
        protocol_failures=sum(run.protocol_failures for run in selected_runs),
        runner_failures=sum(run.runner_failures for run in selected_runs),
        transport_failures=sum(run.transport_failures for run in selected_runs),
    )


def _comparison_disposition(
    *,
    model: RealFaultArmScoreV226,
    current: RealFaultArmScoreV226,
) -> RealFaultComparisonDispositionV226 | None:
    if model.valid_terminal_count != 4 or current.valid_terminal_count != 4:
        return None
    if current.exact_count > model.exact_count:
        return RealFaultComparisonDispositionV226.CURRENT_ADVANTAGE
    if model.exact_count > current.exact_count:
        return RealFaultComparisonDispositionV226.MODEL_ADVANTAGE
    current_tie = current.exact_count > 0 and (
        current.fault_exact_count >= model.fault_exact_count
        and current.baseline_exact_count >= model.baseline_exact_count
        and current.evidence_clause_valid_count >= model.evidence_clause_valid_count
        and (
            current.provider_calls < model.provider_calls
            or current.total_tokens < model.total_tokens
        )
        and current.target_equivalent_reads <= model.target_equivalent_reads
    )
    model_tie = model.exact_count > 0 and (
        model.fault_exact_count >= current.fault_exact_count
        and model.baseline_exact_count >= current.baseline_exact_count
        and model.evidence_clause_valid_count >= current.evidence_clause_valid_count
        and (
            model.provider_calls < current.provider_calls
            or model.total_tokens < current.total_tokens
        )
        and model.target_equivalent_reads <= current.target_equivalent_reads
    )
    if current_tie and not model_tie:
        return RealFaultComparisonDispositionV226.CURRENT_ADVANTAGE
    if model_tie and not current_tie:
        return RealFaultComparisonDispositionV226.MODEL_ADVANTAGE
    return RealFaultComparisonDispositionV226.NO_ADVANTAGE


def _distribution(
    runs: tuple[RealFaultArmRunV226, ...], attribute: str
) -> dict[str, int]:
    values = (
        getattr(run.trace, attribute)
        for run in runs
        if getattr(run.trace, attribute) is not None
    )
    output: dict[str, int] = {}
    for value in values:
        key = value.value
        output[key] = output.get(key, 0) + 1
    return dict(sorted(output.items()))


def score_real_fault_study_v226(
    *,
    execution: RealFaultStudyExecutionV226,
    truths: tuple[RealFaultCaseTruthV226, ...],
    live_fault: RealFaultLiveShadowRunV226,
    live_baseline: RealFaultLiveShadowRunV226,
    baseline_restored: bool,
    cleanup: Literal["CLEAN", "NOT_CLEAN"],
    non_owned_changes: int,
) -> RealFaultStudyScoreV226:
    truth_by_case = {item.case_id: item for item in truths}
    if set(truth_by_case) != {run.case_id for run in execution.runs}:
        raise ValueError("v2.2.6 truth set differs from execution")
    run_scores = tuple(
        _run_score(run=run, truth=truth_by_case[run.case_id])
        for run in execution.runs
    )
    model = _arm_score(
        arm=RealFaultStudyArmV226.MODEL_DIRECTED_RETRIEVAL,
        runs=execution.runs,
        run_scores=run_scores,
    )
    current = _arm_score(
        arm=RealFaultStudyArmV226.CURRENT_RUNTIME_BUNDLE,
        runs=execution.runs,
        run_scores=run_scores,
    )
    live_fault_exact = exact_run_v226(
        run=live_fault.arm_run,
        truth=truth_by_case[live_fault.arm_run.case_id],
    )
    live_baseline_exact = exact_run_v226(
        run=live_baseline.arm_run,
        truth=truth_by_case[live_baseline.arm_run.case_id],
    )
    snapshot_fault = next(
        run
        for run in execution.runs
        if run.case_id == live_fault.arm_run.case_id
        and run.arm is RealFaultStudyArmV226.CURRENT_RUNTIME_BUNDLE
    )
    snapshot_baseline = next(
        run
        for run in execution.runs
        if run.case_id == live_baseline.arm_run.case_id
        and run.arm is RealFaultStudyArmV226.CURRENT_RUNTIME_BUNDLE
    )
    transfer_supported = (
        current.exact_count == 4
        and current.protocol_failures == 0
        and current.runner_failures == 0
        and current.transport_failures == 0
        and current.multi_target_resource_read_count == 4
        and current.all_candidates_covered_count == 4
        and current.evidence_clause_valid_count == 4
        and live_fault_exact
        and live_baseline_exact
        and live_fault.backend == "LocalSandboxReadBackend"
        and live_baseline.backend == "LocalSandboxReadBackend"
        and live_fault.arm_run.all_candidates_covered
        and live_baseline.arm_run.all_candidates_covered
        and baseline_restored
        and cleanup == "CLEAN"
        and non_owned_changes == 0
    )
    disposition = _comparison_disposition(model=model, current=current)
    payload: dict[str, object] = {
        "schema_version": "dta-v226-real-fault.study-score.v1",
        "execution_id": execution.execution_id,
        "run_scores": run_scores,
        "arm_scores": (model, current),
        "all_snapshot_runs_valid": all(item.valid_terminal for item in run_scores),
        "current_snapshot_exact_count": current.exact_count,
        "current_live_fault_exact": live_fault_exact,
        "current_live_baseline_exact": live_baseline_exact,
        "snapshot_live_fault_terminal_agreement": (
            snapshot_fault.prediction.terminal == live_fault.arm_run.prediction.terminal
        ),
        "snapshot_live_baseline_terminal_agreement": (
            snapshot_baseline.prediction.terminal
            == live_baseline.arm_run.prediction.terminal
        ),
        "snapshot_live_fault_evidence_source_agreement": (
            snapshot_fault.resources_selected and live_fault.arm_run.resources_selected
        ),
        "snapshot_live_baseline_evidence_source_agreement": (
            snapshot_baseline.resources_selected
            and live_baseline.arm_run.resources_selected
        ),
        "premature_no_incident_count": sum(
            item.premature_no_incident for item in run_scores
        ),
        "false_positive_fault_on_baseline_count": sum(
            item.false_positive_fault_on_baseline for item in run_scores
        ),
        "failure_stage_distribution": _distribution(execution.runs, "failure_stage"),
        "safe_error_code_distribution": _distribution(
            execution.runs, "safe_error_code"
        ),
        "baseline_restored": baseline_restored,
        "cleanup": cleanup,
        "non_owned_changes": non_owned_changes,
        "transfer_terminal": (
            RealFaultTransferTerminalV226.SUPPORTED
            if transfer_supported
            else RealFaultTransferTerminalV226.NOT_SUPPORTED
        ),
        "comparison_admissible": disposition is not None,
        "comparison_disposition": disposition,
        "statistical_significance_testing_performed": False,
        "score_driven_retries": execution.score_driven_retries,
        "agent_writes": 0,
        "action_proposals": 0,
        "runbook_executions": 0,
    }
    draft = cast(Any, RealFaultStudyScoreV226).model_construct(
        **payload, score_sha256="0" * 64
    )
    return RealFaultStudyScoreV226.model_validate(
        {**payload, "score_sha256": draft.recompute_sha256()}
    )


__all__ = (
    "RealFaultArmScoreV226",
    "RealFaultComparisonDispositionV226",
    "RealFaultRunScoreV226",
    "RealFaultStudyScoreV226",
    "RealFaultTransferTerminalV226",
    "exact_run_v226",
    "score_real_fault_study_v226",
)
