"""Deterministic run-bound replay over sanitized PR-E capture fixtures."""

from __future__ import annotations

from typing import cast

from pydantic import JsonValue

from ecomsre.dta_v2.evaluation_contracts import (
    AgentVisibleReplayCase,
    ReplayObservationFixture,
)
from ecomsre.dta_v2.read_tools import BackendResult, ReadBackendFailure
from ecomsre.dta_v2.tool_contracts import (
    DiagnosticLogRecord,
    InspectResourceUsageRequest,
    InspectServiceRuntimeRequest,
    MetricKind,
    MetricRecord,
    QueryMetricsRequest,
    ReadToolRequest,
    ResourceUsageRecord,
    RuntimeRecord,
    SearchLogsRequest,
    ToolErrorCode,
    ToolName,
    TraceNeighborhoodRecord,
    TraceNeighborhoodRequest,
    build_fake_read_authority,
    build_inspect_resource_usage_request,
    build_inspect_service_runtime_request,
    build_query_metrics_request,
    build_search_logs_request,
    build_trace_neighborhood_request,
    revalidate_read_tool_request,
)


class ReplayCaseReadBackend:
    """Serve only records captured in one exact truth-free replay case."""

    def __init__(self, case: AgentVisibleReplayCase) -> None:
        self.case = AgentVisibleReplayCase.model_validate(case.model_dump())
        self.authority = build_fake_read_authority()
        self.call_count = 0
        self._fixtures = {item.tool: item for item in self.case.observations}

    def execute(self, request: ReadToolRequest) -> BackendResult:
        request = revalidate_read_tool_request(request)
        self.call_count += 1
        fixture = self._fixtures.get(request.tool)
        if fixture is None:
            raise ReadBackendFailure(ToolErrorCode.SOURCE_UNAVAILABLE)
        if fixture.error_code is not None:
            raise ReadBackendFailure(fixture.error_code)
        if isinstance(request, QueryMetricsRequest):
            by_kind = {
                item.metric_kind: item
                for item in fixture.records
                if type(item) is MetricRecord and item.service == request.service
            }
            if any(kind not in by_kind for kind in request.metric_kinds):
                raise ReadBackendFailure(ToolErrorCode.SOURCE_UNAVAILABLE)
            return BackendResult(
                tuple(by_kind[kind] for kind in request.metric_kinds),
                fixture.truncated,
            )
        if isinstance(request, SearchLogsRequest):
            log_records = tuple(
                item
                for item in fixture.records
                if type(item) is DiagnosticLogRecord
                and item.service == request.service
                and request.started_at <= item.observed_at <= request.ended_at
            )
            if not log_records:
                raise ReadBackendFailure(ToolErrorCode.SOURCE_UNAVAILABLE)
            return BackendResult(
                log_records[: request.max_records],
                fixture.truncated or len(log_records) > request.max_records,
            )
        if isinstance(request, TraceNeighborhoodRequest):
            trace_records = tuple(
                TraceNeighborhoodRecord.model_validate(
                    {
                        **item.model_dump(mode="python"),
                        "anchor_service": request.service,
                    }
                )
                for item in fixture.records
                if type(item) is TraceNeighborhoodRecord
                and request.service in item.service_path
            )
            if not trace_records:
                raise ReadBackendFailure(ToolErrorCode.SOURCE_UNAVAILABLE)
            trace_records = tuple(
                sorted(
                    trace_records,
                    key=lambda item: (
                        item.service_path,
                        item.service,
                        item.relationship.value,
                        item.parent_service or "",
                        item.operation,
                        item.status.value,
                        item.duration_ms,
                        item.first_error_location,
                    ),
                )
            )
            return BackendResult(
                trace_records[: request.max_spans],
                fixture.truncated or len(trace_records) > request.max_spans,
            )
        if isinstance(request, InspectServiceRuntimeRequest):
            runtime_by_service = {
                item.logical_service: item
                for item in fixture.records
                if type(item) is RuntimeRecord
            }
            if any(service not in runtime_by_service for service in request.services):
                raise ReadBackendFailure(ToolErrorCode.SOURCE_UNAVAILABLE)
            return BackendResult(
                tuple(runtime_by_service[item] for item in request.services)
            )
        assert isinstance(request, InspectResourceUsageRequest)
        resource_by_service = {
            item.logical_service: item
            for item in fixture.records
            if type(item) is ResourceUsageRecord
            and item.sampling_window_seconds == request.sampling_window_seconds
            and len(item.samples) == request.sample_count
        }
        if any(service not in resource_by_service for service in request.services):
            raise ReadBackendFailure(ToolErrorCode.SOURCE_UNAVAILABLE)
        return BackendResult(
            tuple(resource_by_service[item] for item in request.services)
        )


def _single_service(fixture: ReplayObservationFixture) -> str:
    if len(fixture.service_scope) != 1:
        raise ValueError("windowed materialization fixture is not single-service")
    return fixture.service_scope[0]


def build_materialization_request(
    *,
    run_id: str,
    case: AgentVisibleReplayCase,
    fixture: ReplayObservationFixture,
) -> ReadToolRequest:
    """Build the exact bounded request that rehydrates one captured source."""

    case = AgentVisibleReplayCase.model_validate(case.model_dump())
    fixture = ReplayObservationFixture.model_validate(fixture.model_dump())
    service = _single_service(fixture)
    if fixture.tool is ToolName.QUERY_METRICS:
        kinds = tuple(
            item.metric_kind for item in fixture.records if type(item) is MetricRecord
        )
        if not kinds:
            kinds = (MetricKind.ERROR_RATE,)
        return build_query_metrics_request(
            run_id=run_id,
            service=service,
            started_at=case.captured_started_at,
            ended_at=case.captured_ended_at,
            metric_kinds=kinds,
            max_results=len(kinds),
        )
    if fixture.tool is ToolName.SEARCH_LOGS:
        return build_search_logs_request(
            run_id=run_id,
            service=service,
            started_at=case.captured_started_at,
            ended_at=case.captured_ended_at,
            max_records=max(1, len(fixture.records)),
        )
    if fixture.tool is ToolName.QUERY_TRACE_NEIGHBORHOOD:
        return build_trace_neighborhood_request(
            run_id=run_id,
            service=service,
            started_at=case.captured_started_at,
            ended_at=case.captured_ended_at,
            max_spans=max(1, len(fixture.records)),
        )
    if fixture.tool is ToolName.INSPECT_SERVICE_RUNTIME:
        services = fixture.service_scope
        return build_inspect_service_runtime_request(
            run_id=run_id, services=services, max_results=len(services)
        )
    records = tuple(
        item for item in fixture.records if type(item) is ResourceUsageRecord
    )
    if not records:
        return build_inspect_resource_usage_request(
            run_id=run_id,
            services=fixture.service_scope,
            sampling_window_seconds=5,
            sample_count=3,
        )
    first = records[0]
    return build_inspect_resource_usage_request(
        run_id=run_id,
        services=fixture.service_scope,
        sampling_window_seconds=first.sampling_window_seconds,
        sample_count=len(first.samples),
    )


def provider_request_arguments(request: ReadToolRequest) -> dict[str, JsonValue]:
    """Project one runtime-owned request back to its safe Provider surface."""

    request = revalidate_read_tool_request(request)
    excluded = {
        "schema_version",
        "tool",
        "run_id",
        "started_at",
        "ended_at",
        "normalized_request_sha256",
    }
    return cast(dict[str, JsonValue], request.model_dump(mode="json", exclude=excluded))


__all__ = [
    "ReplayCaseReadBackend",
    "build_materialization_request",
    "provider_request_arguments",
]
