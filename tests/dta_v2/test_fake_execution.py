from __future__ import annotations

from datetime import timedelta

import pytest

from ecomsre.dta_v2.authorization import derive_attempt_authorization
from ecomsre.dta_v2.contracts import (
    ActionDisposition,
    RunbookId,
    RunbookStepId,
    semantic_sha256,
)
from ecomsre.dta_v2.fake_runtime import (
    ExecutionError,
    ExecutionErrorCode,
    FakeBackend,
    FakeDockerServiceRestartExecutor,
    FakeFeatureFlagRollbackExecutor,
    FakeMemoryLeakMitigationExecutor,
    execute_fake_transaction,
    validate_step_receipt,
)
from ecomsre.dta_v2.operational_contracts import (
    ExecutionTerminal,
    ServiceRuntimeState,
    StepOutcome,
    VerificationOutcome,
)
from ecomsre.dta_v2.policy import evaluate_operational_admission
from ecomsre.dta_v2.registry import load_runbook_registry

from test_admission_policy import (
    NOW,
    RUNBOOK_ROOT,
    _CASE,
    case_artifacts,
    current_state,
    master_authorization,
    rehash,
)


def rehash_fields(model, *, updates: dict[str, object], digest_field: str):
    payload = {name: getattr(model, name) for name in type(model).model_fields}
    payload.update(updates)
    draft = type(model).model_construct(**payload)
    payload[digest_field] = semantic_sha256(
        draft.model_dump(mode="json", exclude={digest_field})
    )
    return type(model).model_validate(payload)


def admitted_case(runbook_id: RunbookId, *, fail_steps=(), verifier_failure=False):
    registry = load_runbook_registry(RUNBOOK_ROOT)
    artifacts = case_artifacts(registry, runbook_id)
    snapshot = current_state(registry, runbook_id)
    master = master_authorization(registry)
    scenario_id = _CASE[runbook_id][-1]
    authorization = derive_attempt_authorization(
        master=master,
        scenario_id=scenario_id,
        registry=registry,
        candidate_set=artifacts.candidates,
        diagnosis=artifacts.diagnosis,
        diagnosis_evidence=artifacts.evidence,
        proposal=artifacts.proposal,
        current_state=snapshot,
        issued_at=NOW + timedelta(seconds=10),
        expires_at=NOW + timedelta(hours=1),
    )
    admission = evaluate_operational_admission(
        registry=registry,
        candidate_set=artifacts.candidates,
        diagnosis=artifacts.diagnosis,
        diagnosis_evidence=artifacts.evidence,
        proposal=artifacts.proposal,
        current_state=snapshot,
        master_authorization=master,
        attempt_authorization=authorization,
        as_of=NOW + timedelta(minutes=1),
    )
    backend = FakeBackend.from_snapshot(
        snapshot,
        fail_steps=frozenset(fail_steps),
        force_verifier_failure=verifier_failure,
    )
    return registry, artifacts, snapshot, authorization, admission, backend


@pytest.mark.parametrize(
    ("runbook_id", "expected_executor", "expected_steps"),
    [
        (
            RunbookId.ROLLBACK_CONFIGURATION,
            FakeFeatureFlagRollbackExecutor,
            (RunbookStepId.RESTORE_BASELINE_CONFIGURATION,),
        ),
        (
            RunbookId.RESTART_SERVICE,
            FakeDockerServiceRestartExecutor,
            (RunbookStepId.RESTART_OWNED_SERVICE,),
        ),
        (
            RunbookId.MITIGATE_MEMORY_LEAK,
            FakeMemoryLeakMitigationExecutor,
            (
                RunbookStepId.DISABLE_LEAK_FLAG,
                RunbookStepId.RESTART_OWNED_SERVICE,
            ),
        ),
    ],
)
def test_fake_executor_and_verifier_succeed_for_each_runbook(
    runbook_id: RunbookId,
    expected_executor: type,
    expected_steps: tuple[RunbookStepId, ...],
) -> None:
    registry, artifacts, snapshot, authorization, admission, backend = admitted_case(
        runbook_id
    )
    transaction = execute_fake_transaction(
        registry=registry,
        proposal=artifacts.proposal,
        current_state=snapshot,
        admission=admission,
        authorization=authorization,
        backend=backend,
    )

    assert isinstance(backend.executor, expected_executor)
    assert tuple(receipt.step_id for receipt in transaction.receipts) == expected_steps
    assert all(receipt.outcome is StepOutcome.APPLIED for receipt in transaction.receipts)
    assert transaction.terminal is ExecutionTerminal.RECOVERED
    assert transaction.verification is not None
    assert transaction.verification.outcome is VerificationOutcome.PASS
    assert backend.forward_write_count == len(expected_steps)


@pytest.mark.parametrize("runbook_id", tuple(RunbookId))
def test_receipt_hash_drift_is_rejected(runbook_id: RunbookId) -> None:
    registry, artifacts, snapshot, authorization, admission, backend = admitted_case(
        runbook_id
    )
    transaction = execute_fake_transaction(
        registry=registry,
        proposal=artifacts.proposal,
        current_state=snapshot,
        admission=admission,
        authorization=authorization,
        backend=backend,
    )
    forged = transaction.receipts[0].model_copy(
        update={"after_state_digest": "f" * 64}
    )

    with pytest.raises(ValueError, match="receipt digest"):
        validate_step_receipt(forged)


@pytest.mark.parametrize("runbook_id", tuple(RunbookId))
def test_verifier_failure_escalates_without_another_forward_write(
    runbook_id: RunbookId,
) -> None:
    registry, artifacts, snapshot, authorization, admission, backend = admitted_case(
        runbook_id,
        verifier_failure=True,
    )
    transaction = execute_fake_transaction(
        registry=registry,
        proposal=artifacts.proposal,
        current_state=snapshot,
        admission=admission,
        authorization=authorization,
        backend=backend,
    )

    assert transaction.terminal is ExecutionTerminal.VERIFICATION_FAILED
    assert transaction.final_disposition is ActionDisposition.ESCALATE_HUMAN
    assert transaction.verification is not None
    assert transaction.verification.outcome is VerificationOutcome.FAIL
    assert backend.forward_write_count == len(
        registry.require(runbook_id).forward_steps
    )


def test_executor_rejects_forged_admission_digest_bindings() -> None:
    registry, artifacts, snapshot, authorization, admission, backend = admitted_case(
        RunbookId.RESTART_SERVICE
    )
    forged = rehash(
        admission,
        field="registry_sha256",
        value="f" * 64,
        digest_field="admission_sha256",
    )

    with pytest.raises(ExecutionError) as error:
        execute_fake_transaction(
            registry=registry,
            proposal=artifacts.proposal,
            current_state=snapshot,
            admission=forged,
            authorization=authorization,
            backend=backend,
        )
    assert error.value.code is ExecutionErrorCode.BINDING_MISMATCH
    assert backend.forward_write_count == 0


def test_executor_rejects_correlated_digest_forgery() -> None:
    registry, artifacts, snapshot, authorization, admission, backend = admitted_case(
        RunbookId.MITIGATE_MEMORY_LEAK
    )
    forged_sha = "f" * 64
    forged_proposal = rehash_fields(
        artifacts.proposal,
        updates={
            "candidate_set_sha256": forged_sha,
            "resolved_evidence_sha256": forged_sha,
            "registry_sha256": forged_sha,
            "runbook_sha256": forged_sha,
        },
        digest_field="proposal_sha256",
    )
    forged_authorization = rehash_fields(
        authorization,
        updates={
            "proposal_sha256": forged_proposal.proposal_sha256,
            "candidate_set_sha256": forged_sha,
            "resolved_evidence_sha256": forged_sha,
            "runbook_sha256": forged_sha,
        },
        digest_field="authorization_sha256",
    )
    forged_admission = rehash_fields(
        admission,
        updates={
            "proposal_sha256": forged_proposal.proposal_sha256,
            "candidate_set_sha256": forged_sha,
            "resolved_evidence_sha256": forged_sha,
            "runbook_sha256": forged_sha,
            "authorization_sha256": forged_authorization.authorization_sha256,
        },
        digest_field="admission_sha256",
    )

    with pytest.raises(ExecutionError) as error:
        execute_fake_transaction(
            registry=registry,
            proposal=forged_proposal,
            current_state=snapshot,
            admission=forged_admission,
            authorization=forged_authorization,
            backend=backend,
        )
    assert error.value.code is ExecutionErrorCode.BINDING_MISMATCH
    assert backend.forward_write_count == 0


def test_executor_rejects_post_admission_state_drift_before_write() -> None:
    registry, artifacts, snapshot, authorization, admission, backend = admitted_case(
        RunbookId.RESTART_SERVICE
    )
    backend.service_runtime_state = ServiceRuntimeState.RUNNING_HEALTHY

    with pytest.raises(ExecutionError) as error:
        execute_fake_transaction(
            registry=registry,
            proposal=artifacts.proposal,
            current_state=snapshot,
            admission=admission,
            authorization=authorization,
            backend=backend,
        )
    assert error.value.code is ExecutionErrorCode.BINDING_MISMATCH
    assert backend.forward_write_count == 0


def test_email_restart_failure_preserves_leak_off_and_stops_after_two_writes() -> None:
    registry, artifacts, snapshot, authorization, admission, backend = admitted_case(
        RunbookId.MITIGATE_MEMORY_LEAK,
        fail_steps=(RunbookStepId.RESTART_OWNED_SERVICE,),
    )
    transaction = execute_fake_transaction(
        registry=registry,
        proposal=artifacts.proposal,
        current_state=snapshot,
        admission=admission,
        authorization=authorization,
        backend=backend,
    )

    assert transaction.terminal is ExecutionTerminal.PARTIALLY_APPLIED
    assert transaction.final_disposition is ActionDisposition.ESCALATE_HUMAN
    assert tuple(receipt.outcome for receipt in transaction.receipts) == (
        StepOutcome.APPLIED,
        StepOutcome.FAILED,
    )
    assert backend.leak_flag_active is False
    assert backend.forward_write_count == 2
    assert transaction.verification is None

    with pytest.raises(ExecutionError) as error:
        execute_fake_transaction(
            registry=registry,
            proposal=artifacts.proposal,
            current_state=snapshot,
            admission=admission,
            authorization=authorization,
            backend=backend,
        )
    assert error.value.code is ExecutionErrorCode.SECOND_TRANSACTION
    assert backend.forward_write_count == 2


def test_transaction_contract_rejects_false_partial_apply_and_failed_state_drift() -> None:
    registry, artifacts, snapshot, authorization, admission, backend = admitted_case(
        RunbookId.MITIGATE_MEMORY_LEAK
    )
    transaction = execute_fake_transaction(
        registry=registry,
        proposal=artifacts.proposal,
        current_state=snapshot,
        admission=admission,
        authorization=authorization,
        backend=backend,
    )
    transaction_payload = {
        name: getattr(transaction, name)
        for name in type(transaction).model_fields
    }
    transaction_payload["terminal"] = ExecutionTerminal.PARTIALLY_APPLIED
    transaction_payload["final_disposition"] = ActionDisposition.ESCALATE_HUMAN
    transaction_payload["verification"] = None
    draft = type(transaction).model_construct(**transaction_payload)
    transaction_payload["transaction_sha256"] = semantic_sha256(
        draft.model_dump(mode="json", exclude={"transaction_sha256"})
    )
    with pytest.raises(ValueError, match="partial transaction"):
        type(transaction).model_validate(transaction_payload)

    receipt = transaction.receipts[0]
    receipt_payload = {
        name: getattr(receipt, name)
        for name in type(receipt).model_fields
    }
    receipt_payload["outcome"] = StepOutcome.FAILED
    receipt_payload["error_code"] = "FAKE_FAILURE"
    receipt_draft = type(receipt).model_construct(**receipt_payload)
    receipt_payload["receipt_sha256"] = semantic_sha256(
        receipt_draft.model_dump(mode="json", exclude={"receipt_sha256"})
    )
    failed_with_drift = type(receipt).model_validate(receipt_payload)
    assert failed_with_drift.outcome is StepOutcome.FAILED
    assert (
        failed_with_drift.before_state_digest
        != failed_with_drift.after_state_digest
    )


def test_transaction_cross_binds_verification_identity_and_step_continuity() -> None:
    registry, artifacts, snapshot, authorization, admission, backend = admitted_case(
        RunbookId.MITIGATE_MEMORY_LEAK
    )
    transaction = execute_fake_transaction(
        registry=registry,
        proposal=artifacts.proposal,
        current_state=snapshot,
        admission=admission,
        authorization=authorization,
        backend=backend,
    )
    assert transaction.verification is not None

    wrong_run = rehash_fields(
        transaction.verification,
        updates={"run_id": "b" * 32},
        digest_field="verification_sha256",
    )
    with pytest.raises(ValueError, match="verification identity"):
        rehash_fields(
            transaction,
            updates={"verification": wrong_run},
            digest_field="transaction_sha256",
        )

    first, second = transaction.receipts
    discontinuous_second = rehash_fields(
        second,
        updates={"before_state_digest": "f" * 64},
        digest_field="receipt_sha256",
    )
    receipts = (first, discontinuous_second)
    rebound_verification = rehash_fields(
        transaction.verification,
        updates={
            "receipt_sha256s": tuple(item.receipt_sha256 for item in receipts),
        },
        digest_field="verification_sha256",
    )
    with pytest.raises(ValueError, match="state continuity"):
        rehash_fields(
            transaction,
            updates={"receipts": receipts, "verification": rebound_verification},
            digest_field="transaction_sha256",
        )
