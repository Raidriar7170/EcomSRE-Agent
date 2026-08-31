#!/usr/bin/env python3
"""Verify the single Product v0.2.3.2.3 Diagnosis persistence replay."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Any, Sequence

from ecomsre.dta_v2.v22.read_contracts import EvidenceSourceV22, semantic_sha256_v22
from ecomsre.product.incidents.contracts import (
    DiagnosisResultV1,
    EvidenceBundleV1,
    EvidenceObjectV1,
)
from ecomsre.product.incidents.diagnosis_stage_journal_v02322 import (
    DiagnosisStageEventV02322,
)
from ecomsre.product.incidents.evidence_binding_v0232 import (
    DiagnosisDecisionTraceV0232,
    DiagnosisEvidenceIndexV0232,
)
from ecomsre.product.jobs.contracts import (
    ProductJobRecordV1,
    ProductJobStatusV1,
    ProductJobTypeV1,
)
from ecomsre.product.pilot.diagnosis_replay_v02323 import (
    DiagnosisPipelineReplayResultV02323,
    FrozenIncidentReplayInputV02323,
    is_read_only_tree_v02323,
)
from ecomsre.product.pilot.schema8_reconstruction_v02323 import (
    Schema8ReconstructionV02323,
)
from scripts.ci.verify_product_v02323_increment3 import (
    _EXPECTED_SUCCESS_SEQUENCE,
    _expected_schema9_inventory,
    _logical_database_payload,
    _schema_inventory_payload,
    verify_product_v02323_increment3,
)
from scripts.product_v02323.run_increment2_reconstruction import _private_path


FORMAL_INCIDENT_ID = "inc-a5a8df708ab77c2f2e19da63"
ORIGINAL_FAILED_JOB_ID = "job-216dd1caac0b92270b1870a2"
DIAGNOSIS_REPLAY_PASS = "ECOMSRE_PRODUCT_V02323_DIAGNOSIS_PIPELINE_REPLAY_PASS"
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
_MUTATED_TABLES = {
    "diagnosis_evidence_indexes",
    "diagnosis_evidence_links",
    "diagnosis_jobs",
    "diagnosis_results",
    "evidence_objects",
    "job_events",
    "schema_migrations",
}
_INCREMENT3_TERMINALS = [
    "ECOMSRE_PRODUCT_V02323_HISTORY_AND_BLOCKER_PASS",
    "ECOMSRE_PRODUCT_V02323_FORENSIC_SOURCE_SNAPSHOT_PASS",
    "ECOMSRE_PRODUCT_V02323_DIGEST_SEMANTICS_PASS",
    "ECOMSRE_PRODUCT_V02323_SCHEMA9_CONTAMINATION_AUDIT_PASS",
    "ECOMSRE_PRODUCT_V02323_RECONSTRUCTION_DISPOSITION_FROZEN",
    "ECOMSRE_PRODUCT_V02323_REPLAY_INPUT_PASS",
    "ECOMSRE_PRODUCT_V02323_ROOT_CAUSE_DISPOSITION_FROZEN",
]


def _load(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return payload


def _require_seal(payload: dict[str, Any], field: str) -> str:
    body = dict(payload)
    supplied = body.pop(field, None)
    if not isinstance(supplied, str) or supplied != semantic_sha256_v22(body):
        raise ValueError(f"Product v0.2.3.2.3 seal differs: {field}")
    return supplied


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _connect(database: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(
        f"{database.resolve(strict=True).as_uri()}?mode=ro&immutable=1",
        uri=True,
    )
    connection.row_factory = sqlite3.Row
    return connection


def _row(
    database: Path,
    sql: str,
    parameters: tuple[object, ...],
) -> sqlite3.Row:
    with _connect(database) as connection:
        row = connection.execute(sql, parameters).fetchone()
    if row is None:
        raise ValueError("required Product v0.2.3.2.3 row is absent")
    return row


def _job(database: Path, job_id: str) -> ProductJobRecordV1:
    row = _row(
        database,
        "SELECT * FROM diagnosis_jobs WHERE job_id = ?",
        (job_id,),
    )
    keys = set(row.keys())
    return ProductJobRecordV1(
        job_id=str(row["job_id"]),
        job_type=ProductJobTypeV1(str(row["job_type"])),
        status=ProductJobStatusV1(str(row["status"])),
        payload=json.loads(str(row["payload_json"])),
        result=(
            None if row["result_json"] is None else json.loads(str(row["result_json"]))
        ),
        safe_error_code=row["safe_error_code"],
        failure_stage=(row["failure_stage"] if "failure_stage" in keys else None),
        exception_fingerprint=(
            row["exception_fingerprint"] if "exception_fingerprint" in keys else None
        ),
        journal_tail_sha256=(
            row["journal_tail_sha256"] if "journal_tail_sha256" in keys else None
        ),
        idempotency_key=row["idempotency_key"],
        claimed_by=row["claimed_by"],
        lease_expires_at=row["lease_expires_at"],
        attempt_count=int(row["attempt_count"]),
        created_at=float(row["created_at"]),
        updated_at=float(row["updated_at"]),
    )


def _model_sha256(value: Any) -> str:
    return semantic_sha256_v22(value.model_dump(mode="json"))


def _counts(database: Path) -> dict[str, int]:
    tables = (
        "diagnosis_results",
        "diagnosis_evidence_indexes",
        "evidence_objects",
        "diagnosis_evidence_links",
        "diagnosis_jobs",
        "incidents",
        "baseline_versions",
    )
    with _connect(database) as connection:
        return {
            table: int(
                connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
            )
            for table in tables
        }


def _object_bytes(product_root: Path, object_sha256: str) -> bytes:
    path = product_root / "objects/sha256" / object_sha256[:2] / f"{object_sha256}.json"
    payload = path.read_bytes()
    if hashlib.sha256(payload).hexdigest() != object_sha256:
        raise ValueError("Product v0.2.3.2.3 persisted object digest differs")
    return payload


def _object_set(database: Path) -> set[str]:
    with _connect(database) as connection:
        return {
            str(row[0])
            for row in connection.execute(
                "SELECT object_sha256 FROM evidence_objects"
            ).fetchall()
        }


def _stage_events(
    database: Path,
    *,
    job_id: str,
    incident_id: str,
) -> tuple[DiagnosisStageEventV02322, ...]:
    with _connect(database) as connection:
        rows = connection.execute(
            "SELECT payload_json, event_sha256 "
            "FROM diagnosis_stage_events_v02322 WHERE job_id = ? ORDER BY ordinal",
            (job_id,),
        ).fetchall()
    events: list[DiagnosisStageEventV02322] = []
    previous = "0" * 64
    for ordinal, row in enumerate(rows, start=1):
        event = DiagnosisStageEventV02322.model_validate_json(str(row["payload_json"]))
        if (
            event.ordinal != ordinal
            or event.job_id != job_id
            or event.incident_id != incident_id
            or event.previous_event_sha256 != previous
            or event.event_sha256 != str(row["event_sha256"])
        ):
            raise ValueError("Product v0.2.3.2.3 replay journal chain differs")
        events.append(event)
        previous = event.event_sha256
    return tuple(events)


def _file_inventory(root: Path) -> dict[str, str]:
    if any(path.is_symlink() for path in (root, *root.rglob("*"))):
        raise ValueError("Product v0.2.3.2.3 support inventory contains a symlink")
    return {
        path.relative_to(root).as_posix(): _sha256_file(path)
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _stable_database_payload(database: Path) -> dict[str, object]:
    payload = _logical_database_payload(
        database,
        project_schema8=True,
        exclude_stage_journal=True,
    )
    return {
        table: rows for table, rows in payload.items() if table not in _MUTATED_TABLES
    }


def _table_rows(database: Path, table: str) -> tuple[dict[str, object], ...]:
    with _connect(database) as connection:
        columns = tuple(
            str(row[1])
            for row in connection.execute(f'PRAGMA table_info("{table}")').fetchall()
        )
        rows = connection.execute(f'SELECT * FROM "{table}"').fetchall()
    return tuple({column: row[column] for column in columns} for row in rows)


def _require_inherited_rows_unchanged(
    source_database: Path,
    replay_database: Path,
    *,
    table: str,
) -> None:
    def canonical(rows: tuple[dict[str, object], ...]) -> Counter[str]:
        return Counter(
            json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            for row in rows
        )

    source = canonical(_table_rows(source_database, table))
    replay = canonical(_table_rows(replay_database, table))
    if any(replay[row] < count for row, count in source.items()):
        raise ValueError(f"Product v0.2.3.2.3 inherited {table} rows differ")


def _require_existing_files_unchanged(
    source_root: Path,
    replay_root: Path,
) -> set[str]:
    source = _file_inventory(source_root)
    replay = _file_inventory(replay_root)
    if any(replay.get(relative) != sha256 for relative, sha256 in source.items()):
        raise ValueError("Product v0.2.3.2.3 inherited object bytes differ")
    return set(replay) - set(source)


def verify_product_v02323_increment4(
    root: Path,
    *,
    source_root: Path,
    pristine_root: Path,
    formal_private_root: Path,
) -> dict[str, object]:
    project = Path(root).resolve(strict=True)
    increment3 = verify_product_v02323_increment3(
        project,
        source_root=source_root,
        pristine_root=pristine_root,
        formal_private_root=formal_private_root,
        allow_later_phase_artifacts=True,
    )
    replay_input = FrozenIncidentReplayInputV02323.model_validate_json(
        (project / "config/product-v02323/replay/replay-input.json").read_text(
            encoding="utf-8"
        )
    )
    result_path = (
        project / "docs/analysis/product-v02323-diagnosis-pipeline-replay.json"
    )
    result = DiagnosisPipelineReplayResultV02323.model_validate_json(
        result_path.read_text(encoding="utf-8")
    )
    attempts = _load(
        project / "docs/analysis/product-v02323-diagnosis-persistence-attempts.json"
    )
    attempts_sha256 = _require_seal(attempts, "attempts_sha256")
    progress = _load(project / "docs/analysis/product-v02323-progress.json")
    progress_sha256 = _require_seal(progress, "progress_sha256")
    attempt_root = _private_path(
        project,
        f".local/product-v02323/diagnosis-persistence-replay/{result.replay_id}",
    )
    persistence_parent = attempt_root.parent
    product_root = attempt_root / "product"
    database = product_root / "product.sqlite3"
    if (
        {item.name for item in persistence_parent.iterdir()} != {result.replay_id}
        or not is_read_only_tree_v02323(attempt_root)
        or {item.name for item in attempt_root.iterdir()}
        != {"preflight.json", "product", "publication", "replay-result.json"}
        or {item.name for item in product_root.iterdir()}
        != {"objects", "pilot", "product.sqlite3"}
        or any(
            item.name.endswith(("-wal", "-shm", "-journal"))
            for item in attempt_root.rglob("*")
            if item.is_file()
        )
    ):
        raise ValueError("Product v0.2.3.2.3 persistence attempt boundary differs")

    private_result = DiagnosisPipelineReplayResultV02323.model_validate_json(
        (attempt_root / "replay-result.json").read_text(encoding="utf-8")
    )
    publication = attempt_root / "publication"
    publication_names = {
        "pre-publication-progress.json",
        "product-v02323-diagnosis-pipeline-replay.json",
        "product-v02323-diagnosis-persistence-attempts.json",
        "product-v02323-progress.json",
    }
    if (
        not publication.is_dir()
        or publication.is_symlink()
        or {item.name for item in publication.iterdir()} != publication_names
        or (publication / "product-v02323-diagnosis-pipeline-replay.json").read_bytes()
        != result_path.read_bytes()
        or (
            publication / "product-v02323-diagnosis-persistence-attempts.json"
        ).read_bytes()
        != (
            project / "docs/analysis/product-v02323-diagnosis-persistence-attempts.json"
        ).read_bytes()
        or (publication / "product-v02323-progress.json").read_bytes()
        != (project / "docs/analysis/product-v02323-progress.json").read_bytes()
    ):
        raise ValueError("Product v0.2.3.2.3 staged publication differs")
    progress_before = _load(publication / "pre-publication-progress.json")
    progress_before_sha256 = _require_seal(progress_before, "progress_sha256")
    preflight = _load(attempt_root / "preflight.json")
    _require_seal(preflight, "preflight_sha256")
    observed_at = datetime.fromisoformat(str(preflight.get("observed_at")))
    review_path = project / "docs/external-reviews/product-v02323-replay-review.md"
    if (
        private_result != result
        or preflight.get("schema_version")
        != "ecomsre.product.diagnosis-persistence-preflight.v02323"
        or preflight.get("terminal")
        != "ECOMSRE_PRODUCT_V02323_DIAGNOSIS_PERSISTENCE_PREFLIGHT_PASS"
        or preflight.get("replay_id") != result.replay_id
        or observed_at.tzinfo is None
        or observed_at.utcoffset() != timedelta(0)
        or preflight.get("replay_input_sha256") != replay_input.replay_input_sha256
        or preflight.get("forensics_sha256") != increment3["forensics_sha256"]
        or preflight.get("root_cause_disposition")
        != "ECOMSRE_PRODUCT_V02323_ORIGINAL_ROOT_CAUSE_UNPROVEN"
        or preflight.get("review_file_sha256") != _sha256_file(review_path)
        or preflight.get("review_file_sha256") != EXPECTED_INCREMENT3_REVIEW_SHA256
        or preflight.get("progress_sha256_before") != progress_before_sha256
        or preflight.get("diagnosis_persistence_replay_attempt_count_before") != 0
        or preflight.get("diagnosis_persistence_replay_attempt_limit") != 1
        or preflight.get("provider_agent_runbook_docker_calls") != 0
        or preflight.get("measured_nofault_authority") != "NONE"
        or preflight.get("knowledge_loop_authority") != "NONE"
    ):
        raise ValueError("Product v0.2.3.2.3 persistence preflight differs")

    expected_attempts = {
        "schema_version": "ecomsre.product.diagnosis-persistence-attempts.v02323",
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
        "attempts_sha256": attempts_sha256,
    }
    if attempts != expected_attempts:
        raise ValueError("Product v0.2.3.2.3 persistence attempts differ")

    reconstruction_payload = _load(
        project / "docs/analysis/product-v02323-schema8-reconstruction.json"
    )
    reconstruction = Schema8ReconstructionV02323.model_validate(
        reconstruction_payload["reconstruction"]
    )
    reconstructed_product = _private_path(
        project, reconstruction.reconstruction_locator
    )
    reconstructed_database = reconstructed_product / "product.sqlite3"
    base_migrations = _table_rows(reconstructed_database, "schema_migrations")
    replay_migrations = _table_rows(database, "schema_migrations")
    for inherited_table in (
        "diagnosis_results",
        "diagnosis_evidence_indexes",
        "diagnosis_evidence_links",
    ):
        _require_inherited_rows_unchanged(
            reconstructed_database,
            database,
            table=inherited_table,
        )
    if (
        _schema_inventory_payload(database)
        != _expected_schema9_inventory(reconstructed_product)
        or _file_inventory(product_root / "pilot")
        != _file_inventory(reconstructed_product / "pilot")
        or _stable_database_payload(database)
        != _stable_database_payload(reconstructed_database)
        or replay_migrations[: len(base_migrations)] != base_migrations
        or len(replay_migrations) != len(base_migrations) + 1
        or replay_migrations[-1].get("version") != 9
        or replay_migrations[-1].get("name") != "product-v02322-diagnosis-stage-journal"
    ):
        raise ValueError("Product v0.2.3.2.3 replay clone structure differs")

    before = _counts(reconstructed_database)
    after = _counts(database)
    expected_count_bindings = {
        "diagnosis_results": (
            result.diagnosis_count_before,
            result.diagnosis_count_after,
        ),
        "diagnosis_evidence_indexes": (
            result.evidence_index_count_before,
            result.evidence_index_count_after,
        ),
        "evidence_objects": (
            result.evidence_object_count_before,
            result.evidence_object_count_after,
        ),
        "diagnosis_evidence_links": (
            result.evidence_link_count_before,
            result.evidence_link_count_after,
        ),
        "diagnosis_jobs": (result.job_count_before, result.job_count_after),
    }
    if (
        any(
            before[table] != expected_before or after[table] != expected_after
            for table, (
                expected_before,
                expected_after,
            ) in expected_count_bindings.items()
        )
        or after["incidents"] != before["incidents"]
        or after["baseline_versions"] != before["baseline_versions"]
    ):
        raise ValueError("Product v0.2.3.2.3 persistence row delta differs")

    original_before = _job(reconstructed_database, ORIGINAL_FAILED_JOB_ID)
    original_after = _job(database, ORIGINAL_FAILED_JOB_ID)
    recovery = _job(database, result.recovery_job_id)
    with _connect(reconstructed_database) as connection:
        base_job_ids = tuple(
            str(row[0])
            for row in connection.execute(
                "SELECT job_id FROM diagnosis_jobs ORDER BY job_id"
            ).fetchall()
        )
    with _connect(database) as connection:
        replay_job_ids = tuple(
            str(row[0])
            for row in connection.execute(
                "SELECT job_id FROM diagnosis_jobs ORDER BY job_id"
            ).fetchall()
        )
    inherited_jobs_unchanged = all(
        _job(reconstructed_database, job_id) == _job(database, job_id)
        for job_id in base_job_ids
    )
    base_job_events = {
        str(row["event_id"]): row
        for row in _table_rows(reconstructed_database, "job_events")
    }
    replay_job_events = {
        str(row["event_id"]): row for row in _table_rows(database, "job_events")
    }
    new_job_events = tuple(
        row
        for event_id, row in replay_job_events.items()
        if event_id not in base_job_events
    )
    expected_job_event_details = {
        "ENQUEUED": {"job_type": "DIAGNOSIS"},
        "CLAIMED": {
            "worker_id": f"product-v02323-{result.replay_id.removeprefix('replay-')}"
        },
        "SUCCEEDED": {},
    }
    expected_recovery_payload = {
        "incident_id": FORMAL_INCIDENT_ID,
        "replay_id": result.replay_id,
        "replay_of_job_id": ORIGINAL_FAILED_JOB_ID,
        "replay_input_sha256": replay_input.replay_input_sha256,
        "reconstruction_disposition": "PRISTINE_BASE_DELTA_RECONSTRUCTION",
        "replay_classification": "STRUCTURAL_CONTRACT_REPLAY",
    }
    if (
        result.formal_incident_id != FORMAL_INCIDENT_ID
        or result.original_failed_job_id != ORIGINAL_FAILED_JOB_ID
        or set(replay_job_ids) != {*base_job_ids, result.recovery_job_id}
        or not inherited_jobs_unchanged
        or any(
            replay_job_events.get(event_id) != row
            for event_id, row in base_job_events.items()
        )
        or len(new_job_events) != 3
        or {str(row["event_type"]) for row in new_job_events}
        != set(expected_job_event_details)
        or any(
            row["job_id"] != result.recovery_job_id
            or json.loads(str(row["details_json"]))
            != expected_job_event_details[str(row["event_type"])]
            for row in new_job_events
        )
        or original_before != original_after
        or original_after.status.value != "FAILED"
        or original_after.safe_error_code != "INTERNAL_CONTRACT_FAILURE"
        or original_after.result is not None
        or _model_sha256(original_before) != result.original_failed_job_sha256_before
        or _model_sha256(original_after) != result.original_failed_job_sha256_after
        or recovery.job_type.value != "DIAGNOSIS"
        or recovery.status.value != "SUCCEEDED"
        or recovery.payload != expected_recovery_payload
        or recovery.idempotency_key != f"product-v02323:{result.replay_id}"
        or recovery.attempt_count != 1
        or recovery.safe_error_code is not None
        or recovery.failure_stage is not None
        or recovery.exception_fingerprint is not None
        or _model_sha256(recovery) != result.recovery_job_sha256
    ):
        raise ValueError("Product v0.2.3.2.3 recovery job differs")

    diagnosis_row = _row(
        database,
        "SELECT payload_json FROM diagnosis_results WHERE incident_id = ?",
        (FORMAL_INCIDENT_ID,),
    )
    diagnosis = DiagnosisResultV1.model_validate_json(str(diagnosis_row[0]))
    index_row = _row(
        database,
        "SELECT payload_json FROM diagnosis_evidence_indexes WHERE incident_id = ?",
        (FORMAL_INCIDENT_ID,),
    )
    index = DiagnosisEvidenceIndexV0232.model_validate_json(str(index_row[0]))
    with _connect(database) as connection:
        links = connection.execute(
            "SELECT object_sha256, evidence_ref, source, action_id, role "
            "FROM diagnosis_evidence_links WHERE diagnosis_id = ? ORDER BY evidence_ref",
            (diagnosis.diagnosis_id,),
        ).fetchall()
    if any(str(row["role"]) != "OBSERVATION" for row in links):
        raise ValueError("Product v0.2.3.2.3 persisted evidence role differs")
    objects = tuple(
        EvidenceObjectV1(
            evidence_ref=str(row["evidence_ref"]),
            source=EvidenceSourceV22(str(row["source"])),
            action_id=str(row["action_id"]),
            object_sha256=str(row["object_sha256"]),
            payload=json.loads(
                _object_bytes(product_root, str(row["object_sha256"])).decode("utf-8")
            ),
        )
        for row in links
    )
    bundle = EvidenceBundleV1(
        incident_id=FORMAL_INCIDENT_ID,
        diagnosis_id=diagnosis.diagnosis_id,
        objects=objects,
        supporting_evidence_refs=diagnosis.supporting_evidence_refs,
        contradicting_evidence_refs=diagnosis.contradicting_evidence_refs,
    )
    trace = DiagnosisDecisionTraceV0232.model_validate_json(
        _object_bytes(product_root, result.decision_trace_object_sha256)
    )
    base_objects = _object_set(reconstructed_database)
    replay_objects = _object_set(database)
    linked_objects = {str(row["object_sha256"]) for row in links}
    base_object_metadata = {
        str(row["object_sha256"]): row
        for row in _table_rows(reconstructed_database, "evidence_objects")
    }
    replay_object_metadata = {
        str(row["object_sha256"]): row
        for row in _table_rows(database, "evidence_objects")
    }
    extra_object_files = _require_existing_files_unchanged(
        reconstructed_product / "objects",
        product_root / "objects",
    )
    disk_objects = {
        path.stem
        for path in (product_root / "objects/sha256").glob("*/*.json")
        if path.is_file()
    }
    if (
        diagnosis.result_sha256 != result.diagnosis_result_sha256
        or diagnosis.terminal.value != "INSUFFICIENT_EVIDENCE"
        or recovery.result != diagnosis.model_dump(mode="json")
        or index.index_sha256 != result.evidence_index_sha256
        or index.diagnosis_id != diagnosis.diagnosis_id
        or semantic_sha256_v22(bundle.model_dump(mode="json"))
        != result.evidence_bundle_sha256
        or index.evidence_bundle_sha256 != result.evidence_bundle_sha256
        or trace.trace_sha256 != result.decision_trace_sha256
        or index.decision_trace_sha256 != trace.trace_sha256
        or trace.diagnosis_id != diagnosis.diagnosis_id
        or replay_objects - base_objects
        != linked_objects | {result.decision_trace_object_sha256}
        or len(replay_objects - base_objects) != 7
        or any(
            replay_object_metadata.get(object_sha256) != metadata
            for object_sha256, metadata in base_object_metadata.items()
        )
        or extra_object_files
        != {
            f"sha256/{object_sha256[:2]}/{object_sha256}.json"
            for object_sha256 in replay_objects - base_objects
        }
        or disk_objects != replay_objects
    ):
        raise ValueError("Product v0.2.3.2.3 persisted evidence differs")

    events = _stage_events(
        database,
        job_id=result.recovery_job_id,
        incident_id=FORMAL_INCIDENT_ID,
    )
    with _connect(database) as connection:
        total_event_count = int(
            connection.execute(
                "SELECT COUNT(*) FROM diagnosis_stage_events_v02322"
            ).fetchone()[0]
        )
    if (
        len(events) != 54
        or total_event_count != 54
        or tuple((event.stage.value, event.status.value) for event in events)
        != _EXPECTED_PERSISTENCE_SEQUENCE
        or events[-1].event_sha256 != result.journal_tail_sha256
    ):
        raise ValueError("Product v0.2.3.2.3 persistence journal differs")

    if (
        len(progress_before) != 48
        or progress_before.get("increment") != 3
        or progress_before.get("phase") != "ROOT_CAUSE_DISPOSITION_FROZEN"
        or progress_before.get("terminals") != _INCREMENT3_TERMINALS
        or progress_before.get("diagnosis_persistence_replay_attempt_count") != 0
        or progress_before.get("next_gate")
        != "INCREMENT_4_NO_DEFECT_PATH_AND_SINGLE_PERSISTENCE_REPLAY"
    ):
        raise ValueError("Product v0.2.3.2.3 pre-publication progress differs")
    expected_progress_body = {
        **{
            key: value
            for key, value in progress_before.items()
            if key
            not in {"progress_sha256", "increment", "phase", "terminals", "next_gate"}
        },
        "increment": 4,
        "phase": "DIAGNOSIS_REPLAY_COMPLETE",
        "terminals": [*_INCREMENT3_TERMINALS, DIAGNOSIS_REPLAY_PASS],
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
    expected_progress = {
        **expected_progress_body,
        "progress_sha256": semantic_sha256_v22(expected_progress_body),
    }
    if (
        progress != expected_progress
        or result.replay_input_sha256 != replay_input.replay_input_sha256
        or result.diagnosis_persistence_replay_attempt_count != 1
        or result.provider_agent_runbook_docker_calls != 0
        or result.measured_nofault_authority != "NONE"
        or result.knowledge_loop_authority != "NONE"
    ):
        raise ValueError("Product v0.2.3.2.3 Increment 4 progress differs")

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
        "stage_event_count": len(events),
        "stage_journal_terminal": events[-1].stage.value,
        "diagnosis_persistence_replay_attempt_count": 1,
        "provider_agent_runbook_docker_calls": 0,
        "measured_nofault_authority": "NONE",
        "knowledge_loop_authority": "NONE",
        "progress_sha256": progress_sha256,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--root", type=Path, default=Path(__file__).resolve().parents[2]
    )
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--pristine-root", type=Path, required=True)
    parser.add_argument("--formal-private-root", type=Path, required=True)
    arguments = parser.parse_args(argv)
    print(
        json.dumps(
            verify_product_v02323_increment4(
                arguments.root,
                source_root=arguments.source_root,
                pristine_root=arguments.pristine_root,
                formal_private_root=arguments.formal_private_root,
            ),
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ("verify_product_v02323_increment4",)
