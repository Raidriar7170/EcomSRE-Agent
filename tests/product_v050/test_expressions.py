import pytest
from pydantic import ValidationError
from ecomsre.product.knowledge.expressions import DerivedExpression, evaluate_expression


def expression(**overrides):
    return DerivedExpression.model_validate(
        {
            "numerator": {"field": "cpu_percent", "operator": "max"},
            "denominator": {"field": "cpu_percent", "operator": "mean"},
            "comparator": "gt",
            "threshold": 1.5,
            "threshold_unit": "RATIO",
            "threshold_provenance": "frozen-development-v1",
            "window_seconds": 10,
            "minimum_samples": 3,
        }
        | overrides
    )


def observation(values=(10.0, 90.0, 10.0)):
    return {
        "evidence_ref": "r1",
        "source": "RESOURCES",
        "status": "SUCCESS_NONEMPTY",
        "covered_services": ["payment"],
        "truncated": False,
        "records": [
            {
                "service": "payment",
                "sampling_window_seconds": 10,
                "samples": [
                    {"offset_ms": i * 5000, "cpu_percent": v, "memory_bytes": 1000}
                    for i, v in enumerate(values)
                ],
            }
        ],
    }


def test_derived_feature_evaluates_unseen_values_and_negative():
    assert (
        evaluate_expression(
            expression(), target="payment", observations=[observation()]
        ).value
        == 1.8
    )
    assert (
        evaluate_expression(
            expression(),
            target="payment",
            observations=[observation((30.0, 30.0, 30.0))],
        ).status
        == "FALSE"
    )
    assert (
        evaluate_expression(
            expression(), target="payment", observations=[observation((7.0, 70.0, 7.0))]
        ).status
        == "TRUE"
    )


@pytest.mark.parametrize(
    "change,reason",
    [
        ({"truncated": True}, "MISSING_OR_AMBIGUOUS_OBSERVATION"),
        ({"covered_services": []}, "MISSING_OR_AMBIGUOUS_OBSERVATION"),
        ({"status": "FAILURE_UNAVAILABLE"}, "MISSING_OR_AMBIGUOUS_OBSERVATION"),
    ],
)
def test_missing_evidence_never_becomes_zero(change, reason):
    result = evaluate_expression(
        expression(), target="payment", observations=[observation() | change]
    )
    assert (
        result.status == "UNKNOWN" and result.value is None and result.reason == reason
    )


def test_zero_denominator_and_wrong_target():
    assert (
        evaluate_expression(
            expression(), target="payment", observations=[observation((0.0, 0.0, 0.0))]
        ).reason
        == "ZERO_DENOMINATOR"
    )
    assert (
        evaluate_expression(
            expression(), target="ad", observations=[observation()]
        ).status
        == "UNKNOWN"
    )


@pytest.mark.parametrize(
    "change",
    [
        {"numerator": {"field": "arbitrary", "operator": "max"}},
        {"numerator": {"field": "cpu_percent", "operator": "exec"}},
        {"denominator": {"field": "memory_bytes", "operator": "mean"}},
        {"threshold_unit": "BYTES"},
        {"threshold": float("nan")},
        {"code": "import os"},
        {"minimum_samples": 1},
    ],
)
def test_invalid_dsl_fails_closed(change):
    with pytest.raises(ValidationError):
        expression(**change)


def test_sample_alignment_and_insufficient_samples():
    raw = observation()
    raw["records"][0]["samples"][-1]["offset_ms"] = 9000
    assert (
        evaluate_expression(expression(), target="payment", observations=[raw]).reason
        == "INCOMPLETE_TIME_COVERAGE"
    )
    raw["records"][0]["samples"].pop()
    assert (
        evaluate_expression(expression(), target="payment", observations=[raw]).reason
        == "WINDOW_OR_SAMPLE_COUNT"
    )
