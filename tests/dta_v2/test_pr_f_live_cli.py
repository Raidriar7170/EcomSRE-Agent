from __future__ import annotations

import os
from pathlib import Path
import subprocess


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
