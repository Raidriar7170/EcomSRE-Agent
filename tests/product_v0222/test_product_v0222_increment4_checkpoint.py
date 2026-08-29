from __future__ import annotations

from pathlib import Path

from scripts.ci.verify_product_v0222_increment4 import (
    verify_product_v0222_increment4,
)


def test_increment4_checkpoint_binds_p01_offline_and_fresh_holdout() -> None:
    result = verify_product_v0222_increment4(Path(__file__).resolve().parents[2])

    assert result["status"] == "ECOMSRE_PRODUCT_V0222_HOLDOUT_VERIFICATION_PASS"
    assert result["selected_candidate_alias"] == "P01"
    assert result["operator_selection_count"] == 1
    assert result["offline_changed_iteration_count"] == 2
    assert result["holdout_verification_session_count"] == 1
    assert result["holdout_read_only_request_count"] == 3
    assert result["holdout_transport_retry_count"] == 0
    assert result["accepted_checkout_record_count"] == 5
    assert result["normalization_profile_status"] == "ACTIVE"
    assert result["cleanup"] == "CLEAN"
