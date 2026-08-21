from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Mapping

import pytest

from scripts.ci.verify_dta_v223_historical_results import (
    DEFAULT_MANIFEST,
    verify_historical_results_v223,
)
from ecomsre.dta_v2.v22.admission_dispatch_campaign_v223 import (
    StudyCombinationV223,
    balanced_combination_order_v223,
    execute_admission_dispatch_case_v223,
    load_frozen_predicate_yield_priors_v223,
    run_admission_dispatch_campaign_v223,
)
from ecomsre.dta_v2.v22.admission_dispatch_scorer_v223 import (
    score_admission_dispatch_study_v223,
)
from ecomsre.dta_v2.v22.dispatch_utility_audit_v223 import (
    audit_development_top1_v223,
)
from ecomsre.dta_v2.v22.dispatch_policy_v223 import (
    AutomaticDispatchUnavailableV223,
    EvidenceDispatchModeV223,
    automatic_dispatch_v223,
)
from ecomsre.dta_v2.v22.evidence_utility_audit_v222 import audit_case_set_v222
from ecomsre.dta_v2.v22.memory import PredicateKindV22
from ecomsre.dta_v2.v22.practical_dataset import load_practical_case_set_v22
from ecomsre.dta_v2.v22.no_incident_closure_v223 import (
    ClosureActionCandidateV223,
    ClosureOutcomeClassV223,
    NoIncidentClosureModeV223,
    evaluate_no_incident_closure_v223,
    initial_no_incident_closure_state_v223,
    record_no_incident_closure_attempt_v223,
)
from ecomsre.dta_v2.v22.offline_simulation_v223 import (
    simulate_development_offline_v223,
)
from ecomsre.dta_v2.v22.predicates import RequirementServiceBindingV22
from ecomsre.dta_v2.v22.read_contracts import EvidenceSourceV22
from ecomsre.dta_v2.v22.selection_provider_v222 import (
    FUNCTION_NAME_V222,
    SelectionAliasTableV222,
    SelectionDecisionV222,
    SelectionProviderOutcomeV222,
    SelectionProviderProtocolFailureV222,
    SelectionTurnRequestV222,
)
from ecomsre.dta_v2.v22.selection_provider_v223 import SelectionProviderV223
from ecomsre.dta_v2.v22.simple_provider import ProviderTransportErrorV22
from ecomsre.model.gateway import OpenAICompatibleConfig


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
    assert report.ranking_repairs_used == 2
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


def _dispatch_fixture(*, executable: bool = True):
    action = SimpleNamespace(
        action_id="a:resources:email",
        source=EvidenceSourceV22.RESOURCES,
        target_services=("email",),
    )
    peer = SimpleNamespace(
        action_id="a:traces:email",
        source=EvidenceSourceV22.TRACES,
        target_services=("email",),
    )
    ranking = (
        SimpleNamespace(action=action, rank_ordinal=1),
        SimpleNamespace(action=peer, rank_ordinal=2),
    )
    routing = SimpleNamespace(
        ranking=ranking,
        actions=(action, peer) if executable else (peer,),
    )
    gap = SimpleNamespace(
        predicate_kind=PredicateKindV22.RESOURCE_MEMORY_GROWTH_STRONG,
        target_service="email",
        parent_service=None,
        service_binding=RequirementServiceBindingV22.TARGET,
        require_exact_parent=False,
    )
    trace_gap = SimpleNamespace(
        predicate_kind=PredicateKindV22.TRACE_FIRST_ERROR,
        target_service="email",
        parent_service=None,
        service_binding=RequirementServiceBindingV22.TARGET,
        require_exact_parent=False,
    )
    graph = SimpleNamespace(
        hypotheses=(
            SimpleNamespace(
                complete=False,
                hypothesis_id="h:email:memory-leak",
                minimum_missing_count=1,
                clauses=(
                    SimpleNamespace(
                        missing_count=1,
                        missing_requirements=(gap,),
                    ),
                ),
            ),
            SimpleNamespace(
                complete=False,
                hypothesis_id="h:email:cpu-saturation",
                minimum_missing_count=2,
                clauses=(
                    SimpleNamespace(
                        missing_count=2,
                        missing_requirements=(gap, trace_gap),
                    ),
                ),
            ),
        )
    )
    return routing, graph


def test_v223_runtime_top1_dispatches_exact_ranking_zero_without_truth() -> None:
    routing, graph = _dispatch_fixture()
    decision = automatic_dispatch_v223(
        mode=EvidenceDispatchModeV223.RUNTIME_TOP1,
        routing=routing,
        gap_graph=graph,
        terminal_ids=(),
    )
    assert decision is not None
    assert decision.action_id == "a:resources:email"
    assert decision.ranking_action_ids[0] == decision.action_id
    assert decision.focus_hypothesis_id == "h:email:memory-leak"
    assert decision.truth_consulted is False


def test_v223_runtime_top1_never_dispatches_when_terminal_exists() -> None:
    routing, graph = _dispatch_fixture()
    assert automatic_dispatch_v223(
        mode=EvidenceDispatchModeV223.RUNTIME_TOP1,
        routing=routing,
        gap_graph=graph,
        terminal_ids=("terminal:no-incident",),
    ) is None
    assert automatic_dispatch_v223(
        mode=EvidenceDispatchModeV223.MODEL_TOP4,
        routing=routing,
        gap_graph=graph,
        terminal_ids=(),
    ) is None


def test_v223_runtime_top1_fails_closed_if_top_rank_is_masked() -> None:
    routing, graph = _dispatch_fixture(executable=False)
    with pytest.raises(AutomaticDispatchUnavailableV223, match="TOP1_ACTION_NOT_EXECUTABLE"):
        automatic_dispatch_v223(
            mode=EvidenceDispatchModeV223.RUNTIME_TOP1,
            routing=routing,
            gap_graph=graph,
            terminal_ids=(),
        )


class _CanonicalProviderV223:
    def complete_turn(self, *, request, run_id: str, max_protocol_repairs: int):
        del run_id, max_protocol_repairs
        if request.aliases.terminals:
            selected = request.aliases.terminals[0]
            decision = SelectionDecisionV222(
                selection_alias=selected.alias,
                focus_alias="NONE",
                action_id=None,
                terminal_id=selected.canonical_id,
                focus_hypothesis_id=None,
            )
        else:
            selected = request.aliases.actions[0]
            focus = request.aliases.hypotheses[0]
            decision = SelectionDecisionV222(
                selection_alias=selected.alias,
                focus_alias=focus.alias,
                action_id=selected.canonical_id,
                terminal_id=None,
                focus_hypothesis_id=focus.canonical_id,
            )
        return SelectionProviderOutcomeV222(
            decision=decision,
            first_pass_protocol_success=True,
            post_repair_protocol_success=True,
            protocol_repairs=0,
            provider_calls=1,
            transport_retry_count=0,
            input_tokens=0,
            output_tokens=0,
            total_tokens=0,
            latency_ms=0.0,
        )


def test_v223_factorial_combinations_differ_only_in_declared_factors() -> None:
    assert {
        (item.dispatch_mode, item.closure_mode) for item in StudyCombinationV223
    } == {
        (
            EvidenceDispatchModeV223.MODEL_TOP4,
            NoIncidentClosureModeV223.LEGACY,
        ),
        (
            EvidenceDispatchModeV223.MODEL_TOP4,
            NoIncidentClosureModeV223.ONE_GAP_RELEVANT_READ,
        ),
        (
            EvidenceDispatchModeV223.RUNTIME_TOP1,
            NoIncidentClosureModeV223.LEGACY,
        ),
        (
            EvidenceDispatchModeV223.RUNTIME_TOP1,
            NoIncidentClosureModeV223.ONE_GAP_RELEVANT_READ,
        ),
    }
    positions = {item: [] for item in StudyCombinationV223}
    for index in range(4):
        for position, item in enumerate(balanced_combination_order_v223(index), 1):
            positions[item].append(position)
    assert all(sorted(values) == [1, 2, 3, 4] for values in positions.values())


def test_v223_auto_closed_skips_provider_action_selection_and_records_closure() -> None:
    case_set = load_practical_case_set_v22(DEVELOPMENT / "cases.json")
    spec = next(item for item in case_set.cases if item.case_id == "e05")
    priors = load_frozen_predicate_yield_priors_v223(
        ROOT / "config/dta-v22-3/development-predicate-yield-prior.json"
    )
    run = execute_admission_dispatch_case_v223(
        spec=spec,
        repository_root=ROOT,
        combination=StudyCombinationV223.AUTO_CLOSED,
        provider=_CanonicalProviderV223(),
        predicate_yield_priors=priors,
    )
    assert run.uncaught_exceptions == 0
    assert run.agent_writes == 0
    assert run.automatic_top1_dispatches >= 1
    assert run.model_action_selections == 0
    assert run.closure_required_count >= 1
    assert run.closure_state.closure_attempted is True
    assert run.adaptive_read_events[0].action_id == run.turn_zero_top4_action_ids[0]


def test_v223_offline_oracle_and_top1_gates_pass() -> None:
    report = simulate_development_offline_v223(
        repository_root=ROOT,
        case_set_path=DEVELOPMENT / "cases.json",
        truth_path=DEVELOPMENT / "truth.json",
        predicate_yield_priors=load_frozen_predicate_yield_priors_v223(
            ROOT / "config/dta-v22-3/development-predicate-yield-prior.json"
        ),
    )
    assert report.oracle_gate_passed is True
    assert report.top1_resource_silent_accuracy >= 0.75
    assert report.top1_resources_before_no_incident is True
    assert report.top1_premature_no_incident_cases == 0
    assert report.top1_control_accuracy >= 0.80
    assert report.uncaught_exceptions == 0
    assert report.agent_writes == 0
    assert report.top1_gate_passed is True


def test_v223_factorial_grid_denominators_and_effects_are_bound() -> None:
    priors = load_frozen_predicate_yield_priors_v223(
        ROOT / "config/dta-v22-3/development-predicate-yield-prior.json"
    )
    campaign = run_admission_dispatch_campaign_v223(
        repository_root=ROOT,
        case_set_path=DEVELOPMENT / "cases.json",
        truth_path=DEVELOPMENT / "truth.json",
        provider=_CanonicalProviderV223(),
        predicate_yield_priors=priors,
    )
    assert len(campaign.runs) == 64
    assert campaign.same_case_bytes_all_combinations is True
    assert campaign.truth_loaded_after_all_four_runs_per_case is True
    assert campaign.truth_load_count == 1
    scores = score_admission_dispatch_study_v223(
        runs=campaign.runs,
        truths=campaign.truths,
        utility_audit=audit_case_set_v222(
            repository_root=ROOT,
            case_set_path=DEVELOPMENT / "cases.json",
            truth_path=DEVELOPMENT / "truth.json",
        ),
        include_development_gate=True,
        include_interpretation=True,
    )
    assert all(item.total_runs == 16 for item in scores.combinations)
    assert all(item.incident_denominator == 10 for item in scores.combinations)
    assert all(item.no_incident_denominator == 3 for item in scores.combinations)
    assert all(item.abstention_denominator == 3 for item in scores.combinations)
    assert scores.development_gate is not None
    assert scores.development_gate.gate_passed is True
    assert scores.interpretation is not None
    assert scores.interpretation.admission_main_effect.extra_mean_reads >= 0
    assert scores.interpretation.dispatch_main_effect.provider_call_change <= 0


class _RecordingTransportV223:
    def __init__(self, outcomes: list[Mapping[str, object] | Exception]) -> None:
        self.outcomes = outcomes
        self.payloads: list[Mapping[str, object]] = []

    def post_json(self, *, url, headers, payload, timeout_seconds):
        del url, headers, timeout_seconds
        self.payloads.append(payload)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _provider_response_v223(selection: str, focus: str) -> dict[str, object]:
    return {
        "choices": [
            {
                "message": {
                    "tool_calls": [
                        {
                            "function": {
                                "name": FUNCTION_NAME_V222,
                                "arguments": json.dumps(
                                    {"selection": selection, "focus": focus}
                                ),
                            }
                        }
                    ]
                }
            }
        ]
    }


def _provider_request_v223() -> SelectionTurnRequestV222:
    return SelectionTurnRequestV222.build(
        system_prompt="read-only v2.2.3 smoke",
        aliases=SelectionAliasTableV222.build(
            hypothesis_ids=("h:payment:configuration-error",),
            action_ids=("a:traces:payment",),
            terminal_ids=("terminal:no-incident", "terminal:abstain"),
            evidence_refs=(),
        ),
        visible_state={"actions": ["A00"], "terminals": ["T00", "T01"]},
    )


def _selection_provider_v223(transport, sleeps: list[float] | None = None):
    return SelectionProviderV223(
        config=OpenAICompatibleConfig(
            base_url="https://provider.invalid/v1",
            api_key="test-secret",
            model="test-model",
        ),
        transport=transport,
        sleeper=(lambda _: None) if sleeps is None else sleeps.append,
        minimum_request_interval_seconds=0,
    )


def test_v223_two_repairs_three_retries_and_valid_wrong_terminal_are_bounded() -> None:
    repaired = _RecordingTransportV223(
        [
            _provider_response_v223("BAD", "H00"),
            _provider_response_v223("BAD", "H00"),
            _provider_response_v223("A00", "H00"),
        ]
    )
    outcome = _selection_provider_v223(repaired).complete_turn(
        request=_provider_request_v223(), run_id="1" * 32
    )
    assert outcome.protocol_repairs == 2
    assert outcome.provider_calls == 3

    sleeps: list[float] = []
    retried = _RecordingTransportV223(
        [
            ProviderTransportErrorV22("HTTP_429", status_code=429),
            ProviderTransportErrorV22("HTTP_503", status_code=503),
            ProviderTransportErrorV22("CONNECTION_ERROR"),
            _provider_response_v223("T00", "NONE"),
        ]
    )
    retry_outcome = _selection_provider_v223(retried, sleeps).complete_turn(
        request=_provider_request_v223(), run_id="2" * 32
    )
    assert retry_outcome.transport_retry_count == 3
    assert sleeps == [5.0, 15.0, 30.0]
    assert all(payload == retried.payloads[0] for payload in retried.payloads)

    valid_wrong = _RecordingTransportV223(
        [_provider_response_v223("T00", "NONE")]
    )
    wrong_outcome = _selection_provider_v223(valid_wrong).complete_turn(
        request=_provider_request_v223(), run_id="3" * 32
    )
    assert wrong_outcome.decision.terminal_id == "terminal:no-incident"
    assert wrong_outcome.provider_calls == 1
    assert wrong_outcome.protocol_repairs == 0

    exhausted = _RecordingTransportV223(
        [_provider_response_v223("BAD", "H00")] * 3
    )
    with pytest.raises(SelectionProviderProtocolFailureV222) as captured:
        _selection_provider_v223(exhausted).complete_turn(
            request=_provider_request_v223(), run_id="4" * 32
        )
    assert captured.value.protocol_repairs == 2
    assert captured.value.provider_calls == 3
