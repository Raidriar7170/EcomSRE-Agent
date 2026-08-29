#!/usr/bin/env python3
"""Verify the immutable merged Product v0.2.2.2 handoff for v0.2.3."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import stat
import subprocess
from typing import Any, Sequence

from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22
from ecomsre.product.connectors.opensearch_profile_binding_v023 import (
    ACTIVE_PROFILE_SHA256_V023,
    BASELINE_HANDOFF_SHA256_V023,
    CANDIDATE_SET_SHA256_V023,
    CAPTURE_BUNDLE_SHA256_V023,
    OPERATOR_DECISION_SHA256_V023,
)
from ecomsre.product.connectors.opensearch_profile_v0222 import (
    OpenSearchNormalizationProfileV0222,
    OpenSearchProfileStatusV0222,
)


STARTING_MAIN_V023 = "613f6203e4a174b4549b912cb16ca7998cf6238c"
V0222_TERMINAL_V023 = (
    "ECOMSRE_PRODUCT_V0222_CAPTURE_FIRST_OPERATOR_PROFILE_COMPLETE"
)
V0222_HANDOFF_TERMINAL_V023 = "ECOMSRE_PRODUCT_V0222_BASELINE_HANDOFF_READY"
HISTORY_VERIFIED_V023 = "ECOMSRE_PRODUCT_V023_HISTORY_VERIFIED"
_EXPECTED_ROLE_PATHS = {
    "V0222_PREDECESSOR_HISTORY": "config/product-v0222/historical-results.v1.json",
    "V0222_ACTIVE_PROFILE": (
        "config/product-v0222/opensearch/normalization-profile.json"
    ),
    "V0222_CANDIDATE_SET": "config/product-v0222/opensearch/candidate-set.json",
    "V0222_OPERATOR_DECISION": (
        "config/product-v0222/opensearch/operator-decision.json"
    ),
    "V0222_CAPTURE_SUMMARY": "docs/analysis/product-v0222-capture-summary.json",
    "V0222_OFFLINE_PROFILE": "docs/analysis/product-v0222-offline-profile.json",
    "V0222_HOLDOUT_VERIFICATION": (
        "docs/analysis/product-v0222-holdout-verification.json"
    ),
    "V0222_CONNECTOR_SMOKE": "docs/analysis/product-v0222-connector-smoke.json",
    "V0222_ACTIVE_PROFILE_RESTART": (
        "docs/analysis/product-v0222-active-profile-restart-proof.json"
    ),
    "V0222_SERVICE_IDENTITY": (
        "docs/analysis/product-v0222-service-identity-binding.json"
    ),
    "V0222_BASELINE_HANDOFF": "docs/analysis/product-v0222-baseline-handoff.json",
    "V0222_PROGRESS": "docs/analysis/product-v0222-progress.json",
}


def _load_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Product v0.2.3 historical payload must be an object")
    return payload


def _regular_bytes(root: Path, relative: str) -> bytes:
    candidate = Path(relative)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise ValueError("Product v0.2.3 historical path is not repository-relative")
    resolved = root / candidate
    metadata = resolved.lstat()
    if not stat.S_ISREG(metadata.st_mode):
        raise ValueError(f"Product v0.2.3 historical path is not regular: {relative}")
    return resolved.read_bytes()


def _git_bytes(root: Path, revision: str, relative: str) -> bytes:
    return subprocess.run(
        ("git", "show", f"{revision}:{relative}"),
        cwd=root,
        check=True,
        capture_output=True,
    ).stdout


def _verify_manifest_identity(manifest: dict[str, Any]) -> dict[str, Any]:
    if (
        manifest.get("schema_version")
        != "ecomsre.product-v023.historical-results.v1"
        or manifest.get("goal_version")
        != "ecomsre-product-v023-fresh-baseline-nofault-v1"
        or manifest.get("starting_main") != STARTING_MAIN_V023
    ):
        raise ValueError("Product v0.2.3 historical manifest identity differs")
    predecessor = manifest.get("v0222")
    if not isinstance(predecessor, dict):
        raise ValueError("Product v0.2.3 predecessor binding is malformed")
    expected = {
        "pr": 79,
        "terminal": V0222_TERMINAL_V023,
        "handoff_terminal": V0222_HANDOFF_TERMINAL_V023,
        "active_profile_sha256": ACTIVE_PROFILE_SHA256_V023,
        "selected_candidate_alias": "P01",
        "candidate_set_sha256": CANDIDATE_SET_SHA256_V023,
        "operator_decision_sha256": OPERATOR_DECISION_SHA256_V023,
        "capture_bundle_sha256": CAPTURE_BUNDLE_SHA256_V023,
        "offline_parser_sha256": (
            "a62b00a0fa4d8abbb96a7bdbbeb6749a3e6bab41550027b0cdc11856a550484f"
        ),
        "holdout_verification_sha256": (
            "a30fbac9277267093fd9cfaaae33b87164d28711ec3b655025db38dbc083cd25"
        ),
        "connector_smoke_sha256": (
            "a3573782f56f5445db8920301e267840d9a1296e51027c0fda841bfd4bd303c2"
        ),
        "active_profile_restart_proof_sha256": (
            "b2b0ea37d763316a7be9acb65899f2a7d1fddcdb7f222ca05f63bab742fda69b"
        ),
        "service_identity_sha256": (
            "a84e3f82180cd40024f9449096586b0ac94ceea8b492f59d58c58c28d7828d72"
        ),
        "handoff_sha256": BASELINE_HANDOFF_SHA256_V023,
        "fault_attempt_count": 0,
        "baseline_readiness_attempt_count": 0,
        "product_diagnosis_attempt_count": 0,
        "knowledge_loop_campaign_count": 0,
        "agent_writes": 0,
        "runbook_executions": 0,
        "action_authority": "NONE",
    }
    if predecessor != expected:
        raise ValueError("Product v0.2.2.2 predecessor identity differs")
    return predecessor


def _verify_bound_files(root: Path, manifest: dict[str, Any]) -> int:
    files = manifest.get("files")
    if not isinstance(files, list) or len(files) != len(_EXPECTED_ROLE_PATHS):
        raise ValueError("Product v0.2.3 historical file set differs")
    roles: set[str] = set()
    paths: set[str] = set()
    for item in files:
        if not isinstance(item, dict):
            raise ValueError("Product v0.2.3 historical binding is malformed")
        relative = item.get("path")
        revision = item.get("revision")
        role = item.get("role")
        digest = item.get("sha256")
        size = item.get("size_bytes")
        if (
            not isinstance(relative, str)
            or revision != STARTING_MAIN_V023
            or not isinstance(role, str)
            or not isinstance(digest, str)
            or not isinstance(size, int)
            or role in roles
            or relative in paths
        ):
            raise ValueError("Product v0.2.3 historical binding is malformed")
        if _EXPECTED_ROLE_PATHS.get(role) != relative:
            raise ValueError("Product v0.2.3 historical role path differs")
        current = _regular_bytes(root, relative)
        if len(current) != size or hashlib.sha256(current).hexdigest() != digest:
            raise ValueError(f"Product v0.2.2.2 historical byte drift: {relative}")
        if _git_bytes(root, STARTING_MAIN_V023, relative) != current:
            raise ValueError(f"Product v0.2.2.2 starting-main drift: {relative}")
        roles.add(role)
        paths.add(relative)
    if roles != set(_EXPECTED_ROLE_PATHS):
        raise ValueError("Product v0.2.3 historical roles differ")
    return len(files)


def _verify_handoff_semantics(root: Path, predecessor: dict[str, Any]) -> None:
    active = OpenSearchNormalizationProfileV0222.model_validate_json(
        (
            root / "config/product-v0222/opensearch/normalization-profile.json"
        ).read_text(encoding="utf-8")
    )
    handoff = _load_object(root / "docs/analysis/product-v0222-baseline-handoff.json")
    progress = _load_object(root / "docs/analysis/product-v0222-progress.json")
    handoff_body = {key: value for key, value in handoff.items() if key != "handoff_sha256"}
    if (
        active.profile_status is not OpenSearchProfileStatusV0222.ACTIVE
        or active.profile_sha256 != predecessor["active_profile_sha256"]
        or active.selected_candidate_alias != predecessor["selected_candidate_alias"]
        or active.candidate_set_sha256 != predecessor["candidate_set_sha256"]
        or active.operator_decision_sha256 != predecessor["operator_decision_sha256"]
        or active.capture_bundle_sha256 != predecessor["capture_bundle_sha256"]
        or handoff.get("status") != predecessor["handoff_terminal"]
        or handoff.get("active_normalization_profile_sha256")
        != active.profile_sha256
        or handoff.get("handoff_sha256") != predecessor["handoff_sha256"]
        or semantic_sha256_v22(handoff_body) != predecessor["handoff_sha256"]
        or progress.get("increment") != 5
        or progress.get("normalization_profile_status") != "ACTIVE"
        or progress.get("normalization_profile_sha256") != active.profile_sha256
        or progress.get("selected_candidate_alias") != "P01"
    ):
        raise ValueError("Product v0.2.2.2 merged handoff semantics differ")


def verify_product_v023_history(
    project_root: Path,
    manifest_path: Path | None = None,
) -> dict[str, object]:
    root = Path(project_root).resolve(strict=True)
    manifest = _load_object(
        manifest_path or root / "config/product-v023/historical-results.v1.json"
    )
    predecessor = _verify_manifest_identity(manifest)
    bound_file_count = _verify_bound_files(root, manifest)
    _verify_handoff_semantics(root, predecessor)
    subprocess.run(
        ("git", "merge-base", "--is-ancestor", STARTING_MAIN_V023, "HEAD"),
        cwd=root,
        check=True,
        capture_output=True,
    )
    return {
        "status": HISTORY_VERIFIED_V023,
        "starting_main": STARTING_MAIN_V023,
        "v0222_terminal": V0222_TERMINAL_V023,
        "handoff_terminal": V0222_HANDOFF_TERMINAL_V023,
        "active_profile_sha256": ACTIVE_PROFILE_SHA256_V023,
        "handoff_sha256": BASELINE_HANDOFF_SHA256_V023,
        "bound_file_count": bound_file_count,
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
            verify_product_v023_history(arguments.project_root, arguments.manifest),
            separators=(",", ":"),
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = (
    "HISTORY_VERIFIED_V023",
    "STARTING_MAIN_V023",
    "verify_product_v023_history",
)
