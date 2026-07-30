from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from ecomsre.environment.ownership import (
    PROJECT_LABEL,
    PROJECT_NAMESPACE,
    RUN_LABEL,
    OwnedResource,
    OwnershipManifest,
)
from ecomsre.environment.ownership_authority import (
    create_ownership_authority_artifacts,
    load_authenticated_ownership_context,
)
from ecomsre.evidence.hashes import canonical_json_sha256, sha256_bytes
from ecomsre.telemetry.http import (
    HttpReason,
    HttpRequest,
    OwnedEndpoint,
    OwnedHttpClient,
)


RUN_ID = "2" * 32
NOW = datetime(2026, 7, 30, 1, 2, 3, tzinfo=UTC)


class FakeResponse:
    def __init__(
        self,
        *,
        status: int = 200,
        headers: tuple[tuple[str, str], ...] = (("Content-Type", "application/json"),),
        body: bytes = b'{"status":"success"}',
        read_error: BaseException | None = None,
        fail_after_reads: int = 0,
    ) -> None:
        self.status = status
        self.reason = "fixture"
        self._headers = headers
        self._body = body
        self._offset = 0
        self._read_error = read_error
        self._fail_after_reads = fail_after_reads
        self._reads = 0

    def getheaders(self) -> list[tuple[str, str]]:
        return list(self._headers)

    def read(self, size: int) -> bytes:
        if self._read_error is not None and self._reads >= self._fail_after_reads:
            raise self._read_error
        self._reads += 1
        chunk = self._body[self._offset : self._offset + size]
        self._offset += len(chunk)
        return chunk


class FakeConnection:
    def __init__(self, response: FakeResponse) -> None:
        self.response = response
        self.requests: list[tuple[str, str, bytes | None, dict[str, str]]] = []
        self.closed = False
        self.connected = False
        self.getresponse_calls = 0
        self.timeout: float | None = None
        self.sock = SimpleNamespace(settimeout=lambda _timeout: None)

    def connect(self) -> None:
        self.connected = True

    def request(
        self,
        method: str,
        target: str,
        body: bytes | None,
        headers: dict[str, str],
    ) -> None:
        self.requests.append((method, target, body, headers))

    def getresponse(self) -> FakeResponse:
        self.getresponse_calls += 1
        return self.response

    def close(self) -> None:
        self.closed = True


class FixtureFactory:
    def __init__(self, connection: FakeConnection) -> None:
        self.connection = connection
        self.calls: list[tuple[str, int, float]] = []

    def __call__(self, host: str, port: int, timeout: float) -> FakeConnection:
        self.calls.append((host, port, timeout))
        return self.connection


def _context(tmp_path: Path, *, host_family: str = "ipv4"):
    service = "prometheus"
    container_id = "container-prometheus"
    host_ip = "0.0.0.0" if host_family == "ipv4" else "::"
    published_port = 32771
    target_port = 9090
    labels = {
        PROJECT_LABEL: PROJECT_NAMESPACE,
        RUN_LABEL: RUN_ID,
        "com.docker.compose.service": service,
    }
    binding_payload = {
        "service": service,
        "container_name": "ecomsre-phase0-prometheus",
        "container_id": container_id,
        "host_ip": host_ip,
        "host_family": host_family,
        "published_port": published_port,
        "target_port": target_port,
        "protocol": "tcp",
    }
    port_id = f"port-binding:{canonical_json_sha256(binding_payload)}"
    manifest = OwnershipManifest(
        run_id=RUN_ID,
        resources=(
            OwnedResource(
                kind="container",
                name="ecomsre-phase0-prometheus",
                resource_id=container_id,
                labels=labels,
                identity_evidence=(
                    f"container:{container_id}",
                    "container_name:ecomsre-phase0-prometheus",
                    f"service:{service}",
                ),
            ),
            OwnedResource(
                kind="port",
                name=f"{service}:{target_port}->{published_port}/tcp@{host_family}",
                resource_id=port_id,
                labels=labels,
                identity_evidence=(
                    f"port:{port_id}",
                    f"container:{container_id}",
                    "container_name:ecomsre-phase0-prometheus",
                    f"service:{service}",
                    f"host_ip:{host_ip}",
                    f"host_family:{host_family}",
                    f"published_port:{published_port}",
                    f"target_port:{target_port}",
                    "protocol:tcp",
                    f"binding:{host_ip}:{published_port}->{target_port}/tcp",
                    f"raw_binding:{host_ip}:{published_port}->{target_port}/tcp",
                ),
            ),
        ),
    )
    create_ownership_authority_artifacts(tmp_path, manifest, created_at=NOW)
    return load_authenticated_ownership_context(tmp_path, RUN_ID)


def _request(
    *,
    base_url: str = "http://127.0.0.1:32771",
    deadline: float = 110.0,
    max_body_bytes: int = 1024,
    max_header_bytes: int = 1024,
) -> HttpRequest:
    return HttpRequest(
        endpoint=OwnedEndpoint(
            base_url=base_url,
            service="prometheus",
            target_port=9090,
        ),
        method="GET",
        target="/api/v1/query?query=up",
        absolute_deadline_monotonic=deadline,
        max_body_bytes=max_body_bytes,
        max_header_bytes=max_header_bytes,
    )


def _client(
    context: object,
    response: FakeResponse,
    *,
    monotonic: Callable[[], float] = lambda: 100.0,
) -> tuple[OwnedHttpClient, FakeConnection, FixtureFactory]:
    connection = FakeConnection(response)
    factory = FixtureFactory(connection)
    client = OwnedHttpClient(
        context=context,
        connection_factory=factory,
        monotonic=monotonic,
        utc_now=lambda: NOW,
    )
    return client, connection, factory


def test_owned_http_client_connects_directly_to_authenticated_loopback_port(
    tmp_path: Path,
) -> None:
    client, connection, factory = _client(_context(tmp_path), FakeResponse())

    exchange = client.request(_request())

    assert exchange.reason is HttpReason.OK
    assert exchange.succeeded
    assert factory.calls == [("127.0.0.1", 32771, 10.0)]
    assert connection.requests == [
        (
            "GET",
            "/api/v1/query?query=up",
            None,
            {"Accept-Encoding": "identity", "Connection": "close"},
        )
    ]
    assert exchange.raw_body == b'{"status":"success"}'
    assert exchange.raw_sha256 == sha256_bytes(exchange.raw_body)
    assert connection.closed


@pytest.mark.parametrize(
    "base_url",
    [
        "https://127.0.0.1:32771",
        "http://localhost:32771",
        "http://127.0.0.2:32771",
        "http://example.com:32771",
        "http://127.0.0.1:32771/path",
        "http://user@127.0.0.1:32771",
        "http://127.0.0.1:32772",
    ],
)
def test_owned_http_client_rejects_non_exact_or_unowned_endpoints_without_connecting(
    tmp_path: Path,
    base_url: str,
) -> None:
    client, connection, factory = _client(_context(tmp_path), FakeResponse())

    exchange = client.request(_request(base_url=base_url))

    assert exchange.reason is HttpReason.RESOURCE_OWNERSHIP_UNKNOWN
    assert not exchange.succeeded
    assert factory.calls == []
    assert not connection.requests
    assert not connection.closed


def test_owned_http_client_supports_only_owned_ipv6_loopback_binding(
    tmp_path: Path,
) -> None:
    client, connection, factory = _client(
        _context(tmp_path, host_family="ipv6"),
        FakeResponse(),
    )

    exchange = client.request(_request(base_url="http://[::1]:32771"))

    assert exchange.reason is HttpReason.OK
    assert factory.calls == [("::1", 32771, 10.0)]
    assert connection.closed


def test_owned_http_client_rejects_redirect_and_preserves_raw_body(
    tmp_path: Path,
) -> None:
    response = FakeResponse(status=302, body=b"redirect")
    client, connection, _factory = _client(_context(tmp_path), response)

    exchange = client.request(_request())

    assert exchange.reason is HttpReason.HTTP_REDIRECT_FORBIDDEN
    assert exchange.status_code == 302
    assert exchange.raw_body == b"redirect"
    assert exchange.raw_sha256 == sha256_bytes(b"redirect")
    assert connection.closed


def test_owned_http_client_preserves_bounded_partial_body_and_hash_on_overflow(
    tmp_path: Path,
) -> None:
    client, connection, _factory = _client(
        _context(tmp_path),
        FakeResponse(body=b"0123456789"),
    )

    exchange = client.request(_request(max_body_bytes=5))

    assert exchange.reason is HttpReason.HTTP_BODY_LIMIT_EXCEEDED
    assert exchange.raw_body == b"01234"
    assert exchange.raw_sha256 == sha256_bytes(b"01234")
    assert exchange.raw_body_partial
    assert connection.closed


def test_owned_http_client_rejects_oversized_headers_before_reading_body(
    tmp_path: Path,
) -> None:
    response = FakeResponse(headers=(("X-Large", "a" * 100),), body=b"unread")
    client, connection, _factory = _client(_context(tmp_path), response)

    exchange = client.request(_request(max_header_bytes=16))

    assert exchange.reason is HttpReason.HTTP_HEADER_LIMIT_EXCEEDED
    assert exchange.raw_body == b""
    assert exchange.raw_sha256 == sha256_bytes(b"")
    assert connection.closed


def test_owned_http_client_fails_before_connect_when_absolute_deadline_expired(
    tmp_path: Path,
) -> None:
    client, connection, factory = _client(
        _context(tmp_path),
        FakeResponse(),
        monotonic=lambda: 111.0,
    )

    exchange = client.request(_request(deadline=110.0))

    assert exchange.reason is HttpReason.HTTP_DEADLINE_EXCEEDED
    assert factory.calls == []
    assert not connection.closed


def test_owned_http_client_rechecks_absolute_deadline_after_send_and_closes(
    tmp_path: Path,
) -> None:
    ticks = iter((100.0, 101.0, 102.0, 111.0, 112.0))
    client, connection, factory = _client(
        _context(tmp_path),
        FakeResponse(),
        monotonic=lambda: next(ticks),
    )

    exchange = client.request(_request(deadline=110.0))

    assert exchange.reason is HttpReason.HTTP_DEADLINE_EXCEEDED
    assert factory.calls == [("127.0.0.1", 32771, 10.0)]
    assert connection.connected
    assert len(connection.requests) == 1
    assert connection.getresponse_calls == 0
    assert connection.closed


@pytest.mark.parametrize(
    ("ticks", "expected_stage"),
    [
        ((100.0, 101.0, 111.0, 112.0), "connect"),
        ((100.0, 101.0, 102.0, 103.0, 111.0, 112.0), "getresponse"),
        (
            (100.0, 101.0, 102.0, 103.0, 104.0, 105.0, 111.0, 112.0),
            "intermediate_read",
        ),
        (
            (
                100.0,
                101.0,
                102.0,
                103.0,
                104.0,
                105.0,
                106.0,
                107.0,
                111.0,
                112.0,
            ),
            "final_eof",
        ),
    ],
)
def test_owned_http_client_enforces_deadline_after_each_blocking_stage(
    tmp_path: Path,
    ticks: tuple[float, ...],
    expected_stage: str,
) -> None:
    client, connection, _factory = _client(
        _context(tmp_path),
        FakeResponse(body=b"x"),
        monotonic=iter(ticks).__next__,
    )

    exchange = client.request(_request(deadline=110.0))

    assert exchange.reason is HttpReason.HTTP_DEADLINE_EXCEEDED
    assert connection.closed
    if expected_stage == "connect":
        assert not connection.requests
    elif expected_stage == "getresponse":
        assert connection.getresponse_calls == 1
        assert exchange.raw_body == b""
    elif expected_stage == "intermediate_read":
        assert exchange.raw_body == b""
    else:
        assert exchange.raw_body == b"x"
        assert exchange.raw_body_partial


def test_owned_http_client_maps_transport_error_and_closes_connection(
    tmp_path: Path,
) -> None:
    response = FakeResponse(read_error=OSError("fixture read failure"))
    client, connection, _factory = _client(_context(tmp_path), response)

    exchange = client.request(_request())

    assert exchange.reason is HttpReason.HTTP_TRANSPORT_ERROR
    assert exchange.raw_body == b""
    assert exchange.raw_sha256 == sha256_bytes(b"")
    assert connection.closed


def test_owned_http_client_preserves_partial_raw_bytes_on_late_transport_error(
    tmp_path: Path,
) -> None:
    response = FakeResponse(
        body=b"x" * (64 * 1024 + 10),
        read_error=OSError("late fixture read failure"),
        fail_after_reads=1,
    )
    client, connection, _factory = _client(_context(tmp_path), response)

    exchange = client.request(_request(max_body_bytes=128 * 1024))

    assert exchange.reason is HttpReason.HTTP_TRANSPORT_ERROR
    assert exchange.raw_body == b"x" * (64 * 1024)
    assert exchange.raw_sha256 == sha256_bytes(exchange.raw_body)
    assert exchange.raw_body_partial
    assert connection.closed


def test_http_request_rejects_unbounded_request_headers_and_body() -> None:
    endpoint = OwnedEndpoint(
        base_url="http://127.0.0.1:32771",
        service="opensearch",
        target_port=9200,
    )
    with pytest.raises(ValidationError, match="request headers"):
        HttpRequest(
            endpoint=endpoint,
            method="POST",
            target="/_search",
            headers=(("X-Large", "a" * 100),),
            body=b"{}",
            absolute_deadline_monotonic=10,
            max_header_bytes=16,
        )
    with pytest.raises(ValidationError, match="request body"):
        HttpRequest(
            endpoint=endpoint,
            method="POST",
            target="/_search",
            body=b"x" * (1024 * 1024 + 1),
            absolute_deadline_monotonic=10,
        )
