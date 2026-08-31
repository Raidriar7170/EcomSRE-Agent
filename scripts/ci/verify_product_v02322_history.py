#!/usr/bin/env python3
"""Verify frozen PR #82 and PR #83 history for Product v0.2.3.2.2."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
from typing import Any, Mapping, Sequence

from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22
from ecomsre.product.pilot.repository_state_v02322 import (
    HISTORY_AND_BLOCKER_PASS_V02322,
)
from scripts.ci.verify_product_v02321_history import (
    resolve_product_history_descendant,
    verify_product_v02321_history,
)


STARTING_MAIN_V02322 = "73fe478886a4f0875b4d60b07b3600e8aae02132"
PR82_HEAD_V02322 = "cc270e5624af573a12bc31f3df9ca8cacad8685d"
PR83_HEAD_V02322 = "142dc1094926f18e789ece3668c34918f859b512"
EXPECTED_MANIFEST_SHA256_V02322 = (
    "748f79c156b8597bb145292aaac046eede351231480aa71eb805d74955f51685"
)
FORMAL_BLOCKER_SHA256_V02322 = (
    "2f8f6fd26c7783091c00fb9cdcfaa29f145b4d29b31f16ec6ac1c8fb3e9999f1"
)
FORMAL_EVIDENCE_MANIFEST_SHA256_V02322 = (
    "6104953a87e3307ae826de6e3348d651d82fd7708f7dbf8341962666a0b93129"
)


def _load_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return payload


def _git_bytes(root: Path, revision: str, relative: str) -> bytes:
    return subprocess.run(
        ("git", "show", f"{revision}:{relative}"),
        cwd=root,
        check=True,
        capture_output=True,
    ).stdout


def _require_commit(root: Path, revision: str) -> None:
    subprocess.run(
        ("git", "cat-file", "-e", f"{revision}^{{commit}}"),
        cwd=root,
        check=True,
        capture_output=True,
    )


def _require_ancestry(root: Path, ancestor: str, descendant: str) -> None:
    subprocess.run(
        ("git", "merge-base", "--is-ancestor", ancestor, descendant),
        cwd=root,
        check=True,
        capture_output=True,
    )


def _require_tracked_bytes(root: Path, tracked: object) -> None:
    if not isinstance(tracked, list) or not tracked:
        raise ValueError("Product v0.2.3.2.2 historical tracked files differ")
    observed_paths: list[str] = []
    for item in tracked:
        if not isinstance(item, Mapping):
            raise ValueError("Product v0.2.3.2.2 historical binding differs")
        relative = item.get("path")
        expected_sha256 = item.get("sha256")
        expected_size = item.get("size_bytes")
        if (
            not isinstance(relative, str)
            or item.get("revision") != PR83_HEAD_V02322
            or not isinstance(expected_sha256, str)
            or not isinstance(expected_size, int)
        ):
            raise ValueError("Product v0.2.3.2.2 historical binding differs")
        observed_paths.append(relative)
        path = root / relative
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"Product v0.2.3.2.2 frozen path differs: {relative}")
        local_bytes = path.read_bytes()
        predecessor_bytes = _git_bytes(root, PR83_HEAD_V02322, relative)
        if (
            local_bytes != predecessor_bytes
            or len(local_bytes) != expected_size
            or hashlib.sha256(local_bytes).hexdigest() != expected_sha256
        ):
            raise ValueError(f"Product v0.2.3.2.2 frozen bytes differ: {relative}")
    if observed_paths != sorted(set(observed_paths)):
        raise ValueError("Product v0.2.3.2.2 historical path set differs")


def _require_private_bindings(
    manifest: Mapping[str, Any],
    evidence_manifest: Mapping[str, Any],
) -> None:
    private = manifest.get("private_artifacts")
    if not isinstance(private, list) or len(private) != 10:
        raise ValueError("Product v0.2.3.2.2 private bindings differ")
    evidence = evidence_manifest.get("artifacts")
    if not isinstance(evidence, Mapping):
        raise ValueError("Product v0.2.3.2.2 blocker evidence differs")
    by_path: dict[str, tuple[str, str]] = {}
    for value in evidence.values():
        if not isinstance(value, Mapping) or not isinstance(
            value.get("private_path"), str
        ):
            continue
        file_sha256 = value.get("file_sha256", value.get("private_file_sha256"))
        semantic_sha256 = value.get("semantic_sha256")
        if not isinstance(file_sha256, str) or not isinstance(semantic_sha256, str):
            raise ValueError("Product v0.2.3.2.2 blocker evidence differs")
        by_path[value["private_path"]] = (file_sha256, semantic_sha256)
    observed_paths: list[str] = []
    for item in private:
        if not isinstance(item, Mapping) or not isinstance(item.get("path"), str):
            raise ValueError("Product v0.2.3.2.2 private bindings differ")
        path = item["path"]
        observed_paths.append(path)
        if by_path.get(path) != (item.get("file_sha256"), item.get("semantic_sha256")):
            raise ValueError("Product v0.2.3.2.2 private bindings differ")
    if len(observed_paths) != len(set(observed_paths)) or set(observed_paths) != set(
        by_path
    ):
        raise ValueError("Product v0.2.3.2.2 private bindings differ")


def verify_product_v02322_history(
    root: Path,
    *,
    manifest_path: Path | None = None,
    descendant_revision: str | None = None,
) -> dict[str, object]:
    project = Path(root).resolve(strict=True)
    descendant = descendant_revision or resolve_product_history_descendant(project)
    manifest = _load_object(
        manifest_path or project / "config/product-v02322/historical-results.v1.json"
    )
    body = dict(manifest)
    supplied_manifest_sha256 = body.pop("manifest_sha256", None)
    if (
        manifest.get("schema_version") != "ecomsre.product-v02322.historical-results.v1"
        or manifest.get("goal_version")
        != "ecomsre-product-v02322-diagnosis-forensics-replay-v1"
        or manifest.get("starting_main") != STARTING_MAIN_V02322
        or supplied_manifest_sha256 != semantic_sha256_v22(body)
        or supplied_manifest_sha256 != EXPECTED_MANIFEST_SHA256_V02322
    ):
        raise ValueError("Product v0.2.3.2.2 historical manifest differs")

    predecessors = manifest.get("predecessors")
    if not isinstance(predecessors, Mapping):
        raise ValueError("Product v0.2.3.2.2 historical manifest differs")
    pr82 = predecessors.get("pr82")
    pr83 = predecessors.get("pr83")
    if (
        not isinstance(pr82, Mapping)
        or not isinstance(pr83, Mapping)
        or pr82.get("head") != PR82_HEAD_V02322
        or pr82.get("terminal") != "BLOCKED_ECOMSRE_PRODUCT_V0232_TRAFFIC_PREFLIGHT"
        or pr82.get("safe_error_code") != "RUN_ID_SCHEMA_PATTERN_MISMATCH"
        or pr83.get("head") != PR83_HEAD_V02322
        or pr83.get("formal_terminal")
        != "BLOCKED_ECOMSRE_PRODUCT_V02321_NOFAULT_INFRASTRUCTURE"
        or pr83.get("repository_terminal")
        != "BLOCKED_ECOMSRE_PRODUCT_V02321_REPOSITORY_ACCEPTANCE"
        or pr83.get("formal_blocker_semantic_sha256") != FORMAL_BLOCKER_SHA256_V02322
        or pr83.get("formal_blocker_evidence_manifest_sha256")
        != FORMAL_EVIDENCE_MANIFEST_SHA256_V02322
        or pr83.get("formal_traffic_completed") != 30
        or pr83.get("formal_traffic_retries") != 0
        or pr83.get("successor_incident_count") != 1
        or pr83.get("successor_diagnosis_count") != 0
        or pr83.get("action_authority") != "NONE"
    ):
        raise ValueError("Product v0.2.3.2.2 historical manifest differs")

    _require_commit(project, STARTING_MAIN_V02322)
    _require_commit(project, PR82_HEAD_V02322)
    _require_commit(project, PR83_HEAD_V02322)
    _require_commit(project, descendant)
    _require_ancestry(project, STARTING_MAIN_V02322, PR82_HEAD_V02322)
    _require_ancestry(project, PR82_HEAD_V02322, PR83_HEAD_V02322)
    _require_ancestry(project, PR83_HEAD_V02322, descendant)
    _require_tracked_bytes(project, manifest.get("tracked_files"))

    prior = verify_product_v02321_history(
        project,
        descendant_revision=descendant,
    )
    if prior["blocker_terminal"] != pr82["terminal"]:
        raise ValueError("Product v0.2.3.2.2 PR #82 blocker differs")

    blocker = _load_object(project / "docs/analysis/product-v02321-formal-blocker.json")
    blocker_body = dict(blocker)
    blocker_sha256 = blocker_body.pop("blocker_sha256", None)
    evidence = _load_object(
        project / "docs/analysis/product-v02321-formal-blocker-evidence-manifest.json"
    )
    evidence_body = dict(evidence)
    evidence_sha256 = evidence_body.pop("manifest_sha256", None)
    if (
        blocker_sha256 != semantic_sha256_v22(blocker_body)
        or blocker_sha256 != FORMAL_BLOCKER_SHA256_V02322
        or evidence_sha256 != semantic_sha256_v22(evidence_body)
        or evidence_sha256 != FORMAL_EVIDENCE_MANIFEST_SHA256_V02322
    ):
        raise ValueError("Product v0.2.3.2.2 blocker evidence differs")
    _require_private_bindings(manifest, evidence)

    return {
        "terminal": HISTORY_AND_BLOCKER_PASS_V02322,
        "pr82_terminal": pr82["terminal"],
        "pr83_formal_terminal": pr83["formal_terminal"],
        "pr83_repository_terminal": pr83["repository_terminal"],
        "predecessor_head": PR83_HEAD_V02322,
        "formal_traffic_completed": pr83["formal_traffic_completed"],
        "successor_incident_count": pr83["successor_incident_count"],
        "successor_diagnosis_count": pr83["successor_diagnosis_count"],
        "product_cleanup": pr83["product_cleanup"],
        "demo_cleanup": pr83["demo_cleanup"],
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--root", type=Path, default=Path(__file__).resolve().parents[2]
    )
    arguments = parser.parse_args(argv)
    print(json.dumps(verify_product_v02322_history(arguments.root), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ("verify_product_v02322_history",)
