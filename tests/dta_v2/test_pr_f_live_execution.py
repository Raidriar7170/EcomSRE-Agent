from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from ecomsre.dta_v2.capture_campaign import (
    CaptureFailureOperation,
    CaptureOperationFailure,
)
from ecomsre.dta_v2.contracts import RunbookId, RunbookStepId, semantic_sha256
from ecomsre.dta_v2.live_contracts import ForwardExecutionTerminal
from ecomsre.dta_v2.live_controls import OwnedLiveControls
from ecomsre.dta_v2.live_execution import (
    LiveExecutionError,
    PartialExecutionError,
    ReceiptPersistenceError,
    execute_live_forward_steps,
)
from ecomsre.dta_v2.operational_contracts import StepOutcome, StepReceipt

from test_fake_execution import admitted_case


class RecordingReceiptJournal:
    def __init__(self) -> None:
        self.receipts: list[StepReceipt] = []

    def append(self, receipt: StepReceipt) -> None:
        self.receipts.append(receipt)


class FakeOwnedControls:
    def __init__(self, snapshot, sink: RecordingReceiptJournal) -> None:
        self.source_snapshot_sha256 = snapshot.snapshot_sha256
        self.run_id = snapshot.run_id
        self.attempt_id = snapshot.attempt_id
        self.target = snapshot.target_logical_service
        self.ownership_digest = snapshot.ownership_digest
        self.forward_write_count = snapshot.prior_forward_step_count
        self.transaction_started = False
        self.version = 0
        self.calls: list[str] = []
        self.fail_operation: str | None = None
        self.email_flag_off = False
        self.revalidation_count = 0
        self.sink = sink
        self.initial_state_digest = self.state_digest()

    def state_digest(self) -> str:
        return semantic_sha256(
            {
                "source_snapshot_sha256": self.source_snapshot_sha256,
                "run_id": self.run_id,
                "attempt_id": self.attempt_id,
                "target": self.target,
                "ownership_digest": self.ownership_digest,
                "version": self.version,
                "email_flag_off": self.email_flag_off,
            }
        )

    def revalidate_before_write(self, authorization, observed_at) -> None:
        assert authorization.run_id == self.run_id
        assert authorization.attempt_id == self.attempt_id
        assert authorization.issued_at <= observed_at < authorization.expires_at
        self.revalidation_count += 1

    def _apply(self, operation: str) -> None:
        if operation == "restart_email":
            assert len(self.sink.receipts) == 1
        self.calls.append(operation)
        self.forward_write_count += 1
        if self.fail_operation == operation:
            raise RuntimeError("untrusted secret detail")
        if operation == "disable_email_leak":
            self.email_flag_off = True
        self.version += 1

    def restore_payment_configuration(self) -> None:
        self._apply("restore_payment")

    def start_recommendation_service(self) -> None:
        self._apply("start_recommendation")

    def disable_email_leak_flag(self) -> None:
        self._apply("disable_email_leak")

    def restart_email_service(self) -> None:
        self._apply("restart_email")


def _clock():
    value = datetime(2026, 8, 16, 8, 30, tzinfo=timezone.utc)

    def now() -> datetime:
        nonlocal value
        current = value
        value += timedelta(milliseconds=1)
        return current

    return now


@pytest.mark.parametrize(
    ("runbook_id", "calls", "steps"),
    [
        (
            RunbookId.ROLLBACK_CONFIGURATION,
            ["restore_payment"],
            (RunbookStepId.RESTORE_BASELINE_CONFIGURATION,),
        ),
        (
            RunbookId.RESTART_SERVICE,
            ["start_recommendation"],
            (RunbookStepId.RESTART_OWNED_SERVICE,),
        ),
        (
            RunbookId.MITIGATE_MEMORY_LEAK,
            ["disable_email_leak", "restart_email"],
            (
                RunbookStepId.DISABLE_LEAK_FLAG,
                RunbookStepId.RESTART_OWNED_SERVICE,
            ),
        ),
    ],
)
def test_exact_registry_steps_persist_receipts_before_continuing(
    runbook_id: RunbookId,
    calls: list[str],
    steps: tuple[RunbookStepId, ...],
) -> None:
    registry, artifacts, snapshot, authorization, admission, _ = admitted_case(
        runbook_id
    )
    journal = RecordingReceiptJournal()
    controls = FakeOwnedControls(snapshot, journal)

    result = execute_live_forward_steps(
        registry=registry,
        proposal=artifacts.proposal,
        current_state=snapshot,
        admission=admission,
        authorization=authorization,
        controls=controls,
        receipt_journal=journal,
        utc_now=_clock(),
    )

    assert result.terminal is ForwardExecutionTerminal.APPLIED
    assert controls.calls == calls
    assert controls.forward_write_count == len(steps)
    assert tuple(receipt.step_id for receipt in result.receipts) == steps
    assert tuple(journal.receipts) == result.receipts
    assert all(receipt.outcome is StepOutcome.APPLIED for receipt in result.receipts)
    assert controls.revalidation_count == len(steps)


def test_email_restart_failure_preserves_flag_off_and_stops_after_two_receipts() -> None:
    registry, artifacts, snapshot, authorization, admission, _ = admitted_case(
        RunbookId.MITIGATE_MEMORY_LEAK
    )
    journal = RecordingReceiptJournal()
    controls = FakeOwnedControls(snapshot, journal)
    controls.fail_operation = "restart_email"

    result = execute_live_forward_steps(
        registry=registry,
        proposal=artifacts.proposal,
        current_state=snapshot,
        admission=admission,
        authorization=authorization,
        controls=controls,
        receipt_journal=journal,
        utc_now=_clock(),
    )

    assert result.terminal is ForwardExecutionTerminal.PARTIALLY_APPLIED
    assert result.escalation_required is True
    assert controls.calls == ["disable_email_leak", "restart_email"]
    assert controls.email_flag_off is True
    assert controls.forward_write_count == 2
    assert tuple(item.outcome for item in result.receipts) == (
        StepOutcome.APPLIED,
        StepOutcome.FAILED,
    )
    assert "untrusted secret detail" not in result.model_dump_json()
    assert len(journal.receipts) == 2


def test_unchanged_email_started_at_seals_partial_with_no_third_write() -> None:
    registry, artifacts, snapshot, authorization, admission, _ = admitted_case(
        RunbookId.MITIGATE_MEMORY_LEAK
    )
    state = {"email_flag_off": False}

    class StatefulFlags:
        def apply(self, document):
            del document
            state["email_flag_off"] = True
            return semantic_sha256(state)

    class UnchangedStartedAtEmail:
        attempts = 0

        def restart(self):
            self.attempts += 1
            raise CaptureOperationFailure(
                CaptureFailureOperation.EMAIL_RESTART_NOT_OBSERVED
            )

    email = UnchangedStartedAtEmail()
    admitted_state_digest = semantic_sha256(state)
    controls = OwnedLiveControls(
        current_state=snapshot,
        flag_controller=StatefulFlags(),
        baseline_flag_document={"flags": {"emailMemoryLeak": {"value": "off"}}},
        email_disabled_flag_document={
            "flags": {"emailMemoryLeak": {"value": "off"}}
        },
        recommendation_controller=type(
            "UnusedRecommendation", (), {"start": lambda self: None}
        )(),
        email_controller=email,
        state_digest=lambda: semantic_sha256(state),
        admitted_state_digest=admitted_state_digest,
        revalidate_before_write=lambda child, observed_at: None,
    )
    journal = RecordingReceiptJournal()

    result = execute_live_forward_steps(
        registry=registry,
        proposal=artifacts.proposal,
        current_state=snapshot,
        admission=admission,
        authorization=authorization,
        controls=controls,
        receipt_journal=journal,
        utc_now=_clock(),
    )

    assert result.terminal is ForwardExecutionTerminal.PARTIALLY_APPLIED
    assert result.escalation_required is True
    assert state["email_flag_off"] is True
    assert controls.email_restart_mutation_proof is None
    assert controls.forward_write_count == 2
    assert email.attempts == 1
    assert len(result.receipts) == len(journal.receipts) == 2
    assert tuple(item.outcome for item in result.receipts) == (
        StepOutcome.APPLIED,
        StepOutcome.FAILED,
    )


def test_live_executor_rejects_binding_drift_before_any_write() -> None:
    registry, artifacts, snapshot, authorization, admission, _ = admitted_case(
        RunbookId.RESTART_SERVICE
    )
    journal = RecordingReceiptJournal()
    controls = FakeOwnedControls(snapshot, journal)
    controls.ownership_digest = "f" * 64

    with pytest.raises(LiveExecutionError, match="state|binding"):
        execute_live_forward_steps(
            registry=registry,
            proposal=artifacts.proposal,
            current_state=snapshot,
            admission=admission,
            authorization=authorization,
            controls=controls,
            receipt_journal=journal,
            utc_now=_clock(),
        )
    assert controls.calls == []
    assert journal.receipts == []


def test_live_executor_rejects_post_snapshot_state_drift_before_first_write() -> None:
    registry, artifacts, snapshot, authorization, admission, _ = admitted_case(
        RunbookId.RESTART_SERVICE
    )
    journal = RecordingReceiptJournal()
    controls = FakeOwnedControls(snapshot, journal)
    controls.initial_state_digest = controls.state_digest()
    controls.version += 1

    with pytest.raises(LiveExecutionError, match="state"):
        execute_live_forward_steps(
            registry=registry,
            proposal=artifacts.proposal,
            current_state=snapshot,
            admission=admission,
            authorization=authorization,
            controls=controls,
            receipt_journal=journal,
            utc_now=_clock(),
        )
    assert controls.calls == []
    assert journal.receipts == []


def test_live_executor_stops_before_second_write_on_interstep_state_drift() -> None:
    registry, artifacts, snapshot, authorization, admission, _ = admitted_case(
        RunbookId.MITIGATE_MEMORY_LEAK
    )
    controls: FakeOwnedControls

    class DriftingJournal(RecordingReceiptJournal):
        def append(self, receipt: StepReceipt) -> None:
            super().append(receipt)
            if receipt.step_ordinal == 1:
                controls.version += 1

    journal = DriftingJournal()
    controls = FakeOwnedControls(snapshot, journal)
    controls.initial_state_digest = controls.state_digest()

    with pytest.raises(PartialExecutionError) as captured:
        execute_live_forward_steps(
            registry=registry,
            proposal=artifacts.proposal,
            current_state=snapshot,
            admission=admission,
            authorization=authorization,
            controls=controls,
            receipt_journal=journal,
            utc_now=_clock(),
        )
    assert controls.calls == ["disable_email_leak"]
    assert len(journal.receipts) == 1
    assert (
        captured.value.forward_execution.terminal
        is ForwardExecutionTerminal.PARTIALLY_APPLIED
    )
    assert captured.value.forward_execution.receipts == tuple(journal.receipts)


def test_email_expiry_before_second_write_retains_applied_prefix() -> None:
    registry, artifacts, snapshot, authorization, admission, _ = admitted_case(
        RunbookId.MITIGATE_MEMORY_LEAK
    )
    journal = RecordingReceiptJournal()
    controls = FakeOwnedControls(snapshot, journal)
    ticks = iter(
        (
            authorization.issued_at + timedelta(minutes=1),
            authorization.issued_at + timedelta(minutes=2),
            authorization.issued_at + timedelta(minutes=3),
            authorization.issued_at + timedelta(minutes=4),
            authorization.issued_at + timedelta(minutes=5),
            authorization.issued_at + timedelta(minutes=6),
            authorization.expires_at,
        )
    )

    with pytest.raises(PartialExecutionError) as captured:
        execute_live_forward_steps(
            registry=registry,
            proposal=artifacts.proposal,
            current_state=snapshot,
            admission=admission,
            authorization=authorization,
            controls=controls,
            receipt_journal=journal,
            utc_now=lambda: next(ticks),
        )

    assert controls.calls == ["disable_email_leak"]
    assert controls.email_flag_off is True
    assert controls.forward_write_count == 1
    assert captured.value.forward_execution.receipts == tuple(journal.receipts)
    assert (
        captured.value.forward_execution.terminal
        is ForwardExecutionTerminal.PARTIALLY_APPLIED
    )


def test_operation_start_equal_to_expiry_makes_zero_writes() -> None:
    registry, artifacts, snapshot, authorization, admission, _ = admitted_case(
        RunbookId.RESTART_SERVICE
    )
    journal = RecordingReceiptJournal()
    controls = FakeOwnedControls(snapshot, journal)
    ticks = iter(
        (
            authorization.issued_at + timedelta(minutes=1),
            authorization.issued_at + timedelta(minutes=2),
            authorization.issued_at + timedelta(minutes=3),
            authorization.expires_at,
        )
    )

    with pytest.raises(LiveExecutionError, match="expired"):
        execute_live_forward_steps(
            registry=registry,
            proposal=artifacts.proposal,
            current_state=snapshot,
            admission=admission,
            authorization=authorization,
            controls=controls,
            receipt_journal=journal,
            utc_now=lambda: next(ticks),
        )

    assert controls.calls == []
    assert controls.forward_write_count == 0
    assert journal.receipts == []


def test_email_second_operation_start_equal_to_expiry_is_typed_partial() -> None:
    registry, artifacts, snapshot, authorization, admission, _ = admitted_case(
        RunbookId.MITIGATE_MEMORY_LEAK
    )
    journal = RecordingReceiptJournal()
    controls = FakeOwnedControls(snapshot, journal)
    ticks = iter(
        (
            authorization.issued_at + timedelta(minutes=1),
            authorization.issued_at + timedelta(minutes=2),
            authorization.issued_at + timedelta(minutes=3),
            authorization.issued_at + timedelta(minutes=4),
            authorization.issued_at + timedelta(minutes=5),
            authorization.issued_at + timedelta(minutes=6),
            authorization.issued_at + timedelta(minutes=7),
            authorization.expires_at,
        )
    )

    with pytest.raises(PartialExecutionError) as captured:
        execute_live_forward_steps(
            registry=registry,
            proposal=artifacts.proposal,
            current_state=snapshot,
            admission=admission,
            authorization=authorization,
            controls=controls,
            receipt_journal=journal,
            utc_now=lambda: next(ticks),
        )

    assert controls.calls == ["disable_email_leak"]
    assert controls.forward_write_count == 1
    assert controls.email_flag_off is True
    assert captured.value.forward_execution.receipts == tuple(journal.receipts)
    assert (
        captured.value.forward_execution.terminal
        is ForwardExecutionTerminal.PARTIALLY_APPLIED
    )


def test_live_executor_rejects_state_drift_in_first_write_revalidation_callback(
) -> None:
    registry, artifacts, snapshot, authorization, admission, _ = admitted_case(
        RunbookId.RESTART_SERVICE
    )
    journal = RecordingReceiptJournal()

    class CallbackDriftControls(FakeOwnedControls):
        def revalidate_before_write(self, authorization, observed_at) -> None:
            super().revalidate_before_write(authorization, observed_at)
            self.version += 1

    controls = CallbackDriftControls(snapshot, journal)

    with pytest.raises(LiveExecutionError, match="state"):
        execute_live_forward_steps(
            registry=registry,
            proposal=artifacts.proposal,
            current_state=snapshot,
            admission=admission,
            authorization=authorization,
            controls=controls,
            receipt_journal=journal,
            utc_now=_clock(),
        )

    assert controls.revalidation_count == 1
    assert controls.calls == []
    assert journal.receipts == []


def test_failed_step_with_observed_state_drift_still_persists_a_receipt() -> None:
    registry, artifacts, snapshot, authorization, admission, _ = admitted_case(
        RunbookId.MITIGATE_MEMORY_LEAK
    )
    journal = RecordingReceiptJournal()

    class ChangedThenFailedControls(FakeOwnedControls):
        def restart_email_service(self) -> None:
            self.calls.append("restart_email")
            self.forward_write_count += 1
            self.version += 1
            raise RuntimeError("untrusted operation detail")

    controls = ChangedThenFailedControls(snapshot, journal)
    controls.initial_state_digest = controls.state_digest()

    result = execute_live_forward_steps(
        registry=registry,
        proposal=artifacts.proposal,
        current_state=snapshot,
        admission=admission,
        authorization=authorization,
        controls=controls,
        receipt_journal=journal,
        utc_now=_clock(),
    )

    assert result.terminal is ForwardExecutionTerminal.PARTIALLY_APPLIED
    assert tuple(item.outcome for item in result.receipts) == (
        StepOutcome.APPLIED,
        StepOutcome.FAILED,
    )
    assert len(journal.receipts) == 2


def test_live_executor_rechecks_child_expiry_before_first_write() -> None:
    registry, artifacts, snapshot, authorization, admission, _ = admitted_case(
        RunbookId.RESTART_SERVICE
    )
    journal = RecordingReceiptJournal()
    controls = FakeOwnedControls(snapshot, journal)
    controls.initial_state_digest = controls.state_digest()

    with pytest.raises(LiveExecutionError, match="expired"):
        execute_live_forward_steps(
            registry=registry,
            proposal=artifacts.proposal,
            current_state=snapshot,
            admission=admission,
            authorization=authorization,
            controls=controls,
            receipt_journal=journal,
            utc_now=lambda: authorization.expires_at,
        )
    assert controls.calls == []
    assert journal.receipts == []


def test_receipt_persistence_failure_stops_before_next_email_write() -> None:
    registry, artifacts, snapshot, authorization, admission, _ = admitted_case(
        RunbookId.MITIGATE_MEMORY_LEAK
    )

    class FailingJournal(RecordingReceiptJournal):
        def append(self, receipt: StepReceipt) -> None:
            self.receipts.append(receipt)
            raise OSError("private filesystem detail")

    journal = FailingJournal()
    controls = FakeOwnedControls(snapshot, journal)

    with pytest.raises(ReceiptPersistenceError) as captured:
        execute_live_forward_steps(
            registry=registry,
            proposal=artifacts.proposal,
            current_state=snapshot,
            admission=admission,
            authorization=authorization,
            controls=controls,
            receipt_journal=journal,
            utc_now=_clock(),
        )

    assert controls.calls == ["disable_email_leak"]
    assert controls.forward_write_count == 1
    assert len(journal.receipts) == 1
    assert captured.value.forward_execution.receipts == tuple(journal.receipts)
    assert (
        captured.value.forward_execution.terminal
        is ForwardExecutionTerminal.EVIDENCE_PERSISTENCE_FAILED
    )
