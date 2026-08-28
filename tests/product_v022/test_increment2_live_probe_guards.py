from __future__ import annotations

import httpx
import pytest

from ecomsre.product.pilot.live_schema_probe_v022 import (
    _BoundedOpenSearchProbeClientV022,
)


def test_probe_client_is_loopback_read_only_bounded_and_records_safe_metadata() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "127.0.0.1"
        return httpx.Response(200, json={"ok": True})

    client = _BoundedOpenSearchProbeClientV022(
        base_url="http://127.0.0.1:19200",
        maximum_request_count=2,
        maximum_response_bytes=200,
        transport=httpx.MockTransport(handler),
    )
    try:
        payload, raw = client.request_json("GET", "/otel-logs-*/_mapping")
        assert payload == {"ok": True}
        assert len(raw) <= 200
        assert client.request_count == 1
        assert client.request_metadata[0]["path"] == "/otel-logs-*/_mapping"
        with pytest.raises(ValueError, match="read-only"):
            client.request_json("DELETE", "/otel-logs-*")
        client.request_json("POST", "/otel-logs-*/_field_caps", json_body={})
        with pytest.raises(RuntimeError, match="budget"):
            client.request_json("GET", "/otel-logs-*/_mapping")
    finally:
        client.close()


def test_probe_client_rejects_non_loopback_and_oversized_responses() -> None:
    with pytest.raises(ValueError, match="loopback"):
        _BoundedOpenSearchProbeClientV022(
            base_url="https://opensearch.example.com",
            maximum_request_count=1,
            maximum_response_bytes=10,
        )

    client = _BoundedOpenSearchProbeClientV022(
        base_url="http://127.0.0.1:19200",
        maximum_request_count=1,
        maximum_response_bytes=10,
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(200, content=b'{"payload":"too large"}')
        ),
    )
    try:
        with pytest.raises(RuntimeError, match="byte bound"):
            client.request_json("GET", "/otel-logs-*/_mapping")
    finally:
        client.close()
