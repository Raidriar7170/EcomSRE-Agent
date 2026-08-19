"""Verify immutable DTA v2 and v2.1 evidence before accepting v2.2 work."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import stat
import subprocess
from typing import Any, Sequence

from scripts.ci.verify_dta_v2_historical_bindings import (
    verify_historical_bindings as verify_v2_historical_bindings,
)


HISTORICAL_BASE_COMMIT = "9da92d54a4fb470c5452cee36a731e81529d05a5"
V21_CAPABILITY_COMMIT = "4442dda6cf7d54e163b34355dad2e8235d3957c1"
V21_CAPABILITY_TREE = "b6b5e5df5ba0cdd45bc97d1990bbe1abe83c2675"
V21_ADMINISTRATIVE_COMMIT = HISTORICAL_BASE_COMMIT
V21_ADMINISTRATIVE_TREE = "65877cf9061bab3e30c6f127fdbe1da59b3b95a6"
V21_HELD_OUT_EXECUTION_ID = "53615cdd78b348b68496f64102c0b4de"
V21_HELD_OUT_SEAL_SHA256 = (
    "9a7c8e56400e99c693c8bddc26007b1dd26e0dcee2167b07cf3fba00fd22fbd7"
)
V21_PLANNER_IDENTITY_SHA256 = (
    "80506a41847d705f048f521b06d63035b4a5b47526eddc501c794b370528300d"
)
V21_HELD_OUT_CLAIM = "DTA_V21_NO_PREREGISTERED_PLANNER_ADVANTAGE_SUPPORTED"
V21_CAPABILITY_REPORT_SHA256 = (
    "24d5fda0f10029817afa4146a99f4d1d19e99e7c6902d84c88dd377a74d7c48f"
)
V21_TERMINAL = (
    "DTA_V21_P0_ENGINEERING_CLOSEOUT_WITH_FROZEN_AGENT_CAPABILITY_LIMITATIONS"
)
HISTORICAL_MANIFEST_SHA256 = (
    "882b520a7847dfdb898913aac9c2329166dcc0fc4762eb2172fb7ac1649710c0"
)
REQUIRED_HISTORICAL_PATHS = (
    "config/dta-v21/historical-v2-bindings.v1.json",
    "config/dta-v2/agent-identity.v1.json",
    "docs/analysis/dta-v2-master-progress.json",
    "docs/results/dta-v2-evaluation.json",
    "docs/results/dta-v2-evaluation.md",
    "docs/results/dta-v2-live-demo.json",
    "docs/results/dta-v2-live-demo.md",
    "docs/design/diagnosis-to-action-v2.1-p0.md",
    "docs/analysis/dta-v21-p0-master-progress.json",
    "config/dta-v21/evaluation/manifest.json",
    "config/dta-v21/evaluation/preregistration.v1.json",
    "config/dta-v21/evaluation/public-case-bindings.v1.json",
    "config/dta-v21/agent-identities/evidence-guided-planner.v1.json",
    "docs/results/dta-v21-evaluation.json",
    "docs/results/dta-v21-evaluation.md",
    "docs/results/dta-v21-live-capability-closeout.json",
    "docs/results/dta-v21-live-capability-closeout.md",
    "docs/review-evidence/dta-v21-live/administrative-test-repair.v1.json",
    "src/ecomsre/dta_v2/v21/live_final_cli.py",
)


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _load_strict_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
        )
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise ValueError(f"invalid JSON: {path.name}") from error
    if not isinstance(payload, dict):
        raise ValueError(f"JSON root must be an object: {path.name}")
    return payload


def _regular_file(project_root: Path, relative: str) -> Path:
    candidate = project_root.joinpath(*relative.split("/"))
    try:
        details = candidate.lstat()
    except FileNotFoundError as error:
        raise ValueError(f"historical path is missing: {relative}") from error
    if stat.S_ISLNK(details.st_mode) or not stat.S_ISREG(details.st_mode):
        raise ValueError(f"historical path must be a regular non-symlink file: {relative}")
    resolved = candidate.resolve(strict=True)
    if not resolved.is_relative_to(project_root):
        raise ValueError(f"historical path escapes project root: {relative}")
    return candidate


def _canonical_json_sha256(path: Path) -> str:
    canonical = json.dumps(
        _load_strict_json(path),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def load_binding_manifest(manifest_path: Path) -> dict[str, Any]:
    """Load and validate the closed-world historical binding manifest."""

    manifest = _load_strict_json(manifest_path)
    if manifest.get("schema_version") != "dta-v22.historical-bindings.v1":
        raise ValueError("unexpected DTA v2.2 historical binding schema")
    files = manifest.get("files")
    if not isinstance(files, list) or not all(isinstance(item, dict) for item in files):
        raise ValueError("historical binding files must be an object list")
    observed_paths = tuple(item.get("path") for item in files)
    if observed_paths != REQUIRED_HISTORICAL_PATHS:
        raise ValueError("historical binding path set or order changed")
    for item in files:
        if set(item) != {"path", "raw_sha256", "semantic_sha256"}:
            raise ValueError("historical binding file fields changed")
    return manifest


def verify_declared_files(project_root: Path, manifest: dict[str, Any]) -> None:
    """Verify every declared byte and canonical JSON digest without Git history."""

    root = project_root.resolve(strict=True)
    for item in manifest["files"]:
        relative = item["path"]
        bound_path = _regular_file(root, relative)
        observed_raw = hashlib.sha256(bound_path.read_bytes()).hexdigest()
        if observed_raw != item.get("raw_sha256"):
            raise ValueError(f"historical path drift detected: {relative}")
        expected_semantic = item.get("semantic_sha256")
        if expected_semantic is not None:
            observed_semantic = _canonical_json_sha256(bound_path)
            if observed_semantic != expected_semantic:
                raise ValueError(f"historical semantic drift detected: {relative}")


def _git(project_root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ("git", "-C", str(project_root), *args),
        check=check,
        capture_output=True,
        text=True,
    )


def _verify_git_history(project_root: Path) -> None:
    ancestry = _git(
        project_root,
        "merge-base",
        "--is-ancestor",
        HISTORICAL_BASE_COMMIT,
        "HEAD",
        check=False,
    )
    if ancestry.returncode != 0:
        raise ValueError("DTA v2.2 historical base is not an ancestor of HEAD")
    for commit, expected_tree in (
        (V21_CAPABILITY_COMMIT, V21_CAPABILITY_TREE),
        (V21_ADMINISTRATIVE_COMMIT, V21_ADMINISTRATIVE_TREE),
    ):
        observed_tree = _git(project_root, "rev-parse", f"{commit}^{{tree}}").stdout.strip()
        if observed_tree != expected_tree:
            raise ValueError(f"historical commit tree differs: {commit}")


def verify_historical_bindings(
    project_root: Path,
    manifest_path: Path,
) -> dict[str, int | str]:
    """Fail closed on v2/v2.1 byte, semantic, identity, claim, or history drift."""

    root = project_root.resolve(strict=True)
    relative_manifest = manifest_path.resolve(strict=True).relative_to(root).as_posix()
    manifest_file = _regular_file(root, relative_manifest)
    if hashlib.sha256(manifest_file.read_bytes()).hexdigest() != HISTORICAL_MANIFEST_SHA256:
        raise ValueError("DTA v2.2 historical binding manifest bytes changed")
    manifest = load_binding_manifest(manifest_file)
    expected_scalars = {
        "inspected_starting_main": HISTORICAL_BASE_COMMIT,
        "v21_capability_merge_commit": V21_CAPABILITY_COMMIT,
        "v21_capability_tree_sha1": V21_CAPABILITY_TREE,
        "v21_administrative_merge_commit": V21_ADMINISTRATIVE_COMMIT,
        "v21_administrative_tree_sha1": V21_ADMINISTRATIVE_TREE,
        "expected_v2_terminal": "DTA_V2_LIVE_DEMO_ACCEPTANCE_PASS",
        "expected_v2_evaluation_result": "COMPLETED_HELD_OUT_NEGATIVE",
        "expected_v21_held_out_execution_id": V21_HELD_OUT_EXECUTION_ID,
        "expected_v21_held_out_seal_sha256": V21_HELD_OUT_SEAL_SHA256,
        "expected_v21_planner_identity_sha256": V21_PLANNER_IDENTITY_SHA256,
        "expected_v21_held_out_claim": V21_HELD_OUT_CLAIM,
        "expected_v21_capability_report_sha256": V21_CAPABILITY_REPORT_SHA256,
        "expected_v21_terminal": V21_TERMINAL,
    }
    for field, expected in expected_scalars.items():
        if manifest.get(field) != expected:
            raise ValueError(f"historical manifest field changed: {field}")

    verify_declared_files(root, manifest)
    verify_v2_historical_bindings(
        root,
        root / "config/dta-v21/historical-v2-bindings.v1.json",
    )

    identity = _load_strict_json(
        root / "config/dta-v21/agent-identities/evidence-guided-planner.v1.json"
    )
    evaluation = _load_strict_json(root / "docs/results/dta-v21-evaluation.json")
    closeout = _load_strict_json(
        root / "docs/results/dta-v21-live-capability-closeout.json"
    )
    progress = _load_strict_json(root / "docs/analysis/dta-v21-p0-master-progress.json")
    attestation = _load_strict_json(
        root / "docs/review-evidence/dta-v21-live/administrative-test-repair.v1.json"
    )
    if identity.get("identity_sha256") != V21_PLANNER_IDENTITY_SHA256:
        raise ValueError("historical v2.1 Planner identity changed")
    if evaluation.get("execution_id") != V21_HELD_OUT_EXECUTION_ID:
        raise ValueError("historical v2.1 held-out execution changed")
    if evaluation.get("held_out_pack_seal_sha256") != V21_HELD_OUT_SEAL_SHA256:
        raise ValueError("historical v2.1 held-out seal changed")
    if progress.get("held_out_claim") != V21_HELD_OUT_CLAIM:
        raise ValueError("historical v2.1 held-out claim changed")
    if progress.get("final_engineering_terminal") != V21_TERMINAL:
        raise ValueError("historical v2.1 terminal changed")
    if closeout.get("report_sha256") != V21_CAPABILITY_REPORT_SHA256:
        raise ValueError("historical v2.1 capability report changed")
    if closeout.get("terminal") != V21_TERMINAL:
        raise ValueError("historical v2.1 capability terminal changed")
    if attestation.get("pr_number") != 56 or attestation.get("base_main_head") != (
        V21_CAPABILITY_COMMIT
    ):
        raise ValueError("historical v2.1 administrative successor changed")

    _verify_git_history(root)
    return {
        "base_commit": HISTORICAL_BASE_COMMIT,
        "file_count": len(REQUIRED_HISTORICAL_PATHS),
        "status": "DTA_V22_HISTORICAL_BINDINGS_VERIFIED",
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Verify immutable DTA v2 and v2.1 historical bindings.",
    )
    parser.add_argument(
        "--project-root",
        type=Path,
        default=Path(__file__).resolve().parents[2],
    )
    parser.add_argument("--manifest", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    root = args.project_root.resolve(strict=True)
    manifest = args.manifest or root / "config/dta-v22/historical-bindings.v1.json"
    print(
        json.dumps(
            verify_historical_bindings(root, manifest),
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
