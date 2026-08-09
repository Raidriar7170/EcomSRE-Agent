"""Strict, serialization-safe contracts for RCAEval RE2 v2 development runs."""

from __future__ import annotations

from enum import Enum
import hashlib
import json
from typing import Annotated, Literal

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictFloat,
    StrictInt,
    model_validator,
)


Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
RunId = Annotated[str, Field(pattern=r"^[0-9a-f]{32}$")]
CaseId = Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9-]{0,127}$")]
ServiceName = Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9-]{0,127}$")]
MetricServiceName = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9-]{0,127}$")]
SourceName = Literal["metrics", "logs", "traces"]
DevSystem = Literal["RE2-OB", "RE2-SS"]
ArchitectureV2 = Literal["single_v2", "fixed_v2", "dynamic_v2"]
CanonicalIndicator = Literal["cpu", "mem", "diskio", "latency", "socket"]


_FORBIDDEN_TEXT = (
    "authorization",
    "bearer ",
    "api_key",
    "openai_api_key",
    "raw http response",
    "raw function-call text",
    "raw function call text",
)


def _assert_serialization_safe(value: object) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = str(key).casefold()
            if normalized in {
                "headers",
                "authorization",
                "api_key",
                "provider_base_url",
                "environment",
                "env",
                "raw_response",
                "raw_function_call",
            }:
                raise ValueError("v2 contract is not serialization-safe")
            _assert_serialization_safe(item)
        return
    if isinstance(value, (list, tuple)):
        for item in value:
            _assert_serialization_safe(item)
        return
    if isinstance(value, str):
        normalized = value.casefold()
        if any(marker in normalized for marker in _FORBIDDEN_TEXT):
            raise ValueError("v2 contract is not serialization-safe")
        from ecomsre_rcaeval_v2.privacy import scan_agent_visible_payload

        if scan_agent_visible_payload(value).path_hit_count:
            raise ValueError("v2 contract is not serialization-safe")


class V2Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    @model_validator(mode="after")
    def reject_sensitive_serialization(self) -> V2Model:
        _assert_serialization_safe(self.model_dump(mode="python"))
        return self


class OperationType(str, Enum):
    METRICS_SPECIALIST = "METRICS_SPECIALIST"
    LOGS_SPECIALIST = "LOGS_SPECIALIST"
    TRACES_SPECIALIST = "TRACES_SPECIALIST"
    COMMANDER = "COMMANDER"
    FINAL_JUDGE = "FINAL_JUDGE"
    INDICATOR_RESOLVER = "INDICATOR_RESOLVER"


class OperationStatus(str, Enum):
    COMPLETED = "COMPLETED"
    PROVIDER_FAILURE = "PROVIDER_FAILURE"
    INVALID_SCHEMA = "INVALID_SCHEMA"
    TIMEOUT = "TIMEOUT"
    PROTOCOL_VIOLATION = "PROTOCOL_VIOLATION"
    NOT_EXECUTED = "NOT_EXECUTED"


class OperationStage(str, Enum):
    INPUT_SANITIZATION = "INPUT_SANITIZATION"
    INPUT_CONSTRUCTION = "INPUT_CONSTRUCTION"
    INPUT_PERSISTENCE = "INPUT_PERSISTENCE"
    PROVIDER_CALL = "PROVIDER_CALL"
    OUTPUT_VALIDATION = "OUTPUT_VALIDATION"
    OUTPUT_PERSISTENCE = "OUTPUT_PERSISTENCE"
    COMPLETED = "COMPLETED"


class OperationFailureCode(str, Enum):
    PROVIDER_TRANSPORT_FAILURE = "PROVIDER_TRANSPORT_FAILURE"
    PROVIDER_TIMEOUT = "PROVIDER_TIMEOUT"
    PROVIDER_OUTPUT_INVALID_SCHEMA = "PROVIDER_OUTPUT_INVALID_SCHEMA"
    PROVIDER_OUTPUT_UNRESOLVED_SERVICE_ALIAS = (
        "PROVIDER_OUTPUT_UNRESOLVED_SERVICE_ALIAS"
    )
    PROVIDER_PROTOCOL_VIOLATION = "PROVIDER_PROTOCOL_VIOLATION"
    RUNTIME_CONTRACT_VIOLATION = "RUNTIME_CONTRACT_VIOLATION"
    OPERATION_NOT_EXECUTED = "OPERATION_NOT_EXECUTED"
    STARTED_ATTEMPT_WITHOUT_TERMINAL = "STARTED_ATTEMPT_WITHOUT_TERMINAL"
    STARTED_OPERATION_WITHOUT_TERMINAL = "STARTED_OPERATION_WITHOUT_TERMINAL"
    AGENT_VISIBLE_PRIVATE_PATH_REMAINED = "AGENT_VISIBLE_PRIVATE_PATH_REMAINED"
    NO_INDICATOR_CANDIDATE = "NO_INDICATOR_CANDIDATE"


class ProviderUsageDelta(V2Model):
    model_calls_delta: StrictInt = Field(ge=0)
    prompt_tokens_delta: StrictInt = Field(ge=0)
    completion_tokens_delta: StrictInt = Field(ge=0)
    total_tokens_delta: StrictInt = Field(ge=0)
    token_usage_known: StrictBool = True

    @model_validator(mode="after")
    def require_exact_total(self) -> ProviderUsageDelta:
        if not self.token_usage_known and any(
            (
                self.prompt_tokens_delta,
                self.completion_tokens_delta,
                self.total_tokens_delta,
            )
        ):
            raise ValueError("unknown provider usage cannot claim token deltas")
        if self.token_usage_known and self.total_tokens_delta != (
            self.prompt_tokens_delta + self.completion_tokens_delta
        ):
            raise ValueError("provider usage total differs from token deltas")
        return self


class SafeValidationError(V2Model):
    error_class: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_.]{0,127}$")
    field_paths: tuple[str, ...] = Field(max_length=64)
    constraint_types: tuple[str, ...] = Field(min_length=1, max_length=64)
    error_count: StrictInt = Field(ge=1, le=64)

    @model_validator(mode="after")
    def require_safe_bounded_diagnostics(self) -> SafeValidationError:
        if self.error_count < max(len(self.field_paths), len(self.constraint_types)):
            raise ValueError("safe validation diagnostics count is inconsistent")
        return self


class SpecialistAssessmentV2(V2Model):
    source: SourceName
    candidate_service: ServiceName | None
    candidate_indicator: CanonicalIndicator | None
    confidence: StrictFloat = Field(ge=0.0, le=1.0)
    supporting_evidence_refs: tuple[str, ...] = Field(max_length=64)
    contradicting_evidence_refs: tuple[str, ...] = Field(max_length=64)
    summary: str = Field(min_length=1, max_length=2_000)


class CommanderDecisionV2(V2Model):
    selected_sources: tuple[Literal["logs", "traces"], ...] = Field(
        min_length=1, max_length=2
    )
    rationale: str = Field(min_length=1, max_length=1_000)

    @model_validator(mode="after")
    def require_unique_sources(self) -> CommanderDecisionV2:
        if len(self.selected_sources) != len(set(self.selected_sources)):
            raise ValueError("commander selected duplicate sources")
        return self


class IncidentSnapshotV2(V2Model):
    incident_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,127}$")
    system: DevSystem
    anomaly_timestamp: StrictInt = Field(ge=0)
    modalities: tuple[SourceName, ...] = Field(min_length=2, max_length=3)
    summary: str = Field(min_length=1, max_length=2_000)


class SourceObservationSnapshotV2(V2Model):
    source: SourceName
    status: Literal["AVAILABLE", "SOURCE_UNAVAILABLE"]
    summary: str = Field(min_length=1, max_length=2_000)
    evidence_refs: tuple[str, ...] = Field(max_length=64)


class BoundedEvidenceSnapshotV2(V2Model):
    evidence_ref: str = Field(min_length=1, max_length=128)
    source: SourceName
    service: MetricServiceName
    observation: str = Field(min_length=1, max_length=2_000)


class IndicatorCandidateSnapshotV2(V2Model):
    service: MetricServiceName
    canonical_indicator: CanonicalIndicator
    metric_name: str = Field(min_length=1, max_length=256)
    score: StrictFloat
    evidence_ref: str = Field(min_length=1, max_length=128)


class JudgeInputSnapshotV2(V2Model):
    incident: IncidentSnapshotV2
    source_observations: tuple[SourceObservationSnapshotV2, ...]
    bounded_evidence: tuple[BoundedEvidenceSnapshotV2, ...]
    specialist_assessments: tuple[SpecialistAssessmentV2, ...]
    commander_decision: CommanderDecisionV2 | None
    indicator_candidates: tuple[IndicatorCandidateSnapshotV2, ...]


class SpecialistInputSnapshotV2(V2Model):
    incident: IncidentSnapshotV2
    architecture: ArchitectureV2
    source: SourceName
    source_observation: SourceObservationSnapshotV2
    bounded_evidence: tuple[BoundedEvidenceSnapshotV2, ...]

    @model_validator(mode="after")
    def require_source_isolation(self) -> SpecialistInputSnapshotV2:
        if self.source_observation.source != self.source or any(
            item.source != self.source for item in self.bounded_evidence
        ):
            raise ValueError("specialist input is not source isolated")
        return self


class CommanderInputSnapshotV2(V2Model):
    incident: IncidentSnapshotV2
    metrics_assessment: SpecialistAssessmentV2

    @model_validator(mode="after")
    def require_metrics_assessment(self) -> CommanderInputSnapshotV2:
        if self.metrics_assessment.source != "metrics":
            raise ValueError("commander input requires the Metrics assessment")
        return self


class ResolverInputSnapshotV2(V2Model):
    selected_service: ServiceName
    indicator_candidates: tuple[IndicatorCandidateSnapshotV2, ...]


class JudgeServiceDecisionV2(V2Model):
    root_cause_service: ServiceName
    model_proposed_indicator: CanonicalIndicator | None
    confidence: StrictFloat = Field(ge=0.0, le=1.0)
    evidence_refs: tuple[str, ...] = Field(min_length=1, max_length=64)
    explanation: str = Field(min_length=1, max_length=2_000)


class IndicatorResolutionV2(V2Model):
    selected_service: ServiceName
    disposition: Literal["RESOLVED", "NO_INDICATOR_CANDIDATE"]
    resolved_indicator: CanonicalIndicator | None
    selected_metric: str | None
    evidence_ref: str | None

    @model_validator(mode="after")
    def require_resolution_disposition(self) -> IndicatorResolutionV2:
        resolved = (
            self.resolved_indicator,
            self.selected_metric,
            self.evidence_ref,
        )
        if self.disposition == "RESOLVED" and any(item is None for item in resolved):
            raise ValueError("resolved indicator requires metric and evidence")
        if self.disposition == "NO_INDICATOR_CANDIDATE" and any(
            item is not None for item in resolved
        ):
            raise ValueError("missing indicator candidate cannot claim a resolution")
        return self


class DiagnosisV2(V2Model):
    root_cause_service: ServiceName
    model_proposed_indicator: CanonicalIndicator | None
    resolved_indicator: CanonicalIndicator | None
    indicator_disposition: Literal["RESOLVED", "NO_INDICATOR_CANDIDATE"]
    judge_evidence_refs: tuple[str, ...] = Field(min_length=1, max_length=64)
    indicator_evidence_ref: str | None = Field(
        default=None, pattern=r"^indicator:[0-9]{4}$"
    )
    confidence: StrictFloat = Field(ge=0.0, le=1.0)
    explanation: str = Field(min_length=1, max_length=2_000)

    @model_validator(mode="after")
    def require_resolved_indicator_disposition(self) -> DiagnosisV2:
        if len(self.judge_evidence_refs) != len(set(self.judge_evidence_refs)):
            raise ValueError("Judge evidence references must be unique")
        if any(
            not reference.startswith(("metric:", "log:", "trace:"))
            for reference in self.judge_evidence_refs
        ):
            raise ValueError("Judge evidence reference has an invalid source")
        if self.indicator_disposition == "RESOLVED":
            if self.resolved_indicator is None or self.indicator_evidence_ref is None:
                raise ValueError("resolved diagnosis requires indicator evidence")
        elif (
            self.resolved_indicator is not None
            or self.indicator_evidence_ref is not None
        ):
            raise ValueError("missing indicator candidate cannot claim resolution")
        return self


class _OperationRecord(V2Model):
    schema_version: Literal["rcaeval-re2-v2.operation-record.v1"]
    run_id: RunId
    case_id: CaseId
    system: DevSystem
    architecture: ArchitectureV2
    operation_index: StrictInt = Field(ge=1)
    operation_type: OperationType
    source: SourceName | None
    started_at_utc: AwareDatetime
    ended_at_utc: AwareDatetime
    latency_ms: StrictFloat = Field(ge=0.0)
    status: OperationStatus
    failure_code: OperationFailureCode | None
    failure_stage: OperationStage | None
    last_completed_stage: OperationStage | None
    stage_trace_sha256: Sha256
    safe_validation_error: SafeValidationError | None
    provider_call_index: StrictInt | None = Field(ge=1)
    input_snapshot_sha256: Sha256 | None
    output_snapshot_sha256: Sha256 | None
    usage_delta: ProviderUsageDelta
    investigated_sources: tuple[SourceName, ...] = Field(max_length=3)
    evidence_refs_visible_to_operation: tuple[str, ...] = Field(max_length=256)
    selected_sources: tuple[Literal["logs", "traces"], ...] = Field(max_length=2)

    @model_validator(mode="after")
    def require_operation_consistency(self) -> _OperationRecord:
        if self.ended_at_utc < self.started_at_utc:
            raise ValueError("operation ended before it started")
        if self.status is OperationStatus.COMPLETED:
            if (
                self.failure_code is not None
                or self.failure_stage is not None
                or self.safe_validation_error is not None
                or self.input_snapshot_sha256 is None
                or self.output_snapshot_sha256 is None
                or self.last_completed_stage is not OperationStage.OUTPUT_PERSISTENCE
            ):
                raise ValueError("completed operation requires output and no failure")
        elif (
            self.failure_code is None
            or self.failure_stage is None
            or self.output_snapshot_sha256 is not None
            or self.last_completed_stage is OperationStage.COMPLETED
        ):
            raise ValueError("failed operation requires failure and no output")
        if self.failure_stage in {
            OperationStage.INPUT_SANITIZATION,
            OperationStage.INPUT_CONSTRUCTION,
            OperationStage.INPUT_PERSISTENCE,
        } and (
            self.provider_call_index is not None
            or self.usage_delta.model_calls_delta != 0
            or self.usage_delta.prompt_tokens_delta != 0
            or self.usage_delta.completion_tokens_delta != 0
            or self.usage_delta.total_tokens_delta != 0
        ):
            raise ValueError("pre-provider failure cannot claim Provider usage")
        if self.safe_validation_error is not None and self.failure_stage not in {
            OperationStage.INPUT_SANITIZATION,
            OperationStage.INPUT_CONSTRUCTION,
            OperationStage.OUTPUT_VALIDATION,
        }:
            raise ValueError("safe validation diagnostics have an invalid stage")
        if self.operation_type is OperationType.INDICATOR_RESOLVER:
            if self.provider_call_index is not None or any(
                (
                    self.usage_delta.model_calls_delta,
                    self.usage_delta.prompt_tokens_delta,
                    self.usage_delta.completion_tokens_delta,
                    self.usage_delta.total_tokens_delta,
                )
            ):
                raise ValueError("deterministic resolver cannot claim provider usage")
        elif self.status is not OperationStatus.NOT_EXECUTED:
            calls = self.usage_delta.model_calls_delta
            if calls not in {0, 1}:
                raise ValueError("one provider operation cannot claim multiple calls")
            if (calls == 1) != (self.provider_call_index is not None):
                raise ValueError("provider call index differs from call delta")
            if self.status is OperationStatus.COMPLETED and calls != 1:
                raise ValueError("completed provider operation requires one model call")
        return self


class SpecialistOperationRecord(_OperationRecord):
    typed_output: SpecialistAssessmentV2 | None

    @model_validator(mode="after")
    def require_specialist_type(self) -> SpecialistOperationRecord:
        expected = {
            "metrics": OperationType.METRICS_SPECIALIST,
            "logs": OperationType.LOGS_SPECIALIST,
            "traces": OperationType.TRACES_SPECIALIST,
        }
        if self.source is None or expected[self.source] is not self.operation_type:
            raise ValueError("specialist source differs from operation type")
        if (self.status is OperationStatus.COMPLETED) != (
            self.typed_output is not None
        ):
            raise ValueError("specialist output differs from operation status")
        if self.typed_output is not None and self.typed_output.source != self.source:
            raise ValueError("specialist output source differs from operation")
        return self


class CommanderOperationRecord(_OperationRecord):
    typed_output: CommanderDecisionV2 | None

    @model_validator(mode="after")
    def require_commander_type(self) -> CommanderOperationRecord:
        if (
            self.operation_type is not OperationType.COMMANDER
            or self.source is not None
        ):
            raise ValueError("commander operation identity is invalid")
        if (self.status is OperationStatus.COMPLETED) != (
            self.typed_output is not None
        ):
            raise ValueError("commander output differs from operation status")
        if self.typed_output is not None:
            if self.selected_sources != self.typed_output.selected_sources:
                raise ValueError("commander selected sources differ from typed output")
        return self


class JudgeOperationRecord(_OperationRecord):
    typed_output: JudgeServiceDecisionV2 | None

    @model_validator(mode="after")
    def require_judge_type(self) -> JudgeOperationRecord:
        if (
            self.operation_type is not OperationType.FINAL_JUDGE
            or self.source is not None
        ):
            raise ValueError("judge operation identity is invalid")
        if (self.status is OperationStatus.COMPLETED) != (
            self.typed_output is not None
        ):
            raise ValueError("judge output differs from operation status")
        return self


class IndicatorResolutionRecord(_OperationRecord):
    typed_output: IndicatorResolutionV2 | None

    @model_validator(mode="after")
    def require_resolver_type(self) -> IndicatorResolutionRecord:
        if (
            self.operation_type is not OperationType.INDICATOR_RESOLVER
            or self.source is not None
        ):
            raise ValueError("indicator resolver operation identity is invalid")
        if (self.status is OperationStatus.COMPLETED) != (
            self.typed_output is not None
        ):
            raise ValueError("indicator output differs from operation status")
        return self


OperationRecord = (
    SpecialistOperationRecord
    | CommanderOperationRecord
    | JudgeOperationRecord
    | IndicatorResolutionRecord
)


class OperationDigestV2(V2Model):
    operation_index: StrictInt = Field(ge=1)
    operation_type: OperationType
    operation_sha256: Sha256
    stage_trace_sha256: Sha256
    completion_marker_sha256: Sha256


def operation_tree_sha256(entries: tuple[OperationDigestV2, ...]) -> str:
    payload = [entry.model_dump(mode="json") for entry in entries]
    encoded = json.dumps(
        payload,
        allow_nan=False,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class RunTraceV2(V2Model):
    schema_version: Literal["rcaeval-re2-v2.run-trace.v1"]
    run_id: RunId
    case_id: CaseId
    system: DevSystem
    architecture: ArchitectureV2
    operation_count: StrictInt = Field(ge=0)
    operations: tuple[OperationDigestV2, ...]
    operation_tree_sha256: Sha256
    created_at_utc: AwareDatetime

    @model_validator(mode="after")
    def require_recomputable_contiguous_tree(self) -> RunTraceV2:
        if self.operation_count != len(self.operations):
            raise ValueError("run trace operation count differs from journal")
        if tuple(item.operation_index for item in self.operations) != tuple(
            range(1, len(self.operations) + 1)
        ):
            raise ValueError("run trace operation indices are not contiguous")
        if self.operation_tree_sha256 != operation_tree_sha256(self.operations):
            raise ValueError("run trace operation tree hash is invalid")
        return self


class TerminalDispositionV2(V2Model):
    terminal_status: OperationStatus
    failure_operation_type: OperationType | None
    failure_operation_index: StrictInt | None = Field(ge=1)
    failure_code: OperationFailureCode | None
    failure_stage: OperationStage | None
    diagnosis: DiagnosisV2 | None
    tool_calls: StrictInt = Field(ge=0, le=8)

    @model_validator(mode="after")
    def require_failure_disposition(self) -> TerminalDispositionV2:
        stage = (self.failure_operation_type, self.failure_operation_index)
        if (stage[0] is None) != (stage[1] is None):
            raise ValueError("failure operation type and index must be paired")
        if self.terminal_status is OperationStatus.COMPLETED:
            if (
                self.failure_code is not None
                or self.failure_stage is not None
                or stage != (None, None)
                or self.diagnosis is None
            ):
                raise ValueError("completed terminal disposition cannot claim failure")
        elif (
            self.failure_code is None
            or self.failure_stage is None
            or self.diagnosis is not None
        ):
            raise ValueError(
                "failed terminal disposition requires failure without diagnosis"
            )
        return self


class TerminalRecordV2(V2Model):
    schema_version: Literal["rcaeval-re2-v2.terminal-record.v1"]
    run_id: RunId
    case_id: CaseId
    system: DevSystem
    architecture: ArchitectureV2
    terminal_status: OperationStatus
    failure_operation_type: OperationType | None
    failure_operation_index: StrictInt | None = Field(ge=1)
    failure_code: OperationFailureCode | None
    failure_stage: OperationStage | None
    diagnosis: DiagnosisV2 | None
    tool_calls: StrictInt = Field(ge=0, le=8)
    run_trace_sha256: Sha256
    operation_tree_sha256: Sha256
    usage: ProviderUsageDelta
    started_at_utc: AwareDatetime
    ended_at_utc: AwareDatetime
    latency_ms: StrictFloat = Field(ge=0.0)

    @model_validator(mode="after")
    def require_terminal_consistency(self) -> TerminalRecordV2:
        TerminalDispositionV2(
            terminal_status=self.terminal_status,
            failure_operation_type=self.failure_operation_type,
            failure_operation_index=self.failure_operation_index,
            failure_code=self.failure_code,
            failure_stage=self.failure_stage,
            diagnosis=self.diagnosis,
            tool_calls=self.tool_calls,
        )
        if self.terminal_status is OperationStatus.NOT_EXECUTED:
            raise ValueError("NOT_EXECUTED is not a terminal run status")
        if self.ended_at_utc < self.started_at_utc:
            raise ValueError("run ended before it started")
        return self
