"""Focused end-to-end checks for the three Phase 2 workflows."""

from pathlib import Path

from ecomsre.backends.replay import ReplayObservabilityBackend, load_replay_case
from ecomsre.model.scripted import ScriptedModelGateway
from ecomsre.phase1.agent import SingleAgent
from ecomsre.phase1.contracts import (
    BudgetLimits,
    InvestigationRequest,
    ModelConfiguration,
)
from ecomsre.phase1.runtime_config import load_agent_settings
from ecomsre.phase2.comparison_adapter import BudgetCaps
from ecomsre.phase2.contracts import ModelOperation, Phase2Variant
from ecomsre.phase2.token_policy import MODEL_SNAPSHOT
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

    assert result.trace.status == "COMPLETED"
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
