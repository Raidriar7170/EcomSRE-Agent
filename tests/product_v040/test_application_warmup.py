"""Pre-freeze warmup is bounded, recorded and separate from healthy acceptance."""

import asyncio
import hashlib
import json

import httpx
import pytest

from scripts.product.v040_runtime import ProductRuntimeV040
import scripts.product.v040_warmup as warmup


def setup_runtime(tmp_path, monkeypatch):
    runtime = ProductRuntimeV040(tmp_path)
    (runtime.private / "host").mkdir(mode=0o700, parents=True)
    monkeypatch.setitem(warmup.WARMUP, "interval_seconds", 0)
    return runtime


def test_warmup_keeps_proxy_failure_and_never_reuses_transaction(tmp_path, monkeypatch):
    runtime = setup_runtime(tmp_path, monkeypatch)
    calls = []

    def upstream(request):
        payload = json.loads(request.content)
        ordinal = len(calls) // 2 + 1
        operation = request.url.path.rsplit("/", 1)[1]
        assert (runtime.private / "host" / f"application-warmup-{ordinal}-{operation}-intent.json").exists()
        calls.append((operation, payload["userId"]))
        if operation == "cart":
            return httpx.Response(200, json={})
        if ordinal == 1:
            return httpx.Response(504)
        return httpx.Response(200, json={"orderId": "fixture", "items": [
            {"item": {"productId": "0PUK6V6EV0", "quantity": 1}}
        ]})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as client:
            result = await warmup.warmup_once(runtime, client=client)
            assert result["transactions"][0]["checkout"]["status"] == 504
            assert [r["business_passed"] for r in result["transactions"]] == [False, True, True]
            with pytest.raises(FileExistsError):
                await warmup.warmup_once(runtime, client=client)
    asyncio.run(run())
    assert len(calls) == 6
    record = json.loads((runtime.private / "host/application-warmup-2-checkout-result.json").read_text())
    raw = (runtime.private / "host/application-warmup-2-checkout-body.bin").read_bytes()
    assert record["response_complete"]
    assert record["response_sha256"] == hashlib.sha256(raw).hexdigest()
    assert json.loads(raw)["orderId"] == "fixture"
    assert len(set(user for operation, user in calls if operation == "checkout")) == 3


def test_warmup_all_failed_stops_and_preserves_all_results(tmp_path, monkeypatch):
    runtime = setup_runtime(tmp_path, monkeypatch)
    calls = []

    def upstream(request):
        calls.append(request)
        return httpx.Response(503)

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as client:
            with pytest.raises(ValueError, match="no replacement"):
                await warmup.warmup_once(runtime, client=client)
    asyncio.run(run())
    assert len(calls) == 3
    result = json.loads((runtime.private / "host/application-warmup.json").read_text())
    assert len(result["transactions"]) == 3
    assert not any(row["business_passed"] for row in result["transactions"])


def test_warmup_total_deadline_preserves_ambiguous_request_without_replay(tmp_path, monkeypatch):
    runtime = setup_runtime(tmp_path, monkeypatch)
    monkeypatch.setitem(warmup.WARMUP, "total_request_deadline_seconds", 0.02)
    calls = []

    async def upstream(request):
        calls.append(request)
        await asyncio.sleep(0.2)
        return httpx.Response(200)

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as client:
            with pytest.raises(ValueError, match="no replacement"):
                await warmup.warmup_once(runtime, client=client)
    asyncio.run(run())
    assert len(calls) == 1
    result = json.loads((runtime.private / "host/application-warmup-1-cart-result.json").read_text())
    assert result["error_type"] == "TOTAL_DEADLINE"


def test_warmup_profile_drift_fails_before_io(tmp_path):
    runtime = ProductRuntimeV040(tmp_path)
    with pytest.raises(ValueError, match="profile differs"):
        warmup.application_warmup(runtime, {})
    assert not runtime.private.exists()
