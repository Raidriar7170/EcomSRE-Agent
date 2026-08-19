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
from ecomsre.dta_v2.v22.controller_inputs import (
    ControllerArmV22,
    ControllerRuntimeContextV22,
    TriageSnapshotV22,
    build_common_triage_snapshot_v22,
    build_controller_turn_input_v22,
)
from ecomsre.dta_v2.v22.memory import (
    MetricSalientPayloadV22,
    RuntimeSalientPayloadV22,
    SalientEvidenceMemoryV22,
    SalientFactV22,
    SignalStrengthV22,
)
from ecomsre.dta_v2.v22.read_contracts import (
    EvidenceSourceV22,
    MetricKindV22,
    MetricSupportStatusV22,
    MetricUnitV22,
    RuntimeStateV22,
    semantic_sha256_v22,
)
from ecomsre.dta_v2.tool_contracts import EndpointState


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


def _salient_fact(
    *,
    source: EvidenceSourceV22,
    service: str,
    suffix: str,
    payload: MetricSalientPayloadV22 | RuntimeSalientPayloadV22,
) -> SalientFactV22:
    fact_payload: dict[str, Any] = {
        "schema_version": "dta-v22.salient-fact.v1",
        "fact_id": f"f:{source.value.casefold()}:{suffix:0>16}",
        "source": source,
        "service": service,
        "evidence_refs": (f"e:a:{source.value.casefold()}:{service}:0:{suffix:0>12}",),
        "signal_strength": SignalStrengthV22.NONE,
        "payload": payload,
    }
    draft = SalientFactV22.model_construct(
        **fact_payload,
        fact_sha256="0" * 64,
    )
    return SalientFactV22.model_validate(
        {
            **fact_payload,
            "fact_sha256": semantic_sha256_v22(
                draft.model_dump(mode="json", exclude={"fact_sha256"})
            ),
        }
    )


def _bootstrap_memory() -> SalientEvidenceMemoryV22:
    facts: list[SalientFactV22] = []
    ordinal = 1
    for service in ("checkout", "payment"):
        facts.append(
            _salient_fact(
                source=EvidenceSourceV22.RUNTIME,
                service=service,
                suffix=f"{ordinal:x}",
                payload=RuntimeSalientPayloadV22(
                    schema_version="dta-v22.salient-runtime.v1",
                    state=RuntimeStateV22.RUNNING,
                    healthy=True,
                    endpoint=EndpointState.READY,
                    restart_count=0,
                    exit_code=0,
                ),
            )
        )
        ordinal += 1
        for kind, value, unit in (
            (MetricKindV22.ERROR_RATE, 0.01, MetricUnitV22.RATIO),
            (MetricKindV22.LATENCY_P95_MS, 100.0, MetricUnitV22.MILLISECONDS),
            (MetricKindV22.REQUEST_SUPPORT, 1000.0, MetricUnitV22.COUNT),
        ):
            facts.append(
                _salient_fact(
                    source=EvidenceSourceV22.METRICS,
                    service=service,
                    suffix=f"{ordinal:x}",
                    payload=MetricSalientPayloadV22(
                        schema_version="dta-v22.salient-metric.v1",
                        metric_kind=kind,
                        support_status=MetricSupportStatusV22.SUPPORTED,
                        sample_count=20,
                        value=value,
                        unit=unit,
                        baseline_value=value,
                        baseline_ratio=1.0,
                        delta=0.0,
                        z_score=0.0,
                    ),
                )
            )
            ordinal += 1
    return SalientEvidenceMemoryV22.model_construct(
        schema_version="dta-v22.salient-evidence-memory.v1",
        baseline_sha256="1" * 64,
        thresholds_sha256="2" * 64,
        observed_at=None,
        evidence_refs=(),
        observation_summaries=(),
        predicates=(),
        salient_facts=tuple(facts),
        loss_ledger=None,
        memory_sha256="3" * 64,
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


def test_common_bootstrap_and_primary_turn_inputs_differ_only_by_belief_view() -> None:
    memory = _bootstrap_memory()
    topology = StaticTopologyV22.build(
        services=("checkout", "payment"),
        edges=(("checkout", "payment"),),
    )
    capabilities = build_default_tool_capability_registry_v22()
    actions = _catalog()
    hypotheses = build_hypothesis_catalog_v22(
        candidate_services=("checkout", "payment")
    )
    ledger = initialize_belief_ledger_v22(catalog=hypotheses)
    view = build_belief_ledger_view_v22(
        ledger=ledger,
        hypothesis_catalog=hypotheses,
    )
    bootstrap = build_common_triage_snapshot_v22(
        memory=memory,
        candidate_services=("checkout", "payment"),
        topology=topology,
        capability_registry=capabilities,
    )
    assert len(bootstrap.runtime_fact_ids) == 2
    assert len(bootstrap.core_metric_fact_ids) == 6
    assert bootstrap.bootstrap_weighted_cost == 1.0
    assert set(inspect.signature(build_common_triage_snapshot_v22).parameters) == {
        "memory",
        "candidate_services",
        "topology",
        "capability_registry",
    }
    context = ControllerRuntimeContextV22.build(
        run_id="4" * 32,
        turn_ordinal=1,
        controller_identity_sha256="5" * 64,
        remaining_evidence_budget=3.0,
        remaining_provider_turns=5,
        correction_remaining=True,
    )
    flat = build_controller_turn_input_v22(
        arm=ControllerArmV22.FLAT_CANONICAL,
        runtime_context=context,
        bootstrap=bootstrap,
        hypothesis_catalog=hypotheses,
        action_catalog=actions,
        salient_memory=memory,
        belief_ledger_view=None,
    )
    planner = build_controller_turn_input_v22(
        arm=ControllerArmV22.PLANNER_LITE,
        runtime_context=context,
        bootstrap=bootstrap,
        hypothesis_catalog=hypotheses,
        action_catalog=actions,
        salient_memory=memory,
        belief_ledger_view=view,
    )
    assert flat.bootstrap == planner.bootstrap
    assert flat.hypothesis_catalog == planner.hypothesis_catalog
    assert flat.action_catalog == planner.action_catalog
    assert flat.salient_memory == planner.salient_memory
    assert flat.belief_ledger_view is None
    assert planner.belief_ledger_view == view

    with pytest.raises(ValueError, match="Flat cannot receive"):
        build_controller_turn_input_v22(
            arm=ControllerArmV22.FLAT_CANONICAL,
            runtime_context=context,
            bootstrap=bootstrap,
            hypothesis_catalog=hypotheses,
            action_catalog=actions,
            salient_memory=memory,
            belief_ledger_view=view,
        )
    with pytest.raises(ValueError, match="Planner-Lite requires"):
        build_controller_turn_input_v22(
            arm=ControllerArmV22.PLANNER_LITE,
            runtime_context=context,
            bootstrap=bootstrap,
            hypothesis_catalog=hypotheses,
            action_catalog=actions,
            salient_memory=memory,
            belief_ledger_view=None,
        )

    forged_draft = bootstrap.model_copy(
        update={"runtime_fact_ids": bootstrap.runtime_fact_ids[:-1]}
    )
    with pytest.raises(ValueError, match="authoritative bootstrap"):
        TriageSnapshotV22.model_validate(
            forged_draft.model_copy(
                update={
                    "snapshot_sha256": semantic_sha256_v22(
                        forged_draft.model_dump(
                            mode="json",
                            exclude={"snapshot_sha256"},
                        )
                    )
                }
            ).model_dump(mode="python"),
            context={
                "memory": memory,
                "topology": topology,
                "capability_registry": capabilities,
            },
        )
