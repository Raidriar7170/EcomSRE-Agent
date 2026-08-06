"""Strict public and evaluator-only contracts for RCAEval RE2."""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictFloat,
    StrictInt,
    model_validator,
)


class RCAEvalModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class Architecture(str, Enum):
    SINGLE = "single"
    FIXED = "fixed"
    DYNAMIC = "dynamic"


class TerminalStatus(str, Enum):
    COMPLETED = "COMPLETED"
    PROVIDER_FAILURE = "PROVIDER_FAILURE"
    PROTOCOL_VIOLATION = "PROTOCOL_VIOLATION"
    TIMEOUT = "TIMEOUT"
    INVALID_SCHEMA = "INVALID_SCHEMA"
    WORKFLOW_FAILURE = "WORKFLOW_FAILURE"
    EMPTY_DIAGNOSIS = "EMPTY_DIAGNOSIS"
    UNRESOLVED_ALIAS = "UNRESOLVED_ALIAS"


CanonicalIndicator = Literal["cpu", "mem", "diskio", "latency", "socket"]
FaultName = Literal["cpu", "mem", "disk", "delay", "loss", "socket"]


class Diagnosis(RCAEvalModel):
    schema_version: Literal["rcaeval-re2.diagnosis.v1"] = (
        "rcaeval-re2.diagnosis.v1"
    )
    root_cause_service: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,127}$")
    root_cause_indicator: CanonicalIndicator
    confidence: StrictFloat | None = Field(default=None, ge=0.0, le=1.0)
    evidence_refs: tuple[str, ...] = Field(min_length=1, max_length=64)
    explanation: str = Field(min_length=1, max_length=2_000)

    @model_validator(mode="after")
    def require_run_scoped_evidence(self) -> Diagnosis:
        if len(set(self.evidence_refs)) != len(self.evidence_refs):
            raise ValueError("diagnosis evidence references must be unique")
        for evidence_ref in self.evidence_refs:
            prefix, separator, sequence = evidence_ref.partition(":")
            if (
                separator != ":"
                or prefix not in {"metric", "log", "trace"}
                or len(sequence) != 4
                or not sequence.isdigit()
            ):
                raise ValueError("invalid RCAEval evidence reference")
        return self


class SpecialistAssessment(RCAEvalModel):
    schema_version: Literal["rcaeval-re2.specialist-assessment.v1"] = (
        "rcaeval-re2.specialist-assessment.v1"
    )
    source: Literal["metrics", "logs", "traces"]
    observation_status: Literal["AVAILABLE", "SOURCE_UNAVAILABLE"]
    candidate_service: str | None = Field(
        default=None,
        pattern=r"^[a-z0-9][a-z0-9-]{0,127}$",
    )
    candidate_indicator: CanonicalIndicator | None = None
    confidence: StrictFloat = Field(ge=0.0, le=1.0)
    evidence_refs: tuple[str, ...] = Field(max_length=64)
    summary: str = Field(min_length=1, max_length=2_000)

    @model_validator(mode="after")
    def require_source_disposition(self) -> SpecialistAssessment:
        if len(self.evidence_refs) != len(set(self.evidence_refs)):
            raise ValueError("specialist evidence references must be unique")
        expected_prefix = {
            "metrics": "metric:",
            "logs": "log:",
            "traces": "trace:",
        }[self.source]
        if any(
            not item.startswith(expected_prefix)
            or len(item) != len(expected_prefix) + 4
            or not item.removeprefix(expected_prefix).isdigit()
            for item in self.evidence_refs
        ):
            raise ValueError("specialist evidence reference differs from source")
        if self.observation_status == "SOURCE_UNAVAILABLE" and (
            self.candidate_service is not None
            or self.candidate_indicator is not None
            or self.evidence_refs
            or self.confidence != 0.0
        ):
            raise ValueError("unavailable specialist source cannot make evidence claims")
        return self


class CommanderDecision(RCAEvalModel):
    schema_version: Literal["rcaeval-re2.commander-decision.v1"] = (
        "rcaeval-re2.commander-decision.v1"
    )
    selected_sources: tuple[Literal["logs", "traces"], ...] = Field(
        min_length=1,
        max_length=2,
    )
    rationale: str = Field(min_length=1, max_length=1_000)

    @model_validator(mode="after")
    def require_unique_sources(self) -> CommanderDecision:
        if len(self.selected_sources) != len(set(self.selected_sources)):
            raise ValueError("commander selected duplicate sources")
        return self


class TerminalRecord(RCAEvalModel):
    schema_version: Literal["rcaeval-re2.terminal-record.v1"] = (
        "rcaeval-re2.terminal-record.v1"
    )
    run_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    case_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,127}$")
    architecture: Architecture
    terminal_status: TerminalStatus
    diagnosis: Diagnosis | None = None
    failure_code: str | None = Field(default=None, min_length=1, max_length=128)
    tool_calls: StrictInt = Field(ge=0)
    model_calls: StrictInt = Field(ge=0)
    known_provider_tokens: StrictInt | None = Field(default=None, ge=0)
    latency_seconds: StrictFloat = Field(ge=0.0)

    @model_validator(mode="after")
    def require_terminal_disposition(self) -> TerminalRecord:
        if self.terminal_status is TerminalStatus.COMPLETED:
            if self.diagnosis is None or self.failure_code is not None:
                raise ValueError("completed run requires one diagnosis")
        elif self.diagnosis is not None or self.failure_code is None:
            raise ValueError("failed run requires one failure code and no diagnosis")
        return self


class GroundTruth(RCAEvalModel):
    """Evaluator-only case mapping. Never expose this model to Agent runtime."""

    schema_version: Literal["rcaeval-re2.ground-truth.v1"] = (
        "rcaeval-re2.ground-truth.v1"
    )
    case_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,127}$")
    root_cause_service: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,127}$")
    fault: FaultName
    instance: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}$")


class ScheduledRun(RCAEvalModel):
    schema_version: Literal["rcaeval-re2.scheduled-run.v1"] = (
        "rcaeval-re2.scheduled-run.v1"
    )
    run_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    case_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,127}$")
    architecture: Architecture
    call_position: StrictInt = Field(ge=1, le=3)
    schedule_seed: StrictInt
