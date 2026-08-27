"""Bounded read-only OpenSearch Product connector."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Callable, Literal, Mapping, cast
from urllib.parse import quote

import httpx

from ecomsre.dta_v2.v22.read_contracts import (
    EvidenceSourceV22,
    LogRecordV22,
    ReadSourceStatusV22,
)
from ecomsre.product.connectors._http import (
    BoundedHttpTransportV1,
    ConnectorRequestError,
    require_mapping,
)
from ecomsre.product.connectors.base import (
    ConnectorAvailabilityV1,
    ConnectorCapabilityV1,
    ConnectorHealthResultV1,
    ConnectorQueryContextV1,
    ConnectorQueryResultV1,
)
from ecomsre.product.connectors.credentials import CredentialResolverV1
from ecomsre.product.contracts import (
    ConnectorConfigV1,
    ConnectorKindV1,
    OpenSearchConnectorSettingsV1,
)


def _field(source: Mapping[str, object], path: str) -> object:
    current: object = source
    for segment in path.split("."):
        if not isinstance(current, Mapping) or segment not in current:
            raise ValueError("OpenSearch source field is unavailable")
        current = current[segment]
    return current


def _optional_field(source: Mapping[str, object], path: str) -> object | None:
    current: object = source
    for segment in path.split("."):
        if not isinstance(current, Mapping) or segment not in current:
            return None
        current = current[segment]
    return current


def _timestamp(value: object) -> datetime:
    if not isinstance(value, str):
        raise ValueError("OpenSearch timestamp is invalid")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.utcoffset() is None:
        raise ValueError("OpenSearch timestamp is not timezone-aware")
    return parsed.astimezone(UTC)


class OpenSearchConnectorV1:
    def __init__(
        self,
        config: ConnectorConfigV1,
        *,
        credential_resolver: CredentialResolverV1,
        timeout_seconds: float,
        transport: httpx.BaseTransport | None = None,
        before_request: Callable[[], None] | None = None,
    ) -> None:
        if config.kind is not ConnectorKindV1.OPENSEARCH or config.endpoint is None:
            raise ValueError("OpenSearch connector configuration is invalid")
        self.config = config
        self._settings = OpenSearchConnectorSettingsV1.model_validate(config.settings)
        self._index_pattern = self._settings.index_pattern
        self._timestamp_field = self._settings.timestamp_field
        self._service_field = self._settings.service_field
        self._severity_field = self._settings.severity_field
        self._message_field = self._settings.message_field
        self._trace_id_field = self._settings.trace_id_field
        endpoint = config.endpoint.rstrip("/")
        index = quote(self._index_pattern, safe="*,-_")
        self._search_url = f"{endpoint}/{index}/_search"
        self._http = BoundedHttpTransportV1(
            credential_resolver=credential_resolver,
            credential_refs=config.credential_refs,
            timeout_seconds=timeout_seconds,
            maximum_response_bytes=self._settings.maximum_response_bytes,
            transport=transport,
            before_request=before_request,
        )

    def capabilities(self) -> tuple[ConnectorCapabilityV1, ...]:
        return (
            ConnectorCapabilityV1(
                source=EvidenceSourceV22.LOGS,
                supports_historical_range=True,
                supports_multi_target=True,
                supports_service_discovery=True,
                supports_baseline=True,
                supports_target_complete_coverage=False,
                maximum_window_seconds=3600,
            ),
        )

    def verify(self) -> ConnectorHealthResultV1:
        started_latency = 0.0
        try:
            payload, _, started_latency = self._http.request_json(
                "POST",
                self._search_url,
                json_body={
                    "size": 0,
                    "aggs": {
                        "services": {
                            "terms": {
                                "field": self._service_field,
                                "size": min(self._settings.maximum_result_count, 1000),
                            }
                        }
                    },
                },
            )
            body = require_mapping(payload)
            aggregations = require_mapping(body.get("aggregations"))
            services = require_mapping(aggregations.get("services"))
            buckets = services.get("buckets")
            if not isinstance(buckets, list):
                raise ValueError("OpenSearch service aggregation is invalid")
            discovered: list[str] = []
            for bucket in buckets:
                key = require_mapping(bucket).get("key")
                if not isinstance(key, str) or not key:
                    raise ValueError("OpenSearch service aggregation key is invalid")
                discovered.append(key)
        except ConnectorRequestError as error:
            return self._unavailable_health(error.safe_error_code, error.latency_ms)
        except ValueError:
            return self._unavailable_health(
                "CONNECTOR_SCHEMA_INVALID",
                started_latency,
            )
        return ConnectorHealthResultV1(
            connector_name=self.config.name,
            kind=self.config.kind,
            status=ConnectorAvailabilityV1.AVAILABLE,
            capabilities=self.capabilities(),
            discovered_services=tuple(sorted(set(discovered))),
            safe_error_code=None,
            latency_ms=started_latency,
        )

    def query(
        self,
        context: ConnectorQueryContextV1,
    ) -> tuple[ConnectorQueryResultV1, ...]:
        context = ConnectorQueryContextV1.model_validate(context.model_dump())
        limit = min(context.maximum_records, self._settings.maximum_result_count, 200)
        latency_ms = 0.0
        filters: list[object] = [
            {
                "terms": {
                    self._service_field: [
                        alias
                        for service in context.requested_services
                        for alias in context.aliases_for(service)
                    ]
                }
            },
            {
                "range": {
                    self._timestamp_field: {
                        "gte": context.window.started_at.isoformat(),
                        "lte": context.window.ended_at.isoformat(),
                    }
                }
            },
        ]
        if self._settings.severity_filter:
            filters.append(
                {"terms": {self._severity_field: list(self._settings.severity_filter)}}
            )
        try:
            projected_fields = [
                self._timestamp_field,
                self._service_field,
                self._severity_field,
                self._message_field,
            ]
            if self._trace_id_field is not None:
                projected_fields.append(self._trace_id_field)
            payload, _, latency_ms = self._http.request_json(
                "POST",
                self._search_url,
                json_body={
                    "size": limit,
                    "sort": [{self._timestamp_field: {"order": "desc"}}],
                    "query": {
                        "bool": {
                            "filter": filters
                        }
                    },
                    "_source": list(dict.fromkeys(projected_fields)),
                },
            )
            body = require_mapping(payload)
            hits_body = require_mapping(body.get("hits"))
            hits = hits_body.get("hits")
            if not isinstance(hits, list):
                raise ValueError("OpenSearch hits are invalid")
            records: list[LogRecordV22] = []
            for hit in hits:
                source = require_mapping(require_mapping(hit).get("_source"))
                source_service = _field(source, self._service_field)
                severity = _field(source, self._severity_field)
                message = _field(source, self._message_field)
                if (
                    not isinstance(source_service, str)
                    or not isinstance(severity, str)
                    or not isinstance(message, str)
                ):
                    raise ValueError("OpenSearch projected log fields are invalid")
                if self._trace_id_field is not None:
                    trace_id = _optional_field(source, self._trace_id_field)
                    if trace_id is not None and (
                        not isinstance(trace_id, str)
                        or not trace_id
                        or len(trace_id) > 128
                    ):
                        raise ValueError("OpenSearch trace ID field is invalid")
                service = context.normalize_service(source_service)
                if service not in context.requested_services:
                    raise ValueError("OpenSearch service alias is not requested")
                normalized_raw = severity.upper()
                normalized_severity = (
                    cast(Literal["WARN", "ERROR", "FATAL"], normalized_raw)
                    if normalized_raw in {"WARN", "ERROR", "FATAL"}
                    else "DIAGNOSTIC"
                )
                observed_at = _timestamp(_field(source, self._timestamp_field))
                if not context.window.started_at <= observed_at <= context.window.ended_at:
                    raise ValueError("OpenSearch timestamp is outside the requested window")
                records.append(
                    LogRecordV22(
                        schema_version="dta-v22.log-record.v1",
                        observed_at=observed_at,
                        service=service,
                        severity=normalized_severity,
                        message=message[:500],
                    )
                )
        except ConnectorRequestError as error:
            return (self._failure(context, error),)
        except (ValueError, TypeError):
            return (
                self._failure(
                    context,
                    ConnectorRequestError(
                        ReadSourceStatusV22.FAILURE_SCHEMA,
                        "CONNECTOR_SCHEMA_INVALID",
                        latency_ms,
                    ),
                ),
            )
        total = hits_body.get("total")
        total_value: object = total
        if isinstance(total, Mapping):
            total_value = total.get("value")
        truncated = isinstance(total_value, int) and total_value > len(records)
        covered = tuple(sorted({item.service for item in records}))
        return (
            ConnectorQueryResultV1.build(
                source=EvidenceSourceV22.LOGS,
                status=(
                    ReadSourceStatusV22.SUCCESS_NONEMPTY
                    if records
                    else ReadSourceStatusV22.SUCCESS_EMPTY
                ),
                requested_services=context.requested_services,
                covered_services=covered,
                window=context.window,
                records=tuple(records),
                truncated=truncated,
                safe_error_code=None,
                latency_ms=latency_ms,
            ),
        )

    def _failure(
        self,
        context: ConnectorQueryContextV1,
        error: ConnectorRequestError,
    ) -> ConnectorQueryResultV1:
        return ConnectorQueryResultV1.build(
            source=EvidenceSourceV22.LOGS,
            status=error.status,
            requested_services=context.requested_services,
            covered_services=(),
            window=context.window,
            records=(),
            truncated=False,
            safe_error_code=error.safe_error_code,
            latency_ms=error.latency_ms,
        )

    def _unavailable_health(
        self,
        safe_error_code: str,
        latency_ms: float,
    ) -> ConnectorHealthResultV1:
        return ConnectorHealthResultV1(
            connector_name=self.config.name,
            kind=self.config.kind,
            status=ConnectorAvailabilityV1.UNAVAILABLE,
            capabilities=self.capabilities(),
            discovered_services=(),
            safe_error_code=safe_error_code,
            latency_ms=latency_ms,
        )

    def close(self) -> None:
        self._http.close()


__all__ = ("OpenSearchConnectorV1",)
