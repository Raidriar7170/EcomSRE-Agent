"""Offline CLI and Make wiring checks for the Phase 2 comparison."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path

import pytest

from ecomsre.phase2.cli import (
    aggregate_provider_smoke,
    run_provider_smoke,
    run_provider_smoke_case,
)
from ecomsre.phase2.token_policy import MODEL_SNAPSHOT, load_token_authority


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

    provider = subprocess.run(
        ["make", "-n", "phase2-provider-smoke"],
        cwd=PROJECT_ROOT,
        env=_environment(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        text=True,
        timeout=30,
    )
    assert provider.returncode == 0, provider.stderr
    assert "ecomsre.phase2.cli provider-smoke" in provider.stdout

    case = subprocess.run(
        [
            "make",
            "-n",
            "phase2-provider-smoke-case",
            "PHASE2_PROVIDER_REQUIREMENT=fixed_positive",
            f"PHASE2_PROVIDER_CASE_ROOT={tmp_path}",
        ],
        cwd=PROJECT_ROOT,
        env=_environment(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        text=True,
        timeout=30,
    )
    assert case.returncode == 0, case.stderr
    assert "provider-smoke-case" in case.stdout
    assert "--requirement \"fixed_positive\"" in case.stdout

    aggregate = subprocess.run(
        [
            "make",
            "-n",
            "phase2-provider-smoke-aggregate",
            f"PHASE2_PROVIDER_CASE_ROOT={tmp_path}",
        ],
        cwd=PROJECT_ROOT,
        env=_environment(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        text=True,
        timeout=30,
    )
    assert aggregate.returncode == 0, aggregate.stderr
    assert "provider-smoke-aggregate" in aggregate.stdout
    assert f'--case-root "{tmp_path}"' in aggregate.stdout


class ForbiddenTransport:
    def post_json(
        self,
        *,
        url: str,
        headers: Mapping[str, str],
        payload: Mapping[str, object],
        timeout_seconds: float,
    ) -> Mapping[str, object]:
        del url, headers, payload, timeout_seconds
        raise AssertionError("unconfigured smoke touched the provider transport")


def test_unconfigured_provider_smoke_skips_without_transport() -> None:
    report = run_provider_smoke(
        project_root=PROJECT_ROOT,
        environment={},
        transport=ForbiddenTransport(),
    )

    assert report == {
        "schema_version": "phase2.provider-smoke-report.v1",
        "status": "SKIPPED_NOT_CONFIGURED",
        "provider": "openai-compatible",
        "model": None,
        "scripted_fallback": False,
        "case_results": [],
        "requirements": {
            "fixed_positive": False,
            "dynamic_positive": False,
            "fixed_negative": False,
            "dynamic_negative": False,
            "no_scripted_fallback": True,
        },
    }


def test_unconfigured_provider_smoke_case_skips_without_transport() -> None:
    report = run_provider_smoke_case(
        project_root=PROJECT_ROOT,
        environment={},
        requirement="fixed_positive",
        transport=ForbiddenTransport(),
    )

    assert report["status"] == "SKIPPED_NOT_CONFIGURED"
    assert report["requirement"] == "fixed_positive"
    assert report["scripted_fallback"] is False
    assert report["provider_call_count"] == 0


def _passed_case_report(requirement: str) -> dict[str, object]:
    definitions = {
        "fixed_positive": (
            "FIXED_SPECIALIST_WORKFLOW",
            "ad-partial-failure-complete",
            "RCA_CONFIRMED",
        ),
        "dynamic_positive": (
            "DYNAMIC_MULTI_AGENT",
            "ad-partial-failure-complete",
            "RCA_CONFIRMED",
        ),
        "fixed_negative": (
            "FIXED_SPECIALIST_WORKFLOW",
            "no-real-incident",
            "ABSTAIN",
        ),
        "dynamic_negative": (
            "DYNAMIC_MULTI_AGENT",
            "no-real-incident",
            "ABSTAIN",
        ),
    }
    variant, case_id, decision = definitions[requirement]
    return {
        "schema_version": "phase2.provider-smoke-case-report.v1",
        "status": "PASSED",
        "requirement": requirement,
        "provider": "openai-compatible",
        "model": MODEL_SNAPSHOT,
        "token_policy_core_sha256": load_token_authority(
            PROJECT_ROOT
        ).core_sha256,
        "scripted_fallback": False,
        "provider_call_count": 1,
        "provider_prompt_tokens": [123],
        "case_result": {
            "variant": variant,
            "case_id": case_id,
            "status": "COMPLETED",
            "expected_decision": decision,
            "decision": decision,
            "evidence_references_valid": True,
            "provider_identity_valid": True,
            "terminal_failure_code": None,
        },
    }


def _write_case_reports(case_root: Path) -> None:
    case_root.mkdir(parents=True, exist_ok=True)
    for requirement in (
        "fixed_positive",
        "dynamic_positive",
        "fixed_negative",
        "dynamic_negative",
    ):
        (case_root / f"{requirement}.json").write_text(
            json.dumps(_passed_case_report(requirement)),
            encoding="utf-8",
        )


def test_provider_smoke_aggregate_binds_four_passed_case_reports(
    tmp_path: Path,
) -> None:
    _write_case_reports(tmp_path)

    report = aggregate_provider_smoke(case_root=tmp_path)

    assert report["status"] == "PASSED"
    assert report["model"] == MODEL_SNAPSHOT
    assert report["token_policy_core_sha256"] == load_token_authority(
        PROJECT_ROOT
    ).core_sha256
    assert report["scripted_fallback"] is False
    assert report["provider_call_count"] == 4
    assert report["requirements"] == {
        "fixed_positive": True,
        "dynamic_positive": True,
        "fixed_negative": True,
        "dynamic_negative": True,
        "no_scripted_fallback": True,
    }


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("model", "different-model"),
        ("token_policy_core_sha256", "b" * 64),
        ("scripted_fallback", True),
    ),
)
def test_provider_smoke_aggregate_rejects_mixed_authority(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    _write_case_reports(tmp_path)
    path = tmp_path / "dynamic_negative.json"
    report = json.loads(path.read_text(encoding="utf-8"))
    report[field] = value
    path.write_text(json.dumps(report), encoding="utf-8")

    with pytest.raises(ValueError, match="one authority"):
        aggregate_provider_smoke(case_root=tmp_path)


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("model", "stale-model"),
        ("token_policy_core_sha256", "b" * 64),
    ),
)
def test_provider_smoke_aggregate_rejects_uniformly_stale_authority(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    _write_case_reports(tmp_path)
    for path in tmp_path.glob("*.json"):
        report = json.loads(path.read_text(encoding="utf-8"))
        report[field] = value
        path.write_text(json.dumps(report), encoding="utf-8")

    with pytest.raises(ValueError, match="current frozen authority"):
        aggregate_provider_smoke(case_root=tmp_path)


def test_provider_smoke_cli_writes_skip_report_when_unconfigured(
    tmp_path: Path,
) -> None:
    report_path = tmp_path / "provider-smoke-report.json"
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "ecomsre.phase2.cli",
            "provider-smoke",
            "--output",
            str(report_path),
        ],
        cwd=PROJECT_ROOT,
        env=_environment(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        text=True,
        timeout=30,
    )

    assert completed.returncode == 3, completed.stderr
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["status"] == "SKIPPED_NOT_CONFIGURED"
    assert report["scripted_fallback"] is False


def test_provider_smoke_cli_preserves_the_previous_attempt(
    tmp_path: Path,
) -> None:
    report_path = tmp_path / "provider-smoke-report.json"
    previous = b'{"status":"FAILED"}\n'
    report_path.write_bytes(previous)

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "ecomsre.phase2.cli",
            "provider-smoke",
            "--output",
            str(report_path),
        ],
        cwd=PROJECT_ROOT,
        env=_environment(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        text=True,
        timeout=30,
    )

    assert completed.returncode == 3, completed.stderr
    digest = hashlib.sha256(previous).hexdigest()
    assert (tmp_path / "attempts" / f"{digest}.json").read_bytes() == previous
