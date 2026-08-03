from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from ecomsre.phase1.contracts import EvidenceSource, ReadOnlyToolName
from ecomsre.phase2.contracts import (
    BudgetLease,
    BudgetLeaseStatus,
    BudgetOwnerRole,
    CapacitySlotRequest,
    CapacitySlotStatus,
    ConditionalRefinementBundleStatus,
    ModelAllowedActions,
    ModelOperation,
    Phase2FailureCode,
    Phase2Variant,
    SpecialistAuthorizationStatus,
    SpecialistExecutionAuthorization,
    SpecialistToolOutcomeKind,
)
from ecomsre.phase2.budgets import BudgetLedger, BudgetLedgerError


NOW = datetime(2026, 8, 1, 2, 0, tzinfo=UTC)
RUN_ID = "a" * 32
OTHER_RUN_ID = "b" * 32
CASE_ID = "case-001"

COMMANDER_INPUT = 401
COMMANDER_MINIMUM_COMPLETION = 101
COMMANDER_FLOOR = COMMANDER_INPUT + COMMANDER_MINIMUM_COMPLETION
SPECIALIST_INPUT = 509
SPECIALIST_MINIMUM_COMPLETION = 211
SPECIALIST_FLOOR = SPECIALIST_INPUT + SPECIALIST_MINIMUM_COMPLETION
FIRST_JUDGE_INPUT = 419
FIRST_JUDGE_MINIMUM_COMPLETION = 113
FIRST_JUDGE_FLOOR = FIRST_JUDGE_INPUT + FIRST_JUDGE_MINIMUM_COMPLETION
FINAL_JUDGE_INPUT = 433
FINAL_JUDGE_MINIMUM_COMPLETION = 109
FINAL_JUDGE_FLOOR = FINAL_JUDGE_INPUT + FINAL_JUDGE_MINIMUM_COMPLETION
TOOL_RECORD_SHA256 = "d" * 64
SPECIALIST_BINDINGS = (
    (
        BudgetOwnerRole.METRICS_AGENT,
        "node-metrics-001",
        EvidenceSource.METRICS,
        ReadOnlyToolName.QUERY_METRICS,
    ),
    (
        BudgetOwnerRole.LOGS_AGENT,
        "node-logs-001",
        EvidenceSource.LOGS,
        ReadOnlyToolName.SEARCH_LOGS,
    ),
    (
        BudgetOwnerRole.TRACE_AGENT,
        "node-traces-001",
        EvidenceSource.TRACES,
        ReadOnlyToolName.SEARCH_TRACES,
    ),
    (
        BudgetOwnerRole.CHANGE_AGENT,
        "node-changes-001",
        EvidenceSource.CHANGES,
        ReadOnlyToolName.LIST_CHANGES,
    ),
)


class DeterministicIds:
    def __init__(self) -> None:
        self._next = 0

    def __call__(self, prefix: str) -> str:
        self._next += 1
        return f"{prefix}-{self._next:04d}"


class ReentrantUtcClock:
    def __init__(self) -> None:
        self.ledger: BudgetLedger | None = None
        self.attempted = False
        self.error_codes: list[Phase2FailureCode] = []

    def __call__(self) -> datetime:
        if self.ledger is not None and not self.attempted:
            self.attempted = True
            try:
                self.ledger.hold_capacity_slots(
                    expected_snapshot_sequence=self.ledger.snapshot().sequence,
                    requests=(commander_request(),),
                )
            except BudgetLedgerError as error:
                self.error_codes.append(error.code)
        return NOW


class ReentrantIds(DeterministicIds):
    def __init__(self) -> None:
        super().__init__()
        self.ledger: BudgetLedger | None = None
        self.attempted = False
        self.error_codes: list[Phase2FailureCode] = []

    def __call__(self, prefix: str) -> str:
        if self.ledger is not None and not self.attempted:
            self.attempted = True
            try:
                self.ledger.hold_capacity_slots(
                    expected_snapshot_sequence=self.ledger.snapshot().sequence,
                    requests=(commander_request(),),
                )
            except BudgetLedgerError as error:
                self.error_codes.append(error.code)
        return super().__call__(prefix)


class ToggleFailureCallback:
    def __init__(self, value: object) -> None:
        self.value = value
        self.fail = False

    def __call__(self, *_args: object) -> object:
        if self.fail:
            raise RuntimeError("injected callback failure")
        return self.value


class ToggleFailureIds(DeterministicIds):
    def __init__(self) -> None:
        super().__init__()
        self.fail = False

    def __call__(self, prefix: str) -> str:
        if self.fail:
            raise RuntimeError("injected ID failure")
        return super().__call__(prefix)


class CountingUtcClock:
    def __init__(self, now: datetime) -> None:
        self.now = now
        self.calls = 0

    def __call__(self) -> datetime:
        self.calls += 1
        return self.now


def ledger(
    *,
    run_id: str = RUN_ID,
    variant: Phase2Variant = Phase2Variant.DYNAMIC_MULTI_AGENT,
    case_id: str = CASE_ID,
    current_time: list[datetime] | None = None,
) -> BudgetLedger:
    clock = current_time if current_time is not None else [NOW]
    return BudgetLedger(
        run_id=run_id,
        variant=variant,
        case_id=case_id,
        max_model_calls=8,
        max_tool_calls=8,
        max_total_tokens=32_000,
        id_factory=DeterministicIds(),
        monotonic_clock=lambda: 10.0,
        utc_clock=lambda: clock[0],
    )


def ledger_with_clock(clock: CountingUtcClock) -> BudgetLedger:
    return BudgetLedger(
        run_id=RUN_ID,
        variant=Phase2Variant.DYNAMIC_MULTI_AGENT,
        case_id=CASE_ID,
        max_model_calls=8,
        max_tool_calls=8,
        max_total_tokens=32_000,
        id_factory=DeterministicIds(),
        monotonic_clock=lambda: 10.0,
        utc_clock=clock,
    )


def request(
    operation: ModelOperation,
    actions: ModelAllowedActions,
    floor: int,
    *,
    tool_calls: int = 0,
    expires_at: datetime | None = None,
) -> CapacitySlotRequest:
    return CapacitySlotRequest(
        permitted_operation=operation,
        allowed_actions=actions,
        reserved_model_calls=1,
        reserved_tool_calls=tool_calls,
        minimum_token_floor=floor,
        expires_at=expires_at or NOW + timedelta(minutes=5),
    )


def specialist_request(
    floor: int = SPECIALIST_FLOOR,
    *,
    expires_at: datetime | None = None,
) -> CapacitySlotRequest:
    return request(
        ModelOperation.SPECIALIST_MODEL,
        ModelAllowedActions.FINDING_ONLY,
        floor,
        tool_calls=1,
        expires_at=expires_at,
    )


def commander_request(floor: int = COMMANDER_FLOOR) -> CapacitySlotRequest:
    return request(
        ModelOperation.COMMANDER_MODEL,
        ModelAllowedActions.PLAN_ONLY,
        floor,
    )


def first_judge_request(
    floor: int = FIRST_JUDGE_FLOOR,
    *,
    conditional: bool = False,
) -> CapacitySlotRequest:
    return request(
        ModelOperation.FIRST_JUDGE_MODEL,
        (
            ModelAllowedActions.FINAL_OR_REFINEMENT
            if conditional
            else ModelAllowedActions.FINAL_ONLY
        ),
        floor,
    )


def final_judge_request(floor: int = FINAL_JUDGE_FLOOR) -> CapacitySlotRequest:
    return request(
        ModelOperation.FINAL_JUDGE_MODEL,
        ModelAllowedActions.FINAL_ONLY,
        floor,
    )


def hold_one(
    instance: BudgetLedger,
    slot_request: CapacitySlotRequest,
    *,
    sequence: int | None = None,
):
    slots, snapshot = instance.hold_capacity_slots(
        expected_snapshot_sequence=(
            instance.snapshot().sequence if sequence is None else sequence
        ),
        requests=(slot_request,),
    )
    return slots[0], snapshot


def materialize_metrics(instance: BudgetLedger, slot_id: str):
    return instance.materialize_specialist_authorization(
        expected_snapshot_sequence=instance.snapshot().sequence,
        slot_id=slot_id,
        owner_role=BudgetOwnerRole.METRICS_AGENT,
        owner_node_id="node-metrics-001",
        source=EvidenceSource.METRICS,
        tool_name=ReadOnlyToolName.QUERY_METRICS,
    )


def charged_metrics_authorization(instance: BudgetLedger):
    slot, _ = hold_one(instance, specialist_request())
    authorization, _ = materialize_metrics(instance, slot.slot_id)
    return claim_and_complete(instance, authorization)[0]


def claim_dispatch(
    instance: BudgetLedger,
    authorization: SpecialistExecutionAuthorization,
):
    return instance.claim_specialist_tool_dispatch(
        expected_snapshot_sequence=instance.snapshot().sequence,
        authorization_id=authorization.authorization_id,
        run_id=authorization.run_id,
        case_id=authorization.case_id,
        variant=authorization.variant,
        owner_role=authorization.owner_role,
        owner_node_id=authorization.owner_node_id,
        source=authorization.source,
        tool_name=authorization.tool_name,
    )


def claim_and_complete(
    instance: BudgetLedger,
    authorization: SpecialistExecutionAuthorization,
    *,
    tool_call_record_sha256: str = TOOL_RECORD_SHA256,
):
    dispatching, _ = claim_dispatch(instance, authorization)
    receipt = instance.complete_specialist_tool_dispatch(
        authorization_id=authorization.authorization_id,
        dispatch_claim_snapshot_sequence=(
            dispatching.dispatch_claim_snapshot_sequence
        ),
        tool_call_record_sha256=tool_call_record_sha256,
    )
    return receipt.post_outcome_authorization, receipt.outcome_snapshot, receipt


def expand(
    instance: BudgetLedger,
    source_record_id: str,
    *,
    exact_input_tokens: int,
    minimum_completion_tokens: int,
):
    snapshot = instance.snapshot()
    max_completion_tokens = (
        snapshot.remaining_tokens
        + instance.reserved_floor_for(source_record_id)
        - exact_input_tokens
    )
    return instance.expand_exact_model_lease(
        expected_snapshot_sequence=snapshot.sequence,
        source_record_id=source_record_id,
        exact_input_tokens=exact_input_tokens,
        minimum_completion_tokens=minimum_completion_tokens,
        max_completion_tokens=max_completion_tokens,
    )


def dynamic_after_commander_charge(
    instance: BudgetLedger,
) -> tuple[object, object, object]:
    slots, _ = instance.initialize_dynamic(
        expected_snapshot_sequence=instance.snapshot().sequence,
        commander=commander_request(),
        specialist=specialist_request(),
        first_judge=first_judge_request(),
    )
    commander, specialist, judge = slots
    exact_lease, _ = expand(
        instance,
        commander.slot_id,
        exact_input_tokens=COMMANDER_INPUT,
        minimum_completion_tokens=COMMANDER_MINIMUM_COMPLETION,
    )
    instance.charge_exact_model_lease(
        expected_snapshot_sequence=instance.snapshot().sequence,
        lease_id=exact_lease.lease_id,
        owner_role=BudgetOwnerRole.INCIDENT_COMMANDER,
        owner_node_id=None,
        source_record_id=commander.slot_id,
        input_tokens=COMMANDER_INPUT,
        output_tokens=COMMANDER_MINIMUM_COMPLETION,
        total_tokens=COMMANDER_FLOOR,
    )
    return commander, specialist, judge


def test_capacity_slot_request_is_strict_and_closed() -> None:
    valid = specialist_request()
    assert valid.minimum_token_floor == SPECIALIST_FLOOR
    for field, value in (
        ("reserved_model_calls", True),
        ("reserved_tool_calls", "1"),
        ("minimum_token_floor", 0),
    ):
        with pytest.raises(ValidationError):
            CapacitySlotRequest.model_validate(
                {**valid.model_dump(mode="python"), field: value}
            )
    with pytest.raises(ValidationError):
        CapacitySlotRequest.model_validate(
            {**valid.model_dump(mode="python"), "owner_role": "METRICS_AGENT"}
        )


def test_hold_capacity_slots_is_ordered_atomic_and_cas_guarded() -> None:
    instance = ledger()
    before = instance.snapshot()
    slots, after = instance.hold_capacity_slots(
        expected_snapshot_sequence=0,
        requests=(commander_request(), specialist_request(), first_judge_request()),
    )
    assert tuple(slot.permitted_operation for slot in slots) == (
        ModelOperation.COMMANDER_MODEL,
        ModelOperation.SPECIALIST_MODEL,
        ModelOperation.FIRST_JUDGE_MODEL,
    )
    assert all(slot.status is CapacitySlotStatus.HELD for slot in slots)
    assert (after.reserved_model_calls, after.reserved_tool_calls) == (3, 1)
    assert after.reserved_tokens == (
        COMMANDER_FLOOR + SPECIALIST_FLOOR + FIRST_JUDGE_FLOOR
    )
    assert after.sequence == before.sequence + 1
    assert after.active_capacity_slot_ids == tuple(slot.slot_id for slot in slots)
    assert len(instance.audit_events()) == 1

    frozen = (instance.snapshot(), instance.audit_events())
    with pytest.raises(BudgetLedgerError) as captured:
        instance.hold_capacity_slots(
            expected_snapshot_sequence=0,
            requests=(specialist_request(),),
        )
    assert captured.value.code is Phase2FailureCode.BUDGET_CAS_CONFLICT
    assert (instance.snapshot(), instance.audit_events()) == frozen


@pytest.mark.parametrize("invalid_sequence", (True, "0", -1))
def test_every_cas_rejects_non_exact_sequence_without_mutation(
    invalid_sequence: object,
) -> None:
    instance = ledger()
    before = (instance.snapshot(), instance.audit_events())
    with pytest.raises(BudgetLedgerError) as captured:
        instance.hold_capacity_slots(
            expected_snapshot_sequence=invalid_sequence,  # type: ignore[arg-type]
            requests=(specialist_request(),),
        )
    assert captured.value.code is Phase2FailureCode.BUDGET_CAS_CONFLICT
    assert (instance.snapshot(), instance.audit_events()) == before


def test_hold_failure_rolls_back_records_counts_audit_and_sequence() -> None:
    instance = ledger()
    before = (instance.snapshot(), instance.audit_events())
    with pytest.raises(BudgetLedgerError) as captured:
        instance.hold_capacity_slots(
            expected_snapshot_sequence=0,
            requests=(
                commander_request(26_500),
                specialist_request(5_501),
            ),
        )
    assert captured.value.code is Phase2FailureCode.BUDGET_MINIMUM_FLOOR_UNAVAILABLE
    assert (instance.snapshot(), instance.audit_events()) == before
    assert instance.capacity_slot_ids() == ()


def test_specialist_slot_materializes_once_into_owner_bound_authorization() -> None:
    instance = ledger()
    slot, _ = hold_one(instance, specialist_request())
    authorization, snapshot = materialize_metrics(instance, slot.slot_id)
    assert authorization.status is SpecialistAuthorizationStatus.TOOL_AUTHORIZED
    assert authorization.capacity_slot_id == slot.slot_id
    assert authorization.owner_role is BudgetOwnerRole.METRICS_AGENT
    assert authorization.owner_node_id == "node-metrics-001"
    assert snapshot.reserved_model_calls == 1
    assert snapshot.reserved_tool_calls == 1
    assert slot.slot_id not in snapshot.active_capacity_slot_ids
    assert snapshot.active_specialist_authorization_ids == (
        authorization.authorization_id,
    )
    assert instance.capacity_slot(slot.slot_id).status is CapacitySlotStatus.MATERIALIZED

    before = (instance.snapshot(), instance.audit_events())
    with pytest.raises(BudgetLedgerError) as captured:
        instance.materialize_specialist_authorization(
            expected_snapshot_sequence=snapshot.sequence,
            slot_id=slot.slot_id,
            owner_role=BudgetOwnerRole.LOGS_AGENT,
            owner_node_id="node-logs-001",
            source=EvidenceSource.LOGS,
            tool_name=ReadOnlyToolName.SEARCH_LOGS,
        )
    assert captured.value.code is Phase2FailureCode.BUDGET_SLOT_ALREADY_CONSUMED
    assert (instance.snapshot(), instance.audit_events()) == before


@pytest.mark.parametrize(
    ("owner_role", "owner_node_id", "source", "tool_name"),
    (
        (
            BudgetOwnerRole.LOGS_AGENT,
            "node-metrics-001",
            EvidenceSource.METRICS,
            ReadOnlyToolName.QUERY_METRICS,
        ),
        (
            BudgetOwnerRole.METRICS_AGENT,
            "node-metrics-001",
            EvidenceSource.LOGS,
            ReadOnlyToolName.SEARCH_LOGS,
        ),
        (
            BudgetOwnerRole.METRICS_AGENT,
            "node-metrics-001",
            EvidenceSource.METRICS,
            ReadOnlyToolName.SEARCH_LOGS,
        ),
    ),
)
def test_specialist_materialization_rejects_role_source_tool_transfer(
    owner_role: BudgetOwnerRole,
    owner_node_id: str,
    source: EvidenceSource,
    tool_name: ReadOnlyToolName,
) -> None:
    instance = ledger()
    slot, _ = hold_one(instance, specialist_request())
    before = (instance.snapshot(), instance.audit_events())
    with pytest.raises(BudgetLedgerError) as captured:
        instance.materialize_specialist_authorization(
            expected_snapshot_sequence=instance.snapshot().sequence,
            slot_id=slot.slot_id,
            owner_role=owner_role,
            owner_node_id=owner_node_id,
            source=source,
            tool_name=tool_name,
        )
    assert captured.value.code is Phase2FailureCode.BUDGET_SLOT_OWNER_MISMATCH
    assert (instance.snapshot(), instance.audit_events()) == before


def test_cross_run_variant_and_case_slot_ids_are_not_transferable() -> None:
    origin = ledger()
    slot, _ = hold_one(origin, specialist_request())
    targets = (
        ledger(run_id=OTHER_RUN_ID),
        ledger(variant=Phase2Variant.FIXED_SPECIALIST_WORKFLOW),
        ledger(case_id="case-002"),
    )
    for target in targets:
        before = (target.snapshot(), target.audit_events())
        with pytest.raises(BudgetLedgerError) as captured:
            target.materialize_specialist_authorization(
                expected_snapshot_sequence=0,
                slot_id=slot.slot_id,
                owner_role=BudgetOwnerRole.METRICS_AGENT,
                owner_node_id="node-metrics-001",
                source=EvidenceSource.METRICS,
                tool_name=ReadOnlyToolName.QUERY_METRICS,
            )
        assert captured.value.code is Phase2FailureCode.BUDGET_SLOT_STALE
        assert (target.snapshot(), target.audit_events()) == before


def test_specialist_authorization_closed_happy_path() -> None:
    instance = ledger()
    slot, _ = hold_one(instance, specialist_request())
    authorized, _ = materialize_metrics(instance, slot.slot_id)
    charged, charged_snapshot, receipt = claim_and_complete(instance, authorized)
    assert charged.status is SpecialistAuthorizationStatus.TOOL_CHARGED
    assert receipt.outcome_kind is SpecialistToolOutcomeKind.SUCCESS
    assert charged.tool_charged_snapshot_sequence == charged_snapshot.sequence
    assert charged.tool_call_record_sha256 == TOOL_RECORD_SHA256
    assert charged_snapshot.charged_tool_calls == 1
    assert charged_snapshot.reserved_tool_calls == 0

    lease, leased_snapshot = expand(
        instance,
        charged.authorization_id,
        exact_input_tokens=SPECIALIST_INPUT,
        minimum_completion_tokens=SPECIALIST_MINIMUM_COMPLETION,
    )
    assert lease.status is BudgetLeaseStatus.RESERVED
    assert lease.source_record_id == charged.authorization_id
    assert lease.reserved_model_calls == 1
    assert lease.reserved_tool_calls == 0
    assert lease.reserved_tokens == lease.exact_input_tokens + lease.max_completion_tokens
    assert instance.specialist_authorization(
        charged.authorization_id
    ).status is SpecialistAuthorizationStatus.MODEL_LEASED
    assert leased_snapshot.active_lease_ids == (lease.lease_id,)

    charged_lease, model_snapshot = instance.charge_exact_model_lease(
        expected_snapshot_sequence=instance.snapshot().sequence,
        lease_id=lease.lease_id,
        owner_role=BudgetOwnerRole.METRICS_AGENT,
        owner_node_id="node-metrics-001",
        source_record_id=charged.authorization_id,
        input_tokens=SPECIALIST_INPUT,
        output_tokens=SPECIALIST_MINIMUM_COMPLETION,
        total_tokens=SPECIALIST_FLOOR,
    )
    assert charged_lease.status is BudgetLeaseStatus.CHARGED
    assert model_snapshot.charged_model_calls == 1
    assert model_snapshot.cumulative_tokens == SPECIALIST_FLOOR

    completed, completed_snapshot = instance.complete_specialist_authorization(
        expected_snapshot_sequence=instance.snapshot().sequence,
        authorization_id=charged.authorization_id,
    )
    assert completed.status is SpecialistAuthorizationStatus.COMPLETED
    assert completed_snapshot.active_specialist_authorization_ids == ()


def test_tool_outcome_is_idempotent_and_returns_exact_frozen_receipt() -> None:
    instance = ledger()
    slot, _ = hold_one(instance, specialist_request())
    authorization, _ = materialize_metrics(instance, slot.slot_id)
    dispatching, _ = claim_dispatch(instance, authorization)
    first = instance.complete_specialist_tool_dispatch(
        authorization_id=authorization.authorization_id,
        dispatch_claim_snapshot_sequence=(
            dispatching.dispatch_claim_snapshot_sequence
        ),
        tool_call_record_sha256=TOOL_RECORD_SHA256,
    )
    duplicate = instance.complete_specialist_tool_dispatch(
        authorization_id=authorization.authorization_id,
        dispatch_claim_snapshot_sequence=(
            dispatching.dispatch_claim_snapshot_sequence
        ),
        tool_call_record_sha256=TOOL_RECORD_SHA256,
    )
    assert duplicate == first
    assert duplicate.outcome_snapshot == first.outcome_snapshot
    assert len(instance.audit_events()) == 4


@pytest.mark.parametrize(
    "variant",
    (
        Phase2Variant.FIXED_SPECIALIST_WORKFLOW,
        Phase2Variant.DYNAMIC_MULTI_AGENT,
    ),
)
@pytest.mark.parametrize(
    ("owner_role", "owner_node_id", "source", "tool_name"),
    SPECIALIST_BINDINGS,
)
def test_all_explicit_role_mappings_claim_and_charge_once(
    variant: Phase2Variant,
    owner_role: BudgetOwnerRole,
    owner_node_id: str,
    source: EvidenceSource,
    tool_name: ReadOnlyToolName,
) -> None:
    instance = ledger(variant=variant)
    slot, _ = hold_one(instance, specialist_request())
    authorization, _ = instance.materialize_specialist_authorization(
        expected_snapshot_sequence=instance.snapshot().sequence,
        slot_id=slot.slot_id,
        owner_role=owner_role,
        owner_node_id=owner_node_id,
        source=source,
        tool_name=tool_name,
    )
    dispatching, claim_snapshot = claim_dispatch(instance, authorization)
    assert dispatching.status is SpecialistAuthorizationStatus.TOOL_DISPATCHING
    assert dispatching.dispatch_claim_snapshot_sequence == claim_snapshot.sequence
    assert claim_snapshot.active_specialist_authorization_ids == (
        authorization.authorization_id,
    )
    receipt = instance.complete_specialist_tool_dispatch(
        authorization_id=authorization.authorization_id,
        dispatch_claim_snapshot_sequence=claim_snapshot.sequence,
        tool_call_record_sha256=TOOL_RECORD_SHA256,
    )
    assert receipt.outcome_snapshot.charged_tool_calls == 1


@pytest.mark.parametrize(
    ("field", "value", "expected"),
    (
        ("run_id", OTHER_RUN_ID, Phase2FailureCode.BUDGET_SLOT_OWNER_MISMATCH),
        ("case_id", "case-other", Phase2FailureCode.BUDGET_SLOT_OWNER_MISMATCH),
        (
            "variant",
            Phase2Variant.FIXED_SPECIALIST_WORKFLOW,
            Phase2FailureCode.BUDGET_SLOT_OWNER_MISMATCH,
        ),
        (
            "owner_role",
            BudgetOwnerRole.LOGS_AGENT,
            Phase2FailureCode.BUDGET_SLOT_OWNER_MISMATCH,
        ),
        ("owner_node_id", "node-other", Phase2FailureCode.BUDGET_SLOT_OWNER_MISMATCH),
        ("source", EvidenceSource.LOGS, Phase2FailureCode.BUDGET_SLOT_OWNER_MISMATCH),
        (
            "tool_name",
            ReadOnlyToolName.SEARCH_LOGS,
            Phase2FailureCode.BUDGET_SLOT_OWNER_MISMATCH,
        ),
    ),
)
def test_claim_rejects_scope_or_binding_mismatch_without_mutation(
    field: str,
    value: object,
    expected: Phase2FailureCode,
) -> None:
    instance = ledger()
    slot, _ = hold_one(instance, specialist_request())
    authorization, _ = materialize_metrics(instance, slot.slot_id)
    kwargs: dict[str, object] = {
        "expected_snapshot_sequence": instance.snapshot().sequence,
        "authorization_id": authorization.authorization_id,
        "run_id": authorization.run_id,
        "case_id": authorization.case_id,
        "variant": authorization.variant,
        "owner_role": authorization.owner_role,
        "owner_node_id": authorization.owner_node_id,
        "source": authorization.source,
        "tool_name": authorization.tool_name,
    }
    kwargs[field] = value
    before = (instance.snapshot(), instance.audit_events())
    with pytest.raises(BudgetLedgerError) as captured:
        instance.claim_specialist_tool_dispatch(**kwargs)  # type: ignore[arg-type]
    assert captured.value.code is expected
    assert (instance.snapshot(), instance.audit_events()) == before


def test_claim_is_global_cas_but_outcome_is_claim_scoped() -> None:
    instance = ledger()
    slot, _ = hold_one(instance, specialist_request())
    authorization, _ = materialize_metrics(instance, slot.slot_id)
    stale_sequence = instance.snapshot().sequence - 1
    before = (instance.snapshot(), instance.audit_events())
    with pytest.raises(BudgetLedgerError) as stale:
        instance.claim_specialist_tool_dispatch(
            expected_snapshot_sequence=stale_sequence,
            authorization_id=authorization.authorization_id,
            run_id=authorization.run_id,
            case_id=authorization.case_id,
            variant=authorization.variant,
            owner_role=authorization.owner_role,
            owner_node_id=authorization.owner_node_id,
            source=authorization.source,
            tool_name=authorization.tool_name,
        )
    assert stale.value.code is Phase2FailureCode.BUDGET_CAS_CONFLICT
    assert (instance.snapshot(), instance.audit_events()) == before

    dispatching, claim_snapshot = claim_dispatch(instance, authorization)
    hold_one(instance, commander_request())
    receipt = instance.complete_specialist_tool_dispatch(
        authorization_id=authorization.authorization_id,
        dispatch_claim_snapshot_sequence=(
            dispatching.dispatch_claim_snapshot_sequence
        ),
        tool_call_record_sha256=TOOL_RECORD_SHA256,
    )
    assert receipt.outcome_snapshot.sequence > claim_snapshot.sequence + 1
    assert receipt.outcome_snapshot.charged_tool_calls == 1


def test_failed_dispatch_counts_one_attempt_releases_only_its_floor_and_is_idempotent() -> None:
    instance = ledger()
    slot, _ = hold_one(instance, specialist_request())
    authorization, _ = materialize_metrics(instance, slot.slot_id)
    dispatching, _ = claim_dispatch(instance, authorization)
    receipt = instance.fail_specialist_tool_dispatch(
        authorization_id=authorization.authorization_id,
        dispatch_claim_snapshot_sequence=(
            dispatching.dispatch_claim_snapshot_sequence
        ),
        failure_kind="EXECUTOR_FAILURE",
    )
    assert receipt.outcome_kind is SpecialistToolOutcomeKind.FAILURE
    assert receipt.failure_code is Phase2FailureCode.TOOL_DISPATCH_FAILED
    assert receipt.post_outcome_authorization.status is (
        SpecialistAuthorizationStatus.FAILED
    )
    assert receipt.outcome_snapshot.charged_tool_calls == 1
    assert receipt.outcome_snapshot.reserved_model_calls == 0
    assert instance.terminal_failure_code is None
    assert instance.audit_events()[-1].failure_code is (
        Phase2FailureCode.TOOL_DISPATCH_FAILED
    )
    hold_one(instance, commander_request())
    before = (instance.snapshot(), instance.audit_events())
    duplicate = instance.fail_specialist_tool_dispatch(
        authorization_id=authorization.authorization_id,
        dispatch_claim_snapshot_sequence=(
            dispatching.dispatch_claim_snapshot_sequence
        ),
        failure_kind="EXECUTOR_FAILURE",
    )
    assert duplicate == receipt
    assert duplicate.outcome_snapshot.sequence < instance.snapshot().sequence
    assert (instance.snapshot(), instance.audit_events()) == before


@pytest.mark.parametrize("failure_kind", ([], object(), 1, True))
def test_failed_dispatch_rejects_nonexact_failure_kind_with_stable_error(
    failure_kind: object,
) -> None:
    instance = ledger()
    slot, _ = hold_one(instance, specialist_request())
    authorization, _ = materialize_metrics(instance, slot.slot_id)
    dispatching, _ = claim_dispatch(instance, authorization)
    before = (instance.snapshot(), instance.audit_events())
    with pytest.raises(BudgetLedgerError) as captured:
        instance.fail_specialist_tool_dispatch(
            authorization_id=authorization.authorization_id,
            dispatch_claim_snapshot_sequence=(
                dispatching.dispatch_claim_snapshot_sequence
            ),
            failure_kind=failure_kind,  # type: ignore[arg-type]
        )
    assert captured.value.code is Phase2FailureCode.BUDGET_SLOT_OWNER_MISMATCH
    assert (instance.snapshot(), instance.audit_events()) == before


def test_outcome_conflicts_never_increment_twice() -> None:
    instance = ledger()
    slot, _ = hold_one(instance, specialist_request())
    authorization, _ = materialize_metrics(instance, slot.slot_id)
    dispatching, _ = claim_dispatch(instance, authorization)
    claim_sequence = dispatching.dispatch_claim_snapshot_sequence
    instance.complete_specialist_tool_dispatch(
        authorization_id=authorization.authorization_id,
        dispatch_claim_snapshot_sequence=claim_sequence,
        tool_call_record_sha256=TOOL_RECORD_SHA256,
    )
    for outcome in (
        lambda: instance.complete_specialist_tool_dispatch(
            authorization_id=authorization.authorization_id,
            dispatch_claim_snapshot_sequence=claim_sequence,
            tool_call_record_sha256="e" * 64,
        ),
        lambda: instance.complete_specialist_tool_dispatch(
            authorization_id=authorization.authorization_id,
            dispatch_claim_snapshot_sequence=claim_sequence + 1,
            tool_call_record_sha256=TOOL_RECORD_SHA256,
        ),
        lambda: instance.fail_specialist_tool_dispatch(
            authorization_id=authorization.authorization_id,
            dispatch_claim_snapshot_sequence=claim_sequence,
            failure_kind="RECORD_MISMATCH",
        ),
    ):
        before = (instance.snapshot(), instance.audit_events())
        with pytest.raises(BudgetLedgerError) as captured:
            outcome()
        assert captured.value.code is Phase2FailureCode.BUDGET_SLOT_ALREADY_CONSUMED
        assert (instance.snapshot(), instance.audit_events()) == before
        assert instance.snapshot().charged_tool_calls == 1


def test_duplicate_success_receipt_survives_later_model_leased_state() -> None:
    instance = ledger()
    slot, _ = hold_one(instance, specialist_request())
    authorization, _ = materialize_metrics(instance, slot.slot_id)
    charged, _snapshot, receipt = claim_and_complete(instance, authorization)
    expand(
        instance,
        charged.authorization_id,
        exact_input_tokens=SPECIALIST_INPUT,
        minimum_completion_tokens=SPECIALIST_MINIMUM_COMPLETION,
    )
    duplicate = instance.complete_specialist_tool_dispatch(
        authorization_id=charged.authorization_id,
        dispatch_claim_snapshot_sequence=(
            charged.dispatch_claim_snapshot_sequence
        ),
        tool_call_record_sha256=TOOL_RECORD_SHA256,
    )
    assert instance.specialist_authorization(charged.authorization_id).status is (
        SpecialistAuthorizationStatus.MODEL_LEASED
    )
    assert duplicate == receipt
    assert duplicate.post_outcome_authorization.status is (
        SpecialistAuthorizationStatus.TOOL_CHARGED
    )


def test_duplicate_success_receipt_survives_completed_successor() -> None:
    instance = ledger()
    slot, _ = hold_one(instance, specialist_request())
    authorization, _ = materialize_metrics(instance, slot.slot_id)
    charged, _snapshot, receipt = claim_and_complete(instance, authorization)
    exact_lease, _ = expand(
        instance,
        charged.authorization_id,
        exact_input_tokens=SPECIALIST_INPUT,
        minimum_completion_tokens=SPECIALIST_MINIMUM_COMPLETION,
    )
    instance.charge_exact_model_lease(
        expected_snapshot_sequence=instance.snapshot().sequence,
        lease_id=exact_lease.lease_id,
        owner_role=BudgetOwnerRole.METRICS_AGENT,
        owner_node_id="node-metrics-001",
        source_record_id=charged.authorization_id,
        input_tokens=SPECIALIST_INPUT,
        output_tokens=SPECIALIST_MINIMUM_COMPLETION,
        total_tokens=SPECIALIST_FLOOR,
    )
    instance.complete_specialist_authorization(
        expected_snapshot_sequence=instance.snapshot().sequence,
        authorization_id=charged.authorization_id,
    )
    duplicate = instance.complete_specialist_tool_dispatch(
        authorization_id=charged.authorization_id,
        dispatch_claim_snapshot_sequence=(
            charged.dispatch_claim_snapshot_sequence
        ),
        tool_call_record_sha256=TOOL_RECORD_SHA256,
    )
    assert instance.specialist_authorization(charged.authorization_id).status is (
        SpecialistAuthorizationStatus.COMPLETED
    )
    assert duplicate == receipt


def test_duplicate_success_receipt_survives_provenance_retaining_failed_successor() -> None:
    instance = ledger()
    slot, _ = hold_one(instance, specialist_request())
    authorization, _ = materialize_metrics(instance, slot.slot_id)
    charged, _snapshot, receipt = claim_and_complete(instance, authorization)
    failed, _ = instance.fail_specialist_authorization(
        expected_snapshot_sequence=instance.snapshot().sequence,
        authorization_id=charged.authorization_id,
    )
    assert failed.status is SpecialistAuthorizationStatus.FAILED
    assert failed.tool_call_record_sha256 == TOOL_RECORD_SHA256
    duplicate = instance.complete_specialist_tool_dispatch(
        authorization_id=charged.authorization_id,
        dispatch_claim_snapshot_sequence=(
            charged.dispatch_claim_snapshot_sequence
        ),
        tool_call_record_sha256=TOOL_RECORD_SHA256,
    )
    assert duplicate == receipt


def test_claim_expiry_fails_atomically_but_claimed_outcome_seals_after_expiry() -> None:
    current_time = [NOW]
    expired_before_claim = ledger(current_time=current_time)
    slot, _ = hold_one(
        expired_before_claim,
        specialist_request(expires_at=NOW + timedelta(seconds=1)),
    )
    authorization, _ = materialize_metrics(expired_before_claim, slot.slot_id)
    current_time[0] = authorization.expires_at
    before = (
        expired_before_claim.snapshot(),
        expired_before_claim.audit_events(),
    )
    with pytest.raises(BudgetLedgerError) as captured:
        claim_dispatch(expired_before_claim, authorization)
    assert captured.value.code is Phase2FailureCode.BUDGET_SLOT_STALE
    assert (
        expired_before_claim.snapshot(),
        expired_before_claim.audit_events(),
    ) == before

    in_flight_time = [NOW]
    in_flight = ledger(current_time=in_flight_time)
    in_flight_slot, _ = hold_one(
        in_flight,
        specialist_request(expires_at=NOW + timedelta(seconds=1)),
    )
    in_flight_authorization, _ = materialize_metrics(
        in_flight, in_flight_slot.slot_id
    )
    dispatching, _ = claim_dispatch(in_flight, in_flight_authorization)
    in_flight_time[0] = in_flight_authorization.expires_at
    receipt = in_flight.complete_specialist_tool_dispatch(
        authorization_id=in_flight_authorization.authorization_id,
        dispatch_claim_snapshot_sequence=(
            dispatching.dispatch_claim_snapshot_sequence
        ),
        tool_call_record_sha256=TOOL_RECORD_SHA256,
    )
    assert receipt.outcome_kind is SpecialistToolOutcomeKind.SUCCESS


def test_terminal_interleaving_seals_outcome_and_preserves_original_code() -> None:
    instance = ledger()
    slot, _ = hold_one(instance, specialist_request())
    authorization, _ = materialize_metrics(instance, slot.slot_id)
    dispatching, _ = claim_dispatch(instance, authorization)
    instance.record_terminal_failure(
        expected_snapshot_sequence=instance.snapshot().sequence,
        code=Phase2FailureCode.BUDGET_CUMULATIVE_OVERFLOW,
    )
    receipt = instance.fail_specialist_tool_dispatch(
        authorization_id=authorization.authorization_id,
        dispatch_claim_snapshot_sequence=(
            dispatching.dispatch_claim_snapshot_sequence
        ),
        failure_kind="RECORD_MISMATCH",
    )
    assert receipt.outcome_snapshot.charged_tool_calls == 1
    assert instance.terminal_failure_code is (
        Phase2FailureCode.BUDGET_CUMULATIVE_OVERFLOW
    )
    with pytest.raises(BudgetLedgerError) as blocked:
        instance.hold_capacity_slots(
            expected_snapshot_sequence=instance.snapshot().sequence,
            requests=(commander_request(),),
        )
    assert blocked.value.code is Phase2FailureCode.BUDGET_CUMULATIVE_OVERFLOW


def test_exact_expansion_requires_charged_specialist_tool() -> None:
    instance = ledger()
    slot, _ = hold_one(instance, specialist_request())
    authorization, _ = materialize_metrics(instance, slot.slot_id)
    before = (instance.snapshot(), instance.audit_events())
    with pytest.raises(BudgetLedgerError) as captured:
        expand(
            instance,
            authorization.authorization_id,
            exact_input_tokens=SPECIALIST_INPUT,
            minimum_completion_tokens=SPECIALIST_MINIMUM_COMPLETION,
        )
    assert captured.value.code is Phase2FailureCode.BUDGET_SLOT_ALREADY_CONSUMED
    assert (instance.snapshot(), instance.audit_events()) == before


def test_non_specialist_slot_expands_to_exact_owner_bound_lease() -> None:
    instance = ledger()
    slot, _ = hold_one(instance, commander_request())
    lease, snapshot = expand(
        instance,
        slot.slot_id,
        exact_input_tokens=COMMANDER_INPUT,
        minimum_completion_tokens=COMMANDER_MINIMUM_COMPLETION,
    )
    assert lease.owner_role is BudgetOwnerRole.INCIDENT_COMMANDER
    assert lease.owner_node_id is None
    assert lease.permitted_operation is ModelOperation.COMMANDER_MODEL
    assert lease.allowed_actions is ModelAllowedActions.PLAN_ONLY
    assert lease.source_record_id == slot.slot_id
    assert instance.capacity_slot(slot.slot_id).status is CapacitySlotStatus.MATERIALIZED
    assert snapshot.active_capacity_slot_ids == ()


def test_budget_lease_requires_every_exact_source_and_token_field() -> None:
    required_fields = (
        "allowed_actions",
        "source_record_id",
        "exact_input_tokens",
        "minimum_completion_tokens",
        "max_completion_tokens",
    )
    assert all(BudgetLease.model_fields[name].is_required() for name in required_fields)

    legacy_payload = {
        "schema_version": "phase2.budget-lease.v1",
        "lease_id": "legacy-source-less-lease",
        "run_id": RUN_ID,
        "variant": Phase2Variant.DYNAMIC_MULTI_AGENT,
        "case_id": CASE_ID,
        "snapshot_sequence": 0,
        "owner_role": BudgetOwnerRole.INCIDENT_COMMANDER,
        "owner_node_id": None,
        "permitted_operation": ModelOperation.COMMANDER_MODEL,
        "reserved_model_calls": 1,
        "reserved_tool_calls": 0,
        "reserved_tokens": COMMANDER_FLOOR,
        "issued_at": NOW,
        "expires_at": NOW + timedelta(minutes=5),
        "status": BudgetLeaseStatus.RESERVED,
        "actual_model_calls": 0,
        "actual_tool_calls": 0,
        "actual_tokens": 0,
    }
    with pytest.raises(ValidationError):
        BudgetLease.model_validate(legacy_payload)


def test_failed_authorization_rejects_impossible_stage_combinations() -> None:
    instance = ledger()
    slot, _ = hold_one(instance, specialist_request())
    authorization, _ = materialize_metrics(instance, slot.slot_id)
    dispatching, _ = claim_dispatch(instance, authorization)
    payload = dispatching.model_dump(mode="python")

    with pytest.raises(ValidationError, match="failed"):
        SpecialistExecutionAuthorization.model_validate(
            {
                **payload,
                "status": SpecialistAuthorizationStatus.FAILED,
                "actual_tool_calls": 0,
                "model_lease_id": "impossible-lease",
            }
        )

    failed = SpecialistExecutionAuthorization.model_validate(
        {
            **payload,
            "status": SpecialistAuthorizationStatus.FAILED,
            "actual_tool_calls": 1,
        }
    )
    assert failed.status is SpecialistAuthorizationStatus.FAILED


def test_exact_expansion_accepts_a_bounded_completion_below_available_slack() -> None:
    instance = ledger()
    slot, _ = hold_one(instance, commander_request())
    lease, snapshot = instance.expand_exact_model_lease(
        expected_snapshot_sequence=instance.snapshot().sequence,
        source_record_id=slot.slot_id,
        exact_input_tokens=COMMANDER_INPUT,
        minimum_completion_tokens=COMMANDER_MINIMUM_COMPLETION,
        max_completion_tokens=500,
    )
    assert lease.reserved_tokens == COMMANDER_INPUT + 500
    assert snapshot.reserved_tokens == COMMANDER_INPUT + 500


def test_exact_expansion_above_available_capacity_rolls_back() -> None:
    instance = ledger()
    slot, _ = hold_one(instance, commander_request())
    snapshot = instance.snapshot()
    available_for_call = snapshot.remaining_tokens + COMMANDER_FLOOR
    before = (snapshot, instance.audit_events())
    with pytest.raises(BudgetLedgerError) as captured:
        instance.expand_exact_model_lease(
            expected_snapshot_sequence=snapshot.sequence,
            source_record_id=slot.slot_id,
            exact_input_tokens=COMMANDER_INPUT,
            minimum_completion_tokens=COMMANDER_MINIMUM_COMPLETION,
            max_completion_tokens=available_for_call - COMMANDER_INPUT + 1,
        )
    assert captured.value.code is Phase2FailureCode.BUDGET_EXACT_EXPANSION_FAILED
    assert (instance.snapshot(), instance.audit_events()) == before


def test_exact_expansion_accepts_a_conservative_floor_above_exact_minimum() -> None:
    instance = ledger()
    slot, _ = hold_one(instance, commander_request(COMMANDER_FLOOR + 1))

    lease, snapshot = expand(
        instance,
        slot.slot_id,
        exact_input_tokens=COMMANDER_INPUT,
        minimum_completion_tokens=COMMANDER_MINIMUM_COMPLETION,
    )

    assert lease.reserved_tokens == 32_000
    assert snapshot.active_lease_ids == (lease.lease_id,)


def test_exact_expansion_grows_a_minimum_floor_when_current_pool_allows() -> None:
    instance = ledger()
    slot, _ = hold_one(instance, commander_request(COMMANDER_FLOOR - 1))

    lease, snapshot = expand(
        instance,
        slot.slot_id,
        exact_input_tokens=COMMANDER_INPUT,
        minimum_completion_tokens=COMMANDER_MINIMUM_COMPLETION,
    )

    assert lease.exact_input_tokens == COMMANDER_INPUT
    assert lease.minimum_completion_tokens == COMMANDER_MINIMUM_COMPLETION
    assert lease.reserved_tokens == 32_000
    assert snapshot.active_lease_ids == (lease.lease_id,)


@pytest.mark.parametrize(
    ("exact_input_tokens", "minimum_completion_tokens", "max_completion_tokens"),
    (
        (True, 100, 200),
        (100, "100", 200),
        (100, 201, 200),
        (100, 100, 200.0),
    ),
)
def test_exact_expansion_failure_is_atomic(
    exact_input_tokens: object,
    minimum_completion_tokens: object,
    max_completion_tokens: object,
) -> None:
    instance = ledger()
    slot, _ = hold_one(instance, commander_request())
    before = (instance.snapshot(), instance.audit_events())
    with pytest.raises(BudgetLedgerError) as captured:
        instance.expand_exact_model_lease(
            expected_snapshot_sequence=instance.snapshot().sequence,
            source_record_id=slot.slot_id,
            exact_input_tokens=exact_input_tokens,  # type: ignore[arg-type]
            minimum_completion_tokens=minimum_completion_tokens,  # type: ignore[arg-type]
            max_completion_tokens=max_completion_tokens,  # type: ignore[arg-type]
        )
    assert captured.value.code is Phase2FailureCode.BUDGET_EXACT_EXPANSION_FAILED
    assert (instance.snapshot(), instance.audit_events()) == before
    assert instance.capacity_slot(slot.slot_id).status is CapacitySlotStatus.HELD


def test_final_judge_floor_is_never_lent_to_specialist() -> None:
    instance = ledger()
    slot, _ = hold_one(instance, final_judge_request())
    before = (instance.snapshot(), instance.audit_events())
    with pytest.raises(BudgetLedgerError) as captured:
        materialize_metrics(instance, slot.slot_id)
    assert captured.value.code is Phase2FailureCode.BUDGET_SLOT_OWNER_MISMATCH
    assert (instance.snapshot(), instance.audit_events()) == before


def test_release_and_expiry_are_one_way_and_return_only_unused_capacity() -> None:
    current_time = [NOW]
    instance = ledger(current_time=current_time)
    released_slot, _ = hold_one(instance, specialist_request())
    released, released_snapshot = instance.release_capacity_slot(
        expected_snapshot_sequence=instance.snapshot().sequence,
        slot_id=released_slot.slot_id,
    )
    assert released.status is CapacitySlotStatus.RELEASED
    assert released_snapshot.remaining_tokens == 32_000

    expiring_slot, _ = hold_one(
        instance,
        specialist_request(expires_at=NOW + timedelta(seconds=1)),
    )
    current_time[0] = NOW + timedelta(seconds=2)
    expired, expired_snapshot = instance.expire_capacity_slot(
        expected_snapshot_sequence=instance.snapshot().sequence,
        slot_id=expiring_slot.slot_id,
    )
    assert expired.status is CapacitySlotStatus.EXPIRED
    assert expired_snapshot.remaining_tokens == 32_000

    for slot_id in (released_slot.slot_id, expiring_slot.slot_id):
        before = (instance.snapshot(), instance.audit_events())
        with pytest.raises(BudgetLedgerError) as captured:
            instance.release_capacity_slot(
                expected_snapshot_sequence=instance.snapshot().sequence,
                slot_id=slot_id,
            )
        assert captured.value.code is Phase2FailureCode.BUDGET_SLOT_ALREADY_CONSUMED
        assert (instance.snapshot(), instance.audit_events()) == before


def test_only_unused_specialist_authorization_can_be_released() -> None:
    instance = ledger()
    slot, _ = hold_one(instance, specialist_request())
    authorization, _ = materialize_metrics(instance, slot.slot_id)
    released, snapshot = instance.release_specialist_authorization(
        expected_snapshot_sequence=instance.snapshot().sequence,
        authorization_id=authorization.authorization_id,
    )
    assert released.status is SpecialistAuthorizationStatus.RELEASED
    assert snapshot.remaining_model_calls == 8
    assert snapshot.remaining_tool_calls == 8

    second_slot, _ = hold_one(instance, specialist_request())
    second, _ = materialize_metrics(instance, second_slot.slot_id)
    claim_and_complete(instance, second)
    before = (instance.snapshot(), instance.audit_events())
    with pytest.raises(BudgetLedgerError) as captured:
        instance.release_specialist_authorization(
            expected_snapshot_sequence=instance.snapshot().sequence,
            authorization_id=second.authorization_id,
        )
    assert captured.value.code is Phase2FailureCode.BUDGET_SLOT_ALREADY_CONSUMED
    assert (instance.snapshot(), instance.audit_events()) == before


def test_specialist_failure_releases_unused_model_floor_but_not_charged_tool() -> None:
    instance = ledger()
    authorization = charged_metrics_authorization(instance)
    failed, snapshot = instance.fail_specialist_authorization(
        expected_snapshot_sequence=instance.snapshot().sequence,
        authorization_id=authorization.authorization_id,
    )
    assert failed.status is SpecialistAuthorizationStatus.FAILED
    assert snapshot.charged_tool_calls == 1
    assert snapshot.reserved_model_calls == 0
    assert snapshot.remaining_model_calls == 8


def test_specialist_failure_rejects_unused_but_allows_post_charge_stages() -> None:
    unused = ledger()
    unused_slot, _ = hold_one(unused, specialist_request())
    unused_authorization, _ = materialize_metrics(unused, unused_slot.slot_id)
    unused_before = (unused.snapshot(), unused.audit_events())
    with pytest.raises(BudgetLedgerError) as unused_error:
        unused.fail_specialist_authorization(
            expected_snapshot_sequence=unused.snapshot().sequence,
            authorization_id=unused_authorization.authorization_id,
        )
    assert unused_error.value.code is (
        Phase2FailureCode.BUDGET_SLOT_ALREADY_CONSUMED
    )
    assert (unused.snapshot(), unused.audit_events()) == unused_before

    leased = ledger()
    leased_authorization = charged_metrics_authorization(leased)
    exact_lease, _ = expand(
        leased,
        leased_authorization.authorization_id,
        exact_input_tokens=SPECIALIST_INPUT,
        minimum_completion_tokens=SPECIALIST_MINIMUM_COMPLETION,
    )
    leased_failed, leased_snapshot = leased.fail_specialist_authorization(
        expected_snapshot_sequence=leased.snapshot().sequence,
        authorization_id=leased_authorization.authorization_id,
    )
    assert leased_failed.status is SpecialistAuthorizationStatus.FAILED
    assert leased.model_lease(exact_lease.lease_id).status is BudgetLeaseStatus.RETURNED
    assert leased_snapshot.reserved_model_calls == 0
    assert (leased_failed.actual_tool_calls, leased_failed.model_lease_id) == (
        1,
        exact_lease.lease_id,
    )


def test_dynamic_fixed_and_final_only_initialization_helpers_are_atomic() -> None:
    dynamic = ledger()
    dynamic_slots, dynamic_snapshot = dynamic.initialize_dynamic(
        expected_snapshot_sequence=0,
        commander=commander_request(),
        specialist=specialist_request(),
        first_judge=first_judge_request(),
    )
    assert len(dynamic_slots) == 3
    assert dynamic_snapshot.sequence == 1

    fixed = ledger(variant=Phase2Variant.FIXED_SPECIALIST_WORKFLOW)
    fixed_slots, fixed_snapshot = fixed.initialize_fixed(
        expected_snapshot_sequence=0,
        specialists=(
            specialist_request(401),
            specialist_request(402),
            specialist_request(403),
            specialist_request(404),
        ),
        final_judge=final_judge_request(),
    )
    assert len(fixed_slots) == 5
    assert fixed_snapshot.reserved_tool_calls == 4
    assert fixed_snapshot.reserved_model_calls == 5

    final_only = ledger()
    final_slot, final_snapshot = final_only.hold_final_only_first_judge(
        expected_snapshot_sequence=0,
        request=first_judge_request(),
    )
    assert final_slot.allowed_actions is ModelAllowedActions.FINAL_ONLY
    assert final_snapshot.sequence == 1


@pytest.mark.parametrize(
    "variant",
    (
        Phase2Variant.DYNAMIC_MULTI_AGENT,
        Phase2Variant.FIXED_SPECIALIST_WORKFLOW,
    ),
)
def test_initialization_helpers_reject_repeat_and_prior_activity_atomically(
    variant: Phase2Variant,
) -> None:
    repeated = ledger(variant=variant)
    if variant is Phase2Variant.DYNAMIC_MULTI_AGENT:
        repeated.initialize_dynamic(
            expected_snapshot_sequence=0,
            commander=commander_request(),
            specialist=specialist_request(),
            first_judge=first_judge_request(),
        )
    else:
        repeated.initialize_fixed(
            expected_snapshot_sequence=0,
            specialists=tuple(specialist_request() for _ in range(4)),
            final_judge=final_judge_request(),
        )
    repeated_before = (repeated.snapshot(), repeated.audit_events())
    with pytest.raises(BudgetLedgerError) as repeated_error:
        if variant is Phase2Variant.DYNAMIC_MULTI_AGENT:
            repeated.initialize_dynamic(
                expected_snapshot_sequence=repeated.snapshot().sequence,
                commander=commander_request(),
                specialist=specialist_request(),
                first_judge=first_judge_request(),
            )
        else:
            repeated.initialize_fixed(
                expected_snapshot_sequence=repeated.snapshot().sequence,
                specialists=tuple(specialist_request() for _ in range(4)),
                final_judge=final_judge_request(),
            )
    assert repeated_error.value.code is Phase2FailureCode.BUDGET_SLOT_ALREADY_CONSUMED
    assert (repeated.snapshot(), repeated.audit_events()) == repeated_before

    active = ledger(variant=variant)
    hold_one(active, commander_request())
    active_before = (active.snapshot(), active.audit_events())
    with pytest.raises(BudgetLedgerError) as active_error:
        if variant is Phase2Variant.DYNAMIC_MULTI_AGENT:
            active.initialize_dynamic(
                expected_snapshot_sequence=active.snapshot().sequence,
                commander=commander_request(),
                specialist=specialist_request(),
                first_judge=first_judge_request(),
            )
        else:
            active.initialize_fixed(
                expected_snapshot_sequence=active.snapshot().sequence,
                specialists=tuple(specialist_request() for _ in range(4)),
                final_judge=final_judge_request(),
            )
    assert active_error.value.code is Phase2FailureCode.BUDGET_SLOT_ALREADY_CONSUMED
    assert (active.snapshot(), active.audit_events()) == active_before


def test_initial_plan_resize_retains_or_creates_one_slot_per_node() -> None:
    instance = ledger()
    commander_slot, bootstrap_specialist, judge_slot = (
        dynamic_after_commander_charge(instance)
    )
    slots, snapshot = instance.resize_dynamic_initial_plan(
        expected_snapshot_sequence=instance.snapshot().sequence,
        commander_slot_id=commander_slot.slot_id,
        retained_specialist_slot_ids=(bootstrap_specialist.slot_id,),
        new_specialists=(
            specialist_request(expires_at=bootstrap_specialist.expires_at),
        ),
        first_judge_slot_id=judge_slot.slot_id,
    )
    assert len(slots) == 2
    assert slots[0].slot_id == bootstrap_specialist.slot_id
    assert snapshot.reserved_model_calls == 3
    assert snapshot.reserved_tool_calls == 2
    assert (
        instance.capacity_slot(commander_slot.slot_id).status
        is CapacitySlotStatus.MATERIALIZED
    )


def test_resize_uses_one_locked_utc_read_and_inherits_bootstrap_expiry() -> None:
    clock = CountingUtcClock(NOW)
    instance = ledger_with_clock(clock)
    commander, bootstrap, judge = dynamic_after_commander_charge(instance)
    calls_before = clock.calls
    slots, snapshot = instance.resize_dynamic_initial_plan(
        expected_snapshot_sequence=instance.snapshot().sequence,
        commander_slot_id=commander.slot_id,
        retained_specialist_slot_ids=(bootstrap.slot_id,),
        new_specialists=(
            specialist_request(expires_at=bootstrap.expires_at),
        ),
        first_judge_slot_id=judge.slot_id,
    )
    assert clock.calls == calls_before + 1
    assert slots[1].expires_at == bootstrap.expires_at
    assert judge.slot_id in snapshot.active_capacity_slot_ids


@pytest.mark.parametrize("target", ("commander", "bootstrap", "judge", "new"))
def test_resize_at_expiry_is_atomic_and_does_not_consume_ids(
    target: str,
) -> None:
    current_time = [NOW]
    instance = ledger(current_time=current_time)
    control = ledger(current_time=[NOW])
    commander, bootstrap, judge = dynamic_after_commander_charge(instance)
    control_commander, control_bootstrap, control_judge = (
        dynamic_after_commander_charge(control)
    )
    expires_at = bootstrap.expires_at
    if target == "commander":
        expires_at = commander.expires_at
    elif target == "judge":
        expires_at = judge.expires_at
    current_time[0] = expires_at
    new_expiry = (
        expires_at if target == "new" else bootstrap.expires_at
    )
    before = (
        instance.snapshot(),
        instance.audit_events(),
        instance.capacity_slot_ids(),
    )
    with pytest.raises(BudgetLedgerError) as captured:
        instance.resize_dynamic_initial_plan(
            expected_snapshot_sequence=instance.snapshot().sequence,
            commander_slot_id=commander.slot_id,
            retained_specialist_slot_ids=(bootstrap.slot_id,),
            new_specialists=(specialist_request(expires_at=new_expiry),),
            first_judge_slot_id=judge.slot_id,
        )
    assert captured.value.code is Phase2FailureCode.BUDGET_SLOT_STALE
    assert (
        instance.snapshot(),
        instance.audit_events(),
        instance.capacity_slot_ids(),
    ) == before
    current_time[0] = NOW
    created_after_failure, _ = instance.resize_dynamic_initial_plan(
        expected_snapshot_sequence=instance.snapshot().sequence,
        commander_slot_id=commander.slot_id,
        retained_specialist_slot_ids=(bootstrap.slot_id,),
        new_specialists=(
            specialist_request(expires_at=bootstrap.expires_at),
        ),
        first_judge_slot_id=judge.slot_id,
    )
    created_without_failure, _ = control.resize_dynamic_initial_plan(
        expected_snapshot_sequence=control.snapshot().sequence,
        commander_slot_id=control_commander.slot_id,
        retained_specialist_slot_ids=(control_bootstrap.slot_id,),
        new_specialists=(
            specialist_request(expires_at=control_bootstrap.expires_at),
        ),
        first_judge_slot_id=control_judge.slot_id,
    )
    assert tuple(slot.slot_id for slot in created_after_failure) == tuple(
        slot.slot_id for slot in created_without_failure
    )


@pytest.mark.parametrize(
    ("retained", "new", "expected"),
    (
        ((), (), Phase2FailureCode.BUDGET_MINIMUM_FLOOR_UNAVAILABLE),
        (("bootstrap", "bootstrap"), (), Phase2FailureCode.BUDGET_MINIMUM_FLOOR_UNAVAILABLE),
    ),
)
def test_resize_requires_exactly_one_bootstrap_specialist(
    retained: tuple[str, ...],
    new: tuple[CapacitySlotRequest, ...],
    expected: Phase2FailureCode,
) -> None:
    instance = ledger()
    commander, bootstrap, judge = dynamic_after_commander_charge(instance)
    retained_ids = tuple(
        bootstrap.slot_id if item == "bootstrap" else item for item in retained
    )
    before = (instance.snapshot(), instance.audit_events())
    with pytest.raises(BudgetLedgerError) as captured:
        instance.resize_dynamic_initial_plan(
            expected_snapshot_sequence=instance.snapshot().sequence,
            commander_slot_id=commander.slot_id,
            retained_specialist_slot_ids=retained_ids,
            new_specialists=new,
            first_judge_slot_id=judge.slot_id,
        )
    assert captured.value.code is expected
    assert (instance.snapshot(), instance.audit_events()) == before


def test_resize_rejects_wrong_new_expiry_floor_and_extra_active_slot() -> None:
    for failure in ("expiry", "floor", "extra"):
        instance = ledger()
        commander, bootstrap, judge = dynamic_after_commander_charge(instance)
        if failure == "extra":
            hold_one(instance, specialist_request())
        request_value = specialist_request(
            floor=(SPECIALIST_FLOOR + 1 if failure == "floor" else SPECIALIST_FLOOR),
            expires_at=(
                bootstrap.expires_at + timedelta(seconds=1)
                if failure == "expiry"
                else bootstrap.expires_at
            ),
        )
        before = (instance.snapshot(), instance.audit_events())
        with pytest.raises(BudgetLedgerError) as captured:
            instance.resize_dynamic_initial_plan(
                expected_snapshot_sequence=instance.snapshot().sequence,
                commander_slot_id=commander.slot_id,
                retained_specialist_slot_ids=(bootstrap.slot_id,),
                new_specialists=(request_value,),
                first_judge_slot_id=judge.slot_id,
            )
        assert captured.value.code in {
            Phase2FailureCode.BUDGET_SLOT_OWNER_MISMATCH,
            Phase2FailureCode.BUDGET_SLOT_ALREADY_CONSUMED,
        }
        assert (instance.snapshot(), instance.audit_events()) == before


@pytest.mark.parametrize(
    ("field", "malicious_value"),
    (
        ("reserved_model_calls", True),
        ("reserved_tool_calls", True),
        ("minimum_token_floor", float(SPECIALIST_FLOOR)),
    ),
)
def test_resize_revalidates_unchecked_new_request_before_clock_or_id_callbacks(
    field: str,
    malicious_value: object,
) -> None:
    target_clock = CountingUtcClock(NOW)
    control_clock = CountingUtcClock(NOW)
    target = ledger_with_clock(target_clock)
    control = ledger_with_clock(control_clock)
    commander, bootstrap, judge = dynamic_after_commander_charge(target)
    control_commander, control_bootstrap, control_judge = (
        dynamic_after_commander_charge(control)
    )
    malicious = specialist_request(
        expires_at=bootstrap.expires_at
    ).model_copy(update={field: malicious_value})
    before = (target.snapshot(), target.audit_events(), target.capacity_slot_ids())
    calls_before = target_clock.calls
    with pytest.raises(BudgetLedgerError) as captured:
        target.resize_dynamic_initial_plan(
            expected_snapshot_sequence=target.snapshot().sequence,
            commander_slot_id=commander.slot_id,
            retained_specialist_slot_ids=(bootstrap.slot_id,),
            new_specialists=(malicious,),
            first_judge_slot_id=judge.slot_id,
        )
    assert captured.value.code is Phase2FailureCode.BUDGET_SLOT_OWNER_MISMATCH
    assert (target.snapshot(), target.audit_events(), target.capacity_slot_ids()) == before
    assert target_clock.calls == calls_before

    target_created, _ = target.resize_dynamic_initial_plan(
        expected_snapshot_sequence=target.snapshot().sequence,
        commander_slot_id=commander.slot_id,
        retained_specialist_slot_ids=(bootstrap.slot_id,),
        new_specialists=(specialist_request(expires_at=bootstrap.expires_at),),
        first_judge_slot_id=judge.slot_id,
    )
    control_created, _ = control.resize_dynamic_initial_plan(
        expected_snapshot_sequence=control.snapshot().sequence,
        commander_slot_id=control_commander.slot_id,
        retained_specialist_slot_ids=(control_bootstrap.slot_id,),
        new_specialists=(
            specialist_request(expires_at=control_bootstrap.expires_at),
        ),
        first_judge_slot_id=control_judge.slot_id,
    )
    assert tuple(item.slot_id for item in target_created) == tuple(
        item.slot_id for item in control_created
    )


def test_resize_rejects_pre_charge_commander_without_mutation() -> None:
    instance = ledger()
    slots, _ = instance.initialize_dynamic(
        expected_snapshot_sequence=0,
        commander=commander_request(),
        specialist=specialist_request(),
        first_judge=first_judge_request(),
    )
    before = (instance.snapshot(), instance.audit_events())
    with pytest.raises(BudgetLedgerError) as captured:
        instance.resize_dynamic_initial_plan(
            expected_snapshot_sequence=instance.snapshot().sequence,
            commander_slot_id=slots[0].slot_id,
            retained_specialist_slot_ids=(slots[1].slot_id,),
            new_specialists=(),
            first_judge_slot_id=slots[2].slot_id,
        )
    assert captured.value.code is Phase2FailureCode.BUDGET_SLOT_ALREADY_CONSUMED
    assert (instance.snapshot(), instance.audit_events()) == before


def refinement_bundle(instance: BudgetLedger):
    initial_slots, snapshot = instance.initialize_dynamic(
        expected_snapshot_sequence=instance.snapshot().sequence,
        commander=commander_request(),
        specialist=specialist_request(),
        first_judge=first_judge_request(),
    )
    return instance.replace_first_judge_with_conditional_bundle(
        expected_snapshot_sequence=snapshot.sequence,
        replaced_first_judge_slot_id=initial_slots[2].slot_id,
        first_judge=first_judge_request(conditional=True),
        specialists=(specialist_request(), specialist_request()),
        final_judge=final_judge_request(),
    )


def test_conditional_bundle_has_no_public_standalone_creation_path() -> None:
    assert not hasattr(ledger(), "hold_conditional_refinement_bundle")


def test_active_bundle_members_reject_generic_release_and_expiry() -> None:
    current_time = [NOW]
    instance = ledger(current_time=current_time)
    bundle, _ = refinement_bundle(instance)
    member_id = bundle.specialist_capacity_slot_ids[0]
    before_release = (instance.snapshot(), instance.audit_events())
    with pytest.raises(BudgetLedgerError) as released:
        instance.release_capacity_slot(
            expected_snapshot_sequence=instance.snapshot().sequence,
            slot_id=member_id,
        )
    assert released.value.code is Phase2FailureCode.BUDGET_SLOT_ALREADY_CONSUMED
    assert (instance.snapshot(), instance.audit_events()) == before_release

    current_time[0] = NOW + timedelta(minutes=6)
    before_expiry = (instance.snapshot(), instance.audit_events())
    with pytest.raises(BudgetLedgerError) as expired:
        instance.expire_capacity_slot(
            expected_snapshot_sequence=instance.snapshot().sequence,
            slot_id=member_id,
        )
    assert expired.value.code is Phase2FailureCode.BUDGET_SLOT_ALREADY_CONSUMED
    assert (instance.snapshot(), instance.audit_events()) == before_expiry


def test_bundle_specialist_cannot_authorize_before_first_judge_charge() -> None:
    instance = ledger()
    bundle, _ = refinement_bundle(instance)
    before = (instance.snapshot(), instance.audit_events())
    with pytest.raises(BudgetLedgerError) as captured:
        materialize_metrics(instance, bundle.specialist_capacity_slot_ids[0])
    assert captured.value.code is Phase2FailureCode.BUDGET_SLOT_ALREADY_CONSUMED
    assert (instance.snapshot(), instance.audit_events()) == before


@pytest.mark.parametrize("first_judge_reserved", (False, True))
def test_bundle_final_judge_cannot_expand_before_first_judge_charge(
    first_judge_reserved: bool,
) -> None:
    instance = ledger()
    bundle, _ = refinement_bundle(instance)
    if first_judge_reserved:
        expand(
            instance,
            bundle.first_judge_capacity_slot_id,
            exact_input_tokens=FIRST_JUDGE_INPUT,
            minimum_completion_tokens=FIRST_JUDGE_MINIMUM_COMPLETION,
        )
    before = (instance.snapshot(), instance.audit_events())
    with pytest.raises(BudgetLedgerError) as captured:
        instance.expand_exact_model_lease(
            expected_snapshot_sequence=instance.snapshot().sequence,
            source_record_id=bundle.final_judge_capacity_slot_id,
            exact_input_tokens=FINAL_JUDGE_INPUT,
            minimum_completion_tokens=FINAL_JUDGE_MINIMUM_COMPLETION,
            max_completion_tokens=FINAL_JUDGE_MINIMUM_COMPLETION,
        )
    assert captured.value.code is Phase2FailureCode.BUDGET_SLOT_ALREADY_CONSUMED
    assert (instance.snapshot(), instance.audit_events()) == before
    assert instance.capacity_slot(
        bundle.final_judge_capacity_slot_id
    ).status is CapacitySlotStatus.HELD


def test_bundle_final_judge_waits_for_retained_specialist_completion() -> None:
    instance = ledger()
    bundle, _ = refinement_bundle(instance)
    charge_bundle_first_judge(instance, bundle.first_judge_capacity_slot_id)
    specialist_slot_id = bundle.specialist_capacity_slot_ids[0]
    authorization, _ = materialize_metrics(instance, specialist_slot_id)
    instance.release_unused_refinement_members(
        expected_snapshot_sequence=instance.snapshot().sequence,
        bundle_id=bundle.bundle_id,
        used_specialist_slot_ids=(specialist_slot_id,),
        retain_final_judge=True,
    )
    authorization = claim_and_complete(instance, authorization)[0]
    specialist_lease, _ = expand(
        instance,
        authorization.authorization_id,
        exact_input_tokens=SPECIALIST_INPUT,
        minimum_completion_tokens=SPECIALIST_MINIMUM_COMPLETION,
    )
    instance.charge_exact_model_lease(
        expected_snapshot_sequence=instance.snapshot().sequence,
        lease_id=specialist_lease.lease_id,
        owner_role=BudgetOwnerRole.METRICS_AGENT,
        owner_node_id="node-metrics-001",
        source_record_id=authorization.authorization_id,
        input_tokens=SPECIALIST_INPUT,
        output_tokens=SPECIALIST_MINIMUM_COMPLETION,
        total_tokens=SPECIALIST_FLOOR,
    )

    before = (instance.snapshot(), instance.audit_events())
    with pytest.raises(BudgetLedgerError) as captured:
        instance.expand_exact_model_lease(
            expected_snapshot_sequence=instance.snapshot().sequence,
            source_record_id=bundle.final_judge_capacity_slot_id,
            exact_input_tokens=FINAL_JUDGE_INPUT,
            minimum_completion_tokens=FINAL_JUDGE_MINIMUM_COMPLETION,
            max_completion_tokens=FINAL_JUDGE_MINIMUM_COMPLETION,
        )
    assert captured.value.code is Phase2FailureCode.BUDGET_SLOT_ALREADY_CONSUMED
    assert (instance.snapshot(), instance.audit_events()) == before


def test_partial_bundle_requires_at_least_one_retained_specialist() -> None:
    instance = ledger()
    bundle, _ = refinement_bundle(instance)
    charge_bundle_first_judge(instance, bundle.first_judge_capacity_slot_id)
    before = (instance.snapshot(), instance.audit_events())
    with pytest.raises(BudgetLedgerError) as captured:
        instance.release_unused_refinement_members(
            expected_snapshot_sequence=instance.snapshot().sequence,
            bundle_id=bundle.bundle_id,
            used_specialist_slot_ids=(),
            retain_final_judge=True,
        )
    assert captured.value.code is Phase2FailureCode.BUDGET_SLOT_OWNER_MISMATCH
    assert (instance.snapshot(), instance.audit_events()) == before


def test_bundle_specialist_authorization_cannot_release_independently() -> None:
    instance = ledger()
    bundle, _ = refinement_bundle(instance)
    charge_bundle_first_judge(instance, bundle.first_judge_capacity_slot_id)
    authorization, _ = materialize_metrics(
        instance, bundle.specialist_capacity_slot_ids[0]
    )
    before = (instance.snapshot(), instance.audit_events())
    with pytest.raises(BudgetLedgerError) as captured:
        instance.release_specialist_authorization(
            expected_snapshot_sequence=instance.snapshot().sequence,
            authorization_id=authorization.authorization_id,
        )
    assert captured.value.code is Phase2FailureCode.BUDGET_SLOT_ALREADY_CONSUMED
    assert (instance.snapshot(), instance.audit_events()) == before
    assert instance.capacity_slot(
        authorization.capacity_slot_id
    ).status is CapacitySlotStatus.MATERIALIZED
    assert instance.specialist_authorization(authorization.authorization_id) == authorization
    assert instance.conditional_bundle(bundle.bundle_id) == bundle


def test_plan_resize_cannot_mutate_active_bundle_members() -> None:
    instance = ledger()
    initial_slots, _initial_snapshot = instance.initialize_dynamic(
        expected_snapshot_sequence=0,
        commander=commander_request(),
        specialist=specialist_request(),
        first_judge=first_judge_request(),
    )
    extra_judge, extra_snapshot = hold_one(instance, first_judge_request())
    instance.replace_first_judge_with_conditional_bundle(
        expected_snapshot_sequence=extra_snapshot.sequence,
        replaced_first_judge_slot_id=initial_slots[2].slot_id,
        first_judge=first_judge_request(conditional=True),
        specialists=(specialist_request(), specialist_request()),
        final_judge=final_judge_request(),
    )
    before = (instance.snapshot(), instance.audit_events())
    with pytest.raises(BudgetLedgerError) as captured:
        instance.resize_dynamic_initial_plan(
            expected_snapshot_sequence=instance.snapshot().sequence,
            commander_slot_id=initial_slots[0].slot_id,
            retained_specialist_slot_ids=(initial_slots[1].slot_id,),
            new_specialists=(),
            first_judge_slot_id=extra_judge.slot_id,
        )
    assert captured.value.code is Phase2FailureCode.BUDGET_SLOT_ALREADY_CONSUMED
    assert (instance.snapshot(), instance.audit_events()) == before


def test_dynamic_first_judge_floor_is_atomically_replaced_by_conditional_bundle() -> None:
    instance = ledger()
    initial_slots, initial_snapshot = instance.initialize_dynamic(
        expected_snapshot_sequence=0,
        commander=commander_request(),
        specialist=specialist_request(),
        first_judge=first_judge_request(),
    )
    final_only_slot = initial_slots[2]
    bundle, replaced_snapshot = instance.replace_first_judge_with_conditional_bundle(
        expected_snapshot_sequence=initial_snapshot.sequence,
        replaced_first_judge_slot_id=final_only_slot.slot_id,
        first_judge=first_judge_request(conditional=True),
        specialists=(specialist_request(), specialist_request()),
        final_judge=final_judge_request(),
    )
    assert replaced_snapshot.sequence == initial_snapshot.sequence + 1
    assert instance.capacity_slot(
        final_only_slot.slot_id
    ).status is CapacitySlotStatus.RELEASED
    assert replaced_snapshot.reserved_model_calls == 6
    assert replaced_snapshot.reserved_tool_calls == 3
    assert bundle.first_judge_capacity_slot_id in replaced_snapshot.active_capacity_slot_ids

    before_reuse = (instance.snapshot(), instance.audit_events())
    with pytest.raises(BudgetLedgerError) as captured:
        instance.expand_exact_model_lease(
            expected_snapshot_sequence=instance.snapshot().sequence,
            source_record_id=final_only_slot.slot_id,
            exact_input_tokens=COMMANDER_INPUT,
            minimum_completion_tokens=COMMANDER_MINIMUM_COMPLETION,
            max_completion_tokens=502,
        )
    assert captured.value.code is Phase2FailureCode.BUDGET_SLOT_ALREADY_CONSUMED
    assert (instance.snapshot(), instance.audit_events()) == before_reuse


def test_dynamic_conditional_replacement_rolls_back_on_stale_or_capacity_failure() -> None:
    stale = ledger()
    stale_slots, stale_snapshot = stale.initialize_dynamic(
        expected_snapshot_sequence=0,
        commander=commander_request(),
        specialist=specialist_request(),
        first_judge=first_judge_request(),
    )
    stale_before = (stale.snapshot(), stale.audit_events())
    with pytest.raises(BudgetLedgerError) as stale_error:
        stale.replace_first_judge_with_conditional_bundle(
            expected_snapshot_sequence=stale_snapshot.sequence - 1,
            replaced_first_judge_slot_id=stale_slots[2].slot_id,
            first_judge=first_judge_request(conditional=True),
            specialists=(specialist_request(), specialist_request()),
            final_judge=final_judge_request(),
        )
    assert stale_error.value.code is Phase2FailureCode.BUDGET_CAS_CONFLICT
    assert (stale.snapshot(), stale.audit_events()) == stale_before

    full = ledger()
    full_slots, _ = full.initialize_dynamic(
        expected_snapshot_sequence=0,
        commander=commander_request(),
        specialist=specialist_request(),
        first_judge=first_judge_request(),
    )
    full.hold_capacity_slots(
        expected_snapshot_sequence=full.snapshot().sequence,
        requests=tuple(commander_request(101 + index) for index in range(5)),
    )
    capacity_before = (full.snapshot(), full.audit_events())
    with pytest.raises(BudgetLedgerError) as capacity_error:
        full.replace_first_judge_with_conditional_bundle(
            expected_snapshot_sequence=full.snapshot().sequence,
            replaced_first_judge_slot_id=full_slots[2].slot_id,
            first_judge=first_judge_request(conditional=True),
            specialists=(specialist_request(), specialist_request()),
            final_judge=final_judge_request(),
        )
    assert (
        capacity_error.value.code
        is Phase2FailureCode.BUDGET_MINIMUM_FLOOR_UNAVAILABLE
    )
    assert (full.snapshot(), full.audit_events()) == capacity_before
    assert full.capacity_slot(full_slots[2].slot_id).status is CapacitySlotStatus.HELD


def charge_bundle_first_judge(instance: BudgetLedger, slot_id: str) -> None:
    lease, _ = expand(
        instance,
        slot_id,
        exact_input_tokens=FIRST_JUDGE_INPUT,
        minimum_completion_tokens=FIRST_JUDGE_MINIMUM_COMPLETION,
    )
    instance.charge_exact_model_lease(
        expected_snapshot_sequence=instance.snapshot().sequence,
        lease_id=lease.lease_id,
        owner_role=BudgetOwnerRole.RCA_JUDGE,
        owner_node_id=None,
        source_record_id=slot_id,
        input_tokens=FIRST_JUDGE_INPUT,
        output_tokens=FIRST_JUDGE_MINIMUM_COMPLETION,
        total_tokens=FIRST_JUDGE_FLOOR,
    )


def terminal_bundle_with_spare_first_judge(
    status: ConditionalRefinementBundleStatus,
):
    instance = ledger()
    initial, _ = instance.initialize_dynamic(
        expected_snapshot_sequence=0,
        commander=commander_request(),
        specialist=specialist_request(),
        first_judge=first_judge_request(),
    )
    spare, _ = hold_one(instance, first_judge_request())
    instance.release_capacity_slot(
        expected_snapshot_sequence=instance.snapshot().sequence,
        slot_id=initial[0].slot_id,
    )
    instance.release_capacity_slot(
        expected_snapshot_sequence=instance.snapshot().sequence,
        slot_id=initial[1].slot_id,
    )
    bundle, _ = instance.replace_first_judge_with_conditional_bundle(
        expected_snapshot_sequence=instance.snapshot().sequence,
        replaced_first_judge_slot_id=initial[2].slot_id,
        first_judge=first_judge_request(conditional=True),
        specialists=(specialist_request(), specialist_request()),
        final_judge=final_judge_request(),
    )
    charge_bundle_first_judge(instance, bundle.first_judge_capacity_slot_id)
    if status is ConditionalRefinementBundleStatus.RELEASED:
        instance.release_unused_refinement_members(
            expected_snapshot_sequence=instance.snapshot().sequence,
            bundle_id=bundle.bundle_id,
            used_specialist_slot_ids=(),
            retain_final_judge=False,
        )
        return instance, bundle, spare

    specialist_slot_id = bundle.specialist_capacity_slot_ids[0]
    authorization, _ = materialize_metrics(instance, specialist_slot_id)
    instance.release_unused_refinement_members(
        expected_snapshot_sequence=instance.snapshot().sequence,
        bundle_id=bundle.bundle_id,
        used_specialist_slot_ids=(specialist_slot_id,),
        retain_final_judge=True,
    )
    authorization = claim_and_complete(instance, authorization)[0]
    specialist_lease, _ = expand(
        instance,
        authorization.authorization_id,
        exact_input_tokens=SPECIALIST_INPUT,
        minimum_completion_tokens=SPECIALIST_MINIMUM_COMPLETION,
    )
    instance.charge_exact_model_lease(
        expected_snapshot_sequence=instance.snapshot().sequence,
        lease_id=specialist_lease.lease_id,
        owner_role=BudgetOwnerRole.METRICS_AGENT,
        owner_node_id="node-metrics-001",
        source_record_id=authorization.authorization_id,
        input_tokens=SPECIALIST_INPUT,
        output_tokens=SPECIALIST_MINIMUM_COMPLETION,
        total_tokens=SPECIALIST_FLOOR,
    )
    instance.complete_specialist_authorization(
        expected_snapshot_sequence=instance.snapshot().sequence,
        authorization_id=authorization.authorization_id,
    )
    final_lease, _ = expand(
        instance,
        bundle.final_judge_capacity_slot_id,
        exact_input_tokens=FINAL_JUDGE_INPUT,
        minimum_completion_tokens=FINAL_JUDGE_MINIMUM_COMPLETION,
    )
    instance.charge_exact_model_lease(
        expected_snapshot_sequence=instance.snapshot().sequence,
        lease_id=final_lease.lease_id,
        owner_role=BudgetOwnerRole.RCA_JUDGE,
        owner_node_id=None,
        source_record_id=bundle.final_judge_capacity_slot_id,
        input_tokens=FINAL_JUDGE_INPUT,
        output_tokens=FINAL_JUDGE_MINIMUM_COMPLETION,
        total_tokens=FINAL_JUDGE_FLOOR,
    )
    instance.complete_conditional_refinement_bundle(
        expected_snapshot_sequence=instance.snapshot().sequence,
        bundle_id=bundle.bundle_id,
    )
    assert status is ConditionalRefinementBundleStatus.COMPLETED
    return instance, bundle, spare


def test_conditional_bundle_supports_one_node_and_releases_only_unused_member() -> None:
    instance = ledger()
    bundle, snapshot = refinement_bundle(instance)
    assert bundle.status is ConditionalRefinementBundleStatus.HELD
    assert len(bundle.specialist_capacity_slot_ids) == 2
    assert snapshot.sequence == 2
    charge_bundle_first_judge(instance, bundle.first_judge_capacity_slot_id)

    first_specialist = bundle.specialist_capacity_slot_ids[0]
    authorization, _ = materialize_metrics(instance, first_specialist)
    updated, released_snapshot = instance.release_unused_refinement_members(
        expected_snapshot_sequence=instance.snapshot().sequence,
        bundle_id=bundle.bundle_id,
        used_specialist_slot_ids=(first_specialist,),
        retain_final_judge=True,
    )
    assert updated.status is ConditionalRefinementBundleStatus.PARTIALLY_CONSUMED
    assert instance.capacity_slot(
        bundle.specialist_capacity_slot_ids[1]
    ).status is CapacitySlotStatus.RELEASED
    assert instance.capacity_slot(
        bundle.final_judge_capacity_slot_id
    ).status is CapacitySlotStatus.HELD
    assert authorization.authorization_id in released_snapshot.active_specialist_authorization_ids


def test_conditional_bundle_supports_two_nodes_without_extension() -> None:
    instance = ledger()
    bundle, _ = refinement_bundle(instance)
    charge_bundle_first_judge(instance, bundle.first_judge_capacity_slot_id)
    first, second = bundle.specialist_capacity_slot_ids
    first_auth, _ = materialize_metrics(instance, first)
    second_auth = instance.materialize_specialist_authorization(
        expected_snapshot_sequence=instance.snapshot().sequence,
        slot_id=second,
        owner_role=BudgetOwnerRole.LOGS_AGENT,
        owner_node_id="node-logs-001",
        source=EvidenceSource.LOGS,
        tool_name=ReadOnlyToolName.SEARCH_LOGS,
    )[0]
    updated, _ = instance.release_unused_refinement_members(
        expected_snapshot_sequence=instance.snapshot().sequence,
        bundle_id=bundle.bundle_id,
        used_specialist_slot_ids=(first, second),
        retain_final_judge=True,
    )
    assert updated.status is ConditionalRefinementBundleStatus.PARTIALLY_CONSUMED
    assert {
        first_auth.authorization_id,
        second_auth.authorization_id,
    } == set(instance.snapshot().active_specialist_authorization_ids)

    assert not hasattr(instance, "extend_conditional_refinement_bundle")


def test_conditional_bundle_resolution_cannot_repeat_or_change_selection() -> None:
    instance = ledger()
    bundle, _ = refinement_bundle(instance)
    charge_bundle_first_judge(instance, bundle.first_judge_capacity_slot_id)
    first, second = bundle.specialist_capacity_slot_ids
    materialize_metrics(instance, first)
    instance.materialize_specialist_authorization(
        expected_snapshot_sequence=instance.snapshot().sequence,
        slot_id=second,
        owner_role=BudgetOwnerRole.LOGS_AGENT,
        owner_node_id="node-logs-001",
        source=EvidenceSource.LOGS,
        tool_name=ReadOnlyToolName.SEARCH_LOGS,
    )
    instance.release_unused_refinement_members(
        expected_snapshot_sequence=instance.snapshot().sequence,
        bundle_id=bundle.bundle_id,
        used_specialist_slot_ids=(first, second),
        retain_final_judge=True,
    )

    for repeated_selection in ((first, second), (first,)):
        before = (instance.snapshot(), instance.audit_events())
        with pytest.raises(BudgetLedgerError) as captured:
            instance.release_unused_refinement_members(
                expected_snapshot_sequence=instance.snapshot().sequence,
                bundle_id=bundle.bundle_id,
                used_specialist_slot_ids=repeated_selection,
                retain_final_judge=True,
            )
        assert captured.value.code is Phase2FailureCode.BUDGET_SLOT_ALREADY_CONSUMED
        assert (instance.snapshot(), instance.audit_events()) == before


def test_no_refinement_releases_unused_bundle_members() -> None:
    instance = ledger()
    bundle, _ = refinement_bundle(instance)
    before_judge = (instance.snapshot(), instance.audit_events())
    with pytest.raises(BudgetLedgerError) as captured:
        instance.release_unused_refinement_members(
            expected_snapshot_sequence=instance.snapshot().sequence,
            bundle_id=bundle.bundle_id,
            used_specialist_slot_ids=(),
            retain_final_judge=False,
        )
    assert captured.value.code is Phase2FailureCode.BUDGET_SLOT_ALREADY_CONSUMED
    assert (instance.snapshot(), instance.audit_events()) == before_judge

    charge_bundle_first_judge(instance, bundle.first_judge_capacity_slot_id)
    updated, snapshot = instance.release_unused_refinement_members(
        expected_snapshot_sequence=instance.snapshot().sequence,
        bundle_id=bundle.bundle_id,
        used_specialist_slot_ids=(),
        retain_final_judge=False,
    )
    assert updated.status is ConditionalRefinementBundleStatus.RELEASED
    for slot_id in (
        *bundle.specialist_capacity_slot_ids,
        bundle.final_judge_capacity_slot_id,
    ):
        assert instance.capacity_slot(slot_id).status is CapacitySlotStatus.RELEASED
    assert bundle.first_judge_capacity_slot_id not in snapshot.active_capacity_slot_ids
    assert snapshot.charged_model_calls == 1


def test_conditional_bundle_completes_only_after_consumed_members_finish() -> None:
    instance = ledger()
    bundle, _ = refinement_bundle(instance)

    charge_bundle_first_judge(instance, bundle.first_judge_capacity_slot_id)

    specialist_slot_id = bundle.specialist_capacity_slot_ids[0]
    authorization, _ = materialize_metrics(instance, specialist_slot_id)
    instance.release_unused_refinement_members(
        expected_snapshot_sequence=instance.snapshot().sequence,
        bundle_id=bundle.bundle_id,
        used_specialist_slot_ids=(specialist_slot_id,),
        retain_final_judge=True,
    )
    authorization = claim_and_complete(instance, authorization)[0]
    specialist_lease, _ = expand(
        instance,
        authorization.authorization_id,
        exact_input_tokens=SPECIALIST_INPUT,
        minimum_completion_tokens=SPECIALIST_MINIMUM_COMPLETION,
    )
    instance.charge_exact_model_lease(
        expected_snapshot_sequence=instance.snapshot().sequence,
        lease_id=specialist_lease.lease_id,
        owner_role=BudgetOwnerRole.METRICS_AGENT,
        owner_node_id="node-metrics-001",
        source_record_id=authorization.authorization_id,
        input_tokens=SPECIALIST_INPUT,
        output_tokens=SPECIALIST_MINIMUM_COMPLETION,
        total_tokens=SPECIALIST_FLOOR,
    )
    instance.complete_specialist_authorization(
        expected_snapshot_sequence=instance.snapshot().sequence,
        authorization_id=authorization.authorization_id,
    )

    final_judge_lease, _ = expand(
        instance,
        bundle.final_judge_capacity_slot_id,
        exact_input_tokens=FINAL_JUDGE_INPUT,
        minimum_completion_tokens=FINAL_JUDGE_MINIMUM_COMPLETION,
    )
    before_final_charge = (instance.snapshot(), instance.audit_events())
    with pytest.raises(BudgetLedgerError) as captured:
        instance.complete_conditional_refinement_bundle(
            expected_snapshot_sequence=instance.snapshot().sequence,
            bundle_id=bundle.bundle_id,
        )
    assert captured.value.code is Phase2FailureCode.BUDGET_SLOT_ALREADY_CONSUMED
    assert (instance.snapshot(), instance.audit_events()) == before_final_charge

    instance.charge_exact_model_lease(
        expected_snapshot_sequence=instance.snapshot().sequence,
        lease_id=final_judge_lease.lease_id,
        owner_role=BudgetOwnerRole.RCA_JUDGE,
        owner_node_id=None,
        source_record_id=bundle.final_judge_capacity_slot_id,
        input_tokens=FINAL_JUDGE_INPUT,
        output_tokens=FINAL_JUDGE_MINIMUM_COMPLETION,
        total_tokens=FINAL_JUDGE_FLOOR,
    )
    completed, snapshot = instance.complete_conditional_refinement_bundle(
        expected_snapshot_sequence=instance.snapshot().sequence,
        bundle_id=bundle.bundle_id,
    )
    assert completed.status is ConditionalRefinementBundleStatus.COMPLETED
    assert snapshot.sequence == instance.audit_events()[-1].snapshot_sequence


def test_exact_charge_is_idempotent_and_conflicting_duplicate_is_terminal() -> None:
    instance = ledger()
    slot, _ = hold_one(instance, commander_request())
    lease, _ = expand(
        instance,
        slot.slot_id,
        exact_input_tokens=COMMANDER_INPUT,
        minimum_completion_tokens=COMMANDER_MINIMUM_COMPLETION,
    )
    kwargs = {
        "lease_id": lease.lease_id,
        "owner_role": BudgetOwnerRole.INCIDENT_COMMANDER,
        "owner_node_id": None,
        "source_record_id": slot.slot_id,
        "input_tokens": COMMANDER_INPUT,
        "output_tokens": COMMANDER_MINIMUM_COMPLETION,
        "total_tokens": COMMANDER_FLOOR,
    }
    first, first_snapshot = instance.charge_exact_model_lease(
        expected_snapshot_sequence=instance.snapshot().sequence,
        **kwargs,
    )
    assert instance.terminal_failure_code is None
    duplicate, duplicate_snapshot = instance.charge_exact_model_lease(
        expected_snapshot_sequence=first_snapshot.sequence,
        **kwargs,
    )
    assert duplicate == first
    assert duplicate_snapshot == first_snapshot

    with pytest.raises(BudgetLedgerError) as captured:
        instance.charge_exact_model_lease(
            expected_snapshot_sequence=instance.snapshot().sequence,
            **{**kwargs, "output_tokens": 102, "total_tokens": 503},
        )
    assert captured.value.code is Phase2FailureCode.PROVIDER_USAGE_INCONSISTENT
    assert instance.terminal_failure_code is Phase2FailureCode.PROVIDER_USAGE_INCONSISTENT


@pytest.mark.parametrize(
    ("usage", "code"),
    (
        (
            {"input_tokens": None, "output_tokens": 1, "total_tokens": 1},
            Phase2FailureCode.PROVIDER_USAGE_MISSING,
        ),
        (
            {
                "input_tokens": COMMANDER_INPUT,
                "output_tokens": COMMANDER_MINIMUM_COMPLETION,
                "total_tokens": COMMANDER_FLOOR + 1,
            },
            Phase2FailureCode.PROVIDER_USAGE_INCONSISTENT,
        ),
        (
            {
                "input_tokens": COMMANDER_INPUT,
                "output_tokens": 32_000,
                "total_tokens": 32_000 + COMMANDER_INPUT,
            },
            Phase2FailureCode.PROVIDER_USAGE_EXCEEDS_LEASE,
        ),
    ),
)
def test_provider_usage_failure_is_recorded_terminal_without_reducing_usage(
    usage: dict[str, object],
    code: Phase2FailureCode,
) -> None:
    instance = ledger()
    slot, _ = hold_one(instance, commander_request())
    lease, _ = expand(
        instance,
        slot.slot_id,
        exact_input_tokens=COMMANDER_INPUT,
        minimum_completion_tokens=COMMANDER_MINIMUM_COMPLETION,
    )
    prior_usage = (
        instance.snapshot().charged_model_calls,
        instance.snapshot().cumulative_tokens,
    )
    with pytest.raises(BudgetLedgerError) as captured:
        instance.charge_exact_model_lease(
            expected_snapshot_sequence=instance.snapshot().sequence,
            lease_id=lease.lease_id,
            owner_role=BudgetOwnerRole.INCIDENT_COMMANDER,
            owner_node_id=None,
            source_record_id=slot.slot_id,
            **usage,  # type: ignore[arg-type]
        )
    assert captured.value.code is code
    assert instance.terminal_failure_code is code
    assert (
        instance.snapshot().charged_model_calls,
        instance.snapshot().cumulative_tokens,
    ) == prior_usage
    assert instance.audit_events()[-1].failure_code is code

    terminal_snapshot = instance.snapshot()
    with pytest.raises(BudgetLedgerError) as blocked:
        instance.hold_capacity_slots(
            expected_snapshot_sequence=terminal_snapshot.sequence,
            requests=(specialist_request(),),
        )
    assert blocked.value.code is code
    assert instance.snapshot() == terminal_snapshot


def test_charge_rejects_owner_source_and_exact_input_transfer_as_terminal() -> None:
    instance = ledger()
    authorization = charged_metrics_authorization(instance)
    lease, _ = expand(
        instance,
        authorization.authorization_id,
        exact_input_tokens=SPECIALIST_INPUT,
        minimum_completion_tokens=SPECIALIST_MINIMUM_COMPLETION,
    )
    with pytest.raises(BudgetLedgerError) as captured:
        instance.charge_exact_model_lease(
            expected_snapshot_sequence=instance.snapshot().sequence,
            lease_id=lease.lease_id,
            owner_role=BudgetOwnerRole.LOGS_AGENT,
            owner_node_id="node-logs-001",
            source_record_id="other-source",
            input_tokens=SPECIALIST_INPUT + 1,
            output_tokens=100,
            total_tokens=604,
        )
    assert captured.value.code is Phase2FailureCode.PROVIDER_USAGE_INCONSISTENT
    assert instance.terminal_failure_code is Phase2FailureCode.PROVIDER_USAGE_INCONSISTENT


def test_return_and_expire_model_lease_release_unused_exact_reservation() -> None:
    instance = ledger()
    slot, _ = hold_one(instance, commander_request())
    lease, _ = expand(
        instance,
        slot.slot_id,
        exact_input_tokens=COMMANDER_INPUT,
        minimum_completion_tokens=COMMANDER_MINIMUM_COMPLETION,
    )
    returned, snapshot = instance.return_exact_model_lease(
        expected_snapshot_sequence=instance.snapshot().sequence,
        lease_id=lease.lease_id,
        owner_role=BudgetOwnerRole.INCIDENT_COMMANDER,
        owner_node_id=None,
        source_record_id=slot.slot_id,
    )
    assert returned.status is BudgetLeaseStatus.RETURNED
    assert snapshot.remaining_tokens == 32_000
    duplicate, duplicate_snapshot = instance.return_exact_model_lease(
        expected_snapshot_sequence=snapshot.sequence,
        lease_id=lease.lease_id,
        owner_role=BudgetOwnerRole.INCIDENT_COMMANDER,
        owner_node_id=None,
        source_record_id=slot.slot_id,
    )
    assert duplicate == returned
    assert duplicate_snapshot == snapshot

    expiring = ledger(current_time=(current_time := [NOW]))
    expiring_slot, _ = hold_one(
        expiring,
        request(
            ModelOperation.COMMANDER_MODEL,
            ModelAllowedActions.PLAN_ONLY,
            COMMANDER_FLOOR,
            expires_at=NOW + timedelta(seconds=1),
        ),
    )
    expiring_lease, _ = expand(
        expiring,
        expiring_slot.slot_id,
        exact_input_tokens=COMMANDER_INPUT,
        minimum_completion_tokens=COMMANDER_MINIMUM_COMPLETION,
    )
    current_time[0] = NOW + timedelta(seconds=2)
    expired, expired_snapshot = expiring.expire_model_lease(
        expected_snapshot_sequence=expiring.snapshot().sequence,
        lease_id=expiring_lease.lease_id,
    )
    assert expired.status is BudgetLeaseStatus.EXPIRED
    assert expired_snapshot.remaining_tokens == 32_000


def test_deterministic_concurrent_cas_has_exactly_one_winner() -> None:
    instance = ledger()

    def attempt() -> str:
        try:
            slots, _ = instance.hold_capacity_slots(
                expected_snapshot_sequence=0,
                requests=(specialist_request(),),
            )
        except BudgetLedgerError as error:
            return error.code.value
        return slots[0].slot_id

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = tuple(executor.map(lambda _: attempt(), range(2)))
    assert sum(item.startswith("slot-") for item in outcomes) == 1
    assert outcomes.count(Phase2FailureCode.BUDGET_CAS_CONFLICT.value) == 1
    assert instance.snapshot().sequence == 1
    assert len(instance.audit_events()) == 1


def test_explicit_cumulative_overflow_failure_is_terminal_and_immutable() -> None:
    instance = ledger()
    failed_snapshot = instance.record_terminal_failure(
        expected_snapshot_sequence=0,
        code=Phase2FailureCode.BUDGET_CUMULATIVE_OVERFLOW,
    )
    assert failed_snapshot.sequence == 1
    assert instance.terminal_failure_code is Phase2FailureCode.BUDGET_CUMULATIVE_OVERFLOW
    assert instance.audit_events()[-1].failure_code is Phase2FailureCode.BUDGET_CUMULATIVE_OVERFLOW

    before = (instance.snapshot(), instance.audit_events())
    with pytest.raises(BudgetLedgerError) as captured:
        instance.hold_capacity_slots(
            expected_snapshot_sequence=failed_snapshot.sequence,
            requests=(specialist_request(),),
        )
    assert captured.value.code is Phase2FailureCode.BUDGET_CUMULATIVE_OVERFLOW
    assert (instance.snapshot(), instance.audit_events()) == before


def test_records_audit_and_snapshots_are_immutable_values() -> None:
    instance = ledger()
    slot, snapshot = hold_one(instance, commander_request())
    event = instance.audit_events()[0]
    with pytest.raises(ValidationError):
        slot.status = CapacitySlotStatus.RELEASED  # type: ignore[misc]
    with pytest.raises(ValidationError):
        snapshot.sequence = 99  # type: ignore[misc]
    with pytest.raises(ValidationError):
        event.event_type = "forged"  # type: ignore[misc]


def test_outer_caps_are_exact_and_no_pool_is_shared() -> None:
    with pytest.raises(BudgetLedgerError) as captured:
        BudgetLedger(
            run_id=RUN_ID,
            variant=Phase2Variant.DYNAMIC_MULTI_AGENT,
            case_id=CASE_ID,
            max_model_calls=9,
            max_tool_calls=8,
            max_total_tokens=32_000,
            utc_clock=lambda: NOW,
        )
    assert captured.value.code is Phase2FailureCode.BUDGET_CUMULATIVE_OVERFLOW

    first = ledger()
    second = ledger()
    hold_one(first, specialist_request())
    assert first.snapshot().reserved_model_calls == 1
    assert second.snapshot().reserved_model_calls == 0
    assert second.snapshot().remaining_tokens == 32_000


def test_single_agent_tool_attempt_charges_before_dispatch_once() -> None:
    instance = ledger(variant=Phase2Variant.SINGLE_AGENT)

    snapshot = instance.charge_single_agent_tool_attempt(
        expected_snapshot_sequence=0,
        attempt_id="tool-call-0001",
        tool_name=ReadOnlyToolName.QUERY_METRICS,
    )

    assert snapshot.charged_tool_calls == 1
    assert snapshot.remaining_tool_calls == 7
    assert instance.audit_events()[-1].record_ids == ("tool-call-0001",)
    before = (instance.snapshot(), instance.audit_events())
    with pytest.raises(BudgetLedgerError) as captured:
        instance.charge_single_agent_tool_attempt(
            expected_snapshot_sequence=snapshot.sequence,
            attempt_id="tool-call-0001",
            tool_name=ReadOnlyToolName.QUERY_METRICS,
        )
    assert captured.value.code is Phase2FailureCode.BUDGET_SLOT_ALREADY_CONSUMED
    assert (instance.snapshot(), instance.audit_events()) == before


def test_direct_tool_attempt_charge_is_rejected_for_multi_agent_variants() -> None:
    instance = ledger()
    before = (instance.snapshot(), instance.audit_events())

    with pytest.raises(BudgetLedgerError) as captured:
        instance.charge_single_agent_tool_attempt(
            expected_snapshot_sequence=0,
            attempt_id="tool-call-0001",
            tool_name=ReadOnlyToolName.QUERY_METRICS,
        )

    assert captured.value.code is Phase2FailureCode.BUDGET_SLOT_OWNER_MISMATCH
    assert (instance.snapshot(), instance.audit_events()) == before


@pytest.mark.parametrize("callback_kind", ("utc", "id"))
def test_callback_reentry_is_rejected_without_inner_mutation(
    callback_kind: str,
) -> None:
    utc_clock = ReentrantUtcClock()
    id_factory = ReentrantIds()
    instance = BudgetLedger(
        run_id=RUN_ID,
        variant=Phase2Variant.DYNAMIC_MULTI_AGENT,
        case_id=CASE_ID,
        max_model_calls=8,
        max_tool_calls=8,
        max_total_tokens=32_000,
        id_factory=id_factory,
        monotonic_clock=lambda: 10.0,
        utc_clock=utc_clock,
    )
    callback = utc_clock if callback_kind == "utc" else id_factory
    callback.ledger = instance

    slots, snapshot = instance.hold_capacity_slots(
        expected_snapshot_sequence=0,
        requests=(commander_request(),),
    )
    assert callback.error_codes == [Phase2FailureCode.BUDGET_CAS_CONFLICT]
    assert snapshot.sequence == 1
    assert instance.capacity_slot_ids() == (slots[0].slot_id,)
    assert len(instance.audit_events()) == 1
    assert instance.audit_events()[0].snapshot_sequence == 1


@pytest.mark.parametrize("callback_kind", ("utc", "monotonic", "id"))
def test_callback_exception_is_typed_and_rolls_back(callback_kind: str) -> None:
    utc_clock = ToggleFailureCallback(NOW)
    monotonic_clock = ToggleFailureCallback(10.0)
    id_factory = ToggleFailureCallback("generated-id")
    selected = {
        "utc": utc_clock,
        "monotonic": monotonic_clock,
        "id": id_factory,
    }[callback_kind]
    instance = BudgetLedger(
        run_id=RUN_ID,
        variant=Phase2Variant.DYNAMIC_MULTI_AGENT,
        case_id=CASE_ID,
        max_model_calls=8,
        max_tool_calls=8,
        max_total_tokens=32_000,
        id_factory=(id_factory if callback_kind == "id" else DeterministicIds()),
        monotonic_clock=monotonic_clock,
        utc_clock=utc_clock,
    )
    selected.fail = True
    before = (instance.snapshot(), instance.audit_events(), instance.capacity_slot_ids())
    with pytest.raises(BudgetLedgerError) as captured:
        instance.hold_capacity_slots(
            expected_snapshot_sequence=0,
            requests=(commander_request(),),
        )
    assert captured.value.code is Phase2FailureCode.BUDGET_SLOT_STALE
    assert (
        instance.snapshot(),
        instance.audit_events(),
        instance.capacity_slot_ids(),
    ) == before
    assert instance.terminal_failure_code is None


def test_provider_failure_remains_terminal_when_audit_callback_fails() -> None:
    utc_clock = ToggleFailureCallback(NOW)
    instance = BudgetLedger(
        run_id=RUN_ID,
        variant=Phase2Variant.DYNAMIC_MULTI_AGENT,
        case_id=CASE_ID,
        max_model_calls=8,
        max_tool_calls=8,
        max_total_tokens=32_000,
        id_factory=DeterministicIds(),
        monotonic_clock=lambda: 10.0,
        utc_clock=utc_clock,
    )
    slot, _ = hold_one(instance, commander_request())
    lease, _ = instance.expand_exact_model_lease(
        expected_snapshot_sequence=instance.snapshot().sequence,
        source_record_id=slot.slot_id,
        exact_input_tokens=COMMANDER_INPUT,
        minimum_completion_tokens=COMMANDER_MINIMUM_COMPLETION,
        max_completion_tokens=500,
    )
    before = (instance.snapshot(), instance.audit_events())
    utc_clock.fail = True
    with pytest.raises(BudgetLedgerError) as captured:
        instance.charge_exact_model_lease(
            expected_snapshot_sequence=instance.snapshot().sequence,
            lease_id=lease.lease_id,
            owner_role=BudgetOwnerRole.INCIDENT_COMMANDER,
            owner_node_id=None,
            source_record_id=slot.slot_id,
            input_tokens=None,
            output_tokens=1,
            total_tokens=1,
        )
    assert captured.value.code is Phase2FailureCode.PROVIDER_USAGE_MISSING
    assert instance.terminal_failure_code is Phase2FailureCode.PROVIDER_USAGE_MISSING
    assert (instance.snapshot(), instance.audit_events()) == before

    with pytest.raises(BudgetLedgerError) as blocked:
        instance.return_exact_model_lease(
            expected_snapshot_sequence=instance.snapshot().sequence,
            lease_id=lease.lease_id,
            owner_role=BudgetOwnerRole.INCIDENT_COMMANDER,
            owner_node_id=None,
            source_record_id=slot.slot_id,
        )
    assert blocked.value.code is Phase2FailureCode.PROVIDER_USAGE_MISSING


def test_active_bundle_rejects_raw_holds_and_second_bundle_atomically() -> None:
    raw = ledger()
    refinement_bundle(raw)
    raw_before = (raw.snapshot(), raw.audit_events(), raw.capacity_slot_ids())
    with pytest.raises(BudgetLedgerError) as raw_error:
        raw.hold_capacity_slots(
            expected_snapshot_sequence=raw.snapshot().sequence,
            requests=(commander_request(),),
        )
    assert raw_error.value.code is Phase2FailureCode.BUDGET_SLOT_ALREADY_CONSUMED
    assert (raw.snapshot(), raw.audit_events(), raw.capacity_slot_ids()) == raw_before

    duplicate = ledger()
    initial, _ = duplicate.initialize_dynamic(
        expected_snapshot_sequence=0,
        commander=commander_request(),
        specialist=specialist_request(),
        first_judge=first_judge_request(),
    )
    duplicate.release_capacity_slot(
        expected_snapshot_sequence=duplicate.snapshot().sequence,
        slot_id=initial[0].slot_id,
    )
    duplicate.release_capacity_slot(
        expected_snapshot_sequence=duplicate.snapshot().sequence,
        slot_id=initial[1].slot_id,
    )
    second_judge, _ = hold_one(duplicate, first_judge_request())
    duplicate.replace_first_judge_with_conditional_bundle(
        expected_snapshot_sequence=duplicate.snapshot().sequence,
        replaced_first_judge_slot_id=initial[2].slot_id,
        first_judge=first_judge_request(conditional=True),
        specialists=(specialist_request(), specialist_request()),
        final_judge=final_judge_request(),
    )
    duplicate_before = (
        duplicate.snapshot(),
        duplicate.audit_events(),
        duplicate.capacity_slot_ids(),
    )
    with pytest.raises(BudgetLedgerError) as duplicate_error:
        duplicate.replace_first_judge_with_conditional_bundle(
            expected_snapshot_sequence=duplicate.snapshot().sequence,
            replaced_first_judge_slot_id=second_judge.slot_id,
            first_judge=first_judge_request(conditional=True),
            specialists=(specialist_request(), specialist_request()),
            final_judge=final_judge_request(),
        )
    assert (
        duplicate_error.value.code
        is Phase2FailureCode.BUDGET_SLOT_ALREADY_CONSUMED
    )
    assert (
        duplicate.snapshot(),
        duplicate.audit_events(),
        duplicate.capacity_slot_ids(),
    ) == duplicate_before


def test_returning_bundle_first_judge_lease_atomically_aborts_bundle() -> None:
    instance = ledger()
    bundle, _ = refinement_bundle(instance)
    lease, _ = expand(
        instance,
        bundle.first_judge_capacity_slot_id,
        exact_input_tokens=FIRST_JUDGE_INPUT,
        minimum_completion_tokens=FIRST_JUDGE_MINIMUM_COMPLETION,
    )
    before_sequence = instance.snapshot().sequence
    before_events = len(instance.audit_events())
    returned, snapshot = instance.return_exact_model_lease(
        expected_snapshot_sequence=before_sequence,
        lease_id=lease.lease_id,
        owner_role=BudgetOwnerRole.RCA_JUDGE,
        owner_node_id=None,
        source_record_id=bundle.first_judge_capacity_slot_id,
    )
    assert returned.status is BudgetLeaseStatus.RETURNED
    assert instance.conditional_bundle(
        bundle.bundle_id
    ).status is ConditionalRefinementBundleStatus.RELEASED
    for slot_id in (
        *bundle.specialist_capacity_slot_ids,
        bundle.final_judge_capacity_slot_id,
    ):
        assert instance.capacity_slot(slot_id).status is CapacitySlotStatus.RELEASED
    assert snapshot.sequence == before_sequence + 1
    assert len(instance.audit_events()) == before_events + 1
    assert {
        lease.lease_id,
        bundle.bundle_id,
        *bundle.specialist_capacity_slot_ids,
        bundle.final_judge_capacity_slot_id,
    }.issubset(instance.audit_events()[-1].record_ids)


def test_expiring_bundle_member_lease_atomically_aborts_bundle() -> None:
    current_time = [NOW]
    instance = ledger(current_time=current_time)
    bundle, _ = refinement_bundle(instance)
    lease, _ = expand(
        instance,
        bundle.first_judge_capacity_slot_id,
        exact_input_tokens=FIRST_JUDGE_INPUT,
        minimum_completion_tokens=FIRST_JUDGE_MINIMUM_COMPLETION,
    )
    current_time[0] = NOW + timedelta(minutes=6)
    expired, snapshot = instance.expire_model_lease(
        expected_snapshot_sequence=instance.snapshot().sequence,
        lease_id=lease.lease_id,
    )
    assert expired.status is BudgetLeaseStatus.EXPIRED
    assert instance.conditional_bundle(
        bundle.bundle_id
    ).status is ConditionalRefinementBundleStatus.RELEASED
    assert snapshot.active_lease_ids == ()
    assert bundle.final_judge_capacity_slot_id not in snapshot.active_capacity_slot_ids


def test_failing_bundle_specialist_atomically_aborts_bundle() -> None:
    instance = ledger()
    bundle, _ = refinement_bundle(instance)
    charge_bundle_first_judge(instance, bundle.first_judge_capacity_slot_id)
    slot_id = bundle.specialist_capacity_slot_ids[0]
    authorization, _ = materialize_metrics(instance, slot_id)
    instance.release_unused_refinement_members(
        expected_snapshot_sequence=instance.snapshot().sequence,
        bundle_id=bundle.bundle_id,
        used_specialist_slot_ids=(slot_id,),
        retain_final_judge=True,
    )
    authorization = claim_and_complete(instance, authorization)[0]
    before_sequence = instance.snapshot().sequence
    failed, snapshot = instance.fail_specialist_authorization(
        expected_snapshot_sequence=before_sequence,
        authorization_id=authorization.authorization_id,
    )
    assert failed.status is SpecialistAuthorizationStatus.FAILED
    assert instance.conditional_bundle(
        bundle.bundle_id
    ).status is ConditionalRefinementBundleStatus.RELEASED
    assert instance.capacity_slot(
        bundle.final_judge_capacity_slot_id
    ).status is CapacitySlotStatus.RELEASED
    assert snapshot.sequence == before_sequence + 1
    assert {
        authorization.authorization_id,
        bundle.bundle_id,
        bundle.final_judge_capacity_slot_id,
    }.issubset(instance.audit_events()[-1].record_ids)


def test_audit_derives_all_changed_record_ids_for_multi_record_transactions() -> None:
    failed_instance = ledger()
    failed_authorization = charged_metrics_authorization(failed_instance)
    failed_lease, _ = expand(
        failed_instance,
        failed_authorization.authorization_id,
        exact_input_tokens=SPECIALIST_INPUT,
        minimum_completion_tokens=SPECIALIST_MINIMUM_COMPLETION,
    )
    before_sequence = failed_instance.snapshot().sequence
    before_events = len(failed_instance.audit_events())
    failed_instance.fail_specialist_authorization(
        expected_snapshot_sequence=before_sequence,
        authorization_id=failed_authorization.authorization_id,
    )
    assert failed_instance.snapshot().sequence == before_sequence + 1
    assert len(failed_instance.audit_events()) == before_events + 1
    assert {
        failed_authorization.authorization_id,
        failed_lease.lease_id,
    }.issubset(failed_instance.audit_events()[-1].record_ids)

    resized = ledger()
    commander, bootstrap, judge = dynamic_after_commander_charge(resized)
    resized_slots, _ = resized.resize_dynamic_initial_plan(
        expected_snapshot_sequence=resized.snapshot().sequence,
        commander_slot_id=commander.slot_id,
        retained_specialist_slot_ids=(bootstrap.slot_id,),
        new_specialists=(
            specialist_request(expires_at=bootstrap.expires_at),
        ),
        first_judge_slot_id=judge.slot_id,
    )
    assert resized_slots[1].slot_id in resized.audit_events()[-1].record_ids

    no_refinement = ledger()
    bundle, _ = refinement_bundle(no_refinement)
    charge_bundle_first_judge(
        no_refinement, bundle.first_judge_capacity_slot_id
    )
    no_refinement.release_unused_refinement_members(
        expected_snapshot_sequence=no_refinement.snapshot().sequence,
        bundle_id=bundle.bundle_id,
        used_specialist_slot_ids=(),
        retain_final_judge=False,
    )
    assert {
        bundle.bundle_id,
        *bundle.specialist_capacity_slot_ids,
        bundle.final_judge_capacity_slot_id,
    }.issubset(no_refinement.audit_events()[-1].record_ids)


@pytest.mark.parametrize("callback_kind", ("utc", "monotonic", "id"))
def test_valid_provider_charge_fails_closed_when_commit_callback_fails(
    callback_kind: str,
) -> None:
    utc_clock = ToggleFailureCallback(NOW)
    monotonic_clock = ToggleFailureCallback(10.0)
    id_factory = ToggleFailureIds()
    selected = {
        "utc": utc_clock,
        "monotonic": monotonic_clock,
        "id": id_factory,
    }[callback_kind]
    instance = BudgetLedger(
        run_id=RUN_ID,
        variant=Phase2Variant.DYNAMIC_MULTI_AGENT,
        case_id=CASE_ID,
        max_model_calls=8,
        max_tool_calls=8,
        max_total_tokens=32_000,
        id_factory=id_factory,
        monotonic_clock=monotonic_clock,
        utc_clock=utc_clock,
    )
    slot, _ = hold_one(instance, commander_request())
    lease, _ = expand(
        instance,
        slot.slot_id,
        exact_input_tokens=COMMANDER_INPUT,
        minimum_completion_tokens=COMMANDER_MINIMUM_COMPLETION,
    )
    before = (instance.snapshot(), instance.audit_events(), instance.model_lease(lease.lease_id))
    selected.fail = True

    with pytest.raises(BudgetLedgerError) as captured:
        instance.charge_exact_model_lease(
            expected_snapshot_sequence=instance.snapshot().sequence,
            lease_id=lease.lease_id,
            owner_role=BudgetOwnerRole.INCIDENT_COMMANDER,
            owner_node_id=None,
            source_record_id=slot.slot_id,
            input_tokens=COMMANDER_INPUT,
            output_tokens=COMMANDER_MINIMUM_COMPLETION,
            total_tokens=COMMANDER_FLOOR,
        )
    assert captured.value.code is Phase2FailureCode.BUDGET_SLOT_STALE
    assert instance.terminal_failure_code is Phase2FailureCode.BUDGET_SLOT_STALE
    assert (
        instance.snapshot(),
        instance.audit_events(),
        instance.model_lease(lease.lease_id),
    ) == before

    for mutation in (
        lambda: instance.return_exact_model_lease(
            expected_snapshot_sequence=instance.snapshot().sequence,
            lease_id=lease.lease_id,
            owner_role=BudgetOwnerRole.INCIDENT_COMMANDER,
            owner_node_id=None,
            source_record_id=slot.slot_id,
        ),
        lambda: instance.hold_capacity_slots(
            expected_snapshot_sequence=instance.snapshot().sequence,
            requests=(specialist_request(),),
        ),
    ):
        with pytest.raises(BudgetLedgerError) as blocked:
            mutation()
        assert blocked.value.code is Phase2FailureCode.BUDGET_SLOT_STALE


@pytest.mark.parametrize(
    "terminal_status",
    (
        ConditionalRefinementBundleStatus.RELEASED,
        ConditionalRefinementBundleStatus.COMPLETED,
    ),
)
def test_terminal_bundle_history_rejects_later_raw_holds(
    terminal_status: ConditionalRefinementBundleStatus,
) -> None:
    instance, bundle, _spare = terminal_bundle_with_spare_first_judge(
        terminal_status
    )
    assert instance.conditional_bundle(bundle.bundle_id).status is terminal_status
    before = (instance.snapshot(), instance.audit_events(), instance.capacity_slot_ids())
    with pytest.raises(BudgetLedgerError) as captured:
        instance.hold_capacity_slots(
            expected_snapshot_sequence=instance.snapshot().sequence,
            requests=(specialist_request(),),
        )
    assert captured.value.code is Phase2FailureCode.BUDGET_SLOT_ALREADY_CONSUMED
    assert (instance.snapshot(), instance.audit_events(), instance.capacity_slot_ids()) == before


@pytest.mark.parametrize(
    "terminal_status",
    (
        ConditionalRefinementBundleStatus.RELEASED,
        ConditionalRefinementBundleStatus.COMPLETED,
    ),
)
def test_terminal_bundle_history_rejects_second_bundle_creation(
    terminal_status: ConditionalRefinementBundleStatus,
) -> None:
    instance, bundle, spare = terminal_bundle_with_spare_first_judge(terminal_status)
    assert instance.conditional_bundle(bundle.bundle_id).status is terminal_status
    before = (instance.snapshot(), instance.audit_events(), instance.capacity_slot_ids())
    with pytest.raises(BudgetLedgerError) as captured:
        instance.replace_first_judge_with_conditional_bundle(
            expected_snapshot_sequence=instance.snapshot().sequence,
            replaced_first_judge_slot_id=spare.slot_id,
            first_judge=first_judge_request(conditional=True),
            specialists=(specialist_request(), specialist_request()),
            final_judge=final_judge_request(),
        )
    assert captured.value.code is Phase2FailureCode.BUDGET_SLOT_ALREADY_CONSUMED
    assert (instance.snapshot(), instance.audit_events(), instance.capacity_slot_ids()) == before
