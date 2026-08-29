from __future__ import annotations

from pathlib import Path

from scripts.ci.verify_product_v0222_increment2 import (
    verify_product_v0222_increment2,
)


ROOT = Path(__file__).resolve().parents[2]


def test_increment2_candidate_set_engine_checkpoint() -> None:
    result = verify_product_v0222_increment2(ROOT)

    assert result["status"] == "ECOMSRE_PRODUCT_V0222_CANDIDATE_SET_ENGINE_READY"
    assert result["increment1_status"] == "ECOMSRE_PRODUCT_V0222_CAPTURE_FIRST_READY"
    assert 2 <= result["synthetic_candidate_count"] <= 12  # type: ignore[operator]
    assert result["candidate_count_bound"] == 12
    assert result["beam_width"] == 24
    assert result["component_count_per_kind_bound"] == 8
    assert result["recommendation_status"] == "OPERATOR_SELECTION_REQUIRED"
    assert result["operator_decision_status"] == "NOT_EXECUTED_SYNTHETIC_CHECKPOINT"
    assert result["live_capture_session_count"] == 0
    assert result["operator_selection_count"] == 0
    assert result["fault_attempt_count"] == 0
    assert result["baseline_readiness_attempt_count"] == 0
    assert result["product_diagnosis_attempt_count"] == 0
    assert result["knowledge_loop_campaign_count"] == 0
    assert result["agent_writes"] == 0
    assert result["runbook_executions"] == 0
    assert result["action_authority"] == "NONE"
