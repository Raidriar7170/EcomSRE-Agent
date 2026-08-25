"""Shared arm-run contracts for the DTA v2.2.6 acquisition comparison."""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal, cast

from pydantic import Field, StrictBool, StrictFloat, StrictInt, model_validator

from ecomsre.dta_v2.v22.read_contracts import DtaModelV22, semantic_sha256_v22
from ecomsre.dta_v2.v22.real_fault_stage_trace_v226 import RealFaultRunTraceV226


class RealFaultStudyArmV226(str, Enum):
    MODEL_DIRECTED_RETRIEVAL = "MODEL_DIRECTED_RETRIEVAL"
    CURRENT_RUNTIME_BUNDLE = "CURRENT_RUNTIME_BUNDLE"


class RealFaultArmStatusV226(str, Enum):
    VALID_TERMINAL = "VALID_TERMINAL"
    PROTOCOL_FAILED = "PROTOCOL_FAILED"
    TRANSPORT_FAILED = "TRANSPORT_FAILED"
    RUNNER_FAILED = "RUNNER_FAILED"


class RealFaultPredictionV226(DtaModelV22):
    schema_version: Literal["dta-v226-real-fault.prediction.v1"]
    terminal: Literal["DIAGNOSED", "NO_INCIDENT", "ABSTAIN", "FAILED"]
    terminal_id: str | None
    root_service_alias: str | None = Field(
        default=None, pattern=r"^svc-[0-9a-f]{10}$"
    )
    fault_domain: Literal["LOCAL_RESOURCE"] | None
    mechanism: Literal["CPU_SATURATION"] | None
    supporting_evidence_refs: tuple[str, ...]
    evidence_clause_valid: StrictBool

    @model_validator(mode="after")
    def require_prediction(self) -> RealFaultPredictionV226:
        if self.supporting_evidence_refs != tuple(
            sorted(set(self.supporting_evidence_refs))
        ):
            raise ValueError("v2.2.6 prediction refs are not canonical")
        if self.terminal == "DIAGNOSED":
            if (
                self.terminal_id is None
                or self.root_service_alias is None
                or self.fault_domain != "LOCAL_RESOURCE"
                or self.mechanism != "CPU_SATURATION"
                or not self.supporting_evidence_refs
                or not self.evidence_clause_valid
            ):
                raise ValueError("v2.2.6 Diagnosis is incomplete")
        elif self.terminal == "NO_INCIDENT":
            if (
                self.terminal_id is None
                or self.root_service_alias is not None
                or self.fault_domain is not None
                or self.mechanism is not None
                or not self.supporting_evidence_refs
                or not self.evidence_clause_valid
            ):
                raise ValueError("v2.2.6 No-Incident is not evidence-closed")
        elif self.terminal == "ABSTAIN":
            if (
                self.terminal_id is None
                or self.root_service_alias is not None
                or self.fault_domain is not None
                or self.mechanism is not None
                or self.supporting_evidence_refs
                or self.evidence_clause_valid
            ):
                raise ValueError("v2.2.6 Abstain carries a fault claim")
        elif any(
            item is not None
            for item in (
                self.terminal_id,
                self.root_service_alias,
                self.fault_domain,
                self.mechanism,
            )
        ) or self.supporting_evidence_refs or self.evidence_clause_valid:
            raise ValueError("v2.2.6 failed prediction carries a terminal")
        return self


class RealFaultArmRunV226(DtaModelV22):
    schema_version: Literal["dta-v226-real-fault.arm-run.v1"]
    case_id: str = Field(pattern=r"^(?:fault|baseline)-map-[ab]$")
    arm: RealFaultStudyArmV226
    case_bytes_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    model_id: str = Field(min_length=1, max_length=128)
    status: RealFaultArmStatusV226
    prediction: RealFaultPredictionV226
    trace: RealFaultRunTraceV226
    strictly_ambiguous: StrictBool | None
    comparison_set_size: StrictInt = Field(ge=0, le=4)
    bundle_eligible: StrictBool
    bundle_dispatched: StrictBool
    bundle_target_count: StrictInt = Field(ge=0, le=4)
    first_useful_evidence_ordinal: StrictInt | None = Field(default=None, ge=1, le=4)
    resources_selected: StrictBool
    resource_read_shape: Literal["NONE", "SINGLE_TARGET", "MULTI_TARGET"]
    all_candidates_covered: StrictBool
    semantic_evidence_actions: StrictInt = Field(ge=0, le=4)
    target_equivalent_reads: StrictInt = Field(ge=0, le=4)
    shared_capture_reads_charged_to_arm: Literal[0]
    predicate_yield_count: StrictInt = Field(ge=0, le=20)
    duplicate_read_attempts: StrictInt = Field(ge=0, le=4)
    empty_read_count: StrictInt = Field(ge=0, le=4)
    provider_turns: StrictInt = Field(ge=0, le=5)
    provider_calls: StrictInt = Field(ge=0, le=20)
    first_pass_protocol_success: StrictBool
    post_repair_protocol_success: StrictBool
    protocol_repairs: StrictInt = Field(ge=0, le=10)
    input_tokens: StrictInt = Field(ge=0)
    output_tokens: StrictInt = Field(ge=0)
    total_tokens: StrictInt = Field(ge=0)
    latency_ms: StrictFloat = Field(ge=0)
    transport_retries: StrictInt = Field(ge=0, le=3)
    protocol_failures: StrictInt = Field(ge=0, le=1)
    runner_failures: StrictInt = Field(ge=0, le=1)
    transport_failures: StrictInt = Field(ge=0, le=1)
    agent_writes: Literal[0]
    action_proposals: Literal[0]
    runbook_executions: Literal[0]
    run_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_run(self) -> RealFaultArmRunV226:
        if self.trace.arm != self.arm.value:
            raise ValueError("arm-run trace belongs to another arm")
        if self.total_tokens != self.input_tokens + self.output_tokens:
            raise ValueError("arm-run token accounting differs")
        if self.semantic_evidence_actions > self.target_equivalent_reads:
            raise ValueError("semantic actions exceed target-equivalent reads")
        if self.resources_selected != (self.resource_read_shape != "NONE"):
            raise ValueError("Resources selection and shape differ")
        if self.bundle_dispatched and (
            self.arm is not RealFaultStudyArmV226.CURRENT_RUNTIME_BUNDLE
            or not self.bundle_eligible
            or self.resource_read_shape != "MULTI_TARGET"
            or self.bundle_target_count < 2
        ):
            raise ValueError("bundle dispatch accounting differs")
        if self.status is RealFaultArmStatusV226.VALID_TERMINAL:
            if (
                self.prediction.terminal == "FAILED"
                or self.trace.failure_stage is not None
                or self.protocol_failures
                or self.runner_failures
                or self.transport_failures
            ):
                raise ValueError("valid arm-run carries a failure")
        elif self.prediction.terminal != "FAILED" or self.trace.failure_stage is None:
            raise ValueError("failed arm-run lacks failed prediction or trace")
        expected_counts = {
            RealFaultArmStatusV226.VALID_TERMINAL: (0, 0, 0),
            RealFaultArmStatusV226.PROTOCOL_FAILED: (1, 0, 0),
            RealFaultArmStatusV226.TRANSPORT_FAILED: (0, 0, 1),
            RealFaultArmStatusV226.RUNNER_FAILED: (0, 1, 0),
        }[self.status]
        if (
            self.protocol_failures,
            self.runner_failures,
            self.transport_failures,
        ) != expected_counts:
            raise ValueError("arm-run failure counters differ from status")
        if self.run_sha256 != self.recompute_sha256():
            raise ValueError("v2.2.6 arm-run digest differs")
        return self

    def recompute_sha256(self) -> str:
        return semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"run_sha256"})
        )


def build_real_fault_arm_run_v226(**values: object) -> RealFaultArmRunV226:
    payload = {
        "schema_version": "dta-v226-real-fault.arm-run.v1",
        "shared_capture_reads_charged_to_arm": 0,
        "agent_writes": 0,
        "action_proposals": 0,
        "runbook_executions": 0,
        **values,
    }
    draft = cast(Any, RealFaultArmRunV226).model_construct(
        **payload, run_sha256="0" * 64
    )
    return RealFaultArmRunV226.model_validate(
        {
            **payload,
            "run_sha256": semantic_sha256_v22(
                draft.model_dump(mode="json", exclude={"run_sha256"})
            ),
        }
    )


__all__ = (
    "RealFaultArmRunV226",
    "RealFaultArmStatusV226",
    "RealFaultPredictionV226",
    "RealFaultStudyArmV226",
    "build_real_fault_arm_run_v226",
)
