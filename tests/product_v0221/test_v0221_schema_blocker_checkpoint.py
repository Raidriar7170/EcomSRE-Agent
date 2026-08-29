from __future__ import annotations

from pathlib import Path

from scripts.ci.verify_product_v0221_schema_blocker import (
    SCHEMA_AMBIGUOUS_BLOCKER_V0221,
    verify_product_v0221_schema_blocker,
)


ROOT = Path(__file__).resolve().parents[2]


def test_consumed_schema_ambiguity_blocker_is_frozen() -> None:
    result = verify_product_v0221_schema_blocker(ROOT)

    assert result["status"] == SCHEMA_AMBIGUOUS_BLOCKER_V0221
    assert result["live_schema_discovery_session_count"] == 1
    assert result["total_read_only_opensearch_request_count"] == 6
    assert result["normalization_profile_status"] == "ABSENT"
    assert result["offline_parser_status"] == "NOT_STARTED"
    assert result["connector_smoke_status"] == "NOT_STARTED"
    assert result["rerun_authority"] == "NONE"
    assert result["baseline_unchanged"] is True
    assert result["owned_demo_cleanup"] == "CLEAN"
