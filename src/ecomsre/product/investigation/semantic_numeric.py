"""Existing retained counter arithmetic, shared with semantic investigation."""

from collections import defaultdict
import math


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


def classify(labels, spec):
    key = (labels.get("service_name"), labels.get("span_name"), labels.get("span_kind"))
    for rule in spec["rules"]:
        if key == (rule["service"], rule["operation"], rule["span_kind"]):
            return rule["group"]
    return "UNCLASSIFIED"


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
