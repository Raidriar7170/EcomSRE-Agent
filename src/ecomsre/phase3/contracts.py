"""Closed, replay-only contracts for the lean Phase 3 MVP."""

from __future__ import annotations

import hashlib
import json
import re
from enum import Enum
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    field_validator,
    model_validator,
)

from ecomsre.phase1.contracts import FaultMechanism, RCADecision


_RUN_ID_RE = re.compile(r"^[0-9a-f]{32}$")
_EVIDENCE_REF_RE = re.compile(
    r"^evidence://[0-9a-f]{32}/(?:metrics|logs|traces|changes)/[0-9]{4}$"
)
_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


class Phase3Model(BaseModel):
    """Immutable closed-world Phase 3 value object."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)


def semantic_sha256(payload: object) -> str:
    """Return one deterministic semantic digest for a compact replay object."""

    encoded = json.dumps(
        payload,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _require_identifier(value: object, *, field_name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string")
    trimmed = value.strip()
    if _IDENTIFIER_RE.fullmatch(trimmed) is None:
        raise ValueError(f"{field_name} is not a bounded identifier")
    return trimmed


class ActionType(str, Enum):
    RESTORE_FROZEN_SERVICE_CONFIGURATION = "RESTORE_FROZEN_SERVICE_CONFIGURATION"


class ConfigurationState(str, Enum):
    FAULTED = "FAULTED"
    FROZEN = "FROZEN"


class PlannerReasonCode(str, Enum):
    RCA_NOT_CONFIRMED = "RCA_NOT_CONFIRMED"
    RCA_ACTION_MISMATCH = "RCA_ACTION_MISMATCH"
    EVIDENCE_UNRESOLVED = "EVIDENCE_UNRESOLVED"
    MISSING_EVIDENCE = "MISSING_EVIDENCE"
    RESOURCE_NOT_CURRENT_RUN = "RESOURCE_NOT_CURRENT_RUN"
    TARGET_NOT_REPLAY_ONLY = "TARGET_NOT_REPLAY_ONLY"
    PRE_STATE_MISMATCH = "PRE_STATE_MISMATCH"


class ApprovalMode(str, Enum):
    HUMAN = "HUMAN"
    LOCAL_TEST_AUTO_APPROVAL = "LOCAL_TEST_AUTO_APPROVAL"


class ApprovalOutcome(str, Enum):
    APPROVED = "APPROVED"
    DENIED = "DENIED"


class PolicyOutcome(str, Enum):
    ALLOW = "ALLOW"
    DENY = "DENY"


class PolicyReasonCode(str, Enum):
    ALLOWED = "ALLOWED"
    IDENTITY_MISMATCH = "IDENTITY_MISMATCH"
    ACTION_NOT_ALLOWLISTED = "ACTION_NOT_ALLOWLISTED"
    RCA_ACTION_MISMATCH = "RCA_ACTION_MISMATCH"
    EVIDENCE_SCOPE_INVALID = "EVIDENCE_SCOPE_INVALID"
    RESOURCE_UNOWNED = "RESOURCE_UNOWNED"
    TARGET_NOT_REPLAY_ONLY = "TARGET_NOT_REPLAY_ONLY"
    PRE_STATE_MISMATCH = "PRE_STATE_MISMATCH"
    STATE_VERSION_MISMATCH = "STATE_VERSION_MISMATCH"
    FORWARD_MUTATION_LIMIT_REACHED = "FORWARD_MUTATION_LIMIT_REACHED"
    ATTEMPT_CLOSED = "ATTEMPT_CLOSED"
    APPROVAL_IDENTITY_MISMATCH = "APPROVAL_IDENTITY_MISMATCH"
    APPROVAL_DENIED = "APPROVAL_DENIED"
    DUPLICATE_APPROVAL = "DUPLICATE_APPROVAL"
    LOCAL_TEST_AUTO_APPROVAL_FORBIDDEN = "LOCAL_TEST_AUTO_APPROVAL_FORBIDDEN"
    ROLLBACK_PRE_STATE_MISSING = "ROLLBACK_PRE_STATE_MISSING"


class MutationBehavior(str, Enum):
    APPLY = "APPLY"
    NOT_APPLIED = "NOT_APPLIED"
    FAIL = "FAIL"


class ExecutionOutcome(str, Enum):
    NOT_APPLIED = "NOT_APPLIED"
    APPLIED = "APPLIED"
    FAILED = "FAILED"


class ReplayHealthStatus(str, Enum):
    RECOVERED = "RECOVERED"
    FAILED = "FAILED"
    INCONCLUSIVE = "INCONCLUSIVE"


class VerificationOutcome(str, Enum):
    VERIFIED = "VERIFIED"
    FAILED = "FAILED"
    INCONCLUSIVE = "INCONCLUSIVE"


class VerificationReasonCode(str, Enum):
    VERIFIED = "VERIFIED"
    EXECUTION_NOT_APPLIED = "EXECUTION_NOT_APPLIED"
    POST_STATE_MISMATCH = "POST_STATE_MISMATCH"
    OWNERSHIP_CHANGED = "OWNERSHIP_CHANGED"
    STATE_VERSION_MISMATCH = "STATE_VERSION_MISMATCH"
    FIELD_CHANGE_MISMATCH = "FIELD_CHANGE_MISMATCH"
    FORWARD_COUNT_MISMATCH = "FORWARD_COUNT_MISMATCH"
    HEALTH_NOT_RECOVERED = "HEALTH_NOT_RECOVERED"
    HEALTH_INCONCLUSIVE = "HEALTH_INCONCLUSIVE"


class RollbackBehavior(str, Enum):
    RESTORE = "RESTORE"
    FAIL = "FAIL"


class RollbackOutcome(str, Enum):
    NOT_REQUIRED = "NOT_REQUIRED"
    RESTORED = "RESTORED"
    FAILED = "FAILED"


class TerminalOutcome(str, Enum):
    REMEDIATION_VERIFIED = "REMEDIATION_VERIFIED"
    NO_ACTION = "NO_ACTION"
    APPROVAL_DENIED = "APPROVAL_DENIED"
    POLICY_REJECTED = "POLICY_REJECTED"
    PRECONDITION_FAILED = "PRECONDITION_FAILED"
    VERIFICATION_FAILED_ROLLED_BACK = "VERIFICATION_FAILED_ROLLED_BACK"
    ROLLBACK_FAILED = "ROLLBACK_FAILED"
    UNSAFE = "UNSAFE"


class DiagnosisHandoff(Phase3Model):
    schema_version: Literal["phase3.diagnosis-handoff.v1"]
    run_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    incident_id: str
    decision: RCADecision
    root_service: str | None = None
    fault_mechanism: FaultMechanism | None = None
    supporting_evidence_refs: tuple[str, ...] = Field(max_length=64)
    missing_evidence: tuple[str, ...] = Field(max_length=32)

    @field_validator("incident_id", mode="before")
    @classmethod
    def validate_incident_id(cls, value: object) -> str:
        return _require_identifier(value, field_name="incident_id")

    @field_validator("supporting_evidence_refs")
    @classmethod
    def validate_evidence_refs(
        cls,
        values: tuple[str, ...],
    ) -> tuple[str, ...]:
        if len(values) != len(set(values)):
            raise ValueError("evidence references contain duplicates")
        if any(_EVIDENCE_REF_RE.fullmatch(value) is None for value in values):
            raise ValueError("evidence reference is malformed")
        return values

    @field_validator("missing_evidence")
    @classmethod
    def validate_missing_evidence(
        cls,
        values: tuple[str, ...],
    ) -> tuple[str, ...]:
        if any(not value.strip() or len(value) > 1000 for value in values):
            raise ValueError("missing evidence descriptions are invalid")
        return tuple(value.strip() for value in values)

    @model_validator(mode="after")
    def validate_handoff_scope(self) -> DiagnosisHandoff:
        for reference in self.supporting_evidence_refs:
            if reference.split("/")[2] != self.run_id:
                raise ValueError("evidence belongs to another run")
        return self


class ReplayResourceSnapshot(Phase3Model):
    schema_version: Literal["phase3.replay-resource-snapshot.v1"]
    backend: Literal["REPLAY_ONLY"]
    owner_run_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    resource_id: str
    service: Literal["ad"]
    configuration_state: ConfigurationState
    state_version: StrictInt = Field(ge=0)

    @field_validator("resource_id", mode="before")
    @classmethod
    def validate_resource_id(cls, value: object) -> str:
        return _require_identifier(value, field_name="resource_id")


class RemediationAction(Phase3Model):
    schema_version: Literal["phase3.remediation-action.v1"]
    action_type: Literal[ActionType.RESTORE_FROZEN_SERVICE_CONFIGURATION]
    action_id: str
    run_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    incident_id: str
    attempt_id: str
    resource_id: str
    target_service: Literal["ad"]
    target_backend: Literal["REPLAY_ONLY"]
    expected_pre_state: Literal[ConfigurationState.FAULTED]
    desired_state: Literal[ConfigurationState.FROZEN]
    expected_state_version: StrictInt = Field(ge=0)
    blast_radius: StrictInt

    @field_validator(
        "action_id",
        "incident_id",
        "attempt_id",
        "resource_id",
        mode="before",
    )
    @classmethod
    def validate_identifiers(cls, value: object, info: object) -> str:
        field_name = getattr(info, "field_name", None) or "identifier"
        return _require_identifier(value, field_name=field_name)

    @field_validator("blast_radius")
    @classmethod
    def require_one_field(cls, value: int) -> int:
        if value != 1:
            raise ValueError("blast_radius must be exactly one")
        return value


class RemediationPlan(Phase3Model):
    schema_version: Literal["phase3.remediation-plan.v1"]
    plan_id: str
    run_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    incident_id: str
    attempt_id: str
    action: RemediationAction
    plan_digest: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("plan_id", "incident_id", "attempt_id", mode="before")
    @classmethod
    def validate_identifiers(cls, value: object, info: object) -> str:
        field_name = getattr(info, "field_name", None) or "identifier"
        return _require_identifier(value, field_name=field_name)

    @model_validator(mode="after")
    def validate_plan_binding(self) -> RemediationPlan:
        if (
            self.action.run_id != self.run_id
            or self.action.incident_id != self.incident_id
            or self.action.attempt_id != self.attempt_id
        ):
            raise ValueError("plan and action identities differ")
        expected = semantic_sha256(
            self.model_dump(mode="json", exclude={"plan_digest"})
        )
        if self.plan_digest != expected:
            raise ValueError("plan digest does not bind the plan")
        return self


class NoAction(Phase3Model):
    schema_version: Literal["phase3.no-action.v1"]
    decision: Literal["NO_ACTION"]
    run_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    incident_id: str
    attempt_id: str
    reason_code: PlannerReasonCode

    @field_validator("incident_id", "attempt_id", mode="before")
    @classmethod
    def validate_identifiers(cls, value: object, info: object) -> str:
        field_name = getattr(info, "field_name", None) or "identifier"
        return _require_identifier(value, field_name=field_name)


class ApprovalDecision(Phase3Model):
    schema_version: Literal["phase3.approval-decision.v1"]
    mode: ApprovalMode
    run_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    incident_id: str
    attempt_id: str
    action_id: str
    plan_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    decision: ApprovalOutcome

    @field_validator(
        "incident_id",
        "attempt_id",
        "action_id",
        mode="before",
    )
    @classmethod
    def validate_identifiers(cls, value: object, info: object) -> str:
        field_name = getattr(info, "field_name", None) or "identifier"
        return _require_identifier(value, field_name=field_name)


class AttemptSnapshot(Phase3Model):
    schema_version: Literal["phase3.attempt-snapshot.v1"]
    run_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    incident_id: str
    attempt_id: str
    resource_id: str
    state_version: StrictInt = Field(ge=0)
    forward_mutation_count: StrictInt = Field(ge=0)
    closed: StrictBool
    approval_consumed: StrictBool = False
    local_test_mode: StrictBool = False
    rollback_pre_state: ReplayResourceSnapshot | None

    @field_validator(
        "incident_id",
        "attempt_id",
        "resource_id",
        mode="before",
    )
    @classmethod
    def validate_identifiers(cls, value: object, info: object) -> str:
        field_name = getattr(info, "field_name", None) or "identifier"
        return _require_identifier(value, field_name=field_name)


class PolicyDecision(Phase3Model):
    schema_version: Literal["phase3.policy-decision.v1"]
    outcome: PolicyOutcome
    reason_code: PolicyReasonCode
    run_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    incident_id: str
    attempt_id: str
    action_id: str
    plan_digest: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator(
        "incident_id",
        "attempt_id",
        "action_id",
        mode="before",
    )
    @classmethod
    def validate_identifiers(cls, value: object, info: object) -> str:
        field_name = getattr(info, "field_name", None) or "identifier"
        return _require_identifier(value, field_name=field_name)


class ExecutionReceipt(Phase3Model):
    schema_version: Literal["phase3.execution-receipt.v1"]
    run_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    incident_id: str
    attempt_id: str
    action_id: str
    plan_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    resource_id: str
    outcome: ExecutionOutcome
    before_state: ReplayResourceSnapshot
    after_state: ReplayResourceSnapshot
    changed_configuration_fields: tuple[Literal["configuration_state"], ...]
    forward_mutation_count: Literal[1]

    @field_validator(
        "incident_id",
        "attempt_id",
        "action_id",
        "resource_id",
        mode="before",
    )
    @classmethod
    def validate_identifiers(cls, value: object, info: object) -> str:
        field_name = getattr(info, "field_name", None) or "identifier"
        return _require_identifier(value, field_name=field_name)

    @model_validator(mode="after")
    def validate_execution_delta(self) -> ExecutionReceipt:
        before = self.before_state
        after = self.after_state
        if (
            before.owner_run_id != self.run_id
            or after.owner_run_id != self.run_id
            or before.resource_id != self.resource_id
            or after.resource_id != self.resource_id
            or before.backend != "REPLAY_ONLY"
            or after.backend != "REPLAY_ONLY"
        ):
            raise ValueError("execution receipt resource binding is invalid")
        if self.outcome is ExecutionOutcome.APPLIED:
            if (
                before.configuration_state is not ConfigurationState.FAULTED
                or after.configuration_state is not ConfigurationState.FROZEN
                or after.state_version != before.state_version + 1
                or self.changed_configuration_fields != ("configuration_state",)
            ):
                raise ValueError("applied receipt has an invalid replay delta")
        elif after != before or self.changed_configuration_fields:
            raise ValueError("non-applied receipt must preserve replay state")
        return self


class VerificationDecision(Phase3Model):
    schema_version: Literal["phase3.verification-decision.v1"]
    run_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    attempt_id: str
    resource_id: str
    outcome: VerificationOutcome
    reason_code: VerificationReasonCode

    @field_validator("attempt_id", "resource_id", mode="before")
    @classmethod
    def validate_identifiers(cls, value: object, info: object) -> str:
        field_name = getattr(info, "field_name", None) or "identifier"
        return _require_identifier(value, field_name=field_name)


class RollbackReceipt(Phase3Model):
    schema_version: Literal["phase3.rollback-receipt.v1"]
    run_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    attempt_id: str
    action_id: str
    resource_id: str
    outcome: RollbackOutcome
    restored_state: ReplayResourceSnapshot
    forward_mutation_count: Literal[1]

    @field_validator(
        "attempt_id",
        "action_id",
        "resource_id",
        mode="before",
    )
    @classmethod
    def validate_identifiers(cls, value: object, info: object) -> str:
        field_name = getattr(info, "field_name", None) or "identifier"
        return _require_identifier(value, field_name=field_name)


class RemediationReport(Phase3Model):
    schema_version: Literal["phase3.remediation-report.v1"]
    run_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    incident_id: str
    attempt_id: str
    terminal_outcome: TerminalOutcome
    closed: Literal[True]
    replay_only: Literal[True]
    live_mutation: Literal[False]
    live_telemetry: Literal[False]
    durable_ledger: Literal[False]
    phase4_entered: Literal[False]
    approval_mode: ApprovalMode | None
    plan_digest: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    policy_outcome: PolicyOutcome | None
    policy_reason_code: PolicyReasonCode | None
    execution_outcome: ExecutionOutcome | None
    verification_outcome: VerificationOutcome | None
    rollback_outcome: RollbackOutcome
    forward_mutation_count: StrictInt = Field(ge=0, le=1)
    final_resource: ReplayResourceSnapshot
    events: tuple[str, ...] = Field(min_length=1, max_length=32)
    semantic_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("incident_id", "attempt_id", mode="before")
    @classmethod
    def validate_identifiers(cls, value: object, info: object) -> str:
        field_name = getattr(info, "field_name", None) or "identifier"
        return _require_identifier(value, field_name=field_name)

    @field_validator("events")
    @classmethod
    def validate_events(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if any(not value or len(value) > 128 for value in values):
            raise ValueError("events must be compact stable markers")
        return values

    @model_validator(mode="after")
    def validate_report_digest(self) -> RemediationReport:
        expected = semantic_sha256(
            self.model_dump(mode="json", exclude={"semantic_sha256"})
        )
        if self.semantic_sha256 != expected:
            raise ValueError("report semantic digest is invalid")
        return self


def make_plan_digest(payload: dict[str, object]) -> str:
    """Compute the required digest before constructing an immutable plan."""

    return semantic_sha256(payload)
