"""Deterministic replay-only remediation Planner."""

from __future__ import annotations

import hashlib

from ecomsre.phase1.contracts import FaultMechanism, RCADecision
from ecomsre.phase1.evidence import EvidenceStore
from ecomsre.phase3.contracts import (
    ActionType,
    ConfigurationState,
    DiagnosisHandoff,
    NoAction,
    PlannerReasonCode,
    RemediationAction,
    RemediationPlan,
    ReplayResourceSnapshot,
    make_plan_digest,
)
from ecomsre.phase3.handoff import (
    HandoffError,
    resolve_current_run_supporting_evidence,
)


def _stable_id(prefix: str, *parts: str) -> str:
    material = "\x00".join(parts).encode("utf-8")
    return f"{prefix}-{hashlib.sha256(material).hexdigest()[:20]}"


def _no_action(
    *,
    handoff: DiagnosisHandoff,
    attempt_id: str,
    reason_code: PlannerReasonCode,
) -> NoAction:
    return NoAction(
        schema_version="phase3.no-action.v1",
        decision="NO_ACTION",
        run_id=handoff.run_id,
        incident_id=handoff.incident_id,
        attempt_id=attempt_id,
        reason_code=reason_code,
    )


def plan_remediation(
    *,
    handoff: DiagnosisHandoff,
    resource: ReplayResourceSnapshot,
    attempt_id: str,
    evidence_store: EvidenceStore,
) -> RemediationPlan | NoAction:
    """Emit the one allowed plan or a typed fail-closed no-action result."""

    validated_handoff = DiagnosisHandoff.model_validate(
        handoff.model_dump(mode="python")
    )
    validated_resource = ReplayResourceSnapshot.model_validate(
        resource.model_dump(mode="python")
    )
    if validated_handoff.decision is not RCADecision.RCA_CONFIRMED:
        return _no_action(
            handoff=validated_handoff,
            attempt_id=attempt_id,
            reason_code=PlannerReasonCode.RCA_NOT_CONFIRMED,
        )
    if (
        validated_handoff.root_service != "ad"
        or validated_handoff.fault_mechanism
        is not FaultMechanism.RUNTIME_CONFIGURATION_FAILURE
    ):
        return _no_action(
            handoff=validated_handoff,
            attempt_id=attempt_id,
            reason_code=PlannerReasonCode.RCA_ACTION_MISMATCH,
        )
    try:
        resolved_evidence = resolve_current_run_supporting_evidence(
            handoff=validated_handoff,
            evidence_store=evidence_store,
        )
    except HandoffError:
        resolved_evidence = ()
    if not resolved_evidence:
        return _no_action(
            handoff=validated_handoff,
            attempt_id=attempt_id,
            reason_code=PlannerReasonCode.EVIDENCE_UNRESOLVED,
        )
    if validated_handoff.missing_evidence:
        return _no_action(
            handoff=validated_handoff,
            attempt_id=attempt_id,
            reason_code=PlannerReasonCode.MISSING_EVIDENCE,
        )
    if validated_resource.backend != "REPLAY_ONLY":
        return _no_action(
            handoff=validated_handoff,
            attempt_id=attempt_id,
            reason_code=PlannerReasonCode.TARGET_NOT_REPLAY_ONLY,
        )
    if validated_resource.owner_run_id != validated_handoff.run_id:
        return _no_action(
            handoff=validated_handoff,
            attempt_id=attempt_id,
            reason_code=PlannerReasonCode.RESOURCE_NOT_CURRENT_RUN,
        )
    if validated_resource.configuration_state is not ConfigurationState.FAULTED:
        return _no_action(
            handoff=validated_handoff,
            attempt_id=attempt_id,
            reason_code=PlannerReasonCode.PRE_STATE_MISMATCH,
        )

    action = RemediationAction(
        schema_version="phase3.remediation-action.v1",
        action_type=ActionType.RESTORE_FROZEN_SERVICE_CONFIGURATION,
        action_id=_stable_id(
            "action",
            validated_handoff.run_id,
            validated_handoff.incident_id,
            attempt_id,
            validated_resource.resource_id,
        ),
        run_id=validated_handoff.run_id,
        incident_id=validated_handoff.incident_id,
        attempt_id=attempt_id,
        resource_id=validated_resource.resource_id,
        target_service="ad",
        target_backend="REPLAY_ONLY",
        expected_pre_state=ConfigurationState.FAULTED,
        desired_state=ConfigurationState.FROZEN,
        expected_state_version=validated_resource.state_version,
        blast_radius=1,
    )
    payload: dict[str, object] = {
        "schema_version": "phase3.remediation-plan.v1",
        "plan_id": _stable_id("plan", action.action_id),
        "run_id": action.run_id,
        "incident_id": action.incident_id,
        "attempt_id": action.attempt_id,
        "action": action.model_dump(mode="json"),
    }
    return RemediationPlan.model_validate(
        {
            **payload,
            "plan_digest": make_plan_digest(payload),
        }
    )
