import copy
import json

import pytest
from pydantic import ValidationError

import test_semantic_investigation as fixtures
from ecomsre.product.investigation.semantic_analysis import SemanticAnalysis
from ecomsre.product.investigation.semantic_contracts import AnalysisRequest
from ecomsre.product.investigation.semantic_representation import (
    normalize_status,
    status_stats,
    window_stats,
    changes,
    select_reference,
    alert_ranking,
)
from ecomsre.product.investigation.semantic_lite import (
    TurnDecision,
    ReportDecision,
    bound_schema,
    investigate_semantic_lite,
)
from ecomsre.product.knowledge.drafts_v050 import strict_schema

snapshot = fixtures.snapshot
config = fixtures.config


def rows(codes, durations=None):
    return [
        dict(
            normalize_status(code),
            trace_id=str(i),
            duration_ms=(durations or [1] * len(codes))[i],
        )
        for i, code in enumerate(codes)
    ]


@pytest.mark.parametrize(
    "codes,marker,explicit,coverage",
    [
        ([0] * 99 + [2], 0.01, 1, 1),
        ([0] * 100, 0, None, 1),
        ([None] * 100, None, None, 0),
        ([0, 1, 2, None], 1 / 3, 0.5, 0.75),
    ],
)
def test_status_denominators(codes, marker, explicit, coverage):
    s = status_stats(rows(codes))
    assert s["error_marker_fraction"] == marker
    assert s["explicit_status_error_fraction"] == explicit
    assert s["status_coverage"] == coverage
    assert sum(
        s[k] for k in ("n_unset", "n_ok", "n_error", "n_missing_or_invalid")
    ) == len(codes)
    assert normalize_status(False)["normalized_status"] == "MISSING_OR_INVALID"
    assert normalize_status("0")["normalized_status"] == "UNSET"


def reference(signal="duration_ms", **changes):
    return dict(
        dict(
            signal=signal,
            scope=["a", None, None],
            unit="ms" if signal == "duration_ms" else "fraction",
            method="trace_sample",
            fixed_at=9,
            window=[0, 10],
            source_windows=[[-20, -10], [-10, 0], [0, 10]],
            values=[1, 2, 3],
            source_refs=[],
        ),
        **changes,
    )


def test_reference_selection_is_semantic_and_order_independent(snapshot):
    for order in (False, True):
        refs = [
            ("a-duration", reference()),
            ("z-errors", reference("error_marker_fraction")),
            ("explicit", reference("explicit_status_error_fraction")),
        ]
        snapshot["references"] = dict(reversed(refs) if order else refs)
        for signal, expected in [
            ("duration_ms", "a-duration"),
            ("error_marker_fraction", "z-errors"),
        ]:
            req = AnalysisRequest(tool="compare_baseline", target="a", signal=signal)
            assert select_reference(snapshot, req)[0] == expected
        snapshot["references"]["duplicate"] = reference()
        assert (
            select_reference(
                snapshot, req.model_copy(update={"signal": "duration_ms"})
            )[2]
            == "AMBIGUOUS_REFERENCE"
        )


@pytest.mark.parametrize(
    "mutation,reason",
    [
        ({"source_windows": [[-20, -10], [-10, 0], [11, 21]]}, "REFERENCE_NOT_PRIOR"),
        ({"scope": ["a", "op", "client"]}, "REFERENCE_SCOPE_MISMATCH"),
        ({"unit": "fraction"}, "REFERENCE_SCOPE_MISMATCH"),
        (
            {"source_windows": [[-20, -11], [-10, 0], [0, 10]]},
            "REFERENCE_WINDOW_DEFINITION_MISMATCH",
        ),
    ],
)
def test_incompatible_reference_is_not_comparable(snapshot, mutation, reason):
    snapshot["references"] = {"r": reference(**mutation)}
    req = AnalysisRequest(
        tool="compare_baseline", target="a", signal="duration_ms", reference_id="r"
    )
    assert select_reference(snapshot, req)[2] == reason
    snapshot["references"]["r"].pop("signal")
    assert select_reference(snapshot, req)[0] is None


def repaired_config(config):
    return dict(
        config,
        representation_version="trace-representation-repair-v1",
        reference_view="operation",
        representation_support=dict(
            median_spans=5, reference_windows=2, robust_windows=3, tail_spans=40
        ),
    )


def test_continuous_change_support_and_zero_scale(config):
    config = repaired_config(config)
    current = window_stats(rows([0] * 40, [0.003] * 40))
    refs = [window_stats(rows([0] * 40, [v] * 40)) for v in [0.001, 0.002, 0.003]]
    out = changes(current, refs, config)
    assert out["reference_center"] == 0.002
    assert out["absolute_difference"] == 0.001
    assert out["z"] == pytest.approx(0.001 / (1.4826 * 0.001))
    assert out["p95_absolute_difference"] == 0.001
    zero = changes(current, [refs[0]] * 3, config)
    assert zero["z"] is None and zero["absolute_difference"] == 0.002
    low = changes(window_stats(rows([0] * 4)), refs, config)
    assert (
        low["status"] == "INSUFFICIENT_SAMPLE_SUPPORT"
        and low["absolute_difference"] is None
    )


def test_deduplicate_status_and_dependency_signal(snapshot, config):
    config = repaired_config(config)
    for r in snapshot["records"]:
        r.update(normalize_status(0))
    snapshot["records"].append(copy.deepcopy(snapshot["records"][0]))
    a = SemanticAnalysis(snapshot, config)
    result = a.execute(AnalysisRequest(tool="profile_operations", target="a"))
    assert sum(r["n_total"] for r in result["table"]) == 2
    snapshot["records"][-1]["raw_status_code"] = 2
    with pytest.raises(ValueError, match="CONFLICTING_DUPLICATE_SPAN"):
        SemanticAnalysis(snapshot, config)


def test_dynamic_schema_report_only_and_empty_values():
    view = dict(
        services=["a", "b"],
        reference_handles={"O1": "obs"},
        questions=[dict(question_id="Q1")],
    )
    schema = bound_schema(strict_schema(TurnDecision), view)
    assert schema["$defs"]["HypothesisDelta"]["properties"]["target"]["enum"] == [
        "a",
        "b",
    ]
    assert schema["$defs"]["AnalyzeDecision"]["properties"]["question_id"]["enum"] == [
        "Q1"
    ]
    assert "hypothesis_updates" not in schema["$defs"]["ReportDecision"]["properties"]
    empty = bound_schema(
        strict_schema(ReportDecision), dict(view, reference_handles={}, questions=[])
    )
    assert empty["$defs"]["LiteReport"]["properties"]["references"]["maxItems"] == 0
    assert '"enum": []' not in json.dumps(empty)
    with pytest.raises(ValidationError):
        ReportDecision.model_validate(
            dict(action="report", rationale="", hypothesis_updates=[], report={})
        )


def test_fake_loop_hides_executed_actions_retains_results(snapshot, config):
    config = repaired_config(config)

    class Fake:
        first = None

        def complete(self, *, view, schema, **kw):
            assert view["services"] == ["a", "b"]
            if not view["evidence"]:
                self.first = view["questions"][0]["question_id"]
                return TurnDecision.model_validate(
                    dict(
                        decision=dict(
                            action="analyze",
                            question_id=self.first,
                            hypothesis_updates=[],
                            expectations=[],
                            rationale="inspect",
                        )
                    )
                )
            assert self.first not in [q["question_id"] for q in view["questions"]]
            assert view["evidence"][0]["reference"] == "E1"
            return TurnDecision.model_validate(
                dict(
                    decision=dict(
                        action="report",
                        rationale="done",
                        report=dict(
                            ranked_components=["a"],
                            explanation="bounded",
                            references=["E1"],
                            residuals=[],
                            limitations=["sample only"],
                            conclusion="LOCAL_FINDING",
                        ),
                    )
                )
            )

    out = investigate_semantic_lite(
        SemanticAnalysis(snapshot, config), Fake(), "C", "fixture", config
    )
    assert out["status"] == "COMPLETED" and out["analysis_actions"] == 1


def test_alert_entity_boundaries():
    assert alert_ranking(
        "cartography checkout-service and CART then checkout",
        ["cart", "checkout", "checkout-service"],
    ) == ["checkout-service", "cart", "checkout"]
    assert alert_ranking("nothing named here", ["cart"]) == []


def test_dependencies_keep_requested_signal(snapshot, config):
    snapshot["references"] = {
        "a-duration": reference(),
        "z-errors": reference("error_fraction", values=[0.1, 0.2, 0.3]),
    }
    a = SemanticAnalysis(snapshot, config)
    result = a.execute(
        AnalysisRequest(
            tool="compare_dependencies",
            target="a",
            neighbors=["b"],
            signal="error_fraction",
        )
    )
    assert result["table"][0]["relative_changes"]["a"]["absolute_difference"] == -0.2
    result = a.execute(
        AnalysisRequest(
            tool="compare_dependencies",
            target="a",
            neighbors=["b"],
            signal="duration_ms",
        )
    )
    assert result["table"][0]["relative_changes"]["a"]["absolute_difference"] == 98
