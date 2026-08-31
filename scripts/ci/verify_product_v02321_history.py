#!/usr/bin/env python3
"""Verify frozen PR #82 history before Product v0.2.3.2.1 reuse."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
from typing import Any, Mapping, Sequence

from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22
from scripts.ci.verify_product_v0232_blocker import verify_product_v0232_blocker


HISTORY_AND_REUSE_PASS_V02321 = "ECOMSRE_PRODUCT_V02321_HISTORY_AND_REUSE_PASS"
PREDECESSOR_HEAD_V02321 = "cc270e5624af573a12bc31f3df9ca8cacad8685d"
BLOCKER_EVIDENCE_COMMIT_V02321 = "6f5a629561d3e5e42dc3ae39ce13e3dc6e907be4"
_EXPECTED_PREDECESSOR = {
    "pr": 82,
    "branch": "codex/product-v0232-healthy-traffic-evidence-nofault",
    "head": PREDECESSOR_HEAD_V02321,
    "blocker_evidence_commit": BLOCKER_EVIDENCE_COMMIT_V02321,
    "terminal": "BLOCKED_ECOMSRE_PRODUCT_V0232_TRAFFIC_PREFLIGHT",
    "attempt_ordinal": 1,
    "attempt_consumed": True,
    "attempt_2_authorized": False,
    "attempt_sha256": "5080a440ca6a96cb8b93f104f873ace85ddef01cdc508d6a5656e4219a0221f9",
    "blocker_addendum_sha256": "775e8607768076340cbf4ad81a56bcc91bc1c1f99796e3e92e8f705fc9c8ffa5",
    "failure_stage": "RUNTIME_INSPECT_REQUEST_BUILD",
    "safe_error_code": "RUN_ID_SCHEMA_PATTERN_MISMATCH",
    "observed_run_id": "product-v0232-traffic-preflight-1",
    "required_run_id_pattern": "^[0-9a-f]{32}$",
    "traffic_transactions": 0,
    "formal_healthy_traffic_execution_count": 0,
    "successor_incident_count": 0,
    "successor_diagnosis_count": 0,
    "product_cleanup": "CLEAN",
    "demo_cleanup": "BLOCKED_BASELINE_UNCHANGED_UNPROVEN",
}
_EXPECTED_FROZEN_BINDINGS = {
    "traffic_campaign_sha256": "7a62a210bb8e8194ee9d8a601ac678c41f0e32c87214c806f4a304d046e510b1",
    "preflight_profile_sha256": "20481ac92973ccf5de7510f565f066f13b9e1161e0e36faecec11cd12a40aa4a",
    "formal_profile_sha256": "0110803ab9b39bf397295f1fd8904aee31fabf9b82b314bf586fae98188f6ce7",
    "traffic_contract_sha256": "8e2e6fabb139413ff5ff54efe516023e00f7d04c7b84b4d296b1aa42bf39ce1b",
    "source_product_state_sha256": "0860c3cefe795378b36293342fa7250bab97bb75e8767d3b5a8c200c3e05741c",
    "predecessor_product_state_clone_sha256": "6920044cea06a68f38624803468aeeb0f854caee695f7f876ff2d6f6ef074205",
}
_EXPECTED_TRACKED_FILES = {
    "config/product-v0232/campaign.json": (
        "0fbbc112ee59c2f7bfcb825c56a42442861edfdd2108566cc3ed87b5d1c6d2b1",
        2020,
    ),
    "config/product-v0232/historical-results.v1.json": (
        "dbc1ec5c66729e5e8270f0b64ad54b9f747f7fae131b66005b4da237b8489471",
        5399,
    ),
    "config/product-v0232/traffic/contract.json": (
        "b812f882a01ace100eecacb36f0381c7c50597753b760da7261d29a8e88ad309",
        3725,
    ),
    "config/product-v0232/traffic/formal-profile.json": (
        "17b6a653e023dfc4f6e649c45fa1cecb5becc24edafd9100f4eaca1aa4e552f5",
        408,
    ),
    "config/product-v0232/traffic/preflight-profile.json": (
        "f7655ca86fc4b02da71a07688be2074c371300af89d70c17c4fccac6ed0d16f4",
        410,
    ),
    "docs/analysis/product-v0232-evidence-binding-preflight.json": (
        "fbfd91c88dd5d36e64e81ace02a9140b371dfede5b222ac1e8e3829f96d80a61",
        4471,
    ),
    "docs/analysis/product-v0232-predecessor-audit.json": (
        "491cbb31729e9c4f2618a8d8ffbf6b67befc75e5aee8997b667d11358263a386",
        3082,
    ),
    "docs/analysis/product-v0232-product-state-clone.json": (
        "57033775b583c4cbb3b13d1581d829d4e9b375034e539294fe8e8f2e3603c1cc",
        2747,
    ),
    "docs/analysis/product-v0232-progress.json": (
        "0b3410f64c961ec1517feaefe72130819c6fed5e3a3b61a6105111f6279dc7c9",
        1207,
    ),
    "docs/analysis/product-v0232-traffic-contract.json": (
        "1a46ceeab794575c14d264240e25faa3b79ae399c93b27fbd6684e5141a6a492",
        2157,
    ),
    "docs/analysis/product-v0232-traffic-preflight-attempt-1-blocker-addendum.json": (
        "07bc202e85edcca00023acc29f0304af6cbd1ce50e7981f3e172492544361eec",
        1671,
    ),
    "docs/analysis/product-v0232-traffic-preflight-attempt-1.json": (
        "89d989f1c8edc107df22c0fbb231e00bb5faf201c0d0df22cc3ee2b81fbf985b",
        2570,
    ),
}


def _load_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Product v0.2.3.2.1 historical manifest differs")
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


def resolve_product_history_descendant(root: Path) -> str:
    """Resolve the PR successor head across a verified squash-merge handoff."""

    handoff_path = root / "docs/analysis/product-v02323-fresh-formal-handoff.json"
    if not handoff_path.exists():
        return "HEAD"
    handoff = _load_object(handoff_path)
    body = dict(handoff)
    supplied_sha256 = body.pop("handoff_sha256", None)
    successor_merge = handoff.get("successor_merge")
    merged_successor_commit = handoff.get("merged_successor_commit")
    if (
        supplied_sha256 != semantic_sha256_v22(body)
        or handoff.get("terminal")
        != "ECOMSRE_PRODUCT_V02323_FRESH_FORMAL_NOFAULT_HANDOFF_READY"
        or not isinstance(successor_merge, Mapping)
        or successor_merge.get("merge_commit_sha") != merged_successor_commit
        or not isinstance(merged_successor_commit, str)
        or re.fullmatch(r"[0-9a-f]{40}", merged_successor_commit) is None
    ):
        raise ValueError("Product successor squash handoff differs")
    successor_head = successor_merge.get("head_sha")
    if (
        not isinstance(successor_head, str)
        or re.fullmatch(r"[0-9a-f]{40}", successor_head) is None
    ):
        raise ValueError("Product successor squash head differs")
    _require_commit(root, merged_successor_commit)
    _require_commit(root, successor_head)
    _require_ancestry(root, merged_successor_commit, "HEAD")
    return successor_head


def _require_tracked_bytes(root: Path, tracked: object) -> None:
    expected_paths = tuple(sorted(_EXPECTED_TRACKED_FILES))
    if not isinstance(tracked, list) or len(tracked) != len(expected_paths):
        raise ValueError("Product v0.2.3.2.1 historical tracked files differ")
    observed_paths: list[str] = []
    for item in tracked:
        if not isinstance(item, Mapping):
            raise ValueError("Product v0.2.3.2.1 historical binding differs")
        relative = item.get("path")
        if (
            not isinstance(relative, str)
            or item.get("revision") != PREDECESSOR_HEAD_V02321
            or not isinstance(item.get("sha256"), str)
            or not isinstance(item.get("size_bytes"), int)
        ):
            raise ValueError("Product v0.2.3.2.1 historical binding differs")
        observed_paths.append(relative)
        expected_binding = _EXPECTED_TRACKED_FILES.get(relative)
        if (
            expected_binding is None
            or item.get("sha256") != expected_binding[0]
            or item.get("size_bytes") != expected_binding[1]
        ):
            raise ValueError("Product v0.2.3.2.1 historical tracked files differ")
        local_path = root / relative
        if local_path.is_symlink() or not local_path.is_file():
            raise ValueError("Product v0.2.3.2.1 frozen path differs")
        local_bytes = local_path.read_bytes()
        predecessor_bytes = _git_bytes(root, PREDECESSOR_HEAD_V02321, relative)
        if (
            local_bytes != predecessor_bytes
            or len(local_bytes) != item["size_bytes"]
            or hashlib.sha256(local_bytes).hexdigest() != item["sha256"]
        ):
            raise ValueError(f"Product v0.2.3.2.1 frozen bytes differ: {relative}")
    if tuple(observed_paths) != expected_paths:
        raise ValueError("Product v0.2.3.2.1 historical path set differs")


def verify_product_v02321_history(
    root: Path,
    *,
    manifest_path: Path | None = None,
    descendant_revision: str | None = None,
) -> dict[str, object]:
    project = Path(root).resolve(strict=True)
    descendant = descendant_revision or resolve_product_history_descendant(project)
    manifest = _load_object(
        manifest_path or project / "config/product-v02321/historical-results.v1.json"
    )
    body = dict(manifest)
    supplied_manifest_sha256 = body.pop("manifest_sha256", None)
    if (
        manifest.get("schema_version") != "ecomsre.product-v02321.historical-results.v1"
        or manifest.get("goal_version")
        != "ecomsre-product-v02321-traffic-harness-repair-nofault-v1"
        or manifest.get("starting_main") != "73fe478886a4f0875b4d60b07b3600e8aae02132"
        or manifest.get("predecessor") != _EXPECTED_PREDECESSOR
        or manifest.get("frozen_bindings") != _EXPECTED_FROZEN_BINDINGS
        or supplied_manifest_sha256 != semantic_sha256_v22(body)
    ):
        raise ValueError("Product v0.2.3.2.1 historical manifest differs")
    _require_commit(project, PREDECESSOR_HEAD_V02321)
    _require_commit(project, BLOCKER_EVIDENCE_COMMIT_V02321)
    _require_commit(project, descendant)
    _require_ancestry(project, BLOCKER_EVIDENCE_COMMIT_V02321, PREDECESSOR_HEAD_V02321)
    _require_ancestry(project, PREDECESSOR_HEAD_V02321, descendant)
    _require_tracked_bytes(project, manifest.get("tracked_files"))
    blocker = verify_product_v0232_blocker(project)
    if blocker != {
        "terminal": "BLOCKED_ECOMSRE_PRODUCT_V0232_TRAFFIC_PREFLIGHT",
        "attempt_ordinal": 1,
        "attempt_consumed": True,
        "attempt_2_authorized": False,
        "failure_stage": "RUNTIME_INSPECT_REQUEST_BUILD",
        "safe_error_code": "RUN_ID_SCHEMA_PATTERN_MISMATCH",
        "completed_transactions": 0,
        "formal_healthy_traffic_execution_count": 0,
        "action_authority": "NONE",
    }:
        raise ValueError("Product v0.2.3.2.1 frozen blocker differs")
    predecessor = manifest["predecessor"]
    frozen = manifest["frozen_bindings"]
    return {
        "terminal": HISTORY_AND_REUSE_PASS_V02321,
        "predecessor_head": PREDECESSOR_HEAD_V02321,
        "blocker_terminal": predecessor["terminal"],
        "attempt_sha256": predecessor["attempt_sha256"],
        "traffic_contract_sha256": frozen["traffic_contract_sha256"],
        "formal_healthy_traffic_execution_count": 0,
        "successor_incident_count": 0,
        "successor_diagnosis_count": 0,
        "product_cleanup": predecessor["product_cleanup"],
        "demo_cleanup": predecessor["demo_cleanup"],
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--root", type=Path, default=Path(__file__).resolve().parents[2]
    )
    arguments = parser.parse_args(argv)
    print(json.dumps(verify_product_v02321_history(arguments.root), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = (
    "HISTORY_AND_REUSE_PASS_V02321",
    "resolve_product_history_descendant",
    "verify_product_v02321_history",
)
