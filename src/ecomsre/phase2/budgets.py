"""Atomic run-local central-pool accounting for Phase 2 calls."""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from datetime import datetime
from threading import RLock
from time import monotonic
from typing import Literal, Never, cast

from pydantic import ValidationError

from ecomsre.phase1.contracts import EvidenceSource, ReadOnlyToolName
from ecomsre.phase2.contracts import (
    BudgetAuditEvent,
    BudgetLease,
    BudgetLeaseStatus,
    BudgetOwnerRole,
    BudgetSnapshot,
    CapacitySlotRequest,
    CapacitySlotStatus,
    ConditionalRefinementBundle,
    ConditionalRefinementBundleStatus,
    COMPARISON_MAX_TOTAL_TOKENS,
    ModelAllowedActions,
    ModelOperation,
    Phase2FailureCode,
    Phase2Variant,
    SPECIALIST_TOOL_BINDINGS,
    SpecialistAuthorizationStatus,
    SpecialistExecutionAuthorization,
    SpecialistRole,
    SpecialistToolOutcomeKind,
    SpecialistToolOutcomeReceipt,
    UnboundCapacitySlot,
)


_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_SPECIALIST_BINDINGS: dict[
    EvidenceSource, tuple[SpecialistRole, BudgetOwnerRole, ReadOnlyToolName]
] = {
    source: (role, owner_role, tool_name)
    for role, (owner_role, source, tool_name) in SPECIALIST_TOOL_BINDINGS.items()
}
_OPEN_AUTHORIZATION_STATUSES = {
    SpecialistAuthorizationStatus.TOOL_AUTHORIZED,
    SpecialistAuthorizationStatus.TOOL_DISPATCHING,
    SpecialistAuthorizationStatus.TOOL_CHARGED,
    SpecialistAuthorizationStatus.MODEL_LEASED,
}
_PRE_LEASE_ACTIVE_AUTHORIZATION_STATUSES = {
    SpecialistAuthorizationStatus.TOOL_AUTHORIZED,
    SpecialistAuthorizationStatus.TOOL_DISPATCHING,
    SpecialistAuthorizationStatus.TOOL_CHARGED,
}


class BudgetLedgerError(ValueError):
    """Stable fail-closed error using the shared Phase 2 code space."""

    def __init__(self, code: Phase2FailureCode, detail: str) -> None:
        self.code = code
        super().__init__(f"{code.value}: {detail}")


class BudgetLedger:
    """Serialize all slot, authorization, lease, and failure transitions."""

    def __init__(
        self,
        *,
        run_id: str,
        variant: Phase2Variant,
        case_id: str,
        max_model_calls: int,
        max_tool_calls: int,
        max_total_tokens: int,
        id_factory: Callable[[str], str] | None = None,
        monotonic_clock: Callable[[], float] = monotonic,
        utc_clock: Callable[[], datetime],
    ) -> None:
        caps = (max_model_calls, max_tool_calls, max_total_tokens)
        if any(type(value) is not int for value in caps) or caps != (
            8,
            8,
            COMPARISON_MAX_TOTAL_TOKENS,
        ):
            raise BudgetLedgerError(
                Phase2FailureCode.BUDGET_CUMULATIVE_OVERFLOW,
                "outer caps must be exact integers equal to 8 / 8 / 32000",
            )
        if not isinstance(variant, Phase2Variant):
            raise BudgetLedgerError(
                Phase2FailureCode.BUDGET_SLOT_STALE,
                "variant is not a closed Phase2Variant",
            )
        self._validate_identifier(case_id)
        if type(run_id) is not str or not re.fullmatch(r"[0-9a-f]{32}", run_id):
            raise BudgetLedgerError(
                Phase2FailureCode.BUDGET_SLOT_STALE,
                "run ID is not a canonical Phase 2 run ID",
            )

        self._lock = RLock()
        self._run_id = run_id
        self._variant = variant
        self._case_id = case_id
        self._max_model_calls = max_model_calls
        self._max_tool_calls = max_tool_calls
        self._max_total_tokens = max_total_tokens
        self._id_factory = id_factory
        self._monotonic_clock = monotonic_clock
        self._utc_clock = utc_clock
        self._callback_active = False
        monotonic_origin = self._invoke_callback(
            monotonic_clock,
            detail="monotonic clock failed during initialization",
        )
        if type(monotonic_origin) not in {int, float}:
            raise BudgetLedgerError(
                Phase2FailureCode.BUDGET_SLOT_STALE,
                "monotonic clock did not return a number",
            )
        self._monotonic_origin = cast(int | float, monotonic_origin)

        self._sequence = 0
        self._last_elapsed_seconds = 0.0
        self._charged_model_calls = 0
        self._charged_tool_calls = 0
        self._cumulative_tokens = 0
        self._slots: dict[str, UnboundCapacitySlot] = {}
        self._authorizations: dict[str, SpecialistExecutionAuthorization] = {}
        self._outcome_receipts: dict[
            tuple[str, int], SpecialistToolOutcomeReceipt
        ] = {}
        self._leases: dict[str, BudgetLease] = {}
        self._bundles: dict[str, ConditionalRefinementBundle] = {}
        self._charge_signatures: dict[
            str,
            tuple[BudgetOwnerRole, str | None, str, int, int, int],
        ] = {}
        self._audit_events: tuple[BudgetAuditEvent, ...] = ()
        self._terminal_failure_code: Phase2FailureCode | None = None
        self._used_ids: set[str] = set()

        snapshot_id = self._new_id("snapshot", self._used_ids)
        try:
            self._snapshot = self._make_snapshot(
                snapshot_id=snapshot_id,
                sequence=0,
                charged_model_calls=0,
                charged_tool_calls=0,
                cumulative_tokens=0,
                slots={},
                authorizations={},
                leases={},
                elapsed_seconds=0.0,
            )
        except (TypeError, ValidationError, ValueError) as error:
            raise BudgetLedgerError(
                Phase2FailureCode.BUDGET_SLOT_STALE,
                f"invalid ledger identity: {error}",
            ) from error
        self._used_ids.add(snapshot_id)

    @property
    def terminal_failure_code(self) -> Phase2FailureCode | None:
        with self._lock:
            return self._terminal_failure_code

    def snapshot(self) -> BudgetSnapshot:
        with self._lock:
            return self._snapshot

    def audit_events(self) -> tuple[BudgetAuditEvent, ...]:
        with self._lock:
            return self._audit_events

    def capacity_slot_ids(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(self._slots)

    def capacity_slot(self, slot_id: str) -> UnboundCapacitySlot:
        with self._lock:
            return self._require_slot(slot_id)

    def specialist_authorization(
        self, authorization_id: str
    ) -> SpecialistExecutionAuthorization:
        with self._lock:
            return self._require_authorization(authorization_id)

    def model_lease(self, lease_id: str) -> BudgetLease:
        with self._lock:
            return self._require_lease(lease_id)

    def conditional_bundle(self, bundle_id: str) -> ConditionalRefinementBundle:
        with self._lock:
            self._validate_identifier(bundle_id)
            try:
                return self._bundles[bundle_id]
            except KeyError as error:
                raise BudgetLedgerError(
                    Phase2FailureCode.BUDGET_SLOT_STALE,
                    f"unknown conditional bundle {bundle_id}",
                ) from error

    def reserved_floor_for(self, source_record_id: str) -> int:
        with self._lock:
            if source_record_id in self._slots:
                slot = self._slots[source_record_id]
                if slot.status is CapacitySlotStatus.HELD:
                    return slot.minimum_token_floor
            if source_record_id in self._authorizations:
                authorization = self._authorizations[source_record_id]
                if authorization.status in {
                    SpecialistAuthorizationStatus.TOOL_AUTHORIZED,
                    SpecialistAuthorizationStatus.TOOL_DISPATCHING,
                    SpecialistAuthorizationStatus.TOOL_CHARGED,
                }:
                    return authorization.minimum_token_floor
            raise BudgetLedgerError(
                Phase2FailureCode.BUDGET_SLOT_ALREADY_CONSUMED,
                "source record no longer owns a future minimum floor",
            )

    def charge_single_agent_tool_attempt(
        self,
        *,
        expected_snapshot_sequence: int,
        attempt_id: str,
        tool_name: ReadOnlyToolName,
    ) -> BudgetSnapshot:
        """Charge one Single-Agent backend entry before dispatch."""

        with self._lock:
            self._require_operational()
            self._require_cas(expected_snapshot_sequence)
            self._validate_identifier(attempt_id)
            if not isinstance(tool_name, ReadOnlyToolName):
                raise BudgetLedgerError(
                    Phase2FailureCode.BUDGET_SLOT_OWNER_MISMATCH,
                    "Single-Agent tool attempt requires a closed tool name",
                )
            if self._variant is not Phase2Variant.SINGLE_AGENT:
                raise BudgetLedgerError(
                    Phase2FailureCode.BUDGET_SLOT_OWNER_MISMATCH,
                    "direct tool attempt charging is Single-Agent only",
                )
            if attempt_id in self._used_ids:
                raise BudgetLedgerError(
                    Phase2FailureCode.BUDGET_SLOT_ALREADY_CONSUMED,
                    "Single-Agent tool attempt ID was already charged",
                )
            if self._charged_tool_calls + 1 > self._max_tool_calls:
                self._record_failure_and_raise(
                    Phase2FailureCode.BUDGET_CUMULATIVE_OVERFLOW,
                    "Single-Agent tool attempt exceeds the outer cap",
                    record_ids=(attempt_id,),
                )
            used_ids = set(self._used_ids)
            used_ids.add(attempt_id)
            return self._commit(
                event_type=f"single-agent-tool-{tool_name.value.lower()}",
                record_ids=(attempt_id,),
                slots=dict(self._slots),
                authorizations=dict(self._authorizations),
                leases=dict(self._leases),
                bundles=dict(self._bundles),
                charge_signatures=dict(self._charge_signatures),
                charged_model_calls=self._charged_model_calls,
                charged_tool_calls=self._charged_tool_calls + 1,
                cumulative_tokens=self._cumulative_tokens,
                terminal_failure_code=None,
                used_ids=used_ids,
            )

    def hold_capacity_slots(
        self,
        *,
        expected_snapshot_sequence: int,
        requests: Sequence[CapacitySlotRequest],
    ) -> tuple[tuple[UnboundCapacitySlot, ...], BudgetSnapshot]:
        with self._lock:
            self._require_operational()
            self._require_cas(expected_snapshot_sequence)
            if self._bundles:
                raise BudgetLedgerError(
                    Phase2FailureCode.BUDGET_SLOT_ALREADY_CONSUMED,
                    "raw capacity holds are forbidden after bundle creation",
                )
            now = self._utc_now()
            slots = dict(self._slots)
            used_ids = set(self._used_ids)
            created = self._build_slots(
                requests=requests,
                slots=slots,
                used_ids=used_ids,
                now=now,
            )
            snapshot = self._commit(
                event_type="hold-capacity-slots",
                record_ids=tuple(slot.slot_id for slot in created),
                slots=slots,
                authorizations=dict(self._authorizations),
                leases=dict(self._leases),
                bundles=dict(self._bundles),
                charge_signatures=dict(self._charge_signatures),
                charged_model_calls=self._charged_model_calls,
                charged_tool_calls=self._charged_tool_calls,
                cumulative_tokens=self._cumulative_tokens,
                terminal_failure_code=None,
                used_ids=used_ids,
            )
            return created, snapshot

    def materialize_specialist_authorization(
        self,
        *,
        expected_snapshot_sequence: int,
        slot_id: str,
        owner_role: BudgetOwnerRole,
        owner_node_id: str,
        source: EvidenceSource,
        tool_name: ReadOnlyToolName,
    ) -> tuple[SpecialistExecutionAuthorization, BudgetSnapshot]:
        with self._lock:
            self._require_operational()
            self._require_cas(expected_snapshot_sequence)
            slot = self._require_slot(slot_id)
            if slot.status is not CapacitySlotStatus.HELD:
                raise BudgetLedgerError(
                    Phase2FailureCode.BUDGET_SLOT_ALREADY_CONSUMED,
                    "capacity slot has already left HELD",
                )
            if (
                slot.permitted_operation is not ModelOperation.SPECIALIST_MODEL
                or slot.allowed_actions is not ModelAllowedActions.FINDING_ONLY
                or slot.reserved_tool_calls != 1
            ):
                raise BudgetLedgerError(
                    Phase2FailureCode.BUDGET_SLOT_OWNER_MISMATCH,
                    "only a Specialist FINDING_ONLY slot may authorize a tool",
                )
            active_bundle = self._active_bundle_for_slot(slot_id)
            if (
                active_bundle is not None
                and slot_id in active_bundle.specialist_capacity_slot_ids
                and not self._bundle_first_judge_is_charged(active_bundle)
            ):
                raise BudgetLedgerError(
                    Phase2FailureCode.BUDGET_SLOT_ALREADY_CONSUMED,
                    "bundle Specialist requires a charged first-Judge lease",
                )
            self._require_specialist_binding(
                owner_role=owner_role,
                owner_node_id=owner_node_id,
                source=source,
                tool_name=tool_name,
            )
            now = self._utc_now()
            if now >= slot.expires_at:
                raise BudgetLedgerError(
                    Phase2FailureCode.BUDGET_SLOT_STALE,
                    "capacity slot expired before materialization",
                )

            slots = dict(self._slots)
            authorizations = dict(self._authorizations)
            used_ids = set(self._used_ids)
            authorization_id = self._new_id("authorization", used_ids)
            used_ids.add(authorization_id)
            try:
                materialized = UnboundCapacitySlot.model_validate(
                    {
                        **slot.model_dump(mode="python"),
                        "status": CapacitySlotStatus.MATERIALIZED,
                    }
                )
                authorization = SpecialistExecutionAuthorization(
                    schema_version=(
                        "phase2.specialist-execution-authorization.v2"
                    ),
                    authorization_id=authorization_id,
                    capacity_slot_id=slot.slot_id,
                    run_id=self._run_id,
                    variant=self._variant,
                    case_id=self._case_id,
                    creating_snapshot_sequence=self._sequence,
                    owner_role=owner_role,
                    owner_node_id=owner_node_id,
                    source=source,
                    tool_name=tool_name,
                    permitted_operation=ModelOperation.SPECIALIST_MODEL,
                    allowed_actions=ModelAllowedActions.FINDING_ONLY,
                    reserved_model_calls=slot.reserved_model_calls,
                    reserved_tool_calls=slot.reserved_tool_calls,
                    minimum_token_floor=slot.minimum_token_floor,
                    issued_at=now,
                    expires_at=slot.expires_at,
                    status=SpecialistAuthorizationStatus.TOOL_AUTHORIZED,
                    actual_tool_calls=0,
                    model_lease_id=None,
                    dispatch_claim_snapshot_sequence=None,
                    tool_charged_snapshot_sequence=None,
                    tool_call_record_sha256=None,
                )
            except (TypeError, ValidationError, ValueError) as error:
                raise BudgetLedgerError(
                    Phase2FailureCode.BUDGET_SLOT_OWNER_MISMATCH,
                    f"invalid Specialist authorization candidate: {error}",
                ) from error
            slots[slot_id] = materialized
            authorizations[authorization_id] = authorization
            snapshot = self._commit(
                event_type="materialize-specialist-authorization",
                record_ids=(slot_id, authorization_id),
                slots=slots,
                authorizations=authorizations,
                leases=dict(self._leases),
                bundles=dict(self._bundles),
                charge_signatures=dict(self._charge_signatures),
                charged_model_calls=self._charged_model_calls,
                charged_tool_calls=self._charged_tool_calls,
                cumulative_tokens=self._cumulative_tokens,
                terminal_failure_code=None,
                used_ids=used_ids,
            )
            return authorization, snapshot

    def claim_specialist_tool_dispatch(
        self,
        *,
        expected_snapshot_sequence: int,
        authorization_id: str,
        run_id: str,
        case_id: str,
        variant: Phase2Variant,
        owner_role: BudgetOwnerRole,
        owner_node_id: str,
        source: EvidenceSource,
        tool_name: ReadOnlyToolName,
    ) -> tuple[SpecialistExecutionAuthorization, BudgetSnapshot]:
        with self._lock:
            self._require_operational()
            self._require_cas(expected_snapshot_sequence)
            authorization = self._require_authorization(authorization_id)
            if authorization.status is not SpecialistAuthorizationStatus.TOOL_AUTHORIZED:
                raise BudgetLedgerError(
                    Phase2FailureCode.BUDGET_SLOT_ALREADY_CONSUMED,
                    f"cannot claim tool in {authorization.status.value}",
                )
            if (
                self._variant
                not in {
                    Phase2Variant.FIXED_SPECIALIST_WORKFLOW,
                    Phase2Variant.DYNAMIC_MULTI_AGENT,
                }
                or run_id != self._run_id
                or case_id != self._case_id
                or variant is not self._variant
                or authorization.run_id != run_id
                or authorization.case_id != case_id
                or authorization.variant is not variant
                or authorization.owner_role is not owner_role
                or authorization.owner_node_id != owner_node_id
                or authorization.source is not source
                or authorization.tool_name is not tool_name
                or authorization.permitted_operation
                is not ModelOperation.SPECIALIST_MODEL
                or authorization.allowed_actions
                is not ModelAllowedActions.FINDING_ONLY
                or authorization.reserved_model_calls != 1
                or authorization.reserved_tool_calls != 1
                or authorization.minimum_token_floor <= 0
                or authorization.actual_tool_calls != 0
                or authorization.model_lease_id is not None
                or authorization.dispatch_claim_snapshot_sequence is not None
                or authorization.tool_charged_snapshot_sequence is not None
                or authorization.tool_call_record_sha256 is not None
            ):
                raise BudgetLedgerError(
                    Phase2FailureCode.BUDGET_SLOT_OWNER_MISMATCH,
                    "Specialist dispatch claim scope or shape is incompatible",
                )
            self._require_specialist_binding(
                owner_role=owner_role,
                owner_node_id=owner_node_id,
                source=source,
                tool_name=tool_name,
            )
            origin = self._require_slot(authorization.capacity_slot_id)
            if (
                origin.status is not CapacitySlotStatus.MATERIALIZED
                or authorization.capacity_slot_id
                in self._snapshot.active_capacity_slot_ids
                or any(
                    lease.source_record_id == authorization.authorization_id
                    and lease.status is BudgetLeaseStatus.RESERVED
                    for lease in self._leases.values()
                )
            ):
                raise BudgetLedgerError(
                    Phase2FailureCode.BUDGET_SLOT_ALREADY_CONSUMED,
                    "Specialist dispatch origin or lease lineage is not claimable",
                )
            if self._utc_now() >= authorization.expires_at:
                raise BudgetLedgerError(
                    Phase2FailureCode.BUDGET_SLOT_STALE,
                    "Specialist authorization expired before dispatch claim",
                )
            claim_sequence = self._sequence + 1
            try:
                dispatching = SpecialistExecutionAuthorization.model_validate(
                    {
                        **authorization.model_dump(mode="python"),
                        "status": SpecialistAuthorizationStatus.TOOL_DISPATCHING,
                        "dispatch_claim_snapshot_sequence": claim_sequence,
                    }
                )
            except (TypeError, ValidationError, ValueError) as error:
                raise BudgetLedgerError(
                    Phase2FailureCode.BUDGET_SLOT_ALREADY_CONSUMED,
                    f"invalid dispatch claim candidate: {error}",
                ) from error
            snapshot = self._commit(
                event_type="claim-specialist-tool-dispatch",
                record_ids=(authorization_id,),
                slots=dict(self._slots),
                authorizations={
                    **self._authorizations,
                    authorization_id: dispatching,
                },
                leases=dict(self._leases),
                bundles=dict(self._bundles),
                charge_signatures=dict(self._charge_signatures),
                charged_model_calls=self._charged_model_calls,
                charged_tool_calls=self._charged_tool_calls,
                cumulative_tokens=self._cumulative_tokens,
                terminal_failure_code=None,
                used_ids=set(self._used_ids),
            )
            return dispatching, snapshot

    def complete_specialist_tool_dispatch(
        self,
        *,
        authorization_id: str,
        dispatch_claim_snapshot_sequence: int,
        tool_call_record_sha256: str,
    ) -> SpecialistToolOutcomeReceipt:
        with self._lock:
            key = self._validate_outcome_key(
                authorization_id,
                dispatch_claim_snapshot_sequence,
            )
            existing = self._outcome_receipts.get(key)
            if existing is not None:
                if (
                    existing.outcome_kind is SpecialistToolOutcomeKind.SUCCESS
                    and existing.tool_call_record_sha256
                    == tool_call_record_sha256
                ):
                    return existing
                raise BudgetLedgerError(
                    Phase2FailureCode.BUDGET_SLOT_ALREADY_CONSUMED,
                    "Specialist dispatch already has a conflicting outcome",
                )
            self._require_no_other_outcome(authorization_id, key)
            if (
                type(tool_call_record_sha256) is not str
                or re.fullmatch(r"[0-9a-f]{64}", tool_call_record_sha256)
                is None
            ):
                raise BudgetLedgerError(
                    Phase2FailureCode.BUDGET_SLOT_OWNER_MISMATCH,
                    "tool-call record hash is not canonical lowercase SHA-256",
                )
            authorization = self._require_dispatching_authorization(
                authorization_id,
                dispatch_claim_snapshot_sequence,
            )
            charge_sequence = self._sequence + 1
            try:
                charged = SpecialistExecutionAuthorization.model_validate(
                    {
                        **authorization.model_dump(mode="python"),
                        "status": SpecialistAuthorizationStatus.TOOL_CHARGED,
                        "actual_tool_calls": 1,
                        "tool_charged_snapshot_sequence": charge_sequence,
                        "tool_call_record_sha256": tool_call_record_sha256,
                    }
                )
            except (TypeError, ValidationError, ValueError) as error:
                raise BudgetLedgerError(
                    Phase2FailureCode.BUDGET_SLOT_ALREADY_CONSUMED,
                    f"invalid successful dispatch outcome: {error}",
                ) from error
            self._commit(
                event_type="complete-specialist-tool-dispatch",
                record_ids=(authorization_id,),
                slots=dict(self._slots),
                authorizations={
                    **self._authorizations,
                    authorization_id: charged,
                },
                leases=dict(self._leases),
                bundles=dict(self._bundles),
                charge_signatures=dict(self._charge_signatures),
                charged_model_calls=self._charged_model_calls,
                charged_tool_calls=self._charged_tool_calls + 1,
                cumulative_tokens=self._cumulative_tokens,
                terminal_failure_code=self._terminal_failure_code,
                used_ids=set(self._used_ids),
                specialist_outcome=(
                    SpecialistToolOutcomeKind.SUCCESS,
                    charged,
                    tool_call_record_sha256,
                    None,
                ),
            )
            return self._outcome_receipts[key]

    def fail_specialist_tool_dispatch(
        self,
        *,
        authorization_id: str,
        dispatch_claim_snapshot_sequence: int,
        failure_kind: Literal["EXECUTOR_FAILURE", "RECORD_MISMATCH"],
    ) -> SpecialistToolOutcomeReceipt:
        with self._lock:
            key = self._validate_outcome_key(
                authorization_id,
                dispatch_claim_snapshot_sequence,
            )
            if (
                type(failure_kind) is not str
                or failure_kind not in ("EXECUTOR_FAILURE", "RECORD_MISMATCH")
            ):
                raise BudgetLedgerError(
                    Phase2FailureCode.BUDGET_SLOT_OWNER_MISMATCH,
                    "dispatch failure kind is outside the closed enum",
                )
            existing = self._outcome_receipts.get(key)
            if existing is not None:
                if (
                    existing.outcome_kind is SpecialistToolOutcomeKind.FAILURE
                    and existing.failure_kind == failure_kind
                ):
                    return existing
                raise BudgetLedgerError(
                    Phase2FailureCode.BUDGET_SLOT_ALREADY_CONSUMED,
                    "Specialist dispatch already has a conflicting outcome",
                )
            self._require_no_other_outcome(authorization_id, key)
            authorization = self._require_dispatching_authorization(
                authorization_id,
                dispatch_claim_snapshot_sequence,
            )
            try:
                failed = SpecialistExecutionAuthorization.model_validate(
                    {
                        **authorization.model_dump(mode="python"),
                        "status": SpecialistAuthorizationStatus.FAILED,
                        "actual_tool_calls": 1,
                    }
                )
            except (TypeError, ValidationError, ValueError) as error:
                raise BudgetLedgerError(
                    Phase2FailureCode.BUDGET_SLOT_ALREADY_CONSUMED,
                    f"invalid failed dispatch outcome: {error}",
                ) from error
            self._commit(
                event_type="fail-specialist-tool-dispatch",
                record_ids=(authorization_id,),
                slots=dict(self._slots),
                authorizations={
                    **self._authorizations,
                    authorization_id: failed,
                },
                leases=dict(self._leases),
                bundles=dict(self._bundles),
                charge_signatures=dict(self._charge_signatures),
                charged_model_calls=self._charged_model_calls,
                charged_tool_calls=self._charged_tool_calls + 1,
                cumulative_tokens=self._cumulative_tokens,
                terminal_failure_code=self._terminal_failure_code,
                used_ids=set(self._used_ids),
                failure_code=Phase2FailureCode.TOOL_DISPATCH_FAILED,
                specialist_outcome=(
                    SpecialistToolOutcomeKind.FAILURE,
                    failed,
                    None,
                    failure_kind,
                ),
            )
            return self._outcome_receipts[key]

    def expand_exact_model_lease(
        self,
        *,
        expected_snapshot_sequence: int,
        source_record_id: str,
        exact_input_tokens: int,
        minimum_completion_tokens: int,
        max_completion_tokens: int,
    ) -> tuple[BudgetLease, BudgetSnapshot]:
        with self._lock:
            self._require_operational()
            self._require_cas(expected_snapshot_sequence)
            self._require_exact_expansion_integers(
                exact_input_tokens=exact_input_tokens,
                minimum_completion_tokens=minimum_completion_tokens,
                max_completion_tokens=max_completion_tokens,
            )
            self._validate_identifier(source_record_id)

            slots = dict(self._slots)
            authorizations = dict(self._authorizations)
            expires_at: datetime
            owner_role: BudgetOwnerRole
            owner_node_id: str | None
            operation: ModelOperation
            allowed_actions: ModelAllowedActions
            source_authorization: SpecialistExecutionAuthorization | None = None
            source_slot: UnboundCapacitySlot | None = None
            if source_record_id in authorizations:
                source_authorization = authorizations[source_record_id]
                if (
                    source_authorization.status
                    is not SpecialistAuthorizationStatus.TOOL_CHARGED
                ):
                    raise BudgetLedgerError(
                        Phase2FailureCode.BUDGET_SLOT_ALREADY_CONSUMED,
                        "Specialist exact expansion requires TOOL_CHARGED",
                    )
                expires_at = source_authorization.expires_at
                owner_role = source_authorization.owner_role
                owner_node_id = source_authorization.owner_node_id
                operation = source_authorization.permitted_operation
                allowed_actions = source_authorization.allowed_actions
            elif source_record_id in slots:
                source_slot = slots[source_record_id]
                if source_slot.status is not CapacitySlotStatus.HELD:
                    raise BudgetLedgerError(
                        Phase2FailureCode.BUDGET_SLOT_ALREADY_CONSUMED,
                        "capacity slot has already left HELD",
                    )
                if source_slot.permitted_operation is ModelOperation.SPECIALIST_MODEL:
                    raise BudgetLedgerError(
                        Phase2FailureCode.BUDGET_SLOT_OWNER_MISMATCH,
                        "Specialist slot requires a charged execution authorization",
                    )
                expires_at = source_slot.expires_at
                operation = source_slot.permitted_operation
                allowed_actions = source_slot.allowed_actions
                owner_role, owner_node_id = self._owner_for_operation(operation)
            else:
                raise BudgetLedgerError(
                    Phase2FailureCode.BUDGET_SLOT_STALE,
                    f"unknown lease source {source_record_id}",
                )

            active_bundle = self._active_bundle_for_slot(source_record_id)
            if (
                source_slot is not None
                and active_bundle is not None
                and source_record_id
                == active_bundle.final_judge_capacity_slot_id
                and not self._bundle_final_judge_is_ready(active_bundle)
            ):
                raise BudgetLedgerError(
                    Phase2FailureCode.BUDGET_SLOT_ALREADY_CONSUMED,
                    "bundle final Judge requires completed retained refinement",
                )

            now = self._utc_now()
            if now >= expires_at:
                raise BudgetLedgerError(
                    Phase2FailureCode.BUDGET_SLOT_STALE,
                    "lease source expired before exact expansion",
                )
            available_for_call = (
                self._max_total_tokens
                - self._cumulative_tokens
                - self._exact_reserved_tokens(self._leases)
                - self._held_future_floors_excluding(source_record_id)
            )
            if (
                max_completion_tokens < minimum_completion_tokens
                or exact_input_tokens + max_completion_tokens > available_for_call
                or exact_input_tokens + minimum_completion_tokens > available_for_call
            ):
                raise BudgetLedgerError(
                    Phase2FailureCode.BUDGET_EXACT_EXPANSION_FAILED,
                    "exact lease does not match current pool arithmetic",
                )
            reserved_tokens = exact_input_tokens + max_completion_tokens

            used_ids = set(self._used_ids)
            lease_id = self._new_id("lease", used_ids)
            used_ids.add(lease_id)
            try:
                lease = BudgetLease(
                    schema_version="phase2.budget-lease.v1",
                    lease_id=lease_id,
                    run_id=self._run_id,
                    variant=self._variant,
                    case_id=self._case_id,
                    snapshot_sequence=self._sequence,
                    owner_role=owner_role,
                    owner_node_id=owner_node_id,
                    permitted_operation=operation,
                    allowed_actions=allowed_actions,
                    source_record_id=source_record_id,
                    reserved_model_calls=1,
                    reserved_tool_calls=0,
                    reserved_tokens=reserved_tokens,
                    exact_input_tokens=exact_input_tokens,
                    minimum_completion_tokens=minimum_completion_tokens,
                    max_completion_tokens=max_completion_tokens,
                    issued_at=now,
                    expires_at=expires_at,
                    status=BudgetLeaseStatus.RESERVED,
                    actual_model_calls=0,
                    actual_tool_calls=0,
                    actual_tokens=0,
                    actual_input_tokens=0,
                    actual_output_tokens=0,
                )
                if source_authorization is not None:
                    authorizations[source_record_id] = (
                        SpecialistExecutionAuthorization.model_validate(
                            {
                                **source_authorization.model_dump(mode="python"),
                                "status": SpecialistAuthorizationStatus.MODEL_LEASED,
                                "model_lease_id": lease_id,
                            }
                        )
                    )
                elif source_slot is not None:
                    slots[source_record_id] = UnboundCapacitySlot.model_validate(
                        {
                            **source_slot.model_dump(mode="python"),
                            "status": CapacitySlotStatus.MATERIALIZED,
                        }
                    )
            except (TypeError, ValidationError, ValueError) as error:
                raise BudgetLedgerError(
                    Phase2FailureCode.BUDGET_EXACT_EXPANSION_FAILED,
                    f"invalid exact lease candidate: {error}",
                ) from error

            leases = {**self._leases, lease_id: lease}
            snapshot = self._commit(
                event_type="expand-exact-model-lease",
                record_ids=(source_record_id, lease_id),
                slots=slots,
                authorizations=authorizations,
                leases=leases,
                bundles=dict(self._bundles),
                charge_signatures=dict(self._charge_signatures),
                charged_model_calls=self._charged_model_calls,
                charged_tool_calls=self._charged_tool_calls,
                cumulative_tokens=self._cumulative_tokens,
                terminal_failure_code=None,
                used_ids=used_ids,
            )
            return lease, snapshot

    def charge_exact_model_lease(
        self,
        *,
        expected_snapshot_sequence: int,
        lease_id: str,
        owner_role: BudgetOwnerRole,
        owner_node_id: str | None,
        source_record_id: str,
        input_tokens: int | None,
        output_tokens: int | None,
        total_tokens: int | None,
    ) -> tuple[BudgetLease, BudgetSnapshot]:
        with self._lock:
            self._require_operational()
            self._require_cas(expected_snapshot_sequence)
            lease = self._require_lease(lease_id)
            raw_signature = (
                owner_role,
                owner_node_id,
                source_record_id,
                input_tokens,
                output_tokens,
                total_tokens,
            )
            if lease.status is BudgetLeaseStatus.CHARGED:
                if self._charge_signatures.get(lease_id) == raw_signature:
                    return lease, self._snapshot
                self._record_failure_and_raise(
                    Phase2FailureCode.PROVIDER_USAGE_INCONSISTENT,
                    "charged lease received a conflicting duplicate",
                    record_ids=(lease_id,),
                )
            if input_tokens is None or output_tokens is None or total_tokens is None:
                self._record_failure_and_raise(
                    Phase2FailureCode.PROVIDER_USAGE_MISSING,
                    "provider usage is missing",
                    record_ids=(lease_id,),
                )
            signature = (
                owner_role,
                owner_node_id,
                source_record_id,
                input_tokens,
                output_tokens,
                total_tokens,
            )
            usage = (input_tokens, output_tokens, total_tokens)
            if any(type(value) is not int or value < 0 for value in usage):
                self._record_failure_and_raise(
                    Phase2FailureCode.PROVIDER_USAGE_INCONSISTENT,
                    "provider usage must contain non-negative exact integers",
                    record_ids=(lease_id,),
                )
            if (
                lease.status is not BudgetLeaseStatus.RESERVED
                or not isinstance(owner_role, BudgetOwnerRole)
                or lease.owner_role is not owner_role
                or lease.owner_node_id != owner_node_id
                or lease.source_record_id != source_record_id
                or input_tokens != lease.exact_input_tokens
                or input_tokens + output_tokens != total_tokens
            ):
                self._record_failure_and_raise(
                    Phase2FailureCode.PROVIDER_USAGE_INCONSISTENT,
                    "provider usage is not attributable to the exact lease",
                    record_ids=(lease_id,),
                )
            if total_tokens > lease.reserved_tokens or output_tokens > lease.max_completion_tokens:
                self._record_failure_and_raise(
                    Phase2FailureCode.PROVIDER_USAGE_EXCEEDS_LEASE,
                    "provider usage exceeds the exact lease",
                    record_ids=(lease_id,),
                )
            if (
                self._charged_model_calls + 1 > self._max_model_calls
                or self._cumulative_tokens + total_tokens > self._max_total_tokens
            ):
                self._record_failure_and_raise(
                    Phase2FailureCode.BUDGET_CUMULATIVE_OVERFLOW,
                    "provider charge exceeds cumulative outer caps",
                    record_ids=(lease_id,),
                )
            try:
                charged = BudgetLease.model_validate(
                    {
                        **lease.model_dump(mode="python"),
                        "status": BudgetLeaseStatus.CHARGED,
                        "actual_model_calls": 1,
                        "actual_tool_calls": 0,
                        "actual_tokens": total_tokens,
                        "actual_input_tokens": input_tokens,
                        "actual_output_tokens": output_tokens,
                    }
                )
            except (TypeError, ValidationError, ValueError) as error:
                self._record_failure_and_raise(
                    Phase2FailureCode.PROVIDER_USAGE_INCONSISTENT,
                    f"provider usage failed lease validation: {error}",
                    record_ids=(lease_id,),
                )
            leases = {**self._leases, lease_id: charged}
            signatures = {**self._charge_signatures, lease_id: signature}
            self._terminal_failure_code = Phase2FailureCode.BUDGET_SLOT_STALE
            snapshot = self._commit(
                event_type="charge-exact-model-lease",
                record_ids=(lease_id,),
                slots=dict(self._slots),
                authorizations=dict(self._authorizations),
                leases=leases,
                bundles=dict(self._bundles),
                charge_signatures=signatures,
                charged_model_calls=self._charged_model_calls + 1,
                charged_tool_calls=self._charged_tool_calls,
                cumulative_tokens=self._cumulative_tokens + total_tokens,
                terminal_failure_code=None,
                used_ids=set(self._used_ids),
            )
            return charged, snapshot

    def return_exact_model_lease(
        self,
        *,
        expected_snapshot_sequence: int,
        lease_id: str,
        owner_role: BudgetOwnerRole,
        owner_node_id: str | None,
        source_record_id: str,
    ) -> tuple[BudgetLease, BudgetSnapshot]:
        with self._lock:
            self._require_operational()
            self._require_cas(expected_snapshot_sequence)
            lease = self._require_lease(lease_id)
            if (
                not isinstance(owner_role, BudgetOwnerRole)
                or lease.owner_role is not owner_role
                or lease.owner_node_id != owner_node_id
                or lease.source_record_id != source_record_id
            ):
                raise BudgetLedgerError(
                    Phase2FailureCode.BUDGET_SLOT_OWNER_MISMATCH,
                    "model lease cannot be returned by a different owner or source",
                )
            if lease.status is BudgetLeaseStatus.RETURNED:
                return lease, self._snapshot
            if lease.status is not BudgetLeaseStatus.RESERVED:
                raise BudgetLedgerError(
                    Phase2FailureCode.BUDGET_SLOT_ALREADY_CONSUMED,
                    f"cannot return a {lease.status.value} lease",
                )
            returned = self._lease_with_status(lease, BudgetLeaseStatus.RETURNED)
            leases = {**self._leases, lease_id: returned}
            slots = dict(self._slots)
            authorizations = dict(self._authorizations)
            bundles = dict(self._bundles)
            bundle = self._active_bundle_for_lease(lease)
            if bundle is not None:
                self._abort_bundle_candidates(
                    bundle=bundle,
                    slots=slots,
                    authorizations=authorizations,
                    leases=leases,
                    bundles=bundles,
                )
            snapshot = self._commit_simple(
                event_type="return-exact-model-lease",
                record_ids=(lease_id,),
                slots=slots,
                authorizations=authorizations,
                leases=leases,
                bundles=bundles,
            )
            return leases[lease_id], snapshot

    def expire_model_lease(
        self,
        *,
        expected_snapshot_sequence: int,
        lease_id: str,
    ) -> tuple[BudgetLease, BudgetSnapshot]:
        with self._lock:
            self._require_operational()
            self._require_cas(expected_snapshot_sequence)
            lease = self._require_lease(lease_id)
            if lease.status is not BudgetLeaseStatus.RESERVED:
                raise BudgetLedgerError(
                    Phase2FailureCode.BUDGET_SLOT_ALREADY_CONSUMED,
                    f"cannot expire a {lease.status.value} lease",
                )
            if self._utc_now() < lease.expires_at:
                raise BudgetLedgerError(
                    Phase2FailureCode.BUDGET_SLOT_STALE,
                    "lease has not reached its expiry",
                )
            expired = self._lease_with_status(lease, BudgetLeaseStatus.EXPIRED)
            leases = {**self._leases, lease_id: expired}
            slots = dict(self._slots)
            authorizations = dict(self._authorizations)
            bundles = dict(self._bundles)
            bundle = self._active_bundle_for_lease(lease)
            if bundle is not None:
                self._abort_bundle_candidates(
                    bundle=bundle,
                    slots=slots,
                    authorizations=authorizations,
                    leases=leases,
                    bundles=bundles,
                )
            snapshot = self._commit_simple(
                event_type="expire-model-lease",
                record_ids=(lease_id,),
                slots=slots,
                authorizations=authorizations,
                leases=leases,
                bundles=bundles,
            )
            return leases[lease_id], snapshot

    def release_capacity_slot(
        self,
        *,
        expected_snapshot_sequence: int,
        slot_id: str,
    ) -> tuple[UnboundCapacitySlot, BudgetSnapshot]:
        return self._close_capacity_slot(
            expected_snapshot_sequence=expected_snapshot_sequence,
            slot_id=slot_id,
            status=CapacitySlotStatus.RELEASED,
        )

    def expire_capacity_slot(
        self,
        *,
        expected_snapshot_sequence: int,
        slot_id: str,
    ) -> tuple[UnboundCapacitySlot, BudgetSnapshot]:
        return self._close_capacity_slot(
            expected_snapshot_sequence=expected_snapshot_sequence,
            slot_id=slot_id,
            status=CapacitySlotStatus.EXPIRED,
        )

    def release_specialist_authorization(
        self,
        *,
        expected_snapshot_sequence: int,
        authorization_id: str,
    ) -> tuple[SpecialistExecutionAuthorization, BudgetSnapshot]:
        with self._lock:
            self._require_operational()
            self._require_cas(expected_snapshot_sequence)
            authorization = self._require_authorization(authorization_id)
            if self._active_bundle_for_slot(authorization.capacity_slot_id) is not None:
                raise BudgetLedgerError(
                    Phase2FailureCode.BUDGET_SLOT_ALREADY_CONSUMED,
                    "active bundle authorization requires a bundle-aware transition",
                )
            if authorization.status is not SpecialistAuthorizationStatus.TOOL_AUTHORIZED:
                raise BudgetLedgerError(
                    Phase2FailureCode.BUDGET_SLOT_ALREADY_CONSUMED,
                    "only an unused TOOL_AUTHORIZED record may be released",
                )
            released = self._authorization_with_status(
                authorization,
                SpecialistAuthorizationStatus.RELEASED,
            )
            authorizations = {**self._authorizations, authorization_id: released}
            snapshot = self._commit_simple(
                event_type="release-specialist-authorization",
                record_ids=(authorization_id,),
                authorizations=authorizations,
            )
            return released, snapshot

    def fail_specialist_authorization(
        self,
        *,
        expected_snapshot_sequence: int,
        authorization_id: str,
    ) -> tuple[SpecialistExecutionAuthorization, BudgetSnapshot]:
        with self._lock:
            self._require_operational()
            self._require_cas(expected_snapshot_sequence)
            authorization = self._require_authorization(authorization_id)
            if authorization.status not in {
                SpecialistAuthorizationStatus.TOOL_CHARGED,
                SpecialistAuthorizationStatus.MODEL_LEASED,
            }:
                raise BudgetLedgerError(
                    Phase2FailureCode.BUDGET_SLOT_ALREADY_CONSUMED,
                    f"cannot fail a {authorization.status.value} authorization",
                )
            leases = dict(self._leases)
            if authorization.model_lease_id is not None:
                lease = self._require_lease(authorization.model_lease_id)
                if lease.status is BudgetLeaseStatus.RESERVED:
                    leases[lease.lease_id] = self._lease_with_status(
                        lease, BudgetLeaseStatus.RETURNED
                    )
            failed = self._authorization_with_status(
                authorization,
                SpecialistAuthorizationStatus.FAILED,
            )
            authorizations = {**self._authorizations, authorization_id: failed}
            slots = dict(self._slots)
            bundles = dict(self._bundles)
            bundle = self._active_bundle_for_slot(authorization.capacity_slot_id)
            if bundle is not None:
                self._abort_bundle_candidates(
                    bundle=bundle,
                    slots=slots,
                    authorizations=authorizations,
                    leases=leases,
                    bundles=bundles,
                )
            snapshot = self._commit_simple(
                event_type="fail-specialist-authorization",
                record_ids=(authorization_id,),
                slots=slots,
                authorizations=authorizations,
                leases=leases,
                bundles=bundles,
            )
            return authorizations[authorization_id], snapshot

    def complete_specialist_authorization(
        self,
        *,
        expected_snapshot_sequence: int,
        authorization_id: str,
    ) -> tuple[SpecialistExecutionAuthorization, BudgetSnapshot]:
        with self._lock:
            self._require_operational()
            self._require_cas(expected_snapshot_sequence)
            authorization = self._require_authorization(authorization_id)
            if (
                authorization.status is not SpecialistAuthorizationStatus.MODEL_LEASED
                or authorization.model_lease_id is None
                or self._require_lease(authorization.model_lease_id).status
                is not BudgetLeaseStatus.CHARGED
            ):
                raise BudgetLedgerError(
                    Phase2FailureCode.BUDGET_SLOT_ALREADY_CONSUMED,
                    "completion requires a charged same-authorization model lease",
                )
            completed = self._authorization_with_status(
                authorization,
                SpecialistAuthorizationStatus.COMPLETED,
            )
            authorizations = {**self._authorizations, authorization_id: completed}
            snapshot = self._commit_simple(
                event_type="complete-specialist-authorization",
                record_ids=(authorization_id,),
                authorizations=authorizations,
            )
            return completed, snapshot

    def initialize_dynamic(
        self,
        *,
        expected_snapshot_sequence: int,
        commander: CapacitySlotRequest,
        specialist: CapacitySlotRequest,
        first_judge: CapacitySlotRequest,
    ) -> tuple[tuple[UnboundCapacitySlot, ...], BudgetSnapshot]:
        with self._lock:
            self._require_operational()
            self._require_request_shape(
                commander,
                ModelOperation.COMMANDER_MODEL,
                ModelAllowedActions.PLAN_ONLY,
            )
            self._require_request_shape(
                specialist,
                ModelOperation.SPECIALIST_MODEL,
                ModelAllowedActions.FINDING_ONLY,
            )
            self._require_request_shape(
                first_judge,
                ModelOperation.FIRST_JUDGE_MODEL,
                ModelAllowedActions.FINAL_ONLY,
            )
            if self._variant is not Phase2Variant.DYNAMIC_MULTI_AGENT:
                raise BudgetLedgerError(
                    Phase2FailureCode.BUDGET_SLOT_OWNER_MISMATCH,
                    "Dynamic initialization requires the Dynamic variant",
                )
            self._require_pristine_initialization(expected_snapshot_sequence)
            return self.hold_capacity_slots(
                expected_snapshot_sequence=expected_snapshot_sequence,
                requests=(commander, specialist, first_judge),
            )

    def initialize_fixed(
        self,
        *,
        expected_snapshot_sequence: int,
        specialists: Sequence[CapacitySlotRequest],
        final_judge: CapacitySlotRequest,
    ) -> tuple[tuple[UnboundCapacitySlot, ...], BudgetSnapshot]:
        with self._lock:
            self._require_operational()
            if self._variant is not Phase2Variant.FIXED_SPECIALIST_WORKFLOW:
                raise BudgetLedgerError(
                    Phase2FailureCode.BUDGET_SLOT_OWNER_MISMATCH,
                    "Fixed initialization requires the Fixed variant",
                )
            if len(specialists) != 4:
                raise BudgetLedgerError(
                    Phase2FailureCode.BUDGET_MINIMUM_FLOOR_UNAVAILABLE,
                    "Fixed initialization requires exactly four Specialist floors",
                )
            for specialist in specialists:
                self._require_request_shape(
                    specialist,
                    ModelOperation.SPECIALIST_MODEL,
                    ModelAllowedActions.FINDING_ONLY,
                )
            self._require_request_shape(
                final_judge,
                ModelOperation.FINAL_JUDGE_MODEL,
                ModelAllowedActions.FINAL_ONLY,
            )
            self._require_pristine_initialization(expected_snapshot_sequence)
            return self.hold_capacity_slots(
                expected_snapshot_sequence=expected_snapshot_sequence,
                requests=(*specialists, final_judge),
            )

    def hold_final_only_first_judge(
        self,
        *,
        expected_snapshot_sequence: int,
        request: CapacitySlotRequest,
    ) -> tuple[UnboundCapacitySlot, BudgetSnapshot]:
        with self._lock:
            self._require_operational()
            self._require_request_shape(
                request,
                ModelOperation.FIRST_JUDGE_MODEL,
                ModelAllowedActions.FINAL_ONLY,
            )
            slots, snapshot = self.hold_capacity_slots(
                expected_snapshot_sequence=expected_snapshot_sequence,
                requests=(request,),
            )
            return slots[0], snapshot

    def resize_dynamic_initial_plan(
        self,
        *,
        expected_snapshot_sequence: int,
        commander_slot_id: str,
        retained_specialist_slot_ids: Sequence[str],
        new_specialists: Sequence[CapacitySlotRequest],
        first_judge_slot_id: str,
    ) -> tuple[tuple[UnboundCapacitySlot, ...], BudgetSnapshot]:
        with self._lock:
            self._require_operational()
            self._require_cas(expected_snapshot_sequence)
            if len(retained_specialist_slot_ids) != 1:
                raise BudgetLedgerError(
                    Phase2FailureCode.BUDGET_MINIMUM_FLOOR_UNAVAILABLE,
                    "initial resize requires exactly one retained bootstrap Specialist",
                )
            commander = self._require_slot(commander_slot_id)
            bootstrap = self._require_slot(retained_specialist_slot_ids[0])
            judge = self._require_slot(first_judge_slot_id)
            try:
                ordered_new = tuple(
                    CapacitySlotRequest.model_validate(item)
                    for item in tuple(new_specialists)
                )
            except (TypeError, ValidationError, ValueError) as error:
                raise BudgetLedgerError(
                    Phase2FailureCode.BUDGET_SLOT_OWNER_MISMATCH,
                    f"new Specialist request is not an exact closed record: {error}",
                ) from error
            now = self._utc_now()

            if self._variant is not Phase2Variant.DYNAMIC_MULTI_AGENT:
                raise BudgetLedgerError(
                    Phase2FailureCode.BUDGET_SLOT_OWNER_MISMATCH,
                    "plan resize requires the Dynamic variant",
                )
            if (
                self._max_model_calls,
                self._max_tool_calls,
                self._max_total_tokens,
            ) != (8, 8, COMPARISON_MAX_TOTAL_TOKENS):
                raise BudgetLedgerError(
                    Phase2FailureCode.BUDGET_CUMULATIVE_OVERFLOW,
                    "plan resize requires exact 8 / 8 / 32000 caps",
                )
            if (
                self._charged_model_calls != 1
                or self._charged_tool_calls != 0
            ):
                raise BudgetLedgerError(
                    Phase2FailureCode.BUDGET_SLOT_ALREADY_CONSUMED,
                    "plan resize requires exactly one charged model and zero tools",
                )
            if (
                any(
                    lease.status is BudgetLeaseStatus.RESERVED
                    for lease in self._leases.values()
                )
                or any(
                    authorization.status in _OPEN_AUTHORIZATION_STATUSES
                    for authorization in self._authorizations.values()
                )
                or self._has_active_bundle()
            ):
                raise BudgetLedgerError(
                    Phase2FailureCode.BUDGET_SLOT_ALREADY_CONSUMED,
                    "plan resize rejects active leases, authorizations, or bundles",
                )
            if len({commander_slot_id, bootstrap.slot_id, first_judge_slot_id}) != 3:
                raise BudgetLedgerError(
                    Phase2FailureCode.BUDGET_SLOT_OWNER_MISMATCH,
                    "plan resize input slot IDs must be distinct",
                )
            if (
                commander.status is not CapacitySlotStatus.MATERIALIZED
                or commander.permitted_operation is not ModelOperation.COMMANDER_MODEL
                or commander.allowed_actions is not ModelAllowedActions.PLAN_ONLY
                or commander.reserved_model_calls != 1
                or commander.reserved_tool_calls != 0
            ):
                raise BudgetLedgerError(
                    (
                        Phase2FailureCode.BUDGET_SLOT_ALREADY_CONSUMED
                        if commander.status is not CapacitySlotStatus.MATERIALIZED
                        else Phase2FailureCode.BUDGET_SLOT_OWNER_MISMATCH
                    ),
                    "plan resize requires the charged Commander lineage",
                )
            for slot, operation, actions, tool_calls, label in (
                (
                    bootstrap,
                    ModelOperation.SPECIALIST_MODEL,
                    ModelAllowedActions.FINDING_ONLY,
                    1,
                    "bootstrap Specialist",
                ),
                (
                    judge,
                    ModelOperation.FIRST_JUDGE_MODEL,
                    ModelAllowedActions.FINAL_ONLY,
                    0,
                    "first Judge",
                ),
            ):
                if slot.status is not CapacitySlotStatus.HELD:
                    raise BudgetLedgerError(
                        Phase2FailureCode.BUDGET_SLOT_ALREADY_CONSUMED,
                        f"plan resize requires a HELD {label} slot",
                    )
                if (
                    slot.run_id != self._run_id
                    or slot.variant is not self._variant
                    or slot.case_id != self._case_id
                    or slot.permitted_operation is not operation
                    or slot.allowed_actions is not actions
                    or slot.reserved_model_calls != 1
                    or slot.reserved_tool_calls != tool_calls
                ):
                    raise BudgetLedgerError(
                        Phase2FailureCode.BUDGET_SLOT_OWNER_MISMATCH,
                        f"plan resize {label} slot shape or scope is incompatible",
                    )
            if (
                commander.run_id != self._run_id
                or commander.variant is not self._variant
                or commander.case_id != self._case_id
            ):
                raise BudgetLedgerError(
                    Phase2FailureCode.BUDGET_SLOT_OWNER_MISMATCH,
                    "plan resize Commander scope is incompatible",
                )
            expected_active = {bootstrap.slot_id, judge.slot_id}
            actual_active = {
                slot_id
                for slot_id, slot in self._slots.items()
                if slot.status is CapacitySlotStatus.HELD
            }
            if actual_active != expected_active:
                raise BudgetLedgerError(
                    Phase2FailureCode.BUDGET_SLOT_ALREADY_CONSUMED,
                    "plan resize requires only bootstrap Specialist and first Judge active",
                )
            if any(
                now >= slot.expires_at for slot in (commander, bootstrap, judge)
            ):
                raise BudgetLedgerError(
                    Phase2FailureCode.BUDGET_SLOT_STALE,
                    "plan resize lineage contains an expired capacity slot",
                )
            if len(ordered_new) > 2:
                raise BudgetLedgerError(
                    Phase2FailureCode.BUDGET_MINIMUM_FLOOR_UNAVAILABLE,
                    "initial resize creates at most two Specialist slots",
                )
            for specialist in ordered_new:
                self._require_request_shape(
                    specialist,
                    ModelOperation.SPECIALIST_MODEL,
                    ModelAllowedActions.FINDING_ONLY,
                )
                if (
                    specialist.reserved_model_calls != 1
                    or specialist.reserved_tool_calls != 1
                    or specialist.minimum_token_floor
                    != bootstrap.minimum_token_floor
                    or specialist.expires_at != bootstrap.expires_at
                ):
                    raise BudgetLedgerError(
                        Phase2FailureCode.BUDGET_SLOT_OWNER_MISMATCH,
                        "new Specialist request must exactly inherit bootstrap shape",
                    )
                if now >= specialist.expires_at:
                    raise BudgetLedgerError(
                        Phase2FailureCode.BUDGET_SLOT_STALE,
                        "new Specialist request is expired",
                    )

            slots = dict(self._slots)
            used_ids = set(self._used_ids)
            created = self._build_slots(
                requests=ordered_new,
                slots=slots,
                used_ids=used_ids,
                now=now,
                allow_empty=True,
            )
            ordered = (bootstrap, *created)
            snapshot = self._commit(
                event_type="resize-dynamic-initial-plan",
                record_ids=(
                    commander_slot_id,
                    *retained_specialist_slot_ids,
                    *(slot.slot_id for slot in created),
                    first_judge_slot_id,
                ),
                slots=slots,
                authorizations=dict(self._authorizations),
                leases=dict(self._leases),
                bundles=dict(self._bundles),
                charge_signatures=dict(self._charge_signatures),
                charged_model_calls=self._charged_model_calls,
                charged_tool_calls=self._charged_tool_calls,
                cumulative_tokens=self._cumulative_tokens,
                terminal_failure_code=None,
                used_ids=used_ids,
                occurred_at=now,
            )
            return ordered, snapshot

    def _hold_conditional_refinement_bundle(
        self,
        *,
        expected_snapshot_sequence: int,
        first_judge: CapacitySlotRequest,
        specialists: Sequence[CapacitySlotRequest],
        final_judge: CapacitySlotRequest,
        replaced_first_judge_slot_id: str,
    ) -> tuple[ConditionalRefinementBundle, BudgetSnapshot]:
        with self._lock:
            self._require_operational()
            self._require_cas(expected_snapshot_sequence)
            if self._variant is not Phase2Variant.DYNAMIC_MULTI_AGENT:
                raise BudgetLedgerError(
                    Phase2FailureCode.BUDGET_SLOT_OWNER_MISMATCH,
                    "conditional refinement requires the Dynamic variant",
                )
            if self._bundles:
                raise BudgetLedgerError(
                    Phase2FailureCode.BUDGET_SLOT_ALREADY_CONSUMED,
                    "conditional refinement bundle creation is one-shot",
                )
            self._require_request_shape(
                first_judge,
                ModelOperation.FIRST_JUDGE_MODEL,
                ModelAllowedActions.FINAL_OR_REFINEMENT,
            )
            if len(specialists) != 2:
                raise BudgetLedgerError(
                    Phase2FailureCode.BUDGET_MINIMUM_FLOOR_UNAVAILABLE,
                    "conditional refinement requires exactly two Specialist floors",
                )
            for specialist in specialists:
                self._require_request_shape(
                    specialist,
                    ModelOperation.SPECIALIST_MODEL,
                    ModelAllowedActions.FINDING_ONLY,
                )
            self._require_request_shape(
                final_judge,
                ModelOperation.FINAL_JUDGE_MODEL,
                ModelAllowedActions.FINAL_ONLY,
            )
            slots = dict(self._slots)
            replaced = self._require_slot(replaced_first_judge_slot_id)
            if (
                replaced.status is not CapacitySlotStatus.HELD
                or replaced.permitted_operation
                is not ModelOperation.FIRST_JUDGE_MODEL
                or replaced.allowed_actions is not ModelAllowedActions.FINAL_ONLY
            ):
                raise BudgetLedgerError(
                    Phase2FailureCode.BUDGET_SLOT_ALREADY_CONSUMED,
                    "only a held first-Judge FINAL_ONLY slot may be replaced",
                )
            slots[replaced.slot_id] = self._slot_with_status(
                replaced, CapacitySlotStatus.RELEASED
            )
            now = self._utc_now()
            used_ids = set(self._used_ids)
            created = self._build_slots(
                requests=(first_judge, *specialists, final_judge),
                slots=slots,
                used_ids=used_ids,
                now=now,
            )
            bundle_id = self._new_id("bundle", used_ids)
            used_ids.add(bundle_id)
            try:
                bundle = ConditionalRefinementBundle(
                    schema_version="phase2.conditional-refinement-bundle.v1",
                    bundle_id=bundle_id,
                    run_id=self._run_id,
                    case_id=self._case_id,
                    variant=Phase2Variant.DYNAMIC_MULTI_AGENT,
                    first_judge_capacity_slot_id=created[0].slot_id,
                    specialist_capacity_slot_ids=(
                        created[1].slot_id,
                        created[2].slot_id,
                    ),
                    final_judge_capacity_slot_id=created[3].slot_id,
                    creating_snapshot_sequence=self._sequence,
                    status=ConditionalRefinementBundleStatus.HELD,
                )
            except (TypeError, ValidationError, ValueError) as error:
                raise BudgetLedgerError(
                    Phase2FailureCode.BUDGET_MINIMUM_FLOOR_UNAVAILABLE,
                    f"invalid conditional bundle: {error}",
                ) from error
            bundles = {**self._bundles, bundle_id: bundle}
            snapshot = self._commit(
                event_type="replace-first-judge-with-conditional-bundle",
                record_ids=(
                    replaced.slot_id,
                    bundle_id,
                    *(slot.slot_id for slot in created),
                ),
                slots=slots,
                authorizations=dict(self._authorizations),
                leases=dict(self._leases),
                bundles=bundles,
                charge_signatures=dict(self._charge_signatures),
                charged_model_calls=self._charged_model_calls,
                charged_tool_calls=self._charged_tool_calls,
                cumulative_tokens=self._cumulative_tokens,
                terminal_failure_code=None,
                used_ids=used_ids,
            )
            return bundle, snapshot

    def replace_first_judge_with_conditional_bundle(
        self,
        *,
        expected_snapshot_sequence: int,
        replaced_first_judge_slot_id: str,
        first_judge: CapacitySlotRequest,
        specialists: Sequence[CapacitySlotRequest],
        final_judge: CapacitySlotRequest,
    ) -> tuple[ConditionalRefinementBundle, BudgetSnapshot]:
        return self._hold_conditional_refinement_bundle(
            expected_snapshot_sequence=expected_snapshot_sequence,
            first_judge=first_judge,
            specialists=specialists,
            final_judge=final_judge,
            replaced_first_judge_slot_id=replaced_first_judge_slot_id,
        )

    def release_unused_refinement_members(
        self,
        *,
        expected_snapshot_sequence: int,
        bundle_id: str,
        used_specialist_slot_ids: Sequence[str],
        retain_final_judge: bool,
    ) -> tuple[ConditionalRefinementBundle, BudgetSnapshot]:
        with self._lock:
            self._require_operational()
            self._require_cas(expected_snapshot_sequence)
            if type(retain_final_judge) is not bool:
                raise BudgetLedgerError(
                    Phase2FailureCode.BUDGET_SLOT_ALREADY_CONSUMED,
                    "retain_final_judge must be an exact boolean",
                )
            bundle = self.conditional_bundle(bundle_id)
            if bundle.status is not ConditionalRefinementBundleStatus.HELD:
                raise BudgetLedgerError(
                    Phase2FailureCode.BUDGET_SLOT_ALREADY_CONSUMED,
                    "conditional bundle member resolution already occurred",
                )
            used = tuple(used_specialist_slot_ids)
            if (
                len(used) != len(set(used))
                or not set(used).issubset(bundle.specialist_capacity_slot_ids)
                or (not retain_final_judge and used)
                or (retain_final_judge and not used)
            ):
                raise BudgetLedgerError(
                    Phase2FailureCode.BUDGET_SLOT_OWNER_MISMATCH,
                    "bundle member selection is inconsistent",
                )
            first_judge_charged = any(
                lease.source_record_id == bundle.first_judge_capacity_slot_id
                and lease.status is BudgetLeaseStatus.CHARGED
                for lease in self._leases.values()
            )
            if not first_judge_charged:
                raise BudgetLedgerError(
                    Phase2FailureCode.BUDGET_SLOT_ALREADY_CONSUMED,
                    "bundle members cannot resolve before the first Judge charge",
                )
            slots = dict(self._slots)
            for slot_id in bundle.specialist_capacity_slot_ids:
                slot = slots[slot_id]
                if slot_id in used:
                    if slot.status is not CapacitySlotStatus.MATERIALIZED:
                        raise BudgetLedgerError(
                            Phase2FailureCode.BUDGET_SLOT_ALREADY_CONSUMED,
                            "used refinement slot was not materialized",
                        )
                elif slot.status is CapacitySlotStatus.HELD:
                    slots[slot_id] = self._slot_with_status(
                        slot, CapacitySlotStatus.RELEASED
                    )
                else:
                    raise BudgetLedgerError(
                        Phase2FailureCode.BUDGET_SLOT_ALREADY_CONSUMED,
                        "unused refinement slot is already consumed",
                    )
            final_slot = slots[bundle.final_judge_capacity_slot_id]
            if not retain_final_judge:
                if final_slot.status is not CapacitySlotStatus.HELD:
                    raise BudgetLedgerError(
                        Phase2FailureCode.BUDGET_SLOT_ALREADY_CONSUMED,
                        "final-Judge bundle floor is already consumed",
                    )
                slots[final_slot.slot_id] = self._slot_with_status(
                    final_slot, CapacitySlotStatus.RELEASED
                )
            status = (
                ConditionalRefinementBundleStatus.PARTIALLY_CONSUMED
                if retain_final_judge
                else ConditionalRefinementBundleStatus.RELEASED
            )
            updated = ConditionalRefinementBundle.model_validate(
                {**bundle.model_dump(mode="python"), "status": status}
            )
            bundles = {**self._bundles, bundle_id: updated}
            snapshot = self._commit(
                event_type="release-unused-refinement-members",
                record_ids=(bundle_id, *bundle.specialist_capacity_slot_ids),
                slots=slots,
                authorizations=dict(self._authorizations),
                leases=dict(self._leases),
                bundles=bundles,
                charge_signatures=dict(self._charge_signatures),
                charged_model_calls=self._charged_model_calls,
                charged_tool_calls=self._charged_tool_calls,
                cumulative_tokens=self._cumulative_tokens,
                terminal_failure_code=None,
                used_ids=set(self._used_ids),
            )
            return updated, snapshot

    def complete_conditional_refinement_bundle(
        self,
        *,
        expected_snapshot_sequence: int,
        bundle_id: str,
    ) -> tuple[ConditionalRefinementBundle, BudgetSnapshot]:
        with self._lock:
            self._require_operational()
            self._require_cas(expected_snapshot_sequence)
            bundle = self.conditional_bundle(bundle_id)
            if bundle.status is not ConditionalRefinementBundleStatus.PARTIALLY_CONSUMED:
                raise BudgetLedgerError(
                    Phase2FailureCode.BUDGET_SLOT_ALREADY_CONSUMED,
                    "only a partially consumed bundle may complete",
                )
            first_slot = self._require_slot(bundle.first_judge_capacity_slot_id)
            final_slot = self._require_slot(bundle.final_judge_capacity_slot_id)
            charged_slot_sources = {
                lease.source_record_id
                for lease in self._leases.values()
                if lease.status is BudgetLeaseStatus.CHARGED
            }
            if (
                first_slot.status is not CapacitySlotStatus.MATERIALIZED
                or final_slot.status is not CapacitySlotStatus.MATERIALIZED
                or first_slot.slot_id not in charged_slot_sources
                or final_slot.slot_id not in charged_slot_sources
            ):
                raise BudgetLedgerError(
                    Phase2FailureCode.BUDGET_SLOT_ALREADY_CONSUMED,
                    "bundle completion requires charged first and final Judge leases",
                )
            authorization_by_slot = {
                authorization.capacity_slot_id: authorization
                for authorization in self._authorizations.values()
            }
            for slot_id in bundle.specialist_capacity_slot_ids:
                slot = self._require_slot(slot_id)
                if slot.status is CapacitySlotStatus.RELEASED:
                    continue
                authorization = authorization_by_slot.get(slot_id)
                if (
                    slot.status is not CapacitySlotStatus.MATERIALIZED
                    or authorization is None
                    or authorization.status
                    is not SpecialistAuthorizationStatus.COMPLETED
                ):
                    raise BudgetLedgerError(
                        Phase2FailureCode.BUDGET_SLOT_ALREADY_CONSUMED,
                        "bundle completion requires each used Specialist to complete",
                    )
            updated = ConditionalRefinementBundle.model_validate(
                {
                    **bundle.model_dump(mode="python"),
                    "status": ConditionalRefinementBundleStatus.COMPLETED,
                }
            )
            bundles = {**self._bundles, bundle_id: updated}
            snapshot = self._commit_simple(
                event_type="complete-conditional-refinement-bundle",
                record_ids=(bundle_id,),
                bundles=bundles,
            )
            return updated, snapshot

    def record_terminal_failure(
        self,
        *,
        expected_snapshot_sequence: int,
        code: Phase2FailureCode,
    ) -> BudgetSnapshot:
        with self._lock:
            self._require_operational()
            self._require_cas(expected_snapshot_sequence)
            if not isinstance(code, Phase2FailureCode):
                raise BudgetLedgerError(
                    Phase2FailureCode.BUDGET_SLOT_STALE,
                    "terminal failure code is not in the shared Phase 2 enum",
                )
            self._terminal_failure_code = code
            try:
                return self._commit(
                    event_type="record-terminal-failure",
                    record_ids=(),
                    slots=dict(self._slots),
                    authorizations=dict(self._authorizations),
                    leases=dict(self._leases),
                    bundles=dict(self._bundles),
                    charge_signatures=dict(self._charge_signatures),
                    charged_model_calls=self._charged_model_calls,
                    charged_tool_calls=self._charged_tool_calls,
                    cumulative_tokens=self._cumulative_tokens,
                    terminal_failure_code=code,
                    used_ids=set(self._used_ids),
                    failure_code=code,
                )
            except BudgetLedgerError as error:
                raise BudgetLedgerError(
                    code,
                    "terminal failure was latched before audit generation failed",
                ) from error

    def _validate_outcome_key(
        self,
        authorization_id: str,
        dispatch_claim_snapshot_sequence: int,
    ) -> tuple[str, int]:
        if self._callback_active:
            raise BudgetLedgerError(
                Phase2FailureCode.BUDGET_CAS_CONFLICT,
                "same-ledger outcome is forbidden during an injected callback",
            )
        self._validate_identifier(authorization_id)
        if (
            type(dispatch_claim_snapshot_sequence) is not int
            or dispatch_claim_snapshot_sequence < 0
        ):
            raise BudgetLedgerError(
                Phase2FailureCode.BUDGET_SLOT_ALREADY_CONSUMED,
                "dispatch claim sequence is not an exact non-negative integer",
            )
        return authorization_id, dispatch_claim_snapshot_sequence

    def _require_no_other_outcome(
        self,
        authorization_id: str,
        key: tuple[str, int],
    ) -> None:
        if any(
            stored_key[0] == authorization_id and stored_key != key
            for stored_key in self._outcome_receipts
        ):
            raise BudgetLedgerError(
                Phase2FailureCode.BUDGET_SLOT_ALREADY_CONSUMED,
                "Specialist authorization already has another outcome receipt",
            )

    def _require_dispatching_authorization(
        self,
        authorization_id: str,
        dispatch_claim_snapshot_sequence: int,
    ) -> SpecialistExecutionAuthorization:
        authorization = self._require_authorization(authorization_id)
        if (
            authorization.status
            is not SpecialistAuthorizationStatus.TOOL_DISPATCHING
            or authorization.dispatch_claim_snapshot_sequence
            != dispatch_claim_snapshot_sequence
            or authorization.actual_tool_calls != 0
            or authorization.tool_charged_snapshot_sequence is not None
            or authorization.tool_call_record_sha256 is not None
            or authorization.model_lease_id is not None
        ):
            raise BudgetLedgerError(
                Phase2FailureCode.BUDGET_SLOT_ALREADY_CONSUMED,
                "Specialist dispatch claim is stale or already consumed",
            )
        return authorization

    def _close_capacity_slot(
        self,
        *,
        expected_snapshot_sequence: int,
        slot_id: str,
        status: CapacitySlotStatus,
    ) -> tuple[UnboundCapacitySlot, BudgetSnapshot]:
        with self._lock:
            self._require_operational()
            self._require_cas(expected_snapshot_sequence)
            slot = self._require_slot(slot_id)
            if self._active_bundle_for_slot(slot_id) is not None:
                raise BudgetLedgerError(
                    Phase2FailureCode.BUDGET_SLOT_ALREADY_CONSUMED,
                    "active bundle members require bundle-aware transitions",
                )
            if slot.status is not CapacitySlotStatus.HELD:
                raise BudgetLedgerError(
                    Phase2FailureCode.BUDGET_SLOT_ALREADY_CONSUMED,
                    "only a HELD capacity slot may close",
                )
            if (
                status is CapacitySlotStatus.EXPIRED
                and self._utc_now() < slot.expires_at
            ):
                raise BudgetLedgerError(
                    Phase2FailureCode.BUDGET_SLOT_STALE,
                    "capacity slot has not reached its expiry",
                )
            closed = self._slot_with_status(slot, status)
            slots = {**self._slots, slot_id: closed}
            snapshot = self._commit_simple(
                event_type=f"{status.value.lower()}-capacity-slot",
                record_ids=(slot_id,),
                slots=slots,
            )
            return closed, snapshot

    def _build_slots(
        self,
        *,
        requests: Sequence[CapacitySlotRequest],
        slots: dict[str, UnboundCapacitySlot],
        used_ids: set[str],
        now: datetime,
        allow_empty: bool = False,
    ) -> tuple[UnboundCapacitySlot, ...]:
        try:
            ordered_requests = tuple(requests)
        except TypeError as error:
            raise BudgetLedgerError(
                Phase2FailureCode.BUDGET_MINIMUM_FLOOR_UNAVAILABLE,
                "capacity-slot requests must be a finite sequence",
            ) from error
        if not ordered_requests and not allow_empty:
            raise BudgetLedgerError(
                Phase2FailureCode.BUDGET_MINIMUM_FLOOR_UNAVAILABLE,
                "at least one capacity-slot request is required",
            )
        if len(ordered_requests) + len(slots) > 64:
            raise BudgetLedgerError(
                Phase2FailureCode.BUDGET_MINIMUM_FLOOR_UNAVAILABLE,
                "capacity-slot request batch is unbounded",
            )
        for item in ordered_requests:
            if not isinstance(item, CapacitySlotRequest):
                raise BudgetLedgerError(
                    Phase2FailureCode.BUDGET_MINIMUM_FLOOR_UNAVAILABLE,
                    "capacity-slot requests must be validated closed records",
                )
            if item.expires_at <= now:
                raise BudgetLedgerError(
                    Phase2FailureCode.BUDGET_SLOT_STALE,
                    "capacity-slot request is already expired",
                )
        requested_model_calls = sum(item.reserved_model_calls for item in ordered_requests)
        requested_tool_calls = sum(item.reserved_tool_calls for item in ordered_requests)
        requested_tokens = sum(item.minimum_token_floor for item in ordered_requests)
        current = self._reservation_totals(
            slots=slots,
            authorizations=self._authorizations,
            leases=self._leases,
        )
        if (
            self._charged_model_calls + current[0] + requested_model_calls
            > self._max_model_calls
            or self._charged_tool_calls + current[1] + requested_tool_calls
            > self._max_tool_calls
            or self._cumulative_tokens + current[2] + requested_tokens
            > self._max_total_tokens
        ):
            raise BudgetLedgerError(
                Phase2FailureCode.BUDGET_MINIMUM_FLOOR_UNAVAILABLE,
                "requested minimum floors exceed the independent central pool",
            )
        created: list[UnboundCapacitySlot] = []
        for item in ordered_requests:
            slot_id = self._new_id("slot", used_ids)
            used_ids.add(slot_id)
            try:
                slot = UnboundCapacitySlot(
                    schema_version="phase2.unbound-capacity-slot.v1",
                    slot_id=slot_id,
                    run_id=self._run_id,
                    variant=self._variant,
                    case_id=self._case_id,
                    permitted_operation=item.permitted_operation,
                    allowed_actions=item.allowed_actions,
                    reserved_model_calls=item.reserved_model_calls,
                    reserved_tool_calls=item.reserved_tool_calls,
                    minimum_token_floor=item.minimum_token_floor,
                    creating_snapshot_sequence=self._sequence,
                    issued_at=now,
                    expires_at=item.expires_at,
                    status=CapacitySlotStatus.HELD,
                )
            except (TypeError, ValidationError, ValueError) as error:
                raise BudgetLedgerError(
                    Phase2FailureCode.BUDGET_MINIMUM_FLOOR_UNAVAILABLE,
                    f"invalid capacity-slot candidate: {error}",
                ) from error
            slots[slot_id] = slot
            created.append(slot)
        return tuple(created)

    def _record_failure_and_raise(
        self,
        code: Phase2FailureCode,
        detail: str,
        *,
        record_ids: tuple[str, ...],
    ) -> Never:
        self._terminal_failure_code = code
        try:
            self._commit(
                event_type="terminal-budget-failure",
                record_ids=record_ids,
                slots=dict(self._slots),
                authorizations=dict(self._authorizations),
                leases=dict(self._leases),
                bundles=dict(self._bundles),
                charge_signatures=dict(self._charge_signatures),
                charged_model_calls=self._charged_model_calls,
                charged_tool_calls=self._charged_tool_calls,
                cumulative_tokens=self._cumulative_tokens,
                terminal_failure_code=code,
                used_ids=set(self._used_ids),
                failure_code=code,
            )
        except BudgetLedgerError as error:
            raise BudgetLedgerError(code, detail) from error
        raise BudgetLedgerError(code, detail)

    def _commit_simple(
        self,
        *,
        event_type: str,
        record_ids: tuple[str, ...],
        slots: dict[str, UnboundCapacitySlot] | None = None,
        authorizations: dict[str, SpecialistExecutionAuthorization] | None = None,
        leases: dict[str, BudgetLease] | None = None,
        bundles: dict[str, ConditionalRefinementBundle] | None = None,
    ) -> BudgetSnapshot:
        return self._commit(
            event_type=event_type,
            record_ids=record_ids,
            slots=dict(self._slots) if slots is None else slots,
            authorizations=(
                dict(self._authorizations)
                if authorizations is None
                else authorizations
            ),
            leases=dict(self._leases) if leases is None else leases,
            bundles=dict(self._bundles) if bundles is None else bundles,
            charge_signatures=dict(self._charge_signatures),
            charged_model_calls=self._charged_model_calls,
            charged_tool_calls=self._charged_tool_calls,
            cumulative_tokens=self._cumulative_tokens,
            terminal_failure_code=None,
            used_ids=set(self._used_ids),
        )

    def _commit(
        self,
        *,
        event_type: str,
        record_ids: tuple[str, ...],
        slots: dict[str, UnboundCapacitySlot],
        authorizations: dict[str, SpecialistExecutionAuthorization],
        leases: dict[str, BudgetLease],
        bundles: dict[str, ConditionalRefinementBundle],
        charge_signatures: dict[
            str, tuple[BudgetOwnerRole, str | None, str, int, int, int]
        ],
        charged_model_calls: int,
        charged_tool_calls: int,
        cumulative_tokens: int,
        terminal_failure_code: Phase2FailureCode | None,
        used_ids: set[str],
        failure_code: Phase2FailureCode | None = None,
        occurred_at: datetime | None = None,
        specialist_outcome: tuple[
            SpecialistToolOutcomeKind,
            SpecialistExecutionAuthorization,
            str | None,
            Literal["EXECUTOR_FAILURE", "RECORD_MISMATCH"] | None,
        ]
        | None = None,
    ) -> BudgetSnapshot:
        sequence = self._sequence + 1
        audit_record_ids = self._derive_audit_record_ids(
            explicit_record_ids=record_ids,
            slots=slots,
            authorizations=authorizations,
            leases=leases,
            bundles=bundles,
        )
        outcome_receipt: SpecialistToolOutcomeReceipt | None = None
        outcome_key: tuple[str, int] | None = None
        try:
            event_time = self._utc_now() if occurred_at is None else occurred_at
            elapsed_seconds = self._next_elapsed_seconds()
            event_id = self._new_id("audit", used_ids)
            used_ids.add(event_id)
            snapshot_id = self._new_id("snapshot", used_ids)
            used_ids.add(snapshot_id)
            snapshot = self._make_snapshot(
                snapshot_id=snapshot_id,
                sequence=sequence,
                charged_model_calls=charged_model_calls,
                charged_tool_calls=charged_tool_calls,
                cumulative_tokens=cumulative_tokens,
                slots=slots,
                authorizations=authorizations,
                leases=leases,
                elapsed_seconds=elapsed_seconds,
            )
            event = BudgetAuditEvent(
                schema_version="phase2.budget-audit-event.v1",
                event_id=event_id,
                run_id=self._run_id,
                variant=self._variant,
                case_id=self._case_id,
                snapshot_sequence=sequence,
                event_type=event_type,
                record_ids=audit_record_ids,
                occurred_at=event_time,
                failure_code=failure_code,
            )
            if specialist_outcome is not None:
                outcome_kind, outcome_authorization, record_hash, failure_kind = (
                    specialist_outcome
                )
                claim_sequence = cast(
                    int,
                    outcome_authorization.dispatch_claim_snapshot_sequence,
                )
                outcome_key = (
                    outcome_authorization.authorization_id,
                    claim_sequence,
                )
                if outcome_key in self._outcome_receipts or any(
                    key[0] == outcome_authorization.authorization_id
                    for key in self._outcome_receipts
                ):
                    raise BudgetLedgerError(
                        Phase2FailureCode.BUDGET_SLOT_ALREADY_CONSUMED,
                        "Specialist authorization already has an outcome receipt",
                    )
                outcome_receipt = SpecialistToolOutcomeReceipt(
                    schema_version=(
                        "phase2.specialist-tool-outcome-receipt.v1"
                    ),
                    outcome_kind=outcome_kind,
                    authorization_id=outcome_authorization.authorization_id,
                    dispatch_claim_snapshot_sequence=claim_sequence,
                    post_outcome_authorization=outcome_authorization,
                    outcome_snapshot=snapshot,
                    tool_call_record_sha256=record_hash,
                    failure_kind=failure_kind,
                    failure_code=(
                        None
                        if outcome_kind is SpecialistToolOutcomeKind.SUCCESS
                        else Phase2FailureCode.TOOL_DISPATCH_FAILED
                    ),
                )
        except BudgetLedgerError:
            raise
        except (TypeError, ValidationError, ValueError) as error:
            raise BudgetLedgerError(
                Phase2FailureCode.BUDGET_SLOT_STALE,
                f"candidate budget state is invalid: {error}",
            ) from error

        self._slots = slots
        self._authorizations = authorizations
        self._leases = leases
        self._bundles = bundles
        self._charge_signatures = charge_signatures
        self._charged_model_calls = charged_model_calls
        self._charged_tool_calls = charged_tool_calls
        self._cumulative_tokens = cumulative_tokens
        self._sequence = sequence
        self._last_elapsed_seconds = elapsed_seconds
        self._snapshot = snapshot
        self._audit_events = (*self._audit_events, event)
        self._used_ids = used_ids
        self._terminal_failure_code = terminal_failure_code
        if outcome_key is not None and outcome_receipt is not None:
            self._outcome_receipts = {
                **self._outcome_receipts,
                outcome_key: outcome_receipt,
            }
        return snapshot

    def _derive_audit_record_ids(
        self,
        *,
        explicit_record_ids: tuple[str, ...],
        slots: dict[str, UnboundCapacitySlot],
        authorizations: dict[str, SpecialistExecutionAuthorization],
        leases: dict[str, BudgetLease],
        bundles: dict[str, ConditionalRefinementBundle],
    ) -> tuple[str, ...]:
        changed_ids: set[str] = set()
        for current, candidate in (
            (self._slots, slots),
            (self._authorizations, authorizations),
            (self._leases, leases),
            (self._bundles, bundles),
        ):
            changed_ids.update(
                record_id
                for record_id in current.keys() | candidate.keys()
                if current.get(record_id) != candidate.get(record_id)
            )
        ordered = list(dict.fromkeys(explicit_record_ids))
        ordered.extend(sorted(changed_ids - set(ordered)))
        if not changed_ids.issubset(ordered):
            raise BudgetLedgerError(
                Phase2FailureCode.BUDGET_SLOT_STALE,
                "audit event does not cover every changed budget record",
            )
        return tuple(ordered)

    def _make_snapshot(
        self,
        *,
        snapshot_id: str,
        sequence: int,
        charged_model_calls: int,
        charged_tool_calls: int,
        cumulative_tokens: int,
        slots: dict[str, UnboundCapacitySlot],
        authorizations: dict[str, SpecialistExecutionAuthorization],
        leases: dict[str, BudgetLease],
        elapsed_seconds: float,
    ) -> BudgetSnapshot:
        reserved_model_calls, reserved_tool_calls, reserved_tokens = (
            self._reservation_totals(
                slots=slots,
                authorizations=authorizations,
                leases=leases,
            )
        )
        return BudgetSnapshot(
            schema_version="phase2.budget-snapshot.v1",
            snapshot_id=snapshot_id,
            run_id=self._run_id,
            variant=self._variant,
            case_id=self._case_id,
            max_model_calls=self._max_model_calls,
            max_tool_calls=self._max_tool_calls,
            max_total_tokens=self._max_total_tokens,
            charged_model_calls=charged_model_calls,
            charged_tool_calls=charged_tool_calls,
            cumulative_tokens=cumulative_tokens,
            reserved_model_calls=reserved_model_calls,
            reserved_tool_calls=reserved_tool_calls,
            reserved_tokens=reserved_tokens,
            remaining_model_calls=(
                self._max_model_calls
                - charged_model_calls
                - reserved_model_calls
            ),
            remaining_tool_calls=(
                self._max_tool_calls - charged_tool_calls - reserved_tool_calls
            ),
            remaining_tokens=(
                self._max_total_tokens - cumulative_tokens - reserved_tokens
            ),
            monotonic_elapsed_seconds=elapsed_seconds,
            sequence=sequence,
            active_capacity_slot_ids=tuple(
                sorted(
                    slot_id
                    for slot_id, slot in slots.items()
                    if slot.status is CapacitySlotStatus.HELD
                )
            ),
            active_specialist_authorization_ids=tuple(
                sorted(
                    authorization_id
                    for authorization_id, authorization in authorizations.items()
                    if authorization.status
                    in _PRE_LEASE_ACTIVE_AUTHORIZATION_STATUSES
                )
            ),
            active_lease_ids=tuple(
                sorted(
                    lease_id
                    for lease_id, lease in leases.items()
                    if lease.status is BudgetLeaseStatus.RESERVED
                )
            ),
        )

    @staticmethod
    def _reservation_totals(
        *,
        slots: dict[str, UnboundCapacitySlot],
        authorizations: dict[str, SpecialistExecutionAuthorization],
        leases: dict[str, BudgetLease],
    ) -> tuple[int, int, int]:
        model_calls = 0
        tool_calls = 0
        tokens = 0
        for slot in slots.values():
            if slot.status is CapacitySlotStatus.HELD:
                model_calls += slot.reserved_model_calls
                tool_calls += slot.reserved_tool_calls
                tokens += slot.minimum_token_floor
        for authorization in authorizations.values():
            if authorization.status in {
                SpecialistAuthorizationStatus.TOOL_AUTHORIZED,
                SpecialistAuthorizationStatus.TOOL_DISPATCHING,
            }:
                model_calls += 1
                tool_calls += 1
                tokens += authorization.minimum_token_floor
            elif authorization.status is SpecialistAuthorizationStatus.TOOL_CHARGED:
                model_calls += 1
                tokens += authorization.minimum_token_floor
        for lease in leases.values():
            if lease.status is BudgetLeaseStatus.RESERVED:
                model_calls += 1
                tokens += lease.reserved_tokens
        return model_calls, tool_calls, tokens

    def _held_future_floors_excluding(self, source_record_id: str) -> int:
        total = 0
        for slot_id, slot in self._slots.items():
            if slot_id != source_record_id and slot.status is CapacitySlotStatus.HELD:
                total += slot.minimum_token_floor
        for authorization_id, authorization in self._authorizations.items():
            if authorization_id != source_record_id and authorization.status in {
                SpecialistAuthorizationStatus.TOOL_AUTHORIZED,
                SpecialistAuthorizationStatus.TOOL_DISPATCHING,
                SpecialistAuthorizationStatus.TOOL_CHARGED,
            }:
                total += authorization.minimum_token_floor
        return total

    @staticmethod
    def _exact_reserved_tokens(leases: dict[str, BudgetLease]) -> int:
        return sum(
            lease.reserved_tokens
            for lease in leases.values()
            if lease.status is BudgetLeaseStatus.RESERVED
        )

    def _active_bundle_for_slot(
        self, slot_id: str
    ) -> ConditionalRefinementBundle | None:
        for bundle in self._bundles.values():
            if bundle.status not in {
                ConditionalRefinementBundleStatus.HELD,
                ConditionalRefinementBundleStatus.PARTIALLY_CONSUMED,
            }:
                continue
            member_ids = (
                bundle.first_judge_capacity_slot_id,
                *bundle.specialist_capacity_slot_ids,
                bundle.final_judge_capacity_slot_id,
            )
            if slot_id in member_ids:
                return bundle
        return None

    def _active_bundle_for_lease(
        self, lease: BudgetLease
    ) -> ConditionalRefinementBundle | None:
        if lease.source_record_id in self._slots:
            return self._active_bundle_for_slot(lease.source_record_id)
        authorization = self._authorizations.get(lease.source_record_id)
        if authorization is None:
            return None
        return self._active_bundle_for_slot(authorization.capacity_slot_id)

    def _abort_bundle_candidates(
        self,
        *,
        bundle: ConditionalRefinementBundle,
        slots: dict[str, UnboundCapacitySlot],
        authorizations: dict[str, SpecialistExecutionAuthorization],
        leases: dict[str, BudgetLease],
        bundles: dict[str, ConditionalRefinementBundle],
    ) -> None:
        member_slot_ids = (
            bundle.first_judge_capacity_slot_id,
            *bundle.specialist_capacity_slot_ids,
            bundle.final_judge_capacity_slot_id,
        )
        for slot_id in member_slot_ids:
            slot = slots[slot_id]
            if slot.status is CapacitySlotStatus.HELD:
                slots[slot_id] = self._slot_with_status(
                    slot, CapacitySlotStatus.RELEASED
                )

        specialist_slot_ids = set(bundle.specialist_capacity_slot_ids)
        for authorization_id, authorization in tuple(authorizations.items()):
            if authorization.capacity_slot_id not in specialist_slot_ids:
                continue
            if authorization.status is SpecialistAuthorizationStatus.TOOL_AUTHORIZED:
                authorizations[authorization_id] = self._authorization_with_status(
                    authorization,
                    SpecialistAuthorizationStatus.RELEASED,
                )
            elif authorization.status in {
                SpecialistAuthorizationStatus.TOOL_CHARGED,
                SpecialistAuthorizationStatus.MODEL_LEASED,
            }:
                if authorization.model_lease_id is not None:
                    lease = leases[authorization.model_lease_id]
                    if lease.status is BudgetLeaseStatus.RESERVED:
                        leases[lease.lease_id] = self._lease_with_status(
                            lease, BudgetLeaseStatus.RETURNED
                        )
                authorizations[authorization_id] = self._authorization_with_status(
                    authorization,
                    SpecialistAuthorizationStatus.FAILED,
                )

        try:
            bundles[bundle.bundle_id] = ConditionalRefinementBundle.model_validate(
                {
                    **bundle.model_dump(mode="python"),
                    "status": ConditionalRefinementBundleStatus.RELEASED,
                }
            )
        except (TypeError, ValidationError, ValueError) as error:
            raise BudgetLedgerError(
                Phase2FailureCode.BUDGET_SLOT_ALREADY_CONSUMED,
                f"conditional bundle abort is invalid: {error}",
            ) from error

    def _has_active_bundle(self) -> bool:
        return any(
            bundle.status
            in {
                ConditionalRefinementBundleStatus.HELD,
                ConditionalRefinementBundleStatus.PARTIALLY_CONSUMED,
            }
            for bundle in self._bundles.values()
        )

    def _bundle_first_judge_is_charged(
        self, bundle: ConditionalRefinementBundle
    ) -> bool:
        return any(
            lease.source_record_id == bundle.first_judge_capacity_slot_id
            and lease.status is BudgetLeaseStatus.CHARGED
            for lease in self._leases.values()
        )

    def _bundle_final_judge_is_ready(
        self, bundle: ConditionalRefinementBundle
    ) -> bool:
        if (
            bundle.status
            is not ConditionalRefinementBundleStatus.PARTIALLY_CONSUMED
            or not self._bundle_first_judge_is_charged(bundle)
        ):
            return False
        authorization_by_slot = {
            authorization.capacity_slot_id: authorization
            for authorization in self._authorizations.values()
        }
        completed_specialists = 0
        for slot_id in bundle.specialist_capacity_slot_ids:
            slot = self._slots[slot_id]
            if slot.status is CapacitySlotStatus.RELEASED:
                continue
            authorization = authorization_by_slot.get(slot_id)
            if (
                slot.status is not CapacitySlotStatus.MATERIALIZED
                or authorization is None
                or authorization.status
                is not SpecialistAuthorizationStatus.COMPLETED
            ):
                return False
            completed_specialists += 1
        return completed_specialists > 0

    def _require_pristine_initialization(
        self, expected_snapshot_sequence: int
    ) -> None:
        self._require_operational()
        self._require_cas(expected_snapshot_sequence)
        if (
            self._sequence != 0
            or self._charged_model_calls != 0
            or self._charged_tool_calls != 0
            or self._cumulative_tokens != 0
            or self._slots
            or self._authorizations
            or self._leases
            or self._bundles
            or self._outcome_receipts
            or self._charge_signatures
            or self._audit_events
            or self._terminal_failure_code is not None
        ):
            raise BudgetLedgerError(
                Phase2FailureCode.BUDGET_SLOT_ALREADY_CONSUMED,
                "workflow initialization requires a pristine sequence-zero ledger",
            )

    def _require_operational(self) -> None:
        if self._callback_active:
            raise BudgetLedgerError(
                Phase2FailureCode.BUDGET_CAS_CONFLICT,
                "same-ledger mutation is forbidden during an injected callback",
            )
        if self._terminal_failure_code is not None:
            raise BudgetLedgerError(
                self._terminal_failure_code,
                "budget ledger is terminal and rejects subsequent calls",
            )

    def _require_cas(self, expected_snapshot_sequence: int) -> None:
        if type(expected_snapshot_sequence) is not int or expected_snapshot_sequence < 0:
            raise BudgetLedgerError(
                Phase2FailureCode.BUDGET_CAS_CONFLICT,
                "snapshot sequence must be a non-negative exact integer",
            )
        if expected_snapshot_sequence != self._sequence:
            raise BudgetLedgerError(
                Phase2FailureCode.BUDGET_CAS_CONFLICT,
                "snapshot compare-and-swap sequence is stale",
            )

    @staticmethod
    def _require_exact_expansion_integers(
        *,
        exact_input_tokens: int,
        minimum_completion_tokens: int,
        max_completion_tokens: int,
    ) -> None:
        values = (
            exact_input_tokens,
            minimum_completion_tokens,
            max_completion_tokens,
        )
        if any(type(value) is not int or value <= 0 for value in values):
            raise BudgetLedgerError(
                Phase2FailureCode.BUDGET_EXACT_EXPANSION_FAILED,
                "exact expansion values must be positive exact integers",
            )
        if max_completion_tokens < minimum_completion_tokens:
            raise BudgetLedgerError(
                Phase2FailureCode.BUDGET_EXACT_EXPANSION_FAILED,
                "maximum completion is below the required minimum",
            )

    @staticmethod
    def _require_request_shape(
        request: CapacitySlotRequest,
        operation: ModelOperation,
        actions: ModelAllowedActions,
    ) -> None:
        if (
            not isinstance(request, CapacitySlotRequest)
            or request.permitted_operation is not operation
            or request.allowed_actions is not actions
        ):
            raise BudgetLedgerError(
                Phase2FailureCode.BUDGET_SLOT_OWNER_MISMATCH,
                "capacity-slot request does not match the workflow boundary",
            )

    @staticmethod
    def _require_specialist_binding(
        *,
        owner_role: BudgetOwnerRole,
        owner_node_id: str,
        source: EvidenceSource,
        tool_name: ReadOnlyToolName,
    ) -> None:
        try:
            _specialist_role, expected_role, expected_tool = _SPECIALIST_BINDINGS[
                source
            ]
        except (KeyError, TypeError) as error:
            raise BudgetLedgerError(
                Phase2FailureCode.BUDGET_SLOT_OWNER_MISMATCH,
                "unknown Specialist evidence source",
            ) from error
        if (
            owner_role is not expected_role
            or tool_name is not expected_tool
            or type(owner_node_id) is not str
            or _IDENTIFIER_RE.fullmatch(owner_node_id) is None
        ):
            raise BudgetLedgerError(
                Phase2FailureCode.BUDGET_SLOT_OWNER_MISMATCH,
                "Specialist role, node, source, and tool binding is inconsistent",
            )

    @staticmethod
    def _owner_for_operation(
        operation: ModelOperation,
    ) -> tuple[BudgetOwnerRole, None]:
        if operation is ModelOperation.COMMANDER_MODEL:
            return BudgetOwnerRole.INCIDENT_COMMANDER, None
        if operation in {
            ModelOperation.FIRST_JUDGE_MODEL,
            ModelOperation.FINAL_JUDGE_MODEL,
        }:
            return BudgetOwnerRole.RCA_JUDGE, None
        if operation is ModelOperation.SINGLE_AGENT_MODEL:
            return BudgetOwnerRole.INCIDENT_COMMANDER, None
        raise BudgetLedgerError(
            Phase2FailureCode.BUDGET_SLOT_OWNER_MISMATCH,
            "operation cannot materialize as a non-Specialist lease",
        )

    def _require_slot(self, slot_id: str) -> UnboundCapacitySlot:
        self._validate_identifier(slot_id)
        try:
            return self._slots[slot_id]
        except KeyError as error:
            raise BudgetLedgerError(
                Phase2FailureCode.BUDGET_SLOT_STALE,
                f"unknown capacity slot {slot_id}",
            ) from error

    def _require_authorization(
        self, authorization_id: str
    ) -> SpecialistExecutionAuthorization:
        self._validate_identifier(authorization_id)
        try:
            return self._authorizations[authorization_id]
        except KeyError as error:
            raise BudgetLedgerError(
                Phase2FailureCode.BUDGET_SLOT_STALE,
                f"unknown Specialist authorization {authorization_id}",
            ) from error

    def _require_lease(self, lease_id: str) -> BudgetLease:
        self._validate_identifier(lease_id)
        try:
            return self._leases[lease_id]
        except KeyError as error:
            raise BudgetLedgerError(
                Phase2FailureCode.BUDGET_SLOT_STALE,
                f"unknown exact model lease {lease_id}",
            ) from error

    @staticmethod
    def _slot_with_status(
        slot: UnboundCapacitySlot,
        status: CapacitySlotStatus,
    ) -> UnboundCapacitySlot:
        try:
            return UnboundCapacitySlot.model_validate(
                {**slot.model_dump(mode="python"), "status": status}
            )
        except (TypeError, ValidationError, ValueError) as error:
            raise BudgetLedgerError(
                Phase2FailureCode.BUDGET_SLOT_STALE,
                f"invalid capacity-slot transition: {error}",
            ) from error

    @staticmethod
    def _authorization_with_status(
        authorization: SpecialistExecutionAuthorization,
        status: SpecialistAuthorizationStatus,
    ) -> SpecialistExecutionAuthorization:
        try:
            return SpecialistExecutionAuthorization.model_validate(
                {**authorization.model_dump(mode="python"), "status": status}
            )
        except (TypeError, ValidationError, ValueError) as error:
            raise BudgetLedgerError(
                Phase2FailureCode.BUDGET_SLOT_ALREADY_CONSUMED,
                f"invalid Specialist authorization transition: {error}",
            ) from error

    @staticmethod
    def _lease_with_status(
        lease: BudgetLease,
        status: BudgetLeaseStatus,
    ) -> BudgetLease:
        try:
            return BudgetLease.model_validate(
                {**lease.model_dump(mode="python"), "status": status}
            )
        except (TypeError, ValidationError, ValueError) as error:
            raise BudgetLedgerError(
                Phase2FailureCode.BUDGET_SLOT_ALREADY_CONSUMED,
                f"invalid model-lease transition: {error}",
            ) from error

    def _invoke_callback(
        self,
        callback: Callable[..., object],
        *,
        detail: str,
        args: tuple[object, ...] = (),
    ) -> object:
        if self._callback_active:
            raise BudgetLedgerError(
                Phase2FailureCode.BUDGET_CAS_CONFLICT,
                "nested injected callbacks are forbidden",
            )
        self._callback_active = True
        try:
            return callback(*args)
        except BudgetLedgerError:
            raise
        except Exception as error:
            raise BudgetLedgerError(
                Phase2FailureCode.BUDGET_SLOT_STALE,
                detail,
            ) from error
        finally:
            self._callback_active = False

    def _utc_now(self) -> datetime:
        try:
            value = self._invoke_callback(
                self._utc_clock,
                detail="UTC clock callback failed",
            )
            if not isinstance(value, datetime):
                raise TypeError("UTC clock result is not a datetime")
            offset = value.utcoffset()
            if offset is None or offset.total_seconds() != 0:
                raise ValueError("UTC clock result is not UTC")
        except BudgetLedgerError:
            raise
        except Exception as error:
            raise BudgetLedgerError(
                Phase2FailureCode.BUDGET_SLOT_STALE,
                "UTC clock returned an invalid timestamp",
            ) from error
        return value

    def _next_elapsed_seconds(self) -> float:
        try:
            reading = cast(
                int | float,
                self._invoke_callback(
                    self._monotonic_clock,
                    detail="monotonic clock callback failed",
                ),
            )
            if type(reading) not in {int, float}:
                raise TypeError("monotonic reading is not numeric")
            measured = float(reading - self._monotonic_origin)
        except BudgetLedgerError:
            raise
        except Exception as error:
            raise BudgetLedgerError(
                Phase2FailureCode.BUDGET_SLOT_STALE,
                "monotonic clock returned an invalid reading",
            ) from error
        return max(self._last_elapsed_seconds, 0.0, measured)

    def _new_id(self, prefix: str, used_ids: set[str]) -> str:
        candidate = cast(
            str,
            (
                self._invoke_callback(
                    self._id_factory,
                    detail=f"ID factory failed for {prefix}",
                    args=(prefix,),
                )
                if self._id_factory is not None
                else f"{prefix}-{len(used_ids) + 1:04d}"
            )
        )
        self._validate_identifier(candidate)
        if candidate in used_ids:
            raise BudgetLedgerError(
                Phase2FailureCode.BUDGET_SLOT_STALE,
                f"generated ID collision for {prefix}",
            )
        return candidate

    @staticmethod
    def _validate_identifier(value: object) -> None:
        if type(value) is not str or _IDENTIFIER_RE.fullmatch(value) is None:
            raise BudgetLedgerError(
                Phase2FailureCode.BUDGET_SLOT_STALE,
                "identifier is not an exact bounded Phase 2 identifier",
            )
