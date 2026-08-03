"""Strictly one-call, planning-only Commander runtime for Dynamic Phase 2."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from threading import RLock
from typing import Literal, cast

from pydantic import ValidationError, field_validator, model_validator

from ecomsre.phase1.contracts import EvidenceSource, Incident, ReadOnlyToolName
from ecomsre.phase2.budgets import BudgetLedger, BudgetLedgerError
from ecomsre.phase2.comparison_adapter import (
    ComparisonAdapter,
    ComparisonAdapterError,
    ModelCallResult,
    ModelInvocation,
)
from ecomsre.phase2.contracts import (
    CapacitySlotRequest,
    CommanderRequest,
    InitialDagAdmission,
    InvestigationPlan,
    ModelAllowedActions,
    ModelOperation,
    Phase2FailureCode,
    Phase2Model,
    Phase2Variant,
    RunId,
    SourceCapability,
    SpecialistRole,
)
from ecomsre.phase2.dag import (
    DagAdmissionContext,
    DagValidationError,
    DagValidationErrorCode,
    admit_initial_plan,
)
from ecomsre.phase2.token_policy import build_model_input_envelope


_COMMANDER_KEY = (
    ModelOperation.COMMANDER_MODEL,
    ModelAllowedActions.PLAN_ONLY,
)
_SPECIALIST_KEY = (
    ModelOperation.SPECIALIST_MODEL,
    ModelAllowedActions.FINDING_ONLY,
)
_FIRST_JUDGE_KEY = (
    ModelOperation.FIRST_JUDGE_MODEL,
    ModelAllowedActions.FINAL_ONLY,
)


class CommanderErrorCode(str, Enum):
    INVALID_CONTEXT = "INVALID_CONTEXT"
    ALREADY_INVOKED = "ALREADY_INVOKED"
    INVALID_PLAN = "INVALID_PLAN"


class CommanderError(ValueError):
    """Stable Commander boundary error without provider-controlled text."""

    def __init__(
        self,
        code: CommanderErrorCode | DagValidationErrorCode | Phase2FailureCode,
        detail: str,
    ) -> None:
        self.code = code
        super().__init__(f"{code.value}: {detail}")


class CommanderContext(Phase2Model):
    """Agent-visible planning context, excluding runtime slot identities."""

    schema_version: Literal["phase2.commander-context.v1"]
    run_id: RunId
    incident: Incident
    allowed_started_at: datetime
    allowed_ended_at: datetime

    @field_validator("incident", mode="before")
    @classmethod
    def revalidate_incident(cls, value: object) -> Incident:
        return Incident.model_validate(value)

    @model_validator(mode="after")
    def require_bounded_window(self) -> CommanderContext:
        if (
            self.allowed_ended_at < self.allowed_started_at
            or self.allowed_started_at < self.incident.started_at
            or self.allowed_ended_at > self.incident.ended_at
        ):
            raise ValueError("Commander window must be inside the Incident window")
        return self


@dataclass(frozen=True, slots=True)
class CommanderOutcome:
    """Charged model result plus the runtime-owned initial DAG admission."""

    request: CommanderRequest
    call: ModelCallResult
    plan: InvestigationPlan
    admission: InitialDagAdmission


def source_capabilities() -> tuple[
    SourceCapability,
    SourceCapability,
    SourceCapability,
    SourceCapability,
]:
    """Return the one frozen source/role/tool/action ordering."""

    return (
        SourceCapability(
            source=EvidenceSource.METRICS,
            specialist_role=SpecialistRole.METRICS_AGENT,
            tool_name=ReadOnlyToolName.QUERY_METRICS,
            action_type="metrics",
        ),
        SourceCapability(
            source=EvidenceSource.LOGS,
            specialist_role=SpecialistRole.LOGS_AGENT,
            tool_name=ReadOnlyToolName.SEARCH_LOGS,
            action_type="logs",
        ),
        SourceCapability(
            source=EvidenceSource.TRACES,
            specialist_role=SpecialistRole.TRACE_AGENT,
            tool_name=ReadOnlyToolName.SEARCH_TRACES,
            action_type="traces",
        ),
        SourceCapability(
            source=EvidenceSource.CHANGES,
            specialist_role=SpecialistRole.CHANGE_AGENT,
            tool_name=ReadOnlyToolName.LIST_CHANGES,
            action_type="changes",
        ),
    )


class CommanderRuntime:
    """Expose one method that can consume the Commander capacity only once."""

    def __init__(
        self,
        *,
        ledger: BudgetLedger,
        adapter: ComparisonAdapter,
        utc_clock: Callable[[], datetime],
    ) -> None:
        if not isinstance(ledger, BudgetLedger):
            raise TypeError("ledger must be BudgetLedger")
        if not isinstance(adapter, ComparisonAdapter):
            raise TypeError("adapter must be ComparisonAdapter")
        if adapter._ledger is not ledger:  # noqa: SLF001 - same-ledger authority
            raise CommanderError(
                Phase2FailureCode.COMPARISON_ADAPTER_BYPASS,
                "Commander ledger differs from adapter ledger",
            )
        if ledger.snapshot().variant is not Phase2Variant.DYNAMIC_MULTI_AGENT:
            raise CommanderError(
                CommanderErrorCode.INVALID_CONTEXT,
                "Commander requires a Dynamic ledger",
            )
        if not callable(utc_clock):
            raise TypeError("utc_clock must be callable")
        self._ledger = ledger
        self._adapter = adapter
        self._utc_clock = utc_clock
        self._invoked = False
        self._lock = RLock()

    def create_initial_graph(self, context: CommanderContext) -> CommanderOutcome:
        """Initialize capacity, call Commander once, and atomically admit its DAG."""

        with self._lock:
            if self._invoked:
                raise CommanderError(
                    CommanderErrorCode.ALREADY_INVOKED,
                    "Commander cannot be invoked twice",
                )
            try:
                context = CommanderContext.model_validate(context)
            except (TypeError, ValidationError, ValueError) as error:
                raise CommanderError(
                    CommanderErrorCode.INVALID_CONTEXT,
                    "Commander context violates its closed contract",
                ) from error
            request_snapshot = self._ledger.snapshot()
            if (
                context.run_id != request_snapshot.run_id
                or request_snapshot.sequence != 0
                or request_snapshot.charged_model_calls != 0
                or request_snapshot.charged_tool_calls != 0
                or request_snapshot.reserved_model_calls != 0
                or request_snapshot.reserved_tool_calls != 0
                or request_snapshot.reserved_tokens != 0
            ):
                raise CommanderError(
                    CommanderErrorCode.INVALID_CONTEXT,
                    "Commander requires a pristine same-run budget",
                )
            authority = self._adapter.token_authority
            request = CommanderRequest(
                schema_version="phase2.commander-request.v1",
                run_id=context.run_id,
                incident=context.incident,
                source_capabilities=source_capabilities(),
                allowed_started_at=context.allowed_started_at,
                allowed_ended_at=context.allowed_ended_at,
                budget_snapshot=request_snapshot,
                model_snapshot=authority.core.model_snapshot,
                token_policy_core_sha256=authority.core_sha256,
            )
            envelope = build_model_input_envelope(
                authority.core,
                _COMMANDER_KEY[0],
                _COMMANDER_KEY[1],
                request,
            )
            commander_golden = authority.golden(*_COMMANDER_KEY)
            specialist_golden = authority.golden(*_SPECIALIST_KEY)
            first_judge_golden = authority.golden(*_FIRST_JUDGE_KEY)
            commander_floor = (
                authority.exact_input_tokens(envelope)
                + commander_golden.minimum_completion_tokens
            )
            now = self._utc_clock()
            expires_at = now + timedelta(minutes=5)
            try:
                slots, initialized_snapshot = self._ledger.initialize_dynamic(
                    expected_snapshot_sequence=request_snapshot.sequence,
                    commander=CapacitySlotRequest(
                        permitted_operation=_COMMANDER_KEY[0],
                        allowed_actions=_COMMANDER_KEY[1],
                        reserved_model_calls=1,
                        reserved_tool_calls=0,
                        minimum_token_floor=commander_floor,
                        expires_at=expires_at,
                    ),
                    specialist=CapacitySlotRequest(
                        permitted_operation=_SPECIALIST_KEY[0],
                        allowed_actions=_SPECIALIST_KEY[1],
                        reserved_model_calls=1,
                        reserved_tool_calls=1,
                        minimum_token_floor=(
                            specialist_golden.minimum_call_floor_tokens
                        ),
                        expires_at=expires_at,
                    ),
                    first_judge=CapacitySlotRequest(
                        permitted_operation=_FIRST_JUDGE_KEY[0],
                        allowed_actions=_FIRST_JUDGE_KEY[1],
                        reserved_model_calls=1,
                        reserved_tool_calls=0,
                        minimum_token_floor=(
                            first_judge_golden.minimum_call_floor_tokens
                        ),
                        expires_at=expires_at,
                    ),
                )
            except (BudgetLedgerError, TypeError, ValidationError, ValueError) as error:
                code = getattr(
                    error,
                    "code",
                    Phase2FailureCode.BUDGET_MINIMUM_FLOOR_UNAVAILABLE,
                )
                raise CommanderError(code, "Dynamic capacity initialization failed") from error
            self._invoked = True
            commander_slot, bootstrap_slot, first_judge_slot = slots
            invocation = ModelInvocation(
                schema_version="phase2.model-invocation.v1",
                invocation_id=f"commander-{request_snapshot.snapshot_id}",
                run_id=context.run_id,
                variant=Phase2Variant.DYNAMIC_MULTI_AGENT,
                case_id=initialized_snapshot.case_id,
                operation=_COMMANDER_KEY[0],
                allowed_actions=_COMMANDER_KEY[1],
                request=request,
                provider_parameters=self._adapter.provider_parameters,
                token_policy_core_sha256=authority.core_sha256,
                response_schema_sha256=commander_golden.response_schema_sha256,
                expected_snapshot_sequence=initialized_snapshot.sequence,
                source_record_id=commander_slot.slot_id,
            )
            try:
                call = self._adapter.invoke(invocation)
            except ComparisonAdapterError as error:
                raise CommanderError(error.code, "Commander model call failed") from error
            if type(call.response) is not InvestigationPlan:
                raise CommanderError(
                    CommanderErrorCode.INVALID_PLAN,
                    "Commander did not return one exact InvestigationPlan",
                )
            plan = cast(InvestigationPlan, call.response)
            admission_context = DagAdmissionContext(
                schema_version="phase2.dag-admission-context.v2",
                run_id=context.run_id,
                incident=context.incident,
                allowed_started_at=context.allowed_started_at,
                allowed_ended_at=context.allowed_ended_at,
                commander_request_snapshot_id=request_snapshot.snapshot_id,
                current_budget_snapshot=call.snapshot,
            )
            try:
                admission, _ = admit_initial_plan(
                    plan,
                    admission_context,
                    self._ledger,
                    commander_slot_id=commander_slot.slot_id,
                    bootstrap_specialist_slot_id=bootstrap_slot.slot_id,
                    first_judge_slot_id=first_judge_slot.slot_id,
                    specialist_floor_tokens=(
                        specialist_golden.minimum_call_floor_tokens
                    ),
                    first_judge_floor_tokens=(
                        first_judge_golden.minimum_call_floor_tokens
                    ),
                )
            except DagValidationError as error:
                raise CommanderError(error.code, "Commander plan admission failed") from error
            return CommanderOutcome(
                request=request,
                call=call,
                plan=plan,
                admission=admission,
            )
