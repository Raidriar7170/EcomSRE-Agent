"""Owned loopback HTTP transport shared by Phase 0 observer adapters."""

from __future__ import annotations

import http.client
import math
import socket
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Literal, Protocol
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ecomsre.environment.ownership import RUN_LABEL
from ecomsre.environment.ownership_authority import AuthenticatedOwnershipContext
from ecomsre.evidence.hashes import sha256_bytes
from ecomsre.phase0.models import MeasurementPhase


_COMPOSE_SERVICE_LABEL = "com.docker.compose.service"
_RUN_ID_PATTERN = r"^[0-9a-f]{32}$"
_MAX_REQUEST_TARGET_BYTES = 16 * 1024
_MAX_REQUEST_BODY_BYTES = 1024 * 1024
_DEFAULT_CHUNK_BYTES = 64 * 1024
_OBSERVER_INPUT_TOKEN = object()
_PRODUCTION_TRANSPORT_TOKEN = object()


class PhaseWindow(BaseModel):
    """One current-run phase with both UTC and monotonic clock bounds."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    run_id: str = Field(pattern=_RUN_ID_PATTERN)
    cycle_number: int = Field(ge=1)
    scenario_phase: MeasurementPhase
    utc_started_at: datetime
    utc_ended_at: datetime
    monotonic_started_at: float = Field(ge=0)
    monotonic_ended_at: float = Field(gt=0)

    @model_validator(mode="after")
    def require_consistent_bounds(self) -> "PhaseWindow":
        if (
            self.utc_started_at.utcoffset() is None
            or self.utc_started_at.utcoffset().total_seconds() != 0
            or self.utc_ended_at.utcoffset() is None
            or self.utc_ended_at.utcoffset().total_seconds() != 0
        ):
            raise ValueError("phase window timestamps must be UTC")
        if self.utc_ended_at <= self.utc_started_at:
            raise ValueError("phase window UTC end must follow its start")
        if self.monotonic_ended_at <= self.monotonic_started_at:
            raise ValueError("phase window monotonic end must follow its start")
        utc_duration = (self.utc_ended_at - self.utc_started_at).total_seconds()
        monotonic_duration = self.monotonic_ended_at - self.monotonic_started_at
        if not math.isclose(utc_duration, monotonic_duration, abs_tol=1e-6):
            raise ValueError("phase window UTC and monotonic duration disagree")
        return self

    def contains_delta_sample(self, timestamp: datetime) -> bool:
        """Apply the Prometheus counter window rule ``(start, end]``."""
        _require_utc(timestamp)
        return self.utc_started_at < timestamp <= self.utc_ended_at

    def contains_observation(self, timestamp: datetime) -> bool:
        """Apply inclusive freshness bounds to point observations."""
        _require_utc(timestamp)
        return self.utc_started_at <= timestamp <= self.utc_ended_at


class OwnedEndpoint(BaseModel):
    """An intended loopback endpoint plus its exact Compose service identity."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    base_url: str = Field(min_length=1)
    service: str = Field(min_length=1)
    target_port: int = Field(ge=1, le=65535)
    protocol: Literal["tcp"] = "tcp"


class HttpRequest(BaseModel):
    """One bounded request governed by an absolute monotonic deadline."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    endpoint: OwnedEndpoint
    method: Literal["GET", "POST"]
    target: str = Field(min_length=1)
    headers: tuple[tuple[str, str], ...] = ()
    body: bytes = b""
    absolute_deadline_monotonic: float = Field(gt=0)
    max_body_bytes: int = Field(default=1024 * 1024, ge=0, le=16 * 1024 * 1024)
    max_header_bytes: int = Field(default=64 * 1024, ge=1, le=1024 * 1024)

    @model_validator(mode="after")
    def require_origin_form_target_and_bounded_request(self) -> "HttpRequest":
        encoded = self.target.encode("ascii", errors="strict")
        if (
            not self.target.startswith("/")
            or self.target.startswith("//")
            or "#" in self.target
            or len(encoded) > _MAX_REQUEST_TARGET_BYTES
        ):
            raise ValueError("HTTP target must be a bounded origin-form path")
        names: set[str] = set()
        for name, value in self.headers:
            normalized = name.strip().casefold()
            if (
                not normalized
                or normalized in names
                or normalized in {"connection", "accept-encoding", "host"}
                or "\r" in name
                or "\n" in name
                or "\r" in value
                or "\n" in value
            ):
                raise ValueError("HTTP request headers are unsafe or duplicated")
            names.add(normalized)
        if _header_bytes(self.headers) > self.max_header_bytes:
            raise ValueError("HTTP request headers exceed the configured bound")
        if len(self.body) > _MAX_REQUEST_BODY_BYTES:
            raise ValueError("HTTP request body exceeds the configured bound")
        if self.method == "GET" and self.body:
            raise ValueError("GET request body is forbidden")
        return self


class HttpReason(str, Enum):
    OK = "OK"
    RESOURCE_OWNERSHIP_UNKNOWN = "RESOURCE_OWNERSHIP_UNKNOWN"
    HTTP_DEADLINE_EXCEEDED = "HTTP_DEADLINE_EXCEEDED"
    HTTP_TRANSPORT_ERROR = "HTTP_TRANSPORT_ERROR"
    HTTP_REDIRECT_FORBIDDEN = "HTTP_REDIRECT_FORBIDDEN"
    HTTP_STATUS_ERROR = "HTTP_STATUS_ERROR"
    HTTP_HEADER_LIMIT_EXCEEDED = "HTTP_HEADER_LIMIT_EXCEEDED"
    HTTP_BODY_LIMIT_EXCEEDED = "HTTP_BODY_LIMIT_EXCEEDED"


@dataclass(frozen=True)
class ObserverInputEnvelope:
    """Transport-issued capture of the exact observer-visible request inputs."""

    method: str
    target: str
    headers: tuple[tuple[str, str], ...]
    body_sha256: str
    _token: object = field(repr=False, compare=False)

    def is_authentic(self, request: HttpRequest) -> bool:
        return (
            self._token is _OBSERVER_INPUT_TOKEN
            and self.method == request.method
            and self.target == request.target
            and self.headers == request.headers
            and self.body_sha256 == sha256_bytes(request.body)
        )


@dataclass(frozen=True)
class HttpExchange:
    """Bounded raw exchange retained even when the request fails closed."""

    reason: HttpReason
    request: HttpRequest
    started_at: datetime
    ended_at: datetime
    monotonic_started_at: float
    monotonic_ended_at: float
    status_code: int | None
    response_headers: tuple[tuple[str, str], ...]
    raw_body: bytes
    raw_sha256: str
    raw_body_partial: bool
    observer_input_envelope: ObserverInputEnvelope | None = None

    @property
    def succeeded(self) -> bool:
        return self.reason is HttpReason.OK


class _PartialBodyFailure(RuntimeError):
    def __init__(self, error: BaseException, raw_body: bytes) -> None:
        self.error = error
        self.raw_body = raw_body
        super().__init__(str(error))


class _Response(Protocol):
    status: int

    def getheaders(self) -> list[tuple[str, str]]: ...

    def read(self, size: int) -> bytes: ...


class _Connection(Protocol):
    sock: object | None
    timeout: float | None

    def connect(self) -> None: ...

    def request(
        self,
        method: str,
        target: str,
        body: bytes | None,
        headers: dict[str, str],
    ) -> None: ...

    def getresponse(self) -> _Response: ...

    def close(self) -> None: ...


class ConnectionFactory(Protocol):
    def __call__(self, host: str, port: int, timeout: float) -> _Connection: ...


def _default_connection_factory(
    host: str,
    port: int,
    timeout: float,
) -> http.client.HTTPConnection:
    return http.client.HTTPConnection(host=host, port=port, timeout=timeout)


def _default_utc_now() -> datetime:
    return datetime.now(UTC)


class OwnedHttpClient:
    """Direct HTTP client that refuses endpoints outside signed ownership."""

    __slots__ = (
        "_context",
        "_connection_factory",
        "_monotonic",
        "_utc_now",
        "_transport_token",
    )

    def __init__(
        self,
        *,
        context: AuthenticatedOwnershipContext,
        connection_factory: ConnectionFactory = _default_connection_factory,
        monotonic: Callable[[], float] = time.monotonic,
        utc_now: Callable[[], datetime] = _default_utc_now,
    ) -> None:
        self._context = context
        self._connection_factory = connection_factory
        self._monotonic = monotonic
        self._utc_now = utc_now
        self._transport_token = (
            _PRODUCTION_TRANSPORT_TOKEN
            if connection_factory is _default_connection_factory
            and monotonic is time.monotonic
            and utc_now is _default_utc_now
            else None
        )

    @property
    def run_id(self) -> str:
        return self._context.run_id

    @property
    def has_production_transport_capability(self) -> bool:
        return _owned_http_client_has_production_integrity(self)

    def request(self, request: HttpRequest) -> HttpExchange:
        started_at = self._utc_now()
        monotonic_started = self._monotonic()
        authorization = self._authorize(request.endpoint)
        if authorization is None:
            return self._exchange(
                request=request,
                reason=HttpReason.RESOURCE_OWNERSHIP_UNKNOWN,
                started_at=started_at,
                monotonic_started=monotonic_started,
            )
        host, port = authorization
        remaining = request.absolute_deadline_monotonic - monotonic_started
        if remaining <= 0:
            return self._exchange(
                request=request,
                reason=HttpReason.HTTP_DEADLINE_EXCEEDED,
                started_at=started_at,
                monotonic_started=monotonic_started,
            )

        connection: _Connection | None = None
        status_code: int | None = None
        response_headers: tuple[tuple[str, str], ...] = ()
        raw_body = b""
        raw_partial = False
        reason = HttpReason.HTTP_TRANSPORT_ERROR
        try:
            connection = self._connection_factory(host, port, remaining)
            self._apply_remaining_timeout(
                connection,
                request.absolute_deadline_monotonic,
            )
            connection.connect()
            self._apply_remaining_timeout(
                connection,
                request.absolute_deadline_monotonic,
            )
            headers = {
                "Accept-Encoding": "identity",
                "Connection": "close",
                **dict(request.headers),
            }
            body = request.body if request.method == "POST" else None
            connection.request(request.method, request.target, body, headers)
            self._apply_remaining_timeout(
                connection,
                request.absolute_deadline_monotonic,
            )
            response = connection.getresponse()
            self._apply_remaining_timeout(
                connection,
                request.absolute_deadline_monotonic,
            )
            status_code = response.status
            response_headers = tuple(response.getheaders())
            if _header_bytes(response_headers) > request.max_header_bytes:
                reason = HttpReason.HTTP_HEADER_LIMIT_EXCEEDED
            else:
                raw_body, raw_partial = self._read_bounded_body(
                    connection,
                    response,
                    deadline=request.absolute_deadline_monotonic,
                    limit=request.max_body_bytes,
                )
                if raw_partial:
                    reason = HttpReason.HTTP_BODY_LIMIT_EXCEEDED
                elif 300 <= status_code <= 399:
                    reason = HttpReason.HTTP_REDIRECT_FORBIDDEN
                elif not 200 <= status_code <= 299:
                    reason = HttpReason.HTTP_STATUS_ERROR
                else:
                    reason = HttpReason.OK
        except _PartialBodyFailure as failure:
            raw_body = failure.raw_body
            raw_partial = bool(raw_body)
            reason = (
                HttpReason.HTTP_DEADLINE_EXCEEDED
                if isinstance(failure.error, (TimeoutError, socket.timeout))
                else HttpReason.HTTP_TRANSPORT_ERROR
            )
        except (TimeoutError, socket.timeout):
            reason = HttpReason.HTTP_DEADLINE_EXCEEDED
        except (OSError, http.client.HTTPException, ValueError):
            reason = HttpReason.HTTP_TRANSPORT_ERROR
        finally:
            if connection is not None:
                connection.close()

        return self._exchange(
            request=request,
            reason=reason,
            started_at=started_at,
            monotonic_started=monotonic_started,
            status_code=status_code,
            response_headers=response_headers,
            raw_body=raw_body,
            raw_body_partial=raw_partial,
        )

    def _read_bounded_body(
        self,
        connection: _Connection,
        response: _Response,
        *,
        deadline: float,
        limit: int,
    ) -> tuple[bytes, bool]:
        captured = bytearray()
        while True:
            try:
                self._apply_remaining_timeout(connection, deadline)
                remaining = limit - len(captured)
                chunk = response.read(min(_DEFAULT_CHUNK_BYTES, remaining + 1))
                self._apply_remaining_timeout(connection, deadline)
            except (
                TimeoutError,
                socket.timeout,
                OSError,
                http.client.HTTPException,
                ValueError,
            ) as error:
                raise _PartialBodyFailure(error, bytes(captured)) from error
            if not chunk:
                return bytes(captured), False
            if len(chunk) > remaining:
                captured.extend(chunk[:remaining])
                return bytes(captured), True
            captured.extend(chunk)

    def _apply_remaining_timeout(
        self,
        connection: _Connection,
        deadline: float,
    ) -> None:
        remaining = deadline - self._monotonic()
        if remaining <= 0:
            raise TimeoutError("absolute HTTP deadline elapsed")
        sock = getattr(connection, "sock", None)
        if sock is not None:
            settimeout = getattr(sock, "settimeout", None)
            if callable(settimeout):
                settimeout(remaining)
                return
        if hasattr(connection, "timeout"):
            connection.timeout = remaining

    def _authorize(self, endpoint: OwnedEndpoint) -> tuple[str, int] | None:
        context = self._context
        if (
            not isinstance(context, AuthenticatedOwnershipContext)
            or not context.is_authentic()
            or context.manifest.run_id != context.run_id
        ):
            return None
        try:
            parsed = urlsplit(endpoint.base_url)
            port = parsed.port
        except ValueError:
            return None
        host = parsed.hostname
        if (
            parsed.scheme != "http"
            or host not in {"127.0.0.1", "::1"}
            or port is None
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path
            or parsed.query
            or parsed.fragment
        ):
            return None

        host_family = "ipv6" if host == "::1" else "ipv4"
        matches = []
        for resource in context.manifest.resources:
            if (
                resource.kind != "port"
                or resource.labels.get(RUN_LABEL) != context.run_id
                or resource.labels.get(_COMPOSE_SERVICE_LABEL) != endpoint.service
            ):
                continue
            evidence = _unique_evidence(resource.identity_evidence)
            if evidence is None:
                continue
            allowed_host = evidence.get("host_family") == host_family and evidence.get(
                "host_ip"
            ) in ({"::", "::1"} if host_family == "ipv6" else {"0.0.0.0", "127.0.0.1"})
            if (
                allowed_host
                and evidence.get("service") == endpoint.service
                and evidence.get("protocol") == endpoint.protocol
                and evidence.get("published_port") == str(port)
                and evidence.get("target_port") == str(endpoint.target_port)
            ):
                matches.append(resource)
        if len(matches) != 1:
            return None
        return host, port

    def _exchange(
        self,
        *,
        request: HttpRequest,
        reason: HttpReason,
        started_at: datetime,
        monotonic_started: float,
        status_code: int | None = None,
        response_headers: tuple[tuple[str, str], ...] = (),
        raw_body: bytes = b"",
        raw_body_partial: bool = False,
    ) -> HttpExchange:
        ended_at = self._utc_now()
        monotonic_ended = self._monotonic()
        return HttpExchange(
            reason=reason,
            request=request,
            started_at=started_at,
            ended_at=ended_at,
            monotonic_started_at=monotonic_started,
            monotonic_ended_at=monotonic_ended,
            status_code=status_code,
            response_headers=response_headers,
            raw_body=raw_body,
            raw_sha256=sha256_bytes(raw_body),
            raw_body_partial=raw_body_partial,
            observer_input_envelope=ObserverInputEnvelope(
                method=request.method,
                target=request.target,
                headers=request.headers,
                body_sha256=sha256_bytes(request.body),
                _token=_OBSERVER_INPUT_TOKEN,
            ),
        )


_OWNED_HTTP_CLIENT_METHODS = (
    OwnedHttpClient.__init__,
    OwnedHttpClient.request,
    OwnedHttpClient._read_bounded_body,
    OwnedHttpClient._apply_remaining_timeout,
    OwnedHttpClient._authorize,
    OwnedHttpClient._exchange,
)


def _owned_http_client_has_production_integrity(client: object) -> bool:
    """Reject injected state or class-level monkeypatches at every signing edge."""
    return (
        type(client) is OwnedHttpClient
        and not hasattr(client, "__dict__")
        and client._transport_token is _PRODUCTION_TRANSPORT_TOKEN
        and (
            OwnedHttpClient.__init__,
            OwnedHttpClient.request,
            OwnedHttpClient._read_bounded_body,
            OwnedHttpClient._apply_remaining_timeout,
            OwnedHttpClient._authorize,
            OwnedHttpClient._exchange,
        )
        == _OWNED_HTTP_CLIENT_METHODS
    )


def _unique_evidence(values: tuple[str, ...]) -> dict[str, str] | None:
    parsed: dict[str, str] = {}
    for item in values:
        key, separator, value = item.partition(":")
        if not separator or key in parsed:
            return None
        parsed[key] = value
    return parsed


def _header_bytes(headers: tuple[tuple[str, str], ...]) -> int:
    try:
        return sum(
            len(name.encode("latin-1")) + len(value.encode("latin-1")) + len(b": \r\n")
            for name, value in headers
        )
    except UnicodeEncodeError:
        return 2**63 - 1


def _require_utc(timestamp: datetime) -> None:
    if timestamp.utcoffset() is None or timestamp.utcoffset().total_seconds() != 0:
        raise ValueError("timestamp must be UTC")
