"""Ledger-owned, one-shot tool dispatch for Phase 2 Specialists."""

from __future__ import annotations

from collections.abc import Callable
from enum import Enum

from pydantic import ValidationError

from ecomsre.phase1.contracts import (
    EvidenceSource,
    ReadOnlyToolName,
    ToolAction,
    ToolCallRecord,
)
from ecomsre.phase1.validator import revalidate_phase1_model
from ecomsre.phase2.budgets import BudgetLedger, BudgetLedgerError
from ecomsre.phase2.contracts import (
    Phase2FailureCode,
    Phase2Variant,
    SPECIALIST_TOOL_BINDINGS,
    SpecialistRole,
    SpecialistTask,
    SpecialistToolDispatchResult,
    canonical_tool_call_record_sha256,
)


class ToolIsolationErrorCode(str, Enum):
    INVALID_REGISTRY = "INVALID_REGISTRY"
    INVALID_TASK = "INVALID_TASK"
    ROLE_MISMATCH = "ROLE_MISMATCH"
    AUTHORIZATION_MISMATCH = "AUTHORIZATION_MISMATCH"
    RECORD_MISMATCH = "RECORD_MISMATCH"
    EXECUTOR_FAILURE = "EXECUTOR_FAILURE"


class ToolIsolationError(ValueError):
    def __init__(
        self,
        code: ToolIsolationErrorCode,
        detail: str,
        *,
        phase2_failure_code: Phase2FailureCode | None = None,
    ) -> None:
        self.code = code
        self.phase2_failure_code = phase2_failure_code
        super().__init__(f"{code.value}: {detail}")


class SpecialistToolRegistry:
    """Expose one live ledger-backed tool capability to one Specialist role."""

    def __init__(
        self,
        *,
        run_id: str,
        case_id: str,
        variant: Phase2Variant,
        specialist_role: SpecialistRole,
        ledger: BudgetLedger,
        executor: Callable[[ToolAction], object],
    ) -> None:
        if (
            type(specialist_role) is not SpecialistRole
            or type(variant) is not Phase2Variant
            or variant
            not in {
                Phase2Variant.FIXED_SPECIALIST_WORKFLOW,
                Phase2Variant.DYNAMIC_MULTI_AGENT,
            }
            or not isinstance(ledger, BudgetLedger)
            or not callable(executor)
        ):
            raise ToolIsolationError(
                ToolIsolationErrorCode.INVALID_REGISTRY,
                "registry requires exact closed scope, live ledger, and callable",
            )
        snapshot = ledger.snapshot()
        if (
            type(run_id) is not str
            or type(case_id) is not str
            or snapshot.run_id != run_id
            or snapshot.case_id != case_id
            or snapshot.variant is not variant
        ):
            raise ToolIsolationError(
                ToolIsolationErrorCode.INVALID_REGISTRY,
                "registry scope does not match its authoritative ledger",
            )
        owner_role, source, tool_name = SPECIALIST_TOOL_BINDINGS[
            specialist_role
        ]
        self._run_id = run_id
        self._case_id = case_id
        self._variant = variant
        self._specialist_role = specialist_role
        self._owner_role = owner_role
        self._source = source
        self._tool_name = tool_name
        self._ledger = ledger
        self._executor = executor

    @property
    def specialist_role(self) -> SpecialistRole:
        return self._specialist_role

    @property
    def source(self) -> EvidenceSource:
        return self._source

    @property
    def tool_name(self) -> ReadOnlyToolName:
        return self._tool_name

    @property
    def ledger(self) -> BudgetLedger:
        return self._ledger

    def dispatch(self, task: SpecialistTask) -> SpecialistToolDispatchResult:
        try:
            validated_task = SpecialistTask.model_validate(task)
        except (TypeError, ValidationError, ValueError) as error:
            raise ToolIsolationError(
                ToolIsolationErrorCode.INVALID_TASK,
                str(error),
            ) from error
        if (
            validated_task.run_id != self._run_id
            or validated_task.specialist_role is not self._specialist_role
            or validated_task.source is not self._source
            or validated_task.tool_name is not self._tool_name
        ):
            raise ToolIsolationError(
                ToolIsolationErrorCode.AUTHORIZATION_MISMATCH,
                "task scope does not match this ledger-backed registry",
                phase2_failure_code=Phase2FailureCode.BUDGET_SLOT_OWNER_MISMATCH,
            )

        try:
            live_authorization = self._ledger.specialist_authorization(
                validated_task.tool_authorization_id
            )
        except BudgetLedgerError as error:
            raise ToolIsolationError(
                ToolIsolationErrorCode.AUTHORIZATION_MISMATCH,
                str(error),
                phase2_failure_code=error.code,
            ) from error
        if (
            live_authorization.capacity_slot_id
            != validated_task.model_capacity_slot_id
        ):
            raise ToolIsolationError(
                ToolIsolationErrorCode.AUTHORIZATION_MISMATCH,
                "task capacity slot does not match authorization lineage",
                phase2_failure_code=Phase2FailureCode.BUDGET_SLOT_OWNER_MISMATCH,
            )

        live_snapshot = self._ledger.snapshot()
        try:
            dispatching, _claim_snapshot = (
                self._ledger.claim_specialist_tool_dispatch(
                    expected_snapshot_sequence=live_snapshot.sequence,
                    authorization_id=validated_task.tool_authorization_id,
                    run_id=self._run_id,
                    case_id=self._case_id,
                    variant=self._variant,
                    owner_role=self._owner_role,
                    owner_node_id=validated_task.node_id,
                    source=self._source,
                    tool_name=self._tool_name,
                )
            )
        except BudgetLedgerError as error:
            raise ToolIsolationError(
                ToolIsolationErrorCode.AUTHORIZATION_MISMATCH,
                str(error),
                phase2_failure_code=error.code,
            ) from error

        claim_sequence = dispatching.dispatch_claim_snapshot_sequence
        assert claim_sequence is not None
        try:
            result = self._executor(validated_task.query)
        except Exception as executor_error:
            try:
                self._ledger.fail_specialist_tool_dispatch(
                    authorization_id=dispatching.authorization_id,
                    dispatch_claim_snapshot_sequence=claim_sequence,
                    failure_kind="EXECUTOR_FAILURE",
                )
            except BudgetLedgerError as sealing_error:
                raise ToolIsolationError(
                    ToolIsolationErrorCode.EXECUTOR_FAILURE,
                    f"outcome sealing failed: {sealing_error}",
                    phase2_failure_code=sealing_error.code,
                ) from sealing_error
            raise ToolIsolationError(
                ToolIsolationErrorCode.EXECUTOR_FAILURE,
                f"bound tool executor failed: {type(executor_error).__name__}",
                phase2_failure_code=Phase2FailureCode.TOOL_DISPATCH_FAILED,
            ) from executor_error

        try:
            if type(result) is not ToolCallRecord:
                raise TypeError(
                    "bound executor must return the exact ToolCallRecord type"
                )
            record = revalidate_phase1_model(result, ToolCallRecord)
            if (
                record.status != "OK"
                or not record.usable
                or not record.dispatched
                or record.evidence_quarantined
                or record.run_id != validated_task.run_id
                or record.incident_id != validated_task.incident_id
                or record.task_id != validated_task.node_id
                or record.agent_id != self._specialist_role.value
                or record.tool_name is not self._tool_name
                or record.action != validated_task.query
                or any(
                    item.run_id != validated_task.run_id
                    or item.source is not self._source
                    for item in record.evidence
                )
            ):
                raise ValueError(
                    "tool record conflicts with the bound Specialist task"
                )
            record_hash = canonical_tool_call_record_sha256(record)
        except (TypeError, ValidationError, ValueError) as record_error:
            try:
                self._ledger.fail_specialist_tool_dispatch(
                    authorization_id=dispatching.authorization_id,
                    dispatch_claim_snapshot_sequence=claim_sequence,
                    failure_kind="RECORD_MISMATCH",
                )
            except BudgetLedgerError as sealing_error:
                raise ToolIsolationError(
                    ToolIsolationErrorCode.RECORD_MISMATCH,
                    f"outcome sealing failed: {sealing_error}",
                    phase2_failure_code=sealing_error.code,
                ) from sealing_error
            raise ToolIsolationError(
                ToolIsolationErrorCode.RECORD_MISMATCH,
                str(record_error),
                phase2_failure_code=Phase2FailureCode.TOOL_DISPATCH_FAILED,
            ) from record_error

        try:
            receipt = self._ledger.complete_specialist_tool_dispatch(
                authorization_id=dispatching.authorization_id,
                dispatch_claim_snapshot_sequence=claim_sequence,
                tool_call_record_sha256=record_hash,
            )
        except BudgetLedgerError as error:
            raise ToolIsolationError(
                ToolIsolationErrorCode.AUTHORIZATION_MISMATCH,
                str(error),
                phase2_failure_code=error.code,
            ) from error

        try:
            return SpecialistToolDispatchResult(
                schema_version="phase2.specialist-tool-dispatch-result.v1",
                tool_call_record=record,
                specialist_authorization=receipt.post_outcome_authorization,
                budget_snapshot=receipt.outcome_snapshot,
                outcome_receipt=receipt,
            )
        except (TypeError, ValidationError, ValueError) as error:
            raise ToolIsolationError(
                ToolIsolationErrorCode.INVALID_REGISTRY,
                "outcome already sealed; result projection failed and must "
                f"not be redispatched: {error}",
            ) from error
