"""Deterministic six-case evaluation for the lean Phase 3 replay MVP."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from pydantic import ValidationError

from ecomsre.phase1.contracts import (
    EvidenceSource,
    FaultMechanism,
    Incident,
    RCADecision,
    RCAResult,
    RecommendedNextAction,
    Severity,
)
from ecomsre.phase1.evidence import EvidenceStore
from ecomsre.phase2.contracts import JudgeFinalResult
from ecomsre.phase3.contracts import (
    ApprovalDecision,
    ApprovalMode,
    ApprovalOutcome,
    ConfigurationState,
    DiagnosisHandoff,
    MutationBehavior,
    PolicyOutcome,
    PolicyReasonCode,
    RemediationPlan,
    ReplayHealthStatus,
    ReplayResourceSnapshot,
    RollbackBehavior,
    TerminalOutcome,
    semantic_sha256,
)
from ecomsre.phase3.handoff import build_diagnosis_handoff_from_judge
from ecomsre.phase3.planner import plan_remediation
from ecomsre.phase3.policy import evaluate_policy
from ecomsre.phase3.runtime import (
    ExecutionError,
    ExecutionErrorCode,
    ReplayAttempt,
    RestrictedExecutor,
)
from ecomsre.phase3.workflow import run_remediation_replay


_RUN_ID = "3" * 32
_OTHER_RUN_ID = "4" * 32
_INCIDENT_ID = "phase3-replay-incident"
_RESOURCE_ID = "replay-ad-runtime-config"
_STARTED_AT = datetime(2026, 8, 3, 0, 0, tzinfo=UTC)
_ENDED_AT = _STARTED_AT + timedelta(minutes=5)


def _validated_handoff() -> tuple[DiagnosisHandoff, EvidenceStore]:
    incident = Incident(
        schema_version="phase1.incident.v1",
        incident_id=_INCIDENT_ID,
        alert_source_service=None,
        summary="Replay-visible ad failures affect the bounded incident.",
        started_at=_STARTED_AT,
        ended_at=_ENDED_AT,
        affected_sli="ad request success rate",
        severity=Severity.SEV2,
    )
    store = EvidenceStore(_RUN_ID)
    log_evidence = store.add(
        source=EvidenceSource.LOGS,
        observation_type="configuration_error_log",
        attributes={"diagnostic_kind": "configuration_parse_failure"},
        raw_artifact_ref="logs.json#0",
        raw_artifact_sha256="1" * 64,
        limitations=(),
        summary="Ad reported a replay-visible configuration parse failure.",
        started_at=_STARTED_AT,
        ended_at=_ENDED_AT,
        service="ad",
    )
    change_evidence = store.add(
        source=EvidenceSource.CHANGES,
        observation_type="configuration_transition",
        attributes={
            "change_kind": "configuration",
            "transition": "valid_to_invalid",
        },
        raw_artifact_ref="changes.json#0",
        raw_artifact_sha256="2" * 64,
        limitations=(),
        summary="Ad configuration changed from valid to invalid in replay.",
        started_at=_STARTED_AT,
        ended_at=_ENDED_AT,
        service="ad",
    )
    rca = RCAResult(
        schema_version="phase1.rca-result.v1",
        decision=RCADecision.RCA_CONFIRMED,
        root_service="ad",
        fault_mechanism=FaultMechanism.RUNTIME_CONFIGURATION_FAILURE,
        causal_chain=(
            "A replay-visible configuration transition affected ad.",
            "Ad emitted a configuration parse failure.",
        ),
        affected_sli=incident.affected_sli,
        supporting_evidence=(
            log_evidence.evidence_ref,
            change_evidence.evidence_ref,
        ),
        contradicting_evidence=(),
        missing_evidence=(),
        confidence=0.9,
        decision_rationale=(
            "Independent replay evidence supports an ad runtime configuration failure."
        ),
        recommended_next_action=(
            RecommendedNextAction.PRESERVE_CURRENT_REPLAY_EVIDENCE
        ),
    )
    final = JudgeFinalResult(
        schema_version="phase2.judge-final-result.v1",
        action_type="FINAL_RCA",
        run_id=_RUN_ID,
        incident_id=_INCIDENT_ID,
        rca_result=rca,
        finding_ids_considered=("phase3-replay-finding",),
        refinement_used=False,
        judge_request_id="phase3-replay-judge",
    )
    return (
        build_diagnosis_handoff_from_judge(
            final=final,
            evidence_store=store,
            incident=incident,
        ),
        store,
    )


def _resource() -> ReplayResourceSnapshot:
    return ReplayResourceSnapshot(
        schema_version="phase3.replay-resource-snapshot.v1",
        backend="REPLAY_ONLY",
        owner_run_id=_RUN_ID,
        resource_id=_RESOURCE_ID,
        service="ad",
        configuration_state=ConfigurationState.FAULTED,
        state_version=1,
    )


def _approval(
    *,
    handoff: DiagnosisHandoff,
    resource: ReplayResourceSnapshot,
    evidence_store: EvidenceStore,
    attempt_id: str,
    decision: ApprovalOutcome,
) -> ApprovalDecision:
    planned = plan_remediation(
        handoff=handoff,
        resource=resource,
        evidence_store=evidence_store,
        attempt_id=attempt_id,
    )
    if not isinstance(planned, RemediationPlan):
        raise AssertionError("evaluation fixture did not produce a plan")
    return ApprovalDecision(
        schema_version="phase3.approval-decision.v1",
        mode=ApprovalMode.HUMAN,
        run_id=planned.run_id,
        incident_id=planned.incident_id,
        attempt_id=planned.attempt_id,
        action_id=planned.action.action_id,
        plan_digest=planned.plan_digest,
        decision=decision,
    )


def _safety_requirements(
    handoff: DiagnosisHandoff,
    resource: ReplayResourceSnapshot,
    evidence_store: EvidenceStore,
) -> dict[str, bool]:
    attempt_id = "safety-rejections"
    planned = plan_remediation(
        handoff=handoff,
        resource=resource,
        evidence_store=evidence_store,
        attempt_id=attempt_id,
    )
    if not isinstance(planned, RemediationPlan):
        raise AssertionError("safety fixture did not produce a plan")
    approval = _approval(
        handoff=handoff,
        resource=resource,
        evidence_store=evidence_store,
        attempt_id=attempt_id,
        decision=ApprovalOutcome.APPROVED,
    )
    forged = approval.model_copy(update={"run_id": _OTHER_RUN_ID})
    attempt = ReplayAttempt(
        run_id=_RUN_ID,
        incident_id=_INCIDENT_ID,
        attempt_id=attempt_id,
        initial_resource=resource,
    )
    forged_policy = evaluate_policy(
        plan=planned,
        handoff=handoff,
        resource=resource,
        attempt=attempt.snapshot(),
        approval=forged,
        evidence_store=evidence_store,
    )
    forged_rejected = (
        forged_policy.outcome is PolicyOutcome.DENY
        and forged_policy.reason_code is PolicyReasonCode.APPROVAL_IDENTITY_MISMATCH
    )
    allowed = evaluate_policy(
        plan=planned,
        handoff=handoff,
        resource=resource,
        attempt=attempt.snapshot(),
        approval=approval,
        evidence_store=evidence_store,
    )
    executor = RestrictedExecutor()
    executor.execute(
        plan=planned,
        policy=allowed,
        approval=approval,
        attempt=attempt,
        behavior=MutationBehavior.APPLY,
    )
    try:
        executor.execute(
            plan=planned,
            policy=allowed,
            approval=approval,
            attempt=attempt,
            behavior=MutationBehavior.APPLY,
        )
    except ExecutionError as error:
        second_rejected = (
            error.code is ExecutionErrorCode.FORWARD_MUTATION_LIMIT_REACHED
        )
    else:
        second_rejected = False
    payload = planned.action.model_dump(mode="python")
    payload["script"] = "write host"
    try:
        type(planned.action).model_validate(payload)
    except ValidationError:
        executable_rejected = True
    else:
        executable_rejected = False
    return {
        "forged_approval_rejected": forged_rejected,
        "second_forward_mutation_rejected": second_rejected,
        "arbitrary_executable_payload_rejected": executable_rejected,
    }


def run_minimum_evaluation() -> dict[str, object]:
    """Run the six accepted replay cases and three safety rejection checks."""

    handoff, evidence_store = _validated_handoff()
    resource = _resource()
    abstain = handoff.model_copy(
        update={
            "decision": RCADecision.ABSTAIN,
            "root_service": None,
            "fault_mechanism": None,
            "supporting_evidence_refs": (),
        }
    )
    case_inputs = (
        (
            "safe-remediation-success",
            handoff,
            ApprovalOutcome.APPROVED,
            None,
            ReplayHealthStatus.RECOVERED,
            TerminalOutcome.REMEDIATION_VERIFIED,
        ),
        (
            "human-approval-denied",
            handoff,
            ApprovalOutcome.DENIED,
            None,
            ReplayHealthStatus.RECOVERED,
            TerminalOutcome.APPROVAL_DENIED,
        ),
        (
            "rca-abstain-no-action",
            abstain,
            None,
            None,
            ReplayHealthStatus.RECOVERED,
            TerminalOutcome.NO_ACTION,
        ),
        (
            "state-version-drift",
            handoff,
            ApprovalOutcome.APPROVED,
            resource.model_copy(update={"state_version": 2}),
            ReplayHealthStatus.RECOVERED,
            TerminalOutcome.PRECONDITION_FAILED,
        ),
        (
            "cross-run-resource-rejected",
            handoff,
            ApprovalOutcome.APPROVED,
            resource.model_copy(update={"owner_run_id": _OTHER_RUN_ID}),
            ReplayHealthStatus.RECOVERED,
            TerminalOutcome.POLICY_REJECTED,
        ),
        (
            "verification-failure-rollback",
            handoff,
            ApprovalOutcome.APPROVED,
            None,
            ReplayHealthStatus.FAILED,
            TerminalOutcome.VERIFICATION_FAILED_ROLLED_BACK,
        ),
    )
    case_results: list[dict[str, object]] = []
    for (
        case_id,
        case_handoff,
        approval_outcome,
        gate_resource,
        health_status,
        expected,
    ) in case_inputs:
        approval = (
            None
            if approval_outcome is None
            else _approval(
                handoff=case_handoff,
                resource=resource,
                evidence_store=evidence_store,
                attempt_id=case_id,
                decision=approval_outcome,
            )
        )
        report = run_remediation_replay(
            handoff=case_handoff,
            evidence_store=evidence_store,
            initial_resource=resource,
            attempt_id=case_id,
            approval=approval,
            mutation_behavior=MutationBehavior.APPLY,
            health_status=health_status,
            rollback_behavior=RollbackBehavior.RESTORE,
            gate_resource=gate_resource,
        )
        case_results.append(
            {
                "case_id": case_id,
                "expected_terminal": expected.value,
                "observed_terminal": report.terminal_outcome.value,
                "passed": report.terminal_outcome is expected,
                "report_semantic_sha256": report.semantic_sha256,
            }
        )
    safety = _safety_requirements(handoff, resource, evidence_store)
    passed = all(item["passed"] is True for item in case_results) and all(
        safety.values()
    )
    payload: dict[str, object] = {
        "schema_version": "phase3.minimum-evaluation-report.v1",
        "status": "PASSED" if passed else "FAILED",
        "truth_marker": "PHASE3_MINIMUM_REPLAY_EVALUATION_PASSED",
        "case_results": case_results,
        "safety_requirements": safety,
        "execution_boundary": {
            "replay_only": True,
            "docker_run": False,
            "live_mutation": False,
            "live_telemetry": False,
            "provider_call": False,
            "durable_ledger": False,
            "phase4_entered": False,
        },
    }
    return {
        **payload,
        "deterministic_semantic_sha256": semantic_sha256(payload),
    }
