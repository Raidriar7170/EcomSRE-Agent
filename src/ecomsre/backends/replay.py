"""Strict, bounded, in-memory replay backend."""

from __future__ import annotations

import json
import math
import os
import re
import stat
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Literal, TypeVar

from pydantic import (
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from ecomsre.backends.live_protocol import (
    MAX_BACKEND_OBSERVATIONS,
    BackendObservation,
    BackendStatus,
    ChangesObservationBatch,
    LogsObservationBatch,
    MetricsObservationBatch,
    TracesObservationBatch,
    _ObservationBatch,
)
from ecomsre.evidence.hashes import sha256_bytes
from ecomsre.phase1.contracts import Incident, Phase1Model
from ecomsre.phase1.validator import revalidate_phase1_model
from ecomsre.tools.changes import ChangesQuery
from ecomsre.tools.logs import LogsQuery
from ecomsre.tools.metrics import MetricsQuery
from ecomsre.tools.traces import TracesQuery

MAX_REPLAY_FILE_BYTES = 1024 * 1024
MAX_REPLAY_JSON_DEPTH = 128
MAX_REPLAY_CASE_ID_LENGTH = 64
_CASE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_DATA_FILENAMES = (
    "incident.json",
    "metrics.json",
    "logs.json",
    "traces.json",
    "changes.json",
)
_EXPECTED_ENTRIES = frozenset(("manifest.json", *_DATA_FILENAMES))
_POSIX_CAPABILITY_ERRORS = (OSError, NotImplementedError, TypeError)


class ReplayLoadErrorCode(str, Enum):
    INVALID_ROOT = "INVALID_ROOT"
    INVALID_CASE_ID = "INVALID_CASE_ID"
    INVALID_CASE_DIRECTORY = "INVALID_CASE_DIRECTORY"
    INVALID_ENTRY_SET = "INVALID_ENTRY_SET"
    INVALID_FILE = "INVALID_FILE"
    FILE_TOO_LARGE = "FILE_TOO_LARGE"
    INVALID_JSON = "INVALID_JSON"
    INVALID_SCHEMA = "INVALID_SCHEMA"
    HASH_MISMATCH = "HASH_MISMATCH"


class ReplayLoadError(ValueError):
    """Typed fail-closed replay loading error."""

    def __init__(self, code: ReplayLoadErrorCode, detail: str) -> None:
        self.code = code
        super().__init__(f"{code.value}: {detail}")


class _ReplayManifest(Phase1Model):
    schema_version: Literal["phase1.replay-manifest.v1"]
    case_id: str = Field(
        min_length=1,
        max_length=MAX_REPLAY_CASE_ID_LENGTH,
        pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$",
    )
    files: dict[str, str]

    @field_validator("files")
    @classmethod
    def require_exact_hashed_files(
        cls,
        files: dict[str, str],
    ) -> dict[str, str]:
        if set(files) != set(_DATA_FILENAMES):
            raise ValueError("manifest files mapping is not exact")
        if any(_SHA256_RE.fullmatch(value) is None for value in files.values()):
            raise ValueError("manifest contains an invalid SHA-256")
        return dict(files)


class _ReplayObservationDocument(Phase1Model):
    schema_version: Literal["phase1.replay-observations.v1"]
    status: BackendStatus
    observations: tuple[BackendObservation, ...] = Field(
        max_length=MAX_BACKEND_OBSERVATIONS,
    )

    @model_validator(mode="after")
    def require_status_observation_consistency(
        self,
    ) -> _ReplayObservationDocument:
        if self.status != BackendStatus.AVAILABLE and self.observations:
            raise ValueError(
                "unavailable or timed-out source must have no observations"
            )
        return self


class ReplayCase(Phase1Model):
    """Fully loaded immutable replay case with no retained path."""

    case_id: str = Field(
        min_length=1,
        max_length=MAX_REPLAY_CASE_ID_LENGTH,
        pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$",
    )
    incident: Incident
    metrics: MetricsObservationBatch
    logs: LogsObservationBatch
    traces: TracesObservationBatch
    changes: ChangesObservationBatch


@dataclass(frozen=True, slots=True)
class _FileSnapshot:
    content: bytes
    sha256: str


def _raise_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON constant: {value}")


def _strict_object(
    pairs: list[tuple[str, Any]],
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _parse_strict_json(snapshot: _FileSnapshot, filename: str) -> Any:
    try:
        text = snapshot.content.decode("utf-8", errors="strict")
        parsed = json.loads(
            text,
            object_pairs_hook=_strict_object,
            parse_constant=_raise_constant,
        )
        stack = [(parsed, 1)]
        while stack:
            value, depth = stack.pop()
            if depth > MAX_REPLAY_JSON_DEPTH:
                raise ValueError("JSON nesting exceeds the replay limit")
            if isinstance(value, list):
                stack.extend((item, depth + 1) for item in value)
            elif isinstance(value, dict):
                stack.extend((item, depth + 1) for item in value.values())
        return parsed
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
        RecursionError,
        ValueError,
    ) as error:
        raise ReplayLoadError(
            ReplayLoadErrorCode.INVALID_JSON,
            f"{filename} is not strict UTF-8 JSON",
        ) from error


def _stat_signature(
    file_stat: os.stat_result,
) -> tuple[int, int, int, int, int]:
    return (
        file_stat.st_dev,
        file_stat.st_ino,
        file_stat.st_size,
        file_stat.st_mtime_ns,
        file_stat.st_ctime_ns,
    )


_StatSignature = tuple[int, int, int, int, int]


def _read_file_snapshot(
    filename: str,
    *,
    case_fd: int,
    expected_signature: _StatSignature,
) -> _FileSnapshot:
    nofollow = getattr(os, "O_NOFOLLOW", None)
    if not isinstance(nofollow, int):
        raise ReplayLoadError(
            ReplayLoadErrorCode.INVALID_FILE,
            "platform lacks required O_NOFOLLOW capability",
        )
    flags = os.O_RDONLY | nofollow
    close_on_exec = getattr(os, "O_CLOEXEC", None)
    if isinstance(close_on_exec, int):
        flags |= close_on_exec
    try:
        file_descriptor = os.open(
            filename,
            flags,
            dir_fd=case_fd,
        )
    except _POSIX_CAPABILITY_ERRORS as error:
        raise ReplayLoadError(
            ReplayLoadErrorCode.INVALID_FILE,
            f"{filename} cannot be opened as a regular file",
        ) from error

    try:
        try:
            before = os.fstat(file_descriptor)
            if not stat.S_ISREG(before.st_mode):
                raise ReplayLoadError(
                    ReplayLoadErrorCode.INVALID_FILE,
                    f"{filename} is not a regular file",
                )
            if _stat_signature(before) != expected_signature:
                raise ReplayLoadError(
                    ReplayLoadErrorCode.INVALID_FILE,
                    f"{filename} was replaced before it was opened",
                )
            if before.st_size > MAX_REPLAY_FILE_BYTES:
                raise ReplayLoadError(
                    ReplayLoadErrorCode.FILE_TOO_LARGE,
                    f"{filename} exceeds the replay file limit",
                )

            chunks: list[bytes] = []
            bytes_remaining = MAX_REPLAY_FILE_BYTES + 1
            while bytes_remaining > 0:
                chunk = os.read(
                    file_descriptor,
                    min(64 * 1024, bytes_remaining),
                )
                if not chunk:
                    break
                chunks.append(chunk)
                bytes_remaining -= len(chunk)
            content = b"".join(chunks)
            if len(content) > MAX_REPLAY_FILE_BYTES:
                raise ReplayLoadError(
                    ReplayLoadErrorCode.FILE_TOO_LARGE,
                    f"{filename} exceeds the replay file limit",
                )
            after = os.fstat(file_descriptor)
            if _stat_signature(before) != _stat_signature(after):
                raise ReplayLoadError(
                    ReplayLoadErrorCode.INVALID_FILE,
                    f"{filename} changed while it was read",
                )
        except ReplayLoadError:
            raise
        except _POSIX_CAPABILITY_ERRORS as error:
            raise ReplayLoadError(
                ReplayLoadErrorCode.INVALID_FILE,
                f"{filename} access capability is unsupported",
            ) from error
    finally:
        os.close(file_descriptor)

    try:
        path_after = os.stat(
            filename,
            dir_fd=case_fd,
            follow_symlinks=False,
        )
    except _POSIX_CAPABILITY_ERRORS as error:
        raise ReplayLoadError(
            ReplayLoadErrorCode.INVALID_FILE,
            f"{filename} was replaced while it was read",
        ) from error
    if (
        stat.S_ISLNK(path_after.st_mode)
        or not stat.S_ISREG(path_after.st_mode)
        or _stat_signature(path_after) != _stat_signature(after)
    ):
        raise ReplayLoadError(
            ReplayLoadErrorCode.INVALID_FILE,
            f"{filename} was replaced while it was read",
        )
    return _FileSnapshot(content=content, sha256=sha256_bytes(content))


def _directory_open_flags(code: ReplayLoadErrorCode) -> int:
    nofollow = getattr(os, "O_NOFOLLOW", None)
    directory = getattr(os, "O_DIRECTORY", None)
    if not isinstance(nofollow, int) or not isinstance(directory, int):
        raise ReplayLoadError(
            code,
            "platform lacks required safe directory capabilities",
        )
    flags = os.O_RDONLY | nofollow | directory
    close_on_exec = getattr(os, "O_CLOEXEC", None)
    if isinstance(close_on_exec, int):
        flags |= close_on_exec
    return flags


def _open_directory(
    path_or_name: Path | str,
    *,
    code: ReplayLoadErrorCode,
    parent_fd: int | None = None,
) -> int:
    flags = _directory_open_flags(code)
    try:
        if parent_fd is None:
            directory_fd = os.open(path_or_name, flags)
        else:
            directory_fd = os.open(
                path_or_name,
                flags,
                dir_fd=parent_fd,
            )
    except _POSIX_CAPABILITY_ERRORS as error:
        raise ReplayLoadError(
            code,
            f"{Path(path_or_name).name or path_or_name} is not a safe directory",
        ) from error
    try:
        opened = os.fstat(directory_fd)
        if not stat.S_ISDIR(opened.st_mode):
            raise ReplayLoadError(
                code,
                f"{Path(path_or_name).name or path_or_name} is not a directory",
            )
    except ReplayLoadError:
        os.close(directory_fd)
        raise
    except _POSIX_CAPABILITY_ERRORS as error:
        os.close(directory_fd)
        raise ReplayLoadError(
            code,
            f"{Path(path_or_name).name or path_or_name} cannot be inspected",
        ) from error
    return directory_fd


def _require_regular_file(
    filename: Path | str,
    *,
    case_fd: int,
) -> _StatSignature:
    bounded_filename = Path(filename).name
    if bounded_filename != str(filename):
        raise ReplayLoadError(
            ReplayLoadErrorCode.INVALID_FILE,
            "replay file name must not contain a path",
        )
    try:
        file_stat = os.stat(
            bounded_filename,
            dir_fd=case_fd,
            follow_symlinks=False,
        )
    except _POSIX_CAPABILITY_ERRORS as error:
        raise ReplayLoadError(
            ReplayLoadErrorCode.INVALID_FILE,
            f"{bounded_filename} does not exist",
        ) from error
    if stat.S_ISLNK(file_stat.st_mode) or not stat.S_ISREG(file_stat.st_mode):
        raise ReplayLoadError(
            ReplayLoadErrorCode.INVALID_FILE,
            f"{bounded_filename} is not a regular non-symlink file",
        )
    if file_stat.st_size > MAX_REPLAY_FILE_BYTES:
        raise ReplayLoadError(
            ReplayLoadErrorCode.FILE_TOO_LARGE,
            f"{bounded_filename} exceeds the replay file limit",
        )
    return _stat_signature(file_stat)


def _read_case_from_directory(
    case_fd: int,
    case_id: str,
) -> ReplayCase:
    try:
        entries = set(os.listdir(case_fd))
    except _POSIX_CAPABILITY_ERRORS as error:
        raise ReplayLoadError(
            ReplayLoadErrorCode.INVALID_CASE_DIRECTORY,
            "case directory cannot be listed",
        ) from error
    if entries != _EXPECTED_ENTRIES:
        raise ReplayLoadError(
            ReplayLoadErrorCode.INVALID_ENTRY_SET,
            "case directory must contain exactly the six replay files",
        )
    expected_signatures = {
        filename: _require_regular_file(
            filename,
            case_fd=case_fd,
        )
        for filename in _EXPECTED_ENTRIES
    }

    try:
        manifest_snapshot = _read_file_snapshot(
            "manifest.json",
            case_fd=case_fd,
            expected_signature=expected_signatures["manifest.json"],
        )
        manifest = _ReplayManifest.model_validate(
            _parse_strict_json(manifest_snapshot, "manifest.json")
        )
    except ValidationError as error:
        raise ReplayLoadError(
            ReplayLoadErrorCode.INVALID_SCHEMA,
            "manifest schema is invalid",
        ) from error
    if manifest.case_id != case_id:
        raise ReplayLoadError(
            ReplayLoadErrorCode.INVALID_SCHEMA,
            "manifest case_id does not match requested case",
        )

    parsed: dict[str, Any] = {}
    verified_hashes: dict[str, str] = {}
    for filename in _DATA_FILENAMES:
        snapshot = _read_file_snapshot(
            filename,
            case_fd=case_fd,
            expected_signature=expected_signatures[filename],
        )
        if snapshot.sha256 != manifest.files[filename]:
            raise ReplayLoadError(
                ReplayLoadErrorCode.HASH_MISMATCH,
                f"{filename} does not match the manifest SHA-256",
            )
        parsed[filename] = _parse_strict_json(snapshot, filename)
        verified_hashes[filename] = snapshot.sha256

    try:
        incident = Incident.model_validate(parsed["incident.json"])
        documents = {
            source: _ReplayObservationDocument.model_validate(parsed[f"{source}.json"])
            for source in ("metrics", "logs", "traces", "changes")
        }
        return ReplayCase(
            case_id=case_id,
            incident=incident,
            metrics=MetricsObservationBatch(
                status=documents["metrics"].status,
                observations=documents["metrics"].observations,
                raw_artifact_indices=tuple(
                    range(len(documents["metrics"].observations))
                ),
                raw_artifact_sha256=verified_hashes["metrics.json"],
            ),
            logs=LogsObservationBatch(
                status=documents["logs"].status,
                observations=documents["logs"].observations,
                raw_artifact_indices=tuple(
                    range(len(documents["logs"].observations))
                ),
                raw_artifact_sha256=verified_hashes["logs.json"],
            ),
            traces=TracesObservationBatch(
                status=documents["traces"].status,
                observations=documents["traces"].observations,
                raw_artifact_indices=tuple(
                    range(len(documents["traces"].observations))
                ),
                raw_artifact_sha256=verified_hashes["traces.json"],
            ),
            changes=ChangesObservationBatch(
                status=documents["changes"].status,
                observations=documents["changes"].observations,
                raw_artifact_indices=tuple(
                    range(len(documents["changes"].observations))
                ),
                raw_artifact_sha256=verified_hashes["changes.json"],
            ),
        )
    except ValidationError as error:
        raise ReplayLoadError(
            ReplayLoadErrorCode.INVALID_SCHEMA,
            "incident or observation schema is invalid",
        ) from error


def _load_replay_case(allowed_root: Path, case_id: str) -> ReplayCase:
    if not isinstance(case_id, str) or _CASE_ID_RE.fullmatch(case_id) is None:
        raise ReplayLoadError(
            ReplayLoadErrorCode.INVALID_CASE_ID,
            "case_id must match the bounded identifier grammar",
        )
    root_fd = _open_directory(
        allowed_root,
        code=ReplayLoadErrorCode.INVALID_ROOT,
    )
    try:
        case_fd = _open_directory(
            case_id,
            code=ReplayLoadErrorCode.INVALID_CASE_DIRECTORY,
            parent_fd=root_fd,
        )
        try:
            return _read_case_from_directory(case_fd, case_id)
        finally:
            os.close(case_fd)
    finally:
        os.close(root_fd)


def load_replay_case(allowed_root: Path, case_id: str) -> ReplayCase:
    """Load and verify one bounded case beneath an allowed replay root."""

    try:
        root = Path(allowed_root)
    except TypeError as error:
        raise ReplayLoadError(
            ReplayLoadErrorCode.INVALID_ROOT,
            "allowed replay root must be path-like",
        ) from error
    try:
        return _load_replay_case(root, case_id)
    except ReplayLoadError:
        raise
    except _POSIX_CAPABILITY_ERRORS as error:
        raise ReplayLoadError(
            ReplayLoadErrorCode.INVALID_FILE,
            "replay file access failed closed",
        ) from error


BatchT = TypeVar("BatchT", bound=_ObservationBatch)


def _require_timeout(timeout_seconds: float) -> None:
    if (
        isinstance(timeout_seconds, bool)
        or not isinstance(timeout_seconds, (int, float))
        or not math.isfinite(timeout_seconds)
        or timeout_seconds <= 0
    ):
        raise ValueError("timeout_seconds must be finite and positive")


def _filter_batch(
    batch: BatchT,
    query: MetricsQuery | LogsQuery | TracesQuery | ChangesQuery,
    batch_type: type[BatchT],
) -> BatchT:
    if batch.status != BackendStatus.AVAILABLE:
        return revalidate_phase1_model(batch, batch_type)
    selected = tuple(
        (raw_artifact_index, observation)
        for raw_artifact_index, observation in zip(
            batch.raw_artifact_indices,
            batch.observations,
            strict=True,
        )
        if observation.started_at <= query.ended_at
        and observation.ended_at >= query.started_at
        and (
            query.service is None
            or observation.service == query.service
        )
    )
    filtered = batch_type(
        status=batch.status,
        observations=tuple(observation for _, observation in selected),
        raw_artifact_indices=tuple(
            raw_artifact_index
            for raw_artifact_index, _ in selected
        ),
        raw_artifact_filename=batch.raw_artifact_filename,
        raw_artifact_sha256=batch.raw_artifact_sha256,
    )
    return revalidate_phase1_model(filtered, batch_type)


class ReplayObservabilityBackend:
    """Filesystem-free backend over one already verified replay case."""

    def __init__(self, replay_case: ReplayCase) -> None:
        self._case = revalidate_phase1_model(replay_case, ReplayCase)

    def query_metrics(
        self,
        query: MetricsQuery,
        *,
        timeout_seconds: float,
    ) -> MetricsObservationBatch:
        _require_timeout(timeout_seconds)
        validated_query = revalidate_phase1_model(query, MetricsQuery)
        return _filter_batch(
            self._case.metrics,
            validated_query,
            MetricsObservationBatch,
        )

    def search_logs(
        self,
        query: LogsQuery,
        *,
        timeout_seconds: float,
    ) -> LogsObservationBatch:
        _require_timeout(timeout_seconds)
        validated_query = revalidate_phase1_model(query, LogsQuery)
        return _filter_batch(
            self._case.logs,
            validated_query,
            LogsObservationBatch,
        )

    def search_traces(
        self,
        query: TracesQuery,
        *,
        timeout_seconds: float,
    ) -> TracesObservationBatch:
        _require_timeout(timeout_seconds)
        validated_query = revalidate_phase1_model(query, TracesQuery)
        return _filter_batch(
            self._case.traces,
            validated_query,
            TracesObservationBatch,
        )

    def list_changes(
        self,
        query: ChangesQuery,
        *,
        timeout_seconds: float,
    ) -> ChangesObservationBatch:
        _require_timeout(timeout_seconds)
        validated_query = revalidate_phase1_model(query, ChangesQuery)
        return _filter_batch(
            self._case.changes,
            validated_query,
            ChangesObservationBatch,
        )
