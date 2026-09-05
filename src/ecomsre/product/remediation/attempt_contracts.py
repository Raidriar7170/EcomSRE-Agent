"""Attempt projections, append-only gates and committed write-intent contracts."""

from datetime import datetime
from enum import Enum
from typing import Literal, Self

from pydantic import ConfigDict, Field, model_validator

from ecomsre.product.contracts import ProductModelV1
from ecomsre.product.remediation.contracts import SealedRemediationModelV1, Sha256
from ecomsre.product.remediation.state import DenialReasonV1


class AttemptStateV1(str, Enum):
    CANDIDATE_CREATED = "CANDIDATE_CREATED"
    APPROVED = "APPROVED"
    STATE_BOUND = "STATE_BOUND"
    AUTHORIZED = "AUTHORIZED"
    WRITE_INTENT_COMMITTED = "WRITE_INTENT_COMMITTED"
    EXECUTING = "EXECUTING"
    APPLIED = "APPLIED"
    VERIFYING = "VERIFYING"
    RECOVERED = "RECOVERED"
    CANDIDATE_INELIGIBLE = "CANDIDATE_INELIGIBLE"
    APPROVAL_EXPIRED = "APPROVAL_EXPIRED"
    APPROVAL_REVOKED = "APPROVAL_REVOKED"
    STATE_DRIFTED = "STATE_DRIFTED"
    NO_LONGER_APPLICABLE = "NO_LONGER_APPLICABLE"
    AUTHORIZATION_DENIED = "AUTHORIZATION_DENIED"
    AUTHORIZATION_EXPIRED = "AUTHORIZATION_EXPIRED"
    EXECUTION_FAILED = "EXECUTION_FAILED"
    OUTCOME_UNKNOWN = "OUTCOME_UNKNOWN"
    VERIFICATION_FAILED = "VERIFICATION_FAILED"
    ESCALATED = "ESCALATED"
    CANCELLED_BEFORE_WRITE = "CANCELLED_BEFORE_WRITE"


TERMINAL_STATES = frozenset(AttemptStateV1) - {
    AttemptStateV1.CANDIDATE_CREATED,
    AttemptStateV1.APPROVED,
    AttemptStateV1.STATE_BOUND,
    AttemptStateV1.AUTHORIZED,
    AttemptStateV1.WRITE_INTENT_COMMITTED,
    AttemptStateV1.EXECUTING,
    AttemptStateV1.APPLIED,
    AttemptStateV1.VERIFYING,
}


class AttemptRequestV1(ProductModelV1):
    model_config = ConfigDict(extra="forbid", frozen=True)
    approval_id: str = Field(pattern=r"^appr-[0-9a-f]{24}$")


class RemediationAttemptV1(SealedRemediationModelV1):
    seal_field = "attempt_sha256"
    schema_version: Literal["ecomsre.product.remediation-attempt.v1"] = (
        "ecomsre.product.remediation-attempt.v1"
    )
    attempt_id: str = Field(pattern=r"^attempt-[0-9a-f]{24}$")
    environment_id: str = Field(pattern=r"^env-[0-9a-f]{24}$")
    target_logical_service: Literal["payment"] = "payment"
    candidate_id: str = Field(pattern=r"^cand-[0-9a-f]{24}$")
    candidate_sha256: Sha256
    approval_sha256: Sha256
    approval_id: str = Field(pattern=r"^appr-[0-9a-f]{24}$")
    authorization_id: str | None = Field(default=None, pattern=r"^auth-[0-9a-f]{24}$")
    authorization_sha256: Sha256 | None = None
    write_intent_sha256: Sha256 | None = None
    state: AttemptStateV1
    active_lease_owner: str | None = Field(
        default=None, pattern=r"^lease-[0-9a-f]{24}$"
    )
    lease_expires_at: datetime | None = None
    lease_generation: int = Field(default=0, strict=True, ge=0)
    write_intent_id: str | None = Field(default=None, pattern=r"^intent-[0-9a-f]{24}$")
    forward_write_count: int = Field(default=0, strict=True, ge=0, le=1)
    terminal: AttemptStateV1 | None = None
    safe_error_code: DenialReasonV1 | None = None
    final_disposition: Literal["PENDING", "NO_WRITE", "ESCALATE_HUMAN", "RECOVERED"] = (
        "PENDING"
    )
    revision: int = Field(default=0, strict=True, ge=0)
    created_at: datetime
    updated_at: datetime
    attempt_sha256: Sha256

    @model_validator(mode="after")
    def require_state_consistency(self) -> Self:
        if (self.authorization_id is None) != (self.authorization_sha256 is None) or (
            self.write_intent_id is None
        ) != (self.write_intent_sha256 is None):
            raise ValueError("attempt parent digest is partial")
        if (self.state in TERMINAL_STATES) != (self.terminal is not None):
            raise ValueError("attempt terminal classification differs")
        if self.terminal is not None and self.terminal != self.state:
            raise ValueError("attempt terminal differs from state")
        if self.updated_at < self.created_at:
            raise ValueError("attempt update precedes creation")
        if (self.active_lease_owner is None) != (self.lease_expires_at is None):
            raise ValueError("attempt lease is partial")
        if (
            self.state
            in {
                AttemptStateV1.WRITE_INTENT_COMMITTED,
                AttemptStateV1.EXECUTING,
                AttemptStateV1.APPLIED,
                AttemptStateV1.VERIFYING,
                AttemptStateV1.RECOVERED,
            }
            and self.write_intent_id is None
        ):
            raise ValueError("post-intent state lacks intent")
        return self


class AttemptCreationRecordV1(SealedRemediationModelV1):
    """Immutable idempotency anchor; the separate attempt status may evolve."""

    seal_field = "creation_sha256"
    schema_version: Literal["ecomsre.product.remediation-attempt-creation.v1"] = (
        "ecomsre.product.remediation-attempt-creation.v1"
    )
    attempt_id: str = Field(pattern=r"^attempt-[0-9a-f]{24}$")
    candidate_id: str = Field(pattern=r"^cand-[0-9a-f]{24}$")
    candidate_sha256: Sha256
    approval_sha256: Sha256
    approval_id: str = Field(pattern=r"^appr-[0-9a-f]{24}$")
    initial_attempt_sha256: Sha256
    created_at: datetime
    creation_sha256: Sha256


class WriteIntentV1(SealedRemediationModelV1):
    seal_field = "write_intent_sha256"
    schema_version: Literal["ecomsre.product.remediation-write-intent.v1"] = (
        "ecomsre.product.remediation-write-intent.v1"
    )
    write_intent_id: str = Field(pattern=r"^intent-[0-9a-f]{24}$")
    attempt_id: str = Field(pattern=r"^attempt-[0-9a-f]{24}$")
    attempt_sha256: Sha256
    authorization_id: str = Field(pattern=r"^auth-[0-9a-f]{24}$")
    authorization_sha256: Sha256
    runbook_sha256: Sha256
    target_logical_service: Literal["payment"] = "payment"
    step_id: Literal["RESTORE_BASELINE_CONFIGURATION"] = (
        "RESTORE_BASELINE_CONFIGURATION"
    )
    before_state_snapshot_id: str = Field(pattern=r"^snap-[0-9a-f]{24}$")
    before_state_sha256: Sha256
    status: Literal["COMMITTED"] = "COMMITTED"
    committed_at: datetime
    created_at: datetime
    write_intent_sha256: Sha256

    @model_validator(mode="after")
    def require_creation_anchor(self) -> Self:
        if self.committed_at != self.created_at:
            raise ValueError("write intent creation anchor differs")
        return self


class RemediationDecisionEventV1(SealedRemediationModelV1):
    seal_field = "event_sha256"
    schema_version: Literal["ecomsre.product.remediation-decision-event.v1"] = (
        "ecomsre.product.remediation-decision-event.v1"
    )
    attempt_id: str = Field(pattern=r"^attempt-[0-9a-f]{24}$")
    ordinal: int = Field(strict=True, ge=1)
    gate: Literal[
        "CANDIDATE",
        "APPROVAL",
        "OWNERSHIP",
        "BASELINE",
        "DRIFT",
        "ACTIVE_TRANSACTION",
        "STATE_BINDING",
        "AUTHORIZATION",
        "LEASE",
        "WRITE_INTENT",
        "RECONCILIATION",
        "EXECUTOR",
        "RECEIPT",
        "RECOVERY_WINDOW",
        "VERIFICATION",
        "CANCELLATION",
    ]
    outcome: Literal["PASS", "DENY", "ESCALATE"]
    state: AttemptStateV1
    reason_code: DenialReasonV1 | None = None
    previous_event_sha256: Sha256
    attempt_sha256: Sha256
    evidence_refs: tuple[Sha256, ...] = ()
    elapsed_ms: float = Field(ge=0, allow_inf_nan=False)
    created_at: datetime
    event_sha256: Sha256
