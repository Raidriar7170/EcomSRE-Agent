import inspect
import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from ecomsre.environment import readiness as readiness_module
from ecomsre.environment.readiness import collect_candidate_initial_readiness
from ecomsre.evidence.hashes import sha256_bytes
from ecomsre.telemetry.http import (
    HttpExchange,
    HttpReason,
    HttpRequest,
    OwnedEndpoint,
    PhaseWindow,
)


RUN_ID = "c" * 32


class FakeClient:
    calls = 0
    targets: list[str] = []

    def __init__(self, *, context) -> None:
        self.run_id = context.run_id

    def request(self, request):
        type(self).calls += 1
        type(self).targets.append(request.target)
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
                                        "service.name": "ad",
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
    FakeClient.targets = []
    preflight = SimpleNamespace(
        run_id=RUN_ID,
        content_sha256="a" * 64,
        is_current=lambda: True,
        is_authentic=lambda: True,
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
    assert any(
        target.startswith("/jaeger/ui/api/traces?")
        for target in FakeClient.targets
    )
    assert Path(evidence.evidence_artifact).is_file()


def test_running_container_without_configured_healthcheck_is_ready() -> None:
    assert readiness_module._container_state_is_ready(
        {
            "Running": True,
            "Status": "running",
            "Health": None,
        }
    )
    assert not readiness_module._container_state_is_ready(
        {
            "Running": True,
            "Status": "running",
            "Health": {"Status": "starting"},
        }
    )


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
        is_authentic=lambda: True,
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
        is_authentic=lambda: True,
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

    sleeps: list[float] = []
    evidence = collect_candidate_initial_readiness(
        project_root=tmp_path,
        artifacts_root=tmp_path,
        preflight=authority,
        ownership=ownership,
        purpose="CONTROL_MUTATION",
        retry_sleep=sleeps.append,
    )

    assert evidence.ready
    assert evidence.purpose == "CONTROL_MUTATION"
    assert evidence.endpoint_gates["probe"] is False
    assert all(evidence.propagation_gates.values())
    assert sleeps == []


def test_pre_http_lifecycle_failure_persists_typed_gate_matrix(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    preflight = SimpleNamespace(
        run_id=RUN_ID,
        content_sha256="a" * 64,
        is_current=lambda: True,
        is_authentic=lambda: True,
    )
    ownership = SimpleNamespace(
        run_id=RUN_ID,
        manifest_sha256="b" * 64,
        manifest=SimpleNamespace(resources=()),
        is_authentic=lambda: True,
    )
    monkeypatch.setattr(
        readiness_module,
        "_verify_initial_lifecycle_ownership",
        lambda **_kwargs: (_ for _ in ()).throw(
            ValueError("lifecycle runner authority is invalid")
        ),
    )

    with pytest.raises(readiness_module.ReadinessCollectionError) as captured:
        collect_candidate_initial_readiness(
            project_root=tmp_path,
            artifacts_root=tmp_path,
            preflight=preflight,
            ownership=ownership,
            retry_sleep=lambda _seconds: pytest.fail(
                "pre-HTTP readiness must not sleep"
            ),
        )

    assert captured.value.reason_code == (
        "INITIAL_READINESS_LIFECYCLE_AUTHORITY_INVALID"
    )
    artifact = Path(captured.value.artifact_path)
    assert artifact.is_file()
    payload = json.loads(artifact.read_text(encoding="utf-8"))
    assert payload["attempt_count"] == 0
    assert payload["endpoint_gates"] == {
        "prometheus": "NOT_EVALUATED",
        "jaeger": "NOT_EVALUATED",
        "opensearch": "NOT_EVALUATED",
        "probe": "NOT_EVALUATED",
    }
    assert payload["propagation_gates"] == {
        "prometheus_ad_getads_current": "NOT_EVALUATED",
        "jaeger_load_to_ad_getads_current": "NOT_EVALUATED",
        "opensearch_ad_log_current": "NOT_EVALUATED",
        "load_generator_healthy": "NOT_EVALUATED",
        "otel_collector_healthy": "NOT_EVALUATED",
    }
    assert set(payload["propagation_diagnostics"]) == set(
        payload["propagation_gates"]
    )
    for diagnostic in payload["propagation_diagnostics"].values():
        assert diagnostic == {
            "raw_artifact": None,
            "parse_reason": "NOT_ATTEMPTED",
            "freshness_reason": "NOT_EVALUATED",
        }
    for diagnostic in payload["endpoint_diagnostics"].values():
        assert diagnostic == {
            "http_status": "NOT_ATTEMPTED",
            "transport_reason": "NOT_ATTEMPTED",
            "raw_artifact": None,
            "parse_reason": "NOT_ATTEMPTED_NO_HTTP_RESPONSE",
            "freshness_reason": (
                "INITIAL_READINESS_LIFECYCLE_AUTHORITY_INVALID"
            ),
        }
    invalid = dict(payload)
    invalid["propagation_diagnostics"] = dict(
        invalid["propagation_diagnostics"]
    )
    invalid["propagation_diagnostics"].pop("otel_collector_healthy")
    with pytest.raises(ValidationError, match="propagation diagnostic keys"):
        readiness_module.CandidateReadinessPreHttpFailure.model_validate(
            invalid
        )


def test_expired_same_run_authority_persists_pre_http_failure_artifact(
    tmp_path: Path,
) -> None:
    preflight = SimpleNamespace(
        run_id=RUN_ID,
        content_sha256="a" * 64,
        is_current=lambda: False,
        is_authentic=lambda: True,
    )
    ownership = SimpleNamespace(
        run_id=RUN_ID,
        manifest_sha256="b" * 64,
        is_authentic=lambda: True,
    )

    with pytest.raises(readiness_module.ReadinessCollectionError) as captured:
        collect_candidate_initial_readiness(
            project_root=tmp_path,
            artifacts_root=tmp_path,
            preflight=preflight,
            ownership=ownership,
        )

    assert captured.value.reason_code == "INITIAL_READINESS_AUTHORITY_INVALID"
    assert captured.value.artifact_path is not None
    payload = json.loads(
        Path(captured.value.artifact_path).read_text(encoding="utf-8")
    )
    assert payload["reason_code"] == "INITIAL_READINESS_AUTHORITY_INVALID"
    assert payload["attempt_count"] == 0


def test_run_mismatch_never_creates_pre_http_failure_evidence(
    tmp_path: Path,
) -> None:
    other_run_id = "d" * 32
    preflight = SimpleNamespace(
        run_id=RUN_ID,
        content_sha256="a" * 64,
        is_current=lambda: True,
        is_authentic=lambda: True,
    )
    ownership = SimpleNamespace(
        run_id=other_run_id,
        manifest_sha256="b" * 64,
        is_authentic=lambda: True,
    )

    with pytest.raises(readiness_module.ReadinessCollectionError) as captured:
        collect_candidate_initial_readiness(
            project_root=tmp_path,
            artifacts_root=tmp_path,
            preflight=preflight,
            ownership=ownership,
        )

    assert captured.value.reason_code == "INITIAL_READINESS_AUTHORITY_INVALID"
    assert captured.value.artifact_path is None
    assert not (tmp_path / "observer-visible" / RUN_ID).exists()
    assert not (tmp_path / "observer-visible" / other_run_id).exists()


@pytest.mark.parametrize(
    "invalidated_authority",
    ["preflight", "ownership"],
)
def test_unauthenticated_authority_never_receives_evidence_write_path(
    tmp_path: Path,
    invalidated_authority: str,
) -> None:
    preflight = SimpleNamespace(
        run_id=RUN_ID,
        content_sha256="a" * 64,
        is_current=lambda: False,
        is_authentic=lambda: invalidated_authority != "preflight",
    )
    ownership = SimpleNamespace(
        run_id=RUN_ID,
        manifest_sha256="b" * 64,
        is_authentic=lambda: invalidated_authority != "ownership",
    )

    with pytest.raises(readiness_module.ReadinessCollectionError) as captured:
        collect_candidate_initial_readiness(
            project_root=tmp_path,
            artifacts_root=tmp_path,
            preflight=preflight,
            ownership=ownership,
        )

    assert captured.value.artifact_path is None
    assert not (tmp_path / "observer-visible" / RUN_ID).exists()


def test_authority_invalidated_during_pre_http_failure_is_not_persisted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = {"authentic": True}
    preflight = SimpleNamespace(
        run_id=RUN_ID,
        content_sha256="a" * 64,
        is_current=lambda: True,
        is_authentic=lambda: state["authentic"],
    )
    ownership = SimpleNamespace(
        run_id=RUN_ID,
        manifest_sha256="b" * 64,
        is_authentic=lambda: True,
    )

    def invalidate(**_kwargs):
        state["authentic"] = False
        raise ValueError("lifecycle runner authority is invalid")

    monkeypatch.setattr(
        readiness_module,
        "_verify_initial_lifecycle_ownership",
        invalidate,
    )

    with pytest.raises(readiness_module.ReadinessCollectionError) as captured:
        collect_candidate_initial_readiness(
            project_root=tmp_path,
            artifacts_root=tmp_path,
            preflight=preflight,
            ownership=ownership,
        )

    assert captured.value.reason_code == (
        "INITIAL_READINESS_LIFECYCLE_AUTHORITY_INVALID"
    )
    assert captured.value.artifact_path is None
    assert not (tmp_path / "observer-visible" / RUN_ID).exists()


def test_collector_receipt_is_issued_after_three_backend_measurements() -> None:
    source = inspect.getsource(readiness_module.collect_fresh_readiness)

    collector = source.index("collector_receipt =")
    assert source.index("prometheus = PrometheusAdapter") < collector
    assert source.index("jaeger = JaegerAdapter") < collector
    assert source.index("opensearch = OpenSearchAdapter") < collector
    assert "context=ownership" in source[collector:]
    assert "execution=execution" in source[collector:]


def _candidate_window() -> PhaseWindow:
    now = datetime.now(UTC)
    return PhaseWindow(
        run_id=RUN_ID,
        cycle_number=1,
        scenario_phase=readiness_module.MeasurementPhase.BASELINE,
        utc_started_at=now,
        utc_ended_at=now.replace(microsecond=now.microsecond)
        + readiness_module.timedelta(seconds=60),
        monotonic_started_at=1,
        monotonic_ended_at=61,
    )


def _candidate_exchange(
    *,
    reason: HttpReason,
    status: int | None,
) -> HttpExchange:
    window = _candidate_window()
    request = HttpRequest(
        endpoint=OwnedEndpoint(
            base_url="http://127.0.0.1:8080",
            service="frontend-proxy",
            target_port=8080,
        ),
        method="GET",
        target="/api/data?contextKeys=telescopes",
        absolute_deadline_monotonic=window.monotonic_ended_at,
    )
    body = b"service unavailable"
    return HttpExchange(
        reason=reason,
        request=request,
        started_at=window.utc_started_at,
        ended_at=window.utc_started_at,
        monotonic_started_at=1,
        monotonic_ended_at=1,
        status_code=status,
        response_headers=(),
        raw_body=body,
        raw_sha256=sha256_bytes(body),
        raw_body_partial=False,
    )


def test_candidate_diagnostics_distinguish_http_503_from_transport_failure() -> None:
    window = _candidate_window()
    unavailable = readiness_module._candidate_endpoint_diagnostic(
        "probe",
        _candidate_exchange(reason=HttpReason.HTTP_STATUS_ERROR, status=503),
        attempt=2,
        raw_artifact="attempt-02-probe-raw.json",
        window=window,
    )
    transport = readiness_module._candidate_endpoint_diagnostic(
        "probe",
        _candidate_exchange(reason=HttpReason.HTTP_TRANSPORT_ERROR, status=None),
        attempt=2,
        raw_artifact="attempt-02-probe-raw.json",
        window=window,
    )

    assert unavailable.transport_outcome == "PASSED"
    assert unavailable.http_outcome == "FAILED"
    assert unavailable.http_status == 503
    assert unavailable.http_reason == "HTTP_STATUS_ERROR"
    assert unavailable.parse_outcome == "NOT_EVALUATED"
    assert transport.transport_outcome == "FAILED"
    assert transport.transport_reason == "HTTP_TRANSPORT_ERROR"
    assert transport.http_outcome == "NOT_EVALUATED"
    assert transport.http_status is None


@pytest.mark.parametrize(
    "mutation",
    [
        {
            "transport_outcome": "NOT_APPLICABLE",
            "transport_reason": "NOT_APPLICABLE_LIFECYCLE_ARTIFACT",
        },
        {
            "http_outcome": "NOT_EVALUATED",
            "http_status": None,
            "http_reason": "NOT_EVALUATED_TRANSPORT_FAILURE",
            "parse_outcome": "NOT_EVALUATED",
            "parse_reason": "NOT_EVALUATED_TRANSPORT_FAILURE",
            "freshness_outcome": "NOT_EVALUATED",
            "freshness_reason": "NOT_EVALUATED_TRANSPORT_FAILURE",
        },
        {
            "parse_outcome": "NOT_EVALUATED",
            "parse_reason": "NOT_EVALUATED_HTTP_FAILURE",
            "freshness_outcome": "NOT_EVALUATED",
            "freshness_reason": "NOT_EVALUATED_HTTP_FAILURE",
        },
        {
            "transport_outcome": "NOT_APPLICABLE",
            "transport_reason": "NOT_APPLICABLE_LIFECYCLE_ARTIFACT",
            "http_outcome": "NOT_APPLICABLE",
            "http_status": None,
            "http_reason": "NOT_APPLICABLE_LIFECYCLE_ARTIFACT",
            "parse_outcome": "FAILED",
            "parse_reason": "LIFECYCLE_SCHEMA_INVALID",
            "freshness_outcome": "NOT_EVALUATED",
            "freshness_reason": "NOT_EVALUATED_PARSE_FAILURE",
        },
    ],
)
def test_candidate_gate_diagnostic_rejects_contradictory_state_chains(
    mutation: dict[str, object],
) -> None:
    valid_endpoint = {
        "attempt": 1,
        "raw_artifact": "attempt-01-probe-raw.json",
        "transport_outcome": "PASSED",
        "transport_reason": "TRANSPORT_SUCCEEDED",
        "http_outcome": "PASSED",
        "http_status": 200,
        "http_reason": "HTTP_STATUS_OK",
        "parse_outcome": "PASSED",
        "parse_reason": "PROBE_AD_ARRAY_PARSED",
        "freshness_outcome": "PASSED",
        "freshness_reason": "PROBE_CURRENT_ATTEMPT_RESPONSE",
    }

    with pytest.raises(ValidationError, match="diagnostic state chain"):
        readiness_module.CandidateGateDiagnostic.model_validate(
            {**valid_endpoint, **mutation}
        )


def test_candidate_gate_diagnostic_accepts_exact_lifecycle_chain() -> None:
    diagnostic = readiness_module.CandidateGateDiagnostic(
        attempt=6,
        raw_artifact="verified.json",
        transport_outcome="NOT_APPLICABLE",
        transport_reason="NOT_APPLICABLE_LIFECYCLE_ARTIFACT",
        http_outcome="NOT_APPLICABLE",
        http_status=None,
        http_reason="NOT_APPLICABLE_LIFECYCLE_ARTIFACT",
        parse_outcome="PASSED",
        parse_reason="LIFECYCLE_VERIFIED_ARTIFACT_PARSED",
        freshness_outcome="FAILED",
        freshness_reason="OTEL_COLLECTOR_NOT_HEALTHY",
    )

    assert not diagnostic.passed


@pytest.mark.parametrize(
    "payload",
    [
        {
            "attempt": 1,
            "raw_artifact": "attempt-01-probe-raw.json",
            "transport_outcome": "FAILED",
            "transport_reason": "HTTP_TRANSPORT_ERROR",
            "http_outcome": "NOT_EVALUATED",
            "http_status": 503,
            "http_reason": "NOT_EVALUATED_TRANSPORT_FAILURE",
            "parse_outcome": "NOT_EVALUATED",
            "parse_reason": "NOT_EVALUATED_TRANSPORT_FAILURE",
            "freshness_outcome": "NOT_EVALUATED",
            "freshness_reason": "NOT_EVALUATED_TRANSPORT_FAILURE",
        },
        {
            "attempt": 1,
            "raw_artifact": "attempt-01-probe-raw.json",
            "transport_outcome": "PASSED",
            "transport_reason": "TRANSPORT_SUCCEEDED",
            "http_outcome": "FAILED",
            "http_status": 200,
            "http_reason": "HTTP_STATUS_ERROR",
            "parse_outcome": "NOT_EVALUATED",
            "parse_reason": "NOT_EVALUATED_HTTP_FAILURE",
            "freshness_outcome": "NOT_EVALUATED",
            "freshness_reason": "NOT_EVALUATED_HTTP_FAILURE",
        },
        {
            "attempt": 1,
            "raw_artifact": "attempt-01-probe-raw.json",
            "transport_outcome": "PASSED",
            "transport_reason": "TRANSPORT_SUCCEEDED",
            "http_outcome": "FAILED",
            "http_status": 503,
            "http_reason": "BOGUS_HTTP_REASON",
            "parse_outcome": "NOT_EVALUATED",
            "parse_reason": "NOT_EVALUATED_HTTP_FAILURE",
            "freshness_outcome": "NOT_EVALUATED",
            "freshness_reason": "NOT_EVALUATED_HTTP_FAILURE",
        },
        {
            "attempt": 1,
            "raw_artifact": "attempt-01-probe-raw.json",
            "transport_outcome": "PASSED",
            "transport_reason": "TRANSPORT_SUCCEEDED",
            "http_outcome": "PASSED",
            "http_status": 200,
            "http_reason": "HTTP_STATUS_OK",
            "parse_outcome": "PASSED",
            "parse_reason": "PROBE_SCHEMA_INVALID",
            "freshness_outcome": "PASSED",
            "freshness_reason": "PROBE_CURRENT_ATTEMPT_RESPONSE",
        },
        {
            "attempt": 1,
            "raw_artifact": "attempt-01-opensearch-raw.json",
            "transport_outcome": "PASSED",
            "transport_reason": "TRANSPORT_SUCCEEDED",
            "http_outcome": "PASSED",
            "http_status": 200,
            "http_reason": "HTTP_STATUS_OK",
            "parse_outcome": "PASSED",
            "parse_reason": "OPENSEARCH_IDENTITY_MATCH",
            "freshness_outcome": "PASSED",
            "freshness_reason": "OPENSEARCH_STALE_LOG",
        },
        {
            "attempt": 1,
            "raw_artifact": "verified.json",
            "transport_outcome": "NOT_APPLICABLE",
            "transport_reason": "NOT_APPLICABLE_LIFECYCLE_ARTIFACT",
            "http_outcome": "NOT_APPLICABLE",
            "http_status": None,
            "http_reason": "NOT_APPLICABLE_LIFECYCLE_ARTIFACT",
            "parse_outcome": "PASSED",
            "parse_reason": "LIFECYCLE_VERIFIED_ARTIFACT_PARSED",
            "freshness_outcome": "FAILED",
            "freshness_reason": "OTEL_COLLECTOR_HEALTHY",
        },
        {
            "attempt": 1,
            "raw_artifact": "verified.json",
            "transport_outcome": "NOT_APPLICABLE",
            "transport_reason": "NOT_APPLICABLE_LIFECYCLE_ARTIFACT",
            "http_outcome": "NOT_APPLICABLE",
            "http_status": None,
            "http_reason": "NOT_APPLICABLE_LIFECYCLE_ARTIFACT",
            "parse_outcome": "PASSED",
            "parse_reason": "LIFECYCLE_VERIFIED_ARTIFACT_PARSED",
            "freshness_outcome": "PASSED",
            "freshness_reason": "OTEL_COLLECTOR_NOT_HEALTHY",
        },
    ],
)
def test_candidate_gate_diagnostic_rejects_outcome_status_reason_conflicts(
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValidationError, match="diagnostic state chain"):
        readiness_module.CandidateGateDiagnostic.model_validate(payload)


_TEST_ENDPOINT_SUCCESS_REASONS = {
    "prometheus": ("PROMETHEUS_IDENTITY_MATCH", "PROMETHEUS_CURRENT_SAMPLE"),
    "jaeger": ("JAEGER_IDENTITY_MATCH", "JAEGER_CURRENT_TRACE"),
    "opensearch": ("OPENSEARCH_IDENTITY_MATCH", "OPENSEARCH_CURRENT_LOG"),
    "probe": ("PROBE_AD_ARRAY_PARSED", "PROBE_CURRENT_ATTEMPT_RESPONSE"),
}
_TEST_ENDPOINT_FAILURE_REASONS = {
    "prometheus": "PROMETHEUS_SCHEMA_INVALID",
    "jaeger": "JAEGER_SCHEMA_INVALID",
    "opensearch": "OPENSEARCH_SCHEMA_INVALID",
    "probe": "PROBE_SCHEMA_INVALID",
}
_TEST_PROPAGATION_BY_ENDPOINT = {
    "prometheus": "prometheus_ad_getads_current",
    "jaeger": "jaeger_load_to_ad_getads_current",
    "opensearch": "opensearch_ad_log_current",
}


def _valid_candidate_v2_payload() -> dict[str, object]:
    endpoint_diagnostics = {}
    raw_artifacts = []
    for endpoint, (parse_reason, freshness_reason) in (
        _TEST_ENDPOINT_SUCCESS_REASONS.items()
    ):
        raw_artifact = f"attempt-01-{endpoint}-raw.json"
        raw_artifacts.append(raw_artifact)
        endpoint_diagnostics[endpoint] = {
            "attempt": 1,
            "raw_artifact": raw_artifact,
            "transport_outcome": "PASSED",
            "transport_reason": "TRANSPORT_SUCCEEDED",
            "http_outcome": "PASSED",
            "http_status": 200,
            "http_reason": "HTTP_STATUS_OK",
            "parse_outcome": "PASSED",
            "parse_reason": parse_reason,
            "freshness_outcome": "PASSED",
            "freshness_reason": freshness_reason,
        }
    lifecycle = {
        "attempt": 1,
        "raw_artifact": "verified.json",
        "transport_outcome": "NOT_APPLICABLE",
        "transport_reason": "NOT_APPLICABLE_LIFECYCLE_ARTIFACT",
        "http_outcome": "NOT_APPLICABLE",
        "http_status": None,
        "http_reason": "NOT_APPLICABLE_LIFECYCLE_ARTIFACT",
        "parse_outcome": "PASSED",
        "parse_reason": "LIFECYCLE_VERIFIED_ARTIFACT_PARSED",
        "freshness_outcome": "PASSED",
        "freshness_reason": "LOAD_GENERATOR_HEALTHY",
    }
    propagation_diagnostics = {
        propagation: dict(endpoint_diagnostics[endpoint])
        for endpoint, propagation in _TEST_PROPAGATION_BY_ENDPOINT.items()
    }
    propagation_diagnostics["load_generator_healthy"] = dict(lifecycle)
    propagation_diagnostics["otel_collector_healthy"] = {
        **lifecycle,
        "freshness_reason": "OTEL_COLLECTOR_HEALTHY",
    }
    window = _candidate_window()
    return {
        "schema_version": "phase0.candidate-initial-readiness.v2",
        "run_id": RUN_ID,
        "preflight_sha256": "a" * 64,
        "ownership_manifest_sha256": "b" * 64,
        "purpose": "INITIAL",
        "endpoint_gates": {name: True for name in endpoint_diagnostics},
        "propagation_authority": "CANDIDATE_OWNED_CURRENT_RUN",
        "attempt_count": 1,
        "max_attempts": 6,
        "window_started_at": window.utc_started_at.isoformat(),
        "window_ended_at": window.utc_ended_at.isoformat(),
        "propagation_gates": {
            name: True for name in propagation_diagnostics
        },
        "endpoint_diagnostics": endpoint_diagnostics,
        "propagation_diagnostics": propagation_diagnostics,
        "raw_artifacts": raw_artifacts,
        "registry_frozen_claimed": False,
        "lifecycle_artifact": "verified.json",
        "lifecycle_sha256": "d" * 64,
    }


def test_candidate_v2_gate_family_fixture_is_valid() -> None:
    readiness_module.CandidateInitialReadiness.model_validate(
        _valid_candidate_v2_payload()
    )


@pytest.mark.parametrize(
    ("target", "source", "outcome"),
    [
        ("prometheus", "jaeger", "success"),
        ("prometheus", "jaeger", "failure"),
        ("jaeger", "opensearch", "success"),
        ("jaeger", "opensearch", "failure"),
        ("opensearch", "probe", "success"),
        ("opensearch", "probe", "failure"),
        ("probe", "prometheus", "success"),
        ("probe", "prometheus", "failure"),
    ],
)
def test_candidate_v2_rejects_cross_endpoint_reason_families(
    target: str,
    source: str,
    outcome: str,
) -> None:
    payload = _valid_candidate_v2_payload()
    diagnostics = payload["endpoint_diagnostics"]
    assert isinstance(diagnostics, dict)
    diagnostic = diagnostics[target]
    assert isinstance(diagnostic, dict)
    if outcome == "success":
        parse_reason, freshness_reason = _TEST_ENDPOINT_SUCCESS_REASONS[source]
        diagnostic["parse_reason"] = parse_reason
        diagnostic["freshness_reason"] = freshness_reason
    else:
        diagnostic.update(
            {
                "parse_outcome": "FAILED",
                "parse_reason": _TEST_ENDPOINT_FAILURE_REASONS[source],
                "freshness_outcome": "NOT_EVALUATED",
                "freshness_reason": "NOT_EVALUATED_PARSE_FAILURE",
            }
        )
        endpoint_gates = payload["endpoint_gates"]
        assert isinstance(endpoint_gates, dict)
        endpoint_gates[target] = False
    propagation = _TEST_PROPAGATION_BY_ENDPOINT.get(target)
    if propagation is not None:
        propagation_diagnostics = payload["propagation_diagnostics"]
        propagation_gates = payload["propagation_gates"]
        assert isinstance(propagation_diagnostics, dict)
        assert isinstance(propagation_gates, dict)
        propagation_diagnostics[propagation] = dict(diagnostic)
        propagation_gates[propagation] = outcome == "success"

    with pytest.raises(ValidationError, match="reason family"):
        readiness_module.CandidateInitialReadiness.model_validate(payload)


@pytest.mark.parametrize(
    ("gate", "wrong_reason"),
    [
        ("load_generator_healthy", "OTEL_COLLECTOR_HEALTHY"),
        ("otel_collector_healthy", "LOAD_GENERATOR_HEALTHY"),
    ],
)
def test_candidate_v2_rejects_cross_lifecycle_reason_families(
    gate: str,
    wrong_reason: str,
) -> None:
    payload = _valid_candidate_v2_payload()
    diagnostics = payload["propagation_diagnostics"]
    assert isinstance(diagnostics, dict)
    diagnostic = diagnostics[gate]
    assert isinstance(diagnostic, dict)
    diagnostic["freshness_reason"] = wrong_reason

    with pytest.raises(ValidationError, match="reason family"):
        readiness_module.CandidateInitialReadiness.model_validate(payload)


def test_candidate_v2_backend_propagation_must_equal_endpoint_diagnostic() -> None:
    payload = _valid_candidate_v2_payload()
    endpoint_diagnostics = payload["endpoint_diagnostics"]
    propagation_diagnostics = payload["propagation_diagnostics"]
    endpoint_gates = payload["endpoint_gates"]
    propagation_gates = payload["propagation_gates"]
    assert isinstance(endpoint_diagnostics, dict)
    assert isinstance(propagation_diagnostics, dict)
    assert isinstance(endpoint_gates, dict)
    assert isinstance(propagation_gates, dict)
    endpoint = endpoint_diagnostics["prometheus"]
    propagation = propagation_diagnostics["prometheus_ad_getads_current"]
    assert isinstance(endpoint, dict)
    assert isinstance(propagation, dict)
    endpoint.update(
        {
            "freshness_outcome": "FAILED",
            "freshness_reason": "PROMETHEUS_STALE_SAMPLE",
        }
    )
    propagation.update(
        {
            "parse_outcome": "FAILED",
            "parse_reason": "PROMETHEUS_IDENTITY_MISMATCH",
            "freshness_outcome": "NOT_EVALUATED",
            "freshness_reason": "NOT_EVALUATED_PARSE_FAILURE",
        }
    )
    endpoint_gates["prometheus"] = False
    propagation_gates["prometheus_ad_getads_current"] = False

    with pytest.raises(ValidationError, match="endpoint diagnostic"):
        readiness_module.CandidateInitialReadiness.model_validate(payload)


@pytest.mark.parametrize(
    ("resource", "timestamp_offset", "parse_outcome", "parse_reason", "freshness_outcome", "freshness_reason"),
    [
        (
            {"service.name": "frontend"},
            5,
            "FAILED",
            "OPENSEARCH_IDENTITY_MISMATCH",
            "NOT_EVALUATED",
            "NOT_EVALUATED_PARSE_FAILURE",
        ),
        (
            {"service.name": "ad"},
            -5,
            "PASSED",
            "OPENSEARCH_IDENTITY_MATCH",
            "FAILED",
            "OPENSEARCH_STALE_LOG",
        ),
        (
            {"service.name": "ad", "service": {"name": "frontend"}},
            5,
            "FAILED",
            "OPENSEARCH_SERVICE_IDENTITY_CONFLICT",
            "NOT_EVALUATED",
            "NOT_EVALUATED_PARSE_FAILURE",
        ),
    ],
)
def test_candidate_opensearch_diagnostics_separate_identity_freshness_and_schema(
    resource: dict,
    timestamp_offset: int,
    parse_outcome: str,
    parse_reason: str,
    freshness_outcome: str,
    freshness_reason: str,
) -> None:
    window = _candidate_window()
    payload = {
        "hits": {
            "hits": [
                {
                    "_source": {
                        "resource": resource,
                        "@timestamp": (
                            window.utc_started_at
                            + readiness_module.timedelta(seconds=timestamp_offset)
                        ).isoformat(),
                    }
                }
            ]
        }
    }

    observed = readiness_module._candidate_opensearch_diagnostic(
        payload,
        window,
    )

    assert observed == (
        parse_outcome,
        parse_reason,
        freshness_outcome,
        freshness_reason,
    )


def test_candidate_opensearch_diagnostic_distinguishes_empty_hits_from_identity_mismatch(
) -> None:
    window = _candidate_window()
    empty = readiness_module._candidate_opensearch_diagnostic(
        {"hits": {"hits": []}},
        window,
    )
    mismatch = readiness_module._candidate_opensearch_diagnostic(
        {
            "hits": {
                "hits": [
                    {
                        "_source": {
                            "resource": {"service.name": "frontend"},
                            "@timestamp": window.utc_started_at.isoformat(),
                        }
                    }
                ]
            }
        },
        window,
    )

    assert empty[1] == "OPENSEARCH_EMPTY_HIT_SET"
    assert mismatch[1] == "OPENSEARCH_IDENTITY_MISMATCH"


def test_failed_six_attempt_candidate_persists_v2_exact_diagnostics(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class WrongIdentityClient(FakeClient):
        def request(self, request):
            exchange = super().request(request)
            if request.endpoint.service != "opensearch":
                return exchange
            payload = json.loads(exchange.raw_body)
            payload["hits"]["hits"][0]["_source"]["resource"][
                "service.name"
            ] = "frontend"
            body = json.dumps(payload).encode()
            return HttpExchange(
                reason=exchange.reason,
                request=exchange.request,
                started_at=exchange.started_at,
                ended_at=exchange.ended_at,
                monotonic_started_at=exchange.monotonic_started_at,
                monotonic_ended_at=exchange.monotonic_ended_at,
                status_code=exchange.status_code,
                response_headers=exchange.response_headers,
                raw_body=body,
                raw_sha256=sha256_bytes(body),
                raw_body_partial=False,
            )

    preflight = SimpleNamespace(
        run_id=RUN_ID,
        content_sha256="a" * 64,
        is_current=lambda: True,
        is_authentic=lambda: True,
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
    monkeypatch.setattr(readiness_module, "OwnedHttpClient", WrongIdentityClient)
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

    with pytest.raises(readiness_module.ReadinessCollectionError) as captured:
        collect_candidate_initial_readiness(
            project_root=tmp_path,
            artifacts_root=tmp_path,
            preflight=preflight,
            ownership=ownership,
            retry_sleep=lambda _seconds: None,
        )

    assert captured.value.reason_code == "INITIAL_CANDIDATE_READINESS_INCOMPLETE"
    assert captured.value.artifact_path is not None
    summary_path = Path(captured.value.artifact_path)
    payload = json.loads(summary_path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == "phase0.candidate-initial-readiness.v2"
    assert payload["attempt_count"] == 6
    assert payload["endpoint_gates"] == {
        "prometheus": True,
        "jaeger": True,
        "opensearch": False,
        "probe": True,
    }
    assert payload["propagation_gates"] == {
        "prometheus_ad_getads_current": True,
        "jaeger_load_to_ad_getads_current": True,
        "opensearch_ad_log_current": False,
        "load_generator_healthy": True,
        "otel_collector_healthy": True,
    }
    assert set(payload["endpoint_diagnostics"]) == set(
        payload["endpoint_gates"]
    )
    assert set(payload["propagation_diagnostics"]) == set(
        payload["propagation_gates"]
    )
    for endpoint, diagnostic in payload["endpoint_diagnostics"].items():
        assert diagnostic["attempt"] == 6
        assert diagnostic["raw_artifact"].endswith(
            f"attempt-06-{endpoint}-raw.json"
        )
    opensearch = payload["endpoint_diagnostics"]["opensearch"]
    assert opensearch["parse_outcome"] == "FAILED"
    assert opensearch["parse_reason"] == "OPENSEARCH_IDENTITY_MISMATCH"
    assert opensearch["freshness_outcome"] == "NOT_EVALUATED"
    assert (
        payload["propagation_diagnostics"]["opensearch_ad_log_current"]
        ["raw_artifact"]
        == opensearch["raw_artifact"]
    )
    for name in ("load_generator_healthy", "otel_collector_healthy"):
        diagnostic = payload["propagation_diagnostics"][name]
        assert diagnostic["raw_artifact"] == "artifact.json"
        assert diagnostic["transport_outcome"] == "NOT_APPLICABLE"
        assert diagnostic["http_outcome"] == "NOT_APPLICABLE"

    invalid = dict(payload)
    invalid["endpoint_diagnostics"] = dict(payload["endpoint_diagnostics"])
    invalid["endpoint_diagnostics"]["opensearch"] = {
        **payload["endpoint_diagnostics"]["opensearch"],
        "raw_artifact": payload["raw_artifacts"][2],
    }
    with pytest.raises(ValidationError, match="final raw artifact"):
        readiness_module.CandidateInitialReadiness.model_validate(invalid)

    missing_key = dict(payload)
    missing_key["endpoint_diagnostics"] = dict(payload["endpoint_diagnostics"])
    missing_key["endpoint_diagnostics"].pop("probe")
    with pytest.raises(ValidationError, match="diagnostic keys"):
        readiness_module.CandidateInitialReadiness.model_validate(missing_key)

    inconsistent = dict(payload)
    inconsistent["endpoint_gates"] = {
        **payload["endpoint_gates"],
        "opensearch": True,
    }
    with pytest.raises(ValidationError, match="gate and diagnostic disagree"):
        readiness_module.CandidateInitialReadiness.model_validate(inconsistent)

    historical = dict(payload)
    historical["schema_version"] = "phase0.candidate-initial-readiness.v1"
    historical.pop("endpoint_diagnostics")
    historical.pop("propagation_diagnostics")
    readiness_module.CandidateInitialReadiness.model_validate(historical)
