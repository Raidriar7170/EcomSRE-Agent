from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
from typing import Any

import pytest

from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22
from ecomsre.product.pilot import forensic_schema8_v02323 as forensics
from scripts.ci import verify_product_v02323_increment1 as increment1_verifier
from scripts.ci.verify_product_v02323_history import verify_product_v02323_history
from scripts.ci.verify_product_v02323_increment1 import (
    verify_product_v02323_increment1,
)


ROOT = Path(__file__).resolve().parents[2]


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _build_source(root: Path, *, schema_version: int = 8) -> Path:
    root.mkdir(parents=True)
    database = root / "product.sqlite3"
    connection = sqlite3.connect(database)
    try:
        connection.executescript(
            """
            CREATE TABLE schema_migrations (
                version INTEGER PRIMARY KEY,
                applied_at REAL NOT NULL
            );
            CREATE TABLE evidence_objects (
                object_sha256 TEXT PRIMARY KEY,
                byte_size INTEGER NOT NULL,
                media_type TEXT NOT NULL
            );
            CREATE TABLE facts (
                fact_id TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            """
        )
        connection.execute(
            "INSERT INTO schema_migrations(version, applied_at) VALUES (?, 1.0)",
            (schema_version,),
        )
        connection.execute("INSERT INTO facts VALUES ('fact-1', 'frozen')")
        object_payload = b'{"fact":"frozen"}\n'
        object_sha256 = _sha256(object_payload)
        connection.execute(
            "INSERT INTO evidence_objects VALUES (?, ?, ?)",
            (object_sha256, len(object_payload), "application/json"),
        )
        connection.commit()
    finally:
        connection.close()

    object_path = root / "objects" / "sha256" / object_sha256[:2]
    object_path.mkdir(parents=True)
    (object_path / f"{object_sha256}.json").write_bytes(object_payload)
    pilot = root / "pilot"
    pilot.mkdir()
    (pilot / "runtime-authority.json").write_text(
        '{"authority":"frozen"}\n', encoding="utf-8"
    )
    (pilot / "runtime-readiness.json").write_text(
        '{"readiness":"frozen"}\n', encoding="utf-8"
    )
    return root


def test_capture_copies_before_sqlite_open_and_preserves_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _build_source(tmp_path / "source")
    snapshot = tmp_path / "snapshots" / "snapshot-1"
    source_before = forensics.raw_tree_inventory_v02323(source)
    opened: list[Path] = []
    real_connect = sqlite3.connect

    def tracking_connect(database: Any, *args: Any, **kwargs: Any):
        locator = str(database)
        if locator.startswith("file:"):
            locator = locator.removeprefix("file:").split("?", maxsplit=1)[0]
        opened.append(Path(locator))
        return real_connect(database, *args, **kwargs)

    monkeypatch.setattr(forensics.sqlite3, "connect", tracking_connect)

    report = forensics.capture_forensic_source_snapshot_v02323(
        source,
        snapshot,
        source_locator=".local/predecessor/product",
        snapshot_locator=".local/product-v02323/forensics/raw-source/snapshot-1",
        captured_at="2026-08-31T03:45:08Z",
        owner_counter=lambda _database: 0,
    )

    assert opened
    assert all(snapshot in path.parents for path in opened)
    assert source not in opened
    assert report.source_owner_count == report.source_owner_count_after == 0
    assert report.source_schema_version == 8
    assert report.source_database_file_sha256 == report.snapshot_database_file_sha256
    assert report.source_object_inventory_sha256 == report.snapshot_object_inventory_sha256
    assert (
        report.source_runtime_file_inventory_sha256
        == report.snapshot_runtime_file_inventory_sha256
    )
    assert report.source_tree_inventory_sha256 == report.source_tree_inventory_sha256_after
    assert forensics.raw_tree_inventory_v02323(source) == source_before
    assert not (source / "product.sqlite3-wal").exists()
    assert not (source / "product.sqlite3-shm").exists()
    assert (snapshot.stat().st_mode & 0o222) == 0


def test_owner_blocks_before_snapshot_destination_is_created(tmp_path: Path) -> None:
    source = _build_source(tmp_path / "source")
    snapshot = tmp_path / "snapshots" / "snapshot-1"

    with pytest.raises(
        forensics.ForensicContractErrorV02323,
        match="BLOCKED_ECOMSRE_PRODUCT_V02323_SOURCE_OWNER",
    ):
        forensics.capture_forensic_source_snapshot_v02323(
            source,
            snapshot,
            source_locator=".local/predecessor/product",
            snapshot_locator=".local/product-v02323/forensics/raw-source/snapshot-1",
            captured_at="2026-08-31T03:45:08Z",
            owner_counter=lambda _database: 1,
        )

    assert not snapshot.exists()


def test_forensic_reader_is_read_only_and_creates_no_sidecars(tmp_path: Path) -> None:
    source = _build_source(tmp_path / "source")
    database = source / "product.sqlite3"
    before = _sha256(database.read_bytes())

    reader = forensics.ForensicSqliteReaderV02323(database)

    assert reader.schema_version() == 8
    assert reader.logical_database_sha256()
    with reader._connection() as connection:
        assert connection.execute("PRAGMA query_only").fetchone()[0] == 1
        with pytest.raises(sqlite3.OperationalError):
            connection.execute("CREATE TABLE forbidden(value TEXT)")
    assert _sha256(database.read_bytes()) == before
    assert not (source / "product.sqlite3-wal").exists()
    assert not (source / "product.sqlite3-shm").exists()


def test_wal_triplet_stays_raw_and_consistent_backup_is_bound(tmp_path: Path) -> None:
    live = _build_source(tmp_path / "live")
    connection = sqlite3.connect(live / "product.sqlite3")
    try:
        assert connection.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
        connection.execute("PRAGMA wal_autocheckpoint=0")
        connection.execute("INSERT INTO facts VALUES ('fact-wal', 'from-wal')")
        connection.commit()
        assert (live / "product.sqlite3-wal").is_file()
        assert (live / "product.sqlite3-shm").is_file()

        source = tmp_path / "source"
        source.mkdir()
        for name in ("product.sqlite3", "product.sqlite3-wal", "product.sqlite3-shm"):
            shutil.copy2(live / name, source / name)
        shutil.copytree(live / "objects", source / "objects")
        shutil.copytree(live / "pilot", source / "pilot")
    finally:
        connection.close()

    snapshot = tmp_path / "snapshots" / "snapshot-wal"
    raw_before = {
        name: _sha256((source / name).read_bytes())
        for name in ("product.sqlite3", "product.sqlite3-wal", "product.sqlite3-shm")
    }
    report = forensics.capture_forensic_source_snapshot_v02323(
        source,
        snapshot,
        source_locator=".local/predecessor/product",
        snapshot_locator=".local/product-v02323/forensics/raw-source/snapshot-wal",
        captured_at="2026-08-31T04:05:35Z",
        owner_counter=lambda _database: 0,
    )

    assert report.source_wal_present is True
    assert report.source_shm_present is True
    assert report.snapshot_consistent_database_present is True
    assert report.snapshot_consistent_database_file_sha256
    assert {
        name: _sha256((snapshot / "product" / name).read_bytes())
        for name in raw_before
    } == raw_before
    consistent = snapshot / "consistent-image" / "product.sqlite3"
    with sqlite3.connect(
        f"file:{consistent.as_posix()}?mode=ro&immutable=1", uri=True
    ) as replay:
        assert replay.execute(
            "SELECT value FROM facts WHERE fact_id = 'fact-wal'"
        ).fetchone()[0] == "from-wal"


def test_private_snapshot_verifier_rehashes_tree_and_requires_read_only(
    tmp_path: Path,
) -> None:
    source = _build_source(tmp_path / "source")
    snapshot_root = tmp_path / "snapshots" / "snapshot-verified"
    report = forensics.capture_forensic_source_snapshot_v02323(
        source,
        snapshot_root,
        source_locator=".local/predecessor/product",
        snapshot_locator=(
            ".local/product-v02323/forensics/raw-source/snapshot-verified"
        ),
        captured_at="2026-08-31T04:11:59Z",
        owner_counter=lambda _database: 0,
    )

    assert forensics.verify_forensic_snapshot_artifact_v02323(
        snapshot_root, report
    )

    database = snapshot_root / "product" / "product.sqlite3"
    os.chmod(database, database.stat().st_mode | 0o200)
    with pytest.raises(
        forensics.ForensicContractErrorV02323,
        match="BLOCKED_ECOMSRE_PRODUCT_V02323_FORENSIC_SNAPSHOT",
    ):
        forensics.verify_forensic_snapshot_artifact_v02323(snapshot_root, report)

    database.write_bytes(database.read_bytes() + b"tampered")
    os.chmod(database, database.stat().st_mode & ~0o222)
    with pytest.raises(
        forensics.ForensicContractErrorV02323,
        match="BLOCKED_ECOMSRE_PRODUCT_V02323_FORENSIC_SNAPSHOT",
    ):
        forensics.verify_forensic_snapshot_artifact_v02323(snapshot_root, report)


def _digest_event(
    *,
    digest: str = "1" * 64,
    source_locator: str = ".local/predecessor/product",
    exit_code: int = 0,
) -> bytes:
    absolute = f"/private/worktree/{source_locator}/product.sqlite3"
    command = f"find /private/worktree/{source_locator} -exec shasum -a 256 {{}} \\;"
    return (
        json.dumps(
            {
                "type": "event_msg",
                "payload": {
                    "type": "item_completed",
                    "item": {
                        "type": "CommandExecution",
                        "source": "unified_exec_startup",
                        "status": "completed",
                        "exit_code": exit_code,
                        "stderr": "",
                        "command": ["/bin/zsh", "-lc", command],
                        "stdout": f"{digest}  {absolute}\n",
                    },
                },
            }
        )
        + "\n"
    ).encode("utf-8")


def test_digest_event_parser_binds_command_status_path_and_raw_sha() -> None:
    digest = "1" * 64
    source_locator = ".local/predecessor/product"
    assert forensics.extract_raw_sqlite_digest_event_v02323(
        _digest_event(digest=digest, source_locator=source_locator),
        expected_digest_full=digest,
        source_locator=source_locator,
    ) == {"source_database_file_sha256": digest}

    for event in (
        _digest_event(digest=digest, source_locator=source_locator, exit_code=1),
        _digest_event(digest=digest, source_locator=".local/other/product"),
        _digest_event(digest="2" * 64, source_locator=source_locator),
    ):
        with pytest.raises(
            forensics.ForensicContractErrorV02323,
            match="BLOCKED_ECOMSRE_PRODUCT_V02323_DIGEST_SEMANTICS",
        ):
            forensics.extract_raw_sqlite_digest_event_v02323(
                event,
                expected_digest_full=digest,
                source_locator=source_locator,
            )


def test_increment1_verifier_rejects_missing_private_snapshot(tmp_path: Path) -> None:
    with pytest.raises((FileNotFoundError, ValueError)):
        verify_product_v02323_increment1(ROOT, private_root=tmp_path)


@pytest.mark.parametrize(
    ("section", "field", "replacement"),
    (
        (
            "pr82",
            "safe_error_code",
            "OTHER_ERROR",
        ),
        (
            "pr83",
            "formal_blocker_semantic_sha256",
            "0" * 64,
        ),
        (
            "pr84",
            "completed_terminals",
            ["ECOMSRE_PRODUCT_V02322_HISTORY_AND_BLOCKER_PASS"],
        ),
        ("counters", "fault_attempts", 1),
        ("authority", "product_action_authority", "MUTATE"),
    ),
)
def test_history_verifier_rejects_resealed_required_claim_changes(
    tmp_path: Path,
    section: str,
    field: str,
    replacement: object,
) -> None:
    manifest = json.loads(
        (ROOT / "config/product-v02323/historical-results.v1.json").read_text(
            encoding="utf-8"
        )
    )
    if section in {"pr82", "pr83", "pr84"}:
        manifest["predecessors"][section][field] = replacement
    else:
        manifest[section][field] = replacement
    body = dict(manifest)
    body.pop("manifest_sha256")
    manifest["manifest_sha256"] = semantic_sha256_v22(body)
    changed = tmp_path / "historical-results.v1.json"
    changed.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ValueError, match="predecessor history differs"):
        verify_product_v02323_history(ROOT, manifest_path=changed)


@pytest.mark.parametrize(
    ("field", "replacement"),
    (
        ("schema_version", "other.schema"),
        ("goal_version", "other-goal"),
        ("terminals", []),
        ("next_gate", "OTHER_GATE"),
        ("fault_attempt_count", 1),
        ("new_baseline_attempt_count", 1),
        ("provider_calls", 1),
        ("agent_writes", 1),
        ("runbook_executions", 1),
    ),
)
def test_exact_progress_contract_rejects_resealed_claim_changes(
    field: str,
    replacement: object,
) -> None:
    progress = json.loads(
        (ROOT / "docs/analysis/product-v02323-progress.json").read_text(
            encoding="utf-8"
        )
    )
    expected_body = dict(progress)
    expected_body.pop("progress_sha256")
    progress[field] = replacement
    changed_body = dict(progress)
    changed_body.pop("progress_sha256")
    progress["progress_sha256"] = semantic_sha256_v22(changed_body)

    with pytest.raises(ValueError, match="progress differs"):
        increment1_verifier._require_exact_sealed_artifact(
            progress,
            seal_field="progress_sha256",
            expected_body=expected_body,
            label="progress",
        )


@pytest.mark.parametrize(
    ("source_field", "expected_kind"),
    (
        (
            "source_database_file_sha256",
            forensics.ProductStateDigestKindV02323.RAW_SQLITE_FILE_SHA256,
        ),
        (
            "source_database_logical_sha256",
            forensics.ProductStateDigestKindV02323.CANONICAL_DATABASE_LOGICAL_SHA256,
        ),
        (
            "source_sha256",
            forensics.ProductStateDigestKindV02323.PRODUCT_STATE_SEMANTIC_SHA256,
        ),
        (
            "source_object_inventory_sha256",
            forensics.ProductStateDigestKindV02323.OBJECT_INVENTORY_SHA256,
        ),
        (
            "source_runtime_file_inventory_sha256",
            forensics.ProductStateDigestKindV02323.RUNTIME_INVENTORY_SHA256,
        ),
    ),
)
def test_digest_semantics_are_classified_by_source_field(
    source_field: str,
    expected_kind: forensics.ProductStateDigestKindV02323,
) -> None:
    expected = "1" * 64
    source_artifact = {source_field: expected}

    audit = forensics.build_product_state_digest_semantics_audit_v02323(
        expected_digest_full=expected,
        observed_contaminated_digest_full="2" * 64,
        expected_digest_source_artifact="private:test-artifact",
        expected_digest_source_field=source_field,
        expected_digest_source_artifact_bytes=b"frozen artifact bytes",
        expected_digest_source_payload=source_artifact,
        raw_digest_function_source=b"def raw(): ...\n",
        logical_digest_function_source=b"def logical(): ...\n",
        state_digest_function_source=b"class State: ...\n",
    )

    assert audit.expected_digest_kind is expected_kind
    assert audit.logical_reconstruction_permitted is True
    assert audit.audit_sha256


def test_unknown_digest_source_field_fails_closed() -> None:
    with pytest.raises(
        forensics.ForensicContractErrorV02323,
        match="BLOCKED_ECOMSRE_PRODUCT_V02323_DIGEST_SEMANTICS",
    ):
        forensics.build_product_state_digest_semantics_audit_v02323(
            expected_digest_full="1" * 64,
            observed_contaminated_digest_full="2" * 64,
            expected_digest_source_artifact="private:test-artifact",
            expected_digest_source_field="displayed_sha256",
            expected_digest_source_artifact_bytes=b"frozen artifact bytes",
            expected_digest_source_payload={"displayed_sha256": "1" * 64},
            raw_digest_function_source=b"def raw(): ...\n",
            logical_digest_function_source=b"def logical(): ...\n",
            state_digest_function_source=b"class State: ...\n",
        )


def test_public_increment1_artifacts_do_not_expose_private_session_locators() -> None:
    public_paths = (
        ROOT / "config/product-v02323/historical-results.v1.json",
        ROOT / "docs/analysis/product-v02323-forensic-source-snapshot.json",
        ROOT / "docs/analysis/product-v02323-digest-semantics.json",
        ROOT / "docs/analysis/product-v02323-digest-semantics.md",
        ROOT / "docs/analysis/product-v02323-predecessor-audit.json",
        ROOT / "docs/analysis/product-v02323-progress.json",
    )
    for path in public_paths:
        payload = path.read_text(encoding="utf-8")
        assert "/Users/" not in payload
        assert "source_thread_id" not in payload
        assert "source_item_id" not in payload
        assert "rollout-2026" not in payload
