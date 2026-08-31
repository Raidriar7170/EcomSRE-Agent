#!/usr/bin/env python3
"""Verify the local/private Product v0.2.3.2.3 Increment 1 closure boundary."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
from typing import Any, Sequence

from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22
from ecomsre.product.pilot.forensic_schema8_v02323 import (
    ForensicRawSourceSnapshotV02323,
    ProductStateDigestKindV02323,
    ProductStateDigestSemanticsAuditV02323,
    extract_raw_sqlite_digest_event_v02323,
    verify_forensic_snapshot_artifact_v02323,
    verify_forensic_source_immutability_v02323,
)
from scripts.ci.verify_product_v02323_history import (
    EXPECTED_SCHEMA8_RAW_SHA256_V02323,
    OBSERVED_SCHEMA9_RAW_SHA256_V02323,
    PR83_HEAD_V02323,
    verify_product_v02323_history,
)
from scripts.product_v02323.run_increment1_forensics import (
    DEFAULT_SOURCE_LOCATOR,
    _git_bytes,
    _owner_count,
    _symbol_bytes,
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
        raise ValueError(f"artifact seal differs: {field}")
    return supplied


def _require_exact_sealed_artifact(
    payload: dict[str, Any],
    *,
    seal_field: str,
    expected_body: dict[str, object],
    label: str,
) -> str:
    supplied = _require_seal(payload, seal_field)
    observed_body = dict(payload)
    observed_body.pop(seal_field)
    if observed_body != expected_body:
        raise ValueError(f"Product v0.2.3.2.3 {label} differs")
    return supplied


def _private_path(root: Path, locator: str, *, expect_directory: bool) -> Path:
    relative = PurePosixPath(locator)
    if (
        not locator
        or relative.is_absolute()
        or "." in relative.parts
        or ".." in relative.parts
        or "\\" in locator
    ):
        raise ValueError("Product v0.2.3.2.3 private locator differs")
    current = root.resolve(strict=True)
    for part in relative.parts:
        current /= part
        if current.is_symlink():
            raise ValueError("Product v0.2.3.2.3 private locator is a symlink")
    resolved = current.resolve(strict=True)
    if not resolved.is_relative_to(root.resolve(strict=True)):
        raise ValueError("Product v0.2.3.2.3 private locator escapes root")
    if (expect_directory and not resolved.is_dir()) or (
        not expect_directory and not resolved.is_file()
    ):
        raise ValueError("Product v0.2.3.2.3 private artifact kind differs")
    return resolved


def verify_product_v02323_increment1(
    root: Path,
    *,
    source_root: Path | None = None,
    private_root: Path | None = None,
) -> dict[str, object]:
    project = Path(root).resolve(strict=True)
    if source_root is None:
        raise ValueError("Product v0.2.3.2.3 source root is required")
    private_project = (
        project if private_root is None else Path(private_root).resolve(strict=True)
    )
    history = verify_product_v02323_history(project)
    historical_manifest = _load(
        project / "config/product-v02323/historical-results.v1.json"
    )
    digest_binding = historical_manifest.get("lost_schema8_raw_digest_binding")
    if not isinstance(digest_binding, dict):
        raise ValueError("Product v0.2.3.2.3 digest binding differs")
    predecessor = _load(
        project / "docs/analysis/product-v02323-predecessor-audit.json"
    )
    predecessor_sha256 = _require_exact_sealed_artifact(
        predecessor,
        seal_field="audit_sha256",
        expected_body={
            "schema_version": "ecomsre.product.predecessor-audit.v02323",
            "goal_version": (
                "ecomsre-product-v02323-schema8-reconstruction-diagnosis-replay-v1"
            ),
            **history,
        },
        label="predecessor audit",
    )
    snapshot = ForensicRawSourceSnapshotV02323.model_validate_json(
        (project / "docs/analysis/product-v02323-forensic-source-snapshot.json").read_text(
            encoding="utf-8"
        )
    )
    digest = ProductStateDigestSemanticsAuditV02323.model_validate_json(
        (project / "docs/analysis/product-v02323-digest-semantics.json").read_text(
            encoding="utf-8"
        )
    )
    progress = _load(project / "docs/analysis/product-v02323-progress.json")

    private_snapshot = _private_path(
        private_project, snapshot.snapshot_locator, expect_directory=True
    )
    snapshot_artifact_verification_sha256 = (
        verify_forensic_snapshot_artifact_v02323(private_snapshot, snapshot)
    )

    digest_artifact = _private_path(
        private_project, digest.expected_digest_source_artifact, expect_directory=False
    )
    digest_artifact_bytes = digest_artifact.read_bytes()
    digest_source_payload = extract_raw_sqlite_digest_event_v02323(
        digest_artifact_bytes,
        expected_digest_full=EXPECTED_SCHEMA8_RAW_SHA256_V02323,
        source_locator=DEFAULT_SOURCE_LOCATOR,
    )
    definition = _git_bytes(
        project, PR83_HEAD_V02323, digest.source_definition_path
    )
    definition_sha256 = hashlib.sha256(definition).hexdigest()
    raw_function_sha256 = hashlib.sha256(
        _symbol_bytes(definition, "_sha256_file")
    ).hexdigest()
    logical_function_sha256 = hashlib.sha256(
        _symbol_bytes(definition, "_logical_database_sha256")
    ).hexdigest()
    state_function_sha256 = hashlib.sha256(
        _symbol_bytes(definition, "ProductStateSourceV0232")
    ).hexdigest()

    immutability = verify_forensic_source_immutability_v02323(
        source_root,
        snapshot,
        owner_counter=_owner_count,
    )
    expected_progress_body: dict[str, object] = {
        "schema_version": "ecomsre.product.progress.v02323",
        "goal_version": (
            "ecomsre-product-v02323-schema8-reconstruction-diagnosis-replay-v1"
        ),
        "increment": 1,
        "phase": "FORENSIC_SOURCE_BLOCKED",
        "terminals": [
            "ECOMSRE_PRODUCT_V02323_HISTORY_AND_BLOCKER_PASS",
            "ECOMSRE_PRODUCT_V02323_FORENSIC_SOURCE_SNAPSHOT_PASS",
            "ECOMSRE_PRODUCT_V02323_DIGEST_SEMANTICS_PASS",
        ],
        "history_audit_sha256": predecessor_sha256,
        "forensic_source_snapshot_sha256": snapshot.snapshot_sha256,
        "source_immutability_proof_sha256": immutability.proof_sha256,
        "digest_semantics_audit_sha256": digest.audit_sha256,
        "source_schema_version": snapshot.source_schema_version,
        "source_database_file_sha256": snapshot.source_database_file_sha256,
        "lost_schema8_database_file_sha256": EXPECTED_SCHEMA8_RAW_SHA256_V02323,
        "expected_digest_kind": digest.expected_digest_kind.value,
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
        "next_gate": "INCREMENT_2_SCHEMA9_CONTAMINATION_AND_SCHEMA8_RECONSTRUCTION",
    }
    progress_sha256 = _require_exact_sealed_artifact(
        progress,
        seal_field="progress_sha256",
        expected_body=expected_progress_body,
        label="progress",
    )

    if (
        snapshot.source_schema_version != 9
        or snapshot.source_database_file_sha256
        != OBSERVED_SCHEMA9_RAW_SHA256_V02323
        or snapshot.source_object_inventory_sha256
        != "93708f4e238e3bd3c9d662011ee098285eecf1112e0ab15a66b72fdcc254bf32"
        or digest.expected_digest_full != EXPECTED_SCHEMA8_RAW_SHA256_V02323
        or digest.observed_contaminated_digest_full
        != snapshot.source_database_file_sha256
        or digest.expected_digest_kind
        is not ProductStateDigestKindV02323.RAW_SQLITE_FILE_SHA256
        or digest.raw_byte_equality_required
        or not digest.logical_reconstruction_permitted
        or digest.expected_digest_source_artifact
        != digest_binding.get("source_artifact_locator")
        or digest.expected_digest_source_artifact_sha256
        != digest_binding.get("source_artifact_sha256")
        or digest.expected_digest_source_artifact_sha256
        != hashlib.sha256(digest_artifact_bytes).hexdigest()
        or digest.expected_digest_source_field
        != digest_binding.get("expected_digest_source_field")
        or digest_source_payload.get(digest.expected_digest_source_field)
        != digest.expected_digest_full
        or digest.source_definition_commit != PR83_HEAD_V02323
        or digest.source_definition_commit
        != digest_binding.get("source_definition_commit")
        or digest.source_definition_path
        != digest_binding.get("source_definition_path")
        or digest.source_definition_file_sha256 != definition_sha256
        or digest.source_definition_file_sha256
        != digest_binding.get("source_definition_file_sha256")
        or digest.raw_digest_function_source_sha256 != raw_function_sha256
        or digest.raw_digest_function_source_sha256
        != digest_binding.get("raw_digest_function_source_sha256")
        or digest.logical_digest_function_source_sha256 != logical_function_sha256
        or digest.logical_digest_function_source_sha256
        != digest_binding.get("logical_digest_function_source_sha256")
        or digest.state_digest_function_source_sha256 != state_function_sha256
        or digest.state_digest_function_source_sha256
        != digest_binding.get("state_digest_function_source_sha256")
    ):
        raise ValueError("Product v0.2.3.2.3 Increment 1 evidence differs")

    forbidden = (
        "docs/analysis/product-v02323-reconstruction-disposition.json",
        "docs/analysis/product-v02323-replay-input.json",
        "docs/analysis/product-v02323-diagnosis-root-cause.json",
        "docs/analysis/product-v02323-diagnosis-replay.json",
        "docs/results/product-v02323-engineering-closeout.json",
    )
    if any((project / relative).exists() for relative in forbidden):
        raise ValueError("Product v0.2.3.2.3 later-phase artifact exists")

    return {
        "history_terminal": history["terminal"],
        "snapshot_terminal": snapshot.terminal,
        "digest_terminal": digest.terminal,
        "source_schema_version": snapshot.source_schema_version,
        "source_database_file_sha256": snapshot.source_database_file_sha256,
        "snapshot_artifact_verification_sha256": (
            snapshot_artifact_verification_sha256
        ),
        "expected_digest_kind": digest.expected_digest_kind.value,
        "source_immutability_proof_sha256": immutability.proof_sha256,
        "progress_sha256": progress_sha256,
        "fault_attempts": 0,
        "new_business_traffic_executions": 0,
        "new_product_incidents": 0,
        "diagnosis_persistence_replay_attempts": 0,
        "provider_agent_runbook_docker_calls": 0,
        "measured_nofault_authority": "NONE",
        "knowledge_loop_authority": "NONE",
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--private-root", type=Path)
    arguments = parser.parse_args(argv)
    print(
        json.dumps(
            verify_product_v02323_increment1(
                arguments.root,
                source_root=arguments.source_root,
                private_root=arguments.private_root,
            ),
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ("verify_product_v02323_increment1",)
