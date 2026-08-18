"""Verify frozen Phase 5B execution bindings across successor Make targets."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import stat
import subprocess
from typing import Sequence

from scripts.phase5b_execution.freeze import (
    EXECUTION_FREEZE_RELATIVE,
    load_execution_freeze_manifest,
    sha256_regular_file,
)
from scripts.phase5b_execution.contracts import ExecutionFreezeManifest


SUCCESSOR_BASE_COMMIT = "925d23994888d1b83e57fc1bbdd1944e57a1bfff"
HISTORICAL_EXECUTION_MANIFEST_SHA256 = (
    "36a96ddf089c1ca49720fa73651ecc50472a19279e846c776a2d88f055e4a615"
)
SUCCESSOR_MAKEFILE_SHA256 = (
    "0d8283ba7a40caa615c488b2079c27daad9a3c50478bd7d604358ac140b9f42b"
)
SUCCESSOR_BLOCK_START = "# BEGIN DTA_V21_SUCCESSOR_TARGETS"
SUCCESSOR_BLOCK_END = "# END DTA_V21_SUCCESSOR_TARGETS"


def _regular_file(path: Path, *, description: str) -> Path:
    try:
        details = path.lstat()
    except FileNotFoundError as error:
        raise ValueError(f"{description} is missing") from error
    if stat.S_ISLNK(details.st_mode) or not stat.S_ISREG(details.st_mode):
        raise ValueError(f"{description} must be a regular non-symlink file")
    return path


def _historical_git_blob(project_root: Path, relative: str) -> bytes:
    ancestor = subprocess.run(
        [
            "git",
            "merge-base",
            "--is-ancestor",
            SUCCESSOR_BASE_COMMIT,
            "HEAD",
        ],
        cwd=project_root,
        check=False,
        capture_output=True,
    )
    if ancestor.returncode != 0:
        raise ValueError("DTA v2 successor base is not an ancestor of HEAD")
    try:
        completed = subprocess.run(
            ["git", "show", f"{SUCCESSOR_BASE_COMMIT}:{relative}"],
            cwd=project_root,
            check=True,
            capture_output=True,
        )
    except subprocess.CalledProcessError as error:
        raise ValueError(f"historical Git path is unavailable: {relative}") from error
    return completed.stdout


def _verify_successor_makefile_bytes(
    historical: bytes,
    current: bytes,
    *,
    expected_historical_sha256: str,
    expected_current_sha256: str,
) -> str:
    observed_historical_sha256 = hashlib.sha256(historical).hexdigest()
    if observed_historical_sha256 != expected_historical_sha256:
        raise ValueError("historical execution harness drift detected: Makefile")
    if hashlib.sha256(current).hexdigest() != expected_current_sha256:
        raise ValueError("successor Makefile bytes changed")
    if not current.startswith(historical):
        raise ValueError("successor Makefile changed the historical byte prefix")
    try:
        successor = current[len(historical) :].decode("utf-8")
    except UnicodeDecodeError as error:
        raise ValueError("successor Makefile block is not UTF-8") from error
    if (
        successor.count(SUCCESSOR_BLOCK_START) != 1
        or successor.count(SUCCESSOR_BLOCK_END) != 1
        or not successor.rstrip().endswith(SUCCESSOR_BLOCK_END)
    ):
        raise ValueError("successor Makefile block markers differ")
    return observed_historical_sha256


def _verify_successor_makefile(project_root: Path, expected_sha256: str) -> str:
    historical = _historical_git_blob(project_root, "Makefile")
    current = _regular_file(
        project_root / "Makefile",
        description="successor Makefile",
    ).read_bytes()
    return _verify_successor_makefile_bytes(
        historical,
        current,
        expected_historical_sha256=expected_sha256,
        expected_current_sha256=SUCCESSOR_MAKEFILE_SHA256,
    )


def verify_historical_execution_bindings(
    project_root: Path,
    manifest_path: Path | None = None,
) -> ExecutionFreezeManifest:
    """Verify the frozen harness while allowing post-freeze Make targets."""

    root = project_root.resolve(strict=True)
    path = manifest_path or root / EXECUTION_FREEZE_RELATIVE
    path = _regular_file(path, description="historical execution freeze manifest")
    if not path.resolve(strict=True).is_relative_to(root):
        raise ValueError("historical execution freeze manifest escapes project root")
    if hashlib.sha256(path.read_bytes()).hexdigest() != (
        HISTORICAL_EXECUTION_MANIFEST_SHA256
    ):
        raise ValueError("historical execution freeze manifest bytes changed")
    manifest = load_execution_freeze_manifest(path)

    for relative, expected_sha256 in manifest.harness_files.items():
        if relative == "Makefile":
            observed_sha256 = _verify_successor_makefile(root, expected_sha256)
        else:
            observed_sha256 = sha256_regular_file(root / relative)
        if observed_sha256 != expected_sha256:
            raise ValueError(f"historical execution harness drift detected: {relative}")
    return manifest


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Verify immutable Phase 5B execution harness bindings.",
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
    manifest = verify_historical_execution_bindings(root, args.manifest)
    print(
        json.dumps(
            {
                "evaluation_version": manifest.evaluation_version,
                "harness_file_count": len(manifest.harness_files),
                "status": "PHASE5B_EXECUTION_HISTORICAL_BINDINGS_VERIFIED",
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
