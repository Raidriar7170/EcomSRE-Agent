from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime, timedelta
from typing import Callable

import pytest

from ecomsre.product.connectors.base import (
    ConnectorQueryContextV1,
    ConnectorWindowV1,
)
from ecomsre.product.connectors.opensearch_normalization_v022 import (
    OpenSearchSchemaExceptionV022,
    normalize_opensearch_search_v022,
)
from ecomsre.product.connectors.opensearch_schema_v022 import (
    OpenSearchBatchStatusV022,
    OpenSearchExtractionModeV022,
    OpenSearchExtractionRuleV022,
    OpenSearchMessageExtractionV022,
    OpenSearchMessageModeV022,
    OpenSearchNormalizationProfileV022,
    OpenSearchSchemaErrorCodeV022,
    OpenSearchSeverityExtractionV022,
    OpenSearchSeverityModeV022,
    OpenSearchTimestampExtractionV022,
    OpenSearchTimestampParserV022,
)


NOW = datetime(2026, 8, 28, 3, 0, tzinfo=UTC)


def _context(*, requested: tuple[str, ...] = ("checkout",)) -> ConnectorQueryContextV1:
    return ConnectorQueryContextV1(
        environment_id="env-" + "1" * 24,
        requested_services=requested,
        service_aliases={"checkoutservice": "checkout"},
        window=ConnectorWindowV1(
            started_at=NOW,
            ended_at=NOW + timedelta(minutes=1),
        ),
        maximum_records=10,
    )


def _profile(
    *,
    severity_mode: OpenSearchSeverityModeV022 = OpenSearchSeverityModeV022.STRING_VALUE,
    message_mode: OpenSearchMessageModeV022 = OpenSearchMessageModeV022.STRING_VALUE,
    message_projection_policy: str = "AS_OBSERVED",
    timestamp_parser: OpenSearchTimestampParserV022 = OpenSearchTimestampParserV022.ISO_8601,
) -> OpenSearchNormalizationProfileV022:
    return OpenSearchNormalizationProfileV022.build(
        profile_id="fixture-checkout-v022",
        index_pattern="otel-v1-apm-span-*",
        mapping_sha256="1" * 64,
        field_caps_sha256="2" * 64,
        sample_shape_sha256="3" * 64,
        timestamp_extraction=OpenSearchTimestampExtractionV022(
            extraction=OpenSearchExtractionRuleV022(
                mode=OpenSearchExtractionModeV022.DOTTED_OR_NESTED_PATH,
                paths=("observed.timestamp",),
            ),
            parsers=(timestamp_parser,),
        ),
        service_extraction=OpenSearchExtractionRuleV022(
            mode=OpenSearchExtractionModeV022.COALESCE_PATHS,
            paths=("resource.service.name", "serviceName"),
        ),
        service_source_field="resource.service.name",
        service_query_field="resource.service.name.keyword",
        severity_extraction=OpenSearchSeverityExtractionV022(
            extraction=OpenSearchExtractionRuleV022(
                mode=OpenSearchExtractionModeV022.DIRECT_KEY,
                paths=("severity",),
            ),
            mode=severity_mode,
        ),
        message_extraction=OpenSearchMessageExtractionV022(
            extraction=OpenSearchExtractionRuleV022(
                mode=(
                    OpenSearchExtractionModeV022.OTLP_VALUE_WRAPPER
                    if message_mode
                    is OpenSearchMessageModeV022.OTLP_STRING_VALUE_WRAPPER
                    else OpenSearchExtractionModeV022.DIRECT_KEY
                ),
                paths=("body",),
            ),
            mode=message_mode,
        ),
        trace_id_extraction=OpenSearchExtractionRuleV022(
            mode=OpenSearchExtractionModeV022.OPTIONAL,
            paths=("traceId",),
        ),
        message_projection_policy=message_projection_policy,
        maximum_record_rejection_fraction=0.25,
        profile_source="LIVE_SCHEMA_PROBE_V022",
    )


def _source() -> dict[str, object]:
    return {
        "observed": {"timestamp": (NOW + timedelta(seconds=5)).isoformat()},
        "resource": {"service": {"name": "checkoutservice"}},
        "severity": "INFO",
        "body": "checkout completed",
        "traceId": "a" * 32,
    }


def _payload(*sources: object) -> dict[str, object]:
    hits = [{"_source": source} for source in (sources or (_source(),))]
    return {"hits": {"total": {"value": len(hits)}, "hits": hits}}


@pytest.mark.parametrize(
    ("payload", "code"),
    [
        ([], OpenSearchSchemaErrorCodeV022.OPENSEARCH_SEARCH_RESPONSE_NOT_OBJECT),
        ({}, OpenSearchSchemaErrorCodeV022.OPENSEARCH_HITS_CONTAINER_INVALID),
        (
            {"hits": {"hits": {}}},
            OpenSearchSchemaErrorCodeV022.OPENSEARCH_HITS_LIST_INVALID,
        ),
    ],
)
def test_outer_parser_failures_are_stage_specific(
    payload: object,
    code: OpenSearchSchemaErrorCodeV022,
) -> None:
    with pytest.raises(OpenSearchSchemaExceptionV022) as raised:
        normalize_opensearch_search_v022(
            payload,
            profile=_profile(),
            context=_context(),
            latency_ms=1.0,
        )

    assert raised.value.failure.code is code


Mutation = Callable[[dict[str, object]], object]


def _remove(path: str) -> Mutation:
    def mutate(source: dict[str, object]) -> object:
        target: dict[str, object] = source
        parts = path.split(".")
        for part in parts[:-1]:
            target = target[part]  # type: ignore[assignment]
        target.pop(parts[-1])
        return source

    return mutate


def _set(path: str, value: object) -> Mutation:
    def mutate(source: dict[str, object]) -> object:
        target: dict[str, object] = source
        parts = path.split(".")
        for part in parts[:-1]:
            target = target[part]  # type: ignore[assignment]
        target[parts[-1]] = value
        return source

    return mutate


@pytest.mark.parametrize(
    ("mutation", "code"),
    [
        (
            lambda _source: "not-a-hit",
            OpenSearchSchemaErrorCodeV022.OPENSEARCH_HIT_NOT_OBJECT,
        ),
        (
            lambda _source: {"not_source": {}},
            OpenSearchSchemaErrorCodeV022.OPENSEARCH_SOURCE_MISSING,
        ),
        (
            lambda _source: {"_source": []},
            OpenSearchSchemaErrorCodeV022.OPENSEARCH_SOURCE_NOT_OBJECT,
        ),
        (
            _remove("observed.timestamp"),
            OpenSearchSchemaErrorCodeV022.OPENSEARCH_TIMESTAMP_FIELD_MISSING,
        ),
        (
            _set("observed.timestamp", []),
            OpenSearchSchemaErrorCodeV022.OPENSEARCH_TIMESTAMP_TYPE_INVALID,
        ),
        (
            _set("observed.timestamp", "not-a-time"),
            OpenSearchSchemaErrorCodeV022.OPENSEARCH_TIMESTAMP_FORMAT_UNSUPPORTED,
        ),
        (
            _set("observed.timestamp", (NOW - timedelta(minutes=1)).isoformat()),
            OpenSearchSchemaErrorCodeV022.OPENSEARCH_TIMESTAMP_OUT_OF_WINDOW,
        ),
        (
            _remove("resource.service.name"),
            OpenSearchSchemaErrorCodeV022.OPENSEARCH_SERVICE_FIELD_MISSING,
        ),
        (
            _set("resource.service.name", []),
            OpenSearchSchemaErrorCodeV022.OPENSEARCH_SERVICE_TYPE_INVALID,
        ),
        (
            _set("resource.service.name", "unknownservice"),
            OpenSearchSchemaErrorCodeV022.OPENSEARCH_SERVICE_ALIAS_UNMAPPED,
        ),
        (
            _remove("severity"),
            OpenSearchSchemaErrorCodeV022.OPENSEARCH_SEVERITY_FIELD_MISSING,
        ),
        (
            _set("severity", []),
            OpenSearchSchemaErrorCodeV022.OPENSEARCH_SEVERITY_TYPE_INVALID,
        ),
        (
            _set("severity", "ALIEN"),
            OpenSearchSchemaErrorCodeV022.OPENSEARCH_SEVERITY_VALUE_UNSUPPORTED,
        ),
        (
            _remove("body"),
            OpenSearchSchemaErrorCodeV022.OPENSEARCH_MESSAGE_FIELD_MISSING,
        ),
        (
            _set("body", []),
            OpenSearchSchemaErrorCodeV022.OPENSEARCH_MESSAGE_TYPE_INVALID,
        ),
        (
            _set("traceId", []),
            OpenSearchSchemaErrorCodeV022.OPENSEARCH_TRACE_ID_TYPE_INVALID,
        ),
        (
            _set("traceId", "xyz"),
            OpenSearchSchemaErrorCodeV022.OPENSEARCH_TRACE_ID_VALUE_INVALID,
        ),
    ],
)
def test_each_malformed_hit_becomes_one_typed_rejection(
    mutation: Mutation,
    code: OpenSearchSchemaErrorCodeV022,
) -> None:
    source = mutation(deepcopy(_source()))
    payload = (
        {"hits": {"total": {"value": 1}, "hits": [source]}}
        if code
        in {
            OpenSearchSchemaErrorCodeV022.OPENSEARCH_HIT_NOT_OBJECT,
            OpenSearchSchemaErrorCodeV022.OPENSEARCH_SOURCE_MISSING,
            OpenSearchSchemaErrorCodeV022.OPENSEARCH_SOURCE_NOT_OBJECT,
        }
        else _payload(source)
    )
    batch = normalize_opensearch_search_v022(
        payload,
        profile=_profile(),
        context=_context(),
        latency_ms=1.0,
    )

    assert batch.status is OpenSearchBatchStatusV022.FAILURE_SCHEMA
    assert tuple(item.failure.code for item in batch.rejections) == (code,)


def test_unrequested_mapped_service_is_distinct_from_unmapped_alias() -> None:
    source = _source()
    source["resource"] = {"service": {"name": "checkoutservice"}}
    context = _context(requested=("payment",))
    context = context.model_copy(
        update={"service_aliases": {"checkoutservice": "checkout"}}
    )

    batch = normalize_opensearch_search_v022(
        _payload(source),
        profile=_profile(),
        context=context,
        latency_ms=1.0,
    )

    assert batch.rejections[0].failure.code is (
        OpenSearchSchemaErrorCodeV022.OPENSEARCH_SERVICE_NOT_REQUESTED
    )


def test_otlp_wrapper_and_epoch_timestamp_are_profile_driven() -> None:
    source = _source()
    source["observed"] = {"timestamp": int((NOW + timedelta(seconds=5)).timestamp() * 1000)}
    source["body"] = {"stringValue": "checkout completed"}
    batch = normalize_opensearch_search_v022(
        _payload(source),
        profile=_profile(
            message_mode=OpenSearchMessageModeV022.OTLP_STRING_VALUE_WRAPPER,
            timestamp_parser=OpenSearchTimestampParserV022.EPOCH_MILLIS,
        ),
        context=_context(),
        latency_ms=1.0,
    )

    assert batch.status is OpenSearchBatchStatusV022.SUCCESS_NONEMPTY
    assert batch.normalizations[0].record.message == "checkout completed"


def test_invalid_otlp_wrapper_and_observer_projection_have_exact_codes() -> None:
    wrapped = _source()
    wrapped["body"] = {"wrong": "checkout completed"}
    wrapper_batch = normalize_opensearch_search_v022(
        _payload(wrapped),
        profile=_profile(
            message_mode=OpenSearchMessageModeV022.OTLP_STRING_VALUE_WRAPPER
        ),
        context=_context(),
        latency_ms=1.0,
    )
    projected = _source()
    projected["body"] = "feature flag leak remains"
    projection_batch = normalize_opensearch_search_v022(
        _payload(projected),
        profile=_profile(message_projection_policy="OBSERVER_SYMPTOM_V1"),
        context=_context(),
        latency_ms=1.0,
    )

    assert wrapper_batch.rejections[0].failure.code is (
        OpenSearchSchemaErrorCodeV022.OPENSEARCH_MESSAGE_WRAPPER_INVALID
    )
    assert projection_batch.rejections[0].failure.code is (
        OpenSearchSchemaErrorCodeV022.OPENSEARCH_OBSERVER_PROJECTION_REJECTED
    )


def test_one_malformed_hit_does_not_destroy_valid_sibling() -> None:
    invalid = _source()
    invalid.pop("body")
    batch = normalize_opensearch_search_v022(
        _payload(_source(), invalid, _source(), _source(), _source()),
        profile=_profile(),
        context=_context(),
        latency_ms=1.0,
    )

    assert batch.status is OpenSearchBatchStatusV022.SUCCESS_NONEMPTY
    assert batch.accepted_record_count == 4
    assert batch.rejected_record_count == 1
    assert batch.rejection_fraction == 0.2
