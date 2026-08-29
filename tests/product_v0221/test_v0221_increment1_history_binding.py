from __future__ import annotations

from pathlib import Path

from scripts.ci.verify_product_v0221_history import verify_product_v0221_history


ROOT = Path(__file__).resolve().parents[2]


def test_v0221_history_binds_all_three_blocked_predecessors() -> None:
    result = verify_product_v0221_history(ROOT)

    assert result["status"] == "ECOMSRE_PRODUCT_V0221_HISTORY_VERIFIED"
    assert result["v02_terminal"] == (
        "BLOCKED_ECOMSRE_PRODUCT_V02_UNKNOWN_FAULT_PROFILE"
    )
    assert result["v021_terminal"] == (
        "BLOCKED_ECOMSRE_PRODUCT_V021_BASELINE_READINESS"
    )
    assert result["v022_terminal"] == (
        "BLOCKED_ECOMSRE_PRODUCT_V022_SCHEMA_PROBE"
    )
    assert result["v022_head"] == "1568c72c3262befb90fb4e191592e51aa345bdcb"
    assert result["v022_execution_count"] == 1
    assert result["v022_request_count"] == 2
    assert result["v022_sample_count"] == 0
    assert result["baseline_unchanged"] is True
    assert result["owned_demo_cleanup"] == "CLEAN"
    assert result["fault_attempt_count"] == 0
    assert result["agent_writes"] == 0
    assert result["runbook_executions"] == 0
    assert result["bound_file_count"] >= 16
