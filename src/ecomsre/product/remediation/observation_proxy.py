"""Strict GET-only Prometheus/Jaeger proxy for the isolated API/Worker network."""

from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import Response
import httpx
from pydantic import ConfigDict

from ecomsre.product.contracts import ProductModelV1


class ObservationProxyProfileV1(ProductModelV1):
    model_config = ConfigDict(frozen=True, extra="forbid")
    prometheus_base_url: str
    jaeger_base_url: str


def mount_observation_proxy(
    app: FastAPI,
    profile: ObservationProxyProfileV1,
    *,
    client: httpx.Client | None = None,
) -> None:
    for url in (profile.prometheus_base_url, profile.jaeger_base_url):
        parsed = urlsplit(url)
        if (
            parsed.scheme != "http"
            or parsed.hostname not in {"127.0.0.1", "host.docker.internal"}
            or parsed.port is None
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
            or parsed.username
            or parsed.password
        ):
            raise ValueError("observation proxy upstream is not a fixed local origin")
    transport = client or httpx.Client(
        timeout=10, trust_env=False, follow_redirects=False
    )
    paths = {
        "prometheus/api/v1/query": (profile.prometheus_base_url, "/api/v1/query"),
        "prometheus/api/v1/query_range": (
            profile.prometheus_base_url,
            "/api/v1/query_range",
        ),
        "prometheus/api/v1/labels": (profile.prometheus_base_url, "/api/v1/labels"),
        "jaeger/api/traces": (profile.jaeger_base_url, "/api/traces"),
        "jaeger/api/services": (profile.jaeger_base_url, "/api/services"),
    }

    @app.get("/observability/{path:path}")
    def observe(path: str, request: Request) -> Response:
        if path not in paths:
            raise HTTPException(status_code=404, detail="OBSERVATION_ROUTE_DENIED")
        origin, fixed_path = paths[path]
        try:
            response = transport.get(
                origin.rstrip("/") + fixed_path, params=request.query_params
            )
            response.raise_for_status()
            if len(response.content) > 8 * 1024 * 1024:
                raise ValueError("observation exceeds bound")
            return Response(response.content, media_type="application/json")
        except Exception:
            raise HTTPException(
                status_code=409, detail="OBSERVATION_UNAVAILABLE"
            ) from None
