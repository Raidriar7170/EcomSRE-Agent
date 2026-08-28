#!/usr/bin/env python3
"""Verify immutable Product v0.2, v0.2.1, and v0.2.2 predecessor evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import stat
import subprocess
from typing import Any, Sequence

from scripts.ci.verify_product_v022_schema_probe_blocker import (
    verify_product_v022_schema_probe_blocker,
)


PUBLIC_MAIN = "8398a063de048064f160a7ffed236fbb3327b701"
V02_HEAD = "a439f8882cd2fcdd3767f6bcfd5d955219fa1e15"
V021_HEAD = "55ccae45738c00a8be3752b81fecf19f37c87ce5"
V022_HEAD = "1568c72c3262befb90fb4e191592e51aa345bdcb"
V02_TERMINAL = "BLOCKED_ECOMSRE_PRODUCT_V02_UNKNOWN_FAULT_PROFILE"
V021_TERMINAL = "BLOCKED_ECOMSRE_PRODUCT_V021_BASELINE_READINESS"
V022_TERMINAL = "BLOCKED_ECOMSRE_PRODUCT_V022_SCHEMA_PROBE"
VERIFIED_TERMINAL = "ECOMSRE_PRODUCT_V0221_HISTORY_VERIFIED"

REQUIRED_ROLES = {
    "V02_CONSUMED_MARKER",
    "V02_CLEANUP_CLOSURE",
    "V02_PROGRESS",
    "V02_BLOCKED_RESULT",
    "V02_REVIEW",
    "V021_QUERY_BINDING_PROFILE",
    "V021_BLOCKED_RESULT",
    "V021_FINAL_REPORT",
    "V021_READINESS_ATTEMPT_1",
    "V021_READINESS_ATTEMPT_2",
    "V021_PREDECESSOR_AUDIT",
    "V021_PROGRESS",
    "V022_PREDECESSOR_HISTORY",
    "V022_PROBE_PROFILE",
    "V022_BLOCKER",
    "V022_PROGRESS",
}


def _load_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Product v0.2.2.1 historical payload must be an object")
    return payload


def _regular_bytes(root: Path, relative: str) -> bytes:
    candidate = Path(relative)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise ValueError("Product v0.2.2.1 historical path is not repository-relative")
    resolved = root / candidate
    metadata = resolved.lstat()
    if not stat.S_ISREG(metadata.st_mode):
        raise ValueError(
            f"Product v0.2.2.1 historical path is not regular: {relative}"
        )
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


def _require_predecessor_identity(manifest: dict[str, Any]) -> None:
    if (
        manifest.get("schema_version")
        != "ecomsre.product-v0221.historical-results.v1"
        or manifest.get("goal_version")
        != "ecomsre-product-v0221-opensearch-probe-protocol-v1"
        or manifest.get("public_main_base") != PUBLIC_MAIN
    ):
        raise ValueError("Product v0.2.2.1 historical manifest identity differs")
    v02 = manifest.get("v02")
    v021 = manifest.get("v021")
    v022 = manifest.get("v022")
    if not all(isinstance(item, dict) for item in (v02, v021, v022)):
        raise ValueError("Product v0.2.2.1 predecessor identity is malformed")
    assert isinstance(v02, dict)
    assert isinstance(v021, dict)
    assert isinstance(v022, dict)
    expected_common = (
        (
            v02,
            75,
            "codex/product-v02-live-knowledge-loop-pilot",
            V02_HEAD,
            V02_TERMINAL,
            "cb4d67b2e4de684a32bccbc0e93010d48fec5244e09d8c7f74d17a7d66b85953",
        ),
        (
            v021,
            76,
            "codex/product-v021-baseline-readiness-successor",
            V021_HEAD,
            V021_TERMINAL,
            "2b66b0c457df32c6e628cb39e89b6859610a89a8ee4151dd768d77e9a1c883a7",
        ),
        (
            v022,
            77,
            "codex/product-v022-opensearch-baseline-compatibility",
            V022_HEAD,
            V022_TERMINAL,
            "e4ce29f0be40e16b8b20407327633712997e1044a2bc8602ab5f7c8ff836ba8b",
        ),
    )
    for item, pr, branch, head, terminal, pr_body_sha256 in expected_common:
        if (
            item.get("pr") != pr
            or item.get("branch") != branch
            or item.get("head") != head
            or item.get("terminal") != terminal
            or item.get("classification") != "VALID_BLOCKED_ENGINEERING_EVIDENCE"
            or item.get("merged") is not False
            or item.get("rerun_authority") != 0
            or item.get("review_disposition") != "DRAFT_REVIEW_REQUIRED"
            or item.get("github_review_object_count") != 0
            or item.get("github_comment_object_count") != 0
            or item.get("pr_body_sha256") != pr_body_sha256
        ):
            raise ValueError("Product predecessor PR disposition differs")
    if (
        v02.get("fault_attempt_count") != 0
        or v021.get("readiness_attempt_count") != 2
        or v021.get("fault_attempt_count") != 0
        or v022.get("execution_count") != 1
        or v022.get("failure_stage") != "FIELD_CAPS_QUERY"
        or v022.get("safe_message") != "OpenSearch schema probe HTTP status 400"
        or v022.get("request_count") != 2
        or v022.get("maximum_request_count") != 12
        or v022.get("sample_count") != 0
        or v022.get("maximum_sample_count") != 5
        or v022.get("baseline_unchanged") is not True
        or v022.get("owned_demo_cleanup") != "CLEAN"
        or v022.get("retry_authority") != "NONE"
        or v022.get("fault_attempt_count") != 0
        or v022.get("baseline_readiness_campaign_count") != 0
        or v022.get("knowledge_loop_campaign_count") != 0
        or v022.get("agent_writes") != 0
        or v022.get("runbook_executions") != 0
        or v022.get("action_authority") != "NONE"
    ):
        raise ValueError("Product v0.2.2 consumed blocker identity differs")


def _verify_bound_files(root: Path, manifest: dict[str, Any]) -> int:
    files = manifest.get("files")
    if not isinstance(files, list) or len(files) != len(REQUIRED_ROLES):
        raise ValueError("Product v0.2.2.1 historical file set differs")
    roles: set[str] = set()
    paths: set[str] = set()
    for item in files:
        if not isinstance(item, dict):
            raise ValueError("Product v0.2.2.1 historical binding is malformed")
        relative = item.get("path")
        revision = item.get("revision")
        role = item.get("role")
        digest = item.get("sha256")
        size = item.get("size_bytes")
        if (
            not isinstance(relative, str)
            or revision not in {V02_HEAD, V021_HEAD, V022_HEAD}
            or not isinstance(role, str)
            or not isinstance(digest, str)
            or not isinstance(size, int)
        ):
            raise ValueError("Product v0.2.2.1 historical binding is malformed")
        if relative in paths or role in roles:
            raise ValueError("Product v0.2.2.1 historical binding is duplicated")
        current = _regular_bytes(root, relative)
        if len(current) != size or hashlib.sha256(current).hexdigest() != digest:
            raise ValueError(f"Product predecessor byte drift: {relative}")
        if _git_bytes(root, revision, relative) != current:
            raise ValueError(f"Product predecessor head binding drift: {relative}")
        paths.add(relative)
        roles.add(role)
    if roles != REQUIRED_ROLES:
        raise ValueError("Product v0.2.2.1 historical roles differ")
    return len(files)


def _require_review_boundary(root: Path) -> None:
    v02_names = subprocess.run(
        ("git", "ls-tree", "-r", "--name-only", V02_HEAD),
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    if "docs/external-reviews/product-v02-pre-campaign-review.md" not in v02_names:
        raise ValueError("Product v0.2 tracked review artifact is absent")
    for revision, prefix in (
        (V021_HEAD, "docs/external-reviews/product-v021"),
        (V022_HEAD, "docs/external-reviews/product-v022"),
    ):
        names = subprocess.run(
            ("git", "ls-tree", "-r", "--name-only", revision),
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.splitlines()
        if any(name.startswith(prefix) and "final-review" in name for name in names):
            raise ValueError("Product predecessor final-review absence differs")


def verify_product_v0221_history(
    project_root: Path,
    manifest_path: Path | None = None,
) -> dict[str, object]:
    root = Path(project_root).resolve(strict=True)
    manifest = _load_object(
        manifest_path or root / "config/product-v0221/historical-results.v1.json"
    )
    _require_predecessor_identity(manifest)
    bound_file_count = _verify_bound_files(root, manifest)
    _require_ancestry(root, PUBLIC_MAIN, V02_HEAD)
    _require_ancestry(root, V02_HEAD, V021_HEAD)
    _require_ancestry(root, V021_HEAD, V022_HEAD)
    _require_ancestry(root, V022_HEAD, "HEAD")
    _require_review_boundary(root)
    blocker = verify_product_v022_schema_probe_blocker(root)
    if blocker.get("status") != V022_TERMINAL:
        raise ValueError("Product v0.2.2 blocker verifier did not pass")
    return {
        "status": VERIFIED_TERMINAL,
        "v02_head": V02_HEAD,
        "v021_head": V021_HEAD,
        "v022_head": V022_HEAD,
        "v02_terminal": V02_TERMINAL,
        "v021_terminal": V021_TERMINAL,
        "v022_terminal": V022_TERMINAL,
        "v022_execution_count": blocker["execution_count"],
        "v022_request_count": blocker["request_count"],
        "v022_sample_count": blocker["sample_count"],
        "baseline_unchanged": blocker["baseline_unchanged"],
        "owned_demo_cleanup": blocker["owned_demo_cleanup"],
        "bound_file_count": bound_file_count,
        "fault_attempt_count": 0,
        "agent_writes": 0,
        "runbook_executions": 0,
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
            verify_product_v0221_history(arguments.project_root, arguments.manifest),
            separators=(",", ":"),
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = (
    "PUBLIC_MAIN",
    "V02_HEAD",
    "V021_HEAD",
    "V022_HEAD",
    "V02_TERMINAL",
    "V021_TERMINAL",
    "V022_TERMINAL",
    "VERIFIED_TERMINAL",
    "verify_product_v0221_history",
)
