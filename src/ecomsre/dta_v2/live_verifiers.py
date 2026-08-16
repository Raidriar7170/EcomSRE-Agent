"""Runbook-specific verification over two bounded recovery windows."""

from __future__ import annotations

import math

from ecomsre.dta_v2.contracts import ActionDisposition, RunbookId, semantic_sha256
from ecomsre.dta_v2.live_contracts import (
    ForwardExecution,
    ForwardExecutionTerminal,
    RecoveryWindow,
)
from ecomsre.dta_v2.operational_contracts import (
    ExecutionTerminal,
    ExecutionTransaction,
    VerificationOutcome,
    VerificationResult,
)
from ecomsre.dta_v2.registry import RunbookRegistry


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


def _build_transaction(
    *,
    forward: ForwardExecution,
    verification: VerificationResult | None,
    terminal: ExecutionTerminal,
    disposition: ActionDisposition,
) -> ExecutionTransaction:
    payload: dict[str, object] = {
        "schema_version": "dta-v2.execution-transaction.v1",
        "run_id": forward.run_id,
        "attempt_id": forward.attempt_id,
        "transaction_id": forward.transaction_id,
        "runbook_id": forward.runbook_id,
        "target": forward.target,
        "proposal_sha256": forward.proposal_sha256,
        "admission_sha256": forward.admission_sha256,
        "authorization_sha256": forward.authorization_sha256,
        "maximum_forward_steps": forward.maximum_forward_steps,
        "forward_step_count": forward.forward_step_count,
        "receipts": forward.receipts,
        "verification": verification,
        "terminal": terminal,
        "final_disposition": disposition,
    }
    return ExecutionTransaction.model_validate(
        _with_digest(ExecutionTransaction, payload, "transaction_sha256")
    )


def finalize_live_execution(
    *,
    registry: RunbookRegistry,
    forward_execution: ForwardExecution,
    recovery_windows: tuple[RecoveryWindow, ...],
    email_leak_flag_off: bool | None,
    maximum_email_memory_slope_bytes_per_second: float,
) -> ExecutionTransaction:
    """Seal a failed forward result or verify an applied result exactly once."""

    registry = RunbookRegistry.model_validate(registry.model_dump(mode="python"))
    forward = ForwardExecution.model_validate(
        forward_execution.model_dump(mode="python")
    )
    runbook = registry.require(forward.runbook_id)
    if runbook.target_services != (forward.target,):
        raise ValueError("Verifier target differs from the trusted Registry")
    if forward.maximum_forward_steps != runbook.maximum_forward_steps:
        raise ValueError("Verifier step cap differs from the trusted Registry")
    expected_verifier = {
        RunbookId.ROLLBACK_CONFIGURATION: "ConfigurationRecoveryVerifier",
        RunbookId.RESTART_SERVICE: "ServiceRecoveryVerifier",
        RunbookId.MITIGATE_MEMORY_LEAK: "MemoryLeakRecoveryVerifier",
    }[forward.runbook_id]
    if runbook.verifier_id != expected_verifier:
        raise ValueError("Registry Verifier identity differs")

    if forward.terminal is not ForwardExecutionTerminal.APPLIED:
        if recovery_windows:
            raise ValueError("failed forward execution cannot claim recovery windows")
        terminal = (
            ExecutionTerminal.PARTIALLY_APPLIED
            if forward.terminal is ForwardExecutionTerminal.PARTIALLY_APPLIED
            else ExecutionTerminal.EXECUTION_FAILED
        )
        return _build_transaction(
            forward=forward,
            verification=None,
            terminal=terminal,
            disposition=ActionDisposition.ESCALATE_HUMAN,
        )

    if len(recovery_windows) != 2:
        raise ValueError("applied execution requires exactly two recovery windows")
    windows = tuple(
        RecoveryWindow.model_validate(item.model_dump(mode="python"))
        for item in recovery_windows
    )
    if tuple(item.ordinal for item in windows) != (1, 2):
        raise ValueError("recovery windows are not canonically ordered")
    if windows[1].started_at < windows[0].ended_at:
        raise ValueError("recovery windows overlap")
    if (
        not isinstance(maximum_email_memory_slope_bytes_per_second, float)
        or not math.isfinite(maximum_email_memory_slope_bytes_per_second)
        or maximum_email_memory_slope_bytes_per_second < 0
    ):
        raise ValueError("Email recovery memory threshold is invalid")

    reasons: list[str] = []
    if not all(item.infrastructure_passed for item in windows):
        reasons.append("INFRASTRUCTURE_NOT_RECOVERED")
    if not all(item.endpoint_passed for item in windows):
        reasons.append("ENDPOINT_NOT_RECOVERED")
    if not all(item.business_sli_passed for item in windows):
        reasons.append("BUSINESS_SLI_NOT_RECOVERED")
    if forward.runbook_id is RunbookId.ROLLBACK_CONFIGURATION:
        if email_leak_flag_off is not None:
            raise ValueError("non-Email verifier received Email flag state")
        if not all(item.configuration_restored for item in windows):
            reasons.append("CONFIGURATION_NOT_RESTORED")
    elif forward.runbook_id is RunbookId.RESTART_SERVICE:
        if email_leak_flag_off is not None:
            raise ValueError("non-Email verifier received Email flag state")
    else:
        if email_leak_flag_off is not True:
            reasons.append("LEAK_FLAG_STILL_ACTIVE")
        slopes = tuple(item.memory_slope_bytes_per_second for item in windows)
        if any(
            slope is None
            or slope > maximum_email_memory_slope_bytes_per_second
            for slope in slopes
        ):
            reasons.append("MEMORY_SLOPE_NOT_RECOVERED")

    passed = not reasons
    verification_payload: dict[str, object] = {
        "schema_version": "dta-v2.verification-result.v1",
        "run_id": forward.run_id,
        "attempt_id": forward.attempt_id,
        "transaction_id": forward.transaction_id,
        "runbook_id": forward.runbook_id,
        "verifier_id": runbook.verifier_id,
        "outcome": VerificationOutcome.PASS if passed else VerificationOutcome.FAIL,
        "infrastructure_passed": (
            all(item.infrastructure_passed and item.endpoint_passed for item in windows)
        ),
        "business_sli_passed": all(item.business_sli_passed for item in windows),
        "receipt_sha256s": tuple(item.receipt_sha256 for item in forward.receipts),
        "reason_codes": ("VERIFIED",) if passed else tuple(reasons),
    }
    verification = VerificationResult.model_validate(
        _with_digest(
            VerificationResult,
            verification_payload,
            "verification_sha256",
        )
    )
    return _build_transaction(
        forward=forward,
        verification=verification,
        terminal=(
            ExecutionTerminal.RECOVERED
            if passed
            else ExecutionTerminal.VERIFICATION_FAILED
        ),
        disposition=(
            ActionDisposition.EXECUTE_RUNBOOK
            if passed
            else ActionDisposition.ESCALATE_HUMAN
        ),
    )
