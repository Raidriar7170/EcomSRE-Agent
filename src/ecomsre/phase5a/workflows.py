"""Offline Single, Fixed, and Dynamic capability-parity workflows v2."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from datetime import timedelta
from enum import Enum
from pathlib import Path
from typing import Literal

from pydantic import Field, StrictInt, model_validator

from ecomsre.backends.replay import ReplayCase, ReplayObservabilityBackend
from ecomsre.phase1.budgets import RunBudget
from ecomsre.phase1.contracts import (
    BudgetLimits,
    ChangesAction,
    EvidenceSource,
    LogsAction,
    MetricsAction,
    ReadOnlyToolName,
    StableErrorCode,
    ToolAction,
    ToolCallRecord,
    TracesAction,
)
from ecomsre.phase1.evidence import EvidenceStore
from ecomsre.phase1.runtime_config import load_agent_settings
from ecomsre.phase2.budgets import BudgetLedger
from ecomsre.phase2.contracts import (
    AdmittedInvestigationGraph,
    BudgetOwnerRole,
    BudgetSnapshot,
    CapacitySlotRequest,
    FixedInvestigationPlan,
    InvestigationNode,
    InvestigationPlan,
    ModelAllowedActions,
    ModelOperation,
    Phase2Variant,
    SPECIALIST_TOOL_BINDINGS,
    SpecialistRole,
    SpecialistTask,
    build_fixed_admitted_graph,
    build_initial_admitted_graph,
)
from ecomsre.phase2.tool_isolation import SpecialistToolRegistry
from ecomsre.phase5a.contracts import (
    DiagnosisResultV2,
    ObservationsStatusV2,
    Phase5AModel,
    SpecialistFindingV2,
)
from ecomsre.phase5a.judge import judge_diagnosis_v2
from ecomsre.phase5a.planner import (
    STAGE1_SOURCES,
    select_stage2_sources,
    select_targeted_refinement,
)
from ecomsre.phase5a.single import (
    SINGLE_V2_SOURCE_ORDER,
    finalize_single_diagnosis_v2,
)
from ecomsre.phase5a.specialists import build_specialist_finding
from ecomsre.tools.base import ToolContext, ToolResultBase, ToolStatus
from ecomsre.tools.changes import ChangesQuery, list_changes
from ecomsre.tools.logs import LogsQuery, search_logs
from ecomsre.tools.metrics import MetricsQuery, query_metrics
from ecomsre.tools.traces import TracesQuery, search_traces


class DiagnosisVariantV2(str, Enum):
    SINGLE_AGENT_V2 = "SINGLE_AGENT_V2"
    FIXED_SPECIALIST_V2 = "FIXED_SPECIALIST_V2"
    DYNAMIC_MULTI_AGENT_V2 = "DYNAMIC_MULTI_AGENT_V2"


_PHASE2_VARIANT = {
    DiagnosisVariantV2.SINGLE_AGENT_V2: Phase2Variant.SINGLE_AGENT,
    DiagnosisVariantV2.FIXED_SPECIALIST_V2: (
        Phase2Variant.FIXED_SPECIALIST_WORKFLOW
    ),
    DiagnosisVariantV2.DYNAMIC_MULTI_AGENT_V2: (
        Phase2Variant.DYNAMIC_MULTI_AGENT
    ),
}

_SOURCE_ORDER = (
    EvidenceSource.METRICS,
    EvidenceSource.LOGS,
    EvidenceSource.TRACES,
    EvidenceSource.CHANGES,
)
_ACTION_TYPE = {
    EvidenceSource.METRICS: MetricsAction,
    EvidenceSource.LOGS: LogsAction,
    EvidenceSource.TRACES: TracesAction,
    EvidenceSource.CHANGES: ChangesAction,
}


class ModelCallCostV2(Phase5AModel):
    schema_version: Literal["phase5a.model-call-cost.v2"]
    operation: ModelOperation
    input_tokens: StrictInt = Field(gt=0)
    output_tokens: StrictInt = Field(gt=0)
    total_tokens: StrictInt = Field(gt=0)
    no_retry: Literal[True]

    @model_validator(mode="after")
    def require_total(self) -> ModelCallCostV2:
        if self.input_tokens + self.output_tokens != self.total_tokens:
            raise ValueError("model-call total tokens do not add up")
        return self


class SourceObservationV2(Phase5AModel):
    schema_version: Literal["phase5a.source-observation.v2"]
    source: EvidenceSource
    status: ObservationsStatusV2
    error_code: StableErrorCode | None = None

    @model_validator(mode="after")
    def require_status_error_pair(self) -> SourceObservationV2:
        if self.status in {
            ObservationsStatusV2.AVAILABLE,
            ObservationsStatusV2.EMPTY,
        } and self.error_code is not None:
            raise ValueError("successful source status cannot carry an error code")
        if self.status in {
            ObservationsStatusV2.SOURCE_UNAVAILABLE,
            ObservationsStatusV2.QUERY_FAILED,
        } and self.error_code is None:
            raise ValueError("failed source status requires an error code")
        return self


class DiagnosisWorkflowTraceV2(Phase5AModel):
    schema_version: Literal["phase5a.diagnosis-workflow-trace.v2"]
    run_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    case_id: str
    variant: DiagnosisVariantV2
    status: Literal["COMPLETED", "FAILED"]
    final_diagnosis: DiagnosisResultV2 | None = None
    findings: tuple[SpecialistFindingV2, ...]
    tool_call_records: tuple[ToolCallRecord, ...]
    model_call_costs: tuple[ModelCallCostV2, ...]
    source_observations: tuple[SourceObservationV2, ...]
    investigated_sources: tuple[EvidenceSource, ...]
    admitted_graphs: tuple[AdmittedInvestigationGraph, ...]
    targeted_refinement_used: bool
    final_budget_snapshot: BudgetSnapshot
    model_mode: Literal["SCRIPTED_REPLAY"]
    live_environment: Literal[False]
    phase5b_entered: Literal[False]
    terminal_reason: str | None = Field(default=None, max_length=128)

    @model_validator(mode="after")
    def require_trace_consistency(self) -> DiagnosisWorkflowTraceV2:
        if self.status == "COMPLETED":
            if self.final_diagnosis is None or self.terminal_reason is not None:
                raise ValueError("completed workflow requires one diagnosis")
            snapshot = self.final_budget_snapshot
            if (
                snapshot.active_capacity_slot_ids
                or snapshot.active_specialist_authorization_ids
                or snapshot.active_lease_ids
            ):
                raise ValueError("completed workflow retains active budget records")
        elif self.final_diagnosis is not None or self.terminal_reason is None:
            raise ValueError("failed workflow requires one terminal reason")
        snapshot = self.final_budget_snapshot
        retained_tokens = sum(item.total_tokens for item in self.model_call_costs)
        retained_tools = len(self.tool_call_records)
        retained_models = len(self.model_call_costs)
        if self.status == "COMPLETED" and (
            snapshot.charged_tool_calls != retained_tools
            or snapshot.charged_model_calls != retained_models
            or snapshot.cumulative_tokens != retained_tokens
        ):
            raise ValueError("completed workflow lost charged audit records")
        if self.status == "FAILED" and (
            snapshot.charged_tool_calls < retained_tools
            or snapshot.charged_model_calls < retained_models
            or snapshot.cumulative_tokens < retained_tokens
        ):
            raise ValueError("failed workflow audit exceeds its ledger snapshot")
        return self


class _DeterministicIds:
    def __init__(self) -> None:
        self._next = 0

    def __call__(self, prefix: str) -> str:
        self._next += 1
        return f"{prefix}-{self._next:04d}"


def stable_v2_run_id(case_id: str, variant: DiagnosisVariantV2) -> str:
    return hashlib.sha256(
        f"phase5a.diagnosis-quality-v2:{case_id}:{variant.value}".encode()
    ).hexdigest()[:32]


def _ledger(
    *,
    run_id: str,
    case_id: str,
    variant: DiagnosisVariantV2,
    replay_case: ReplayCase,
) -> BudgetLedger:
    return BudgetLedger(
        run_id=run_id,
        variant=_PHASE2_VARIANT[variant],
        case_id=case_id,
        max_model_calls=8,
        max_tool_calls=8,
        max_total_tokens=32_000,
        id_factory=_DeterministicIds(),
        monotonic_clock=lambda: 0.0,
        utc_clock=lambda: replay_case.incident.started_at,
    )


def _jsonable(value: object) -> object:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")  # type: ignore[union-attr]
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_jsonable(item) for item in value]
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    return value


def _charge_model_call(
    *,
    ledger: BudgetLedger,
    operation: ModelOperation,
    allowed_actions: ModelAllowedActions,
    payload: object,
    expires_at,
    source_record_id: str | None = None,
    owner_role: BudgetOwnerRole | None = None,
    owner_node_id: str | None = None,
) -> ModelCallCostV2:
    encoded = json.dumps(
        _jsonable(payload),
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    input_tokens = max(32, (len(encoded) + 3) // 4)
    output_tokens = 32
    if source_record_id is None:
        slots, _ = ledger.hold_capacity_slots(
            expected_snapshot_sequence=ledger.snapshot().sequence,
            requests=(
                CapacitySlotRequest(
                    permitted_operation=operation,
                    allowed_actions=allowed_actions,
                    reserved_model_calls=1,
                    reserved_tool_calls=0,
                    minimum_token_floor=1,
                    expires_at=expires_at,
                ),
            ),
        )
        source_record_id = slots[0].slot_id
        if operation in {
            ModelOperation.FIRST_JUDGE_MODEL,
            ModelOperation.FINAL_JUDGE_MODEL,
        }:
            owner_role = BudgetOwnerRole.RCA_JUDGE
        else:
            owner_role = BudgetOwnerRole.INCIDENT_COMMANDER
        owner_node_id = None
    assert owner_role is not None
    lease, _ = ledger.expand_exact_model_lease(
        expected_snapshot_sequence=ledger.snapshot().sequence,
        source_record_id=source_record_id,
        exact_input_tokens=input_tokens,
        minimum_completion_tokens=output_tokens,
        max_completion_tokens=output_tokens,
    )
    ledger.charge_exact_model_lease(
        expected_snapshot_sequence=ledger.snapshot().sequence,
        lease_id=lease.lease_id,
        owner_role=owner_role,
        owner_node_id=owner_node_id,
        source_record_id=source_record_id,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=input_tokens + output_tokens,
    )
    return ModelCallCostV2(
        schema_version="phase5a.model-call-cost.v2",
        operation=operation,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=input_tokens + output_tokens,
        no_retry=True,
    )


def _dispatch_tool(context: ToolContext, action: ToolAction) -> ToolResultBase:
    if type(action) is MetricsAction:
        return query_metrics(
            context,
            MetricsQuery(
                schema_version="phase1.metrics-query.v1",
                started_at=action.started_at,
                ended_at=action.ended_at,
                service=action.service,
            ),
        )
    if type(action) is LogsAction:
        return search_logs(
            context,
            LogsQuery(
                schema_version="phase1.logs-query.v1",
                started_at=action.started_at,
                ended_at=action.ended_at,
                service=action.service,
            ),
        )
    if type(action) is TracesAction:
        return search_traces(
            context,
            TracesQuery(
                schema_version="phase1.traces-query.v1",
                started_at=action.started_at,
                ended_at=action.ended_at,
                service=action.service,
            ),
        )
    if type(action) is ChangesAction:
        return list_changes(
            context,
            ChangesQuery(
                schema_version="phase1.changes-query.v1",
                started_at=action.started_at,
                ended_at=action.ended_at,
                service=action.service,
            ),
        )
    raise TypeError("unsupported Phase 5A tool action")


def _tool_record_executor(
    *,
    node: InvestigationNode,
    replay_case: ReplayCase,
    run_id: str,
    evidence_store: EvidenceStore,
    phase1_budget: RunBudget,
    backend: ReplayObservabilityBackend,
    timeout_seconds: float,
) -> Callable[[ToolAction], ToolCallRecord]:
    def execute(action: ToolAction) -> ToolCallRecord:
        before = len(evidence_store.snapshot())
        result = _dispatch_tool(
            ToolContext(
                incident=replay_case.incident,
                evidence_store=evidence_store,
                budget=phase1_budget,
                backend=backend,
                timeout_seconds=timeout_seconds,
            ),
            action,
        )
        new_evidence = evidence_store.snapshot()[before:]
        ok = result.status is ToolStatus.OK
        return ToolCallRecord(
            schema_version="phase1.tool-call-record.v1",
            call_id=f"tool-{node.node_id}",
            run_id=run_id,
            agent_id=node.specialist_role.value,
            incident_id=replay_case.incident.incident_id,
            task_id=node.node_id,
            tool_name=node.tool_name,
            action=action,
            evidence=new_evidence,
            evidence_refs=result.evidence_refs,
            started_at=action.started_at,
            ended_at=action.ended_at,
            monotonic_duration_seconds=0.0,
            budget_consumed=result.budget_consumed,
            dispatched=result.dispatched,
            evidence_quarantined=False,
            usable=ok,
            status="OK" if ok else "ERROR",
            error_code=result.error_code,
        )

    return execute


def _source_observation(record: ToolCallRecord) -> SourceObservationV2:
    if record.status == "OK":
        status = (
            ObservationsStatusV2.AVAILABLE
            if record.evidence
            else ObservationsStatusV2.EMPTY
        )
    elif record.error_code is StableErrorCode.BACKEND_UNAVAILABLE:
        status = ObservationsStatusV2.SOURCE_UNAVAILABLE
    else:
        status = ObservationsStatusV2.QUERY_FAILED
    return SourceObservationV2(
        schema_version="phase5a.source-observation.v2",
        source=record.evidence[0].source if record.evidence else {
            ReadOnlyToolName.QUERY_METRICS: EvidenceSource.METRICS,
            ReadOnlyToolName.SEARCH_LOGS: EvidenceSource.LOGS,
            ReadOnlyToolName.SEARCH_TRACES: EvidenceSource.TRACES,
            ReadOnlyToolName.LIST_CHANGES: EvidenceSource.CHANGES,
        }[record.tool_name],
        status=status,
        error_code=record.error_code,
    )


def _node(
    *,
    source: EvidenceSource,
    node_id: str,
    replay_case: ReplayCase,
    priority: int,
) -> InvestigationNode:
    owner, bound_source, tool_name = SPECIALIST_TOOL_BINDINGS[
        {
            EvidenceSource.METRICS: SpecialistRole.METRICS_AGENT,
            EvidenceSource.LOGS: SpecialistRole.LOGS_AGENT,
            EvidenceSource.TRACES: SpecialistRole.TRACE_AGENT,
            EvidenceSource.CHANGES: SpecialistRole.CHANGE_AGENT,
        }[source]
    ]
    del owner
    assert bound_source is source
    action = _ACTION_TYPE[source](
        action_type=source.value.lower(),
        started_at=replay_case.incident.started_at,
        ended_at=replay_case.incident.ended_at,
        service=None,
    )
    return InvestigationNode(
        schema_version="phase2.investigation-node.v1",
        node_id=node_id,
        source=source,
        specialist_role={
            EvidenceSource.METRICS: SpecialistRole.METRICS_AGENT,
            EvidenceSource.LOGS: SpecialistRole.LOGS_AGENT,
            EvidenceSource.TRACES: SpecialistRole.TRACE_AGENT,
            EvidenceSource.CHANGES: SpecialistRole.CHANGE_AGENT,
        }[source],
        tool_name=tool_name,
        query=action,
        depends_on=(),
        objective=f"Inspect bounded {source.value} observations.",
        query_started_at=replay_case.incident.started_at,
        query_ended_at=replay_case.incident.ended_at,
        priority=priority,
    )


def _graph(
    *,
    run_id: str,
    replay_case: ReplayCase,
    ledger: BudgetLedger,
    plan_id: str,
    sources: tuple[EvidenceSource, ...],
    fixed: bool = False,
) -> AdmittedInvestigationGraph:
    nodes = tuple(
        _node(
            source=source,
            node_id=f"{plan_id}-{source.value.lower()}",
            replay_case=replay_case,
            priority=index,
        )
        for index, source in enumerate(sources)
    )
    if fixed:
        return build_fixed_admitted_graph(
            FixedInvestigationPlan(
                schema_version="phase2.fixed-investigation-plan.v1",
                run_id=run_id,
                incident_id=replay_case.incident.incident_id,
                plan_id=plan_id,
                nodes=nodes,
                planning_rationale="Inspect all four bounded sources once.",
                budget_snapshot_id=ledger.snapshot().snapshot_id,
            )
        )
    return build_initial_admitted_graph(
        InvestigationPlan(
            schema_version="phase2.investigation-plan.v1",
            run_id=run_id,
            incident_id=replay_case.incident.incident_id,
            plan_id=plan_id,
            nodes=nodes,
            planning_rationale="Use one bounded evidence-driven investigation stage.",
            budget_snapshot_id=ledger.snapshot().snapshot_id,
        )
    )


def _execute_specialist(
    *,
    ledger: BudgetLedger,
    graph: AdmittedInvestigationGraph,
    node: InvestigationNode,
    replay_case: ReplayCase,
    evidence_store: EvidenceStore,
    phase1_budget: RunBudget,
    backend: ReplayObservabilityBackend,
    timeout_seconds: float,
) -> tuple[ToolCallRecord, SpecialistFindingV2, ModelCallCostV2, SourceObservationV2]:
    slots, _ = ledger.hold_capacity_slots(
        expected_snapshot_sequence=ledger.snapshot().sequence,
        requests=(
            CapacitySlotRequest(
                permitted_operation=ModelOperation.SPECIALIST_MODEL,
                allowed_actions=ModelAllowedActions.FINDING_ONLY,
                reserved_model_calls=1,
                reserved_tool_calls=1,
                minimum_token_floor=1,
                expires_at=replay_case.incident.ended_at + timedelta(minutes=5),
            ),
        ),
    )
    owner_role = BudgetOwnerRole(node.specialist_role.value)
    authorization, _ = ledger.materialize_specialist_authorization(
        expected_snapshot_sequence=ledger.snapshot().sequence,
        slot_id=slots[0].slot_id,
        owner_role=owner_role,
        owner_node_id=node.node_id,
        source=node.source,
        tool_name=node.tool_name,
    )
    task = SpecialistTask(
        schema_version="phase2.specialist-task.v1",
        run_id=graph.run_id,
        incident_id=graph.incident_id,
        plan_id=graph.initial_plan.plan_id,
        node_id=node.node_id,
        source=node.source,
        specialist_role=node.specialist_role,
        tool_name=node.tool_name,
        query=node.query,
        objective=node.objective,
        dependency_finding_ids=(),
        dependency_evidence_refs=(),
        tool_authorization_id=authorization.authorization_id,
        model_capacity_slot_id=authorization.capacity_slot_id,
    )
    registry = SpecialistToolRegistry(
        run_id=graph.run_id,
        case_id=replay_case.case_id,
        variant=ledger.snapshot().variant,
        specialist_role=node.specialist_role,
        ledger=ledger,
        executor=_tool_record_executor(
            node=node,
            replay_case=replay_case,
            run_id=graph.run_id,
            evidence_store=evidence_store,
            phase1_budget=phase1_budget,
            backend=backend,
            timeout_seconds=timeout_seconds,
        ),
    )
    record, charged_authorization, _snapshot, _receipt = (
        registry.dispatch_attempt(task)
    )
    observation = _source_observation(record)
    finding = build_specialist_finding(
        run_id=graph.run_id,
        incident=replay_case.incident,
        plan_id=graph.initial_plan.plan_id,
        node_id=node.node_id,
        source=node.source,
        specialist_role=node.specialist_role,
        observations_status=observation.status,
        evidence=record.evidence,
    )
    cost = _charge_model_call(
        ledger=ledger,
        operation=ModelOperation.SPECIALIST_MODEL,
        allowed_actions=ModelAllowedActions.FINDING_ONLY,
        payload={"task": task, "finding": finding},
        expires_at=replay_case.incident.ended_at + timedelta(minutes=5),
        source_record_id=charged_authorization.authorization_id,
        owner_role=owner_role,
        owner_node_id=node.node_id,
    )
    ledger.complete_specialist_authorization(
        expected_snapshot_sequence=ledger.snapshot().sequence,
        authorization_id=charged_authorization.authorization_id,
    )
    return record, finding, cost, observation


def run_diagnosis_v2(
    *,
    project_root: Path,
    replay_case: ReplayCase,
    variant: DiagnosisVariantV2,
) -> DiagnosisWorkflowTraceV2:
    """Run one public replay case without evaluator, network, or mutation."""

    replay_case = ReplayCase.model_validate(replay_case)
    variant = DiagnosisVariantV2(variant)
    run_id = stable_v2_run_id(replay_case.case_id, variant)
    ledger = _ledger(
        run_id=run_id,
        case_id=replay_case.case_id,
        variant=variant,
        replay_case=replay_case,
    )
    evidence_store = EvidenceStore(run_id)
    phase1_budget = RunBudget(
        BudgetLimits(max_model_calls=8, max_tool_calls=8, max_total_tokens=32_000)
    )
    backend = ReplayObservabilityBackend(replay_case)
    timeout_seconds = load_agent_settings(Path(project_root)).tool_timeout_seconds
    records: list[ToolCallRecord] = []
    findings: list[SpecialistFindingV2] = []
    costs: list[ModelCallCostV2] = []
    observations: list[SourceObservationV2] = []
    sources: list[EvidenceSource] = []
    graphs: list[AdmittedInvestigationGraph] = []
    targeted_refinement_used = False

    try:
        if variant is DiagnosisVariantV2.SINGLE_AGENT_V2:
            for index, source in enumerate(SINGLE_V2_SOURCE_ORDER):
                graph = _graph(
                    run_id=run_id,
                    replay_case=replay_case,
                    ledger=ledger,
                    plan_id=f"single-stage-{index}",
                    sources=(source,),
                )
                node = graph.initial_plan.nodes[0]
                ledger.charge_single_agent_tool_attempt(
                    expected_snapshot_sequence=ledger.snapshot().sequence,
                    attempt_id=f"single-tool-{index}",
                    tool_name=node.tool_name,
                )
                record = _tool_record_executor(
                    node=node,
                    replay_case=replay_case,
                    run_id=run_id,
                    evidence_store=evidence_store,
                    phase1_budget=phase1_budget,
                    backend=backend,
                    timeout_seconds=timeout_seconds,
                )(node.query)
                records.append(record)
                observations.append(_source_observation(record))
                sources.append(source)
                costs.append(
                    _charge_model_call(
                        ledger=ledger,
                        operation=ModelOperation.SINGLE_AGENT_MODEL,
                        allowed_actions=ModelAllowedActions.PHASE1_ACTION_CATALOG,
                        payload={"source": source.value, "record": record},
                        expires_at=(
                            replay_case.incident.ended_at + timedelta(minutes=5)
                        ),
                    )
                )
            final = finalize_single_diagnosis_v2(
                run_id=run_id,
                incident=replay_case.incident,
                evidence=evidence_store.snapshot(),
            )
            costs.append(
                _charge_model_call(
                    ledger=ledger,
                    operation=ModelOperation.SINGLE_AGENT_MODEL,
                    allowed_actions=ModelAllowedActions.PHASE1_ACTION_CATALOG,
                    payload={"final": final, "evidence": evidence_store.snapshot()},
                    expires_at=replay_case.incident.ended_at + timedelta(minutes=5),
                )
            )
        else:
            stage_graphs: tuple[AdmittedInvestigationGraph, ...]
            if variant is DiagnosisVariantV2.FIXED_SPECIALIST_V2:
                graph = _graph(
                    run_id=run_id,
                    replay_case=replay_case,
                    ledger=ledger,
                    plan_id="fixed-v2-plan",
                    sources=_SOURCE_ORDER,
                    fixed=True,
                )
                graphs.append(graph)
                stage_graphs = (graph,)
            else:
                costs.append(
                    _charge_model_call(
                        ledger=ledger,
                        operation=ModelOperation.COMMANDER_MODEL,
                        allowed_actions=ModelAllowedActions.PLAN_ONLY,
                        payload={"stage": 1, "incident": replay_case.incident},
                        expires_at=(
                            replay_case.incident.ended_at + timedelta(minutes=5)
                        ),
                    )
                )
                metrics_graph = _graph(
                    run_id=run_id,
                    replay_case=replay_case,
                    ledger=ledger,
                    plan_id="dynamic-stage-1",
                    sources=STAGE1_SOURCES,
                )
                graphs.append(metrics_graph)
                record, finding, cost, observation = _execute_specialist(
                    ledger=ledger,
                    graph=metrics_graph,
                    node=metrics_graph.initial_plan.nodes[0],
                    replay_case=replay_case,
                    evidence_store=evidence_store,
                    phase1_budget=phase1_budget,
                    backend=backend,
                    timeout_seconds=timeout_seconds,
                )
                records.append(record)
                findings.append(finding)
                costs.append(cost)
                observations.append(observation)
                sources.append(EvidenceSource.METRICS)
                stage2_sources = select_stage2_sources(finding)
                costs.append(
                    _charge_model_call(
                        ledger=ledger,
                        operation=ModelOperation.COMMANDER_MODEL,
                        allowed_actions=ModelAllowedActions.PLAN_ONLY,
                        payload={"stage": 2, "metrics_finding": finding},
                        expires_at=(
                            replay_case.incident.ended_at + timedelta(minutes=5)
                        ),
                    )
                )
                if stage2_sources:
                    stage2_graph = _graph(
                        run_id=run_id,
                        replay_case=replay_case,
                        ledger=ledger,
                        plan_id="dynamic-stage-2",
                        sources=stage2_sources,
                    )
                    graphs.append(stage2_graph)
                    stage_graphs = (stage2_graph,)
                else:
                    stage_graphs = ()

            for graph in stage_graphs:
                for node in graph.initial_plan.nodes:
                    if node.source in sources:
                        continue
                    record, finding, cost, observation = _execute_specialist(
                        ledger=ledger,
                        graph=graph,
                        node=node,
                        replay_case=replay_case,
                        evidence_store=evidence_store,
                        phase1_budget=phase1_budget,
                        backend=backend,
                        timeout_seconds=timeout_seconds,
                    )
                    records.append(record)
                    findings.append(finding)
                    costs.append(cost)
                    observations.append(observation)
                    sources.append(node.source)

            assessment = judge_diagnosis_v2(
                run_id=run_id,
                incident=replay_case.incident,
                findings=tuple(findings),
                evidence=evidence_store.snapshot(),
            )
            final = assessment.result
            judge_allowed = (
                ModelAllowedActions.FINAL_OR_REFINEMENT
                if variant is DiagnosisVariantV2.DYNAMIC_MULTI_AGENT_V2
                else ModelAllowedActions.FINAL_ONLY
            )
            costs.append(
                _charge_model_call(
                    ledger=ledger,
                    operation=ModelOperation.FIRST_JUDGE_MODEL,
                    allowed_actions=judge_allowed,
                    payload={"findings": tuple(findings), "result": final},
                    expires_at=replay_case.incident.ended_at + timedelta(minutes=5),
                )
            )
            if variant is DiagnosisVariantV2.DYNAMIC_MULTI_AGENT_V2:
                refinement = select_targeted_refinement(
                    result=final,
                    findings=tuple(findings),
                    investigated_sources=tuple(sources),
                )
                if refinement is not None:
                    targeted_refinement_used = True
                    refinement_graph = _graph(
                        run_id=run_id,
                        replay_case=replay_case,
                        ledger=ledger,
                        plan_id="dynamic-refinement-1",
                        sources=(refinement.source,),
                    )
                    graphs.append(refinement_graph)
                    record, finding, cost, observation = _execute_specialist(
                        ledger=ledger,
                        graph=refinement_graph,
                        node=refinement_graph.initial_plan.nodes[0],
                        replay_case=replay_case,
                        evidence_store=evidence_store,
                        phase1_budget=phase1_budget,
                        backend=backend,
                        timeout_seconds=timeout_seconds,
                    )
                    records.append(record)
                    findings.append(finding)
                    costs.append(cost)
                    observations.append(observation)
                    sources.append(refinement.source)
                    final = judge_diagnosis_v2(
                        run_id=run_id,
                        incident=replay_case.incident,
                        findings=tuple(findings),
                        evidence=evidence_store.snapshot(),
                    ).result
                    costs.append(
                        _charge_model_call(
                            ledger=ledger,
                            operation=ModelOperation.FINAL_JUDGE_MODEL,
                            allowed_actions=ModelAllowedActions.FINAL_ONLY,
                            payload={"findings": tuple(findings), "result": final},
                            expires_at=(
                                replay_case.incident.ended_at
                                + timedelta(minutes=5)
                            ),
                        )
                    )

        return DiagnosisWorkflowTraceV2(
            schema_version="phase5a.diagnosis-workflow-trace.v2",
            run_id=run_id,
            case_id=replay_case.case_id,
            variant=variant,
            status="COMPLETED",
            final_diagnosis=final,
            findings=tuple(findings),
            tool_call_records=tuple(records),
            model_call_costs=tuple(costs),
            source_observations=tuple(observations),
            investigated_sources=tuple(sources),
            admitted_graphs=tuple(graphs),
            targeted_refinement_used=targeted_refinement_used,
            final_budget_snapshot=ledger.snapshot(),
            model_mode="SCRIPTED_REPLAY",
            live_environment=False,
            phase5b_entered=False,
            terminal_reason=None,
        )
    except Exception as error:
        return DiagnosisWorkflowTraceV2(
            schema_version="phase5a.diagnosis-workflow-trace.v2",
            run_id=run_id,
            case_id=replay_case.case_id,
            variant=variant,
            status="FAILED",
            final_diagnosis=None,
            findings=tuple(findings),
            tool_call_records=tuple(records),
            model_call_costs=tuple(costs),
            source_observations=tuple(observations),
            investigated_sources=tuple(sources),
            admitted_graphs=tuple(graphs),
            targeted_refinement_used=targeted_refinement_used,
            final_budget_snapshot=ledger.snapshot(),
            model_mode="SCRIPTED_REPLAY",
            live_environment=False,
            phase5b_entered=False,
            terminal_reason=type(error).__name__,
        )
