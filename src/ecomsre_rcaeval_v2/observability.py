"""Durable create-once operation journals with no-retry recovery."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from typing import TypeVar

from pydantic import TypeAdapter, ValidationError

from ecomsre_rcaeval_v2.contracts import (
    ArchitectureV2,
    CaseId,
    DevSystem,
    OperationDigestV2,
    OperationFailureCode,
    OperationRecord,
    OperationStatus,
    OperationType,
    ProviderUsageDelta,
    RunId,
    RunTraceV2,
    TerminalDispositionV2,
    TerminalRecordV2,
    V2Model,
    operation_tree_sha256,
)


RecordT = TypeVar("RecordT", bound=OperationRecord)
_OPERATION_ADAPTER: TypeAdapter[OperationRecord] = TypeAdapter(OperationRecord)


def _canonical_bytes(value: object) -> bytes:
    return (
        json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _ensure_private_directory(path: Path) -> None:
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.chmod(0o700)


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _durable_create(path: Path, payload: bytes) -> str:
    _ensure_private_directory(path.parent)
    with path.open("xb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    path.chmod(0o600)
    _fsync_directory(path.parent)
    return _sha256_bytes(payload)


def _write_model(path: Path, value: V2Model) -> str:
    return _durable_create(path, _canonical_bytes(value.model_dump(mode="json")))


def write_private_snapshot_create_once(
    run_root: Path, name: str, snapshot: V2Model
) -> str:
    """Write one typed snapshot without embedding its local path in JSON."""

    if not name or any(character not in "abcdefghijklmnopqrstuvwxyz0123456789-_" for character in name):
        raise ValueError("snapshot name is invalid")
    _ensure_private_directory(run_root)
    return _write_model(run_root / "snapshots" / f"{name}.json", snapshot)


def compute_operation_tree_sha256(entries: tuple[object, ...]) -> str:
    validated = tuple(
        item
        if isinstance(item, OperationDigestV2)
        else OperationDigestV2.model_validate_json(
            json.dumps(item, allow_nan=False, ensure_ascii=False)
        )
        for item in entries
    )
    return operation_tree_sha256(validated)


class RunJournalV2:
    def __init__(
        self,
        run_root: Path,
        *,
        run_id: RunId,
        case_id: CaseId,
        system: DevSystem,
        architecture: ArchitectureV2,
        started_at_utc: datetime,
    ) -> None:
        self.run_root = run_root
        self.run_id = run_id
        self.case_id = case_id
        self.system = system
        self.architecture = architecture
        self.started_at_utc = started_at_utc
        self._records: list[OperationRecord] = []

    @property
    def attempt_path(self) -> Path:
        return self.run_root / "run-attempt.json"

    @property
    def terminal_path(self) -> Path:
        return self.run_root / "terminal-record.json"

    def begin(self) -> None:
        _ensure_private_directory(self.run_root)
        _durable_create(self.attempt_path, _canonical_bytes(self.attempt_payload()))

    def attempt_payload(self) -> dict[str, object]:
        return {
            "schema_version": "rcaeval-re2-v2.run-attempt.v1",
            "run_id": self.run_id,
            "case_id": self.case_id,
            "system": self.system,
            "architecture": self.architecture,
            "max_semantic_attempts": 1,
            "started_at_utc": self.started_at_utc.isoformat(),
        }

    def record_operation(
        self,
        operation_index: int,
        operation_type: OperationType,
        callback: Callable[[], RecordT],
    ) -> RecordT:
        expected_index = len(self._records) + 1
        if type(operation_index) is not int or operation_index != expected_index:
            raise ValueError("operation indices must be contiguous")
        stem = f"{operation_index:04d}-{operation_type.value}"
        marker = {
            "schema_version": "rcaeval-re2-v2.operation-attempt.v1",
            "run_id": self.run_id,
            "case_id": self.case_id,
            "operation_index": operation_index,
            "operation_type": operation_type.value,
            "max_semantic_attempts": 1,
        }
        _durable_create(
            self.run_root / "operation-attempts" / f"{stem}.json",
            _canonical_bytes(marker),
        )
        record = callback()
        if (
            record.run_id != self.run_id
            or record.case_id != self.case_id
            or record.system != self.system
            or record.architecture != self.architecture
            or record.operation_index != operation_index
            or record.operation_type is not operation_type
        ):
            raise ValueError("operation record differs from run journal")
        _write_model(self.run_root / "operations" / f"{stem}.json", record)
        self._records.append(record)
        return record

    def terminalize(self, disposition: TerminalDispositionV2) -> TerminalRecordV2:
        now = datetime.now(timezone.utc)
        digests = tuple(
            OperationDigestV2(
                operation_index=record.operation_index,
                operation_type=record.operation_type,
                operation_sha256=_sha256_file(
                    self.run_root
                    / "operations"
                    / f"{record.operation_index:04d}-{record.operation_type.value}.json"
                ),
            )
            for record in self._records
        )
        tree_sha = operation_tree_sha256(digests)
        trace = RunTraceV2(
            schema_version="rcaeval-re2-v2.run-trace.v1",
            run_id=self.run_id,
            case_id=self.case_id,
            system=self.system,
            architecture=self.architecture,
            operation_count=len(digests),
            operations=digests,
            operation_tree_sha256=tree_sha,
            created_at_utc=now,
        )
        trace_path = self.run_root / "run-trace.json"
        if trace_path.exists():
            try:
                existing_trace = RunTraceV2.model_validate_json(
                    trace_path.read_text(encoding="utf-8")
                )
            except (OSError, UnicodeError, ValidationError) as error:
                raise ValueError("existing v2 run trace is invalid") from error
            expected_trace = trace.model_copy(
                update={"created_at_utc": existing_trace.created_at_utc}
            )
            if existing_trace != expected_trace:
                raise ValueError("existing v2 run trace differs from operation journal")
            trace_sha = _sha256_file(trace_path)
        else:
            trace_sha = _write_model(trace_path, trace)
        usage_known = all(
            item.usage_delta.token_usage_known for item in self._records
        )
        usage = ProviderUsageDelta(
            model_calls_delta=sum(
                item.usage_delta.model_calls_delta for item in self._records
            ),
            prompt_tokens_delta=(
                sum(item.usage_delta.prompt_tokens_delta for item in self._records)
                if usage_known
                else 0
            ),
            completion_tokens_delta=(
                sum(
                    item.usage_delta.completion_tokens_delta
                    for item in self._records
                )
                if usage_known
                else 0
            ),
            total_tokens_delta=(
                sum(item.usage_delta.total_tokens_delta for item in self._records)
                if usage_known
                else 0
            ),
            token_usage_known=usage_known,
        )
        terminal = TerminalRecordV2(
            schema_version="rcaeval-re2-v2.terminal-record.v1",
            run_id=self.run_id,
            case_id=self.case_id,
            system=self.system,
            architecture=self.architecture,
            terminal_status=disposition.terminal_status,
            failure_operation_type=disposition.failure_operation_type,
            failure_operation_index=disposition.failure_operation_index,
            failure_code=disposition.failure_code,
            diagnosis=disposition.diagnosis,
            tool_calls=disposition.tool_calls,
            run_trace_sha256=trace_sha,
            operation_tree_sha256=tree_sha,
            usage=usage,
            started_at_utc=self.started_at_utc,
            ended_at_utc=now,
            latency_ms=float(max(0.0, (now - self.started_at_utc).total_seconds() * 1_000)),
        )
        _write_model(self.terminal_path, terminal)
        return terminal


def _load_terminal(journal: RunJournalV2) -> TerminalRecordV2:
    try:
        terminal = TerminalRecordV2.model_validate_json(
            journal.terminal_path.read_text(encoding="utf-8")
        )
        trace_path = journal.run_root / "run-trace.json"
        trace = RunTraceV2.model_validate_json(trace_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValidationError) as error:
        raise ValueError("v2 terminal journal is invalid") from error
    if (
        terminal.run_id != journal.run_id
        or terminal.case_id != journal.case_id
        or terminal.system != journal.system
        or terminal.architecture != journal.architecture
        or terminal.run_trace_sha256 != _sha256_file(trace_path)
        or terminal.operation_tree_sha256 != trace.operation_tree_sha256
    ):
        raise ValueError("v2 terminal journal differs from requested run")
    return terminal


def _load_existing_operations(journal: RunJournalV2) -> list[OperationRecord]:
    operations_root = journal.run_root / "operations"
    if not operations_root.exists():
        return []
    if operations_root.is_symlink() or not operations_root.is_dir():
        raise ValueError("v2 operation journal directory is invalid")
    records: list[OperationRecord] = []
    for expected_index, path in enumerate(sorted(operations_root.glob("*.json")), 1):
        if path.is_symlink() or not path.is_file():
            raise ValueError("v2 operation journal entry is invalid")
        try:
            record = _OPERATION_ADAPTER.validate_json(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValidationError) as error:
            raise ValueError("v2 operation journal entry is invalid") from error
        if (
            record.run_id != journal.run_id
            or record.case_id != journal.case_id
            or record.system != journal.system
            or record.architecture != journal.architecture
            or record.operation_index != expected_index
            or path.name
            != f"{expected_index:04d}-{record.operation_type.value}.json"
        ):
            raise ValueError("v2 operation journal entry differs from requested run")
        records.append(record)
    return records


def _validate_run_attempt(journal: RunJournalV2) -> datetime:
    if journal.attempt_path.is_symlink() or not journal.attempt_path.is_file():
        raise ValueError("v2 run attempt marker is invalid")
    try:
        payload = json.loads(journal.attempt_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("v2 run attempt marker is invalid") from error
    expected = journal.attempt_payload()
    started_at = payload.get("started_at_utc")
    if not isinstance(started_at, str):
        raise ValueError("v2 run attempt marker has no start time")
    try:
        parsed_start = datetime.fromisoformat(started_at)
    except ValueError as error:
        raise ValueError("v2 run attempt marker has an invalid start time") from error
    if parsed_start.tzinfo is None or parsed_start.utcoffset() is None:
        raise ValueError("v2 run attempt marker start time is not timezone aware")
    expected["started_at_utc"] = started_at
    if payload != expected:
        raise ValueError("v2 run attempt marker differs from requested run")
    return parsed_start


def _orphan_disposition(journal: RunJournalV2) -> TerminalDispositionV2:
    attempts_root = journal.run_root / "operation-attempts"
    markers = (
        ()
        if not attempts_root.exists()
        else tuple(sorted(attempts_root.glob("*.json")))
    )
    parsed_markers: list[tuple[int, OperationType]] = []
    for expected_index, marker_path in enumerate(markers, 1):
        if marker_path.is_symlink() or not marker_path.is_file():
            raise ValueError("operation attempt marker is invalid")
        try:
            payload = json.loads(marker_path.read_text(encoding="utf-8"))
            marker_type = OperationType(payload["operation_type"])
            marker_index = payload["operation_index"]
        except (
            OSError,
            UnicodeError,
            json.JSONDecodeError,
            KeyError,
            TypeError,
            ValueError,
        ) as error:
            raise ValueError("operation attempt marker is invalid") from error
        if (
            type(marker_index) is not int
            or marker_index != expected_index
            or payload.get("schema_version")
            != "rcaeval-re2-v2.operation-attempt.v1"
            or payload.get("run_id") != journal.run_id
            or payload.get("case_id") != journal.case_id
            or payload.get("max_semantic_attempts") != 1
            or marker_path.name
            != f"{marker_index:04d}-{marker_type.value}.json"
        ):
            raise ValueError("operation attempt marker differs from run journal")
        parsed_markers.append((marker_index, marker_type))
    if len(parsed_markers) not in {
        len(journal._records),
        len(journal._records) + 1,
    }:
        raise ValueError("operation attempts and completed records are inconsistent")
    if any(
        parsed_markers[index - 1]
        != (record.operation_index, record.operation_type)
        for index, record in enumerate(journal._records, 1)
    ):
        raise ValueError("operation attempt differs from completed record")
    failure_type: OperationType | None = None
    failure_index: int | None = None
    if len(parsed_markers) == len(journal._records) + 1:
        failure_index, failure_type = parsed_markers[-1]
    return TerminalDispositionV2(
        terminal_status=OperationStatus.PROTOCOL_VIOLATION,
        failure_operation_type=failure_type,
        failure_operation_index=failure_index,
        failure_code=OperationFailureCode.STARTED_ATTEMPT_WITHOUT_TERMINAL,
        diagnosis=None,
        tool_calls=max(
            (len(record.investigated_sources) for record in journal._records),
            default=0,
        ),
    )


def execute_run_once(
    run_root: Path,
    *,
    run_id: RunId,
    case_id: CaseId,
    system: DevSystem,
    architecture: ArchitectureV2,
    started_at_utc: datetime,
    callback: Callable[[RunJournalV2], TerminalDispositionV2],
) -> TerminalRecordV2:
    journal = RunJournalV2(
        run_root,
        run_id=run_id,
        case_id=case_id,
        system=system,
        architecture=architecture,
        started_at_utc=started_at_utc,
    )
    if journal.terminal_path.exists():
        return _load_terminal(journal)
    if journal.attempt_path.exists():
        journal.started_at_utc = _validate_run_attempt(journal)
        journal._records = _load_existing_operations(journal)
        return journal.terminalize(_orphan_disposition(journal))
    journal.begin()
    disposition = callback(journal)
    return journal.terminalize(disposition)
