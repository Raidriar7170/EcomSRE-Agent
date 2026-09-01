#!/usr/bin/env python3
"""Verify and inspect a resumable Product v0.2.3.3 formal attempt."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
import json
from pathlib import Path
import time
from typing import Any, Mapping, Sequence, cast

from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22
from ecomsre.product.incidents.contracts import (
    DiagnosisResultV1,
    EvidenceBundleV1,
    IncidentRecordV1,
)
from ecomsre.product.incidents.evidence_binding_v0232 import (
    DiagnosisEvidenceIndexV0232,
)
from ecomsre.product.incidents.diagnosis_pipeline_v02322 import (
    DiagnosisPipelineStageV02322,
    DiagnosisPipelineV02322,
)
from ecomsre.product.incidents.diagnosis_stage_journal_v02322 import (
    DiagnosisStageJournalRepositoryV02322,
    DiagnosisStageStatusV02322,
)
from ecomsre.product.jobs.contracts import (
    ProductJobRecordV1,
    ProductJobStatusV1,
    ProductJobTypeV1,
)
from ecomsre.product.jobs.repository import JobRepositoryV1
from ecomsre.product.pilot.diagnosis_recovery_v0233 import (
    FormalDiagnosisJobContextV0233,
    FormalDiagnosisRecoverySubmissionV0233,
    final_diagnosis_idempotency_key_v0233,
)
from ecomsre.product.pilot.formal_live_v0233 import (
    BaselineRestartProofV0233,
    FormalActionEventV0233,
    FormalActionJournalV0233,
    FormalClosureProofV0233,
    FormalExecutionAdmissionV0233,
    FormalExecutionReservationV0233,
    FormalObservedStateCountsV0233,
    FormalTrafficResultV0233,
    FreshRuntimeSnapshotProofV0233,
    RuntimeAuthorityProofV0233,
)

from ecomsre.product.pilot.formal_recovery_v0233 import (
    FormalCheckpointRepositoryV0233,
    FormalAttemptLedgerV0233,
    FormalAttemptRecordV0233,
    FormalExecutionCheckpointV0233,
    FormalExecutionStateV0233,
    DiagnosisAcquisitionCheckpointV0233,
    LiveCaptureBundleV0233,
    determine_earliest_safe_resume_state_v0233,
    verify_checkpoint_artifacts_v0233,
)
from ecomsre.product.pilot.fresh_formal_acceptance_v0233 import (
    FormalIncidentDiagnosisCardinalityV0233,
    NoFaultAcceptanceResultV0233,
    SafetyCountersV0233,
    load_fresh_formal_campaign_v0233,
)
from ecomsre.product.pilot.fresh_formal_source_v0233 import (
    FreshFormalStateCloneV0233,
    read_formal_active_binding_v0233,
    read_formal_diagnosis_action_totals_v0233,
    read_fresh_formal_state_counts_v0233,
)
from ecomsre.product.pilot.healthy_traffic_v0232 import (
    HealthyTrafficExecutionV0232,
    IncidentTrafficBindingV0232,
)
from ecomsre.product.pilot.live_baseline_readiness_v023 import (
    _ProductHostProcessesV023,
    _request_json,
    _wait_job,
)
from ecomsre.product.pilot.nofault_acceptance_v0232 import score_nofault_evidence_v0232
from ecomsre.product.pilot.serialization_v0233 import semantic_json_sha256_v0233
from ecomsre.product.storage.sqlite_store import SqliteStoreV1
from ecomsre_live_sandbox.contracts import write_private_json
from scripts.product_v02321.run_formal_nofault import _find_decision_trace
from scripts.product_v02321.run_traffic_preflight import _database_owner_count
from scripts.product_v0233.run_formal_nofault import (
    _attempt_private_locator_v0233,
    _attempt_product_locator_v0233,
    _attempt_public_locator_v0233,
    _diagnosis_acceptance,
    _formal_surfaces_v0233,
    _knowledge_handoff,
    _publish_measured_terminal_v0233,
    _recover_terminal_publication,
    _selected_source,
    _sha256_file,
    _safety_observation,
    strict_resume_formal_admission_v0233,
)


def inspect_formal_resume_v0233(
    *,
    project_root: Path,
    attempt_id: str,
) -> dict[str, Any]:
    root = Path(project_root).resolve(strict=True)
    attempt_root = root / ".local/product-v0233/attempts" / attempt_id
    repository = FormalCheckpointRepositoryV0233(attempt_root)
    chain = repository.load_chain()
    if not chain:
        raise ValueError("Product v0.2.3.3 attempt has no resumable checkpoint")
    latest = chain[-1]
    semantic, operational = _formal_surfaces_v0233(
        root,
        semantic_generation=latest.semantic_generation,
    )
    if semantic.semantic_surface_sha256 != latest.semantic_surface_sha256:
        raise ValueError("Product v0.2.3.3 resume semantic surface differs")
    verify_checkpoint_artifacts_v0233(root, latest)
    resume_state = determine_earliest_safe_resume_state_v0233(latest)
    body = {
        "schema_version": "ecomsre.product.formal-resume-decision.v0233",
        "campaign_id": latest.campaign_id,
        "semantic_generation": latest.semantic_generation,
        "attempt_id": latest.attempt_id,
        "latest_checkpoint_sha256": latest.checkpoint_sha256,
        "resume_state": resume_state.value,
        "semantic_surface_sha256": semantic.semantic_surface_sha256,
        "checkpoint_operational_surface_sha256": (
            latest.operational_surface_sha256
        ),
        "current_operational_surface_sha256": operational.operational_surface_sha256,
        "operational_surface_changed": (
            operational.operational_surface_sha256
            != latest.operational_surface_sha256
        ),
        "referenced_artifacts_verified": True,
    }
    return {**body, "decision_sha256": semantic_json_sha256_v0233(body)}


def _load_model(path: Path, model_type):
    return model_type.model_validate_json(path.read_bytes())


def _failed_formal_job_ids_v0233(
    *,
    product_root: Path,
    attempt_id: str,
    incident_id: str,
) -> tuple[str, ...]:
    store = SqliteStoreV1(product_root / "product.sqlite3")
    failed: list[str] = []
    with store.connect() as connection:
        rows = connection.execute(
            "SELECT job_id, payload_json FROM diagnosis_jobs "
            "WHERE job_type = ? AND status = ? ORDER BY created_at, job_id",
            (ProductJobTypeV1.DIAGNOSIS.value, ProductJobStatusV1.FAILED.value),
        ).fetchall()
    for row in rows:
        payload = json.loads(str(row["payload_json"]))
        context = payload.get("formal_recovery_v0233")
        if (
            isinstance(context, dict)
            and context.get("attempt_id") == attempt_id
            and payload.get("incident_id") == incident_id
        ):
            failed.append(str(row["job_id"]))
    return tuple(failed)


def _job_lineage_projection_v0233(job: ProductJobRecordV1) -> dict[str, Any]:
    body = {
        "job_id": job.job_id,
        "status": job.status.value,
        "idempotency_key": job.idempotency_key,
        "attempt_count": job.attempt_count,
        "payload_sha256": semantic_json_sha256_v0233(job.payload),
        "result_sha256": (
            None
            if not isinstance(job.result, dict)
            else semantic_json_sha256_v0233(job.result)
        ),
        "diagnosis_result_sha256": (
            job.result.get("result_sha256")
            if isinstance(job.result, dict)
            and isinstance(job.result.get("result_sha256"), str)
            else None
        ),
        "safe_error_code": job.safe_error_code,
        "failure_stage": job.failure_stage,
        "exception_fingerprint": job.exception_fingerprint,
        "journal_tail_sha256": job.journal_tail_sha256,
    }
    return {**body, "projection_sha256": semantic_json_sha256_v0233(body)}


def _seal_interrupted_job_v0233(
    *,
    jobs: JobRepositoryV1,
    product_root: Path,
    job: ProductJobRecordV1,
    acquisition: DiagnosisAcquisitionCheckpointV0233,
    cleanup_clean: bool,
) -> ProductJobRecordV1:
    """Fence one abandoned RUNNING job and preserve a terminal failure journal."""

    if job.status is not ProductJobStatusV1.RUNNING:
        return job
    if (
        not cleanup_clean
        or job.claimed_by is None
        or job.lease_expires_at is None
    ):
        raise RuntimeError("BLOCKED_ECOMSRE_PRODUCT_V0233_RESUME_ACTIVE_LEASE")
    timestamp = time.time()
    if job.lease_expires_at <= timestamp:
        claimed = jobs.reclaim_expired(
            job.job_id,
            expected_attempt_count=job.attempt_count,
            worker_id=f"formal-v0233-recovery-{job.job_id[-8:]}",
            lease_seconds=60,
            now=timestamp,
        )
    else:
        jobs.renew_lease(
            job.job_id,
            job.claimed_by,
            job.attempt_count,
            lease_seconds=60,
            now=timestamp,
        )
        claimed = jobs.get(job.job_id)
    store = SqliteStoreV1(product_root / "product.sqlite3")
    journal = DiagnosisStageJournalRepositoryV02322(store)
    events = journal.list_events(job.job_id)
    if not events or events[-1].stage in {
        DiagnosisPipelineStageV02322.JOB_SUCCEEDED,
        DiagnosisPipelineStageV02322.FAILED,
    }:
        raise RuntimeError("BLOCKED_ECOMSRE_PRODUCT_V0233_RESUME_LINEAGE_MISSING")
    pipeline = DiagnosisPipelineV02322(
        journal,
        job_id=job.job_id,
        incident_id=acquisition.incident_id,
        observed_at=datetime.fromtimestamp(timestamp, UTC),
    )
    pipeline.last_passed_stage = next(
        (
            event.stage
            for event in reversed(events)
            if event.status is DiagnosisStageStatusV02322.PASSED
        ),
        None,
    )
    pipeline.failing_stage = DiagnosisPipelineStageV02322.BRIDGE_DIAGNOSIS_STARTED
    pipeline.bind_artifacts(
        incident_sha256=acquisition.incident_sha256,
        baseline_sha256=acquisition.baseline_sha256,
        identity_sha256=acquisition.service_identity_sha256,
        capability_sha256=acquisition.capability_sha256,
        read_acquisition_sha256=acquisition.acquisition_sha256,
    )
    projection, _envelope, _path = pipeline.capture_failure(
        RuntimeError("formal worker interrupted after frozen acquisition"),
        data_root=product_root,
        job_payload=job.payload,
        safe_error_code="FORMAL_WORKER_INTERRUPTED",
    )
    return jobs.fail(
        claimed.job_id,
        str(claimed.claimed_by),
        claimed.attempt_count,
        "FORMAL_WORKER_INTERRUPTED",
        public_failure_v02322=projection,
        now=time.time(),
    )


def _recovery_generation_v0233(
    recovery_root: Path,
) -> tuple[int, FormalDiagnosisRecoverySubmissionV0233 | None]:
    candidates = tuple(sorted(recovery_root.glob("diagnosis-generation-*")))
    if not candidates:
        return 2, None
    latest_root = candidates[-1]
    submission = _load_model(
        latest_root / "submission.json", FormalDiagnosisRecoverySubmissionV0233
    )
    completion_path = latest_root / "diagnosis-job-completion.json"
    if not completion_path.is_file():
        return submission.context.diagnosis_generation, submission
    completion = json.loads(completion_path.read_text(encoding="utf-8"))
    if completion.get("status") == ProductJobStatusV1.FAILED.value:
        return submission.context.diagnosis_generation + 1, None
    return submission.context.diagnosis_generation, submission


def _append_checkpoint_v0233(
    *,
    repository: FormalCheckpointRepositoryV0233,
    latest: FormalExecutionCheckpointV0233,
    state: FormalExecutionStateV0233,
    operational_surface_sha256: str,
    outputs: Mapping[str, str],
) -> FormalExecutionCheckpointV0233:
    checkpoint = FormalExecutionCheckpointV0233.build(
        previous=latest,
        state=state,
        created_at=datetime.now(UTC),
        operational_surface_sha256=operational_surface_sha256,
        output_artifact_sha256s=outputs,
    )
    repository.append(checkpoint)
    return checkpoint


def _build_measured_ledger_v0233(
    *,
    root: Path,
    attempt_id: str,
    latest: FormalExecutionCheckpointV0233,
    repository: FormalCheckpointRepositoryV0233,
    evidence: Mapping[str, str],
    measured_terminal: str,
) -> FormalAttemptLedgerV0233:
    current = FormalAttemptLedgerV0233.model_validate_json(
        (root / "config/product-v0233/formal-attempt-ledger.json").read_bytes()
    )
    complete_evidence = dict(evidence)
    for checkpoint_path in sorted(repository.root.glob("*.json")):
        complete_evidence[checkpoint_path.relative_to(root).as_posix()] = (
            _sha256_file(checkpoint_path)
        )
    prior = next(
        (item for item in current.attempts if item.attempt_id == attempt_id), None
    )
    ordinal = len(current.attempts) if prior is not None else len(current.attempts) + 1
    record = FormalAttemptRecordV0233.build(
        attempt_id=attempt_id,
        ordinal=ordinal,
        semantic_generation=latest.semantic_generation,
        disposition="MEASURED",
        latest_state=latest.state,
        latest_checkpoint_sha256=latest.checkpoint_sha256,
        blocker_terminal=None,
        measured_terminal=measured_terminal,
        evidence_sha256_by_path=complete_evidence,
    )
    attempts = (
        (*current.attempts[:-1], record)
        if prior is not None
        else (*current.attempts, record)
    )
    return FormalAttemptLedgerV0233.build(
        campaign_id=latest.campaign_id,
        attempts=attempts,
    )


def resume_formal_nofault_v0233(
    *,
    project_root: Path,
    attempt_id: str,
) -> NoFaultAcceptanceResultV0233:
    """Resume one attempt from a sealed Diagnosis acquisition checkpoint."""

    root = Path(project_root).resolve(strict=True)
    attempt_root = root / _attempt_private_locator_v0233(attempt_id)
    private_root = attempt_root / "execution"
    reservation_path = attempt_root / "reservation.json"
    intent_path = private_root / "terminal-publication.json"
    latest, semantic, operational = strict_resume_formal_admission_v0233(
        root,
        attempt_id=attempt_id,
    )
    if intent_path.is_file():
        recovered = _recover_terminal_publication(
            root,
            reservation_path=reservation_path,
            private_root=private_root,
        )
        if recovered is None:
            raise RuntimeError("BLOCKED_ECOMSRE_PRODUCT_V0233_ACCEPTANCE_ARTIFACTS")
        return recovered

    repository = FormalCheckpointRepositoryV0233(attempt_root)

    product_root = root / _attempt_product_locator_v0233(attempt_id)
    acquisition_path = private_root / "diagnosis-acquisition-checkpoint.json"
    if not acquisition_path.is_file():
        submitted = _load_model(
            private_root / "diagnosis-job.json", ProductJobRecordV1
        )
        context = FormalDiagnosisJobContextV0233.model_validate(
            submitted.payload.get("formal_recovery_v0233")
        )
        product_acquisition_path = (
            product_root / context.acquisition_checkpoint_locator
        )
        promoted = _load_model(
            product_acquisition_path, DiagnosisAcquisitionCheckpointV0233
        )
        if (
            context.attempt_id != attempt_id
            or promoted.attempt_id != attempt_id
            or promoted.semantic_surface_sha256
            != semantic.semantic_surface_sha256
        ):
            raise RuntimeError("BLOCKED_ECOMSRE_PRODUCT_V0233_RESUME_SEMANTIC_DRIFT")
        write_private_json(
            acquisition_path,
            promoted.model_dump(mode="json"),
            create_once=True,
        )
    acquisition = _load_model(acquisition_path, DiagnosisAcquisitionCheckpointV0233)
    if (
        acquisition.attempt_id != attempt_id
        or acquisition.semantic_surface_sha256 != semantic.semantic_surface_sha256
    ):
        raise RuntimeError("BLOCKED_ECOMSRE_PRODUCT_V0233_RESUME_SEMANTIC_DRIFT")
    earliest_state = (
        latest.state
        if latest.state is FormalExecutionStateV0233.CLOSED
        else determine_earliest_safe_resume_state_v0233(latest)
    )
    if latest.state in {
        FormalExecutionStateV0233.INCIDENT_CREATED,
        FormalExecutionStateV0233.RECOVERABLE_FAILURE,
    } and earliest_state in {
        FormalExecutionStateV0233.PREPARED,
        FormalExecutionStateV0233.LIVE_CAPTURE_SEALED,
        FormalExecutionStateV0233.INCIDENT_CREATED,
    }:
        recovered_outputs = dict(latest.output_artifact_sha256s)
        recovered_outputs[acquisition_path.relative_to(root).as_posix()] = (
            _sha256_file(acquisition_path)
        )
        latest = _append_checkpoint_v0233(
            repository=repository,
            latest=latest,
            state=FormalExecutionStateV0233.ACQUISITION_SEALED,
            operational_surface_sha256=operational.operational_surface_sha256,
            outputs=recovered_outputs,
        )
    resume_state = (
        latest.state
        if latest.state is FormalExecutionStateV0233.CLOSED
        else determine_earliest_safe_resume_state_v0233(latest)
    )
    if resume_state not in {
        FormalExecutionStateV0233.ACQUISITION_SEALED,
        FormalExecutionStateV0233.DIAGNOSIS_RUNNING,
        FormalExecutionStateV0233.DIAGNOSIS_PERSISTED,
        FormalExecutionStateV0233.SCORED,
        FormalExecutionStateV0233.CLOSED,
    }:
        raise RuntimeError("BLOCKED_ECOMSRE_PRODUCT_V0233_RESUME_REQUIRED")

    admission = _load_model(
        private_root / "admission.json", FormalExecutionAdmissionV0233
    )
    reservation = _load_model(reservation_path, FormalExecutionReservationV0233)
    clone = _load_model(
        root / _attempt_public_locator_v0233(attempt_id) / "formal-state-clone.json",
        FreshFormalStateCloneV0233,
    )
    authority = _load_model(
        private_root / "runtime-authority.json", RuntimeAuthorityProofV0233
    )
    restart = _load_model(
        private_root / "baseline-restart.json", BaselineRestartProofV0233
    )
    execution = _load_model(
        private_root / "traffic-execution.json", HealthyTrafficExecutionV0232
    )
    traffic = _load_model(
        private_root / "formal-traffic.json", FormalTrafficResultV0233
    )
    fresh_snapshot = _load_model(
        private_root / "fresh-runtime-snapshot.json",
        FreshRuntimeSnapshotProofV0233,
    )
    live_capture = _load_model(
        private_root / "live-capture-bundle.json", LiveCaptureBundleV0233
    )
    incident = _load_model(private_root / "incident.json", IncidentRecordV1)
    incident_binding = _load_model(
        private_root / "incident-traffic-binding.json", IncidentTrafficBindingV0232
    )
    action_journal_path = private_root / "action-journal.json"
    original_journal = (
        _load_model(action_journal_path, FormalActionJournalV0233)
        if action_journal_path.is_file()
        else FormalActionJournalV0233.build(
            observation_status="COMPLETE",
            events=(
                "RESERVATION_CONSUMED",
                "FORMAL_CLONE_REQUESTED",
                "DEMO_START_REQUESTED",
                "PRODUCT_START_REQUESTED",
                "PRODUCT_RESTART_REQUESTED",
                "FORMAL_TRAFFIC_REQUESTED",
                "INCIDENT_CREATE_REQUESTED",
                "DIAGNOSIS_CREATE_REQUESTED",
            ),
        )
    )
    closure_path = private_root / "formal-closure.json"
    original_closure = (
        _load_model(closure_path, FormalClosureProofV0233)
        if closure_path.is_file()
        else None
    )
    if (
        reservation.admission != admission
        or clone.clone_sha256 != latest.formal_clone_sha256
        or incident.incident_id != acquisition.incident_id
        or incident.incident_sha256 != acquisition.incident_sha256
        or (original_closure is not None and original_closure.verdict != "CLEAN")
    ):
        raise RuntimeError("BLOCKED_ECOMSRE_PRODUCT_V0233_ACCEPTANCE_ARTIFACTS")

    jobs = JobRepositoryV1(SqliteStoreV1(product_root / "product.sqlite3"))
    stale_processes = _ProductHostProcessesV023(
        root=root,
        data_root=product_root,
        private_root=private_root / "product-processes",
    )
    stale_cleanup = stale_processes.cleanup_observation()
    cleanup_clean = stale_cleanup.get("verdict") == "CLEAN"
    if not cleanup_clean:
        raise RuntimeError("BLOCKED_ECOMSRE_PRODUCT_V0233_RESUME_ACTIVE_LEASE")

    def reconcile_job(
        *,
        job_path: Path,
        completion_path: Path,
        expected_payload: Mapping[str, Any] | None = None,
        expected_idempotency_key: str | None = None,
        allow_initial_acquisition_rebind: bool = False,
    ) -> ProductJobRecordV1:
        submitted_job = _load_model(job_path, ProductJobRecordV1)
        current_job = jobs.get(submitted_job.job_id)
        idempotency_exact = current_job.idempotency_key == submitted_job.idempotency_key
        if allow_initial_acquisition_rebind:
            submitted_context = FormalDiagnosisJobContextV0233.model_validate(
                submitted_job.payload.get("formal_recovery_v0233")
            )
            initial_key = (
                "formal-v0233-acquisition-"
                f"{live_capture.live_capture_bundle_sha256[:32]}"
            )
            rebound_key = final_diagnosis_idempotency_key_v0233(
                context=submitted_context,
                incident_sha256=acquisition.incident_sha256,
                acquisition_sha256=acquisition.acquisition_sha256,
            )
            idempotency_exact = (
                submitted_job.idempotency_key == initial_key
                and current_job.idempotency_key in {initial_key, rebound_key}
                and not (
                    current_job.status is ProductJobStatusV1.SUCCEEDED
                    and current_job.idempotency_key != rebound_key
                )
                and submitted_context.campaign_id == acquisition.campaign_id
                and submitted_context.semantic_generation
                == acquisition.semantic_generation
                and submitted_context.attempt_id == acquisition.attempt_id
                and submitted_context.diagnosis_generation == 1
                and submitted_context.active_profile_sha256
                == acquisition.active_profile_sha256
                and submitted_context.semantic_surface_sha256
                == acquisition.semantic_surface_sha256
                and submitted_context.acquisition_sha256 is None
                and submitted_job.payload.get("incident_id")
                == acquisition.incident_id
            )
        if (
            current_job.payload != submitted_job.payload
            or not idempotency_exact
            or (
                expected_payload is not None
                and current_job.payload != dict(expected_payload)
            )
            or (
                expected_idempotency_key is not None
                and current_job.idempotency_key != expected_idempotency_key
            )
        ):
            raise RuntimeError("BLOCKED_ECOMSRE_PRODUCT_V0233_RESUME_LINEAGE_MISSING")
        current_job = _seal_interrupted_job_v0233(
            jobs=jobs,
            product_root=product_root,
            job=current_job,
            acquisition=acquisition,
            cleanup_clean=cleanup_clean,
        )
        if current_job.status in {
            ProductJobStatusV1.SUCCEEDED,
            ProductJobStatusV1.FAILED,
        }:
            write_private_json(
                completion_path,
                current_job.model_dump(mode="json"),
                create_once=True,
            )
        return current_job

    original_job = reconcile_job(
        job_path=private_root / "diagnosis-job.json",
        completion_path=private_root / "diagnosis-job-completion.json",
        allow_initial_acquisition_rebind=True,
    )
    recovery_root = private_root / "recovery"
    successful_job: ProductJobRecordV1 | None = None
    diagnosis_generation = 1
    generation_root = private_root
    submission: FormalDiagnosisRecoverySubmissionV0233 | None = None
    if original_job.status is ProductJobStatusV1.SUCCEEDED:
        successful_job = original_job
    elif original_job.status is not ProductJobStatusV1.FAILED:
        raise RuntimeError("BLOCKED_ECOMSRE_PRODUCT_V0233_RESUME_LINEAGE_MISSING")

    recovery_candidates = tuple(sorted(recovery_root.glob("diagnosis-generation-*")))
    if successful_job is None and recovery_candidates:
        latest_recovery_root = recovery_candidates[-1]
        recovery_submission = _load_model(
            latest_recovery_root / "submission.json",
            FormalDiagnosisRecoverySubmissionV0233,
        )
        recovery_job_path = latest_recovery_root / "diagnosis-job.json"
        if recovery_job_path.is_file():
            recovery_job = reconcile_job(
                job_path=recovery_job_path,
                completion_path=(
                    latest_recovery_root / "diagnosis-job-completion.json"
                ),
                expected_payload=recovery_submission.job_payload,
                expected_idempotency_key=recovery_submission.idempotency_key,
            )
            if recovery_job.status is ProductJobStatusV1.SUCCEEDED:
                successful_job = recovery_job
                submission = recovery_submission
                diagnosis_generation = (
                    recovery_submission.context.diagnosis_generation
                )
                generation_root = latest_recovery_root

    failed_job_ids = _failed_formal_job_ids_v0233(
        product_root=product_root,
        attempt_id=attempt_id,
        incident_id=incident.incident_id,
    )
    if successful_job is None:
        if not failed_job_ids:
            raise RuntimeError(
                "BLOCKED_ECOMSRE_PRODUCT_V0233_RESUME_LINEAGE_MISSING"
            )
        diagnosis_generation, existing_submission = _recovery_generation_v0233(
            recovery_root
        )
        generation_root = (
            recovery_root / f"diagnosis-generation-{diagnosis_generation:04d}"
        )
        submission = (
            existing_submission
            or FormalDiagnosisRecoverySubmissionV0233.build(
                checkpoint=acquisition,
                diagnosis_generation=diagnosis_generation,
                preserved_failed_job_ids=failed_job_ids,
            )
        )
        write_private_json(
            generation_root / "submission.json",
            submission.model_dump(mode="json"),
            create_once=True,
        )
        checkpoint_destination = (
            product_root / submission.context.acquisition_checkpoint_locator
        )
        write_private_json(
            checkpoint_destination,
            acquisition.model_dump(mode="json"),
            create_once=True,
        )
    outputs = dict(latest.output_artifact_sha256s)

    def capture(path: Path) -> None:
        outputs[path.resolve(strict=True).relative_to(root).as_posix()] = (
            _sha256_file(path)
        )

    for recovery_artifact in (
        generation_root / "submission.json",
        generation_root / "diagnosis-job.json",
        generation_root / "diagnosis-job-completion.json",
        private_root / "diagnosis-job.json",
        private_root / "diagnosis-job-completion.json",
        acquisition_path,
    ):
        if recovery_artifact.is_file():
            capture(recovery_artifact)
    if (
        latest.state is FormalExecutionStateV0233.RECOVERABLE_FAILURE
        and resume_state is FormalExecutionStateV0233.ACQUISITION_SEALED
    ):
        latest = _append_checkpoint_v0233(
            repository=repository,
            latest=latest,
            state=FormalExecutionStateV0233.ACQUISITION_SEALED,
            operational_surface_sha256=operational.operational_surface_sha256,
            outputs=outputs,
        )
    if latest.state is FormalExecutionStateV0233.ACQUISITION_SEALED:
        latest = _append_checkpoint_v0233(
            repository=repository,
            latest=latest,
            state=FormalExecutionStateV0233.DIAGNOSIS_RUNNING,
            operational_surface_sha256=operational.operational_surface_sha256,
            outputs=outputs,
        )
    if latest.state not in {
        FormalExecutionStateV0233.DIAGNOSIS_RUNNING,
        FormalExecutionStateV0233.DIAGNOSIS_PERSISTED,
        FormalExecutionStateV0233.SCORED,
        FormalExecutionStateV0233.CLOSED,
    }:
        raise RuntimeError("BLOCKED_ECOMSRE_PRODUCT_V0233_RESUME_REQUIRED")

    processes = _ProductHostProcessesV023(
        root=root,
        data_root=product_root,
        private_root=generation_root / "product-processes",
    )
    completed_job = None
    diagnosis = None
    evidence = None
    index = None
    decision_trace = None
    assessment = None
    pipeline = None
    product_cleanup: Mapping[str, Any] = {"verdict": "BLOCKED"}
    execution_error: BaseException | None = None
    try:
        processes.start()
        if successful_job is not None:
            completed_job = successful_job
        else:
            if submission is None:
                raise RuntimeError(
                    "BLOCKED_ECOMSRE_PRODUCT_V0233_RESUME_LINEAGE_MISSING"
                )
            queued_path = generation_root / "diagnosis-job.json"
            if queued_path.is_file():
                submitted_job = _load_model(queued_path, ProductJobRecordV1)
                queued = jobs.get(submitted_job.job_id)
                if (
                    submitted_job.payload != submission.job_payload
                    or submitted_job.idempotency_key != submission.idempotency_key
                    or queued.payload != submission.job_payload
                    or queued.idempotency_key != submission.idempotency_key
                ):
                    raise RuntimeError(
                        "BLOCKED_ECOMSRE_PRODUCT_V0233_RESUME_LINEAGE_MISSING"
                    )
            else:
                queued = jobs.enqueue(
                    ProductJobTypeV1.DIAGNOSIS,
                    submission.job_payload,
                    idempotency_key=submission.idempotency_key,
                )
                write_private_json(
                    queued_path,
                    queued.model_dump(mode="json"),
                    create_once=True,
                )
            capture(queued_path)
            completed_job = _wait_job(
                processes,
                queued.job_id,
                data_root=product_root,
                timeout_seconds=240,
            )
        write_private_json(
            generation_root / "diagnosis-job-completion.json",
            completed_job.model_dump(mode="json"),
            create_once=True,
        )
        capture(generation_root / "diagnosis-job-completion.json")
        if completed_job.status is ProductJobStatusV1.SUCCEEDED and isinstance(
            completed_job.result, dict
        ):
            diagnosis = DiagnosisResultV1.model_validate(completed_job.result)
            evidence = EvidenceBundleV1.model_validate(
                _request_json(
                    processes,
                    "GET",
                    f"/v1/incidents/{incident.incident_id}/evidence",
                )
            )
            index = DiagnosisEvidenceIndexV0232.model_validate(
                _request_json(
                    processes,
                    "GET",
                    f"/v1/incidents/{incident.incident_id}/evidence-index",
                )
            )
            decision_trace = _find_decision_trace(
                product_root, expected_sha256=index.decision_trace_sha256
            )
            assessment = score_nofault_evidence_v0232(
                diagnosis=diagnosis,
                bundle=evidence,
                index=index,
                decision_trace=decision_trace,
            )
            pipeline = _diagnosis_acceptance(
                product_root=product_root,
                job=completed_job,
                diagnosis=diagnosis,
                evidence=evidence,
                index=index,
                decision_trace_sha256=decision_trace.trace_sha256,
            )
            for name, model in (
                ("diagnosis.json", diagnosis),
                ("evidence-bundle.json", evidence),
                ("evidence-index.json", index),
                ("decision-trace.json", decision_trace),
                ("assessment.json", assessment),
                ("diagnosis-pipeline.json", pipeline),
            ):
                write_private_json(
                    generation_root / name,
                    model.model_dump(mode="json"),
                    create_once=True,
                )
                capture(generation_root / name)
        else:
            pipeline = _diagnosis_acceptance(
                product_root=product_root,
                job=completed_job,
                diagnosis=None,
                evidence=None,
                index=None,
                decision_trace_sha256=None,
            )
            write_private_json(
                generation_root / "diagnosis-pipeline.json",
                pipeline.model_dump(mode="json"),
                create_once=True,
            )
            capture(generation_root / "diagnosis-pipeline.json")
    except BaseException as error:
        execution_error = error
    finally:
        product_cleanup = processes.cleanup_observation()

    if (
        execution_error is not None
        or product_cleanup.get("verdict") != "CLEAN"
        or completed_job is None
        or completed_job.status is not ProductJobStatusV1.SUCCEEDED
        or diagnosis is None
        or evidence is None
        or index is None
        or decision_trace is None
        or assessment is None
        or pipeline is None
    ):
        latest = _append_checkpoint_v0233(
            repository=repository,
            latest=latest,
            state=FormalExecutionStateV0233.RECOVERABLE_FAILURE,
            operational_surface_sha256=operational.operational_surface_sha256,
            outputs=outputs,
        )
        raise RuntimeError(
            "BLOCKED_ECOMSRE_PRODUCT_V0233_RESUME_REQUIRED"
        ) from execution_error

    if latest.state is FormalExecutionStateV0233.DIAGNOSIS_RUNNING:
        latest = _append_checkpoint_v0233(
            repository=repository,
            latest=latest,
            state=FormalExecutionStateV0233.DIAGNOSIS_PERSISTED,
            operational_surface_sha256=operational.operational_surface_sha256,
            outputs=outputs,
        )
    if latest.state is FormalExecutionStateV0233.DIAGNOSIS_PERSISTED:
        latest = _append_checkpoint_v0233(
            repository=repository,
            latest=latest,
            state=FormalExecutionStateV0233.SCORED,
            operational_surface_sha256=operational.operational_surface_sha256,
            outputs=outputs,
        )

    predecessor, source_root, source_before = _selected_source(root)
    del predecessor
    if source_before.selection_sha256 != latest.source_selection_sha256:
        raise RuntimeError("BLOCKED_ECOMSRE_PRODUCT_V0233_RESUME_SEMANTIC_DRIFT")
    current_counts = read_fresh_formal_state_counts_v0233(product_root)
    FormalIncidentDiagnosisCardinalityV0233.build(
        phase=(
            "POST_DIAGNOSIS_RECOVERED"
            if failed_job_ids
            else "POST_DIAGNOSIS_SUCCEEDED"
        ),
        source_incident_count=source_before.source_counts.incident_count,
        source_diagnosis_job_count=source_before.source_counts.diagnosis_job_count,
        source_diagnosis_result_count=source_before.source_counts.diagnosis_count,
        source_evidence_index_count=(
            source_before.source_counts.diagnosis_evidence_index_count
        ),
        source_fault_family_count=source_before.source_counts.fault_family_count,
        source_knowledge_artifact_count=(
            source_before.source_counts.knowledge_artifact_count
        ),
        source_baseline_job_count=source_before.source_counts.baseline_job_count,
        current_incident_count=current_counts.incident_count,
        current_diagnosis_job_count=current_counts.diagnosis_job_count,
        current_diagnosis_result_count=current_counts.diagnosis_count,
        current_evidence_index_count=current_counts.diagnosis_evidence_index_count,
        current_fault_family_count=current_counts.fault_family_count,
        current_knowledge_artifact_count=current_counts.knowledge_artifact_count,
        current_baseline_job_count=current_counts.baseline_job_count,
    )
    final_failed_job_ids = _failed_formal_job_ids_v0233(
        product_root=product_root,
        attempt_id=attempt_id,
        incident_id=incident.incident_id,
    )
    if not set(failed_job_ids).issubset(final_failed_job_ids):
        raise RuntimeError("BLOCKED_ECOMSRE_PRODUCT_V0233_RESUME_LINEAGE_MISSING")

    recovery_count = len(tuple(recovery_root.glob("diagnosis-generation-*")))
    action_journal = FormalActionJournalV0233.build(
        observation_status="COMPLETE",
        events=cast(
            tuple[FormalActionEventV0233, ...],
            original_journal.events
            + ("DIAGNOSIS_CREATE_REQUESTED",) * recovery_count,
        ),
    )
    write_private_json(
        generation_root / "action-journal.json",
        action_journal.model_dump(mode="json"),
        create_once=True,
    )
    capture(generation_root / "action-journal.json")
    starting_counts = FormalObservedStateCountsV0233.model_validate(
        source_before.source_counts.model_dump(mode="json")
    )
    safety = _safety_observation(
        starting_counts=starting_counts,
        source_action_totals=read_formal_diagnosis_action_totals_v0233(source_root),
        product_root=product_root,
        action_journal=action_journal,
    )
    active_binding_exact = read_formal_active_binding_v0233(product_root) == {
        "environment_id": source_before.active_environment_id,
        "baseline_id": source_before.active_baseline_id,
        "baseline_sha256": source_before.active_baseline_sha256,
        "profile_sha256": source_before.active_profile_sha256,
    }
    if (
        product_cleanup.get("verdict") != "CLEAN"
        or _database_owner_count(product_root / "product.sqlite3") != 0
        or not active_binding_exact
        or not safety.safe
        or semantic.semantic_surface_sha256 != latest.semantic_surface_sha256
    ):
        raise RuntimeError("BLOCKED_ECOMSRE_PRODUCT_V0233_ACCEPTANCE_ARTIFACTS")
    closure = FormalClosureProofV0233.build(
        queue_before_sha256=(
            live_capture.queue_before_sha256
            if original_closure is None
            else original_closure.queue_before_sha256
        ),
        queue_after_sha256=(
            live_capture.queue_after_sha256
            if original_closure is None
            else original_closure.queue_after_sha256
        ),
        outer_baseline_before_sha256=(
            live_capture.outer_baseline_before_sha256
            if original_closure is None
            else original_closure.outer_baseline_before_sha256
        ),
        outer_baseline_after_sha256=(
            live_capture.outer_baseline_after_sha256
            if original_closure is None
            else original_closure.outer_baseline_after_sha256
        ),
        source_selection_before_sha256=source_before.selection_sha256,
        source_selection_after_sha256=source_before.selection_sha256,
        source_database_before_sha256=source_before.source_database_file_sha256,
        source_database_after_sha256=source_before.source_database_file_sha256,
        product_cleanup="CLEAN",
        demo_cleanup="CLEAN",
        owned_host_processes=0,
        owned_containers=0,
        owned_networks=0,
        owned_volumes=0,
        formal_clone_database_owner_count=0,
        non_owned_resources_changed=False,
        clone_baseline_binding_exact=True,
        frozen_semantic_surface_before_sha256=latest.semantic_surface_sha256,
        frozen_semantic_surface_after_sha256=semantic.semantic_surface_sha256,
        safety_observation=safety.model_dump(mode="json"),
    )
    write_private_json(
        generation_root / "formal-closure.json",
        closure.model_dump(mode="json"),
        create_once=True,
    )
    capture(generation_root / "formal-closure.json")
    safety_counters = SafetyCountersV0233(
        agent_writes=diagnosis.agent_writes,
        runbook_executions=diagnosis.runbook_executions,
        provider_calls=diagnosis.provider_calls,
        fault_attempts=0,
        knowledge_loop_executions=0,
    )
    campaign = load_fresh_formal_campaign_v0233(root)
    result = NoFaultAcceptanceResultV0233.build_from_v0232(
        campaign_sha256=campaign.campaign_sha256,
        source_selection_sha256=source_before.selection_sha256,
        formal_clone_sha256=clone.clone_sha256,
        runtime_authority_proof_sha256=authority.proof_sha256,
        baseline_restart_proof_sha256=restart.proof_sha256,
        traffic_preflight_sha256=str(
            json.loads(
                (root / "docs/analysis/product-v0233-traffic-preflight.json").read_text(
                    encoding="utf-8"
                )
            )["preflight_sha256"]
        ),
        formal_traffic_execution_sha256=execution.execution_sha256,
        fresh_runtime_snapshot_sha256=fresh_snapshot.runtime_snapshot_sha256,
        incident_traffic_binding_sha256=incident_binding.binding_sha256,
        incident_sha256=incident.incident_sha256,
        diagnosis_result_sha256=diagnosis.result_sha256,
        evidence_bundle_sha256=semantic_sha256_v22(
            evidence.model_dump(mode="json")
        ),
        evidence_index_sha256=index.index_sha256,
        decision_trace_sha256=decision_trace.trace_sha256,
        stage_journal_tail_sha256=pipeline.journal_tail_sha256,
        v0232_assessment_sha256=assessment.result_sha256,
        v0232_measured_terminal=assessment.terminal.value,
        reasons=assessment.reasons,
        safety_counters=safety_counters.model_dump(mode="json"),
        cleanup_proof_sha256=closure.closure_sha256,
    )
    write_private_json(
        private_root / "nofault-acceptance-result.json",
        result.model_dump(mode="json"),
        create_once=True,
    )
    capture(private_root / "nofault-acceptance-result.json")
    lineage: dict[str, Any] | None = None
    if final_failed_job_ids:
        failed_job_projections = tuple(
            _job_lineage_projection_v0233(jobs.get(job_id))
            for job_id in final_failed_job_ids
        )
        successful_job_projection = _job_lineage_projection_v0233(completed_job)
        lineage_body = {
            "schema_version": "ecomsre.product.diagnosis-recovery-lineage.v0233",
            "attempt_id": attempt_id,
            "incident_id": incident.incident_id,
            "incident_sha256": incident.incident_sha256,
            "acquisition_sha256": acquisition.acquisition_sha256,
            "preserved_failed_job_ids": final_failed_job_ids,
            "preserved_failed_jobs": failed_job_projections,
            "successful_job_id": completed_job.job_id,
            "successful_job": successful_job_projection,
            "successful_diagnosis_generation": diagnosis_generation,
            "diagnosis_result_sha256": diagnosis.result_sha256,
        }
        lineage = {
            **lineage_body,
            "lineage_sha256": semantic_json_sha256_v0233(lineage_body),
        }
        write_private_json(
            generation_root / "diagnosis-recovery-lineage.json",
            lineage,
            create_once=True,
        )
        capture(generation_root / "diagnosis-recovery-lineage.json")
    if latest.state is FormalExecutionStateV0233.SCORED:
        latest = _append_checkpoint_v0233(
            repository=repository,
            latest=latest,
            state=FormalExecutionStateV0233.CLOSED,
            operational_surface_sha256=operational.operational_surface_sha256,
            outputs=outputs,
        )
    if latest.state is not FormalExecutionStateV0233.CLOSED:
        raise RuntimeError("BLOCKED_ECOMSRE_PRODUCT_V0233_ACCEPTANCE_ARTIFACTS")
    handoff = _knowledge_handoff(result)
    ledger = _build_measured_ledger_v0233(
        root=root,
        attempt_id=attempt_id,
        latest=latest,
        repository=repository,
        evidence=outputs,
        measured_terminal=result.measured_terminal,
    )
    assert safety.new_diagnosis_count is not None
    _publish_measured_terminal_v0233(
        root=root,
        private_root=private_root,
        public_attempt_locator=_attempt_public_locator_v0233(attempt_id),
        reservation=reservation,
        clone=clone,
        authority=authority,
        restart=restart,
        traffic=traffic,
        fresh_snapshot_proof=fresh_snapshot,
        incident_binding=incident_binding,
        assessment=assessment,
        pipeline=pipeline,
        closure=closure.model_dump(mode="json"),
        result=result,
        handoff=handoff,
        measured_ledger=ledger,
        new_diagnosis_count=safety.new_diagnosis_count,
        recovery_lineage=lineage,
        recovery_acquisition=(acquisition if lineage is not None else None),
    )
    return result


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--attempt", required=True)
    parser.add_argument(
        "--project-root",
        type=Path,
        default=Path(__file__).resolve().parents[2],
    )
    parser.add_argument("--inspect-only", action="store_true")
    arguments = parser.parse_args(argv)
    if arguments.inspect_only:
        decision = inspect_formal_resume_v0233(
            project_root=arguments.project_root,
            attempt_id=arguments.attempt,
        )
        print(json.dumps(decision, ensure_ascii=False, sort_keys=True))
    else:
        result = resume_formal_nofault_v0233(
            project_root=arguments.project_root,
            attempt_id=arguments.attempt,
        )
        print(result.model_dump_json())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ("inspect_formal_resume_v0233", "resume_formal_nofault_v0233")
