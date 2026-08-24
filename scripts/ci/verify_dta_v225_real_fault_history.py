"""Verify the frozen historical inputs to the DTA v2.2.5 real-fault study."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import cast


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MANIFEST = ROOT / "config/dta-v225-real-fault/historical-results.v1.json"
BASE_COMMIT = "8e4227fb8ac8880f89eadc3d13bf423244b378a7"


def verify_dta_v225_real_fault_history(
    *, repository_root: Path = ROOT, manifest_path: Path = DEFAULT_MANIFEST
) -> int:
    manifest = json.loads(manifest_path.read_bytes())
    if manifest.get("schema_version") != (
        "dta-v225-real-fault.historical-results.v1"
    ):
        raise ValueError("real-fault historical manifest schema differs")
    if manifest.get("base_commit") != BASE_COMMIT:
        raise ValueError("real-fault historical base commit differs")
    files = cast(list[dict[str, str]], manifest.get("files"))
    required = {
        "docs/results/dta-v2-live-demo.json",
        "docs/results/dta-v2-evaluation.json",
        "docs/results/dta-v21-live-capability-closeout.json",
        "docs/results/dta-v22-2-gap-routing-evaluation.json",
        "docs/results/dta-v22-5-opaque-ambiguity-evaluation.json",
        "docs/results/dta-v22-5-opaque-ambiguity-error-analysis.md",
    }
    paths = tuple(item["path"] for item in files)
    if set(paths) != required or paths != tuple(sorted(set(paths))):
        raise ValueError("real-fault historical result scope differs")
    for item in files:
        relative = Path(item["path"])
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("real-fault historical path escapes the repository")
        target = repository_root / relative
        actual = hashlib.sha256(target.read_bytes()).hexdigest() if target.is_file() else None
        if actual != item["sha256"]:
            raise ValueError(f"historical real-fault input drift: {relative}")
    return len(files)


def main() -> int:
    count = verify_dta_v225_real_fault_history()
    print(
        json.dumps(
            {
                "status": "DTA_V225_REAL_FAULT_HISTORICAL_BYTES_PRESERVED",
                "files_verified": count,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
