"""Capability-separated, append-safe Phase 0 evidence storage."""

from __future__ import annotations

import base64
import binascii
import fcntl
import os
import re
import secrets
import stat
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Self
from urllib.parse import unquote

from pydantic import BaseModel

from ecomsre.evidence.hashes import canonical_json_bytes, sha256_bytes
from ecomsre.evidence.models import (
    ControlEvent,
    FinalReport,
    IntegrityManifest,
    RunManifest,
    redact_command_arguments,
)
from ecomsre.phase0.models import Outcome, SmokeReport


_OBSERVER_TOP_LEVEL = {
    "changes",
    "commands",
    "cycles",
    "dependency-audit",
    "environment-manifest.json",
    "inputs",
    "lifecycle",
    "machine-manifest.json",
    "resource-ownership.json",
    "run-manifest.json",
    "telemetry",
}
_FORBIDDEN_OBSERVER_PATH_MARKERS = {
    "adfailure",
    "adservicefailure",
    "evaluator-only",
    "expected-answer",
    "expected-outcome",
    "expected-transition",
    "flag-key",
    "flag-value",
    "feature-flag",
    "ground-truth",
    "hidden",
    "ofrep",
    "physical-flag",
    "physical-truth",
    "raw-readback",
    "readback",
    "scenario-name",
    "scenario-identity",
}
_FORBIDDEN_OBSERVER_EXACT_SEMANTICS = {
    "action",
    "before-state",
    "mutation-applied",
    "mutation-state",
    "scenario",
    "target-state",
    "terminal-result",
}


@dataclass(frozen=True)
class StoredArtifact:
    path: Path
    sha256: str


class _DirectoryCapability:
    """An inode-bound evidence root used only through *at syscalls."""

    def __init__(
        self,
        base_root: Path,
        directory_name: str,
        run_id: str,
        *,
        zone: str,
        allowed_top_level: set[str],
        create: bool = True,
    ) -> None:
        self.base_root = Path(base_root)
        self.root = self.base_root / directory_name / run_id
        self.zone = zone
        self.allowed_top_level = allowed_top_level
        base_descriptor = _open_capability_base(self.base_root, create=create)
        try:
            zone_descriptor = (
                _open_or_create_directory(
                    base_descriptor,
                    directory_name,
                    zone=zone,
                )
                if create
                else _open_existing_directory(
                    base_descriptor,
                    directory_name,
                    zone=zone,
                )
            )
            try:
                self._root_descriptor = (
                    _open_or_create_directory(
                        zone_descriptor,
                        run_id,
                        zone=zone,
                    )
                    if create
                    else _open_existing_directory(
                        zone_descriptor,
                        run_id,
                        zone=zone,
                    )
                )
            finally:
                os.close(zone_descriptor)
        finally:
            os.close(base_descriptor)
        root_stat = os.fstat(self._root_descriptor)
        self._root_identity = (root_stat.st_dev, root_stat.st_ino)
        self.assert_intact()

    def close(self) -> None:
        descriptor = getattr(self, "_root_descriptor", None)
        if descriptor is None:
            return
        self._root_descriptor = None
        os.close(descriptor)

    def __enter__(self) -> Self:
        self.assert_intact()
        return self

    def __exit__(
        self,
        _exception_type: object,
        _exception: object,
        _traceback: object,
    ) -> None:
        self.close()

    def __del__(self) -> None:
        descriptor = getattr(self, "_root_descriptor", None)
        if descriptor is not None:
            try:
                self.close()
            except OSError:
                pass

    def assert_intact(self) -> None:
        descriptor = self._require_open()
        try:
            path_stat = os.lstat(self.root)
            descriptor_stat = os.fstat(descriptor)
        except OSError as error:
            raise ValueError(
                f"{self.zone} evidence capability root was replaced"
            ) from error
        if (
            not stat.S_ISDIR(path_stat.st_mode)
            or (path_stat.st_dev, path_stat.st_ino) != self._root_identity
            or (descriptor_stat.st_dev, descriptor_stat.st_ino) != self._root_identity
        ):
            raise ValueError(f"{self.zone} evidence capability root was replaced")
        _validate_owned_directory(path_stat, zone=self.zone)
        _validate_owned_directory(descriptor_stat, zone=self.zone)

    def prepare_target(self, relative_path: str) -> tuple[int, str, Path]:
        candidate = _validate_relative_path(
            relative_path,
            zone=self.zone,
            allowed_top_level=self.allowed_top_level,
        )
        self.assert_intact()
        descriptor = os.dup(self._require_open())
        try:
            for component in candidate.parts[:-1]:
                child = _open_or_create_directory(
                    descriptor,
                    component,
                    zone=self.zone,
                )
                os.close(descriptor)
                descriptor = child
        except BaseException:
            os.close(descriptor)
            raise
        self.assert_intact()
        return descriptor, candidate.name, self.root / candidate

    def _require_open(self) -> int:
        descriptor = getattr(self, "_root_descriptor", None)
        if descriptor is None:
            raise RuntimeError(f"{self.zone} evidence capability is closed")
        return descriptor


class _EvidenceStoreLifecycle:
    _capability: _DirectoryCapability

    def close(self) -> None:
        self._capability.close()

    def __enter__(self) -> Self:
        self._capability.assert_intact()
        return self

    def __exit__(
        self,
        _exception_type: object,
        _exception: object,
        _traceback: object,
    ) -> None:
        self.close()


class ObserverEvidenceStore(_EvidenceStoreLifecycle):
    """Write only observer-visible evidence under its dedicated capability."""

    def __init__(self, base_root: Path, run_id: str) -> None:
        _validate_run_id(run_id)
        self._capability = _DirectoryCapability(
            base_root,
            "observer-visible",
            run_id,
            zone="observer",
            allowed_top_level=_OBSERVER_TOP_LEVEL,
        )
        self.root = self._capability.root
        self.run_id = run_id

    @classmethod
    def open_existing(cls, base_root: Path, run_id: str) -> "ObserverEvidenceStore":
        """Open an existing observer run without creating missing authority."""
        _validate_run_id(run_id)
        instance = cls.__new__(cls)
        instance._capability = _DirectoryCapability(
            base_root,
            "observer-visible",
            run_id,
            zone="observer",
            allowed_top_level=_OBSERVER_TOP_LEVEL,
            create=False,
        )
        instance.root = instance._capability.root
        instance.run_id = run_id
        return instance

    def write_run_manifest(self, manifest: RunManifest) -> StoredArtifact:
        self._require_run_id(manifest.run_id)
        return _write_immutable(
            self._capability,
            "run-manifest.json",
            manifest,
            zone="observer",
            allowed_top_level=_OBSERVER_TOP_LEVEL,
        )

    def write_immutable(
        self,
        relative_path: str,
        value: BaseModel | dict[str, Any] | list[Any],
    ) -> StoredArtifact:
        _assert_observer_safe(value)
        return _write_immutable(
            self._capability,
            relative_path,
            value,
            zone="observer",
            allowed_top_level=_OBSERVER_TOP_LEVEL,
        )

    def append_event(
        self,
        relative_path: str,
        value: BaseModel | dict[str, Any] | list[Any],
    ) -> StoredArtifact:
        _assert_observer_safe(value)
        return _append_jsonl(
            self._capability,
            relative_path,
            value,
            zone="observer",
            allowed_top_level=_OBSERVER_TOP_LEVEL,
        )

    def _require_run_id(self, run_id: str) -> None:
        if run_id != self.run_id:
            raise ValueError("observer artifact run_id does not match capability")


class EvaluatorEvidenceStore(_EvidenceStoreLifecycle):
    """Write evaluator-only control truth without observer methods."""

    def __init__(self, base_root: Path, run_id: str) -> None:
        _validate_run_id(run_id)
        allowed_top_level = {"commands", "control-events.jsonl", "lifecycle"}
        self._capability = _DirectoryCapability(
            base_root,
            "evaluator-only",
            run_id,
            zone="evaluator",
            allowed_top_level=allowed_top_level,
        )
        self.root = self._capability.root
        self.run_id = run_id
        self._allowed_top_level = allowed_top_level

    def write_control_event(self, event: ControlEvent) -> StoredArtifact:
        if event.run_id != self.run_id:
            raise ValueError("control event run_id does not match capability")
        return _append_jsonl(
            self._capability,
            "control-events.jsonl",
            event,
            zone="evaluator",
            allowed_top_level=self._allowed_top_level,
        )

    def write_immutable(
        self,
        relative_path: str,
        value: BaseModel | dict[str, Any],
    ) -> StoredArtifact:
        return _write_immutable(
            self._capability,
            relative_path,
            value,
            zone="evaluator",
            allowed_top_level=self._allowed_top_level,
        )


class ReportEvidenceStore(_EvidenceStoreLifecycle):
    """Write terminal reports and checksums under a report-only capability."""

    def __init__(self, base_root: Path, run_id: str) -> None:
        _validate_run_id(run_id)
        self._capability = _DirectoryCapability(
            base_root,
            "reports",
            run_id,
            zone="report",
            allowed_top_level={
                "acceptance-report.json",
                "failure-report.json",
                "smoke-report.json",
                "human-summary.md",
                "minimal-terminal.json",
                "checksums.sha256",
            },
        )
        self.root = self._capability.root
        self.run_id = run_id

    def write_final_report(self, report: FinalReport) -> StoredArtifact:
        self._require_run_id(report.run_id)
        if report.overall_outcome is not Outcome.SUCCESS:
            raise ValueError("acceptance report requires SUCCESS")
        return _write_immutable(
            self._capability,
            "acceptance-report.json",
            report,
            zone="report",
            allowed_top_level={"acceptance-report.json"},
        )

    def write_failure_report(self, report: FinalReport) -> StoredArtifact:
        self._require_run_id(report.run_id)
        if report.overall_outcome is Outcome.SUCCESS:
            raise ValueError("failure report cannot contain SUCCESS")
        return _write_immutable(
            self._capability,
            "failure-report.json",
            report,
            zone="report",
            allowed_top_level={"failure-report.json"},
        )

    def write_smoke_report(self, report: SmokeReport) -> StoredArtifact:
        """Persist a non-canonical report outside formal acceptance outputs."""
        validated = SmokeReport.model_validate(report.model_dump(mode="python"))
        self._require_run_id(validated.run_id)
        return _write_immutable(
            self._capability,
            "smoke-report.json",
            validated,
            zone="report",
            allowed_top_level={"smoke-report.json"},
        )

    def write_human_summary(self, report: SmokeReport) -> StoredArtifact:
        """Write a compact report-derived summary without acceptance semantics."""
        validated = SmokeReport.model_validate(report.model_dump(mode="python"))
        self._require_run_id(validated.run_id)
        failures = (
            ", ".join(validated.failure_reason_codes)
            if validated.failure_reason_codes
            else "None"
        )
        attempt = validated.attempts[0] if validated.attempts else None
        phase_lines = (
            "".join(
                (
                    f"- `{phase.phase.value}`: attempts={phase.attempts}, "
                    f"errors={phase.errors}, error_rate={phase.error_rate:.6f}, "
                    f"passed={str(phase.passed).lower()}, "
                    f"fixture_sha256={phase.fixture_sha256 or 'MISSING'}\n"
                )
                for phase in attempt.phase_evidence
            )
            if attempt is not None
            else "- None\n"
        )
        acknowledgement_lines = (
            "".join(
                (
                    f"- `{ack.stage}/{ack.phase.value if ack.phase else 'none'}`: "
                    f"succeeded={str(ack.transition_succeeded).lower()}, "
                    f"duration={ack.acknowledgement_duration_seconds:.3f}s, "
                    f"reason={ack.reason_code}\n"
                )
                for ack in attempt.control_acknowledgements
            )
            if attempt is not None
            else "- None\n"
        )
        telemetry_lines = "".join(
            f"- `{name}` freshness gate: `{str(decision).lower()}`\n"
            for name, decision in sorted(validated.telemetry_gate_decisions.items())
        )
        content = (
            "# Phase 0 diagnostic smoke\n\n"
            f"- Run ID: `{validated.run_id}`\n"
            f"- Diagnostic status: `{validated.diagnostic_status.value}`\n"
            "- Canonical acceptance: `false`\n"
            "- Phase 0 complete: `false`\n"
            f"- Safe stop confirmed: `{str(validated.safe_stop_completed).lower()}`\n"
            f"- Failure reasons: {failures}\n"
            "\n## Phase measurements\n\n"
            f"{phase_lines}"
            "\n## Control acknowledgements\n\n"
            f"{acknowledgement_lines}"
            "\n## Backend freshness decisions\n\n"
            f"{telemetry_lines}"
        ).encode("utf-8")
        return _write_immutable_bytes(
            self._capability,
            "human-summary.md",
            content,
            zone="report",
            allowed_top_level={"human-summary.md"},
        )

    def write_minimal_terminal(
        self,
        *,
        run_id: str,
        reason_code: str,
        environment_started: bool,
        reset_attempted: bool,
        stop_attempted: bool,
    ) -> StoredArtifact:
        """Last-resort immutable truth when full report finalization fails."""
        self._require_run_id(run_id)
        return _write_immutable(
            self._capability,
            "minimal-terminal.json",
            {
                "schema_version": "phase0.smoke-minimal-terminal.v1",
                "run_id": run_id,
                "canonical": False,
                "phase0_complete": False,
                "reason_code": reason_code,
                "environment_started": environment_started,
                "reset_attempted": reset_attempted,
                "stop_attempted": stop_attempted,
            },
            zone="report",
            allowed_top_level={"minimal-terminal.json"},
        )

    def write_checksums(self, manifest: IntegrityManifest) -> StoredArtifact:
        validated = IntegrityManifest.model_validate(manifest.model_dump(mode="python"))
        self._require_run_id(validated.run_id)
        content = "".join(
            f"{digest}  {relative_path}\n"
            for relative_path, digest in sorted(validated.content_hashes.items())
        ).encode("utf-8")
        return _write_immutable_bytes(
            self._capability,
            "checksums.sha256",
            content,
            zone="report",
            allowed_top_level={"checksums.sha256"},
        )

    def _require_run_id(self, run_id: str) -> None:
        if run_id != self.run_id:
            raise ValueError("report run_id does not match capability")


def redact_command(arguments: tuple[str, ...]) -> tuple[str, ...]:
    """Redact common credential-bearing flags and environment assignments."""
    return redact_command_arguments(arguments)


def _write_immutable(
    capability: _DirectoryCapability,
    relative_path: str,
    value: BaseModel | dict[str, Any] | list[Any],
    *,
    zone: str,
    allowed_top_level: set[str],
) -> StoredArtifact:
    return _write_immutable_bytes(
        capability,
        relative_path,
        canonical_json_bytes(_json_value(value)),
        zone=zone,
        allowed_top_level=allowed_top_level,
    )


def _write_immutable_bytes(
    capability: _DirectoryCapability,
    relative_path: str,
    content: bytes,
    *,
    zone: str,
    allowed_top_level: set[str],
) -> StoredArtifact:
    parent_descriptor, target_name, target = capability.prepare_target(relative_path)
    temporary_name = f".{secrets.token_hex(16)}.tmp"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    temporary_descriptor: int | None = None
    try:
        temporary_descriptor = os.open(
            temporary_name,
            flags,
            0o600,
            dir_fd=parent_descriptor,
        )
        _write_all(temporary_descriptor, content)
        os.fsync(temporary_descriptor)
        os.link(
            temporary_name,
            target_name,
            src_dir_fd=parent_descriptor,
            dst_dir_fd=parent_descriptor,
            follow_symlinks=False,
        )
        os.close(temporary_descriptor)
        temporary_descriptor = None
        os.unlink(temporary_name, dir_fd=parent_descriptor)
        _validate_regular_file_at(
            parent_descriptor,
            target_name,
            zone=zone,
        )
        os.fsync(parent_descriptor)
        capability.assert_intact()
    finally:
        if temporary_descriptor is not None:
            os.close(temporary_descriptor)
            try:
                os.unlink(temporary_name, dir_fd=parent_descriptor)
            except FileNotFoundError:
                pass
        os.close(parent_descriptor)

    return StoredArtifact(path=target, sha256=sha256_bytes(content))


def _append_jsonl(
    capability: _DirectoryCapability,
    relative_path: str,
    value: BaseModel | dict[str, Any],
    *,
    zone: str,
    allowed_top_level: set[str],
) -> StoredArtifact:
    content = canonical_json_bytes(_json_value(value)) + b"\n"
    parent_descriptor, target_name, target = capability.prepare_target(relative_path)
    try:
        descriptor, created = _open_jsonl_target(
            parent_descriptor,
            target_name,
        )
    except OSError as error:
        os.close(parent_descriptor)
        raise ValueError(f"{zone} JSONL target is not a safe regular file") from error
    locked = False
    try:
        _validate_jsonl_descriptor(descriptor, zone=zone)
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        locked = True
        _validate_jsonl_descriptor(descriptor, zone=zone)
        if created:
            os.fsync(parent_descriptor)
        valid_size = _recover_jsonl_tail(descriptor)
        try:
            os.lseek(descriptor, valid_size, os.SEEK_SET)
            _write_all(descriptor, content)
            os.fsync(descriptor)
        except BaseException:
            os.ftruncate(descriptor, valid_size)
            os.fsync(descriptor)
            raise
        capability.assert_intact()
    finally:
        try:
            if locked:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)
            os.close(parent_descriptor)
    return StoredArtifact(path=target, sha256=sha256_bytes(content[:-1]))


def _open_jsonl_target(
    parent_descriptor: int,
    target_name: str,
) -> tuple[int, bool]:
    base_flags = os.O_RDWR
    if hasattr(os, "O_NOFOLLOW"):
        base_flags |= os.O_NOFOLLOW
    if hasattr(os, "O_NONBLOCK"):
        base_flags |= os.O_NONBLOCK
    try:
        return (
            os.open(
                target_name,
                base_flags | os.O_CREAT | os.O_EXCL,
                0o600,
                dir_fd=parent_descriptor,
            ),
            True,
        )
    except FileExistsError:
        return (
            os.open(
                target_name,
                base_flags,
                dir_fd=parent_descriptor,
            ),
            False,
        )


def _write_all(descriptor: int, content: bytes) -> None:
    remaining = memoryview(content)
    while remaining:
        written = os.write(descriptor, remaining)
        if written <= 0:
            raise OSError("short JSONL write made no progress")
        remaining = remaining[written:]


def _validate_relative_path(
    relative_path: str,
    *,
    zone: str,
    allowed_top_level: set[str],
) -> Path:
    candidate = Path(relative_path)
    normalized_paths = _semantic_variants(relative_path)
    if (
        candidate.is_absolute()
        or ".." in candidate.parts
        or not candidate.parts
        or candidate.parts[0] not in allowed_top_level
        or (
            zone == "observer"
            and any(
                marker in normalized_path
                for normalized_path in normalized_paths
                for marker in _FORBIDDEN_OBSERVER_PATH_MARKERS
            )
        )
        or any(
            ".." in Path(normalized_path).parts for normalized_path in normalized_paths
        )
    ):
        raise ValueError(f"{zone} evidence path is outside its capability")

    return candidate


def _assert_observer_safe(
    value: BaseModel | dict[str, Any] | list[Any],
) -> None:
    payload: Any = (
        value.model_dump(mode="json") if isinstance(value, BaseModel) else value
    )

    def inspect(item: Any) -> None:
        if isinstance(item, dict):
            for key, nested in item.items():
                if _contains_forbidden_semantics(str(key)):
                    raise ValueError(f"observer semantic leakage in key {key!r}")
                inspect(nested)
        elif isinstance(item, (list, tuple)):
            for nested in item:
                inspect(nested)
        elif isinstance(item, str):
            if _contains_forbidden_semantics(item):
                raise ValueError("observer semantic leakage in artifact value")

    inspect(payload)


def _contains_forbidden_semantics(value: str) -> bool:
    variants = _semantic_variants(value)
    return bool(variants & _FORBIDDEN_OBSERVER_EXACT_SEMANTICS) or any(
        marker in variant
        for variant in variants
        for marker in _FORBIDDEN_OBSERVER_PATH_MARKERS
    )


def _semantic_variants(value: str) -> set[str]:
    pending = [value]
    decoded: set[str] = set()
    while pending and len(decoded) < 8:
        candidate = pending.pop()
        if candidate in decoded:
            continue
        decoded.add(candidate)

        url_decoded = unquote(candidate)
        if url_decoded != candidate:
            pending.append(url_decoded)

        compact = candidate.strip()
        if (
            len(compact) >= 8
            and re.fullmatch(r"[A-Za-z0-9_+/=-]+", compact) is not None
        ):
            padded = compact + "=" * (-len(compact) % 4)
            try:
                raw = base64.b64decode(
                    padded,
                    altchars=b"-_",
                    validate=True,
                )
                base64_decoded = raw.decode("utf-8")
            except (binascii.Error, UnicodeDecodeError):
                pass
            else:
                if base64_decoded.isprintable():
                    pending.append(base64_decoded)

    return {
        unicodedata.normalize("NFKC", candidate)
        .replace("\\", "/")
        .replace("_", "-")
        .casefold()
        for candidate in decoded
    }


def _json_value(value: BaseModel | dict[str, Any]) -> dict[str, Any]:
    if isinstance(value, BaseModel):
        validated = type(value).model_validate(value.model_dump(mode="python"))
        return validated.model_dump(mode="json")
    return value


def _validate_run_id(run_id: str) -> None:
    if re.fullmatch(r"[0-9a-f]{32}", run_id) is None:
        raise ValueError("run_id is not an opaque path-safe identifier")


def _open_capability_base(base_root: Path, *, create: bool = True) -> int:
    try:
        if create:
            base_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        path_stat = os.lstat(base_root)
    except OSError as error:
        raise ValueError("evidence capability root is unavailable") from error
    if not stat.S_ISDIR(path_stat.st_mode):
        raise ValueError("evidence capability root must be a real directory")
    _validate_owned_directory(path_stat, zone="evidence")
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(base_root, flags)
    except OSError as error:
        raise ValueError("evidence capability root must not be a symlink") from error
    descriptor_stat = os.fstat(descriptor)
    if (descriptor_stat.st_dev, descriptor_stat.st_ino) != (
        path_stat.st_dev,
        path_stat.st_ino,
    ):
        os.close(descriptor)
        raise ValueError("evidence capability root changed during validation")
    return descriptor


def _open_existing_directory(
    parent_descriptor: int,
    name: str,
    *,
    zone: str,
) -> int:
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(name, flags, dir_fd=parent_descriptor)
    except OSError as error:
        raise ValueError(
            f"{zone} evidence capability directory is unavailable"
        ) from error
    try:
        _validate_owned_directory(os.fstat(descriptor), zone=zone)
    except BaseException:
        os.close(descriptor)
        raise
    return descriptor


def _open_or_create_directory(
    parent_descriptor: int,
    name: str,
    *,
    zone: str,
) -> int:
    try:
        os.mkdir(name, mode=0o700, dir_fd=parent_descriptor)
    except FileExistsError:
        pass
    except OSError as error:
        raise ValueError(
            f"{zone} evidence path cannot create a capability directory"
        ) from error
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(name, flags, dir_fd=parent_descriptor)
    except OSError as error:
        raise ValueError(f"{zone} evidence path escapes its capability root") from error
    try:
        _validate_owned_directory(os.fstat(descriptor), zone=zone)
    except BaseException:
        os.close(descriptor)
        raise
    return descriptor


def _validate_owned_directory(metadata: os.stat_result, *, zone: str) -> None:
    if not stat.S_ISDIR(metadata.st_mode):
        raise ValueError(f"{zone} evidence capability root is not a directory")
    if metadata.st_uid != os.getuid():
        raise ValueError(f"{zone} evidence capability root has another owner")
    if stat.S_IMODE(metadata.st_mode) & 0o022:
        raise ValueError(f"{zone} evidence capability root is group/other writable")


def _validate_regular_file_at(
    parent_descriptor: int,
    target_name: str,
    *,
    zone: str,
) -> None:
    metadata = os.stat(
        target_name,
        dir_fd=parent_descriptor,
        follow_symlinks=False,
    )
    _validate_file_metadata(metadata, zone=zone)


def _validate_jsonl_descriptor(descriptor: int, *, zone: str) -> None:
    _validate_file_metadata(os.fstat(descriptor), zone=zone)


def _validate_file_metadata(metadata: os.stat_result, *, zone: str) -> None:
    if not stat.S_ISREG(metadata.st_mode):
        raise ValueError(f"{zone} JSONL target must be a regular file")
    if metadata.st_uid != os.getuid():
        raise ValueError(f"{zone} JSONL target has another owner")
    if metadata.st_nlink != 1:
        raise ValueError(f"{zone} JSONL target has unsafe link count")
    if stat.S_IMODE(metadata.st_mode) & 0o077:
        raise ValueError(f"{zone} JSONL target permissions are too broad")


def _recover_jsonl_tail(descriptor: int) -> int:
    size = os.fstat(descriptor).st_size
    if size == 0 or os.pread(descriptor, 1, size - 1) == b"\n":
        return size
    cursor = size
    valid_size = 0
    while cursor > 0:
        chunk_size = min(cursor, 64 * 1024)
        cursor -= chunk_size
        chunk = os.pread(descriptor, chunk_size, cursor)
        newline_index = chunk.rfind(b"\n")
        if newline_index >= 0:
            valid_size = cursor + newline_index + 1
            break
    os.ftruncate(descriptor, valid_size)
    os.fsync(descriptor)
    return valid_size
