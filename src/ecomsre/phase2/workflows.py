"""Offline assembly for the three Phase 2 comparison workflows."""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from ecomsre.backends.live_protocol import (
    ChangesObservationBatch,
    LogsObservationBatch,
    MetricsObservationBatch,
    TracesObservationBatch,
)
from ecomsre.backends.replay import ReplayCase, ReplayObservabilityBackend
from ecomsre.phase1.agent import SingleAgent
from ecomsre.phase1.budgets import RunBudget
from ecomsre.phase1.contracts import (
    AgentRunReport,
    BudgetLimits,
    ChangesAction,
    EvidenceSource,
    InvestigationRequest,
    LogsAction,
    MetricsAction,
    ModelConfiguration,
    RCAResult,
    ReadOnlyToolName,
    ToolAction,
    ToolCallRecord,
    TracesAction,
)
from ecomsre.phase1.evidence import EvidenceStore
from ecomsre.phase1.runtime_config import load_agent_settings
from ecomsre.phase2.budgets import BudgetLedger
from ecomsre.phase2.commander import CommanderContext, CommanderRuntime, source_capabilities
from ecomsre.phase2.comparison_adapter import (
    BudgetCaps,
    ComparisonAdapter,
    ModelCallAuditRecord,
    Phase1GatewayBackend,
    TypedModelBackend,
    ToolCallAuditRecord,
    make_phase1_comparison_gateway,
)
from ecomsre.phase2.contracts import (
    AdmittedInvestigationGraph,
    BudgetAuditEvent,
    BudgetSnapshot,
    CapacitySlotRequest,
    FixedInvestigationPlan,
    InvestigationNode,
    ModelAllowedActions,
    ModelOperation,
    Phase2FailureCode,
    Phase2Model,
    Phase2Variant,
    SpecialistFinding,
    SpecialistRole,
    SpecialistTask,
    SpecialistToolDispatchResult,
    build_fixed_admitted_graph,
)
from ecomsre.phase2.dag import schedule_layers
from ecomsre.phase2.evidence_views import FindingStore
from ecomsre.phase2.judge import JudgeContext, JudgeOutcome, JudgeRuntime
from ecomsre.phase2.scripted import ExactTokenScriptedGateway, ScriptedModelBackend
from ecomsre.phase2.specialists import (
    SpecialistError,
    SpecialistExecutionContext,
    SpecialistOutcome,
    SpecialistRuntime,
)
from ecomsre.phase2.token_policy import MODEL_SNAPSHOT, TokenAuthority, load_token_authority
from ecomsre.phase2.tool_isolation import SpecialistToolRegistry
from ecomsre.tools.base import ToolContext, ToolResultBase, ToolStatus
from ecomsre.tools.changes import ChangesQuery, list_changes
from ecomsre.tools.logs import LogsQuery, search_logs
from ecomsre.tools.metrics import MetricsQuery, query_metrics
from ecomsre.tools.traces import TracesQuery, search_traces


_CAPS = BudgetCaps()
_PROVIDER_ID = "phase2-scripted"


class WorkflowRunTrace(Phase2Model):
    """Serializable graph, audit, failure, and usage trace for one run."""

    schema_version: Literal["phase2.workflow-run-trace.v1"]
    run_id: str
    variant: Phase2Variant
    case_id: str
    status: Literal["COMPLETED", "FAILED"]
    final_rca: RCAResult | None = None
    admitted_graph: AdmittedInvestigationGraph | None = None
    findings: tuple[SpecialistFinding, ...] = ()
    tool_call_records: tuple[ToolCallRecord, ...] = ()
    model_call_audits: tuple[ModelCallAuditRecord, ...] = ()
    tool_call_audits: tuple[ToolCallAuditRecord, ...] = ()
    budget_audit_events: tuple[BudgetAuditEvent, ...] = ()
    final_budget_snapshot: BudgetSnapshot
    terminal_failure_code: Phase2FailureCode | None = None
    terminal_reason: str | None = Field(default=None, max_length=128)

    @model_validator(mode="after")
    def require_terminal_consistency(self) -> WorkflowRunTrace:
        if self.status == "COMPLETED":
            if self.final_rca is None or self.terminal_reason is not None:
                raise ValueError("completed workflow requires only a final RCA")
        elif self.final_rca is not None or self.terminal_reason is None:
            raise ValueError("failed workflow requires one stable terminal reason")
        if (
            self.final_budget_snapshot.run_id != self.run_id
            or self.final_budget_snapshot.variant is not self.variant
            or self.final_budget_snapshot.case_id != self.case_id
        ):
            raise ValueError("workflow trace differs from its final budget scope")
        if any(record.run_id != self.run_id for record in self.tool_call_records):
            raise ValueError("workflow trace contains a cross-run tool record")
        if (
            len(self.tool_call_audits)
            != self.final_budget_snapshot.charged_tool_calls
        ):
            raise ValueError("workflow trace lost charged tool-call audit lineage")
        return self


@dataclass(frozen=True, slots=True)
class WorkflowRunResult:
    trace: WorkflowRunTrace
    phase1_report: AgentRunReport | None = None


class _DeterministicIds:
    def __init__(self) -> None:
        self._next = 0

    def __call__(self, prefix: str) -> str:
        self._next += 1
        return f"{prefix}-{self._next:04d}"


def stable_workflow_run_id(
    case_id: str,
    variant: Phase2Variant,
    *,
    namespace: str = "phase2-comparison",
) -> str:
    digest = hashlib.sha256(
        f"{namespace}:{case_id}:{variant.value}".encode("utf-8")
    ).hexdigest()
    return f"{digest[:8]}{'0' * 24}"


def _ledger(
    *,
    run_id: str,
    case_id: str,
    variant: Phase2Variant,
    now: datetime,
) -> BudgetLedger:
    return BudgetLedger(
        run_id=run_id,
        variant=variant,
        case_id=case_id,
        max_model_calls=_CAPS.model_calls,
        max_tool_calls=_CAPS.tool_calls,
        max_total_tokens=_CAPS.total_tokens,
        id_factory=_DeterministicIds(),
        monotonic_clock=lambda: 0.0,
        utc_clock=lambda: now,
    )


def _dispatch_tool(
    context: ToolContext,
    action: ToolAction,
) -> ToolResultBase:
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
    raise TypeError("unsupported workflow tool action")


def _tool_executor(
    *,
    node: InvestigationNode,
    incident,
    evidence_store: EvidenceStore,
    phase1_budget: RunBudget,
    backend: ReplayObservabilityBackend,
    timeout_seconds: float,
) -> Callable[[ToolAction], ToolCallRecord]:
    def execute(action: ToolAction) -> ToolCallRecord:
        before = len(evidence_store.snapshot())
        result = _dispatch_tool(
            ToolContext(
                incident=incident,
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
            run_id=evidence_store.run_id,
            agent_id=node.specialist_role.value,
            incident_id=incident.incident_id,
            task_id=node.node_id,
            tool_name=node.tool_name,
            action=action,
            evidence=new_evidence,
            evidence_refs=result.evidence_refs,
            started_at=action.started_at,
            ended_at=action.started_at,
            monotonic_duration_seconds=0.0,
            budget_consumed=result.budget_consumed,
            dispatched=result.dispatched,
            evidence_quarantined=False,
            usable=ok,
            status="OK" if ok else "ERROR",
            error_code=result.error_code,
        )

    return execute


def _registries(
    *,
    nodes: tuple[InvestigationNode, ...],
    ledger: BudgetLedger,
    case_id: str,
    variant: Phase2Variant,
    incident,
    evidence_store: EvidenceStore,
    phase1_budget: RunBudget,
    backend: ReplayObservabilityBackend,
    timeout_seconds: float,
) -> Mapping[SpecialistRole, SpecialistToolRegistry]:
    if len({node.specialist_role for node in nodes}) != len(nodes):
        raise ValueError("one workflow layer cannot repeat a Specialist role")
    return {
        node.specialist_role: SpecialistToolRegistry(
            run_id=ledger.snapshot().run_id,
            case_id=case_id,
            variant=variant,
            specialist_role=node.specialist_role,
            ledger=ledger,
            executor=_tool_executor(
                node=node,
                incident=incident,
                evidence_store=evidence_store,
                phase1_budget=phase1_budget,
                backend=backend,
                timeout_seconds=timeout_seconds,
            ),
        )
        for node in nodes
    }


def _fixed_graph(run_id: str, replay_case: ReplayCase, snapshot_id: str):
    query_types = {
        EvidenceSource.METRICS: MetricsAction,
        EvidenceSource.LOGS: LogsAction,
        EvidenceSource.TRACES: TracesAction,
        EvidenceSource.CHANGES: ChangesAction,
    }
    nodes = tuple(
        InvestigationNode(
            schema_version="phase2.investigation-node.v1",
            node_id=f"fixed-{capability.source.value.lower()}-1",
            source=capability.source,
            specialist_role=capability.specialist_role,
            tool_name=capability.tool_name,
            query=query_types[capability.source](
                action_type=capability.action_type,
                started_at=replay_case.incident.started_at,
                ended_at=replay_case.incident.ended_at,
                service=None,
            ),
            depends_on=(),
            objective=f"Inspect bounded {capability.source.value.lower()} observations.",
            query_started_at=replay_case.incident.started_at,
            query_ended_at=replay_case.incident.ended_at,
            priority=priority,
        )
        for priority, capability in enumerate(source_capabilities())
    )
    return build_fixed_admitted_graph(
        FixedInvestigationPlan(
            schema_version="phase2.fixed-investigation-plan.v1",
            run_id=run_id,
            incident_id=replay_case.incident.incident_id,
            plan_id=f"fixed-plan-{replay_case.case_id}",
            nodes=nodes,
            planning_rationale="Inspect each bounded read-only source once.",
            budget_snapshot_id=snapshot_id,
        )
    )


def _tool_audits(
    ledger: BudgetLedger,
    *,
    variant: Phase2Variant,
    case_id: str,
) -> tuple[ToolCallAuditRecord, ...]:
    dispatch_outcomes = {
        "complete-specialist-tool-dispatch",
        "fail-specialist-tool-dispatch",
    }
    records: list[ToolCallAuditRecord] = []
    for event in ledger.audit_events():
        if event.event_type not in dispatch_outcomes:
            continue
        if len(event.record_ids) != 1:
            raise ValueError("dispatch outcome event must name one authorization")
        authorization = ledger.specialist_authorization(event.record_ids[0])
        if authorization.actual_tool_calls != 1:
            raise ValueError("dispatch outcome lost its charged tool attempt")
        records.append(
            ToolCallAuditRecord(
                schema_version="phase2.tool-call-audit.v1",
                call_id=authorization.authorization_id,
                run_id=authorization.run_id,
                variant=variant,
                case_id=case_id,
                source=authorization.source,
                tool_name=authorization.tool_name,
                charged_tool_calls=1,
                final_snapshot_sequence=event.snapshot_sequence,
            )
        )
    return tuple(records)


def _failure_projection(
    error: Exception,
    ledger: BudgetLedger,
) -> tuple[Phase2FailureCode | None, str]:
    code = getattr(error, "code", None)
    terminal_code = ledger.terminal_failure_code
    if isinstance(error, SpecialistError) and isinstance(code, Phase2FailureCode):
        terminal_code = code
    reason = getattr(code, "value", type(error).__name__)
    return terminal_code, reason


def _trace(
    *,
    ledger: BudgetLedger,
    adapter: ComparisonAdapter,
    variant: Phase2Variant,
    case_id: str,
    final_rca: RCAResult | None,
    graph: AdmittedInvestigationGraph | None,
    specialist_outcomes: tuple[SpecialistOutcome, ...],
    successful_dispatches: tuple[
        tuple[SpecialistTask, SpecialistToolDispatchResult], ...
    ],
    terminal_failure_code: Phase2FailureCode | None,
    terminal_reason: str | None,
) -> WorkflowRunTrace:
    tool_records = tuple(
        dispatch_result.tool_call_record
        for _task, dispatch_result in successful_dispatches
    )
    tool_audits = _tool_audits(
        ledger,
        variant=variant,
        case_id=case_id,
    )
    return WorkflowRunTrace(
        schema_version="phase2.workflow-run-trace.v1",
        run_id=ledger.snapshot().run_id,
        variant=variant,
        case_id=case_id,
        status="COMPLETED" if final_rca is not None else "FAILED",
        final_rca=final_rca,
        admitted_graph=graph,
        findings=tuple(outcome.finding for outcome in specialist_outcomes),
        tool_call_records=tool_records,
        model_call_audits=adapter.audit_records,
        tool_call_audits=tool_audits,
        budget_audit_events=ledger.audit_events(),
        final_budget_snapshot=ledger.snapshot(),
        terminal_failure_code=terminal_failure_code,
        terminal_reason=terminal_reason,
    )


class _SingleAgentComparisonBackend:
    """Charge the shared ledger immediately before every Phase 1 tool entry."""

    def __init__(
        self,
        delegate: ReplayObservabilityBackend,
        adapter: ComparisonAdapter,
    ) -> None:
        self._delegate = delegate
        self._adapter = adapter
        self._calls = 0

    def _charge(self, tool_name: ReadOnlyToolName) -> None:
        self._calls += 1
        self._adapter.charge_single_agent_tool_attempt(
            attempt_id=f"tool-call-{self._calls:04d}",
            tool_name=tool_name,
        )

    def query_metrics(
        self,
        query: MetricsQuery,
        *,
        timeout_seconds: float,
    ) -> MetricsObservationBatch:
        self._charge(ReadOnlyToolName.QUERY_METRICS)
        return self._delegate.query_metrics(query, timeout_seconds=timeout_seconds)

    def search_logs(
        self,
        query: LogsQuery,
        *,
        timeout_seconds: float,
    ) -> LogsObservationBatch:
        self._charge(ReadOnlyToolName.SEARCH_LOGS)
        return self._delegate.search_logs(query, timeout_seconds=timeout_seconds)

    def search_traces(
        self,
        query: TracesQuery,
        *,
        timeout_seconds: float,
    ) -> TracesObservationBatch:
        self._charge(ReadOnlyToolName.SEARCH_TRACES)
        return self._delegate.search_traces(query, timeout_seconds=timeout_seconds)

    def list_changes(
        self,
        query: ChangesQuery,
        *,
        timeout_seconds: float,
    ) -> ChangesObservationBatch:
        self._charge(ReadOnlyToolName.LIST_CHANGES)
        return self._delegate.list_changes(query, timeout_seconds=timeout_seconds)


def _run_single(
    *,
    project_root: Path,
    replay_case: ReplayCase,
    run_id: str,
    ledger: BudgetLedger,
    authority: TokenAuthority,
) -> WorkflowRunResult:
    settings = load_agent_settings(project_root)
    inner = ExactTokenScriptedGateway(authority)
    adapter = ComparisonAdapter(
        ledger=ledger,
        token_authority=authority,
        backend=Phase1GatewayBackend(inner),
        expected_provider_identity=inner.provider_name,
        utc_clock=lambda: replay_case.incident.started_at,
    )
    agent = SingleAgent(
        gateway=make_phase1_comparison_gateway(inner, adapter),
        backend=_SingleAgentComparisonBackend(
            ReplayObservabilityBackend(replay_case),
            adapter,
        ),
        model_configuration=ModelConfiguration(
            model_name=MODEL_SNAPSHOT,
            temperature=0.0,
            model_timeout_seconds=settings.model_timeout_seconds,
        ),
        tool_timeout_seconds=settings.tool_timeout_seconds,
    )
    report = agent.run(
        InvestigationRequest(
            schema_version="phase1.investigation-request.v1",
            request_id=f"phase2-{replay_case.case_id}-single",
            run_id=run_id,
            agent_id="single-agent",
            task_id="root-cause-analysis",
            incident=replay_case.incident,
            budgets=BudgetLimits(
                max_model_calls=_CAPS.model_calls,
                max_tool_calls=_CAPS.tool_calls,
                max_total_tokens=_CAPS.total_tokens,
            ),
        )
    )
    tool_audits = tuple(
        ToolCallAuditRecord(
            schema_version="phase2.tool-call-audit.v1",
            call_id=record.call_id,
            run_id=run_id,
            variant=Phase2Variant.SINGLE_AGENT,
            case_id=replay_case.case_id,
            source=record.evidence[0].source if record.evidence else {
                ReadOnlyToolName.QUERY_METRICS: EvidenceSource.METRICS,
                ReadOnlyToolName.SEARCH_LOGS: EvidenceSource.LOGS,
                ReadOnlyToolName.SEARCH_TRACES: EvidenceSource.TRACES,
                ReadOnlyToolName.LIST_CHANGES: EvidenceSource.CHANGES,
            }[record.tool_name],
            tool_name=record.tool_name,
            charged_tool_calls=1,
            final_snapshot_sequence=ledger.snapshot().sequence,
        )
        for record in report.tool_call_records
        if record.budget_consumed
    )
    trace = WorkflowRunTrace(
        schema_version="phase2.workflow-run-trace.v1",
        run_id=run_id,
        variant=Phase2Variant.SINGLE_AGENT,
        case_id=replay_case.case_id,
        status="COMPLETED" if report.final_rca is not None else "FAILED",
        final_rca=report.final_rca,
        admitted_graph=None,
        findings=(),
        tool_call_records=report.tool_call_records,
        model_call_audits=adapter.audit_records,
        tool_call_audits=tool_audits,
        budget_audit_events=ledger.audit_events(),
        final_budget_snapshot=ledger.snapshot(),
        terminal_failure_code=ledger.terminal_failure_code,
        terminal_reason=(
            None if report.final_rca is not None else report.terminal_reason.value
        ),
    )
    return WorkflowRunResult(trace=trace, phase1_report=report)


def _run_fixed_or_dynamic(
    *,
    project_root: Path,
    replay_case: ReplayCase,
    run_id: str,
    ledger: BudgetLedger,
    authority: TokenAuthority,
    variant: Phase2Variant,
    allow_refinement: bool,
    model_backend: TypedModelBackend | None,
    expected_provider_identity: str,
) -> WorkflowRunResult:
    settings = load_agent_settings(project_root)
    evidence_store = EvidenceStore(run_id)
    finding_store = FindingStore(run_id)
    replay_backend = ReplayObservabilityBackend(replay_case)
    backend = model_backend or ScriptedModelBackend(
        token_authority=authority,
        provider_identity=_PROVIDER_ID,
    )
    adapter = ComparisonAdapter(
        ledger=ledger,
        token_authority=authority,
        backend=backend,
        expected_provider_identity=expected_provider_identity,
        utc_clock=lambda: replay_case.incident.started_at,
    )
    phase1_budget = RunBudget(
        BudgetLimits(
            max_model_calls=_CAPS.model_calls,
            max_tool_calls=_CAPS.tool_calls,
            max_total_tokens=_CAPS.total_tokens,
        )
    )
    outcomes: list[SpecialistOutcome] = []
    successful_dispatches: list[
        tuple[SpecialistTask, SpecialistToolDispatchResult]
    ] = []

    def remember_dispatch(
        task: SpecialistTask,
        dispatch_result: SpecialistToolDispatchResult,
    ) -> None:
        successful_dispatches.append((task, dispatch_result))

    graph: AdmittedInvestigationGraph | None = None
    try:
        if variant is Phase2Variant.DYNAMIC_MULTI_AGENT:
            commander = CommanderRuntime(
                ledger=ledger,
                adapter=adapter,
                utc_clock=lambda: replay_case.incident.started_at,
            ).create_initial_graph(
                CommanderContext(
                    schema_version="phase2.commander-context.v1",
                    run_id=run_id,
                    incident=replay_case.incident,
                    allowed_started_at=replay_case.incident.started_at,
                    allowed_ended_at=replay_case.incident.ended_at,
                )
            )
            graph = commander.admission.admitted_graph
            bindings = commander.admission.node_slot_bindings
            judge_slot_id = commander.admission.first_judge_capacity_slot_id
        else:
            graph = _fixed_graph(
                run_id,
                replay_case,
                ledger.snapshot().snapshot_id,
            )
            final_golden = authority.golden(
                ModelOperation.FINAL_JUDGE_MODEL,
                ModelAllowedActions.FINAL_ONLY,
            )
            slots, _ = ledger.hold_capacity_slots(
                expected_snapshot_sequence=ledger.snapshot().sequence,
                requests=(
                    CapacitySlotRequest(
                        permitted_operation=ModelOperation.FINAL_JUDGE_MODEL,
                        allowed_actions=ModelAllowedActions.FINAL_ONLY,
                        reserved_model_calls=1,
                        reserved_tool_calls=0,
                        minimum_token_floor=final_golden.minimum_call_floor_tokens,
                        expires_at=replay_case.incident.started_at + timedelta(minutes=5),
                    ),
                ),
            )
            judge_slot_id = slots[0].slot_id
            bindings = ()

        if graph is None:
            raise RuntimeError("workflow graph was not admitted")
        specialist_golden = authority.golden(
            ModelOperation.SPECIALIST_MODEL,
            ModelAllowedActions.FINDING_ONLY,
        )
        dynamic_slot_by_node = {
            binding.node_id: binding.specialist_capacity_slot_id
            for binding in bindings
        }
        finding_id_by_node: dict[str, str] = {}
        scheduled_nodes = (
            node for layer in schedule_layers(graph.initial_plan) for node in layer
        )
        for node in scheduled_nodes:
            if variant is Phase2Variant.DYNAMIC_MULTI_AGENT:
                slot_id = dynamic_slot_by_node[node.node_id]
            else:
                slots, _ = ledger.hold_capacity_slots(
                    expected_snapshot_sequence=ledger.snapshot().sequence,
                    requests=(
                        CapacitySlotRequest(
                            permitted_operation=ModelOperation.SPECIALIST_MODEL,
                            allowed_actions=ModelAllowedActions.FINDING_ONLY,
                            reserved_model_calls=1,
                            reserved_tool_calls=1,
                            minimum_token_floor=(
                                specialist_golden.minimum_call_floor_tokens
                            ),
                            expires_at=(
                                replay_case.incident.started_at + timedelta(minutes=5)
                            ),
                        ),
                    ),
                )
                slot_id = slots[0].slot_id
            runtime = SpecialistRuntime(
                ledger=ledger,
                adapter=adapter,
                evidence_store=evidence_store,
                finding_store=finding_store,
                registries=_registries(
                    nodes=(node,),
                    ledger=ledger,
                    case_id=replay_case.case_id,
                    variant=variant,
                    incident=replay_case.incident,
                    evidence_store=evidence_store,
                    phase1_budget=phase1_budget,
                    backend=replay_backend,
                    timeout_seconds=settings.tool_timeout_seconds,
                ),
                dispatch_observer=remember_dispatch,
            )
            outcome = runtime.execute_node(
                SpecialistExecutionContext(
                    schema_version="phase2.specialist-execution-context.v1",
                    admitted_graph=graph,
                    node_id=node.node_id,
                    specialist_capacity_slot_id=slot_id,
                    dependency_finding_ids=tuple(
                        finding_id_by_node[dependency]
                        for dependency in node.depends_on
                    ),
                )
            )
            outcomes.append(outcome)
            finding_id_by_node[node.node_id] = outcome.finding.finding_id

        judge = JudgeRuntime(
            ledger=ledger,
            adapter=adapter,
            evidence_store=evidence_store,
            finding_store=finding_store,
            utc_clock=lambda: replay_case.incident.started_at,
        )
        judged: JudgeOutcome = judge.judge(
            JudgeContext(
                schema_version="phase2.judge-context.v1",
                run_id=run_id,
                incident=replay_case.incident,
                admitted_graph=graph,
                finding_ids=tuple(
                    finding_id_by_node[node.node_id]
                    for node in graph.initial_plan.nodes
                ),
                judge_capacity_slot_id=judge_slot_id,
                allow_refinement=(
                    allow_refinement
                    if variant is Phase2Variant.DYNAMIC_MULTI_AGENT
                    else False
                ),
            )
        )
        if judged.result is None:
            refinement_nodes = tuple(
                node
                for context in judged.refinement_contexts
                for node in judged.admitted_graph.all_nodes
                if node.node_id == context.node_id
            )
            refinement_runtime = SpecialistRuntime(
                ledger=ledger,
                adapter=adapter,
                evidence_store=evidence_store,
                finding_store=finding_store,
                registries=_registries(
                    nodes=refinement_nodes,
                    ledger=ledger,
                    case_id=replay_case.case_id,
                    variant=variant,
                    incident=replay_case.incident,
                    evidence_store=evidence_store,
                    phase1_budget=phase1_budget,
                    backend=replay_backend,
                    timeout_seconds=settings.tool_timeout_seconds,
                ),
                dispatch_observer=remember_dispatch,
            )
            refined = tuple(
                refinement_runtime.execute_node(context)
                for context in judged.refinement_contexts
            )
            outcomes.extend(refined)
            finding_id_by_node.update(
                {
                    outcome.finding.node_id: outcome.finding.finding_id
                    for outcome in refined
                }
            )
            graph = judged.admitted_graph
            judged = judge.finalize(
                tuple(
                    finding_id_by_node[node.node_id]
                    for node in graph.all_nodes
                )
            )
        return WorkflowRunResult(
            trace=_trace(
                ledger=ledger,
                adapter=adapter,
                variant=variant,
                case_id=replay_case.case_id,
                final_rca=judged.result,
                graph=graph,
                specialist_outcomes=tuple(outcomes),
                successful_dispatches=tuple(successful_dispatches),
                terminal_failure_code=ledger.terminal_failure_code,
                terminal_reason=None,
            )
        )
    except Exception as error:
        terminal_failure_code, terminal_reason = _failure_projection(error, ledger)
        return WorkflowRunResult(
            trace=_trace(
                ledger=ledger,
                adapter=adapter,
                variant=variant,
                case_id=replay_case.case_id,
                final_rca=None,
                graph=graph,
                specialist_outcomes=tuple(outcomes),
                successful_dispatches=tuple(successful_dispatches),
                terminal_failure_code=terminal_failure_code,
                terminal_reason=terminal_reason,
            )
        )


def run_replay_workflow(
    *,
    project_root: Path,
    replay_case: ReplayCase,
    variant: Phase2Variant,
    run_id: str | None = None,
    allow_refinement: bool = False,
    model_backend: TypedModelBackend | None = None,
    expected_provider_identity: str = _PROVIDER_ID,
) -> WorkflowRunResult:
    """Run one already-loaded replay case without evaluator or network access."""

    validated_case = ReplayCase.model_validate(replay_case)
    selected_variant = Phase2Variant(variant)
    selected_run_id = run_id or stable_workflow_run_id(
        validated_case.case_id,
        selected_variant,
    )
    now = validated_case.incident.started_at
    ledger = _ledger(
        run_id=selected_run_id,
        case_id=validated_case.case_id,
        variant=selected_variant,
        now=now,
    )
    authority = load_token_authority(Path(project_root))
    if selected_variant is Phase2Variant.SINGLE_AGENT:
        if (
            allow_refinement
            or model_backend is not None
            or expected_provider_identity != _PROVIDER_ID
        ):
            raise ValueError(
                "Single-Agent cannot enable refinement or a Phase 2 backend"
            )
        return _run_single(
            project_root=Path(project_root),
            replay_case=validated_case,
            run_id=selected_run_id,
            ledger=ledger,
            authority=authority,
        )
    return _run_fixed_or_dynamic(
        project_root=Path(project_root),
        replay_case=validated_case,
        run_id=selected_run_id,
        ledger=ledger,
        authority=authority,
        variant=selected_variant,
        allow_refinement=allow_refinement,
        model_backend=model_backend,
        expected_provider_identity=expected_provider_identity,
    )
