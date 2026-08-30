#!/usr/bin/env python3
"""Verify immutable Product v0.2 and v0.2.1 evidence for v0.2.2."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import stat
import subprocess
from typing import Any, Sequence

from scripts.ci.verify_product_v021_history import verify_product_v021_history
from scripts.ci.product_squash_history_v0231 import (
    require_product_import_ancestry_v0231,
    require_product_import_bytes_v0231,
)


PUBLIC_MAIN = "8398a063de048064f160a7ffed236fbb3327b701"
V02_HEAD = "a439f8882cd2fcdd3767f6bcfd5d955219fa1e15"
V021_HEAD = "55ccae45738c00a8be3752b81fecf19f37c87ce5"
V02_TERMINAL = "BLOCKED_ECOMSRE_PRODUCT_V02_UNKNOWN_FAULT_PROFILE"
V021_TERMINAL = "BLOCKED_ECOMSRE_PRODUCT_V021_BASELINE_READINESS"
VERIFIED_TERMINAL = "ECOMSRE_PRODUCT_V022_HISTORY_VERIFIED"

REQUIRED_ROLES = {
    "V02_CONSUMED_MARKER",
    "V02_CLEANUP_CLOSURE",
    "V02_PROGRESS",
    "V02_BLOCKED_RESULT",
    "V021_QUERY_BINDING_PROFILE",
    "V021_BLOCKED_RESULT",
    "V021_FINAL_REPORT",
    "V021_READINESS_ATTEMPT_1",
    "V021_READINESS_ATTEMPT_2",
    "V021_PREDECESSOR_AUDIT",
    "V021_PROGRESS",
}


def _load_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Product v0.2.2 historical payload must be an object")
    return payload


def _regular_bytes(root: Path, relative: str) -> bytes:
    candidate = Path(relative)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise ValueError("Product v0.2.2 historical path is not repository-relative")
    resolved = root / candidate
    metadata = resolved.lstat()
    if not stat.S_ISREG(metadata.st_mode):
        raise ValueError(f"Product v0.2.2 historical path is not regular: {relative}")
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
        != "ecomsre.product-v022.historical-results.v1"
        or manifest.get("goal_version")
        != "ecomsre-product-v022-opensearch-baseline-compatibility-v1"
        or manifest.get("public_main_base") != PUBLIC_MAIN
    ):
        raise ValueError("Product v0.2.2 historical manifest identity differs")
    expected_v02 = {
        "pr": 75,
        "branch": "codex/product-v02-live-knowledge-loop-pilot",
        "head": V02_HEAD,
        "terminal": V02_TERMINAL,
        "classification": "VALID_BLOCKED_ENGINEERING_EVIDENCE",
        "readiness_blocker": "BASELINE_INSUFFICIENT_WINDOWS",
        "fault_attempt_count": 0,
        "merged": False,
        "rerun_authority": 0,
    }
    expected_v021 = {
        "pr": 76,
        "branch": "codex/product-v021-baseline-readiness-successor",
        "head": V021_HEAD,
        "terminal": V021_TERMINAL,
        "classification": "VALID_BLOCKED_ENGINEERING_EVIDENCE",
        "readiness_attempt_count": 2,
        "readiness_run_count": 2,
        "accepted_window_count": 0,
        "logs_terminal": "FAILURE_SCHEMA/CONNECTOR_SCHEMA_INVALID",
        "fault_attempt_count": 0,
        "merged": False,
        "rerun_authority": 0,
        "review_disposition": "DRAFT_REVIEW_REQUIRED",
        "tracked_final_review": "ABSENT_AT_FROZEN_HEAD",
        "github_review_object_count": 0,
        "github_comment_object_count": 0,
        "pr_body_review_summary": (
            "Must Fix 0 / Should Fix 0 / Claim Accuracy PASS"
        ),
        "pr_body_sha256": (
            "2b66b0c457df32c6e628cb39e89b6859610a89a8ee4151dd768d77e9a1c883a7"
        ),
    }
    if manifest.get("v02") != expected_v02 or manifest.get("v021") != expected_v021:
        raise ValueError("Product v0.2.2 predecessor disposition differs")


def _require_no_tracked_v021_final_review(root: Path) -> None:
    names = subprocess.run(
        ("git", "ls-tree", "-r", "--name-only", V021_HEAD),
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    if any(
        name.startswith("docs/external-reviews/product-v021")
        and "final-review" in name
        for name in names
    ):
        raise ValueError("Product v0.2.1 tracked final-review absence differs")


def _verify_bound_files(root: Path, manifest: dict[str, Any]) -> int:
    files = manifest.get("files")
    if not isinstance(files, list) or len(files) != len(REQUIRED_ROLES):
        raise ValueError("Product v0.2.2 historical file set differs")
    roles: set[str] = set()
    paths: set[str] = set()
    for item in files:
        if not isinstance(item, dict):
            raise ValueError("Product v0.2.2 historical binding is malformed")
        relative = item.get("path")
        revision = item.get("revision")
        role = item.get("role")
        digest = item.get("sha256")
        size = item.get("size_bytes")
        if (
            not isinstance(relative, str)
            or revision not in {V02_HEAD, V021_HEAD}
            or not isinstance(role, str)
            or not isinstance(digest, str)
            or not isinstance(size, int)
        ):
            raise ValueError("Product v0.2.2 historical binding is malformed")
        if relative in paths or role in roles:
            raise ValueError("Product v0.2.2 historical binding is duplicated")
        current = _regular_bytes(root, relative)
        if len(current) != size or hashlib.sha256(current).hexdigest() != digest:
            raise ValueError(f"Product v0.2.2 predecessor byte drift: {relative}")
        if _git_bytes(root, revision, relative) != current:
            raise ValueError(
                f"Product v0.2.2 predecessor head binding drift: {relative}"
            )
        require_product_import_bytes_v0231(
            root,
            relative=relative,
            expected=current,
        )
        paths.add(relative)
        roles.add(role)
    if roles != REQUIRED_ROLES:
        raise ValueError("Product v0.2.2 historical roles differ")
    return len(files)


def _verify_public_terminals(root: Path) -> None:
    v02 = _load_object(root / "docs/results/product-v02-live-knowledge-loop.json")
    if (
        v02.get("engineering_terminal") != V02_TERMINAL
        or v02.get("blocker_code") != "BASELINE_INSUFFICIENT_WINDOWS"
        or v02.get("live_calibration_attempt_count") != 0
        or v02.get("pilot_outcome") != "NOT_REACHED"
        or v02.get("agent_writes") != 0
        or v02.get("runbook_executions") != 0
    ):
        raise ValueError("Product v0.2 blocked terminal differs")
    v021 = _load_object(root / "docs/analysis/product-v021-baseline-readiness.json")
    progress = _load_object(root / "docs/analysis/product-v021-progress.json")
    if (
        v021.get("terminal") != V021_TERMINAL
        or v021.get("readiness_attempt_count") != 2
        or v021.get("readiness_run_count") != 2
        or v021.get("fault_attempt_count") != 0
        or progress.get("terminal") != V021_TERMINAL
        or progress.get("baseline_readiness_attempt_count") != 2
        or progress.get("fault_attempt_count") != 0
        or progress.get("agent_writes") != 0
        or progress.get("runbook_executions") != 0
    ):
        raise ValueError("Product v0.2.1 blocked terminal differs")
    for number in (1, 2):
        attempt = _load_object(
            root
            / f"docs/analysis/product-v021-baseline-readiness-attempt-{number}.json"
        )
        if attempt.get("fault_attempt_count") != 0:
            raise ValueError("Product v0.2.1 predecessor fault count differs")
        latest = attempt.get("latest_attempt", attempt)
        accepted = (
            latest.get("accepted_window_count")
            if isinstance(latest, dict)
            else None
        )
        if accepted != 0:
            raise ValueError("Product v0.2.1 predecessor accepted windows differ")


def verify_product_v022_history(
    project_root: Path,
    manifest_path: Path | None = None,
) -> dict[str, object]:
    root = project_root.resolve(strict=True)
    manifest = _load_object(
        manifest_path or root / "config/product-v022/historical-results.v1.json"
    )
    _require_predecessor_identity(manifest)
    bound_file_count = _verify_bound_files(root, manifest)
    _require_ancestry(root, PUBLIC_MAIN, V02_HEAD)
    _require_ancestry(root, V02_HEAD, V021_HEAD)
    require_product_import_ancestry_v0231(root)
    _require_no_tracked_v021_final_review(root)
    previous = verify_product_v021_history(root)
    if previous.get("status") != "ECOMSRE_PRODUCT_V021_HISTORY_VERIFIED":
        raise ValueError("Product v0.2.1 historical verifier did not pass")
    _verify_public_terminals(root)
    return {
        "status": VERIFIED_TERMINAL,
        "v02_head": V02_HEAD,
        "v021_head": V021_HEAD,
        "v02_terminal": V02_TERMINAL,
        "v021_terminal": V021_TERMINAL,
        "v021_readiness_attempt_count": 2,
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
            verify_product_v022_history(arguments.project_root, arguments.manifest),
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
    "V02_TERMINAL",
    "V021_HEAD",
    "V021_TERMINAL",
    "VERIFIED_TERMINAL",
    "verify_product_v022_history",
)
