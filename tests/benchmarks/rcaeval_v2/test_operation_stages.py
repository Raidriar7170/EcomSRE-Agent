from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

import pytest

from ecomsre_rcaeval_v2.contracts import (
    OperationFailureCode,
    OperationStage,
    OperationStatus,
    OperationType,
    ProviderUsageDelta,
    SafeValidationError,
    SpecialistOperationRecord,
    TerminalDispositionV2,
)
from ecomsre_rcaeval_v2.observability import (
    OperationTransaction,
    RunJournalV2,
    execute_run_once,
    write_private_snapshot_create_once,
)
from ecomsre_rcaeval_v2.contracts import SpecialistAssessmentV2


NOW = datetime(2026, 8, 7, tzinfo=timezone.utc)
RUN_ID = "1" * 32
CASE_ID = "re2-ob-case-0001"


def _usage(calls: int = 0) -> ProviderUsageDelta:
    return ProviderUsageDelta(
        model_calls_delta=calls,
        prompt_tokens_delta=10 if calls else 0,
        completion_tokens_delta=5 if calls else 0,
        total_tokens_delta=15 if calls else 0,
    )


def _assessment() -> SpecialistAssessmentV2:
    return SpecialistAssessmentV2(
        source="metrics",
        candidate_service="cartservice",
        candidate_indicator="mem",
        confidence=0.8,
        supporting_evidence_refs=("metric:0001",),
        contradicting_evidence_refs=(),
        summary="Bounded metrics support cartservice.",
    )


def _completed_record(
    transaction: OperationTransaction,
    input_sha: str,
    output_sha: str,
) -> SpecialistOperationRecord:
    return SpecialistOperationRecord(
        schema_version="rcaeval-re2-v2.operation-record.v1",
        run_id=RUN_ID,
        case_id=CASE_ID,
        system="RE2-OB",
        architecture="fixed_v2",
        operation_index=1,
        operation_type=OperationType.METRICS_SPECIALIST,
        source="metrics",
        started_at_utc=NOW,
        ended_at_utc=NOW,
        latency_ms=0.0,
        status=OperationStatus.COMPLETED,
        failure_code=None,
        failure_stage=None,
        last_completed_stage=OperationStage.OUTPUT_PERSISTENCE,
        stage_trace_sha256=transaction.stage_trace_sha256(),
        safe_validation_error=None,
        provider_call_index=1,
        input_snapshot_sha256=input_sha,
        output_snapshot_sha256=output_sha,
        usage_delta=_usage(1),
        investigated_sources=("metrics",),
        evidence_refs_visible_to_operation=("metric:0001",),
        selected_sources=(),
        typed_output=_assessment(),
    )


def test_operation_attempt_and_stage_markers_precede_work_and_bind_record(
    tmp_path: Path,
) -> None:
    run_root = tmp_path / "run"
    journal = RunJournalV2(
        run_root,
        run_id=RUN_ID,
        case_id=CASE_ID,
        system="RE2-OB",
        architecture="fixed_v2",
        started_at_utc=NOW,
    )
    journal.begin()

    def callback(transaction: OperationTransaction) -> SpecialistOperationRecord:
        attempt = run_root / "operation-attempts" / "0001-METRICS_SPECIALIST.json"
        assert attempt.is_file()
        transaction.start_stage(OperationStage.INPUT_SANITIZATION)
        assert (
            run_root
            / "operation-stages"
            / "0001-METRICS_SPECIALIST-01-INPUT_SANITIZATION.json"
        ).is_file()
        transaction.start_stage(OperationStage.INPUT_CONSTRUCTION)
        transaction.start_stage(OperationStage.INPUT_PERSISTENCE)
        input_sha = write_private_snapshot_create_once(
            run_root, "0001-metrics-specialist-input", _assessment()
        )
        transaction.start_stage(OperationStage.PROVIDER_CALL)
        transaction.start_stage(OperationStage.OUTPUT_VALIDATION)
        transaction.start_stage(OperationStage.OUTPUT_PERSISTENCE)
        output_sha = write_private_snapshot_create_once(
            run_root, "0001-metrics-specialist-output", _assessment()
        )
        return _completed_record(transaction, input_sha, output_sha)

    record = journal.record_operation(1, OperationType.METRICS_SPECIALIST, callback)

    stage_names = [
        path.name for path in sorted((run_root / "operation-stages").iterdir())
    ]
    assert stage_names == [
        "0001-METRICS_SPECIALIST-01-INPUT_SANITIZATION.json",
        "0001-METRICS_SPECIALIST-02-INPUT_CONSTRUCTION.json",
        "0001-METRICS_SPECIALIST-03-INPUT_PERSISTENCE.json",
        "0001-METRICS_SPECIALIST-04-PROVIDER_CALL.json",
        "0001-METRICS_SPECIALIST-05-OUTPUT_VALIDATION.json",
        "0001-METRICS_SPECIALIST-06-OUTPUT_PERSISTENCE.json",
        "0001-METRICS_SPECIALIST-07-COMPLETED.json",
    ]
    operation_path = run_root / "operations" / "0001-METRICS_SPECIALIST.json"
    completion = json.loads(
        (run_root / "operation-stages" / stage_names[-1]).read_text()
    )
    assert (
        completion["operation_record_sha256"]
        == hashlib.sha256(operation_path.read_bytes()).hexdigest()
    )
    assert completion["stage_trace_sha256"] == record.stage_trace_sha256


def test_failed_operation_contract_has_exact_stage_and_safe_diagnostics() -> None:
    record = SpecialistOperationRecord(
        schema_version="rcaeval-re2-v2.operation-record.v1",
        run_id=RUN_ID,
        case_id=CASE_ID,
        system="RE2-OB",
        architecture="fixed_v2",
        operation_index=1,
        operation_type=OperationType.METRICS_SPECIALIST,
        source="metrics",
        started_at_utc=NOW,
        ended_at_utc=NOW,
        latency_ms=0.0,
        status=OperationStatus.PROTOCOL_VIOLATION,
        failure_code=OperationFailureCode.AGENT_VISIBLE_PRIVATE_PATH_REMAINED,
        failure_stage=OperationStage.INPUT_SANITIZATION,
        last_completed_stage=None,
        stage_trace_sha256="a" * 64,
        safe_validation_error=SafeValidationError(
            error_class="ValidationError",
            field_paths=("bounded_evidence.0.observation",),
            constraint_types=("agent_visible_private_path",),
            error_count=1,
        ),
        provider_call_index=None,
        input_snapshot_sha256=None,
        output_snapshot_sha256=None,
        usage_delta=_usage(),
        investigated_sources=("metrics",),
        evidence_refs_visible_to_operation=("metric:0001",),
        selected_sources=(),
        typed_output=None,
    )

    assert record.failure_stage is OperationStage.INPUT_SANITIZATION
    assert record.usage_delta.model_calls_delta == 0
    diagnostics = record.safe_validation_error.model_dump_json()
    assert "/Users/" not in diagnostics
    assert "raidriar" not in diagnostics


def test_orphan_recovery_uses_last_stage_and_never_reinvokes_callback(
    tmp_path: Path,
) -> None:
    run_root = tmp_path / "run"
    journal = RunJournalV2(
        run_root,
        run_id=RUN_ID,
        case_id=CASE_ID,
        system="RE2-OB",
        architecture="fixed_v2",
        started_at_utc=NOW,
    )
    journal.begin()

    def crash(transaction: OperationTransaction) -> SpecialistOperationRecord:
        transaction.start_stage(OperationStage.INPUT_SANITIZATION)
        transaction.start_stage(OperationStage.INPUT_CONSTRUCTION)
        raise RuntimeError("synthetic crash")

    with pytest.raises(RuntimeError, match="synthetic crash"):
        journal.record_operation(1, OperationType.METRICS_SPECIALIST, crash)

    callback_invocations = 0

    def forbidden_callback(_journal: RunJournalV2) -> TerminalDispositionV2:
        nonlocal callback_invocations
        callback_invocations += 1
        raise AssertionError("recovery must not rerun work")

    terminal = execute_run_once(
        run_root,
        run_id=RUN_ID,
        case_id=CASE_ID,
        system="RE2-OB",
        architecture="fixed_v2",
        started_at_utc=NOW,
        callback=forbidden_callback,
    )

    assert callback_invocations == 0
    assert terminal.terminal_status is OperationStatus.PROTOCOL_VIOLATION
    assert terminal.failure_operation_type is OperationType.METRICS_SPECIALIST
    assert terminal.failure_operation_index == 1
    assert terminal.failure_stage is OperationStage.INPUT_CONSTRUCTION
    assert (
        terminal.failure_code is OperationFailureCode.STARTED_OPERATION_WITHOUT_TERMINAL
    )
    assert terminal.usage.model_calls_delta == 0
