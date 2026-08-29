from __future__ import annotations

from pathlib import Path

from scripts.ci.verify_product_v0222_increment1 import (
    verify_product_v0222_increment1,
)


ROOT = Path(__file__).resolve().parents[2]


def test_increment1_capture_first_checkpoint() -> None:
    result = verify_product_v0222_increment1(ROOT)

    assert result == {
        "status": "ECOMSRE_PRODUCT_V0222_CAPTURE_FIRST_READY",
        "history_status": "ECOMSRE_PRODUCT_V0222_HISTORY_VERIFIED",
        "predecessor_audit_status": "ECOMSRE_PRODUCT_V0222_PREDECESSOR_AUDIT_PASS",
        "captured_response_count": 7,
        "capture_completeness": True,
        "resolution_failure_recovery": "PASS",
        "public_summary_raw_body_leak_count": 0,
        "fault_attempt_count": 0,
        "baseline_readiness_attempt_count": 0,
        "product_diagnosis_attempt_count": 0,
        "knowledge_loop_campaign_count": 0,
        "agent_writes": 0,
        "runbook_executions": 0,
        "action_authority": "NONE",
    }
