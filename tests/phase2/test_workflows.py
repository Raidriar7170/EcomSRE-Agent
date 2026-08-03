"""Focused end-to-end checks for the three Phase 2 workflows."""

from pathlib import Path

import pytest

from ecomsre.backends.replay import ReplayObservabilityBackend, load_replay_case
from ecomsre.model.scripted import ScriptedModelGateway
from ecomsre.phase1.agent import SingleAgent
from ecomsre.phase1.contracts import (
    BudgetLimits,
    EvidenceSource,
    FaultMechanism,
    InvestigationRequest,
    MetricsAction,
    ModelConfiguration,
    ReadOnlyToolName,
    RCADecision,
    TracesAction,
)
from ecomsre.phase1.runtime_config import load_agent_settings
from ecomsre.phase2.comparison_adapter import (
    BudgetCaps,
    ModelCompletion,
    ModelInvocation,
)
from ecomsre.phase2.contracts import (
    CommanderRequest,
    InvestigationNode,
    InvestigationPlan,
    ModelInputEnvelope,
    ModelOperation,
    Phase2Variant,
    SpecialistModelRequest,
    SpecialistRole,
)
from ecomsre.phase2.scripted import ScriptedModelBackend
from ecomsre.phase2.token_policy import MODEL_SNAPSHOT, load_token_authority
from ecomsre.phase2.workflows import run_replay_workflow, stable_workflow_run_id


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_workflow_entrypoint_exists() -> None:
    assert callable(run_replay_workflow)


def test_all_workflows_share_the_revised_32000_token_cap() -> None:
    assert BudgetCaps().model_calls == 8
    assert BudgetCaps().tool_calls == 8
    assert BudgetCaps().total_tokens == 32_000


def test_fixed_control_executes_four_specialists_and_final_judge() -> None:
    replay_case = load_replay_case(
        PROJECT_ROOT / "config/phase1/replay-cases/agent-visible",
        "ad-partial-failure-complete",
    )

    result = run_replay_workflow(
        project_root=PROJECT_ROOT,
        replay_case=replay_case,
        variant=Phase2Variant.FIXED_SPECIALIST_WORKFLOW,
    )

    assert result.trace.status == "COMPLETED", (
        result.trace.terminal_failure_code,
        result.trace.terminal_reason,
    )
    assert tuple(
        record.operation for record in result.trace.model_call_audits
    ) == (
        ModelOperation.SPECIALIST_MODEL,
        ModelOperation.SPECIALIST_MODEL,
        ModelOperation.SPECIALIST_MODEL,
        ModelOperation.SPECIALIST_MODEL,
        ModelOperation.FINAL_JUDGE_MODEL,
    )
    assert len(result.trace.tool_call_audits) == 4


def test_single_agent_adapter_preserves_phase1_action_semantics() -> None:
    replay_case = load_replay_case(
        PROJECT_ROOT / "config/phase1/replay-cases/agent-visible",
        "ad-partial-failure-complete",
    )
    run_id = stable_workflow_run_id(
        replay_case.case_id,
        Phase2Variant.SINGLE_AGENT,
    )

    adapted = run_replay_workflow(
        project_root=PROJECT_ROOT,
        replay_case=replay_case,
        variant=Phase2Variant.SINGLE_AGENT,
        run_id=run_id,
    )
    settings = load_agent_settings(PROJECT_ROOT)
    baseline = SingleAgent(
        gateway=ScriptedModelGateway(),
        backend=ReplayObservabilityBackend(replay_case),
        model_configuration=ModelConfiguration(
            model_name=MODEL_SNAPSHOT,
            temperature=0.0,
            model_timeout_seconds=settings.model_timeout_seconds,
        ),
        tool_timeout_seconds=settings.tool_timeout_seconds,
    ).run(
        InvestigationRequest(
            schema_version="phase1.investigation-request.v1",
            request_id=f"phase2-{replay_case.case_id}-single",
            run_id=run_id,
            agent_id="single-agent",
            task_id="root-cause-analysis",
            incident=replay_case.incident,
            budgets=BudgetLimits(
                max_model_calls=8,
                max_tool_calls=8,
                max_total_tokens=32_000,
            ),
        )
    )

    assert adapted.trace.status == "COMPLETED"
    assert adapted.phase1_report is not None
    assert tuple(
        record.response.action.model_dump(mode="json")
        for record in adapted.phase1_report.model_call_records
    ) == tuple(
        record.response.action.model_dump(mode="json")
        for record in baseline.model_call_records
    )
    assert adapted.phase1_report.final_rca == baseline.final_rca
    charged_tool_records = tuple(
        record
        for record in adapted.phase1_report.tool_call_records
        if record.budget_consumed
    )
    assert adapted.trace.final_budget_snapshot.charged_tool_calls == len(
        charged_tool_records
    )
    assert len(adapted.trace.tool_call_audits) == len(charged_tool_records)


def test_dynamic_runs_one_bounded_refinement_and_complete_trace() -> None:
    replay_case = load_replay_case(
        PROJECT_ROOT / "config/phase1/replay-cases/agent-visible",
        "ad-partial-failure-complete",
    )
    replay_without_metrics = replay_case.model_copy(
        update={
            "metrics": replay_case.metrics.model_copy(
                update={"observations": (), "raw_artifact_indices": ()},
            )
        }
    )

    result = run_replay_workflow(
        project_root=PROJECT_ROOT,
        replay_case=replay_without_metrics,
        variant=Phase2Variant.DYNAMIC_MULTI_AGENT,
        allow_refinement=True,
    )

    operations = tuple(
        record.operation for record in result.trace.model_call_audits
    )
    assert result.trace.status == "COMPLETED"
    assert result.trace.admitted_graph is not None
    assert 1 <= len(result.trace.admitted_graph.initial_plan.nodes) <= 3
    assert result.trace.admitted_graph.refinement_fragment is not None
    assert len(result.trace.admitted_graph.refinement_fragment.nodes) == 1
    assert operations.count(ModelOperation.COMMANDER_MODEL) == 1
    assert operations.count(ModelOperation.SPECIALIST_MODEL) == 3
    assert operations.count(ModelOperation.FIRST_JUDGE_MODEL) == 1
    assert operations.count(ModelOperation.FINAL_JUDGE_MODEL) == 1
    assert len(result.trace.tool_call_records) == 3
    assert len(result.trace.tool_call_audits) == 3
    assert result.trace.final_budget_snapshot.charged_model_calls == len(operations)
    assert result.trace.final_budget_snapshot.charged_tool_calls == 3
    assert result.trace.final_budget_snapshot.cumulative_tokens == sum(
        record.total_tokens or 0 for record in result.trace.model_call_audits
    )


def test_dynamic_workflow_executes_dependency_layers_not_declaration_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    replay_case = load_replay_case(
        PROJECT_ROOT / "config/phase1/replay-cases/agent-visible",
        "ad-partial-failure-complete",
    )

    def dependent_plan(request: CommanderRequest) -> InvestigationPlan:
        started_at = request.allowed_started_at
        ended_at = request.allowed_ended_at
        prerequisite = InvestigationNode(
            schema_version="phase2.investigation-node.v1",
            node_id="metrics-prerequisite",
            source=EvidenceSource.METRICS,
            specialist_role=SpecialistRole.METRICS_AGENT,
            tool_name=ReadOnlyToolName.QUERY_METRICS,
            query=MetricsAction(
                action_type="metrics",
                started_at=started_at,
                ended_at=ended_at,
                service="ad",
            ),
            depends_on=(),
            objective="Establish the prerequisite service signal.",
            query_started_at=started_at,
            query_ended_at=ended_at,
            priority=1,
        )
        dependent = InvestigationNode(
            schema_version="phase2.investigation-node.v1",
            node_id="traces-dependent",
            source=EvidenceSource.TRACES,
            specialist_role=SpecialistRole.TRACE_AGENT,
            tool_name=ReadOnlyToolName.SEARCH_TRACES,
            query=TracesAction(
                action_type="traces",
                started_at=started_at,
                ended_at=ended_at,
                service="ad",
            ),
            depends_on=(prerequisite.node_id,),
            objective="Use the prerequisite signal to inspect request traces.",
            query_started_at=started_at,
            query_ended_at=ended_at,
            priority=0,
        )
        return InvestigationPlan(
            schema_version="phase2.investigation-plan.v1",
            run_id=request.run_id,
            incident_id=request.incident.incident_id,
            plan_id="dependent-plan",
            nodes=(dependent, prerequisite),
            planning_rationale="Exercise dependency ordering explicitly.",
            budget_snapshot_id=request.budget_snapshot.snapshot_id,
        )

    monkeypatch.setattr(
        ScriptedModelBackend,
        "_commander_plan",
        staticmethod(dependent_plan),
    )
    specialist_requests: list[SpecialistModelRequest] = []
    original_complete = ScriptedModelBackend.complete

    def recording_complete(
        self: ScriptedModelBackend,
        invocation: ModelInvocation,
        *,
        envelope: ModelInputEnvelope,
        exact_input_tokens: int,
        max_completion_tokens: int,
    ) -> ModelCompletion:
        if invocation.operation is ModelOperation.SPECIALIST_MODEL:
            specialist_requests.append(
                SpecialistModelRequest.model_validate(envelope.request)
            )
        return original_complete(
            self,
            invocation,
            envelope=envelope,
            exact_input_tokens=exact_input_tokens,
            max_completion_tokens=max_completion_tokens,
        )

    monkeypatch.setattr(ScriptedModelBackend, "complete", recording_complete)

    result = run_replay_workflow(
        project_root=PROJECT_ROOT,
        replay_case=replay_case,
        variant=Phase2Variant.DYNAMIC_MULTI_AGENT,
    )

    assert result.trace.status == "COMPLETED", (
        result.trace.terminal_failure_code,
        result.trace.terminal_reason,
    )
    assert tuple(finding.node_id for finding in result.trace.findings) == (
        "metrics-prerequisite",
        "traces-dependent",
    )
    assert len(specialist_requests) == 2
    prerequisite_finding = result.trace.findings[0]
    dependent_request = specialist_requests[1]
    assert dependent_request.dependency_finding_ids == (
        prerequisite_finding.finding_id,
    )
    assert tuple(
        evidence.evidence_ref
        for evidence in dependent_request.resolved_dependency_evidence_view.evidence
    ) == prerequisite_finding.evidence_refs


def test_fixed_workflow_uses_an_explicit_typed_backend() -> None:
    replay_case = load_replay_case(
        PROJECT_ROOT / "config/phase1/replay-cases/agent-visible",
        "ad-partial-failure-complete",
    )
    backend = ScriptedModelBackend(
        token_authority=load_token_authority(PROJECT_ROOT),
        provider_identity="injected-provider",
    )

    result = run_replay_workflow(
        project_root=PROJECT_ROOT,
        replay_case=replay_case,
        variant=Phase2Variant.FIXED_SPECIALIST_WORKFLOW,
        model_backend=backend,
        expected_provider_identity="injected-provider",
    )

    assert result.trace.status == "COMPLETED"
    assert backend.calls == 5
    assert {
        record.observed_provider_identity
        for record in result.trace.model_call_audits
    } == {"injected-provider"}


@pytest.mark.parametrize(
    ("case_id", "root_service", "fault_mechanism"),
    (
        (
            "ad-partial-failure-complete",
            "ad",
            FaultMechanism.RUNTIME_CONFIGURATION_FAILURE,
        ),
        (
            "recommendation-cache-failure",
            "recommendation",
            FaultMechanism.CACHE_BACKEND_TIMEOUT,
        ),
    ),
)
def test_dynamic_scripted_evidence_confirmation_is_generic_and_opt_in(
    case_id: str,
    root_service: str,
    fault_mechanism: FaultMechanism,
) -> None:
    replay_case = load_replay_case(
        PROJECT_ROOT / "config/phase1/replay-cases/agent-visible",
        case_id,
    )
    backend = ScriptedModelBackend(
        token_authority=load_token_authority(PROJECT_ROOT),
        enable_evidence_confirmation=True,
    )

    result = run_replay_workflow(
        project_root=PROJECT_ROOT,
        replay_case=replay_case,
        variant=Phase2Variant.DYNAMIC_MULTI_AGENT,
        allow_refinement=True,
        model_backend=backend,
    )

    assert result.trace.status == "COMPLETED"
    assert result.trace.final_rca is not None
    assert result.trace.final_rca.decision is RCADecision.RCA_CONFIRMED
    assert result.trace.final_rca.root_service == root_service
    assert result.trace.final_rca.fault_mechanism is fault_mechanism
    refs = set(result.trace.final_rca.supporting_evidence)
    sources = {
        item.source
        for record in result.trace.tool_call_records
        for item in record.evidence
        if item.evidence_ref in refs
    }
    assert len(sources) >= 2
    if fault_mechanism is FaultMechanism.RUNTIME_CONFIGURATION_FAILURE:
        assert EvidenceSource.CHANGES in sources
        assert result.trace.admitted_graph is not None
        assert result.trace.admitted_graph.refinement_fragment is not None


def test_dynamic_scripted_evidence_confirmation_fails_closed_without_anomaly() -> None:
    replay_case = load_replay_case(
        PROJECT_ROOT / "config/phase1/replay-cases/agent-visible",
        "no-real-incident",
    )
    backend = ScriptedModelBackend(
        token_authority=load_token_authority(PROJECT_ROOT),
        enable_evidence_confirmation=True,
    )

    result = run_replay_workflow(
        project_root=PROJECT_ROOT,
        replay_case=replay_case,
        variant=Phase2Variant.DYNAMIC_MULTI_AGENT,
        allow_refinement=True,
        model_backend=backend,
    )

    assert result.trace.status == "COMPLETED"
    assert result.trace.final_rca is not None
    assert result.trace.final_rca.decision is RCADecision.ABSTAIN


def test_scripted_evidence_confirmation_is_disabled_by_default() -> None:
    replay_case = load_replay_case(
        PROJECT_ROOT / "config/phase1/replay-cases/agent-visible",
        "ad-partial-failure-complete",
    )

    result = run_replay_workflow(
        project_root=PROJECT_ROOT,
        replay_case=replay_case,
        variant=Phase2Variant.DYNAMIC_MULTI_AGENT,
        allow_refinement=True,
    )

    assert result.trace.status == "COMPLETED"
    assert result.trace.final_rca is not None
    assert result.trace.final_rca.decision is RCADecision.ABSTAIN
