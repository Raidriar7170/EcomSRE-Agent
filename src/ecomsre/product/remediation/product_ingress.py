"""Literal Product ingress; no credentials, control target or retry authority."""

import asyncio
import re

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import Response
import httpx

_TOTAL_SECONDS = 25
_ID = r"[A-Za-z0-9_-]{1,128}"
_ROUTES = {
    "GET": (
        r"/readyz",
        rf"/v1/jobs/{_ID}",
        rf"/v1/incidents/{_ID}/evidence",
        rf"/v1/remediation-attempts/{_ID}",
    ),
    "POST": (
        r"/v1/environments",
        r"/v1/incidents",
        rf"/v1/environments/{_ID}/(?:verify-jobs|baseline-jobs|changes)",
        rf"/v1/incidents/{_ID}/(?:diagnosis-jobs|remediation-candidates)",
        rf"/v1/remediation-candidates/{_ID}/(?:approvals|attempts)",
    ),
}


def product_ingress_app(*, client: httpx.AsyncClient | None = None) -> FastAPI:
    app = FastAPI(openapi_url=None, docs_url=None, redoc_url=None)
    transport = client or httpx.AsyncClient(
        timeout=25, trust_env=False, follow_redirects=False
    )

    @app.api_route("/{path:path}", methods=["GET", "POST"])
    async def forward(request: Request, path: str) -> Response:
        try:
            async with asyncio.timeout(_TOTAL_SECONDS):
                return await forward_once(request, path)
        except TimeoutError:
            raise HTTPException(504, "PRODUCT_INGRESS_DEADLINE") from None

    async def forward_once(request: Request, path: str) -> Response:
        target = "/" + path
        if (
            request.url.query
            or request.scope.get("raw_path", b"") != target.encode("ascii", "replace")
            or not any(re.fullmatch(p, target) for p in _ROUTES[request.method])
        ):
            raise HTTPException(404, "PRODUCT_INGRESS_ROUTE_DENIED")
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > 1024 * 1024:
                raise HTTPException(413, "PRODUCT_INGRESS_BODY_TOO_LARGE")
        headers = {
            key: request.headers[key]
            for key in ("authorization", "content-type", "idempotency-key")
            if key in request.headers
        }
        try:
            # Fixed service origin, explicit header allowlist, one transmission.
            # Ambiguous POST transport failures are returned, never retried.
            async with transport.stream(
                request.method, "http://api:8080" + target,
                content=bytes(body), headers=headers, follow_redirects=False,
            ) as upstream:
                output = bytearray()
                async for chunk in upstream.aiter_bytes():
                    output.extend(chunk)
                    if len(output) > 8 * 1024 * 1024:
                        raise ValueError("response exceeds bound")
                return Response(
                    bytes(output), status_code=upstream.status_code,
                    headers={"content-type": upstream.headers.get(
                        "content-type", "application/json"
                    )},
                )
        except (httpx.HTTPError, ValueError):
            raise HTTPException(502, "PRODUCT_INGRESS_UNAVAILABLE") from None

    return app
