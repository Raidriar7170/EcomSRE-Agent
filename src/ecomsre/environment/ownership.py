"""Project-scoped ownership proof for mutable local resources."""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


PROJECT_NAMESPACE = "ecomsre-phase0"
PROJECT_LABEL = "io.ecomsre.project"
RUN_LABEL = "io.ecomsre.run"


class OwnershipError(RuntimeError):
    """Raised when project ownership cannot be proven."""


class OwnedResource(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: str = Field(min_length=1)
    name: str = Field(min_length=1)
    resource_id: str = Field(min_length=1)
    labels: dict[str, str]
    identity_evidence: tuple[str, ...] = ()


class OwnershipManifest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    namespace: Literal["ecomsre-phase0"] = PROJECT_NAMESPACE
    run_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    resources: tuple[OwnedResource, ...]

    @model_validator(mode="after")
    def require_manifest_labels(self) -> "OwnershipManifest":
        if not self.is_consistent():
            raise ValueError("RESOURCE_OWNERSHIP_UNKNOWN: manifest is inconsistent")
        return self

    def is_consistent(self) -> bool:
        if (
            self.namespace != PROJECT_NAMESPACE
            or re.fullmatch(r"[0-9a-f]{32}", self.run_id) is None
        ):
            return False
        identities: set[tuple[str, str]] = set()
        for resource in self.resources:
            identity = (resource.kind, resource.resource_id)
            if identity in identities:
                return False
            identities.add(identity)
            if resource.kind == "port":
                legacy_tcp_port = re.fullmatch(
                    r"tcp:(?:[1-9]\d{0,4})",
                    resource.resource_id,
                )
                stable_binding = re.fullmatch(
                    r"port-binding:[0-9a-f]{64}",
                    resource.resource_id,
                )
                if legacy_tcp_port is not None:
                    if (
                        resource.name != resource.resource_id
                        or int(resource.resource_id.removeprefix("tcp:")) > 65535
                    ):
                        return False
                elif stable_binding is None or not all(
                    any(
                        evidence.startswith(prefix)
                        for evidence in resource.identity_evidence
                    )
                    for prefix in (
                        f"port:{resource.resource_id}",
                        "container:",
                        "container_name:",
                        "service:",
                        "host_ip:",
                        "host_family:",
                        "published_port:",
                        "target_port:",
                        "protocol:",
                        "binding:",
                        "raw_binding:",
                    )
                ):
                    return False
            if (
                resource.labels.get(PROJECT_LABEL) != self.namespace
                or resource.labels.get(RUN_LABEL) != self.run_id
            ):
                return False
        return True


def verify_owned_resources(
    discovered: tuple[OwnedResource, ...],
    manifest: OwnershipManifest,
) -> tuple[OwnedResource, ...]:
    """Return resources only when discovery and manifest prove ownership."""
    manifest_by_identity = {
        (resource.kind, resource.resource_id): resource
        for resource in manifest.resources
    }

    if len(manifest_by_identity) != len(manifest.resources):
        raise OwnershipError("RESOURCE_OWNERSHIP_UNKNOWN: duplicate manifest identity")

    discovered_identities: set[tuple[str, str]] = set()
    for resource in discovered:
        identity = (resource.kind, resource.resource_id)
        if identity in discovered_identities:
            raise OwnershipError(
                "RESOURCE_OWNERSHIP_UNKNOWN: duplicate discovered identity"
            )
        discovered_identities.add(identity)
        expected = manifest_by_identity.get(identity)
        if expected is None or expected.name != resource.name:
            raise OwnershipError(
                f"RESOURCE_OWNERSHIP_UNKNOWN: unrecorded {resource.kind} "
                f"{resource.name}"
            )
        if expected.identity_evidence != resource.identity_evidence:
            raise OwnershipError(
                "RESOURCE_OWNERSHIP_UNKNOWN: stable identity evidence differs "
                f"for {resource.name}"
            )
        if (
            resource.labels.get(PROJECT_LABEL) != PROJECT_NAMESPACE
            or resource.labels.get(RUN_LABEL) != manifest.run_id
        ):
            raise OwnershipError(
                f"RESOURCE_OWNERSHIP_UNKNOWN: conflicting labels on {resource.name}"
            )

    if discovered_identities != set(manifest_by_identity):
        raise OwnershipError(
            "RESOURCE_OWNERSHIP_UNKNOWN: discovery does not match manifest"
        )
    return discovered
