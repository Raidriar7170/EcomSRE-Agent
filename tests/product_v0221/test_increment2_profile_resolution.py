from __future__ import annotations

from copy import deepcopy

import pytest

from ecomsre.product.connectors.opensearch_probe_resolution_v0221 import (
    OpenSearchEmpiricalQueryVerificationV0221,
    OpenSearchFieldCapsStatusV0221,
    OpenSearchProbeProtocolBlockerV0221,
    OpenSearchProfileResolutionModeV0221,
    build_empirical_query_verification_v0221,
    build_profile_verification_body_v0221,
    build_service_aggregation_body_v0221,
    build_timestamp_range_body_v0221,
    parse_field_mapping_v0221,
    resolve_normalization_profile_v0221,
)
from ecomsre.product.connectors.opensearch_probe_v022 import (
    parse_field_caps_v022,
    parse_mapping_v022,
    summarize_sample_shapes_v022,
)


def _mapping() -> dict[str, object]:
    return {
        "otel-v1-apm-span-0001": {
            "mappings": {
                "properties": {
                    "observed": {
                        "properties": {"timestamp": {"type": "date"}}
                    },
                    "resource": {
                        "properties": {
                            "service": {
                                "properties": {
                                    "name": {
                                        "type": "text",
                                        "fields": {
                                            "keyword": {"type": "keyword"}
                                        },
                                    }
                                }
                            }
                        }
                    },
                    "body": {
                        "properties": {"stringValue": {"type": "text"}}
                    },
                    "severityText": {"type": "keyword"},
                    "traceId": {"type": "keyword"},
                }
            }
        }
    }


def _field_caps() -> dict[str, object]:
    return {
        "fields": {
            "observed.timestamp": {
                "date": {"type": "date", "searchable": True, "aggregatable": True}
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
            "body.stringValue": {
                "text": {"type": "text", "searchable": True, "aggregatable": False}
            },
            "severityText": {
                "keyword": {
                    "type": "keyword",
                    "searchable": True,
                    "aggregatable": True,
                }
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


def _samples() -> tuple[dict[str, object], ...]:
    return (
        {
            "observed": {"timestamp": "2026-08-28T12:00:00+00:00"},
            "resource": {"service": {"name": "checkoutservice"}},
            "body": {"stringValue": "checkout completed"},
            "severityText": "INFO",
            "traceId": "a" * 32,
        },
        {
            "observed": {"timestamp": "2026-08-28T12:00:01+00:00"},
            "resource": {"service": {"name": "checkoutservice"}},
            "body": {"stringValue": "checkout completed"},
            "severityText": "INFO",
            "traceId": "b" * 32,
        },
    )


def _verification() -> OpenSearchEmpiricalQueryVerificationV0221:
    return OpenSearchEmpiricalQueryVerificationV0221.build(
        service_query_field="resource.service.name.keyword",
        timestamp_query_field="observed.timestamp",
        checkout_service_observed=True,
        terms_aggregation_succeeded=True,
        timestamp_range_query_succeeded=True,
        profile_verification_status="SUCCESS_NONEMPTY",
        verification_hit_count=2,
    )


def test_focused_field_mapping_and_empirical_query_evidence_are_typed() -> None:
    focused = parse_field_mapping_v0221(
        {
            "otel-v1-apm-span-0001": {
                "mappings": {
                    "resource.service.name.keyword": {
                        "full_name": "resource.service.name.keyword",
                        "mapping": {
                            "resource.service.name.keyword": {"type": "keyword"}
                        },
                    }
                }
            }
        }
    )
    verification = build_empirical_query_verification_v0221(
        service_query_field="resource.service.name.keyword",
        timestamp_query_field="observed.timestamp",
        checkout_aliases=("checkoutservice",),
        service_aggregation_response={
            "aggregations": {
                "services": {
                    "buckets": [{"key": "checkoutservice", "doc_count": 2}]
                }
            }
        },
        timestamp_range_response={
            "timed_out": False,
            "_shards": {"failed": 0},
            "hits": {"hits": []},
        },
        profile_verification_response={
            "timed_out": False,
            "_shards": {"failed": 0},
            "hits": {"hits": [{"_source": _samples()[0]}]},
        },
    )

    assert focused.fields["resource.service.name.keyword"].mapping_type == "keyword"
    assert verification.checkout_service_observed is True
    assert verification.profile_verification_status == "SUCCESS_NONEMPTY"
    assert build_service_aggregation_body_v0221(
        "resource.service.name.keyword"
    )["size"] == 0
    assert build_timestamp_range_body_v0221(
        "observed.timestamp",
        started_at="2026-08-28T12:00:00+00:00",
        ended_at="2026-08-28T12:01:00+00:00",
    )["size"] == 0
    assert build_profile_verification_body_v0221(
        service_query_field="resource.service.name.keyword",
        timestamp_query_field="observed.timestamp",
        checkout_aliases=("checkoutservice",),
        started_at="2026-08-28T12:00:00+00:00",
        ended_at="2026-08-28T12:01:00+00:00",
    )["size"] == 5


def test_field_caps_available_profile_requires_empirical_verification() -> None:
    samples = _samples()
    resolution = resolve_normalization_profile_v0221(
        index_pattern="otel-v1-apm-span-*",
        mapping=parse_mapping_v022(_mapping()),
        field_caps=parse_field_caps_v022(_field_caps()),
        samples=samples,
        sample_shapes=summarize_sample_shapes_v022(samples),
        checkout_aliases=("checkoutservice",),
        empirical_verification=_verification(),
    )

    assert resolution.profile.field_caps_status is (
        OpenSearchFieldCapsStatusV0221.AVAILABLE
    )
    assert resolution.profile.profile_resolution_mode is (
        OpenSearchProfileResolutionModeV0221.MAPPING_FIELD_CAPS_SAMPLE
    )
    assert resolution.profile.service_query_field == (
        "resource.service.name.keyword"
    )
    assert resolution.profile.empirical_query_verification_sha256 == (
        _verification().verification_sha256
    )
    assert resolution.terminal == "ECOMSRE_PRODUCT_V0221_PROFILE_VERIFIED"


def test_mapping_sample_empirical_fallback_produces_verified_profile() -> None:
    samples = _samples()
    resolution = resolve_normalization_profile_v0221(
        index_pattern="otel-v1-apm-span-*",
        mapping=parse_mapping_v022(_mapping()),
        field_caps=None,
        samples=samples,
        sample_shapes=summarize_sample_shapes_v022(samples),
        checkout_aliases=("checkoutservice",),
        empirical_verification=_verification(),
    )

    assert resolution.profile.field_caps_status is (
        OpenSearchFieldCapsStatusV0221.UNAVAILABLE_OPTIONAL
    )
    assert resolution.profile.field_caps_sha256 is None
    assert resolution.profile.profile_resolution_mode is (
        OpenSearchProfileResolutionModeV0221.MAPPING_SAMPLE_EMPIRICAL
    )
    assert resolution.profile.service_source_field == "resource.service.name"
    assert resolution.profile.service_query_field == (
        "resource.service.name.keyword"
    )


def test_empirically_selected_timestamp_resolves_a_mapping_sample_tie() -> None:
    mapping_payload = deepcopy(_mapping())
    properties = mapping_payload["otel-v1-apm-span-0001"]["mappings"]["properties"]  # type: ignore[index]
    properties["recorded"] = {"properties": {"timestamp": {"type": "date"}}}  # type: ignore[index]
    samples = list(_samples())
    for sample in samples:
        sample["recorded"] = deepcopy(sample["observed"])

    resolution = resolve_normalization_profile_v0221(
        index_pattern="otel-v1-apm-span-*",
        mapping=parse_mapping_v022(mapping_payload),
        field_caps=None,
        samples=tuple(samples),
        sample_shapes=summarize_sample_shapes_v022(tuple(samples)),
        checkout_aliases=("checkoutservice",),
        empirical_verification=_verification(),
    )

    assert resolution.profile.timestamp_extraction.extraction.paths == (
        "observed.timestamp",
    )
    assert "timestamp:EMPIRICAL_RANGE_QUERY" in resolution.tie_breaks


def test_required_message_tie_blocks_instead_of_lexical_tie_break() -> None:
    mapping_payload = deepcopy(_mapping())
    properties = mapping_payload["otel-v1-apm-span-0001"]["mappings"]["properties"]  # type: ignore[index]
    properties["event"] = {  # type: ignore[index]
        "properties": {"stringValue": {"type": "text"}}
    }
    samples = list(_samples())
    for sample in samples:
        sample["event"] = deepcopy(sample["body"])

    with pytest.raises(OpenSearchProbeProtocolBlockerV0221) as raised:
        resolve_normalization_profile_v0221(
            index_pattern="otel-v1-apm-span-*",
            mapping=parse_mapping_v022(mapping_payload),
            field_caps=None,
            samples=tuple(samples),
            sample_shapes=summarize_sample_shapes_v022(tuple(samples)),
            checkout_aliases=("checkoutservice",),
            empirical_verification=_verification(),
        )

    assert raised.value.terminal == (
        "BLOCKED_ECOMSRE_PRODUCT_V0221_SCHEMA_AMBIGUOUS"
    )


def test_fallback_does_not_guess_unmapped_keyword_field() -> None:
    mapping_payload = deepcopy(_mapping())
    name = mapping_payload["otel-v1-apm-span-0001"]["mappings"]["properties"]["resource"]["properties"]["service"]["properties"]["name"]  # type: ignore[index]
    name.pop("fields")  # type: ignore[union-attr]
    samples = _samples()

    with pytest.raises(OpenSearchProbeProtocolBlockerV0221) as raised:
        resolve_normalization_profile_v0221(
            index_pattern="otel-v1-apm-span-*",
            mapping=parse_mapping_v022(mapping_payload),
            field_caps=None,
            samples=samples,
            sample_shapes=summarize_sample_shapes_v022(samples),
            checkout_aliases=("checkoutservice",),
            empirical_verification=_verification(),
        )

    assert raised.value.terminal == (
        "BLOCKED_ECOMSRE_PRODUCT_V0221_SCHEMA_AMBIGUOUS"
    )
