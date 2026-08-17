"""Verify the immutable DTA v2 portfolio before accepting v2.1 evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import stat
from typing import Any, Sequence


HISTORICAL_BASE_COMMIT = "925d23994888d1b83e57fc1bbdd1944e57a1bfff"
HISTORICAL_AGENT_IDENTITY_SHA256 = (
    "6efc26c6e5fab6190be9e63c0bec318c6e94fa29196e6693eb63b2845c6ad0a4"
)
HISTORICAL_HELD_OUT_SEAL_SHA256 = (
    "0f944e79f0958f285006c3bdc3cf8f82b8a71731d8d96d02b474f254a54e247a"
)
HISTORICAL_TERMINAL = "DTA_V2_LIVE_DEMO_ACCEPTANCE_PASS"
HISTORICAL_EVALUATION_RESULT = "COMPLETED_HELD_OUT_NEGATIVE"
HISTORICAL_MANIFEST_SHA256 = (
    "dc715cfad0738d33c4662543f539b179e61812bc85a6b5d6e137f2bafb1b0695"
)
REQUIRED_HISTORICAL_PATHS = (
    "config/dta-v2/agent-identity.v1.json",
    "config/dta-v2/live-demo.v1.json",
    "config/dta-v2/live-demo.v2.json",
    "config/dta-v2/evaluation/manifest.json",
    "docs/analysis/dta-v2-master-progress.json",
    "docs/design/diagnosis-to-action-v2.md",
    "docs/results/dta-v2-evaluation.json",
    "docs/results/dta-v2-evaluation.md",
    "docs/results/dta-v2-live-demo.json",
    "docs/results/dta-v2-live-demo.md",
    "docs/results/dta-v2-live-demo-human-brief.md",
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
        raise ValueError(f"historical DTA v2 path is missing: {relative}") from error
    if stat.S_ISLNK(details.st_mode) or not stat.S_ISREG(details.st_mode):
        raise ValueError(
            f"historical DTA v2 path must be a regular non-symlink file: {relative}"
        )
    resolved = candidate.resolve(strict=True)
    if not resolved.is_relative_to(project_root):
        raise ValueError(f"historical DTA v2 path escapes project root: {relative}")
    return candidate


def _canonical_json_sha256(path: Path) -> str:
    payload = _load_strict_json(path)
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def load_binding_manifest(manifest_path: Path) -> dict[str, Any]:
    """Load and minimally validate the versioned binding manifest."""

    manifest = _load_strict_json(manifest_path)
    if manifest.get("schema_version") != "dta-v21.historical-v2-bindings.v1":
        raise ValueError("unexpected DTA v2 historical binding schema")
    files = manifest.get("files")
    if not isinstance(files, list) or not all(isinstance(item, dict) for item in files):
        raise ValueError("historical binding files must be an object list")
    observed_paths = tuple(item.get("path") for item in files)
    if observed_paths != REQUIRED_HISTORICAL_PATHS:
        raise ValueError("historical DTA v2 binding path set or order changed")
    return manifest


def verify_historical_bindings(
    project_root: Path,
    manifest_path: Path,
) -> dict[str, int | str]:
    """Fail closed on any bound byte, semantic, identity, seal, or claim drift."""

    root = project_root.resolve(strict=True)
    manifest_file = _regular_file(
        root,
        manifest_path.resolve(strict=True).relative_to(root).as_posix(),
    )
    manifest_bytes = manifest_file.read_bytes()
    if hashlib.sha256(manifest_bytes).hexdigest() != HISTORICAL_MANIFEST_SHA256:
        raise ValueError("historical DTA v2 binding manifest bytes changed")
    manifest = load_binding_manifest(manifest_file)
    expected_scalars = {
        "base_commit": HISTORICAL_BASE_COMMIT,
        "expected_historical_terminal": HISTORICAL_TERMINAL,
        "expected_evaluation_result": HISTORICAL_EVALUATION_RESULT,
        "expected_agent_identity_sha256": HISTORICAL_AGENT_IDENTITY_SHA256,
        "expected_held_out_seal_sha256": HISTORICAL_HELD_OUT_SEAL_SHA256,
    }
    for field, expected in expected_scalars.items():
        if manifest.get(field) != expected:
            raise ValueError(f"historical DTA v2 manifest field changed: {field}")

    for item in manifest["files"]:
        relative = item["path"]
        bound_path = _regular_file(root, relative)
        observed_raw = hashlib.sha256(bound_path.read_bytes()).hexdigest()
        if observed_raw != item.get("raw_sha256"):
            raise ValueError(f"historical DTA v2 path drift detected: {relative}")
        expected_semantic = item.get("semantic_sha256")
        if expected_semantic is not None:
            observed_semantic = _canonical_json_sha256(bound_path)
            if observed_semantic != expected_semantic:
                raise ValueError(
                    f"historical DTA v2 semantic drift detected: {relative}"
                )

    identity = _load_strict_json(root / REQUIRED_HISTORICAL_PATHS[0])
    progress = _load_strict_json(root / "docs/analysis/dta-v2-master-progress.json")
    evaluation = _load_strict_json(root / "docs/results/dta-v2-evaluation.json")
    live = _load_strict_json(root / "docs/results/dta-v2-live-demo.json")
    if identity.get("identity_sha256") != HISTORICAL_AGENT_IDENTITY_SHA256:
        raise ValueError("historical DTA v2 Agent identity changed")
    if progress.get("held_out_seal_sha256") != HISTORICAL_HELD_OUT_SEAL_SHA256:
        raise ValueError("historical DTA v2 held-out seal changed")
    if progress.get("live_demo_terminal") != HISTORICAL_TERMINAL:
        raise ValueError("historical DTA v2 progress terminal changed")
    if evaluation.get("result") != HISTORICAL_EVALUATION_RESULT:
        raise ValueError("historical DTA v2 evaluation result changed")
    if live.get("terminal") != HISTORICAL_TERMINAL:
        raise ValueError("historical DTA v2 live terminal changed")

    return {
        "base_commit": HISTORICAL_BASE_COMMIT,
        "file_count": len(REQUIRED_HISTORICAL_PATHS),
        "status": "DTA_V2_HISTORICAL_BINDINGS_VERIFIED",
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Verify immutable DTA v2 historical portfolio bindings.",
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
    manifest_path = args.manifest or (
        root / "config/dta-v21/historical-v2-bindings.v1.json"
    )
    print(
        json.dumps(
            verify_historical_bindings(root, manifest_path),
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
