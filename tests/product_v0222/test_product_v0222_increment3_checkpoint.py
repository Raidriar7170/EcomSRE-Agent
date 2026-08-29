from __future__ import annotations

from pathlib import Path

from scripts.ci.verify_product_v0222_increment3 import (
    verify_product_v0222_increment3,
)


def test_increment3_checkpoint_is_bound_to_frozen_candidate_set() -> None:
    root = Path(__file__).resolve().parents[2]

    result = verify_product_v0222_increment3(root)

    assert result["status"] == "BLOCKED_ECOMSRE_PRODUCT_V0222_OPERATOR_SELECTION"
    assert result["capture_session_count"] == 1
    assert result["read_only_request_count"] == 7
    assert result["offline_changed_iteration_count"] == 1
    assert result["operator_selection_count"] == 0
    assert result["candidate_count"] == 2
