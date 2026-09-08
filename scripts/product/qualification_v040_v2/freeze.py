"""Exact policy/reviewer binding and a repository-wide create-once no-fault fuse."""

from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import subprocess
from typing import Any

from scripts.product.qualification_v040.guard import require, sha
from scripts.product.qualification_v040_v2.policy import validate_policy
from scripts.product.v040_runtime import seal_private

BASE = "e8a8afe935d972732bd37034b86af43d1c969ed8"
WRITES = (
    "scripts/product/qualification_v040_v2/",
    "tests/product_v040/qualification_v2/",
    "config/product-v040/copyup-access-v2/",
    "docs/results/product-v040-qualification-v2/",
    "scripts/ci/verify_product_v040_qualification_v2.py",
)


def git(repository: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", "-C", str(repository), *args], text=True
    ).strip()


def verify_freeze(repository: Path) -> dict[str, Any]:
    require(not git(repository, "status", "--porcelain"), "SOURCE_TREE_DIRTY")
    require(git(repository, "merge-base", BASE, "HEAD") == BASE, "SOURCE_BASE_DRIFT")
    changed = git(repository, "diff", "--name-status", BASE).splitlines()
    require(
        all(line.startswith("A\t") and line[2:].startswith(WRITES) for line in changed),
        "HISTORICAL_SOURCE_MUTATION",
    )
    root = repository / "config/product-v040/copyup-access-v2"
    policy_path = root / "policy.json"
    policy = json.loads(policy_path.read_bytes())
    validate_policy(policy)
    review = json.loads(
        (
            repository
            / "docs/results/product-v040-qualification-v2/pre-runtime-review.json"
        ).read_bytes()
    )
    require(
        review["status"] == "PASS"
        and review["must_fix"] == 0
        and review["claim_accuracy"] == "PASS"
        and review["runtime_decision"] == "FRESH_NOFAULT_QUALIFICATION_ALLOW"
        and review["justified_successor_security_contract"] is True,
        "POLICY_REVIEW_WITHHOLD",
    )
    require(
        hashlib.sha256(policy_path.read_bytes()).hexdigest()
        == review["policy_file_sha256"]
        and sha(policy) == review["policy_sha256"],
        "POLICY_FREEZE_DRIFT",
    )
    source_paths = {
        p.relative_to(repository).as_posix()
        for directory in (
            "scripts/product/qualification_v040_v2",
            "scripts/product/qualification_v040",
        )
        for p in (repository / directory).glob("*.py")
    }
    require(
        set(review["implementation_sha256"]) == source_paths,
        "IMPLEMENTATION_BINDING_INCOMPLETE",
    )
    require(
        all(
            hashlib.sha256((repository / p).read_bytes()).hexdigest() == digest
            for p, digest in review["implementation_sha256"].items()
        ),
        "IMPLEMENTATION_FREEZE_DRIFT",
    )
    return policy


def consume_once(
    repository: Path, authorization_sha256: str, qualification: str
) -> dict[str, Any]:
    require(
        len(authorization_sha256) == 64 and len(qualification) == 32,
        "CONSUMPTION_IDENTITY_INVALID",
    )
    common = Path(git(repository, "rev-parse", "--git-common-dir"))
    if not common.is_absolute():
        common = (repository / common).resolve()
    root = common / "ecomsre-no-fault-qualification-authorizations"
    root.mkdir(mode=0o700, exist_ok=True)
    require(
        not root.is_symlink() and root.stat().st_mode & 0o077 == 0,
        "CONSUMPTION_ROOT_UNSAFE",
    )
    result = {
        "authorization_sha256": authorization_sha256,
        "qualification_id": qualification,
        "source_head": git(repository, "rev-parse", "HEAD"),
        "no_fault_qualification_count": 1,
        "formal_campaign_count": 0,
        "formal_allowance_consumed": False,
    }
    path = root / (authorization_sha256 + ".json")
    require(
        not path.exists() and not path.is_symlink(),
        "NOFAULT_ALLOWANCE_ALREADY_CONSUMED",
    )
    old = os.umask(0o077)
    try:
        seal_private(path, result)  # O_EXCL, shared across all repository worktrees.
    finally:
        os.umask(old)
    return result
