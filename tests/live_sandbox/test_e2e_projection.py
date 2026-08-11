from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from ecomsre_live_sandbox.e2e_telemetry import (
    LiveLogObservation,
    LiveMetricObservation,
    LiveTraceObservation,
    build_live_a0_context,
    scan_model_projection,
)


NOW = datetime(2026, 8, 11, 12, tzinfo=timezone.utc)


def _metric(service: str, baseline_error: float, fault_error: float) -> LiveMetricObservation:
    return LiveMetricObservation(
        service_name=service,
        baseline_requests=100.0,
        baseline_errors=baseline_error,
        fault_requests=100.0,
        fault_errors=fault_error,
        baseline_p95_ms=20.0,
        fault_p95_ms=40.0,
    )


def test_live_projection_is_broad_deterministic_and_control_blind() -> None:
    context = build_live_a0_context(
        opaque_case_id="rca100-case-0001",
        window_start=NOW,
        window_end=NOW + timedelta(seconds=60),
        metrics=(
            _metric("currency", 1.0, 60.0),
            _metric("checkout", 1.0, 25.0),
            _metric("frontend", 1.0, 12.0),
            _metric("email", 1.0, 8.0),
            _metric("payment", 1.0, 4.0),
            _metric("cart", 1.0, 3.0),
        ),
        logs=(
            LiveLogObservation(observed_at=NOW, service_name="currency", severity="ERROR", body="currency request errors increased"),
            LiveLogObservation(observed_at=NOW, service_name="checkout", severity="WARN", body="checkout dependency latency"),
            LiveLogObservation(observed_at=NOW, service_name="frontend", severity="WARN", body="purchase request returned error"),
        ),
        traces=(
            LiveTraceObservation(observed_at=NOW, service_name="currency", operation="charge", status="ERROR", duration_ms=20.0),
            LiveTraceObservation(observed_at=NOW, service_name="checkout", operation="place order", status="ERROR", duration_ms=15.0),
            LiveTraceObservation(observed_at=NOW, service_name="frontend", operation="route", status="OK", duration_ms=5.0),
        ),
    )

    payload = context.model_dump(mode="json")
    assert context.task.alert_title == "Observed purchase-flow request error-rate increase"
    assert 3 <= len(context.visible_entities) <= 8
    assert [item.entity_name for item in context.visible_entities][:2] == ["checkout", "currency"]
    assert "payment" not in [item.entity_name for item in context.visible_entities]
    assert len(context.metrics.evidence) == 4
    assert len(context.logs.evidence) <= 16
    assert len(context.traces.evidence) <= 20
    assert {item.entity_ref for item in context.metrics.evidence}.issubset(
        {item.entity_ref for item in context.visible_entities}
    )
    assert {item.entity_ref for item in context.logs.evidence}.issubset(
        {item.entity_ref for item in context.visible_entities}
    )
    assert {item.entity_ref for item in context.traces.evidence}.issubset(
        {item.entity_ref for item in context.visible_entities}
    )
    assert scan_model_projection(payload) == ()


def test_live_projection_rejects_any_control_truth_marker() -> None:
    with pytest.raises(ValueError, match="control"):
        build_live_a0_context(
            opaque_case_id="rca100-case-0001",
            window_start=NOW,
            window_end=NOW + timedelta(seconds=60),
            metrics=(_metric("checkout", 1.0, 20.0),),
            logs=(
                LiveLogObservation(
                    observed_at=NOW,
                    service_name="checkout",
                    severity="ERROR",
                    body="paymentFailure.defaultVariant changed",
                ),
            ),
            traces=(LiveTraceObservation(observed_at=NOW, service_name="checkout", operation="place order", status="ERROR", duration_ms=10.0),),
        )
