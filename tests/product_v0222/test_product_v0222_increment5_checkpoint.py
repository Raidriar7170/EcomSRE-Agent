from __future__ import annotations

from pathlib import Path

from scripts.ci.verify_product_v0222_increment5 import (
    verify_product_v0222_increment5,
)


def test_increment5_checkpoint_binds_smoke_and_baseline_handoff() -> None:
    result = verify_product_v0222_increment5(Path(__file__).resolve().parents[2])

    assert result["status"] == "ECOMSRE_PRODUCT_V0222_CONNECTOR_SMOKE_PASS"
    assert result["query_count"] == 3
    assert result["nonempty_window_count"] == 3
    assert result["accepted_checkout_record_count"] == 15
    assert result["active_profile_survived_restart"] is True
    assert result["restart_proof_terminal"] == (
        "ECOMSRE_PRODUCT_V0222_ACTIVE_PROFILE_RESTART_PROOF_PASS"
    )
    assert result["live_smoke_rerun_count"] == 0
    assert result["successful_identity_query_count"] == 3
    assert result["baseline_readiness_attempt_count"] == 0
    assert result["cleanup"] == "CLEAN"
