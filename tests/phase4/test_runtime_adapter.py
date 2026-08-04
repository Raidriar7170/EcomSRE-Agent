from __future__ import annotations

from pathlib import Path

import pytest

from ecomsre.backends.replay import load_replay_case
from ecomsre.phase2.contracts import Phase2Variant
from ecomsre.phase2.workflows import (
    execute_replay_specialists,
    prepare_specialist_execution,
    specialist_tool_audits,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
VISIBLE_ROOT = PROJECT_ROOT / "config/phase4/replay-cases/agent-visible"


@pytest.mark.parametrize(
    "variant",
    (
        Phase2Variant.FIXED_SPECIALIST_WORKFLOW,
        Phase2Variant.DYNAMIC_MULTI_AGENT,
    ),
)
def test_phase4_reuses_phase2_specialists_before_any_phase2_judge(
    variant: Phase2Variant,
) -> None:
    case = load_replay_case(VISIBLE_ROOT, "search-feature-freshness-lag-complete")
    boundary = prepare_specialist_execution(
        project_root=PROJECT_ROOT,
        replay_case=case,
        variant=variant,
        namespace="phase4-domain",
    )
    execute_replay_specialists(boundary)

    assert boundary.graph is not None
    assert boundary.judge_capacity_slot_id is not None
    assert boundary.specialist_outcomes
    assert boundary.successful_dispatches
    assert {item.source.value for item in boundary.evidence_store.snapshot()} >= {
        "METRICS",
        "LOGS",
    }
    assert all(
        item.run_id == boundary.run_id
        for item in boundary.evidence_store.snapshot()
    )
    assert all(
        outcome.finding.run_id == boundary.run_id
        for outcome in boundary.specialist_outcomes
    )
    operations = tuple(record.operation.value for record in boundary.adapter.audit_records)
    assert "FIRST_JUDGE_MODEL" not in operations
    assert "FINAL_JUDGE_MODEL" not in operations
    assert boundary.ledger.snapshot().charged_tool_calls == len(
        boundary.successful_dispatches
    )
    audits = specialist_tool_audits(boundary)
    assert len(audits) == len(boundary.successful_dispatches)
    assert all(audit.run_id == boundary.run_id for audit in audits)
    assert all(audit.variant is variant for audit in audits)


def test_specialist_boundary_is_single_use() -> None:
    case = load_replay_case(VISIBLE_ROOT, "ranking-change-with-normal-search-sli")
    boundary = prepare_specialist_execution(
        project_root=PROJECT_ROOT,
        replay_case=case,
        variant=Phase2Variant.FIXED_SPECIALIST_WORKFLOW,
        namespace="phase4-domain",
    )
    execute_replay_specialists(boundary)
    with pytest.raises(RuntimeError, match="already executed"):
        execute_replay_specialists(boundary)
