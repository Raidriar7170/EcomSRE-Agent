"""Attribution is measured increments, never synthetic healthy support."""

import pytest
from scripts.product_v050.health_semantics_replay import (
    increments,
    call_contributions,
    bucket_contributions,
    replay,
)


def row(name="EventStream", status="STATUS_CODE_ERROR", values=None, le=None):
    labels = dict(span_name=name, status_code=status, service_name="payment")
    if le is not None:
        labels["le"] = le
    return dict(metric=labels, values=values or [[10, "0"], [20, "1"]])


def test_eventstream_contributes_to_total_and_error_not_ignored():
    p = call_contributions([row(), row("Business", "STATUS_CODE_UNSET")], [20])[0]
    assert p["total"] == 2 and p["errors"] == p["eventstream_errors"] == 1
    assert p["error_fraction"] == p["eventstream_fraction"] == 0.5


@pytest.mark.parametrize(
    "values", [[[20, "1"]], [[10, "2"], [20, "1"]], [[10, "NaN"], [20, "1"]]]
)
def test_missing_birth_reset_nonfinite_never_become_zero(values):
    with pytest.raises(ValueError, match="insufficient or reset"):
        increments([row(values=values)], 20)


def test_incomparable_grids_and_zero_total_are_unknown():
    p = call_contributions([row(), row(values=[[11, "0"], [20, "1"]])], [20])[0]
    assert p["status"] == "UNKNOWN_INCOMPARABLE_SAMPLE_GRIDS"
    p = call_contributions([row(values=[[10, "1"], [20, "1"]])], [20])[0]
    assert p["error_fraction"] is None and p["total"] == 0


def test_overflow_matches_labels_and_cannot_be_negative():
    rows = [row(le="+Inf"), row(le="15000", values=[[10, "0"], [20, "0"]])]
    assert bucket_contributions(rows, [20])[0]["eventstream_above_15000ms"] == 1
    rows[1]["metric"]["span_name"] = "Other"
    with pytest.raises(ValueError, match="correspondence"):
        bucket_contributions(rows, [20])


def test_output_cannot_replace_input_or_existing_results(tmp_path):
    for output in [tmp_path, tmp_path / "child"]:
        with pytest.raises(ValueError, match="fresh separate"):
            replay(tmp_path, 3, output)
