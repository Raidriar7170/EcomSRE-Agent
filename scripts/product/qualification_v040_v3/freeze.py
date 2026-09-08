"""Frozen Goal, cleanup, reviewer and exact-head CI admission; no runtime side effects."""

from __future__ import annotations
import hashlib
import json
from pathlib import Path
import subprocess
from typing import Any

from scripts.product.qualification_v040.guard import require
from scripts.product.qualification_v040_v2.freeze import git, consume_once
from scripts.product.qualification_v040_v2.policy import validate_policy
from scripts.product.qualification_v040_v3.retained_cleanup import GOAL_SHA

BASE = "7d1c975e3450901a6b09fcbca8c0cd82f24cfc14"
GOAL_PATH = "docs/goals/EcomSRE_Product_v0.4_Runtime_Qualification_Successor_v3_Goal.md"
WRITES = (
    "scripts/product/qualification_v040_v3/",
    "tests/product_v040/qualification_v3/",
    "config/product-v040/runtime-qualification-v3/",
    "docs/results/product-v040-qualification-v3/",
    GOAL_PATH,
)


def implementation_hashes(repository: Path) -> dict[str, str]:
    return {
        p.relative_to(repository).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for namespace in (
            "qualification_v040",
            "qualification_v040_v2",
            "qualification_v040_v3",
        )
        for p in sorted((repository / "scripts/product" / namespace).glob("*.py"))
    }


def verify_freeze(repository: Path) -> dict[str, Any]:
    require(not git(repository, "status", "--porcelain"), "SOURCE_TREE_DIRTY")
    require(git(repository, "merge-base", BASE, "HEAD") == BASE, "SOURCE_BASE_DRIFT")
    require(
        all(
            row.startswith("A\t") and row[2:].startswith(WRITES)
            for row in git(repository, "diff", "--name-status", BASE).splitlines()
        ),
        "FROZEN_HISTORY_MUTATION",
    )
    require(
        hashlib.sha256((repository / GOAL_PATH).read_bytes()).hexdigest() == GOAL_SHA,
        "GOAL_FREEZE_DRIFT",
    )
    out = repository / "docs/results/product-v040-qualification-v3"
    cleanup = json.loads((out / "CleanupReceipt.json").read_bytes())
    require(
        cleanup["status"] == "CLEAN"
        and cleanup["remaining_retained_containers"]
        == cleanup["remaining_retained_volumes"]
        == 0,
        "RETAINED_CLEANUP_INCOMPLETE",
    )
    review = json.loads((out / "pre-runtime-review.json").read_bytes())
    require(
        review["status"] == "PASS"
        and review["must_fix"] == 0
        and review["claim_accuracy"] == "PASS"
        and review["runtime_decision"] == "FRESH_NOFAULT_QUALIFICATION_ALLOW",
        "PRE_RUNTIME_REVIEW_WITHHOLD",
    )
    require(
        review["implementation_sha256"] == implementation_hashes(repository),
        "IMPLEMENTATION_FREEZE_DRIFT",
    )
    require(review["goal_sha256"] == GOAL_SHA, "REVIEW_GOAL_DRIFT")
    path = (
        repository
        / "config/product-v040/runtime-qualification-v3/fingerprint-policy.json"
    )
    require(
        hashlib.sha256(path.read_bytes()).hexdigest()
        == review["fingerprint_policy_file_sha256"],
        "FINGERPRINT_POLICY_DRIFT",
    )
    fingerprint = json.loads(path.read_bytes())
    copyup = repository / fingerprint["copyup_policy_path"]
    require(
        hashlib.sha256(copyup.read_bytes()).hexdigest()
        == fingerprint["copyup_policy_sha256"],
        "COPYUP_POLICY_DRIFT",
    )
    policy = json.loads(copyup.read_bytes())
    validate_policy(policy)
    receipt_path = repository / ".local/runtime-qualification-v3/ci-admission.json"
    ci = json.loads(receipt_path.read_bytes())
    live = json.loads(
        subprocess.check_output(
            [
                "gh",
                "run",
                "view",
                str(ci["run_id"]),
                "--json",
                "headSha,status,conclusion,event,workflowName,url",
            ],
            cwd=repository,
            text=True,
        )
    )
    require(
        live["headSha"] == git(repository, "rev-parse", "HEAD")
        and live["status"] == "completed"
        and live["conclusion"] == "success"
        and live["event"] == "workflow_dispatch"
        and live["workflowName"] == "Agent mainline",
        "EXACT_HEAD_CI_REQUIRED",
    )
    jobs = json.loads(
        subprocess.check_output(
            ["gh", "run", "view", str(ci["run_id"]), "--json", "jobs"],
            cwd=repository,
            text=True,
        )
    )["jobs"]
    require(
        any(
            job["name"] == "Offline replay and verification"
            and job["conclusion"] == "success"
            for job in jobs
        ),
        "CI_REQUIRED_JOB_MISSING",
    )
    return policy


__all__ = [
    "BASE",
    "GOAL_PATH",
    "GOAL_SHA",
    "verify_freeze",
    "consume_once",
    "implementation_hashes",
]
