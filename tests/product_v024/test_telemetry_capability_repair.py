from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json
from pathlib import Path

import httpx
import pytest

from ecomsre.dta_v2.v22.action_catalog import (
    EvidenceActionV22,
    StaticTopologyV22,
    build_action_catalog_v22,
    build_tool_capability_registry_v22,
)
from ecomsre.dta_v2.v22.read_contracts import (
    EvidenceSourceV22,
    METRIC_UNIT_BY_KIND_V22,
    MetricFactV22,
    MetricKindV22,
    MetricSupportStatusV22,
    ReadSourceStatusV22,
)
from ecomsre.product.connectors.base import (
    ConnectorQueryContextV1,
    ConnectorQueryResultV1,
    ConnectorWindowV1,
)
from ecomsre.product.connectors.credentials import CredentialResolverV1
from ecomsre.product.connectors.prometheus import PrometheusConnectorV1
from ecomsre.product.contracts import ConnectorConfigV1
from ecomsre.product.incidents.read_backend import _combine_results, _result_limitation


ROOT = Path(__file__).resolve().parents[2]
WINDOW = ConnectorWindowV1(
    started_at=datetime(2026, 9, 2, 0, 0, tzinfo=UTC),
    ended_at=datetime(2026, 9, 2, 0, 5, tzinfo=UTC),
)


def _metrics_action() -> EvidenceActionV22:
    catalog = build_action_catalog_v22(
        candidate_services=("checkout",),
        topology=StaticTopologyV22.build(services=("checkout",), edges=()),
        capability_registry=build_tool_capability_registry_v22(),
        executed_action_ids=(),
        remaining_budget=100.0,
    )
    return next(
        action
        for action in catalog.registry_actions
        if action.source is EvidenceSourceV22.METRICS
    )


def _resources_action() -> EvidenceActionV22:
    catalog = build_action_catalog_v22(
        candidate_services=("checkout",),
        topology=StaticTopologyV22.build(services=("checkout",), edges=()),
        capability_registry=build_tool_capability_registry_v22(),
        executed_action_ids=(),
        remaining_budget=100.0,
    )
    return next(
        action
        for action in catalog.registry_actions
        if action.source is EvidenceSourceV22.RESOURCES
    )


def _metric(
    kind: MetricKindV22,
    *,
    service: str = "checkout",
    window: ConnectorWindowV1 = WINDOW,
) -> MetricFactV22:
    return MetricFactV22(
        schema_version="dta-v22.metric-fact.v1",
        service=service,
        metric_kind=kind,
        support_status=MetricSupportStatusV22.SUPPORTED,
        sample_count=2,
        value=0.1,
        unit=METRIC_UNIT_BY_KIND_V22[kind],
        window_started_at=window.started_at,
        window_ended_at=window.ended_at,
    )


def _component(
    records: tuple[MetricFactV22, ...],
    *,
    requested_services: tuple[str, ...] = ("checkout",),
    covered_services: tuple[str, ...] = ("checkout",),
    window: ConnectorWindowV1 = WINDOW,
) -> ConnectorQueryResultV1:
    return ConnectorQueryResultV1.build(
        source=EvidenceSourceV22.METRICS,
        status=(
            ReadSourceStatusV22.SUCCESS_NONEMPTY
            if records
            else ReadSourceStatusV22.SUCCESS_EMPTY
        ),
        requested_services=requested_services,
        covered_services=covered_services,
        window=window,
        records=records,
        truncated=False,
        safe_error_code=None,
        latency_ms=1.0,
    )


def test_prometheus_aggregates_multiple_aliases_into_one_fact_per_kind() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        query = request.url.params["query"]
        started = float(request.url.params["start"])
        ended = float(request.url.params["end"])
        if query.startswith("errors"):
            values = (
                ("0.2", "0.4")
                if 'service="checkoutservice"' in query
                else ("0.0", "0.2")
            )
        else:
            alias_bias = 10 if 'service="checkoutservice"' in query else 0
            values = (str(alias_bias + 1), str(alias_bias + 3))
        return httpx.Response(
            200,
            json={
                "status": "success",
                "data": {
                    "resultType": "matrix",
                    "result": [
                        {
                            "metric": {},
                            "values": [
                                [started, values[0]],
                                [ended, values[1]],
                            ],
                        }
                    ],
                },
            },
        )

    connector = PrometheusConnectorV1(
        ConnectorConfigV1(
            name="prometheus",
            kind="PROMETHEUS",
            endpoint="https://prometheus.test",
            settings={
                "query_templates": {
                    "error_rate": 'errors{service="{service}"}',
                    "request_support": 'requests{service="{service}"}',
                    "latency": 'latency{service="{service}"}',
                    "cpu": 'cpu{service="{service}"}',
                    "memory": 'memory{service="{service}"}',
                }
            },
            credential_refs={},
        ),
        credential_resolver=CredentialResolverV1(environment={}),
        timeout_seconds=2,
        transport=httpx.MockTransport(handler),
    )
    action = _metrics_action()
    context = ConnectorQueryContextV1(
        environment_id="env-0123456789abcdef01234567",
        requested_services=action.target_services,
        service_aliases={"checkout": "checkout", "checkoutservice": "checkout"},
        window=WINDOW,
        maximum_records=3,
        requested_source=EvidenceSourceV22.METRICS,
        request_sha256=action.request_sha256,
        metric_kinds=action.request.metric_kinds,
    )

    component = connector.query(context)[0]
    combined = _combine_results(action=action, window=WINDOW, results=(component,))

    assert combined.status is ReadSourceStatusV22.SUCCESS_NONEMPTY
    assert len(combined.records) == 3
    assert {record.metric_kind for record in combined.records} == set(
        action.request.metric_kinds
    )
    assert all(record.sample_count == 4 for record in combined.records)


def test_observed_container_name_yields_one_checkout_resource_record() -> None:
    profile = json.loads(
        (ROOT / "examples/product/environment.otel-demo.json").read_text(
            encoding="utf-8"
        )
    )
    prometheus = profile["connector_configs"][0]
    prometheus["endpoint"] = "https://prometheus.test"
    resource_window = ConnectorWindowV1(
        started_at=WINDOW.started_at,
        ended_at=WINDOW.started_at + timedelta(seconds=10),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        query = request.url.params["query"]
        assert (
            'container_name="ecomsre-live-sandbox-v1-checkout"' in query
            or 'container_name="ecomsre-live-sandbox-v1-checkoutservice"' in query
        )
        started = float(request.url.params["start"])
        step = float(request.url.params["step"])
        values = []
        if 'container_name="ecomsre-live-sandbox-v1-checkout"' in query:
            base = 10.0 if "container_cpu_usage_nanoseconds_total" in query else 1024.0
            timestamp_jitter = (
                0.0 if "container_cpu_usage_nanoseconds_total" in query else 0.1
            )
            values = [
                [started + index * step + timestamp_jitter, str(base + index)]
                for index in range(5)
            ]
        return httpx.Response(
            200,
            json={
                "status": "success",
                "data": {
                    "resultType": "matrix",
                    "result": [] if not values else [{"metric": {}, "values": values}],
                },
            },
        )

    connector = PrometheusConnectorV1(
        ConnectorConfigV1.model_validate(prometheus),
        credential_resolver=CredentialResolverV1(environment={}),
        timeout_seconds=2,
        transport=httpx.MockTransport(handler),
    )
    action = _resources_action()
    result = connector.query(
        ConnectorQueryContextV1(
            environment_id="env-0123456789abcdef01234567",
            requested_services=("checkout",),
            service_aliases={"checkout": "checkout", "checkoutservice": "checkout"},
            window=resource_window,
            maximum_records=1,
            requested_source=EvidenceSourceV22.RESOURCES,
            request_sha256=action.request_sha256,
            sampling_window_seconds=10,
            sample_count=5,
        )
    )[0]

    assert result.status is ReadSourceStatusV22.SUCCESS_NONEMPTY
    assert result.covered_services == ("checkout",)
    assert len(result.records) == 1
    record = result.records[0]
    assert record.service == "checkout"
    assert record.sampling_window_seconds == 10
    assert len(record.samples) == 5
    assert all(sample.cpu_percent >= 0 for sample in record.samples)
    assert all(sample.memory_bytes >= 0 for sample in record.samples)


@pytest.mark.parametrize(
    ("component", "expected_status", "expected_code"),
    (
        (
            _component(
                tuple(
                    _metric(kind)
                    for kind in (
                        MetricKindV22.ERROR_RATE,
                        MetricKindV22.LATENCY_P95_MS,
                    )
                )
            ),
            ReadSourceStatusV22.FAILURE_UNAVAILABLE,
            "METRICS_MISSING_KIND",
        ),
        (
            _component(
                (
                    _metric(MetricKindV22.ERROR_RATE),
                    _metric(MetricKindV22.ERROR_RATE),
                    _metric(MetricKindV22.LATENCY_P95_MS),
                )
            ),
            ReadSourceStatusV22.FAILURE_SCHEMA,
            "METRICS_DUPLICATE_KIND",
        ),
        (
            _component(
                (
                    _metric(MetricKindV22.ERROR_RATE),
                    _metric(MetricKindV22.LATENCY_P95_MS),
                    _metric(MetricKindV22.CPU_PERCENT),
                )
            ),
            ReadSourceStatusV22.FAILURE_SCHEMA,
            "METRICS_UNEXPECTED_KIND",
        ),
        (
            _component(
                (
                    _metric(MetricKindV22.ERROR_RATE),
                    _metric(MetricKindV22.LATENCY_P95_MS),
                    _metric(MetricKindV22.REQUEST_SUPPORT),
                    _metric(MetricKindV22.CPU_PERCENT),
                )
            ),
            ReadSourceStatusV22.FAILURE_SCHEMA,
            "METRICS_RECORD_LIMIT_EXCEEDED",
        ),
        (
            _component(
                tuple(
                    _metric(kind, service="payment")
                    for kind in (
                        MetricKindV22.ERROR_RATE,
                        MetricKindV22.LATENCY_P95_MS,
                        MetricKindV22.REQUEST_SUPPORT,
                    )
                ),
                requested_services=("payment",),
                covered_services=("payment",),
            ),
            ReadSourceStatusV22.FAILURE_SCHEMA,
            "METRICS_TARGET_MISMATCH",
        ),
        (
            _component(
                tuple(
                    _metric(
                        kind,
                        window=ConnectorWindowV1(
                            started_at=WINDOW.started_at - timedelta(seconds=1),
                            ended_at=WINDOW.ended_at - timedelta(seconds=1),
                        ),
                    )
                    for kind in (
                        MetricKindV22.ERROR_RATE,
                        MetricKindV22.LATENCY_P95_MS,
                        MetricKindV22.REQUEST_SUPPORT,
                    )
                ),
                window=ConnectorWindowV1(
                    started_at=WINDOW.started_at - timedelta(seconds=1),
                    ended_at=WINDOW.ended_at - timedelta(seconds=1),
                ),
            ),
            ReadSourceStatusV22.FAILURE_SCHEMA,
            "METRICS_WINDOW_MISMATCH",
        ),
    ),
)
def test_metrics_contract_reports_precise_diagnostics(
    component: ConnectorQueryResultV1,
    expected_status: ReadSourceStatusV22,
    expected_code: str,
) -> None:
    combined = _combine_results(
        action=_metrics_action(),
        window=WINDOW,
        results=(component,),
    )

    assert combined.status is expected_status
    assert combined.safe_error_code == expected_code
    assert combined.covered_services == ()
    assert combined.records == ()
    if expected_code == "METRICS_MISSING_KIND":
        assert _result_limitation(_metrics_action(), combined) == (
            "SOURCE_METRICS_COVERAGE_GAP",
            "COVERAGE_GAP",
        )
