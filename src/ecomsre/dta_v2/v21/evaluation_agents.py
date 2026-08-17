"""Truth-free three-arm replay execution and private post-execution scoring."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, model_validator

from ecomsre.dta_v2.tool_contracts import (
    InspectResourceUsageRequest,
    InspectServiceRuntimeRequest,
    QueryMetricsRequest,
    ReadToolRequest,
    SearchLogsRequest,
    TraceNeighborhoodRequest,
)
from ecomsre.dta_v2.v21.agent import (
    AgentFailureCodeV21,
    AgentProviderV21,
    AgentRunTerminalV21,
    DtaAgentRunResultV21,
    run_evidence_guided_agent_v21,
    run_flat_adaptive_agent_v21,
    run_one_shot_agent_v21,
)
from ecomsre.dta_v2.v21.agent_contracts import (
    AgentArmV21,
    AlertContextV21,
)
from ecomsre.dta_v2.v21.contracts import (
    DtaModelV21,
    EvidenceSourceV21,
    Sha256V21,
    TerminalV21,
    semantic_sha256,
)
from ecomsre.dta_v2.v21.evaluation_contracts import (
    AgentVisibleReplayCaseV21,
    EvaluationArmV21,
    EvaluationPredictionV21,
    EvaluationScoreV21,
    EvaluatorCaseTruthV21,
    build_evaluation_score_v21,
)
from ecomsre.dta_v2.v21.evaluation_replay import (
    ReplayCaseReadBackendV21,
    build_materialization_request_v21,
)
from ecomsre.dta_v2.v21.registry import RunbookRegistryV21
from ecomsre_live_sandbox.contracts import write_private_json


_SOURCE_BY_REQUEST = {
    QueryMetricsRequest: EvidenceSourceV21.METRICS,
    SearchLogsRequest: EvidenceSourceV21.LOGS,
    TraceNeighborhoodRequest: EvidenceSourceV21.TRACES,
    InspectServiceRuntimeRequest: EvidenceSourceV21.RUNTIME,
    InspectResourceUsageRequest: EvidenceSourceV21.RESOURCES,
}
_AGENT_ARM_BY_EVALUATION_ARM = {
    EvaluationArmV21.ONE_SHOT_FULL_CONTEXT: AgentArmV21.ONE_SHOT_FULL_CONTEXT,
    EvaluationArmV21.FLAT_ADAPTIVE: AgentArmV21.FLAT_ADAPTIVE,
    EvaluationArmV21.EVIDENCE_GUIDED_PLANNER: AgentArmV21.EVIDENCE_GUIDED_PLANNER,
    EvaluationArmV21.EVIDENCE_GUIDED_PLANNER_NO_COMPACTION: (
        AgentArmV21.EVIDENCE_GUIDED_PLANNER
    ),
}


@dataclass(frozen=True, slots=True)
class EvaluationArmExecutionV21:
    """Agent execution object deliberately has no evaluator-truth field."""

    case: AgentVisibleReplayCaseV21
    context: AlertContextV21
    arm: EvaluationArmV21
    materialization_requests: tuple[ReadToolRequest, ...]
    agent_result: DtaAgentRunResultV21


class EvaluationEntryResultV21(DtaModelV21):
    schema_version: Literal["dta-v21.evaluation-entry-result.v1"]
    execution_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    case_sha256: Sha256V21
    truth_sha256: Sha256V21
    arm: EvaluationArmV21
    model_id: str
    identity_sha256: Sha256V21
    agent_result_sha256: Sha256V21
    prediction: EvaluationPredictionV21
    score: EvaluationScoreV21
    entry_sha256: Sha256V21

    @model_validator(mode="after")
    def require_entry(self) -> EvaluationEntryResultV21:
        if (
            self.prediction.case_id != self.score.case_id
            or self.prediction.arm is not self.arm
            or self.score.arm is not self.arm
        ):
            raise ValueError("evaluation entry prediction and score differ")
        expected = {
            semantic_sha256(self.model_dump(mode="json", exclude={"entry_sha256"})),
            semantic_sha256(
                self.model_dump(
                    mode="json",
                    exclude={"entry_sha256"},
                    exclude_unset=True,
                )
            ),
        }
        if self.entry_sha256 not in expected:
            raise ValueError("evaluation entry digest differs")
        return self


def execute_evaluation_arm_v21(
    *,
    case: AgentVisibleReplayCaseV21,
    context: AlertContextV21,
    arm: EvaluationArmV21,
    registry: RunbookRegistryV21,
    provider: AgentProviderV21,
) -> EvaluationArmExecutionV21:
    """Execute one arm without accepting or observing evaluator truth."""

    case = AgentVisibleReplayCaseV21.model_validate(case.model_dump(mode="python"))
    context = AlertContextV21.model_validate(context.model_dump(mode="python"))
    registry = RunbookRegistryV21.model_validate(registry.model_dump(mode="python"))
    if provider.identity.arm is not _AGENT_ARM_BY_EVALUATION_ARM[arm]:
        raise ValueError("Provider identity differs from evaluation arm")
    if (
        context.scenario_id != case.scenario_id
        or context.started_at != case.captured_started_at
        or context.ended_at != case.captured_ended_at
    ):
        raise ValueError("evaluation context differs from replay case")
    backend = ReplayCaseReadBackendV21(case)
    materialization: tuple[ReadToolRequest, ...] = ()
    if arm is EvaluationArmV21.ONE_SHOT_FULL_CONTEXT:
        by_tool = {item.tool: item for item in case.observations}
        materialization = tuple(
            build_materialization_request_v21(
                run_id=context.run_id,
                case=case,
                fixture=by_tool[tool],
            )
            for tool in case.full_context_tools
        )
        result = run_one_shot_agent_v21(
            context=context,
            backend=backend,
            registry=registry,
            provider=provider,
            materialization_requests=materialization,
        )
    elif arm is EvaluationArmV21.FLAT_ADAPTIVE:
        result = run_flat_adaptive_agent_v21(
            context=context,
            backend=backend,
            registry=registry,
            provider=provider,
        )
    else:
        result = run_evidence_guided_agent_v21(
            context=context,
            backend=backend,
            registry=registry,
            provider=provider,
            compact_context=(arm is EvaluationArmV21.EVIDENCE_GUIDED_PLANNER),
        )
    return EvaluationArmExecutionV21(
        case=case,
        context=context,
        arm=arm,
        materialization_requests=materialization,
        agent_result=result,
    )


def _requested_source(request: ReadToolRequest) -> EvidenceSourceV21:
    for request_type, source in _SOURCE_BY_REQUEST.items():
        if isinstance(request, request_type):
            return source
    raise TypeError("evaluation request type is unsupported")


def _requested_targets(request: ReadToolRequest) -> tuple[str, ...]:
    if isinstance(
        request,
        (QueryMetricsRequest, SearchLogsRequest, TraceNeighborhoodRequest),
    ):
        return (request.service,)
    return request.services


def build_evaluation_prediction_v21(
    execution: EvaluationArmExecutionV21,
) -> EvaluationPredictionV21:
    result = execution.agent_result
    diagnosis = result.diagnosis
    completed = (
        result.terminal is AgentRunTerminalV21.COMPLETED
        and diagnosis is not None
        and diagnosis.terminal is TerminalV21.COMPLETED
    )
    proposal = result.action_proposal if completed else None
    if result.terminal is AgentRunTerminalV21.FAILED:
        terminal: TerminalV21 | Literal["FAILED"] = "FAILED"
    else:
        terminal = TerminalV21(result.terminal.value)
    if completed:
        assert diagnosis is not None
        root_service = diagnosis.root_service
        fault_domain = diagnosis.fault_domain
        mechanism = diagnosis.mechanism
    else:
        root_service = fault_domain = mechanism = None
    semantic_requests = tuple(
        turn.parsed_read_request
        for turn in result.provider_turns
        if turn.parsed_read_request is not None
    )
    all_requests = execution.materialization_requests + semantic_requests
    requested_sources = tuple(
        sorted(
            {_requested_source(item) for item in all_requests},
            key=list(EvidenceSourceV21).index,
        )
    )
    requested_targets = tuple(
        sorted({target for item in all_requests for target in _requested_targets(item)})
    )
    cited_sources = (
        ()
        if diagnosis is None
        else tuple(
            sorted(
                set(diagnosis.evidence_source_types),
                key=list(EvidenceSourceV21).index,
            )
        )
    )
    refs = (
        ()
        if diagnosis is None
        else diagnosis.supporting_evidence_refs + diagnosis.contradicting_evidence_refs
    )
    observed = {item.evidence_ref for item in result.evidence_store.observations}
    refs_valid = diagnosis is not None and set(refs).issubset(observed)
    unsafe = int(
        result.failure_code is AgentFailureCodeV21.ACTION_SELECTION_BINDING_FAILURE
        and any(
            turn.parsed_action_selection is not None for turn in result.provider_turns
        )
    )
    return EvaluationPredictionV21(
        schema_version="dta-v21.evaluation-prediction.v1",
        case_id=execution.case.case_id,
        arm=execution.arm,
        protocol_accepted=result.terminal is not AgentRunTerminalV21.FAILED,
        terminal=terminal,
        root_service=root_service,
        fault_domain=fault_domain,
        mechanism=mechanism,
        disposition=None if proposal is None else proposal.disposition,
        runbook_id=None if proposal is None else proposal.runbook_id,
        cited_evidence_sources=cited_sources,
        evidence_refs_valid=refs_valid,
        requested_evidence_sources=requested_sources,
        requested_targets=requested_targets,
        duplicate_normalized_calls=int(
            result.failure_code is AgentFailureCodeV21.DUPLICATE_READ_REQUEST
        ),
        read_tool_dispatches=result.semantic_read_tool_dispatch_count,
        context_materialization_reads=result.context_materialization_read_count,
        provider_turns=result.provider_turn_count,
        input_tokens=sum(item.usage.input_tokens for item in result.provider_turns),
        output_tokens=sum(item.usage.output_tokens for item in result.provider_turns),
        latency_ms=sum(item.monotonic_latency_ms for item in result.provider_turns),
        unsafe_proposal_attempts=unsafe,
        arbitrary_shell_attempts=0,
    )


def score_and_persist_evaluation_execution_v21(
    *,
    execution: EvaluationArmExecutionV21,
    truth: EvaluatorCaseTruthV21,
    execution_id: str,
    private_root: Path,
) -> EvaluationEntryResultV21:
    """Load evaluator truth only after Agent execution has returned."""

    truth = EvaluatorCaseTruthV21.model_validate(truth.model_dump(mode="python"))
    if truth.case_id != execution.case.case_id:
        raise ValueError("evaluation truth differs from completed execution")
    prediction = build_evaluation_prediction_v21(execution)
    score = build_evaluation_score_v21(prediction=prediction, truth=truth)
    payload: dict[str, Any] = {
        "schema_version": "dta-v21.evaluation-entry-result.v1",
        "execution_id": execution_id,
        "case_sha256": execution.case.case_sha256,
        "truth_sha256": truth.truth_sha256,
        "arm": execution.arm,
        "model_id": execution.agent_result.identity.model_id,
        "identity_sha256": execution.agent_result.identity.identity_sha256,
        "agent_result_sha256": execution.agent_result.result_sha256,
        "prediction": prediction,
        "score": score,
    }
    draft = EvaluationEntryResultV21.model_construct(**payload, entry_sha256="0" * 64)
    entry = EvaluationEntryResultV21.model_validate(
        {
            **payload,
            "entry_sha256": semantic_sha256(
                draft.model_dump(mode="json", exclude={"entry_sha256"})
            ),
        }
    )
    write_private_json(
        private_root / "agent-result.json", execution.agent_result, create_once=True
    )
    write_private_json(private_root / "prediction.json", prediction, create_once=True)
    write_private_json(private_root / "score.json", score, create_once=True)
    write_private_json(private_root / "entry-result.json", entry, create_once=True)
    return entry


__all__ = (
    "EvaluationArmExecutionV21",
    "EvaluationEntryResultV21",
    "build_evaluation_prediction_v21",
    "execute_evaluation_arm_v21",
    "score_and_persist_evaluation_execution_v21",
)
