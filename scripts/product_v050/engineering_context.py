"""Bounded context for one existing engineering round; no business writes."""

from datetime import UTC, datetime
import argparse
import json
import math
import time
from scripts.product_v050 import engineering_capture as ec, ingestion_evidence as ie


def collect_context(number):
    directory = ec.ROOT / f"round-{number}" / "context"
    directory.mkdir(mode=0o700)
    states = ec.load(ec.ROOT / f"snapshot-round-{number}.json")["container"]
    end = time.time()
    start = min(
        datetime.fromisoformat(
            r["State"]["StartedAt"].replace("Z", "+00:00")
        ).timestamp()
        for r in states
    )
    collection = ec.load(ec.PRIOR / "validation-authorization.json")["plan"][
        "collection"
    ]
    queries = {
        k: collection["actual_queries"][k] for k in collection["preparation_query_keys"]
    }
    chosen = set()
    for key, query in queries.items():
        if key.endswith(":kafka") and any(
            t in key for t in [":error_rate:", ":latency:", ":request_support:"]
        ):
            chosen.update(
                s
                for s in ie.selectors(query)
                if s.startswith(("kafka_request_", "kafka_produce_"))
            )
        elif key.endswith((":fraud-detection", ":payment")) and any(
            t in key for t in [":latency:", ":request_support:", ":queue_lag:"]
        ):
            chosen.update(
                s
                for s in ie.selectors(query)
                if s.startswith(("traces_span_metrics_", "kafka_consumer_group_"))
            )
    if len(chosen) > 10:
        raise ValueError("CONTEXT_SELECTOR_CAP")
    summaries = []
    for i, selector in enumerate(sorted(chosen)):
        body = ec.http_get(
            "http://127.0.0.1:19090/api/v1/query",
            dict(query=f"{selector}[{math.ceil(end - start) + 1}s]", time=end),
            directory / f"lifecycle-{i:02}.json",
        )
        series = []
        if body and body.get("status") == "success":
            for row in body.get("data", {}).get("result", []):
                values = row.get("values", [])
                series.append(
                    dict(
                        labels=row.get("metric"),
                        first_sample=values[0][0] if values else None,
                        last_sample=values[-1][0] if values else None,
                        count=len(values),
                        decreases=[
                            b[0]
                            for a, b in zip(values, values[1:])
                            if float(b[1]) < float(a[1])
                        ],
                    )
                )
        summaries.append(
            dict(
                selector=selector,
                series=series,
                observation_start=start,
                first_sample_is_birth_proof=False,
            )
        )
    logs = []
    traces = []
    for service in ["fraud-detection", "payment", "kafka"]:
        query = {
            "_source": ["@timestamp", "resource", "severity", "body", "traceId"],
            "size": 300,
            "sort": [{"@timestamp": {"order": "asc"}}],
            "query": {
                "bool": {
                    "filter": [
                        {"term": {"resource.service.name.keyword": service}},
                        {
                            "range": {
                                "@timestamp": {
                                    "gte": datetime.fromtimestamp(
                                        start, UTC
                                    ).isoformat(),
                                    "lte": datetime.fromtimestamp(end, UTC).isoformat(),
                                }
                            }
                        },
                    ]
                }
            },
        }
        body = ec.http_get(
            "http://127.0.0.1:19200/otel-logs-*/_search",
            {"source": json.dumps(query), "source_content_type": "application/json"},
            directory / f"logs-{service}.json",
        )
        hits = (body or {}).get("hits", {})
        rows = hits.get("hits", [])
        logs.append(
            dict(
                service=service,
                total=hits.get("total"),
                evidence_available=body is not None,
                possible_limit_reached=hits.get("total", {}).get("value", len(rows))
                > len(rows),
                returned=len(rows) if body is not None else None,
                eventstream_records=sum("eventstream" in str(r).lower() for r in rows),
                scope="returned records only",
            )
        )
        if service != "kafka":
            for variant, tags in [("all", "{}"), ("errors", '{"error":"true"}')]:
                body = ec.http_get(
                    "http://127.0.0.1:16686/jaeger/ui/api/traces",
                    dict(
                        service=service,
                        start=int(start * 1e6),
                        end=int(end * 1e6),
                        limit=50,
                        tags=tags,
                    ),
                    directory / f"traces-{service}-{variant}.json",
                )
                data = (body or {}).get("data", [])
                streams = [
                    dict(trace_id=t.get("traceID"), span=s)
                    for t in data
                    for s in t.get("spans", [])
                    if "eventstream" in s.get("operationName", "").lower()
                ]
                traces.append(
                    dict(
                        service=service,
                        variant=variant,
                        evidence_available=body is not None,
                        returned_traces=len(data) if body is not None else None,
                        eventstream_spans=streams,
                        possible_limit_reached=len(data) >= 50,
                    )
                )
    ec.save(
        directory / "summary.json",
        dict(
            start=start,
            end=end,
            lifecycle=summaries,
            logs=logs,
            traces=traces,
            otlp_start_time="UNAVAILABLE_IN_PROMETHEUS_MATRIX",
            changes="NO_FAULT_OR_RECOVERY_WRITES_IN_THIS_CALIBRATION",
        ),
    )
    print(
        json.dumps(
            {
                "round": number,
                "context_requests": len(chosen) + 7,
                "lifecycle_selectors": len(chosen),
                "eventstream_spans": sum(len(t["eventstream_spans"]) for t in traces),
            }
        ),
        flush=True,
    )


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("number", type=int, choices=[1, 2, 3])
    collect_context(p.parse_args().number)
