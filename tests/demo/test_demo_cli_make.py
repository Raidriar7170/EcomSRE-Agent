"""Public demo CLI, report-safety, and Make target checks."""

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
    environment["ECOMSRE_LLM_API_KEY"] = "must-not-appear-in-demo"
    environment["ECOMSRE_LLM_BASE_URL"] = "https://provider.invalid/v1"
    environment["ECOMSRE_LLM_MODEL"] = "must-not-be-used"
    return environment


def test_cli_writes_a_safe_deterministic_report_and_human_summary(
    tmp_path: Path,
) -> None:
    report_path = tmp_path / "agent-mainline-v1-report.json"
    command = [
        sys.executable,
        "-m",
        "ecomsre.demo",
        "run",
        "--output",
        str(report_path),
    ]

    first = subprocess.run(
        command,
        cwd=PROJECT_ROOT,
        env=_environment(),
        capture_output=True,
        check=False,
        text=True,
        timeout=60,
    )
    assert first.returncode == 0, first.stderr
    first_bytes = report_path.read_bytes()
    repeated = subprocess.run(
        command,
        cwd=PROJECT_ROOT,
        env=_environment(),
        capture_output=True,
        check=False,
        text=True,
        timeout=60,
    )
    assert repeated.returncode == 0, repeated.stderr
    assert report_path.read_bytes() == first_bytes

    report = json.loads(first_bytes)
    assert report["schema_version"] == "ecomsre.agent-mainline-demo-report.v1"
    assert report["remediation"]["terminal_status"] == "REMEDIATION_VERIFIED"
    assert report["execution_boundary"] == {
        "provider_called": False,
        "docker_called": False,
        "live_execution": False,
        "evaluator_truth_read": False,
        "phase4_entered": False,
        "phase5_entered": False,
    }
    lowered = first_bytes.lower()
    assert b"must-not-appear-in-demo" not in lowered
    assert b"authorization" not in lowered
    assert b"bearer " not in lowered
    assert str(PROJECT_ROOT).encode() not in first_bytes
    assert b"ground-truth" not in lowered
    assert b"raw provider" not in lowered
    for label in (
        "Case:",
        "Diagnosis variant:",
        "Decision:",
        "Root service:",
        "Fault mechanism:",
        "Supporting evidence count:",
        "Selected remediation action:",
        "Policy decision:",
        "Approval mode:",
        "Forward mutation count:",
        "Verification result:",
        "Rollback count:",
        "Terminal status:",
        "Model/tool/token usage:",
    ):
        assert label in first.stdout


def test_make_agent_demo_is_offline_and_writes_the_ignored_report() -> None:
    completed = subprocess.run(
        ["make", "-n", "agent-demo"],
        cwd=PROJECT_ROOT,
        env=_environment(),
        capture_output=True,
        check=False,
        text=True,
        timeout=30,
    )

    assert completed.returncode == 0, completed.stderr
    rendered = completed.stdout
    assert "python -m ecomsre.demo run" in rendered
    assert "artifacts/demo/agent-mainline-v1-report.json" in rendered
    assert all(
        token not in rendered.casefold()
        for token in (
            "docker ",
            "provider-smoke",
            "curl ",
            "wget ",
            "prometheus",
            "jaeger",
            "opensearch",
        )
    )
