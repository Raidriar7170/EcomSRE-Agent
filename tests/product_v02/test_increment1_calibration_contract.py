from __future__ import annotations

from pathlib import Path
import json
import subprocess
import sys

import pytest

from scripts.product_v02.calibrate_unknown_profile import (
    verify_calibration_contract,
)
from ecomsre.product.pilot.live_calibration_v02 import run_live_calibration_v02


ROOT = Path(__file__).resolve().parents[2]


def test_calibration_contract_is_pinned_bounded_and_zero_attempt() -> None:
    result = verify_calibration_contract(ROOT)

    assert result["terminal"] == "ECOMSRE_PRODUCT_V02_CALIBRATION_CONTRACT_PASS"
    assert result["candidate_values"] == (5, 10, 20)
    assert result["traffic_profile_count"] == 6
    assert result["live_attempt_count"] == 0
    assert result["action_authority"] == "NONE"
    assert result["runbook_executions"] == 0


def test_tracked_consumed_campaign_blocks_fresh_checkout_before_live_action() -> None:
    with pytest.raises(ValueError, match="tracked calibration campaign is consumed"):
        run_live_calibration_v02(repository_root=ROOT)


def test_goal_documented_module_cli_works_without_pythonpath() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "scripts.product_v02.calibrate_unknown_profile",
            "--check-only",
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        env={key: value for key, value in __import__("os").environ.items() if key != "PYTHONPATH"},
    )
    result = json.loads(completed.stdout)
    assert result["terminal"] == "ECOMSRE_PRODUCT_V02_CALIBRATION_CONTRACT_PASS"
    assert result["campaign_consumed"] is True
