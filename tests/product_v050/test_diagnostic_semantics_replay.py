"""Counterexamples for the opt-in offline semantics experiment."""

import copy
import json
from pathlib import Path

import pytest
from scripts.product_v050 import diagnostic_semantics_replay as ds

SPEC = json.loads(
    Path("config/product-v050/diagnostic-semantics-experiment-v1.json").read_text()
)


def calls(
    operation="grpc.flagd.evaluation.v2.Service/EventStream",
    kind="SPAN_KIND_CLIENT",
    status="STATUS_CODE_ERROR",
    values=None,
):
    return dict(
        metric=dict(
            __name__="calls",
            service_name="payment",
            service_instance_id="instance",
            span_name=operation,
            span_kind=kind,
            status_code=status,
        ),
        values=values or [[100, "2"], [110, "3"]],
    )


def buckets(rows):
    return [
        dict(
            metric=dict(r["metric"], __name__="buckets", le=le),
            values=copy.deepcopy(r["values"]),
        )
        for r in rows
        for le in ("15000", "+Inf")
    ]


def span():
    return dict(
        trace_id="trace",
        span_id="span",
        service="payment",
        instance="instance",
        operation="grpc.flagd.evaluation.v2.Service/EventStream",
        direction="client",
        start=0,
        end=105,
        duration_us=105000000,
        references=[],
        rpc_system="grpc",
        rpc_service="flagd.evaluation.v2.Service",
        rpc_method="EventStream",
        status="ERROR",
        grpc_status=4,
        captures=[
            dict(
                received_at="1970-01-01T00:02:00+00:00",
                truncated=False,
                limit_reached=False,
            )
        ],
    )


def associate(s, rows=None):
    return ds.associate(
        s, rows or [calls()], dict(start=100, end=120), [110, 120], 115, set()
    )


def test_control_exclusion_cannot_remove_business_error_or_only_numerator():
    rows = [calls(), calls("grpc.oteldemo.PaymentService/Charge", "SPAN_KIND_SERVER")]
    result = ds.grouped_point(rows, buckets(rows), 120, SPEC)
    by = {p["group"]: p for p in result}
    assert by["BUSINESS_REQUEST"]["errors"] == by["BUSINESS_REQUEST"]["total"] == 1
    assert by["CONTROL_STREAM"]["errors"] == by["CONTROL_STREAM"]["total"] == 1
    assert ds.contribution(result)["control_error_fraction"] == 0.5
    assert all(p["error_fraction"] == 1 for p in result)


@pytest.mark.parametrize(
    "status", ["STATUS_CODE_ERROR", "STATUS_CODE_UNSET", "STATUS_CODE_OK"]
)
def test_purpose_does_not_depend_on_result(status):
    assert ds.classify(calls(status=status)["metric"], SPEC) == "CONTROL_STREAM"


def test_unknown_and_async_directions():
    assert ds.classify(calls("new/EventStream")["metric"], SPEC) == "UNCLASSIFIED"
    assert (
        ds.classify({"span_name": "flagd.evaluation.v2.Service/EventStream"}, SPEC)
        == "UNCLASSIFIED"
    )
    assert (
        ds.classify(
            dict(
                service_name="fraud-detection",
                span_name="orders process",
                span_kind="SPAN_KIND_CONSUMER",
            ),
            SPEC,
        )
        == "BUSINESS_ASYNC"
    )
    assert ds.classify(calls(kind="SPAN_KIND_SERVER")["metric"], SPEC) == "UNCLASSIFIED"


@pytest.mark.parametrize(
    "values", [[[100, "2"], [110, "NaN"]], [[100, "3"], [110, "1"]], [[110, "1"]]]
)
def test_nan_reset_birth_missing_denominator_never_healthy(values):
    rows = [calls(values=values)]
    p = ds.grouped_point(rows, buckets(rows), 120, SPEC)[0]
    assert p["status"] == "NOT_COMPUTABLE"
    assert not p["baseline_eligible"] and p["p95_ms"] is None


def test_zero_traffic_and_missing_buckets_do_not_produce_quantile():
    rows = [calls(values=[[100, "2"], [110, "2"]])]
    p = ds.grouped_point(rows, buckets(rows), 120, SPEC)[0]
    assert (
        p["status"] == "ZERO_OBSERVED_TRAFFIC_NOT_HEALTH"
        and p["error_fraction"] is None
    )
    p = ds.grouped_point(rows, [], 120, SPEC)[0]
    assert p["status"] == "NOT_COMPUTABLE" and p["p95_ms"] is None


def test_p95_is_never_subtracted_and_overflow_is_count_difference():
    rows = [calls()]
    bs = buckets(rows)
    bs[0]["values"] = [[100, "1"], [110, "1"]]
    p = ds.grouped_point(rows, bs, 120, SPEC)[0]
    assert p["above_15000ms"] == 1 and p["p95_ms"] is None
    assert p["p95_status"] == "NOT_COMPUTABLE_NO_VALIDATED_BUCKET_RATES"


def test_misaligned_status_grids_and_bucket_population_fail_closed():
    rows = [calls(), calls(status="STATUS_CODE_UNSET", values=[[101, "1"], [111, "2"]])]
    assert (
        ds.grouped_point(rows, buckets(rows), 120, SPEC)[0]["status"]
        == "NOT_COMPUTABLE"
    )
    rows = [calls()]
    bs = buckets(rows)
    bs[1]["metric"]["service_instance_id"] = "other"
    assert ds.grouped_point(rows, bs, 120, SPEC)[0]["status"] == "NOT_COMPUTABLE"


def test_pre_window_span_completion_and_late_capture_are_separate():
    out = associate(span())
    assert out["association_types"] == ["END_IN_SUPPORT_WINDOW", "OVERLAPS_WINDOW"]
    assert len(out["counter_intervals"]) == 1
    assert out["original_start_query_excluded"]
    assert out["online_availability"] == "ONLINE_AVAILABILITY_UNPROVEN"
    assert out["capture_after_cutoff"] and out["causal_root"] == "UNPROVEN"
    assert out["start"] == 0


@pytest.mark.parametrize(
    "field,value",
    [
        ("instance", "other"),
        ("instance", None),
        ("operation", "other"),
        ("rpc_system", "http"),
    ],
)
def test_time_coincidence_without_identity_or_rpc_semantics_not_attribution(
    field, value
):
    s = span()
    s[field] = value
    out = associate(s)
    assert out["counter_intervals"] == [] and out["shared_support_candidates"] == []


def test_export_delay_is_not_exact_completion_interval():
    s = span()
    s["end"] = 95
    out = associate(s)
    assert not out["counter_intervals"] and out["shared_support_candidates"]
    assert out["match_strength"] == "INSTANCE_OPERATION_SHARED_SUPPORT_NOT_CAUSAL"


def test_duplicates_truncation_and_parent_identity_retained():
    s = span()
    s["captures"][0]["truncated"] = True
    combined = ds.deduplicate([s, copy.deepcopy(s)])
    assert len(combined) == 1 and len(combined[0]["captures"]) == 2
    assert associate(combined[0])["truncation_present"]
    bad = copy.deepcopy(s)
    bad["end"] = 106
    with pytest.raises(ValueError, match="conflicting duplicate"):
        ds.deduplicate([s, bad])


def test_empty_or_unknown_status_has_no_healthy_ratio():
    assert ds.contribution([])["fraction"] is None
    r = calls(status="NEW_STATUS")
    p = ds.grouped_point([r], buckets([r]), 120, SPEC)[0]
    assert p["errors"] is None and p["error_fraction"] is None


def test_no_default_product_import_or_config_activation():
    for root in [Path("src/ecomsre"), Path("config/product")]:
        if root.exists():
            assert not any(
                "diagnostic_semantics_replay" in p.read_text()
                or "diagnostic-semantics-experiment-v1" in p.read_text()
                for p in root.rglob("*")
                if p.suffix in (".py", ".json")
            )


def test_real_retained_chain_is_repeatable_and_preserves_resource_predicates(tmp_path):
    root = Path(".local/engineering-calibration/live-03")
    if not root.exists():
        pytest.skip(
            "Private retained live-03 inputs unavailable; numerical/association counterexamples run independently"
        )
    data = ds.replay(
        root,
        [2, 3],
        "config/product-v050/diagnostic-semantics-experiment-v1.json",
        tmp_path / "first",
    )
    again = ds.replay(
        root,
        [2, 3],
        "config/product-v050/diagnostic-semantics-experiment-v1.json",
        tmp_path / "second",
    )
    assert data == again
    for name in ("comparison.json", "public-comparison.json"):
        assert (tmp_path / "first" / name).read_bytes() == (
            tmp_path / "second" / name
        ).read_bytes()
    for w in data["windows"]:
        assert w["A"]["diagnosis"]["core_or_extension_or_open_world"] == "ABSTAIN"
        assert w["A"]["health"]["accepted"] is False
        assert any(
            p["predicate_kind"] == "RESOURCE_MEMORY_GROWTH_STRONG"
            for p in w["B"]["unchanged_non_rpc_predicates"]
        )
        assert w["B"]["qualification"] == w["C"]["qualification"] == "NOT_ISSUED"
        assert w["B"]["predicate_changes"] is None
        assert w["C"]["metric_view_sha256"] == ds.sha(
            json.dumps(w["B"]["metrics"], sort_keys=True).encode()
        )
        assert w["C"]["new_control_support_associations"] == 2
        assert all(
            p["baseline_status"] == "BASELINE_SCOPE_UNAVAILABLE_FOR_EXACT_RATE_PROFILE"
            for p in w["B"]["metrics"].values()
        )
    with pytest.raises(ValueError, match="fresh separate"):
        ds.replay(
            root,
            [2, 3],
            "config/product-v050/diagnostic-semantics-experiment-v1.json",
            tmp_path / "first",
        )


def test_unknown_control_errors_stay_unknown_in_summary():
    r = calls(status="UNKNOWN")
    out = ds.contribution(ds.grouped_point([r], buckets([r]), 120, SPEC))
    assert out["control_errors"] is None and out["control_error_fraction"] is None


@pytest.mark.parametrize("status", ["OK", "UNSET", None])
def test_non_error_span_cannot_explain_error_counter(status):
    s = span()
    s["status"] = status
    assert not associate(s)["shared_support_candidates"]


def test_capture_before_cutoff_with_wrong_instance_still_unproven():
    s = span()
    s["instance"] = "wrong"
    s["captures"][0]["received_at"] = "1970-01-01T00:01:50+00:00"
    assert associate(s)["online_availability"] == "ONLINE_AVAILABILITY_UNPROVEN"


@pytest.mark.parametrize("built_at", [100, 101])
def test_check_cannot_build_its_own_baseline(built_at):
    with pytest.raises(ValueError, match="BASELINE_NOT_FIXED"):
        ds.require_prior_baseline(built_at, 100)


def test_joint_original_extra_dedup_retains_earliest_capture():
    late = span()
    early = copy.deepcopy(late)
    early["captures"][0]["received_at"] = "1970-01-01T00:01:50+00:00"
    merged = ds.deduplicate([early, late])[0]
    result = associate(merged)
    assert result["online_availability"] == "AVAILABLE_BY_CAPTURE_CUTOFF"
    assert not result["capture_after_cutoff"]


def test_invalid_counter_grid_is_not_repaired_silently():
    r = calls(values=[[110, "2"], [100, "3"]])
    assert (
        ds.grouped_point([r], buckets([r]), 120, SPEC)[0]["status"] == "NOT_COMPUTABLE"
    )
