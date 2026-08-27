from __future__ import annotations

import time
from typing import Callable
from urllib.parse import urlsplit

import httpx

from ecomsre.product.pilot.contracts_v02 import (
    TrafficProfileV02,
    TrafficRunResultV02,
    semantic_sha256_v02,
)


class BoundedCheckoutTrafficV02:
    def __init__(
        self,
        *,
        client: httpx.Client,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.client = client
        self.sleep = sleep

    @staticmethod
    def _payload(seed: int, ordinal: int) -> dict[str, object]:
        suffix = f"{seed:010d}-{ordinal:03d}"
        return {
            "userId": f"pilot-{suffix}",
            "userCurrency": "USD",
            "email": f"pilot-{suffix}@example.invalid",
            "address": {
                "streetAddress": "1 Pilot Way",
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

    @staticmethod
    def _cart_payload(seed: int, ordinal: int) -> dict[str, object]:
        suffix = f"{seed:010d}-{ordinal:03d}"
        return {
            "userId": f"pilot-{suffix}",
            "item": {"productId": "0PUK6V6EV0", "quantity": 1},
        }

    def run(
        self,
        *,
        endpoint: str,
        profile: TrafficProfileV02,
    ) -> TrafficRunResultV02:
        parsed = urlsplit(endpoint)
        if (
            parsed.scheme != "http"
            or parsed.hostname not in {"127.0.0.1", "localhost"}
            or parsed.path != "/api/checkout"
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("traffic endpoint must be the local checkout API")
        started = time.monotonic()
        cart_endpoint = endpoint.removesuffix("/api/checkout") + "/api/cart"
        succeeded = 0
        failed = 0
        attempted = 0
        for ordinal in range(1, profile.maximum_request_count + 1):
            cart_response = self.client.post(
                cart_endpoint,
                json=self._cart_payload(profile.request_seed, ordinal),
                timeout=10.0,
            )
            response = (
                self.client.post(
                    endpoint,
                    json=self._payload(profile.request_seed, ordinal),
                    timeout=20.0,
                )
                if 200 <= cart_response.status_code < 300
                else cart_response
            )
            attempted += 1
            if 200 <= response.status_code < 300:
                succeeded += 1
            else:
                failed += 1
            if failed >= profile.error_budget:
                break
            if ordinal < profile.maximum_request_count:
                self.sleep(1.0 / profile.requests_per_second)
        body = {
            "schema_version": "ecomsre.product.pilot.traffic-result.v02",
            "profile_id": profile.profile_id,
            "attempted": attempted,
            "succeeded": succeeded,
            "failed": failed,
            "stopped_on_error_budget": failed >= profile.error_budget,
            "duration_seconds": max(0.0, time.monotonic() - started),
        }
        return TrafficRunResultV02.model_validate(
            {**body, "result_sha256": semantic_sha256_v02(body)}
        )


__all__ = ["BoundedCheckoutTrafficV02"]
