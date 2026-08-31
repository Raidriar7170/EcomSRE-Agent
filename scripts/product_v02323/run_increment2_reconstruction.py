#!/usr/bin/env python3
"""Run Product v0.2.3.2.3 Increment 2 without Docker or Product runtime writes."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
from typing import Mapping, Sequence

from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22
from ecomsre.product.pilot.forensic_schema8_v02323 import (
    ForensicRawSourceSnapshotV02323,
    verify_forensic_source_immutability_v02323,
)
from ecomsre.product.pilot.schema8_reconstruction_v02323 import (
    GOAL_VERSION_V02323,
    PR83_HEAD_V02323,
    admit_pristine_base_v02323,
    audit_schema9_contamination_v02323,
    build_clean_schema8_database_v02323,
    build_formal_product_delta_v02323,
    export_schema8_projection_v02323,
    freeze_reconstruction_disposition_v02323,
    inspect_post_formal_state_v02323,
    load_schema8_definition_v02323,
    load_schema9_definition_v02323,
    verify_schema8_reconstruction_v02323,
)
from scripts.ci.verify_product_v02323_increment1 import (
    verify_product_v02323_increment1,
)
from scripts.product_v02323.run_increment1_forensics import (
    _git_bytes,
    _owner_count,
)


PRISTINE_LOCATOR = (
    ".local/product-v023/baseline-readiness/runs/"
    "20260829T150806-1eaee825/product"
)
FORMAL_INCIDENT_LOCATOR = ".local/product-v02321/formal/incident.json"
FORMAL_PENDING_JOB_LOCATOR = ".local/product-v02321/formal/diagnosis-job.json"
FORMAL_COMPLETION_LOCATOR = (
    ".local/product-v02321/formal/diagnosis-job-completion.json"
)


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _json_bytes(payload: object) -> bytes:
    return (
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        + "\n"
    ).encode("utf-8")


def _sealed(body: dict[str, object], field: str) -> dict[str, object]:
    return {**body, field: semantic_sha256_v22(body)}


def _write_public(path: Path, payload: bytes, *, replace: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and not replace:
        raise RuntimeError(f"create-once public artifact exists: {path}")
    temporary = path.with_name(f".{path.name}.v02323-increment2.tmp")
    if temporary.exists():
        raise RuntimeError(f"temporary public artifact exists: {temporary}")
    with temporary.open("xb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def _seal_tree(root: Path) -> None:
    for path in sorted(root.rglob("*"), key=lambda item: len(item.parts), reverse=True):
        os.chmod(path, path.stat().st_mode & ~0o222, follow_symlinks=False)
    os.chmod(root, root.stat().st_mode & ~0o222, follow_symlinks=False)


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write_attempt_envelope(attempt_root: Path, body: dict[str, object]) -> dict[str, object]:
    payload = _sealed(body, "attempt_sha256")
    status = str(body["status"]).lower().replace("_", "-")
    path = attempt_root / f"attempt-{status}.json"
    temporary = attempt_root / f".{path.name}.tmp"
    if path.exists() or temporary.exists():
        raise RuntimeError(f"create-once attempt envelope exists: {path}")
    published = False
    try:
        with temporary.open("xb") as handle:
            handle.write(_json_bytes(payload))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        published = True
        _fsync_directory(attempt_root)
        _seal_tree(attempt_root)
    except Exception:
        if not published:
            raise
        # Atomic publication is the commit point. If a transient directory-fsync
        # or sealing failure occurs afterwards, finish the seal and accept only
        # the exact, complete envelope that was published.
        _seal_tree(attempt_root)
        _fsync_directory(attempt_root)
        if json.loads(path.read_text(encoding="utf-8")) != payload:
            raise RuntimeError("published attempt envelope differs")
    return payload


def _attempt_summaries(reconstruction_root: Path) -> list[dict[str, object]]:
    summaries: list[dict[str, object]] = []
    payloads: dict[str, dict[str, object]] = {}
    for attempt_root in sorted(path for path in reconstruction_root.iterdir() if path.is_dir()):
        envelopes = sorted(attempt_root.glob("attempt-*.json"))
        if len(envelopes) != 1:
            raise RuntimeError(f"reconstruction attempt envelope differs: {attempt_root.name}")
        payload = json.loads(envelopes[0].read_text(encoding="utf-8"))
        body = dict(payload)
        supplied = body.pop("attempt_sha256", None)
        if supplied != semantic_sha256_v22(body) or payload.get("attempt_id") != attempt_root.name:
            raise RuntimeError(f"reconstruction attempt seal differs: {attempt_root.name}")
        status = payload.get("status")
        if (
            status not in {"FAILED_CLOSED", "SUPERSEDED_PASS", "PASS"}
            or payload.get("source_unchanged") is not True
            or payload.get("diagnosis_persistence_replay_attempt_count") != 0
            or payload.get("provider_agent_runbook_docker_calls") != 0
        ):
            raise RuntimeError(f"reconstruction attempt boundary differs: {attempt_root.name}")
        if status == "FAILED_CLOSED":
            if (
                payload.get("safe_error_code")
                != "BLOCKED_ECOMSRE_PRODUCT_V02323_RECONSTRUCTION"
                or not isinstance(payload.get("failure_stage"), str)
                or not payload["failure_stage"]
            ):
                raise RuntimeError(
                    f"failed reconstruction attempt fields differ: {attempt_root.name}"
                )
        else:
            for field in (
                "reconstruction_sha256",
                "source_projection_sha256",
                "reconstructed_projection_sha256",
            ):
                if re.fullmatch(r"[0-9a-f]{64}", str(payload.get(field))) is None:
                    raise RuntimeError(
                        f"successful reconstruction attempt fields differ: {attempt_root.name}"
                    )
            if status == "PASS" and payload.get("source_projection_sha256") != payload.get(
                "reconstructed_projection_sha256"
            ):
                raise RuntimeError(
                    f"PASS reconstruction projection differs: {attempt_root.name}"
                )
            if status == "SUPERSEDED_PASS" and not isinstance(
                payload.get("superseded_by_attempt_id"), str
            ):
                raise RuntimeError(
                    f"superseded reconstruction target differs: {attempt_root.name}"
                )
        if any(path.stat().st_mode & 0o222 for path in (attempt_root, *attempt_root.rglob("*"))):
            raise RuntimeError(f"reconstruction attempt is writable: {attempt_root.name}")
        payloads[attempt_root.name] = payload
        summaries.append(
            {
                "attempt_id": attempt_root.name,
                "status": payload["status"],
                "attempt_sha256": supplied,
            }
        )
    attempt_ids = set(payloads)
    for attempt_id, payload in payloads.items():
        successor_field = (
            "successor_attempt_id"
            if payload["status"] == "FAILED_CLOSED"
            else "superseded_by_attempt_id"
            if payload["status"] == "SUPERSEDED_PASS"
            else None
        )
        if successor_field is None or successor_field not in payload:
            continue
        successor = payload[successor_field]
        if (
            not isinstance(successor, str)
            or successor <= attempt_id
            or successor not in attempt_ids
            or (
                payload["status"] == "SUPERSEDED_PASS"
                and payloads[successor]["status"] != "PASS"
            )
        ):
            raise RuntimeError(f"reconstruction successor differs: {attempt_id}")
    if sum(item["status"] == "PASS" for item in summaries) != 1:
        raise RuntimeError("reconstruction PASS attempt count differs")
    if summaries[-1]["status"] != "PASS":
        raise RuntimeError("latest reconstruction attempt is not PASS")
    return summaries


def _private_path(root: Path, locator: str) -> Path:
    relative = Path(locator)
    if relative.is_absolute() or ".." in relative.parts or "." in relative.parts:
        raise RuntimeError("private locator differs")
    resolved = (root / relative).resolve(strict=True)
    if not resolved.is_relative_to(root.resolve(strict=True)):
        raise RuntimeError("private locator escapes project")
    return resolved


def _normalized(value: object) -> object:
    if isinstance(value, bytes):
        return {"base64": base64.b64encode(value).decode("ascii")}
    if value is None or isinstance(value, (str, int, float)):
        return value
    raise RuntimeError("unsupported SQLite value")


def _private_rows_payload(
    rows: Mapping[str, Sequence[Sequence[object]]],
) -> dict[str, object]:
    return {
        table: [[_normalized(value) for value in row] for row in table_rows]
        for table, table_rows in sorted(rows.items())
    }


def _validate_formal_artifacts(
    source_database: Path,
    *,
    incident_bytes: bytes,
    pending_job_bytes: bytes,
    completion_bytes: bytes,
) -> None:
    incident = json.loads(incident_bytes)
    pending = json.loads(pending_job_bytes)
    completion = json.loads(completion_bytes)
    uri = f"file:{source_database.resolve(strict=True).as_posix()}?mode=ro&immutable=1"
    connection = sqlite3.connect(uri, uri=True, isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only = ON")
    try:
        incident_rows = connection.execute(
            "SELECT payload_json, environment_id, external_incident_key, created_at "
            "FROM incidents WHERE incident_id = ?",
            ("inc-a5a8df708ab77c2f2e19da63",),
        ).fetchall()
        job_rows = connection.execute(
            "SELECT * FROM diagnosis_jobs WHERE job_id = ?",
            ("job-216dd1caac0b92270b1870a2",),
        ).fetchall()
    finally:
        connection.close()
    if len(incident_rows) != 1 or len(job_rows) != 1:
        raise RuntimeError("formal Incident or job differs")
    incident_row = incident_rows[0]
    if (
        json.loads(str(incident_row["payload_json"])) != incident
        or incident_row["environment_id"] != incident["environment_id"]
        or incident_row["external_incident_key"] != incident["external_incident_key"]
        or str(incident_row["created_at"]).replace("+00:00", "Z")
        != incident["created_at"]
    ):
        raise RuntimeError("formal Incident artifact binding differs")
    job_row = job_rows[0]
    projected = {
        "schema_version": "ecomsre.product.job.v1",
        "job_id": job_row["job_id"],
        "job_type": job_row["job_type"],
        "status": job_row["status"],
        "payload": json.loads(str(job_row["payload_json"])),
        "result": (
            None
            if job_row["result_json"] is None
            else json.loads(str(job_row["result_json"]))
        ),
        "safe_error_code": job_row["safe_error_code"],
        "idempotency_key": job_row["idempotency_key"],
        "claimed_by": job_row["claimed_by"],
        "lease_expires_at": job_row["lease_expires_at"],
        "attempt_count": job_row["attempt_count"],
        "created_at": job_row["created_at"],
        "updated_at": job_row["updated_at"],
    }
    if projected != completion:
        raise RuntimeError("formal completion artifact binding differs")
    if (
        pending.get("job_id") != completion.get("job_id")
        or pending.get("job_type") != completion.get("job_type")
        or pending.get("payload") != completion.get("payload")
        or pending.get("status") != "PENDING"
        or pending.get("attempt_count") != 0
        or pending.get("safe_error_code") is not None
    ):
        raise RuntimeError("formal pending job artifact binding differs")


def run_increment2(
    root: Path,
    *,
    source_root: Path,
    pristine_root: Path,
    formal_private_root: Path,
    reconstruction_id: str,
    replace_public_artifacts: bool = False,
) -> dict[str, object]:
    project = Path(root).resolve(strict=True)
    if re.fullmatch(r"[0-9]{8}T[0-9]{6}Z", reconstruction_id) is None:
        raise RuntimeError("reconstruction ID differs")
    verify_product_v02323_increment1(
        project,
        source_root=source_root,
        private_root=project,
        allow_later_phase_artifacts=replace_public_artifacts,
    )
    predecessor_audit = json.loads(
        (project / "docs/analysis/product-v02323-predecessor-audit.json").read_text(
            encoding="utf-8"
        )
    )
    digest_audit = json.loads(
        (project / "docs/analysis/product-v02323-digest-semantics.json").read_text(
            encoding="utf-8"
        )
    )
    snapshot = ForensicRawSourceSnapshotV02323.model_validate_json(
        (project / "docs/analysis/product-v02323-forensic-source-snapshot.json").read_text(
            encoding="utf-8"
        )
    )
    snapshot_root = _private_path(project, snapshot.snapshot_locator)
    snapshot_product = snapshot_root / "product"
    snapshot_database = (
        snapshot_root / "consistent-image" / "product.sqlite3"
        if snapshot.snapshot_consistent_database_present
        else snapshot_product / "product.sqlite3"
    )
    incident_path = _private_path(formal_private_root, FORMAL_INCIDENT_LOCATOR)
    pending_path = _private_path(formal_private_root, FORMAL_PENDING_JOB_LOCATOR)
    completion_path = _private_path(formal_private_root, FORMAL_COMPLETION_LOCATOR)
    incident_bytes = incident_path.read_bytes()
    pending_bytes = pending_path.read_bytes()
    completion_bytes = completion_path.read_bytes()
    _validate_formal_artifacts(
        snapshot_database,
        incident_bytes=incident_bytes,
        pending_job_bytes=pending_bytes,
        completion_bytes=completion_bytes,
    )

    migrations_source = _git_bytes(
        project, PR83_HEAD_V02323, "src/ecomsre/product/storage/migrations.py"
    )
    job_repository_source = _git_bytes(
        project, PR83_HEAD_V02323, "src/ecomsre/product/jobs/repository.py"
    )
    formal_runner_source = _git_bytes(
        project, PR83_HEAD_V02323, "scripts/product_v02321/run_formal_nofault.py"
    )
    artifact_bindings = {
        FORMAL_INCIDENT_LOCATOR: _sha256_bytes(incident_bytes),
        FORMAL_PENDING_JOB_LOCATOR: _sha256_bytes(pending_bytes),
        FORMAL_COMPLETION_LOCATOR: _sha256_bytes(completion_bytes),
        f"git:{PR83_HEAD_V02323}:src/ecomsre/product/jobs/repository.py": (
            _sha256_bytes(job_repository_source)
        ),
        f"git:{PR83_HEAD_V02323}:scripts/product_v02321/run_formal_nofault.py": (
            _sha256_bytes(formal_runner_source)
        ),
        f"git:{PR83_HEAD_V02323}:src/ecomsre/product/storage/migrations.py": (
            _sha256_bytes(migrations_source)
        ),
    }
    definition = load_schema8_definition_v02323(project)
    schema9_definition = load_schema9_definition_v02323(project, definition)
    admission = admit_pristine_base_v02323(
        pristine_root,
        source_locator=PRISTINE_LOCATOR,
    )
    _base_export, base_rows = export_schema8_projection_v02323(
        pristine_root / "product.sqlite3",
        definition,
        formal_artifact_bindings={},
        allow_missing_schema8_tables=True,
    )
    source_export, post_rows = export_schema8_projection_v02323(
        snapshot_database,
        definition,
        formal_artifact_bindings=artifact_bindings,
    )
    delta = build_formal_product_delta_v02323(
        definition,
        base_rows,
        post_rows,
        provenance_by_table={
            "incidents": (
                FORMAL_INCIDENT_LOCATOR,
                artifact_bindings[FORMAL_INCIDENT_LOCATOR],
                "formal Incident creation",
            ),
            "diagnosis_jobs": (
                FORMAL_COMPLETION_LOCATOR,
                artifact_bindings[FORMAL_COMPLETION_LOCATOR],
                "formal Diagnosis job final FAILED state",
            ),
            "job_events": (
                f"git:{PR83_HEAD_V02323}:src/ecomsre/product/jobs/repository.py",
                artifact_bindings[
                    f"git:{PR83_HEAD_V02323}:src/ecomsre/product/jobs/repository.py"
                ],
                "submit, claim, and fail lifecycle events",
            ),
            "product_metric_counters": (
                f"git:{PR83_HEAD_V02323}:scripts/product_v02321/run_formal_nofault.py",
                artifact_bindings[
                    f"git:{PR83_HEAD_V02323}:scripts/product_v02321/run_formal_nofault.py"
                ],
                "formal HTTP and job-lifecycle observations",
            ),
            "schema_migrations": (
                f"git:{PR83_HEAD_V02323}:src/ecomsre/product/storage/migrations.py",
                artifact_bindings[
                    f"git:{PR83_HEAD_V02323}:src/ecomsre/product/storage/migrations.py"
                ],
                "schema-8 migration admission",
            ),
        },
        require_goal_delta=True,
    )
    reconstruction_locator = (
        f".local/product-v02323/reconstruction/{reconstruction_id}/product"
    )
    destination = project / reconstruction_locator
    attempt_root = destination.parent
    failure_stage = "SCHEMA8_CLEAN_BUILD"
    try:
        reconstruction = build_clean_schema8_database_v02323(
            destination,
            reconstruction_locator=reconstruction_locator,
            definition=definition,
            formal_delta=delta,
            source_projection=source_export,
            post_rows=post_rows,
            asset_source_product_root=snapshot_product,
        )
        failure_stage = "RECONSTRUCTION_VERIFY"
        reconstruction_verification_sha256 = verify_schema8_reconstruction_v02323(
            destination,
            definition=definition,
            projection=source_export,
            reconstruction=reconstruction,
        )
        failure_stage = "POST_FORMAL_STATE"
        post_formal_state = inspect_post_formal_state_v02323(destination)
        failure_stage = "SOURCE_IMMUTABILITY"
        final_immutability = verify_forensic_source_immutability_v02323(
            source_root,
            snapshot,
            owner_counter=_owner_count,
        )
        failure_stage = "CONTAMINATION_AUDIT"
        contamination = audit_schema9_contamination_v02323(
            snapshot_product,
            definition,
            schema9_definition,
            source_export,
            reconstructed_projection_sha256=(
                reconstruction.reconstructed_projection_sha256
            ),
            formal_artifact_bindings=artifact_bindings,
            source_immutability_proof_sha256=final_immutability.proof_sha256,
        )
        failure_stage = "DISPOSITION_FREEZE"
        disposition = freeze_reconstruction_disposition_v02323(
            admission=admission,
            delta=delta,
            projection=source_export,
            reconstruction=reconstruction,
            post_formal_state=post_formal_state,
            contamination=contamination,
        )

        failure_stage = "PRIVATE_ROW_EXPORT"
        private_rows_body: dict[str, object] = {
            "schema_version": "ecomsre.product.private-schema8-row-exports.v02323",
            "base_rows": _private_rows_payload(base_rows),
            "post_formal_rows": _private_rows_payload(post_rows),
        }
        private_rows_payload = _sealed(private_rows_body, "row_exports_sha256")
        private_rows_path = attempt_root / "schema8-row-exports.json"
        with private_rows_path.open("xb") as handle:
            handle.write(_json_bytes(private_rows_payload))
            handle.flush()
            os.fsync(handle.fileno())
        failure_stage = "ATTEMPT_PASS_SEAL"
        success_attempt = _write_attempt_envelope(
            attempt_root,
            {
                "schema_version": "ecomsre.product.reconstruction-attempt.v02323",
                "attempt_id": reconstruction_id,
                "status": "PASS",
                "reconstruction_sha256": reconstruction.reconstruction_sha256,
                "source_projection_sha256": source_export.overall_projection_sha256,
                "reconstructed_projection_sha256": (
                    reconstruction.reconstructed_projection_sha256
                ),
                "source_unchanged": True,
                "diagnosis_persistence_replay_attempt_count": 0,
                "provider_agent_runbook_docker_calls": 0,
            },
        )
    except Exception as error:
        source_unchanged = False
        source_immutability_proof_sha256: str | None = None
        source_immutability_error_type: str | None = None
        try:
            failure_immutability = verify_forensic_source_immutability_v02323(
                source_root,
                snapshot,
                owner_counter=_owner_count,
            )
            source_unchanged = True
            source_immutability_proof_sha256 = failure_immutability.proof_sha256
        except Exception as immutability_error:
            source_immutability_error_type = type(immutability_error).__name__
        attempt_root.mkdir(parents=True, exist_ok=True)
        envelopes = tuple(attempt_root.glob("attempt-*.json"))
        if not envelopes:
            os.chmod(attempt_root, attempt_root.stat().st_mode | 0o200)
            failure_body: dict[str, object] = {
                "schema_version": "ecomsre.product.reconstruction-attempt.v02323",
                "attempt_id": reconstruction_id,
                "status": "FAILED_CLOSED",
                "failure_stage": failure_stage,
                "safe_error_code": "BLOCKED_ECOMSRE_PRODUCT_V02323_RECONSTRUCTION",
                "error_type": type(error).__name__,
                "source_projection_sha256": source_export.overall_projection_sha256,
                "source_unchanged": source_unchanged,
                "source_immutability_proof_sha256": source_immutability_proof_sha256,
                "source_immutability_error_type": source_immutability_error_type,
                "diagnosis_persistence_replay_attempt_count": 0,
                "provider_agent_runbook_docker_calls": 0,
            }
            _write_attempt_envelope(attempt_root, failure_body)
        else:
            pass_commit_recovered = False
            _seal_tree(attempt_root)
            if (
                failure_stage == "ATTEMPT_PASS_SEAL"
                and len(envelopes) == 1
                and source_unchanged
            ):
                expected_success_body: dict[str, object] = {
                    "schema_version": "ecomsre.product.reconstruction-attempt.v02323",
                    "attempt_id": reconstruction_id,
                    "status": "PASS",
                    "reconstruction_sha256": reconstruction.reconstruction_sha256,
                    "source_projection_sha256": source_export.overall_projection_sha256,
                    "reconstructed_projection_sha256": (
                        reconstruction.reconstructed_projection_sha256
                    ),
                    "source_unchanged": True,
                    "diagnosis_persistence_replay_attempt_count": 0,
                    "provider_agent_runbook_docker_calls": 0,
                }
                expected_success = _sealed(expected_success_body, "attempt_sha256")
                if json.loads(envelopes[0].read_text(encoding="utf-8")) == expected_success:
                    success_attempt = expected_success
                    final_immutability = failure_immutability
                    pass_commit_recovered = True
            if not pass_commit_recovered:
                raise
        if not envelopes:
            raise
    attempt_summaries = _attempt_summaries(attempt_root.parent)

    reconstruction_body: dict[str, object] = {
        "schema_version": "ecomsre.product.schema8-reconstruction-evidence.v02323",
        "goal_version": GOAL_VERSION_V02323,
        "terminal": "ECOMSRE_PRODUCT_V02323_PRISTINE_BASE_DELTA_RECONSTRUCTION_PASS",
        "pristine_base_admission": admission.model_dump(mode="json"),
        "reconstruction": reconstruction.model_dump(mode="json"),
        "post_formal_state": post_formal_state.model_dump(mode="json"),
        "reconstruction_verification_sha256": reconstruction_verification_sha256,
        "private_row_exports_sha256": private_rows_payload["row_exports_sha256"],
        "successful_attempt_sha256": success_attempt["attempt_sha256"],
        "reconstruction_attempt_count": len(attempt_summaries),
        "reconstruction_attempts": attempt_summaries,
    }
    reconstruction_evidence = _sealed(
        reconstruction_body, "reconstruction_evidence_sha256"
    )
    progress_body: dict[str, object] = {
        "schema_version": "ecomsre.product.progress.v02323",
        "goal_version": GOAL_VERSION_V02323,
        "increment": 2,
        "phase": "SCHEMA8_RECONSTRUCTION_FROZEN",
        "terminals": [
            "ECOMSRE_PRODUCT_V02323_HISTORY_AND_BLOCKER_PASS",
            "ECOMSRE_PRODUCT_V02323_FORENSIC_SOURCE_SNAPSHOT_PASS",
            "ECOMSRE_PRODUCT_V02323_DIGEST_SEMANTICS_PASS",
            "ECOMSRE_PRODUCT_V02323_SCHEMA9_CONTAMINATION_AUDIT_PASS",
            "ECOMSRE_PRODUCT_V02323_RECONSTRUCTION_DISPOSITION_FROZEN",
        ],
        "history_audit_sha256": predecessor_audit["audit_sha256"],
        "forensic_source_snapshot_sha256": snapshot.snapshot_sha256,
        "source_immutability_proof_sha256": final_immutability.proof_sha256,
        "digest_semantics_audit_sha256": digest_audit["audit_sha256"],
        "schema8_definition_sha256": definition.schema8_definition_sha256,
        "schema9_contamination_audit_sha256": contamination.audit_sha256,
        "formal_delta_sha256": delta.delta_sha256,
        "schema8_projection_export_sha256": source_export.export_sha256,
        "reconstruction_evidence_sha256": reconstruction_evidence[
            "reconstruction_evidence_sha256"
        ],
        "reconstruction_disposition_sha256": disposition.disposition_sha256,
        "source_schema_version": 9,
        "reconstructed_schema_version": 8,
        "source_database_file_sha256": snapshot.source_database_file_sha256,
        "reconstructed_database_file_sha256": (
            reconstruction.reconstructed_database_file_sha256
        ),
        "reconstructed_database_logical_sha256": (
            reconstruction.reconstructed_database_logical_sha256
        ),
        "reconstructed_product_state_semantic_sha256": (
            reconstruction.reconstructed_product_state_semantic_sha256
        ),
        "reconstruction_disposition": "PRISTINE_BASE_DELTA_RECONSTRUCTION",
        "reconstruction_attempt_count": len(attempt_summaries),
        "historical_raw_byte_authority": "LOST_RAW_BYTES_NOT_RECONSTRUCTED",
        "historical_logical_authority": "PRISTINE_BASE_DELTA_RECONSTRUCTION",
        "fault_attempt_count": 0,
        "new_baseline_attempt_count": 0,
        "new_business_traffic_execution_count": 0,
        "new_product_incident_count": 0,
        "diagnosis_persistence_replay_attempt_count": 0,
        "provider_calls": 0,
        "agent_writes": 0,
        "runbook_executions": 0,
        "docker_calls": 0,
        "action_authority": "NONE",
        "measured_nofault_authority": "NONE",
        "knowledge_loop_authority": "NONE",
        "next_gate": "INCREMENT_3_REPLAY_INPUT_AND_ROOT_CAUSE_DISPOSITION",
    }
    progress = _sealed(progress_body, "progress_sha256")

    public_payloads = {
        "config/product-v02323/schema8-definition.json": _json_bytes(
            definition.model_dump(mode="json")
        ),
        "docs/analysis/product-v02323-schema9-contamination-audit.json": _json_bytes(
            contamination.model_dump(mode="json")
        ),
        "docs/analysis/product-v02323-formal-delta.json": _json_bytes(
            delta.model_dump(mode="json")
        ),
        "docs/analysis/product-v02323-schema8-projection.json": _json_bytes(
            source_export.model_dump(mode="json")
        ),
        "docs/analysis/product-v02323-schema8-reconstruction.json": _json_bytes(
            reconstruction_evidence
        ),
        "docs/analysis/product-v02323-reconstruction-disposition.json": _json_bytes(
            disposition.model_dump(mode="json")
        ),
        "docs/analysis/product-v02323-schema9-contamination-audit.md": (
            "# Product v0.2.3.2.3 schema-9 contamination audit\n\n"
            f"- Terminal: `{contamination.terminal}`\n"
            f"- Classification: `{contamination.contamination_class.value}`\n"
            f"- Schema-8 projection SHA-256: `{source_export.overall_projection_sha256}`\n"
            f"- Audit SHA-256: `{contamination.audit_sha256}`\n"
            "- Migration 9 is additive schema-only on the frozen source: the three "
            "new job columns are null and the stage journal has zero rows.\n"
            "- The schema-8 projection exactly matches the pristine-base plus formal-delta reconstruction.\n"
        ).encode("utf-8"),
        "docs/analysis/product-v02323-reconstruction-disposition.md": (
            "# Product v0.2.3.2.3 reconstruction disposition\n\n"
            f"- Terminal: `{disposition.terminal}`\n"
            f"- Reconstruction terminal: `{disposition.reconstruction_terminal}`\n"
            f"- Disposition: `{disposition.disposition}`\n"
            f"- Logical database SHA-256: `{reconstruction.reconstructed_database_logical_sha256}`\n"
            f"- Raw database SHA-256: `{reconstruction.reconstructed_database_file_sha256}`\n"
            f"- Disposition SHA-256: `{disposition.disposition_sha256}`\n\n"
            "The pristine pre-formal source admitted exactly and its complete formal delta "
            "reproduces the surviving schema-8 projection. This is the strongest historical "
            "logical reconstruction. The original raw schema-8 SQLite bytes remain lost; no "
            "raw-byte equality or measured No-Fault claim is made. Diagnosis replay has not run.\n"
        ).encode("utf-8"),
        "docs/analysis/product-v02323-progress.json": _json_bytes(progress),
    }
    for relative, payload in public_payloads.items():
        _write_public(
            project / relative,
            payload,
            replace=(
                replace_public_artifacts
                or relative == "docs/analysis/product-v02323-progress.json"
            ),
        )
    return {
        "schema8_definition_sha256": definition.schema8_definition_sha256,
        "schema9_contamination_audit_sha256": contamination.audit_sha256,
        "formal_delta_sha256": delta.delta_sha256,
        "schema8_projection_export_sha256": source_export.export_sha256,
        "reconstruction_sha256": reconstruction.reconstruction_sha256,
        "reconstruction_verification_sha256": reconstruction_verification_sha256,
        "reconstruction_disposition_sha256": disposition.disposition_sha256,
        "progress_sha256": progress["progress_sha256"],
        "contamination_class": contamination.contamination_class.value,
        "reconstruction_disposition": disposition.disposition,
        "reconstructed_database_file_sha256": (
            reconstruction.reconstructed_database_file_sha256
        ),
        "reconstructed_database_logical_sha256": (
            reconstruction.reconstructed_database_logical_sha256
        ),
        "reconstructed_product_state_semantic_sha256": (
            reconstruction.reconstructed_product_state_semantic_sha256
        ),
        "source_immutability_proof_sha256": final_immutability.proof_sha256,
        "diagnosis_persistence_replay_attempt_count": 0,
        "provider_agent_runbook_docker_calls": 0,
        "measured_nofault_authority": "NONE",
        "knowledge_loop_authority": "NONE",
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--pristine-root", type=Path, required=True)
    parser.add_argument("--formal-private-root", type=Path, required=True)
    parser.add_argument("--reconstruction-id", required=True)
    parser.add_argument("--replace-public-artifacts", action="store_true")
    arguments = parser.parse_args(argv)
    result = run_increment2(
        arguments.root,
        source_root=arguments.source_root,
        pristine_root=arguments.pristine_root,
        formal_private_root=arguments.formal_private_root,
        reconstruction_id=arguments.reconstruction_id,
        replace_public_artifacts=arguments.replace_public_artifacts,
    )
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
