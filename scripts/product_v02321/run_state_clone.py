#!/usr/bin/env python3
"""Create and verify the fresh Product v0.2.3.2.1 preflight state clone."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any, Sequence

from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22
from ecomsre.product.pilot.product_state_clone_v0232 import (
    ProductStateCloneV0232,
    ProductStateSourceV0232,
    admit_product_state_source_v0232,
    clone_product_state_v0232,
)
from ecomsre.product.pilot.product_state_clone_v02321 import (
    PreflightStateCloneReportV02321,
)
from ecomsre_live_sandbox.contracts import canonical_json_bytes
from scripts.ci.verify_product_v0232_history import (
    SOURCE_LOCATOR_V0232,
    expected_source_repository_binding_v0232,
    verify_product_v0232_private_result,
)
from scripts.ci.verify_product_v02321_history import verify_product_v02321_history
from scripts.product_v0232.run_state_clone import (
    BASELINE_ID_V0232,
    BASELINE_SHA256_V0232,
    ENVIRONMENT_ID_V0232,
    PILOT_RUNTIME_AUTHORITY_SHA256_V0232,
    PROFILE_SHA256_V0232,
    RUNTIME_CONNECTOR_BINDING_SHA256_V0232,
    _require_fixed_source_root,
    _require_source_unowned,
)


def _write_create_once(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise ValueError("Product v0.2.3.2.1 clone report path is a symlink")
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(canonical_json_bytes(payload))
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def _admit_state(
    state_root: Path, *, locator: str
) -> ProductStateSourceV0232:
    return admit_product_state_source_v0232(
        state_root,
        source_locator=locator,
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


def _bind_existing_clone(
    *,
    source: ProductStateSourceV0232,
    destination: ProductStateSourceV0232,
    destination_locator: str,
) -> ProductStateCloneV0232:
    body: dict[str, object] = {
        "schema_version": "ecomsre.product.product-state-clone.v0232",
        "terminal": "ECOMSRE_PRODUCT_V0232_HISTORY_AND_STATE_PASS",
        "source_locator": source.source_locator,
        "source_database_file_sha256_before": source.source_database_file_sha256,
        "source_database_file_sha256_after": source.source_database_file_sha256,
        "source_database_logical_sha256": source.source_database_logical_sha256,
        "source_object_inventory_sha256": source.source_object_inventory_sha256,
        "source_runtime_file_inventory_sha256": (
            source.source_runtime_file_inventory_sha256
        ),
        "source_counts": source.source_counts.model_dump(mode="json"),
        "source_environment_id": source.source_environment_id,
        "source_active_baseline_id": source.source_active_baseline_id,
        "source_active_baseline_sha256": source.source_active_baseline_sha256,
        "source_profile_sha256": source.source_profile_sha256,
        "destination_locator": destination_locator,
        "destination_database_logical_sha256": (
            destination.source_database_logical_sha256
        ),
        "destination_object_inventory_sha256": (
            destination.source_object_inventory_sha256
        ),
        "destination_runtime_file_inventory_sha256": (
            destination.source_runtime_file_inventory_sha256
        ),
        "destination_counts": destination.source_counts.model_dump(mode="json"),
        "destination_environment_id": destination.source_environment_id,
        "destination_active_baseline_id": destination.source_active_baseline_id,
        "destination_active_baseline_sha256": (
            destination.source_active_baseline_sha256
        ),
        "destination_profile_sha256": destination.source_profile_sha256,
    }
    return ProductStateCloneV0232.model_validate(
        {**body, "clone_sha256": semantic_sha256_v22(body)}
    )


def _load_frozen_source_state(root: Path) -> ProductStateSourceV0232:
    payload = json.loads(
        (root / "docs/analysis/product-v0232-predecessor-audit.json").read_bytes()
    )
    if not isinstance(payload, dict):
        raise ValueError("Product v0.2.3.2 predecessor audit differs")
    return ProductStateSourceV0232.model_validate(payload.get("source_state"))


def create_preflight_state_clone_v02321(
    *,
    project_root: Path,
    source_root: Path,
    predecessor_private_acceptance: Path,
) -> PreflightStateCloneReportV02321:
    root = Path(project_root).resolve(strict=True)
    source_product = Path(source_root).resolve(strict=True)
    report_path = (
        root / "docs/analysis/product-v02321-product-state-clone-preflight.json"
    )
    if report_path.exists() or report_path.is_symlink():
        raise FileExistsError("Product v0.2.3.2.1 preflight clone report exists")

    verify_product_v02321_history(root)
    private_acceptance = verify_product_v0232_private_result(
        predecessor_private_acceptance
    )
    source_repository_binding = _require_fixed_source_root(source_product)
    if source_repository_binding != expected_source_repository_binding_v0232():
        raise ValueError("Product v0.2.3.2.1 source repository binding differs")
    _require_source_unowned(source_product / "product.sqlite3")
    source = _admit_state(source_product, locator=SOURCE_LOCATOR_V0232)
    if source != _load_frozen_source_state(root):
        raise ValueError("Product v0.2.3.2.1 frozen source state differs")
    destination_locator = (
        ".local/product-v02321/product-state/"
        f"preflight-{source.source_sha256[:24]}/product"
    )
    destination_root = root / destination_locator
    if destination_root.exists() and not destination_root.is_symlink():
        destination_state = _admit_state(
            destination_root, locator=destination_locator
        )
        clone = _bind_existing_clone(
            source=source,
            destination=destination_state,
            destination_locator=destination_locator,
        )
    else:
        clone = clone_product_state_v0232(
            source_product,
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
        destination_state = _admit_state(
            destination_root, locator=destination_locator
        )
    report = PreflightStateCloneReportV02321.build(
        source_repository_binding=source_repository_binding,
        predecessor_private_acceptance=private_acceptance,
        source_state=source.model_dump(mode="json"),
        clone=clone.model_dump(mode="json"),
        destination_state=destination_state.model_dump(mode="json"),
        destination_locator=destination_locator,
        source_incident_count=source.source_counts.incident_count,
        source_diagnosis_count=source.source_counts.diagnosis_count,
        fault_family_count=source.source_counts.fault_family_count,
        knowledge_artifact_count=source.source_counts.knowledge_artifact_count,
    )
    _write_create_once(report_path, report.model_dump(mode="json"))
    return report


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--project-root",
        type=Path,
        default=Path(__file__).resolve().parents[2],
    )
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument(
        "--predecessor-private-acceptance", type=Path, required=True
    )
    arguments = parser.parse_args(argv)
    result = create_preflight_state_clone_v02321(
        project_root=arguments.project_root,
        source_root=arguments.source_root,
        predecessor_private_acceptance=arguments.predecessor_private_acceptance,
    )
    print(result.model_dump_json())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = (
    "create_preflight_state_clone_v02321",
)
