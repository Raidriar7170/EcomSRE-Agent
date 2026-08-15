from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlparse

import pytest

from ecomsre_live_sandbox.contracts import verify_private_tree_permissions
from ecomsre_live_sandbox.instrumentation_v2 import (
    EvidenceResolver,
    LogsSourceProbe,
    MetricsSourceProbe,
    PrivateArtifactStore,
    TracesSourceProbe,
    build_opensearch_target_query,
    build_instrumentation_report,
    discover_opensearch_fields,
    load_instrumentation_config,
    parse_jaeger_services,
    parse_jaeger_traces_v2,
    parse_opensearch_logs_v2,
    parse_prometheus_matrix_v2,
    parse_prometheus_vector_v2,
    required_prometheus_value,
    resolve_private_root,
    public_projection,
    scan_public_payload,
    SourceProbeFailure,
    SourceProbeResult,
    SourceProbeStatus,
    terminalize_source_probes,
    verify_public_result,
)
from ecomsre_live_sandbox import instrumentation_v2


NOW = datetime(2026, 8, 11, tzinfo=timezone.utc)
CONFIG = Path("config/live-telemetry-instrumentation-v2")


def _available(source: str, backend: str) -> SourceProbeResult:
    return SourceProbeResult(
        source=source,
        backend_kind=backend,
        status=SourceProbeStatus.AVAILABLE,
        window_start=NOW,
        window_end=NOW + timedelta(seconds=30),
        probe_started_at=NOW + timedelta(seconds=45),
        probe_ended_at=NOW + timedelta(seconds=46),
        attempt_count=1,
        backend_reachable=True,
        raw_response_count=1,
        parsed_record_count=1,
        target_record_count=1,
        service_catalog_count=1,
        target_service_present=True,
        identity_fields_present=("service.name",),
        raw_artifact_hashes={"raw.json": "a" * 64},
        evidence_refs=(f"{source.casefold()[:-1] if source.endswith('S') else source.casefold()}:0001",),
        invalid_ref_count=0,
    )


class _Probe:
    def __init__(self, result: SourceProbeResult | SourceProbeFailure) -> None:
        self.result = result
        self.source = result.source
        self.backend_kind = result.backend_kind

    def probe(self) -> SourceProbeResult:
        if isinstance(self.result, SourceProbeFailure):
            raise self.result
        return self.result


def test_source_failure_does_not_erase_other_terminal_results() -> None:
    metrics = _available("METRICS", "PROMETHEUS_HTTP_API")
    logs_failure = SourceProbeFailure(
        source="LOGS",
        backend_kind="OPENSEARCH_HTTP_API",
        status=SourceProbeStatus.SCHEMA_MISMATCH,
        safe_reason_code="LOG_SCHEMA_INVALID",
    )
    traces = _available("TRACES", "JAEGER_QUERY_API")

    results = terminalize_source_probes(
        (_Probe(metrics), _Probe(logs_failure), _Probe(traces)),
        window_start=NOW,
        window_end=NOW + timedelta(seconds=30),
    )

    assert tuple(result.source for result in results) == ("METRICS", "LOGS", "TRACES")
    assert results[0].status is SourceProbeStatus.AVAILABLE
    assert results[1].status is SourceProbeStatus.SCHEMA_MISMATCH
    assert results[1].safe_reason_code == "LOG_SCHEMA_INVALID"
    assert results[2].status is SourceProbeStatus.AVAILABLE


def test_prometheus_required_zero_is_distinct_from_absent() -> None:
    zero = parse_prometheus_vector_v2(
        {
            "status": "success",
            "data": {
                "resultType": "vector",
                "result": [
                    {"metric": {"service_name": "payment"}, "value": [1, "0"]},
                    {"metric": {"service_name": "checkout"}, "value": [1, "9"]},
                ],
            },
        },
        target_service="payment",
    )
    assert zero.series_count == 2
    assert zero.sample_count == 2
    assert zero.finite_value_count == 2
    assert zero.target_label_match_count == 1
    assert required_prometheus_value(zero, empty_reason="TARGET_TOTAL_SERIES_EMPTY") == 0.0

    absent = parse_prometheus_vector_v2(
        {
            "status": "success",
            "data": {
                "resultType": "vector",
                "result": [
                    {"metric": {"service_name": "checkout"}, "value": [1, "0"]}
                ],
            },
        },
        target_service="payment",
    )
    with pytest.raises(SourceProbeFailure) as failure:
        required_prometheus_value(absent, empty_reason="TARGET_TOTAL_SERIES_EMPTY")
    assert failure.value.status is SourceProbeStatus.EMPTY
    assert failure.value.safe_reason_code == "TARGET_TOTAL_SERIES_EMPTY"


def test_prometheus_matrix_counts_only_finite_target_samples() -> None:
    summary = parse_prometheus_matrix_v2(
        {
            "status": "success",
            "data": {
                "resultType": "matrix",
                "result": [
                    {
                        "metric": {"service_name": "payment"},
                        "values": [[1, "1"], [2, "2"], [3, "3"]],
                    },
                    {
                        "metric": {"service_name": "checkout"},
                        "values": [[1, "7"]],
                    },
                ],
            },
        },
        target_service="payment",
    )
    assert summary.series_count == 2
    assert summary.sample_count == 4
    assert summary.finite_value_count == 4
    assert summary.target_label_match_count == 1
    assert summary.target_sample_count == 3


def test_opensearch_field_caps_selects_observed_timestamp_and_exact_service_field() -> None:
    selected = discover_opensearch_fields(
        {
            "fields": {
                "observedTimestamp": {"date_nanos": {"type": "date_nanos", "searchable": True}},
                "@timestamp": {"text": {"type": "text", "searchable": True}},
                "resource.service.name": {"keyword": {"type": "keyword", "searchable": True}},
                "body": {"text": {"type": "text", "searchable": True}},
                "severity.text": {"keyword": {"type": "keyword", "searchable": True}},
            }
        },
        time_candidates=("observedTimestamp", "@timestamp"),
        service_candidates=("resource.service.name.keyword", "resource.service.name"),
        body_candidates=("body", "message"),
        severity_candidates=("severity.text.keyword", "severity.text"),
    )
    assert selected.time_field == "observedTimestamp"
    assert selected.service_field == "resource.service.name"

    query = build_opensearch_target_query(
        selected,
        target_service="payment",
        window_start=NOW,
        window_end=NOW + timedelta(seconds=30),
    )
    encoded = json.dumps(query, sort_keys=True)
    assert "observedTimestamp" in encoded
    assert "resource.service.name" in encoded
    assert "@timestamp" not in encoded


def test_opensearch_parser_uses_discovered_fields_and_bounds_body() -> None:
    selected = discover_opensearch_fields(
        {
            "fields": {
                "observedTimestamp": {"date": {"type": "date", "searchable": True}},
                "resource.service.name": {"keyword": {"type": "keyword", "searchable": True}},
                "body": {"text": {"type": "text", "searchable": True}},
                "severity.text": {"keyword": {"type": "keyword", "searchable": True}},
            }
        },
        time_candidates=("observedTimestamp",),
        service_candidates=("resource.service.name",),
        body_candidates=("body",),
        severity_candidates=("severity.text",),
    )
    records = parse_opensearch_logs_v2(
        {
            "hits": {
                "hits": [
                    {
                        "_source": {
                            "observedTimestamp": "2026-08-11T00:00:01Z",
                            "resource": {"service": {"name": "payment"}},
                            "severity": {"text": "INFO"},
                            "body": "x" * 3_000,
                            "traceId": "a" * 32,
                            "spanId": "b" * 16,
                        }
                    }
                ]
            }
        },
        selected=selected,
        target_service="payment",
    )
    assert len(records) == 1
    assert records[0].service_name == "payment"
    assert len(records[0].body) == 2_000


def test_jaeger_catalog_and_trace_envelope_are_typed() -> None:
    catalog = parse_jaeger_services({"data": ["checkout", "payment"]})
    assert catalog == ("checkout", "payment")
    traces = parse_jaeger_traces_v2(
        {
            "data": [
                {
                    "traceID": "a" * 32,
                    "processes": {
                        "p1": {
                            "serviceName": "payment",
                            "tags": [{"key": "service.instance.id", "value": "instance-1"}],
                        }
                    },
                    "spans": [
                        {
                            "traceID": "a" * 32,
                            "spanID": "b" * 16,
                            "processID": "p1",
                            "operationName": "charge",
                            "startTime": 1_000_000,
                            "duration": 20_000,
                            "tags": [{"key": "otel.status_code", "value": "OK"}],
                        }
                    ],
                }
            ]
        },
        target_service="payment",
    )
    assert len(traces) == 1
    assert traces[0].service_name == "payment"
    assert traces[0].service_instance_id == "instance-1"


def test_evidence_refs_resolve_and_duplicate_or_wrong_prefix_is_rejected(tmp_path: Path) -> None:
    store = PrivateArtifactStore(tmp_path / "telemetry")
    raw = store.write_raw("LOGS", "attempt-01", {"hits": {"hits": []}})
    reference = store.add_record(
        source="LOGS",
        raw_artifact=raw,
        normalized_record={"service_name": "payment", "body": "bounded"},
        window_start=NOW,
        window_end=NOW + timedelta(seconds=30),
        target_service="payment",
    )
    resolver_path = store.seal()
    resolver = EvidenceResolver.from_file(resolver_path)
    metadata = resolver.resolve(reference)
    assert metadata.source == "LOGS"
    assert len(metadata.raw_artifact_sha256) == 64
    assert not Path(metadata.private_artifact_relative_key).is_absolute()
    with pytest.raises(ValueError, match="duplicate"):
        store.add_record(
            source="LOGS",
            raw_artifact=raw,
            normalized_record={"service_name": "payment", "body": "bounded"},
            window_start=NOW,
            window_end=NOW + timedelta(seconds=30),
            target_service="payment",
        )
    with pytest.raises(ValueError, match="prefix"):
        resolver.resolve("trace:" + reference.split(":", 1)[1])


def test_target_service_and_readiness_limits_are_loaded_from_v2_config() -> None:
    config = load_instrumentation_config(CONFIG)
    assert config.environment.target_service == "payment"
    assert config.readiness.capture_window_seconds == 30
    assert config.readiness.ingestion_grace_seconds == 15
    assert config.readiness.poll_interval_seconds == 5
    assert config.readiness.maximum_readiness_seconds == 45
    assert config.readiness.maximum_probe_attempts == 7


def test_metrics_probe_accepts_absent_optional_error_after_required_total(tmp_path: Path) -> None:
    config = load_instrumentation_config(CONFIG)
    observed_urls: list[str] = []

    def request(url: str, **_: object) -> object:
        observed_urls.append(url)
        if "/status/config" in url:
            return {"status": "success", "data": {"yaml": "global: {}"}}
        if "/label/__name__/values" in url:
            return {
                "status": "success",
                "data": list(config.sources.prometheus.required_metric_names),
            }
        if "/query_range?" in url:
            return {
                "status": "success",
                "data": {
                    "resultType": "matrix",
                    "result": [
                        {
                            "metric": {"service_name": "checkout"},
                            "values": [[1, "1"], [2, "2"], [3, "3"]],
                        }
                    ],
                },
            }
        if "STATUS_CODE_ERROR" in url:
            result: list[object] = []
        elif "duration_milliseconds" in url:
            result = [{"metric": {"service_name": "checkout"}, "value": [1, "12"]}]
        elif "otel_sdk_exporter_span" in url:
            result = [{"metric": {"service_name": "checkout"}, "value": [1, "1"]}]
        else:
            result = [{"metric": {"service_name": "checkout"}, "value": [1, "8"]}]
        return {"status": "success", "data": {"resultType": "vector", "result": result}}

    result = MetricsSourceProbe(
        endpoint="http://127.0.0.1:19090",
        target_service="checkout",
        config=config.sources.prometheus,
        readiness=config.readiness,
        store=PrivateArtifactStore(tmp_path / "metrics"),
        window_start=NOW,
        window_end=NOW + timedelta(seconds=30),
        request_json=request,
        sleep=lambda _: None,
    ).probe()
    assert result.status is SourceProbeStatus.AVAILABLE
    assert result.attempt_count == 1
    assert len(result.evidence_refs) == 5
    assert all("checkout" in url or "/status/" in url or "/label/" in url for url in observed_urls)
    instant_queries = [
        parse_qs(urlparse(url).query)
        for url in observed_urls
        if urlparse(url).path.endswith("/query")
    ]
    expected_time = f"{(NOW + timedelta(seconds=30)).timestamp():.3f}"
    assert len(instant_queries) == 4
    assert all(parameters.get("time") == [expected_time] for parameters in instant_queries)
    range_queries = [
        parse_qs(urlparse(url).query)
        for url in observed_urls
        if urlparse(url).path.endswith("/query_range")
    ]
    assert range_queries == [
        {
            "query": [
                'sum by (service_name) (increase(traces_span_metrics_calls_total{service_name="checkout"}[30s]))'
            ],
            "start": [f"{NOW.timestamp():.3f}"],
            "end": [expected_time],
            "step": ["5"],
        }
    ]


def test_metrics_probe_preserves_the_frozen_window_across_readiness_retries(
    tmp_path: Path,
) -> None:
    config = load_instrumentation_config(CONFIG)
    observed_urls: list[str] = []
    attempt = 0

    def request(url: str, **_: object) -> object:
        nonlocal attempt
        observed_urls.append(url)
        parsed = urlparse(url)
        parameters = parse_qs(parsed.query)
        if "/status/config" in url:
            attempt += 1
            return {"status": "success", "data": {"yaml": "global: {}"}}
        if "/label/__name__/values" in url:
            return {
                "status": "success",
                "data": list(config.sources.prometheus.required_metric_names),
            }
        if parsed.path.endswith("/query_range"):
            return {
                "status": "success",
                "data": {
                    "resultType": "matrix",
                    "result": [
                        {
                            "metric": {"service_name": "checkout"},
                            "values": [[1, "1"], [2, "2"], [3, "3"]],
                        }
                    ],
                },
            }
        query = parameters["query"][0]
        if attempt == 1 and "increase(" in query and "STATUS_CODE_ERROR" not in query:
            result: list[object] = []
        elif "STATUS_CODE_ERROR" in query:
            result = []
        else:
            result = [
                {"metric": {"service_name": "checkout"}, "value": [1, "8"]}
            ]
        return {"status": "success", "data": {"resultType": "vector", "result": result}}

    result = MetricsSourceProbe(
        endpoint="http://127.0.0.1:19090",
        target_service="checkout",
        config=config.sources.prometheus,
        readiness=config.readiness,
        store=PrivateArtifactStore(tmp_path / "metrics-retry"),
        window_start=NOW,
        window_end=NOW + timedelta(seconds=30),
        request_json=request,
        sleep=lambda _: None,
    ).probe()
    assert result.status is SourceProbeStatus.AVAILABLE
    assert result.attempt_count == 2
    expected_start = f"{NOW.timestamp():.3f}"
    expected_end = f"{(NOW + timedelta(seconds=30)).timestamp():.3f}"
    instant_parameters = [
        parse_qs(urlparse(url).query)
        for url in observed_urls
        if urlparse(url).path.endswith("/query")
    ]
    range_parameters = [
        parse_qs(urlparse(url).query)
        for url in observed_urls
        if urlparse(url).path.endswith("/query_range")
    ]
    assert len(instant_parameters) == 8
    assert all(item["time"] == [expected_end] for item in instant_parameters)
    assert len(range_parameters) == 2
    assert all(item["start"] == [expected_start] for item in range_parameters)
    assert all(item["end"] == [expected_end] for item in range_parameters)
    assert all(item["step"] == ["5"] for item in range_parameters)


def test_logs_probe_uses_discovered_fields_for_catalog_and_target_query(tmp_path: Path) -> None:
    config = load_instrumentation_config(CONFIG)
    posted: list[object] = []
    field_caps_requests: list[tuple[str, object]] = []

    def request(url: str, **kwargs: object) -> object:
        if "_cat/indices" in url:
            return [{"index": "otel-logs-2026.08.11"}]
        if "_field_caps" in url:
            field_caps_requests.append((url, kwargs))
            return {
                "fields": {
                    "observedTimestamp": {"date_nanos": {"type": "date_nanos", "searchable": True}},
                    "resource.service.name": {"keyword": {"type": "keyword", "searchable": True}},
                    "body": {"text": {"type": "text", "searchable": True}},
                    "severity.text": {"keyword": {"type": "keyword", "searchable": True}},
                }
            }
        payload = kwargs.get("payload")
        posted.append(payload)
        if isinstance(payload, dict) and payload.get("size") == 0:
            return {
                "hits": {"hits": []},
                "aggregations": {
                    "services": {"buckets": [{"key": "payment", "doc_count": 2}]}
                },
            }
        return {
            "hits": {
                "hits": [
                    {
                        "_source": {
                            "observedTimestamp": "2026-08-11T00:00:02Z",
                            "resource.service.name": "payment",
                            "severity.text": "INFO",
                            "body": "ok",
                        }
                    }
                ]
            }
        }

    result = LogsSourceProbe(
        endpoint="http://127.0.0.1:19200",
        target_service="payment",
        config=config.sources.opensearch,
        readiness=config.readiness,
        store=PrivateArtifactStore(tmp_path / "logs"),
        window_start=NOW,
        window_end=NOW + timedelta(seconds=30),
        request_json=request,
        sleep=lambda _: None,
    ).probe()
    assert result.status is SourceProbeStatus.AVAILABLE
    assert result.selected_time_field == "observedTimestamp"
    assert result.selected_service_field == "resource.service.name"
    assert result.service_catalog_count == 1
    assert "@timestamp" not in json.dumps(posted, sort_keys=True)
    assert len(field_caps_requests) == 1
    field_caps_url, field_caps_kwargs = field_caps_requests[0]
    assert field_caps_kwargs == {}
    assert parse_qs(urlparse(field_caps_url).query) == {
        "fields": [
            ",".join(
                sorted(
                    set(
                        config.sources.opensearch.time_field_candidates
                        + config.sources.opensearch.service_field_candidates
                        + config.sources.opensearch.body_field_candidates
                        + config.sources.opensearch.severity_field_candidates
                    )
                )
            )
        ]
    }


def test_logs_probe_distinguishes_missing_index_from_unreachable_backend(
    tmp_path: Path,
) -> None:
    config = load_instrumentation_config(CONFIG)

    def request(url: str, **_: object) -> object:
        raise HTTPError(url, 404, "not found", hdrs=None, fp=None)

    store = PrivateArtifactStore(tmp_path / "logs")
    result = LogsSourceProbe(
        endpoint="http://127.0.0.1:19200",
        target_service="payment",
        config=config.sources.opensearch,
        readiness=config.readiness,
        store=store,
        window_start=NOW,
        window_end=NOW + timedelta(seconds=30),
        request_json=request,
        sleep=lambda _: None,
    ).probe()

    assert result.status is SourceProbeStatus.INGESTION_TIMEOUT
    assert result.attempt_count == 7
    assert result.backend_reachable is True
    assert result.safe_reason_code == "LOG_INDEX_MISSING"
    diagnostics = sorted((store.root / "diagnostics" / "logs").glob("*.json"))
    assert len(diagnostics) == 7
    assert json.loads(diagnostics[0].read_text(encoding="utf-8")) == {
        "exception_type": "HTTPError",
        "http_status": 404,
        "request_phase": "INDEX_DISCOVERY",
    }
    assert diagnostics[0].stat().st_mode & 0o777 == 0o600


def test_traces_readiness_is_bounded_when_target_catalog_entry_never_appears(tmp_path: Path) -> None:
    config = load_instrumentation_config(CONFIG)
    sleeps: list[float] = []
    requests = 0

    def request(_: str, **__: object) -> object:
        nonlocal requests
        requests += 1
        return {"data": ["checkout"]}

    result = TracesSourceProbe(
        endpoint="http://127.0.0.1:11686",
        target_service="payment",
        config=config.sources.jaeger,
        readiness=config.readiness,
        store=PrivateArtifactStore(tmp_path / "traces"),
        window_start=NOW,
        window_end=NOW + timedelta(seconds=30),
        request_json=request,
        sleep=sleeps.append,
    ).probe()
    assert result.status is SourceProbeStatus.INGESTION_TIMEOUT
    assert result.attempt_count == 7
    assert result.safe_reason_code == "TARGET_SERVICE_NOT_IN_JAEGER"
    assert requests == 7
    assert sleeps == [5] * 6


def test_canonical_gate_and_public_verifier_recompute_all_source_truth() -> None:
    metrics = _available("METRICS", "PROMETHEUS_HTTP_API")
    logs = _available("LOGS", "OPENSEARCH_HTTP_API").model_copy(
        update={
            "selected_time_field": "observedTimestamp",
            "selected_service_field": "resource.service.name",
        }
    )
    traces = _available("TRACES", "JAEGER_QUERY_API")
    report = build_instrumentation_report(
        environment_id="opentelemetry-demo-local-v1",
        sandbox_binding_sha256="b" * 64,
        resolved_compose_sha256="c" * 64,
        target_service="payment",
        window_start=NOW,
        window_end=NOW + timedelta(seconds=30),
        ingestion_grace_seconds=15,
        metrics=metrics,
        logs=logs,
        traces=traces,
        all_refs_resolve=True,
        canonical_preflight=True,
        cleanup={
            "baseline_restored": True,
            "owned_containers": 0,
            "owned_networks": 0,
            "owned_volumes": 0,
            "non_owned_resources_changed": False,
            "verdict": "CLEAN",
        },
    )
    assert report.final_verdict == "LIVE_TELEMETRY_INSTRUMENTATION_V2_READY_FOR_E2E"
    public = public_projection(report, claim_boundary=load_instrumentation_config(CONFIG).reporting.claim_boundary)
    verify_public_result(public)
    assert scan_public_payload(public) == ()
    assert "sandbox_binding_sha256" not in public
    assert "evidence_refs" not in json.dumps(public, sort_keys=True)

    forged = json.loads(json.dumps(public))
    forged["sources"]["LOGS"]["target_record_count"] = 0
    with pytest.raises(ValueError, match="truth gate"):
        verify_public_result(forged)

    boolean_forgery = json.loads(json.dumps(public))
    boolean_forgery["sources"]["LOGS"]["target_record_count"] = True
    boolean_forgery["safety"]["provider_calls"] = False
    with pytest.raises(ValueError, match="truth gate"):
        verify_public_result(boolean_forgery)


def test_public_leakage_scan_rejects_endpoint_path_and_runtime_identity() -> None:
    findings = scan_public_payload(
        {
            "endpoint": "http://127.0.0.1:19200",
            "private_path": "/Users/example/.ecomsre/private/run.json",
            "trace_id": "a" * 32,
        }
    )
    assert set(findings) == {"FORBIDDEN_KEY", "LOCAL_ENDPOINT", "PRIVATE_PATH", "RUNTIME_ID"}


def test_v2_runtime_has_no_invocation_or_provider_or_remediation_entrypoint() -> None:
    source = Path("src/ecomsre_live_sandbox/instrumentation_v2.py").read_text(
        encoding="utf-8"
    )
    for forbidden in (
        "run_invocation_a",
        "run_invocation_b",
        "make_provider",
        "run_a0_diagnosis",
        "restore_baseline(",
        "restore_fault(",
    ):
        assert forbidden not in source


def test_private_roots_are_outside_git_and_have_exact_directory_permissions(tmp_path: Path) -> None:
    roots = resolve_private_root(tmp_path / "private-root", repository_root=Path.cwd())
    store = PrivateArtifactStore(roots.telemetry)
    store.write_raw("METRICS", "sample", {"value": 1})
    store.write_diagnostic("LOGS", "sample", {"safe": True})
    for path in (
        roots.root,
        roots.control,
        roots.runtime,
        roots.telemetry,
        roots.reports,
        roots.development_probes,
        roots.canonical_preflight,
    ):
        assert path.stat().st_mode & 0o777 == 0o700
    descendants = tuple(path for path in roots.root.rglob("*") if path.is_dir())
    assert descendants
    assert all(path.stat().st_mode & 0o777 == 0o700 for path in descendants)
    verify_private_tree_permissions(roots.root)
    (roots.telemetry / "raw").chmod(0o755)
    with pytest.raises(PermissionError, match="private directory permissions"):
        verify_private_tree_permissions(roots.root)
    (roots.telemetry / "raw").chmod(0o700)
    private_file = roots.telemetry / "raw/metrics/sample.json"
    private_file.chmod(0o644)
    with pytest.raises(PermissionError, match="private file permissions"):
        verify_private_tree_permissions(roots.root)
    with pytest.raises(ValueError, match="inside the repository"):
        resolve_private_root(Path.cwd() / "private-root", repository_root=Path.cwd())


def test_canonical_admission_recomputes_latest_source_truth(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    roots = resolve_private_root(
        tmp_path / "private",
        repository_root=Path.cwd(),
    )
    probe = roots.development_probes / "development-probe-04"
    probe.mkdir(mode=0o700)
    terminal_path = probe / "development-probe-04.json"
    terminal_path.write_text(
        json.dumps(
            {
                "schema_version": "live-telemetry-instrumentation-v2.terminal.v1",
                "mode": "DEVELOPMENT_PROBE",
                "development_probe_number": 4,
                "verdict": "DEVELOPMENT_PROBE_AVAILABLE",
                "sandbox_startup_attempted": True,
                "all_refs_resolve": True,
                "sources": {
                    "METRICS": {
                        "status": "AVAILABLE",
                        "target_record_count": 5,
                        "invalid_ref_count": 0,
                    },
                    "LOGS": {
                        "status": "EMPTY",
                        "target_record_count": 0,
                        "invalid_ref_count": 0,
                    },
                    "TRACES": {
                        "status": "AVAILABLE",
                        "target_record_count": 8,
                        "invalid_ref_count": 0,
                    },
                },
                "cleanup": {
                    "baseline_restored": True,
                    "owned_containers": 0,
                    "owned_networks": 0,
                    "owned_volumes": 0,
                    "non_owned_resources_changed": False,
                    "verdict": "CLEAN",
                },
            }
        ),
        encoding="utf-8",
    )
    terminal_path.chmod(0o600)

    def fake_git(_: Path, *arguments: str) -> str:
        if arguments == ("status", "--porcelain=v1"):
            return ""
        if arguments == ("branch", "--show-current"):
            return "feature/live-telemetry-instrumentation-v2"
        return "a" * 40

    monkeypatch.setattr(instrumentation_v2, "_git", fake_git)
    with pytest.raises(RuntimeError, match="3/3 AVAILABLE"):
        instrumentation_v2._verify_canonical_admission(
            tmp_path,
            roots,
            implementation_ci_passed=True,
        )
