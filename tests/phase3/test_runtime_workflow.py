"""Restricted execution, independent replay verification, and rollback."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from ecomsre.phase1.contracts import EvidenceSource, FaultMechanism, RCADecision
from ecomsre.phase1.evidence import EvidenceStore
from ecomsre.phase3.contracts import (
    ApprovalDecision,
    ApprovalMode,
    ApprovalOutcome,
    ConfigurationState,
    DiagnosisHandoff,
    ExecutionOutcome,
    MutationBehavior,
    PolicyOutcome,
    RemediationPlan,
    ReplayHealthStatus,
    ReplayResourceSnapshot,
    RollbackBehavior,
    RollbackOutcome,
    TerminalOutcome,
    VerificationOutcome,
    VerificationReasonCode,
)
from ecomsre.phase3.planner import plan_remediation
from ecomsre.phase3.policy import evaluate_policy
from ecomsre.phase3.runtime import (
    ExecutionError,
    ExecutionErrorCode,
    ReplayAttempt,
    ReplayHealthObservation,
    RestrictedExecutor,
    RollbackError,
    RollbackErrorCode,
    compensate_rollback,
    verify_replay,
)
from ecomsre.phase3.workflow import run_remediation_replay


RUN_ID = "a" * 32
INCIDENT_ID = "incident-001"
ATTEMPT_ID = "attempt-001"
RESOURCE_ID = "replay-ad-runtime-config"
EVIDENCE_REF = f"evidence://{RUN_ID}/changes/0001"


def _evidence_store() -> EvidenceStore:
    store = EvidenceStore(RUN_ID)
    store.add(
        source=EvidenceSource.CHANGES,
        observation_type="configuration_transition",
        attributes={
            "change_kind": "configuration",
            "transition": "valid_to_invalid",
        },
        raw_artifact_ref="changes.json#0",
        raw_artifact_sha256="1" * 64,
        limitations=(),
        summary="Replay configuration changed from valid to invalid.",
        started_at=datetime(2026, 8, 3, tzinfo=UTC),
        ended_at=datetime(2026, 8, 3, 0, 1, tzinfo=UTC),
        service="ad",
    )
    return store


def _handoff(*, decision: RCADecision = RCADecision.RCA_CONFIRMED) -> DiagnosisHandoff:
    confirmed = decision is RCADecision.RCA_CONFIRMED
    return DiagnosisHandoff(
        schema_version="phase3.diagnosis-handoff.v1",
        run_id=RUN_ID,
        incident_id=INCIDENT_ID,
        decision=decision,
        root_service="ad" if confirmed else None,
        fault_mechanism=(
            FaultMechanism.RUNTIME_CONFIGURATION_FAILURE if confirmed else None
        ),
        supporting_evidence_refs=(EVIDENCE_REF,) if confirmed else (),
        missing_evidence=(),
    )


def _resource() -> ReplayResourceSnapshot:
    return ReplayResourceSnapshot(
        schema_version="phase3.replay-resource-snapshot.v1",
        backend="REPLAY_ONLY",
        owner_run_id=RUN_ID,
        resource_id=RESOURCE_ID,
        service="ad",
        configuration_state=ConfigurationState.FAULTED,
        state_version=4,
    )


def _plan() -> RemediationPlan:
    outcome = plan_remediation(
        handoff=_handoff(),
        resource=_resource(),
        attempt_id=ATTEMPT_ID,
        evidence_store=_evidence_store(),
    )
    assert isinstance(outcome, RemediationPlan)
    return outcome


def _approval(plan: RemediationPlan) -> ApprovalDecision:
    return ApprovalDecision(
        schema_version="phase3.approval-decision.v1",
        mode=ApprovalMode.HUMAN,
        run_id=RUN_ID,
        incident_id=INCIDENT_ID,
        attempt_id=ATTEMPT_ID,
        action_id=plan.action.action_id,
        plan_digest=plan.plan_digest,
        decision=ApprovalOutcome.APPROVED,
    )


def _allowed_execution():
    plan = _plan()
    approval = _approval(plan)
    attempt = ReplayAttempt(
        run_id=RUN_ID,
        incident_id=INCIDENT_ID,
        attempt_id=ATTEMPT_ID,
        initial_resource=_resource(),
    )
    policy = evaluate_policy(
        plan=plan,
        handoff=_handoff(),
        resource=attempt.resource,
        attempt=attempt.snapshot(),
        approval=approval,
        evidence_store=_evidence_store(),
    )
    assert policy.outcome is PolicyOutcome.ALLOW
    return plan, approval, attempt, policy


def test_restricted_executor_changes_only_the_frozen_replay_field_once() -> None:
    plan, approval, attempt, policy = _allowed_execution()

    receipt = RestrictedExecutor().execute(
        plan=plan,
        policy=policy,
        approval=approval,
        attempt=attempt,
        behavior=MutationBehavior.APPLY,
    )

    assert receipt.outcome is ExecutionOutcome.APPLIED
    assert receipt.before_state.configuration_state is ConfigurationState.FAULTED
    assert receipt.after_state.configuration_state is ConfigurationState.FROZEN
    assert receipt.after_state.state_version == receipt.before_state.state_version + 1
    assert receipt.changed_configuration_fields == ("configuration_state",)
    assert attempt.forward_mutation_count == 1

    with pytest.raises(ExecutionError) as raised:
        RestrictedExecutor().execute(
            plan=plan,
            policy=policy,
            approval=approval,
            attempt=attempt,
            behavior=MutationBehavior.APPLY,
        )
    assert raised.value.code is ExecutionErrorCode.FORWARD_MUTATION_LIMIT_REACHED
    assert attempt.forward_mutation_count == 1


def test_independent_verifier_reads_receipt_and_health_without_mutating() -> None:
    plan, approval, attempt, policy = _allowed_execution()
    receipt = RestrictedExecutor().execute(
        plan=plan,
        policy=policy,
        approval=approval,
        attempt=attempt,
        behavior=MutationBehavior.APPLY,
    )
    observation = ReplayHealthObservation(
        schema_version="phase3.replay-health-observation.v1",
        run_id=RUN_ID,
        resource_id=RESOURCE_ID,
        owner_run_id=RUN_ID,
        observed_state_version=receipt.after_state.state_version,
        status=ReplayHealthStatus.RECOVERED,
    )
    before_verify = attempt.resource

    verification = verify_replay(
        receipt=receipt,
        post_state=attempt.resource,
        forward_mutation_count=attempt.forward_mutation_count,
        observation=observation,
    )

    assert verification.outcome is VerificationOutcome.VERIFIED
    assert attempt.resource == before_verify


def test_failed_verification_uses_exact_receipt_before_state_for_rollback() -> None:
    plan, approval, attempt, policy = _allowed_execution()
    receipt = RestrictedExecutor().execute(
        plan=plan,
        policy=policy,
        approval=approval,
        attempt=attempt,
        behavior=MutationBehavior.APPLY,
    )
    observation = ReplayHealthObservation(
        schema_version="phase3.replay-health-observation.v1",
        run_id=RUN_ID,
        resource_id=RESOURCE_ID,
        owner_run_id=RUN_ID,
        observed_state_version=receipt.after_state.state_version,
        status=ReplayHealthStatus.FAILED,
    )
    verification = verify_replay(
        receipt=receipt,
        post_state=attempt.resource,
        forward_mutation_count=attempt.forward_mutation_count,
        observation=observation,
    )

    rollback = compensate_rollback(
        receipt=receipt,
        verification=verification,
        attempt=attempt,
        behavior=RollbackBehavior.RESTORE,
    )

    assert verification.outcome is VerificationOutcome.FAILED
    assert rollback.outcome is RollbackOutcome.RESTORED
    assert attempt.resource == receipt.before_state
    assert attempt.forward_mutation_count == 1


def test_workflow_success_and_rollback_reports_are_deterministic() -> None:
    plan = _plan()
    approved = _approval(plan)

    success = run_remediation_replay(
        handoff=_handoff(),
        initial_resource=_resource(),
        attempt_id=ATTEMPT_ID,
        approval=approved,
        mutation_behavior=MutationBehavior.APPLY,
        health_status=ReplayHealthStatus.RECOVERED,
        rollback_behavior=RollbackBehavior.RESTORE,
        evidence_store=_evidence_store(),
    )
    repeated = run_remediation_replay(
        handoff=_handoff(),
        initial_resource=_resource(),
        attempt_id=ATTEMPT_ID,
        approval=approved,
        mutation_behavior=MutationBehavior.APPLY,
        health_status=ReplayHealthStatus.RECOVERED,
        rollback_behavior=RollbackBehavior.RESTORE,
        evidence_store=_evidence_store(),
    )
    rolled_back = run_remediation_replay(
        handoff=_handoff(),
        initial_resource=_resource(),
        attempt_id=ATTEMPT_ID,
        approval=approved,
        mutation_behavior=MutationBehavior.APPLY,
        health_status=ReplayHealthStatus.FAILED,
        rollback_behavior=RollbackBehavior.RESTORE,
        evidence_store=_evidence_store(),
    )

    assert success.terminal_outcome is TerminalOutcome.REMEDIATION_VERIFIED
    assert success == repeated
    assert success.semantic_sha256 == repeated.semantic_sha256
    assert (
        rolled_back.terminal_outcome is TerminalOutcome.VERIFICATION_FAILED_ROLLED_BACK
    )
    assert rolled_back.final_resource == _resource()
    assert rolled_back.forward_mutation_count == 1
    assert not rolled_back.live_mutation
    assert not rolled_back.durable_ledger


def test_failed_execution_preserves_pre_state_and_closes_unsafe() -> None:
    plan = _plan()
    report = run_remediation_replay(
        handoff=_handoff(),
        initial_resource=_resource(),
        attempt_id=ATTEMPT_ID,
        approval=_approval(plan),
        mutation_behavior=MutationBehavior.FAIL,
        health_status=ReplayHealthStatus.RECOVERED,
        rollback_behavior=RollbackBehavior.RESTORE,
        evidence_store=_evidence_store(),
    )

    assert report.terminal_outcome is TerminalOutcome.UNSAFE
    assert report.execution_outcome is ExecutionOutcome.FAILED
    assert report.final_resource == _resource()
    assert report.forward_mutation_count == 1
    assert report.closed


def test_rollback_failure_and_local_auto_mode_are_visible_in_reports() -> None:
    plan = _plan()
    automatic = _approval(plan).model_copy(
        update={"mode": ApprovalMode.LOCAL_TEST_AUTO_APPROVAL}
    )
    report = run_remediation_replay(
        handoff=_handoff(),
        initial_resource=_resource(),
        attempt_id=ATTEMPT_ID,
        approval=automatic,
        mutation_behavior=MutationBehavior.APPLY,
        health_status=ReplayHealthStatus.INCONCLUSIVE,
        rollback_behavior=RollbackBehavior.FAIL,
        evidence_store=_evidence_store(),
        local_test_mode=True,
    )

    assert report.approval_mode is ApprovalMode.LOCAL_TEST_AUTO_APPROVAL
    assert report.verification_outcome is VerificationOutcome.INCONCLUSIVE
    assert report.rollback_outcome is RollbackOutcome.FAILED
    assert report.terminal_outcome is TerminalOutcome.ROLLBACK_FAILED
    assert report.forward_mutation_count == 1


def test_foreign_verification_cannot_authorize_rollback() -> None:
    plan, approval, attempt, policy = _allowed_execution()
    receipt = RestrictedExecutor().execute(
        plan=plan,
        policy=policy,
        approval=approval,
        attempt=attempt,
        behavior=MutationBehavior.APPLY,
    )
    observation = ReplayHealthObservation(
        schema_version="phase3.replay-health-observation.v1",
        run_id=RUN_ID,
        resource_id=RESOURCE_ID,
        owner_run_id=RUN_ID,
        observed_state_version=receipt.after_state.state_version,
        status=ReplayHealthStatus.FAILED,
    )
    verification = verify_replay(
        receipt=receipt,
        post_state=attempt.resource,
        forward_mutation_count=1,
        observation=observation,
    ).model_copy(update={"run_id": "b" * 32, "attempt_id": "foreign-attempt"})

    with pytest.raises(RollbackError) as raised:
        compensate_rollback(
            receipt=receipt,
            verification=verification,
            attempt=attempt,
            behavior=RollbackBehavior.RESTORE,
        )
    assert raised.value.code is RollbackErrorCode.BINDING_MISMATCH
    assert attempt.resource == receipt.after_state


@pytest.mark.parametrize("forged_count", [True, 1.0])
def test_verifier_rejects_non_integer_mutation_counts(forged_count: object) -> None:
    plan, approval, attempt, policy = _allowed_execution()
    receipt = RestrictedExecutor().execute(
        plan=plan,
        policy=policy,
        approval=approval,
        attempt=attempt,
        behavior=MutationBehavior.APPLY,
    )
    observation = ReplayHealthObservation(
        schema_version="phase3.replay-health-observation.v1",
        run_id=RUN_ID,
        resource_id=RESOURCE_ID,
        owner_run_id=RUN_ID,
        observed_state_version=receipt.after_state.state_version,
        status=ReplayHealthStatus.RECOVERED,
    )

    verification = verify_replay(
        receipt=receipt,
        post_state=attempt.resource,
        forward_mutation_count=forged_count,  # type: ignore[arg-type]
        observation=observation,
    )

    assert verification.outcome is VerificationOutcome.FAILED
    assert verification.reason_code is VerificationReasonCode.FORWARD_COUNT_MISMATCH


def test_local_test_mode_rejects_truthy_non_boolean_values() -> None:
    with pytest.raises(ValueError):
        ReplayAttempt(
            run_id=RUN_ID,
            incident_id=INCIDENT_ID,
            attempt_id=ATTEMPT_ID,
            initial_resource=_resource(),
            local_test_mode="false",  # type: ignore[arg-type]
        )


def test_not_applied_execution_is_terminal_without_state_change() -> None:
    plan = _plan()
    report = run_remediation_replay(
        handoff=_handoff(),
        initial_resource=_resource(),
        attempt_id=ATTEMPT_ID,
        approval=_approval(plan),
        mutation_behavior=MutationBehavior.NOT_APPLIED,
        health_status=ReplayHealthStatus.RECOVERED,
        rollback_behavior=RollbackBehavior.RESTORE,
        evidence_store=_evidence_store(),
    )

    assert report.execution_outcome is ExecutionOutcome.NOT_APPLIED
    assert report.terminal_outcome is TerminalOutcome.PRECONDITION_FAILED
    assert report.final_resource == _resource()
    assert report.forward_mutation_count == 1
