from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta, timezone

import pytest
from pydantic import TypeAdapter, ValidationError

from ecomsre.phase1.contracts import (
    ChangesAction,
    Evidence,
    EvidenceAttribute,
    EvidenceSource,
    Incident,
    LogsAction,
    MetricsAction,
    RCADecision,
    RCAResult,
    ReadOnlyToolName,
    RecommendedNextAction,
    Severity,
    ToolCallRecord,
    TracesAction,
)
from ecomsre.phase2.contracts import (
    AdmittedInvestigationGraph,
    AdmittedRefinementFragment,
    AdditionalInvestigationRequest,
    BudgetLease,
    BudgetLeaseStatus,
    BudgetSnapshot,
    BudgetOwnerRole,
    CommanderRequest,
    ConditionalRefinementBundle,
    ConditionalRefinementBundleStatus,
    FindingHypothesis,
    FirstJudgeAction,
    HypothesisEvidenceGroup,
    Identifier,
    InvestigationNode,
    InvestigationPlan,
    InitialDagAdmission,
    InitialNodeCapacityBinding,
    JudgeFinalResult,
    JudgeRequest,
    MissingEvidenceItem,
    ModelAllowedActions,
    ModelOperation,
    Phase2FailureCode,
    Phase2Variant,
    ResolvedEvidenceView,
    RunId,
    Sha256,
    SourceCapability,
    SpecialistFinding,
    SpecialistAuthorizationStatus,
    SpecialistExecutionAuthorization,
    SpecialistModelRequest,
    SpecialistRole,
    SpecialistTask,
    SpecialistToolDispatchResult,
    SpecialistToolOutcomeKind,
    SpecialistToolOutcomeReceipt,
    canonical_tool_call_record_sha256,
)


START = datetime(2026, 8, 1, 1, 0, tzinfo=UTC)
END = START + timedelta(minutes=5)
RUN_ID = "a" * 32
OTHER_RUN_ID = "b" * 32
INCIDENT_ID = "inc-001"
METRICS_REF = f"evidence://{RUN_ID}/metrics/0001"
MODEL_SNAPSHOT = "gpt-5.4-mini-2026-03-17"
TOKEN_POLICY_CORE_SHA256 = "c" * 64


SOURCE_BINDINGS = {
    EvidenceSource.METRICS: (
        SpecialistRole.METRICS_AGENT,
        ReadOnlyToolName.QUERY_METRICS,
        "metrics",
        MetricsAction,
    ),
    EvidenceSource.LOGS: (
        SpecialistRole.LOGS_AGENT,
        ReadOnlyToolName.SEARCH_LOGS,
        "logs",
        LogsAction,
    ),
    EvidenceSource.TRACES: (
        SpecialistRole.TRACE_AGENT,
        ReadOnlyToolName.SEARCH_TRACES,
        "traces",
        TracesAction,
    ),
    EvidenceSource.CHANGES: (
        SpecialistRole.CHANGE_AGENT,
        ReadOnlyToolName.LIST_CHANGES,
        "changes",
        ChangesAction,
    ),
}


def incident() -> Incident:
    return Incident(
        schema_version="phase1.incident.v1",
        incident_id=INCIDENT_ID,
        alert_source_service="frontend",
        summary="Checkout latency exceeds the SLO.",
        started_at=START,
        ended_at=END,
        affected_sli="checkout p95 latency",
        severity=Severity.SEV2,
    )


def query_for(source: EvidenceSource):
    _, _, action_type, query_type = SOURCE_BINDINGS[source]
    return query_type(
        action_type=action_type,
        started_at=START,
        ended_at=END,
        service="checkoutservice",
    )


def node(
    node_id: str = "node-metrics-001",
    *,
    source: EvidenceSource = EvidenceSource.METRICS,
    **overrides: object,
) -> InvestigationNode:
    role, tool_name, _, _ = SOURCE_BINDINGS[source]
    payload: dict[str, object] = {
        "schema_version": "phase2.investigation-node.v1",
        "node_id": node_id,
        "source": source,
        "specialist_role": role,
        "tool_name": tool_name,
        "query": query_for(source),
        "depends_on": (),
        "objective": "Determine whether checkout latency increased.",
        "query_started_at": START,
        "query_ended_at": END,
        "priority": 1,
    }
    payload.update(overrides)
    return InvestigationNode.model_validate(payload)


def budget_snapshot(**overrides: object) -> BudgetSnapshot:
    payload: dict[str, object] = {
        "schema_version": "phase2.budget-snapshot.v1",
        "snapshot_id": "budget-snapshot-001",
        "run_id": RUN_ID,
        "variant": Phase2Variant.DYNAMIC_MULTI_AGENT,
        "case_id": "case-001",
        "max_model_calls": 8,
        "max_tool_calls": 8,
        "max_total_tokens": 32_000,
        "charged_model_calls": 1,
        "charged_tool_calls": 1,
        "cumulative_tokens": 800,
        "reserved_model_calls": 1,
        "reserved_tool_calls": 0,
        "reserved_tokens": 1_200,
        "remaining_model_calls": 6,
        "remaining_tool_calls": 7,
        "remaining_tokens": 30_000,
        "monotonic_elapsed_seconds": 0.25,
        "sequence": 2,
        "active_capacity_slot_ids": (),
        "active_specialist_authorization_ids": ("tool-auth-1",),
        "active_lease_ids": (),
    }
    payload.update(overrides)
    return BudgetSnapshot.model_validate(payload)


def plan(*nodes: InvestigationNode, **overrides: object) -> InvestigationPlan:
    selected_nodes = nodes or (node(),)
    payload: dict[str, object] = {
        "schema_version": "phase2.investigation-plan.v1",
        "run_id": RUN_ID,
        "incident_id": INCIDENT_ID,
        "plan_id": "plan-001",
        "nodes": selected_nodes,
        "planning_rationale": "Metrics can establish whether the alert is real.",
        "budget_snapshot_id": "budget-snapshot-001",
    }
    payload.update(overrides)
    return InvestigationPlan.model_validate(payload)


def source_capabilities() -> tuple[SourceCapability, ...]:
    return tuple(
        SourceCapability(
            source=source,
            specialist_role=SOURCE_BINDINGS[source][0],
            tool_name=SOURCE_BINDINGS[source][1],
            action_type=SOURCE_BINDINGS[source][2],
        )
        for source in (
            EvidenceSource.METRICS,
            EvidenceSource.LOGS,
            EvidenceSource.TRACES,
            EvidenceSource.CHANGES,
        )
    )


def commander_request(**overrides: object) -> CommanderRequest:
    payload: dict[str, object] = {
        "schema_version": "phase2.commander-request.v1",
        "run_id": RUN_ID,
        "incident": incident(),
        "source_capabilities": source_capabilities(),
        "allowed_started_at": START,
        "allowed_ended_at": END,
        "budget_snapshot": budget_snapshot(),
        "model_snapshot": MODEL_SNAPSHOT,
        "token_policy_core_sha256": TOKEN_POLICY_CORE_SHA256,
    }
    payload.update(overrides)
    return CommanderRequest.model_validate(payload)


def refinement_fragment(
    *nodes: InvestigationNode,
    **overrides: object,
) -> AdmittedRefinementFragment:
    payload: dict[str, object] = {
        "schema_version": "phase2.admitted-refinement-fragment.v1",
        "request_id": "refinement-request-001",
        "parent_plan_id": "plan-001",
        "nodes": nodes or (node("node-refinement-001", depends_on=("node-metrics-001",)),),
    }
    payload.update(overrides)
    return AdmittedRefinementFragment.model_validate(payload)


def graph_hash(
    initial_plan: InvestigationPlan,
    fragment: AdmittedRefinementFragment | None,
) -> str:
    all_nodes = initial_plan.nodes + (() if fragment is None else fragment.nodes)
    dependency_edges = tuple(
        (dependency, item.node_id)
        for item in all_nodes
        for dependency in item.depends_on
    )
    projection = {
        "schema_version": "phase2.admitted-investigation-graph.v1",
        "run_id": initial_plan.run_id,
        "incident_id": initial_plan.incident_id,
        "initial_plan": initial_plan.model_dump(mode="json"),
        "refinement_fragment": (
            None if fragment is None else fragment.model_dump(mode="json")
        ),
        "all_nodes": [item.model_dump(mode="json") for item in all_nodes],
        "dependency_edges": [list(edge) for edge in dependency_edges],
    }
    canonical = json.dumps(
        projection,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def admitted_graph(
    initial_plan: InvestigationPlan | None = None,
    fragment: AdmittedRefinementFragment | None = None,
    **overrides: object,
) -> AdmittedInvestigationGraph:
    selected_plan = initial_plan or plan()
    all_nodes = selected_plan.nodes + (() if fragment is None else fragment.nodes)
    dependency_edges = tuple(
        (dependency, item.node_id)
        for item in all_nodes
        for dependency in item.depends_on
    )
    payload: dict[str, object] = {
        "schema_version": "phase2.admitted-investigation-graph.v1",
        "run_id": selected_plan.run_id,
        "incident_id": selected_plan.incident_id,
        "initial_plan": selected_plan,
        "refinement_fragment": fragment,
        "all_nodes": all_nodes,
        "dependency_edges": dependency_edges,
        "graph_sha256": graph_hash(selected_plan, fragment),
    }
    payload.update(overrides)
    return AdmittedInvestigationGraph.model_validate(payload)


def valid_initial_admission_payload() -> dict[str, object]:
    graph = admitted_graph(
        plan(
            node("z-node", priority=2),
            node("a-node", priority=1),
            node("dependent", depends_on=("z-node",), priority=0),
        )
    )
    return {
        "schema_version": "phase2.initial-dag-admission.v1",
        "admitted_graph": graph,
        "node_slot_bindings": (
            {
                "node_id": "a-node",
                "specialist_capacity_slot_id": "slot-specialist-001",
            },
            {
                "node_id": "z-node",
                "specialist_capacity_slot_id": "slot-specialist-002",
            },
            {
                "node_id": "dependent",
                "specialist_capacity_slot_id": "slot-specialist-003",
            },
        ),
        "first_judge_capacity_slot_id": "slot-judge-001",
        "admission_snapshot_id": "snapshot-admitted-001",
        "admission_snapshot_sequence": 4,
    }


def test_initial_dag_admission_is_closed_frozen_and_runtime_only() -> None:
    payload = valid_initial_admission_payload()
    admission = InitialDagAdmission.model_validate(payload)
    graph = admission.admitted_graph
    assert "specialist_capacity_slot_id" not in graph.model_dump_json()
    with pytest.raises(ValidationError):
        admission.admission_snapshot_id = "snapshot-mutated"
    with pytest.raises(ValidationError):
        InitialDagAdmission.model_validate(
            {**payload, "admission_snapshot_sequence": -1}
        )
    with pytest.raises(ValidationError):
        InitialNodeCapacityBinding.model_validate(
            {
                "node_id": "a-node",
                "specialist_capacity_slot_id": "slot-specialist-001",
                "unexpected": True,
            }
        )


@pytest.mark.parametrize(
    "mutator",
    (
        lambda payload: {**payload, "node_slot_bindings": ()},
        lambda payload: {
            **payload,
            "node_slot_bindings": payload["node_slot_bindings"][:-1],
        },
        lambda payload: {
            **payload,
            "node_slot_bindings": (
                *payload["node_slot_bindings"],
                {
                    "node_id": "extra-node",
                    "specialist_capacity_slot_id": "slot-extra",
                },
            ),
        },
        lambda payload: {
            **payload,
            "node_slot_bindings": tuple(reversed(payload["node_slot_bindings"])),
        },
        lambda payload: {
            **payload,
            "node_slot_bindings": (
                payload["node_slot_bindings"][0],
                payload["node_slot_bindings"][0],
                payload["node_slot_bindings"][2],
            ),
        },
        lambda payload: {
            **payload,
            "node_slot_bindings": (
                payload["node_slot_bindings"][0],
                {
                    **payload["node_slot_bindings"][1],
                    "specialist_capacity_slot_id": "slot-specialist-001",
                },
                payload["node_slot_bindings"][2],
            ),
        },
        lambda payload: {
            **payload,
            "first_judge_capacity_slot_id": "slot-specialist-001",
        },
        lambda payload: {**payload, "admission_snapshot_sequence": True},
        lambda payload: {**payload, "admission_snapshot_id": ""},
        lambda payload: {**payload, "unexpected": "closed-model-negative"},
    ),
)
def test_initial_admission_rejects_incomplete_duplicate_or_coerced_state(
    mutator: Callable[[dict[str, object]], dict[str, object]],
) -> None:
    payload = valid_initial_admission_payload()
    with pytest.raises(ValidationError):
        InitialDagAdmission.model_validate(mutator(payload))


def task(**overrides: object) -> SpecialistTask:
    payload: dict[str, object] = {
        "schema_version": "phase2.specialist-task.v1",
        "run_id": RUN_ID,
        "incident_id": INCIDENT_ID,
        "plan_id": "plan-001",
        "node_id": "node-metrics-001",
        "source": EvidenceSource.METRICS,
        "specialist_role": SpecialistRole.METRICS_AGENT,
        "tool_name": ReadOnlyToolName.QUERY_METRICS,
        "query": query_for(EvidenceSource.METRICS),
        "objective": "Determine whether checkout latency increased.",
        "dependency_finding_ids": (),
        "dependency_evidence_refs": (),
        "tool_authorization_id": "tool-auth-1",
        "model_capacity_slot_id": "slot-specialist-1",
    }
    payload.update(overrides)
    return SpecialistTask.model_validate(payload)


def evidence(evidence_ref: str = METRICS_REF) -> Evidence:
    return Evidence(
        schema_version="phase1.evidence.v1",
        evidence_ref=evidence_ref,
        run_id=evidence_ref.split("/")[2],
        source=EvidenceSource.METRICS,
        observation_type="latency_observation",
        attributes=(EvidenceAttribute(name="p95_ms", value=920.0),),
        raw_artifact_ref="metrics.json#0",
        raw_artifact_sha256="0" * 64,
        limitations=(),
        summary="Checkout latency increased inside the incident window.",
        started_at=START,
        ended_at=END,
        service="checkoutservice",
    )


def tool_record(item: Evidence | None = None) -> ToolCallRecord:
    selected = item or evidence()
    return ToolCallRecord(
        schema_version="phase1.tool-call-record.v1",
        call_id="tool-call-001",
        run_id=RUN_ID,
        agent_id=SpecialistRole.METRICS_AGENT.value,
        incident_id=INCIDENT_ID,
        task_id="node-metrics-001",
        tool_name=ReadOnlyToolName.QUERY_METRICS,
        action=query_for(EvidenceSource.METRICS),
        evidence=(selected,),
        evidence_refs=(selected.evidence_ref,),
        started_at=START,
        ended_at=END,
        monotonic_duration_seconds=0.1,
        budget_consumed=True,
        dispatched=True,
        evidence_quarantined=False,
        usable=True,
        status="OK",
        error_code=None,
    )


def specialist_authorization_payload(
    status: SpecialistAuthorizationStatus = SpecialistAuthorizationStatus.TOOL_CHARGED,
    **overrides: object,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": "phase2.specialist-execution-authorization.v2",
        "authorization_id": "tool-auth-1",
        "capacity_slot_id": "slot-specialist-1",
        "run_id": RUN_ID,
        "variant": Phase2Variant.DYNAMIC_MULTI_AGENT,
        "case_id": "case-001",
        "creating_snapshot_sequence": 2,
        "owner_role": BudgetOwnerRole.METRICS_AGENT,
        "owner_node_id": "node-metrics-001",
        "source": EvidenceSource.METRICS,
        "tool_name": ReadOnlyToolName.QUERY_METRICS,
        "permitted_operation": ModelOperation.SPECIALIST_MODEL,
        "allowed_actions": ModelAllowedActions.FINDING_ONLY,
        "reserved_model_calls": 1,
        "reserved_tool_calls": 1,
        "minimum_token_floor": 1_200,
        "issued_at": START,
        "expires_at": END,
        "status": status,
        "actual_tool_calls": 1,
        "model_lease_id": None,
        "dispatch_claim_snapshot_sequence": 3,
        "tool_charged_snapshot_sequence": 4,
        "tool_call_record_sha256": canonical_tool_call_record_sha256(
            tool_record()
        ),
    }
    payload.update(overrides)
    return payload


def success_outcome_receipt() -> SpecialistToolOutcomeReceipt:
    authorization = SpecialistExecutionAuthorization.model_validate(
        specialist_authorization_payload()
    )
    outcome_snapshot = budget_snapshot(sequence=4)
    return SpecialistToolOutcomeReceipt(
        schema_version="phase2.specialist-tool-outcome-receipt.v1",
        outcome_kind=SpecialistToolOutcomeKind.SUCCESS,
        authorization_id=authorization.authorization_id,
        dispatch_claim_snapshot_sequence=(
            authorization.dispatch_claim_snapshot_sequence
        ),
        post_outcome_authorization=authorization,
        outcome_snapshot=outcome_snapshot,
        tool_call_record_sha256=authorization.tool_call_record_sha256,
        failure_kind=None,
        failure_code=None,
    )


def successful_dispatch_result() -> SpecialistToolDispatchResult:
    receipt = success_outcome_receipt()
    return SpecialistToolDispatchResult(
        schema_version="phase2.specialist-tool-dispatch-result.v1",
        tool_call_record=tool_record(),
        specialist_authorization=receipt.post_outcome_authorization,
        budget_snapshot=receipt.outcome_snapshot,
        outcome_receipt=receipt,
    )


def resolved_view(*items: Evidence) -> ResolvedEvidenceView:
    return ResolvedEvidenceView(
        schema_version="phase2.resolved-evidence-view.v1",
        run_id=RUN_ID,
        evidence=items,
    )


def finding(**overrides: object) -> SpecialistFinding:
    payload: dict[str, object] = {
        "schema_version": "phase2.specialist-finding.v1",
        "finding_id": "finding-001",
        "run_id": RUN_ID,
        "incident_id": INCIDENT_ID,
        "plan_id": "plan-001",
        "node_id": "node-metrics-001",
        "source": EvidenceSource.METRICS,
        "specialist_role": SpecialistRole.METRICS_AGENT,
        "evidence_refs": (METRICS_REF,),
        "hypotheses": (
            FindingHypothesis(
                schema_version="phase2.finding-hypothesis.v1",
                hypothesis_id="hypothesis-001",
                root_service="checkoutservice",
                fault_mechanism=None,
                claim="Checkout latency is elevated.",
            ),
        ),
        "supporting_evidence_refs": (
            HypothesisEvidenceGroup(
                schema_version="phase2.hypothesis-evidence-group.v1",
                hypothesis_id="hypothesis-001",
                evidence_refs=(METRICS_REF,),
            ),
        ),
        "contradicting_evidence_refs": (),
        "missing_evidence": (
            MissingEvidenceItem(
                schema_version="phase2.missing-evidence-item.v1",
                question="Do traces show the same latency increase?",
                desired_source=EvidenceSource.TRACES,
            ),
        ),
        "confidence": 0.6,
        "finding_rationale": "Metrics support an elevated-latency hypothesis.",
    }
    payload.update(overrides)
    return SpecialistFinding.model_validate(payload)


def judge_request(**overrides: object) -> JudgeRequest:
    payload: dict[str, object] = {
        "schema_version": "phase2.judge-request.v1",
        "judge_request_id": "judge-request-001",
        "run_id": RUN_ID,
        "incident": incident(),
        "admitted_graph": admitted_graph(),
        "finding_ids": ("finding-001",),
        "findings": (finding(),),
        "available_evidence_refs": (METRICS_REF,),
        "resolved_evidence_view": resolved_view(evidence()),
        "budget_snapshot": budget_snapshot(),
        "refinement_round": 0,
        "allowed_actions": ModelAllowedActions.FINAL_ONLY,
        "conditional_refinement_bundle_id": None,
    }
    payload.update(overrides)
    return JudgeRequest.model_validate(payload)


def abstain_result() -> RCAResult:
    return RCAResult(
        schema_version="phase1.rca-result.v1",
        decision=RCADecision.ABSTAIN,
        root_service=None,
        fault_mechanism=None,
        causal_chain=(),
        affected_sli="checkout p95 latency",
        supporting_evidence=(METRICS_REF,),
        contradicting_evidence=(),
        missing_evidence=(),
        confidence=0.25,
        decision_rationale="No confirmed incident is established by the evidence.",
        recommended_next_action=RecommendedNextAction.CONTINUE_MONITORING_AFFECTED_SLI,
    )


def additional_request(**overrides: object) -> AdditionalInvestigationRequest:
    payload: dict[str, object] = {
        "schema_version": "phase2.additional-investigation-request.v1",
        "action_type": "ADDITIONAL_INVESTIGATION",
        "run_id": RUN_ID,
        "incident_id": INCIDENT_ID,
        "parent_plan_id": "plan-001",
        "request_id": "refinement-001",
        "nodes": (node("node-refinement-001"),),
        "target_hypothesis_ids": ("hypothesis-001",),
        "reason": "Trace evidence is missing for the current hypothesis.",
        "conditional_refinement_bundle_id": "bundle-001",
        "fallback_rca_result": abstain_result(),
    }
    payload.update(overrides)
    return AdditionalInvestigationRequest.model_validate(payload)


def test_a1_enums_are_exact_and_closed() -> None:
    assert tuple(ModelOperation) == (
        ModelOperation.SINGLE_AGENT_MODEL,
        ModelOperation.COMMANDER_MODEL,
        ModelOperation.SPECIALIST_MODEL,
        ModelOperation.FIRST_JUDGE_MODEL,
        ModelOperation.FINAL_JUDGE_MODEL,
    )
    assert tuple(ModelAllowedActions) == (
        ModelAllowedActions.PHASE1_ACTION_CATALOG,
        ModelAllowedActions.PLAN_ONLY,
        ModelAllowedActions.FINDING_ONLY,
        ModelAllowedActions.FINAL_ONLY,
        ModelAllowedActions.FINAL_OR_REFINEMENT,
    )
    assert tuple(item.value for item in Phase2FailureCode) == (
        "TOKEN_POLICY_MISSING",
        "TOKEN_POLICY_CORE_HASH_MISMATCH",
        "TOKEN_GOLDEN_MANIFEST_MISMATCH",
        "TOKENIZER_VERSION_MISMATCH",
        "TOKENIZER_ASSET_MISSING",
        "TOKENIZER_ASSET_SIZE_MISMATCH",
        "TOKENIZER_ASSET_HASH_MISMATCH",
        "TOKEN_MODEL_MAPPING_MISMATCH",
        "TOKEN_CANONICALIZATION_FAILED",
        "TOKEN_INPUT_TOO_LARGE",
        "BUDGET_MINIMUM_FLOOR_UNAVAILABLE",
        "BUDGET_SLOT_STALE",
        "BUDGET_SLOT_OWNER_MISMATCH",
        "BUDGET_SLOT_ALREADY_CONSUMED",
        "BUDGET_EXACT_EXPANSION_FAILED",
        "BUDGET_CAS_CONFLICT",
        "PROVIDER_USAGE_MISSING",
        "PROVIDER_USAGE_INCONSISTENT",
        "PROVIDER_USAGE_EXCEEDS_LEASE",
        "PROVIDER_PARAMETER_MISMATCH",
        "COMPARISON_ADAPTER_BYPASS",
        "BUDGET_CUMULATIVE_OVERFLOW",
        "TOOL_DISPATCH_FAILED",
    )


@pytest.mark.parametrize(
    ("status", "updates"),
    (
        (
            SpecialistAuthorizationStatus.TOOL_AUTHORIZED,
            {
                "actual_tool_calls": 0,
                "dispatch_claim_snapshot_sequence": None,
                "tool_charged_snapshot_sequence": None,
                "tool_call_record_sha256": None,
            },
        ),
        (
            SpecialistAuthorizationStatus.TOOL_DISPATCHING,
            {
                "actual_tool_calls": 0,
                "tool_charged_snapshot_sequence": None,
                "tool_call_record_sha256": None,
            },
        ),
        (SpecialistAuthorizationStatus.TOOL_CHARGED, {}),
        (
            SpecialistAuthorizationStatus.MODEL_LEASED,
            {"model_lease_id": "lease-specialist-1"},
        ),
        (
            SpecialistAuthorizationStatus.COMPLETED,
            {"model_lease_id": "lease-specialist-1"},
        ),
        (
            SpecialistAuthorizationStatus.RELEASED,
            {
                "actual_tool_calls": 0,
                "dispatch_claim_snapshot_sequence": None,
                "tool_charged_snapshot_sequence": None,
                "tool_call_record_sha256": None,
            },
        ),
        (
            SpecialistAuthorizationStatus.FAILED,
            {
                "tool_charged_snapshot_sequence": None,
                "tool_call_record_sha256": None,
            },
        ),
        (SpecialistAuthorizationStatus.FAILED, {}),
    ),
)
def test_specialist_authorization_v2_accepts_only_exact_status_provenance(
    status: SpecialistAuthorizationStatus,
    updates: dict[str, object],
) -> None:
    payload = specialist_authorization_payload(status, **updates)
    authorization = SpecialistExecutionAuthorization.model_validate(payload)
    assert authorization.schema_version == (
        "phase2.specialist-execution-authorization.v2"
    )
    assert authorization.authorization_id != authorization.capacity_slot_id


@pytest.mark.parametrize(
    "updates",
    (
        {"schema_version": "phase2.specialist-execution-authorization.v1"},
        {"authorization_id": "slot-specialist-1"},
        {"dispatch_claim_snapshot_sequence": None},
        {"tool_charged_snapshot_sequence": None},
        {"tool_call_record_sha256": None},
        {"dispatch_claim_snapshot_sequence": 2},
        {"tool_charged_snapshot_sequence": 3},
        {"actual_tool_calls": 0},
        {"model_lease_id": "forbidden-before-model-lease"},
    ),
)
def test_tool_charged_authorization_rejects_partial_or_unordered_provenance(
    updates: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        SpecialistExecutionAuthorization.model_validate(
            specialist_authorization_payload(**updates)
        )


def test_dispatching_and_failed_authorizations_reject_forbidden_partial_success() -> None:
    for status, updates in (
        (
            SpecialistAuthorizationStatus.TOOL_DISPATCHING,
            {"actual_tool_calls": 0},
        ),
        (
            SpecialistAuthorizationStatus.FAILED,
            {"tool_charged_snapshot_sequence": None},
        ),
    ):
        with pytest.raises(ValidationError):
            SpecialistExecutionAuthorization.model_validate(
                specialist_authorization_payload(status, **updates)
            )


def test_success_receipt_and_dispatch_result_freeze_exact_charge_outcome() -> None:
    receipt = success_outcome_receipt()
    result = successful_dispatch_result()
    assert receipt.outcome_kind is SpecialistToolOutcomeKind.SUCCESS
    assert receipt.outcome_snapshot.sequence == (
        receipt.post_outcome_authorization.tool_charged_snapshot_sequence
    )
    assert result.outcome_receipt == receipt
    assert result.tool_call_record == tool_record()
    assert result.specialist_authorization.tool_call_record_sha256 == (
        canonical_tool_call_record_sha256(result.tool_call_record)
    )
    with pytest.raises(ValidationError):
        SpecialistToolDispatchResult.model_validate(
            {
                **result.model_dump(mode="python"),
                "budget_snapshot": budget_snapshot(sequence=5),
            }
        )


def test_failure_receipt_requires_exact_failed_attempt_shape() -> None:
    failed_authorization = SpecialistExecutionAuthorization.model_validate(
        specialist_authorization_payload(
            SpecialistAuthorizationStatus.FAILED,
            tool_charged_snapshot_sequence=None,
            tool_call_record_sha256=None,
        )
    )
    failed_snapshot = budget_snapshot(
        sequence=4,
        reserved_model_calls=0,
        reserved_tokens=0,
        remaining_model_calls=7,
        remaining_tokens=31_200,
        active_specialist_authorization_ids=(),
    )
    receipt = SpecialistToolOutcomeReceipt(
        schema_version="phase2.specialist-tool-outcome-receipt.v1",
        outcome_kind=SpecialistToolOutcomeKind.FAILURE,
        authorization_id=failed_authorization.authorization_id,
        dispatch_claim_snapshot_sequence=(
            failed_authorization.dispatch_claim_snapshot_sequence
        ),
        post_outcome_authorization=failed_authorization,
        outcome_snapshot=failed_snapshot,
        tool_call_record_sha256=None,
        failure_kind="EXECUTOR_FAILURE",
        failure_code=Phase2FailureCode.TOOL_DISPATCH_FAILED,
    )
    assert receipt.failure_kind == "EXECUTOR_FAILURE"
    assert receipt.outcome_snapshot.active_specialist_authorization_ids == ()
    with pytest.raises(ValidationError):
        SpecialistToolOutcomeReceipt.model_validate(
            {
                **receipt.model_dump(mode="python"),
                "tool_call_record_sha256": "d" * 64,
            }
        )


def test_identifier_run_id_and_sha256_reject_bytes() -> None:
    with pytest.raises(ValidationError):
        TypeAdapter(Identifier).validate_python(b"plan-001")
    with pytest.raises(ValidationError):
        TypeAdapter(RunId).validate_python(b"a" * 32)
    with pytest.raises(ValidationError):
        TypeAdapter(Sha256).validate_python(b"c" * 64)


def test_commander_request_is_the_only_typed_planning_boundary() -> None:
    request = commander_request()
    assert tuple(item.source for item in request.source_capabilities) == (
        EvidenceSource.METRICS,
        EvidenceSource.LOGS,
        EvidenceSource.TRACES,
        EvidenceSource.CHANGES,
    )
    assert request.model_snapshot == MODEL_SNAPSHOT
    assert len(request.token_policy_core_sha256) == 64
    assert not {
        "fixture_path",
        "scenario_name",
        "expected_answer",
        "evidence",
        "findings",
        "ground_truth",
    } & request.model_fields_set

    with pytest.raises(ValidationError):
        CommanderRequest.model_validate(
            {**request.model_dump(mode="python"), "scenario_name": "hidden"}
        )


def test_commander_rejects_capability_order_mapping_and_window_drift() -> None:
    request = commander_request()
    reversed_capabilities = tuple(reversed(request.source_capabilities))
    with pytest.raises(ValidationError, match="frozen source capability"):
        commander_request(source_capabilities=reversed_capabilities)
    with pytest.raises(ValidationError, match="source capability mapping"):
        SourceCapability.model_validate(
            {
                **request.source_capabilities[0].model_dump(),
                "specialist_role": SpecialistRole.LOGS_AGENT,
            }
        )
    with pytest.raises(ValidationError, match="frozen source capability"):
        commander_request(
            source_capabilities=(
                request.source_capabilities[0],
                request.source_capabilities[0],
                request.source_capabilities[2],
                request.source_capabilities[3],
            )
        )
    non_utc = timezone(timedelta(hours=8))
    with pytest.raises(ValidationError, match="UTC"):
        commander_request(allowed_started_at=START.astimezone(non_utc))
    with pytest.raises(ValidationError, match="allowed window"):
        commander_request(allowed_started_at=END, allowed_ended_at=START)
    with pytest.raises(ValidationError):
        commander_request(model_snapshot="gpt-5.4-mini")
    with pytest.raises(ValidationError):
        commander_request(token_policy_core_sha256="C" * 64)


def test_plan_is_closed_immutable_and_dag_consistent() -> None:
    validated = plan()
    assert validated.nodes[0].specialist_role is SpecialistRole.METRICS_AGENT

    with pytest.raises(ValidationError):
        InvestigationPlan.model_validate({**validated.model_dump(), "unexpected": True})
    with pytest.raises(ValidationError):
        validated.plan_id = "mutated"

    duplicate = node("duplicate")
    with pytest.raises(ValidationError, match="duplicate node_id"):
        plan(duplicate, duplicate)
    with pytest.raises(ValidationError, match="unknown dependency"):
        plan(node(depends_on=("missing-node",)))
    with pytest.raises(ValidationError, match="dependency cycle"):
        plan(
            node("cyclic-left", depends_on=("cyclic-right",)),
            node("cyclic-right", depends_on=("cyclic-left",)),
        )


def test_plan_round_trip_preserves_declared_contract_only() -> None:
    original = plan()
    assert InvestigationPlan.model_validate_json(original.model_dump_json()) == original


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"specialist_role": SpecialistRole.LOGS_AGENT}, "source-role-tool-query"),
        ({"tool_name": ReadOnlyToolName.SEARCH_LOGS}, "source-role-tool-query"),
        ({"query_started_at": START + timedelta(seconds=1)}, "query window"),
        ({"depends_on": ("node-metrics-001",)}, "self-dependency"),
    ],
)
def test_node_rejects_binding_and_window_mismatch(
    overrides: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(ValidationError, match=message):
        node(**overrides)


def test_node_rejects_wrong_query_non_utc_and_executable_text() -> None:
    with pytest.raises(ValidationError, match="source-role-tool-query"):
        node(query=query_for(EvidenceSource.LOGS))
    non_utc = timezone(timedelta(hours=8))
    with pytest.raises(ValidationError, match="UTC"):
        node(query_started_at=START.astimezone(non_utc))
    for objective in (
        "docker restart checkoutservice now",
        "python diagnose.py",
        "make deploy",
        "restart checkoutservice now",
    ):
        with pytest.raises(ValidationError, match="read-only text"):
            node(objective=objective)


def test_node_schema_publishes_typed_datetimes() -> None:
    properties = InvestigationNode.model_json_schema()["properties"]
    assert properties["query_started_at"]["format"] == "date-time"
    assert properties["query_ended_at"]["format"] == "date-time"


def test_admitted_graph_has_exact_canonical_node_and_edge_projections() -> None:
    initial = plan(
        node("initial-001"),
        node("initial-002", depends_on=("initial-001",)),
    )
    fragment = refinement_fragment(
        node("refinement-001", depends_on=("initial-002",)),
        node("refinement-002", depends_on=("refinement-001",)),
    )
    graph = admitted_graph(initial, fragment)
    assert graph.all_nodes == initial.nodes + fragment.nodes
    assert graph.dependency_edges == (
        ("initial-001", "initial-002"),
        ("initial-002", "refinement-001"),
        ("refinement-001", "refinement-002"),
    )
    assert len(graph.graph_sha256) == 64

    with pytest.raises(ValidationError, match="all_nodes projection"):
        AdmittedInvestigationGraph.model_validate(
            {**graph.model_dump(mode="python"), "all_nodes": graph.all_nodes[:-1]}
        )
    with pytest.raises(ValidationError, match="dependency_edges projection"):
        AdmittedInvestigationGraph.model_validate(
            {**graph.model_dump(mode="python"), "dependency_edges": ()}
        )
    with pytest.raises(ValidationError, match="graph hash"):
        AdmittedInvestigationGraph.model_validate(
            {**graph.model_dump(mode="python"), "graph_sha256": "0" * 64}
        )


def test_admitted_graph_known_canonical_sha256() -> None:
    assert admitted_graph().graph_sha256 == (
        "25020528436abd7815f2a9a770551f8d9f096e2706c78dab4ee3a5f0579717f5"
    )


def test_admitted_graph_rejects_duplicate_ids_invalid_refinement_and_overflow() -> None:
    initial = plan(node("initial-001"), node("initial-002"), node("initial-003"))
    with pytest.raises(ValidationError, match="duplicate node_id"):
        admitted_graph(
            initial,
            refinement_fragment(node("initial-001")),
        )
    with pytest.raises(ValidationError, match="parent plan"):
        admitted_graph(
            initial,
            refinement_fragment(
                node("refinement-001", depends_on=("initial-001",)),
                parent_plan_id="other-plan",
            ),
        )
    with pytest.raises(ValidationError, match="later refinement node"):
        admitted_graph(
            initial,
            refinement_fragment(
                node("refinement-forward", depends_on=("refinement-later",)),
                node("refinement-later"),
            ),
        )
    invalid_fragment = AdmittedRefinementFragment.model_construct(
        schema_version="phase2.admitted-refinement-fragment.v1",
        request_id="refinement-request-overflow",
        parent_plan_id=initial.plan_id,
        nodes=(
            node("refinement-001"),
            node("refinement-002"),
            node("refinement-003"),
        ),
    )
    combined_nodes = initial.nodes + invalid_fragment.nodes
    with pytest.raises(
        ValidationError,
        match="combined graph cannot exceed five nodes",
    ):
        AdmittedInvestigationGraph.model_validate(
            {
                "schema_version": "phase2.admitted-investigation-graph.v1",
                "run_id": RUN_ID,
                "incident_id": INCIDENT_ID,
                "initial_plan": initial,
                "refinement_fragment": invalid_fragment,
                "all_nodes": combined_nodes,
                "dependency_edges": (),
                "graph_sha256": "0" * 64,
            }
        )


def test_budget_snapshot_requires_exact_accounting_and_finite_time() -> None:
    assert budget_snapshot().remaining_tokens == 30_000
    with pytest.raises(ValidationError, match="model call accounting"):
        budget_snapshot(remaining_model_calls=7)
    with pytest.raises(ValidationError):
        budget_snapshot(monotonic_elapsed_seconds=float("nan"))
    with pytest.raises(ValidationError, match="duplicates"):
        budget_snapshot(active_lease_ids=("lease-1", "lease-1"))


@pytest.mark.parametrize(
    "overrides",
    (
        {"max_model_calls": 9, "remaining_model_calls": 7},
        {"max_tool_calls": 9, "remaining_tool_calls": 8},
        {"max_total_tokens": 32_001, "remaining_tokens": 30_001},
        {"max_model_calls": 8.0},
        {"max_model_calls": "8"},
        {"max_tool_calls": 8.0},
        {"max_tool_calls": "8"},
        {"max_total_tokens": 32_000.0},
        {"max_total_tokens": "32000"},
    ),
)
def test_budget_snapshot_rejects_balanced_outer_cap_drift(
    overrides: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        budget_snapshot(**overrides)


def test_budget_lease_is_model_only_and_charge_consistent() -> None:
    lease = BudgetLease(
        schema_version="phase2.budget-lease.v1",
        lease_id="lease-commander-001",
        run_id=RUN_ID,
        variant=Phase2Variant.DYNAMIC_MULTI_AGENT,
        case_id="case-001",
        snapshot_sequence=2,
        owner_role=BudgetOwnerRole.INCIDENT_COMMANDER,
        owner_node_id=None,
        permitted_operation=ModelOperation.COMMANDER_MODEL,
        allowed_actions=ModelAllowedActions.PLAN_ONLY,
        source_record_id="slot-commander-001",
        reserved_model_calls=1,
        reserved_tool_calls=0,
        reserved_tokens=1_600,
        exact_input_tokens=500,
        minimum_completion_tokens=100,
        max_completion_tokens=1_100,
        issued_at=START,
        expires_at=END,
        status=BudgetLeaseStatus.RESERVED,
        actual_model_calls=0,
        actual_tool_calls=0,
        actual_tokens=0,
    )
    assert lease.reserved_tool_calls == 0
    assert lease.reserved_tokens == (
        lease.exact_input_tokens + lease.max_completion_tokens
    )
    with pytest.raises(ValidationError, match="model-only"):
        BudgetLease.model_validate({**lease.model_dump(), "reserved_tool_calls": 1})
    with pytest.raises(ValidationError, match="reserved lease"):
        BudgetLease.model_validate({**lease.model_dump(), "actual_tokens": 1})
    with pytest.raises(ValidationError):
        payload = lease.model_dump()
        del payload["source_record_id"]
        BudgetLease.model_validate(payload)


@pytest.mark.parametrize(
    ("owner_role", "owner_node_id"),
    (
        (BudgetOwnerRole.RCA_JUDGE, None),
        (BudgetOwnerRole.INCIDENT_COMMANDER, "forbidden-owner-node"),
    ),
)
def test_single_agent_budget_lease_requires_commander_without_node(
    owner_role: BudgetOwnerRole,
    owner_node_id: str | None,
) -> None:
    with pytest.raises(ValidationError, match="owner role"):
        BudgetLease(
            schema_version="phase2.budget-lease.v1",
            lease_id="lease-single-agent-001",
            run_id=RUN_ID,
            variant=Phase2Variant.SINGLE_AGENT,
            case_id="case-001",
            snapshot_sequence=2,
            owner_role=owner_role,
            owner_node_id=owner_node_id,
            permitted_operation=ModelOperation.SINGLE_AGENT_MODEL,
            allowed_actions=ModelAllowedActions.PHASE1_ACTION_CATALOG,
            source_record_id="slot-single-agent-001",
            reserved_model_calls=1,
            reserved_tool_calls=0,
            reserved_tokens=1_600,
            exact_input_tokens=500,
            minimum_completion_tokens=100,
            max_completion_tokens=1_100,
            issued_at=START,
            expires_at=END,
            status=BudgetLeaseStatus.RESERVED,
            actual_model_calls=0,
            actual_tool_calls=0,
            actual_tokens=0,
        )


def test_conditional_refinement_bundle_has_distinct_opaque_ids() -> None:
    bundle = ConditionalRefinementBundle(
        schema_version="phase2.conditional-refinement-bundle.v1",
        bundle_id="bundle-001",
        run_id=RUN_ID,
        case_id="case-001",
        variant=Phase2Variant.DYNAMIC_MULTI_AGENT,
        first_judge_capacity_slot_id="slot-first-judge-001",
        specialist_capacity_slot_ids=(
            "slot-refinement-001",
            "slot-refinement-002",
        ),
        final_judge_capacity_slot_id="slot-final-judge-001",
        creating_snapshot_sequence=2,
        status=ConditionalRefinementBundleStatus.HELD,
    )
    assert bundle.specialist_capacity_slot_ids == (
        "slot-refinement-001",
        "slot-refinement-002",
    )
    with pytest.raises(ValidationError, match="distinct"):
        ConditionalRefinementBundle.model_validate(
            {
                **bundle.model_dump(),
                "final_judge_capacity_slot_id": bundle.first_judge_capacity_slot_id,
            }
        )
    for legacy_field in (
        "first_judge_" + "lease_id",
        "specialist_" + "lease_ids",
        "final_judge_" + "lease_id",
    ):
        with pytest.raises(ValidationError):
            ConditionalRefinementBundle.model_validate(
                {**bundle.model_dump(), legacy_field: "forged"}
            )


def test_specialist_task_uses_authorization_and_slot_not_model_lease() -> None:
    payload = task().model_dump(mode="python")
    assert payload["tool_authorization_id"] == "tool-auth-1"
    assert payload["model_capacity_slot_id"] == "slot-specialist-1"
    legacy_field = "budget_" + "lease_id"
    assert legacy_field not in payload
    with pytest.raises(ValidationError):
        SpecialistTask.model_validate({**payload, legacy_field: "forged"})


def test_specialist_model_request_is_post_tool_pre_exact_model_lease() -> None:
    item = evidence()
    request = SpecialistModelRequest(
        schema_version="phase2.specialist-model-request.v1",
        task=task(),
        tool_call_record=tool_record(item),
        new_evidence=(item,),
        dependency_finding_ids=(),
        resolved_dependency_evidence_view=resolved_view(),
        budget_snapshot=budget_snapshot(),
    )
    assert request.task.tool_authorization_id in (
        request.budget_snapshot.active_specialist_authorization_ids
    )
    assert request.task.model_capacity_slot_id not in (
        request.budget_snapshot.active_capacity_slot_ids
    )

    with pytest.raises(ValidationError, match="active specialist authorization"):
        SpecialistModelRequest.model_validate(
            {
                **request.model_dump(mode="python"),
                "budget_snapshot": budget_snapshot(
                    active_specialist_authorization_ids=()
                ),
            }
        )
    with pytest.raises(ValidationError, match="originating capacity slot"):
        SpecialistModelRequest.model_validate(
            {
                **request.model_dump(mode="python"),
                "budget_snapshot": budget_snapshot(
                    active_capacity_slot_ids=("slot-specialist-1",)
                ),
            }
        )
    with_unrelated_lease = SpecialistModelRequest.model_validate(
        {
            **request.model_dump(mode="python"),
            "budget_snapshot": budget_snapshot(
                active_lease_ids=("lease-unrelated-001",)
            ),
        }
    )
    assert with_unrelated_lease.budget_snapshot.active_lease_ids == (
        "lease-unrelated-001",
    )
    with pytest.raises(ValidationError, match="new_evidence"):
        SpecialistModelRequest.model_validate(
            {**request.model_dump(mode="python"), "new_evidence": ()}
        )


def test_runtime_dispatch_contracts_never_enter_model_visible_request_v1() -> None:
    item = evidence()
    request = SpecialistModelRequest(
        schema_version="phase2.specialist-model-request.v1",
        task=task(),
        tool_call_record=tool_record(item),
        new_evidence=(item,),
        dependency_finding_ids=(),
        resolved_dependency_evidence_view=resolved_view(),
        budget_snapshot=budget_snapshot(),
    )
    payload = request.model_dump(mode="json")
    canonical = json.dumps(
        payload,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    for forbidden in (
        "specialist_authorization",
        "dispatch_claim_snapshot_sequence",
        "tool_charged_snapshot_sequence",
        "tool_call_record_sha256",
        "outcome_receipt",
    ):
        assert forbidden not in payload
        assert f'"{forbidden}":' not in canonical


def test_specialist_request_rejects_unresolved_or_invalid_evidence() -> None:
    item = evidence()
    dependency_task = task(
        dependency_finding_ids=("finding-prerequisite",),
        dependency_evidence_refs=(METRICS_REF,),
    )
    with pytest.raises(ValidationError, match="dependency refs"):
        SpecialistModelRequest(
            schema_version="phase2.specialist-model-request.v1",
            task=dependency_task,
            tool_call_record=tool_record(item),
            new_evidence=(item,),
            dependency_finding_ids=("finding-prerequisite",),
            resolved_dependency_evidence_view=resolved_view(),
            budget_snapshot=budget_snapshot(),
        )

    invalid_record = tool_record(item).model_copy(update={"budget_consumed": False})
    with pytest.raises(ValidationError, match="consumed budget"):
        SpecialistModelRequest(
            schema_version="phase2.specialist-model-request.v1",
            task=task(),
            tool_call_record=invalid_record,
            new_evidence=(item,),
            dependency_finding_ids=(),
            resolved_dependency_evidence_view=resolved_view(),
            budget_snapshot=budget_snapshot(),
        )

    wrong_run_item = evidence(f"evidence://{OTHER_RUN_ID}/metrics/0001")
    with pytest.raises(ValidationError, match="current run"):
        resolved_view(wrong_run_item)


def test_nested_phase1_copies_are_revalidated_to_canonical_types() -> None:
    copied_query = query_for(EvidenceSource.METRICS).model_copy(
        update={"started_at": START.isoformat()}
    )
    canonical_node = node(query=copied_query)
    assert isinstance(canonical_node.query.started_at, datetime)

    copied_item = evidence().model_copy(
        update={"source": EvidenceSource.METRICS.value, "started_at": START.isoformat()}
    )
    copied_record = tool_record().model_copy(
        update={"started_at": START.isoformat(), "evidence": (copied_item,)}
    )
    specialist_request = SpecialistModelRequest(
        schema_version="phase2.specialist-model-request.v1",
        task=task(query=copied_query),
        tool_call_record=copied_record,
        new_evidence=(copied_item,),
        dependency_finding_ids=(),
        resolved_dependency_evidence_view=resolved_view(),
        budget_snapshot=budget_snapshot(),
    )
    assert isinstance(specialist_request.task.query.started_at, datetime)
    assert isinstance(specialist_request.tool_call_record.started_at, datetime)
    assert isinstance(specialist_request.new_evidence[0].started_at, datetime)

    copied_incident = incident().model_copy(
        update={"started_at": START.isoformat(), "severity": Severity.SEV2.value}
    )
    canonical_judge = judge_request(incident=copied_incident)
    assert isinstance(canonical_judge.incident.started_at, datetime)
    assert canonical_judge.incident.severity is Severity.SEV2


def test_specialist_finding_requires_scoped_refs_and_hypothesis_groups() -> None:
    validated = finding()
    assert validated.confidence == 0.6
    with pytest.raises(ValidationError, match="unknown hypothesis"):
        SpecialistFinding.model_validate(
            {
                **validated.model_dump(),
                "supporting_evidence_refs": (
                    {
                        "schema_version": "phase2.hypothesis-evidence-group.v1",
                        "hypothesis_id": "missing-hypothesis",
                        "evidence_refs": (METRICS_REF,),
                    },
                ),
            }
        )
    with pytest.raises(ValidationError, match="duplicates"):
        finding(evidence_refs=(METRICS_REF, METRICS_REF))
    with pytest.raises(ValidationError):
        finding(confidence=float("inf"))


def test_judge_request_contains_exact_bodies_and_projections() -> None:
    request = judge_request()
    assert request.finding_ids == tuple(item.finding_id for item in request.findings)
    assert request.available_evidence_refs == tuple(
        item.evidence_ref for item in request.resolved_evidence_view.evidence
    )
    assert request.admitted_graph.run_id == request.run_id

    with pytest.raises(ValidationError, match="finding_ids projection"):
        JudgeRequest.model_validate(
            {**request.model_dump(mode="python"), "finding_ids": ("finding-drift",)}
        )
    with pytest.raises(ValidationError, match="finding body"):
        JudgeRequest.model_validate(
            {
                **request.model_dump(mode="python"),
                "findings": (finding(node_id="node-unknown"),),
            }
        )
    with pytest.raises(ValidationError, match="evidence ref projection"):
        JudgeRequest.model_validate(
            {**request.model_dump(mode="python"), "available_evidence_refs": ()}
        )
    with pytest.raises(ValidationError, match="unresolved evidence"):
        JudgeRequest.model_validate(
            {
                **request.model_dump(mode="python"),
                "resolved_evidence_view": resolved_view(),
            }
        )


def test_judge_rejects_duplicate_ids_and_wrong_action_bundle_combinations() -> None:
    request = judge_request()
    duplicated = finding(finding_id="finding-duplicate")
    with pytest.raises(ValidationError, match="duplicates"):
        JudgeRequest.model_validate(
            {
                **request.model_dump(mode="python"),
                "finding_ids": ("finding-duplicate", "finding-duplicate"),
                "findings": (duplicated, duplicated),
            }
        )
    with pytest.raises(ValidationError, match="conditional refinement bundle"):
        judge_request(allowed_actions=ModelAllowedActions.FINAL_OR_REFINEMENT)
    with pytest.raises(ValidationError, match="FINAL_ONLY"):
        judge_request(conditional_refinement_bundle_id="bundle-001")
    with pytest.raises(ValidationError, match="Judge allowed action"):
        judge_request(allowed_actions=ModelAllowedActions.FINDING_ONLY)


def test_judge_rejects_a_second_refinement_round() -> None:
    initial = plan()
    refined_graph = admitted_graph(initial, refinement_fragment())
    refined_finding = finding(node_id="node-refinement-001", finding_id="finding-refined")
    with pytest.raises(ValidationError, match="second refinement"):
        judge_request(
            admitted_graph=refined_graph,
            finding_ids=("finding-refined",),
            findings=(refined_finding,),
            refinement_round=1,
            allowed_actions=ModelAllowedActions.FINAL_OR_REFINEMENT,
            conditional_refinement_bundle_id="bundle-001",
        )


def test_refinement_response_has_only_opaque_bundle_and_exact_phase1_fallback() -> None:
    request = additional_request()
    payload = request.model_dump(mode="python")
    forbidden_reserve_field = "required_final_judge_" + "reserve"
    assert forbidden_reserve_field not in payload
    assert type(request.fallback_rca_result) is RCAResult
    with pytest.raises(ValidationError):
        AdditionalInvestigationRequest.model_validate(
            {**payload, forbidden_reserve_field: {"lease_id": "forged"}}
        )

    invalid_fallback = abstain_result().model_copy(
        update={"decision": RCADecision.RCA_CONFIRMED}
    )
    with pytest.raises(ValidationError, match="fallback"):
        AdditionalInvestigationRequest.model_validate(
            {**payload, "fallback_rca_result": invalid_fallback}
        )
    invalid_enum_fallback = abstain_result().model_copy(
        update={"recommended_next_action": "restart checkoutservice now"}
    )
    with pytest.raises(ValidationError):
        additional_request(fallback_rca_result=invalid_enum_fallback)
    with pytest.raises(ValidationError, match="duplicates"):
        additional_request(target_hypothesis_ids=("hypothesis-001", "hypothesis-001"))


def test_first_judge_action_is_discriminated_and_preserves_rca_type() -> None:
    final = JudgeFinalResult(
        schema_version="phase2.judge-final-result.v1",
        action_type="FINAL_RCA",
        run_id=RUN_ID,
        incident_id=INCIDENT_ID,
        rca_result=abstain_result(),
        finding_ids_considered=("finding-001",),
        refinement_used=False,
        judge_request_id="judge-request-001",
    )
    parsed: JudgeFinalResult | AdditionalInvestigationRequest = TypeAdapter(
        FirstJudgeAction
    ).validate_python(final.model_dump())
    assert isinstance(parsed, JudgeFinalResult)
    assert type(parsed.rca_result) is RCAResult


def test_final_result_revalidates_exact_phase1_rca_semantics() -> None:
    invalid_result = abstain_result().model_copy(
        update={"recommended_next_action": "restart checkoutservice now"}
    )
    with pytest.raises(ValidationError):
        JudgeFinalResult(
            schema_version="phase2.judge-final-result.v1",
            action_type="FINAL_RCA",
            run_id=RUN_ID,
            incident_id=INCIDENT_ID,
            rca_result=invalid_result,
            finding_ids_considered=("finding-001",),
            refinement_used=False,
            judge_request_id="judge-request-001",
        )
