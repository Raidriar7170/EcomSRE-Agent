"""Action-catalog adapter for opaque snapshot and owned live v2 read backends."""

from __future__ import annotations

from datetime import timedelta
from typing import Protocol, cast

from ecomsre.dta_v2.read_tools import ReadBackend, ReadBackendFailure
from ecomsre.dta_v2.tool_contracts import (
    DiagnosticLogRecord,
    HealthState,
    MetricKind,
    MetricRecord,
    ResourceUsageRecord,
    RuntimeRecord,
    RuntimeState,
    ToolErrorCode,
    TraceNeighborhoodRecord,
    build_inspect_resource_usage_request,
    build_inspect_service_runtime_request,
    build_query_metrics_request,
    build_search_logs_request,
    build_trace_neighborhood_request,
)
from ecomsre.dta_v2.v22.action_catalog import EvidenceActionV22
from ecomsre.dta_v2.v22.contrastive_actions_v225 import (
    ContrastiveResourceActionV225,
)
from ecomsre.dta_v2.v22.read_contracts import (
    EvidenceSourceV22,
    LogRecordV22,
    METRIC_UNIT_BY_KIND_V22,
    MetricFactV22,
    MetricKindV22,
    MetricSupportStatusV22,
    ReadSourceStatusV22,
    ResourceSampleV22,
    ResourceUsageRecordV22,
    RuntimeRecordV22,
    RuntimeStateV22,
    SpanStatusV22,
    TraceSpanV22,
    semantic_sha256_v22,
)
from ecomsre.dta_v2.v22.real_capture_backend_v225 import (
    RealCaptureSnapshotBackendV225,
)
from ecomsre.dta_v2.v22.real_fault_capture_v225 import (
    RealFaultAliasMapV1,
    RealFaultOpaqueCaptureV1,
    RealFaultSourceWindowV1,
)
from ecomsre.dta_v2.v22.replay import ReadOutcomeV22


ActionV225 = EvidenceActionV22 | ContrastiveResourceActionV225


class ActionReadBackendV225(Protocol):
    @property
    def duplicate_request_count(self) -> int: ...

    def execute(self, action: ActionV225) -> ReadOutcomeV22: ...


_STATUS = {
    ToolErrorCode.SOURCE_TIMEOUT: ReadSourceStatusV22.FAILURE_TIMEOUT,
    ToolErrorCode.SOURCE_SCHEMA_INVALID: ReadSourceStatusV22.FAILURE_SCHEMA,
    ToolErrorCode.SOURCE_UNAVAILABLE: ReadSourceStatusV22.FAILURE_UNAVAILABLE,
    ToolErrorCode.OWNERSHIP_NOT_PROVEN: ReadSourceStatusV22.FAILURE_UNAVAILABLE,
    ToolErrorCode.REMOTE_DOCKER_FORBIDDEN: ReadSourceStatusV22.FAILURE_UNAVAILABLE,
    ToolErrorCode.AMBIGUOUS_OWNED_RUNTIME: ReadSourceStatusV22.FAILURE_UNAVAILABLE,
}


def _outcome(
    *,
    action: ActionV225,
    status: ReadSourceStatusV22,
    records: tuple[object, ...],
    truncated: bool,
) -> ReadOutcomeV22:
    payload = {
        "schema_version": "dta-v22.read-outcome.v1",
        "action_id": action.action_id,
        "source": action.source,
        "request_sha256": action.request_sha256,
        "status": status,
        "records": records,
        "truncated": truncated,
    }
    draft = ReadOutcomeV22.model_construct(
        schema_version="dta-v22.read-outcome.v1",
        action_id=action.action_id,
        source=action.source,
        request_sha256=action.request_sha256,
        status=status,
        records=records,
        truncated=truncated,
        outcome_sha256="0" * 64,
    )
    return ReadOutcomeV22.model_validate(
        {
            **payload,
            "outcome_sha256": semantic_sha256_v22(
                draft.model_dump(mode="json", exclude={"outcome_sha256"})
            ),
        }
    )


class RealFaultActionReadBackendV225:
    """Translate v2.2.5 actions to bounded v2 reads and remap results to aliases."""

    def __init__(
        self,
        *,
        backend: ReadBackend,
        run_id: str,
        source_window: RealFaultSourceWindowV1,
        alias_to_backend_service: dict[str, str],
    ) -> None:
        if len(run_id) != 32:
            raise ValueError("real-fault action adapter run ID is invalid")
        if tuple(sorted(alias_to_backend_service)) != tuple(
            sorted(set(alias_to_backend_service))
        ):
            raise ValueError("real-fault action adapter aliases are invalid")
        if len(set(alias_to_backend_service.values())) != len(alias_to_backend_service):
            raise ValueError("real-fault action adapter mapping is not bijective")
        self.backend = backend
        self.run_id = run_id
        self.source_window = source_window
        self.alias_to_backend_service = dict(alias_to_backend_service)
        self.backend_service_to_alias = {
            value: key for key, value in alias_to_backend_service.items()
        }
        self.requests: list[object] = []
        self._duplicate_request_count = 0

    @classmethod
    def snapshot(
        cls, *, capture: RealFaultOpaqueCaptureV1, run_id: str
    ) -> RealFaultActionReadBackendV225:
        return cls(
            backend=RealCaptureSnapshotBackendV225(run_id=run_id, capture=capture),
            run_id=run_id,
            source_window=capture.source_window,
            alias_to_backend_service={item: item for item in capture.candidate_aliases},
        )

    @classmethod
    def live(
        cls,
        *,
        backend: ReadBackend,
        run_id: str,
        source_window: RealFaultSourceWindowV1,
        alias_map: RealFaultAliasMapV1,
    ) -> RealFaultActionReadBackendV225:
        return cls(
            backend=backend,
            run_id=run_id,
            source_window=source_window,
            alias_to_backend_service={
                item.alias: item.physical_service for item in alias_map.bindings
            },
        )

    @property
    def duplicate_request_count(self) -> int:
        underlying = getattr(self.backend, "duplicate_request_count", 0)
        return max(self._duplicate_request_count, int(underlying))

    def _physical(self, aliases: tuple[str, ...]) -> tuple[str, ...]:
        try:
            return tuple(sorted(self.alias_to_backend_service[item] for item in aliases))
        except KeyError as error:
            raise ValueError("action target is outside the frozen alias map") from error

    def _request(self, action: ActionV225):
        targets = self._physical(action.target_services)
        ended = self.source_window.captured_at
        if action.source is EvidenceSourceV22.METRICS:
            return build_query_metrics_request(
                run_id=self.run_id,
                service=targets[0],
                started_at=ended
                - timedelta(seconds=self.source_window.metrics_lookback_seconds),
                ended_at=ended,
                metric_kinds=tuple(
                    MetricKind(item.value) for item in action.request.metric_kinds
                ),
                max_results=cast(int, action.request.max_results),
            )
        if action.source is EvidenceSourceV22.LOGS:
            return build_search_logs_request(
                run_id=self.run_id,
                service=targets[0],
                started_at=ended
                - timedelta(seconds=self.source_window.logs_lookback_seconds),
                ended_at=ended,
                max_records=cast(int, action.request.max_records),
            )
        if action.source is EvidenceSourceV22.TRACES:
            return build_trace_neighborhood_request(
                run_id=self.run_id,
                service=targets[0],
                started_at=ended
                - timedelta(seconds=self.source_window.traces_lookback_seconds),
                ended_at=ended,
                max_spans=cast(int, action.request.max_spans),
            )
        if action.source is EvidenceSourceV22.RUNTIME:
            return build_inspect_service_runtime_request(
                run_id=self.run_id,
                services=targets,
                max_results=len(targets),
            )
        if action.source is EvidenceSourceV22.RESOURCES:
            return build_inspect_resource_usage_request(
                run_id=self.run_id,
                services=targets,
                sampling_window_seconds=cast(
                    int, action.request.sampling_window_seconds
                ),
                sample_count=cast(int, action.request.sample_count),
            )
        raise ValueError("v2 read tools do not expose a Changes source")

    def _alias(self, physical: str) -> str:
        try:
            return self.backend_service_to_alias[physical]
        except KeyError as error:
            raise ValueError("read result escaped the frozen physical pair") from error

    def _records(self, *, action: ActionV225, records: tuple[object, ...]):
        if action.source is EvidenceSourceV22.METRICS:
            return tuple(
                MetricFactV22(
                    schema_version="dta-v22.metric-fact.v1",
                    service=self._alias(item.service),
                    metric_kind=MetricKindV22(item.metric_kind.value),
                    support_status=(
                        MetricSupportStatusV22.SUPPORTED
                        if item.sample_count > 0
                        else MetricSupportStatusV22.UNSUPPORTED
                    ),
                    sample_count=item.sample_count,
                    value=item.value if item.sample_count > 0 else None,
                    unit=METRIC_UNIT_BY_KIND_V22[MetricKindV22(item.metric_kind.value)],
                    window_started_at=self.source_window.captured_at
                    - timedelta(seconds=self.source_window.metrics_lookback_seconds),
                    window_ended_at=self.source_window.captured_at,
                )
                for raw in records
                if isinstance(raw, MetricRecord)
                for item in (raw,)
            )
        if action.source is EvidenceSourceV22.LOGS:
            return tuple(
                LogRecordV22(
                    schema_version="dta-v22.log-record.v1",
                    observed_at=item.observed_at,
                    service=self._alias(item.service),
                    severity=item.severity.value,  # type: ignore[arg-type]
                    message=item.message,
                )
                for item in records
                if isinstance(item, DiagnosticLogRecord)
            )
        if action.source is EvidenceSourceV22.TRACES:
            output: list[TraceSpanV22] = []
            for item in records:
                if not isinstance(item, TraceNeighborhoodRecord):
                    continue
                if any(
                    value not in self.backend_service_to_alias
                    for value in item.service_path
                ) or (
                    item.parent_service is not None
                    and item.parent_service not in self.backend_service_to_alias
                ):
                    continue
                output.append(
                    TraceSpanV22(
                        schema_version="dta-v22.trace-span.v1",
                        observed_at=self.source_window.captured_at,
                        service_path=tuple(
                            self._alias(value) for value in item.service_path
                        ),
                        service=self._alias(item.service),
                        parent_service=(
                            None
                            if item.parent_service is None
                            else self._alias(item.parent_service)
                        ),
                        operation=item.operation,
                        status=SpanStatusV22(item.status.value),
                        duration_ms=item.duration_ms,
                        first_error_location=item.first_error_location,
                    )
                )
            return tuple(output)
        if action.source is EvidenceSourceV22.RUNTIME:
            return tuple(
                RuntimeRecordV22(
                    schema_version="dta-v22.runtime-record.v1",
                    service=self._alias(item.logical_service),
                    state=RuntimeStateV22(item.state.value),
                    healthy=(
                        item.state is RuntimeState.RUNNING
                        and item.health
                        in {HealthState.HEALTHY, HealthState.NOT_CONFIGURED}
                    ),
                    restart_count=item.restart_count,
                )
                for item in records
                if isinstance(item, RuntimeRecord)
            )
        return tuple(
            ResourceUsageRecordV22(
                schema_version="dta-v22.resource-usage-record.v1",
                service=self._alias(item.logical_service),
                sampling_window_seconds=item.sampling_window_seconds,
                samples=tuple(
                    ResourceSampleV22(
                        offset_ms=sample.offset_ms,
                        cpu_percent=sample.cpu_percent,
                        memory_bytes=sample.memory_bytes,
                    )
                    for sample in item.samples
                ),
                memory_slope_bytes_per_second=item.memory_slope_bytes_per_second,
            )
            for item in records
            if isinstance(item, ResourceUsageRecord)
        )

    def execute(self, action: ActionV225) -> ReadOutcomeV22:
        request = self._request(action)
        self.requests.append(request)
        try:
            result = self.backend.execute(request)
        except ReadBackendFailure as error:
            if error.error_code is ToolErrorCode.DUPLICATE_REQUEST:
                self._duplicate_request_count += 1
                raise ValueError("duplicate action-backend request") from error
            status = _STATUS.get(error.error_code, ReadSourceStatusV22.FAILURE_SCHEMA)
            return _outcome(
                action=action,
                status=status,
                records=(),
                truncated=False,
            )
        records = self._records(action=action, records=cast(tuple[object, ...], result.records))
        return _outcome(
            action=action,
            status=(
                ReadSourceStatusV22.SUCCESS_NONEMPTY
                if records
                else ReadSourceStatusV22.SUCCESS_EMPTY
            ),
            records=records,
            truncated=result.truncated,
        )


__all__ = (
    "ActionReadBackendV225",
    "ActionV225",
    "RealFaultActionReadBackendV225",
)
