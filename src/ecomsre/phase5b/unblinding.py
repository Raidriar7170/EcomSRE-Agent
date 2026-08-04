"""Fail-closed Phase 5B protocol and unblinding state transitions."""

from __future__ import annotations

from enum import Enum
import hashlib
import json
import os
from pathlib import Path

from ecomsre.phase5b.analysis import validate_complete_results
from ecomsre.phase5b.contracts import (
    ExecutionSchedule,
    FrozenExecutionReport,
    UnblindingRecord,
)
from ecomsre.phase5b.protocol import load_strict_json


class ProtocolState(str, Enum):
    PROTOCOL_FROZEN = "PROTOCOL_FROZEN"
    HIDDEN_PACK_SEALED = "HIDDEN_PACK_SEALED"
    EXECUTION_STARTED = "EXECUTION_STARTED"
    EXECUTION_COMPLETE = "EXECUTION_COMPLETE"
    UNBLINDED = "UNBLINDED"
    FINAL_REPORT_FROZEN = "FINAL_REPORT_FROZEN"


_ORDER = tuple(ProtocolState)


def advance_protocol_state(
    current: ProtocolState,
    target: ProtocolState,
    *,
    completed_runs: int,
    planned_runs: int,
    frozen_files_unchanged: bool,
) -> ProtocolState:
    if planned_runs != 180 or not 0 <= completed_runs <= planned_runs:
        raise ValueError("execution run counts are outside the frozen protocol")
    if target is ProtocolState.UNBLINDED and current is not ProtocolState.EXECUTION_COMPLETE:
        raise ValueError("execution must be complete before unblinding")
    current_index = _ORDER.index(current)
    target_index = _ORDER.index(target)
    if target_index <= current_index:
        raise ValueError("unblinding state is irreversible")
    if target_index != current_index + 1:
        raise ValueError("protocol state transition must advance exactly one step")
    if target_index >= _ORDER.index(ProtocolState.EXECUTION_STARTED) and not frozen_files_unchanged:
        raise ValueError("frozen files changed after execution started")
    if target is ProtocolState.EXECUTION_STARTED and completed_runs != 0:
        raise ValueError("execution must start with zero completed runs")
    if target is ProtocolState.EXECUTION_COMPLETE and completed_runs != planned_runs:
        raise ValueError("execution is not complete")
    if target is ProtocolState.UNBLINDED and completed_runs != planned_runs:
        raise ValueError("execution is not complete")
    return target


def create_unblinding_record(
    path: Path,
    *,
    state: ProtocolState,
    execution_schedule: ExecutionSchedule,
    frozen_files_unchanged: bool,
    execution_report_path: Path,
    protocol_commit: str,
    freeze_manifest_sha256: str,
    execution_schedule_sha256: str,
    hidden_pack_manifest_sha256: str,
    agent_visible_pack_sha256: str,
    ground_truth_pack_sha256: str,
) -> UnblindingRecord:
    if state is not ProtocolState.EXECUTION_COMPLETE:
        raise ValueError("unblinding record requires EXECUTION_COMPLETE state")
    if not frozen_files_unchanged:
        raise ValueError("frozen files changed before unblinding")
    schedule_payload = (
        json.dumps(
            execution_schedule.model_dump(mode="json"),
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")
    if hashlib.sha256(schedule_payload).hexdigest() != execution_schedule_sha256:
        raise ValueError("execution schedule hash does not bind the verified schedule")
    execution_report = load_strict_json(
        execution_report_path, FrozenExecutionReport
    )
    report_payload = (
        json.dumps(
            execution_report.model_dump(mode="json"),
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")
    if execution_report_path.read_bytes() != report_payload:
        raise ValueError("execution report must be canonical JSON")
    if execution_report.execution_schedule_sha256 != execution_schedule_sha256:
        raise ValueError("execution report is bound to a different schedule")
    validate_complete_results(
        execution_schedule,
        tuple(item.run_id for item in execution_report.records),
    )
    execution_report_sha256 = hashlib.sha256(report_payload).hexdigest()
    record = UnblindingRecord(
        schema_version="phase5b.unblinding-record.v1",
        evaluation_version="phase5b.v1",
        protocol_commit=protocol_commit,
        freeze_manifest_sha256=freeze_manifest_sha256,
        execution_schedule_sha256=execution_schedule_sha256,
        hidden_pack_manifest_sha256=hidden_pack_manifest_sha256,
        agent_visible_pack_sha256=agent_visible_pack_sha256,
        ground_truth_pack_sha256=ground_truth_pack_sha256,
        execution_report_sha256=execution_report_sha256,
        completed_runs=180,
        from_state="EXECUTION_COMPLETE",
        to_state="UNBLINDED",
        irreversible=True,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = (
        json.dumps(record.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(encoded)
        stream.flush()
        os.fsync(stream.fileno())
    directory_descriptor = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory_descriptor)
    finally:
        os.close(directory_descriptor)
    return record


def require_new_version_after_retuning(
    evaluation_version: str,
    *,
    retuning_requested: bool,
) -> str:
    if retuning_requested and evaluation_version == "phase5b.v1":
        raise ValueError("retuning after freeze requires phase5b.v2")
    return evaluation_version
