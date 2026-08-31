#!/usr/bin/env python3
"""Freeze and audit the Product v0.2.3.2.3 forensic source."""

from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path
import subprocess
from typing import Any, Sequence

from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22
from ecomsre.product.pilot.forensic_schema8_v02323 import (
    GOAL_VERSION_V02323,
    build_product_state_digest_semantics_audit_v02323,
    capture_forensic_source_snapshot_v02323,
    extract_raw_sqlite_digest_event_v02323,
    verify_forensic_source_immutability_v02323,
)
from scripts.ci.verify_product_v02323_history import (
    EXPECTED_SCHEMA8_RAW_SHA256_V02323,
    OBSERVED_SCHEMA9_RAW_SHA256_V02323,
    verify_product_v02323_history,
)


PR83_HEAD = "142dc1094926f18e789ece3668c34918f859b512"
DIGEST_SOURCE_PATH = "src/ecomsre/product/pilot/product_state_clone_v0232.py"
DEFAULT_SOURCE_LOCATOR = (
    ".local/product-v02321/product-state/"
    "formal-0860c3cefe795378b3629334/product"
)
def _owner_count(database: Path) -> int:
    result = subprocess.run(
        ("lsof", "-t", "--", str(database)),
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode not in {0, 1}:
        raise RuntimeError("BLOCKED_ECOMSRE_PRODUCT_V02323_SOURCE_OWNER")
    return len(set(result.stdout.splitlines()))


def _git_bytes(root: Path, revision: str, relative: str) -> bytes:
    return subprocess.run(
        ("git", "show", f"{revision}:{relative}"),
        cwd=root,
        check=True,
        capture_output=True,
    ).stdout


def _symbol_bytes(source: bytes, symbol: str) -> bytes:
    text = source.decode("utf-8")
    tree = ast.parse(text)
    for node in tree.body:
        if isinstance(node, (ast.ClassDef, ast.FunctionDef)) and node.name == symbol:
            if node.end_lineno is None:
                break
            return b"".join(
                line.encode("utf-8")
                for line in text.splitlines(keepends=True)[
                    node.lineno - 1 : node.end_lineno
                ]
            )
    raise ValueError(f"missing source symbol: {symbol}")


def _find_digest_event(session: Path, item_id: str) -> tuple[bytes, dict[str, object]]:
    for raw_line in session.read_bytes().splitlines(keepends=True):
        try:
            event = json.loads(raw_line)
        except json.JSONDecodeError:
            continue
        payload = event.get("payload")
        item = payload.get("item") if isinstance(payload, dict) else None
        if not isinstance(item, dict) or item.get("id") != item_id:
            continue
        payload = extract_raw_sqlite_digest_event_v02323(
            raw_line,
            expected_digest_full=EXPECTED_SCHEMA8_RAW_SHA256_V02323,
            source_locator=DEFAULT_SOURCE_LOCATOR,
        )
        return raw_line, dict(payload)
    raise ValueError("BLOCKED_ECOMSRE_PRODUCT_V02323_DIGEST_SEMANTICS")


def _write_exact(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.is_symlink() or path.read_bytes() != payload:
            raise ValueError(f"artifact bytes differ: {path}")
        return
    with path.open("xb") as handle:
        handle.write(payload)


def _json_bytes(payload: object) -> bytes:
    return (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _sealed(payload: dict[str, Any], field: str) -> dict[str, Any]:
    return {**payload, field: semantic_sha256_v22(payload)}


def run_increment1(
    root: Path,
    *,
    source_root: Path,
    snapshot_id: str,
    captured_at: str,
    digest_session: Path,
    digest_source_item_id: str,
) -> dict[str, object]:
    project = root.resolve(strict=True)
    history = verify_product_v02323_history(project)

    event_bytes, digest_source_payload = _find_digest_event(
        digest_session, digest_source_item_id
    )
    private_digest_path = (
        project
        / ".local/product-v02323/forensics/digest-source/"
        "pre-migration-shasum-event.jsonl"
    )
    _write_exact(private_digest_path, event_bytes)

    snapshot_locator = (
        f".local/product-v02323/forensics/raw-source/{snapshot_id}"
    )
    snapshot = capture_forensic_source_snapshot_v02323(
        source_root,
        project / snapshot_locator,
        source_locator=DEFAULT_SOURCE_LOCATOR,
        snapshot_locator=snapshot_locator,
        captured_at=captured_at,
        owner_counter=_owner_count,
    )
    if snapshot.source_database_file_sha256 != OBSERVED_SCHEMA9_RAW_SHA256_V02323:
        raise ValueError("BLOCKED_ECOMSRE_PRODUCT_V02323_FORENSIC_SNAPSHOT")

    definition = _git_bytes(project, PR83_HEAD, DIGEST_SOURCE_PATH)
    digest_audit = build_product_state_digest_semantics_audit_v02323(
        expected_digest_full=EXPECTED_SCHEMA8_RAW_SHA256_V02323,
        observed_contaminated_digest_full=snapshot.source_database_file_sha256,
        expected_digest_source_artifact=(
            ".local/product-v02323/forensics/digest-source/"
            "pre-migration-shasum-event.jsonl"
        ),
        expected_digest_source_field="source_database_file_sha256",
        expected_digest_source_artifact_bytes=event_bytes,
        expected_digest_source_payload=digest_source_payload,
        raw_digest_function_source=_symbol_bytes(definition, "_sha256_file"),
        logical_digest_function_source=_symbol_bytes(
            definition, "_logical_database_sha256"
        ),
        state_digest_function_source=_symbol_bytes(
            definition, "ProductStateSourceV0232"
        ),
        source_definition_commit=PR83_HEAD,
        source_definition_path=DIGEST_SOURCE_PATH,
        source_definition_file_bytes=definition,
    )
    immutability = verify_forensic_source_immutability_v02323(
        source_root,
        snapshot,
        owner_counter=_owner_count,
    )

    predecessor_body: dict[str, Any] = {
        "schema_version": "ecomsre.product.predecessor-audit.v02323",
        "goal_version": GOAL_VERSION_V02323,
        **history,
    }
    predecessor = _sealed(predecessor_body, "audit_sha256")
    progress_body: dict[str, Any] = {
        "schema_version": "ecomsre.product.progress.v02323",
        "goal_version": GOAL_VERSION_V02323,
        "increment": 1,
        "phase": "FORENSIC_SOURCE_BLOCKED",
        "terminals": [
            "ECOMSRE_PRODUCT_V02323_HISTORY_AND_BLOCKER_PASS",
            "ECOMSRE_PRODUCT_V02323_FORENSIC_SOURCE_SNAPSHOT_PASS",
            "ECOMSRE_PRODUCT_V02323_DIGEST_SEMANTICS_PASS",
        ],
        "history_audit_sha256": predecessor["audit_sha256"],
        "forensic_source_snapshot_sha256": snapshot.snapshot_sha256,
        "source_immutability_proof_sha256": immutability.proof_sha256,
        "digest_semantics_audit_sha256": digest_audit.audit_sha256,
        "source_schema_version": snapshot.source_schema_version,
        "source_database_file_sha256": snapshot.source_database_file_sha256,
        "lost_schema8_database_file_sha256": EXPECTED_SCHEMA8_RAW_SHA256_V02323,
        "expected_digest_kind": digest_audit.expected_digest_kind.value,
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
    progress = _sealed(progress_body, "progress_sha256")

    _write_exact(
        project / "docs/analysis/product-v02323-predecessor-audit.json",
        _json_bytes(predecessor),
    )
    _write_exact(
        project / "docs/analysis/product-v02323-forensic-source-snapshot.json",
        _json_bytes(snapshot.model_dump(mode="json")),
    )
    _write_exact(
        project / "docs/analysis/product-v02323-digest-semantics.json",
        _json_bytes(digest_audit.model_dump(mode="json")),
    )
    digest_markdown = f"""# Product v0.2.3.2.3 Digest Semantics Audit

Terminal: `{digest_audit.terminal}`

- Historical digest: `{digest_audit.expected_digest_full}`
- Surviving schema-9 raw digest: `{digest_audit.observed_contaminated_digest_full}`
- Classified kind: `{digest_audit.expected_digest_kind.value}`
- Source field: `{digest_audit.expected_digest_source_field}`
- PR #83 source definition: `{digest_audit.source_definition_commit}:{digest_audit.source_definition_path}`

The historical value is the SHA-256 of the raw schema-8 SQLite file bytes. Those
bytes are lost. A reconstructed database may prove canonical logical equality,
but it must not claim the same SQLite page layout or raw-file identity. This
reconstruction/replay has no measured No-Fault or Knowledge-Loop authority.
"""
    _write_exact(
        project / "docs/analysis/product-v02323-digest-semantics.md",
        digest_markdown.encode("utf-8"),
    )
    _write_exact(
        project / "docs/analysis/product-v02323-progress.json",
        _json_bytes(progress),
    )
    return {
        "history": history,
        "snapshot": snapshot.model_dump(mode="json"),
        "immutability": immutability.model_dump(mode="json"),
        "digest_semantics": digest_audit.model_dump(mode="json"),
        "progress": progress,
        "terminal": "ECOMSRE_PRODUCT_V02323_DIGEST_SEMANTICS_PASS",
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--snapshot-id", required=True)
    parser.add_argument("--captured-at", required=True)
    parser.add_argument("--digest-session", type=Path, required=True)
    parser.add_argument("--digest-source-item-id", required=True)
    arguments = parser.parse_args(argv)
    result = run_increment1(
        arguments.root,
        source_root=arguments.source_root,
        snapshot_id=arguments.snapshot_id,
        captured_at=arguments.captured_at,
        digest_session=arguments.digest_session,
        digest_source_item_id=arguments.digest_source_item_id,
    )
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
