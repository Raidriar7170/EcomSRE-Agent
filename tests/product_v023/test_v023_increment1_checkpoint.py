from __future__ import annotations

from pathlib import Path

from scripts.ci.verify_product_v023_increment1 import verify_product_v023_increment1


ROOT = Path(__file__).resolve().parents[2]


def test_increment1_profile_binding_checkpoint() -> None:
    result = verify_product_v023_increment1(ROOT)

    assert result["status"] == "ECOMSRE_PRODUCT_V023_PROFILE_BINDING_PASS"
    assert result["history_status"] == "ECOMSRE_PRODUCT_V023_HISTORY_VERIFIED"
    assert result["settings_modes"] == (
        "LEGACY_EXPLICIT_FIELDS",
        "PROFILE_BOUND",
    )
    assert result["active_profile_sha256"] == (
        "b9577dfc4eaa933b62048bbcbd041ed470343f7c76255ab851cdcaeef60a7df2"
    )
    assert result["selected_candidate_alias"] == "P01"
    assert result["timestamp_query_field"] == "@timestamp"
    assert result["severity_field"] == "severity.text"
    assert result["trace_id_field"] == "traceId"
    assert result["profile_snapshot_persisted_in_environment"] is True
    assert result["fault_attempt_count"] == 0
    assert result["baseline_readiness_attempt_count"] == 0
    assert result["product_diagnosis_attempt_count"] == 0
    assert result["knowledge_loop_campaign_count"] == 0
    assert result["agent_writes"] == 0
    assert result["runbook_executions"] == 0
    assert result["action_authority"] == "NONE"
