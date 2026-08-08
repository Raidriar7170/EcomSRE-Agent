from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[3]


def test_rcaeval_workflow_has_job_level_pythonpath_and_dev2_scanner() -> None:
    workflow = (PROJECT_ROOT / ".github/workflows/rcaeval-v2-dev.yml").read_text(
        encoding="utf-8"
    )
    verify = workflow.split("  verify:\n", 1)[1]
    assert "    env:\n      PYTHONPATH: src:.\n" in verify
    assert 'config/rcaeval-re2-v2-dev2/**' in workflow
    assert "rcaeval-re2-v2-dev2" in workflow
    assert "|| true" not in workflow
    assert "continue-on-error" not in workflow


def test_scanner_imports_with_only_locked_pythonpath_and_allow_missing(
    tmp_path: Path,
) -> None:
    missing = tmp_path / "not-yet-published.json"
    result = subprocess.run(
        (
            sys.executable,
            "-m",
            "scripts.rcaeval_v2.scan_public_outputs",
            "--allow-missing",
            str(missing),
        ),
        cwd=PROJECT_ROOT,
        env={"PATH": os.environ["PATH"], "PYTHONPATH": "src:."},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_scanner_really_rejects_leakage_when_result_exists(tmp_path: Path) -> None:
    leaked = tmp_path / "result.json"
    leaked.write_text('{"case_id":"forbidden"}\n', encoding="utf-8")
    result = subprocess.run(
        (
            sys.executable,
            "-m",
            "scripts.rcaeval_v2.scan_public_outputs",
            str(leaked),
        ),
        cwd=PROJECT_ROOT,
        env={"PATH": os.environ["PATH"], "PYTHONPATH": "src:."},
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
