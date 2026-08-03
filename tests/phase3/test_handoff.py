"""Phase 2 diagnosis handoff into the replay-only Phase 3 Planner."""

from __future__ import annotations

from pathlib import Path

from ecomsre.backends.replay import load_replay_case
from ecomsre.phase1.contracts import (
    EvidenceSource,
    FaultMechanism,
    RCADecision,
    RCAResult,
    RecommendedNextAction,
)
from ecomsre.phase2.contracts import Phase2Variant
from ecomsre.phase2.workflows import run_replay_workflow
from ecomsre.phase3.handoff import build_diagnosis_handoff


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_completed_phase2_trace_builds_a_valid_current_run_handoff() -> None:
    replay_case = load_replay_case(
        PROJECT_ROOT / "config/phase1/replay-cases/agent-visible",
        "ad-partial-failure-complete",
    )
    result = run_replay_workflow(
        project_root=PROJECT_ROOT,
        replay_case=replay_case,
        variant=Phase2Variant.FIXED_SPECIALIST_WORKFLOW,
    )
    assert result.trace.status == "COMPLETED"
    supporting = tuple(
        evidence.evidence_ref
        for record in result.trace.tool_call_records
        for evidence in record.evidence
        if evidence.source
        in {EvidenceSource.LOGS, EvidenceSource.TRACES, EvidenceSource.CHANGES}
    )
    confirmed = RCAResult(
        schema_version="phase1.rca-result.v1",
        decision=RCADecision.RCA_CONFIRMED,
        root_service="ad",
        fault_mechanism=FaultMechanism.RUNTIME_CONFIGURATION_FAILURE,
        causal_chain=(
            "A replay-visible configuration transition affected ad.",
            "The ad request success rate degraded in the incident window.",
        ),
        affected_sli=replay_case.incident.affected_sli,
        supporting_evidence=supporting,
        contradicting_evidence=(),
        missing_evidence=(),
        confidence=0.9,
        decision_rationale=(
            "Independent replay evidence supports a runtime configuration "
            "failure in ad."
        ),
        recommended_next_action=(
            RecommendedNextAction.PRESERVE_CURRENT_REPLAY_EVIDENCE
        ),
    )
    trace = result.trace.model_copy(update={"final_rca": confirmed})

    handoff = build_diagnosis_handoff(
        trace=trace,
        incident=replay_case.incident,
    )

    assert handoff.run_id == trace.run_id
    assert handoff.incident_id == replay_case.incident.incident_id
    assert handoff.decision is RCADecision.RCA_CONFIRMED
    assert handoff.root_service == "ad"
    assert handoff.fault_mechanism is FaultMechanism.RUNTIME_CONFIGURATION_FAILURE
    assert handoff.supporting_evidence_refs
    assert handoff.supporting_evidence_refs == (
        trace.final_rca.supporting_evidence if trace.final_rca is not None else ()
    )
