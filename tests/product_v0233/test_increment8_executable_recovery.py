from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22
from ecomsre.product.jobs.contracts import (
    ProductJobRecordV1,
    ProductJobStatusV1,
    ProductJobTypeV1,
)
from ecomsre.product.pilot.diagnosis_recovery_v0233 import (
    FormalDiagnosisJobContextV0233,
    final_diagnosis_idempotency_key_v0233,
)
from ecomsre.product.pilot.formal_live_v0233 import (
    FormalObservedStateCountsV0233,
    FormalSafetyObservationV0233,
)
from ecomsre.product.pilot.formal_recovery_v0233 import (
    FormalAttemptLedgerV0233,
    FormalAttemptRecordV0233,
    FormalExecutionStateV0233,
)
from scripts.product_v0233 import resume_formal_nofault as resume_command
from scripts.product_v0233 import run_formal_nofault as run_command


def _sha(character: str) -> str:
    return character * 64


def _artifact(**values):
    payload = {
        key: getattr(value, "value", value)
        for key, value in values.items()
    }
    return SimpleNamespace(
        **values,
        model_dump=lambda mode="json": dict(payload),
    )


def _counts(*, incident: int, jobs: int, diagnosis: int) -> FormalObservedStateCountsV0233:
    return FormalObservedStateCountsV0233(
        baseline_count=1,
        active_baseline_count=1,
        baseline_job_count=1,
        verify_job_count=1,
        diagnosis_job_count=jobs,
        incident_count=incident,
        diagnosis_count=diagnosis,
        evidence_object_count=max(diagnosis, 1),
        diagnosis_evidence_index_count=diagnosis,
        diagnosis_stage_event_count=max(jobs, 1),
        fault_family_count=0,
        knowledge_artifact_count=0,
        pending_job_count=0,
        running_job_count=0,
        failed_job_count=0,
    )


class _Processes:
    def __init__(self, **_values) -> None:
        pass

    def start(self) -> None:
        pass

    def cleanup_observation(self):
        return {"verdict": "CLEAN"}


class _Jobs:
    def __init__(
        self,
        job: ProductJobRecordV1,
        recovery_success: ProductJobRecordV1 | None = None,
    ) -> None:
        self.jobs = {job.job_id: job}
        self.recovery_success = recovery_success
        self.enqueue_calls = 0

    def get(self, job_id: str) -> ProductJobRecordV1:
        return self.jobs[job_id]

    def enqueue(self, job_type, payload, *, idempotency_key):
        self.enqueue_calls += 1
        if self.recovery_success is None:
            raise AssertionError("post-success finalization must not enqueue Diagnosis")
        queued = self.recovery_success.model_copy(
            update={
                "status": ProductJobStatusV1.PENDING,
                "payload": dict(payload),
                "result": None,
                "idempotency_key": idempotency_key,
                "safe_error_code": None,
            }
        )
        assert job_type is ProductJobTypeV1.DIAGNOSIS
        self.jobs[queued.job_id] = queued
        return queued


def test_process_interruption_at_every_stage_is_classified_from_durable_acquisition() -> (
    None
):
    for stage in FormalExecutionStateV0233:
        if stage in {
            FormalExecutionStateV0233.CLOSED,
            FormalExecutionStateV0233.NONRECOVERABLE_FAILURE,
        }:
            continue
        assert run_command._failure_checkpoint_state_v0233(
            acquisition_sealed=False
        ) is FormalExecutionStateV0233.NONRECOVERABLE_FAILURE, stage
        assert run_command._failure_checkpoint_state_v0233(
            acquisition_sealed=True
        ) is FormalExecutionStateV0233.RECOVERABLE_FAILURE, stage


def test_recovery_starts_next_attempt_only_after_nonrecoverable_terminal_is_sealed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempt_id = "attempt-2"
    terminal = "BLOCKED_ECOMSRE_PRODUCT_V0233_ACCEPTANCE_ARTIFACTS"
    checkpoint_sha256 = _sha("2")
    legacy = FormalAttemptRecordV0233.build(
        attempt_id="attempt-1",
        ordinal=1,
        semantic_generation=1,
        disposition="LEGACY_BLOCKED",
        latest_state=FormalExecutionStateV0233.RECOVERABLE_FAILURE,
        latest_checkpoint_sha256=None,
        blocker_terminal=terminal,
        measured_terminal=None,
        evidence_sha256_by_path={},
    )
    retired = FormalAttemptRecordV0233.build(
        attempt_id=attempt_id,
        ordinal=2,
        semantic_generation=2,
        disposition="NONRECOVERABLE_FAILURE",
        latest_state=FormalExecutionStateV0233.NONRECOVERABLE_FAILURE,
        latest_checkpoint_sha256=checkpoint_sha256,
        blocker_terminal=terminal,
        measured_terminal=None,
        evidence_sha256_by_path={},
    )
    ledger = FormalAttemptLedgerV0233.build(
        campaign_id="product-v0233-fresh-formal-nofault",
        attempts=(legacy, retired),
    )
    ledger_path = tmp_path / "config/product-v0233/formal-attempt-ledger.json"
    ledger_path.parent.mkdir(parents=True)
    ledger_path.write_text(ledger.model_dump_json(), encoding="utf-8")
    completion_body = {
        "schema_version": "ecomsre.product.terminal-publication-completion.v0233",
        "publication_sha256": _sha("3"),
        "terminal": terminal,
    }
    completion_path = (
        tmp_path
        / ".local/product-v0233/attempts/attempt-2/execution/"
        "terminal-publication-completion.json"
    )
    completion_path.parent.mkdir(parents=True)
    completion_path.write_text(
        json.dumps(
            {
                **completion_body,
                "completion_sha256": semantic_sha256_v22(completion_body),
            }
        ),
        encoding="utf-8",
    )
    calls: list[dict[str, object]] = []
    expected = SimpleNamespace(result_sha256=_sha("4"))
    monkeypatch.setattr(
        resume_command,
        "run_formal_nofault_v0233",
        lambda **kwargs: (calls.append(kwargs), expected)[1],
    )

    observed = resume_command._start_successor_after_nonrecoverable_v0233(
        root=tmp_path,
        attempt_id=attempt_id,
        latest=SimpleNamespace(
            state=FormalExecutionStateV0233.NONRECOVERABLE_FAILURE,
            checkpoint_sha256=checkpoint_sha256,
            semantic_generation=2,
        ),
        trigger=RuntimeError(terminal),
    )

    assert observed is expected
    assert calls == [
        {
            "project_root": tmp_path,
            "attempt_id": "attempt-3",
            "semantic_generation": 2,
        }
    ]


@pytest.mark.parametrize(
    ("recovery_required", "latest_state", "private_acquisition"),
    (
        (False, FormalExecutionStateV0233.RECOVERABLE_FAILURE, True),
        (True, FormalExecutionStateV0233.RECOVERABLE_FAILURE, True),
        (False, FormalExecutionStateV0233.INCIDENT_CREATED, False),
    ),
)
def test_resume_executes_post_success_or_failed_job_recovery(
    tmp_path: Path,
    monkeypatch,
    recovery_required: bool,
    latest_state: FormalExecutionStateV0233,
    private_acquisition: bool,
) -> None:
    attempt_id = "attempt-2"
    private_root = tmp_path / ".local/product-v0233/attempts" / attempt_id / "execution"
    private_root.mkdir(parents=True)
    product_root = tmp_path / ".local/product-v0233/formal-state" / attempt_id
    product_root.mkdir(parents=True)
    (private_root / "diagnosis-job.json").write_text("{}\n", encoding="utf-8")
    if private_acquisition:
        (private_root / "diagnosis-acquisition-checkpoint.json").write_text(
            "{}\n", encoding="utf-8"
        )
    preflight_path = tmp_path / "docs/analysis/product-v0233-traffic-preflight.json"
    preflight_path.parent.mkdir(parents=True)
    preflight_path.write_text(
        json.dumps({"preflight_sha256": _sha("f")}),
        encoding="utf-8",
    )

    semantic = SimpleNamespace(semantic_surface_sha256=_sha("1"))
    operational = SimpleNamespace(operational_surface_sha256=_sha("2"))
    latest = SimpleNamespace(
        campaign_id="product-v0233-fresh-formal-nofault",
        semantic_generation=2,
        attempt_id=attempt_id,
        state=latest_state,
        semantic_surface_sha256=_sha("1"),
        operational_surface_sha256=_sha("2"),
        source_selection_sha256=_sha("3"),
        formal_clone_sha256=_sha("4"),
        output_artifact_sha256s=(
            {
                ".local/product-v0233/attempts/attempt-2/execution/"
                "diagnosis-acquisition-checkpoint.json": _sha("8")
            }
            if private_acquisition
            else {}
        ),
    )
    admission = _artifact(admission_sha256=_sha("5"))
    reservation = _artifact(
        admission=admission,
        reservation_sha256=_sha("6"),
    )
    acquisition = _artifact(
        campaign_id="product-v0233-fresh-formal-nofault",
        semantic_generation=2,
        attempt_id=attempt_id,
        active_profile_sha256=_sha("4"),
        semantic_surface_sha256=_sha("1"),
        incident_id="inc-" + "1" * 24,
        incident_sha256=_sha("7"),
        acquisition_sha256=_sha("8"),
        baseline_sha256=_sha("9"),
        service_identity_sha256=_sha("a"),
        capability_sha256=_sha("b"),
    )
    clone = _artifact(clone_sha256=_sha("4"))
    authority = _artifact(proof_sha256=_sha("c"))
    restart = _artifact(proof_sha256=_sha("d"))
    execution = _artifact(execution_sha256=_sha("e"))
    traffic = _artifact(result_sha256=_sha("0"))
    fresh_snapshot = _artifact(runtime_snapshot_sha256=_sha("1"))
    live_capture = _artifact(
        live_capture_bundle_sha256=_sha("c"),
        queue_before_sha256=_sha("2"),
        queue_after_sha256=_sha("2"),
        outer_baseline_before_sha256=_sha("3"),
        outer_baseline_after_sha256=_sha("3"),
    )
    incident = _artifact(
        incident_id=acquisition.incident_id,
        incident_sha256=acquisition.incident_sha256,
    )
    incident_binding = _artifact(binding_sha256=_sha("4"))
    diagnosis = _artifact(
        result_sha256=_sha("5"),
        agent_writes=0,
        runbook_executions=0,
        provider_calls=0,
        action_authority=SimpleNamespace(value="NONE"),
    )
    evidence = _artifact(incident_id=incident.incident_id)
    index = _artifact(index_sha256=_sha("6"), decision_trace_sha256=_sha("7"))
    decision_trace = _artifact(trace_sha256=_sha("7"))
    assessment = _artifact(
        result_sha256=_sha("8"),
        terminal=SimpleNamespace(value="ECOMSRE_PRODUCT_V0232_NOFAULT_NOT_SUPPORTED"),
        reasons=("NOFAULT_EVIDENCE_INSUFFICIENT",),
    )
    pipeline = _artifact(
        journal_tail_sha256=_sha("9"),
        acceptance_sha256=_sha("a"),
    )
    job_context = FormalDiagnosisJobContextV0233.build(
        campaign_id=acquisition.campaign_id,
        semantic_generation=acquisition.semantic_generation,
        attempt_id=attempt_id,
        diagnosis_generation=1,
        active_profile_sha256=acquisition.active_profile_sha256,
        semantic_surface_sha256=acquisition.semantic_surface_sha256,
        acquisition_sha256=None,
    )
    job_payload = {
        "incident_id": incident.incident_id,
        "formal_recovery_v0233": job_context.model_dump(mode="json"),
    }
    initial_idempotency_key = (
        "formal-v0233-acquisition-"
        f"{live_capture.live_capture_bundle_sha256[:32]}"
    )
    rebound_idempotency_key = final_diagnosis_idempotency_key_v0233(
        context=job_context,
        incident_sha256=acquisition.incident_sha256,
        acquisition_sha256=acquisition.acquisition_sha256,
    )
    successful_job = ProductJobRecordV1(
        job_id="job-" + ("2" if recovery_required else "1") * 24,
        job_type=ProductJobTypeV1.DIAGNOSIS,
        status=ProductJobStatusV1.SUCCEEDED,
        payload=job_payload,
        result={"result_sha256": diagnosis.result_sha256},
        safe_error_code=None,
        idempotency_key=rebound_idempotency_key,
        claimed_by=None,
        lease_expires_at=None,
        attempt_count=1,
        created_at=1.0,
        updated_at=2.0,
    )
    original_job = (
        ProductJobRecordV1(
            job_id="job-" + "1" * 24,
            job_type=ProductJobTypeV1.DIAGNOSIS,
            status=ProductJobStatusV1.FAILED,
            payload=job_payload,
            result=None,
            safe_error_code="INTERNAL_CONTRACT_FAILURE",
            idempotency_key=rebound_idempotency_key,
            claimed_by=None,
            lease_expires_at=None,
            attempt_count=1,
            created_at=1.0,
            updated_at=2.0,
        )
        if recovery_required
        else successful_job
    )
    submitted_original_job = original_job.model_copy(
        update={
            "status": ProductJobStatusV1.PENDING,
            "result": None,
            "safe_error_code": None,
            "idempotency_key": initial_idempotency_key,
            "attempt_count": 0,
            "updated_at": 1.0,
        }
    )
    jobs = _Jobs(
        original_job,
        successful_job if recovery_required else None,
    )
    by_name = {
        "diagnosis-acquisition-checkpoint.json": acquisition,
        "admission.json": admission,
        "reservation.json": reservation,
        "formal-state-clone.json": clone,
        "runtime-authority.json": authority,
        "baseline-restart.json": restart,
        "traffic-execution.json": execution,
        "formal-traffic.json": traffic,
        "fresh-runtime-snapshot.json": fresh_snapshot,
        "live-capture-bundle.json": live_capture,
        "incident.json": incident,
        "incident-traffic-binding.json": incident_binding,
        "diagnosis-job.json": submitted_original_job,
    }
    base_counts = _counts(incident=10, jobs=10, diagnosis=10)
    current_counts = _counts(
        incident=11,
        jobs=12 if recovery_required else 11,
        diagnosis=11,
    )
    source_before = SimpleNamespace(
        selection_sha256=_sha("3"),
        source_counts=base_counts,
        active_environment_id="env-" + "1" * 24,
        active_baseline_id="base-" + "2" * 24,
        active_baseline_sha256=_sha("3"),
        active_profile_sha256=_sha("4"),
        source_database_file_sha256=_sha("5"),
    )
    published = {}

    monkeypatch.setattr(
        resume_command,
        "strict_resume_formal_admission_v0233",
        lambda *_args, **_kwargs: (latest, semantic, operational),
    )
    monkeypatch.setattr(
        resume_command,
        "_load_model",
        lambda path, _model: by_name[path.name],
    )
    monkeypatch.setattr(resume_command, "_ProductHostProcessesV023", _Processes)
    monkeypatch.setattr(resume_command, "JobRepositoryV1", lambda _store: jobs)
    monkeypatch.setattr(
        resume_command,
        "_failed_formal_job_ids_v0233",
        lambda **_kwargs: (
            (original_job.job_id,) if recovery_required else ()
        ),
    )
    monkeypatch.setattr(
        resume_command,
        "_append_checkpoint_v0233",
        lambda *, latest, state, outputs, **_kwargs: SimpleNamespace(
            **{**latest.__dict__, "state": state, "output_artifact_sha256s": dict(outputs)}
        ),
    )
    monkeypatch.setattr(
        resume_command,
        "DiagnosisResultV1",
        SimpleNamespace(model_validate=lambda _value: diagnosis),
    )
    monkeypatch.setattr(
        resume_command,
        "EvidenceBundleV1",
        SimpleNamespace(model_validate=lambda _value: evidence),
    )
    monkeypatch.setattr(
        resume_command,
        "DiagnosisEvidenceIndexV0232",
        SimpleNamespace(model_validate=lambda _value: index),
    )
    monkeypatch.setattr(resume_command, "_request_json", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(
        resume_command,
        "_wait_job",
        lambda *_args, **_kwargs: successful_job,
    )
    monkeypatch.setattr(resume_command, "_find_decision_trace", lambda *_args, **_kwargs: decision_trace)
    monkeypatch.setattr(resume_command, "score_nofault_evidence_v0232", lambda **_kwargs: assessment)
    monkeypatch.setattr(resume_command, "_diagnosis_acceptance", lambda **_kwargs: pipeline)
    monkeypatch.setattr(
        resume_command,
        "_selected_source",
        lambda _root: (tmp_path, tmp_path, source_before),
    )
    monkeypatch.setattr(
        resume_command,
        "read_fresh_formal_state_counts_v0233",
        lambda _root: current_counts,
    )
    monkeypatch.setattr(
        resume_command,
        "read_formal_diagnosis_action_totals_v0233",
        lambda _root: {
            "provider_calls": 0,
            "agent_writes": 0,
            "runbook_executions": 0,
        },
    )
    monkeypatch.setattr(
        resume_command,
        "_safety_observation",
        lambda *, action_journal, **_kwargs: FormalSafetyObservationV0233.build(
            observation_status="OBSERVED",
            action_journal=action_journal.model_dump(mode="json"),
            starting_counts=base_counts.model_dump(mode="json"),
            ending_counts=current_counts.model_dump(mode="json"),
            new_incident_count=1,
            new_diagnosis_count=2 if recovery_required else 1,
            provider_calls=0,
            agent_writes=0,
            runbook_executions=0,
            fault_attempts=0,
            knowledge_loop_executions=0,
            observed_action_authority="NONE",
            safe=True,
        ),
    )
    monkeypatch.setattr(
        resume_command,
        "read_formal_active_binding_v0233",
        lambda _root: {
            "environment_id": source_before.active_environment_id,
            "baseline_id": source_before.active_baseline_id,
            "baseline_sha256": source_before.active_baseline_sha256,
            "profile_sha256": source_before.active_profile_sha256,
        },
    )
    monkeypatch.setattr(resume_command, "_database_owner_count", lambda _path: 0)
    monkeypatch.setattr(
        resume_command,
        "load_fresh_formal_campaign_v0233",
        lambda _root: SimpleNamespace(campaign_sha256=_sha("b")),
    )
    monkeypatch.setattr(
        resume_command,
        "_build_measured_ledger_v0233",
        lambda **_kwargs: SimpleNamespace(),
    )
    monkeypatch.setattr(
        resume_command,
        "_publish_measured_terminal_v0233",
        lambda **kwargs: published.update(kwargs),
    )

    result = resume_command.resume_formal_nofault_v0233(
        project_root=tmp_path,
        attempt_id=attempt_id,
    )

    assert jobs.enqueue_calls == (1 if recovery_required else 0)
    assert result.diagnosis_result_sha256 == diagnosis.result_sha256
    assert published["result"] == result
    assert published["recovery_lineage"] is not None
    assert published["recovery_acquisition"] is acquisition
    assert published["diagnosis"] is diagnosis
    assert published["evidence"] is evidence
    assert published["index"] is index
    assert published["decision_trace"] is decision_trace
    if recovery_required:
        assert published["recovery_lineage"]["preserved_failed_job_ids"] == [
            original_job.job_id
        ]
    else:
        assert published["recovery_lineage"]["preserved_failed_job_ids"] == []
