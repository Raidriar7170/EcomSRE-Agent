from __future__ import annotations

from pathlib import Path

import pytest

from scripts.ci.verify_dta_v223_historical_results import (
    DEFAULT_MANIFEST,
    verify_historical_results_v223,
)
from ecomsre.dta_v2.v22.dispatch_utility_audit_v223 import (
    audit_development_top1_v223,
)
from ecomsre.dta_v2.v22.no_incident_closure_v223 import (
    ClosureActionCandidateV223,
    ClosureOutcomeClassV223,
    NoIncidentClosureModeV223,
    evaluate_no_incident_closure_v223,
    initial_no_incident_closure_state_v223,
    record_no_incident_closure_attempt_v223,
)


ROOT = Path(__file__).resolve().parents[2]
DEVELOPMENT = ROOT / "config/dta-v22-2/evaluation"


def test_v223_binds_every_merged_v22_result_byte() -> None:
    assert verify_historical_results_v223(
        repository_root=ROOT,
        manifest_path=DEFAULT_MANIFEST,
    ) == 23


def test_v223_historical_verifier_fails_closed_on_drift(tmp_path: Path) -> None:
    result_root = tmp_path / "repo"
    result_root.mkdir()
    relative = "docs/results/dta-v22-2-gap-routing-evaluation.json"
    target = result_root / relative
    target.parent.mkdir(parents=True)
    target.write_bytes((ROOT / relative).read_bytes() + b"\n")

    with pytest.raises(ValueError, match="historical DTA v2.2.3 result drift"):
        verify_historical_results_v223(
            repository_root=result_root,
            manifest_path=DEFAULT_MANIFEST,
        )


def test_v223_development_top1_gate_passes_without_runtime_truth() -> None:
    report = audit_development_top1_v223(
        repository_root=ROOT,
        case_set_path=DEVELOPMENT / "cases.json",
        truth_path=DEVELOPMENT / "truth.json",
    )
    assert report.gate.turn_zero_recall >= 0.75
    assert report.gate.post_empty_read_recall >= 0.70
    assert report.gate.gate_passed is True
    assert report.ranking_repairs_used == 1
    assert report.oracle_visible_to_runtime is False
    assert report.oracle_visible_to_provider is False
    assert all(item.alpha == item.beta == 1 for item in report.predicate_yield_priors)
    assert all(item.trials > 0 for item in report.predicate_yield_priors)


def _closure_action(*, relevant: bool = True, executable: bool = True):
    return ClosureActionCandidateV223(
        action_id="a:resources:email",
        rank_ordinal=1,
        executable=executable,
        shortest_clauses_completable=1 if relevant else 0,
    )


def test_v223_legacy_no_incident_behavior_is_unchanged() -> None:
    state = evaluate_no_incident_closure_v223(
        state=initial_no_incident_closure_state_v223(
            NoIncidentClosureModeV223.LEGACY
        ),
        legacy_no_incident_exposed=True,
        remaining_evidence_budget=3.0,
        ranked_actions=(_closure_action(),),
    )
    assert state.closure_required is False
    assert state.no_incident_withheld is False


def test_v223_closed_mode_withholds_until_one_gap_relevant_empty_read() -> None:
    state = evaluate_no_incident_closure_v223(
        state=initial_no_incident_closure_state_v223(
            NoIncidentClosureModeV223.ONE_GAP_RELEVANT_READ
        ),
        legacy_no_incident_exposed=True,
        remaining_evidence_budget=3.0,
        ranked_actions=(_closure_action(),),
    )
    assert state.closure_required is True
    assert state.no_incident_withheld is True

    irrelevant = record_no_incident_closure_attempt_v223(
        state=state,
        action=_closure_action(relevant=False),
        outcome_class=ClosureOutcomeClassV223.EMPTY_CAPTURED,
    )
    assert irrelevant.closure_attempted is False
    assert irrelevant.closure_satisfied is False
    assert irrelevant.no_incident_withheld is True

    closed = record_no_incident_closure_attempt_v223(
        state=state,
        action=_closure_action(),
        outcome_class=ClosureOutcomeClassV223.EMPTY_CAPTURED,
    )
    assert closed.closure_attempted is True
    assert closed.closure_satisfied is True
    assert closed.no_incident_withheld is False
    assert closed.closure_action_rank == 1


def test_v223_predicate_yield_and_source_failure_have_distinct_admission_effects() -> None:
    required = evaluate_no_incident_closure_v223(
        state=initial_no_incident_closure_state_v223(
            NoIncidentClosureModeV223.ONE_GAP_RELEVANT_READ
        ),
        legacy_no_incident_exposed=True,
        remaining_evidence_budget=3.0,
        ranked_actions=(_closure_action(),),
    )
    yielded = record_no_incident_closure_attempt_v223(
        state=required,
        action=_closure_action(),
        outcome_class=ClosureOutcomeClassV223.PREDICATE_YIELD,
    )
    assert yielded.closure_satisfied is True
    assert yielded.closure_predicate_yield is True
    assert evaluate_no_incident_closure_v223(
        state=yielded,
        legacy_no_incident_exposed=False,
        remaining_evidence_budget=1.5,
        ranked_actions=(),
    ).no_incident_withheld is False

    failed = record_no_incident_closure_attempt_v223(
        state=required,
        action=_closure_action(),
        outcome_class=ClosureOutcomeClassV223.SOURCE_FAILURE,
    )
    assert failed.closure_attempted is True
    assert failed.closure_satisfied is False
    assert failed.no_incident_withheld is True
    after_failure = evaluate_no_incident_closure_v223(
        state=failed,
        legacy_no_incident_exposed=True,
        remaining_evidence_budget=1.5,
        ranked_actions=(_closure_action(),),
    )
    assert after_failure.closure_required is False
    assert after_failure.no_incident_withheld is True
    with pytest.raises(ValueError, match="already attempted"):
        record_no_incident_closure_attempt_v223(
            state=failed,
            action=_closure_action(),
            outcome_class=ClosureOutcomeClassV223.EMPTY_CAPTURED,
        )


def test_v223_closure_is_not_forced_without_budget_or_completable_gap() -> None:
    for budget, actions in ((0.0, (_closure_action(),)), (3.0, (_closure_action(relevant=False),))):
        state = evaluate_no_incident_closure_v223(
            state=initial_no_incident_closure_state_v223(
                NoIncidentClosureModeV223.ONE_GAP_RELEVANT_READ
            ),
            legacy_no_incident_exposed=True,
            remaining_evidence_budget=budget,
            ranked_actions=actions,
        )
        assert state.closure_required is False
        assert state.no_incident_withheld is False
