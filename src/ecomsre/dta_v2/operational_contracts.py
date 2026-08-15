"""Offline operational contracts for DTA v2 admission and execution."""

from __future__ import annotations

from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Literal

from pydantic import Field, StrictBool, StrictInt, model_validator

from ecomsre.dta_v2.contracts import (
    ActionDisposition,
    DtaModel,
    Identifier,
    Precondition,
    RunId,
    RunbookId,
    RunbookStepId,
    Sha256,
    semantic_sha256,
)


def _require_utc(value: datetime, *, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError(f"{field_name} must be timezone-aware UTC")


class DockerBoundary(str, Enum):
    LOCAL_UNIX = "LOCAL_UNIX"
    REMOTE = "REMOTE"


class OwnershipStatus(str, Enum):
    PROVEN = "PROVEN"
    UNKNOWN = "UNKNOWN"
    MISMATCH = "MISMATCH"


class ServiceRuntimeState(str, Enum):
    RUNNING_HEALTHY = "RUNNING_HEALTHY"
    RUNNING_UNHEALTHY = "RUNNING_UNHEALTHY"
    STOPPED = "STOPPED"
    UNKNOWN = "UNKNOWN"


class PreconditionObservation(DtaModel):
    precondition: Precondition
    satisfied: StrictBool


class CurrentStateSnapshot(DtaModel):
    schema_version: Literal["dta-v2.current-state-snapshot.v1"]
    run_id: RunId
    attempt_id: Identifier
    docker_boundary: DockerBoundary
    docker_context_identity: Sha256
    daemon_identity: Sha256
    sandbox_identity: Identifier
    ownership_digest: Sha256
    ownership_status: OwnershipStatus
    target_logical_service: Identifier
    service_runtime_state: ServiceRuntimeState
    configuration_state_digest: Sha256 | None
    baseline_digest: Sha256
    active_transaction_count: StrictInt = Field(ge=0)
    prior_forward_step_count: StrictInt = Field(ge=0)
    preconditions: tuple[PreconditionObservation, ...] = Field(
        min_length=1,
        max_length=8,
    )
    observed_at_start: datetime
    observed_at_end: datetime
    observation_monotonic_duration_ms: StrictInt = Field(ge=0)
    snapshot_sha256: Sha256

    @model_validator(mode="after")
    def require_snapshot_semantics(self) -> CurrentStateSnapshot:
        _require_utc(self.observed_at_start, field_name="observed_at_start")
        _require_utc(self.observed_at_end, field_name="observed_at_end")
        if self.observed_at_end < self.observed_at_start:
            raise ValueError("observation window ends before it starts")
        keys = tuple(item.precondition for item in self.preconditions)
        if len(keys) != len(set(keys)):
            raise ValueError("current-state preconditions contain duplicates")
        if keys != tuple(sorted(keys, key=lambda item: item.value)):
            raise ValueError("current-state preconditions are not canonical")
        expected = semantic_sha256(
            self.model_dump(mode="json", exclude={"snapshot_sha256"})
        )
        if self.snapshot_sha256 != expected:
            raise ValueError("snapshot digest does not bind current state")
        return self


def build_current_state_snapshot(
    *,
    run_id: str,
    attempt_id: str,
    docker_boundary: DockerBoundary,
    docker_context_identity: str,
    daemon_identity: str,
    sandbox_identity: str,
    ownership_digest: str,
    ownership_status: OwnershipStatus,
    target_logical_service: str,
    service_runtime_state: ServiceRuntimeState,
    configuration_state_digest: str | None,
    baseline_digest: str,
    active_transaction_count: int,
    prior_forward_step_count: int,
    preconditions: tuple[PreconditionObservation, ...],
    observed_at_start: datetime,
    observed_at_end: datetime,
    observation_monotonic_duration_ms: int,
) -> CurrentStateSnapshot:
    ordered = tuple(sorted(preconditions, key=lambda item: item.precondition.value))
    payload: dict[str, Any] = {
        "schema_version": "dta-v2.current-state-snapshot.v1",
        "run_id": run_id,
        "attempt_id": attempt_id,
        "docker_boundary": docker_boundary,
        "docker_context_identity": docker_context_identity,
        "daemon_identity": daemon_identity,
        "sandbox_identity": sandbox_identity,
        "ownership_digest": ownership_digest,
        "ownership_status": ownership_status,
        "target_logical_service": target_logical_service,
        "service_runtime_state": service_runtime_state,
        "configuration_state_digest": configuration_state_digest,
        "baseline_digest": baseline_digest,
        "active_transaction_count": active_transaction_count,
        "prior_forward_step_count": prior_forward_step_count,
        "preconditions": ordered,
        "observed_at_start": observed_at_start,
        "observed_at_end": observed_at_end,
        "observation_monotonic_duration_ms": observation_monotonic_duration_ms,
    }
    draft = CurrentStateSnapshot.model_construct(
        **payload,
        snapshot_sha256="0" * 64,
    )
    return CurrentStateSnapshot.model_validate(
        {
            **payload,
            "snapshot_sha256": semantic_sha256(
                draft.model_dump(mode="json", exclude={"snapshot_sha256"})
            ),
        }
    )


class AdmissionVerdict(str, Enum):
    ALLOW = "ALLOW"
    DENY = "DENY"


class AdmissionReasonCode(str, Enum):
    ALLOWED = "ALLOWED"
    PROPOSAL_BINDING_INVALID = "PROPOSAL_BINDING_INVALID"
    REGISTRY_MISMATCH = "REGISTRY_MISMATCH"
    RUNBOOK_MISMATCH = "RUNBOOK_MISMATCH"
    EVIDENCE_COVERAGE_INVALID = "EVIDENCE_COVERAGE_INVALID"
    PARAMETERS_INVALID = "PARAMETERS_INVALID"
    TARGET_MISMATCH = "TARGET_MISMATCH"
    OWNERSHIP_NOT_PROVEN = "OWNERSHIP_NOT_PROVEN"
    REMOTE_DOCKER = "REMOTE_DOCKER"
    PRECONDITION_FALSE = "PRECONDITION_FALSE"
    RISK_DENIED = "RISK_DENIED"
    AUTHORIZATION_EXPIRED = "AUTHORIZATION_EXPIRED"
    AUTHORIZATION_BINDING_MISMATCH = "AUTHORIZATION_BINDING_MISMATCH"
    SECOND_TRANSACTION = "SECOND_TRANSACTION"
    STEP_CAP_EXCEEDED = "STEP_CAP_EXCEEDED"


class OperationalAdmission(DtaModel):
    schema_version: Literal["dta-v2.operational-admission.v1"]
    verdict: AdmissionVerdict
    reason_codes: tuple[AdmissionReasonCode, ...] = Field(min_length=1)
    current_state_sha256: Sha256
    proposal_sha256: Sha256
    candidate_set_sha256: Sha256
    resolved_evidence_sha256: Sha256
    registry_sha256: Sha256
    runbook_sha256: Sha256
    authorization_sha256: Sha256
    admission_sha256: Sha256

    @model_validator(mode="after")
    def require_admission_semantics(self) -> OperationalAdmission:
        if self.verdict is AdmissionVerdict.ALLOW:
            if self.reason_codes != (AdmissionReasonCode.ALLOWED,):
                raise ValueError("ALLOW admission must carry only ALLOWED")
        elif AdmissionReasonCode.ALLOWED in self.reason_codes:
            raise ValueError("DENY admission cannot carry ALLOWED")
        if len(self.reason_codes) != len(set(self.reason_codes)):
            raise ValueError("admission reason codes contain duplicates")
        expected = semantic_sha256(
            self.model_dump(mode="json", exclude={"admission_sha256"})
        )
        if self.admission_sha256 != expected:
            raise ValueError("admission digest does not bind the verdict")
        return self


def build_operational_admission(
    *,
    verdict: AdmissionVerdict,
    reason_codes: tuple[AdmissionReasonCode, ...],
    current_state_sha256: str,
    proposal_sha256: str,
    candidate_set_sha256: str,
    resolved_evidence_sha256: str,
    registry_sha256: str,
    runbook_sha256: str,
    authorization_sha256: str,
) -> OperationalAdmission:
    payload: dict[str, Any] = {
        "schema_version": "dta-v2.operational-admission.v1",
        "verdict": verdict,
        "reason_codes": reason_codes,
        "current_state_sha256": current_state_sha256,
        "proposal_sha256": proposal_sha256,
        "candidate_set_sha256": candidate_set_sha256,
        "resolved_evidence_sha256": resolved_evidence_sha256,
        "registry_sha256": registry_sha256,
        "runbook_sha256": runbook_sha256,
        "authorization_sha256": authorization_sha256,
    }
    draft = OperationalAdmission.model_construct(
        **payload,
        admission_sha256="0" * 64,
    )
    return OperationalAdmission.model_validate(
        {
            **payload,
            "admission_sha256": semantic_sha256(
                draft.model_dump(mode="json", exclude={"admission_sha256"})
            ),
        }
    )


class StepOutcome(str, Enum):
    APPLIED = "APPLIED"
    FAILED = "FAILED"


class StepReceipt(DtaModel):
    schema_version: Literal["dta-v2.step-receipt.v1"]
    run_id: RunId
    attempt_id: Identifier
    transaction_id: Identifier
    step_ordinal: StrictInt = Field(ge=1, le=2)
    step_id: RunbookStepId
    target: Identifier
    before_state_digest: Sha256
    after_state_digest: Sha256
    start_time: datetime
    end_time: datetime
    outcome: StepOutcome
    error_code: Identifier | None
    receipt_sha256: Sha256

    @model_validator(mode="after")
    def require_receipt_semantics(self) -> StepReceipt:
        _require_utc(self.start_time, field_name="start_time")
        _require_utc(self.end_time, field_name="end_time")
        if self.end_time < self.start_time:
            raise ValueError("step receipt ends before it starts")
        if self.outcome is StepOutcome.APPLIED and self.error_code is not None:
            raise ValueError("applied step cannot carry an error code")
        if self.outcome is StepOutcome.FAILED and self.error_code is None:
            raise ValueError("failed step requires an error code")
        if (
            self.outcome is StepOutcome.APPLIED
            and self.before_state_digest == self.after_state_digest
        ):
            raise ValueError("applied step must change state")
        if (
            self.outcome is StepOutcome.FAILED
            and self.before_state_digest != self.after_state_digest
        ):
            raise ValueError("failed step must preserve state")
        expected = semantic_sha256(
            self.model_dump(mode="json", exclude={"receipt_sha256"})
        )
        if self.receipt_sha256 != expected:
            raise ValueError("receipt digest does not bind the step")
        return self


class VerificationOutcome(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    UNKNOWN = "UNKNOWN"


class VerificationResult(DtaModel):
    schema_version: Literal["dta-v2.verification-result.v1"]
    run_id: RunId
    attempt_id: Identifier
    transaction_id: Identifier
    runbook_id: RunbookId
    verifier_id: Identifier
    outcome: VerificationOutcome
    infrastructure_passed: StrictBool
    business_sli_passed: StrictBool
    receipt_sha256s: tuple[Sha256, ...] = Field(min_length=1, max_length=2)
    reason_codes: tuple[Identifier, ...] = Field(min_length=1, max_length=8)
    verification_sha256: Sha256

    @model_validator(mode="after")
    def require_verification_semantics(self) -> VerificationResult:
        if len(self.receipt_sha256s) != len(set(self.receipt_sha256s)):
            raise ValueError("verification receipts contain duplicates")
        if self.outcome is VerificationOutcome.PASS and not (
            self.infrastructure_passed and self.business_sli_passed
        ):
            raise ValueError("passing verification requires both verdicts")
        expected = semantic_sha256(
            self.model_dump(mode="json", exclude={"verification_sha256"})
        )
        if self.verification_sha256 != expected:
            raise ValueError("verification digest does not bind the result")
        return self


class ExecutionTerminal(str, Enum):
    RECOVERED = "RECOVERED"
    EXECUTION_FAILED = "EXECUTION_FAILED"
    PARTIALLY_APPLIED = "PARTIALLY_APPLIED"
    VERIFICATION_FAILED = "VERIFICATION_FAILED"


class ExecutionTransaction(DtaModel):
    schema_version: Literal["dta-v2.execution-transaction.v1"]
    run_id: RunId
    attempt_id: Identifier
    transaction_id: Identifier
    runbook_id: RunbookId
    target: Identifier
    proposal_sha256: Sha256
    admission_sha256: Sha256
    authorization_sha256: Sha256
    maximum_forward_steps: StrictInt = Field(ge=1, le=2)
    forward_step_count: StrictInt = Field(ge=1, le=2)
    receipts: tuple[StepReceipt, ...] = Field(min_length=1, max_length=2)
    verification: VerificationResult | None
    terminal: ExecutionTerminal
    final_disposition: ActionDisposition
    transaction_sha256: Sha256

    @model_validator(mode="after")
    def require_transaction_semantics(self) -> ExecutionTransaction:
        if len(self.receipts) != self.forward_step_count:
            raise ValueError("transaction count differs from its receipts")
        if self.forward_step_count > self.maximum_forward_steps:
            raise ValueError("transaction exceeds the forward-step cap")
        for ordinal, receipt in enumerate(self.receipts, start=1):
            if (
                receipt.run_id != self.run_id
                or receipt.attempt_id != self.attempt_id
                or receipt.transaction_id != self.transaction_id
                or receipt.target != self.target
                or receipt.step_ordinal != ordinal
            ):
                raise ValueError("step receipt differs from the transaction")
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
        observed_steps = tuple(receipt.step_id for receipt in self.receipts)
        if observed_steps != expected_steps[: len(observed_steps)]:
            raise ValueError("transaction steps differ from the frozen Runbook")
        if any(
            previous.after_state_digest != following.before_state_digest
            for previous, following in zip(
                self.receipts,
                self.receipts[1:],
                strict=False,
            )
        ):
            raise ValueError("transaction receipts break state continuity")
        if self.terminal is ExecutionTerminal.RECOVERED:
            if (
                self.verification is None
                or self.verification.outcome is not VerificationOutcome.PASS
                or self.final_disposition is not ActionDisposition.EXECUTE_RUNBOOK
                or observed_steps != expected_steps
                or any(
                    receipt.outcome is not StepOutcome.APPLIED
                    for receipt in self.receipts
                )
            ):
                raise ValueError("recovered transaction requires passing verification")
        else:
            if self.final_disposition is not ActionDisposition.ESCALATE_HUMAN:
                raise ValueError("failed transaction must escalate")
            if self.terminal is ExecutionTerminal.PARTIALLY_APPLIED:
                if (
                    self.runbook_id is not RunbookId.MITIGATE_MEMORY_LEAK
                    or observed_steps != expected_steps
                    or tuple(receipt.outcome for receipt in self.receipts)
                    != (StepOutcome.APPLIED, StepOutcome.FAILED)
                    or self.verification is not None
                ):
                    raise ValueError("partial transaction has invalid Email semantics")
            elif self.terminal is ExecutionTerminal.EXECUTION_FAILED:
                if (
                    len(self.receipts) != 1
                    or self.receipts[0].outcome is not StepOutcome.FAILED
                    or self.verification is not None
                ):
                    raise ValueError("execution failure has invalid receipt semantics")
            elif self.terminal is ExecutionTerminal.VERIFICATION_FAILED:
                if (
                    self.verification is None
                    or self.verification.outcome is VerificationOutcome.PASS
                    or observed_steps != expected_steps
                    or any(
                        receipt.outcome is not StepOutcome.APPLIED
                        for receipt in self.receipts
                    )
                ):
                    raise ValueError("verification failure has invalid semantics")
        if self.verification is not None:
            trusted_verifier = {
                RunbookId.ROLLBACK_CONFIGURATION: "ConfigurationRecoveryVerifier",
                RunbookId.RESTART_SERVICE: "ServiceRecoveryVerifier",
                RunbookId.MITIGATE_MEMORY_LEAK: "MemoryLeakRecoveryVerifier",
            }[self.runbook_id]
            if (
                self.verification.run_id != self.run_id
                or self.verification.attempt_id != self.attempt_id
                or self.verification.transaction_id != self.transaction_id
                or self.verification.runbook_id is not self.runbook_id
                or self.verification.verifier_id != trusted_verifier
            ):
                raise ValueError("verification identity differs from the transaction")
            if self.verification.receipt_sha256s != tuple(
                receipt.receipt_sha256 for receipt in self.receipts
            ):
                raise ValueError("verification is outside the transaction receipts")
        expected = semantic_sha256(
            self.model_dump(mode="json", exclude={"transaction_sha256"})
        )
        if self.transaction_sha256 != expected:
            raise ValueError("transaction digest does not bind the result")
        return self
