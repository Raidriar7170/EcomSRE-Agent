"""Prepare a bounded, label-separated subset of already retained RCA-100.

Read-only source. Derived snapshots remain private. No download or Provider calls.
This is a trace-only service-localization slice, not the RCA-100 official score.
"""

import argparse
from collections import defaultdict
from datetime import datetime
import hashlib
import json
from pathlib import Path
from statistics import median

import pyarrow.parquet as pq


def sha(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for b in iter(lambda: f.read(1024 * 1024), b""):
            h.update(b)
    return h.hexdigest()


def opaque(value):
    return hashlib.sha256(("semantic-rca100-v1:" + str(value)).encode()).hexdigest()[
        :20
    ]


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x") as f:
        json.dump(value, f, indent=2, allow_nan=False)


def observed_topology(records):
    by_id = {(r["trace_id"], r["span_id"]): r for r in records}
    edges = set()
    for r in records:
        parent = by_id.get((r["trace_id"], r["parent_span_id"]))
        if parent and parent["service"] != r["service"]:
            edges.add((parent["service"], r["service"]))
    return sorted(edges)


def prepare(source, answers, output, base_config):
    config = json.loads(base_config.read_text())
    items = []
    for directory in sorted((source / "cases").iterdir()):
        task = json.loads((directory / "task.json").read_text())
        lo, hi = [
            datetime.fromisoformat(task["alert_window"][k]).timestamp()
            for k in ("start", "end")
        ]
        gt = json.loads((answers / (directory.name + ".gt.json")).read_text())
        entities = json.loads(gt["raw_ground_truth"])["outcome"].get(
            "target_entities", []
        )
        eligible = (
            len(entities) == 1 and entities[0].get("entity_type") == "apm.service"
        )
        items.append(
            dict(
                name=directory.name,
                lo=lo,
                hi=hi,
                task=task,
                root=entities[0]["entity_name"] if eligible else None,
                mechanisms=gt.get("root_cause_types", []),
            )
        )
    groups = []
    for item in sorted(items, key=lambda x: (x["lo"], x["name"])):
        if not groups or item["hi"] - 1500 >= groups[-1]["end"]:
            groups.append(dict(start=item["hi"] - 1500, end=item["hi"], items=[item]))
        else:
            groups[-1]["end"] = max(groups[-1]["end"], item["hi"])
            groups[-1]["items"].append(item)
    candidates = []
    for group in groups:
        eligible = [x for x in group["items"] if x["root"] is not None]
        if eligible:
            # t001 was inspected during schema discovery: its entire group is development.
            chosen = min(
                eligible, key=lambda x: (x["name"] != "t001", opaque(x["name"]))
            )
            candidates.append((group, chosen))
    candidates.sort(
        key=lambda x: (
            not any(y["name"] == "t001" for y in x[0]["items"]),
            opaque(",".join(y["name"] for y in x[0]["items"])),
        )
    )
    selected = candidates[:36]
    config["dataset"] = "retained-rca100-trace-subset-v1"
    config["cases"] = []
    config["subset_protocol"] = dict(
        development_events=12,
        test_events=24,
        scoring="exact_service_Acc1_and_MRR",
        sample="service-stratified lowest SHA256 whole traces, then hash fill; <=450 spans per window",
        windows="last 300s ending at alert end; three prior 300s windows with a 300s gap",
        group="transitive overlapping full current+reference support across all 103 tasks",
        mapping="existing frozen purpose mapping; unmatched operations remain UNCLASSIFIED",
        missing="no counters/logs/metrics; no online timing/healthy baseline/causal proof",
        semantic_revisions=2,
        prompt_policy="unchanged; no additional semantic revision",
    )
    manifests = []
    fields = [
        "traceId",
        "spanId",
        "parentSpanId",
        "kind",
        "spanName",
        "startTime",
        "endTime",
        "duration",
        "serviceName",
        "statusCode",
    ]
    for index, (group, item) in enumerate(selected):
        directory = source / "cases" / item["name"]
        case_id = f"rcase-{index + 1:03d}"
        lo, hi = item["hi"] - 600, item["hi"]
        windows = dict(current=[hi - 300, hi])
        windows.update(
            {
                f"reference-{i}": [lo - 900 + i * 300, lo - 600 + i * 300]
                for i in range(3)
            }
        )
        pools = {w: defaultdict(list) for w in windows}
        services = set()
        totals = defaultdict(int)
        kinds = {
            "1": "internal",
            "2": "server",
            "3": "client",
            "4": "producer",
            "5": "consumer",
        }
        q = pq.ParquetFile(directory / "traces.parquet")
        for batch in q.iter_batches(batch_size=16384, columns=fields):
            for r in batch.to_pylist():
                services.add(r["serviceName"])
                try:
                    end = int(r["endTime"]) / 1e9
                    start = int(r["startTime"]) / 1e9
                    duration = int(r["duration"]) / 1e6
                except (ValueError, TypeError):
                    continue
                w = next((w for w, (a, b) in windows.items() if a <= end < b), None)
                if w is None:
                    continue
                totals[w] += 1
                row = dict(
                    service=r["serviceName"],
                    operation=r["spanName"][:180],
                    direction=kinds.get(str(r["kind"])),
                    error={"1": False, "2": True}.get(str(r["statusCode"])),
                    duration_ms=duration,
                    start=start,
                    end=end,
                    trace_id=opaque(r["traceId"]),
                    span_id=opaque(r["spanId"]),
                    parent_span_id=opaque(r["parentSpanId"])
                    if r["parentSpanId"]
                    else None,
                    record_ref="rec-"
                    + opaque(str(r["traceId"]) + ":" + str(r["spanId"])),
                )
                pools[w][row["trace_id"]].append(row)
        records = []
        sampled = {}
        for w, traces in pools.items():
            sample = []
            chosen = set()
            # Cover observed services before hash filling; no label/status selection.
            service_traces = defaultdict(list)
            for trace_id in sorted(traces):
                for service in {r["service"] for r in traces[trace_id]}:
                    service_traces[service].append(trace_id)
            for service in sorted(service_traces):
                if any(r["service"] == service for r in sample):
                    continue
                for trace_id in service_traces[service]:
                    if (
                        trace_id not in chosen
                        and len(sample) + len(traces[trace_id]) <= 450
                    ):
                        sample.extend(traces[trace_id])
                        chosen.add(trace_id)
                        break
            for trace_id in sorted(traces):
                if (
                    trace_id not in chosen
                    and len(sample) + len(traces[trace_id]) <= 450
                ):
                    sample.extend(traces[trace_id])
                    chosen.add(trace_id)
                if len(sample) == 450:
                    break
            sampled[w] = sample
            records.extend(sample)
        # Remove identical cross-window duplicates; conflicts must fail in SemanticAnalysis.
        records = sorted(records, key=lambda r: (r["end"], r["record_ref"]))
        references = {}
        for service in sorted(services):
            for signal, unit in [("error_fraction", "fraction"), ("duration_ms", "ms")]:
                values, refs = [], []
                for i in range(3):
                    rows = [
                        r for r in sampled[f"reference-{i}"] if r["service"] == service
                    ]
                    vals = (
                        [r["error"] for r in rows if type(r["error"]) is bool]
                        if signal == "error_fraction"
                        else [r["duration_ms"] for r in rows]
                    )
                    values.append(
                        (
                            sum(vals) / len(vals)
                            if signal == "error_fraction"
                            else median(vals)
                        )
                        if vals
                        else None
                    )
                    refs += [r["record_ref"] for r in rows]
                references[f"ref-{service}-{signal}"] = dict(
                    scope=[service, None, None],
                    unit=unit,
                    method="trace_sample",
                    fixed_at=lo,
                    window=windows["reference-2"],
                    source_windows=[windows[f"reference-{i}"] for i in range(3)],
                    values=values,
                    source_refs=refs,
                )
        # Initial alert only, never evaluator entities or narrative.
        title = item["task"]["alert_title"][:200]
        alert_services = [s for s in services if s in title]
        residuals = [
            dict(
                observation_id="obs-alert",
                kind="source_alert",
                summary=title,
                records=[dict(service=s) for s in sorted(alert_services)],
            )
        ]
        snapshot = dict(
            services=sorted(services),
            records=records,
            windows=windows,
            references=references,
            topology=observed_topology(records),
            residuals=residuals,
            counters={},
            metadata=dict(
                record_count=len(records),
                counter_points=0,
                available_sources=["traces"],
                trace_fields=True,
                coverage="SERVICE_STRATIFIED_WHOLE_TRACE_SAMPLE_450_PER_WINDOW",
                truncated=True,
                source_window_counts=dict(totals),
                reference_status="MATCHED_PRIOR_SAMPLED_WINDOWS_NOT_HEALTHY_BASELINE",
                availability="RETROSPECTIVE_REPLAY",
                status_semantics="OTel UNSET unknown, OK false, ERROR true",
                duration_unit="ms converted from documented ns",
                clock_synchronized=False,
            ),
        )
        if item["root"] not in services:
            raise ValueError("ROOT_NOT_IN_OBSERVED_SERVICE_CANDIDATES")
        hashes = {
            str(p.relative_to(source)): sha(p)
            for p in [directory / "task.json", directory / "traces.parquet"]
        }
        snapshot_file = f"snapshots/{case_id}.json"
        write(output / snapshot_file, snapshot)
        case = dict(
            case_id=case_id,
            event_group=f"group-{index + 1:03d}",
            split="development" if index < 12 else "test",
            root=item["root"],
            snapshot_file=snapshot_file,
        )
        config["cases"].append(case)
        manifests.append(
            dict(
                **case,
                source_task=item["name"],
                group_tasks=[x["name"] for x in group["items"]],
                support=[group["start"], group["end"]],
                mechanisms=item["mechanisms"],
                files=hashes,
                snapshot_sha256=sha(output / snapshot_file),
                sampled_counts={w: len(r) for w, r in sampled.items()},
                source_rows=q.metadata.num_rows,
                window_rows=dict(totals),
            )
        )
        print(case_id, case["split"], len(records), "records", flush=True)
    write(output / "experiment.json", config)
    write(output / "manifest.json", manifests)
    write(
        output / "availability.json",
        dict(
            source_tasks=len(items),
            eligible_tasks=sum(x["root"] is not None for x in items),
            support_groups=len(groups),
            eligible_groups=len(candidates),
            selected_development=min(12, len(selected)),
            selected_test=max(0, len(selected) - 12),
        ),
    )


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--source", type=Path, required=True)
    p.add_argument("--answers", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--base-config", type=Path, required=True)
    a = p.parse_args()
    prepare(a.source, a.answers, a.output, a.base_config)
