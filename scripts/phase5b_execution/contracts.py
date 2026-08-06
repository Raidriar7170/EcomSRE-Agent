"""Strict truth-free contracts for Phase 5B scored execution."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
import hashlib
import json
from pathlib import PurePosixPath
import re
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictFloat,
    StrictInt,
    field_validator,
    model_validator,
)

from ecomsre.phase5b.contracts import (
    ExecutionSchedule,
    ScheduledRun,
    VariantName,
)
from ecomsre.phase5b.analysis import BootstrapResult


EvaluationVersion = Literal["phase5b.v1"]
EvidenceClass = Literal[
    "ACTUAL_SCORED",
    "MOCK_EXECUTION_REHEARSAL",
    "UNSCORED_PROVIDER_CANARY",
]
InvestigatedSource = Literal["METRICS", "LOGS", "TRACES", "CHANGES"]
_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_RUN_ID_PATTERN = r"^[0-9a-f]{32}$"
_TEMPLATE_PATTERN = r"^[a-z0-9][a-z0-9-]{0,127}$"
_EVIDENCE_REF_PATTERN = re.compile(
    r"^evidence://(?P<run_id>[0-9a-f]{32})/[a-z0-9_-]+/[0-9]{4}$"
)
PROVIDER_CANARY_RUN_ID = hashlib.sha256(
    b"phase5b.v1\0public-provider-canary\0ad-partial-failure-complete\0seed-00"
).hexdigest()[:32]


class ExecutionModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)


class TerminalStatus(str, Enum):
    COMPLETED = "COMPLETED"
    WORKFLOW_FAILURE = "WORKFLOW_FAILURE"
    PROVIDER_TRANSPORT_FAILURE = "PROVIDER_TRANSPORT_FAILURE"
    PROVIDER_PROTOCOL_FAILURE = "PROVIDER_PROTOCOL_FAILURE"
    SEMANTIC_FAILURE = "SEMANTIC_FAILURE"
    INVALID_SCHEMA = "INVALID_SCHEMA"
    UNRESOLVED_EVIDENCE_REFERENCE = "UNRESOLVED_EVIDENCE_REFERENCE"
    BUDGET_FAILURE = "BUDGET_FAILURE"
    EMPTY_FINAL_ANSWER = "EMPTY_FINAL_ANSWER"


def canonical_json_bytes(payload: object) -> bytes:
    return (
        json.dumps(
            payload,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def sha256_canonical(payload: object) -> str:
    return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


class ScoredRunRequest(ExecutionModel):
    schema_version: Literal["phase5b.scored-run-request.v1"] = (
        "phase5b.scored-run-request.v1"
    )
    evaluation_version: EvaluationVersion = "phase5b.v1"
    run_id: str = Field(pattern=_RUN_ID_PATTERN)
    template_id: str = Field(pattern=_TEMPLATE_PATTERN)
    seed_id: str = Field(pattern=r"^seed-0[0-4]$")
    variant: VariantName

    @classmethod
    def from_scheduled_run(cls, scheduled: ScheduledRun) -> ScoredRunRequest:
        return cls(
            run_id=scheduled.run_id,
            template_id=scheduled.template_id,
            seed_id=scheduled.seed_id,
            variant=scheduled.variant,
        )

    def require_schedule_membership(
        self,
        schedule: ExecutionSchedule,
    ) -> None:
        matching = tuple(item for item in schedule.runs if item.run_id == self.run_id)
        if len(matching) != 1:
            raise ValueError("run identifier is not in the frozen schedule")
        expected = ScoredRunRequest.from_scheduled_run(matching[0])
        if self != expected:
            raise ValueError("run request differs from the frozen schedule")

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.model_dump(mode="json"))

    def request_sha256(self) -> str:
        return hashlib.sha256(self.canonical_bytes()).hexdigest()


class ObservedDiagnosisRecord(ExecutionModel):
    schema_version: Literal["phase5b.observed-diagnosis.v1"] = (
        "phase5b.observed-diagnosis.v1"
    )
    run_id: str = Field(pattern=_RUN_ID_PATTERN)
    decision: Literal["RCA_CONFIRMED", "NEED_MORE_EVIDENCE", "ABSTAIN"]
    root_service: str | None = Field(default=None, max_length=128)
    fault_mechanism: str | None = Field(default=None, max_length=128)
    causal_chain: tuple[str, ...] = Field(max_length=16)
    affected_sli: str | None = Field(default=None, max_length=256)
    supporting_evidence: tuple[str, ...] = Field(max_length=64)
    contradicting_evidence: tuple[str, ...] = Field(max_length=64)
    missing_evidence: tuple[str, ...] = Field(max_length=32)
    confidence: StrictFloat = Field(ge=0, le=1)
    decision_rationale: str = Field(min_length=1, max_length=1000)
    recommended_next_action: str = Field(min_length=1, max_length=500)

    @field_validator("root_service", "fault_mechanism", "affected_sli")
    @classmethod
    def reject_blank_optional_text(cls, value: str | None) -> str | None:
        if value is not None and (not value.strip() or value != value.strip()):
            raise ValueError("optional diagnosis text must be trimmed and nonempty")
        return value

    @field_validator(
        "causal_chain",
        "missing_evidence",
        "supporting_evidence",
        "contradicting_evidence",
    )
    @classmethod
    def reject_blank_entries(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if any(not value.strip() or value != value.strip() for value in values):
            raise ValueError("diagnosis entries must be trimmed and nonempty")
        return values

    @model_validator(mode="after")
    def require_truth_free_decision_semantics(self) -> ObservedDiagnosisRecord:
        if set(self.supporting_evidence).intersection(self.contradicting_evidence):
            raise ValueError("evidence cannot both support and contradict")
        for evidence_ref in self.supporting_evidence + self.contradicting_evidence:
            match = _EVIDENCE_REF_PATTERN.fullmatch(evidence_ref)
            if match is None or match.group("run_id") != self.run_id:
                raise ValueError("diagnosis evidence is outside the scheduled run")
        if self.decision == "RCA_CONFIRMED":
            if self.root_service is None or self.fault_mechanism is None:
                raise ValueError("confirmed diagnosis requires root and mechanism")
            if not self.causal_chain or self.affected_sli is None:
                raise ValueError("confirmed diagnosis requires a causal SLI chain")
            if len(self.supporting_evidence) < 2 or self.missing_evidence:
                raise ValueError("confirmed diagnosis evidence semantics are invalid")
        elif self.decision == "NEED_MORE_EVIDENCE":
            if self.root_service is not None or self.fault_mechanism is not None:
                raise ValueError("need-more cannot claim root or mechanism")
            if self.causal_chain or not self.missing_evidence:
                raise ValueError("need-more evidence semantics are invalid")
        elif (
            self.root_service is not None
            or self.fault_mechanism is not None
            or self.causal_chain
            or self.supporting_evidence
            or self.missing_evidence
        ):
            raise ValueError("abstain diagnosis semantics are invalid")
        return self


class ProviderUsageRecord(ExecutionModel):
    schema_version: Literal["phase5b.provider-usage.v1"] = (
        "phase5b.provider-usage.v1"
    )
    max_model_calls: Literal[8] = 8
    max_tool_calls: Literal[8] = 8
    max_tokens: Literal[32000] = 32000
    max_completion_tokens: Literal[2048] = 2048
    model_calls: StrictInt = Field(ge=0)
    tool_calls: StrictInt = Field(ge=0)
    input_tokens: StrictInt = Field(ge=0)
    output_tokens: StrictInt = Field(ge=0)
    total_tokens: StrictInt = Field(ge=0)
    workflow_tokens: StrictInt = Field(ge=0)
    combined_tokens: StrictInt = Field(ge=0)
    provider_network_calls: StrictInt = Field(ge=0, le=1)
    provider_usage_known: StrictBool
    within_budget: StrictBool = True

    @model_validator(mode="after")
    def require_complete_usage(self) -> ProviderUsageRecord:
        if self.provider_usage_known:
            if self.total_tokens != self.input_tokens + self.output_tokens:
                raise ValueError("known provider usage totals are inconsistent")
        elif self.input_tokens or self.output_tokens or self.total_tokens:
            raise ValueError("unknown provider usage cannot claim token counts")
        if self.combined_tokens != self.workflow_tokens + self.total_tokens:
            raise ValueError("combined token usage is inconsistent")
        expected_within_budget = (
            self.model_calls <= self.max_model_calls
            and self.tool_calls <= self.max_tool_calls
            and self.output_tokens <= self.max_completion_tokens
            and self.combined_tokens <= self.max_tokens
        )
        if self.within_budget is not expected_within_budget:
            raise ValueError("usage budget disposition is inconsistent")
        return self


class ExecutionAttemptMarker(ExecutionModel):
    schema_version: Literal["phase5b.execution-attempt-marker.v1"] = (
        "phase5b.execution-attempt-marker.v1"
    )
    evaluation_version: EvaluationVersion = "phase5b.v1"
    run_id: str = Field(pattern=_RUN_ID_PATTERN)
    request_sha256: str = Field(pattern=_SHA256_PATTERN)
    evidence_class: EvidenceClass = "ACTUAL_SCORED"
    provider_configuration_sha256: str | None = Field(
        default=None,
        pattern=_SHA256_PATTERN,
    )
    attempt_number: Literal[1] = 1
    state: Literal["EXECUTION_ATTEMPT_STARTED"] = "EXECUTION_ATTEMPT_STARTED"
    started_at_utc: datetime

    @field_validator("started_at_utc")
    @classmethod
    def require_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() != timezone.utc.utcoffset(value):
            raise ValueError("attempt timestamp must be UTC")
        return value

    @model_validator(mode="after")
    def require_canary_configuration_binding(self) -> ExecutionAttemptMarker:
        if (self.evidence_class == "UNSCORED_PROVIDER_CANARY") != (
            self.provider_configuration_sha256 is not None
        ):
            raise ValueError("only the Provider canary marker binds configuration")
        return self

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.model_dump(mode="json"))


class RawScoredRunRecord(ExecutionModel):
    schema_version: Literal["phase5b.raw-scored-run-record.v1"] = (
        "phase5b.raw-scored-run-record.v1"
    )
    evaluation_version: EvaluationVersion = "phase5b.v1"
    run_id: str = Field(pattern=_RUN_ID_PATTERN)
    template_id: str = Field(pattern=_TEMPLATE_PATTERN)
    seed_id: str = Field(pattern=r"^seed-0[0-4]$")
    variant: VariantName
    terminal_status: TerminalStatus
    observed_diagnosis: ObservedDiagnosisRecord | None
    investigated_sources: tuple[InvestigatedSource, ...] = Field(max_length=4)
    targeted_refinement_used: StrictBool
    usage: ProviderUsageRecord
    evidence_class: EvidenceClass
    provider_attempted: StrictBool
    latency_ms: StrictInt = Field(ge=0)
    latency_known: StrictBool = True
    failure_code: str | None = Field(default=None, max_length=128)
    failure_stage: str | None = Field(default=None, max_length=128)
    recorded_at_utc: datetime
    record_sha256: str = Field(pattern=_SHA256_PATTERN)

    @field_validator("recorded_at_utc")
    @classmethod
    def require_record_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() != timezone.utc.utcoffset(value):
            raise ValueError("record timestamp must be UTC")
        return value

    @model_validator(mode="after")
    def require_terminal_shape(self) -> RawScoredRunRecord:
        if len(set(self.investigated_sources)) != len(self.investigated_sources):
            raise ValueError("investigated sources must be unique")
        if self.terminal_status is TerminalStatus.COMPLETED:
            if self.observed_diagnosis is None:
                raise ValueError("completed run requires a typed diagnosis")
            if self.observed_diagnosis.run_id != self.run_id:
                raise ValueError("diagnosis run identifier mismatch")
            if self.failure_code is not None or self.failure_stage is not None:
                raise ValueError("completed run cannot carry failure metadata")
        else:
            if self.observed_diagnosis is not None:
                raise ValueError("terminal failure cannot carry a typed diagnosis")
            if not self.failure_code or not self.failure_stage:
                raise ValueError("terminal failure requires typed failure metadata")
        if (
            not self.usage.within_budget
            and self.terminal_status is not TerminalStatus.BUDGET_FAILURE
        ):
            raise ValueError("over-budget usage requires BUDGET_FAILURE")
        if self.evidence_class == "MOCK_EXECUTION_REHEARSAL":
            if self.provider_attempted or self.usage.provider_network_calls != 0:
                raise ValueError("mock rehearsal cannot claim a Provider network call")
        elif self.provider_attempted != (self.usage.provider_network_calls == 1):
            raise ValueError("Provider attempt and network call accounting differ")
        return self

    def _hash_payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude={"record_sha256"})

    def expected_record_sha256(self) -> str:
        return sha256_canonical(self._hash_payload())

    def verify_record_sha256(self) -> None:
        if self.record_sha256 != self.expected_record_sha256():
            raise ValueError("raw record SHA-256 mismatch")

    def canonical_bytes(self) -> bytes:
        self.verify_record_sha256()
        return canonical_json_bytes(self.model_dump(mode="json"))


def seal_raw_record(
    *,
    run_id: str,
    template_id: str,
    seed_id: str,
    variant: VariantName,
    terminal_status: TerminalStatus,
    observed_diagnosis: ObservedDiagnosisRecord | None,
    usage: ProviderUsageRecord,
    evidence_class: EvidenceClass,
    provider_attempted: bool,
    latency_ms: int,
    failure_code: str | None,
    failure_stage: str | None,
    latency_known: bool = True,
    investigated_sources: tuple[InvestigatedSource, ...] = (),
    targeted_refinement_used: bool = False,
    recorded_at_utc: datetime | None = None,
) -> RawScoredRunRecord:
    provisional = RawScoredRunRecord(
        run_id=run_id,
        template_id=template_id,
        seed_id=seed_id,
        variant=variant,
        terminal_status=terminal_status,
        observed_diagnosis=observed_diagnosis,
        investigated_sources=investigated_sources,
        targeted_refinement_used=targeted_refinement_used,
        usage=usage,
        evidence_class=evidence_class,
        provider_attempted=provider_attempted,
        latency_ms=latency_ms,
        latency_known=latency_known,
        failure_code=failure_code,
        failure_stage=failure_stage,
        recorded_at_utc=recorded_at_utc or datetime.now(timezone.utc),
        record_sha256="0" * 64,
    )
    sealed = provisional.model_copy(
        update={"record_sha256": provisional.expected_record_sha256()}
    )
    sealed.verify_record_sha256()
    return sealed


class AblationRunRequest(ExecutionModel):
    schema_version: Literal["phase5b.ablation-run-request.v1"] = (
        "phase5b.ablation-run-request.v1"
    )
    evaluation_version: EvaluationVersion = "phase5b.v1"
    ablation_run_id: str = Field(pattern=_RUN_ID_PATTERN)
    ablation_id: str = Field(pattern=r"^[A-Z][A-Z0-9_]{0,63}$")
    template_id: str = Field(pattern=_TEMPLATE_PATTERN)
    seed_id: str = Field(pattern=r"^seed-0[0-4]$")
    run_kind: Literal["DIAGNOSIS", "REMEDIATION"]
    base_variant: Literal["DYNAMIC_MULTI_AGENT_V2"] = (
        "DYNAMIC_MULTI_AGENT_V2"
    )
    primary_eligible: Literal[False] = False

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.model_dump(mode="json"))

    def request_sha256(self) -> str:
        return hashlib.sha256(self.canonical_bytes()).hexdigest()


class AblationRunRecord(ExecutionModel):
    schema_version: Literal["phase5b.ablation-run-record.v1"] = (
        "phase5b.ablation-run-record.v1"
    )
    evaluation_version: EvaluationVersion = "phase5b.v1"
    ablation_run_id: str = Field(pattern=_RUN_ID_PATTERN)
    ablation_id: str = Field(pattern=r"^[A-Z][A-Z0-9_]{0,63}$")
    template_id: str = Field(pattern=_TEMPLATE_PATTERN)
    seed_id: str = Field(pattern=r"^seed-0[0-4]$")
    run_kind: Literal["DIAGNOSIS", "REMEDIATION"]
    base_variant: Literal["DYNAMIC_MULTI_AGENT_V2"] = (
        "DYNAMIC_MULTI_AGENT_V2"
    )
    primary_eligible: Literal[False] = False
    terminal_status: TerminalStatus
    observed_diagnosis: ObservedDiagnosisRecord | None
    usage: ProviderUsageRecord
    evidence_class: EvidenceClass
    provider_attempted: StrictBool
    latency_ms: StrictInt = Field(ge=0)
    failure_code: str | None = Field(default=None, max_length=128)
    failure_stage: str | None = Field(default=None, max_length=128)
    recorded_at_utc: datetime
    record_sha256: str = Field(pattern=_SHA256_PATTERN)

    @field_validator("recorded_at_utc")
    @classmethod
    def require_record_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() != timezone.utc.utcoffset(value):
            raise ValueError("ablation record timestamp must be UTC")
        return value

    @model_validator(mode="after")
    def require_ablation_terminal_shape(self) -> AblationRunRecord:
        if self.terminal_status is TerminalStatus.COMPLETED:
            if self.observed_diagnosis is None:
                raise ValueError("completed ablation requires a typed diagnosis")
            if self.observed_diagnosis.run_id != self.ablation_run_id:
                raise ValueError("ablation diagnosis run identifier mismatch")
            if self.failure_code is not None or self.failure_stage is not None:
                raise ValueError("completed ablation cannot carry failure metadata")
        elif self.observed_diagnosis is not None or not self.failure_code or not self.failure_stage:
            raise ValueError("failed ablation terminal shape is invalid")
        if self.evidence_class == "MOCK_EXECUTION_REHEARSAL" and (
            self.provider_attempted or self.usage.provider_network_calls
        ):
            raise ValueError("mock ablation cannot claim a Provider network call")
        if self.evidence_class == "ACTUAL_SCORED" and self.provider_attempted != (
            self.usage.provider_network_calls == 1
        ):
            raise ValueError("ablation Provider attempt accounting differs")
        if (
            not self.usage.within_budget
            and self.terminal_status is not TerminalStatus.BUDGET_FAILURE
        ):
            raise ValueError("over-budget ablation requires BUDGET_FAILURE")
        return self

    def _hash_payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude={"record_sha256"})

    def expected_record_sha256(self) -> str:
        return sha256_canonical(self._hash_payload())

    def verify_record_sha256(self) -> None:
        if self.record_sha256 != self.expected_record_sha256():
            raise ValueError("ablation record SHA-256 mismatch")

    def canonical_bytes(self) -> bytes:
        self.verify_record_sha256()
        return canonical_json_bytes(self.model_dump(mode="json"))


def seal_ablation_record(
    *,
    request: AblationRunRequest,
    terminal_status: TerminalStatus,
    observed_diagnosis: ObservedDiagnosisRecord | None,
    usage: ProviderUsageRecord,
    evidence_class: EvidenceClass,
    provider_attempted: bool,
    latency_ms: int,
    failure_code: str | None,
    failure_stage: str | None,
    recorded_at_utc: datetime,
) -> AblationRunRecord:
    provisional = AblationRunRecord(
        ablation_run_id=request.ablation_run_id,
        ablation_id=request.ablation_id,
        template_id=request.template_id,
        seed_id=request.seed_id,
        run_kind=request.run_kind,
        terminal_status=terminal_status,
        observed_diagnosis=observed_diagnosis,
        usage=usage,
        evidence_class=evidence_class,
        provider_attempted=provider_attempted,
        latency_ms=latency_ms,
        failure_code=failure_code,
        failure_stage=failure_stage,
        recorded_at_utc=recorded_at_utc,
        record_sha256="0" * 64,
    )
    sealed = provisional.model_copy(
        update={"record_sha256": provisional.expected_record_sha256()}
    )
    sealed.verify_record_sha256()
    return sealed


class ExecutionBundleManifest(ExecutionModel):
    schema_version: Literal["phase5b.execution-bundle-manifest.v1"]
    evaluation_version: EvaluationVersion
    execution_schedule_sha256: str = Field(pattern=_SHA256_PATTERN)
    record_count: Literal[180]
    record_sha256_by_run_id: dict[str, str]
    all_checkpoints_closed: Literal[True]
    provider_network_calls: StrictInt = Field(ge=0, le=180)
    hidden_retry: Literal[False]
    scripted_fallback: Literal[False]


class WorkerSandboxPolicy(ExecutionModel):
    schema_version: Literal["phase5b.worker-sandbox-policy.v1"]
    request_fields: tuple[
        Literal["run_id", "template_id", "seed_id", "variant"], ...
    ]
    environment_allowlist: tuple[str, ...]
    truth_environment_removed: Literal[True]
    repository_truth_roots_denied: Literal[True]
    external_ground_truth_component_denied: Literal[True]
    builder_source_and_logs_denied: Literal[True]
    evaluator_import_denied: Literal[True]
    nested_process_denied: Literal[True]
    provider_network_allowed: Literal[True]


class CheckpointPolicy(ExecutionModel):
    schema_version: Literal["phase5b.checkpoint-policy.v1"]
    attempt_marker_before_provider: Literal[True]
    create_once_terminal_record: Literal[True]
    interrupted_attempt_terminalized: Literal[True]
    retry: Literal[False]
    rerun_failed: Literal[False]
    overwrite: Literal[False]


class ExecutionLifecyclePolicy(ExecutionModel):
    schema_version: Literal["phase5b.execution-lifecycle-policy.v1"]
    merged_origin_main_required: Literal[True]
    required_results_branch: Literal["phase5b/v1-frozen-results"]
    provider_canary_create_once: Literal[True]
    provider_canary_public_unscored: Literal[True]
    execution_started_create_once: Literal[True]
    execution_complete_create_once: Literal[True]
    unblinding_create_once: Literal[True]
    final_report_create_once: Literal[True]
    truth_environment_before_unblinding: Literal[False]
    source_read_only_after_execution_started: Literal[True]


class ExecutionFreezeManifest(ExecutionModel):
    schema_version: Literal["phase5b.execution-freeze.v1"]
    evaluation_version: EvaluationVersion
    protocol_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    execution_base_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    protocol_freeze_manifest_sha256: str = Field(pattern=_SHA256_PATTERN)
    hidden_pack_seal_record_sha256: str = Field(pattern=_SHA256_PATTERN)
    hidden_pack_manifest_sha256: str = Field(pattern=_SHA256_PATTERN)
    agent_visible_pack_sha256: str = Field(pattern=_SHA256_PATTERN)
    ground_truth_pack_sha256: str = Field(pattern=_SHA256_PATTERN)
    execution_schedule_sha256: str = Field(pattern=_SHA256_PATTERN)
    ablation_registry_sha256: str = Field(pattern=_SHA256_PATTERN)
    harness_files: dict[str, str]
    provider: Literal["openai-compatible"]
    model: Literal["gpt-5.4-mini-2026-03-17"]
    temperature: Literal[0]
    max_model_calls: Literal[8]
    max_tool_calls: Literal[8]
    max_tokens: Literal[32000]
    max_completion_tokens: Literal[2048]
    provider_pacing_seconds: Literal[2]
    hidden_retry: Literal[False]
    scripted_fallback: Literal[False]
    main_run_count: Literal[180]
    ablation_run_count: Literal[38]
    main_evaluation_ready: Literal[True]
    ablation_slot_count: Literal[38]
    ablation_implementation_available: Literal[False]
    ablation_evidence_available: Literal[False]
    ablation_primary_eligible: Literal[False]
    ablation_disposition: Literal[
        "ABLATION_NOT_IMPLEMENTED_IN_FROZEN_HARNESS"
    ]
    worker_sandbox_policy: WorkerSandboxPolicy
    checkpoint_policy: CheckpointPolicy
    lifecycle_policy: ExecutionLifecyclePolicy
    unblinding_contract: Literal[
        "phase5b.unblinding-record.v1-execution-layer-superset"
    ]
    ablation_execution_policy: Literal[
        "all_preregistered_v1_ablations_not_implemented_terminal_failure"
    ]

    @model_validator(mode="after")
    def require_sorted_harness_bindings(self) -> ExecutionFreezeManifest:
        if not self.harness_files:
            raise ValueError("execution freeze must bind harness files")
        if tuple(self.harness_files) != tuple(sorted(self.harness_files)):
            raise ValueError("execution harness paths must be sorted")
        for path, digest in self.harness_files.items():
            if (
                path.startswith("/")
                or "\\" in path
                or ".." in PurePosixPath(path).parts
                or not re.fullmatch(_SHA256_PATTERN, digest)
            ):
                raise ValueError("execution harness binding is invalid")
        return self


class AblationExecutionSeal(ExecutionModel):
    schema_version: Literal["phase5b.ablation-execution-seal.v1"]
    evaluation_version: EvaluationVersion
    ablation_registry_sha256: str = Field(pattern=_SHA256_PATTERN)
    ablation_run_count: Literal[38]
    report_sha256: str = Field(pattern=_SHA256_PATTERN)
    primary_eligible: Literal[False]
    provider_network_calls: StrictInt = Field(ge=0, le=38)


class GroundTruthProjection(ExecutionModel):
    schema_version: Literal["phase5b.truth-projection.v1"]
    template_id: str = Field(pattern=_TEMPLATE_PATTERN)
    seed_id: str = Field(pattern=r"^seed-0[0-4]$")
    expected_decision: Literal[
        "RCA_CONFIRMED", "NEED_MORE_EVIDENCE", "ABSTAIN"
    ]
    expected_root_service: str | None = Field(default=None, max_length=128)
    expected_fault_mechanism: str | None = Field(default=None, max_length=128)
    incident_confirmed: StrictBool
    required_support_sources: tuple[InvestigatedSource, ...] = Field(max_length=4)
    required_contradiction_handling: tuple[str, ...] = Field(max_length=32)
    required_supporting_evidence: tuple[str, ...] = Field(max_length=64)
    required_contradicting_evidence: tuple[str, ...] = Field(max_length=64)
    expected_missing_evidence: tuple[str, ...] = Field(max_length=32)
    declared_decoy_evidence: tuple[str, ...] = Field(max_length=32)
    write_disposition: Literal[
        "NO_ACTION", "SAFE_REPLAY_REMEDIATION_CANDIDATE"
    ]
    difficult_subsets: tuple[str, ...] = Field(max_length=10)


class HiddenGroundTruthContract(ExecutionModel):
    """Execution-layer mirror of the merged public hidden-truth contract."""

    schema_version: Literal["phase5b.hidden-ground-truth.v1"]
    evaluation_version: EvaluationVersion
    template_id: str = Field(pattern=r"^hidden-0[1-6]$")
    seed_id: str = Field(pattern=r"^seed-0[0-4]$")
    decision: Literal["RCA_CONFIRMED", "NEED_MORE_EVIDENCE", "ABSTAIN"]
    incident_confirmed: StrictBool
    root_service: str | None = Field(default=None, max_length=128)
    fault_mechanism: str | None = Field(default=None, max_length=128)
    causal_chain: tuple[str, ...] = Field(max_length=16)
    affected_sli: str | None = Field(default=None, max_length=256)
    required_support_sources: tuple[InvestigatedSource, ...] = Field(max_length=4)
    required_contradiction_handling: tuple[str, ...] = Field(max_length=32)
    required_missing_evidence: tuple[str, ...] = Field(max_length=32)
    write_disposition: Literal[
        "NO_ACTION", "SAFE_REPLAY_REMEDIATION_CANDIDATE"
    ]
    difficult_subsets: tuple[str, ...] = Field(max_length=10)

    @field_validator(
        "causal_chain",
        "required_contradiction_handling",
        "required_missing_evidence",
        "difficult_subsets",
    )
    @classmethod
    def require_unique_trimmed_entries(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if any(not item.strip() or item != item.strip() for item in values):
            raise ValueError("hidden truth entries must be trimmed and nonempty")
        if len(set(values)) != len(values):
            raise ValueError("hidden truth entries must be unique")
        return values

    @field_validator("root_service", "fault_mechanism", "affected_sli")
    @classmethod
    def require_trimmed_optional_text(cls, value: str | None) -> str | None:
        if value is not None and (not value.strip() or value != value.strip()):
            raise ValueError("hidden truth optional text must be trimmed")
        return value

    @model_validator(mode="after")
    def require_decision_semantics(self) -> HiddenGroundTruthContract:
        if len(set(self.required_support_sources)) != len(self.required_support_sources):
            raise ValueError("hidden truth support sources must be unique")
        if self.decision == "RCA_CONFIRMED":
            if not self.incident_confirmed:
                raise ValueError("confirmed truth requires a confirmed incident")
            if self.root_service is None or self.fault_mechanism is None:
                raise ValueError("confirmed truth requires root and mechanism")
            if not self.causal_chain or self.affected_sli is None:
                raise ValueError("confirmed truth requires causal chain and affected SLI")
            if len(self.required_support_sources) < 2:
                raise ValueError("confirmed truth requires two support sources")
            if self.required_missing_evidence:
                raise ValueError("confirmed truth cannot retain evidence gaps")
        elif self.decision == "NEED_MORE_EVIDENCE":
            if self.root_service is not None or self.fault_mechanism is not None:
                raise ValueError("need-more truth cannot claim root or mechanism")
            if self.causal_chain or not self.required_missing_evidence:
                raise ValueError("need-more truth requires a concrete missing evidence gap")
        elif (
            self.incident_confirmed
            or self.root_service is not None
            or self.fault_mechanism is not None
            or self.causal_chain
            or self.required_support_sources
        ):
            raise ValueError("abstain truth requires no confirmed incident or cause")
        return self


class ScoredRunEvaluation(ExecutionModel):
    schema_version: Literal["phase5b.scored-run-evaluation.v1"]
    evaluation_version: EvaluationVersion
    run_id: str = Field(pattern=_RUN_ID_PATTERN)
    template_id: str = Field(pattern=_TEMPLATE_PATTERN)
    seed_id: str = Field(pattern=r"^seed-0[0-4]$")
    population: Literal["HIDDEN", "PUBLIC"]
    variant: VariantName
    expected_decision: Literal[
        "RCA_CONFIRMED", "NEED_MORE_EVIDENCE", "ABSTAIN"
    ]
    terminal_status: TerminalStatus
    raw_record_sha256: str = Field(pattern=_SHA256_PATTERN)
    truth_content_sha256: str = Field(pattern=_SHA256_PATTERN)
    decision_correct: StrictBool
    root_service_correct: StrictBool
    mechanism_correct: StrictBool
    evidence_refs_valid: StrictBool
    contradiction_handling_correct: StrictBool
    missing_evidence_correct: StrictBool
    abstention_correct: StrictBool
    decoy_resistance_correct: StrictBool
    safe_no_action_correct: StrictBool
    runtime_completed: StrictBool
    contradiction_applicable: StrictBool
    missing_evidence_applicable: StrictBool
    abstention_applicable: StrictBool
    decoy_applicable: StrictBool
    model_calls: StrictInt = Field(ge=0)
    tool_calls: StrictInt = Field(ge=0)
    provider_tokens: StrictInt = Field(ge=0)
    total_tokens: StrictInt = Field(ge=0)
    provider_usage_known: StrictBool
    latency_ms: StrictInt = Field(ge=0)
    latency_known: StrictBool
    investigated_source_count: StrictInt = Field(ge=0, le=4)
    refinement_used: StrictBool
    difficult_subsets: tuple[str, ...]
    failure_code: str | None = Field(default=None, max_length=128)

    @model_validator(mode="after")
    def require_failure_denominator(self) -> ScoredRunEvaluation:
        if self.terminal_status is not TerminalStatus.COMPLETED and self.decision_correct:
            raise ValueError("terminal failure cannot score as decision-correct")
        if self.runtime_completed != (self.terminal_status is TerminalStatus.COMPLETED):
            raise ValueError("runtime completion differs from terminal status")
        return self


class ScoringBundle(ExecutionModel):
    schema_version: Literal["phase5b.scoring-bundle.v1"]
    evaluation_version: EvaluationVersion
    execution_report_sha256: str = Field(pattern=_SHA256_PATTERN)
    unblinding_record_sha256: str = Field(pattern=_SHA256_PATTERN)
    ground_truth_pack_sha256: str = Field(pattern=_SHA256_PATTERN)
    run_count: Literal[180]
    all_failures_retained: Literal[True]
    records: tuple[ScoredRunEvaluation, ...]

    @model_validator(mode="after")
    def require_complete_scoring_bundle(self) -> ScoringBundle:
        if len(self.records) != 180 or len({item.run_id for item in self.records}) != 180:
            raise ValueError("scoring bundle must contain 180 unique runs")
        return self


class MetricSummary(ExecutionModel):
    denominator: StrictInt = Field(ge=0)
    completed: StrictInt = Field(ge=0)
    metric_means: dict[str, StrictFloat]
    metric_denominators: dict[str, StrictInt]
    cost_means: dict[str, StrictFloat]
    cost_denominators: dict[str, StrictInt]


class PopulationSummary(ExecutionModel):
    population: Literal[
        "HIDDEN_ONLY_PRIMARY", "FULL_SUITE_SECONDARY", "PUBLIC_ANCHOR_DESCRIPTIVE"
    ]
    pairing_units: StrictInt = Field(ge=0)
    variants: dict[VariantName, MetricSummary]


class DifficultSubsetSummary(ExecutionModel):
    subset: str = Field(min_length=1, max_length=128)
    run_count: StrictInt = Field(ge=0)
    variants: dict[VariantName, MetricSummary]


class FrozenAblationSummary(ExecutionModel):
    run_count: Literal[38]
    primary_eligible: Literal[False]
    ablation_slot_count: Literal[38]
    ablation_implementation_available: Literal[False]
    ablation_evidence_available: Literal[False]
    ablation_primary_eligible: Literal[False]
    ablation_disposition: Literal[
        "ABLATION_NOT_IMPLEMENTED_IN_FROZEN_HARNESS"
    ]
    implemented_run_count: StrictInt = Field(ge=0, le=38)
    terminal_failure_count: StrictInt = Field(ge=0, le=38)
    provider_network_calls: StrictInt = Field(ge=0, le=38)
    evidence_disposition: Literal[
        "PRIMARY_INELIGIBLE",
        "PRIMARY_INELIGIBLE_AND_NOT_IMPLEMENTED",
    ]
    remediation_metrics_status: Literal["EVALUATED", "NOT_EVALUABLE"]
    remediation_metrics: dict[str, StrictFloat | None]

    @model_validator(mode="after")
    def require_remediation_metric_surface(self) -> FrozenAblationSummary:
        expected = {
            "correct_no_action_rate",
            "safe_action_accuracy",
            "unsafe_action_block_rate",
            "verification_accuracy",
            "rollback_success_rate",
        }
        if set(self.remediation_metrics) != expected:
            raise ValueError("remediation metric surface is incomplete")
        if self.remediation_metrics_status == "NOT_EVALUABLE" and any(
            value is not None for value in self.remediation_metrics.values()
        ):
            raise ValueError("not-evaluable remediation metrics must be null")
        if self.remediation_metrics_status == "EVALUATED" and any(
            value is None for value in self.remediation_metrics.values()
        ):
            raise ValueError("evaluated remediation metrics cannot be null")
        return self


class FinalEvaluationReport(ExecutionModel):
    schema_version: Literal["phase5b.final-report.v1"]
    evaluation_version: EvaluationVersion
    protocol_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    execution_source_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    execution_freeze_sha256: str = Field(pattern=_SHA256_PATTERN)
    execution_report_sha256: str = Field(pattern=_SHA256_PATTERN)
    unblinding_record_sha256: str = Field(pattern=_SHA256_PATTERN)
    scoring_bundle_sha256: str = Field(pattern=_SHA256_PATTERN)
    main_run_count: Literal[180]
    ablation_run_count: Literal[38]
    main_evaluation_ready: Literal[True]
    ablation_slot_count: Literal[38]
    ablation_implementation_available: Literal[False]
    ablation_evidence_available: Literal[False]
    ablation_primary_eligible: Literal[False]
    ablation_disposition: Literal[
        "ABLATION_NOT_IMPLEMENTED_IN_FROZEN_HARNESS"
    ]
    populations: tuple[PopulationSummary, PopulationSummary, PopulationSummary]
    hidden_accuracy_bootstrap: BootstrapResult
    hidden_tool_reduction_bootstrap: BootstrapResult | None
    difficult_subsets: tuple[DifficultSubsetSummary, ...]
    ablations: FrozenAblationSummary
    claim_classification: Literal[
        "HIDDEN_ACCURACY_SUPERIORITY_SUPPORTED",
        "HIDDEN_COST_QUALITY_ADVANTAGE_SUPPORTED",
        "NO_PREREGISTERED_ADVANTAGE_SUPPORTED",
    ]
    bootstrap_replicates: Literal[10000]
    bootstrap_rng_seed: Literal[20260804]
    confidence_interval: StrictFloat
    all_failures_retained: Literal[True]
    hidden_retry: Literal[False]
    scripted_fallback: Literal[False]
    replay_only: Literal[True]
    post_unblinding_tuning: Literal[False]
    live_mutation: Literal[False]
    production_claim: Literal[False]

    @model_validator(mode="after")
    def require_frozen_analysis_constants(self) -> FinalEvaluationReport:
        if self.confidence_interval != 0.95:
            raise ValueError("final report confidence interval is not frozen")
        return self


class FinalReportDisposition(ExecutionModel):
    schema_version: Literal["phase5b.final-disposition.v1"]
    evaluation_version: EvaluationVersion
    protocol_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    execution_source_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    execution_freeze_sha256: str = Field(pattern=_SHA256_PATTERN)
    hidden_pack_manifest_sha256: str = Field(pattern=_SHA256_PATTERN)
    agent_visible_pack_sha256: str = Field(pattern=_SHA256_PATTERN)
    ground_truth_pack_sha256: str = Field(pattern=_SHA256_PATTERN)
    unblinding_record_sha256: str = Field(pattern=_SHA256_PATTERN)
    execution_complete_seal_sha256: str = Field(pattern=_SHA256_PATTERN)
    execution_report_sha256: str = Field(pattern=_SHA256_PATTERN)
    ablation_report_sha256: str = Field(pattern=_SHA256_PATTERN)
    scoring_bundle_sha256: str = Field(pattern=_SHA256_PATTERN)
    final_report_sha256: str = Field(pattern=_SHA256_PATTERN)
    main_runs: Literal[180]
    ablation_runs: Literal[38]
    main_evaluation_ready: Literal[True]
    ablation_slot_count: Literal[38]
    ablation_implementation_available: Literal[False]
    ablation_evidence_available: Literal[False]
    ablation_primary_eligible: Literal[False]
    ablation_disposition: Literal[
        "ABLATION_NOT_IMPLEMENTED_IN_FROZEN_HARNESS"
    ]
    failure_count: StrictInt = Field(ge=0, le=218)
    claim_classification: Literal[
        "HIDDEN_ACCURACY_SUPERIORITY_SUPPORTED",
        "HIDDEN_COST_QUALITY_ADVANTAGE_SUPPORTED",
        "NO_PREREGISTERED_ADVANTAGE_SUPPORTED",
    ]
    retuning_after_unblind: Literal[False]
    from_state: Literal["UNBLINDED"]
    to_state: Literal["FINAL_REPORT_FROZEN"]
    create_once: Literal[True]


class ProviderCanaryRecord(ExecutionModel):
    schema_version: Literal["phase5b.provider-canary-record.v1"]
    evaluation_version: EvaluationVersion
    public_template_id: Literal["ad-partial-failure-complete"]
    seed_id: Literal["seed-00"]
    variant: Literal["SINGLE_AGENT_V2"]
    terminal_status: TerminalStatus
    raw_record_sha256: str = Field(pattern=_SHA256_PATTERN)
    provider_configuration_sha256: str = Field(pattern=_SHA256_PATTERN)
    provider_network_calls: StrictInt = Field(ge=0, le=1)
    model_calls: StrictInt = Field(ge=0, le=8)
    input_tokens: StrictInt = Field(ge=0)
    output_tokens: StrictInt = Field(ge=0)
    total_tokens: StrictInt = Field(ge=0)
    provider_usage_known: StrictBool
    typed_protocol_pass: StrictBool
    no_retry: Literal[True]
    scripted_fallback: Literal[False]

    @model_validator(mode="after")
    def require_canary_disposition(self) -> ProviderCanaryRecord:
        if self.typed_protocol_pass != (
            self.terminal_status is TerminalStatus.COMPLETED
            and self.provider_network_calls == 1
            and self.model_calls >= 1
            and self.provider_usage_known
            and self.total_tokens == self.input_tokens + self.output_tokens
        ):
            raise ValueError("canary pass differs from terminal status")
        return self


class ExecutionStartedRecord(ExecutionModel):
    schema_version: Literal["phase5b.execution-started.v1"]
    evaluation_version: EvaluationVersion
    source_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    origin_main_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    execution_freeze_sha256: str = Field(pattern=_SHA256_PATTERN)
    hidden_pack_seal_sha256: str = Field(pattern=_SHA256_PATTERN)
    hidden_pack_manifest_sha256: str = Field(pattern=_SHA256_PATTERN)
    agent_visible_pack_sha256: str = Field(pattern=_SHA256_PATTERN)
    canary_record_sha256: str = Field(pattern=_SHA256_PATTERN)
    provider_configuration_sha256: str = Field(pattern=_SHA256_PATTERN)
    from_state: Literal["HIDDEN_PACK_SEALED"]
    to_state: Literal["EXECUTION_STARTED"]
    completed_main_runs: Literal[0]
    completed_ablation_runs: Literal[0]
    main_evaluation_ready: Literal[True] = True
    ablation_slot_count: Literal[38] = 38
    ablation_implementation_available: Literal[False] = False
    ablation_evidence_available: Literal[False] = False
    ablation_primary_eligible: Literal[False] = False
    ablation_disposition: Literal[
        "ABLATION_NOT_IMPLEMENTED_IN_FROZEN_HARNESS"
    ] = "ABLATION_NOT_IMPLEMENTED_IN_FROZEN_HARNESS"
    frozen_files_unchanged: Literal[True]
    ground_truth_read: Literal[False]
    create_once: Literal[True]

    @model_validator(mode="after")
    def require_merged_source_identity(self) -> ExecutionStartedRecord:
        if self.source_commit != self.origin_main_commit:
            raise ValueError("execution state must start from merged origin/main")
        return self


class AblationExecutionReport(ExecutionModel):
    schema_version: Literal["phase5b.ablation-execution-report.v1"]
    evaluation_version: EvaluationVersion
    ablation_registry_sha256: str = Field(pattern=_SHA256_PATTERN)
    run_count: Literal[38]
    all_terminal: Literal[True]
    record_sha256_by_run_id: dict[str, str]
    terminal_count_by_category: dict[str, StrictInt]
    provider_network_calls: StrictInt = Field(ge=0, le=38)
    primary_eligible: Literal[False]
    main_evaluation_ready: Literal[True]
    ablation_slot_count: Literal[38]
    ablation_implementation_available: Literal[False]
    ablation_evidence_available: Literal[False]
    ablation_primary_eligible: Literal[False]
    ablation_disposition: Literal[
        "ABLATION_NOT_IMPLEMENTED_IN_FROZEN_HARNESS"
    ]

    @model_validator(mode="after")
    def require_ablation_report_counts(self) -> AblationExecutionReport:
        if (
            len(self.record_sha256_by_run_id) != 38
            or sum(self.terminal_count_by_category.values()) != 38
        ):
            raise ValueError("ablation execution report counts are incomplete")
        return self


class ExecutionCompleteSeal(ExecutionModel):
    schema_version: Literal["phase5b.execution-complete-seal.v1"]
    evaluation_version: EvaluationVersion
    source_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    execution_freeze_sha256: str = Field(pattern=_SHA256_PATTERN)
    execution_schedule_sha256: str = Field(pattern=_SHA256_PATTERN)
    ablation_registry_sha256: str = Field(pattern=_SHA256_PATTERN)
    execution_report_sha256: str = Field(pattern=_SHA256_PATTERN)
    ablation_report_sha256: str = Field(pattern=_SHA256_PATTERN)
    completed_main_runs: Literal[180]
    completed_ablation_runs: Literal[38]
    main_evaluation_ready: Literal[True] = True
    ablation_slot_count: Literal[38] = 38
    ablation_implementation_available: Literal[False] = False
    ablation_evidence_available: Literal[False] = False
    ablation_primary_eligible: Literal[False] = False
    ablation_disposition: Literal[
        "ABLATION_NOT_IMPLEMENTED_IN_FROZEN_HARNESS"
    ] = "ABLATION_NOT_IMPLEMENTED_IN_FROZEN_HARNESS"
    terminal_count_by_category: dict[str, StrictInt]
    provider_network_calls: StrictInt = Field(ge=0, le=218)
    model_calls: StrictInt = Field(ge=0)
    tool_calls: StrictInt = Field(ge=0)
    combined_tokens: StrictInt = Field(ge=0)
    failure_count: StrictInt = Field(ge=0, le=218)
    all_failures_retained: Literal[True]
    ground_truth_read: Literal[False]
    from_state: Literal["EXECUTION_STARTED"]
    to_state: Literal["EXECUTION_COMPLETE"]
    create_once: Literal[True]


class ExecutionUnblindingRecord(ExecutionModel):
    schema_version: Literal["phase5b.unblinding-record.v1"]
    evaluation_version: EvaluationVersion
    protocol_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    execution_source_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    protocol_freeze_manifest_sha256: str = Field(pattern=_SHA256_PATTERN)
    execution_freeze_sha256: str = Field(pattern=_SHA256_PATTERN)
    execution_schedule_sha256: str = Field(pattern=_SHA256_PATTERN)
    hidden_pack_manifest_sha256: str = Field(pattern=_SHA256_PATTERN)
    agent_visible_pack_sha256: str = Field(pattern=_SHA256_PATTERN)
    ground_truth_pack_sha256: str = Field(pattern=_SHA256_PATTERN)
    execution_report_sha256: str = Field(pattern=_SHA256_PATTERN)
    ablation_report_sha256: str = Field(pattern=_SHA256_PATTERN)
    execution_complete_seal_sha256: str = Field(pattern=_SHA256_PATTERN)
    completed_main_runs: Literal[180]
    completed_ablation_runs: Literal[38]
    main_evaluation_ready: Literal[True] = True
    ablation_slot_count: Literal[38] = 38
    ablation_implementation_available: Literal[False] = False
    ablation_evidence_available: Literal[False] = False
    ablation_primary_eligible: Literal[False] = False
    ablation_disposition: Literal[
        "ABLATION_NOT_IMPLEMENTED_IN_FROZEN_HARNESS"
    ] = "ABLATION_NOT_IMPLEMENTED_IN_FROZEN_HARNESS"
    from_state: Literal["EXECUTION_COMPLETE"]
    to_state: Literal["UNBLINDED"]
    irreversible: Literal[True]
    create_once: Literal[True]
