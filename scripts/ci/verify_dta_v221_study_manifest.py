"""Provider-free verification for the frozen DTA v2.2.1 study contract."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
from typing import cast

from ecomsre.dta_v2.v22.evidence_acquisition_manifest_v221 import (
    load_and_verify_study_manifest_v221,
)
from scripts.ci.verify_dta_v221_historical_practical_results import (
    verify_historical_practical_results_v221,
)


ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "config/dta-v22-1/evidence-acquisition-study-manifest.json"
PROGRESS = ROOT / "docs/analysis/dta-v22-1-evidence-acquisition-progress.json"
RESULT = ROOT / "docs/results/dta-v22-1-evidence-acquisition-study.json"


def _git(*args: str, repository_root: Path) -> bytes:
    completed = subprocess.run(
        ["git", *args],
        cwd=repository_root,
        check=False,
        capture_output=True,
    )
    if completed.returncode != 0:
        raise ValueError("DTA v2.2.1 study Git binding differs")
    return completed.stdout


def verify_dta_v221_study_manifest(
    *, repository_root: Path = ROOT
) -> dict[str, object]:
    raw = cast(dict[str, object], json.loads(MANIFEST.read_text(encoding="utf-8")))
    model = cast(str, raw["model"])
    manifest = load_and_verify_study_manifest_v221(
        manifest_path=MANIFEST,
        repository_root=repository_root,
        configured_model=model,
    )
    _git(
        "merge-base",
        "--is-ancestor",
        manifest.base_commit,
        manifest.implementation_commit,
        repository_root=repository_root,
    )
    _git(
        "merge-base",
        "--is-ancestor",
        manifest.implementation_commit,
        "HEAD",
        repository_root=repository_root,
    )
    for binding in (
        manifest.prompt,
        manifest.case_set,
        manifest.truth_set,
        manifest.policy_source,
        manifest.scorer_source,
        manifest.historical_results_manifest,
    ):
        committed = _git(
            "show",
            f"{manifest.implementation_commit}:{binding.path}",
            repository_root=repository_root,
        )
        if hashlib.sha256(committed).hexdigest() != binding.sha256:
            raise ValueError(f"implementation commit binding drift: {binding.path}")
    if verify_historical_practical_results_v221(repository_root=repository_root) != 7:
        raise ValueError("historical Practical result scope differs")
    progress = cast(
        dict[str, object], json.loads(PROGRESS.read_text(encoding="utf-8"))
    )
    development = cast(dict[str, object], progress["development"])
    final_study = cast(dict[str, object], progress["final_study"])
    execution_count = cast(int, final_study["execution_count"])
    if (
        progress.get("implementation_commit") != manifest.implementation_commit
        or development.get("changed_iterations_used") != 1
        or development.get("gate_passed") is not True
        or development.get("arm_runs") != 16
        or development.get("agent_writes") != 0
        or execution_count not in {0, 1}
        or (execution_count == 0 and RESULT.exists())
        or (execution_count == 1 and not RESULT.is_file())
    ):
        raise ValueError("DTA v2.2.1 progress binding differs")
    return {
        "implementation_commit": manifest.implementation_commit,
        "model": manifest.model,
        "expected_arm_policy_runs": manifest.expected_arm_policy_runs,
        "development_iterations": development["changed_iterations_used"],
        "final_study_executions": execution_count,
        "historical_files_verified": 7,
    }


def main() -> int:
    result = verify_dta_v221_study_manifest()
    print(
        json.dumps(
            {"status": "DTA_V22_1_STUDY_MANIFEST_VERIFIED", **result},
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
