"""In-memory replay executor, read-only verifier, and exact rollback."""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import Field, StrictInt

from ecomsre.phase3.contracts import (
    ApprovalDecision,
    ApprovalOutcome,
    AttemptSnapshot,
    ConfigurationState,
    ExecutionOutcome,
    ExecutionReceipt,
    MutationBehavior,
    Phase3Model,
    PolicyDecision,
    PolicyOutcome,
    RemediationPlan,
    ReplayHealthStatus,
    ReplayResourceSnapshot,
    RollbackBehavior,
    RollbackOutcome,
    RollbackReceipt,
    VerificationDecision,
    VerificationOutcome,
    VerificationReasonCode,
)


class ExecutionErrorCode(str, Enum):
    POLICY_DENIED = "POLICY_DENIED"
    APPROVAL_INVALID = "APPROVAL_INVALID"
    BINDING_MISMATCH = "BINDING_MISMATCH"
    ATTEMPT_CLOSED = "ATTEMPT_CLOSED"
    FORWARD_MUTATION_LIMIT_REACHED = "FORWARD_MUTATION_LIMIT_REACHED"


class ExecutionError(RuntimeError):
    """Typed fail-closed rejection before a replay mutation."""

    def __init__(self, code: ExecutionErrorCode, detail: str) -> None:
        self.code = code
        super().__init__(f"{code.value}: {detail}")


class RollbackErrorCode(str, Enum):
    INVALID_EXECUTION = "INVALID_EXECUTION"
    INVALID_VERIFICATION = "INVALID_VERIFICATION"
    BINDING_MISMATCH = "BINDING_MISMATCH"
    ROLLBACK_ALREADY_USED = "ROLLBACK_ALREADY_USED"


class RollbackError(RuntimeError):
    def __init__(self, code: RollbackErrorCode, detail: str) -> None:
        self.code = code
        super().__init__(f"{code.value}: {detail}")


class ReplayHealthObservation(Phase3Model):
    schema_version: str = Field(pattern=r"^phase3\.replay-health-observation\.v1$")
    run_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    resource_id: str
    owner_run_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    observed_state_version: StrictInt = Field(ge=0)
    status: ReplayHealthStatus


class ReplayAttempt:
    """One process-local attempt state machine with one forward dispatch."""

    def __init__(
        self,
        *,
        run_id: str,
        incident_id: str,
        attempt_id: str,
        initial_resource: ReplayResourceSnapshot,
        local_test_mode: bool = False,
    ) -> None:
        if type(local_test_mode) is not bool:
            raise ValueError("local_test_mode must be an exact boolean")
        initial = ReplayResourceSnapshot.model_validate(
            initial_resource.model_dump(mode="python")
        )
        if initial.owner_run_id != run_id:
            raise ValueError("initial replay resource is not owned by this run")
        self._run_id = run_id
        self._incident_id = incident_id
        self._attempt_id = attempt_id
        self._resource = initial
        self._rollback_pre_state = initial
        self._forward_mutation_count = 0
        self._approval_consumed = False
        self._local_test_mode = local_test_mode
        self._closed = False
        self._rollback_used = False

    @property
    def resource(self) -> ReplayResourceSnapshot:
        return self._resource

    @property
    def forward_mutation_count(self) -> int:
        return self._forward_mutation_count

    @property
    def rollback_used(self) -> bool:
        return self._rollback_used

    def snapshot(self) -> AttemptSnapshot:
        return AttemptSnapshot(
            schema_version="phase3.attempt-snapshot.v1",
            run_id=self._run_id,
            incident_id=self._incident_id,
            attempt_id=self._attempt_id,
            resource_id=self._resource.resource_id,
            state_version=self._resource.state_version,
            forward_mutation_count=self._forward_mutation_count,
            closed=self._closed,
            approval_consumed=self._approval_consumed,
            local_test_mode=self._local_test_mode,
            rollback_pre_state=self._rollback_pre_state,
        )

    def close(self) -> None:
        self._closed = True

    def _begin_forward(self) -> None:
        self._forward_mutation_count = 1
        self._approval_consumed = True

    def _replace_resource(self, resource: ReplayResourceSnapshot) -> None:
        self._resource = ReplayResourceSnapshot.model_validate(
            resource.model_dump(mode="python")
        )

    def _mark_rollback_used(self) -> None:
        self._rollback_used = True


class RestrictedExecutor:
    """Executor for exactly one frozen replay configuration transition."""

    def execute(
        self,
        *,
        plan: RemediationPlan,
        policy: PolicyDecision,
        approval: ApprovalDecision,
        attempt: ReplayAttempt,
        behavior: MutationBehavior,
    ) -> ExecutionReceipt:
        plan = RemediationPlan.model_validate(plan.model_dump(mode="python"))
        policy = PolicyDecision.model_validate(policy.model_dump(mode="python"))
        approval = ApprovalDecision.model_validate(approval.model_dump(mode="python"))
        if policy.outcome is not PolicyOutcome.ALLOW:
            raise ExecutionError(
                ExecutionErrorCode.POLICY_DENIED,
                "Policy Gate did not issue ALLOW",
            )
        if approval.decision is not ApprovalOutcome.APPROVED:
            raise ExecutionError(
                ExecutionErrorCode.APPROVAL_INVALID,
                "approval decision is not APPROVED",
            )
        snapshot = attempt.snapshot()
        if snapshot.closed:
            raise ExecutionError(
                ExecutionErrorCode.ATTEMPT_CLOSED,
                "attempt is terminal",
            )
        if snapshot.forward_mutation_count != 0:
            raise ExecutionError(
                ExecutionErrorCode.FORWARD_MUTATION_LIMIT_REACHED,
                "attempt already issued its forward mutation",
            )
        action = plan.action
        if (
            policy.run_id != plan.run_id
            or policy.incident_id != plan.incident_id
            or policy.attempt_id != plan.attempt_id
            or policy.action_id != action.action_id
            or policy.plan_digest != plan.plan_digest
            or approval.run_id != plan.run_id
            or approval.incident_id != plan.incident_id
            or approval.attempt_id != plan.attempt_id
            or approval.action_id != action.action_id
            or approval.plan_digest != plan.plan_digest
            or snapshot.run_id != plan.run_id
            or snapshot.incident_id != plan.incident_id
            or snapshot.attempt_id != plan.attempt_id
            or attempt.resource.resource_id != action.resource_id
            or attempt.resource.state_version != action.expected_state_version
            or attempt.resource.configuration_state is not ConfigurationState.FAULTED
        ):
            raise ExecutionError(
                ExecutionErrorCode.BINDING_MISMATCH,
                "execution identities or pre-state do not match",
            )

        before = attempt.resource
        attempt._begin_forward()
        changed: tuple[Literal["configuration_state"], ...]
        if behavior is MutationBehavior.APPLY:
            after = before.model_copy(
                update={
                    "configuration_state": ConfigurationState.FROZEN,
                    "state_version": before.state_version + 1,
                }
            )
            attempt._replace_resource(after)
            outcome = ExecutionOutcome.APPLIED
            changed = ("configuration_state",)
        elif behavior is MutationBehavior.NOT_APPLIED:
            after = before
            outcome = ExecutionOutcome.NOT_APPLIED
            changed = ()
        else:
            after = before
            outcome = ExecutionOutcome.FAILED
            changed = ()
        return ExecutionReceipt(
            schema_version="phase3.execution-receipt.v1",
            run_id=plan.run_id,
            incident_id=plan.incident_id,
            attempt_id=plan.attempt_id,
            action_id=action.action_id,
            plan_digest=plan.plan_digest,
            resource_id=action.resource_id,
            outcome=outcome,
            before_state=before,
            after_state=after,
            changed_configuration_fields=changed,
            forward_mutation_count=1,
        )


def _verification(
    receipt: ExecutionReceipt,
    *,
    outcome: VerificationOutcome,
    reason_code: VerificationReasonCode,
) -> VerificationDecision:
    return VerificationDecision(
        schema_version="phase3.verification-decision.v1",
        run_id=receipt.run_id,
        attempt_id=receipt.attempt_id,
        resource_id=receipt.resource_id,
        outcome=outcome,
        reason_code=reason_code,
    )


def verify_replay(
    *,
    receipt: ExecutionReceipt,
    post_state: ReplayResourceSnapshot,
    forward_mutation_count: int,
    observation: ReplayHealthObservation,
) -> VerificationDecision:
    """Read only the typed receipt, post-state, and deterministic health fixture."""

    receipt = ExecutionReceipt.model_validate(receipt.model_dump(mode="python"))
    post_state = ReplayResourceSnapshot.model_validate(
        post_state.model_dump(mode="python")
    )
    observation = ReplayHealthObservation.model_validate(
        observation.model_dump(mode="python")
    )
    if receipt.outcome is not ExecutionOutcome.APPLIED:
        return _verification(
            receipt,
            outcome=VerificationOutcome.FAILED,
            reason_code=VerificationReasonCode.EXECUTION_NOT_APPLIED,
        )
    if post_state != receipt.after_state:
        return _verification(
            receipt,
            outcome=VerificationOutcome.FAILED,
            reason_code=VerificationReasonCode.POST_STATE_MISMATCH,
        )
    if (
        receipt.before_state.owner_run_id != receipt.run_id
        or post_state.owner_run_id != receipt.run_id
        or observation.owner_run_id != receipt.run_id
    ):
        return _verification(
            receipt,
            outcome=VerificationOutcome.FAILED,
            reason_code=VerificationReasonCode.OWNERSHIP_CHANGED,
        )
    if (
        post_state.state_version != receipt.before_state.state_version + 1
        or observation.observed_state_version != post_state.state_version
    ):
        return _verification(
            receipt,
            outcome=VerificationOutcome.FAILED,
            reason_code=VerificationReasonCode.STATE_VERSION_MISMATCH,
        )
    if (
        receipt.changed_configuration_fields != ("configuration_state",)
        or post_state.configuration_state is not ConfigurationState.FROZEN
    ):
        return _verification(
            receipt,
            outcome=VerificationOutcome.FAILED,
            reason_code=VerificationReasonCode.FIELD_CHANGE_MISMATCH,
        )
    if type(forward_mutation_count) is not int or forward_mutation_count != 1:
        return _verification(
            receipt,
            outcome=VerificationOutcome.FAILED,
            reason_code=VerificationReasonCode.FORWARD_COUNT_MISMATCH,
        )
    if (
        observation.run_id != receipt.run_id
        or observation.resource_id != receipt.resource_id
    ):
        return _verification(
            receipt,
            outcome=VerificationOutcome.FAILED,
            reason_code=VerificationReasonCode.POST_STATE_MISMATCH,
        )
    if observation.status is ReplayHealthStatus.INCONCLUSIVE:
        return _verification(
            receipt,
            outcome=VerificationOutcome.INCONCLUSIVE,
            reason_code=VerificationReasonCode.HEALTH_INCONCLUSIVE,
        )
    if observation.status is ReplayHealthStatus.FAILED:
        return _verification(
            receipt,
            outcome=VerificationOutcome.FAILED,
            reason_code=VerificationReasonCode.HEALTH_NOT_RECOVERED,
        )
    return _verification(
        receipt,
        outcome=VerificationOutcome.VERIFIED,
        reason_code=VerificationReasonCode.VERIFIED,
    )


def compensate_rollback(
    *,
    receipt: ExecutionReceipt,
    verification: VerificationDecision,
    attempt: ReplayAttempt,
    behavior: RollbackBehavior,
) -> RollbackReceipt:
    """Restore only the exact before-state carried by the execution receipt."""

    receipt = ExecutionReceipt.model_validate(receipt.model_dump(mode="python"))
    verification = VerificationDecision.model_validate(
        verification.model_dump(mode="python")
    )
    if receipt.outcome is not ExecutionOutcome.APPLIED:
        raise RollbackError(
            RollbackErrorCode.INVALID_EXECUTION,
            "rollback requires an applied execution",
        )
    if verification.outcome not in {
        VerificationOutcome.FAILED,
        VerificationOutcome.INCONCLUSIVE,
    }:
        raise RollbackError(
            RollbackErrorCode.INVALID_VERIFICATION,
            "rollback requires failed or inconclusive verification",
        )
    if (
        verification.run_id != receipt.run_id
        or verification.attempt_id != receipt.attempt_id
        or verification.resource_id != receipt.resource_id
    ):
        raise RollbackError(
            RollbackErrorCode.BINDING_MISMATCH,
            "verification and execution identities do not match",
        )
    if attempt.rollback_used:
        raise RollbackError(
            RollbackErrorCode.ROLLBACK_ALREADY_USED,
            "rollback has already been attempted",
        )
    snapshot = attempt.snapshot()
    if (
        snapshot.run_id != receipt.run_id
        or snapshot.attempt_id != receipt.attempt_id
        or attempt.resource != receipt.after_state
        or snapshot.forward_mutation_count != 1
    ):
        raise RollbackError(
            RollbackErrorCode.BINDING_MISMATCH,
            "rollback receipt and current replay state do not match",
        )
    attempt._mark_rollback_used()
    if behavior is RollbackBehavior.RESTORE:
        attempt._replace_resource(receipt.before_state)
        outcome = (
            RollbackOutcome.RESTORED
            if attempt.resource == receipt.before_state
            else RollbackOutcome.FAILED
        )
    else:
        outcome = RollbackOutcome.FAILED
    return RollbackReceipt(
        schema_version="phase3.rollback-receipt.v1",
        run_id=receipt.run_id,
        attempt_id=receipt.attempt_id,
        action_id=receipt.action_id,
        resource_id=receipt.resource_id,
        outcome=outcome,
        restored_state=attempt.resource,
        forward_mutation_count=1,
    )
