"""One fixed pre-freeze application warmup; retain failures, never replay a POST."""

import asyncio
from datetime import UTC, datetime
import hashlib
import json
import time
from typing import Any

import httpx

from ecomsre.product.pilot.baseline_readiness_v021 import BoundedHealthyCheckoutTrafficV021
from ecomsre.product.remediation.window_requests import create_private_file
from scripts.product.v040_observer import checkout_business_passed
from scripts.product.v040_runtime import ProductRuntimeV040, seal_private

WARMUP = {
    "request_seed": 40400,
    "maximum_transactions": 3,
    "interval_seconds": 6,
    "total_request_deadline_seconds": 120,
    # Rate queries use a 5-minute range; add a fixed 30-second margin.
    # This does not prove telemetry expiry (native Kafka quantiles differ);
    # the later healthy control and NO_INCIDENT remain required.
    "telemetry_settlement_seconds": 330,
}


async def warmup_once(
    runtime: ProductRuntimeV040, *, client: httpx.AsyncClient,
) -> dict[str, Any]:
    started = time.monotonic()
    seal_private(runtime.private / "host/application-warmup-start.json", {
        "created_at": datetime.now(UTC).isoformat(), "profile": WARMUP,
        "purpose": "PRE_FREEZE_APPLICATION_WARMUP_ONLY",
    })
    transactions = []
    deadline = False
    try:
        async with asyncio.timeout(WARMUP["total_request_deadline_seconds"]):
            for ordinal in range(1, WARMUP["maximum_transactions"] + 1):
                row: dict[str, Any] = {"ordinal": ordinal, "business_passed": False}
                for operation in ("cart", "checkout"):
                    stem = f"application-warmup-{ordinal}-{operation}"
                    seal_private(runtime.private / "host" / f"{stem}-intent.json", {
                        "ordinal": ordinal, "operation": operation,
                        "request_seed": WARMUP["request_seed"],
                        "created_at": datetime.now(UTC).isoformat(),
                    })
                    began = time.monotonic()
                    observation: dict[str, Any] = {}
                    raw = bytearray()
                    complete = False
                    try:
                        payload = (
                            BoundedHealthyCheckoutTrafficV021._cart_payload
                            if operation == "cart"
                            else BoundedHealthyCheckoutTrafficV021._checkout_payload
                        )(WARMUP["request_seed"], ordinal)
                        async with client.stream(
                            "POST", "http://127.0.0.1:18080/api/" + operation,
                            json=payload, timeout=20, follow_redirects=False,
                        ) as response:
                            observation["status"] = response.status_code
                            async for chunk in response.aiter_bytes():
                                remaining = 1024 * 1024 - len(raw)
                                raw.extend(chunk[:remaining])
                                if len(chunk) > remaining:
                                    raise ValueError("response exceeds bound")
                            complete = True
                            if operation == "checkout" and response.is_success:
                                try:
                                    row["business_passed"] = checkout_business_passed(json.loads(raw))
                                except ValueError:
                                    row["business_passed"] = False
                    except ValueError:
                        observation["error_type"] = "RESPONSE_BODY_TOO_LARGE"
                    except httpx.HTTPError as error:
                        observation["error_type"] = type(error).__name__
                    except asyncio.CancelledError:
                        observation["error_type"] = "TOTAL_DEADLINE"
                        raise
                    finally:
                        if "status" in observation:
                            create_private_file(runtime.private / "host" / f"{stem}-body.bin", bytes(raw))
                            observation.update(
                                response_sha256=hashlib.sha256(raw).hexdigest(),
                                response_bytes=len(raw), response_complete=complete,
                            )
                        observation["ended_at"] = datetime.now(UTC).isoformat()
                        observation["monotonic_seconds"] = time.monotonic() - began
                        seal_private(runtime.private / "host" / f"{stem}-result.json", observation)
                        row[operation] = observation
                    if operation == "cart" and not 200 <= observation.get("status", 0) < 300:
                        break
                transactions.append(row)
                if ordinal < WARMUP["maximum_transactions"]:
                    await asyncio.sleep(WARMUP["interval_seconds"])
    except TimeoutError:
        deadline = True
    result = {
        "purpose": "PRE_FREEZE_APPLICATION_WARMUP_ONLY",
        "transactions": transactions, "deadline_exhausted": deadline,
        "ended_at": datetime.now(UTC).isoformat(),
        "monotonic_seconds": time.monotonic() - started,
    }
    seal_private(runtime.private / "host/application-warmup.json", result)
    if deadline or len(transactions) != 3 or not any(row["business_passed"] for row in transactions):
        raise ValueError("bounded application warmup failed; no replacement group")
    return result


def application_warmup(runtime: ProductRuntimeV040, profile: dict[str, Any]) -> None:
    if profile != WARMUP:
        raise ValueError("application warmup profile differs from fixed source")

    async def run() -> None:
        async with httpx.AsyncClient(trust_env=False, follow_redirects=False) as client:
            await warmup_once(runtime, client=client)

    asyncio.run(run())
    time.sleep(WARMUP["telemetry_settlement_seconds"])
    seal_private(runtime.private / "host/application-warmup-settled.json", {
        "created_at": datetime.now(UTC).isoformat(),
        "settlement_seconds": WARMUP["telemetry_settlement_seconds"],
    })
