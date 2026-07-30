from __future__ import annotations

import ast
import base64
import hashlib
import json
import os
import socket
import stat
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from ecomsre.evidence.store import ObserverEvidenceStore
from ecomsre.scenarios.ad_service_failure import ObserverControlEvent
from ecomsre.scenarios.ground_truth import (
    FLAGD_CONFIG_RELATIVE_PATH,
    UPSTREAM_FLAGD_CONFIG_SHA256,
    UPSTREAM_FLAGD_SCHEMA,
    FlagdGroundTruthRuntime,
    HttpOfrepClient,
    MAX_OFREP_RESPONSE_BYTES,
)
from ecomsre.scenarios import ground_truth


ROOT = Path(__file__).resolve().parents[2]
RUN_ID = "a" * 32
UPSTREAM_CONFIG = (
    ROOT / "third_party" / "opentelemetry-demo" / "src" / "flagd" / "demo.flagd.json"
)


class NoopOfrepClient:
    def evaluate(
        self,
        *,
        endpoint: str,
        flag_key: str,
        timeout_seconds: float,
    ):
        raise AssertionError("bootstrap must not make an OFREP request")


class FixtureReadbackClock:
    def __init__(self) -> None:
        self.elapsed = 0.0
        self.started_at = datetime(2026, 7, 30, 8, 0, tzinfo=UTC)

    def now(self) -> datetime:
        return self.started_at + timedelta(seconds=self.elapsed)

    def monotonic(self) -> float:
        return self.elapsed


class FixtureSocket:
    def __init__(
        self,
        *,
        response: bytes,
        clock: FixtureReadbackClock,
        chunk_sizes: list[int] | None = None,
        delays: list[float] | None = None,
    ) -> None:
        self._response = response
        self._clock = clock
        self._chunk_sizes = list(chunk_sizes or [])
        self._delays = list(delays or [])
        self.timeout: float | None = None
        self.timeouts: list[float] = []
        self.address = None
        self.sent = b""
        self.closed = False
        self.received_bytes = 0

    def settimeout(self, timeout: float) -> None:
        self.timeout = timeout
        self.timeouts.append(timeout)

    def connect(self, address) -> None:
        self.address = address

    def sendall(self, content: bytes) -> None:
        self.sent += content

    def recv(self, amount: int) -> bytes:
        delay = self._delays.pop(0) if self._delays else 0.0
        assert self.timeout is not None
        if delay > self.timeout:
            self._clock.elapsed += self.timeout
            raise TimeoutError("fixture total deadline")
        self._clock.elapsed += delay
        if not self._response:
            return b""
        configured = self._chunk_sizes.pop(0) if self._chunk_sizes else amount
        size = min(amount, configured)
        result, self._response = self._response[:size], self._response[size:]
        self.received_bytes += len(result)
        return result

    def close(self) -> None:
        self.closed = True


def _http_response(
    status: int,
    body: bytes,
    *,
    headers: tuple[tuple[str, str], ...] = (),
) -> bytes:
    lines = [
        f"HTTP/1.1 {status} Fixture",
        f"Content-Length: {len(body)}",
        "Connection: close",
        *(f"{name}: {value}" for name, value in headers),
        "",
        "",
    ]
    return "\r\n".join(lines).encode("ascii") + body


def _direct_fixture_client(
    response: bytes,
    *,
    clock: FixtureReadbackClock | None = None,
    chunk_sizes: list[int] | None = None,
    delays: list[float] | None = None,
) -> tuple[HttpOfrepClient, FixtureSocket, FixtureReadbackClock]:
    active_clock = clock or FixtureReadbackClock()
    fixture_socket = FixtureSocket(
        response=response,
        clock=active_clock,
        chunk_sizes=chunk_sizes,
        delays=delays,
    )

    def factory(family: int, kind: int):
        assert family in {socket.AF_INET, socket.AF_INET6}
        assert kind == socket.SOCK_STREAM
        return fixture_socket

    return (
        HttpOfrepClient(
            clock=active_clock,
            socket_factory=factory,
        ),
        fixture_socket,
        active_clock,
    )


def _all_text(value: object) -> str:
    if isinstance(value, dict):
        return " ".join(
            [
                *(str(key) for key in value),
                *(_all_text(item) for item in value.values()),
            ]
        )
    if isinstance(value, (list, tuple)):
        return " ".join(_all_text(item) for item in value)
    return str(value)


def test_pinned_upstream_flag_contract_is_frozen_from_real_300_files() -> None:
    content = UPSTREAM_CONFIG.read_bytes()
    payload = json.loads(content)
    physical_flag = payload["flags"]["adFailure"]

    assert hashlib.sha256(content).hexdigest() == UPSTREAM_FLAGD_CONFIG_SHA256
    assert payload["$schema"] == UPSTREAM_FLAGD_SCHEMA
    assert physical_flag == {
        "defaultVariant": "off",
        "description": "Fail ad service",
        "state": "ENABLED",
        "variants": {"off": False, "on": True},
    }

    compose = (ROOT / "third_party" / "opentelemetry-demo" / "compose.yaml").read_text(
        encoding="utf-8"
    )
    environment = (ROOT / "third_party" / "opentelemetry-demo" / ".env").read_text(
        encoding="utf-8"
    )
    load_generator = (
        ROOT
        / "third_party"
        / "opentelemetry-demo"
        / "src"
        / "load-generator"
        / "entrypoint.sh"
    ).read_text(encoding="utf-8")

    assert "file:./etc/flagd/demo.flagd.json" in compose
    assert "./src/flagd:/etc/flagd" in compose
    assert "./src/flagd:/app/data" in compose
    assert "FLAGD_PORT=8013" in environment
    assert "FLAGD_OFREP_PORT=8016" in environment
    assert "FLAGD_UI_PORT=4000" in environment
    assert "/ofrep/v1/evaluate/flags/$1" in load_generator
    assert "--post-data='{}'" in load_generator


def test_upstream_control_fixture_hashes_every_selected_fact_source() -> None:
    contract = json.loads(
        (ROOT / "config" / "phase0" / "flagd-upstream-contract.json").read_text(
            encoding="utf-8"
        )
    )

    assert contract["schema_version"] == "phase0.flagd-upstream-contract.v1"
    assert contract["upstream_commit"] == ("1755859a9de82c2e5e225be68abc401a5ebf2b4f")
    assert contract["upstream_tag"] == "3.0.0"
    for relative_path, expected_sha256 in contract["files"].items():
        assert (
            hashlib.sha256((ROOT / relative_path).read_bytes()).hexdigest()
            == expected_sha256
        )
    assert contract["flagd"]["command"] == [
        "start",
        "--uri",
        "file:./etc/flagd/demo.flagd.json",
    ]
    assert contract["flagd"]["upstream_mount"] == "./src/flagd:/etc/flagd"
    assert contract["flagd"]["grpc_port"] == 8013
    assert contract["flagd"]["ofrep_port"] == 8016
    assert contract["flagd_ui"]["selected_for_control"] is False
    assert contract["scenario_mapping"]["physical_flag_key"] == "adFailure"
    assert contract["readback"] == {
        "method": "POST",
        "path_template": "/ofrep/v1/evaluate/flags/{physical_flag_key}",
        "request_json": {},
        "requires_file_and_evaluation_agreement": True,
    }


def test_project_override_replaces_upstream_mount_with_run_owned_control_file() -> None:
    override = (ROOT / "config" / "phase0" / "compose.phase0.yaml").read_text(
        encoding="utf-8"
    )

    assert "volumes: !override" in override
    assert (
        "../../artifacts/phase0/evaluator-only/"
        "${ECOMSRE_RUN_ID:?ECOMSRE_RUN_ID is required}/control"
    ) in override
    assert "target: /etc/flagd" in override
    assert "target: /app/data" in override
    assert '"file:./etc/flagd/demo.flagd.json"' in override
    assert override.count("read_only: true") >= 2


def test_logical_observer_module_cannot_import_ground_truth() -> None:
    source_path = ROOT / "src" / "ecomsre" / "scenarios" / "ad_service_failure.py"
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    imported_modules = {
        node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
    }
    imported_modules.update(
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    )

    assert all("ground_truth" not in module for module in imported_modules)
    assert "ecomsre.evidence.store" not in imported_modules


def test_observer_event_recursively_excludes_hidden_semantics() -> None:
    fields = set(ObserverControlEvent.model_fields)
    forbidden_fields = {
        "action",
        "endpoint",
        "expected",
        "flag",
        "logical_state",
        "path",
        "physical_state",
        "scenario",
        "uri",
        "value",
    }

    assert not any(
        forbidden in field.casefold()
        for field in fields
        for forbidden in forbidden_fields
    )


@pytest.mark.parametrize(
    "payload",
    [
        {"physical_flag_key": "adFailure"},
        {"flag_value": True},
        {"scenario": "adServiceFailure"},
        {"expected_transition": "baseline-to-fault"},
        {"path": "../evaluator-only/run/control/demo.flagd.json"},
        {"endpoint_uri": "http://127.0.0.1:8016/ofrep/v1/evaluate/flags/adFailure"},
        {"artifact_ref": "readbacks/ofrep-attempts.jsonl"},
        {"nested": [{"encoded": "adFailure"}]},
        {
            "execution": {
                "action": "INJECT",
                "before_state": "BASELINE",
                "target_state": "INJECTED",
                "mutation_state": "APPLIED",
            }
        },
    ],
)
def test_observer_store_rejects_recursive_control_truth_leakage(
    tmp_path: Path,
    payload: dict[str, object],
) -> None:
    observer = ObserverEvidenceStore(tmp_path, RUN_ID)

    with pytest.raises(ValueError, match="semantic leakage"):
        observer.append_event("changes/changes.jsonl", payload)


def test_bootstrap_derives_baseline_only_under_evaluator_control_root(
    tmp_path: Path,
) -> None:
    runtime = FlagdGroundTruthRuntime.bootstrap(
        project_root=ROOT,
        artifacts_root=tmp_path,
        run_id=RUN_ID,
        ofrep_client=NoopOfrepClient(),
        ofrep_endpoint="http://127.0.0.1:18016",
    )

    expected_root = tmp_path / "evaluator-only" / RUN_ID
    assert runtime.runtime_path == expected_root / FLAGD_CONFIG_RELATIVE_PATH
    assert runtime.runtime_path.read_bytes() == UPSTREAM_CONFIG.read_bytes()
    metadata = os.lstat(runtime.runtime_path)
    assert stat.S_ISREG(metadata.st_mode)
    assert metadata.st_uid == os.getuid()
    assert metadata.st_nlink == 1
    assert stat.S_IMODE(metadata.st_mode) == 0o600
    assert not (tmp_path / "observer-visible" / RUN_ID / "control").exists()

    truth = json.loads(
        (expected_root / "scenario-ground-truth.json").read_text(encoding="utf-8")
    )
    truth_text = _all_text(truth)
    assert "adFailure" in truth_text
    assert "adServiceFailure" in truth_text
    assert "off" in truth_text
    assert "on" in truth_text
    assert sorted(path.name for path in (expected_root / "control").iterdir()) == [
        "demo.flagd.json"
    ]


def test_http_adapter_is_direct_bounded_ofrep_without_shell_surface() -> None:
    assert not hasattr(HttpOfrepClient, "runner")
    assert not hasattr(HttpOfrepClient, "command")
    assert HttpOfrepClient.evaluate.__annotations__["timeout_seconds"] is float


def test_http_adapter_posts_exact_ofrep_request_with_caller_timeout() -> None:
    body = b'{"value":true,"variant":"on"}'
    client, fixture_socket, _clock = _direct_fixture_client(_http_response(200, body))

    evaluation = client.evaluate(
        endpoint="http://127.0.0.1:18016",
        flag_key="adFailure",
        timeout_seconds=1.25,
    )

    assert fixture_socket.address == ("127.0.0.1", 18016)
    assert fixture_socket.sent == (
        b"POST /ofrep/v1/evaluate/flags/adFailure HTTP/1.1\r\n"
        b"Host: 127.0.0.1:18016\r\n"
        b"Content-Type: application/json\r\n"
        b"Content-Length: 2\r\n"
        b"Connection: close\r\n"
        b"\r\n"
        b"{}"
    )
    assert fixture_socket.closed is True
    assert fixture_socket.timeouts[0] == pytest.approx(1.25)
    assert evaluation.http_status == 200
    assert evaluation.parsed_value is True
    assert evaluation.parsed_variant == "on"
    assert evaluation.error_code == "NONE"
    assert base64.b64decode(evaluation.raw_response_body_b64) == (
        b'{"value":true,"variant":"on"}'
    )
    assert (
        evaluation.content_sha256
        == hashlib.sha256(b'{"value":true,"variant":"on"}').hexdigest()
    )
    assert set(evaluation.request_metadata) == {
        "content_type",
        "method",
        "request_body_sha256",
    }


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://127.0.0.1:8016",
        "http://example.test:8016",
        "http://localhost:8016",
        "http://127.0.0.1:8016/path",
        "http://user:pass@127.0.0.1:8016",
    ],
)
def test_http_adapter_rejects_non_exact_local_origin_without_request(
    endpoint: str,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        ground_truth.socket,
        "socket",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("unsafe endpoint reached direct socket")
        ),
    )

    with pytest.raises(ValueError, match="literal loopback HTTP origin"):
        HttpOfrepClient().evaluate(
            endpoint=endpoint,
            flag_key="adFailure",
            timeout_seconds=1,
        )


@pytest.mark.parametrize(
    ("status", "body", "expected_error"),
    [
        (503, b'{"error":"unavailable"}', "HTTP_STATUS"),
        (200, b"not-json", "INVALID_JSON"),
        (200, b"x" * (MAX_OFREP_RESPONSE_BYTES + 1), "BODY_OVERSIZE"),
    ],
)
def test_http_adapter_returns_bounded_typed_raw_failures(
    status: int,
    body: bytes,
    expected_error: str,
) -> None:
    client, fixture_socket, _clock = _direct_fixture_client(
        _http_response(status, body)
    )

    readback = client.evaluate(
        endpoint="http://127.0.0.1:18016",
        flag_key="adFailure",
        timeout_seconds=1,
    )

    raw = base64.b64decode(readback.raw_response_body_b64)
    assert readback.error_code == expected_error
    assert len(raw) <= MAX_OFREP_RESPONSE_BYTES
    assert readback.raw_body_truncated is (expected_error == "BODY_OVERSIZE")
    assert readback.content_sha256 == hashlib.sha256(raw).hexdigest()
    assert fixture_socket.closed is True


def test_http_adapter_ignores_proxy_environment_and_never_follows_redirect(
    monkeypatch,
) -> None:
    monkeypatch.setenv("HTTP_PROXY", "http://remote.example:8888")
    monkeypatch.setenv("HTTPS_PROXY", "http://remote.example:8888")
    monkeypatch.setenv("ALL_PROXY", "socks5://remote.example:1080")
    client, fixture_socket, _clock = _direct_fixture_client(
        _http_response(
            302,
            b"",
            headers=(("Location", "https://remote.example/steal"),),
        )
    )

    readback = client.evaluate(
        endpoint="http://127.0.0.1:18016",
        flag_key="adFailure",
        timeout_seconds=1,
    )

    assert readback.http_status == 302
    assert readback.error_code == "HTTP_STATUS"
    assert fixture_socket.address == ("127.0.0.1", 18016)
    assert b"remote.example" not in fixture_socket.sent
    assert fixture_socket.closed is True


def test_http_adapter_recomputes_remaining_timeout_for_slow_body_and_closes() -> None:
    body = b"slow"
    response = _http_response(200, body)
    header_size = response.index(b"\r\n\r\n") + 4
    clock = FixtureReadbackClock()
    client, fixture_socket, _clock = _direct_fixture_client(
        response,
        clock=clock,
        chunk_sizes=[header_size, 1, 1, 1, 1],
        delays=[0.0, 0.4, 0.4, 0.4, 0.4],
    )

    readback = client.evaluate(
        endpoint="http://127.0.0.1:18016",
        flag_key="adFailure",
        timeout_seconds=1,
    )

    assert readback.error_code == "TRANSPORT_ERROR"
    assert clock.elapsed == pytest.approx(1)
    assert fixture_socket.closed is True
    assert fixture_socket.timeouts == sorted(
        fixture_socket.timeouts,
        reverse=True,
    )


def test_content_length_timeout_preserves_bounded_partial_raw_body() -> None:
    header = b"HTTP/1.1 200 Fixture\r\nContent-Length: 4\r\nConnection: close\r\n\r\n"
    client, fixture_socket, _clock = _direct_fixture_client(
        header + b"abcd",
        chunk_sizes=[len(header), 1, 1, 1],
        delays=[0.0, 0.0, 0.0, 2.0],
    )

    readback = client.evaluate(
        endpoint="http://127.0.0.1:18016",
        flag_key="adFailure",
        timeout_seconds=1,
    )

    assert readback.error_code == "TRANSPORT_ERROR"
    assert base64.b64decode(readback.raw_response_body_b64) == b"ab"
    assert readback.content_sha256 == hashlib.sha256(b"ab").hexdigest()
    assert readback.raw_body_truncated is False
    assert fixture_socket.closed is True


def test_oversize_declared_body_timeout_caps_and_marks_partial_raw_body() -> None:
    header = (
        b"HTTP/1.1 200 Fixture\r\nContent-Length: 100000\r\nConnection: close\r\n\r\n"
    )
    client, fixture_socket, _clock = _direct_fixture_client(
        header + (b"x" * (MAX_OFREP_RESPONSE_BYTES + 1)),
        chunk_sizes=[len(header), 16_384, 16_384, 16_384, 16_384, 1],
        delays=[0.0, 0.0, 0.0, 0.0, 0.0, 2.0],
    )

    readback = client.evaluate(
        endpoint="http://127.0.0.1:18016",
        flag_key="adFailure",
        timeout_seconds=1,
    )

    raw = base64.b64decode(readback.raw_response_body_b64)
    assert readback.error_code == "TRANSPORT_ERROR"
    assert len(raw) == MAX_OFREP_RESPONSE_BYTES
    assert readback.content_sha256 == hashlib.sha256(raw).hexdigest()
    assert readback.raw_body_truncated is True
    assert fixture_socket.closed is True


def test_chunked_timeout_preserves_bounded_partial_raw_body() -> None:
    header = (
        b"HTTP/1.1 200 Fixture\r\n"
        b"Transfer-Encoding: chunked\r\n"
        b"Connection: close\r\n"
        b"\r\n"
    )
    client, fixture_socket, _clock = _direct_fixture_client(
        header + b"4\r\nabcd\r\n0\r\n\r\n",
        chunk_sizes=[len(header), 3, 1, 1, 1],
        delays=[0.0, 0.0, 0.0, 0.0, 2.0],
    )

    readback = client.evaluate(
        endpoint="http://127.0.0.1:18016",
        flag_key="adFailure",
        timeout_seconds=1,
    )

    assert readback.error_code == "TRANSPORT_ERROR"
    assert base64.b64decode(readback.raw_response_body_b64) == b"ab"
    assert readback.content_sha256 == hashlib.sha256(b"ab").hexdigest()
    assert readback.raw_body_truncated is False
    assert fixture_socket.closed is True


def test_malformed_content_length_preserves_buffered_partial_raw_body() -> None:
    response = (
        b"HTTP/1.1 200 Fixture\r\n"
        b"Content-Length: invalid\r\n"
        b"Connection: close\r\n"
        b"\r\n"
        b"ab"
    )
    client, fixture_socket, _clock = _direct_fixture_client(response)

    readback = client.evaluate(
        endpoint="http://127.0.0.1:18016",
        flag_key="adFailure",
        timeout_seconds=1,
    )

    assert readback.error_code == "TRANSPORT_ERROR"
    assert base64.b64decode(readback.raw_response_body_b64) == b"ab"
    assert readback.content_sha256 == hashlib.sha256(b"ab").hexdigest()
    assert fixture_socket.closed is True


def test_malformed_http_status_preserves_buffered_partial_raw_body() -> None:
    response = b"BROKEN STATUS\r\nContent-Length: 2\r\nConnection: close\r\n\r\nab"
    client, fixture_socket, _clock = _direct_fixture_client(response)

    readback = client.evaluate(
        endpoint="http://127.0.0.1:18016",
        flag_key="adFailure",
        timeout_seconds=1,
    )

    assert readback.error_code == "TRANSPORT_ERROR"
    assert readback.http_status is None
    assert base64.b64decode(readback.raw_response_body_b64) == b"ab"
    assert readback.content_sha256 == hashlib.sha256(b"ab").hexdigest()
    assert fixture_socket.closed is True


def test_malformed_header_after_valid_status_preserves_status_and_partial_body() -> (
    None
):
    response = (
        b"HTTP/1.1 503 Fixture\r\nMalformed-Header\r\nContent-Length: 2\r\n\r\nab"
    )
    client, fixture_socket, _clock = _direct_fixture_client(response)

    readback = client.evaluate(
        endpoint="http://127.0.0.1:18016",
        flag_key="adFailure",
        timeout_seconds=1,
    )

    assert readback.http_status == 503
    assert readback.error_code == "TRANSPORT_ERROR"
    assert base64.b64decode(readback.raw_response_body_b64) == b"ab"
    assert readback.content_sha256 == hashlib.sha256(b"ab").hexdigest()
    assert fixture_socket.closed is True


def test_malformed_chunk_delimiter_preserves_completed_partial_raw_body() -> None:
    header = (
        b"HTTP/1.1 200 Fixture\r\n"
        b"Transfer-Encoding: chunked\r\n"
        b"Connection: close\r\n"
        b"\r\n"
    )
    client, fixture_socket, _clock = _direct_fixture_client(
        header + b"2\r\nabXX",
        chunk_sizes=[len(header), 3, 2, 2],
    )

    readback = client.evaluate(
        endpoint="http://127.0.0.1:18016",
        flag_key="adFailure",
        timeout_seconds=1,
    )

    assert readback.error_code == "TRANSPORT_ERROR"
    assert base64.b64decode(readback.raw_response_body_b64) == b"ab"
    assert readback.content_sha256 == hashlib.sha256(b"ab").hexdigest()
    assert fixture_socket.closed is True


def test_chunked_oversize_stops_after_max_plus_one_body_bytes() -> None:
    header = (
        b"HTTP/1.1 200 Fixture\r\n"
        b"Transfer-Encoding: chunked\r\n"
        b"Connection: close\r\n"
        b"\r\n"
    )
    size_line = b"186a0\r\n"
    response = header + size_line + (b"x" * 100_000) + b"\r\n0\r\n\r\n"
    client, fixture_socket, _clock = _direct_fixture_client(
        response,
        chunk_sizes=[len(header)],
    )

    readback = client.evaluate(
        endpoint="http://127.0.0.1:18016",
        flag_key="adFailure",
        timeout_seconds=1,
    )

    assert readback.error_code == "BODY_OVERSIZE"
    assert readback.raw_body_truncated is True
    assert fixture_socket.received_bytes - len(header) <= (
        len(size_line) + MAX_OFREP_RESPONSE_BYTES + 1
    )
    assert fixture_socket.closed is True


@pytest.mark.parametrize(
    "chunked_body",
    [
        (b"f" * 1_000) + b"\r\n",
        b"0\r\nX-Fixture: " + (b"x" * 10_000) + b"\r\n\r\n",
    ],
)
def test_chunked_size_and_trailer_lines_fail_closed_with_small_wire_bounds(
    chunked_body: bytes,
) -> None:
    header = (
        b"HTTP/1.1 200 Fixture\r\n"
        b"Transfer-Encoding: chunked\r\n"
        b"Connection: close\r\n"
        b"\r\n"
    )
    client, fixture_socket, _clock = _direct_fixture_client(
        header + chunked_body,
        chunk_sizes=[len(header)],
    )

    readback = client.evaluate(
        endpoint="http://127.0.0.1:18016",
        flag_key="adFailure",
        timeout_seconds=1,
    )

    assert readback.error_code == "TRANSPORT_ERROR"
    assert fixture_socket.received_bytes - len(header) <= 4 * 1024 + 2
    assert fixture_socket.closed is True


def test_oversize_http_headers_stop_at_header_bound_plus_one() -> None:
    response = b"HTTP/1.1 200 Fixture\r\nX-Fixture: " + (b"x" * 100_000)
    client, fixture_socket, _clock = _direct_fixture_client(response)

    readback = client.evaluate(
        endpoint="http://127.0.0.1:18016",
        flag_key="adFailure",
        timeout_seconds=1,
    )

    assert readback.error_code == "TRANSPORT_ERROR"
    assert fixture_socket.received_bytes <= 64 * 1024 + 1
    assert fixture_socket.closed is True
