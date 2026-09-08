"""Exactly-once demo checkout traffic; warmup and healthy groups stay distinct."""

from __future__ import annotations
import time
from typing import Any
from ecomsre.product.pilot.baseline_readiness_v021 import (
    BoundedHealthyCheckoutTrafficV021,
)
from .common import require, seal
from .http import LocalHTTP


def business_success(response: Any) -> bool:
    if (
        not isinstance(response, dict)
        or not isinstance(response.get("orderId"), str)
        or not response["orderId"]
    ):
        return False
    items = response.get("items")
    return (
        isinstance(items, list)
        and len(items) == 1
        and isinstance(items[0], dict)
        and isinstance(items[0].get("item"), dict)
        and items[0]["item"].get("productId") == "0PUK6V6EV0"
        and items[0]["item"].get("quantity") == 1
    )


def traffic(http: LocalHTTP, attempt: str, group: str) -> dict[str, Any]:
    require(group in ("warmup", "healthy"), "TRAFFIC_GROUP_INVALID")
    count = 3 if group == "warmup" else 30
    started = time.monotonic()
    results = []
    for ordinal in range(count):
        require(
            time.monotonic() - started < (120 if group == "warmup" else 1200),
            "TRAFFIC_DEADLINE",
        )
        user = f"preflight-v4-{attempt}-{group}-{ordinal:02d}"
        checkout = BoundedHealthyCheckoutTrafficV021._checkout_payload(0, ordinal)
        checkout.update({"userId": user, "email": user + "@example.invalid"})
        cart = {"userId": user, "item": {"productId": "0PUK6V6EV0", "quantity": 1}}
        key = f"{group}-{ordinal:02d}"
        cart_result = http.request(key + "-cart", "POST", "/api/cart", cart, timeout=10)
        result = {
            "ordinal": ordinal,
            "user": user,
            "cart_status": cart_result["status"],
            "checkout_status": None,
            "business_success": False,
        }
        if cart_result["status"] is not None and 200 <= cart_result["status"] < 300:
            response = http.request(
                key + "-checkout", "POST", "/api/checkout", checkout, timeout=20
            )
            result["checkout_status"] = response["status"]
            result["business_success"] = (
                response["status"] is not None
                and 200 <= response["status"] < 300
                and business_success(response.get("body"))
            )
        results.append(result)
    success = sum(r["business_success"] for r in results)
    result = {
        "group": group,
        "planned": count,
        "completed": len(results),
        "business_successes": success,
        "failures": len(results) - success,
        "retries": 0,
        "requests": results,
        "elapsed_seconds": time.monotonic() - started,
    }
    seal(http.root, group + "-summary.json", result)
    require(
        success >= 2 if group == "warmup" else success == count,
        "TRAFFIC_BUSINESS_FAILURE:" + group,
    )
    return result
