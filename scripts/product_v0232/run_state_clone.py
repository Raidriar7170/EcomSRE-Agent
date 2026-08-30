#!/usr/bin/env python3
"""Admit and clone the one preserved Product v0.2.3.1 state."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile
from typing import Any, Sequence

from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22
from ecomsre.product.pilot.product_state_clone_v0232 import (
    HISTORY_AND_STATE_PASS_V0232,
    ProductStateCloneV0232,
    ProductStateCloneErrorV0232,
    ProductStateSourceV0232,
    admit_product_state_source_v0232,
    clone_product_state_v0232,
)
from scripts.ci.verify_product_v0232_history import (
    SOURCE_LOCATOR_V0232,
    SOURCE_REPOSITORY_BRANCH_V0232,
    SOURCE_REPOSITORY_HEAD_V0232,
    expected_source_repository_binding_v0232,
    verify_product_v0232_history,
    verify_product_v0232_private_result,
    verify_product_v0232_written_reports,
)


ENVIRONMENT_ID_V0232 = "env-2b5c86f47f449acfc54cfcec"
BASELINE_ID_V0232 = "base-b25440a36089a8f0e6b9f1dc"
BASELINE_SHA256_V0232 = (
    "6d3d2d7a4854d1cfc2477746e7d0c940ed8a08644ebc69b7b91066eabe45ae64"
)
PROFILE_SHA256_V0232 = (
    "b9577dfc4eaa933b62048bbcbd041ed470343f7c76255ab851cdcaeef60a7df2"
)
PILOT_RUNTIME_AUTHORITY_SHA256_V0232 = (
    "bd1546ecdf961206d3c7a4c9c065bdb2882357da56dfd775ff5d6aed9edad57c"
)
RUNTIME_CONNECTOR_BINDING_SHA256_V0232 = (
    "ee49aaa2835b97645c639a3a9cae01471e51e6aa427e92f353d7b4fdf3840915"
)


def _require_fixed_source_root(source_root: Path) -> dict[str, object]:
    candidate = source_root.absolute()
    try:
        repository_root = Path(
            subprocess.run(
                ("git", "-C", str(candidate), "rev-parse", "--show-toplevel"),
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
        ).resolve(strict=True)
        head = subprocess.run(
            ("git", "-C", str(repository_root), "rev-parse", "HEAD"),
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        branch = subprocess.run(
            ("git", "-C", str(repository_root), "symbolic-ref", "--short", "HEAD"),
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        expected = (repository_root / SOURCE_LOCATOR_V0232).resolve(strict=True)
        observed = candidate.resolve(strict=True)
    except (OSError, subprocess.CalledProcessError) as error:
        raise ProductStateCloneErrorV0232(
            "fixed source Product-state locator cannot be resolved"
        ) from error
    if (
        observed != expected
        or head != SOURCE_REPOSITORY_HEAD_V0232
        or branch != SOURCE_REPOSITORY_BRANCH_V0232
    ):
        raise ProductStateCloneErrorV0232(
            "source Product state is not the fixed predecessor locator"
        )
    return expected_source_repository_binding_v0232()


def _require_source_unowned(database: Path) -> None:
    try:
        result = subprocess.run(
            ("lsof", "-F", "p", str(database)),
            check=False,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as error:
        raise ProductStateCloneErrorV0232(
            "lsof is required to prove the source Product state is unowned"
        ) from error
    if result.returncode not in {0, 1}:
        raise ProductStateCloneErrorV0232(
            "source Product-state ownership check failed"
        )
    if result.stdout.strip():
        raise ProductStateCloneErrorV0232(
            "a process still owns the source Product state"
        )


def _write_json_create_once(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() or path.is_symlink():
        raise FileExistsError(f"Product v0.2.3.2 report already exists: {path}")
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def run_state_clone(
    *,
    project_root: Path,
    source_root: Path,
    predecessor_private_acceptance: Path,
) -> dict[str, object]:
    root = project_root.resolve(strict=True)
    history = verify_product_v0232_history(root)
    private_history = verify_product_v0232_private_result(
        predecessor_private_acceptance
    )
    source_repository_binding = _require_fixed_source_root(source_root)
    _require_source_unowned(source_root / "product.sqlite3")
    source = admit_product_state_source_v0232(
        source_root,
        source_locator=SOURCE_LOCATOR_V0232,
        expected_environment_id=ENVIRONMENT_ID_V0232,
        expected_baseline_id=BASELINE_ID_V0232,
        expected_baseline_sha256=BASELINE_SHA256_V0232,
        expected_profile_sha256=PROFILE_SHA256_V0232,
        expected_pilot_runtime_authority_sha256=(
            PILOT_RUNTIME_AUTHORITY_SHA256_V0232
        ),
        expected_runtime_connector_binding_sha256=(
            RUNTIME_CONNECTOR_BINDING_SHA256_V0232
        ),
    )
    clone_id = f"clone-{source.source_sha256[:24]}"
    destination_locator = f".local/product-v0232/product-state/{clone_id}/product"
    destination_root = root / destination_locator
    clone = clone_product_state_v0232(
        source_root,
        destination_root,
        source_locator=SOURCE_LOCATOR_V0232,
        destination_locator=destination_locator,
        expected_environment_id=ENVIRONMENT_ID_V0232,
        expected_baseline_id=BASELINE_ID_V0232,
        expected_baseline_sha256=BASELINE_SHA256_V0232,
        expected_profile_sha256=PROFILE_SHA256_V0232,
        expected_pilot_runtime_authority_sha256=(
            PILOT_RUNTIME_AUTHORITY_SHA256_V0232
        ),
        expected_runtime_connector_binding_sha256=(
            RUNTIME_CONNECTOR_BINDING_SHA256_V0232
        ),
    )
    audit_body: dict[str, Any] = {
        "schema_version": "ecomsre.product.predecessor-audit.v0232",
        "terminal": HISTORY_AND_STATE_PASS_V0232,
        "history": history,
        "private_history": private_history,
        "source_state": source.model_dump(mode="json"),
        "source_repository_binding": source_repository_binding,
        "source_product_process_owner_count": 0,
        "source_clone_count": 1,
        "clone_sha256": clone.clone_sha256,
    }
    audit = {**audit_body, "audit_sha256": semantic_sha256_v22(audit_body)}
    progress_body: dict[str, Any] = {
        "schema_version": "ecomsre.product.progress.v0232",
        "terminal": HISTORY_AND_STATE_PASS_V0232,
        "increment": 1,
        "history_terminal": history["terminal"],
        "source_clone_count": 1,
        "offline_changed_iteration_count": 1,
        "live_traffic_preflight_attempt_count": 0,
        "formal_healthy_traffic_execution_count": 0,
        "accepted_successor_incident_count": 0,
        "successor_diagnosis_count": 0,
        "fault_attempt_count": 0,
        "knowledge_loop_campaign_count": 0,
        "agent_writes": 0,
        "runbook_executions": 0,
        "provider_calls": 0,
        "action_authority": "NONE",
        "clone_sha256": clone.clone_sha256,
    }
    progress = {
        **progress_body,
        "progress_sha256": semantic_sha256_v22(progress_body),
    }
    _write_json_create_once(
        root / "docs/analysis/product-v0232-predecessor-audit.json", audit
    )
    _write_json_create_once(
        root / "docs/analysis/product-v0232-product-state-clone.json",
        clone.model_dump(mode="json"),
    )
    _write_json_create_once(
        root / "docs/analysis/product-v0232-progress.json", progress
    )
    return {
        "terminal": HISTORY_AND_STATE_PASS_V0232,
        "source_clone_count": 1,
        "clone_sha256": clone.clone_sha256,
        "destination_locator": destination_locator,
    }


def _require_live_clone_report_binding(
    *,
    source: ProductStateSourceV0232,
    destination: ProductStateSourceV0232,
    audit_source: ProductStateSourceV0232,
    clone: ProductStateCloneV0232,
) -> None:
    if source != audit_source:
        raise ProductStateCloneErrorV0232(
            "live source differs from sealed predecessor audit"
        )
    clone_source = {
        "source_locator": clone.source_locator,
        "source_database_file_sha256": clone.source_database_file_sha256_before,
        "source_database_file_sha256_after": (
            clone.source_database_file_sha256_after
        ),
        "source_database_logical_sha256": clone.source_database_logical_sha256,
        "source_object_inventory_sha256": clone.source_object_inventory_sha256,
        "source_runtime_file_inventory_sha256": (
            clone.source_runtime_file_inventory_sha256
        ),
        "source_counts": clone.source_counts,
        "source_environment_id": clone.source_environment_id,
        "source_active_baseline_id": clone.source_active_baseline_id,
        "source_active_baseline_sha256": clone.source_active_baseline_sha256,
        "source_profile_sha256": clone.source_profile_sha256,
    }
    live_source = {
        "source_locator": source.source_locator,
        "source_database_file_sha256": source.source_database_file_sha256,
        "source_database_file_sha256_after": source.source_database_file_sha256,
        "source_database_logical_sha256": source.source_database_logical_sha256,
        "source_object_inventory_sha256": source.source_object_inventory_sha256,
        "source_runtime_file_inventory_sha256": (
            source.source_runtime_file_inventory_sha256
        ),
        "source_counts": source.source_counts,
        "source_environment_id": source.source_environment_id,
        "source_active_baseline_id": source.source_active_baseline_id,
        "source_active_baseline_sha256": source.source_active_baseline_sha256,
        "source_profile_sha256": source.source_profile_sha256,
    }
    if live_source != clone_source:
        raise ProductStateCloneErrorV0232(
            "live source differs from sealed clone report"
        )
    clone_destination = {
        "source_locator": clone.destination_locator,
        "source_database_logical_sha256": clone.destination_database_logical_sha256,
        "source_object_inventory_sha256": (
            clone.destination_object_inventory_sha256
        ),
        "source_runtime_file_inventory_sha256": (
            clone.destination_runtime_file_inventory_sha256
        ),
        "source_counts": clone.destination_counts,
        "source_environment_id": clone.destination_environment_id,
        "source_active_baseline_id": clone.destination_active_baseline_id,
        "source_active_baseline_sha256": clone.destination_active_baseline_sha256,
        "source_profile_sha256": clone.destination_profile_sha256,
    }
    live_destination = {
        "source_locator": destination.source_locator,
        "source_database_logical_sha256": (
            destination.source_database_logical_sha256
        ),
        "source_object_inventory_sha256": (
            destination.source_object_inventory_sha256
        ),
        "source_runtime_file_inventory_sha256": (
            destination.source_runtime_file_inventory_sha256
        ),
        "source_counts": destination.source_counts,
        "source_environment_id": destination.source_environment_id,
        "source_active_baseline_id": destination.source_active_baseline_id,
        "source_active_baseline_sha256": (
            destination.source_active_baseline_sha256
        ),
        "source_profile_sha256": destination.source_profile_sha256,
    }
    if live_destination != clone_destination:
        raise ProductStateCloneErrorV0232(
            "live destination differs from sealed clone report"
        )


def verify_existing_state_clone(
    *,
    project_root: Path,
    source_root: Path,
    predecessor_private_acceptance: Path,
) -> dict[str, object]:
    root = project_root.resolve(strict=True)
    verify_product_v0232_history(root)
    verify_product_v0232_private_result(predecessor_private_acceptance)
    _require_fixed_source_root(source_root)
    _require_source_unowned(source_root / "product.sqlite3")
    source = admit_product_state_source_v0232(
        source_root,
        source_locator=SOURCE_LOCATOR_V0232,
        expected_environment_id=ENVIRONMENT_ID_V0232,
        expected_baseline_id=BASELINE_ID_V0232,
        expected_baseline_sha256=BASELINE_SHA256_V0232,
        expected_profile_sha256=PROFILE_SHA256_V0232,
        expected_pilot_runtime_authority_sha256=(
            PILOT_RUNTIME_AUTHORITY_SHA256_V0232
        ),
        expected_runtime_connector_binding_sha256=(
            RUNTIME_CONNECTOR_BINDING_SHA256_V0232
        ),
    )
    try:
        audit_payload = json.loads(
            (root / "docs/analysis/product-v0232-predecessor-audit.json").read_text(
                encoding="utf-8"
            )
        )
        audit_source = ProductStateSourceV0232.model_validate(
            audit_payload.get("source_state")
        )
        clone = ProductStateCloneV0232.model_validate_json(
            (
                root / "docs/analysis/product-v0232-product-state-clone.json"
            ).read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError, ValueError) as error:
        raise ProductStateCloneErrorV0232(
            "existing clone reports cannot be admitted"
        ) from error
    destination_locator = clone.destination_locator
    destination = admit_product_state_source_v0232(
        root / destination_locator,
        source_locator=destination_locator,
        expected_environment_id=source.source_environment_id,
        expected_baseline_id=source.source_active_baseline_id,
        expected_baseline_sha256=source.source_active_baseline_sha256,
        expected_profile_sha256=source.source_profile_sha256,
        expected_pilot_runtime_authority_sha256=(
            PILOT_RUNTIME_AUTHORITY_SHA256_V0232
        ),
        expected_runtime_connector_binding_sha256=(
            RUNTIME_CONNECTOR_BINDING_SHA256_V0232
        ),
    )
    _require_live_clone_report_binding(
        source=source,
        destination=destination,
        audit_source=audit_source,
        clone=clone,
    )
    reports = verify_product_v0232_written_reports(root)
    return {
        **reports,
        "existing_clone_reverified": True,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--project-root", type=Path, default=Path(__file__).resolve().parents[2]
    )
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--predecessor-private-acceptance", type=Path, required=True)
    parser.add_argument("--verify-existing", action="store_true")
    arguments = parser.parse_args(argv)
    operation = verify_existing_state_clone if arguments.verify_existing else run_state_clone
    result = operation(
        project_root=arguments.project_root,
        source_root=arguments.source_root,
        predecessor_private_acceptance=arguments.predecessor_private_acceptance,
    )
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
