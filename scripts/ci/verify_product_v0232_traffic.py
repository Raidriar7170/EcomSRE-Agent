#!/usr/bin/env python3
"""Verify the source-bound Product v0.2.3.2 checkout traffic checkpoint."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Callable, Sequence

import httpx

from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22
from ecomsre.product.pilot.healthy_traffic_v0232 import (
    CheckoutTrafficContractV0232,
    HealthyTrafficProfileV0232,
    HealthyTrafficRunnerV0232,
    TRAFFIC_CONTRACT_PASS_V0232,
    load_checkout_traffic_contract_v0232,
)
from scripts.ci.verify_product_v0232_history import (
    verify_product_v0232_written_reports,
)


_CANONICAL_PROTO_SHA256 = (
    "28e2c4badedcbed88543afdafc8c60f9d9e9e5cc499ee3b7098a6438d3deaf85"
)
_CHECKOUT_CYPRESS_SHA256 = (
    "a3c1aee4b5006af285b5a5dba1818151e2d9015d3206b689ab902d835e1d9954"
)
_CLONE_SHA256 = "6920044cea06a68f38624803468aeeb0f854caee695f7f876ff2d6f6ef074205"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _cart_response(user_id: str) -> dict[str, object]:
    return {
        "userId": user_id,
        "items": [{"productId": "0PUK6V6EV0", "quantity": 1}],
    }


def _checkout_response(*, order_id: str = "order-offline-fixture") -> dict[str, object]:
    return {
        "orderId": order_id,
        "shippingTrackingId": "tracking-offline-fixture",
        "shippingCost": {"currencyCode": "USD", "units": 1, "nanos": 0},
        "shippingAddress": {
            "streetAddress": "shape-only",
            "city": "shape-only",
            "state": "shape-only",
            "country": "shape-only",
            "zipCode": "shape-only",
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


def _fixture_handler(case: str) -> Callable[[httpx.Request], httpx.Response]:
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        if request.url.path == "/api/cart":
            if case == "cart_http_failure":
                return httpx.Response(503, json={"safe": "unavailable"})
            if case == "cart_schema_failure":
                return httpx.Response(200, json={"items": "not-a-list"})
            return httpx.Response(200, json=_cart_response(payload["userId"]))
        if case == "checkout_http_failure":
            return httpx.Response(503, json={"safe": "unavailable"})
        return httpx.Response(
            200,
            json=_checkout_response(
                order_id="" if case == "business_semantic_failure" else "order-pass"
            ),
        )

    return handler


def _mocked_dispositions(
    contract: CheckoutTrafficContractV0232,
) -> list[dict[str, object]]:
    profile = HealthyTrafficProfileV0232.build(
        profile_id="offline-contract-fixture",
        transactions=1,
        requests_per_second=1.0,
        request_seed=23083200,
        maximum_failures=0,
        stabilization_seconds=0,
        minimum_full_episode_duration_seconds=0,
        queue_fault_flag=0,
    )
    cases = (
        "success",
        "cart_http_failure",
        "checkout_http_failure",
        "cart_schema_failure",
        "business_semantic_failure",
    )
    dispositions: list[dict[str, object]] = []
    for case in cases:
        with HealthyTrafficRunnerV0232(
            transport=httpx.MockTransport(_fixture_handler(case)),
            sleep=lambda _delay: None,
        ) as runner:
            execution = runner.run(
                endpoint="http://127.0.0.1:8080/api/checkout",
                profile=profile,
                contract=contract,
                role="PREFLIGHT",
            )
        observation = execution.observations[0]
        dispositions.append(
            {
                "case": case,
                "business_success": observation.business_success,
                "failure_stage": (
                    None
                    if observation.failure_stage is None
                    else observation.failure_stage.value
                ),
                "safe_error_code": (
                    None
                    if observation.safe_error_code is None
                    else observation.safe_error_code.value
                ),
                "cart_status": observation.cart_status,
                "checkout_status": observation.checkout_status,
                "transport_retry_count": execution.run.transport_retry_count,
            }
        )
    return dispositions


def build_product_v0232_traffic_report(root: Path) -> dict[str, object]:
    project = root.resolve(strict=True)
    contract = load_checkout_traffic_contract_v0232(project)
    upstream = project / "third_party/opentelemetry-demo"
    canonical_proto = _sha256_file(upstream / "pb/demo.proto")
    checkout_cypress = _sha256_file(
        upstream / "src/frontend/cypress/e2e/Checkout.cy.ts"
    )
    if (
        canonical_proto != _CANONICAL_PROTO_SHA256
        or checkout_cypress != _CHECKOUT_CYPRESS_SHA256
    ):
        raise ValueError("Product v0.2.3.2 corroborating traffic source drift")
    dispositions = _mocked_dispositions(contract)
    expected_dispositions = [
        {
            "case": "success",
            "business_success": True,
            "failure_stage": None,
            "safe_error_code": None,
            "cart_status": 200,
            "checkout_status": 200,
            "transport_retry_count": 0,
        },
        {
            "case": "cart_http_failure",
            "business_success": False,
            "failure_stage": "CART_HTTP",
            "safe_error_code": "CART_HTTP_NON_SUCCESS",
            "cart_status": 503,
            "checkout_status": None,
            "transport_retry_count": 0,
        },
        {
            "case": "checkout_http_failure",
            "business_success": False,
            "failure_stage": "CHECKOUT_HTTP",
            "safe_error_code": "CHECKOUT_HTTP_NON_SUCCESS",
            "cart_status": 200,
            "checkout_status": 503,
            "transport_retry_count": 0,
        },
        {
            "case": "cart_schema_failure",
            "business_success": False,
            "failure_stage": "CART_RESPONSE",
            "safe_error_code": "CART_RESPONSE_SCHEMA_INVALID",
            "cart_status": 200,
            "checkout_status": None,
            "transport_retry_count": 0,
        },
        {
            "case": "business_semantic_failure",
            "business_success": False,
            "failure_stage": "BUSINESS_SUCCESS",
            "safe_error_code": "CHECKOUT_BUSINESS_SUCCESS_MISSING",
            "cart_status": 200,
            "checkout_status": 200,
            "transport_retry_count": 0,
        },
    ]
    if dispositions != expected_dispositions:
        raise ValueError("Product v0.2.3.2 mocked traffic disposition differs")
    body: dict[str, Any] = {
        "schema_version": "ecomsre.product.traffic-contract-report.v0232",
        "terminal": TRAFFIC_CONTRACT_PASS_V0232,
        "contract_sha256": contract.contract_sha256,
        "upstream_commit": contract.upstream_commit,
        "source_file_binding_count": len(contract.source_file_bindings),
        "canonical_proto_corroboration_sha256": canonical_proto,
        "checkout_test_corroboration_sha256": checkout_cypress,
        "mocked_transaction_count": len(dispositions),
        "mocked_fixture_dispositions": dispositions,
        "all_transactions_have_exact_disposition": True,
        "full_synthetic_payloads_persisted_in_execution_or_evidence_artifacts": (
            False
        ),
        "transport_retry_count": 0,
    }
    return {**body, "report_sha256": semantic_sha256_v22(body)}


def _load_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Product v0.2.3.2 JSON object is invalid: {path.name}")
    return payload


def verify_product_v0232_traffic(
    root: Path,
    *,
    contract_path: Path | None = None,
    report_path: Path | None = None,
    progress_path: Path | None = None,
) -> dict[str, object]:
    project = root.resolve(strict=True)
    contract = load_checkout_traffic_contract_v0232(project)
    written_contract = CheckoutTrafficContractV0232.model_validate(
        _load_object(
            contract_path or project / "config/product-v0232/traffic/contract.json"
        )
    )
    if written_contract != contract:
        raise ValueError("Product v0.2.3.2 written traffic contract differs")
    expected_report = build_product_v0232_traffic_report(project)
    written_report = _load_object(
        report_path or project / "docs/analysis/product-v0232-traffic-contract.json"
    )
    if written_report != expected_report:
        raise ValueError("Product v0.2.3.2 traffic report differs")
    bound_progress_path = (
        progress_path or project / "docs/analysis/product-v0232-progress.json"
    )
    progress = _load_object(bound_progress_path)
    progress_bindings = {
        "traffic_contract_sha256": contract.contract_sha256,
        "traffic_contract_report_sha256": expected_report["report_sha256"],
    }
    if progress.get("increment") == 2:
        verify_product_v0232_written_reports(
            project,
            progress_path=bound_progress_path,
            expected_progress_terminal=TRAFFIC_CONTRACT_PASS_V0232,
            expected_progress_increment=2,
            expected_offline_changed_iteration_count=2,
            expected_progress_bindings=progress_bindings,
        )
    else:
        verify_product_v0232_written_reports(
            project,
            progress_path=bound_progress_path,
            expected_progress_bindings=progress_bindings,
        )
    if progress.get("clone_sha256") != _CLONE_SHA256:
        raise ValueError("Product v0.2.3.2 traffic progress clone differs")
    public_bytes = b"\n".join(
        json.dumps(payload, sort_keys=True).encode("utf-8")
        for payload in (
            written_contract.model_dump(mode="json"),
            written_report,
            progress,
        )
    )
    forbidden = (
        b"/Users/",
        b"4111111111111111",
        b"1 Contract Way",
    )
    if any(value in public_bytes for value in forbidden):
        raise ValueError("Product v0.2.3.2 public traffic artifact leaks payload data")
    return {
        "terminal": TRAFFIC_CONTRACT_PASS_V0232,
        "contract_sha256": contract.contract_sha256,
        "source_file_binding_count": len(contract.source_file_bindings),
        "mocked_transaction_count": len(
            expected_report["mocked_fixture_dispositions"]  # type: ignore[arg-type]
        ),
        "offline_changed_iteration_count": 2,
        "live_traffic_preflight_attempt_count": 0,
        "formal_healthy_traffic_execution_count": 0,
        "source_clone_count": 1,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--print-contract", action="store_true")
    parser.add_argument("--print-report", action="store_true")
    arguments = parser.parse_args(argv)
    if arguments.print_contract and arguments.print_report:
        parser.error("choose only one print mode")
    if arguments.print_contract:
        result: object = load_checkout_traffic_contract_v0232(arguments.root).model_dump(
            mode="json"
        )
    elif arguments.print_report:
        result = build_product_v0232_traffic_report(arguments.root)
    else:
        result = verify_product_v0232_traffic(arguments.root)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = (
    "build_product_v0232_traffic_report",
    "verify_product_v0232_traffic",
)
