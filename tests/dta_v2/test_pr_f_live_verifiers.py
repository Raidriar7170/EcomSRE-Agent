from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from ecomsre.dta_v2.contracts import ActionDisposition, RunbookId
from ecomsre.dta_v2.live_contracts import build_recovery_window
from ecomsre.dta_v2.live_execution import execute_live_forward_steps
from ecomsre.dta_v2.live_verifiers import finalize_live_execution
from ecomsre.dta_v2.operational_contracts import (
    ExecutionTerminal,
    VerificationOutcome,
)

from test_fake_execution import admitted_case
from test_pr_f_live_execution import (
    FakeOwnedControls,
    RecordingReceiptJournal,
    _clock,
)


def _forward(runbook_id: RunbookId, *, fail: str | None = None):
    registry, artifacts, snapshot, authorization, admission, _ = admitted_case(
        runbook_id
    )
    journal = RecordingReceiptJournal()
    controls = FakeOwnedControls(snapshot, journal)
    controls.fail_operation = fail
    forward = execute_live_forward_steps(
        registry=registry,
        proposal=artifacts.proposal,
        current_state=snapshot,
        admission=admission,
        authorization=authorization,
        controls=controls,
        receipt_journal=journal,
        utc_now=_clock(),
    )
    return registry, forward


def _windows(*, slope: float | None = None, passed: bool = True):
    start = datetime(2026, 8, 16, 10, 0, tzinfo=timezone.utc)
    return tuple(
        build_recovery_window(
            ordinal=ordinal,
            started_at=start + timedelta(minutes=ordinal - 1),
            ended_at=start + timedelta(minutes=ordinal),
            infrastructure_passed=passed,
            business_sli_passed=passed,
            endpoint_passed=passed,
            configuration_restored=passed,
            memory_slope_bytes_per_second=slope,
        )
        for ordinal in (1, 2)
    )


@pytest.mark.parametrize("runbook_id", tuple(RunbookId))
def test_two_exact_recovery_windows_finalize_each_runbook(
    runbook_id: RunbookId,
) -> None:
    registry, forward = _forward(runbook_id)
    transaction = finalize_live_execution(
        registry=registry,
        forward_execution=forward,
        recovery_windows=_windows(
            slope=0.0 if runbook_id is RunbookId.MITIGATE_MEMORY_LEAK else None
        ),
        email_leak_flag_off=(
            True if runbook_id is RunbookId.MITIGATE_MEMORY_LEAK else None
        ),
        maximum_email_memory_slope_bytes_per_second=100_000.0,
    )

    assert transaction.terminal is ExecutionTerminal.RECOVERED
    assert transaction.final_disposition is ActionDisposition.EXECUTE_RUNBOOK
    assert transaction.verification is not None
    assert transaction.verification.outcome is VerificationOutcome.PASS
    assert transaction.verification.verifier_id == registry.require(
        runbook_id
    ).verifier_id


def test_email_memory_or_window_failure_escalates_without_more_writes() -> None:
    registry, forward = _forward(RunbookId.MITIGATE_MEMORY_LEAK)
    transaction = finalize_live_execution(
        registry=registry,
        forward_execution=forward,
        recovery_windows=_windows(slope=100_000.1),
        email_leak_flag_off=True,
        maximum_email_memory_slope_bytes_per_second=100_000.0,
    )

    assert transaction.forward_step_count == 2
    assert transaction.terminal is ExecutionTerminal.VERIFICATION_FAILED
    assert transaction.final_disposition is ActionDisposition.ESCALATE_HUMAN
    assert transaction.verification is not None
    assert transaction.verification.outcome is VerificationOutcome.FAIL
    assert transaction.verification.reason_codes == ("MEMORY_SLOPE_NOT_RECOVERED",)


def test_partial_email_is_sealed_without_verification_or_compensation() -> None:
    registry, forward = _forward(
        RunbookId.MITIGATE_MEMORY_LEAK,
        fail="restart_email",
    )
    transaction = finalize_live_execution(
        registry=registry,
        forward_execution=forward,
        recovery_windows=(),
        email_leak_flag_off=True,
        maximum_email_memory_slope_bytes_per_second=100_000.0,
    )

    assert transaction.terminal is ExecutionTerminal.PARTIALLY_APPLIED
    assert transaction.verification is None
    assert transaction.forward_step_count == 2
    assert transaction.final_disposition is ActionDisposition.ESCALATE_HUMAN


def test_applied_execution_requires_two_canonical_nonoverlapping_windows() -> None:
    registry, forward = _forward(RunbookId.RESTART_SERVICE)
    with pytest.raises(ValueError, match="exactly two"):
        finalize_live_execution(
            registry=registry,
            forward_execution=forward,
            recovery_windows=_windows()[:1],
            email_leak_flag_off=None,
            maximum_email_memory_slope_bytes_per_second=100_000.0,
        )
