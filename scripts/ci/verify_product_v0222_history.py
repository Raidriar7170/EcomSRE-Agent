#!/usr/bin/env python3
"""Verify immutable Product v0.2 through v0.2.2.1 predecessor evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import stat
import subprocess
from typing import Any, Sequence

from scripts.ci.verify_product_v0221_history import verify_product_v0221_history
from scripts.ci.verify_product_v0221_schema_blocker import (
    verify_product_v0221_schema_blocker,
)
from scripts.ci.product_squash_history_v0231 import (
    require_product_import_ancestry_v0231,
    require_product_import_bytes_v0231,
)


PUBLIC_MAIN = "8398a063de048064f160a7ffed236fbb3327b701"
V02_HEAD = "a439f8882cd2fcdd3767f6bcfd5d955219fa1e15"
V021_HEAD = "55ccae45738c00a8be3752b81fecf19f37c87ce5"
V022_HEAD = "1568c72c3262befb90fb4e191592e51aa345bdcb"
V0221_HEAD = "08e813f9a48da091069dc95c5e312a552076aa99"
V0221_TERMINAL = "BLOCKED_ECOMSRE_PRODUCT_V0221_SCHEMA_AMBIGUOUS"
VERIFIED_TERMINAL = "ECOMSRE_PRODUCT_V0222_HISTORY_VERIFIED"

REQUIRED_ROLES = {
    "V0221_PREDECESSOR_HISTORY",
    "V0221_PROBE_PROFILE",
    "V0221_PREDECESSOR_AUDIT",
    "V0221_REQUEST_PROTOCOL_JSON",
    "V0221_REQUEST_PROTOCOL_MD",
    "V0221_SCHEMA_BLOCKER_JSON",
    "V0221_SCHEMA_BLOCKER_MD",
    "V0221_PROGRESS",
}


def _load_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Product v0.2.2.2 historical payload must be an object")
    return payload


def _regular_bytes(root: Path, relative: str) -> bytes:
    candidate = Path(relative)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise ValueError("Product v0.2.2.2 historical path is not repository-relative")
    resolved = root / candidate
    metadata = resolved.lstat()
    if not stat.S_ISREG(metadata.st_mode):
        raise ValueError(f"Product v0.2.2.2 historical path is not regular: {relative}")
    return resolved.read_bytes()


def _git_bytes(root: Path, revision: str, relative: str) -> bytes:
    return subprocess.run(
        ("git", "show", f"{revision}:{relative}"),
        cwd=root,
        check=True,
        capture_output=True,
    ).stdout


def _require_ancestry(root: Path, ancestor: str, descendant: str) -> None:
    subprocess.run(
        ("git", "merge-base", "--is-ancestor", ancestor, descendant),
        cwd=root,
        check=True,
        capture_output=True,
    )


def _require_identity(manifest: dict[str, Any]) -> dict[str, Any]:
    if (
        manifest.get("schema_version")
        != "ecomsre.product-v0222.historical-results.v1"
        or manifest.get("goal_version")
        != "ecomsre-product-v0222-capture-first-operator-profile-v1"
        or manifest.get("public_main_base") != PUBLIC_MAIN
    ):
        raise ValueError("Product v0.2.2.2 historical manifest identity differs")
    for key, pr, head, terminal in (
        ("v02", 75, V02_HEAD, "BLOCKED_ECOMSRE_PRODUCT_V02_UNKNOWN_FAULT_PROFILE"),
        ("v021", 76, V021_HEAD, "BLOCKED_ECOMSRE_PRODUCT_V021_BASELINE_READINESS"),
        ("v022", 77, V022_HEAD, "BLOCKED_ECOMSRE_PRODUCT_V022_SCHEMA_PROBE"),
    ):
        item = manifest.get(key)
        if not isinstance(item, dict) or item != {
            "pr": pr,
            "head": head,
            "terminal": terminal,
        }:
            raise ValueError("Product frozen predecessor identity differs")
    v0221 = manifest.get("v0221")
    if not isinstance(v0221, dict):
        raise ValueError("Product v0.2.2.1 predecessor identity is malformed")
    expected = {
        "pr": 78,
        "branch": "codex/product-v0221-opensearch-probe-protocol",
        "head": V0221_HEAD,
        "terminal": V0221_TERMINAL,
        "classification": "VALID_BLOCKED_ENGINEERING_EVIDENCE",
        "request_protocol_terminal": "ECOMSRE_PRODUCT_V0221_REQUEST_PROTOCOL_PASS",
        "session_id": "product-v0221-schema-discovery-1",
        "live_schema_session_count": 1,
        "request_plan_count": 1,
        "request_plan_variant": "PLAN_A_FIELD_CAPS_GET_QUERY",
        "changed_request_plan_count": 0,
        "read_only_request_count": 6,
        "maximum_read_only_request_count": 16,
        "transport_retry_count": 0,
        "failure_stage": "PROFILE_RESOLUTION",
        "mapping_status": "SUCCEEDED_IN_MEMORY_NOT_RETAINED",
        "field_caps_status": "AVAILABLE_IN_MEMORY_NOT_RETAINED",
        "bounded_sample_status": "PRESENT_IN_MEMORY_NOT_RETAINED",
        "normalization_profile_status": "ABSENT",
        "sanitized_fixture_status": "ABSENT",
        "offline_parser_status": "NOT_STARTED",
        "connector_smoke_status": "NOT_STARTED",
        "private_start_file_sha256": "4eb6525c0e33629eeb755c5a0d2f13a60a389f0dd3e178f991c5eab110d8b6d5",
        "private_completion_file_sha256": "8cf7908b6527024f9aa77bf7027de336123381210d253255ef6985d98b02794b",
        "private_completion_report_sha256": "7cc52aac4a786d6b72b1e5af4d52c2f9baac5a246d15c9363d25b12df356e42d",
        "baseline_unchanged": True,
        "cleanup": "CLEAN",
        "fault_attempt_count": 0,
        "baseline_readiness_attempt_count": 0,
        "product_diagnosis_attempt_count": 0,
        "knowledge_loop_campaign_count": 0,
        "agent_writes": 0,
        "runbook_executions": 0,
        "action_authority": "NONE",
        "merged": False,
        "rerun_authority": 0,
        "review_disposition": "DRAFT_REVIEW_REQUIRED",
        "tracked_final_review": "ABSENT_AT_FROZEN_HEAD",
        "github_review_object_count": 0,
        "github_issue_comment_object_count": 0,
        "github_review_comment_object_count": 0,
        "pr_body_sha256": "40823f0dc08c08ea1bc92f4bb66af1c8fbe846b347aab29f4a052d88f20593cd",
    }
    if v0221 != expected:
        raise ValueError("Product v0.2.2.1 frozen result identity differs")
    return v0221


def _verify_bound_files(root: Path, manifest: dict[str, Any]) -> int:
    files = manifest.get("files")
    if not isinstance(files, list) or len(files) != len(REQUIRED_ROLES):
        raise ValueError("Product v0.2.2.2 historical file set differs")
    roles: set[str] = set()
    paths: set[str] = set()
    for item in files:
        if not isinstance(item, dict):
            raise ValueError("Product v0.2.2.2 historical binding is malformed")
        relative = item.get("path")
        revision = item.get("revision")
        role = item.get("role")
        digest = item.get("sha256")
        size = item.get("size_bytes")
        if (
            not isinstance(relative, str)
            or revision != V0221_HEAD
            or not isinstance(role, str)
            or not isinstance(digest, str)
            or not isinstance(size, int)
            or role in roles
            or relative in paths
        ):
            raise ValueError("Product v0.2.2.2 historical binding is malformed")
        current = _regular_bytes(root, relative)
        if len(current) != size or hashlib.sha256(current).hexdigest() != digest:
            raise ValueError(f"Product v0.2.2.1 byte drift: {relative}")
        if _git_bytes(root, revision, relative) != current:
            raise ValueError(f"Product v0.2.2.1 head binding drift: {relative}")
        require_product_import_bytes_v0231(
            root,
            relative=relative,
            expected=current,
        )
        roles.add(role)
        paths.add(relative)
    if roles != REQUIRED_ROLES:
        raise ValueError("Product v0.2.2.2 historical roles differ")
    return len(files)


def verify_product_v0222_history(
    project_root: Path,
    manifest_path: Path | None = None,
) -> dict[str, object]:
    root = Path(project_root).resolve(strict=True)
    manifest = _load_object(
        manifest_path or root / "config/product-v0222/historical-results.v1.json"
    )
    v0221 = _require_identity(manifest)
    direct_bound_file_count = _verify_bound_files(root, manifest)
    predecessor = verify_product_v0221_history(root)
    blocker = verify_product_v0221_schema_blocker(root)
    if (
        predecessor.get("status") != "ECOMSRE_PRODUCT_V0221_HISTORY_VERIFIED"
        or blocker.get("status") != V0221_TERMINAL
    ):
        raise ValueError("Product v0.2.2.1 predecessor verifier did not pass")
    _require_ancestry(root, PUBLIC_MAIN, V02_HEAD)
    _require_ancestry(root, V02_HEAD, V021_HEAD)
    _require_ancestry(root, V021_HEAD, V022_HEAD)
    _require_ancestry(root, V022_HEAD, V0221_HEAD)
    require_product_import_ancestry_v0231(root)
    return {
        "status": VERIFIED_TERMINAL,
        "v02_head": V02_HEAD,
        "v021_head": V021_HEAD,
        "v022_head": V022_HEAD,
        "v0221_head": V0221_HEAD,
        "v0221_terminal": V0221_TERMINAL,
        "v0221_live_schema_session_count": v0221["live_schema_session_count"],
        "v0221_read_only_request_count": v0221["read_only_request_count"],
        "v0221_changed_request_plan_count": v0221["changed_request_plan_count"],
        "transitive_bound_file_count": predecessor["bound_file_count"],
        "direct_bound_file_count": direct_bound_file_count,
        "cleanup": "CLEAN",
        "fault_attempt_count": 0,
        "baseline_readiness_attempt_count": 0,
        "product_diagnosis_attempt_count": 0,
        "knowledge_loop_campaign_count": 0,
        "agent_writes": 0,
        "runbook_executions": 0,
        "action_authority": "NONE",
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--project-root",
        type=Path,
        default=Path(__file__).resolve().parents[2],
    )
    parser.add_argument("--manifest", type=Path)
    arguments = parser.parse_args(argv)
    print(
        json.dumps(
            verify_product_v0222_history(arguments.project_root, arguments.manifest),
            separators=(",", ":"),
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = (
    "PUBLIC_MAIN",
    "V0221_HEAD",
    "V0221_TERMINAL",
    "VERIFIED_TERMINAL",
    "verify_product_v0222_history",
)
