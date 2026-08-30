from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from scripts.ci.verify_product_v023_history import verify_product_v023_history


ROOT = Path(__file__).resolve().parents[2]


def test_v023_history_binds_merged_v0222_handoff_without_mutation() -> None:
    result = verify_product_v023_history(ROOT)

    assert result == {
        "status": "ECOMSRE_PRODUCT_V023_HISTORY_VERIFIED",
        "starting_main": "613f6203e4a174b4549b912cb16ca7998cf6238c",
        "v0222_terminal": (
            "ECOMSRE_PRODUCT_V0222_CAPTURE_FIRST_OPERATOR_PROFILE_COMPLETE"
        ),
        "handoff_terminal": "ECOMSRE_PRODUCT_V0222_BASELINE_HANDOFF_READY",
        "active_profile_sha256": (
            "b9577dfc4eaa933b62048bbcbd041ed470343f7c76255ab851cdcaeef60a7df2"
        ),
        "handoff_sha256": (
            "fee46e6f335f106f365c3c0c85bb1cf8e7fb0b7cbf00289f5555ec84ea0cdaa7"
        ),
        "bound_file_count": 12,
        "fault_attempt_count": 0,
        "baseline_readiness_attempt_count": 0,
        "product_diagnosis_attempt_count": 0,
        "knowledge_loop_campaign_count": 0,
        "agent_writes": 0,
        "runbook_executions": 0,
        "action_authority": "NONE",
    }


def test_v023_history_rejects_a_role_bound_to_the_wrong_starting_main_path(
    tmp_path: Path,
) -> None:
    manifest = json.loads(
        (ROOT / "config/product-v023/historical-results.v1.json").read_text(
            encoding="utf-8"
        )
    )
    replacement = (ROOT / "pyproject.toml").read_bytes()
    predecessor = next(
        item
        for item in manifest["files"]
        if item["role"] == "V0222_PREDECESSOR_HISTORY"
    )
    predecessor.update(
        {
            "path": "pyproject.toml",
            "sha256": hashlib.sha256(replacement).hexdigest(),
            "size_bytes": len(replacement),
        }
    )
    tampered = tmp_path / "historical-results.v1.json"
    tampered.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ValueError, match="historical role path differs"):
        verify_product_v023_history(ROOT, tampered)
