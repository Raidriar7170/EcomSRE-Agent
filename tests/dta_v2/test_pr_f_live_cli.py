from __future__ import annotations

import os
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from ecomsre.dta_v2 import live_cli


ROOT = Path(__file__).resolve().parents[2]


def test_project_environment_can_import_owned_campaign_cli_help() -> None:
    python = ROOT / ".venv/bin/python"
    completed = subprocess.run(
        (str(python), "-m", "ecomsre.dta_v2.live_cli", "--help"),
        cwd=ROOT,
        env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
        capture_output=True,
        check=False,
        text=True,
        timeout=15,
    )

    assert completed.returncode == 0, completed.stderr
    assert "exact four-attempt DTA v2 owned local campaign" in completed.stdout
    assert "pyarrow" not in completed.stderr


def test_review_required_campaign_does_not_publish_success_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    public_root = tmp_path / "public"
    writes: list[object] = []
    monkeypatch.setattr(live_cli, "load_live_demo_config", lambda path: object())
    monkeypatch.setattr(live_cli, "load_runbook_registry", lambda path: object())
    monkeypatch.setattr(live_cli, "load_master_authorization", lambda path: object())
    monkeypatch.setattr(live_cli, "OwnedLiveCampaign", lambda **kwargs: object())
    monkeypatch.setattr(live_cli, "run_owned_live_campaign", lambda campaign: ())
    monkeypatch.setattr(
        live_cli,
        "build_public_live_campaign_report",
        lambda closures: SimpleNamespace(
            terminal="DTA_V2_LIVE_DEMO_REVIEW_REQUIRED"
        ),
    )
    monkeypatch.setattr(
        live_cli,
        "write_public_live_campaign_artifacts",
        lambda **kwargs: writes.append(kwargs),
    )

    exit_code = live_cli.main(
        (
            "--repository-root",
            str(ROOT),
            "--private-root",
            str(tmp_path / "private"),
            "--provider-env",
            str(tmp_path / "provider.env"),
            "--master-authorization",
            str(tmp_path / "authorization.json"),
            "--campaign-id",
            "d800fdaebec596d4c14c80c7b6816054",
            "--public-result-root",
            str(public_root),
        )
    )

    assert exit_code == 2
    assert writes == []
    assert not public_root.exists()
