"""Profile-driven OpenSearch response normalization for Product v0.2.2."""

from __future__ import annotations

from datetime import UTC, datetime
import re
from typing import Literal, Mapping, NoReturn

from ecomsre.dta_v2.v22.read_contracts import LogRecordV22
from ecomsre.product.connectors.base import ConnectorQueryContextV1
from ecomsre.product.connectors.opensearch_schema_v022 import (
    OpenSearchBatchNormalizationV022,
    OpenSearchExtractionModeV022,
    OpenSearchExtractionRuleV022,
    OpenSearchMessageModeV022,
    OpenSearchNormalizationProfileV022,
    OpenSearchRecordNormalizationV022,
    OpenSearchRecordRejectionV022,
    OpenSearchSchemaErrorCodeV022,
    OpenSearchSchemaFailureV022,
    OpenSearchSeverityModeV022,
    OpenSearchTimestampParserV022,
    build_opensearch_batch_v022,
)


_MISSING_V022 = object()
_FEATURE_CONTROL_CAUSE_V022 = re.compile(
    r"(?i)(?:feature\s*flag)\s+['\"][^'\"]{1,120}['\"]\s+"
    r"is\s+activated,\s*"
)
_OVERLOAD_SIMULATION_COUNT_V022 = re.compile(
    r"(?i)done\s+with\s+#\d+\s+messages\s+for\s+overload\s+simulation\.?"
)
_OBSERVER_TRUTH_REMAINDER_V022 = re.compile(
    r"(?i)feature\s*flag|#\d+\s+messages\s+for\s+overload\s+simulation|"
    r"['\"][a-z]+(?:[A-Z][A-Za-z0-9]*)+['\"]"
)


class OpenSearchSchemaExceptionV022(ValueError):
    """Typed fail-closed exception for one non-record schema boundary."""

    def __init__(self, failure: OpenSearchSchemaFailureV022) -> None:
        super().__init__(failure.code.value)
        self.failure = failure


def _raise_schema_v022(
    code: OpenSearchSchemaErrorCodeV022,
    *,
    context: ConnectorQueryContextV1,
    field_path: str | None = None,
    mapping_type: str | None = None,
    hit_ordinal: int | None = None,
) -> NoReturn:
    raise OpenSearchSchemaExceptionV022(
        OpenSearchSchemaFailureV022.build(
            code=code,
            field_path=field_path,
            mapping_type=mapping_type,
            hit_ordinal=hit_ordinal,
            window=context.window,
        )
    )


def _path_v022(
    source: Mapping[str, object],
    path: str,
    *,
    direct: bool,
) -> object:
    if path in source:
        return source[path]
    if direct:
        return _MISSING_V022
    current: object = source
    for segment in path.split("."):
        if not isinstance(current, Mapping) or segment not in current:
            return _MISSING_V022
        current = current[segment]
    return current


def _extract_v022(
    source: Mapping[str, object],
    rule: OpenSearchExtractionRuleV022,
) -> tuple[object, str]:
    if rule.mode is OpenSearchExtractionModeV022.DIRECT_KEY:
        return _path_v022(source, rule.paths[0], direct=True), rule.paths[0]
    if rule.mode is OpenSearchExtractionModeV022.COALESCE_PATHS:
        for path in rule.paths:
            value = _path_v022(source, path, direct=False)
            if value is not _MISSING_V022:
                return value, path
        return _MISSING_V022, ",".join(rule.paths)
    value = _path_v022(source, rule.paths[0], direct=False)
    return value, rule.paths[0]


def _timestamp_v022(
    source: Mapping[str, object],
    *,
    profile: OpenSearchNormalizationProfileV022,
    context: ConnectorQueryContextV1,
    hit_ordinal: int,
) -> datetime:
    value, field = _extract_v022(source, profile.timestamp_extraction.extraction)
    if value is _MISSING_V022:
        _raise_schema_v022(
            OpenSearchSchemaErrorCodeV022.OPENSEARCH_TIMESTAMP_FIELD_MISSING,
            context=context,
            field_path=field,
            hit_ordinal=hit_ordinal,
        )
    parsed: datetime | None = None
    type_compatible = False
    for parser in profile.timestamp_extraction.parsers:
        try:
            if parser is OpenSearchTimestampParserV022.ISO_8601:
                if not isinstance(value, str):
                    continue
                type_compatible = True
                candidate = datetime.fromisoformat(value.replace("Z", "+00:00"))
                if candidate.utcoffset() is None:
                    continue
                parsed = candidate.astimezone(UTC)
            else:
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    continue
                type_compatible = True
                divisor = {
                    OpenSearchTimestampParserV022.EPOCH_SECONDS: 1,
                    OpenSearchTimestampParserV022.EPOCH_MILLIS: 1_000,
                    OpenSearchTimestampParserV022.EPOCH_MICROS: 1_000_000,
                    OpenSearchTimestampParserV022.EPOCH_NANOS: 1_000_000_000,
                }[parser]
                parsed = datetime.fromtimestamp(float(value) / divisor, tz=UTC)
        except (OverflowError, OSError, ValueError):
            parsed = None
        if parsed is not None:
            break
    if parsed is None:
        _raise_schema_v022(
            (
                OpenSearchSchemaErrorCodeV022.OPENSEARCH_TIMESTAMP_FORMAT_UNSUPPORTED
                if type_compatible
                else OpenSearchSchemaErrorCodeV022.OPENSEARCH_TIMESTAMP_TYPE_INVALID
            ),
            context=context,
            field_path=field,
            hit_ordinal=hit_ordinal,
        )
    if not context.window.started_at <= parsed <= context.window.ended_at:
        _raise_schema_v022(
            OpenSearchSchemaErrorCodeV022.OPENSEARCH_TIMESTAMP_OUT_OF_WINDOW,
            context=context,
            field_path=field,
            hit_ordinal=hit_ordinal,
        )
    return parsed


def _service_v022(
    source: Mapping[str, object],
    *,
    profile: OpenSearchNormalizationProfileV022,
    context: ConnectorQueryContextV1,
    hit_ordinal: int,
) -> str:
    value, field = _extract_v022(source, profile.service_extraction)
    if value is _MISSING_V022:
        _raise_schema_v022(
            OpenSearchSchemaErrorCodeV022.OPENSEARCH_SERVICE_FIELD_MISSING,
            context=context,
            field_path=field,
            hit_ordinal=hit_ordinal,
        )
    if not isinstance(value, str) or not value:
        _raise_schema_v022(
            OpenSearchSchemaErrorCodeV022.OPENSEARCH_SERVICE_TYPE_INVALID,
            context=context,
            field_path=field,
            hit_ordinal=hit_ordinal,
        )
    if value in context.service_aliases:
        service = context.service_aliases[value]
    elif value in context.requested_services:
        service = value
    else:
        _raise_schema_v022(
            OpenSearchSchemaErrorCodeV022.OPENSEARCH_SERVICE_ALIAS_UNMAPPED,
            context=context,
            field_path=field,
            hit_ordinal=hit_ordinal,
        )
    if service not in context.requested_services:
        _raise_schema_v022(
            OpenSearchSchemaErrorCodeV022.OPENSEARCH_SERVICE_NOT_REQUESTED,
            context=context,
            field_path=field,
            hit_ordinal=hit_ordinal,
        )
    return service


def _severity_v022(
    source: Mapping[str, object],
    *,
    profile: OpenSearchNormalizationProfileV022,
    context: ConnectorQueryContextV1,
    hit_ordinal: int,
) -> Literal["WARN", "ERROR", "FATAL", "DIAGNOSTIC"]:
    rule = profile.severity_extraction
    value, field = _extract_v022(source, rule.extraction)
    if value is _MISSING_V022:
        if rule.mode is OpenSearchSeverityModeV022.OPTIONAL_TO_DIAGNOSTIC:
            return "DIAGNOSTIC"
        _raise_schema_v022(
            OpenSearchSchemaErrorCodeV022.OPENSEARCH_SEVERITY_FIELD_MISSING,
            context=context,
            field_path=field,
            hit_ordinal=hit_ordinal,
        )
    if rule.mode is OpenSearchSeverityModeV022.INTEGER_OTEL_SEVERITY:
        if isinstance(value, bool) or not isinstance(value, int):
            _raise_schema_v022(
                OpenSearchSchemaErrorCodeV022.OPENSEARCH_SEVERITY_TYPE_INVALID,
                context=context,
                field_path=field,
                hit_ordinal=hit_ordinal,
            )
        if 1 <= value <= 8:
            return "DIAGNOSTIC"
        if 9 <= value <= 12:
            return "WARN"
        if 13 <= value <= 16:
            return "ERROR"
        if 17 <= value <= 24:
            return "FATAL"
        _raise_schema_v022(
            OpenSearchSchemaErrorCodeV022.OPENSEARCH_SEVERITY_VALUE_UNSUPPORTED,
            context=context,
            field_path=field,
            hit_ordinal=hit_ordinal,
        )
    if not isinstance(value, str):
        _raise_schema_v022(
            OpenSearchSchemaErrorCodeV022.OPENSEARCH_SEVERITY_TYPE_INVALID,
            context=context,
            field_path=field,
            hit_ordinal=hit_ordinal,
        )
    normalized = value.upper()
    if normalized in {"TRACE", "DEBUG", "INFO", "NOTICE", "UNSPECIFIED"}:
        return "DIAGNOSTIC"
    if normalized in {"WARN", "WARNING"}:
        return "WARN"
    if normalized == "ERROR":
        return "ERROR"
    if normalized in {"FATAL", "CRITICAL"}:
        return "FATAL"
    _raise_schema_v022(
        OpenSearchSchemaErrorCodeV022.OPENSEARCH_SEVERITY_VALUE_UNSUPPORTED,
        context=context,
        field_path=field,
        hit_ordinal=hit_ordinal,
    )


def _project_observer_message_v022(
    message: str,
    *,
    policy: str,
    context: ConnectorQueryContextV1,
    field_path: str,
    hit_ordinal: int,
) -> str:
    if policy == "AS_OBSERVED":
        projected = message
    else:
        projected = _FEATURE_CONTROL_CAUSE_V022.sub("", message)
        projected = _OVERLOAD_SIMULATION_COUNT_V022.sub(
            "Queue overload activity completed.",
            projected,
        )
        if _OBSERVER_TRUTH_REMAINDER_V022.search(projected):
            _raise_schema_v022(
                OpenSearchSchemaErrorCodeV022.OPENSEARCH_OBSERVER_PROJECTION_REJECTED,
                context=context,
                field_path=field_path,
                hit_ordinal=hit_ordinal,
            )
    projected = projected.strip()[:500]
    if not projected:
        _raise_schema_v022(
            OpenSearchSchemaErrorCodeV022.OPENSEARCH_MESSAGE_TYPE_INVALID,
            context=context,
            field_path=field_path,
            hit_ordinal=hit_ordinal,
        )
    return projected


def _message_v022(
    source: Mapping[str, object],
    *,
    profile: OpenSearchNormalizationProfileV022,
    context: ConnectorQueryContextV1,
    hit_ordinal: int,
) -> str:
    rule = profile.message_extraction
    value, field = _extract_v022(source, rule.extraction)
    if value is _MISSING_V022:
        _raise_schema_v022(
            OpenSearchSchemaErrorCodeV022.OPENSEARCH_MESSAGE_FIELD_MISSING,
            context=context,
            field_path=field,
            hit_ordinal=hit_ordinal,
        )
    if rule.mode is OpenSearchMessageModeV022.OTLP_STRING_VALUE_WRAPPER:
        if not isinstance(value, Mapping) or set(value) != {"stringValue"}:
            _raise_schema_v022(
                OpenSearchSchemaErrorCodeV022.OPENSEARCH_MESSAGE_WRAPPER_INVALID,
                context=context,
                field_path=field,
                hit_ordinal=hit_ordinal,
            )
        value = value.get("stringValue")
    elif rule.mode is OpenSearchMessageModeV022.SCALAR_TO_TEXT:
        if isinstance(value, (Mapping, list, tuple, set)) or value is None:
            _raise_schema_v022(
                OpenSearchSchemaErrorCodeV022.OPENSEARCH_MESSAGE_TYPE_INVALID,
                context=context,
                field_path=field,
                hit_ordinal=hit_ordinal,
            )
        value = str(value)
    if not isinstance(value, str):
        _raise_schema_v022(
            OpenSearchSchemaErrorCodeV022.OPENSEARCH_MESSAGE_TYPE_INVALID,
            context=context,
            field_path=field,
            hit_ordinal=hit_ordinal,
        )
    return _project_observer_message_v022(
        value,
        policy=profile.message_projection_policy,
        context=context,
        field_path=field,
        hit_ordinal=hit_ordinal,
    )


def _validate_trace_id_v022(
    source: Mapping[str, object],
    *,
    profile: OpenSearchNormalizationProfileV022,
    context: ConnectorQueryContextV1,
    hit_ordinal: int,
) -> None:
    if profile.trace_id_extraction is None:
        return
    value, field = _extract_v022(source, profile.trace_id_extraction)
    if value is _MISSING_V022 or value is None:
        return
    if not isinstance(value, str):
        _raise_schema_v022(
            OpenSearchSchemaErrorCodeV022.OPENSEARCH_TRACE_ID_TYPE_INVALID,
            context=context,
            field_path=field,
            hit_ordinal=hit_ordinal,
        )
    if not re.fullmatch(r"[0-9a-fA-F]{16,64}", value):
        _raise_schema_v022(
            OpenSearchSchemaErrorCodeV022.OPENSEARCH_TRACE_ID_VALUE_INVALID,
            context=context,
            field_path=field,
            hit_ordinal=hit_ordinal,
        )


def _normalize_source_v022(
    source: Mapping[str, object],
    *,
    profile: OpenSearchNormalizationProfileV022,
    context: ConnectorQueryContextV1,
    hit_ordinal: int,
) -> OpenSearchRecordNormalizationV022:
    observed_at = _timestamp_v022(
        source,
        profile=profile,
        context=context,
        hit_ordinal=hit_ordinal,
    )
    service = _service_v022(
        source,
        profile=profile,
        context=context,
        hit_ordinal=hit_ordinal,
    )
    severity = _severity_v022(
        source,
        profile=profile,
        context=context,
        hit_ordinal=hit_ordinal,
    )
    message = _message_v022(
        source,
        profile=profile,
        context=context,
        hit_ordinal=hit_ordinal,
    )
    _validate_trace_id_v022(
        source,
        profile=profile,
        context=context,
        hit_ordinal=hit_ordinal,
    )
    return OpenSearchRecordNormalizationV022.build(
        hit_ordinal=hit_ordinal,
        record=LogRecordV22(
            schema_version="dta-v22.log-record.v1",
            observed_at=observed_at,
            service=service,
            severity=severity,
            message=message,
        ),
    )


def normalize_opensearch_search_v022(
    payload: object,
    *,
    profile: OpenSearchNormalizationProfileV022,
    context: ConnectorQueryContextV1,
    latency_ms: float,
) -> OpenSearchBatchNormalizationV022:
    if not isinstance(payload, Mapping):
        _raise_schema_v022(
            OpenSearchSchemaErrorCodeV022.OPENSEARCH_SEARCH_RESPONSE_NOT_OBJECT,
            context=context,
        )
    hits_container = payload.get("hits")
    if not isinstance(hits_container, Mapping):
        _raise_schema_v022(
            OpenSearchSchemaErrorCodeV022.OPENSEARCH_HITS_CONTAINER_INVALID,
            context=context,
            field_path="hits",
        )
    hits = hits_container.get("hits")
    if not isinstance(hits, list):
        _raise_schema_v022(
            OpenSearchSchemaErrorCodeV022.OPENSEARCH_HITS_LIST_INVALID,
            context=context,
            field_path="hits.hits",
        )
    normalizations: list[OpenSearchRecordNormalizationV022] = []
    rejections: list[OpenSearchRecordRejectionV022] = []
    for ordinal, hit in enumerate(hits):
        try:
            if not isinstance(hit, Mapping):
                _raise_schema_v022(
                    OpenSearchSchemaErrorCodeV022.OPENSEARCH_HIT_NOT_OBJECT,
                    context=context,
                    hit_ordinal=ordinal,
                )
            if "_source" not in hit:
                _raise_schema_v022(
                    OpenSearchSchemaErrorCodeV022.OPENSEARCH_SOURCE_MISSING,
                    context=context,
                    field_path="_source",
                    hit_ordinal=ordinal,
                )
            source = hit.get("_source")
            if not isinstance(source, Mapping):
                _raise_schema_v022(
                    OpenSearchSchemaErrorCodeV022.OPENSEARCH_SOURCE_NOT_OBJECT,
                    context=context,
                    field_path="_source",
                    hit_ordinal=ordinal,
                )
            normalizations.append(
                _normalize_source_v022(
                    source,
                    profile=profile,
                    context=context,
                    hit_ordinal=ordinal,
                )
            )
        except OpenSearchSchemaExceptionV022 as error:
            rejections.append(
                OpenSearchRecordRejectionV022.build(
                    hit_ordinal=ordinal,
                    failure=error.failure,
                )
            )
    total: object = hits_container.get("total", len(hits))
    if isinstance(total, Mapping):
        total = total.get("value", len(hits))
    total_hit_count = total if isinstance(total, int) and total >= len(hits) else len(hits)
    return build_opensearch_batch_v022(
        total_hit_count=total_hit_count,
        sampled_hit_count=len(hits),
        normalizations=tuple(normalizations),
        rejections=tuple(rejections),
        requested_services=context.requested_services,
        maximum_record_rejection_fraction=(
            profile.maximum_record_rejection_fraction
        ),
        truncated=total_hit_count > len(hits),
        latency_ms=latency_ms,
    )


__all__ = (
    "OpenSearchSchemaExceptionV022",
    "normalize_opensearch_search_v022",
)
