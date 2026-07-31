from __future__ import annotations

import base64
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from ecomsre.evidence.hashes import canonical_json_bytes, sha256_bytes
from ecomsre.phase0.models import MeasurementPhase
from ecomsre.telemetry.http import HttpExchange, HttpReason, HttpRequest, PhaseWindow
from ecomsre.telemetry.jaeger import JaegerAdapter, JaegerReason
from ecomsre.telemetry.opensearch import OpenSearchAdapter, OpenSearchReason
from ecomsre.telemetry.opensearch_identity import (
    OpenSearchServiceIdentityReason,
    parse_opensearch_service_identity,
)
from ecomsre.telemetry.prometheus import (
    _load_test_query_registry,
    load_query_registry,
)


ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "telemetry"
FROZEN = _load_test_query_registry(FIXTURES / "frozen-query-registry.json")
UNRESOLVED = load_query_registry(
    ROOT / "config" / "phase0" / "telemetry-queries-v3.0.0.json"
)
RUN_ID = "4" * 32
START = datetime(2026, 7, 30, 1, 2, 0, tzinfo=UTC)
END = START + timedelta(seconds=30)


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
        return SimpleNamespace(path=Path(relative_path), sha256="a" * 64)


class OneResponseClient:
    _synthetic_telemetry_test_double = True

    def __init__(
        self,
        body: bytes,
        *,
        observed_at: datetime | None = None,
        monotonic_ended: float = 20.0,
    ) -> None:
        self.run_id = RUN_ID
        self.body = body
        self.observed_at = observed_at or START + timedelta(seconds=20)
        self.monotonic_ended = monotonic_ended
        self.requests: list[HttpRequest] = []

    def request(self, request: HttpRequest) -> HttpExchange:
        self.requests.append(request)
        return HttpExchange(
            reason=HttpReason.OK,
            request=request,
            started_at=self.observed_at - timedelta(seconds=1),
            ended_at=self.observed_at,
            monotonic_started_at=self.monotonic_ended - 1.0,
            monotonic_ended_at=self.monotonic_ended,
            status_code=200,
            response_headers=(("Content-Type", "application/json"),),
            raw_body=self.body,
            raw_sha256=sha256_bytes(self.body),
            raw_body_partial=False,
        )


def _window() -> PhaseWindow:
    return PhaseWindow(
        run_id=RUN_ID,
        cycle_number=1,
        scenario_phase=MeasurementPhase.BASELINE,
        utc_started_at=START,
        utc_ended_at=END,
        monotonic_started_at=0.0,
        monotonic_ended_at=30.0,
    )


def test_jaeger_requires_exact_current_phase_ad_operation_and_persists_raw_first() -> (
    None
):
    body = (FIXTURES / "jaeger-current.json").read_bytes()
    client = OneResponseClient(body)
    store = RecordingStore()
    adapter = JaegerAdapter(client=client, evidence_store=store, fixture=FROZEN)

    result = adapter.check_readiness(
        window=_window(),
        base_url="http://127.0.0.1:32772",
        artifact_prefix="cycles/01/baseline",
    )

    assert result.ready
    assert result.reason is JaegerReason.READY
    assert result.trace_id == "trace-current-001"
    assert result.span_id == "span-current-001"
    assert len(store.records) == 2
    assert store.records[0][0].endswith("raw.json")
    assert base64.b64decode(store.records[0][1]["raw_response_base64"]) == body
    assert store.records[1][1]["decision"]
    assert "service=ad" in client.requests[0].target
    assert "operation=oteldemo.AdService%2FGetAds" in client.requests[0].target


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        (
            lambda payload: payload["data"][0]["processes"]["p1"].update(
                {"serviceName": "frontend"}
            ),
            JaegerReason.JAEGER_IDENTITY_MISMATCH,
        ),
        (
            lambda payload: payload["data"][0]["spans"][0].update(
                {"startTime": 1785373200000000}
            ),
            JaegerReason.JAEGER_STALE_TRACE,
        ),
    ],
)
def test_jaeger_rejects_wrong_identity_and_previous_window_trace(
    mutation,
    reason: JaegerReason,
) -> None:
    payload = json.loads((FIXTURES / "jaeger-current.json").read_bytes())
    mutation(payload)
    client = OneResponseClient(canonical_json_bytes(payload))
    store = RecordingStore()

    result = JaegerAdapter(
        client=client,
        evidence_store=store,
        fixture=FROZEN,
    ).check_readiness(
        window=_window(),
        base_url="http://127.0.0.1:32772",
        artifact_prefix="cycles/01/baseline",
    )

    assert not result.ready
    assert result.reason is reason
    assert len(store.records) == 2
    assert not store.records[-1][1]["decision"]


def test_jaeger_refuses_candidate_fixture_before_query() -> None:
    client = OneResponseClient(b"")
    store = RecordingStore()

    result = JaegerAdapter(
        client=client,
        evidence_store=store,
        fixture=UNRESOLVED,
    ).check_readiness(
        window=_window(),
        base_url="http://127.0.0.1:32772",
        artifact_prefix="cycles/01/baseline",
    )

    assert result.reason is JaegerReason.QUERY_FIXTURE_NOT_FROZEN
    assert not result.ready
    assert client.requests == []
    assert store.records == []


def test_opensearch_requires_exact_current_phase_ad_log_and_bounded_query() -> None:
    body = (FIXTURES / "opensearch-current.json").read_bytes()
    client = OneResponseClient(body)
    store = RecordingStore()

    result = OpenSearchAdapter(
        client=client,
        evidence_store=store,
        fixture=FROZEN,
    ).check_readiness(
        window=_window(),
        base_url="http://127.0.0.1:32773",
        artifact_prefix="cycles/01/baseline",
    )

    assert result.ready
    assert result.reason is OpenSearchReason.READY
    assert result.log_id == "log-current-001"
    assert result.trace_id == "trace-current-001"
    request = client.requests[0]
    assert request.method == "POST"
    assert request.target == "/otel-logs-%2A/_search"
    query = json.loads(request.body)
    assert query["size"] == 100
    filters = query["query"]["bool"]["filter"]
    assert {"term": {"resource.service.name": "ad"}} in filters
    assert len(store.records) == 2
    assert store.records[0][0].endswith("raw.json")


@pytest.mark.parametrize(
    ("resource", "reason"),
    [
        (
            {"service.name": "ad"},
            OpenSearchReason.READY,
        ),
        (
            {"service": {"name": "ad"}},
            OpenSearchReason.READY,
        ),
        (
            {"service.name": "ad", "service": {"name": "ad"}},
            OpenSearchReason.READY,
        ),
        (
            {"service.name": "ad", "service": {"name": "frontend"}},
            OpenSearchReason.OPENSEARCH_SCHEMA_INVALID,
        ),
        (
            {"service.name": 7},
            OpenSearchReason.OPENSEARCH_SCHEMA_INVALID,
        ),
        (
            {"service": "ad"},
            OpenSearchReason.OPENSEARCH_SCHEMA_INVALID,
        ),
        (
            {},
            OpenSearchReason.OPENSEARCH_SCHEMA_INVALID,
        ),
    ],
)
def test_opensearch_adapter_accepts_only_approved_identity_shapes(
    resource: dict[str, Any],
    reason: OpenSearchReason,
) -> None:
    payload = json.loads((FIXTURES / "opensearch-current.json").read_bytes())
    payload["hits"]["hits"][0]["_source"]["resource"] = resource
    result = OpenSearchAdapter(
        client=OneResponseClient(canonical_json_bytes(payload)),
        evidence_store=RecordingStore(),
        fixture=FROZEN,
    ).check_readiness(
        window=_window(),
        base_url="http://127.0.0.1:32773",
        artifact_prefix="cycles/01/baseline",
    )

    assert result.reason is reason


def test_opensearch_adapter_accepts_flattened_resource_identity() -> None:
    payload = json.loads((FIXTURES / "opensearch-current.json").read_bytes())
    resource = payload["hits"]["hits"][0]["_source"]["resource"]
    resource["service.name"] = resource.pop("service")["name"]
    client = OneResponseClient(canonical_json_bytes(payload))

    result = OpenSearchAdapter(
        client=client,
        evidence_store=RecordingStore(),
        fixture=FROZEN,
    ).check_readiness(
        window=_window(),
        base_url="http://127.0.0.1:32773",
        artifact_prefix="cycles/01/baseline",
    )

    assert result.ready


@pytest.mark.parametrize(
    "resource",
    [
        {"service.name": "ad", "service": {"name": "frontend"}},
        {"service.name": 7},
        {"service": "ad"},
        {},
    ],
)
def test_opensearch_adapter_fails_closed_on_identity_parse_error(
    resource: dict[str, Any],
) -> None:
    payload = json.loads((FIXTURES / "opensearch-current.json").read_bytes())
    payload["hits"]["hits"][0]["_source"]["resource"] = resource
    client = OneResponseClient(canonical_json_bytes(payload))

    result = OpenSearchAdapter(
        client=client,
        evidence_store=RecordingStore(),
        fixture=FROZEN,
    ).check_readiness(
        window=_window(),
        base_url="http://127.0.0.1:32773",
        artifact_prefix="cycles/01/baseline",
    )

    assert result.reason is OpenSearchReason.OPENSEARCH_SCHEMA_INVALID


@pytest.mark.parametrize(
    ("source", "field", "reason"),
    [
        (
            {"resource": {"service.name": "ad"}},
            "resource.service.name",
            OpenSearchServiceIdentityReason.PARSED,
        ),
        (
            {"resource": {"service": {"name": "ad"}}},
            "resource.service.name",
            OpenSearchServiceIdentityReason.PARSED,
        ),
        (
            {
                "resource": {
                    "service.name": "ad",
                    "service": {"name": "ad"},
                }
            },
            "resource.service.name",
            OpenSearchServiceIdentityReason.PARSED,
        ),
        (
            {
                "resource": {
                    "service.name": "ad",
                    "service": {"name": "frontend"},
                }
            },
            "resource.service.name",
            OpenSearchServiceIdentityReason.CONFLICT,
        ),
        (
            {"resource": {"service.name": 7}},
            "resource.service.name",
            OpenSearchServiceIdentityReason.TYPE_INVALID,
        ),
        (
            {"resource": {"service": "ad"}},
            "resource.service.name",
            OpenSearchServiceIdentityReason.SHAPE_INVALID,
        ),
        (
            {"resource": {}},
            "resource.service.name",
            OpenSearchServiceIdentityReason.MISSING,
        ),
        (
            {"resource": {"service.name": "ad"}},
            "resource.attributes.service.name",
            OpenSearchServiceIdentityReason.FIELD_UNSUPPORTED,
        ),
    ],
)
def test_opensearch_service_identity_parser_reports_exact_shape_reason(
    source: object,
    field: str,
    reason: OpenSearchServiceIdentityReason,
) -> None:
    parsed = parse_opensearch_service_identity(source, field=field)

    assert parsed.reason is reason


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        (
            lambda payload: payload["hits"]["hits"][0]["_source"]["resource"][
                "service"
            ].update({"name": "frontend"}),
            OpenSearchReason.OPENSEARCH_IDENTITY_MISMATCH,
        ),
        (
            lambda payload: payload["hits"]["hits"][0]["_source"].update(
                {"@timestamp": "2026-07-30T00:00:00Z"}
            ),
            OpenSearchReason.OPENSEARCH_STALE_LOG,
        ),
    ],
)
def test_opensearch_rejects_wrong_identity_and_previous_run_log(
    mutation,
    reason: OpenSearchReason,
) -> None:
    payload = json.loads((FIXTURES / "opensearch-current.json").read_bytes())
    mutation(payload)
    client = OneResponseClient(canonical_json_bytes(payload))
    store = RecordingStore()

    result = OpenSearchAdapter(
        client=client,
        evidence_store=store,
        fixture=FROZEN,
    ).check_readiness(
        window=_window(),
        base_url="http://127.0.0.1:32773",
        artifact_prefix="cycles/01/baseline",
    )

    assert not result.ready
    assert result.reason is reason
    assert len(store.records) == 2


@pytest.mark.parametrize("adapter_name", ["jaeger", "opensearch"])
def test_backend_decision_evidence_failure_never_returns_ready(
    adapter_name: str,
) -> None:
    body = (FIXTURES / f"{adapter_name}-current.json").read_bytes()
    client = OneResponseClient(body)
    store = RecordingStore(fail_at=2)
    adapter = (
        JaegerAdapter(client=client, evidence_store=store, fixture=FROZEN)
        if adapter_name == "jaeger"
        else OpenSearchAdapter(client=client, evidence_store=store, fixture=FROZEN)
    )

    result = adapter.check_readiness(
        window=_window(),
        base_url=(
            "http://127.0.0.1:32772"
            if adapter_name == "jaeger"
            else "http://127.0.0.1:32773"
        ),
        artifact_prefix="cycles/01/baseline",
    )

    assert not result.ready
    assert result.reason.value == "EVIDENCE_PERSISTENCE_FAILED"
