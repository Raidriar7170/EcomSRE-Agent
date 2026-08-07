from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path

import pytest

from ecomsre_rcaeval_v2.contracts import (
    DiagnosisV2,
    OperationStatus,
    OperationType,
    ProviderUsageDelta,
    SpecialistAssessmentV2,
    SpecialistOperationRecord,
    TerminalDispositionV2,
)
from ecomsre_rcaeval_v2.observability import (
    RunJournalV2,
    compute_operation_tree_sha256,
    execute_run_once,
    write_private_snapshot_create_once,
)


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


def _assessment() -> SpecialistAssessmentV2:
    return SpecialistAssessmentV2(
        source="metrics",
        candidate_service="cartservice",
        candidate_indicator="mem",
        confidence=0.9,
        supporting_evidence_refs=("metric:0001",),
        contradicting_evidence_refs=(),
        summary="Bounded metrics support cartservice memory pressure.",
    )


def _operation(input_sha: str, output_sha: str) -> SpecialistOperationRecord:
    return SpecialistOperationRecord(
        schema_version="rcaeval-re2-v2.operation-record.v1",
        run_id="1" * 32,
        case_id="re2-ob-case-0001",
        system="RE2-OB",
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
        input_snapshot_sha256=input_sha,
        output_snapshot_sha256=output_sha,
        usage_delta=ProviderUsageDelta(
            model_calls_delta=1,
            prompt_tokens_delta=10,
            completion_tokens_delta=5,
            total_tokens_delta=15,
        ),
        investigated_sources=("metrics",),
        evidence_refs_visible_to_operation=("metric:0001",),
        selected_sources=(),
        typed_output=_assessment(),
    )


def test_private_typed_snapshot_is_create_once_durable_and_contains_no_path(
    tmp_path: Path,
) -> None:
    run_root = tmp_path / "private-run"
    digest = write_private_snapshot_create_once(run_root, "specialist-output", _assessment())
    path = run_root / "snapshots" / "specialist-output.json"

    assert path.is_file()
    assert path.stat().st_mode & 0o777 == 0o600
    assert run_root.stat().st_mode & 0o077 == 0
    assert len(digest) == 64
    assert "/Users/" not in path.read_text(encoding="utf-8")
    with pytest.raises(FileExistsError):
        write_private_snapshot_create_once(run_root, "specialist-output", _assessment())


def test_run_journal_writes_attempts_before_callbacks_and_binds_recomputable_hashes(
    tmp_path: Path,
) -> None:
    run_root = tmp_path / "run"

    def run_callback(journal: RunJournalV2) -> TerminalDispositionV2:
        assert (run_root / "run-attempt.json").is_file()
        input_sha = write_private_snapshot_create_once(
            run_root, "specialist-input", _assessment()
        )
        output_sha = write_private_snapshot_create_once(
            run_root, "specialist-output", _assessment()
        )

        def operation_callback() -> SpecialistOperationRecord:
            marker = run_root / "operation-attempts" / "0001-METRICS_SPECIALIST.json"
            assert marker.is_file()
            return _operation(input_sha, output_sha)

        journal.record_operation(
            1, OperationType.METRICS_SPECIALIST, operation_callback
        )
        return TerminalDispositionV2(
            terminal_status=OperationStatus.COMPLETED,
            failure_operation_type=None,
            failure_operation_index=None,
            failure_code=None,
            diagnosis=_diagnosis(),
            tool_calls=1,
        )

    terminal = execute_run_once(
        run_root,
        run_id="1" * 32,
        case_id="re2-ob-case-0001",
        system="RE2-OB",
        architecture="single_v2",
        started_at_utc=NOW,
        callback=run_callback,
    )
    trace_payload = json.loads((run_root / "run-trace.json").read_text())

    assert trace_payload["operation_count"] == 1
    assert terminal.run_trace_sha256 != terminal.operation_tree_sha256
    assert terminal.operation_tree_sha256 == compute_operation_tree_sha256(
        tuple(journal_entry for journal_entry in trace_payload["operations"])
    )
    assert (run_root / "terminal-record.json").is_file()
    assert terminal.diagnosis == _diagnosis()
    assert terminal.tool_calls == 1


def test_operation_indices_must_be_contiguous(tmp_path: Path) -> None:
    journal = RunJournalV2(
        tmp_path / "run",
        run_id="1" * 32,
        case_id="re2-ob-case-0001",
        system="RE2-OB",
        architecture="single_v2",
        started_at_utc=NOW,
    )
    journal.begin()
    with pytest.raises(ValueError, match="contiguous"):
        journal.record_operation(
            2,
            OperationType.METRICS_SPECIALIST,
            lambda: _operation("a" * 64, "b" * 64),
        )
