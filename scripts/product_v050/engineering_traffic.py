"""Normal fixed local-demo traffic; counts physical requests, not checkout pairs."""

import argparse
import hashlib
import json
import time
import httpx
from ecomsre.product.pilot.baseline_readiness_v021 import (
    BoundedHealthyCheckoutTrafficV021,
)
from scripts.product_v050 import engineering_capture as ec


def traffic(group):
    target = ec.ROOT / f"traffic-{group}"
    target.mkdir(mode=0o700)
    for ordinal in range(1, 6):
        for name, payload in [
            (
                "cart",
                BoundedHealthyCheckoutTrafficV021._cart_payload(60000 + group, ordinal),
            ),
            (
                "checkout",
                BoundedHealthyCheckoutTrafficV021._checkout_payload(
                    60000 + group, ordinal
                ),
            ),
        ]:
            # Sleep before every physical request, including each checkout pair.
            time.sleep(1.01)
            url = "http://127.0.0.1:18080/api/" + name
            seq = ec.reserve(
                "normal_traffic", {"url": url, "payload_sha256": ec.digest(payload)}
            )
            started = time.monotonic()
            data = bytearray()
            status = None
            error = None
            truncated = False
            try:
                with httpx.stream(
                    "POST", url, json=payload, timeout=10, trust_env=False
                ) as response:
                    status = response.status_code
                    for block in response.iter_bytes():
                        data.extend(block)
                        if len(data) > 2 * 1024 * 1024:
                            del data[2 * 1024 * 1024 :]
                            truncated = True
                            break
            except Exception as exc:
                error = type(exc).__name__
            path = target / f"{ordinal}-{name}.body"
            path.write_bytes(data)
            path.chmod(0o600)
            ec.save(
                path.with_suffix(".json"),
                dict(
                    sequence=seq,
                    url=url,
                    status=status,
                    error=error,
                    truncated=truncated,
                    elapsed_seconds=time.monotonic() - started,
                    payload=payload,
                    response_sha256=hashlib.sha256(data).hexdigest(),
                ),
            )
            if error or truncated or status not in range(200, 300):
                print(
                    json.dumps(
                        {
                            "group": group,
                            "ordinal": ordinal,
                            "request": name,
                            "status": status,
                            "error": error,
                            "stopped": True,
                        }
                    ),
                    flush=True,
                )
                return
    print(
        json.dumps(
            {"group": group, "physical_requests": 10, "successful_checkouts": 5}
        ),
        flush=True,
    )


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("group", type=int, choices=[1, 2, 3])
    traffic(p.parse_args().group)
