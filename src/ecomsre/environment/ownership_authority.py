"""Authenticated, run-scoped ownership authority for Phase 0 preflight."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import stat
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from ecomsre.environment.ownership import (
    PROJECT_LABEL,
    PROJECT_NAMESPACE,
    RUN_LABEL,
    OwnedResource,
    OwnershipManifest,
)
from ecomsre.evidence.hashes import (
    canonical_json_bytes,
    canonical_json_sha256,
)
from ecomsre.evidence.store import (
    _DirectoryCapability,
    _write_immutable_bytes,
)
from ecomsre.phase0.models import Outcome


_RUN_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")
_CONTEXT_TOKEN = object()
_INTENT_CONTEXT_TOKEN = object()
_CONTEXT_INTEGRITY_KEY = secrets.token_bytes(32)
_INTENT_CONTEXT_INTEGRITY_KEY = secrets.token_bytes(32)
_KEY_BYTES = 32


class OwnershipAuthorityError(RuntimeError):
    """The fixed ownership authority artifacts could not be authenticated."""

    outcome = Outcome.UNSAFE
    exit_code = Outcome.UNSAFE.exit_code
    reason_code = "RESOURCE_OWNERSHIP_UNKNOWN"


class _OwnershipAnchor(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["phase0.ownership-anchor.v1"]
    run_id: str = Field(pattern=_RUN_ID_PATTERN.pattern)
    project_name: Literal["ecomsre-phase0"]
    canonical_labels: dict[str, str]
    manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    created_at: datetime
    hmac_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_canonical_anchor(self) -> "_OwnershipAnchor":
        if (
            self.created_at.utcoffset() is None
            or self.created_at.utcoffset() != timedelta(0)
        ):
            raise ValueError("ownership anchor created_at must be UTC")
        if self.canonical_labels != _canonical_labels(self.run_id):
            raise ValueError("ownership anchor labels are not canonical")
        return self


class OwnershipIntent(BaseModel):
    """Authenticated pre-mutation intent with an explicitly empty inventory."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["phase0.ownership-intent.v2"]
    run_id: str = Field(pattern=_RUN_ID_PATTERN.pattern)
    project_name: Literal["ecomsre-phase0"]
    canonical_labels: dict[str, str]
    expected_compose_files: tuple[str, ...] = Field(min_length=1)
    runtime_compose_instance_sha256: str = Field(
        pattern=r"^[0-9a-f]{64}$"
    )
    canonical_compose_contract_sha256: str = Field(
        pattern=r"^[0-9a-f]{64}$"
    )
    compose_canonicalization_schema_version: Literal[
        "phase0.compose-canonicalization.v1"
    ]
    expected_image_sources: tuple[str, ...] = Field(min_length=1)
    pull_policy: Literal["never"]
    build_policy: Literal["no-build"]
    resources: tuple[OwnedResource, ...]
    created_at: datetime

    @model_validator(mode="after")
    def require_canonical_empty_intent(self) -> "OwnershipIntent":
        if self.canonical_labels != _canonical_labels(self.run_id):
            raise ValueError("ownership intent labels are not canonical")
        if self.resources:
            raise ValueError("ownership intent requires an empty inventory")
        if len(set(self.expected_compose_files)) != len(
            self.expected_compose_files
        ) or len(set(self.expected_image_sources)) != len(self.expected_image_sources):
            raise ValueError("ownership intent sources must be unique")
        if (
            self.created_at.utcoffset() is None
            or self.created_at.utcoffset() != timedelta(0)
        ):
            raise ValueError("ownership intent created_at must be UTC")
        return self


class OwnershipIntentArtifactPaths(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    intent_path: Path
    anchor_path: Path
    key_path: Path


class _OwnershipIntentAnchor(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["phase0.ownership-intent-anchor.v1"]
    run_id: str = Field(pattern=_RUN_ID_PATTERN.pattern)
    project_name: Literal["ecomsre-phase0"]
    canonical_labels: dict[str, str]
    intent_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    created_at: datetime
    hmac_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_canonical_anchor(self) -> "_OwnershipIntentAnchor":
        if self.canonical_labels != _canonical_labels(self.run_id):
            raise ValueError("ownership intent anchor labels are not canonical")
        if (
            self.created_at.utcoffset() is None
            or self.created_at.utcoffset() != timedelta(0)
        ):
            raise ValueError("ownership intent anchor created_at must be UTC")
        return self


@dataclass(frozen=True, init=False)
class AuthenticatedOwnershipIntent:
    """Opaque authenticated handle for a persisted ownership intent."""

    _intent: OwnershipIntent
    _intent_sha256: str
    _integrity_hmac: str
    _provenance: object = field(repr=False, compare=False)

    def __init__(
        self,
        *,
        _token: object | None = None,
        intent: OwnershipIntent,
        intent_sha256: str,
    ) -> None:
        if _token is not _INTENT_CONTEXT_TOKEN:
            raise TypeError("authenticated ownership intent must come from the loader")
        integrity = _intent_context_integrity(intent, intent_sha256)
        object.__setattr__(self, "_intent", intent)
        object.__setattr__(self, "_intent_sha256", intent_sha256)
        object.__setattr__(self, "_integrity_hmac", integrity)
        object.__setattr__(self, "_provenance", _INTENT_CONTEXT_TOKEN)

    @property
    def intent(self) -> OwnershipIntent:
        return self._intent

    @property
    def intent_sha256(self) -> str:
        return self._intent_sha256

    def is_authentic(self) -> bool:
        if self._provenance is not _INTENT_CONTEXT_TOKEN:
            return False
        return hmac.compare_digest(
            self._integrity_hmac,
            _intent_context_integrity(self._intent, self._intent_sha256),
        )


@dataclass(frozen=True, init=False)
class AuthenticatedOwnershipContext:
    """Opaque context issued only after fixed-path artifact authentication."""

    _run_id: str
    _project_name: str
    _canonical_labels: tuple[tuple[str, str], ...]
    _manifest: OwnershipManifest
    _manifest_sha256: str
    _created_at: datetime
    _integrity_hmac: str
    _provenance: object = field(repr=False, compare=False)

    def __init__(
        self,
        *,
        _token: object | None = None,
        run_id: str,
        project_name: str,
        canonical_labels: dict[str, str],
        manifest: OwnershipManifest,
        manifest_sha256: str,
        created_at: datetime,
    ) -> None:
        if _token is not _CONTEXT_TOKEN:
            raise TypeError("authenticated ownership context must come from the loader")
        labels = tuple(sorted(canonical_labels.items()))
        integrity = _context_integrity(
            run_id=run_id,
            project_name=project_name,
            canonical_labels=labels,
            manifest=manifest,
            manifest_sha256=manifest_sha256,
            created_at=created_at,
        )
        for name, value in {
            "_run_id": run_id,
            "_project_name": project_name,
            "_canonical_labels": labels,
            "_manifest": manifest,
            "_manifest_sha256": manifest_sha256,
            "_created_at": created_at,
            "_integrity_hmac": integrity,
            "_provenance": _CONTEXT_TOKEN,
        }.items():
            object.__setattr__(self, name, value)

    @property
    def run_id(self) -> str:
        return self._run_id

    @property
    def project_name(self) -> str:
        return self._project_name

    @property
    def canonical_labels(self) -> dict[str, str]:
        return dict(self._canonical_labels)

    @property
    def manifest(self) -> OwnershipManifest:
        return self._manifest

    @property
    def manifest_sha256(self) -> str:
        return self._manifest_sha256

    @property
    def created_at(self) -> datetime:
        return self._created_at

    def is_authentic(self) -> bool:
        if self._provenance is not _CONTEXT_TOKEN:
            return False
        expected = _context_integrity(
            run_id=self._run_id,
            project_name=self._project_name,
            canonical_labels=self._canonical_labels,
            manifest=self._manifest,
            manifest_sha256=self._manifest_sha256,
            created_at=self._created_at,
        )
        return hmac.compare_digest(expected, self._integrity_hmac)


def create_ownership_authority_artifacts(
    artifacts_root: Path,
    manifest: OwnershipManifest,
    *,
    created_at: datetime,
) -> None:
    """Environment-up factory for new authority artifacts.

    Phase 0 Task 4 defines this interface. The later environment-up lifecycle
    is responsible for invoking it; preflight never creates or repairs anchors.
    """
    if not manifest.is_consistent():
        raise OwnershipAuthorityError("ownership manifest is inconsistent")
    if created_at.utcoffset() is None or created_at.utcoffset() != timedelta(0):
        raise OwnershipAuthorityError("ownership anchor created_at must be UTC")

    manifest_path, anchor_path, key_path = _authority_paths(
        artifacts_root,
        manifest.run_id,
    )
    manifest_sha256 = canonical_json_sha256(manifest.model_dump(mode="json"))
    key = secrets.token_bytes(_KEY_BYTES)
    unsigned_anchor = {
        "schema_version": "phase0.ownership-anchor.v1",
        "run_id": manifest.run_id,
        "project_name": PROJECT_NAMESPACE,
        "canonical_labels": _canonical_labels(manifest.run_id),
        "manifest_sha256": manifest_sha256,
        "created_at": created_at.isoformat(),
    }
    signature = hmac.new(
        key,
        canonical_json_bytes(unsigned_anchor),
        hashlib.sha256,
    ).hexdigest()
    anchor = {**unsigned_anchor, "hmac_sha256": signature}

    _write_authority_bundle(
        artifacts_root,
        manifest.run_id,
        observer_files={
            manifest_path.name: canonical_json_bytes(manifest.model_dump(mode="json"))
        },
        evaluator_files={
            key_path.name: key,
            anchor_path.name: canonical_json_bytes(anchor),
        },
    )


def create_ownership_intent_artifacts(
    artifacts_root: Path,
    intent: OwnershipIntent,
) -> OwnershipIntentArtifactPaths:
    """Persist and sign a pre-up empty-inventory ownership intent."""
    validated = OwnershipIntent.model_validate(intent.model_dump(mode="python"))
    intent_path, anchor_path, key_path = _intent_paths(
        artifacts_root,
        validated.run_id,
    )
    intent_payload = validated.model_dump(mode="json")
    intent_sha256 = canonical_json_sha256(intent_payload)
    key = secrets.token_bytes(_KEY_BYTES)
    unsigned_anchor = {
        "schema_version": "phase0.ownership-intent-anchor.v1",
        "run_id": validated.run_id,
        "project_name": PROJECT_NAMESPACE,
        "canonical_labels": _canonical_labels(validated.run_id),
        "intent_sha256": intent_sha256,
        "created_at": validated.created_at.isoformat(),
    }
    signature = hmac.new(
        key,
        canonical_json_bytes(unsigned_anchor),
        hashlib.sha256,
    ).hexdigest()
    _write_authority_bundle(
        artifacts_root,
        validated.run_id,
        observer_files={
            intent_path.name: canonical_json_bytes(intent_payload),
        },
        evaluator_files={
            key_path.name: key,
            anchor_path.name: canonical_json_bytes(
                {**unsigned_anchor, "hmac_sha256": signature}
            ),
        },
    )
    return OwnershipIntentArtifactPaths(
        intent_path=intent_path,
        anchor_path=anchor_path,
        key_path=key_path,
    )


def load_authenticated_ownership_intent(
    artifacts_root: Path,
    run_id: str,
) -> AuthenticatedOwnershipIntent:
    """Verify intent content, anchor HMAC, and fixed-path provenance."""
    intent_path, anchor_path, key_path = _intent_paths(
        artifacts_root,
        run_id,
    )
    intent_bytes = _secure_read(
        artifacts_root,
        intent_path,
        purpose="ownership intent",
        exact_mode=None,
    )
    anchor_bytes = _secure_read(
        artifacts_root,
        anchor_path,
        purpose="ownership intent anchor",
        exact_mode=None,
    )
    key = _secure_read(
        artifacts_root,
        key_path,
        purpose="ownership intent key",
        exact_mode=0o600,
    )
    if len(key) != _KEY_BYTES:
        raise OwnershipAuthorityError("ownership intent key has invalid length")
    try:
        raw_intent = json.loads(intent_bytes)
        intent = OwnershipIntent.model_validate(raw_intent)
        raw_anchor = json.loads(anchor_bytes)
        anchor = _OwnershipIntentAnchor.model_validate(raw_anchor)
    except (json.JSONDecodeError, TypeError, ValidationError) as error:
        raise OwnershipAuthorityError(
            "ownership intent authentication metadata is invalid"
        ) from error
    unsigned_anchor = {
        name: value for name, value in raw_anchor.items() if name != "hmac_sha256"
    }
    expected_hmac = hmac.new(
        key,
        canonical_json_bytes(unsigned_anchor),
        hashlib.sha256,
    ).hexdigest()
    intent_sha256 = canonical_json_sha256(intent.model_dump(mode="json"))
    if not hmac.compare_digest(expected_hmac, anchor.hmac_sha256):
        raise OwnershipAuthorityError("ownership intent anchor authentication failed")
    if (
        intent.run_id != run_id
        or anchor.run_id != run_id
        or anchor.project_name != PROJECT_NAMESPACE
        or anchor.canonical_labels != _canonical_labels(run_id)
        or anchor.intent_sha256 != intent_sha256
        or anchor.created_at != intent.created_at
    ):
        raise OwnershipAuthorityError("ownership intent authentication binding failed")
    return AuthenticatedOwnershipIntent(
        _token=_INTENT_CONTEXT_TOKEN,
        intent=intent,
        intent_sha256=intent_sha256,
    )


def load_authenticated_ownership_context(
    artifacts_root: Path,
    run_id: str,
) -> AuthenticatedOwnershipContext:
    """Authenticate fixed run artifacts and issue an opaque in-memory context."""
    manifest_path, anchor_path, key_path = _authority_paths(
        artifacts_root,
        run_id,
    )
    manifest_bytes = _secure_read(
        artifacts_root,
        manifest_path,
        purpose="ownership manifest",
        exact_mode=None,
    )
    anchor_bytes = _secure_read(
        artifacts_root,
        anchor_path,
        purpose="ownership anchor",
        exact_mode=None,
    )
    key = _secure_read(
        artifacts_root,
        key_path,
        purpose="ownership key",
        exact_mode=0o600,
    )
    if len(key) != _KEY_BYTES:
        raise OwnershipAuthorityError("ownership key has invalid length")

    try:
        raw_anchor = json.loads(anchor_bytes)
        anchor = _OwnershipAnchor.model_validate(raw_anchor)
    except (json.JSONDecodeError, TypeError, ValidationError) as error:
        raise OwnershipAuthorityError(
            "ownership anchor authentication metadata is invalid"
        ) from error

    unsigned_anchor = {
        key: value for key, value in raw_anchor.items() if key != "hmac_sha256"
    }
    expected_hmac = hmac.new(
        key,
        canonical_json_bytes(unsigned_anchor),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(expected_hmac, anchor.hmac_sha256):
        raise OwnershipAuthorityError("ownership anchor authentication failed")
    if anchor.run_id != run_id:
        raise OwnershipAuthorityError("ownership anchor run does not match")
    if (
        anchor.project_name != PROJECT_NAMESPACE
        or anchor.canonical_labels != _canonical_labels(run_id)
    ):
        raise OwnershipAuthorityError("ownership anchor project or labels do not match")

    try:
        manifest = OwnershipManifest.model_validate_json(manifest_bytes)
    except ValidationError as error:
        raise OwnershipAuthorityError("ownership manifest is invalid") from error
    if manifest.run_id != run_id or not manifest.is_consistent():
        raise OwnershipAuthorityError("ownership manifest run does not match")
    manifest_sha256 = canonical_json_sha256(manifest.model_dump(mode="json"))
    if manifest_sha256 != anchor.manifest_sha256:
        raise OwnershipAuthorityError(
            "ownership manifest authentication hash does not match"
        )

    return AuthenticatedOwnershipContext(
        _token=_CONTEXT_TOKEN,
        run_id=run_id,
        project_name=anchor.project_name,
        canonical_labels=anchor.canonical_labels,
        manifest=manifest,
        manifest_sha256=manifest_sha256,
        created_at=anchor.created_at,
    )


def _authority_paths(
    artifacts_root: Path,
    run_id: str,
) -> tuple[Path, Path, Path]:
    if _RUN_ID_PATTERN.fullmatch(run_id) is None:
        raise OwnershipAuthorityError("ownership authority run_id is invalid")
    root = Path(artifacts_root)
    return (
        root / "observer-visible" / run_id / "resource-ownership.json",
        root / "evaluator-only" / run_id / "ownership-anchor.json",
        root / "evaluator-only" / run_id / ".ownership-anchor.key",
    )


def _intent_paths(
    artifacts_root: Path,
    run_id: str,
) -> tuple[Path, Path, Path]:
    if _RUN_ID_PATTERN.fullmatch(run_id) is None:
        raise OwnershipAuthorityError("ownership intent run_id is invalid")
    root = Path(artifacts_root)
    return (
        root / "observer-visible" / run_id / "ownership-intent.json",
        root / "evaluator-only" / run_id / "ownership-intent-anchor.json",
        root / "evaluator-only" / run_id / ".ownership-intent.key",
    )


def _write_authority_bundle(
    artifacts_root: Path,
    run_id: str,
    *,
    observer_files: dict[str, bytes],
    evaluator_files: dict[str, bytes],
) -> None:
    """Write a fixed authority bundle through inode-bound directory handles."""
    with (
        _DirectoryCapability(
            artifacts_root,
            "observer-visible",
            run_id,
            zone="observer",
            allowed_top_level=set(observer_files),
        ) as observer,
        _DirectoryCapability(
            artifacts_root,
            "evaluator-only",
            run_id,
            zone="evaluator",
            allowed_top_level=set(evaluator_files),
        ) as evaluator,
    ):
        for relative_path, content in evaluator_files.items():
            _write_immutable_bytes(
                evaluator,
                relative_path,
                content,
                zone="evaluator",
                allowed_top_level=set(evaluator_files),
            )
        for relative_path, content in observer_files.items():
            _write_immutable_bytes(
                observer,
                relative_path,
                content,
                zone="observer",
                allowed_top_level=set(observer_files),
            )


def _secure_read(
    root: Path,
    path: Path,
    *,
    purpose: str,
    exact_mode: int | None,
) -> bytes:
    _validate_trust_directory_chain(Path(root), path.parent, purpose=purpose)
    try:
        metadata = path.lstat()
    except FileNotFoundError as error:
        raise OwnershipAuthorityError(f"{purpose} is missing") from error
    if stat.S_ISLNK(metadata.st_mode):
        raise OwnershipAuthorityError(f"{purpose} symlink is forbidden")
    if not stat.S_ISREG(metadata.st_mode):
        raise OwnershipAuthorityError(f"{purpose} is not a regular file")
    if metadata.st_uid != os.geteuid():
        raise OwnershipAuthorityError(f"{purpose} owner is unsafe")
    file_mode = stat.S_IMODE(metadata.st_mode)
    if (exact_mode is not None and file_mode != exact_mode) or (
        exact_mode is None and file_mode & 0o022
    ):
        raise OwnershipAuthorityError(f"{purpose} permissions are unsafe")

    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags)
    try:
        opened = os.fstat(descriptor)
        if opened.st_dev != metadata.st_dev or opened.st_ino != metadata.st_ino:
            raise OwnershipAuthorityError(f"{purpose} changed during read")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 64 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
    finally:
        os.close(descriptor)
    return b"".join(chunks)


def _validate_trust_directory_chain(
    root: Path,
    target_parent: Path,
    *,
    purpose: str,
) -> None:
    root_absolute = root.absolute()
    parent_absolute = target_parent.absolute()
    try:
        relative = parent_absolute.relative_to(root_absolute)
    except ValueError as error:
        raise OwnershipAuthorityError(
            "ownership authority path escapes artifact root"
        ) from error

    directories = (root_absolute,) + tuple(
        root_absolute.joinpath(*relative.parts[:index])
        for index in range(1, len(relative.parts) + 1)
    )
    for directory in directories:
        try:
            metadata = directory.lstat()
        except FileNotFoundError as error:
            raise OwnershipAuthorityError(f"{purpose} is missing") from error
        if stat.S_ISLNK(metadata.st_mode):
            raise OwnershipAuthorityError(f"{purpose} path contains a symlink")
        if not stat.S_ISDIR(metadata.st_mode):
            raise OwnershipAuthorityError(f"{purpose} parent directory is unsafe")
        if metadata.st_uid != os.geteuid():
            raise OwnershipAuthorityError(f"{purpose} parent owner is unsafe")
        if stat.S_IMODE(metadata.st_mode) & 0o022:
            raise OwnershipAuthorityError(f"{purpose} parent permissions are unsafe")


def _canonical_labels(run_id: str) -> dict[str, str]:
    return {
        PROJECT_LABEL: PROJECT_NAMESPACE,
        RUN_LABEL: run_id,
    }


def _context_integrity(
    *,
    run_id: str,
    project_name: str,
    canonical_labels: tuple[tuple[str, str], ...],
    manifest: OwnershipManifest,
    manifest_sha256: str,
    created_at: datetime,
) -> str:
    payload: dict[str, Any] = {
        "run_id": run_id,
        "project_name": project_name,
        "canonical_labels": dict(canonical_labels),
        "manifest": manifest.model_dump(mode="json"),
        "manifest_sha256": manifest_sha256,
        "created_at": created_at.isoformat(),
    }
    return hmac.new(
        _CONTEXT_INTEGRITY_KEY,
        canonical_json_bytes(payload),
        hashlib.sha256,
    ).hexdigest()


def _intent_context_integrity(
    intent: OwnershipIntent,
    intent_sha256: str,
) -> str:
    return hmac.new(
        _INTENT_CONTEXT_INTEGRITY_KEY,
        canonical_json_bytes(
            {
                "intent": intent.model_dump(mode="json"),
                "intent_sha256": intent_sha256,
            }
        ),
        hashlib.sha256,
    ).hexdigest()
