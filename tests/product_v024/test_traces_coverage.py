from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path

import httpx

from ecomsre.dta_v2.v22.read_contracts import (
    EvidenceSourceV22,
    METRIC_UNIT_BY_KIND_V22,
    MetricFactV22,
    MetricKindV22,
    MetricSupportStatusV22,
    ReadSourceStatusV22,
    ResourceSampleV22,
    ResourceUsageRecordV22,
    SpanStatusV22,
    TraceSpanV22,
)
from ecomsre.product.connectors.base import (
    ConnectorQueryContextV1,
    ConnectorQueryResultV1,
    ConnectorWindowV1,
)
from ecomsre.product.connectors.credentials import CredentialResolverV1
from ecomsre.product.connectors.jaeger import JaegerConnectorV1
from ecomsre.product.contracts import ConnectorConfigV1
from ecomsre.product.incidents.contracts import EvidenceBundleV1, EvidenceObjectV1
from scripts.product_v024.run_fresh_nofault import _validate_v024_sources


ROOT = Path(__file__).resolve().parents[2]
WINDOW = ConnectorWindowV1(
    started_at=datetime(2026, 9, 2, 0, 0, tzinfo=UTC),
    ended_at=datetime(2026, 9, 2, 0, 5, tzinfo=UTC),
)


def _context() -> ConnectorQueryContextV1:
    return ConnectorQueryContextV1(
        environment_id="env-0123456789abcdef01234567",
        requested_services=("checkout",),
        service_aliases={"checkout": "checkout"},
        window=WINDOW,
        maximum_records=12,
        requested_source=EvidenceSourceV22.TRACES,
        neighborhood_hops=1,
    )


def _partial_trace() -> dict[str, object]:
    return {
        "traceID": "1" * 32,
        "processes": {"p1": {"serviceName": "checkout", "tags": []}},
        "spans": [
            {
                "traceID": "1" * 32,
                "spanID": "1" * 16,
                "operationName": "partial",
                "startTime": int(WINDOW.started_at.timestamp() * 1_000_000),
                "duration": 10_000,
                "processID": "p1",
                "references": [
                    {"refType": "CHILD_OF", "spanID": "f" * 16}
                ],
                "tags": [],
            }
        ],
    }


def _deep_valid_trace() -> dict[str, object]:
    trace_id = "2" * 32
    spans: list[dict[str, object]] = []
    for ordinal in range(14):
        span_id = f"{ordinal + 1:016x}"
        spans.append(
            {
                "traceID": trace_id,
                "spanID": span_id,
                "operationName": f"operation-{ordinal}",
                "startTime": int(WINDOW.started_at.timestamp() * 1_000_000)
                + ordinal * 1_000,
                "duration": 10_000,
                "processID": "p1" if ordinal == 0 else "p2",
                "references": (
                    []
                    if ordinal == 0
                    else [
                        {
                            "refType": "CHILD_OF",
                            "spanID": f"{ordinal:016x}",
                        }
                    ]
                ),
                "tags": [],
            }
        )
    return {
        "traceID": trace_id,
        "processes": {
            "p1": {"serviceName": "frontend", "tags": []},
            "p2": {"serviceName": "checkout", "tags": []},
        },
        "spans": spans,
    }


def _tagged_trace(*values: object) -> dict[str, object]:
    return {
        "traceID": "3" * 32,
        "processes": {"p1": {"serviceName": "checkout", "tags": []}},
        "spans": [
            {
                "traceID": "3" * 32,
                "spanID": "3" * 16,
                "operationName": "tagged",
                "startTime": int(WINDOW.started_at.timestamp() * 1_000_000),
                "duration": 10_000,
                "processID": "p1",
                "references": [],
                "tags": [{"key": "error", "value": value} for value in values],
            }
        ],
    }


def _target_after_neighbors_trace() -> dict[str, object]:
    trace_id = "4" * 32
    root_id = "f" * 16
    neighbor_spans = [
        {
            "traceID": trace_id,
            "spanID": f"{ordinal + 1:016x}",
            "operationName": f"neighbor-{ordinal}",
            "startTime": int(WINDOW.started_at.timestamp() * 1_000_000)
            + ordinal * 1_000,
            "duration": 10_000,
            "processID": "p2",
            "references": [{"refType": "CHILD_OF", "spanID": root_id}],
            "tags": [],
        }
        for ordinal in range(13)
    ]
    return {
        "traceID": trace_id,
        "processes": {
            "p1": {"serviceName": "checkout", "tags": []},
            "p2": {"serviceName": "payment", "tags": []},
        },
        "spans": [
            *neighbor_spans,
            {
                "traceID": trace_id,
                "spanID": root_id,
                "operationName": "checkout",
                "startTime": int(WINDOW.started_at.timestamp() * 1_000_000),
                "duration": 10_000,
                "processID": "p1",
                "references": [],
                "tags": [],
            },
        ],
    }


def _connector(payload: dict[str, object]) -> JaegerConnectorV1:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/traces"
        assert request.url.params["service"] == "checkout"
        assert request.url.params["minDuration"] == "0ms"
        assert request.url.params["limit"] == "12"
        return httpx.Response(200, json=payload)

    return JaegerConnectorV1(
        ConnectorConfigV1(
            name="jaeger",
            kind="JAEGER",
            endpoint="https://jaeger.test",
            settings={"minimum_duration_ms": 0},
            credential_refs={},
        ),
        credential_resolver=CredentialResolverV1(environment={}),
        timeout_seconds=2,
        transport=httpx.MockTransport(handler),
    )


def test_v024_local_profile_acquires_normal_duration_spans() -> None:
    profile = json.loads(
        (ROOT / "examples/product/environment.otel-demo.json").read_text(
            encoding="utf-8"
        )
    )
    jaeger = next(
        item for item in profile["connector_configs"] if item["kind"] == "JAEGER"
    )

    assert jaeger["settings"]["minimum_duration_ms"] == 0


def test_jaeger_does_not_claim_untruncated_baseline_support() -> None:
    connector = _connector({"data": []})
    try:
        capability = connector.capabilities()[0]
    finally:
        connector.close()

    assert capability.source is EvidenceSourceV22.TRACES
    assert capability.supports_historical_range is True
    assert capability.supports_baseline is False


def test_partial_trace_does_not_mask_deep_valid_checkout_trace() -> None:
    connector = _connector({"data": [_partial_trace(), _deep_valid_trace()]})
    try:
        result = connector.query(_context())[0]
    finally:
        connector.close()

    assert result.status is ReadSourceStatusV22.SUCCESS_NONEMPTY
    assert result.safe_error_code is None
    assert "checkout" in result.covered_services
    assert result.records
    assert all(isinstance(item, TraceSpanV22) for item in result.records)
    assert all(item.duration_ms < 5000 for item in result.records)
    deep_records = tuple(
        item for item in result.records if item.operation.startswith("operation-")
    )
    assert deep_records
    assert all(
        item.service_path == ("frontend", "checkout") for item in deep_records
    )
    assert all(item.parent_service == "frontend" for item in deep_records)
    assert result.truncated is True


def test_missing_parent_trace_is_preserved_as_a_bounded_partial_root() -> None:
    connector = _connector({"data": [_partial_trace()]})
    try:
        result = connector.query(_context())[0]
    finally:
        connector.close()

    assert result.status is ReadSourceStatusV22.SUCCESS_NONEMPTY
    assert result.covered_services == ("checkout",)
    assert len(result.records) == 1
    assert result.records[0].service_path == ("checkout",)
    assert result.records[0].parent_service is None
    assert result.records[0].first_error_location is False
    assert result.truncated is True


def test_equivalent_duplicate_error_tags_preserve_the_span() -> None:
    connector = _connector({"data": [_tagged_trace("true", True)]})
    try:
        result = connector.query(_context())[0]
    finally:
        connector.close()

    assert result.status is ReadSourceStatusV22.SUCCESS_NONEMPTY
    assert len(result.records) == 1
    assert result.records[0].status is SpanStatusV22.ERROR
    assert result.records[0].first_error_location is True


def test_target_span_is_not_evicted_by_neighbor_record_limit() -> None:
    connector = _connector({"data": [_target_after_neighbors_trace()]})
    try:
        result = connector.query(_context())[0]
    finally:
        connector.close()

    assert result.status is ReadSourceStatusV22.SUCCESS_NONEMPTY
    assert len(result.records) == 12
    assert "checkout" in result.covered_services
    assert any(item.service == "checkout" for item in result.records)
    assert result.truncated is True


def test_conflicting_duplicate_error_tags_still_fail_closed() -> None:
    connector = _connector({"data": [_tagged_trace(True, False)]})
    try:
        result = connector.query(_context())[0]
    finally:
        connector.close()

    assert result.status is ReadSourceStatusV22.FAILURE_SCHEMA
    assert result.safe_error_code == "CONNECTOR_SCHEMA_INVALID"
    assert result.records == ()


def test_final_source_gate_requires_and_reports_trace_coverage() -> None:
    metric_records = tuple(
        MetricFactV22(
            schema_version="dta-v22.metric-fact.v1",
            service="checkout",
            metric_kind=kind,
            support_status=MetricSupportStatusV22.SUPPORTED,
            sample_count=3,
            value=1.0,
            unit=METRIC_UNIT_BY_KIND_V22[kind],
            window_started_at=WINDOW.started_at,
            window_ended_at=WINDOW.ended_at,
        )
        for kind in (
            MetricKindV22.ERROR_RATE,
            MetricKindV22.LATENCY_P95_MS,
            MetricKindV22.REQUEST_SUPPORT,
        )
    )
    resource = ResourceUsageRecordV22(
        schema_version="dta-v22.resource-usage-record.v1",
        service="checkout",
        sampling_window_seconds=10,
        samples=tuple(
            ResourceSampleV22(
                offset_ms=offset,
                cpu_percent=1.0,
                memory_bytes=1024,
            )
            for offset in (0, 2500, 5000, 7500, 10000)
        ),
        memory_slope_bytes_per_second=0.0,
    )
    trace = TraceSpanV22(
        schema_version="dta-v22.trace-span.v1",
        observed_at=WINDOW.started_at,
        service_path=("checkout",),
        service="checkout",
        parent_service=None,
        operation="checkout",
        status=SpanStatusV22.UNSET,
        duration_ms=1.0,
        first_error_location=False,
    )
    results = (
        ConnectorQueryResultV1.build(
            source=EvidenceSourceV22.METRICS,
            status=ReadSourceStatusV22.SUCCESS_NONEMPTY,
            requested_services=("checkout",),
            covered_services=("checkout",),
            window=WINDOW,
            records=metric_records,
            truncated=False,
            safe_error_code=None,
            latency_ms=1.0,
        ),
        ConnectorQueryResultV1.build(
            source=EvidenceSourceV22.RESOURCES,
            status=ReadSourceStatusV22.SUCCESS_NONEMPTY,
            requested_services=("checkout",),
            covered_services=("checkout",),
            window=WINDOW,
            records=(resource,),
            truncated=False,
            safe_error_code=None,
            latency_ms=1.0,
        ),
        ConnectorQueryResultV1.build(
            source=EvidenceSourceV22.TRACES,
            status=ReadSourceStatusV22.SUCCESS_NONEMPTY,
            requested_services=("checkout",),
            covered_services=("checkout",),
            window=WINDOW,
            records=(trace,),
            truncated=False,
            safe_error_code=None,
            latency_ms=1.0,
        ),
    )
    bundle = EvidenceBundleV1(
        incident_id="inc-" + "1" * 24,
        diagnosis_id="diag-" + "2" * 24,
        objects=tuple(
            EvidenceObjectV1(
                evidence_ref=f"e:test:{result.source.value.lower()}",
                source=result.source,
                action_id=f"a:{result.source.value.lower()}:checkout",
                object_sha256=f"{ordinal}" * 64,
                payload={"connector_result": result.model_dump(mode="json")},
            )
            for ordinal, result in enumerate(results, start=1)
        ),
        supporting_evidence_refs=(),
        contradicting_evidence_refs=(),
    )

    metrics, resources, traces = _validate_v024_sources(bundle)

    assert metrics["terminal"] == "ECOMSRE_PRODUCT_V024_METRICS_CONTRACT_PASS"
    assert resources["terminal"] == "ECOMSRE_PRODUCT_V024_RESOURCES_COVERAGE_PASS"
    assert traces == {
        "terminal": "ECOMSRE_PRODUCT_V024_TRACES_COVERAGE_PASS",
        "status": "SUCCESS_NONEMPTY",
        "requested_services": ["checkout"],
        "covered_services": ["checkout"],
        "record_count": 1,
        "normalized_services": ["checkout"],
        "safe_error_code": None,
    }
