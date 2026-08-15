from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from ecomsre.dta_v2.tool_contracts import (
    MetricKind,
    QueryMetricsRequest,
    SearchLogsRequest,
    TraceNeighborhoodRequest,
    build_query_metrics_request,
    build_search_logs_request,
    build_trace_neighborhood_request,
)


RUN_ID = "1" * 32
START = datetime(2026, 8, 16, 1, 0, tzinfo=timezone.utc)
END = START + timedelta(minutes=5)


def test_request_builder_normalizes_order_and_binds_digest() -> None:
    first = build_query_metrics_request(
        run_id=RUN_ID,
        service=" payment ",
        started_at=START,
        ended_at=END,
        metric_kinds=(MetricKind.LATENCY_P95_MS, MetricKind.ERROR_RATE),
        max_results=6,
    )
    second = build_query_metrics_request(
        run_id=RUN_ID,
        service="payment",
        started_at=START,
        ended_at=END,
        metric_kinds=(MetricKind.ERROR_RATE, MetricKind.LATENCY_P95_MS),
        max_results=6,
    )

    assert first == second
    assert first.normalized_request_sha256 == second.normalized_request_sha256
    assert first.metric_kinds == (MetricKind.ERROR_RATE, MetricKind.LATENCY_P95_MS)

    with pytest.raises(ValidationError, match="digest"):
        QueryMetricsRequest.model_validate(
            {**first.model_dump(), "normalized_request_sha256": "0" * 64}
        )


def test_requests_are_strict_bounded_and_run_bound() -> None:
    with pytest.raises(ValidationError):
        build_search_logs_request(
            run_id=RUN_ID,
            service="payment",
            started_at=START,
            ended_at=END,
            max_records=21,
        )

    with pytest.raises(ValidationError):
        SearchLogsRequest.model_validate(
            {
                **build_search_logs_request(
                    run_id=RUN_ID,
                    service="payment",
                    started_at=START,
                    ended_at=END,
                    max_records=5,
                ).model_dump(mode="json"),
                "unexpected": True,
            }
        )

    with pytest.raises(ValidationError, match="window"):
        build_trace_neighborhood_request(
            run_id=RUN_ID,
            service="payment",
            started_at=END,
            ended_at=START,
            max_spans=5,
        )


def test_trace_request_has_no_raw_query_or_identity_fields() -> None:
    request = build_trace_neighborhood_request(
        run_id=RUN_ID,
        service="checkout",
        started_at=START,
        ended_at=END,
        max_spans=20,
    )
    assert isinstance(request, TraceNeighborhoodRequest)
    fields = set(type(request).model_fields)
    assert "trace_id" not in fields
    assert "container_id" not in fields
    assert "query" not in fields
    assert "url" not in fields
