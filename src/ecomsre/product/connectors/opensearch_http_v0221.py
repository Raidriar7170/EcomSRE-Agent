"""Safe HTTP envelopes and bounded OpenSearch transport for Product v0.2.2.1."""

from __future__ import annotations

from enum import Enum
import hashlib
import json
import re
import time
from typing import Any, Literal, Mapping, Sequence
from urllib.parse import urlsplit

import httpx
from pydantic import Field, model_validator

from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22
from ecomsre.product.connectors.opensearch_probe_protocol_v0221 import (
    OpenSearchProbeEndpointKindV0221,
    OpenSearchProbeRequestAttemptV0221,
)
from ecomsre.product.contracts import ProductModelV1


_SECRET_PARAMETER = re.compile(
    r"(?:^|[_-])(?:api[_-]?key|authorization|credential|password|secret|token)(?:$|[_-])",
    re.I,
)


class OpenSearchHttpErrorCodeV0221(str, Enum):
    OPENSEARCH_REQUEST_PARAMETER_INVALID = "OPENSEARCH_REQUEST_PARAMETER_INVALID"
    OPENSEARCH_REQUEST_BODY_INVALID = "OPENSEARCH_REQUEST_BODY_INVALID"
    OPENSEARCH_ENDPOINT_NOT_FOUND = "OPENSEARCH_ENDPOINT_NOT_FOUND"
    OPENSEARCH_METHOD_NOT_ALLOWED = "OPENSEARCH_METHOD_NOT_ALLOWED"
    OPENSEARCH_PERMISSION_DENIED = "OPENSEARCH_PERMISSION_DENIED"
    OPENSEARCH_INDEX_NOT_FOUND = "OPENSEARCH_INDEX_NOT_FOUND"
    OPENSEARCH_FIELD_CAPS_UNSUPPORTED = "OPENSEARCH_FIELD_CAPS_UNSUPPORTED"
    OPENSEARCH_MAPPING_UNAVAILABLE = "OPENSEARCH_MAPPING_UNAVAILABLE"
    OPENSEARCH_SEARCH_UNAVAILABLE = "OPENSEARCH_SEARCH_UNAVAILABLE"
    OPENSEARCH_HTTP_TRANSIENT = "OPENSEARCH_HTTP_TRANSIENT"
    OPENSEARCH_HTTP_UNKNOWN = "OPENSEARCH_HTTP_UNKNOWN"


class OpenSearchRootCauseV0221(ProductModelV1):
    error_type: str | None = Field(default=None, max_length=120)
    error_reason: str | None = Field(default=None, max_length=240)


class OpenSearchHttpErrorEnvelopeV0221(ProductModelV1):
    schema_version: Literal["ecomsre.product.opensearch-http-error.v0221"] = (
        "ecomsre.product.opensearch-http-error.v0221"
    )
    http_status: int = Field(ge=100, le=599)
    error_type: str | None = Field(default=None, max_length=120)
    error_reason: str | None = Field(default=None, max_length=240)
    root_causes: tuple[OpenSearchRootCauseV0221, ...] = Field(max_length=3)
    method: Literal["GET", "POST"]
    endpoint_kind: OpenSearchProbeEndpointKindV0221
    path_template: str = Field(min_length=2, max_length=255)
    query_parameter_names: tuple[str, ...] = Field(max_length=10)
    request_body_schema_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    response_body_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    response_bytes: int = Field(ge=0, le=2_000_000)
    safe_error_code: OpenSearchHttpErrorCodeV0221
    envelope_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_bound_envelope(self) -> "OpenSearchHttpErrorEnvelopeV0221":
        if self.query_parameter_names != tuple(sorted(set(self.query_parameter_names))):
            raise ValueError("OpenSearch error query names are not canonical")
        if any(_SECRET_PARAMETER.search(name) for name in self.query_parameter_names):
            raise ValueError("OpenSearch error query name is secret-bearing")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"envelope_sha256"})
        )
        if self.envelope_sha256 != expected:
            raise ValueError("OpenSearch HTTP error envelope digest differs")
        return self

    @classmethod
    def build(cls, **values: Any) -> "OpenSearchHttpErrorEnvelopeV0221":
        body = {
            "schema_version": "ecomsre.product.opensearch-http-error.v0221",
            **values,
        }
        draft = cls.model_construct(**body, envelope_sha256="0" * 64)
        serialized = draft.model_dump(mode="json", exclude={"envelope_sha256"})
        return cls.model_validate(
            {**serialized, "envelope_sha256": semantic_sha256_v22(serialized)}
        )


class OpenSearchHttpErrorV0221(RuntimeError):
    def __init__(
        self,
        envelope: OpenSearchHttpErrorEnvelopeV0221,
        attempt: OpenSearchProbeRequestAttemptV0221,
    ) -> None:
        super().__init__(f"OpenSearch probe {envelope.safe_error_code.value}")
        self.envelope = envelope
        self.attempt = attempt


def _body_schema_v0221(value: object) -> object:
    if value is None:
        return {"type": "null"}
    if isinstance(value, bool):
        return {"type": "boolean"}
    if isinstance(value, int):
        return {"type": "integer"}
    if isinstance(value, float):
        return {"type": "number"}
    if isinstance(value, str):
        return {"type": "string"}
    if isinstance(value, Mapping):
        return {
            "type": "object",
            "properties": {
                str(key): _body_schema_v0221(value[key])
                for key in sorted(value, key=str)
            },
        }
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        item_shapes = {
            json.dumps(_body_schema_v0221(item), sort_keys=True, separators=(",", ":"))
            for item in value
        }
        return {
            "type": "array",
            "items": [json.loads(item) for item in sorted(item_shapes)],
        }
    return {"type": type(value).__name__}


def request_body_schema_sha256_v0221(request_body: object) -> str:
    return semantic_sha256_v22(_body_schema_v0221(request_body))


def _request_scalar_values_v0221(value: object) -> set[str]:
    if isinstance(value, str):
        return {value} if value else set()
    if isinstance(value, Mapping):
        output: set[str] = set()
        for nested in value.values():
            output.update(_request_scalar_values_v0221(nested))
        return output
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        output = set()
        for nested in value:
            output.update(_request_scalar_values_v0221(nested))
        return output
    return set()


def _bounded_text_v0221(
    value: object,
    maximum: int,
    *,
    request_values: set[str],
) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = " ".join(value.replace("\x00", " ").split())
    for request_value in sorted(request_values, key=len, reverse=True):
        normalized = normalized.replace(request_value, "<request-value-redacted>")
    normalized = re.sub(r"https?://\S+", "<url-redacted>", normalized, flags=re.I)
    normalized = re.sub(
        r"(?i)\b(?:bearer|basic)\s+[A-Za-z0-9._~+\-/=]+",
        "<authorization-redacted>",
        normalized,
    )
    return normalized[:maximum] or None


def _classify_error_v0221(
    *,
    http_status: int,
    error_type: str | None,
    error_reason: str | None,
    endpoint_kind: OpenSearchProbeEndpointKindV0221,
) -> OpenSearchHttpErrorCodeV0221:
    lowered_type = (error_type or "").lower()
    lowered_reason = (error_reason or "").lower()
    if http_status in {429, 502, 503, 504}:
        return OpenSearchHttpErrorCodeV0221.OPENSEARCH_HTTP_TRANSIENT
    if "index_not_found" in lowered_type:
        return OpenSearchHttpErrorCodeV0221.OPENSEARCH_INDEX_NOT_FOUND
    if http_status == 403:
        return OpenSearchHttpErrorCodeV0221.OPENSEARCH_PERMISSION_DENIED
    if http_status == 405:
        return OpenSearchHttpErrorCodeV0221.OPENSEARCH_METHOD_NOT_ALLOWED
    if http_status == 404:
        return OpenSearchHttpErrorCodeV0221.OPENSEARCH_ENDPOINT_NOT_FOUND
    if http_status == 400:
        if (
            "body" in lowered_reason
            or "parse" in lowered_type
            or "x_content" in lowered_type
        ):
            return OpenSearchHttpErrorCodeV0221.OPENSEARCH_REQUEST_BODY_INVALID
        return OpenSearchHttpErrorCodeV0221.OPENSEARCH_REQUEST_PARAMETER_INVALID
    if endpoint_kind is OpenSearchProbeEndpointKindV0221.MAPPING:
        return OpenSearchHttpErrorCodeV0221.OPENSEARCH_MAPPING_UNAVAILABLE
    if endpoint_kind is OpenSearchProbeEndpointKindV0221.FIELD_CAPS:
        return OpenSearchHttpErrorCodeV0221.OPENSEARCH_FIELD_CAPS_UNSUPPORTED
    if endpoint_kind in {
        OpenSearchProbeEndpointKindV0221.SERVICE_AGGREGATION,
        OpenSearchProbeEndpointKindV0221.TIMESTAMP_RANGE,
        OpenSearchProbeEndpointKindV0221.SAMPLE_SEARCH,
        OpenSearchProbeEndpointKindV0221.PROFILE_VERIFICATION,
    }:
        return OpenSearchHttpErrorCodeV0221.OPENSEARCH_SEARCH_UNAVAILABLE
    return OpenSearchHttpErrorCodeV0221.OPENSEARCH_HTTP_UNKNOWN


def build_opensearch_http_error_envelope_v0221(
    *,
    http_status: int,
    response_body: bytes,
    method: Literal["GET", "POST"],
    endpoint_kind: OpenSearchProbeEndpointKindV0221,
    path_template: str,
    query_parameters: Mapping[str, str],
    request_body: object,
) -> OpenSearchHttpErrorEnvelopeV0221:
    query_names = tuple(sorted(query_parameters))
    if any(_SECRET_PARAMETER.search(name) for name in query_names):
        raise ValueError("OpenSearch query parameter name is secret-bearing")
    request_values = set(query_parameters.values())
    request_values.update(_request_scalar_values_v0221(request_body))
    error_type: str | None = None
    error_reason: str | None = None
    root_causes: list[OpenSearchRootCauseV0221] = []
    try:
        payload = json.loads(response_body)
    except (UnicodeDecodeError, json.JSONDecodeError):
        payload = None
    if isinstance(payload, Mapping):
        raw_error = payload.get("error")
        if isinstance(raw_error, Mapping):
            error_type = _bounded_text_v0221(
                raw_error.get("type"), 120, request_values=request_values
            )
            error_reason = _bounded_text_v0221(
                raw_error.get("reason"), 240, request_values=request_values
            )
            raw_roots = raw_error.get("root_cause")
            if isinstance(raw_roots, list):
                for raw_root in raw_roots[:3]:
                    if isinstance(raw_root, Mapping):
                        root_causes.append(
                            OpenSearchRootCauseV0221(
                                error_type=_bounded_text_v0221(
                                    raw_root.get("type"),
                                    120,
                                    request_values=request_values,
                                ),
                                error_reason=_bounded_text_v0221(
                                    raw_root.get("reason"),
                                    240,
                                    request_values=request_values,
                                ),
                            )
                        )
    return OpenSearchHttpErrorEnvelopeV0221.build(
        http_status=http_status,
        error_type=error_type,
        error_reason=error_reason,
        root_causes=tuple(root_causes),
        method=method,
        endpoint_kind=endpoint_kind,
        path_template=path_template,
        query_parameter_names=query_names,
        request_body_schema_sha256=request_body_schema_sha256_v0221(request_body),
        response_body_sha256=hashlib.sha256(response_body).hexdigest(),
        response_bytes=len(response_body),
        safe_error_code=_classify_error_v0221(
            http_status=http_status,
            error_type=error_type,
            error_reason=error_reason,
            endpoint_kind=endpoint_kind,
        ),
    )


class OpenSearchProbeClientV0221:
    def __init__(
        self,
        *,
        base_url: str,
        maximum_request_count: int,
        maximum_response_bytes: int,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        parsed = urlsplit(base_url)
        if (
            parsed.scheme != "http"
            or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
            or parsed.port is None
            or parsed.path not in {"", "/"}
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("OpenSearch probe endpoint must be loopback")
        if not 1 <= maximum_request_count <= 16:
            raise ValueError("OpenSearch probe request bound differs")
        if not 1 <= maximum_response_bytes <= 2_000_000:
            raise ValueError("OpenSearch probe response-byte bound differs")
        self.maximum_request_count = maximum_request_count
        self.maximum_response_bytes = maximum_response_bytes
        self.request_count = 0
        self.attempts: list[OpenSearchProbeRequestAttemptV0221] = []
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            timeout=15.0,
            follow_redirects=False,
            trust_env=False,
            transport=transport,
        )

    @staticmethod
    def _validate_request(
        *,
        method: str,
        endpoint_kind: OpenSearchProbeEndpointKindV0221,
        path: str,
        path_template: str,
        query_parameters: Mapping[str, str],
        json_body: object,
    ) -> None:
        if method not in {"GET", "POST"}:
            raise ValueError("OpenSearch probe request is not read-only")
        if not path.startswith("/") or "?" in path or "#" in path or ".." in path:
            raise ValueError("OpenSearch probe request path is invalid")
        if any(_SECRET_PARAMETER.search(name) for name in query_parameters):
            raise ValueError("OpenSearch query parameter name is secret-bearing")
        if endpoint_kind is OpenSearchProbeEndpointKindV0221.MAPPING:
            valid = (
                method == "GET"
                and path_template == "/{index}/_mapping"
                and path.endswith("/_mapping")
                and not query_parameters
                and json_body is None
            )
        elif endpoint_kind is OpenSearchProbeEndpointKindV0221.FIELD_MAPPING:
            valid = (
                method == "GET"
                and path_template == "/{index}/_mapping/field/{candidate_fields}"
                and "/_mapping/field/" in path
                and not query_parameters
                and json_body is None
            )
        elif endpoint_kind is OpenSearchProbeEndpointKindV0221.FIELD_CAPS:
            valid_body = json_body is None or (
                isinstance(json_body, Mapping) and set(json_body) == {"index_filter"}
            )
            valid = (
                path_template == "/{index}/_field_caps"
                and path.endswith("/_field_caps")
                and set(query_parameters) == {"fields"}
                and valid_body
            )
        else:
            valid = (
                method == "POST"
                and path_template == "/{index}/_search"
                and path.endswith("/_search")
                and not query_parameters
                and isinstance(json_body, Mapping)
            )
        if not valid:
            raise ValueError("OpenSearch probe endpoint request shape is invalid")

    def request_json(
        self,
        *,
        plan_id: str,
        request_id: str,
        method: Literal["GET", "POST"],
        endpoint_kind: OpenSearchProbeEndpointKindV0221,
        path: str,
        path_template: str,
        query_parameters: Mapping[str, str],
        json_body: object = None,
        transport_retry_count: int = 0,
    ) -> tuple[object, bytes, OpenSearchProbeRequestAttemptV0221]:
        self._validate_request(
            method=method,
            endpoint_kind=endpoint_kind,
            path=path,
            path_template=path_template,
            query_parameters=query_parameters,
            json_body=json_body,
        )
        if self.request_count >= self.maximum_request_count:
            raise RuntimeError("OpenSearch probe request budget exhausted")
        self.request_count += 1
        started = time.perf_counter()
        response = self._client.request(
            method,
            path,
            params=dict(query_parameters),
            json=json_body,
        )
        latency_ms = (time.perf_counter() - started) * 1000
        content = response.content
        if len(content) > self.maximum_response_bytes:
            raise RuntimeError("OpenSearch probe response exceeds byte bound")
        response_sha256 = hashlib.sha256(content).hexdigest()
        body_schema_sha256 = request_body_schema_sha256_v0221(json_body)
        query_names = tuple(sorted(query_parameters))
        if not 200 <= response.status_code < 300:
            envelope = build_opensearch_http_error_envelope_v0221(
                http_status=response.status_code,
                response_body=content,
                method=method,
                endpoint_kind=endpoint_kind,
                path_template=path_template,
                query_parameters=query_parameters,
                request_body=json_body,
            )
            attempt = OpenSearchProbeRequestAttemptV0221.build(
                ordinal=self.request_count,
                plan_id=plan_id,
                request_id=request_id,
                method=method,
                endpoint_kind=endpoint_kind,
                path_template=path_template,
                query_parameter_names=query_names,
                request_body_schema_sha256=body_schema_sha256,
                http_status=response.status_code,
                latency_ms=latency_ms,
                response_bytes=len(content),
                response_sha256=response_sha256,
                transport_retry_count=transport_retry_count,
                safe_error_envelope_sha256=envelope.envelope_sha256,
            )
            self.attempts.append(attempt)
            raise OpenSearchHttpErrorV0221(envelope, attempt)
        attempt = OpenSearchProbeRequestAttemptV0221.build(
            ordinal=self.request_count,
            plan_id=plan_id,
            request_id=request_id,
            method=method,
            endpoint_kind=endpoint_kind,
            path_template=path_template,
            query_parameter_names=query_names,
            request_body_schema_sha256=body_schema_sha256,
            http_status=response.status_code,
            latency_ms=latency_ms,
            response_bytes=len(content),
            response_sha256=response_sha256,
            transport_retry_count=transport_retry_count,
            safe_error_envelope_sha256=None,
        )
        self.attempts.append(attempt)
        try:
            payload = json.loads(content)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError("OpenSearch probe response is not JSON") from error
        return payload, content, attempt

    def close(self) -> None:
        self._client.close()


__all__ = (
    "OpenSearchHttpErrorCodeV0221",
    "OpenSearchHttpErrorEnvelopeV0221",
    "OpenSearchHttpErrorV0221",
    "OpenSearchProbeClientV0221",
    "OpenSearchRootCauseV0221",
    "build_opensearch_http_error_envelope_v0221",
    "request_body_schema_sha256_v0221",
)
