"""Pure, bounded analysis of frozen trace samples. No network or storage writes."""

from collections import defaultdict
import hashlib
import json
import math
from statistics import median
from time import monotonic

from .semantic_numeric import grouped_point


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, allow_nan=False).encode()
    ).hexdigest()


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def purpose(row, rules):
    for rule in rules:
        if (row["service"], row.get("operation"), row.get("direction")) == (
            rule["service"],
            rule["operation"],
            rule["span_kind"].removeprefix("SPAN_KIND_").lower(),
        ):
            return rule["group"]
    return "UNCLASSIFIED"


def row_key(row, group_by):
    # Service and direction always remain separate; RPC sides are not orders.
    return "|".join(
        str(row.get(k) or "UNKNOWN")
        for k in {
            "operation": ("service", "operation", "direction"),
            "span_kind": ("service", "direction"),
            "purpose": ("service", "purpose", "direction"),
        }[group_by]
    )


def summarize(rows, group_by, detail):
    groups = defaultdict(list)
    for row in rows:
        groups[row_key(row, group_by)].append(row)
    table = []
    for key, values in sorted(groups.items()):
        known = [r for r in values if type(r.get("error")) is bool]
        durations = sorted(
            r["duration_ms"]
            for r in values
            if finite(r.get("duration_ms")) and r["duration_ms"] >= 0
        )
        item = dict(
            row_key=key,
            count=len(values),
            status_known_count=len(known),
            error_count=sum(r["error"] for r in known),
            error_fraction=sum(r["error"] for r in known) / len(known)
            if known
            else None,
            unknown_status_count=len(values) - len(known),
            duration_sample_count=len(durations),
            median_duration_ms=median(durations) if durations else None,
            p95_duration_ms=durations[math.ceil(0.95 * len(durations)) - 1]
            if durations
            else None,
            purposes=sorted({r["purpose"] for r in values}),
            representative_refs=[r["record_ref"] for r in values[:3]]
            if detail == "representative_records"
            else [],
        )
        table.append(item)
    return table


def compare_series(current, references, *, epsilon, minimum, threshold):
    """Historical scalar samples, never subtraction/averaging of quantiles."""
    refs = [v for v in references if finite(v)]
    good = [v for v in current if finite(v)]
    if len(refs) < minimum or not good:
        return dict(
            status="INSUFFICIENT_REFERENCE",
            absolute_difference=None,
            relative_change=None,
            z=None,
            consecutive_deviations=None,
        )
    center = median(refs)
    mad = median(abs(x - center) for x in refs)
    scale = max(1.4826 * mad, epsilon)
    value = median(good)
    # Missing points break consecutive runs, never disappear from time support.
    run = longest = 0
    for x in current:
        run = run + 1 if finite(x) and abs(x - center) >= threshold else 0
        longest = max(longest, run)
    return dict(
        status="ZERO_REFERENCE_SCALE"
        if mad == 0
        else "COMPARABLE_HISTORICAL_REFERENCE",
        current=value,
        reference=center,
        absolute_difference=value - center,
        relative_change=(value - center) / abs(center) if center else None,
        z=(value - center) / scale if mad > 0 else None,
        consecutive_deviations=longest,
        reference_count=len(refs),
        missing_reference_count=len(references) - len(refs),
        healthy_baseline=False,
    )


class SemanticAnalysis:
    """Snapshot contains only observer data; labels stay outside this object."""

    def __init__(self, snapshot, config):
        self.snapshot, self.config = snapshot, config
        self.snapshot_id = digest(snapshot)
        self.services = snapshot["services"]
        self.rows = []
        unique = {}
        for original in snapshot["records"]:
            row = dict(original)
            identity = (row.get("trace_id"), row.get("span_id"))
            if None in identity:
                identity = (row["record_ref"], None)
            if identity in unique:
                if unique[identity] != row:
                    raise ValueError("CONFLICTING_DUPLICATE_SPAN")
                continue
            unique[identity] = dict(row)
            row["purpose"] = purpose(row, config["mapping"])
            self.rows.append(row)
        self.by_id = {
            (r.get("trace_id"), r.get("span_id")): r
            for r in self.rows
            if r.get("trace_id") and r.get("span_id")
        }

    def validate(self, request):
        if request.source == "counters" and (
            request.tool not in ("read_records", "profile_operations")
            or request.group_by != "operation"
        ):
            raise ValueError("COUNTERS_SUPPORT_OPERATION_PROFILE_OR_RAW_READ_ONLY")
        if (
            request.target not in self.services
            or request.window not in self.snapshot["windows"]
        ):
            raise ValueError("UNKNOWN_TARGET_OR_WINDOW")
        if any(
            n not in self.services or n == request.target for n in request.neighbors
        ) or len(set(request.neighbors)) != len(request.neighbors):
            raise ValueError("UNKNOWN_OR_DUPLICATE_NEIGHBOR")
        if (
            request.reference_id is not None
            and request.reference_id not in self.snapshot.get("references", {})
        ):
            raise ValueError("UNKNOWN_REFERENCE")
        if (
            request.source == "counters"
            and self.snapshot["windows"][request.window][1]
            - self.snapshot["windows"][request.window][0]
            != 300
        ):
            raise ValueError("COUNTER_PROXY_REQUIRES_300_SECOND_SUPPORT")
        if request.tool != "read_records" and request.offset:
            raise ValueError("OFFSET_ONLY_FOR_RAW_READ")

    def rows_for(self, request, service=None):
        start, end = self.snapshot["windows"][request.window]
        return [
            r
            for r in self.rows
            if r["service"] == (service or request.target)
            and finite(r.get("end"))
            and start <= r["end"] < end
            and (request.operation is None or r.get("operation") == request.operation)
            and (request.direction is None or r.get("direction") == request.direction)
        ]

    def estimate(self, request):
        self.validate(request)
        # Only public metadata. No data-dependent answer peeking.
        queries = 1 + (
            len(request.neighbors) if request.tool == "compare_dependencies" else 0
        )
        if request.tool == "compare_baseline":
            queries += 1
        if request.tool == "compare_dependencies":
            queries += sum(
                any(
                    v["scope"] == [service, request.operation, request.direction]
                    for v in self.snapshot.get("references", {}).values()
                )
                for service in [request.target, *request.neighbors]
            )
        count = (
            self.snapshot["metadata"]
            .get("counter_points_by_service", {})
            .get(request.target, 0)
            if request.source == "counters"
            else self.snapshot["metadata"]["record_count"]
            * (13 if request.tool == "compare_dependencies" else queries)
        )
        q = 1.0 if self.snapshot["metadata"].get("trace_fields") else 0.25
        if request.tool == "compare_baseline" and request.reference_id is None:
            q = 0.0
        return dict(
            quality=q,
            cost=queries + count / self.config["cost_record_scale"],
            queries=queries,
            records_scanned=count,
        )

    def metadata(self):
        return dict(
            services=self.services,
            windows=self.snapshot["windows"],
            references={
                k: {f: v[f] for f in ("fixed_at", "window", "unit", "method")}
                for k, v in self.snapshot.get("references", {}).items()
            },
            source_metadata=self.snapshot["metadata"],
            mapping=self.config["mapping"],
            residuals=self.snapshot.get("residuals", []),
            operation_inventory=sorted(
                {
                    (r["service"], r.get("operation"), r.get("direction"))
                    for r in self.rows
                },
                key=str,
            ),
            basic_fields=[
                "service",
                "operation",
                "direction",
                "error",
                "duration_ms",
                "start",
                "end",
                "trace_id",
                "span_id",
                "parent_span_id",
                "purpose",
                "record_ref",
            ],
        )

    def execute(self, request):
        start = monotonic()
        estimate = self.estimate(request)
        rows = self.rows_for(request) if request.source == "traces" else []
        limitations = [
            "TRACE_SAMPLE_NOT_POPULATION",
            "COMPLETED_SPANS_IN_WINDOW_NOT_INDEPENDENT_ORDERS",
            "MISSING_NOT_ZERO",
            "NO_CAUSAL_OR_HEALTH_PROOF",
        ]
        refs = [r["record_ref"] for r in rows]
        if request.source == "counters":
            counter = self.snapshot.get("counters", {}).get(
                request.target, dict(rows=[], buckets=[])
            )
            lo, hi = self.snapshot["windows"][request.window]

            def filtered(series):
                return [
                    dict(
                        metric=r["metric"],
                        values=[[t, v] for t, v in r["values"] if lo < float(t) <= hi],
                    )
                    for r in series
                    if (
                        request.operation is None
                        or r["metric"].get("span_name") == request.operation
                    )
                    and (
                        request.direction is None
                        or r["metric"]
                        .get("span_kind", "")
                        .removeprefix("SPAN_KIND_")
                        .lower()
                        == request.direction
                    )
                ]

            selected = filtered(counter["rows"])
            buckets = filtered(counter["buckets"])
            if request.tool == "read_records":
                raw = [dict(kind="calls", **r) for r in selected] + [
                    dict(kind="bucket", **r) for r in buckets
                ]
                table = raw[request.offset : request.offset + self.config["page_size"]]
                extra = dict(
                    total_records=len(raw),
                    next_offset=request.offset + len(table)
                    if request.offset + len(table) < len(raw)
                    else None,
                )
            else:
                table = grouped_point(
                    selected,
                    buckets,
                    self.snapshot["windows"][request.window][1],
                    dict(rules=self.config["mapping"]),
                )
                for r in table:
                    r.update(
                        row_key="|".join(
                            [
                                request.target,
                                str(r["operation"]),
                                str(r["direction"]).removeprefix("SPAN_KIND_").lower(),
                            ]
                        ),
                        count=r.get("total"),
                        error_count=r.get("errors"),
                    )
                extra = dict(total_series=len(selected), other_groups=0, other_count=0)
            refs = counter.get("source_refs", [])
            limitations = [
                "OBSERVED_COUNTER_INCREMENT_NOT_RATE",
                "NO_POPULATION_COMPLETENESS",
                "NO_P95_RECONSTRUCTION",
                "OVERLAPPING_SUPPORT_NOT_ADDITIVE",
            ]
        elif request.tool == "read_records":
            table = rows[request.offset : request.offset + self.config["page_size"]]
            extra = dict(
                total_records=len(rows),
                next_offset=request.offset + len(table)
                if request.offset + len(table) < len(rows)
                else None,
            )
        elif request.tool == "profile_operations":
            table = summarize(rows, request.group_by, request.detail_level)
            extra = dict(total_records=len(rows), other_groups=0, other_count=0)
        elif request.tool == "compare_baseline":
            table = [self.baseline(request, rows)]
            refs += table[0].pop("source_refs", [])
            extra = {}
        else:
            table, dependency_refs = self.dependencies(request, rows)
            refs += dependency_refs
            extra = {}
        return dict(
            analysis_id="an-"
            + digest([self.snapshot_id, canonical_request(request)])[:20],
            request=request.model_dump(),
            method=request.tool,
            table=table,
            **extra,
            source_refs=sorted(set(refs)),
            coverage=self.snapshot["metadata"].get("coverage", "UNKNOWN"),
            truncated=self.snapshot["metadata"].get("truncated", True),
            limitations=limitations,
            access=dict(
                queries=estimate["queries"],
                records_scanned=estimate["records_scanned"],
                returned_bytes=len(json.dumps(table).encode()),
                compute_ms=(monotonic() - start) * 1000,
            ),
        )

    def baseline(self, request, rows):
        ref = self.snapshot.get("references", {}).get(request.reference_id)
        unit = "fraction" if request.signal == "error_fraction" else "ms"
        window = self.snapshot["windows"][request.window]
        scope = [request.target, request.operation, request.direction]
        result = dict(
            row_key="comparison",
            absolute_difference=None,
            relative_change=None,
            z=None,
            consecutive_deviations=None,
        )
        if ref is None:
            return dict(result, status="INSUFFICIENT_REFERENCE")
        if ref["fixed_at"] >= window[0] or ref["window"][1] > window[0]:
            return dict(result, status="REFERENCE_NOT_PRIOR")
        if (
            ref["unit"] != unit
            or ref["scope"] != scope
            or ref["method"] != "trace_sample"
            or ref["window"][1] - ref["window"][0] != window[1] - window[0]
        ):
            return dict(result, status="REFERENCE_SCOPE_MISMATCH")
        if request.signal == "error_fraction":
            known = [r["error"] for r in rows if type(r.get("error")) is bool]
            values = [sum(known) / len(known)] if known else []
        else:
            values = [
                r["duration_ms"]
                for r in sorted(rows, key=lambda r: r["end"])
                if finite(r.get("duration_ms"))
            ]
        params = self.config["baseline"][request.signal]
        return dict(
            result,
            **compare_series(values, ref["values"], **params),
            source_refs=ref["source_refs"],
        )

    def dependencies(self, request, rows):
        involved = set([request.target, *request.neighbors])
        observed = []
        target_refs = {r["record_ref"] for r in rows}
        missing = 0
        refs = []
        counts = defaultdict(lambda: dict(count=0, error_count=0, unknown_status=0))
        for r in self.rows:
            if r["service"] not in involved or not (
                self.snapshot["windows"][request.window][0]
                <= (r.get("end") or -1)
                < self.snapshot["windows"][request.window][1]
            ):
                continue
            if r["service"] == request.target and r["record_ref"] not in target_refs:
                continue
            refs.append(r["record_ref"])
            key = r.get("parent_span_id")
            parent = self.by_id.get((r.get("trace_id"), key)) if key else None
            if key and parent is None:
                missing += 1
            if (
                parent
                and parent["service"] != r["service"]
                and parent["service"] in involved
                and request.target in (r["service"], parent["service"])
                and (
                    parent["service"] != request.target
                    or parent["record_ref"] in target_refs
                )
            ):
                if (
                    self.snapshot["windows"][request.window][0]
                    <= (parent.get("end") or -1)
                    < self.snapshot["windows"][request.window][1]
                ):
                    observed.append([parent["service"], r["service"]])
                    refs.append(parent["record_ref"])
                # Keep both RPC sides separately, count each span once below.
            c = counts[r["service"]]
            c["count"] += 1
            c["error_count"] += int(r.get("error") is True)
            c["unknown_status"] += int(type(r.get("error")) is not bool)
        static = self.snapshot.get("topology", [])
        neighbors = {
            n
            for edge in static
            for n in edge
            if request.target in edge and n != request.target
        }
        neighbors |= {n for edge in observed for n in edge if n != request.target}
        if any(n not in neighbors for n in request.neighbors):
            return [
                dict(
                    row_key="dependencies",
                    status="UNSUPPORTED_ONE_HOP_RELATION",
                    observed_edges=None,
                    missing_parents=missing,
                )
            ], refs
        shared = []
        ref_set = set(refs)
        by_trace = defaultdict(list)
        for r in self.rows:
            if r["record_ref"] in ref_set:
                by_trace[r.get("trace_id")].append(r)
        for tid in sorted({r.get("trace_id") for r in rows if r.get("trace_id")}):
            members = by_trace[tid]
            if len({r["service"] for r in members}) > 1:
                shared.append(
                    dict(
                        trace_handle="tr-" + digest(tid)[:12],
                        services={
                            s: dict(
                                spans=sum(r["service"] == s for r in members),
                                errors=sum(
                                    r["service"] == s and r.get("error") is True
                                    for r in members
                                ),
                            )
                            for s in sorted({r["service"] for r in members})
                        },
                    )
                )
        changes = {}
        for service in sorted(involved):
            matches = sorted(
                k
                for k, v in self.snapshot.get("references", {}).items()
                if v["scope"] == [service, request.operation, request.direction]
            )
            comparison_request = request.model_copy(
                update={
                    "tool": "compare_baseline",
                    "target": service,
                    "reference_id": matches[0] if matches else None,
                }
            )
            changes[service] = self.baseline(
                comparison_request, self.rows_for(comparison_request)
            )
            refs += changes[service].pop("source_refs", [])
        resolution = self.snapshot["metadata"].get("time_resolution_seconds")
        synced = self.snapshot["metadata"].get("synchronized_clocks", False)
        onset = {}
        if finite(resolution) and resolution > 0 and synced:
            for service in sorted(involved):
                starts = [
                    r["start"]
                    for r in self.rows_for(request, service)
                    if r.get("error") is True and finite(r.get("start"))
                ]
                onset[service] = min(starts) if starts else None
        return [
            dict(
                row_key="dependencies",
                observed_edges=len(observed),
                edges=sorted({tuple(e) for e in observed}),
                missing_parents=missing,
                service_samples=dict(counts),
                shared_trace_errors=shared[:10],
                shared_trace_count=len(shared),
                shared_trace_truncated=len(shared) > 10,
                onset_order="OBSERVED_SAMPLE_ONSETS_NOT_CAUSAL"
                if onset
                else "UNKNOWN_CLOCK_AND_SAMPLING_RESOLUTION",
                onset_times=onset,
                time_resolution_seconds=resolution,
                relative_changes=changes,
                static_topology_only=[
                    edge
                    for edge in static
                    if request.target in edge and set(edge) <= involved
                ],
            )
        ], refs


def canonical_request(request):
    value = request.model_dump()
    relevant = {"tool", "target", "source", "window", "operation", "direction"}
    relevant |= {"offset"} if request.tool == "read_records" else set()
    relevant |= {"group_by"} if request.tool == "profile_operations" else set()
    relevant |= (
        {"signal", "reference_id"} if request.tool == "compare_baseline" else set()
    )
    relevant |= (
        {"signal", "neighbors"} if request.tool == "compare_dependencies" else set()
    )
    return {
        k: (sorted(v) if k == "neighbors" else v)
        for k, v in value.items()
        if k in relevant
    }
