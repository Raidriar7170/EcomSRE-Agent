"""Fresh Product-state selection and clone-only migration for Product v0.2.3.3."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import json
from pathlib import Path
import re
import shutil
import sqlite3
import tempfile
from typing import Callable, Literal

from pydantic import ConfigDict, Field, field_validator, model_validator

from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22
from ecomsre.product.contracts import ProductModelV1, ServiceIdentityPolicyV1
from ecomsre.product.environment.capabilities import EnvironmentCapabilityMatrixV1
from ecomsre.product.pilot.product_state_clone_v0232 import (
    ProductStateCloneErrorV0232,
    _active_bindings,
    _copy_file,
    _logical_database_sha256,
    _object_inventory,
    _online_backup,
    _read_only_connection,
    _require_destination_ancestors,
    _require_no_sqlite_sidecars,
    _require_regular_tree,
    _require_relative_locator,
    _runtime_file_inventory,
    _sha256_file,
)
from ecomsre.product.storage.sqlite_store import SqliteStoreV1


HISTORY_AND_HANDOFF_PASS_V0233: Literal[
    "ECOMSRE_PRODUCT_V0233_HISTORY_AND_HANDOFF_PASS"
] = "ECOMSRE_PRODUCT_V0233_HISTORY_AND_HANDOFF_PASS"
SOURCE_AND_CLONE_CONTRACT_PASS_V0233: Literal[
    "ECOMSRE_PRODUCT_V0233_SOURCE_AND_CLONE_CONTRACT_PASS"
] = "ECOMSRE_PRODUCT_V0233_SOURCE_AND_CLONE_CONTRACT_PASS"
PRIVATE_PRODUCT_STATE_BLOCKER_V0233 = (
    "BLOCKED_ECOMSRE_PRODUCT_V0233_PRIVATE_PRODUCT_STATE"
)
STATE_CLONE_BLOCKER_V0233 = "BLOCKED_ECOMSRE_PRODUCT_V0233_STATE_CLONE"

_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_PILOT_FILES = ("runtime-authority.json", "runtime-readiness.json")
_EXPECTED_ENVIRONMENT_ID = "env-2b5c86f47f449acfc54cfcec"
_EXPECTED_BASELINE_ID = "base-b25440a36089a8f0e6b9f1dc"
_EXPECTED_BASELINE_SHA256 = (
    "6d3d2d7a4854d1cfc2477746e7d0c940ed8a08644ebc69b7b91066eabe45ae64"
)
_EXPECTED_PROFILE_SHA256 = (
    "b9577dfc4eaa933b62048bbcbd041ed470343f7c76255ab851cdcaeef60a7df2"
)


class FreshFormalSourceSelectionErrorV0233(RuntimeError):
    """Neither frozen Product-state source can be admitted exactly."""


class FreshFormalStateCloneErrorV0233(RuntimeError):
    """The admitted source cannot be cloned and migrated exactly once."""


class FreshFormalSourceKindV0233(str, Enum):
    PRISTINE_PREFORMAL_BASE = "PRISTINE_PREFORMAL_BASE"
    SEALED_SCHEMA8_RECONSTRUCTION = "SEALED_SCHEMA8_RECONSTRUCTION"


class FreshFormalStateCountsV0233(ProductModelV1):
    model_config = ConfigDict(extra="forbid", frozen=True)

    baseline_count: int = Field(ge=0)
    active_baseline_count: int = Field(ge=0)
    baseline_job_count: int = Field(ge=0)
    verify_job_count: int = Field(ge=0)
    diagnosis_job_count: int = Field(ge=0)
    incident_count: int = Field(ge=0)
    diagnosis_count: int = Field(ge=0)
    evidence_object_count: int = Field(ge=0)
    diagnosis_evidence_index_count: int = Field(ge=0)
    diagnosis_stage_event_count: int = Field(ge=0)
    fault_family_count: int = Field(ge=0)
    knowledge_artifact_count: int = Field(ge=0)
    pending_job_count: int = Field(ge=0)
    running_job_count: int = Field(ge=0)
    failed_job_count: int = Field(ge=0)

    @model_validator(mode="after")
    def require_admissible_source_counts(self) -> FreshFormalStateCountsV0233:
        exact = {
            "baseline_count": 1,
            "active_baseline_count": 1,
            "baseline_job_count": 1,
            "verify_job_count": 1,
            "fault_family_count": 0,
            "knowledge_artifact_count": 0,
            "pending_job_count": 0,
            "running_job_count": 0,
        }
        if any(getattr(self, field) != expected for field, expected in exact.items()):
            raise ValueError("Product v0.2.3.3 source counts differ")
        if (
            self.diagnosis_job_count < 1
            or self.incident_count < 1
            or self.diagnosis_count < 1
            or self.evidence_object_count < 1
            or self.diagnosis_count > self.incident_count
            or self.diagnosis_evidence_index_count > self.diagnosis_count
        ):
            raise ValueError("Product v0.2.3.3 source counts differ")
        return self


class FreshFormalSourceCandidateV0233(ProductModelV1):
    """Private runtime locator plus its public frozen admission binding."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    source_kind: FreshFormalSourceKindV0233
    source_root: Path
    source_locator: str
    source_schema_version: int = Field(ge=1)
    source_database_file_sha256: str = Field(pattern=_SHA256_PATTERN)
    source_database_logical_sha256: str = Field(pattern=_SHA256_PATTERN)
    source_product_state_sha256: str = Field(pattern=_SHA256_PATTERN)
    source_semantic_context: dict[str, str] | None = None
    source_object_inventory_sha256: str = Field(pattern=_SHA256_PATTERN)
    source_runtime_inventory_sha256: str = Field(pattern=_SHA256_PATTERN)
    active_environment_id: str
    active_baseline_id: str
    active_baseline_sha256: str = Field(pattern=_SHA256_PATTERN)
    active_profile_sha256: str = Field(pattern=_SHA256_PATTERN)

    @field_validator("source_locator")
    @classmethod
    def locator_is_relative(cls, value: str) -> str:
        try:
            return _require_relative_locator(value)
        except ProductStateCloneErrorV0232 as error:
            raise ValueError("Product v0.2.3.3 source locator differs") from error

    @model_validator(mode="after")
    def semantic_context_matches_kind(self) -> FreshFormalSourceCandidateV0233:
        expected_context_keys = {
            "schema8_definition_sha256",
            "formal_delta_sha256",
            "source_projection_sha256",
        }
        if self.source_kind is FreshFormalSourceKindV0233.PRISTINE_PREFORMAL_BASE:
            if self.source_semantic_context is not None:
                raise ValueError("Product v0.2.3.3 pristine semantic context differs")
        elif (
            self.source_semantic_context is None
            or set(self.source_semantic_context) != expected_context_keys
            or any(
                re.fullmatch(_SHA256_PATTERN, value) is None
                for value in self.source_semantic_context.values()
            )
        ):
            raise ValueError("Product v0.2.3.3 reconstruction semantic context differs")
        return self


class FreshFormalSourceSelectionV0233(ProductModelV1):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["ecomsre.product.fresh-formal-source-selection.v0233"] = (
        "ecomsre.product.fresh-formal-source-selection.v0233"
    )
    terminal: Literal["ECOMSRE_PRODUCT_V0233_SOURCE_AND_CLONE_CONTRACT_PASS"] = (
        SOURCE_AND_CLONE_CONTRACT_PASS_V0233
    )
    source_kind: FreshFormalSourceKindV0233
    source_locator: str
    source_schema_version: Literal[7, 8]
    source_database_file_sha256: str = Field(pattern=_SHA256_PATTERN)
    source_database_logical_sha256: str = Field(pattern=_SHA256_PATTERN)
    source_product_state_sha256: str = Field(pattern=_SHA256_PATTERN)
    source_object_inventory_sha256: str = Field(pattern=_SHA256_PATTERN)
    source_runtime_inventory_sha256: str = Field(pattern=_SHA256_PATTERN)
    source_counts: FreshFormalStateCountsV0233
    active_environment_id: str
    active_baseline_id: str
    active_baseline_sha256: str = Field(pattern=_SHA256_PATTERN)
    active_profile_sha256: str = Field(pattern=_SHA256_PATTERN)
    selection_reason: Literal[
        "PREFERRED_SOURCE_ADMITTED",
        "PREFERRED_REJECTED_FALLBACK_ADMITTED",
    ]
    selection_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def require_kind_schema_and_seal(self) -> FreshFormalSourceSelectionV0233:
        expected_schema = {
            FreshFormalSourceKindV0233.PRISTINE_PREFORMAL_BASE: 7,
            FreshFormalSourceKindV0233.SEALED_SCHEMA8_RECONSTRUCTION: 8,
        }[self.source_kind]
        if self.source_schema_version != expected_schema:
            raise ValueError("Product v0.2.3.3 source schema differs")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"selection_sha256"})
        )
        if self.selection_sha256 != expected:
            raise ValueError("Product v0.2.3.3 source selection digest differs")
        return self


class FreshFormalStateCloneV0233(ProductModelV1):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["ecomsre.product.fresh-formal-state-clone.v0233"] = (
        "ecomsre.product.fresh-formal-state-clone.v0233"
    )
    terminal: Literal["ECOMSRE_PRODUCT_V0233_SOURCE_AND_CLONE_CONTRACT_PASS"] = (
        SOURCE_AND_CLONE_CONTRACT_PASS_V0233
    )
    source_selection_sha256: str = Field(pattern=_SHA256_PATTERN)
    destination_locator: str
    pre_migration_schema_version: Literal[7, 8]
    post_migration_schema_version: Literal[9]
    source_counts: FreshFormalStateCountsV0233
    starting_counts: FreshFormalStateCountsV0233
    source_database_logical_sha256: str = Field(pattern=_SHA256_PATTERN)
    clone_database_logical_sha256_before_migration: str = Field(pattern=_SHA256_PATTERN)
    clone_database_logical_sha256_after_migration: str = Field(pattern=_SHA256_PATTERN)
    object_inventory_sha256: str = Field(pattern=_SHA256_PATTERN)
    runtime_inventory_sha256: str = Field(pattern=_SHA256_PATTERN)
    active_environment_id: str
    active_baseline_id: str
    active_baseline_sha256: str = Field(pattern=_SHA256_PATTERN)
    active_profile_sha256: str = Field(pattern=_SHA256_PATTERN)
    clone_sha256: str = Field(pattern=_SHA256_PATTERN)

    @field_validator("destination_locator")
    @classmethod
    def locator_is_relative(cls, value: str) -> str:
        try:
            return _require_relative_locator(value)
        except ProductStateCloneErrorV0232 as error:
            raise ValueError("Product v0.2.3.3 clone locator differs") from error

    @model_validator(mode="after")
    def require_clone_only_migration_and_seal(self) -> FreshFormalStateCloneV0233:
        if (
            self.source_counts != self.starting_counts
            or self.source_database_logical_sha256
            != self.clone_database_logical_sha256_before_migration
        ):
            raise ValueError("Product v0.2.3.3 clone business state differs")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"clone_sha256"})
        )
        if self.clone_sha256 != expected:
            raise ValueError("Product v0.2.3.3 clone digest differs")
        return self


@dataclass(frozen=True)
class _FreshFormalInspectionV0233:
    schema_version: int
    database_file_sha256: str
    database_logical_sha256: str
    object_inventory_sha256: str
    runtime_inventory_sha256: str
    counts: FreshFormalStateCountsV0233
    environment_id: str
    active_baseline_id: str
    active_baseline_sha256: str
    active_profile_sha256: str


def _table_exists(connection: sqlite3.Connection, table: str) -> bool:
    return (
        connection.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table' AND name = ?",
            (table,),
        ).fetchone()[0]
        == 1
    )


def _count(connection: sqlite3.Connection, table: str) -> int:
    if not _table_exists(connection, table):
        return 0
    quoted = '"' + table.replace('"', '""') + '"'
    return int(connection.execute(f"SELECT COUNT(*) FROM {quoted}").fetchone()[0])


def _raw_state_counts(connection: sqlite3.Connection) -> dict[str, int]:
    jobs = {
        (str(row[0]), str(row[1])): int(row[2])
        for row in connection.execute(
            "SELECT job_type, status, COUNT(*) FROM diagnosis_jobs "
            "GROUP BY job_type, status"
        ).fetchall()
    }
    by_status = {
        str(row[0]): int(row[1])
        for row in connection.execute(
            "SELECT status, COUNT(*) FROM diagnosis_jobs GROUP BY status"
        ).fetchall()
    }
    knowledge_tables = (
        "predicate_matrices",
        "human_reviews",
        "registration_drafts",
        "shadow_evaluations",
        "environment_extension_registrations",
        "environment_extension_registry_versions",
        "promotion_records",
        "revocation_records",
    )
    return {
        "baseline_count": _count(connection, "baseline_versions"),
        "active_baseline_count": int(
            connection.execute(
                "SELECT COUNT(*) FROM baseline_versions WHERE active = 1"
            ).fetchone()[0]
        ),
        "baseline_job_count": sum(
            count for (kind, _status), count in jobs.items() if kind == "BASELINE_BUILD"
        ),
        "verify_job_count": sum(
            count
            for (kind, _status), count in jobs.items()
            if kind == "ENVIRONMENT_VERIFY"
        ),
        "diagnosis_job_count": sum(
            count for (kind, _status), count in jobs.items() if kind == "DIAGNOSIS"
        ),
        "incident_count": _count(connection, "incidents"),
        "diagnosis_count": _count(connection, "diagnosis_results"),
        "evidence_object_count": _count(connection, "evidence_objects"),
        "diagnosis_evidence_index_count": _count(
            connection, "diagnosis_evidence_indexes"
        ),
        "diagnosis_stage_event_count": _count(
            connection, "diagnosis_stage_events_v02322"
        ),
        "fault_family_count": _count(connection, "fault_families"),
        "knowledge_artifact_count": sum(
            _count(connection, table) for table in knowledge_tables
        ),
        "pending_job_count": by_status.get("PENDING", 0),
        "running_job_count": by_status.get("RUNNING", 0),
        "failed_job_count": by_status.get("FAILED", 0),
    }


def _state_counts(connection: sqlite3.Connection) -> FreshFormalStateCountsV0233:
    try:
        return FreshFormalStateCountsV0233.model_validate(_raw_state_counts(connection))
    except ValueError as error:
        raise FreshFormalSourceSelectionErrorV0233(
            "Product v0.2.3.3 source counts differ"
        ) from error


def read_fresh_formal_state_counts_v0233(
    product_root: Path,
) -> FreshFormalStateCountsV0233:
    """Read current formal-clone cardinalities without applying migrations."""

    root = Path(product_root).expanduser().resolve(strict=True)
    try:
        with _read_only_connection(root / "product.sqlite3") as connection:
            return _state_counts(connection)
    except (OSError, sqlite3.Error, ValueError) as error:
        raise FreshFormalStateCloneErrorV0233(STATE_CLONE_BLOCKER_V0233) from error


def read_raw_formal_state_counts_v0233(product_root: Path) -> dict[str, int]:
    """Read exact poststate counts without applying source-admission invariants."""

    root = Path(product_root).expanduser().resolve(strict=True)
    try:
        with _read_only_connection(root / "product.sqlite3") as connection:
            return _raw_state_counts(connection)
    except (OSError, sqlite3.Error, ValueError) as error:
        raise FreshFormalStateCloneErrorV0233(STATE_CLONE_BLOCKER_V0233) from error


def read_formal_active_binding_v0233(product_root: Path) -> dict[str, str]:
    """Read the clone's active environment/Baseline/Profile without mutation."""

    root = Path(product_root).expanduser().resolve(strict=True)
    try:
        with _read_only_connection(root / "product.sqlite3") as connection:
            environment_id, baseline_id, baseline_sha256, profile_sha256 = (
                _active_bindings(connection)
            )
        return {
            "environment_id": environment_id,
            "baseline_id": baseline_id,
            "baseline_sha256": baseline_sha256,
            "profile_sha256": profile_sha256,
        }
    except (OSError, sqlite3.Error, ValueError) as error:
        raise FreshFormalStateCloneErrorV0233(STATE_CLONE_BLOCKER_V0233) from error


def read_formal_diagnosis_action_totals_v0233(
    product_root: Path,
) -> dict[str, int | bool]:
    """Read persisted Diagnosis action counters through a read-only connection."""

    root = Path(product_root).expanduser().resolve(strict=True)
    totals: dict[str, int | bool] = {
        "provider_calls": 0,
        "agent_writes": 0,
        "runbook_executions": 0,
        "action_authority_none": True,
    }
    try:
        with _read_only_connection(root / "product.sqlite3") as connection:
            rows = connection.execute(
                "SELECT payload_json FROM diagnosis_results ORDER BY diagnosis_id"
            ).fetchall()
        for row in rows:
            payload = json.loads(str(row[0]))
            if not isinstance(payload, dict):
                raise ValueError("Diagnosis result payload differs")
            for name in ("provider_calls", "agent_writes", "runbook_executions"):
                value = payload.get(name)
                if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                    raise ValueError("Diagnosis action counter differs")
                totals[name] = int(totals[name]) + value
            totals["action_authority_none"] = (
                bool(totals["action_authority_none"])
                and payload.get("action_authority") == "NONE"
            )
        return totals
    except (OSError, sqlite3.Error, json.JSONDecodeError, ValueError) as error:
        raise FreshFormalStateCloneErrorV0233(STATE_CLONE_BLOCKER_V0233) from error


def _validate_identity_and_capability(
    connection: sqlite3.Connection,
    *,
    environment_id: str,
) -> None:
    environments = connection.execute(
        "SELECT service_identity_policy_json FROM environments WHERE environment_id = ?",
        (environment_id,),
    ).fetchall()
    capabilities = connection.execute(
        "SELECT payload_json FROM environment_capability_matrices "
        "WHERE environment_id = ?",
        (environment_id,),
    ).fetchall()
    if len(environments) != 1 or len(capabilities) != 1:
        raise FreshFormalSourceSelectionErrorV0233(
            "Product v0.2.3.3 service identity/capability matrix differs"
        )
    try:
        identity = ServiceIdentityPolicyV1.model_validate_json(environments[0][0])
        capability = EnvironmentCapabilityMatrixV1.model_validate_json(
            capabilities[0][0]
        )
    except ValueError as error:
        raise FreshFormalSourceSelectionErrorV0233(
            "Product v0.2.3.3 service identity/capability matrix differs"
        ) from error
    identity_services = {item.logical_service for item in identity.services}
    service_row_count = int(
        connection.execute(
            "SELECT COUNT(*) FROM services WHERE environment_id = ? "
            "AND logical_service = 'checkout'",
            (environment_id,),
        ).fetchone()[0]
    )
    if (
        "checkout" not in identity_services
        or capability.environment_id != environment_id
        or "checkout" not in capability.logical_services
        or service_row_count != 1
    ):
        raise FreshFormalSourceSelectionErrorV0233(
            "Product v0.2.3.3 service identity/capability matrix differs"
        )


def _inspect_source(
    root: Path,
    *,
    owner_counter: Callable[[Path], int],
) -> _FreshFormalInspectionV0233:
    try:
        _require_regular_tree(root)
        _require_no_sqlite_sidecars(root)
    except ProductStateCloneErrorV0232 as error:
        raise FreshFormalSourceSelectionErrorV0233(str(error)) from error
    database = root / "product.sqlite3"
    try:
        owner_count = owner_counter(database)
    except Exception as error:
        raise FreshFormalSourceSelectionErrorV0233(
            "Product v0.2.3.3 source owner check failed"
        ) from error
    if owner_count != 0:
        raise FreshFormalSourceSelectionErrorV0233(
            "Product v0.2.3.3 source owner count differs"
        )
    try:
        with _read_only_connection(database) as connection:
            schema_version = int(
                connection.execute(
                    "SELECT MAX(version) FROM schema_migrations"
                ).fetchone()[0]
            )
            logical_sha256 = _logical_database_sha256(connection)
            counts = _state_counts(connection)
            environment_id, baseline_id, baseline_sha256, profile_sha256 = (
                _active_bindings(connection)
            )
            _validate_identity_and_capability(connection, environment_id=environment_id)
            object_inventory_sha256 = _object_inventory(root, connection)
        runtime_inventory_sha256 = _runtime_file_inventory(
            root,
            expected_environment_id=environment_id,
            expected_pilot_runtime_authority_sha256=None,
            expected_runtime_connector_binding_sha256=None,
        )
    except FreshFormalSourceSelectionErrorV0233:
        raise
    except (ProductStateCloneErrorV0232, sqlite3.Error, json.JSONDecodeError) as error:
        raise FreshFormalSourceSelectionErrorV0233(
            "Product v0.2.3.3 source admission failed"
        ) from error
    if owner_counter(database) != 0:
        raise FreshFormalSourceSelectionErrorV0233(
            "Product v0.2.3.3 source owner count changed"
        )
    return _FreshFormalInspectionV0233(
        schema_version=schema_version,
        database_file_sha256=_sha256_file(database),
        database_logical_sha256=logical_sha256,
        object_inventory_sha256=object_inventory_sha256,
        runtime_inventory_sha256=runtime_inventory_sha256,
        counts=counts,
        environment_id=environment_id,
        active_baseline_id=baseline_id,
        active_baseline_sha256=baseline_sha256,
        active_profile_sha256=profile_sha256,
    )


def _selection_from_inspection(
    candidate: FreshFormalSourceCandidateV0233,
    inspection: _FreshFormalInspectionV0233,
    *,
    selection_reason: Literal[
        "PREFERRED_SOURCE_ADMITTED",
        "PREFERRED_REJECTED_FALLBACK_ADMITTED",
    ],
) -> FreshFormalSourceSelectionV0233:
    expected_schema = {
        FreshFormalSourceKindV0233.PRISTINE_PREFORMAL_BASE: 7,
        FreshFormalSourceKindV0233.SEALED_SCHEMA8_RECONSTRUCTION: 8,
    }[candidate.source_kind]
    expected = (
        candidate.source_schema_version,
        candidate.source_database_file_sha256,
        candidate.source_database_logical_sha256,
        candidate.source_object_inventory_sha256,
        candidate.source_runtime_inventory_sha256,
        candidate.active_environment_id,
        candidate.active_baseline_id,
        candidate.active_baseline_sha256,
        candidate.active_profile_sha256,
    )
    actual = (
        inspection.schema_version,
        inspection.database_file_sha256,
        inspection.database_logical_sha256,
        inspection.object_inventory_sha256,
        inspection.runtime_inventory_sha256,
        inspection.environment_id,
        inspection.active_baseline_id,
        inspection.active_baseline_sha256,
        inspection.active_profile_sha256,
    )
    if candidate.source_schema_version != expected_schema or actual != expected:
        raise FreshFormalSourceSelectionErrorV0233(
            "Product v0.2.3.3 source schema/hash/binding differs"
        )
    if candidate.source_kind is FreshFormalSourceKindV0233.PRISTINE_PREFORMAL_BASE:
        source_counts_v0232 = {
            key: value
            for key, value in inspection.counts.model_dump(mode="json").items()
            if key
            not in {
                "diagnosis_evidence_index_count",
                "diagnosis_stage_event_count",
            }
        }
        semantic_body: dict[str, object] = {
            "schema_version": "ecomsre.product.product-state-source.v0232",
            "source_locator": candidate.source_locator,
            "source_database_file_sha256": inspection.database_file_sha256,
            "source_database_logical_sha256": inspection.database_logical_sha256,
            "source_object_inventory_sha256": inspection.object_inventory_sha256,
            "source_runtime_file_inventory_sha256": inspection.runtime_inventory_sha256,
            "source_counts": source_counts_v0232,
            "source_environment_id": inspection.environment_id,
            "source_active_baseline_id": inspection.active_baseline_id,
            "source_active_baseline_sha256": inspection.active_baseline_sha256,
            "source_profile_sha256": inspection.active_profile_sha256,
        }
    else:
        context = candidate.source_semantic_context
        if context is None:
            raise FreshFormalSourceSelectionErrorV0233(
                "Product v0.2.3.3 reconstruction semantic context differs"
            )
        semantic_body = {
            "schema8_definition_sha256": context["schema8_definition_sha256"],
            "formal_delta_sha256": context["formal_delta_sha256"],
            "database_logical_sha256": inspection.database_logical_sha256,
            "object_inventory_sha256": inspection.object_inventory_sha256,
            "runtime_file_inventory_sha256": inspection.runtime_inventory_sha256,
            "source_projection_sha256": context["source_projection_sha256"],
        }
    if candidate.source_product_state_sha256 != semantic_sha256_v22(semantic_body):
        raise FreshFormalSourceSelectionErrorV0233(
            "Product v0.2.3.3 source semantic state differs"
        )
    body: dict[str, object] = {
        "schema_version": "ecomsre.product.fresh-formal-source-selection.v0233",
        "terminal": SOURCE_AND_CLONE_CONTRACT_PASS_V0233,
        "source_kind": candidate.source_kind.value,
        "source_locator": candidate.source_locator,
        "source_schema_version": inspection.schema_version,
        "source_database_file_sha256": inspection.database_file_sha256,
        "source_database_logical_sha256": inspection.database_logical_sha256,
        "source_product_state_sha256": candidate.source_product_state_sha256,
        "source_object_inventory_sha256": inspection.object_inventory_sha256,
        "source_runtime_inventory_sha256": inspection.runtime_inventory_sha256,
        "source_counts": inspection.counts.model_dump(mode="json"),
        "active_environment_id": inspection.environment_id,
        "active_baseline_id": inspection.active_baseline_id,
        "active_baseline_sha256": inspection.active_baseline_sha256,
        "active_profile_sha256": inspection.active_profile_sha256,
        "selection_reason": selection_reason,
    }
    return FreshFormalSourceSelectionV0233.model_validate(
        {**body, "selection_sha256": semantic_sha256_v22(body)}
    )


def admit_fresh_formal_source_v0233(
    candidate: FreshFormalSourceCandidateV0233,
    *,
    owner_counter: Callable[[Path], int],
    selection_reason: Literal[
        "PREFERRED_SOURCE_ADMITTED",
        "PREFERRED_REJECTED_FALLBACK_ADMITTED",
    ] = "PREFERRED_SOURCE_ADMITTED",
) -> FreshFormalSourceSelectionV0233:
    root = candidate.source_root.expanduser()
    if not root.is_absolute():
        root = root.absolute()
    inspection = _inspect_source(root, owner_counter=owner_counter)
    return _selection_from_inspection(
        candidate, inspection, selection_reason=selection_reason
    )


def select_fresh_formal_source_v0233(
    *,
    preferred: FreshFormalSourceCandidateV0233,
    fallback: FreshFormalSourceCandidateV0233,
    owner_counter: Callable[[Path], int],
) -> FreshFormalSourceSelectionV0233:
    if (
        preferred.source_kind is not FreshFormalSourceKindV0233.PRISTINE_PREFORMAL_BASE
        or fallback.source_kind
        is not FreshFormalSourceKindV0233.SEALED_SCHEMA8_RECONSTRUCTION
    ):
        raise FreshFormalSourceSelectionErrorV0233(
            "Product v0.2.3.3 source priority differs"
        )
    try:
        return admit_fresh_formal_source_v0233(
            preferred,
            owner_counter=owner_counter,
            selection_reason="PREFERRED_SOURCE_ADMITTED",
        )
    except FreshFormalSourceSelectionErrorV0233:
        try:
            return admit_fresh_formal_source_v0233(
                fallback,
                owner_counter=owner_counter,
                selection_reason="PREFERRED_REJECTED_FALLBACK_ADMITTED",
            )
        except FreshFormalSourceSelectionErrorV0233 as error:
            raise FreshFormalSourceSelectionErrorV0233(
                PRIVATE_PRODUCT_STATE_BLOCKER_V0233
            ) from error


def configured_source_candidates_v0233(
    *,
    preferred_root: Path,
    fallback_root: Path,
) -> tuple[FreshFormalSourceCandidateV0233, FreshFormalSourceCandidateV0233]:
    return (
        FreshFormalSourceCandidateV0233(
            source_kind=FreshFormalSourceKindV0233.PRISTINE_PREFORMAL_BASE,
            source_root=preferred_root,
            source_locator=(
                ".local/product-v023/baseline-readiness/runs/"
                "20260829T150806-1eaee825/product"
            ),
            source_schema_version=7,
            source_database_file_sha256=(
                "2b79610d0c3a03957c8df8817d56c1531007f713b1683a8384c0cfd4fe7baf49"
            ),
            source_database_logical_sha256=(
                "65a5c739b54c10cf12b973a1c9b0a5afca57eefc8f21d05877ad3185389ebce1"
            ),
            source_product_state_sha256=(
                "0860c3cefe795378b36293342fa7250bab97bb75e8767d3b5a8c200c3e05741c"
            ),
            source_object_inventory_sha256=(
                "93708f4e238e3bd3c9d662011ee098285eecf1112e0ab15a66b72fdcc254bf32"
            ),
            source_runtime_inventory_sha256=(
                "21714573aee49676ef9a504d29b51b043b4c80289443d1bf88227eef690a4356"
            ),
            active_environment_id=_EXPECTED_ENVIRONMENT_ID,
            active_baseline_id=_EXPECTED_BASELINE_ID,
            active_baseline_sha256=_EXPECTED_BASELINE_SHA256,
            active_profile_sha256=_EXPECTED_PROFILE_SHA256,
        ),
        FreshFormalSourceCandidateV0233(
            source_kind=FreshFormalSourceKindV0233.SEALED_SCHEMA8_RECONSTRUCTION,
            source_root=fallback_root,
            source_locator=(
                ".local/product-v02323/reconstruction/20260831T051548Z/product"
            ),
            source_schema_version=8,
            source_database_file_sha256=(
                "59d8c25d3a53fc62af6e2f333eb3ac9d9bb7ef1f63d2d365cb4cd127eaf0bd7d"
            ),
            source_database_logical_sha256=(
                "fa8c0e75c51823bb9220878d9b2f73445c544c66a24213db23d25f408b4a1891"
            ),
            source_product_state_sha256=(
                "dbb9b21a7e476d0cf2b31eb3b7486d75a953ad62d5963794cda4540126364056"
            ),
            source_semantic_context={
                "schema8_definition_sha256": (
                    "339e63806704bd5c3d01a2923f281d7ce0ed130d6c9e2d1393ad2a4022028ea5"
                ),
                "formal_delta_sha256": (
                    "ba945a1ec0b944085507781efb8db4a21626298842e967b4095e8c37a953ca3f"
                ),
                "source_projection_sha256": (
                    "1c0470913cf45bcf40318110a97c8da521bfb02edd4088254f23a44a5b8aff79"
                ),
            },
            source_object_inventory_sha256=(
                "93708f4e238e3bd3c9d662011ee098285eecf1112e0ab15a66b72fdcc254bf32"
            ),
            source_runtime_inventory_sha256=(
                "578c8a6e923b3550e94a8b6b3351ddba212b2b98dbf34b805b8d98fcbfd16d3a"
            ),
            active_environment_id=_EXPECTED_ENVIRONMENT_ID,
            active_baseline_id=_EXPECTED_BASELINE_ID,
            active_baseline_sha256=_EXPECTED_BASELINE_SHA256,
            active_profile_sha256=_EXPECTED_PROFILE_SHA256,
        ),
    )


def _checkpoint_clone_database(database: Path) -> None:
    connection = sqlite3.connect(database, isolation_level=None)
    try:
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        connection.execute("PRAGMA journal_mode = DELETE")
    finally:
        connection.close()
    for suffix in ("-wal", "-shm"):
        sidecar = Path(f"{database}{suffix}")
        if sidecar.exists():
            if suffix == "-wal" and sidecar.stat().st_size != 0:
                raise FreshFormalStateCloneErrorV0233(STATE_CLONE_BLOCKER_V0233)
            sidecar.unlink()


def _clone_result_from_inspection_v0233(
    *,
    selection: FreshFormalSourceSelectionV0233,
    inspection: _FreshFormalInspectionV0233,
    destination_locator: str,
) -> FreshFormalStateCloneV0233:
    if (
        inspection.schema_version != 9
        or inspection.counts != selection.source_counts
        or inspection.object_inventory_sha256
        != selection.source_object_inventory_sha256
        or inspection.runtime_inventory_sha256
        != selection.source_runtime_inventory_sha256
        or inspection.environment_id != selection.active_environment_id
        or inspection.active_baseline_id != selection.active_baseline_id
        or inspection.active_baseline_sha256 != selection.active_baseline_sha256
        or inspection.active_profile_sha256 != selection.active_profile_sha256
        or inspection.counts.diagnosis_stage_event_count != 0
    ):
        raise FreshFormalStateCloneErrorV0233(STATE_CLONE_BLOCKER_V0233)
    body: dict[str, object] = {
        "schema_version": "ecomsre.product.fresh-formal-state-clone.v0233",
        "terminal": SOURCE_AND_CLONE_CONTRACT_PASS_V0233,
        "source_selection_sha256": selection.selection_sha256,
        "destination_locator": destination_locator,
        "pre_migration_schema_version": selection.source_schema_version,
        "post_migration_schema_version": inspection.schema_version,
        "source_counts": selection.source_counts.model_dump(mode="json"),
        "starting_counts": inspection.counts.model_dump(mode="json"),
        "source_database_logical_sha256": selection.source_database_logical_sha256,
        "clone_database_logical_sha256_before_migration": (
            selection.source_database_logical_sha256
        ),
        "clone_database_logical_sha256_after_migration": (
            inspection.database_logical_sha256
        ),
        "object_inventory_sha256": inspection.object_inventory_sha256,
        "runtime_inventory_sha256": inspection.runtime_inventory_sha256,
        "active_environment_id": inspection.environment_id,
        "active_baseline_id": inspection.active_baseline_id,
        "active_baseline_sha256": inspection.active_baseline_sha256,
        "active_profile_sha256": inspection.active_profile_sha256,
    }
    return FreshFormalStateCloneV0233.model_validate(
        {**body, "clone_sha256": semantic_sha256_v22(body)}
    )


def recover_fresh_formal_state_clone_v0233(
    *,
    selection: FreshFormalSourceSelectionV0233,
    destination_root: Path,
    destination_locator: str,
) -> FreshFormalStateCloneV0233:
    """Recompute the sealed clone proof after a hard interruption."""

    try:
        locator = _require_relative_locator(destination_locator)
        inspection = _inspect_source(
            Path(destination_root).resolve(strict=True),
            owner_counter=lambda _database: 0,
        )
        return _clone_result_from_inspection_v0233(
            selection=selection,
            inspection=inspection,
            destination_locator=locator,
        )
    except (
        OSError,
        ProductStateCloneErrorV0232,
        FreshFormalSourceSelectionErrorV0233,
    ) as error:
        raise FreshFormalStateCloneErrorV0233(STATE_CLONE_BLOCKER_V0233) from error


def clone_fresh_formal_state_v0233(
    *,
    selection: FreshFormalSourceSelectionV0233,
    source_root: Path,
    destination_root: Path,
    destination_locator: str,
    owner_counter: Callable[[Path], int],
) -> FreshFormalStateCloneV0233:
    try:
        destination_locator = _require_relative_locator(destination_locator)
    except ProductStateCloneErrorV0232 as error:
        raise FreshFormalStateCloneErrorV0233(STATE_CLONE_BLOCKER_V0233) from error
    source = Path(source_root).expanduser()
    if not source.is_absolute():
        source = source.absolute()
    candidate = FreshFormalSourceCandidateV0233(
        source_kind=selection.source_kind,
        source_root=source,
        source_locator=selection.source_locator,
        source_schema_version=selection.source_schema_version,
        source_database_file_sha256=selection.source_database_file_sha256,
        source_database_logical_sha256=selection.source_database_logical_sha256,
        source_product_state_sha256=selection.source_product_state_sha256,
        source_semantic_context=(
            {
                "schema8_definition_sha256": (
                    "339e63806704bd5c3d01a2923f281d7ce0ed130d6c9e2d1393ad2a4022028ea5"
                ),
                "formal_delta_sha256": (
                    "ba945a1ec0b944085507781efb8db4a21626298842e967b4095e8c37a953ca3f"
                ),
                "source_projection_sha256": (
                    "1c0470913cf45bcf40318110a97c8da521bfb02edd4088254f23a44a5b8aff79"
                ),
            }
            if selection.source_kind
            is FreshFormalSourceKindV0233.SEALED_SCHEMA8_RECONSTRUCTION
            else None
        ),
        source_object_inventory_sha256=selection.source_object_inventory_sha256,
        source_runtime_inventory_sha256=selection.source_runtime_inventory_sha256,
        active_environment_id=selection.active_environment_id,
        active_baseline_id=selection.active_baseline_id,
        active_baseline_sha256=selection.active_baseline_sha256,
        active_profile_sha256=selection.active_profile_sha256,
    )
    source_before = admit_fresh_formal_source_v0233(
        candidate,
        owner_counter=owner_counter,
        selection_reason=selection.selection_reason,
    )
    if source_before != selection:
        raise FreshFormalStateCloneErrorV0233(STATE_CLONE_BLOCKER_V0233)

    destination = Path(destination_root).expanduser()
    if not destination.is_absolute():
        destination = destination.absolute()
    try:
        _require_destination_ancestors(destination)
    except ProductStateCloneErrorV0232 as error:
        raise FreshFormalStateCloneErrorV0233(STATE_CLONE_BLOCKER_V0233) from error
    clone_container = destination.parent
    if (
        destination.exists()
        or destination.is_symlink()
        or clone_container.exists()
        or clone_container.is_symlink()
    ):
        raise FreshFormalStateCloneErrorV0233(
            "Product v0.2.3.3 clone destination already exists"
        )
    clone_container.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(
        tempfile.mkdtemp(
            prefix=".product-state-clone-v0233-", dir=clone_container.parent
        )
    )
    clone_container.mkdir(mode=0o700)
    completed = False
    try:
        _online_backup(source / "product.sqlite3", temporary / "product.sqlite3")
        for object_path in sorted((source / "objects/sha256").glob("*/*.json")):
            _copy_file(object_path, temporary / object_path.relative_to(source))
        for name in _PILOT_FILES:
            _copy_file(source / "pilot" / name, temporary / "pilot" / name)

        before = _inspect_source(temporary, owner_counter=lambda _database: 0)
        if (
            before.schema_version != selection.source_schema_version
            or before.database_logical_sha256
            != selection.source_database_logical_sha256
            or before.object_inventory_sha256
            != selection.source_object_inventory_sha256
            or before.runtime_inventory_sha256
            != selection.source_runtime_inventory_sha256
            or before.counts != selection.source_counts
        ):
            raise FreshFormalStateCloneErrorV0233(STATE_CLONE_BLOCKER_V0233)

        SqliteStoreV1(temporary / "product.sqlite3")
        _checkpoint_clone_database(temporary / "product.sqlite3")
        after = _inspect_source(temporary, owner_counter=lambda _database: 0)
        source_after = admit_fresh_formal_source_v0233(
            candidate,
            owner_counter=owner_counter,
            selection_reason=selection.selection_reason,
        )
        if source_after != selection:
            raise FreshFormalStateCloneErrorV0233(STATE_CLONE_BLOCKER_V0233)

        result = _clone_result_from_inspection_v0233(
            selection=selection,
            inspection=after,
            destination_locator=destination_locator,
        )
        temporary.replace(destination)
        completed = True
        return result
    except FreshFormalStateCloneErrorV0233:
        raise
    except (OSError, sqlite3.Error, ValueError) as error:
        raise FreshFormalStateCloneErrorV0233(STATE_CLONE_BLOCKER_V0233) from error
    finally:
        if not completed:
            shutil.rmtree(temporary, ignore_errors=True)
            try:
                clone_container.rmdir()
            except OSError:
                pass


__all__ = (
    "HISTORY_AND_HANDOFF_PASS_V0233",
    "PRIVATE_PRODUCT_STATE_BLOCKER_V0233",
    "SOURCE_AND_CLONE_CONTRACT_PASS_V0233",
    "STATE_CLONE_BLOCKER_V0233",
    "FreshFormalSourceCandidateV0233",
    "FreshFormalSourceKindV0233",
    "FreshFormalSourceSelectionErrorV0233",
    "FreshFormalSourceSelectionV0233",
    "FreshFormalStateCloneErrorV0233",
    "FreshFormalStateCloneV0233",
    "FreshFormalStateCountsV0233",
    "admit_fresh_formal_source_v0233",
    "clone_fresh_formal_state_v0233",
    "recover_fresh_formal_state_clone_v0233",
    "configured_source_candidates_v0233",
    "read_fresh_formal_state_counts_v0233",
    "read_formal_active_binding_v0233",
    "read_formal_diagnosis_action_totals_v0233",
    "read_raw_formal_state_counts_v0233",
    "select_fresh_formal_source_v0233",
)
