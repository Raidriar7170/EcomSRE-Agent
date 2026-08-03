"""Deterministic typed replay backend derived only from model-visible inputs."""

from __future__ import annotations

import hashlib
from typing import cast

from pydantic import JsonValue

from ecomsre.phase1.contracts import (
    ChangesAction,
    EvidenceSource,
    RCAResult,
    RCADecision,
    RecommendedNextAction,
    LogsAction,
    MetricsAction,
    ReadOnlyToolName,
    TracesAction,
    ModelRequest,
    ModelResponse,
    ModelUsage,
)
from ecomsre.model.scripted import ScriptedModelGateway
from ecomsre.phase2.comparison_adapter import (
    ModelCompletion,
    ModelInvocation,
)
from ecomsre.phase2.contracts import (
    AdditionalInvestigationRequest,
    CommanderRequest,
    FindingHypothesis,
    HypothesisEvidenceGroup,
    InvestigationNode,
    InvestigationPlan,
    JudgeFinalResult,
    JudgeRequest,
    MissingEvidenceItem,
    ModelInputEnvelope,
    ModelOperation,
    ModelAllowedActions,
    SpecialistFinding,
    SpecialistModelRequest,
    SpecialistRole,
)
from ecomsre.phase2.token_policy import TokenAuthority, canonical_json_bytes
from ecomsre.phase2.token_policy import build_model_input_envelope


_SOURCE_ORDER = (
    EvidenceSource.METRICS,
    EvidenceSource.LOGS,
    EvidenceSource.TRACES,
    EvidenceSource.CHANGES,
)
_SOURCE_BINDINGS = {
    EvidenceSource.METRICS: (
        SpecialistRole.METRICS_AGENT,
        ReadOnlyToolName.QUERY_METRICS,
        MetricsAction,
        "Measure the affected service signal inside the admitted window.",
    ),
    EvidenceSource.LOGS: (
        SpecialistRole.LOGS_AGENT,
        ReadOnlyToolName.SEARCH_LOGS,
        LogsAction,
        "Inspect bounded service errors inside the admitted window.",
    ),
    EvidenceSource.TRACES: (
        SpecialistRole.TRACE_AGENT,
        ReadOnlyToolName.SEARCH_TRACES,
        TracesAction,
        "Trace the affected request path inside the admitted window.",
    ),
    EvidenceSource.CHANGES: (
        SpecialistRole.CHANGE_AGENT,
        ReadOnlyToolName.LIST_CHANGES,
        ChangesAction,
        "Review bounded service changes inside the admitted window.",
    ),
}


class ExactTokenScriptedGateway:
    """Phase 1 scripted policy with usage from the canonical Phase 2 envelope."""

    provider_name = ScriptedModelGateway.provider_name

    def __init__(self, token_authority: TokenAuthority) -> None:
        if not isinstance(token_authority, TokenAuthority):
            raise TypeError("token_authority must be TokenAuthority")
        self._token_authority = token_authority
        self._inner = ScriptedModelGateway()

    def complete(self, request: ModelRequest) -> ModelResponse:
        response = self._inner.complete(request)
        operation = ModelOperation.SINGLE_AGENT_MODEL
        actions = ModelAllowedActions.PHASE1_ACTION_CATALOG
        envelope = build_model_input_envelope(
            self._token_authority.core,
            operation,
            actions,
            request,
        )
        input_tokens = self._token_authority.exact_input_tokens(envelope)
        action_payload = cast(
            dict[str, JsonValue], response.action.model_dump(mode="json")
        )
        output_tokens = len(
            self._token_authority.encoding.encode(
                canonical_json_bytes(action_payload).decode("utf-8"),
                allowed_special=set(),
                disallowed_special="all",
            )
        )
        return response.model_copy(
            update={
                "usage": ModelUsage(
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    total_tokens=input_tokens + output_tokens,
                )
            }
        )


class ScriptedModelBackend:
    """Offline backend with one deterministic policy per typed operation."""

    def __init__(
        self,
        *,
        token_authority: TokenAuthority,
        provider_identity: str = "phase2-scripted",
    ) -> None:
        self._token_authority = token_authority
        self._provider_identity = provider_identity
        self._seen_invocation_ids: set[str] = set()
        self._invocations: tuple[ModelInvocation, ...] = ()

    @property
    def calls(self) -> int:
        return len(self._invocations)

    @property
    def invocations(self) -> tuple[ModelInvocation, ...]:
        return self._invocations

    def complete(
        self,
        invocation: ModelInvocation,
        *,
        envelope: ModelInputEnvelope,
        exact_input_tokens: int,
        max_completion_tokens: int,
    ) -> ModelCompletion:
        del envelope
        if invocation.invocation_id in self._seen_invocation_ids:
            raise ValueError("duplicate scripted invocation")
        self._seen_invocation_ids.add(invocation.invocation_id)
        self._invocations = (*self._invocations, invocation)
        response: (
            InvestigationPlan
            | SpecialistFinding
            | JudgeFinalResult
            | AdditionalInvestigationRequest
        )
        if invocation.operation is ModelOperation.COMMANDER_MODEL:
            response = self._commander_plan(
                cast(CommanderRequest, invocation.request)
            )
        elif invocation.operation is ModelOperation.SPECIALIST_MODEL:
            response = self._specialist_finding(
                cast(SpecialistModelRequest, invocation.request)
            )
        elif invocation.operation in {
            ModelOperation.FIRST_JUDGE_MODEL,
            ModelOperation.FINAL_JUDGE_MODEL,
        }:
            response = self._judge_action(cast(JudgeRequest, invocation.request))
        else:
            raise ValueError("scripted operation is not implemented")
        response_payload = cast(
            dict[str, JsonValue], response.model_dump(mode="json")
        )
        output_tokens = len(
            self._token_authority.encoding.encode(
                canonical_json_bytes(response_payload).decode("utf-8"),
                allowed_special=set(),
                disallowed_special="all",
            )
        )
        if output_tokens > max_completion_tokens:
            raise ValueError("scripted response exceeds admitted completion capacity")
        return ModelCompletion(
            schema_version="phase2.model-completion.v1",
            provider_identity=self._provider_identity,
            response=response_payload,
            input_tokens=exact_input_tokens,
            output_tokens=output_tokens,
            total_tokens=exact_input_tokens + output_tokens,
            phase1_response=None,
        )

    @staticmethod
    def _commander_plan(request: CommanderRequest) -> InvestigationPlan:
        visible = (
            f"{request.incident.summary} {request.incident.affected_sli}"
        ).casefold()
        selected = {EvidenceSource.METRICS}
        if any(term in visible for term in ("error", "failure", "failed")):
            selected.add(EvidenceSource.LOGS)
        if "latency" in visible:
            selected.add(EvidenceSource.TRACES)
        if any(term in visible for term in ("change", "config", "rollout", "deploy")):
            selected.add(EvidenceSource.CHANGES)
        sources = tuple(source for source in _SOURCE_ORDER if source in selected)[:3]
        service = request.incident.alert_source_service
        nodes: list[InvestigationNode] = []
        for priority, source in enumerate(sources):
            role, tool_name, action_type, objective = _SOURCE_BINDINGS[source]
            query = action_type(
                action_type=source.value.lower(),
                started_at=request.allowed_started_at,
                ended_at=request.allowed_ended_at,
                service=service,
            )
            nodes.append(
                InvestigationNode(
                    schema_version="phase2.investigation-node.v1",
                    node_id=f"{source.value.lower()}-initial-1",
                    source=source,
                    specialist_role=role,
                    tool_name=tool_name,
                    query=query,
                    depends_on=(),
                    objective=objective,
                    query_started_at=request.allowed_started_at,
                    query_ended_at=request.allowed_ended_at,
                    priority=priority,
                )
            )
        identity = hashlib.sha256(
            canonical_json_bytes(request.model_dump(mode="json"))
        ).hexdigest()[:12]
        return InvestigationPlan(
            schema_version="phase2.investigation-plan.v1",
            run_id=request.run_id,
            incident_id=request.incident.incident_id,
            plan_id=f"plan-{identity}",
            nodes=tuple(nodes),
            planning_rationale=(
                "Use the smallest ordered set of visible read-only sources needed "
                "to investigate the reported service signal."
            ),
            budget_snapshot_id=request.budget_snapshot.snapshot_id,
        )

    @staticmethod
    def _specialist_finding(
        request: SpecialistModelRequest,
    ) -> SpecialistFinding:
        task = request.task
        visible_evidence = (
            *request.resolved_dependency_evidence_view.evidence,
            *request.new_evidence,
        )
        refs = tuple(dict.fromkeys(item.evidence_ref for item in visible_evidence))
        identity = hashlib.sha256(
            canonical_json_bytes(request.model_dump(mode="json"))
        ).hexdigest()[:12]
        hypothesis_id = f"hypothesis-{identity}"
        service = (
            visible_evidence[0].service
            if visible_evidence
            else task.query.service
        )
        supporting = (
            (
                HypothesisEvidenceGroup(
                    schema_version="phase2.hypothesis-evidence-group.v1",
                    hypothesis_id=hypothesis_id,
                    evidence_refs=refs,
                ),
            )
            if refs
            else ()
        )
        missing = (
            ()
            if refs
            else (
                MissingEvidenceItem(
                    schema_version="phase2.missing-evidence-item.v1",
                    question="Which bounded observation establishes the service signal?",
                    desired_source=task.source,
                ),
            )
        )
        return SpecialistFinding(
            schema_version="phase2.specialist-finding.v1",
            finding_id=f"finding-{identity}",
            run_id=task.run_id,
            incident_id=task.incident_id,
            plan_id=task.plan_id,
            node_id=task.node_id,
            source=task.source,
            specialist_role=task.specialist_role,
            evidence_refs=refs,
            hypotheses=(
                FindingHypothesis(
                    schema_version="phase2.finding-hypothesis.v1",
                    hypothesis_id=hypothesis_id,
                    root_service=service,
                    fault_mechanism=None,
                    claim="Visible observations support the bounded service signal.",
                ),
            ),
            supporting_evidence_refs=supporting,
            contradicting_evidence_refs=(),
            missing_evidence=missing,
            confidence=0.6 if refs else 0.2,
            finding_rationale=(
                "The finding is limited to the supplied tool record and resolved "
                "dependency observations."
            ),
        )

    @staticmethod
    def _abstain() -> RCAResult:
        return RCAResult(
            schema_version="phase1.rca-result.v1",
            decision=RCADecision.ABSTAIN,
            root_service=None,
            fault_mechanism=None,
            causal_chain=(),
            affected_sli=None,
            supporting_evidence=(),
            contradicting_evidence=(),
            missing_evidence=(),
            confidence=0.0,
            decision_rationale=(
                "There is no confirmed incident from the bounded observations."
            ),
            recommended_next_action=(
                RecommendedNextAction.REVIEW_BOUNDED_REPLAY_EVIDENCE
            ),
        )

    @classmethod
    def _judge_action(
        cls,
        request: JudgeRequest,
    ) -> JudgeFinalResult | AdditionalInvestigationRequest:
        missing = tuple(
            item
            for finding in request.findings
            for item in finding.missing_evidence
        )
        if (
            request.allowed_actions is ModelAllowedActions.FINAL_OR_REFINEMENT
            and missing
        ):
            desired = missing[0].desired_source
            role, tool_name, action_type, objective = _SOURCE_BINDINGS[desired]
            query = action_type(
                action_type=desired.value.lower(),
                started_at=request.incident.started_at,
                ended_at=request.incident.ended_at,
                service=request.incident.alert_source_service,
            )
            bundle_id = request.conditional_refinement_bundle_id
            assert bundle_id is not None
            first_finding = request.findings[0]
            return AdditionalInvestigationRequest(
                schema_version="phase2.additional-investigation-request.v1",
                action_type="ADDITIONAL_INVESTIGATION",
                run_id=request.run_id,
                incident_id=request.incident.incident_id,
                parent_plan_id=request.admitted_graph.initial_plan.plan_id,
                request_id=f"refinement-{request.judge_request_id}",
                nodes=(
                    InvestigationNode(
                        schema_version="phase2.investigation-node.v1",
                        node_id=f"{desired.value.lower()}-refinement-1",
                        source=desired,
                        specialist_role=role,
                        tool_name=tool_name,
                        query=query,
                        depends_on=(first_finding.node_id,),
                        objective=objective,
                        query_started_at=request.incident.started_at,
                        query_ended_at=request.incident.ended_at,
                        priority=0,
                    ),
                ),
                target_hypothesis_ids=(
                    first_finding.hypotheses[0].hypothesis_id,
                ),
                reason="One bounded source observation remains missing.",
                conditional_refinement_bundle_id=bundle_id,
                fallback_rca_result=cls._abstain(),
            )
        return JudgeFinalResult(
            schema_version="phase2.judge-final-result.v1",
            action_type="FINAL_RCA",
            run_id=request.run_id,
            incident_id=request.incident.incident_id,
            rca_result=cls._abstain(),
            finding_ids_considered=request.finding_ids,
            refinement_used=request.refinement_round == 1,
            judge_request_id=request.judge_request_id,
        )
