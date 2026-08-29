#!/usr/bin/env python3
"""Verify the immutable Product MVP v0.1 evidence used by Product v0.2."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import stat
from typing import Any, Sequence


STARTING_MAIN = "8398a063de048064f160a7ffed236fbb3327b701"
V01_TERMINAL = "ECOMSRE_PRODUCT_MVP_V01_LIVE_READONLY_PASS"
REPORT_KIND = "ENGINEERING_ACCEPTANCE"


def _sha256_regular_file(project_root: Path, relative: str) -> str:
    if Path(relative).is_absolute() or ".." in Path(relative).parts:
        raise ValueError("historical Product v0.1 path is not repository-relative")
    path = project_root / relative
    metadata = path.lstat()
    if not stat.S_ISREG(metadata.st_mode):
        raise ValueError(f"historical Product v0.1 path is not a regular file: {relative}")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("historical Product v0.1 manifest must be an object")
    return payload


def verify_product_v02_history(
    project_root: Path,
    manifest_path: Path | None = None,
) -> dict[str, object]:
    root = project_root.resolve(strict=True)
    manifest = _load_json(
        manifest_path or root / "config/product-v02/historical-results.v1.json"
    )
    if manifest.get("schema_version") != "ecomsre.product-v02.historical-results.v1":
        raise ValueError("historical Product v0.1 manifest schema differs")
    if manifest.get("starting_main") != STARTING_MAIN:
        raise ValueError("historical Product v0.1 starting main differs")
    if manifest.get("v01_terminal") != V01_TERMINAL:
        raise ValueError("historical Product v0.1 terminal differs")
    if manifest.get("report_kind") != REPORT_KIND or manifest.get("production_claim") is not False:
        raise ValueError("historical Product v0.1 claim boundary differs")
    files = manifest.get("files")
    if not isinstance(files, dict) or tuple(files) != tuple(sorted(files)) or len(files) != 4:
        raise ValueError("historical Product v0.1 file set differs")
    for relative, expected in files.items():
        if not isinstance(relative, str) or not isinstance(expected, str):
            raise ValueError("historical Product v0.1 binding is malformed")
        if _sha256_regular_file(root, relative) != expected:
            raise ValueError(f"historical Product v0.1 byte drift: {relative}")
    acceptance = _load_json(root / "docs/results/ecomsre-product-mvp-v01-acceptance.json")
    if (
        acceptance.get("current_terminal") != V01_TERMINAL
        or acceptance.get("report_kind") != REPORT_KIND
        or acceptance.get("causal_algorithm_effect_study") is not False
    ):
        raise ValueError("historical Product v0.1 acceptance semantics differ")
    return {
        "status": "ECOMSRE_PRODUCT_V02_HISTORY_VERIFIED",
        "starting_main": STARTING_MAIN,
        "bound_file_count": len(files),
        "v01_terminal": V01_TERMINAL,
        "report_kind": REPORT_KIND,
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
            verify_product_v02_history(arguments.project_root, arguments.manifest),
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ("verify_product_v02_history",)
