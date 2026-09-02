from __future__ import annotations

from datetime import UTC, datetime
import json

import httpx
from fastapi.testclient import TestClient

from ecomsre.product.app import create_app
from ecomsre.product.baselines import (
    BaselineJobCreateV1,
    BaselineRepositoryV1,
    HistoricalBaselineServiceV1,
)
from ecomsre.product.connectors.credentials import CredentialResolverV1
from ecomsre.product.connectors.registry import ConnectorRegistryV1
from ecomsre.product.environment.capabilities import CapabilityMatrixRepositoryV1
from ecomsre.product.environment.repository import EnvironmentRepositoryV1
from ecomsre.product.environment.services import ServiceCatalogRepositoryV1
from ecomsre.product.environment.verification import EnvironmentVerificationServiceV1
from ecomsre.product.settings import ProductSettingsV1
from ecomsre.product.storage.sqlite_store import SqliteStoreV1


NOW = datetime(2026, 8, 27, 1, 0, tzinfo=UTC)


def _settings(tmp_path) -> ProductSettingsV1:
    return ProductSettingsV1(
        data_root=tmp_path,
        sqlite_path=tmp_path / "product.sqlite3",
        object_store_root=tmp_path / "objects",
    )


def _environment_payload() -> dict[str, object]:
    return {
        "name": "connector-backed",
        "service_identity_policy": {
            "services": [
                {
                    "logical_service": "payment",
                    "aliases": {
                        "prometheus": ["payment-api"],
                        "opensearch": ["payment_logs"],
                        "jaeger": ["PaymentService"],
                        "http_health": ["payment-health"],
                    },
                }
            ]
        },
        "connector_configs": [
            {
                "name": "prometheus",
                "kind": "PROMETHEUS",
                "endpoint": "https://prometheus.test",
                "settings": {
                    "query_templates": {
                        "error_rate": "errors{service=\"{service}\"}",
                        "request_support": "requests{service=\"{service}\"}",
                        "latency": "latency{service=\"{service}\"}",
                        "cpu": "cpu{service=\"{service}\"}",
                        "memory": "memory{service=\"{service}\"}",
                    }
                },
                "credential_refs": {},
            },
            {
                "name": "logs",
                "kind": "OPENSEARCH",
                "endpoint": "https://opensearch.test",
                "settings": {
                    "index_pattern": "otel-*",
                    "timestamp_field": "@timestamp",
                    "service_field": "service",
                    "severity_field": "severity",
                    "message_field": "body",
                },
                "credential_refs": {},
            },
            {
                "name": "traces",
                "kind": "JAEGER",
                "endpoint": "https://jaeger.test",
                "settings": {},
                "credential_refs": {},
            },
            {
                "name": "runtime",
                "kind": "HTTP_HEALTH",
                "endpoint": None,
                "settings": {
                    "services": [
                        {
                            "service_id": "payment-health",
                            "health_url": "https://payment.test/health",
                            "success_statuses": [200],
                            "healthy_json_field": "healthy",
                        }
                    ]
                },
                "credential_refs": {},
            },
        ],
        "explicit_service_catalog": ["payment"],
    }


def _prometheus(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/api/v1/label/service_name/values":
        return httpx.Response(200, json={"status": "success", "data": ["payment-api"]})
    assert request.url.path == "/api/v1/query_range"
    assert "payment-api" in request.url.params["query"]
    ended = int(float(request.url.params["end"]))
    query = request.url.params["query"]
    if query.startswith("errors"):
        values = [[ended - 30, "0.01"], [ended, "0.02"]]
    elif query.startswith("cpu"):
        values = [[ended - 30, "10"], [ended, "20"]]
    else:
        values = [[ended - 30, "100"], [ended, "130"]]
    return httpx.Response(
        200,
        json={
            "status": "success",
            "data": {
                "resultType": "matrix",
                "result": [{"metric": {}, "values": values}],
            },
        },
    )


def _opensearch(request: httpx.Request) -> httpx.Response:
    body = json.loads(request.content)
    if body["size"] == 0:
        return httpx.Response(
            200,
            json={
                "hits": {"hits": []},
                "aggregations": {"services": {"buckets": [{"key": "payment_logs"}]}},
            },
        )
    assert body["query"]["bool"]["filter"][0]["terms"]["service"] == [
        "payment_logs"
    ]
    ended_at = body["query"]["bool"]["filter"][1]["range"]["@timestamp"]["lte"]
    return httpx.Response(
        200,
        json={
            "hits": {
                "hits": [
                    {
                        "_source": {
                            "@timestamp": ended_at,
                            "service": "payment_logs",
                            "severity": "DIAGNOSTIC",
                            "body": "charge completed in 20 ms",
                        }
                    }
                ],
                "total": {"value": 1},
            }
        },
    )


def _jaeger(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/api/services":
        return httpx.Response(200, json={"data": ["PaymentService"]})
    assert request.url.params["service"] == "PaymentService"
    started = int(request.url.params["start"])
    return httpx.Response(
        200,
        json={
            "data": [
                {
                    "processes": {"p1": {"serviceName": "PaymentService"}},
                    "spans": [
                        {
                            "spanID": "root",
                            "operationName": "charge",
                            "startTime": started + 1_000_000,
                            "duration": 20_000,
                            "processID": "p1",
                            "references": [],
                            "tags": [],
                        }
                    ],
                }
            ]
        },
    )


def _health(_request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"healthy": True})


def test_increment2_connector_verification_baseline_and_read_api(tmp_path) -> None:
    settings = _settings(tmp_path)
    store = SqliteStoreV1(settings.sqlite_path)
    environments = EnvironmentRepositoryV1(store)
    services = ServiceCatalogRepositoryV1(store)
    capabilities = CapabilityMatrixRepositoryV1(store)
    baseline_repository = BaselineRepositoryV1(store)
    environment = environments.create(_environment_payload(), now=100)
    registry = ConnectorRegistryV1(
        credential_resolver=CredentialResolverV1(environment={}),
        timeout_seconds=2,
        transports={
            "prometheus": httpx.MockTransport(_prometheus),
            "logs": httpx.MockTransport(_opensearch),
            "traces": httpx.MockTransport(_jaeger),
            "runtime": httpx.MockTransport(_health),
        },
    )
    verification = EnvironmentVerificationServiceV1(
        services=services,
        capabilities=capabilities,
        connectors=registry,
    ).verify(environment, verified_at=NOW)

    assert all(item.status.value == "AVAILABLE" for item in verification.connector_health)
    assert verification.service_identity_map.services[0].logical_service == "payment"
    baseline = HistoricalBaselineServiceV1(
        connectors=registry,
        repository=baseline_repository,
        maximum_records_per_source=200,
    ).build(
        environment=environment,
        identity_map=verification.service_identity_map,
        capability_matrix=verification.capability_matrix,
        request=BaselineJobCreateV1(activate=False),
        built_at=NOW,
    )

    assert baseline.successful_windows == 6
    assert baseline.active is False
    assert baseline.v22_baseline_profile.metric_stats[0].service == "payment"
    assert baseline.v22_baseline_profile.trace_stats == ()

    with TestClient(create_app(settings)) as client:
        matrix_response = client.get(
            f"/v1/environments/{environment.environment_id}/capabilities"
        )
        baselines_response = client.get(
            f"/v1/environments/{environment.environment_id}/baselines"
        )
        baseline_job = client.post(
            f"/v1/environments/{environment.environment_id}/baseline-jobs",
            json={"activate": False},
        )
    assert matrix_response.status_code == 200
    assert baselines_response.json()["items"][0]["baseline_id"] == baseline.baseline_id
    assert baseline_job.status_code == 202
