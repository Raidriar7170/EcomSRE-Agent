from __future__ import annotations

import ssl
import urllib.error
from collections.abc import Mapping

import pytest

from ecomsre_live_sandbox.provider_transport import TransientTLSRetryTransport


class ScriptedTransport:
    def __init__(
        self, outcomes: list[BaseException | Mapping[str, object]]
    ) -> None:
        self.outcomes = list(outcomes)
        self.calls = 0

    def post_json(
        self,
        *,
        url: str,
        headers: Mapping[str, str],
        payload: Mapping[str, object],
        timeout_seconds: float,
    ) -> Mapping[str, object]:
        del url, headers, payload, timeout_seconds
        self.calls += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def _transport_failure(reason: BaseException) -> ConnectionError:
    error = ConnectionError("OpenAI-compatible request failed")
    error.__cause__ = urllib.error.URLError(reason)
    return error


def test_live_transport_retries_one_tls_eof_without_new_semantic_call() -> None:
    base = ScriptedTransport(
        [
            _transport_failure(ssl.SSLEOFError(8, "handshake eof")),
            {"ok": True},
        ]
    )
    waits: list[float] = []
    transport = TransientTLSRetryTransport(
        base,
        maximum_retries=1,
        sleeper=waits.append,
    )

    result = transport.post_json(
        url="https://llm.example.test/v1/chat/completions",
        headers={"Authorization": "Bearer test-only"},
        payload={"bounded": True},
        timeout_seconds=0.5,
    )

    assert result == {"ok": True}
    assert base.calls == 2
    assert waits == [2.0]
    assert transport.last_retry_count == 1


def test_live_transport_never_retries_certificate_failure() -> None:
    base = ScriptedTransport(
        [
            _transport_failure(
                ssl.SSLCertVerificationError(1, "certificate verify failed")
            )
        ]
    )
    waits: list[float] = []
    transport = TransientTLSRetryTransport(
        base,
        maximum_retries=1,
        sleeper=waits.append,
    )

    with pytest.raises(ConnectionError, match="request failed"):
        transport.post_json(
            url="https://llm.example.test/v1/chat/completions",
            headers={"Authorization": "Bearer test-only"},
            payload={"bounded": True},
            timeout_seconds=0.5,
        )

    assert base.calls == 1
    assert waits == []
    assert transport.last_retry_count == 0


def test_live_transport_retry_count_is_per_request() -> None:
    base = ScriptedTransport(
        [
            _transport_failure(ssl.SSLZeroReturnError(6, "closed")),
            {"first": True},
            {"second": True},
        ]
    )
    transport = TransientTLSRetryTransport(
        base,
        maximum_retries=1,
        sleeper=lambda _: None,
    )

    transport.post_json(
        url="https://llm.example.test/v1/chat/completions",
        headers={},
        payload={},
        timeout_seconds=0.5,
    )
    assert transport.last_retry_count == 1

    transport.post_json(
        url="https://llm.example.test/v1/chat/completions",
        headers={},
        payload={},
        timeout_seconds=0.5,
    )
    assert transport.last_retry_count == 0
