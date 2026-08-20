from __future__ import annotations

from pathlib import Path

from ecomsre.dta_v2.v22.controller_contracts import (
    NO_ACTION_ID_V22,
    ControllerDecisionKindV22,
    ControllerDecisionV22,
)
from ecomsre.dta_v2.v22.controller_inputs import ControllerArmV22
from ecomsre.dta_v2.v22.memory import PredicateKindV22
from ecomsre.dta_v2.v22.predicates import MechanismV22
from ecomsre.dta_v2.v22.practical_replay import (
    load_and_normalize_practical_case_v22,
)
from ecomsre.dta_v2.v22.practical_runner import (
    PracticalRunStatusV22,
    execute_practical_case_v22,
)
from ecomsre.dta_v2.v22.read_contracts import EvidenceSourceV22
from ecomsre.dta_v2.v22.simple_provider import ProviderTurnOutcomeV22


ROOT = Path(__file__).resolve().parents[2]


class _ConfigurationScript:
    def complete_turn(
        self,
        *,
        turn_input: object,
        run_id: str,
        system_prompt: str,
        allow_semantic_repair: bool,
    ) -> ProviderTurnOutcomeV22:
        del run_id, system_prompt, allow_semantic_repair
        hypotheses = turn_input.hypothesis_catalog.hypotheses  # type: ignore[attr-defined]
        hypothesis = next(
            item
            for item in hypotheses
            if item.target_service == "payment"
            and item.mechanism is MechanismV22.CONFIGURATION_ERROR
        )
        predicates = turn_input.salient_memory.predicates  # type: ignore[attr-defined]
        required = {
            PredicateKindV22.METRIC_ERROR_RATE_STRONG,
            PredicateKindV22.TRACE_FIRST_ERROR,
        }
        selected = tuple(item for item in predicates if item.predicate_kind in required)
        if {item.predicate_kind for item in selected} == required:
            decision = ControllerDecisionV22(
                decision=ControllerDecisionKindV22.COMMIT,
                working_hypothesis_id=hypothesis.hypothesis_id,
                action_id=NO_ACTION_ID_V22,
                supporting_evidence_refs=tuple(
                    sorted({ref for item in selected for ref in item.evidence_refs})
                ),
                contradicting_evidence_refs=(),
            )
        else:
            action = next(
                item
                for item in turn_input.action_catalog.actions  # type: ignore[attr-defined]
                if item.source is EvidenceSourceV22.TRACES
                and item.target_services == ("payment",)
            )
            decision = ControllerDecisionV22(
                decision=ControllerDecisionKindV22.READ,
                working_hypothesis_id=hypothesis.hypothesis_id,
                action_id=action.action_id,
                supporting_evidence_refs=(),
                contradicting_evidence_refs=(),
            )
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


def test_dual_arm_controller_executes_read_lifecycle_and_practical_admission() -> None:
    case = load_and_normalize_practical_case_v22(
        ROOT / "config/dta-v2/evaluation/development/agent-visible/dta-case-001.json"
    )

    flat = execute_practical_case_v22(
        case=case,
        arm=ControllerArmV22.FLAT_CANONICAL,
        provider=_ConfigurationScript(),
    )
    planner = execute_practical_case_v22(
        case=case,
        arm=ControllerArmV22.PLANNER_LITE,
        provider=_ConfigurationScript(),
    )

    for result in (flat, planner):
        assert result.status is PracticalRunStatusV22.VALID_TERMINAL
        assert result.terminal == "DIAGNOSED"
        assert result.root_service == "payment"
        assert result.mechanism == "CONFIGURATION_ERROR"
        assert result.adaptive_reads == 1
        assert result.provider_turns == 2
        assert result.semantic_clause_valid is True
        assert result.agent_writes == 0
    assert flat.case_bytes_sha256 == planner.case_bytes_sha256
    assert flat.planner_ledger_visible is False
    assert planner.planner_ledger_visible is True
