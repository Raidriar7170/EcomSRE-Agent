#!/usr/bin/env python3
"""Verify Product v0.2.3.2.3 Increment 3 replay and no-write evidence."""

from __future__ import annotations

import argparse
import base64
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import tempfile
from typing import Any, Sequence

from pydantic import TypeAdapter

from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22
from ecomsre.product.incidents.contracts import EvidenceBundleV1, EvidenceObjectV1
from ecomsre.product.incidents.diagnosis_bridge import ProductDiagnosisBridgeV1
from ecomsre.product.incidents.diagnosis_pipeline_v02322 import (
    DiagnosisBridgeArtifactV02322,
    DiagnosisPersistencePlanV02322,
    DiagnosisPrivateFailureEnvelopeV02322,
)
from ecomsre.product.incidents.evidence_binding_v0232 import (
    DiagnosisEvidenceIndexV0232,
)
from ecomsre.product.incidents.diagnosis_stage_journal_v02322 import (
    DiagnosisStageEventV02322,
)
from ecomsre.product.incidents.read_backend import ProductReadAcquisitionV1
from ecomsre.product.incidents.repository import (
    _build_limitation_bindings,
    _source_disposition,
    _specialized_binding_refs,
)
from ecomsre.product.pilot.forensic_schema8_v02323 import (
    ForensicRawSourceSnapshotV02323,
    ForensicSqliteReaderV02323,
)
from ecomsre.product.pilot.diagnosis_replay_v02323 import (
    DiagnosisForensicsEvidenceV02323,
    DiagnosisRootCauseDispositionV02323,
    FrozenIncidentReplayInputV02323,
    ReplayCloneEvidenceV02323,
    build_structural_acquisition_v02323,
    clone_and_apply_migration9_v02323,
    is_read_only_tree_v02323,
)
from ecomsre.product.pilot.schema8_reconstruction_v02323 import (
    GOAL_VERSION_V02323,
    Schema8ReconstructionV02323,
    _runtime_inventory_sha256,
    _schema_inventory,
)
from scripts.ci.verify_product_v02323_increment2 import (
    verify_product_v02323_increment2,
)
from scripts.product_v02323.run_increment2_reconstruction import _private_path
from scripts.product_v02323.run_increment3_diagnosis_forensics import (
    _acquisition_payload,
    _load_context,
    _make_tree_owner_writable,
)
from ecomsre.product.storage.sqlite_store import SqliteStoreV1


ORIGINAL_FAILED_JOB_ID = "job-216dd1caac0b92270b1870a2"
FORMAL_INCIDENT_ID = "inc-a5a8df708ab77c2f2e19da63"
_NEW_JOB_COLUMNS = {
    "exception_fingerprint",
    "failure_stage",
    "journal_tail_sha256",
}
_EXACT_ACQUISITION_KEYS = {
    "capability_limitation_candidates_v0232",
    "capability_observations",
    "capability_observations_v0232",
    "connector_result",
    "connector_result_payloads",
    "limitation_candidates",
    "memory_outcomes",
    "p01_binding",
    "raw_outcomes",
    "raw_read_outcomes",
    "runtime_snapshot_binding",
    "source_query_windows",
}
_COMMON_FORENSIC_STAGES = (
    "JOB_CLAIMED",
    "INCIDENT_LOAD_STARTED",
    "INCIDENT_LOADED",
    "BASELINE_BINDING_STARTED",
    "BASELINE_BOUND",
    "SERVICE_IDENTITY_BINDING_STARTED",
    "SERVICE_IDENTITY_BOUND",
    "CAPABILITY_BINDING_STARTED",
    "CAPABILITY_BOUND",
    "ENVIRONMENT_LOAD_STARTED",
    "ENVIRONMENT_LOADED",
    "READ_ACQUISITION_STARTED",
    "READ_ACQUISITION_COMPLETED",
)
_SUCCESS_FORENSIC_STAGES = (
    "BRIDGE_DIAGNOSIS_STARTED",
    "BRIDGE_DIAGNOSIS_COMPLETED",
    "EVIDENCE_PREPARE_STARTED",
    "EVIDENCE_OBJECTS_PREPARED",
    "LIMITATION_BINDING_STARTED",
    "LIMITATION_BINDING_COMPLETED",
    "EVIDENCE_INDEX_STARTED",
    "EVIDENCE_INDEX_VALIDATED",
    "OBJECT_STORE_PREPARE_STARTED",
    "OBJECT_STORE_PREPARED",
    "SQL_TRANSACTION_STARTED",
)
_EXPECTED_FAILED_SEQUENCE = tuple(
    (stage, status)
    for stage in _COMMON_FORENSIC_STAGES
    for status in ("STARTED", "PASSED")
) + (("BRIDGE_DIAGNOSIS_STARTED", "STARTED"), ("FAILED", "FAILED"))
_EXPECTED_SUCCESS_SEQUENCE = tuple(
    (stage, status)
    for stage in (*_COMMON_FORENSIC_STAGES, *_SUCCESS_FORENSIC_STAGES)
    for status in ("STARTED", "PASSED")
)


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


def _connect_read_only(database: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(
        f"{database.resolve(strict=True).as_uri()}?mode=ro&immutable=1",
        uri=True,
    )
    connection.row_factory = sqlite3.Row
    return connection


def _normalized(value: object) -> object:
    if isinstance(value, bytes):
        return {"base64": base64.b64encode(value).decode("ascii")}
    if value is None or isinstance(value, (str, int, float)):
        return value
    raise ValueError("unsupported SQLite value")


def _logical_database_payload(
    database: Path,
    *,
    project_schema8: bool,
    exclude_stage_journal: bool,
) -> dict[str, object]:
    with _connect_read_only(database) as connection:
        tables = tuple(
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' "
                "AND name NOT LIKE 'sqlite_%' ORDER BY name"
            ).fetchall()
        )
        payload: dict[str, object] = {}
        for table in tables:
            if exclude_stage_journal and table == "diagnosis_stage_events_v02322":
                continue
            if project_schema8 and table == "diagnosis_stage_events_v02322":
                continue
            columns = [
                str(row[1])
                for row in connection.execute(
                    f'PRAGMA table_info("{table}")'
                ).fetchall()
            ]
            if project_schema8 and table == "diagnosis_jobs":
                columns = [item for item in columns if item not in _NEW_JOB_COLUMNS]
            selected = ",".join(f'"{item}"' for item in columns)
            rows = [
                [_normalized(value) for value in row]
                for row in connection.execute(
                    f'SELECT {selected} FROM "{table}"'
                ).fetchall()
            ]
            if project_schema8 and table == "schema_migrations":
                version_index = columns.index("version")

                def is_schema8_row(row: list[object]) -> bool:
                    version = row[version_index]
                    if not isinstance(version, int):
                        raise ValueError("schema migration version differs")
                    return version <= 8

                rows = [row for row in rows if is_schema8_row(row)]
            payload[table] = {
                "columns": columns,
                "rows": sorted(
                    rows,
                    key=lambda row: json.dumps(
                        row,
                        sort_keys=True,
                        separators=(",", ":"),
                        ensure_ascii=False,
                    ),
                ),
            }
    return payload


def _schema_versions(database: Path) -> tuple[int, ...]:
    with _connect_read_only(database) as connection:
        return tuple(
            int(row[0])
            for row in connection.execute(
                "SELECT version FROM schema_migrations ORDER BY version"
            ).fetchall()
        )


def _schema_inventory_payload(database: Path) -> tuple[dict[str, Any], ...]:
    with _connect_read_only(database) as connection:
        return _schema_inventory(connection)


def _expected_schema9_inventory(
    reconstructed_product: Path,
) -> tuple[dict[str, Any], ...]:
    with tempfile.TemporaryDirectory(prefix="product-v02323-schema9-") as temporary:
        destination = Path(temporary) / "product"
        clone_and_apply_migration9_v02323(
            reconstructed_product,
            destination,
            applied_at=datetime(2000, 1, 1, tzinfo=UTC),
        )
        return _schema_inventory_payload(destination / "product.sqlite3")


def _support_file_inventory(product_root: Path) -> tuple[dict[str, object], ...]:
    entries: list[dict[str, object]] = []
    for support_root in (product_root / "objects", product_root / "pilot"):
        if not support_root.is_dir() or support_root.is_symlink():
            raise ValueError("Product v0.2.3.2.3 support directory differs")
        for path in sorted(support_root.rglob("*")):
            if path.is_symlink() or (not path.is_file() and not path.is_dir()):
                raise ValueError("Product v0.2.3.2.3 support entry differs")
            if path.is_file():
                entries.append(
                    {
                        "path": str(path.relative_to(product_root)),
                        "size_bytes": path.stat().st_size,
                        "sha256": _sha256_file(path),
                    }
                )
    return tuple(entries)


def _verify_product_support_inventory(
    product_root: Path,
    *,
    reconstruction: Schema8ReconstructionV02323,
    expected_files: tuple[dict[str, object], ...],
    allowed_top_level: set[str],
) -> None:
    if {item.name for item in product_root.iterdir()} != allowed_top_level:
        raise ValueError("Product v0.2.3.2.3 product-root inventory differs")
    database = product_root / "product.sqlite3"
    reader = ForensicSqliteReaderV02323(database)
    if (
        _support_file_inventory(product_root) != expected_files
        or reader.object_inventory_sha256(product_root)
        != reconstruction.reconstructed_object_inventory_sha256
        or _runtime_inventory_sha256(product_root)
        != reconstruction.reconstructed_runtime_file_inventory_sha256
    ):
        raise ValueError("Product v0.2.3.2.3 Product support inventory differs")


def _row(database: Path, sql: str, parameters: tuple[object, ...]) -> dict[str, object]:
    with _connect_read_only(database) as connection:
        row = connection.execute(sql, parameters).fetchone()
    if row is None:
        raise ValueError("required Product row is absent")
    return {key: _normalized(row[key]) for key in row.keys()}


def _counts(database: Path) -> dict[str, int]:
    with _connect_read_only(database) as connection:
        return {
            table: int(
                connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
            )
            for table in (
                "diagnosis_results",
                "diagnosis_evidence_indexes",
                "evidence_objects",
            )
        }


def _stage_events(database: Path) -> tuple[DiagnosisStageEventV02322, ...]:
    with _connect_read_only(database) as connection:
        rows = connection.execute(
            "SELECT payload_json, event_sha256 FROM diagnosis_stage_events_v02322 "
            "WHERE job_id = ? ORDER BY ordinal",
            (ORIGINAL_FAILED_JOB_ID,),
        ).fetchall()
    events: list[DiagnosisStageEventV02322] = []
    previous = "0" * 64
    for ordinal, row in enumerate(rows, start=1):
        event = DiagnosisStageEventV02322.model_validate_json(str(row["payload_json"]))
        if (
            event.ordinal != ordinal
            or event.event_sha256 != str(row["event_sha256"])
            or event.previous_event_sha256 != previous
            or event.job_id != ORIGINAL_FAILED_JOB_ID
            or event.incident_id != FORMAL_INCIDENT_ID
        ):
            raise ValueError("Product v0.2.3.2.3 stage journal chain differs")
        previous = event.event_sha256
        events.append(event)
    return tuple(events)


def _require_read_only_tree(path: Path) -> None:
    if not is_read_only_tree_v02323(path):
        raise ValueError(f"Product v0.2.3.2.3 private tree is writable: {path}")
    if any(
        item.name.endswith(("-wal", "-shm", "-journal"))
        for item in path.rglob("*")
        if item.is_file()
    ):
        raise ValueError(f"Product v0.2.3.2.3 SQLite sidecar differs: {path}")


def _validate_structural_acquisition(
    path: Path,
    replay: FrozenIncidentReplayInputV02323,
    *,
    expected_acquisition_payload: dict[str, object],
) -> None:
    wrapper = _load(path)
    wrapper_sha256 = _require_seal(wrapper, "structural_acquisition_sha256")
    acquisition_payload = wrapper.get("acquisition")
    acquisition = TypeAdapter(ProductReadAcquisitionV1).validate_json(
        json.dumps(acquisition_payload, sort_keys=True, separators=(",", ":"))
    )
    if (
        wrapper.get("schema_version")
        != "ecomsre.product.structural-acquisition-private.v02323"
        or set(wrapper)
        != {
            "schema_version",
            "replay_input_sha256",
            "acquisition",
            "evaluator_truth_field_count",
            "structural_acquisition_sha256",
        }
        or wrapper.get("replay_input_sha256") != replay.replay_input_sha256
        or wrapper.get("evaluator_truth_field_count") != 0
        or wrapper_sha256
        != semantic_sha256_v22(
            {
                key: value
                for key, value in wrapper.items()
                if key != "structural_acquisition_sha256"
            }
        )
        or replay.structural_input_sha256_by_kind.get("acquisition")
        != semantic_sha256_v22(acquisition_payload)
        or acquisition_payload != expected_acquisition_payload
        or len(acquisition.raw_outcomes) != 6
        or len(acquisition.memory_outcomes) != 5
        or len(acquisition.snapshots) != 6
        or acquisition.capability_limitations
        != ("RUNTIME_DIAGNOSIS_UNAVAILABLE", "RUNTIME_MEMORY_AUTHORITY_UNAVAILABLE")
        or len(acquisition.capability_limitation_candidates_v0232) != 2
    ):
        raise ValueError("Product v0.2.3.2.3 structural acquisition differs")
    serialized = json.dumps(acquisition_payload, sort_keys=True, ensure_ascii=False)
    if "evaluator_truth" in serialized or "fault_truth" in serialized:
        raise ValueError(
            "Product v0.2.3.2.3 structural acquisition leaks evaluator truth"
        )
    for snapshot in acquisition.snapshots:
        connector = snapshot.get("connector_result")
        outcome = snapshot.get("read_outcome")
        if (
            not isinstance(connector, dict)
            or connector.get("status") != "SUCCESS_EMPTY"
            or connector.get("records") not in ([], ())
            or not isinstance(outcome, dict)
            or outcome.get("status") != "SUCCESS_EMPTY"
            or outcome.get("records") not in ([], ())
        ):
            raise ValueError("Product v0.2.3.2.3 structural read differs")


def _validate_failed_structural_acquisition(
    path: Path,
    *,
    replay: FrozenIncidentReplayInputV02323,
    failed_replay_id: str,
) -> str:
    wrapper = _load(path)
    wrapper_sha256 = _require_seal(wrapper, "structural_acquisition_sha256")
    acquisition_payload = wrapper.get("acquisition")
    acquisition = TypeAdapter(ProductReadAcquisitionV1).validate_json(
        json.dumps(acquisition_payload, sort_keys=True, separators=(",", ":"))
    )
    acquisition_sha256 = semantic_sha256_v22(acquisition_payload)
    failed_bindings = {
        **replay.structural_input_sha256_by_kind,
        "acquisition": acquisition_sha256,
    }
    failed_input_body = replay.model_dump(
        mode="json",
        exclude={"replay_input_sha256"},
    )
    failed_input_body["replay_id"] = failed_replay_id
    failed_input_body["structural_input_sha256_by_kind"] = failed_bindings
    expected_replay_input_sha256 = semantic_sha256_v22(failed_input_body)
    serialized = json.dumps(acquisition_payload, sort_keys=True, ensure_ascii=False)
    if (
        set(wrapper)
        != {
            "schema_version",
            "replay_input_sha256",
            "acquisition",
            "evaluator_truth_field_count",
            "structural_acquisition_sha256",
        }
        or wrapper.get("schema_version")
        != "ecomsre.product.structural-acquisition-private.v02323"
        or wrapper.get("evaluator_truth_field_count") != 0
        or wrapper.get("replay_input_sha256") != expected_replay_input_sha256
        or len(acquisition.raw_outcomes) != 6
        or len(acquisition.memory_outcomes) != 6
        or len(acquisition.snapshots) != 6
        or acquisition.capability_limitations
        or acquisition.capability_limitation_candidates_v0232
        or "evaluator_truth" in serialized
        or "fault_truth" in serialized
        or any(
            item.status.value != "SUCCESS_EMPTY" for item in acquisition.raw_outcomes
        )
        or any(item.records for item in acquisition.raw_outcomes)
        or any(
            item.status.value != "SUCCESS_EMPTY" for item in acquisition.memory_outcomes
        )
        or any(item.records for item in acquisition.memory_outcomes)
    ):
        raise ValueError("Product v0.2.3.2.3 failed structural admission differs")
    return wrapper_sha256


def _rebuild_structural_acquisition(
    replay_product: Path,
    replay: FrozenIncidentReplayInputV02323,
) -> tuple[
    ProductReadAcquisitionV1,
    dict[str, object],
    dict[str, str],
    Any,
    Any,
    Any,
]:
    with tempfile.TemporaryDirectory(prefix="product-v02323-context-") as temporary:
        temporary_product = Path(temporary) / "product"
        shutil.copytree(replay_product, temporary_product, copy_function=shutil.copy2)
        _make_tree_owner_writable(temporary_product)
        store = SqliteStoreV1(temporary_product / "product.sqlite3")
        incident, environment, identity, capability, baseline = _load_context(store)
        acquisition = build_structural_acquisition_v02323(
            incident=incident,
            baseline=baseline,
        )
        payload = _acquisition_payload(acquisition)
        bindings = {
            "acquisition": semantic_sha256_v22(payload),
            "baseline": baseline.baseline_sha256,
            "capability": capability.capability_sha256,
            "environment": semantic_sha256_v22(environment.model_dump(mode="json")),
            "incident": incident.incident_sha256,
            "service_identity": identity.identity_sha256,
        }
    if bindings != replay.structural_input_sha256_by_kind:
        raise ValueError("Product v0.2.3.2.3 structural input bindings differ")
    return acquisition, payload, bindings, incident, baseline, identity


def _canonical_json_sha256(payload: object) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _rebuild_persistence_artifacts(
    *,
    result: Any,
    observations: tuple[dict[str, Any], ...],
    decision_trace: Any,
    acquisition: ProductReadAcquisitionV1,
    bridge: DiagnosisBridgeArtifactV02322,
) -> tuple[
    EvidenceBundleV1,
    DiagnosisEvidenceIndexV0232,
    DiagnosisPersistencePlanV02322,
]:
    observation_sha256 = {
        str(item["evidence_ref"]): semantic_sha256_v22(item["payload"])
        for item in observations
    }
    limitation_bindings = _build_limitation_bindings(
        result=result,
        observations=observations,
        candidates=acquisition.capability_limitation_candidates_v0232,
    )
    objects = tuple(
        sorted(
            (
                EvidenceObjectV1(
                    evidence_ref=str(item["evidence_ref"]),
                    source=item["source"],
                    action_id=str(item["action_id"]),
                    object_sha256=_canonical_json_sha256(item["payload"]),
                    payload=item["payload"],
                )
                for item in observations
            ),
            key=lambda item: item.evidence_ref,
        )
    )
    bundle = EvidenceBundleV1(
        incident_id=result.incident_id,
        diagnosis_id=result.diagnosis_id,
        objects=objects,
        supporting_evidence_refs=result.supporting_evidence_refs,
        contradicting_evidence_refs=result.contradicting_evidence_refs,
    )
    dispositions = {
        str(item["evidence_ref"]): _source_disposition(item["payload"])
        for item in observations
    }
    opensearch_refs = _specialized_binding_refs(
        observations,
        binding_kind="OPENSEARCH_PROFILE",
    )
    runtime_refs = _specialized_binding_refs(
        observations,
        binding_kind="RUNTIME_SNAPSHOT",
    )
    index = DiagnosisEvidenceIndexV0232.build(
        incident_id=result.incident_id,
        diagnosis_id=result.diagnosis_id,
        evidence_bundle_sha256=semantic_sha256_v22(bundle.model_dump(mode="json")),
        all_object_refs=tuple(item.evidence_ref for item in objects),
        all_object_sha256_by_ref={
            item.evidence_ref: item.object_sha256 for item in objects
        },
        linked_support_refs=result.supporting_evidence_refs,
        linked_contradiction_refs=result.contradicting_evidence_refs,
        successful_source_refs=tuple(
            sorted(
                reference
                for reference, disposition in dispositions.items()
                if disposition == "SUCCESSFUL"
            )
        ),
        failed_source_refs=tuple(
            sorted(
                reference
                for reference, disposition in dispositions.items()
                if disposition == "FAILED"
            )
        ),
        open_search_profile_binding_ref=(
            opensearch_refs[0] if opensearch_refs else None
        ),
        runtime_snapshot_binding_ref=(runtime_refs[0] if runtime_refs else None),
        capability_limitation_bindings=limitation_bindings,
        decision_trace_sha256=decision_trace.trace_sha256,
    )
    plan = DiagnosisPersistencePlanV02322.build(
        incident_id=result.incident_id,
        diagnosis_id=result.diagnosis_id,
        bridge_sha256=bridge.bridge_sha256,
        evidence_object_sha256_by_ref=dict(sorted(observation_sha256.items())),
        limitation_bindings_sha256=semantic_sha256_v22(
            [item.model_dump(mode="json") for item in limitation_bindings]
        ),
        evidence_bundle_sha256=index.evidence_bundle_sha256,
        evidence_index_sha256=index.index_sha256,
        decision_trace_sha256=decision_trace.trace_sha256,
    )
    return bundle, index, plan


def _walk_mappings(value: object) -> Sequence[dict[str, Any]]:
    mappings: list[dict[str, Any]] = []
    if isinstance(value, dict):
        mappings.append(value)
        for item in value.values():
            mappings.extend(_walk_mappings(item))
    elif isinstance(value, list):
        for item in value:
            mappings.extend(_walk_mappings(item))
    return mappings


def _audit_historical_exact_acquisition(
    *,
    formal_private_root: Path,
    forensic_snapshot_product: Path,
    forensic_snapshot_database: Path,
) -> dict[str, object]:
    formal_root = _private_path(formal_private_root, ".local/product-v02321/formal")
    json_paths = tuple(
        sorted(
            {
                *formal_root.rglob("*.json"),
                *(forensic_snapshot_product / "objects").rglob("*.json"),
                *(forensic_snapshot_product / "pilot").rglob("*.json"),
            }
        )
    )
    inventory: list[dict[str, object]] = []
    acquisition_hits: list[str] = []
    for path in json_paths:
        payload = json.loads(path.read_text(encoding="utf-8"))
        relative = (
            f"formal/{path.relative_to(formal_root)}"
            if path.is_relative_to(formal_root)
            else f"forensic-snapshot/{path.relative_to(forensic_snapshot_product)}"
        )
        inventory.append(
            {
                "path": relative,
                "sha256": _sha256_file(path),
                "size_bytes": path.stat().st_size,
            }
        )
        for mapping in _walk_mappings(payload):
            bound_identity = {
                mapping.get("incident_id"),
                mapping.get("job_id"),
            }
            schema_version = str(mapping.get("schema_version", ""))
            if {FORMAL_INCIDENT_ID, ORIGINAL_FAILED_JOB_ID}.intersection(
                bound_identity
            ) and (
                _EXACT_ACQUISITION_KEYS.intersection(mapping)
                or "acquisition" in schema_version.lower()
                or "read-snapshot" in schema_version.lower()
            ):
                acquisition_hits.append(relative)
    database = forensic_snapshot_database
    failed_job = _row(
        database,
        "SELECT status, result_json, safe_error_code FROM diagnosis_jobs "
        "WHERE job_id = ?",
        (ORIGINAL_FAILED_JOB_ID,),
    )
    with _connect_read_only(database) as connection:
        acquisition_table_count = int(
            connection.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table' "
                "AND lower(name) LIKE '%acquisition%'"
            ).fetchone()[0]
        )
        formal_diagnosis_count = int(
            connection.execute(
                "SELECT COUNT(*) FROM diagnosis_results WHERE incident_id = ?",
                (FORMAL_INCIDENT_ID,),
            ).fetchone()[0]
        )
        formal_evidence_link_count = int(
            connection.execute(
                "SELECT COUNT(*) FROM diagnosis_evidence_links AS links "
                "JOIN diagnosis_results AS results "
                "ON results.diagnosis_id = links.diagnosis_id "
                "WHERE results.incident_id = ?",
                (FORMAL_INCIDENT_ID,),
            ).fetchone()[0]
        )
    body: dict[str, object] = {
        "schema_version": "ecomsre.product.historical-acquisition-audit.v02323",
        "formal_incident_id": FORMAL_INCIDENT_ID,
        "original_failed_job_id": ORIGINAL_FAILED_JOB_ID,
        "scanned_json_file_count": len(json_paths),
        "scanned_json_inventory_sha256": semantic_sha256_v22(inventory),
        "formal_bound_acquisition_hit_count": len(acquisition_hits),
        "acquisition_table_count": acquisition_table_count,
        "formal_diagnosis_result_count": formal_diagnosis_count,
        "formal_evidence_link_count": formal_evidence_link_count,
        "failed_job_status": failed_job.get("status"),
        "failed_job_result_absent": failed_job.get("result_json") is None,
        "failed_job_safe_error_code": failed_job.get("safe_error_code"),
        "exact_acquisition_available": False,
        "classification": "STRUCTURAL_CONTRACT_REPLAY",
    }
    if (
        acquisition_hits
        or acquisition_table_count != 0
        or formal_diagnosis_count != 0
        or formal_evidence_link_count != 0
        or failed_job
        != {
            "status": "FAILED",
            "result_json": None,
            "safe_error_code": "INTERNAL_CONTRACT_FAILURE",
        }
    ):
        raise ValueError("Product v0.2.3.2.3 historical acquisition audit differs")
    return {**body, "audit_sha256": semantic_sha256_v22(body)}


def _require_exact_entries(
    path: Path,
    *,
    directories: set[str],
    files: set[str],
) -> None:
    entries = tuple(path.iterdir())
    if any(item.is_symlink() for item in entries):
        raise ValueError("Product v0.2.3.2.3 private inventory contains symlink")
    actual_directories = {item.name for item in entries if item.is_dir()}
    actual_files = {item.name for item in entries if item.is_file()}
    if (
        actual_directories != directories
        or actual_files != files
        or len(entries) != len(directories) + len(files)
    ):
        raise ValueError("Product v0.2.3.2.3 private inventory differs")


def _validate_temporary_cas(path: Path, *, expected_sha256: set[str]) -> int:
    expected_directories = {"sha256"} | {
        f"sha256/{digest[:2]}" for digest in expected_sha256
    }
    expected_files = {
        f"sha256/{digest[:2]}/{digest}.json" for digest in expected_sha256
    }
    entries = tuple(path.rglob("*"))
    if any(item.is_symlink() for item in entries):
        raise ValueError("Product v0.2.3.2.3 temporary CAS contains symlink")
    actual_directories = {
        item.relative_to(path).as_posix() for item in entries if item.is_dir()
    }
    actual_files = {
        item.relative_to(path).as_posix() for item in entries if item.is_file()
    }
    if actual_directories != expected_directories or actual_files != expected_files:
        raise ValueError("Product v0.2.3.2.3 temporary CAS inventory differs")
    objects = tuple(sorted(path / relative for relative in expected_files))
    for item in objects:
        if item.stem != _sha256_file(item):
            raise ValueError("Product v0.2.3.2.3 temporary CAS object differs")
        json.loads(item.read_text(encoding="utf-8"))
    return len(objects)


def verify_product_v02323_increment3(
    root: Path,
    *,
    source_root: Path,
    pristine_root: Path,
    formal_private_root: Path,
) -> dict[str, object]:
    project = Path(root).resolve(strict=True)
    increment2 = verify_product_v02323_increment2(
        project,
        source_root=source_root,
        pristine_root=pristine_root,
        formal_private_root=formal_private_root,
        allow_later_phase_artifacts=True,
    )
    reconstruction_evidence = _load(
        project / "docs/analysis/product-v02323-schema8-reconstruction.json"
    )
    forensic_snapshot = ForensicRawSourceSnapshotV02323.model_validate_json(
        (
            project / "docs/analysis/product-v02323-forensic-source-snapshot.json"
        ).read_text(encoding="utf-8")
    )
    forensic_snapshot_root = _private_path(project, forensic_snapshot.snapshot_locator)
    forensic_snapshot_product = forensic_snapshot_root / "product"
    forensic_snapshot_database = (
        forensic_snapshot_root / "consistent-image/product.sqlite3"
        if forensic_snapshot.snapshot_consistent_database_present
        else forensic_snapshot_product / "product.sqlite3"
    )
    reconstruction = Schema8ReconstructionV02323.model_validate(
        reconstruction_evidence.get("reconstruction")
    )
    replay = FrozenIncidentReplayInputV02323.model_validate_json(
        (project / "config/product-v02323/replay/replay-input.json").read_text(
            encoding="utf-8"
        )
    )
    replay_evidence = _load(project / "docs/analysis/product-v02323-replay-input.json")
    replay_evidence_sha256 = _require_seal(replay_evidence, "replay_evidence_sha256")
    clone = ReplayCloneEvidenceV02323.model_validate(
        replay_evidence.get("clone_evidence")
    )
    if (
        replay_evidence.get("schema_version")
        != "ecomsre.product.replay-input-evidence.v02323"
        or replay_evidence.get("goal_version") != GOAL_VERSION_V02323
        or replay_evidence.get("terminal") != replay.terminal
        or replay_evidence.get("replay_input") != replay.model_dump(mode="json")
        or replay_evidence.get("diagnosis_persistence_replay_attempt_count") != 0
        or replay_evidence.get("provider_agent_runbook_docker_calls") != 0
        or replay_evidence.get("exact_acquisition_persistence_audit")
        != {
            "database_acquisition_table_count": 0,
            "persisted_exact_acquisition_bundle_count": 0,
            "missing_fields": list(replay.missing_exact_acquisition_fields),
            "classification": "STRUCTURAL_CONTRACT_REPLAY",
        }
        or replay.reconstruction_sha256 != reconstruction.reconstruction_sha256
        or clone.reconstruction_sha256 != reconstruction.reconstruction_sha256
        or clone.replay_id != replay.replay_id
    ):
        raise ValueError("Product v0.2.3.2.3 replay evidence differs")

    reconstructed_product = _private_path(project, clone.source_locator)
    replay_root = _private_path(project, str(Path(clone.replay_clone_locator).parent))
    replay_product = _private_path(project, clone.replay_clone_locator)
    reconstructed_database = reconstructed_product / "product.sqlite3"
    replay_database = replay_product / "product.sqlite3"
    _require_read_only_tree(reconstructed_product.parent)
    _require_read_only_tree(replay_root)
    expected_schema9_inventory = _expected_schema9_inventory(reconstructed_product)
    expected_schema9_inventory_sha256 = semantic_sha256_v22(expected_schema9_inventory)
    reconstructed_support_inventory = _support_file_inventory(reconstructed_product)
    _verify_product_support_inventory(
        replay_product,
        reconstruction=reconstruction,
        expected_files=reconstructed_support_inventory,
        allowed_top_level={"objects", "pilot", "product.sqlite3"},
    )
    if (
        _sha256_file(reconstructed_database) != clone.source_database_file_sha256
        or _sha256_file(replay_database) != clone.replay_database_after_migration_sha256
        or _schema_versions(reconstructed_database) != tuple(range(1, 9))
        or _schema_versions(replay_database) != tuple(range(1, 10))
        or _schema_inventory_payload(replay_database) != expected_schema9_inventory
        or semantic_sha256_v22(
            _logical_database_payload(
                reconstructed_database,
                project_schema8=True,
                exclude_stage_journal=True,
            )
        )
        != semantic_sha256_v22(
            _logical_database_payload(
                replay_database,
                project_schema8=True,
                exclude_stage_journal=True,
            )
        )
    ):
        raise ValueError("Product v0.2.3.2.3 migration-9-only clone differs")
    with _connect_read_only(replay_database) as connection:
        stage_count = int(
            connection.execute(
                "SELECT COUNT(*) FROM diagnosis_stage_events_v02322"
            ).fetchone()[0]
        )
        acquisition_table_count = int(
            connection.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table' "
                "AND lower(name) LIKE '%acquisition%'"
            ).fetchone()[0]
        )
        non_null = {
            column: int(
                connection.execute(
                    f"SELECT COUNT(*) FROM diagnosis_jobs WHERE {column} IS NOT NULL"
                ).fetchone()[0]
            )
            for column in _NEW_JOB_COLUMNS
        }
    if (
        stage_count != 0
        or acquisition_table_count != 0
        or set(non_null.values()) != {0}
    ):
        raise ValueError("Product v0.2.3.2.3 replay clone initialization differs")
    (
        expected_acquisition,
        expected_acquisition_payload,
        _structural_bindings,
        structural_incident,
        structural_baseline,
        structural_identity,
    ) = _rebuild_structural_acquisition(replay_product, replay)
    _validate_structural_acquisition(
        replay_root / "structural-acquisition.json",
        replay,
        expected_acquisition_payload=expected_acquisition_payload,
    )
    historical_acquisition_audit = _audit_historical_exact_acquisition(
        formal_private_root=formal_private_root,
        forensic_snapshot_product=forensic_snapshot_product,
        forensic_snapshot_database=forensic_snapshot_database,
    )

    forensics = DiagnosisForensicsEvidenceV02323.model_validate_json(
        (project / "docs/analysis/product-v02323-diagnosis-forensics.json").read_text(
            encoding="utf-8"
        )
    )
    root_cause = DiagnosisRootCauseDispositionV02323.model_validate_json(
        (project / "docs/analysis/product-v02323-diagnosis-root-cause.json").read_text(
            encoding="utf-8"
        )
    )
    if (
        forensics.replay_input_sha256 != replay.replay_input_sha256
        or root_cause.replay_input_sha256 != replay.replay_input_sha256
        or root_cause.forensics_sha256 != forensics.forensics_sha256
    ):
        raise ValueError("Product v0.2.3.2.3 root-cause bindings differ")
    root_cause_markdown = (
        project / "docs/analysis/product-v02323-diagnosis-root-cause.md"
    ).read_text(encoding="utf-8")
    if any(
        marker not in root_cause_markdown
        for marker in (
            root_cause.disposition,
            root_cause.disposition_sha256,
            "STRUCTURAL_CONTRACT_REPLAY",
            "NOT_APPLICABLE",
        )
    ):
        raise ValueError("Product v0.2.3.2.3 root-cause Markdown differs")

    attempts = _load(
        project / "docs/analysis/product-v02323-diagnosis-forensics-attempts.json"
    )
    attempts_sha256 = _require_seal(attempts, "attempts_sha256")
    attempt_items = attempts.get("attempts")
    if (
        attempts.get("schema_version")
        != "ecomsre.product.diagnosis-forensics-attempts.v02323"
        or attempts.get("attempt_count") != 2
        or attempts.get("diagnosis_persistence_replay_attempt_count") != 0
        or attempts.get("provider_agent_runbook_docker_calls") != 0
        or not isinstance(attempt_items, list)
        or len(attempt_items) != 2
        or attempt_items[0]
        != {
            "attempt_ordinal": 1,
            "failure_sha256": (
                "e0c237dc2108a34478a055b83111b5cf6695f111cf508bba043f825307972443"
            ),
            "failure_stage": "BRIDGE_DIAGNOSIS_STARTED",
            "read_only": True,
            "replay_id": "replay-1ba71a74639c964f501adf86",
            "safe_error_code": "INTERNAL_CONTRACT_FAILURE",
            "status": "FAILED_CLOSED",
        }
        or attempt_items[1]
        != {
            "attempt_ordinal": 2,
            "forensics_sha256": forensics.forensics_sha256,
            "read_only": True,
            "replay_id": replay.replay_id,
            "replay_input_sha256": replay.replay_input_sha256,
            "status": "PASS",
        }
    ):
        raise ValueError("Product v0.2.3.2.3 forensics attempts differ")

    failed = attempt_items[0]
    failed_replay_id = str(failed.get("replay_id"))
    expected_attempt_ids = {failed_replay_id, replay.replay_id}
    for attempts_root in (
        project / ".local/product-v02323/replay-input",
        project / ".local/product-v02323/diagnosis-forensics",
    ):
        entries = tuple(attempts_root.iterdir())
        if (
            any(not item.is_dir() or item.is_symlink() for item in entries)
            or {item.name for item in entries} != expected_attempt_ids
        ):
            raise ValueError("Product v0.2.3.2.3 private attempt inventory differs")
    failed_replay_root = _private_path(
        project, f".local/product-v02323/replay-input/{failed_replay_id}"
    )
    failed_forensics_root = _private_path(
        project, f".local/product-v02323/diagnosis-forensics/{failed_replay_id}"
    )
    success_forensics_root = _private_path(
        project, f".local/product-v02323/diagnosis-forensics/{replay.replay_id}"
    )
    for private_root in (
        failed_replay_root,
        failed_forensics_root,
        success_forensics_root,
    ):
        _require_read_only_tree(private_root)
    _require_exact_entries(
        failed_replay_root,
        directories={"product"},
        files={"structural-acquisition.json"},
    )
    _require_exact_entries(
        failed_forensics_root,
        directories={"product"},
        files={"diagnosis-forensics-failure.json"},
    )
    _require_exact_entries(
        success_forensics_root,
        directories={"product", "temporary-cas"},
        files=set(),
    )
    private_products = (
        (failed_replay_root / "product", {"objects", "pilot", "product.sqlite3"}),
        (
            failed_forensics_root / "product",
            {"objects", "pilot", "private", "product.sqlite3"},
        ),
        (
            success_forensics_root / "product",
            {"objects", "pilot", "product.sqlite3"},
        ),
    )
    for product_root, allowed_top_level in private_products:
        _verify_product_support_inventory(
            product_root,
            reconstruction=reconstruction,
            expected_files=reconstructed_support_inventory,
            allowed_top_level=allowed_top_level,
        )
        if (
            _schema_inventory_payload(product_root / "product.sqlite3")
            != expected_schema9_inventory
        ):
            raise ValueError("Product v0.2.3.2.3 private schema inventory differs")
    failed_replay_database = failed_replay_root / "product/product.sqlite3"
    if semantic_sha256_v22(
        _logical_database_payload(
            failed_replay_database,
            project_schema8=True,
            exclude_stage_journal=True,
        )
    ) != semantic_sha256_v22(
        _logical_database_payload(
            reconstructed_database,
            project_schema8=True,
            exclude_stage_journal=True,
        )
    ):
        raise ValueError("Product v0.2.3.2.3 failed migration-9-only clone differs")
    with _connect_read_only(failed_replay_database) as connection:
        failed_initial_stage_count = int(
            connection.execute(
                "SELECT COUNT(*) FROM diagnosis_stage_events_v02322"
            ).fetchone()[0]
        )
        failed_initial_non_null = {
            column: int(
                connection.execute(
                    f"SELECT COUNT(*) FROM diagnosis_jobs WHERE {column} IS NOT NULL"
                ).fetchone()[0]
            )
            for column in _NEW_JOB_COLUMNS
        }
    if failed_initial_stage_count != 0 or set(failed_initial_non_null.values()) != {0}:
        raise ValueError("Product v0.2.3.2.3 failed replay initialization differs")
    failed_structural_acquisition_sha256 = _validate_failed_structural_acquisition(
        failed_replay_root / "structural-acquisition.json",
        replay=replay,
        failed_replay_id=failed_replay_id,
    )

    public_failure = _load(failed_forensics_root / "diagnosis-forensics-failure.json")
    public_failure_sha256 = _require_seal(public_failure, "failure_sha256")
    private_failures = tuple(
        sorted(
            (
                failed_forensics_root
                / "product/private/diagnosis-failures"
                / ORIGINAL_FAILED_JOB_ID
            ).glob("failure-*.json")
        )
    )
    if len(private_failures) != 1:
        raise ValueError("Product v0.2.3.2.3 private failure inventory differs")
    private_failure_root = failed_forensics_root / "product/private"
    private_failure_parent = private_failure_root / "diagnosis-failures"
    private_failure_job_root = private_failure_parent / ORIGINAL_FAILED_JOB_ID
    _require_exact_entries(
        private_failure_root,
        directories={"diagnosis-failures"},
        files=set(),
    )
    _require_exact_entries(
        private_failure_parent,
        directories={ORIGINAL_FAILED_JOB_ID},
        files=set(),
    )
    _require_exact_entries(
        private_failure_job_root,
        directories=set(),
        files={private_failures[0].name},
    )
    private_failure = DiagnosisPrivateFailureEnvelopeV02322.model_validate_json(
        private_failures[0].read_text(encoding="utf-8")
    )
    failed_events = _stage_events(failed_forensics_root / "product/product.sqlite3")
    if (
        failed.get("failure_sha256") != public_failure_sha256
        or failed.get("failure_stage") != "BRIDGE_DIAGNOSIS_STARTED"
        or failed.get("safe_error_code") != "INTERNAL_CONTRACT_FAILURE"
        or public_failure.get("failure_stage") != private_failure.failing_stage.value
        or public_failure.get("exception_fingerprint")
        != private_failure.exception_fingerprint
        or len(failed_events) != 28
        or tuple((event.stage.value, event.status.value) for event in failed_events)
        != _EXPECTED_FAILED_SEQUENCE
        or failed_events[-2].stage.value != "BRIDGE_DIAGNOSIS_STARTED"
        or failed_events[-2].status.value != "STARTED"
        or failed_events[-1].stage.value != "FAILED"
        or failed_events[-1].status.value != "FAILED"
        or public_failure.get("journal_tail_sha256") != failed_events[-1].event_sha256
        or private_failure.journal_tail_sha256 != failed_events[-2].event_sha256
        or _counts(failed_replay_root / "product/product.sqlite3")
        != _counts(failed_forensics_root / "product/product.sqlite3")
        or semantic_sha256_v22(
            _logical_database_payload(
                failed_replay_root / "product/product.sqlite3",
                project_schema8=False,
                exclude_stage_journal=True,
            )
        )
        != semantic_sha256_v22(
            _logical_database_payload(
                failed_forensics_root / "product/product.sqlite3",
                project_schema8=False,
                exclude_stage_journal=True,
            )
        )
    ):
        raise ValueError("Product v0.2.3.2.3 failed forensics attempt differs")

    forensics_database = success_forensics_root / "product/product.sqlite3"
    success_events = _stage_events(forensics_database)
    diagnosis_id = (
        "diag-"
        + semantic_sha256_v22(
            {
                "replay_input_sha256": replay.replay_input_sha256,
                "incident_id": structural_incident.incident_id,
                "phase": "ROLLBACK_ONLY_FORENSICS",
            }
        )[:24]
    )
    result, observations, decision_trace = ProductDiagnosisBridgeV1().diagnose(
        incident=structural_incident,
        baseline=structural_baseline,
        identity_map=structural_identity,
        acquisition=expected_acquisition,
        diagnosis_id=diagnosis_id,
        created_at=success_events[0].observed_at,
    )
    bridge = DiagnosisBridgeArtifactV02322.build(
        incident_id=result.incident_id,
        diagnosis_id=result.diagnosis_id,
        result_sha256=result.result_sha256,
        observations_sha256=semantic_sha256_v22(list(observations)),
        decision_trace_sha256=decision_trace.trace_sha256,
    )
    bundle, index, persistence_plan = _rebuild_persistence_artifacts(
        result=result,
        observations=observations,
        decision_trace=decision_trace,
        acquisition=expected_acquisition,
        bridge=bridge,
    )
    expected_temporary_cas_sha256 = {
        *(_canonical_json_sha256(item["payload"]) for item in observations),
        _canonical_json_sha256(decision_trace.model_dump(mode="json")),
    }
    success_event_by_stage_status = {
        (event.stage.value, event.status.value): event for event in success_events
    }
    original_input_job = _row(
        replay_database,
        "SELECT * FROM diagnosis_jobs WHERE job_id = ?",
        (ORIGINAL_FAILED_JOB_ID,),
    )
    original_forensics_job = _row(
        forensics_database,
        "SELECT * FROM diagnosis_jobs WHERE job_id = ?",
        (ORIGINAL_FAILED_JOB_ID,),
    )
    if (
        len(success_events) != forensics.stage_event_count
        or len(success_events) != 48
        or tuple((event.stage.value, event.status.value) for event in success_events)
        != _EXPECTED_SUCCESS_SEQUENCE
        or success_events[-1].stage.value != "SQL_TRANSACTION_STARTED"
        or success_events[-1].status.value != "PASSED"
        or success_events[-1].event_sha256 != forensics.journal_tail_sha256
        or any(event.stage.value == "FAILED" for event in success_events)
        or result.result_sha256 != forensics.diagnosis_result_sha256
        or result.terminal.value != forensics.diagnosis_terminal
        or bridge.bridge_sha256 != forensics.bridge_sha256
        or decision_trace.trace_sha256 != forensics.decision_trace_sha256
        or semantic_sha256_v22(bundle.model_dump(mode="json"))
        != forensics.evidence_bundle_sha256
        or index.index_sha256 != forensics.evidence_index_sha256
        or persistence_plan.persistence_plan_sha256 != forensics.persistence_plan_sha256
        or success_event_by_stage_status[
            ("BRIDGE_DIAGNOSIS_COMPLETED", "STARTED")
        ].input_binding_sha256
        != bridge.bridge_sha256
        or success_event_by_stage_status[
            ("EVIDENCE_PREPARE_STARTED", "STARTED")
        ].input_binding_sha256
        != result.result_sha256
        or success_event_by_stage_status[
            ("EVIDENCE_INDEX_VALIDATED", "STARTED")
        ].input_binding_sha256
        != index.index_sha256
        or success_event_by_stage_status[
            ("OBJECT_STORE_PREPARE_STARTED", "STARTED")
        ].input_binding_sha256
        != index.index_sha256
        or success_event_by_stage_status[
            ("SQL_TRANSACTION_STARTED", "STARTED")
        ].input_binding_sha256
        != result.result_sha256
        or original_input_job != original_forensics_job
        or original_forensics_job.get("status") != "FAILED"
        or original_forensics_job.get("safe_error_code") != "INTERNAL_CONTRACT_FAILURE"
        or original_forensics_job.get("result_json") is not None
        or _counts(replay_database)
        != {
            "diagnosis_results": forensics.diagnosis_count_before,
            "diagnosis_evidence_indexes": forensics.evidence_index_count_before,
            "evidence_objects": forensics.evidence_object_count_before,
        }
        or _counts(forensics_database)
        != {
            "diagnosis_results": forensics.diagnosis_count_after,
            "diagnosis_evidence_indexes": forensics.evidence_index_count_after,
            "evidence_objects": forensics.evidence_object_count_after,
        }
        or semantic_sha256_v22(
            _logical_database_payload(
                replay_database,
                project_schema8=False,
                exclude_stage_journal=True,
            )
        )
        != semantic_sha256_v22(
            _logical_database_payload(
                forensics_database,
                project_schema8=False,
                exclude_stage_journal=True,
            )
        )
    ):
        raise ValueError("Product v0.2.3.2.3 rollback-only forensics differs")
    temporary_cas_count = _validate_temporary_cas(
        success_forensics_root / "temporary-cas",
        expected_sha256=expected_temporary_cas_sha256,
    )

    progress = _load(project / "docs/analysis/product-v02323-progress.json")
    progress_sha256 = _require_seal(progress, "progress_sha256")
    expected_later = {
        "increment": 3,
        "phase": "ROOT_CAUSE_DISPOSITION_FROZEN",
        "terminals": [
            "ECOMSRE_PRODUCT_V02323_HISTORY_AND_BLOCKER_PASS",
            "ECOMSRE_PRODUCT_V02323_FORENSIC_SOURCE_SNAPSHOT_PASS",
            "ECOMSRE_PRODUCT_V02323_DIGEST_SEMANTICS_PASS",
            "ECOMSRE_PRODUCT_V02323_SCHEMA9_CONTAMINATION_AUDIT_PASS",
            "ECOMSRE_PRODUCT_V02323_RECONSTRUCTION_DISPOSITION_FROZEN",
            replay.terminal,
            root_cause.terminal,
        ],
        "replay_input_sha256": replay.replay_input_sha256,
        "replay_clone_evidence_sha256": clone.clone_evidence_sha256,
        "diagnosis_forensics_sha256": forensics.forensics_sha256,
        "diagnosis_forensics_attempt_count": 2,
        "diagnosis_forensics_attempts_sha256": attempts_sha256,
        "root_cause_disposition_sha256": root_cause.disposition_sha256,
        "replay_classification": "STRUCTURAL_CONTRACT_REPLAY",
        "root_cause_disposition": (
            "ECOMSRE_PRODUCT_V02323_ORIGINAL_ROOT_CAUSE_UNPROVEN"
        ),
        "targeted_repair": "NOT_APPLICABLE",
        "diagnosis_persistence_replay_attempt_count": 0,
        "provider_calls": 0,
        "agent_writes": 0,
        "runbook_executions": 0,
        "docker_calls": 0,
        "measured_nofault_authority": "NONE",
        "knowledge_loop_authority": "NONE",
        "next_gate": "INCREMENT_4_NO_DEFECT_PATH_AND_SINGLE_PERSISTENCE_REPLAY",
    }
    if len(progress) != 48 or any(
        progress.get(key) != value for key, value in expected_later.items()
    ):
        raise ValueError("Product v0.2.3.2.3 Increment 3 progress differs")

    return {
        "replay_terminal": replay.terminal,
        "replay_classification": replay.replay_classification.value,
        "historical_acquisition_audit_sha256": historical_acquisition_audit[
            "audit_sha256"
        ],
        "replay_evidence_sha256": replay_evidence_sha256,
        "clone_evidence_sha256": clone.clone_evidence_sha256,
        "schema9_inventory_object_count": len(expected_schema9_inventory),
        "schema9_inventory_sha256": expected_schema9_inventory_sha256,
        "forensics_terminal": forensics.terminal,
        "forensics_sha256": forensics.forensics_sha256,
        "forensics_attempt_count": 2,
        "forensics_attempts_sha256": attempts_sha256,
        "failed_forensics_stage": str(failed.get("failure_stage")),
        "failed_structural_acquisition_sha256": (failed_structural_acquisition_sha256),
        "successful_stage_event_count": len(success_events),
        "temporary_cas_object_count": temporary_cas_count,
        "root_cause_terminal": root_cause.terminal,
        "root_cause_disposition": root_cause.disposition,
        "targeted_repair": root_cause.targeted_repair,
        "progress_sha256": progress_sha256,
        "reconstruction_verification_sha256": increment2[
            "reconstruction_verification_sha256"
        ],
        "diagnosis_persistence_replay_attempt_count": 0,
        "provider_agent_runbook_docker_calls": 0,
        "measured_nofault_authority": "NONE",
        "knowledge_loop_authority": "NONE",
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
            verify_product_v02323_increment3(
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


__all__ = ("verify_product_v02323_increment3",)
