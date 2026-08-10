from __future__ import annotations

import pytest
from pydantic import ValidationError

from ecomsre_rca100.contracts import (
    RCA100InitialDiagnosis,
    RCA100MetricsEntityRank,
    RCA100ReasoningStep,
    arbitrate_rca100_diagnosis,
    decide_rca100_metrics_arbitration,
)


def _initial(entity_ref: str) -> RCA100InitialDiagnosis:
    return RCA100InitialDiagnosis(
        root_cause_entity_ref=entity_ref,
        fault_type="latency",
        confidence=0.8,
        evidence_refs=("log:0001",),
        reasoning_steps=(
            RCA100ReasoningStep(
                claim="The bounded log pattern supports this entity.",
                entity_ref_or_none=entity_ref,
                evidence_refs=("log:0001",),
            ),
        ),
        summary="One label-blind initial diagnosis.",
    )


def _ranking() -> tuple[RCA100MetricsEntityRank, ...]:
    return (
        RCA100MetricsEntityRank(
            entity_ref="apm|apm.service|svc-b",
            rank=1,
            score=4.0,
            supporting_metrics_evidence_refs=("metric:0001",),
        ),
        RCA100MetricsEntityRank(
            entity_ref="apm|apm.service|svc-a",
            rank=2,
            score=1.0,
            supporting_metrics_evidence_refs=("metric:0002",),
        ),
    )


def test_m3_override_changes_only_root_and_replaces_evidence() -> None:
    initial = _initial("k8s|k8s.pod|pod-a")

    final = arbitrate_rca100_diagnosis(initial, _ranking())

    assert final.arbitration_decision.action == "OVERRIDE_METRICS_TOP1"
    assert final.final_diagnosis.root_cause_entity_ref == "apm|apm.service|svc-b"
    assert final.final_diagnosis.fault_type == initial.fault_type
    assert final.final_diagnosis.evidence_refs == ("metric:0001",)
    assert final.final_diagnosis.confidence is None
    assert final.root_provenance == "DETERMINISTIC_METRICS_M3"
    assert final.fault_type_provenance == "MODEL_INITIAL"
    assert final.final_diagnosis.reasoning_steps[-1].evidence_refs == ("metric:0001",)


def test_m3_keeps_exact_initial_when_rank_condition_fails() -> None:
    initial = _initial("apm|apm.service|svc-a")

    final = arbitrate_rca100_diagnosis(initial, _ranking())

    assert final.arbitration_decision.action == "KEEP_INITIAL"
    assert final.arbitration_decision.initial_metrics_rank_or_none == 2
    assert final.final_diagnosis == initial
    assert final.root_provenance == "MODEL_INITIAL"


def test_m3_top1_match_is_an_explicit_noop() -> None:
    decision = decide_rca100_metrics_arbitration(
        initial_root_entity_ref="apm|apm.service|svc-b",
        ranking=_ranking(),
    )

    assert decision.action == "KEEP_INITIAL"
    assert decision.reason_codes == ("KEEP_INITIAL_TOP1_MATCH",)


def test_m3_keeps_exact_initial_when_metrics_projection_is_unavailable() -> None:
    initial = _initial("apm|apm.service|svc-a")

    final = arbitrate_rca100_diagnosis(initial, ())

    assert final.arbitration_decision.action == "KEEP_INITIAL"
    assert final.arbitration_decision.reason_codes == (
        "METRICS_PROJECTION_UNAVAILABLE",
    )
    assert final.arbitration_decision.metrics_top1_entity_ref is None
    assert final.final_diagnosis == initial


def test_m3_rejects_non_metrics_provenance() -> None:
    with pytest.raises(ValidationError, match="Metrics references"):
        RCA100MetricsEntityRank(
            entity_ref="apm|apm.service|svc-b",
            rank=1,
            score=4.0,
            supporting_metrics_evidence_refs=("indicator:0001",),
        )
