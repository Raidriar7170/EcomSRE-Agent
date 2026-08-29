from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path

import httpx
import pytest

from ecomsre.product.connectors.opensearch_capture_v0222 import (
    OpenSearchCaptureStatusV0222,
    OpenSearchCaptureStoreV0222,
    OpenSearchPublicStructuralSummaryV0222,
    build_public_structural_summary_v0222,
)
from ecomsre.product.connectors.opensearch_http_v0221 import (
    OpenSearchProbeClientV0221,
)
from ecomsre.product.connectors.opensearch_probe_protocol_v0221 import (
    OpenSearchProbeEndpointKindV0221,
)


NOW = datetime(2026, 8, 29, 4, 0, tzinfo=UTC)


def _capture(
    store: OpenSearchCaptureStoreV0222,
    *,
    ordinal: int,
    request_id: str,
    request_kind: str,
    body: object,
) -> None:
    store.record_request_intent(
        request_id=request_id,
        request_plan_id="plan-a-capture-first",
        request_kind=request_kind,
        method="GET" if request_kind in {"INDEX_RESOLUTION", "MAPPING"} else "POST",
        endpoint_class=f"/{request_kind.lower()}",
        index_binding="otel-v1-apm-span-*",
        query_parameter_names=(),
        request_body_schema_sha256="1" * 64,
        request_ordinal=ordinal,
        created_at=NOW,
    )
    raw = json.dumps(body, separators=(",", ":")).encode()
    store.record_response(
        request_id=request_id,
        http_status=200,
        response_headers={
            "content-type": "application/json",
            "authorization": "must-not-persist",
        },
        response_body=raw,
        transport_latency_ms=3.25,
        received_at=NOW,
    )
    store.record_parse_result(
        request_id=request_id,
        safe_parse_stage="JSON_DECODED",
        safe_error_code=None,
        structural_summary_sha256="2" * 64,
        accepted=True,
    )


def test_completed_responses_survive_later_profile_resolution_failure(
    tmp_path: Path,
) -> None:
    store = OpenSearchCaptureStoreV0222(
        private_root=tmp_path / "private",
        session_id="product-v0222-capture-1",
        maximum_response_bytes=2_000_000,
    )
    captures = (
        ("resolved-index", "INDEX_RESOLUTION", {"indices": ["otel-logs-1"]}),
        ("mapping", "MAPPING", {"otel-logs-1": {"mappings": {}}}),
        ("field-caps", "FIELD_CAPS", {"fields": {}}),
        ("structural-sample", "STRUCTURAL_SAMPLE", {"hits": {"hits": []}}),
        ("service-aggregation", "SERVICE_AGGREGATION", {"aggregations": {}}),
        ("timestamp-range", "TIMESTAMP_RANGE", {"hits": {"hits": []}}),
        ("profile-verification", "PROFILE_VERIFICATION", {"hits": {"hits": []}}),
    )
    for ordinal, (request_id, request_kind, body) in enumerate(captures, start=1):
        _capture(
            store,
            ordinal=ordinal,
            request_id=request_id,
            request_kind=request_kind,
            body=body,
        )

    try:
        raise RuntimeError("simulated PROFILE_RESOLUTION failure")
    except RuntimeError:
        pass

    bundle = store.build_bundle()
    assert bundle.capture_completeness is True
    assert bundle.missing_capture_kinds == ()
    assert len(bundle.requests) == 7
    assert bundle.mapping_response_refs
    assert bundle.field_caps_response_refs
    assert bundle.structural_sample_refs
    assert bundle.service_aggregation_refs
    assert bundle.timestamp_range_refs
    assert bundle.profile_verification_refs

    loaded = OpenSearchCaptureStoreV0222.load_bundle(
        private_root=tmp_path / "private"
    )
    assert loaded == bundle
    assert store.verify_content_addressed_objects() == 7

    ledger_text = (tmp_path / "private" / "capture-ledger.jsonl").read_text(
        encoding="utf-8"
    )
    assert "must-not-persist" not in ledger_text
    assert "authorization" not in ledger_text.lower()


def test_http_client_records_intent_before_transport_and_response_before_json_parse(
    tmp_path: Path,
) -> None:
    store = OpenSearchCaptureStoreV0222(
        private_root=tmp_path / "private",
        session_id="product-v0222-capture-1",
        maximum_response_bytes=2_000_000,
    )

    def handler(_: httpx.Request) -> httpx.Response:
        ledger = store.capture_ledger()
        assert tuple(event.status for event in ledger.events) == (
            OpenSearchCaptureStatusV0222.INTENT_RECORDED,
        )
        return httpx.Response(
            200,
            content=b"not-json but must remain captured",
            headers={
                "content-type": "text/plain",
                "authorization": "must-not-persist",
            },
        )

    client = OpenSearchProbeClientV0221(
        base_url="http://127.0.0.1:19200",
        maximum_request_count=16,
        maximum_response_bytes=2_000_000,
        transport=httpx.MockTransport(handler),
        capture_store=store,
    )
    try:
        with pytest.raises(ValueError, match="not JSON"):
            client.request_json(
                plan_id="plan-a-capture-first",
                request_id="mapping",
                method="GET",
                endpoint_kind=OpenSearchProbeEndpointKindV0221.MAPPING,
                path="/otel-v1-apm-span-*/_mapping",
                path_template="/{index}/_mapping",
                query_parameters={},
                json_body=None,
            )
    finally:
        client.close()

    ledger = store.capture_ledger()
    assert tuple(event.status for event in ledger.events) == (
        OpenSearchCaptureStatusV0222.INTENT_RECORDED,
        OpenSearchCaptureStatusV0222.RESPONSE_CAPTURED,
        OpenSearchCaptureStatusV0222.REJECTED,
    )
    assert store.verify_content_addressed_objects() == 1
    assert (
        tmp_path
        / "private"
        / ledger.events[1].event_payload["response_object_ref"]
    ).read_bytes() == b"not-json but must remain captured"
    assert "must-not-persist" not in (
        tmp_path / "private" / "capture-ledger.jsonl"
    ).read_text(encoding="utf-8")


def test_public_summary_is_structural_and_excludes_private_response_material(
    tmp_path: Path,
) -> None:
    store = OpenSearchCaptureStoreV0222(
        private_root=tmp_path / "private",
        session_id="product-v0222-capture-1",
        maximum_response_bytes=2_000_000,
    )
    _capture(
        store,
        ordinal=1,
        request_id="mapping",
        request_kind="MAPPING",
        body={"message": "private checkout log body"},
    )
    bundle = store.build_bundle()

    summary = build_public_structural_summary_v0222(
        bundle=bundle,
        json_path_inventory={
            "body": "string",
            "resource.service.name": "string",
        },
        mapping_types={"body": ("text",)},
        field_caps_types={"body": ("text",)},
        presence_rates={"body": (5, 5)},
        timestamp_parseability_counts={"observedTimestamp": (5, 5)},
        service_alias_counts={"checkout": 5},
        message_type_classes=("string",),
        severity_type_classes=("string",),
        trace_id_type_classes=("string",),
        private_structural_shape_sha256="3" * 64,
    )

    reloaded = OpenSearchPublicStructuralSummaryV0222.model_validate_json(
        summary.model_dump_json()
    )
    assert reloaded == summary
    serialized = summary.model_dump_json()
    assert "private checkout log body" not in serialized
    assert "authorization" not in serialized.lower()
    assert bundle.responses[0].response_sha256 in serialized
    assert bundle.responses[0].response_object_ref not in serialized


def test_capture_ledger_detects_append_only_chain_tampering(tmp_path: Path) -> None:
    private_root = tmp_path / "private"
    store = OpenSearchCaptureStoreV0222(
        private_root=private_root,
        session_id="product-v0222-capture-1",
        maximum_response_bytes=2_000_000,
    )
    _capture(
        store,
        ordinal=1,
        request_id="mapping",
        request_kind="MAPPING",
        body={"mapping": True},
    )
    ledger_path = private_root / "capture-ledger.jsonl"
    lines = ledger_path.read_text(encoding="utf-8").splitlines()
    event = json.loads(lines[1])
    event["event_payload"]["http_status"] = 201
    lines[1] = json.dumps(event, separators=(",", ":"), sort_keys=True)
    ledger_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="event digest differs"):
        OpenSearchCaptureStoreV0222(
            private_root=private_root,
            session_id="product-v0222-capture-1",
            maximum_response_bytes=2_000_000,
        )
