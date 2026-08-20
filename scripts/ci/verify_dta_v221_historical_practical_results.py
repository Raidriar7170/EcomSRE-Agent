"""Verify that DTA v2.2.1 preserves every merged Practical result byte."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import cast


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MANIFEST = ROOT / "config/dta-v22-1/historical-practical-results.v1.json"


def verify_historical_practical_results_v221(
    *, repository_root: Path = ROOT, manifest_path: Path = DEFAULT_MANIFEST
) -> int:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != "dta-v22.1.historical-practical-results.v1":
        raise ValueError("DTA v2.2.1 historical manifest schema differs")
    if manifest.get("base_commit") != "fceadc924d4909ca1457b35f268429f0272427ce":
        raise ValueError("DTA v2.2.1 historical base commit differs")
    files = cast(list[dict[str, str]], manifest.get("files"))
    paths = tuple(item["path"] for item in files)
    expected = tuple(
        f"docs/results/dta-v22-practical-{suffix}"
        for suffix in (
            "development.json",
            "development.md",
            "error-analysis.md",
            "evaluation.json",
            "evaluation.md",
            "final-summary.md",
            "interview-brief.md",
        )
    )
    if paths != expected:
        raise ValueError("DTA v2.2 Practical result scope differs")
    for item in files:
        payload = (repository_root / item["path"]).read_bytes()
        if hashlib.sha256(payload).hexdigest() != item["sha256"]:
            raise ValueError(f"historical Practical result drift: {item['path']}")
    return len(files)


def main() -> int:
    count = verify_historical_practical_results_v221()
    print(
        json.dumps(
            {
                "status": "DTA_V22_PRACTICAL_RESULT_BYTES_PRESERVED",
                "files_verified": count,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
