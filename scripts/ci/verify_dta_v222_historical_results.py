"""Verify the merged DTA v2.2 and v2.2.1 result bytes remain immutable."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import cast


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MANIFEST = ROOT / "config/dta-v22-2/historical-results.v1.json"
_EXPECTED_PATHS = (
    "docs/results/dta-v22-practical-evaluation.json",
    "docs/results/dta-v22-practical-evaluation.md",
    "docs/results/dta-v22-practical-error-analysis.md",
    "docs/results/dta-v22-1-evidence-acquisition-study.json",
    "docs/results/dta-v22-1-evidence-acquisition-study.md",
    "docs/results/dta-v22-1-evidence-acquisition-error-analysis.md",
)


def verify_historical_results_v222(
    *, repository_root: Path = ROOT, manifest_path: Path = DEFAULT_MANIFEST
) -> int:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != "dta-v22.2.historical-results.v1":
        raise ValueError("DTA v2.2.2 historical manifest schema differs")
    if manifest.get("base_commit") != "b1418ff202831d809f85a2902e28b169a38e73d2":
        raise ValueError("DTA v2.2.2 historical base commit differs")
    files = cast(list[dict[str, str]], manifest.get("files"))
    if tuple(item["path"] for item in files) != _EXPECTED_PATHS:
        raise ValueError("DTA v2.2.2 historical result scope differs")
    for item in files:
        actual = hashlib.sha256((repository_root / item["path"]).read_bytes()).hexdigest()
        if actual != item["sha256"]:
            raise ValueError(f"historical DTA v2.2 result drift: {item['path']}")
    return len(files)


def main() -> int:
    count = verify_historical_results_v222()
    print(
        json.dumps(
            {
                "status": "DTA_V22_2_HISTORICAL_RESULT_BYTES_PRESERVED",
                "files_verified": count,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
