"""Bounded read-only Jaeger Product connector."""

from __future__ import annotations

from datetime import UTC, datetime
import json
from typing import Callable, Mapping

import httpx

from ecomsre.dta_v2.v22.read_contracts import (
    EvidenceSourceV22,
    ReadSourceStatusV22,
    SpanStatusV22,
    TraceSpanV22,
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
    JaegerConnectorSettingsV1,
)


def _tag_map(value: object) -> dict[str, object]:
    if not isinstance(value, list):
        raise ValueError("Jaeger tags are invalid")
    result: dict[str, object] = {}
    for raw_tag in value:
        tag = require_mapping(raw_tag)
        key = tag.get("key")
        if not isinstance(key, str):
            raise ValueError("Jaeger tag is invalid")
        tag_value = tag.get("value")
        if key == "error" and isinstance(tag_value, str):
            if tag_value.casefold() == "true":
                tag_value = True
            elif tag_value.casefold() == "false":
                tag_value = False
        if key in result:
            if key != "error" or result[key] != tag_value:
                raise ValueError("Jaeger tag is invalid")
            continue
        result[key] = tag_value
    return result


def _span_is_error(tags: Mapping[str, object]) -> bool:
    error_value = tags.get("error")
    otel_status = tags.get("otel.status_code")
    return error_value is True or (
        isinstance(otel_status, str) and otel_status.upper() == "ERROR"
    )


def _field(source: Mapping[str, object], path: str) -> object:
    current: object = source
    for segment in path.split("."):
        if not isinstance(current, Mapping) or segment not in current:
            raise ValueError("Jaeger service field is unavailable")
        current = current[segment]
    return current


def _trace_has_missing_parent(trace: Mapping[str, object]) -> bool:
    spans = trace.get("spans")
    if not isinstance(spans, list):
        raise ValueError("Jaeger trace is invalid")
    span_ids = {
        span_id
        for raw_span in spans
        if isinstance((span_id := require_mapping(raw_span).get("spanID")), str)
    }
    for raw_span in spans:
        references = require_mapping(raw_span).get("references")
        if not isinstance(references, list):
            raise ValueError("Jaeger span references are invalid")
        for raw_reference in references:
            reference = require_mapping(raw_reference)
            if reference.get("refType") != "CHILD_OF":
                continue
            parent_id = reference.get("spanID")
            if not isinstance(parent_id, str):
                raise ValueError("Jaeger parent reference is invalid")
            if parent_id not in span_ids:
                return True
    return False


class JaegerConnectorV1:
    def __init__(
        self,
        config: ConnectorConfigV1,
        *,
        credential_resolver: CredentialResolverV1,
        timeout_seconds: float,
        transport: httpx.BaseTransport | None = None,
        before_request: Callable[[], None] | None = None,
    ) -> None:
        if config.kind is not ConnectorKindV1.JAEGER or config.endpoint is None:
            raise ValueError("Jaeger connector configuration is invalid")
        self.config = config
        self._settings = JaegerConnectorSettingsV1.model_validate(config.settings)
        self._endpoint = config.endpoint.rstrip("/")
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
                source=EvidenceSourceV22.TRACES,
                supports_historical_range=True,
                supports_multi_target=True,
                supports_service_discovery=True,
                supports_baseline=False,
                supports_target_complete_coverage=False,
                maximum_window_seconds=3600,
            ),
        )

    def verify(self) -> ConnectorHealthResultV1:
        latency_ms = 0.0
        try:
            payload, _, latency_ms = self._http.request_json(
                "GET",
                f"{self._endpoint}/api/services",
            )
            data = require_mapping(payload).get("data")
            if not isinstance(data, list) or any(
                not isinstance(item, str) or not item for item in data
            ):
                raise ValueError("Jaeger service discovery is invalid")
        except ConnectorRequestError as error:
            return self._unavailable_health(error.safe_error_code, error.latency_ms)
        except ValueError:
            return self._unavailable_health("CONNECTOR_SCHEMA_INVALID", latency_ms)
        return ConnectorHealthResultV1(
            connector_name=self.config.name,
            kind=self.config.kind,
            status=ConnectorAvailabilityV1.AVAILABLE,
            capabilities=self.capabilities(),
            discovered_services=tuple(sorted(set(data))),
            safe_error_code=None,
            latency_ms=latency_ms,
        )

    def query(
        self,
        context: ConnectorQueryContextV1,
    ) -> tuple[ConnectorQueryResultV1, ...]:
        context = ConnectorQueryContextV1.model_validate(context.model_dump())
        maximum_records = min(context.maximum_records, self._settings.limit, 200)
        records: list[TraceSpanV22] = []
        latency_ms = 0.0
        truncated = False
        rejected_trace_count = 0
        try:
            for service in context.requested_services:
                for source_alias in context.aliases_for(service):
                    params: dict[str, str | int] = {
                        "service": source_alias,
                        "start": int(
                            context.window.started_at.timestamp() * 1_000_000
                        ),
                        "end": int(
                            context.window.ended_at.timestamp() * 1_000_000
                        ),
                        "limit": min(self._settings.limit, maximum_records),
                        "lookback": f"{self._settings.lookback_seconds}s",
                        "minDuration": f"{self._settings.minimum_duration_ms}ms",
                    }
                    if self._settings.tags:
                        params["tags"] = json.dumps(
                            self._settings.tags,
                            sort_keys=True,
                            separators=(",", ":"),
                        )
                    payload, _, request_latency = self._http.request_json(
                        "GET",
                        f"{self._endpoint}/api/traces",
                        params=params,
                    )
                    latency_ms += request_latency
                    traces = require_mapping(payload).get("data")
                    if not isinstance(traces, list):
                        raise ValueError("Jaeger trace response is invalid")
                    for trace in traces:
                        try:
                            trace_payload = require_mapping(trace)
                            partial_trace = _trace_has_missing_parent(trace_payload)
                            normalized = self._normalize_trace(
                                trace_payload,
                                context=context,
                            )
                        except ValueError:
                            rejected_trace_count += 1
                            truncated = True
                            continue
                        if partial_trace:
                            truncated = True
                        if context.neighborhood_hops is not None:
                            normalized = [
                                record
                                for record in normalized
                                if record.service in record.service_path
                                and any(
                                    target in record.service_path
                                    and abs(
                                        record.service_path.index(target)
                                        - record.service_path.index(record.service)
                                    )
                                    <= context.neighborhood_hops
                                    for target in context.requested_services
                                )
                            ]
                        normalized.sort(
                            key=lambda record: (
                                record.service not in context.requested_services
                            )
                        )
                        available = maximum_records - len(records)
                        if len(normalized) > available:
                            records.extend(normalized[:available])
                            truncated = True
                            break
                        records.extend(normalized)
                    if len(records) >= maximum_records:
                        truncated = True
                        break
                if len(records) >= maximum_records:
                    break
            if rejected_trace_count and not records:
                raise ValueError("Jaeger response contains no valid trace")
        except ConnectorRequestError as error:
            return (self._failure(context, error),)
        except (ValueError, TypeError, OverflowError):
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
        covered = tuple(sorted({item.service for item in records}))
        return (
            ConnectorQueryResultV1.build(
                source=EvidenceSourceV22.TRACES,
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

    def _normalize_trace(
        self,
        trace: Mapping[str, object],
        *,
        context: ConnectorQueryContextV1,
    ) -> list[TraceSpanV22]:
        processes_raw = trace.get("processes")
        spans_raw = trace.get("spans")
        if not isinstance(processes_raw, Mapping) or not isinstance(spans_raw, list):
            raise ValueError("Jaeger trace is invalid")
        processes: dict[str, str] = {}
        for process_id, raw_process in processes_raw.items():
            process = require_mapping(raw_process)
            service = _field(process, self._settings.service_field_behavior)
            if not isinstance(process_id, str) or not isinstance(service, str):
                raise ValueError("Jaeger process is invalid")
            processes[process_id] = context.normalize_service(service)

        raw_by_id: dict[str, Mapping[str, object]] = {}
        parent_by_id: dict[str, str | None] = {}
        service_by_id: dict[str, str] = {}
        error_by_id: dict[str, bool] = {}
        for raw_span in spans_raw:
            span = require_mapping(raw_span)
            span_id = span.get("spanID")
            process_id = span.get("processID")
            references = span.get("references")
            if (
                not isinstance(span_id, str)
                or span_id in raw_by_id
                or not isinstance(process_id, str)
                or process_id not in processes
                or not isinstance(references, list)
            ):
                raise ValueError("Jaeger span identity is invalid")
            parents: list[str] = []
            for raw_reference in references:
                reference = require_mapping(raw_reference)
                if reference.get("refType") == "CHILD_OF":
                    parent = reference.get("spanID")
                    if not isinstance(parent, str):
                        raise ValueError("Jaeger parent reference is invalid")
                    parents.append(parent)
            if len(parents) > 1:
                raise ValueError("Jaeger span has multiple causal parents")
            raw_by_id[span_id] = span
            parent_by_id[span_id] = parents[0] if parents else None
            service_by_id[span_id] = processes[process_id]
            error_by_id[span_id] = _span_is_error(_tag_map(span.get("tags")))

        path_cache: dict[str, tuple[str, ...]] = {}

        def path_for(span_id: str, visiting: frozenset[str] = frozenset()) -> tuple[str, ...]:
            if span_id in path_cache:
                return path_cache[span_id]
            if span_id in visiting:
                raise ValueError("Jaeger causal graph contains a cycle")
            parent_id = parent_by_id[span_id]
            if parent_id is None:
                path: tuple[str, ...] = (service_by_id[span_id],)
            elif parent_id not in raw_by_id:
                path = (service_by_id[span_id],)
            else:
                parent_path = path_for(parent_id, visiting | {span_id})
                current_service = service_by_id[span_id]
                path = (
                    parent_path
                    if parent_path[-1] == current_service
                    else parent_path + (current_service,)
                )
            if len(path) > 12:
                raise ValueError("Jaeger causal path exceeds the bound")
            path_cache[span_id] = path
            return path

        records: list[TraceSpanV22] = []

        def error_ancestry(span_id: str) -> tuple[bool, bool]:
            parent_id = parent_by_id[span_id]
            visited: set[str] = set()
            while parent_id is not None:
                if parent_id in visited:
                    raise ValueError("Jaeger error ancestry is invalid")
                if parent_id not in error_by_id:
                    return False, False
                visited.add(parent_id)
                if error_by_id[parent_id]:
                    return True, True
                parent_id = parent_by_id[parent_id]
            return False, True

        for span_id, span in raw_by_id.items():
            duration = span.get("duration")
            started = span.get("startTime")
            operation = span.get("operationName")
            if (
                not isinstance(duration, (int, float))
                or isinstance(duration, bool)
                or duration < 0
                or not isinstance(started, (int, float))
                or isinstance(started, bool)
                or not isinstance(operation, str)
                or not operation.strip()
            ):
                raise ValueError("Jaeger span fields are invalid")
            duration_ms = float(duration) / 1000
            if duration_ms < self._settings.minimum_duration_ms:
                continue
            is_error = error_by_id[span_id]
            service_path = path_for(span_id)
            has_error_ancestor, ancestry_complete = error_ancestry(span_id)
            records.append(
                TraceSpanV22(
                    schema_version="dta-v22.trace-span.v1",
                    observed_at=datetime.fromtimestamp(float(started) / 1_000_000, UTC),
                    service_path=service_path,
                    service=service_by_id[span_id],
                    parent_service=(
                        service_path[-2] if len(service_path) > 1 else None
                    ),
                    operation=operation.strip()[:160],
                    status=SpanStatusV22.ERROR if is_error else SpanStatusV22.UNSET,
                    duration_ms=duration_ms,
                    first_error_location=(
                        is_error and ancestry_complete and not has_error_ancestor
                    ),
                )
            )
        return records

    def _failure(
        self,
        context: ConnectorQueryContextV1,
        error: ConnectorRequestError,
    ) -> ConnectorQueryResultV1:
        return ConnectorQueryResultV1.build(
            source=EvidenceSourceV22.TRACES,
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


__all__ = ("JaegerConnectorV1",)
