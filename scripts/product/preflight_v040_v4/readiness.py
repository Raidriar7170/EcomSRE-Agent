"""Per-role admission with explicit Docker-health or fixed endpoint/dependency evidence."""

from __future__ import annotations
from datetime import UTC, datetime
from urllib.parse import urlencode
from typing import Any
from .common import require
from .http import LocalHTTP
from .identity import service_health


ENDPOINTS = {
    "prometheus": (19090, "/-/ready"),
    "jaeger": (11686, "/jaeger/ui/api/services"),
    "grafana": (18080, "/grafana/api/health"),
    "cart": (18080, "/api/cart?sessionId=preflight-v4-readiness&currencyCode=USD"),
}
DEPENDENCIES = {
    "accounting": ("kafka", "astronomy-db", "otel-collector"),
    "fraud-detection": ("kafka", "flagd", "otel-collector"),
}


def dependency_order(roles: dict[str, Any]) -> list[str]:
    remaining = set(roles)
    result: list[str] = []
    while remaining:
        eligible = sorted(
            role
            for role in remaining
            if set(roles[role]["service"].get("depends_on", {})) <= set(result)
        )
        require(bool(eligible), "COMPOSE_DEPENDENCY_CYCLE")
        result.extend(eligible)
        remaining.difference_update(eligible)
    return result


def dependencies_ready(
    role: str, roles: dict[str, Any], records: dict[str, Any]
) -> bool:
    for name, condition in roles[role]["service"].get("depends_on", {}).items():
        if name not in records or records[name]["State"]["Running"] is not True:
            return False
        if (
            condition["condition"] == "service_healthy"
            and records[name]["State"].get("Health", {}).get("Status") != "healthy"
        ):
            return False
    return True


def role_readiness(
    roles: dict[str, Any], records: dict[str, Any], http: dict[int, LocalHTTP], key: str
) -> dict[str, bool]:
    ready = {}
    for role, (port, path) in ENDPOINTS.items():
        result = http[port].request(key + "-" + role, "GET", path)
        okay = result["status"] is not None and 200 <= result["status"] < 300
        body = result.get("body")
        if role == "grafana":
            okay = okay and isinstance(body, dict) and body.get("database") == "ok"
        if role == "jaeger":
            okay = (
                okay and isinstance(body, dict) and isinstance(body.get("data"), list)
            )
        if role == "cart":
            okay = (
                okay and isinstance(body, dict) and isinstance(body.get("items"), list)
            )
        ready[role] = okay
    # flagd 8016 is OFREP; evaluation reads the frozen load flag without a write.
    flag = http[18016].request(
        key + "-flagd", "POST", "/ofrep/v1/evaluate/flags/loadGeneratorVUs", {}
    )
    ready["flagd"] = (
        flag["status"] == 200
        and isinstance(flag.get("body"), dict)
        and flag["body"].get("value") == 25
    )
    query = urlencode({"query": "sum(traces_span_metrics_calls_total)"})
    collector = http[19090].request(key + "-collector", "GET", "/api/v1/query?" + query)
    body = collector.get("body") or {}
    samples = body.get("data", {}).get("result", []) if isinstance(body, dict) else []
    ready["otel-collector"] = (
        collector["status"] == 200
        and bool(samples)
        and all(float(x["value"][1]) > 0 for x in samples)
    )
    for role, deps in DEPENDENCIES.items():
        row = records[role]
        since = datetime.fromisoformat(row["State"]["StartedAt"].replace("Z", "+00:00"))
        stable = (datetime.now(UTC) - since).total_seconds() >= 30 and row[
            "RestartCount"
        ] == 0
        dependency_ok = all(
            records[name]["State"]["Running"] is True
            and (
                records[name]["State"]["Health"]["Status"] == "healthy"
                if "Health" in records[name]["State"]
                else ready.get(name) is True
            )
            for name in deps
        )
        ready[role] = stable and dependency_ok
    # Every role that has neither a Docker check nor this fixed contract is denied.
    for role in roles:
        if "Health" not in records[role]["State"]:
            require(role in ready, "ROLE_READINESS_UNDECLARED:" + role)
    return ready


def health_map(
    view: dict[str, Any],
    plan: dict[str, Any],
    births: list[dict[str, Any]],
    http: dict[int, LocalHTTP],
    key: str,
) -> dict[str, Any]:
    ids = {
        b["role"]: b["record"]["Id"]
        for b in births
        if b["kind"] == "container" and b["role"] in plan["roles"]
    }
    by_id = {r["Id"]: r for r in view["resources"]["container"]}
    require(set(ids) == set(plan["roles"]), "SANDBOX_BIRTH_SET_INCOMPLETE")
    records = {role: by_id[identifier] for role, identifier in ids.items()}
    ready = role_readiness(plan["roles"], records, http, key)
    return service_health(
        view["resources"]["container"],
        project=plan["project"],
        attempt=plan["attempt_id"],
        births=ids,
        readiness=ready,
    )
