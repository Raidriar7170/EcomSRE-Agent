"""Stable image authority and run-scoped Compose identity for live E2E v3."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal, Mapping

from pydantic import Field, model_validator

from ecomsre_live_sandbox.contracts import (
    FrozenModel,
    canonical_sha256,
    write_private_json,
)


_DIGEST_PATTERN = r"^sha256:[0-9a-f]{64}$"
_SHA256_PATTERN = r"^[0-9a-f]{64}$"
PRIVATE_FLAGD_PLACEHOLDER = "${ECOMSRE_PRIVATE_FLAGD_DIR}"
COMPOSE_NORMALIZATION_POLICY = {
    "schema_version": "live-e2e.compose-normalization-policy.v3",
    "allowed_services": ["flagd", "flagd-ui"],
    "allowed_mount_key": "source",
    "replacement": PRIVATE_FLAGD_PLACEHOLDER,
    "required_normalized_bind_count": 2,
    "unexpected_private_root_path_policy": "FAIL_CLOSED",
}
COMPOSE_NORMALIZATION_POLICY_SHA256 = canonical_sha256(COMPOSE_NORMALIZATION_POLICY)


class ImageAuthorityMismatch(RuntimeError):
    """Current cached images differ from the create-once shared authority."""


class ComposeIdentityMismatch(RuntimeError):
    """Resolved Compose cannot be normalized by the frozen allowlist."""


class CachedImage(FrozenModel):
    source_reference: str = Field(min_length=1)
    image_id: str = Field(pattern=_DIGEST_PATTERN)
    image_index_digest: str = Field(pattern=_DIGEST_PATTERN)
    resolved_platform_digest: str = Field(pattern=_DIGEST_PATTERN)
    raw_inspect_sha256: str = Field(pattern=_SHA256_PATTERN)


class CachedImageInspection(FrozenModel):
    historical_image_lock_sha256: str = Field(pattern=_SHA256_PATTERN)
    upstream_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    upstream_tag: Literal["3.0.0"]
    platform: Literal["linux/arm64"]
    images: tuple[CachedImage, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_sorted_sources(self) -> "CachedImageInspection":
        sources = tuple(image.source_reference for image in self.images)
        if sources != tuple(sorted(set(sources))):
            raise ValueError("cached image inspection sources must be unique and sorted")
        return self


class AuthorityImage(FrozenModel):
    source_reference: str = Field(min_length=1)
    image_id: str = Field(pattern=_DIGEST_PATTERN)
    image_index_digest: str = Field(pattern=_DIGEST_PATTERN)
    resolved_platform_digest: str = Field(pattern=_DIGEST_PATTERN)


class ImageAuthority(FrozenModel):
    schema_version: Literal["live-e2e.image-authority.v3"]
    authority_version: Literal["live-fault-a0-controlled-remediation-e2e-v3"]
    historical_image_lock_sha256: str = Field(pattern=_SHA256_PATTERN)
    upstream_commit: Literal["1755859a9de82c2e5e225be68abc401a5ebf2b4f"]
    upstream_tag: Literal["3.0.0"]
    platform: Literal["linux/arm64"]
    source_references: tuple[str, ...] = Field(min_length=1)
    source_reference_set_sha256: str = Field(pattern=_SHA256_PATTERN)
    images: tuple[AuthorityImage, ...] = Field(min_length=1)
    authority_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def verify_semantics(self) -> "ImageAuthority":
        sources = tuple(image.source_reference for image in self.images)
        if self.source_references != sources or sources != tuple(sorted(set(sources))):
            raise ValueError("image authority source set is not unique and sorted")
        if self.source_reference_set_sha256 != canonical_sha256(self.source_references):
            raise ValueError("image authority source-set hash differs")
        core = self.model_dump(mode="json")
        core.pop("authority_sha256")
        if self.authority_sha256 != canonical_sha256(core):
            raise ValueError("image authority semantic hash differs")
        return self


class ComposeIdentities(FrozenModel):
    instance_sha256: str = Field(pattern=_SHA256_PATTERN)
    structure_sha256: str = Field(pattern=_SHA256_PATTERN)
    normalization_policy_sha256: str = Field(pattern=_SHA256_PATTERN)
    normalized_bind_count: Literal[2]


class RunImageVerification(FrozenModel):
    schema_version: Literal["live-e2e.run-image-verification.v3"]
    run_id: str = Field(min_length=1)
    run_kind: Literal["DIAGNOSTIC_PROBE", "CANONICAL_INVOCATION_A", "INVOCATION_B"]
    image_authority_sha256: str = Field(pattern=_SHA256_PATTERN)
    historical_image_lock_sha256: str = Field(pattern=_SHA256_PATTERN)
    compose_structure_sha256: str = Field(pattern=_SHA256_PATTERN)
    compose_instance_sha256: str = Field(pattern=_SHA256_PATTERN)
    normalization_policy_sha256: str = Field(pattern=_SHA256_PATTERN)
    source_reference_set_sha256: str = Field(pattern=_SHA256_PATTERN)
    inspected_image_count: int = Field(ge=1)
    all_images_match_authority: Literal[True]
    private_raw_inspect_artifact_hashes: tuple[str, ...] = Field(min_length=1)
    verification_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def verify_semantic_hash(self) -> "RunImageVerification":
        core = self.model_dump(mode="json")
        core.pop("verification_sha256")
        if self.verification_sha256 != canonical_sha256(core):
            raise ValueError("run image verification semantic hash differs")
        return self


def _authority_from_inspection(inspection: CachedImageInspection) -> ImageAuthority:
    images = tuple(
        AuthorityImage(
            source_reference=image.source_reference,
            image_id=image.image_id,
            image_index_digest=image.image_index_digest,
            resolved_platform_digest=image.resolved_platform_digest,
        )
        for image in inspection.images
    )
    sources = tuple(image.source_reference for image in images)
    core = {
        "schema_version": "live-e2e.image-authority.v3",
        "authority_version": "live-fault-a0-controlled-remediation-e2e-v3",
        "historical_image_lock_sha256": inspection.historical_image_lock_sha256,
        "upstream_commit": inspection.upstream_commit,
        "upstream_tag": inspection.upstream_tag,
        "platform": inspection.platform,
        "source_references": sources,
        "source_reference_set_sha256": canonical_sha256(sources),
        "images": [image.model_dump(mode="json") for image in images],
    }
    return ImageAuthority.model_validate(
        {**core, "authority_sha256": canonical_sha256(core)}
    )


def ensure_image_authority(
    path: Path, inspection: CachedImageInspection
) -> ImageAuthority:
    expected = _authority_from_inspection(inspection)
    if path.exists() or path.is_symlink():
        if path.is_symlink() or not path.is_file():
            raise ImageAuthorityMismatch("shared image authority is not a regular file")
        try:
            current = ImageAuthority.model_validate_json(path.read_text(encoding="utf-8"))
        except Exception as error:
            raise ImageAuthorityMismatch("shared image authority is malformed") from error
        if current != expected:
            raise ImageAuthorityMismatch("current cached images differ from shared authority")
        return current
    write_private_json(path, expected, create_once=True)
    return expected


def _is_relative_to(value: str, root: Path) -> bool:
    try:
        return Path(value).resolve(strict=False).is_relative_to(root.resolve(strict=False))
    except (OSError, RuntimeError, ValueError):
        return False


def compose_identities(
    resolved_compose: Mapping[str, object],
    *,
    private_root: Path,
    flagd_directory: Path,
) -> ComposeIdentities:
    raw = json.loads(json.dumps(resolved_compose, allow_nan=False))
    if not isinstance(raw, dict):
        raise ComposeIdentityMismatch("resolved Compose must be an object")
    instance_sha256 = canonical_sha256(raw)
    normalized_bind_count = 0
    normalized_services: set[str] = set()

    def normalize(value: Any, path: tuple[object, ...]) -> Any:
        nonlocal normalized_bind_count
        if isinstance(value, dict):
            return {key: normalize(item, (*path, key)) for key, item in value.items()}
        if isinstance(value, list):
            return [normalize(item, (*path, index)) for index, item in enumerate(value)]
        if isinstance(value, str) and _is_relative_to(value, private_root):
            allowed = (
                len(path) == 5
                and path[0] == "services"
                and path[1] in {"flagd", "flagd-ui"}
                and path[2] == "volumes"
                and isinstance(path[3], int)
                and path[4] == "source"
                and Path(value).resolve(strict=False)
                == flagd_directory.resolve(strict=False)
            )
            if allowed:
                volume_index = path[3]
                assert isinstance(volume_index, int)
                services = raw.get("services")
                service = services.get(path[1]) if isinstance(services, dict) else None
                volumes = service.get("volumes") if isinstance(service, dict) else None
                volume = (
                    volumes[volume_index]
                    if isinstance(volumes, list) and volume_index < len(volumes)
                    else None
                )
                allowed = isinstance(volume, dict) and volume.get("type") == "bind"
            if not allowed:
                raise ComposeIdentityMismatch(
                    "resolved Compose contains an unexpected private-root path"
                )
            normalized_bind_count += 1
            normalized_services.add(str(path[1]))
            return PRIVATE_FLAGD_PLACEHOLDER
        return value

    normalized = normalize(raw, ())
    if normalized_bind_count != 2 or normalized_services != {"flagd", "flagd-ui"}:
        raise ComposeIdentityMismatch("resolved Compose does not contain both flagd binds")
    return ComposeIdentities(
        instance_sha256=instance_sha256,
        structure_sha256=canonical_sha256(normalized),
        normalization_policy_sha256=COMPOSE_NORMALIZATION_POLICY_SHA256,
        normalized_bind_count=2,
    )


def write_run_image_verification(
    path: Path,
    *,
    run_id: str,
    run_kind: str,
    authority: ImageAuthority,
    inspection: CachedImageInspection,
    resolved_compose: Mapping[str, object],
    private_root: Path,
    flagd_directory: Path,
) -> RunImageVerification:
    if authority != _authority_from_inspection(inspection):
        raise ImageAuthorityMismatch("run inspection differs from shared image authority")
    identities = compose_identities(
        resolved_compose,
        private_root=private_root,
        flagd_directory=flagd_directory,
    )
    core = {
        "schema_version": "live-e2e.run-image-verification.v3",
        "run_id": run_id,
        "run_kind": run_kind,
        "image_authority_sha256": authority.authority_sha256,
        "historical_image_lock_sha256": inspection.historical_image_lock_sha256,
        "compose_structure_sha256": identities.structure_sha256,
        "compose_instance_sha256": identities.instance_sha256,
        "normalization_policy_sha256": identities.normalization_policy_sha256,
        "source_reference_set_sha256": authority.source_reference_set_sha256,
        "inspected_image_count": len(inspection.images),
        "all_images_match_authority": True,
        "private_raw_inspect_artifact_hashes": tuple(
            image.raw_inspect_sha256 for image in inspection.images
        ),
    }
    verification = RunImageVerification.model_validate(
        {**core, "verification_sha256": canonical_sha256(core)}
    )
    write_private_json(path, verification, create_once=True)
    return verification


__all__ = [
    "COMPOSE_NORMALIZATION_POLICY",
    "COMPOSE_NORMALIZATION_POLICY_SHA256",
    "CachedImage",
    "CachedImageInspection",
    "ComposeIdentities",
    "ComposeIdentityMismatch",
    "ImageAuthority",
    "ImageAuthorityMismatch",
    "RunImageVerification",
    "compose_identities",
    "ensure_image_authority",
    "write_run_image_verification",
]
