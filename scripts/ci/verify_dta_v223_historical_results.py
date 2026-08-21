"""Verify every merged DTA v2.2, v2.2.1, and v2.2.2 result byte."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import cast


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MANIFEST = ROOT / "config/dta-v22-3/historical-results.v1.json"


def verify_historical_results_v223(
    *, repository_root: Path = ROOT, manifest_path: Path = DEFAULT_MANIFEST
) -> int:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != "dta-v22.3.historical-results.v1":
        raise ValueError("DTA v2.2.3 historical manifest schema differs")
    if manifest.get("base_commit") != "bb85500fd4aa1777e2ac186f04b4b887c3a1023b":
        raise ValueError("DTA v2.2.3 historical base commit differs")
    files = cast(list[dict[str, str]], manifest.get("files"))
    paths = tuple(item["path"] for item in files)
    if len(paths) != 23 or paths != tuple(sorted(set(paths))):
        raise ValueError("DTA v2.2.3 historical result scope differs")
    required = {
        "docs/results/dta-v22-practical-evaluation.json",
        "docs/results/dta-v22-1-evidence-acquisition-study.json",
        "docs/results/dta-v22-2-gap-routing-evaluation.json",
        "docs/results/dta-v22-2-gap-routing-error-analysis.md",
        "docs/results/dta-v22-2-gap-routing-interview-brief.md",
    }
    if not required.issubset(paths):
        raise ValueError("DTA v2.2.3 required historical results are absent")
    for item in files:
        relative = Path(item["path"])
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("DTA v2.2.3 historical path escapes the repository")
        target = repository_root / relative
        actual = hashlib.sha256(target.read_bytes()).hexdigest() if target.is_file() else None
        if actual != item["sha256"]:
            raise ValueError(f"historical DTA v2.2.3 result drift: {relative}")
    return len(files)


def main() -> int:
    count = verify_historical_results_v223()
    print(
        json.dumps(
            {
                "status": "DTA_V22_3_HISTORICAL_RESULT_BYTES_PRESERVED",
                "files_verified": count,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
