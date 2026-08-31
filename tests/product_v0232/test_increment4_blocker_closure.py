from __future__ import annotations

import json
from pathlib import Path

import pytest

from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22
from scripts.ci.verify_product_v0232_blocker import verify_product_v0232_blocker


ROOT = Path(__file__).resolve().parents[2]


def test_attempt_one_blocker_addendum_closes_exact_failure_evidence() -> None:
    result = verify_product_v0232_blocker(ROOT)

    assert result == {
        "terminal": "BLOCKED_ECOMSRE_PRODUCT_V0232_TRAFFIC_PREFLIGHT",
        "attempt_ordinal": 1,
        "attempt_consumed": True,
        "attempt_2_authorized": False,
        "failure_stage": "RUNTIME_INSPECT_REQUEST_BUILD",
        "safe_error_code": "RUN_ID_SCHEMA_PATTERN_MISMATCH",
        "completed_transactions": 0,
        "formal_healthy_traffic_execution_count": 0,
        "action_authority": "NONE",
    }


def test_blocker_verifier_rejects_resealed_wrong_failure_code(
    tmp_path: Path,
) -> None:
    payload = json.loads(
        (
            ROOT
            / "docs/analysis/product-v0232-traffic-preflight-attempt-1-blocker-addendum.json"
        ).read_text(encoding="utf-8")
    )
    payload["safe_error_code"] = "ARBITRARY_RESEALED_CODE"
    payload.pop("addendum_sha256")
    payload["addendum_sha256"] = semantic_sha256_v22(payload)
    addendum_path = tmp_path / "addendum.json"
    addendum_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError):
        verify_product_v0232_blocker(ROOT, addendum_path=addendum_path)
