"""Verify every merged DTA v2.2 through v2.2.3 result byte."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
from typing import cast


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MANIFEST = ROOT / "config/dta-v22-5/historical-results.v1.json"
BASE_COMMIT_V225 = "9c601bd5d802fbe31990348c228e094985044a0b"
INVALID_PREDECESSOR_HEAD_V225 = "98019c7be879b156672210679484bb7fa71a82b7"


def verify_historical_results_v225(
    *, repository_root: Path = ROOT, manifest_path: Path = DEFAULT_MANIFEST
) -> int:
    manifest = json.loads(manifest_path.read_bytes())
    if manifest.get("schema_version") != "dta-v22.5.historical-results.v1":
        raise ValueError("DTA v2.2.5 historical manifest schema differs")
    if manifest.get("base_commit") != BASE_COMMIT_V225:
        raise ValueError("DTA v2.2.5 historical base commit differs")
    if manifest.get("invalid_predecessor") != {
        "pr": 65,
        "head": INVALID_PREDECESSOR_HEAD_V225,
        "status": "INVALID_UNMERGED",
        "reason": "TRUTH_ISOLATION_AND_PROTOCOL_REVIEW_FAILURE",
        "frozen_evaluation_sha256": (
            "ee695c2d2791eb58130b7ba7b9ca400485b7e8773f953a3878faef98281be343"
        ),
    }:
        raise ValueError("DTA v2.2.5 INVALID predecessor metadata differs")
    merged = subprocess.run(
        ["git", "merge-base", "--is-ancestor", INVALID_PREDECESSOR_HEAD_V225, "HEAD"],
        cwd=repository_root,
        check=False,
        capture_output=True,
    )
    if merged.returncode == 0:
        raise ValueError("DTA v2.2.4 INVALID predecessor became an ancestor")
    files = cast(list[dict[str, str]], manifest.get("files"))
    paths = tuple(item["path"] for item in files)
    if len(paths) != 29 or paths != tuple(sorted(set(paths))):
        raise ValueError("DTA v2.2.5 historical result scope differs")
    required = {
        "docs/results/dta-v22-practical-evaluation.json",
        "docs/results/dta-v22-1-evidence-acquisition-study.json",
        "docs/results/dta-v22-2-gap-routing-evaluation.json",
        "docs/results/dta-v22-3-admission-dispatch-evaluation.json",
        "docs/results/dta-v22-3-admission-dispatch-error-analysis.md",
        "docs/results/dta-v22-3-admission-dispatch-interview-brief.md",
    }
    if not required.issubset(paths):
        raise ValueError("DTA v2.2.5 required historical results are absent")
    for item in files:
        relative = Path(item["path"])
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("DTA v2.2.5 historical path escapes the repository")
        target = repository_root / relative
        actual = hashlib.sha256(target.read_bytes()).hexdigest() if target.is_file() else None
        if actual != item["sha256"]:
            raise ValueError(f"historical DTA v2.2.5 result drift: {relative}")
    return len(files)


def main() -> int:
    count = verify_historical_results_v225()
    print(
        json.dumps(
            {
                "status": "DTA_V22_5_HISTORICAL_RESULT_BYTES_PRESERVED",
                "files_verified": count,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
