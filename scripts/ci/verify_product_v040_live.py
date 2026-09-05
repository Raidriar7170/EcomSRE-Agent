"""Offline binding verifier. Never starts Docker, calls a Provider or remediates."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess

from ecomsre.product.remediation.live_evidence import (
    LiveManifestV040,
    LiveResultV040,
    PASS,
)
from ecomsre.product.remediation.repository import REGISTRY_SHA256
from ecomsre.product.remediation.payment_control import digest
from scripts.ci.verify_product_v040_history import verify as verify_history

PATHS = (
    "config/product-v040/live-campaign-manifest.v1.json",
    "docs/results/product-v040-payment-live-acceptance.json",
    "docs/analysis/product-v040-live-evidence-manifest.json",
)
SOURCE_PATTERNS = (
    "Dockerfile.product",
    "pyproject.toml",
    "uv.lock",
    "src",
    "config/product-v040/remediation-registry.v1.json",
    "scripts/product/v040_*.py",
    "scripts/product/run_payment_v040.py",
    "scripts/product/export_payment_v040.py",
    "scripts/live_sandbox/product_v040.py",
    "scripts/ci/verify_product_v040_live.py",
    "config/product-v040/live-profile.v1.json",
    "config/product-v040/live-runtime.v1.yml",
    "config/product-v040/remediation-network.v1.yml",
    "docker-compose.product.yml",
)


def current_sources(root: Path) -> dict[str, dict[str, str]]:
    raw = subprocess.run(
        ("git", "ls-files", "--stage", "-z", "--", *SOURCE_PATTERNS),
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    bindings = {}
    for record in filter(None, raw.split("\0")):
        metadata, name = record.split("\t", 1)
        mode, _, stage = metadata.split()
        if (
            stage != "0"
            or mode not in {"100644", "100755"}
            or (root / name).is_symlink()
        ):
            raise ValueError("invalid source index binding")
        bindings[name] = {
            "git_mode": mode,
            "sha256": hashlib.sha256((root / name).read_bytes()).hexdigest(),
        }
    return bindings


def historical_sources(root: Path, head: str) -> tuple[str, dict[str, dict[str, str]]]:
    def git(*args: str) -> bytes:
        return subprocess.run(("git", *args), cwd=root, check=True, capture_output=True).stdout

    tree = git("rev-parse", f"{head}^{{tree}}").decode().strip()
    bindings = {}
    # Walk once; reuse the exact current source-selection patterns below through
    # git's commit-aware pathspec support, without consulting a mutable index.
    names = git("ls-tree", "-r", "-z", head).split(b"\0")
    from fnmatch import fnmatchcase

    for record in filter(None, names):
        metadata, raw_name = record.split(b"\t", 1)
        name = raw_name.decode()
        if not any(name == pattern or name.startswith(pattern + "/")
                   or fnmatchcase(name, pattern) for pattern in SOURCE_PATTERNS):
            continue
        mode, kind, object_id = metadata.decode().split()
        if kind != "blob" or mode not in {"100644", "100755"}:
            raise ValueError("historical source is not a regular blob")
        bindings[name] = {"git_mode": mode, "sha256": hashlib.sha256(git("cat-file", "blob", object_id)).hexdigest()}
    return tree, bindings


def verify_code_binding(root: Path, manifest: LiveManifestV040, bindings: object) -> None:
    tree, historical = historical_sources(root, manifest.code_head)
    if tree != manifest.code_tree or historical != bindings or bindings != current_sources(root):
        raise ValueError("measured Git commit/tree/source binding differs")
    build_inputs = {name: value for name, value in historical.items()
                    if name in {"Dockerfile.product", "pyproject.toml", "uv.lock",
                                "config/product-v040/remediation-registry.v1.json"}
                    or name.startswith("src/")}
    if digest(build_inputs) != manifest.source_inputs_sha256:
        raise ValueError("measured build input digest differs")


def verify(root: Path) -> dict[str, object]:
    history = verify_history(root)
    exists = [(root / name).exists() for name in PATHS]
    if not any(exists):
        return {
            "status": "PRE_EXECUTION_ONLY",
            "measured_live_result": False,
            "history": history,
        }
    if not all(exists):
        raise ValueError("partial public live evidence cannot be accepted")
    manifest = LiveManifestV040.model_validate_json((root / PATHS[0]).read_bytes())
    result = LiveResultV040.model_validate_json((root / PATHS[1]).read_bytes())
    evidence = json.loads((root / PATHS[2]).read_bytes())
    if (
        set(evidence)
        != {
            "schema_version",
            "goal_sha256",
            "code_head",
            "private_evidence_sha256",
            "private_file_count",
            "source_bindings",
            "artifacts",
        }
        or evidence["schema_version"]
        != "ecomsre.product.v040.public-evidence-manifest.v1"
    ):
        raise ValueError("public evidence manifest fields differ")
    verify_code_binding(root, manifest, evidence["source_bindings"])
    expected_artifacts = {
        name: hashlib.sha256((root / name).read_bytes()).hexdigest()
        for name in PATHS[:2]
    }
    if (
        evidence["artifacts"] != expected_artifacts
        or evidence["private_evidence_sha256"] != result.preserved_evidence_sha256
        or type(evidence["private_file_count"]) is not int
        or evidence["private_file_count"] < 1
    ):
        raise ValueError("public artifact or private-evidence binding differs")
    if (
        result.manifest_sha256 != manifest.manifest_sha256
        or any(
            getattr(result, name) != getattr(manifest, name)
            for name in (
                "goal_sha256",
                "code_head",
                "environment_id",
                "baseline_id",
                "baseline_sha256",
            )
        )
        or evidence["goal_sha256"] != manifest.goal_sha256
        or evidence["code_head"] != manifest.code_head
    ):
        raise ValueError("result/manifest parent binding differs")
    profile_path = root / "config/product-v040/live-profile.v1.json"
    profile = json.loads(profile_path.read_bytes())
    if (
        hashlib.sha256(profile_path.read_bytes()).hexdigest()
        != manifest.runtime_profile_sha256
        or manifest.registry_sha256 != REGISTRY_SHA256
    ):
        raise ValueError("frozen workload/registry differs")
    if (
        manifest.policy.window_seconds != profile["recovery_window_seconds"]
        or manifest.policy.minimum_business_requests
        != profile["recovery_minimum_requests"]
        or manifest.policy.business_error_ratio_max
        != profile["recovery_business_error_ratio_max"]
    ):
        raise ValueError("recovery threshold differs from the frozen profile")
    if result.receipt is None and result.counts.forward_mutations not in {0, None}:
        raise ValueError("missing receipt cannot confirm a forward mutation")
    if result.terminal == PASS:
        assert result.receipt is not None and result.evaluation is not None
        if (
            result.receipt.before_state_digest
            != manifest.policy.fault_configuration_digest
            or result.receipt.after_state_digest
            != manifest.policy.baseline_configuration_digest
            or result.evaluation.policy_sha256 != manifest.policy.policy_sha256
        ):
            raise ValueError("live receipt/policy binding differs")
        if any(
            (window.ended_at - window.started_at).total_seconds()
            != manifest.policy.window_seconds
            or (window.created_at - window.ended_at).total_seconds() > 30
            for window in result.recovery_windows
        ):
            raise ValueError("live window duration or finalization differs")
    return {
        "status": "PASS",
        "measured_live_result": True,
        "terminal": result.terminal,
        "code_head": result.code_head,
        "result_sha256": result.result_sha256,
        "history": history,
        "verification_scope": "PUBLIC_BINDINGS_AND_DETERMINISTIC_CONTRACTS; PRIVATE_EVIDENCE_REVIEW_SEPARATE",
    }


if __name__ == "__main__":
    print(
        json.dumps(
            verify(Path(__file__).resolve().parents[2]), indent=2, sort_keys=True
        )
    )
