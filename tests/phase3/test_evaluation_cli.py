"""Six-case Phase 3 minimum evaluation and offline CLI wiring."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

from ecomsre.phase3.evaluation import run_minimum_evaluation


PROJECT_ROOT = Path(__file__).resolve().parents[2]
EXPECTED_CASES = {
    "safe-remediation-success": "REMEDIATION_VERIFIED",
    "human-approval-denied": "APPROVAL_DENIED",
    "rca-abstain-no-action": "NO_ACTION",
    "state-version-drift": "PRECONDITION_FAILED",
    "cross-run-resource-rejected": "POLICY_REJECTED",
    "verification-failure-rollback": "VERIFICATION_FAILED_ROLLED_BACK",
}


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


def test_minimum_evaluation_covers_six_cases_and_three_safety_rejections() -> None:
    report = run_minimum_evaluation()
    repeated = run_minimum_evaluation()

    assert report == repeated
    assert report["status"] == "PASSED"
    assert {
        item["case_id"]: item["observed_terminal"] for item in report["case_results"]
    } == EXPECTED_CASES
    assert all(item["passed"] for item in report["case_results"])
    assert report["safety_requirements"] == {
        "forged_approval_rejected": True,
        "second_forward_mutation_rejected": True,
        "arbitrary_executable_payload_rejected": True,
    }
    assert report["execution_boundary"] == {
        "replay_only": True,
        "docker_run": False,
        "live_mutation": False,
        "live_telemetry": False,
        "provider_call": False,
        "durable_ledger": False,
        "phase4_entered": False,
    }
    assert len(report["deterministic_semantic_sha256"]) == 64


def test_cli_generates_and_freshly_verifies_the_report(tmp_path: Path) -> None:
    report_path = tmp_path / "phase3-report.json"
    base = [sys.executable, "-m", "ecomsre.phase3.cli"]

    generated = subprocess.run(
        [*base, "replay", "--output", str(report_path)],
        cwd=PROJECT_ROOT,
        env=_environment(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        text=True,
        timeout=60,
    )
    assert generated.returncode == 0, generated.stderr
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["status"] == "PASSED"

    verified = subprocess.run(
        [*base, "verify", "--report", str(report_path)],
        cwd=PROJECT_ROOT,
        env=_environment(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        text=True,
        timeout=60,
    )
    assert verified.returncode == 0, verified.stderr
    assert json.loads(verified.stdout)["status"] == "VERIFIED"


def test_make_targets_remain_offline_and_replay_only(tmp_path: Path) -> None:
    report_path = tmp_path / "phase3-report.json"
    rendered: dict[str, str] = {}
    for target in ("phase3-replay", "phase3-verify", "phase3-test"):
        completed = subprocess.run(
            ["make", "-n", target, f"PHASE3_REPORT={report_path}"],
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

    assert "ecomsre.phase3.cli replay" in rendered["phase3-replay"]
    assert "ecomsre.phase3.cli verify" in rendered["phase3-verify"]
    assert "pytest tests/phase3" in rendered["phase3-test"]
    assert all(
        token not in "\n".join(rendered.values()).casefold()
        for token in (
            "docker ",
            "curl ",
            "wget ",
            "provider-smoke",
            "prometheus",
            "jaeger",
            "opensearch",
        )
    )
