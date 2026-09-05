"""Fixed ingress preserves authentication and cannot become a control proxy."""

from fastapi.testclient import TestClient
import httpx
import pytest

from ecomsre.product.remediation.product_ingress import product_ingress_app


def fixture_client(handler):
    transport = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return TestClient(product_ingress_app(client=transport))


def test_ingress_forwards_single_post_with_only_authority_headers():
    requests = []

    def upstream(request):
        requests.append(request)
        assert str(request.url) == "http://api:8080/v1/environments"
        assert request.headers["authorization"] == "Bearer private-test"
        assert request.headers["idempotency-key"] == "once"
        assert "cookie" not in request.headers
        assert "x-target" not in request.headers
        assert request.content == b'{"name":"test"}'
        return httpx.Response(202, json={"accepted": True})

    with fixture_client(upstream) as client:
        response = client.post(
            "/v1/environments", content=b'{"name":"test"}',
            headers={"Authorization": "Bearer private-test", "Idempotency-Key": "once",
                     "Cookie": "secret=value", "X-Target": "http://flagd:8016"},
        )
    assert response.status_code == 202
    assert len(requests) == 1


@pytest.mark.parametrize("method,path", [
    ("POST", "/control/restore"), ("POST", "/observability/flags"),
    ("POST", "/v1/environments?target=http://flagd:8016"),
    ("GET", "/v1/jobs/a%2Fb"), ("POST", "/v1/unknown"),
    ("CONNECT", "/v1/environments"), ("DELETE", "/v1/environments"),
])
def test_ingress_denies_unlisted_routes_without_upstream(method, path):
    def upstream(request):
        pytest.fail("denied route reached upstream")

    with fixture_client(upstream) as client:
        response = client.request(method, path)
    assert response.status_code in {404, 405}


def test_ingress_does_not_retry_ambiguous_post_or_supply_credentials():
    calls = []

    def upstream(request):
        calls.append(request)
        assert "authorization" not in request.headers
        raise httpx.ReadTimeout("receipt lost")

    with fixture_client(upstream) as client:
        response = client.post("/v1/remediation-candidates/test/attempts", json={})
    assert response.status_code == 502
    assert len(calls) == 1


def test_ingress_preserves_upstream_auth_rejection():
    with fixture_client(lambda request: httpx.Response(401, json={"error": "denied"})) as client:
        assert client.post("/v1/environments", json={}).status_code == 401


def test_ingress_never_follows_upstream_redirect():
    calls = []

    def upstream(request):
        calls.append(request)
        return httpx.Response(307, headers={"location": "http://flagd:8016"})

    with fixture_client(upstream) as client:
        response = client.get("/readyz")
    assert response.status_code == 307
    assert "location" not in response.headers
    assert len(calls) == 1


def test_ingress_rejects_oversized_body_before_upstream():
    def upstream(request):
        pytest.fail("oversized body reached upstream")

    with fixture_client(upstream) as client:
        assert client.post("/v1/environments", content=b"x" * (1024 * 1024 + 1)).status_code == 413


@pytest.mark.parametrize("slow_side", ["inbound", "upstream"])
def test_total_deadline_covers_streaming_and_never_retries(monkeypatch, slow_side):
    import asyncio
    import ecomsre.product.remediation.product_ingress as ingress

    monkeypatch.setattr(ingress, "_TOTAL_SECONDS", 0.02)
    calls = []

    async def upstream(request):
        calls.append(request)
        await asyncio.sleep(0.2)
        return httpx.Response(200, json={})

    async def slow_body():
        yield b"{"
        await asyncio.sleep(0.2)
        yield b"}"

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as target:
            app = ingress.product_ingress_app(client=target)
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app), base_url="http://test"
            ) as client:
                response = await client.post(
                    "/v1/environments",
                    content=slow_body() if slow_side == "inbound" else b"{}",
                )
                assert response.status_code == 504
    asyncio.run(run())
    assert len(calls) == (0 if slow_side == "inbound" else 1)
