"""Truth-late single-execution contract for the DTA v2.2.6 paired study."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Literal, cast

from pydantic import Field, StrictInt, model_validator

from ecomsre.dta_v2.v22.current_runtime_bundle_v226 import (
    run_current_runtime_bundle_v226,
)
from ecomsre.dta_v2.v22.model_directed_retrieval_v226 import (
    run_model_directed_retrieval_v226,
)
from ecomsre.dta_v2.v22.read_contracts import DtaModelV22, semantic_sha256_v22
from ecomsre.dta_v2.v22.real_fault_capture_v225 import RealFaultOpaqueCaptureV1
from ecomsre.dta_v2.v22.real_fault_comparison_contracts_v226 import (
    RealFaultArmRunV226,
    RealFaultStudyArmV226,
)
from ecomsre.dta_v2.v22.real_fault_selection_v226 import (
    RealFaultSelectionProviderV226,
)


class RealFaultCaseTruthV226(DtaModelV22):
    schema_version: Literal["dta-v226-real-fault.case-truth.v1"]
    case_id: str = Field(pattern=r"^(?:fault|baseline)-map-[ab]$")
    case_kind: Literal["BASELINE", "AD_CPU_FAULT"]
    expected_root_alias: str | None = Field(
        default=None, pattern=r"^svc-[0-9a-f]{10}$"
    )
    expected_fault_domain: Literal["LOCAL_RESOURCE"] | None
    expected_mechanism: Literal["CPU_SATURATION"] | None

    @model_validator(mode="after")
    def require_truth(self) -> RealFaultCaseTruthV226:
        claims = (
            self.expected_root_alias,
            self.expected_fault_domain,
            self.expected_mechanism,
        )
        if self.case_kind == "AD_CPU_FAULT":
            if any(item is None for item in claims) or not self.case_id.startswith(
                "fault-"
            ):
                raise ValueError("v2.2.6 fault truth lacks the exact CPU claim")
        elif any(item is not None for item in claims) or not self.case_id.startswith(
            "baseline-"
        ):
            raise ValueError("v2.2.6 baseline truth carries a fault claim")
        return self


class RealFaultScheduleEntryV226(DtaModelV22):
    ordinal: StrictInt = Field(ge=1, le=8)
    case_id: str = Field(pattern=r"^(?:fault|baseline)-map-[ab]$")
    case_local_position: Literal[1, 2]
    arm: RealFaultStudyArmV226


def build_real_fault_schedule_v226() -> tuple[RealFaultScheduleEntryV226, ...]:
    pairs = (
        (
            "fault-map-a",
            RealFaultStudyArmV226.MODEL_DIRECTED_RETRIEVAL,
            RealFaultStudyArmV226.CURRENT_RUNTIME_BUNDLE,
        ),
        (
            "fault-map-b",
            RealFaultStudyArmV226.CURRENT_RUNTIME_BUNDLE,
            RealFaultStudyArmV226.MODEL_DIRECTED_RETRIEVAL,
        ),
        (
            "baseline-map-a",
            RealFaultStudyArmV226.MODEL_DIRECTED_RETRIEVAL,
            RealFaultStudyArmV226.CURRENT_RUNTIME_BUNDLE,
        ),
        (
            "baseline-map-b",
            RealFaultStudyArmV226.CURRENT_RUNTIME_BUNDLE,
            RealFaultStudyArmV226.MODEL_DIRECTED_RETRIEVAL,
        ),
    )
    return tuple(
        RealFaultScheduleEntryV226(
            ordinal=ordinal,
            case_id=case_id,
            case_local_position=cast(Literal[1, 2], position),
            arm=arm,
        )
        for ordinal, (case_id, position, arm) in enumerate(
            (
                (case_id, position, arm)
                for case_id, first, second in pairs
                for position, arm in ((1, first), (2, second))
            ),
            start=1,
        )
    )


class RealFaultStudyExecutionV226(DtaModelV22):
    schema_version: Literal["dta-v226-real-fault.study-execution.v1"]
    execution_id: str = Field(pattern=r"^exec-v226-[0-9a-f]{16}$")
    schedule: tuple[RealFaultScheduleEntryV226, ...] = Field(
        min_length=8, max_length=8
    )
    runs: tuple[RealFaultArmRunV226, ...] = Field(min_length=8, max_length=8)
    truth_load_after_run_ordinals: tuple[Literal[2, 4, 6, 8], ...] = Field(
        min_length=4, max_length=4
    )
    execution_count: Literal[1]
    arm_run_count: Literal[8]
    run_attempts_per_ordinal: tuple[Literal[1], ...] = Field(
        min_length=8, max_length=8
    )
    score_driven_retries: Literal[0]
    no_retry_after_valid_terminal: Literal[True]
    same_case_bytes_both_arms: Literal[True]
    truth_consulted_during_arm_runs: Literal[False]
    agent_writes: Literal[0]
    action_proposals: Literal[0]
    runbook_executions: Literal[0]
    execution_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_execution(self) -> RealFaultStudyExecutionV226:
        if self.schedule != build_real_fault_schedule_v226():
            raise ValueError("v2.2.6 final schedule differs")
        if self.truth_load_after_run_ordinals != (2, 4, 6, 8):
            raise ValueError("v2.2.6 truth load boundary differs")
        if self.run_attempts_per_ordinal != (1, 1, 1, 1, 1, 1, 1, 1):
            raise ValueError("v2.2.6 run attempt count differs")
        for entry, run in zip(self.schedule, self.runs, strict=True):
            if (entry.case_id, entry.arm) != (run.case_id, run.arm):
                raise ValueError("v2.2.6 run order differs from schedule")
        for case_id in {item.case_id for item in self.schedule}:
            case_hashes = {
                run.case_bytes_sha256 for run in self.runs if run.case_id == case_id
            }
            if len(case_hashes) != 1:
                raise ValueError("v2.2.6 paired arms received different capture bytes")
        if any(
            run.agent_writes or run.action_proposals or run.runbook_executions
            for run in self.runs
        ):
            raise ValueError("v2.2.6 execution crossed the read-only boundary")
        if self.execution_sha256 != self.recompute_sha256():
            raise ValueError("v2.2.6 execution digest differs")
        return self

    def recompute_sha256(self) -> str:
        return semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"execution_sha256"})
        )


def build_real_fault_study_execution_v226(
    *,
    execution_id: str,
    runs: tuple[RealFaultArmRunV226, ...],
) -> RealFaultStudyExecutionV226:
    payload = {
        "schema_version": "dta-v226-real-fault.study-execution.v1",
        "execution_id": execution_id,
        "schedule": build_real_fault_schedule_v226(),
        "runs": runs,
        "truth_load_after_run_ordinals": (2, 4, 6, 8),
        "execution_count": 1,
        "arm_run_count": 8,
        "run_attempts_per_ordinal": (1, 1, 1, 1, 1, 1, 1, 1),
        "score_driven_retries": 0,
        "no_retry_after_valid_terminal": True,
        "same_case_bytes_both_arms": True,
        "truth_consulted_during_arm_runs": False,
        "agent_writes": 0,
        "action_proposals": 0,
        "runbook_executions": 0,
    }
    draft = cast(Any, RealFaultStudyExecutionV226).model_construct(
        **payload, execution_sha256="0" * 64
    )
    return RealFaultStudyExecutionV226.model_validate(
        {**payload, "execution_sha256": draft.recompute_sha256()}
    )


ProviderFactoryV226 = Callable[[], RealFaultSelectionProviderV226]
TruthLoaderV226 = Callable[[str], RealFaultCaseTruthV226]
RunObserverV226 = Callable[[int, RealFaultArmRunV226], None]


def execute_real_fault_study_v226(
    *,
    execution_id: str,
    captures: dict[str, RealFaultOpaqueCaptureV1],
    model_id: str,
    provider_factory: ProviderFactoryV226,
    truth_loader: TruthLoaderV226,
    run_observer: RunObserverV226 | None = None,
) -> tuple[
    RealFaultStudyExecutionV226,
    tuple[
        RealFaultCaseTruthV226,
        RealFaultCaseTruthV226,
        RealFaultCaseTruthV226,
        RealFaultCaseTruthV226,
    ],
]:
    """Execute each scheduled arm once and load truth only after each local pair."""

    schedule = build_real_fault_schedule_v226()
    if set(captures) != {item.case_id for item in schedule}:
        raise ValueError("v2.2.6 capture set differs from schedule")
    runs: list[RealFaultArmRunV226] = []
    truths: list[RealFaultCaseTruthV226] = []
    for entry in schedule:
        capture = captures[entry.case_id]
        baseline = captures[f"baseline-map-{entry.case_id[-1]}"]
        runner = (
            run_model_directed_retrieval_v226
            if entry.arm is RealFaultStudyArmV226.MODEL_DIRECTED_RETRIEVAL
            else run_current_runtime_bundle_v226
        )
        run = runner(
            capture=capture,
            baseline_capture=baseline,
            model_id=model_id,
            provider=provider_factory(),
        )
        runs.append(run)
        if run_observer is not None:
            run_observer(entry.ordinal, run)
        if entry.case_local_position == 2:
            truth = truth_loader(entry.case_id)
            if truth.case_id != entry.case_id:
                raise ValueError("v2.2.6 truth loader returned another case")
            truths.append(truth)
    execution = build_real_fault_study_execution_v226(
        execution_id=execution_id,
        runs=tuple(runs),
    )
    return execution, cast(
        tuple[
            RealFaultCaseTruthV226,
            RealFaultCaseTruthV226,
            RealFaultCaseTruthV226,
            RealFaultCaseTruthV226,
        ],
        tuple(truths),
    )


__all__ = (
    "RealFaultCaseTruthV226",
    "RealFaultScheduleEntryV226",
    "RealFaultStudyExecutionV226",
    "build_real_fault_schedule_v226",
    "build_real_fault_study_execution_v226",
    "execute_real_fault_study_v226",
)
