"""Contracts for Product v0.2.3.1 Runtime-authority continuity."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import sqlite3
import stat
from typing import Any, Callable, cast, Literal, Mapping

from pydantic import Field, field_validator, model_validator

from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22
from ecomsre.product.baselines import EnvironmentBaselineV1
from ecomsre.product.contracts import ProductModelV1
from ecomsre.product.contracts import ServiceIdentityMapV1, ServiceIdentityV1
from ecomsre.product.environment.capabilities import EnvironmentCapabilityMatrixV1
from ecomsre.product.pilot.baseline_attempts_v023 import (
    BASELINE_READINESS_PASS_V023,
    BaselineAttemptLedgerV023,
    BaselineAttemptV023,
)
from ecomsre.product.pilot.baseline_readiness_v023 import (
    ProductBaselineReadinessAuditV023,
)
from ecomsre.product.pilot.runtime_authority_v02 import (
    PilotRuntimeAuthorityV02,
)
from ecomsre_live_sandbox.contracts import (
    ConfigBundle,
    ResolvedSandbox,
    canonical_json_bytes,
    ensure_private_directory,
    write_private_json,
)
from ecomsre_live_sandbox.control import build_flag_documents
from ecomsre_live_sandbox.environment import SandboxEnvironment


def _require_relative_locator(value: str) -> str:
    candidate = PurePosixPath(value)
    if candidate.is_absolute() or ".." in candidate.parts or str(candidate) != value:
        raise ValueError("continuity locator must be repository-relative")
    if not value or value == ".":
        raise ValueError("continuity locator must be repository-relative")
    return value


class SquashMergeBoundFileV0231(ProductModelV1):
    path: str = Field(min_length=1, max_length=512)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(ge=0)

    _relative_path = field_validator("path")(_require_relative_locator)


class SquashMergeHistoryBindingV0231(ProductModelV1):
    schema_version: Literal["ecomsre.product.squash-merge-history-binding.v0231"] = (
        "ecomsre.product.squash-merge-history-binding.v0231"
    )
    source_pr: int = Field(ge=1)
    source_branch: str = Field(min_length=1, max_length=255)
    source_head: str = Field(pattern=r"^[0-9a-f]{40}$")
    source_terminal: str = Field(pattern=r"^[A-Z][A-Z0-9_]{2,159}$")
    import_pr: int = Field(ge=1)
    import_squash_merge_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    public_base: str = Field(pattern=r"^[0-9a-f]{40}$")
    bound_files: tuple[SquashMergeBoundFileV0231, ...] = Field(min_length=1)
    binding_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_canonical_self_sealed_binding(
        self,
    ) -> "SquashMergeHistoryBindingV0231":
        paths = tuple(item.path for item in self.bound_files)
        if paths != tuple(sorted(set(paths))):
            raise ValueError("squash history bound files are not canonical")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"binding_sha256"})
        )
        if self.binding_sha256 != expected:
            raise ValueError("squash history binding digest differs")
        return self

    @classmethod
    def build(cls, **payload: Any) -> "SquashMergeHistoryBindingV0231":
        files = tuple(
            sorted(
                (
                    item
                    if isinstance(item, SquashMergeBoundFileV0231)
                    else SquashMergeBoundFileV0231.model_validate(item)
                    for item in payload["bound_files"]
                ),
                key=lambda item: item.path,
            )
        )
        body = {
            "schema_version": ("ecomsre.product.squash-merge-history-binding.v0231"),
            **payload,
            "bound_files": files,
        }
        draft = cls.model_construct(**body, binding_sha256="0" * 64)
        normalized = draft.model_dump(mode="json", exclude={"binding_sha256"})
        return cls.model_validate(
            {**normalized, "binding_sha256": semantic_sha256_v22(normalized)}
        )


class ProductV023PrivateStateBindingV0231(ProductModelV1):
    baseline_private_report_locator: str
    baseline_private_report_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    product_data_root_locator: str
    product_database_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    product_database_wal_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    product_database_shm_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    nofault_blocker_locator: str
    nofault_blocker_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    runtime_authority_locator: str
    runtime_authority_file_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    resolved_compose_locator: str
    resolved_compose_file_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    flagd_file_locator: str
    flagd_file_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    _relative_locators = field_validator(
        "baseline_private_report_locator",
        "product_data_root_locator",
        "nofault_blocker_locator",
        "runtime_authority_locator",
        "resolved_compose_locator",
        "flagd_file_locator",
    )(_require_relative_locator)


class ProductBaselineContinuationContextV0231(ProductModelV1):
    schema_version: Literal["ecomsre.product.baseline-continuation-context.v0231"] = (
        "ecomsre.product.baseline-continuation-context.v0231"
    )
    predecessor_head: str = Field(pattern=r"^[0-9a-f]{40}$")
    source_attempt_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_private_report_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    product_data_root_locator: str
    product_data_root_locator_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    environment_id: str = Field(pattern=r"^env-[0-9a-f]{24}$")
    active_baseline_id: str = Field(pattern=r"^base-[0-9a-f]{24}$")
    active_baseline_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    readiness_audit_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    parity_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    active_profile_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    service_identity_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    capability_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    runtime_authority_path: str
    runtime_authority_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    context_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    _relative_locators = field_validator(
        "product_data_root_locator",
        "runtime_authority_path",
    )(_require_relative_locator)

    @model_validator(mode="after")
    def require_self_sealed_context(
        self,
    ) -> "ProductBaselineContinuationContextV0231":
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"context_sha256"})
        )
        if self.context_sha256 != expected:
            raise ValueError("continuation context digest differs")
        return self

    @classmethod
    def build(cls, **payload: Any) -> "ProductBaselineContinuationContextV0231":
        body = {
            "schema_version": ("ecomsre.product.baseline-continuation-context.v0231"),
            **payload,
        }
        draft = cls.model_construct(**body, context_sha256="0" * 64)
        normalized = draft.model_dump(mode="json", exclude={"context_sha256"})
        return cls.model_validate(
            {**normalized, "context_sha256": semantic_sha256_v22(normalized)}
        )


class FlagdBindDescriptorV0231(ProductModelV1):
    schema_version: Literal["ecomsre.product.flagd-bind-descriptor.v0231"] = (
        "ecomsre.product.flagd-bind-descriptor.v0231"
    )
    source_attempt_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    flagd_directory_locator: str
    flagd_directory_locator_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    flag_file_locator: str
    flag_file_locator_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    flag_file_bytes_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    flag_file_mode: Literal[384]
    directory_mode: Literal[448]
    container_destination: Literal["/etc/flagd"]
    mount_mode: Literal["READ_ONLY"]
    baseline_document_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    fault_document_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    config_bundle_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    resolved_compose_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    descriptor_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    _relative_locators = field_validator(
        "flagd_directory_locator",
        "flag_file_locator",
    )(_require_relative_locator)

    @model_validator(mode="after")
    def require_exact_self_sealed_descriptor(self) -> "FlagdBindDescriptorV0231":
        if self.flag_file_locator != str(
            PurePosixPath(self.flagd_directory_locator) / "demo.flagd.json"
        ):
            raise ValueError("flagd descriptor file locator differs")
        if self.flag_file_bytes_sha256 != self.baseline_document_sha256:
            raise ValueError("flagd descriptor Baseline bytes differ")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"descriptor_sha256"})
        )
        if self.descriptor_sha256 != expected:
            raise ValueError("flagd descriptor digest differs")
        return self

    @classmethod
    def build(cls, **payload: Any) -> "FlagdBindDescriptorV0231":
        body = {
            "schema_version": "ecomsre.product.flagd-bind-descriptor.v0231",
            **payload,
        }
        draft = cls.model_construct(**body, descriptor_sha256="0" * 64)
        normalized = draft.model_dump(mode="json", exclude={"descriptor_sha256"})
        return cls.model_validate(
            {**normalized, "descriptor_sha256": semantic_sha256_v22(normalized)}
        )


class RuntimeAuthorityContinuityDescriptorV0231(ProductModelV1):
    schema_version: Literal[
        "ecomsre.product.runtime-authority-continuity-descriptor.v0231"
    ] = "ecomsre.product.runtime-authority-continuity-descriptor.v0231"
    environment_id: str = Field(pattern=r"^env-[0-9a-f]{24}$")
    allowed_logical_services: tuple[str, ...] = Field(min_length=1)
    profile_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    daemon_identity_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    docker_context_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    config_bundle_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    resolved_sandbox_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    resolved_endpoints_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    ownership_scope_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    read_authority_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    pilot_runtime_authority_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    connector_binding_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    resolved_compose_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    flagd_bind_descriptor_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    active_baseline_id: str = Field(pattern=r"^base-[0-9a-f]{24}$")
    active_baseline_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    descriptor_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_canonical_self_sealed_descriptor(
        self,
    ) -> "RuntimeAuthorityContinuityDescriptorV0231":
        if self.allowed_logical_services != tuple(
            sorted(set(self.allowed_logical_services))
        ):
            raise ValueError("Runtime continuity services are not canonical")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"descriptor_sha256"})
        )
        if self.descriptor_sha256 != expected:
            raise ValueError("Runtime authority descriptor digest differs")
        return self

    @classmethod
    def build(cls, **payload: Any) -> "RuntimeAuthorityContinuityDescriptorV0231":
        body = {
            "schema_version": (
                "ecomsre.product.runtime-authority-continuity-descriptor.v0231"
            ),
            **payload,
            "allowed_logical_services": tuple(
                sorted(set(payload["allowed_logical_services"]))
            ),
        }
        draft = cls.model_construct(**body, descriptor_sha256="0" * 64)
        normalized = draft.model_dump(mode="json", exclude={"descriptor_sha256"})
        return cls.model_validate(
            {**normalized, "descriptor_sha256": semantic_sha256_v22(normalized)}
        )


class ContinuityPreflightReportV0231(ProductModelV1):
    schema_version: Literal["ecomsre.product.continuity-preflight.v0231"] = (
        "ecomsre.product.continuity-preflight.v0231"
    )
    terminal: Literal["ECOMSRE_PRODUCT_V0231_CONTINUITY_PREFLIGHT_PASS"]
    descriptor_terminal: Literal[
        "ECOMSRE_PRODUCT_V0231_CONTINUITY_DESCRIPTOR_PASS"
    ]
    context_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    flagd_bind_descriptor_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    runtime_authority_descriptor_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    resolved_compose_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    resolved_sandbox_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    read_authority_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    pilot_runtime_authority_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    connector_binding_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    flagd_path_exact: Literal[True]
    flagd_bytes_exact: Literal[True]
    resolved_compose_exact: Literal[True]
    config_bundle_exact: Literal[True]
    daemon_identity_exact: Literal[True]
    docker_context_exact: Literal[True]
    resolved_sandbox_exact: Literal[True]
    resolved_endpoints_exact: Literal[True]
    ownership_scope_exact: Literal[True]
    product_baseline_exact: Literal[True]
    docker_start_count: Literal[0]
    live_session_count: Literal[0]
    accepted_incident_count: Literal[0]
    diagnosis_count: Literal[0]
    fault_attempt_count: Literal[0]
    knowledge_loop_campaign_count: Literal[0]
    fault_family_count: Literal[0]
    agent_writes: Literal[0]
    runbook_executions: Literal[0]
    action_authority: Literal["NONE"]
    owned_resource_count: Literal[0]
    report_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_self_sealed_report(self) -> "ContinuityPreflightReportV0231":
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"report_sha256"})
        )
        if self.report_sha256 != expected:
            raise ValueError("continuity preflight digest differs")
        return self

    @classmethod
    def build(cls, **payload: Any) -> "ContinuityPreflightReportV0231":
        body = {
            "schema_version": "ecomsre.product.continuity-preflight.v0231",
            **payload,
        }
        draft = cls.model_construct(**body, report_sha256="0" * 64)
        normalized = draft.model_dump(mode="json", exclude={"report_sha256"})
        return cls.model_validate(
            {**normalized, "report_sha256": semantic_sha256_v22(normalized)}
        )


def _flagd_mounts_are_exact(
    resolved_compose: Mapping[str, Any],
    *,
    directory: Path,
) -> bool:
    services = resolved_compose.get("services")
    if not isinstance(services, Mapping):
        return False
    expected = {
        "flagd": ("/etc/flagd", True),
        "flagd-ui": ("/app/data", False),
    }
    for service_name, (target, read_only) in expected.items():
        service = services.get(service_name)
        volumes = service.get("volumes") if isinstance(service, Mapping) else None
        if not isinstance(volumes, list) or len(volumes) != 1:
            return False
        volume = volumes[0]
        if not isinstance(volume, Mapping):
            return False
        observed_read_only = volume.get("read_only") is True
        if (
            volume.get("type") != "bind"
            or volume.get("source") != str(directory)
            or volume.get("target") != target
            or observed_read_only is not read_only
        ):
            return False
    return True


def admit_flagd_bind_descriptor_v0231(
    *,
    predecessor_root: Path,
    binding: ProductV023PrivateStateBindingV0231,
    context: ProductBaselineContinuationContextV0231,
    bundle: ConfigBundle,
    resolved_compose: Mapping[str, Any],
    reconstruction_proof_path: Path | None = None,
) -> FlagdBindDescriptorV0231:
    root_input = Path(predecessor_root).expanduser()
    if root_input.is_symlink():
        raise ValueError("predecessor checkout must not be a symlink")
    root = root_input.resolve(strict=True)
    attempt_root = PurePosixPath(binding.baseline_private_report_locator).parent.parent
    expected_directory = attempt_root / "private/demo/runtime/flagd"
    expected_file = expected_directory / "demo.flagd.json"
    if (
        PurePosixPath(binding.product_data_root_locator) != attempt_root / "product"
        or PurePosixPath(binding.flagd_file_locator) != expected_file
        or context.product_data_root_locator != binding.product_data_root_locator
        or context.source_private_report_sha256
        != binding.baseline_private_report_sha256
    ):
        raise ValueError("flagd descriptor attempt binding differs")

    root_fd = _open_root_fd(root)
    directory_fd = -1
    file_fd = -1
    try:
        directory_fd = _open_bound_path_fd(
            root_fd,
            str(expected_directory),
            regular_file=False,
        )
        directory_metadata = os.fstat(directory_fd)
        if directory_metadata.st_uid != os.getuid():
            raise PermissionError("exact flagd ownership differs")
        directory_mode = stat.S_IMODE(directory_metadata.st_mode)
        if directory_mode != 0o700:
            raise PermissionError("exact flagd directory mode differs")
        upstream_fd = _open_bound_path_fd(
            root_fd,
            "third_party/opentelemetry-demo/src/flagd/demo.flagd.json",
            regular_file=True,
        )
        try:
            upstream = json.loads(_descriptor_bytes(upstream_fd))
        finally:
            os.close(upstream_fd)
        if not isinstance(upstream, Mapping):
            raise ValueError("upstream flag document is invalid")
        baseline, fault = build_flag_documents(upstream, bundle)
        baseline_bytes = canonical_json_bytes(baseline)
        baseline_sha256 = hashlib.sha256(baseline_bytes).hexdigest()
        if (
            baseline_sha256 != binding.flagd_file_sha256
            or baseline_sha256 != bundle.scenario.baseline_document_sha256
        ):
            raise ValueError(
                "BLOCKED_ECOMSRE_PRODUCT_V0231_FLAGD_BIND_CONTINUITY: "
                "reconstructed Baseline bytes differ before write"
            )
        try:
            file_fd = _open_bound_path_fd(
                root_fd,
                str(expected_file),
                regular_file=True,
            )
        except FileNotFoundError:
            if reconstruction_proof_path is None:
                raise ValueError(
                    "BLOCKED_ECOMSRE_PRODUCT_V0231_FLAGD_BIND_CONTINUITY: "
                    "exact flagd file is absent"
                ) from None
            proof_parent_fd, proof_name = _prepare_private_create_once_target(
                reconstruction_proof_path
            )
            try:
                file_fd = _create_exact_flag_file(
                    directory_fd,
                    payload=baseline_bytes,
                )
                try:
                    _write_private_reconstruction_proof(
                        proof_parent_fd,
                        proof_name=proof_name,
                        payload={
                            "schema_version": (
                                "ecomsre.product.flagd-reconstruction-proof.v0231"
                            ),
                        "source_attempt_sha256": context.source_attempt_sha256,
                        "flag_file_locator": str(expected_file),
                        "flag_file_bytes_sha256": baseline_sha256,
                            "baseline_document_sha256": (
                                bundle.scenario.baseline_document_sha256
                            ),
                            "config_bundle_sha256": semantic_sha256_v22(
                                bundle.model_dump(mode="json")
                            ),
                        },
                    )
                except BaseException:
                    os.close(file_fd)
                    file_fd = -1
                    os.unlink("demo.flagd.json", dir_fd=directory_fd)
                    raise
            finally:
                os.close(proof_parent_fd)
        file_metadata = os.fstat(file_fd)
        if file_metadata.st_uid != os.getuid():
            raise PermissionError("exact flagd ownership differs")
        file_mode = stat.S_IMODE(file_metadata.st_mode)
        if file_mode != 0o600:
            raise PermissionError("exact flag file mode differs")
        flag_bytes = _descriptor_bytes(file_fd)
        if hashlib.sha256(flag_bytes).hexdigest() != binding.flagd_file_sha256:
            raise ValueError("exact flagd file bytes differ")
        if flag_bytes != baseline_bytes:
            raise ValueError("exact flagd file differs from frozen Baseline document")
        directory = root / expected_directory
        if not _flagd_mounts_are_exact(resolved_compose, directory=directory):
            raise ValueError("exact flagd mounts differ")
        return FlagdBindDescriptorV0231.build(
            source_attempt_sha256=context.source_attempt_sha256,
            flagd_directory_locator=str(expected_directory),
            flagd_directory_locator_sha256=hashlib.sha256(
                os.fsencode(str(directory))
            ).hexdigest(),
            flag_file_locator=str(expected_file),
            flag_file_locator_sha256=hashlib.sha256(
                os.fsencode(str(root / expected_file))
            ).hexdigest(),
            flag_file_bytes_sha256=binding.flagd_file_sha256,
            flag_file_mode=file_mode,
            directory_mode=directory_mode,
            container_destination="/etc/flagd",
            mount_mode="READ_ONLY",
            baseline_document_sha256=bundle.scenario.baseline_document_sha256,
            fault_document_sha256=bundle.scenario.fault_document_sha256,
            config_bundle_sha256=semantic_sha256_v22(bundle.model_dump(mode="json")),
            resolved_compose_sha256=semantic_sha256_v22(resolved_compose),
        )
    finally:
        if file_fd >= 0:
            os.close(file_fd)
        if directory_fd >= 0:
            os.close(directory_fd)
        os.close(root_fd)


def _prepare_private_create_once_target(path: Path) -> tuple[int, str]:
    target = Path(path).expanduser()
    parent = target.parent
    if not target.name or target.name in {".", ".."}:
        raise ValueError("private reconstruction proof target is invalid")
    if parent.resolve(strict=True) != parent.absolute():
        raise ValueError("private reconstruction proof parent contains a symlink")
    descriptor = _open_root_fd(parent)
    metadata = os.fstat(descriptor)
    if metadata.st_uid != os.getuid() or stat.S_IMODE(metadata.st_mode) != 0o700:
        os.close(descriptor)
        raise PermissionError("private reconstruction proof directory differs")
    try:
        os.stat(target.name, dir_fd=descriptor, follow_symlinks=False)
    except FileNotFoundError:
        return descriptor, target.name
    os.close(descriptor)
    raise FileExistsError(f"private reconstruction proof already exists: {target.name}")


def _create_exact_flag_file(directory_fd: int, *, payload: bytes) -> int:
    descriptor = os.open(
        "demo.flagd.json",
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
        0o600,
        dir_fd=directory_fd,
    )
    try:
        offset = 0
        while offset < len(payload):
            written = os.write(descriptor, payload[offset:])
            if written <= 0:
                raise OSError("exact flagd reconstruction write made no progress")
            offset += written
        os.fsync(descriptor)
    except BaseException:
        os.close(descriptor)
        os.unlink("demo.flagd.json", dir_fd=directory_fd)
        raise
    else:
        os.close(descriptor)
    try:
        return os.open(
            "demo.flagd.json",
            os.O_RDONLY | os.O_NOFOLLOW,
            dir_fd=directory_fd,
        )
    except BaseException:
        os.unlink("demo.flagd.json", dir_fd=directory_fd)
        raise


def _write_private_reconstruction_proof(
    parent_fd: int,
    *,
    proof_name: str,
    payload: Mapping[str, Any],
) -> None:
    descriptor = os.open(
        proof_name,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
        0o600,
        dir_fd=parent_fd,
    )
    try:
        body = canonical_json_bytes(payload)
        offset = 0
        while offset < len(body):
            written = os.write(descriptor, body[offset:])
            if written <= 0:
                raise OSError("private reconstruction proof write made no progress")
            offset += written
        os.fsync(descriptor)
    except BaseException:
        os.close(descriptor)
        os.unlink(proof_name, dir_fd=parent_fd)
        raise
    else:
        os.close(descriptor)


def build_runtime_authority_continuity_descriptor_v0231(
    *,
    authority: PilotRuntimeAuthorityV02,
    context: ProductBaselineContinuationContextV0231,
    flagd_descriptor: FlagdBindDescriptorV0231,
    resolved_compose_sha256: str,
) -> RuntimeAuthorityContinuityDescriptorV0231:
    read_authority = authority.read_authority
    if (
        authority.environment_id != context.environment_id
        or authority.pilot_authority_sha256 != context.runtime_authority_sha256
        or read_authority.config_bundle_sha256
        != flagd_descriptor.config_bundle_sha256
        or resolved_compose_sha256 != flagd_descriptor.resolved_compose_sha256
        or any(
            value is None
            for value in (
                read_authority.daemon_identity_sha256,
                read_authority.docker_context_sha256,
                read_authority.config_bundle_sha256,
                read_authority.resolved_sandbox_sha256,
            )
        )
    ):
        raise ValueError(
            "BLOCKED_ECOMSRE_PRODUCT_V0231_RUNTIME_AUTHORITY_CONTINUITY: "
            "preserved descriptor inputs differ"
        )
    return RuntimeAuthorityContinuityDescriptorV0231.build(
        environment_id=authority.environment_id,
        allowed_logical_services=authority.allowed_logical_services,
        profile_sha256=authority.profile_sha256,
        daemon_identity_sha256=read_authority.daemon_identity_sha256,
        docker_context_sha256=read_authority.docker_context_sha256,
        config_bundle_sha256=read_authority.config_bundle_sha256,
        resolved_sandbox_sha256=read_authority.resolved_sandbox_sha256,
        resolved_endpoints_sha256=read_authority.resolved_endpoints_sha256,
        ownership_scope_sha256=read_authority.ownership_scope_sha256,
        read_authority_sha256=read_authority.authority_sha256,
        pilot_runtime_authority_sha256=authority.pilot_authority_sha256,
        connector_binding_sha256=authority.connector_binding_sha256,
        resolved_compose_sha256=resolved_compose_sha256,
        flagd_bind_descriptor_sha256=flagd_descriptor.descriptor_sha256,
        active_baseline_id=context.active_baseline_id,
        active_baseline_sha256=context.active_baseline_sha256,
    )


def _expected_rebound_authority_v0231(
    *,
    preserved: PilotRuntimeAuthorityV02,
    docker: Mapping[str, str],
    bundle: ConfigBundle,
    resolved: ResolvedSandbox,
) -> PilotRuntimeAuthorityV02:
    return PilotRuntimeAuthorityV02.build(
        environment_id=preserved.environment_id,
        allowed_logical_services=preserved.allowed_logical_services,
        profile_sha256=preserved.profile_sha256,
        daemon_identity_sha256=semantic_sha256_v22(
            {"daemon_identity": docker["daemon_id"].strip()}
        ),
        docker_context_sha256=semantic_sha256_v22(
            {"docker_context": docker["context"]}
        ),
        config_bundle_sha256=semantic_sha256_v22(bundle.model_dump(mode="json")),
        resolved_sandbox_sha256=semantic_sha256_v22(
            resolved.model_dump(mode="json")
        ),
        resolved_endpoints_sha256=semantic_sha256_v22(
            {
                "prometheus": resolved.endpoints.prometheus,
                "opensearch": resolved.endpoints.opensearch,
                "jaeger": resolved.endpoints.jaeger,
                "docker": docker["endpoint"],
            }
        ),
        ownership_scope_sha256=semantic_sha256_v22(
            {
                "compose_project": bundle.environment.compose_project,
                "sandbox_label_key": bundle.environment.sandbox_label_key,
                "sandbox_label_value": bundle.environment.sandbox_id,
            }
        ),
    )


class AuthorityContinuousSandboxLifecycleV0231:
    """Product lifecycle that preserves the predecessor checkout and flag path."""

    def __init__(
        self,
        *,
        predecessor_root: Path,
        private_root: Path,
        binding: ProductV023PrivateStateBindingV0231,
        context: ProductBaselineContinuationContextV0231,
        bundle: ConfigBundle,
        preserved_authority: PilotRuntimeAuthorityV02,
        preserved_resolved_compose: Mapping[str, Any],
        environment_factory: Callable[..., Any] = SandboxEnvironment,
    ) -> None:
        self.predecessor_root = Path(predecessor_root).resolve(strict=True)
        self.private_root = Path(private_root)
        self.binding = binding
        self.context = context
        self.bundle = bundle
        self.preserved_authority = preserved_authority
        self.preserved_resolved_compose = dict(preserved_resolved_compose)
        self.environment_factory = environment_factory
        self.environment: Any = None
        self.flagd_descriptor: FlagdBindDescriptorV0231 | None = None
        self.runtime_descriptor: RuntimeAuthorityContinuityDescriptorV0231 | None = (
            None
        )
        self.preflight_report: ContinuityPreflightReportV0231 | None = None
        self.admitted_resolved_sha256: str | None = None
        self._admitted_raw_compose: Mapping[str, Any] | None = None
        self.started = False
        self.ready = False
        self.controller: Any = None

    @property
    def flag_file(self) -> Path:
        return self.predecessor_root / self.binding.flagd_file_locator

    def admit_prestart(self) -> ContinuityPreflightReportV0231:
        if self.preflight_report is not None:
            return self.preflight_report
        ensure_private_directory(self.private_root)
        preserved_compose_sha256 = semantic_sha256_v22(
            self.preserved_resolved_compose
        )
        flagd = admit_flagd_bind_descriptor_v0231(
            predecessor_root=self.predecessor_root,
            binding=self.binding,
            context=self.context,
            bundle=self.bundle,
            resolved_compose=self.preserved_resolved_compose,
            reconstruction_proof_path=(
                self.private_root / "flagd-reconstruction.json"
            ),
        )
        runtime = build_runtime_authority_continuity_descriptor_v0231(
            authority=self.preserved_authority,
            context=self.context,
            flagd_descriptor=flagd,
            resolved_compose_sha256=preserved_compose_sha256,
        )
        environment = self.environment_factory(
            repository_root=self.predecessor_root,
            bundle=self.bundle,
            flagd_directory=self.flag_file.parent,
        )
        docker = environment.verify_local_docker()
        if not isinstance(docker, Mapping) or any(
            not isinstance(docker.get(name), str)
            for name in ("context", "endpoint", "daemon_id")
        ):
            raise ValueError("fresh local Docker identity is incomplete")
        docker_identity = {
            name: str(docker[name]) for name in ("context", "endpoint", "daemon_id")
        }
        environment.verify_upstream()
        owned = environment.verify_owned_resources(require_complete=False)
        if (
            not isinstance(owned, Mapping)
            or set(owned) != {"container", "network", "volume"}
            or any(
                not isinstance(value, int) or value != 0 for value in owned.values()
            )
        ):
            raise ValueError(
                "BLOCKED_ECOMSRE_PRODUCT_V0231_PREEXISTING_OWNED_RESOURCES"
            )
        resolved, raw_compose = environment.resolve()
        if not isinstance(resolved, ResolvedSandbox) or not isinstance(
            raw_compose, Mapping
        ):
            raise TypeError("fresh pre-start Compose resolve is malformed")
        current_compose_sha256 = semantic_sha256_v22(raw_compose)
        current_resolved_sha256 = semantic_sha256_v22(
            resolved.model_dump(mode="json")
        )
        if (
            current_compose_sha256 != preserved_compose_sha256
            or resolved.compose_sha256 != preserved_compose_sha256
            or current_resolved_sha256
            != self.preserved_authority.read_authority.resolved_sandbox_sha256
        ):
            raise ValueError("BLOCKED_ECOMSRE_PRODUCT_V0231_COMPOSE_CONTINUITY")
        rebound = _expected_rebound_authority_v0231(
            preserved=self.preserved_authority,
            docker=docker_identity,
            bundle=self.bundle,
            resolved=resolved,
        )
        if rebound != self.preserved_authority:
            raise ValueError(
                "BLOCKED_ECOMSRE_PRODUCT_V0231_RUNTIME_AUTHORITY_CONTINUITY"
            )
        revalidated_flagd = admit_flagd_bind_descriptor_v0231(
            predecessor_root=self.predecessor_root,
            binding=self.binding,
            context=self.context,
            bundle=self.bundle,
            resolved_compose=raw_compose,
        )
        if revalidated_flagd != flagd:
            raise ValueError(
                "BLOCKED_ECOMSRE_PRODUCT_V0231_FLAGD_BIND_CONTINUITY"
            )

        write_private_json(
            self.private_root / "pre-start-resolve.json",
            {
                "schema_version": (
                    "ecomsre.product.private-pre-start-resolve.v0231"
                ),
                "predecessor_root": str(self.predecessor_root),
                "flagd_directory": str(self.flag_file.parent),
                "docker": docker_identity,
                "raw_compose": raw_compose,
                "resolved_sandbox": resolved.model_dump(mode="json"),
                "rebound_authority": rebound.model_dump(mode="json"),
            },
            create_once=True,
        )
        report = ContinuityPreflightReportV0231.build(
            terminal="ECOMSRE_PRODUCT_V0231_CONTINUITY_PREFLIGHT_PASS",
            descriptor_terminal="ECOMSRE_PRODUCT_V0231_CONTINUITY_DESCRIPTOR_PASS",
            context_sha256=self.context.context_sha256,
            flagd_bind_descriptor_sha256=flagd.descriptor_sha256,
            runtime_authority_descriptor_sha256=runtime.descriptor_sha256,
            resolved_compose_sha256=current_compose_sha256,
            resolved_sandbox_sha256=current_resolved_sha256,
            read_authority_sha256=rebound.read_authority.authority_sha256,
            pilot_runtime_authority_sha256=rebound.pilot_authority_sha256,
            connector_binding_sha256=rebound.connector_binding_sha256,
            flagd_path_exact=True,
            flagd_bytes_exact=True,
            resolved_compose_exact=True,
            config_bundle_exact=True,
            daemon_identity_exact=True,
            docker_context_exact=True,
            resolved_sandbox_exact=True,
            resolved_endpoints_exact=True,
            ownership_scope_exact=True,
            product_baseline_exact=True,
            docker_start_count=0,
            live_session_count=0,
            accepted_incident_count=0,
            diagnosis_count=0,
            fault_attempt_count=0,
            knowledge_loop_campaign_count=0,
            fault_family_count=0,
            agent_writes=0,
            runbook_executions=0,
            action_authority="NONE",
            owned_resource_count=0,
        )
        self.environment = environment
        self.flagd_descriptor = flagd
        self.runtime_descriptor = runtime
        self.preflight_report = report
        self.admitted_resolved_sha256 = current_resolved_sha256
        self._admitted_raw_compose = dict(raw_compose)
        return report

    def start(self) -> None:
        if (
            self.preflight_report is None
            or self.environment is None
            or self.flagd_descriptor is None
            or self._admitted_raw_compose is None
        ):
            raise RuntimeError("Runtime-continuity preflight has not passed")
        if self.started:
            raise RuntimeError("Runtime-continuity lifecycle already started")
        docker = self.environment.verify_local_docker()
        if not isinstance(docker, Mapping) or any(
            not isinstance(docker.get(name), str)
            for name in ("context", "endpoint", "daemon_id")
        ):
            raise ValueError("fresh pre-start Docker identity is incomplete")
        docker_identity = {
            name: str(docker[name]) for name in ("context", "endpoint", "daemon_id")
        }
        self.environment.verify_upstream()
        owned = self.environment.verify_owned_resources(require_complete=False)
        if (
            not isinstance(owned, Mapping)
            or set(owned) != {"container", "network", "volume"}
            or any(
                not isinstance(value, int) or value != 0 for value in owned.values()
            )
        ):
            raise ValueError(
                "BLOCKED_ECOMSRE_PRODUCT_V0231_PREEXISTING_OWNED_RESOURCES"
            )
        resolved, raw_compose = self.environment.resolve()
        if not isinstance(resolved, ResolvedSandbox) or not isinstance(
            raw_compose, Mapping
        ):
            raise TypeError("fresh start-boundary Compose resolve is malformed")
        compose_sha256 = semantic_sha256_v22(raw_compose)
        resolved_sha256 = semantic_sha256_v22(resolved.model_dump(mode="json"))
        if (
            compose_sha256
            != semantic_sha256_v22(self.preserved_resolved_compose)
            or resolved.compose_sha256 != compose_sha256
            or resolved_sha256 != self.admitted_resolved_sha256
            or resolved_sha256
            != self.preserved_authority.read_authority.resolved_sandbox_sha256
        ):
            raise ValueError("BLOCKED_ECOMSRE_PRODUCT_V0231_COMPOSE_CONTINUITY")
        rebound = _expected_rebound_authority_v0231(
            preserved=self.preserved_authority,
            docker=docker_identity,
            bundle=self.bundle,
            resolved=resolved,
        )
        if rebound != self.preserved_authority:
            raise ValueError(
                "BLOCKED_ECOMSRE_PRODUCT_V0231_RUNTIME_AUTHORITY_CONTINUITY"
            )
        revalidated = admit_flagd_bind_descriptor_v0231(
            predecessor_root=self.predecessor_root,
            binding=self.binding,
            context=self.context,
            bundle=self.bundle,
            resolved_compose=raw_compose,
        )
        if revalidated != self.flagd_descriptor:
            raise ValueError(
                "BLOCKED_ECOMSRE_PRODUCT_V0231_FLAGD_BIND_CONTINUITY"
            )
        self.environment.start()
        self.started = True

    def wait_ready(self, *, timeout_seconds: float = 300) -> None:
        if not self.started:
            raise RuntimeError("Runtime-continuity lifecycle has not started")
        self.environment.wait_healthy(timeout_seconds=timeout_seconds)
        self.ready = True

    def authorize_reads(self, *, timeout_seconds: float = 5.0) -> Any:
        from ecomsre.dta_v2.telemetry_adapters import (
            LocalSandboxReadBackend,
            _issue_owned_read_capability,
        )

        if (
            not self.ready
            or self.environment is None
            or self.admitted_resolved_sha256 is None
        ):
            raise RuntimeError("Runtime-continuity lifecycle is not ready")
        capability = _issue_owned_read_capability(
            environment=self.environment,
            bundle=self.bundle,
            admitted_resolved_sha256=self.admitted_resolved_sha256,
            timeout_seconds=timeout_seconds,
        )
        backend = LocalSandboxReadBackend._from_owned_capability(capability)
        authority_inputs = {
            "daemon_identity_sha256": backend.authority.daemon_identity_sha256,
            "docker_context_sha256": backend.authority.docker_context_sha256,
            "config_bundle_sha256": backend.authority.config_bundle_sha256,
            "resolved_sandbox_sha256": backend.authority.resolved_sandbox_sha256,
            "resolved_endpoints_sha256": backend.authority.resolved_endpoints_sha256,
            "ownership_scope_sha256": backend.authority.ownership_scope_sha256,
        }
        if any(not isinstance(value, str) for value in authority_inputs.values()):
            raise ValueError("fresh post-start Runtime authority is incomplete")
        rebound = PilotRuntimeAuthorityV02.build(
            environment_id=self.preserved_authority.environment_id,
            allowed_logical_services=(
                self.preserved_authority.allowed_logical_services
            ),
            profile_sha256=self.preserved_authority.profile_sha256,
            **cast(dict[str, str], authority_inputs),
        )
        if rebound != self.preserved_authority:
            raise ValueError(
                "BLOCKED_ECOMSRE_PRODUCT_V0231_RUNTIME_AUTHORITY_CONTINUITY"
            )
        return backend

    def cleanup_owned(self, *, baseline_unchanged: bool) -> Any:
        if self.environment is None:
            raise RuntimeError("Runtime-continuity lifecycle is unavailable")
        return self.environment.cleanup(baseline_restored=baseline_unchanged)


def _open_root_fd(root: Path) -> int:
    before = root.lstat()
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISDIR(before.st_mode):
        raise ValueError("predecessor checkout is not a physical directory")
    if not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_DIRECTORY"):
        raise ValueError("fd-relative no-follow traversal is unavailable")
    descriptor = os.open(
        root,
        os.O_RDONLY | os.O_NOFOLLOW | os.O_DIRECTORY,
    )
    after = os.fstat(descriptor)
    if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
        os.close(descriptor)
        raise ValueError("predecessor checkout changed during admission")
    return descriptor


def _open_bound_path_fd(
    root_fd: int,
    locator: str,
    *,
    regular_file: bool,
) -> int:
    _require_relative_locator(locator)
    parts = PurePosixPath(locator).parts
    current_fd = os.dup(root_fd)
    try:
        for index, part in enumerate(parts):
            final = index == len(parts) - 1
            flags = os.O_RDONLY | os.O_NOFOLLOW
            if not final or not regular_file:
                flags |= os.O_DIRECTORY
            next_fd = os.open(part, flags, dir_fd=current_fd)
            metadata = os.fstat(next_fd)
            expected = stat.S_ISREG if final and regular_file else stat.S_ISDIR
            if not expected(metadata.st_mode):
                os.close(next_fd)
                kind = "regular file" if final and regular_file else "directory"
                raise ValueError(f"continuity locator is not a {kind}: {locator}")
            os.close(current_fd)
            current_fd = next_fd
        return current_fd
    except BaseException:
        os.close(current_fd)
        raise


def _descriptor_bytes(descriptor: int) -> bytes:
    os.lseek(descriptor, 0, os.SEEK_SET)
    with os.fdopen(os.dup(descriptor), "rb") as handle:
        return handle.read()


def _bound_file_bytes_from_fd(
    root_fd: int,
    locator: str,
    expected_sha256: str,
) -> bytes:
    descriptor = _open_bound_path_fd(root_fd, locator, regular_file=True)
    try:
        payload = _descriptor_bytes(descriptor)
    finally:
        os.close(descriptor)
    if hashlib.sha256(payload).hexdigest() != expected_sha256:
        raise ValueError(f"continuity private byte binding differs: {locator}")
    return payload


def _bound_file_bytes(root: Path, locator: str, expected_sha256: str) -> bytes:
    root_fd = _open_root_fd(root)
    try:
        return _bound_file_bytes_from_fd(root_fd, locator, expected_sha256)
    finally:
        os.close(root_fd)


def load_preserved_runtime_inputs_v0231(
    *,
    predecessor_root: Path,
    binding: ProductV023PrivateStateBindingV0231,
) -> tuple[PilotRuntimeAuthorityV02, dict[str, Any]]:
    root = Path(predecessor_root).expanduser().resolve(strict=True)
    authority = PilotRuntimeAuthorityV02.model_validate_json(
        _bound_file_bytes(
            root,
            binding.runtime_authority_locator,
            binding.runtime_authority_file_sha256,
        )
    )
    compose = json.loads(
        _bound_file_bytes(
            root,
            binding.resolved_compose_locator,
            binding.resolved_compose_file_sha256,
        )
    )
    if not isinstance(compose, dict):
        raise ValueError("preserved resolved Compose is not an object")
    return authority, compose


_SQLITE_BUNDLE_NAMES_V0231 = (
    "product.sqlite3",
    "product.sqlite3-shm",
    "product.sqlite3-wal",
)


def _sqlite_bundle_snapshot(
    product_data_root_fd: int,
    *,
    binding: ProductV023PrivateStateBindingV0231,
) -> tuple[dict[str, tuple[int, int, int, int, str]], bytes]:
    present = {
        name
        for name in os.listdir(product_data_root_fd)
        if name.startswith("product.sqlite3")
    }
    if present != set(_SQLITE_BUNDLE_NAMES_V0231):
        raise ValueError("preserved Product SQLite sidecar set differs")
    expected = {
        "product.sqlite3": binding.product_database_sha256,
        "product.sqlite3-shm": binding.product_database_shm_sha256,
        "product.sqlite3-wal": binding.product_database_wal_sha256,
    }
    snapshot: dict[str, tuple[int, int, int, int, str]] = {}
    database_bytes = b""
    for name in _SQLITE_BUNDLE_NAMES_V0231:
        descriptor = os.open(
            name,
            os.O_RDONLY | os.O_NOFOLLOW,
            dir_fd=product_data_root_fd,
        )
        try:
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode):
                raise ValueError(f"preserved Product SQLite member is not regular: {name}")
            payload = _descriptor_bytes(descriptor)
        finally:
            os.close(descriptor)
        digest = hashlib.sha256(payload).hexdigest()
        if digest != expected[name]:
            raise ValueError(f"preserved Product SQLite member differs: {name}")
        snapshot[name] = (
            metadata.st_dev,
            metadata.st_ino,
            stat.S_IMODE(metadata.st_mode),
            metadata.st_size,
            digest,
        )
        if name == "product.sqlite3":
            database_bytes = payload
        elif name == "product.sqlite3-wal" and payload:
            raise ValueError("preserved Product SQLite WAL is not empty")
    return snapshot, database_bytes


def _read_only_connection(database_bytes: bytes) -> sqlite3.Connection:
    if len(database_bytes) < 100 or database_bytes[:16] != b"SQLite format 3\x00":
        raise ValueError("preserved Product SQLite image is invalid")
    read_write_versions = database_bytes[18:20]
    if read_write_versions == b"\x02\x02":
        database_image = bytearray(database_bytes)
        database_image[18:20] = b"\x01\x01"
        database_bytes = bytes(database_image)
    elif read_write_versions != b"\x01\x01":
        raise ValueError("preserved Product SQLite image mode differs")
    connection = sqlite3.connect(":memory:", isolation_level=None)
    connection.deserialize(database_bytes)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only = ON")
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def _database_bindings(
    database_bytes: bytes,
    *,
    environment_id: str,
) -> tuple[
    EnvironmentBaselineV1,
    ProductBaselineReadinessAuditV023,
    ServiceIdentityMapV1,
    EnvironmentCapabilityMatrixV1,
    dict[str, int],
]:
    with _read_only_connection(database_bytes) as connection:
        baseline_rows = connection.execute(
            "SELECT payload_json, active FROM baseline_versions "
            "WHERE environment_id = ? ORDER BY baseline_id",
            (environment_id,),
        ).fetchall()
        audit_rows = connection.execute(
            "SELECT payload_json FROM baseline_readiness_audits_v023 "
            "WHERE environment_id = ? ORDER BY baseline_id",
            (environment_id,),
        ).fetchall()
        service_rows = connection.execute(
            "SELECT payload_json FROM services WHERE environment_id = ? "
            "ORDER BY logical_service",
            (environment_id,),
        ).fetchall()
        capability_row = connection.execute(
            "SELECT payload_json FROM environment_capability_matrices "
            "WHERE environment_id = ?",
            (environment_id,),
        ).fetchone()
        environment_count = int(
            connection.execute(
                "SELECT COUNT(*) FROM environments WHERE environment_id = ?",
                (environment_id,),
            ).fetchone()[0]
        )
        incident_count = int(
            connection.execute(
                "SELECT COUNT(*) FROM incidents WHERE environment_id = ?",
                (environment_id,),
            ).fetchone()[0]
        )
        diagnosis_count = int(
            connection.execute(
                "SELECT COUNT(*) FROM diagnosis_results d "
                "JOIN incidents i ON i.incident_id = d.incident_id "
                "WHERE i.environment_id = ?",
                (environment_id,),
            ).fetchone()[0]
        )
        fault_family_count = int(
            connection.execute(
                "SELECT COUNT(*) FROM fault_families WHERE environment_id = ?",
                (environment_id,),
            ).fetchone()[0]
        )
        knowledge_artifact_count = sum(
            int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in (
                "fault_families",
                "registration_drafts",
                "shadow_evaluations",
                "promotion_records",
            )
        )
        baseline_job_count = int(
            connection.execute(
                "SELECT COUNT(*) FROM diagnosis_jobs WHERE job_type = 'BASELINE_BUILD'"
            ).fetchone()[0]
        )
        verify_job_count = int(
            connection.execute(
                "SELECT COUNT(*) FROM diagnosis_jobs "
                "WHERE job_type = 'ENVIRONMENT_VERIFY'"
            ).fetchone()[0]
        )
    if (
        environment_count != 1
        or len(baseline_rows) != 1
        or int(baseline_rows[0]["active"]) != 1
        or len(audit_rows) != 1
        or not service_rows
        or capability_row is None
    ):
        raise ValueError("preserved Product Baseline database shape differs")
    baseline_payload = json.loads(baseline_rows[0]["payload_json"])
    baseline_payload["active"] = True
    baseline = EnvironmentBaselineV1.model_validate_json(
        json.dumps(baseline_payload, sort_keys=True, separators=(",", ":"))
    )
    audit = ProductBaselineReadinessAuditV023.model_validate_json(
        audit_rows[0]["payload_json"]
    )
    identity = ServiceIdentityMapV1.build(
        environment_id=environment_id,
        services=tuple(
            ServiceIdentityV1.model_validate_json(row["payload_json"])
            for row in service_rows
        ),
    )
    capability = EnvironmentCapabilityMatrixV1.model_validate_json(
        capability_row["payload_json"]
    )
    counts = {
        "incident_count": incident_count,
        "diagnosis_count": diagnosis_count,
        "fault_family_count": fault_family_count,
        "knowledge_artifact_count": knowledge_artifact_count,
        "baseline_job_count": baseline_job_count,
        "verify_job_count": verify_job_count,
    }
    return baseline, audit, identity, capability, counts


def admit_product_baseline_continuation_context_v0231(
    *,
    predecessor_root: Path,
    binding: ProductV023PrivateStateBindingV0231,
    predecessor: Mapping[str, Any],
) -> ProductBaselineContinuationContextV0231:
    root_input = Path(predecessor_root).expanduser()
    if root_input.is_symlink():
        raise ValueError("predecessor checkout must not be a symlink")
    root = root_input.resolve(strict=True)
    if not root.is_dir():
        raise ValueError("predecessor checkout is not a directory")
    root_fd = _open_root_fd(root)
    product_data_root_fd = -1
    try:
        report_bytes = _bound_file_bytes_from_fd(
            root_fd,
            binding.baseline_private_report_locator,
            binding.baseline_private_report_sha256,
        )
        report = json.loads(report_bytes)
        if not isinstance(report, dict):
            raise ValueError("preserved private Baseline report is not an object")
        attempt = BaselineAttemptV023.model_validate(report.get("attempt"))
        ledger = BaselineAttemptLedgerV023.model_validate(report.get("ledger"))
        product_data_root = root / binding.product_data_root_locator
        product_data_root_fd = _open_bound_path_fd(
            root_fd,
            binding.product_data_root_locator,
            regular_file=False,
        )
        sqlite_before, database_bytes = _sqlite_bundle_snapshot(
            product_data_root_fd,
            binding=binding,
        )
        if (
            len(ledger.attempts) != 1
            or ledger.attempts[0] != attempt
            or Path(attempt.start.product_data_root) != product_data_root
            or attempt.completion.terminal != BASELINE_READINESS_PASS_V023
            or attempt.completion.cleanup != "CLEAN"
            or report.get("product_cleanup") != "CLEAN"
            or report.get("demo_cleanup") != "CLEAN"
            or report.get("fault_attempt_count") != 0
            or report.get("knowledge_campaign_count") != 0
            or report.get("agent_writes") != 0
            or report.get("runbook_executions") != 0
            or report.get("action_authority") != "NONE"
        ):
            raise ValueError("preserved private Baseline completion differs")

        nofault_bytes = _bound_file_bytes_from_fd(
            root_fd,
            binding.nofault_blocker_locator,
            binding.nofault_blocker_sha256,
        )
        nofault = json.loads(nofault_bytes)
        contract = nofault.get("contract") if isinstance(nofault, dict) else None
        if (
            not isinstance(contract, dict)
            or nofault.get("stage") != "PREFLIGHT"
            or nofault.get("incident") is not None
            or nofault.get("diagnosis") is not None
            or nofault.get("result") is not None
            or nofault.get("fault_attempt_count") != 0
            or nofault.get("knowledge_campaign_count") != 0
            or nofault.get("agent_writes") != 0
            or nofault.get("runbook_executions") != 0
            or nofault.get("action_authority") != "NONE"
        ):
            raise ValueError("preserved private No-Fault blocker differs")

        runtime_authority_bytes = _bound_file_bytes_from_fd(
            root_fd,
            binding.runtime_authority_locator,
            binding.runtime_authority_file_sha256,
        )
        runtime_authority = PilotRuntimeAuthorityV02.model_validate_json(
            runtime_authority_bytes
        )
        resolved_compose = json.loads(
            _bound_file_bytes_from_fd(
                root_fd,
                binding.resolved_compose_locator,
                binding.resolved_compose_file_sha256,
            )
        )
        _bound_file_bytes_from_fd(
            root_fd,
            binding.flagd_file_locator,
            binding.flagd_file_sha256,
        )

        environment_id = str(predecessor["environment_id"])
        baseline, audit, identity, capability, counts = _database_bindings(
            database_bytes,
            environment_id=environment_id,
        )
        sqlite_after, _ = _sqlite_bundle_snapshot(
            product_data_root_fd,
            binding=binding,
        )
        if sqlite_before != sqlite_after:
            raise ValueError("preserved Product SQLite bundle changed during admission")
    finally:
        if product_data_root_fd >= 0:
            os.close(product_data_root_fd)
        os.close(root_fd)
    completion_audit = attempt.completion.per_window_audit
    expected_zero_counts = {
        "incident_count": 0,
        "diagnosis_count": 0,
        "fault_family_count": 0,
        "knowledge_artifact_count": 0,
    }
    if (
        completion_audit is None
        or any(
            counts[name] != expected for name, expected in expected_zero_counts.items()
        )
        or counts["baseline_job_count"] != 1
        or counts["verify_job_count"] != 1
        or baseline.baseline_id != predecessor["active_baseline_id"]
        or baseline.baseline_sha256 != predecessor["active_baseline_sha256"]
        or audit.audit_sha256 != predecessor["readiness_audit_sha256"]
        or audit.evaluation.parity_sha256
        != predecessor["window_evaluation_parity_sha256"]
        or audit.active_opensearch_profile_sha256
        != predecessor["active_profile_sha256"]
        or capability.capability_sha256 != audit.capability_sha256
        or runtime_authority.environment_id != environment_id
        or runtime_authority.read_authority.authority_sha256
        != predecessor["preserved_runtime_read_authority_sha256"]
        or runtime_authority.pilot_authority_sha256
        != predecessor["preserved_pilot_runtime_authority_sha256"]
        or runtime_authority.connector_binding_sha256
        != predecessor["preserved_connector_binding_sha256"]
        or semantic_sha256_v22(resolved_compose)
        != predecessor["preserved_resolved_compose_sha256"]
        or contract.get("environment_id") != environment_id
        or contract.get("active_baseline_id") != baseline.baseline_id
        or contract.get("active_baseline_sha256") != baseline.baseline_sha256
        or contract.get("active_profile_sha256") != predecessor["active_profile_sha256"]
        or contract.get("service_identity_sha256") != identity.identity_sha256
        or contract.get("capability_sha256") != capability.capability_sha256
        or contract.get("incident_count") != 0
        or contract.get("diagnosis_count") != 0
        or contract.get("fault_family_count") != 0
        or contract.get("knowledge_artifact_count") != 0
        or contract.get("baseline_job_count") != 1
        or contract.get("verify_job_count") != 1
    ):
        raise ValueError("preserved Product Baseline binding differs")

    source_attempt_sha256 = semantic_sha256_v22(attempt.model_dump(mode="json"))
    locator_sha256 = hashlib.sha256(os.fsencode(str(product_data_root))).hexdigest()
    return ProductBaselineContinuationContextV0231.build(
        predecessor_head=str(predecessor["head"]),
        source_attempt_sha256=source_attempt_sha256,
        source_private_report_sha256=binding.baseline_private_report_sha256,
        product_data_root_locator=binding.product_data_root_locator,
        product_data_root_locator_sha256=locator_sha256,
        environment_id=environment_id,
        active_baseline_id=baseline.baseline_id,
        active_baseline_sha256=baseline.baseline_sha256,
        readiness_audit_sha256=audit.audit_sha256,
        parity_sha256=audit.evaluation.parity_sha256,
        active_profile_sha256=audit.active_opensearch_profile_sha256,
        service_identity_sha256=identity.identity_sha256,
        capability_sha256=capability.capability_sha256,
        runtime_authority_path=binding.runtime_authority_locator,
        runtime_authority_sha256=runtime_authority.pilot_authority_sha256,
    )


__all__ = (
    "AuthorityContinuousSandboxLifecycleV0231",
    "ContinuityPreflightReportV0231",
    "FlagdBindDescriptorV0231",
    "ProductBaselineContinuationContextV0231",
    "ProductV023PrivateStateBindingV0231",
    "RuntimeAuthorityContinuityDescriptorV0231",
    "SquashMergeBoundFileV0231",
    "SquashMergeHistoryBindingV0231",
    "admit_flagd_bind_descriptor_v0231",
    "admit_product_baseline_continuation_context_v0231",
    "build_runtime_authority_continuity_descriptor_v0231",
    "load_preserved_runtime_inputs_v0231",
)
