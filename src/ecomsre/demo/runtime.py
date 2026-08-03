"""Thin Phase 2 -> Phase 3 integration for the public offline demo."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator

from ecomsre.backends.replay import load_replay_case
from ecomsre.phase1.contracts import FaultMechanism, RCADecision
from ecomsre.phase1.evidence import EvidenceStore
from ecomsre.phase2.contracts import Phase2Variant
from ecomsre.phase2.scripted import ScriptedModelBackend
from ecomsre.phase2.token_policy import load_token_authority
from ecomsre.phase2.workflows import WorkflowRunTrace, run_replay_workflow
from ecomsre.phase3.contracts import (
    ActionType,
    ApprovalDecision,
    ApprovalMode,
    ApprovalOutcome,
    ConfigurationState,
    MutationBehavior,
    PolicyOutcome,
    RemediationPlan,
    ReplayHealthStatus,
    ReplayResourceSnapshot,
    RollbackBehavior,
    RollbackOutcome,
    TerminalOutcome,
    VerificationOutcome,
    semantic_sha256,
)
from ecomsre.phase3.handoff import build_diagnosis_handoff
from ecomsre.phase3.planner import plan_remediation
from ecomsre.phase3.workflow import run_remediation_replay

CASE_ID = "ad-partial-failure-complete"
ATTEMPT_ID = "agent-mainline-v1"
RESOURCE_ID = "replay-ad-runtime-config"


class DemoModel(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        revalidate_instances="always",
    )


class DiagnosisSummary(DemoModel):
    variant: Literal[Phase2Variant.DYNAMIC_MULTI_AGENT]
    backend: Literal["SCRIPTED_REPLAY"]
    trace_status: Literal["COMPLETED"]
    decision: Literal[RCADecision.RCA_CONFIRMED]
    root_service: Literal["ad"]
    fault_mechanism: Literal[FaultMechanism.RUNTIME_CONFIGURATION_FAILURE]
    supporting_evidence_count: StrictInt = Field(ge=2)


class RemediationSummary(DemoModel):
    backend: Literal["REPLAY"]
    selected_action: Literal[ActionType.RESTORE_FROZEN_SERVICE_CONFIGURATION]
    policy_decision: Literal[PolicyOutcome.ALLOW]
    approval_mode: Literal[ApprovalMode.LOCAL_TEST_AUTO_APPROVAL]
    forward_mutation_count: Literal[1]
    verification_result: Literal[VerificationOutcome.VERIFIED]
    rollback_count: Literal[0]
    terminal_status: Literal[TerminalOutcome.REMEDIATION_VERIFIED]


class UsageSummary(DemoModel):
    model_calls: StrictInt = Field(ge=1)
    tool_calls: StrictInt = Field(ge=1)
    total_tokens: StrictInt = Field(ge=1)


class ExecutionBoundary(DemoModel):
    provider_called: Literal[False]
    docker_called: Literal[False]
    live_execution: Literal[False]
    evaluator_truth_read: Literal[False]
    phase4_entered: Literal[False]
    phase5_entered: Literal[False]


class AgentMainlineReport(DemoModel):
    schema_version: Literal["ecomsre.agent-mainline-demo-report.v1"]
    case: Literal["ad-partial-failure-complete"]
    run_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    diagnosis: DiagnosisSummary
    remediation: RemediationSummary
    usage: UsageSummary
    execution_boundary: ExecutionBoundary
    phase3_report_semantic_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    semantic_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_semantic_digest(self) -> AgentMainlineReport:
        expected = semantic_sha256(
            self.model_dump(mode="json", exclude={"semantic_sha256"})
        )
        if self.semantic_sha256 != expected:
            raise ValueError("demo report semantic digest is invalid")
        return self


def canonical_report_bytes(report: AgentMainlineReport) -> bytes:
    """Return stable UTF-8 JSON bytes for exact replay comparison."""

    validated = AgentMainlineReport.model_validate(report)
    return (
        json.dumps(
            validated.model_dump(mode="json"),
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def _rebuild_evidence_store(trace: WorkflowRunTrace) -> EvidenceStore:
    store = EvidenceStore(trace.run_id)
    seen: dict[str, object] = {}
    for record in trace.tool_call_records:
        for item in record.evidence:
            prior = seen.get(item.evidence_ref)
            if prior is not None:
                if prior != item:
                    raise ValueError(
                        "duplicate Phase 2 evidence ref has different content"
                    )
                continue
            rebuilt = store.add(
                source=item.source,
                observation_type=item.observation_type,
                attributes=item.attributes,
                raw_artifact_ref=item.raw_artifact_ref,
                raw_artifact_sha256=item.raw_artifact_sha256,
                limitations=item.limitations,
                summary=item.summary,
                started_at=item.started_at,
                ended_at=item.ended_at,
                service=item.service,
            )
            if rebuilt != item:
                raise ValueError("rebuilt Phase 2 evidence differs from the trace")
            seen[item.evidence_ref] = item
    return store


def run_agent_mainline_demo(project_root: Path) -> AgentMainlineReport:
    """Run one deterministic, provider-free Agent mainline replay."""

    root = Path(project_root).resolve()
    replay_case = load_replay_case(
        root / "config/phase1/replay-cases/agent-visible",
        CASE_ID,
    )
    model_backend = ScriptedModelBackend(
        token_authority=load_token_authority(root),
        enable_evidence_confirmation=True,
    )
    workflow = run_replay_workflow(
        project_root=root,
        replay_case=replay_case,
        variant=Phase2Variant.DYNAMIC_MULTI_AGENT,
        allow_refinement=True,
        model_backend=model_backend,
    )
    trace = WorkflowRunTrace.model_validate(workflow.trace)
    if trace.status != "COMPLETED" or trace.final_rca is None:
        raise ValueError("Phase 2 demo diagnosis did not complete")

    handoff = build_diagnosis_handoff(
        trace=trace,
        incident=replay_case.incident,
    )
    evidence_store = _rebuild_evidence_store(trace)
    resource = ReplayResourceSnapshot(
        schema_version="phase3.replay-resource-snapshot.v1",
        backend="REPLAY_ONLY",
        owner_run_id=trace.run_id,
        resource_id=RESOURCE_ID,
        service="ad",
        configuration_state=ConfigurationState.FAULTED,
        state_version=1,
    )
    planned = plan_remediation(
        handoff=handoff,
        resource=resource,
        attempt_id=ATTEMPT_ID,
        evidence_store=evidence_store,
    )
    if not isinstance(planned, RemediationPlan):
        raise TypeError(f"Phase 3 Planner returned {planned.reason_code.value}")
    approval = ApprovalDecision(
        schema_version="phase3.approval-decision.v1",
        mode=ApprovalMode.LOCAL_TEST_AUTO_APPROVAL,
        run_id=planned.run_id,
        incident_id=planned.incident_id,
        attempt_id=planned.attempt_id,
        action_id=planned.action.action_id,
        plan_digest=planned.plan_digest,
        decision=ApprovalOutcome.APPROVED,
    )
    remediation = run_remediation_replay(
        handoff=handoff,
        initial_resource=resource,
        attempt_id=ATTEMPT_ID,
        approval=approval,
        mutation_behavior=MutationBehavior.APPLY,
        health_status=ReplayHealthStatus.RECOVERED,
        rollback_behavior=RollbackBehavior.RESTORE,
        evidence_store=evidence_store,
        local_test_mode=True,
    )
    if (
        remediation.policy_outcome is not PolicyOutcome.ALLOW
        or remediation.verification_outcome is not VerificationOutcome.VERIFIED
        or remediation.terminal_outcome is not TerminalOutcome.REMEDIATION_VERIFIED
        or remediation.rollback_outcome is not RollbackOutcome.NOT_REQUIRED
    ):
        raise ValueError("Phase 3 demo remediation did not verify")

    budget = trace.final_budget_snapshot
    payload: dict[str, object] = {
        "schema_version": "ecomsre.agent-mainline-demo-report.v1",
        "case": CASE_ID,
        "run_id": trace.run_id,
        "diagnosis": DiagnosisSummary.model_validate(
            {
                "variant": trace.variant,
                "backend": "SCRIPTED_REPLAY",
                "trace_status": trace.status,
                "decision": trace.final_rca.decision,
                "root_service": trace.final_rca.root_service,
                "fault_mechanism": trace.final_rca.fault_mechanism,
                "supporting_evidence_count": len(trace.final_rca.supporting_evidence),
            }
        ).model_dump(mode="json"),
        "remediation": RemediationSummary.model_validate(
            {
                "backend": "REPLAY",
                "selected_action": planned.action.action_type,
                "policy_decision": remediation.policy_outcome,
                "approval_mode": remediation.approval_mode,
                "forward_mutation_count": remediation.forward_mutation_count,
                "verification_result": remediation.verification_outcome,
                "rollback_count": (
                    0
                    if remediation.rollback_outcome is RollbackOutcome.NOT_REQUIRED
                    else 1
                ),
                "terminal_status": remediation.terminal_outcome,
            }
        ).model_dump(mode="json"),
        "usage": UsageSummary(
            model_calls=budget.charged_model_calls,
            tool_calls=budget.charged_tool_calls,
            total_tokens=budget.cumulative_tokens,
        ).model_dump(mode="json"),
        "execution_boundary": ExecutionBoundary(
            provider_called=False,
            docker_called=False,
            live_execution=remediation.live_mutation,
            evaluator_truth_read=False,
            phase4_entered=remediation.phase4_entered,
            phase5_entered=False,
        ).model_dump(mode="json"),
        "phase3_report_semantic_sha256": remediation.semantic_sha256,
    }
    return AgentMainlineReport.model_validate(
        {
            **payload,
            "semantic_sha256": semantic_sha256(payload),
        }
    )
