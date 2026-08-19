"""Query-specific deterministic replay for DTA v2.2 canonical actions."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Literal

from pydantic import Field, StrictBool, model_validator

from ecomsre.dta_v2.v22.action_catalog import EvidenceActionV22
from ecomsre.dta_v2.v22.read_contracts import (
    DtaModelV22,
    EvidenceSourceV22,
    LogRecordV22,
    METRIC_UNIT_BY_KIND_V22,
    MetricFactV22,
    MetricSupportStatusV22,
    ReadRecordV22,
    ReadSourceStatusV22,
    RecentChangeRecordV22,
    ResourceUsageRecordV22,
    RuntimeRecordV22,
    Sha256V22,
    TraceSpanV22,
    semantic_sha256_v22,
)


_FAILURE_STATUSES = {
    ReadSourceStatusV22.FAILURE_UNAVAILABLE,
    ReadSourceStatusV22.FAILURE_TIMEOUT,
    ReadSourceStatusV22.FAILURE_SCHEMA,
}


class ReplaySourceFailureV22(DtaModelV22):
    schema_version: Literal["dta-v22.replay-source-failure.v1"]
    source: EvidenceSourceV22
    status: ReadSourceStatusV22

    @model_validator(mode="after")
    def require_failure(self) -> ReplaySourceFailureV22:
        if self.status not in _FAILURE_STATUSES:
            raise ValueError("replay source failure contains a success status")
        return self


class ReplayCaptureV22(DtaModelV22):
    schema_version: Literal["dta-v22.replay-capture.v1"]
    captured_at: datetime
    metrics: tuple[MetricFactV22, ...] = Field(max_length=100)
    logs: tuple[LogRecordV22, ...] = Field(max_length=200)
    traces: tuple[TraceSpanV22, ...] = Field(max_length=400)
    runtime: tuple[RuntimeRecordV22, ...] = Field(max_length=40)
    resources: tuple[ResourceUsageRecordV22, ...] = Field(max_length=40)
    changes: tuple[RecentChangeRecordV22, ...] = Field(max_length=100)
    source_failures: tuple[ReplaySourceFailureV22, ...] = Field(max_length=6)

    @model_validator(mode="after")
    def require_capture(self) -> ReplayCaptureV22:
        if self.captured_at.tzinfo is None or self.captured_at.utcoffset() != timedelta(0):
            raise ValueError("capture timestamp must be timezone-aware UTC")
        failure_sources = tuple(item.source for item in self.source_failures)
        if failure_sources != tuple(
            sorted(set(failure_sources), key=list(EvidenceSourceV22).index)
        ):
            raise ValueError("capture source failures are not canonical and unique")
        metric_keys = tuple((item.service, item.metric_kind) for item in self.metrics)
        if len(metric_keys) != len(set(metric_keys)):
            raise ValueError("capture contains a duplicate metric service/kind")
        for values, label in (
            (tuple(item.service for item in self.runtime), "runtime service"),
            (tuple(item.service for item in self.resources), "resource service"),
            (
                tuple(item.opaque_change_id for item in self.changes),
                "opaque change ID",
            ),
        ):
            if len(values) != len(set(values)):
                raise ValueError(f"capture contains a duplicate {label}")
        return self


class ReadOutcomeV22(DtaModelV22):
    schema_version: Literal["dta-v22.read-outcome.v1"]
    action_id: str
    source: EvidenceSourceV22
    request_sha256: Sha256V22
    status: ReadSourceStatusV22
    records: tuple[ReadRecordV22, ...]
    truncated: StrictBool
    outcome_sha256: Sha256V22

    @model_validator(mode="after")
    def require_outcome(self) -> ReadOutcomeV22:
        if self.status is ReadSourceStatusV22.SUCCESS_NONEMPTY and not self.records:
            raise ValueError("nonempty read outcome has no records")
        if self.status is not ReadSourceStatusV22.SUCCESS_NONEMPTY and self.records:
            raise ValueError("empty or failed read outcome contains records")
        if self.status in _FAILURE_STATUSES and self.truncated:
            raise ValueError("failed read outcome cannot be truncated")
        expected_types: dict[EvidenceSourceV22, type[DtaModelV22]] = {
            EvidenceSourceV22.METRICS: MetricFactV22,
            EvidenceSourceV22.LOGS: LogRecordV22,
            EvidenceSourceV22.TRACES: TraceSpanV22,
            EvidenceSourceV22.RUNTIME: RuntimeRecordV22,
            EvidenceSourceV22.RESOURCES: ResourceUsageRecordV22,
            EvidenceSourceV22.CHANGES: RecentChangeRecordV22,
        }
        if any(type(item) is not expected_types[self.source] for item in self.records):
            raise ValueError("read outcome record type differs from source")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"outcome_sha256"})
        )
        if self.outcome_sha256 != expected:
            raise ValueError("read outcome digest does not bind outcome")
        return self


class QuerySpecificReplayBackendV22:
    """Filter a full capture by the selected canonical request, never by tool alone."""

    def __init__(self, capture: ReplayCaptureV22) -> None:
        self.capture = ReplayCaptureV22.model_validate(capture.model_dump(mode="python"))
        self.call_count = 0

    def execute(self, action: EvidenceActionV22) -> ReadOutcomeV22:
        action = EvidenceActionV22.model_validate(action.model_dump(mode="python"))
        self.call_count += 1
        failure = next(
            (
                item.status
                for item in self.capture.source_failures
                if item.source is action.source
            ),
            None,
        )
        if failure is not None:
            return _build_outcome(action=action, status=failure, records=())
        if action.source is EvidenceSourceV22.METRICS:
            return self._metrics(action)
        if action.source is EvidenceSourceV22.LOGS:
            return self._logs(action)
        if action.source is EvidenceSourceV22.TRACES:
            return self._traces(action)
        if action.source is EvidenceSourceV22.RUNTIME:
            return self._runtime(action)
        if action.source is EvidenceSourceV22.RESOURCES:
            return self._resources(action)
        return self._changes(action)

    def _metrics(self, action: EvidenceActionV22) -> ReadOutcomeV22:
        request = action.request
        target = request.target_services[0]
        assert request.lookback_seconds is not None
        expected_start = self.capture.captured_at - timedelta(
            seconds=request.lookback_seconds
        )
        target_facts = tuple(
            item for item in self.capture.metrics if item.service == target
        )
        if any(
            item.window_started_at != expected_start
            or item.window_ended_at != self.capture.captured_at
            for item in target_facts
        ):
            return _build_outcome(
                action=action,
                status=ReadSourceStatusV22.FAILURE_SCHEMA,
                records=(),
            )
        by_kind = {
            item.metric_kind: item
            for item in target_facts
        }
        records = tuple(
            by_kind.get(kind)
            or MetricFactV22(
                schema_version="dta-v22.metric-fact.v1",
                service=target,
                metric_kind=kind,
                support_status=MetricSupportStatusV22.UNSUPPORTED,
                sample_count=0,
                value=None,
                unit=METRIC_UNIT_BY_KIND_V22[kind],
                window_started_at=expected_start,
                window_ended_at=self.capture.captured_at,
            )
            for kind in request.metric_kinds
        )
        return _build_success(action=action, records=records)

    def _logs(self, action: EvidenceActionV22) -> ReadOutcomeV22:
        request = action.request
        assert request.lookback_seconds is not None
        assert request.max_records is not None
        earliest = self.capture.captured_at - timedelta(seconds=request.lookback_seconds)
        selected = tuple(
            sorted(
                (
                    item
                    for item in self.capture.logs
                    if item.service == request.target_services[0]
                    and earliest <= item.observed_at <= self.capture.captured_at
                ),
                key=lambda item: (item.observed_at, item.severity, item.message),
            )
        )
        return _build_success(
            action=action,
            records=selected[: request.max_records],
            truncated=len(selected) > request.max_records,
        )

    def _traces(self, action: EvidenceActionV22) -> ReadOutcomeV22:
        request = action.request
        assert request.max_spans is not None
        assert request.lookback_seconds is not None
        target = request.target_services[0]
        earliest = self.capture.captured_at - timedelta(seconds=request.lookback_seconds)
        # Radius-one means every selected edge is incident to the requested target.
        # This is connected by construction and never rewrites captured paths.
        selected = tuple(
            sorted(
                (
                    item
                    for item in self.capture.traces
                    if earliest <= item.observed_at <= self.capture.captured_at
                    and (item.service == target or item.parent_service == target)
                ),
                key=_trace_key,
            )
        )
        return _build_success(
            action=action,
            records=selected[: request.max_spans],
            truncated=len(selected) > request.max_spans,
        )

    def _runtime(self, action: EvidenceActionV22) -> ReadOutcomeV22:
        request = action.request
        by_service = {item.service: item for item in self.capture.runtime}
        selected = tuple(
            by_service[target]
            for target in request.target_services
            if target in by_service
        )
        if selected and len(selected) != len(request.target_services):
            return _build_outcome(
                action=action,
                status=ReadSourceStatusV22.FAILURE_SCHEMA,
                records=(),
            )
        return _build_success(action=action, records=selected)

    def _resources(self, action: EvidenceActionV22) -> ReadOutcomeV22:
        request = action.request
        by_service = {item.service: item for item in self.capture.resources}
        selected = tuple(
            by_service[target]
            for target in request.target_services
            if target in by_service
        )
        if not selected:
            return _build_success(action=action, records=())
        if len(selected) != len(request.target_services) or any(
            item.sampling_window_seconds != request.sampling_window_seconds
            or len(item.samples) != request.sample_count
            for item in selected
        ):
            return _build_outcome(
                action=action,
                status=ReadSourceStatusV22.FAILURE_SCHEMA,
                records=(),
            )
        return _build_success(action=action, records=selected)

    def _changes(self, action: EvidenceActionV22) -> ReadOutcomeV22:
        request = action.request
        assert request.lookback_seconds is not None
        assert request.max_records is not None
        earliest = self.capture.captured_at - timedelta(seconds=request.lookback_seconds)
        selected = tuple(
            sorted(
                (
                    item
                    for item in self.capture.changes
                    if item.service == request.target_services[0]
                    and earliest <= item.observed_at <= self.capture.captured_at
                ),
                key=lambda item: (item.observed_at, item.opaque_change_id),
            )
        )
        return _build_success(
            action=action,
            records=selected[: request.max_records],
            truncated=len(selected) > request.max_records,
        )


def _trace_key(item: TraceSpanV22) -> tuple[object, ...]:
    return (
        not item.first_error_location,
        item.status.value,
        item.service_path,
        item.service,
        item.parent_service or "",
        item.operation,
        item.duration_ms,
    )


def _build_success(
    *,
    action: EvidenceActionV22,
    records: tuple[ReadRecordV22, ...],
    truncated: bool = False,
) -> ReadOutcomeV22:
    return _build_outcome(
        action=action,
        status=(
            ReadSourceStatusV22.SUCCESS_NONEMPTY
            if records
            else ReadSourceStatusV22.SUCCESS_EMPTY
        ),
        records=records,
        truncated=truncated,
    )


def _build_outcome(
    *,
    action: EvidenceActionV22,
    status: ReadSourceStatusV22,
    records: tuple[ReadRecordV22, ...],
    truncated: bool = False,
) -> ReadOutcomeV22:
    payload: dict[str, Any] = {
        "schema_version": "dta-v22.read-outcome.v1",
        "action_id": action.action_id,
        "source": action.source,
        "request_sha256": action.request_sha256,
        "status": status,
        "records": records,
        "truncated": truncated,
    }
    draft = ReadOutcomeV22.model_construct(**payload, outcome_sha256="0" * 64)
    return ReadOutcomeV22.model_validate(
        {
            **payload,
            "outcome_sha256": semantic_sha256_v22(
                draft.model_dump(mode="json", exclude={"outcome_sha256"})
            ),
        }
    )


__all__ = (
    "QuerySpecificReplayBackendV22",
    "ReadOutcomeV22",
    "ReplayCaptureV22",
    "ReplaySourceFailureV22",
)
