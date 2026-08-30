from __future__ import annotations

from datetime import timedelta
from pathlib import Path
import json

import httpx
import pytest

from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22
from ecomsre.product.pilot.healthy_traffic_v0232 import (
    CheckoutTransactionObservationV0232,
    HealthyTrafficExecutionV0232,
    IncidentTrafficBindingV0232,
    HealthyTrafficProfileV0232,
    HealthyTrafficRunnerV0232,
    TrafficContractErrorV0232,
    load_checkout_traffic_contract_v0232,
)
from scripts.ci.verify_product_v0232_traffic import verify_product_v0232_traffic


ROOT = Path(__file__).resolve().parents[2]
ENDPOINT = "http://127.0.0.1:8080/api/checkout"


def _profile(*, transactions: int = 1) -> HealthyTrafficProfileV0232:
    return HealthyTrafficProfileV0232.build(
        profile_id="fixture-preflight",
        transactions=transactions,
        requests_per_second=1.0,
        request_seed=23083201,
        maximum_failures=0,
        stabilization_seconds=0,
        minimum_full_episode_duration_seconds=0,
        queue_fault_flag=0,
    )


def _cart_response(user_id: str) -> dict[str, object]:
    return {
        "userId": user_id,
        "items": [{"productId": "0PUK6V6EV0", "quantity": 1}],
    }


def _checkout_response() -> dict[str, object]:
    return {
        "orderId": "order-fixture",
        "shippingTrackingId": "tracking-fixture",
        "shippingCost": {"currencyCode": "USD", "units": 1, "nanos": 0},
        "shippingAddress": {
            "streetAddress": "1 Contract Way",
            "city": "Local",
            "state": "CA",
            "country": "United States",
            "zipCode": "94016",
        },
        "items": [
            {
                "item": {
                    "productId": "0PUK6V6EV0",
                    "quantity": 1,
                    "product": {"id": "0PUK6V6EV0"},
                },
                "cost": {"currencyCode": "USD", "units": 1, "nanos": 0},
            }
        ],
    }


def _checkout_request(user_id: str) -> dict[str, object]:
    return {
        "userId": user_id,
        "userCurrency": "USD",
        "email": f"{user_id}@example.invalid",
        "address": {
            "streetAddress": "1 Contract Way",
            "city": "Local",
            "state": "CA",
            "country": "United States",
            "zipCode": "94016",
        },
        "creditCard": {
            "creditCardNumber": "4111111111111111",
            "creditCardCvv": 123,
            "creditCardExpirationYear": 2030,
            "creditCardExpirationMonth": 12,
        },
    }


def _run(
    handler: httpx.MockTransport,
    *,
    transactions: int = 1,
):
    contract = load_checkout_traffic_contract_v0232(ROOT)
    with HealthyTrafficRunnerV0232(
        transport=handler,
        sleep=lambda _delay: None,
    ) as runner:
        return runner.run(
            endpoint=ENDPOINT,
            profile=_profile(transactions=transactions),
            contract=contract,
            role="PREFLIGHT",
        )


def test_source_bound_checkout_contract_matches_pinned_routes() -> None:
    contract = load_checkout_traffic_contract_v0232(ROOT)

    assert contract.upstream_commit == "1755859a9de82c2e5e225be68abc401a5ebf2b4f"
    assert len(contract.source_file_bindings) == 7
    assert contract.cart_method == "POST"
    assert contract.cart_path == "/api/cart"
    assert contract.cart_success_statuses == (200,)
    assert contract.checkout_method == "POST"
    assert contract.checkout_path == "/api/checkout"
    assert contract.checkout_success_statuses == (200,)
    assert contract.cart_before_checkout is True
    assert {binding.path for binding in contract.source_file_bindings} == {
        "src/frontend/gateways/Api.gateway.ts",
        "src/frontend/pages/api/cart.ts",
        "src/frontend/pages/api/checkout.ts",
        "src/frontend/protos/demo.ts",
        "src/frontend/types/Cart.ts",
        "src/load-generator/people.json",
        "src/load-generator/script.js",
    }
    assert "additionalProperties" not in contract.cart_request_schema
    assert (
        "const"
        not in contract.checkout_request_schema["properties"]["userCurrency"]
    )
    assert len(contract.contract_sha256) == 64


def test_written_traffic_contract_checkpoint_is_cross_bound() -> None:
    result = verify_product_v0232_traffic(ROOT)

    assert len(str(result.pop("contract_sha256"))) == 64
    assert result == {
        "terminal": "ECOMSRE_PRODUCT_V0232_TRAFFIC_CONTRACT_PASS",
        "source_file_binding_count": 7,
        "mocked_transaction_count": 5,
        "offline_changed_iteration_count": 2,
        "live_traffic_preflight_attempt_count": 0,
        "formal_healthy_traffic_execution_count": 0,
        "source_clone_count": 1,
    }


def test_written_traffic_contract_rejects_resealed_fixture_drift(
    tmp_path: Path,
) -> None:
    report = json.loads(
        (ROOT / "docs/analysis/product-v0232-traffic-contract.json").read_text(
            encoding="utf-8"
        )
    )
    report["mocked_transaction_count"] = 4
    report["report_sha256"] = semantic_sha256_v22(
        {key: value for key, value in report.items() if key != "report_sha256"}
    )
    drifted = tmp_path / "product-v0232-traffic-contract.json"
    drifted.write_text(json.dumps(report), encoding="utf-8")

    with pytest.raises(ValueError, match="traffic report differs"):
        verify_product_v0232_traffic(ROOT, report_path=drifted)


def test_source_bound_checkout_contract_rejects_route_drift(tmp_path: Path) -> None:
    upstream = ROOT / "third_party/opentelemetry-demo"
    copied = tmp_path / "opentelemetry-demo"
    copied.mkdir()
    for binding in load_checkout_traffic_contract_v0232(ROOT).source_file_bindings:
        destination = copied / binding.path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes((upstream / binding.path).read_bytes())
    (copied / "src/frontend/pages/api/cart.ts").write_text(
        "drift", encoding="utf-8"
    )

    with pytest.raises(TrafficContractErrorV0232, match="source drift"):
        load_checkout_traffic_contract_v0232(
            ROOT,
            upstream_root=copied,
            observed_upstream_commit="1755859a9de82c2e5e225be68abc401a5ebf2b4f",
        )


def test_successful_transaction_requires_cart_then_checkout_business_success() -> None:
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        payload = __import__("json").loads(request.content)
        if request.url.path == "/api/cart":
            return httpx.Response(200, json=_cart_response(payload["userId"]))
        return httpx.Response(200, json=_checkout_response())

    execution = _run(httpx.MockTransport(handler))
    observation = execution.observations[0]

    assert requests == ["/api/cart", "/api/checkout"]
    assert observation.business_success is True
    assert observation.failure_stage is None
    assert observation.safe_error_code is None
    assert execution.run.completed_transactions == 1
    assert execution.run.successful_transactions == 1
    assert execution.run.failed_transactions == 0
    assert execution.run.transport_retry_count == 0
    assert execution.run.passed is True
    public_execution = json.dumps(execution.model_dump(mode="json"), sort_keys=True)
    assert "4111111111111111" not in public_execution
    assert "1 Contract Way" not in public_execution


def test_cart_http_failure_stops_before_checkout_with_one_disposition() -> None:
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        return httpx.Response(503, json={"safe": "unavailable"})

    execution = _run(httpx.MockTransport(handler))
    observation = execution.observations[0]

    assert requests == ["/api/cart"]
    assert observation.business_success is False
    assert observation.failure_stage == "CART_HTTP"
    assert observation.safe_error_code == "CART_HTTP_NON_SUCCESS"
    assert observation.checkout_status is None
    assert execution.run.failed_transactions == 1
    assert execution.run.passed is False


def test_checkout_transport_failure_is_not_retried() -> None:
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        payload = __import__("json").loads(request.content)
        if request.url.path == "/api/cart":
            return httpx.Response(200, json=_cart_response(payload["userId"]))
        raise httpx.ConnectError("fixture transport failure", request=request)

    execution = _run(httpx.MockTransport(handler))
    observation = execution.observations[0]

    assert requests == ["/api/cart", "/api/checkout"]
    assert observation.failure_stage == "CHECKOUT_TRANSPORT"
    assert observation.safe_error_code == "CHECKOUT_TRANSPORT_ERROR"
    assert execution.run.transport_retry_count == 0


def test_request_schema_failures_happen_before_the_affected_transport() -> None:
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        payload = __import__("json").loads(request.content)
        return httpx.Response(200, json=_cart_response(payload["userId"]))

    contract = load_checkout_traffic_contract_v0232(ROOT)
    with HealthyTrafficRunnerV0232(
        transport=httpx.MockTransport(handler)
    ) as runner:
        cart_invalid = runner.observe_transaction(
            endpoint=ENDPOINT,
            ordinal=1,
            contract=contract,
            cart_payload={"userId": "schema-fixture"},
            checkout_payload=_checkout_request("schema-fixture"),
        )
        checkout_invalid = runner.observe_transaction(
            endpoint=ENDPOINT,
            ordinal=2,
            contract=contract,
            cart_payload={
                "userId": "schema-fixture",
                "item": {"productId": "0PUK6V6EV0", "quantity": 1},
            },
            checkout_payload={"userId": "schema-fixture"},
        )

    assert cart_invalid.safe_error_code == "CART_REQUEST_SCHEMA_INVALID"
    assert checkout_invalid.safe_error_code == "CHECKOUT_REQUEST_SCHEMA_INVALID"
    assert requests == ["/api/cart"]


def test_checkout_http_failure_has_its_own_stage() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        payload = __import__("json").loads(request.content)
        if request.url.path == "/api/cart":
            return httpx.Response(200, json=_cart_response(payload["userId"]))
        return httpx.Response(503, json={"safe": "unavailable"})

    observation = _run(httpx.MockTransport(handler)).observations[0]
    assert observation.failure_stage == "CHECKOUT_HTTP"
    assert observation.safe_error_code == "CHECKOUT_HTTP_NON_SUCCESS"


def test_runner_rejects_a_caller_configured_retrying_transport() -> None:
    retrying_transport = httpx.HTTPTransport(retries=1)
    try:
        with pytest.raises(ValueError, match="custom healthy traffic transport"):
            HealthyTrafficRunnerV0232(transport=retrying_transport)
    finally:
        retrying_transport.close()


def test_cart_response_schema_failure_is_distinct_from_business_failure() -> None:
    def schema_failure(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"items": "not-a-list"})

    invalid = _run(httpx.MockTransport(schema_failure)).observations[0]
    assert invalid.failure_stage == "CART_RESPONSE"
    assert invalid.safe_error_code == "CART_RESPONSE_SCHEMA_INVALID"

    def semantic_failure(request: httpx.Request) -> httpx.Response:
        payload = __import__("json").loads(request.content)
        return httpx.Response(200, json={"userId": payload["userId"], "items": []})

    missing = _run(httpx.MockTransport(semantic_failure)).observations[0]
    assert missing.failure_stage == "BUSINESS_SUCCESS"
    assert missing.safe_error_code == "CART_BUSINESS_SUCCESS_MISSING"


def test_duplicate_key_response_is_a_schema_failure() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=b'{"userId":"one","userId":"two","items":[]}',
            headers={"content-type": "application/json"},
        )

    observation = _run(httpx.MockTransport(handler)).observations[0]
    assert observation.safe_error_code == "CART_RESPONSE_SCHEMA_INVALID"


@pytest.mark.parametrize(
    ("response", "safe_error_code"),
    [
        ({"items": []}, "CHECKOUT_RESPONSE_SCHEMA_INVALID"),
        (
            {
                **_checkout_response(),
                "orderId": "",
            },
            "CHECKOUT_BUSINESS_SUCCESS_MISSING",
        ),
    ],
)
def test_checkout_schema_and_business_failures_are_distinct(
    response: dict[str, object],
    safe_error_code: str,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        payload = __import__("json").loads(request.content)
        if request.url.path == "/api/cart":
            return httpx.Response(200, json=_cart_response(payload["userId"]))
        return httpx.Response(200, json=response)

    observation = _run(httpx.MockTransport(handler)).observations[0]
    assert observation.safe_error_code == safe_error_code
    assert observation.failure_stage in {"CHECKOUT_RESPONSE", "BUSINESS_SUCCESS"}


def test_resealed_observation_cannot_claim_two_dispositions() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        payload = __import__("json").loads(request.content)
        if request.url.path == "/api/cart":
            return httpx.Response(200, json=_cart_response(payload["userId"]))
        return httpx.Response(200, json=_checkout_response())

    payload = _run(httpx.MockTransport(handler)).observations[0].model_dump(
        mode="json"
    )
    payload["business_success"] = False
    payload["observation_sha256"] = semantic_sha256_v22(
        {key: value for key, value in payload.items() if key != "observation_sha256"}
    )

    with pytest.raises(ValueError, match="disposition is not exact"):
        CheckoutTransactionObservationV0232.model_validate(payload)


def test_resealed_observation_cannot_mismatch_stage_and_safe_error() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"safe": "unavailable"})

    payload = _run(httpx.MockTransport(handler)).observations[0].model_dump(
        mode="json"
    )
    payload["safe_error_code"] = "CHECKOUT_RESPONSE_SCHEMA_INVALID"
    payload["observation_sha256"] = semantic_sha256_v22(
        {key: value for key, value in payload.items() if key != "observation_sha256"}
    )

    with pytest.raises(ValueError, match="stage/error disposition differs"):
        CheckoutTransactionObservationV0232.model_validate(payload)


def _reseal_execution(payload: dict[str, object]) -> dict[str, object]:
    run = payload["run"]
    assert isinstance(run, dict)
    run["result_sha256"] = semantic_sha256_v22(
        {key: value for key, value in run.items() if key != "result_sha256"}
    )
    payload["execution_sha256"] = semantic_sha256_v22(
        {key: value for key, value in payload.items() if key != "execution_sha256"}
    )
    return payload


def test_resealed_execution_cannot_turn_a_failed_observation_into_pass() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"safe": "unavailable"})

    payload = _run(httpx.MockTransport(handler)).model_dump(mode="json")
    run = payload["run"]
    assert isinstance(run, dict)
    run.update(
        successful_transactions=1,
        failed_transactions=0,
        stage_failure_counts={},
        passed=True,
    )

    with pytest.raises(ValueError, match="observation summary differs"):
        HealthyTrafficExecutionV0232.model_validate(_reseal_execution(payload))


def test_resealed_execution_rejects_duplicate_transaction_identity() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        if request.url.path == "/api/cart":
            return httpx.Response(200, json=_cart_response(payload["userId"]))
        return httpx.Response(200, json=_checkout_response())

    payload = _run(httpx.MockTransport(handler)).model_dump(mode="json")
    observations = payload["observations"]
    run = payload["run"]
    assert isinstance(observations, list)
    assert isinstance(run, dict)
    observations.append(dict(observations[0]))
    run.update(
        planned_transactions=2,
        completed_transactions=2,
        successful_transactions=2,
        transaction_observation_sha256s=[
            observations[0]["observation_sha256"],
            observations[0]["observation_sha256"],
        ],
    )

    with pytest.raises(ValueError, match="transaction identity differs"):
        HealthyTrafficExecutionV0232.model_validate(_reseal_execution(payload))


def test_resealed_execution_rejects_observation_outside_run_window() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        if request.url.path == "/api/cart":
            return httpx.Response(200, json=_cart_response(payload["userId"]))
        return httpx.Response(200, json=_checkout_response())

    execution = _run(httpx.MockTransport(handler))
    payload = execution.model_dump(mode="json")
    run = payload["run"]
    assert isinstance(run, dict)
    original = execution.observations[0]
    observation = CheckoutTransactionObservationV0232.build(
        **{
            **original.model_dump(exclude={"schema_version", "observation_sha256"}),
            "transaction_started_at": execution.run.ended_at,
            "transaction_ended_at": execution.run.ended_at + timedelta(seconds=1),
        }
    )
    payload["observations"] = [observation.model_dump(mode="json")]
    run["transaction_observation_sha256s"] = [
        observation.observation_sha256
    ]

    with pytest.raises(ValueError, match="observation time window differs"):
        HealthyTrafficExecutionV0232.model_validate(_reseal_execution(payload))


def test_response_stream_stops_once_the_byte_limit_is_exceeded() -> None:
    consumed: list[int] = []

    class OversizeStream(httpx.SyncByteStream):
        def __iter__(self):
            for ordinal, chunk in enumerate(
                (b"{" + b" " * 599_999, b" " * 600_000, b'"late":true}')
            ):
                consumed.append(ordinal)
                yield chunk

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            stream=OversizeStream(),
        )

    observation = _run(httpx.MockTransport(handler)).observations[0]

    assert consumed == [0, 1]
    assert observation.safe_error_code == "CART_RESPONSE_SCHEMA_INVALID"


def test_deeply_nested_json_is_a_bounded_schema_failure() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=b"[" * 1_100 + b"0" + b"]" * 1_100,
            headers={"content-type": "application/json"},
        )

    observation = _run(httpx.MockTransport(handler)).observations[0]

    assert observation.safe_error_code == "CART_RESPONSE_SCHEMA_INVALID"


def test_incident_traffic_binding_requires_a_passing_formal_run() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        payload = __import__("json").loads(request.content)
        if request.url.path == "/api/cart":
            return httpx.Response(200, json=_cart_response(payload["userId"]))
        return httpx.Response(200, json=_checkout_response())

    contract = load_checkout_traffic_contract_v0232(ROOT)
    with HealthyTrafficRunnerV0232(
        transport=httpx.MockTransport(handler),
        sleep=lambda _delay: None,
    ) as runner:
        execution = runner.run(
            endpoint=ENDPOINT,
            profile=_profile(transactions=30),
            contract=contract,
            role="FORMAL",
        )
    binding = IncidentTrafficBindingV0232.build(
        incident_id="inc-fixture",
        execution=execution,
        episode_started_at=execution.run.started_at - timedelta(seconds=1),
        episode_ended_at=execution.run.ended_at + timedelta(seconds=1),
    )

    assert binding.contract_sha256 == contract.contract_sha256
    assert binding.traffic_execution_sha256 == execution.execution_sha256
    assert binding.formal_profile_sha256 == execution.run.profile_sha256
    assert binding.traffic_started_at == execution.run.started_at
    assert binding.traffic_ended_at == execution.run.ended_at
    assert binding.successful_transactions == 30
    assert len(binding.binding_sha256) == 64


def test_incident_traffic_binding_rejects_a_partial_episode_window() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        if request.url.path == "/api/cart":
            return httpx.Response(200, json=_cart_response(payload["userId"]))
        return httpx.Response(200, json=_checkout_response())

    contract = load_checkout_traffic_contract_v0232(ROOT)
    with HealthyTrafficRunnerV0232(
        transport=httpx.MockTransport(handler),
        sleep=lambda _delay: None,
    ) as runner:
        execution = runner.run(
            endpoint=ENDPOINT,
            profile=_profile(transactions=30),
            contract=contract,
            role="FORMAL",
        )

    with pytest.raises(ValueError, match="episode window does not contain traffic"):
        IncidentTrafficBindingV0232.build(
            incident_id="inc-fixture",
            execution=execution,
            episode_started_at=execution.run.started_at + timedelta(seconds=1),
            episode_ended_at=execution.run.ended_at + timedelta(seconds=1),
        )
