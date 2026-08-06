from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from ecomsre.phase5b.protocol import load_strict_json
from ecomsre.phase5b.contracts import ExecutionSchedule
from scripts.phase5b_execution.checkpoint import CheckpointStore
from scripts.phase5b_execution.contracts import (
    ExecutionAttemptMarker,
    ObservedDiagnosisRecord,
    ProviderUsageRecord,
    RawScoredRunRecord,
    ScoredRunRequest,
    TerminalStatus,
    seal_raw_record,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _schedule() -> ExecutionSchedule:
    return load_strict_json(
        PROJECT_ROOT / "config/phase5b/execution-schedule.v1.json",
        ExecutionSchedule,
    )


def _request() -> ScoredRunRequest:
    return ScoredRunRequest.from_scheduled_run(_schedule().runs[0])


def _usage(*, network_calls: int = 0) -> ProviderUsageRecord:
    return ProviderUsageRecord(
        model_calls=1,
        tool_calls=3,
        input_tokens=100,
        output_tokens=50,
        total_tokens=150,
        workflow_tokens=0,
        combined_tokens=150,
        provider_network_calls=network_calls,
        provider_usage_known=True,
    )


def _mock_record(request: ScoredRunRequest) -> RawScoredRunRecord:
    diagnosis = ObservedDiagnosisRecord(
        run_id=request.run_id,
        decision="NEED_MORE_EVIDENCE",
        root_service=None,
        fault_mechanism=None,
        causal_chain=(),
        affected_sli="synthetic rehearsal SLI",
        supporting_evidence=(),
        contradicting_evidence=(),
        missing_evidence=("Mock rehearsal does not score diagnosis truth.",),
        confidence=0.2,
        decision_rationale="Deterministic mock result.",
        recommended_next_action="No external action.",
    )
    return seal_raw_record(
        run_id=request.run_id,
        template_id=request.template_id,
        seed_id=request.seed_id,
        variant=request.variant,
        terminal_status=TerminalStatus.COMPLETED,
        observed_diagnosis=diagnosis,
        usage=_usage(),
        evidence_class="MOCK_EXECUTION_REHEARSAL",
        provider_attempted=False,
        latency_ms=0,
        failure_code=None,
        failure_stage=None,
    )


def test_scored_request_is_strict_frozen_and_bound_to_schedule() -> None:
    schedule = _schedule()
    request = ScoredRunRequest.from_scheduled_run(schedule.runs[0])
    request.require_schedule_membership(schedule)

    with pytest.raises(ValidationError):
        ScoredRunRequest.model_validate(
            {**request.model_dump(mode="json"), "ground_truth_root": "/forbidden"}
        )
    with pytest.raises(ValidationError):
        request.run_id = "0" * 32  # type: ignore[misc]
    with pytest.raises(ValueError, match="frozen schedule"):
        request.model_copy(update={"run_id": "0" * 32}).require_schedule_membership(
            schedule
        )


def test_raw_record_is_canonical_self_hashed_and_secret_free() -> None:
    record = _mock_record(_request())
    record.verify_record_sha256()
    serialized = record.canonical_bytes()

    assert serialized.endswith(b"\n")
    assert json.loads(serialized)["record_sha256"] == record.record_sha256
    forbidden = {"api_key", "authorization", "endpoint", "headers", "raw_response"}
    assert not forbidden.intersection(record.model_dump(mode="json"))

    with pytest.raises(ValidationError):
        RawScoredRunRecord.model_validate(
            {**record.model_dump(mode="json"), "api_key": "forbidden"}
        )


def test_attempt_marker_recovery_never_reexecutes_interrupted_run(
    tmp_path: Path,
) -> None:
    request = _request()
    store = CheckpointStore(tmp_path)
    marker = store.start(request)

    assert isinstance(marker, ExecutionAttemptMarker)
    assert store.marker_path(request.run_id).exists()
    assert not store.record_path(request.run_id).exists()

    recovered = store.recover_interrupted(request)

    assert recovered is not None
    assert recovered.terminal_status is TerminalStatus.PROVIDER_TRANSPORT_FAILURE
    assert recovered.failure_code == "INTERRUPTED_AFTER_ATTEMPT"
    assert recovered.provider_attempted is True
    assert recovered.usage.provider_network_calls == 1
    assert store.record_path(request.run_id).exists()
    assert not store.marker_path(request.run_id).exists()
    assert store.recover_interrupted(request) == recovered


def test_checkpoint_completion_is_create_once(tmp_path: Path) -> None:
    request = _request()
    store = CheckpointStore(tmp_path)
    store.start(request)
    record = _mock_record(request)
    store.complete(record)

    assert store.load_record(request.run_id) == record
    assert not store.marker_path(request.run_id).exists()
    with pytest.raises(FileExistsError):
        store.start(request)
    with pytest.raises(FileExistsError):
        store.complete(record)
