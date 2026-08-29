from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from ecomsre.product.connectors.opensearch_http_v0221 import (
    OpenSearchHttpErrorCodeV0221,
    OpenSearchHttpErrorV0221,
    OpenSearchProbeClientV0221,
    build_opensearch_http_error_envelope_v0221,
    request_body_schema_sha256_v0221,
)
from ecomsre.product.connectors.opensearch_probe_protocol_v0221 import (
    OpenSearchProbeEndpointKindV0221,
)


ROOT = Path(__file__).resolve().parents[2]
FIXTURE = (
    ROOT
    / "tests/fixtures/product_v0221/opensearch_field_caps_body_400.safe.json"
)


def _fixture() -> dict[str, object]:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


def test_predecessor_400_safe_reproduction_extracts_typed_bounded_envelope() -> None:
    fixture = _fixture()
    request = fixture["request"]
    response = fixture["response"]
    assert isinstance(request, dict)
    assert isinstance(response, dict)
    response_bytes = json.dumps(response, separators=(",", ":")).encode()

    envelope = build_opensearch_http_error_envelope_v0221(
        http_status=400,
        response_body=response_bytes,
        method="POST",
        endpoint_kind=OpenSearchProbeEndpointKindV0221.FIELD_CAPS,
        path_template="/{index}/_field_caps",
        query_parameters={},
        request_body=request["body"],
    )

    assert fixture["provenance"] == "SAFE_REPRODUCTION_NOT_HISTORICAL_RAW_RESPONSE"
    assert envelope.http_status == 400
    assert envelope.error_type == "illegal_argument_exception"
    assert envelope.error_reason == "request body does not support [fields]"
    assert tuple(item.error_type for item in envelope.root_causes) == (
        "illegal_argument_exception",
    )
    assert envelope.safe_error_code is (
        OpenSearchHttpErrorCodeV0221.OPENSEARCH_REQUEST_BODY_INVALID
    )
    assert envelope.method == "POST"
    assert envelope.path_template == "/{index}/_field_caps"
    assert envelope.query_parameter_names == ()
    assert envelope.response_bytes == len(response_bytes)
    assert len(envelope.envelope_sha256) == 64

    serialized = envelope.model_dump_json()
    assert "resource.service.name.keyword" not in serialized
    assert "observed.timestamp" not in serialized
    assert "headers" not in serialized


def test_request_body_digest_binds_shape_not_field_values() -> None:
    first = request_body_schema_sha256_v0221({"fields": ["first.secret.value"]})
    second = request_body_schema_sha256_v0221({"fields": ["other.secret.value"]})
    changed = request_body_schema_sha256_v0221(
        {"index_filter": {"term": {"service": "checkout"}}}
    )

    assert first == second
    assert first != changed


def test_error_envelope_bounds_root_causes_and_reasons() -> None:
    long_reason = "x" * 400
    response = {
        "error": {
            "type": "illegal_argument_exception",
            "reason": long_reason,
            "root_cause": [
                {"type": f"type-{index}", "reason": long_reason}
                for index in range(5)
            ],
        },
        "status": 400,
    }
    envelope = build_opensearch_http_error_envelope_v0221(
        http_status=400,
        response_body=json.dumps(response).encode(),
        method="GET",
        endpoint_kind=OpenSearchProbeEndpointKindV0221.FIELD_CAPS,
        path_template="/{index}/_field_caps",
        query_parameters={"fields": "one,two"},
        request_body=None,
    )

    assert len(envelope.root_causes) == 3
    assert len(envelope.error_reason or "") == 240
    assert all(len(item.error_reason or "") == 240 for item in envelope.root_causes)
    assert envelope.query_parameter_names == ("fields",)
    assert "one,two" not in envelope.model_dump_json()


def test_non_json_error_and_secret_query_name_are_fail_closed() -> None:
    envelope = build_opensearch_http_error_envelope_v0221(
        http_status=502,
        response_body=b"upstream unavailable",
        method="GET",
        endpoint_kind=OpenSearchProbeEndpointKindV0221.MAPPING,
        path_template="/{index}/_mapping",
        query_parameters={},
        request_body=None,
    )
    assert envelope.error_type is None
    assert envelope.error_reason is None
    assert envelope.safe_error_code is (
        OpenSearchHttpErrorCodeV0221.OPENSEARCH_HTTP_TRANSIENT
    )

    with pytest.raises(ValueError, match="secret-bearing"):
        build_opensearch_http_error_envelope_v0221(
            http_status=400,
            response_body=b"{}",
            method="GET",
            endpoint_kind=OpenSearchProbeEndpointKindV0221.FIELD_CAPS,
            path_template="/{index}/_field_caps",
            query_parameters={"api_key": "must-not-persist"},
            request_body=None,
        )


def test_server_echo_of_query_and_body_values_is_redacted() -> None:
    response = {
        "error": {
            "type": "illegal_argument_exception",
            "reason": (
                "field must-not-leak and body private-field are invalid at "
                "http://127.0.0.1:19200/index?token=secret"
            ),
            "root_cause": [
                {
                    "type": "illegal_argument_exception",
                    "reason": "must-not-leak is unsupported",
                }
            ],
        },
        "status": 400,
    }
    envelope = build_opensearch_http_error_envelope_v0221(
        http_status=400,
        response_body=json.dumps(response).encode(),
        method="GET",
        endpoint_kind=OpenSearchProbeEndpointKindV0221.FIELD_CAPS,
        path_template="/{index}/_field_caps",
        query_parameters={"fields": "must-not-leak"},
        request_body={"candidate": "private-field"},
    )

    serialized = envelope.model_dump_json()
    assert "must-not-leak" not in serialized
    assert "private-field" not in serialized
    assert "token=secret" not in serialized
    assert "<request-value-redacted>" in serialized
    assert "<url-redacted>" in serialized


def test_probe_client_retains_400_attempt_and_typed_envelope() -> None:
    fixture = _fixture()
    response = fixture["response"]

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "127.0.0.1"
        return httpx.Response(400, json=response)

    client = OpenSearchProbeClientV0221(
        base_url="http://127.0.0.1:19200",
        maximum_request_count=16,
        maximum_response_bytes=2_000_000,
        transport=httpx.MockTransport(handler),
    )
    try:
        with pytest.raises(OpenSearchHttpErrorV0221) as raised:
            client.request_json(
                plan_id="plan-a-official-field-caps-get",
                request_id="field-caps-get-query",
                method="GET",
                endpoint_kind=OpenSearchProbeEndpointKindV0221.FIELD_CAPS,
                path="/otel-v1-apm-span-*/_field_caps",
                path_template="/{index}/_field_caps",
                query_parameters={
                    "fields": "observed.timestamp,resource.service.name.keyword"
                },
                json_body=None,
            )
    finally:
        client.close()

    assert raised.value.envelope.http_status == 400
    assert client.request_count == 1
    assert len(client.attempts) == 1
    assert client.attempts[0].http_status == 400
    assert client.attempts[0].safe_error_envelope_sha256 == (
        raised.value.envelope.envelope_sha256
    )
    assert client.attempts[0].query_parameter_names == ("fields",)
