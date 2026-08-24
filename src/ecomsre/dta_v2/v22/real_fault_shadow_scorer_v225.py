"""Truth-late exact scorer for the bounded v2.2.5 real-fault study."""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal, cast

from pydantic import Field, StrictBool, StrictFloat, StrictInt, model_validator

from ecomsre.dta_v2.v22.read_contracts import DtaModelV22, semantic_sha256_v22
from ecomsre.dta_v2.v22.real_fault_comparison_contracts_v225 import (
    RealFaultArmRun,
    RealFaultArmStatus,
    RealFaultCaseTruthV1,
    RealFaultLiveShadowRun,
    RealFaultStudyArm,
    RealFaultStudyExecutionV1,
)


class RealFaultTransferTerminal(str, Enum):
    SUPPORTED = "DTA_V225_REAL_FAULT_TRANSFER_SUPPORTED"
    NOT_SUPPORTED = "DTA_V225_REAL_FAULT_TRANSFER_NOT_SUPPORTED"


class RealFaultComparisonDisposition(str, Enum):
    CURRENT_ADVANTAGE = "CURRENT_RUNTIME_DESCRIPTIVE_ADVANTAGE"
    NO_ADVANTAGE = "NO_DESCRIPTIVE_ADVANTAGE"
    V2_STYLE_ADVANTAGE = "V2_STYLE_DESCRIPTIVE_ADVANTAGE"


class RealFaultRunScoreV1(DtaModelV22):
    case_id: str
    arm: RealFaultStudyArm
    exact: StrictBool
    correct_fault_target_covered: StrictBool
    premature_no_incident: StrictBool
    transport_failure: StrictBool


class RealFaultArmScoreV1(DtaModelV22):
    arm: RealFaultStudyArm
    exact_count: StrictInt = Field(ge=0, le=4)
    fault_exact_count: StrictInt = Field(ge=0, le=2)
    baseline_exact_count: StrictInt = Field(ge=0, le=2)
    provider_calls: StrictInt = Field(ge=0)
    total_tokens: StrictInt = Field(ge=0)
    latency_ms: StrictFloat = Field(ge=0)
    semantic_evidence_actions: StrictInt = Field(ge=0)
    target_equivalent_reads: StrictInt = Field(ge=0)
    protocol_failures: StrictInt = Field(ge=0)
    transport_failures: StrictInt = Field(ge=0)
    duplicate_read_attempts: StrictInt = Field(ge=0)
    empty_read_rate: StrictFloat = Field(ge=0, le=1)
    predicate_yield_rate: StrictFloat = Field(ge=0, le=1)


class RealFaultStudyScoreV1(DtaModelV22):
    schema_version: Literal["dta-v225-real-fault.study-score.v1"]
    run_scores: tuple[RealFaultRunScoreV1, ...] = Field(min_length=8, max_length=8)
    arm_scores: tuple[RealFaultArmScoreV1, RealFaultArmScoreV1]
    map_a_fault_accuracy: dict[str, StrictBool]
    map_b_fault_accuracy: dict[str, StrictBool]
    map_a_baseline_accuracy: dict[str, StrictBool]
    map_b_baseline_accuracy: dict[str, StrictBool]
    prediction_consistency_under_alias_swap: dict[str, StrictBool]
    current_live_fault_exact: StrictBool
    current_live_baseline_exact: StrictBool | None
    live_baseline_omission_reason: str | None
    baseline_restored: StrictBool
    cleanup: Literal["CLEAN", "NOT_CLEAN"]
    non_owned_changes: StrictInt = Field(ge=0)
    transfer_terminal: RealFaultTransferTerminal
    comparison_disposition: RealFaultComparisonDisposition
    statistical_significance_testing_performed: Literal[False]
    agent_writes: Literal[0]
    action_proposals: Literal[0]
    runbook_executions: Literal[0]
    score_sha256: str

    @model_validator(mode="after")
    def require_score(self) -> RealFaultStudyScoreV1:
        if (self.current_live_baseline_exact is None) != bool(
            self.live_baseline_omission_reason
        ):
            raise ValueError("live baseline result and omission reason differ")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"score_sha256"})
        )
        if self.score_sha256 != expected:
            raise ValueError("real-fault study score digest differs")
        return self


def exact_run_v225(*, run: RealFaultArmRun, truth: RealFaultCaseTruthV1) -> bool:
    if run.case_id != truth.case_id or run.status is not RealFaultArmStatus.VALID_TERMINAL:
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
        and run.agent_writes == 0
    )


def _arm_score(
    *, arm: RealFaultStudyArm, scored: tuple[tuple[RealFaultArmRun, bool], ...]
) -> RealFaultArmScoreV1:
    rows = tuple(item for item in scored if item[0].arm is arm)
    fault = tuple(item for item in rows if item[0].case_id.startswith("fault-"))
    baseline = tuple(item for item in rows if item[0].case_id.startswith("baseline-"))
    total_reads = sum(item[0].semantic_evidence_actions for item in rows)
    return RealFaultArmScoreV1(
        arm=arm,
        exact_count=sum(item[1] for item in rows),
        fault_exact_count=sum(item[1] for item in fault),
        baseline_exact_count=sum(item[1] for item in baseline),
        provider_calls=sum(item[0].provider_calls for item in rows),
        total_tokens=sum(item[0].total_tokens for item in rows),
        latency_ms=float(sum(item[0].latency_ms for item in rows)),
        semantic_evidence_actions=total_reads,
        target_equivalent_reads=sum(item[0].target_equivalent_reads for item in rows),
        protocol_failures=sum(item[0].protocol_failures for item in rows),
        transport_failures=sum(
            item[0].status is RealFaultArmStatus.TRANSPORT_FAILED for item in rows
        ),
        duplicate_read_attempts=sum(item[0].duplicate_read_attempts for item in rows),
        empty_read_rate=(
            0.0
            if total_reads == 0
            else sum(item[0].empty_read_count for item in rows) / total_reads
        ),
        predicate_yield_rate=(
            0.0
            if total_reads == 0
            else sum(item[0].predicate_yield_count for item in rows) / total_reads
        ),
    )


def _disposition(
    *, flat: RealFaultArmScoreV1, current: RealFaultArmScoreV1
) -> RealFaultComparisonDisposition:
    if current.exact_count > flat.exact_count or (
        current.exact_count == flat.exact_count
        and current.baseline_exact_count >= flat.baseline_exact_count
        and current.provider_calls < flat.provider_calls
        and current.total_tokens < flat.total_tokens
        and current.target_equivalent_reads <= flat.target_equivalent_reads
    ):
        return RealFaultComparisonDisposition.CURRENT_ADVANTAGE
    if flat.exact_count > current.exact_count or (
        flat.exact_count == current.exact_count
        and flat.baseline_exact_count >= current.baseline_exact_count
        and flat.provider_calls < current.provider_calls
        and flat.total_tokens < current.total_tokens
        and flat.target_equivalent_reads <= current.target_equivalent_reads
    ):
        return RealFaultComparisonDisposition.V2_STYLE_ADVANTAGE
    return RealFaultComparisonDisposition.NO_ADVANTAGE


def _alias_consistency(
    *, arm: RealFaultStudyArm, scored: tuple[tuple[RealFaultArmRun, bool], ...]
) -> bool:
    rows = tuple(item for item in scored if item[0].arm is arm)
    return all(
        next(item[1] for item in rows if item[0].case_id == left)
        == next(item[1] for item in rows if item[0].case_id == right)
        for left, right in (
            ("fault-map-a", "fault-map-b"),
            ("baseline-map-a", "baseline-map-b"),
        )
    )


def score_real_fault_study_v225(
    *,
    execution: RealFaultStudyExecutionV1,
    truths: tuple[RealFaultCaseTruthV1, ...],
    live_fault: RealFaultLiveShadowRun,
    live_baseline: RealFaultLiveShadowRun | None,
    live_baseline_omission_reason: str | None,
    baseline_restored: bool,
    cleanup: Literal["CLEAN", "NOT_CLEAN"],
    non_owned_changes: int,
) -> RealFaultStudyScoreV1:
    by_case = {item.case_id: item for item in truths}
    if tuple(sorted(by_case)) != tuple(sorted({item.case_id for item in execution.runs})):
        raise ValueError("study truth set differs from executed cases")
    scored = tuple((run, exact_run_v225(run=run, truth=by_case[run.case_id])) for run in execution.runs)
    run_scores = tuple(
        RealFaultRunScoreV1(
            case_id=run.case_id,
            arm=run.arm,
            exact=exact,
            correct_fault_target_covered=(
                by_case[run.case_id].case_kind == "AD_CPU_FAULT"
                and run.resources_requested
                and (
                    run.all_candidates_covered
                    or run.prediction.root_service_alias
                    == by_case[run.case_id].expected_root_alias
                )
            ),
            premature_no_incident=(
                by_case[run.case_id].case_kind == "AD_CPU_FAULT"
                and run.prediction.terminal == "NO_INCIDENT"
            ),
            transport_failure=run.status is RealFaultArmStatus.TRANSPORT_FAILED,
        )
        for run, exact in scored
    )
    flat = _arm_score(arm=RealFaultStudyArm.V2_STYLE_FLAT_ADAPTIVE, scored=scored)
    current = _arm_score(arm=RealFaultStudyArm.CURRENT_RUNTIME_BUNDLE, scored=scored)
    live_fault_truth = next(item for item in truths if item.case_id == live_fault.arm_run.case_id)
    live_fault_exact = exact_run_v225(run=live_fault.arm_run, truth=live_fault_truth)
    live_baseline_exact = (
        None
        if live_baseline is None
        else exact_run_v225(
            run=live_baseline.arm_run,
            truth=next(
                item for item in truths if item.case_id == live_baseline.arm_run.case_id
            ),
        )
    )
    baseline_clause = (
        live_baseline_exact is True
        or (
            live_baseline is None
            and bool(live_baseline_omission_reason)
            and current.baseline_exact_count == 2
        )
    )
    supported = (
        current.exact_count == 4
        and baseline_clause
        and live_fault_exact
        and live_fault.arm_run.all_candidates_covered
        and current.protocol_failures == 0
        and baseline_restored
        and cleanup == "CLEAN"
        and non_owned_changes == 0
        and all(item.agent_writes == 0 for item in execution.runs)
        and live_fault.agent_writes == 0
        and (live_baseline is None or live_baseline.agent_writes == 0)
    )
    exact_by = {(item.case_id, item.arm): item.exact for item in run_scores}
    arm_key = {item.value: item for item in RealFaultStudyArm}
    payload: dict[str, object] = {
        "schema_version": "dta-v225-real-fault.study-score.v1",
        "run_scores": run_scores,
        "arm_scores": (flat, current),
        "map_a_fault_accuracy": {
            key: exact_by[("fault-map-a", arm)] for key, arm in arm_key.items()
        },
        "map_b_fault_accuracy": {
            key: exact_by[("fault-map-b", arm)] for key, arm in arm_key.items()
        },
        "map_a_baseline_accuracy": {
            key: exact_by[("baseline-map-a", arm)] for key, arm in arm_key.items()
        },
        "map_b_baseline_accuracy": {
            key: exact_by[("baseline-map-b", arm)] for key, arm in arm_key.items()
        },
        "prediction_consistency_under_alias_swap": {
            key: _alias_consistency(arm=arm, scored=scored)
            for key, arm in arm_key.items()
        },
        "current_live_fault_exact": live_fault_exact,
        "current_live_baseline_exact": live_baseline_exact,
        "live_baseline_omission_reason": live_baseline_omission_reason,
        "baseline_restored": baseline_restored,
        "cleanup": cleanup,
        "non_owned_changes": non_owned_changes,
        "transfer_terminal": (
            RealFaultTransferTerminal.SUPPORTED
            if supported
            else RealFaultTransferTerminal.NOT_SUPPORTED
        ),
        "comparison_disposition": _disposition(flat=flat, current=current),
        "statistical_significance_testing_performed": False,
        "agent_writes": 0,
        "action_proposals": 0,
        "runbook_executions": 0,
    }
    draft = cast(Any, RealFaultStudyScoreV1).model_construct(
        **payload, score_sha256="0" * 64
    )
    return RealFaultStudyScoreV1.model_validate(
        {
            **payload,
            "score_sha256": semantic_sha256_v22(
                draft.model_dump(mode="json", exclude={"score_sha256"})
            ),
        }
    )


__all__ = (
    "RealFaultArmScoreV1",
    "RealFaultComparisonDisposition",
    "RealFaultRunScoreV1",
    "RealFaultStudyScoreV1",
    "RealFaultTransferTerminal",
    "exact_run_v225",
    "score_real_fault_study_v225",
)
