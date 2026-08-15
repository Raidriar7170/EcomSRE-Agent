from __future__ import annotations

import hashlib
import json
import stat
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from ecomsre.dta_v2.authorization import (
    AuthorizedRunbookScope,
    build_master_authorization,
    derive_attempt_authorization,
    load_master_authorization,
    persist_master_authorization,
)
from ecomsre.dta_v2.candidate_filter import filter_runbook_candidates
from ecomsre.dta_v2.contracts import (
    ActionDisposition,
    ActionParameter,
    ActionProposal,
    CandidateSet,
    DtaDiagnosis,
    EvidenceSource,
    FaultDomain,
    FaultMechanism,
    ResolvedDiagnosisEvidenceView,
    ResolvedEvidence,
    RiskLevel,
    RunbookId,
    Terminal,
    build_action_proposal,
    build_resolved_diagnosis_evidence_view,
    semantic_sha256,
)
from ecomsre.dta_v2.operational_contracts import (
    AdmissionReasonCode,
    AdmissionVerdict,
    DockerBoundary,
    OwnershipStatus,
    PreconditionObservation,
    ServiceRuntimeState,
    build_current_state_snapshot,
)
from ecomsre.dta_v2.policy import evaluate_operational_admission
from ecomsre.dta_v2.registry import RunbookRegistry, load_runbook_registry


RUN_ID = "a" * 32
ATTEMPT_ID = "attempt-001"
GOAL_SHA256 = "7ecced0b1698f4a8b3e1f4e8a25f72598cc3ac6d3791bd74827618eb00bb10ea"
NOW = datetime(2026, 8, 16, 8, 0, tzinfo=timezone.utc)
REPO_ROOT = Path(__file__).resolve().parents[2]
RUNBOOK_ROOT = REPO_ROOT / "config" / "dta-v2" / "runbooks"


@dataclass(frozen=True)
class CaseArtifacts:
    diagnosis: DtaDiagnosis
    evidence: ResolvedDiagnosisEvidenceView
    candidates: CandidateSet
    proposal: ActionProposal


_CASE = {
    RunbookId.ROLLBACK_CONFIGURATION: (
        "payment",
        FaultDomain.CONFIGURATION,
        FaultMechanism.CONFIGURATION_ERROR,
        (EvidenceSource.METRICS, EvidenceSource.TRACES),
        (),
        "dta-dev-001",
    ),
    RunbookId.RESTART_SERVICE: (
        "recommendation",
        FaultDomain.SERVICE_RUNTIME,
        FaultMechanism.SERVICE_UNAVAILABLE,
        (EvidenceSource.METRICS, EvidenceSource.RUNTIME),
        (ActionParameter(name="wait_for_health_seconds", value=30),),
        "dta-dev-002",
    ),
    RunbookId.MITIGATE_MEMORY_LEAK: (
        "email",
        FaultDomain.LOCAL_RESOURCE,
        FaultMechanism.MEMORY_LEAK,
        (
            EvidenceSource.METRICS,
            EvidenceSource.RUNTIME,
            EvidenceSource.RESOURCES,
        ),
        (ActionParameter(name="wait_for_health_seconds", value=30),),
        "dta-dev-003",
    ),
}


def case_artifacts(
    registry: RunbookRegistry,
    runbook_id: RunbookId,
) -> CaseArtifacts:
    service, domain, mechanism, sources, parameters, _ = _CASE[runbook_id]
    refs = tuple(
        f"evidence://{RUN_ID}/{source.value.lower()}/{index:04d}"
        for index, source in enumerate(sources, start=1)
    )
    diagnosis = DtaDiagnosis(
        schema_version="dta-v2.diagnosis.v1",
        run_id=RUN_ID,
        terminal=Terminal.COMPLETED,
        root_service=service,
        root_entity_ref=f"service:{service}",
        fault_domain=domain,
        mechanism=mechanism,
        confidence=0.9,
        supporting_evidence_refs=refs,
        contradicting_evidence_refs=(),
        evidence_source_types=sources,
        uncertainties=(),
        summary="Bounded evidence supports the compatible mechanism.",
    )
    evidence = build_resolved_diagnosis_evidence_view(
        run_id=RUN_ID,
        evidence=tuple(
            ResolvedEvidence(
                evidence_ref=reference,
                source=source,
                artifact_sha256=hashlib.sha256(reference.encode()).hexdigest(),
            )
            for reference, source in zip(refs, sources, strict=True)
        ),
    )
    candidates = filter_runbook_candidates(
        diagnosis=diagnosis,
        registry=registry,
        diagnosis_evidence=evidence,
    )
    proposal = build_action_proposal(
        candidate_set=candidates,
        diagnosis=diagnosis,
        registry=registry,
        diagnosis_evidence=evidence,
        disposition=ActionDisposition.EXECUTE_RUNBOOK,
        runbook_id=runbook_id,
        target_service=service,
        parameters=parameters,
        supporting_evidence_refs=refs,
        rationale="The selected typed Runbook matches the bounded evidence.",
    )
    return CaseArtifacts(diagnosis, evidence, candidates, proposal)


def master_authorization(registry: RunbookRegistry):
    return build_master_authorization(
        registry=registry,
        goal_version="dta-v2-master-v1",
        goal_sha256=GOAL_SHA256,
        allowed_scenario_ids=("dta-dev-001", "dta-dev-002", "dta-dev-003"),
        sandbox_identity="ecomsre-live-sandbox-v1",
        issued_at=NOW,
    )


def current_state(
    registry: RunbookRegistry,
    runbook_id: RunbookId,
    *,
    docker_boundary: DockerBoundary = DockerBoundary.LOCAL_UNIX,
    ownership_status: OwnershipStatus = OwnershipStatus.PROVEN,
    active_transaction_count: int = 0,
    prior_forward_step_count: int = 0,
    false_precondition: str | None = None,
):
    service, *_ = _CASE[runbook_id]
    runbook = registry.require(runbook_id)
    runtime = ServiceRuntimeState.RUNNING_HEALTHY
    if runbook_id is RunbookId.RESTART_SERVICE:
        runtime = ServiceRuntimeState.STOPPED
    observations = tuple(
        PreconditionObservation(
            precondition=precondition,
            satisfied=precondition.value != false_precondition,
        )
        for precondition in runbook.preconditions
    )
    return build_current_state_snapshot(
        run_id=RUN_ID,
        attempt_id=ATTEMPT_ID,
        docker_boundary=docker_boundary,
        docker_context_identity="1" * 64,
        daemon_identity="2" * 64,
        sandbox_identity="ecomsre-live-sandbox-v1",
        ownership_digest="3" * 64,
        ownership_status=ownership_status,
        target_logical_service=service,
        service_runtime_state=runtime,
        configuration_state_digest=(
            "4" * 64
            if runbook_id is RunbookId.ROLLBACK_CONFIGURATION
            else None
        ),
        baseline_digest="5" * 64,
        active_transaction_count=active_transaction_count,
        prior_forward_step_count=prior_forward_step_count,
        preconditions=observations,
        observed_at_start=NOW,
        observed_at_end=NOW + timedelta(seconds=2),
        observation_monotonic_duration_ms=2000,
    )


def admission_for(
    registry: RunbookRegistry,
    runbook_id: RunbookId,
    *,
    snapshot=None,
    proposal: ActionProposal | None = None,
    authorization=None,
    as_of: datetime = NOW + timedelta(minutes=1),
):
    artifacts = case_artifacts(registry, runbook_id)
    selected_proposal = proposal or artifacts.proposal
    selected_snapshot = snapshot or current_state(registry, runbook_id)
    master = master_authorization(registry)
    _, _, _, _, _, scenario_id = _CASE[runbook_id]
    child = authorization or derive_attempt_authorization(
        master=master,
        scenario_id=scenario_id,
        registry=registry,
        candidate_set=artifacts.candidates,
        diagnosis=artifacts.diagnosis,
        diagnosis_evidence=artifacts.evidence,
        proposal=artifacts.proposal,
        current_state=selected_snapshot,
        issued_at=NOW + timedelta(seconds=10),
        expires_at=NOW + timedelta(hours=1),
    )
    return evaluate_operational_admission(
        registry=registry,
        candidate_set=artifacts.candidates,
        diagnosis=artifacts.diagnosis,
        diagnosis_evidence=artifacts.evidence,
        proposal=selected_proposal,
        current_state=selected_snapshot,
        master_authorization=master,
        attempt_authorization=child,
        as_of=as_of,
    )


def rehash(model, *, field: str, value: object, digest_field: str):
    payload = {
        name: getattr(model, name)
        for name in type(model).model_fields
    }
    payload[field] = value
    draft = type(model).model_construct(**payload)
    payload[digest_field] = semantic_sha256(
        draft.model_dump(mode="json", exclude={digest_field})
    )
    return type(model).model_validate(payload)


def test_master_authorization_is_exact_create_once_and_child_is_run_bound(
    tmp_path: Path,
) -> None:
    registry = load_runbook_registry(RUNBOOK_ROOT)
    master = master_authorization(registry)

    assert master.authorization_mode == "DTA_V2_MASTER_STANDING_AUTHORIZATION"
    assert master.codex_autonomous_self_approval is False
    assert master.additional_human_confirmation_required is False
    assert master.allowed_scenario_ids == (
        "dta-dev-001",
        "dta-dev-002",
        "dta-dev-003",
    )
    assert all(
        "scenario" not in scope.model_dump(mode="json")
        for scope in master.authorized_runbooks
    )
    assert master.registry_sha256 == registry.registry_sha256

    private_root = tmp_path / "dta-v2-master-v1"
    path = private_root / "master" / "master-authorization.json"
    persist_master_authorization(path, master)
    assert stat.S_IMODE(private_root.stat().st_mode) == 0o700
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert load_master_authorization(path) == master
    with pytest.raises(FileExistsError):
        persist_master_authorization(path, master)

    artifacts = case_artifacts(registry, RunbookId.MITIGATE_MEMORY_LEAK)
    snapshot = current_state(registry, RunbookId.MITIGATE_MEMORY_LEAK)
    child = derive_attempt_authorization(
        master=master,
        scenario_id="dta-dev-003",
        registry=registry,
        candidate_set=artifacts.candidates,
        diagnosis=artifacts.diagnosis,
        diagnosis_evidence=artifacts.evidence,
        proposal=artifacts.proposal,
        current_state=snapshot,
        issued_at=NOW + timedelta(seconds=10),
        expires_at=NOW + timedelta(hours=1),
    )
    assert child.run_id == RUN_ID
    assert child.attempt_id == ATTEMPT_ID
    assert child.master_authorization_sha256 == master.authorization_sha256
    assert child.proposal_sha256 == artifacts.proposal.proposal_sha256
    assert child.current_state_sha256 == snapshot.snapshot_sha256
    assert child.maximum_forward_steps == 2


@pytest.mark.parametrize("runbook_id", tuple(RunbookId))
def test_operational_admission_allows_each_exact_mvp_runbook(
    runbook_id: RunbookId,
) -> None:
    registry = load_runbook_registry(RUNBOOK_ROOT)
    admission = admission_for(registry, runbook_id)

    assert admission.verdict is AdmissionVerdict.ALLOW
    assert admission.reason_codes == (AdmissionReasonCode.ALLOWED,)
    assert admission.registry_sha256 == registry.registry_sha256
    assert admission.runbook_sha256 == semantic_sha256(
        registry.require(runbook_id).model_dump(mode="json")
    )


@pytest.mark.parametrize("runbook_id", tuple(RunbookId))
@pytest.mark.parametrize(
    ("snapshot_kwargs", "reason"),
    [
        (
            {"false_precondition": "OWNED_SERVICE"},
            AdmissionReasonCode.PRECONDITION_FALSE,
        ),
        (
            {"ownership_status": OwnershipStatus.MISMATCH},
            AdmissionReasonCode.OWNERSHIP_NOT_PROVEN,
        ),
        (
            {"docker_boundary": DockerBoundary.REMOTE},
            AdmissionReasonCode.REMOTE_DOCKER,
        ),
        (
            {"active_transaction_count": 1},
            AdmissionReasonCode.SECOND_TRANSACTION,
        ),
        (
            {"prior_forward_step_count": 1},
            AdmissionReasonCode.STEP_CAP_EXCEEDED,
        ),
    ],
)
def test_operational_admission_denies_current_state_boundaries(
    runbook_id: RunbookId,
    snapshot_kwargs: dict[str, Any],
    reason: AdmissionReasonCode,
) -> None:
    registry = load_runbook_registry(RUNBOOK_ROOT)
    if "false_precondition" in snapshot_kwargs:
        snapshot_kwargs = {
            **snapshot_kwargs,
            "false_precondition": registry.require(runbook_id).preconditions[0].value,
        }
    snapshot = current_state(registry, runbook_id, **snapshot_kwargs)
    admission = admission_for(registry, runbook_id, snapshot=snapshot)

    assert admission.verdict is AdmissionVerdict.DENY
    assert reason in admission.reason_codes


@pytest.mark.parametrize("runbook_id", tuple(RunbookId))
@pytest.mark.parametrize("forgery", ("target", "runbook", "parameters"))
def test_admission_recomputes_wrong_target_runbook_and_parameters(
    runbook_id: RunbookId,
    forgery: str,
) -> None:
    registry = load_runbook_registry(RUNBOOK_ROOT)
    artifacts = case_artifacts(registry, runbook_id)
    alternate = next(item for item in RunbookId if item is not runbook_id)
    alternate_target = next(
        values[0] for candidate, values in _CASE.items() if candidate is not runbook_id
    )
    field, value = {
        "target": ("target_service", alternate_target),
        "runbook": ("runbook_id", alternate),
        "parameters": (
            "parameters",
            (ActionParameter(name="wait_for_health_seconds", value=121),),
        ),
    }[forgery]
    forged = rehash(
        artifacts.proposal,
        field=field,
        value=value,
        digest_field="proposal_sha256",
    )
    admission = admission_for(
        registry,
        runbook_id,
        proposal=forged,
    )

    assert admission.verdict is AdmissionVerdict.DENY
    assert AdmissionReasonCode.PROPOSAL_BINDING_INVALID in admission.reason_codes


@pytest.mark.parametrize("runbook_id", tuple(RunbookId))
def test_admission_denies_expired_and_mismatched_attempt_authorization(
    runbook_id: RunbookId,
) -> None:
    registry = load_runbook_registry(RUNBOOK_ROOT)
    artifacts = case_artifacts(registry, runbook_id)
    snapshot = current_state(registry, runbook_id)
    master = master_authorization(registry)
    child = derive_attempt_authorization(
        master=master,
        scenario_id=_CASE[runbook_id][-1],
        registry=registry,
        candidate_set=artifacts.candidates,
        diagnosis=artifacts.diagnosis,
        diagnosis_evidence=artifacts.evidence,
        proposal=artifacts.proposal,
        current_state=snapshot,
        issued_at=NOW + timedelta(seconds=10),
        expires_at=NOW + timedelta(minutes=2),
    )
    expired = admission_for(
        registry,
        runbook_id,
        snapshot=snapshot,
        authorization=child,
        as_of=NOW + timedelta(minutes=3),
    )
    assert AdmissionReasonCode.AUTHORIZATION_EXPIRED in expired.reason_codes

    mismatched = rehash(
        child,
        field="current_state_sha256",
        value="f" * 64,
        digest_field="authorization_sha256",
    )
    denied = admission_for(
        registry,
        runbook_id,
        snapshot=snapshot,
        authorization=mismatched,
    )
    assert AdmissionReasonCode.AUTHORIZATION_BINDING_MISMATCH in denied.reason_codes


def test_master_authorization_json_contains_no_scenario_to_runbook_mapping() -> None:
    registry = load_runbook_registry(RUNBOOK_ROOT)
    payload = json.dumps(master_authorization(registry).model_dump(mode="json"))

    assert "scenario_runbook" not in payload
    assert "expected_runbook" not in payload


def test_master_authorization_persistence_rejects_symlinks_and_weak_modes(
    tmp_path: Path,
) -> None:
    registry = load_runbook_registry(RUNBOOK_ROOT)
    master = master_authorization(registry)
    real_parent = tmp_path / "real-private"
    real_parent.mkdir(mode=0o700)
    linked_parent = tmp_path / "linked-private"
    linked_parent.symlink_to(real_parent, target_is_directory=True)

    with pytest.raises(ValueError, match="symlink"):
        persist_master_authorization(linked_parent / "authorization.json", master)

    path = real_parent / "authorization.json"
    persist_master_authorization(path, master)
    path.chmod(0o644)
    with pytest.raises(ValueError, match="0600"):
        load_master_authorization(path)


def test_master_authorization_rejects_an_alternate_scenario_catalog() -> None:
    registry = load_runbook_registry(RUNBOOK_ROOT)
    master = master_authorization(registry)

    with pytest.raises(ValueError, match="exact MVP scenario set"):
        rehash(
            master,
            field="allowed_scenario_ids",
            value=("alternate-scenario",),
            digest_field="authorization_sha256",
        )


def test_high_risk_scope_is_denied() -> None:
    registry = load_runbook_registry(RUNBOOK_ROOT)
    scope = master_authorization(registry).authorized_runbooks[0]

    with pytest.raises(ValueError, match="HIGH risk"):
        AuthorizedRunbookScope.model_validate(
            {
                **scope.model_dump(mode="python"),
                "risk_level": RiskLevel.HIGH,
            }
        )
