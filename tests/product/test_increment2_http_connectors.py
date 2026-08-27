from __future__ import annotations

from datetime import UTC, datetime
import json

import httpx

from ecomsre.dta_v2.v22.read_contracts import (
    EvidenceSourceV22,
    LogRecordV22,
    MetricFactV22,
    ReadSourceStatusV22,
    ResourceUsageRecordV22,
    RuntimeRecordV22,
    RuntimeStateV22,
    TraceSpanV22,
)
from ecomsre.product.connectors.base import (
    ConnectorAvailabilityV1,
    ConnectorQueryContextV1,
    ConnectorWindowV1,
)
from ecomsre.product.connectors.credentials import CredentialResolverV1
from ecomsre.product.connectors.http_health import HttpHealthConnectorV1
from ecomsre.product.connectors.jaeger import JaegerConnectorV1
from ecomsre.product.connectors.opensearch import OpenSearchConnectorV1
from ecomsre.product.connectors.prometheus import PrometheusConnectorV1
from ecomsre.product.contracts import ConnectorConfigV1


WINDOW = ConnectorWindowV1(
    started_at=datetime(2026, 8, 27, 0, 0, tzinfo=UTC),
    ended_at=datetime(2026, 8, 27, 0, 5, tzinfo=UTC),
)
CONTEXT = ConnectorQueryContextV1(
    environment_id="env-0123456789abcdef01234567",
    requested_services=("payment",),
    window=WINDOW,
    maximum_records=200,
)


def test_prometheus_connector_discovers_and_normalizes_metrics_and_resources() -> None:
    seen_paths: list[str] = []
    request_fences: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_paths.append(request.url.path)
        if request.url.path == "/api/v1/label/service_name/values":
            return httpx.Response(
                200,
                json={"status": "success", "data": ["payment", "frontend"]},
            )
        if request.url.path == "/api/v1/query_range":
            query = request.url.params["query"]
            end = int(WINDOW.ended_at.timestamp())
            if query.startswith("errors"):
                values = [[end - 30, "0.01"], [end - 15, "0.02"], [end, "0.03"]]
            elif query.startswith("cpu"):
                values = [[end - 30, "10"], [end - 15, "20"], [end, "30"]]
            elif query.startswith("memory"):
                values = [[end - 30, "100"], [end - 15, "130"], [end, "160"]]
            elif query.startswith("requests"):
                values = [[end - 30, "10"], [end - 15, "11"], [end, "12"]]
            elif query.startswith("latency"):
                values = [[end - 30, "20"], [end - 15, "21"], [end, "22"]]
            else:
                raise AssertionError(f"unexpected query: {query}")
            return httpx.Response(
                200,
                json={
                    "status": "success",
                    "data": {
                        "resultType": "matrix",
                        "result": [{"metric": {"service_name": "payment"}, "values": values}],
                    },
                },
            )
        raise AssertionError(f"unexpected path: {request.url.path}")

    connector = PrometheusConnectorV1(
        ConnectorConfigV1(
            name="prometheus",
            kind="PROMETHEUS",
            endpoint="https://prometheus.test",
            settings={
                "query_templates": {
                    "error_rate": "errors{service=\"{service}\"}",
                    "request_support": "requests{service=\"{service}\"}",
                    "latency": "latency{service=\"{service}\"}",
                    "cpu": "cpu{service=\"{service}\"}",
                    "memory": "memory{service=\"{service}\"}",
                },
                "service_label": "service_name",
                "step_seconds": 15,
            },
            credential_refs={},
        ),
        credential_resolver=CredentialResolverV1(environment={}),
        timeout_seconds=2,
        transport=httpx.MockTransport(handler),
        before_request=lambda: request_fences.append("renewed"),
    )

    health = connector.verify()
    results = connector.query(CONTEXT)

    assert health.status is ConnectorAvailabilityV1.AVAILABLE
    assert health.discovered_services == ("frontend", "payment")
    assert {item.source for item in health.capabilities} == {
        EvidenceSourceV22.METRICS,
        EvidenceSourceV22.RESOURCES,
    }
    metrics = next(item for item in results if item.source is EvidenceSourceV22.METRICS)
    resources = next(
        item for item in results if item.source is EvidenceSourceV22.RESOURCES
    )
    assert metrics.status is ReadSourceStatusV22.SUCCESS_NONEMPTY
    assert all(isinstance(item, MetricFactV22) for item in metrics.records)
    assert len(metrics.records) == 5
    assert resources.status is ReadSourceStatusV22.SUCCESS_NONEMPTY
    assert isinstance(resources.records[0], ResourceUsageRecordV22)
    assert resources.records[0].sampling_window_seconds == 30
    assert "/api/v1/label/service_name/values" in seen_paths
    assert seen_paths.count("/api/v1/query_range") == 5
    assert len(request_fences) == len(seen_paths)


def test_opensearch_connector_uses_bounded_search_and_projects_only_log_fields() -> None:
    requests: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/otel-*/_search"
        body = json.loads(request.content)
        requests.append(body)
        if body["size"] == 0:
            return httpx.Response(
                200,
                json={
                    "hits": {"hits": []},
                    "aggregations": {
                        "services": {"buckets": [{"key": "payment"}]}
                    },
                },
            )
        return httpx.Response(
            200,
            json={
                "hits": {
                    "hits": [
                        {
                            "_source": {
                                "@timestamp": "2026-08-27T00:04:00Z",
                                "resource": {"service": {"name": "payment"}},
                                "severity": "ERROR",
                                "body": "payment failure observed",
                                "trace_id": "abc",
                                "scenario_truth": "must-not-project",
                            }
                        }
                    ]
                }
            },
        )

    connector = OpenSearchConnectorV1(
        ConnectorConfigV1(
            name="logs",
            kind="OPENSEARCH",
            endpoint="https://opensearch.test",
            settings={
                "index_pattern": "otel-*",
                "timestamp_field": "@timestamp",
                "service_field": "resource.service.name",
                "severity_field": "severity",
                "message_field": "body",
                "trace_id_field": "trace_id",
                "maximum_result_count": 50,
            },
            credential_refs={},
        ),
        credential_resolver=CredentialResolverV1(environment={}),
        timeout_seconds=2,
        transport=httpx.MockTransport(handler),
    )

    assert connector.verify().status is ConnectorAvailabilityV1.AVAILABLE
    result = connector.query(CONTEXT)[0]

    assert result.source is EvidenceSourceV22.LOGS
    assert result.status is ReadSourceStatusV22.SUCCESS_NONEMPTY
    assert isinstance(result.records[0], LogRecordV22)
    assert result.records[0].message == "payment failure observed"
    assert "scenario_truth" not in result.model_dump_json()
    assert requests[1]["size"] == 50
    assert "trace_id" in requests[1]["_source"]


def test_jaeger_connector_discovers_and_normalizes_causal_spans() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/services":
            return httpx.Response(200, json={"data": ["frontend", "payment"]})
        assert request.url.path == "/api/traces"
        assert request.url.params["service"] == "payment"
        assert request.url.params["tags"] == '{"deployment.environment":"test"}'
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "processes": {
                            "p1": {"resource": {"service": {"name": "frontend"}}},
                            "p2": {"resource": {"service": {"name": "payment"}}},
                        },
                        "spans": [
                            {
                                "spanID": "root",
                                "operationName": "checkout",
                                "startTime": 1787789040000000,
                                "duration": 100000,
                                "processID": "p1",
                                "references": [],
                                "tags": [],
                            },
                            {
                                "spanID": "child",
                                "operationName": "charge",
                                "startTime": 1787789040050000,
                                "duration": 50000,
                                "processID": "p2",
                                "references": [
                                    {"refType": "CHILD_OF", "spanID": "root"}
                                ],
                                "tags": [{"key": "error", "value": True}],
                            },
                        ],
                    }
                ]
            },
        )

    connector = JaegerConnectorV1(
        ConnectorConfigV1(
            name="traces",
            kind="JAEGER",
            endpoint="https://jaeger.test",
            settings={
                "limit": 10,
                "minimum_duration_ms": 0,
                "service_field_behavior": "resource.service.name",
                "tags": {"deployment.environment": "test"},
            },
            credential_refs={},
        ),
        credential_resolver=CredentialResolverV1(environment={}),
        timeout_seconds=2,
        transport=httpx.MockTransport(handler),
    )

    assert connector.verify().discovered_services == ("frontend", "payment")
    result = connector.query(CONTEXT)[0]

    assert result.source is EvidenceSourceV22.TRACES
    assert all(isinstance(item, TraceSpanV22) for item in result.records)
    child = next(item for item in result.records if item.service == "payment")
    assert child.service_path == ("frontend", "payment")
    assert child.parent_service == "frontend"
    assert child.first_error_location is True


def test_http_health_connector_distinguishes_unhealthy_from_transport_failure() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.extensions["timeout"]["read"] == 2
        if request.url.host == "healthy.test":
            return httpx.Response(200, json={"healthy": True})
        if request.url.host == "unhealthy.test":
            return httpx.Response(503, json={"healthy": False})
        raise httpx.ReadTimeout("bounded timeout", request=request)

    connector = HttpHealthConnectorV1(
        ConnectorConfigV1(
            name="runtime",
            kind="HTTP_HEALTH",
            endpoint=None,
            settings={
                "services": [
                    {
                        "service_id": "healthy",
                        "health_url": "https://healthy.test/health",
                        "success_statuses": [200],
                        "healthy_json_field": "healthy",
                    },
                    {
                        "service_id": "timeout",
                        "health_url": "https://timeout.test/health",
                        "success_statuses": [200],
                    },
                    {
                        "service_id": "unhealthy",
                        "health_url": "https://unhealthy.test/health",
                        "success_statuses": [200],
                        "healthy_json_field": "healthy",
                    },
                ]
            },
            credential_refs={},
        ),
        credential_resolver=CredentialResolverV1(environment={}),
        timeout_seconds=2,
        transport=httpx.MockTransport(handler),
    )
    context = CONTEXT.model_copy(
        update={"requested_services": ("healthy", "timeout", "unhealthy")}
    )

    health = connector.verify()
    results = connector.query(context)

    assert health.status is ConnectorAvailabilityV1.PARTIAL
    by_service = {item.requested_services[0]: item for item in results}
    healthy = by_service["healthy"]
    unhealthy = by_service["unhealthy"]
    timeout = by_service["timeout"]
    assert isinstance(healthy.records[0], RuntimeRecordV22)
    assert healthy.records[0].state is RuntimeStateV22.RUNNING
    assert healthy.records[0].healthy is True
    assert unhealthy.records[0].state is RuntimeStateV22.RUNNING
    assert unhealthy.records[0].healthy is False
    assert timeout.status is ReadSourceStatusV22.FAILURE_TIMEOUT
    assert timeout.records == ()
    assert timeout.safe_error_code == "CONNECTOR_TIMEOUT"


def test_http_health_accepts_bounded_non_json_body_without_json_field() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.extensions["timeout"]["read"] == 0.5
        return httpx.Response(200, text="ok")

    connector = HttpHealthConnectorV1(
        ConnectorConfigV1(
            name="runtime",
            kind="HTTP_HEALTH",
            endpoint=None,
            settings={
                "services": [
                    {
                        "service_id": "payment",
                        "health_url": "https://payment.test/health",
                        "success_statuses": [200],
                        "timeout_seconds": 0.5,
                    }
                ]
            },
            credential_refs={},
        ),
        credential_resolver=CredentialResolverV1(environment={}),
        timeout_seconds=2,
        transport=httpx.MockTransport(handler),
    )

    assert connector.verify().status is ConnectorAvailabilityV1.AVAILABLE
    result = connector.query(CONTEXT)[0]
    assert result.status is ReadSourceStatusV22.SUCCESS_NONEMPTY
    assert result.records[0].healthy is True
