"""Evaluator-only physical flagd control and durable hidden truth."""

import base64
import binascii
import fcntl
import json
import os
import re
import secrets
import socket
import stat
import threading
import time
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable, Iterator, Literal, Protocol
from urllib.parse import quote, urlsplit

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ecomsre.evidence.hashes import canonical_json_bytes, sha256_bytes
from ecomsre.evidence.store import (
    _DirectoryCapability,
    _append_jsonl,
    _validate_file_metadata,
    _write_all,
    _write_immutable_bytes,
)
from ecomsre.scenarios.ad_service_failure import (
    ControlMutationUncertain,
    EvidencePersistenceError,
    MutationState,
    PendingRecoveryEvidenceError,
    ScenarioAction,
    ScenarioState,
    TransitionGuardCleanupError,
    TransitionExecution,
    TransitionPreparation,
)


UPSTREAM_FLAGD_CONFIG_SHA256 = (
    "bef4fa5da0ad8b1f64cc0d66fc66afaf7b9877c85895b78bf47d9a97577f9983"
)
UPSTREAM_FLAGD_SCHEMA = "https://flagd.dev/schema/v0/flags.json"
UPSTREAM_FLAGD_CONFIG_PATH = (
    Path("third_party") / "opentelemetry-demo" / "src" / "flagd" / "demo.flagd.json"
)
FLAGD_CONFIG_RELATIVE_PATH = "control/demo.flagd.json"
MAX_OFREP_RESPONSE_BYTES = 64 * 1024
_SCENARIO_IDENTITY = "adServiceFailure"
_PHYSICAL_FLAG_KEY = "adFailure"
_BASELINE_VARIANT = "off"
_FAULT_VARIANT = "on"
_TRANSITION_LOCK_RELATIVE_PATH = "locks/scenario-control.lock"


@dataclass
class _ThreadLockEntry:
    lock: threading.Lock = field(default_factory=threading.Lock)
    users: int = 0


_THREAD_LOCKS: dict[str, _ThreadLockEntry] = {}
_THREAD_LOCKS_GUARD = threading.Lock()
_ALLOWED_TOP_LEVEL = {
    "control",
    "control-prepared.jsonl",
    "control-intents.jsonl",
    "control-events.jsonl",
    "emergency",
    "locks",
    "readbacks",
    "scenario-ground-truth.json",
}


class FlagdSourceContractError(ValueError):
    """The pinned upstream flagd source no longer matches Phase 0."""


class FlagdRuntimeUnavailable(ValueError):
    """The prepared run-scoped control files do not exist."""


class _ObservationEvidencePersistenceError(EvidencePersistenceError):
    def __init__(self, *, observed_state: ScenarioState) -> None:
        super().__init__("raw OFREP readback evidence could not be persisted")
        self.observed_state = observed_state


class OfrepReadback(BaseModel):
    """Bounded raw OFREP response plus strict parse and receipt metadata."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["phase0.ofrep-readback.v1"]
    http_status: int | None = Field(default=None, ge=100, le=599)
    raw_response_body_b64: str
    raw_body_truncated: bool
    parsed_value: bool | None
    parsed_variant: str | None
    parsed_reason: str | None
    received_at: datetime
    received_monotonic: float = Field(ge=0)
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    error_code: Literal[
        "NONE",
        "HTTP_STATUS",
        "INVALID_JSON",
        "INVALID_SCHEMA",
        "BODY_OVERSIZE",
        "TRANSPORT_ERROR",
    ]
    request_metadata: dict[str, str]

    @model_validator(mode="after")
    def require_bounded_consistent_readback(self) -> "OfrepReadback":
        try:
            raw = base64.b64decode(
                self.raw_response_body_b64,
                validate=True,
            )
        except binascii.Error as error:
            raise ValueError("OFREP raw body must be canonical base64") from error
        if len(raw) > MAX_OFREP_RESPONSE_BYTES:
            raise ValueError("OFREP raw body exceeds the evidence bound")
        if sha256_bytes(raw) != self.content_sha256:
            raise ValueError("OFREP raw body content hash is inconsistent")
        if (
            self.received_at.utcoffset() is None
            or self.received_at.utcoffset().total_seconds() != 0
        ):
            raise ValueError("OFREP receipt timestamp must be UTC")
        if self.request_metadata != {
            "method": "POST",
            "content_type": "application/json",
            "request_body_sha256": sha256_bytes(b"{}"),
        }:
            raise ValueError("OFREP request metadata is not sanitized and canonical")
        if self.error_code == "NONE" and (
            self.http_status is None
            or not 200 <= self.http_status < 300
            or not isinstance(self.parsed_value, bool)
            or not self.parsed_variant
        ):
            raise ValueError("successful OFREP readback lacks a strict parsed value")
        if self.error_code == "BODY_OVERSIZE" and not self.raw_body_truncated:
            raise ValueError("OFREP truncation marker conflicts with error code")
        if self.raw_body_truncated and self.error_code not in {
            "BODY_OVERSIZE",
            "TRANSPORT_ERROR",
        }:
            raise ValueError("OFREP truncation marker conflicts with error code")
        return self


class OfrepClient(Protocol):
    def evaluate(
        self,
        *,
        endpoint: str,
        flag_key: str,
        timeout_seconds: float,
    ) -> OfrepReadback: ...


class _PreparationOnlyOfrepClient:
    def evaluate(
        self,
        *,
        endpoint: str,
        flag_key: str,
        timeout_seconds: float,
    ) -> OfrepReadback:
        del endpoint, flag_key, timeout_seconds
        raise RuntimeError("prepared runtime has no live OFREP endpoint")


class HttpOfrepClient:
    """Bounded direct loopback OFREP adapter with no proxy or redirect surface."""

    def __init__(
        self,
        *,
        clock: "ReadbackClock | None" = None,
        socket_factory: "SocketFactory | None" = None,
    ) -> None:
        self._clock = clock or SystemReadbackClock()
        self._socket_factory = socket_factory or socket.socket

    def evaluate(
        self,
        *,
        endpoint: str,
        flag_key: str,
        timeout_seconds: float,
    ) -> OfrepReadback:
        if timeout_seconds <= 0:
            raise ValueError("OFREP timeout must be positive")
        base = _validate_local_ofrep_endpoint(endpoint)
        parsed = urlsplit(base)
        assert parsed.hostname is not None
        assert parsed.port is not None
        host = parsed.hostname
        port = parsed.port
        path = f"/ofrep/v1/evaluate/flags/{quote(flag_key, safe='')}"
        host_header = f"[{host}]:{port}" if ":" in host else f"{host}:{port}"
        request = (
            f"POST {path} HTTP/1.1\r\n"
            f"Host: {host_header}\r\n"
            "Content-Type: application/json\r\n"
            "Content-Length: 2\r\n"
            "Connection: close\r\n"
            "\r\n"
        ).encode("ascii") + b"{}"
        deadline = self._clock.monotonic() + timeout_seconds
        family = socket.AF_INET6 if host == "::1" else socket.AF_INET
        connection = self._socket_factory(family, socket.SOCK_STREAM)
        status: int | None = None
        body = b""
        truncated = False
        try:
            _set_socket_remaining_timeout(
                connection,
                clock=self._clock,
                deadline=deadline,
            )
            address = (host, port, 0, 0) if family == socket.AF_INET6 else (host, port)
            connection.connect(address)
            _set_socket_remaining_timeout(
                connection,
                clock=self._clock,
                deadline=deadline,
            )
            connection.sendall(request)
            status, headers, buffered_body = _read_http_headers(
                connection,
                clock=self._clock,
                deadline=deadline,
            )
            body, truncated = _read_http_body(
                connection,
                headers=headers,
                buffered=buffered_body,
                clock=self._clock,
                deadline=deadline,
            )
        except _PartialHttpReadError as error:
            return _build_ofrep_readback(
                status=(error.http_status if error.http_status is not None else status),
                body=error.body,
                truncated=error.truncated,
                transport_failed=True,
                clock=self._clock,
            )
        except (OSError, TimeoutError, ValueError):
            return _build_ofrep_readback(
                status=status,
                body=body,
                truncated=False,
                transport_failed=True,
                clock=self._clock,
            )
        finally:
            try:
                connection.close()
            except OSError:
                pass
        return _build_ofrep_readback(
            status=status,
            body=body,
            truncated=truncated,
            transport_failed=False,
            clock=self._clock,
        )


class ReadbackClock(Protocol):
    def now(self) -> datetime: ...

    def monotonic(self) -> float: ...


class SystemReadbackClock:
    def now(self) -> datetime:
        return datetime.now(UTC)

    def monotonic(self) -> float:
        return time.monotonic()


class DirectSocket(Protocol):
    def settimeout(self, timeout: float) -> None: ...

    def connect(self, address: object) -> None: ...

    def sendall(self, content: bytes) -> None: ...

    def recv(self, amount: int) -> bytes: ...

    def close(self) -> None: ...


class _PartialHttpReadError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        body: bytes,
        truncated: bool = False,
        http_status: int | None = None,
    ) -> None:
        super().__init__(message)
        self.body = body[:MAX_OFREP_RESPONSE_BYTES]
        self.truncated = truncated or len(body) > MAX_OFREP_RESPONSE_BYTES
        self.http_status = http_status


SocketFactory = Callable[[int, int], DirectSocket]
_MAX_OFREP_HEADER_BYTES = 64 * 1024
_MAX_CHUNK_SIZE_LINE_BYTES = 128
_MAX_CHUNK_TRAILER_LINE_BYTES = 1024
_MAX_CHUNK_TRAILER_BYTES = 4 * 1024
_MAX_CHUNK_COUNT = 1024
_MAX_CHUNK_TRAILER_COUNT = 64
_MAX_CHUNK_FRAMING_BYTES = 4 * 1024


def _set_socket_remaining_timeout(
    connection: DirectSocket,
    *,
    clock: ReadbackClock,
    deadline: float,
) -> None:
    remaining = deadline - clock.monotonic()
    if remaining <= 0:
        raise TimeoutError("OFREP total deadline exceeded")
    connection.settimeout(remaining)


def _receive_with_deadline(
    connection: DirectSocket,
    *,
    amount: int,
    clock: ReadbackClock,
    deadline: float,
) -> bytes:
    _set_socket_remaining_timeout(
        connection,
        clock=clock,
        deadline=deadline,
    )
    return connection.recv(amount)


def _read_http_headers(
    connection: DirectSocket,
    *,
    clock: ReadbackClock,
    deadline: float,
) -> tuple[int, dict[str, str], bytes]:
    received = bytearray()
    delimiter = b"\r\n\r\n"
    while delimiter not in received:
        remaining_wire = _MAX_OFREP_HEADER_BYTES + 1 - len(received)
        if remaining_wire <= 0:
            raise ValueError("OFREP response headers exceed the bound")
        chunk = _receive_with_deadline(
            connection,
            amount=min(16 * 1024, remaining_wire),
            clock=clock,
            deadline=deadline,
        )
        if not chunk:
            raise ValueError("OFREP response ended before HTTP headers")
        received.extend(chunk)
        if len(received) > _MAX_OFREP_HEADER_BYTES:
            raise ValueError("OFREP response headers exceed the bound")
    header_bytes, buffered_body = bytes(received).split(delimiter, 1)
    try:
        lines = header_bytes.decode("iso-8859-1").split("\r\n")
    except UnicodeDecodeError as error:
        raise ValueError("OFREP response headers are malformed") from error
    status_match = re.fullmatch(r"HTTP/1\.[01] ([0-9]{3})(?: .*)?", lines[0])
    if status_match is None:
        raise _PartialHttpReadError(
            "OFREP response status line is malformed",
            body=buffered_body,
        )
    status = int(status_match.group(1))
    if not 100 <= status <= 599:
        raise _PartialHttpReadError(
            "OFREP response status is out of range",
            body=buffered_body,
        )
    headers: dict[str, str] = {}
    for line in lines[1:]:
        name, separator, value = line.partition(":")
        if not separator or not name or name[:1].isspace():
            raise _PartialHttpReadError(
                "OFREP response header is malformed",
                body=buffered_body,
                http_status=status,
            )
        normalized = name.strip().casefold()
        if normalized in headers:
            headers[normalized] = f"{headers[normalized]},{value.strip()}"
        else:
            headers[normalized] = value.strip()
    return status, headers, buffered_body


def _read_http_body(
    connection: DirectSocket,
    *,
    headers: dict[str, str],
    buffered: bytes,
    clock: ReadbackClock,
    deadline: float,
) -> tuple[bytes, bool]:
    transfer_encoding = headers.get("transfer-encoding", "").casefold()
    if transfer_encoding:
        if transfer_encoding != "chunked":
            raise _PartialHttpReadError(
                "unsupported OFREP transfer encoding",
                body=buffered,
            )
        return _read_chunked_http_body(
            connection,
            buffered=buffered,
            clock=clock,
            deadline=deadline,
        )

    content_length_text = headers.get("content-length")
    if content_length_text is not None:
        if not content_length_text.isdigit():
            raise _PartialHttpReadError(
                "OFREP content length is malformed",
                body=buffered,
            )
        content_length = int(content_length_text)
        target = min(content_length, MAX_OFREP_RESPONSE_BYTES + 1)
        body = bytearray(buffered[:target])
        try:
            while len(body) < target:
                chunk = _receive_with_deadline(
                    connection,
                    amount=min(16 * 1024, target - len(body)),
                    clock=clock,
                    deadline=deadline,
                )
                if not chunk:
                    raise ValueError("OFREP body ended before Content-Length")
                body.extend(chunk)
        except (OSError, TimeoutError, ValueError) as error:
            raise _PartialHttpReadError(
                "OFREP Content-Length body read failed",
                body=bytes(body),
                truncated=content_length > MAX_OFREP_RESPONSE_BYTES,
            ) from error
        truncated = content_length > MAX_OFREP_RESPONSE_BYTES
        return bytes(body[:MAX_OFREP_RESPONSE_BYTES]), truncated

    body = bytearray(buffered[: MAX_OFREP_RESPONSE_BYTES + 1])
    try:
        while len(body) <= MAX_OFREP_RESPONSE_BYTES:
            chunk = _receive_with_deadline(
                connection,
                amount=min(
                    16 * 1024,
                    MAX_OFREP_RESPONSE_BYTES + 1 - len(body),
                ),
                clock=clock,
                deadline=deadline,
            )
            if not chunk:
                break
            body.extend(chunk)
    except (OSError, TimeoutError, ValueError) as error:
        raise _PartialHttpReadError(
            "OFREP close-delimited body read failed",
            body=bytes(body),
        ) from error
    return (
        bytes(body[:MAX_OFREP_RESPONSE_BYTES]),
        len(body) > MAX_OFREP_RESPONSE_BYTES,
    )


class _DeadlineSocketBuffer:
    def __init__(
        self,
        connection: DirectSocket,
        *,
        buffered: bytes,
        clock: ReadbackClock,
        deadline: float,
    ) -> None:
        self._connection = connection
        self._buffer = bytearray(buffered)
        self._clock = clock
        self._deadline = deadline

    def read_line(self, *, max_bytes: int) -> bytes:
        if max_bytes <= 0:
            raise ValueError("chunked OFREP line bound must be positive")
        while True:
            delimiter = self._buffer.find(b"\r\n")
            if delimiter >= 0:
                if delimiter > max_bytes:
                    raise ValueError("chunked OFREP line exceeds the bound")
                line = bytes(self._buffer[:delimiter])
                del self._buffer[: delimiter + 2]
                return line
            remaining_wire = max_bytes + 2 - len(self._buffer)
            if remaining_wire <= 0:
                raise ValueError("chunked OFREP line exceeds the bound")
            self._fill(max_amount=remaining_wire)

    def read_exact(
        self,
        amount: int,
        *,
        preserve_partial: bool = False,
    ) -> bytes:
        if amount < 0:
            raise ValueError("chunked OFREP read amount is invalid")
        try:
            while len(self._buffer) < amount:
                self._fill(max_amount=amount - len(self._buffer))
        except (OSError, TimeoutError, ValueError) as error:
            if preserve_partial:
                partial = bytes(self._buffer[:amount])
                self._buffer.clear()
                raise _PartialHttpReadError(
                    "chunked OFREP data read failed",
                    body=partial,
                ) from error
            raise
        result = bytes(self._buffer[:amount])
        del self._buffer[:amount]
        return result

    def _fill(self, *, max_amount: int) -> None:
        if max_amount <= 0:
            raise ValueError("chunked OFREP receive bound is exhausted")
        chunk = _receive_with_deadline(
            self._connection,
            amount=min(16 * 1024, max_amount),
            clock=self._clock,
            deadline=self._deadline,
        )
        if not chunk:
            raise ValueError("chunked OFREP body ended unexpectedly")
        self._buffer.extend(chunk)


def _read_chunked_http_body(
    connection: DirectSocket,
    *,
    buffered: bytes,
    clock: ReadbackClock,
    deadline: float,
) -> tuple[bytes, bool]:
    stream = _DeadlineSocketBuffer(
        connection,
        buffered=buffered,
        clock=clock,
        deadline=deadline,
    )
    body = bytearray()
    chunk_count = 0
    framing_bytes = 0
    try:
        while True:
            size_line = stream.read_line(max_bytes=_MAX_CHUNK_SIZE_LINE_BYTES)
            size_text = size_line.split(b";", 1)[0]
            framing_bytes += len(size_line) + 2
            if framing_bytes > _MAX_CHUNK_FRAMING_BYTES:
                raise ValueError("chunked OFREP framing exceeds the bound")
            if re.fullmatch(rb"[0-9A-Fa-f]+", size_text) is None:
                raise ValueError("chunked OFREP size is malformed")
            chunk_size = int(size_text, 16)
            if chunk_size == 0:
                trailer_bytes = 0
                trailer_count = 0
                while True:
                    trailer = stream.read_line(max_bytes=_MAX_CHUNK_TRAILER_LINE_BYTES)
                    trailer_bytes += len(trailer) + 2
                    trailer_count += 1
                    if (
                        trailer_bytes > _MAX_CHUNK_TRAILER_BYTES
                        or trailer_count > _MAX_CHUNK_TRAILER_COUNT
                    ):
                        raise ValueError("chunked OFREP trailers exceed the bound")
                    if not trailer:
                        break
                break
            chunk_count += 1
            if chunk_count > _MAX_CHUNK_COUNT:
                raise ValueError("chunked OFREP chunk count exceeds the bound")
            remaining = MAX_OFREP_RESPONSE_BYTES + 1 - len(body)
            if chunk_size > remaining:
                body.extend(
                    stream.read_exact(
                        remaining,
                        preserve_partial=True,
                    )
                )
                return bytes(body[:MAX_OFREP_RESPONSE_BYTES]), True
            chunk = stream.read_exact(
                chunk_size,
                preserve_partial=True,
            )
            body.extend(chunk)
            if stream.read_exact(2) != b"\r\n":
                raise ValueError("chunked OFREP delimiter is malformed")
            framing_bytes += 2
            if framing_bytes > _MAX_CHUNK_FRAMING_BYTES:
                raise ValueError("chunked OFREP framing exceeds the bound")
            if len(body) > MAX_OFREP_RESPONSE_BYTES:
                return bytes(body[:MAX_OFREP_RESPONSE_BYTES]), True
    except _PartialHttpReadError as error:
        raise _PartialHttpReadError(
            "chunked OFREP body read failed",
            body=bytes(body) + error.body,
            truncated=error.truncated,
        ) from error
    except (OSError, TimeoutError, ValueError) as error:
        raise _PartialHttpReadError(
            "chunked OFREP framing read failed",
            body=bytes(body),
        ) from error
    return bytes(body), False


def _build_ofrep_readback(
    *,
    status: int | None,
    body: bytes,
    truncated: bool,
    transport_failed: bool,
    clock: ReadbackClock,
) -> OfrepReadback:
    parsed_value: bool | None = None
    parsed_variant: str | None = None
    parsed_reason: str | None = None
    parse_error: Literal["INVALID_JSON", "INVALID_SCHEMA"] | None = None

    if not transport_failed and not truncated:
        try:
            payload = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError):
            parse_error = "INVALID_JSON"
        else:
            if (
                not isinstance(payload, dict)
                or not isinstance(payload.get("value"), bool)
                or not isinstance(payload.get("variant"), str)
                or not payload["variant"]
                or (
                    payload.get("reason") is not None
                    and not isinstance(payload.get("reason"), str)
                )
            ):
                parse_error = "INVALID_SCHEMA"
            else:
                parsed_value = payload["value"]
                parsed_variant = payload["variant"]
                parsed_reason = payload.get("reason")

    if transport_failed:
        error_code = "TRANSPORT_ERROR"
    elif truncated:
        error_code = "BODY_OVERSIZE"
    elif status is None or not 200 <= status < 300:
        error_code = "HTTP_STATUS"
    elif parse_error is not None:
        error_code = parse_error
    else:
        error_code = "NONE"

    return OfrepReadback(
        schema_version="phase0.ofrep-readback.v1",
        http_status=status,
        raw_response_body_b64=base64.b64encode(body).decode("ascii"),
        raw_body_truncated=truncated,
        parsed_value=parsed_value,
        parsed_variant=parsed_variant,
        parsed_reason=parsed_reason,
        received_at=clock.now(),
        received_monotonic=clock.monotonic(),
        content_sha256=sha256_bytes(body),
        error_code=error_code,
        request_metadata={
            "method": "POST",
            "content_type": "application/json",
            "request_body_sha256": sha256_bytes(b"{}"),
        },
    )


class _PhysicalObservation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    runtime_config_sha256: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    file_value: bool | None
    evaluation_value: bool | None
    evaluation_variant: str | None
    readback_attempt_id: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{32}$",
    )
    readback_sha256: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    readback_error_code: str | None


class _ReadbackEvidence(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["phase0.ofrep-readback-evidence.v1"]
    run_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    attempt_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    physical_flag_key: Literal["adFailure"]
    readback_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    readback: OfrepReadback


class _PendingControlIntent(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["phase0.control-intent.v1"]
    event_type: Literal["PENDING"]
    run_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    event_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    action: ScenarioAction
    before_state: ScenarioState
    target_state: ScenarioState
    mutation_state: Literal["UNKNOWN"]
    started_at: datetime
    started_monotonic: float = Field(ge=0)
    deadline_monotonic: float = Field(ge=0)
    runtime_config_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_safe_pending_window(self) -> "_PendingControlIntent":
        if (
            self.started_at.utcoffset() is None
            or self.started_at.utcoffset().total_seconds() != 0
        ):
            raise ValueError("pending intent start timestamp must be UTC")
        if self.deadline_monotonic <= self.started_monotonic:
            raise ValueError("pending intent deadline must follow start")
        if self.before_state is ScenarioState.UNKNOWN:
            raise ValueError("pending intent requires a known pre-state")
        if self.target_state is ScenarioState.UNKNOWN:
            raise ValueError("pending intent requires a known target")
        return self


class _ControlIntentResolution(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["phase0.control-intent-resolution.v1"]
    event_type: Literal["COMPLETED", "RECOVERY"]
    run_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    event_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    linked_intent_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    observed_state: ScenarioState
    resolution: Literal[
        "TARGET_CONFIRMED",
        "PRESTATE_CONFIRMED",
        "UNRESOLVED_UNKNOWN",
    ]
    recorded_at: datetime
    recorded_monotonic: float = Field(ge=0)

    @model_validator(mode="after")
    def require_utc_resolution(self) -> "_ControlIntentResolution":
        if (
            self.recorded_at.utcoffset() is None
            or self.recorded_at.utcoffset().total_seconds() != 0
        ):
            raise ValueError("intent resolution timestamp must be UTC")
        return self


class _HiddenControlEvent(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["phase0.hidden-control-event.v1"]
    run_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    control_event_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    record_status: Literal[
        "FINALIZED",
        "CLEANUP_FAILED",
        "INTERRUPTED",
        "PRELOCK_TIMEOUT",
    ]
    preparation_id: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{32}$",
    )
    transition_sequence: int | None = Field(default=None, ge=1)
    observation_basis: Literal[
        "LOCKED_OBSERVATION",
        "LOCKED_UNAVAILABLE",
        "PRELOCK_UNAVAILABLE",
    ]
    scenario_identity: Literal["adServiceFailure"]
    physical_flag_key: Literal["adFailure"]
    action: ScenarioAction
    before_logical_state: ScenarioState
    target_logical_state: ScenarioState
    before_physical_value: bool | None
    target_physical_value: bool
    expected_transition: str
    mutation_state: MutationState
    transition_succeeded: bool
    outcome: str
    reason_code: str
    started_at: str
    ended_at: str
    monotonic_duration_seconds: float = Field(ge=0)
    upstream_source_sha256: Literal[
        "bef4fa5da0ad8b1f64cc0d66fc66afaf7b9877c85895b78bf47d9a97577f9983"
    ]
    upstream_schema: Literal["https://flagd.dev/schema/v0/flags.json"]
    runtime_config_sha256: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    observed_file_value: bool | None
    observed_evaluation_value: bool | None
    observed_evaluation_variant: str | None
    readback_attempt_id: str | None
    readback_sha256: str | None
    readback_error_code: str | None

    @model_validator(mode="after")
    def require_linked_finalization_truth(self) -> "_HiddenControlEvent":
        prelock = self.record_status == "PRELOCK_TIMEOUT"
        if prelock != (self.observation_basis == "PRELOCK_UNAVAILABLE"):
            raise ValueError("pre-lock finalization observation basis is inconsistent")
        if prelock:
            if (
                self.preparation_id is not None
                or self.transition_sequence is not None
                or self.runtime_config_sha256 is not None
                or self.mutation_state is not MutationState.NOT_APPLIED
                or self.before_logical_state is not ScenarioState.UNKNOWN
                or self.transition_succeeded
            ):
                raise ValueError("pre-lock timeout contains unavailable locked truth")
        elif self.preparation_id is None or self.transition_sequence is None:
            raise ValueError("locked finalization lacks its preparation link")
        if self.record_status != "FINALIZED" and self.transition_succeeded:
            raise ValueError("non-finalized transition cannot claim success")
        return self


class _PreparedControlEvent(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["phase0.prepared-control-event.v1"]
    record_status: Literal["PREPARED"]
    run_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    preparation_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    transition_sequence: int = Field(ge=1)
    control_event_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    action: ScenarioAction
    before_logical_state: ScenarioState
    target_logical_state: ScenarioState
    mutation_state: MutationState
    observation_basis: Literal["LOCKED_OBSERVATION", "LOCKED_UNAVAILABLE"]
    runtime_config_sha256: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    observed_file_value: bool | None
    observed_evaluation_value: bool | None
    observed_evaluation_variant: str | None
    readback_attempt_id: str | None
    readback_sha256: str | None
    readback_error_code: str | None

    @model_validator(mode="after")
    def require_observation_basis(self) -> "_PreparedControlEvent":
        if self.observation_basis == "LOCKED_OBSERVATION":
            if self.runtime_config_sha256 is None:
                raise ValueError("locked observation lacks runtime configuration hash")
        elif any(
            value is not None
            for value in (
                self.runtime_config_sha256,
                self.observed_file_value,
                self.observed_evaluation_value,
                self.observed_evaluation_variant,
                self.readback_attempt_id,
                self.readback_sha256,
                self.readback_error_code,
            )
        ):
            raise ValueError("unavailable locked observation contains claimed truth")
        return self


class FlagdGroundTruthRuntime:
    """Run-bound physical adapter implementing the logical control protocol."""

    def __init__(
        self,
        *,
        capability: _DirectoryCapability,
        run_id: str,
        source_payload: dict[str, object],
        ofrep_client: OfrepClient,
        ofrep_endpoint: str,
    ) -> None:
        self._capability = capability
        self.run_id = run_id
        self._source_payload = source_payload
        self._ofrep_client = ofrep_client
        self._ofrep_endpoint = _validate_local_ofrep_endpoint(ofrep_endpoint)
        self.runtime_path = capability.root / FLAGD_CONFIG_RELATIVE_PATH
        self._transition_local = threading.local()

    @classmethod
    def bootstrap(
        cls,
        *,
        project_root: Path,
        artifacts_root: Path,
        run_id: str,
        ofrep_client: OfrepClient,
        ofrep_endpoint: str,
    ) -> "FlagdGroundTruthRuntime":
        _validate_run_id(run_id)
        source_bytes, source_payload = _load_verified_upstream_source(project_root)
        endpoint = _validate_local_ofrep_endpoint(ofrep_endpoint)
        capability = _DirectoryCapability(
            artifacts_root,
            "evaluator-only",
            run_id,
            zone="evaluator",
            allowed_top_level=_ALLOWED_TOP_LEVEL,
        )
        try:
            _require_private_directory(capability.root)
            runtime = cls(
                capability=capability,
                run_id=run_id,
                source_payload=source_payload,
                ofrep_client=ofrep_client,
                ofrep_endpoint=endpoint,
            )
            runtime._initialize_runtime(source_bytes)
            runtime._initialize_ground_truth()
            return runtime
        except BaseException:
            capability.close()
            raise

    @classmethod
    def open_existing(
        cls,
        *,
        project_root: Path,
        artifacts_root: Path,
        run_id: str,
        ofrep_client: OfrepClient,
        ofrep_endpoint: str,
    ) -> "FlagdGroundTruthRuntime":
        _validate_run_id(run_id)
        _source_bytes, source_payload = _load_verified_upstream_source(project_root)
        endpoint = _validate_local_ofrep_endpoint(ofrep_endpoint)
        _require_existing_runtime_paths(artifacts_root, run_id)
        capability = _DirectoryCapability(
            artifacts_root,
            "evaluator-only",
            run_id,
            zone="evaluator",
            allowed_top_level=_ALLOWED_TOP_LEVEL,
            create=False,
        )
        try:
            _require_private_directory(capability.root)
            runtime = cls(
                capability=capability,
                run_id=run_id,
                source_payload=source_payload,
                ofrep_client=ofrep_client,
                ofrep_endpoint=endpoint,
            )
            runtime._read_runtime_payload()
            ground_truth = _read_secure_file(
                capability,
                "scenario-ground-truth.json",
            )
            if json.loads(ground_truth) != runtime._ground_truth_payload():
                raise ValueError(
                    "evaluator ground truth conflicts with frozen contract"
                )
            return runtime
        except BaseException:
            capability.close()
            raise

    def close(self) -> None:
        self._capability.close()

    @contextmanager
    def transition_guard(
        self,
        *,
        timeout_seconds: float,
    ) -> Iterator[bool]:
        if timeout_seconds <= 0:
            yield False
            return
        deadline = time.monotonic() + timeout_seconds
        lock_key, lock_entry = _retain_thread_lock(self._capability.root)
        acquired_thread = False
        descriptor: int | None = None
        acquired_process = False
        body_error: BaseException | None = None
        cleanup_errors: list[BaseException] = []
        cleanup_interrupt: KeyboardInterrupt | SystemExit | None = None

        def capture_cleanup_error(error: BaseException) -> None:
            nonlocal cleanup_interrupt
            if isinstance(error, (KeyboardInterrupt, SystemExit)):
                if cleanup_interrupt is None:
                    cleanup_interrupt = error
                else:
                    cleanup_errors.append(error)
            else:
                cleanup_errors.append(error)

        try:
            acquired_thread = lock_entry.lock.acquire(timeout=timeout_seconds)
            if not acquired_thread:
                yield False
                return
            self._transition_local.observation = None
            descriptor = _open_secure_transition_lock(self._capability)
            while time.monotonic() < deadline:
                try:
                    fcntl.flock(
                        descriptor,
                        fcntl.LOCK_EX | fcntl.LOCK_NB,
                    )
                except BlockingIOError:
                    time.sleep(
                        min(
                            0.01,
                            max(0.0, deadline - time.monotonic()),
                        )
                    )
                else:
                    acquired_process = True
                    break
            try:
                yield acquired_process
            except BaseException as error:
                body_error = error
                raise
        finally:
            if descriptor is not None:
                if acquired_process:
                    try:
                        fcntl.flock(descriptor, fcntl.LOCK_UN)
                    except BaseException as error:
                        capture_cleanup_error(error)
                try:
                    os.close(descriptor)
                except BaseException as error:
                    capture_cleanup_error(error)
            try:
                if hasattr(self._transition_local, "observation"):
                    del self._transition_local.observation
            except BaseException as error:
                capture_cleanup_error(error)
            if acquired_thread:
                try:
                    lock_entry.lock.release()
                except BaseException as error:
                    capture_cleanup_error(error)
            try:
                _release_thread_lock(lock_key, lock_entry)
            except BaseException as error:
                capture_cleanup_error(error)
            if body_error is not None:
                if cleanup_interrupt is not None or cleanup_errors:
                    all_cleanup_errors = [
                        *([cleanup_interrupt] if cleanup_interrupt is not None else []),
                        *cleanup_errors,
                    ]
                    body_error.add_note(
                        "transition guard cleanup also failed: "
                        + "; ".join(str(error) for error in all_cleanup_errors)
                    )
            elif cleanup_interrupt is not None:
                raise cleanup_interrupt
            elif cleanup_errors:
                raise TransitionGuardCleanupError(
                    "transition guard cleanup failed"
                ) from cleanup_errors[0]

    def reconcile_pending_intent(
        self,
        *,
        timeout_seconds: float,
    ) -> ScenarioState | None:
        pending = self._unresolved_pending_intent()
        if pending is None:
            return None
        try:
            observed = self.observe_state(timeout_seconds=timeout_seconds)
        except _ObservationEvidencePersistenceError as error:
            raise PendingRecoveryEvidenceError(
                "pending recovery readback evidence could not be persisted",
                observed_state=error.observed_state,
            ) from error
        if observed is pending.target_state:
            resolution = "TARGET_CONFIRMED"
        elif observed is pending.before_state:
            resolution = "PRESTATE_CONFIRMED"
        else:
            resolution = "UNRESOLVED_UNKNOWN"
        try:
            self._append_intent_record(
                _ControlIntentResolution(
                    schema_version="phase0.control-intent-resolution.v1",
                    event_type="RECOVERY",
                    run_id=self.run_id,
                    event_id=secrets.token_hex(16),
                    linked_intent_id=pending.event_id,
                    observed_state=observed,
                    resolution=resolution,
                    recorded_at=datetime.now(UTC),
                    recorded_monotonic=time.monotonic(),
                )
            )
        except EvidencePersistenceError as error:
            raise PendingRecoveryEvidenceError(
                "pending recovery resolution could not be persisted",
                observed_state=observed,
            ) from error
        return observed

    def record_pending_intent(
        self,
        *,
        action: ScenarioAction,
        before_state: ScenarioState,
        target_state: ScenarioState,
        started_at: datetime,
        started_monotonic: float,
        deadline_monotonic: float,
    ) -> str:
        if self._unresolved_pending_intent() is not None:
            raise EvidencePersistenceError(
                "unresolved control intent must be reconciled before mutation"
            )
        current = _read_secure_file(
            self._capability,
            FLAGD_CONFIG_RELATIVE_PATH,
        )
        intent_id = secrets.token_hex(16)
        self._append_intent_record(
            _PendingControlIntent(
                schema_version="phase0.control-intent.v1",
                event_type="PENDING",
                run_id=self.run_id,
                event_id=intent_id,
                action=action,
                before_state=before_state,
                target_state=target_state,
                mutation_state="UNKNOWN",
                started_at=started_at,
                started_monotonic=started_monotonic,
                deadline_monotonic=deadline_monotonic,
                runtime_config_sha256=sha256_bytes(current),
            )
        )
        return intent_id

    def complete_pending_intent(
        self,
        intent_id: str,
        *,
        observed_state: ScenarioState,
    ) -> None:
        pending = self._unresolved_pending_intent()
        if pending is None or pending.event_id != intent_id:
            raise EvidencePersistenceError(
                "pending control intent is unavailable for completion"
            )
        if observed_state is not pending.target_state:
            raise EvidencePersistenceError(
                "control intent completion lacks target confirmation"
            )
        self._append_intent_record(
            _ControlIntentResolution(
                schema_version="phase0.control-intent-resolution.v1",
                event_type="COMPLETED",
                run_id=self.run_id,
                event_id=secrets.token_hex(16),
                linked_intent_id=intent_id,
                observed_state=observed_state,
                resolution="TARGET_CONFIRMED",
                recorded_at=datetime.now(UTC),
                recorded_monotonic=time.monotonic(),
            )
        )

    def _append_intent_record(
        self,
        record: _PendingControlIntent | _ControlIntentResolution,
    ) -> None:
        try:
            _append_jsonl(
                self._capability,
                "control-intents.jsonl",
                record,
                zone="evaluator",
                allowed_top_level=_ALLOWED_TOP_LEVEL,
            )
        except (OSError, ValueError) as error:
            raise EvidencePersistenceError(
                "control intent evidence could not be persisted"
            ) from error

    def _unresolved_pending_intent(
        self,
    ) -> _PendingControlIntent | None:
        try:
            content = _read_secure_file(
                self._capability,
                "control-intents.jsonl",
            )
        except FileNotFoundError:
            return None
        try:
            pending: dict[str, _PendingControlIntent] = {}
            for line in content.splitlines():
                payload = json.loads(line)
                if not isinstance(payload, dict):
                    raise ValueError("control intent record must be an object")
                if payload.get("event_type") == "PENDING":
                    record = _PendingControlIntent.model_validate(payload)
                    if record.event_id in pending:
                        raise ValueError("duplicate pending control intent")
                    pending[record.event_id] = record
                    continue
                resolution = _ControlIntentResolution.model_validate(payload)
                if resolution.linked_intent_id not in pending:
                    raise ValueError("control intent resolution has no pending link")
                if resolution.resolution != "UNRESOLVED_UNKNOWN":
                    del pending[resolution.linked_intent_id]
            if len(pending) > 1:
                raise ValueError("multiple unresolved control intents")
            return next(iter(pending.values()), None)
        except (json.JSONDecodeError, ValueError) as error:
            raise EvidencePersistenceError(
                "control intent evidence is malformed"
            ) from error

    def observe_state(self, *, timeout_seconds: float) -> ScenarioState:
        try:
            runtime_bytes, payload = self._read_runtime_snapshot()
            file_value = _payload_flag_value(payload)
        except (OSError, ValueError, json.JSONDecodeError):
            self._transition_local.observation = _PhysicalObservation(
                runtime_config_sha256=None,
                file_value=None,
                evaluation_value=None,
                evaluation_variant=None,
                readback_attempt_id=None,
                readback_sha256=None,
                readback_error_code=None,
            )
            return ScenarioState.UNKNOWN
        try:
            readback = self._ofrep_client.evaluate(
                endpoint=self._ofrep_endpoint,
                flag_key=_PHYSICAL_FLAG_KEY,
                timeout_seconds=timeout_seconds,
            )
        except Exception:
            readback = _build_ofrep_readback(
                status=None,
                body=b"",
                truncated=False,
                transport_failed=True,
                clock=SystemReadbackClock(),
            )
        observation = _PhysicalObservation(
            runtime_config_sha256=sha256_bytes(runtime_bytes),
            file_value=file_value,
            evaluation_value=readback.parsed_value,
            evaluation_variant=readback.parsed_variant,
            readback_attempt_id=None,
            readback_sha256=None,
            readback_error_code=readback.error_code,
        )
        expected_variant = _FAULT_VARIANT if file_value else _BASELINE_VARIANT
        if (
            readback.error_code != "NONE"
            or readback.parsed_value != file_value
            or readback.parsed_variant != expected_variant
        ):
            observed_state = ScenarioState.UNKNOWN
        else:
            observed_state = (
                ScenarioState.INJECTED if file_value else ScenarioState.BASELINE
            )
        try:
            attempt_id, readback_sha256 = self._persist_readback(readback)
        except EvidencePersistenceError as error:
            self._transition_local.observation = observation
            raise _ObservationEvidencePersistenceError(
                observed_state=observed_state
            ) from error
        self._transition_local.observation = observation.model_copy(
            update={
                "readback_attempt_id": attempt_id,
                "readback_sha256": readback_sha256,
            }
        )
        return observed_state

    def apply_state(self, target: ScenarioState) -> MutationState:
        if target not in {ScenarioState.BASELINE, ScenarioState.INJECTED}:
            raise ValueError("physical control target is not allowlisted")
        payload = self._read_runtime_payload()
        flags = payload["flags"]
        assert isinstance(flags, dict)
        flag = flags[_PHYSICAL_FLAG_KEY]
        assert isinstance(flag, dict)
        flag["defaultVariant"] = (
            _FAULT_VARIANT if target is ScenarioState.INJECTED else _BASELINE_VARIANT
        )
        _validate_runtime_payload(payload, self._source_payload)
        _atomic_replace(
            self._capability,
            FLAGD_CONFIG_RELATIVE_PATH,
            canonical_json_bytes(payload),
        )
        return MutationState.APPLIED

    def prepare_transition(
        self,
        execution: TransitionExecution,
    ) -> TransitionPreparation:
        observation = getattr(
            self._transition_local,
            "observation",
            None,
        )
        observation_basis: Literal["LOCKED_OBSERVATION", "LOCKED_UNAVAILABLE"]
        if observation is None:
            observation = _PhysicalObservation(
                runtime_config_sha256=None,
                file_value=None,
                evaluation_value=None,
                evaluation_variant=None,
                readback_attempt_id=None,
                readback_sha256=None,
                readback_error_code=None,
            )
            observation_basis = "LOCKED_UNAVAILABLE"
        else:
            observation_basis = "LOCKED_OBSERVATION"
        preparation = TransitionPreparation(
            schema_version="phase0.transition-preparation.v1",
            preparation_id=secrets.token_hex(16),
            transition_sequence=self._next_transition_sequence(),
        )
        prepared = _PreparedControlEvent(
            schema_version="phase0.prepared-control-event.v1",
            record_status="PREPARED",
            run_id=self.run_id,
            preparation_id=preparation.preparation_id,
            transition_sequence=preparation.transition_sequence,
            control_event_id=execution.observer_event.control_event_id,
            action=execution.action,
            before_logical_state=execution.before_state,
            target_logical_state=execution.target_state,
            mutation_state=execution.mutation_state,
            observation_basis=observation_basis,
            runtime_config_sha256=observation.runtime_config_sha256,
            observed_file_value=observation.file_value,
            observed_evaluation_value=observation.evaluation_value,
            observed_evaluation_variant=observation.evaluation_variant,
            readback_attempt_id=observation.readback_attempt_id,
            readback_sha256=observation.readback_sha256,
            readback_error_code=observation.readback_error_code,
        )
        try:
            _append_jsonl(
                self._capability,
                "control-prepared.jsonl",
                prepared,
                zone="evaluator",
                allowed_top_level=_ALLOWED_TOP_LEVEL,
            )
        except (OSError, ValueError) as error:
            raise EvidencePersistenceError(
                "prepared control evidence could not be persisted"
            ) from error
        return preparation

    def finalize_transition(
        self,
        execution: TransitionExecution,
        *,
        preparation: TransitionPreparation | None,
        record_status: Literal[
            "FINALIZED",
            "CLEANUP_FAILED",
            "INTERRUPTED",
            "PRELOCK_TIMEOUT",
        ],
    ) -> None:
        if record_status == "PRELOCK_TIMEOUT":
            if preparation is not None:
                raise ValueError("pre-lock timeout cannot link a locked preparation")
            prepared: _PreparedControlEvent | None = None
            observation_basis = "PRELOCK_UNAVAILABLE"
            observation = _PhysicalObservation(
                runtime_config_sha256=None,
                file_value=None,
                evaluation_value=None,
                evaluation_variant=None,
                readback_attempt_id=None,
                readback_sha256=None,
                readback_error_code=None,
            )
        else:
            if preparation is None:
                raise ValueError("locked finalization requires a preparation")
            prepared = self._load_prepared_transition(preparation)
            if (
                prepared.control_event_id != execution.observer_event.control_event_id
                or prepared.action is not execution.action
                or prepared.before_logical_state is not execution.before_state
                or prepared.target_logical_state is not execution.target_state
                or prepared.mutation_state is not execution.mutation_state
            ):
                raise EvidencePersistenceError(
                    "terminal transition conflicts with locked preparation"
                )
            observation_basis = prepared.observation_basis
            observation = _PhysicalObservation(
                runtime_config_sha256=prepared.runtime_config_sha256,
                file_value=prepared.observed_file_value,
                evaluation_value=prepared.observed_evaluation_value,
                evaluation_variant=prepared.observed_evaluation_variant,
                readback_attempt_id=prepared.readback_attempt_id,
                readback_sha256=prepared.readback_sha256,
                readback_error_code=prepared.readback_error_code,
            )
        before_value = {
            ScenarioState.BASELINE: False,
            ScenarioState.INJECTED: True,
            ScenarioState.UNKNOWN: None,
        }[execution.before_state]
        target_value = execution.target_state is ScenarioState.INJECTED
        hidden = _HiddenControlEvent(
            schema_version="phase0.hidden-control-event.v1",
            run_id=self.run_id,
            control_event_id=execution.observer_event.control_event_id,
            record_status=record_status,
            preparation_id=(
                preparation.preparation_id if preparation is not None else None
            ),
            transition_sequence=(
                preparation.transition_sequence if preparation is not None else None
            ),
            observation_basis=observation_basis,
            scenario_identity=_SCENARIO_IDENTITY,
            physical_flag_key=_PHYSICAL_FLAG_KEY,
            action=execution.action,
            before_logical_state=execution.before_state,
            target_logical_state=execution.target_state,
            before_physical_value=before_value,
            target_physical_value=target_value,
            expected_transition=_expected_transition(execution),
            mutation_state=execution.mutation_state,
            transition_succeeded=(execution.terminal_result.outcome.value == "SUCCESS"),
            outcome=execution.terminal_result.outcome.value,
            reason_code=execution.terminal_result.reason_code,
            started_at=execution.observer_event.started_at.isoformat(),
            ended_at=execution.observer_event.ended_at.isoformat(),
            monotonic_duration_seconds=(
                execution.observer_event.monotonic_duration_seconds
            ),
            upstream_source_sha256=UPSTREAM_FLAGD_CONFIG_SHA256,
            upstream_schema=UPSTREAM_FLAGD_SCHEMA,
            runtime_config_sha256=observation.runtime_config_sha256,
            observed_file_value=observation.file_value,
            observed_evaluation_value=observation.evaluation_value,
            observed_evaluation_variant=observation.evaluation_variant,
            readback_attempt_id=observation.readback_attempt_id,
            readback_sha256=observation.readback_sha256,
            readback_error_code=observation.readback_error_code,
        )
        try:
            _append_jsonl(
                self._capability,
                "control-events.jsonl",
                hidden,
                zone="evaluator",
                allowed_top_level=_ALLOWED_TOP_LEVEL,
            )
        except (OSError, ValueError) as error:
            raise EvidencePersistenceError(
                "terminal control evidence could not be persisted"
            ) from error

    def _next_transition_sequence(self) -> int:
        try:
            content = _read_secure_file(
                self._capability,
                "control-prepared.jsonl",
            )
        except FileNotFoundError:
            return 1
        try:
            records = [
                _PreparedControlEvent.model_validate_json(line)
                for line in content.splitlines()
            ]
        except ValueError as error:
            raise EvidencePersistenceError(
                "prepared control evidence is malformed"
            ) from error
        sequences = [record.transition_sequence for record in records]
        if sequences != list(range(1, len(sequences) + 1)):
            raise EvidencePersistenceError(
                "prepared control sequence is non-contiguous"
            )
        return len(sequences) + 1

    def _load_prepared_transition(
        self,
        preparation: TransitionPreparation,
    ) -> _PreparedControlEvent:
        try:
            content = _read_secure_file(
                self._capability,
                "control-prepared.jsonl",
            )
            matches = [
                _PreparedControlEvent.model_validate_json(line)
                for line in content.splitlines()
                if json.loads(line).get("preparation_id") == preparation.preparation_id
            ]
        except (FileNotFoundError, json.JSONDecodeError, ValueError) as error:
            raise EvidencePersistenceError(
                "prepared control evidence is unavailable"
            ) from error
        if len(matches) != 1:
            raise EvidencePersistenceError(
                "prepared control finalization link is ambiguous"
            )
        prepared = matches[0]
        if prepared.transition_sequence != preparation.transition_sequence:
            raise EvidencePersistenceError(
                "prepared control sequence link is inconsistent"
            )
        return prepared

    def _persist_readback(
        self,
        readback: OfrepReadback,
    ) -> tuple[str, str]:
        attempt_id = secrets.token_hex(16)
        readback_sha256 = sha256_bytes(
            canonical_json_bytes(readback.model_dump(mode="json"))
        )
        evidence = _ReadbackEvidence(
            schema_version="phase0.ofrep-readback-evidence.v1",
            run_id=self.run_id,
            attempt_id=attempt_id,
            physical_flag_key=_PHYSICAL_FLAG_KEY,
            readback_sha256=readback_sha256,
            readback=readback,
        )
        try:
            _append_jsonl(
                self._capability,
                "readbacks/ofrep-attempts.jsonl",
                evidence,
                zone="evaluator",
                allowed_top_level=_ALLOWED_TOP_LEVEL,
            )
        except (OSError, ValueError) as error:
            raise EvidencePersistenceError(
                "raw OFREP readback evidence could not be persisted"
            ) from error
        return attempt_id, readback_sha256

    def record_emergency_diagnostic(
        self,
        execution: TransitionExecution,
        *,
        failure_stage: str,
    ) -> None:
        _append_jsonl(
            self._capability,
            "emergency/evidence-failures.jsonl",
            {
                "schema_version": "phase0.emergency-control-evidence.v1",
                "run_id": self.run_id,
                "control_event_id": execution.observer_event.control_event_id,
                "failure_stage": failure_stage,
                "mutation_state": execution.mutation_state.value,
                "outcome": execution.terminal_result.outcome.value,
                "reason_code": execution.terminal_result.reason_code,
                "recorded_at": execution.observer_event.ended_at.isoformat(),
            },
            zone="evaluator",
            allowed_top_level=_ALLOWED_TOP_LEVEL,
        )

    def _initialize_runtime(self, source_bytes: bytes) -> None:
        try:
            _write_immutable_bytes(
                self._capability,
                FLAGD_CONFIG_RELATIVE_PATH,
                source_bytes,
                zone="evaluator",
                allowed_top_level=_ALLOWED_TOP_LEVEL,
            )
        except FileExistsError:
            payload = self._read_runtime_payload()
            _validate_runtime_payload(payload, self._source_payload)

    def _initialize_ground_truth(self) -> None:
        value = self._ground_truth_payload()
        content = canonical_json_bytes(value)
        try:
            _write_immutable_bytes(
                self._capability,
                "scenario-ground-truth.json",
                content,
                zone="evaluator",
                allowed_top_level=_ALLOWED_TOP_LEVEL,
            )
        except FileExistsError:
            if (
                _read_secure_file(
                    self._capability,
                    "scenario-ground-truth.json",
                )
                != content
            ):
                raise ValueError(
                    "evaluator ground truth conflicts with frozen contract"
                )

    def _ground_truth_payload(self) -> dict[str, object]:
        return {
            "schema_version": "phase0.scenario-ground-truth.v1",
            "run_id": self.run_id,
            "scenario_identity": _SCENARIO_IDENTITY,
            "physical_flag_key": _PHYSICAL_FLAG_KEY,
            "logical_states": {
                ScenarioState.BASELINE.value: {
                    "variant": _BASELINE_VARIANT,
                    "value": False,
                },
                ScenarioState.INJECTED.value: {
                    "variant": _FAULT_VARIANT,
                    "value": True,
                },
            },
            "expected_transitions": {
                ScenarioAction.INJECT.value: "BASELINE_TO_INJECTED",
                ScenarioAction.RESET.value: "INJECTED_TO_BASELINE",
            },
            "expected_fault_mechanism": "Ad service GetAds controlled failure",
            "upstream_source_sha256": UPSTREAM_FLAGD_CONFIG_SHA256,
            "upstream_schema": UPSTREAM_FLAGD_SCHEMA,
        }

    def _read_runtime_payload(self) -> dict[str, object]:
        _content, payload = self._read_runtime_snapshot()
        return payload

    def _read_runtime_snapshot(self) -> tuple[bytes, dict[str, object]]:
        content = _read_secure_file(
            self._capability,
            FLAGD_CONFIG_RELATIVE_PATH,
        )
        payload = json.loads(content)
        if not isinstance(payload, dict):
            raise ValueError("runtime flagd configuration must be an object")
        _validate_runtime_payload(payload, self._source_payload)
        return content, payload


def _load_verified_upstream_source(
    project_root: Path,
) -> tuple[bytes, dict[str, object]]:
    source_path = Path(project_root).resolve() / UPSTREAM_FLAGD_CONFIG_PATH
    try:
        content = source_path.read_bytes()
    except OSError as error:
        raise FlagdSourceContractError(
            "upstream flagd configuration is unavailable"
        ) from error
    if sha256_bytes(content) != UPSTREAM_FLAGD_CONFIG_SHA256:
        raise FlagdSourceContractError("upstream flagd configuration hash mismatch")
    try:
        payload = json.loads(content)
    except json.JSONDecodeError as error:
        raise FlagdSourceContractError(
            "upstream flagd configuration is malformed"
        ) from error
    if not isinstance(payload, dict) or payload.get("$schema") != UPSTREAM_FLAGD_SCHEMA:
        raise FlagdSourceContractError("upstream flagd configuration schema mismatch")
    _validate_source_flag_contract(payload)
    return content, payload


def _validate_source_flag_contract(payload: dict[str, object]) -> None:
    flags = payload.get("flags")
    if not isinstance(flags, dict):
        raise FlagdSourceContractError("upstream flagd flags object is missing")
    if flags.get(_PHYSICAL_FLAG_KEY) != {
        "defaultVariant": _BASELINE_VARIANT,
        "description": "Fail ad service",
        "state": "ENABLED",
        "variants": {
            _BASELINE_VARIANT: False,
            _FAULT_VARIANT: True,
        },
    }:
        raise FlagdSourceContractError("upstream Ad failure flag contract mismatch")


def _validate_runtime_payload(
    payload: dict[str, object],
    source_payload: dict[str, object],
) -> None:
    flags = payload.get("flags")
    if not isinstance(flags, dict):
        raise ValueError("runtime flagd flags object is missing")
    flag = flags.get(_PHYSICAL_FLAG_KEY)
    if not isinstance(flag, dict):
        raise ValueError("runtime Ad failure flag is missing")
    variant = flag.get("defaultVariant")
    if variant not in {_BASELINE_VARIANT, _FAULT_VARIANT}:
        raise ValueError("runtime Ad failure variant is not allowlisted")
    expected = deepcopy(source_payload)
    expected_flags = expected["flags"]
    assert isinstance(expected_flags, dict)
    expected_flag = expected_flags[_PHYSICAL_FLAG_KEY]
    assert isinstance(expected_flag, dict)
    expected_flag["defaultVariant"] = variant
    if payload != expected:
        raise ValueError("runtime flagd configuration drifted from frozen source")


def _payload_flag_value(payload: dict[str, object]) -> bool:
    flags = payload["flags"]
    assert isinstance(flags, dict)
    flag = flags[_PHYSICAL_FLAG_KEY]
    assert isinstance(flag, dict)
    return flag["defaultVariant"] == _FAULT_VARIANT


def _read_secure_file(
    capability: _DirectoryCapability,
    relative_path: str,
) -> bytes:
    parent_descriptor, target_name, _target = capability.prepare_target(relative_path)
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    if hasattr(os, "O_NONBLOCK"):
        flags |= os.O_NONBLOCK
    descriptor: int | None = None
    try:
        descriptor = os.open(target_name, flags, dir_fd=parent_descriptor)
        metadata = os.fstat(descriptor)
        _validate_file_metadata(metadata, zone="evaluator")
        if stat.S_IMODE(metadata.st_mode) != 0o600:
            raise ValueError("evaluator control file mode is not private")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 64 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        capability.assert_intact()
        return b"".join(chunks)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        os.close(parent_descriptor)


def _retain_thread_lock(root: Path) -> tuple[str, _ThreadLockEntry]:
    key = str(root.absolute())
    with _THREAD_LOCKS_GUARD:
        entry = _THREAD_LOCKS.get(key)
        if entry is None:
            entry = _ThreadLockEntry()
            _THREAD_LOCKS[key] = entry
        entry.users += 1
        return key, entry


def _release_thread_lock(key: str, entry: _ThreadLockEntry) -> None:
    with _THREAD_LOCKS_GUARD:
        current = _THREAD_LOCKS.get(key)
        if current is not entry or entry.users <= 0:
            raise RuntimeError("thread lock registry ownership is inconsistent")
        entry.users -= 1
        if entry.users == 0:
            if entry.lock.locked():
                raise RuntimeError("thread lock registry released a held lock")
            del _THREAD_LOCKS[key]


def _open_secure_transition_lock(
    capability: _DirectoryCapability,
) -> int:
    parent_descriptor, target_name, _target = capability.prepare_target(
        _TRANSITION_LOCK_RELATIVE_PATH
    )
    flags = os.O_RDWR | os.O_CREAT
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor: int | None = None
    try:
        descriptor = os.open(
            target_name,
            flags,
            0o600,
            dir_fd=parent_descriptor,
        )
        metadata = os.fstat(descriptor)
        _validate_file_metadata(metadata, zone="evaluator")
        if stat.S_IMODE(metadata.st_mode) != 0o600:
            raise ValueError("transition lock mode is not private")
        capability.assert_intact()
        return descriptor
    except BaseException:
        if descriptor is not None:
            os.close(descriptor)
        raise
    finally:
        os.close(parent_descriptor)


def _atomic_replace(
    capability: _DirectoryCapability,
    relative_path: str,
    content: bytes,
) -> None:
    try:
        _atomic_replace_impl(capability, relative_path, content)
    except (OSError, ValueError) as error:
        raise ControlMutationUncertain(
            "atomic physical control mutation is uncertain"
        ) from error


def _atomic_replace_impl(
    capability: _DirectoryCapability,
    relative_path: str,
    content: bytes,
) -> None:
    parent_descriptor, target_name, _target = capability.prepare_target(relative_path)
    temporary_name = f".{secrets.token_hex(16)}.tmp"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    temporary_descriptor: int | None = None
    try:
        existing = os.stat(
            target_name,
            dir_fd=parent_descriptor,
            follow_symlinks=False,
        )
        _validate_file_metadata(existing, zone="evaluator")
        if stat.S_IMODE(existing.st_mode) != 0o600:
            raise ValueError("evaluator control file mode is not private")
        temporary_descriptor = os.open(
            temporary_name,
            flags,
            0o600,
            dir_fd=parent_descriptor,
        )
        _write_all(temporary_descriptor, content)
        os.fsync(temporary_descriptor)
        os.close(temporary_descriptor)
        temporary_descriptor = None
        os.replace(
            temporary_name,
            target_name,
            src_dir_fd=parent_descriptor,
            dst_dir_fd=parent_descriptor,
        )
        replaced = os.stat(
            target_name,
            dir_fd=parent_descriptor,
            follow_symlinks=False,
        )
        _validate_file_metadata(replaced, zone="evaluator")
        if stat.S_IMODE(replaced.st_mode) != 0o600:
            raise ValueError("replacement control file mode is not private")
        os.fsync(parent_descriptor)
        capability.assert_intact()
    finally:
        if temporary_descriptor is not None:
            os.close(temporary_descriptor)
        try:
            os.unlink(temporary_name, dir_fd=parent_descriptor)
        except FileNotFoundError:
            pass
        os.close(parent_descriptor)


def _validate_local_ofrep_endpoint(endpoint: str) -> str:
    parsed = urlsplit(endpoint)
    try:
        port = parsed.port
    except ValueError as error:
        raise ValueError(
            "OFREP endpoint must be an exact literal loopback HTTP origin"
        ) from error
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"127.0.0.1", "::1"}
        or port is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("OFREP endpoint must be an exact literal loopback HTTP origin")
    return endpoint.rstrip("/")


def _expected_transition(execution: TransitionExecution) -> str:
    if execution.action is ScenarioAction.INJECT:
        return (
            "INJECTED_CONFIRMED"
            if execution.before_state is ScenarioState.INJECTED
            else "BASELINE_TO_INJECTED"
        )
    return (
        "BASELINE_CONFIRMED"
        if execution.before_state is ScenarioState.BASELINE
        else "INJECTED_TO_BASELINE"
    )


def _require_private_directory(path: Path) -> None:
    metadata = os.lstat(path)
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        raise ValueError("evaluator capability directory is not private")


def _validate_run_id(run_id: str) -> None:
    if re.fullmatch(r"[0-9a-f]{32}", run_id) is None:
        raise ValueError("run_id is not an opaque path-safe identifier")


def _require_existing_runtime_paths(artifacts_root: Path, run_id: str) -> None:
    base = Path(artifacts_root)
    required = (
        base,
        base / "evaluator-only",
        base / "evaluator-only" / run_id,
        base / "evaluator-only" / run_id / "control",
        base / "evaluator-only" / run_id / FLAGD_CONFIG_RELATIVE_PATH,
        base / "evaluator-only" / run_id / "scenario-ground-truth.json",
    )
    try:
        for path in required:
            os.lstat(path)
    except FileNotFoundError as error:
        raise FlagdRuntimeUnavailable(
            "run-scoped flagd control runtime is unavailable"
        ) from error


def prepare_flagd_runtime(
    *,
    project_root: Path,
    artifacts_root: Path,
    run_id: str,
) -> Path:
    """Create or validate the run file required before Compose starts flagd."""
    runtime = FlagdGroundTruthRuntime.bootstrap(
        project_root=project_root,
        artifacts_root=artifacts_root,
        run_id=run_id,
        ofrep_client=_PreparationOnlyOfrepClient(),
        ofrep_endpoint="http://127.0.0.1:1",
    )
    try:
        return runtime.runtime_path
    finally:
        runtime.close()
