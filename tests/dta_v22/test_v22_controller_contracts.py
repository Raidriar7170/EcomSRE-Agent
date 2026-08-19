from __future__ import annotations

import inspect
from typing import Any

import pytest
from pydantic import ValidationError

from ecomsre.dta_v2.v22.action_catalog import (
    StaticTopologyV22,
    build_action_catalog_v22,
    build_default_tool_capability_registry_v22,
)
from ecomsre.dta_v2.v22.controller_contracts import (
    ABSTAIN_HYPOTHESIS_ID_V22,
    NO_ACTION_ID_V22,
    NO_INCIDENT_HYPOTHESIS_ID_V22,
    BeliefStatusV22,
    ControllerDecisionKindV22,
    ControllerDecisionV22,
    HypothesisCatalogV22,
    build_belief_ledger_view_v22,
    build_hypothesis_catalog_v22,
    initialize_belief_ledger_v22,
    record_belief_turn_v22,
)
from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22


def _catalog():
    topology = StaticTopologyV22.build(
        services=("checkout", "payment"),
        edges=(("checkout", "payment"),),
    )
    return build_action_catalog_v22(
        candidate_services=("checkout", "payment"),
        topology=topology,
        capability_registry=build_default_tool_capability_registry_v22(),
        executed_action_ids=(),
        remaining_budget=3.0,
    )


def _decision(
    *,
    decision: ControllerDecisionKindV22,
    hypothesis_id: str,
    action_id: str,
    support: tuple[str, ...] = (),
    contradict: tuple[str, ...] = (),
) -> ControllerDecisionV22:
    return ControllerDecisionV22(
        decision=decision,
        working_hypothesis_id=hypothesis_id,
        action_id=action_id,
        supporting_evidence_refs=support,
        contradicting_evidence_refs=contradict,
    )


def test_hypothesis_catalog_is_closed_truth_independent_and_complete() -> None:
    catalog = build_hypothesis_catalog_v22(
        candidate_services=("checkout", "payment")
    )
    assert len(catalog.hypotheses) == 12
    assert catalog.hypotheses[-2].hypothesis_id == NO_INCIDENT_HYPOTHESIS_ID_V22
    assert catalog.hypotheses[-1].hypothesis_id == ABSTAIN_HYPOTHESIS_ID_V22
    assert {
        item.hypothesis_id
        for item in catalog.hypotheses
        if item.target_service == "payment"
    } == {
        "h:payment:configuration-error",
        "h:payment:service-unavailable",
        "h:payment:memory-leak",
        "h:payment:cpu-saturation",
        "h:payment:dependency-latency",
    }
    assert set(inspect.signature(build_hypothesis_catalog_v22).parameters) == {
        "candidate_services"
    }

    forged_draft = catalog.model_copy(update={"hypotheses": catalog.hypotheses[:-1]})
    with pytest.raises(ValueError, match="closed ontology"):
        HypothesisCatalogV22.model_validate(
            forged_draft.model_copy(
                update={
                    "catalog_sha256": semantic_sha256_v22(
                        forged_draft.model_dump(
                            mode="json",
                            exclude={"catalog_sha256"},
                        )
                    )
                }
            ).model_dump(mode="python")
        )


def test_controller_decision_schema_is_shared_lightweight_and_fail_closed() -> None:
    action_id = _catalog().actions[0].action_id
    read = _decision(
        decision=ControllerDecisionKindV22.READ,
        hypothesis_id="h:payment:configuration-error",
        action_id=action_id,
    )
    assert tuple(ControllerDecisionV22.model_fields) == (
        "decision",
        "working_hypothesis_id",
        "action_id",
        "supporting_evidence_refs",
        "contradicting_evidence_refs",
    )
    assert read.action_id == action_id
    assert not {
        "run_id",
        "turn_ordinal",
        "identity",
        "hash",
        "budget",
    }.intersection(ControllerDecisionV22.model_fields)

    no_incident = _decision(
        decision=ControllerDecisionKindV22.NO_INCIDENT,
        hypothesis_id=NO_INCIDENT_HYPOTHESIS_ID_V22,
        action_id=NO_ACTION_ID_V22,
    )
    abstain = _decision(
        decision=ControllerDecisionKindV22.ABSTAIN,
        hypothesis_id=ABSTAIN_HYPOTHESIS_ID_V22,
        action_id=NO_ACTION_ID_V22,
    )
    assert no_incident.action_id == abstain.action_id == NO_ACTION_ID_V22

    with pytest.raises(ValidationError, match="READ requires an action"):
        _decision(
            decision=ControllerDecisionKindV22.READ,
            hypothesis_id="h:payment:configuration-error",
            action_id=NO_ACTION_ID_V22,
        )
    with pytest.raises(ValidationError, match="non-READ decision"):
        _decision(
            decision=ControllerDecisionKindV22.COMMIT,
            hypothesis_id="h:payment:configuration-error",
            action_id=action_id,
        )
    with pytest.raises(ValidationError, match="No-Incident sentinel"):
        _decision(
            decision=ControllerDecisionKindV22.NO_INCIDENT,
            hypothesis_id="h:payment:configuration-error",
            action_id=NO_ACTION_ID_V22,
        )


def test_belief_ledger_derives_history_coverage_and_status_from_turns() -> None:
    hypotheses = build_hypothesis_catalog_v22(
        candidate_services=("checkout", "payment")
    )
    actions = _catalog()
    action = next(
        item for item in actions.actions if item.action_id == "a:logs:payment"
    )
    ledger = initialize_belief_ledger_v22(catalog=hypotheses)
    read = _decision(
        decision=ControllerDecisionKindV22.READ,
        hypothesis_id="h:payment:configuration-error",
        action_id=action.action_id,
        support=("e:a:changes:payment:0:111111111111",),
    )
    ledger = record_belief_turn_v22(
        ledger=ledger,
        hypothesis_catalog=hypotheses,
        action_catalog=actions,
        decision=read,
        known_evidence_refs=("e:a:changes:payment:0:111111111111",),
    )
    assert ledger.current_working_hypothesis_id == read.working_hypothesis_id
    assert ledger.selected_hypothesis_ids == (read.working_hypothesis_id,)
    assert ledger.executed_action_ids == (action.action_id,)
    assert ledger.covered_capability_keys == action.coverage_keys
    assert ledger.turn_records[0].turn_ordinal == 1

    view = build_belief_ledger_view_v22(
        ledger=ledger,
        hypothesis_catalog=hypotheses,
    )
    belief = next(
        item
        for item in view.hypotheses
        if item.hypothesis_id == read.working_hypothesis_id
    )
    assert belief.status is BeliefStatusV22.PARTIALLY_SUPPORTED
    assert belief.supporting_evidence_refs == read.supporting_evidence_refs

    forged_data: dict[str, Any] = ledger.model_dump(mode="python")
    forged_data["executed_action_ids"] = ()
    forged_data["ledger_sha256"] = semantic_sha256_v22(
        {
            key: value
            for key, value in ledger.model_dump(mode="json").items()
            if key not in {"executed_action_ids", "ledger_sha256"}
        }
        | {"executed_action_ids": []}
    )
    with pytest.raises(ValueError, match="derived turn state"):
        type(ledger).model_validate(forged_data)


def test_belief_turn_rejects_stale_actions_unknown_refs_and_unknown_hypotheses() -> None:
    hypotheses = build_hypothesis_catalog_v22(
        candidate_services=("checkout", "payment")
    )
    actions = _catalog()
    ledger = initialize_belief_ledger_v22(catalog=hypotheses)
    action = actions.actions[0]
    unknown_ref = _decision(
        decision=ControllerDecisionKindV22.READ,
        hypothesis_id="h:payment:configuration-error",
        action_id=action.action_id,
        support=("e:a:logs:payment:0:222222222222",),
    )
    with pytest.raises(ValueError, match="outside current memory"):
        record_belief_turn_v22(
            ledger=ledger,
            hypothesis_catalog=hypotheses,
            action_catalog=actions,
            decision=unknown_ref,
            known_evidence_refs=(),
        )

    unknown_hypothesis = _decision(
        decision=ControllerDecisionKindV22.READ,
        hypothesis_id="h:payment:not-in-ontology",
        action_id=action.action_id,
    )
    with pytest.raises(ValueError, match="closed catalog"):
        record_belief_turn_v22(
            ledger=ledger,
            hypothesis_catalog=hypotheses,
            action_catalog=actions,
            decision=unknown_hypothesis,
            known_evidence_refs=(),
        )

    accepted = record_belief_turn_v22(
        ledger=ledger,
        hypothesis_catalog=hypotheses,
        action_catalog=actions,
        decision=_decision(
            decision=ControllerDecisionKindV22.READ,
            hypothesis_id="h:payment:configuration-error",
            action_id=action.action_id,
        ),
        known_evidence_refs=(),
    )
    with pytest.raises(ValueError, match="already executed"):
        record_belief_turn_v22(
            ledger=accepted,
            hypothesis_catalog=hypotheses,
            action_catalog=actions,
            decision=_decision(
                decision=ControllerDecisionKindV22.READ,
                hypothesis_id="h:payment:configuration-error",
                action_id=action.action_id,
            ),
            known_evidence_refs=(),
        )
