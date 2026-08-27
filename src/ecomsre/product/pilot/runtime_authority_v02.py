from __future__ import annotations

import json
import os
from pathlib import Path
import stat
from typing import Literal

from pydantic import Field, model_validator

from ecomsre.dta_v2.tool_contracts import (
    ReadAuthorityContext,
    ReadAuthorityMode,
)
from ecomsre.dta_v2.tool_contracts import semantic_sha256 as authority_sha256
from ecomsre.product.pilot.contracts_v02 import PilotModelV02, semantic_sha256_v02


class PilotRuntimeAuthorityV02(PilotModelV02):
    schema_version: Literal["ecomsre.product.pilot.runtime-authority.v02"] = (
        "ecomsre.product.pilot.runtime-authority.v02"
    )
    environment_id: str = Field(min_length=1, max_length=160)
    allowed_logical_services: tuple[str, ...] = Field(min_length=1, max_length=4)
    profile_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    read_authority: ReadAuthorityContext
    pilot_authority_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_authority(self) -> "PilotRuntimeAuthorityV02":
        if self.allowed_logical_services != tuple(
            sorted(set(self.allowed_logical_services))
        ):
            raise ValueError("pilot Runtime authority services must be canonical")
        if self.read_authority.mode is not ReadAuthorityMode.OWNED_LOCAL:
            raise ValueError("pilot Runtime authority must be OWNED_LOCAL")
        expected = semantic_sha256_v02(
            self.model_dump(mode="json", exclude={"pilot_authority_sha256"})
        )
        if self.pilot_authority_sha256 != expected:
            raise ValueError("pilot Runtime authority digest differs")
        return self

    @classmethod
    def build(
        cls,
        *,
        environment_id: str,
        allowed_logical_services: tuple[str, ...],
        profile_sha256: str,
        daemon_identity_sha256: str,
        docker_context_sha256: str,
        config_bundle_sha256: str,
        resolved_sandbox_sha256: str,
        resolved_endpoints_sha256: str,
        ownership_scope_sha256: str,
    ) -> "PilotRuntimeAuthorityV02":
        authority_payload = {
            "schema_version": "dta-v2.read-authority.v1",
            "mode": ReadAuthorityMode.OWNED_LOCAL,
            "daemon_identity_sha256": daemon_identity_sha256,
            "docker_context_sha256": docker_context_sha256,
            "config_bundle_sha256": config_bundle_sha256,
            "resolved_sandbox_sha256": resolved_sandbox_sha256,
            "resolved_endpoints_sha256": resolved_endpoints_sha256,
            "ownership_scope_sha256": ownership_scope_sha256,
        }
        read_authority = ReadAuthorityContext.model_validate(
            {
                **authority_payload,
                "authority_sha256": authority_sha256(authority_payload),
            }
        )
        payload = {
            "schema_version": "ecomsre.product.pilot.runtime-authority.v02",
            "environment_id": environment_id,
            "allowed_logical_services": tuple(sorted(allowed_logical_services)),
            "profile_sha256": profile_sha256,
            "read_authority": read_authority,
        }
        draft = cls.model_construct(
            **payload,  # type: ignore[arg-type]
            pilot_authority_sha256="0" * 64,
        )
        return cls.model_validate(
            {
                **payload,
                "pilot_authority_sha256": semantic_sha256_v02(
                    draft.model_dump(
                        mode="json", exclude={"pilot_authority_sha256"}
                    )
                ),
            }
        )

    def admits(self, *, environment_id: str, services: tuple[str, ...]) -> bool:
        return (
            environment_id == self.environment_id
            and bool(services)
            and set(services).issubset(self.allowed_logical_services)
        )

    @property
    def connector_binding_sha256(self) -> str:
        return semantic_sha256_v02(
            {
                "schema_version": "ecomsre.product.pilot.runtime-binding.v02",
                "profile_sha256": self.profile_sha256,
                "read_authority_sha256": self.read_authority.authority_sha256,
            }
        )


def load_pilot_runtime_authority_v02(path: Path) -> PilotRuntimeAuthorityV02:
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags)
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise ValueError("pilot Runtime authority is not a regular file")
        with os.fdopen(os.dup(descriptor), "rb") as handle:
            payload = handle.read()
    finally:
        os.close(descriptor)
    return PilotRuntimeAuthorityV02.model_validate_json(payload)


def write_pilot_runtime_authority_v02(
    path: Path,
    authority: PilotRuntimeAuthorityV02,
) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if destination.exists():
        raise FileExistsError("pilot Runtime authority is create-once")
    descriptor = os.open(
        destination,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
        0o600,
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", closefd=False) as handle:
            json.dump(
                authority.model_dump(mode="json"),
                handle,
                sort_keys=True,
                separators=(",", ":"),
            )
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        os.close(descriptor)


__all__ = (
    "PilotRuntimeAuthorityV02",
    "load_pilot_runtime_authority_v02",
    "write_pilot_runtime_authority_v02",
)
