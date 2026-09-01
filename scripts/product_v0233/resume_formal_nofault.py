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
    DiagnosisStageEventV02322,
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
    _diagnosis_lineage_v0233,
    _diagnosis_acceptance,
    _formal_surfaces_v0233,
    _knowledge_handoff,
    _persist_product_process_authority_v0233,
    _publish_measured_terminal_v0233,
    _recover_owned_product_processes_v0233,
    _recover_existing_attempt_clone_v0233,
    _recover_terminal_publication,
    recover_interrupted_attempt_cleanup_v0233,
    run_formal_nofault_v0233,
    _selected_source,
    _sha256_file,
    _safety_observation,
    _interrupted_diagnosis_lineage_v0233,
    SemanticGenerationTransitionRequiredV0233,
    strict_resume_formal_admission_v0233,
    terminalize_nonrecoverable_attempt_v0233,
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
        "checkpoint_operational_surface_sha256": (latest.operational_surface_sha256),
        "current_operational_surface_sha256": operational.operational_surface_sha256,
        "operational_surface_changed": (
            operational.operational_surface_sha256 != latest.operational_surface_sha256
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
    if not cleanup_clean or job.claimed_by is None or job.lease_expires_at is None:
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
    executable_stages = tuple(
        stage
        for stage in DiagnosisPipelineStageV02322
        if stage is not DiagnosisPipelineStageV02322.FAILED
    )
    if pipeline.last_passed_stage is None:
        pipeline.failing_stage = DiagnosisPipelineStageV02322.JOB_CLAIMED
    else:
        last_index = executable_stages.index(pipeline.last_passed_stage)
        if last_index + 1 >= len(executable_stages):
            raise RuntimeError("BLOCKED_ECOMSRE_PRODUCT_V0233_RESUME_LINEAGE_MISSING")
        pipeline.failing_stage = executable_stages[last_index + 1]
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


def _semantic_rollover_fence_payload_v0233(
    *,
    job: ProductJobRecordV1,
    acquisition: DiagnosisAcquisitionCheckpointV0233,
    successor_semantic_surface_sha256: str,
    last_passed_stage: str | None,
    failing_stage: str,
    journal_tail_sha256: str,
    observed_at: datetime,
) -> dict[str, Any]:
    intent_body = {
        "schema_version": "ecomsre.product.semantic-rollover-job-fence.v0233",
        "attempt_id": acquisition.attempt_id,
        "job_id": job.job_id,
        "incident_id": acquisition.incident_id,
        "acquisition_sha256": acquisition.acquisition_sha256,
        "prior_semantic_surface_sha256": acquisition.semantic_surface_sha256,
        "successor_semantic_surface_sha256": successor_semantic_surface_sha256,
        "diagnosis_generation": FormalDiagnosisJobContextV0233.model_validate(
            job.payload.get("formal_recovery_v0233")
        ).diagnosis_generation,
        "idempotency_key": job.idempotency_key,
        "attempt_count": job.attempt_count,
        "last_passed_stage": last_passed_stage,
        "failing_stage": failing_stage,
        "safe_error_code": "FORMAL_SEMANTIC_GENERATION_CHANGED",
    }
    intent_sha256 = semantic_sha256_v22(intent_body)
    body = {
        **intent_body,
        "fence_intent_sha256": intent_sha256,
        "exception_fingerprint": semantic_sha256_v22(
            {"semantic_rollover_fence": intent_sha256}
        ),
        "journal_tail_sha256": journal_tail_sha256,
        "observed_at": observed_at.isoformat(),
    }
    return {**body, "proof_sha256": semantic_sha256_v22(body)}


def _seal_semantic_rollover_job_v0233(
    *,
    jobs: JobRepositoryV1,
    product_root: Path,
    job: ProductJobRecordV1,
    acquisition: DiagnosisAcquisitionCheckpointV0233,
    successor_semantic_surface_sha256: str,
) -> ProductJobRecordV1:
    journal = DiagnosisStageJournalRepositoryV02322(jobs.store)
    events = journal.list_events(job.job_id)
    if not events:
        raise RuntimeError("BLOCKED_ECOMSRE_PRODUCT_V0233_RESUME_LINEAGE_MISSING")
    terminal = events[-1]
    fence_path = (
        product_root
        / "private/semantic-rollover-fences"
        / f"{job.job_id}.json"
    )
    if job.status is ProductJobStatusV1.SUCCEEDED:
        if not (
            terminal.stage is DiagnosisPipelineStageV02322.JOB_SUCCEEDED
            and terminal.status is DiagnosisStageStatusV02322.PASSED
            and job.result is not None
            and not fence_path.exists()
        ):
            raise RuntimeError("BLOCKED_ECOMSRE_PRODUCT_V0233_RESUME_LINEAGE_MISSING")
        return job
    if job.status is ProductJobStatusV1.FAILED:
        if job.safe_error_code != "FORMAL_SEMANTIC_GENERATION_CHANGED":
            if not (
                terminal.stage is DiagnosisPipelineStageV02322.FAILED
                and terminal.status is DiagnosisStageStatusV02322.FAILED
                and job.journal_tail_sha256 == terminal.event_sha256
            ):
                raise RuntimeError(
                    "BLOCKED_ECOMSRE_PRODUCT_V0233_RESUME_LINEAGE_MISSING"
                )
            return job
        if not (
            terminal.stage is DiagnosisPipelineStageV02322.FAILED
            and terminal.status is DiagnosisStageStatusV02322.FAILED
            and terminal.safe_error_code == "FORMAL_SEMANTIC_GENERATION_CHANGED"
            and job.journal_tail_sha256 == terminal.event_sha256
            and job.failure_stage is not None
            and job.exception_fingerprint == terminal.exception_fingerprint
        ):
            raise RuntimeError("BLOCKED_ECOMSRE_PRODUCT_V0233_RESUME_LINEAGE_MISSING")
        prior = events[-2] if len(events) > 1 else None
        proof = _semantic_rollover_fence_payload_v0233(
            job=job,
            acquisition=acquisition,
            successor_semantic_surface_sha256=successor_semantic_surface_sha256,
            last_passed_stage=(
                None
                if prior is None
                else next(
                    (
                        event.stage.value
                        for event in reversed(events[:-1])
                        if event.status is DiagnosisStageStatusV02322.PASSED
                    ),
                    None,
                )
            ),
            failing_stage=job.failure_stage,
            journal_tail_sha256=terminal.event_sha256,
            observed_at=terminal.observed_at,
        )
        if (
            terminal.output_artifact_sha256 != proof["fence_intent_sha256"]
            or terminal.exception_fingerprint != proof["exception_fingerprint"]
        ):
            raise RuntimeError("BLOCKED_ECOMSRE_PRODUCT_V0233_RESUME_LINEAGE_MISSING")
        write_private_json(fence_path, proof, create_once=True)
        return job
    if job.status is not ProductJobStatusV1.RUNNING:
        raise RuntimeError("BLOCKED_ECOMSRE_PRODUCT_V0233_RESUME_LINEAGE_MISSING")
    if terminal.stage in {
        DiagnosisPipelineStageV02322.JOB_SUCCEEDED,
        DiagnosisPipelineStageV02322.FAILED,
    }:
        raise RuntimeError("BLOCKED_ECOMSRE_PRODUCT_V0233_RESUME_LINEAGE_MISSING")
    last_passed = next(
        (
            event.stage
            for event in reversed(events)
            if event.status is DiagnosisStageStatusV02322.PASSED
        ),
        None,
    )
    executable = tuple(
        stage
        for stage in DiagnosisPipelineStageV02322
        if stage is not DiagnosisPipelineStageV02322.FAILED
    )
    failing_stage = (
        DiagnosisPipelineStageV02322.JOB_CLAIMED
        if last_passed is None
        else executable[executable.index(last_passed) + 1]
    )
    observed_at = datetime.now(UTC)
    placeholder = _semantic_rollover_fence_payload_v0233(
        job=job,
        acquisition=acquisition,
        successor_semantic_surface_sha256=successor_semantic_surface_sha256,
        last_passed_stage=None if last_passed is None else last_passed.value,
        failing_stage=failing_stage.value,
        journal_tail_sha256="0" * 64,
        observed_at=observed_at,
    )
    event = DiagnosisStageEventV02322.build(
        journal_id=events[0].journal_id,
        job_id=job.job_id,
        incident_id=acquisition.incident_id,
        ordinal=terminal.ordinal + 1,
        stage=DiagnosisPipelineStageV02322.FAILED,
        status=DiagnosisStageStatusV02322.FAILED,
        input_binding_sha256=terminal.event_sha256,
        output_artifact_sha256=placeholder["fence_intent_sha256"],
        source_code_sha256=_sha256_file(Path(__file__)),
        observed_at=observed_at,
        safe_error_code="FORMAL_SEMANTIC_GENERATION_CHANGED",
        exception_fingerprint=placeholder["exception_fingerprint"],
        previous_event_sha256=terminal.event_sha256,
    )
    raced_job: ProductJobRecordV1 | None = None
    with jobs.store.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        try:
            row = connection.execute(
                "SELECT status, claimed_by, attempt_count, idempotency_key "
                "FROM diagnosis_jobs WHERE job_id = ?",
                (job.job_id,),
            ).fetchone()
            if row is None:
                raise RuntimeError(
                    "BLOCKED_ECOMSRE_PRODUCT_V0233_RESUME_LINEAGE_MISSING"
                )
            if row["status"] != ProductJobStatusV1.RUNNING.value:
                connection.execute("COMMIT")
                raced_job = jobs.get(job.job_id)
            else:
                if (
                    row["claimed_by"] != job.claimed_by
                    or row["attempt_count"] != job.attempt_count
                    or row["idempotency_key"] != job.idempotency_key
                ):
                    raise RuntimeError(
                        "BLOCKED_ECOMSRE_PRODUCT_V0233_RESUME_LINEAGE_MISSING"
                    )
                DiagnosisStageJournalRepositoryV02322._insert(  # noqa: SLF001
                    connection, event
                )
                cursor = connection.execute(
                    "UPDATE diagnosis_jobs SET status = ?, result_json = NULL, "
                    "safe_error_code = ?, failure_stage = ?, "
                    "exception_fingerprint = ?, journal_tail_sha256 = ?, "
                    "claimed_by = NULL, lease_expires_at = NULL, updated_at = ? "
                    "WHERE job_id = ? AND status = ? AND claimed_by = ? "
                    "AND attempt_count = ? AND idempotency_key = ?",
                    (
                        ProductJobStatusV1.FAILED.value,
                        "FORMAL_SEMANTIC_GENERATION_CHANGED",
                        failing_stage.value,
                        placeholder["exception_fingerprint"],
                        event.event_sha256,
                        observed_at.timestamp(),
                        job.job_id,
                        ProductJobStatusV1.RUNNING.value,
                        job.claimed_by,
                        job.attempt_count,
                        job.idempotency_key,
                    ),
                )
                if cursor.rowcount != 1:
                    raise RuntimeError(
                        "BLOCKED_ECOMSRE_PRODUCT_V0233_RESUME_LINEAGE_MISSING"
                    )
                jobs._append_event(  # noqa: SLF001
                    connection,
                    job.job_id,
                    "SEMANTIC_GENERATION_FENCED",
                    {
                        "successor_semantic_surface_sha256": (
                            successor_semantic_surface_sha256
                        )
                    },
                    observed_at.timestamp(),
                )
                connection.execute("COMMIT")
        except Exception:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
    if raced_job is not None:
        return _seal_semantic_rollover_job_v0233(
            jobs=jobs,
            product_root=product_root,
            job=raced_job,
            acquisition=acquisition,
            successor_semantic_surface_sha256=successor_semantic_surface_sha256,
        )
    sealed = jobs.get(job.job_id)
    return _seal_semantic_rollover_job_v0233(
        jobs=jobs,
        product_root=product_root,
        job=sealed,
        acquisition=acquisition,
        successor_semantic_surface_sha256=successor_semantic_surface_sha256,
    )


def _reconcile_semantic_rollover_lineage_v0233(
    *,
    root: Path,
    attempt_id: str,
    latest: FormalExecutionCheckpointV0233,
    successor_semantic_surface_sha256: str,
) -> tuple[Path, ...]:
    private_root = root / _attempt_private_locator_v0233(attempt_id) / "execution"
    acquisition_path = private_root / "diagnosis-acquisition-checkpoint.json"
    product_root = root / _attempt_product_locator_v0233(attempt_id)
    promoted_from_job_id: str | None = None
    if not acquisition_path.is_file() or acquisition_path.is_symlink():
        if acquisition_path.exists() or acquisition_path.is_symlink():
            raise RuntimeError(
                "BLOCKED_ECOMSRE_PRODUCT_V0233_RESUME_LINEAGE_MISSING"
            )
        submitted_path = private_root / "diagnosis-job.json"
        if not submitted_path.is_file() or submitted_path.is_symlink():
            if submitted_path.exists() or submitted_path.is_symlink():
                raise RuntimeError(
                    "BLOCKED_ECOMSRE_PRODUCT_V0233_RESUME_LINEAGE_MISSING"
                )
            return ()
        submitted = _load_model(submitted_path, ProductJobRecordV1)
        context = FormalDiagnosisJobContextV0233.model_validate(
            submitted.payload.get("formal_recovery_v0233")
        )
        product_acquisition_path = (
            product_root / context.acquisition_checkpoint_locator
        )
        if (
            not product_acquisition_path.is_file()
            or product_acquisition_path.is_symlink()
        ):
            if (
                product_acquisition_path.exists()
                or product_acquisition_path.is_symlink()
            ):
                raise RuntimeError(
                    "BLOCKED_ECOMSRE_PRODUCT_V0233_RESUME_LINEAGE_MISSING"
                )
            return ()
        acquisition = _load_model(
            product_acquisition_path, DiagnosisAcquisitionCheckpointV0233
        )
        rebound_context = context.model_copy(
            update={"acquisition_sha256": acquisition.acquisition_sha256}
        )
        if (
            submitted.job_type is not ProductJobTypeV1.DIAGNOSIS
            or context.attempt_id != attempt_id
            or context.campaign_id != acquisition.campaign_id
            or context.semantic_generation != latest.semantic_generation
            or context.active_profile_sha256 != acquisition.active_profile_sha256
            or context.semantic_surface_sha256 != latest.semantic_surface_sha256
            or submitted.payload.get("incident_id") != acquisition.incident_id
            or (
                context.acquisition_sha256 is not None
                and context.acquisition_sha256 != acquisition.acquisition_sha256
            )
            or submitted.idempotency_key
            != final_diagnosis_idempotency_key_v0233(
                context=rebound_context,
                incident_sha256=acquisition.incident_sha256,
                acquisition_sha256=acquisition.acquisition_sha256,
            )
        ):
            raise RuntimeError(
                "BLOCKED_ECOMSRE_PRODUCT_V0233_RESUME_LINEAGE_MISSING"
            )
        write_private_json(
            acquisition_path,
            acquisition.model_dump(mode="json"),
            create_once=True,
        )
        promoted_from_job_id = submitted.job_id
    else:
        acquisition = DiagnosisAcquisitionCheckpointV0233.model_validate_json(
            acquisition_path.read_bytes()
        )
    if (
        acquisition.attempt_id != attempt_id
        or acquisition.semantic_generation != latest.semantic_generation
        or acquisition.semantic_surface_sha256 != latest.semantic_surface_sha256
    ):
        raise RuntimeError("BLOCKED_ECOMSRE_PRODUCT_V0233_RESUME_SEMANTIC_DRIFT")
    cleanup = _recover_owned_product_processes_v0233(
        root=root,
        product_root=product_root,
        private_root=private_root,
    )
    if cleanup.get("verdict") != "CLEAN":
        raise RuntimeError("BLOCKED_ECOMSRE_PRODUCT_V0233_RESUME_ACTIVE_LEASE")
    store = SqliteStoreV1(product_root / "product.sqlite3")
    jobs = JobRepositoryV1(store)
    matching: list[tuple[FormalDiagnosisJobContextV0233, ProductJobRecordV1]] = []
    with store.connect() as connection:
        job_ids = tuple(
            str(row["job_id"])
            for row in connection.execute(
                "SELECT job_id FROM diagnosis_jobs WHERE job_type = ? "
                "ORDER BY created_at, job_id",
                (ProductJobTypeV1.DIAGNOSIS.value,),
            ).fetchall()
        )
    for job_id in job_ids:
        job = jobs.get(job_id)
        context_value = job.payload.get("formal_recovery_v0233")
        if not isinstance(context_value, Mapping):
            continue
        context = FormalDiagnosisJobContextV0233.model_validate(context_value)
        if (
            context.attempt_id == attempt_id
            and job.payload.get("incident_id") == acquisition.incident_id
        ):
            matching.append((context, job))
    matching.sort(key=lambda item: item[0].diagnosis_generation)
    if (
        not matching
        or (
            promoted_from_job_id is not None
            and all(job.job_id != promoted_from_job_id for _context, job in matching)
        )
        or tuple(context.diagnosis_generation for context, _ in matching)
        != tuple(range(1, len(matching) + 1))
    ):
        raise RuntimeError("BLOCKED_ECOMSRE_PRODUCT_V0233_RESUME_LINEAGE_MISSING")
    for context, job in matching:
        rebound_context = context.model_copy(
            update={"acquisition_sha256": acquisition.acquisition_sha256}
        )
        if (
            context.campaign_id != acquisition.campaign_id
            or context.semantic_generation != acquisition.semantic_generation
            or context.active_profile_sha256 != acquisition.active_profile_sha256
            or context.semantic_surface_sha256 != acquisition.semantic_surface_sha256
            or (
                context.acquisition_sha256 is not None
                if context.diagnosis_generation == 1
                else context.acquisition_sha256 != acquisition.acquisition_sha256
            )
            or job.idempotency_key
            != final_diagnosis_idempotency_key_v0233(
                context=rebound_context,
                incident_sha256=acquisition.incident_sha256,
                acquisition_sha256=acquisition.acquisition_sha256,
            )
        ):
            raise RuntimeError(
                "BLOCKED_ECOMSRE_PRODUCT_V0233_RESUME_LINEAGE_MISSING"
            )
    statuses = tuple(job.status for _context, job in matching)
    if (
        sum(status is ProductJobStatusV1.RUNNING for status in statuses) > 1
        or ProductJobStatusV1.PENDING in statuses
        or ProductJobStatusV1.CANCELLED in statuses
        or any(
            status is ProductJobStatusV1.SUCCEEDED
            for status in statuses[:-1]
        )
        or (
            ProductJobStatusV1.RUNNING in statuses
            and statuses[-1] is not ProductJobStatusV1.RUNNING
        )
    ):
        raise RuntimeError("BLOCKED_ECOMSRE_PRODUCT_V0233_RESUME_LINEAGE_MISSING")
    for _context, job in matching:
        _seal_semantic_rollover_job_v0233(
            jobs=jobs,
            product_root=product_root,
            job=job,
            acquisition=acquisition,
            successor_semantic_surface_sha256=(
                successor_semantic_surface_sha256
            ),
        )
    lineage = _interrupted_diagnosis_lineage_v0233(
        product_root=product_root,
        acquisition=acquisition,
    )
    lineage_path = private_root / "interrupted-diagnosis-lineage.json"
    write_private_json(lineage_path, lineage, create_once=True)
    fence_root = product_root / "private/semantic-rollover-fences"
    fence_paths = (
        tuple(sorted(fence_root.glob("*.json"))) if fence_root.is_dir() else ()
    )
    return (acquisition_path, lineage_path, *fence_paths)


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
        complete_evidence[checkpoint_path.relative_to(root).as_posix()] = _sha256_file(
            checkpoint_path
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


def _next_attempt_id_v0233(ledger: FormalAttemptLedgerV0233) -> str:
    expected = tuple(
        f"attempt-{ordinal}" for ordinal in range(1, len(ledger.attempts) + 1)
    )
    observed = tuple(item.attempt_id for item in ledger.attempts)
    if observed != expected or ledger.measured_result_count != 0:
        raise RuntimeError("BLOCKED_ECOMSRE_PRODUCT_V0233_ACCEPTANCE_ARTIFACTS")
    return f"attempt-{len(ledger.attempts) + 1}"


def _start_successor_after_nonrecoverable_v0233(
    *,
    root: Path,
    attempt_id: str,
    latest: FormalExecutionCheckpointV0233,
    trigger: RuntimeError,
    successor_semantic_generation: int | None = None,
) -> NoFaultAcceptanceResultV0233:
    if latest.state is not FormalExecutionStateV0233.NONRECOVERABLE_FAILURE:
        raise trigger
    ledger = FormalAttemptLedgerV0233.model_validate_json(
        (root / "config/product-v0233/formal-attempt-ledger.json").read_bytes()
    )
    completion = json.loads(
        (
            root
            / _attempt_private_locator_v0233(attempt_id)
            / "execution/terminal-publication-completion.json"
        ).read_text(encoding="utf-8")
    )
    current = ledger.attempts[-1]
    completion_body = (
        {key: value for key, value in completion.items() if key != "completion_sha256"}
        if isinstance(completion, dict)
        else {}
    )
    if (
        current.attempt_id != attempt_id
        or current.disposition != "NONRECOVERABLE_FAILURE"
        or current.latest_checkpoint_sha256 != latest.checkpoint_sha256
        or current.blocker_terminal != str(trigger)
        or not isinstance(completion, dict)
        or completion.get("terminal") != current.blocker_terminal
        or completion.get("completion_sha256") != semantic_sha256_v22(completion_body)
    ):
        raise RuntimeError(
            "BLOCKED_ECOMSRE_PRODUCT_V0233_ACCEPTANCE_ARTIFACTS"
        ) from trigger
    return run_formal_nofault_v0233(
        project_root=root,
        attempt_id=_next_attempt_id_v0233(ledger),
        semantic_generation=(
            latest.semantic_generation
            if successor_semantic_generation is None
            else successor_semantic_generation
        ),
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

    def retire_nonrecoverable(
        checkpoint: FormalExecutionCheckpointV0233,
        *,
        failure_stage: str = "NONRECOVERABLE_FAILURE_RECOVERY",
        safe_error_code: str = "FORMAL_NONRECOVERABLE_INTERRUPTION",
        next_gate: str = "FRESH_CAPTURE_REQUIRED",
        successor_semantic_generation: int | None = None,
        operational_surface_sha256: str | None = None,
    ) -> NoFaultAcceptanceResultV0233:
        repository = FormalCheckpointRepositoryV0233(attempt_root)
        retired = checkpoint
        if retired.state is FormalExecutionStateV0233.CLOSED:
            raise RuntimeError(
                "BLOCKED_ECOMSRE_PRODUCT_V0233_TERMINAL_PUBLICATION_REQUIRED"
            )
        recovered_clone = _recover_existing_attempt_clone_v0233(
            root,
            attempt_id=attempt_id,
            publish_missing=True,
        )
        recovered_outputs = dict(retired.output_artifact_sha256s)
        recovered_clone_sha256 = retired.formal_clone_sha256
        if recovered_clone is not None:
            recovered_clone_sha256 = recovered_clone.clone_sha256
            public_clone_path = (
                root
                / _attempt_public_locator_v0233(attempt_id)
                / "formal-state-clone.json"
            )
            recovered_outputs[public_clone_path.relative_to(root).as_posix()] = (
                _sha256_file(public_clone_path)
            )
        staging_cleanup_path = (
            attempt_root / "execution/interrupted-clone-staging-cleanup.json"
        )
        if staging_cleanup_path.is_file() and not staging_cleanup_path.is_symlink():
            recovered_outputs[staging_cleanup_path.relative_to(root).as_posix()] = (
                _sha256_file(staging_cleanup_path)
            )
        if retired.state is not FormalExecutionStateV0233.NONRECOVERABLE_FAILURE:
            candidate = FormalExecutionCheckpointV0233.build(
                previous=retired,
                state=FormalExecutionStateV0233.NONRECOVERABLE_FAILURE,
                created_at=datetime.now(UTC),
                operational_surface_sha256=(
                    operational_surface_sha256 or retired.operational_surface_sha256
                ),
                formal_clone_sha256=recovered_clone_sha256,
                input_artifact_sha256s=retired.input_artifact_sha256s,
                output_artifact_sha256s=recovered_outputs,
            )
            retired = candidate
            repository.append(retired)
            if candidate.formal_clone_sha256 is not None:
                cleanup = recover_interrupted_attempt_cleanup_v0233(
                    root,
                    attempt_id=attempt_id,
                    latest=retired,
                    persist=True,
                )
                if cleanup is None or cleanup.get("verdict") != "CLEAN":
                    raise RuntimeError(
                        "BLOCKED_ECOMSRE_PRODUCT_V0233_INTERRUPTED_CLEANUP"
                    )
        elif (
            recovered_clone_sha256 != retired.formal_clone_sha256
            or recovered_outputs != retired.output_artifact_sha256s
        ):
            raise RuntimeError("BLOCKED_ECOMSRE_PRODUCT_V0233_ACCEPTANCE_ARTIFACTS")
        else:
            cleanup = recover_interrupted_attempt_cleanup_v0233(
                root,
                attempt_id=attempt_id,
                latest=retired,
                persist=True,
            )
            if retired.formal_clone_sha256 is not None and (
                cleanup is None or cleanup.get("verdict") != "CLEAN"
            ):
                raise RuntimeError("BLOCKED_ECOMSRE_PRODUCT_V0233_INTERRUPTED_CLEANUP")
        terminal = terminalize_nonrecoverable_attempt_v0233(
            root,
            attempt_id=attempt_id,
            latest=retired,
            failure_stage=failure_stage,
            safe_error_code=safe_error_code,
            next_gate=next_gate,
        )
        return _start_successor_after_nonrecoverable_v0233(
            root=root,
            attempt_id=attempt_id,
            latest=retired,
            trigger=RuntimeError(terminal),
            successor_semantic_generation=successor_semantic_generation,
        )

    if intent_path.is_file():
        chain = FormalCheckpointRepositoryV0233(attempt_root).load_chain()
        if not chain:
            raise RuntimeError("BLOCKED_ECOMSRE_PRODUCT_V0233_ACCEPTANCE_ARTIFACTS")
        latest_for_publication = chain[-1]
        try:
            recovered = _recover_terminal_publication(
                root,
                reservation_path=reservation_path,
                private_root=private_root,
            )
        except RuntimeError as error:
            return _start_successor_after_nonrecoverable_v0233(
                root=root,
                attempt_id=attempt_id,
                latest=latest_for_publication,
                trigger=error,
            )
        if recovered is None:
            raise RuntimeError("BLOCKED_ECOMSRE_PRODUCT_V0233_ACCEPTANCE_ARTIFACTS")
        return recovered

    try:
        latest, semantic, operational = strict_resume_formal_admission_v0233(
            root,
            attempt_id=attempt_id,
        )
    except SemanticGenerationTransitionRequiredV0233 as transition:
        repository = FormalCheckpointRepositoryV0233(attempt_root)
        rollover_intent_body = {
            "schema_version": "ecomsre.product.semantic-rollover-intent.v0233",
            "campaign_id": transition.latest.campaign_id,
            "attempt_id": attempt_id,
            "prior_semantic_generation": transition.latest.semantic_generation,
            "prior_semantic_surface_sha256": (
                transition.latest.semantic_surface_sha256
            ),
            "successor_semantic_generation": (
                transition.semantic.semantic_generation
            ),
            "successor_semantic_surface_sha256": (
                transition.semantic.semantic_surface_sha256
            ),
        }
        rollover_intent = {
            **rollover_intent_body,
            "intent_sha256": semantic_sha256_v22(rollover_intent_body),
        }
        rollover_intent_path = private_root / "semantic-rollover-intent.json"
        write_private_json(
            rollover_intent_path,
            rollover_intent,
            create_once=True,
        )
        rollover_outputs = dict(transition.latest.output_artifact_sha256s)
        rollover_outputs[rollover_intent_path.relative_to(root).as_posix()] = (
            _sha256_file(rollover_intent_path)
        )
        rollover_latest = transition.latest
        if rollover_outputs != rollover_latest.output_artifact_sha256s:
            rollover_latest = _append_checkpoint_v0233(
                repository=repository,
                latest=rollover_latest,
                state=FormalExecutionStateV0233.RECOVERABLE_FAILURE,
                operational_surface_sha256=(
                    transition.operational.operational_surface_sha256
                ),
                outputs=rollover_outputs,
            )
        reconciled_paths = _reconcile_semantic_rollover_lineage_v0233(
            root=root,
            attempt_id=attempt_id,
            latest=rollover_latest,
            successor_semantic_surface_sha256=(
                transition.semantic.semantic_surface_sha256
            ),
        )
        reconciled_outputs = dict(rollover_latest.output_artifact_sha256s)
        for path in reconciled_paths:
            reconciled_outputs[path.relative_to(root).as_posix()] = _sha256_file(path)
        if reconciled_outputs != rollover_latest.output_artifact_sha256s:
            rollover_latest = _append_checkpoint_v0233(
                repository=repository,
                latest=rollover_latest,
                state=FormalExecutionStateV0233.RECOVERABLE_FAILURE,
                operational_surface_sha256=(
                    transition.operational.operational_surface_sha256
                ),
                outputs=reconciled_outputs,
            )
        return retire_nonrecoverable(
            rollover_latest,
            failure_stage="SEMANTIC_GENERATION_INVALIDATED",
            safe_error_code="FORMAL_SEMANTIC_GENERATION_CHANGED",
            next_gate="FRESH_CAPTURE_AT_NEXT_SEMANTIC_GENERATION",
            successor_semantic_generation=transition.semantic.semantic_generation,
            operational_surface_sha256=(
                transition.operational.operational_surface_sha256
            ),
        )
    if latest.state is FormalExecutionStateV0233.NONRECOVERABLE_FAILURE:
        return retire_nonrecoverable(latest)

    repository = FormalCheckpointRepositoryV0233(attempt_root)

    product_root = root / _attempt_product_locator_v0233(attempt_id)
    acquisition_path = private_root / "diagnosis-acquisition-checkpoint.json"
    if not acquisition_path.is_file():
        submitted_path = private_root / "diagnosis-job.json"
        if not submitted_path.is_file() or submitted_path.is_symlink():
            return retire_nonrecoverable(latest)
        submitted = _load_model(submitted_path, ProductJobRecordV1)
        context = FormalDiagnosisJobContextV0233.model_validate(
            submitted.payload.get("formal_recovery_v0233")
        )
        product_acquisition_path = product_root / context.acquisition_checkpoint_locator
        if (
            not product_acquisition_path.is_file()
            or product_acquisition_path.is_symlink()
        ):
            return retire_nonrecoverable(latest)
        promoted = _load_model(
            product_acquisition_path, DiagnosisAcquisitionCheckpointV0233
        )
        if (
            context.attempt_id != attempt_id
            or promoted.attempt_id != attempt_id
            or promoted.semantic_surface_sha256 != semantic.semantic_surface_sha256
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
        recovered_outputs[acquisition_path.relative_to(root).as_posix()] = _sha256_file(
            acquisition_path
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
    stale_cleanup = recover_interrupted_attempt_cleanup_v0233(
        root,
        attempt_id=attempt_id,
        latest=latest,
        persist=False,
    )
    cleanup_clean = (
        stale_cleanup is not None
        and stale_cleanup.get("resource_cleanup_verdict") == "CLEAN"
    )
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
                and submitted_job.payload.get("incident_id") == acquisition.incident_id
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
                diagnosis_generation = recovery_submission.context.diagnosis_generation
                generation_root = latest_recovery_root

    failed_job_ids = _failed_formal_job_ids_v0233(
        product_root=product_root,
        attempt_id=attempt_id,
        incident_id=incident.incident_id,
    )
    if successful_job is None:
        if not failed_job_ids:
            raise RuntimeError("BLOCKED_ECOMSRE_PRODUCT_V0233_RESUME_LINEAGE_MISSING")
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
        outputs[path.resolve(strict=True).relative_to(root).as_posix()] = _sha256_file(
            path
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
        _persist_product_process_authority_v0233(
            processes,
            private_root=generation_root / "product-processes",
        )
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
            "POST_DIAGNOSIS_RECOVERED" if failed_job_ids else "POST_DIAGNOSIS_SUCCEEDED"
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
            original_journal.events + ("DIAGNOSIS_CREATE_REQUESTED",) * recovery_count,
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
        evidence_bundle_sha256=semantic_sha256_v22(evidence.model_dump(mode="json")),
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
    lineage = _diagnosis_lineage_v0233(
        product_root=product_root,
        attempt_id=attempt_id,
        acquisition=acquisition,
        failed_jobs=tuple(jobs.get(job_id) for job_id in final_failed_job_ids),
        successful_job=completed_job,
        diagnosis_generation=diagnosis_generation,
        diagnosis=diagnosis,
    )
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
        live_capture=live_capture,
        incident_binding=incident_binding,
        assessment=assessment,
        pipeline=pipeline,
        diagnosis=diagnosis,
        evidence=evidence,
        index=index,
        decision_trace=decision_trace,
        closure=closure.model_dump(mode="json"),
        result=result,
        handoff=handoff,
        measured_ledger=ledger,
        new_diagnosis_count=safety.new_diagnosis_count,
        recovery_lineage=lineage,
        recovery_acquisition=acquisition,
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
