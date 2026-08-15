"""One-shot full-context comparison arm over the shared DTA v2 contracts."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import model_validator

from ecomsre.dta_v2.agent import (
    AgentFailureCode,
    AgentProvider,
    AgentRunTerminal,
    DtaAgentRunResult,
    ProviderStage,
    ProviderTurnEvidence,
    _build_result,
    _provider_failure_code,
    _turn_evidence,
)
from ecomsre.dta_v2.agent_contracts import (
    ActionSelectionDecision,
    AlertContext,
    InvestigationTranscriptEntry,
    build_agent_visible_observation,
    build_candidate_action_view,
)
from ecomsre.dta_v2.agent_provider import ProviderProtocolError
from ecomsre.dta_v2.candidate_filter import filter_runbook_candidates
from ecomsre.dta_v2.contracts import (
    DtaDiagnosis,
    DtaModel,
    Terminal,
    build_action_proposal,
    semantic_sha256,
)
from ecomsre.dta_v2.evaluation_contracts import AgentVisibleReplayCase
from ecomsre.dta_v2.evaluation_replay import (
    ReplayCaseReadBackend,
    build_materialization_request,
    provider_request_arguments,
)
from ecomsre.dta_v2.evidence_store import resolve_diagnosis_view
from ecomsre.dta_v2.read_tools import InvestigationReadTools
from ecomsre.dta_v2.registry import RunbookRegistry


class FullContextAgentRunResult(DtaModel):
    schema_version: Literal["dta-v2.full-context-agent-run-result.v1"]
    case_sha256: str
    agent_read_tool_dispatches: Literal[0]
    context_materialization_reads: Literal[4]
    agent_result: DtaAgentRunResult
    result_sha256: str

    @model_validator(mode="after")
    def require_full_context_result(self) -> FullContextAgentRunResult:
        if self.agent_result.read_tool_dispatch_count != 4:
            raise ValueError("full-context Evidence Store must contain four reads")
        expected = semantic_sha256(
            self.model_dump(mode="json", exclude={"result_sha256"})
        )
        if self.result_sha256 != expected:
            raise ValueError("full-context result digest differs")
        return self


def _wrap(
    *, case: AgentVisibleReplayCase, result: DtaAgentRunResult
) -> FullContextAgentRunResult:
    payload: dict[str, Any] = {
        "schema_version": "dta-v2.full-context-agent-run-result.v1",
        "case_sha256": case.case_sha256,
        "agent_read_tool_dispatches": 0,
        "context_materialization_reads": 4,
        "agent_result": result,
    }
    draft = FullContextAgentRunResult.model_construct(
        **payload, result_sha256="0" * 64
    )
    return FullContextAgentRunResult.model_validate(
        {
            **payload,
            "result_sha256": semantic_sha256(
                draft.model_dump(mode="json", exclude={"result_sha256"})
            ),
        }
    )


def run_full_context_agent(
    *,
    case: AgentVisibleReplayCase,
    context: AlertContext,
    backend: ReplayCaseReadBackend,
    registry: RunbookRegistry,
    provider: AgentProvider,
) -> FullContextAgentRunResult:
    """Materialize four equal-budget observations, then make one diagnosis call."""

    case = AgentVisibleReplayCase.model_validate(case.model_dump())
    context = AlertContext.model_validate(context.model_dump())
    registry = RunbookRegistry.model_validate(registry.model_dump())
    if (
        context.scenario_id != case.scenario_id
        or context.started_at != case.captured_started_at
        or context.ended_at != case.captured_ended_at
        or backend.case.case_sha256 != case.case_sha256
    ):
        raise ValueError("full-context case, context, and replay backend differ")
    if len(case.full_context_tools) != 4:
        raise ValueError("full-context case lacks four source fixtures")

    tools = InvestigationReadTools(run_id=context.run_id, backend=backend)
    transcript: list[InvestigationTranscriptEntry] = []
    fixtures = {item.tool: item for item in case.observations}
    for tool in case.full_context_tools:
        fixture = fixtures[tool]
        request = build_materialization_request(
            run_id=context.run_id, case=case, fixture=fixture
        )
        observation = tools.dispatch(request)
        transcript.append(
            InvestigationTranscriptEntry(
                schema_version="dta-v2.investigation-transcript-entry.v1",
                dispatch_ordinal=len(transcript) + 1,
                tool=request.tool,
                request_arguments=provider_request_arguments(request),
                observation=build_agent_visible_observation(observation),
            )
        )
    snapshot = tools.snapshot()
    evidence: list[ProviderTurnEvidence] = []

    try:
        turn = provider.investigation_turn(
            context=context,
            transcript=tuple(transcript),
            read_tools_enabled=False,
        )
    except Exception as error:
        if not isinstance(
            error,
            (ProviderProtocolError, ConnectionError, TimeoutError, ValueError),
        ):
            raise
        return _wrap(
            case=case,
            result=_build_result(
                context=context,
                provider=provider,
                terminal=AgentRunTerminal.FAILED,
                failure_code=_provider_failure_code(error),
                turns=(),
                evidence_store=snapshot,
            ),
        )
    if turn.diagnosis is None or turn.read_request is not None:
        return _wrap(
            case=case,
            result=_build_result(
                context=context,
                provider=provider,
                terminal=AgentRunTerminal.FAILED,
                failure_code=AgentFailureCode.PROVIDER_PROTOCOL_FAILURE,
                turns=(),
                evidence_store=snapshot,
            ),
        )
    diagnosis = DtaDiagnosis.model_validate(turn.diagnosis.model_dump())
    evidence.append(
        _turn_evidence(
            turn=turn,
            stage=ProviderStage.INVESTIGATION,
            ordinal=1,
        )
    )
    refs = diagnosis.supporting_evidence_refs + diagnosis.contradicting_evidence_refs
    if diagnosis.terminal is not Terminal.COMPLETED:
        if refs:
            try:
                resolve_diagnosis_view(snapshot, evidence_refs=refs)
            except (TypeError, ValueError):
                return _wrap(
                    case=case,
                    result=_build_result(
                        context=context,
                        provider=provider,
                        terminal=AgentRunTerminal.FAILED,
                        failure_code=AgentFailureCode.DIAGNOSIS_BINDING_FAILURE,
                        turns=tuple(evidence),
                        evidence_store=snapshot,
                        diagnosis=diagnosis,
                    ),
                )
        return _wrap(
            case=case,
            result=_build_result(
                context=context,
                provider=provider,
                terminal=AgentRunTerminal(diagnosis.terminal.value),
                failure_code=None,
                turns=tuple(evidence),
                evidence_store=snapshot,
                diagnosis=diagnosis,
            ),
        )

    try:
        resolved = resolve_diagnosis_view(snapshot, evidence_refs=refs)
        candidates = filter_runbook_candidates(
            diagnosis=diagnosis,
            registry=registry,
            diagnosis_evidence=resolved,
        )
        candidate_view = build_candidate_action_view(candidates)
    except (TypeError, ValueError):
        return _wrap(
            case=case,
            result=_build_result(
                context=context,
                provider=provider,
                terminal=AgentRunTerminal.FAILED,
                failure_code=AgentFailureCode.DIAGNOSIS_BINDING_FAILURE,
                turns=tuple(evidence),
                evidence_store=snapshot,
                diagnosis=diagnosis,
            ),
        )

    try:
        action_turn = provider.action_selection_turn(
            diagnosis=diagnosis, candidate_view=candidate_view
        )
        if action_turn.action_selection is None:
            raise ValueError("Provider action-selection output is missing")
        decision = ActionSelectionDecision.model_validate(
            action_turn.action_selection.model_dump()
        )
        evidence.append(
            _turn_evidence(
                turn=action_turn,
                stage=ProviderStage.ACTION_SELECTION,
                ordinal=2,
            )
        )
        proposal = build_action_proposal(
            candidate_set=candidates,
            diagnosis=diagnosis,
            registry=registry,
            diagnosis_evidence=resolved,
            disposition=decision.disposition,
            runbook_id=decision.runbook_id,
            target_service=decision.target_service,
            parameters=decision.parameters,
            supporting_evidence_refs=decision.supporting_evidence_refs,
            rationale=decision.rationale,
        )
    except Exception as error:
        if not isinstance(
            error,
            (
                ProviderProtocolError,
                ConnectionError,
                TimeoutError,
                TypeError,
                ValueError,
            ),
        ):
            raise
        failure = (
            _provider_failure_code(error)
            if isinstance(error, (ProviderProtocolError, ConnectionError, TimeoutError))
            else AgentFailureCode.ACTION_SELECTION_BINDING_FAILURE
        )
        return _wrap(
            case=case,
            result=_build_result(
                context=context,
                provider=provider,
                terminal=AgentRunTerminal.FAILED,
                failure_code=failure,
                turns=tuple(evidence),
                evidence_store=snapshot,
                diagnosis=diagnosis,
                resolved_evidence=resolved,
                candidate_set=candidates,
                candidate_view=candidate_view,
            ),
        )

    return _wrap(
        case=case,
        result=_build_result(
            context=context,
            provider=provider,
            terminal=AgentRunTerminal.COMPLETED,
            failure_code=None,
            turns=tuple(evidence),
            evidence_store=snapshot,
            diagnosis=diagnosis,
            resolved_evidence=resolved,
            candidate_set=candidates,
            candidate_view=candidate_view,
            action_proposal=proposal,
        ),
    )


__all__ = ["FullContextAgentRunResult", "run_full_context_agent"]
