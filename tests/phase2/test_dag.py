from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

import ecomsre.phase2.dag as dag_module
from ecomsre.phase1.contracts import (
    EvidenceSource,
    Incident,
    MetricsAction,
    ReadOnlyToolName,
    Severity,
)
from ecomsre.phase2.budgets import BudgetLedger
from ecomsre.phase2.contracts import (
    AdmittedInvestigationGraph,
    BudgetOwnerRole,
    BudgetSnapshot,
    CapacitySlotRequest,
    InitialDagAdmission,
    InvestigationNode,
    InvestigationPlan,
    ModelAllowedActions,
    ModelOperation,
    Phase2FailureCode,
    Phase2Variant,
    SpecialistRole,
    UnboundCapacitySlot,
)
from ecomsre.phase2.dag import (
    DagAdmissionContext,
    DagValidationError,
    DagValidationErrorCode,
    admit_initial_plan,
    build_initial_admitted_graph,
    schedule_layers,
)
from ecomsre.phase2.tool_isolation import SpecialistToolRegistry


START = datetime(2026, 8, 1, 1, 0, tzinfo=UTC)
END = START + timedelta(minutes=10)
EXPIRES = END + timedelta(minutes=5)
RUN_ID = "a" * 32
INCIDENT_ID = "inc-001"
CASE_ID = "case-001"
COMMANDER_INPUT = 401
COMMANDER_COMPLETION = 101
COMMANDER_FLOOR = COMMANDER_INPUT + COMMANDER_COMPLETION
SPECIALIST_FLOOR = 720
FIRST_JUDGE_FLOOR = 532


@pytest.fixture(autouse=True)
def dispatch_count(
    monkeypatch: pytest.MonkeyPatch,
) -> Callable[[], int]:
    calls = 0

    def counted_dispatch(*_args: object, **_kwargs: object) -> object:
        nonlocal calls
        calls += 1
        raise AssertionError("DAG admission must not dispatch a Specialist tool")

    monkeypatch.setattr(SpecialistToolRegistry, "dispatch", counted_dispatch)
    yield lambda: calls
    assert calls == 0


class DeterministicIds:
    def __init__(self, slot_ids: tuple[str, ...] = ()) -> None:
        self._next = 0
        self._slot_ids = iter(slot_ids)

    def __call__(self, prefix: str) -> str:
        self._next += 1
        if prefix == "slot":
            try:
                return next(self._slot_ids)
            except StopIteration:
                pass
        return f"{prefix}-{self._next:04d}"


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


def node(
    node_id: str,
    *,
    depends_on: tuple[str, ...] = (),
    priority: int = 1,
    started_at: datetime = START,
    ended_at: datetime = END,
) -> InvestigationNode:
    return InvestigationNode(
        schema_version="phase2.investigation-node.v1",
        node_id=node_id,
        source=EvidenceSource.METRICS,
        specialist_role=SpecialistRole.METRICS_AGENT,
        tool_name=ReadOnlyToolName.QUERY_METRICS,
        query=MetricsAction(
            action_type="metrics",
            started_at=started_at,
            ended_at=ended_at,
            service="checkoutservice",
        ),
        depends_on=depends_on,
        objective="Determine whether checkout latency increased.",
        query_started_at=started_at,
        query_ended_at=ended_at,
        priority=priority,
    )


def plan(*nodes: InvestigationNode, **overrides: object) -> InvestigationPlan:
    payload: dict[str, object] = {
        "schema_version": "phase2.investigation-plan.v1",
        "run_id": RUN_ID,
        "incident_id": INCIDENT_ID,
        "plan_id": "plan-001",
        "nodes": nodes or (node("node-001"),),
        "planning_rationale": "Metrics establish whether the alert is real.",
        "budget_snapshot_id": "snapshot-request",
    }
    payload.update(overrides)
    return InvestigationPlan.model_validate(payload)


def capacity_request(
    operation: ModelOperation,
    actions: ModelAllowedActions,
    floor: int,
    *,
    tool_calls: int = 0,
    expires_at: datetime = EXPIRES,
) -> CapacitySlotRequest:
    return CapacitySlotRequest(
        permitted_operation=operation,
        allowed_actions=actions,
        reserved_model_calls=1,
        reserved_tool_calls=tool_calls,
        minimum_token_floor=floor,
        expires_at=expires_at,
    )


def dag_budget_after_commander_charge(
    *,
    slot_ids: tuple[str, ...] = (),
    specialist_floor: int = SPECIALIST_FLOOR,
    first_judge_floor: int = FIRST_JUDGE_FLOOR,
) -> tuple[
    BudgetLedger,
    UnboundCapacitySlot,
    UnboundCapacitySlot,
    UnboundCapacitySlot,
    str,
]:
    budget = BudgetLedger(
        run_id=RUN_ID,
        variant=Phase2Variant.DYNAMIC_MULTI_AGENT,
        case_id=CASE_ID,
        max_model_calls=8,
        max_tool_calls=8,
        max_total_tokens=32_000,
        id_factory=DeterministicIds(slot_ids),
        monotonic_clock=lambda: 10.0,
        utc_clock=lambda: START,
    )
    request_snapshot_id = budget.snapshot().snapshot_id
    slots, _ = budget.initialize_dynamic(
        expected_snapshot_sequence=budget.snapshot().sequence,
        commander=capacity_request(
            ModelOperation.COMMANDER_MODEL,
            ModelAllowedActions.PLAN_ONLY,
            COMMANDER_FLOOR,
        ),
        specialist=capacity_request(
            ModelOperation.SPECIALIST_MODEL,
            ModelAllowedActions.FINDING_ONLY,
            specialist_floor,
            tool_calls=1,
        ),
        first_judge=capacity_request(
            ModelOperation.FIRST_JUDGE_MODEL,
            ModelAllowedActions.FINAL_ONLY,
            first_judge_floor,
        ),
    )
    commander, specialist, judge = slots
    before_expand = budget.snapshot()
    lease, _ = budget.expand_exact_model_lease(
        expected_snapshot_sequence=before_expand.sequence,
        source_record_id=commander.slot_id,
        exact_input_tokens=COMMANDER_INPUT,
        minimum_completion_tokens=COMMANDER_COMPLETION,
        max_completion_tokens=(
            before_expand.remaining_tokens
            + budget.reserved_floor_for(commander.slot_id)
            - COMMANDER_INPUT
        ),
    )
    budget.charge_exact_model_lease(
        expected_snapshot_sequence=budget.snapshot().sequence,
        lease_id=lease.lease_id,
        owner_role=BudgetOwnerRole.INCIDENT_COMMANDER,
        owner_node_id=None,
        source_record_id=commander.slot_id,
        input_tokens=COMMANDER_INPUT,
        output_tokens=COMMANDER_COMPLETION,
        total_tokens=COMMANDER_FLOOR,
    )
    return budget, commander, specialist, judge, request_snapshot_id


def context(
    live: BudgetSnapshot,
    *,
    commander_request_snapshot_id: str,
    **overrides: object,
) -> DagAdmissionContext:
    payload: dict[str, object] = {
        "schema_version": "phase2.dag-admission-context.v2",
        "run_id": RUN_ID,
        "incident": incident(),
        "allowed_started_at": START,
        "allowed_ended_at": END,
        "commander_request_snapshot_id": commander_request_snapshot_id,
        "current_budget_snapshot": live,
    }
    payload.update(overrides)
    return DagAdmissionContext.model_validate(payload)


def admit(
    *,
    budget: BudgetLedger,
    commander: UnboundCapacitySlot,
    bootstrap: UnboundCapacitySlot,
    judge: UnboundCapacitySlot,
    request_snapshot_id: str,
    candidate: InvestigationPlan | None = None,
    admission_context: DagAdmissionContext | None = None,
    specialist_floor_tokens: object = SPECIALIST_FLOOR,
    first_judge_floor_tokens: object = FIRST_JUDGE_FLOOR,
) -> tuple[InitialDagAdmission, BudgetSnapshot]:
    live = budget.snapshot()
    return admit_initial_plan(
        candidate or plan(budget_snapshot_id=request_snapshot_id),
        admission_context
        or context(
            live,
            commander_request_snapshot_id=request_snapshot_id,
        ),
        budget,
        commander_slot_id=commander.slot_id,
        bootstrap_specialist_slot_id=bootstrap.slot_id,
        first_judge_slot_id=judge.slot_id,
        specialist_floor_tokens=specialist_floor_tokens,
        first_judge_floor_tokens=first_judge_floor_tokens,
    )


def test_context_v2_is_required_and_closed() -> None:
    budget, _, _, _, request_snapshot_id = dag_budget_after_commander_charge()
    valid = context(
        budget.snapshot(),
        commander_request_snapshot_id=request_snapshot_id,
    )
    with pytest.raises(ValidationError):
        DagAdmissionContext.model_validate(
            {**valid.model_dump(mode="python"), "schema_version": "phase2.dag-admission-context.v1"}
        )
    with pytest.raises(ValidationError):
        DagAdmissionContext.model_validate(
            {
                key: value
                for key, value in valid.model_dump(mode="python").items()
                if key != "commander_request_snapshot_id"
            }
        )


def test_initial_graph_factory_preserves_declaration_projection_and_hash() -> None:
    candidate = plan(
        node("z", priority=2),
        node("a", priority=1),
        node("dependent", depends_on=("z", "a"), priority=0),
    )
    graph = build_initial_admitted_graph(candidate)
    assert tuple(item.node_id for item in graph.all_nodes) == (
        "z",
        "a",
        "dependent",
    )
    assert graph.dependency_edges == (("z", "dependent"), ("a", "dependent"))
    assert graph == AdmittedInvestigationGraph.model_validate(
        graph.model_dump(mode="python")
    )


def test_binding_order_is_layer_then_priority_then_node_id() -> None:
    layers = schedule_layers(
        plan(
            node("z", priority=2),
            node("a", priority=1),
            node("dependent", depends_on=("z",), priority=0),
        )
    )
    assert tuple(item.node_id for layer in layers for item in layer) == (
        "a",
        "z",
        "dependent",
    )


def test_schedule_layers_delegates_to_the_contract_canonical_helper(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = plan(node("only"))
    expected = ((candidate.nodes[0],),)
    calls: list[InvestigationPlan] = []

    def canonical_helper(value: InvestigationPlan):
        calls.append(value)
        return expected

    monkeypatch.setattr(
        dag_module,
        "_initial_dependency_layers",
        canonical_helper,
        raising=False,
    )
    assert schedule_layers(candidate) == expected
    assert calls == [candidate]


@pytest.mark.parametrize("node_count", (1, 2, 3))
def test_admit_initial_plan_binds_each_node_and_first_judge(
    node_count: int,
) -> None:
    budget, commander, bootstrap, judge, request_snapshot_id = (
        dag_budget_after_commander_charge()
    )
    candidate = plan(
        *(node(f"node-{index}") for index in range(node_count)),
        budget_snapshot_id=request_snapshot_id,
    )
    admission, returned = admit(
        budget=budget,
        commander=commander,
        bootstrap=bootstrap,
        judge=judge,
        request_snapshot_id=request_snapshot_id,
        candidate=candidate,
    )
    assert len(admission.node_slot_bindings) == node_count
    assert admission.first_judge_capacity_slot_id == judge.slot_id
    assert admission.admission_snapshot_id == returned.snapshot_id
    assert admission.admission_snapshot_sequence == returned.sequence
    assert set(returned.active_capacity_slot_ids) == {
        *(item.specialist_capacity_slot_id for item in admission.node_slot_bindings),
        judge.slot_id,
    }


def test_binding_uses_creation_order_not_lexical_slot_order() -> None:
    budget, commander, bootstrap, judge, request_snapshot_id = (
        dag_budget_after_commander_charge(
            slot_ids=("slot-commander", "slot-bootstrap", "slot-judge", "slot-z", "slot-a")
        )
    )
    candidate = plan(
        node("node-z", priority=2),
        node("node-a", priority=1),
        node("node-dependent", depends_on=("node-z",), priority=0),
        budget_snapshot_id=request_snapshot_id,
    )
    admission, _ = admit(
        budget=budget,
        commander=commander,
        bootstrap=bootstrap,
        judge=judge,
        request_snapshot_id=request_snapshot_id,
        candidate=candidate,
    )
    assert tuple(
        (item.node_id, item.specialist_capacity_slot_id)
        for item in admission.node_slot_bindings
    ) == (
        ("node-a", bootstrap.slot_id),
        ("node-z", "slot-z"),
        ("node-dependent", "slot-a"),
    )


def test_runtime_identity_does_not_change_graph_hash_or_graph_serialization() -> None:
    first = dag_budget_after_commander_charge()
    second = dag_budget_after_commander_charge(
        slot_ids=("slot-c2", "slot-s2", "slot-j2", "slot-extra-2")
    )
    admissions: list[InitialDagAdmission] = []
    for budget, commander, bootstrap, judge, request_snapshot_id in (first, second):
        admission, _ = admit(
            budget=budget,
            commander=commander,
            bootstrap=bootstrap,
            judge=judge,
            request_snapshot_id=request_snapshot_id,
            candidate=plan(
                node("one"),
                node("two"),
                budget_snapshot_id=request_snapshot_id,
            ),
        )
        admissions.append(admission)
    assert admissions[0].admitted_graph.graph_sha256 == admissions[1].admitted_graph.graph_sha256
    serialized = admissions[0].admitted_graph.model_dump_json()
    for runtime_name in (
        "node_slot_bindings",
        "specialist_capacity_slot_id",
        "first_judge_capacity_slot_id",
        "admission_snapshot_sequence",
    ):
        assert runtime_name not in serialized


def test_complete_live_snapshot_equality_precedes_sequence_cas() -> None:
    budget, commander, bootstrap, judge, request_snapshot_id = (
        dag_budget_after_commander_charge()
    )
    live = budget.snapshot()
    forged = live.model_copy(
        update={"monotonic_elapsed_seconds": live.monotonic_elapsed_seconds + 1.0}
    )
    before = (budget.snapshot(), budget.audit_events(), budget.capacity_slot_ids())
    with pytest.raises(DagValidationError) as captured:
        admit(
            budget=budget,
            commander=commander,
            bootstrap=bootstrap,
            judge=judge,
            request_snapshot_id=request_snapshot_id,
            admission_context=context(
                forged,
                commander_request_snapshot_id=request_snapshot_id,
            ),
        )
    assert captured.value.code is Phase2FailureCode.BUDGET_CAS_CONFLICT
    assert (budget.snapshot(), budget.audit_events(), budget.capacity_slot_ids()) == before


def test_extra_active_slot_rejects_before_graph_resize_or_dispatch() -> None:
    budget, commander, bootstrap, judge, request_snapshot_id = (
        dag_budget_after_commander_charge()
    )
    budget.hold_capacity_slots(
        expected_snapshot_sequence=budget.snapshot().sequence,
        requests=(
            capacity_request(
                ModelOperation.SPECIALIST_MODEL,
                ModelAllowedActions.FINDING_ONLY,
                SPECIALIST_FLOOR,
                tool_calls=1,
            ),
        ),
    )
    live = budget.snapshot()
    before = (live, budget.audit_events(), budget.capacity_slot_ids())
    with pytest.raises(DagValidationError) as captured:
        admit(
            budget=budget,
            commander=commander,
            bootstrap=bootstrap,
            judge=judge,
            request_snapshot_id=request_snapshot_id,
            admission_context=context(
                live,
                commander_request_snapshot_id=request_snapshot_id,
            ),
        )
    assert captured.value.code is Phase2FailureCode.BUDGET_SLOT_ALREADY_CONSUMED
    assert (budget.snapshot(), budget.audit_events(), budget.capacity_slot_ids()) == before


def test_minimum_capacity_error_identity_is_preserved_without_dispatch() -> None:
    budget, commander, bootstrap, judge, request_snapshot_id = (
        dag_budget_after_commander_charge(specialist_floor=12_000)
    )
    before = (budget.snapshot(), budget.audit_events(), budget.capacity_slot_ids())
    with pytest.raises(DagValidationError) as captured:
        admit(
            budget=budget,
            commander=commander,
            bootstrap=bootstrap,
            judge=judge,
            request_snapshot_id=request_snapshot_id,
            candidate=plan(
                node("one"),
                node("two"),
                node("three"),
                budget_snapshot_id=request_snapshot_id,
            ),
            specialist_floor_tokens=bootstrap.minimum_token_floor,
        )
    assert captured.value.code is Phase2FailureCode.BUDGET_MINIMUM_FLOOR_UNAVAILABLE
    assert (budget.snapshot(), budget.audit_events(), budget.capacity_slot_ids()) == before


def test_commander_request_provenance_is_distinct_from_live_snapshot() -> None:
    budget, commander, bootstrap, judge, request_snapshot_id = (
        dag_budget_after_commander_charge()
    )
    assert request_snapshot_id != budget.snapshot().snapshot_id
    admission, _ = admit(
        budget=budget,
        commander=commander,
        bootstrap=bootstrap,
        judge=judge,
        request_snapshot_id=request_snapshot_id,
    )
    assert admission.admitted_graph.initial_plan.budget_snapshot_id == request_snapshot_id

    other = dag_budget_after_commander_charge()
    other_budget, other_commander, other_bootstrap, other_judge, other_request = other
    before = (other_budget.snapshot(), other_budget.audit_events())
    with pytest.raises(DagValidationError) as captured:
        admit(
            budget=other_budget,
            commander=other_commander,
            bootstrap=other_bootstrap,
            judge=other_judge,
            request_snapshot_id=other_request,
            candidate=plan(budget_snapshot_id="snapshot-wrong"),
        )
    assert captured.value.code is DagValidationErrorCode.STALE_BUDGET_SNAPSHOT
    assert (other_budget.snapshot(), other_budget.audit_events()) == before


@pytest.mark.parametrize("bad_floor", (True, "700", 700.0, 0, -1))
@pytest.mark.parametrize("field", ("specialist", "judge"))
def test_floor_inputs_are_exact_positive_ints(
    bad_floor: object,
    field: str,
) -> None:
    budget, commander, bootstrap, judge, request_snapshot_id = (
        dag_budget_after_commander_charge()
    )
    before = (budget.snapshot(), budget.audit_events(), budget.capacity_slot_ids())
    kwargs = (
        {"specialist_floor_tokens": bad_floor}
        if field == "specialist"
        else {"first_judge_floor_tokens": bad_floor}
    )
    with pytest.raises(DagValidationError):
        admit(
            budget=budget,
            commander=commander,
            bootstrap=bootstrap,
            judge=judge,
            request_snapshot_id=request_snapshot_id,
            **kwargs,
        )
    assert (budget.snapshot(), budget.audit_events(), budget.capacity_slot_ids()) == before


@pytest.mark.parametrize(
    ("mutator", "code"),
    (
        (
            lambda plan_value, context_value: (
                plan_value.model_copy(update={"run_id": "b" * 32}),
                context_value,
            ),
            DagValidationErrorCode.RUN_ID_MISMATCH,
        ),
        (
            lambda plan_value, context_value: (
                plan_value.model_copy(update={"incident_id": "inc-other"}),
                context_value,
            ),
            DagValidationErrorCode.INCIDENT_ID_MISMATCH,
        ),
        (
            lambda plan_value, context_value: (
                plan_value.model_copy(
                    update={
                        "nodes": (
                            node("outside", started_at=START - timedelta(seconds=1)),
                        )
                    }
                ),
                context_value,
            ),
            DagValidationErrorCode.OUTSIDE_ALLOWED_WINDOW,
        ),
    ),
)
def test_admission_retains_context_validation_failures_before_mutation(
    mutator,
    code: DagValidationErrorCode,
) -> None:
    budget, commander, bootstrap, judge, request_snapshot_id = (
        dag_budget_after_commander_charge()
    )
    candidate, admission_context = mutator(
        plan(budget_snapshot_id=request_snapshot_id),
        context(
            budget.snapshot(),
            commander_request_snapshot_id=request_snapshot_id,
        ),
    )
    before = (budget.snapshot(), budget.audit_events(), budget.capacity_slot_ids())
    with pytest.raises(DagValidationError) as captured:
        admit(
            budget=budget,
            commander=commander,
            bootstrap=bootstrap,
            judge=judge,
            request_snapshot_id=request_snapshot_id,
            candidate=candidate,
            admission_context=admission_context,
        )
    assert captured.value.code is code
    assert (budget.snapshot(), budget.audit_events(), budget.capacity_slot_ids()) == before


def test_graph_factory_failure_happens_before_resize(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    budget, commander, bootstrap, judge, request_snapshot_id = (
        dag_budget_after_commander_charge()
    )

    def broken_factory(_: InvestigationPlan) -> AdmittedInvestigationGraph:
        raise ValidationError.from_exception_data("graph", [])

    monkeypatch.setattr(dag_module, "build_initial_admitted_graph", broken_factory)
    before = (budget.snapshot(), budget.audit_events(), budget.capacity_slot_ids())
    with pytest.raises(DagValidationError) as captured:
        admit(
            budget=budget,
            commander=commander,
            bootstrap=bootstrap,
            judge=judge,
            request_snapshot_id=request_snapshot_id,
        )
    assert captured.value.code is DagValidationErrorCode.INVALID_DAG
    assert (budget.snapshot(), budget.audit_events(), budget.capacity_slot_ids()) == before


@pytest.mark.parametrize("corruption", ("projection", "hash"))
def test_injected_graph_projection_or_hash_mismatch_fails_before_resize_or_dispatch(
    corruption: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    budget, commander, bootstrap, judge, request_snapshot_id = (
        dag_budget_after_commander_charge()
    )
    real_factory = dag_module.build_initial_admitted_graph

    def corrupt_graph(candidate: InvestigationPlan) -> AdmittedInvestigationGraph:
        graph = real_factory(candidate)
        if corruption == "projection":
            return graph.model_copy(update={"all_nodes": tuple(reversed(graph.all_nodes))})
        return graph.model_copy(update={"graph_sha256": "0" * 64})

    monkeypatch.setattr(dag_module, "build_initial_admitted_graph", corrupt_graph)
    before = (budget.snapshot(), budget.audit_events(), budget.capacity_slot_ids())
    with pytest.raises(DagValidationError) as captured:
        admit(
            budget=budget,
            commander=commander,
            bootstrap=bootstrap,
            judge=judge,
            request_snapshot_id=request_snapshot_id,
            candidate=plan(
                node("first"),
                node("second"),
                budget_snapshot_id=request_snapshot_id,
            ),
        )
    assert captured.value.code is DagValidationErrorCode.INVALID_DAG
    assert (budget.snapshot(), budget.audit_events(), budget.capacity_slot_ids()) == before


@pytest.mark.parametrize("malformed", ("specialist", "snapshot"))
def test_injected_ledger_return_postconditions_fail_without_dispatch(
    malformed: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    budget, commander, bootstrap, judge, request_snapshot_id = (
        dag_budget_after_commander_charge()
    )
    live = budget.snapshot()

    def fake_resize(**_kwargs: object):
        if malformed == "specialist":
            bad_slot = bootstrap.model_copy(
                update={"minimum_token_floor": bootstrap.minimum_token_floor + 1}
            )
            return (bad_slot,), live
        if malformed == "snapshot":
            bad_snapshot = live.model_copy(update={"active_capacity_slot_ids": ()})
            return (bootstrap,), bad_snapshot
        raise AssertionError("unknown postcondition injection")

    monkeypatch.setattr(budget, "resize_dynamic_initial_plan", fake_resize)

    before = (budget.snapshot(), budget.audit_events(), budget.capacity_slot_ids())
    with pytest.raises(DagValidationError) as captured:
        admit(
            budget=budget,
            commander=commander,
            bootstrap=bootstrap,
            judge=judge,
            request_snapshot_id=request_snapshot_id,
        )
    expected = (
        Phase2FailureCode.BUDGET_CUMULATIVE_OVERFLOW
        if malformed == "snapshot"
        else Phase2FailureCode.BUDGET_SLOT_OWNER_MISMATCH
    )
    assert captured.value.code is expected
    assert (budget.snapshot(), budget.audit_events(), budget.capacity_slot_ids()) == before


def test_injected_judge_shape_fails_at_pre_resize_public_read_without_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    budget, commander, bootstrap, judge, request_snapshot_id = (
        dag_budget_after_commander_charge()
    )
    real_capacity_slot = budget.capacity_slot

    def malformed_judge(slot_id: str) -> UnboundCapacitySlot:
        slot = real_capacity_slot(slot_id)
        if slot_id == judge.slot_id:
            return slot.model_copy(
                update={"minimum_token_floor": slot.minimum_token_floor + 1}
            )
        return slot

    monkeypatch.setattr(budget, "capacity_slot", malformed_judge)
    before = (budget.snapshot(), budget.audit_events(), budget.capacity_slot_ids())
    with pytest.raises(DagValidationError) as captured:
        admit(
            budget=budget,
            commander=commander,
            bootstrap=bootstrap,
            judge=judge,
            request_snapshot_id=request_snapshot_id,
        )
    assert captured.value.code is Phase2FailureCode.BUDGET_SLOT_OWNER_MISMATCH
    assert (budget.snapshot(), budget.audit_events(), budget.capacity_slot_ids()) == before


def test_post_resize_judge_release_does_not_reinterpret_admission_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    budget, commander, bootstrap, judge, request_snapshot_id = (
        dag_budget_after_commander_charge()
    )
    real_resize = budget.resize_dynamic_initial_plan
    resize_snapshots: list[BudgetSnapshot] = []

    def resize_then_release(**kwargs: object):
        slots, admitted_snapshot = real_resize(**kwargs)
        resize_snapshots.append(admitted_snapshot)
        budget.release_capacity_slot(
            expected_snapshot_sequence=budget.snapshot().sequence,
            slot_id=judge.slot_id,
        )
        return slots, admitted_snapshot

    monkeypatch.setattr(budget, "resize_dynamic_initial_plan", resize_then_release)
    admission, returned = admit(
        budget=budget,
        commander=commander,
        bootstrap=bootstrap,
        judge=judge,
        request_snapshot_id=request_snapshot_id,
    )
    assert returned == resize_snapshots[0]
    assert admission.admission_snapshot_id == returned.snapshot_id
    assert admission.admission_snapshot_sequence == returned.sequence
    assert budget.snapshot().sequence == returned.sequence + 1
    assert judge.slot_id not in budget.snapshot().active_capacity_slot_ids


def test_resize_preserves_shared_sequence_cas_error_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    budget, commander, bootstrap, judge, request_snapshot_id = (
        dag_budget_after_commander_charge()
    )
    real_factory = dag_module.build_initial_admitted_graph

    def racing_factory(candidate: InvestigationPlan) -> AdmittedInvestigationGraph:
        graph = real_factory(candidate)
        budget.hold_capacity_slots(
            expected_snapshot_sequence=budget.snapshot().sequence,
            requests=(
                capacity_request(
                    ModelOperation.SPECIALIST_MODEL,
                    ModelAllowedActions.FINDING_ONLY,
                    SPECIALIST_FLOOR,
                    tool_calls=1,
                ),
            ),
        )
        return graph

    monkeypatch.setattr(dag_module, "build_initial_admitted_graph", racing_factory)
    with pytest.raises(DagValidationError) as captured:
        admit(
            budget=budget,
            commander=commander,
            bootstrap=bootstrap,
            judge=judge,
            request_snapshot_id=request_snapshot_id,
        )
    assert captured.value.code is Phase2FailureCode.BUDGET_CAS_CONFLICT


def test_corrupt_copied_contracts_are_normalized_to_stable_dag_errors() -> None:
    corrupt_node = node("corrupt").model_copy(
        update={"query_started_at": "not-a-timestamp"}
    )
    corrupt_plan = plan().model_copy(update={"nodes": (corrupt_node,)})
    with pytest.raises(DagValidationError) as scheduling_error:
        schedule_layers(corrupt_plan)
    assert scheduling_error.value.code is DagValidationErrorCode.INVALID_DAG
