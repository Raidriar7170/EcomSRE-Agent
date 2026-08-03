"""End-to-end offline Phase 3 remediation replay workflow."""

from __future__ import annotations

from ecomsre.phase1.evidence import EvidenceStore
from ecomsre.phase3.contracts import (
    ApprovalDecision,
    ApprovalMode,
    DiagnosisHandoff,
    ExecutionOutcome,
    MutationBehavior,
    NoAction,
    PolicyOutcome,
    PolicyReasonCode,
    RemediationPlan,
    RemediationReport,
    ReplayHealthStatus,
    ReplayResourceSnapshot,
    RollbackBehavior,
    RollbackOutcome,
    TerminalOutcome,
    VerificationOutcome,
    semantic_sha256,
)
from ecomsre.phase3.planner import plan_remediation
from ecomsre.phase3.policy import evaluate_policy
from ecomsre.phase3.runtime import (
    ExecutionError,
    ReplayAttempt,
    ReplayHealthObservation,
    RestrictedExecutor,
    compensate_rollback,
    verify_replay,
)


def _report(
    *,
    attempt: ReplayAttempt,
    terminal: TerminalOutcome,
    approval_mode: ApprovalMode | None,
    plan: RemediationPlan | None,
    policy_outcome: PolicyOutcome | None,
    policy_reason_code: PolicyReasonCode | None,
    execution_outcome: ExecutionOutcome | None,
    verification_outcome: VerificationOutcome | None,
    rollback_outcome: RollbackOutcome,
    events: list[str],
) -> RemediationReport:
    attempt.close()
    snapshot = attempt.snapshot()
    payload: dict[str, object] = {
        "schema_version": "phase3.remediation-report.v1",
        "run_id": snapshot.run_id,
        "incident_id": snapshot.incident_id,
        "attempt_id": snapshot.attempt_id,
        "terminal_outcome": terminal.value,
        "closed": True,
        "replay_only": True,
        "live_mutation": False,
        "live_telemetry": False,
        "durable_ledger": False,
        "phase4_entered": False,
        "approval_mode": None if approval_mode is None else approval_mode.value,
        "plan_digest": None if plan is None else plan.plan_digest,
        "policy_outcome": (None if policy_outcome is None else policy_outcome.value),
        "policy_reason_code": (
            None if policy_reason_code is None else policy_reason_code.value
        ),
        "execution_outcome": (
            None if execution_outcome is None else execution_outcome.value
        ),
        "verification_outcome": (
            None if verification_outcome is None else verification_outcome.value
        ),
        "rollback_outcome": rollback_outcome.value,
        "forward_mutation_count": snapshot.forward_mutation_count,
        "final_resource": attempt.resource.model_dump(mode="json"),
        "events": tuple((*events, f"TERMINAL:{terminal.value}")),
    }
    return RemediationReport.model_validate(
        {
            **payload,
            "semantic_sha256": semantic_sha256(payload),
        }
    )


def run_remediation_replay(
    *,
    handoff: DiagnosisHandoff,
    initial_resource: ReplayResourceSnapshot,
    attempt_id: str,
    approval: ApprovalDecision | None,
    mutation_behavior: MutationBehavior,
    health_status: ReplayHealthStatus,
    rollback_behavior: RollbackBehavior,
    evidence_store: EvidenceStore,
    local_test_mode: bool = False,
    gate_resource: ReplayResourceSnapshot | None = None,
) -> RemediationReport:
    """Run one deterministic replay attempt with no live dependencies."""

    attempt = ReplayAttempt(
        run_id=handoff.run_id,
        incident_id=handoff.incident_id,
        attempt_id=attempt_id,
        initial_resource=initial_resource,
        local_test_mode=local_test_mode,
    )
    events: list[str] = []
    planned = plan_remediation(
        handoff=handoff,
        resource=attempt.resource,
        attempt_id=attempt_id,
        evidence_store=evidence_store,
    )
    if isinstance(planned, NoAction):
        events.append(f"PLANNER:NO_ACTION:{planned.reason_code.value}")
        return _report(
            attempt=attempt,
            terminal=TerminalOutcome.NO_ACTION,
            approval_mode=None,
            plan=None,
            policy_outcome=None,
            policy_reason_code=None,
            execution_outcome=None,
            verification_outcome=None,
            rollback_outcome=RollbackOutcome.NOT_REQUIRED,
            events=events,
        )
    plan = planned
    events.append("PLANNER:ACTION")
    if approval is None:
        events.append("APPROVAL:MISSING")
        return _report(
            attempt=attempt,
            terminal=TerminalOutcome.APPROVAL_DENIED,
            approval_mode=None,
            plan=plan,
            policy_outcome=PolicyOutcome.DENY,
            policy_reason_code=PolicyReasonCode.APPROVAL_DENIED,
            execution_outcome=None,
            verification_outcome=None,
            rollback_outcome=RollbackOutcome.NOT_REQUIRED,
            events=events,
        )
    events.append(f"APPROVAL:{approval.mode.value}:{approval.decision.value}")
    policy_resource = attempt.resource if gate_resource is None else gate_resource
    policy = evaluate_policy(
        plan=plan,
        handoff=handoff,
        resource=policy_resource,
        attempt=attempt.snapshot(),
        approval=approval,
        evidence_store=evidence_store,
    )
    events.append(f"POLICY:{policy.outcome.value}:{policy.reason_code.value}")
    if policy.outcome is PolicyOutcome.DENY:
        if policy.reason_code is PolicyReasonCode.APPROVAL_DENIED:
            terminal = TerminalOutcome.APPROVAL_DENIED
        elif policy.reason_code in {
            PolicyReasonCode.PRE_STATE_MISMATCH,
            PolicyReasonCode.STATE_VERSION_MISMATCH,
        }:
            terminal = TerminalOutcome.PRECONDITION_FAILED
        else:
            terminal = TerminalOutcome.POLICY_REJECTED
        return _report(
            attempt=attempt,
            terminal=terminal,
            approval_mode=approval.mode,
            plan=plan,
            policy_outcome=policy.outcome,
            policy_reason_code=policy.reason_code,
            execution_outcome=None,
            verification_outcome=None,
            rollback_outcome=RollbackOutcome.NOT_REQUIRED,
            events=events,
        )
    try:
        execution = RestrictedExecutor().execute(
            plan=plan,
            policy=policy,
            approval=approval,
            attempt=attempt,
            behavior=mutation_behavior,
        )
    except ExecutionError as error:
        events.append(f"EXECUTION:REJECTED:{error.code.value}")
        return _report(
            attempt=attempt,
            terminal=TerminalOutcome.UNSAFE,
            approval_mode=approval.mode,
            plan=plan,
            policy_outcome=policy.outcome,
            policy_reason_code=policy.reason_code,
            execution_outcome=None,
            verification_outcome=None,
            rollback_outcome=RollbackOutcome.NOT_REQUIRED,
            events=events,
        )
    events.append(f"EXECUTION:{execution.outcome.value}")
    if execution.outcome is not ExecutionOutcome.APPLIED:
        terminal = (
            TerminalOutcome.PRECONDITION_FAILED
            if execution.outcome is ExecutionOutcome.NOT_APPLIED
            else TerminalOutcome.UNSAFE
        )
        return _report(
            attempt=attempt,
            terminal=terminal,
            approval_mode=approval.mode,
            plan=plan,
            policy_outcome=policy.outcome,
            policy_reason_code=policy.reason_code,
            execution_outcome=execution.outcome,
            verification_outcome=None,
            rollback_outcome=RollbackOutcome.NOT_REQUIRED,
            events=events,
        )
    observation = ReplayHealthObservation(
        schema_version="phase3.replay-health-observation.v1",
        run_id=plan.run_id,
        resource_id=plan.action.resource_id,
        owner_run_id=attempt.resource.owner_run_id,
        observed_state_version=attempt.resource.state_version,
        status=health_status,
    )
    verification = verify_replay(
        receipt=execution,
        post_state=attempt.resource,
        forward_mutation_count=attempt.forward_mutation_count,
        observation=observation,
    )
    events.append(
        f"VERIFICATION:{verification.outcome.value}:{verification.reason_code.value}"
    )
    if verification.outcome is VerificationOutcome.VERIFIED:
        return _report(
            attempt=attempt,
            terminal=TerminalOutcome.REMEDIATION_VERIFIED,
            approval_mode=approval.mode,
            plan=plan,
            policy_outcome=policy.outcome,
            policy_reason_code=policy.reason_code,
            execution_outcome=execution.outcome,
            verification_outcome=verification.outcome,
            rollback_outcome=RollbackOutcome.NOT_REQUIRED,
            events=events,
        )
    rollback = compensate_rollback(
        receipt=execution,
        verification=verification,
        attempt=attempt,
        behavior=rollback_behavior,
    )
    events.append(f"ROLLBACK:{rollback.outcome.value}")
    terminal = (
        TerminalOutcome.VERIFICATION_FAILED_ROLLED_BACK
        if rollback.outcome is RollbackOutcome.RESTORED
        else TerminalOutcome.ROLLBACK_FAILED
    )
    return _report(
        attempt=attempt,
        terminal=terminal,
        approval_mode=approval.mode,
        plan=plan,
        policy_outcome=policy.outcome,
        policy_reason_code=policy.reason_code,
        execution_outcome=execution.outcome,
        verification_outcome=verification.outcome,
        rollback_outcome=rollback.outcome,
        events=events,
    )
