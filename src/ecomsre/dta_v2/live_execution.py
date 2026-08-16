"""Exact no-shell Registry-owned forward execution for PR-F."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Protocol

from ecomsre.dta_v2.authorization import AttemptAuthorizationRecord
from ecomsre.dta_v2.contracts import (
    ActionProposal,
    RunbookId,
    RunbookSpec,
    RunbookStepId,
    semantic_sha256,
)
from ecomsre.dta_v2.live_contracts import (
    ForwardExecution,
    ForwardExecutionTerminal,
)
from ecomsre.dta_v2.operational_contracts import (
    AdmissionVerdict,
    CurrentStateSnapshot,
    OperationalAdmission,
    StepOutcome,
    StepReceipt,
)
from ecomsre.dta_v2.registry import RunbookRegistry


class LiveExecutionError(RuntimeError):
    """A safe execution-boundary failure without underlying exception text."""


class ReceiptPersistenceError(LiveExecutionError):
    """Carries a typed receipt when the primary journal could not retain it."""

    def __init__(self, forward_execution: ForwardExecution) -> None:
        super().__init__("step receipt persistence failed")
        self.forward_execution = forward_execution


class PartialExecutionError(LiveExecutionError):
    """Carries an applied prefix when the next write is denied before mutation."""

    def __init__(self, forward_execution: ForwardExecution) -> None:
        super().__init__("partial execution stopped before the next write")
        self.forward_execution = forward_execution


class LiveControls(Protocol):
    source_snapshot_sha256: str
    run_id: str
    attempt_id: str
    target: str
    ownership_digest: str
    forward_write_count: int
    transaction_started: bool
    initial_state_digest: str

    def state_digest(self) -> str: ...

    def revalidate_before_write(
        self,
        authorization: AttemptAuthorizationRecord,
        observed_at: datetime,
    ) -> None: ...

    def restore_payment_configuration(self) -> None: ...

    def start_recommendation_service(self) -> None: ...

    def disable_email_leak_flag(self) -> None: ...

    def restart_email_service(self) -> None: ...


class ReceiptJournal(Protocol):
    def append(self, receipt: StepReceipt) -> None: ...


def _require_utc(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise LiveExecutionError("receipt clock must return UTC")


def _with_digest(model_type, payload: dict[str, object], digest_field: str):
    draft = model_type.model_construct(**payload, **{digest_field: "0" * 64})
    return model_type.model_validate(
        {
            **payload,
            digest_field: semantic_sha256(
                draft.model_dump(mode="json", exclude={digest_field})
            ),
        }
    )


def _validate_bindings(
    *,
    registry: RunbookRegistry,
    proposal: ActionProposal,
    current_state: CurrentStateSnapshot,
    admission: OperationalAdmission,
    authorization: AttemptAuthorizationRecord,
    controls: LiveControls,
) -> RunbookSpec:
    if controls.state_digest() != controls.initial_state_digest:
        raise LiveExecutionError("live state drifted after Operational Admission")
    if admission.verdict is not AdmissionVerdict.ALLOW:
        raise LiveExecutionError("execution binding requires ALLOW admission")
    if proposal.runbook_id is None or proposal.target_service is None:
        raise LiveExecutionError("execution binding requires an executable proposal")
    runbook = registry.require(proposal.runbook_id)
    runbook_sha256 = semantic_sha256(runbook.model_dump(mode="json"))
    if (
        proposal.runbook_sha256 != runbook_sha256
        or proposal.registry_sha256 != registry.registry_sha256
        or admission.registry_sha256 != registry.registry_sha256
        or authorization.registry_sha256 != registry.registry_sha256
        or admission.runbook_sha256 != runbook_sha256
        or authorization.runbook_sha256 != runbook_sha256
        or authorization.runbook_id is not runbook.runbook_id
        or authorization.target_service != proposal.target_service
        or admission.proposal_sha256 != proposal.proposal_sha256
        or authorization.proposal_sha256 != proposal.proposal_sha256
        or admission.authorization_sha256 != authorization.authorization_sha256
        or admission.current_state_sha256 != current_state.snapshot_sha256
        or authorization.current_state_sha256 != current_state.snapshot_sha256
        or admission.candidate_set_sha256 != proposal.candidate_set_sha256
        or admission.candidate_set_sha256 != authorization.candidate_set_sha256
        or admission.resolved_evidence_sha256 != proposal.resolved_evidence_sha256
        or admission.resolved_evidence_sha256
        != authorization.resolved_evidence_sha256
        or controls.source_snapshot_sha256 != current_state.snapshot_sha256
        or controls.run_id != proposal.run_id
        or controls.attempt_id != authorization.attempt_id
        or controls.target != proposal.target_service
        or controls.ownership_digest != current_state.ownership_digest
        or controls.forward_write_count != current_state.prior_forward_step_count
        or controls.transaction_started
    ):
        raise LiveExecutionError("live execution binding differs")
    if len(runbook.forward_steps) > runbook.maximum_forward_steps:
        raise LiveExecutionError("Registry steps exceed the forward-step cap")
    expected_executor = {
        RunbookId.ROLLBACK_CONFIGURATION: "FeatureFlagRollbackExecutor",
        RunbookId.RESTART_SERVICE: "DockerServiceRestartExecutor",
        RunbookId.MITIGATE_MEMORY_LEAK: "MemoryLeakMitigationExecutor",
    }[runbook.runbook_id]
    if runbook.executor_id != expected_executor:
        raise LiveExecutionError("Registry Executor identity differs")
    return runbook


def _operation_for(
    *,
    runbook: RunbookSpec,
    step_id: RunbookStepId,
    controls: LiveControls,
) -> Callable[[], None]:
    exact = (runbook.runbook_id, step_id, controls.target)
    if exact == (
        RunbookId.ROLLBACK_CONFIGURATION,
        RunbookStepId.RESTORE_BASELINE_CONFIGURATION,
        "payment",
    ):
        return controls.restore_payment_configuration
    if exact == (
        RunbookId.RESTART_SERVICE,
        RunbookStepId.RESTART_OWNED_SERVICE,
        "recommendation",
    ):
        return controls.start_recommendation_service
    if exact == (
        RunbookId.MITIGATE_MEMORY_LEAK,
        RunbookStepId.DISABLE_LEAK_FLAG,
        "email",
    ):
        return controls.disable_email_leak_flag
    if exact == (
        RunbookId.MITIGATE_MEMORY_LEAK,
        RunbookStepId.RESTART_OWNED_SERVICE,
        "email",
    ):
        return controls.restart_email_service
    raise LiveExecutionError("Registry step has no exact trusted operation")


def execute_live_forward_steps(
    *,
    registry: RunbookRegistry,
    proposal: ActionProposal,
    current_state: CurrentStateSnapshot,
    admission: OperationalAdmission,
    authorization: AttemptAuthorizationRecord,
    controls: LiveControls,
    receipt_journal: ReceiptJournal,
    utc_now: Callable[[], datetime],
) -> ForwardExecution:
    """Attempt each fixed forward step once and persist its receipt immediately."""

    registry = RunbookRegistry.model_validate(registry.model_dump(mode="python"))
    proposal = ActionProposal.model_validate(proposal.model_dump(mode="python"))
    current_state = CurrentStateSnapshot.model_validate(
        current_state.model_dump(mode="python")
    )
    admission = OperationalAdmission.model_validate(admission.model_dump(mode="python"))
    authorization = AttemptAuthorizationRecord.model_validate(
        authorization.model_dump(mode="python")
    )
    execution_as_of = utc_now()
    _require_utc(execution_as_of)
    if not authorization.issued_at <= execution_as_of < authorization.expires_at:
        raise LiveExecutionError("attempt authorization expired before execution")
    runbook = _validate_bindings(
        registry=registry,
        proposal=proposal,
        current_state=current_state,
        admission=admission,
        authorization=authorization,
        controls=controls,
    )
    controls.transaction_started = True
    transaction_id = f"txn:{authorization.attempt_id}"
    receipts: list[StepReceipt] = []
    for ordinal, step in enumerate(runbook.forward_steps, start=1):
        try:
            if controls.forward_write_count >= runbook.maximum_forward_steps:
                raise LiveExecutionError("forward-step cap reached")
            write_as_of = utc_now()
            _require_utc(write_as_of)
            if not authorization.issued_at <= write_as_of < authorization.expires_at:
                raise LiveExecutionError("attempt authorization expired before write")
            controls.revalidate_before_write(authorization, write_as_of)
            mutation_as_of = utc_now()
            _require_utc(mutation_as_of)
            if not authorization.issued_at <= mutation_as_of < authorization.expires_at:
                raise LiveExecutionError(
                    "attempt authorization expired during write revalidation"
                )
            before = controls.state_digest()
            expected_before = (
                controls.initial_state_digest
                if not receipts
                else receipts[-1].after_state_digest
            )
            if before != expected_before:
                raise LiveExecutionError("pre-write live state continuity drifted")
            started_at = utc_now()
            _require_utc(started_at)
            if not authorization.issued_at <= started_at < authorization.expires_at:
                raise LiveExecutionError(
                    "attempt authorization expired at operation start"
                )
        except Exception as error:
            if receipts:
                partial = _build_forward_execution(
                    proposal=proposal,
                    admission=admission,
                    authorization=authorization,
                    runbook=runbook,
                    transaction_id=transaction_id,
                    receipts=tuple(receipts),
                    terminal=ForwardExecutionTerminal.PARTIALLY_APPLIED,
                )
                raise PartialExecutionError(partial) from error
            if isinstance(error, LiveExecutionError):
                raise
            raise LiveExecutionError(
                "pre-write authority or live state validation failed"
            ) from error
        operation = _operation_for(
            runbook=runbook,
            step_id=step.step_id,
            controls=controls,
        )
        error_code: str | None = None
        try:
            operation()
        except Exception:
            error_code = "LIVE_STEP_FAILED"
        try:
            ended_at = utc_now()
            _require_utc(ended_at)
            after = controls.state_digest()
        except Exception:
            ended_at = started_at
            after = before
            error_code = "POST_WRITE_STATE_UNKNOWN"
        if error_code is None and after == before:
            error_code = "STATE_CHANGE_NOT_OBSERVED"
        payload: dict[str, object] = {
            "schema_version": "dta-v2.step-receipt.v1",
            "run_id": proposal.run_id,
            "attempt_id": authorization.attempt_id,
            "transaction_id": transaction_id,
            "step_ordinal": ordinal,
            "step_id": step.step_id,
            "target": proposal.target_service,
            "before_state_digest": before,
            "after_state_digest": after,
            "start_time": started_at,
            "end_time": ended_at,
            "outcome": (
                StepOutcome.FAILED if error_code is not None else StepOutcome.APPLIED
            ),
            "error_code": error_code,
        }
        receipt = StepReceipt.model_validate(
            _with_digest(StepReceipt, payload, "receipt_sha256")
        )
        receipts.append(receipt)
        try:
            receipt_journal.append(receipt)
        except Exception as error:
            failed_persistence = _build_forward_execution(
                proposal=proposal,
                admission=admission,
                authorization=authorization,
                runbook=runbook,
                transaction_id=transaction_id,
                receipts=tuple(receipts),
                terminal=ForwardExecutionTerminal.EVIDENCE_PERSISTENCE_FAILED,
            )
            raise ReceiptPersistenceError(failed_persistence) from error
        if receipt.outcome is StepOutcome.FAILED:
            terminal = (
                ForwardExecutionTerminal.PARTIALLY_APPLIED
                if any(item.outcome is StepOutcome.APPLIED for item in receipts[:-1])
                else ForwardExecutionTerminal.EXECUTION_FAILED
            )
            return _build_forward_execution(
                proposal=proposal,
                admission=admission,
                authorization=authorization,
                runbook=runbook,
                transaction_id=transaction_id,
                receipts=tuple(receipts),
                terminal=terminal,
            )
    return _build_forward_execution(
        proposal=proposal,
        admission=admission,
        authorization=authorization,
        runbook=runbook,
        transaction_id=transaction_id,
        receipts=tuple(receipts),
        terminal=ForwardExecutionTerminal.APPLIED,
    )


def _build_forward_execution(
    *,
    proposal: ActionProposal,
    admission: OperationalAdmission,
    authorization: AttemptAuthorizationRecord,
    runbook: RunbookSpec,
    transaction_id: str,
    receipts: tuple[StepReceipt, ...],
    terminal: ForwardExecutionTerminal,
) -> ForwardExecution:
    payload: dict[str, object] = {
        "schema_version": "dta-v2.forward-execution.v1",
        "run_id": proposal.run_id,
        "attempt_id": authorization.attempt_id,
        "transaction_id": transaction_id,
        "runbook_id": runbook.runbook_id,
        "target": authorization.target_service,
        "proposal_sha256": proposal.proposal_sha256,
        "admission_sha256": admission.admission_sha256,
        "authorization_sha256": authorization.authorization_sha256,
        "maximum_forward_steps": runbook.maximum_forward_steps,
        "forward_step_count": len(receipts),
        "receipts": receipts,
        "terminal": terminal,
        "escalation_required": terminal is not ForwardExecutionTerminal.APPLIED,
    }
    return ForwardExecution.model_validate(
        _with_digest(ForwardExecution, payload, "forward_execution_sha256")
    )
