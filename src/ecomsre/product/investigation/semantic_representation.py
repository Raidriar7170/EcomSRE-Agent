"""Explicit trace-representation-repair-v1 arithmetic; no labels or I/O."""

from collections import Counter
import math
import re
from statistics import median

VERSION = "trace-representation-repair-v1"


def normalize_status(raw):
    # bool is not an integer encoding; missing is never implicit UNSET.
    key = str(raw) if type(raw) in (str, int) else None
    return dict(
        raw_status_code=raw,
        normalized_status={"0": "UNSET", "1": "OK", "2": "ERROR"}.get(
            key, "MISSING_OR_INVALID"
        ),
        status_normalization_basis="OTLP_STATUS_CODE_0_1_2_EXPLICIT_ONLY",
    )


def status_stats(rows):
    counts = Counter(r.get("normalized_status", "MISSING_OR_INVALID") for r in rows)
    unset, ok, error = (counts[k] for k in ("UNSET", "OK", "ERROR"))
    total, valid = len(rows), unset + ok + error
    return dict(
        n_total=total,
        n_valid=valid,
        n_unset=unset,
        n_ok=ok,
        n_error=error,
        n_missing_or_invalid=total - valid,
        unique_trace_count=len({r["trace_id"] for r in rows if r.get("trace_id")}),
        status_coverage=valid / total if total else None,
        missing_status_fraction=(total - valid) / total if total else None,
        error_marker_fraction=error / valid if valid else None,
        explicit_status_error_fraction=error / (ok + error) if ok + error else None,
    )


def window_stats(rows):
    vals = sorted(
        r["duration_ms"]
        for r in rows
        if type(r.get("duration_ms")) in (int, float)
        and math.isfinite(r["duration_ms"])
        and r["duration_ms"] >= 0
    )
    return dict(
        **status_stats(rows),
        duration_sample_count=len(vals),
        median_duration_ms=median(vals) if vals else None,
        p95_duration_ms=vals[math.ceil(0.95 * len(vals)) - 1] if len(vals) >= 40 else None,
        quantile_method="nearest_rank",
    )


def reference_signal(ref, reference_id):
    if "signal" in ref:
        return ref["signal"]
    # Explicit legacy adapter ID convention; unit alone never assigns semantics.
    for signal in ("duration_ms", "error_fraction"):
        if (
            reference_id == f"ref-{ref['scope'][0]}-{signal}"
            and ref.get("method") == "trace_sample"
        ):
            return signal
    return None


def select_reference(snapshot, request):
    references = snapshot.get("references", {})
    if request.reference_id is not None:
        candidates = [(request.reference_id, references.get(request.reference_id))]
    else:
        candidates = list(references.items())
    if not candidates or all(v is None for _, v in candidates):
        return None, None, "INSUFFICIENT_REFERENCE"
    current = snapshot["windows"][request.window]
    good, reasons = [], []
    for key, ref in candidates:
        if ref is None:
            continue
        if ref["fixed_at"] >= current[0] or ref["window"][1] > current[0]:
            reasons.append("REFERENCE_NOT_PRIOR")
            continue
        signal = reference_signal(ref, key)
        expected_unit = "ms" if request.signal == "duration_ms" else "fraction"
        if (
            ref["scope"] != [request.target, request.operation, request.direction]
            or signal != request.signal
            or ref["unit"] != expected_unit
            or ref["method"] != "trace_sample"
            or ref.get(
                "statistic",
                "window_median" if signal == "duration_ms" else "window_fraction",
            )
            != ("window_median" if signal == "duration_ms" else "window_fraction")
        ):
            reasons.append("REFERENCE_SCOPE_MISMATCH")
            continue
        windows = ref.get("source_windows", [ref["window"]])
        if (
            not windows or len(windows) != len(ref["values"])
        ) and "source_windows" in ref:
            reasons.append("REFERENCE_WINDOW_DEFINITION_MISMATCH")
            continue
        if (
            ref["fixed_at"] >= current[0]
            or ref["window"][1] > current[0]
            or any(w[1] > current[0] for w in windows)
        ):
            reasons.append("REFERENCE_NOT_PRIOR")
            continue
        if any(w[1] - w[0] != current[1] - current[0] for w in windows):
            reasons.append("REFERENCE_WINDOW_DEFINITION_MISMATCH")
            continue
        good.append((ref["fixed_at"], max(w[1] for w in windows), key, ref))
    if not good:
        return (
            None,
            None,
            reasons[0] if request.reference_id else "NO_COMPATIBLE_REFERENCE",
        )
    priority = max((a, b) for a, b, _, _ in good)
    best = [(k, r) for a, b, k, r in good if (a, b) == priority]
    if len(best) != 1:
        return None, None, "AMBIGUOUS_REFERENCE"
    return *best[0], None


def changes(current, reference, config):
    """Equal weight per historical window, not pooled spans or averaged p95."""
    support = config["representation_support"]
    metric = "median_duration_ms"
    eligible = [
        r for r in reference if r["duration_sample_count"] >= support["median_spans"]
    ]
    sufficient = (
        current["duration_sample_count"] >= support["median_spans"]
        and len(eligible) >= support["reference_windows"]
    )
    vals = [r[metric] for r in eligible]
    center = median(vals) if sufficient else None
    delta = current[metric] - center if center is not None else None
    mad = median(abs(v - center) for v in vals) if center is not None else None
    tail = (
        sufficient
        and current["duration_sample_count"] >= support["tail_spans"]
        and all(r["duration_sample_count"] >= support["tail_spans"] for r in eligible)
    )
    tail_center = median(r["p95_duration_ms"] for r in eligible) if tail else None
    return dict(
        status="INSUFFICIENT_SAMPLE_SUPPORT"
        if not sufficient
        else "ZERO_REFERENCE_SCALE"
        if mad == 0
        else "COMPARABLE_HISTORICAL_REFERENCE",
        current=current,
        reference_windows=reference,
        reference_center=center,
        reference_center_method="equal_weight_median_of_window_medians",
        absolute_difference=delta,
        relative_change=delta / abs(center) if center else None,
        reference_mad=mad,
        z=delta / (1.4826 * mad)
        if sufficient and len(vals) >= support["robust_windows"] and mad > 0
        else None,
        reference_count=len(eligible),
        p95_reference_center=tail_center,
        p95_absolute_difference=current["p95_duration_ms"] - tail_center
        if tail
        else None,
        p95_relative_change=(current["p95_duration_ms"] - tail_center)
        / abs(tail_center)
        if tail_center
        else None,
        healthy_baseline=False,
        support_thresholds_are_not_statistical_guarantees=True,
    )


def alert_ranking(text, services):
    matches = []
    for service in services:
        for m in re.finditer(
            r"(?<![\w-])" + re.escape(service) + r"(?![\w-])", text, re.IGNORECASE
        ):
            matches.append((m.start(), -len(service), service, m.end()))
    accepted, end = [], -1
    for start, _, service, stop in sorted(matches):
        if start >= end:
            if service not in accepted:
                accepted.append(service)
            end = stop
    return accepted[:4]
