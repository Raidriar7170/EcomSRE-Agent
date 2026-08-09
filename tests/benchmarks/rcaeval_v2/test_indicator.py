from __future__ import annotations

import hashlib
import inspect
import json
import math
from pathlib import Path

import pytest

from ecomsre_rcaeval_v2.indicator import (
    CoverageAtK,
    FormulaEvaluation,
    FormulaId,
    FormulaScoreStatus,
    MetricSample,
    collapse_and_rank_candidates,
    load_indicator_config,
    normalize_metric_name,
    resolve_indicator,
    score_formula,
    score_metric_candidate,
    select_formula,
)


CONFIG_PATH = (
    Path(__file__).parents[3]
    / "config"
    / "rcaeval-re2-v2-dev"
    / "indicator-candidate-formulas.json"
)


def _config():
    digest = hashlib.sha256(CONFIG_PATH.read_bytes()).hexdigest()
    return load_indicator_config(CONFIG_PATH, expected_sha256=digest)


def _samples(
    pre: tuple[float, ...], post: tuple[float, ...], *, t0: float = 1_000.0
) -> tuple[MetricSample, ...]:
    pre_start = t0 - 600.0
    return tuple(
        [
            MetricSample(
                timestamp=pre_start + (index + 1) * 10.0,
                value=float(value),
            )
            for index, value in enumerate(pre)
        ]
        + [
            MetricSample(timestamp=t0 + index * 10.0, value=float(value))
            for index, value in enumerate(post)
        ]
    )


def _coverage(numerator: int, denominator: int) -> CoverageAtK:
    return CoverageAtK(
        numerator=numerator,
        denominator=denominator,
        value=float(numerator / denominator),
    )


def _evaluation(
    formula: FormulaId,
    *,
    macro: float,
    overall: tuple[int, int] = (96, 100),
    memory: tuple[int, int] = (8, 10),
    socket: tuple[int, int] = (8, 10),
    cpu: tuple[int, int] = (10, 10),
    unknown: int = 0,
    ambiguous: int = 0,
) -> FormulaEvaluation:
    return FormulaEvaluation(
        formula=formula,
        macro_truth_indicator_coverage_at_6=macro,
        overall_coverage_at_6=_coverage(*overall),
        memory_coverage_at_6=_coverage(*memory),
        socket_coverage_at_6=_coverage(*socket),
        per_fault_coverage_at_6={"cpu": _coverage(*cpu)},
        eligible_unknown_count=unknown,
        ambiguous_count=ambiguous,
        auxiliary_metric_count=50,
    )


def test_preregistered_config_is_complete_and_contains_no_results_or_placeholders() -> None:
    raw = CONFIG_PATH.read_text(encoding="utf-8")
    payload = json.loads(raw)
    assert "TBD" not in raw
    assert "0" * 64 not in raw
    assert "coverage" not in payload
    assert payload["dataset_schema_sha256"] == {
        "RE2-OB": "1ec60a1a5c5fc95f56048d24c53c4c6ef671c98025e7a6cae13adf2a14b3105c",
        "RE2-SS": "2cbb47c37cb486892fd2fbb1d16482483220afec5ca6b83a8bb0f5ccbcddfcf8",
    }
    assert payload["windows"] == {
        "post": "[T0,T0+600]",
        "post_seconds": 600,
        "pre": "[T0-600,T0)",
        "pre_seconds": 600,
    }
    assert payload["schema_version"] == (
        "rcaeval-re2-v2.indicator-candidate-formulas.v2"
    )
    assert payload["metric_value_policy"] == {
        "initial_value": "ZERO",
        "missing_timestamp": "DROP_ROW",
        "missing_value": "PREVIOUS_FINITE_OR_ZERO",
        "nonfinite_timestamp": "FAIL_CLOSED",
        "nonfinite_value": "PREVIOUS_FINITE_OR_ZERO",
        "row_order": "PRESERVE",
    }


def test_config_hash_mismatch_fails_before_any_formula_score() -> None:
    with pytest.raises(ValueError, match="hash"):
        load_indicator_config(CONFIG_PATH, expected_sha256="f" * 64)


def test_f0_f1_f2_are_finite_for_zero_baseline_and_zero_mad() -> None:
    samples = _samples((0.0, 0.0, 0.0), (2.0, 2.0, 2.0))
    scores = {
        formula: score_formula(samples, 1_000.0, formula, _config())
        for formula in FormulaId
    }
    assert all(item.status is FormulaScoreStatus.SCORED for item in scores.values())
    assert all(item.score is not None and math.isfinite(item.score) for item in scores.values())
    assert scores[FormulaId.F0].score == pytest.approx(2.0e9)
    assert scores[FormulaId.F1].score == pytest.approx(2.0e9)
    assert scores[FormulaId.F2].score == pytest.approx(21.0)
    assert scores[FormulaId.F2].persistence == 1.0


def test_window_boundaries_and_input_order_are_exact_and_deterministic() -> None:
    config = _config()
    samples = (
        MetricSample(timestamp=399.999, value=999.0),
        MetricSample(timestamp=400.0, value=1.0),
        MetricSample(timestamp=999.999, value=1.0),
        MetricSample(timestamp=1_000.0, value=3.0),
        MetricSample(timestamp=1_599.999, value=3.0),
        MetricSample(timestamp=1_600.0, value=3.0),
    )
    forward = score_formula(samples, 1_000.0, FormulaId.F0, config)
    reverse = score_formula(tuple(reversed(samples)), 1_000.0, FormulaId.F0, config)
    assert forward == reverse
    assert forward.pre_count == 2
    assert forward.post_count == 3
    assert forward.score == pytest.approx(2.0)


def test_persistence_counts_only_the_preregistered_shift_direction() -> None:
    score = score_formula(
        _samples((0.0, 0.0, 0.0), (-2.0, -2.0, 2.0)),
        1_000.0,
        FormulaId.F2,
        _config(),
    )

    assert score.persistence == pytest.approx(2 / 3)


def test_empty_window_returns_typed_no_usable_series() -> None:
    score = score_formula(
        (MetricSample(timestamp=1_000.0, value=1.0),),
        1_000.0,
        FormulaId.F1,
        _config(),
    )
    assert score.status is FormulaScoreStatus.NO_USABLE_SERIES
    assert score.score is None
    assert score.pre_count == 0
    assert score.post_count == 1


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_nonfinite_samples_fail_closed(value: float) -> None:
    with pytest.raises(ValueError, match="finite"):
        MetricSample(timestamp=1.0, value=value)


@pytest.mark.parametrize(
    ("system", "metric_name", "disposition", "service", "indicator"),
    [
        (
            "RE2-OB",
            "InboundPassthroughClusterIpv4_cpu",
            "CANONICAL",
            "InboundPassthroughClusterIpv4",
            "cpu",
        ),
        ("RE2-OB", "frontend_cpu", "CANONICAL", "frontend", "cpu"),
        ("RE2-OB", "frontend-check_latency-90", "CANONICAL", "frontend-check", "latency"),
        ("RE2-SS", "carts-db_socket", "CANONICAL", "carts-db", "socket"),
        ("RE2-SS", "orders_error", "AUXILIARY", "orders", None),
        ("RE2-OB", "frontend_workload", "AUXILIARY", "frontend", None),
        ("RE2-OB", "Frontend_cpu", "UNKNOWN", None, None),
        ("RE2-OB", "frontend_cpu ", "UNKNOWN", "frontend", None),
        ("RE2-OB", "frontend_mystery", "UNKNOWN", "frontend", None),
    ],
)
def test_normalization_uses_exact_identity_prefix_and_suffix_without_alias_guessing(
    system: str,
    metric_name: str,
    disposition: str,
    service: str | None,
    indicator: str | None,
) -> None:
    result = normalize_metric_name(system, metric_name, _config())
    assert result.disposition.value == disposition
    assert result.service == service
    assert result.canonical_indicator == indicator


def test_normalization_fails_closed_for_non_development_system() -> None:
    with pytest.raises(ValueError, match="OB/SS"):
        normalize_metric_name("RE2-XX", "frontend_cpu", _config())


def test_raw_candidates_are_preserved_then_collapsed_and_ranked_deterministically() -> None:
    config = _config()
    case_identity_sha256 = hashlib.sha256(b"case-a").hexdigest()
    series = _samples((1.0, 1.0, 1.0), (2.0, 2.0, 2.0))
    candidates = (
        score_metric_candidate(
            "RE2-OB",
            "frontend_latency-50",
            series,
            1_000.0,
            FormulaId.F0,
            "metric:0002",
            case_identity_sha256,
            config,
        ),
        score_metric_candidate(
            "RE2-OB",
            "frontend_latency-90",
            series,
            1_000.0,
            FormulaId.F0,
            "metric:0001",
            case_identity_sha256,
            config,
        ),
        score_metric_candidate(
            "RE2-OB",
            "frontend_cpu",
            series,
            1_000.0,
            FormulaId.F0,
            "metric:0003",
            case_identity_sha256,
            config,
        ),
        score_metric_candidate(
            "RE2-OB",
            "frontend_error",
            series,
            1_000.0,
            FormulaId.F0,
            "metric:0004",
            case_identity_sha256,
            config,
        ),
        score_metric_candidate(
            "RE2-OB",
            "frontend_unknown",
            series,
            1_000.0,
            FormulaId.F0,
            "metric:0005",
            case_identity_sha256,
            config,
        ),
    )
    ranked_once = collapse_and_rank_candidates(candidates, config)
    ranked_twice = collapse_and_rank_candidates(tuple(reversed(candidates)), config)

    assert len(candidates) == 5
    assert len(ranked_once) == 2
    assert ranked_once == ranked_twice
    assert {(item.service, item.canonical_indicator) for item in ranked_once} == {
        ("frontend", "cpu"),
        ("frontend", "latency"),
    }
    assert [item.rank_global for item in ranked_once] == [1, 2]
    assert [item.rank_within_service for item in ranked_once] == [1, 2]


def test_resolver_selects_only_rank_one_for_exact_judge_service() -> None:
    config = _config()
    case_identity_sha256 = hashlib.sha256(b"case-b").hexdigest()
    series = _samples((1.0, 1.0), (3.0, 3.0))
    ranked = collapse_and_rank_candidates(
        (
            score_metric_candidate(
                "RE2-SS",
                "orders_cpu",
                series,
                1_000.0,
                FormulaId.F0,
                "metric:0001",
                case_identity_sha256,
                config,
            ),
            score_metric_candidate(
                "RE2-SS",
                "payment_mem",
                series,
                1_000.0,
                FormulaId.F0,
                "metric:0002",
                case_identity_sha256,
                config,
            ),
        ),
        config,
    )
    orders = resolve_indicator("orders", ranked)
    missing = resolve_indicator("catalogue", ranked)
    assert orders.disposition == "RESOLVED"
    assert orders.selected_service == "orders"
    assert orders.evidence_ref == "metric:0001"
    assert missing.disposition == "NO_INDICATOR_CANDIDATE"


def test_formula_selection_applies_all_gates_and_simpler_within_one_pp() -> None:
    selected = select_formula(
        (
            _evaluation(FormulaId.F0, macro=0.955),
            _evaluation(FormulaId.F1, macro=0.964),
            _evaluation(FormulaId.F2, macro=0.980, unknown=1, ambiguous=1),
        ),
        _config(),
    )
    assert selected.selected_formula is FormulaId.F0
    assert selected.eligible_formulas == (FormulaId.F0, FormulaId.F1)
    assert selected.gate_passed is True
    assert selected.rejections[FormulaId.F2] == (
        "ELIGIBLE_UNKNOWN_NONZERO",
        "AMBIGUOUS_NONZERO",
    )


def test_formula_selection_rejects_per_fault_regression_against_f0() -> None:
    selected = select_formula(
        (
            _evaluation(FormulaId.F0, macro=0.96, cpu=(10, 10)),
            _evaluation(FormulaId.F1, macro=0.99, cpu=(9, 10)),
            _evaluation(FormulaId.F2, macro=0.97, memory=(7, 10)),
        ),
        _config(),
    )
    assert selected.selected_formula is FormulaId.F0
    assert "PER_FAULT_REGRESSION" in selected.rejections[FormulaId.F1]
    assert "MEMORY_GATE" in selected.rejections[FormulaId.F2]


def test_indicator_api_exposes_no_ground_truth_or_fault_inputs() -> None:
    for function in (
        score_formula,
        normalize_metric_name,
        score_metric_candidate,
        collapse_and_rank_candidates,
        resolve_indicator,
    ):
        names = set(inspect.signature(function).parameters)
        assert not names & {"ground_truth", "truth", "fault", "root_cause_service"}


def test_metric_candidate_requires_opaque_case_identity_for_tie_domain() -> None:
    assert "case_identity_sha256" in inspect.signature(
        score_metric_candidate
    ).parameters
