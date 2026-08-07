from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from ecomsre_rcaeval_v2.contracts import (
    DiagnosisV2,
    OperationFailureCode,
    OperationStatus,
    OperationType,
    ProviderUsageDelta,
    SpecialistAssessmentV2,
    SpecialistOperationRecord,
    TerminalDispositionV2,
)
from ecomsre_rcaeval_v2.observability import RunJournalV2, execute_run_once


NOW = datetime(2026, 8, 7, tzinfo=timezone.utc)


def _diagnosis() -> DiagnosisV2:
    return DiagnosisV2(
        root_cause_service="cartservice",
        model_proposed_indicator="mem",
        resolved_indicator="mem",
        indicator_disposition="RESOLVED",
        judge_evidence_refs=("metric:0001",),
        indicator_evidence_ref="indicator:0001",
        confidence=0.9,
        explanation="The bounded evidence and deterministic resolver agree.",
    )


def _execute(run_root: Path, callback):
    return execute_run_once(
        run_root,
        run_id="1" * 32,
        case_id="re2-ss-case-0001",
        system="RE2-SS",
        architecture="single_v2",
        started_at_utc=NOW,
        callback=callback,
    )


def _completed_operation() -> SpecialistOperationRecord:
    return SpecialistOperationRecord(
        schema_version="rcaeval-re2-v2.operation-record.v1",
        run_id="1" * 32,
        case_id="re2-ss-case-0001",
        system="RE2-SS",
        architecture="single_v2",
        operation_index=1,
        operation_type=OperationType.METRICS_SPECIALIST,
        source="metrics",
        started_at_utc=NOW,
        ended_at_utc=NOW,
        latency_ms=0.0,
        status=OperationStatus.COMPLETED,
        failure_code=None,
        provider_call_index=1,
        input_snapshot_sha256="a" * 64,
        output_snapshot_sha256="b" * 64,
        usage_delta=ProviderUsageDelta(
            model_calls_delta=1,
            prompt_tokens_delta=10,
            completion_tokens_delta=5,
            total_tokens_delta=15,
        ),
        investigated_sources=("metrics",),
        evidence_refs_visible_to_operation=("metric:0001",),
        selected_sources=(),
        typed_output=SpecialistAssessmentV2(
            source="metrics",
            candidate_service="cartservice",
            candidate_indicator="mem",
            confidence=0.9,
            supporting_evidence_refs=("metric:0001",),
            contradicting_evidence_refs=(),
            summary="Metrics support cartservice memory pressure.",
        ),
    )


def test_existing_terminal_is_read_only_and_second_execution_skips_callback(
    tmp_path: Path,
) -> None:
    calls = 0

    def first(_journal: RunJournalV2) -> TerminalDispositionV2:
        nonlocal calls
        calls += 1
        return TerminalDispositionV2(
            terminal_status=OperationStatus.COMPLETED,
            failure_operation_type=None,
            failure_operation_index=None,
            failure_code=None,
            diagnosis=_diagnosis(),
            tool_calls=1,
        )

    first_record = _execute(tmp_path / "run", first)

    def must_not_run(_journal: RunJournalV2) -> TerminalDispositionV2:
        raise AssertionError("second execution called the fake provider callback")

    second_record = _execute(tmp_path / "run", must_not_run)
    assert calls == 1
    assert second_record == first_record


def test_orphan_run_attempt_terminalizes_without_callback(tmp_path: Path) -> None:
    run_root = tmp_path / "run"
    journal = RunJournalV2(
        run_root,
        run_id="1" * 32,
        case_id="re2-ss-case-0001",
        system="RE2-SS",
        architecture="single_v2",
        started_at_utc=NOW,
    )
    journal.begin()

    def must_not_run(_journal: RunJournalV2) -> TerminalDispositionV2:
        raise AssertionError("orphan recovery called the fake provider callback")

    recovered = _execute(run_root, must_not_run)
    assert recovered.terminal_status is OperationStatus.PROTOCOL_VIOLATION
    assert (
        recovered.failure_code
        is OperationFailureCode.STARTED_ATTEMPT_WITHOUT_TERMINAL
    )
    assert recovered.failure_operation_type is None
    assert recovered.failure_operation_index is None


def test_orphan_recovery_uses_persisted_start_time_after_process_restart(
    tmp_path: Path,
) -> None:
    run_root = tmp_path / "run"
    journal = RunJournalV2(
        run_root,
        run_id="1" * 32,
        case_id="re2-ss-case-0001",
        system="RE2-SS",
        architecture="single_v2",
        started_at_utc=NOW,
    )
    journal.begin()

    recovered = execute_run_once(
        run_root,
        run_id="1" * 32,
        case_id="re2-ss-case-0001",
        system="RE2-SS",
        architecture="single_v2",
        started_at_utc=NOW + timedelta(minutes=1),
        callback=lambda _journal: (_ for _ in ()).throw(
            AssertionError("orphan recovery called the provider callback")
        ),
    )

    assert recovered.started_at_utc == NOW
    assert recovered.terminal_status is OperationStatus.PROTOCOL_VIOLATION


def test_orphan_operation_attempt_preserves_exact_stage_without_retry(
    tmp_path: Path,
) -> None:
    run_root = tmp_path / "run"
    journal = RunJournalV2(
        run_root,
        run_id="1" * 32,
        case_id="re2-ss-case-0001",
        system="RE2-SS",
        architecture="single_v2",
        started_at_utc=NOW,
    )
    journal.begin()
    try:
        journal.record_operation(
            1,
            OperationType.METRICS_SPECIALIST,
            lambda: (_ for _ in ()).throw(ConnectionError("interrupted")),
        )
    except ConnectionError:
        pass

    def must_not_run(_journal: RunJournalV2) -> TerminalDispositionV2:
        raise AssertionError("orphan operation recovery called the provider callback")

    recovered = _execute(run_root, must_not_run)
    assert recovered.failure_operation_type is OperationType.METRICS_SPECIALIST
    assert recovered.failure_operation_index == 1


def test_orphan_after_completed_operation_does_not_mislabel_it_as_failure(
    tmp_path: Path,
) -> None:
    run_root = tmp_path / "run"
    journal = RunJournalV2(
        run_root,
        run_id="1" * 32,
        case_id="re2-ss-case-0001",
        system="RE2-SS",
        architecture="single_v2",
        started_at_utc=NOW,
    )
    journal.begin()
    journal.record_operation(
        1, OperationType.METRICS_SPECIALIST, _completed_operation
    )

    recovered = _execute(
        run_root,
        lambda _journal: (_ for _ in ()).throw(
            AssertionError("orphan recovery called the provider callback")
        ),
    )

    assert recovered.failure_operation_type is None
    assert recovered.failure_operation_index is None
    assert recovered.tool_calls == 1
