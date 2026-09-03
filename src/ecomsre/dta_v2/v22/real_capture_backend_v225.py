"""Canonical snapshot query semantics over one frozen opaque live capture."""

from __future__ import annotations

from ecomsre.dta_v2.read_tools import BackendResult, ReadBackendFailure
from ecomsre.dta_v2.tool_contracts import (
    DiagnosticLogRecord,
    EndpointState,
    HealthState,
    InspectResourceUsageRequest,
    InspectServiceRuntimeRequest,
    LogSeverity,
    METRIC_UNIT_BY_KIND,
    MetricKind,
    MetricRecord,
    QueryMetricsRequest,
    ReadToolRequest,
    ResourceSample,
    ResourceUsageRecord,
    RuntimeRecord,
    RuntimeState,
    SearchLogsRequest,
    SpanRelationship,
    SpanStatus,
    ToolErrorCode,
    TraceNeighborhoodRecord,
    TraceNeighborhoodRequest,
    build_fake_read_authority,
    revalidate_read_tool_request,
)
from ecomsre.dta_v2.v22.read_contracts import (
    EvidenceSourceV22,
    MetricKindV22,
    ReadSourceStatusV22,
    RuntimeStateV22,
)
from ecomsre.dta_v2.v22.real_fault_capture_v225 import RealFaultOpaqueCaptureV1


_METRIC_KIND = {item.value: item for item in MetricKind}
_FAILURE = {
    ReadSourceStatusV22.FAILURE_UNAVAILABLE: ToolErrorCode.SOURCE_UNAVAILABLE,
    ReadSourceStatusV22.FAILURE_TIMEOUT: ToolErrorCode.SOURCE_TIMEOUT,
    ReadSourceStatusV22.FAILURE_SCHEMA: ToolErrorCode.SOURCE_SCHEMA_INVALID,
}


class RealCaptureSnapshotBackendV225:
    """Serve captured records only; never inspect truth or a physical alias map."""

    def __init__(self, *, run_id: str, capture: RealFaultOpaqueCaptureV1) -> None:
        if len(run_id) != 32 or any(character not in "0123456789abcdef" for character in run_id):
            raise ValueError("snapshot backend run ID is invalid")
        self.run_id = run_id
        self.capture = RealFaultOpaqueCaptureV1.model_validate(
            capture.model_dump(mode="python")
        )
        self.authority = build_fake_read_authority()
        self.semantic_action_count = 0
        self.target_equivalent_read_count = 0
        self.duplicate_request_count = 0
        self._request_digests: set[str] = set()
        self._requested_targets: list[tuple[str, ...]] = []

    @property
    def requested_targets(self) -> tuple[tuple[str, ...], ...]:
        return tuple(self._requested_targets)

    def execute(self, request: ReadToolRequest) -> BackendResult:
        request = revalidate_read_tool_request(request)
        if request.run_id != self.run_id:
            raise ValueError("snapshot backend request has another run ID")
        if request.normalized_request_sha256 in self._request_digests:
            self.duplicate_request_count += 1
            raise ReadBackendFailure(ToolErrorCode.DUPLICATE_REQUEST)
        targets = (
            (request.service,)
            if isinstance(request, (QueryMetricsRequest, SearchLogsRequest, TraceNeighborhoodRequest))
            else request.services
        )
        if not set(targets).issubset(set(self.capture.candidate_aliases)):
            raise ValueError("snapshot backend request target is outside opaque candidates")
        self._request_digests.add(request.normalized_request_sha256)
        self._requested_targets.append(targets)
        self.semantic_action_count += 1
        self.target_equivalent_read_count += len(targets)
        self._raise_source_failure(_source(request))
        if isinstance(request, QueryMetricsRequest):
            return self._metrics(request)
        if isinstance(request, SearchLogsRequest):
            return self._logs(request)
        if isinstance(request, TraceNeighborhoodRequest):
            return self._traces(request)
        if isinstance(request, InspectServiceRuntimeRequest):
            return self._runtime(request)
        return self._resources(request)

    def _raise_source_failure(self, source: EvidenceSourceV22) -> None:
        status = next(
            (
                item.status
                for item in self.capture.capture.source_failures
                if item.source is source
            ),
            None,
        )
        if status is not None:
            raise ReadBackendFailure(_FAILURE[status])

    def _metrics(self, request: QueryMetricsRequest) -> BackendResult:
        by_kind = {
            item.metric_kind: item
            for item in self.capture.capture.metrics
            if item.service == request.service
        }
        records = tuple(
            MetricRecord(
                service=request.service,
                metric_kind=_METRIC_KIND[kind.value],
                value=item.value,
                unit=METRIC_UNIT_BY_KIND[_METRIC_KIND[kind.value]],
                sample_count=item.sample_count,
            )
            for kind in request.metric_kinds
            if (item := by_kind.get(MetricKindV22(kind.value))) is not None
            and item.value is not None
        )
        return BackendResult(records=records[: request.max_results])

    def _logs(self, request: SearchLogsRequest) -> BackendResult:
        selected = tuple(
            DiagnosticLogRecord(
                observed_at=item.observed_at,
                service=item.service,
                severity=LogSeverity(item.severity),
                message=item.message,
            )
            for item in self.capture.capture.logs
            if item.service == request.service
        )
        return BackendResult(
            records=selected[: request.max_records],
            truncated=len(selected) > request.max_records,
        )

    def _traces(self, request: TraceNeighborhoodRequest) -> BackendResult:
        selected = tuple(
            TraceNeighborhoodRecord(
                anchor_service=request.service,
                service_path=item.service_path,
                relationship=(
                    SpanRelationship.ROOT
                    if item.parent_service is None
                    else SpanRelationship.CHILD
                ),
                service=item.service,
                parent_service=item.parent_service,
                operation=item.operation,
                status=SpanStatus(item.status.value),
                duration_ms=item.duration_ms,
                first_error_location=item.first_error_location,
            )
            for item in self.capture.capture.traces
            if item.service == request.service or item.parent_service == request.service
        )
        return BackendResult(
            records=selected[: request.max_spans],
            truncated=len(selected) > request.max_spans,
        )

    def _runtime(self, request: InspectServiceRuntimeRequest) -> BackendResult:
        by_service = {item.service: item for item in self.capture.capture.runtime}
        if any(target not in by_service for target in request.services):
            raise ReadBackendFailure(ToolErrorCode.SOURCE_SCHEMA_INVALID)
        return BackendResult(
            records=tuple(
                RuntimeRecord(
                    logical_service=target,
                    owned_container_present=by_service[target].state is not RuntimeStateV22.ABSENT,
                    state=RuntimeState(by_service[target].state.value),
                    health=HealthState.HEALTHY if by_service[target].healthy else HealthState.UNHEALTHY,
                    restart_count=by_service[target].restart_count,
                    exit_code=None,
                    endpoint_probe_performed=False,
                    endpoint_state=EndpointState.UNKNOWN,
                )
                for target in request.services
            )
        )

    def _resources(self, request: InspectResourceUsageRequest) -> BackendResult:
        if request.sampling_window_seconds != 10 or request.sample_count != 5:
            raise ReadBackendFailure(ToolErrorCode.SOURCE_SCHEMA_INVALID)
        by_service = {item.service: item for item in self.capture.capture.resources}
        if any(target not in by_service for target in request.services):
            raise ReadBackendFailure(ToolErrorCode.SOURCE_SCHEMA_INVALID)
        return BackendResult(
            records=tuple(
                ResourceUsageRecord(
                    logical_service=target,
                    sampling_window_seconds=by_service[target].sampling_window_seconds,
                    samples=tuple(
                        ResourceSample(
                            offset_ms=item.offset_ms,
                            cpu_percent=item.cpu_percent,
                            memory_bytes=item.memory_bytes,
                        )
                        for item in by_service[target].samples
                    ),
                    memory_slope_bytes_per_second=by_service[target].memory_slope_bytes_per_second,
                )
                for target in request.services
            )
        )


def _source(request: ReadToolRequest) -> EvidenceSourceV22:
    if isinstance(request, QueryMetricsRequest):
        return EvidenceSourceV22.METRICS
    if isinstance(request, SearchLogsRequest):
        return EvidenceSourceV22.LOGS
    if isinstance(request, TraceNeighborhoodRequest):
        return EvidenceSourceV22.TRACES
    if isinstance(request, InspectServiceRuntimeRequest):
        return EvidenceSourceV22.RUNTIME
    return EvidenceSourceV22.RESOURCES


__all__ = ("RealCaptureSnapshotBackendV225",)
