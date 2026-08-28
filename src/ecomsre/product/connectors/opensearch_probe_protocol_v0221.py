"""Bounded OpenSearch request-plan contracts for Product v0.2.2.1."""

from __future__ import annotations

from enum import Enum
import re
from typing import Any, TYPE_CHECKING, Literal

from pydantic import Field, field_validator, model_validator

from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22
from ecomsre.product.contracts import ProductModelV1


if TYPE_CHECKING:
    from ecomsre.product.connectors.opensearch_http_v0221 import (
        OpenSearchHttpErrorEnvelopeV0221,
    )


_SECRET_PARAMETER = re.compile(
    r"(?:^|[_-])(?:api[_-]?key|authorization|credential|password|secret|token)(?:$|[_-])",
    re.I,
)


class OpenSearchProbeEndpointKindV0221(str, Enum):
    MAPPING = "MAPPING"
    FIELD_MAPPING = "FIELD_MAPPING"
    FIELD_CAPS = "FIELD_CAPS"
    SERVICE_AGGREGATION = "SERVICE_AGGREGATION"
    TIMESTAMP_RANGE = "TIMESTAMP_RANGE"
    SAMPLE_SEARCH = "SAMPLE_SEARCH"
    PROFILE_VERIFICATION = "PROFILE_VERIFICATION"


class OpenSearchProbeBodyShapeV0221(str, Enum):
    NONE = "NONE"
    INDEX_FILTER = "INDEX_FILTER"
    SEARCH_AGGREGATION = "SEARCH_AGGREGATION"
    SEARCH_SAMPLE = "SEARCH_SAMPLE"
    SEARCH_VERIFICATION = "SEARCH_VERIFICATION"
    FIELDS_BODY = "FIELDS_BODY"


class OpenSearchProbeChangeReasonV0221(str, Enum):
    INITIAL_OFFICIAL_PROTOCOL = "INITIAL_OFFICIAL_PROTOCOL"
    FIELD_CAPS_FIELDS_BODY_HTTP_400 = "FIELD_CAPS_FIELDS_BODY_HTTP_400"
    FIELD_CAPS_METHOD_NOT_ALLOWED = "FIELD_CAPS_METHOD_NOT_ALLOWED"
    FIELD_CAPS_ENDPOINT_NOT_FOUND = "FIELD_CAPS_ENDPOINT_NOT_FOUND"
    FIELD_CAPS_PERMISSION_DENIED = "FIELD_CAPS_PERMISSION_DENIED"
    FIELD_CAPS_UNSUPPORTED = "FIELD_CAPS_UNSUPPORTED"
    RESPONSE_SHAPE_INCOMPATIBLE = "RESPONSE_SHAPE_INCOMPATIBLE"


class OpenSearchProbePlanVariantV0221(str, Enum):
    PLAN_A_FIELD_CAPS_GET_QUERY = "PLAN_A_FIELD_CAPS_GET_QUERY"
    PLAN_B_FIELD_CAPS_POST_QUERY = "PLAN_B_FIELD_CAPS_POST_QUERY"
    PLAN_C_MAPPING_SAMPLE_EMPIRICAL = "PLAN_C_MAPPING_SAMPLE_EMPIRICAL"


class OpenSearchProbeRequestV0221(ProductModelV1):
    schema_version: Literal["ecomsre.product.opensearch-probe-request.v0221"] = (
        "ecomsre.product.opensearch-probe-request.v0221"
    )
    request_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{2,79}$")
    endpoint_kind: OpenSearchProbeEndpointKindV0221
    method: Literal["GET", "POST"]
    path_template: str = Field(min_length=2, max_length=255)
    query_parameters: dict[str, str]
    body_shape: OpenSearchProbeBodyShapeV0221
    required: bool
    fallback_rank: int = Field(ge=0, le=20)
    request_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("path_template")
    @classmethod
    def path_template_is_safe(cls, value: str) -> str:
        if (
            not value.startswith("/")
            or "?" in value
            or "#" in value
            or ".." in value
            or "\r" in value
            or "\n" in value
        ):
            raise ValueError("OpenSearch request path template is invalid")
        return value

    @field_validator("query_parameters")
    @classmethod
    def query_parameters_are_bounded(cls, value: dict[str, str]) -> dict[str, str]:
        if len(value) > 10:
            raise ValueError("OpenSearch request query parameter count is invalid")
        for name, parameter_value in value.items():
            if (
                not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]{0,79}", name)
                or _SECRET_PARAMETER.search(name)
                or not parameter_value
                or len(parameter_value) > 4000
                or any(character in parameter_value for character in "\r\n\x00")
            ):
                raise ValueError("OpenSearch request query parameter is invalid")
        return dict(sorted(value.items()))

    @model_validator(mode="after")
    def request_contract_is_consistent(self) -> "OpenSearchProbeRequestV0221":
        if self.endpoint_kind is OpenSearchProbeEndpointKindV0221.FIELD_CAPS:
            if self.body_shape is OpenSearchProbeBodyShapeV0221.FIELDS_BODY:
                raise ValueError("OpenSearch Field Caps fields in request body are forbidden")
            if set(self.query_parameters) != {"fields"}:
                raise ValueError("OpenSearch Field Caps requires the fields query parameter")
            if self.body_shape not in {
                OpenSearchProbeBodyShapeV0221.NONE,
                OpenSearchProbeBodyShapeV0221.INDEX_FILTER,
            }:
                raise ValueError("OpenSearch Field Caps request body shape is invalid")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"request_sha256"})
        )
        if self.request_sha256 != expected:
            raise ValueError("OpenSearch request digest differs")
        return self

    @classmethod
    def build(cls, **values: Any) -> "OpenSearchProbeRequestV0221":
        body = {
            "schema_version": "ecomsre.product.opensearch-probe-request.v0221",
            **values,
        }
        draft = cls.model_construct(**body, request_sha256="0" * 64)
        serialized = draft.model_dump(mode="json", exclude={"request_sha256"})
        return cls.model_validate(
            {**serialized, "request_sha256": semantic_sha256_v22(serialized)}
        )


class OpenSearchProbeRequestAttemptV0221(ProductModelV1):
    schema_version: Literal["ecomsre.product.opensearch-probe-attempt.v0221"] = (
        "ecomsre.product.opensearch-probe-attempt.v0221"
    )
    ordinal: int = Field(ge=1, le=16)
    plan_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{2,79}$")
    request_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{2,79}$")
    method: Literal["GET", "POST"]
    endpoint_kind: OpenSearchProbeEndpointKindV0221
    path_template: str = Field(min_length=2, max_length=255)
    query_parameter_names: tuple[str, ...] = Field(max_length=10)
    request_body_schema_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    http_status: int | None = Field(default=None, ge=100, le=599)
    latency_ms: float = Field(ge=0, le=120_000, allow_inf_nan=False)
    response_bytes: int = Field(ge=0, le=2_000_000)
    response_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    transport_retry_count: int = Field(ge=0, le=2)
    safe_error_envelope_sha256: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    attempt_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_bound_attempt(self) -> "OpenSearchProbeRequestAttemptV0221":
        if self.query_parameter_names != tuple(sorted(set(self.query_parameter_names))):
            raise ValueError("OpenSearch attempt query names are not canonical")
        if any(_SECRET_PARAMETER.search(name) for name in self.query_parameter_names):
            raise ValueError("OpenSearch attempt query name is secret-bearing")
        is_error = self.http_status is not None and not 200 <= self.http_status < 300
        if is_error != (self.safe_error_envelope_sha256 is not None):
            raise ValueError("OpenSearch attempt error-envelope binding differs")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"attempt_sha256"})
        )
        if self.attempt_sha256 != expected:
            raise ValueError("OpenSearch attempt digest differs")
        return self

    @classmethod
    def build(cls, **values: Any) -> "OpenSearchProbeRequestAttemptV0221":
        body = {
            "schema_version": "ecomsre.product.opensearch-probe-attempt.v0221",
            **values,
        }
        draft = cls.model_construct(**body, attempt_sha256="0" * 64)
        serialized = draft.model_dump(mode="json", exclude={"attempt_sha256"})
        return cls.model_validate(
            {**serialized, "attempt_sha256": semantic_sha256_v22(serialized)}
        )


class OpenSearchProbeRequestPlanV0221(ProductModelV1):
    schema_version: Literal["ecomsre.product.opensearch-probe-plan.v0221"] = (
        "ecomsre.product.opensearch-probe-plan.v0221"
    )
    plan_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{2,79}$")
    parent_plan_id: str | None = Field(
        default=None,
        pattern=r"^[a-z0-9][a-z0-9-]{2,79}$",
    )
    change_reason_code: OpenSearchProbeChangeReasonV0221
    mapping_request: OpenSearchProbeRequestV0221
    field_caps_request: OpenSearchProbeRequestV0221 | None
    field_mapping_requests: tuple[OpenSearchProbeRequestV0221, ...]
    aggregation_request: OpenSearchProbeRequestV0221 | None
    sample_requests: tuple[OpenSearchProbeRequestV0221, ...]
    maximum_requests: Literal[16]
    plan_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_bound_plan(self) -> "OpenSearchProbeRequestPlanV0221":
        requests = (
            (self.mapping_request,)
            + ((self.field_caps_request,) if self.field_caps_request else ())
            + self.field_mapping_requests
            + ((self.aggregation_request,) if self.aggregation_request else ())
            + self.sample_requests
        )
        if self.mapping_request.endpoint_kind is not OpenSearchProbeEndpointKindV0221.MAPPING:
            raise ValueError("OpenSearch plan mapping request differs")
        if self.field_caps_request is not None and self.field_caps_request.endpoint_kind is not (
            OpenSearchProbeEndpointKindV0221.FIELD_CAPS
        ):
            raise ValueError("OpenSearch plan Field Caps request differs")
        request_ids = tuple(request.request_id for request in requests)
        if len(request_ids) != len(set(request_ids)):
            raise ValueError("OpenSearch plan request IDs are duplicated")
        if len(requests) > self.maximum_requests:
            raise ValueError("OpenSearch plan exceeds the request budget")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"plan_sha256"})
        )
        if self.plan_sha256 != expected:
            raise ValueError("OpenSearch plan digest differs")
        return self

    @classmethod
    def build(cls, **values: Any) -> "OpenSearchProbeRequestPlanV0221":
        body = {
            "schema_version": "ecomsre.product.opensearch-probe-plan.v0221",
            **values,
        }
        draft = cls.model_construct(**body, plan_sha256="0" * 64)
        serialized = draft.model_dump(mode="json", exclude={"plan_sha256"})
        return cls.model_validate(
            {**serialized, "plan_sha256": semantic_sha256_v22(serialized)}
        )


def select_next_request_plan_variant_v0221(
    envelope: "OpenSearchHttpErrorEnvelopeV0221",
) -> OpenSearchProbePlanVariantV0221 | None:
    """Choose only the next evidence-authorized request-plan variant."""

    from ecomsre.product.connectors.opensearch_http_v0221 import (
        OpenSearchHttpErrorCodeV0221,
    )

    if envelope.endpoint_kind is not OpenSearchProbeEndpointKindV0221.FIELD_CAPS:
        return None
    if envelope.method == "POST" and envelope.query_parameter_names == () and (
        envelope.safe_error_code
        in {
            OpenSearchHttpErrorCodeV0221.OPENSEARCH_REQUEST_PARAMETER_INVALID,
            OpenSearchHttpErrorCodeV0221.OPENSEARCH_REQUEST_BODY_INVALID,
        }
    ):
        return OpenSearchProbePlanVariantV0221.PLAN_A_FIELD_CAPS_GET_QUERY
    if envelope.safe_error_code is OpenSearchHttpErrorCodeV0221.OPENSEARCH_METHOD_NOT_ALLOWED:
        return OpenSearchProbePlanVariantV0221.PLAN_B_FIELD_CAPS_POST_QUERY
    if envelope.safe_error_code in {
        OpenSearchHttpErrorCodeV0221.OPENSEARCH_PERMISSION_DENIED,
        OpenSearchHttpErrorCodeV0221.OPENSEARCH_ENDPOINT_NOT_FOUND,
        OpenSearchHttpErrorCodeV0221.OPENSEARCH_FIELD_CAPS_UNSUPPORTED,
    }:
        return OpenSearchProbePlanVariantV0221.PLAN_C_MAPPING_SAMPLE_EMPIRICAL
    return None


__all__ = (
    "OpenSearchProbeBodyShapeV0221",
    "OpenSearchProbeChangeReasonV0221",
    "OpenSearchProbeEndpointKindV0221",
    "OpenSearchProbePlanVariantV0221",
    "OpenSearchProbeRequestAttemptV0221",
    "OpenSearchProbeRequestPlanV0221",
    "OpenSearchProbeRequestV0221",
    "select_next_request_plan_variant_v0221",
)
