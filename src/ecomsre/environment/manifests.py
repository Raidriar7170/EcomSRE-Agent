"""Frozen upstream and image-lock contracts for Phase 0."""

from __future__ import annotations

import copy
import json
import os
import re
import secrets
import stat
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from ecomsre.evidence.hashes import canonical_json_sha256, sha256_bytes
from ecomsre.phase0.models import Outcome


UPSTREAM_TAG = "3.0.0"
UPSTREAM_COMMIT = "1755859a9de82c2e5e225be68abc401a5ebf2b4f"
EXPECTED_ARCHITECTURE = "arm64"
EXPECTED_PLATFORM = "linux/arm64"
SHA256_PATTERN = r"^[0-9a-f]{64}$"
DIGEST_PATTERN = r"^sha256:[0-9a-f]{64}$"
RUN_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")
COMPOSE_CANONICALIZATION_SCHEMA_VERSION = (
    "phase0.compose-canonicalization.v1"
)
_CANONICAL_RUN_ID = "<ECOMSRE_RUN_ID>"
_RUN_LABEL = "io.ecomsre.run"
_PROJECT_LABEL = "io.ecomsre.project"
_PROJECT_NAMESPACE = "ecomsre-phase0"


class ImageLockStatus(str, Enum):
    UNINITIALIZED = "UNINITIALIZED"
    LOCKED = "LOCKED"


class ImageLockSourceSetChanged(ValueError):
    """Lightweight rotation cannot change the frozen source inventory."""


def compose_canonicalization_schema() -> dict[str, object]:
    """Return the complete immutable-by-value v1 projection contract."""
    return {
        "schema_version": COMPOSE_CANONICALIZATION_SCHEMA_VERSION,
        "runtime_identity": {
            "name": "ECOMSRE_RUN_ID",
            "format": "lowercase-hex-32",
            "canonical_token": _CANONICAL_RUN_ID,
        },
        "selectors": [
            "services.*.labels.io.ecomsre.run",
            "networks.*.labels.io.ecomsre.run",
            "volumes.*.labels.io.ecomsre.run",
            "x-phase0-labels.io.ecomsre.run",
            "x-phase0-service.labels.io.ecomsre.run",
            "volumes.<logical-name>.name",
            (
                "services.*.volumes[type=bind].source:"
                "artifacts/phase0/evaluator-only/<run-id>/..."
            ),
        ],
    }


def canonicalize_compose_contract(
    payload: dict[str, object],
) -> dict[str, object]:
    """Project only schema-approved run identity out of resolved Compose JSON."""
    projected = copy.deepcopy(payload)
    label_mappings = _compose_runtime_label_mappings(projected)
    raw_runtime_ids: set[str] = set()
    canonical_identity_seen = False
    for labels in label_mappings:
        if _RUN_LABEL not in labels:
            continue
        value = labels[_RUN_LABEL]
        if value == _CANONICAL_RUN_ID:
            canonical_identity_seen = True
        elif isinstance(value, str) and RUN_ID_PATTERN.fullmatch(value):
            raw_runtime_ids.add(value)
        else:
            raise ValueError("resolved Compose runtime identity is malformed")
    if len(raw_runtime_ids) > 1 or (raw_runtime_ids and canonical_identity_seen):
        raise ValueError("resolved Compose runtime identity is inconsistent")
    runtime_identity = (
        next(iter(raw_runtime_ids))
        if raw_runtime_ids
        else (_CANONICAL_RUN_ID if canonical_identity_seen else None)
    )
    if runtime_identity is None:
        return projected

    for labels in label_mappings:
        if _RUN_LABEL in labels:
            labels[_RUN_LABEL] = _CANONICAL_RUN_ID
    _canonicalize_run_scoped_volume_names(
        projected,
        runtime_identity=runtime_identity,
    )
    _canonicalize_evaluator_bind_sources(
        projected,
        runtime_identity=runtime_identity,
    )
    return projected


class ResolvedComposeConfig(BaseModel):
    """Exact, interpolated Compose JSON and its derived image inventory."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    stdout: str = Field(min_length=1)
    runtime_compose_instance_sha256: str = Field(pattern=SHA256_PATTERN)
    canonical_compose_contract_sha256: str = Field(pattern=SHA256_PATTERN)
    canonicalization_schema_version: Literal[
        "phase0.compose-canonicalization.v1"
    ]
    image_references: tuple[str, ...] = Field(min_length=1)
    service_image_mapping: tuple[tuple[str, str], ...] = Field(min_length=1)

    @classmethod
    def from_stdout(cls, stdout: str) -> "ResolvedComposeConfig":
        mapping = _compose_service_image_mapping(stdout)
        payload = _compose_payload(stdout)
        return cls(
            stdout=stdout,
            runtime_compose_instance_sha256=sha256_bytes(
                stdout.encode("utf-8")
            ),
            canonical_compose_contract_sha256=canonical_json_sha256(
                {
                    "canonicalization_schema": (
                        compose_canonicalization_schema()
                    ),
                    "resolved_compose": canonicalize_compose_contract(payload),
                }
            ),
            canonicalization_schema_version=(
                COMPOSE_CANONICALIZATION_SCHEMA_VERSION
            ),
            image_references=tuple(sorted({image for _service, image in mapping})),
            service_image_mapping=mapping,
        )

    @model_validator(mode="after")
    def bind_inventory_to_exact_stdout(self) -> "ResolvedComposeConfig":
        if self.runtime_compose_instance_sha256 != sha256_bytes(
            self.stdout.encode("utf-8")
        ):
            raise ValueError(
                "runtime Compose instance hash does not match stdout"
            )
        payload = _compose_payload(self.stdout)
        expected_contract_hash = canonical_json_sha256(
            {
                "canonicalization_schema": (
                    compose_canonicalization_schema()
                ),
                "resolved_compose": canonicalize_compose_contract(payload),
            }
        )
        if self.canonical_compose_contract_sha256 != expected_contract_hash:
            raise ValueError(
                "canonical Compose contract hash does not match stdout"
            )
        mapping = _compose_service_image_mapping(self.stdout)
        if self.service_image_mapping != mapping:
            raise ValueError("resolved Compose service image mapping differs")
        if self.image_references != tuple(
            sorted({image for _service, image in mapping})
        ):
            raise ValueError("resolved Compose image inventory does not match stdout")
        return self


def _compose_runtime_label_mappings(
    payload: dict[str, object],
) -> tuple[dict[str, object], ...]:
    mappings: list[dict[str, object]] = []
    for collection_name in ("services", "networks", "volumes"):
        collection = payload.get(collection_name)
        if collection is None:
            continue
        if not isinstance(collection, dict):
            raise ValueError(
                f"resolved Compose {collection_name} must be an object"
            )
        for definition in collection.values():
            if not isinstance(definition, dict):
                raise ValueError(
                    f"resolved Compose {collection_name} entry must be an object"
                )
            labels = definition.get("labels")
            if labels is None:
                continue
            if not isinstance(labels, dict):
                raise ValueError("resolved Compose labels must be an object")
            mappings.append(labels)

    phase0_labels = payload.get("x-phase0-labels")
    if phase0_labels is not None:
        if not isinstance(phase0_labels, dict):
            raise ValueError("x-phase0-labels must be an object")
        mappings.append(phase0_labels)
    phase0_service = payload.get("x-phase0-service")
    if phase0_service is not None:
        if not isinstance(phase0_service, dict):
            raise ValueError("x-phase0-service must be an object")
        labels = phase0_service.get("labels")
        if labels is not None:
            if not isinstance(labels, dict):
                raise ValueError("x-phase0-service labels must be an object")
            mappings.append(labels)
    return tuple(mappings)


def _canonicalize_run_scoped_volume_names(
    payload: dict[str, object],
    *,
    runtime_identity: str,
) -> None:
    volumes = payload.get("volumes")
    if volumes is None:
        return
    if not isinstance(volumes, dict):
        raise ValueError("resolved Compose volumes must be an object")
    for logical_name, definition in volumes.items():
        if (
            not isinstance(logical_name, str)
            or not isinstance(definition, dict)
        ):
            raise ValueError("resolved Compose volume entry is malformed")
        labels = definition.get("labels")
        if (
            not isinstance(labels, dict)
            or labels.get(_PROJECT_LABEL) != _PROJECT_NAMESPACE
            or labels.get(_RUN_LABEL) != _CANONICAL_RUN_ID
        ):
            continue
        current_name = definition.get("name")
        approved_name = (
            f"{_PROJECT_NAMESPACE}-{runtime_identity}-{logical_name}"
        )
        canonical_name = (
            f"{_PROJECT_NAMESPACE}-{_CANONICAL_RUN_ID}-{logical_name}"
        )
        if current_name in {approved_name, canonical_name}:
            definition["name"] = canonical_name


def _canonicalize_evaluator_bind_sources(
    payload: dict[str, object],
    *,
    runtime_identity: str,
) -> None:
    services = payload.get("services")
    if services is None:
        return
    if not isinstance(services, dict):
        raise ValueError("resolved Compose services must be an object")
    for definition in services.values():
        if not isinstance(definition, dict):
            raise ValueError("resolved Compose service entry is malformed")
        labels = definition.get("labels")
        if (
            not isinstance(labels, dict)
            or labels.get(_PROJECT_LABEL) != _PROJECT_NAMESPACE
            or labels.get(_RUN_LABEL) != _CANONICAL_RUN_ID
        ):
            continue
        mounts = definition.get("volumes", [])
        if not isinstance(mounts, list):
            raise ValueError("resolved Compose service volumes must be a list")
        for mount in mounts:
            if not isinstance(mount, dict):
                raise ValueError("resolved Compose mount must be an object")
            if mount.get("type") != "bind":
                continue
            source = mount.get("source")
            if not isinstance(source, str):
                continue
            components = source.split("/")
            matching_indexes = [
                index
                for index in range(3, len(components))
                if components[index - 3 : index]
                == ["artifacts", "phase0", "evaluator-only"]
                and components[index]
                in {runtime_identity, _CANONICAL_RUN_ID}
            ]
            if len(matching_indexes) > 1:
                raise ValueError(
                    "evaluator-only bind source runtime identity is ambiguous"
                )
            if matching_indexes:
                components[matching_indexes[0]] = _CANONICAL_RUN_ID
                mount["source"] = "/".join(components)


def _compose_payload(stdout: str) -> dict[str, object]:
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError as error:
        raise ValueError("resolved Compose output must be JSON") from error
    if not isinstance(payload, dict):
        raise ValueError("resolved Compose output must be an object")
    return payload


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
    compose_config_sha256: str | None = Field(
        default=None,
        pattern=SHA256_PATTERN,
    )
    canonical_compose_contract_sha256: str | None = Field(
        default=None,
        pattern=SHA256_PATTERN,
    )
    compose_canonicalization_schema_version: Literal[
        "phase0.compose-canonicalization.v1"
    ] | None = None

    @field_validator("acquired_at")
    @classmethod
    def require_utc_acquisition(cls, value: datetime) -> datetime:
        if value.utcoffset() is None or value.utcoffset().total_seconds() != 0:
            raise ValueError("image acquisition timestamp must be UTC")
        return value


class ImageLockManifest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[
        "phase0.image-lock.v1",
        "phase0.image-lock.v2",
    ]
    status: ImageLockStatus
    upstream_tag: str
    upstream_commit: str
    compose_config_sha256: str | None = Field(
        default=None,
        pattern=SHA256_PATTERN,
    )
    canonical_compose_contract_sha256: str | None = Field(
        default=None,
        pattern=SHA256_PATTERN,
    )
    compose_canonicalization_schema_version: Literal[
        "phase0.compose-canonicalization.v1"
    ] | None = None
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
                or self.canonical_compose_contract_sha256 is not None
                or self.compose_canonicalization_schema_version is not None
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
        if self.schema_version == "phase0.image-lock.v1":
            if (
                self.compose_config_sha256 is None
                or self.canonical_compose_contract_sha256 is not None
                or self.compose_canonicalization_schema_version is not None
            ):
                raise ValueError(
                    "legacy image lock requires only its runtime Compose hash"
                )
        elif (
            self.compose_config_sha256 is not None
            or self.canonical_compose_contract_sha256 is None
            or self.compose_canonicalization_schema_version
            != COMPOSE_CANONICALIZATION_SCHEMA_VERSION
        ):
            raise ValueError(
                "v2 image lock requires only its canonical Compose binding"
            )
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
            if self.schema_version == "phase0.image-lock.v1":
                if (
                    image.compose_config_sha256
                    != self.compose_config_sha256
                    or image.canonical_compose_contract_sha256 is not None
                    or image.compose_canonicalization_schema_version is not None
                ):
                    raise ValueError("legacy image entry Compose hash mismatch")
            elif (
                image.compose_config_sha256 is not None
                or image.canonical_compose_contract_sha256
                != self.canonical_compose_contract_sha256
                or image.compose_canonicalization_schema_version
                != self.compose_canonicalization_schema_version
            ):
                raise ValueError(
                    "image entry canonical Compose binding mismatch"
                )
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

    schema_version: Literal["phase0.image-lock-rotation.v2"]
    rotation_reason: Literal[
        "COMPOSE_OVERRIDE_CHANGED",
        "RUN_INVARIANT_COMPOSE_CONTRACT_MIGRATION",
    ]
    old_lock_schema_version: Literal[
        "phase0.image-lock.v1",
        "phase0.image-lock.v2",
    ]
    new_lock_schema_version: Literal["phase0.image-lock.v2"]
    old_compose_binding_kind: Literal[
        "runtime_compose_instance_sha256",
        "canonical_compose_contract_sha256",
    ]
    old_compose_binding_sha256: str = Field(pattern=SHA256_PATTERN)
    new_canonical_compose_contract_sha256: str = Field(
        pattern=SHA256_PATTERN
    )
    runtime_compose_instance_sha256: str = Field(pattern=SHA256_PATTERN)
    compose_canonicalization_schema_version: Literal[
        "phase0.compose-canonicalization.v1"
    ]
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
            canonical_compose_contract_sha256=(
                resolved_compose.canonical_compose_contract_sha256
            ),
            compose_canonicalization_schema_version=(
                resolved_compose.canonicalization_schema_version
            ),
        )
        for image in images
    )
    return ImageLockManifest(
        schema_version="phase0.image-lock.v2",
        status=ImageLockStatus.LOCKED,
        upstream_tag=UPSTREAM_TAG,
        upstream_commit=UPSTREAM_COMMIT,
        canonical_compose_contract_sha256=(
            resolved_compose.canonical_compose_contract_sha256
        ),
        compose_canonicalization_schema_version=(
            resolved_compose.canonicalization_schema_version
        ),
        created_at=acquired_at,
        allowed_source_references=resolved_compose.image_references,
        images=entries,
    )


def verify_acceptance_image_lock(
    lock: ImageLockManifest,
    *,
    cached_images: tuple[InspectedImage, ...],
    observed_upstream_commit: str,
    observed_canonical_compose_contract_sha256: str,
    observed_canonicalization_schema_version: str,
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
    if lock.schema_version == "phase0.image-lock.v1":
        compose_binding = False
        reasons.append("IMAGE_LOCK_CANONICALIZATION_REQUIRED")
    else:
        schema_binding = (
            observed_canonicalization_schema_version
            == lock.compose_canonicalization_schema_version
        )
        compose_binding = (
            schema_binding
            and observed_canonical_compose_contract_sha256
            == lock.canonical_compose_contract_sha256
        )
        if not schema_binding:
            reasons.append("COMPOSE_CANONICALIZATION_SCHEMA_MISMATCH")
        elif not compose_binding:
            reasons.append("COMPOSE_CONTRACT_HASH_MISMATCH")

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
        or rotation_reason
        not in {
            "COMPOSE_OVERRIDE_CHANGED",
            "RUN_INVARIANT_COMPOSE_CONTRACT_MIGRATION",
        }
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
    if (
        original.schema_version == "phase0.image-lock.v1"
        and rotation_reason
        != "RUN_INVARIANT_COMPOSE_CONTRACT_MIGRATION"
    ) or (
        original.schema_version == "phase0.image-lock.v2"
        and rotation_reason != "COMPOSE_OVERRIDE_CHANGED"
    ):
        raise ValueError("image lock rotation reason does not match lock schema")
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
        observed_canonical_compose_contract_sha256=(
            original.canonical_compose_contract_sha256
            or resolved_compose.canonical_compose_contract_sha256
        ),
        observed_canonicalization_schema_version=(
            resolved_compose.canonicalization_schema_version
        ),
    )
    legacy_migration_verified = (
        original.schema_version == "phase0.image-lock.v1"
        and old_verification.reason_codes
        == ("IMAGE_LOCK_CANONICALIZATION_REQUIRED",)
        and old_verification.checks.source_references
        and old_verification.checks.digests
        and old_verification.checks.platforms
        and old_verification.checks.image_ids
        and old_verification.checks.upstream_binding
        and old_verification.checks.complete_inventory
    )
    if not old_verification.passed and not legacy_migration_verified:
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
        observed_canonical_compose_contract_sha256=(
            resolved_compose.canonical_compose_contract_sha256
        ),
        observed_canonicalization_schema_version=(
            resolved_compose.canonicalization_schema_version
        ),
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
            observed_canonical_compose_contract_sha256=(
                resolved_compose.canonical_compose_contract_sha256
            ),
            observed_canonicalization_schema_version=(
                resolved_compose.canonicalization_schema_version
            ),
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
            schema_version="phase0.image-lock-rotation.v2",
            rotation_reason=rotation_reason,
            old_lock_schema_version=original.schema_version,
            new_lock_schema_version="phase0.image-lock.v2",
            old_compose_binding_kind=(
                "runtime_compose_instance_sha256"
                if original.schema_version == "phase0.image-lock.v1"
                else "canonical_compose_contract_sha256"
            ),
            old_compose_binding_sha256=(
                original.compose_config_sha256
                or original.canonical_compose_contract_sha256
                or ("0" * 64)
            ),
            new_canonical_compose_contract_sha256=(
                resolved_compose.canonical_compose_contract_sha256
            ),
            runtime_compose_instance_sha256=(
                resolved_compose.runtime_compose_instance_sha256
            ),
            compose_canonicalization_schema_version=(
                resolved_compose.canonicalization_schema_version
            ),
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
