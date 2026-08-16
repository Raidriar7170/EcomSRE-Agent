"""Truth-separated execution, scoring, and private evidence for PR-E."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import model_validator

from ecomsre.dta_v2.agent import (
    AgentFailureCode,
    AgentProvider,
    AgentRunTerminal,
    DtaAgentRunResult,
    run_tool_using_agent,
)
from ecomsre.dta_v2.agent_contracts import AlertContext
from ecomsre.dta_v2.agent_evidence import _write_private_json, persist_agent_run
from ecomsre.dta_v2.contracts import DtaModel, Sha256, Terminal, semantic_sha256
from ecomsre.dta_v2.evaluation_agents import (
    FullContextAgentRunResult,
    run_full_context_agent,
)
from ecomsre.dta_v2.evaluation_contracts import (
    AgentVisibleReplayCase,
    EvaluationArm,
    EvaluationPrediction,
    EvaluationScore,
    EvaluationSplit,
    EvaluatorCaseTruth,
    OpaqueExecutionId,
    build_evaluation_score,
)
from ecomsre.dta_v2.evaluation_replay import ReplayCaseReadBackend
from ecomsre.dta_v2.registry import RunbookRegistry
from ecomsre.dta_v2.provider_development_smoke import ProhibitedActionCounters


@dataclass(frozen=True, slots=True)
class EvaluationArmExecution:
    """In-memory Agent output. Evaluator truth is deliberately absent."""

    case: AgentVisibleReplayCase
    context: AlertContext
    arm: EvaluationArm
    agent_result: DtaAgentRunResult
    full_context_result: FullContextAgentRunResult | None


class EvaluationEntryResult(DtaModel):
    schema_version: Literal["dta-v2.evaluation-entry-result.v1"]
    execution_id: OpaqueExecutionId
    case_sha256: Sha256
    truth_sha256: Sha256
    split: EvaluationSplit
    arm: EvaluationArm
    model_id: str
    identity_sha256: Sha256
    agent_result_sha256: Sha256
    full_context_result_sha256: Sha256 | None
    evidence_manifest_sha256: Sha256
    prediction: EvaluationPrediction
    score: EvaluationScore
    prohibited_action_counters: ProhibitedActionCounters
    entry_sha256: Sha256

    @model_validator(mode="after")
    def require_entry(self) -> EvaluationEntryResult:
        if (
            self.prediction.case_id != self.score.case_id
            or self.prediction.arm is not self.arm
            or self.score.arm is not self.arm
        ):
            raise ValueError("evaluation entry prediction and score differ")
        if (
            self.arm is EvaluationArm.ONE_SHOT_FULL_CONTEXT
            and self.full_context_result_sha256 is None
        ) or (
            self.arm is EvaluationArm.ADAPTIVE_TOOL_USING
            and self.full_context_result_sha256 is not None
        ):
            raise ValueError("evaluation entry arm wrapper differs")
        expected = semantic_sha256(
            self.model_dump(mode="json", exclude={"entry_sha256"})
        )
        if self.entry_sha256 != expected:
            raise ValueError("evaluation entry digest differs")
        return self


def execute_evaluation_arm(
    *,
    case: AgentVisibleReplayCase,
    context: AlertContext,
    arm: EvaluationArm,
    registry: RunbookRegistry,
    provider: AgentProvider,
) -> EvaluationArmExecution:
    """Execute without accepting, loading, or observing evaluator truth."""

    case = AgentVisibleReplayCase.model_validate_json(case.model_dump_json())
    context = AlertContext.model_validate_json(context.model_dump_json())
    registry = RunbookRegistry.model_validate_json(registry.model_dump_json())
    if (
        context.scenario_id != case.scenario_id
        or context.started_at != case.captured_started_at
        or context.ended_at != case.captured_ended_at
    ):
        raise ValueError("evaluation context differs from replay case")
    backend = ReplayCaseReadBackend(case)
    if arm is EvaluationArm.ONE_SHOT_FULL_CONTEXT:
        full = run_full_context_agent(
            case=case,
            context=context,
            backend=backend,
            registry=registry,
            provider=provider,
        )
        result = full.agent_result
    else:
        full = None
        result = run_tool_using_agent(
            context=context,
            backend=backend,
            registry=registry,
            provider=provider,
        )
    return EvaluationArmExecution(
        case=case,
        context=context,
        arm=arm,
        agent_result=result,
        full_context_result=full,
    )


def _refs_are_valid(result: DtaAgentRunResult) -> bool:
    diagnosis = result.diagnosis
    if diagnosis is None:
        return False
    refs = (
        diagnosis.supporting_evidence_refs
        + diagnosis.contradicting_evidence_refs
    )
    observed = {item.evidence_ref for item in result.evidence_store.observations}
    if any(item not in observed for item in refs):
        return False
    if result.resolved_evidence is not None:
        return {item.evidence_ref for item in result.resolved_evidence.evidence} == set(
            refs
        )
    return True


def _prediction(execution: EvaluationArmExecution) -> EvaluationPrediction:
    result = execution.agent_result
    diagnosis = result.diagnosis
    completed = (
        result.terminal is AgentRunTerminal.COMPLETED
        and diagnosis is not None
        and diagnosis.terminal is Terminal.COMPLETED
    )
    proposal = result.action_proposal if completed else None
    unsafe = int(
        result.failure_code is AgentFailureCode.ACTION_SELECTION_BINDING_FAILURE
        and any(
            turn.parsed_action_selection is not None
            for turn in result.provider_turns
        )
    )
    if result.terminal is AgentRunTerminal.FAILED:
        terminal: Terminal | Literal["FAILED"] = "FAILED"
    else:
        terminal = Terminal(result.terminal.value)
    input_tokens = sum(turn.usage.input_tokens for turn in result.provider_turns)
    output_tokens = sum(turn.usage.output_tokens for turn in result.provider_turns)
    latency_ms = sum(turn.monotonic_latency_ms for turn in result.provider_turns)
    sources = (
        ()
        if diagnosis is None
        else tuple(sorted(set(diagnosis.evidence_source_types), key=lambda item: item.value))
    )
    if completed:
        assert diagnosis is not None
        root_service = diagnosis.root_service
        fault_domain = diagnosis.fault_domain
        mechanism = diagnosis.mechanism
    else:
        root_service = fault_domain = mechanism = None
    return EvaluationPrediction(
        schema_version="dta-v2.evaluation-prediction.v1",
        case_id=execution.case.case_id,
        arm=execution.arm,
        terminal=terminal,
        root_service=root_service,
        fault_domain=fault_domain,
        mechanism=mechanism,
        disposition=None if proposal is None else proposal.disposition,
        runbook_id=None if proposal is None else proposal.runbook_id,
        cited_evidence_sources=sources,
        evidence_refs_valid=_refs_are_valid(result),
        read_tool_dispatches=(
            0
            if execution.arm is EvaluationArm.ONE_SHOT_FULL_CONTEXT
            else result.read_tool_dispatch_count
        ),
        context_materialization_reads=(
            4 if execution.arm is EvaluationArm.ONE_SHOT_FULL_CONTEXT else 0
        ),
        provider_turns=result.provider_turn_count,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        latency_ms=latency_ms,
        unsafe_proposal_attempts=unsafe,
    )


def score_and_persist_evaluation_execution(
    *,
    execution: EvaluationArmExecution,
    truth: EvaluatorCaseTruth,
    execution_id: str,
    private_root: Path,
    forbidden_secrets: tuple[str, ...] = (),
) -> EvaluationEntryResult:
    """Load truth only after execution, then score and persist create-once."""

    truth = EvaluatorCaseTruth.model_validate_json(truth.model_dump_json())
    if truth.case_id != execution.case.case_id:
        raise ValueError("evaluation truth differs from completed execution")
    prediction = _prediction(execution)
    score = build_evaluation_score(prediction=prediction, truth=truth)
    root = private_root
    manifest = persist_agent_run(
        root / "agent",
        execution.agent_result,
        forbidden_secrets=forbidden_secrets,
    )
    payload: dict[str, Any] = {
        "schema_version": "dta-v2.evaluation-entry-result.v1",
        "execution_id": execution_id,
        "case_sha256": execution.case.case_sha256,
        "truth_sha256": truth.truth_sha256,
        "split": truth.split,
        "arm": execution.arm,
        "model_id": execution.agent_result.identity.model_id,
        "identity_sha256": execution.agent_result.identity.identity_sha256,
        "agent_result_sha256": execution.agent_result.result_sha256,
        "full_context_result_sha256": (
            None
            if execution.full_context_result is None
            else execution.full_context_result.result_sha256
        ),
        "evidence_manifest_sha256": manifest.manifest_sha256,
        "prediction": prediction,
        "score": score,
        "prohibited_action_counters": ProhibitedActionCounters(),
    }
    draft = EvaluationEntryResult.model_construct(
        **payload, entry_sha256="0" * 64
    )
    entry = EvaluationEntryResult.model_validate(
        {
            **payload,
            "entry_sha256": semantic_sha256(
                draft.model_dump(mode="json", exclude={"entry_sha256"})
            ),
        }
    )
    _write_private_json(root / "prediction.json", prediction)
    _write_private_json(root / "score.json", score)
    _write_private_json(root / "entry-result.json", entry)
    return entry


__all__ = [
    "EvaluationArmExecution",
    "EvaluationEntryResult",
    "execute_evaluation_arm",
    "score_and_persist_evaluation_execution",
]
