from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
import json

import httpx
import pytest

from ecomsre.product.pilot.healthy_traffic_v0232 import (
    HealthyTrafficProfileV0232,
    HealthyTrafficRunnerV0232,
    load_checkout_traffic_contract_v0232,
)
from ecomsre.product.pilot.traffic_preflight_v0232 import (
    TrafficPreflightAttemptV0232,
    TrafficPreflightEvidenceV0232,
    load_traffic_campaign_v0232,
    load_traffic_profile_v0232,
)
from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22
from scripts.ci.verify_product_v0232_preflight import (
    verify_product_v0232_preflight,
)
from scripts.product_v0232.run_traffic_preflight import _blocked_progress


ROOT = Path(__file__).resolve().parents[2]


def _execution(*, transactions: int, success: bool):
    contract = load_checkout_traffic_contract_v0232(ROOT)

    def handler(request: httpx.Request) -> httpx.Response:
        payload = __import__("json").loads(request.content)
        if not success:
            return httpx.Response(
                503,
                json={"safe": "unavailable"},
                headers={"content-type": "application/json; charset=utf-8"},
            )
        if request.url.path == "/api/cart":
            return httpx.Response(
                200,
                json={
                    "userId": payload["userId"],
                    "items": [{"productId": "0PUK6V6EV0", "quantity": 1}],
                },
            )
        return httpx.Response(
            200,
            json={
                "orderId": "order-fixture",
                "shippingTrackingId": "tracking-fixture",
                "shippingCost": {
                    "currencyCode": "USD",
                    "units": 1,
                    "nanos": 0,
                },
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
                        "cost": {
                            "currencyCode": "USD",
                            "units": 1,
                            "nanos": 0,
                        },
                    }
                ],
            },
        )

    profile = HealthyTrafficProfileV0232.build(
        profile_id="product-v0232-preflight",
        transactions=transactions,
        requests_per_second=1.0,
        request_seed=23083201,
        maximum_failures=0,
        stabilization_seconds=30,
        minimum_full_episode_duration_seconds=0,
        queue_fault_flag=0,
    )
    moments = iter(
        datetime(2026, 8, 30, tzinfo=UTC) + timedelta(milliseconds=i)
        for i in range(100)
    )
    ticks = iter(float(i) for i in range(100))
    with HealthyTrafficRunnerV0232(
        transport=httpx.MockTransport(handler),
        sleep=lambda _delay: None,
        clock=lambda: next(moments),
        monotonic=lambda: next(ticks),
    ) as runner:
        return runner.run(
            endpoint="http://127.0.0.1:18080/api/checkout",
            profile=profile,
            contract=contract,
            role="PREFLIGHT",
        )


def _attempt(*, success: bool, attempt_ordinal: int = 1):
    execution = _execution(transactions=10, success=success)
    contract = load_checkout_traffic_contract_v0232(ROOT)
    campaign = load_traffic_campaign_v0232(ROOT)
    return TrafficPreflightAttemptV0232.build(
        attempt_ordinal=attempt_ordinal,
        changed_parameter=None,
        changed_parameter_evidence_sha256=None,
        execution=execution,
        source_file_bindings=contract.source_file_bindings,
        flagd_bind_descriptor_sha256=campaign.flagd_bind_descriptor_sha256,
        runtime_continuity_descriptor_sha256=(
            campaign.runtime_continuity_descriptor_sha256
        ),
        resolved_compose_sha256=campaign.resolved_compose_sha256,
        read_authority_sha256=campaign.read_authority_sha256,
        pilot_runtime_authority_sha256=campaign.pilot_runtime_authority_sha256,
        checkout_state="RUNNING",
        checkout_healthy=True,
        checkout_restart_count=0,
        queue_before_sha256=campaign.queue_default_bytes_sha256,
        queue_after_sha256=campaign.queue_default_bytes_sha256,
        outer_baseline_before_sha256=campaign.outer_baseline_document_sha256,
        outer_baseline_after_sha256=campaign.outer_baseline_document_sha256,
        source_state_before_sha256=campaign.source_state_sha256,
        source_state_after_sha256=campaign.source_state_sha256,
        product_state_clone_sha256=campaign.product_state_clone_sha256,
        product_state_before_sha256=campaign.product_state_sha256,
        product_state_after_sha256=campaign.product_state_sha256,
        incident_count_before=1,
        incident_count_after=1,
        diagnosis_count_before=1,
        diagnosis_count_after=1,
        demo_cleanup={
            "verdict": "CLEAN",
            "owned_containers": 0,
            "owned_networks": 0,
            "owned_volumes": 0,
            "non_owned_resources_changed": False,
        },
        product_cleanup={
            "schema_version": "ecomsre.product.host-process-cleanup.v023",
            "verdict": "CLEAN",
            "owned_host_processes": 0,
            "product_api_port": 18081,
            "product_api_port_available": True,
            "launches": [],
            "non_owned_resources_changed": False,
            "safe_error": None,
            "database_owner_count_before": 0,
            "database_owner_count_after": 0,
        },
    )


def test_frozen_profiles_and_campaign_match_the_goal() -> None:
    preflight = load_traffic_profile_v0232(ROOT, role="PREFLIGHT")
    formal = load_traffic_profile_v0232(ROOT, role="FORMAL")
    campaign = load_traffic_campaign_v0232(ROOT)

    assert preflight.transactions == 10
    assert preflight.requests_per_second == 1.0
    assert preflight.request_seed == 23083201
    assert preflight.maximum_failures == 0
    assert preflight.stabilization_seconds == 30
    assert formal.transactions == 30
    assert formal.requests_per_second == 1.0
    assert formal.request_seed == 23083202
    assert formal.minimum_full_episode_duration_seconds == 300
    assert campaign.maximum_live_traffic_preflight_attempts == 2
    assert campaign.formal_healthy_traffic_execution_limit == 1
    assert campaign.action_authority == "NONE"


def test_attempt_one_pass_binds_exact_runtime_traffic_and_cleanup() -> None:
    attempt = _attempt(success=True)

    assert attempt.terminal == "ECOMSRE_PRODUCT_V0232_TRAFFIC_PREFLIGHT_ATTEMPT_PASS"
    assert attempt.completed_transactions == 10
    assert attempt.successful_transactions == 10
    assert attempt.failed_transactions == 0
    assert attempt.queue_default_unchanged is True
    assert attempt.outer_baseline_unchanged is True
    assert attempt.source_state_unchanged is True
    assert attempt.demo_cleanup.verdict == "CLEAN"
    assert attempt.product_cleanup.verdict == "CLEAN"
    assert attempt.product_cleanup.launches == ()
    assert attempt.incident_count_before == attempt.incident_count_after == 1
    assert attempt.first_failure is None


def test_failed_attempt_preserves_one_bounded_failure_and_cannot_mint_pass() -> None:
    attempt = _attempt(success=False)

    assert attempt.terminal == "ECOMSRE_PRODUCT_V0232_TRAFFIC_PREFLIGHT_ATTEMPT_FAIL"
    assert attempt.completed_transactions == 10
    assert attempt.failed_transactions == 10
    assert attempt.first_failure is not None
    assert attempt.first_failure.failure_stage == "CART_HTTP"
    assert attempt.first_failure.safe_error_code == "CART_HTTP_NON_SUCCESS"
    assert attempt.first_failure.http_status == 503
    assert attempt.first_failure.response_content_type == "application/json"
    assert len(attempt.first_failure.response_shape_summary) <= 200

    with pytest.raises(ValueError, match="passing Attempt"):
        TrafficPreflightEvidenceV0232.build(
            attempt=attempt,
            formal_profile=load_traffic_profile_v0232(ROOT, role="FORMAL"),
            campaign=load_traffic_campaign_v0232(ROOT),
        )


def test_attempt_two_requires_exactly_one_declared_change() -> None:
    with pytest.raises(ValueError, match="changed parameter"):
        _attempt(success=True, attempt_ordinal=2)


def test_preflight_evidence_mints_only_from_exact_ten_of_ten() -> None:
    attempt = _attempt(success=True)
    result = TrafficPreflightEvidenceV0232.build(
        attempt=attempt,
        formal_profile=load_traffic_profile_v0232(ROOT, role="FORMAL"),
        campaign=load_traffic_campaign_v0232(ROOT),
    )

    assert result.terminal == "ECOMSRE_PRODUCT_V0232_TRAFFIC_PREFLIGHT_PASS"
    assert result.live_traffic_preflight_attempt_count == 1
    assert result.transaction_count == 10
    assert result.successful_transaction_count == 10
    assert result.action_authority == "NONE"


def test_public_preflight_verifier_rebuilds_all_cross_bindings(tmp_path: Path) -> None:
    attempt = _attempt(success=True)
    formal = load_traffic_profile_v0232(ROOT, role="FORMAL")
    campaign = load_traffic_campaign_v0232(ROOT)
    preflight = TrafficPreflightEvidenceV0232.build(
        attempt=attempt,
        formal_profile=formal,
        campaign=campaign,
    )
    attempt_path = tmp_path / "attempt.json"
    preflight_path = tmp_path / "preflight.json"
    progress_path = tmp_path / "progress.json"
    attempt_path.write_text(attempt.model_dump_json(), encoding="utf-8")
    preflight_path.write_text(preflight.model_dump_json(), encoding="utf-8")
    progress = json.loads(
        (ROOT / "docs/analysis/product-v0232-progress.json").read_text(
            encoding="utf-8"
        )
    )
    progress.pop("progress_sha256")
    progress.update(
        terminal="ECOMSRE_PRODUCT_V0232_TRAFFIC_PREFLIGHT_PASS",
        increment=4,
        live_traffic_preflight_attempt_count=1,
        traffic_preflight_attempt_sha256=attempt.attempt_sha256,
        traffic_preflight_sha256=preflight.preflight_sha256,
        formal_profile_sha256=formal.profile_sha256,
        campaign_sha256=campaign.campaign_sha256,
    )
    progress["progress_sha256"] = semantic_sha256_v22(progress)
    progress_path.write_text(json.dumps(progress), encoding="utf-8")

    verified = verify_product_v0232_preflight(
        ROOT,
        attempt_path=attempt_path,
        preflight_path=preflight_path,
        progress_path=progress_path,
    )

    assert verified["terminal"] == "ECOMSRE_PRODUCT_V0232_TRAFFIC_PREFLIGHT_PASS"
    assert verified["live_traffic_preflight_attempt_count"] == 1


def test_consumed_failure_advances_public_attempt_count() -> None:
    base_progress = json.loads(
        (ROOT / "docs/analysis/product-v0232-progress.json").read_text(
            encoding="utf-8"
        )
    )
    base_progress.pop("progress_sha256")
    base_progress.pop("traffic_preflight_attempt_sha256")
    base_progress.update(
        terminal="ECOMSRE_PRODUCT_V0232_EVIDENCE_BINDING_CONTRACT_PASS",
        increment=3,
        live_traffic_preflight_attempt_count=0,
    )
    progress = _blocked_progress(
        ROOT,
        attempt_ordinal=1,
        attempt_sha256="f" * 64,
        base_progress=base_progress,
    )
    supplied = progress.pop("progress_sha256")

    assert progress["terminal"] == "BLOCKED_ECOMSRE_PRODUCT_V0232_TRAFFIC_PREFLIGHT"
    assert progress["increment"] == 4
    assert progress["live_traffic_preflight_attempt_count"] == 1
    assert progress["traffic_preflight_attempt_sha256"] == "f" * 64
    assert supplied == semantic_sha256_v22(progress)


def test_verifier_rejects_resealed_unfrozen_attempt_bindings(
    tmp_path: Path,
) -> None:
    attempt = _attempt(success=True)
    formal = load_traffic_profile_v0232(ROOT, role="FORMAL")
    campaign = load_traffic_campaign_v0232(ROOT)
    preflight = TrafficPreflightEvidenceV0232.build(
        attempt=attempt,
        formal_profile=formal,
        campaign=campaign,
    )
    progress = json.loads(
        (ROOT / "docs/analysis/product-v0232-progress.json").read_text(
            encoding="utf-8"
        )
    )
    progress.pop("progress_sha256")
    progress.update(
        terminal="ECOMSRE_PRODUCT_V0232_TRAFFIC_PREFLIGHT_PASS",
        increment=4,
        live_traffic_preflight_attempt_count=1,
        traffic_preflight_attempt_sha256=attempt.attempt_sha256,
        traffic_preflight_sha256=preflight.preflight_sha256,
        formal_profile_sha256=formal.profile_sha256,
        campaign_sha256=campaign.campaign_sha256,
    )
    progress["progress_sha256"] = semantic_sha256_v22(progress)
    base = attempt.model_dump(mode="json")
    variants = {
        "runtime": {
            "runtime_continuity_descriptor_sha256": "1" * 64,
        },
        "queue": {
            "queue_before_sha256": "2" * 64,
            "queue_after_sha256": "2" * 64,
        },
        "source": {
            "source_state_before_sha256": "3" * 64,
            "source_state_after_sha256": "3" * 64,
        },
        "product": {
            "product_state_before_sha256": "4" * 64,
            "product_state_after_sha256": "4" * 64,
        },
        "counts": {
            "incident_count_before": 2,
            "incident_count_after": 2,
            "diagnosis_count_before": 2,
            "diagnosis_count_after": 2,
        },
        "cleanup": {
            "product_cleanup": {
                **base["product_cleanup"],
                "launches": [{"pid": 123}],
            },
        },
    }
    for name, mutation in variants.items():
        case = tmp_path / name
        case.mkdir()
        payload = {**base, **mutation}
        payload.pop("attempt_sha256")
        payload["attempt_sha256"] = semantic_sha256_v22(payload)
        try:
            tampered_attempt = TrafficPreflightAttemptV0232.model_validate(payload)
        except ValueError:
            assert name in {"counts", "cleanup"}
        else:
            with pytest.raises(ValueError, match="frozen profile binding"):
                TrafficPreflightEvidenceV0232.build(
                    attempt=tampered_attempt,
                    formal_profile=formal,
                    campaign=campaign,
                )
        attempt_path = case / "attempt.json"
        preflight_path = case / "preflight.json"
        progress_path = case / "progress.json"
        attempt_path.write_text(json.dumps(payload), encoding="utf-8")
        preflight_path.write_text(preflight.model_dump_json(), encoding="utf-8")
        progress_path.write_text(json.dumps(progress), encoding="utf-8")

        with pytest.raises(ValueError):
            verify_product_v0232_preflight(
                ROOT,
                attempt_path=attempt_path,
                preflight_path=preflight_path,
                progress_path=progress_path,
            )
