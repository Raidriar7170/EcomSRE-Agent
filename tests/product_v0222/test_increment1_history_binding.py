from __future__ import annotations

from pathlib import Path

from scripts.ci.verify_product_v0222_history import verify_product_v0222_history


ROOT = Path(__file__).resolve().parents[2]


def test_v0222_history_preserves_all_four_predecessors() -> None:
    result = verify_product_v0222_history(ROOT)

    assert result["status"] == "ECOMSRE_PRODUCT_V0222_HISTORY_VERIFIED"
    assert result["v0221_terminal"] == (
        "BLOCKED_ECOMSRE_PRODUCT_V0221_SCHEMA_AMBIGUOUS"
    )
    assert result["v0221_live_schema_session_count"] == 1
    assert result["v0221_read_only_request_count"] == 6
    assert result["v0221_changed_request_plan_count"] == 0
    assert result["transitive_bound_file_count"] == 16
    assert result["direct_bound_file_count"] == 8
    assert result["cleanup"] == "CLEAN"
    assert result["fault_attempt_count"] == 0
    assert result["baseline_readiness_attempt_count"] == 0
    assert result["product_diagnosis_attempt_count"] == 0
    assert result["knowledge_loop_campaign_count"] == 0
    assert result["agent_writes"] == 0
    assert result["runbook_executions"] == 0
    assert result["action_authority"] == "NONE"
