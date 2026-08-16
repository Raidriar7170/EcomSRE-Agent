from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from pydantic import ValidationError

from ecomsre.dta_v2.read_tools import InvestigationReadTools
from ecomsre.dta_v2.telemetry_adapters import (
    LocalReadBackendConfig,
    LocalSandboxReadBackend,
    _parse_prometheus_vector,
)
from ecomsre.dta_v2.tool_contracts import (
    MetricKind,
    ObservationStatus,
    build_fake_read_authority,
    build_inspect_resource_usage_request,
    build_inspect_service_runtime_request,
    build_query_metrics_request,
    build_search_logs_request,
    build_trace_neighborhood_request,
)


RUN_ID = "3" * 32
START = datetime(2026, 8, 16, 3, 0, tzinfo=timezone.utc)
END = START + timedelta(minutes=2)


class StubHttp:
    def request_json(
        self, *, base_url: str, path: str, method: str, payload: object | None
    ) -> object:
        del base_url, method, payload
        if "/api/v1/query?" in path:
            return {
                "status": "success",
                "data": {
                    "resultType": "vector",
                    "result": [
                        {
                            "metric": {"service_name": "payment"},
                            "value": [END.timestamp(), "2.5"],
                        }
                    ],
                },
            }
        if path.endswith("/_search"):
            return {
                "hits": {
                    "hits": [
                        {
                            "_source": {
                                "observedTimestamp": END.isoformat(),
                                "resource": {"service": {"name": "payment"}},
                                "severityText": "ERROR",
                                "body": "request timeout trace=0123456789abcdef0123456789abcdef",
                            }
                        },
                        {
                            "_source": {
                                "observedTimestamp": END.isoformat(),
                                "resource": {"service": {"name": "payment"}},
                                "severityText": "INFO",
                                "body": "ordinary request completed",
                            }
                        },
                    ]
                }
            }
        if "/jaeger/ui/api/traces?" in path:
            return {
                "data": [
                    {
                        "processes": {
                            "p1": {"serviceName": "frontend"},
                            "p2": {"serviceName": "payment"},
                        },
                        "spans": [
                            {
                                "spanID": "a" * 16,
                                "processID": "p1",
                                "operationName": "checkout",
                                "startTime": 1_000_000,
                                "duration": 10_000,
                                "tags": [],
                                "references": [],
                            },
                            {
                                "spanID": "b" * 16,
                                "processID": "p2",
                                "operationName": "charge",
                                "startTime": 1_005_000,
                                "duration": 20_000,
                                "tags": [{"key": "error", "value": True}],
                                "references": [
                                    {"refType": "CHILD_OF", "spanID": "a" * 16}
                                ],
                            },
                        ],
                    }
                ]
            }
        raise AssertionError(path)


class StubDocker:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def get_json(self, path: str) -> Any:
        self.calls.append(path)
        if path.startswith("/containers/json?"):
            return [
                {
                    "Id": "c" * 64,
                    "Labels": {
                        "com.docker.compose.project": "ecomsre-live-sandbox-v1",
                        "io.ecomsre.sandbox.id": "sandbox-opaque",
                        "com.docker.compose.service": "payment",
                    },
                }
            ]
        if path == f"/containers/{'c' * 64}/json":
            return {
                "State": {
                    "Status": "running",
                    "Running": True,
                    "ExitCode": 0,
                    "Restarting": False,
                    "Health": {"Status": "healthy"},
                },
                "RestartCount": 1,
                "Config": {
                    "Labels": {
                        "com.docker.compose.project": "ecomsre-live-sandbox-v1",
                        "io.ecomsre.sandbox.id": "sandbox-opaque",
                        "com.docker.compose.service": "payment",
                    }
                },
            }
        if path == f"/containers/{'c' * 64}/stats?stream=false":
            return {
                "cpu_stats": {
                    "cpu_usage": {"total_usage": 200, "percpu_usage": [1, 1]},
                    "system_cpu_usage": 1000,
                    "online_cpus": 2,
                },
                "precpu_stats": {
                    "cpu_usage": {"total_usage": 100},
                    "system_cpu_usage": 500,
                },
                "memory_stats": {"usage": 2000, "stats": {"cache": 100}},
            }
        raise AssertionError(path)


def _config() -> LocalReadBackendConfig:
    return LocalReadBackendConfig(
        prometheus_base_url="http://127.0.0.1:19090",
        opensearch_base_url="http://127.0.0.1:19200",
        jaeger_base_url="http://127.0.0.1:11686",
        opensearch_index="otel-logs-*",
        docker_endpoint="unix:///var/run/docker.sock",
        compose_project="ecomsre-live-sandbox-v1",
        sandbox_label_key="io.ecomsre.sandbox.id",
        sandbox_label_value="sandbox-opaque",
        timeout_seconds=3.0,
        authority=build_fake_read_authority(),
    )


def test_config_rejects_remote_or_arbitrary_endpoints() -> None:
    payload = _config().model_dump()
    with pytest.raises(ValidationError, match="loopback"):
        LocalReadBackendConfig.model_validate(
            {**payload, "prometheus_base_url": "https://example.com"}
        )
    with pytest.raises(ValidationError, match="Unix"):
        LocalReadBackendConfig.model_validate(
            {**payload, "docker_endpoint": "tcp://127.0.0.1:2375"}
        )


def test_live_backend_projects_all_five_sources_without_raw_identities() -> None:
    backend = LocalSandboxReadBackend(
        config=_config(), http=StubHttp(), docker=StubDocker(), sleep=lambda _: None
    )
    requests = (
        build_query_metrics_request(
            run_id=RUN_ID,
            service="payment",
            started_at=START,
            ended_at=END,
            metric_kinds=(MetricKind.ERROR_RATE, MetricKind.REQUEST_SUPPORT),
            max_results=6,
        ),
        build_search_logs_request(
            run_id=RUN_ID,
            service="payment",
            started_at=START,
            ended_at=END,
            max_records=5,
        ),
        build_trace_neighborhood_request(
            run_id=RUN_ID,
            service="payment",
            started_at=START,
            ended_at=END,
            max_spans=10,
        ),
        build_inspect_service_runtime_request(
            run_id=RUN_ID, services=("payment",), max_results=3
        ),
        build_inspect_resource_usage_request(
            run_id=RUN_ID,
            services=("payment",),
            sampling_window_seconds=2,
            sample_count=2,
        ),
    )
    for request in requests:
        tools = InvestigationReadTools(run_id=RUN_ID, backend=backend)
        observation = tools.dispatch(request)
        assert observation.status is ObservationStatus.SUCCESS
        serialized = observation.model_dump_json()
        assert "c" * 64 not in serialized
        assert "0123456789abcdef0123456789abcdef" not in serialized
    log_observation = InvestigationReadTools(run_id=RUN_ID, backend=backend).dispatch(
        requests[1]
    )
    assert log_observation.result_count == 1
    assert "redacted-identity" in log_observation.model_dump_json()


def test_trace_projection_retains_anchor_service_and_deepest_error_under_cap() -> None:
    backend = LocalSandboxReadBackend(
        config=_config(), http=StubHttp(), docker=StubDocker(), sleep=lambda _: None
    )
    request = build_trace_neighborhood_request(
        run_id=RUN_ID,
        service="payment",
        started_at=START,
        ended_at=END,
        max_spans=1,
    )

    observation = InvestigationReadTools(run_id=RUN_ID, backend=backend).dispatch(
        request
    )

    assert observation.status is ObservationStatus.SUCCESS
    assert observation.result_count == 1
    assert observation.results[0].service == "payment"
    assert observation.results[0].first_error_location is True


def test_trace_projection_prioritizes_target_error_across_returned_traces() -> None:
    class HealthyThenErrorHttp(StubHttp):
        def request_json(
            self,
            *,
            base_url: str,
            path: str,
            method: str,
            payload: object | None,
        ) -> object:
            if "/jaeger/ui/api/traces?" not in path:
                return super().request_json(
                    base_url=base_url,
                    path=path,
                    method=method,
                    payload=payload,
                )
            process = {"p1": {"serviceName": "payment"}}
            return {
                "data": [
                    {
                        "processes": process,
                        "spans": [
                            {
                                "spanID": "a" * 16,
                                "processID": "p1",
                                "operationName": "healthy-charge",
                                "startTime": 2_000_000,
                                "duration": 1_000,
                                "tags": [],
                                "references": [],
                            }
                        ],
                    },
                    {
                        "processes": process,
                        "spans": [
                            {
                                "spanID": "b" * 16,
                                "processID": "p1",
                                "operationName": "failed-charge",
                                "startTime": 1_000_000,
                                "duration": 1_000,
                                "tags": [{"key": "error", "value": True}],
                                "references": [],
                            }
                        ],
                    },
                ]
            }

    backend = LocalSandboxReadBackend(
        config=_config(),
        http=HealthyThenErrorHttp(),
        docker=StubDocker(),
        sleep=lambda _: None,
    )
    request = build_trace_neighborhood_request(
        run_id=RUN_ID,
        service="payment",
        started_at=START,
        ended_at=END,
        max_spans=1,
    )

    observation = InvestigationReadTools(run_id=RUN_ID, backend=backend).dispatch(
        request
    )

    assert observation.status is ObservationStatus.SUCCESS
    assert observation.result_count == 1
    assert observation.results[0].operation == "failed-charge"
    assert observation.results[0].first_error_location is True


def test_prometheus_nan_no_sample_is_empty_but_infinity_is_invalid() -> None:
    no_sample = {
        "status": "success",
        "data": {
            "resultType": "vector",
            "result": [
                {
                    "metric": {"service_name": "recommendation"},
                    "value": [END.timestamp(), "NaN"],
                }
            ],
        },
    }
    value, sample_count = _parse_prometheus_vector(
        no_sample, expected_service="recommendation"
    )
    assert value == 0.0
    assert sample_count == 0

    infinite = {
        **no_sample,
        "data": {
            **no_sample["data"],
            "result": [
                {
                    "metric": {"service_name": "recommendation"},
                    "value": [END.timestamp(), "+Inf"],
                }
            ],
        },
    }
    with pytest.raises(ValueError, match="finite"):
        _parse_prometheus_vector(infinite, expected_service="recommendation")
