"""Verify every merged DTA v2.2 through v2.2.3 result byte."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import cast


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MANIFEST = ROOT / "config/dta-v22-4/historical-results.v1.json"
BASE_COMMIT_V224 = "9c601bd5d802fbe31990348c228e094985044a0b"


def verify_historical_results_v224(
    *, repository_root: Path = ROOT, manifest_path: Path = DEFAULT_MANIFEST
) -> int:
    manifest = json.loads(manifest_path.read_bytes())
    if manifest.get("schema_version") != "dta-v22.4.historical-results.v1":
        raise ValueError("DTA v2.2.4 historical manifest schema differs")
    if manifest.get("base_commit") != BASE_COMMIT_V224:
        raise ValueError("DTA v2.2.4 historical base commit differs")
    files = cast(list[dict[str, str]], manifest.get("files"))
    paths = tuple(item["path"] for item in files)
    if len(paths) != 29 or paths != tuple(sorted(set(paths))):
        raise ValueError("DTA v2.2.4 historical result scope differs")
    required = {
        "docs/results/dta-v22-practical-evaluation.json",
        "docs/results/dta-v22-1-evidence-acquisition-study.json",
        "docs/results/dta-v22-2-gap-routing-evaluation.json",
        "docs/results/dta-v22-3-admission-dispatch-evaluation.json",
        "docs/results/dta-v22-3-admission-dispatch-error-analysis.md",
        "docs/results/dta-v22-3-admission-dispatch-interview-brief.md",
    }
    if not required.issubset(paths):
        raise ValueError("DTA v2.2.4 required historical results are absent")
    for item in files:
        relative = Path(item["path"])
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("DTA v2.2.4 historical path escapes the repository")
        target = repository_root / relative
        actual = hashlib.sha256(target.read_bytes()).hexdigest() if target.is_file() else None
        if actual != item["sha256"]:
            raise ValueError(f"historical DTA v2.2.4 result drift: {relative}")
    return len(files)


def main() -> int:
    count = verify_historical_results_v224()
    print(
        json.dumps(
            {
                "status": "DTA_V22_4_HISTORICAL_RESULT_BYTES_PRESERVED",
                "files_verified": count,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
