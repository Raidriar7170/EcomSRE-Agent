"""One optional Product read, outside the frozen Core action catalog."""

from __future__ import annotations

import json
from typing import Any

from pydantic import model_validator

from ecomsre.dta_v2.v22.action_catalog import EvidenceActionV22
from ecomsre.dta_v2.v22.read_contracts import (
    EvidenceSourceV22,
    MetricKindV22,
    build_canonical_read_request_v22,
    semantic_sha256_v22,
)


def _queue_payload() -> dict[str, Any]:
    request = build_canonical_read_request_v22(
        source=EvidenceSourceV22.METRICS,
        target_services=("fraud-detection",),
        metric_kinds=(MetricKindV22.QUEUE_LAG,),
        lookback_seconds=60,
        max_results=1,
    )
    return {
        "schema_version": "dta-v22.evidence-action.v1",
        "action_id": "a:metrics:fraud-detection:queue-lag",
        "source": "METRICS",
        "target_services": ["fraud-detection"],
        "request": request.model_dump(mode="json"),
        "coverage_keys": ["METRICS:fraud-detection:QUEUE_LAG"],
        "weighted_cost": 1.0,
        "request_sha256": request.request_sha256,
        "dominates_action_ids": [],
    }


class ProductQueueLagActionV030(EvidenceActionV22):
    """Validate this exact optional request without widening Core admission."""

    @model_validator(mode="after")
    def require_action_binding(self) -> ProductQueueLagActionV030:
        expected = _queue_payload()
        if self.model_dump(
            mode="json", exclude={"action_sha256"}
        ) != expected or self.action_sha256 != semantic_sha256_v22(expected):
            raise ValueError("Product queue-lag action differs from its exact contract")
        return self


def build_queue_lag_action_v030() -> ProductQueueLagActionV030:
    payload = _queue_payload()
    return ProductQueueLagActionV030.model_validate_json(
        json.dumps(
            {
                **payload,
                "action_sha256": semantic_sha256_v22(payload),
            }
        )
    )
