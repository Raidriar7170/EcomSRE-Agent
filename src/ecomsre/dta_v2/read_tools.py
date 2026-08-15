"""Bounded dispatcher and replay/fake backend for DTA v2 read tools."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timezone
import time
from typing import Protocol

from ecomsre.dta_v2.evidence_store import (
    CanonicalRequestEnvelope,
    EvidenceStoreSnapshot,
    build_canonical_request_envelope,
    build_evidence_store_snapshot,
)
from ecomsre.dta_v2.tool_contracts import (
    DiagnosticLogRecord,
    EndpointState,
    HealthState,
    InspectResourceUsageRequest,
    InspectServiceRuntimeRequest,
    LogSeverity,
    MetricRecord,
    METRIC_UNIT_BY_KIND,
    ObservationStatus,
    QueryMetricsRequest,
    ReadToolObservation,
    ReadToolRequest,
    ReadAuthorityContext,
    ResourceSample,
    ResourceUsageRecord,
    RuntimeRecord,
    RuntimeState,
    SearchLogsRequest,
    SpanRelationship,
    SpanStatus,
    ToolCounters,
    ToolErrorCode,
    ToolResultRecord,
    TraceNeighborhoodRecord,
    TraceNeighborhoodRequest,
    TruthIsolationError,
    assert_truth_isolated,
    build_fake_read_authority,
    build_read_tool_observation,
    revalidate_read_tool_request,
    validate_results_for_request,
    validate_truncation_for_request,
)


@dataclass(frozen=True, slots=True)
class BackendResult:
    records: tuple[ToolResultRecord, ...]
    truncated: bool = False


class ReadBackendFailure(RuntimeError):
    def __init__(self, error_code: ToolErrorCode) -> None:
        super().__init__(error_code.value)
        self.error_code = error_code


class ReadBackend(Protocol):
    @property
    def authority(self) -> ReadAuthorityContext: ...

    def execute(self, request: ReadToolRequest) -> BackendResult: ...


class DispatchLimitExceeded(RuntimeError):
    pass


class InvestigationReadTools:
    """One run-local, max-four, no-repeat read-tool dispatcher."""

    def __init__(self, *, run_id: str, backend: ReadBackend) -> None:
        self.run_id = run_id
        self.backend = backend
        self.authority = ReadAuthorityContext.model_validate(
            backend.authority.model_dump()
        )
        self._observations: list[ReadToolObservation] = []
        self._request_digests: set[str] = set()
        self._request_envelopes: dict[str, CanonicalRequestEnvelope] = {}
        self._backend_call_count = 0
        self._success_count = 0
        self._failure_count = 0

    def dispatch(self, request: ReadToolRequest) -> ReadToolObservation:
        try:
            request = revalidate_read_tool_request(request)
        except (TypeError, ValueError) as error:
            raise ValueError("read-tool request failed exact boundary revalidation") from error
        if request.run_id != self.run_id:
            raise ValueError("read-tool request is bound to another run")
        if len(self._observations) >= 4:
            raise DispatchLimitExceeded("DTA v2 read-tool dispatch limit is four")

        ordinal = len(self._observations) + 1
        started_wall = _utc_now()
        started_monotonic = time.monotonic_ns()
        error_code: ToolErrorCode | None = None
        records: tuple[ToolResultRecord, ...] = ()
        truncated = False

        if request.normalized_request_sha256 in self._request_digests:
            error_code = ToolErrorCode.DUPLICATE_REQUEST
        else:
            self._request_digests.add(request.normalized_request_sha256)
            self._request_envelopes[request.normalized_request_sha256] = (
                build_canonical_request_envelope(request)
            )
            self._backend_call_count += 1
            try:
                result = self.backend.execute(request)
                records = validate_results_for_request(request, result.records)
                assert_truth_isolated(
                    [item.model_dump(mode="json") for item in records]
                )
                truncated = result.truncated
                validate_truncation_for_request(request, records, truncated)
            except ReadBackendFailure as error:
                error_code = (
                    ToolErrorCode.INTERNAL_CONTRACT_VIOLATION
                    if error.error_code is ToolErrorCode.DUPLICATE_REQUEST
                    else error.error_code
                )
            except TimeoutError:
                error_code = ToolErrorCode.SOURCE_TIMEOUT
            except TruthIsolationError:
                error_code = ToolErrorCode.TRUTH_ISOLATION_VIOLATION
            except (TypeError, ValueError):
                error_code = ToolErrorCode.SOURCE_SCHEMA_INVALID
            except Exception:
                error_code = ToolErrorCode.INTERNAL_CONTRACT_VIOLATION

        ended_wall = _utc_now()
        latency_ms = max(0, (time.monotonic_ns() - started_monotonic) // 1_000_000)
        if error_code is None:
            try:
                observation = self._build(
                    request=request,
                    ordinal=ordinal,
                    records=records,
                    truncated=truncated,
                    started_wall=started_wall,
                    ended_wall=ended_wall,
                    latency_ms=latency_ms,
                    error_code=None,
                )
            except TruthIsolationError:
                error_code = ToolErrorCode.TRUTH_ISOLATION_VIOLATION
            except ValueError:
                error_code = ToolErrorCode.SOURCE_SCHEMA_INVALID
            else:
                self._success_count += 1
                self._observations.append(observation)
                return observation

        observation = self._build(
            request=request,
            ordinal=ordinal,
            records=(),
            truncated=False,
            started_wall=started_wall,
            ended_wall=ended_wall,
            latency_ms=latency_ms,
            error_code=error_code,
        )
        self._failure_count += 1
        self._observations.append(observation)
        return observation

    def snapshot(self) -> EvidenceStoreSnapshot:
        return build_evidence_store_snapshot(
            run_id=self.run_id,
            authority=self.authority,
            request_envelopes=tuple(self._request_envelopes.values()),
            observations=tuple(self._observations),
        )

    def _build(
        self,
        *,
        request: ReadToolRequest,
        ordinal: int,
        records: tuple[ToolResultRecord, ...],
        truncated: bool,
        started_wall: object,
        ended_wall: object,
        latency_ms: int,
        error_code: ToolErrorCode | None,
    ) -> ReadToolObservation:
        from datetime import datetime

        assert isinstance(started_wall, datetime)
        assert isinstance(ended_wall, datetime)
        success_count = self._success_count + (error_code is None)
        failure_count = self._failure_count + (error_code is not None)
        return build_read_tool_observation(
            request=request,
            authority=self.authority,
            duplicate_of_request_sha256=(
                request.normalized_request_sha256
                if error_code is ToolErrorCode.DUPLICATE_REQUEST
                else None
            ),
            status=(
                ObservationStatus.SUCCESS
                if error_code is None
                else ObservationStatus.FAILURE
            ),
            error_code=error_code,
            results=records,
            truncated=truncated,
            observed_at_start=started_wall,
            observed_at_end=ended_wall,
            monotonic_latency_ms=latency_ms,
            counters=ToolCounters(
                dispatch_ordinal=ordinal,
                backend_call_count=self._backend_call_count,
                success_count=success_count,
                failure_count=failure_count,
            ),
        )


def _utc_now():
    from datetime import datetime

    return datetime.now(timezone.utc)


class FakeReadBackend:
    """Deterministic fake/replay backend used only behind production contracts."""

    def __init__(
        self,
        *,
        failure: ToolErrorCode | None = None,
        log_message: str = "upstream diagnostic error",
    ) -> None:
        self.failure = failure
        self.log_message = log_message
        self.call_count = 0
        self.authority = build_fake_read_authority()

    @classmethod
    def healthy(cls) -> FakeReadBackend:
        return cls()

    @classmethod
    def failing(cls, error_code: ToolErrorCode) -> FakeReadBackend:
        return cls(failure=error_code)

    @classmethod
    def with_log_message(cls, message: str) -> FakeReadBackend:
        return cls(log_message=message)

    def execute(self, request: ReadToolRequest) -> BackendResult:
        self.call_count += 1
        if self.failure is not None:
            raise ReadBackendFailure(self.failure)
        if isinstance(request, QueryMetricsRequest):
            return BackendResult(
                tuple(
                    MetricRecord(
                        service=request.service,
                        metric_kind=kind,
                        value=float(index + 1),
                        unit=METRIC_UNIT_BY_KIND[kind],
                        sample_count=3,
                    )
                    for index, kind in enumerate(request.metric_kinds)
                )[: request.max_results]
            )
        if isinstance(request, SearchLogsRequest):
            return BackendResult(
                (
                    DiagnosticLogRecord(
                        observed_at=request.ended_at,
                        service=request.service,
                        severity=LogSeverity.ERROR,
                        message=self.log_message,
                    ),
                )
            )
        if isinstance(request, TraceNeighborhoodRequest):
            return BackendResult(
                (
                    TraceNeighborhoodRecord(
                        anchor_service=request.service,
                        service_path=("frontend", request.service),
                        relationship=SpanRelationship.CHILD,
                        service=request.service,
                        parent_service="frontend",
                        operation="request",
                        status=SpanStatus.ERROR,
                        duration_ms=12.5,
                        first_error_location=True,
                    ),
                )
            )
        if isinstance(request, InspectServiceRuntimeRequest):
            return BackendResult(
                tuple(
                    RuntimeRecord(
                        logical_service=service,
                        owned_container_present=True,
                        state=RuntimeState.RUNNING,
                        health=HealthState.HEALTHY,
                        restart_count=0,
                        exit_code=None,
                        endpoint_probe_performed=False,
                        endpoint_state=EndpointState.UNKNOWN,
                    )
                    for service in request.services[: request.max_results]
                )
            )
        if isinstance(request, InspectResourceUsageRequest):
            return BackendResult(
                tuple(
                    ResourceUsageRecord(
                        logical_service=service,
                        sampling_window_seconds=request.sampling_window_seconds,
                        samples=tuple(
                            ResourceSample(
                                offset_ms=(request.sampling_window_seconds * 1000 * index)
                                // (request.sample_count - 1),
                                cpu_percent=1.0 + index,
                                memory_bytes=1_000_000 + (index * 1_000),
                            )
                            for index in range(request.sample_count)
                        ),
                        memory_slope_bytes_per_second=1000.0,
                    )
                    for service in request.services
                )
            )
        raise TypeError("unsupported DTA v2 read-tool request")
