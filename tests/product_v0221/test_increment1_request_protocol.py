from __future__ import annotations

from pydantic import ValidationError
import pytest

from ecomsre.product.connectors.opensearch_http_v0221 import (
    OpenSearchHttpErrorCodeV0221,
    OpenSearchHttpErrorEnvelopeV0221,
)
from ecomsre.product.connectors.opensearch_probe_protocol_v0221 import (
    OpenSearchProbeBodyShapeV0221,
    OpenSearchProbeChangeReasonV0221,
    OpenSearchProbeEndpointKindV0221,
    OpenSearchProbePlanVariantV0221,
    OpenSearchProbeRequestAttemptV0221,
    OpenSearchProbeRequestPlanV0221,
    OpenSearchProbeRequestV0221,
    select_next_request_plan_variant_v0221,
)


def _request(
    *,
    request_id: str,
    endpoint: OpenSearchProbeEndpointKindV0221,
    method: str,
    path: str,
    query: dict[str, str] | None = None,
    body_shape: OpenSearchProbeBodyShapeV0221 = OpenSearchProbeBodyShapeV0221.NONE,
    required: bool = True,
    rank: int = 0,
) -> OpenSearchProbeRequestV0221:
    return OpenSearchProbeRequestV0221.build(
        request_id=request_id,
        endpoint_kind=endpoint,
        method=method,
        path_template=path,
        query_parameters=query or {},
        body_shape=body_shape,
        required=required,
        fallback_rank=rank,
    )


def test_official_plan_a_uses_get_field_caps_query_parameter() -> None:
    mapping = _request(
        request_id="mapping",
        endpoint=OpenSearchProbeEndpointKindV0221.MAPPING,
        method="GET",
        path="/{index}/_mapping",
    )
    field_caps = _request(
        request_id="field-caps-get-query",
        endpoint=OpenSearchProbeEndpointKindV0221.FIELD_CAPS,
        method="GET",
        path="/{index}/_field_caps",
        query={"fields": "observed.timestamp,resource.service.name.keyword"},
        required=False,
    )
    plan = OpenSearchProbeRequestPlanV0221.build(
        plan_id="plan-a-official-field-caps-get",
        parent_plan_id="predecessor-fields-body",
        change_reason_code=(
            OpenSearchProbeChangeReasonV0221.FIELD_CAPS_FIELDS_BODY_HTTP_400
        ),
        mapping_request=mapping,
        field_caps_request=field_caps,
        field_mapping_requests=(),
        aggregation_request=None,
        sample_requests=(),
        maximum_requests=16,
    )

    assert plan.field_caps_request is not None
    assert plan.field_caps_request.method == "GET"
    assert plan.field_caps_request.query_parameters == {
        "fields": "observed.timestamp,resource.service.name.keyword"
    }
    assert plan.field_caps_request.body_shape is OpenSearchProbeBodyShapeV0221.NONE
    assert len(plan.plan_sha256) == 64


def test_field_caps_fields_body_is_forbidden() -> None:
    with pytest.raises(ValidationError, match="fields.*body"):
        _request(
            request_id="invalid-fields-body",
            endpoint=OpenSearchProbeEndpointKindV0221.FIELD_CAPS,
            method="POST",
            path="/{index}/_field_caps",
            body_shape=OpenSearchProbeBodyShapeV0221.FIELDS_BODY,
        )


def test_predecessor_400_selects_official_plan_a_deterministically() -> None:
    envelope = OpenSearchHttpErrorEnvelopeV0221.build(
        http_status=400,
        error_type="illegal_argument_exception",
        error_reason="request body does not support [fields]",
        root_causes=(),
        method="POST",
        endpoint_kind=OpenSearchProbeEndpointKindV0221.FIELD_CAPS,
        path_template="/{index}/_field_caps",
        query_parameter_names=(),
        request_body_schema_sha256="1" * 64,
        response_body_sha256="2" * 64,
        response_bytes=120,
        safe_error_code=(
            OpenSearchHttpErrorCodeV0221.OPENSEARCH_REQUEST_BODY_INVALID
        ),
    )

    assert select_next_request_plan_variant_v0221(envelope) is (
        OpenSearchProbePlanVariantV0221.PLAN_A_FIELD_CAPS_GET_QUERY
    )


def test_attempt_digest_binds_safe_request_and_response_metadata() -> None:
    attempt = OpenSearchProbeRequestAttemptV0221.build(
        ordinal=1,
        plan_id="plan-a-official-field-caps-get",
        request_id="field-caps-get-query",
        method="GET",
        endpoint_kind=OpenSearchProbeEndpointKindV0221.FIELD_CAPS,
        path_template="/{index}/_field_caps",
        query_parameter_names=("fields",),
        request_body_schema_sha256="1" * 64,
        http_status=400,
        latency_ms=4.5,
        response_bytes=120,
        response_sha256="2" * 64,
        transport_retry_count=0,
        safe_error_envelope_sha256="3" * 64,
    )

    assert attempt.query_parameter_names == ("fields",)
    assert len(attempt.attempt_sha256) == 64
    assert "observed.timestamp" not in attempt.model_dump_json()
