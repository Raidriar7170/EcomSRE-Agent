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
    OperationStage,
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
_PROVIDER_STAGE_ORDER = (
    OperationStage.INPUT_SANITIZATION,
    OperationStage.INPUT_CONSTRUCTION,
    OperationStage.INPUT_PERSISTENCE,
    OperationStage.PROVIDER_CALL,
    OperationStage.OUTPUT_VALIDATION,
    OperationStage.OUTPUT_PERSISTENCE,
)
_DETERMINISTIC_STAGE_ORDER = (
    OperationStage.INPUT_SANITIZATION,
    OperationStage.INPUT_CONSTRUCTION,
    OperationStage.INPUT_PERSISTENCE,
    OperationStage.OUTPUT_VALIDATION,
    OperationStage.OUTPUT_PERSISTENCE,
)


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


def _stage_trace_digest(
    entries: list[tuple[int, OperationStage, str]],
) -> str:
    payload = [
        {
            "stage_index": index,
            "stage": stage.value,
            "marker_sha256": marker_sha,
        }
        for index, stage, marker_sha in entries
    ]
    encoded = json.dumps(
        payload,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return _sha256_bytes(encoded)


def write_private_snapshot_create_once(
    run_root: Path, name: str, snapshot: V2Model
) -> str:
    """Write one typed snapshot without embedding its local path in JSON."""

    if not name or any(
        character not in "abcdefghijklmnopqrstuvwxyz0123456789-_" for character in name
    ):
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


class OperationTransaction:
    """Append-only stage transaction created after the operation attempt marker."""

    def __init__(
        self,
        journal: RunJournalV2,
        operation_index: int,
        operation_type: OperationType,
    ) -> None:
        self._journal = journal
        self.operation_index = operation_index
        self.operation_type = operation_type
        self._stage_entries: list[tuple[int, OperationStage, str]] = []

    @property
    def _stem(self) -> str:
        return f"{self.operation_index:04d}-{self.operation_type.value}"

    @property
    def _allowed_order(self) -> tuple[OperationStage, ...]:
        if self.operation_type is OperationType.INDICATOR_RESOLVER:
            return _DETERMINISTIC_STAGE_ORDER
        return _PROVIDER_STAGE_ORDER

    def start_stage(self, stage: OperationStage) -> str:
        if not isinstance(stage, OperationStage) or stage is OperationStage.COMPLETED:
            raise ValueError("operation stage must be a non-terminal typed stage")
        stage_index = len(self._stage_entries) + 1
        expected = self._allowed_order[len(self._stage_entries)]
        if stage is not expected:
            raise ValueError("operation stages must follow the fixed order")
        payload = {
            "schema_version": "rcaeval-re2-v2-dev1.operation-stage.v1",
            "run_id": self._journal.run_id,
            "case_id": self._journal.case_id,
            "system": self._journal.system,
            "architecture": self._journal.architecture,
            "operation_index": self.operation_index,
            "operation_type": self.operation_type.value,
            "stage_index": stage_index,
            "stage": stage.value,
        }
        path = (
            self._journal.run_root
            / "operation-stages"
            / f"{self._stem}-{stage_index:02d}-{stage.value}.json"
        )
        marker_sha = _durable_create(path, _canonical_bytes(payload))
        self._stage_entries.append((stage_index, stage, marker_sha))
        return marker_sha

    @property
    def current_stage(self) -> OperationStage | None:
        return None if not self._stage_entries else self._stage_entries[-1][1]

    @property
    def last_completed_stage(self) -> OperationStage | None:
        if len(self._stage_entries) < 2:
            return None
        return self._stage_entries[-2][1]

    def stage_trace_sha256(self) -> str:
        return _stage_trace_digest(self._stage_entries)

    def mark_completed(self, operation_record_sha256: str) -> str:
        stage_index = len(self._stage_entries) + 1
        trace_sha = self.stage_trace_sha256()
        payload = {
            "schema_version": "rcaeval-re2-v2-dev1.operation-stage.v1",
            "run_id": self._journal.run_id,
            "case_id": self._journal.case_id,
            "system": self._journal.system,
            "architecture": self._journal.architecture,
            "operation_index": self.operation_index,
            "operation_type": self.operation_type.value,
            "stage_index": stage_index,
            "stage": OperationStage.COMPLETED.value,
            "stage_trace_sha256": trace_sha,
            "operation_record_sha256": operation_record_sha256,
        }
        path = (
            self._journal.run_root
            / "operation-stages"
            / f"{self._stem}-{stage_index:02d}-{OperationStage.COMPLETED.value}.json"
        )
        return _durable_create(path, _canonical_bytes(payload))


def _load_stage_markers(
    journal: RunJournalV2,
    operation_index: int,
    operation_type: OperationType,
) -> tuple[
    list[tuple[int, OperationStage, str]],
    tuple[Path, dict[str, object]] | None,
]:
    stem = f"{operation_index:04d}-{operation_type.value}"
    root = journal.run_root / "operation-stages"
    paths = () if not root.exists() else tuple(sorted(root.glob(f"{stem}-*.json")))
    allowed = (
        _DETERMINISTIC_STAGE_ORDER
        if operation_type is OperationType.INDICATOR_RESOLVER
        else _PROVIDER_STAGE_ORDER
    )
    entries: list[tuple[int, OperationStage, str]] = []
    completion: tuple[Path, dict[str, object]] | None = None
    for expected_index, path in enumerate(paths, 1):
        if path.is_symlink() or not path.is_file():
            raise ValueError("operation stage marker is invalid")
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            stage = OperationStage(payload["stage"])
            stage_index = payload["stage_index"]
        except (
            OSError,
            UnicodeError,
            json.JSONDecodeError,
            KeyError,
            TypeError,
            ValueError,
        ) as error:
            raise ValueError("operation stage marker is invalid") from error
        if (
            type(stage_index) is not int
            or stage_index != expected_index
            or payload.get("schema_version") != "rcaeval-re2-v2-dev1.operation-stage.v1"
            or payload.get("run_id") != journal.run_id
            or payload.get("case_id") != journal.case_id
            or payload.get("system") != journal.system
            or payload.get("architecture") != journal.architecture
            or payload.get("operation_index") != operation_index
            or payload.get("operation_type") != operation_type.value
            or path.name != f"{stem}-{stage_index:02d}-{stage.value}.json"
        ):
            raise ValueError("operation stage marker differs from run journal")
        if stage is OperationStage.COMPLETED:
            if completion is not None or expected_index != len(paths):
                raise ValueError("operation completion marker is not terminal")
            completion = (path, payload)
            continue
        if completion is not None or stage is not allowed[len(entries)]:
            raise ValueError("operation stage markers do not follow fixed order")
        entries.append((stage_index, stage, _sha256_file(path)))
    return entries, completion


def _completion_marker_sha256(
    run_root: Path, operation_index: int, operation_type: OperationType
) -> str:
    stem = f"{operation_index:04d}-{operation_type.value}"
    matches = tuple(
        sorted((run_root / "operation-stages").glob(f"{stem}-*-COMPLETED.json"))
    )
    if len(matches) != 1:
        raise ValueError("operation completion marker is missing or ambiguous")
    return _sha256_file(matches[0])


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
        callback: Callable[[OperationTransaction], RecordT],
    ) -> RecordT:
        expected_index = len(self._records) + 1
        if type(operation_index) is not int or operation_index != expected_index:
            raise ValueError("operation indices must be contiguous")
        stem = f"{operation_index:04d}-{operation_type.value}"
        marker = {
            "schema_version": "rcaeval-re2-v2.operation-attempt.v1",
            "run_id": self.run_id,
            "case_id": self.case_id,
            "system": self.system,
            "architecture": self.architecture,
            "operation_index": operation_index,
            "operation_type": operation_type.value,
            "max_semantic_attempts": 1,
        }
        _durable_create(
            self.run_root / "operation-attempts" / f"{stem}.json",
            _canonical_bytes(marker),
        )
        transaction = OperationTransaction(self, operation_index, operation_type)
        record = callback(transaction)
        if (
            record.run_id != self.run_id
            or record.case_id != self.case_id
            or record.system != self.system
            or record.architecture != self.architecture
            or record.operation_index != operation_index
            or record.operation_type is not operation_type
            or record.stage_trace_sha256 != transaction.stage_trace_sha256()
        ):
            raise ValueError("operation record differs from run journal")
        operation_sha = _write_model(
            self.run_root / "operations" / f"{stem}.json", record
        )
        transaction.mark_completed(operation_sha)
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
                stage_trace_sha256=record.stage_trace_sha256,
                completion_marker_sha256=_completion_marker_sha256(
                    self.run_root, record.operation_index, record.operation_type
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
        usage_known = all(item.usage_delta.token_usage_known for item in self._records)
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
                sum(item.usage_delta.completion_tokens_delta for item in self._records)
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
            failure_stage=disposition.failure_stage,
            diagnosis=disposition.diagnosis,
            tool_calls=disposition.tool_calls,
            run_trace_sha256=trace_sha,
            operation_tree_sha256=tree_sha,
            usage=usage,
            started_at_utc=self.started_at_utc,
            ended_at_utc=now,
            latency_ms=float(
                max(0.0, (now - self.started_at_utc).total_seconds() * 1_000)
            ),
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
    digests = tuple(
        OperationDigestV2(
            operation_index=record.operation_index,
            operation_type=record.operation_type,
            operation_sha256=_sha256_file(
                journal.run_root
                / "operations"
                / f"{record.operation_index:04d}-{record.operation_type.value}.json"
            ),
            stage_trace_sha256=record.stage_trace_sha256,
            completion_marker_sha256=_completion_marker_sha256(
                journal.run_root, record.operation_index, record.operation_type
            ),
        )
        for record in journal._records
    )
    expected_tree_sha256 = operation_tree_sha256(digests)
    if (
        terminal.run_id != journal.run_id
        or terminal.case_id != journal.case_id
        or terminal.system != journal.system
        or terminal.architecture != journal.architecture
        or trace.run_id != journal.run_id
        or trace.case_id != journal.case_id
        or trace.system != journal.system
        or trace.architecture != journal.architecture
        or trace.operation_count != len(digests)
        or trace.operations != digests
        or trace.operation_tree_sha256 != expected_tree_sha256
        or terminal.run_trace_sha256 != _sha256_file(trace_path)
        or terminal.operation_tree_sha256 != expected_tree_sha256
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
            or path.name != f"{expected_index:04d}-{record.operation_type.value}.json"
        ):
            raise ValueError("v2 operation journal entry differs from requested run")
        stage_entries, completion = _load_stage_markers(
            journal, expected_index, record.operation_type
        )
        if completion is None:
            raise ValueError("completed operation has no completion marker")
        completion_path, completion_payload = completion
        if (
            record.stage_trace_sha256 != _stage_trace_digest(stage_entries)
            or completion_payload.get("stage_trace_sha256") != record.stage_trace_sha256
            or completion_payload.get("operation_record_sha256") != _sha256_file(path)
            or _sha256_file(completion_path)
            != _completion_marker_sha256(
                journal.run_root, expected_index, record.operation_type
            )
        ):
            raise ValueError("operation stage trace differs from operation record")
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
            or payload.get("schema_version") != "rcaeval-re2-v2.operation-attempt.v1"
            or payload.get("run_id") != journal.run_id
            or payload.get("case_id") != journal.case_id
            or payload.get("system") != journal.system
            or payload.get("architecture") != journal.architecture
            or payload.get("max_semantic_attempts") != 1
            or marker_path.name != f"{marker_index:04d}-{marker_type.value}.json"
        ):
            raise ValueError("operation attempt marker differs from run journal")
        parsed_markers.append((marker_index, marker_type))
    if len(parsed_markers) not in {
        len(journal._records),
        len(journal._records) + 1,
    }:
        raise ValueError("operation attempts and completed records are inconsistent")
    if any(
        parsed_markers[index - 1] != (record.operation_index, record.operation_type)
        for index, record in enumerate(journal._records, 1)
    ):
        raise ValueError("operation attempt differs from completed record")
    failure_type: OperationType | None = None
    failure_index: int | None = None
    if len(parsed_markers) == len(journal._records) + 1:
        failure_index, failure_type = parsed_markers[-1]
    failure_stage = OperationStage.INPUT_SANITIZATION
    if failure_index is not None and failure_type is not None:
        stage_entries, completion = _load_stage_markers(
            journal, failure_index, failure_type
        )
        if completion is not None:
            raise ValueError("orphan operation unexpectedly has a completion marker")
        if stage_entries:
            failure_stage = stage_entries[-1][1]
    return TerminalDispositionV2(
        terminal_status=OperationStatus.PROTOCOL_VIOLATION,
        failure_operation_type=failure_type,
        failure_operation_index=failure_index,
        failure_code=OperationFailureCode.STARTED_OPERATION_WITHOUT_TERMINAL,
        failure_stage=failure_stage,
        diagnosis=None,
        tool_calls=max(
            (len(record.investigated_sources) for record in journal._records),
            default=0,
        ),
    )


def verify_terminal_run_journal(
    run_root: Path,
) -> tuple[TerminalRecordV2, tuple[OperationRecord, ...]]:
    """Read and verify one terminal v2 journal without creating artifacts."""

    terminal_path = run_root / "terminal-record.json"
    try:
        terminal = TerminalRecordV2.model_validate_json(
            terminal_path.read_text(encoding="utf-8")
        )
    except (OSError, UnicodeError, ValidationError) as error:
        raise ValueError("v2 terminal journal is invalid") from error
    journal = RunJournalV2(
        run_root,
        run_id=terminal.run_id,
        case_id=terminal.case_id,
        system=terminal.system,
        architecture=terminal.architecture,
        started_at_utc=terminal.started_at_utc,
    )
    _validate_run_attempt(journal)
    records = _load_existing_operations(journal)
    journal._records = records
    _orphan_disposition(journal)
    verified = _load_terminal(journal)
    if verified != terminal:
        raise ValueError("v2 terminal journal changed during verification")
    return terminal, tuple(records)


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
        _validate_run_attempt(journal)
        journal._records = _load_existing_operations(journal)
        _orphan_disposition(journal)
        return _load_terminal(journal)
    if journal.attempt_path.exists():
        journal.started_at_utc = _validate_run_attempt(journal)
        journal._records = _load_existing_operations(journal)
        return journal.terminalize(_orphan_disposition(journal))
    journal.begin()
    disposition = callback(journal)
    return journal.terminalize(disposition)
