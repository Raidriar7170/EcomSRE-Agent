"""Frozen upstream and image-lock contracts for Phase 0."""

from __future__ import annotations

import json
import os
import re
import secrets
import stat
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ecomsre.evidence.hashes import sha256_bytes
from ecomsre.phase0.models import Outcome


UPSTREAM_TAG = "3.0.0"
UPSTREAM_COMMIT = "1755859a9de82c2e5e225be68abc401a5ebf2b4f"
EXPECTED_ARCHITECTURE = "arm64"
EXPECTED_PLATFORM = "linux/arm64"
SHA256_PATTERN = r"^[0-9a-f]{64}$"
DIGEST_PATTERN = r"^sha256:[0-9a-f]{64}$"


class ImageLockStatus(str, Enum):
    UNINITIALIZED = "UNINITIALIZED"
    LOCKED = "LOCKED"


class ImageLockSourceSetChanged(ValueError):
    """Lightweight rotation cannot change the frozen source inventory."""


class ResolvedComposeConfig(BaseModel):
    """Exact, interpolated Compose JSON and its derived image inventory."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    stdout: str = Field(min_length=1)
    sha256: str = Field(pattern=SHA256_PATTERN)
    image_references: tuple[str, ...] = Field(min_length=1)
    service_image_mapping: tuple[tuple[str, str], ...] = Field(min_length=1)

    @classmethod
    def from_stdout(cls, stdout: str) -> "ResolvedComposeConfig":
        mapping = _compose_service_image_mapping(stdout)
        return cls(
            stdout=stdout,
            sha256=sha256_bytes(stdout.encode("utf-8")),
            image_references=tuple(sorted({image for _service, image in mapping})),
            service_image_mapping=mapping,
        )

    @model_validator(mode="after")
    def bind_inventory_to_exact_stdout(self) -> "ResolvedComposeConfig":
        if self.sha256 != sha256_bytes(self.stdout.encode("utf-8")):
            raise ValueError("resolved Compose hash does not match stdout")
        mapping = _compose_service_image_mapping(self.stdout)
        if self.service_image_mapping != mapping:
            raise ValueError("resolved Compose service image mapping differs")
        if self.image_references != tuple(
            sorted({image for _service, image in mapping})
        ):
            raise ValueError("resolved Compose image inventory does not match stdout")
        return self


class InspectedImage(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    logical_name: str = Field(min_length=1)
    source_reference: str = Field(min_length=1)
    image_index_digest: str = Field(pattern=DIGEST_PATTERN)
    resolved_platform_digest: str = Field(pattern=DIGEST_PATTERN)
    architecture: str
    platform: str
    image_id: str = Field(pattern=DIGEST_PATTERN)

    @field_validator("source_reference")
    @classmethod
    def require_frozen_source_reference(cls, value: str) -> str:
        if "@sha256:" in value:
            digest = value.rsplit("@", 1)[1]
            if re.fullmatch(DIGEST_PATTERN, digest) is not None:
                return value
            raise ValueError("source reference digest is not frozen")

        final_component = value.rsplit("/", 1)[-1]
        if ":" not in final_component:
            raise ValueError("source reference must use a frozen version tag")
        tag = final_component.split(":", 1)[1]
        if (
            not tag
            or tag.lower() in {"latest", "main", "edge", "nightly", "dev"}
            or not any(character.isdigit() for character in tag)
        ):
            raise ValueError("source reference must use a frozen version tag")
        return value


class ImageLockEntry(InspectedImage):
    acquired_at: datetime
    upstream_commit: str
    compose_config_sha256: str = Field(pattern=SHA256_PATTERN)

    @field_validator("acquired_at")
    @classmethod
    def require_utc_acquisition(cls, value: datetime) -> datetime:
        if value.utcoffset() is None or value.utcoffset().total_seconds() != 0:
            raise ValueError("image acquisition timestamp must be UTC")
        return value


class ImageLockManifest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["phase0.image-lock.v1"]
    status: ImageLockStatus
    upstream_tag: str
    upstream_commit: str
    compose_config_sha256: str | None
    created_at: datetime | None
    allowed_source_references: tuple[str, ...]
    images: tuple[ImageLockEntry, ...]

    @model_validator(mode="after")
    def validate_lock_state(self) -> "ImageLockManifest":
        if self.upstream_tag != UPSTREAM_TAG or self.upstream_commit != UPSTREAM_COMMIT:
            raise ValueError("image lock does not match the frozen upstream")
        if self.status is ImageLockStatus.UNINITIALIZED:
            if (
                self.images
                or self.allowed_source_references
                or self.compose_config_sha256 is not None
            ):
                raise ValueError(
                    "uninitialized image lock cannot contain image metadata"
                )
            if self.created_at is not None:
                raise ValueError(
                    "uninitialized image lock cannot have acquisition time"
                )
            return self

        if not self.images or self.created_at is None:
            raise ValueError("locked image manifest requires inspected images")
        if (
            self.created_at.utcoffset() is None
            or self.created_at.utcoffset().total_seconds() != 0
        ):
            raise ValueError("image lock creation timestamp must be UTC")
        if self.compose_config_sha256 is None or len(self.compose_config_sha256) != 64:
            raise ValueError("locked image manifest requires Compose content hash")
        logical_names = [image.logical_name for image in self.images]
        if len(set(logical_names)) != len(logical_names):
            raise ValueError("image lock contains duplicate logical images")
        if len(set(self.allowed_source_references)) != len(
            self.allowed_source_references
        ):
            raise ValueError("image lock contains duplicate source references")
        if {image.source_reference for image in self.images} != set(
            self.allowed_source_references
        ):
            raise ValueError(
                "image lock sources do not match resolved Compose references"
            )
        for image in self.images:
            if (
                image.architecture != EXPECTED_ARCHITECTURE
                or image.platform != EXPECTED_PLATFORM
            ):
                raise ValueError("candidate image lock must be native linux/arm64")
            if image.upstream_commit != self.upstream_commit:
                raise ValueError("image entry upstream commit mismatch")
            if image.compose_config_sha256 != self.compose_config_sha256:
                raise ValueError("image entry Compose hash mismatch")
            if image.acquired_at != self.created_at:
                raise ValueError(
                    "image acquisition timestamp differs from lock creation"
                )
        return self


class LockMatchChecks(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    source_references: bool
    digests: bool
    platforms: bool
    image_ids: bool
    upstream_binding: bool
    compose_binding: bool
    complete_inventory: bool

    @property
    def all_matched(self) -> bool:
        return all(
            (
                self.source_references,
                self.digests,
                self.platforms,
                self.image_ids,
                self.upstream_binding,
                self.compose_binding,
                self.complete_inventory,
            )
        )

    @classmethod
    def all_passed(cls) -> "LockMatchChecks":
        return cls(
            source_references=True,
            digests=True,
            platforms=True,
            image_ids=True,
            upstream_binding=True,
            compose_binding=True,
            complete_inventory=True,
        )


class LockVerification(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    passed: bool
    outcome: Outcome
    reason_codes: tuple[str, ...]
    checks: LockMatchChecks

    @model_validator(mode="after")
    def require_consistent_result(self) -> "LockVerification":
        if not self.is_consistent():
            state = "successful" if self.passed else "failed"
            raise ValueError(f"{state} lock verification is inconsistent")
        return self

    def is_consistent(self) -> bool:
        if self.passed:
            return (
                self.outcome is Outcome.SUCCESS
                and not self.reason_codes
                and self.checks.all_matched
            )
        return (
            self.outcome is not Outcome.SUCCESS
            and bool(self.reason_codes)
            and not self.checks.all_matched
        )


class ImageLockRotationEvidence(BaseModel):
    """Machine-readable compare-and-swap evidence for one live rotation."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["phase0.image-lock-rotation.v1"]
    rotation_reason: Literal["COMPOSE_OVERRIDE_CHANGED"]
    old_compose_config_sha256: str = Field(pattern=SHA256_PATTERN)
    new_compose_config_sha256: str = Field(pattern=SHA256_PATTERN)
    old_lock_sha256: str = Field(pattern=SHA256_PATTERN)
    new_lock_sha256: str = Field(pattern=SHA256_PATTERN)
    source_references_unchanged: Literal[True] = True
    cached_images_reverified: Literal[True] = True


class ImageLockRotationResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    lock: ImageLockManifest
    verification: LockVerification
    evidence: ImageLockRotationEvidence


def generate_candidate_image_lock(
    *,
    images: tuple[InspectedImage, ...],
    resolved_compose: ResolvedComposeConfig,
    acquired_at: datetime,
) -> ImageLockManifest:
    """Generate a bootstrap candidate strictly from inspected local metadata."""
    logical_names = [image.logical_name for image in images]
    if len(set(logical_names)) != len(logical_names):
        raise ValueError("candidate image lock contains duplicate logical images")
    if len(set(resolved_compose.image_references)) != len(
        resolved_compose.image_references
    ) or {image.source_reference for image in images} != set(
        resolved_compose.image_references
    ):
        raise ValueError(
            "candidate image sources do not match resolved Compose references"
        )
    entries = tuple(
        ImageLockEntry(
            **image.model_dump(),
            acquired_at=acquired_at,
            upstream_commit=UPSTREAM_COMMIT,
            compose_config_sha256=resolved_compose.sha256,
        )
        for image in images
    )
    return ImageLockManifest(
        schema_version="phase0.image-lock.v1",
        status=ImageLockStatus.LOCKED,
        upstream_tag=UPSTREAM_TAG,
        upstream_commit=UPSTREAM_COMMIT,
        compose_config_sha256=resolved_compose.sha256,
        created_at=acquired_at,
        allowed_source_references=resolved_compose.image_references,
        images=entries,
    )


def verify_acceptance_image_lock(
    lock: ImageLockManifest,
    *,
    cached_images: tuple[InspectedImage, ...],
    observed_upstream_commit: str,
    observed_compose_config_sha256: str,
) -> LockVerification:
    """Verify, without modifying, the lock against cached image metadata."""
    if lock.status is ImageLockStatus.UNINITIALIZED:
        return LockVerification(
            passed=False,
            outcome=Outcome.BLOCKED_UPSTREAM,
            reason_codes=("INPUT_NOT_FROZEN",),
            checks=LockMatchChecks(
                source_references=False,
                digests=False,
                platforms=False,
                image_ids=False,
                upstream_binding=False,
                compose_binding=False,
                complete_inventory=False,
            ),
        )

    reasons: list[str] = []
    cached_names = [image.logical_name for image in cached_images]
    no_duplicate_cached_names = len(set(cached_names)) == len(cached_names)
    if not no_duplicate_cached_names:
        reasons.append("DUPLICATE_CACHED_IMAGE")
    expected_names = {image.logical_name for image in lock.images}
    complete_inventory = (
        no_duplicate_cached_names and set(cached_names) == expected_names
    )
    if no_duplicate_cached_names and set(cached_names) != expected_names:
        reasons.append("CACHED_IMAGE_SET_MISMATCH")
    upstream_binding = observed_upstream_commit == lock.upstream_commit
    if not upstream_binding:
        reasons.append("INPUT_NOT_FROZEN")
    compose_binding = observed_compose_config_sha256 == lock.compose_config_sha256
    if not compose_binding:
        reasons.append("COMPOSE_CONFIG_HASH_MISMATCH")

    cached_by_name = {image.logical_name: image for image in cached_images}
    source_references = complete_inventory
    digests = complete_inventory
    platforms = complete_inventory
    image_ids = complete_inventory
    for expected in lock.images:
        actual = cached_by_name.get(expected.logical_name)
        if actual is None:
            reasons.append("CACHED_IMAGE_MISSING")
            source_references = False
            digests = False
            platforms = False
            image_ids = False
            continue
        if actual.source_reference != expected.source_reference:
            reasons.append("SOURCE_REFERENCE_MISMATCH")
            source_references = False
        if actual.image_index_digest != expected.image_index_digest:
            reasons.append("IMAGE_INDEX_DIGEST_MISMATCH")
            digests = False
        if actual.resolved_platform_digest != expected.resolved_platform_digest:
            reasons.append("ARM64_DIGEST_MISMATCH")
            digests = False
        if (
            actual.architecture != EXPECTED_ARCHITECTURE
            or actual.platform != EXPECTED_PLATFORM
        ):
            reasons.append("PLATFORM_MISMATCH")
            platforms = False
        if actual.image_id != expected.image_id:
            reasons.append("IMAGE_ID_MISMATCH")
            image_ids = False

    unique_reasons = tuple(dict.fromkeys(reasons))
    checks = LockMatchChecks(
        source_references=source_references,
        digests=digests,
        platforms=platforms,
        image_ids=image_ids,
        upstream_binding=upstream_binding,
        compose_binding=compose_binding,
        complete_inventory=complete_inventory,
    )
    return LockVerification(
        passed=not unique_reasons,
        outcome=(Outcome.SUCCESS if not unique_reasons else Outcome.BLOCKED_UPSTREAM),
        reason_codes=unique_reasons,
        checks=checks,
    )


def acceptance_compose_arguments(
    docker_endpoint: str,
) -> tuple[str, ...]:
    """Return the frozen Compose invocation suffix for later lifecycle use."""
    endpoint_path = Path(docker_endpoint.removeprefix("unix://"))
    if (
        not docker_endpoint.startswith("unix:///")
        or not endpoint_path.is_absolute()
        or ".." in endpoint_path.parts
        or any(character.isspace() for character in docker_endpoint)
    ):
        raise ValueError("acceptance Compose endpoint is not a local Unix socket")
    return (
        "docker",
        "--host",
        docker_endpoint,
        "compose",
        "--project-name",
        "ecomsre-phase0",
        "--project-directory",
        "third_party/opentelemetry-demo",
        "--env-file",
        "third_party/opentelemetry-demo/.env",
        "--file",
        "third_party/opentelemetry-demo/compose.yaml",
        "--file",
        "third_party/opentelemetry-demo/compose.observability.yaml",
        "--file",
        "config/phase0/compose.phase0.yaml",
        "up",
        "--detach",
        "--pull",
        "never",
        "--no-build",
    )


def load_image_lock(path: Path) -> ImageLockManifest:
    return ImageLockManifest.model_validate_json(path.read_text(encoding="utf-8"))


def write_candidate_image_lock(
    path: Path,
    lock: ImageLockManifest,
) -> None:
    """Publish once, permitting only UNINITIALIZED -> LOCKED replacement."""
    validated = ImageLockManifest.model_validate(lock.model_dump(mode="python"))
    if validated.status is not ImageLockStatus.LOCKED:
        raise ValueError("candidate image lock must be LOCKED")
    serialized = (
        json.dumps(
            validated.model_dump(mode="json"),
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")
    parent_descriptor = os.open(path.parent, os.O_RDONLY)
    existing_identity: tuple[int, int] | None = None
    try:
        existing_descriptor = os.open(
            path.name,
            os.O_RDONLY | (getattr(os, "O_NOFOLLOW", 0)),
            dir_fd=parent_descriptor,
        )
    except FileNotFoundError:
        existing_descriptor = None
    if existing_descriptor is not None:
        try:
            metadata = os.fstat(existing_descriptor)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_uid != os.getuid()
                or metadata.st_nlink != 1
                or stat.S_IMODE(metadata.st_mode) & 0o022
            ):
                raise ValueError("existing image lock placeholder is unsafe")
            existing = ImageLockManifest.model_validate_json(
                _read_descriptor(existing_descriptor)
            )
            if existing.status is not ImageLockStatus.UNINITIALIZED:
                raise FileExistsError("existing image lock is already initialized")
            existing_identity = (metadata.st_dev, metadata.st_ino)
        finally:
            os.close(existing_descriptor)
    temporary_name = f".{secrets.token_hex(16)}.tmp"
    temporary_descriptor: int | None = None
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        temporary_descriptor = os.open(
            temporary_name,
            flags,
            0o600,
            dir_fd=parent_descriptor,
        )
        remaining = memoryview(serialized)
        while remaining:
            written = os.write(temporary_descriptor, remaining)
            if written <= 0:
                raise OSError("candidate image lock write made no progress")
            remaining = remaining[written:]
        os.fsync(temporary_descriptor)
        if existing_identity is None:
            os.link(
                temporary_name,
                path.name,
                src_dir_fd=parent_descriptor,
                dst_dir_fd=parent_descriptor,
                follow_symlinks=False,
            )
        else:
            current = os.stat(
                path.name,
                dir_fd=parent_descriptor,
                follow_symlinks=False,
            )
            if (current.st_dev, current.st_ino) != existing_identity:
                raise ValueError("image lock placeholder changed before publish")
            os.rename(
                temporary_name,
                path.name,
                src_dir_fd=parent_descriptor,
                dst_dir_fd=parent_descriptor,
            )
        os.close(temporary_descriptor)
        temporary_descriptor = None
        if existing_identity is None:
            os.unlink(temporary_name, dir_fd=parent_descriptor)
        os.fsync(parent_descriptor)
    finally:
        if temporary_descriptor is not None:
            os.close(temporary_descriptor)
            try:
                os.unlink(temporary_name, dir_fd=parent_descriptor)
            except FileNotFoundError:
                pass
        os.close(parent_descriptor)


def rotate_candidate_image_lock(
    *,
    path: Path,
    resolved_compose: ResolvedComposeConfig,
    cached_images: tuple[InspectedImage, ...],
    expected_old_lock_sha256: str,
    rotation_reason: str,
    rotated_at: datetime,
) -> ImageLockRotationResult:
    """Rotate one LOCKED candidate with explicit live evidence and CAS."""
    if (
        re.fullmatch(SHA256_PATTERN, expected_old_lock_sha256) is None
        or rotation_reason != "COMPOSE_OVERRIDE_CHANGED"
    ):
        raise ValueError("image lock rotation authorization is invalid")
    lock_path = Path(path)
    original_bytes, original_identity = _read_secure_lock_snapshot(lock_path)
    original_sha256 = sha256_bytes(original_bytes)
    if original_sha256 != expected_old_lock_sha256:
        raise ValueError("expected old lock sha256 does not match current lock")
    original = ImageLockManifest.model_validate_json(original_bytes)
    if original.status is not ImageLockStatus.LOCKED:
        raise ValueError("image lock rotation requires an existing LOCKED file")
    if set(resolved_compose.image_references) != set(
        original.allowed_source_references
    ):
        raise ImageLockSourceSetChanged(
            "IMAGE_LOCK_SOURCE_SET_CHANGED_REQUIRES_FULL_BOOTSTRAP"
        )
    old_verification = verify_acceptance_image_lock(
        original,
        cached_images=cached_images,
        observed_upstream_commit=UPSTREAM_COMMIT,
        observed_compose_config_sha256=(
            original.compose_config_sha256 or ("0" * 64)
        ),
    )
    if not old_verification.passed:
        raise ValueError("cached image metadata differs from the current lock")
    candidate = generate_candidate_image_lock(
        images=cached_images,
        resolved_compose=resolved_compose,
        acquired_at=rotated_at,
    )
    candidate_verification = verify_acceptance_image_lock(
        candidate,
        cached_images=cached_images,
        observed_upstream_commit=UPSTREAM_COMMIT,
        observed_compose_config_sha256=resolved_compose.sha256,
    )
    if not candidate_verification.passed:
        raise ValueError("rotated candidate image lock verification failed")
    candidate_bytes = _serialized_image_lock(candidate)
    candidate_sha256 = sha256_bytes(candidate_bytes)
    _persist_image_lock_history(
        lock_path.parent,
        old_sha256=original_sha256,
        old_bytes=original_bytes,
    )
    _replace_lock_bytes_compare_and_swap(
        lock_path,
        replacement=candidate_bytes,
        expected_bytes=original_bytes,
        expected_identity=original_identity,
    )
    try:
        published = load_image_lock(lock_path)
        published_verification = verify_acceptance_image_lock(
            published,
            cached_images=cached_images,
            observed_upstream_commit=UPSTREAM_COMMIT,
            observed_compose_config_sha256=resolved_compose.sha256,
        )
        if (
            lock_path.read_bytes() != candidate_bytes
            or sha256_bytes(lock_path.read_bytes()) != candidate_sha256
            or published != candidate
            or not published_verification.passed
        ):
            raise ValueError("published rotated image lock verification failed")
    except (OSError, ValidationError, ValueError):
        try:
            current_bytes, current_identity = _read_secure_lock_snapshot(
                lock_path
            )
            if current_bytes == candidate_bytes:
                _replace_lock_bytes_compare_and_swap(
                    lock_path,
                    replacement=original_bytes,
                    expected_bytes=candidate_bytes,
                    expected_identity=current_identity,
                )
        except (OSError, ValueError):
            pass
        raise
    return ImageLockRotationResult(
        lock=published,
        verification=published_verification,
        evidence=ImageLockRotationEvidence(
            schema_version="phase0.image-lock-rotation.v1",
            rotation_reason="COMPOSE_OVERRIDE_CHANGED",
            old_compose_config_sha256=original.compose_config_sha256
            or ("0" * 64),
            new_compose_config_sha256=resolved_compose.sha256,
            old_lock_sha256=original_sha256,
            new_lock_sha256=candidate_sha256,
        ),
    )


def _serialized_image_lock(lock: ImageLockManifest) -> bytes:
    return (
        json.dumps(
            lock.model_dump(mode="json"),
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def _read_secure_lock_snapshot(
    path: Path,
) -> tuple[bytes, tuple[int, int]]:
    metadata = path.lstat()
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or metadata.st_nlink != 1
        or stat.S_IMODE(metadata.st_mode) & 0o022
    ):
        raise ValueError("current image lock is unsafe")
    raw = path.read_bytes()
    return raw, (metadata.st_dev, metadata.st_ino)


def _persist_image_lock_history(
    lock_directory: Path,
    *,
    old_sha256: str,
    old_bytes: bytes,
) -> Path:
    history_directory = lock_directory / "image-lock-history"
    history_directory.mkdir(mode=0o700, exist_ok=True)
    directory_metadata = history_directory.lstat()
    if (
        stat.S_ISLNK(directory_metadata.st_mode)
        or not stat.S_ISDIR(directory_metadata.st_mode)
        or directory_metadata.st_uid != os.getuid()
        or stat.S_IMODE(directory_metadata.st_mode) & 0o022
    ):
        raise ValueError("image lock history directory is unsafe")
    history_path = history_directory / f"{old_sha256}.json"
    if history_path.exists() or history_path.is_symlink():
        metadata = history_path.lstat()
        if (
            stat.S_ISLNK(metadata.st_mode)
            or not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or metadata.st_nlink != 1
            or stat.S_IMODE(metadata.st_mode) & 0o022
            or history_path.read_bytes() != old_bytes
        ):
            raise ValueError("existing image lock history conflicts")
        return history_path
    _write_exclusive_bytes(history_directory, history_path.name, old_bytes)
    return history_path


def _write_exclusive_bytes(
    directory: Path,
    name: str,
    payload: bytes,
) -> None:
    directory_descriptor = os.open(directory, os.O_RDONLY)
    temporary_name = f".{secrets.token_hex(16)}.tmp"
    temporary_descriptor: int | None = None
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        temporary_descriptor = os.open(
            temporary_name,
            flags,
            0o600,
            dir_fd=directory_descriptor,
        )
        _write_all(temporary_descriptor, payload)
        os.fsync(temporary_descriptor)
        os.link(
            temporary_name,
            name,
            src_dir_fd=directory_descriptor,
            dst_dir_fd=directory_descriptor,
            follow_symlinks=False,
        )
        os.unlink(temporary_name, dir_fd=directory_descriptor)
        os.close(temporary_descriptor)
        temporary_descriptor = None
        os.fsync(directory_descriptor)
    finally:
        if temporary_descriptor is not None:
            os.close(temporary_descriptor)
            try:
                os.unlink(temporary_name, dir_fd=directory_descriptor)
            except FileNotFoundError:
                pass
        os.close(directory_descriptor)


def _replace_lock_bytes_compare_and_swap(
    path: Path,
    *,
    replacement: bytes,
    expected_bytes: bytes,
    expected_identity: tuple[int, int],
) -> None:
    parent_descriptor = os.open(path.parent, os.O_RDONLY)
    temporary_name = f".{secrets.token_hex(16)}.tmp"
    temporary_descriptor: int | None = None
    current_descriptor: int | None = None
    try:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        current_descriptor = os.open(
            path.name,
            flags,
            dir_fd=parent_descriptor,
        )
        current_metadata = os.fstat(current_descriptor)
        current_bytes = _read_descriptor(current_descriptor)
        if (
            (current_metadata.st_dev, current_metadata.st_ino)
            != expected_identity
            or current_bytes != expected_bytes
            or sha256_bytes(current_bytes) != sha256_bytes(expected_bytes)
        ):
            raise ValueError("current image lock changed before rotation")
        write_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            write_flags |= os.O_NOFOLLOW
        temporary_descriptor = os.open(
            temporary_name,
            write_flags,
            0o600,
            dir_fd=parent_descriptor,
        )
        _write_all(temporary_descriptor, replacement)
        os.fsync(temporary_descriptor)
        current_at_publish = os.stat(
            path.name,
            dir_fd=parent_descriptor,
            follow_symlinks=False,
        )
        current_at_publish_bytes = _read_descriptor(current_descriptor)
        if (
            current_at_publish.st_dev,
            current_at_publish.st_ino,
        ) != expected_identity or (
            current_at_publish_bytes != expected_bytes
            or sha256_bytes(current_at_publish_bytes)
            != sha256_bytes(expected_bytes)
        ):
            raise ValueError("current image lock changed before rotation")
        os.rename(
            temporary_name,
            path.name,
            src_dir_fd=parent_descriptor,
            dst_dir_fd=parent_descriptor,
        )
        os.close(temporary_descriptor)
        temporary_descriptor = None
        os.fsync(parent_descriptor)
    finally:
        if current_descriptor is not None:
            os.close(current_descriptor)
        if temporary_descriptor is not None:
            os.close(temporary_descriptor)
            try:
                os.unlink(temporary_name, dir_fd=parent_descriptor)
            except FileNotFoundError:
                pass
        os.close(parent_descriptor)


def _write_all(descriptor: int, payload: bytes) -> None:
    remaining = memoryview(payload)
    while remaining:
        written = os.write(descriptor, remaining)
        if written <= 0:
            raise OSError("image lock write made no progress")
        remaining = remaining[written:]


def _compose_service_image_mapping(stdout: str) -> tuple[tuple[str, str], ...]:
    try:
        payload = json.loads(stdout)
        services = payload["services"]
        if not isinstance(services, dict) or not services:
            raise ValueError
        mapping = tuple(
            (str(name), str(service["image"]))
            for name, service in sorted(services.items())
            if isinstance(service, dict)
        )
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
        raise ValueError(
            "resolved Compose JSON lacks a complete service image inventory"
        ) from error
    if len(mapping) != len(services) or any(
        not service or not reference for service, reference in mapping
    ):
        raise ValueError(
            "resolved Compose JSON lacks a complete service image inventory"
        )
    return mapping


def _read_descriptor(descriptor: int) -> bytes:
    chunks: list[bytes] = []
    offset = 0
    while True:
        chunk = os.pread(descriptor, 64 * 1024, offset)
        if not chunk:
            return b"".join(chunks)
        chunks.append(chunk)
        offset += len(chunk)
