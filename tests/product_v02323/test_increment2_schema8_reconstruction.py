from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat

import pytest

import scripts.product_v02323.run_increment2_reconstruction as increment2_runner
from ecomsre.product.pilot.schema8_reconstruction_v02323 import (
    FormalProductDeltaV02323,
    ReconstructionDispositionV02323,
    Schema8ProjectionExportV02323,
    Schema8ReconstructionV02323,
    Schema9ContaminationAuditV02323,
    _normalized_schema_sql,
    ReconstructionContractErrorV02323,
    build_clean_schema8_database_v02323,
    build_formal_product_delta_v02323,
    export_schema8_projection_v02323,
    load_schema8_definition_v02323,
    load_schema9_definition_v02323,
    verify_schema8_reconstruction_v02323,
)
from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22
from ecomsre.product.storage.migrations import MIGRATIONS
from scripts.product_v02323.run_increment2_reconstruction import (
    _attempt_summaries,
    _write_attempt_envelope,
)


PR83_HEAD = "142dc1094926f18e789ece3668c34918f859b512"
PR83_MIGRATIONS_BLOB = "b0918363182b1fa6ce10aca90ef03f3d05a96cfd"


def test_schema_sql_comparison_ignores_only_unquoted_whitespace() -> None:
    assert _normalized_schema_sql(
        "CREATE TABLE t ( value TEXT CHECK(value = 'a  b') )"
    ) == _normalized_schema_sql(
        "CREATE  TABLE t (\n value TEXT CHECK(value = 'a  b')\n)"
    )
    assert _normalized_schema_sql(
        "CREATE TABLE t ( value TEXT CHECK(value = 'a  b') )"
    ) != _normalized_schema_sql(
        "CREATE TABLE t ( value TEXT CHECK(value = 'a b') )"
    )


def test_schema8_definition_is_built_from_the_exact_pr83_git_object() -> None:
    repository = Path(__file__).resolve().parents[2]

    definition = load_schema8_definition_v02323(repository)

    assert definition.source_commit == PR83_HEAD
    assert definition.source_blob_sha == PR83_MIGRATIONS_BLOB
    assert tuple(item.version for item in definition.migrations) == tuple(range(1, 9))
    tables = {
        item["name"]
        for item in definition.reference_schema_inventory
        if item["type"] == "table"
    }
    assert len(tables) == 30
    assert "diagnosis_evidence_indexes" in tables
    assert "diagnosis_stage_events_v02322" not in tables


def test_schema9_definition_is_full_pr84_git_inventory() -> None:
    repository = Path(__file__).resolve().parents[2]
    schema8 = load_schema8_definition_v02323(repository)
    schema9 = load_schema9_definition_v02323(repository, schema8)

    assert schema9.source_blob_sha == "195ce09b0b444979391e949f10c58cd5496a10ac"
    objects = {(item["type"], item["name"]) for item in schema9.expected_schema_inventory}
    assert ("table", "diagnosis_stage_events_v02322") in objects
    assert ("index", "diagnosis_stage_events_v02322_incident_idx") in objects
    assert not any(kind in {"view", "trigger"} for kind, _name in objects)


def _build_product_root(
    root: Path,
    *,
    maximum_migration: int,
    metric_value: int,
) -> Path:
    root.mkdir(parents=True)
    (root / "objects" / "sha256").mkdir(parents=True)
    (root / "pilot").mkdir()
    (root / "pilot" / "runtime-authority.json").write_text("{}\n", encoding="utf-8")
    (root / "pilot" / "runtime-readiness.json").write_text("{}\n", encoding="utf-8")
    database = root / "product.sqlite3"
    connection = sqlite3.connect(database, isolation_level=None)
    try:
        connection.execute("BEGIN IMMEDIATE")
        for version, name, statements in MIGRATIONS:
            if version > maximum_migration:
                break
            for statement in statements:
                connection.execute(statement)
            connection.execute(
                "INSERT INTO schema_migrations(version, name, applied_at) VALUES (?, ?, ?)",
                (version, name, f"2026-01-01T00:00:0{version}+00:00"),
            )
        connection.execute(
            "INSERT INTO product_metric_counters(metric_name, labels_json, value, updated_at) "
            "VALUES ('metric', '{}', ?, '2026-01-01T00:00:00+00:00')",
            (metric_value,),
        )
        connection.execute("COMMIT")
    finally:
        connection.close()
    return root


def _restore_write_bits(root: Path) -> None:
    for path in (root, *root.rglob("*")):
        os.chmod(path, path.stat().st_mode | stat.S_IWUSR)


def test_projection_delta_and_clean_builder_preserve_schema8_values(
    tmp_path: Path,
) -> None:
    repository = Path(__file__).resolve().parents[2]
    definition = load_schema8_definition_v02323(repository)
    base = _build_product_root(tmp_path / "base", maximum_migration=7, metric_value=1)
    source = _build_product_root(
        tmp_path / "source", maximum_migration=9, metric_value=2
    )
    base_export, base_rows = export_schema8_projection_v02323(
        base / "product.sqlite3",
        definition,
        formal_artifact_bindings={},
        allow_missing_schema8_tables=True,
    )
    source_export, source_rows = export_schema8_projection_v02323(
        source / "product.sqlite3",
        definition,
        formal_artifact_bindings={},
    )
    provenance_sha = hashlib.sha256(b"fixture provenance").hexdigest()
    delta = build_formal_product_delta_v02323(
        definition,
        base_rows,
        source_rows,
        provenance_by_table={
            "product_metric_counters": (
                "fixture/metric.json",
                provenance_sha,
                "fixture metric update",
            ),
            "schema_migrations": (
                "fixture/migrations.py",
                provenance_sha,
                "fixture migration 8",
            ),
        },
    )

    assert base_export.overall_projection_sha256 != source_export.overall_projection_sha256
    assert delta.changed_table_counts == {
        "product_metric_counters": 1,
        "schema_migrations": 1,
    }

    destination = tmp_path / "reconstruction" / "product"
    report = build_clean_schema8_database_v02323(
        destination,
        reconstruction_locator=".local/test/reconstruction/product",
        definition=definition,
        formal_delta=delta,
        source_projection=source_export,
        post_rows=source_rows,
        asset_source_product_root=source,
    )
    try:
        assert report.reconstructed_schema_version == 8
        assert report.source_projection_sha256 == source_export.overall_projection_sha256
        assert report.reconstructed_database_file_sha256 != hashlib.sha256(
            (source / "product.sqlite3").read_bytes()
        ).hexdigest()
        assert verify_schema8_reconstruction_v02323(
            destination,
            definition=definition,
            projection=source_export,
            reconstruction=report,
        )

        os.chmod(destination / "product.sqlite3", stat.S_IRUSR | stat.S_IWUSR)
        with pytest.raises(ReconstructionContractErrorV02323):
            verify_schema8_reconstruction_v02323(
                destination,
                definition=definition,
                projection=source_export,
                reconstruction=report,
            )
    finally:
        _restore_write_bits(destination)


def test_failed_clean_build_preserves_a_read_only_attempt_tree(tmp_path: Path) -> None:
    repository = Path(__file__).resolve().parents[2]
    definition = load_schema8_definition_v02323(repository)
    base = _build_product_root(tmp_path / "base", maximum_migration=7, metric_value=1)
    source = _build_product_root(tmp_path / "source", maximum_migration=9, metric_value=2)
    _base_export, base_rows = export_schema8_projection_v02323(
        base / "product.sqlite3",
        definition,
        formal_artifact_bindings={},
        allow_missing_schema8_tables=True,
    )
    source_export, source_rows = export_schema8_projection_v02323(
        source / "product.sqlite3", definition, formal_artifact_bindings={}
    )
    provenance_sha = hashlib.sha256(b"fixture provenance").hexdigest()
    delta = build_formal_product_delta_v02323(
        definition,
        base_rows,
        source_rows,
        provenance_by_table={
            "product_metric_counters": ("fixture", provenance_sha, "metric"),
            "schema_migrations": ("fixture", provenance_sha, "migration"),
        },
    )
    (source / "pilot" / "runtime-readiness.json").unlink()
    destination = tmp_path / "failed" / "product"

    with pytest.raises(ReconstructionContractErrorV02323):
        build_clean_schema8_database_v02323(
            destination,
            reconstruction_locator=".local/test/failed/product",
            definition=definition,
            formal_delta=delta,
            source_projection=source_export,
            post_rows=source_rows,
            asset_source_product_root=source,
        )

    try:
        assert destination.is_dir()
        assert all(
            not path.stat().st_mode & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH)
            for path in (destination, *destination.rglob("*"))
        )
    finally:
        _restore_write_bits(destination)


def test_attempt_summary_rejects_a_resealed_pass_with_projection_drift(
    tmp_path: Path,
) -> None:
    attempt_root = tmp_path / "reconstruction" / "20260831T120000Z"
    attempt_root.mkdir(parents=True)
    _write_attempt_envelope(
        attempt_root,
        {
            "schema_version": "ecomsre.product.reconstruction-attempt.v02323",
            "attempt_id": attempt_root.name,
            "status": "PASS",
            "reconstruction_sha256": "1" * 64,
            "source_projection_sha256": "2" * 64,
            "reconstructed_projection_sha256": "3" * 64,
            "source_unchanged": True,
            "diagnosis_persistence_replay_attempt_count": 0,
            "provider_agent_runbook_docker_calls": 0,
        },
    )

    try:
        with pytest.raises(RuntimeError, match="PASS reconstruction projection differs"):
            _attempt_summaries(attempt_root.parent)
    finally:
        _restore_write_bits(attempt_root)


def test_attempt_envelope_fsync_failure_does_not_publish_final_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    attempt_root = tmp_path / "20260831T120000Z"
    attempt_root.mkdir()
    real_fsync = increment2_runner.os.fsync
    calls = 0

    def fail_first_fsync(descriptor: int) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("injected file fsync failure")
        real_fsync(descriptor)

    monkeypatch.setattr(increment2_runner.os, "fsync", fail_first_fsync)

    with pytest.raises(OSError, match="injected file fsync failure"):
        _write_attempt_envelope(
            attempt_root,
            {
                "schema_version": "ecomsre.product.reconstruction-attempt.v02323",
                "attempt_id": attempt_root.name,
                "status": "PASS",
            },
        )

    assert not tuple(attempt_root.glob("attempt-*.json"))
    assert (attempt_root / ".attempt-pass.json.tmp").is_file()


def test_attempt_envelope_recovers_transient_post_publish_seal_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    attempt_root = tmp_path / "20260831T120000Z"
    attempt_root.mkdir()
    real_seal_tree = increment2_runner._seal_tree
    calls = 0

    def fail_first_seal(root: Path) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("injected seal failure")
        real_seal_tree(root)

    monkeypatch.setattr(increment2_runner, "_seal_tree", fail_first_seal)
    payload = _write_attempt_envelope(
        attempt_root,
        {
            "schema_version": "ecomsre.product.reconstruction-attempt.v02323",
            "attempt_id": attempt_root.name,
            "status": "PASS",
        },
    )

    try:
        assert calls == 2
        assert json.loads(
            (attempt_root / "attempt-pass.json").read_text(encoding="utf-8")
        ) == payload
        assert all(
            not path.stat().st_mode & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH)
            for path in (attempt_root, *attempt_root.rglob("*"))
        )
    finally:
        _restore_write_bits(attempt_root)


def test_increment2_public_artifacts_freeze_the_strongest_bounded_claim() -> None:
    repository = Path(__file__).resolve().parents[2]
    contamination = Schema9ContaminationAuditV02323.model_validate_json(
        (
            repository
            / "docs/analysis/product-v02323-schema9-contamination-audit.json"
        ).read_text(encoding="utf-8")
    )
    projection = Schema8ProjectionExportV02323.model_validate_json(
        (
            repository / "docs/analysis/product-v02323-schema8-projection.json"
        ).read_text(encoding="utf-8")
    )
    delta = FormalProductDeltaV02323.model_validate_json(
        (repository / "docs/analysis/product-v02323-formal-delta.json").read_text(
            encoding="utf-8"
        )
    )
    reconstruction_payload = json.loads(
        (
            repository / "docs/analysis/product-v02323-schema8-reconstruction.json"
        ).read_text(encoding="utf-8")
    )
    reconstruction = Schema8ReconstructionV02323.model_validate(
        reconstruction_payload["reconstruction"]
    )
    disposition = ReconstructionDispositionV02323.model_validate_json(
        (
            repository
            / "docs/analysis/product-v02323-reconstruction-disposition.json"
        ).read_text(encoding="utf-8")
    )

    assert contamination.contamination_class.value == "ADDITIVE_SCHEMA_ONLY"
    assert contamination.schema9_inventory_matches_expected is True
    assert contamination.source_schema_inventory_sha256 == (
        contamination.expected_schema9_inventory_sha256
    )
    assert contamination.extra_objects == (
        "index:diagnosis_stage_events_v02322_incident_idx",
        "table:diagnosis_stage_events_v02322",
    )
    assert contamination.diagnosis_stage_event_count == 0
    assert set(contamination.new_diagnosis_job_column_non_null_counts.values()) == {0}
    assert projection.overall_projection_sha256 == (
        reconstruction.reconstructed_projection_sha256
    )
    assert delta.changed_table_counts == {
        "diagnosis_jobs": 1,
        "incidents": 1,
        "job_events": 3,
        "product_metric_counters": 7,
        "schema_migrations": 1,
    }
    assert disposition.disposition == "PRISTINE_BASE_DELTA_RECONSTRUCTION"
    assert disposition.raw_byte_equality_claimed is False
    assert disposition.diagnosis_persistence_replay_attempt_count == 0
    assert reconstruction.reconstructed_database_file_sha256 != (
        "25d0fae060c396e63f338de886da97885c21508d265a94f1e45b999b5bc206f6"
    )
    assert reconstruction.source_object_inventory_sha256 == (
        reconstruction.reconstructed_object_inventory_sha256
    )
    assert reconstruction.source_runtime_file_inventory_sha256 == (
        reconstruction.reconstructed_runtime_file_inventory_sha256
    )
    assert reconstruction_payload["reconstruction_attempt_count"] == 3
    assert [item["status"] for item in reconstruction_payload["reconstruction_attempts"]] == [
        "FAILED_CLOSED",
        "SUPERSEDED_PASS",
        "PASS",
    ]


def test_contamination_terminal_rejects_inventory_mismatch_even_when_resealed() -> None:
    repository = Path(__file__).resolve().parents[2]
    payload = json.loads(
        (
            repository
            / "docs/analysis/product-v02323-schema9-contamination-audit.json"
        ).read_text(encoding="utf-8")
    )
    payload["schema9_inventory_matches_expected"] = False
    body = dict(payload)
    body.pop("audit_sha256")
    payload["audit_sha256"] = semantic_sha256_v22(body)

    with pytest.raises(ValueError):
        Schema9ContaminationAuditV02323.model_validate(payload)
