from __future__ import annotations

import pytest

from ecomsre.product.connectors.opensearch_normalization_v022 import (
    OpenSearchSchemaExceptionV022,
)
from ecomsre.product.connectors.opensearch_probe_v022 import (
    build_public_schema_fingerprint_v022,
    parse_field_caps_v022,
    parse_mapping_v022,
    resolve_normalization_profile_v022,
    summarize_sample_shapes_v022,
)
from ecomsre.product.connectors.opensearch_schema_v022 import (
    OpenSearchMessageModeV022,
    OpenSearchSchemaErrorCodeV022,
    OpenSearchTimestampParserV022,
)


MAPPING = {
    "otel-v1-apm-span-2026.08.28": {
        "mappings": {
            "properties": {
                "observedTimestamp": {"type": "date_nanos"},
                "resource": {
                    "properties": {
                        "service": {
                            "properties": {
                                "name": {
                                    "type": "text",
                                    "fields": {"keyword": {"type": "keyword"}},
                                }
                            }
                        }
                    }
                },
                "severity": {
                    "properties": {"text": {"type": "keyword"}}
                },
                "body": {
                    "properties": {"stringValue": {"type": "text"}}
                },
                "traceId": {"type": "keyword"},
            }
        }
    }
}

FIELD_CAPS = {
    "fields": {
        "observedTimestamp": {
            "date_nanos": {
                "type": "date_nanos",
                "searchable": True,
                "aggregatable": True,
            }
        },
        "resource.service.name": {
            "text": {"type": "text", "searchable": True, "aggregatable": False}
        },
        "resource.service.name.keyword": {
            "keyword": {
                "type": "keyword",
                "searchable": True,
                "aggregatable": True,
            }
        },
        "severity.text": {
            "keyword": {
                "type": "keyword",
                "searchable": True,
                "aggregatable": True,
            }
        },
        "body.stringValue": {
            "text": {"type": "text", "searchable": True, "aggregatable": False}
        },
        "traceId": {
            "keyword": {
                "type": "keyword",
                "searchable": True,
                "aggregatable": True,
            }
        },
    }
}

SAMPLES = (
    {
        "observedTimestamp": "2026-08-28T03:00:05Z",
        "resource": {"service": {"name": "checkoutservice"}},
        "severity": {"text": "INFO"},
        "body": {"stringValue": "checkout completed for redacted-user"},
        "traceId": "a" * 32,
    },
    {
        "observedTimestamp": "2026-08-28T03:00:06Z",
        "resource.service.name": "checkoutservice",
        "severity.text": "WARN",
        "body": {"stringValue": "bounded raw message must not be public"},
        "traceId": "b" * 32,
    },
)


def test_mapping_and_field_caps_are_flattened_without_values() -> None:
    mapping = parse_mapping_v022(MAPPING)
    caps = parse_field_caps_v022(FIELD_CAPS)

    assert mapping.index_names == ("otel-v1-apm-span-2026.08.28",)
    assert mapping.fields["resource.service.name"].mapping_type == "text"
    assert mapping.fields["resource.service.name.keyword"].mapping_type == "keyword"
    assert caps.fields["resource.service.name.keyword"].aggregatable is True
    assert caps.fields["body.stringValue"].searchable is True


@pytest.mark.parametrize(
    ("parser", "payload", "code"),
    [
        (
            parse_mapping_v022,
            [],
            OpenSearchSchemaErrorCodeV022.OPENSEARCH_MAPPING_RESPONSE_INVALID,
        ),
        (
            parse_field_caps_v022,
            {"fields": []},
            OpenSearchSchemaErrorCodeV022.OPENSEARCH_FIELD_CAPS_RESPONSE_INVALID,
        ),
    ],
)
def test_probe_response_errors_are_typed(parser, payload, code) -> None:
    with pytest.raises(OpenSearchSchemaExceptionV022) as raised:
        parser(payload)
    assert raised.value.failure.code is code


def test_sample_shapes_exclude_raw_messages_and_bind_digest() -> None:
    summary = summarize_sample_shapes_v022(SAMPLES)
    public = summary.model_dump_json()

    assert summary.sample_count == 2
    assert summary.field_presence["body.stringValue"] == 2
    assert summary.field_types["body.stringValue"] == ("string",)
    assert "checkout completed" not in public
    assert "bounded raw message" not in public
    assert len(summary.sample_shape_sha256) == 64


def test_profile_resolution_binds_source_query_asymmetry_and_wrapper() -> None:
    mapping = parse_mapping_v022(MAPPING)
    caps = parse_field_caps_v022(FIELD_CAPS)
    shapes = summarize_sample_shapes_v022(SAMPLES)
    resolved = resolve_normalization_profile_v022(
        index_pattern="otel-v1-apm-span-*",
        mapping=mapping,
        field_caps=caps,
        samples=SAMPLES,
        sample_shapes=shapes,
        checkout_aliases=("checkout", "checkoutservice"),
    )

    profile = resolved.profile
    assert profile.timestamp_extraction.parsers == (
        OpenSearchTimestampParserV022.ISO_8601,
    )
    assert profile.service_source_field == "resource.service.name"
    assert profile.service_query_field == "resource.service.name.keyword"
    assert profile.message_extraction.mode is (
        OpenSearchMessageModeV022.OTLP_STRING_VALUE_WRAPPER
    )
    assert resolved.candidate_rankings["service"][0].sample_coverage == 2


def test_public_fingerprint_is_redacted_and_digest_bound() -> None:
    mapping = parse_mapping_v022(MAPPING)
    caps = parse_field_caps_v022(FIELD_CAPS)
    shapes = summarize_sample_shapes_v022(SAMPLES)
    resolved = resolve_normalization_profile_v022(
        index_pattern="otel-v1-apm-span-*",
        mapping=mapping,
        field_caps=caps,
        samples=SAMPLES,
        sample_shapes=shapes,
        checkout_aliases=("checkout", "checkoutservice"),
    )
    fingerprint = build_public_schema_fingerprint_v022(
        mapping=mapping,
        field_caps=caps,
        sample_shapes=shapes,
        resolution=resolved,
        private_capture_sha256="4" * 64,
        request_count=4,
    )
    public = fingerprint.model_dump_json()

    assert fingerprint.request_count == 4
    assert fingerprint.sample_count == 2
    assert fingerprint.terminal == "ECOMSRE_PRODUCT_V022_SCHEMA_DISCOVERY_PASS"
    assert "checkout completed" not in public
    assert "bounded raw message" not in public
    assert len(fingerprint.fingerprint_sha256) == 64
