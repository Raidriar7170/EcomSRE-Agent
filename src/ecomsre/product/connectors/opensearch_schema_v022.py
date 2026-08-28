"""Typed OpenSearch schema and normalization contracts for Product v0.2.2."""

from __future__ import annotations

from collections import Counter
from enum import Enum
import re
from typing import Any, Literal

from pydantic import Field, model_validator

from ecomsre.dta_v2.v22.read_contracts import LogRecordV22, semantic_sha256_v22
from ecomsre.product.connectors.base import ConnectorWindowV1
from ecomsre.product.contracts import ProductModelV1


class OpenSearchSchemaStageV022(str, Enum):
    MAPPING_RESPONSE = "MAPPING_RESPONSE"
    FIELD_CAPS_RESPONSE = "FIELD_CAPS_RESPONSE"
    SEARCH_RESPONSE = "SEARCH_RESPONSE"
    HITS_CONTAINER = "HITS_CONTAINER"
    HITS_LIST = "HITS_LIST"
    HIT = "HIT"
    SOURCE = "SOURCE"
    TIMESTAMP = "TIMESTAMP"
    SERVICE = "SERVICE"
    SEVERITY = "SEVERITY"
    MESSAGE = "MESSAGE"
    TRACE_ID = "TRACE_ID"
    OBSERVER_PROJECTION = "OBSERVER_PROJECTION"
    PROFILE = "PROFILE"


class OpenSearchSchemaErrorCodeV022(str, Enum):
    OPENSEARCH_MAPPING_RESPONSE_INVALID = "OPENSEARCH_MAPPING_RESPONSE_INVALID"
    OPENSEARCH_FIELD_CAPS_RESPONSE_INVALID = (
        "OPENSEARCH_FIELD_CAPS_RESPONSE_INVALID"
    )
    OPENSEARCH_SEARCH_RESPONSE_NOT_OBJECT = "OPENSEARCH_SEARCH_RESPONSE_NOT_OBJECT"
    OPENSEARCH_HITS_CONTAINER_INVALID = "OPENSEARCH_HITS_CONTAINER_INVALID"
    OPENSEARCH_HITS_LIST_INVALID = "OPENSEARCH_HITS_LIST_INVALID"
    OPENSEARCH_HIT_NOT_OBJECT = "OPENSEARCH_HIT_NOT_OBJECT"
    OPENSEARCH_SOURCE_MISSING = "OPENSEARCH_SOURCE_MISSING"
    OPENSEARCH_SOURCE_NOT_OBJECT = "OPENSEARCH_SOURCE_NOT_OBJECT"
    OPENSEARCH_TIMESTAMP_FIELD_MISSING = "OPENSEARCH_TIMESTAMP_FIELD_MISSING"
    OPENSEARCH_TIMESTAMP_TYPE_INVALID = "OPENSEARCH_TIMESTAMP_TYPE_INVALID"
    OPENSEARCH_TIMESTAMP_FORMAT_UNSUPPORTED = (
        "OPENSEARCH_TIMESTAMP_FORMAT_UNSUPPORTED"
    )
    OPENSEARCH_TIMESTAMP_OUT_OF_WINDOW = "OPENSEARCH_TIMESTAMP_OUT_OF_WINDOW"
    OPENSEARCH_SERVICE_FIELD_MISSING = "OPENSEARCH_SERVICE_FIELD_MISSING"
    OPENSEARCH_SERVICE_TYPE_INVALID = "OPENSEARCH_SERVICE_TYPE_INVALID"
    OPENSEARCH_SERVICE_ALIAS_UNMAPPED = "OPENSEARCH_SERVICE_ALIAS_UNMAPPED"
    OPENSEARCH_SERVICE_NOT_REQUESTED = "OPENSEARCH_SERVICE_NOT_REQUESTED"
    OPENSEARCH_SEVERITY_FIELD_MISSING = "OPENSEARCH_SEVERITY_FIELD_MISSING"
    OPENSEARCH_SEVERITY_TYPE_INVALID = "OPENSEARCH_SEVERITY_TYPE_INVALID"
    OPENSEARCH_SEVERITY_VALUE_UNSUPPORTED = (
        "OPENSEARCH_SEVERITY_VALUE_UNSUPPORTED"
    )
    OPENSEARCH_MESSAGE_FIELD_MISSING = "OPENSEARCH_MESSAGE_FIELD_MISSING"
    OPENSEARCH_MESSAGE_TYPE_INVALID = "OPENSEARCH_MESSAGE_TYPE_INVALID"
    OPENSEARCH_MESSAGE_WRAPPER_INVALID = "OPENSEARCH_MESSAGE_WRAPPER_INVALID"
    OPENSEARCH_OBSERVER_PROJECTION_REJECTED = (
        "OPENSEARCH_OBSERVER_PROJECTION_REJECTED"
    )
    OPENSEARCH_TRACE_ID_TYPE_INVALID = "OPENSEARCH_TRACE_ID_TYPE_INVALID"
    OPENSEARCH_TRACE_ID_VALUE_INVALID = "OPENSEARCH_TRACE_ID_VALUE_INVALID"
    OPENSEARCH_REQUIRED_FIELD_NOT_DISCOVERED = (
        "OPENSEARCH_REQUIRED_FIELD_NOT_DISCOVERED"
    )
    OPENSEARCH_QUERY_FIELD_NOT_SEARCHABLE = (
        "OPENSEARCH_QUERY_FIELD_NOT_SEARCHABLE"
    )
    OPENSEARCH_QUERY_FIELD_NOT_AGGREGATABLE = (
        "OPENSEARCH_QUERY_FIELD_NOT_AGGREGATABLE"
    )
    OPENSEARCH_NORMALIZATION_PROFILE_AMBIGUOUS = (
        "OPENSEARCH_NORMALIZATION_PROFILE_AMBIGUOUS"
    )
    OPENSEARCH_NORMALIZATION_PROFILE_INCONSISTENT = (
        "OPENSEARCH_NORMALIZATION_PROFILE_INCONSISTENT"
    )


OPENSEARCH_ERROR_STAGE_V022: dict[
    OpenSearchSchemaErrorCodeV022, OpenSearchSchemaStageV022
] = {
    OpenSearchSchemaErrorCodeV022.OPENSEARCH_MAPPING_RESPONSE_INVALID: (
        OpenSearchSchemaStageV022.MAPPING_RESPONSE
    ),
    OpenSearchSchemaErrorCodeV022.OPENSEARCH_FIELD_CAPS_RESPONSE_INVALID: (
        OpenSearchSchemaStageV022.FIELD_CAPS_RESPONSE
    ),
    OpenSearchSchemaErrorCodeV022.OPENSEARCH_SEARCH_RESPONSE_NOT_OBJECT: (
        OpenSearchSchemaStageV022.SEARCH_RESPONSE
    ),
    OpenSearchSchemaErrorCodeV022.OPENSEARCH_HITS_CONTAINER_INVALID: (
        OpenSearchSchemaStageV022.HITS_CONTAINER
    ),
    OpenSearchSchemaErrorCodeV022.OPENSEARCH_HITS_LIST_INVALID: (
        OpenSearchSchemaStageV022.HITS_LIST
    ),
    OpenSearchSchemaErrorCodeV022.OPENSEARCH_HIT_NOT_OBJECT: (
        OpenSearchSchemaStageV022.HIT
    ),
    OpenSearchSchemaErrorCodeV022.OPENSEARCH_SOURCE_MISSING: (
        OpenSearchSchemaStageV022.SOURCE
    ),
    OpenSearchSchemaErrorCodeV022.OPENSEARCH_SOURCE_NOT_OBJECT: (
        OpenSearchSchemaStageV022.SOURCE
    ),
    OpenSearchSchemaErrorCodeV022.OPENSEARCH_TIMESTAMP_FIELD_MISSING: (
        OpenSearchSchemaStageV022.TIMESTAMP
    ),
    OpenSearchSchemaErrorCodeV022.OPENSEARCH_TIMESTAMP_TYPE_INVALID: (
        OpenSearchSchemaStageV022.TIMESTAMP
    ),
    OpenSearchSchemaErrorCodeV022.OPENSEARCH_TIMESTAMP_FORMAT_UNSUPPORTED: (
        OpenSearchSchemaStageV022.TIMESTAMP
    ),
    OpenSearchSchemaErrorCodeV022.OPENSEARCH_TIMESTAMP_OUT_OF_WINDOW: (
        OpenSearchSchemaStageV022.TIMESTAMP
    ),
    OpenSearchSchemaErrorCodeV022.OPENSEARCH_SERVICE_FIELD_MISSING: (
        OpenSearchSchemaStageV022.SERVICE
    ),
    OpenSearchSchemaErrorCodeV022.OPENSEARCH_SERVICE_TYPE_INVALID: (
        OpenSearchSchemaStageV022.SERVICE
    ),
    OpenSearchSchemaErrorCodeV022.OPENSEARCH_SERVICE_ALIAS_UNMAPPED: (
        OpenSearchSchemaStageV022.SERVICE
    ),
    OpenSearchSchemaErrorCodeV022.OPENSEARCH_SERVICE_NOT_REQUESTED: (
        OpenSearchSchemaStageV022.SERVICE
    ),
    OpenSearchSchemaErrorCodeV022.OPENSEARCH_SEVERITY_FIELD_MISSING: (
        OpenSearchSchemaStageV022.SEVERITY
    ),
    OpenSearchSchemaErrorCodeV022.OPENSEARCH_SEVERITY_TYPE_INVALID: (
        OpenSearchSchemaStageV022.SEVERITY
    ),
    OpenSearchSchemaErrorCodeV022.OPENSEARCH_SEVERITY_VALUE_UNSUPPORTED: (
        OpenSearchSchemaStageV022.SEVERITY
    ),
    OpenSearchSchemaErrorCodeV022.OPENSEARCH_MESSAGE_FIELD_MISSING: (
        OpenSearchSchemaStageV022.MESSAGE
    ),
    OpenSearchSchemaErrorCodeV022.OPENSEARCH_MESSAGE_TYPE_INVALID: (
        OpenSearchSchemaStageV022.MESSAGE
    ),
    OpenSearchSchemaErrorCodeV022.OPENSEARCH_MESSAGE_WRAPPER_INVALID: (
        OpenSearchSchemaStageV022.MESSAGE
    ),
    OpenSearchSchemaErrorCodeV022.OPENSEARCH_OBSERVER_PROJECTION_REJECTED: (
        OpenSearchSchemaStageV022.OBSERVER_PROJECTION
    ),
    OpenSearchSchemaErrorCodeV022.OPENSEARCH_TRACE_ID_TYPE_INVALID: (
        OpenSearchSchemaStageV022.TRACE_ID
    ),
    OpenSearchSchemaErrorCodeV022.OPENSEARCH_TRACE_ID_VALUE_INVALID: (
        OpenSearchSchemaStageV022.TRACE_ID
    ),
    OpenSearchSchemaErrorCodeV022.OPENSEARCH_REQUIRED_FIELD_NOT_DISCOVERED: (
        OpenSearchSchemaStageV022.PROFILE
    ),
    OpenSearchSchemaErrorCodeV022.OPENSEARCH_QUERY_FIELD_NOT_SEARCHABLE: (
        OpenSearchSchemaStageV022.PROFILE
    ),
    OpenSearchSchemaErrorCodeV022.OPENSEARCH_QUERY_FIELD_NOT_AGGREGATABLE: (
        OpenSearchSchemaStageV022.PROFILE
    ),
    OpenSearchSchemaErrorCodeV022.OPENSEARCH_NORMALIZATION_PROFILE_AMBIGUOUS: (
        OpenSearchSchemaStageV022.PROFILE
    ),
    OpenSearchSchemaErrorCodeV022.OPENSEARCH_NORMALIZATION_PROFILE_INCONSISTENT: (
        OpenSearchSchemaStageV022.PROFILE
    ),
}


class OpenSearchSchemaFailureV022(ProductModelV1):
    schema_version: Literal["ecomsre.product.opensearch-schema-failure.v022"] = (
        "ecomsre.product.opensearch-schema-failure.v022"
    )
    stage: OpenSearchSchemaStageV022
    code: OpenSearchSchemaErrorCodeV022
    field_path: str | None = Field(default=None, min_length=1, max_length=255)
    mapping_type: str | None = Field(default=None, min_length=1, max_length=80)
    hit_ordinal: int | None = Field(default=None, ge=0, le=200)
    window: ConnectorWindowV1 | None = None
    result_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @classmethod
    def build(
        cls,
        *,
        code: OpenSearchSchemaErrorCodeV022,
        field_path: str | None = None,
        mapping_type: str | None = None,
        hit_ordinal: int | None = None,
        window: ConnectorWindowV1 | None = None,
    ) -> "OpenSearchSchemaFailureV022":
        payload: dict[str, Any] = {
            "schema_version": "ecomsre.product.opensearch-schema-failure.v022",
            "stage": OPENSEARCH_ERROR_STAGE_V022[code],
            "code": code,
            "field_path": field_path,
            "mapping_type": mapping_type,
            "hit_ordinal": hit_ordinal,
            "window": window,
        }
        draft = cls.model_construct(**payload, result_sha256="0" * 64)
        return cls.model_validate(
            {
                **payload,
                "result_sha256": semantic_sha256_v22(
                    draft.model_dump(mode="json", exclude={"result_sha256"})
                ),
            }
        )

    @model_validator(mode="after")
    def require_bound_failure(self) -> "OpenSearchSchemaFailureV022":
        if self.stage is not OPENSEARCH_ERROR_STAGE_V022[self.code]:
            raise ValueError("OpenSearch schema failure stage differs from code")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"result_sha256"})
        )
        if self.result_sha256 != expected:
            raise ValueError("OpenSearch schema failure digest differs")
        return self


class OpenSearchExtractionModeV022(str, Enum):
    DIRECT_KEY = "DIRECT_KEY"
    DOTTED_OR_NESTED_PATH = "DOTTED_OR_NESTED_PATH"
    COALESCE_PATHS = "COALESCE_PATHS"
    OTLP_VALUE_WRAPPER = "OTLP_VALUE_WRAPPER"
    SCALAR_TO_TEXT = "SCALAR_TO_TEXT"
    OPTIONAL = "OPTIONAL"


_FIELD_PATH_V022 = re.compile(r"^[A-Za-z0-9_@-]+(?:\.[A-Za-z0-9_@-]+)*$")


class OpenSearchExtractionRuleV022(ProductModelV1):
    mode: OpenSearchExtractionModeV022
    paths: tuple[str, ...] = Field(min_length=1, max_length=8)

    @model_validator(mode="after")
    def require_bounded_extraction(self) -> "OpenSearchExtractionRuleV022":
        if len(set(self.paths)) != len(self.paths) or any(
            not _FIELD_PATH_V022.fullmatch(path) for path in self.paths
        ):
            raise ValueError("OpenSearch extraction paths are invalid")
        if self.mode is OpenSearchExtractionModeV022.COALESCE_PATHS:
            if len(self.paths) < 2:
                raise ValueError("OpenSearch coalesced extraction requires paths")
        elif len(self.paths) != 1:
            raise ValueError("OpenSearch extraction requires one path")
        return self


class OpenSearchTimestampParserV022(str, Enum):
    ISO_8601 = "ISO_8601"
    EPOCH_SECONDS = "EPOCH_SECONDS"
    EPOCH_MILLIS = "EPOCH_MILLIS"
    EPOCH_MICROS = "EPOCH_MICROS"
    EPOCH_NANOS = "EPOCH_NANOS"


class OpenSearchTimestampExtractionV022(ProductModelV1):
    extraction: OpenSearchExtractionRuleV022
    parsers: tuple[OpenSearchTimestampParserV022, ...] = Field(
        min_length=1,
        max_length=5,
    )

    @model_validator(mode="after")
    def require_unique_parsers(self) -> "OpenSearchTimestampExtractionV022":
        if len(set(self.parsers)) != len(self.parsers):
            raise ValueError("OpenSearch timestamp parsers are duplicated")
        return self


class OpenSearchSeverityModeV022(str, Enum):
    STRING_VALUE = "STRING_VALUE"
    INTEGER_OTEL_SEVERITY = "INTEGER_OTEL_SEVERITY"
    OPTIONAL_TO_DIAGNOSTIC = "OPTIONAL_TO_DIAGNOSTIC"


class OpenSearchSeverityExtractionV022(ProductModelV1):
    extraction: OpenSearchExtractionRuleV022
    mode: OpenSearchSeverityModeV022


class OpenSearchMessageModeV022(str, Enum):
    STRING_VALUE = "STRING_VALUE"
    OTLP_STRING_VALUE_WRAPPER = "OTLP_STRING_VALUE_WRAPPER"
    SCALAR_TO_TEXT = "SCALAR_TO_TEXT"


class OpenSearchMessageExtractionV022(ProductModelV1):
    extraction: OpenSearchExtractionRuleV022
    mode: OpenSearchMessageModeV022


class OpenSearchNormalizationProfileV022(ProductModelV1):
    schema_version: Literal[
        "ecomsre.product.opensearch-normalization-profile.v022"
    ] = "ecomsre.product.opensearch-normalization-profile.v022"
    profile_id: str = Field(pattern=r"^[a-z][a-z0-9-]{2,79}$")
    index_pattern: str = Field(min_length=1, max_length=255)
    mapping_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    field_caps_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    sample_shape_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    timestamp_extraction: OpenSearchTimestampExtractionV022
    service_extraction: OpenSearchExtractionRuleV022
    service_source_field: str = Field(min_length=1, max_length=255)
    service_query_field: str = Field(min_length=1, max_length=255)
    severity_extraction: OpenSearchSeverityExtractionV022
    message_extraction: OpenSearchMessageExtractionV022
    trace_id_extraction: OpenSearchExtractionRuleV022 | None = None
    message_projection_policy: Literal["AS_OBSERVED", "OBSERVER_SYMPTOM_V1"]
    maximum_record_rejection_fraction: float = Field(
        ge=0,
        le=0.25,
        allow_inf_nan=False,
    )
    profile_source: Literal["LIVE_SCHEMA_PROBE_V022"]
    profile_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @classmethod
    def build(cls, **values: Any) -> "OpenSearchNormalizationProfileV022":
        normalized = dict(values)
        normalized["timestamp_extraction"] = (
            OpenSearchTimestampExtractionV022.model_validate(
                normalized["timestamp_extraction"]
            )
        )
        normalized["service_extraction"] = OpenSearchExtractionRuleV022.model_validate(
            normalized["service_extraction"]
        )
        normalized["severity_extraction"] = (
            OpenSearchSeverityExtractionV022.model_validate(
                normalized["severity_extraction"]
            )
        )
        normalized["message_extraction"] = (
            OpenSearchMessageExtractionV022.model_validate(
                normalized["message_extraction"]
            )
        )
        if normalized.get("trace_id_extraction") is not None:
            normalized["trace_id_extraction"] = (
                OpenSearchExtractionRuleV022.model_validate(
                    normalized["trace_id_extraction"]
                )
            )
        payload = {
            "schema_version": (
                "ecomsre.product.opensearch-normalization-profile.v022"
            ),
            **normalized,
        }
        draft = cls.model_construct(**payload, profile_sha256="0" * 64)
        return cls.model_validate(
            {
                **payload,
                "profile_sha256": semantic_sha256_v22(
                    draft.model_dump(mode="json", exclude={"profile_sha256"})
                ),
            }
        )

    @model_validator(mode="after")
    def require_bound_profile(self) -> "OpenSearchNormalizationProfileV022":
        if self.service_source_field not in self.service_extraction.paths:
            raise ValueError("OpenSearch service source field differs from extraction")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"profile_sha256"})
        )
        if self.profile_sha256 != expected:
            raise ValueError("OpenSearch normalization profile digest differs")
        return self


class OpenSearchRecordNormalizationV022(ProductModelV1):
    schema_version: Literal[
        "ecomsre.product.opensearch-record-normalization.v022"
    ] = "ecomsre.product.opensearch-record-normalization.v022"
    hit_ordinal: int = Field(ge=0, le=200)
    record: LogRecordV22
    result_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @classmethod
    def build(
        cls,
        *,
        hit_ordinal: int,
        record: LogRecordV22,
    ) -> "OpenSearchRecordNormalizationV022":
        payload: dict[str, Any] = {
            "schema_version": (
                "ecomsre.product.opensearch-record-normalization.v022"
            ),
            "hit_ordinal": hit_ordinal,
            "record": record,
        }
        draft = cls.model_construct(**payload, result_sha256="0" * 64)
        return cls.model_validate(
            {
                **payload,
                "result_sha256": semantic_sha256_v22(
                    draft.model_dump(mode="json", exclude={"result_sha256"})
                ),
            }
        )

    @model_validator(mode="after")
    def require_bound_normalization(self) -> "OpenSearchRecordNormalizationV022":
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"result_sha256"})
        )
        if self.result_sha256 != expected:
            raise ValueError("OpenSearch record normalization digest differs")
        return self


class OpenSearchRecordRejectionV022(ProductModelV1):
    schema_version: Literal["ecomsre.product.opensearch-record-rejection.v022"] = (
        "ecomsre.product.opensearch-record-rejection.v022"
    )
    hit_ordinal: int = Field(ge=0, le=200)
    failure: OpenSearchSchemaFailureV022
    result_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @classmethod
    def build(
        cls,
        *,
        hit_ordinal: int,
        failure: OpenSearchSchemaFailureV022,
    ) -> "OpenSearchRecordRejectionV022":
        if failure.hit_ordinal != hit_ordinal:
            raise ValueError("OpenSearch rejection ordinal differs from failure")
        payload: dict[str, Any] = {
            "schema_version": "ecomsre.product.opensearch-record-rejection.v022",
            "hit_ordinal": hit_ordinal,
            "failure": failure,
        }
        draft = cls.model_construct(**payload, result_sha256="0" * 64)
        return cls.model_validate(
            {
                **payload,
                "result_sha256": semantic_sha256_v22(
                    draft.model_dump(mode="json", exclude={"result_sha256"})
                ),
            }
        )

    @model_validator(mode="after")
    def require_bound_rejection(self) -> "OpenSearchRecordRejectionV022":
        if self.failure.hit_ordinal != self.hit_ordinal:
            raise ValueError("OpenSearch rejection ordinal differs from failure")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"result_sha256"})
        )
        if self.result_sha256 != expected:
            raise ValueError("OpenSearch record rejection digest differs")
        return self


class OpenSearchBatchStatusV022(str, Enum):
    SUCCESS_EMPTY = "SUCCESS_EMPTY"
    SUCCESS_NONEMPTY = "SUCCESS_NONEMPTY"
    PARTIAL_SCHEMA = "PARTIAL_SCHEMA"
    FAILURE_SCHEMA = "FAILURE_SCHEMA"


class OpenSearchBatchNormalizationV022(ProductModelV1):
    schema_version: Literal["ecomsre.product.opensearch-batch.v022"] = (
        "ecomsre.product.opensearch-batch.v022"
    )
    status: OpenSearchBatchStatusV022
    total_hit_count: int = Field(ge=0)
    sampled_hit_count: int = Field(ge=0, le=200)
    accepted_record_count: int = Field(ge=0, le=200)
    rejected_record_count: int = Field(ge=0, le=200)
    rejection_fraction: float = Field(ge=0, le=1, allow_inf_nan=False)
    maximum_record_rejection_fraction: float = Field(
        ge=0,
        le=0.25,
        allow_inf_nan=False,
    )
    rejection_codes_by_count: dict[str, int]
    covered_services: tuple[str, ...]
    missing_services: tuple[str, ...]
    truncated: bool
    latency_ms: float = Field(ge=0, allow_inf_nan=False)
    normalizations: tuple[OpenSearchRecordNormalizationV022, ...]
    rejections: tuple[OpenSearchRecordRejectionV022, ...]
    batch_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_bound_batch(self) -> "OpenSearchBatchNormalizationV022":
        if self.sampled_hit_count != (
            self.accepted_record_count + self.rejected_record_count
        ):
            raise ValueError("OpenSearch sampled count differs")
        if self.accepted_record_count != len(self.normalizations):
            raise ValueError("OpenSearch accepted count differs")
        if self.rejected_record_count != len(self.rejections):
            raise ValueError("OpenSearch rejected count differs")
        expected_status = _batch_status_v022(
            total_hit_count=self.total_hit_count,
            accepted_record_count=self.accepted_record_count,
            rejected_record_count=self.rejected_record_count,
            rejection_fraction=self.rejection_fraction,
            maximum_record_rejection_fraction=(
                self.maximum_record_rejection_fraction
            ),
        )
        if self.status is not expected_status:
            raise ValueError("OpenSearch batch status differs")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"batch_sha256"})
        )
        if self.batch_sha256 != expected:
            raise ValueError("OpenSearch batch digest differs")
        return self


def _batch_status_v022(
    *,
    total_hit_count: int,
    accepted_record_count: int,
    rejected_record_count: int,
    rejection_fraction: float,
    maximum_record_rejection_fraction: float,
) -> OpenSearchBatchStatusV022:
    if total_hit_count == 0:
        return OpenSearchBatchStatusV022.SUCCESS_EMPTY
    if accepted_record_count == 0 and rejected_record_count > 0:
        return OpenSearchBatchStatusV022.FAILURE_SCHEMA
    if rejection_fraction > maximum_record_rejection_fraction:
        return OpenSearchBatchStatusV022.PARTIAL_SCHEMA
    return OpenSearchBatchStatusV022.SUCCESS_NONEMPTY


def build_opensearch_batch_v022(
    *,
    total_hit_count: int,
    sampled_hit_count: int,
    normalizations: tuple[OpenSearchRecordNormalizationV022, ...],
    rejections: tuple[OpenSearchRecordRejectionV022, ...],
    requested_services: tuple[str, ...],
    maximum_record_rejection_fraction: float,
    truncated: bool,
    latency_ms: float,
) -> OpenSearchBatchNormalizationV022:
    if sampled_hit_count != len(normalizations) + len(rejections):
        raise ValueError("OpenSearch sampled hit count differs")
    rejected = len(rejections)
    rejection_fraction = rejected / sampled_hit_count if sampled_hit_count else 0.0
    covered_services = tuple(
        sorted({item.record.service for item in normalizations})
    )
    missing_services = tuple(sorted(set(requested_services) - set(covered_services)))
    code_counts = Counter(item.failure.code.value for item in rejections)
    status = _batch_status_v022(
        total_hit_count=total_hit_count,
        accepted_record_count=len(normalizations),
        rejected_record_count=rejected,
        rejection_fraction=rejection_fraction,
        maximum_record_rejection_fraction=maximum_record_rejection_fraction,
    )
    payload: dict[str, Any] = {
        "schema_version": "ecomsre.product.opensearch-batch.v022",
        "status": status,
        "total_hit_count": total_hit_count,
        "sampled_hit_count": sampled_hit_count,
        "accepted_record_count": len(normalizations),
        "rejected_record_count": rejected,
        "rejection_fraction": rejection_fraction,
        "maximum_record_rejection_fraction": maximum_record_rejection_fraction,
        "rejection_codes_by_count": dict(sorted(code_counts.items())),
        "covered_services": covered_services,
        "missing_services": missing_services,
        "truncated": truncated,
        "latency_ms": latency_ms,
        "normalizations": normalizations,
        "rejections": rejections,
    }
    draft = OpenSearchBatchNormalizationV022.model_construct(
        **payload,
        batch_sha256="0" * 64,
    )
    return OpenSearchBatchNormalizationV022.model_validate(
        {
            **payload,
            "batch_sha256": semantic_sha256_v22(
                draft.model_dump(mode="json", exclude={"batch_sha256"})
            ),
        }
    )


__all__ = (
    "OPENSEARCH_ERROR_STAGE_V022",
    "OpenSearchBatchNormalizationV022",
    "OpenSearchBatchStatusV022",
    "OpenSearchExtractionModeV022",
    "OpenSearchExtractionRuleV022",
    "OpenSearchMessageExtractionV022",
    "OpenSearchMessageModeV022",
    "OpenSearchNormalizationProfileV022",
    "OpenSearchRecordNormalizationV022",
    "OpenSearchRecordRejectionV022",
    "OpenSearchSchemaErrorCodeV022",
    "OpenSearchSchemaFailureV022",
    "OpenSearchSchemaStageV022",
    "OpenSearchSeverityExtractionV022",
    "OpenSearchSeverityModeV022",
    "OpenSearchTimestampExtractionV022",
    "OpenSearchTimestampParserV022",
    "build_opensearch_batch_v022",
)
