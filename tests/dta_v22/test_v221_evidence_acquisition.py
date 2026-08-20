from __future__ import annotations

import pytest

from ecomsre.dta_v2.v22.action_catalog import (
    ActionCatalogV22,
    StaticTopologyV22,
    build_action_catalog_v22,
    build_default_tool_capability_registry_v22,
)
from ecomsre.dta_v2.v22.controller_contracts import (
    ABSTAIN_HYPOTHESIS_ID_V22,
    NO_ACTION_ID_V22,
    NO_INCIDENT_HYPOTHESIS_ID_V22,
    ControllerDecisionKindV22,
    ControllerDecisionV22,
)
from ecomsre.dta_v2.v22.controller_inputs import ControllerArmV22
from ecomsre.dta_v2.v22.practical_dataset import (
    load_practical_case_set_v22,
    materialize_practical_case_v22,
)
from ecomsre.dta_v2.v22.practical_runner import (
    PracticalRunStatusV22,
    execute_practical_case_v221,
)
from ecomsre.dta_v2.v22.read_contracts import EvidenceSourceV22
from ecomsre.dta_v2.v22.simple_provider import ProviderTurnOutcomeV22
from ecomsre.dta_v2.v22.evidence_acquisition_v221 import (
    TerminalExplorationDispositionV221,
    TerminalExplorationPolicyV221,
    evaluate_terminal_exploration_policy_v221,
)
from ecomsre.dta_v2.v22.memory import PredicateKindV22
from ecomsre.dta_v2.v22.predicates import MechanismV22


ROOT = __import__("pathlib").Path(__file__).resolve().parents[2]


def _catalog(*, remaining_budget: float = 3.0, exhaust_actions: bool = False) -> ActionCatalogV22:
    topology = StaticTopologyV22.build(
        services=("checkout", "payment"),
        edges=(("checkout", "payment"),),
    )
    registry = build_default_tool_capability_registry_v22()
    initial = build_action_catalog_v22(
        candidate_services=("checkout", "payment"),
        topology=topology,
        capability_registry=registry,
        executed_action_ids=(),
        remaining_budget=remaining_budget,
    )
    if not exhaust_actions:
        return initial
    return build_action_catalog_v22(
        candidate_services=("checkout", "payment"),
        topology=topology,
        capability_registry=registry,
        executed_action_ids=tuple(item.action_id for item in initial.registry_actions),
        remaining_budget=remaining_budget,
    )


@pytest.mark.parametrize(
    "decision",
    tuple(ControllerDecisionKindV22),
)
def test_legacy_policy_never_redirects(decision: ControllerDecisionKindV22) -> None:
    result = evaluate_terminal_exploration_policy_v221(
        policy=TerminalExplorationPolicyV221.LEGACY,
        decision=decision,
        session_read_dispatches=0,
        action_catalog=_catalog(),
        remaining_evidence_budget=3.0,
        policy_redirect_used=False,
    )

    assert result is TerminalExplorationDispositionV221.ALLOW


def test_gate_redirects_only_first_abstain_while_evidence_action_remains() -> None:
    result = evaluate_terminal_exploration_policy_v221(
        policy=TerminalExplorationPolicyV221.MIN_ONE_ADAPTIVE_READ_BEFORE_ABSTAIN,
        decision=ControllerDecisionKindV22.ABSTAIN,
        session_read_dispatches=0,
        action_catalog=_catalog(),
        remaining_evidence_budget=3.0,
        policy_redirect_used=False,
    )

    assert result is TerminalExplorationDispositionV221.PREMATURE_ABSTENTION
    assert not hasattr(result, "action_id")


@pytest.mark.parametrize(
    ("decision", "read_dispatches", "remaining_budget", "redirect_used", "exhaust_actions"),
    (
        (ControllerDecisionKindV22.READ, 0, 3.0, False, False),
        (ControllerDecisionKindV22.COMMIT, 0, 3.0, False, False),
        (ControllerDecisionKindV22.NO_INCIDENT, 0, 3.0, False, False),
        (ControllerDecisionKindV22.ABSTAIN, 1, 3.0, False, False),
        (ControllerDecisionKindV22.ABSTAIN, 0, 3.0, False, True),
        (ControllerDecisionKindV22.ABSTAIN, 0, 0.0, False, False),
        (ControllerDecisionKindV22.ABSTAIN, 0, 3.0, True, False),
    ),
)
def test_gate_allows_every_non_gate_condition(
    decision: ControllerDecisionKindV22,
    read_dispatches: int,
    remaining_budget: float,
    redirect_used: bool,
    exhaust_actions: bool,
) -> None:
    result = evaluate_terminal_exploration_policy_v221(
        policy=TerminalExplorationPolicyV221.MIN_ONE_ADAPTIVE_READ_BEFORE_ABSTAIN,
        decision=decision,
        session_read_dispatches=read_dispatches,
        action_catalog=_catalog(
            remaining_budget=remaining_budget,
            exhaust_actions=exhaust_actions,
        ),
        remaining_evidence_budget=remaining_budget,
        policy_redirect_used=redirect_used,
    )

    assert result is TerminalExplorationDispositionV221.ALLOW


def _case(case_id: str):
    case_set = load_practical_case_set_v22(
        ROOT / "config/dta-v22-sprint/development/cases.json"
    )
    spec = next(item for item in case_set.cases if item.case_id == case_id)
    return materialize_practical_case_v22(spec=spec, repository_root=ROOT)


def _outcome(decision: ControllerDecisionV22) -> ProviderTurnOutcomeV22:
    return ProviderTurnOutcomeV22(
        decision=decision,
        first_pass_protocol_success=True,
        post_repair_protocol_success=True,
        semantic_repair_used=False,
        provider_calls=1,
        transport_retry_count=0,
        input_tokens=10,
        output_tokens=5,
        total_tokens=15,
        latency_ms=1.0,
    )


def _abstain() -> ControllerDecisionV22:
    return ControllerDecisionV22(
        decision=ControllerDecisionKindV22.ABSTAIN,
        working_hypothesis_id=ABSTAIN_HYPOTHESIS_ID_V22,
        action_id=NO_ACTION_ID_V22,
        supporting_evidence_refs=(),
        contradicting_evidence_refs=(),
    )


class _RedirectToReadScript:
    def __init__(self) -> None:
        self.initial_input_sha256: str | None = None
        self.feedback_input_sha256: str | None = None
        self.normal_calls = 0
        self.feedback_calls = 0

    def complete_turn_v221(self, *, turn_input: object, **kwargs: object) -> ProviderTurnOutcomeV22:
        del kwargs
        self.normal_calls += 1
        if self.normal_calls == 1:
            self.initial_input_sha256 = turn_input.input_sha256  # type: ignore[attr-defined]
            return _outcome(_abstain())
        hypothesis = next(
            item
            for item in turn_input.hypothesis_catalog.hypotheses  # type: ignore[attr-defined]
            if item.target_service == "payment"
            and item.mechanism is MechanismV22.CONFIGURATION_ERROR
        )
        predicates = turn_input.salient_memory.predicates  # type: ignore[attr-defined]
        required = {
            PredicateKindV22.METRIC_ERROR_RATE_STRONG,
            PredicateKindV22.TRACE_FIRST_ERROR,
        }
        selected = tuple(item for item in predicates if item.predicate_kind in required)
        return _outcome(
            ControllerDecisionV22(
                decision=ControllerDecisionKindV22.COMMIT,
                working_hypothesis_id=hypothesis.hypothesis_id,
                action_id=NO_ACTION_ID_V22,
                supporting_evidence_refs=tuple(
                    sorted({ref for item in selected for ref in item.evidence_refs})
                ),
                contradicting_evidence_refs=(),
            )
        )

    def complete_policy_redirect_turn_v221(
        self, *, turn_input: object, **kwargs: object
    ) -> ProviderTurnOutcomeV22:
        del kwargs
        self.feedback_calls += 1
        self.feedback_input_sha256 = turn_input.input_sha256  # type: ignore[attr-defined]
        action = next(
            item
            for item in turn_input.action_catalog.actions  # type: ignore[attr-defined]
            if item.source is EvidenceSourceV22.TRACES
        )
        hypothesis = next(
            item
            for item in turn_input.hypothesis_catalog.hypotheses  # type: ignore[attr-defined]
            if item.target_service is not None
        )
        return _outcome(
            ControllerDecisionV22(
                decision=ControllerDecisionKindV22.READ,
                working_hypothesis_id=hypothesis.hypothesis_id,
                action_id=action.action_id,
                supporting_evidence_refs=(),
                contradicting_evidence_refs=(),
            )
        )

    def complete_repair_turn(self, **kwargs: object) -> ProviderTurnOutcomeV22:
        del kwargs
        raise AssertionError("policy redirect must not use semantic repair")


class _RedirectToNoIncidentScript(_RedirectToReadScript):
    def complete_policy_redirect_turn_v221(
        self, *, turn_input: object, **kwargs: object
    ) -> ProviderTurnOutcomeV22:
        del kwargs
        self.feedback_calls += 1
        self.feedback_input_sha256 = turn_input.input_sha256  # type: ignore[attr-defined]
        return _outcome(
            ControllerDecisionV22(
                decision=ControllerDecisionKindV22.NO_INCIDENT,
                working_hypothesis_id=NO_INCIDENT_HYPOTHESIS_ID_V22,
                action_id=NO_ACTION_ID_V22,
                supporting_evidence_refs=(),
                contradicting_evidence_refs=(),
            )
        )


class _RepeatedAbstentionScript(_RedirectToReadScript):
    def complete_policy_redirect_turn_v221(
        self, *, turn_input: object, **kwargs: object
    ) -> ProviderTurnOutcomeV22:
        del kwargs
        self.feedback_calls += 1
        self.feedback_input_sha256 = turn_input.input_sha256  # type: ignore[attr-defined]
        return _outcome(_abstain())


@pytest.mark.parametrize("arm", tuple(ControllerArmV22))
def test_gate_redirects_to_one_real_read_for_flat_and_planner(arm: ControllerArmV22) -> None:
    provider = _RedirectToReadScript()

    result = execute_practical_case_v221(
        case=_case("d01"),
        arm=arm,
        provider=provider,
        terminal_exploration_policy=(
            TerminalExplorationPolicyV221.MIN_ONE_ADAPTIVE_READ_BEFORE_ABSTAIN
        ),
    )

    assert result.status is PracticalRunStatusV22.VALID_TERMINAL
    assert result.terminal == "DIAGNOSED"
    assert result.policy_redirects == 1
    assert result.repeated_premature_abstentions == 0
    assert result.adaptive_reads == 1
    assert len(result.adaptive_read_events) == 1
    assert result.adaptive_read_events[0].source is EvidenceSourceV22.TRACES
    assert result.provider_calls == 3
    assert result.provider_turns == 2
    assert result.semantic_repairs == 0
    assert provider.feedback_calls == 1
    assert provider.initial_input_sha256 == provider.feedback_input_sha256


def test_policy_redirect_to_valid_terminal_does_not_preadmit_or_mutate_ledger() -> None:
    provider = _RedirectToNoIncidentScript()

    result = execute_practical_case_v221(
        case=_case("d07"),
        arm=ControllerArmV22.PLANNER_LITE,
        provider=provider,
        terminal_exploration_policy=(
            TerminalExplorationPolicyV221.MIN_ONE_ADAPTIVE_READ_BEFORE_ABSTAIN
        ),
    )

    assert result.status is PracticalRunStatusV22.VALID_TERMINAL
    assert result.terminal == "NO_INCIDENT"
    assert result.policy_redirects == 1
    assert result.adaptive_reads == 0
    assert result.provider_calls == 2
    assert result.provider_turns == 1
    assert result.semantic_repairs == 0
    assert provider.initial_input_sha256 == provider.feedback_input_sha256


def test_repeated_premature_abstention_fails_once_without_a_loop() -> None:
    provider = _RepeatedAbstentionScript()

    result = execute_practical_case_v221(
        case=_case("d01"),
        arm=ControllerArmV22.FLAT_CANONICAL,
        provider=provider,
        terminal_exploration_policy=(
            TerminalExplorationPolicyV221.MIN_ONE_ADAPTIVE_READ_BEFORE_ABSTAIN
        ),
    )

    assert result.status is PracticalRunStatusV22.PROTOCOL_FAILED
    assert result.safe_error_code == "PREMATURE_ABSTENTION_REPEATED"
    assert result.policy_redirects == 1
    assert result.repeated_premature_abstentions == 1
    assert result.adaptive_reads == 0
    assert result.provider_calls == 2
    assert result.provider_turns == 0
    assert provider.normal_calls == 1
    assert provider.feedback_calls == 1
