"""Fail-closed replay of the public live chain; no live services are contacted."""

from copy import deepcopy
import json
import pytest
from scripts.ci.verify_product_v040_minimal_payment import RESULT, verify
from scripts.product.minimal_payment_acceptance_v040.api_transport import validate


@pytest.fixture
def live():
    return json.loads((RESULT / "live-result.json").read_bytes())


def test_public_chain_replays_with_current_product_verifier(live):
    assert verify(live)["recovery_windows"] == 2


@pytest.mark.parametrize(
    "mutation",
    ["cleanup", "second_write", "missing_window", "goal_approval", "diagnosis"],
)
def test_incomplete_or_misbound_live_claim_is_denied(live, mutation):
    value = deepcopy(live)
    if mutation == "cleanup":
        value["cleanup"]["remaining"]["container"] = 1
    elif mutation == "second_write":
        value["gateway_consumptions"] = 2
    elif mutation == "missing_window":
        value["objects"]["recovery_windows"].pop()
    elif mutation == "goal_approval":
        value["goal_authorization"]["goal_sha256"] = "0" * 64
    else:
        value["product_diagnosis"]["mechanism"] = "OTHER"
    with pytest.raises(ValueError):
        verify(value)


@pytest.mark.parametrize(
    "route",
    [
        "http://external/v1/environments",
        "/v1/environments/../secrets",
        "/restore-payment-baseline",
        "/v1/environments?token=x",
    ],
)
def test_api_transport_rejects_non_product_or_arbitrary_routes(route):
    with pytest.raises(ValueError):
        validate({"method": "POST", "route": route, "body": {}, "key": "minimal-1"})


def test_api_transport_rejects_command_fields():
    with pytest.raises(ValueError):
        validate(
            {
                "method": "GET",
                "route": "/readyz",
                "body": None,
                "key": "minimal-1",
                "command": "anything",
            }
        )
