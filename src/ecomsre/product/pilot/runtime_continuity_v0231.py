"""Contracts for Product v0.2.3.1 Runtime-authority continuity."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import sqlite3
import stat
from typing import Any, Literal, Mapping

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
    "ProductBaselineContinuationContextV0231",
    "ProductV023PrivateStateBindingV0231",
    "SquashMergeBoundFileV0231",
    "SquashMergeHistoryBindingV0231",
    "admit_product_baseline_continuation_context_v0231",
)
