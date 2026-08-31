from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from ecomsre.product.pilot.product_state_clone_v0232 import (
    ProductStateCloneErrorV0232,
    ProductStateSourceV0232,
    admit_product_state_source_v0232,
    clone_product_state_v0232,
)
from ecomsre.product.connectors.pilot_runtime import PilotRuntimeSnapshotV02
from ecomsre.product.pilot.runtime_authority_v02 import PilotRuntimeAuthorityV02
from ecomsre.product.storage.object_store import ContentAddressedObjectStoreV1
from ecomsre.product.storage.sqlite_store import SqliteStoreV1
from scripts.ci.verify_product_v0232_history import (
    verify_product_v0232_history,
    verify_product_v0232_written_reports,
)
from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22
from scripts.product_v0232.run_state_clone import (
    _require_fixed_source_root,
    _require_live_clone_report_binding,
)


ROOT = Path(__file__).resolve().parents[2]
SHA = "a" * 64
ENVIRONMENT_ID = "env-" + "1" * 24
BASELINE_ID = "base-" + "2" * 24
BASELINE_SHA256 = "b" * 64
PROFILE_SHA256 = "b9577dfc4eaa933b62048bbcbd041ed470343f7c76255ab851cdcaeef60a7df2"
SOURCE_LOCATOR = ".local/product-v023/baseline-readiness/runs/source/product"


def _json(payload: object) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _build_source_state(root: Path) -> Path:
    root.mkdir(parents=True)
    store = SqliteStoreV1(root / "product.sqlite3")
    object_store = ContentAddressedObjectStoreV1(root / "objects", metadata_store=store)
    object_store.put_json({"kind": "fixture-evidence", "value": 1})
    with store.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            "INSERT INTO environments(environment_id, name, description, timezone, "
            "service_identity_policy_json, explicit_service_catalog_json, created_at, "
            "updated_at) VALUES (?, 'fixture', '', 'UTC', '{}', '[]', ?, ?)",
            (ENVIRONMENT_ID, "2026-08-30T00:00:00+00:00", "2026-08-30T00:00:00+00:00"),
        )
        connection.execute(
            "INSERT INTO connector_configs(connector_config_id, environment_id, name, "
            "kind, endpoint, settings_json, credential_refs_json, created_at) "
            "VALUES ('conn-opensearch', ?, 'opensearch', 'OPENSEARCH', NULL, ?, '[]', ?)",
            (
                ENVIRONMENT_ID,
                _json(
                    {
                        "mode": "PROFILE_BOUND",
                        "profile_binding": {
                            "profile_status": "ACTIVE",
                            "selected_candidate_alias": "P01",
                            "profile_sha256": PROFILE_SHA256,
                        },
                    }
                ),
                "2026-08-30T00:00:00+00:00",
            ),
        )
        connection.execute(
            "INSERT INTO baseline_versions(baseline_id, environment_id, payload_json, "
            "active, created_at) VALUES (?, ?, ?, 1, ?)",
            (
                BASELINE_ID,
                ENVIRONMENT_ID,
                _json(
                    {
                        "baseline_id": BASELINE_ID,
                        "baseline_sha256": BASELINE_SHA256,
                        "environment_id": ENVIRONMENT_ID,
                    }
                ),
                "2026-08-30T00:00:00+00:00",
            ),
        )
        jobs = (
            ("job-build", "BASELINE_BUILD"),
            ("job-verify", "ENVIRONMENT_VERIFY"),
            ("job-diagnosis", "DIAGNOSIS"),
        )
        for ordinal, (job_id, job_type) in enumerate(jobs, start=1):
            connection.execute(
                "INSERT INTO diagnosis_jobs(job_id, job_type, status, payload_json, "
                "result_json, safe_error_code, idempotency_key, claimed_by, "
                "lease_expires_at, attempt_count, created_at, updated_at) "
                "VALUES (?, ?, 'SUCCEEDED', '{}', '{}', NULL, ?, NULL, NULL, 1, ?, ?)",
                (job_id, job_type, f"fixture-{job_type}", float(ordinal), float(ordinal)),
            )
        connection.execute(
            "INSERT INTO incidents(incident_id, environment_id, external_incident_key, "
            "payload_json, created_at) VALUES ('inc-fixture', ?, 'fixture', '{}', ?)",
            (ENVIRONMENT_ID, "2026-08-30T00:00:00+00:00"),
        )
        connection.execute(
            "INSERT INTO diagnosis_results(diagnosis_id, incident_id, payload_json, "
            "created_at) VALUES ('diag-fixture', 'inc-fixture', '{}', ?)",
            ("2026-08-30T00:00:00+00:00",),
        )
        connection.execute("COMMIT")
    with store.connect() as connection:
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    for suffix in ("-wal", "-shm"):
        (root / f"product.sqlite3{suffix}").unlink(missing_ok=True)
    pilot = root / "pilot"
    pilot.mkdir()
    runtime_authority = PilotRuntimeAuthorityV02.build(
        environment_id=ENVIRONMENT_ID,
        allowed_logical_services=("checkout",),
        profile_sha256="c" * 64,
        daemon_identity_sha256="d" * 64,
        docker_context_sha256="e" * 64,
        config_bundle_sha256="f" * 64,
        resolved_sandbox_sha256="1" * 64,
        resolved_endpoints_sha256="2" * 64,
        ownership_scope_sha256="3" * 64,
    )
    runtime_snapshot = PilotRuntimeSnapshotV02.build(
        environment_id=ENVIRONMENT_ID,
        authority_sha256=runtime_authority.connector_binding_sha256,
        observed_at=datetime(2026, 8, 30, tzinfo=UTC),
        services={
            "checkout": {"state": "RUNNING", "healthy": True, "restart_count": 0}
        },
    )
    (pilot / "runtime-authority.json").write_text(
        _json(runtime_authority.model_dump(mode="json")),
        encoding="utf-8",
    )
    (pilot / "runtime-readiness.json").write_text(
        _json(runtime_snapshot.model_dump(mode="json")),
        encoding="utf-8",
    )
    return root


def test_v0232_history_binds_frozen_v0231_result() -> None:
    result = verify_product_v0232_history(ROOT)

    assert result == {
        "terminal": "ECOMSRE_PRODUCT_V0232_HISTORY_VERIFIED",
        "starting_main": "73fe478886a4f0875b4d60b07b3600e8aae02132",
        "predecessor_head": "7ee7eca638edd388c8cba46e4092228fdbcc1008",
        "predecessor_terminal": "ECOMSRE_PRODUCT_V0231_NOFAULT_NOT_SUPPORTED",
        "tracked_file_count": 10,
    }


def test_v0232_history_rejects_reason_drift(tmp_path: Path) -> None:
    manifest = json.loads(
        (ROOT / "config/product-v0232/historical-results.v1.json").read_text(
            encoding="utf-8"
        )
    )
    manifest["predecessor"]["acceptance_reasons"] = ["SMOOTHED_SUCCESS"]
    drifted = tmp_path / "historical-results.v1.json"
    drifted.write_text(_json(manifest), encoding="utf-8")

    with pytest.raises(ValueError, match="predecessor identity differs"):
        verify_product_v0232_history(ROOT, manifest_path=drifted)


def test_v0232_history_rejects_role_path_substitution(tmp_path: Path) -> None:
    manifest = json.loads(
        (ROOT / "config/product-v0232/historical-results.v1.json").read_text(
            encoding="utf-8"
        )
    )
    manifest["tracked_files"][0]["role"] = "V0231_LIMITATIONS"
    drifted = tmp_path / "historical-results.v1.json"
    drifted.write_text(_json(manifest), encoding="utf-8")

    with pytest.raises(ValueError, match="historical binding differs"):
        verify_product_v0232_history(ROOT, manifest_path=drifted)


def test_v0232_written_reports_are_cross_bound() -> None:
    result = verify_product_v0232_written_reports(ROOT)

    assert result == {
        "terminal": "ECOMSRE_PRODUCT_V0232_HISTORY_AND_STATE_PASS",
        "source_clone_count": 1,
        "clone_sha256": "6920044cea06a68f38624803468aeeb0f854caee695f7f876ff2d6f6ef074205",
    }


def test_v0232_written_reports_reject_resealed_counter_drift(
    tmp_path: Path,
) -> None:
    progress = json.loads(
        (ROOT / "docs/analysis/product-v0232-progress.json").read_text(
            encoding="utf-8"
        )
    )
    progress["source_clone_count"] = 2
    progress["progress_sha256"] = semantic_sha256_v22(
        {key: value for key, value in progress.items() if key != "progress_sha256"}
    )
    drifted = tmp_path / "product-v0232-progress.json"
    drifted.write_text(_json(progress), encoding="utf-8")

    with pytest.raises(ValueError, match="written report binding differs"):
        verify_product_v0232_written_reports(ROOT, progress_path=drifted)


def test_v0232_written_reports_reject_resealed_source_mismatch(
    tmp_path: Path,
) -> None:
    clone = json.loads(
        (ROOT / "docs/analysis/product-v0232-product-state-clone.json").read_text(
            encoding="utf-8"
        )
    )
    clone["source_environment_id"] = "env-" + "4" * 24
    clone["destination_environment_id"] = "env-" + "4" * 24
    clone["clone_sha256"] = semantic_sha256_v22(
        {key: value for key, value in clone.items() if key != "clone_sha256"}
    )
    drifted = tmp_path / "product-v0232-product-state-clone.json"
    drifted.write_text(_json(clone), encoding="utf-8")

    with pytest.raises(ValueError, match="written report binding differs"):
        verify_product_v0232_written_reports(ROOT, clone_path=drifted)


def test_source_admission_and_online_backup_clone_are_exact(tmp_path: Path) -> None:
    source_root = _build_source_state(tmp_path / "source" / "product")
    source_before = hashlib.sha256((source_root / "product.sqlite3").read_bytes()).hexdigest()
    source = admit_product_state_source_v0232(
        source_root,
        source_locator=SOURCE_LOCATOR,
    )

    assert source.source_active_baseline_id == BASELINE_ID
    assert source.source_active_baseline_sha256 == BASELINE_SHA256
    assert source.source_profile_sha256 == PROFILE_SHA256
    assert source.source_counts.incident_count == 1
    assert source.source_counts.diagnosis_count == 1
    assert source.source_counts.pending_job_count == 0

    destination_root = tmp_path / "destination" / "product"
    clone = clone_product_state_v0232(
        source_root,
        destination_root,
        source_locator=SOURCE_LOCATOR,
        destination_locator=".local/product-v0232/product-state/clone-fixture/product",
    )

    assert clone.source_database_logical_sha256 == clone.destination_database_logical_sha256
    assert clone.source_object_inventory_sha256 == clone.destination_object_inventory_sha256
    assert clone.source_counts == clone.destination_counts
    assert clone.source_database_file_sha256_before == source_before
    assert clone.source_database_file_sha256_after == source_before
    assert hashlib.sha256((source_root / "product.sqlite3").read_bytes()).hexdigest() == source_before
    assert not (destination_root / "product.sqlite3-wal").exists()
    assert not (destination_root / "product.sqlite3-shm").exists()
    assert (destination_root / "pilot/runtime-authority.json").is_file()
    assert len(clone.clone_sha256) == 64


def test_existing_clone_reverification_rejects_live_source_physical_byte_drift(
    tmp_path: Path,
) -> None:
    source_root = _build_source_state(tmp_path / "source" / "product")
    source = admit_product_state_source_v0232(
        source_root,
        source_locator=SOURCE_LOCATOR,
    )
    destination_root = tmp_path / "destination" / "product"
    clone = clone_product_state_v0232(
        source_root,
        destination_root,
        source_locator=SOURCE_LOCATOR,
        destination_locator=(
            ".local/product-v0232/product-state/clone-fixture/product"
        ),
    )
    destination = admit_product_state_source_v0232(
        destination_root,
        source_locator=clone.destination_locator,
    )
    drifted_payload = source.model_dump(mode="json")
    drifted_payload["source_database_file_sha256"] = "c" * 64
    drifted_payload["source_sha256"] = semantic_sha256_v22(
        {
            key: value
            for key, value in drifted_payload.items()
            if key != "source_sha256"
        }
    )
    live_source_with_physical_byte_drift = ProductStateSourceV0232.model_validate(
        drifted_payload
    )

    with pytest.raises(
        ProductStateCloneErrorV0232,
        match="live source differs from sealed predecessor audit",
    ):
        _require_live_clone_report_binding(
            source=live_source_with_physical_byte_drift,
            destination=destination,
            audit_source=source,
            clone=clone,
        )


def test_source_admission_rejects_symlink_root(tmp_path: Path) -> None:
    source_root = _build_source_state(tmp_path / "real" / "product")
    linked = tmp_path / "linked-product"
    linked.symlink_to(source_root, target_is_directory=True)

    with pytest.raises(ProductStateCloneErrorV0232, match="symlink"):
        admit_product_state_source_v0232(linked, source_locator=SOURCE_LOCATOR)


def test_source_admission_rejects_sqlite_sidecars(tmp_path: Path) -> None:
    source_root = _build_source_state(tmp_path / "source" / "product")
    (source_root / "product.sqlite3-wal").write_bytes(b"not-authoritative")

    with pytest.raises(ProductStateCloneErrorV0232, match="sidecar"):
        admit_product_state_source_v0232(source_root, source_locator=SOURCE_LOCATOR)


def test_source_admission_rejects_runtime_environment_mismatch(tmp_path: Path) -> None:
    source_root = _build_source_state(tmp_path / "source" / "product")
    other_environment = "env-" + "4" * 24
    runtime_authority = PilotRuntimeAuthorityV02.build(
        environment_id=other_environment,
        allowed_logical_services=("checkout",),
        profile_sha256="c" * 64,
        daemon_identity_sha256="d" * 64,
        docker_context_sha256="e" * 64,
        config_bundle_sha256="f" * 64,
        resolved_sandbox_sha256="1" * 64,
        resolved_endpoints_sha256="2" * 64,
        ownership_scope_sha256="3" * 64,
    )
    runtime_snapshot = PilotRuntimeSnapshotV02.build(
        environment_id=other_environment,
        authority_sha256=runtime_authority.connector_binding_sha256,
        observed_at=datetime(2026, 8, 30, tzinfo=UTC),
        services={
            "checkout": {"state": "RUNNING", "healthy": True, "restart_count": 0}
        },
    )
    (source_root / "pilot/runtime-authority.json").write_text(
        _json(runtime_authority.model_dump(mode="json")), encoding="utf-8"
    )
    (source_root / "pilot/runtime-readiness.json").write_text(
        _json(runtime_snapshot.model_dump(mode="json")), encoding="utf-8"
    )

    with pytest.raises(ProductStateCloneErrorV0232, match="authority/snapshot"):
        admit_product_state_source_v0232(source_root, source_locator=SOURCE_LOCATOR)


def test_source_admission_rejects_unfrozen_runtime_authority(tmp_path: Path) -> None:
    source_root = _build_source_state(tmp_path / "source" / "product")

    with pytest.raises(ProductStateCloneErrorV0232, match="authority/snapshot"):
        admit_product_state_source_v0232(
            source_root,
            source_locator=SOURCE_LOCATOR,
            expected_pilot_runtime_authority_sha256="0" * 64,
        )


def test_source_admission_rejects_object_digest_drift(tmp_path: Path) -> None:
    source_root = _build_source_state(tmp_path / "source" / "product")
    object_path = next((source_root / "objects/sha256").glob("*/*.json"))
    object_path.write_bytes(b"drift")

    with pytest.raises(ProductStateCloneErrorV0232, match="object digest"):
        admit_product_state_source_v0232(source_root, source_locator=SOURCE_LOCATOR)


def test_source_admission_rejects_unknown_object_file(tmp_path: Path) -> None:
    source_root = _build_source_state(tmp_path / "source" / "product")
    (source_root / "objects/sha256/unknown.txt").write_text("unknown", encoding="utf-8")

    with pytest.raises(ProductStateCloneErrorV0232, match="object directory"):
        admit_product_state_source_v0232(source_root, source_locator=SOURCE_LOCATOR)


def test_source_admission_rejects_starting_count_drift(tmp_path: Path) -> None:
    source_root = _build_source_state(tmp_path / "source" / "product")
    connection = __import__("sqlite3").connect(source_root / "product.sqlite3")
    connection.execute("DELETE FROM diagnosis_results")
    connection.commit()
    connection.close()

    with pytest.raises(ProductStateCloneErrorV0232, match="starting counts"):
        admit_product_state_source_v0232(source_root, source_locator=SOURCE_LOCATOR)


def test_clone_destination_is_create_once(tmp_path: Path) -> None:
    source_root = _build_source_state(tmp_path / "source" / "product")
    destination_root = tmp_path / "destination" / "product"
    destination_root.mkdir(parents=True)

    with pytest.raises(ProductStateCloneErrorV0232, match="already exists"):
        clone_product_state_v0232(
            source_root,
            destination_root,
            source_locator=SOURCE_LOCATOR,
            destination_locator=(
                ".local/product-v0232/product-state/clone-fixture/product"
            ),
        )


def test_clone_rejects_destination_parent_symlink(tmp_path: Path) -> None:
    source_root = _build_source_state(tmp_path / "source" / "product")
    real_parent = tmp_path / "real-destination"
    real_parent.mkdir()
    linked_parent = tmp_path / "linked-destination"
    linked_parent.symlink_to(real_parent, target_is_directory=True)

    with pytest.raises(ProductStateCloneErrorV0232, match="destination path.*symlink"):
        clone_product_state_v0232(
            source_root,
            linked_parent / "clone-fixture" / "product",
            source_locator=SOURCE_LOCATOR,
            destination_locator=(
                ".local/product-v0232/product-state/clone-fixture/product"
            ),
        )


def test_runner_rejects_an_unbound_source_tree(tmp_path: Path) -> None:
    source_root = _build_source_state(tmp_path / "source" / "product")

    with pytest.raises(ProductStateCloneErrorV0232, match="fixed source"):
        _require_fixed_source_root(source_root)
