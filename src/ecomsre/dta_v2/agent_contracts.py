"""Strict model-visible and identity contracts for the DTA v2 Agent."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Annotated, Literal

from pydantic import (
    Field,
    JsonValue,
    Strict,
    StrictFloat,
    StrictInt,
    StringConstraints,
    field_validator,
    model_validator,
)

from ecomsre.dta_v2.contracts import (
    ActionDisposition,
    ActionParameter,
    CandidateSet,
    DtaModel,
    EvidenceSource,
    RiskLevel,
    RunId,
    RunbookId,
    RunbookParameterSpec,
    ScenarioSpec,
    Sha256,
    _evidence_ref_order,
    _safe_text,
    semantic_sha256,
)
from ecomsre.dta_v2.tool_contracts import ToolName
from ecomsre.dta_v2.tool_contracts import (
    ObservationStatus,
    ReadToolObservation,
    ToolErrorCode,
    ToolResultRecord,
    assert_truth_isolated,
    revalidate_observation,
)


ModelId = Annotated[
    str,
    Strict(),
    StringConstraints(strip_whitespace=True, min_length=1, max_length=128),
]

PROVIDER_ADAPTER_VERSION = "dta-v2.openai-compatible-agent.v1"


class AlertContext(DtaModel):
    """Opaque ScenarioSpec plus one runtime-owned bounded incident window."""

    schema_version: Literal["dta-v2.alert-context.v1"]
    run_id: RunId
    scenario_id: str = Field(min_length=1, max_length=128)
    alert_summary: str = Field(min_length=1, max_length=1000)
    candidate_services: tuple[str, ...] = Field(min_length=1, max_length=8)
    allowed_read_tools: tuple[ToolName, ...] = Field(min_length=1, max_length=5)
    maximum_read_tool_dispatches: Literal[4]
    maximum_repeated_identical_calls: Literal[0]
    started_at: datetime
    ended_at: datetime

    @model_validator(mode="after")
    def require_alert_window(self) -> AlertContext:
        for value in (self.started_at, self.ended_at):
            if value.tzinfo is None or value.utcoffset() != timedelta(0):
                raise ValueError("alert context window must use UTC")
        if self.ended_at <= self.started_at:
            raise ValueError("alert context window is reversed")
        if self.ended_at - self.started_at > timedelta(hours=1):
            raise ValueError("alert context window exceeds one hour")
        if len(self.candidate_services) != len(set(self.candidate_services)):
            raise ValueError("alert candidate services contain duplicates")
        if len(self.allowed_read_tools) != len(set(self.allowed_read_tools)):
            raise ValueError("alert read tools contain duplicates")
        return self


def build_alert_context(
    *,
    scenario: ScenarioSpec,
    run_id: str,
    started_at: datetime,
    ended_at: datetime,
) -> AlertContext:
    scenario = ScenarioSpec.model_validate(scenario.model_dump(mode="python"))
    return AlertContext(
        schema_version="dta-v2.alert-context.v1",
        run_id=run_id,
        scenario_id=scenario.scenario_id,
        alert_summary=scenario.alert_summary,
        candidate_services=scenario.candidate_services,
        allowed_read_tools=tuple(ToolName(item) for item in scenario.allowed_read_tools),
        maximum_read_tool_dispatches=scenario.maximum_read_tool_dispatches,
        maximum_repeated_identical_calls=(
            scenario.maximum_repeated_identical_calls
        ),
        started_at=started_at,
        ended_at=ended_at,
    )


class AgentVisibleObservation(DtaModel):
    """The bounded typed observation projected back into the model transcript."""

    schema_version: Literal["dta-v2.agent-visible-observation.v1"]
    tool: ToolName
    source: EvidenceSource
    evidence_ref: str
    status: ObservationStatus
    error_code: ToolErrorCode | None
    results: tuple[ToolResultRecord, ...] = Field(max_length=40)
    result_count: StrictInt = Field(ge=0, le=40)
    truncated: bool

    @model_validator(mode="after")
    def require_visible_observation(self) -> AgentVisibleObservation:
        if self.result_count != len(self.results):
            raise ValueError("visible observation count differs")
        if self.status is ObservationStatus.SUCCESS:
            if self.error_code is not None:
                raise ValueError("successful visible observation has an error")
        elif self.error_code is None or self.results or self.truncated:
            raise ValueError("failed visible observation is not fail-closed")
        assert_truth_isolated(
            [item.model_dump(mode="json") for item in self.results]
        )
        return self


def build_agent_visible_observation(
    observation: ReadToolObservation,
) -> AgentVisibleObservation:
    observation = revalidate_observation(observation)
    return AgentVisibleObservation(
        schema_version="dta-v2.agent-visible-observation.v1",
        tool=observation.tool,
        source=observation.source,
        evidence_ref=observation.evidence_ref,
        status=observation.status,
        error_code=observation.error_code,
        results=observation.results,
        result_count=observation.result_count,
        truncated=observation.truncated,
    )


class InvestigationTranscriptEntry(DtaModel):
    """One safe read request/result continuation item."""

    schema_version: Literal["dta-v2.investigation-transcript-entry.v1"]
    dispatch_ordinal: StrictInt = Field(ge=1, le=4)
    tool: ToolName
    request_arguments: dict[str, JsonValue]
    observation: AgentVisibleObservation

    @model_validator(mode="after")
    def require_transcript_binding(self) -> InvestigationTranscriptEntry:
        if self.tool is not self.observation.tool:
            raise ValueError("transcript request and observation tools differ")
        assert_truth_isolated(self.request_arguments)
        return self


class CandidateActionRunbookView(DtaModel):
    """The exact Runbook projection that Action Selection may observe."""

    runbook_id: RunbookId
    target_service: str = Field(min_length=1, max_length=128)
    risk_level: RiskLevel
    parameters: tuple[RunbookParameterSpec, ...] = Field(max_length=8)
    required_evidence_sources: tuple[EvidenceSource, ...] = Field(min_length=1)


class CandidateActionView(DtaModel):
    """Hash- and implementation-free model input derived from CandidateSet."""

    schema_version: Literal["dta-v2.candidate-action-view.v1"]
    write_candidates: tuple[CandidateActionRunbookView, ...] = Field(max_length=3)
    allowed_nonwrite_dispositions: tuple[ActionDisposition, ...]

    @model_validator(mode="after")
    def require_safe_view_semantics(self) -> CandidateActionView:
        ids = tuple(item.runbook_id for item in self.write_candidates)
        if len(ids) != len(set(ids)):
            raise ValueError("candidate action view contains duplicate runbooks")
        if self.allowed_nonwrite_dispositions != (
            ActionDisposition.ESCALATE_HUMAN,
            ActionDisposition.NO_ACTION,
        ):
            raise ValueError("candidate action view has unsafe dispositions")
        return self


def build_candidate_action_view(candidate_set: CandidateSet) -> CandidateActionView:
    """Discard every integrity and implementation field before model exposure."""

    candidate_set = CandidateSet.model_validate(candidate_set.model_dump(mode="python"))
    return CandidateActionView(
        schema_version="dta-v2.candidate-action-view.v1",
        write_candidates=tuple(
            CandidateActionRunbookView(
                runbook_id=item.runbook_id,
                target_service=item.target_service,
                risk_level=item.risk_level,
                parameters=item.parameters,
                required_evidence_sources=item.required_evidence_sources,
            )
            for item in candidate_set.write_candidates
        ),
        allowed_nonwrite_dispositions=candidate_set.allowed_nonwrite_dispositions,
    )


class ActionSelectionDecision(DtaModel):
    """Non-authorizing Provider output that runtime binds to trusted candidates."""

    schema_version: Literal["dta-v2.action-selection-decision.v1"]
    disposition: ActionDisposition
    runbook_id: RunbookId | None = None
    target_service: str | None = Field(default=None, min_length=1, max_length=128)
    parameters: tuple[ActionParameter, ...] = Field(max_length=8)
    supporting_evidence_refs: tuple[str, ...] = Field(max_length=32)
    rationale: str = Field(min_length=1, max_length=1000)

    @field_validator("rationale", mode="before")
    @classmethod
    def require_safe_rationale(cls, value: object) -> str:
        return _safe_text(value, field_name="action-selection rationale")

    @model_validator(mode="after")
    def require_decision_semantics(self) -> ActionSelectionDecision:
        names = tuple(item.name for item in self.parameters)
        if len(names) != len(set(names)) or names != tuple(sorted(names)):
            raise ValueError("action-selection parameters are not canonical")
        refs = self.supporting_evidence_refs
        if len(refs) != len(set(refs)):
            raise ValueError("action-selection evidence contains duplicates")
        try:
            canonical_refs = tuple(sorted(refs, key=_evidence_ref_order))
        except ValueError as error:
            raise ValueError("action-selection evidence reference is invalid") from error
        if refs != canonical_refs:
            raise ValueError("action-selection evidence is not canonical")
        if self.disposition is ActionDisposition.EXECUTE_RUNBOOK:
            if self.runbook_id is None or self.target_service is None or not refs:
                raise ValueError("execute decision requires candidate and evidence")
        elif self.runbook_id is not None or self.target_service is not None or self.parameters:
            raise ValueError("nonexecute decision cannot carry write authority")
        return self


class AgentIdentityManifest(DtaModel):
    """Provisional PR-D Provider, Prompt, and schema identity lock."""

    schema_version: Literal["dta-v2.agent-identity.v1"]
    model_id: ModelId
    temperature: StrictFloat = Field(ge=0.0, le=0.0)
    provider_adapter_version: Literal["dta-v2.openai-compatible-agent.v1"]
    prompt_sha256: Sha256
    tool_schema_sha256: Sha256
    diagnosis_schema_sha256: Sha256
    action_selection_schema_sha256: Sha256
    action_proposal_schema_sha256: Sha256
    identity_sha256: Sha256

    @model_validator(mode="after")
    def require_identity_digest(self) -> AgentIdentityManifest:
        expected = semantic_sha256(
            self.model_dump(mode="json", exclude={"identity_sha256"})
        )
        if self.identity_sha256 != expected:
            raise ValueError("identity digest does not bind Agent identity")
        return self


class ProviderUsage(DtaModel):
    """Exact per-call token accounting retained in private evidence."""

    input_tokens: StrictInt = Field(ge=0)
    output_tokens: StrictInt = Field(ge=0)
    total_tokens: StrictInt = Field(ge=0)

    @model_validator(mode="after")
    def require_usage_sum(self) -> ProviderUsage:
        if self.input_tokens + self.output_tokens != self.total_tokens:
            raise ValueError("Provider usage total is inconsistent")
        return self


def build_agent_identity_manifest(
    *,
    model_id: str,
    prompt_sha256: str,
    tool_schema_sha256: str,
    diagnosis_schema_sha256: str,
    action_selection_schema_sha256: str,
    action_proposal_schema_sha256: str,
) -> AgentIdentityManifest:
    payload: dict[str, object] = {
        "schema_version": "dta-v2.agent-identity.v1",
        "model_id": model_id,
        "temperature": 0.0,
        "provider_adapter_version": PROVIDER_ADAPTER_VERSION,
        "prompt_sha256": prompt_sha256,
        "tool_schema_sha256": tool_schema_sha256,
        "diagnosis_schema_sha256": diagnosis_schema_sha256,
        "action_selection_schema_sha256": action_selection_schema_sha256,
        "action_proposal_schema_sha256": action_proposal_schema_sha256,
    }
    return AgentIdentityManifest.model_validate(
        {**payload, "identity_sha256": semantic_sha256(payload)}
    )


__all__ = [
    "ActionSelectionDecision",
    "AgentVisibleObservation",
    "AlertContext",
    "AgentIdentityManifest",
    "CandidateActionRunbookView",
    "CandidateActionView",
    "InvestigationTranscriptEntry",
    "PROVIDER_ADAPTER_VERSION",
    "ProviderUsage",
    "build_agent_identity_manifest",
    "build_alert_context",
    "build_agent_visible_observation",
    "build_candidate_action_view",
]
