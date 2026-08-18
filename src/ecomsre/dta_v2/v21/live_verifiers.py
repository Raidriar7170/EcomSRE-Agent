"""Independent deterministic verifiers for DTA v2.1 PR-F live Agent outputs."""

from __future__ import annotations

from ecomsre.dta_v2.v21.agent import AgentRunTerminalV21, DtaAgentRunResultV21
from ecomsre.dta_v2.v21.agent_contracts import (
    AgentArmV21,
    build_candidate_action_view_v21,
)
from ecomsre.dta_v2.v21.candidate_filter import filter_runbook_candidates
from ecomsre.dta_v2.v21.contracts import ActionDispositionV21, TerminalV21
from ecomsre.dta_v2.v21.live_contracts import (
    LiveScenarioSpecV21,
    LiveScenarioV21,
)
from ecomsre.dta_v2.v21.registry import RunbookRegistryV21


def verify_live_agent_result_v21(
    *,
    result: DtaAgentRunResultV21,
    scenario: LiveScenarioSpecV21,
    registry: RunbookRegistryV21,
    planner_identity_sha256: str,
) -> DtaAgentRunResultV21:
    """Verify the Agent result after inference; never expose this oracle to it."""

    result = DtaAgentRunResultV21.model_validate(result.model_dump(mode="python"))
    scenario = LiveScenarioSpecV21.model_validate(scenario.model_dump(mode="python"))
    registry = RunbookRegistryV21.model_validate(registry.model_dump(mode="python"))
    if (
        result.arm is not AgentArmV21.EVIDENCE_GUIDED_PLANNER
        or result.identity.identity_sha256 != planner_identity_sha256
        or result.identity.model_id != "gpt-5.4-mini-2026-03-17"
    ):
        raise ValueError("live Agent identity differs from the PR-E planner")
    if scenario.scenario is LiveScenarioV21.NO_FAULT:
        completed_no_action = (
            result.terminal is AgentRunTerminalV21.COMPLETED
            and result.diagnosis is not None
            and result.diagnosis.terminal is TerminalV21.COMPLETED
            and result.diagnosis.root_service is None
            and result.diagnosis.fault_domain is None
            and result.diagnosis.mechanism is None
            and result.candidate_set is not None
            and not result.candidate_set.write_candidates
            and result.candidate_view is not None
            and result.candidate_view
            == build_candidate_action_view_v21(result.candidate_set)
            and result.action_proposal is not None
            and result.action_proposal.disposition is ActionDispositionV21.NO_ACTION
        )
        accepted_abstain = (
            result.terminal is AgentRunTerminalV21.ABSTAIN
            and result.diagnosis is not None
            and result.diagnosis.terminal is TerminalV21.ABSTAIN
            and result.diagnosis.root_service is None
            and result.diagnosis.root_entity_ref is None
            and result.diagnosis.fault_domain is None
            and result.diagnosis.mechanism is None
            and result.resolved_evidence is None
            and result.candidate_set is None
            and result.candidate_view is None
            and result.action_proposal is None
        )
        if not (completed_no_action or accepted_abstain):
            raise ValueError(
                "no-fault Agent result is not an accepted non-write terminal"
            )
        return result
    if (
        result.terminal is not AgentRunTerminalV21.COMPLETED
        or result.diagnosis is None
        or result.resolved_evidence is None
        or result.candidate_set is None
        or result.candidate_view is None
        or result.action_proposal is None
        or scenario.target_service is None
        or scenario.expected_fault_domain is None
        or scenario.expected_mechanism is None
        or scenario.expected_runbook is None
    ):
        raise ValueError("positive live Agent result is incomplete")
    diagnosis = result.diagnosis
    if (
        diagnosis.terminal is not TerminalV21.COMPLETED
        or diagnosis.root_service != scenario.target_service
        or diagnosis.root_entity_ref != f"service:{scenario.target_service}"
        or diagnosis.fault_domain is not scenario.expected_fault_domain
        or diagnosis.mechanism is not scenario.expected_mechanism
        or not set(scenario.required_evidence_sources).issubset(
            set(diagnosis.evidence_source_types)
        )
    ):
        raise ValueError("live Agent Diagnosis differs from the frozen slot oracle")
    rebuilt = filter_runbook_candidates(
        diagnosis=diagnosis,
        diagnosis_evidence=result.resolved_evidence,
        registry=registry,
        exact_target=scenario.target_service,
    )
    if rebuilt != result.candidate_set:
        raise ValueError("live Agent CandidateSet differs from recomputation")
    if result.candidate_view != build_candidate_action_view_v21(result.candidate_set):
        raise ValueError("live Agent CandidateActionView differs from the CandidateSet")
    exact = tuple(
        item
        for item in result.candidate_set.write_candidates
        if item.runbook_id is scenario.expected_runbook
        and item.target_service == scenario.target_service
    )
    proposal = result.action_proposal
    if (
        len(exact) != 1
        or len(result.candidate_set.write_candidates) != 1
        or proposal.disposition is not ActionDispositionV21.EXECUTE_RUNBOOK
        or proposal.runbook_id is not scenario.expected_runbook
        or proposal.target_service != scenario.target_service
        or not set(proposal.supporting_evidence_refs).issubset(
            {item.evidence_ref for item in result.resolved_evidence.evidence}
        )
    ):
        raise ValueError("live Agent Action Selection differs from the exact candidate")
    return result


__all__ = ("verify_live_agent_result_v21",)
