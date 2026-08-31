"""Phase-aware repository acceptance for Product v0.2.3.2.3."""

from __future__ import annotations

from enum import Enum
import hashlib
import os
from pathlib import Path
from typing import Literal

from pydantic import ConfigDict, Field, field_validator, model_validator

from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22
from ecomsre.product.contracts import ProductModelV1


REPOSITORY_STATE_MODEL_PASS_V02323: Literal[
    "ECOMSRE_PRODUCT_V02323_REPOSITORY_STATE_MODEL_PASS"
] = "ECOMSRE_PRODUCT_V02323_REPOSITORY_STATE_MODEL_PASS"
_SHA256_PATTERN = r"^[0-9a-f]{64}$"


class RepositoryPhaseV02323(str, Enum):
    PRE_FORMAL = "PRE_FORMAL"
    FORMAL_RUNNING = "FORMAL_RUNNING"
    FORMAL_BLOCKED_DIAGNOSIS = "FORMAL_BLOCKED_DIAGNOSIS"
    FORENSIC_SOURCE_BLOCKED = "FORENSIC_SOURCE_BLOCKED"
    SCHEMA8_RECONSTRUCTION_COMPLETE = "SCHEMA8_RECONSTRUCTION_COMPLETE"
    DIAGNOSIS_REPLAY_COMPLETE = "DIAGNOSIS_REPLAY_COMPLETE"
    MEASURED_COMPLETE = "MEASURED_COMPLETE"


class FrozenRepositoryArtifactV02323(ProductModelV1):
    model_config = ConfigDict(extra="forbid", frozen=True)

    role: str = Field(min_length=1, max_length=100)
    path: str = Field(min_length=1, max_length=512)
    file_sha256: str = Field(pattern=_SHA256_PATTERN)
    size_bytes: int = Field(ge=1)

    @field_validator("path")
    @classmethod
    def path_is_repository_relative(cls, value: str) -> str:
        relative = Path(value)
        if (
            relative.is_absolute()
            or ".." in relative.parts
            or value != relative.as_posix()
        ):
            raise ValueError("repository artifact path differs")
        return value


class ProductV02323RepositoryStateManifest(ProductModelV1):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["ecomsre.product.repository-state.v02323"] = (
        "ecomsre.product.repository-state.v02323"
    )
    goal_version: Literal[
        "ecomsre-product-v02323-schema8-reconstruction-diagnosis-replay-v1"
    ] = "ecomsre-product-v02323-schema8-reconstruction-diagnosis-replay-v1"
    phase: RepositoryPhaseV02323
    pr83_formal_blocker_sha256: str | None = Field(
        default=None, pattern=_SHA256_PATTERN
    )
    pr84_private_state_contract_sha256: str | None = Field(
        default=None, pattern=_SHA256_PATTERN
    )
    forensic_source_snapshot_sha256: str | None = Field(
        default=None, pattern=_SHA256_PATTERN
    )
    reconstruction_disposition_sha256: str | None = Field(
        default=None, pattern=_SHA256_PATTERN
    )
    replay_result_sha256: str | None = Field(default=None, pattern=_SHA256_PATTERN)
    measured_nofault_result_sha256: str | None = Field(
        default=None, pattern=_SHA256_PATTERN
    )
    allowed_artifacts: tuple[FrozenRepositoryArtifactV02323, ...] = Field(min_length=1)
    forbidden_artifacts: tuple[str, ...]
    manifest_sha256: str = Field(pattern=_SHA256_PATTERN)

    @field_validator("forbidden_artifacts")
    @classmethod
    def forbidden_paths_are_safe(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for item in value:
            relative = Path(item)
            if (
                relative.is_absolute()
                or ".." in relative.parts
                or item != relative.as_posix()
            ):
                raise ValueError("forbidden repository path differs")
        if tuple(sorted(set(value))) != value:
            raise ValueError("forbidden repository paths differ")
        return value

    @model_validator(mode="after")
    def phase_and_seal_are_exact(self) -> ProductV02323RepositoryStateManifest:
        allowed_paths = tuple(item.path for item in self.allowed_artifacts)
        allowed_roles = tuple(item.role for item in self.allowed_artifacts)
        if (
            tuple(sorted(allowed_paths)) != allowed_paths
            or len(set(allowed_paths)) != len(allowed_paths)
            or len(set(allowed_roles)) != len(allowed_roles)
            or set(allowed_paths).intersection(self.forbidden_artifacts)
        ):
            raise ValueError("phase artifact contract differs")

        observed = (
            self.pr83_formal_blocker_sha256,
            self.pr84_private_state_contract_sha256,
            self.forensic_source_snapshot_sha256,
            self.reconstruction_disposition_sha256,
            self.replay_result_sha256,
            self.measured_nofault_result_sha256,
        )
        required_prefix_by_phase = {
            RepositoryPhaseV02323.PRE_FORMAL: 0,
            RepositoryPhaseV02323.FORMAL_RUNNING: 0,
            RepositoryPhaseV02323.FORMAL_BLOCKED_DIAGNOSIS: 1,
            RepositoryPhaseV02323.FORENSIC_SOURCE_BLOCKED: 3,
            RepositoryPhaseV02323.SCHEMA8_RECONSTRUCTION_COMPLETE: 4,
            RepositoryPhaseV02323.DIAGNOSIS_REPLAY_COMPLETE: 5,
            RepositoryPhaseV02323.MEASURED_COMPLETE: 6,
        }
        required_prefix = required_prefix_by_phase[self.phase]
        if any(item is None for item in observed[:required_prefix]) or any(
            item is not None for item in observed[required_prefix:]
        ):
            raise ValueError("phase artifact contract differs")

        body = self.model_dump(mode="json", exclude={"manifest_sha256"})
        if self.manifest_sha256 != semantic_sha256_v22(body):
            raise ValueError("repository state manifest digest differs")
        return self

    @classmethod
    def load(cls, path: Path) -> ProductV02323RepositoryStateManifest:
        return cls.model_validate_json(path.read_text(encoding="utf-8"))


def sha256_file_v02323(path: Path) -> tuple[str, int]:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"repository artifact is not a regular file: {path}")
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            size += len(chunk)
            digest.update(chunk)
    return digest.hexdigest(), size


def verify_repository_state_v02323(
    root: Path,
    *,
    manifest: ProductV02323RepositoryStateManifest | None = None,
    manifest_path: Path | None = None,
) -> dict[str, object]:
    project = Path(root).resolve(strict=True)
    state = manifest or ProductV02323RepositoryStateManifest.load(
        manifest_path
        or project / "config/product-v02323/repository-state-manifest.json"
    )
    for artifact in state.allowed_artifacts:
        observed_sha256, observed_size = sha256_file_v02323(project / artifact.path)
        if (
            observed_sha256 != artifact.file_sha256
            or observed_size != artifact.size_bytes
        ):
            raise ValueError(f"repository artifact bytes differ: {artifact.path}")
    for relative in state.forbidden_artifacts:
        if os.path.lexists(project / relative):
            raise ValueError(f"forbidden repository phase artifact exists: {relative}")

    authority = (
        "MEASURED" if state.phase is RepositoryPhaseV02323.MEASURED_COMPLETE else "NONE"
    )
    return {
        "terminal": REPOSITORY_STATE_MODEL_PASS_V02323,
        "phase": state.phase.value,
        "pr83_formal_blocker_sha256": state.pr83_formal_blocker_sha256,
        "pr84_private_state_contract_sha256": (
            state.pr84_private_state_contract_sha256
        ),
        "reconstruction_disposition_sha256": (state.reconstruction_disposition_sha256),
        "replay_result_sha256": state.replay_result_sha256,
        "measured_nofault_authority": authority,
        "knowledge_loop_authority": authority,
    }


__all__ = (
    "REPOSITORY_STATE_MODEL_PASS_V02323",
    "FrozenRepositoryArtifactV02323",
    "ProductV02323RepositoryStateManifest",
    "RepositoryPhaseV02323",
    "sha256_file_v02323",
    "verify_repository_state_v02323",
)
