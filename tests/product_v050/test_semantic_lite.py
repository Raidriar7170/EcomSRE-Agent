import copy
from types import SimpleNamespace

import pytest

import test_semantic_investigation as base_fixtures

from ecomsre.model.gateway import OpenAICompatibleConfig
from ecomsre.product.errors import ProductError
from ecomsre.product.investigation.contracts import PriceSchedule
from ecomsre.product.investigation.provider import StructuredProvider
from ecomsre.product.investigation.semantic_analysis import SemanticAnalysis
from ecomsre.product.investigation.semantic_contracts import AnalysisRequest
from ecomsre.product.investigation.semantic_lite import (
    TASK,
    LiteDecision,
    question_catalog,
    answer_question,
    already_exposed,
    investigate_semantic_lite,
)
from scripts.product_v050.evaluate_semantic_investigation import ResearchLedger


config = base_fixtures.config
snapshot = base_fixtures.snapshot


def decision(**overrides):
    base = dict(
        action="analyze",
        question_id="Q1",
        hypothesis_updates=[],
        expectations=[],
        rationale="bounded fixture",
        report=None,
    )
    return LiteDecision(**dict(base, **overrides))


def report(refs=None, ranks=None):
    return decision(
        action="report",
        question_id=None,
        report=dict(
            ranked_components=ranks or [],
            explanation="Observed sample only.",
            references=refs or [],
            residuals=["Independent unknown"],
            limitations=["Not causal proof"],
            conclusion="INSUFFICIENT_EVIDENCE" if ranks else "ABSTAIN",
        ),
    )


def test_catalog_no_answer_or_label_selection_and_missing_capabilities(
    snapshot, config
):
    a = SemanticAnalysis(snapshot, config)
    a.execute = lambda *args: pytest.fail("catalog must not execute tools")
    original = question_catalog(a)
    changed = copy.deepcopy(snapshot)
    for row in changed["records"]:
        row["error"] = not bool(row["error"])
        row["duration_ms"] = 99999
    c = dict(config, cases=[dict(root="b")])
    assert question_catalog(SemanticAnalysis(changed, c)) == original
    for row in changed["records"]:
        row.pop("operation")
        row.pop("parent_span_id")
    changed["topology"] = []
    catalog, gaps = question_catalog(SemanticAnalysis(changed, c))
    assert not catalog and len(gaps) == 8
    assert original[0]["Q1"]["request"]["target"] == "a"
    assert any(q["request"]["target"] == "b" for q in original[0].values())


def test_catalog_matches_reference_scope_without_reading_values(snapshot, config):
    snapshot["references"] = dict(
        good=dict(
            scope=["a", None, None],
            unit="ms",
            method="trace_sample",
            fixed_at=9,
            window=[0, 10],
            values=[1, 2, 3],
            source_refs=[],
        ),
        wrong=dict(
            scope=["a", "op", None],
            unit="fraction",
            method="trace_sample",
            fixed_at=9,
            window=[0, 10],
            values=[0],
            source_refs=[],
        ),
    )
    one = question_catalog(SemanticAnalysis(snapshot, config))
    snapshot["references"]["good"]["values"] = [None, 999999, -1]
    assert question_catalog(SemanticAnalysis(snapshot, config)) == one
    baselines = [q for q in one[0].values() if q["kind"] == "historical_increase"]
    assert len(baselines) == 1 and baselines[0]["request"]["reference_id"] == "good"


def test_predicates_preserve_unknown_instead_of_false(snapshot, config):
    a = SemanticAnalysis(snapshot, config)
    cat, _ = question_catalog(a)
    q = cat["Q1"]
    for row in snapshot["records"]:
        row["error"] = None
    result = SemanticAnalysis(snapshot, config).execute(AnalysisRequest(**q["request"]))
    assert answer_question(q, result)["category"] == "OBSERVATION_INSUFFICIENT"
    assert (
        answer_question(
            dict(kind="historical_increase", threshold=0.05),
            dict(
                table=[dict(absolute_difference=None, status="INSUFFICIENT_REFERENCE")]
            ),
        )["outcome"]
        == "UNKNOWN"
    )


def test_delta_state_and_observation_update_are_retained(snapshot, config):
    seen = []
    sequence = [
        decision(
            hypothesis_updates=[
                dict(
                    hypothesis_id="H1",
                    target="a",
                    explanation="local errors",
                    support=[],
                    conflicts=[],
                ),
                dict(
                    hypothesis_id="H2",
                    target="b",
                    explanation="other source",
                    support=[],
                    conflicts=[],
                ),
            ],
            expectations=[
                dict(hypothesis_id="H1", outcome="YES"),
                dict(hypothesis_id="H2", outcome="NO"),
            ],
        ),
        report(["E1"], ["b"]),
    ]

    def complete(**kwargs):
        seen.append(kwargs["view"])
        return sequence.pop(0)

    r = investigate_semantic_lite(
        SemanticAnalysis(snapshot, config),
        SimpleNamespace(complete=complete),
        "D",
        "fixture",
        config,
    )
    assert r["status"] == "COMPLETED" and len(r["hypotheses"]) == 2
    assert seen[1]["hypotheses"] and seen[1]["prediction_checks"]
    assert r["report"]["resolved_references"] == [
        r["analysis_results"][0]["analysis_id"]
    ]
    assert (
        r["prediction_checks"][0]["predictions"][0]["status"]
        == "PREDICTION_CONTRADICTED"
    )
    assert all(h["explanation"] for h in r["hypotheses"])
    selection = r["trajectory"][0]["selection"]
    assert selection["branch"] == "CONTRAST" and selection["executed"]
    assert selection["selection_score"] == 1 / (1 + selection["estimated_cost"])

    q = question_catalog(SemanticAnalysis(snapshot, config))[0]["Q1"]
    baseline = dict(
        request=AnalysisRequest(tool="compare_baseline", target="a").model_dump(),
        table=[dict(current=1.0)],
    )
    assert already_exposed(q, [baseline])
    dependency = dict(
        request=AnalysisRequest(
            tool="compare_dependencies", target="b", neighbors=["a"]
        ).model_dump(),
        table=[
            dict(service_samples={"a": dict(count=10, unknown_status=2, error_count=5)})
        ],
    )
    assert already_exposed(q, [dependency])


@pytest.mark.parametrize(
    "bad",
    [
        decision(question_id="invented"),
        report(["invented"], ["a"]),
        report(["E1"], ["fake-service"]),
    ],
)
@pytest.mark.parametrize("method", ["C", "D"])
def test_invalid_handles_and_scope_not_repaired_silently(snapshot, config, bad, method):
    r = investigate_semantic_lite(
        SemanticAnalysis(snapshot, config),
        SimpleNamespace(complete=lambda **kw: bad),
        method,
        "fixture",
        config,
    )
    assert (
        r["status"] == "FORMAT_FAILED"
        and r["provider_calls"] == 2
        and r["report"] is None
    )


def test_strict_wire_and_fail_closed_truncation_with_same_configuration(tmp_path):
    ledger = ResearchLedger(tmp_path)
    captured = []
    response = dict(
        model="fixture",
        status="completed",
        usage=dict(
            input_tokens=10,
            output_tokens=20,
            output_tokens_details=dict(reasoning_tokens=3),
        ),
        output=[
            dict(
                type="function_call",
                status="completed",
                name="submit_proposal",
                arguments=report().model_dump_json(),
            )
        ],
    )

    def post(**kwargs):
        captured.append(kwargs["payload"])
        return response

    provider = StructuredProvider(
        OpenAICompatibleConfig("https://example.test/v1", "fixture", "fixture"),
        PriceSchedule(
            provider_profile="fixture",
            model="fixture",
            as_of="2026-09-29",
            source="fixture",
            input_usd_per_million=0.75,
            output_usd_per_million=4.5,
        ),
        ledger,
        api_style="responses",
        transport=SimpleNamespace(post_json=post),
    )
    for method in ["C", "D"]:
        provider.complete(
            key=f"semantic-investigation-v1:fixture:{method}",
            task=TASK,
            view=dict(strategy=method),
            schema=LiteDecision,
            reasoning="low",
        )
    assert all(
        x["tools"][0]["strict"] is True
        and x["max_output_tokens"] == 4096
        and x["reasoning"] == dict(effort="low")
        for x in captured
    )
    assert captured[0]["tools"] == captured[1]["tools"]
    params = captured[0]["tools"][0]["parameters"]
    assert (
        set(params["required"]) == set(params["properties"])
        and params["additionalProperties"] is False
    )
    response.update(
        status="incomplete", incomplete_details=dict(reason="max_output_tokens")
    )
    with pytest.raises(ProductError, match="Output token cap"):
        provider.complete(
            key="semantic-investigation-v1:fixture:truncated",
            task=TASK,
            view={},
            schema=LiteDecision,
            reasoning="low",
        )
    saved = ledger.entries()["semantic-investigation-v1:fixture:truncated"]
    assert (
        saved["payload"]["incomplete_details"]["reason"] == "max_output_tokens"
        and saved["payload"]["structured_output_strict"] is True
    )


@pytest.mark.parametrize("method", ["C", "D"])
def test_single_hypothesis_read_and_exposed_fallback(snapshot, config, method):
    h = dict(
        hypothesis_id="H1",
        target="a",
        explanation="local errors",
        support=[],
        conflicts=[],
    )
    first = decision(
        hypothesis_updates=[h], expectations=[dict(hypothesis_id="H1", outcome="YES")]
    )
    updated = dict(
        h, explanation="Sample did not show the predicted pattern", conflicts=["E1"]
    )
    repeat = decision(hypothesis_updates=[updated], expectations=first.expectations)
    sequence = [first, repeat, report(["E1"], ["a"])]
    result = investigate_semantic_lite(
        SemanticAnalysis(snapshot, config),
        SimpleNamespace(complete=lambda **kw: sequence.pop(0)),
        method,
        "single",
        config,
    )
    assert result["status"] == "COMPLETED" and result["analysis_actions"] == 1
    assert result["hypotheses"][0]["explanation"] == updated["explanation"]
    assert result["trajectory"][1]["check"]["category"] == "NO_PROGRESS"
    assert result["trajectory"][0]["selection"]["branch"] == (
        "REACT" if method == "C" else "REACT_FALLBACK"
    )
    assert result["prediction_checks"][0]["precommitted"] == (method == "C")
    assert result["trajectory"][1]["selection"]["executed"] is False
