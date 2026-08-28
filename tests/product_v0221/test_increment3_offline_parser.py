from __future__ import annotations

from datetime import UTC, datetime

from ecomsre.product.connectors.opensearch_probe_resolution_v0221 import (
    OpenSearchNormalizationProfileV0221,
)
from ecomsre.product.connectors.opensearch_probe_session_v0221 import (
    OFFLINE_PARSER_BLOCKED_V0221,
    OFFLINE_PARSER_PASS_V0221,
    build_sanitized_live_fixture_v0221,
    evaluate_offline_parser_v0221,
)


def _profile() -> OpenSearchNormalizationProfileV0221:
    return OpenSearchNormalizationProfileV0221.model_validate_json(
        (
            __import__("pathlib").Path(__file__).parents[2]
            / "tests/fixtures/product_v0221/"
            "opensearch_verified_profile.synthetic.json"
        ).read_text(encoding="utf-8")
    )


def _live_response() -> dict[str, object]:
    return {
        "timed_out": False,
        "_shards": {"failed": 0},
        "hits": {
            "total": {"value": 2, "relation": "eq"},
            "hits": [
                {
                    "_index": "otel-v1-apm-span-private-1",
                    "_id": "private-id-1",
                    "_source": {
                        "observed": {"timestamp": "2026-08-28T12:00:01Z"},
                        "resource": {"service": {"name": "checkoutservice"}},
                        "body": {"stringValue": "private checkout message"},
                        "severityText": "INFO",
                        "traceId": "a" * 32,
                    },
                },
                {
                    "_index": "otel-v1-apm-span-private-1",
                    "_id": "private-id-2",
                    "_source": {
                        "observed": {"timestamp": "2026-08-28T12:00:02Z"},
                        "resource": {"service": {"name": "checkoutservice"}},
                        "body": {"stringValue": "another private message"},
                        "severityText": "WARN",
                        "traceId": "b" * 32,
                    },
                },
            ],
        },
    }


def test_sanitized_live_shape_preserves_structure_and_parses_offline() -> None:
    profile = _profile()
    fixture = build_sanitized_live_fixture_v0221(
        live_response=_live_response(),
        profile=profile,
        private_sample_shape_sha256="1" * 64,
        started_at=datetime(2026, 8, 28, 12, 0, tzinfo=UTC),
        ended_at=datetime(2026, 8, 28, 12, 5, tzinfo=UTC),
        service_aliases={"checkoutservice": "checkout"},
    )
    report = evaluate_offline_parser_v0221(fixture=fixture, profile=profile)

    assert fixture.response["hits"]["hits"][0]["_source"] == {  # type: ignore[index]
        "body": {"stringValue": "Checkout request completed."},
        "observed": {"timestamp": "2026-08-28T12:02:30Z"},
        "resource": {"service": {"name": "checkoutservice"}},
        "severityText": "INFO",
        "traceId": "0" * 32,
    }
    serialized = fixture.model_dump_json()
    assert "private checkout message" not in serialized
    assert "another private message" not in serialized
    assert "otel-v1-apm-span-private-1" not in serialized
    assert report.terminal == OFFLINE_PARSER_PASS_V0221
    assert report.accepted_record_count == 2
    assert report.rejected_record_count == 0
    assert report.timestamp_parse_failures == 0
    assert report.observer_projection_failures == 0
    assert report.outer_schema_failure_code is None


def test_offline_acceptance_distinguishes_record_and_outer_schema_failures() -> None:
    profile = _profile()
    fixture = build_sanitized_live_fixture_v0221(
        live_response=_live_response(),
        profile=profile,
        private_sample_shape_sha256="2" * 64,
        started_at=datetime(2026, 8, 28, 12, 0, tzinfo=UTC),
        ended_at=datetime(2026, 8, 28, 12, 5, tzinfo=UTC),
        service_aliases={"checkoutservice": "checkout"},
    )
    record_invalid = fixture.model_copy(deep=True)
    record_invalid.response["hits"]["hits"][0]["_source"].pop("body")  # type: ignore[index,union-attr]
    record_invalid = record_invalid.rebind()
    record_report = evaluate_offline_parser_v0221(
        fixture=record_invalid,
        profile=profile,
    )

    outer_invalid = fixture.model_copy(deep=True)
    outer_invalid.response.pop("hits")
    outer_invalid = outer_invalid.rebind()
    outer_report = evaluate_offline_parser_v0221(
        fixture=outer_invalid,
        profile=profile,
    )

    assert record_report.terminal == OFFLINE_PARSER_BLOCKED_V0221
    assert record_report.rejection_codes_by_count == {
        "OPENSEARCH_MESSAGE_FIELD_MISSING": 1
    }
    assert record_report.outer_schema_failure_code is None
    assert outer_report.terminal == OFFLINE_PARSER_BLOCKED_V0221
    assert outer_report.rejection_codes_by_count == {}
    assert outer_report.outer_schema_failure_code == (
        "OPENSEARCH_HITS_CONTAINER_INVALID"
    )
