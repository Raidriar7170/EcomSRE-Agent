from __future__ import annotations

from pathlib import Path

import pytest

from ecomsre.backends.live_protocol import (
    BackendObservation,
    BackendStatus,
    ChangesObservationBatch,
    LogsObservationBatch,
)
from ecomsre.backends.replay import ReplayCase, load_replay_case
from ecomsre.phase1.contracts import EvidenceSource
from ecomsre.phase5a.contracts import DiagnosisDecisionV2, DiagnosisResultV2
from ecomsre.phase5a.judge import judge_diagnosis_v2
from ecomsre.phase5a import workflows
from ecomsre.phase5a.workflows import (
    DiagnosisVariantV2,
    run_diagnosis_v2,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
PHASE1_CASE_ROOT = PROJECT_ROOT / "config/phase1/replay-cases/agent-visible"
PHASE4_CASE_ROOT = PROJECT_ROOT / "config/phase4/replay-cases/agent-visible"


def _run(replay_case: ReplayCase, variant: DiagnosisVariantV2):
    return run_diagnosis_v2(
        project_root=PROJECT_ROOT,
        replay_case=replay_case,
        variant=variant,
    )


def _semantic_projection(result: DiagnosisResultV2) -> tuple[object, ...]:
    return (
        result.decision,
        result.root_service,
        result.fault_mechanism,
        result.causal_chain,
        result.affected_sli,
        result.missing_evidence,
        result.confidence,
        result.decision_rationale,
        result.recommended_next_action,
    )


@pytest.mark.parametrize("variant", tuple(DiagnosisVariantV2))
def test_case_id_invariance(variant: DiagnosisVariantV2) -> None:
    original = load_replay_case(
        PHASE1_CASE_ROOT,
        "ad-partial-failure-complete",
    )
    renamed = original.model_copy(update={"case_id": "randomized-case-42"})

    original_trace = _run(original, variant)
    renamed_trace = _run(renamed, variant)

    assert original_trace.final_diagnosis is not None
    assert renamed_trace.final_diagnosis is not None
    assert _semantic_projection(renamed_trace.final_diagnosis) == (
        _semantic_projection(original_trace.final_diagnosis)
    )


@pytest.mark.parametrize("variant", tuple(DiagnosisVariantV2))
def test_evidence_tuple_order_invariance(variant: DiagnosisVariantV2) -> None:
    original = load_replay_case(
        PHASE4_CASE_ROOT,
        "search-ranking-configuration-frontend-decoy",
    )
    reordered = original.model_copy(
        update={
            "metrics": original.metrics.model_copy(
                update={"observations": tuple(reversed(original.metrics.observations))}
            ),
            "changes": original.changes.model_copy(
                update={"observations": tuple(reversed(original.changes.observations))}
            ),
        }
    )

    original_trace = _run(original, variant)
    reordered_trace = _run(reordered, variant)

    assert original_trace.final_diagnosis is not None
    assert reordered_trace.final_diagnosis is not None
    assert _semantic_projection(reordered_trace.final_diagnosis) == (
        _semantic_projection(original_trace.final_diagnosis)
    )


def test_finding_evidence_and_same_layer_dag_order_invariance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    replay_case = load_replay_case(
        PHASE1_CASE_ROOT,
        "ad-partial-failure-complete",
    )
    baseline = _run(replay_case, DiagnosisVariantV2.FIXED_SPECIALIST_V2)
    assert baseline.final_diagnosis is not None
    evidence = tuple(
        item for record in baseline.tool_call_records for item in record.evidence
    )
    reordered_judgment = judge_diagnosis_v2(
        run_id=baseline.run_id,
        incident=replay_case.incident,
        findings=tuple(reversed(baseline.findings)),
        evidence=tuple(reversed(evidence)),
    ).result
    assert reordered_judgment == baseline.final_diagnosis

    monkeypatch.setattr(workflows, "_SOURCE_ORDER", tuple(reversed(workflows._SOURCE_ORDER)))
    reordered_graph = _run(
        replay_case,
        DiagnosisVariantV2.FIXED_SPECIALIST_V2,
    )
    assert reordered_graph.final_diagnosis is not None
    assert tuple(
        node.source for node in reordered_graph.admitted_graphs[0].initial_plan.nodes
    ) == tuple(reversed(baseline.investigated_sources))
    assert _semantic_projection(reordered_graph.final_diagnosis) == (
        _semantic_projection(baseline.final_diagnosis)
    )


@pytest.mark.parametrize("variant", tuple(DiagnosisVariantV2))
def test_unsupported_current_run_decoy_does_not_change_root_or_mechanism(
    variant: DiagnosisVariantV2,
) -> None:
    original = load_replay_case(
        PHASE1_CASE_ROOT,
        "ad-partial-failure-complete",
    )
    decoy = BackendObservation(
        service="auxiliary",
        started_at=original.incident.started_at,
        ended_at=original.incident.ended_at,
        observation_type="maintenance_notice",
        attributes=(),
        limitations=("No mechanism-bearing fields.",),
    )
    changes = ChangesObservationBatch(
        status=BackendStatus.AVAILABLE,
        observations=(*original.changes.observations, decoy),
        raw_artifact_indices=(*original.changes.raw_artifact_indices, 100),
        raw_artifact_filename=original.changes.raw_artifact_filename,
        raw_artifact_sha256=original.changes.raw_artifact_sha256,
    )
    mutated = original.model_copy(update={"changes": changes})

    baseline = _run(original, variant)
    with_decoy = _run(mutated, variant)

    assert baseline.final_diagnosis is not None
    assert with_decoy.final_diagnosis is not None
    assert _semantic_projection(with_decoy.final_diagnosis) == (
        _semantic_projection(baseline.final_diagnosis)
    )
    decoy_refs = {
        item.evidence_ref
        for record in with_decoy.tool_call_records
        for item in record.evidence
        if item.service == "auxiliary"
    }
    if not decoy_refs:
        assert EvidenceSource.CHANGES not in with_decoy.investigated_sources
    assert decoy_refs.isdisjoint(with_decoy.final_diagnosis.supporting_evidence)


@pytest.mark.parametrize("variant", tuple(DiagnosisVariantV2))
def test_empty_nonessential_source_never_fails_workflow(
    variant: DiagnosisVariantV2,
) -> None:
    original = load_replay_case(
        PHASE1_CASE_ROOT,
        "ad-partial-failure-complete",
    )
    empty_logs = LogsObservationBatch(
        status=BackendStatus.AVAILABLE,
        observations=(),
        raw_artifact_indices=(),
        raw_artifact_filename=original.logs.raw_artifact_filename,
        raw_artifact_sha256=original.logs.raw_artifact_sha256,
    )
    mutated = original.model_copy(update={"logs": empty_logs})

    baseline = _run(original, variant)
    emptied = _run(mutated, variant)

    assert baseline.final_diagnosis is not None
    assert emptied.status == "COMPLETED"
    assert emptied.final_diagnosis is not None
    assert emptied.final_diagnosis.decision in {
        baseline.final_diagnosis.decision,
        DiagnosisDecisionV2.NEED_MORE_EVIDENCE,
    }
    logs = next(
        item
        for item in emptied.source_observations
        if item.source is EvidenceSource.LOGS
    )
    assert logs.status.value == "EMPTY"


def test_production_has_no_development_case_id_fragments() -> None:
    forbidden = (
        "ad-partial-failure",
        "telemetry-insufficient",
        "frontend-decoy",
        "feature-freshness",
    )
    production_root = PROJECT_ROOT / "src/ecomsre/phase5a"
    matches = {
        path.relative_to(PROJECT_ROOT): fragment
        for path in production_root.glob("*.py")
        for fragment in forbidden
        if fragment in path.read_text(encoding="utf-8")
    }
    assert matches == {}
