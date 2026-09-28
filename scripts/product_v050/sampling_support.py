"""Versioned, candidate-independent sample support; no I/O or state transitions.

Observed spacing is never configuration. A computable PromQL value is not a
complete observation window. Missing pre-birth values are never synthesized.
"""

import math
import re
import statistics

VERSION = "ingestion-sample-evidence-v3"


def seconds(value):
    if not isinstance(value, str) or not value.endswith("s"):
        raise ValueError("explicit producer interval in seconds required")
    result = float(value[:-1])
    if not math.isfinite(result) or result <= 0:
        raise ValueError("invalid producer interval")
    return result


def profile(collector, selector, application=None):
    name = selector.split("{", 1)[0]
    defaults = (application or {}).get("versioned_producer_defaults", {})

    def declared_default(key):
        value = defaults.get(key)
        if value is None:
            return None
        refs = value.get("evidence_sha256", [])
        if (
            value.get("basis") != "RUNTIME_VERSION_BOUND_DEFAULT"
            or not value.get("runtime_version")
            or not value.get("runtime_image_id", "").startswith("sha256:")
            or len(refs) < 2
            or any(
                not isinstance(h, str) or not re.fullmatch(r"[0-9a-f]{64}", h)
                for h in refs
            )
            or value.get("overrides_absent") is not True
        ):
            raise ValueError("incomplete runtime default evidence")
        return value

    if name.startswith("container_"):
        period = seconds(collector["receivers"]["docker_stats"]["collection_interval"])
        source = "collector.receivers.docker_stats.collection_interval"
    elif name.startswith("kafka_consumer_group_"):
        period = seconds(collector["receivers"]["kafkametrics"]["collection_interval"])
        source = "collector.receivers.kafkametrics.collection_interval"
    elif name.startswith("traces_span_metrics_"):
        period = seconds(
            collector["connectors"]["span_metrics"]["metrics_flush_interval"]
        )
        source = "collector.connectors.span_metrics.metrics_flush_interval"
    else:
        # Defaults require retained running-version and override-absence evidence.
        # Observed cadence alone never declares the producer period.
        from scripts.product_v050.ingestion_evidence import MATCHER

        service = next(
            (
                v
                for k, op, v in MATCHER.findall(selector)
                if k == "service_name" and op == "="
            ),
            None,
        )
        config = (application or {}).get("effective_environments", {}).get(service, {})
        raw = config.get("OTEL_METRIC_EXPORT_INTERVAL")
        period = float(raw) / 1000 if raw is not None else None
        if period is not None and (not math.isfinite(period) or period <= 0):
            raise ValueError("invalid effective export interval")
        default = declared_default(service) if raw is None else None
        if default is not None:
            period = float(default["period_seconds"])
            if not math.isfinite(period) or period <= 0:
                raise ValueError("invalid runtime default interval")
        source = (
            "runtime_version_bound_default"
            if default is not None
            else "effective_environment.OTEL_METRIC_EXPORT_INTERVAL"
            if raw
            else "UNKNOWN_EFFECTIVE_APPLICATION_INTERVAL"
        )
    # Separate requirements, with a fixed scheduling allowance chosen before
    # evaluation. At most 25% of one period plus 1s; not one missed full cycle.
    allowance = None if period is None else period * 0.25 + 1
    histogram_bounds = None
    if name.startswith("traces_span_metrics_") and name.endswith(
        "_milliseconds_bucket"
    ):
        configured = (
            collector["connectors"]["span_metrics"]
            .get("histogram", {})
            .get("explicit", {})
            .get("buckets")
        )
        if configured:
            histogram_bounds = []
            for boundary in configured:
                match = re.fullmatch(r"(\d+(?:\.\d+)?)(ns|us|µs|ms|s)", str(boundary))
                if not match:
                    raise ValueError("unsupported explicit histogram duration")
                number, unit = match.groups()
                histogram_bounds.append(
                    float(number)
                    * {"ns": 1e-6, "us": 0.001, "µs": 0.001, "ms": 1, "s": 1000}[unit]
                )
            if histogram_bounds != sorted(set(histogram_bounds)):
                raise ValueError("invalid explicit histogram boundaries")
            histogram_bounds.append("+Inf")
    histogram_source = (
        "explicit_collector_configuration"
        if histogram_bounds is not None
        else "UNKNOWN"
    )
    if (
        name.endswith("_milliseconds_bucket")
        and name.startswith("traces_span_metrics_")
        and histogram_bounds is None
    ):
        default = declared_default("span_metrics")
        if default is not None:
            from scripts.product_v050.ingestion_evidence import sha

            if default.get("collector_sha256") != sha(collector):
                raise ValueError("runtime histogram configuration binding differs")
            bounds = default["histogram_bounds_milliseconds"]
            if (
                not bounds
                or any(
                    not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0
                    for v in bounds
                )
                or bounds != sorted(set(bounds))
            ):
                raise ValueError("invalid runtime default histogram bounds")
            histogram_bounds = list(bounds) + ["+Inf"]
            histogram_source = "runtime_version_bound_default"
    return dict(
        default_evidence_sha256=sorted(
            {h for value in defaults.values() for h in value.get("evidence_sha256", [])}
        ),
        histogram_source=histogram_source,
        expected_histogram_bounds=histogram_bounds,
        declared_period_seconds=period,
        period_source=source,
        metric_kind="COUNTER" if name.endswith(("_total", "_bucket")) else "GAUGE",
        maximum_age_seconds=None if period is None else period + allowance,
        maximum_gap_seconds=None if period is None else period + allowance,
        left_boundary_tolerance_seconds=None if period is None else period + allowance,
        minimum_samples=2,
        rationale="one producer cycle plus fixed 25 percent scheduling allowance and 1s; no missed full cycle",
    )


def assess(body, selector, start, end, policy, *, query_start, inner_seconds):
    from scripts.product_v050.ingestion_evidence import label_matches, sha

    result = dict(
        policy=policy,
        series=[],
        reasons=[],
        state="UNKNOWN",
        support_window=[start, end],
        query_window=[query_start, end],
        otlp_start_time="UNAVAILABLE_IN_PROMETHEUS_MATRIX",
        population_completeness="UNKNOWN_UNRETURNED_LABELS",
    )
    reasons = result["reasons"]
    if (
        not isinstance(body, dict)
        or body.get("status") != "success"
        or body.get("warnings")
        or body.get("infos")
    ):
        reasons.append("QUERY_INCOMPLETE")
        return result
    data = body.get("data", {})
    rows = data.get("result")
    if data.get("resultType") != "matrix" or not isinstance(rows, list):
        reasons.append("MALFORMED_MATRIX")
        return result
    if not rows:
        result["state"] = "EMPTY"
        return result
    if selector.split("{", 1)[0].endswith("_bucket"):
        expected = policy.get("expected_histogram_bounds")
        if expected is None:
            reasons.append("HISTOGRAM_EXPECTED_BUCKETS_UNPROVEN")
        else:
            groups = {}
            try:
                for row in rows:
                    labels = row["metric"]
                    key = tuple(sorted((k, v) for k, v in labels.items() if k != "le"))
                    groups.setdefault(key, []).append(float(labels["le"]))
                wanted = sorted(float(x) for x in expected)
                if any(sorted(bounds) != wanted for bounds in groups.values()):
                    reasons.append("HISTOGRAM_CONFIGURED_BUCKETS_MISSING_OR_EXTRA")
            except (KeyError, TypeError, ValueError):
                reasons.append("HISTOGRAM_BUCKET_GROUP_INCOMPLETE")
    seen = set()
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("metric"), dict):
            reasons.append("MALFORMED_SERIES")
            continue
        labels = row["metric"]
        identity = sha(labels)
        r = dict(
            labels=labels,
            series_sha256=identity,
            reasons=[],
            lifecycle="ESTABLISHED_IN_RETURNED_WINDOW",
            counter_resets=[],
            effective_support_intervals=[],
        )
        result["series"].append(r)
        why = r["reasons"]
        if identity in seen:
            why.append("DUPLICATE_SERIES")
        seen.add(identity)
        if not label_matches(selector, labels):
            why.append("WRONG_SERVICE_OR_SELECTOR")
        try:
            values = row["values"]
            if not values or row.get("histograms"):
                raise ValueError()
            times = [float(v[0]) for v in values]
            nums = [float(v[1]) for v in values]
            if not all(math.isfinite(v) for v in times + nums):
                raise ValueError()
        except (KeyError, TypeError, ValueError, IndexError):
            why.append("INVALID_SAMPLES")
            continue
        if (
            times != sorted(set(times))
            or times[-1] > end
            or times[0] < start - (301 if not inner_seconds else 1)
        ):
            why.append("SAMPLE_TIME_SCOPE_ERROR")
            continue
        r["raw_first_sample"] = times[0]
        if not inner_seconds:
            # Raw v3 requests include a 5m prefix. Only the latest predecessor
            # can support the first instant; older gaps/resets are irrelevant.
            before = [i for i, t in enumerate(times) if t <= query_start]
            offset = before[-1] if before else 0
            times, nums = times[offset:], nums[offset:]
        gaps = [b - a for a, b in zip(times, times[1:])]
        r.update(
            first_sample=times[0],
            last_sample=times[-1],
            sample_count=len(times),
            last_age_seconds=end - times[-1],
            left_offset_seconds=times[0] - start,
            observed_median_period_seconds=statistics.median(gaps) if gaps else None,
            observed_max_gap_seconds=max(gaps, default=None),
            minimum_value=min(nums),
            maximum_value=max(nums),
        )
        period = policy["declared_period_seconds"]
        if period is None:
            why.append("PRODUCER_PERIOD_UNPROVEN")
        else:
            if end - times[-1] > policy["maximum_age_seconds"]:
                why.append("STALE_LAST_SAMPLE")
            if times[0] - start > policy["left_boundary_tolerance_seconds"]:
                why.append("LEFT_SUPPORT_MISSING")
                r["lifecycle"] = "FIRST_OBSERVED_LABEL_BIRTH_OR_RESTART_UNKNOWN"
            breaks = [
                i + 1
                for i, gap in enumerate(gaps)
                if gap > policy["maximum_gap_seconds"]
            ]
            if breaks:
                why.append("ESTABLISHED_SERIES_INTERRUPTION")
            points = [0] + breaks + [len(times)]
            r["effective_support_intervals"] = [
                [times[a], times[b - 1]] for a, b in zip(points, points[1:])
            ]
        if len(times) < policy["minimum_samples"]:
            why.append("MINIMUM_SAMPLE_SUPPORT_MISSING")
        if policy["metric_kind"] == "COUNTER":
            if min(nums) < 0:
                why.append("NEGATIVE_COUNTER")
            resets = [
                times[i + 1] for i, (a, b) in enumerate(zip(nums, nums[1:])) if b < a
            ]
            r["counter_resets"] = resets
            if resets:
                r["lifecycle"] = "COUNTER_RESET_RESTART_OR_RESET_UNKNOWN"
                why.append("COUNTER_RESET_IN_SUPPORT")
        # PromQL evaluates rate at each outer 10s grid point. Its left endpoint
        # is exclusive; two samples somewhere in the outer window do not suffice.
        grid = [query_start + i * 10 for i in range(int((end - query_start) // 10) + 1)]
        if inner_seconds:
            missing = [
                t for t in grid if sum(t - inner_seconds < s <= t for s in times) < 2
            ]
            r["unsupported_evaluation_times"] = missing
            if missing:
                why.append("INNER_WINDOW_INSUFFICIENT_SAMPLES")
        else:
            age = policy["maximum_age_seconds"]
            missing = [
                t
                for t in grid
                if not any(
                    s <= t and t - s <= min(300, age if age is not None else 300)
                    for s in times
                )
            ]
            r["unsupported_evaluation_times"] = missing
            if missing:
                why.append("INSTANT_EVALUATION_SAMPLE_MISSING")
                if query_start in missing:
                    r["lifecycle"] = "FIRST_EVALUATION_BIRTH_OR_PRIOR_SAMPLE_UNKNOWN"
        reasons.extend(why)
    for r in result["series"]:
        if "LEFT_SUPPORT_MISSING" not in r["reasons"]:
            continue
        instance = r["labels"].get("service_instance_id", r["labels"].get("instance"))
        if instance and any(
            other is not r
            and other.get("first_sample", end) < r.get("first_sample", start)
            and other["labels"].get(
                "service_instance_id", other["labels"].get("instance")
            )
            == instance
            for other in result["series"]
        ):
            r["lifecycle"] = "NEW_LABEL_ON_OBSERVED_INSTANCE_BIRTH_UNPROVEN"
    if result["series"] and all(
        "LEFT_SUPPORT_MISSING" in r["reasons"] for r in result["series"]
    ):
        result["lifecycle_scope"] = "SERVICE_STARTUP_OR_COMMON_SOURCE_GAP_UNRESOLVED"
    else:
        result["lifecycle_scope"] = "PER_SERIES_OBSERVATIONS_ONLY"
    reasons[:] = sorted(
        set(reasons + [w for r in result["series"] for w in r["reasons"]])
    )
    if not reasons:
        result["state"] = "FRESH_COVERED"
    return result


def correspondence(bodies):
    """Check label/time/value correspondence before aggregations hide it."""
    reasons = []
    for selector, body in bodies.items():
        rows = body.get("data", {}).get("result", [])
        if not isinstance(rows, list):
            continue
        if 'status_code="STATUS_CODE_ERROR"' in selector or selector.startswith(
            "kafka_request_failed_total{"
        ):
            companion = selector.replace(
                "kafka_request_failed_total{", "kafka_request_count_total{"
            ).replace(',status_code="STATUS_CODE_ERROR"', "")
            totals = bodies.get(companion, {}).get("data", {}).get("result", [])

            def key(row):
                return tuple(
                    sorted(
                        (k, v)
                        for k, v in row.get("metric", {}).items()
                        if k not in {"__name__", "status_code"}
                    )
                )

            for row in rows:
                # Span total selector includes the error series itself. Compare
                # exact full labels there; Kafka uses corresponding metric names.
                matches = [
                    t
                    for t in totals
                    if key(t) == key(row)
                    and (
                        not selector.startswith("traces_")
                        or t.get("metric") == row.get("metric")
                    )
                ]
                if len(matches) != 1:
                    reasons.append("ERROR_SUBSET_TOTAL_LABEL_MISMATCH")
                    continue
                a, b = row.get("values", []), matches[0].get("values", [])
                if [v[0] for v in a] != [v[0] for v in b]:
                    reasons.append("ERROR_SUBSET_TOTAL_TIME_MISMATCH")
                else:
                    if any(float(x[1]) > float(y[1]) for x, y in zip(a, b)):
                        reasons.append("ERROR_SUBSET_EXCEEDS_TOTAL")
                    if any(
                        float(a[i + 1][1]) - float(a[i][1])
                        > float(b[i + 1][1]) - float(b[i][1])
                        for i in range(len(a) - 1)
                    ):
                        reasons.append("ERROR_SUBSET_INCREMENT_EXCEEDS_TOTAL")
        if selector.split("{", 1)[0].endswith("_bucket"):
            groups = {}
            for row in rows:
                labels = row.get("metric", {})
                key = tuple(sorted((k, v) for k, v in labels.items() if k != "le"))
                groups.setdefault(key, []).append(row)
            for buckets in groups.values():
                try:
                    ordered = sorted(buckets, key=lambda r: float(r["metric"]["le"]))
                    bounds = [float(r["metric"]["le"]) for r in ordered]
                    if (
                        len(bounds) < 2
                        or len(set(bounds)) != len(bounds)
                        or bounds[-1] != math.inf
                        or any(math.isnan(x) for x in bounds)
                    ):
                        raise ValueError()
                    grid = [v[0] for v in ordered[0]["values"]]
                    if any([v[0] for v in r["values"]] != grid for r in ordered):
                        reasons.append("HISTOGRAM_BUCKET_TIME_MISMATCH")
                    else:
                        for a, b in zip(ordered, ordered[1:]):
                            if any(
                                float(x[1]) > float(y[1])
                                for x, y in zip(a["values"], b["values"])
                            ):
                                reasons.append("HISTOGRAM_NONMONOTONIC_BUCKETS")
                            av, bv = a["values"], b["values"]
                            if any(
                                float(av[i + 1][1]) - float(av[i][1])
                                > float(bv[i + 1][1]) - float(bv[i][1])
                                for i in range(len(av) - 1)
                            ):
                                reasons.append("HISTOGRAM_NONMONOTONIC_INCREMENTS")
                except (KeyError, TypeError, ValueError):
                    reasons.append("HISTOGRAM_BUCKET_GROUP_INCOMPLETE")
    return sorted(set(reasons))


def verify_default_evidence(application, read_bytes):
    """Check engineering source bytes only; this does not prove default semantics.

    Formal ingestion verification rejects these manually reviewed declarations.
    """
    import hashlib

    for value in (application or {}).get("versioned_producer_defaults", {}).values():
        for h in value.get("evidence_sha256", []):
            if (
                not isinstance(h, str)
                or not re.fullmatch(r"[0-9a-f]{64}", h)
                or hashlib.sha256(read_bytes(h)).hexdigest() != h
            ):
                raise ValueError("runtime default evidence digest differs")
