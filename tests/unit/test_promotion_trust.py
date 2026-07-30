from __future__ import annotations

import base64
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import ecomsre.telemetry.prometheus as prometheus_module
import ecomsre.telemetry.probe as probe_module
import pytest
from ecomsre.evidence.hashes import sha256_bytes
from ecomsre.evidence.store import ObserverEvidenceStore
from ecomsre.environment.ownership import OwnedResource
from ecomsre.telemetry.http import (
    HttpExchange,
    HttpReason,
    HttpRequest,
    OwnedEndpoint,
    PhaseWindow,
)
from ecomsre.phase0.models import MeasurementPhase


RUN_ID = "b" * 32
NOW = datetime(2026, 7, 30, 1, 2, 0, tzinfo=UTC)


def _window() -> PhaseWindow:
    return PhaseWindow(
        run_id=RUN_ID,
        cycle_number=1,
        scenario_phase=MeasurementPhase.BASELINE,
        utc_started_at=NOW,
        utc_ended_at=NOW + timedelta(seconds=30),
        monotonic_started_at=0,
        monotonic_ended_at=30,
    )


def test_promotion_exchange_is_persisted_before_parse_even_on_partial_failure(
    tmp_path: Path,
) -> None:
    request = HttpRequest(
        endpoint=OwnedEndpoint(
            base_url="http://127.0.0.1:32771",
            service="prometheus",
            target_port=9090,
        ),
        method="GET",
        target="/api/v1/query?query=up",
        absolute_deadline_monotonic=30,
    )
    raw = b'{"partial":'
    exchange = HttpExchange(
        reason=HttpReason.HTTP_TRANSPORT_ERROR,
        request=request,
        started_at=NOW + timedelta(seconds=1),
        ended_at=NOW + timedelta(seconds=2),
        monotonic_started_at=1,
        monotonic_ended_at=2,
        status_code=None,
        response_headers=(),
        raw_body=raw,
        raw_sha256=sha256_bytes(raw),
        raw_body_partial=True,
    )
    persist = getattr(
        prometheus_module,
        "_persist_promotion_exchange_before_parse",
        None,
    )
    assert callable(persist)

    with ObserverEvidenceStore(tmp_path, RUN_ID) as store:
        path, digest = persist(
            store,
            exchange=exchange,
            sequence=1,
            purpose="prometheus-total",
        )
        payload = json.loads((tmp_path / path).read_text(encoding="utf-8"))

    assert digest == sha256_bytes((tmp_path / path).read_bytes())
    assert payload["transport_reason"] == "HTTP_TRANSPORT_ERROR"
    assert payload["raw_response_partial"] is True
    assert base64.b64decode(payload["raw_response_base64"]) == raw
    assert payload["terminal_failure"] is True


def test_jaeger_trace_correlation_requires_same_trace_and_ad_getads_span() -> None:
    trace_id = "1" * 32
    body = {
        "data": [
            {
                "traceID": trace_id,
                "spans": [
                    {
                        "traceID": trace_id,
                        "spanID": "2" * 16,
                        "operationName": "oteldemo.AdService/GetAds",
                        "startTime": int(
                            (NOW + timedelta(seconds=5)).timestamp() * 1_000_000
                        ),
                        "duration": 500_000,
                        "processID": "p1",
                        "tags": [],
                    }
                ],
                "processes": {
                    "p1": {
                        "serviceName": "ad",
                        "tags": [],
                    }
                },
            }
        ]
    }
    verifies = getattr(prometheus_module, "_jaeger_trace_proves_getads", None)
    assert callable(verifies)

    assert verifies(
        body,
        trace_id=trace_id,
        operation="oteldemo.AdService/GetAds",
        window=_window(),
    )
    assert not verifies(
        body,
        trace_id="3" * 32,
        operation="oteldemo.AdService/GetAds",
        window=_window(),
    )
    body["data"][0]["spans"][0]["operationName"] = "unrelated"
    assert not verifies(
        body,
        trace_id=trace_id,
        operation="oteldemo.AdService/GetAds",
        window=_window(),
    )


def test_w3c_traceparent_is_exact_and_trace_bound() -> None:
    validates = getattr(prometheus_module, "_traceparent_matches_trace_id", None)
    assert callable(validates)
    trace_id = "1" * 32

    assert validates(f"00-{trace_id}-{'2' * 16}-01", trace_id=trace_id)
    assert not validates(f"00-{'3' * 32}-{'2' * 16}-01", trace_id=trace_id)
    assert not validates(f"00-{trace_id}-{'0' * 16}-01", trace_id=trace_id)
    assert not validates(f"01-{trace_id}-{'2' * 16}-01", trace_id=trace_id)


def test_live_discovery_plan_starts_from_repository_candidate_registry() -> None:
    source = Path("config/phase0/telemetry-queries-v3.0.0.json")
    candidate = json.loads(source.read_text(encoding="utf-8"))
    prepare = getattr(
        prometheus_module,
        "_prepare_candidate_registry_for_live_discovery",
        None,
    )
    assert callable(prepare)

    planned = prepare(candidate, run_id=RUN_ID)

    assert candidate["state"] == "UNRESOLVED"
    assert planned["promotion_proof"] is None
    assert planned["prometheus"]["total_query"].startswith(
        "traces_span_metrics_calls_total"
    )
    assert planned["jaeger"]["operation"] == "oteldemo.AdService/GetAds"
    assert planned["opensearch"]["timestamp_field"] == "@timestamp"
    assert planned["probe"]["path"] == "/api/data?contextKeys=telescopes"


def test_service_readiness_parses_exact_container_id_name_and_real_state() -> None:
    resource = OwnedResource(
        kind="container",
        name="ecomsre-phase0-load-generator",
        resource_id="a" * 64,
        labels={
            "com.docker.compose.project": "ecomsre-phase0",
            "com.ecomsre.project": "ecomsre-phase0",
            "com.ecomsre.run_id": RUN_ID,
            "com.docker.compose.service": "load-generator",
        },
        identity_evidence=(
            f"container:{'a' * 64}",
            "container_name:ecomsre-phase0-load-generator",
            "service:load-generator",
        ),
    )
    parse = getattr(probe_module, "_parse_service_container_inspect", None)
    assert callable(parse)
    payload = {
        "Id": resource.resource_id,
        "Name": f"/{resource.name}",
        "State": {
            "Status": "running",
            "Running": True,
            "Health": {"Status": "healthy"},
        },
    }

    assert parse(json.dumps(payload), resource=resource) == payload["State"]
    payload["Id"] = "b" * 64
    assert parse(json.dumps(payload), resource=resource) is None


def test_service_readiness_requires_service_specific_receipt_type() -> None:
    with pytest.raises(TypeError, match="LoadGeneratorTelemetryReceipt"):
        probe_module.derive_service_readiness_proof(
            object(),  # type: ignore[arg-type]
            object(),  # type: ignore[arg-type]
            object(),  # type: ignore[arg-type]
            service="load-generator",
            telemetry_receipt=SimpleNamespace(ready=True),
            registry_capability=object(),  # type: ignore[arg-type]
            window=_window(),
        )
    with pytest.raises(TypeError, match="CollectorPipelineReceipt"):
        probe_module.derive_service_readiness_proof(
            object(),  # type: ignore[arg-type]
            object(),  # type: ignore[arg-type]
            object(),  # type: ignore[arg-type]
            service="otel-collector",
            telemetry_receipt=SimpleNamespace(ready=True),
            registry_capability=object(),  # type: ignore[arg-type]
            window=_window(),
        )


def test_specialized_readiness_receipts_are_authority_issued() -> None:
    for name in ("LoadGeneratorTelemetryReceipt", "CollectorPipelineReceipt"):
        receipt_type = getattr(probe_module, name, None)
        assert receipt_type is not None
        with pytest.raises(TypeError, match="adapter"):
            receipt_type()


def test_load_generator_trace_requires_own_span_linked_to_ad_getads() -> None:
    trace_id = "4" * 32
    started = int((NOW + timedelta(seconds=5)).timestamp() * 1_000_000)
    payload = {
        "data": [
            {
                "traceID": trace_id,
                "processes": {
                    "load": {"serviceName": "load-generator", "tags": []},
                    "ad": {"serviceName": "ad", "tags": []},
                },
                "spans": [
                    {
                        "traceID": trace_id,
                        "spanID": "5" * 16,
                        "operationName": "user_get_ads",
                        "startTime": started,
                        "duration": 500_000,
                        "processID": "load",
                    },
                    {
                        "traceID": trace_id,
                        "spanID": "6" * 16,
                        "operationName": "oteldemo.AdService/GetAds",
                        "startTime": started + 100_000,
                        "duration": 100_000,
                        "processID": "ad",
                    },
                ],
            }
        ]
    }
    verifies = getattr(
        probe_module,
        "_jaeger_trace_proves_load_generator_and_getads",
        None,
    )
    assert callable(verifies)
    assert verifies(payload, window=_window()) == trace_id
    payload["data"][0]["spans"].pop()
    assert verifies(payload, window=_window()) is None
