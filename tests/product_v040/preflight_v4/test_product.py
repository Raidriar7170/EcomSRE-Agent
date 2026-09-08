"""Real offline Product API/worker/CAS integration and closed HTTP mutation routes."""

from copy import deepcopy
from fastapi.testclient import TestClient
import pytest
from ecomsre.product.app import create_app
from tests.product.test_increment3_incident_diagnosis import (
    _settings,
    _prepare_environment,
    _diagnose,
)
from scripts.product.preflight_v040_v4.common import Failure
from scripts.product.preflight_v040_v4.http import LocalHTTP
from scripts.product.preflight_v040_v4.product import (
    validate_diagnosis,
    resolve_objects,
    resolve_trace,
    formal_zero,
)
from scripts.product.preflight_v040_v4.traffic import business_success


def test_real_fixture_api_baseline_noincident_and_zero_authority(tmp_path):
    settings = _settings(tmp_path)
    with TestClient(create_app(settings)) as client:
        environment, service = _prepare_environment(
            client, settings, dataset="product-mvp-demo", name="v4-offline"
        )
        diagnosis, evidence = _diagnose(
            client,
            settings,
            environment_id=environment,
            service_id=service,
            external_key="v4-no-fault",
        )
        validate_diagnosis(diagnosis)
        resolved = resolve_objects(tmp_path, evidence, diagnosis)
        index = client.get(
            "/v1/incidents/" + diagnosis["incident_id"] + "/evidence-index"
        ).json()
        trace = resolve_trace(
            tmp_path, index, diagnosis, resolved["reference_to_sha256"]
        )
        assert trace["payload"]["no_incident_admissible"]
        assert not any(formal_zero(tmp_path).values())


@pytest.mark.parametrize(
    "path",
    [
        "/v1/remediation-candidates",
        "/v1/incidents/inc-" + "a" * 24 + "/remediation-candidates",
        "/v1/remediation/activate",
        "/v1/environments/env-" + "a" * 24 + "/changes",
    ],
)
def test_http_cannot_reach_remediation_routes(tmp_path, path):
    client = LocalHTTP(tmp_path, port=18001)
    with pytest.raises(Failure, match="WRITE_ROUTE_DENIED"):
        client.request("forbidden", "POST", path, {})
    assert not list(tmp_path.glob("*"))


def test_business_oracle_requires_expected_content():
    success = {
        "orderId": "order",
        "items": [{"item": {"productId": "0PUK6V6EV0", "quantity": 1}}],
    }
    assert business_success(success)
    for changed in (
        {"orderId": "order"},
        {"orderId": "", "items": success["items"]},
        {"orderId": "order", "items": []},
    ):
        assert not business_success(changed)
    wrong = deepcopy(success)
    wrong["items"][0]["item"]["quantity"] = 2
    assert not business_success(wrong)
