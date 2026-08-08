from __future__ import annotations

import json

import pytest

from ecomsre_rcaeval_adaptive.contracts import (
    CausalRole,
    FusionAction,
    FusionDecision,
    InitialDiagnosis,
    RankedHypothesis,
)
from ecomsre_rcaeval_adaptive.fusion import (
    FusionInput,
    build_fusion_request_payload,
    validate_fusion_decision,
)
from ecomsre_rcaeval_v2.contracts import BoundedEvidenceSnapshotV2


def _input() -> FusionInput:
    return FusionInput(
        initial_diagnosis=InitialDiagnosis(
            root_cause_service="checkoutservice",
            model_proposed_indicator="cpu",
            confidence=0.7,
            evidence_refs=("metric:0001",),
            explanation="Initial bounded evidence.",
            uncertainty_flags=(),
        ),
        metrics_hypotheses=(
            RankedHypothesis(
                service="checkoutservice",
                indicator_or_none="cpu",
                score=1.0,
                causal_role=CausalRole.ROOT_CANDIDATE,
                supporting_evidence_refs=("metric:0001",),
                contradicting_evidence_refs=(),
                summary="Metrics anchor.",
                source="metrics",
            ),
        ),
        specialist_hypotheses=(),
        bounded_evidence=(
            BoundedEvidenceSnapshotV2(
                evidence_ref="metric:0001",
                source="metrics",
                service="checkoutservice",
                observation="Metric anomaly.",
            ),
        ),
    )


def test_fusion_payload_is_architecture_blind() -> None:
    payload = build_fusion_request_payload(
        model="locked-model", fusion_input=_input(), max_completion_tokens=512
    )
    serialized = json.dumps(payload).casefold()

    for forbidden in ("adaptive", "single", "dynamic", "variant"):
        assert forbidden not in serialized


def test_default_keep_requires_initial_service() -> None:
    decision = FusionDecision(
        action=FusionAction.KEEP_INITIAL,
        final_root_service="frontend",
        confidence=0.8,
        supporting_evidence_refs=("metric:0001",),
        contradicting_evidence_refs=(),
        reason_codes=("WEAK_NEW_EVIDENCE",),
    )

    with pytest.raises(ValueError, match="KEEP_INITIAL"):
        validate_fusion_decision(decision, _input())


def test_strong_contradiction_can_override_to_supported_candidate() -> None:
    fusion_input = _input().model_copy(
        update={
            "specialist_hypotheses": (
                RankedHypothesis(
                    service="frontend",
                    indicator_or_none="latency",
                    score=0.9,
                    causal_role=CausalRole.ROOT_CANDIDATE,
                    supporting_evidence_refs=("trace:0001",),
                    contradicting_evidence_refs=("metric:0001",),
                    summary="Trace direction contradicts the initial service.",
                    source="traces",
                ),
            ),
            "bounded_evidence": _input().bounded_evidence
            + (
                BoundedEvidenceSnapshotV2(
                    evidence_ref="trace:0001",
                    source="traces",
                    service="frontend",
                    observation="Trace propagation starts at frontend.",
                ),
            ),
        }
    )
    decision = FusionDecision(
        action=FusionAction.OVERRIDE_INITIAL,
        final_root_service="frontend",
        confidence=0.9,
        supporting_evidence_refs=("trace:0001",),
        contradicting_evidence_refs=("metric:0001",),
        reason_codes=("STRONG_CAUSAL_CONTRADICTION",),
    )

    assert validate_fusion_decision(decision, fusion_input) == decision


def test_override_rejects_weak_support_without_contradiction() -> None:
    fusion_input = _input().model_copy(
        update={
            "specialist_hypotheses": (
                RankedHypothesis(
                    service="frontend",
                    indicator_or_none="latency",
                    score=0.6,
                    causal_role=CausalRole.PROPAGATED_SYMPTOM,
                    supporting_evidence_refs=("log:0001",),
                    contradicting_evidence_refs=(),
                    summary="Weak symptom support only.",
                    source="logs",
                ),
            ),
            "bounded_evidence": _input().bounded_evidence
            + (
                BoundedEvidenceSnapshotV2(
                    evidence_ref="log:0001",
                    source="logs",
                    service="frontend",
                    observation="Frontend emitted an error log.",
                ),
            ),
        }
    )
    decision = FusionDecision(
        action=FusionAction.OVERRIDE_INITIAL,
        final_root_service="frontend",
        confidence=0.8,
        supporting_evidence_refs=("log:0001",),
        contradicting_evidence_refs=(),
        reason_codes=("ALTERNATIVE_SUPPORT",),
    )

    with pytest.raises(ValueError, match="contradicting root-candidate evidence"):
        validate_fusion_decision(decision, fusion_input)
