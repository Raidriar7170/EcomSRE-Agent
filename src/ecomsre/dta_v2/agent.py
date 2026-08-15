"""Bounded two-stage Tool-Using Strong Single orchestration for DTA v2."""

from __future__ import annotations

from collections.abc import Mapping
from enum import Enum
import json
from typing import Any, Literal, Protocol, cast

from pydantic import Field, JsonValue, StrictInt, model_validator

from ecomsre.dta_v2.agent_contracts import (
    ActionSelectionDecision,
    AgentIdentityManifest,
    AlertContext,
    CandidateActionView,
    InvestigationTranscriptEntry,
    ProviderUsage,
    build_agent_visible_observation,
    build_candidate_action_view,
)
from ecomsre.dta_v2.agent_provider import ProviderProtocolError, ProviderTurn
from ecomsre.dta_v2.candidate_filter import filter_runbook_candidates
from ecomsre.dta_v2.contracts import (
    ActionProposal,
    CandidateSet,
    DtaDiagnosis,
    DtaModel,
    ResolvedDiagnosisEvidenceView,
    RunId,
    Sha256,
    Terminal,
    build_action_proposal,
    semantic_sha256,
)
from ecomsre.dta_v2.evidence_store import (
    EvidenceStoreSnapshot,
    resolve_diagnosis_view,
)
from ecomsre.dta_v2.read_tools import InvestigationReadTools, ReadBackend
from ecomsre.dta_v2.registry import RunbookRegistry
from ecomsre.dta_v2.tool_contracts import ReadToolObservation, ReadToolRequest


class AgentRunTerminal(str, Enum):
    COMPLETED = "COMPLETED"
    NEED_MORE_EVIDENCE = "NEED_MORE_EVIDENCE"
    ABSTAIN = "ABSTAIN"
    FAILED = "FAILED"


class AgentFailureCode(str, Enum):
    PROVIDER_PROTOCOL_FAILURE = "PROVIDER_PROTOCOL_FAILURE"
    PROVIDER_TRANSPORT_FAILURE = "PROVIDER_TRANSPORT_FAILURE"
    DIAGNOSIS_BINDING_FAILURE = "DIAGNOSIS_BINDING_FAILURE"
    ACTION_SELECTION_BINDING_FAILURE = "ACTION_SELECTION_BINDING_FAILURE"
    INTERNAL_CONTRACT_FAILURE = "INTERNAL_CONTRACT_FAILURE"


class ProviderStage(str, Enum):
    INVESTIGATION = "INVESTIGATION"
    ACTION_SELECTION = "ACTION_SELECTION"


class AgentProvider(Protocol):
    @property
    def identity(self) -> AgentIdentityManifest: ...

    @property
    def attempted_calls(self) -> int: ...

    def investigation_turn(
        self,
        *,
        context: AlertContext,
        transcript: tuple[object, ...],
        read_tools_enabled: bool,
    ) -> ProviderTurn: ...

    def action_selection_turn(
        self,
        *,
        diagnosis: DtaDiagnosis,
        candidate_view: CandidateActionView,
    ) -> ProviderTurn: ...


class ProviderTurnEvidence(DtaModel):
    """Private accepted Provider response and parsed lineage for one turn."""

    schema_version: Literal["dta-v2.provider-turn-evidence.v1"]
    stage: ProviderStage
    turn_ordinal: StrictInt = Field(ge=1, le=6)
    function_name: str = Field(min_length=1, max_length=128)
    tool_call_id: str = Field(min_length=1, max_length=512)
    raw_response: dict[str, JsonValue]
    raw_response_sha256: Sha256
    raw_arguments: dict[str, JsonValue]
    usage: ProviderUsage
    monotonic_latency_ms: StrictInt = Field(ge=0)
    parsed_read_request: ReadToolRequest | None = None
    observation: ReadToolObservation | None = None
    parsed_diagnosis: DtaDiagnosis | None = None
    parsed_action_selection: ActionSelectionDecision | None = None
    turn_evidence_sha256: Sha256

    @model_validator(mode="after")
    def require_turn_evidence(self) -> ProviderTurnEvidence:
        if semantic_sha256(self.raw_response) != self.raw_response_sha256:
            raise ValueError("raw Provider response digest differs")
        parsed_count = sum(
            item is not None
            for item in (
                self.parsed_read_request,
                self.parsed_diagnosis,
                self.parsed_action_selection,
            )
        )
        if parsed_count != 1:
            raise ValueError("Provider turn must retain exactly one parsed output")
        if self.parsed_read_request is not None:
            if self.stage is not ProviderStage.INVESTIGATION or self.observation is None:
                raise ValueError("read turn lacks its typed observation")
        elif self.observation is not None:
            raise ValueError("non-read Provider turn cannot carry an observation")
        if self.parsed_diagnosis is not None and self.stage is not ProviderStage.INVESTIGATION:
            raise ValueError("diagnosis turn stage differs")
        if (
            self.parsed_action_selection is not None
            and self.stage is not ProviderStage.ACTION_SELECTION
        ):
            raise ValueError("action-selection turn stage differs")
        expected = semantic_sha256(
            self.model_dump(mode="json", exclude={"turn_evidence_sha256"})
        )
        if self.turn_evidence_sha256 != expected:
            raise ValueError("Provider turn evidence digest differs")
        return self


class DtaAgentRunResult(DtaModel):
    """Run-scoped typed terminal with full private Provider lineage."""

    schema_version: Literal["dta-v2.agent-run-result.v1"]
    run_id: RunId
    terminal: AgentRunTerminal
    failure_code: AgentFailureCode | None
    identity: AgentIdentityManifest
    provider_turn_count: StrictInt = Field(ge=0, le=6)
    read_tool_dispatch_count: StrictInt = Field(ge=0, le=4)
    provider_turns: tuple[ProviderTurnEvidence, ...] = Field(max_length=6)
    diagnosis: DtaDiagnosis | None
    evidence_store: EvidenceStoreSnapshot
    resolved_evidence: ResolvedDiagnosisEvidenceView | None
    candidate_set: CandidateSet | None
    candidate_view: CandidateActionView | None
    action_proposal: ActionProposal | None
    result_sha256: Sha256

    @model_validator(mode="after")
    def require_result_semantics(self) -> DtaAgentRunResult:
        turn_gap = self.provider_turn_count - len(self.provider_turns)
        if self.terminal is AgentRunTerminal.FAILED:
            if turn_gap not in (0, 1):
                raise ValueError("failed Agent Provider turn count is not exact")
            if turn_gap == 1 and self.failure_code not in (
                AgentFailureCode.PROVIDER_PROTOCOL_FAILURE,
                AgentFailureCode.PROVIDER_TRANSPORT_FAILURE,
            ):
                raise ValueError("failed Agent Provider turn count state differs")
        elif turn_gap != 0:
            raise ValueError("successful Agent Provider turn count is not exact")
        if self.read_tool_dispatch_count != self.evidence_store.dispatch_count:
            raise ValueError("Agent and Evidence Store dispatch counts differ")
        if self.evidence_store.run_id != self.run_id:
            raise ValueError("Agent result Evidence Store belongs to another run")
        if self.diagnosis is not None and self.diagnosis.run_id != self.run_id:
            raise ValueError("Agent diagnosis belongs to another run")
        stage_two = (
            self.resolved_evidence,
            self.candidate_set,
            self.candidate_view,
            self.action_proposal,
        )
        if self.terminal is AgentRunTerminal.COMPLETED:
            if (
                self.failure_code is not None
                or self.diagnosis is None
                or self.diagnosis.terminal is not Terminal.COMPLETED
                or any(item is None for item in stage_two)
            ):
                raise ValueError("completed Agent result lacks Stage 2 artifacts")
        elif self.terminal in (
            AgentRunTerminal.NEED_MORE_EVIDENCE,
            AgentRunTerminal.ABSTAIN,
        ):
            expected = Terminal(self.terminal.value)
            if (
                self.failure_code is not None
                or self.diagnosis is None
                or self.diagnosis.terminal is not expected
                or any(item is not None for item in stage_two)
            ):
                raise ValueError("noncompleted diagnosis result has Stage 2 artifacts")
        elif self.failure_code is None or self.action_proposal is not None:
            raise ValueError("failed Agent result lacks typed failure semantics")
        expected_sha = semantic_sha256(
            self.model_dump(mode="json", exclude={"result_sha256"})
        )
        if self.result_sha256 != expected_sha:
            raise ValueError("Agent result digest differs")
        return self


def _json_mapping(value: Mapping[str, object]) -> dict[str, JsonValue]:
    return cast(
        dict[str, JsonValue],
        json.loads(
            json.dumps(
                value,
                allow_nan=False,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
        ),
    )


def _turn_evidence(
    *,
    turn: ProviderTurn,
    stage: ProviderStage,
    ordinal: int,
    observation: ReadToolObservation | None = None,
) -> ProviderTurnEvidence:
    payload: dict[str, object] = {
        "schema_version": "dta-v2.provider-turn-evidence.v1",
        "stage": stage,
        "turn_ordinal": ordinal,
        "function_name": turn.function_name,
        "tool_call_id": turn.tool_call_id,
        "raw_response": _json_mapping(turn.raw_response),
        "raw_response_sha256": turn.raw_response_sha256,
        "raw_arguments": _json_mapping(turn.raw_arguments),
        "usage": turn.usage,
        "monotonic_latency_ms": turn.monotonic_latency_ms,
        "parsed_read_request": turn.read_request,
        "observation": observation,
        "parsed_diagnosis": turn.diagnosis,
        "parsed_action_selection": turn.action_selection,
    }
    draft = cast(Any, ProviderTurnEvidence).model_construct(
        **payload, turn_evidence_sha256="0" * 64
    )
    return ProviderTurnEvidence.model_validate(
        {
            **payload,
            "turn_evidence_sha256": semantic_sha256(
                draft.model_dump(
                    mode="json", exclude={"turn_evidence_sha256"}
                )
            ),
        }
    )


def _build_result(
    *,
    context: AlertContext,
    provider: AgentProvider,
    terminal: AgentRunTerminal,
    failure_code: AgentFailureCode | None,
    turns: tuple[ProviderTurnEvidence, ...],
    evidence_store: EvidenceStoreSnapshot,
    diagnosis: DtaDiagnosis | None = None,
    resolved_evidence: ResolvedDiagnosisEvidenceView | None = None,
    candidate_set: CandidateSet | None = None,
    candidate_view: CandidateActionView | None = None,
    action_proposal: ActionProposal | None = None,
) -> DtaAgentRunResult:
    payload: dict[str, object] = {
        "schema_version": "dta-v2.agent-run-result.v1",
        "run_id": context.run_id,
        "terminal": terminal,
        "failure_code": failure_code,
        "identity": provider.identity,
        "provider_turn_count": provider.attempted_calls,
        "read_tool_dispatch_count": evidence_store.dispatch_count,
        "provider_turns": turns,
        "diagnosis": diagnosis,
        "evidence_store": evidence_store,
        "resolved_evidence": resolved_evidence,
        "candidate_set": candidate_set,
        "candidate_view": candidate_view,
        "action_proposal": action_proposal,
    }
    draft = cast(Any, DtaAgentRunResult).model_construct(
        **payload, result_sha256="0" * 64
    )
    return DtaAgentRunResult.model_validate(
        {
            **payload,
            "result_sha256": semantic_sha256(
                draft.model_dump(mode="json", exclude={"result_sha256"})
            ),
        }
    )


def _provider_failure_code(error: Exception) -> AgentFailureCode:
    if isinstance(error, (ConnectionError, TimeoutError)):
        return AgentFailureCode.PROVIDER_TRANSPORT_FAILURE
    return AgentFailureCode.PROVIDER_PROTOCOL_FAILURE


def run_tool_using_agent(
    *,
    context: AlertContext,
    backend: ReadBackend,
    registry: RunbookRegistry,
    provider: AgentProvider,
) -> DtaAgentRunResult:
    """Run at most four reads, one diagnosis, and one Action Selection turn."""

    context = AlertContext.model_validate(context.model_dump(mode="python"))
    registry = RunbookRegistry.model_validate(registry.model_dump(mode="python"))
    tools = InvestigationReadTools(run_id=context.run_id, backend=backend)
    transcript: list[InvestigationTranscriptEntry] = []
    evidence: list[ProviderTurnEvidence] = []
    diagnosis: DtaDiagnosis | None = None

    while diagnosis is None:
        try:
            turn = provider.investigation_turn(
                context=context,
                transcript=tuple(transcript),
                read_tools_enabled=len(transcript) < 4,
            )
        except Exception as error:
            if not isinstance(
                error,
                (ProviderProtocolError, ConnectionError, TimeoutError, ValueError),
            ):
                raise
            return _build_result(
                context=context,
                provider=provider,
                terminal=AgentRunTerminal.FAILED,
                failure_code=_provider_failure_code(error),
                turns=tuple(evidence),
                evidence_store=tools.snapshot(),
            )

        if turn.diagnosis is not None:
            diagnosis = DtaDiagnosis.model_validate(
                turn.diagnosis.model_dump(mode="python")
            )
            evidence.append(
                _turn_evidence(
                    turn=turn,
                    stage=ProviderStage.INVESTIGATION,
                    ordinal=len(evidence) + 1,
                )
            )
            break
        if turn.read_request is None or len(transcript) >= 4:
            return _build_result(
                context=context,
                provider=provider,
                terminal=AgentRunTerminal.FAILED,
                failure_code=AgentFailureCode.PROVIDER_PROTOCOL_FAILURE,
                turns=tuple(evidence),
                evidence_store=tools.snapshot(),
            )
        try:
            observation = tools.dispatch(turn.read_request)
            transcript_entry = InvestigationTranscriptEntry(
                schema_version="dta-v2.investigation-transcript-entry.v1",
                dispatch_ordinal=len(transcript) + 1,
                tool=turn.read_request.tool,
                request_arguments=_json_mapping(turn.raw_arguments),
                observation=build_agent_visible_observation(observation),
            )
            turn_record = _turn_evidence(
                turn=turn,
                stage=ProviderStage.INVESTIGATION,
                ordinal=len(evidence) + 1,
                observation=observation,
            )
        except (TypeError, ValueError):
            return _build_result(
                context=context,
                provider=provider,
                terminal=AgentRunTerminal.FAILED,
                failure_code=AgentFailureCode.PROVIDER_PROTOCOL_FAILURE,
                turns=tuple(evidence),
                evidence_store=tools.snapshot(),
            )
        transcript.append(transcript_entry)
        evidence.append(turn_record)

    snapshot = tools.snapshot()
    refs = diagnosis.supporting_evidence_refs + diagnosis.contradicting_evidence_refs
    if diagnosis.terminal is not Terminal.COMPLETED:
        if refs:
            try:
                resolve_diagnosis_view(snapshot, evidence_refs=refs)
            except (TypeError, ValueError):
                return _build_result(
                    context=context,
                    provider=provider,
                    terminal=AgentRunTerminal.FAILED,
                    failure_code=AgentFailureCode.DIAGNOSIS_BINDING_FAILURE,
                    turns=tuple(evidence),
                    evidence_store=snapshot,
                    diagnosis=diagnosis,
                )
        return _build_result(
            context=context,
            provider=provider,
            terminal=AgentRunTerminal(diagnosis.terminal.value),
            failure_code=None,
            turns=tuple(evidence),
            evidence_store=snapshot,
            diagnosis=diagnosis,
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
        return _build_result(
            context=context,
            provider=provider,
            terminal=AgentRunTerminal.FAILED,
            failure_code=AgentFailureCode.DIAGNOSIS_BINDING_FAILURE,
            turns=tuple(evidence),
            evidence_store=snapshot,
            diagnosis=diagnosis,
        )

    try:
        action_turn = provider.action_selection_turn(
            diagnosis=diagnosis, candidate_view=candidate_view
        )
        if action_turn.action_selection is None:
            raise ValueError("Provider action-selection output is missing")
        decision = ActionSelectionDecision.model_validate(
            action_turn.action_selection.model_dump(mode="python")
        )
        evidence.append(
            _turn_evidence(
                turn=action_turn,
                stage=ProviderStage.ACTION_SELECTION,
                ordinal=len(evidence) + 1,
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
        return _build_result(
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
        )

    return _build_result(
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
    )


__all__ = [
    "AgentFailureCode",
    "AgentRunTerminal",
    "DtaAgentRunResult",
    "ProviderStage",
    "ProviderTurnEvidence",
    "run_tool_using_agent",
]
