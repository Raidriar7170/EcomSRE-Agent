from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier, Event, Lock

import pytest

from ecomsre.phase1.contracts import (
    ChangesAction,
    EvidenceSource,
    LogsAction,
    MetricsAction,
    ReadOnlyToolName,
    ToolCallRecord,
    TracesAction,
)
from ecomsre.phase2.budgets import BudgetLedger, BudgetLedgerError
from ecomsre.phase2.contracts import (
    BudgetOwnerRole,
    CapacitySlotRequest,
    ModelAllowedActions,
    ModelOperation,
    Phase2FailureCode,
    Phase2Variant,
    SpecialistAuthorizationStatus,
    SpecialistRole,
    SpecialistTask,
    SpecialistToolDispatchResult,
)
from ecomsre.phase2.tool_isolation import (
    SpecialistToolRegistry,
    ToolIsolationError,
    ToolIsolationErrorCode,
)
import ecomsre.phase2.tool_isolation as tool_isolation_module


NOW = datetime(2026, 8, 2, 3, 0, tzinfo=UTC)
END = NOW + timedelta(minutes=5)
RUN_ID = "a" * 32
CASE_ID = "case-001"


class DeterministicIds:
    def __init__(self) -> None:
        self._next = 0

    def __call__(self, prefix: str) -> str:
        self._next += 1
        return f"{prefix}-{self._next:04d}"


def ledger(
    variant: Phase2Variant = Phase2Variant.DYNAMIC_MULTI_AGENT,
) -> BudgetLedger:
    return BudgetLedger(
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


def task(**overrides: object) -> SpecialistTask:
    payload: dict[str, object] = {
        "schema_version": "phase2.specialist-task.v1",
        "run_id": RUN_ID,
        "incident_id": "inc-001",
        "plan_id": "plan-001",
        "node_id": "node-metrics-001",
        "source": EvidenceSource.METRICS,
        "specialist_role": SpecialistRole.METRICS_AGENT,
        "tool_name": ReadOnlyToolName.QUERY_METRICS,
        "query": MetricsAction(
            action_type="metrics",
            started_at=NOW,
            ended_at=END,
            service="checkoutservice",
        ),
        "objective": "Determine whether checkout latency increased.",
        "dependency_finding_ids": (),
        "dependency_evidence_refs": (),
        "tool_authorization_id": "placeholder-authorization",
        "model_capacity_slot_id": "placeholder-slot",
    }
    payload.update(overrides)
    return SpecialistTask.model_validate(payload)


def authorized_ledger(
    variant: Phase2Variant = Phase2Variant.DYNAMIC_MULTI_AGENT,
    *,
    specialist_role: SpecialistRole = SpecialistRole.METRICS_AGENT,
    source: EvidenceSource = EvidenceSource.METRICS,
    tool_name: ReadOnlyToolName = ReadOnlyToolName.QUERY_METRICS,
):
    instance = ledger(variant)
    slots, _ = instance.hold_capacity_slots(
        expected_snapshot_sequence=0,
        requests=(
            CapacitySlotRequest(
                permitted_operation=ModelOperation.SPECIALIST_MODEL,
                allowed_actions=ModelAllowedActions.FINDING_ONLY,
                reserved_model_calls=1,
                reserved_tool_calls=1,
                minimum_token_floor=720,
                expires_at=END,
            ),
        ),
    )
    authorization, _ = instance.materialize_specialist_authorization(
        expected_snapshot_sequence=instance.snapshot().sequence,
        slot_id=slots[0].slot_id,
        owner_role=BudgetOwnerRole(specialist_role.value),
        owner_node_id=f"node-{source.value.lower()}-001",
        source=source,
        tool_name=tool_name,
    )
    action_type = source.value.lower()
    action_class = {
        EvidenceSource.METRICS: MetricsAction,
        EvidenceSource.LOGS: LogsAction,
        EvidenceSource.TRACES: TracesAction,
        EvidenceSource.CHANGES: ChangesAction,
    }[source]
    selected_task = task(
        node_id=f"node-{source.value.lower()}-001",
        source=source,
        specialist_role=specialist_role,
        tool_name=tool_name,
        query=action_class(
            action_type=action_type,
            started_at=NOW,
            ended_at=END,
            service="checkoutservice",
        ),
        tool_authorization_id=authorization.authorization_id,
        model_capacity_slot_id=authorization.capacity_slot_id,
    )
    return instance, authorization, selected_task


def record(selected_task: SpecialistTask, **overrides: object) -> ToolCallRecord:
    payload: dict[str, object] = {
        "schema_version": "phase1.tool-call-record.v1",
        "call_id": "tool-call-0001",
        "run_id": selected_task.run_id,
        "agent_id": selected_task.specialist_role.value,
        "incident_id": selected_task.incident_id,
        "task_id": selected_task.node_id,
        "tool_name": selected_task.tool_name,
        "action": selected_task.query,
        "evidence": (),
        "evidence_refs": (),
        "started_at": NOW,
        "ended_at": END,
        "monotonic_duration_seconds": 0.1,
        "budget_consumed": True,
        "dispatched": True,
        "evidence_quarantined": False,
        "usable": True,
        "status": "OK",
        "error_code": None,
    }
    payload.update(overrides)
    return ToolCallRecord.model_validate(payload)


def registry(
    instance: BudgetLedger,
    executor,
    specialist_role: SpecialistRole = SpecialistRole.METRICS_AGENT,
) -> SpecialistToolRegistry:
    return SpecialistToolRegistry(
        run_id=RUN_ID,
        case_id=CASE_ID,
        variant=instance.snapshot().variant,
        specialist_role=specialist_role,
        ledger=instance,
        executor=executor,
    )


def test_tool_isolation_error_code_contract_is_closed() -> None:
    assert tuple(item.value for item in ToolIsolationErrorCode) == (
        "INVALID_REGISTRY",
        "INVALID_TASK",
        "ROLE_MISMATCH",
        "AUTHORIZATION_MISMATCH",
        "RECORD_MISMATCH",
        "EXECUTOR_FAILURE",
    )


@pytest.mark.parametrize(
    "variant",
    (
        Phase2Variant.FIXED_SPECIALIST_WORKFLOW,
        Phase2Variant.DYNAMIC_MULTI_AGENT,
    ),
)
@pytest.mark.parametrize(
    ("specialist_role", "source", "tool_name"),
    (
        (
            SpecialistRole.METRICS_AGENT,
            EvidenceSource.METRICS,
            ReadOnlyToolName.QUERY_METRICS,
        ),
        (
            SpecialistRole.LOGS_AGENT,
            EvidenceSource.LOGS,
            ReadOnlyToolName.SEARCH_LOGS,
        ),
        (
            SpecialistRole.TRACE_AGENT,
            EvidenceSource.TRACES,
            ReadOnlyToolName.SEARCH_TRACES,
        ),
        (
            SpecialistRole.CHANGE_AGENT,
            EvidenceSource.CHANGES,
            ReadOnlyToolName.LIST_CHANGES,
        ),
    ),
)
def test_registry_dispatches_through_live_ledger_and_returns_exact_result(
    variant: Phase2Variant,
    specialist_role: SpecialistRole,
    source: EvidenceSource,
    tool_name: ReadOnlyToolName,
) -> None:
    instance, authorization, selected_task = authorized_ledger(
        variant,
        specialist_role=specialist_role,
        source=source,
        tool_name=tool_name,
    )
    calls: list[object] = []
    expected = record(selected_task)
    result = registry(
        instance,
        lambda query: calls.append(query) or expected,
        specialist_role,
    ).dispatch(selected_task)
    assert isinstance(result, SpecialistToolDispatchResult)
    assert calls == [selected_task.query]
    assert result.tool_call_record == expected
    assert result.specialist_authorization.status is (
        SpecialistAuthorizationStatus.TOOL_CHARGED
    )
    assert instance.snapshot().charged_tool_calls == 1
    assert result.specialist_authorization.authorization_id == (
        authorization.authorization_id
    )


def test_registry_rejects_single_agent_variant_before_executor() -> None:
    instance, _authorization, _selected_task = authorized_ledger(
        Phase2Variant.SINGLE_AGENT
    )
    calls: list[object] = []
    with pytest.raises(ToolIsolationError) as captured:
        registry(instance, lambda query: calls.append(query))
    assert captured.value.code is ToolIsolationErrorCode.INVALID_REGISTRY
    assert calls == []


def test_new_registry_and_stale_task_cannot_redispatch_same_authorization() -> None:
    instance, _authorization, selected_task = authorized_ledger()
    calls = 0

    def execute(_query):
        nonlocal calls
        calls += 1
        return record(selected_task)

    registry(instance, execute).dispatch(selected_task)
    with pytest.raises(ToolIsolationError) as captured:
        registry(instance, execute).dispatch(selected_task)
    assert captured.value.code is ToolIsolationErrorCode.AUTHORIZATION_MISMATCH
    assert captured.value.phase2_failure_code is (
        Phase2FailureCode.BUDGET_SLOT_ALREADY_CONSUMED
    )
    assert calls == 1


def test_concurrent_dispatch_claims_once_and_calls_executor_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    instance, _authorization, selected_task = authorized_ledger()
    claim_entry = Barrier(2)
    counter_lock = Lock()
    calls = 0
    original_claim = instance.claim_specialist_tool_dispatch

    def synchronized_claim(**kwargs):
        claim_entry.wait(timeout=5)
        return original_claim(**kwargs)

    monkeypatch.setattr(
        instance,
        "claim_specialist_tool_dispatch",
        synchronized_claim,
    )

    def execute(_query):
        nonlocal calls
        with counter_lock:
            calls += 1
        return record(selected_task)

    bound = registry(instance, execute)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = tuple(
            pool.submit(bound.dispatch, selected_task) for _ in range(2)
        )
        successes: list[SpecialistToolDispatchResult] = []
        failures: list[ToolIsolationError] = []
        for future in futures:
            try:
                successes.append(future.result(timeout=5))
            except ToolIsolationError as error:
                failures.append(error)
    assert len(successes) == 1
    assert len(failures) == 1
    assert failures[0].code is ToolIsolationErrorCode.AUTHORIZATION_MISMATCH
    assert failures[0].phase2_failure_code is (
        Phase2FailureCode.BUDGET_CAS_CONFLICT
    )
    assert calls == 1
    assert instance.snapshot().charged_tool_calls == 1


@pytest.mark.parametrize("record_mismatch", (False, True))
def test_sealing_error_is_the_direct_failure_cause(
    monkeypatch: pytest.MonkeyPatch,
    record_mismatch: bool,
) -> None:
    instance, _authorization, selected_task = authorized_ledger()
    executor_error = RuntimeError("executor failed")
    sealing_error = BudgetLedgerError(
        Phase2FailureCode.BUDGET_SLOT_ALREADY_CONSUMED,
        "injected sealing failure",
    )

    def fail_sealing(**_kwargs):
        raise sealing_error

    monkeypatch.setattr(instance, "fail_specialist_tool_dispatch", fail_sealing)
    executor = (
        (lambda _query: object())
        if record_mismatch
        else (lambda _query: (_ for _ in ()).throw(executor_error))
    )
    expected_code = (
        ToolIsolationErrorCode.RECORD_MISMATCH
        if record_mismatch
        else ToolIsolationErrorCode.EXECUTOR_FAILURE
    )
    with pytest.raises(ToolIsolationError) as captured:
        registry(instance, executor).dispatch(selected_task)
    assert captured.value.code is expected_code
    assert captured.value.phase2_failure_code is sealing_error.code
    assert captured.value.__cause__ is sealing_error


def test_projection_failure_after_successful_seal_is_local_and_not_retryable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    instance, authorization, selected_task = authorized_ledger()
    calls = 0

    def execute(_query):
        nonlocal calls
        calls += 1
        return record(selected_task)

    def reject_projection(**_kwargs):
        raise ValueError("injected result projection invariant failure")

    monkeypatch.setattr(
        tool_isolation_module,
        "SpecialistToolDispatchResult",
        reject_projection,
    )
    with pytest.raises(ToolIsolationError) as captured:
        registry(instance, execute).dispatch(selected_task)
    assert captured.value.code is ToolIsolationErrorCode.INVALID_REGISTRY
    assert captured.value.phase2_failure_code is None
    assert "outcome already sealed" in str(captured.value)
    assert "must not be redispatched" in str(captured.value)
    assert instance.snapshot().charged_tool_calls == 1
    charged = instance.specialist_authorization(authorization.authorization_id)
    assert charged.tool_call_record_sha256 is not None
    assert charged.dispatch_claim_snapshot_sequence is not None
    receipt = instance.complete_specialist_tool_dispatch(
        authorization_id=charged.authorization_id,
        dispatch_claim_snapshot_sequence=(
            charged.dispatch_claim_snapshot_sequence
        ),
        tool_call_record_sha256=charged.tool_call_record_sha256,
    )
    assert receipt.outcome_snapshot.charged_tool_calls == 1
    with pytest.raises(ToolIsolationError) as retried:
        registry(instance, execute).dispatch(selected_task)
    assert retried.value.code is ToolIsolationErrorCode.AUTHORIZATION_MISMATCH
    assert calls == 1


def test_executor_runs_outside_ledger_lock_and_outcome_ignores_unrelated_sequence() -> None:
    instance, _authorization, selected_task = authorized_ledger()
    entered = Event()
    release = Event()

    def execute(_query):
        entered.set()
        assert release.wait(timeout=5)
        return record(selected_task)

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(registry(instance, execute).dispatch, selected_task)
        assert entered.wait(timeout=5)
        before = instance.snapshot()
        _, unrelated = instance.hold_capacity_slots(
            expected_snapshot_sequence=before.sequence,
            requests=(
                CapacitySlotRequest(
                    permitted_operation=ModelOperation.COMMANDER_MODEL,
                    allowed_actions=ModelAllowedActions.PLAN_ONLY,
                    reserved_model_calls=1,
                    reserved_tool_calls=0,
                    minimum_token_floor=100,
                    expires_at=END,
                ),
            ),
        )
        release.set()
        result = future.result(timeout=5)
    assert unrelated.sequence < result.budget_snapshot.sequence
    assert result.budget_snapshot.charged_tool_calls == 1


@pytest.mark.parametrize(
    ("executor", "code", "failure_kind"),
    (
        (
            lambda _query: (_ for _ in ()).throw(RuntimeError("failed")),
            ToolIsolationErrorCode.EXECUTOR_FAILURE,
            "EXECUTOR_FAILURE",
        ),
        (
            lambda _query: object(),
            ToolIsolationErrorCode.RECORD_MISMATCH,
            "RECORD_MISMATCH",
        ),
    ),
)
def test_post_claim_failure_counts_one_attempt_without_terminalizing(
    executor,
    code: ToolIsolationErrorCode,
    failure_kind: str,
) -> None:
    instance, authorization, selected_task = authorized_ledger()
    with pytest.raises(ToolIsolationError) as captured:
        registry(instance, executor).dispatch(selected_task)
    assert captured.value.code is code
    assert captured.value.phase2_failure_code is (
        Phase2FailureCode.TOOL_DISPATCH_FAILED
    )
    assert instance.snapshot().charged_tool_calls == 1
    assert instance.snapshot().reserved_model_calls == 0
    assert instance.terminal_failure_code is None
    assert instance.specialist_authorization(
        authorization.authorization_id
    ).status is SpecialistAuthorizationStatus.FAILED
    assert instance.audit_events()[-1].failure_code is (
        Phase2FailureCode.TOOL_DISPATCH_FAILED
    )
    with pytest.raises(ToolIsolationError) as retried:
        registry(instance, executor).dispatch(selected_task)
    assert retried.value.phase2_failure_code is (
        Phase2FailureCode.BUDGET_SLOT_ALREADY_CONSUMED
    )
    assert instance.snapshot().charged_tool_calls == 1


def test_terminal_interleaving_seals_claimed_success_and_preserves_terminal_code() -> None:
    instance, _authorization, selected_task = authorized_ledger()
    entered = Event()
    release = Event()

    def execute(_query):
        entered.set()
        assert release.wait(timeout=5)
        return record(selected_task)

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(registry(instance, execute).dispatch, selected_task)
        assert entered.wait(timeout=5)
        instance.record_terminal_failure(
            expected_snapshot_sequence=instance.snapshot().sequence,
            code=Phase2FailureCode.BUDGET_CUMULATIVE_OVERFLOW,
        )
        release.set()
        result = future.result(timeout=5)
    assert result.budget_snapshot.charged_tool_calls == 1
    assert instance.terminal_failure_code is (
        Phase2FailureCode.BUDGET_CUMULATIVE_OVERFLOW
    )
    with pytest.raises(BudgetLedgerError) as blocked:
        instance.hold_capacity_slots(
            expected_snapshot_sequence=instance.snapshot().sequence,
            requests=(),
        )
    assert blocked.value.code is (
        Phase2FailureCode.BUDGET_CUMULATIVE_OVERFLOW
    )


def test_preclaim_scope_failure_has_zero_executor_calls_and_zero_charge() -> None:
    instance, _authorization, selected_task = authorized_ledger()
    calls: list[object] = []
    wrong = selected_task.model_copy(update={"run_id": "b" * 32})
    with pytest.raises(ToolIsolationError) as captured:
        registry(instance, lambda query: calls.append(query)).dispatch(wrong)
    assert captured.value.code is ToolIsolationErrorCode.AUTHORIZATION_MISMATCH
    assert calls == []
    assert instance.snapshot().charged_tool_calls == 0


def test_preclaim_capacity_lineage_mismatch_has_zero_side_effects() -> None:
    instance, _authorization, selected_task = authorized_ledger()
    calls: list[object] = []
    wrong = selected_task.model_copy(
        update={"model_capacity_slot_id": "slot-forged"}
    )
    before = (instance.snapshot(), instance.audit_events())
    with pytest.raises(ToolIsolationError) as captured:
        registry(instance, lambda query: calls.append(query)).dispatch(wrong)
    assert captured.value.code is ToolIsolationErrorCode.AUTHORIZATION_MISMATCH
    assert captured.value.phase2_failure_code is (
        Phase2FailureCode.BUDGET_SLOT_OWNER_MISMATCH
    )
    assert calls == []
    assert (instance.snapshot(), instance.audit_events()) == before


def test_fresh_empty_restart_ledger_for_existing_task_fails_closed() -> None:
    _original, _authorization, selected_task = authorized_ledger()
    restarted = ledger()
    calls: list[object] = []
    with pytest.raises(ToolIsolationError) as captured:
        registry(restarted, lambda query: calls.append(query)).dispatch(selected_task)
    assert captured.value.code is ToolIsolationErrorCode.AUTHORIZATION_MISMATCH
    assert calls == []
