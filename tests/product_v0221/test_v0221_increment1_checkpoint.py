from __future__ import annotations

from pathlib import Path

from scripts.ci.verify_product_v0221_increment1 import (
    verify_product_v0221_increment1,
)


ROOT = Path(__file__).resolve().parents[2]


def test_increment1_checkpoint_replays_400_and_selects_official_plan_a() -> None:
    result = verify_product_v0221_increment1(ROOT)

    assert result["status"] == "ECOMSRE_PRODUCT_V0221_REQUEST_PROTOCOL_READY"
    assert result["history_status"] == "ECOMSRE_PRODUCT_V0221_HISTORY_VERIFIED"
    assert result["fixture_provenance"] == (
        "SAFE_REPRODUCTION_NOT_HISTORICAL_RAW_RESPONSE"
    )
    assert result["http_status"] == 400
    assert result["safe_error_code"] == "OPENSEARCH_REQUEST_BODY_INVALID"
    assert result["query_parameter_names"] == ()
    assert result["next_plan_variant"] == "PLAN_A_FIELD_CAPS_GET_QUERY"
    assert result["fault_attempt_count"] == 0
    assert result["baseline_readiness_attempt_count"] == 0
    assert result["knowledge_loop_campaign_count"] == 0
    assert result["agent_writes"] == 0
    assert result["runbook_executions"] == 0
    assert result["action_authority"] == "NONE"
