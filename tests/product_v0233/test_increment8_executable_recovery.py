from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22
from ecomsre.product.incidents.diagnosis_stage_journal_v02322 import (
    DiagnosisPipelineStageV02322,
    DiagnosisStageEventV02322,
    DiagnosisStageStatusV02322,
)
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
    FormalActionJournalV0233,
    FormalClosureProofV0233,
    FormalExecutionAdmissionV0233,
    FormalExecutionBlockerV0233,
    FormalExecutionReservationV0233,
    FormalObservedStateCountsV0233,
    FormalSafetyObservationV0233,
    InterruptedAttemptCleanupProofV0233,
)
from ecomsre.product.pilot.formal_recovery_v0233 import (
    FormalAttemptLedgerV0233,
    FormalAttemptRecordV0233,
    FormalCheckpointRepositoryV0233,
    FormalExecutionCheckpointV0233,
    FormalExecutionStateV0233,
)
from ecomsre.product.pilot.fresh_formal_source_v0233 import (
    FreshFormalStateCloneV0233,
    FreshFormalStateCountsV0233,
)
from scripts.product_v0233 import resume_formal_nofault as resume_command
from scripts.product_v0233 import run_formal_nofault as run_command
from scripts.ci import verify_product_v0233_terminal as terminal_verifier


def _sha(character: str) -> str:
    return character * 64


def _copy_legacy_recovery_state(
    repository_root: Path,
    destination_root: Path,
) -> None:
    source_manifest = (
        repository_root / "config/product-v0233/repository-state-manifest.json"
    )
    source_progress = repository_root / "docs/analysis/product-v0233-progress.json"
    for source, relative in (
        (source_manifest, "config/product-v0233/repository-state-manifest.json"),
        (
            source_manifest,
            "config/product-v0233/recovery-repository-state-manifest.json",
        ),
        (source_progress, "docs/analysis/product-v0233-progress.json"),
        (source_progress, "docs/analysis/product-v0233-recovery-progress.json"),
    ):
        destination = destination_root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(source.read_bytes())

    tracked_ledger = FormalAttemptLedgerV0233.model_validate_json(
        (
            repository_root / "config/product-v0233/formal-attempt-ledger.json"
        ).read_bytes()
    )
    legacy_ledger = FormalAttemptLedgerV0233.build(
        campaign_id=tracked_ledger.campaign_id,
        attempts=(tracked_ledger.attempts[0],),
    )
    ledger_path = destination_root / "config/product-v0233/formal-attempt-ledger.json"
    ledger_path.write_text(legacy_ledger.model_dump_json(), encoding="utf-8")


def _artifact(**values):
    payload = {key: getattr(value, "value", value) for key, value in values.items()}
    return SimpleNamespace(
        **values,
        model_dump=lambda mode="json": dict(payload),
    )


def _counts(
    *, incident: int, jobs: int, diagnosis: int
) -> FormalObservedStateCountsV0233:
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


def test_hard_interruption_recovers_parent_and_orphaned_worker_processes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    product_root = (
        tmp_path / ".local/product-v0233/attempts/attempt-2/formal-state/product"
    )
    private_root = tmp_path / ".local/product-v0233/attempts/attempt-2/execution"
    product_root.mkdir(parents=True)
    private_root.mkdir(parents=True)
    processes = run_command._ProductHostProcessesV023(
        root=tmp_path,
        data_root=product_root,
        private_root=private_root,
    )
    run_command._persist_product_process_authority_v0233(
        processes,
        private_root=private_root,
    )
    child_pid_path = tmp_path / "orphan-worker.pid"
    child_script = "import time; time.sleep(60)"
    parent_script = (
        "import pathlib,subprocess,sys,time;"
        "child=subprocess.Popen([sys.executable,'-c',sys.argv[2],"
        "'-m','ecomsre.product.jobs.worker'],stdin=subprocess.DEVNULL,"
        "stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL);"
        "pathlib.Path(sys.argv[1]).write_text(str(child.pid));"
        "time.sleep(60)"
    )
    environment = {
        **os.environ,
        "ECOMSRE_ADMIN_TOKEN": processes.token,
        "ECOMSRE_PRODUCT_DATA_ROOT": str(product_root),
    }
    parent = subprocess.Popen(
        (
            sys.executable,
            "-c",
            parent_script,
            str(child_pid_path),
            child_script,
            "-m",
            "ecomsre.product.app",
        ),
        cwd=tmp_path,
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    deadline = time.monotonic() + 5
    while not child_pid_path.is_file() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert child_pid_path.is_file()
    child_pid = int(child_pid_path.read_text(encoding="utf-8"))
    monkeypatch.setattr(run_command, "_ProductHostProcessesV023", _Processes)

    try:
        cleanup = run_command._recover_owned_product_processes_v0233(
            root=tmp_path,
            product_root=product_root,
            private_root=private_root,
        )
        parent.wait(timeout=5)
    finally:
        for pid in (parent.pid, child_pid):
            try:
                os.kill(pid, 9)
            except ProcessLookupError:
                pass

    assert cleanup["verdict"] == "CLEAN"
    assert cleanup["recovered_owned_process_count"] == 2
    assert cleanup["remaining_owned_process_count"] == 0


def test_interrupted_cleanup_rejects_wrong_attempt_and_uncommitted_checkpoint(
    tmp_path: Path,
) -> None:
    attempt_root = tmp_path / ".local/product-v0233/attempts/attempt-2"
    repository = FormalCheckpointRepositoryV0233(attempt_root)
    prepared = _prepared_checkpoint()
    repository.append(prepared)
    clone_sealed = FormalExecutionCheckpointV0233.build(
        previous=prepared,
        state=FormalExecutionStateV0233.CLONE_SEALED,
        formal_clone_sha256=_sha("4"),
        created_at=prepared.created_at + timedelta(seconds=1),
    )
    repository.append(clone_sealed)
    closure_path = attempt_root / "execution/interrupted-cleanup.json"
    closure_path.parent.mkdir(parents=True)
    wrong_body = {
        "schema_version": "ecomsre.product.interrupted-attempt-cleanup.v0233",
        "verdict": "CLEAN",
        "resource_cleanup_verdict": "CLEAN",
        "attempt_id": "attempt-9",
        "latest_checkpoint_sha256": clone_sealed.checkpoint_sha256,
    }
    closure_path.write_text(
        json.dumps(
            {**wrong_body, "closure_sha256": semantic_sha256_v22(wrong_body)}
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="cleanup proof"):
        run_command.recover_interrupted_attempt_cleanup_v0233(
            tmp_path,
            attempt_id="attempt-2",
            latest=clone_sealed,
        )

    closure_path.unlink()
    uncommitted = FormalExecutionCheckpointV0233.build(
        previous=clone_sealed,
        state=FormalExecutionStateV0233.NONRECOVERABLE_FAILURE,
        created_at=clone_sealed.created_at + timedelta(seconds=1),
    )
    with pytest.raises(ValueError, match="committed checkpoint"):
        run_command.recover_interrupted_attempt_cleanup_v0233(
            tmp_path,
            attempt_id="attempt-2",
            latest=uncommitted,
            persist=True,
        )


def test_public_verifier_strongly_types_interrupted_cleanup_and_binds_safety() -> None:
    repository_root = Path(run_command.__file__).resolve().parents[2]
    normal_closure = FormalClosureProofV0233.model_validate_json(
        (
            repository_root / "docs/analysis/product-v0233-formal-closure.json"
        ).read_bytes()
    )
    body = {
        "schema_version": "ecomsre.product.interrupted-attempt-cleanup.v0233",
        "verdict": "CLEAN",
        "resource_cleanup_verdict": "CLEAN",
        "attempt_id": "attempt-2",
        "latest_checkpoint_sha256": _sha("2"),
        "source_selection_before_sha256": _sha("3"),
        "source_selection_after_sha256": _sha("3"),
        "queue_sha256": _sha("4"),
        "product_cleanup": {
            "schema_version": "ecomsre.product.host-process-cleanup.v023",
            "verdict": "CLEAN",
            "owned_host_processes": 0,
            "product_api_port_available": True,
            "non_owned_resources_changed": False,
            "safe_error": None,
            "remaining_owned_process_count": 0,
        },
        "demo_cleanup": {
            "verdict": "CLEAN",
            "baseline_restored": True,
            "owned_containers": 0,
            "owned_networks": 0,
            "owned_volumes": 0,
            "non_owned_resources_changed": False,
        },
        "formal_clone_database_owner_count": 0,
        "clone_baseline_binding_exact": True,
        "safety_observation": normal_closure.safety_observation.model_dump(mode="json"),
        "safe_error_code": None,
    }
    proof = InterruptedAttemptCleanupProofV0233.model_validate(
        {**body, "closure_sha256": semantic_sha256_v22(body)}
    )
    blocker = SimpleNamespace(safety_observation=normal_closure.safety_observation)

    assert terminal_verifier._verify_cleanup_proof_v0233(
        proof.model_dump(mode="json"),
        attempt_id="attempt-2",
        latest_checkpoint_sha256=_sha("2"),
        blocker=blocker,
    ) == proof.closure_sha256

    invented_body = {**body, "schema_version": "invented-cleanup-proof"}
    with pytest.raises(ValueError, match="closure schema"):
        terminal_verifier._verify_cleanup_proof_v0233(
            {
                **invented_body,
                "closure_sha256": semantic_sha256_v22(invented_body),
            },
            attempt_id="attempt-2",
            latest_checkpoint_sha256=_sha("2"),
            blocker=blocker,
        )

    unsafe_body = {
        **body,
        "product_cleanup": {**body["product_cleanup"], "verdict": "BLOCKED"},
    }
    with pytest.raises(ValueError, match="interrupted cleanup proof"):
        terminal_verifier._verify_cleanup_proof_v0233(
            {**unsafe_body, "closure_sha256": semantic_sha256_v22(unsafe_body)},
            attempt_id="attempt-2",
            latest_checkpoint_sha256=_sha("2"),
            blocker=blocker,
        )

    with pytest.raises(ValueError, match="closure safety"):
        terminal_verifier._verify_cleanup_proof_v0233(
            proof.model_dump(mode="json"),
            attempt_id="attempt-2",
            latest_checkpoint_sha256=_sha("2"),
            blocker=SimpleNamespace(safety_observation=object()),
        )


def test_formal_closure_observation_requires_verified_clean_safety() -> None:
    repository_root = Path(run_command.__file__).resolve().parents[2]
    public_root = repository_root / "docs/analysis/product-v0233-attempts/attempt-2"
    closure = json.loads((public_root / "formal-closure.json").read_bytes())
    blocker = FormalExecutionBlockerV0233.model_validate_json(
        (public_root / "formal-blocker.json").read_bytes()
    )

    assert run_command._verified_blocker_cleanup_sha256_v0233(
        closure,
        attempt_id="attempt-2",
        latest_checkpoint_sha256=_sha("2"),
        blocker=blocker,
    ) == closure["closure_sha256"]
    assert terminal_verifier._verify_cleanup_proof_v0233(
        closure,
        attempt_id="attempt-2",
        latest_checkpoint_sha256=_sha("2"),
        blocker=blocker,
    ) == closure["closure_sha256"]

    def blocked_product(payload: dict[str, object]) -> None:
        payload["product_cleanup"]["verdict"] = "BLOCKED"  # type: ignore[index]

    def invented_product_schema(payload: dict[str, object]) -> None:
        payload["product_cleanup"]["schema_version"] = (  # type: ignore[index]
            "invented-cleanup"
        )

    def product_safe_error(payload: dict[str, object]) -> None:
        payload["product_cleanup"]["safe_error"] = "invented"  # type: ignore[index]

    def product_boolean_count(payload: dict[str, object]) -> None:
        payload["product_cleanup"]["owned_host_processes"] = False  # type: ignore[index]

    def product_float_port(payload: dict[str, object]) -> None:
        payload["product_cleanup"]["product_api_port"] = 18081.0  # type: ignore[index]

    def clone_boolean_count(payload: dict[str, object]) -> None:
        payload["formal_clone_database_owner_count"] = False

    def unrestored_demo(payload: dict[str, object]) -> None:
        payload["demo_cleanup"] = {
            "baseline_restored": False,
            "owned_containers": 0,
            "owned_networks": 0,
            "owned_volumes": 0,
            "non_owned_resources_changed": False,
            "verdict": "CLEAN",
        }

    def demo_float_count(payload: dict[str, object]) -> None:
        unrestored_demo(payload)
        payload["demo_cleanup"]["baseline_restored"] = True  # type: ignore[index]
        payload["demo_cleanup"]["owned_containers"] = 0.0  # type: ignore[index]

    for mutate in (
        blocked_product,
        invented_product_schema,
        product_safe_error,
        product_boolean_count,
        product_float_port,
        clone_boolean_count,
        unrestored_demo,
        demo_float_count,
    ):
        unsafe = json.loads(json.dumps(closure))
        mutate(unsafe)
        unsafe_body = {
            key: value for key, value in unsafe.items() if key != "closure_sha256"
        }
        unsafe["closure_sha256"] = semantic_sha256_v22(unsafe_body)
        with pytest.raises(ValueError, match="formal closure observation"):
            run_command._verified_blocker_cleanup_sha256_v0233(
                unsafe,
                attempt_id="attempt-2",
                latest_checkpoint_sha256=_sha("2"),
                blocker=blocker,
            )
        with pytest.raises(ValueError, match="formal closure observation"):
            terminal_verifier._verify_cleanup_proof_v0233(
                unsafe,
                attempt_id="attempt-2",
                latest_checkpoint_sha256=_sha("2"),
                blocker=blocker,
            )


def test_process_interruption_at_every_stage_is_classified_from_durable_acquisition() -> (
    None
):
    for stage in FormalExecutionStateV0233:
        if stage in {
            FormalExecutionStateV0233.CLOSED,
            FormalExecutionStateV0233.NONRECOVERABLE_FAILURE,
        }:
            continue
        assert (
            run_command._failure_checkpoint_state_v0233(acquisition_sealed=False)
            is FormalExecutionStateV0233.NONRECOVERABLE_FAILURE
        ), stage
        assert (
            run_command._failure_checkpoint_state_v0233(acquisition_sealed=True)
            is FormalExecutionStateV0233.RECOVERABLE_FAILURE
        ), stage


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
        tmp_path / ".local/product-v0233/attempts/attempt-2/execution/"
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


def test_successor_identity_requires_exact_next_attempt_and_generation() -> None:
    terminal = "BLOCKED_ECOMSRE_PRODUCT_V0233_ACCEPTANCE_ARTIFACTS"
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
    initial = FormalAttemptLedgerV0233.build(
        campaign_id="product-v0233-fresh-formal-nofault",
        attempts=(legacy,),
    )
    assert run_command._is_exact_successor_identity_v0233(
        initial,
        attempt_id="attempt-2",
        semantic_generation=2,
        expected_semantic_generation=2,
    )
    assert not run_command._is_exact_successor_identity_v0233(
        initial,
        attempt_id="attempt-3",
        semantic_generation=2,
        expected_semantic_generation=2,
    )
    assert not run_command._is_exact_successor_identity_v0233(
        initial,
        attempt_id="attempt-2",
        semantic_generation=3,
        expected_semantic_generation=2,
    )

    retired = FormalAttemptRecordV0233.build(
        attempt_id="attempt-2",
        ordinal=2,
        semantic_generation=2,
        disposition="NONRECOVERABLE_FAILURE",
        latest_state=FormalExecutionStateV0233.NONRECOVERABLE_FAILURE,
        latest_checkpoint_sha256=_sha("2"),
        blocker_terminal=terminal,
        measured_terminal=None,
        evidence_sha256_by_path={},
    )
    continued = FormalAttemptLedgerV0233.build(
        campaign_id="product-v0233-fresh-formal-nofault",
        attempts=(legacy, retired),
    )
    assert run_command._is_exact_successor_identity_v0233(
        continued,
        attempt_id="attempt-3",
        semantic_generation=2,
        expected_semantic_generation=2,
    )
    assert run_command._is_exact_successor_identity_v0233(
        continued,
        attempt_id="attempt-3",
        semantic_generation=3,
        expected_semantic_generation=3,
    )
    assert not run_command._is_exact_successor_identity_v0233(
        continued,
        attempt_id="attempt-4",
        semantic_generation=2,
        expected_semantic_generation=2,
    )
    assert not run_command._is_exact_successor_identity_v0233(
        continued,
        attempt_id="attempt-3",
        semantic_generation=4,
        expected_semantic_generation=3,
    )


def test_successor_admission_recognizes_only_exact_sealed_prior_publication(
    tmp_path: Path,
) -> None:
    repository_root = Path(run_command.__file__).resolve().parents[2]
    _copy_legacy_recovery_state(repository_root, tmp_path)
    attempt_root = tmp_path / ".local/product-v0233/attempts/attempt-2"
    repository = FormalCheckpointRepositoryV0233(attempt_root)
    prepared = _prepared_checkpoint()
    failed = FormalExecutionCheckpointV0233.build(
        previous=prepared,
        state=FormalExecutionStateV0233.NONRECOVERABLE_FAILURE,
        created_at=prepared.created_at + timedelta(seconds=1),
    )
    repository.append(prepared)
    repository.append(failed)
    admission = FormalExecutionAdmissionV0233.build(
        execution_head="1" * 40,
        campaign_sha256=_sha("1"),
        source_selection_sha256=_sha("2"),
        formal_clone_plan_sha256=_sha("3"),
        formal_contract_freeze_sha256=_sha("4"),
        pre_execution_review_sha256=_sha("5"),
        repository_state_manifest_sha256=_sha("6"),
    )
    reservation = FormalExecutionReservationV0233.build(
        admission=admission,
        reserved_at=datetime(2026, 9, 1, tzinfo=UTC),
    )
    reservation_path = attempt_root / "reservation.json"
    reservation_path.parent.mkdir(parents=True, exist_ok=True)
    reservation_path.write_text(reservation.model_dump_json(), encoding="utf-8")
    run_command.terminalize_nonrecoverable_attempt_v0233(
        tmp_path,
        attempt_id="attempt-2",
        latest=failed,
    )
    ledger = FormalAttemptLedgerV0233.model_validate_json(
        (tmp_path / "config/product-v0233/formal-attempt-ledger.json").read_bytes()
    )

    allowed = run_command._sealed_nonrecoverable_publication_paths_v0233(
        tmp_path,
        ledger,
    )
    assert allowed

    blocker_path = (
        tmp_path / "docs/analysis/product-v0233-attempts/attempt-2/formal-blocker.json"
    )
    blocker_path.write_text('{"drift":true}\n', encoding="utf-8")
    with pytest.raises(RuntimeError, match="ACCEPTANCE_ARTIFACTS"):
        run_command._sealed_nonrecoverable_publication_paths_v0233(
            tmp_path,
            ledger,
        )


def _prepared_checkpoint() -> FormalExecutionCheckpointV0233:
    return FormalExecutionCheckpointV0233.build(
        previous=None,
        state=FormalExecutionStateV0233.PREPARED,
        created_at=datetime(2026, 9, 1, tzinfo=UTC),
        campaign_id="product-v0233-fresh-formal-nofault",
        semantic_generation=2,
        attempt_id="attempt-2",
        semantic_surface_sha256=_sha("1"),
        operational_surface_sha256=_sha("2"),
        source_selection_sha256=_sha("3"),
        input_artifact_sha256s={},
        output_artifact_sha256s={},
    )


def _used_clone_recovery_fixture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[FreshFormalStateCloneV0233, FormalClosureProofV0233, SimpleNamespace]:
    repository_root = Path(run_command.__file__).resolve().parents[2]
    clone_body = FreshFormalStateCloneV0233.model_validate_json(
        (
            repository_root / "docs/analysis/product-v0233-formal-state-clone.json"
        ).read_bytes()
    ).model_dump(mode="json", exclude={"clone_sha256"})
    clone_body.update(
        {
            "destination_locator": (
                ".local/product-v0233/attempts/attempt-2/formal-state/product"
            ),
            "source_selection_sha256": _sha("3"),
        }
    )
    clone = FreshFormalStateCloneV0233.model_validate(
        {**clone_body, "clone_sha256": semantic_sha256_v22(clone_body)}
    )
    starting = FormalObservedStateCountsV0233.model_validate(
        clone.starting_counts.model_dump(mode="json")
    )
    ending_payload = starting.model_dump(mode="json")
    ending_payload.update(
        {
            "diagnosis_job_count": starting.diagnosis_job_count + 1,
            "incident_count": starting.incident_count + 1,
            "diagnosis_count": starting.diagnosis_count + 1,
            "evidence_object_count": starting.evidence_object_count + 7,
            "diagnosis_evidence_index_count": (
                starting.diagnosis_evidence_index_count + 1
            ),
            "diagnosis_stage_event_count": starting.diagnosis_stage_event_count + 54,
        }
    )
    ending = FormalObservedStateCountsV0233.model_validate(ending_payload)
    action_journal = FormalActionJournalV0233.build(
        observation_status="COMPLETE",
        events=("INCIDENT_CREATE_REQUESTED", "DIAGNOSIS_CREATE_REQUESTED"),
    )
    safety = FormalSafetyObservationV0233.build(
        observation_status="OBSERVED",
        action_journal=action_journal.model_dump(mode="json"),
        starting_counts=starting.model_dump(mode="json"),
        ending_counts=ending.model_dump(mode="json"),
        new_incident_count=1,
        new_diagnosis_count=1,
        provider_calls=0,
        agent_writes=0,
        runbook_executions=0,
        fault_attempts=0,
        knowledge_loop_executions=0,
        observed_action_authority="NONE",
        safe=True,
    )
    closure = FormalClosureProofV0233.build(
        queue_before_sha256=_sha("4"),
        queue_after_sha256=_sha("4"),
        outer_baseline_before_sha256=_sha("5"),
        outer_baseline_after_sha256=_sha("5"),
        source_selection_before_sha256=_sha("3"),
        source_selection_after_sha256=_sha("3"),
        source_database_before_sha256=_sha("6"),
        source_database_after_sha256=_sha("6"),
        product_cleanup="CLEAN",
        demo_cleanup="CLEAN",
        owned_host_processes=0,
        owned_containers=0,
        owned_networks=0,
        owned_volumes=0,
        formal_clone_database_owner_count=0,
        non_owned_resources_changed=False,
        clone_baseline_binding_exact=True,
        frozen_semantic_surface_before_sha256=_sha("1"),
        frozen_semantic_surface_after_sha256=_sha("1"),
        safety_observation=safety.model_dump(mode="json"),
    )
    attempt_root = tmp_path / ".local/product-v0233/attempts/attempt-2"
    product_root = attempt_root / "formal-state/product"
    product_root.mkdir(parents=True)
    public_path = (
        tmp_path
        / "docs/analysis/product-v0233-attempts/attempt-2/formal-state-clone.json"
    )
    public_path.parent.mkdir(parents=True)
    public_path.write_bytes(run_command.canonical_json_bytes(clone))
    closure_path = attempt_root / "execution/formal-closure.json"
    closure_path.parent.mkdir(parents=True)
    closure_path.write_bytes(run_command.canonical_json_bytes(closure))

    repository = FormalCheckpointRepositoryV0233(attempt_root)
    prepared = _prepared_checkpoint()
    clone_relative = public_path.relative_to(tmp_path).as_posix()
    closure_relative = closure_path.relative_to(tmp_path).as_posix()
    clone_sealed = FormalExecutionCheckpointV0233.build(
        previous=prepared,
        state=FormalExecutionStateV0233.CLONE_SEALED,
        formal_clone_sha256=clone.clone_sha256,
        output_artifact_sha256s={
            clone_relative: run_command._sha256_file(public_path),
        },
        created_at=prepared.created_at + timedelta(seconds=1),
    )
    ready = FormalExecutionCheckpointV0233.build(
        previous=clone_sealed,
        state=FormalExecutionStateV0233.FORMAL_ENVIRONMENT_READY,
        created_at=clone_sealed.created_at + timedelta(seconds=1),
    )
    recoverable = FormalExecutionCheckpointV0233.build(
        previous=ready,
        state=FormalExecutionStateV0233.RECOVERABLE_FAILURE,
        output_artifact_sha256s={
            **ready.output_artifact_sha256s,
            closure_relative: run_command._sha256_file(closure_path),
        },
        created_at=ready.created_at + timedelta(seconds=1),
    )
    for checkpoint in (prepared, clone_sealed, ready, recoverable):
        repository.append(checkpoint)

    inspection = SimpleNamespace(
        schema_version=9,
        counts=FreshFormalStateCountsV0233.model_validate(
            ending.model_dump(mode="json")
        ),
        environment_id=clone.active_environment_id,
        active_baseline_id=clone.active_baseline_id,
        active_baseline_sha256=clone.active_baseline_sha256,
        active_profile_sha256=clone.active_profile_sha256,
    )
    monkeypatch.setattr(run_command, "_inspect_source", lambda *_args, **_kwargs: inspection)
    return clone, closure, inspection


def test_used_clone_recovery_accepts_checkpointed_clean_post_diagnosis_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clone, _closure, _inspection = _used_clone_recovery_fixture(
        tmp_path, monkeypatch
    )

    recovered = run_command._recover_existing_attempt_clone_v0233(
        tmp_path,
        attempt_id="attempt-2",
        publish_missing=False,
    )

    assert recovered == clone


def test_resume_loads_only_the_checkpoint_bound_formal_closure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clone, closure, _inspection = _used_clone_recovery_fixture(tmp_path, monkeypatch)
    latest = FormalCheckpointRepositoryV0233(
        tmp_path / ".local/product-v0233/attempts/attempt-2"
    ).load_chain()[-1]
    private_root = tmp_path / ".local/product-v0233/attempts/attempt-2/execution"

    assert (
        resume_command._load_bound_formal_closure_v0233(
            root=tmp_path,
            private_root=private_root,
            latest=latest,
        )
        == closure
    )

    alternate_body = closure.model_dump(mode="json", exclude={"closure_sha256"})
    alternate_body.update(
        {
            "queue_before_sha256": _sha("7"),
            "queue_after_sha256": _sha("7"),
        }
    )
    alternate = FormalClosureProofV0233.model_validate(
        {
            **alternate_body,
            "closure_sha256": semantic_sha256_v22(alternate_body),
        }
    )
    (private_root / "formal-closure.json").write_bytes(
        run_command.canonical_json_bytes(alternate)
    )

    with pytest.raises(RuntimeError, match="ACCEPTANCE_ARTIFACTS"):
        resume_command._load_bound_formal_closure_v0233(
            root=tmp_path,
            private_root=private_root,
            latest=latest,
        )


@pytest.mark.parametrize(
    "tamper",
    ("current-counts", "closure-binding", "public-binding"),
)
def test_used_clone_recovery_rejects_unbound_or_drifted_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    tamper: str,
) -> None:
    _clone, closure, inspection = _used_clone_recovery_fixture(tmp_path, monkeypatch)
    attempt_root = tmp_path / ".local/product-v0233/attempts/attempt-2"
    if tamper == "current-counts":
        drifted = inspection.counts.model_dump(mode="json")
        drifted["diagnosis_stage_event_count"] += 1
        monkeypatch.setattr(
            run_command,
            "_inspect_source",
            lambda *_args, **_kwargs: SimpleNamespace(
                **{
                    **inspection.__dict__,
                    "counts": FreshFormalStateCountsV0233.model_validate(drifted),
                }
            ),
        )
    elif tamper == "closure-binding":
        closure_path = attempt_root / "execution/formal-closure.json"
        closure_path.write_bytes(
            run_command.canonical_json_bytes(
                closure.model_copy(update={"queue_after_sha256": _sha("7")})
            )
        )
    else:
        public_path = (
            tmp_path
            / "docs/analysis/product-v0233-attempts/attempt-2/formal-state-clone.json"
        )
        public_path.write_bytes(public_path.read_bytes() + b"\n")

    with pytest.raises(ValueError, match="interrupted clone"):
        run_command._recover_existing_attempt_clone_v0233(
            tmp_path,
            attempt_id="attempt-2",
            publish_missing=False,
        )


def test_resume_cleanup_accepts_external_only_drift_after_clean_bound_closure() -> None:
    repository_root = Path(run_command.__file__).resolve().parents[2]
    closure = FormalClosureProofV0233.model_validate_json(
        (
            repository_root / "docs/analysis/product-v0233-formal-closure.json"
        ).read_bytes()
    )
    cleanup = {
        "verdict": "BLOCKED",
        "resource_cleanup_verdict": "BLOCKED",
        "source_selection_before_sha256": (
            closure.source_selection_before_sha256
        ),
        "source_selection_after_sha256": closure.source_selection_after_sha256,
        "queue_sha256": closure.queue_after_sha256,
        "product_cleanup": {
            "schema_version": "ecomsre.product.host-process-cleanup.v023",
            "verdict": "CLEAN",
            "owned_host_processes": 0,
            "product_api_port": 18081,
            "product_api_port_available": True,
            "non_owned_resources_changed": False,
            "safe_error": None,
            "remaining_owned_process_count": 0,
        },
        "demo_cleanup": {
            "verdict": "BLOCKED",
            "baseline_restored": True,
            "owned_containers": 0,
            "owned_networks": 0,
            "owned_volumes": 0,
            "non_owned_resources_changed": True,
        },
        "formal_clone_database_owner_count": 0,
        "clone_baseline_binding_exact": True,
        "safety_observation": closure.safety_observation.model_dump(mode="json"),
        "safe_error_code": None,
    }

    assert resume_command._resume_cleanup_admitted_v0233(
        cleanup,
        original_closure=closure,
    )


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("formal_clone_database_owner_count", 1),
        ("formal_clone_database_owner_count", False),
        ("formal_clone_database_owner_count", 0.0),
        ("clone_baseline_binding_exact", False),
        ("safe_error_code", "RuntimeError"),
        ("source_selection_after_sha256", _sha("9")),
        ("product_cleanup.schema_version", None),
        ("product_cleanup.owned_host_processes", 1),
        ("product_cleanup.owned_host_processes", False),
        ("product_cleanup.owned_host_processes", 0.0),
        ("product_cleanup.product_api_port", 18080),
        ("product_cleanup.product_api_port", 18081.0),
        ("product_cleanup.product_api_port_available", False),
        ("product_cleanup.safe_error", "PORT_BUSY"),
        ("demo_cleanup.owned_containers", False),
        ("demo_cleanup.owned_networks", 0.0),
    ),
)
def test_resume_cleanup_rejects_unsafe_state_despite_prior_clean_closure(
    field: str,
    value: object,
) -> None:
    repository_root = Path(run_command.__file__).resolve().parents[2]
    closure = FormalClosureProofV0233.model_validate_json(
        (
            repository_root / "docs/analysis/product-v0233-formal-closure.json"
        ).read_bytes()
    )
    cleanup = {
        "verdict": "BLOCKED",
        "resource_cleanup_verdict": "BLOCKED",
        "source_selection_before_sha256": (
            closure.source_selection_before_sha256
        ),
        "source_selection_after_sha256": closure.source_selection_after_sha256,
        "queue_sha256": closure.queue_after_sha256,
        "product_cleanup": {
            "schema_version": "ecomsre.product.host-process-cleanup.v023",
            "verdict": "CLEAN",
            "owned_host_processes": 0,
            "product_api_port": 18081,
            "product_api_port_available": True,
            "non_owned_resources_changed": False,
            "safe_error": None,
            "remaining_owned_process_count": 0,
        },
        "demo_cleanup": {
            "verdict": "BLOCKED",
            "baseline_restored": True,
            "owned_containers": 0,
            "owned_networks": 0,
            "owned_volumes": 0,
            "non_owned_resources_changed": True,
        },
        "formal_clone_database_owner_count": 0,
        "clone_baseline_binding_exact": True,
        "safety_observation": closure.safety_observation.model_dump(mode="json"),
        "safe_error_code": None,
    }
    if "." in field:
        parent, child = field.split(".", maxsplit=1)
        nested = cleanup[parent]
        assert isinstance(nested, dict)
        nested[child] = value
    else:
        cleanup[field] = value

    assert not resume_command._resume_cleanup_admitted_v0233(
        cleanup,
        original_closure=closure,
    )


def test_resume_terminalizes_nonrecoverable_crash_window_before_routing_forward(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepared = _prepared_checkpoint()
    latest = FormalExecutionCheckpointV0233.build(
        previous=prepared,
        state=FormalExecutionStateV0233.NONRECOVERABLE_FAILURE,
        created_at=prepared.created_at + timedelta(seconds=1),
    )
    semantic = SimpleNamespace(semantic_surface_sha256=_sha("1"))
    operational = SimpleNamespace(operational_surface_sha256=_sha("2"))
    calls: list[tuple[str, object]] = []
    expected = SimpleNamespace(result_sha256=_sha("4"))
    monkeypatch.setattr(
        resume_command,
        "strict_resume_formal_admission_v0233",
        lambda *_args, **_kwargs: (latest, semantic, operational),
    )
    monkeypatch.setattr(
        resume_command,
        "terminalize_nonrecoverable_attempt_v0233",
        lambda *_args, **kwargs: (
            calls.append(("terminalize", kwargs["latest"])),
            "BLOCKED_ECOMSRE_PRODUCT_V0233_ACCEPTANCE_ARTIFACTS",
        )[1],
    )
    monkeypatch.setattr(
        resume_command,
        "_start_successor_after_nonrecoverable_v0233",
        lambda **kwargs: (calls.append(("successor", kwargs)), expected)[1],
    )

    observed = resume_command.resume_formal_nofault_v0233(
        project_root=tmp_path,
        attempt_id="attempt-2",
    )

    assert observed is expected
    assert calls[0] == ("terminalize", latest)
    assert calls[1][0] == "successor"


def test_failed_diagnosis_before_acquisition_uses_failure_evidence_path() -> None:
    assert (
        run_command._diagnosis_completion_acquisition_state_v0233(
            job_status=ProductJobStatusV1.FAILED,
            acquisition_checkpoint=None,
        )
        == "FAILED_BEFORE_ACQUISITION"
    )
    with pytest.raises(RuntimeError, match="DIAGNOSIS_ACQUISITION_CHECKPOINT_MISSING"):
        run_command._diagnosis_completion_acquisition_state_v0233(
            job_status=ProductJobStatusV1.SUCCEEDED,
            acquisition_checkpoint=None,
        )


def test_successful_diagnosis_acceptance_uses_terminal_journal_tail(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job_id = "job-" + "1" * 24
    incident_id = "inc-" + "2" * 24
    previous = _sha("0")
    events = []
    for ordinal, (stage, status) in enumerate(
        (
            (stage, status)
            for stage in DiagnosisPipelineStageV02322
            if stage is not DiagnosisPipelineStageV02322.FAILED
            for status in (
                DiagnosisStageStatusV02322.STARTED,
                DiagnosisStageStatusV02322.PASSED,
            )
        ),
        start=1,
    ):
        event = DiagnosisStageEventV02322.build(
            journal_id="journal-" + "3" * 24,
            job_id=job_id,
            incident_id=incident_id,
            ordinal=ordinal,
            stage=stage,
            status=status,
            input_binding_sha256=_sha("4"),
            output_artifact_sha256=_sha("5"),
            source_code_sha256=_sha("6"),
            observed_at=datetime(2026, 9, 1, tzinfo=UTC),
            previous_event_sha256=previous,
        )
        events.append(event)
        previous = event.event_sha256
    terminal = events[-1]
    monkeypatch.setattr(
        run_command.DiagnosisStageJournalRepositoryV02322,
        "list_events",
        lambda _self, _job_id: tuple(events),
    )
    job = SimpleNamespace(
        job_id=job_id,
        job_type=ProductJobTypeV1.DIAGNOSIS,
        status=ProductJobStatusV1.SUCCEEDED,
        payload={"incident_id": incident_id},
        journal_tail_sha256=None,
    )
    pipeline = run_command._diagnosis_acceptance(
        product_root=tmp_path,
        job=job,
        diagnosis=_artifact(result_sha256=_sha("1"), incident_id=incident_id),
        evidence=_artifact(incident_id=incident_id),
        index=_artifact(index_sha256=_sha("3")),
        decision_trace_sha256=_sha("4"),
    )

    assert pipeline.job_status == "SUCCEEDED"
    assert pipeline.journal_tail_sha256 == terminal.event_sha256
    with pytest.raises(RuntimeError, match="DIAGNOSIS_PIPELINE"):
        run_command._diagnosis_acceptance(
            product_root=tmp_path,
            job=SimpleNamespace(
                **{
                    **job.__dict__,
                    "journal_tail_sha256": terminal.event_sha256,
                }
            ),
            diagnosis=_artifact(result_sha256=_sha("1"), incident_id=incident_id),
            evidence=_artifact(incident_id=incident_id),
            index=_artifact(index_sha256=_sha("3")),
            decision_trace_sha256=_sha("4"),
        )
    monkeypatch.setattr(
        run_command.DiagnosisStageJournalRepositoryV02322,
        "list_events",
        lambda _self, _job_id: (terminal,),
    )
    with pytest.raises(RuntimeError, match="DIAGNOSIS_PIPELINE"):
        run_command._diagnosis_acceptance(
            product_root=tmp_path,
            job=job,
            diagnosis=_artifact(result_sha256=_sha("1"), incident_id=incident_id),
            evidence=_artifact(incident_id=incident_id),
            index=_artifact(index_sha256=_sha("3")),
            decision_trace_sha256=_sha("4"),
        )


def test_resume_from_pre_acquisition_hard_interruption_seals_and_routes_successor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository_root = Path(run_command.__file__).resolve().parents[2]
    _copy_legacy_recovery_state(repository_root, tmp_path)
    attempt_root = tmp_path / ".local/product-v0233/attempts/attempt-2"
    repository = FormalCheckpointRepositoryV0233(attempt_root)
    prepared = _prepared_checkpoint()
    repository.append(prepared)
    admission = FormalExecutionAdmissionV0233.build(
        execution_head="1" * 40,
        campaign_sha256=_sha("1"),
        source_selection_sha256=_sha("2"),
        formal_clone_plan_sha256=_sha("3"),
        formal_contract_freeze_sha256=_sha("4"),
        pre_execution_review_sha256=_sha("5"),
        repository_state_manifest_sha256=_sha("6"),
    )
    reservation = FormalExecutionReservationV0233.build(
        admission=admission,
        reserved_at=datetime(2026, 9, 1, tzinfo=UTC),
    )
    (attempt_root / "reservation.json").write_text(
        reservation.model_dump_json(), encoding="utf-8"
    )
    expected = SimpleNamespace(result_sha256=_sha("7"))
    successor_calls: list[dict[str, object]] = []
    monkeypatch.setattr(
        resume_command,
        "strict_resume_formal_admission_v0233",
        lambda *_args, **_kwargs: (
            prepared,
            SimpleNamespace(semantic_surface_sha256=prepared.semantic_surface_sha256),
            SimpleNamespace(
                operational_surface_sha256=prepared.operational_surface_sha256
            ),
        ),
    )
    monkeypatch.setattr(
        resume_command,
        "run_formal_nofault_v0233",
        lambda **kwargs: (successor_calls.append(kwargs), expected)[1],
    )

    observed = resume_command.resume_formal_nofault_v0233(
        project_root=tmp_path,
        attempt_id="attempt-2",
    )

    assert observed is expected
    assert successor_calls == [
        {
            "project_root": tmp_path,
            "attempt_id": "attempt-3",
            "semantic_generation": 2,
        }
    ]
    chain = repository.load_chain()
    assert chain[-1].state is FormalExecutionStateV0233.NONRECOVERABLE_FAILURE
    ledger = FormalAttemptLedgerV0233.model_validate_json(
        (tmp_path / "config/product-v0233/formal-attempt-ledger.json").read_bytes()
    )
    assert run_command._sealed_nonrecoverable_publication_paths_v0233(tmp_path, ledger)


def test_nonrecoverable_crash_window_publication_is_executable_and_sealed(
    tmp_path: Path,
) -> None:
    repository_root = Path(run_command.__file__).resolve().parents[2]
    _copy_legacy_recovery_state(repository_root, tmp_path)
    attempt_root = tmp_path / ".local/product-v0233/attempts/attempt-2"
    checkpoint_repository = FormalCheckpointRepositoryV0233(attempt_root)
    prepared = _prepared_checkpoint()
    failed = FormalExecutionCheckpointV0233.build(
        previous=prepared,
        state=FormalExecutionStateV0233.NONRECOVERABLE_FAILURE,
        created_at=prepared.created_at + timedelta(seconds=1),
    )
    checkpoint_repository.append(prepared)
    checkpoint_repository.append(failed)
    admission = FormalExecutionAdmissionV0233.build(
        execution_head="1" * 40,
        campaign_sha256=_sha("1"),
        source_selection_sha256=_sha("2"),
        formal_clone_plan_sha256=_sha("3"),
        formal_contract_freeze_sha256=_sha("4"),
        pre_execution_review_sha256=_sha("5"),
        repository_state_manifest_sha256=_sha("6"),
    )
    reservation = FormalExecutionReservationV0233.build(
        admission=admission,
        reserved_at=datetime(2026, 9, 1, tzinfo=UTC),
    )
    reservation_path = attempt_root / "reservation.json"
    reservation_path.parent.mkdir(parents=True, exist_ok=True)
    reservation_path.write_text(reservation.model_dump_json(), encoding="utf-8")

    terminal = run_command.terminalize_nonrecoverable_attempt_v0233(
        tmp_path,
        attempt_id="attempt-2",
        latest=failed,
    )

    public_root = tmp_path / "docs/analysis/product-v0233-attempts/attempt-2"
    observed_ledger = FormalAttemptLedgerV0233.model_validate_json(
        (tmp_path / "config/product-v0233/formal-attempt-ledger.json").read_bytes()
    )
    assert terminal == "BLOCKED_ECOMSRE_PRODUCT_V0233_ACCEPTANCE_ARTIFACTS"
    assert (public_root / "checkpoint-chain.json").is_file()
    assert (public_root / "formal-blocker.json").is_file()
    assert observed_ledger.attempts[-1].disposition == "NONRECOVERABLE_FAILURE"
    assert set(observed_ledger.attempts[-1].evidence_sha256_by_path) == {
        f"docs/analysis/product-v0233-attempts/attempt-2/{name}"
        for name in (
            "checkpoint-chain.json",
            "formal-blocker.json",
            "repository-state-manifest.json",
            "progress.json",
        )
    }


def test_reviewed_semantic_change_retires_generation_and_starts_fresh_capture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempt_root = tmp_path / ".local/product-v0233/attempts/attempt-2"
    repository = FormalCheckpointRepositoryV0233(attempt_root)
    prepared = _prepared_checkpoint()
    recoverable = FormalExecutionCheckpointV0233.build(
        previous=prepared,
        state=FormalExecutionStateV0233.RECOVERABLE_FAILURE,
        created_at=prepared.created_at + timedelta(seconds=1),
    )
    repository.append(prepared)
    repository.append(recoverable)
    next_semantic = SimpleNamespace(
        semantic_generation=3,
        semantic_surface_sha256=_sha("4"),
    )
    next_operational = SimpleNamespace(operational_surface_sha256=_sha("5"))
    transition = run_command.SemanticGenerationTransitionRequiredV0233(
        latest=recoverable,
        semantic=next_semantic,
        operational=next_operational,
    )
    monkeypatch.setattr(
        resume_command,
        "strict_resume_formal_admission_v0233",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(transition),
    )
    monkeypatch.setattr(
        resume_command,
        "_recover_existing_attempt_clone_v0233",
        lambda *_args, **_kwargs: None,
    )
    terminalized: list[FormalExecutionCheckpointV0233] = []
    monkeypatch.setattr(
        resume_command,
        "terminalize_nonrecoverable_attempt_v0233",
        lambda *_args, **kwargs: (
            terminalized.append(kwargs["latest"]),
            "BLOCKED_ECOMSRE_PRODUCT_V0233_ACCEPTANCE_ARTIFACTS",
        )[1],
    )
    successor_calls: list[dict[str, object]] = []
    expected = SimpleNamespace(result_sha256=_sha("6"))
    monkeypatch.setattr(
        resume_command,
        "_start_successor_after_nonrecoverable_v0233",
        lambda **kwargs: (successor_calls.append(kwargs), expected)[1],
    )

    observed = resume_command.resume_formal_nofault_v0233(
        project_root=tmp_path,
        attempt_id="attempt-2",
    )

    assert observed is expected
    assert terminalized[0].state is FormalExecutionStateV0233.NONRECOVERABLE_FAILURE
    assert terminalized[0].semantic_generation == 2
    assert successor_calls[0]["successor_semantic_generation"] == 3
    assert repository.load_chain()[-1] == terminalized[0]


def test_clone_bearing_semantic_rollover_closes_before_fresh_generation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository_root = Path(run_command.__file__).resolve().parents[2]
    _copy_legacy_recovery_state(repository_root, tmp_path)
    clone_payload = json.loads(
        (
            repository_root / "docs/analysis/product-v0233-formal-state-clone.json"
        ).read_text(encoding="utf-8")
    )
    attempt_root = tmp_path / ".local/product-v0233/attempts/attempt-2"
    repository = FormalCheckpointRepositoryV0233(attempt_root)
    prepared = _prepared_checkpoint()
    clone_sealed = FormalExecutionCheckpointV0233.build(
        previous=prepared,
        state=FormalExecutionStateV0233.CLONE_SEALED,
        formal_clone_sha256=clone_payload["clone_sha256"],
        created_at=prepared.created_at + timedelta(seconds=1),
    )
    environment_ready = FormalExecutionCheckpointV0233.build(
        previous=clone_sealed,
        state=FormalExecutionStateV0233.FORMAL_ENVIRONMENT_READY,
        created_at=clone_sealed.created_at + timedelta(seconds=1),
    )
    recoverable = FormalExecutionCheckpointV0233.build(
        previous=environment_ready,
        state=FormalExecutionStateV0233.RECOVERABLE_FAILURE,
        created_at=environment_ready.created_at + timedelta(seconds=1),
    )
    for checkpoint in (prepared, clone_sealed, environment_ready, recoverable):
        repository.append(checkpoint)
    admission = FormalExecutionAdmissionV0233.build(
        execution_head="1" * 40,
        campaign_sha256=_sha("1"),
        source_selection_sha256=_sha("2"),
        formal_clone_plan_sha256=_sha("3"),
        formal_contract_freeze_sha256=_sha("4"),
        pre_execution_review_sha256=_sha("5"),
        repository_state_manifest_sha256=_sha("6"),
    )
    reservation = FormalExecutionReservationV0233.build(
        admission=admission,
        reserved_at=datetime(2026, 9, 1, tzinfo=UTC),
    )
    (attempt_root / "reservation.json").write_text(
        reservation.model_dump_json(), encoding="utf-8"
    )
    execution_root = attempt_root / "execution"
    execution_root.mkdir(parents=True, exist_ok=True)
    (execution_root / "formal-closure.json").write_bytes(
        (
            repository_root / "docs/analysis/product-v0233-formal-closure.json"
        ).read_bytes()
    )
    public_root = tmp_path / "docs/analysis/product-v0233-attempts/attempt-2"
    public_root.mkdir(parents=True, exist_ok=True)
    (public_root / "formal-state-clone.json").write_bytes(
        run_command.canonical_json_bytes(clone_payload)
    )
    transition = run_command.SemanticGenerationTransitionRequiredV0233(
        latest=recoverable,
        semantic=SimpleNamespace(
            semantic_generation=3,
            semantic_surface_sha256=_sha("7"),
        ),
        operational=SimpleNamespace(operational_surface_sha256=_sha("8")),
    )
    monkeypatch.setattr(
        resume_command,
        "strict_resume_formal_admission_v0233",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(transition),
    )
    monkeypatch.setattr(
        resume_command,
        "_recover_existing_attempt_clone_v0233",
        lambda *_args, **_kwargs: FreshFormalStateCloneV0233.model_validate(
            clone_payload
        ),
    )
    monkeypatch.setattr(
        resume_command,
        "recover_interrupted_attempt_cleanup_v0233",
        lambda *_args, **_kwargs: {"verdict": "CLEAN"},
    )
    expected = SimpleNamespace(result_sha256=_sha("9"))
    successor_calls: list[dict[str, object]] = []
    monkeypatch.setattr(
        resume_command,
        "run_formal_nofault_v0233",
        lambda **kwargs: (successor_calls.append(kwargs), expected)[1],
    )

    observed = resume_command.resume_formal_nofault_v0233(
        project_root=tmp_path,
        attempt_id="attempt-2",
    )

    assert observed is expected
    assert successor_calls[0]["semantic_generation"] == 3
    assert repository.load_chain()[-1].state is (
        FormalExecutionStateV0233.NONRECOVERABLE_FAILURE
    )
    ledger = FormalAttemptLedgerV0233.model_validate_json(
        (tmp_path / "config/product-v0233/formal-attempt-ledger.json").read_bytes()
    )
    assert run_command._sealed_nonrecoverable_publication_paths_v0233(tmp_path, ledger)


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
    product_root = tmp_path / run_command._attempt_product_locator_v0233(attempt_id)
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
        checkpoint_sha256=_sha("d"),
        input_artifact_sha256s={},
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
    if not private_acquisition:
        worker_checkpoint = product_root / job_context.acquisition_checkpoint_locator
        worker_checkpoint.parent.mkdir(parents=True, exist_ok=True)
        worker_checkpoint.write_text("{}\n", encoding="utf-8")
    initial_idempotency_key = (
        f"formal-v0233-acquisition-{live_capture.live_capture_bundle_sha256[:32]}"
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
        "recover_interrupted_attempt_cleanup_v0233",
        lambda *_args, **_kwargs: {"resource_cleanup_verdict": "CLEAN"},
    )
    monkeypatch.setattr(
        resume_command,
        "_persist_product_process_authority_v0233",
        lambda *_args, **_kwargs: {},
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
        lambda **_kwargs: (original_job.job_id,) if recovery_required else (),
    )
    monkeypatch.setattr(
        resume_command,
        "_append_checkpoint_v0233",
        lambda *, latest, state, outputs, **_kwargs: SimpleNamespace(
            **{
                **latest.__dict__,
                "state": state,
                "output_artifact_sha256s": dict(outputs),
            }
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
    monkeypatch.setattr(
        resume_command, "_find_decision_trace", lambda *_args, **_kwargs: decision_trace
    )
    monkeypatch.setattr(
        resume_command, "score_nofault_evidence_v0232", lambda **_kwargs: assessment
    )
    monkeypatch.setattr(
        resume_command, "_diagnosis_acceptance", lambda **_kwargs: pipeline
    )
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
        "_diagnosis_lineage_v0233",
        lambda **_kwargs: {
            "preserved_failed_job_ids": (
                [original_job.job_id] if recovery_required else []
            )
        },
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
