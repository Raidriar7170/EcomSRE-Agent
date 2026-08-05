"""Deterministic 38-run ablation schedule and mock rehearsal."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta, timezone
import hashlib
from pathlib import Path
from typing import Literal, Protocol, cast

from ecomsre.phase5b.registry import validate_ablation_registry

from scripts.phase5b_execution.checkpoint import (
    _atomic_create,
    _ensure_private_directory,
    _entry_exists,
    _fsync_directory,
    _load_canonical,
)
from scripts.phase5b_execution.contracts import (
    AblationExecutionSeal,
    AblationRunRecord,
    AblationRunRequest,
    EvidenceClass,
    ExecutionAttemptMarker,
    ObservedDiagnosisRecord,
    ProviderUsageRecord,
    TerminalStatus,
    seal_ablation_record,
    sha256_canonical,
)


def _ablation_run_id(
    ablation_id: str,
    template_id: str,
    seed_id: str,
) -> str:
    return hashlib.sha256(
        b"phase5b.v1\0ablation\0"
        + ablation_id.encode()
        + b"\0"
        + template_id.encode()
        + b"\0"
        + seed_id.encode()
    ).hexdigest()[:32]


def build_ablation_schedule(registry_path: Path) -> tuple[AblationRunRequest, ...]:
    registry = validate_ablation_registry(registry_path)
    diagnosis_units = cast(list[dict[str, str]], registry["diagnosis_pairing_units"])
    remediation_units = cast(list[dict[str, str]], registry["remediation_pairing_units"])
    requests: list[AblationRunRequest] = []
    for item in cast(list[dict[str, object]], registry["ablations"]):
        ablation_id = cast(str, item["ablation_id"])
        is_remediation = ablation_id == "NO_INDEPENDENT_VERIFIER"
        units = remediation_units if is_remediation else diagnosis_units
        run_kind: Literal["DIAGNOSIS", "REMEDIATION"] = (
            "REMEDIATION" if is_remediation else "DIAGNOSIS"
        )
        for unit in units:
            template_id = unit["template_id"]
            seed_id = unit["seed_id"]
            requests.append(
                AblationRunRequest(
                    ablation_run_id=_ablation_run_id(
                        ablation_id, template_id, seed_id
                    ),
                    ablation_id=ablation_id,
                    template_id=template_id,
                    seed_id=seed_id,
                    run_kind=run_kind,
                )
            )
    if len(requests) != 38 or len({item.ablation_run_id for item in requests}) != 38:
        raise ValueError("ablation schedule is not the frozen 38-run set")
    return tuple(requests)


class _AblationStore:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.attempts_root = root / "ablation-attempts"
        self.records_root = root / "ablation-raw"
        _ensure_private_directory(self.root)
        _ensure_private_directory(self.attempts_root)
        _ensure_private_directory(self.records_root)

    def marker_path(self, run_id: str) -> Path:
        return self.attempts_root / f"{run_id}.json"

    def record_path(self, run_id: str) -> Path:
        return self.records_root / f"{run_id}.json"

    def load(self, run_id: str) -> AblationRunRecord | None:
        path = self.record_path(run_id)
        if not _entry_exists(path):
            return None
        record = _load_canonical(path, AblationRunRecord)
        record.verify_record_sha256()
        return record

    def _validated_marker(
        self,
        request: AblationRunRequest,
    ) -> ExecutionAttemptMarker:
        marker = _load_canonical(
            self.marker_path(request.ablation_run_id),
            ExecutionAttemptMarker,
        )
        if (
            marker.run_id != request.ablation_run_id
            or marker.request_sha256 != request.request_sha256()
        ):
            raise ValueError("ablation marker differs from the frozen request")
        return marker

    def reconcile_completed(
        self,
        request: AblationRunRequest,
    ) -> AblationRunRecord | None:
        record = self.load(request.ablation_run_id)
        if record is None:
            return None
        if (
            record.ablation_run_id != request.ablation_run_id
            or record.ablation_id != request.ablation_id
            or record.template_id != request.template_id
            or record.seed_id != request.seed_id
            or record.run_kind != request.run_kind
        ):
            raise ValueError("ablation record differs from the frozen request")
        marker_path = self.marker_path(request.ablation_run_id)
        if _entry_exists(marker_path):
            marker = self._validated_marker(request)
            if marker.evidence_class != record.evidence_class:
                raise ValueError("ablation record and marker evidence class differ")
            marker_path.unlink()
            _fsync_directory(marker_path.parent)
        return record

    def start(
        self,
        request: AblationRunRequest,
        *,
        evidence_class: EvidenceClass,
    ) -> None:
        marker = ExecutionAttemptMarker(
            run_id=request.ablation_run_id,
            request_sha256=request.request_sha256(),
            evidence_class=evidence_class,
            started_at_utc=datetime.now(timezone.utc),
        )
        _atomic_create(
            self.marker_path(request.ablation_run_id), marker.canonical_bytes()
        )

    def recover_interrupted(
        self,
        request: AblationRunRequest,
    ) -> AblationRunRecord | None:
        existing = self.reconcile_completed(request)
        if existing is not None:
            return existing
        marker_path = self.marker_path(request.ablation_run_id)
        if not _entry_exists(marker_path):
            return None
        marker = self._validated_marker(request)
        actual = marker.evidence_class == "ACTUAL_SCORED"
        interrupted = seal_ablation_record(
            request=request,
            terminal_status=(
                TerminalStatus.WORKFLOW_FAILURE
                if actual
                else TerminalStatus.PROVIDER_TRANSPORT_FAILURE
            ),
            observed_diagnosis=None,
            usage=ProviderUsageRecord(
                model_calls=0,
                tool_calls=0,
                input_tokens=0,
                output_tokens=0,
                total_tokens=0,
                workflow_tokens=0,
                combined_tokens=0,
                provider_network_calls=0,
                provider_usage_known=False,
            ),
            evidence_class=marker.evidence_class,
            provider_attempted=False,
            latency_ms=0,
            failure_code=(
                "ABLATION_NOT_IMPLEMENTED_IN_FROZEN_HARNESS"
                if actual
                else "INTERRUPTED_AFTER_ATTEMPT"
            ),
            failure_stage=(
                "ABLATION_IMPLEMENTATION" if actual else "OFFLINE_WORKFLOW"
            ),
            recorded_at_utc=datetime.now(timezone.utc),
        )
        self.complete(interrupted)
        return interrupted

    def complete(self, record: AblationRunRecord) -> None:
        marker = self.marker_path(record.ablation_run_id)
        if not _entry_exists(marker):
            raise ValueError("ablation terminal record requires an open marker")
        _atomic_create(
            self.record_path(record.ablation_run_id), record.canonical_bytes()
        )
        marker.unlink()
        _fsync_directory(marker.parent)


def _mock_record(
    request: AblationRunRequest,
    call_index: int,
) -> AblationRunRecord:
    diagnosis = ObservedDiagnosisRecord(
        run_id=request.ablation_run_id,
        decision="NEED_MORE_EVIDENCE",
        root_service=None,
        fault_mechanism=None,
        causal_chain=(),
        affected_sli="synthetic ablation rehearsal SLI",
        supporting_evidence=(),
        contradicting_evidence=(),
        missing_evidence=("Mock ablation does not score diagnosis truth.",),
        confidence=0.2,
        decision_rationale="Deterministic mock ablation result.",
        recommended_next_action="No external action.",
    )
    return seal_ablation_record(
        request=request,
        terminal_status=TerminalStatus.COMPLETED,
        observed_diagnosis=diagnosis,
        usage=ProviderUsageRecord(
            model_calls=1,
            tool_calls=2,
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


class AblationExecutor(Protocol):
    def __call__(self, request: AblationRunRequest) -> AblationRunRecord: ...


class UnsupportedFrozenAblationExecutor:
    """Fail closed for preregistered ablations lacking frozen semantics."""

    def __call__(self, request: AblationRunRequest) -> AblationRunRecord:
        return seal_ablation_record(
            request=request,
            terminal_status=TerminalStatus.WORKFLOW_FAILURE,
            observed_diagnosis=None,
            usage=ProviderUsageRecord(
                model_calls=0,
                tool_calls=0,
                input_tokens=0,
                output_tokens=0,
                total_tokens=0,
                workflow_tokens=0,
                combined_tokens=0,
                provider_network_calls=0,
                provider_usage_known=False,
            ),
            evidence_class="ACTUAL_SCORED",
            provider_attempted=False,
            latency_ms=0,
            failure_code="ABLATION_NOT_IMPLEMENTED_IN_FROZEN_HARNESS",
            failure_stage="ABLATION_IMPLEMENTATION",
            recorded_at_utc=datetime.now(timezone.utc),
        )


def _executor_failure(
    request: AblationRunRequest,
    evidence_class: EvidenceClass,
) -> AblationRunRecord:
    actual = evidence_class == "ACTUAL_SCORED"
    return seal_ablation_record(
        request=request,
        terminal_status=(
            TerminalStatus.WORKFLOW_FAILURE
        ),
        observed_diagnosis=None,
        usage=ProviderUsageRecord(
            model_calls=0,
            tool_calls=0,
            input_tokens=0,
            output_tokens=0,
            total_tokens=0,
            workflow_tokens=0,
            combined_tokens=0,
            provider_network_calls=0,
            provider_usage_known=False,
        ),
        evidence_class=evidence_class,
        provider_attempted=False,
        latency_ms=0,
        failure_code=(
            "ABLATION_NOT_IMPLEMENTED_IN_FROZEN_HARNESS"
            if actual
            else "WORKFLOW_FAILURE"
        ),
        failure_stage=(
            "ABLATION_IMPLEMENTATION" if actual else "OFFLINE_WORKFLOW"
        ),
        recorded_at_utc=datetime.now(timezone.utc),
    )


def run_ablation_schedule(
    *,
    registry_path: Path,
    output_root: Path,
    executor: AblationExecutor,
    sleeper: Callable[[float], object],
    evidence_class: EvidenceClass,
    integrity_guard: Callable[[], None] | None = None,
) -> dict[str, object]:
    schedule = build_ablation_schedule(registry_path)
    store = _AblationStore(output_root)
    calls = 0
    prior_attempt_seen = False
    for request in schedule:
        existing = store.reconcile_completed(request)
        if existing is not None:
            if existing.evidence_class != evidence_class:
                raise ValueError("ablation record evidence class differs from this run")
            prior_attempt_seen = True
            continue
        recovered = store.recover_interrupted(request)
        if recovered is not None:
            if recovered.evidence_class != evidence_class:
                raise ValueError("recovered ablation evidence class differs from this run")
            prior_attempt_seen = True
            continue
        if prior_attempt_seen:
            sleeper(2.0)
        if integrity_guard is not None:
            integrity_guard()
        store.start(request, evidence_class=evidence_class)
        try:
            record = executor(request)
        except Exception:
            record = _executor_failure(request, evidence_class)
        if integrity_guard is not None:
            try:
                integrity_guard()
            except Exception:
                drift = seal_ablation_record(
                    request=request,
                    terminal_status=TerminalStatus.SEMANTIC_FAILURE,
                    observed_diagnosis=None,
                    usage=record.usage,
                    evidence_class=evidence_class,
                    provider_attempted=record.provider_attempted,
                    latency_ms=record.latency_ms,
                    failure_code="EXECUTION_INTEGRITY_DRIFT",
                    failure_stage="POST_ATTEMPT_INTEGRITY",
                    recorded_at_utc=datetime.now(timezone.utc),
                )
                store.complete(drift)
                raise
        if (
            record.ablation_run_id != request.ablation_run_id
            or record.ablation_id != request.ablation_id
            or record.template_id != request.template_id
            or record.seed_id != request.seed_id
            or record.run_kind != request.run_kind
            or record.evidence_class != evidence_class
        ):
            raise ValueError("ablation executor record differs from frozen request")
        store.complete(record)
        calls += 1
        prior_attempt_seen = True
    collected: list[AblationRunRecord] = []
    for request in schedule:
        loaded = store.load(request.ablation_run_id)
        if loaded is not None:
            collected.append(loaded)
    records = tuple(collected)
    if any(record.evidence_class != evidence_class for record in records):
        raise ValueError("collected ablation evidence class differs from this run")
    expected_ids = {item.ablation_run_id for item in schedule}
    observed_ids = {path.stem for path in store.records_root.glob("*.json")}
    open_markers = tuple(store.attempts_root.glob("*.json"))
    if len(records) != 38 or observed_ids != expected_ids or open_markers:
        raise ValueError("ablation execution did not reach exact terminal closure")
    registry_sha = hashlib.sha256(registry_path.read_bytes()).hexdigest()
    record_hashes = {
        record.ablation_run_id: record.record_sha256 for record in records
    }
    seal = AblationExecutionSeal(
        schema_version="phase5b.ablation-execution-seal.v1",
        evaluation_version="phase5b.v1",
        ablation_registry_sha256=registry_sha,
        ablation_run_count=38,
        report_sha256=sha256_canonical(record_hashes),
        primary_eligible=False,
        provider_network_calls=sum(
            record.usage.provider_network_calls for record in records
        ),
    )
    return {
        "schema_version": (
            "phase5b.mock-ablation-rehearsal.v1"
            if evidence_class == "MOCK_EXECUTION_REHEARSAL"
            else "phase5b.ablation-execution-progress.v1"
        ),
        "evaluation_version": "phase5b.v1",
        "evidence_class": evidence_class,
        "not_model_evidence": evidence_class == "MOCK_EXECUTION_REHEARSAL",
        "ablation_run_count": 38,
        "unique_terminal_records": len(records),
        "diagnosis_run_count": sum(
            record.run_kind == "DIAGNOSIS" for record in records
        ),
        "remediation_run_count": sum(
            record.run_kind == "REMEDIATION" for record in records
        ),
        "primary_eligible": False,
        "primary_disposition": "PRIMARY_INELIGIBLE",
        "executor_calls_this_process": calls,
        "provider_network_calls": seal.provider_network_calls,
        "ground_truth_reads": 0,
        "provider_pacing_seconds": 2,
        "all_checkpoints_closed": True,
        "hidden_retry": False,
        "scripted_fallback": False,
        "seal": seal.model_dump(mode="json"),
    }
def run_mock_ablation_rehearsal(
    *,
    registry_path: Path,
    output_root: Path,
    sleeper: Callable[[float], object],
) -> dict[str, object]:
    call_index = 0

    def execute(request: AblationRunRequest) -> AblationRunRecord:
        nonlocal call_index
        record = _mock_record(request, call_index)
        call_index += 1
        return record

    return run_ablation_schedule(
        registry_path=registry_path,
        output_root=output_root,
        executor=execute,
        sleeper=sleeper,
        evidence_class="MOCK_EXECUTION_REHEARSAL",
    )
