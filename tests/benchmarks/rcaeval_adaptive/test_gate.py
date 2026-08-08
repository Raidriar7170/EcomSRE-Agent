from __future__ import annotations

from ecomsre_rcaeval_adaptive.contracts import (
    EscalationRoute,
    InitialDiagnosis,
    UncertaintyFlag,
)
from ecomsre_rcaeval_adaptive.gate import GateInputs, GatePolicy, decide_escalation


def _diagnosis(
    *, confidence: float = 0.9, flags: tuple[UncertaintyFlag, ...] = ()
) -> InitialDiagnosis:
    return InitialDiagnosis(
        root_cause_service="checkoutservice",
        model_proposed_indicator="cpu",
        confidence=confidence,
        evidence_refs=("metric:0001", "log:0001"),
        explanation="The bounded evidence supports checkoutservice.",
        uncertainty_flags=flags,
    )


def _inputs(**updates: object) -> GateInputs:
    values: dict[str, object] = {
        "initial_diagnosis": _diagnosis(),
        "metrics_service_ranking": (
            ("checkoutservice", 1.0),
            ("frontend", 0.4),
            ("cartservice", 0.2),
        ),
        "initial_evidence_supports_predicted_service": True,
        "cross_source_service_disagreement": False,
        "indicator_candidate_available": True,
        "trace_available": True,
    }
    values.update(updates)
    return GateInputs.model_validate(values)


def test_high_confidence_easy_case_returns_directly() -> None:
    decision = decide_escalation(_inputs(), GatePolicy())

    assert decision.route is EscalationRoute.DIRECT_RETURN
    assert decision.gate_feature_snapshot.metrics_service_rank == 1
    assert decision.gate_feature_snapshot.metrics_top1_top2_margin == 0.6


def test_medium_conflict_escalates_to_logs() -> None:
    decision = decide_escalation(
        _inputs(
            initial_diagnosis=_diagnosis(confidence=0.7),
            cross_source_service_disagreement=True,
        ),
        GatePolicy(),
    )

    assert decision.route is EscalationRoute.ESCALATE_LOGS


def test_network_ambiguity_escalates_to_traces_when_available() -> None:
    decision = decide_escalation(
        _inputs(
            initial_diagnosis=_diagnosis(
                confidence=0.7,
                flags=(UncertaintyFlag.NETWORK_OR_TRACE_AMBIGUITY,),
            )
        ),
        GatePolicy(),
    )

    assert decision.route is EscalationRoute.ESCALATE_TRACES


def test_low_confidence_multiple_conflicts_escalates_both() -> None:
    decision = decide_escalation(
        _inputs(
            initial_diagnosis=_diagnosis(confidence=0.3),
            metrics_service_ranking=(("frontend", 1.0), ("cartservice", 0.8)),
            initial_evidence_supports_predicted_service=False,
            cross_source_service_disagreement=True,
        ),
        GatePolicy(),
    )

    assert decision.route is EscalationRoute.ESCALATE_BOTH


def test_ss_never_selects_a_trace_route() -> None:
    decision = decide_escalation(
        _inputs(
            initial_diagnosis=_diagnosis(
                confidence=0.3,
                flags=(UncertaintyFlag.NETWORK_OR_TRACE_AMBIGUITY,),
            ),
            metrics_service_ranking=(("frontend", 1.0),),
            initial_evidence_supports_predicted_service=False,
            cross_source_service_disagreement=True,
            trace_available=False,
        ),
        GatePolicy(),
    )

    assert decision.route is EscalationRoute.ESCALATE_LOGS


def test_gate_is_deterministic() -> None:
    inputs = _inputs(initial_diagnosis=_diagnosis(confidence=0.72))
    first = decide_escalation(inputs, GatePolicy())
    second = decide_escalation(inputs, GatePolicy())

    assert first == second
