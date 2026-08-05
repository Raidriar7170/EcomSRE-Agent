"""Crash-safe create-once journal for no-retry Phase 5B execution."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import stat
import tempfile
from typing import Any, TypeVar

from pydantic import BaseModel

from scripts.phase5b_execution.contracts import (
    EvidenceClass,
    ExecutionAttemptMarker,
    ProviderUsageRecord,
    RawScoredRunRecord,
    ScoredRunRequest,
    TerminalStatus,
    canonical_json_bytes,
    seal_raw_record,
)


ModelT = TypeVar("ModelT", bound=BaseModel)


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate checkpoint JSON key")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-finite checkpoint JSON constant: {value}")


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _entry_exists(path: Path) -> bool:
    try:
        path.lstat()
    except FileNotFoundError:
        return False
    return True


def _ensure_private_directory(path: Path) -> None:
    """Create a journal directory and reject links, foreign owners, or open modes."""

    path = Path(path)
    if not _entry_exists(path):
        missing: list[Path] = []
        current = path
        while not _entry_exists(current):
            missing.append(current)
            current = current.parent
        for item in reversed(missing):
            item.mkdir(mode=0o700)
    details = path.lstat()
    if stat.S_ISLNK(details.st_mode) or not stat.S_ISDIR(details.st_mode):
        raise ValueError("journal directory must be a real directory")
    if details.st_uid != os.geteuid():
        raise ValueError("journal directory must be owned by the executing user")
    if stat.S_IMODE(details.st_mode) & 0o077:
        raise ValueError("journal directory must not grant group/world access")


def _atomic_create(path: Path, payload: bytes) -> None:
    _ensure_private_directory(path.parent)
    if _entry_exists(path):
        raise FileExistsError(path)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb", closefd=True) as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, path)
        _fsync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def _load_canonical(path: Path, model: type[ModelT]) -> ModelT:
    details = path.lstat()
    if stat.S_ISLNK(details.st_mode) or not stat.S_ISREG(details.st_mode):
        raise ValueError("checkpoint input must be a regular non-symlink file")
    observed = path.read_bytes()
    payload = json.loads(
        observed.decode("utf-8", errors="strict"),
        object_pairs_hook=_strict_object,
        parse_constant=_reject_constant,
    )
    if not isinstance(payload, dict):
        raise ValueError("checkpoint JSON must be an object")
    value = model.model_validate_json(observed, strict=True)
    if observed != canonical_json_bytes(value.model_dump(mode="json")):
        raise ValueError("checkpoint JSON is not canonical")
    return value


class CheckpointStore:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.attempts_root = self.root / "attempts"
        self.records_root = self.root / "raw"
        _ensure_private_directory(self.root)
        _ensure_private_directory(self.attempts_root)
        _ensure_private_directory(self.records_root)

    def marker_path(self, run_id: str) -> Path:
        return self.attempts_root / f"{run_id}.json"

    def record_path(self, run_id: str) -> Path:
        return self.records_root / f"{run_id}.json"

    def start(
        self,
        request: ScoredRunRequest,
        *,
        evidence_class: EvidenceClass = "ACTUAL_SCORED",
    ) -> ExecutionAttemptMarker:
        if _entry_exists(self.record_path(request.run_id)):
            raise FileExistsError(self.record_path(request.run_id))
        marker = ExecutionAttemptMarker(
            run_id=request.run_id,
            request_sha256=request.request_sha256(),
            evidence_class=evidence_class,
            started_at_utc=datetime.now(timezone.utc),
        )
        _atomic_create(self.marker_path(request.run_id), marker.canonical_bytes())
        return marker

    def load_record(self, run_id: str) -> RawScoredRunRecord | None:
        path = self.record_path(run_id)
        if not _entry_exists(path):
            return None
        record = _load_canonical(path, RawScoredRunRecord)
        record.verify_record_sha256()
        return record

    def _validated_marker(
        self,
        request: ScoredRunRequest,
    ) -> ExecutionAttemptMarker:
        marker = _load_canonical(
            self.marker_path(request.run_id),
            ExecutionAttemptMarker,
        )
        if (
            marker.run_id != request.run_id
            or marker.request_sha256 != request.request_sha256()
        ):
            raise ValueError("attempt marker does not match the frozen run request")
        return marker

    def reconcile_completed(
        self,
        request: ScoredRunRequest,
    ) -> RawScoredRunRecord | None:
        record = self.load_record(request.run_id)
        if record is None:
            return None
        if (
            record.run_id != request.run_id
            or record.template_id != request.template_id
            or record.seed_id != request.seed_id
            or record.variant != request.variant
        ):
            raise ValueError("terminal record differs from the frozen request")
        marker_path = self.marker_path(request.run_id)
        if _entry_exists(marker_path):
            marker = self._validated_marker(request)
            if marker.evidence_class != record.evidence_class:
                raise ValueError("terminal record and attempt evidence class differ")
            marker_path.unlink()
            _fsync_directory(marker_path.parent)
        return record

    def complete(self, record: RawScoredRunRecord) -> None:
        record.verify_record_sha256()
        record_path = self.record_path(record.run_id)
        if _entry_exists(record_path):
            raise FileExistsError(record_path)
        marker_path = self.marker_path(record.run_id)
        if not _entry_exists(marker_path):
            raise ValueError("terminal record requires an open attempt marker")
        _atomic_create(record_path, record.canonical_bytes())
        marker_path.unlink()
        _fsync_directory(marker_path.parent)

    def recover_interrupted(
        self,
        request: ScoredRunRequest,
    ) -> RawScoredRunRecord | None:
        existing = self.reconcile_completed(request)
        if existing is not None:
            return existing
        marker_path = self.marker_path(request.run_id)
        if not _entry_exists(marker_path):
            return None
        marker = self._validated_marker(request)
        interrupted = seal_raw_record(
            run_id=request.run_id,
            template_id=request.template_id,
            seed_id=request.seed_id,
            variant=request.variant,
            terminal_status=TerminalStatus.PROVIDER_TRANSPORT_FAILURE,
            observed_diagnosis=None,
            usage=ProviderUsageRecord(
                model_calls=1,
                tool_calls=0,
                input_tokens=0,
                output_tokens=0,
                total_tokens=0,
                workflow_tokens=0,
                combined_tokens=0,
                provider_network_calls=(
                    1 if marker.evidence_class == "ACTUAL_SCORED" else 0
                ),
                provider_usage_known=False,
            ),
            evidence_class=marker.evidence_class,
            provider_attempted=marker.evidence_class == "ACTUAL_SCORED",
            latency_ms=0,
            latency_known=False,
            failure_code="INTERRUPTED_AFTER_ATTEMPT",
            failure_stage="HTTP_TRANSPORT",
        )
        self.complete(interrupted)
        return interrupted
