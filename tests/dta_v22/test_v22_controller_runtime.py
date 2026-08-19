from __future__ import annotations

import pytest

from ecomsre.dta_v2.v22.action_catalog import (
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
    ControllerProtocolErrorCodeV22,
    build_hypothesis_catalog_v22,
)
from ecomsre.dta_v2.v22.controller_inputs import ControllerArmV22
from ecomsre.dta_v2.v22.controller_runtime import (
    ControllerProtocolDispositionV22,
    ControllerSessionTerminalV22,
    PlanCorrectionV22,
    initialize_controller_session_v22,
    process_controller_decision_v22,
)
from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22


KNOWN_REF = "e:a:changes:payment:0:111111111111"


def _actions(*, executed_action_ids: tuple[str, ...] = ()):
    topology = StaticTopologyV22.build(
        services=("checkout", "payment"),
        edges=(("checkout", "payment"),),
    )
    return build_action_catalog_v22(
        candidate_services=("checkout", "payment"),
        topology=topology,
        capability_registry=build_default_tool_capability_registry_v22(),
        executed_action_ids=executed_action_ids,
        remaining_budget=3.0,
    )


def _read(*, action_id: str, hypothesis_id: str) -> ControllerDecisionV22:
    return ControllerDecisionV22(
        decision=ControllerDecisionKindV22.READ,
        working_hypothesis_id=hypothesis_id,
        action_id=action_id,
        supporting_evidence_refs=(),
        contradicting_evidence_refs=(),
    )


def test_valid_read_authorizes_exactly_one_dispatch_and_updates_runtime_ledger() -> None:
    hypotheses = build_hypothesis_catalog_v22(
        candidate_services=("checkout", "payment")
    )
    actions = _actions()
    action = next(item for item in actions.actions if item.action_id == "a:logs:payment")
    session = initialize_controller_session_v22(
        arm=ControllerArmV22.PLANNER_LITE,
        hypothesis_catalog=hypotheses,
    )
    result = process_controller_decision_v22(
        session=session,
        raw_decision=_read(
            action_id=action.action_id,
            hypothesis_id="h:payment:configuration-error",
        ),
        hypothesis_catalog=hypotheses,
        action_catalog=actions,
        known_evidence_refs=(),
    )
    assert result.disposition is ControllerProtocolDispositionV22.ACCEPTED
    assert result.read_dispatch_authorized is True
    assert result.invalid_dispatches == 0
    assert result.session.provider_turns_used == 1
    assert result.session.read_dispatches == 1
    assert result.session.ledger.executed_action_ids == (action.action_id,)
    assert result.session.ledger.weighted_evidence_cost == action.weighted_cost
    assert result.session.terminal is ControllerSessionTerminalV22.ACTIVE


def test_flat_read_may_be_reactive_but_planner_read_requires_working_hypothesis() -> None:
    hypotheses = build_hypothesis_catalog_v22(
        candidate_services=("checkout", "payment")
    )
    actions = _actions()
    action = actions.actions[0]
    flat = initialize_controller_session_v22(
        arm=ControllerArmV22.FLAT_CANONICAL,
        hypothesis_catalog=hypotheses,
    )
    flat_result = process_controller_decision_v22(
        session=flat,
        raw_decision=_read(
            action_id=action.action_id,
            hypothesis_id=ABSTAIN_HYPOTHESIS_ID_V22,
        ),
        hypothesis_catalog=hypotheses,
        action_catalog=actions,
        known_evidence_refs=(),
    )
    assert flat_result.disposition is ControllerProtocolDispositionV22.ACCEPTED

    planner = initialize_controller_session_v22(
        arm=ControllerArmV22.PLANNER_LITE,
        hypothesis_catalog=hypotheses,
    )
    planner_result = process_controller_decision_v22(
        session=planner,
        raw_decision=_read(
            action_id=action.action_id,
            hypothesis_id=ABSTAIN_HYPOTHESIS_ID_V22,
        ),
        hypothesis_catalog=hypotheses,
        action_catalog=actions,
        known_evidence_refs=(),
    )
    assert (
        planner_result.disposition
        is ControllerProtocolDispositionV22.CORRECTION_REQUIRED
    )
    assert planner_result.error_code is ControllerProtocolErrorCodeV22.INVALID_DECISION_SHAPE
    assert planner_result.read_dispatch_authorized is False
    assert planner_result.invalid_dispatches == 0


@pytest.mark.parametrize(
    ("raw_decision", "expected_code"),
    (
        (
            {
                "decision": "READ",
                "working_hypothesis_id": "h:payment:configuration-error",
                "action_id": "a:not-in-registry:payment",
                "supporting_evidence_refs": [],
                "contradicting_evidence_refs": [],
            },
            ControllerProtocolErrorCodeV22.INVALID_ACTION_ID,
        ),
        (
            {
                "decision": "READ",
                "working_hypothesis_id": "h:payment:configuration-error",
                "action_id": "NONE",
                "supporting_evidence_refs": [],
                "contradicting_evidence_refs": [],
            },
            ControllerProtocolErrorCodeV22.INVALID_DECISION_SHAPE,
        ),
        (
            {
                "decision": "COMMIT",
                "working_hypothesis_id": "h:payment:configuration-error",
                "action_id": "NONE",
                "supporting_evidence_refs": ["e:a:logs:payment:0:222222222222"],
                "contradicting_evidence_refs": [],
            },
            ControllerProtocolErrorCodeV22.INVALID_EVIDENCE_REF,
        ),
    ),
)
def test_invalid_first_pass_returns_one_no_dispatch_correction(
    raw_decision: object,
    expected_code: ControllerProtocolErrorCodeV22,
) -> None:
    hypotheses = build_hypothesis_catalog_v22(
        candidate_services=("checkout", "payment")
    )
    actions = _actions()
    result = process_controller_decision_v22(
        session=initialize_controller_session_v22(
            arm=ControllerArmV22.PLANNER_LITE,
            hypothesis_catalog=hypotheses,
        ),
        raw_decision=raw_decision,
        hypothesis_catalog=hypotheses,
        action_catalog=actions,
        known_evidence_refs=(),
    )
    assert result.disposition is ControllerProtocolDispositionV22.CORRECTION_REQUIRED
    assert result.error_code is expected_code
    assert result.correction is not None
    assert result.correction.safe_error_code is expected_code
    assert result.correction.current_valid_action_ids == tuple(
        item.action_id for item in actions.actions
    )
    assert result.correction.remaining_evidence_budget == actions.remaining_budget
    assert result.correction.read_dispatches == 0
    assert result.correction.write_authority == 0
    assert result.session.provider_turns_used == 1
    assert result.session.read_dispatches == 0


def test_corrected_decision_is_accepted_but_second_invalid_decision_fails() -> None:
    hypotheses = build_hypothesis_catalog_v22(
        candidate_services=("checkout", "payment")
    )
    actions = _actions()
    first = process_controller_decision_v22(
        session=initialize_controller_session_v22(
            arm=ControllerArmV22.PLANNER_LITE,
            hypothesis_catalog=hypotheses,
        ),
        raw_decision={"decision": "READ"},
        hypothesis_catalog=hypotheses,
        action_catalog=actions,
        known_evidence_refs=(),
    )
    assert first.correction is not None
    action = actions.actions[0]
    corrected = process_controller_decision_v22(
        session=first.session,
        raw_decision=_read(
            action_id=action.action_id,
            hypothesis_id="h:payment:configuration-error",
        ),
        hypothesis_catalog=hypotheses,
        action_catalog=actions,
        known_evidence_refs=(),
    )
    assert corrected.disposition is ControllerProtocolDispositionV22.ACCEPTED
    assert corrected.session.provider_turns_used == 2
    assert corrected.session.read_dispatches == 1

    second = process_controller_decision_v22(
        session=first.session,
        raw_decision={"decision": "READ"},
        hypothesis_catalog=hypotheses,
        action_catalog=actions,
        known_evidence_refs=(),
    )
    assert second.disposition is ControllerProtocolDispositionV22.FAILED
    assert second.correction is None
    assert second.session.terminal is ControllerSessionTerminalV22.FAILED
    assert second.session.provider_turns_used == 2
    assert second.session.read_dispatches == 0
    assert second.invalid_dispatches == 0


@pytest.mark.parametrize(
    "decision",
    (
        ControllerDecisionV22(
            decision=ControllerDecisionKindV22.COMMIT,
            working_hypothesis_id="h:payment:configuration-error",
            action_id=NO_ACTION_ID_V22,
            supporting_evidence_refs=(KNOWN_REF,),
            contradicting_evidence_refs=(),
        ),
        ControllerDecisionV22(
            decision=ControllerDecisionKindV22.NO_INCIDENT,
            working_hypothesis_id=NO_INCIDENT_HYPOTHESIS_ID_V22,
            action_id=NO_ACTION_ID_V22,
            supporting_evidence_refs=(),
            contradicting_evidence_refs=(),
        ),
        ControllerDecisionV22(
            decision=ControllerDecisionKindV22.ABSTAIN,
            working_hypothesis_id=ABSTAIN_HYPOTHESIS_ID_V22,
            action_id=NO_ACTION_ID_V22,
            supporting_evidence_refs=(),
            contradicting_evidence_refs=(),
        ),
    ),
)
def test_terminal_decisions_complete_without_read_dispatch(
    decision: ControllerDecisionV22,
) -> None:
    hypotheses = build_hypothesis_catalog_v22(
        candidate_services=("checkout", "payment")
    )
    result = process_controller_decision_v22(
        session=initialize_controller_session_v22(
            arm=ControllerArmV22.PLANNER_LITE,
            hypothesis_catalog=hypotheses,
        ),
        raw_decision=decision,
        hypothesis_catalog=hypotheses,
        action_catalog=_actions(),
        known_evidence_refs=(KNOWN_REF,),
    )
    assert result.disposition is ControllerProtocolDispositionV22.ACCEPTED
    assert result.read_dispatch_authorized is False
    assert result.invalid_dispatches == 0
    assert result.session.terminal is ControllerSessionTerminalV22.COMPLETED


def test_correction_contract_rejects_semantic_rehash_of_valid_action_surface() -> None:
    hypotheses = build_hypothesis_catalog_v22(
        candidate_services=("checkout", "payment")
    )
    actions = _actions()
    result = process_controller_decision_v22(
        session=initialize_controller_session_v22(
            arm=ControllerArmV22.PLANNER_LITE,
            hypothesis_catalog=hypotheses,
        ),
        raw_decision={"decision": "READ"},
        hypothesis_catalog=hypotheses,
        action_catalog=actions,
        known_evidence_refs=(),
    )
    assert result.correction is not None
    forged_draft = result.correction.model_copy(
        update={"current_valid_action_ids": result.correction.current_valid_action_ids[:-1]}
    )
    with pytest.raises(ValueError, match="current action catalog"):
        PlanCorrectionV22.model_validate(
            forged_draft.model_copy(
                update={
                    "correction_sha256": semantic_sha256_v22(
                        forged_draft.model_dump(
                            mode="json",
                            exclude={"correction_sha256"},
                        )
                    )
                }
            ).model_dump(mode="python"),
            context={"action_catalog": actions},
        )
