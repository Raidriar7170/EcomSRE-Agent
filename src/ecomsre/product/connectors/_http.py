"""Bounded HTTP transport shared by read-only Product connectors."""

from __future__ import annotations

import json
import math
import time
from typing import Any, Callable, Mapping

import httpx

from ecomsre.dta_v2.v22.read_contracts import ReadSourceStatusV22
from ecomsre.product.connectors.credentials import (
    ConnectorCredentialError,
    CredentialResolverV1,
)


def _pairs_without_duplicates(
    pairs: list[tuple[str, object]],
) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON key")
        value[key] = item
    return value


def _reject_nonfinite(_value: str) -> None:
    raise ValueError("non-finite JSON number")


class ConnectorRequestError(RuntimeError):
    def __init__(
        self,
        status: ReadSourceStatusV22,
        safe_error_code: str,
        latency_ms: float,
    ) -> None:
        super().__init__(safe_error_code)
        self.status = status
        self.safe_error_code = safe_error_code
        self.latency_ms = latency_ms


class BoundedHttpTransportV1:
    def __init__(
        self,
        *,
        credential_resolver: CredentialResolverV1,
        credential_refs: Mapping[str, str],
        timeout_seconds: float,
        maximum_response_bytes: int,
        transport: httpx.BaseTransport | None = None,
        before_request: Callable[[], None] | None = None,
    ) -> None:
        self._resolver = credential_resolver
        self._credential_refs = dict(credential_refs)
        self._timeout_seconds = timeout_seconds
        self._maximum_response_bytes = maximum_response_bytes
        self._before_request = before_request
        self._client = httpx.Client(
            timeout=timeout_seconds,
            transport=transport,
            follow_redirects=False,
            trust_env=False,
        )

    def request_json(
        self,
        method: str,
        url: str,
        *,
        params: Mapping[str, str | int | float | bool | None] | None = None,
        json_body: object | None = None,
        allow_http_error_status: bool = False,
        timeout_seconds: float | None = None,
    ) -> tuple[object, int, float]:
        content, status_code, latency_ms = self.request_bytes(
            method,
            url,
            params=params,
            json_body=json_body,
            allow_http_error_status=allow_http_error_status,
            timeout_seconds=timeout_seconds,
        )
        try:
            payload = json.loads(
                content,
                object_pairs_hook=_pairs_without_duplicates,
                parse_constant=_reject_nonfinite,
            )
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
            raise ConnectorRequestError(
                ReadSourceStatusV22.FAILURE_SCHEMA,
                "CONNECTOR_SCHEMA_INVALID",
                latency_ms,
            ) from error
        return payload, status_code, latency_ms

    def request_bytes(
        self,
        method: str,
        url: str,
        *,
        params: Mapping[str, str | int | float | bool | None] | None = None,
        json_body: object | None = None,
        allow_http_error_status: bool = False,
        timeout_seconds: float | None = None,
    ) -> tuple[bytes, int, float]:
        started = time.monotonic()
        try:
            if self._before_request is not None:
                self._before_request()
            headers = self._resolver.resolve_http_headers(
                self._credential_refs
            ).as_dict()
            headers["Accept"] = "application/json"
            with self._client.stream(
                method,
                url,
                params=params,
                json=json_body,
                headers=headers,
                timeout=(
                    self._timeout_seconds
                    if timeout_seconds is None
                    else timeout_seconds
                ),
            ) as response:
                chunks: list[bytes] = []
                remaining = self._maximum_response_bytes + 1
                for chunk in response.iter_bytes():
                    if not chunk:
                        continue
                    take = chunk[:remaining]
                    chunks.append(take)
                    remaining -= len(take)
                    if remaining == 0:
                        break
                content = b"".join(chunks)
                if len(content) > self._maximum_response_bytes:
                    raise ConnectorRequestError(
                        ReadSourceStatusV22.FAILURE_SCHEMA,
                        "CONNECTOR_RESPONSE_TOO_LARGE",
                        (time.monotonic() - started) * 1000,
                    )
                if not allow_http_error_status and not 200 <= response.status_code < 300:
                    raise ConnectorRequestError(
                        ReadSourceStatusV22.FAILURE_UNAVAILABLE,
                        "CONNECTOR_UNAVAILABLE",
                        (time.monotonic() - started) * 1000,
                    )
                latency_ms = (time.monotonic() - started) * 1000
                if not math.isfinite(latency_ms):
                    raise ConnectorRequestError(
                        ReadSourceStatusV22.FAILURE_SCHEMA,
                        "CONNECTOR_TIMING_INVALID",
                        0,
                    )
                return content, response.status_code, latency_ms
        except ConnectorRequestError:
            raise
        except ConnectorCredentialError as error:
            raise ConnectorRequestError(
                ReadSourceStatusV22.FAILURE_UNAVAILABLE,
                "CONNECTOR_CREDENTIAL_UNAVAILABLE",
                (time.monotonic() - started) * 1000,
            ) from error
        except httpx.TimeoutException as error:
            raise ConnectorRequestError(
                ReadSourceStatusV22.FAILURE_TIMEOUT,
                "CONNECTOR_TIMEOUT",
                (time.monotonic() - started) * 1000,
            ) from error
        except httpx.HTTPError as error:
            raise ConnectorRequestError(
                ReadSourceStatusV22.FAILURE_UNAVAILABLE,
                "CONNECTOR_UNAVAILABLE",
                (time.monotonic() - started) * 1000,
            ) from error

    def close(self) -> None:
        self._client.close()


def require_mapping(value: object) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("connector response is not a JSON object")
    return value


__all__ = (
    "BoundedHttpTransportV1",
    "ConnectorRequestError",
    "require_mapping",
)
