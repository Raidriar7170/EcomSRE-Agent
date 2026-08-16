from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest

from ecomsre.dta_v2.contracts import (
    ActionDisposition,
    RunbookId,
    build_action_proposal,
)
from ecomsre.dta_v2.live_state import require_trusted_live_current_state
from ecomsre.dta_v2.operational_contracts import (
    AdmissionReasonCode,
    AdmissionVerdict,
    DockerBoundary,
    OwnershipStatus,
)
from ecomsre.dta_v2.policy import evaluate_nonwrite_operational_admission
from ecomsre.dta_v2.registry import load_runbook_registry

from test_admission_policy import (
    NOW,
    case_artifacts,
    current_state,
    master_authorization,
)


ROOT = Path(__file__).resolve().parents[2]
RUNBOOK_ROOT = ROOT / "config" / "dta-v2" / "runbooks"


def test_live_state_projection_accepts_only_exact_owned_local_state() -> None:
    registry = load_runbook_registry(RUNBOOK_ROOT)
    master = master_authorization(registry)
    snapshot = current_state(registry, RunbookId.RESTART_SERVICE)

    assert (
        require_trusted_live_current_state(
            snapshot=snapshot,
            registry=registry,
            master_authorization=master,
            expected_run_id=snapshot.run_id,
            expected_attempt_id=snapshot.attempt_id,
            authoritative_target="recommendation",
        )
        == snapshot
    )

    forged = snapshot.model_copy(
        update={"daemon_identity": "f" * 64},
    )
    with pytest.raises(ValueError, match="snapshot digest"):
        require_trusted_live_current_state(
            snapshot=forged,
            registry=registry,
            master_authorization=master,
            expected_run_id=snapshot.run_id,
            expected_attempt_id=snapshot.attempt_id,
            authoritative_target="recommendation",
        )


@pytest.mark.parametrize(
    ("snapshot_kwargs", "message"),
    [
        ({"docker_boundary": DockerBoundary.REMOTE}, "local Unix"),
        ({"ownership_status": OwnershipStatus.UNKNOWN}, "ownership"),
        ({"ownership_status": OwnershipStatus.MISMATCH}, "ownership"),
    ],
)
def test_live_state_projection_rejects_untrusted_runtime_boundaries(
    snapshot_kwargs: dict[str, object],
    message: str,
) -> None:
    registry = load_runbook_registry(RUNBOOK_ROOT)
    snapshot = current_state(
        registry,
        RunbookId.RESTART_SERVICE,
        **snapshot_kwargs,
    )
    with pytest.raises(ValueError, match=message):
        require_trusted_live_current_state(
            snapshot=snapshot,
            registry=registry,
            master_authorization=master_authorization(registry),
            expected_run_id=snapshot.run_id,
            expected_attempt_id=snapshot.attempt_id,
            authoritative_target="recommendation",
        )


def test_nonwrite_admission_is_deny_and_uses_only_master_authority() -> None:
    registry = load_runbook_registry(RUNBOOK_ROOT)
    artifacts = case_artifacts(registry, RunbookId.ROLLBACK_CONFIGURATION)
    proposal = build_action_proposal(
        candidate_set=artifacts.candidates,
        diagnosis=artifacts.diagnosis,
        registry=registry,
        diagnosis_evidence=artifacts.evidence,
        disposition=ActionDisposition.NO_ACTION,
        runbook_id=None,
        target_service=None,
        parameters=(),
        supporting_evidence_refs=artifacts.diagnosis.supporting_evidence_refs,
        rationale="The healthy control requires no write.",
    )
    snapshot = current_state(registry, RunbookId.ROLLBACK_CONFIGURATION)
    master = master_authorization(registry)

    admission = evaluate_nonwrite_operational_admission(
        registry=registry,
        candidate_set=artifacts.candidates,
        diagnosis=artifacts.diagnosis,
        diagnosis_evidence=artifacts.evidence,
        proposal=proposal,
        current_state=snapshot,
        master_authorization=master,
        as_of=NOW + timedelta(minutes=1),
    )

    assert admission.verdict is AdmissionVerdict.DENY
    assert admission.reason_codes == (AdmissionReasonCode.NONWRITE_ACTION_PROPOSAL,)
    assert admission.authorization_sha256 == master.authorization_sha256
    assert admission.runbook_sha256 == "0" * 64
    assert admission.proposal_sha256 == proposal.proposal_sha256


def test_nonwrite_admission_rejects_execute_and_forged_bindings() -> None:
    registry = load_runbook_registry(RUNBOOK_ROOT)
    artifacts = case_artifacts(registry, RunbookId.ROLLBACK_CONFIGURATION)
    snapshot = current_state(registry, RunbookId.ROLLBACK_CONFIGURATION)
    master = master_authorization(registry)

    with pytest.raises(ValueError, match="non-write"):
        evaluate_nonwrite_operational_admission(
            registry=registry,
            candidate_set=artifacts.candidates,
            diagnosis=artifacts.diagnosis,
            diagnosis_evidence=artifacts.evidence,
            proposal=artifacts.proposal,
            current_state=snapshot,
            master_authorization=master,
            as_of=NOW + timedelta(minutes=1),
        )

    forged = artifacts.proposal.model_copy(
        update={"disposition": ActionDisposition.NO_ACTION},
    )
    with pytest.raises(ValueError):
        evaluate_nonwrite_operational_admission(
            registry=registry,
            candidate_set=artifacts.candidates,
            diagnosis=artifacts.diagnosis,
            diagnosis_evidence=artifacts.evidence,
            proposal=forged,
            current_state=snapshot,
            master_authorization=master,
            as_of=NOW + timedelta(minutes=1),
        )
