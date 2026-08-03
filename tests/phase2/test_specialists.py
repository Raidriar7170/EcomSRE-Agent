"""Focused tests for source-bound one-tool Specialist execution."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from ecomsre.phase1.contracts import (
    ChangesAction,
    EvidenceAttribute,
    EvidenceSource,
    LogsAction,
    MetricsAction,
    ReadOnlyToolName,
    ToolAction,
    ToolCallRecord,
    TracesAction,
)
from ecomsre.phase1.evidence import EvidenceStore
from ecomsre.phase2.budgets import BudgetLedger
from ecomsre.phase2.comparison_adapter import ComparisonAdapter
from ecomsre.phase2.contracts import (
    AdmittedInvestigationGraph,
    CapacitySlotRequest,
    InvestigationNode,
    InvestigationPlan,
    ModelAllowedActions,
    ModelOperation,
    Phase2FailureCode,
    Phase2Variant,
    SpecialistAuthorizationStatus,
    SpecialistRole,
    build_initial_admitted_graph,
)
from ecomsre.phase2.evidence_views import FindingStore
from ecomsre.phase2.scripted import ScriptedModelBackend
from ecomsre.phase2.specialists import (
    SpecialistError,
    SpecialistErrorCode,
    SpecialistExecutionContext,
    SpecialistRuntime,
)
from ecomsre.phase2.token_policy import TokenAuthority, load_token_authority
from ecomsre.phase2.tool_isolation import SpecialistToolRegistry


PROJECT_ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 8, 1, 2, 0, tzinfo=UTC)
END = NOW + timedelta(minutes=5)
EXPIRES = END + timedelta(minutes=5)
RUN_ID = "a" * 32
CASE_ID = "case-001"
INCIDENT_ID = "inc-001"
PROVIDER_ID = "phase2-scripted"
SPECIALIST_KEY = (
    ModelOperation.SPECIALIST_MODEL,
    ModelAllowedActions.FINDING_ONLY,
)

SOURCE_BINDINGS = {
    EvidenceSource.METRICS: (
        SpecialistRole.METRICS_AGENT,
        ReadOnlyToolName.QUERY_METRICS,
        MetricsAction,
    ),
    EvidenceSource.LOGS: (
        SpecialistRole.LOGS_AGENT,
        ReadOnlyToolName.SEARCH_LOGS,
        LogsAction,
    ),
    EvidenceSource.TRACES: (
        SpecialistRole.TRACE_AGENT,
        ReadOnlyToolName.SEARCH_TRACES,
        TracesAction,
    ),
    EvidenceSource.CHANGES: (
        SpecialistRole.CHANGE_AGENT,
        ReadOnlyToolName.LIST_CHANGES,
        ChangesAction,
    ),
}


class DeterministicIds:
    def __init__(self) -> None:
        self._next = 0

    def __call__(self, prefix: str) -> str:
        self._next += 1
        return f"{prefix}-{self._next:04d}"


@pytest.fixture(scope="module")
def authority() -> TokenAuthority:
    return load_token_authority(PROJECT_ROOT)


def node(
    source: EvidenceSource,
    *,
    depends_on: tuple[str, ...] = (),
) -> InvestigationNode:
    role, tool_name, action_type = SOURCE_BINDINGS[source]
    node_id = f"node-{source.value.lower()}-001"
    query = action_type(
        action_type=source.value.lower(),
        started_at=NOW,
        ended_at=END,
        service="checkoutservice",
    )
    return InvestigationNode(
        schema_version="phase2.investigation-node.v1",
        node_id=node_id,
        source=source,
        specialist_role=role,
        tool_name=tool_name,
        query=query,
        depends_on=depends_on,
        objective=f"Inspect bounded {source.value.lower()} observations.",
        query_started_at=NOW,
        query_ended_at=END,
        priority=len(depends_on),
    )


def graph(*nodes: InvestigationNode) -> AdmittedInvestigationGraph:
    return build_initial_admitted_graph(
        InvestigationPlan(
            schema_version="phase2.investigation-plan.v1",
            run_id=RUN_ID,
            incident_id=INCIDENT_ID,
            plan_id="plan-001",
            nodes=nodes,
            planning_rationale="Use bounded read-only source observations.",
            budget_snapshot_id="commander-request-snapshot",
        )
    )


def make_record_executor(
    *,
    selected_node: InvestigationNode,
    store: EvidenceStore,
    calls: list[ToolAction],
    fail: bool,
) -> Callable[[ToolAction], ToolCallRecord]:
    def execute(query: ToolAction) -> ToolCallRecord:
        calls.append(query)
        if fail:
            raise RuntimeError("injected tool failure")
        evidence = store.add(
            source=selected_node.source,
            observation_type="bounded-observation",
            attributes=(EvidenceAttribute(name="value", value=1.0),),
            raw_artifact_ref=f"{selected_node.source.value.lower()}.json#0",
            raw_artifact_sha256="0" * 64,
            limitations=(),
            summary=f"Observed bounded {selected_node.source.value.lower()} signal.",
            started_at=NOW,
            ended_at=END,
            service="checkoutservice",
        )
        return ToolCallRecord(
            schema_version="phase1.tool-call-record.v1",
            call_id=f"tool-call-{selected_node.node_id}",
            run_id=RUN_ID,
            agent_id=selected_node.specialist_role.value,
            incident_id=INCIDENT_ID,
            task_id=selected_node.node_id,
            tool_name=selected_node.tool_name,
            action=query,
            evidence=(evidence,),
            evidence_refs=(evidence.evidence_ref,),
            started_at=NOW,
            ended_at=END,
            monotonic_duration_seconds=0.1,
            budget_consumed=True,
            dispatched=True,
            evidence_quarantined=False,
            usable=True,
            status="OK",
            error_code=None,
        )

    return execute


def harness(
    authority: TokenAuthority,
    admitted_graph: AdmittedInvestigationGraph,
    *,
    variant: Phase2Variant = Phase2Variant.DYNAMIC_MULTI_AGENT,
    fail_role: SpecialistRole | None = None,
    dispatch_observer: Callable[[object, object], None] | None = None,
) -> tuple[
    SpecialistRuntime,
    ScriptedModelBackend,
    BudgetLedger,
    FindingStore,
    dict[str, SpecialistExecutionContext],
    dict[SpecialistRole, list[ToolAction]],
]:
    budget = BudgetLedger(
        run_id=RUN_ID,
        variant=variant,
        case_id=CASE_ID,
        max_model_calls=8,
        max_tool_calls=8,
        max_total_tokens=32_000,
        id_factory=DeterministicIds(),
        monotonic_clock=lambda: 10.0,
        utc_clock=lambda: NOW,
    )
    golden = authority.golden(*SPECIALIST_KEY)
    slots, _ = budget.hold_capacity_slots(
        expected_snapshot_sequence=0,
        requests=tuple(
            CapacitySlotRequest(
                permitted_operation=SPECIALIST_KEY[0],
                allowed_actions=SPECIALIST_KEY[1],
                reserved_model_calls=1,
                reserved_tool_calls=1,
                minimum_token_floor=golden.minimum_call_floor_tokens,
                expires_at=EXPIRES,
            )
            for _ in admitted_graph.all_nodes
        ),
    )
    backend = ScriptedModelBackend(
        token_authority=authority,
        provider_identity=PROVIDER_ID,
    )
    adapter = ComparisonAdapter(
        ledger=budget,
        token_authority=authority,
        backend=backend,
        expected_provider_identity=PROVIDER_ID,
        utc_clock=lambda: NOW,
    )
    evidence_store = EvidenceStore(RUN_ID)
    finding_store = FindingStore(RUN_ID)
    calls: dict[SpecialistRole, list[ToolAction]] = {}
    registries: dict[SpecialistRole, SpecialistToolRegistry] = {}
    for selected_node in admitted_graph.all_nodes:
        role_calls: list[ToolAction] = []
        calls[selected_node.specialist_role] = role_calls
        registries[selected_node.specialist_role] = SpecialistToolRegistry(
            run_id=RUN_ID,
            case_id=CASE_ID,
            variant=variant,
            specialist_role=selected_node.specialist_role,
            ledger=budget,
            executor=make_record_executor(
                selected_node=selected_node,
                store=evidence_store,
                calls=role_calls,
                fail=selected_node.specialist_role is fail_role,
            ),
        )
    contexts = {
        selected_node.node_id: SpecialistExecutionContext(
            schema_version="phase2.specialist-execution-context.v1",
            admitted_graph=admitted_graph,
            node_id=selected_node.node_id,
            specialist_capacity_slot_id=slot.slot_id,
        )
        for selected_node, slot in zip(admitted_graph.all_nodes, slots, strict=True)
    }
    return (
        SpecialistRuntime(
            ledger=budget,
            adapter=adapter,
            evidence_store=evidence_store,
            finding_store=finding_store,
            registries=registries,
            dispatch_observer=dispatch_observer,
        ),
        backend,
        budget,
        finding_store,
        contexts,
        calls,
    )


@pytest.mark.parametrize("variant", (
    Phase2Variant.FIXED_SPECIALIST_WORKFLOW,
    Phase2Variant.DYNAMIC_MULTI_AGENT,
))
@pytest.mark.parametrize("source", tuple(EvidenceSource))
def test_specialist_executes_bound_tool_then_exact_model_once(
    authority: TokenAuthority,
    variant: Phase2Variant,
    source: EvidenceSource,
) -> None:
    selected = node(source)
    runtime, backend, budget, finding_store, contexts, calls = harness(
        authority,
        graph(selected),
        variant=variant,
    )

    outcome = runtime.execute_node(contexts[selected.node_id])

    assert calls[selected.specialist_role] == [selected.query]
    assert backend.calls == 1
    assert outcome.finding.source is source
    assert outcome.finding.specialist_role is selected.specialist_role
    assert outcome.finding.evidence_refs == (
        outcome.dispatch_result.tool_call_record.evidence_refs
    )
    assert finding_store.resolve(outcome.finding.finding_id) == outcome.finding
    assert outcome.authorization.status is SpecialistAuthorizationStatus.COMPLETED
    assert budget.snapshot().charged_tool_calls == 1
    assert budget.snapshot().charged_model_calls == 1
    assert outcome.snapshot == budget.snapshot()


def test_successful_dispatch_is_observed_before_outcome_completion(
    authority: TokenAuthority,
) -> None:
    selected = node(EvidenceSource.METRICS)
    observed: list[tuple[object, object]] = []
    runtime, _, _, _, contexts, _ = harness(
        authority,
        graph(selected),
        dispatch_observer=lambda task, result: observed.append((task, result)),
    )

    outcome = runtime.execute_node(contexts[selected.node_id])

    assert observed == [(outcome.task, outcome.dispatch_result)]


def test_dependency_must_complete_before_dependent_specialist(
    authority: TokenAuthority,
) -> None:
    metrics = node(EvidenceSource.METRICS)
    traces = node(EvidenceSource.TRACES, depends_on=(metrics.node_id,))
    runtime, backend, budget, _, contexts, calls = harness(
        authority,
        graph(metrics, traces),
    )

    with pytest.raises(SpecialistError) as blocked:
        runtime.execute_node(contexts[traces.node_id])
    first = runtime.execute_node(contexts[metrics.node_id])
    second = runtime.execute_node(contexts[traces.node_id])

    assert blocked.value.code is SpecialistErrorCode.DEPENDENCY_NOT_READY
    assert calls[traces.specialist_role] == [traces.query]
    assert backend.calls == 2
    assert budget.snapshot().charged_tool_calls == 2
    assert budget.snapshot().charged_model_calls == 2
    assert second.request.dependency_finding_ids == (first.finding.finding_id,)
    assert second.request.resolved_dependency_evidence_view.evidence == (
        first.dispatch_result.tool_call_record.evidence[0],
    )


def test_tool_failure_is_typed_and_never_calls_model_or_fabricates_finding(
    authority: TokenAuthority,
) -> None:
    selected = node(EvidenceSource.METRICS)
    runtime, backend, budget, _, contexts, calls = harness(
        authority,
        graph(selected),
        fail_role=selected.specialist_role,
    )

    with pytest.raises(SpecialistError) as captured:
        runtime.execute_node(contexts[selected.node_id])

    assert captured.value.code is Phase2FailureCode.TOOL_DISPATCH_FAILED
    assert calls[selected.specialist_role] == [selected.query]
    assert backend.calls == 0
    assert budget.snapshot().charged_tool_calls == 1
    assert budget.snapshot().charged_model_calls == 0
    authorization_id = budget.audit_events()[-1].record_ids[0]
    assert budget.specialist_authorization(authorization_id).status is (
        SpecialistAuthorizationStatus.FAILED
    )


def test_duplicate_node_execution_is_rejected_without_second_charge(
    authority: TokenAuthority,
) -> None:
    selected = node(EvidenceSource.METRICS)
    runtime, backend, budget, _, contexts, calls = harness(
        authority,
        graph(selected),
    )
    runtime.execute_node(contexts[selected.node_id])
    before = budget.snapshot()

    with pytest.raises(SpecialistError) as captured:
        runtime.execute_node(contexts[selected.node_id])

    assert captured.value.code is SpecialistErrorCode.ALREADY_EXECUTED
    assert len(calls[selected.specialist_role]) == 1
    assert backend.calls == 1
    assert budget.snapshot() == before


def test_execution_context_forbids_caller_supplied_budget_snapshot(
    authority: TokenAuthority,
) -> None:
    selected = node(EvidenceSource.METRICS)
    _, _, budget, _, contexts, _ = harness(authority, graph(selected))
    valid = contexts[selected.node_id]

    with pytest.raises(ValidationError):
        SpecialistExecutionContext.model_validate(
            {
                **valid.model_dump(mode="python"),
                "budget_snapshot": budget.snapshot(),
            }
        )


def test_missing_exact_role_registry_fails_before_tool_or_slot_mutation(
    authority: TokenAuthority,
) -> None:
    selected = node(EvidenceSource.METRICS)
    runtime, backend, budget, _, contexts, calls = harness(
        authority,
        graph(selected),
    )
    runtime._registries = {}  # noqa: SLF001 - injected boundary corruption
    before = budget.snapshot()

    with pytest.raises(SpecialistError) as captured:
        runtime.execute_node(contexts[selected.node_id])

    assert captured.value.code is SpecialistErrorCode.INVALID_CONTEXT
    assert calls[selected.specialist_role] == []
    assert backend.calls == 0
    assert budget.snapshot() == before
