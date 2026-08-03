"""Offline CLI and Make wiring checks for the Phase 2 comparison."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _environment() -> dict[str, str]:
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(PROJECT_ROOT / "src")
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    for name in (
        "ECOMSRE_LLM_BASE_URL",
        "ECOMSRE_LLM_API_KEY",
        "ECOMSRE_LLM_MODEL",
    ):
        environment.pop(name, None)
    return environment


def test_cli_writes_and_freshly_verifies_deterministic_report(
    tmp_path: Path,
) -> None:
    report_path = tmp_path / "comparison-report.json"
    base = [sys.executable, "-m", "ecomsre.phase2.cli"]

    generated = subprocess.run(
        [*base, "compare", "--output", str(report_path)],
        cwd=PROJECT_ROOT,
        env=_environment(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        text=True,
        timeout=120,
    )
    assert generated.returncode == 0, generated.stderr
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["status"] == "COMPLETED"
    assert len(report["variant_results"]) == 3

    verified = subprocess.run(
        [*base, "verify", "--report", str(report_path)],
        cwd=PROJECT_ROOT,
        env=_environment(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        text=True,
        timeout=120,
    )
    assert verified.returncode == 0, verified.stderr
    assert json.loads(verified.stdout)["status"] == "VERIFIED"


def test_make_targets_are_offline_phase2_cli_wiring(tmp_path: Path) -> None:
    report_path = tmp_path / "comparison-report.json"
    rendered: dict[str, str] = {}
    for target in ("phase2-compare", "phase2-verify", "phase2-test"):
        completed = subprocess.run(
            [
                "make",
                "-n",
                target,
                f"PHASE2_REPORT={report_path}",
            ],
            cwd=PROJECT_ROOT,
            env=_environment(),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            text=True,
            timeout=30,
        )
        assert completed.returncode == 0, completed.stderr
        rendered[target] = completed.stdout

    assert "ecomsre.phase2.cli compare" in rendered["phase2-compare"]
    assert "ecomsre.phase2.cli verify" in rendered["phase2-verify"]
    assert "pytest tests/phase2" in rendered["phase2-test"]
    assert all(
        token not in "\n".join(rendered.values()).casefold()
        for token in ("curl ", "wget ", "provider-smoke", "docker ")
    )
