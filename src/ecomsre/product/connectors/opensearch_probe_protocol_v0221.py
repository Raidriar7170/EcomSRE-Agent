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
    FIELD_CAPS_GET_HTTP_400 = "FIELD_CAPS_GET_HTTP_400"
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
    semantic_plan_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
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
        expected_semantic = semantic_sha256_v22(
            {
                "mapping_request": _semantic_request_payload_v0221(
                    self.mapping_request
                ),
                "field_caps_request": (
                    _semantic_request_payload_v0221(self.field_caps_request)
                    if self.field_caps_request is not None
                    else None
                ),
                "field_mapping_requests": tuple(
                    _semantic_request_payload_v0221(request)
                    for request in self.field_mapping_requests
                ),
                "aggregation_request": (
                    _semantic_request_payload_v0221(self.aggregation_request)
                    if self.aggregation_request is not None
                    else None
                ),
                "sample_requests": tuple(
                    _semantic_request_payload_v0221(request)
                    for request in self.sample_requests
                ),
                "maximum_requests": self.maximum_requests,
            }
        )
        if self.semantic_plan_sha256 != expected_semantic:
            raise ValueError("OpenSearch semantic plan digest differs")
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
        draft = cls.model_construct(
            **body,
            semantic_plan_sha256="0" * 64,
            plan_sha256="0" * 64,
        )
        semantic_source = draft.model_dump(
            mode="json",
            exclude={
                "schema_version",
                "plan_id",
                "parent_plan_id",
                "change_reason_code",
                "semantic_plan_sha256",
                "plan_sha256",
            },
        )
        for key in (
            "mapping_request",
            "field_caps_request",
            "field_mapping_requests",
            "aggregation_request",
            "sample_requests",
        ):
            semantic_source[key] = _strip_request_identity_v0221(
                semantic_source[key]
            )
        semantic_plan_sha256 = semantic_sha256_v22(semantic_source)
        serialized = draft.model_dump(
            mode="json", exclude={"semantic_plan_sha256", "plan_sha256"}
        )
        serialized["semantic_plan_sha256"] = semantic_plan_sha256
        return cls.model_validate(
            {**serialized, "plan_sha256": semantic_sha256_v22(serialized)}
        )


def _strip_request_identity_v0221(value: object) -> object:
    if isinstance(value, dict):
        return {
            key: _strip_request_identity_v0221(nested)
            for key, nested in value.items()
            if key not in {"schema_version", "request_id", "request_sha256"}
        }
    if isinstance(value, list):
        return [_strip_request_identity_v0221(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_strip_request_identity_v0221(item) for item in value)
    return value


def _semantic_request_payload_v0221(
    request: OpenSearchProbeRequestV0221,
) -> object:
    return _strip_request_identity_v0221(request.model_dump(mode="json"))


class OpenSearchProbePlanResultV0221(ProductModelV1):
    schema_version: Literal["ecomsre.product.opensearch-probe-plan-result.v0221"] = (
        "ecomsre.product.opensearch-probe-plan-result.v0221"
    )
    plan_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{2,79}$")
    request_results: tuple[OpenSearchProbeRequestAttemptV0221, ...]
    mapping_status: str = Field(min_length=1, max_length=80)
    field_caps_status: str = Field(min_length=1, max_length=80)
    sample_status: str = Field(min_length=1, max_length=80)
    profile_resolution_status: str = Field(min_length=1, max_length=80)
    terminal: str = Field(min_length=1, max_length=120)
    result_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_bound_result(self) -> "OpenSearchProbePlanResultV0221":
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"result_sha256"})
        )
        if self.result_sha256 != expected:
            raise ValueError("OpenSearch plan result digest differs")
        return self

    @classmethod
    def build(cls, **values: Any) -> "OpenSearchProbePlanResultV0221":
        payload = {
            "schema_version": "ecomsre.product.opensearch-probe-plan-result.v0221",
            **values,
        }
        draft = cls.model_construct(**payload, result_sha256="0" * 64)
        serialized = draft.model_dump(mode="json", exclude={"result_sha256"})
        return cls.model_validate(
            {**serialized, "result_sha256": semantic_sha256_v22(serialized)}
        )


class OpenSearchProbeSessionLedgerV0221:
    """In-memory changed-plan ledger; tracked artifacts persist its projections."""

    def __init__(self) -> None:
        self.plans: list[OpenSearchProbeRequestPlanV0221] = []
        self.attempts: list[OpenSearchProbeRequestAttemptV0221] = []

    def register_plan(self, plan: OpenSearchProbeRequestPlanV0221) -> None:
        if len(self.plans) >= 3:
            raise ValueError("OpenSearch changed-plan budget exhausted")
        if any(
            previous.semantic_plan_sha256 == plan.semantic_plan_sha256
            for previous in self.plans
        ):
            raise ValueError("OpenSearch semantic plan repeat is forbidden")
        if self.plans and plan.parent_plan_id != self.plans[-1].plan_id:
            raise ValueError("OpenSearch changed plan parent differs")
        if not self.plans and plan.parent_plan_id is not None:
            raise ValueError("OpenSearch initial plan cannot have a parent")
        self.plans.append(plan)

    def record_attempt(self, attempt: OpenSearchProbeRequestAttemptV0221) -> None:
        if attempt.plan_id not in {plan.plan_id for plan in self.plans}:
            raise ValueError("OpenSearch attempt plan is not registered")
        if len(self.attempts) >= 16 or attempt.ordinal != len(self.attempts) + 1:
            raise ValueError("OpenSearch request budget or ordinal differs")
        self.attempts.append(attempt)


def _request_v0221(
    *,
    request_id: str,
    endpoint_kind: OpenSearchProbeEndpointKindV0221,
    method: Literal["GET", "POST"],
    path_template: str,
    query_parameters: dict[str, str] | None = None,
    body_shape: OpenSearchProbeBodyShapeV0221 = OpenSearchProbeBodyShapeV0221.NONE,
    required: bool = True,
    fallback_rank: int = 0,
) -> OpenSearchProbeRequestV0221:
    return OpenSearchProbeRequestV0221.build(
        request_id=request_id,
        endpoint_kind=endpoint_kind,
        method=method,
        path_template=path_template,
        query_parameters=query_parameters or {},
        body_shape=body_shape,
        required=required,
        fallback_rank=fallback_rank,
    )


def build_probe_request_plan_v0221(
    *,
    variant: OpenSearchProbePlanVariantV0221,
    fields: tuple[str, ...],
    parent_plan_id: str | None,
    change_reason_code: OpenSearchProbeChangeReasonV0221,
) -> OpenSearchProbeRequestPlanV0221:
    if not fields or fields != tuple(sorted(set(fields))):
        raise ValueError("OpenSearch Field Caps fields are not canonical")
    mapping = _request_v0221(
        request_id="mapping",
        endpoint_kind=OpenSearchProbeEndpointKindV0221.MAPPING,
        method="GET",
        path_template="/{index}/_mapping",
    )
    field_caps: OpenSearchProbeRequestV0221 | None
    field_mapping: tuple[OpenSearchProbeRequestV0221, ...] = ()
    if variant is OpenSearchProbePlanVariantV0221.PLAN_A_FIELD_CAPS_GET_QUERY:
        if parent_plan_id is not None or change_reason_code not in {
            OpenSearchProbeChangeReasonV0221.INITIAL_OFFICIAL_PROTOCOL,
            OpenSearchProbeChangeReasonV0221.FIELD_CAPS_FIELDS_BODY_HTTP_400,
        }:
            raise ValueError("OpenSearch Plan A lineage differs")
        plan_id = "plan-a-field-caps-get-query"
        method: Literal["GET", "POST"] = "GET"
    elif variant is OpenSearchProbePlanVariantV0221.PLAN_B_FIELD_CAPS_POST_QUERY:
        if parent_plan_id is None or change_reason_code not in {
            OpenSearchProbeChangeReasonV0221.FIELD_CAPS_METHOD_NOT_ALLOWED,
            OpenSearchProbeChangeReasonV0221.FIELD_CAPS_GET_HTTP_400,
            OpenSearchProbeChangeReasonV0221.RESPONSE_SHAPE_INCOMPATIBLE,
        }:
            raise ValueError("OpenSearch Plan B evidence differs")
        plan_id = "plan-b-field-caps-post-query"
        method = "POST"
    else:
        if parent_plan_id is None or change_reason_code not in {
            OpenSearchProbeChangeReasonV0221.FIELD_CAPS_PERMISSION_DENIED,
            OpenSearchProbeChangeReasonV0221.FIELD_CAPS_ENDPOINT_NOT_FOUND,
            OpenSearchProbeChangeReasonV0221.FIELD_CAPS_UNSUPPORTED,
        }:
            raise ValueError("OpenSearch Plan C requires Field Caps unavailability")
        plan_id = "plan-c-mapping-sample-empirical"
        field_caps = None
        field_mapping = (
            _request_v0221(
                request_id="focused-field-mapping",
                endpoint_kind=OpenSearchProbeEndpointKindV0221.FIELD_MAPPING,
                method="GET",
                path_template="/{index}/_mapping/field/{candidate_fields}",
                required=False,
                fallback_rank=1,
            ),
        )
        return OpenSearchProbeRequestPlanV0221.build(
            plan_id=plan_id,
            parent_plan_id=parent_plan_id,
            change_reason_code=change_reason_code,
            mapping_request=mapping,
            field_caps_request=field_caps,
            field_mapping_requests=field_mapping,
            aggregation_request=_request_v0221(
                request_id="service-aggregation",
                endpoint_kind=OpenSearchProbeEndpointKindV0221.SERVICE_AGGREGATION,
                method="POST",
                path_template="/{index}/_search",
                body_shape=OpenSearchProbeBodyShapeV0221.SEARCH_AGGREGATION,
            ),
            sample_requests=(
                _request_v0221(
                    request_id="broad-sample",
                    endpoint_kind=OpenSearchProbeEndpointKindV0221.SAMPLE_SEARCH,
                    method="POST",
                    path_template="/{index}/_search",
                    body_shape=OpenSearchProbeBodyShapeV0221.SEARCH_SAMPLE,
                ),
                _request_v0221(
                    request_id="timestamp-range-verification",
                    endpoint_kind=OpenSearchProbeEndpointKindV0221.TIMESTAMP_RANGE,
                    method="POST",
                    path_template="/{index}/_search",
                    body_shape=OpenSearchProbeBodyShapeV0221.SEARCH_VERIFICATION,
                ),
                _request_v0221(
                    request_id="checkout-sample",
                    endpoint_kind=OpenSearchProbeEndpointKindV0221.SAMPLE_SEARCH,
                    method="POST",
                    path_template="/{index}/_search",
                    body_shape=OpenSearchProbeBodyShapeV0221.SEARCH_SAMPLE,
                ),
                _request_v0221(
                    request_id="profile-verification",
                    endpoint_kind=OpenSearchProbeEndpointKindV0221.PROFILE_VERIFICATION,
                    method="POST",
                    path_template="/{index}/_search",
                    body_shape=OpenSearchProbeBodyShapeV0221.SEARCH_VERIFICATION,
                ),
            ),
            maximum_requests=16,
        )
    field_caps = _request_v0221(
        request_id=(
            "field-caps-get-query" if method == "GET" else "field-caps-post-query"
        ),
        endpoint_kind=OpenSearchProbeEndpointKindV0221.FIELD_CAPS,
        method=method,
        path_template="/{index}/_field_caps",
        query_parameters={"fields": ",".join(fields)},
        required=False,
    )
    return OpenSearchProbeRequestPlanV0221.build(
        plan_id=plan_id,
        parent_plan_id=parent_plan_id,
        change_reason_code=change_reason_code,
        mapping_request=mapping,
        field_caps_request=field_caps,
        field_mapping_requests=(),
        aggregation_request=_request_v0221(
            request_id="service-aggregation",
            endpoint_kind=OpenSearchProbeEndpointKindV0221.SERVICE_AGGREGATION,
            method="POST",
            path_template="/{index}/_search",
            body_shape=OpenSearchProbeBodyShapeV0221.SEARCH_AGGREGATION,
        ),
        sample_requests=(
            _request_v0221(
                request_id="timestamp-range-verification",
                endpoint_kind=OpenSearchProbeEndpointKindV0221.TIMESTAMP_RANGE,
                method="POST",
                path_template="/{index}/_search",
                body_shape=OpenSearchProbeBodyShapeV0221.SEARCH_VERIFICATION,
            ),
            _request_v0221(
                request_id="checkout-sample",
                endpoint_kind=OpenSearchProbeEndpointKindV0221.SAMPLE_SEARCH,
                method="POST",
                path_template="/{index}/_search",
                body_shape=OpenSearchProbeBodyShapeV0221.SEARCH_SAMPLE,
            ),
            _request_v0221(
                request_id="profile-verification",
                endpoint_kind=OpenSearchProbeEndpointKindV0221.PROFILE_VERIFICATION,
                method="POST",
                path_template="/{index}/_search",
                body_shape=OpenSearchProbeBodyShapeV0221.SEARCH_VERIFICATION,
            ),
        ),
        maximum_requests=16,
    )


def select_next_request_plan_variant_v0221(
    envelope: "OpenSearchHttpErrorEnvelopeV0221",
    *,
    current_variant: OpenSearchProbePlanVariantV0221 | None = None,
) -> OpenSearchProbePlanVariantV0221 | None:
    """Choose only the next evidence-authorized request-plan variant."""

    from ecomsre.product.connectors.opensearch_http_v0221 import (
        OpenSearchHttpErrorCodeV0221,
    )

    if envelope.endpoint_kind is not OpenSearchProbeEndpointKindV0221.FIELD_CAPS:
        return None
    if (
        current_variant is None
        and envelope.method == "POST"
        and envelope.query_parameter_names == ()
        and envelope.safe_error_code
        in {
            OpenSearchHttpErrorCodeV0221.OPENSEARCH_REQUEST_PARAMETER_INVALID,
            OpenSearchHttpErrorCodeV0221.OPENSEARCH_REQUEST_BODY_INVALID,
        }
    ):
        return OpenSearchProbePlanVariantV0221.PLAN_A_FIELD_CAPS_GET_QUERY
    if (
        current_variant
        is OpenSearchProbePlanVariantV0221.PLAN_A_FIELD_CAPS_GET_QUERY
        and envelope.safe_error_code
        in {
            OpenSearchHttpErrorCodeV0221.OPENSEARCH_METHOD_NOT_ALLOWED,
            OpenSearchHttpErrorCodeV0221.OPENSEARCH_REQUEST_PARAMETER_INVALID,
            OpenSearchHttpErrorCodeV0221.OPENSEARCH_REQUEST_BODY_INVALID,
        }
    ):
        return OpenSearchProbePlanVariantV0221.PLAN_B_FIELD_CAPS_POST_QUERY
    if (
        current_variant
        in {
            OpenSearchProbePlanVariantV0221.PLAN_A_FIELD_CAPS_GET_QUERY,
            OpenSearchProbePlanVariantV0221.PLAN_B_FIELD_CAPS_POST_QUERY,
        }
        and envelope.safe_error_code
        in {
            OpenSearchHttpErrorCodeV0221.OPENSEARCH_PERMISSION_DENIED,
            OpenSearchHttpErrorCodeV0221.OPENSEARCH_ENDPOINT_NOT_FOUND,
            OpenSearchHttpErrorCodeV0221.OPENSEARCH_FIELD_CAPS_UNSUPPORTED,
            OpenSearchHttpErrorCodeV0221.OPENSEARCH_REQUEST_PARAMETER_INVALID,
            OpenSearchHttpErrorCodeV0221.OPENSEARCH_REQUEST_BODY_INVALID,
        }
    ):
        return OpenSearchProbePlanVariantV0221.PLAN_C_MAPPING_SAMPLE_EMPIRICAL
    return None


__all__ = (
    "OpenSearchProbeBodyShapeV0221",
    "OpenSearchProbeChangeReasonV0221",
    "OpenSearchProbeEndpointKindV0221",
    "OpenSearchProbePlanVariantV0221",
    "OpenSearchProbePlanResultV0221",
    "OpenSearchProbeRequestAttemptV0221",
    "OpenSearchProbeRequestPlanV0221",
    "OpenSearchProbeRequestV0221",
    "OpenSearchProbeSessionLedgerV0221",
    "build_probe_request_plan_v0221",
    "select_next_request_plan_variant_v0221",
)
