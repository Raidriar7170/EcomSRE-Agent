"""Lean Phase 3 Planner and deterministic Policy Gate contracts."""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import ValidationError
import pytest

from ecomsre.phase1.contracts import EvidenceSource, FaultMechanism, RCADecision
from ecomsre.phase1.evidence import EvidenceStore
from ecomsre.phase3.contracts import (
    ActionType,
    ApprovalDecision,
    ApprovalMode,
    ApprovalOutcome,
    AttemptSnapshot,
    ConfigurationState,
    DiagnosisHandoff,
    NoAction,
    PolicyOutcome,
    PolicyReasonCode,
    RemediationPlan,
    ReplayResourceSnapshot,
)
from ecomsre.phase3.planner import plan_remediation
from ecomsre.phase3.policy import evaluate_policy


RUN_ID = "a" * 32
OTHER_RUN_ID = "b" * 32
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


def _handoff(
    *,
    decision: RCADecision = RCADecision.RCA_CONFIRMED,
    supporting_refs: tuple[str, ...] = (EVIDENCE_REF,),
    missing_evidence: tuple[str, ...] = (),
) -> DiagnosisHandoff:
    return DiagnosisHandoff(
        schema_version="phase3.diagnosis-handoff.v1",
        run_id=RUN_ID,
        incident_id=INCIDENT_ID,
        decision=decision,
        root_service=("ad" if decision is RCADecision.RCA_CONFIRMED else None),
        fault_mechanism=(
            FaultMechanism.RUNTIME_CONFIGURATION_FAILURE
            if decision is RCADecision.RCA_CONFIRMED
            else None
        ),
        supporting_evidence_refs=supporting_refs,
        missing_evidence=missing_evidence,
    )


def _resource(*, owner_run_id: str = RUN_ID) -> ReplayResourceSnapshot:
    return ReplayResourceSnapshot(
        schema_version="phase3.replay-resource-snapshot.v1",
        backend="REPLAY_ONLY",
        owner_run_id=owner_run_id,
        resource_id=RESOURCE_ID,
        service="ad",
        configuration_state=ConfigurationState.FAULTED,
        state_version=7,
    )


def _approved(plan: RemediationPlan) -> ApprovalDecision:
    return ApprovalDecision(
        schema_version="phase3.approval-decision.v1",
        mode=ApprovalMode.HUMAN,
        run_id=plan.run_id,
        incident_id=plan.incident_id,
        attempt_id=plan.attempt_id,
        action_id=plan.action.action_id,
        plan_digest=plan.plan_digest,
        decision=ApprovalOutcome.APPROVED,
    )


def _attempt(resource: ReplayResourceSnapshot) -> AttemptSnapshot:
    return AttemptSnapshot(
        schema_version="phase3.attempt-snapshot.v1",
        run_id=RUN_ID,
        incident_id=INCIDENT_ID,
        attempt_id=ATTEMPT_ID,
        resource_id=RESOURCE_ID,
        state_version=resource.state_version,
        forward_mutation_count=0,
        closed=False,
        rollback_pre_state=resource,
    )


def test_eligible_rca_produces_the_only_allowlisted_typed_plan() -> None:
    resource = _resource()

    outcome = plan_remediation(
        handoff=_handoff(),
        resource=resource,
        attempt_id=ATTEMPT_ID,
        evidence_store=_evidence_store(),
    )

    assert isinstance(outcome, RemediationPlan)
    assert outcome.action.action_type is ActionType.RESTORE_FROZEN_SERVICE_CONFIGURATION
    assert outcome.action.target_service == "ad"
    assert outcome.action.target_backend == "REPLAY_ONLY"
    assert outcome.action.blast_radius == 1
    assert outcome.action.expected_state_version == resource.state_version
    assert len(outcome.plan_digest) == 64


@pytest.mark.parametrize(
    ("handoff", "owner_run_id", "evidence_store"),
    [
        (_handoff(decision=RCADecision.ABSTAIN), RUN_ID, _evidence_store()),
        (_handoff(), RUN_ID, EvidenceStore(RUN_ID)),
        (
            _handoff(missing_evidence=("configuration snapshot missing",)),
            RUN_ID,
            _evidence_store(),
        ),
        (_handoff(), OTHER_RUN_ID, _evidence_store()),
    ],
)
def test_ineligible_inputs_return_typed_no_action(
    handoff: DiagnosisHandoff,
    owner_run_id: str,
    evidence_store: EvidenceStore,
) -> None:
    outcome = plan_remediation(
        handoff=handoff,
        resource=_resource(owner_run_id=owner_run_id),
        attempt_id=ATTEMPT_ID,
        evidence_store=evidence_store,
    )

    assert isinstance(outcome, NoAction)
    assert outcome.decision == "NO_ACTION"


def test_policy_allows_only_the_exact_bound_replay_action() -> None:
    handoff = _handoff()
    resource = _resource()
    plan = plan_remediation(
        handoff=handoff,
        resource=resource,
        attempt_id=ATTEMPT_ID,
        evidence_store=_evidence_store(),
    )
    assert isinstance(plan, RemediationPlan)

    decision = evaluate_policy(
        plan=plan,
        handoff=handoff,
        resource=resource,
        attempt=_attempt(resource),
        approval=_approved(plan),
        evidence_store=_evidence_store(),
    )

    assert decision.outcome is PolicyOutcome.ALLOW
    assert decision.reason_code is PolicyReasonCode.ALLOWED


def test_forged_approval_is_denied_by_typed_identity_validation() -> None:
    handoff = _handoff()
    resource = _resource()
    plan = plan_remediation(
        handoff=handoff,
        resource=resource,
        attempt_id=ATTEMPT_ID,
        evidence_store=_evidence_store(),
    )
    assert isinstance(plan, RemediationPlan)
    forged = _approved(plan).model_copy(update={"run_id": OTHER_RUN_ID})

    decision = evaluate_policy(
        plan=plan,
        handoff=handoff,
        resource=resource,
        attempt=_attempt(resource),
        approval=forged,
        evidence_store=_evidence_store(),
    )

    assert decision.outcome is PolicyOutcome.DENY
    assert decision.reason_code is PolicyReasonCode.APPROVAL_IDENTITY_MISMATCH


def test_policy_denies_state_version_drift_and_second_forward_mutation() -> None:
    handoff = _handoff()
    resource = _resource()
    plan = plan_remediation(
        handoff=handoff,
        resource=resource,
        attempt_id=ATTEMPT_ID,
        evidence_store=_evidence_store(),
    )
    assert isinstance(plan, RemediationPlan)
    attempt = _attempt(resource)

    drifted = resource.model_copy(update={"state_version": 8})
    drift = evaluate_policy(
        plan=plan,
        handoff=handoff,
        resource=drifted,
        attempt=attempt,
        approval=_approved(plan),
        evidence_store=_evidence_store(),
    )
    second = evaluate_policy(
        plan=plan,
        handoff=handoff,
        resource=resource,
        attempt=attempt.model_copy(update={"forward_mutation_count": 1}),
        approval=_approved(plan),
        evidence_store=_evidence_store(),
    )

    assert drift.reason_code is PolicyReasonCode.STATE_VERSION_MISMATCH
    assert second.reason_code is PolicyReasonCode.FORWARD_MUTATION_LIMIT_REACHED


def test_action_schema_rejects_arbitrary_executable_payload() -> None:
    resource = _resource()
    plan = plan_remediation(
        handoff=_handoff(),
        resource=resource,
        attempt_id=ATTEMPT_ID,
        evidence_store=_evidence_store(),
    )
    assert isinstance(plan, RemediationPlan)
    payload = plan.action.model_dump(mode="python")
    payload["argv"] = ("sh", "-c", "write host")

    with pytest.raises(ValidationError):
        type(plan.action).model_validate(payload)


def test_local_test_auto_approval_requires_explicit_test_mode() -> None:
    handoff = _handoff()
    resource = _resource()
    plan = plan_remediation(
        handoff=handoff,
        resource=resource,
        attempt_id=ATTEMPT_ID,
        evidence_store=_evidence_store(),
    )
    assert isinstance(plan, RemediationPlan)
    automatic = _approved(plan).model_copy(
        update={"mode": ApprovalMode.LOCAL_TEST_AUTO_APPROVAL}
    )
    normal_attempt = _attempt(resource)
    test_attempt = normal_attempt.model_copy(update={"local_test_mode": True})

    denied = evaluate_policy(
        plan=plan,
        handoff=handoff,
        resource=resource,
        attempt=normal_attempt,
        approval=automatic,
        evidence_store=_evidence_store(),
    )
    allowed = evaluate_policy(
        plan=plan,
        handoff=handoff,
        resource=resource,
        attempt=test_attempt,
        approval=automatic,
        evidence_store=_evidence_store(),
    )

    assert denied.reason_code is PolicyReasonCode.LOCAL_TEST_AUTO_APPROVAL_FORBIDDEN
    assert allowed.outcome is PolicyOutcome.ALLOW


def test_policy_requires_exact_rollback_pre_state() -> None:
    handoff = _handoff()
    resource = _resource()
    plan = plan_remediation(
        handoff=handoff,
        resource=resource,
        attempt_id=ATTEMPT_ID,
        evidence_store=_evidence_store(),
    )
    assert isinstance(plan, RemediationPlan)

    decision = evaluate_policy(
        plan=plan,
        handoff=handoff,
        resource=resource,
        attempt=_attempt(resource).model_copy(update={"rollback_pre_state": None}),
        approval=_approved(plan),
        evidence_store=_evidence_store(),
    )

    assert decision.outcome is PolicyOutcome.DENY
    assert decision.reason_code is PolicyReasonCode.ROLLBACK_PRE_STATE_MISSING


def test_nonexistent_supporting_reference_cannot_authorize_a_plan_or_policy() -> None:
    empty_store = EvidenceStore(RUN_ID)
    populated_store = _evidence_store()
    resource = _resource()
    forged_handoff = _handoff()

    planned = plan_remediation(
        handoff=forged_handoff,
        resource=resource,
        attempt_id=ATTEMPT_ID,
        evidence_store=empty_store,
    )

    assert isinstance(planned, NoAction)
    assert planned.reason_code.value == "EVIDENCE_UNRESOLVED"

    valid_plan = plan_remediation(
        handoff=forged_handoff,
        resource=resource,
        attempt_id=ATTEMPT_ID,
        evidence_store=populated_store,
    )
    assert isinstance(valid_plan, RemediationPlan)
    denied = evaluate_policy(
        plan=valid_plan,
        handoff=forged_handoff,
        resource=resource,
        attempt=_attempt(resource),
        approval=_approved(valid_plan),
        evidence_store=empty_store,
    )

    assert denied.outcome is PolicyOutcome.DENY
    assert denied.reason_code is PolicyReasonCode.EVIDENCE_SCOPE_INVALID
