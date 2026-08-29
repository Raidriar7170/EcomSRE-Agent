from __future__ import annotations

import json
from pathlib import Path

from ecomsre.product.cli import main as product_main
from scripts.product_v0221.run_opensearch_schema_probe import (
    verify_schema_session_contract_v0221,
)


ROOT = Path(__file__).resolve().parents[2]


def test_new_cli_reports_the_consumed_schema_ambiguity_blocker(
    capsys,
    monkeypatch,
) -> None:
    result = verify_schema_session_contract_v0221(ROOT)

    assert result["status"] == "BLOCKED_ECOMSRE_PRODUCT_V0221_SCHEMA_AMBIGUOUS"
    assert result["live_schema_discovery_session_count"] == 1
    assert result["total_read_only_opensearch_request_count"] == 6
    assert result["rerun_authority"] == "NONE"
    assert result["fault_attempt_count"] == 0
    assert result["baseline_readiness_attempt_count"] == 0
    assert result["agent_writes"] == 0
    assert result["runbook_executions"] == 0

    monkeypatch.chdir(ROOT)
    assert (
        product_main(
            [
                "product-v0221",
                "opensearch-probe",
                "--config",
                "config/product-v0221/opensearch-probe/profile.json",
            ]
        )
        == 0
    )
    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "BLOCKED_ECOMSRE_PRODUCT_V0221_SCHEMA_AMBIGUOUS"
    assert output["live_schema_discovery_session_count"] == 1
