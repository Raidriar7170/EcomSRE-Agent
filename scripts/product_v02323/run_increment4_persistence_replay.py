#!/usr/bin/env python3
"""Run the single Product v0.2.3.2.3 Diagnosis-only persistence replay."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime, timedelta
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
from typing import Any, Sequence, cast

from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22
from ecomsre.product.baselines import BaselineRepositoryV1
from ecomsre.product.environment.capabilities import CapabilityMatrixRepositoryV1
from ecomsre.product.environment.repository import EnvironmentRepositoryV1
from ecomsre.product.environment.services import ServiceCatalogRepositoryV1
from ecomsre.product.incidents.diagnosis_bridge import ProductDiagnosisBridgeV1
from ecomsre.product.incidents.diagnosis_pipeline_v02322 import (
    DiagnosisPipelineStageV02322,
    DiagnosisPipelineV02322,
)
from ecomsre.product.incidents.diagnosis_stage_journal_v02322 import (
    DiagnosisStageJournalRepositoryV02322,
)
from ecomsre.product.incidents.evidence_binding_v0232 import (
    DiagnosisDecisionTraceV0232,
)
from ecomsre.product.incidents.read_backend import ProductReadBackendV1
from ecomsre.product.incidents.repository import (
    DiagnosisRepositoryV1,
    IncidentRepositoryV1,
)
from ecomsre.product.jobs.contracts import (
    JobLeaseFenceV1,
    ProductJobStatusV1,
    ProductJobTypeV1,
)
from ecomsre.product.jobs.handlers import handle_incident_diagnosis
from ecomsre.product.jobs.repository import JobRepositoryV1
from ecomsre.product.pilot.diagnosis_replay_v02323 import (
    DIAGNOSIS_PIPELINE_REPLAY_PASS_V02323,
    GOAL_VERSION_V02323,
    DiagnosisPipelineReplayResultV02323,
    DiagnosisReplayContractErrorV02323,
    DiagnosisReplayReadBackendV02323,
    DiagnosisRootCauseDispositionV02323,
    FrozenIncidentReplayInputV02323,
    build_structural_acquisition_v02323,
    clone_and_apply_migration9_v02323,
    is_read_only_tree_v02323,
    seal_tree_v02323,
)
from ecomsre.product.pilot.schema8_reconstruction_v02323 import (
    ReconstructionDispositionV02323,
    Schema8ReconstructionV02323,
)
from ecomsre.product.storage.object_store import ContentAddressedObjectStoreV1
from ecomsre.product.storage.sqlite_store import SqliteStoreV1
from scripts.ci.verify_product_v02323_increment3 import (
    _EXPECTED_SUCCESS_SEQUENCE,
    verify_product_v02323_increment3,
)
from scripts.product_v02323.run_increment3_diagnosis_forensics import (
    _acquisition_payload,
    _json_bytes,
    _make_tree_owner_writable,
    _sealed,
    _write_create_once,
)


DIAGNOSIS_REPLAY_BLOCKER_V02323 = "BLOCKED_ECOMSRE_PRODUCT_V02323_DIAGNOSIS_REPLAY"
FORMAL_INCIDENT_ID = "inc-a5a8df708ab77c2f2e19da63"
ORIGINAL_FAILED_JOB_ID = "job-216dd1caac0b92270b1870a2"
EXPECTED_INCREMENT3_REVIEW_SHA256 = (
    "ace4ba428266912b74499e25efe92f67e97a4205c30f39642c798a436cadaaae"
)
_EXPECTED_PERSISTENCE_SEQUENCE = _EXPECTED_SUCCESS_SEQUENCE + tuple(
    (stage, status)
    for stage in (
        "DIAGNOSIS_PERSISTED",
        "JOB_RESULT_PREPARED",
        "JOB_SUCCEEDED",
    )
    for status in ("STARTED", "PASSED")
)


def _validate_replay_request(
    *,
    replay_id: str,
    frozen_replay_id: str,
    observed_at: datetime,
) -> None:
    if (
        re.fullmatch(r"replay-[0-9a-f]{24}", replay_id) is None
        or replay_id == frozen_replay_id
        or observed_at.tzinfo is None
        or observed_at.utcoffset() != timedelta(0)
    ):
        raise DiagnosisReplayContractErrorV02323(DIAGNOSIS_REPLAY_BLOCKER_V02323)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _counts(store: SqliteStoreV1) -> dict[str, int]:
    with store.connect() as connection:
        return {
            "diagnosis_results": int(
                connection.execute("SELECT COUNT(*) FROM diagnosis_results").fetchone()[
                    0
                ]
            ),
            "diagnosis_evidence_indexes": int(
                connection.execute(
                    "SELECT COUNT(*) FROM diagnosis_evidence_indexes"
                ).fetchone()[0]
            ),
            "evidence_objects": int(
                connection.execute("SELECT COUNT(*) FROM evidence_objects").fetchone()[
                    0
                ]
            ),
            "diagnosis_evidence_links": int(
                connection.execute(
                    "SELECT COUNT(*) FROM diagnosis_evidence_links"
                ).fetchone()[0]
            ),
            "diagnosis_jobs": int(
                connection.execute("SELECT COUNT(*) FROM diagnosis_jobs").fetchone()[0]
            ),
            "incidents": int(
                connection.execute("SELECT COUNT(*) FROM incidents").fetchone()[0]
            ),
            "baseline_versions": int(
                connection.execute("SELECT COUNT(*) FROM baseline_versions").fetchone()[
                    0
                ]
            ),
            "diagnosis_stage_events": int(
                connection.execute(
                    "SELECT COUNT(*) FROM diagnosis_stage_events_v02322"
                ).fetchone()[0]
            ),
        }


def _model_sha256(value: Any) -> str:
    return semantic_sha256_v22(value.model_dump(mode="json"))


def _detect_persistence_commit(
    product_root: Path,
    *,
    replay_id: str,
    formal_incident_id: str,
) -> tuple[bool, str | None]:
    database = product_root / "product.sqlite3"
    if not database.is_file():
        return False, None
    connection = sqlite3.connect(
        f"{database.resolve(strict=True).as_uri()}?mode=ro",
        uri=True,
    )
    try:
        diagnosis_row = connection.execute(
            "SELECT payload_json FROM diagnosis_results WHERE incident_id = ?",
            (formal_incident_id,),
        ).fetchone()
        recovery_rows = connection.execute(
            "SELECT status, payload_json FROM diagnosis_jobs WHERE job_type = ?",
            (ProductJobTypeV1.DIAGNOSIS.value,),
        ).fetchall()
    finally:
        connection.close()
    recovery_committed = any(
        str(row[0]) == ProductJobStatusV1.SUCCEEDED.value
        and json.loads(str(row[1])).get("replay_id") == replay_id
        for row in recovery_rows
    )
    result_sha256: str | None = None
    if diagnosis_row is not None:
        payload = json.loads(str(diagnosis_row[0]))
        supplied = payload.get("result_sha256")
        if isinstance(supplied, str) and re.fullmatch(r"[0-9a-f]{64}", supplied):
            result_sha256 = supplied
    return diagnosis_row is not None or recovery_committed, result_sha256


def _load_persisted_decision_trace(
    store: SqliteStoreV1,
    object_store: ContentAddressedObjectStoreV1,
    *,
    expected_trace_sha256: str,
) -> tuple[DiagnosisDecisionTraceV0232, str]:
    with store.connect() as connection:
        object_sha256s = tuple(
            str(row["object_sha256"])
            for row in connection.execute(
                "SELECT object_sha256 FROM evidence_objects ORDER BY object_sha256"
            ).fetchall()
        )
    matches: list[tuple[DiagnosisDecisionTraceV0232, str]] = []
    for object_sha256 in object_sha256s:
        try:
            candidate = DiagnosisDecisionTraceV0232.model_validate_json(
                object_store.read_bytes(object_sha256)
            )
        except (ValueError, json.JSONDecodeError):
            continue
        if candidate.trace_sha256 == expected_trace_sha256:
            matches.append((candidate, object_sha256))
    if len(matches) != 1:
        raise DiagnosisReplayContractErrorV02323(DIAGNOSIS_REPLAY_BLOCKER_V02323)
    return matches[0]


def execute_persistence_replay_v02323(
    product_root: Path,
    *,
    replay_id: str,
    replay_input: FrozenIncidentReplayInputV02323,
    reconstruction_disposition: ReconstructionDispositionV02323,
    root_cause: DiagnosisRootCauseDispositionV02323,
    observed_at: datetime,
) -> DiagnosisPipelineReplayResultV02323:
    """Persist exactly one recovery Diagnosis in an already-disposable clone."""

    _validate_replay_request(
        replay_id=replay_id,
        frozen_replay_id=replay_input.replay_id,
        observed_at=observed_at,
    )
    if (
        root_cause.replay_input_sha256 != replay_input.replay_input_sha256
        or reconstruction_disposition.disposition_sha256
        != replay_input.reconstruction_disposition_sha256
    ):
        raise DiagnosisReplayContractErrorV02323(DIAGNOSIS_REPLAY_BLOCKER_V02323)

    store = SqliteStoreV1(product_root / "product.sqlite3")
    jobs = JobRepositoryV1(store)
    journal = DiagnosisStageJournalRepositoryV02322(store)
    environments = EnvironmentRepositoryV1(store)
    services = ServiceCatalogRepositoryV1(store)
    capabilities = CapabilityMatrixRepositoryV1(store)
    baselines = BaselineRepositoryV1(store)
    incidents = IncidentRepositoryV1(
        store,
        environments=environments,
        services=services,
        capabilities=capabilities,
        baselines=baselines,
    )
    object_store = ContentAddressedObjectStoreV1(
        product_root / "objects", metadata_store=store
    )
    diagnoses = DiagnosisRepositoryV1(store, object_store)
    formal_incident_id = replay_input.formal_incident_id
    original_failed_job_id = replay_input.original_failed_job_id
    incident = incidents.get(formal_incident_id)
    identity = services.get_map(incident.environment_id)
    capability = capabilities.get(incident.environment_id)
    baseline = baselines.get_optional(incident.baseline_id)
    if (
        incident.incident_sha256 != replay_input.formal_incident_sha256
        or baseline is None
        or baseline.baseline_sha256 != incident.baseline_sha256
        or identity.identity_sha256 != incident.service_identity_sha256
        or capability.capability_sha256 != incident.source_capability_sha256
    ):
        raise DiagnosisReplayContractErrorV02323(DIAGNOSIS_REPLAY_BLOCKER_V02323)
    acquisition = build_structural_acquisition_v02323(
        incident=incident, baseline=baseline
    )
    if (
        semantic_sha256_v22(_acquisition_payload(acquisition))
        != replay_input.structural_input_sha256_by_kind["acquisition"]
        or diagnoses.get_optional(formal_incident_id) is not None
    ):
        raise DiagnosisReplayContractErrorV02323(DIAGNOSIS_REPLAY_BLOCKER_V02323)

    original_before = jobs.get(original_failed_job_id)
    original_before_sha256 = _model_sha256(original_before)
    before = _counts(store)
    if (
        original_before.status is not ProductJobStatusV1.FAILED
        or original_before.safe_error_code != "INTERNAL_CONTRACT_FAILURE"
        or original_before.result is not None
    ):
        raise DiagnosisReplayContractErrorV02323(DIAGNOSIS_REPLAY_BLOCKER_V02323)

    recovery_payload: dict[str, object] = {
        "incident_id": formal_incident_id,
        "replay_id": replay_id,
        "replay_of_job_id": original_failed_job_id,
        "replay_input_sha256": replay_input.replay_input_sha256,
        "reconstruction_disposition": reconstruction_disposition.disposition,
        "replay_classification": replay_input.replay_classification.value,
    }
    recovery = jobs.enqueue(
        ProductJobTypeV1.DIAGNOSIS,
        recovery_payload,
        idempotency_key=f"product-v02323:{replay_id}",
        now=observed_at.timestamp(),
    )
    worker_id = f"product-v02323-{replay_id.removeprefix('replay-')}"
    claimed = jobs.claim_next(
        worker_id,
        lease_seconds=300,
        now=(observed_at + timedelta(seconds=1)).timestamp(),
    )
    if claimed is None or claimed.job_id != recovery.job_id:
        raise DiagnosisReplayContractErrorV02323(DIAGNOSIS_REPLAY_BLOCKER_V02323)

    pipeline = DiagnosisPipelineV02322(
        journal,
        job_id=claimed.job_id,
        incident_id=formal_incident_id,
        observed_at=observed_at + timedelta(seconds=2),
    )
    pipeline.bind_artifacts(incident_sha256=incident.incident_sha256)
    fence = JobLeaseFenceV1(
        job_id=claimed.job_id,
        claimed_by=worker_id,
        attempt_count=claimed.attempt_count,
        checked_at=(observed_at + timedelta(seconds=2)).timestamp(),
    )
    replay_backend = DiagnosisReplayReadBackendV02323(acquisition)
    try:
        job_payload_sha256 = semantic_sha256_v22(claimed.payload)
        pipeline.run(
            DiagnosisPipelineStageV02322.JOB_CLAIMED,
            input_binding_sha256=job_payload_sha256,
            operation=lambda: {"attempt_count": claimed.attempt_count},
        )
        loaded_incident = pipeline.run(
            DiagnosisPipelineStageV02322.INCIDENT_LOAD_STARTED,
            input_binding_sha256=job_payload_sha256,
            operation=lambda: incidents.get(formal_incident_id),
        )
        pipeline.run(
            DiagnosisPipelineStageV02322.INCIDENT_LOADED,
            input_binding_sha256=loaded_incident.incident_sha256,
            operation=lambda: loaded_incident,
        )
        result_payload = handle_incident_diagnosis(
            claimed,
            incidents,
            diagnoses,
            environments,
            services,
            capabilities,
            baselines,
            cast(ProductReadBackendV1, replay_backend),
            ProductDiagnosisBridgeV1(),
            fence=fence,
            stage_pipeline_v02322=pipeline,
            loaded_incident_v02322=loaded_incident,
        )
        result_payload_sha256 = semantic_sha256_v22(result_payload)
        pipeline.run(
            DiagnosisPipelineStageV02322.JOB_RESULT_PREPARED,
            input_binding_sha256=result_payload_sha256,
            operation=lambda: result_payload,
        )
        pipeline.run(
            DiagnosisPipelineStageV02322.JOB_SUCCEEDED,
            input_binding_sha256=result_payload_sha256,
            operation=lambda: jobs.succeed(
                claimed.job_id,
                worker_id,
                claimed.attempt_count,
                result_payload,
                now=(observed_at + timedelta(seconds=3)).timestamp(),
            ),
        )
    except Exception as error:
        projection, _envelope, _path = pipeline.capture_failure(
            error,
            data_root=product_root,
            job_payload=claimed.payload,
        )
        jobs.fail(
            claimed.job_id,
            worker_id,
            claimed.attempt_count,
            projection.safe_error_code,
            public_failure_v02322=projection,
            now=(observed_at + timedelta(seconds=3)).timestamp(),
        )
        raise

    original_after = jobs.get(original_failed_job_id)
    recovery_after = jobs.get(recovery.job_id)
    stored_result = diagnoses.get(formal_incident_id)
    bundle = diagnoses.evidence(formal_incident_id)
    index = diagnoses.evidence_index(formal_incident_id)
    decision_trace, decision_trace_object_sha256 = _load_persisted_decision_trace(
        store,
        object_store,
        expected_trace_sha256=index.decision_trace_sha256,
    )
    events = journal.list_events(recovery.job_id)
    after = _counts(store)
    if (
        original_before != original_after
        or recovery_after.status is not ProductJobStatusV1.SUCCEEDED
        or recovery_after.result != stored_result.model_dump(mode="json")
        or stored_result.terminal.value != "INSUFFICIENT_EVIDENCE"
        or bundle.diagnosis_id != stored_result.diagnosis_id
        or index.diagnosis_id != stored_result.diagnosis_id
        or decision_trace.diagnosis_id != stored_result.diagnosis_id
        or index.evidence_bundle_sha256
        != semantic_sha256_v22(bundle.model_dump(mode="json"))
        or decision_trace.trace_sha256 != index.decision_trace_sha256
        or len(events) != 54
        or tuple((event.stage.value, event.status.value) for event in events)
        != _EXPECTED_PERSISTENCE_SEQUENCE
        or before["diagnosis_stage_events"] != 0
        or after["diagnosis_stage_events"] != 54
        or events[-1].stage is not DiagnosisPipelineStageV02322.JOB_SUCCEEDED
        or events[-1].status.value != "PASSED"
        or before["incidents"] != after["incidents"]
        or before["baseline_versions"] != after["baseline_versions"]
        or replay_backend.call_count != 1
    ):
        raise DiagnosisReplayContractErrorV02323(DIAGNOSIS_REPLAY_BLOCKER_V02323)

    body: dict[str, object] = {
        "schema_version": "ecomsre.product.diagnosis-pipeline-replay-result.v02323",
        "goal_version": GOAL_VERSION_V02323,
        "terminal": DIAGNOSIS_PIPELINE_REPLAY_PASS_V02323,
        "replay_id": replay_id,
        "replay_input_sha256": replay_input.replay_input_sha256,
        "reconstruction_disposition": reconstruction_disposition.disposition,
        "reconstruction_disposition_sha256": (
            reconstruction_disposition.disposition_sha256
        ),
        "replay_classification": replay_input.replay_classification.value,
        "root_cause_disposition": root_cause.disposition,
        "root_cause_disposition_sha256": root_cause.disposition_sha256,
        "targeted_repair_sha256": None,
        "formal_incident_id": formal_incident_id,
        "original_failed_job_id": original_failed_job_id,
        "original_failed_job_sha256_before": original_before_sha256,
        "original_failed_job_sha256_after": _model_sha256(original_after),
        "recovery_job_id": recovery.job_id,
        "recovery_job_sha256": _model_sha256(recovery_after),
        "recovery_job_status": recovery_after.status.value,
        "diagnosis_result_sha256": stored_result.result_sha256,
        "diagnosis_terminal": stored_result.terminal.value,
        "evidence_bundle_sha256": semantic_sha256_v22(bundle.model_dump(mode="json")),
        "evidence_index_sha256": index.index_sha256,
        "decision_trace_sha256": decision_trace.trace_sha256,
        "decision_trace_object_sha256": decision_trace_object_sha256,
        "stage_event_count": len(events),
        "stage_journal_terminal": events[-1].stage.value,
        "journal_tail_sha256": events[-1].event_sha256,
        "diagnosis_count_before": before["diagnosis_results"],
        "diagnosis_count_after": after["diagnosis_results"],
        "evidence_index_count_before": before["diagnosis_evidence_indexes"],
        "evidence_index_count_after": after["diagnosis_evidence_indexes"],
        "evidence_object_count_before": before["evidence_objects"],
        "evidence_object_count_after": after["evidence_objects"],
        "evidence_link_count_before": before["diagnosis_evidence_links"],
        "evidence_link_count_after": after["diagnosis_evidence_links"],
        "job_count_before": before["diagnosis_jobs"],
        "job_count_after": after["diagnosis_jobs"],
        "replay_backend_call_count": replay_backend.call_count,
        "original_failed_job_unchanged": True,
        "sealed_reconstruction_unchanged": True,
        "forensic_source_snapshot_unchanged": True,
        "diagnosis_persistence_replay_attempt_count": 1,
        "fault_attempts": 0,
        "new_baseline_attempts": 0,
        "new_business_traffic_executions": 0,
        "new_product_incidents": 0,
        "provider_agent_runbook_docker_calls": 0,
        "measured_nofault_authority": "NONE",
        "knowledge_loop_authority": "NONE",
    }
    return DiagnosisPipelineReplayResultV02323.model_validate(
        {**body, "result_sha256": semantic_sha256_v22(body)}
    )


def preflight_increment4(
    root: Path,
    *,
    source_root: Path,
    pristine_root: Path,
    formal_private_root: Path,
    replay_id: str,
    observed_at: datetime,
) -> dict[str, object]:
    project = Path(root).resolve(strict=True)
    increment3 = verify_product_v02323_increment3(
        project,
        source_root=source_root,
        pristine_root=pristine_root,
        formal_private_root=formal_private_root,
    )
    progress = json.loads(
        (project / "docs/analysis/product-v02323-progress.json").read_text(
            encoding="utf-8"
        )
    )
    review_path = project / "docs/external-reviews/product-v02323-replay-review.md"
    review = review_path.read_text(encoding="utf-8")
    replay_input = FrozenIncidentReplayInputV02323.model_validate_json(
        (project / "config/product-v02323/replay/replay-input.json").read_text(
            encoding="utf-8"
        )
    )
    persistence_root = project / ".local/product-v02323/diagnosis-persistence-replay"
    public_paths = (
        project / "docs/analysis/product-v02323-diagnosis-pipeline-replay.json",
        project / "docs/analysis/product-v02323-diagnosis-persistence-attempts.json",
    )
    _validate_replay_request(
        replay_id=replay_id,
        frozen_replay_id=replay_input.replay_id,
        observed_at=observed_at,
    )
    if (
        replay_input.formal_incident_id != FORMAL_INCIDENT_ID
        or replay_input.original_failed_job_id != ORIGINAL_FAILED_JOB_ID
        or progress.get("diagnosis_persistence_replay_attempt_count") != 0
        or progress.get("next_gate")
        != "INCREMENT_4_NO_DEFECT_PATH_AND_SINGLE_PERSISTENCE_REPLAY"
        or any(path.exists() for path in public_paths)
        or persistence_root.exists()
        or _sha256_file(review_path) != EXPECTED_INCREMENT3_REVIEW_SHA256
        or any(
            marker not in review
            for marker in (
                "Implementation verdict: `PASS`",
                "Claim accuracy: `PASS`",
                "Must Fix: `0`",
                "Should Fix: `0`",
                "AUTHORIZED_BY_REVIEW_NOT_EXECUTED",
            )
        )
    ):
        raise DiagnosisReplayContractErrorV02323(DIAGNOSIS_REPLAY_BLOCKER_V02323)
    body: dict[str, object] = {
        "schema_version": "ecomsre.product.diagnosis-persistence-preflight.v02323",
        "terminal": "ECOMSRE_PRODUCT_V02323_DIAGNOSIS_PERSISTENCE_PREFLIGHT_PASS",
        "replay_id": replay_id,
        "observed_at": observed_at.isoformat(),
        "replay_input_sha256": replay_input.replay_input_sha256,
        "forensics_sha256": increment3["forensics_sha256"],
        "root_cause_disposition": increment3["root_cause_disposition"],
        "review_file_sha256": _sha256_file(review_path),
        "progress_sha256_before": increment3["progress_sha256"],
        "diagnosis_persistence_replay_attempt_count_before": 0,
        "diagnosis_persistence_replay_attempt_limit": 1,
        "provider_agent_runbook_docker_calls": 0,
        "measured_nofault_authority": "NONE",
        "knowledge_loop_authority": "NONE",
    }
    return _sealed(body, "preflight_sha256")


def _build_attempts_manifest(
    result: DiagnosisPipelineReplayResultV02323,
) -> dict[str, object]:
    return _sealed(
        {
            "schema_version": ("ecomsre.product.diagnosis-persistence-attempts.v02323"),
            "attempt_count": 1,
            "attempts": [
                {
                    "attempt_ordinal": 1,
                    "replay_id": result.replay_id,
                    "status": "PASS",
                    "result_sha256": result.result_sha256,
                    "read_only": True,
                }
            ],
            "diagnosis_persistence_replay_attempt_count": 1,
            "provider_agent_runbook_docker_calls": 0,
        },
        "attempts_sha256",
    )


def _build_increment4_progress(
    progress_before: dict[str, object],
    *,
    result: DiagnosisPipelineReplayResultV02323,
    attempts_sha256: object,
) -> dict[str, object]:
    terminals = progress_before.get("terminals")
    if not isinstance(terminals, list):
        raise DiagnosisReplayContractErrorV02323(DIAGNOSIS_REPLAY_BLOCKER_V02323)
    body = {
        **{
            key: value
            for key, value in progress_before.items()
            if key
            not in {"progress_sha256", "increment", "phase", "terminals", "next_gate"}
        },
        "increment": 4,
        "phase": "DIAGNOSIS_REPLAY_COMPLETE",
        "terminals": [*terminals, result.terminal],
        "diagnosis_pipeline_replay_result_sha256": result.result_sha256,
        "diagnosis_persistence_replay_attempt_count": 1,
        "diagnosis_persistence_attempts_sha256": attempts_sha256,
        "provider_calls": 0,
        "agent_writes": 0,
        "runbook_executions": 0,
        "docker_calls": 0,
        "measured_nofault_authority": "NONE",
        "knowledge_loop_authority": "NONE",
        "next_gate": "INCREMENT_5_REPOSITORY_ACCEPTANCE_AND_CLOSEOUT",
    }
    return _sealed(body, "progress_sha256")


def _result_summary(
    result: DiagnosisPipelineReplayResultV02323,
    *,
    progress_sha256: object,
) -> dict[str, object]:
    return {
        "terminal": result.terminal,
        "replay_id": result.replay_id,
        "result_sha256": result.result_sha256,
        "recovery_job_id": result.recovery_job_id,
        "diagnosis_result_sha256": result.diagnosis_result_sha256,
        "evidence_bundle_sha256": result.evidence_bundle_sha256,
        "evidence_index_sha256": result.evidence_index_sha256,
        "decision_trace_sha256": result.decision_trace_sha256,
        "decision_trace_object_sha256": result.decision_trace_object_sha256,
        "stage_event_count": result.stage_event_count,
        "stage_journal_terminal": result.stage_journal_terminal,
        "diagnosis_persistence_replay_attempt_count": 1,
        "provider_agent_runbook_docker_calls": 0,
        "measured_nofault_authority": "NONE",
        "knowledge_loop_authority": "NONE",
        "progress_sha256": progress_sha256,
    }


def _stage_publication(
    attempt_root: Path,
    *,
    progress_before_bytes: bytes,
    result: DiagnosisPipelineReplayResultV02323,
    attempts: dict[str, object],
    progress: dict[str, object],
) -> None:
    publication = attempt_root / "publication"
    payloads = {
        "pre-publication-progress.json": progress_before_bytes,
        "product-v02323-diagnosis-pipeline-replay.json": _json_bytes(
            result.model_dump(mode="json")
        ),
        "product-v02323-diagnosis-persistence-attempts.json": _json_bytes(attempts),
        "product-v02323-progress.json": _json_bytes(progress),
    }
    for name, payload in payloads.items():
        _write_create_once(publication / name, payload)


def _publish_exact(path: Path, payload: bytes, *, replace: bool = False) -> None:
    temporary = path.with_name(f".{path.name}.v02323-increment3.tmp")
    if path.exists():
        if not path.is_file():
            raise DiagnosisReplayContractErrorV02323(DIAGNOSIS_REPLAY_BLOCKER_V02323)
        if path.read_bytes() == payload:
            if temporary.exists():
                if not temporary.is_file() or temporary.read_bytes() != payload:
                    raise DiagnosisReplayContractErrorV02323(
                        DIAGNOSIS_REPLAY_BLOCKER_V02323
                    )
                temporary.unlink()
            return
        if not replace:
            raise DiagnosisReplayContractErrorV02323(DIAGNOSIS_REPLAY_BLOCKER_V02323)
    if temporary.exists():
        if not temporary.is_file() or temporary.read_bytes() != payload:
            raise DiagnosisReplayContractErrorV02323(DIAGNOSIS_REPLAY_BLOCKER_V02323)
        os.replace(temporary, path)
        return
    _write_create_once(path, payload, replace=replace)


def finalize_increment4_publication(
    root: Path,
    *,
    replay_id: str,
) -> dict[str, object]:
    project = Path(root).resolve(strict=True)
    attempt_root = (
        project / ".local/product-v02323/diagnosis-persistence-replay" / replay_id
    ).resolve(strict=True)
    publication = attempt_root / "publication"
    expected_names = {
        "pre-publication-progress.json",
        "product-v02323-diagnosis-pipeline-replay.json",
        "product-v02323-diagnosis-persistence-attempts.json",
        "product-v02323-progress.json",
    }
    if (
        attempt_root.is_symlink()
        or not is_read_only_tree_v02323(attempt_root)
        or not publication.is_dir()
        or publication.is_symlink()
        or {item.name for item in publication.iterdir()} != expected_names
    ):
        raise DiagnosisReplayContractErrorV02323(DIAGNOSIS_REPLAY_BLOCKER_V02323)
    pre_progress_bytes = (publication / "pre-publication-progress.json").read_bytes()
    result_bytes = (
        publication / "product-v02323-diagnosis-pipeline-replay.json"
    ).read_bytes()
    attempts_bytes = (
        publication / "product-v02323-diagnosis-persistence-attempts.json"
    ).read_bytes()
    progress_bytes = (publication / "product-v02323-progress.json").read_bytes()
    result = DiagnosisPipelineReplayResultV02323.model_validate_json(result_bytes)
    attempts = json.loads(attempts_bytes)
    progress = json.loads(progress_bytes)
    if not isinstance(attempts, dict) or not isinstance(progress, dict):
        raise DiagnosisReplayContractErrorV02323(DIAGNOSIS_REPLAY_BLOCKER_V02323)
    attempts_body = dict(attempts)
    attempts_sha256 = attempts_body.pop("attempts_sha256", None)
    progress_body = dict(progress)
    progress_sha256 = progress_body.pop("progress_sha256", None)
    if (
        result.replay_id != replay_id
        or attempts_sha256 != semantic_sha256_v22(attempts_body)
        or progress_sha256 != semantic_sha256_v22(progress_body)
        or attempts.get("attempts")
        != [
            {
                "attempt_ordinal": 1,
                "replay_id": replay_id,
                "status": "PASS",
                "result_sha256": result.result_sha256,
                "read_only": True,
            }
        ]
        or progress.get("diagnosis_pipeline_replay_result_sha256")
        != result.result_sha256
        or progress.get("diagnosis_persistence_attempts_sha256") != attempts_sha256
    ):
        raise DiagnosisReplayContractErrorV02323(DIAGNOSIS_REPLAY_BLOCKER_V02323)

    result_target = (
        project / "docs/analysis/product-v02323-diagnosis-pipeline-replay.json"
    )
    attempts_target = (
        project / "docs/analysis/product-v02323-diagnosis-persistence-attempts.json"
    )
    progress_target = project / "docs/analysis/product-v02323-progress.json"
    _publish_exact(result_target, result_bytes)
    _publish_exact(attempts_target, attempts_bytes)
    current_progress = progress_target.read_bytes()
    if current_progress != progress_bytes:
        if current_progress != pre_progress_bytes:
            raise DiagnosisReplayContractErrorV02323(DIAGNOSIS_REPLAY_BLOCKER_V02323)
        _publish_exact(progress_target, progress_bytes, replace=True)
    return _result_summary(result, progress_sha256=progress_sha256)


def run_increment4(
    root: Path,
    *,
    source_root: Path,
    pristine_root: Path,
    formal_private_root: Path,
    replay_id: str,
    observed_at: datetime,
) -> dict[str, object]:
    project = Path(root).resolve(strict=True)
    preflight = preflight_increment4(
        project,
        source_root=source_root,
        pristine_root=pristine_root,
        formal_private_root=formal_private_root,
        replay_id=replay_id,
        observed_at=observed_at,
    )
    replay_input = FrozenIncidentReplayInputV02323.model_validate_json(
        (project / "config/product-v02323/replay/replay-input.json").read_text(
            encoding="utf-8"
        )
    )
    reconstruction_payload = json.loads(
        (
            project / "docs/analysis/product-v02323-schema8-reconstruction.json"
        ).read_text(encoding="utf-8")
    )
    reconstruction = Schema8ReconstructionV02323.model_validate(
        reconstruction_payload["reconstruction"]
    )
    reconstruction_disposition = ReconstructionDispositionV02323.model_validate_json(
        (
            project / "docs/analysis/product-v02323-reconstruction-disposition.json"
        ).read_text(encoding="utf-8")
    )
    root_cause = DiagnosisRootCauseDispositionV02323.model_validate_json(
        (project / "docs/analysis/product-v02323-diagnosis-root-cause.json").read_text(
            encoding="utf-8"
        )
    )
    progress_path = project / "docs/analysis/product-v02323-progress.json"
    progress_before_bytes = progress_path.read_bytes()
    progress_before = json.loads(progress_before_bytes)
    if (
        not isinstance(progress_before, dict)
        or progress_before.get("progress_sha256") != preflight["progress_sha256_before"]
    ):
        raise DiagnosisReplayContractErrorV02323(DIAGNOSIS_REPLAY_BLOCKER_V02323)
    attempt_root = (
        project / ".local/product-v02323/diagnosis-persistence-replay" / replay_id
    )
    product_root = attempt_root / "product"
    attempt_created = False
    attempt_started = False
    persistence_committed = False
    result: DiagnosisPipelineReplayResultV02323 | None = None
    try:
        attempt_root.mkdir(parents=True, exist_ok=False)
        attempt_created = True
        _write_create_once(
            attempt_root / "preflight.json",
            _json_bytes(preflight),
        )
        clone_and_apply_migration9_v02323(
            project / reconstruction.reconstruction_locator,
            product_root,
            applied_at=observed_at,
        )
        attempt_started = True
        result = execute_persistence_replay_v02323(
            product_root,
            replay_id=replay_id,
            replay_input=replay_input,
            reconstruction_disposition=reconstruction_disposition,
            root_cause=root_cause,
            observed_at=observed_at + timedelta(seconds=1),
        )
        persistence_committed = True
        post = verify_product_v02323_increment3(
            project,
            source_root=source_root,
            pristine_root=pristine_root,
            formal_private_root=formal_private_root,
        )
        if post[
            "diagnosis_persistence_replay_attempt_count"
        ] != 0 or not is_read_only_tree_v02323(
            project / reconstruction.reconstruction_locator
        ):
            raise DiagnosisReplayContractErrorV02323(DIAGNOSIS_REPLAY_BLOCKER_V02323)
        _write_create_once(
            attempt_root / "replay-result.json",
            _json_bytes(result.model_dump(mode="json")),
        )
        attempts = _build_attempts_manifest(result)
        progress = _build_increment4_progress(
            progress_before,
            result=result,
            attempts_sha256=attempts["attempts_sha256"],
        )
        _stage_publication(
            attempt_root,
            progress_before_bytes=progress_before_bytes,
            result=result,
            attempts=attempts,
            progress=progress,
        )
        seal_tree_v02323(attempt_root)
    except Exception as error:
        if attempt_created:
            recovered_result_sha256: str | None = None
            if attempt_started:
                detected_commit, recovered_result_sha256 = _detect_persistence_commit(
                    product_root,
                    replay_id=replay_id,
                    formal_incident_id=replay_input.formal_incident_id,
                )
                persistence_committed = persistence_committed or detected_commit
            _make_tree_owner_writable(attempt_root)
            failure = _sealed(
                {
                    "schema_version": (
                        "ecomsre.product.diagnosis-persistence-failure.v02323"
                    ),
                    "terminal": DIAGNOSIS_REPLAY_BLOCKER_V02323,
                    "replay_id": replay_id,
                    "exception_class": type(error).__qualname__,
                    "persistence_attempt_started": attempt_started,
                    "persistence_committed": persistence_committed,
                    "result_sha256": (
                        recovered_result_sha256
                        if result is None
                        else result.result_sha256
                    ),
                    "diagnosis_persistence_replay_attempt_count": int(attempt_started),
                    "provider_agent_runbook_docker_calls": 0,
                },
                "failure_sha256",
            )
            _write_create_once(
                attempt_root / "replay-failure.json",
                _json_bytes(failure),
            )
            seal_tree_v02323(attempt_root)
        raise
    return finalize_increment4_publication(project, replay_id=replay_id)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--pristine-root", type=Path, required=True)
    parser.add_argument("--formal-private-root", type=Path, required=True)
    parser.add_argument("--replay-id", required=True)
    parser.add_argument("--observed-at", type=datetime.fromisoformat, required=True)
    parser.add_argument("--preflight-only", action="store_true")
    parser.add_argument("--finalize-only", action="store_true")
    arguments = parser.parse_args(argv)
    if arguments.preflight_only and arguments.finalize_only:
        parser.error("--preflight-only and --finalize-only are mutually exclusive")
    if arguments.finalize_only:
        result = finalize_increment4_publication(
            arguments.root,
            replay_id=arguments.replay_id,
        )
    else:
        operation = preflight_increment4 if arguments.preflight_only else run_increment4
        result = operation(
            arguments.root,
            source_root=arguments.source_root,
            pristine_root=arguments.pristine_root,
            formal_private_root=arguments.formal_private_root,
            replay_id=arguments.replay_id,
            observed_at=arguments.observed_at.astimezone(UTC),
        )
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = (
    "execute_persistence_replay_v02323",
    "finalize_increment4_publication",
    "preflight_increment4",
    "run_increment4",
)
