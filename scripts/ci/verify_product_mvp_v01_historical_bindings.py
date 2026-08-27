#!/usr/bin/env python3
"""Verify Phase 5B history across the Product MVP dependency successor."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
from typing import Sequence

from ecomsre.phase5b.contracts import FrozenEvaluationManifest
from ecomsre.phase5b.protocol import load_strict_json
from scripts.ci.verify_phase5b_historical_bindings import (
    HISTORICAL_BASE_MAIN_COMMIT,
    HISTORICAL_EVALUATION_VERSION,
    HISTORICAL_MANIFEST_SHA256,
    _sha256_regular_file,
)


PRODUCT_SUCCESSOR_OVERRIDES = frozenset({"pyproject.toml", "uv.lock"})


def _historical_blob_sha256(project_root: Path, relative: str) -> str:
    completed = subprocess.run(
        ("git", "show", f"{HISTORICAL_BASE_MAIN_COMMIT}:{relative}"),
        cwd=project_root,
        check=True,
        capture_output=True,
    )
    return hashlib.sha256(completed.stdout).hexdigest()


def verify_product_mvp_historical_bindings(
    project_root: Path,
    manifest_path: Path | None = None,
) -> FrozenEvaluationManifest:
    """Keep 185 live bindings exact and verify old dependency blobs in Git."""

    root = project_root.resolve(strict=True)
    resolved_manifest = manifest_path or (
        root / "config/phase5b/freeze-manifest.v1.json"
    )
    manifest_bytes = resolved_manifest.read_bytes()
    if hashlib.sha256(manifest_bytes).hexdigest() != HISTORICAL_MANIFEST_SHA256:
        raise ValueError("historical freeze manifest bytes changed")
    manifest = load_strict_json(resolved_manifest, FrozenEvaluationManifest)
    if manifest.base_main_commit != HISTORICAL_BASE_MAIN_COMMIT:
        raise ValueError("historical freeze manifest base main commit changed")
    if manifest.evaluation_version != HISTORICAL_EVALUATION_VERSION:
        raise ValueError("historical freeze manifest evaluation identity changed")
    if not PRODUCT_SUCCESSOR_OVERRIDES.issubset(manifest.frozen_files):
        raise ValueError("Product successor override is not historically bound")
    for relative, expected_sha256 in manifest.frozen_files.items():
        observed_sha256 = (
            _historical_blob_sha256(root, relative)
            if relative in PRODUCT_SUCCESSOR_OVERRIDES
            else _sha256_regular_file(root, relative)
        )
        if observed_sha256 != expected_sha256:
            raise ValueError(f"historical frozen path drift detected: {relative}")
    return manifest


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Verify Product MVP successor Phase 5B historical bindings.",
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
    manifest = verify_product_mvp_historical_bindings(
        args.project_root,
        args.manifest,
    )
    print(
        json.dumps(
            {
                "evaluation_version": manifest.evaluation_version,
                "frozen_file_count": len(manifest.frozen_files),
                "successor_overrides": sorted(PRODUCT_SUCCESSOR_OVERRIDES),
                "status": "PHASE5B_PRODUCT_SUCCESSOR_HISTORICAL_BINDINGS_VERIFIED",
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = (
    "PRODUCT_SUCCESSOR_OVERRIDES",
    "main",
    "verify_product_mvp_historical_bindings",
)
