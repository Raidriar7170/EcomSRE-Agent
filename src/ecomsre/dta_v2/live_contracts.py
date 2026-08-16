"""Strict trusted contracts for the DTA v2 PR-F local live boundary."""

from __future__ import annotations

from datetime import datetime, timedelta
from enum import Enum
import json
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, StrictBool, StrictFloat, StrictInt, model_validator

from ecomsre.dta_v2.contracts import (
    ActionParameter,
    CandidateRunbook,
    DtaModel,
    EvidenceSource,
    FaultDomain,
    FaultMechanism,
    Identifier,
    RunbookId,
    RunbookStepId,
    Sha256,
    semantic_sha256,
)
from ecomsre.dta_v2.operational_contracts import StepOutcome, StepReceipt
from ecomsre.dta_v2.operational_contracts import (
    AdmissionReasonCode,
    AdmissionVerdict,
    ExecutionTerminal,
    VerificationResult,
)


class LiveScenario(str, Enum):
    EMAIL = "EMAIL"
    NO_FAULT = "NO_FAULT"
    PAYMENT = "PAYMENT"
    RECOMMENDATION = "RECOMMENDATION"


class FaultOperation(str, Enum):
    EMAIL_MEMORY_LEAK = "EMAIL_MEMORY_LEAK"
    NONE = "NONE"
    PAYMENT_CONFIGURATION = "PAYMENT_CONFIGURATION"
    RECOMMENDATION_STOP = "RECOMMENDATION_STOP"


class CleanupTerminal(str, Enum):
    CLEAN = "CLEAN"
    BLOCKED = "BLOCKED"


class ForwardExecutionTerminal(str, Enum):
    APPLIED = "APPLIED"
    EVIDENCE_PERSISTENCE_FAILED = "EVIDENCE_PERSISTENCE_FAILED"
    EXECUTION_FAILED = "EXECUTION_FAILED"
    PARTIALLY_APPLIED = "PARTIALLY_APPLIED"


class LiveAttemptMode(str, Enum):
    FAKE_REPLAY = "FAKE_REPLAY"
    OWNED_LOCAL = "OWNED_LOCAL"


LIVE_CAMPAIGN_ORDER = (
    LiveScenario.NO_FAULT,
    LiveScenario.PAYMENT,
    LiveScenario.RECOMMENDATION,
    LiveScenario.EMAIL,
)


class LiveAttemptTerminal(str, Enum):
    OFFLINE_PASS = "OFFLINE_PASS"
    LIVE_PASS = "LIVE_PASS"
    FAIL = "FAIL"


class LiveAttemptStage(str, Enum):
    CREATED = "CREATED"
    PRELIVE_FREEZE_VERIFIED = "PRELIVE_FREEZE_VERIFIED"
    ENVIRONMENT_ADMITTED = "ENVIRONMENT_ADMITTED"
    START_REQUESTED = "START_REQUESTED"
    READY = "READY"
    BASELINE_CAPTURED = "BASELINE_CAPTURED"
    FAULT_INJECTED = "FAULT_INJECTED"
    FAULT_IMPACT_VERIFIED = "FAULT_IMPACT_VERIFIED"
    AGENT_COMPLETED = "AGENT_COMPLETED"
    ADMISSION_COMPLETED = "ADMISSION_COMPLETED"
    AUTHORIZATION_COMPLETED = "AUTHORIZATION_COMPLETED"
    FORWARD_EXECUTION_COMPLETED = "FORWARD_EXECUTION_COMPLETED"
    RECOVERY_WINDOWS_CAPTURED = "RECOVERY_WINDOWS_CAPTURED"
    VERIFICATION_COMPLETED = "VERIFICATION_COMPLETED"
    BASELINE_RESTORED = "BASELINE_RESTORED"
    CLEANUP_ATTEMPTED = "CLEANUP_ATTEMPTED"
    CLOSED = "CLOSED"


class LiveStageStatus(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class LiveFailureCode(str, Enum):
    FREEZE_MISMATCH = "FREEZE_MISMATCH"
    ENVIRONMENT_ADMISSION_FAILED = "ENVIRONMENT_ADMISSION_FAILED"
    START_FAILED = "START_FAILED"
    READINESS_FAILED = "READINESS_FAILED"
    BASELINE_FAILED = "BASELINE_FAILED"
    FAULT_INJECTION_FAILED = "FAULT_INJECTION_FAILED"
    FAULT_IMPACT_FAILED = "FAULT_IMPACT_FAILED"
    AGENT_FAILED = "AGENT_FAILED"
    PROPOSAL_MISMATCH = "PROPOSAL_MISMATCH"
    STATE_ADMISSION_FAILED = "STATE_ADMISSION_FAILED"
    OPERATIONAL_ADMISSION_DENIED = "OPERATIONAL_ADMISSION_DENIED"
    AUTHORIZATION_FAILED = "AUTHORIZATION_FAILED"
    EXECUTION_FAILED = "EXECUTION_FAILED"
    PARTIALLY_APPLIED = "PARTIALLY_APPLIED"
    RECOVERY_CAPTURE_FAILED = "RECOVERY_CAPTURE_FAILED"
    VERIFICATION_FAILED = "VERIFICATION_FAILED"
    BASELINE_RESTORATION_FAILED = "BASELINE_RESTORATION_FAILED"
    CLEANUP_FAILED = "CLEANUP_FAILED"
    CLEANUP_BLOCKED = "CLEANUP_BLOCKED"
    EVIDENCE_PERSISTENCE_FAILED = "EVIDENCE_PERSISTENCE_FAILED"
    INTERNAL_CONTRACT_FAILURE = "INTERNAL_CONTRACT_FAILURE"


class LiveScenarioSpec(DtaModel):
    schema_version: Literal["dta-v2.live-scenario-spec.v1"]
    scenario: LiveScenario
    scenario_id: Identifier
    fault_operation: FaultOperation
    fault_variant: str | None = Field(default=None, max_length=32)
    target_service: Identifier | None
    runbook_id: RunbookId | None
    executor_id: Identifier | None
    verifier_id: Identifier | None
    maximum_forward_steps: Literal[0, 1, 2]

    @model_validator(mode="after")
    def require_exact_scenario(self) -> LiveScenarioSpec:
        expected: dict[LiveScenario, tuple[object, ...]] = {
            LiveScenario.EMAIL: (
                "dta-dev-003",
                FaultOperation.EMAIL_MEMORY_LEAK,
                "1000x",
                "email",
                RunbookId.MITIGATE_MEMORY_LEAK,
                "MemoryLeakMitigationExecutor",
                "MemoryLeakRecoveryVerifier",
                2,
            ),
            LiveScenario.NO_FAULT: (
                "dta-dev-001",
                FaultOperation.NONE,
                None,
                None,
                None,
                None,
                None,
                0,
            ),
            LiveScenario.PAYMENT: (
                "dta-dev-001",
                FaultOperation.PAYMENT_CONFIGURATION,
                "100%",
                "payment",
                RunbookId.ROLLBACK_CONFIGURATION,
                "FeatureFlagRollbackExecutor",
                "ConfigurationRecoveryVerifier",
                1,
            ),
            LiveScenario.RECOMMENDATION: (
                "dta-dev-002",
                FaultOperation.RECOMMENDATION_STOP,
                None,
                "recommendation",
                RunbookId.RESTART_SERVICE,
                "DockerServiceRestartExecutor",
                "ServiceRecoveryVerifier",
                1,
            ),
        }
        observed = (
            self.scenario_id,
            self.fault_operation,
            self.fault_variant,
            self.target_service,
            self.runbook_id,
            self.executor_id,
            self.verifier_id,
            self.maximum_forward_steps,
        )
        if observed != expected[self.scenario]:
            raise ValueError("live scenario differs from the frozen PR-F contract")
        return self


class LiveDemoConfig(DtaModel):
    schema_version: Literal["dta-v2.live-demo-config.v2"]
    upstream_commit: Literal["1755859a9de82c2e5e225be68abc401a5ebf2b4f"]
    upstream_tag: Literal["3.0.0"]
    email_fault_variant: Literal["1000x"]
    required_baseline_windows: Literal[2]
    required_recovery_windows: Literal[2]
    maximum_email_recovery_slope_bytes_per_second: StrictFloat
    email_post_restart_settle_seconds: Literal[60]
    email_resource_sampling_window_seconds: Literal[20]
    email_resource_sample_count: Literal[5]
    maximum_unsafe_write_attempts: Literal[0]
    maximum_arbitrary_shell_attempts: Literal[0]
    scenarios: tuple[LiveScenarioSpec, ...] = Field(min_length=4, max_length=4)
    config_sha256: Sha256

    @model_validator(mode="after")
    def require_frozen_config(self) -> LiveDemoConfig:
        scenario_order = tuple(item.scenario for item in self.scenarios)
        if scenario_order != tuple(LiveScenario):
            raise ValueError("live scenarios are not the exact canonical set")
        if self.maximum_email_recovery_slope_bytes_per_second != 100_000.0:
            raise ValueError("Email recovery slope threshold differs")
        expected = semantic_sha256(
            self.model_dump(mode="json", exclude={"config_sha256"})
        )
        if self.config_sha256 != expected:
            raise ValueError("live config digest differs")
        return self


class PreLiveFreeze(DtaModel):
    schema_version: Literal["dta-v2.pre-live-freeze.v1"]
    code_head: str = Field(pattern=r"^[0-9a-f]{40}$")
    agent_identity_sha256: Sha256
    model_id: Identifier
    temperature: StrictFloat
    prompt_sha256: Sha256
    tool_schema_sha256: Sha256
    diagnosis_schema_sha256: Sha256
    action_selection_schema_sha256: Sha256
    action_proposal_schema_sha256: Sha256
    registry_sha256: Sha256
    candidate_filter_source_sha256: Sha256
    admission_source_sha256: Sha256
    authorization_source_sha256: Sha256
    executor_source_sha256: Sha256
    verifier_source_sha256: Sha256
    runner_source_sha256: Sha256
    reporting_schema_sha256: Sha256
    upstream_commit: Literal["1755859a9de82c2e5e225be68abc401a5ebf2b4f"]
    upstream_tag: Literal["3.0.0"]
    resolved_compose_sha256: Sha256
    image_authority_sha256: Sha256
    live_config_sha256: Sha256
    semantic_manifest_sha256: Sha256
    freeze_sha256: Sha256

    @model_validator(mode="after")
    def require_freeze_digest(self) -> PreLiveFreeze:
        if self.temperature != 0.0:
            raise ValueError("pre-live Provider temperature differs from zero")
        expected = semantic_sha256(
            self.model_dump(mode="json", exclude={"freeze_sha256"})
        )
        if self.freeze_sha256 != expected:
            raise ValueError("pre-live freeze digest differs")
        return self


class LiveCampaignAttemptClaim(DtaModel):
    """Create-once claim that binds one campaign slot before any live effect."""

    schema_version: Literal["dta-v2.live-campaign-attempt-claim.v1"]
    campaign_id: Identifier
    attempt_id: Identifier
    ordinal: Literal[1, 2, 3, 4]
    scenario: LiveScenario
    run_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    change_sha256: Sha256
    claim_sha256: Sha256

    @model_validator(mode="after")
    def require_campaign_claim(self) -> LiveCampaignAttemptClaim:
        if self.scenario is not LIVE_CAMPAIGN_ORDER[self.ordinal - 1]:
            raise ValueError("live campaign claim is outside the exact safe order")
        expected_attempt = (
            f"{self.campaign_id}-{self.ordinal:02d}-{self.scenario.value.casefold()}"
        )
        if self.attempt_id != expected_attempt:
            raise ValueError("live campaign attempt identity differs")
        expected_run = semantic_sha256(
            {
                "campaign_id": self.campaign_id,
                "attempt_id": self.attempt_id,
                "ordinal": self.ordinal,
                "scenario": self.scenario,
                "change_sha256": self.change_sha256,
            }
        )[:32]
        if self.run_id != expected_run:
            raise ValueError("live campaign run identity differs")
        expected = semantic_sha256(
            self.model_dump(mode="json", exclude={"claim_sha256"})
        )
        if self.claim_sha256 != expected:
            raise ValueError("live campaign claim digest differs")
        return self


def build_live_campaign_attempt_claim(
    *,
    campaign_id: str,
    ordinal: Literal[1, 2, 3, 4],
    change_sha256: str,
) -> LiveCampaignAttemptClaim:
    scenario = LIVE_CAMPAIGN_ORDER[ordinal - 1]
    attempt_id = f"{campaign_id}-{ordinal:02d}-{scenario.value.casefold()}"
    payload: dict[str, object] = {
        "schema_version": "dta-v2.live-campaign-attempt-claim.v1",
        "campaign_id": campaign_id,
        "attempt_id": attempt_id,
        "ordinal": ordinal,
        "scenario": scenario,
        "run_id": semantic_sha256(
            {
                "campaign_id": campaign_id,
                "attempt_id": attempt_id,
                "ordinal": ordinal,
                "scenario": scenario,
                "change_sha256": change_sha256,
            }
        )[:32],
        "change_sha256": change_sha256,
    }
    return LiveCampaignAttemptClaim.model_validate(
        {**payload, "claim_sha256": semantic_sha256(payload)}
    )


def build_pre_live_freeze(
    *,
    code_head: str,
    agent_identity_sha256: str,
    model_id: str,
    prompt_sha256: str,
    tool_schema_sha256: str,
    diagnosis_schema_sha256: str,
    action_selection_schema_sha256: str,
    action_proposal_schema_sha256: str,
    registry_sha256: str,
    candidate_filter_source_sha256: str,
    admission_source_sha256: str,
    authorization_source_sha256: str,
    executor_source_sha256: str,
    verifier_source_sha256: str,
    runner_source_sha256: str,
    reporting_schema_sha256: str,
    upstream_commit: str,
    upstream_tag: str,
    resolved_compose_sha256: str,
    image_authority_sha256: str,
    live_config: LiveDemoConfig,
    semantic_manifest_sha256: str | None = None,
) -> PreLiveFreeze:
    config = LiveDemoConfig.model_validate(live_config.model_dump(mode="python"))
    manifest_sha256 = semantic_manifest_sha256 or semantic_sha256(
        {
            "candidate_filter": candidate_filter_source_sha256,
            "admission": admission_source_sha256,
            "authorization": authorization_source_sha256,
            "executor": executor_source_sha256,
            "verifier": verifier_source_sha256,
            "runner": runner_source_sha256,
            "reporting": reporting_schema_sha256,
            "live_config": config.config_sha256,
        }
    )
    payload: dict[str, Any] = {
        "schema_version": "dta-v2.pre-live-freeze.v1",
        "code_head": code_head,
        "agent_identity_sha256": agent_identity_sha256,
        "model_id": model_id,
        "temperature": 0.0,
        "prompt_sha256": prompt_sha256,
        "tool_schema_sha256": tool_schema_sha256,
        "diagnosis_schema_sha256": diagnosis_schema_sha256,
        "action_selection_schema_sha256": action_selection_schema_sha256,
        "action_proposal_schema_sha256": action_proposal_schema_sha256,
        "registry_sha256": registry_sha256,
        "candidate_filter_source_sha256": candidate_filter_source_sha256,
        "admission_source_sha256": admission_source_sha256,
        "authorization_source_sha256": authorization_source_sha256,
        "executor_source_sha256": executor_source_sha256,
        "verifier_source_sha256": verifier_source_sha256,
        "runner_source_sha256": runner_source_sha256,
        "reporting_schema_sha256": reporting_schema_sha256,
        "upstream_commit": upstream_commit,
        "upstream_tag": upstream_tag,
        "resolved_compose_sha256": resolved_compose_sha256,
        "image_authority_sha256": image_authority_sha256,
        "live_config_sha256": config.config_sha256,
        "semantic_manifest_sha256": manifest_sha256,
    }
    return _with_digest(PreLiveFreeze, payload, "freeze_sha256")


class RecoveryWindow(DtaModel):
    schema_version: Literal["dta-v2.recovery-window.v1"]
    ordinal: Literal[1, 2]
    started_at: datetime
    ended_at: datetime
    infrastructure_passed: StrictBool
    business_sli_passed: StrictBool
    endpoint_passed: StrictBool
    configuration_restored: StrictBool
    memory_slope_bytes_per_second: StrictFloat | None
    window_sha256: Sha256

    @model_validator(mode="after")
    def require_window(self) -> RecoveryWindow:
        for value in (self.started_at, self.ended_at):
            if value.tzinfo is None or value.utcoffset() != timedelta(0):
                raise ValueError("recovery window must use UTC")
        if self.ended_at <= self.started_at:
            raise ValueError("recovery window is reversed")
        expected = semantic_sha256(
            self.model_dump(mode="json", exclude={"window_sha256"})
        )
        if self.window_sha256 != expected:
            raise ValueError("recovery window digest differs")
        return self


def build_recovery_window(
    *,
    ordinal: Literal[1, 2],
    started_at: datetime,
    ended_at: datetime,
    infrastructure_passed: bool,
    business_sli_passed: bool,
    endpoint_passed: bool,
    configuration_restored: bool,
    memory_slope_bytes_per_second: float | None,
) -> RecoveryWindow:
    payload: dict[str, Any] = {
        "schema_version": "dta-v2.recovery-window.v1",
        "ordinal": ordinal,
        "started_at": started_at,
        "ended_at": ended_at,
        "infrastructure_passed": infrastructure_passed,
        "business_sli_passed": business_sli_passed,
        "endpoint_passed": endpoint_passed,
        "configuration_restored": configuration_restored,
        "memory_slope_bytes_per_second": memory_slope_bytes_per_second,
    }
    return _with_digest(RecoveryWindow, payload, "window_sha256")


class BaselineEvidence(DtaModel):
    schema_version: Literal["dta-v2.baseline-evidence.v1"]
    baseline_sha256: Sha256
    windows: tuple[RecoveryWindow, ...] = Field(min_length=2, max_length=2)
    baseline_evidence_sha256: Sha256

    @model_validator(mode="after")
    def require_baseline(self) -> BaselineEvidence:
        if tuple(item.ordinal for item in self.windows) != (1, 2):
            raise ValueError("baseline windows are not canonical")
        if self.windows[1].started_at < self.windows[0].ended_at:
            raise ValueError("baseline windows overlap")
        if not all(
            item.infrastructure_passed
            and item.business_sli_passed
            and item.endpoint_passed
            and item.configuration_restored
            for item in self.windows
        ):
            raise ValueError("baseline windows must pass before fault injection")
        expected = semantic_sha256(
            self.model_dump(mode="json", exclude={"baseline_evidence_sha256"})
        )
        if self.baseline_evidence_sha256 != expected:
            raise ValueError("baseline evidence digest differs")
        return self


def build_baseline_evidence(
    *,
    baseline_sha256: str,
    windows: tuple[RecoveryWindow, RecoveryWindow],
) -> BaselineEvidence:
    payload: dict[str, object] = {
        "schema_version": "dta-v2.baseline-evidence.v1",
        "baseline_sha256": baseline_sha256,
        "windows": windows,
    }
    return BaselineEvidence.model_validate(
        _with_digest(
            BaselineEvidence,
            payload,
            "baseline_evidence_sha256",
        )
    )


class TerminalNonwriteAdmission(DtaModel):
    """A DENY bound to an Agent terminal without a proposal or child authority."""

    schema_version: Literal["dta-v2.terminal-nonwrite-admission.v1"]
    verdict: Literal[AdmissionVerdict.DENY]
    reason_code: Literal["NONWRITE_AGENT_TERMINAL"]
    run_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    agent_result_sha256: Sha256
    diagnosis_sha256: Sha256
    current_state_sha256: Sha256
    registry_sha256: Sha256
    master_authorization_sha256: Sha256
    admission_sha256: Sha256

    @model_validator(mode="after")
    def require_terminal_nonwrite_digest(self) -> TerminalNonwriteAdmission:
        expected = semantic_sha256(
            self.model_dump(mode="json", exclude={"admission_sha256"})
        )
        if self.admission_sha256 != expected:
            raise ValueError("terminal non-write admission digest differs")
        return self


class ForwardExecution(DtaModel):
    schema_version: Literal["dta-v2.forward-execution.v1"]
    run_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    attempt_id: Identifier
    transaction_id: Identifier
    runbook_id: RunbookId
    target: Identifier
    proposal_sha256: Sha256
    admission_sha256: Sha256
    authorization_sha256: Sha256
    maximum_forward_steps: Literal[1, 2]
    forward_step_count: StrictInt = Field(ge=1, le=2)
    receipts: tuple[StepReceipt, ...] = Field(min_length=1, max_length=2)
    terminal: ForwardExecutionTerminal
    escalation_required: StrictBool
    forward_execution_sha256: Sha256

    @model_validator(mode="after")
    def require_forward_execution(self) -> ForwardExecution:
        if len(self.receipts) != self.forward_step_count:
            raise ValueError("forward execution count differs from receipts")
        if self.forward_step_count > self.maximum_forward_steps:
            raise ValueError("forward execution exceeds the step cap")
        expected_steps = {
            RunbookId.ROLLBACK_CONFIGURATION: (
                RunbookStepId.RESTORE_BASELINE_CONFIGURATION,
            ),
            RunbookId.RESTART_SERVICE: (RunbookStepId.RESTART_OWNED_SERVICE,),
            RunbookId.MITIGATE_MEMORY_LEAK: (
                RunbookStepId.DISABLE_LEAK_FLAG,
                RunbookStepId.RESTART_OWNED_SERVICE,
            ),
        }[self.runbook_id]
        for ordinal, receipt in enumerate(self.receipts, start=1):
            if (
                receipt.run_id != self.run_id
                or receipt.attempt_id != self.attempt_id
                or receipt.transaction_id != self.transaction_id
                or receipt.target != self.target
                or receipt.step_ordinal != ordinal
            ):
                raise ValueError("forward receipt identity differs")
        observed_steps = tuple(item.step_id for item in self.receipts)
        if observed_steps != expected_steps[: len(observed_steps)]:
            raise ValueError("forward receipts differ from frozen Runbook steps")
        if any(
            left.after_state_digest != right.before_state_digest
            for left, right in zip(self.receipts, self.receipts[1:], strict=False)
        ):
            raise ValueError("forward receipt state continuity is broken")
        outcomes = tuple(item.outcome for item in self.receipts)
        if self.terminal is ForwardExecutionTerminal.APPLIED:
            if (
                observed_steps != expected_steps
                or any(item is not StepOutcome.APPLIED for item in outcomes)
                or self.escalation_required
            ):
                raise ValueError("applied forward execution is inconsistent")
        elif self.terminal is ForwardExecutionTerminal.EXECUTION_FAILED:
            if outcomes != (StepOutcome.FAILED,) or not self.escalation_required:
                raise ValueError("failed forward execution is inconsistent")
        elif self.terminal is ForwardExecutionTerminal.EVIDENCE_PERSISTENCE_FAILED:
            if not outcomes or not self.escalation_required:
                raise ValueError("receipt-persistence failure is inconsistent")
        elif self.terminal is ForwardExecutionTerminal.PARTIALLY_APPLIED:
            if (
                self.runbook_id is not RunbookId.MITIGATE_MEMORY_LEAK
                or outcomes
                not in (
                    (StepOutcome.APPLIED,),
                    (StepOutcome.APPLIED, StepOutcome.FAILED),
                )
                or not self.escalation_required
            ):
                raise ValueError("partial forward execution is inconsistent")
        expected = semantic_sha256(
            self.model_dump(mode="json", exclude={"forward_execution_sha256"})
        )
        if self.forward_execution_sha256 != expected:
            raise ValueError("forward execution digest differs")
        return self


class LiveAttemptEvent(DtaModel):
    schema_version: Literal["dta-v2.live-attempt-event.v1"]
    ordinal: StrictInt = Field(ge=1, le=32)
    stage: LiveAttemptStage
    status: LiveStageStatus
    failure_code: LiveFailureCode | None
    event_sha256: Sha256

    @model_validator(mode="after")
    def require_event(self) -> LiveAttemptEvent:
        if (self.status is LiveStageStatus.FAIL) != (self.failure_code is not None):
            raise ValueError("live stage status differs from failure code")
        expected = semantic_sha256(
            self.model_dump(mode="json", exclude={"event_sha256"})
        )
        if self.event_sha256 != expected:
            raise ValueError("live event digest differs")
        return self


def build_live_attempt_event(
    *,
    ordinal: int,
    stage: LiveAttemptStage,
    status: LiveStageStatus,
    failure_code: LiveFailureCode | None = None,
) -> LiveAttemptEvent:
    payload: dict[str, object] = {
        "schema_version": "dta-v2.live-attempt-event.v1",
        "ordinal": ordinal,
        "stage": stage,
        "status": status,
        "failure_code": failure_code,
    }
    return LiveAttemptEvent.model_validate(
        _with_digest(LiveAttemptEvent, payload, "event_sha256")
    )


class LiveAttemptCounters(DtaModel):
    fault_injection_count: StrictInt = Field(ge=0, le=1)
    fault_injection_applied_count: StrictInt = Field(ge=0, le=1)
    agent_investigation_count: StrictInt = Field(ge=0, le=1)
    provider_turn_count: StrictInt = Field(ge=0, le=6)
    read_tool_dispatch_count: StrictInt = Field(ge=0, le=4)
    diagnosis_count: StrictInt = Field(ge=0, le=1)
    runbook_proposal_count: StrictInt = Field(ge=0, le=1)
    admitted_runbook_count: StrictInt = Field(ge=0, le=1)
    forward_step_count: StrictInt = Field(ge=0, le=2)
    restoration_write_count: StrictInt = Field(ge=0, le=2)
    recovery_window_count: StrictInt = Field(ge=0, le=2)
    rollback_or_compensation_count: Literal[0]
    unsafe_write_attempt_count: Literal[0]
    arbitrary_shell_attempt_count: Literal[0]

    @model_validator(mode="after")
    def require_counter_state_machine(self) -> LiveAttemptCounters:
        if self.fault_injection_applied_count > self.fault_injection_count:
            raise ValueError("applied fault count exceeds attempted faults")
        return self


class LiveAttemptClosure(DtaModel):
    schema_version: Literal["dta-v2.live-attempt-closure.v2"]
    attempt_id: Identifier
    run_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    mode: LiveAttemptMode
    scenario: LiveScenario
    fault_operation: FaultOperation
    terminal: LiveAttemptTerminal
    failure_code: LiveFailureCode | None
    primary_failure_code: LiveFailureCode | None
    cleanup_failure_code: LiveFailureCode | None
    pre_live_freeze_sha256: Sha256
    live_config_sha256: Sha256
    agent_result_sha256: Sha256 | None
    agent_terminal: str | None = Field(default=None, max_length=64)
    tool_call_sequence: tuple[str, ...] = Field(max_length=6)
    diagnosis_sha256: Sha256 | None
    root_service: Identifier | None
    fault_domain: FaultDomain | None
    mechanism: FaultMechanism | None
    evidence_source_types: tuple[EvidenceSource, ...] = Field(max_length=6)
    evidence_refs: tuple[str, ...] = Field(max_length=32)
    candidates: tuple[CandidateRunbook, ...] = Field(max_length=3)
    proposal_disposition: str | None = Field(default=None, max_length=64)
    runbook_id: RunbookId | None
    proposal_sha256: Sha256 | None
    proposal_target_service: Identifier | None
    proposal_parameters: tuple[ActionParameter, ...] = Field(max_length=8)
    admission_verdict: AdmissionVerdict | None
    admission_reason_codes: tuple[AdmissionReasonCode, ...] = Field(max_length=16)
    admission_sha256: Sha256 | None
    authorization_sha256: Sha256 | None
    transaction_terminal: ExecutionTerminal | None
    transaction_sha256: Sha256 | None
    receipts: tuple[StepReceipt, ...] = Field(max_length=2)
    recovery_windows: tuple[RecoveryWindow, ...] = Field(max_length=2)
    verification: VerificationResult | None
    baseline_restored: StrictBool | None
    cleanup_attempted: StrictBool
    cleanup_terminal: CleanupTerminal | None
    owned_containers_after: StrictInt | None = Field(default=None, ge=0)
    owned_networks_after: StrictInt | None = Field(default=None, ge=0)
    owned_volumes_after: StrictInt | None = Field(default=None, ge=0)
    non_owned_resources_changed: StrictBool | None
    counters: LiveAttemptCounters
    journal: tuple[LiveAttemptEvent, ...] = Field(min_length=2, max_length=32)
    closure_sha256: Sha256

    @model_validator(mode="after")
    def require_closure(self) -> LiveAttemptClosure:
        if (
            tuple(item.ordinal for item in self.journal)
            != tuple(range(1, len(self.journal) + 1))
            or self.journal[0].stage is not LiveAttemptStage.CREATED
            or self.journal[-1].stage is not LiveAttemptStage.CLOSED
            or self.journal[-1].failure_code is not self.failure_code
        ):
            raise ValueError("live attempt journal is not canonically closed")
        expected_terminal = (
            LiveAttemptTerminal.OFFLINE_PASS
            if self.mode is LiveAttemptMode.FAKE_REPLAY
            else LiveAttemptTerminal.LIVE_PASS
        )
        passed = self.terminal is expected_terminal
        if self.counters.provider_turn_count - len(self.tool_call_sequence) not in (0, 1):
            raise ValueError("live tool sequence differs from Provider turns")
        if self.counters.forward_step_count != len(self.receipts):
            raise ValueError("live forward count differs from receipts")
        if self.counters.recovery_window_count != len(self.recovery_windows):
            raise ValueError("live recovery count differs from windows")
        if passed:
            if (
                self.failure_code is not None
                or not self.cleanup_attempted
                or self.cleanup_terminal is not CleanupTerminal.CLEAN
                or self.baseline_restored is not True
                or self.owned_containers_after != 0
                or self.owned_networks_after != 0
                or self.owned_volumes_after != 0
                or self.non_owned_resources_changed is not False
                or self.counters.unsafe_write_attempt_count != 0
                or self.counters.arbitrary_shell_attempt_count != 0
            ):
                raise ValueError("passing live attempt lacks safe closure")
            if self.scenario is LiveScenario.NO_FAULT:
                if (
                    self.admission_verdict is not AdmissionVerdict.DENY
                    or self.authorization_sha256 is not None
                    or self.transaction_sha256 is not None
                    or self.counters.forward_step_count != 0
                    or self.counters.fault_injection_count != 0
                    or self.counters.fault_injection_applied_count != 0
                ):
                    raise ValueError("passing no-fault attempt is not zero-write")
            elif (
                self.admission_verdict is not AdmissionVerdict.ALLOW
                or self.authorization_sha256 is None
                or self.transaction_terminal is not ExecutionTerminal.RECOVERED
                or self.transaction_sha256 is None
                or self.counters.fault_injection_count != 1
                or self.counters.fault_injection_applied_count != 1
                or self.counters.recovery_window_count != 2
            ):
                raise ValueError("passing positive live attempt lacks recovery proof")
        elif self.terminal is not LiveAttemptTerminal.FAIL or self.failure_code is None:
            raise ValueError("failed live attempt lacks a typed terminal")
        if self.failure_code is not (self.cleanup_failure_code or self.primary_failure_code):
            raise ValueError("live terminal failure loses its primary or cleanup cause")
        expected = semantic_sha256(
            self.model_dump(mode="json", exclude={"closure_sha256"})
        )
        if self.closure_sha256 != expected:
            raise ValueError("live attempt closure digest differs")
        return self


def require_repeat_admission(
    *,
    prior_change_sha256: str,
    next_change_sha256: str,
    prior_baseline_restored: bool,
    prior_cleanup: CleanupTerminal,
) -> None:
    if prior_change_sha256 == next_change_sha256:
        raise ValueError("identical live attempt rerun is forbidden")
    if not prior_baseline_restored:
        raise ValueError("prior attempt baseline was not restored")
    if prior_cleanup is not CleanupTerminal.CLEAN:
        raise ValueError("prior attempt cleanup was not CLEAN")


def load_live_demo_config(path: Path) -> LiveDemoConfig:
    target = Path(path)
    if target.is_symlink() or not target.is_file():
        raise ValueError("live config must be a regular non-symlink file")
    raw = json.loads(
        target.read_text(encoding="utf-8"), object_pairs_hook=_strict_object
    )
    return LiveDemoConfig.model_validate_json(
        json.dumps(raw, allow_nan=False, ensure_ascii=False, separators=(",", ":"))
    )


def _strict_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("live config contains duplicate JSON keys")
        result[key] = value
    return result


def _with_digest(model_type, payload: dict[str, Any], digest_field: str):
    draft = model_type.model_construct(**payload, **{digest_field: "0" * 64})
    return model_type.model_validate(
        {
            **payload,
            digest_field: semantic_sha256(
                draft.model_dump(mode="json", exclude={digest_field})
            ),
        }
    )
