"""Strict model-visible and two-stage Agent contracts for DTA v2.1."""

from __future__ import annotations

from datetime import datetime, timedelta
from enum import Enum
from typing import Annotated, Literal

from pydantic import Field, Strict, StrictFloat, StrictInt, StringConstraints, field_validator, model_validator

from ecomsre.dta_v2.agent_contracts import AgentVisibleObservation
from ecomsre.dta_v2.tool_contracts import ToolName
from ecomsre.dta_v2.v21.contracts import (
    ActionDispositionV21,
    ActionParameterV21,
    ActionProposalV21,
    CandidateSetV21,
    DtaDiagnosisV21,
    DtaModelV21,
    EvidenceSourceV21,
    IdentifierV21,
    ResolvedDiagnosisEvidenceViewV21,
    RiskLevelV21,
    RunbookIdV21,
    RunbookParameterSpecV21,
    RunbookParameterTypeV21,
    ScenarioSpecV21,
    Sha256V21,
    semantic_sha256,
    validate_evidence_refs,
)
from ecomsre.dta_v2.v21.registry import RunbookRegistryV21


ModelIdV21 = Annotated[
    str,
    Strict(),
    StringConstraints(strip_whitespace=True, min_length=1, max_length=128),
]
PROVIDER_ADAPTER_VERSION_V21 = "dta-v21.openai-compatible-agent.v1"


class AgentArmV21(str, Enum):
    ONE_SHOT_FULL_CONTEXT = "ONE_SHOT_FULL_CONTEXT"
    FLAT_ADAPTIVE = "FLAT_ADAPTIVE"
    EVIDENCE_GUIDED_PLANNER = "EVIDENCE_GUIDED_PLANNER"


class AlertContextV21(DtaModelV21):
    schema_version: Literal["dta-v21.alert-context.v1"]
    run_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    scenario_id: IdentifierV21
    alert_summary: str = Field(min_length=1, max_length=1000)
    candidate_services: tuple[IdentifierV21, ...] = Field(min_length=1, max_length=8)
    allowed_read_tools: tuple[ToolName, ...] = Field(min_length=1, max_length=5)
    maximum_read_tool_dispatches: Literal[4]
    maximum_repeated_identical_calls: Literal[0]
    maximum_provider_investigation_turns: Literal[5]
    maximum_action_selection_turns: Literal[1]
    started_at: datetime
    ended_at: datetime

    @model_validator(mode="after")
    def require_scope_and_window(self) -> AlertContextV21:
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


def build_alert_context_v21(
    *,
    scenario: ScenarioSpecV21,
    run_id: str,
    started_at: datetime,
    ended_at: datetime,
) -> AlertContextV21:
    scenario = ScenarioSpecV21.model_validate(scenario.model_dump(mode="python"))
    return AlertContextV21(
        schema_version="dta-v21.alert-context.v1",
        run_id=run_id,
        scenario_id=scenario.scenario_id,
        alert_summary=scenario.alert_summary,
        candidate_services=scenario.candidate_services,
        allowed_read_tools=tuple(ToolName(item) for item in scenario.allowed_read_tools),
        maximum_read_tool_dispatches=scenario.maximum_read_tool_dispatches,
        maximum_repeated_identical_calls=scenario.maximum_repeated_identical_calls,
        maximum_provider_investigation_turns=5,
        maximum_action_selection_turns=1,
        started_at=started_at,
        ended_at=ended_at,
    )


class CandidateActionRunbookViewV21(DtaModelV21):
    runbook_id: RunbookIdV21
    target_service: IdentifierV21
    risk_level: RiskLevelV21
    parameters: tuple[RunbookParameterSpecV21, ...] = Field(max_length=8)
    required_evidence_sources: tuple[EvidenceSourceV21, ...] = Field(min_length=1)


class FlatInvestigationStateViewV21(DtaModelV21):
    schema_version: Literal["dta-v21.flat-investigation-state-view.v1"]
    alert_context: AlertContextV21
    observations: tuple[AgentVisibleObservation, ...] = Field(max_length=4)
    prior_normalized_request_sha256: tuple[Sha256V21, ...] = Field(max_length=4)
    remaining_read_dispatches: StrictInt = Field(ge=0, le=4)

    @model_validator(mode="after")
    def require_flat_budget(self) -> FlatInvestigationStateViewV21:
        if len(self.observations) != len(self.prior_normalized_request_sha256):
            raise ValueError("flat investigation request history is partial")
        if self.remaining_read_dispatches != 4 - len(self.observations):
            raise ValueError("flat investigation read budget differs")
        return self


class OneShotFullContextViewV21(DtaModelV21):
    schema_version: Literal["dta-v21.one-shot-full-context-view.v1"]
    alert_context: AlertContextV21
    observations: tuple[AgentVisibleObservation, ...] = Field(min_length=1, max_length=4)
    context_materialization_reads: StrictInt = Field(ge=1, le=4)

    @model_validator(mode="after")
    def require_materialization_count(self) -> OneShotFullContextViewV21:
        if self.context_materialization_reads != len(self.observations):
            raise ValueError("one-shot materialization count differs")
        return self


class CandidateActionViewV21(DtaModelV21):
    schema_version: Literal["dta-v21.candidate-action-view.v1"]
    write_candidates: tuple[CandidateActionRunbookViewV21, ...] = Field(max_length=3)
    allowed_nonwrite_dispositions: tuple[ActionDispositionV21, ...]

    @model_validator(mode="after")
    def require_safe_view(self) -> CandidateActionViewV21:
        keys = tuple(
            (item.runbook_id.value, item.target_service)
            for item in self.write_candidates
        )
        if keys != tuple(sorted(keys)) or len(keys) != len(set(keys)):
            raise ValueError("candidate action view is not canonical and unique")
        if self.allowed_nonwrite_dispositions != (
            ActionDispositionV21.ESCALATE_HUMAN,
            ActionDispositionV21.NO_ACTION,
        ):
            raise ValueError("candidate action view lacks fail-closed dispositions")
        return self


def build_candidate_action_view_v21(
    candidate_set: CandidateSetV21,
) -> CandidateActionViewV21:
    candidate_set = CandidateSetV21.model_validate(
        candidate_set.model_dump(mode="python")
    )
    return CandidateActionViewV21(
        schema_version="dta-v21.candidate-action-view.v1",
        write_candidates=tuple(
            CandidateActionRunbookViewV21(
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


class ActionSelectionDecisionV21(DtaModelV21):
    schema_version: Literal["dta-v21.action-selection-decision.v1"]
    disposition: ActionDispositionV21
    runbook_id: RunbookIdV21 | None = None
    target_service: IdentifierV21 | None = None
    parameters: tuple[ActionParameterV21, ...] = Field(max_length=8)
    supporting_evidence_refs: tuple[str, ...] = Field(max_length=32)
    rationale: str = Field(min_length=1, max_length=1000)

    @field_validator("rationale", mode="before")
    @classmethod
    def require_bounded_rationale(cls, value: object) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("action rationale must be nonempty text")
        if len(value.strip()) > 1000:
            raise ValueError("action rationale exceeds 1000 characters")
        return value.strip()

    @model_validator(mode="after")
    def require_decision_shape(self) -> ActionSelectionDecisionV21:
        names = tuple(item.name for item in self.parameters)
        if names != tuple(sorted(names)) or len(names) != len(set(names)):
            raise ValueError("action parameters are not canonical and unique")
        if self.disposition is ActionDispositionV21.EXECUTE_RUNBOOK:
            if self.runbook_id is None or self.target_service is None:
                raise ValueError("execute decision lacks a Runbook or target")
            if not self.supporting_evidence_refs:
                raise ValueError("execute decision lacks cited evidence")
        elif self.runbook_id is not None or self.target_service is not None or self.parameters:
            raise ValueError("nonwrite decision carries write authority")
        return self


def _validate_parameter_value(
    parameter: ActionParameterV21, specification: RunbookParameterSpecV21
) -> None:
    value = parameter.value
    if specification.parameter_type is RunbookParameterTypeV21.INTEGER:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError("action parameter type differs from visible candidate")
        if specification.minimum is not None and value < specification.minimum:
            raise ValueError("action parameter is below visible minimum")
        if specification.maximum is not None and value > specification.maximum:
            raise ValueError("action parameter is above visible maximum")
    else:
        if not isinstance(value, str) or value not in specification.allowed_values:
            raise ValueError("action parameter is outside visible allowed values")


def build_action_proposal_v21(
    *,
    diagnosis: DtaDiagnosisV21,
    resolved_evidence: ResolvedDiagnosisEvidenceViewV21,
    candidate_set: CandidateSetV21,
    candidate_view: CandidateActionViewV21,
    registry: RunbookRegistryV21,
    decision: ActionSelectionDecisionV21,
) -> ActionProposalV21:
    """Bind a non-authorizing model decision to one trusted candidate."""

    diagnosis = DtaDiagnosisV21.model_validate(diagnosis.model_dump(mode="python"))
    resolved_evidence = ResolvedDiagnosisEvidenceViewV21.model_validate(
        resolved_evidence.model_dump(mode="python")
    )
    candidate_set = CandidateSetV21.model_validate(
        candidate_set.model_dump(mode="python")
    )
    candidate_view = CandidateActionViewV21.model_validate(
        candidate_view.model_dump(mode="python")
    )
    registry = RunbookRegistryV21.model_validate(registry.model_dump(mode="python"))
    decision = ActionSelectionDecisionV21.model_validate(
        decision.model_dump(mode="python")
    )
    if candidate_view != build_candidate_action_view_v21(candidate_set):
        raise ValueError("candidate view differs from the trusted candidate set")
    validate_evidence_refs(
        decision.supporting_evidence_refs,
        run_id=diagnosis.run_id,
        label="action selection evidence",
    )
    resolved_refs = {item.evidence_ref for item in resolved_evidence.evidence}
    if not set(decision.supporting_evidence_refs).issubset(resolved_refs):
        raise ValueError("action selection cites unresolved evidence")

    candidate = None
    if decision.disposition is ActionDispositionV21.EXECUTE_RUNBOOK:
        candidate = next(
            (
                item
                for item in candidate_set.write_candidates
                if item.runbook_id is decision.runbook_id
                and item.target_service == decision.target_service
            ),
            None,
        )
        visible = next(
            (
                item
                for item in candidate_view.write_candidates
                if item.runbook_id is decision.runbook_id
                and item.target_service == decision.target_service
            ),
            None,
        )
        if candidate is None or visible is None:
            raise ValueError("action selection is not an exact visible candidate")
        specifications = {item.name: item for item in candidate.parameters}
        supplied = {item.name: item for item in decision.parameters}
        required = {item.name for item in candidate.parameters if item.required}
        if not required.issubset(supplied) or set(supplied) != set(specifications):
            raise ValueError("action parameters differ from the visible candidate")
        for name, parameter in supplied.items():
            _validate_parameter_value(parameter, specifications[name])
        if candidate.runbook_sha256 != registry.require(candidate.runbook_id).semantic_sha256:
            raise ValueError("candidate Runbook differs from the trusted registry")

    payload: dict[str, object] = {
        "schema_version": "dta-v21.action-proposal.v1",
        "run_id": diagnosis.run_id,
        "disposition": decision.disposition,
        "candidate_set_sha256": candidate_set.candidate_set_sha256,
        "diagnosis_sha256": candidate_set.diagnosis_sha256,
        "resolved_evidence_sha256": resolved_evidence.resolved_evidence_sha256,
        "registry_sha256": registry.registry_sha256,
        "runbook_id": None if candidate is None else candidate.runbook_id,
        "runbook_sha256": None if candidate is None else candidate.runbook_sha256,
        "target_service": None if candidate is None else candidate.target_service,
        "parameters": decision.parameters,
        "supporting_evidence_refs": decision.supporting_evidence_refs,
        "rationale": decision.rationale,
    }
    digest_payload = {
        **payload,
        "disposition": decision.disposition.value,
        "runbook_id": None if candidate is None else candidate.runbook_id.value,
        "parameters": [item.model_dump(mode="json") for item in decision.parameters],
    }
    return ActionProposalV21.model_validate(
        {**payload, "proposal_sha256": semantic_sha256(digest_payload)}
    )


class AgentIdentityManifestV21(DtaModelV21):
    schema_version: Literal["dta-v21.agent-identity.v1"]
    arm: AgentArmV21
    model_id: ModelIdV21
    temperature: StrictFloat = Field(ge=0.0, le=0.0)
    provider_adapter_version: Literal["dta-v21.openai-compatible-agent.v1"]
    system_prompt_sha256: Sha256V21
    tool_schema_sha256: Sha256V21
    planner_schema_sha256: Sha256V21 | None
    diagnosis_schema_sha256: Sha256V21
    action_selection_schema_sha256: Sha256V21
    action_proposal_schema_sha256: Sha256V21
    context_projection_source_sha256: Sha256V21
    registry_sha256: Sha256V21
    candidate_filter_source_sha256: Sha256V21
    max_completion_tokens: StrictInt = Field(ge=1, le=100_000)
    identity_sha256: Sha256V21

    @model_validator(mode="after")
    def require_identity_digest(self) -> AgentIdentityManifestV21:
        if (
            self.arm is AgentArmV21.EVIDENCE_GUIDED_PLANNER
        ) != (self.planner_schema_sha256 is not None):
            raise ValueError("planner schema identity differs from the Agent arm")
        expected = semantic_sha256(
            self.model_dump(mode="json", exclude={"identity_sha256"})
        )
        if self.identity_sha256 != expected:
            raise ValueError("Agent identity digest does not bind the manifest")
        return self


__all__ = (
    "ActionSelectionDecisionV21",
    "AgentArmV21",
    "AgentIdentityManifestV21",
    "AlertContextV21",
    "CandidateActionRunbookViewV21",
    "CandidateActionViewV21",
    "FlatInvestigationStateViewV21",
    "OneShotFullContextViewV21",
    "PROVIDER_ADAPTER_VERSION_V21",
    "build_action_proposal_v21",
    "build_alert_context_v21",
    "build_candidate_action_view_v21",
)
