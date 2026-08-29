from __future__ import annotations

from pathlib import Path

from scripts.ci.verify_product_v02_blocked_result import (
    verify_product_v02_blocked_result,
)


ROOT = Path(__file__).resolve().parents[2]


def test_blocked_result_verifier_binds_consumption_and_safe_terminal() -> None:
    result = verify_product_v02_blocked_result(ROOT)

    assert result["status"] == "ECOMSRE_PRODUCT_V02_BLOCKED_RESULT_VERIFIED"
    assert result["live_attempt_count"] == 0
    assert result["outer_baseline_restored"] is True
    assert result["owned_demo_cleanup"] == "CLEAN"
    assert result["cleanup_closure_sha256"] == (
        "98d7444d89219d124425ac0653a5a8bae1b1f67976e79ceb1e4276da3cec1dda"
    )
