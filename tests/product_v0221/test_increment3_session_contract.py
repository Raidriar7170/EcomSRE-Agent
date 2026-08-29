from __future__ import annotations

import json
from pathlib import Path

import pytest

from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22
from ecomsre.product.connectors.opensearch_probe_session_v0221 import (
    OpenSearchSchemaSessionProfileV0221,
    load_schema_session_profile_v0221,
)
from scripts.ci.verify_product_v0221_live_preflight import (
    verify_product_v0221_live_preflight,
)


def _payload() -> dict[str, object]:
    body: dict[str, object] = {
        "schema_version": "ecomsre.product.opensearch-schema-session-profile.v0221",
        "session_id": "product-v0221-schema-discovery-1",
        "index_pattern": "otel-logs-*",
        "checkout_aliases": ["checkout", "checkoutservice"],
        "maximum_changed_plan_count": 3,
        "maximum_request_count": 16,
        "maximum_transport_retries": 2,
        "maximum_sample_documents": 5,
        "maximum_response_bytes": 2000000,
        "recent_window_seconds": 600,
        "stabilization_seconds": 30,
        "healthy_traffic_profile": {
            "request_seed": 5221,
            "maximum_request_count": 30,
            "requests_per_second": 1.0,
            "error_budget": 5,
        },
        "private_root": ".local/product-v0221/opensearch-schema-session/private",
        "schema_session_json": "docs/analysis/product-v0221-schema-session.json",
        "schema_session_markdown": "docs/analysis/product-v0221-schema-session.md",
        "normalization_profile_path": "config/product-v0221/opensearch-probe/normalization-profile.json",
        "sanitized_fixture_path": "tests/fixtures/product_v0221/opensearch_live_shape.json",
        "offline_parser_report_path": "docs/analysis/product-v0221-offline-parser.json",
    }
    body["profile_sha256"] = semantic_sha256_v22(body)
    return body


def test_schema_session_profile_is_digest_bound_and_exactly_bounded(
    tmp_path: Path,
) -> None:
    payload = _payload()
    path = tmp_path / "profile.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    profile = load_schema_session_profile_v0221(path)

    assert profile.session_id == "product-v0221-schema-discovery-1"
    assert profile.maximum_changed_plan_count == 3
    assert profile.maximum_request_count == 16
    assert profile.maximum_transport_retries == 2
    assert profile.private_root.startswith(".local/product-v0221/")


def test_schema_session_profile_rejects_relaxed_or_predecessor_boundaries() -> None:
    payload = _payload()
    payload["maximum_request_count"] = 17
    payload["private_root"] = (
        ".local/product-v022/opensearch-schema-probe/private"
    )
    payload["profile_sha256"] = semantic_sha256_v22(
        {key: value for key, value in payload.items() if key != "profile_sha256"}
    )

    with pytest.raises(ValueError):
        OpenSearchSchemaSessionProfileV0221.model_validate(payload)


def test_live_preflight_refuses_the_consumed_session() -> None:
    root = Path(__file__).resolve().parents[2]
    with pytest.raises(ValueError, match="live preflight differs"):
        verify_product_v0221_live_preflight(root)
