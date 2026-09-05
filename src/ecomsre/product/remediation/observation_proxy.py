"""Fixed read operations for the isolated API/Worker observation network."""

from datetime import datetime
import json
import re
from typing import Any
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
    opensearch_base_url: str


def mount_observation_proxy(
    app: FastAPI,
    profile: ObservationProxyProfileV1,
    *,
    client: httpx.Client | None = None,
) -> None:
    for url in (
        profile.prometheus_base_url,
        profile.jaeger_base_url,
        profile.opensearch_base_url,
    ):
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
        "prometheus/api/v1/label/service_name/values": (
            profile.prometheus_base_url,
            "/api/v1/label/service_name/values",
        ),
        "prometheus/api/v1/series": (profile.prometheus_base_url, "/api/v1/series"),
        "jaeger/api/traces": (profile.jaeger_base_url, "/jaeger/ui/api/traces"),
        "jaeger/api/services": (profile.jaeger_base_url, "/jaeger/ui/api/services"),
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

    @app.post("/observability/opensearch/otel-logs-*/_search")
    async def search_logs(request: Request) -> Response:
        # Existing Product uses POST for the read-only Search API. Reconstruct
        # only its fixed aggregation/range forms; never forward arbitrary DSL.
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > 16384:
                raise HTTPException(status_code=422, detail="OBSERVATION_QUERY_DENIED")
        try:
            body = fixed_log_query(json.loads(raw))
        except Exception:
            raise HTTPException(
                status_code=422, detail="OBSERVATION_QUERY_DENIED"
            ) from None
        try:
            response = transport.post(
                profile.opensearch_base_url.rstrip("/") + "/otel-logs-*/_search",
                json=body,
            )
            response.raise_for_status()
            if len(response.content) > 10_000_000:
                raise ValueError("observation exceeds bound")
            return Response(response.content, media_type="application/json")
        except Exception:
            raise HTTPException(
                status_code=409, detail="OBSERVATION_UNAVAILABLE"
            ) from None


def fixed_log_query(value: object) -> dict[str, Any]:
    if not isinstance(value, dict) or type(value.get("size")) is not int:
        raise ValueError("invalid search form")
    size = value["size"]
    service_field = "resource.service.name.keyword"
    fields = ["@timestamp", "resource.service.name", "severity.text", "body", "traceId"]
    if size == 0:
        count = value["aggs"]["services"]["terms"]["size"]
        if type(count) is not int or not 1 <= count <= 200:
            raise ValueError("invalid aggregation bound")
        expected = {
            "size": 0,
            "aggs": {"services": {"terms": {"field": service_field, "size": count}}},
        }
    else:
        if not 1 <= size <= 200:
            raise ValueError("invalid search bound")
        filters = value["query"]["bool"]["filter"]
        services = filters[0]["terms"][service_field]
        if (
            not isinstance(services, list)
            or not 1 <= len(services) <= 20
            or any(
                not isinstance(s, str)
                or re.fullmatch(r"[a-z][a-z0-9-]{0,63}", s) is None
                for s in services
            )
        ):
            raise ValueError("invalid service selectors")
        window = filters[1]["range"]["@timestamp"]
        start, end = (
            datetime.fromisoformat(window["gte"]),
            datetime.fromisoformat(window["lte"]),
        )
        if (
            start.tzinfo is None
            or end.tzinfo is None
            or not 0 < (end - start).total_seconds() <= 3600
        ):
            raise ValueError("invalid observation window")
        expected = {
            "size": size,
            "sort": [{"@timestamp": {"order": "desc"}}],
            "query": {
                "bool": {
                    "filter": [
                        {"terms": {service_field: services}},
                        {
                            "range": {
                                "@timestamp": {
                                    "gte": window["gte"],
                                    "lte": window["lte"],
                                }
                            }
                        },
                        *({"exists": {"field": field}} for field in fields[:4]),
                    ]
                }
            },
            "_source": fields,
        }
    if value != expected:
        raise ValueError("search form differs from the frozen connector")
    return expected
