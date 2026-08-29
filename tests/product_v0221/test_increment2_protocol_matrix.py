from __future__ import annotations

import json

import httpx
import pytest

from ecomsre.product.connectors.opensearch_http_v0221 import (
    OpenSearchHttpErrorV0221,
    OpenSearchProbeClientV0221,
)
from ecomsre.product.connectors.opensearch_probe_protocol_v0221 import (
    OpenSearchProbeChangeReasonV0221,
    OpenSearchProbeEndpointKindV0221,
    OpenSearchProbePlanVariantV0221,
    OpenSearchProbeSessionLedgerV0221,
    build_probe_request_plan_v0221,
    select_next_request_plan_variant_v0221,
)


FIELDS = ("observed.timestamp", "resource.service.name.keyword")


def _error(status: int, error_type: str, reason: str) -> dict[str, object]:
    return {
        "error": {
            "type": error_type,
            "reason": reason,
            "root_cause": [{"type": error_type, "reason": reason}],
        },
        "status": status,
    }


def _run_field_caps_error(
    *,
    variant: OpenSearchProbePlanVariantV0221,
    status: int,
    error_type: str,
    reason: str,
) -> OpenSearchHttpErrorV0221:
    plan = build_probe_request_plan_v0221(
        variant=variant,
        fields=FIELDS,
        parent_plan_id=(
            None
            if variant is OpenSearchProbePlanVariantV0221.PLAN_A_FIELD_CAPS_GET_QUERY
            else "plan-a-field-caps-get-query"
        ),
        change_reason_code=(
            OpenSearchProbeChangeReasonV0221.INITIAL_OFFICIAL_PROTOCOL
            if variant is OpenSearchProbePlanVariantV0221.PLAN_A_FIELD_CAPS_GET_QUERY
            else OpenSearchProbeChangeReasonV0221.FIELD_CAPS_METHOD_NOT_ALLOWED
        ),
    )
    request = plan.field_caps_request
    assert request is not None
    client = OpenSearchProbeClientV0221(
        base_url="http://127.0.0.1:19200",
        maximum_request_count=16,
        maximum_response_bytes=2_000_000,
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(
                status,
                content=json.dumps(_error(status, error_type, reason)).encode(),
            )
        ),
    )
    try:
        with pytest.raises(OpenSearchHttpErrorV0221) as raised:
            client.request_json(
                plan_id=plan.plan_id,
                request_id=request.request_id,
                method=request.method,
                endpoint_kind=request.endpoint_kind,
                path="/otel-v1-apm-span-*/_field_caps",
                path_template=request.path_template,
                query_parameters=request.query_parameters,
                json_body=None,
            )
        return raised.value
    finally:
        client.close()


def test_plan_a_and_b_use_official_query_parameter_forms_without_body() -> None:
    plan_a = build_probe_request_plan_v0221(
        variant=OpenSearchProbePlanVariantV0221.PLAN_A_FIELD_CAPS_GET_QUERY,
        fields=FIELDS,
        parent_plan_id=None,
        change_reason_code=OpenSearchProbeChangeReasonV0221.INITIAL_OFFICIAL_PROTOCOL,
    )
    plan_b = build_probe_request_plan_v0221(
        variant=OpenSearchProbePlanVariantV0221.PLAN_B_FIELD_CAPS_POST_QUERY,
        fields=FIELDS,
        parent_plan_id=plan_a.plan_id,
        change_reason_code=(
            OpenSearchProbeChangeReasonV0221.FIELD_CAPS_METHOD_NOT_ALLOWED
        ),
    )

    assert plan_a.field_caps_request is not None
    assert plan_a.field_caps_request.method == "GET"
    assert plan_b.field_caps_request is not None
    assert plan_b.field_caps_request.method == "POST"
    for request in (plan_a.field_caps_request, plan_b.field_caps_request):
        assert request.query_parameters == {"fields": ",".join(FIELDS)}
        assert request.body_shape.value == "NONE"


def test_typed_field_caps_failures_select_only_evidence_authorized_next_plan() -> None:
    method_error = _run_field_caps_error(
        variant=OpenSearchProbePlanVariantV0221.PLAN_A_FIELD_CAPS_GET_QUERY,
        status=405,
        error_type="method_not_allowed_exception",
        reason="method GET is not allowed",
    )
    permission_error = _run_field_caps_error(
        variant=OpenSearchProbePlanVariantV0221.PLAN_A_FIELD_CAPS_GET_QUERY,
        status=403,
        error_type="security_exception",
        reason="no permissions for field caps",
    )
    post_400 = _run_field_caps_error(
        variant=OpenSearchProbePlanVariantV0221.PLAN_B_FIELD_CAPS_POST_QUERY,
        status=400,
        error_type="illegal_argument_exception",
        reason="field capabilities are unsupported by this proxy",
    )

    assert select_next_request_plan_variant_v0221(
        method_error.envelope,
        current_variant=OpenSearchProbePlanVariantV0221.PLAN_A_FIELD_CAPS_GET_QUERY,
    ) is OpenSearchProbePlanVariantV0221.PLAN_B_FIELD_CAPS_POST_QUERY
    assert select_next_request_plan_variant_v0221(
        permission_error.envelope,
        current_variant=OpenSearchProbePlanVariantV0221.PLAN_A_FIELD_CAPS_GET_QUERY,
    ) is OpenSearchProbePlanVariantV0221.PLAN_C_MAPPING_SAMPLE_EMPIRICAL
    assert select_next_request_plan_variant_v0221(
        post_400.envelope,
        current_variant=OpenSearchProbePlanVariantV0221.PLAN_B_FIELD_CAPS_POST_QUERY,
    ) is OpenSearchProbePlanVariantV0221.PLAN_C_MAPPING_SAMPLE_EMPIRICAL


def test_session_rejects_semantic_plan_repeats_and_more_than_three_plans() -> None:
    ledger = OpenSearchProbeSessionLedgerV0221()
    plan_a = build_probe_request_plan_v0221(
        variant=OpenSearchProbePlanVariantV0221.PLAN_A_FIELD_CAPS_GET_QUERY,
        fields=FIELDS,
        parent_plan_id=None,
        change_reason_code=OpenSearchProbeChangeReasonV0221.INITIAL_OFFICIAL_PROTOCOL,
    )
    ledger.register_plan(plan_a)

    duplicate = plan_a.model_copy(
        update={
            "plan_id": "plan-a-renamed-but-semantically-identical",
            "parent_plan_id": plan_a.plan_id,
            "change_reason_code": (
                OpenSearchProbeChangeReasonV0221.RESPONSE_SHAPE_INCOMPATIBLE
            ),
        }
    )
    with pytest.raises(ValueError, match="semantic plan repeat"):
        ledger.register_plan(duplicate)

    plan_b = build_probe_request_plan_v0221(
        variant=OpenSearchProbePlanVariantV0221.PLAN_B_FIELD_CAPS_POST_QUERY,
        fields=FIELDS,
        parent_plan_id=plan_a.plan_id,
        change_reason_code=(
            OpenSearchProbeChangeReasonV0221.FIELD_CAPS_METHOD_NOT_ALLOWED
        ),
    )
    plan_c = build_probe_request_plan_v0221(
        variant=OpenSearchProbePlanVariantV0221.PLAN_C_MAPPING_SAMPLE_EMPIRICAL,
        fields=FIELDS,
        parent_plan_id=plan_b.plan_id,
        change_reason_code=OpenSearchProbeChangeReasonV0221.FIELD_CAPS_UNSUPPORTED,
    )
    ledger.register_plan(plan_b)
    ledger.register_plan(plan_c)

    fourth = plan_c.model_copy(
        update={
            "plan_id": "plan-c-fourth",
            "semantic_plan_sha256": "f" * 64,
            "plan_sha256": "e" * 64,
        }
    )
    with pytest.raises(ValueError, match="changed-plan budget"):
        ledger.register_plan(fourth)


def test_plan_c_requires_field_caps_unavailability_evidence() -> None:
    with pytest.raises(ValueError, match="Plan C.*unavailability"):
        build_probe_request_plan_v0221(
            variant=OpenSearchProbePlanVariantV0221.PLAN_C_MAPPING_SAMPLE_EMPIRICAL,
            fields=FIELDS,
            parent_plan_id="plan-a-field-caps-get-query",
            change_reason_code=(
                OpenSearchProbeChangeReasonV0221.RESPONSE_SHAPE_INCOMPATIBLE
            ),
        )


def test_exact_request_retries_only_transient_failures_and_caps_at_two() -> None:
    statuses = iter((503, 503, 200))

    def transient_then_success(_request: httpx.Request) -> httpx.Response:
        status = next(statuses)
        if status == 200:
            return httpx.Response(200, json={"fields": {}})
        return httpx.Response(
            status,
            json=_error(status, "unavailable_exception", "temporarily unavailable"),
        )

    client = OpenSearchProbeClientV0221(
        base_url="http://127.0.0.1:19200",
        maximum_request_count=16,
        maximum_response_bytes=2_000_000,
        transport=httpx.MockTransport(transient_then_success),
    )
    try:
        payload, _, final_attempt = client.request_json_with_transport_retries(
            maximum_transport_retries=2,
            plan_id="plan-a-field-caps-get-query",
            request_id="field-caps-get-query",
            method="GET",
            endpoint_kind=OpenSearchProbeEndpointKindV0221.FIELD_CAPS,
            path="/otel-v1-apm-span-*/_field_caps",
            path_template="/{index}/_field_caps",
            query_parameters={"fields": ",".join(FIELDS)},
            json_body=None,
        )
    finally:
        client.close()

    assert payload == {"fields": {}}
    assert client.request_count == 3
    assert tuple(attempt.transport_retry_count for attempt in client.attempts) == (
        0,
        1,
        2,
    )
    assert final_attempt.http_status == 200

    with pytest.raises(ValueError, match="retry bound"):
        client.request_json_with_transport_retries(maximum_transport_retries=3)


def test_http_400_is_not_retried_unchanged() -> None:
    calls = 0

    def invalid(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            400,
            json=_error(400, "illegal_argument_exception", "invalid parameter"),
        )

    client = OpenSearchProbeClientV0221(
        base_url="http://127.0.0.1:19200",
        maximum_request_count=16,
        maximum_response_bytes=2_000_000,
        transport=httpx.MockTransport(invalid),
    )
    try:
        with pytest.raises(OpenSearchHttpErrorV0221):
            client.request_json_with_transport_retries(
                maximum_transport_retries=2,
                plan_id="plan-a-field-caps-get-query",
                request_id="field-caps-get-query",
                method="GET",
                endpoint_kind=OpenSearchProbeEndpointKindV0221.FIELD_CAPS,
                path="/otel-v1-apm-span-*/_field_caps",
                path_template="/{index}/_field_caps",
                query_parameters={"fields": ",".join(FIELDS)},
                json_body=None,
            )
    finally:
        client.close()

    assert calls == 1
    assert client.request_count == 1


def test_transport_timeout_is_recorded_before_exact_retry() -> None:
    calls = 0

    def timeout_then_success(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise httpx.ReadTimeout("bounded timeout", request=request)
        return httpx.Response(200, json={"fields": {}})

    client = OpenSearchProbeClientV0221(
        base_url="http://127.0.0.1:19200",
        maximum_request_count=16,
        maximum_response_bytes=2_000_000,
        transport=httpx.MockTransport(timeout_then_success),
    )
    try:
        client.request_json_with_transport_retries(
            maximum_transport_retries=1,
            plan_id="plan-a-field-caps-get-query",
            request_id="field-caps-get-query",
            method="GET",
            endpoint_kind=OpenSearchProbeEndpointKindV0221.FIELD_CAPS,
            path="/otel-v1-apm-span-*/_field_caps",
            path_template="/{index}/_field_caps",
            query_parameters={"fields": ",".join(FIELDS)},
            json_body=None,
        )
    finally:
        client.close()

    assert tuple(attempt.http_status for attempt in client.attempts) == (None, 200)
    assert tuple(attempt.transport_retry_count for attempt in client.attempts) == (0, 1)


def test_client_request_count_cap_fails_closed() -> None:
    client = OpenSearchProbeClientV0221(
        base_url="http://127.0.0.1:19200",
        maximum_request_count=1,
        maximum_response_bytes=2_000_000,
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(200, json={"fields": {}})
        ),
    )
    request = {
        "plan_id": "plan-a-field-caps-get-query",
        "request_id": "field-caps-get-query",
        "method": "GET",
        "endpoint_kind": OpenSearchProbeEndpointKindV0221.FIELD_CAPS,
        "path": "/otel-v1-apm-span-*/_field_caps",
        "path_template": "/{index}/_field_caps",
        "query_parameters": {"fields": ",".join(FIELDS)},
        "json_body": None,
    }
    try:
        client.request_json(**request)  # type: ignore[arg-type]
        with pytest.raises(RuntimeError, match="request budget"):
            client.request_json(**request)  # type: ignore[arg-type]
    finally:
        client.close()
