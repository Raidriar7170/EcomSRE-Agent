"""Pure deterministic Operational Admission policy for DTA v2."""

from __future__ import annotations

from datetime import datetime, timedelta

from ecomsre.dta_v2.authorization import (
    AttemptAuthorizationRecord,
    MasterAuthorizationRecord,
    action_parameters_sha256,
    runbook_parameter_schema_sha256,
)
from ecomsre.dta_v2.contracts import (
    ActionDisposition,
    ActionProposal,
    CandidateSet,
    DtaDiagnosis,
    Precondition,
    ResolvedDiagnosisEvidenceView,
    RiskLevel,
    semantic_sha256,
    validate_action_proposal_binding,
)
from ecomsre.dta_v2.operational_contracts import (
    AdmissionReasonCode,
    AdmissionVerdict,
    CurrentStateSnapshot,
    DockerBoundary,
    OperationalAdmission,
    OwnershipStatus,
    ServiceRuntimeState,
    build_operational_admission,
)
from ecomsre.dta_v2.registry import RunbookRegistry


def _require_utc(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError("admission time must be timezone-aware UTC")


def _result(
    *,
    reasons: tuple[AdmissionReasonCode, ...],
    proposal: ActionProposal,
    candidate_set: CandidateSet,
    current_state: CurrentStateSnapshot,
    registry: RunbookRegistry,
    runbook_sha256: str,
    authorization: AttemptAuthorizationRecord,
) -> OperationalAdmission:
    verdict = AdmissionVerdict.ALLOW if not reasons else AdmissionVerdict.DENY
    return build_operational_admission(
        verdict=verdict,
        reason_codes=(AdmissionReasonCode.ALLOWED,) if not reasons else reasons,
        current_state_sha256=current_state.snapshot_sha256,
        proposal_sha256=proposal.proposal_sha256,
        candidate_set_sha256=candidate_set.candidate_set_sha256,
        resolved_evidence_sha256=candidate_set.resolved_evidence_sha256,
        registry_sha256=registry.registry_sha256,
        runbook_sha256=runbook_sha256,
        authorization_sha256=authorization.authorization_sha256,
    )


def evaluate_operational_admission(
    *,
    registry: RunbookRegistry,
    candidate_set: CandidateSet,
    diagnosis: DtaDiagnosis,
    diagnosis_evidence: ResolvedDiagnosisEvidenceView,
    proposal: ActionProposal,
    current_state: CurrentStateSnapshot,
    master_authorization: MasterAuthorizationRecord,
    attempt_authorization: AttemptAuthorizationRecord,
    as_of: datetime,
) -> OperationalAdmission:
    """Recompute every runtime-owned gate without evaluator truth."""

    _require_utc(as_of)
    registry = RunbookRegistry.model_validate(registry.model_dump(mode="python"))
    candidate_set = CandidateSet.model_validate(
        candidate_set.model_dump(mode="python")
    )
    diagnosis = DtaDiagnosis.model_validate(diagnosis.model_dump(mode="python"))
    diagnosis_evidence = ResolvedDiagnosisEvidenceView.model_validate(
        diagnosis_evidence.model_dump(mode="python")
    )
    proposal = ActionProposal.model_validate(proposal.model_dump(mode="python"))
    current_state = CurrentStateSnapshot.model_validate(
        current_state.model_dump(mode="python")
    )
    master_authorization = MasterAuthorizationRecord.model_validate(
        master_authorization.model_dump(mode="python")
    )
    attempt_authorization = AttemptAuthorizationRecord.model_validate(
        attempt_authorization.model_dump(mode="python")
    )
    proposed_runbook_sha256 = proposal.runbook_sha256 or "0" * 64
    try:
        validate_action_proposal_binding(
            proposal=proposal,
            candidate_set=candidate_set,
            diagnosis=diagnosis,
            registry=registry,
            diagnosis_evidence=diagnosis_evidence,
        )
    except (KeyError, ValueError):
        return _result(
            reasons=(AdmissionReasonCode.PROPOSAL_BINDING_INVALID,),
            proposal=proposal,
            candidate_set=candidate_set,
            current_state=current_state,
            registry=registry,
            runbook_sha256=proposed_runbook_sha256,
            authorization=attempt_authorization,
        )
    if (
        proposal.disposition is not ActionDisposition.EXECUTE_RUNBOOK
        or proposal.runbook_id is None
        or proposal.runbook_sha256 is None
        or proposal.target_service is None
    ):
        return _result(
            reasons=(AdmissionReasonCode.PROPOSAL_BINDING_INVALID,),
            proposal=proposal,
            candidate_set=candidate_set,
            current_state=current_state,
            registry=registry,
            runbook_sha256=proposed_runbook_sha256,
            authorization=attempt_authorization,
        )
    runbook = registry.require(proposal.runbook_id)
    runbook_sha256 = semantic_sha256(runbook.model_dump(mode="json"))
    reasons: list[AdmissionReasonCode] = []
    if (
        proposal.registry_sha256 != registry.registry_sha256
        or candidate_set.registry_sha256 != registry.registry_sha256
        or master_authorization.registry_sha256 != registry.registry_sha256
    ):
        reasons.append(AdmissionReasonCode.REGISTRY_MISMATCH)
    if proposal.runbook_sha256 != runbook_sha256:
        reasons.append(AdmissionReasonCode.RUNBOOK_MISMATCH)
    if current_state.docker_boundary is not DockerBoundary.LOCAL_UNIX:
        reasons.append(AdmissionReasonCode.REMOTE_DOCKER)
    if current_state.ownership_status is not OwnershipStatus.PROVEN:
        reasons.append(AdmissionReasonCode.OWNERSHIP_NOT_PROVEN)
    if (
        current_state.run_id != proposal.run_id
        or current_state.target_logical_service != proposal.target_service
        or current_state.sandbox_identity != master_authorization.sandbox_identity
    ):
        reasons.append(AdmissionReasonCode.TARGET_MISMATCH)
    if current_state.active_transaction_count >= 1:
        reasons.append(AdmissionReasonCode.SECOND_TRANSACTION)
    if (
        current_state.prior_forward_step_count + len(runbook.forward_steps)
        > runbook.maximum_forward_steps
    ):
        reasons.append(AdmissionReasonCode.STEP_CAP_EXCEEDED)

    observed = {
        item.precondition: item.satisfied for item in current_state.preconditions
    }
    if set(observed) != set(runbook.preconditions) or not all(observed.values()):
        reasons.append(AdmissionReasonCode.PRECONDITION_FALSE)
    if (
        Precondition.CONFIGURATION_DRIFT_VISIBLE in runbook.preconditions
        and (
            current_state.configuration_state_digest is None
            or current_state.configuration_state_digest
            == current_state.baseline_digest
        )
    ):
        if AdmissionReasonCode.PRECONDITION_FALSE not in reasons:
            reasons.append(AdmissionReasonCode.PRECONDITION_FALSE)
    if (
        Precondition.SERVICE_NOT_HEALTHY in runbook.preconditions
        and current_state.service_runtime_state
        not in (
            ServiceRuntimeState.RUNNING_UNHEALTHY,
            ServiceRuntimeState.STOPPED,
        )
    ):
        if AdmissionReasonCode.PRECONDITION_FALSE not in reasons:
            reasons.append(AdmissionReasonCode.PRECONDITION_FALSE)
    if runbook.risk_level is RiskLevel.HIGH:
        reasons.append(AdmissionReasonCode.RISK_DENIED)

    if (
        as_of < master_authorization.issued_at
        or as_of < attempt_authorization.issued_at
        or as_of >= attempt_authorization.expires_at
    ):
        reasons.append(AdmissionReasonCode.AUTHORIZATION_EXPIRED)
    scope = tuple(
        item
        for item in master_authorization.authorized_runbooks
        if item.runbook_id is runbook.runbook_id
    )
    scope_matches = len(scope) == 1 and (
        scope[0].runbook_sha256 == runbook_sha256
        and scope[0].target_service == proposal.target_service
        and scope[0].risk_level is runbook.risk_level
        and scope[0].parameter_schema_sha256
        == runbook_parameter_schema_sha256(runbook)
        and scope[0].maximum_forward_steps == runbook.maximum_forward_steps
    )
    authorization_matches = (
        attempt_authorization.master_authorization_sha256
        == master_authorization.authorization_sha256
        and attempt_authorization.scenario_id
        in master_authorization.allowed_scenario_ids
        and attempt_authorization.run_id == proposal.run_id
        and attempt_authorization.attempt_id == current_state.attempt_id
        and attempt_authorization.current_state_sha256
        == current_state.snapshot_sha256
        and attempt_authorization.proposal_sha256 == proposal.proposal_sha256
        and attempt_authorization.candidate_set_sha256
        == candidate_set.candidate_set_sha256
        and attempt_authorization.diagnosis_sha256
        == candidate_set.diagnosis_sha256
        and attempt_authorization.resolved_evidence_sha256
        == diagnosis_evidence.resolved_evidence_sha256
        and attempt_authorization.registry_sha256 == registry.registry_sha256
        and attempt_authorization.runbook_id is runbook.runbook_id
        and attempt_authorization.runbook_sha256 == runbook_sha256
        and attempt_authorization.target_service == proposal.target_service
        and attempt_authorization.parameters_sha256
        == action_parameters_sha256(proposal)
        and attempt_authorization.risk_level is runbook.risk_level
        and attempt_authorization.maximum_forward_steps
        == runbook.maximum_forward_steps
        and scope_matches
    )
    if not authorization_matches:
        reasons.append(AdmissionReasonCode.AUTHORIZATION_BINDING_MISMATCH)
    return _result(
        reasons=tuple(reasons),
        proposal=proposal,
        candidate_set=candidate_set,
        current_state=current_state,
        registry=registry,
        runbook_sha256=runbook_sha256,
        authorization=attempt_authorization,
    )
