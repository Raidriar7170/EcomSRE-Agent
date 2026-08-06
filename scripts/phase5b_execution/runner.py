"""Exact-order no-retry runner for the frozen 180-run schedule."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path
import time
from typing import Protocol

from ecomsre.phase5b.contracts import ExecutionSchedule

from scripts.phase5b_execution.checkpoint import CheckpointStore
from scripts.phase5b_execution.contracts import (
    EvidenceClass,
    ExecutionBundleManifest,
    ObservedDiagnosisRecord,
    ProviderUsageRecord,
    RawScoredRunRecord,
    ScoredRunRequest,
    TerminalStatus,
    seal_raw_record,
)


class ScheduledExecutor(Protocol):
    def __call__(self, request: ScoredRunRequest) -> RawScoredRunRecord: ...


class MockScheduledExecutor:
    def __init__(self) -> None:
        self.call_order: list[str] = []

    @property
    def calls(self) -> int:
        return len(self.call_order)

    def __call__(self, request: ScoredRunRequest) -> RawScoredRunRecord:
        call_index = len(self.call_order)
        self.call_order.append(request.run_id)
        tool_calls = {
            "SINGLE_AGENT_V2": 4,
            "FIXED_SPECIALIST_V2": 3,
            "DYNAMIC_MULTI_AGENT_V2": 2,
        }[request.variant]
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
            usage=ProviderUsageRecord(
                model_calls=1,
                tool_calls=tool_calls,
                input_tokens=100,
                output_tokens=50,
                total_tokens=150,
                workflow_tokens=0,
                combined_tokens=150,
                provider_network_calls=0,
                provider_usage_known=True,
            ),
            evidence_class="MOCK_EXECUTION_REHEARSAL",
            provider_attempted=False,
            latency_ms=0,
            failure_code=None,
            failure_stage=None,
            recorded_at_utc=(
                datetime(2026, 8, 4, tzinfo=timezone.utc)
                + timedelta(seconds=call_index * 2)
            ),
        )


def _executor_failure(
    request: ScoredRunRequest,
    evidence_class: EvidenceClass,
    *,
    latency_ms: int = 0,
) -> RawScoredRunRecord:
    actual = evidence_class == "ACTUAL_SCORED"
    return seal_raw_record(
        run_id=request.run_id,
        template_id=request.template_id,
        seed_id=request.seed_id,
        variant=request.variant,
        terminal_status=(
            TerminalStatus.PROVIDER_TRANSPORT_FAILURE
            if actual
            else TerminalStatus.WORKFLOW_FAILURE
        ),
        observed_diagnosis=None,
        usage=ProviderUsageRecord(
            model_calls=1 if actual else 0,
            tool_calls=0,
            input_tokens=0,
            output_tokens=0,
            total_tokens=0,
            workflow_tokens=0,
            combined_tokens=0,
            provider_network_calls=1 if actual else 0,
            provider_usage_known=False,
        ),
        evidence_class=evidence_class,
        provider_attempted=actual,
        latency_ms=latency_ms,
        failure_code=(
            "INTERRUPTED_OR_UNREADABLE_WORKER_RESULT"
            if actual
            else "WORKFLOW_FAILURE"
        ),
        failure_stage="HTTP_TRANSPORT" if actual else "OFFLINE_WORKFLOW",
    )


def _integrity_failure(
    request: ScoredRunRequest,
    evidence_class: EvidenceClass,
    attempted: RawScoredRunRecord,
) -> RawScoredRunRecord:
    return seal_raw_record(
        run_id=request.run_id,
        template_id=request.template_id,
        seed_id=request.seed_id,
        variant=request.variant,
        terminal_status=TerminalStatus.SEMANTIC_FAILURE,
        observed_diagnosis=None,
        usage=attempted.usage,
        evidence_class=evidence_class,
        provider_attempted=attempted.provider_attempted,
        latency_ms=attempted.latency_ms,
        latency_known=attempted.latency_known,
        failure_code="EXECUTION_INTEGRITY_DRIFT",
        failure_stage="POST_ATTEMPT_INTEGRITY",
        investigated_sources=attempted.investigated_sources,
        targeted_refinement_used=attempted.targeted_refinement_used,
    )


def run_frozen_schedule(
    *,
    schedule: ExecutionSchedule,
    output_root: Path,
    executor: ScheduledExecutor,
    sleeper: Callable[[float], object],
    evidence_class: EvidenceClass = "MOCK_EXECUTION_REHEARSAL",
    integrity_guard: Callable[[], None] | None = None,
) -> dict[str, object]:
    store = CheckpointStore(output_root)
    calls_this_process = 0
    prior_attempt_seen = False
    for scheduled in schedule.runs:
        request = ScoredRunRequest.from_scheduled_run(scheduled)
        request.require_schedule_membership(schedule)
        existing = store.reconcile_completed(request)
        if existing is not None:
            if existing.evidence_class != evidence_class:
                raise ValueError("terminal record evidence class differs from this run")
            prior_attempt_seen = True
            continue
        recovered = store.recover_interrupted(request)
        if recovered is not None:
            if recovered.evidence_class != evidence_class:
                raise ValueError("recovered record evidence class differs from this run")
            prior_attempt_seen = True
            continue
        if prior_attempt_seen:
            sleeper(float(schedule.provider_pacing_seconds))
        if integrity_guard is not None:
            integrity_guard()
        store.start(request, evidence_class=evidence_class)
        attempt_started = time.monotonic()
        try:
            record = executor(request)
        except Exception:
            record = _executor_failure(
                request,
                evidence_class,
                latency_ms=max(0, int((time.monotonic() - attempt_started) * 1000)),
            )
        if integrity_guard is not None:
            try:
                integrity_guard()
            except Exception:
                store.complete(_integrity_failure(request, evidence_class, record))
                raise
        if (
            record.run_id != request.run_id
            or record.template_id != request.template_id
            or record.seed_id != request.seed_id
            or record.variant != request.variant
            or record.evidence_class != evidence_class
        ):
            raise ValueError("executor record differs from the frozen request")
        store.complete(record)
        calls_this_process += 1
        prior_attempt_seen = True

    expected_ids = tuple(item.run_id for item in schedule.runs)
    collected: list[RawScoredRunRecord] = []
    for run_id in expected_ids:
        loaded = store.load_record(run_id)
        if loaded is not None:
            collected.append(loaded)
    records = tuple(collected)
    if any(record.evidence_class != evidence_class for record in records):
        raise ValueError("collected record evidence class differs from this run")
    observed_ids = {path.stem for path in store.records_root.glob("*.json")}
    expected_set = set(expected_ids)
    missing = sorted(expected_set - observed_ids)
    extra = sorted(observed_ids - expected_set)
    open_markers = tuple(store.attempts_root.glob("*.json"))
    if len(records) != 180 or missing or extra or open_markers:
        raise ValueError("frozen schedule did not reach exact terminal closure")
    manifest = ExecutionBundleManifest(
        schema_version="phase5b.execution-bundle-manifest.v1",
        evaluation_version="phase5b.v1",
        execution_schedule_sha256=(
            "a711696a2c12745e062d068fd507b74a4ce67e845505b05f458d7db5a97d37ec"
        ),
        record_count=180,
        record_sha256_by_run_id={
            record.run_id: record.record_sha256 for record in records
        },
        all_checkpoints_closed=True,
        provider_network_calls=sum(
            record.usage.provider_network_calls for record in records
        ),
        hidden_retry=False,
        scripted_fallback=False,
    )
    return {
        "schema_version": (
            "phase5b.mock-execution-rehearsal.v1"
            if evidence_class == "MOCK_EXECUTION_REHEARSAL"
            else "phase5b.execution-progress.v1"
        ),
        "evaluation_version": "phase5b.v1",
        "evidence_class": evidence_class,
        "not_model_evidence": evidence_class == "MOCK_EXECUTION_REHEARSAL",
        "run_count": 180,
        "unique_terminal_records": len(records),
        "missing_run_ids": missing,
        "extra_run_ids": extra,
        "executor_attempts_this_process": calls_this_process,
        "provider_network_calls": manifest.provider_network_calls,
        "ground_truth_reads": 0,
        "provider_pacing_seconds": schedule.provider_pacing_seconds,
        "all_checkpoints_closed": True,
        "hidden_retry": False,
        "scripted_fallback": False,
        "bundle_manifest": manifest.model_dump(mode="json"),
    }
