"""Live-sandbox-only handling for one transient Provider TLS EOF."""

from __future__ import annotations

import ssl
import time
import urllib.error
from collections.abc import Callable, Mapping

from ecomsre.model.gateway import OpenAICompatibleTransport


class TransientTLSRetryTransport:
    """Retry one TLS EOF transport attempt without adding a model call."""

    def __init__(
        self,
        base: OpenAICompatibleTransport,
        *,
        maximum_retries: int,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        if type(maximum_retries) is not int or not 0 <= maximum_retries <= 1:
            raise ValueError("TLS transient retry budget must be zero or one")
        self._base = base
        self._maximum_retries = maximum_retries
        self._sleeper = sleeper
        self._last_retry_count = 0

    @property
    def last_retry_count(self) -> int:
        return self._last_retry_count

    def post_json(
        self,
        *,
        url: str,
        headers: Mapping[str, str],
        payload: Mapping[str, object],
        timeout_seconds: float,
    ) -> Mapping[str, object]:
        self._last_retry_count = 0
        while True:
            try:
                return self._base.post_json(
                    url=url,
                    headers=headers,
                    payload=payload,
                    timeout_seconds=timeout_seconds,
                )
            except ConnectionError as error:
                if (
                    self._last_retry_count >= self._maximum_retries
                    or not _is_tls_eof_transient(error)
                ):
                    raise
                self._last_retry_count += 1
                self._sleeper(2.0)


def _is_tls_eof_transient(error: BaseException) -> bool:
    pending = [error]
    found: list[BaseException] = []
    seen: set[int] = set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        found.append(current)
        if current.__cause__ is not None:
            pending.append(current.__cause__)
        if isinstance(current, urllib.error.URLError) and isinstance(
            current.reason, BaseException
        ):
            pending.append(current.reason)
    if any(isinstance(item, ssl.SSLCertVerificationError) for item in found):
        return False
    return any(
        isinstance(item, (ssl.SSLEOFError, ssl.SSLZeroReturnError))
        for item in found
    )


__all__ = ["TransientTLSRetryTransport"]
