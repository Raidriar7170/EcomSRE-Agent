from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]


def _run(*arguments: str) -> dict[str, object]:
    environment = dict(os.environ)
    environment["PYTHONPATH"] = f"{ROOT}:{ROOT / 'src'}"
    completed = subprocess.run(
        (sys.executable, "-m", "ecomsre.product.cli", *arguments),
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        env=environment,
    )
    return json.loads(completed.stdout)


def test_history_verify_cli_reports_both_frozen_predecessors() -> None:
    result = _run("product-v022", "history-verify")
    assert result["status"] == "ECOMSRE_PRODUCT_V022_HISTORY_VERIFIED"
    assert result["v021_readiness_attempt_count"] == 2
    assert result["fault_attempt_count"] == 0


def test_probe_cli_reports_consumed_blocker_without_another_campaign() -> None:
    private_root = (
        ROOT / ".local/product-v022/opensearch-schema-probe/private"
    )
    before = tuple(private_root.glob("schema-probe-*.json"))
    result = _run(
        "product-v022",
        "opensearch-probe",
        "--config",
        "config/product-v022/opensearch-probe/profile.json",
    )
    after = tuple(private_root.glob("schema-probe-*.json"))

    assert result["status"] == "BLOCKED_ECOMSRE_PRODUCT_V022_SCHEMA_PROBE"
    assert result["execution_count"] == 1
    assert result["request_count"] == 2
    assert result["owned_demo_cleanup"] == "CLEAN"
    assert result["maximum_request_count"] == 12
    assert before == after


def test_probe_cli_refuses_a_second_live_execution() -> None:
    private_root = ROOT / ".local/product-v022/opensearch-schema-probe/private"
    before = tuple(private_root.glob("schema-probe-*.json"))
    result = _run(
        "product-v022",
        "opensearch-probe",
        "--config",
        "config/product-v022/opensearch-probe/profile.json",
        "--execute-live",
    )
    after = tuple(private_root.glob("schema-probe-*.json"))

    assert result["status"] == "BLOCKED_ECOMSRE_PRODUCT_V022_SCHEMA_PROBE"
    assert result["execution_count"] == 1
    assert before == after
