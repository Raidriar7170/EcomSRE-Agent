import inspect
import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

from ecomsre.environment import readiness as readiness_module
from ecomsre.environment.readiness import collect_candidate_initial_readiness
from ecomsre.evidence.hashes import sha256_bytes
from ecomsre.telemetry.http import HttpExchange, HttpReason


RUN_ID = "c" * 32


class FakeClient:
    calls = 0

    def __init__(self, *, context) -> None:
        self.run_id = context.run_id

    def request(self, request):
        type(self).calls += 1
        now = datetime.now(UTC)
        started = int(now.timestamp() * 1_000_000)
        if request.endpoint.service == "jaeger":
            body = json.dumps(
                {
                    "data": [
                        {
                            "traceID": "1" * 32,
                            "processes": {
                                "load": {"serviceName": "load-generator"},
                                "ad": {"serviceName": "ad"},
                            },
                            "spans": [
                                {
                                    "traceID": "1" * 32,
                                    "processID": "load",
                                    "operationName": "user_get_ads",
                                    "startTime": started,
                                    "duration": 10,
                                },
                                {
                                    "traceID": "1" * 32,
                                    "processID": "ad",
                                    "operationName": "oteldemo.AdService/GetAds",
                                    "startTime": started,
                                    "duration": 10,
                                },
                            ],
                        }
                    ]
                }
            ).encode()
        elif request.endpoint.service == "opensearch":
            body = json.dumps(
                {
                    "hits": {
                        "hits": [
                            {
                                "_id": "log-1",
                                "_source": {
                                    "@timestamp": now.isoformat(),
                                    "resource": {
                                        "service": {"name": "ad"},
                                    },
                                },
                            }
                        ]
                    }
                }
            ).encode()
        elif request.target.startswith("/api/data"):
            body = b'[{"redirectUrl":"/product/1","text":"Ad"}]'
        else:
            body = json.dumps(
                {
                    "status": "success",
                    "data": {
                        "resultType": "vector",
                        "result": [
                            {
                                "metric": {
                                    "service_name": "ad",
                                    "span_name": "oteldemo.AdService/GetAds",
                                },
                                "value": [now.timestamp(), "1"],
                            }
                        ],
                    },
                }
            ).encode()
        return HttpExchange(
            reason=HttpReason.OK,
            request=request,
            started_at=now,
            ended_at=now,
            monotonic_started_at=1,
            monotonic_ended_at=1,
            status_code=200,
            response_headers=(),
            raw_body=body,
            raw_sha256=sha256_bytes(body),
            raw_body_partial=False,
        )


def test_initial_candidate_readiness_persists_owned_endpoints_without_frozen_claim(
    tmp_path: Path,
    monkeypatch,
) -> None:
    preflight = SimpleNamespace(
        run_id=RUN_ID,
        content_sha256="a" * 64,
        is_current=lambda: True,
    )
    ownership = SimpleNamespace(
        run_id=RUN_ID,
        manifest_sha256="b" * 64,
        manifest=SimpleNamespace(resources=()),
        is_authentic=lambda: True,
    )
    monkeypatch.setattr(
        "ecomsre.environment.readiness._owned_base_urls",
        lambda _ownership: {
            "prometheus": "http://127.0.0.1:9090",
            "jaeger": "http://127.0.0.1:16686",
            "opensearch": "http://127.0.0.1:9200",
            "probe": "http://127.0.0.1:8080",
        },
    )
    monkeypatch.setattr(
        "ecomsre.environment.readiness.OwnedHttpClient",
        FakeClient,
    )
    monkeypatch.setattr(
        "ecomsre.environment.readiness._verify_initial_lifecycle_ownership",
        lambda **_kwargs: (
            "artifact.json",
            "d" * 64,
            {
                "load_generator_healthy": True,
                "otel_collector_healthy": True,
            },
        ),
    )
    sleeps = []

    evidence = collect_candidate_initial_readiness(
        project_root=tmp_path,
        artifacts_root=tmp_path,
        preflight=preflight,
        ownership=ownership,
        retry_sleep=sleeps.append,
    )

    assert evidence.ready
    assert evidence.registry_frozen_claimed is False
    assert set(evidence.endpoint_gates) == {
        "prometheus",
        "jaeger",
        "opensearch",
        "probe",
    }
    assert all(evidence.endpoint_gates.values())
    assert evidence.propagation_authority == "CANDIDATE_OWNED_CURRENT_RUN"
    assert evidence.registry_frozen_claimed is False
    assert evidence.attempt_count == 1
    assert evidence.propagation_gates == {
        "prometheus_ad_getads_current": True,
        "jaeger_load_to_ad_getads_current": True,
        "opensearch_ad_log_current": True,
        "load_generator_healthy": True,
        "otel_collector_healthy": True,
    }
    assert len(evidence.raw_artifacts) == 4
    assert sleeps == []
    assert Path(evidence.evidence_artifact).is_file()


def test_initial_candidate_readiness_retries_all_signals_with_one_bounded_policy(
    tmp_path: Path,
    monkeypatch,
) -> None:
    class RetryClient(FakeClient):
        def __init__(self, *, context) -> None:
            super().__init__(context=context)
            self.attempts = 0

        def request(self, request):
            self.attempts += 1
            if self.attempts <= 4:
                now = datetime.now(UTC)
                body = (
                    b'{"status":"success","data":{"resultType":"vector","result":[]}}'
                    if request.endpoint.service == "prometheus"
                    else (
                        b'{"data":[]}'
                        if request.endpoint.service == "jaeger"
                        else (
                            b'{"hits":{"hits":[]}}'
                            if request.endpoint.service == "opensearch"
                            else b"[]"
                        )
                    )
                )
                return HttpExchange(
                    reason=HttpReason.OK,
                    request=request,
                    started_at=now,
                    ended_at=now,
                    monotonic_started_at=1,
                    monotonic_ended_at=1,
                    status_code=200,
                    response_headers=(),
                    raw_body=body,
                    raw_sha256=sha256_bytes(body),
                    raw_body_partial=False,
                )
            return super().request(request)

    preflight = SimpleNamespace(
        run_id=RUN_ID,
        content_sha256="a" * 64,
        is_current=lambda: True,
    )
    ownership = SimpleNamespace(
        run_id=RUN_ID,
        manifest_sha256="b" * 64,
        manifest=SimpleNamespace(resources=()),
        is_authentic=lambda: True,
    )
    monkeypatch.setattr(
        readiness_module,
        "_owned_base_urls",
        lambda _ownership: {
            "prometheus": "http://127.0.0.1:9090",
            "jaeger": "http://127.0.0.1:16686",
            "opensearch": "http://127.0.0.1:9200",
            "probe": "http://127.0.0.1:8080",
        },
    )
    monkeypatch.setattr(readiness_module, "OwnedHttpClient", RetryClient)
    monkeypatch.setattr(
        readiness_module,
        "_verify_initial_lifecycle_ownership",
        lambda **_kwargs: (
            "artifact.json",
            "d" * 64,
            {
                "load_generator_healthy": True,
                "otel_collector_healthy": True,
            },
        ),
    )
    sleeps = []

    evidence = collect_candidate_initial_readiness(
        project_root=tmp_path,
        artifacts_root=tmp_path,
        preflight=preflight,
        ownership=ownership,
        retry_sleep=sleeps.append,
    )

    assert evidence.ready
    assert evidence.attempt_count == 2
    assert len(evidence.raw_artifacts) == 8
    assert sleeps == [5.0]


def test_control_mutation_candidate_tolerates_fault_probe_transport_outcome(
    tmp_path: Path,
    monkeypatch,
) -> None:
    class FaultProbeClient(FakeClient):
        def request(self, request):
            if request.endpoint.service != "frontend-proxy":
                return super().request(request)
            now = datetime.now(UTC)
            body = b"upstream GetAds unavailable"
            return HttpExchange(
                reason=HttpReason.HTTP_STATUS_ERROR,
                request=request,
                started_at=now,
                ended_at=now,
                monotonic_started_at=1,
                monotonic_ended_at=1,
                status_code=503,
                response_headers=(),
                raw_body=body,
                raw_sha256=sha256_bytes(body),
                raw_body_partial=False,
            )

    authority = SimpleNamespace(
        run_id=RUN_ID,
        content_sha256="a" * 64,
        is_current=lambda: True,
    )
    ownership = SimpleNamespace(
        run_id=RUN_ID,
        manifest_sha256="b" * 64,
        manifest=SimpleNamespace(resources=()),
        is_authentic=lambda: True,
    )
    monkeypatch.setattr(
        readiness_module,
        "_owned_base_urls",
        lambda _ownership: {
            "prometheus": "http://127.0.0.1:9090",
            "jaeger": "http://127.0.0.1:16686",
            "opensearch": "http://127.0.0.1:9200",
            "probe": "http://127.0.0.1:8080",
        },
    )
    monkeypatch.setattr(readiness_module, "OwnedHttpClient", FaultProbeClient)
    monkeypatch.setattr(
        readiness_module,
        "_verify_initial_lifecycle_ownership",
        lambda **_kwargs: (
            "artifact.json",
            "d" * 64,
            {
                "load_generator_healthy": True,
                "otel_collector_healthy": True,
            },
        ),
    )

    evidence = collect_candidate_initial_readiness(
        project_root=tmp_path,
        artifacts_root=tmp_path,
        preflight=authority,
        ownership=ownership,
        purpose="CONTROL_MUTATION",
        retry_sleep=lambda _seconds: None,
    )

    assert evidence.ready
    assert evidence.purpose == "CONTROL_MUTATION"
    assert evidence.endpoint_gates["probe"] is False
    assert all(evidence.propagation_gates.values())


def test_collector_receipt_is_issued_after_three_backend_measurements() -> None:
    source = inspect.getsource(readiness_module.collect_fresh_readiness)

    collector = source.index("collector_receipt =")
    assert source.index("prometheus = PrometheusAdapter") < collector
    assert source.index("jaeger = JaegerAdapter") < collector
    assert source.index("opensearch = OpenSearchAdapter") < collector
    assert "context=ownership" in source[collector:]
    assert "execution=execution" in source[collector:]
