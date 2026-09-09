"""Fixed stdin/stdout transport to the isolated API's own loopback listener.

The trusted harness executes this file in its exact birth-bound API container.
No command, host, credentials, URL or filesystem path is accepted in the input.
"""

import json
import os
import re
import sys
import httpx


def validate(value):
    if set(value) != {"method", "route", "body", "key"}:
        raise ValueError("TRANSPORT_FIELDS_DENIED")
    if value["method"] not in ("GET", "POST"):
        raise ValueError("TRANSPORT_METHOD_DENIED")
    if not re.fullmatch(
        r"/(?:readyz|v1/(?:environments|jobs|incidents|remediation-candidates|remediation-approvals|remediation-attempts)(?:/[a-zA-Z0-9-]+){0,2})",
        value["route"],
    ):
        raise ValueError("TRANSPORT_ROUTE_DENIED")
    if not re.fullmatch(r"minimal-[0-9]+", value["key"]):
        raise ValueError("TRANSPORT_KEY_DENIED")
    return value


def main():
    raw = sys.stdin.buffer.read(100001)
    if len(raw) > 100000:
        raise ValueError("TRANSPORT_BODY_TOO_LARGE")
    value = validate(json.loads(raw))
    with httpx.Client(
        base_url="http://127.0.0.1:8080",
        timeout=30,
        trust_env=False,
        follow_redirects=False,
    ) as client:
        response = client.request(
            value["method"],
            value["route"],
            json=value["body"],
            headers={
                "Authorization": "Bearer " + os.environ["ECOMSRE_ADMIN_TOKEN"],
                "Idempotency-Key": value["key"],
            },
        )
    print(json.dumps({"status": response.status_code, "body": response.json()}))


if __name__ == "__main__":
    main()
