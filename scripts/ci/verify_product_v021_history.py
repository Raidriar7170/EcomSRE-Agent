#!/usr/bin/env python3
"""Verify immutable Product v0.2 predecessor evidence for the v0.2.1 successor."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import stat
import subprocess
from typing import Any, Sequence

from scripts.ci.verify_product_v02_blocked_result import (
    verify_product_v02_blocked_result,
)
from scripts.ci.verify_product_v02_history import verify_product_v02_history


PUBLIC_MAIN = "8398a063de048064f160a7ffed236fbb3327b701"
PREDECESSOR_HEAD = "a439f8882cd2fcdd3767f6bcfd5d955219fa1e15"
PREDECESSOR_TERMINAL = "BLOCKED_ECOMSRE_PRODUCT_V02_UNKNOWN_FAULT_PROFILE"
VERIFIED_TERMINAL = "ECOMSRE_PRODUCT_V021_HISTORY_VERIFIED"
REQUIRED_PATHS = (
    "config/product-v02/live-pilot/calibration-consumed.json",
    "docs/analysis/product-v02-cleanup-closure.json",
    "docs/analysis/product-v02-profile-calibration.json",
    "docs/analysis/product-v02-progress.json",
    "docs/external-reviews/product-v02-pre-campaign-review.md",
    "docs/results/product-v02-live-knowledge-loop-limitations.md",
    "docs/results/product-v02-live-knowledge-loop.json",
    "docs/results/product-v02-live-knowledge-loop.md",
)
REQUIRED_BINDINGS = {
    "config/product-v02/live-pilot/calibration-consumed.json": (
        "PREDECESSOR_CONSUMED_MARKER"
    ),
    "docs/analysis/product-v02-cleanup-closure.json": (
        "PREDECESSOR_CLEANUP_CLOSURE"
    ),
    "docs/analysis/product-v02-profile-calibration.json": (
        "PREDECESSOR_PUBLIC_CALIBRATION"
    ),
    "docs/analysis/product-v02-progress.json": "PREDECESSOR_PROGRESS",
    "docs/external-reviews/product-v02-pre-campaign-review.md": (
        "PREDECESSOR_PRE_CAMPAIGN_REVIEW"
    ),
    "docs/results/product-v02-live-knowledge-loop-limitations.md": (
        "PREDECESSOR_LIMITATIONS"
    ),
    "docs/results/product-v02-live-knowledge-loop.json": (
        "PREDECESSOR_BLOCKED_RESULT"
    ),
    "docs/results/product-v02-live-knowledge-loop.md": "PREDECESSOR_BLOCKED_REPORT",
}


def _load_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Product v0.2.1 historical manifest must be an object")
    return payload


def _regular_bytes(root: Path, relative: str) -> bytes:
    candidate = Path(relative)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise ValueError("Product v0.2.1 historical path is not repository-relative")
    resolved = root / candidate
    metadata = resolved.lstat()
    if not stat.S_ISREG(metadata.st_mode):
        raise ValueError(f"Product v0.2.1 historical path is not regular: {relative}")
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


def verify_product_v021_history(
    project_root: Path,
    manifest_path: Path | None = None,
) -> dict[str, object]:
    root = project_root.resolve(strict=True)
    manifest = _load_object(
        manifest_path or root / "config/product-v021/historical-results.v1.json"
    )
    if (
        manifest.get("schema_version")
        != "ecomsre.product-v021.historical-results.v1"
        or manifest.get("goal_version")
        != "ecomsre-product-v021-live-baseline-knowledge-loop-successor-v1"
        or manifest.get("public_main_base") != PUBLIC_MAIN
        or manifest.get("predecessor_pr") != 75
        or manifest.get("predecessor_branch")
        != "codex/product-v02-live-knowledge-loop-pilot"
        or manifest.get("predecessor_head") != PREDECESSOR_HEAD
        or manifest.get("predecessor_terminal") != PREDECESSOR_TERMINAL
        or manifest.get("predecessor_blocker_stage") != "PRODUCT_BASELINE"
        or manifest.get("predecessor_blocker_code")
        != "BASELINE_INSUFFICIENT_WINDOWS"
    ):
        raise ValueError("Product v0.2.1 predecessor identity differs")

    expected_classification = {
        "baseline_readiness": "NOT_ESTABLISHED",
        "engineering_harness": "PRESENT",
        "fault_attempt": "NOT_STARTED",
        "measured_knowledge_loop_result": "ABSENT",
        "pilot_outcome": "NOT_REACHED",
    }
    expected_counters = {
        "accepted_positive_episodes": 0,
        "agent_writes": 0,
        "heldout_recurrences": 0,
        "live_calibration_attempts": 0,
        "runbook_executions": 0,
    }
    if manifest.get("classification") != expected_classification:
        raise ValueError("Product v0.2.1 predecessor classification differs")
    if manifest.get("counters") != expected_counters:
        raise ValueError("Product v0.2.1 predecessor counters differ")

    private = manifest.get("private_evidence")
    if not isinstance(private, dict) or private != {
        "content_audit_claimed": False,
        "preserved_private_report_sha256": (
            "81442f6cd7a0a36c5cda703d6d054d2ff57fbc4f6f4a9aa7630626d74dbb3c9b"
        ),
        "tracked_in_git": False,
    }:
        raise ValueError("Product v0.2.1 private predecessor boundary differs")

    files = manifest.get("files")
    if not isinstance(files, list) or len(files) != 8:
        raise ValueError("Product v0.2.1 predecessor file set differs")
    paths = tuple(sorted(str(item.get("path")) for item in files if isinstance(item, dict)))
    bindings = {
        str(item.get("path")): str(item.get("role"))
        for item in files
        if isinstance(item, dict)
    }
    if paths != REQUIRED_PATHS or bindings != REQUIRED_BINDINGS:
        raise ValueError("Product v0.2.1 predecessor bindings differ")
    for item in files:
        if not isinstance(item, dict):
            raise ValueError("Product v0.2.1 predecessor binding is malformed")
        relative = item.get("path")
        expected_sha256 = item.get("sha256")
        expected_size = item.get("size_bytes")
        if not isinstance(relative, str) or not isinstance(expected_sha256, str):
            raise ValueError("Product v0.2.1 predecessor binding is malformed")
        current = _regular_bytes(root, relative)
        if len(current) != expected_size or hashlib.sha256(current).hexdigest() != expected_sha256:
            raise ValueError(f"Product v0.2.1 predecessor byte drift: {relative}")
        if _git_bytes(root, PREDECESSOR_HEAD, relative) != current:
            raise ValueError(f"Product v0.2.1 predecessor head binding drift: {relative}")

    _require_ancestry(root, PUBLIC_MAIN, PREDECESSOR_HEAD)
    _require_ancestry(root, PREDECESSOR_HEAD, "HEAD")
    v01 = verify_product_v02_history(root)
    v02 = verify_product_v02_blocked_result(root)
    if v01.get("status") != "ECOMSRE_PRODUCT_V02_HISTORY_VERIFIED":
        raise ValueError("Product v0.1 historical verifier did not pass")
    if v02.get("status") != "ECOMSRE_PRODUCT_V02_BLOCKED_RESULT_VERIFIED":
        raise ValueError("Product v0.2 blocked-result verifier did not pass")
    return {
        "status": VERIFIED_TERMINAL,
        "predecessor_head": PREDECESSOR_HEAD,
        "predecessor_terminal": PREDECESSOR_TERMINAL,
        "bound_file_count": len(files),
        "live_attempt_count": 0,
        "pilot_outcome": "NOT_REACHED",
        "private_content_audit_claimed": False,
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
            verify_product_v021_history(arguments.project_root, arguments.manifest),
            separators=(",", ":"),
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = (
    "PREDECESSOR_HEAD",
    "PREDECESSOR_TERMINAL",
    "VERIFIED_TERMINAL",
    "verify_product_v021_history",
)
