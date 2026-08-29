from __future__ import annotations

from pathlib import Path

from scripts.ci.verify_product_v0221_increment2 import (
    verify_product_v0221_increment2,
)


ROOT = Path(__file__).resolve().parents[2]


def test_increment2_checkpoint_passes_offline_protocol_matrix() -> None:
    result = verify_product_v0221_increment2(ROOT)

    assert result["status"] == "ECOMSRE_PRODUCT_V0221_REQUEST_PROTOCOL_PASS"
    assert result["case_count"] == 9
    assert result["plan_count"] == 3
    assert result["live_schema_discovery_session_count"] == 0
    assert result["total_live_read_only_opensearch_request_count"] == 0
    assert result["fault_attempt_count"] == 0
    assert result["agent_writes"] == 0
    assert result["runbook_executions"] == 0
