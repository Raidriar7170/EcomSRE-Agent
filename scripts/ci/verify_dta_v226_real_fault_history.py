"""Verify the frozen PR #67 public result bytes used by DTA v2.2.6."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import cast


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MANIFEST = ROOT / "config/dta-v226-real-fault/historical-results.v1.json"
BASE_COMMIT = "1c6520d706481f37b63a5b14c1fe8554b52d530b"
REQUIRED_TERMINALS = {
    "preserved_engineering_terminal": "DTA_V225_REAL_FAULT_SHADOW_STUDY_COMPLETE",
    "preserved_transfer_terminal": "DTA_V225_REAL_FAULT_TRANSFER_NOT_SUPPORTED",
    "preserved_comparison_disposition": "CURRENT_RUNTIME_DESCRIPTIVE_ADVANTAGE",
}
REQUIRED_FILES = {
    "docs/external-reviews/dta-v225-real-fault-final-review.md",
    "docs/results/dta-v225-real-fault-shadow-comparison.json",
    "docs/results/dta-v225-real-fault-shadow-comparison.md",
    "docs/results/dta-v225-real-fault-shadow-error-analysis.md",
}


def verify_dta_v226_real_fault_history(
    *, repository_root: Path = ROOT, manifest_path: Path = DEFAULT_MANIFEST
) -> int:
    manifest = json.loads(manifest_path.read_bytes())
    if manifest.get("schema_version") != (
        "dta-v226-real-fault.historical-results.v1"
    ):
        raise ValueError("v2.2.6 predecessor manifest schema differs")
    if manifest.get("base_commit") != BASE_COMMIT:
        raise ValueError("v2.2.6 predecessor base commit differs")
    if manifest.get("predecessor_pr") != 67:
        raise ValueError("v2.2.6 predecessor PR differs")
    for field, expected in REQUIRED_TERMINALS.items():
        if manifest.get(field) != expected:
            raise ValueError(f"v2.2.6 predecessor {field} differs")

    files = cast(list[dict[str, str]], manifest.get("files"))
    paths = tuple(item["path"] for item in files)
    if set(paths) != REQUIRED_FILES or paths != tuple(sorted(REQUIRED_FILES)):
        raise ValueError("v2.2.6 predecessor file scope differs")
    for item in files:
        relative = Path(item["path"])
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("v2.2.6 predecessor path escapes the repository")
        target = repository_root / relative
        actual = hashlib.sha256(target.read_bytes()).hexdigest() if target.is_file() else None
        if actual != item["sha256"]:
            raise ValueError(f"PR #67 public result drift: {relative}")
    return len(files)


def main() -> int:
    count = verify_dta_v226_real_fault_history()
    print(
        json.dumps(
            {
                "status": "DTA_V226_PR67_PUBLIC_BYTES_PRESERVED",
                "files_verified": count,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
