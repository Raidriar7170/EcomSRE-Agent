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


def repair_representation(source, retained, output, base_config):
    """Four exposed events only; restore raw status on identical span identities."""
    from copy import deepcopy
    from time import monotonic
    from ecomsre.product.investigation.semantic_representation import (
        VERSION,
        normalize_status,
        window_stats,
        alert_ranking,
    )
    from ecomsre.product.investigation.semantic_analysis import SemanticAnalysis, digest
    from ecomsre.product.investigation.semantic_contracts import AnalysisRequest

    config = json.loads(base_config.read_text())
    old_config = json.loads((retained / "experiment.json").read_text())
    manifests = json.loads((retained / "manifest.json").read_text())[:4]
    config["cases"] = old_config["cases"][:4]
    expected = [f"rcase-{i:03d}" for i in range(1, 5)]
    assert [c["case_id"] for c in config["cases"]] == expected
    assert [m["case_id"] for m in manifests] == expected
    evidence = []
    for entry, case in zip(manifests, config["cases"], strict=True):
        started = monotonic()
        snapshot = json.loads((retained / case["snapshot_file"]).read_text())
        old = deepcopy(snapshot)
        directory = source / "cases" / entry["source_task"]
        for path, expected_hash in entry["files"].items():
            if sha(source / path) != expected_hash:
                raise ValueError("RAW_SOURCE_CHANGED")
        identities = {(r["trace_id"], r["span_id"]): r for r in snapshot["records"]}
        recovered = {}
        pools = {w: {} for w in snapshot["windows"]}
        raw_counts = defaultdict(int)
        scanned = 0
        parquet = pq.ParquetFile(directory / "traces.parquet")
        fields = [
            "traceId",
            "spanId",
            "parentSpanId",
            "kind",
            "spanName",
            "endTime",
            "duration",
            "serviceName",
            "statusCode",
        ]
        for batch in parquet.iter_batches(
            batch_size=16384,
            columns=[f for f in fields if f in parquet.schema_arrow.names],
        ):
            for r in batch.to_pylist():
                scanned += 1
                try:
                    end = int(r["endTime"]) / 1e9
                    duration = int(r["duration"]) / 1e6
                except (ValueError, TypeError):
                    continue
                window = next(
                    (w for w, (a, b) in snapshot["windows"].items() if a <= end < b),
                    None,
                )
                if window is None:
                    continue
                raw_counts[window] += 1
                identity = (opaque(r["traceId"]), opaque(r["spanId"]))
                row = dict(
                    service=r["serviceName"],
                    operation=r["spanName"],
                    direction={
                        "1": "internal",
                        "2": "server",
                        "3": "client",
                        "4": "producer",
                        "5": "consumer",
                    }.get(str(r["kind"])),
                    duration_ms=duration,
                    trace_id=identity[0],
                    span_id=identity[1],
                    parent_span_id=opaque(r["parentSpanId"])
                    if r["parentSpanId"]
                    else None,
                    **normalize_status(r.get("statusCode")),
                )
                previous = pools[window].get(identity)
                if previous is not None and previous != row:
                    raise ValueError("CONFLICTING_RAW_DUPLICATE")
                pools[window][identity] = row
                if identity in identities:
                    sampled = identities[identity]
                    if (
                        sampled["service"],
                        sampled["operation"],
                        sampled["direction"],
                        sampled["duration_ms"],
                    ) != (
                        row["service"],
                        row["operation"][:180],
                        row["direction"],
                        row["duration_ms"],
                    ):
                        raise ValueError("RETAINED_IDENTITY_FIELDS_MISMATCH")
                    recovered[identity] = row
        if set(recovered) != set(identities):
            raise ValueError("RAW_STATUS_NOT_RECOVERABLE_FOR_ALL_RETAINED_SPANS")
        for identity, row in identities.items():
            raw = recovered[identity]
            row.update(
                {
                    k: raw[k]
                    for k in (
                        "raw_status_code",
                        "normalized_status",
                        "status_normalization_basis",
                        "operation",
                    )
                }
            )
        snapshot["metadata"].update(
            representation_version=VERSION,
            status_semantics="explicit OTel 0=UNSET,1=OK,2=ERROR; missing/invalid separate; no business success inference",
            sampling_revision=0,
            sampling_identity_preserved=True,
        )
        references = {}
        scopes = {(s, None, None) for s in snapshot["services"]}
        scopes |= {
            (r["service"], r["operation"], r["direction"])
            for r in snapshot["records"]
            if r["operation"] and r["direction"]
        }
        windows = [snapshot["windows"][f"reference-{i}"] for i in range(3)]
        for scope in sorted(scopes, key=str):
            stats, refs = [], []
            for a, b in windows:
                rows = [
                    r
                    for r in snapshot["records"]
                    if a <= r["end"] < b
                    and r["service"] == scope[0]
                    and (scope[1] is None or r["operation"] == scope[1])
                    and (scope[2] is None or r["direction"] == scope[2])
                ]
                stats.append(window_stats(rows))
                refs.extend(r["record_ref"] for r in rows)
            for signal in (
                "duration_ms",
                "error_marker_fraction",
                "explicit_status_error_fraction",
            ):
                key = "R" + digest([scope, signal])[:16]
                references[key] = dict(
                    scope=list(scope),
                    signal=signal,
                    unit="ms" if signal == "duration_ms" else "fraction",
                    method="trace_sample",
                    statistic="window_median"
                    if signal == "duration_ms"
                    else "window_fraction",
                    fixed_at=max(w[1] for w in windows),
                    window=windows[-1],
                    source_windows=windows,
                    values=[
                        v["median_duration_ms" if signal == "duration_ms" else signal]
                        for v in stats
                    ],
                    window_statistics=stats,
                    source_refs=sorted(set(refs)),
                )
        snapshot["references"] = references
        availability = {}
        for window, (a, b) in snapshot["windows"].items():
            sampled = [r for r in snapshot["records"] if a <= r["end"] < b]
            full = list(pools[window].values())

            def coverage(rows):
                by_scope = defaultdict(list)
                ids = {(r["trace_id"], r["span_id"]) for r in rows}
                for r in rows:
                    by_scope[(r["service"], r["operation"], r["direction"])].append(r)
                return dict(
                    **window_stats(rows),
                    missing_parents=sum(
                        bool(r.get("parent_span_id"))
                        and (r["trace_id"], r["parent_span_id"]) not in ids
                        for r in rows
                    ),
                    operations=[
                        dict(scope=list(scope), **window_stats(rs))
                        for scope, rs in sorted(
                            by_scope.items(), key=lambda x: str(x[0])
                        )
                    ],
                )

            full_scopes = {(r["service"], r["operation"], r["direction"]) for r in full}
            sample_scopes = {
                (r["service"], r["operation"], r["direction"]) for r in sampled
            }
            availability[window] = dict(
                source=coverage(full),
                sample=coverage(sampled),
                sampling_lost_scopes=[
                    list(s) for s in sorted(full_scopes - sample_scopes, key=str)
                ],
                raw_rows=raw_counts[window],
            )
        task = json.loads((directory / "task.json").read_text())
        alert = task["alert_title"]
        # Both C arms receive the same original visible alert, never labels.
        snapshot["residuals"] = [
            dict(
                observation_id="obs-alert",
                kind="source_alert",
                summary=alert,
                records=[
                    dict(service=s) for s in alert_ranking(alert, snapshot["services"])
                ],
            )
        ]
        analysis = SemanticAnalysis(snapshot, config)
        features = []
        for scope in sorted(scopes, key=str):
            req = AnalysisRequest(
                tool="compare_baseline",
                target=scope[0],
                operation=scope[1],
                direction=scope[2],
                signal="duration_ms",
            )
            features.append(dict(scope=list(scope), result=analysis.execute(req)))
        write(output / case["snapshot_file"], snapshot)
        result = dict(
            case_id=case["case_id"],
            source_hashes=entry["files"],
            old_snapshot_sha256=sha(retained / case["snapshot_file"]),
            snapshot_sha256=sha(output / case["snapshot_file"]),
            same_span_identities=True,
            sampling_revision=0,
            source_records_scanned=scanned,
            retained_records=len(snapshot["records"]),
            availability=availability,
            features=features,
            alert_only=dict(
                ranked_components=alert_ranking(alert, snapshot["services"]),
                model_calls=0,
            ),
            legacy_unknown_restored=sum(
                type(r.get("error")) is not bool
                and identities[(r["trace_id"], r["span_id"])]["normalized_status"]
                == "UNSET"
                for r in old["records"]
            ),
            elapsed_seconds=monotonic() - started,
        )
        write(output / f"{case['case_id']}-features.json", result)
        evidence.append(
            {k: v for k, v in result.items() if k not in ("availability", "features")}
        )
        print(case["case_id"], len(recovered), "restored identities", flush=True)
    write(output / "manifest.json", evidence)
    write(output / "experiment.json", config)


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--source", type=Path, required=True)
    p.add_argument("--answers", type=Path)
    p.add_argument("--repair-retained", type=Path)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--base-config", type=Path, required=True)
    a = p.parse_args()
    if a.repair_retained:
        repair_representation(a.source, a.repair_retained, a.output, a.base_config)
    else:
        if a.answers is None:
            p.error("--answers required for legacy preparation")
        prepare(a.source, a.answers, a.output, a.base_config)
