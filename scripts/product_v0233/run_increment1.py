#!/usr/bin/env python3
"""Run the v0.2.3.3 handoff, source-selection, and temporary clone checkpoint."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from typing import Any, Sequence

from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22
from ecomsre.product.pilot.fresh_formal_source_v0233 import (
    SOURCE_AND_CLONE_CONTRACT_PASS_V0233,
    FreshFormalSourceSelectionErrorV0233,
    admit_fresh_formal_source_v0233,
    clone_fresh_formal_state_v0233,
    configured_source_candidates_v0233,
    select_fresh_formal_source_v0233,
)
from scripts.ci.verify_product_v0233_history import (
    HISTORY_AND_HANDOFF_PASS_V0233,
    verify_product_v0233_history,
)


GOAL_VERSION_V0233 = (
    "ecomsre-product-v0233-fresh-formal-evidence-bound-nofault-v1"
)
_ATTEMPT_ID = re.compile(r"^[0-9]{8}T[0-9]{6}Z-[a-z0-9][a-z0-9-]{0,39}$")


def _database_owner_count(database: Path) -> int:
    try:
        result = subprocess.run(
            ("lsof", "-F", "p", "--", str(database)),
            check=False,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as error:
        raise FreshFormalSourceSelectionErrorV0233(
            "lsof is required to prove the Product-state source is unowned"
        ) from error
    if result.returncode not in {0, 1}:
        raise FreshFormalSourceSelectionErrorV0233(
            "Product-state source ownership check failed"
        )
    return sum(1 for line in result.stdout.splitlines() if line.startswith("p"))


def _write_json_create_once(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() or path.is_symlink():
        raise FileExistsError(f"Product v0.2.3.3 artifact already exists: {path}")
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


def _seal(body: dict[str, Any], field: str) -> dict[str, Any]:
    return {**body, field: semantic_sha256_v22(body)}


def run_increment1(
    *,
    project_root: Path,
    preferred_root: Path,
    fallback_root: Path,
    attempt_id: str,
) -> dict[str, object]:
    if _ATTEMPT_ID.fullmatch(attempt_id) is None:
        raise ValueError("Product v0.2.3.3 Increment 1 attempt ID differs")
    root = project_root.resolve(strict=True)
    history = verify_product_v0233_history(root)
    preferred, fallback = configured_source_candidates_v0233(
        preferred_root=preferred_root,
        fallback_root=fallback_root,
    )
    selection = select_fresh_formal_source_v0233(
        preferred=preferred,
        fallback=fallback,
        owner_counter=_database_owner_count,
    )
    selected_root = (
        preferred.source_root
        if selection.source_kind == preferred.source_kind
        else fallback.source_root
    )
    source_owner_count_before = _database_owner_count(
        selected_root / "product.sqlite3"
    )
    checkpoint_locator = (
        f".local/product-v0233/contract-checkpoints/{attempt_id}/product"
    )
    checkpoint_root = root / checkpoint_locator
    clone = clone_fresh_formal_state_v0233(
        selection=selection,
        source_root=selected_root,
        destination_root=checkpoint_root,
        destination_locator=checkpoint_locator,
        owner_counter=_database_owner_count,
    )
    checkpoint_attempt_root = checkpoint_root.parent
    shutil.rmtree(checkpoint_attempt_root)
    if checkpoint_attempt_root.exists() or checkpoint_attempt_root.is_symlink():
        raise RuntimeError("Product v0.2.3.3 temporary clone cleanup differs")
    source_after = admit_fresh_formal_source_v0233(
        preferred if selection.source_kind == preferred.source_kind else fallback,
        owner_counter=_database_owner_count,
        selection_reason=selection.selection_reason,
    )
    source_owner_count_after = _database_owner_count(
        selected_root / "product.sqlite3"
    )
    if (
        source_after != selection
        or source_owner_count_before != 0
        or source_owner_count_after != 0
    ):
        raise RuntimeError("Product v0.2.3.3 source changed during checkpoint")

    predecessor_audit_body: dict[str, Any] = {
        "schema_version": "ecomsre.product.predecessor-audit.v0233",
        "goal_version": GOAL_VERSION_V0233,
        "terminal": HISTORY_AND_HANDOFF_PASS_V0233,
        "history": history,
        "predecessor_artifacts_unchanged": True,
        "formal_clone_count": 0,
        "formal_execution_count": 0,
        "new_incident_count": 0,
        "new_diagnosis_count": 0,
        "measured_result_count": 0,
    }
    predecessor_audit = _seal(predecessor_audit_body, "audit_sha256")

    selection_audit_body: dict[str, Any] = {
        "schema_version": "ecomsre.product.source-selection-audit.v0233",
        "goal_version": GOAL_VERSION_V0233,
        "terminal": SOURCE_AND_CLONE_CONTRACT_PASS_V0233,
        "selection": selection.model_dump(mode="json"),
        "preferred_source_locator": preferred.source_locator,
        "fallback_source_locator": fallback.source_locator,
        "preferred_source_selected": selection.source_kind == preferred.source_kind,
        "source_owner_count_before": source_owner_count_before,
        "source_owner_count_after": source_owner_count_after,
        "source_unchanged": True,
        "structural_replay_output_used": False,
        "schema9_contaminated_source_used": False,
    }
    selection_audit = _seal(selection_audit_body, "audit_sha256")

    clone_contract_body: dict[str, Any] = {
        "schema_version": "ecomsre.product.clone-contract.v0233",
        "goal_version": GOAL_VERSION_V0233,
        "terminal": SOURCE_AND_CLONE_CONTRACT_PASS_V0233,
        "checkpoint_kind": "TEMPORARY_TEST_CLONE",
        "clone": clone.model_dump(mode="json"),
        "source_unchanged": True,
        "temporary_clone_removed": True,
        "authoritative_formal_clone_count": 0,
        "formal_execution_count": 0,
        "new_incident_count": 0,
        "new_diagnosis_count": 0,
        "measured_result_count": 0,
    }
    clone_contract = _seal(clone_contract_body, "contract_sha256")

    progress_body: dict[str, Any] = {
        "schema_version": "ecomsre.product.progress.v0233",
        "goal_version": GOAL_VERSION_V0233,
        "phase": "INCREMENT_1_SOURCE_AND_CLONE_CONTRACT_PASS",
        "current_terminal": SOURCE_AND_CLONE_CONTRACT_PASS_V0233,
        "history_terminal": HISTORY_AND_HANDOFF_PASS_V0233,
        "history_audit_sha256": predecessor_audit["audit_sha256"],
        "source_selection_sha256": selection.selection_sha256,
        "source_selection_audit_sha256": selection_audit["audit_sha256"],
        "clone_contract_sha256": clone_contract["contract_sha256"],
        "formal_clone_count": 0,
        "live_traffic_preflight_count": 0,
        "formal_execution_count": 0,
        "formal_transaction_count": 0,
        "new_incident_count": 0,
        "new_diagnosis_count": 0,
        "measured_result_count": 0,
        "new_baseline_attempts": 0,
        "profile_changes": 0,
        "fault_attempts": 0,
        "knowledge_loop_campaigns": 0,
        "provider_agent_runbook_calls": 0,
        "action_authority": "NONE",
        "next_gate": "ECOMSRE_PRODUCT_V0233_FORMAL_CONTRACT_PREFLIGHT_PASS",
    }
    progress = _seal(progress_body, "progress_sha256")

    outputs = {
        root / "config/product-v0233/source-selection.json": selection.model_dump(
            mode="json"
        ),
        root
        / "docs/analysis/product-v0233-predecessor-audit.json": predecessor_audit,
        root / "docs/analysis/product-v0233-source-selection.json": selection_audit,
        root / "docs/analysis/product-v0233-clone-contract.json": clone_contract,
        root / "docs/analysis/product-v0233-progress.json": progress,
    }
    if any(path.exists() or path.is_symlink() for path in outputs):
        raise FileExistsError("Product v0.2.3.3 Increment 1 output already exists")
    for path, payload in outputs.items():
        _write_json_create_once(path, payload)

    return {
        "terminal": SOURCE_AND_CLONE_CONTRACT_PASS_V0233,
        "history_terminal": HISTORY_AND_HANDOFF_PASS_V0233,
        "source_kind": selection.source_kind.value,
        "source_selection_sha256": selection.selection_sha256,
        "clone_contract_sha256": clone_contract["contract_sha256"],
        "temporary_clone_removed": True,
        "formal_clone_count": 0,
        "formal_execution_count": 0,
        "new_incident_count": 0,
        "new_diagnosis_count": 0,
        "measured_result_count": 0,
        "action_authority": "NONE",
    }


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--preferred-root", type=Path, required=True)
    parser.add_argument("--fallback-root", type=Path, required=True)
    parser.add_argument("--attempt-id", required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parse_args(argv)
    result = run_increment1(
        project_root=arguments.project_root,
        preferred_root=arguments.preferred_root,
        fallback_root=arguments.fallback_root,
        attempt_id=arguments.attempt_id,
    )
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ("run_increment1",)
