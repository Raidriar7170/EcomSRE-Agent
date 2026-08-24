"""Unified arm, schedule, and study contracts for the real-fault comparison."""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal, cast

from pydantic import Field, StrictBool, StrictFloat, StrictInt, model_validator

from ecomsre.dta_v2.v22.read_contracts import DtaModelV22, semantic_sha256_v22


class RealFaultStudyArm(str, Enum):
    V2_STYLE_FLAT_ADAPTIVE = "V2_STYLE_FLAT_ADAPTIVE"
    CURRENT_RUNTIME_BUNDLE = "CURRENT_RUNTIME_BUNDLE"


class RealFaultArmStatus(str, Enum):
    VALID_TERMINAL = "VALID_TERMINAL"
    PROTOCOL_FAILED = "PROTOCOL_FAILED"
    TRANSPORT_FAILED = "TRANSPORT_FAILED"
    RUNNER_FAILED = "RUNNER_FAILED"


class RealFaultShadowPrediction(DtaModelV22):
    schema_version: Literal["dta-v225-real-fault.shadow-prediction.v1"]
    terminal: Literal["DIAGNOSED", "NO_INCIDENT", "ABSTAIN", "FAILED"]
    root_service_alias: str | None = Field(default=None, pattern=r"^svc-[0-9a-f]{10}$")
    fault_domain: str | None
    mechanism: str | None
    supporting_evidence_refs: tuple[str, ...]
    evidence_clause_valid: StrictBool

    @model_validator(mode="after")
    def require_prediction(self) -> RealFaultShadowPrediction:
        fault_claims = (
            self.root_service_alias,
            self.fault_domain,
            self.mechanism,
        )
        if self.terminal == "DIAGNOSED":
            if any(item is None for item in fault_claims) or not self.supporting_evidence_refs:
                raise ValueError("diagnosed prediction lacks a complete supported claim")
        elif any(item is not None for item in fault_claims):
            raise ValueError("non-diagnosed prediction carries a fault claim")
        if self.supporting_evidence_refs != tuple(
            sorted(set(self.supporting_evidence_refs))
        ):
            raise ValueError("prediction evidence references are not canonical")
        return self


class RealFaultArmRun(DtaModelV22):
    schema_version: Literal["dta-v225-real-fault.arm-run.v1"]
    case_id: str = Field(pattern=r"^(?:fault|baseline)-map-[ab]$")
    arm: RealFaultStudyArm
    case_bytes_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    model_id: str = Field(min_length=1, max_length=128)
    status: RealFaultArmStatus
    prediction: RealFaultShadowPrediction
    first_useful_evidence_ordinal: StrictInt | None = Field(default=None, ge=1, le=4)
    resources_requested: StrictBool
    resource_read_shape: Literal["NONE", "SINGLE_TARGET", "MULTI_TARGET"]
    all_candidates_covered: StrictBool
    semantic_evidence_actions: StrictInt = Field(ge=0, le=4)
    target_equivalent_reads: StrictInt = Field(ge=0, le=4)
    shared_capture_reads_charged_to_arm: Literal[0]
    provider_turns: StrictInt = Field(ge=0, le=5)
    provider_calls: StrictInt = Field(ge=0, le=20)
    input_tokens: StrictInt = Field(ge=0)
    output_tokens: StrictInt = Field(ge=0)
    total_tokens: StrictInt = Field(ge=0)
    latency_ms: StrictFloat = Field(ge=0)
    protocol_failures: StrictInt = Field(ge=0, le=1)
    transport_retries: StrictInt = Field(ge=0, le=9)
    duplicate_read_attempts: StrictInt = Field(ge=0, le=4)
    empty_read_count: StrictInt = Field(ge=0, le=4)
    predicate_yield_count: StrictInt = Field(ge=0, le=4)
    bundle_resources_reads: StrictInt = Field(ge=0, le=1)
    agent_writes: Literal[0]
    action_proposals: Literal[0]
    runbook_executions: Literal[0]
    run_sha256: str

    @model_validator(mode="after")
    def require_run(self) -> RealFaultArmRun:
        if self.total_tokens != self.input_tokens + self.output_tokens:
            raise ValueError("arm-run token accounting differs")
        if self.semantic_evidence_actions > self.target_equivalent_reads:
            raise ValueError("semantic action count exceeds target-equivalent reads")
        if self.resources_requested != (self.resource_read_shape != "NONE"):
            raise ValueError("Resources request shape differs from request accounting")
        if self.resource_read_shape == "SINGLE_TARGET" and self.all_candidates_covered:
            raise ValueError("single-target Resources read claims all-candidate coverage")
        if self.bundle_resources_reads and (
            self.arm is not RealFaultStudyArm.CURRENT_RUNTIME_BUNDLE
            or self.resource_read_shape != "MULTI_TARGET"
        ):
            raise ValueError("bundle accounting differs from the current arm")
        if self.status is RealFaultArmStatus.VALID_TERMINAL:
            if self.prediction.terminal == "FAILED" or self.protocol_failures:
                raise ValueError("valid arm-run carries a failure prediction")
        elif self.prediction.terminal != "FAILED":
            raise ValueError("failed arm-run lacks a failed prediction")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"run_sha256"})
        )
        if self.run_sha256 != expected:
            raise ValueError("real-fault arm-run digest differs")
        return self


class RealFaultScheduleEntry(DtaModelV22):
    ordinal: StrictInt = Field(ge=1, le=8)
    case_id: str = Field(pattern=r"^(?:fault|baseline)-map-[ab]$")
    case_local_position: Literal[1, 2]
    arm: RealFaultStudyArm


class RealFaultLiveShadowRun(DtaModelV22):
    schema_version: Literal["dta-v225-real-fault.live-shadow-run.v1"]
    backend: Literal["LocalSandboxReadBackend"]
    case_kind: Literal["BASELINE", "AD_CPU_FAULT"]
    arm_run: RealFaultArmRun
    live_read_only: Literal[True]
    agent_writes: Literal[0]
    action_proposals: Literal[0]
    runbook_executions: Literal[0]


class RealFaultCaseTruthV1(DtaModelV22):
    schema_version: Literal["dta-v225-real-fault.case-truth.v1"]
    case_id: str = Field(pattern=r"^(?:fault|baseline)-map-[ab]$")
    case_kind: Literal["BASELINE", "AD_CPU_FAULT"]
    expected_root_alias: str | None = Field(
        default=None, pattern=r"^svc-[0-9a-f]{10}$"
    )
    expected_fault_domain: Literal["LOCAL_RESOURCE"] | None
    expected_mechanism: Literal["CPU_SATURATION"] | None

    @model_validator(mode="after")
    def require_truth(self) -> RealFaultCaseTruthV1:
        expected = (
            self.expected_root_alias,
            self.expected_fault_domain,
            self.expected_mechanism,
        )
        if self.case_kind == "AD_CPU_FAULT":
            if any(item is None for item in expected):
                raise ValueError("fault truth lacks the exact CPU claim")
        elif any(item is not None for item in expected):
            raise ValueError("baseline truth carries a fault claim")
        return self


class RealFaultStudyExecutionV1(DtaModelV22):
    schema_version: Literal["dta-v225-real-fault.study-execution.v1"]
    schedule: tuple[RealFaultScheduleEntry, ...] = Field(min_length=8, max_length=8)
    runs: tuple[RealFaultArmRun, ...] = Field(min_length=8, max_length=8)
    truth_load_after_run_ordinals: tuple[Literal[2, 4, 6, 8], ...] = Field(
        min_length=4, max_length=4
    )
    execution_count: Literal[1]
    same_case_bytes_both_arms: Literal[True]
    agent_writes: Literal[0]
    action_proposals: Literal[0]
    runbook_executions: Literal[0]
    execution_sha256: str

    @model_validator(mode="after")
    def require_execution(self) -> RealFaultStudyExecutionV1:
        expected_schedule = build_real_fault_schedule_v225()
        if self.schedule != expected_schedule:
            raise ValueError("real-fault execution schedule differs")
        if self.truth_load_after_run_ordinals != (2, 4, 6, 8):
            raise ValueError("truth was not loaded after each paired case")
        for entry, run in zip(self.schedule, self.runs, strict=True):
            if (entry.case_id, entry.arm) != (run.case_id, run.arm):
                raise ValueError("real-fault run order differs from the frozen schedule")
        for case_id in sorted({item.case_id for item in self.schedule}):
            hashes = {
                item.case_bytes_sha256 for item in self.runs if item.case_id == case_id
            }
            if len(hashes) != 1:
                raise ValueError("paired arms did not receive the same case bytes")
        if any(
            item.agent_writes or item.action_proposals or item.runbook_executions
            for item in self.runs
        ):
            raise ValueError("real-fault study crossed the read-only boundary")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"execution_sha256"})
        )
        if self.execution_sha256 != expected:
            raise ValueError("real-fault study execution digest differs")
        return self


def build_real_fault_arm_run_v225(**values: object) -> RealFaultArmRun:
    payload: dict[str, object] = {
        "schema_version": "dta-v225-real-fault.arm-run.v1",
        "shared_capture_reads_charged_to_arm": 0,
        "agent_writes": 0,
        "action_proposals": 0,
        "runbook_executions": 0,
        **values,
    }
    draft = cast(Any, RealFaultArmRun).model_construct(
        **payload, run_sha256="0" * 64
    )
    return RealFaultArmRun.model_validate(
        {
            **payload,
            "run_sha256": semantic_sha256_v22(
                draft.model_dump(mode="json", exclude={"run_sha256"})
            ),
        }
    )


def build_real_fault_schedule_v225() -> tuple[RealFaultScheduleEntry, ...]:
    pairs = (
        (
            "fault-map-a",
            RealFaultStudyArm.V2_STYLE_FLAT_ADAPTIVE,
            RealFaultStudyArm.CURRENT_RUNTIME_BUNDLE,
        ),
        (
            "fault-map-b",
            RealFaultStudyArm.CURRENT_RUNTIME_BUNDLE,
            RealFaultStudyArm.V2_STYLE_FLAT_ADAPTIVE,
        ),
        (
            "baseline-map-a",
            RealFaultStudyArm.V2_STYLE_FLAT_ADAPTIVE,
            RealFaultStudyArm.CURRENT_RUNTIME_BUNDLE,
        ),
        (
            "baseline-map-b",
            RealFaultStudyArm.CURRENT_RUNTIME_BUNDLE,
            RealFaultStudyArm.V2_STYLE_FLAT_ADAPTIVE,
        ),
    )
    return tuple(
        RealFaultScheduleEntry(
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


def build_real_fault_study_execution_v225(
    *, runs: tuple[RealFaultArmRun, ...]
) -> RealFaultStudyExecutionV1:
    payload: dict[str, object] = {
        "schema_version": "dta-v225-real-fault.study-execution.v1",
        "schedule": build_real_fault_schedule_v225(),
        "runs": runs,
        "truth_load_after_run_ordinals": (2, 4, 6, 8),
        "execution_count": 1,
        "same_case_bytes_both_arms": True,
        "agent_writes": 0,
        "action_proposals": 0,
        "runbook_executions": 0,
    }
    draft = cast(Any, RealFaultStudyExecutionV1).model_construct(
        **payload, execution_sha256="0" * 64
    )
    return RealFaultStudyExecutionV1.model_validate(
        {
            **payload,
            "execution_sha256": semantic_sha256_v22(
                draft.model_dump(mode="json", exclude={"execution_sha256"})
            ),
        }
    )


__all__ = (
    "RealFaultArmRun",
    "RealFaultArmStatus",
    "RealFaultCaseTruthV1",
    "RealFaultLiveShadowRun",
    "RealFaultScheduleEntry",
    "RealFaultShadowPrediction",
    "RealFaultStudyArm",
    "RealFaultStudyExecutionV1",
    "build_real_fault_arm_run_v225",
    "build_real_fault_schedule_v225",
    "build_real_fault_study_execution_v225",
)
