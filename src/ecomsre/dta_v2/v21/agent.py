"""Bounded three-arm, two-stage DTA v2.1 Agent orchestration."""

from __future__ import annotations

from enum import Enum
import re
from typing import Any, Literal, Protocol, cast

from pydantic import Field, StrictInt, model_validator

from ecomsre.dta_v2.agent_contracts import (
    ProviderUsage,
    build_agent_visible_observation,
)
from ecomsre.dta_v2.evidence_store import EvidenceStoreSnapshot
from ecomsre.dta_v2.read_tools import InvestigationReadTools, ReadBackend
from ecomsre.dta_v2.tool_contracts import (
    InspectResourceUsageRequest,
    InspectServiceRuntimeRequest,
    ObservationStatus,
    QueryMetricsRequest,
    ReadToolObservation,
    ReadToolRequest,
    SearchLogsRequest,
    TraceNeighborhoodRequest,
    revalidate_read_tool_request,
)
from ecomsre.dta_v2.v21.agent_contracts import (
    ActionSelectionDecisionV21,
    AgentArmV21,
    AgentIdentityManifestV21,
    AlertContextV21,
    CandidateActionViewV21,
    FlatInvestigationStateViewV21,
    OneShotFullContextViewV21,
    build_action_proposal_v21,
    build_candidate_action_view_v21,
)
from ecomsre.dta_v2.v21.agent_provider import ProviderProtocolError, ProviderTurnV21
from ecomsre.dta_v2.v21.candidate_filter import filter_runbook_candidates
from ecomsre.dta_v2.v21.context_projection import (
    EvidenceIndexV21,
    InvestigationStateViewV21,
    NoCompactionInvestigationStateViewV21,
    build_evidence_index_v21,
    build_investigation_state_view_v21,
    build_no_compaction_investigation_state_view_v21,
    build_prior_request_history_v21,
)
from ecomsre.dta_v2.v21.contracts import (
    ActionProposalV21,
    CandidateSetV21,
    DtaDiagnosisV21,
    DtaModelV21,
    ResolvedDiagnosisEvidenceViewV21,
    ResolvedEvidenceV21,
    Sha256V21,
    TerminalV21,
    build_resolved_diagnosis_evidence_view_v21,
    semantic_sha256,
)
from ecomsre.dta_v2.v21.planner import validate_plan_decision_v21
from ecomsre.dta_v2.v21.planner_contracts import (
    DiagnosticHypothesisV21,
    EvidencePlanDecisionV21,
    PlannerNextStepV21,
    PlannerTraceEntryV21,
)
from ecomsre.dta_v2.v21.registry import RunbookRegistryV21


class AgentRunTerminalV21(str, Enum):
    COMPLETED = "COMPLETED"
    NEED_MORE_EVIDENCE = "NEED_MORE_EVIDENCE"
    ABSTAIN = "ABSTAIN"
    FAILED = "FAILED"


class AgentFailureCodeV21(str, Enum):
    PROVIDER_PROTOCOL_FAILURE = "PROVIDER_PROTOCOL_FAILURE"
    PROVIDER_TRANSPORT_FAILURE = "PROVIDER_TRANSPORT_FAILURE"
    PLANNER_CONTRACT_FAILURE = "PLANNER_CONTRACT_FAILURE"
    READ_BUDGET_EXHAUSTED = "READ_BUDGET_EXHAUSTED"
    DUPLICATE_READ_REQUEST = "DUPLICATE_READ_REQUEST"
    DIAGNOSIS_BINDING_FAILURE = "DIAGNOSIS_BINDING_FAILURE"
    ACTION_SELECTION_BINDING_FAILURE = "ACTION_SELECTION_BINDING_FAILURE"
    INTERNAL_CONTRACT_FAILURE = "INTERNAL_CONTRACT_FAILURE"


class ProviderStageV21(str, Enum):
    INVESTIGATION = "INVESTIGATION"
    ACTION_SELECTION = "ACTION_SELECTION"


class AgentProviderV21(Protocol):
    @property
    def identity(self) -> AgentIdentityManifestV21: ...

    @property
    def attempted_calls(self) -> int: ...

    def investigation_turn(
        self,
        *,
        context: AlertContextV21,
        visible_state: object,
        read_tools_enabled: bool,
    ) -> ProviderTurnV21: ...

    def action_selection_turn(
        self,
        *,
        diagnosis: DtaDiagnosisV21,
        resolved_evidence: ResolvedDiagnosisEvidenceViewV21,
        candidate_view: CandidateActionViewV21,
    ) -> ProviderTurnV21: ...


class ProviderTurnEvidenceV21(DtaModelV21):
    schema_version: Literal["dta-v21.provider-turn-evidence.v1"]
    stage: ProviderStageV21
    turn_ordinal: StrictInt = Field(ge=1, le=6)
    function_name: str = Field(min_length=1, max_length=128)
    tool_call_id: str = Field(min_length=1, max_length=512)
    raw_response_sha256: Sha256V21
    usage: ProviderUsage
    monotonic_latency_ms: StrictInt = Field(ge=0)
    parsed_plan_decision: EvidencePlanDecisionV21 | None = None
    parsed_read_request: ReadToolRequest | None = None
    observation: ReadToolObservation | None = None
    parsed_diagnosis: DtaDiagnosisV21 | None = None
    parsed_action_selection: ActionSelectionDecisionV21 | None = None
    protocol_failure: AgentFailureCodeV21 | None = None
    turn_evidence_sha256: Sha256V21

    @model_validator(mode="after")
    def require_turn_shape(self) -> ProviderTurnEvidenceV21:
        plan = self.parsed_plan_decision
        if self.stage is ProviderStageV21.ACTION_SELECTION:
            if (
                self.parsed_action_selection is None
                or plan is not None
                or self.parsed_read_request is not None
                or self.parsed_diagnosis is not None
                or self.observation is not None
            ):
                raise ValueError("Action Selection turn has invalid artifacts")
        elif plan is not None:
            if plan.next_step is PlannerNextStepV21.REQUEST_EVIDENCE:
                if self.parsed_read_request != plan.read_request:
                    raise ValueError("Planner trace read request differs")
                if self.observation is None and self.protocol_failure is None:
                    raise ValueError("Planner read turn lacks observation or failure")
            elif plan.next_step is PlannerNextStepV21.SUBMIT_DIAGNOSIS:
                if self.parsed_diagnosis != plan.diagnosis:
                    raise ValueError("Planner trace Diagnosis differs")
            elif any(
                item is not None
                for item in (
                    self.parsed_read_request,
                    self.observation,
                    self.parsed_diagnosis,
                )
            ):
                raise ValueError("Planner abstain turn carries another output")
        elif self.parsed_read_request is not None:
            if self.observation is None and self.protocol_failure is None:
                raise ValueError("flat read turn lacks observation or failure")
            if self.parsed_diagnosis is not None:
                raise ValueError("flat read turn also carries a Diagnosis")
        elif self.parsed_diagnosis is None:
            raise ValueError("investigation turn lacks an admitted output")
        if self.protocol_failure is not None and self.observation is not None:
            raise ValueError("rejected Provider turn cannot carry an observation")
        expected = semantic_sha256(
            self.model_dump(mode="json", exclude={"turn_evidence_sha256"})
        )
        if self.turn_evidence_sha256 != expected:
            raise ValueError("Provider turn evidence digest differs")
        return self


class DtaAgentRunResultV21(DtaModelV21):
    schema_version: Literal["dta-v21.agent-run-result.v1"]
    arm: AgentArmV21
    run_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    terminal: AgentRunTerminalV21
    failure_code: AgentFailureCodeV21 | None
    provider_failure_codes: tuple[str, ...] = Field(max_length=16)
    identity: AgentIdentityManifestV21
    provider_turn_count: StrictInt = Field(ge=0, le=6)
    semantic_read_tool_dispatch_count: StrictInt = Field(ge=0, le=4)
    context_materialization_read_count: StrictInt = Field(ge=0, le=4)
    provider_turns: tuple[ProviderTurnEvidenceV21, ...] = Field(max_length=6)
    planner_trace: tuple[PlannerTraceEntryV21, ...] = Field(max_length=5)
    diagnosis: DtaDiagnosisV21 | None
    evidence_store: EvidenceStoreSnapshot
    evidence_index: EvidenceIndexV21
    resolved_evidence: ResolvedDiagnosisEvidenceViewV21 | None
    candidate_set: CandidateSetV21 | None
    candidate_view: CandidateActionViewV21 | None
    action_proposal: ActionProposalV21 | None
    result_sha256: Sha256V21

    @model_validator(mode="after")
    def require_result_shape(self) -> DtaAgentRunResultV21:
        if (
            self.provider_failure_codes != tuple(sorted(set(self.provider_failure_codes)))
            or any(
                re.fullmatch(r"[a-z][a-z0-9_]{0,63}:[a-z][a-z0-9_]{0,63}", code)
                is None
                for code in self.provider_failure_codes
            )
        ):
            raise ValueError("Agent result Provider failure codes are not safe")
        if self.provider_failure_codes and (
            self.terminal is not AgentRunTerminalV21.FAILED
            or self.failure_code is not AgentFailureCodeV21.PROVIDER_PROTOCOL_FAILURE
        ):
            raise ValueError("Agent result carries Provider codes outside a protocol failure")
        if self.identity.arm is not self.arm:
            raise ValueError("Agent result identity differs from the arm")
        if (
            self.evidence_store.run_id != self.run_id
            or self.evidence_index.run_id != self.run_id
        ):
            raise ValueError("Agent result evidence belongs to another run")
        if self.diagnosis is not None and self.diagnosis.run_id != self.run_id:
            raise ValueError("Agent result Diagnosis belongs to another run")
        if self.arm is AgentArmV21.ONE_SHOT_FULL_CONTEXT:
            if self.semantic_read_tool_dispatch_count != 0:
                raise ValueError("one-shot result reports semantic reads")
            if (
                self.context_materialization_read_count
                != self.evidence_store.dispatch_count
            ):
                raise ValueError("one-shot materialization accounting differs")
        else:
            if self.context_materialization_read_count != 0:
                raise ValueError("adaptive arm reports context materialization reads")
            if (
                self.semantic_read_tool_dispatch_count
                != self.evidence_store.dispatch_count
            ):
                raise ValueError("adaptive read accounting differs")
        if self.arm is AgentArmV21.EVIDENCE_GUIDED_PLANNER:
            if len(self.planner_trace) > 5:
                raise ValueError("Planner trace exceeds the turn budget")
        elif self.planner_trace:
            raise ValueError("non-Planner arm carries a Planner trace")
        stage_two = (
            self.resolved_evidence,
            self.candidate_set,
            self.candidate_view,
            self.action_proposal,
        )
        if self.terminal is AgentRunTerminalV21.COMPLETED:
            if (
                self.failure_code is not None
                or self.diagnosis is None
                or any(item is None for item in stage_two)
            ):
                raise ValueError("completed Agent result lacks Stage 2 artifacts")
        elif self.terminal in (
            AgentRunTerminalV21.NEED_MORE_EVIDENCE,
            AgentRunTerminalV21.ABSTAIN,
        ):
            if (
                self.failure_code is not None
                or self.diagnosis is None
                or any(item is not None for item in stage_two)
            ):
                raise ValueError("noncompleted Agent result has Stage 2 artifacts")
        elif self.failure_code is None or self.action_proposal is not None:
            raise ValueError("failed Agent result lacks typed failure semantics")
        expected = semantic_sha256(
            self.model_dump(mode="json", exclude={"result_sha256"})
        )
        if self.result_sha256 != expected:
            raise ValueError("Agent result digest differs")
        return self


def _turn_evidence(
    *,
    turn: ProviderTurnV21,
    stage: ProviderStageV21,
    ordinal: int,
    observation: ReadToolObservation | None = None,
    protocol_failure: AgentFailureCodeV21 | None = None,
) -> ProviderTurnEvidenceV21:
    plan = turn.plan_decision
    payload: dict[str, object] = {
        "schema_version": "dta-v21.provider-turn-evidence.v1",
        "stage": stage,
        "turn_ordinal": ordinal,
        "function_name": turn.function_name,
        "tool_call_id": turn.tool_call_id,
        "raw_response_sha256": turn.raw_response_sha256,
        "usage": turn.usage,
        "monotonic_latency_ms": turn.monotonic_latency_ms,
        "parsed_plan_decision": plan,
        "parsed_read_request": (
            plan.read_request if plan is not None else turn.read_request
        ),
        "observation": observation,
        "parsed_diagnosis": plan.diagnosis if plan is not None else turn.diagnosis,
        "parsed_action_selection": turn.action_selection,
        "protocol_failure": protocol_failure,
    }
    draft = cast(Any, ProviderTurnEvidenceV21).model_construct(
        **payload, turn_evidence_sha256="0" * 64
    )
    return ProviderTurnEvidenceV21.model_validate(
        {
            **payload,
            "turn_evidence_sha256": semantic_sha256(
                draft.model_dump(mode="json", exclude={"turn_evidence_sha256"})
            ),
        }
    )


def _planner_trace_entry(
    *,
    turn: ProviderTurnV21,
    prior_index_sha256: str,
    observation: ReadToolObservation | None,
) -> PlannerTraceEntryV21:
    assert turn.plan_decision is not None
    payload: dict[str, object] = {
        "schema_version": "dta-v21.planner-trace-entry.v1",
        "turn_ordinal": turn.plan_decision.turn_ordinal,
        "prior_evidence_index_sha256": prior_index_sha256,
        "decision": turn.plan_decision,
        "resulting_observation_ref": (
            None if observation is None else observation.evidence_ref
        ),
        "resulting_observation_sha256": (
            None if observation is None else observation.artifact_sha256
        ),
        "raw_provider_response_sha256": turn.raw_response_sha256,
        "usage": turn.usage,
        "monotonic_latency_ms": turn.monotonic_latency_ms,
    }
    draft = cast(Any, PlannerTraceEntryV21).model_construct(
        **payload, semantic_sha256="0" * 64
    )
    return PlannerTraceEntryV21.model_validate(
        {
            **payload,
            "semantic_sha256": semantic_sha256(
                draft.model_dump(mode="json", exclude={"semantic_sha256"})
            ),
        }
    )


def _build_result(
    *,
    arm: AgentArmV21,
    context: AlertContextV21,
    provider: AgentProviderV21,
    terminal: AgentRunTerminalV21,
    failure_code: AgentFailureCodeV21 | None,
    tools: InvestigationReadTools,
    turns: tuple[ProviderTurnEvidenceV21, ...],
    provider_failure_codes: tuple[str, ...] = (),
    planner_trace: tuple[PlannerTraceEntryV21, ...] = (),
    diagnosis: DtaDiagnosisV21 | None = None,
    resolved_evidence: ResolvedDiagnosisEvidenceViewV21 | None = None,
    candidate_set: CandidateSetV21 | None = None,
    candidate_view: CandidateActionViewV21 | None = None,
    action_proposal: ActionProposalV21 | None = None,
) -> DtaAgentRunResultV21:
    snapshot = tools.snapshot()
    materialized = (
        snapshot.dispatch_count if arm is AgentArmV21.ONE_SHOT_FULL_CONTEXT else 0
    )
    semantic_reads = (
        0 if arm is AgentArmV21.ONE_SHOT_FULL_CONTEXT else snapshot.dispatch_count
    )
    payload: dict[str, object] = {
        "schema_version": "dta-v21.agent-run-result.v1",
        "arm": arm,
        "run_id": context.run_id,
        "terminal": terminal,
        "failure_code": failure_code,
        "provider_failure_codes": provider_failure_codes,
        "identity": provider.identity,
        "provider_turn_count": provider.attempted_calls,
        "semantic_read_tool_dispatch_count": semantic_reads,
        "context_materialization_read_count": materialized,
        "provider_turns": turns,
        "planner_trace": planner_trace,
        "diagnosis": diagnosis,
        "evidence_store": snapshot,
        "evidence_index": build_evidence_index_v21(snapshot),
        "resolved_evidence": resolved_evidence,
        "candidate_set": candidate_set,
        "candidate_view": candidate_view,
        "action_proposal": action_proposal,
    }
    draft = cast(Any, DtaAgentRunResultV21).model_construct(
        **payload, result_sha256="0" * 64
    )
    return DtaAgentRunResultV21.model_validate(
        {
            **payload,
            "result_sha256": semantic_sha256(
                draft.model_dump(mode="json", exclude={"result_sha256"})
            ),
        }
    )


def _provider_failure(error: Exception) -> AgentFailureCodeV21:
    if isinstance(error, (ConnectionError, TimeoutError)):
        return AgentFailureCodeV21.PROVIDER_TRANSPORT_FAILURE
    return AgentFailureCodeV21.PROVIDER_PROTOCOL_FAILURE


def _provider_failure_codes(error: Exception) -> tuple[str, ...]:
    if not isinstance(error, ProviderProtocolError):
        return ()
    match = re.search(r"\[codes=([a-z0-9_:,]+)\]$", str(error))
    if match is None:
        return ()
    codes = tuple(sorted(set(match.group(1).split(","))))
    if len(codes) > 16 or any(
        re.fullmatch(r"[a-z][a-z0-9_]{0,63}:[a-z][a-z0-9_]{0,63}", code)
        is None
        for code in codes
    ):
        return ()
    return codes


def _request_services(request: ReadToolRequest) -> tuple[str, ...]:
    if isinstance(
        request,
        (QueryMetricsRequest, SearchLogsRequest, TraceNeighborhoodRequest),
    ):
        return (request.service,)
    if isinstance(request, (InspectServiceRuntimeRequest, InspectResourceUsageRequest)):
        return request.services
    raise TypeError("unsupported DTA read request")


def _admit_read_request(
    *, request: ReadToolRequest, context: AlertContextV21
) -> ReadToolRequest:
    request = revalidate_read_tool_request(request)
    if request.run_id != context.run_id:
        raise ValueError("read request belongs to another run")
    if request.tool not in context.allowed_read_tools:
        raise ValueError("read request is outside the tool allowlist")
    if not set(_request_services(request)).issubset(context.candidate_services):
        raise ValueError("read request is outside candidate services")
    return request


def _resolve_evidence(
    snapshot: EvidenceStoreSnapshot, diagnosis: DtaDiagnosisV21
) -> ResolvedDiagnosisEvidenceViewV21:
    refs = diagnosis.supporting_evidence_refs + diagnosis.contradicting_evidence_refs
    index = build_evidence_index_v21(snapshot)
    observations = {item.evidence_ref: item for item in snapshot.observations}
    indexed = {item.evidence_ref: item for item in index.entries}
    resolved: list[ResolvedEvidenceV21] = []
    for reference in refs:
        observation = observations.get(reference)
        entry = indexed.get(reference)
        if observation is None or entry is None:
            raise ValueError("Diagnosis cites evidence outside the full store")
        if observation.status is not ObservationStatus.SUCCESS:
            raise ValueError("Diagnosis cites a failed observation")
        resolved.append(
            ResolvedEvidenceV21(
                evidence_ref=reference,
                source=entry.source,
                service_scope=entry.service_scope,
                artifact_sha256=observation.artifact_sha256,
            )
        )
    return build_resolved_diagnosis_evidence_view_v21(
        run_id=snapshot.run_id, evidence=tuple(resolved)
    )


def _abstain_diagnosis(
    *, context: AlertContextV21, plan: EvidencePlanDecisionV21
) -> DtaDiagnosisV21:
    gaps = ", ".join(item.value for item in plan.evidence_gap_sources) or "unspecified"
    return DtaDiagnosisV21(
        schema_version="dta-v21.diagnosis.v1",
        run_id=context.run_id,
        terminal=TerminalV21.ABSTAIN,
        root_service=None,
        root_entity_ref=None,
        fault_domain=None,
        mechanism=None,
        confidence=None,
        supporting_evidence_refs=(),
        contradicting_evidence_refs=(),
        evidence_source_types=(),
        uncertainties=(f"Unresolved bounded evidence sources are {gaps}.",),
        summary="The bounded investigation abstained because evidence remained insufficient.",
    )


def _finish_diagnosis(
    *,
    arm: AgentArmV21,
    context: AlertContextV21,
    registry: RunbookRegistryV21,
    provider: AgentProviderV21,
    tools: InvestigationReadTools,
    turns: list[ProviderTurnEvidenceV21],
    planner_trace: list[PlannerTraceEntryV21],
    diagnosis: DtaDiagnosisV21,
) -> DtaAgentRunResultV21:
    snapshot = tools.snapshot()
    if diagnosis.run_id != context.run_id:
        return _build_result(
            arm=arm,
            context=context,
            provider=provider,
            terminal=AgentRunTerminalV21.FAILED,
            failure_code=AgentFailureCodeV21.DIAGNOSIS_BINDING_FAILURE,
            tools=tools,
            turns=tuple(turns),
            planner_trace=tuple(planner_trace),
        )
    if diagnosis.terminal is not TerminalV21.COMPLETED:
        if diagnosis.supporting_evidence_refs or diagnosis.contradicting_evidence_refs:
            try:
                _resolve_evidence(snapshot, diagnosis)
            except (TypeError, ValueError):
                return _build_result(
                    arm=arm,
                    context=context,
                    provider=provider,
                    terminal=AgentRunTerminalV21.FAILED,
                    failure_code=AgentFailureCodeV21.DIAGNOSIS_BINDING_FAILURE,
                    tools=tools,
                    turns=tuple(turns),
                    planner_trace=tuple(planner_trace),
                    diagnosis=diagnosis,
                )
        return _build_result(
            arm=arm,
            context=context,
            provider=provider,
            terminal=AgentRunTerminalV21(diagnosis.terminal.value),
            failure_code=None,
            tools=tools,
            turns=tuple(turns),
            planner_trace=tuple(planner_trace),
            diagnosis=diagnosis,
        )

    try:
        resolved = _resolve_evidence(snapshot, diagnosis)
        candidates = filter_runbook_candidates(
            diagnosis=diagnosis,
            diagnosis_evidence=resolved,
            registry=registry,
            exact_target=diagnosis.root_service,
        )
        candidate_view = build_candidate_action_view_v21(candidates)
    except (TypeError, ValueError):
        return _build_result(
            arm=arm,
            context=context,
            provider=provider,
            terminal=AgentRunTerminalV21.FAILED,
            failure_code=AgentFailureCodeV21.DIAGNOSIS_BINDING_FAILURE,
            tools=tools,
            turns=tuple(turns),
            planner_trace=tuple(planner_trace),
            diagnosis=diagnosis,
        )

    try:
        action_turn = provider.action_selection_turn(
            diagnosis=diagnosis,
            resolved_evidence=resolved,
            candidate_view=candidate_view,
        )
        if action_turn.action_selection is None:
            raise ProviderProtocolError("Provider action-selection output is missing")
        turns.append(
            _turn_evidence(
                turn=action_turn,
                stage=ProviderStageV21.ACTION_SELECTION,
                ordinal=len(turns) + 1,
            )
        )
        proposal = build_action_proposal_v21(
            diagnosis=diagnosis,
            resolved_evidence=resolved,
            candidate_set=candidates,
            candidate_view=candidate_view,
            registry=registry,
            decision=action_turn.action_selection,
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
            _provider_failure(error)
            if isinstance(error, (ProviderProtocolError, ConnectionError, TimeoutError))
            else AgentFailureCodeV21.ACTION_SELECTION_BINDING_FAILURE
        )
        return _build_result(
            arm=arm,
            context=context,
            provider=provider,
            terminal=AgentRunTerminalV21.FAILED,
            failure_code=failure,
            provider_failure_codes=_provider_failure_codes(error),
            tools=tools,
            turns=tuple(turns),
            planner_trace=tuple(planner_trace),
            diagnosis=diagnosis,
            resolved_evidence=resolved,
            candidate_set=candidates,
            candidate_view=candidate_view,
        )

    return _build_result(
        arm=arm,
        context=context,
        provider=provider,
        terminal=AgentRunTerminalV21.COMPLETED,
        failure_code=None,
        tools=tools,
        turns=tuple(turns),
        planner_trace=tuple(planner_trace),
        diagnosis=diagnosis,
        resolved_evidence=resolved,
        candidate_set=candidates,
        candidate_view=candidate_view,
        action_proposal=proposal,
    )


def run_evidence_guided_agent_v21(
    *,
    context: AlertContextV21,
    backend: ReadBackend,
    registry: RunbookRegistryV21,
    provider: AgentProviderV21,
    compact_context: bool = True,
) -> DtaAgentRunResultV21:
    context = AlertContextV21.model_validate(context.model_dump(mode="python"))
    registry = RunbookRegistryV21.model_validate(registry.model_dump(mode="python"))
    if provider.identity.arm is not AgentArmV21.EVIDENCE_GUIDED_PLANNER:
        raise ValueError("Provider identity does not bind the Planner arm")
    tools = InvestigationReadTools(run_id=context.run_id, backend=backend)
    turns: list[ProviderTurnEvidenceV21] = []
    trace: list[PlannerTraceEntryV21] = []
    hypotheses: tuple[DiagnosticHypothesisV21, ...] = ()
    newest: ReadToolObservation | None = None
    diagnosis: DtaDiagnosisV21 | None = None

    while diagnosis is None:
        state: InvestigationStateViewV21 | NoCompactionInvestigationStateViewV21
        if compact_context:
            state = build_investigation_state_view_v21(
                context=context,
                hypotheses=hypotheses,
                evidence_store=tools.snapshot(),
                newest_observation=newest,
                completed_provider_turns=len(trace),
            )
        else:
            state = build_no_compaction_investigation_state_view_v21(
                context=context,
                hypotheses=hypotheses,
                evidence_store=tools.snapshot(),
                newest_observation=newest,
                completed_provider_turns=len(trace),
            )
        try:
            turn = provider.investigation_turn(
                context=context,
                visible_state=state,
                read_tools_enabled=tools.snapshot().dispatch_count < 4,
            )
        except Exception as error:
            if not isinstance(
                error,
                (ProviderProtocolError, ConnectionError, TimeoutError, ValueError),
            ):
                raise
            return _build_result(
                arm=AgentArmV21.EVIDENCE_GUIDED_PLANNER,
                context=context,
                provider=provider,
                terminal=AgentRunTerminalV21.FAILED,
                failure_code=_provider_failure(error),
                provider_failure_codes=_provider_failure_codes(error),
                tools=tools,
                turns=tuple(turns),
                planner_trace=tuple(trace),
            )
        plan = turn.plan_decision
        if plan is None or plan.turn_ordinal != len(trace) + 1:
            failure = AgentFailureCodeV21.PLANNER_CONTRACT_FAILURE
            turns.append(
                _turn_evidence(
                    turn=turn,
                    stage=ProviderStageV21.INVESTIGATION,
                    ordinal=len(turns) + 1,
                    protocol_failure=failure,
                )
            )
            return _build_result(
                arm=AgentArmV21.EVIDENCE_GUIDED_PLANNER,
                context=context,
                provider=provider,
                terminal=AgentRunTerminalV21.FAILED,
                failure_code=failure,
                tools=tools,
                turns=tuple(turns),
                planner_trace=tuple(trace),
            )
        prior_index = state.evidence_index.evidence_index_sha256
        observation: ReadToolObservation | None = None
        if plan.next_step is PlannerNextStepV21.REQUEST_EVIDENCE:
            request = plan.read_request
            assert request is not None
            try:
                validate_plan_decision_v21(
                    decision=plan,
                    context=context,
                    evidence_index=state.evidence_index,
                    seen_request_sha256=tuple(
                        item.request_sha256 for item in tools.snapshot().observations
                    ),
                    completed_read_dispatches=tools.snapshot().dispatch_count,
                )
            except ValueError as error:
                failure = (
                    AgentFailureCodeV21.READ_BUDGET_EXHAUSTED
                    if "budget" in str(error).casefold()
                    else (
                        AgentFailureCodeV21.DUPLICATE_READ_REQUEST
                        if "duplicate" in str(error).casefold()
                        else AgentFailureCodeV21.PLANNER_CONTRACT_FAILURE
                    )
                )
                turns.append(
                    _turn_evidence(
                        turn=turn,
                        stage=ProviderStageV21.INVESTIGATION,
                        ordinal=len(turns) + 1,
                        protocol_failure=failure,
                    )
                )
                return _build_result(
                    arm=AgentArmV21.EVIDENCE_GUIDED_PLANNER,
                    context=context,
                    provider=provider,
                    terminal=AgentRunTerminalV21.FAILED,
                    failure_code=failure,
                    tools=tools,
                    turns=tuple(turns),
                    planner_trace=tuple(trace),
                )
            observation = tools.dispatch(request)
            newest = observation
        else:
            try:
                validate_plan_decision_v21(
                    decision=plan,
                    context=context,
                    evidence_index=state.evidence_index,
                    seen_request_sha256=tuple(
                        item.request_sha256 for item in tools.snapshot().observations
                    ),
                    completed_read_dispatches=tools.snapshot().dispatch_count,
                )
            except ValueError:
                failure = AgentFailureCodeV21.PLANNER_CONTRACT_FAILURE
                turns.append(
                    _turn_evidence(
                        turn=turn,
                        stage=ProviderStageV21.INVESTIGATION,
                        ordinal=len(turns) + 1,
                        protocol_failure=failure,
                    )
                )
                return _build_result(
                    arm=AgentArmV21.EVIDENCE_GUIDED_PLANNER,
                    context=context,
                    provider=provider,
                    terminal=AgentRunTerminalV21.FAILED,
                    failure_code=failure,
                    tools=tools,
                    turns=tuple(turns),
                    planner_trace=tuple(trace),
                )
        turns.append(
            _turn_evidence(
                turn=turn,
                stage=ProviderStageV21.INVESTIGATION,
                ordinal=len(turns) + 1,
                observation=observation,
            )
        )
        trace.append(
            _planner_trace_entry(
                turn=turn,
                prior_index_sha256=prior_index,
                observation=observation,
            )
        )
        hypotheses = plan.hypotheses
        if plan.next_step is PlannerNextStepV21.SUBMIT_DIAGNOSIS:
            assert plan.diagnosis is not None
            diagnosis = plan.diagnosis
        elif plan.next_step is PlannerNextStepV21.ABSTAIN:
            diagnosis = _abstain_diagnosis(context=context, plan=plan)

    return _finish_diagnosis(
        arm=AgentArmV21.EVIDENCE_GUIDED_PLANNER,
        context=context,
        registry=registry,
        provider=provider,
        tools=tools,
        turns=turns,
        planner_trace=trace,
        diagnosis=diagnosis,
    )


def run_flat_adaptive_agent_v21(
    *,
    context: AlertContextV21,
    backend: ReadBackend,
    registry: RunbookRegistryV21,
    provider: AgentProviderV21,
) -> DtaAgentRunResultV21:
    context = AlertContextV21.model_validate(context.model_dump(mode="python"))
    registry = RunbookRegistryV21.model_validate(registry.model_dump(mode="python"))
    if provider.identity.arm is not AgentArmV21.FLAT_ADAPTIVE:
        raise ValueError("Provider identity does not bind the flat adaptive arm")
    tools = InvestigationReadTools(run_id=context.run_id, backend=backend)
    turns: list[ProviderTurnEvidenceV21] = []
    diagnosis: DtaDiagnosisV21 | None = None
    while diagnosis is None:
        snapshot = tools.snapshot()
        state = FlatInvestigationStateViewV21(
            schema_version="dta-v21.flat-investigation-state-view.v1",
            alert_context=context,
            observations=tuple(
                build_agent_visible_observation(item) for item in snapshot.observations
            ),
            prior_requests=build_prior_request_history_v21(snapshot),
            prior_normalized_request_sha256=tuple(
                item.request_sha256 for item in snapshot.observations
            ),
            remaining_read_dispatches=4 - snapshot.dispatch_count,
        )
        try:
            turn = provider.investigation_turn(
                context=context,
                visible_state=state,
                read_tools_enabled=snapshot.dispatch_count < 4,
            )
        except Exception as error:
            if not isinstance(
                error,
                (ProviderProtocolError, ConnectionError, TimeoutError, ValueError),
            ):
                raise
            return _build_result(
                arm=AgentArmV21.FLAT_ADAPTIVE,
                context=context,
                provider=provider,
                terminal=AgentRunTerminalV21.FAILED,
                failure_code=_provider_failure(error),
                provider_failure_codes=_provider_failure_codes(error),
                tools=tools,
                turns=tuple(turns),
            )
        if turn.diagnosis is not None:
            diagnosis = DtaDiagnosisV21.model_validate(
                turn.diagnosis.model_dump(mode="python")
            )
            turns.append(
                _turn_evidence(
                    turn=turn,
                    stage=ProviderStageV21.INVESTIGATION,
                    ordinal=len(turns) + 1,
                )
            )
            break
        request = turn.read_request
        if request is None:
            failure = AgentFailureCodeV21.PROVIDER_PROTOCOL_FAILURE
        elif snapshot.dispatch_count >= 4:
            failure = AgentFailureCodeV21.READ_BUDGET_EXHAUSTED
        else:
            failure = None
        if failure is not None:
            turns.append(
                _turn_evidence(
                    turn=turn,
                    stage=ProviderStageV21.INVESTIGATION,
                    ordinal=len(turns) + 1,
                    protocol_failure=failure,
                )
            )
            return _build_result(
                arm=AgentArmV21.FLAT_ADAPTIVE,
                context=context,
                provider=provider,
                terminal=AgentRunTerminalV21.FAILED,
                failure_code=failure,
                tools=tools,
                turns=tuple(turns),
            )
        assert request is not None
        try:
            request = _admit_read_request(request=request, context=context)
            if request.normalized_request_sha256 in {
                item.request_sha256 for item in snapshot.observations
            }:
                failure = AgentFailureCodeV21.DUPLICATE_READ_REQUEST
                turns.append(
                    _turn_evidence(
                        turn=turn,
                        stage=ProviderStageV21.INVESTIGATION,
                        ordinal=len(turns) + 1,
                        protocol_failure=failure,
                    )
                )
                return _build_result(
                    arm=AgentArmV21.FLAT_ADAPTIVE,
                    context=context,
                    provider=provider,
                    terminal=AgentRunTerminalV21.FAILED,
                    failure_code=failure,
                    tools=tools,
                    turns=tuple(turns),
                )
            observation = tools.dispatch(request)
        except (TypeError, ValueError):
            failure = AgentFailureCodeV21.PROVIDER_PROTOCOL_FAILURE
            turns.append(
                _turn_evidence(
                    turn=turn,
                    stage=ProviderStageV21.INVESTIGATION,
                    ordinal=len(turns) + 1,
                    protocol_failure=failure,
                )
            )
            return _build_result(
                arm=AgentArmV21.FLAT_ADAPTIVE,
                context=context,
                provider=provider,
                terminal=AgentRunTerminalV21.FAILED,
                failure_code=failure,
                tools=tools,
                turns=tuple(turns),
            )
        turns.append(
            _turn_evidence(
                turn=turn,
                stage=ProviderStageV21.INVESTIGATION,
                ordinal=len(turns) + 1,
                observation=observation,
            )
        )

    return _finish_diagnosis(
        arm=AgentArmV21.FLAT_ADAPTIVE,
        context=context,
        registry=registry,
        provider=provider,
        tools=tools,
        turns=turns,
        planner_trace=[],
        diagnosis=diagnosis,
    )


def run_one_shot_agent_v21(
    *,
    context: AlertContextV21,
    backend: ReadBackend,
    registry: RunbookRegistryV21,
    provider: AgentProviderV21,
    materialization_requests: tuple[ReadToolRequest, ...],
) -> DtaAgentRunResultV21:
    context = AlertContextV21.model_validate(context.model_dump(mode="python"))
    registry = RunbookRegistryV21.model_validate(registry.model_dump(mode="python"))
    if provider.identity.arm is not AgentArmV21.ONE_SHOT_FULL_CONTEXT:
        raise ValueError("Provider identity does not bind the one-shot arm")
    if len(materialization_requests) != 4:
        raise ValueError("one-shot arm requires exactly four materialization reads")
    tools = InvestigationReadTools(run_id=context.run_id, backend=backend)
    seen: set[str] = set()
    for raw_request in materialization_requests:
        request = _admit_read_request(request=raw_request, context=context)
        if request.normalized_request_sha256 in seen:
            raise ValueError("one-shot materialization contains a duplicate request")
        seen.add(request.normalized_request_sha256)
        tools.dispatch(request)
    snapshot = tools.snapshot()
    state = OneShotFullContextViewV21(
        schema_version="dta-v21.one-shot-full-context-view.v1",
        alert_context=context,
        observations=tuple(
            build_agent_visible_observation(item) for item in snapshot.observations
        ),
        context_materialization_reads=snapshot.dispatch_count,
    )
    turns: list[ProviderTurnEvidenceV21] = []
    try:
        turn = provider.investigation_turn(
            context=context,
            visible_state=state,
            read_tools_enabled=False,
        )
    except Exception as error:
        if not isinstance(
            error,
            (ProviderProtocolError, ConnectionError, TimeoutError, ValueError),
        ):
            raise
        return _build_result(
            arm=AgentArmV21.ONE_SHOT_FULL_CONTEXT,
            context=context,
            provider=provider,
            terminal=AgentRunTerminalV21.FAILED,
            failure_code=_provider_failure(error),
            provider_failure_codes=_provider_failure_codes(error),
            tools=tools,
            turns=(),
        )
    if turn.diagnosis is None:
        failure = AgentFailureCodeV21.PROVIDER_PROTOCOL_FAILURE
        turns.append(
            _turn_evidence(
                turn=turn,
                stage=ProviderStageV21.INVESTIGATION,
                ordinal=1,
                protocol_failure=failure,
            )
        )
        return _build_result(
            arm=AgentArmV21.ONE_SHOT_FULL_CONTEXT,
            context=context,
            provider=provider,
            terminal=AgentRunTerminalV21.FAILED,
            failure_code=failure,
            tools=tools,
            turns=tuple(turns),
        )
    diagnosis = DtaDiagnosisV21.model_validate(turn.diagnosis.model_dump(mode="python"))
    turns.append(
        _turn_evidence(
            turn=turn,
            stage=ProviderStageV21.INVESTIGATION,
            ordinal=1,
        )
    )
    return _finish_diagnosis(
        arm=AgentArmV21.ONE_SHOT_FULL_CONTEXT,
        context=context,
        registry=registry,
        provider=provider,
        tools=tools,
        turns=turns,
        planner_trace=[],
        diagnosis=diagnosis,
    )


__all__ = (
    "AgentFailureCodeV21",
    "AgentProviderV21",
    "AgentRunTerminalV21",
    "DtaAgentRunResultV21",
    "ProviderStageV21",
    "ProviderTurnEvidenceV21",
    "run_evidence_guided_agent_v21",
    "run_flat_adaptive_agent_v21",
    "run_one_shot_agent_v21",
)
