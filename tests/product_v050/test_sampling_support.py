"""Counterexamples for producer support; all inputs here are synthetic."""

from copy import deepcopy
import json

import pytest
from test_ingestion_evidence import FIXTURE
from scripts.product_v050 import ingestion_evidence as ie
from scripts.product_v050 import sampling_support as ss

S = 'kafka_request_count_total{service_name="kafka"}'
APP = {"effective_environments": {"kafka": {"OTEL_METRIC_EXPORT_INTERVAL": "60000"}}}


def body(times, name="kafka_request_count_total", **labels):
    return {
        "status": "success",
        "data": {
            "resultType": "matrix",
            "result": [
                {
                    "metric": {"__name__": name, "service_name": "kafka"} | labels,
                    "values": [[t, str(i)] for i, t in enumerate(times)],
                }
            ],
        },
    }


def assess(payload, *, application=APP, start=0, end=600, inner=300):
    return ss.assess(
        payload,
        S,
        start,
        end,
        ss.profile(FIXTURE["collector"], S, application),
        query_start=start + inner,
        inner_seconds=inner,
    )


def test_declared_sixty_second_cycle_is_not_thirty_second_outage():
    payload = body(range(0, 601, 60))
    assert ie.sample_state(payload, S, 0, 600, 30) == "WINDOW_INCOMPLETE"
    result = assess(payload)
    assert result["state"] == "FRESH_COVERED"
    assert result["series"][0]["observed_median_period_seconds"] == 60
    assert assess(payload, application=None)["reasons"] == ["PRODUCER_PERIOD_UNPROVEN"]


@pytest.mark.parametrize(
    "times,reason",
    [
        (
            [0, 60, 120, 180, 300, 360, 420, 480, 540, 600],
            "ESTABLISHED_SERIES_INTERRUPTION",
        ),
        (list(range(0, 481, 60)), "STALE_LAST_SAMPLE"),
        (list(range(120, 601, 60)), "LEFT_SUPPORT_MISSING"),
    ],
)
def test_independent_temporal_requirements(times, reason):
    result = assess(body(times))
    assert reason in result["reasons"]
    if reason == "ESTABLISHED_SERIES_INTERRUPTION":
        assert result["series"][0]["last_age_seconds"] == 0
        assert len(result["series"][0]["effective_support_intervals"]) == 2


def test_computable_query_is_not_full_inner_support():
    result = assess(body(range(0, 601, 60)), inner=60)
    assert "INNER_WINDOW_INSUFFICIENT_SAMPLES" in result["reasons"]
    assert result["series"][0]["sample_count"] == 11


def test_new_label_not_zero_filled_and_reset_not_assumed_birth():
    payload = body(range(0, 601, 60))
    late = body(range(240, 601, 60), instance="new")["data"]["result"][0]
    payload["data"]["result"].append(late)
    result = assess(payload)
    assert "BIRTH_OR_RESTART_UNKNOWN" in result["series"][1]["lifecycle"]
    assert result["series"][1]["first_sample"] == 240
    payload = body(range(0, 601, 60))
    payload["data"]["result"][0]["values"][5][1] = "0"
    result = assess(payload)
    assert result["series"][0]["counter_resets"] == [300]
    assert "COUNTER_RESET_IN_SUPPORT" in result["reasons"]


def test_error_subset_total_correspondence():
    failed = S.replace("count", "failed")
    numerator = body([0, 60], name="kafka_request_failed_total", instance="a")
    total = body([0, 60], instance="b")
    assert ss.correspondence({failed: numerator, S: total}) == [
        "ERROR_SUBSET_TOTAL_LABEL_MISMATCH"
    ]
    total["data"]["result"][0]["metric"]["instance"] = "a"
    numerator["data"]["result"][0]["values"][1][1] = "9"
    assert "ERROR_SUBSET_EXCEEDS_TOTAL" in ss.correspondence(
        {failed: numerator, S: total}
    )
    total["data"]["result"][0]["values"][1][0] = 61
    assert ss.correspondence({failed: numerator, S: total}) == [
        "ERROR_SUBSET_TOTAL_TIME_MISMATCH"
    ]


@pytest.mark.parametrize("problem", ["missing_inf", "time", "values", "labels"])
def test_histogram_correspondence(problem):
    metric = "traces_span_metrics_duration_milliseconds_bucket"
    selector = metric + '{service_name="kafka"}'
    small = body([0, 5], name=metric, le="1")["data"]["result"][0]
    large = body([0, 5], name=metric, le="+Inf")["data"]["result"][0]
    payload = body([0, 5], name=metric)
    payload["data"]["result"] = [small, large]
    assert ss.correspondence({selector: payload}) == []
    if problem == "missing_inf":
        large["metric"]["le"] = "10"
    elif problem == "time":
        large["values"][1][0] = 6
    elif problem == "labels":
        large["metric"]["instance"] = "other"
    else:
        small["values"][1][1] = "2"
    assert ss.correspondence({selector: payload})


def test_receipt_recalculates_even_if_passed_flag_is_forged():
    query = f"sum(rate({S}[5m]))"
    queries = {"prometheus:request_support:kafka": query}
    objects = {}

    def put(value):
        import hashlib

        raw = json.dumps(value).encode()
        digest = hashlib.sha256(raw).hexdigest()
        objects[digest] = raw
        return digest

    binding = ie.topology_v3(
        FIXTURE["collector"],
        FIXTURE["prometheus_command"],
        queries,
        application_object_sha256=put(APP),
    )
    req = dict(query_key=next(iter(queries)), query=query, start=300, end=600)
    _, _, params = ie.expected_reads(query, 300, 600)[0]
    digest = put(body(range(0, 601, 60)))
    entry = dict(
        params=params,
        action_context=dict(
            ingestion_version=ss.VERSION,
            incident_id="i",
            binding_sha256=binding["sha256"],
        ),
        occurrence="fixture",
        truncated=False,
        method="GET",
        status_code=200,
        url="http://localhost/api/v1/query",
        requested_at="2026-09-27T00:00:00+00:00",
        received_at="2026-09-27T00:00:00+00:00",
        response_object_sha256=digest,
    )
    kwargs = dict(
        binding=binding,
        entries=[entry],
        occurrence="fixture",
        incident_id="i",
        queries=queries,
        read_bytes=objects.__getitem__,
        requirements=[req],
    )
    assessment = ie.verify(
        **kwargs, collector=FIXTURE["collector"], command=FIXTURE["prometheus_command"]
    )
    assert assessment["passed"]
    receipt = dict(
        version=ss.VERSION,
        assessment=assessment,
        collector_object_sha256=put(FIXTURE["collector"]),
        prometheus_command_object_sha256=put(FIXTURE["prometheus_command"]),
    )
    assert ie.verify_receipt(receipt, **kwargs)["passed"]
    bad = deepcopy(body(range(0, 601, 60)))
    del bad["data"]["result"][0]["values"][4]
    entry["response_object_sha256"] = put(bad)
    with pytest.raises(ValueError, match="not established"):
        ie.verify_receipt(receipt, **kwargs)


def test_cumulative_order_does_not_prove_rate_order():
    total = body(range(0, 301, 60))
    failed = body(range(0, 301, 60), name="kafka_request_failed_total")
    total["data"]["result"][0]["values"] = [[i * 60, str(100 + i)] for i in range(6)]
    failed["data"]["result"][0]["values"] = [[i * 60, str(i * 5)] for i in range(6)]
    assert "ERROR_SUBSET_INCREMENT_EXCEEDS_TOTAL" in ss.correspondence(
        {S: total, S.replace("count", "failed"): failed}
    )
    metric = "traces_span_metrics_duration_milliseconds_bucket"
    a, b = deepcopy(failed["data"]["result"][0]), deepcopy(total["data"]["result"][0])
    a["metric"].update(__name__=metric, le="1")
    b["metric"].update(__name__=metric, le="+Inf")
    total["data"]["result"] = [a, b]
    assert "HISTOGRAM_NONMONOTONIC_INCREMENTS" in ss.correspondence(
        {metric + '{service_name="kafka"}': total}
    )


def test_mixed_lookbacks_are_rejected_before_collection():
    q = f"sum(rate({S}[1m])) / sum(rate({S}[5m]))"
    with pytest.raises(ValueError, match="mixed selector lookbacks"):
        ie.topology_v3(
            FIXTURE["collector"],
            FIXTURE["prometheus_command"],
            {"prometheus:request_support:kafka": q},
        )


@pytest.mark.parametrize(
    "query", [f"rate({S}[1h])", f"rate({S}[5m:10s])", f"rate({S}[5m] offset 1m)"]
)
def test_unsupported_temporal_syntax_cannot_silently_shorten_support(query):
    with pytest.raises(ValueError, match="unsupported temporal"):
        ie.topology_v3(
            FIXTURE["collector"],
            FIXTURE["prometheus_command"],
            {"prometheus:request_support:kafka": query},
        )


def test_whole_service_short_support_is_not_assumed_label_birth():
    result = assess(body(range(240, 601, 60), instance="i"))
    assert (
        result["lifecycle_scope"] == "SERVICE_STARTUP_OR_COMMON_SOURCE_GAP_UNRESOLVED"
    )
    assert "LEFT_SUPPORT_MISSING" in result["reasons"]
    payload = body(range(0, 601, 60), instance="i", operation="old")
    payload["data"]["result"].extend(
        body(range(240, 601, 60), instance="i", operation="new")["data"]["result"]
    )
    result = assess(payload)
    assert (
        result["series"][1]["lifecycle"]
        == "NEW_LABEL_ON_OBSERVED_INSTANCE_BIRTH_UNPROVEN"
    )
    assert result["lifecycle_scope"] == "PER_SERIES_OBSERVATIONS_ONLY"


def test_instant_query_requires_predecessor_and_v3_acquisition_retains_it():
    selector = 'kafka_consumer_group_lag{group="checkout"}'
    query = f"sum({selector})"
    historical = ie.expected_reads(query, 1000, 1300)
    modern = ie.expected_reads(query, 1000, 1300, version=ss.VERSION)
    assert historical[0][2]["query"].endswith("[301s]")
    assert modern[0][2]["query"].endswith("[601s]")
    assert modern[0][1] == 1000  # prefix is not a longer business window
    policy = ss.profile(FIXTURE["collector"], selector)

    def check(times):
        payload = body(times, name="kafka_consumer_group_lag", group="checkout")
        return ss.assess(
            payload, selector, 1000, 1300, policy, query_start=1000, inner_seconds=0
        )

    late = check(range(1010, 1301, 10))
    assert late["state"] == "UNKNOWN"
    assert late["series"][0]["unsupported_evaluation_times"] == [1000]
    assert "INSTANT_EVALUATION_SAMPLE_MISSING" in late["reasons"]
    assert check(range(990, 1301, 10))["state"] == "FRESH_COVERED"
    # Ancient prefix gaps do not impair current support, but stale predecessor does.
    assert check([701, 710] + list(range(990, 1301, 10)))["state"] == "FRESH_COVERED"
    assert check([980] + list(range(1010, 1301, 10)))["state"] == "UNKNOWN"


def test_histogram_binds_complete_configured_bucket_set():
    collector = deepcopy(FIXTURE["collector"])
    collector["connectors"]["span_metrics"]["histogram"] = {
        "explicit": {"buckets": ["1ms", "0.005s", "10ms"]}
    }
    name = "traces_span_metrics_duration_milliseconds_bucket"
    selector = name + '{service_name="kafka"}'
    payload = body(range(0, 601, 5), name=name)
    payload["data"]["result"] = [
        body(range(0, 601, 5), name=name, le=le)["data"]["result"][0]
        for le in ["1", "5", "10", "+Inf"]
    ]
    policy = ss.profile(collector, selector)
    assert policy["expected_histogram_bounds"] == [1, 5, 10, "+Inf"]

    def check(p):
        return ss.assess(
            payload, selector, 0, 600, p, query_start=300, inner_seconds=300
        )

    assert check(policy)["state"] == "FRESH_COVERED"
    del payload["data"]["result"][1]
    assert "HISTOGRAM_CONFIGURED_BUCKETS_MISSING_OR_EXTRA" in check(policy)["reasons"]
    assert (
        "HISTOGRAM_EXPECTED_BUCKETS_UNPROVEN"
        in check(ss.profile(FIXTURE["collector"], selector))["reasons"]
    )


def test_version_bound_defaults_are_distinct_from_explicit_environment():
    common = dict(
        basis="RUNTIME_VERSION_BOUND_DEFAULT",
        runtime_version="fixture",
        runtime_image_id="sha256:" + "a" * 64,
        evidence_sha256=["a" * 64, "b" * 64],
        overrides_absent=True,
    )
    app = {
        "versioned_producer_defaults": {
            "kafka": dict(common, period_seconds=60),
            "span_metrics": dict(
                common,
                collector_sha256=ie.sha(FIXTURE["collector"]),
                histogram_bounds_milliseconds=[2, 4, 8],
            ),
        }
    }
    p = ss.profile(FIXTURE["collector"], S, app)
    assert (
        p["declared_period_seconds"] == 60
        and p["period_source"] == "runtime_version_bound_default"
    )
    selector = 'traces_span_metrics_duration_milliseconds_bucket{service_name="kafka"}'
    assert ss.profile(FIXTURE["collector"], selector, app)[
        "expected_histogram_bounds"
    ] == [2, 4, 8, "+Inf"]
    broken = deepcopy(app)
    broken["versioned_producer_defaults"]["kafka"]["overrides_absent"] = False
    with pytest.raises(ValueError, match="evidence"):
        ss.profile(FIXTURE["collector"], S, broken)
    broken = deepcopy(app)
    broken["versioned_producer_defaults"]["span_metrics"]["collector_sha256"] = "f" * 64
    with pytest.raises(ValueError, match="binding"):
        ss.profile(FIXTURE["collector"], selector, broken)


def test_default_proof_objects_are_reverified():
    import hashlib

    raw = b"fixed runtime proof"
    h = hashlib.sha256(raw).hexdigest()
    app = {"versioned_producer_defaults": {"kafka": {"evidence_sha256": [h]}}}
    ss.verify_default_evidence(app, lambda _: raw)
    with pytest.raises(ValueError, match="digest"):
        ss.verify_default_evidence(app, lambda _: b"changed")
