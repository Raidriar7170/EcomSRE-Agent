from __future__ import annotations

from datetime import datetime, timedelta, timezone
import inspect
import json
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

from ecomsre_live_sandbox import e2e_v1
from ecomsre_live_sandbox.contracts import LocalEndpoints
from ecomsre_live_sandbox.e2e_diagnostics import (
    DiagnosticJournal,
    DiagnosticRunKind,
    ExceptionArtifactStore,
)
from ecomsre_live_sandbox.e2e_source_batch import collect_ordered_source_batch
from ecomsre_live_sandbox.e2e_telemetry import (
    LiveLogObservation,
    LiveMetricObservation,
    LiveTraceObservation,
)
from ecomsre_live_sandbox.e2e_v3_contracts import load_e2e_v3_config
from ecomsre_live_sandbox.e2e_v3 import _StageTracker
from ecomsre_live_sandbox.e2e_v4 import _collect_v4_no_fault_evidence
from ecomsre_live_sandbox.e2e_v4_contracts import (
    E2EV4PrivateRoots,
    load_e2e_v4_config,
)
import ecomsre_live_sandbox.e2e_v5 as e2e_v5_module
from ecomsre_live_sandbox.e2e_v5 import _collect_v5_no_fault_evidence
from ecomsre_live_sandbox.e2e_v5_contracts import (
    E2EV5PrivateRoots,
    load_e2e_v5_config,
)
from ecomsre_live_sandbox.instrumentation_v2 import (
    LogsSourceProbe,
    MetricsSourceProbe,
    PrivateArtifactStore,
    SourceProbeStatus,
    TracesSourceProbe,
    load_instrumentation_config,
    terminalize_source_probes,
)


NOW = datetime(2026, 8, 12, tzinfo=timezone.utc)
TELEMETRY_CONFIG = Path("config/live-telemetry-instrumentation-v3")
E2E_CONFIG = Path("config/live-fault-a0-controlled-remediation-e2e-v3")
E2E_V4_CONFIG = Path("config/live-fault-a0-controlled-remediation-e2e-v4")
E2E_V5_CONFIG = Path("config/live-fault-a0-controlled-remediation-e2e-v5")
ENDPOINTS = LocalEndpoints(
    frontend="http://127.0.0.1:18080",
    flag_control="http://127.0.0.1:18080/feature/api",
    flag_evaluation="http://127.0.0.1:18016",
    prometheus="http://127.0.0.1:19090",
    opensearch="http://127.0.0.1:19200",
    jaeger="http://127.0.0.1:11686",
)


class Clock:
    def __init__(self) -> None:
        self.value = NOW

    def now(self) -> datetime:
        current = self.value
        self.value += timedelta(seconds=30)
        return current


class FixtureTransports:
    def __init__(self, *, metrics_available: bool = True) -> None:
        self.metrics_available = metrics_available
        self.calls = {"METRICS": 0, "LOGS": 0, "TRACES": 0}

    def metrics(self, url: str, **_: object) -> object:
        self.calls["METRICS"] += 1
        config = load_instrumentation_config(TELEMETRY_CONFIG)
        if "/status/config" in url:
            return {"status": "success", "data": {"yaml": "global: {}"}}
        if "/label/__name__/values" in url:
            names = list(config.sources.prometheus.required_metric_names)
            return {"status": "success", "data": names if self.metrics_available else []}
        query = parse_qs(urlparse(url).query).get("query", [""])[0]
        if "/query_range?" in url:
            return {
                "status": "success",
                "data": {
                    "resultType": "matrix",
                    "result": [
                        {
                            "metric": {"service_name": "payment"},
                            "values": [[1, "3"], [2, "4"], [3, "5"]],
                        }
                    ],
                },
            }
        value = "1" if "otel_sdk_exporter_span" in query else "12"
        if "STATUS_CODE_ERROR" in query:
            value = "2"
        if "duration_milliseconds" in query:
            value = "25"
        return {
            "status": "success",
            "data": {
                "resultType": "vector",
                "result": [
                    {"metric": {"service_name": "payment"}, "value": [1, value]}
                ],
            },
        }

    def logs(self, url: str, **kwargs: object) -> object:
        self.calls["LOGS"] += 1
        if "_cat/indices" in url:
            return [{"index": "otel-logs-2026.08.12"}]
        if "_field_caps" in url:
            return {
                "fields": {
                    "observedTimestamp": {
                        "date_nanos": {"type": "date_nanos", "searchable": True}
                    },
                    "resource.service.name": {
                        "keyword": {"type": "keyword", "searchable": True}
                    },
                    "body": {"text": {"type": "text", "searchable": True}},
                    "severity.text": {
                        "keyword": {"type": "keyword", "searchable": True}
                    },
                }
            }
        payload = kwargs.get("payload")
        if isinstance(payload, dict) and payload.get("size") == 0:
            return {
                "hits": {"hits": []},
                "aggregations": {
                    "services": {"buckets": [{"key": "payment", "doc_count": 1}]}
                },
            }
        return {
            "hits": {
                "hits": [
                    {
                        "_source": {
                            "observedTimestamp": "2026-08-12T00:00:20Z",
                            "resource.service.name": "payment",
                            "severity.text": "ERROR",
                            "body": "observed payment request error",
                            "traceId": "a" * 32,
                            "spanId": "b" * 16,
                        }
                    }
                ]
            }
        }

    def traces(self, url: str, **_: object) -> object:
        self.calls["TRACES"] += 1
        if "/services" in url:
            return {"data": ["checkout", "payment", "frontend"]}
        return {
            "data": [
                {
                    "traceID": "a" * 32,
                    "processes": {
                        "p1": {
                            "serviceName": "payment",
                            "tags": [
                                {"key": "service.instance.id", "value": "payment-1"}
                            ],
                        }
                    },
                    "spans": [
                        {
                            "traceID": "a" * 32,
                            "spanID": "b" * 16,
                            "processID": "p1",
                            "operationName": "charge",
                            "startTime": 1_786_492_820_000_000,
                            "duration": 20_000,
                            "tags": [{"key": "otel.status_code", "value": "ERROR"}],
                        }
                    ],
                }
            ]
        }


def _projection_inputs(
    _: LocalEndpoints,
    window_start: datetime,
    __: datetime,
    ___: object,
) -> tuple[
    tuple[LiveMetricObservation, ...],
    tuple[LiveLogObservation, ...],
    tuple[LiveTraceObservation, ...],
]:
    services = ("checkout", "currency", "frontend")
    metrics = tuple(
        LiveMetricObservation(
            service_name=service,
            baseline_requests=100,
            baseline_errors=1,
            fault_requests=100,
            fault_errors=10 + index,
            baseline_p95_ms=20,
            fault_p95_ms=30 + index,
        )
        for index, service in enumerate(services)
    )
    logs = tuple(
        LiveLogObservation(
            observed_at=window_start,
            service_name=service,
            severity="ERROR",
            body="observed request error",
        )
        for service in services
    )
    traces = tuple(
        LiveTraceObservation(
            observed_at=window_start,
            service_name=service,
            operation="request",
            status="ERROR",
            duration_ms=20,
        )
        for service in services
    )
    return metrics, logs, traces


def _collect(
    tmp_path: Path,
    transports: FixtureTransports,
    *,
    run_id: str = "probe-01",
    tracker: _StageTracker | None = None,
) -> object:
    run_root = tmp_path / "development" / run_id
    run_root.mkdir(parents=True)
    return collect_ordered_source_batch(
        instrumentation=load_instrumentation_config(TELEMETRY_CONFIG),
        endpoints=ENDPOINTS,
        telemetry_root=tmp_path / "telemetry",
        run_root=run_root,
        run_id=run_id,
        projection=load_e2e_v3_config(E2E_CONFIG).projection,
        tracker=tracker,
        sleep=lambda _: None,
        now=Clock().now,
        metrics_request_json=transports.metrics,
        logs_request_json=transports.logs,
        traces_request_json=transports.traces,
    )


def test_real_collector_executes_ordered_batch_and_resolvers_without_projection(
    tmp_path: Path,
) -> None:
    transports = FixtureTransports()

    evidence = _collect(tmp_path, transports)

    assert tuple(item.source for item in evidence.source_results) == (
        "METRICS",
        "LOGS",
        "TRACES",
    )
    assert all(
        item.status is SourceProbeStatus.AVAILABLE for item in evidence.source_results
    )
    assert all(item.target_record_count > 0 for item in evidence.source_results)
    assert evidence.invalid_refs == 0
    assert evidence.all_refs_resolve is True
    assert len(evidence.combined_resolver_sha256) == 64
    assert len(evidence.source_results_sha256) == 64
    assert all(count > 0 for count in transports.calls.values())
    assert all(
        (tmp_path / "telemetry" / "probe-01" / source / "resolver.json").is_file()
        for source in ("metrics", "logs", "traces")
    )
    source_results = json.loads(
        (tmp_path / "development" / "probe-01" / "source-results.json").read_text()
    )
    assert [item["source"] for item in source_results["results"]] == [
        "METRICS",
        "LOGS",
        "TRACES",
    ]
    assert not (tmp_path / "telemetry" / "probe-01" / "projection").exists()
    assert not (tmp_path / "telemetry" / "probe-01" / "model-evidence-index.json").exists()


def test_production_v4_adapter_constructs_no_fault_evidence_from_real_collector(
    tmp_path: Path,
) -> None:
    config = load_e2e_v4_config(E2E_V4_CONFIG)
    roots = E2EV4PrivateRoots(tmp_path / "private-v4")
    roots.prepare()
    run_root = roots.probe_root(1)
    run_root.mkdir(parents=True)
    transports = FixtureTransports()

    evidence = _collect_v4_no_fault_evidence(
        config,
        roots,
        run_root,
        None,
        ENDPOINTS,
        lambda _: None,
        metrics_request_json=transports.metrics,
        logs_request_json=transports.logs,
        traces_request_json=transports.traces,
        projection_collector=_projection_inputs,
        now=Clock().now,
    )

    assert evidence.metrics_status == "AVAILABLE"
    assert evidence.logs_status == "AVAILABLE"
    assert evidence.traces_status == "AVAILABLE"
    assert tuple(evidence.source_counts) == ("METRICS", "LOGS", "TRACES")
    assert all(count > 0 for count in evidence.source_counts.values())
    assert evidence.invalid_refs == 0
    assert 3 <= evidence.visible_service_count <= 8
    assert evidence.scenario_truth_leaked is False
    assert all(count > 0 for count in transports.calls.values())


def test_production_v5_adapter_evaluates_readiness_without_projection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = load_e2e_v5_config(E2E_V5_CONFIG)
    roots = E2EV5PrivateRoots(tmp_path / "private-v5")
    roots.prepare()
    run_root = roots.probe_root(1)
    run_root.mkdir(parents=True)
    transports = FixtureTransports()
    journal = DiagnosticJournal(
        run_root / "events.jsonl",
        run_kind=DiagnosticRunKind.DEVELOPMENT_PROBE,
        run_id="probe-01",
    )
    tracker = _StageTracker(
        journal,
        ExceptionArtifactStore(run_root / "exceptions"),
    )
    monkeypatch.setattr(
        e2e_v5_module,
        "_broad_metric_snapshot",
        lambda *_args, **_kwargs: {
            service: (100.0, 1.0, 20.0)
            for service in ("checkout", "currency", "frontend", "payment")
        },
    )

    evidence = _collect_v5_no_fault_evidence(
        config,
        roots,
        run_root,
        tracker,
        ENDPOINTS,
        lambda _: None,
        services_healthy_count=25,
        baseline_exact=True,
        metrics_request_json=transports.metrics,
        logs_request_json=transports.logs,
        traces_request_json=transports.traces,
        now=Clock().now,
    )

    assert evidence.readiness.passed is True
    assert evidence.readiness.broad_metric_service_count == 4
    assert evidence.source_batch.all_refs_resolve is True
    assert not (run_root / "projection-summary.json").exists()
    assert not (roots.telemetry / "probe-01" / "projection").exists()


def test_broad_log_projection_bounds_field_caps_and_prefers_keyword_multifield(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    search_payload: dict[str, object] = {}

    def strict_json(
        url: str,
        *,
        method: str = "GET",
        payload: object | None = None,
    ) -> object:
        if "/_field_caps?" in url:
            fields = set(parse_qs(urlparse(url).query)["fields"][0].split(","))
            assert fields == {
                "body",
                "body.keyword",
                "message",
                "observedTimestamp",
                "resource.service.name.keyword",
                "severity.text",
                "severity.text.keyword",
                "severityText",
                "severityText.keyword",
            }
            return {
                "fields": {
                    "observedTimestamp": {
                        "date": {"type": "date", "searchable": True}
                    },
                    "resource.service.name.keyword": {
                        "keyword": {"type": "keyword", "searchable": True}
                    },
                    "severity.text": {
                        "text": {"type": "text", "searchable": True}
                    },
                    "severity.text.keyword": {
                        "keyword": {"type": "keyword", "searchable": True}
                    },
                    "body": {"text": {"type": "text", "searchable": True}},
                }
            }
        assert method == "POST"
        assert isinstance(payload, dict)
        search_payload.update(payload)
        return {
            "hits": {
                "hits": [
                    {
                        "_source": {
                            "observedTimestamp": "2026-08-12T00:00:20Z",
                            "resource": {"service": {"name": "payment"}},
                            "severity": {"text": "ERROR"},
                            "body": "observed payment request error",
                        }
                    }
                ]
            }
        }

    monkeypatch.setattr(e2e_v1, "_strict_json", strict_json)

    logs = e2e_v1._capture_broad_logs(
        ENDPOINTS.opensearch,
        window_start=NOW,
        window_end=NOW + timedelta(seconds=30),
        maximum_hits=10,
    )

    should = search_payload["query"]["bool"]["should"]  # type: ignore[index]
    assert {"terms": {"severity.text.keyword": ["WARN", "WARNING", "ERROR", "FATAL"]}} in should
    assert len(logs) == 1
    assert logs[0].severity == "ERROR"


def test_unavailable_metrics_preserves_logs_and_traces_terminals(tmp_path: Path) -> None:
    transports = FixtureTransports(metrics_available=False)

    with pytest.raises(RuntimeError, match="sources are unavailable"):
        _collect(tmp_path, transports)

    assert all(count > 0 for count in transports.calls.values())
    payload = json.loads(
        (tmp_path / "development" / "probe-01" / "source-results.json").read_text()
    )
    results = payload["results"]
    assert [item["source"] for item in results] == ["METRICS", "LOGS", "TRACES"]
    assert results[0]["status"] != SourceProbeStatus.AVAILABLE.value
    assert results[1]["status"] == SourceProbeStatus.AVAILABLE.value
    assert results[2]["status"] == SourceProbeStatus.AVAILABLE.value


def test_source_batch_hashes_are_run_scoped_without_model_evidence(tmp_path: Path) -> None:
    first = _collect(tmp_path, FixtureTransports(), run_id="probe-01")
    second = _collect(tmp_path, FixtureTransports(), run_id="probe-02")

    assert len(first.source_results_sha256) == 64
    assert len(second.source_results_sha256) == 64
    assert (
        tmp_path / "development" / "probe-01" / "source-results.json"
    ).is_file()
    assert (
        tmp_path / "development" / "probe-02" / "source-results.json"
    ).is_file()
    assert not (
        tmp_path / "telemetry" / "probe-01" / "model-evidence-index.json"
    ).exists()
    assert not (
        tmp_path / "telemetry" / "probe-02" / "model-evidence-index.json"
    ).exists()
    assert not (tmp_path / "telemetry" / "model-evidence-index.json").exists()


def test_real_collector_preserves_monotonic_v4_journal_stage_order(
    tmp_path: Path,
) -> None:
    run_id = "probe-01"
    journal = DiagnosticJournal(
        tmp_path / "events.jsonl",
        run_kind=DiagnosticRunKind.DEVELOPMENT_PROBE,
        run_id=run_id,
    )
    tracker = _StageTracker(
        journal,
        ExceptionArtifactStore(tmp_path / "exceptions"),
    )

    evidence = _collect(
        tmp_path,
        FixtureTransports(),
        run_id=run_id,
        tracker=tracker,
    )

    assert evidence.all_refs_resolve is True
    events = [json.loads(line) for line in journal.path.read_text().splitlines()]
    passed_stages = [
        event["stage"] for event in events if event["status"] == "PASSED"
    ]
    assert passed_stages.index("SOURCE_BATCH_TERMINALIZATION_COMPLETED") < (
        passed_stages.index("METRICS_PREFLIGHT_COMPLETED")
    )
    assert passed_stages.index("METRICS_PREFLIGHT_COMPLETED") < (
        passed_stages.index("LOGS_PREFLIGHT_COMPLETED")
    )
    assert passed_stages.index("LOGS_PREFLIGHT_COMPLETED") < (
        passed_stages.index("TRACES_PREFLIGHT_COMPLETED")
    )


def test_wrong_source_order_is_rejected_before_execution(tmp_path: Path) -> None:
    config = load_instrumentation_config(TELEMETRY_CONFIG)
    transports = FixtureTransports()
    window_end = NOW + timedelta(seconds=30)
    probes = (
        LogsSourceProbe(
            endpoint=ENDPOINTS.opensearch,
            target_service="payment",
            config=config.sources.opensearch,
            readiness=config.readiness,
            store=PrivateArtifactStore(tmp_path / "logs"),
            window_start=NOW,
            window_end=window_end,
            request_json=transports.logs,
            sleep=lambda _: None,
        ),
        MetricsSourceProbe(
            endpoint=ENDPOINTS.prometheus,
            target_service="payment",
            config=config.sources.prometheus,
            readiness=config.readiness,
            store=PrivateArtifactStore(tmp_path / "metrics"),
            window_start=NOW,
            window_end=window_end,
            request_json=transports.metrics,
            sleep=lambda _: None,
        ),
        TracesSourceProbe(
            endpoint=ENDPOINTS.jaeger,
            target_service="payment",
            config=config.sources.jaeger,
            readiness=config.readiness,
            store=PrivateArtifactStore(tmp_path / "traces"),
            window_start=NOW,
            window_end=window_end,
            request_json=transports.traces,
            sleep=lambda _: None,
        ),
    )

    with pytest.raises(ValueError, match="ordered METRICS, LOGS, TRACES"):
        terminalize_source_probes(probes, window_start=NOW, window_end=window_end)

    assert transports.calls == {"METRICS": 0, "LOGS": 0, "TRACES": 0}


def test_v4_collector_has_no_singleton_terminalization() -> None:
    source = inspect.getsource(collect_ordered_source_batch)

    assert "terminalize_source_probes((" not in source
    assert "probes = (metrics, logs, traces)" in source
    assert source.count("terminalize_source_probes(") == 1
    assert "build_live_a0_context" not in source
