"""Typed, safe stage traces for DTA v2.2.6 real-fault arm runs."""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal, TypeAlias, cast

from pydantic import Field, StrictInt, model_validator

from ecomsre.dta_v2.v22.read_contracts import DtaModelV22, semantic_sha256_v22


RealFaultArmV226: TypeAlias = Literal[
    "MODEL_DIRECTED_RETRIEVAL", "CURRENT_RUNTIME_BUNDLE"
]


class RealFaultStageV226(str, Enum):
    INPUT_VALIDATION = "INPUT_VALIDATION"
    BOOTSTRAP_ACTION_BUILD = "BOOTSTRAP_ACTION_BUILD"
    BOOTSTRAP_DISPATCH = "BOOTSTRAP_DISPATCH"
    BOOTSTRAP_MEMORY_BUILD = "BOOTSTRAP_MEMORY_BUILD"
    BASELINE_PROFILE_BUILD = "BASELINE_PROFILE_BUILD"
    HYPOTHESIS_CATALOG_BUILD = "HYPOTHESIS_CATALOG_BUILD"
    GAP_GRAPH_BUILD = "GAP_GRAPH_BUILD"
    ACTION_CATALOG_BUILD = "ACTION_CATALOG_BUILD"
    RESOURCE_COMPARISON_SET_BUILD = "RESOURCE_COMPARISON_SET_BUILD"
    BUNDLE_BUILD = "BUNDLE_BUILD"
    BUNDLE_DISPATCH = "BUNDLE_DISPATCH"
    POST_READ_MEMORY_BUILD = "POST_READ_MEMORY_BUILD"
    POST_READ_GAP_BUILD = "POST_READ_GAP_BUILD"
    TERMINAL_CATALOG_BUILD = "TERMINAL_CATALOG_BUILD"
    PROVIDER_TERMINAL_SELECTION = "PROVIDER_TERMINAL_SELECTION"
    TERMINAL_BIND = "TERMINAL_BIND"
    BOOTSTRAP_BUILD = "BOOTSTRAP_BUILD"
    ACTION_SURFACE_BUILD = "ACTION_SURFACE_BUILD"
    PROVIDER_ACTION_SELECTION = "PROVIDER_ACTION_SELECTION"
    ACTION_BIND = "ACTION_BIND"
    READ_DISPATCH = "READ_DISPATCH"
    OBSERVATION_BIND = "OBSERVATION_BIND"
    MEMORY_BUILD = "MEMORY_BUILD"
    COMPLETE = "COMPLETE"


class RealFaultSafeFailureCodeV226(str, Enum):
    INPUT_INVALID = "INPUT_INVALID"
    BOOTSTRAP_ACTION_MISSING = "BOOTSTRAP_ACTION_MISSING"
    BOOTSTRAP_READ_FAILED = "BOOTSTRAP_READ_FAILED"
    MEMORY_CONSTRUCTION_FAILED = "MEMORY_CONSTRUCTION_FAILED"
    BASELINE_PROFILE_INVALID = "BASELINE_PROFILE_INVALID"
    GAP_GRAPH_CONSTRUCTION_FAILED = "GAP_GRAPH_CONSTRUCTION_FAILED"
    ACTION_CATALOG_EMPTY = "ACTION_CATALOG_EMPTY"
    RESOURCE_COMPARISON_SET_EMPTY = "RESOURCE_COMPARISON_SET_EMPTY"
    BUNDLE_NOT_ELIGIBLE = "BUNDLE_NOT_ELIGIBLE"
    BUNDLE_REQUEST_CONVERSION_FAILED = "BUNDLE_REQUEST_CONVERSION_FAILED"
    BUNDLE_READ_FAILED = "BUNDLE_READ_FAILED"
    OBSERVATION_CONVERSION_FAILED = "OBSERVATION_CONVERSION_FAILED"
    TERMINAL_CATALOG_EMPTY = "TERMINAL_CATALOG_EMPTY"
    PROVIDER_OUTPUT_INVALID = "PROVIDER_OUTPUT_INVALID"
    PROVIDER_TRANSPORT_FAILED = "PROVIDER_TRANSPORT_FAILED"
    TERMINAL_ALIAS_INVALID = "TERMINAL_ALIAS_INVALID"
    EVIDENCE_CLAUSE_NOT_ADMITTED = "EVIDENCE_CLAUSE_NOT_ADMITTED"
    BUDGET_EXHAUSTED_WITHOUT_TERMINAL = "BUDGET_EXHAUSTED_WITHOUT_TERMINAL"
    INTERNAL_CONTRACT_FAILURE = "INTERNAL_CONTRACT_FAILURE"


class RealFaultStageEventV226(DtaModelV22):
    schema_version: Literal["dta-v226-real-fault.stage-event.v1"]
    ordinal: StrictInt = Field(ge=1, le=64)
    stage: RealFaultStageV226
    outcome: Literal["COMPLETED", "FAILED"]
    safe_error_code: RealFaultSafeFailureCodeV226 | None = None

    @model_validator(mode="after")
    def require_event_identity(self) -> RealFaultStageEventV226:
        if (self.outcome == "FAILED") != (self.safe_error_code is not None):
            raise ValueError("stage event failure identity differs")
        return self


class RealFaultRunTraceV226(DtaModelV22):
    schema_version: Literal["dta-v226-real-fault.run-trace.v1"]
    arm: RealFaultArmV226
    last_completed_stage: RealFaultStageV226 | None
    failure_stage: RealFaultStageV226 | None
    safe_error_code: RealFaultSafeFailureCodeV226 | None
    local_exception_class: str | None = Field(
        default=None, min_length=1, max_length=128, pattern=r"^[A-Za-z][A-Za-z0-9_.]*$"
    )
    safe_validation_codes: tuple[str, ...] = Field(max_length=16)
    stage_events: tuple[RealFaultStageEventV226, ...] = Field(
        min_length=1, max_length=64
    )
    trace_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_trace_identity(self) -> RealFaultRunTraceV226:
        if tuple(event.ordinal for event in self.stage_events) != tuple(
            range(1, len(self.stage_events) + 1)
        ):
            raise ValueError("stage event ordinals are not contiguous")
        failed = tuple(event for event in self.stage_events if event.outcome == "FAILED")
        if self.failure_stage is None:
            if self.safe_error_code is not None or failed:
                raise ValueError("successful trace carries failure identity")
            if self.last_completed_stage is not RealFaultStageV226.COMPLETE:
                raise ValueError("successful trace did not reach COMPLETE")
        else:
            if self.safe_error_code is None or len(failed) != 1:
                raise ValueError("failed trace lacks one safe failure identity")
            if failed[0].stage is not self.failure_stage:
                raise ValueError("failed event stage differs from trace")
            if failed[0].safe_error_code is not self.safe_error_code:
                raise ValueError("failed event code differs from trace")
            completed = tuple(
                event.stage for event in self.stage_events if event.outcome == "COMPLETED"
            )
            if self.failure_stage in completed:
                raise ValueError("failure stage was already completed")
        expected_last = next(
            (
                event.stage
                for event in reversed(self.stage_events)
                if event.outcome == "COMPLETED"
            ),
            None,
        )
        if self.last_completed_stage is not expected_last:
            raise ValueError("last completed stage differs from events")
        if self.safe_validation_codes != tuple(sorted(set(self.safe_validation_codes))):
            raise ValueError("safe validation codes are not canonical")
        if self.trace_sha256 != self.recompute_sha256():
            raise ValueError("real-fault stage trace digest differs")
        return self

    def recompute_sha256(self) -> str:
        return semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"trace_sha256"})
        )


def _build_trace_v226(**payload: object) -> RealFaultRunTraceV226:
    draft = cast(Any, RealFaultRunTraceV226).model_construct(
        schema_version="dta-v226-real-fault.run-trace.v1",
        **payload,
        trace_sha256="0" * 64,
    )
    return RealFaultRunTraceV226.model_validate(
        {
            "schema_version": "dta-v226-real-fault.run-trace.v1",
            **payload,
            "trace_sha256": draft.recompute_sha256(),
        }
    )


def build_failed_real_fault_trace_v226(
    *,
    arm: RealFaultArmV226,
    completed_stages: tuple[RealFaultStageV226, ...],
    failure_stage: RealFaultStageV226,
    safe_error_code: RealFaultSafeFailureCodeV226,
    local_exception_class: str | None = None,
    safe_validation_codes: tuple[str, ...] = (),
) -> RealFaultRunTraceV226:
    events = tuple(
        RealFaultStageEventV226(
            schema_version="dta-v226-real-fault.stage-event.v1",
            ordinal=ordinal,
            stage=stage,
            outcome="COMPLETED",
            safe_error_code=None,
        )
        for ordinal, stage in enumerate(completed_stages, start=1)
    ) + (
        RealFaultStageEventV226(
            schema_version="dta-v226-real-fault.stage-event.v1",
            ordinal=len(completed_stages) + 1,
            stage=failure_stage,
            outcome="FAILED",
            safe_error_code=safe_error_code,
        ),
    )
    return _build_trace_v226(
        arm=arm,
        last_completed_stage=completed_stages[-1] if completed_stages else None,
        failure_stage=failure_stage,
        safe_error_code=safe_error_code,
        local_exception_class=local_exception_class,
        safe_validation_codes=tuple(sorted(set(safe_validation_codes))),
        stage_events=events,
    )


def build_successful_real_fault_trace_v226(
    *,
    arm: RealFaultArmV226,
    completed_stages: tuple[RealFaultStageV226, ...],
) -> RealFaultRunTraceV226:
    events = tuple(
        RealFaultStageEventV226(
            schema_version="dta-v226-real-fault.stage-event.v1",
            ordinal=ordinal,
            stage=stage,
            outcome="COMPLETED",
            safe_error_code=None,
        )
        for ordinal, stage in enumerate(completed_stages, start=1)
    )
    return _build_trace_v226(
        arm=arm,
        last_completed_stage=completed_stages[-1] if completed_stages else None,
        failure_stage=None,
        safe_error_code=None,
        local_exception_class=None,
        safe_validation_codes=(),
        stage_events=events,
    )


__all__ = (
    "RealFaultArmV226",
    "RealFaultRunTraceV226",
    "RealFaultSafeFailureCodeV226",
    "RealFaultStageEventV226",
    "RealFaultStageV226",
    "build_failed_real_fault_trace_v226",
    "build_successful_real_fault_trace_v226",
)
