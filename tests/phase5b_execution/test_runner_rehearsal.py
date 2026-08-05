from __future__ import annotations

from pathlib import Path
import pytest

from ecomsre.phase5b.contracts import ExecutionSchedule
from ecomsre.phase5b.protocol import load_strict_json

from scripts.phase5b_execution.checkpoint import CheckpointStore, _atomic_create
from scripts.phase5b_execution.contracts import (
    ObservedDiagnosisRecord,
    ProviderUsageRecord,
    ScoredRunRequest,
    TerminalStatus,
    seal_raw_record,
)
from scripts.phase5b_execution.runner import (
    MockScheduledExecutor,
    run_frozen_schedule,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _schedule() -> ExecutionSchedule:
    return load_strict_json(
        PROJECT_ROOT / "config/phase5b/execution-schedule.v1.json",
        ExecutionSchedule,
    )


def test_complete_180_run_mock_rehearsal_preserves_order_and_pacing(
    tmp_path: Path,
) -> None:
    schedule = _schedule()
    waits: list[float] = []
    executor = MockScheduledExecutor()

    report = run_frozen_schedule(
        schedule=schedule,
        output_root=tmp_path,
        executor=executor,
        sleeper=waits.append,
    )

    expected_order = [item.run_id for item in schedule.runs]
    assert executor.call_order == expected_order
    assert waits == [2.0] * 179
    assert report["schema_version"] == "phase5b.mock-execution-rehearsal.v1"
    assert report["evidence_class"] == "MOCK_EXECUTION_REHEARSAL"
    assert report["not_model_evidence"] is True
    assert report["run_count"] == 180
    assert report["unique_terminal_records"] == 180
    assert report["missing_run_ids"] == []
    assert report["extra_run_ids"] == []
    assert report["executor_attempts_this_process"] == 180
    assert report["provider_network_calls"] == 0
    assert report["ground_truth_reads"] == 0
    assert report["all_checkpoints_closed"] is True
    assert len(list((tmp_path / "raw").glob("*.json"))) == 180
    assert list((tmp_path / "attempts").glob("*.json")) == []

    with pytest.raises(ValueError, match="evidence class"):
        run_frozen_schedule(
            schedule=schedule,
            output_root=tmp_path,
            executor=MockScheduledExecutor(),
            sleeper=lambda _seconds: None,
            evidence_class="ACTUAL_SCORED",
        )


def test_resume_converts_open_marker_to_terminal_without_executor_call(
    tmp_path: Path,
) -> None:
    schedule = _schedule()
    first = ScoredRunRequest.from_scheduled_run(schedule.runs[0])
    store = CheckpointStore(tmp_path)
    store.start(first, evidence_class="MOCK_EXECUTION_REHEARSAL")
    waits: list[float] = []
    executor = MockScheduledExecutor()

    report = run_frozen_schedule(
        schedule=schedule,
        output_root=tmp_path,
        executor=executor,
        sleeper=waits.append,
    )

    recovered = store.load_record(first.run_id)
    assert recovered is not None
    assert recovered.terminal_status is TerminalStatus.PROVIDER_TRANSPORT_FAILURE
    assert recovered.failure_code == "INTERRUPTED_AFTER_ATTEMPT"
    assert first.run_id not in executor.call_order
    assert executor.call_order == [item.run_id for item in schedule.runs[1:]]
    assert waits == [2.0] * 179
    assert report["executor_attempts_this_process"] == 179
    assert report["unique_terminal_records"] == 180


def test_integrity_guard_stops_after_post_attempt_drift(tmp_path: Path) -> None:
    schedule = _schedule()
    executor = MockScheduledExecutor()
    checks = 0

    def guard() -> None:
        nonlocal checks
        checks += 1
        if checks == 2:
            raise ValueError("source drift")

    with pytest.raises(ValueError, match="source drift"):
        run_frozen_schedule(
            schedule=schedule,
            output_root=tmp_path,
            executor=executor,
            sleeper=lambda _seconds: None,
            evidence_class="ACTUAL_SCORED",
            integrity_guard=guard,
        )

    assert executor.calls == 1
    first = CheckpointStore(tmp_path).load_record(schedule.runs[0].run_id)
    assert first is not None
    assert first.failure_code == "EXECUTION_INTEGRITY_DRIFT"
    assert first.usage.provider_network_calls == 0


def test_resume_closes_stale_marker_after_terminal_record_fsync(
    tmp_path: Path,
) -> None:
    schedule = _schedule()
    first = ScoredRunRequest.from_scheduled_run(schedule.runs[0])
    store = CheckpointStore(tmp_path)
    store.start(first, evidence_class="MOCK_EXECUTION_REHEARSAL")
    diagnosis = ObservedDiagnosisRecord(
        run_id=first.run_id,
        decision="NEED_MORE_EVIDENCE",
        root_service=None,
        fault_mechanism=None,
        causal_chain=(),
        affected_sli="synthetic",
        supporting_evidence=(),
        contradicting_evidence=(),
        missing_evidence=("synthetic gap",),
        confidence=0.2,
        decision_rationale="Synthetic crash-window record.",
        recommended_next_action="No external action.",
    )
    record = seal_raw_record(
        run_id=first.run_id,
        template_id=first.template_id,
        seed_id=first.seed_id,
        variant=first.variant,
        terminal_status=TerminalStatus.COMPLETED,
        observed_diagnosis=diagnosis,
        usage=ProviderUsageRecord(
            model_calls=1,
            tool_calls=0,
            input_tokens=1,
            output_tokens=1,
            total_tokens=2,
            workflow_tokens=0,
            combined_tokens=2,
            provider_network_calls=0,
            provider_usage_known=True,
        ),
        evidence_class="MOCK_EXECUTION_REHEARSAL",
        provider_attempted=False,
        latency_ms=0,
        failure_code=None,
        failure_stage=None,
    )
    _atomic_create(store.record_path(first.run_id), record.canonical_bytes())
    waits: list[float] = []

    report = run_frozen_schedule(
        schedule=schedule,
        output_root=tmp_path,
        executor=MockScheduledExecutor(),
        sleeper=waits.append,
    )

    assert not store.marker_path(first.run_id).exists()
    assert waits == [2.0] * 179
    assert report["unique_terminal_records"] == 180


def test_actual_executor_exception_is_terminal_and_never_retried(
    tmp_path: Path,
) -> None:
    schedule = _schedule()

    class FailingActualExecutor:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def __call__(self, request: ScoredRunRequest):
            self.calls.append(request.run_id)
            raise RuntimeError("unreadable child result")

    executor = FailingActualExecutor()
    report = run_frozen_schedule(
        schedule=schedule,
        output_root=tmp_path,
        executor=executor,
        sleeper=lambda _seconds: None,
        evidence_class="ACTUAL_SCORED",
    )

    assert executor.calls == [item.run_id for item in schedule.runs]
    assert report["schema_version"] == "phase5b.execution-progress.v1"
    assert report["evidence_class"] == "ACTUAL_SCORED"
    assert report["unique_terminal_records"] == 180
    assert report["provider_network_calls"] == 180
    first = CheckpointStore(tmp_path).load_record(schedule.runs[0].run_id)
    assert first is not None
    assert first.terminal_status is TerminalStatus.PROVIDER_TRANSPORT_FAILURE
    assert first.failure_code == "INTERRUPTED_OR_UNREADABLE_WORKER_RESULT"
