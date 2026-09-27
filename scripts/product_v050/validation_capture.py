"""Private raw-response retention and a fixed query-derived isolation bound."""

from contextlib import contextmanager
from datetime import datetime, timedelta
import json
import re
from urllib.parse import urlsplit

from ecomsre.product.connectors._http import raw_response_observer

VERSION = "fixed-isolation-raw-capture-v1"
# Frozen margins, never extended in response to a diagnosis.
SCRAPE_BOUND = 30
INGESTION_BOUND = 30
PREPARATION_CAP = 3660


def protocol(queries):
    inner = [
        int(n) * {"s": 1, "m": 60, "h": 3600}[u]
        for q in queries.values()
        for n, u in re.findall(r"\[(\d+)([smh])\]", q)
    ]
    if not inner or max(inner) > 300:
        raise ValueError("actual query lookback differs from validated collection")
    return dict(
        version=VERSION,
        actual_queries=queries,
        core_outer_seconds=300,
        metric_inner_seconds=max(inner),
        metric_effective_seconds=300 + max(inner),
        changes_seconds=3600,
        logs_seconds=300,
        traces_seconds=300,
        scrape_bound_seconds=SCRAPE_BOUND,
        ingestion_bound_seconds=INGESTION_BOUND,
        preparation_cap_seconds=PREPARATION_CAP,
        readiness_attempts=1,
        fault_observation_seconds=120,
        traffic_requests=3,
        restore_cap_seconds=360,
        order=["TARGET", "HEALTHY", "CORE", "RECURRENCE"],
        role_attempts=1,
    )


def earliest(plan, *, last_recovery, last_change, ready_at):
    def dt(v):
        d = datetime.fromisoformat(v)
        if d.tzinfo is None:
            raise ValueError("UTC-aware observation bounds required")
        return d

    margin = plan["scrape_bound_seconds"] + plan["ingestion_bound_seconds"]
    return max(
        dt(ready_at),
        dt(last_recovery)
        + timedelta(seconds=plan["metric_effective_seconds"] + margin),
        dt(last_change) + timedelta(seconds=plan["changes_seconds"] + margin),
    )


@contextmanager
def capture(objects, index_path, *, occurrence):
    """Raw bytes stay in private CAS; append-only index binds each occurrence."""
    if index_path.exists():
        raise ValueError("raw capture occurrence already exists")
    index_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    entries = []

    def record(value):
        url = urlsplit(value["url"])
        if (
            url.hostname not in {"127.0.0.1", "localhost"}
            or url.username
            or url.password
        ):
            raise ValueError("raw capture only permits owned loopback read endpoints")
        content = value.pop("content")
        obj = objects.put_bytes(content, media_type="application/octet-stream")
        entry = value | dict(
            occurrence=occurrence,
            response_object_sha256=obj.object_sha256,
            raw_bytes=len(content),
            credential_headers_retained=False,
        )
        with index_path.open("a") as stream:
            stream.write(json.dumps(entry, sort_keys=True) + "\n")
        index_path.chmod(0o600)
        entries.append(entry)

    token = raw_response_observer.set(record)
    try:
        yield entries
    finally:
        raw_response_observer.reset(token)


def verify_raw(entries, *, occurrence, incident_id, snapshots, queries, scrape):
    """Bind retained wire requests to this incident's actual connector windows."""
    if not entries or any(
        e["occurrence"] != occurrence or e["truncated"] for e in entries
    ):
        raise ValueError("raw occurrence/truncation differs")
    if (
        not scrape.get("scrape_recency_passed")
        or scrape.get("incident_id") != incident_id
    ):
        raise ValueError("bounded scrape receipt missing")
    kinds = {
        "ERROR_RATE": "error_rate",
        "LATENCY_P95_MS": "latency",
        "REQUEST_SUPPORT": "request_support",
        "QUEUE_LAG": "queue_lag",
    }
    for snapshot in snapshots:
        action = snapshot["action"]
        result = snapshot["connector_result"]
        source = result["source"]
        if source not in {"METRICS", "LOGS", "TRACES"}:
            continue
        reads = [
            e
            for e in entries
            if e.get("action_context")
            and e["action_context"]["incident_id"] == incident_id
            and e["action_context"]["action_id"] == action["action_id"]
        ]
        if not reads or any(
            e["action_context"]["context"]["window"] != result["window"] for e in reads
        ):
            raise ValueError("raw action/window binding missing")
        start = datetime.fromisoformat(result["window"]["started_at"])
        end = datetime.fromisoformat(result["window"]["ended_at"])
        if source == "METRICS":
            for service in action["target_services"]:
                for kind in action["request"]["metric_kinds"]:
                    query = queries["prometheus:" + kinds[kind] + ":" + service]
                    if not any(
                        e["params"].get("query") == query
                        and e["params"].get("start") == start.timestamp()
                        and e["params"].get("end") == end.timestamp()
                        and e["params"].get("step") == 10
                        for e in reads
                    ):
                        raise ValueError("fixed metric request/window missing")
        elif source == "TRACES":
            if not all(
                e["params"].get("start") == int(start.timestamp() * 1_000_000)
                and e["params"].get("end") == int(end.timestamp() * 1_000_000)
                for e in reads
            ):
                raise ValueError("fixed trace window missing")
        else:
            for e in reads:
                ranges = [
                    x["range"]
                    for x in e["json_body"]["query"]["bool"]["filter"]
                    if "range" in x
                ]
                if not any(
                    datetime.fromisoformat(r["gte"]) == start
                    and datetime.fromisoformat(r["lte"]) == end
                    for fields in ranges
                    for r in fields.values()
                ):
                    raise ValueError("fixed log window missing")
