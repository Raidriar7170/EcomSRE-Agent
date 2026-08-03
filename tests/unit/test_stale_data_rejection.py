from __future__ import annotations

import base64
import json
from collections import deque
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from ecomsre.evidence.hashes import canonical_json_bytes, sha256_bytes
from ecomsre.phase0.models import MeasurementPhase
from ecomsre.telemetry.http import (
    HttpExchange,
    HttpReason,
    HttpRequest,
    PhaseWindow,
)
from ecomsre.telemetry.prometheus import (
    PrometheusAcquisitionPolicy,
    _load_test_query_registry,
    PrometheusAdapter,
    PrometheusReason,
    load_query_registry,
)


ROOT = Path(__file__).resolve().parents[2]
REGISTRY = _load_test_query_registry(
    ROOT / "tests" / "fixtures" / "telemetry" / "frozen-query-registry.json"
)
UNRESOLVED = load_query_registry(
    ROOT / "config" / "phase0" / "telemetry-queries-v3.0.0.json"
)
RUN_ID = "3" * 32
PHASE_START = datetime(2026, 7, 30, 1, 2, 0, tzinfo=UTC)

OK = {
    "__name__": "traces_span_metrics_calls_total",
    "service_name": "ad",
    "span_name": "oteldemo.AdService/GetAds",
    "status_code": "STATUS_CODE_UNSET",
}
ERROR = {
    "__name__": "traces_span_metrics_calls_total",
    "service_name": "ad",
    "span_name": "oteldemo.AdService/GetAds",
    "status_code": "STATUS_CODE_ERROR",
}
INCARNATION = {
    "__name__": "process_start_time_seconds",
    "job": "ad",
}


class RecordingStore:
    _synthetic_telemetry_test_double = True

    def __init__(self, *, fail_at: int | None = None) -> None:
        self.records: list[tuple[str, dict[str, Any]]] = []
        self.fail_at = fail_at

    def write_immutable(
        self,
        relative_path: str,
        value: dict[str, Any],
    ) -> SimpleNamespace:
        self.records.append((relative_path, value))
        if self.fail_at == len(self.records):
            raise ValueError("fixture evidence failure")
        return SimpleNamespace(
            path=Path(relative_path),
            sha256=sha256_bytes(canonical_json_bytes(value)),
        )


class FixtureHttpClient:
    _synthetic_telemetry_test_double = True

    def __init__(self, responses: list[tuple[bytes, datetime, float]]) -> None:
        self.run_id = RUN_ID
        self.responses = deque(responses)
        self.requests: list[HttpRequest] = []

    def request(self, request: HttpRequest) -> HttpExchange:
        self.requests.append(request)
        body, ended_at, monotonic_ended = self.responses.popleft()
        return HttpExchange(
            reason=HttpReason.OK,
            request=request,
            started_at=ended_at - timedelta(milliseconds=10),
            ended_at=ended_at,
            monotonic_started_at=monotonic_ended - 0.01,
            monotonic_ended_at=monotonic_ended,
            status_code=200,
            response_headers=(("Content-Type", "application/json"),),
            raw_body=body,
            raw_sha256=sha256_bytes(body),
            raw_body_partial=False,
        )


def _window() -> PhaseWindow:
    return PhaseWindow(
        run_id=RUN_ID,
        cycle_number=1,
        scenario_phase=MeasurementPhase.BASELINE,
        utc_started_at=PHASE_START,
        utc_ended_at=PHASE_START + timedelta(seconds=240),
        monotonic_started_at=0.0,
        monotonic_ended_at=240.0,
    )


def _vector(
    timestamp: datetime,
    values: list[tuple[dict[str, str], str]],
) -> bytes:
    return canonical_json_bytes(
        {
            "status": "success",
            "data": {
                "resultType": "vector",
                "result": [
                    {
                        "metric": labels,
                        "value": [timestamp.timestamp(), value],
                    }
                    for labels, value in values
                ],
            },
        }
    )


def _source_timestamp_vector(
    evaluation_timestamp: datetime,
    source_timestamp: datetime,
    labels: list[dict[str, str]],
) -> bytes:
    return _vector(
        evaluation_timestamp,
        [
            (
                {
                    key: value
                    for key, value in identity.items()
                    if key != "__name__"
                },
                str(source_timestamp.timestamp()),
            )
            for identity in labels
        ],
    )


def _responses(
    *,
    start_values: tuple[int, int] = (100, 10),
    middle_values: tuple[int, int] = (210, 20),
    end_values: tuple[int, int] = (285, 25),
    start_timestamp: datetime | None = None,
    end_monotonic: float = 22.0,
    extra_middle_series: bool = False,
    incarnation_values: tuple[int, int, int] = (1_000, 1_000, 1_000),
    omit_error_from_total: bool = False,
) -> list[tuple[bytes, datetime, float]]:
    timestamps = (
        start_timestamp or PHASE_START + timedelta(seconds=3),
        (start_timestamp or PHASE_START + timedelta(seconds=3)) + timedelta(seconds=10),
        (start_timestamp or PHASE_START + timedelta(seconds=3)) + timedelta(seconds=20),
    )
    counter_values = (start_values, middle_values, end_values)
    monotonic_starts = (2.0, 12.0, end_monotonic)
    responses: list[tuple[bytes, datetime, float]] = []
    for index, (timestamp, (ok_value, error_value)) in enumerate(
        zip(timestamps, counter_values, strict=True)
    ):
        total_series = [(OK, str(ok_value)), (ERROR, str(error_value))]
        if omit_error_from_total:
            total_series = total_series[:1]
        if extra_middle_series and index == 1:
            total_series.append(
                (
                    {
                        **OK,
                        "status_code": "STATUS_CODE_UNKNOWN",
                    },
                    "1",
                )
            )
        observed_at = timestamp + timedelta(seconds=1)
        monotonic_started = monotonic_starts[index]
        responses.extend(
            (
                (_vector(timestamp, total_series), observed_at, monotonic_started),
                (
                    _vector(timestamp, [(ERROR, str(error_value))]),
                    observed_at,
                    monotonic_started + 0.01,
                ),
                (
                    _vector(
                        timestamp,
                        [(INCARNATION, str(incarnation_values[index]))],
                    ),
                    observed_at,
                    monotonic_started + 0.02,
                ),
                (
                    _source_timestamp_vector(
                        timestamp,
                        timestamp,
                        [identity for identity, _value in total_series],
                    ),
                    observed_at,
                    monotonic_started + 0.03,
                ),
            )
        )
    return responses


def _adapter(
    responses: list[tuple[bytes, datetime, float]],
    store: RecordingStore,
    *,
    registry=REGISTRY,
) -> tuple[PrometheusAdapter, FixtureHttpClient]:
    client = FixtureHttpClient(responses)
    return (
        PrometheusAdapter(
            client=client,
            evidence_store=store,
            fixture=registry,
            sleep=lambda _seconds: None,
        ),
        client,
    )


def test_prometheus_uses_raw_scrape_anchors_and_returns_exact_window_delta() -> None:
    store = RecordingStore()
    adapter, client = _adapter(_responses(), store)

    result = adapter.measure_getads(
        window=_window(),
        base_url="http://127.0.0.1:32771",
        artifact_prefix="cycles/01/baseline",
    )

    assert result.ready
    assert result.reason is PrometheusReason.READY
    assert result.getads_attempts == 200
    assert result.getads_errors == 15
    assert result.start_sample_timestamp == PHASE_START + timedelta(seconds=3)
    assert result.end_sample_timestamp == PHASE_START + timedelta(seconds=23)
    assert len(client.requests) == 12
    assert len(store.records) == 25
    assert all(
        "rate(" not in request.target
        and "increase(" not in request.target
        and "delta(" not in request.target
        for request in client.requests
    )
    source_query = f"timestamp({REGISTRY.registry.prometheus.total_query})"
    source_requests = [
        request
        for request in client.requests
        if "timestamp%28" in request.target
    ]
    assert len(source_requests) == 3
    source_raw_payloads = [
        payload
        for _path, payload in store.records
        if payload.get("query_kind") == "source_timestamp"
    ]
    assert len(source_raw_payloads) == 3
    assert all(
        payload["raw_query"] == source_query
        and payload["raw_query_sha256"] == sha256_bytes(source_query.encode())
        for payload in source_raw_payloads
    )
    first_payload = store.records[0][1]
    assert base64.b64decode(first_payload["raw_response_base64"]) == _responses()[0][0]
    assert first_payload["raw_response_sha256"] == sha256_bytes(_responses()[0][0])
    assert first_payload["boundary_rule"] == (
        "(start_sample_timestamp,end_sample_timestamp]"
    )
    final_payload = store.records[-1][1]
    assert final_payload["decision"]
    assert final_payload["getads_attempts"] == 200
    assert final_payload["getads_errors"] == 15


def test_prometheus_rejects_advanced_evaluation_with_unchanged_source_timestamp(
) -> None:
    responses = _responses()
    repeated_source = PHASE_START + timedelta(seconds=3)
    advanced_evaluation = repeated_source + timedelta(seconds=5)
    for index in range(4, 8):
        payload = json.loads(responses[index][0])
        for item in payload["data"]["result"]:
            item["value"][0] = advanced_evaluation.timestamp()
            if index == 7:
                item["value"][1] = str(repeated_source.timestamp())
        responses[index] = (
            canonical_json_bytes(payload),
            advanced_evaluation + timedelta(seconds=1),
            7.0 + (index - 4) * 0.01,
        )
    adapter, client = _adapter(responses, RecordingStore())

    result = adapter.measure_getads(
        window=_window(),
        base_url="http://127.0.0.1:32771",
        artifact_prefix="cycles/01/baseline",
    )

    assert result.reason is PrometheusReason.PROMETHEUS_STALE_SAMPLE
    assert len(client.requests) == 8


@pytest.mark.parametrize(
    ("mutate", "expected_reason"),
    [
        (
            lambda labels: labels.pop("service_name"),
            PrometheusReason.PROMETHEUS_CARDINALITY_DRIFT,
        ),
        (
            lambda labels: labels.__setitem__("span_name", "wrong/GetAds"),
            PrometheusReason.PROMETHEUS_CARDINALITY_DRIFT,
        ),
    ],
)
def test_prometheus_source_timestamp_requires_exact_non_name_labels(
    mutate,
    expected_reason: PrometheusReason,
) -> None:
    responses = _responses()
    payload = json.loads(responses[3][0])
    mutate(payload["data"]["result"][0]["metric"])
    responses[3] = (
        canonical_json_bytes(payload),
        responses[3][1],
        responses[3][2],
    )
    adapter, _client = _adapter(responses, RecordingStore())

    result = adapter.measure_getads(
        window=_window(),
        base_url="http://127.0.0.1:32771",
        artifact_prefix="cycles/01/baseline",
    )

    assert result.reason is expected_reason


def test_prometheus_waits_one_frozen_scrape_interval_between_anchor_polls() -> None:
    waits: list[float] = []
    client = FixtureHttpClient(_responses())
    adapter = PrometheusAdapter(
        client=client,
        evidence_store=RecordingStore(),
        fixture=REGISTRY,
        sleep=waits.append,
    )

    result = adapter.measure_getads(
        window=_window(),
        base_url="http://127.0.0.1:32771",
        artifact_prefix="cycles/01/baseline",
    )

    assert result.ready
    assert waits == [10.0, 10.0]


@pytest.mark.parametrize(
    ("minimum_attempts", "deadline_seconds"),
    [(199, 180), (200, 180.001)],
)
def test_prometheus_public_runtime_has_no_acquisition_limit_override(
    minimum_attempts: int,
    deadline_seconds: float,
) -> None:
    adapter, _client = _adapter(_responses(), RecordingStore())

    with pytest.raises(TypeError):
        adapter.measure_getads(
            window=_window(),
            base_url="http://127.0.0.1:32771",
            artifact_prefix="cycles/01/baseline",
            minimum_attempts=minimum_attempts,
            deadline_seconds=deadline_seconds,
        )


def test_prometheus_smoke_policy_uses_exact_100_attempt_120_second_budget() -> None:
    store = RecordingStore()
    client = FixtureHttpClient(_responses())
    policy = PrometheusAcquisitionPolicy.diagnostic_smoke()
    adapter = PrometheusAdapter(
        client=client,
        evidence_store=store,
        fixture=REGISTRY,
        acquisition_policy=policy,
        sleep=lambda _seconds: None,
    )

    result = adapter.measure_getads(
        window=_window(),
        base_url="http://127.0.0.1:32771",
        artifact_prefix="cycles/01/baseline",
    )

    assert policy.minimum_getads_attempts == 100
    assert policy.window_deadline_seconds == 120
    assert result.ready
    assert result.getads_attempts == 120
    assert len(client.requests) == 8


def test_prometheus_zero_series_rule_accepts_absent_error_vector_as_zero() -> None:
    responses = _responses(
        start_values=(100, 0),
        middle_values=(210, 0),
        end_values=(300, 0),
    )
    for index in (1, 5, 9):
        timestamp = json.loads(responses[index - 1][0])["data"]["result"][0]["value"][0]
        responses[index] = (
            _vector(datetime.fromtimestamp(timestamp, tz=UTC), []),
            responses[index][1],
            responses[index][2],
        )
    store = RecordingStore()
    adapter, _client = _adapter(responses, store)

    result = adapter.measure_getads(
        window=_window(),
        base_url="http://127.0.0.1:32771",
        artifact_prefix="cycles/01/baseline",
    )

    assert result.ready
    assert result.getads_attempts == 200
    assert result.getads_errors == 0
    assert any(
        payload.get("zero_series_inferred") is True for _path, payload in store.records
    )


def test_prometheus_zero_rule_fills_only_missing_error_identity_in_total() -> None:
    responses = _responses(
        start_values=(100, 0),
        middle_values=(210, 0),
        end_values=(300, 0),
        omit_error_from_total=True,
    )
    for index in (1, 5, 9):
        timestamp = json.loads(responses[index - 1][0])["data"]["result"][0]["value"][0]
        responses[index] = (
            _vector(datetime.fromtimestamp(timestamp, tz=UTC), []),
            responses[index][1],
            responses[index][2],
        )
    store = RecordingStore()
    adapter, _client = _adapter(responses, store)

    result = adapter.measure_getads(
        window=_window(),
        base_url="http://127.0.0.1:32771",
        artifact_prefix="cycles/01/baseline",
    )

    assert result.ready
    assert result.getads_attempts == 200
    assert result.getads_errors == 0
    total_decisions = [
        payload
        for _path, payload in store.records
        if payload.get("schema_version") == "phase0.telemetry-parse-decision.v1"
        and payload.get("parsed_series")
        and any(
            labels.get("status_code") == "STATUS_CODE_UNSET"
            for labels in payload["parsed_series"]
        )
    ]
    assert total_decisions
    assert all(payload["zero_series_inferred"] is True for payload in total_decisions)


def test_prometheus_zero_rule_rejects_missing_non_error_identity_in_total() -> None:
    responses = _responses()
    payload = json.loads(responses[0][0])
    payload["data"]["result"] = [
        item
        for item in payload["data"]["result"]
        if item["metric"]["status_code"] == "STATUS_CODE_ERROR"
    ]
    responses[0] = (
        canonical_json_bytes(payload),
        responses[0][1],
        responses[0][2],
    )
    adapter, _client = _adapter(responses, RecordingStore())

    result = adapter.measure_getads(
        window=_window(),
        base_url="http://127.0.0.1:32771",
        artifact_prefix="cycles/01/baseline",
    )

    assert result.reason is PrometheusReason.PROMETHEUS_CARDINALITY_DRIFT


def test_prometheus_accepts_frozen_scrape_jitter_within_tolerance() -> None:
    responses = _responses()
    for index, jitter in (
        *((index, 0.2) for index in range(4, 8)),
        *((index, 0.4) for index in range(8, 12)),
    ):
        payload = json.loads(responses[index][0])
        for item in payload["data"]["result"]:
            item["value"][0] += jitter
            if index % 4 == 3:
                item["value"][1] = str(float(item["value"][1]) + jitter)
        responses[index] = (
            canonical_json_bytes(payload),
            responses[index][1] + timedelta(seconds=jitter),
            responses[index][2],
        )
    adapter, _client = _adapter(responses, RecordingStore())

    result = adapter.measure_getads(
        window=_window(),
        base_url="http://127.0.0.1:32771",
        artifact_prefix="cycles/01/baseline",
    )

    assert result.ready


def test_prometheus_refuses_unresolved_fixture_before_http_or_evidence() -> None:
    store = RecordingStore()
    adapter, client = _adapter([], store, registry=UNRESOLVED)

    result = adapter.measure_getads(
        window=_window(),
        base_url="http://127.0.0.1:32771",
        artifact_prefix="cycles/01/baseline",
    )

    assert not result.ready
    assert result.reason is PrometheusReason.QUERY_FIXTURE_NOT_FROZEN
    assert client.requests == []
    assert store.records == []


def test_prometheus_persists_raw_response_before_schema_failure() -> None:
    malformed = b'{"status":"success","data":{"resultType":"matrix"}}'
    responses = _responses()[:4]
    responses[0] = (
        malformed,
        responses[0][1],
        responses[0][2],
    )
    store = RecordingStore()
    adapter, _client = _adapter(
        responses,
        store,
    )

    result = adapter.measure_getads(
        window=_window(),
        base_url="http://127.0.0.1:32771",
        artifact_prefix="cycles/01/baseline",
    )

    assert not result.ready
    assert result.reason is PrometheusReason.PROMETHEUS_SCHEMA_INVALID
    assert len(store.records) == 3
    assert base64.b64decode(store.records[0][1]["raw_response_base64"]) == malformed


def test_prometheus_evidence_failure_can_never_return_ready() -> None:
    store = RecordingStore(fail_at=1)
    adapter, client = _adapter(_responses(), store)

    result = adapter.measure_getads(
        window=_window(),
        base_url="http://127.0.0.1:32771",
        artifact_prefix="cycles/01/baseline",
    )

    assert not result.ready
    assert result.reason is PrometheusReason.EVIDENCE_PERSISTENCE_FAILED
    assert len(client.requests) == 1


def test_prometheus_final_decision_persistence_failure_can_never_return_ready() -> None:
    store = RecordingStore(fail_at=25)
    adapter, client = _adapter(_responses(), store)

    result = adapter.measure_getads(
        window=_window(),
        base_url="http://127.0.0.1:32771",
        artifact_prefix="cycles/01/baseline",
    )

    assert not result.ready
    assert result.reason is PrometheusReason.EVIDENCE_PERSISTENCE_FAILED
    assert len(client.requests) == 12


@pytest.mark.parametrize(
    ("responses", "expected_reason"),
    [
        (
            _responses(start_timestamp=PHASE_START - timedelta(seconds=3)),
            PrometheusReason.PROMETHEUS_STALE_SAMPLE,
        ),
        (
            _responses(middle_values=(90, 9)),
            PrometheusReason.PROMETHEUS_COUNTER_RESET_OR_TARGET_RESTART,
        ),
        (
            _responses(extra_middle_series=True),
            PrometheusReason.PROMETHEUS_CARDINALITY_DRIFT,
        ),
        (
            _responses(end_monotonic=183.0),
            PrometheusReason.WINDOW_SAMPLE_TIMEOUT,
        ),
    ],
)
def test_prometheus_rejects_stale_reset_drift_and_late_completion(
    responses: list[tuple[bytes, datetime, float]],
    expected_reason: PrometheusReason,
) -> None:
    store = RecordingStore()
    adapter, _client = _adapter(responses, store)

    result = adapter.measure_getads(
        window=_window(),
        base_url="http://127.0.0.1:32771",
        artifact_prefix="cycles/01/baseline",
    )

    assert not result.ready
    assert result.reason is expected_reason


def test_prometheus_rejects_total_error_scrape_timestamp_mismatch() -> None:
    responses = _responses()
    error_payload = json.loads(responses[1][0])
    error_payload["data"]["result"][0]["value"][0] += 1
    responses[1] = (
        canonical_json_bytes(error_payload),
        responses[1][1],
        responses[1][2],
    )
    store = RecordingStore()
    adapter, _client = _adapter(responses, store)

    result = adapter.measure_getads(
        window=_window(),
        base_url="http://127.0.0.1:32771",
        artifact_prefix="cycles/01/baseline",
    )

    assert not result.ready
    assert result.reason is PrometheusReason.PROMETHEUS_SCRAPE_TIMESTAMP_MISMATCH


def test_prometheus_rejects_stale_marker_as_freshness_failure() -> None:
    responses = _responses()
    payload = json.loads(responses[4][0])
    payload["data"]["result"][0]["value"][1] = "NaN"
    responses[4] = (
        canonical_json_bytes(payload),
        responses[4][1],
        responses[4][2],
    )
    adapter, _client = _adapter(responses, RecordingStore())

    result = adapter.measure_getads(
        window=_window(),
        base_url="http://127.0.0.1:32771",
        artifact_prefix="cycles/01/baseline",
    )

    assert result.reason is PrometheusReason.PROMETHEUS_STALE_SAMPLE


def test_prometheus_rejects_non_integral_window_delta() -> None:
    responses = _responses()
    for index, value in ((0, "100.5"), (8, "285.75")):
        payload = json.loads(responses[index][0])
        payload["data"]["result"][0]["value"][1] = value
        responses[index] = (
            canonical_json_bytes(payload),
            responses[index][1],
            responses[index][2],
        )
    adapter, _client = _adapter(responses, RecordingStore())

    result = adapter.measure_getads(
        window=_window(),
        base_url="http://127.0.0.1:32771",
        artifact_prefix="cycles/01/baseline",
    )

    assert result.reason is PrometheusReason.PROMETHEUS_NON_INTEGRAL_DELTA


def test_prometheus_rejects_target_restart_even_if_counters_exceed_old_values() -> None:
    responses = _responses(
        middle_values=(250, 30),
        end_values=(400, 40),
        incarnation_values=(1_000, 2_000, 2_000),
    )
    adapter, _client = _adapter(responses, RecordingStore())

    result = adapter.measure_getads(
        window=_window(),
        base_url="http://127.0.0.1:32771",
        artifact_prefix="cycles/01/baseline",
    )

    assert result.reason is (
        PrometheusReason.PROMETHEUS_COUNTER_RESET_OR_TARGET_RESTART
    )
