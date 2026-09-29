"""Retained live-03 A/B/C experiment. No live backend, original DB writes or policy changes."""

from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
import sqlite3

import httpx
from pydantic import TypeAdapter
from ecomsre.dta_v2.v22.memory import MemoryReadOutcomeV22, build_memory_views_v22
from ecomsre.dta_v2.v22.predicates import evaluate_no_incident_v22
from ecomsre.dta_v2.v22.read_contracts import EvidenceSourceV22
from ecomsre.dta_v2.v22.replay import ReadOutcomeV22
from ecomsre.product.baselines import EnvironmentBaselineV1
from ecomsre.product.contracts import (
    ServiceIdentityMapV1,
    ServiceIdentityV1,
    ConnectorConfigV1,
)
from ecomsre.product.connectors.base import ConnectorQueryContextV1, ConnectorWindowV1
from ecomsre.product.connectors.credentials import CredentialResolverV1
from ecomsre.product.connectors.prometheus import PrometheusConnectorV1
from ecomsre.product.incidents.contracts import IncidentRecordV1
from ecomsre.product.incidents.diagnosis_bridge import ProductDiagnosisBridgeV1
from ecomsre.product.incidents.read_backend import ProductReadAcquisitionV1
from scripts.product_v050.health_semantics_replay import increments

GROUPS = ("BUSINESS_REQUEST", "BUSINESS_ASYNC", "CONTROL_STREAM", "UNCLASSIFIED")


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


class Inputs:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.hashes = {}

    def read(self, name):
        p = self.root / name
        raw = p.read_bytes()
        self.hashes[str(p.relative_to(self.root))] = sha(raw)
        return json.loads(raw)

    def lines(self, name):
        p = self.root / name
        raw = p.read_bytes()
        self.hashes[str(p.relative_to(self.root))] = sha(raw)
        return [json.loads(line) for line in raw.splitlines()]

    def cas(self, digest):
        name = f"product/objects/sha256/{digest[:2]}/{digest}.json"
        value = self.read(name)
        if self.hashes[name] != digest:
            raise ValueError("CAS digest differs")
        return value

    def response(self, entry):
        if entry["status_code"] != 200 or entry["truncated"]:
            raise ValueError("retained response unavailable/truncated")
        return self.cas(entry["response_object_sha256"])


def classify(labels, spec):
    key = (labels.get("service_name"), labels.get("span_name"), labels.get("span_kind"))
    for rule in spec["rules"]:
        if key == (rule["service"], rule["operation"], rule["span_kind"]):
            return rule["group"]
    return "UNCLASSIFIED"


def require_prior_baseline(built_at, check_start):
    if built_at >= check_start:
        raise ValueError("BASELINE_NOT_FIXED_BEFORE_CHECK")


def original(inputs, number):
    prefix = f"round-{number}"
    saved = inputs.read(f"{prefix}/diagnosis.json")
    memory_saved = inputs.read(f"{prefix}/memory.json")
    baseline = EnvironmentBaselineV1.model_validate_json(
        json.dumps(inputs.read("baseline-frozen.json"))
    )
    # Never instantiate a repository/migration against the retained database.
    db = inputs.root / "product/product.sqlite3"
    inputs.hashes["product/product.sqlite3"] = sha(db.read_bytes())
    with sqlite3.connect(db.as_uri() + "?mode=ro", uri=True) as conn:
        conn.execute("PRAGMA query_only=ON")
        incident = IncidentRecordV1.model_validate_json(
            conn.execute(
                "SELECT payload_json FROM incidents WHERE incident_id=?",
                (saved["incident_id"],),
            ).fetchone()[0]
        )
        services = tuple(
            ServiceIdentityV1.model_validate_json(row[0])
            for row in conn.execute(
                "SELECT payload_json FROM services WHERE environment_id=?",
                (incident.environment_id,),
            )
        )
    require_prior_baseline(
        baseline.built_at.timestamp(), incident.started_at.timestamp()
    )
    identity = ServiceIdentityMapV1.build(
        environment_id=incident.environment_id, services=services
    )
    if (
        identity.identity_sha256 != incident.service_identity_sha256
        or baseline.baseline_sha256 != incident.baseline_sha256
    ):
        raise ValueError("original binding differs")
    unique = {}
    for obj in inputs.read(f"{prefix}/evidence.json")["objects"]:
        payload = obj["payload"]
        if payload.get("memory_outcome"):
            if obj["action_id"] in unique and unique[obj["action_id"]] != payload:
                raise ValueError("conflicting action snapshots")
            if inputs.cas(obj["object_sha256"]) != payload:
                raise ValueError("snapshot object differs")
            unique[obj["action_id"]] = payload
    snapshots = tuple(
        unique[k] for k in sorted(unique, key=lambda k: ("queue-lag" in k, k))
    )
    outcomes = tuple(
        TypeAdapter(MemoryReadOutcomeV22).validate_json(json.dumps(p["memory_outcome"]))
        for p in snapshots
    )
    raw = tuple(
        ReadOutcomeV22.model_validate_json(json.dumps(p["read_outcome"]))
        for p in snapshots
    )
    coverage = {s: set() for s in EvidenceSourceV22}
    for p in snapshots:
        result = p["connector_result"]
        coverage[EvidenceSourceV22(result["source"])].update(result["covered_services"])
    acquisition = ProductReadAcquisitionV1(
        raw_outcomes=raw,
        memory_outcomes=outcomes,
        snapshots=snapshots,
        covered_services_by_source={s: tuple(sorted(v)) for s, v in coverage.items()},
        capability_limitations=tuple(saved["capability_limitations"]),
        capability_observations_v0232=(),
        capability_limitation_candidates_v0232=(),
    )
    memory, _ = build_memory_views_v22(
        outcomes=outcomes,
        baseline=baseline.v22_baseline_profile,
        observed_at=incident.diagnosis_observed_at,
        top_k=64,
    )
    if memory.model_dump(mode="json") != memory_saved:
        raise ValueError("A memory differs")
    health = evaluate_no_incident_v22(
        memory=memory, candidate_services=incident.candidate_logical_services
    ).model_dump(mode="json")
    if health != inputs.read(f"{prefix}/health-predicate.json"):
        raise ValueError("A health predicate differs")
    diagnosis, _, decision = ProductDiagnosisBridgeV1().diagnose(
        incident=incident,
        baseline=baseline,
        identity_map=identity,
        acquisition=acquisition,
        diagnosis_id=saved["diagnosis_id"],
        created_at=datetime.fromisoformat(saved["created_at"]),
    )
    if diagnosis.model_dump(mode="json") != saved:
        raise ValueError("A bridge diagnosis differs")
    if (
        decision.trace_sha256
        != inputs.read(f"{prefix}/evidence-index.json")["decision_trace_sha256"]
    ):
        raise ValueError("A decision trace differs")
    normal = inputs.lines(f"{prefix}/diagnosis-job/raw.jsonl")
    numeric = []
    # Use the actual connector's reduction, including its finite-point policy.
    templates = {
        "ERROR_RATE": "error_rate",
        "LATENCY_P95_MS": "latency",
        "REQUEST_SUPPORT": "request_support",
        "QUEUE_LAG": "queue_lag",
    }
    for p in snapshots:
        result = p["connector_result"]
        if result["source"] != "METRICS":
            continue
        service = result["requested_services"][0]
        entries = [
            e
            for e in normal
            if e["url"].endswith("/query_range")
            and e.get("action_context", {}).get("action_id") == p["action"]["action_id"]
        ]
        if not entries:
            # Capture context stores the action under read_action_id on some versions.
            entries = [
                e
                for e in normal
                if e["url"].endswith("/query_range")
                and f'service_name="{service}"' in e["params"].get("query", "")
            ]
        for record in result["records"]:
            kind = record["metric_kind"]
            if kind not in templates:
                continue
            candidates = [
                e
                for e in entries
                if (
                    (
                        "histogram_quantile" in e["params"]["query"]
                        or "95p_" in e["params"]["query"]
                    )
                    if kind == "LATENCY_P95_MS"
                    else ("STATUS_CODE_ERROR" in e["params"]["query"])
                    if kind == "ERROR_RATE"
                    else ("lag_ratio" in e["params"]["query"])
                    if kind == "QUEUE_LAG"
                    else (
                        "calls_total" in e["params"]["query"]
                        and "STATUS_CODE_ERROR" not in e["params"]["query"]
                        and "histogram_quantile" not in e["params"]["query"]
                    )
                )
            ]
            if not candidates:
                numeric.append(
                    dict(
                        service=service,
                        kind=kind,
                        status="NOT_COMPUTABLE",
                        gap="raw query match unavailable",
                    )
                )
                continue
            e = candidates[0]
            body = inputs.response(e)
            values = [
                float(v) for row in body["data"]["result"] for _, v in row["values"]
            ]
            context = ConnectorQueryContextV1(
                environment_id=incident.environment_id,
                requested_services=(service,),
                window=ConnectorWindowV1.model_validate(result["window"]),
                maximum_records=100,
                requested_source=EvidenceSourceV22.METRICS,
                metric_kinds=(kind,),
            )

            def retained_response(request, body=body, expected=e["params"]):
                if dict(request.url.params) != {k: str(v) for k, v in expected.items()}:
                    raise ValueError("A retained request differs")
                return httpx.Response(200, json=body)

            connector = PrometheusConnectorV1(
                ConnectorConfigV1(
                    name="retained",
                    kind="PROMETHEUS",
                    endpoint="http://retained.invalid",
                    settings={
                        "query_templates": {
                            key: e["params"]["query"].replace(service, "{service}")
                            for key in (
                                "error_rate",
                                "latency",
                                "request_support",
                                "queue_lag",
                                "cpu",
                                "memory",
                            )
                        },
                        "step_seconds": 10,
                    },
                ),
                credential_resolver=CredentialResolverV1(environment={}),
                timeout_seconds=1,
                transport=httpx.MockTransport(retained_response),
            )
            try:
                records = connector.query(context)[0].model_dump(mode="json")["records"]
            finally:
                connector.close()
            if records != [record]:
                raise ValueError(f"A connector reduction differs: {service}/{kind}")
            numeric.append(
                dict(
                    service=service,
                    kind=kind,
                    status="EXACT_REPLAY",
                    record=record,
                    returned_points=len(values),
                    finite_points=sum(math.isfinite(v) for v in values),
                    nonfinite_points=sum(not math.isfinite(v) for v in values),
                )
            )
    return dict(
        level="EXACT_REPLAY",
        diagnosis=diagnosis.model_dump(mode="json"),
        memory_sha256=memory.memory_sha256,
        predicates=memory_saved["predicates"],
        health=health,
        decision=decision.model_dump(mode="json"),
        numeric=numeric,
        baseline_id=baseline.baseline_id,
        window=dict(
            start=incident.started_at.timestamp(), end=incident.ended_at.timestamp()
        ),
        trace_scope="ORIGINAL_BOUNDED_INPUT",
        original_trace_records=sum(
            len(p["memory_outcome"]["records"])
            for p in snapshots
            if p["memory_outcome"]["source"] == "TRACES"
        ),
    )


def raw_population(inputs, entries, service, kind):
    prefix = "traces_span_metrics_" + kind + "{"
    matches = [
        e
        for e in entries
        if e["url"].endswith("/query")
        and e["params"]["query"].startswith(prefix)
        and f'service_name="{service}"' in e["params"]["query"]
        and "STATUS_CODE_ERROR" not in e["params"]["query"]
    ]
    if len(matches) != 1:
        return []
    return inputs.response(matches[0])["data"]["result"]


def grouped_point(rows, buckets, at, spec):
    """No extrapolation: operation/direction span-count proxy, not a request rate."""
    result = []
    populations = defaultdict(list)
    for row in rows:
        labels = row["metric"]
        key = (classify(labels, spec), labels.get("span_name"), labels.get("span_kind"))
        populations[key].append(row)
    for (group, operation, direction), subset in sorted(populations.items()):
        item = dict(
            group=group,
            operation=operation,
            direction=direction,
            at=at,
            level="DIAGNOSTIC_PROXY_ONLY",
            unit="OBSERVED_SPAN_COUNTER_INCREMENT",
            series=len(subset),
            baseline_eligible=False,
            p95_ms=None,
            p95_status="NOT_COMPUTABLE_NO_VALIDATED_BUCKET_RATES",
        )
        try:
            for row in subset:
                timestamps = [float(t) for t, _ in row["values"]]
                if any(not math.isfinite(t) for t in timestamps) or any(
                    y <= x for x, y in zip(timestamps, timestamps[1:])
                ):
                    raise ValueError("invalid counter timestamp grid")
            parts = increments(subset, at)
            if any(p["grid"] != parts[0]["grid"] for p in parts):
                raise ValueError("incomparable status sample grids")
            total = sum(p["increase"] for p in parts)
            errors = sum(
                p["increase"]
                for p in parts
                if p["labels"].get("status_code") == "STATUS_CODE_ERROR"
            )
            known_status = all(
                p["labels"].get("status_code")
                in ("STATUS_CODE_ERROR", "STATUS_CODE_UNSET", "STATUS_CODE_OK")
                for p in parts
            )
            item.update(
                total=total,
                errors=errors if known_status else None,
                error_fraction=errors / total if total and known_status else None,
                status="OBSERVED_RETURNED_SERIES_ONLY"
                if total
                else "ZERO_OBSERVED_TRAFFIC_NOT_HEALTH",
                grid=parts[0]["grid"],
                samples=sum(len(p["grid"]) for p in parts),
                complete_population_proven=False,
            )
            selected = [
                b
                for b in buckets
                if b["metric"].get("span_name") == operation
                and b["metric"].get("span_kind") == direction
            ]
            # Require each calls label population to match both overflow buckets exactly.
            overflow = 0.0
            for row, part in zip(subset, parts):
                identity = {k: v for k, v in row["metric"].items() if k != "__name__"}
                matching = [
                    b
                    for b in selected
                    if {
                        k: v
                        for k, v in b["metric"].items()
                        if k not in ("__name__", "le")
                    }
                    == identity
                ]
                pair = {}
                for b in matching:
                    if b["metric"].get("le") in ("15000", "+Inf"):
                        inc = increments([b], at)[0]
                        if inc["grid"] != part["grid"]:
                            raise ValueError("bucket/calls grid mismatch")
                        pair[b["metric"]["le"]] = inc["increase"]
                if (
                    set(pair) != {"15000", "+Inf"}
                    or pair["+Inf"] != part["increase"]
                    or pair["15000"] > pair["+Inf"]
                ):
                    raise ValueError("missing/inconsistent overflow buckets")
                overflow += pair["+Inf"] - pair["15000"]
            item["above_15000ms"] = overflow
        except ValueError as error:
            item.update(status="NOT_COMPUTABLE", gap=str(error))
        result.append(item)
    return result


def contribution(populations):
    """Fractions of observed spans, never deduplicated business requests."""
    if not populations or any(
        "grid" not in p or p["status"] == "NOT_COMPUTABLE" for p in populations
    ):
        return dict(status="NOT_COMPUTABLE", fraction=None)
    if any(p["grid"] != populations[0]["grid"] for p in populations):
        return dict(status="INCOMPARABLE_GRIDS", fraction=None)
    total = sum(p["total"] for p in populations)
    errors = (
        None
        if any(p["errors"] is None for p in populations)
        else sum(p["errors"] for p in populations)
    )
    overflow = sum(p["above_15000ms"] for p in populations)
    control = [p for p in populations if p["group"] == "CONTROL_STREAM"]
    control_errors = (
        None
        if any(p["errors"] is None for p in control)
        else sum(p["errors"] for p in control)
    )
    return dict(
        status="OBSERVED_SPAN_POPULATION_NOT_ORDERS",
        total=total,
        errors=errors,
        control_total=sum(p["total"] for p in control),
        control_errors=control_errors,
        control_error_fraction=control_errors / errors
        if errors and control_errors is not None
        else None,
        overflow=overflow,
        control_overflow=sum(p["above_15000ms"] for p in control),
        control_overflow_fraction=sum(p["above_15000ms"] for p in control) / overflow
        if overflow
        else None,
        unclassified_fraction=sum(
            p["total"] for p in populations if p["group"] == "UNCLASSIFIED"
        )
        / total
        if total
        else None,
    )


def stratify(inputs, number, spec, a):
    entries = inputs.lines(f"round-{number}/samples/raw.jsonl")
    baseline_entries = inputs.lines("baseline-support/raw.jsonl")
    out = {}
    for service in ("checkout", "fraud-detection", "payment"):
        rows = raw_population(inputs, entries, service, "calls_total")
        buckets = raw_population(
            inputs, entries, service, "duration_milliseconds_bucket"
        )
        normal = inputs.lines(f"round-{number}/diagnosis-job/raw.jsonl")
        times = sorted(
            {
                float(t)
                for e in normal
                if e["url"].endswith("/query_range")
                and f'service_name="{service}"' in e["params"]["query"]
                for row in inputs.response(e)["data"]["result"]
                for t, _ in row["values"]
            }
        )
        base_rows = raw_population(inputs, baseline_entries, service, "calls_total")
        base_buckets = raw_population(
            inputs, baseline_entries, service, "duration_milliseconds_bucket"
        )
        base_queries = inputs.lines("baseline/raw.jsonl")
        base_times = sorted(
            {
                float(t)
                for e in base_queries
                if e["url"].endswith("/query_range")
                and f'service_name="{service}"' in e["params"]["query"]
                for row in inputs.response(e)["data"]["result"]
                for t, _ in row["values"]
            }
        )
        points = [
            dict(at=t, populations=grouped_point(rows, buckets, t, spec)) for t in times
        ]
        for point in points:
            point["contribution"] = contribution(point["populations"])
        out[service] = dict(
            points=points,
            baseline_points=[
                dict(at=t, populations=grouped_point(base_rows, base_buckets, t, spec))
                for t in base_times
            ],
            baseline_status="BASELINE_SCOPE_UNAVAILABLE_FOR_EXACT_RATE_PROFILE",
            baseline_raw_series=len(base_rows),
            check_raw_series=len(rows),
            baseline_limitation="Raw subsets retained but bounded sample range does not supply complete 5m supports for all baseline points; no validated rate reconstruction or representative long-RPC cycle.",
            operations=[
                dict(operation=o, direction=k, group=g)
                for g, o, k in sorted(
                    {
                        (
                            classify(r["metric"], spec),
                            r["metric"].get("span_name"),
                            r["metric"].get("span_kind"),
                        )
                        for r in rows
                    }
                )
            ],
        )
    return dict(
        level="DIAGNOSTIC_PROXY_ONLY",
        metrics=out,
        decision="NOT_COMPUTABLE_SEMANTIC_BASELINE_AND_RATE_UNAVAILABLE",
        historical_decision=a["diagnosis"]["core_or_extension_or_open_world"],
        predicate_changes=None,
        unchanged_non_rpc_predicates=[
            p for p in a["predicates"] if not p["predicate_kind"].startswith("METRIC_")
        ],
        kafka="Original non-span Kafka metrics retained; not reclassified as zero business traffic",
        qualification="NOT_ISSUED",
        trace_scope="ORIGINAL_BOUNDED_INPUT",
    )


def tags(values):
    return {v["key"]: v["value"] for v in values}


def spans_from(payload, capture):
    out = []
    for trace in payload.get("data", []):
        for span in trace["spans"]:
            process = trace["processes"][span["processID"]]
            resource = tags(process.get("tags", []))
            attributes = tags(span.get("tags", []))
            start = span.get("startTime")
            duration = span.get("duration")
            out.append(
                dict(
                    trace_id=span["traceID"],
                    span_id=span["spanID"],
                    service=process["serviceName"],
                    instance=resource.get("service.instance.id"),
                    operation=span["operationName"],
                    direction=attributes.get("span.kind"),
                    start=None if start is None else start / 1e6,
                    end=None
                    if start is None or duration is None
                    else (start + duration) / 1e6,
                    duration_us=duration,
                    references=span.get("references", []),
                    rpc_system=attributes.get("rpc.system"),
                    rpc_service=attributes.get("rpc.service"),
                    rpc_method=attributes.get("rpc.method"),
                    status=attributes.get("otel.status_code"),
                    grpc_status=attributes.get("rpc.grpc.status_code"),
                    captures=[capture],
                )
            )
    return out


def deduplicate(spans):
    unique = {}
    for span in spans:
        key = (span["trace_id"], span["span_id"])
        if key in unique:
            if {k: v for k, v in span.items() if k != "captures"} != {
                k: v for k, v in unique[key].items() if k != "captures"
            }:
                raise ValueError("conflicting duplicate span")
            unique[key]["captures"].extend(span["captures"])
        else:
            unique[key] = dict(span, captures=list(span["captures"]))
    return list(unique.values())


def associate(span, rows, window, times, cutoff, original_ids):
    start, end = span["start"], span["end"]
    types = []
    if start is not None and window["start"] <= start <= window["end"]:
        types.append("START_IN_WINDOW")
    supports = [[t - 300, t] for t in times]
    if end is not None and any(lo < end <= hi for lo, hi in supports):
        types.append("END_IN_SUPPORT_WINDOW")
    if (
        start is not None
        and end is not None
        and start <= window["end"]
        and end >= window["start"]
    ):
        types.append("OVERLAPS_WINDOW")
    matches = []
    support_matches = []
    instance_verified = False
    for row in rows:
        labels = row["metric"]
        if not (
            span["instance"]
            and labels.get("service_instance_id") == span["instance"]
            and labels.get("service_name") == span["service"]
            and labels.get("span_name") == span["operation"]
            and labels.get("span_kind") == "SPAN_KIND_" + str(span["direction"]).upper()
        ):
            continue
        if span.get("rpc_method") == "EventStream" and not (
            span.get("rpc_system") == "grpc"
            and span.get("rpc_service") == "flagd.evaluation.v2.Service"
        ):
            continue
        instance_verified = True
        if (
            span["status"] not in ("ERROR", "OK", "UNSET")
            or labels.get("status_code") != "STATUS_CODE_" + span["status"]
        ):
            continue
        points = [(float(t), float(v)) for t, v in row["values"]]
        for (t0, v0), (t1, v1) in zip(points, points[1:]):
            if (
                end is not None
                and t0 < end <= t1
                and math.isfinite(v0)
                and math.isfinite(v1)
                and v1 > v0
                and any(lo < t0 and t1 <= hi for lo, hi in supports)
            ):
                matches.append(dict(start=t0, end=t1, increment=v1 - v0))
            if (
                end is not None
                and math.isfinite(v0)
                and math.isfinite(v1)
                and v1 > v0
                and any(lo < end <= t1 <= hi and lo < t0 for lo, hi in supports)
            ):
                support_matches.append(
                    dict(
                        start=t0,
                        end=t1,
                        increment=v1 - v0,
                        completion_to_first_changed_sample_seconds=t1 - end,
                    )
                )
    original = (span["trace_id"], span["span_id"]) in original_ids
    captured = [
        datetime.fromisoformat(c["received_at"]).timestamp() for c in span["captures"]
    ]
    availability = (
        "AVAILABLE_BY_CAPTURE_CUTOFF"
        if instance_verified
        and end is not None
        and end <= cutoff
        and min(captured) <= cutoff
        else "ONLINE_AVAILABILITY_UNPROVEN"
    )
    return dict(
        span,
        association_types=types,
        counter_intervals=matches,
        shared_support_candidates=support_matches,
        match_strength="INSTANCE_OPERATION_COMPLETION_INTERVAL"
        if matches
        else "INSTANCE_OPERATION_SHARED_SUPPORT_NOT_CAUSAL"
        if support_matches
        else "TEMPORAL_ONLY_OR_NO_MATCH",
        original_input_present=original,
        original_start_query_excluded=start is not None and start < window["start"],
        capture_after_cutoff=min(captured) > cutoff,
        online_availability=availability,
        instance_verified=instance_verified,
        truncation_present=any(
            c["truncated"] or c["limit_reached"] for c in span["captures"]
        ),
        causal_root="UNPROVEN",
    )


def correlate(inputs, number, spec, a, b):
    entries = inputs.lines(f"round-{number}/diagnosis-job/raw.jsonl")
    normal_spans = []
    for e in entries:
        if e["url"].endswith("/api/traces"):
            payload = inputs.response(e)
            normal_spans.extend(
                spans_from(
                    payload,
                    dict(
                        received_at=e["received_at"],
                        params=e["params"],
                        truncated=e["truncated"],
                        limit_reached=len(payload.get("data", []))
                        >= int(e["params"]["limit"]),
                    ),
                )
            )
    normal_spans = deduplicate(normal_spans)
    extra = []
    capture_inventory = []
    for path in sorted((inputs.root / f"round-{number}/context").glob("traces-*.json")):
        name = str(path.relative_to(inputs.root))
        meta = inputs.read(name)
        body_name = str(path.with_suffix(".body").relative_to(inputs.root))
        payload = inputs.read(body_name)
        if (
            inputs.hashes[body_name] != meta["body_sha256"]
            or meta["status"] != 200
            or meta["error"]
        ):
            raise ValueError("context response binding differs")
        capture = dict(
            file=name,
            received_at=meta["at"],
            params=meta["params"],
            truncated=meta["truncated"],
            limit_reached=len(payload.get("data", [])) >= int(meta["params"]["limit"]),
        )
        capture_inventory.append(capture)
        extra.extend(spans_from(payload, capture))
    unique = deduplicate(extra)
    combined = {
        (s["trace_id"], s["span_id"]): s for s in deduplicate(normal_spans + unique)
    }
    unique = [combined[(s["trace_id"], s["span_id"])] for s in unique]
    ids = {(s["trace_id"], s["span_id"]) for s in normal_spans}
    raw = inputs.lines(f"round-{number}/samples/raw.jsonl")
    rows = [
        row
        for service in b["metrics"]
        for row in raw_population(inputs, raw, service, "calls_total")
    ]
    times = sorted({p["at"] for v in b["metrics"].values() for p in v["points"]})
    cutoff = datetime.fromisoformat(a["diagnosis"]["created_at"]).timestamp()
    matched = []
    for span in unique:
        labels = dict(
            service_name=span["service"],
            span_name=span["operation"],
            span_kind="SPAN_KIND_" + str(span["direction"]).upper(),
        )
        # Preserve all relevant spans, with exact purpose mapping. Flagd server companions
        # remain visible without pretending they are additional business failures.
        item = associate(span, rows, a["window"], times, cutoff, ids)
        item["group"] = classify(labels, spec)
        item["rpc_semantics_verified"] = (
            span["rpc_system"] == "grpc"
            and span["rpc_service"] == "flagd.evaluation.v2.Service"
            and span["rpc_method"] == "EventStream"
        )
        if item["association_types"]:
            matched.append(item)
    return dict(
        level="DIAGNOSTIC_PROXY_ONLY",
        augmentation="POST_HOC_AUGMENTED",
        metric_view_sha256=sha(json.dumps(b["metrics"], sort_keys=True).encode()),
        decision=b["decision"],
        historical_decision=a["diagnosis"]["core_or_extension_or_open_world"],
        predicate_changes=None,
        adapter="SIDECAR_ONLY_NO_SYNTHETIC_OBSERVATION_REFS",
        adapter_reason="Purpose-group counters are proxies, not accepted MetricFacts; cross-window context retained with true timestamps and IDs, not injected into formal observations.",
        original_unique_spans=len(normal_spans),
        extra_unique_spans=len(unique),
        new_unique_spans=sum((s["trace_id"], s["span_id"]) not in ids for s in unique),
        relevant_spans=matched,
        captures=capture_inventory,
        rate_support_intervals=[[t - 300, t] for t in times],
        new_supported_associations=sum(
            not s["original_input_present"]
            and bool(s["counter_intervals"] or s["shared_support_candidates"])
            for s in matched
        ),
        new_exact_interval_associations=sum(
            not s["original_input_present"] and bool(s["counter_intervals"])
            for s in matched
        ),
        new_control_support_associations=sum(
            not s["original_input_present"]
            and s["group"] == "CONTROL_STREAM"
            and bool(s["shared_support_candidates"])
            for s in matched
        ),
        original_span_count_scope="Raw response unique IDs; bounded normalized memory record count is separately reported in A, which has no preserved span IDs.",
        causal_root="UNPROVEN",
        reconnect_count="UNKNOWN_NO_CONNECTION_IDENTITY",
        online_efficiency="NOT_EVALUATED",
        qualification="NOT_ISSUED",
    )


def public_view(data):
    """Small publishable projection; full per-point evidence stays in private output."""
    windows = []
    for w in data["windows"]:
        operations = []
        for service, view in w["B"]["metrics"].items():
            for op in view["operations"]:
                points = [
                    p
                    for point in view["points"]
                    for p in point["populations"]
                    if p["operation"] == op["operation"]
                    and p["direction"] == op["direction"]
                ]
                ranges = {}
                for key in ("total", "errors", "above_15000ms", "error_fraction"):
                    values = [p[key] for p in points if p.get(key) is not None]
                    ranges[key] = [min(values), max(values)] if values else None
                operations.append(
                    dict(
                        service=service,
                        **op,
                        ranges=ranges,
                        points=len(points),
                        uncomputable_points=sum(
                            p["status"] == "NOT_COMPUTABLE" for p in points
                        ),
                        baseline_status=view["baseline_status"],
                    )
                )
        controls = [
            {
                k: s[k]
                for k in (
                    "service",
                    "operation",
                    "direction",
                    "start",
                    "end",
                    "duration_us",
                    "association_types",
                    "counter_intervals",
                    "shared_support_candidates",
                    "online_availability",
                    "original_input_present",
                    "capture_after_cutoff",
                    "original_start_query_excluded",
                    "causal_root",
                )
            }
            for s in w["C"]["relevant_spans"]
            if s["group"] == "CONTROL_STREAM"
        ]
        windows.append(
            dict(
                round=w["round"],
                window=w["A"]["window"],
                A=dict(
                    level=w["A"]["level"],
                    terminal=w["A"]["diagnosis"]["terminal"],
                    lane=w["A"]["diagnosis"]["core_or_extension_or_open_world"],
                    memory_sha256=w["A"]["memory_sha256"],
                    decision_trace_sha256=w["A"]["decision"]["trace_sha256"],
                    numeric=w["A"]["numeric"],
                    health_accepted=w["A"]["health"]["accepted"],
                    predicates=[
                        dict(service=p["service"], kind=p["predicate_kind"])
                        for p in w["A"]["predicates"]
                    ],
                ),
                B=dict(
                    level=w["B"]["level"],
                    decision=w["B"]["decision"],
                    operations=operations,
                    predicate_changes=None,
                    qualification=w["B"]["qualification"],
                ),
                C=dict(
                    decision=w["C"]["decision"],
                    augmentation=w["C"]["augmentation"],
                    controls=controls,
                    counts={
                        k: w["C"][k]
                        for k in (
                            "original_unique_spans",
                            "extra_unique_spans",
                            "new_unique_spans",
                            "new_supported_associations",
                            "new_exact_interval_associations",
                            "new_control_support_associations",
                        )
                    },
                    metric_view_sha256=w["C"]["metric_view_sha256"],
                ),
            )
        )
    return dict(
        mode=data["mode"],
        projection="PER_WINDOW_SUMMARY_FULL_POINTS_IN_PRIVATE_COMPARISON",
        windows=windows,
        spec_sha256=data["spec_sha256"],
        source_sha256=data["source_sha256"],
        input_file_count=len(data["input_sha256"]),
        formal_acceptance=False,
        limits=data["limits"],
    )


def replay(root, rounds, spec_path, output):
    inputs = Inputs(root)
    output = Path(output).resolve()
    if output.exists() or output == inputs.root or inputs.root in output.parents:
        raise ValueError("fresh separate output required")
    spec_raw = Path(spec_path).read_bytes()
    spec = json.loads(spec_raw)
    if spec["version"] != "diagnostic-semantics-experiment-v1":
        raise ValueError("unsupported experiment")
    # Finish A for both windows before comparing any B/C output.
    originals = {n: original(inputs, n) for n in rounds}
    windows = []
    for n, a in originals.items():
        b = stratify(inputs, n, spec, a)
        c = correlate(inputs, n, spec, a, b)
        windows.append(dict(round=n, A=a, B=b, C=c))
    for name, digest in inputs.hashes.items():
        if sha((inputs.root / name).read_bytes()) != digest:
            raise ValueError("retained input changed")
    data = dict(
        mode=spec["mode"],
        formal_acceptance=False,
        windows=windows,
        project_provider_calls=0,
        docker_operations=0,
        new_telemetry_requests=0,
        spec_sha256=sha(spec_raw),
        source_sha256=sha(Path(__file__).read_bytes()),
        input_sha256=inputs.hashes,
        limits=[
            "Two seen, adjacent windows of one deployment, not independent fault families.",
            "No independent root/health labels; no accuracy or false-positive claims.",
            "Counter proxies do not establish a new diagnostic baseline or online availability.",
        ],
    )
    output.mkdir(parents=True, mode=0o700)
    with (output / "comparison.json").open("x") as f:
        json.dump(data, f, indent=2, allow_nan=False)
        f.write("\n")
    (output / "comparison.json").chmod(0o600)
    with (output / "public-comparison.json").open("x") as f:
        json.dump(public_view(data), f, indent=2, allow_nan=False)
        f.write("\n")
    return data


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True)
    parser.add_argument("--rounds", nargs="+", type=int, choices=(2, 3), default=[2, 3])
    parser.add_argument("--spec", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    data = replay(args.root, args.rounds, args.spec, args.output)
    print(
        json.dumps(
            [
                dict(
                    round=w["round"],
                    A=w["A"]["diagnosis"]["terminal"],
                    B=w["B"]["decision"],
                    C_new_associations=w["C"]["new_supported_associations"],
                )
                for w in data["windows"]
            ]
        )
    )


if __name__ == "__main__":
    main()
