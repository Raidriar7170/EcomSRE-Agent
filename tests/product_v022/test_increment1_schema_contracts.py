from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from ecomsre.dta_v2.v22.read_contracts import LogRecordV22
from ecomsre.product.connectors.base import ConnectorWindowV1
from ecomsre.product.connectors.opensearch_schema_v022 import (
    OPENSEARCH_ERROR_STAGE_V022,
    OpenSearchBatchStatusV022,
    OpenSearchExtractionModeV022,
    OpenSearchExtractionRuleV022,
    OpenSearchMessageExtractionV022,
    OpenSearchMessageModeV022,
    OpenSearchNormalizationProfileV022,
    OpenSearchRecordNormalizationV022,
    OpenSearchRecordRejectionV022,
    OpenSearchSchemaErrorCodeV022,
    OpenSearchSchemaFailureV022,
    OpenSearchSchemaStageV022,
    OpenSearchSeverityExtractionV022,
    OpenSearchSeverityModeV022,
    OpenSearchTimestampExtractionV022,
    OpenSearchTimestampParserV022,
    build_opensearch_batch_v022,
)


NOW = datetime(2026, 8, 28, 2, 0, tzinfo=UTC)
WINDOW = ConnectorWindowV1(started_at=NOW, ended_at=NOW + timedelta(minutes=1))


def _failure(
    code: OpenSearchSchemaErrorCodeV022,
    *,
    ordinal: int = 0,
) -> OpenSearchSchemaFailureV022:
    return OpenSearchSchemaFailureV022.build(
        code=code,
        field_path="body.stringValue",
        mapping_type="text",
        hit_ordinal=ordinal,
        window=WINDOW,
    )


def _profile() -> OpenSearchNormalizationProfileV022:
    return OpenSearchNormalizationProfileV022.build(
        profile_id="otel-checkout-v022",
        index_pattern="otel-v1-apm-span-*",
        mapping_sha256="1" * 64,
        field_caps_sha256="2" * 64,
        sample_shape_sha256="3" * 64,
        timestamp_extraction=OpenSearchTimestampExtractionV022(
            extraction=OpenSearchExtractionRuleV022(
                mode=OpenSearchExtractionModeV022.DIRECT_KEY,
                paths=("observedTimestamp",),
            ),
            parsers=(OpenSearchTimestampParserV022.ISO_8601,),
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
                paths=("severityText",),
            ),
            mode=OpenSearchSeverityModeV022.STRING_VALUE,
        ),
        message_extraction=OpenSearchMessageExtractionV022(
            extraction=OpenSearchExtractionRuleV022(
                mode=OpenSearchExtractionModeV022.OTLP_VALUE_WRAPPER,
                paths=("body",),
            ),
            mode=OpenSearchMessageModeV022.OTLP_STRING_VALUE_WRAPPER,
        ),
        trace_id_extraction=OpenSearchExtractionRuleV022(
            mode=OpenSearchExtractionModeV022.OPTIONAL,
            paths=("traceId",),
        ),
        message_projection_policy="OBSERVER_SYMPTOM_V1",
        maximum_record_rejection_fraction=0.25,
        profile_source="LIVE_SCHEMA_PROBE_V022",
    )


def test_every_safe_schema_code_has_one_typed_stage() -> None:
    assert set(OPENSEARCH_ERROR_STAGE_V022) == set(OpenSearchSchemaErrorCodeV022)
    assert all(isinstance(stage, OpenSearchSchemaStageV022) for stage in OPENSEARCH_ERROR_STAGE_V022.values())
    assert OPENSEARCH_ERROR_STAGE_V022[
        OpenSearchSchemaErrorCodeV022.OPENSEARCH_SOURCE_MISSING
    ] is OpenSearchSchemaStageV022.SOURCE
    assert OPENSEARCH_ERROR_STAGE_V022[
        OpenSearchSchemaErrorCodeV022.OPENSEARCH_TIMESTAMP_FORMAT_UNSUPPORTED
    ] is OpenSearchSchemaStageV022.TIMESTAMP
    assert OPENSEARCH_ERROR_STAGE_V022[
        OpenSearchSchemaErrorCodeV022.OPENSEARCH_OBSERVER_PROJECTION_REJECTED
    ] is OpenSearchSchemaStageV022.OBSERVER_PROJECTION


def test_schema_failure_derives_stage_and_binds_safe_diagnostics() -> None:
    failure = _failure(
        OpenSearchSchemaErrorCodeV022.OPENSEARCH_MESSAGE_WRAPPER_INVALID,
        ordinal=4,
    )

    assert failure.stage is OpenSearchSchemaStageV022.MESSAGE
    assert failure.hit_ordinal == 4
    assert failure.field_path == "body.stringValue"
    assert len(failure.result_sha256) == 64

    with pytest.raises(ValidationError, match="stage"):
        OpenSearchSchemaFailureV022.model_validate(
            {**failure.model_dump(mode="json"), "stage": "SERVICE"}
        )


@pytest.mark.parametrize(
    ("mode", "paths"),
    [
        (OpenSearchExtractionModeV022.DIRECT_KEY, ("a", "b")),
        (OpenSearchExtractionModeV022.COALESCE_PATHS, ("a",)),
        (OpenSearchExtractionModeV022.OTLP_VALUE_WRAPPER, ("a", "b")),
    ],
)
def test_extraction_rule_rejects_ambiguous_shapes(
    mode: OpenSearchExtractionModeV022,
    paths: tuple[str, ...],
) -> None:
    with pytest.raises(ValidationError, match="extraction"):
        OpenSearchExtractionRuleV022(mode=mode, paths=paths)


def test_profile_sha_is_stable_and_binds_query_field() -> None:
    first = _profile()
    second = _profile()

    assert first.profile_sha256 == second.profile_sha256
    assert first.service_source_field == "resource.service.name"
    assert first.service_query_field == "resource.service.name.keyword"

    changed = OpenSearchNormalizationProfileV022.build(
        **{
            **first.model_dump(mode="python", exclude={"profile_sha256"}),
            "service_query_field": "serviceName.keyword",
        }
    )
    assert changed.profile_sha256 != first.profile_sha256


def test_record_rejection_is_visible_without_raw_values() -> None:
    accepted = OpenSearchRecordNormalizationV022.build(
        hit_ordinal=0,
        record=LogRecordV22(
            schema_version="dta-v22.log-record.v1",
            observed_at=NOW + timedelta(seconds=5),
            service="checkout",
            severity="DIAGNOSTIC",
            message="checkout completed",
        ),
    )
    rejected = OpenSearchRecordRejectionV022.build(
        hit_ordinal=1,
        failure=_failure(
            OpenSearchSchemaErrorCodeV022.OPENSEARCH_SERVICE_ALIAS_UNMAPPED,
            ordinal=1,
        ),
    )

    batch = build_opensearch_batch_v022(
        total_hit_count=2,
        sampled_hit_count=2,
        normalizations=(accepted,),
        rejections=(rejected,),
        requested_services=("checkout",),
        maximum_record_rejection_fraction=0.25,
        truncated=False,
        latency_ms=4.0,
    )

    assert batch.status is OpenSearchBatchStatusV022.PARTIAL_SCHEMA
    assert batch.accepted_record_count == 1
    assert batch.rejected_record_count == 1
    assert batch.rejection_codes_by_count == {
        "OPENSEARCH_SERVICE_ALIAS_UNMAPPED": 1
    }
    assert batch.covered_services == ("checkout",)
    assert batch.missing_services == ()
    assert "raw" not in batch.model_dump(mode="json")


def test_empty_and_all_invalid_batch_semantics_are_distinct() -> None:
    empty = build_opensearch_batch_v022(
        total_hit_count=0,
        sampled_hit_count=0,
        normalizations=(),
        rejections=(),
        requested_services=("checkout",),
        maximum_record_rejection_fraction=0.25,
        truncated=False,
        latency_ms=1.0,
    )
    rejected = OpenSearchRecordRejectionV022.build(
        hit_ordinal=0,
        failure=_failure(
            OpenSearchSchemaErrorCodeV022.OPENSEARCH_SOURCE_MISSING
        ),
    )
    invalid = build_opensearch_batch_v022(
        total_hit_count=1,
        sampled_hit_count=1,
        normalizations=(),
        rejections=(rejected,),
        requested_services=("checkout",),
        maximum_record_rejection_fraction=0.25,
        truncated=False,
        latency_ms=1.0,
    )

    assert empty.status is OpenSearchBatchStatusV022.SUCCESS_EMPTY
    assert invalid.status is OpenSearchBatchStatusV022.FAILURE_SCHEMA
