"""Verify immutable Phase 5B manifest bindings without scanning new code."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import stat
from typing import Sequence

from ecomsre.phase5b.contracts import FrozenEvaluationManifest
from ecomsre.phase5b.protocol import load_strict_json


HISTORICAL_BASE_MAIN_COMMIT = "30c202adb74d5f2e9224098e4f51eb19f214f275"
HISTORICAL_EVALUATION_VERSION = "phase5b.v1"
HISTORICAL_MANIFEST_SHA256 = (
    "527c3f1a6d08a16c8dc56927fc297ecf9d2769c92be5ed29e752277403ad181e"
)


def _sha256_regular_file(project_root: Path, relative: str) -> str:
    path = project_root.joinpath(*relative.split("/"))
    try:
        details = path.lstat()
    except FileNotFoundError as error:
        raise ValueError(f"historical frozen path is missing: {relative}") from error
    if stat.S_ISLNK(details.st_mode) or not stat.S_ISREG(details.st_mode):
        raise ValueError(
            f"historical frozen path must be a regular non-symlink file: {relative}"
        )
    resolved = path.resolve(strict=True)
    if not resolved.is_relative_to(project_root):
        raise ValueError(f"historical frozen path escapes the project root: {relative}")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_historical_bindings(
    project_root: Path,
    manifest_path: Path,
) -> FrozenEvaluationManifest:
    """Verify only the paths bound by the immutable historical manifest."""

    root = project_root.resolve(strict=True)
    manifest_bytes = manifest_path.read_bytes()
    if hashlib.sha256(manifest_bytes).hexdigest() != HISTORICAL_MANIFEST_SHA256:
        raise ValueError("historical freeze manifest bytes changed")
    manifest = load_strict_json(manifest_path, FrozenEvaluationManifest)
    return _verify_declared_historical_bindings(root, manifest)


def _verify_declared_historical_bindings(
    project_root: Path,
    manifest: FrozenEvaluationManifest,
) -> FrozenEvaluationManifest:
    """Validate one parsed manifest for focused regression tests."""

    if manifest.base_main_commit != HISTORICAL_BASE_MAIN_COMMIT:
        raise ValueError("historical freeze manifest base main commit changed")
    if manifest.evaluation_version != HISTORICAL_EVALUATION_VERSION:
        raise ValueError("historical freeze manifest evaluation identity changed")
    for relative, expected_sha256 in manifest.frozen_files.items():
        observed_sha256 = _sha256_regular_file(project_root, relative)
        if observed_sha256 != expected_sha256:
            raise ValueError(f"historical frozen path drift detected: {relative}")
    return manifest


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Verify immutable Phase 5B historical file bindings.",
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
    project_root = args.project_root.resolve(strict=True)
    manifest_path = args.manifest or (
        project_root / "config/phase5b/freeze-manifest.v1.json"
    )
    manifest = verify_historical_bindings(project_root, manifest_path)
    print(
        json.dumps(
            {
                "evaluation_version": manifest.evaluation_version,
                "frozen_file_count": len(manifest.frozen_files),
                "status": "PHASE5B_HISTORICAL_BINDINGS_VERIFIED",
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
