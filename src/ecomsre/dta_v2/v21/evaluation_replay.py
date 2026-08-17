"""Deterministic run-bound replay over sanitized v2.1 capture fixtures."""

from __future__ import annotations

from typing import cast

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
    SpanStatus,
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
from ecomsre.dta_v2.v21.evaluation_contracts import (
    AgentVisibleReplayCaseV21,
    ReplayObservationFixtureV21,
)


class ReplayCaseReadBackendV21:
    def __init__(self, case: AgentVisibleReplayCaseV21) -> None:
        self.case = AgentVisibleReplayCaseV21.model_validate(
            case.model_dump(mode="python")
        )
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
            log_records: tuple[DiagnosticLogRecord, ...] = tuple(
                cast(DiagnosticLogRecord, item)
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
            captured: tuple[TraceNeighborhoodRecord, ...] = tuple(
                cast(TraceNeighborhoodRecord, item)
                for item in fixture.records
                if type(item) is TraceNeighborhoodRecord
            )
            in_neighborhood = any(
                item.anchor_service == request.service
                or request.service in item.service_path
                for item in captured
            )
            trace_records: tuple[TraceNeighborhoodRecord, ...] = tuple(
                TraceNeighborhoodRecord.model_validate(
                    {
                        **item.model_dump(mode="python"),
                        "anchor_service": request.service,
                    }
                )
                for item in captured
                if in_neighborhood
            )
            if not trace_records:
                raise ReadBackendFailure(ToolErrorCode.SOURCE_UNAVAILABLE)
            selected: tuple[TraceNeighborhoodRecord, ...] = tuple(
                sorted(
                    trace_records,
                    key=lambda item: (
                        not (
                            item.service == request.service
                            and item.first_error_location
                            and item.status is SpanStatus.ERROR
                        ),
                        not item.first_error_location,
                        item.service != request.service,
                        item.status is not SpanStatus.ERROR,
                        -len(item.service_path),
                        _trace_key(item),
                    ),
                )[: request.max_spans]
            )
            ordered: tuple[TraceNeighborhoodRecord, ...] = tuple(
                sorted(selected, key=_trace_key)
            )
            return BackendResult(
                records=ordered,
                truncated=(fixture.truncated or len(trace_records) > request.max_spans),
            )
        if isinstance(request, InspectServiceRuntimeRequest):
            runtime_by_service: dict[str, RuntimeRecord] = {
                cast(RuntimeRecord, item).logical_service: cast(RuntimeRecord, item)
                for item in fixture.records
                if type(item) is RuntimeRecord
            }
            if any(service not in runtime_by_service for service in request.services):
                raise ReadBackendFailure(ToolErrorCode.SOURCE_UNAVAILABLE)
            return BackendResult(
                tuple(runtime_by_service[item] for item in request.services)
            )
        assert isinstance(request, InspectResourceUsageRequest)
        resources_by_service: dict[str, ResourceUsageRecord] = {
            cast(ResourceUsageRecord, item).logical_service: cast(
                ResourceUsageRecord, item
            )
            for item in fixture.records
            if type(item) is ResourceUsageRecord
            and cast(ResourceUsageRecord, item).sampling_window_seconds
            == request.sampling_window_seconds
            and len(cast(ResourceUsageRecord, item).samples) == request.sample_count
        }
        if any(service not in resources_by_service for service in request.services):
            raise ReadBackendFailure(ToolErrorCode.SOURCE_UNAVAILABLE)
        return BackendResult(
            tuple(resources_by_service[item] for item in request.services)
        )


def _trace_key(item: TraceNeighborhoodRecord) -> tuple[object, ...]:
    return (
        item.service_path,
        item.service,
        item.relationship.value,
        item.parent_service or "",
        item.operation,
        item.status.value,
        item.duration_ms,
        item.first_error_location,
    )


def build_materialization_request_v21(
    *,
    run_id: str,
    case: AgentVisibleReplayCaseV21,
    fixture: ReplayObservationFixtureV21,
) -> ReadToolRequest:
    if len(fixture.service_scope) != 1 and fixture.tool not in (
        ToolName.INSPECT_SERVICE_RUNTIME,
        ToolName.INSPECT_RESOURCE_USAGE,
    ):
        raise ValueError("windowed materialization fixture is not single-service")
    service = fixture.service_scope[0]
    if fixture.tool is ToolName.QUERY_METRICS:
        kinds = tuple(
            item.metric_kind for item in fixture.records if type(item) is MetricRecord
        ) or (MetricKind.ERROR_RATE,)
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
        return build_inspect_service_runtime_request(
            run_id=run_id,
            services=fixture.service_scope,
            max_results=len(fixture.service_scope),
        )
    resources = tuple(
        item for item in fixture.records if type(item) is ResourceUsageRecord
    )
    if not resources:
        return build_inspect_resource_usage_request(
            run_id=run_id,
            services=fixture.service_scope,
            sampling_window_seconds=5,
            sample_count=3,
        )
    first = resources[0]
    return build_inspect_resource_usage_request(
        run_id=run_id,
        services=fixture.service_scope,
        sampling_window_seconds=first.sampling_window_seconds,
        sample_count=len(first.samples),
    )


__all__ = ("ReplayCaseReadBackendV21", "build_materialization_request_v21")
