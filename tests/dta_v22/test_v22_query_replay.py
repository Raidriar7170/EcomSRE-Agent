from __future__ import annotations

from datetime import datetime, timezone
from datetime import timedelta

import pytest
from pydantic import ValidationError

from ecomsre.dta_v2.v22.action_catalog import (
    StaticTopologyV22,
    build_action_catalog_v22,
    build_default_tool_capability_registry_v22,
)
from ecomsre.dta_v2.v22.read_contracts import (
    ChangeCategoryV22,
    EvidenceSourceV22,
    LogRecordV22,
    MetricFactV22,
    MetricKindV22,
    MetricSupportStatusV22,
    MetricUnitV22,
    RecentChangeRecordV22,
    ReadSourceStatusV22,
    ResourceSampleV22,
    ResourceUsageRecordV22,
    RolloutStateV22,
    RuntimeRecordV22,
    RuntimeStateV22,
    SpanStatusV22,
    TraceSpanV22,
)
from ecomsre.dta_v2.v22.replay import (
    QuerySpecificReplayBackendV22,
    ReplayCaptureV22,
    ReplaySourceFailureV22,
)


NOW = datetime(2026, 8, 19, 12, 0, tzinfo=timezone.utc)
TOPOLOGY = StaticTopologyV22.build(
    services=("ad", "checkout", "frontend", "payment", "shipping"),
    edges=(
        ("frontend", "checkout"),
        ("checkout", "payment"),
        ("checkout", "shipping"),
    ),
)


def _catalog():
    return build_action_catalog_v22(
        candidate_services=("ad", "checkout", "payment", "shipping"),
        topology=TOPOLOGY,
        capability_registry=build_default_tool_capability_registry_v22(),
        executed_action_ids=(),
        remaining_budget=30.0,
    )


def _action(source: EvidenceSourceV22, target: str):
    return next(
        item
        for item in _catalog().actions
        if item.source is source and item.target_services == (target,)
    )


def _span(
    *,
    service: str,
    parent_service: str | None,
    path: tuple[str, ...],
    operation: str,
    first_error: bool = False,
    observed_at: datetime = NOW,
) -> TraceSpanV22:
    return TraceSpanV22(
        schema_version="dta-v22.trace-span.v1",
        observed_at=observed_at,
        service_path=path,
        service=service,
        parent_service=parent_service,
        operation=operation,
        status=SpanStatusV22.ERROR if first_error else SpanStatusV22.OK,
        duration_ms=25.0 if first_error else 5.0,
        first_error_location=first_error,
    )


def _capture(*, logs: tuple[LogRecordV22, ...] = ()) -> ReplayCaptureV22:
    return ReplayCaptureV22(
        schema_version="dta-v22.replay-capture.v1",
        captured_at=NOW,
        metrics=(
            MetricFactV22(
                schema_version="dta-v22.metric-fact.v1",
                service="payment",
                metric_kind=MetricKindV22.ERROR_RATE,
                support_status=MetricSupportStatusV22.UNSUPPORTED,
                sample_count=0,
                value=None,
                unit=MetricUnitV22.RATIO,
                window_started_at=NOW - timedelta(seconds=300),
                window_ended_at=NOW,
            ),
        ),
        logs=logs,
        traces=(
            _span(
                service="checkout",
                parent_service="frontend",
                path=("frontend", "checkout"),
                operation="POST /checkout",
            ),
            _span(
                service="payment",
                parent_service="checkout",
                path=("frontend", "checkout", "payment"),
                operation="Charge",
                first_error=True,
            ),
            _span(
                service="shipping",
                parent_service="checkout",
                path=("frontend", "checkout", "shipping"),
                operation="Quote",
            ),
            _span(
                service="ad",
                parent_service=None,
                path=("ad",),
                operation="ServeAd",
            ),
        ),
        runtime=(
            RuntimeRecordV22(
                schema_version="dta-v22.runtime-record.v1",
                service="payment",
                state=RuntimeStateV22.RUNNING,
                healthy=True,
                restart_count=0,
            ),
        ),
        resources=(),
        changes=(
            RecentChangeRecordV22(
                schema_version="dta-v22.recent-change-record.v1",
                opaque_change_id="chg_0123456789abcdef",
                service="payment",
                observed_at=NOW,
                category=ChangeCategoryV22.CONFIGURATION,
                rollout_state=RolloutStateV22.COMPLETED,
                revision_digest="2" * 64,
            ),
        ),
        source_failures=(),
    )


def test_empty_logs_are_success_empty_not_source_unavailable() -> None:
    result = QuerySpecificReplayBackendV22(_capture()).execute(
        _action(EvidenceSourceV22.LOGS, "payment")
    )

    assert result.status is ReadSourceStatusV22.SUCCESS_EMPTY
    assert result.records == ()


def test_source_failure_is_distinct_from_success_empty() -> None:
    capture = _capture().model_copy(
        update={
            "source_failures": (
                ReplaySourceFailureV22(
                    schema_version="dta-v22.replay-source-failure.v1",
                    source=EvidenceSourceV22.LOGS,
                    status=ReadSourceStatusV22.FAILURE_UNAVAILABLE,
                ),
            )
        }
    )
    result = QuerySpecificReplayBackendV22(capture).execute(
        _action(EvidenceSourceV22.LOGS, "payment")
    )

    assert result.status is ReadSourceStatusV22.FAILURE_UNAVAILABLE
    assert result.records == ()


@pytest.mark.parametrize(
    "status",
    (
        ReadSourceStatusV22.FAILURE_TIMEOUT,
        ReadSourceStatusV22.FAILURE_SCHEMA,
    ),
)
def test_timeout_and_schema_failures_remain_typed(status: ReadSourceStatusV22) -> None:
    capture = _capture().model_copy(
        update={
            "source_failures": (
                ReplaySourceFailureV22(
                    schema_version="dta-v22.replay-source-failure.v1",
                    source=EvidenceSourceV22.LOGS,
                    status=status,
                ),
            )
        }
    )

    result = QuerySpecificReplayBackendV22(capture).execute(
        _action(EvidenceSourceV22.LOGS, "payment")
    )
    assert result.status is status
    assert result.records == ()


def test_zero_metric_samples_are_explicitly_unsupported() -> None:
    result = QuerySpecificReplayBackendV22(_capture()).execute(
        _action(EvidenceSourceV22.METRICS, "payment")
    )

    error_rate = next(
        item
        for item in result.records
        if isinstance(item, MetricFactV22)
        and item.metric_kind is MetricKindV22.ERROR_RATE
    )
    assert error_rate.sample_count == 0
    assert error_rate.support_status is MetricSupportStatusV22.UNSUPPORTED
    assert error_rate.value is None

    with pytest.raises(ValidationError, match="zero-sample"):
        MetricFactV22(
            schema_version="dta-v22.metric-fact.v1",
            service="payment",
            metric_kind=MetricKindV22.ERROR_RATE,
            support_status=MetricSupportStatusV22.SUPPORTED,
            sample_count=0,
            value=0.0,
            unit=MetricUnitV22.RATIO,
            window_started_at=NOW - timedelta(seconds=300),
            window_ended_at=NOW,
        )


def test_metric_domains_and_duplicate_capture_keys_fail_closed() -> None:
    with pytest.raises(ValidationError, match="error rate"):
        MetricFactV22(
            schema_version="dta-v22.metric-fact.v1",
            service="payment",
            metric_kind=MetricKindV22.ERROR_RATE,
            support_status=MetricSupportStatusV22.SUPPORTED,
            sample_count=3,
            value=2.0,
            unit=MetricUnitV22.RATIO,
            window_started_at=NOW - timedelta(seconds=300),
            window_ended_at=NOW,
        )


def test_metric_window_must_match_the_canonical_query_window() -> None:
    capture = _capture()
    wrong_window = capture.metrics[0].model_copy(
        update={"window_started_at": NOW - timedelta(seconds=299)}
    )
    mismatched = ReplayCaptureV22.model_validate(
        {
            **capture.model_dump(mode="python"),
            "metrics": (wrong_window,),
        }
    )

    result = QuerySpecificReplayBackendV22(mismatched).execute(
        _action(EvidenceSourceV22.METRICS, "payment")
    )
    assert result.status is ReadSourceStatusV22.FAILURE_SCHEMA
    assert result.records == ()
    capture = _capture()
    with pytest.raises(ValidationError, match="duplicate metric"):
        ReplayCaptureV22.model_validate(
            {
                **capture.model_dump(mode="python"),
                "metrics": (capture.metrics[0], capture.metrics[0]),
            }
        )


def test_trace_targets_receive_distinct_bounded_connected_neighborhoods() -> None:
    backend = QuerySpecificReplayBackendV22(_capture())
    payment = backend.execute(_action(EvidenceSourceV22.TRACES, "payment"))
    shipping = backend.execute(_action(EvidenceSourceV22.TRACES, "shipping"))

    assert payment.status is ReadSourceStatusV22.SUCCESS_NONEMPTY
    assert shipping.status is ReadSourceStatusV22.SUCCESS_NONEMPTY
    assert {item.operation for item in payment.records} == {"Charge"}
    assert {item.operation for item in shipping.records} == {"Quote"}
    payment_span = payment.records[0]
    assert isinstance(payment_span, TraceSpanV22)
    assert payment_span.parent_service == "checkout"
    assert payment_span.service_path == ("frontend", "checkout", "payment")
    assert payment_span.first_error_location is True


def test_wrong_trace_target_cannot_receive_the_entire_fixture() -> None:
    capture = _capture()
    result = QuerySpecificReplayBackendV22(capture).execute(
        _action(EvidenceSourceV22.TRACES, "ad")
    )

    assert 0 < len(result.records) < len(capture.traces)
    assert {item.service for item in result.records} == {"ad"}
    assert {item.operation for item in result.records} == {"ServeAd"}


def test_trace_lookback_is_enforced_and_first_error_requires_error_status() -> None:
    capture = _capture()
    old = _span(
        service="payment",
        parent_service="checkout",
        path=("frontend", "checkout", "payment"),
        operation="OldCharge",
        observed_at=NOW - timedelta(seconds=301),
    )
    temporal = ReplayCaptureV22.model_validate(
        {
            **capture.model_dump(mode="python"),
            "traces": (*capture.traces, old),
        }
    )
    result = QuerySpecificReplayBackendV22(temporal).execute(
        _action(EvidenceSourceV22.TRACES, "payment")
    )
    assert "OldCharge" not in {item.operation for item in result.records}

    with pytest.raises(ValidationError, match="first-error"):
        TraceSpanV22(
            schema_version="dta-v22.trace-span.v1",
            observed_at=NOW,
            service_path=("payment",),
            service="payment",
            parent_service=None,
            operation="Charge",
            status=SpanStatusV22.OK,
            duration_ms=1.0,
            first_error_location=True,
        )


def test_resource_schedule_is_inside_the_bound_canonical_window() -> None:
    with pytest.raises(ValidationError, match="sampling window"):
        ResourceUsageRecordV22(
            schema_version="dta-v22.resource-usage-record.v1",
            service="payment",
            sampling_window_seconds=10,
            samples=tuple(
                ResourceSampleV22(
                    offset_ms=30000,
                    cpu_percent=1.0,
                    memory_bytes=100,
                )
                for _ in range(5)
            ),
            memory_slope_bytes_per_second=0.0,
        )


def test_resource_query_accepts_the_exact_runtime_owned_schedule() -> None:
    capture = _capture()
    record = ResourceUsageRecordV22(
        schema_version="dta-v22.resource-usage-record.v1",
        service="payment",
        sampling_window_seconds=10,
        samples=tuple(
            ResourceSampleV22(
                offset_ms=offset,
                cpu_percent=1.0,
                memory_bytes=100 + index,
            )
            for index, offset in enumerate((0, 2500, 5000, 7500, 10000))
        ),
        memory_slope_bytes_per_second=0.4,
    )
    with_resource = ReplayCaptureV22.model_validate(
        {
            **capture.model_dump(mode="python"),
            "resources": (record,),
        }
    )

    result = QuerySpecificReplayBackendV22(with_resource).execute(
        _action(EvidenceSourceV22.RESOURCES, "payment")
    )
    assert result.status is ReadSourceStatusV22.SUCCESS_NONEMPTY
    assert result.records == (record,)


def test_recent_changes_are_query_specific_and_schema_closed() -> None:
    result = QuerySpecificReplayBackendV22(_capture()).execute(
        _action(EvidenceSourceV22.CHANGES, "payment")
    )

    assert result.status is ReadSourceStatusV22.SUCCESS_NONEMPTY
    assert len(result.records) == 1
    assert isinstance(result.records[0], RecentChangeRecordV22)
    assert result.records[0].service == "payment"
