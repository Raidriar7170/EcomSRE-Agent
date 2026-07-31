"""Read-only preflight facts and stable fail-closed classification."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import stat
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from ecomsre.environment.manifests import (
    COMPOSE_CANONICALIZATION_SCHEMA_VERSION,
    EXPECTED_PLATFORM,
    InspectedImage,
    ResolvedComposeConfig,
    UPSTREAM_COMMIT,
    LockVerification,
)
from ecomsre.environment.ownership import (
    PROJECT_LABEL,
    PROJECT_NAMESPACE,
    RUN_LABEL,
    OwnershipManifest,
)
from ecomsre.environment.ownership_authority import (
    AuthenticatedOwnershipContext,
)
from ecomsre.evidence.hashes import (
    canonical_json_bytes,
    canonical_json_sha256,
)
from ecomsre.phase0.models import Outcome


_DOCKER_SETTINGS_RELATIVE_PATH = Path(
    "Library/Group Containers/group.com.docker/settings-store.json"
)
_MAX_DOCKER_SETTINGS_BYTES = 4 * 1024 * 1024


GIB = 1024**3
MINIMUM_MEMORY_BYTES = 16 * GIB
MINIMUM_DISK_BYTES = 25 * GIB
PREFLIGHT_EVIDENCE_MAX_AGE_SECONDS = 30
DOCKER_DESKTOP_CONTEXT = "desktop-linux"
_DOCKER_CONTEXT_INSPECT = (
    "docker",
    "--context",
    DOCKER_DESKTOP_CONTEXT,
    "context",
    "inspect",
    DOCKER_DESKTOP_CONTEXT,
    "--format",
    "{{json .}}",
)
_PREFLIGHT_EVIDENCE_TOKEN = object()
_PREFLIGHT_EVIDENCE_KEY = secrets.token_bytes(32)


class CommandResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    arguments: tuple[str, ...]
    exit_code: int
    stdout: str
    stderr: str
    process_exit_code: int | None = None
    process_timed_out: bool = False
    stdout_artifact: str | None = None
    stdout_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    stderr_artifact: str | None = None
    stderr_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    command_log_artifact: str | None = None
    command_log_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_consistent_process_evidence(self) -> "CommandResult":
        if self.process_timed_out and self.process_exit_code is not None:
            raise ValueError("timed-out process cannot have an exit code")
        if (self.stdout_artifact is None) != (self.stdout_sha256 is None):
            raise ValueError("stdout artifact and hash must be recorded together")
        if (self.stderr_artifact is None) != (self.stderr_sha256 is None):
            raise ValueError("stderr artifact and hash must be recorded together")
        if (self.command_log_artifact is None) != (
            self.command_log_sha256 is None
        ):
            raise ValueError("command log artifact and hash must be recorded together")
        return self


class CommandRunner(Protocol):
    def run(
        self,
        arguments: tuple[str, ...],
        *,
        timeout_seconds: float,
    ) -> CommandResult: ...


class HostSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    macos_version: str
    macos_build: str
    architecture: str
    cpu_model: str
    cpu_count: int = Field(ge=1)
    total_memory_bytes: int = Field(ge=0)
    available_memory_bytes: int = Field(ge=0)
    available_disk_bytes: int = Field(ge=0)


class DockerSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    client_available: bool
    client_version: str
    daemon_available: bool
    server_version: str
    desktop_version: str
    engine: str
    desktop_identity_verified: bool
    compose_available: bool
    compose_version: str
    compose_plugin_v2: bool
    server_os_type: str
    server_architecture: str
    native_platform: str
    cpu_count: float = Field(ge=0)
    memory_bytes: int = Field(ge=0)
    disk_bytes: int = Field(ge=0)
    resource_fields_verified: bool
    settings_source_kind: Literal[
        "cli_export",
        "standard_file",
        "unavailable",
    ] = "unavailable"
    settings_content_sha256: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    settings_version: int | None = Field(default=None, ge=0)
    context_name: str
    endpoint: str
    daemon_id: str


OwnershipState = Literal["NONE", "OWNED", "KNOWN_OTHER", "UNKNOWN"]


class OwnershipProof(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    project_namespace: Literal["ecomsre-phase0"]
    manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    run_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    resource_kind: str = Field(min_length=1)
    resource_id: str = Field(min_length=1)
    port: int | None = Field(default=None, ge=1, le=65535)
    identifiers: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def bind_identifiers_to_resource(self) -> "OwnershipProof":
        required_identifier = f"{self.resource_kind}:{self.resource_id}"
        if required_identifier not in self.identifiers:
            raise ValueError("ownership proof identifier does not bind its resource")
        if self.resource_kind == "port":
            stable_binding = re.fullmatch(
                r"port-binding:[0-9a-f]{64}",
                self.resource_id,
            )
            if self.port is None or (
                self.resource_id != f"tcp:{self.port}" and stable_binding is None
            ):
                raise ValueError("port ownership proof is not bound to its port")
            published_identifier = f"published_port:{self.port}"
            if (
                stable_binding is not None
                and published_identifier not in self.identifiers
            ):
                raise ValueError("stable port proof does not bind its published port")
            process_prefixes = ("pid:", "start:", "executable:", "socket:")
            container_binding_prefixes = (
                "container:",
                "container_name:",
                "service:",
                "host_ip:",
                "published_port:",
                "target_port:",
                "protocol:",
                "binding:",
            )
            process_bound = all(
                any(value.startswith(prefix) for value in self.identifiers)
                for prefix in process_prefixes
            )
            container_bound = all(
                any(value.startswith(prefix) for value in self.identifiers)
                for prefix in container_binding_prefixes
            )
            if not process_bound and not container_bound:
                raise ValueError(
                    "port ownership proof lacks stable owner/binding identity"
                )
        elif self.port is not None:
            raise ValueError("non-port ownership proof cannot contain a port")
        if self.resource_kind == "process":
            required_prefixes = ("pid:", "start:", "executable:")
            if any(
                not any(value.startswith(prefix) for value in self.identifiers)
                for prefix in required_prefixes
            ):
                raise ValueError(
                    "process ownership proof lacks stable process identity"
                )
        if self.resource_kind in {"lock", "lock_file", "project_file"}:
            required_prefixes = (
                "path:",
                "device:",
                "inode:",
                "type:",
                "uid:",
            )
            if any(
                not any(value.startswith(prefix) for value in self.identifiers)
                for prefix in required_prefixes
            ):
                raise ValueError("path ownership proof lacks stable file identity")
        return self


class PortObservation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    port: int = Field(ge=1, le=65535)
    occupied: bool
    ownership: OwnershipState
    ownership_proof: OwnershipProof | None = None

    @model_validator(mode="after")
    def require_owned_port_proof(self) -> "PortObservation":
        if not self.occupied:
            if self.ownership != "NONE" or self.ownership_proof is not None:
                raise ValueError("unoccupied port must have ownership NONE")
            return self
        if self.ownership == "NONE":
            raise ValueError("occupied port cannot have ownership NONE")
        if self.ownership == "OWNED" and self.ownership_proof is None:
            raise ValueError("OWNED port requires manifest proof")
        if self.ownership != "OWNED" and self.ownership_proof is not None:
            raise ValueError("non-owned port cannot carry ownership proof")
        if self.ownership_proof is not None and (
            self.ownership_proof.resource_kind != "port"
            or self.ownership_proof.port != self.port
        ):
            raise ValueError("port ownership proof does not match observed port")
        return self


class ResourceObservation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: str
    name: str
    resource_id: str = ""
    labels: dict[str, str] = Field(default_factory=dict)
    present: bool = True
    ownership: OwnershipState
    ownership_proof: OwnershipProof | None = None

    @model_validator(mode="after")
    def require_owned_resource_proof(self) -> "ResourceObservation":
        if not self.present:
            if (
                self.ownership != "NONE"
                or self.resource_id
                or self.labels
                or self.ownership_proof is not None
            ):
                raise ValueError("absent resource must have ownership NONE")
            return self
        if self.ownership == "NONE":
            raise ValueError("present resource cannot have ownership NONE")
        if self.ownership == "OWNED" and self.ownership_proof is None:
            raise ValueError("OWNED resource requires manifest proof")
        if self.ownership != "OWNED" and self.ownership_proof is not None:
            raise ValueError("non-owned resource cannot carry ownership proof")
        if self.ownership_proof is not None and (
            self.ownership_proof.resource_kind != self.kind
            or self.ownership_proof.resource_id != self.resource_id
        ):
            raise ValueError(
                "resource ownership proof does not match observed resource"
            )
        return self


class DiscoveryCommand(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    purpose: str
    arguments: tuple[str, ...]
    read_only: Literal[True] = True
    timeout_seconds: float = Field(default=10, gt=0)


class DiscoveryParseError(RuntimeError):
    """A read-only discovery result lacked evidence needed for safe use."""

    def __init__(
        self,
        message: str,
        *,
        outcome: Outcome = Outcome.UNSAFE,
        reason_code: str = "RESOURCE_OWNERSHIP_UNKNOWN",
    ) -> None:
        super().__init__(message)
        self.outcome = outcome
        self.reason_code = reason_code


class PreflightCollectionError(RuntimeError):
    """A host/Docker probe could not produce trustworthy typed facts."""

    outcome = Outcome.BLOCKED_ENVIRONMENT
    reason_code = "PREFLIGHT_BLOCKED"


class ProcessIdentity(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    pid: int = Field(ge=1)
    start_time: str = Field(min_length=1)
    executable: str = Field(pattern=r"^/")


class PathIdentity(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    canonical_path: str = Field(pattern=r"^/")
    device: int = Field(ge=0)
    inode: int = Field(ge=1)
    file_type: str = Field(min_length=1)
    uid: int = Field(ge=0)


class PreflightInputs(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        arbitrary_types_allowed=True,
    )

    host: HostSnapshot
    docker: DockerSnapshot
    ports: tuple[PortObservation, ...]
    resources: tuple[ResourceObservation, ...]
    ownership_context: AuthenticatedOwnershipContext | None = None
    observed_upstream_commit: str
    runtime_compose_instance_sha256: str | None = Field(
        pattern=r"^[0-9a-f]{64}$"
    )
    observed_canonical_compose_contract_sha256: str | None = Field(
        pattern=r"^[0-9a-f]{64}$"
    )
    expected_canonical_compose_contract_sha256: str | None = Field(
        pattern=r"^[0-9a-f]{64}$"
    )
    compose_canonicalization_schema_version: Literal[
        "phase0.compose-canonicalization.v1"
    ]
    image_lock_verification: LockVerification
    pull_policy: str

    @model_validator(mode="after")
    def require_resolved_hashes_for_available_daemon(
        self,
    ) -> "PreflightInputs":
        if self.docker.daemon_available and any(
            value is None
            for value in (
                self.runtime_compose_instance_sha256,
                self.observed_canonical_compose_contract_sha256,
                self.expected_canonical_compose_contract_sha256,
            )
        ):
            raise ValueError(
                "available Docker daemon requires resolved Compose hashes"
            )
        return self


class PreflightResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    outcome: Outcome
    exit_code: int
    reason_codes: tuple[str, ...]

    @model_validator(mode="after")
    def require_consistent_result(self) -> "PreflightResult":
        if self.exit_code != self.outcome.exit_code:
            raise ValueError("preflight exit code conflicts with outcome")
        if self.outcome is Outcome.SUCCESS and self.reason_codes:
            raise ValueError("successful preflight cannot contain reason codes")
        if self.outcome is not Outcome.SUCCESS and not self.reason_codes:
            raise ValueError("failed preflight requires a reason code")
        if self.outcome is Outcome.INVALID_INVOCATION:
            raise ValueError("INVALID_INVOCATION is not a preflight result")
        return self


@dataclass(frozen=True, init=False)
class AuthenticatedPreflightEvidence:
    """Opaque, full-snapshot authority handoff for a single pre-up action."""

    _run_id: str
    _inputs: PreflightInputs
    _result: PreflightResult
    _collected_at: datetime
    _monotonic_started_ns: int
    _monotonic_finished_ns: int
    _content_sha256: str
    _integrity_hmac: str
    _provenance: object = field(repr=False, compare=False)

    def __init__(
        self,
        *,
        _token: object | None = None,
        run_id: str,
        inputs: PreflightInputs,
        result: PreflightResult,
        collected_at: datetime,
        monotonic_started_ns: int,
        monotonic_finished_ns: int,
        content_sha256: str,
    ) -> None:
        if _token is not _PREFLIGHT_EVIDENCE_TOKEN:
            raise TypeError(
                "authenticated preflight evidence must come from the authority"
            )
        payload = _preflight_evidence_payload(
            run_id=run_id,
            inputs=inputs,
            result=result,
            collected_at=collected_at,
            monotonic_started_ns=monotonic_started_ns,
            monotonic_finished_ns=monotonic_finished_ns,
        )
        expected_hash = canonical_json_sha256(payload)
        if content_sha256 != expected_hash:
            raise ValueError("preflight evidence content hash is inconsistent")
        for name, value in {
            "_run_id": run_id,
            "_inputs": inputs,
            "_result": result,
            "_collected_at": collected_at,
            "_monotonic_started_ns": monotonic_started_ns,
            "_monotonic_finished_ns": monotonic_finished_ns,
            "_content_sha256": content_sha256,
            "_integrity_hmac": _preflight_evidence_hmac(
                payload,
                content_sha256,
            ),
            "_provenance": _PREFLIGHT_EVIDENCE_TOKEN,
        }.items():
            object.__setattr__(self, name, value)

    @property
    def run_id(self) -> str:
        return self._run_id

    @property
    def inputs(self) -> PreflightInputs:
        return self._inputs

    @property
    def result(self) -> PreflightResult:
        return self._result

    @property
    def content_sha256(self) -> str:
        return self._content_sha256

    def is_authentic(self) -> bool:
        if self._provenance is not _PREFLIGHT_EVIDENCE_TOKEN:
            return False
        try:
            payload = _preflight_evidence_payload(
                run_id=self._run_id,
                inputs=self._inputs,
                result=self._result,
                collected_at=self._collected_at,
                monotonic_started_ns=self._monotonic_started_ns,
                monotonic_finished_ns=self._monotonic_finished_ns,
            )
            expected_hash = canonical_json_sha256(payload)
            expected_hmac = _preflight_evidence_hmac(
                payload,
                expected_hash,
            )
        except (AttributeError, TypeError, ValueError):
            return False
        return hmac.compare_digest(
            self._content_sha256,
            expected_hash,
        ) and hmac.compare_digest(self._integrity_hmac, expected_hmac)

    def is_current(
        self,
        *,
        now: datetime | None = None,
        monotonic_now_ns: int | None = None,
        max_age_seconds: int = PREFLIGHT_EVIDENCE_MAX_AGE_SECONDS,
    ) -> bool:
        if not self.is_authentic() or self._result.outcome is not Outcome.SUCCESS:
            return False
        current_time = now or datetime.now(UTC)
        current_monotonic = (
            time.monotonic_ns() if monotonic_now_ns is None else monotonic_now_ns
        )
        if (
            current_time.utcoffset() != timedelta(0)
            or self._collected_at.utcoffset() != timedelta(0)
            or self._monotonic_started_ns < 0
            or self._monotonic_finished_ns < self._monotonic_started_ns
            or current_monotonic < self._monotonic_finished_ns
        ):
            return False
        wall_age = (current_time - self._collected_at).total_seconds()
        monotonic_age = (
            current_monotonic - self._monotonic_finished_ns
        ) / 1_000_000_000
        return (
            0 <= wall_age <= max_age_seconds and 0 <= monotonic_age <= max_age_seconds
        )


def preflight_failure_result(
    error: PreflightCollectionError | DiscoveryParseError,
) -> PreflightResult:
    """Convert the one typed preflight failure boundary to a stable result."""
    return _result(error.outcome, (error.reason_code,))


def evaluate_preflight(inputs: PreflightInputs) -> PreflightResult:
    """Classify facts in safety-first order without mutating the host."""
    if not _active_ownership_is_valid(inputs):
        return _result(Outcome.UNSAFE, ("RESOURCE_OWNERSHIP_UNKNOWN",))

    if any(
        port.occupied and port.ownership == "UNKNOWN" for port in inputs.ports
    ) or any(resource.ownership == "UNKNOWN" for resource in inputs.resources):
        return _result(Outcome.UNSAFE, ("RESOURCE_OWNERSHIP_UNKNOWN",))

    if any(
        port.occupied and port.ownership == "KNOWN_OTHER" for port in inputs.ports
    ) or any(resource.ownership == "KNOWN_OTHER" for resource in inputs.resources):
        return _result(Outcome.BLOCKED_ENVIRONMENT, ("RESOURCE_CONFLICT",))

    host = inputs.host
    if (
        host.architecture != "arm64"
        or host.total_memory_bytes < MINIMUM_MEMORY_BYTES
        or host.available_disk_bytes < MINIMUM_DISK_BYTES
    ):
        return _result(
            Outcome.BLOCKED_ENVIRONMENT,
            ("ENVIRONMENT_UNSUPPORTED",),
        )

    docker = inputs.docker
    if (
        not docker.client_available
        or not docker.daemon_available
        or not docker.compose_available
    ):
        return _result(
            Outcome.BLOCKED_ENVIRONMENT,
            ("PREFLIGHT_BLOCKED",),
        )
    if (
        not docker.desktop_identity_verified
        or not docker.compose_plugin_v2
        or docker.context_name != DOCKER_DESKTOP_CONTEXT
        or not is_local_unix_docker_endpoint(docker.endpoint)
        or not _is_observed_daemon_id(docker.daemon_id)
        or docker.server_os_type != "linux"
        or docker.server_architecture != "arm64"
        or docker.native_platform != EXPECTED_PLATFORM
    ):
        return _result(
            Outcome.BLOCKED_ENVIRONMENT,
            ("ENVIRONMENT_UNSUPPORTED",),
        )
    if not docker.resource_fields_verified:
        return _result(
            Outcome.BLOCKED_ENVIRONMENT,
            ("PREFLIGHT_BLOCKED",),
        )
    if (
        docker.memory_bytes < MINIMUM_MEMORY_BYTES
        or docker.disk_bytes < MINIMUM_DISK_BYTES
    ):
        return _result(
            Outcome.BLOCKED_ENVIRONMENT,
            ("DOCKER_RESOURCES_INSUFFICIENT",),
        )

    frozen_reasons: list[str] = []
    if inputs.observed_upstream_commit != UPSTREAM_COMMIT:
        frozen_reasons.append("INPUT_NOT_FROZEN")
    if (
        inputs.compose_canonicalization_schema_version
        != COMPOSE_CANONICALIZATION_SCHEMA_VERSION
    ):
        frozen_reasons.append("COMPOSE_CANONICALIZATION_SCHEMA_MISMATCH")
    if (
        inputs.observed_canonical_compose_contract_sha256
        != inputs.expected_canonical_compose_contract_sha256
    ):
        frozen_reasons.append("COMPOSE_CONTRACT_HASH_MISMATCH")
    if inputs.pull_policy != "never":
        frozen_reasons.append("PULL_POLICY_NOT_FROZEN")
    if frozen_reasons:
        return _result(Outcome.BLOCKED_UPSTREAM, tuple(frozen_reasons))

    if not inputs.image_lock_verification.is_consistent():
        return _result(
            Outcome.BLOCKED_UPSTREAM,
            ("IMAGE_LOCK_VERIFICATION_INCONSISTENT",),
        )
    if not inputs.image_lock_verification.passed:
        return _result(
            Outcome.BLOCKED_UPSTREAM,
            inputs.image_lock_verification.reason_codes,
        )
    return _result(Outcome.SUCCESS, ())


def is_local_unix_docker_endpoint(endpoint: str) -> bool:
    if not endpoint.startswith("unix:///"):
        return False
    socket_path = endpoint.removeprefix("unix://")
    return (
        Path(socket_path).is_absolute()
        and ".." not in Path(socket_path).parts
        and not any(character.isspace() for character in socket_path)
    )


def docker_host_prefix(endpoint: str) -> tuple[str, str, str]:
    """Build an immutable local-daemon capability for Docker CLI calls."""
    if not is_local_unix_docker_endpoint(endpoint):
        raise ValueError("Docker endpoint must be an observed local Unix socket")
    return ("docker", "--host", endpoint)


def _is_observed_daemon_id(daemon_id: str) -> bool:
    normalized = daemon_id.strip().casefold()
    return bool(normalized) and normalized not in {
        "unknown",
        "unavailable",
        "placeholder",
        "not-observed",
    }


def issue_authenticated_preflight_evidence(
    *,
    run_id: str,
    inputs: PreflightInputs,
    collected_at: datetime,
    monotonic_started_ns: int,
    monotonic_finished_ns: int,
) -> AuthenticatedPreflightEvidence:
    """Evaluate and sign a complete preflight snapshot in one authority step."""
    if re.fullmatch(r"[0-9a-f]{32}", run_id) is None:
        raise ValueError("preflight evidence run_id is invalid")
    if (
        collected_at.utcoffset() != timedelta(0)
        or monotonic_started_ns < 0
        or monotonic_finished_ns < monotonic_started_ns
    ):
        raise ValueError("preflight evidence provenance is invalid")
    result = evaluate_preflight(inputs)
    payload = _preflight_evidence_payload(
        run_id=run_id,
        inputs=inputs,
        result=result,
        collected_at=collected_at,
        monotonic_started_ns=monotonic_started_ns,
        monotonic_finished_ns=monotonic_finished_ns,
    )
    content_sha256 = canonical_json_sha256(payload)
    return AuthenticatedPreflightEvidence(
        _token=_PREFLIGHT_EVIDENCE_TOKEN,
        run_id=run_id,
        inputs=inputs,
        result=result,
        collected_at=collected_at,
        monotonic_started_ns=monotonic_started_ns,
        monotonic_finished_ns=monotonic_finished_ns,
        content_sha256=content_sha256,
    )


def _preflight_evidence_payload(
    *,
    run_id: str,
    inputs: PreflightInputs,
    result: PreflightResult,
    collected_at: datetime,
    monotonic_started_ns: int,
    monotonic_finished_ns: int,
) -> dict[str, object]:
    ownership = inputs.ownership_context
    ownership_payload = (
        None
        if ownership is None
        else {
            "run_id": ownership.run_id,
            "project_name": ownership.project_name,
            "canonical_labels": ownership.canonical_labels,
            "manifest": ownership.manifest.model_dump(mode="json"),
            "manifest_sha256": ownership.manifest_sha256,
            "created_at": ownership.created_at.isoformat(),
            "authenticated": ownership.is_authentic(),
        }
    )
    return {
        "schema_version": "phase0.preflight-evidence.v2",
        "run_id": run_id,
        "project_name": PROJECT_NAMESPACE,
        "canonical_labels": {
            PROJECT_LABEL: PROJECT_NAMESPACE,
            RUN_LABEL: run_id,
        },
        "collected_at": collected_at.isoformat(),
        "monotonic_started_ns": monotonic_started_ns,
        "monotonic_finished_ns": monotonic_finished_ns,
        "inputs": {
            "host": inputs.host.model_dump(mode="json"),
            "docker": inputs.docker.model_dump(mode="json"),
            "ports": [port.model_dump(mode="json") for port in inputs.ports],
            "resources": [
                resource.model_dump(mode="json") for resource in inputs.resources
            ],
            "ownership_context": ownership_payload,
            "observed_upstream_commit": inputs.observed_upstream_commit,
            "runtime_compose_instance_sha256": (
                inputs.runtime_compose_instance_sha256
            ),
            "observed_canonical_compose_contract_sha256": (
                inputs.observed_canonical_compose_contract_sha256
            ),
            "expected_canonical_compose_contract_sha256": (
                inputs.expected_canonical_compose_contract_sha256
            ),
            "compose_canonicalization_schema_version": (
                inputs.compose_canonicalization_schema_version
            ),
            "image_lock_verification": (
                inputs.image_lock_verification.model_dump(mode="json")
            ),
            "pull_policy": inputs.pull_policy,
        },
        "result": result.model_dump(mode="json"),
    }


def _preflight_evidence_hmac(
    payload: dict[str, object],
    content_sha256: str,
) -> str:
    authenticated_payload = {
        "content": payload,
        "content_sha256": content_sha256,
    }
    return hmac.new(
        _PREFLIGHT_EVIDENCE_KEY,
        canonical_json_bytes(authenticated_payload),
        hashlib.sha256,
    ).hexdigest()


def _active_ownership_is_valid(inputs: PreflightInputs) -> bool:
    context = inputs.ownership_context
    if context is None:
        return all(
            not port.occupied
            and port.ownership == "NONE"
            and port.ownership_proof is None
            for port in inputs.ports
        ) and all(
            not resource.present
            and resource.ownership == "NONE"
            and not resource.resource_id
            and not resource.labels
            and resource.ownership_proof is None
            for resource in inputs.resources
        )
    if (
        not isinstance(context, AuthenticatedOwnershipContext)
        or not context.is_authentic()
        or context.project_name != PROJECT_NAMESPACE
        or context.canonical_labels
        != {
            PROJECT_LABEL: PROJECT_NAMESPACE,
            RUN_LABEL: context.run_id,
        }
    ):
        return False

    manifest = context.manifest
    if not manifest.is_consistent() or manifest.run_id != context.run_id:
        return False
    manifest_hash = canonical_json_sha256(manifest.model_dump(mode="json"))
    if manifest_hash != context.manifest_sha256:
        return False

    expected = {
        (resource.kind, resource.resource_id): resource
        for resource in manifest.resources
    }
    if len(expected) != len(manifest.resources):
        return False

    observed_owned: list[tuple[str, str]] = []
    for port in inputs.ports:
        if port.ownership != "OWNED":
            continue
        proof = port.ownership_proof
        identity = (
            "port",
            proof.resource_id if proof is not None else "",
        )
        if (
            proof is None
            or not _proof_matches_active_manifest(
                proof,
                manifest_hash=manifest_hash,
                run_id=context.run_id,
                resource_kind="port",
                resource_id=proof.resource_id,
                port=port.port,
            )
            or identity not in expected
            or not expected[identity].identity_evidence
            or expected[identity].identity_evidence != proof.identifiers
        ):
            return False
        observed_owned.append(identity)

    docker_kinds = {"container", "network", "volume"}
    for resource in inputs.resources:
        if (resource.present and resource.ownership == "NONE") or (
            not resource.present
            and (
                resource.ownership != "NONE"
                or bool(resource.resource_id)
                or bool(resource.labels)
                or resource.ownership_proof is not None
            )
        ):
            return False
        if resource.ownership != "OWNED":
            continue
        proof = resource.ownership_proof
        identity = (resource.kind, resource.resource_id)
        expected_resource = expected.get(identity)
        if (
            proof is None
            or not _proof_matches_active_manifest(
                proof,
                manifest_hash=manifest_hash,
                run_id=context.run_id,
                resource_kind=resource.kind,
                resource_id=resource.resource_id,
                port=None,
            )
            or expected_resource is None
            or expected_resource.name != resource.name
            or (
                resource.kind in {"process", "lock", "lock_file", "project_file"}
                and (
                    not expected_resource.identity_evidence
                    or expected_resource.identity_evidence != proof.identifiers
                )
            )
        ):
            return False
        if resource.kind in docker_kinds and (
            resource.labels.get(PROJECT_LABEL) != PROJECT_NAMESPACE
            or resource.labels.get(RUN_LABEL) != context.run_id
        ):
            return False
        observed_owned.append(identity)

    return len(observed_owned) == len(set(observed_owned)) and set(
        observed_owned
    ) == set(expected)


def _proof_matches_active_manifest(
    proof: OwnershipProof,
    *,
    manifest_hash: str,
    run_id: str,
    resource_kind: str,
    resource_id: str,
    port: int | None,
) -> bool:
    return (
        proof.project_namespace == PROJECT_NAMESPACE
        and proof.manifest_sha256 == manifest_hash
        and proof.run_id == run_id
        and proof.resource_kind == resource_kind
        and proof.resource_id == resource_id
        and proof.port == port
    )


def collect_host_snapshot(
    runner: CommandRunner,
    *,
    timeout_seconds: float = 10,
) -> HostSnapshot:
    """Collect supported-host facts through injected read-only commands."""
    try:
        return _collect_host_snapshot(runner, timeout_seconds=timeout_seconds)
    except PreflightCollectionError:
        raise
    except Exception as error:
        raise PreflightCollectionError(
            "PREFLIGHT_BLOCKED: host snapshot is malformed"
        ) from error


def _collect_host_snapshot(
    runner: CommandRunner,
    *,
    timeout_seconds: float,
) -> HostSnapshot:
    version = _run_required(
        runner,
        ("sw_vers", "-productVersion"),
        timeout_seconds,
    )
    build = _run_required(
        runner,
        ("sw_vers", "-buildVersion"),
        timeout_seconds,
    )
    architecture = _run_required(runner, ("uname", "-m"), timeout_seconds)
    cpu_model = _run_required(
        runner,
        ("sysctl", "-n", "machdep.cpu.brand_string"),
        timeout_seconds,
    )
    cpu_count = _run_required(
        runner,
        ("sysctl", "-n", "hw.logicalcpu"),
        timeout_seconds,
    )
    total_memory = _run_required(
        runner,
        ("sysctl", "-n", "hw.memsize"),
        timeout_seconds,
    )
    virtual_memory = _run_required(runner, ("vm_stat",), timeout_seconds)
    disk = _run_required(runner, ("df", "-Pk", "."), timeout_seconds)

    return HostSnapshot(
        macos_version=version.strip(),
        macos_build=build.strip(),
        architecture=architecture.strip(),
        cpu_model=cpu_model.strip(),
        cpu_count=int(cpu_count.strip()),
        total_memory_bytes=int(total_memory.strip()),
        available_memory_bytes=_available_memory_bytes(virtual_memory),
        available_disk_bytes=_available_disk_bytes(disk),
    )


def collect_docker_snapshot(
    runner: CommandRunner,
    *,
    timeout_seconds: float = 10,
) -> DockerSnapshot:
    """Collect Docker facts through an injected, read-only command runner."""
    try:
        return _collect_docker_snapshot(runner, timeout_seconds=timeout_seconds)
    except PreflightCollectionError:
        raise
    except Exception as error:
        raise PreflightCollectionError(
            "PREFLIGHT_BLOCKED: Docker snapshot is malformed"
        ) from error


def _strict_json_object(raw: bytes) -> dict[str, object]:
    def reject_duplicate_keys(
        pairs: list[tuple[str, object]],
    ) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result

    try:
        payload = json.loads(
            raw,
            object_pairs_hook=reject_duplicate_keys,
        )
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise ValueError("Docker settings JSON is malformed") from error
    if not isinstance(payload, dict):
        raise ValueError("Docker settings must be a JSON object")
    return payload


def _validate_docker_settings_stat(metadata: os.stat_result) -> None:
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or metadata.st_mode & 0o022
        or metadata.st_size < 2
        or metadata.st_size > _MAX_DOCKER_SETTINGS_BYTES
    ):
        raise ValueError("Docker settings file safety boundary differs")


def _load_standard_docker_settings() -> tuple[dict[str, object], str]:
    path = Path.home() / _DOCKER_SETTINGS_RELATIVE_PATH
    before = os.lstat(path)
    _validate_docker_settings_stat(before)
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags)
    try:
        opened = os.fstat(descriptor)
        _validate_docker_settings_stat(opened)
        if (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino):
            raise ValueError("Docker settings file identity changed")
        chunks: list[bytes] = []
        remaining = opened.st_size
        while remaining:
            chunk = os.read(descriptor, min(remaining, 64 * 1024))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    if (
        len(raw) != opened.st_size
        or opened.st_size != after.st_size
        or opened.st_mtime_ns != after.st_mtime_ns
    ):
        raise ValueError("Docker settings file read was incomplete")
    return _strict_json_object(raw), hashlib.sha256(raw).hexdigest()


def _docker_settings_version(settings: dict[str, object]) -> int | None:
    value = settings.get("SettingsVersion")
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _collect_docker_snapshot(
    runner: CommandRunner,
    *,
    timeout_seconds: float,
) -> DockerSnapshot:
    context_result = runner.run(
        _DOCKER_CONTEXT_INSPECT,
        timeout_seconds=timeout_seconds,
    )
    context_payload = (
        json.loads(context_result.stdout)
        if context_result.exit_code == 0 and context_result.stdout.strip()
        else {}
    )
    if not isinstance(context_payload, dict):
        raise ValueError("Docker context inspection must be a JSON object")
    endpoints = context_payload.get("Endpoints", {})
    docker_endpoint = endpoints.get("docker", {}) if isinstance(endpoints, dict) else {}
    endpoint = (
        str(docker_endpoint.get("Host", ""))
        if isinstance(docker_endpoint, dict)
        else ""
    )
    context_name = str(context_payload.get("Name", ""))
    if not is_local_unix_docker_endpoint(endpoint):
        return DockerSnapshot(
            client_available=False,
            client_version="",
            daemon_available=False,
            server_version="",
            desktop_version="",
            engine="",
            desktop_identity_verified=False,
            compose_available=False,
            compose_version="",
            compose_plugin_v2=False,
            server_os_type="",
            server_architecture="",
            native_platform="",
            cpu_count=0,
            memory_bytes=0,
            disk_bytes=0,
            resource_fields_verified=False,
            context_name=context_name,
            endpoint=endpoint,
            daemon_id="",
        )
    docker_prefix = docker_host_prefix(endpoint)
    client = runner.run(
        (*docker_prefix, "--version"),
        timeout_seconds=timeout_seconds,
    )
    compose = runner.run(
        (*docker_prefix, "compose", "version", "--short"),
        timeout_seconds=timeout_seconds,
    )
    info = runner.run(
        (*docker_prefix, "info", "--format", "{{json .}}"),
        timeout_seconds=timeout_seconds,
    )

    client_version = _extract_version(client.stdout) if client.exit_code == 0 else ""
    compose_version = compose.stdout.strip() if compose.exit_code == 0 else ""
    compose_major = (
        compose_plugin_major_version(compose.stdout) if compose.exit_code == 0 else None
    )
    if info.exit_code != 0:
        return DockerSnapshot(
            client_available=client.exit_code == 0,
            client_version=client_version,
            daemon_available=False,
            server_version="",
            desktop_version="",
            engine="",
            desktop_identity_verified=False,
            compose_available=compose.exit_code == 0,
            compose_version=compose_version,
            compose_plugin_v2=compose_major is not None,
            server_os_type="",
            server_architecture="",
            native_platform="",
            cpu_count=0,
            memory_bytes=0,
            disk_bytes=0,
            resource_fields_verified=False,
            context_name=context_name,
            endpoint=endpoint,
            daemon_id="",
        )

    payload = json.loads(info.stdout)
    if not isinstance(payload, dict):
        raise ValueError("Docker info must be a JSON object")
    settings = runner.run(
        (*docker_prefix, "desktop", "settings", "export"),
        timeout_seconds=timeout_seconds,
    )
    settings_payload: dict[str, object] | None = None
    settings_content_sha256: str | None = None
    settings_source_kind: Literal["cli_export", "standard_file"]
    if settings.exit_code == 0 and settings.stdout.strip():
        settings_raw = settings.stdout.encode()
        try:
            settings_payload = _strict_json_object(settings_raw)
        except ValueError:
            pass
        else:
            settings_content_sha256 = hashlib.sha256(settings_raw).hexdigest()
            settings_source_kind = "cli_export"
    if settings_payload is None:
        settings_payload, settings_content_sha256 = (
            _load_standard_docker_settings()
        )
        settings_source_kind = "standard_file"
    settings_version = _docker_settings_version(settings_payload)
    if settings_source_kind == "standard_file":
        disk_size_mib = settings_payload.get("DiskSizeMiB")
        if (
            settings_version is None
            or not isinstance(disk_size_mib, (int, float))
            or isinstance(disk_size_mib, bool)
            or disk_size_mib <= 0
        ):
            raise ValueError("Docker settings standard fields differ")
        disk_bytes = int(disk_size_mib * 1024**2)
    else:
        disk_bytes = _docker_desktop_disk_bytes(settings_payload)
    operating_system = str(payload.get("OperatingSystem", ""))
    desktop_match = re.search(r"Docker Desktop\s+([0-9A-Za-z.-]+)", operating_system)
    os_type = str(payload.get("OSType", "")).lower()
    architecture = _normalize_architecture(str(payload.get("Architecture", "")).lower())
    cpu_count = float(payload.get("NCPU", 0))
    memory_bytes = int(payload.get("MemTotal", 0))
    desktop_identity_verified = (
        "docker desktop" in operating_system.lower()
        and str(payload.get("Name", "")).lower() == "docker-desktop"
    )
    return DockerSnapshot(
        client_available=client.exit_code == 0,
        client_version=client_version,
        daemon_available=True,
        server_version=str(payload.get("ServerVersion", "")),
        desktop_version=desktop_match.group(1) if desktop_match else "",
        engine=operating_system,
        desktop_identity_verified=desktop_identity_verified,
        compose_available=compose.exit_code == 0,
        compose_version=compose_version,
        compose_plugin_v2=compose_major is not None,
        server_os_type=os_type,
        server_architecture=architecture,
        native_platform=f"{os_type}/{architecture}",
        cpu_count=cpu_count,
        memory_bytes=memory_bytes,
        disk_bytes=disk_bytes,
        resource_fields_verified=(
            cpu_count > 0
            and memory_bytes > 0
            and disk_bytes > 0
            and settings_content_sha256 is not None
            and context_result.exit_code == 0
        ),
        settings_source_kind=settings_source_kind,
        settings_content_sha256=settings_content_sha256,
        settings_version=settings_version,
        context_name=context_name,
        endpoint=endpoint,
        daemon_id=str(payload.get("ID", "")),
    )


def build_read_only_discovery_plan(
    *,
    project_root: Path,
    ports: tuple[int, ...],
    image_references: tuple[str, ...],
    project_paths: tuple[Path, ...],
    lock_paths: tuple[Path, ...],
    docker_endpoint: str,
) -> tuple[DiscoveryCommand, ...]:
    """Build an auditable plan containing only read-only discovery commands."""
    root = project_root.resolve()
    docker_prefix = docker_host_prefix(docker_endpoint)
    all_paths = tuple(project_paths) + tuple(lock_paths)
    for path in all_paths:
        if not path.resolve(strict=False).is_relative_to(root):
            raise ValueError("discovery path is outside the project root")

    commands: list[DiscoveryCommand] = []
    for port in ports:
        commands.append(
            DiscoveryCommand(
                purpose="port",
                arguments=(
                    "lsof",
                    "-nP",
                    "-F",
                    "pcn",
                    f"-iTCP:{port}",
                    "-sTCP:LISTEN",
                ),
            )
        )
    commands.extend(
        [
            DiscoveryCommand(
                purpose="containers",
                arguments=(
                    *docker_prefix,
                    "container",
                    "ls",
                    "--all",
                    "--format",
                    "{{json .}}",
                ),
            ),
            DiscoveryCommand(
                purpose="networks",
                arguments=(
                    *docker_prefix,
                    "network",
                    "ls",
                    "--format",
                    "{{json .}}",
                ),
            ),
            DiscoveryCommand(
                purpose="volumes",
                arguments=(
                    *docker_prefix,
                    "volume",
                    "ls",
                    "--format",
                    "{{json .}}",
                ),
            ),
            DiscoveryCommand(
                purpose="processes",
                arguments=("ps", "-axo", "pid=,lstart=,comm="),
            ),
        ]
    )
    commands.extend(
        DiscoveryCommand(
            purpose="project_file",
            arguments=(
                "stat",
                "-f",
                "%N|%d|%i|%HT|%u|%p",
                str(path),
            ),
        )
        for path in project_paths
    )
    commands.extend(
        DiscoveryCommand(
            purpose="lock_file",
            arguments=(
                "stat",
                "-f",
                "%N|%d|%i|%HT|%u|%p",
                str(path),
            ),
        )
        for path in lock_paths
    )
    commands.extend(
        [
            DiscoveryCommand(
                purpose="upstream_commit",
                arguments=(
                    "git",
                    "-C",
                    str(root / "third_party" / "opentelemetry-demo"),
                    "rev-parse",
                    "HEAD",
                ),
            ),
            DiscoveryCommand(
                purpose="compose_config",
                arguments=(
                    *docker_prefix,
                    "compose",
                    "--project-name",
                    PROJECT_NAMESPACE,
                    "--project-directory",
                    str(root / "third_party" / "opentelemetry-demo"),
                    "--env-file",
                    str(root / "third_party" / "opentelemetry-demo" / ".env"),
                    "--file",
                    str(root / "third_party" / "opentelemetry-demo" / "compose.yaml"),
                    "--file",
                    str(
                        root
                        / "third_party"
                        / "opentelemetry-demo"
                        / "compose.observability.yaml"
                    ),
                    "--file",
                    str(root / "config" / "phase0" / "compose.phase0.yaml"),
                    "config",
                    "--format",
                    "json",
                ),
            ),
        ]
    )
    if image_references:
        commands.append(
            DiscoveryCommand(
                purpose="cached_images",
                arguments=(
                    *docker_prefix,
                    "image",
                    "inspect",
                    "--platform",
                    "linux/arm64",
                    *image_references,
                ),
            )
        )
    return tuple(commands)


def parse_port_observation(
    result: CommandResult,
    *,
    port: int,
    owned_processes: dict[int, ProcessIdentity | str],
    manifest_sha256: str,
    active_run_id: str,
    manifest: OwnershipManifest | None = None,
) -> PortObservation:
    """Parse lsof field output and prove ownership only from a process manifest."""
    if (
        result.exit_code == 1
        and not result.stdout.strip()
        and not result.stderr.strip()
    ):
        return PortObservation(
            port=port,
            occupied=False,
            ownership="NONE",
        )
    if result.exit_code != 0:
        return PortObservation(
            port=port,
            occupied=True,
            ownership="UNKNOWN",
        )
    pids = tuple(
        int(match.group(1))
        for line in result.stdout.splitlines()
        if (match := re.fullmatch(r"p(\d+)", line.strip())) is not None
    )
    if not pids:
        return PortObservation(
            port=port,
            occupied=True,
            ownership="UNKNOWN",
        )
    socket_names = tuple(
        line.strip()[1:]
        for line in result.stdout.splitlines()
        if line.strip().startswith("n") and len(line.strip()) > 1
    )
    identities = tuple(owned_processes.get(pid) for pid in sorted(pids))
    if (
        socket_names
        and all(name.endswith(f":{port}") for name in socket_names)
        and all(
            isinstance(identity, ProcessIdentity) and identity.pid == pid
            for pid, identity in zip(sorted(pids), identities, strict=True)
        )
    ):
        identifiers: list[str] = []
        for identity in identities:
            assert isinstance(identity, ProcessIdentity)
            identifiers.extend(
                (
                    f"pid:{identity.pid}",
                    f"start:{identity.start_time}",
                    f"executable:{identity.executable}",
                )
            )
        identifiers.extend(f"socket:{name}" for name in sorted(socket_names))
        proof = OwnershipProof(
            project_namespace=PROJECT_NAMESPACE,
            manifest_sha256=manifest_sha256,
            run_id=active_run_id,
            resource_kind="port",
            resource_id=f"tcp:{port}",
            port=port,
            identifiers=(f"port:tcp:{port}", *identifiers),
        )
        if not _manifest_binds_proof(manifest, proof):
            return PortObservation(
                port=port,
                occupied=True,
                ownership="UNKNOWN",
            )
        return PortObservation(
            port=port,
            occupied=True,
            ownership="OWNED",
            ownership_proof=proof,
        )
    return PortObservation(port=port, occupied=True, ownership="UNKNOWN")


def parse_docker_resource_listing(
    kind: str,
    result: CommandResult,
    manifest: OwnershipManifest,
) -> tuple[ResourceObservation, ...]:
    """Parse Docker JSON lines and compare exact identity, labels, and manifest."""
    if result.exit_code != 0:
        raise DiscoveryParseError(f"{kind} discovery failed")
    manifest_entries = {
        (resource.kind, resource.name, resource.resource_id): resource
        for resource in manifest.resources
    }
    manifest_hash = canonical_json_sha256(manifest.model_dump(mode="json"))
    observations: list[ResourceObservation] = []
    for line in result.stdout.splitlines():
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as error:
            raise DiscoveryParseError(f"malformed {kind} discovery JSON") from error
        name = str(payload.get("Names") or payload.get("Name") or "")
        resource_id = str(payload.get("ID") or name)
        labels = _parse_labels(str(payload.get("Labels", "")))
        if (
            not name.startswith(PROJECT_NAMESPACE)
            and labels.get(PROJECT_LABEL) != PROJECT_NAMESPACE
        ):
            continue
        identity = (kind, name, resource_id)
        expected = manifest_entries.get(identity)
        owned = (
            expected is not None
            and labels.get(PROJECT_LABEL) == PROJECT_NAMESPACE
            and labels.get(RUN_LABEL) == manifest.run_id
            and expected.labels.get(PROJECT_LABEL) == PROJECT_NAMESPACE
            and expected.labels.get(RUN_LABEL) == manifest.run_id
        )
        proof = (
            OwnershipProof(
                project_namespace=PROJECT_NAMESPACE,
                manifest_sha256=manifest_hash,
                run_id=manifest.run_id,
                resource_kind=kind,
                resource_id=resource_id,
                identifiers=(
                    f"{kind}:{resource_id}",
                    f"name:{name}",
                ),
            )
            if owned
            else None
        )
        observations.append(
            ResourceObservation(
                kind=kind,
                name=name,
                resource_id=resource_id,
                labels=labels,
                ownership="OWNED" if owned else "UNKNOWN",
                ownership_proof=proof,
            )
        )
    return tuple(observations)


def parse_path_probe(
    result: CommandResult,
    *,
    path: Path,
    expected_identity: PathIdentity | str | None,
    manifest_sha256: str,
    active_run_id: str,
    kind: str,
    manifest: OwnershipManifest | None = None,
) -> ResourceObservation:
    """Parse a project-local stat probe without adopting an unknown path."""
    stderr = result.stderr.casefold()
    if (
        result.exit_code == 1
        and not result.stdout.strip()
        and ("no such file or directory" in stderr or "not found" in stderr)
    ):
        return ResourceObservation(
            kind=kind,
            name=str(path),
            present=False,
            ownership="NONE",
        )
    if result.exit_code != 0:
        return ResourceObservation(
            kind=kind,
            name=str(path),
            ownership="UNKNOWN",
        )
    fields = result.stdout.strip().split("|")
    if len(fields) == 3:
        return ResourceObservation(
            kind=kind,
            name=str(path),
            resource_id=fields[1],
            labels={},
            ownership="UNKNOWN",
        )
    if len(fields) != 6:
        raise DiscoveryParseError(f"{kind} path discovery is malformed")
    try:
        observed_identity = PathIdentity(
            canonical_path=fields[0],
            device=int(fields[1]),
            inode=int(fields[2]),
            file_type=fields[3],
            uid=int(fields[4]),
        )
    except (ValueError, ValidationError) as error:
        raise DiscoveryParseError(f"{kind} path identity is malformed") from error
    canonical_requested = str(path.resolve(strict=False))
    resource_id = f"{observed_identity.device}:{observed_identity.inode}"
    owned = (
        isinstance(expected_identity, PathIdentity)
        and observed_identity == expected_identity
        and observed_identity.canonical_path == canonical_requested
    )
    proof = OwnershipProof(
        project_namespace=PROJECT_NAMESPACE,
        manifest_sha256=manifest_sha256,
        run_id=active_run_id,
        resource_kind=kind,
        resource_id=resource_id,
        identifiers=(
            f"{kind}:{resource_id}",
            f"path:{observed_identity.canonical_path}",
            f"device:{observed_identity.device}",
            f"inode:{observed_identity.inode}",
            f"type:{observed_identity.file_type}",
            f"uid:{observed_identity.uid}",
        ),
    )
    owned = owned and _manifest_binds_proof(manifest, proof)
    return ResourceObservation(
        kind=kind,
        name=canonical_requested,
        resource_id=resource_id,
        labels={},
        ownership="OWNED" if owned else "UNKNOWN",
        ownership_proof=(proof if owned else None),
    )


def parse_process_listing(
    result: CommandResult,
    *,
    expected_processes: dict[int, ProcessIdentity | str],
    manifest_sha256: str,
    active_run_id: str,
    manifest: OwnershipManifest | None = None,
) -> tuple[ResourceObservation, ...]:
    """Parse relevant host processes and require exact PID/identity proof."""
    if result.exit_code != 0:
        raise DiscoveryParseError("process discovery failed")
    observations: list[ResourceObservation] = []
    for line in result.stdout.splitlines():
        match = re.fullmatch(
            r"\s*(\d+)\s+"
            r"([A-Za-z]{3}\s+[A-Za-z]{3}\s+\d{1,2}\s+"
            r"\d{2}:\d{2}:\d{2}\s+\d{4})\s+(\S+)\s*",
            line,
        )
        if match is None:
            legacy = re.match(r"\s*(\d+)\s+(.+)$", line)
            if legacy is None:
                continue
            pid = int(legacy.group(1))
            command = legacy.group(2)
            if (
                pid in expected_processes
                or "phase0" in command.casefold()
                or PROJECT_NAMESPACE in command
            ):
                observations.append(
                    ResourceObservation(
                        kind="process",
                        name=command,
                        resource_id=str(pid),
                        labels={},
                        ownership="UNKNOWN",
                    )
                )
            continue
        pid = int(match.group(1))
        observed = ProcessIdentity(
            pid=pid,
            start_time=match.group(2),
            executable=match.group(3),
        )
        expected = expected_processes.get(pid)
        if (
            not isinstance(expected, ProcessIdentity)
            and "phase0" not in observed.executable.casefold()
            and PROJECT_NAMESPACE not in observed.executable
        ):
            continue
        owned = isinstance(expected, ProcessIdentity) and observed == expected
        resource_id = f"{pid}:{observed.start_time}"
        proof = OwnershipProof(
            project_namespace=PROJECT_NAMESPACE,
            manifest_sha256=manifest_sha256,
            run_id=active_run_id,
            resource_kind="process",
            resource_id=resource_id,
            identifiers=(
                f"process:{resource_id}",
                f"pid:{pid}",
                f"start:{observed.start_time}",
                f"executable:{observed.executable}",
            ),
        )
        owned = owned and _manifest_binds_proof(manifest, proof)
        observations.append(
            ResourceObservation(
                kind="process",
                name=observed.executable,
                resource_id=resource_id,
                labels={},
                ownership="OWNED" if owned else "UNKNOWN",
                ownership_proof=(proof if owned else None),
            )
        )
    return tuple(observations)


def _manifest_binds_proof(
    manifest: OwnershipManifest | None,
    proof: OwnershipProof,
) -> bool:
    if (
        manifest is None
        or manifest.run_id != proof.run_id
        or canonical_json_sha256(manifest.model_dump(mode="json"))
        != proof.manifest_sha256
    ):
        return False
    return any(
        resource.kind == proof.resource_kind
        and resource.resource_id == proof.resource_id
        and resource.identity_evidence == proof.identifiers
        for resource in manifest.resources
    )


def parse_upstream_commit(result: CommandResult) -> str:
    if result.exit_code != 0:
        raise DiscoveryParseError(
            "upstream commit probe failed",
            outcome=Outcome.BLOCKED_UPSTREAM,
            reason_code="INPUT_NOT_FROZEN",
        )
    commit = result.stdout.strip()
    if re.fullmatch(r"[0-9a-f]{40}", commit) is None:
        raise DiscoveryParseError(
            "upstream commit is malformed",
            outcome=Outcome.BLOCKED_UPSTREAM,
            reason_code="INPUT_NOT_FROZEN",
        )
    return commit


def parse_resolved_compose_config(
    result: CommandResult,
) -> ResolvedComposeConfig:
    if result.exit_code != 0 or not result.stdout:
        raise DiscoveryParseError(
            "resolved Compose configuration is unavailable",
            outcome=Outcome.BLOCKED_UPSTREAM,
            reason_code="COMPOSE_CONTRACT_HASH_MISMATCH",
        )
    try:
        return ResolvedComposeConfig.from_stdout(result.stdout)
    except (ValidationError, ValueError) as error:
        raise DiscoveryParseError(
            "resolved Compose configuration is incomplete",
            outcome=Outcome.BLOCKED_UPSTREAM,
            reason_code="COMPOSE_CONTRACT_HASH_MISMATCH",
        ) from error


def parse_runtime_compose_instance_hash(result: CommandResult) -> str:
    return parse_resolved_compose_config(
        result
    ).runtime_compose_instance_sha256


def parse_canonical_compose_contract_hash(result: CommandResult) -> str:
    return parse_resolved_compose_config(
        result
    ).canonical_compose_contract_sha256


def parse_cached_images(result: CommandResult) -> tuple[InspectedImage, ...]:
    """Parse complete cached image identity without inventing missing digests."""
    if result.exit_code != 0:
        raise DiscoveryParseError(
            "cached image inspection failed",
            outcome=Outcome.BLOCKED_UPSTREAM,
            reason_code="INPUT_NOT_FROZEN",
        )
    try:
        if (
            len(result.arguments) < 8
            or result.arguments[:2] != ("docker", "--host")
            or not is_local_unix_docker_endpoint(result.arguments[2])
            or result.arguments[3:5] != ("image", "inspect")
            or result.arguments[5:7] != ("--platform", "linux/arm64")
        ):
            raise DiscoveryParseError(
                "cached image inspection arguments are not auditable"
            )
        requested_references = result.arguments[7:]
        payload = json.loads(result.stdout)
        if (
            not isinstance(payload, list)
            or not payload
            or len(payload) != len(requested_references)
        ):
            raise DiscoveryParseError("cached image inspection returned no images")
        images: list[InspectedImage] = []
        for item, source_reference in zip(
            payload,
            requested_references,
            strict=True,
        ):
            known_references = tuple(item["RepoTags"]) + tuple(item["RepoDigests"])
            if source_reference not in known_references:
                raise DiscoveryParseError(
                    "requested image reference is absent from inspected metadata"
                )
            repository = _image_repository(source_reference)
            matching_repo_digests = [
                repo_digest
                for repo_digest in item["RepoDigests"]
                if repo_digest.rsplit("@", 1)[0] == repository
            ]
            if len(matching_repo_digests) != 1:
                raise DiscoveryParseError(
                    "requested image repository digest is missing or ambiguous"
                )
            index_digest = matching_repo_digests[0].rsplit("@", 1)[1]
            platform_digest = item["Descriptor"]["digest"]
            architecture = _normalize_architecture(item["Architecture"])
            operating_system = str(item["Os"]).lower()
            if architecture != "arm64" or operating_system != "linux":
                raise DiscoveryParseError(
                    "cached image inspection is not native linux/arm64"
                )
            tag = source_reference.rsplit("/", 1)[-1].split(":", 1)[1]
            images.append(
                InspectedImage(
                    logical_name=tag,
                    source_reference=source_reference,
                    image_index_digest=index_digest,
                    resolved_platform_digest=platform_digest,
                    architecture=architecture,
                    platform=f"{operating_system}/{architecture}",
                    image_id=item["Id"],
                )
            )
    except (
        DiscoveryParseError,
        AttributeError,
        IndexError,
        KeyError,
        TypeError,
        ValidationError,
        json.JSONDecodeError,
    ) as error:
        message = (
            str(error)
            if isinstance(error, DiscoveryParseError)
            else "cached image metadata is incomplete or invalid"
        )
        raise DiscoveryParseError(
            message,
            outcome=Outcome.BLOCKED_UPSTREAM,
            reason_code="INPUT_NOT_FROZEN",
        ) from error
    return tuple(images)


def _image_repository(reference: str) -> str:
    without_digest = reference.split("@", 1)[0]
    prefix, separator, final_component = without_digest.rpartition("/")
    if ":" in final_component:
        final_component = final_component.rsplit(":", 1)[0]
    return f"{prefix}{separator}{final_component}" if prefix else final_component


def _extract_version(output: str) -> str:
    match = re.search(r"\bversion\s+([^,\s]+)", output, flags=re.IGNORECASE)
    return match.group(1) if match else ""


def compose_plugin_major_version(output: str) -> int | None:
    match = re.fullmatch(
        r"(?:Docker Compose version\s+)?v?(\d+)(?:\.\d+){1,2}",
        output.strip(),
        flags=re.IGNORECASE,
    )
    if match is None:
        return None
    major = int(match.group(1))
    return major if major >= 2 else None


def _run_required(
    runner: CommandRunner,
    arguments: tuple[str, ...],
    timeout_seconds: float,
) -> str:
    result = runner.run(arguments, timeout_seconds=timeout_seconds)
    if result.exit_code != 0:
        raise RuntimeError(
            f"PREFLIGHT_BLOCKED: read-only probe {arguments[0]!r} failed"
        )
    return result.stdout


def _available_memory_bytes(vm_stat_output: str) -> int:
    page_size_match = re.search(r"page size of\s+(\d+)\s+bytes", vm_stat_output)
    if page_size_match is None:
        raise RuntimeError("PREFLIGHT_BLOCKED: vm_stat page size unavailable")
    pages = 0
    for label in ("Pages free", "Pages inactive", "Pages speculative"):
        match = re.search(rf"^{label}:\s+(\d+)\.", vm_stat_output, re.MULTILINE)
        if match is None:
            raise RuntimeError(
                f"PREFLIGHT_BLOCKED: vm_stat field {label!r} unavailable"
            )
        pages += int(match.group(1))
    return pages * int(page_size_match.group(1))


def _available_disk_bytes(df_output: str) -> int:
    lines = [line for line in df_output.splitlines() if line.strip()]
    if len(lines) < 2:
        raise RuntimeError("PREFLIGHT_BLOCKED: disk availability unavailable")
    fields = lines[-1].split()
    if len(fields) < 4:
        raise RuntimeError("PREFLIGHT_BLOCKED: disk output is malformed")
    return int(fields[3]) * 1024


def _normalize_architecture(value: str) -> str:
    return {
        "aarch64": "arm64",
        "arm64": "arm64",
        "x86_64": "amd64",
    }.get(value.lower(), value.lower())


def _docker_desktop_disk_bytes(settings: object) -> int:
    """Extract configured Docker Desktop disk size from settings, not info."""
    if isinstance(settings, dict):
        for key, value in settings.items():
            normalized = key.lower()
            if (
                normalized == "disksizemib"
                and isinstance(value, (int, float))
                and not isinstance(value, bool)
            ):
                return int(value * 1024**2)
            if (
                normalized == "disksizegib"
                and isinstance(value, (int, float))
                and not isinstance(value, bool)
            ):
                return int(value * 1024**3)
            nested = _docker_desktop_disk_bytes(value)
            if nested:
                return nested
    elif isinstance(settings, list):
        for value in settings:
            nested = _docker_desktop_disk_bytes(value)
            if nested:
                return nested
    return 0


def _parse_labels(value: str) -> dict[str, str]:
    labels: dict[str, str] = {}
    for item in value.split(","):
        key, separator, label_value = item.partition("=")
        if separator and key:
            labels[key] = label_value
    return labels


def _result(
    outcome: Outcome,
    reason_codes: tuple[str, ...],
) -> PreflightResult:
    return PreflightResult(
        outcome=outcome,
        exit_code=outcome.exit_code,
        reason_codes=reason_codes,
    )
