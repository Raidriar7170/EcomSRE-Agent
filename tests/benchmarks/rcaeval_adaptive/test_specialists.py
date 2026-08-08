from __future__ import annotations

import pytest
from pydantic import ValidationError

from ecomsre_rcaeval_adaptive.contracts import (
    CausalRole,
    InitialDiagnosis,
    RankedHypothesis,
    RankedHypothesisBatch,
)
from ecomsre_rcaeval_adaptive.specialists import (
    validate_hypothesis_batch,
    validate_initial_diagnosis,
)


def _hypothesis(*, source: str = "logs", evidence_ref: str = "log:0001"):
    return RankedHypothesis.model_validate(
        {
            "service": "checkoutservice",
            "indicator_or_none": "cpu",
            "score": 0.8,
            "causal_role": CausalRole.ROOT_CANDIDATE,
            "supporting_evidence_refs": (evidence_ref,),
            "contradicting_evidence_refs": (),
            "summary": "The source supports a root-candidate hypothesis.",
            "source": source,
        }
    )


def test_specialist_returns_one_to_three_hypotheses() -> None:
    with pytest.raises(ValidationError):
        RankedHypothesisBatch.model_validate(
            {
                "source": "logs",
                "hypotheses": tuple(_hypothesis() for _ in range(4)),
            }
        )


def test_specialist_has_no_final_diagnosis_field() -> None:
    schema = RankedHypothesisBatch.model_json_schema(mode="validation")

    assert "final_root_service" not in str(schema)
    assert "root_cause_service" not in str(schema)


def test_unknown_or_cross_source_evidence_is_rejected() -> None:
    batch = RankedHypothesisBatch(
        source="logs",
        hypotheses=(_hypothesis(evidence_ref="trace:0001"),),
    )

    with pytest.raises(ValueError, match="unknown source evidence"):
        validate_hypothesis_batch(
            batch,
            visible_services={"checkoutservice"},
            visible_evidence_refs={"log:0001"},
        )


def test_initial_diagnosis_accepts_supplied_metric_candidate_reference() -> None:
    diagnosis = InitialDiagnosis(
        root_cause_service="checkoutservice",
        model_proposed_indicator="cpu",
        confidence=0.9,
        evidence_refs=("metric-indicator:checkoutservice:cpu",),
        explanation="The deterministic candidate supports the diagnosis.",
        uncertainty_flags=(),
    )

    assert validate_initial_diagnosis(
        diagnosis,
        visible_services={"checkoutservice"},
        visible_evidence_refs={"metric-indicator:checkoutservice:cpu"},
    ) == diagnosis


def test_initial_diagnosis_defaults_absent_optional_outputs_to_none_and_empty() -> None:
    diagnosis = InitialDiagnosis.model_validate(
        {
            "root_cause_service": "checkoutservice",
            "confidence": 0.8,
            "evidence_refs": ("metric:0001",),
            "explanation": "The bounded evidence supports this service.",
        }
    )

    assert diagnosis.model_proposed_indicator is None
    assert diagnosis.uncertainty_flags == ()
