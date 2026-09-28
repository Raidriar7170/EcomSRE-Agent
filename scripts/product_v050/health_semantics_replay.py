"""Offline retained-metric contribution audit; no diagnosis or qualification minted."""

from datetime import UTC, datetime
from pathlib import Path
import argparse
import hashlib
import json
import math

import httpx
from scripts.product_v050 import default_credentials as dc, ingestion_evidence as ie
from ecomsre.product.connectors.prometheus import PrometheusConnectorV1
from ecomsre.product.connectors.base import ConnectorQueryContextV1, ConnectorWindowV1
from ecomsre.product.connectors.credentials import CredentialResolverV1
from ecomsre.product.contracts import ConnectorConfigV1
from ecomsre.dta_v2.v22.read_contracts import EvidenceSourceV22, MetricKindV22
from ecomsre.dta_v2.v22.replay import ReadOutcomeV22
from ecomsre.dta_v2.v22.memory import BaselineProfileV22, build_memory_views_v22
from ecomsre.dta_v2.v22.predicates import evaluate_no_incident_v22


def increments(rows, at, width=300):
    """Observed counter increments only; never synthesize a pre-birth zero."""
    result = []
    for row in rows:
        pairs = [
            (float(t), float(v))
            for t, v in row["values"]
            if at - width < float(t) <= at
        ]
        if (
            len(pairs) < 2
            or any(not math.isfinite(v) for _, v in pairs)
            or any(b[1] < a[1] for a, b in zip(pairs, pairs[1:]))
        ):
            raise ValueError("counter window insufficient or reset")
        result.append(
            dict(
                labels=row["metric"],
                increase=pairs[-1][1] - pairs[0][1],
                grid=[t for t, _ in pairs],
            )
        )
    return result


def call_contributions(rows, times):
    out = []
    for t in times:
        parts = increments(rows, t)
        if not parts or any(p["grid"] != parts[0]["grid"] for p in parts):
            out.append(dict(at=t, status="UNKNOWN_INCOMPARABLE_SAMPLE_GRIDS"))
            continue
        total = sum(p["increase"] for p in parts)
        errors = sum(
            p["increase"]
            for p in parts
            if p["labels"].get("status_code") == "STATUS_CODE_ERROR"
        )
        event = sum(
            p["increase"]
            for p in parts
            if "EventStream" in p["labels"].get("span_name", "")
        )
        event_errors = sum(
            p["increase"]
            for p in parts
            if "EventStream" in p["labels"].get("span_name", "")
            and p["labels"].get("status_code") == "STATUS_CODE_ERROR"
        )
        out.append(
            dict(
                at=t,
                status="OBSERVED_COMMON_GRID_INCREMENTS",
                total=total,
                errors=errors,
                eventstream=event,
                eventstream_errors=event_errors,
                error_fraction=None if total == 0 else errors / total,
                eventstream_fraction=None if total == 0 else event / total,
            )
        )
    return out


def bucket_contributions(rows, times):
    out = []
    for t in times:
        parts = increments(rows, t)
        grouped = {}
        for p in parts:
            key = tuple(sorted((k, v) for k, v in p["labels"].items() if k != "le"))
            grouped.setdefault(key, {})[p["labels"]["le"]] = p
        total = event = 0.0
        for key, group in grouped.items():
            if (
                "+Inf" not in group
                or "15000" not in group
                or group["+Inf"]["grid"] != group["15000"]["grid"]
            ):
                raise ValueError("overflow bucket correspondence missing")
            delta = group["+Inf"]["increase"] - group["15000"]["increase"]
            if delta < 0:
                raise ValueError("negative overflow increment")
            total += delta
            if "EventStream" in dict(key).get("span_name", ""):
                event += delta
        out.append(dict(at=t, above_15000ms=total, eventstream_above_15000ms=event))
    return out


def replay(root, number, output):
    root, output = Path(root).resolve(), Path(output).resolve()
    if output.exists() or output == root or root in output.parents:
        raise ValueError("fresh separate output required")
    hashes = {}

    def read(p):
        raw = p.read_bytes()
        hashes[str(p)] = hashlib.sha256(raw).hexdigest()
        return json.loads(raw)

    base = root / f"round-{number}"
    original = read(base / "result.json")
    responses = {}
    raw_selectors = {}
    points = {}
    for i, q in enumerate(original["queries"]):
        meta = read(base / f"{i:02}-query.json")
        body = read(base / f"{i:02}-query.body")
        if hashes[str(base / f"{i:02}-query.body")] != meta["body_sha256"] or meta[
            "params"
        ] != dict(
            query=q["query"], start=original["start"], end=original["end"], step=10
        ):
            raise ValueError("query binding differs")
        if meta["status"] != 200 or meta["truncated"] or meta["error"]:
            raise ValueError("query unavailable")
        responses[q["query"]] = body
        values = [
            (float(t), float(v))
            for r in body["data"]["result"]
            for t, v in r.get("values", [])
        ]
        finite = [v for _, v in values if math.isfinite(v)]
        points[q["key"]] = dict(
            returned_points=len(values),
            finite_points=len(finite),
            nonfinite_points=len(values) - len(finite),
            connector_mean=sum(finite) / len(finite) if finite else None,
            at_15000ms=sum(v == 15000 for v in finite),
        )
        for j, (sel, _, params) in enumerate(
            ie.expected_reads(
                q["query"],
                original["start"],
                original["end"],
                version="ingestion-sample-evidence-v3",
            )
        ):
            m = read(base / f"{i:02}-raw-{j}.json")
            b = read(base / f"{i:02}-raw-{j}.body")
            if (
                m["params"] != params
                or hashes[str(base / f"{i:02}-raw-{j}.body")] != m["body_sha256"]
                or m["status"] != 200
                or m["truncated"]
                or m["error"]
            ):
                raise ValueError("raw binding differs")
            raw_selectors[sel] = b["data"]["result"]
    templates = {
        q["key"].split(":")[1]: q["query"].replace("checkout", "{service}")
        for q in original["queries"]
        if q["key"].endswith(":checkout")
    }

    def handler(request):
        params = request.url.params
        if (
            request.url.path != "/api/v1/query_range"
            or float(params["start"]) != original["start"]
            or float(params["end"]) != original["end"]
            or float(params["step"]) != 10
        ):
            raise ValueError("offline request differs")
        return httpx.Response(200, json=responses[params["query"]])

    connector = PrometheusConnectorV1(
        ConnectorConfigV1(
            name="retained",
            kind="PROMETHEUS",
            endpoint="http://retained.invalid",
            settings={"query_templates": templates, "step_seconds": 10},
        ),
        credential_resolver=CredentialResolverV1(environment={}),
        timeout_seconds=1,
        transport=httpx.MockTransport(handler),
    )
    candidates = tuple(
        sorted({q["key"].rsplit(":", 1)[-1] for q in original["queries"]})
    )
    end = datetime.fromtimestamp(original["end"], UTC)
    context = ConnectorQueryContextV1(
        environment_id="env-" + "0" * 24,
        requested_services=candidates,
        window=ConnectorWindowV1(
            started_at=datetime.fromtimestamp(original["start"], UTC), ended_at=end
        ),
        maximum_records=100,
        requested_source=EvidenceSourceV22.METRICS,
        metric_kinds=tuple(
            sorted(
                [
                    MetricKindV22.ERROR_RATE,
                    MetricKindV22.LATENCY_P95_MS,
                    MetricKindV22.REQUEST_SUPPORT,
                ],
                key=lambda x: x.value,
            )
        ),
    )
    try:
        result = connector.query(context)[0]
    finally:
        connector.close()
    payload = dict(
        schema_version="dta-v22.read-outcome.v1",
        action_id="a:metrics:retained:partial",
        source=EvidenceSourceV22.METRICS,
        request_sha256=ie.sha(context.model_dump(mode="json")),
        status=result.status,
        records=result.records,
        truncated=result.truncated,
    )
    draft = ReadOutcomeV22.model_construct(**payload, outcome_sha256="0" * 64)
    outcome = ReadOutcomeV22.model_validate(
        payload
        | {
            "outcome_sha256": ie.sha(
                draft.model_dump(mode="json", exclude={"outcome_sha256"})
            )
        }
    )
    # Empty baseline explicitly represents missing data, never a zero healthy baseline.
    memory, _ = build_memory_views_v22(
        outcomes=(outcome,),
        baseline=BaselineProfileV22.build(
            metric_stats=(), trace_stats=(), resource_stats=()
        ),
        observed_at=end,
        top_k=64,
    )
    coverage = evaluate_no_incident_v22(
        memory=memory, candidate_services=candidates
    ).model_dump(mode="json")
    contributions = {}
    times = [original["start"] + 10 * n for n in range(31)]
    for service in ["fraud-detection", "payment"]:
        calls = raw_selectors[
            f'traces_span_metrics_calls_total{{service_name="{service}",service_name!="kafka"}}'
        ]
        buckets = raw_selectors[
            f'traces_span_metrics_duration_milliseconds_bucket{{service_name="{service}",service_name!="kafka"}}'
        ]
        error_query = next(
            q["query"]
            for q in original["queries"]
            if q["key"] == "prometheus:error_rate:" + service
        )
        actual = [
            (float(t), float(v))
            for r in responses[error_query]["data"]["result"]
            for t, v in r["values"]
        ]
        attributed = call_contributions(calls, times)
        differences = [
            abs(p["error_fraction"] - v)
            for p in attributed
            if p.get("error_fraction") is not None
            for t, v in actual
            if abs(t - p["at"]) < 0.001 and math.isfinite(v)
        ]
        contributions[service] = dict(
            actual_error_fraction_compared_points=len(differences),
            actual_error_fraction_max_absolute_difference=max(differences)
            if differences
            else None,
            calls=attributed,
            overflow=bucket_contributions(buckets, times),
        )
    for name in [
        "kafka-javaagent-version.json",
        "kafka-process-options-verified.json",
        "started.json",
    ] + [f"default-source-{i}.json" for i in range(8)]:
        read(root / name)
    audit = dc.audit_retained(root)
    if any(
        hashlib.sha256(Path(p).read_bytes()).hexdigest() != h for p, h in hashes.items()
    ):
        raise ValueError("input changed")
    data = dict(
        mode="SEEN_DEVELOPMENT_SEMANTICS_REPLAY",
        round=number,
        query_summaries=points,
        connector_metrics=result.model_dump(mode="json"),
        contributions=contributions,
        partial_memory_coverage=coverage,
        baseline_status="MISSING_NOT_ZERO",
        normal_diagnosis="NOT_REPLAYED_MISSING_BOUND_BASELINE_RUNTIME_AND_COMPLETE_ACTION_INPUTS",
        healthy_qualification="NOT_ISSUED",
        formal_pass=False,
        default_material_audit=audit,
        input_sha256=hashes,
        source_sha256={
            str(path): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in [
                Path(__file__).resolve(),
                dc.MAPPING_PATH,
                Path(dc.__file__).resolve(),
                Path(ie.__file__).resolve(),
                *[
                    Path(__file__).resolve().parents[2] / name
                    for name in (
                        "src/ecomsre/product/connectors/prometheus.py",
                        "src/ecomsre/dta_v2/v22/memory.py",
                        "src/ecomsre/dta_v2/v22/predicates.py",
                    )
                ],
            ]
        },
        replayed_at=datetime.now(UTC).isoformat(),
    )
    output.mkdir(parents=True, mode=0o700)
    p = output / "replay.json"
    p.write_text(json.dumps(data, indent=2, allow_nan=False) + "\n")
    p.chmod(0o600)
    return data


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--root", required=True)
    p.add_argument("--round", type=int, choices=[3], default=3)
    p.add_argument("--output", required=True)
    a = p.parse_args()
    d = replay(a.root, a.round, a.output)
    print(
        json.dumps(
            {
                "coverage": d["partial_memory_coverage"]["denial_reasons"],
                "default_material_audit": d["default_material_audit"],
                "healthy_qualification": d["healthy_qualification"],
            }
        )
    )
