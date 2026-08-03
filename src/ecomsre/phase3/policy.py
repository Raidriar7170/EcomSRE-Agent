"""Pure deterministic Policy Gate for the replay-only action."""

from __future__ import annotations

from ecomsre.phase1.contracts import FaultMechanism, RCADecision
from ecomsre.phase1.evidence import EvidenceStore
from ecomsre.phase3.contracts import (
    ActionType,
    ApprovalDecision,
    ApprovalMode,
    ApprovalOutcome,
    AttemptSnapshot,
    ConfigurationState,
    DiagnosisHandoff,
    PolicyDecision,
    PolicyOutcome,
    PolicyReasonCode,
    RemediationPlan,
    ReplayResourceSnapshot,
)
from ecomsre.phase3.handoff import (
    HandoffError,
    resolve_current_run_supporting_evidence,
)


def _decision(
    plan: RemediationPlan,
    *,
    outcome: PolicyOutcome,
    reason_code: PolicyReasonCode,
) -> PolicyDecision:
    return PolicyDecision(
        schema_version="phase3.policy-decision.v1",
        outcome=outcome,
        reason_code=reason_code,
        run_id=plan.run_id,
        incident_id=plan.incident_id,
        attempt_id=plan.attempt_id,
        action_id=plan.action.action_id,
        plan_digest=plan.plan_digest,
    )


def evaluate_policy(
    *,
    plan: RemediationPlan,
    handoff: DiagnosisHandoff,
    resource: ReplayResourceSnapshot,
    attempt: AttemptSnapshot,
    approval: ApprovalDecision,
    evidence_store: EvidenceStore,
) -> PolicyDecision:
    """Return only ALLOW or DENY with a stable reason code."""

    plan = RemediationPlan.model_validate(plan.model_dump(mode="python"))
    handoff = DiagnosisHandoff.model_validate(handoff.model_dump(mode="python"))
    resource = ReplayResourceSnapshot.model_validate(resource.model_dump(mode="python"))
    attempt = AttemptSnapshot.model_validate(attempt.model_dump(mode="python"))
    approval = ApprovalDecision.model_validate(approval.model_dump(mode="python"))
    action = plan.action

    if (
        handoff.run_id != plan.run_id
        or handoff.incident_id != plan.incident_id
        or attempt.run_id != plan.run_id
        or attempt.incident_id != plan.incident_id
        or attempt.attempt_id != plan.attempt_id
        or attempt.resource_id != action.resource_id
        or resource.resource_id != action.resource_id
    ):
        return _decision(
            plan,
            outcome=PolicyOutcome.DENY,
            reason_code=PolicyReasonCode.IDENTITY_MISMATCH,
        )
    if action.action_type is not ActionType.RESTORE_FROZEN_SERVICE_CONFIGURATION:
        return _decision(
            plan,
            outcome=PolicyOutcome.DENY,
            reason_code=PolicyReasonCode.ACTION_NOT_ALLOWLISTED,
        )
    if (
        handoff.decision is not RCADecision.RCA_CONFIRMED
        or handoff.root_service != "ad"
        or handoff.fault_mechanism is not FaultMechanism.RUNTIME_CONFIGURATION_FAILURE
    ):
        return _decision(
            plan,
            outcome=PolicyOutcome.DENY,
            reason_code=PolicyReasonCode.RCA_ACTION_MISMATCH,
        )
    try:
        resolved_evidence = resolve_current_run_supporting_evidence(
            handoff=handoff,
            evidence_store=evidence_store,
        )
    except HandoffError:
        resolved_evidence = ()
    if not resolved_evidence:
        return _decision(
            plan,
            outcome=PolicyOutcome.DENY,
            reason_code=PolicyReasonCode.EVIDENCE_SCOPE_INVALID,
        )
    if resource.owner_run_id != plan.run_id:
        return _decision(
            plan,
            outcome=PolicyOutcome.DENY,
            reason_code=PolicyReasonCode.RESOURCE_UNOWNED,
        )
    if resource.backend != "REPLAY_ONLY" or action.target_backend != "REPLAY_ONLY":
        return _decision(
            plan,
            outcome=PolicyOutcome.DENY,
            reason_code=PolicyReasonCode.TARGET_NOT_REPLAY_ONLY,
        )
    if (
        resource.configuration_state is not ConfigurationState.FAULTED
        or action.expected_pre_state is not ConfigurationState.FAULTED
    ):
        return _decision(
            plan,
            outcome=PolicyOutcome.DENY,
            reason_code=PolicyReasonCode.PRE_STATE_MISMATCH,
        )
    if (
        resource.state_version != action.expected_state_version
        or attempt.state_version != action.expected_state_version
    ):
        return _decision(
            plan,
            outcome=PolicyOutcome.DENY,
            reason_code=PolicyReasonCode.STATE_VERSION_MISMATCH,
        )
    if attempt.closed:
        return _decision(
            plan,
            outcome=PolicyOutcome.DENY,
            reason_code=PolicyReasonCode.ATTEMPT_CLOSED,
        )
    if attempt.forward_mutation_count != 0:
        return _decision(
            plan,
            outcome=PolicyOutcome.DENY,
            reason_code=PolicyReasonCode.FORWARD_MUTATION_LIMIT_REACHED,
        )
    if attempt.approval_consumed:
        return _decision(
            plan,
            outcome=PolicyOutcome.DENY,
            reason_code=PolicyReasonCode.DUPLICATE_APPROVAL,
        )
    if (
        approval.run_id != plan.run_id
        or approval.incident_id != plan.incident_id
        or approval.attempt_id != plan.attempt_id
        or approval.action_id != action.action_id
        or approval.plan_digest != plan.plan_digest
    ):
        return _decision(
            plan,
            outcome=PolicyOutcome.DENY,
            reason_code=PolicyReasonCode.APPROVAL_IDENTITY_MISMATCH,
        )
    if approval.decision is not ApprovalOutcome.APPROVED:
        return _decision(
            plan,
            outcome=PolicyOutcome.DENY,
            reason_code=PolicyReasonCode.APPROVAL_DENIED,
        )
    if (
        approval.mode is ApprovalMode.LOCAL_TEST_AUTO_APPROVAL
        and not attempt.local_test_mode
    ):
        return _decision(
            plan,
            outcome=PolicyOutcome.DENY,
            reason_code=PolicyReasonCode.LOCAL_TEST_AUTO_APPROVAL_FORBIDDEN,
        )
    if attempt.rollback_pre_state is None:
        return _decision(
            plan,
            outcome=PolicyOutcome.DENY,
            reason_code=PolicyReasonCode.ROLLBACK_PRE_STATE_MISSING,
        )
    if attempt.rollback_pre_state != resource:
        return _decision(
            plan,
            outcome=PolicyOutcome.DENY,
            reason_code=PolicyReasonCode.PRE_STATE_MISMATCH,
        )
    return _decision(
        plan,
        outcome=PolicyOutcome.ALLOW,
        reason_code=PolicyReasonCode.ALLOWED,
    )
