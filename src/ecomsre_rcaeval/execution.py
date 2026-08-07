"""Frozen schedule execution without evaluator-only Ground Truth access."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from pydantic import ValidationError

from ecomsre_rcaeval.artifacts import canonical_json_bytes, read_json_object
from ecomsre_rcaeval.contracts import ScheduledRun, TerminalRecord
from ecomsre_rcaeval.dataset import TelemetryCase
from ecomsre_rcaeval.runner import (
    DiagnosisProvider,
    execute_scheduled_once,
)


ProviderFactory = Callable[[ScheduledRun], DiagnosisProvider]


def _validate_identity(
    cases: tuple[TelemetryCase, ...],
    schedule: tuple[ScheduledRun, ...],
) -> dict[str, TelemetryCase]:
    case_by_id = {case.case_id: case for case in cases}
    if len(case_by_id) != len(cases):
        raise ValueError("RCAEval cases contain a duplicate identifier")
    if len(schedule) != len(cases) * 3 or len({item.run_id for item in schedule}) != len(
        schedule
    ):
        raise ValueError("RCAEval execution schedule cardinality is invalid")
    combinations = {(item.case_id, item.architecture) for item in schedule}
    if len(combinations) != len(schedule):
        raise ValueError("RCAEval execution schedule contains duplicate arms")
    if {item.case_id for item in schedule} != set(case_by_id):
        raise ValueError("RCAEval execution schedule case set is invalid")
    return case_by_id


def run_schedule(
    cases: tuple[TelemetryCase, ...],
    schedule: tuple[ScheduledRun, ...],
    provider_factory: ProviderFactory,
    journal_root: Path,
) -> tuple[TerminalRecord, ...]:
    case_by_id = _validate_identity(cases, schedule)
    records = tuple(
        execute_scheduled_once(
            scheduled,
            case_by_id[scheduled.case_id],
            provider_factory(scheduled),
            journal_root,
        )
        for scheduled in schedule
    )
    return records


def load_terminal_records(
    schedule: tuple[ScheduledRun, ...],
    journal_root: Path,
) -> tuple[TerminalRecord, ...]:
    if not journal_root.is_dir() or journal_root.is_symlink():
        raise ValueError("terminal journal root is invalid")
    expected = {f"{item.run_id}.json" for item in schedule}
    observed = {path.name for path in journal_root.iterdir()}
    if observed != expected:
        raise ValueError("terminal journal file set differs from schedule")
    records: list[TerminalRecord] = []
    for scheduled in schedule:
        path = journal_root / f"{scheduled.run_id}.json"
        if not path.is_file() or path.is_symlink():
            raise ValueError("terminal journal record must be a regular file")
        try:
            record = TerminalRecord.model_validate_json(
                canonical_json_bytes(read_json_object(path))
            )
        except (ValueError, ValidationError) as error:
            raise ValueError("terminal journal record is invalid") from error
        if (
            record.run_id != scheduled.run_id
            or record.case_id != scheduled.case_id
            or record.architecture is not scheduled.architecture
        ):
            raise ValueError("terminal journal record differs from schedule")
        records.append(record)
    return tuple(records)


def validate_attempt_markers(
    schedule: tuple[ScheduledRun, ...],
    journal_root: Path,
) -> Path:
    attempts_root = journal_root.parent / f"{journal_root.name}.attempts"
    if not attempts_root.is_dir() or attempts_root.is_symlink():
        raise ValueError("semantic attempt journal root is invalid")
    expected_names = {f"{item.run_id}.json" for item in schedule}
    if {path.name for path in attempts_root.iterdir()} != expected_names:
        raise ValueError("semantic attempt marker set differs from schedule")
    for scheduled in schedule:
        path = attempts_root / f"{scheduled.run_id}.json"
        if not path.is_file() or path.is_symlink():
            raise ValueError("semantic attempt marker must be a regular file")
        if read_json_object(path) != {
            "schema_version": "rcaeval-re2.semantic-attempt.v1",
            "run_id": scheduled.run_id,
            "case_id": scheduled.case_id,
            "architecture": scheduled.architecture.value,
            "max_semantic_attempts": 1,
        }:
            raise ValueError("semantic attempt marker differs from schedule")
    return attempts_root
