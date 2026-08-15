"""Deterministic fake-only DTA v2 Executors and Verifiers."""

from __future__ import annotations

from datetime import datetime, timedelta
from enum import Enum

from ecomsre.dta_v2.authorization import AttemptAuthorizationRecord
from ecomsre.dta_v2.contracts import (
    ActionDisposition,
    ActionProposal,
    RunbookId,
    RunbookSpec,
    RunbookStepId,
    semantic_sha256,
)
from ecomsre.dta_v2.operational_contracts import (
    AdmissionVerdict,
    CurrentStateSnapshot,
    ExecutionTerminal,
    ExecutionTransaction,
    OperationalAdmission,
    ServiceRuntimeState,
    StepOutcome,
    StepReceipt,
    VerificationOutcome,
    VerificationResult,
)
from ecomsre.dta_v2.registry import RunbookRegistry


class ExecutionErrorCode(str, Enum):
    ADMISSION_DENIED = "ADMISSION_DENIED"
    BINDING_MISMATCH = "BINDING_MISMATCH"
    SECOND_TRANSACTION = "SECOND_TRANSACTION"
    STEP_CAP_EXCEEDED = "STEP_CAP_EXCEEDED"
    EXECUTOR_MISMATCH = "EXECUTOR_MISMATCH"
    VERIFIER_MISMATCH = "VERIFIER_MISMATCH"


class ExecutionError(RuntimeError):
    def __init__(self, code: ExecutionErrorCode, detail: str) -> None:
        self.code = code
        super().__init__(f"{code.value}: {detail}")


class FakeBackend:
    """One process-local fake state; it performs no Docker or host operation."""

    def __init__(
        self,
        *,
        snapshot: CurrentStateSnapshot,
        fail_steps: frozenset[RunbookStepId],
        force_verifier_failure: bool,
    ) -> None:
        self.run_id = snapshot.run_id
        self.attempt_id = snapshot.attempt_id
        self.target = snapshot.target_logical_service
        self.ownership_digest = snapshot.ownership_digest
        self.configuration_state_digest = snapshot.configuration_state_digest
        self.baseline_digest = snapshot.baseline_digest
        self.service_runtime_state = snapshot.service_runtime_state
        self.leak_flag_active = any(
            item.precondition.value == "LEAK_FLAG_ACTIVE" and item.satisfied
            for item in snapshot.preconditions
        )
        self.state_version = 0
        self.forward_write_count = snapshot.prior_forward_step_count
        self.source_snapshot_sha256 = snapshot.snapshot_sha256
        self.fail_steps = fail_steps
        self.force_verifier_failure = force_verifier_failure
        self.transaction_started = False
        self.executor: _FakeExecutor | None = None
        self.verifier: _FakeVerifier | None = None
        self._clock = snapshot.observed_at_end
        self.initial_state_digest = self.state_digest()

    @classmethod
    def from_snapshot(
        cls,
        snapshot: CurrentStateSnapshot,
        *,
        fail_steps: frozenset[RunbookStepId] = frozenset(),
        force_verifier_failure: bool = False,
    ) -> FakeBackend:
        snapshot = CurrentStateSnapshot.model_validate(
            snapshot.model_dump(mode="python")
        )
        return cls(
            snapshot=snapshot,
            fail_steps=fail_steps,
            force_verifier_failure=force_verifier_failure,
        )

    def state_digest(self) -> str:
        return semantic_sha256(
            {
                "run_id": self.run_id,
                "attempt_id": self.attempt_id,
                "target": self.target,
                "ownership_digest": self.ownership_digest,
                "configuration_state_digest": self.configuration_state_digest,
                "baseline_digest": self.baseline_digest,
                "service_runtime_state": self.service_runtime_state.value,
                "leak_flag_active": self.leak_flag_active,
                "state_version": self.state_version,
            }
        )

    def start_transaction(self) -> None:
        if self.transaction_started:
            raise ExecutionError(
                ExecutionErrorCode.SECOND_TRANSACTION,
                "the attempt already started a transaction",
            )
        self.transaction_started = True

    def step_window(self) -> tuple[datetime, datetime]:
        start = self._clock
        end = start + timedelta(milliseconds=1)
        self._clock = end
        return start, end


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


def validate_step_receipt(receipt: StepReceipt) -> StepReceipt:
    return StepReceipt.model_validate(receipt.model_dump(mode="python"))


class _FakeExecutor:
    trusted_executor_id: str
    allowed_steps: tuple[RunbookStepId, ...]

    def execute_step(
        self,
        *,
        backend: FakeBackend,
        runbook: RunbookSpec,
        transaction_id: str,
        ordinal: int,
        step_id: RunbookStepId,
    ) -> StepReceipt:
        if step_id not in self.allowed_steps:
            raise ExecutionError(
                ExecutionErrorCode.EXECUTOR_MISMATCH,
                "the fake Executor does not implement the selected step",
            )
        if backend.forward_write_count >= runbook.maximum_forward_steps:
            raise ExecutionError(
                ExecutionErrorCode.STEP_CAP_EXCEEDED,
                "the forward-step cap has been reached",
            )
        before = backend.state_digest()
        start, end = backend.step_window()
        backend.forward_write_count += 1
        if step_id in backend.fail_steps:
            outcome = StepOutcome.FAILED
            error_code = "FAKE_STEP_FAILURE"
        else:
            if step_id is RunbookStepId.RESTORE_BASELINE_CONFIGURATION:
                backend.configuration_state_digest = backend.baseline_digest
                backend.service_runtime_state = ServiceRuntimeState.RUNNING_HEALTHY
            elif step_id is RunbookStepId.RESTART_OWNED_SERVICE:
                backend.service_runtime_state = ServiceRuntimeState.RUNNING_HEALTHY
            elif step_id is RunbookStepId.DISABLE_LEAK_FLAG:
                backend.leak_flag_active = False
            backend.state_version += 1
            outcome = StepOutcome.APPLIED
            error_code = None
        after = backend.state_digest()
        payload: dict[str, object] = {
            "schema_version": "dta-v2.step-receipt.v1",
            "run_id": backend.run_id,
            "attempt_id": backend.attempt_id,
            "transaction_id": transaction_id,
            "step_ordinal": ordinal,
            "step_id": step_id,
            "target": backend.target,
            "before_state_digest": before,
            "after_state_digest": after,
            "start_time": start,
            "end_time": end,
            "outcome": outcome,
            "error_code": error_code,
        }
        return StepReceipt.model_validate(
            _with_digest(StepReceipt, payload, "receipt_sha256")
        )


class FakeFeatureFlagRollbackExecutor(_FakeExecutor):
    trusted_executor_id = "FeatureFlagRollbackExecutor"
    allowed_steps = (RunbookStepId.RESTORE_BASELINE_CONFIGURATION,)


class FakeDockerServiceRestartExecutor(_FakeExecutor):
    trusted_executor_id = "DockerServiceRestartExecutor"
    allowed_steps = (RunbookStepId.RESTART_OWNED_SERVICE,)


class FakeMemoryLeakMitigationExecutor(_FakeExecutor):
    trusted_executor_id = "MemoryLeakMitigationExecutor"
    allowed_steps = (
        RunbookStepId.DISABLE_LEAK_FLAG,
        RunbookStepId.RESTART_OWNED_SERVICE,
    )


class _FakeVerifier:
    trusted_verifier_id: str

    def _state_passes(
        self,
        *,
        backend: FakeBackend,
        receipts: tuple[StepReceipt, ...],
    ) -> bool:
        raise NotImplementedError

    def verify(
        self,
        *,
        backend: FakeBackend,
        runbook: RunbookSpec,
        transaction_id: str,
        receipts: tuple[StepReceipt, ...],
    ) -> VerificationResult:
        checked = tuple(validate_step_receipt(receipt) for receipt in receipts)
        state_passes = self._state_passes(backend=backend, receipts=checked)
        passed = state_passes and not backend.force_verifier_failure
        payload: dict[str, object] = {
            "schema_version": "dta-v2.verification-result.v1",
            "run_id": backend.run_id,
            "attempt_id": backend.attempt_id,
            "transaction_id": transaction_id,
            "runbook_id": runbook.runbook_id,
            "verifier_id": self.trusted_verifier_id,
            "outcome": VerificationOutcome.PASS if passed else VerificationOutcome.FAIL,
            "infrastructure_passed": passed,
            "business_sli_passed": passed,
            "receipt_sha256s": tuple(
                receipt.receipt_sha256 for receipt in checked
            ),
            "reason_codes": (
                ("VERIFIED",)
                if passed
                else ("FAKE_VERIFICATION_FAILED",)
            ),
        }
        return VerificationResult.model_validate(
            _with_digest(VerificationResult, payload, "verification_sha256")
        )


class FakeConfigurationRecoveryVerifier(_FakeVerifier):
    trusted_verifier_id = "ConfigurationRecoveryVerifier"

    def _state_passes(
        self,
        *,
        backend: FakeBackend,
        receipts: tuple[StepReceipt, ...],
    ) -> bool:
        return (
            len(receipts) == 1
            and receipts[0].outcome is StepOutcome.APPLIED
            and backend.configuration_state_digest == backend.baseline_digest
            and backend.service_runtime_state is ServiceRuntimeState.RUNNING_HEALTHY
        )


class FakeServiceRecoveryVerifier(_FakeVerifier):
    trusted_verifier_id = "ServiceRecoveryVerifier"

    def _state_passes(
        self,
        *,
        backend: FakeBackend,
        receipts: tuple[StepReceipt, ...],
    ) -> bool:
        return (
            len(receipts) == 1
            and receipts[0].outcome is StepOutcome.APPLIED
            and backend.service_runtime_state is ServiceRuntimeState.RUNNING_HEALTHY
        )


class FakeMemoryLeakRecoveryVerifier(_FakeVerifier):
    trusted_verifier_id = "MemoryLeakRecoveryVerifier"

    def _state_passes(
        self,
        *,
        backend: FakeBackend,
        receipts: tuple[StepReceipt, ...],
    ) -> bool:
        return (
            tuple(receipt.step_id for receipt in receipts)
            == (
                RunbookStepId.DISABLE_LEAK_FLAG,
                RunbookStepId.RESTART_OWNED_SERVICE,
            )
            and all(receipt.outcome is StepOutcome.APPLIED for receipt in receipts)
            and not backend.leak_flag_active
            and backend.service_runtime_state is ServiceRuntimeState.RUNNING_HEALTHY
        )


def _runtime_for(
    runbook: RunbookSpec,
) -> tuple[_FakeExecutor, _FakeVerifier]:
    runtimes: dict[RunbookId, tuple[_FakeExecutor, _FakeVerifier]] = {
        RunbookId.ROLLBACK_CONFIGURATION: (
            FakeFeatureFlagRollbackExecutor(),
            FakeConfigurationRecoveryVerifier(),
        ),
        RunbookId.RESTART_SERVICE: (
            FakeDockerServiceRestartExecutor(),
            FakeServiceRecoveryVerifier(),
        ),
        RunbookId.MITIGATE_MEMORY_LEAK: (
            FakeMemoryLeakMitigationExecutor(),
            FakeMemoryLeakRecoveryVerifier(),
        ),
    }
    executor, verifier = runtimes[runbook.runbook_id]
    if executor.trusted_executor_id != runbook.executor_id:
        raise ExecutionError(
            ExecutionErrorCode.EXECUTOR_MISMATCH,
            "trusted Executor identity differs from the Registry",
        )
    if verifier.trusted_verifier_id != runbook.verifier_id:
        raise ExecutionError(
            ExecutionErrorCode.VERIFIER_MISMATCH,
            "trusted Verifier identity differs from the Registry",
        )
    return executor, verifier


def _transaction(
    *,
    proposal: ActionProposal,
    admission: OperationalAdmission,
    authorization: AttemptAuthorizationRecord,
    runbook: RunbookSpec,
    transaction_id: str,
    receipts: tuple[StepReceipt, ...],
    verification: VerificationResult | None,
    terminal: ExecutionTerminal,
    final_disposition: ActionDisposition,
) -> ExecutionTransaction:
    payload: dict[str, object] = {
        "schema_version": "dta-v2.execution-transaction.v1",
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
        "verification": verification,
        "terminal": terminal,
        "final_disposition": final_disposition,
    }
    return ExecutionTransaction.model_validate(
        _with_digest(ExecutionTransaction, payload, "transaction_sha256")
    )


def execute_fake_transaction(
    *,
    registry: RunbookRegistry,
    proposal: ActionProposal,
    current_state: CurrentStateSnapshot,
    admission: OperationalAdmission,
    authorization: AttemptAuthorizationRecord,
    backend: FakeBackend,
) -> ExecutionTransaction:
    """Execute only Registry-fixed steps against one in-memory fake backend."""

    registry = RunbookRegistry.model_validate(registry.model_dump(mode="python"))
    proposal = ActionProposal.model_validate(proposal.model_dump(mode="python"))
    current_state = CurrentStateSnapshot.model_validate(
        current_state.model_dump(mode="python")
    )
    admission = OperationalAdmission.model_validate(
        admission.model_dump(mode="python")
    )
    authorization = AttemptAuthorizationRecord.model_validate(
        authorization.model_dump(mode="python")
    )
    if admission.verdict is not AdmissionVerdict.ALLOW:
        raise ExecutionError(
            ExecutionErrorCode.ADMISSION_DENIED,
            "Operational Admission did not issue ALLOW",
        )
    if proposal.runbook_id is None or proposal.target_service is None:
        raise ExecutionError(
            ExecutionErrorCode.BINDING_MISMATCH,
            "the proposal is not executable",
        )
    runbook = registry.require(proposal.runbook_id)
    trusted_runbook_sha256 = semantic_sha256(runbook.model_dump(mode="json"))
    if backend.transaction_started:
        raise ExecutionError(
            ExecutionErrorCode.SECOND_TRANSACTION,
            "the attempt already started a transaction",
        )
    if (
        admission.current_state_sha256 != current_state.snapshot_sha256
        or admission.proposal_sha256 != proposal.proposal_sha256
        or admission.candidate_set_sha256
        != authorization.candidate_set_sha256
        or admission.candidate_set_sha256 != proposal.candidate_set_sha256
        or admission.resolved_evidence_sha256
        != authorization.resolved_evidence_sha256
        or admission.resolved_evidence_sha256
        != proposal.resolved_evidence_sha256
        or admission.registry_sha256 != registry.registry_sha256
        or admission.registry_sha256 != authorization.registry_sha256
        or proposal.registry_sha256 != registry.registry_sha256
        or admission.runbook_sha256 != authorization.runbook_sha256
        or admission.runbook_sha256 != trusted_runbook_sha256
        or admission.authorization_sha256 != authorization.authorization_sha256
        or authorization.current_state_sha256 != current_state.snapshot_sha256
        or authorization.proposal_sha256 != proposal.proposal_sha256
        or authorization.runbook_id is not runbook.runbook_id
        or authorization.runbook_sha256 != proposal.runbook_sha256
        or proposal.runbook_sha256 != trusted_runbook_sha256
        or authorization.target_service != proposal.target_service
        or backend.source_snapshot_sha256 != current_state.snapshot_sha256
        or backend.run_id != proposal.run_id
        or backend.attempt_id != authorization.attempt_id
        or backend.target != proposal.target_service
        or backend.ownership_digest != current_state.ownership_digest
        or backend.state_digest() != backend.initial_state_digest
        or backend.forward_write_count != current_state.prior_forward_step_count
    ):
        raise ExecutionError(
            ExecutionErrorCode.BINDING_MISMATCH,
            "execution artifacts or fake state differ",
        )
    if len(runbook.forward_steps) > runbook.maximum_forward_steps:
        raise ExecutionError(
            ExecutionErrorCode.STEP_CAP_EXCEEDED,
            "Registry steps exceed the declared cap",
        )
    backend.start_transaction()
    executor, verifier = _runtime_for(runbook)
    backend.executor = executor
    backend.verifier = verifier
    transaction_id = f"txn:{authorization.attempt_id}"
    receipts: list[StepReceipt] = []
    for ordinal, step in enumerate(runbook.forward_steps, start=1):
        receipt = executor.execute_step(
            backend=backend,
            runbook=runbook,
            transaction_id=transaction_id,
            ordinal=ordinal,
            step_id=step.step_id,
        )
        receipts.append(receipt)
        if receipt.outcome is StepOutcome.FAILED:
            terminal = (
                ExecutionTerminal.PARTIALLY_APPLIED
                if any(
                    prior.outcome is StepOutcome.APPLIED
                    for prior in receipts[:-1]
                )
                else ExecutionTerminal.EXECUTION_FAILED
            )
            return _transaction(
                proposal=proposal,
                admission=admission,
                authorization=authorization,
                runbook=runbook,
                transaction_id=transaction_id,
                receipts=tuple(receipts),
                verification=None,
                terminal=terminal,
                final_disposition=ActionDisposition.ESCALATE_HUMAN,
            )
    checked = tuple(receipts)
    verification = verifier.verify(
        backend=backend,
        runbook=runbook,
        transaction_id=transaction_id,
        receipts=checked,
    )
    if verification.outcome is not VerificationOutcome.PASS:
        return _transaction(
            proposal=proposal,
            admission=admission,
            authorization=authorization,
            runbook=runbook,
            transaction_id=transaction_id,
            receipts=checked,
            verification=verification,
            terminal=ExecutionTerminal.VERIFICATION_FAILED,
            final_disposition=ActionDisposition.ESCALATE_HUMAN,
        )
    return _transaction(
        proposal=proposal,
        admission=admission,
        authorization=authorization,
        runbook=runbook,
        transaction_id=transaction_id,
        receipts=checked,
        verification=verification,
        terminal=ExecutionTerminal.RECOVERED,
        final_disposition=ActionDisposition.EXECUTE_RUNBOOK,
    )
