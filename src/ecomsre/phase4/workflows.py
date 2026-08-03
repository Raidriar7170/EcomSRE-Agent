"""Phase 4 adapter over the existing Phase 2 Specialist execution boundary."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from ecomsre.backends.replay import ReplayCase
from ecomsre.phase1.contracts import RCADecision
from ecomsre.phase2.comparison_adapter import TypedModelBackend
from ecomsre.phase2.contracts import ModelAllowedActions, Phase2Variant
from ecomsre.phase2.evidence_views import build_judge_request
from ecomsre.phase2.workflows import (
    execute_replay_specialists,
    prepare_specialist_execution,
    specialist_tool_audits,
)
from ecomsre.phase4.contracts import (
    DomainModelCallAudit,
    DomainVariant,
    DomainWorkflowTrace,
)
from ecomsre.phase4.handoff import domain_remediation_disposition
from ecomsre.phase4.judge import (
    invoke_provider_domain_judge,
    judge_domain_request,
)
from ecomsre.phase4.provider import OpenAICompatibleDomainBackend


_VARIANT_MAP = {
    DomainVariant.FIXED_SPECIALIST_WORKFLOW: (
        Phase2Variant.FIXED_SPECIALIST_WORKFLOW
    ),
    DomainVariant.DYNAMIC_MULTI_AGENT: Phase2Variant.DYNAMIC_MULTI_AGENT,
}


def run_domain_replay_workflow(
    *,
    project_root: Path,
    replay_case: ReplayCase,
    variant: DomainVariant,
    phase2_model_backend: TypedModelBackend | None = None,
    expected_provider_identity: str = "phase2-scripted",
    domain_backend: OpenAICompatibleDomainBackend | None = None,
) -> DomainWorkflowTrace:
    """Run one Phase 4 case without evaluator, network, or Phase 3 mutation."""

    selected_variant = DomainVariant(variant)
    if (phase2_model_backend is None) is not (domain_backend is None):
        raise ValueError(
            "real-provider mode requires both Specialist and Domain backends"
        )
    model_mode: Literal["SCRIPTED_REPLAY", "REAL_PROVIDER"] = (
        "REAL_PROVIDER" if domain_backend is not None else "SCRIPTED_REPLAY"
    )
    domain_audits: tuple[DomainModelCallAudit, ...] = ()
    boundary = prepare_specialist_execution(
        project_root=Path(project_root),
        replay_case=replay_case,
        variant=_VARIANT_MAP[selected_variant],
        namespace="phase4-domain",
        model_backend=phase2_model_backend,
        expected_provider_identity=expected_provider_identity,
    )
    try:
        execute_replay_specialists(boundary)
        graph = boundary.graph
        judge_slot_id = boundary.judge_capacity_slot_id
        if graph is None or judge_slot_id is None:
            raise RuntimeError("Phase 2 Specialist graph was not admitted")
        finding_ids = tuple(
            boundary.finding_id_by_node[node.node_id]
            for node in graph.initial_plan.nodes
        )
        request = build_judge_request(
            judge_request_id=f"phase4-domain-{judge_slot_id}",
            run_id=boundary.run_id,
            incident=boundary.replay_case.incident,
            admitted_graph=graph,
            finding_ids=finding_ids,
            finding_store=boundary.finding_store,
            evidence_store=boundary.evidence_store,
            budget_snapshot=boundary.ledger.snapshot(),
            refinement_round=0,
            allowed_actions=ModelAllowedActions.FINAL_ONLY,
            conditional_refinement_bundle_id=None,
        )
        if domain_backend is None:
            result = judge_domain_request(request)
            boundary.ledger.release_capacity_slot(
                expected_snapshot_sequence=boundary.ledger.snapshot().sequence,
                slot_id=judge_slot_id,
            )
        else:
            result, domain_audit = invoke_provider_domain_judge(
                request=request,
                ledger=boundary.ledger,
                authority=boundary.authority,
                judge_capacity_slot_id=judge_slot_id,
                variant=selected_variant,
                backend=domain_backend,
            )
            domain_audits = (domain_audit,)
        disposition = domain_remediation_disposition(
            confirmed_mechanism=(
                result.fault_mechanism
                if result.decision is RCADecision.RCA_CONFIRMED
                else None
            )
        )
        return DomainWorkflowTrace(
            schema_version="phase4.domain-workflow-trace.v1",
            run_id=boundary.run_id,
            variant=selected_variant,
            case_id=boundary.replay_case.case_id,
            status="COMPLETED",
            final_rca=result,
            admitted_graph=graph,
            findings=tuple(
                outcome.finding for outcome in boundary.specialist_outcomes
            ),
            tool_call_records=tuple(
                dispatch.tool_call_record
                for _task, dispatch in boundary.successful_dispatches
            ),
            model_call_audits=boundary.adapter.audit_records,
            domain_model_call_audits=domain_audits,
            tool_call_audits=specialist_tool_audits(boundary),
            budget_audit_events=boundary.ledger.audit_events(),
            final_budget_snapshot=boundary.ledger.snapshot(),
            remediation_disposition=disposition,
            model_mode=model_mode,
            live_environment=False,
            phase5_entered=False,
            terminal_reason=None,
        )
    except Exception as error:
        return DomainWorkflowTrace(
            schema_version="phase4.domain-workflow-trace.v1",
            run_id=boundary.run_id,
            variant=selected_variant,
            case_id=boundary.replay_case.case_id,
            status="FAILED",
            final_rca=None,
            admitted_graph=boundary.graph,
            findings=tuple(
                outcome.finding for outcome in boundary.specialist_outcomes
            ),
            tool_call_records=tuple(
                dispatch.tool_call_record
                for _task, dispatch in boundary.successful_dispatches
            ),
            model_call_audits=boundary.adapter.audit_records,
            domain_model_call_audits=domain_audits,
            tool_call_audits=specialist_tool_audits(boundary),
            budget_audit_events=boundary.ledger.audit_events(),
            final_budget_snapshot=boundary.ledger.snapshot(),
            remediation_disposition=domain_remediation_disposition(
                confirmed_mechanism=None
            ),
            model_mode=model_mode,
            live_environment=False,
            phase5_entered=False,
            terminal_reason=type(error).__name__,
        )
