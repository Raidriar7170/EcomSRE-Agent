from __future__ import annotations

from typing import Any

import pytest

from ecomsre.dta_v2.v22.action_catalog import (
    StaticTopologyV22,
    build_action_catalog_v22,
    build_default_tool_capability_registry_v22,
)
from ecomsre.dta_v2.v22.controller_contracts import (
    NO_ACTION_ID_V22,
    NO_INCIDENT_HYPOTHESIS_ID_V22,
    ControllerDecisionKindV22,
    ControllerDecisionV22,
    ControllerProtocolErrorCodeV22,
    build_belief_ledger_view_v22,
    build_hypothesis_catalog_v22,
)
from ecomsre.dta_v2.v22.controller_inputs import (
    ControllerArmV22,
    ControllerRuntimeContextV22,
    TriageSnapshotV22,
    build_controller_turn_input_v22,
)
from ecomsre.dta_v2.v22.controller_runtime import (
    ControllerProtocolDispositionV22,
    ControllerSessionTerminalV22,
    PlanCorrectionV22,
    initialize_controller_session_v22,
    process_controller_decision_v22,
    record_controller_read_dispatch_v22,
    record_controller_read_outcome_v22,
)
from ecomsre.dta_v2.v22.memory import (
    EvidencePredicateV22,
    EvidenceRefV22,
    MemoryLossLedgerV22,
    ObservationSummaryV22,
    SalientEvidenceMemoryV22,
    SalientFactV22,
)
from ecomsre.dta_v2.v22.predicates import build_default_evidence_support_policy_v22
from ecomsre.dta_v2.v22.read_contracts import (
    EvidenceSourceV22,
    ReadSourceStatusV22,
    semantic_sha256_v22,
)
from ecomsre.dta_v2.v22.replay import ReadOutcomeV22


def _topology() -> StaticTopologyV22:
    return StaticTopologyV22.build(
        services=("checkout", "payment"),
        edges=(("checkout", "payment"),),
    )


def _actions(*, executed_action_ids: tuple[str, ...] = (), budget: float = 3.0):
    return build_action_catalog_v22(
        candidate_services=("checkout", "payment"),
        topology=_topology(),
        capability_registry=build_default_tool_capability_registry_v22(),
        executed_action_ids=executed_action_ids,
        remaining_budget=budget,
    )


def _identity(arm: ControllerArmV22) -> str:
    return semantic_sha256_v22({"arm": arm.value, "adapter": "simple-provider.v1"})


def _memory(
    *,
    refs: tuple[EvidenceRefV22, ...] = (),
    predicates: tuple[EvidencePredicateV22, ...] = (),
    facts: tuple[SalientFactV22, ...] = (),
    summaries: tuple[ObservationSummaryV22, ...] = (),
) -> SalientEvidenceMemoryV22:
    draft = SalientEvidenceMemoryV22.model_construct(
        schema_version="dta-v22.salient-evidence-memory.v1",
        baseline_sha256="1" * 64,
        thresholds_sha256="2" * 64,
        observed_at=None,
        evidence_refs=refs,
        observation_summaries=summaries,
        predicates=predicates,
        salient_facts=facts,
        loss_ledger=MemoryLossLedgerV22.model_construct(
            schema_version="dta-v22.memory-loss-ledger.v1",
            entries=(),
            ledger_sha256="3" * 64,
        ),
        memory_sha256="0" * 64,
    )
    return draft.model_copy(
        update={
            "memory_sha256": semantic_sha256_v22(
                draft.model_dump(mode="json", exclude={"memory_sha256"})
            )
        }
    )


def _bootstrap(memory: SalientEvidenceMemoryV22, actions: Any) -> TriageSnapshotV22:
    draft = TriageSnapshotV22.model_construct(
        schema_version="dta-v22.triage-snapshot.v1",
        candidate_services=("checkout", "payment"),
        memory_sha256=memory.memory_sha256,
        topology_sha256=actions.topology_sha256,
        capability_registry_sha256=actions.capability_registry_sha256,
        enabled_sources=actions.enabled_sources,
        runtime_fact_ids=(),
        core_metric_fact_ids=(),
        strong_anomaly_predicate_ids=(),
        bootstrap_evidence_refs=(),
        candidate_subgraph_edges=(("checkout", "payment"),),
        bootstrap_weighted_cost=1.0,
        snapshot_sha256="0" * 64,
    )
    return draft.model_copy(
        update={
            "snapshot_sha256": semantic_sha256_v22(
                draft.model_dump(mode="json", exclude={"snapshot_sha256"})
            )
        }
    )


def _setup(
    *,
    arm: ControllerArmV22 = ControllerArmV22.PLANNER_LITE,
    memory: SalientEvidenceMemoryV22 | None = None,
    actions: Any = None,
):
    selected_memory = _memory() if memory is None else memory
    selected_actions = _actions() if actions is None else actions
    hypotheses = build_hypothesis_catalog_v22(
        candidate_services=("checkout", "payment")
    )
    identity = _identity(arm)
    bootstrap = _bootstrap(selected_memory, selected_actions)
    policy = build_default_evidence_support_policy_v22()
    session = initialize_controller_session_v22(
        arm=arm,
        controller_identity_sha256=identity,
        hypothesis_catalog=hypotheses,
        bootstrap=bootstrap,
        support_policy_sha256=policy.policy_sha256,
    )
    return hypotheses, identity, bootstrap, policy, session, selected_actions, selected_memory


def _turn(
    *,
    hypotheses: Any,
    identity: Any,
    bootstrap: TriageSnapshotV22,
    policy: Any,
    session: Any,
    actions: Any,
    memory: SalientEvidenceMemoryV22,
):
    view = (
        build_belief_ledger_view_v22(
            ledger=session.ledger,
            hypothesis_catalog=hypotheses,
        )
        if session.arm is ControllerArmV22.PLANNER_LITE
        else None
    )
    context = ControllerRuntimeContextV22.build(
        run_id="4" * 32,
        turn_ordinal=session.provider_turns_used + 1,
        controller_identity_sha256=identity,
        remaining_evidence_budget=(
            session.initial_evidence_budget - session.ledger.weighted_evidence_cost
        ),
        remaining_provider_turns=5 - session.provider_turns_used,
        correction_remaining=not session.ledger.correction_used,
    )
    return build_controller_turn_input_v22(
        arm=session.arm,
        runtime_context=context,
        bootstrap=bootstrap,
        hypothesis_catalog=hypotheses,
        action_catalog=actions,
        salient_memory=memory,
        belief_ledger_view=view,
        evidence_support_policy=policy,
    )


def _read(*, action_id: str, hypothesis_id: str) -> ControllerDecisionV22:
    return ControllerDecisionV22(
        decision=ControllerDecisionKindV22.READ,
        working_hypothesis_id=hypothesis_id,
        action_id=action_id,
        supporting_evidence_refs=(),
        contradicting_evidence_refs=(),
    )


def _empty_outcome(action: Any) -> ReadOutcomeV22:
    payload = {
        "schema_version": "dta-v22.read-outcome.v1",
        "action_id": action.action_id,
        "source": action.source,
        "request_sha256": action.request_sha256,
        "status": ReadSourceStatusV22.SUCCESS_EMPTY,
        "records": (),
        "truncated": False,
    }
    return ReadOutcomeV22.model_validate(
        {**payload, "outcome_sha256": semantic_sha256_v22(payload)}
    )


def test_read_is_not_executed_until_dispatch_and_authoritative_outcome() -> None:
    hypotheses, identity, bootstrap, policy, session, actions, memory = _setup()
    action = next(item for item in actions.actions if item.action_id == "a:logs:payment")
    turn = _turn(
        hypotheses=hypotheses,
        identity=identity,
        bootstrap=bootstrap,
        policy=policy,
        session=session,
        actions=actions,
        memory=memory,
    )
    result = process_controller_decision_v22(
        session=session,
        raw_decision=_read(
            action_id=action.action_id,
            hypothesis_id="h:payment:configuration-error",
        ),
        turn_input=turn,
    )
    assert result.disposition is ControllerProtocolDispositionV22.ACCEPTED
    assert result.read_dispatch_authorized is True
    assert result.session.read_dispatches == 0
    assert result.session.ledger.executed_action_ids == ()
    assert result.session.ledger.weighted_evidence_cost == 0
    assert result.session.total_evidence_cost == 1.0
    assert result.session.pending_read is not None

    with pytest.raises(ValueError, match="outcome is still pending"):
        process_controller_decision_v22(
            session=result.session,
            raw_decision=_read(
                action_id=action.action_id,
                hypothesis_id="h:payment:configuration-error",
            ),
            turn_input=turn,
        )

    dispatched = record_controller_read_dispatch_v22(
        session=result.session,
        authorization_sha256=result.session.pending_read.authorization_sha256,
    )
    assert dispatched.read_dispatches == 1
    assert dispatched.ledger.executed_action_ids == ()
    completed = record_controller_read_outcome_v22(
        session=dispatched,
        turn_input=turn,
        outcome=_empty_outcome(action),
    )
    assert completed.pending_read is None
    assert completed.read_dispatches == 1
    assert completed.ledger.executed_action_ids == (action.action_id,)
    assert completed.ledger.weighted_evidence_cost == action.weighted_cost
    assert completed.total_evidence_cost == 1.0 + action.weighted_cost


def test_controller_runtime_rejects_detached_catalog_refs_and_identity() -> None:
    hypotheses, identity, bootstrap, policy, session, actions, memory = _setup()
    turn = _turn(
        hypotheses=hypotheses,
        identity=identity,
        bootstrap=bootstrap,
        policy=policy,
        session=session,
        actions=actions,
        memory=memory,
    )
    forged_context = ControllerRuntimeContextV22.build(
        run_id="4" * 32,
        turn_ordinal=1,
        controller_identity_sha256="f" * 64,
        remaining_evidence_budget=3.0,
        remaining_provider_turns=5,
        correction_remaining=True,
    )
    forged_turn = turn.model_copy(update={"runtime_context": forged_context})
    forged_turn = forged_turn.model_copy(
        update={
            "input_sha256": semantic_sha256_v22(
                forged_turn.model_dump(mode="json", exclude={"input_sha256"})
            )
        }
    )
    with pytest.raises(ValueError, match="runtime session authority"):
        process_controller_decision_v22(
            session=session,
            raw_decision=_read(
                action_id=actions.actions[0].action_id,
                hypothesis_id="h:payment:configuration-error",
            ),
            turn_input=forged_turn,
        )


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
            {"decision": "READ"},
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
    hypotheses, identity, bootstrap, policy, session, actions, memory = _setup()
    turn = _turn(
        hypotheses=hypotheses,
        identity=identity,
        bootstrap=bootstrap,
        policy=policy,
        session=session,
        actions=actions,
        memory=memory,
    )
    result = process_controller_decision_v22(
        session=session,
        raw_decision=raw_decision,
        turn_input=turn,
    )
    assert result.disposition is ControllerProtocolDispositionV22.CORRECTION_REQUIRED
    assert result.error_code is expected_code
    assert result.correction is not None
    assert result.session.provider_turns_used == 1
    assert result.session.read_dispatches == 0


def test_semantic_admission_denies_unsupported_commit_and_no_incident() -> None:
    ref_memory = _memory(
        refs=(
            EvidenceRefV22(
                schema_version="dta-v22.evidence-ref.v1",
                evidence_ref="e:a:changes:payment:0:111111111111",
                action_id="a:changes:payment",
                source=EvidenceSourceV22.CHANGES,
                outcome_sha256="4" * 64,
                record_index=0,
                record_sha256="1" * 64,
            ),
        )
    )
    hypotheses, identity, bootstrap, policy, session, actions, memory = _setup(
        memory=ref_memory
    )
    turn = _turn(
        hypotheses=hypotheses,
        identity=identity,
        bootstrap=bootstrap,
        policy=policy,
        session=session,
        actions=actions,
        memory=memory,
    )
    unsupported_commit = process_controller_decision_v22(
        session=session,
        raw_decision=ControllerDecisionV22(
            decision=ControllerDecisionKindV22.COMMIT,
            working_hypothesis_id="h:payment:configuration-error",
            action_id=NO_ACTION_ID_V22,
            supporting_evidence_refs=("e:a:changes:payment:0:111111111111",),
            contradicting_evidence_refs=(),
        ),
        turn_input=turn,
    )
    assert unsupported_commit.semantic_admission is None
    assert unsupported_commit.disposition is ControllerProtocolDispositionV22.CORRECTION_REQUIRED
    assert unsupported_commit.error_code is ControllerProtocolErrorCodeV22.SEMANTIC_ADMISSION_FAILED
    assert unsupported_commit.session.terminal is ControllerSessionTerminalV22.ACTIVE
    assert unsupported_commit.session.read_dispatches == 0

    edge_free_bootstrap_draft = bootstrap.model_copy(
        update={"candidate_subgraph_edges": (), "snapshot_sha256": "0" * 64}
    )
    edge_free_bootstrap = edge_free_bootstrap_draft.model_copy(
        update={
            "snapshot_sha256": semantic_sha256_v22(
                edge_free_bootstrap_draft.model_dump(
                    mode="json", exclude={"snapshot_sha256"}
                )
            )
        }
    )
    edge_free_session = initialize_controller_session_v22(
        arm=session.arm,
        controller_identity_sha256=identity,
        hypothesis_catalog=hypotheses,
        bootstrap=edge_free_bootstrap,
        support_policy_sha256=policy.policy_sha256,
    )
    invalid_dependency = process_controller_decision_v22(
        session=edge_free_session,
        raw_decision=ControllerDecisionV22(
            decision=ControllerDecisionKindV22.COMMIT,
            working_hypothesis_id="h:payment:dependency-latency",
            action_id=NO_ACTION_ID_V22,
            supporting_evidence_refs=("e:a:changes:payment:0:111111111111",),
            contradicting_evidence_refs=(),
        ),
        turn_input=_turn(
            hypotheses=hypotheses,
            identity=identity,
            bootstrap=edge_free_bootstrap,
            policy=policy,
            session=edge_free_session,
            actions=actions,
            memory=memory,
        ),
    )
    assert invalid_dependency.disposition is ControllerProtocolDispositionV22.CORRECTION_REQUIRED
    assert invalid_dependency.error_code is ControllerProtocolErrorCodeV22.INVALID_DECISION_SHAPE

    hypotheses, identity, bootstrap, policy, session, actions, memory = _setup()
    no_incident = process_controller_decision_v22(
        session=session,
        raw_decision=ControllerDecisionV22(
            decision=ControllerDecisionKindV22.NO_INCIDENT,
            working_hypothesis_id=NO_INCIDENT_HYPOTHESIS_ID_V22,
            action_id=NO_ACTION_ID_V22,
            supporting_evidence_refs=(),
            contradicting_evidence_refs=(),
        ),
        turn_input=_turn(
            hypotheses=hypotheses,
            identity=identity,
            bootstrap=bootstrap,
            policy=policy,
            session=session,
            actions=actions,
            memory=memory,
        ),
    )
    assert no_incident.semantic_admission is None
    assert no_incident.disposition is ControllerProtocolDispositionV22.CORRECTION_REQUIRED
    assert no_incident.error_code is ControllerProtocolErrorCodeV22.SEMANTIC_ADMISSION_FAILED
    assert no_incident.session.terminal is ControllerSessionTerminalV22.ACTIVE
    assert no_incident.session.read_dispatches == 0


def test_correction_contract_rejects_semantic_rehash_of_valid_action_surface() -> None:
    hypotheses, identity, bootstrap, policy, session, actions, memory = _setup()
    result = process_controller_decision_v22(
        session=session,
        raw_decision={"decision": "READ"},
        turn_input=_turn(
            hypotheses=hypotheses,
            identity=identity,
            bootstrap=bootstrap,
            policy=policy,
            session=session,
            actions=actions,
            memory=memory,
        ),
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
                            mode="json", exclude={"correction_sha256"}
                        )
                    )
                }
            ).model_dump(mode="python"),
            context={"action_catalog": actions},
        )
