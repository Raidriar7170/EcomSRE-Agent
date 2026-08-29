from __future__ import annotations

from pathlib import Path

from scripts.ci.verify_product_v022_history import verify_product_v022_history
from scripts.ci.verify_product_v022_increment1 import verify_product_v022_increment1


ROOT = Path(__file__).resolve().parents[2]


def test_v022_history_binds_both_blocked_predecessors() -> None:
    result = verify_product_v022_history(ROOT)

    assert result["status"] == "ECOMSRE_PRODUCT_V022_HISTORY_VERIFIED"
    assert result["v02_head"] == "a439f8882cd2fcdd3767f6bcfd5d955219fa1e15"
    assert result["v021_head"] == "55ccae45738c00a8be3752b81fecf19f37c87ce5"
    assert result["v02_terminal"] == (
        "BLOCKED_ECOMSRE_PRODUCT_V02_UNKNOWN_FAULT_PROFILE"
    )
    assert result["v021_terminal"] == (
        "BLOCKED_ECOMSRE_PRODUCT_V021_BASELINE_READINESS"
    )
    assert result["v021_readiness_attempt_count"] == 2
    assert result["fault_attempt_count"] == 0
    assert result["agent_writes"] == 0
    assert result["runbook_executions"] == 0


def test_increment1_instrumentation_remains_bound_after_probe_consumption() -> None:
    result = verify_product_v022_increment1(ROOT)
    assert result["status"] == (
        "ECOMSRE_PRODUCT_V022_SCHEMA_INSTRUMENTATION_READY"
    )
    assert result["typed_error_code_count"] == 30
    assert result["schema_probe_execution_count"] == 1
    assert result["v021_readiness_attempt_count"] == 2
