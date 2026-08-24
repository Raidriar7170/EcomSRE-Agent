from __future__ import annotations

from collections.abc import Mapping

import pytest

from ecomsre.dta_v2.v22.real_fault_flat_arm_v225 import (
    ExactRequestRetryTransportV225,
)
from ecomsre.dta_v2.v22.simple_provider import ProviderTransportErrorV22


class _SequenceTransport:
    def __init__(self, outcomes: list[object]) -> None:
        self.outcomes = outcomes
        self.payloads: list[Mapping[str, object]] = []

    def post_json(self, *, url, headers, payload, timeout_seconds):
        del url, headers, timeout_seconds
        self.payloads.append(payload)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def test_exact_request_transport_allows_three_retryable_retries() -> None:
    transport = _SequenceTransport(
        [
            ProviderTransportErrorV22("HTTP_429", status_code=429),
            ProviderTransportErrorV22("HTTP_500", status_code=500),
            ProviderTransportErrorV22("TIMEOUTERROR"),
            {"ok": True},
        ]
    )
    delays: list[float] = []
    retry = ExactRequestRetryTransportV225(
        transport=transport,
        sleeper=delays.append,
    )
    payload = {"model": "gpt-test", "value": 1}

    result = retry.post_json(
        url="https://example.invalid/v1/chat/completions",
        headers={},
        payload=payload,
        timeout_seconds=1.0,
    )

    assert result == {"ok": True}
    assert retry.transport_retry_count == 3
    assert delays == [5.0, 15.0, 30.0]
    assert transport.payloads == [payload, payload, payload, payload]


def test_nonretryable_4xx_is_not_retried() -> None:
    transport = _SequenceTransport(
        [ProviderTransportErrorV22("HTTP_400", status_code=400)]
    )
    retry = ExactRequestRetryTransportV225(
        transport=transport,
        sleeper=lambda _delay: pytest.fail("nonretryable output slept"),
    )

    with pytest.raises(ConnectionError, match="Flat Provider transport failed"):
        retry.post_json(
            url="https://example.invalid/v1/chat/completions",
            headers={},
            payload={"model": "gpt-test"},
            timeout_seconds=1.0,
        )

    assert retry.transport_retry_count == 0
    assert len(transport.payloads) == 1
