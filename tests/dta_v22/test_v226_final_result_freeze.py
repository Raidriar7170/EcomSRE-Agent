from __future__ import annotations

from pathlib import Path

from scripts.ci.verify_dta_v226_real_fault_result import (
    verify_dta_v226_real_fault_result,
)


ROOT = Path(__file__).resolve().parents[2]


def test_v226_final_real_fault_result_is_frozen_and_self_consistent() -> None:
    result = verify_dta_v226_real_fault_result(ROOT)

    assert result["status"] == "DTA_V226_REAL_FAULT_RESULT_VERIFIED"
    assert result["execution_count"] == 1
    assert result["arm_run_count"] == 8
    assert result["valid_terminal_count"] == 8
    assert result["baseline_restored"] is True
    assert result["cleanup"] == "CLEAN"
    assert result["non_owned_changes"] == 0
