import copy
import json
from pathlib import Path

import pytest
from ecomsre.product.errors import ProductError
from ecomsre.product.investigation.semantic_analysis import (
    SemanticAnalysis,
    compare_series,
)
from ecomsre.product.investigation.semantic_contracts import (
    AnalysisRequest,
    Candidate,
    Hypothesis,
    ObservableQuestion,
)
from ecomsre.product.investigation.semantic_policy import (
    Progress,
    contrast,
    investigate_semantic_v1,
)
from scripts.product_v050.evaluate_semantic_investigation import (
    FixtureProvider,
    ResearchLedger,
    load_retained,
    summarize_runs,
)

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def config():
    return json.loads(
        (ROOT / "config/semantic-investigation-v1/experiment.json").read_text()
    )


@pytest.fixture
def snapshot():
    rows = [
        dict(
            record_ref="r1",
            trace_id="t1",
            span_id="s1",
            parent_span_id=None,
            service="a",
            operation="op",
            direction="server",
            start=10,
            end=11,
            duration_ms=100,
            error=False,
        ),
        dict(
            record_ref="r2",
            trace_id="t1",
            span_id="s2",
            parent_span_id="s1",
            service="b",
            operation="call",
            direction="client",
            start=10.5,
            end=11,
            duration_ms=50,
            error=True,
        ),
        dict(
            record_ref="r3",
            trace_id="t2",
            span_id="s3",
            parent_span_id="missing",
            service="a",
            operation="op",
            direction="server",
            start=12,
            end=13,
            duration_ms=None,
            error=None,
        ),
    ]
    return dict(
        records=rows,
        services=["a", "b"],
        windows={"current": [10, 20]},
        references={},
        topology=[["a", "b"]],
        metadata=dict(
            record_count=3, trace_fields=True, coverage="SAMPLE", truncated=True
        ),
    )


def test_profile_denominators_and_duplicate(snapshot, config):
    snapshot["records"].append(copy.deepcopy(snapshot["records"][0]))
    a = SemanticAnalysis(snapshot, config)
    r = a.execute(AnalysisRequest(tool="profile_operations", target="a"))
    row = r["table"][0]
    assert (
        row["count"] == 2
        and row["status_known_count"] == 1
        and row["error_fraction"] == 0
    )
    assert row["unknown_status_count"] == 1 and row["purposes"] == ["UNCLASSIFIED"]
    assert set(r["source_refs"]) == {"r1", "r3"}
    assert (
        a.execute(AnalysisRequest(tool="profile_operations", target="b"))["table"][0][
            "error_count"
        ]
        == 1
    )
    snapshot["records"][-1]["error"] = True
    with pytest.raises(ValueError, match="CONFLICTING_DUPLICATE"):
        SemanticAnalysis(snapshot, config)


def test_reference_math_and_local_missing():
    r = compare_series([3, None, 3], [1, 1, 1], epsilon=0.1, minimum=3, threshold=1)
    assert (
        r["absolute_difference"] == 2
        and r["z"] is None
        and r["consecutive_deviations"] == 1
    )
    assert (
        compare_series([1], [0, 0, 0], epsilon=0.1, minimum=3, threshold=1)[
            "relative_change"
        ]
        is None
    )
    assert (
        compare_series([1], [1, float("nan")], epsilon=0.1, minimum=3, threshold=1)[
            "status"
        ]
        == "INSUFFICIENT_REFERENCE"
    )


def test_baseline_scope_time_and_values(snapshot, config):
    ref = dict(
        fixed_at=9,
        window=[0, 10],
        unit="fraction",
        scope=["a", None, None],
        method="trace_sample",
        values=[0.1, 0.2, 0.3],
        source_refs=["prior1"],
    )
    snapshot["references"]["prior"] = ref
    a = SemanticAnalysis(snapshot, config)
    req = AnalysisRequest(tool="compare_baseline", target="a", reference_id="prior")
    assert a.execute(req)["table"][0]["absolute_difference"] == -0.2
    ref["scope"] = ["a", "another", "server"]
    assert a.execute(req)["table"][0]["status"] == "REFERENCE_SCOPE_MISMATCH"
    ref["fixed_at"] = 11
    assert a.execute(req)["table"][0]["status"] == "REFERENCE_NOT_PRIOR"


def test_dependency_identity_and_missing_parent(snapshot, config):
    result = SemanticAnalysis(snapshot, config).execute(
        AnalysisRequest(tool="compare_dependencies", target="a", neighbors=["b"])
    )
    row = result["table"][0]
    assert row["observed_edges"] == 1 and row["missing_parents"] == 1
    assert row["onset_order"] == "UNKNOWN_CLOCK_AND_SAMPLING_RESOLUTION"
    snapshot["records"][1]["trace_id"] = "other"
    row = SemanticAnalysis(snapshot, config).execute(
        AnalysisRequest(tool="compare_dependencies", target="a", neighbors=["b"])
    )["table"][0]
    assert row["observed_edges"] == 0 and row["missing_parents"] == 2


def test_contrast_preobservation_and_repeat(snapshot, config):
    a = SemanticAnalysis(snapshot, config)
    q = ObservableQuestion(
        field="count",
        row_key="a|op|server",
        comparator="gt",
        threshold=0,
        expectations={"H1": "YES", "H2": "NO", "H3": "UNSPECIFIED"},
    )
    c = Candidate(
        request=AnalysisRequest(tool="profile_operations", target="a"), question=q
    )
    hypotheses = [
        Hypothesis(hypothesis_id=h, explanation=h, target="a")
        for h in ["H1", "H2", "H3"]
    ]
    assert contrast(c, hypotheses, a)["disagreement"] == pytest.approx(1 / 3)
    before = contrast(c, hypotheses, a)
    a.rows[0]["error"] = True
    assert contrast(c, hypotheses, a) == before
    progress = Progress()
    r = a.execute(c.request)
    new, check = progress.update(c, r)
    assert (
        new
        and check["outcome"] == "YES"
        and check["prediction_status"]["H2"] == "CONFLICT"
    )
    assert not progress.update(c, r)[0]
    rewritten = c.model_copy(
        update={"question": q.model_copy(update={"expectations": {"H1": "NO"}})}
    )
    with pytest.raises(ValueError, match="PREDICTION_REWRITTEN"):
        progress.update(rewritten, r)


def test_scope_rejects_execution_and_pagination(snapshot, config):
    with pytest.raises(ValueError):
        AnalysisRequest(tool="shell", target="a")
    a = SemanticAnalysis(snapshot, config)
    with pytest.raises(ValueError, match="UNKNOWN_TARGET"):
        a.execute(AnalysisRequest(tool="read_records", target="/etc/passwd"))
    config["page_size"] = 1
    assert (
        a.execute(AnalysisRequest(tool="read_records", target="a"))["next_offset"] == 1
    )
    assert (
        len(
            a.execute(AnalysisRequest(tool="read_records", target="a", offset=1))[
                "table"
            ]
        )
        == 1
    )


def test_ledger_unknown_failure_and_ceiling(tmp_path):
    ledger = ResearchLedger(tmp_path, limit=100, calls=2)
    ledger.reserve("semantic-investigation-v1:a", {}, 70)
    with pytest.raises(ProductError, match="ceiling"):
        ledger.reserve("semantic-investigation-v1:b", {}, 40)
    ledger.settle("semantic-investigation-v1:a", {}, None, "FAILED")
    assert ledger.entries()["semantic-investigation-v1:a"]["accounted"] == 70
    with pytest.raises(ProductError):
        ledger.reserve("semantic-investigation-v1:a", {}, 1)
    ledger.reserve("semantic-investigation-v1:b", {}, 30)
    ledger.settle("semantic-investigation-v1:b", {}, 10, "COMPLETED")
    with pytest.raises(ProductError):
        ledger.reserve("semantic-investigation-v1:c", {}, 1)


def test_fixture_end_to_end_independent_runs(snapshot, config):
    a = SemanticAnalysis(snapshot, config)
    states = [
        investigate_semantic_v1(a, FixtureProvider(), m, f"run-{m}", config)
        for m in "ABCD"
    ]
    assert all(s["status"] == "COMPLETED" for s in states)
    assert len({s["run_id"] for s in states}) == 4
    assert states[0]["observations"] and not states[0]["analysis_results"]
    assert all(s["analysis_results"] for s in states[1:])
    assert states[1]["analysis_actions"] == 6
    assert states[1]["provider_calls"] == 1


def test_scoring_repeats_do_not_expand_denominator():
    def run(m, i, roots):
        return dict(
            method=m,
            case_id="c",
            status="COMPLETED",
            provider_calls=1,
            analysis_actions=1,
            queries=1,
            records_scanned=1,
            report=dict(ranked_components=roots),
        )

    runs = [run(m, i, ["a"]) for m in "ABCD" for i in range(2)]
    summary = summarize_runs(
        runs, {"c": dict(root="a", event_group="e", insufficient=False)}
    )
    assert summary["methods"]["D"]["independent_root_events"] == 1
    assert summary["contrasts"]["D-C"]["paired_bootstrap_95"] is None


def test_real_retained_pure_computation(config):
    root = ROOT / ".local/engineering-calibration/live-03"
    if not root.exists():
        pytest.skip("Private retained source not present; not fixture evidence.")
    snapshot, manifest = load_retained(root, 2, config)
    assert manifest["files"] and snapshot["metadata"]["record_count"] > 0
    a = SemanticAnalysis(snapshot, config)
    result = a.execute(
        AnalysisRequest(tool="profile_operations", target="payment", group_by="purpose")
    )
    assert any(
        "CONTROL_STREAM" in r["purposes"] and r["error_count"] > 0
        for r in result["table"]
    )
    assert all(r["record_ref"].startswith("rec-") for r in snapshot["records"])
    assert not any(k in snapshot for k in ("root", "labels", "diagnosis"))


def test_raw_counter_filter_and_exposed_prediction(snapshot, config):
    snapshot["windows"]["current"] = [0, 300]
    arow = dict(
        metric=dict(
            service_name="a",
            span_name="op",
            span_kind="SPAN_KIND_SERVER",
            status_code="STATUS_CODE_OK",
        ),
        values=[[-1, "1"], [1, "2"], [299, "3"], [301, "4"]],
    )
    other = copy.deepcopy(arow)
    other["metric"]["span_name"] = "other"
    snapshot["counters"] = {"a": dict(rows=[arow, other], buckets=[arow, other])}
    a = SemanticAnalysis(snapshot, config)
    raw = a.execute(
        AnalysisRequest(
            tool="read_records", target="a", source="counters", operation="op"
        )
    )
    assert len(raw["table"]) == 2
    assert all(r["values"] == [[1, "2"], [299, "3"]] for r in raw["table"])
    q = ObservableQuestion(
        field="count",
        row_key="a|op|server",
        comparator="gt",
        threshold=0,
        expectations={"H1": "YES"},
    )
    req = AnalysisRequest(tool="profile_operations", target="a")
    p = Progress()
    p.update(Candidate(request=req), a.execute(req))
    changed = req.model_copy(update={"detail_level": "representative_records"})
    assert not p.update(Candidate(request=changed, question=q), a.execute(changed))[1][
        "precommitted"
    ]
