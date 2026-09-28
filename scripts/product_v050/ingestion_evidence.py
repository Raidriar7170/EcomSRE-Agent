"""Bounded OTLP ingestion evidence. Range-vector timestamps are sample times.

This module never changes diagnostic windows, fills missing series, or proves
business health. A future batch must explicitly freeze this protocol; v1 stays v1.
"""

import math
from datetime import datetime
import re
from urllib.parse import urlsplit

from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22 as sha

VERSION = "ingestion-sample-evidence-v2"
MAX_REQUESTS = 80
MAX_SECONDS = 120
# Acquisition acceptance bounds, not measured ingestion latency or business limits.
MAX_SAMPLE_AGE = 30
SELECTOR = re.compile(r"[a-zA-Z_:][a-zA-Z0-9_:]*\{[^{}]*\}")
MATCHER = re.compile(r'([a-zA-Z_][a-zA-Z0-9_]*)(!=|=)"([^"\\]*)"')


def topology(collector, prometheus_command, queries):
    pipeline = collector["service"]["pipelines"]["metrics"]
    if pipeline != {
        "receivers": ["otlp", "docker_stats", "kafkametrics", "span_metrics"],
        "processors": ["memory_limiter"],
        "exporters": ["otlp_http/prometheus"],
    }:
        raise ValueError("unsupported actual metrics pipeline")
    if (
        collector["exporters"]["otlp_http/prometheus"].get("endpoint")
        != "http://prometheus:9090/api/v1/otlp"
        or "--web.enable-otlp-receiver" not in prometheus_command
        or "span_metrics"
        not in collector["service"]["pipelines"]["traces"]["exporters"]
        or collector["service"]["pipelines"]["traces"]["receivers"] != ["otlp"]
    ):
        raise ValueError("OTLP receiver/exporter binding differs")
    if not all(
        k in collector["receivers"] for k in ("otlp", "docker_stats", "kafkametrics")
    ):
        raise ValueError("receiver configuration missing")
    for key, query in queries.items():
        service = key.rsplit(":", 1)[-1]
        for selector in selectors(query):
            if not selector.startswith(
                (
                    "traces_span_metrics_",
                    "kafka_consumer_group_",
                    "container_",
                    "kafka_request_",
                    "kafka_produce_",
                )
            ):
                raise ValueError("metric receiver provenance unsupported")
            identities = [
                (k, v)
                for k, op, v in MATCHER.findall(selector)
                if op == "=" and k in {"service_name", "group", "container_name"}
            ]
            if not identities or any(
                (not v.endswith("-" + service) and v != service)
                if k == "container_name"
                else v != service
                for k, v in identities
            ):
                raise ValueError("query service binding differs")
    result = dict(
        version=VERSION,
        mode="OTLP_PUSH",
        collector_sha256=sha(collector),
        prometheus_command_sha256=sha(prometheus_command),
        queries_sha256=sha(queries),
        max_requests=MAX_REQUESTS,
        max_seconds=MAX_SECONDS,
        attempts=1,
        max_sample_age_seconds=MAX_SAMPLE_AGE,
        exact_ingestion_latency="UNKNOWN_NOT_EXPOSED",
        collector_telemetry="NOT_REQUIRED_NOT_PROVEN_AVAILABLE",
        source_routes={
            "traces_span_metrics_": "otlp/traces -> span_metrics -> metrics",
            "kafka_consumer_group_": "kafkametrics -> metrics",
            "container_": "docker_stats -> metrics",
            "kafka_request_": "application OTLP -> otlp/metrics",
            "kafka_produce_": "application OTLP -> otlp/metrics",
        },
    )
    result["sha256"] = sha(result)
    return result


def topology_v3(collector, command, queries, *, application_object_sha256=None):
    """Future explicit protocol; no batch installation or historical upgrade."""
    from scripts.product_v050.sampling_support import VERSION as V3

    for query in queries.values():
        windows = {
            int(n) * {"s": 1, "m": 60}[u]
            for n, u in re.findall(r"\[(\d+)([sm])\]", query)
        }
        if len(windows) > 1:
            raise ValueError("v3 mixed selector lookbacks are unsupported")
        if (
            query.count("[") != len(re.findall(r"\[\d+[sm]\]", query))
            or " offset " in query
            or "@" in query
        ):
            raise ValueError("v3 unsupported temporal query syntax")
    result = topology(collector, command, queries)
    result.pop("sha256")
    result.pop("max_sample_age_seconds")
    result.update(
        version=V3,
        application_object_sha256=application_object_sha256,
        maximum_request_start_delay_seconds=30,
        sampling_policy="producer-cycle-25pct-plus-1s-v1",
    )
    result["sha256"] = sha(result)
    return result


def validate_topology(binding, collector, command, queries):
    from scripts.product_v050.sampling_support import VERSION as V3

    expected = (
        topology_v3(
            collector,
            command,
            queries,
            application_object_sha256=binding.get("application_object_sha256"),
        )
        if binding.get("version") == V3
        else topology(collector, command, queries)
    )
    if binding != expected:
        raise ValueError("deployment ingestion mode/configuration differs")


def selectors(query):
    found = list(dict.fromkeys(SELECTOR.findall(query)))
    if not found:
        raise ValueError("query has no supported explicit selector")
    for selector in found:
        body = selector.split("{", 1)[1][:-1]
        matches = MATCHER.findall(body)
        if ",".join(f'{k}{op}"{v}"' for k, op, v in matches) != body:
            raise ValueError("unsupported selector matcher")
        if not any(
            k in {"service_name", "container_name", "group"} and op == "="
            for k, op, _ in matches
        ):
            raise ValueError("service identity matcher required")
    return found


def expected_reads(query, start, end, *, version=VERSION):
    inner = max(
        [
            int(n) * {"s": 1, "m": 60}[u]
            for n, u in re.findall(r"\[(\d+)([sm])\]", query)
        ]
        or [0]
    )
    support_start = start - inner
    # Instant range selector returns stored sample timestamps, unlike query_range.
    width = math.ceil(end - support_start) + 1
    if version == "ingestion-sample-evidence-v3" and not inner:
        width += 300  # retain predecessor evidence for the first instant query
    return [
        (s, support_start, {"query": f"{s}[{width}s]", "time": end})
        for s in selectors(query)
    ]


def label_matches(selector, labels):
    name, body = selector.split("{", 1)
    return labels.get("__name__") == name and all(
        (labels.get(k) == v if op == "=" else labels.get(k, "") != v)
        for k, op, v in MATCHER.findall(body[:-1])
    )


def sample_state(body, selector, start, end, bound):
    if body.get("status") != "success" or body.get("warnings") or body.get("infos"):
        return "QUERY_INCOMPLETE"
    data = body.get("data", {})
    if data.get("resultType") != "matrix":
        return "WRONG_RESULT_TYPE"
    rows = data.get("result")
    if not isinstance(rows, list):
        return "MALFORMED"
    if not rows:
        return "EMPTY"
    for row in rows:
        if not label_matches(selector, row.get("metric", {})):
            return "WRONG_SERVICE_OR_SELECTOR"
        values = row.get("values", [])
        if not values or row.get("histograms"):
            return "UNSUPPORTED_OR_MISSING_SAMPLES"
        try:
            times = [float(v[0]) for v in values]
            numeric = [float(v[1]) for v in values]
        except (ValueError, TypeError, IndexError):
            return "MALFORMED"
        if not all(math.isfinite(x) for x in times + numeric):
            return "NONFINITE"
        if times != sorted(set(times)) or times[-1] > end or times[0] < start - 1:
            return "SAMPLE_TIME_SCOPE_ERROR"
        if end - times[-1] > bound:
            return "STALE"
        if (
            times[0] > start + bound
            or any(b - a > bound for a, b in zip(times, times[1:]))
            or len(times) < 2
        ):
            return "WINDOW_INCOMPLETE"
    return "FRESH_COVERED"


def query_coverage(query, states):
    """Fixed query source alternatives, independent of rule match/health labels."""
    if any(v not in {"EMPTY", "FRESH_COVERED"} for v in states.values()):
        return "INVALID_SAMPLE_EVIDENCE"
    # Empty error subsets can be legitimate only with their same-source total.
    required = []
    for selector, state in states.items():
        if (
            selector.startswith("kafka_request_failed_total{")
            or 'status_code="STATUS_CODE_ERROR"' in selector
        ):
            companion = selector.replace(
                "kafka_request_failed_total{", "kafka_request_count_total{"
            ).replace(',status_code="STATUS_CODE_ERROR"', "")
            if state == "EMPTY" and states.get(companion) == "FRESH_COVERED":
                continue
        required.append((selector, state))
    # Each fixed query has SDK or spanmetrics alternatives (or one direct source).
    groups = {}
    for selector, state in required:
        family = "span" if selector.startswith("traces_span_metrics_") else "direct"
        groups.setdefault(family, []).append(state)
    available = [
        all(v == "FRESH_COVERED" for v in values) for values in groups.values()
    ]
    if (any(available) if " or " in query else all(available)) and available:
        return (
            "SUPPORTED_WITH_EXPLICIT_EMPTY_ALTERNATIVES"
            if "EMPTY" in states.values()
            else "SUPPORTED"
        )
    return "UNKNOWN_NO_COMPLETE_SOURCE"


def verify(
    binding,
    *,
    entries,
    occurrence,
    incident_id,
    queries,
    read_bytes,
    collector,
    command,
    requirements,
):
    validate_topology(binding, collector, command, queries)
    if not requirements:
        raise ValueError("fixed required metric queries missing")
    if any(e.get("occurrence") != occurrence or e.get("truncated") for e in entries):
        raise ValueError("sample occurrence/truncation differs")
    from scripts.product_v050 import sampling_support as support

    version = binding["version"]
    modern = version == support.VERSION
    application = None
    if modern and binding.get("application_object_sha256"):
        application = __import__("json").loads(
            read_bytes(binding["application_object_sha256"])
        )
    # Engineering declarations bind retained bytes, not their source semantics.
    # Do not admit them into qualification/freeze/promotion as proven defaults.
    if (application or {}).get("versioned_producer_defaults"):
        raise ValueError("engineering default declarations are not formal evidence")
    report = []
    sample_entries = [
        e
        for e in entries
        if (e.get("action_context") or {}).get("ingestion_version") == version
    ]
    if not sample_entries or len(sample_entries) > binding["max_requests"]:
        raise ValueError("sample request cap exceeded or no samples")
    first = min(datetime.fromisoformat(e["requested_at"]) for e in sample_entries)
    last = max(datetime.fromisoformat(e["received_at"]) for e in sample_entries)
    if (last - first).total_seconds() > binding["max_seconds"] or last < first:
        raise ValueError("sample acquisition time cap differs")
    for req in requirements:
        query, start, end = req["query"], req["start"], req["end"]
        if query != queries.get(req["query_key"]) or not start < end:
            raise ValueError("required query identity/window differs")
        states, details, bodies = {}, {}, {}
        for selector, support_start, params in expected_reads(
            query, start, end, version=binding["version"]
        ):
            matches = [
                e
                for e in sample_entries
                if e["params"] == params
                and e["action_context"].get("incident_id") == incident_id
                and e["action_context"].get("binding_sha256") == binding["sha256"]
            ]
            if len(matches) != 1:
                states[selector] = "MISSING_RAW_SAMPLE_QUERY"
                continue
            e = matches[0]
            if (
                urlsplit(e["url"]).path != "/api/v1/query"
                or e.get("status_code") != 200
                or e.get("method") != "GET"
            ):
                states[selector] = "WRONG_SAMPLE_REQUEST"
                continue
            body = __import__("json").loads(read_bytes(e["response_object_sha256"]))
            if modern:
                details[selector] = support.assess(
                    body,
                    selector,
                    support_start,
                    end,
                    support.profile(collector, selector, application),
                    query_start=start,
                    inner_seconds=start - support_start,
                )
                details[selector]["raw_response_sha256"] = e["response_object_sha256"]
                states[selector] = details[selector]["state"]
                if states[selector] in {"EMPTY", "FRESH_COVERED"}:
                    bodies[selector] = body
            else:
                states[selector] = sample_state(
                    body,
                    selector,
                    support_start,
                    end,
                    binding["max_sample_age_seconds"],
                )
        correspondence = support.correspondence(bodies) if modern else []
        report.append(
            dict(
                requirement=req,
                selectors=states,
                coverage=(
                    "INVALID_SAMPLE_EVIDENCE"
                    if correspondence
                    else query_coverage(query, states)
                ),
            )
            | (
                dict(
                    sample_diagnostics=details,
                    correspondence_reasons=correspondence,
                    evidence_scope="RETURNED_SERIES_ONLY_NOT_COMPLETE_NEGATIVE",
                )
                if modern
                else {}
            )
        )
    return dict(
        version=version,
        mode=binding["mode"],
        binding_sha256=binding["sha256"],
        occurrence=occurrence,
        incident_id=incident_id,
        passed=all(r["coverage"].startswith("SUPPORTED") for r in report),
        queries=report,
        exact_ingestion_latency="UNKNOWN_NOT_EXPOSED",
        business_health="NOT_EVALUATED",
    )


def acquire(
    http, endpoint, binding, requirements, *, incident_id, monotonic, preparation=False
):
    """One fixed pass; no event creation, retries, polling or candidate access."""
    from ecomsre.product.connectors._http import raw_request_context

    requests = {}
    for req in requirements:
        for _, _, params in expected_reads(
            req["query"], req["start"], req["end"], version=binding["version"]
        ):
            requests[(params["query"], params["time"])] = params
    if (
        not requests
        or len(requests) + (len(requirements) if preparation else 0)
        > binding["max_requests"]
    ):
        raise ValueError("bounded sample request cap exceeded before dispatch")
    started = monotonic()
    token = raw_request_context.set(
        dict(
            ingestion_version=binding["version"],
            incident_id=incident_id,
            binding_sha256=binding["sha256"],
        )
    )
    try:
        if preparation:
            for req in requirements:
                if monotonic() - started >= binding["max_seconds"]:
                    raise ValueError("bounded preparation deadline")
                http.request_json(
                    "GET",
                    endpoint.rstrip("/") + "/api/v1/query_range",
                    params=dict(
                        query=req["query"], start=req["start"], end=req["end"], step=10
                    ),
                )
        for params in requests.values():
            if monotonic() - started >= binding["max_seconds"]:
                raise ValueError("bounded sample acquisition deadline")
            http.request_json(
                "GET", endpoint.rstrip("/") + "/api/v1/query", params=params
            )
        if monotonic() - started > binding["max_seconds"]:
            raise ValueError("bounded sample acquisition deadline")
    finally:
        raw_request_context.reset(token)


def event_requirements(entries, incident_id, queries):
    keys = {v: k for k, v in queries.items()}
    required = {}
    for entry in entries:
        context = entry.get("action_context") or {}
        if (
            context.get("incident_id") != incident_id
            or urlsplit(entry["url"]).path != "/api/v1/query_range"
        ):
            continue
        params = entry["params"]
        query = params["query"]
        if query not in keys:
            raise ValueError("event query outside frozen query map")
        item = dict(
            query_key=keys[query], query=query, start=params["start"], end=params["end"]
        )
        required[sha(item)] = item
    return list(required.values())


def verify_receipt(
    receipt,
    *,
    binding,
    entries,
    occurrence,
    incident_id,
    queries,
    read_bytes,
    requirements,
):
    if receipt.get("version") != binding.get("version") or receipt.get(
        "version"
    ) not in {VERSION, "ingestion-sample-evidence-v3"}:
        raise ValueError("ingestion protocol receipt version differs")
    collector = __import__("json").loads(read_bytes(receipt["collector_object_sha256"]))
    command = __import__("json").loads(
        read_bytes(receipt["prometheus_command_object_sha256"])
    )
    actual = verify(
        binding,
        entries=entries,
        occurrence=occurrence,
        incident_id=incident_id,
        queries=queries,
        read_bytes=read_bytes,
        collector=collector,
        command=command,
        requirements=requirements,
    )
    if receipt.get("assessment") != actual or not actual["passed"]:
        raise ValueError("target sample freshness/coverage not established")
    if incident_id is None:
        for req in requirements:
            reads = [
                e
                for e in entries
                if urlsplit(e["url"]).path == "/api/v1/query_range"
                and e["params"]
                == dict(query=req["query"], start=req["start"], end=req["end"], step=10)
            ]
            if len(reads) != 1 or reads[0]["status_code"] != 200:
                raise ValueError("preparation diagnostic query unavailable")
            body = __import__("json").loads(
                read_bytes(reads[0]["response_object_sha256"])
            )
            if (
                body.get("status") != "success"
                or body.get("warnings")
                or body.get("infos")
            ):
                raise ValueError("preparation diagnostic query incomplete")
    return actual
