from __future__ import annotations

import base64
import inspect
import json
import time
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
from telemetry_promotion_support import issue_strict_frozen_test_capability


RUN_ID = "b" * 32
NOW = datetime(2026, 7, 30, 1, 2, 0, tzinfo=UTC)
ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "telemetry"


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


def test_guarded_atomic_registry_write_replaces_only_exact_unresolved_candidate(
    tmp_path: Path,
) -> None:
    target = tmp_path / "telemetry-registry.json"
    candidate = (
        ROOT / "config" / "phase0" / "telemetry-queries-v3.0.0.json"
    ).read_bytes()
    target.write_bytes(candidate)
    frozen = json.loads(
        (FIXTURES / "frozen-query-registry.json").read_text(encoding="utf-8")
    )

    prometheus_module._guarded_atomic_registry_write(
        target,
        expected_source_sha256=sha256_bytes(candidate),
        frozen_payload=frozen,
    )

    persisted = prometheus_module.load_query_registry(target)
    assert persisted.registry.state.value == "FROZEN"
    assert not list(tmp_path.glob(".*.tmp"))


def test_guarded_atomic_registry_write_rejects_source_drift_without_overwrite(
    tmp_path: Path,
) -> None:
    target = tmp_path / "telemetry-registry.json"
    target.write_text('{"state":"drift"}', encoding="utf-8")
    before = target.read_bytes()
    frozen = json.loads(
        (FIXTURES / "frozen-query-registry.json").read_text(encoding="utf-8")
    )

    with pytest.raises(ValueError, match="source"):
        prometheus_module._guarded_atomic_registry_write(
            target,
            expected_source_sha256="a" * 64,
            frozen_payload=frozen,
        )

    assert target.read_bytes() == before


def test_frozen_registry_origin_proof_can_be_verified_for_a_new_run(
    tmp_path: Path,
) -> None:
    origin_run = "1" * 32
    with ObserverEvidenceStore(tmp_path, origin_run) as origin_store:
        payload, _capability = issue_strict_frozen_test_capability(
            origin_store,
            run_id=origin_run,
            fixture_path=FIXTURES / "frozen-query-registry.json",
        )
    registry = tmp_path / "registry.json"
    registry.write_bytes(prometheus_module.canonical_json_bytes(payload))

    audit = prometheus_module.validate_frozen_query_registry_origin(
        registry,
        artifacts_root=tmp_path,
    )

    assert audit.valid
    assert audit.run_id == origin_run
    assert audit.verified_hashes


def test_frozen_registry_origin_proof_fails_closed_when_artifact_hash_drifts(
    tmp_path: Path,
) -> None:
    origin_run = "2" * 32
    with ObserverEvidenceStore(tmp_path, origin_run) as origin_store:
        payload, _capability = issue_strict_frozen_test_capability(
            origin_store,
            run_id=origin_run,
            fixture_path=FIXTURES / "frozen-query-registry.json",
        )
    registry = tmp_path / "registry.json"
    registry.write_bytes(prometheus_module.canonical_json_bytes(payload))
    proof_path = next(
        (tmp_path / "observer-visible" / origin_run / "telemetry" / "promotion").glob(
            "review.json"
        )
    )
    proof_path.chmod(0o600)
    proof_path.write_text("{}", encoding="utf-8")

    audit = prometheus_module.validate_frozen_query_registry_origin(
        registry,
        artifacts_root=tmp_path,
    )

    assert not audit.valid


def test_staged_promotion_window_is_acquired_only_for_requested_live_phase() -> None:
    calls = []

    def provider(phase: MeasurementPhase) -> PhaseWindow:
        calls.append(phase)
        now = datetime.now(UTC)
        monotonic = time.monotonic()
        return PhaseWindow(
            run_id=RUN_ID,
            cycle_number=1,
            scenario_phase=phase,
            utc_started_at=now,
            utc_ended_at=now + timedelta(seconds=120),
            monotonic_started_at=monotonic,
            monotonic_ended_at=monotonic + 120,
        )

    window = prometheus_module._acquire_staged_promotion_window(
        provider,
        expected_phase=MeasurementPhase.FAULT,
        run_id=RUN_ID,
        cycle_number=1,
    )

    assert calls == [MeasurementPhase.FAULT]
    assert window.scenario_phase is MeasurementPhase.FAULT
    assert window.monotonic_ended_at > time.monotonic()


def test_frozen_registry_revalidation_rejects_injected_transport(
    tmp_path: Path,
) -> None:
    frozen = FIXTURES / "frozen-query-registry.json"
    with ObserverEvidenceStore(tmp_path, RUN_ID) as store:
        with pytest.raises(TypeError, match="production"):
            prometheus_module.revalidate_frozen_query_capability(
                frozen,
                evidence_store=store,
                client=SimpleNamespace(run_id=RUN_ID),
                window=_window(),
                probe_base_url="http://127.0.0.1:8080",
            )


def test_jaeger_correlation_retry_is_bounded_and_persists_each_response(
    tmp_path: Path,
) -> None:
    trace_id = "3" * 32
    request = HttpRequest(
        endpoint=OwnedEndpoint(
            base_url="http://127.0.0.1:16686",
            service="jaeger",
            target_port=16686,
        ),
        method="GET",
        target=f"/jaeger/ui/api/traces/{trace_id}",
        absolute_deadline_monotonic=30,
    )
    empty = b'{"data":[]}'
    correlated = json.dumps(
        {
            "data": [
                {
                    "traceID": trace_id,
                    "spans": [
                        {
                            "traceID": trace_id,
                            "spanID": "4" * 16,
                            "operationName": "oteldemo.AdService/GetAds",
                            "startTime": int(
                                (NOW + timedelta(seconds=5)).timestamp()
                                * 1_000_000
                            ),
                            "duration": 100_000,
                            "processID": "p1",
                            "tags": [],
                        }
                    ],
                    "processes": {
                        "p1": {"serviceName": "ad", "tags": []},
                    },
                }
            ]
        }
    ).encode()

    def exchange(body: bytes, offset: float) -> HttpExchange:
        return HttpExchange(
            reason=HttpReason.OK,
            request=request,
            started_at=NOW + timedelta(seconds=offset),
            ended_at=NOW + timedelta(seconds=offset + 0.1),
            monotonic_started_at=offset,
            monotonic_ended_at=offset + 0.1,
            status_code=200,
            response_headers=(),
            raw_body=body,
            raw_sha256=sha256_bytes(body),
            raw_body_partial=False,
        )

    class Client:
        def __init__(self) -> None:
            self.responses = [exchange(empty, 1), exchange(correlated, 2)]
            self.calls = 0

        def request(self, _request):
            response = self.responses[self.calls]
            self.calls += 1
            return response

    client = Client()
    sleeps = []
    with ObserverEvidenceStore(tmp_path, RUN_ID) as store:
        policy = prometheus_module.PromotionAcquisitionPolicy()
        result, payload, attempts = (
            prometheus_module._bounded_jaeger_correlation_retry(
            client=client,
            evidence_store=store,
            request=request,
            window=_window(),
            trace_id=trace_id,
            operation="oteldemo.AdService/GetAds",
            phase_name="baseline",
            sleep=sleeps.append,
            policy=policy,
            )
        )

    assert result.raw_body == correlated
    assert payload["data"]
    assert client.calls == 2
    assert sleeps == [policy.poll_interval_seconds]
    assert len(attempts) == 2
    retries = sorted(
        (
            tmp_path
            / "observer-visible"
            / RUN_ID
            / "telemetry"
            / "promotion"
            / "acquisition-attempts"
        ).glob("*.json")
    )
    assert len(retries) == 2


def test_backend_promotion_poll_uses_explicit_policy_and_preserves_every_attempt(
    tmp_path: Path,
) -> None:
    request = HttpRequest(
        endpoint=OwnedEndpoint(
            base_url="http://127.0.0.1:9090",
            service="prometheus",
            target_port=9090,
        ),
        method="GET",
        target="/api/v1/query?query=test",
        absolute_deadline_monotonic=30,
    )
    bodies = [b'{"data":[]}', b'{"data":[1]}']

    class Client:
        def __init__(self) -> None:
            self.calls = 0

        def request(self, _request):
            body = bodies[self.calls]
            self.calls += 1
            return HttpExchange(
                reason=HttpReason.OK,
                request=request,
                started_at=NOW + timedelta(seconds=self.calls),
                ended_at=NOW + timedelta(seconds=self.calls, milliseconds=10),
                monotonic_started_at=float(self.calls),
                monotonic_ended_at=float(self.calls) + 0.01,
                status_code=200,
                response_headers=(),
                raw_body=body,
                raw_sha256=sha256_bytes(body),
                raw_body_partial=False,
            )

    client = Client()
    sleeps = []
    policy = prometheus_module.PromotionAcquisitionPolicy()
    with ObserverEvidenceStore(tmp_path, RUN_ID) as store:
        result, attempts = prometheus_module._bounded_promotion_backend_poll(
            client=client,
            evidence_store=store,
            request=request,
            window=_window(),
            purpose="prometheus-total",
            fixture_version="fixture-v1",
            validator=lambda exchange: exchange.raw_body == bodies[-1],
            policy=policy,
            sleep=sleeps.append,
        )

    assert result.raw_body == bodies[-1]
    assert len(attempts) == 2
    assert sleeps == [policy.poll_interval_seconds]
    assert all((tmp_path / path).is_file() for path, _digest in attempts)


def test_live_promotion_contract_is_derived_from_three_phase_prometheus_samples(
) -> None:
    ok_identity = tuple(
        sorted(
            {
                "__name__": "traces_span_metrics_calls_total",
                "service_name": "ad",
                "span_name": "oteldemo.AdService/GetAds",
                "status_code": "STATUS_CODE_UNSET",
            }.items()
        )
    )
    error_identity = tuple(
        sorted({**dict(ok_identity), "status_code": "STATUS_CODE_ERROR"}.items())
    )
    incarnation_identity = tuple(
        sorted({"__name__": "process_start_time_seconds", "job": "ad"}.items())
    )
    source_identity = tuple(
        (key, value) for key, value in ok_identity if key != "__name__"
    )

    def observed(
        identity: tuple[tuple[str, str], ...] | None,
        *,
        timestamp: datetime | None,
        value: str,
    ):
        values = () if identity is None else ((identity, prometheus_module.Decimal(value)),)
        labels = () if identity is None else (identity,)
        return prometheus_module._PrometheusDiscoveryObservation(
            labels=labels,
            values=values,
            sample_timestamp=timestamp,
            response_ended_at=(timestamp + timedelta(seconds=1) if timestamp else NOW),
        )

    evidence = {}
    for index, phase in enumerate(MeasurementPhase):
        first = NOW + timedelta(seconds=index * 40 + 1)
        second = first + timedelta(seconds=10)
        total_identities = (
            (ok_identity,)
            if phase is MeasurementPhase.BASELINE
            else (ok_identity, error_identity)
        )
        total = [
            prometheus_module._PrometheusDiscoveryObservation(
                labels=total_identities,
                values=tuple(
                    (identity, prometheus_module.Decimal("10"))
                    for identity in total_identities
                ),
                sample_timestamp=timestamp,
                response_ended_at=timestamp + timedelta(seconds=1),
            )
            for timestamp in (first, second)
        ]
        errors = (
            [observed(None, timestamp=None, value="0")]
            if phase is MeasurementPhase.BASELINE
            else [
                observed(error_identity, timestamp=timestamp, value="10")
                for timestamp in (first, second)
            ]
        )
        source_timestamps = [
            prometheus_module._PrometheusDiscoveryObservation(
                labels=(source_identity,),
                values=(
                    (
                        source_identity,
                        prometheus_module.Decimal(
                            str(source_timestamp.timestamp())
                        ),
                    ),
                ),
                sample_timestamp=evaluation_timestamp,
                response_ended_at=evaluation_timestamp + timedelta(seconds=1),
            )
            for evaluation_timestamp, source_timestamp in (
                (first, first),
                (first + timedelta(seconds=5), first),
                (second, second),
            )
        ]
        evidence[phase] = {
            "total": total,
            "error": errors,
            "target_incarnation": [
                observed(incarnation_identity, timestamp=timestamp, value="1000")
                for timestamp in (first, second)
            ],
            "source_timestamp": source_timestamps,
        }

    derived = prometheus_module._derive_prometheus_live_contract(evidence)
    paths = prometheus_module._expected_promotion_exchange_paths(
        RUN_ID,
        three_phase_prometheus=True,
    )

    assert derived["error_classification"] == {
        "label": "status_code",
        "values": ["STATUS_CODE_ERROR"],
    }
    assert derived["zero_series_rule"] == "absent_error_series_means_zero"
    assert derived["scrape_interval_seconds"] == 10
    assert derived["maximum_scrape_lag_seconds"] == 6.5
    assert "prometheus-total-fault" in paths
    assert "prometheus-source-timestamp-fault" in paths
    assert "prometheus-error-recovery" in paths
    assert "prometheus-source-timestamp-recovery" in paths


def test_source_timing_verifier_rejects_aggregate_poll_interval_tampering(
) -> None:
    registry = prometheus_module._load_test_query_registry(
        FIXTURES / "frozen-query-registry.json"
    ).registry
    source_query = (
        'timestamp(traces_span_metrics_calls_total{service_name="ad",'
        'span_name="oteldemo.AdService/GetAds"})'
    )
    phase_windows: dict[MeasurementPhase, PhaseWindow] = {}
    attempts: dict[str, dict[str, object]] = {}
    for phase_index, phase in enumerate(MeasurementPhase):
        started_at = NOW + timedelta(seconds=phase_index * 60)
        phase_windows[phase] = PhaseWindow(
            run_id=RUN_ID,
            cycle_number=1,
            scenario_phase=phase,
            utc_started_at=started_at,
            utc_ended_at=started_at + timedelta(seconds=30),
            monotonic_started_at=float(phase_index * 60),
            monotonic_ended_at=float(phase_index * 60 + 30),
        )
        purpose = (
            "prometheus-source-timestamp"
            if phase is MeasurementPhase.BASELINE
            else f"prometheus-source-timestamp-{phase.value}"
        )
        source_at = started_at + timedelta(seconds=1)
        for attempt, (evaluation_at, observed_source_at) in enumerate(
            (
                (source_at, source_at),
                (source_at + timedelta(seconds=5), source_at),
                (source_at + timedelta(seconds=10), source_at + timedelta(seconds=10)),
            ),
            start=1,
        ):
            raw = json.dumps(
                {
                    "status": "success",
                    "data": {
                        "resultType": "vector",
                        "result": [
                            {
                                    "metric": {
                                        "service_name": "ad",
                                        "span_name": "oteldemo.AdService/GetAds",
                                        "status_code": "STATUS_CODE_UNSET",
                                    },
                                "value": [
                                    evaluation_at.timestamp(),
                                    str(observed_source_at.timestamp()),
                                ],
                            }
                        ],
                    },
                },
                separators=(",", ":"),
            ).encode()
            attempts[f"{purpose}-{attempt:02d}.json"] = {
                "purpose": purpose,
                "phase": phase.value,
                "request_target": (
                    "/api/v1/query?query="
                    + prometheus_module.quote(source_query, safe="")
                ),
                "response_ended_at": (
                    evaluation_at + timedelta(seconds=1)
                ).isoformat(),
                "http_status": 200,
                "transport_reason": HttpReason.OK.value,
                "raw_response_partial": False,
                "raw_response_base64": base64.b64encode(raw).decode("ascii"),
                "raw_response_sha256": sha256_bytes(raw),
            }

    raw_timing = (
        prometheus_module._recompute_prometheus_source_timing_from_attempts(
            attempts,
            phase_windows=phase_windows,
            source_timestamp_query=source_query,
            registry=registry,
        )
    )
    expected_contract = {
        "scrape_interval_seconds": raw_timing["scrape_interval_seconds"],
        "scrape_interval_tolerance_seconds": raw_timing[
            "scrape_interval_tolerance_seconds"
        ],
        "maximum_scrape_lag_seconds": raw_timing[
            "maximum_scrape_lag_seconds"
        ],
    }
    derivation = {
        "source_timestamp_query": source_query,
        "scrape_timestamps_by_phase": raw_timing[
            "scrape_timestamps_by_phase"
        ],
        "observed_intervals_seconds": raw_timing[
            "observed_intervals_seconds"
        ],
        "observed_lags_seconds": raw_timing["observed_lags_seconds"],
    }
    prometheus_module._verify_prometheus_source_derivation(
        derivation,
        expected_contract=expected_contract,
        attempt_payloads=attempts,
        phase_windows=phase_windows,
        source_timestamp_query=source_query,
        registry=registry,
    )

    tampered = {
        **derivation,
        "observed_intervals_seconds": [5.0, 5.0, 5.0],
    }
    with pytest.raises(
        ValueError,
        match="raw-derived source timing differs",
    ):
        prometheus_module._verify_prometheus_source_derivation(
            tampered,
            expected_contract=expected_contract,
            attempt_payloads=attempts,
            phase_windows=phase_windows,
            source_timestamp_query=source_query,
            registry=registry,
        )


def test_promotion_source_timestamp_identity_ignores_only_metric_name() -> None:
    registry = prometheus_module._load_test_query_registry(
        FIXTURES / "frozen-query-registry.json"
    ).registry
    source_at = NOW + timedelta(seconds=3)
    expected_labels = [
        {
            key: value
            for key, value in series.labels.items()
            if key != "__name__"
        }
        for series in registry.prometheus.expected_total_series or ()
    ]
    payload = {
        "status": "success",
        "data": {
            "resultType": "vector",
            "result": [
                {
                    "metric": labels,
                    "value": [
                        source_at.timestamp(),
                        str(source_at.timestamp()),
                    ],
                }
                for labels in expected_labels
            ],
        },
    }

    observed = prometheus_module._verify_prometheus_promotion_vector(
        payload,
        query_kind="source_timestamp",
        registry=registry,
        utc_window=(NOW, NOW + timedelta(seconds=30)),
    )

    assert observed == source_at
    payload["data"]["result"][0]["metric"]["service_name"] = "wrong"
    with pytest.raises(
        ValueError,
        match="emitted identity differs",
    ):
        prometheus_module._verify_prometheus_promotion_vector(
            payload,
            query_kind="source_timestamp",
            registry=registry,
            utc_window=(NOW, NOW + timedelta(seconds=30)),
        )


def test_fault_probe_preserves_expected_business_failure_without_selective_retry(
    tmp_path: Path,
) -> None:
    request = HttpRequest(
        endpoint=OwnedEndpoint(
            base_url="http://127.0.0.1:8080",
            service="frontend-proxy",
            target_port=8080,
        ),
        method="GET",
        target="/api/data?contextKeys=telescopes",
        absolute_deadline_monotonic=30,
    )
    raw = b'{"error":"ad service unavailable"}'
    exchange = HttpExchange(
        reason=HttpReason.HTTP_STATUS_ERROR,
        request=request,
        started_at=NOW + timedelta(seconds=1),
        ended_at=NOW + timedelta(seconds=1, milliseconds=10),
        monotonic_started_at=1,
        monotonic_ended_at=1.01,
        status_code=503,
        response_headers=(),
        raw_body=raw,
        raw_sha256=sha256_bytes(raw),
        raw_body_partial=False,
    )
    with ObserverEvidenceStore(tmp_path, RUN_ID) as store:
        path, _digest = (
            prometheus_module._persist_promotion_exchange_before_parse(
                store,
                exchange=exchange,
                sequence=8,
                purpose="probe-fault",
                fixture_version="otel-demo-3.0.0-live-deadbeef-v1",
            )
        )
        payload = json.loads((tmp_path / path).read_text(encoding="utf-8"))

    prometheus_module._verify_promotion_exchange_artifact(
        payload,
        sequence=8,
        purpose="probe-fault",
    )
    with pytest.raises(ValueError, match="raw exchange"):
        prometheus_module._verify_promotion_exchange_artifact(
            {**payload, "purpose": "probe-baseline", "sequence": 6},
            sequence=6,
            purpose="probe-baseline",
        )


def test_frozen_revalidation_fault_probe_is_phase_safe_without_hiding_transport_failure(
) -> None:
    request = HttpRequest(
        endpoint=OwnedEndpoint(
            base_url="http://127.0.0.1:8080",
            service="frontend-proxy",
            target_port=8080,
        ),
        method="GET",
        target="/api/data?contextKeys=telescopes",
        absolute_deadline_monotonic=30,
    )

    def exchange(reason: HttpReason, status: int | None) -> HttpExchange:
        raw = b'{"error":"expected fault"}'
        return HttpExchange(
            reason=reason,
            request=request,
            started_at=NOW + timedelta(seconds=1),
            ended_at=NOW + timedelta(seconds=1, milliseconds=10),
            monotonic_started_at=1,
            monotonic_ended_at=1.01,
            status_code=status,
            response_headers=(),
            raw_body=raw,
            raw_sha256=sha256_bytes(raw),
            raw_body_partial=False,
        )

    expected_fault = exchange(HttpReason.HTTP_STATUS_ERROR, 503)
    transport_failure = exchange(HttpReason.HTTP_TRANSPORT_ERROR, None)

    assert prometheus_module._evaluate_revalidation_probe(
        expected_fault,
        phase=MeasurementPhase.FAULT,
    ) == ("FAULT_NON_SUCCESS", True)
    assert prometheus_module._evaluate_revalidation_probe(
        expected_fault,
        phase=MeasurementPhase.BASELINE,
    ) == ("NON_SUCCESS", False)
    assert prometheus_module._evaluate_revalidation_probe(
        transport_failure,
        phase=MeasurementPhase.FAULT,
    ) == ("TRANSPORT_FAILURE", False)
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


def test_collector_receipt_binds_lifecycle_config_and_all_backend_receipts() -> None:
    source = inspect.getsource(probe_module.acquire_collector_pipeline_receipt)

    assert "_acquire_service_trace_receipt" not in source
    assert "jaeger_base_url" not in source
    assert "otel-collector_status" in source
    assert "otelcol-config.yml" in source
    assert "otelcol-config-extras.yml" in source
    assert '"prometheus"' in source
    assert '"jaeger"' in source
    assert '"opensearch"' in source
    assert "is_production_receipt" in source
    assert "client.run_id != context.run_id" in source


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
