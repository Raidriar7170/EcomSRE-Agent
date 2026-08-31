"""Forensic schema-8 reconstruction contracts for Product v0.2.3.2.3."""

from __future__ import annotations

import ast
import base64
from contextlib import contextmanager
from enum import Enum
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import stat
import subprocess
from typing import Any, cast, Iterator, Literal, Mapping, Sequence

from pydantic import ConfigDict, Field, model_validator

from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22
from ecomsre.product.contracts import ProductModelV1
from ecomsre.product.pilot.forensic_schema8_v02323 import (
    ForensicSqliteReaderV02323,
)
from ecomsre.product.pilot.product_state_clone_v0232 import (
    admit_product_state_source_v0232,
)


GOAL_VERSION_V02323: Literal[
    "ecomsre-product-v02323-schema8-reconstruction-diagnosis-replay-v1"
] = "ecomsre-product-v02323-schema8-reconstruction-diagnosis-replay-v1"
PR83_HEAD_V02323 = "142dc1094926f18e789ece3668c34918f859b512"
PR83_MIGRATIONS_PATH_V02323 = "src/ecomsre/product/storage/migrations.py"
PR83_MIGRATIONS_BLOB_V02323 = "b0918363182b1fa6ce10aca90ef03f3d05a96cfd"
PR84_HEAD_V02323 = "0dfd9c93f7e1f8797aacfee198694b5b2380221c"
PR84_MIGRATIONS_BLOB_V02323 = "195ce09b0b444979391e949f10c58cd5496a10ac"
SCHEMA9_CONTAMINATION_AUDIT_PASS_V02323: Literal[
    "ECOMSRE_PRODUCT_V02323_SCHEMA9_CONTAMINATION_AUDIT_PASS"
] = (
    "ECOMSRE_PRODUCT_V02323_SCHEMA9_CONTAMINATION_AUDIT_PASS"
)
RECONSTRUCTION_DISPOSITION_FROZEN_V02323: Literal[
    "ECOMSRE_PRODUCT_V02323_RECONSTRUCTION_DISPOSITION_FROZEN"
] = (
    "ECOMSRE_PRODUCT_V02323_RECONSTRUCTION_DISPOSITION_FROZEN"
)
PRISTINE_BASE_DELTA_RECONSTRUCTION_PASS_V02323: Literal[
    "ECOMSRE_PRODUCT_V02323_PRISTINE_BASE_DELTA_RECONSTRUCTION_PASS"
] = (
    "ECOMSRE_PRODUCT_V02323_PRISTINE_BASE_DELTA_RECONSTRUCTION_PASS"
)
SCHEMA8_PROJECTION_RECONSTRUCTION_PASS_V02323 = (
    "ECOMSRE_PRODUCT_V02323_SCHEMA8_PROJECTION_RECONSTRUCTION_PASS"
)
EXACT_RECONSTRUCTION_NOT_AVAILABLE_V02323 = (
    "ECOMSRE_PRODUCT_V02323_EXACT_RECONSTRUCTION_NOT_AVAILABLE"
)
RECONSTRUCTION_BLOCKER_V02323 = "BLOCKED_ECOMSRE_PRODUCT_V02323_RECONSTRUCTION"

_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_REFERENCE_APPLIED_AT = "1970-01-01T00:00:00+00:00"


class ReconstructionContractErrorV02323(RuntimeError):
    """A schema-8 reconstruction claim cannot be proved."""


class Schema8MigrationV02323(ProductModelV1):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: int = Field(ge=1, le=8)
    name: str
    statements: tuple[str, ...]


class Schema8DefinitionV02323(ProductModelV1):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[
        "ecomsre.product.schema8-definition.v02323"
    ] = "ecomsre.product.schema8-definition.v02323"
    goal_version: Literal[
        "ecomsre-product-v02323-schema8-reconstruction-diagnosis-replay-v1"
    ] = GOAL_VERSION_V02323
    source_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    source_path: str
    source_blob_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    migrations: tuple[Schema8MigrationV02323, ...]
    schema_sql_sha256: str = Field(pattern=_SHA256_PATTERN)
    reference_database_logical_sha256: str = Field(pattern=_SHA256_PATTERN)
    reference_schema_inventory: tuple[dict[str, Any], ...]
    schema8_definition_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def require_exact_source_and_self_seal(self) -> Schema8DefinitionV02323:
        if (
            self.source_commit != PR83_HEAD_V02323
            or self.source_path != PR83_MIGRATIONS_PATH_V02323
            or self.source_blob_sha != PR83_MIGRATIONS_BLOB_V02323
            or tuple(item.version for item in self.migrations) != tuple(range(1, 9))
        ):
            raise ValueError(RECONSTRUCTION_BLOCKER_V02323)
        body = self.model_dump(mode="json", exclude={"schema8_definition_sha256"})
        if self.schema8_definition_sha256 != semantic_sha256_v22(body):
            raise ValueError(RECONSTRUCTION_BLOCKER_V02323)
        return self


class Schema9ContaminationClassV02323(str, Enum):
    ADDITIVE_SCHEMA_ONLY = "ADDITIVE_SCHEMA_ONLY"
    ADDITIVE_SCHEMA_AND_JOURNAL_ONLY = "ADDITIVE_SCHEMA_AND_JOURNAL_ONLY"
    SCHEMA8_ROW_DRIFT_DETECTED = "SCHEMA8_ROW_DRIFT_DETECTED"
    UNPROVEN = "UNPROVEN"


class Schema9DefinitionV02323(ProductModelV1):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source_commit: Literal["0dfd9c93f7e1f8797aacfee198694b5b2380221c"]
    source_path: Literal["src/ecomsre/product/storage/migrations.py"]
    source_blob_sha: Literal["195ce09b0b444979391e949f10c58cd5496a10ac"]
    migration_version: Literal[9]
    migration_name: Literal["product-v02322-diagnosis-stage-journal"]
    statements: tuple[str, ...]
    expected_schema_inventory: tuple[dict[str, Any], ...]
    expected_schema_inventory_sha256: str = Field(pattern=_SHA256_PATTERN)
    schema9_definition_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def require_self_seal(self) -> Schema9DefinitionV02323:
        body = self.model_dump(mode="json", exclude={"schema9_definition_sha256"})
        if self.schema9_definition_sha256 != semantic_sha256_v22(body):
            raise ValueError(RECONSTRUCTION_BLOCKER_V02323)
        return self


class Schema8ProjectionTableV02323(ProductModelV1):
    model_config = ConfigDict(extra="forbid", frozen=True)

    table: str
    columns: tuple[str, ...]
    row_count: int = Field(ge=0)
    canonical_rows_sha256: str = Field(pattern=_SHA256_PATTERN)


class Schema8ProjectionExportV02323(ProductModelV1):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[
        "ecomsre.product.schema8-projection-export.v02323"
    ] = "ecomsre.product.schema8-projection-export.v02323"
    goal_version: Literal[
        "ecomsre-product-v02323-schema8-reconstruction-diagnosis-replay-v1"
    ] = GOAL_VERSION_V02323
    schema_definition_sha256: str = Field(pattern=_SHA256_PATTERN)
    tables: tuple[Schema8ProjectionTableV02323, ...]
    overall_projection_sha256: str = Field(pattern=_SHA256_PATTERN)
    formal_artifact_bindings: dict[str, str]
    export_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def require_self_seal(self) -> Schema8ProjectionExportV02323:
        if tuple(item.table for item in self.tables) != tuple(
            sorted(item.table for item in self.tables)
        ):
            raise ValueError(RECONSTRUCTION_BLOCKER_V02323)
        body = self.model_dump(mode="json", exclude={"export_sha256"})
        if self.export_sha256 != semantic_sha256_v22(body):
            raise ValueError(RECONSTRUCTION_BLOCKER_V02323)
        return self


class FormalRowDeltaV02323(ProductModelV1):
    model_config = ConfigDict(extra="forbid", frozen=True)

    table: str
    primary_key: dict[str, object]
    operation: Literal["INSERT", "UPDATE"]
    pre_state_present: bool
    pre_state_canonical_row_sha256: str | None = Field(
        default=None, pattern=_SHA256_PATTERN
    )
    post_state_canonical_row_sha256: str = Field(pattern=_SHA256_PATTERN)
    provenance_artifact: str
    provenance_artifact_sha256: str = Field(pattern=_SHA256_PATTERN)
    reason: str


class FormalProductDeltaV02323(ProductModelV1):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[
        "ecomsre.product.formal-product-delta.v02323"
    ] = "ecomsre.product.formal-product-delta.v02323"
    goal_version: Literal[
        "ecomsre-product-v02323-schema8-reconstruction-diagnosis-replay-v1"
    ] = GOAL_VERSION_V02323
    base_projection_sha256: str = Field(pattern=_SHA256_PATTERN)
    post_formal_projection_sha256: str = Field(pattern=_SHA256_PATTERN)
    row_changes: tuple[FormalRowDeltaV02323, ...]
    changed_table_counts: dict[str, int]
    no_diagnosis_result: bool
    no_evidence_index: bool
    no_fault_family_or_knowledge_row: bool
    complete: bool
    delta_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def require_complete_and_self_seal(self) -> FormalProductDeltaV02323:
        if (
            not self.complete
            or not self.no_diagnosis_result
            or not self.no_evidence_index
            or not self.no_fault_family_or_knowledge_row
        ):
            raise ValueError(RECONSTRUCTION_BLOCKER_V02323)
        body = self.model_dump(mode="json", exclude={"delta_sha256"})
        if self.delta_sha256 != semantic_sha256_v22(body):
            raise ValueError(RECONSTRUCTION_BLOCKER_V02323)
        return self


class PristineBaseAdmissionV02323(ProductModelV1):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[
        "ecomsre.product.pristine-base-admission.v02323"
    ] = "ecomsre.product.pristine-base-admission.v02323"
    source_locator: str
    source_state_sha256: str = Field(pattern=_SHA256_PATTERN)
    source_database_logical_sha256: str = Field(pattern=_SHA256_PATTERN)
    source_database_file_sha256: str = Field(pattern=_SHA256_PATTERN)
    source_object_inventory_sha256: str = Field(pattern=_SHA256_PATTERN)
    source_runtime_file_inventory_sha256: str = Field(pattern=_SHA256_PATTERN)
    source_schema_version: Literal[7]
    admitted: Literal[True]
    admission_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def require_self_seal(self) -> PristineBaseAdmissionV02323:
        body = self.model_dump(mode="json", exclude={"admission_sha256"})
        if self.admission_sha256 != semantic_sha256_v22(body):
            raise ValueError(RECONSTRUCTION_BLOCKER_V02323)
        return self


class Schema9ContaminationAuditV02323(ProductModelV1):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[
        "ecomsre.product.schema9-contamination-audit.v02323"
    ] = "ecomsre.product.schema9-contamination-audit.v02323"
    goal_version: Literal[
        "ecomsre-product-v02323-schema8-reconstruction-diagnosis-replay-v1"
    ] = GOAL_VERSION_V02323
    terminal: Literal[
        "ECOMSRE_PRODUCT_V02323_SCHEMA9_CONTAMINATION_AUDIT_PASS"
    ] = SCHEMA9_CONTAMINATION_AUDIT_PASS_V02323
    contamination_class: Schema9ContaminationClassV02323
    schema8_definition_sha256: str = Field(pattern=_SHA256_PATTERN)
    schema9_definition_sha256: str = Field(pattern=_SHA256_PATTERN)
    expected_schema9_inventory_sha256: str = Field(pattern=_SHA256_PATTERN)
    source_schema_inventory_sha256: str = Field(pattern=_SHA256_PATTERN)
    schema9_inventory_matches_expected: bool
    source_schema_version: Literal[9]
    extra_objects: tuple[str, ...]
    extra_tables: tuple[str, ...]
    extra_columns: dict[str, tuple[str, ...]]
    extra_indexes: tuple[str, ...]
    missing_schema8_objects: tuple[str, ...]
    unexpected_schema8_changes: tuple[str, ...]
    schema_migrations_above_8: tuple[dict[str, object], ...]
    new_diagnosis_job_column_non_null_counts: dict[str, int]
    diagnosis_stage_event_count: int = Field(ge=0)
    diagnosis_stage_event_rows_sha256: str = Field(pattern=_SHA256_PATTERN)
    source_schema8_projection_sha256: str = Field(pattern=_SHA256_PATTERN)
    reconstructed_schema8_projection_sha256: str = Field(pattern=_SHA256_PATTERN)
    schema8_projection_matches_reconstruction: bool
    foreign_key_check_clean: bool
    source_object_inventory_sha256: str = Field(pattern=_SHA256_PATTERN)
    source_runtime_file_inventory_sha256: str = Field(pattern=_SHA256_PATTERN)
    formal_artifact_bindings: dict[str, str]
    tracked_formal_facts: dict[str, object]
    source_immutability_proof_sha256: str = Field(pattern=_SHA256_PATTERN)
    audit_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def require_pass_and_self_seal(self) -> Schema9ContaminationAuditV02323:
        if (
            self.contamination_class
            not in {
                Schema9ContaminationClassV02323.ADDITIVE_SCHEMA_ONLY,
                Schema9ContaminationClassV02323.ADDITIVE_SCHEMA_AND_JOURNAL_ONLY,
            }
            or not self.schema8_projection_matches_reconstruction
            or not self.schema9_inventory_matches_expected
            or not self.foreign_key_check_clean
            or self.missing_schema8_objects
            or self.unexpected_schema8_changes
        ):
            raise ValueError(RECONSTRUCTION_BLOCKER_V02323)
        body = self.model_dump(mode="json", exclude={"audit_sha256"})
        if self.audit_sha256 != semantic_sha256_v22(body):
            raise ValueError(RECONSTRUCTION_BLOCKER_V02323)
        return self


class PostFormalProductStateCountsV02323(ProductModelV1):
    model_config = ConfigDict(extra="forbid", frozen=True)

    baseline_count: int = Field(ge=0)
    active_baseline_count: int = Field(ge=0)
    baseline_job_count: int = Field(ge=0)
    verify_job_count: int = Field(ge=0)
    diagnosis_job_count: int = Field(ge=0)
    incident_count: int = Field(ge=0)
    diagnosis_count: int = Field(ge=0)
    evidence_object_count: int = Field(ge=0)
    failed_job_count: int = Field(ge=0)
    pending_job_count: int = Field(ge=0)
    running_job_count: int = Field(ge=0)
    fault_family_count: int = Field(ge=0)
    knowledge_artifact_count: int = Field(ge=0)
    diagnosis_evidence_index_count: int = Field(ge=0)


class PostFormalProductStateV02323(ProductModelV1):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[
        "ecomsre.product.post-formal-state.v02323"
    ] = "ecomsre.product.post-formal-state.v02323"
    counts: PostFormalProductStateCountsV02323
    environment_id: str
    active_baseline_id: str
    active_baseline_sha256: str = Field(pattern=_SHA256_PATTERN)
    active_p01_profile_sha256: str = Field(pattern=_SHA256_PATTERN)
    formal_incident_id: str
    failed_diagnosis_job_id: str
    successor_diagnosis_absent: bool
    historical_raw_byte_authority: str
    historical_logical_authority: str
    replay_authority: str
    measured_nofault_authority: Literal["NONE"]
    knowledge_loop_authority: Literal["NONE"]
    state_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def require_expected_state_and_self_seal(self) -> PostFormalProductStateV02323:
        expected_counts = {
            "baseline_count": 1,
            "active_baseline_count": 1,
            "baseline_job_count": 1,
            "verify_job_count": 1,
            "diagnosis_job_count": 2,
            "incident_count": 2,
            "diagnosis_count": 1,
            "evidence_object_count": 6,
            "failed_job_count": 1,
            "pending_job_count": 0,
            "running_job_count": 0,
            "fault_family_count": 0,
            "knowledge_artifact_count": 0,
            "diagnosis_evidence_index_count": 0,
        }
        if (
            self.counts.model_dump(mode="json") != expected_counts
            or self.environment_id != "env-2b5c86f47f449acfc54cfcec"
            or self.active_baseline_id != "base-b25440a36089a8f0e6b9f1dc"
            or self.active_baseline_sha256
            != "6d3d2d7a4854d1cfc2477746e7d0c940ed8a08644ebc69b7b91066eabe45ae64"
            or self.active_p01_profile_sha256
            != "b9577dfc4eaa933b62048bbcbd041ed470343f7c76255ab851cdcaeef60a7df2"
            or self.formal_incident_id != "inc-a5a8df708ab77c2f2e19da63"
            or self.failed_diagnosis_job_id != "job-216dd1caac0b92270b1870a2"
            or not self.successor_diagnosis_absent
        ):
            raise ValueError(RECONSTRUCTION_BLOCKER_V02323)
        body = self.model_dump(mode="json", exclude={"state_sha256"})
        if self.state_sha256 != semantic_sha256_v22(body):
            raise ValueError(RECONSTRUCTION_BLOCKER_V02323)
        return self


class Schema8ReconstructionV02323(ProductModelV1):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[
        "ecomsre.product.schema8-reconstruction.v02323"
    ] = "ecomsre.product.schema8-reconstruction.v02323"
    reconstruction_locator: str
    schema8_definition_sha256: str = Field(pattern=_SHA256_PATTERN)
    formal_delta_sha256: str = Field(pattern=_SHA256_PATTERN)
    source_projection_sha256: str = Field(pattern=_SHA256_PATTERN)
    reconstructed_projection_sha256: str = Field(pattern=_SHA256_PATTERN)
    reconstructed_schema_version: Literal[8]
    reconstructed_database_file_sha256: str = Field(pattern=_SHA256_PATTERN)
    reconstructed_database_logical_sha256: str = Field(pattern=_SHA256_PATTERN)
    source_object_inventory_sha256: str = Field(pattern=_SHA256_PATTERN)
    source_runtime_file_inventory_sha256: str = Field(pattern=_SHA256_PATTERN)
    reconstructed_object_inventory_sha256: str = Field(pattern=_SHA256_PATTERN)
    reconstructed_runtime_file_inventory_sha256: str = Field(pattern=_SHA256_PATTERN)
    reconstructed_product_state_semantic_sha256: str = Field(pattern=_SHA256_PATTERN)
    schema_inventory_sha256: str = Field(pattern=_SHA256_PATTERN)
    foreign_key_check_clean: bool
    integrity_check: Literal["ok"]
    destination_read_only: bool
    reconstruction_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def require_pass_and_self_seal(self) -> Schema8ReconstructionV02323:
        if (
            self.source_projection_sha256 != self.reconstructed_projection_sha256
            or self.source_object_inventory_sha256
            != self.reconstructed_object_inventory_sha256
            or self.source_runtime_file_inventory_sha256
            != self.reconstructed_runtime_file_inventory_sha256
            or not self.foreign_key_check_clean
            or not self.destination_read_only
        ):
            raise ValueError(RECONSTRUCTION_BLOCKER_V02323)
        body = self.model_dump(mode="json", exclude={"reconstruction_sha256"})
        if self.reconstruction_sha256 != semantic_sha256_v22(body):
            raise ValueError(RECONSTRUCTION_BLOCKER_V02323)
        return self


class ReconstructionDispositionV02323(ProductModelV1):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[
        "ecomsre.product.reconstruction-disposition.v02323"
    ] = "ecomsre.product.reconstruction-disposition.v02323"
    goal_version: Literal[
        "ecomsre-product-v02323-schema8-reconstruction-diagnosis-replay-v1"
    ] = GOAL_VERSION_V02323
    terminal: Literal[
        "ECOMSRE_PRODUCT_V02323_RECONSTRUCTION_DISPOSITION_FROZEN"
    ] = RECONSTRUCTION_DISPOSITION_FROZEN_V02323
    reconstruction_terminal: Literal[
        "ECOMSRE_PRODUCT_V02323_PRISTINE_BASE_DELTA_RECONSTRUCTION_PASS"
    ] = PRISTINE_BASE_DELTA_RECONSTRUCTION_PASS_V02323
    disposition: Literal["PRISTINE_BASE_DELTA_RECONSTRUCTION"]
    pristine_base_admission_sha256: str = Field(pattern=_SHA256_PATTERN)
    formal_delta_sha256: str = Field(pattern=_SHA256_PATTERN)
    schema9_contamination_audit_sha256: str = Field(pattern=_SHA256_PATTERN)
    schema8_projection_export_sha256: str = Field(pattern=_SHA256_PATTERN)
    reconstruction_sha256: str = Field(pattern=_SHA256_PATTERN)
    post_formal_state_sha256: str = Field(pattern=_SHA256_PATTERN)
    historical_raw_byte_authority: Literal["LOST_RAW_BYTES_NOT_RECONSTRUCTED"]
    historical_logical_authority: Literal[
        "PRISTINE_BASE_DELTA_RECONSTRUCTION"
    ]
    replay_authority: Literal["NOT_EXECUTED"]
    measured_nofault_authority: Literal["NONE"]
    knowledge_loop_authority: Literal["NONE"]
    raw_byte_equality_claimed: Literal[False]
    diagnosis_persistence_replay_attempt_count: Literal[0]
    disposition_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def require_self_seal(self) -> ReconstructionDispositionV02323:
        body = self.model_dump(mode="json", exclude={"disposition_sha256"})
        if self.disposition_sha256 != semantic_sha256_v22(body):
            raise ValueError(RECONSTRUCTION_BLOCKER_V02323)
        return self


def _git_bytes(repository: Path, revision: str, relative_path: str) -> bytes:
    completed = subprocess.run(
        ["git", "show", f"{revision}:{relative_path}"],
        cwd=repository,
        check=False,
        capture_output=True,
    )
    if completed.returncode != 0 or completed.stderr:
        raise ReconstructionContractErrorV02323(RECONSTRUCTION_BLOCKER_V02323)
    return completed.stdout


def _git_blob(repository: Path, revision: str, relative_path: str) -> str:
    completed = subprocess.run(
        ["git", "rev-parse", f"{revision}:{relative_path}"],
        cwd=repository,
        check=False,
        capture_output=True,
        text=True,
    )
    blob = completed.stdout.strip()
    if completed.returncode != 0 or completed.stderr or len(blob) != 40:
        raise ReconstructionContractErrorV02323(RECONSTRUCTION_BLOCKER_V02323)
    return blob


def _parse_migrations(source: bytes) -> tuple[Schema8MigrationV02323, ...]:
    try:
        tree = ast.parse(source.decode("utf-8"))
    except (SyntaxError, UnicodeDecodeError) as error:
        raise ReconstructionContractErrorV02323(
            RECONSTRUCTION_BLOCKER_V02323
        ) from error
    raw: object | None = None
    for node in tree.body:
        target: ast.expr | None = None
        value: ast.expr | None = None
        if isinstance(node, ast.AnnAssign):
            target = node.target
            value = node.value
        elif isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            value = node.value
        if isinstance(target, ast.Name) and target.id == "MIGRATIONS" and value:
            try:
                raw = ast.literal_eval(value)
            except (TypeError, ValueError) as error:
                raise ReconstructionContractErrorV02323(
                    RECONSTRUCTION_BLOCKER_V02323
                ) from error
            break
    if not isinstance(raw, tuple):
        raise ReconstructionContractErrorV02323(RECONSTRUCTION_BLOCKER_V02323)
    try:
        migrations = tuple(
            Schema8MigrationV02323(
                version=version,
                name=name,
                statements=tuple(statements),
            )
            for version, name, statements in raw
            if version <= 8
        )
    except (TypeError, ValueError) as error:
        raise ReconstructionContractErrorV02323(
            RECONSTRUCTION_BLOCKER_V02323
        ) from error
    if tuple(item.version for item in migrations) != tuple(range(1, 9)):
        raise ReconstructionContractErrorV02323(RECONSTRUCTION_BLOCKER_V02323)
    return migrations


def _normalized_sqlite_value(value: object) -> object:
    if isinstance(value, bytes):
        return {"base64": base64.b64encode(value).decode("ascii")}
    if value is None or isinstance(value, (str, int, float)):
        return value
    raise ReconstructionContractErrorV02323(RECONSTRUCTION_BLOCKER_V02323)


def _quoted_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _normalized_schema_sql(sql: object) -> object:
    """Collapse only whitespace outside quoted SQLite tokens."""

    if not isinstance(sql, str):
        return sql
    output: list[str] = []
    quote: str | None = None
    pending_space = False
    index = 0
    while index < len(sql):
        character = sql[index]
        if quote is not None:
            output.append(character)
            if character == quote:
                if index + 1 < len(sql) and sql[index + 1] == quote:
                    output.append(sql[index + 1])
                    index += 1
                else:
                    quote = None
        elif character in {"'", '"'}:
            if pending_space and output:
                output.append(" ")
            pending_space = False
            output.append(character)
            quote = character
        elif character.isspace():
            pending_space = True
        else:
            if pending_space and output:
                output.append(" ")
            pending_space = False
            output.append(character)
        index += 1
    return "".join(output).strip()


def _normalized_schema_inventory(
    inventory: Sequence[Mapping[str, object]],
) -> tuple[dict[str, object], ...]:
    normalized = json.loads(json.dumps(inventory, sort_keys=True, separators=(",", ":")))
    if not isinstance(normalized, list):
        raise ReconstructionContractErrorV02323(RECONSTRUCTION_BLOCKER_V02323)
    result: list[dict[str, object]] = []
    for item in normalized:
        if not isinstance(item, dict):
            raise ReconstructionContractErrorV02323(RECONSTRUCTION_BLOCKER_V02323)
        item["sql"] = _normalized_schema_sql(item.get("sql"))
        result.append(item)
    return tuple(result)


def _canonical_rows(
    connection: sqlite3.Connection,
    table: str,
    columns: Sequence[str],
    *,
    schema8_only: bool = False,
) -> tuple[tuple[object, ...], ...]:
    selected = ",".join(_quoted_identifier(column) for column in columns)
    query = f"SELECT {selected} FROM {_quoted_identifier(table)}"
    if schema8_only and table == "schema_migrations":
        query += " WHERE version <= 8"
    rows = [
        tuple(_normalized_sqlite_value(value) for value in tuple(row))
        for row in connection.execute(query).fetchall()
    ]
    rows.sort(key=lambda item: json.dumps(item, sort_keys=True, separators=(",", ":")))
    return tuple(rows)


def _logical_database_sha256(connection: sqlite3.Connection) -> str:
    integrity = connection.execute("PRAGMA integrity_check").fetchone()
    if integrity is None or integrity[0] != "ok":
        raise ReconstructionContractErrorV02323(RECONSTRUCTION_BLOCKER_V02323)
    schema = [
        [_normalized_sqlite_value(value) for value in tuple(row)]
        for row in connection.execute(
            "SELECT type, name, tbl_name, sql FROM sqlite_master "
            "WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name"
        ).fetchall()
    ]
    tables: dict[str, object] = {}
    names = sorted(
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' "
            "AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
    )
    for name in names:
        columns = tuple(
            str(row[1])
            for row in connection.execute(
                f"PRAGMA table_info({_quoted_identifier(name)})"
            ).fetchall()
        )
        tables[name] = {
            "columns": columns,
            "rows": _canonical_rows(connection, name, columns),
        }
    return semantic_sha256_v22({"schema": schema, "tables": tables})


def _schema_inventory(connection: sqlite3.Connection) -> tuple[dict[str, Any], ...]:
    inventory: list[dict[str, Any]] = []
    rows = connection.execute(
        "SELECT type, name, tbl_name, sql FROM sqlite_master "
        "WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name"
    ).fetchall()
    for object_type, name, table_name, sql in rows:
        item: dict[str, Any] = {
            "type": str(object_type),
            "name": str(name),
            "table_name": str(table_name),
            "sql": None if sql is None else str(sql),
        }
        if object_type == "table":
            item["columns"] = tuple(
                {
                    "cid": int(column[0]),
                    "name": str(column[1]),
                    "declared_type": str(column[2]),
                    "not_null": bool(column[3]),
                    "default": _normalized_sqlite_value(column[4]),
                    "primary_key_ordinal": int(column[5]),
                    "hidden": int(column[6]),
                }
                for column in connection.execute(
                    f"PRAGMA table_xinfo({_quoted_identifier(str(name))})"
                ).fetchall()
            )
            item["foreign_keys"] = tuple(
                {
                    "id": int(foreign_key[0]),
                    "sequence": int(foreign_key[1]),
                    "target_table": str(foreign_key[2]),
                    "from_column": str(foreign_key[3]),
                    "to_column": (
                        None if foreign_key[4] is None else str(foreign_key[4])
                    ),
                    "on_update": str(foreign_key[5]),
                    "on_delete": str(foreign_key[6]),
                    "match": str(foreign_key[7]),
                }
                for foreign_key in connection.execute(
                    f"PRAGMA foreign_key_list({_quoted_identifier(str(name))})"
                ).fetchall()
            )
        inventory.append(item)
    return tuple(inventory)


def _apply_schema(
    connection: sqlite3.Connection,
    migrations: Sequence[Schema8MigrationV02323],
    *,
    record_migrations: bool,
) -> None:
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("BEGIN IMMEDIATE")
    try:
        for migration in migrations:
            for statement in migration.statements:
                connection.execute(statement)
            if record_migrations:
                connection.execute(
                    "INSERT INTO schema_migrations(version, name, applied_at) "
                    "VALUES (?, ?, ?)",
                    (migration.version, migration.name, _REFERENCE_APPLIED_AT),
                )
        connection.execute("COMMIT")
    except Exception:
        connection.execute("ROLLBACK")
        raise


def load_schema8_definition_v02323(repository: Path) -> Schema8DefinitionV02323:
    """Load migrations 1-8 from the frozen PR #83 Git object."""

    root = Path(repository).resolve(strict=True)
    source = _git_bytes(root, PR83_HEAD_V02323, PR83_MIGRATIONS_PATH_V02323)
    blob = _git_blob(root, PR83_HEAD_V02323, PR83_MIGRATIONS_PATH_V02323)
    if blob != PR83_MIGRATIONS_BLOB_V02323:
        raise ReconstructionContractErrorV02323(RECONSTRUCTION_BLOCKER_V02323)
    migrations = _parse_migrations(source)
    connection = sqlite3.connect(":memory:", isolation_level=None)
    try:
        _apply_schema(connection, migrations, record_migrations=True)
        inventory = _schema_inventory(connection)
        logical_sha256 = _logical_database_sha256(connection)
    except sqlite3.Error as error:
        raise ReconstructionContractErrorV02323(
            RECONSTRUCTION_BLOCKER_V02323
        ) from error
    finally:
        connection.close()
    migration_payload = tuple(item.model_dump(mode="json") for item in migrations)
    body: dict[str, object] = {
        "schema_version": "ecomsre.product.schema8-definition.v02323",
        "goal_version": GOAL_VERSION_V02323,
        "source_commit": PR83_HEAD_V02323,
        "source_path": PR83_MIGRATIONS_PATH_V02323,
        "source_blob_sha": blob,
        "migrations": migration_payload,
        "schema_sql_sha256": semantic_sha256_v22(migration_payload),
        "reference_database_logical_sha256": logical_sha256,
        "reference_schema_inventory": inventory,
    }
    try:
        return Schema8DefinitionV02323.model_validate(
            {**body, "schema8_definition_sha256": semantic_sha256_v22(body)}
        )
    except ValueError as error:
        raise ReconstructionContractErrorV02323(
            RECONSTRUCTION_BLOCKER_V02323
        ) from error


def load_schema9_definition_v02323(
    repository: Path,
    schema8_definition: Schema8DefinitionV02323,
) -> Schema9DefinitionV02323:
    root = Path(repository).resolve(strict=True)
    source = _git_bytes(root, PR84_HEAD_V02323, PR83_MIGRATIONS_PATH_V02323)
    if _git_blob(root, PR84_HEAD_V02323, PR83_MIGRATIONS_PATH_V02323) != PR84_MIGRATIONS_BLOB_V02323:
        raise ReconstructionContractErrorV02323(RECONSTRUCTION_BLOCKER_V02323)
    tree = ast.parse(source.decode("utf-8"))
    raw: object | None = None
    for node in tree.body:
        if (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == "MIGRATIONS"
            and node.value is not None
        ):
            raw = ast.literal_eval(node.value)
            break
    if not isinstance(raw, tuple):
        raise ReconstructionContractErrorV02323(RECONSTRUCTION_BLOCKER_V02323)
    matches = [item for item in raw if isinstance(item, tuple) and item[0] == 9]
    if len(matches) != 1:
        raise ReconstructionContractErrorV02323(RECONSTRUCTION_BLOCKER_V02323)
    version, name, statements = matches[0]
    connection = sqlite3.connect(":memory:", isolation_level=None)
    try:
        _apply_schema(connection, schema8_definition.migrations, record_migrations=False)
        for statement in statements:
            connection.execute(statement)
        inventory = _schema_inventory(connection)
    finally:
        connection.close()
    body: dict[str, object] = {
        "source_commit": PR84_HEAD_V02323,
        "source_path": PR83_MIGRATIONS_PATH_V02323,
        "source_blob_sha": PR84_MIGRATIONS_BLOB_V02323,
        "migration_version": version,
        "migration_name": name,
        "statements": tuple(statements),
        "expected_schema_inventory": inventory,
        "expected_schema_inventory_sha256": semantic_sha256_v22(inventory),
    }
    return Schema9DefinitionV02323.model_validate(
        {**body, "schema9_definition_sha256": semantic_sha256_v22(body)}
    )


def _definition_tables(
    definition: Schema8DefinitionV02323,
) -> dict[str, tuple[str, ...]]:
    result: dict[str, tuple[str, ...]] = {}
    for item in definition.reference_schema_inventory:
        if item.get("type") != "table":
            continue
        name = item.get("name")
        columns = item.get("columns")
        if not isinstance(name, str) or not isinstance(columns, (tuple, list)):
            raise ReconstructionContractErrorV02323(RECONSTRUCTION_BLOCKER_V02323)
        names: list[str] = []
        for column in columns:
            if not isinstance(column, Mapping) or not isinstance(column.get("name"), str):
                raise ReconstructionContractErrorV02323(
                    RECONSTRUCTION_BLOCKER_V02323
                )
            names.append(str(column["name"]))
        result[name] = tuple(names)
    if len(result) != 30:
        raise ReconstructionContractErrorV02323(RECONSTRUCTION_BLOCKER_V02323)
    return result


@contextmanager
def _read_only_connection(database: Path) -> Iterator[sqlite3.Connection]:
    candidate = Path(database)
    if candidate.is_symlink() or not candidate.is_file():
        raise ReconstructionContractErrorV02323(RECONSTRUCTION_BLOCKER_V02323)
    uri = f"file:{candidate.resolve(strict=True).as_posix()}?mode=ro&immutable=1"
    connection = sqlite3.connect(uri, uri=True, isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only = ON")
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        yield connection
    finally:
        connection.close()


def _raw_projection_rows(
    connection: sqlite3.Connection,
    definition: Schema8DefinitionV02323,
    *,
    allow_missing_schema8_tables: bool = False,
) -> dict[str, tuple[tuple[object, ...], ...]]:
    tables = _definition_tables(definition)
    present = {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' "
            "AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
    }
    missing = set(tables) - present
    if missing and (
        not allow_missing_schema8_tables
        or missing != {"diagnosis_evidence_indexes"}
    ):
        raise ReconstructionContractErrorV02323(RECONSTRUCTION_BLOCKER_V02323)
    rows_by_table: dict[str, tuple[tuple[object, ...], ...]] = {}
    for table, columns in sorted(tables.items()):
        if table not in present:
            rows_by_table[table] = ()
            continue
        selected = ",".join(_quoted_identifier(column) for column in columns)
        query = f"SELECT {selected} FROM {_quoted_identifier(table)}"
        if table == "schema_migrations":
            query += " WHERE version <= 8"
        rows = [tuple(row) for row in connection.execute(query).fetchall()]
        rows.sort(
            key=lambda row: json.dumps(
                tuple(_normalized_sqlite_value(value) for value in row),
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        rows_by_table[table] = tuple(rows)
    return rows_by_table


def _projection_table_models(
    definition: Schema8DefinitionV02323,
    rows_by_table: Mapping[str, Sequence[Sequence[object]]],
) -> tuple[Schema8ProjectionTableV02323, ...]:
    tables = _definition_tables(definition)
    models: list[Schema8ProjectionTableV02323] = []
    if set(rows_by_table) != set(tables):
        raise ReconstructionContractErrorV02323(RECONSTRUCTION_BLOCKER_V02323)
    for table, columns in sorted(tables.items()):
        normalized_rows = tuple(
            tuple(_normalized_sqlite_value(value) for value in row)
            for row in rows_by_table[table]
        )
        models.append(
            Schema8ProjectionTableV02323(
                table=table,
                columns=columns,
                row_count=len(normalized_rows),
                canonical_rows_sha256=semantic_sha256_v22(normalized_rows),
            )
        )
    return tuple(models)


def _projection_sha256(
    definition: Schema8DefinitionV02323,
    rows_by_table: Mapping[str, Sequence[Sequence[object]]],
) -> str:
    models = _projection_table_models(definition, rows_by_table)
    return semantic_sha256_v22(
        tuple(item.model_dump(mode="json") for item in models)
    )


def export_schema8_projection_v02323(
    database: Path,
    definition: Schema8DefinitionV02323,
    *,
    formal_artifact_bindings: Mapping[str, str],
    allow_missing_schema8_tables: bool = False,
) -> tuple[
    Schema8ProjectionExportV02323,
    dict[str, tuple[tuple[object, ...], ...]],
]:
    """Export only schema-8 tables and columns, preserving SQLite value types."""

    try:
        with _read_only_connection(database) as connection:
            if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ReconstructionContractErrorV02323(
                    RECONSTRUCTION_BLOCKER_V02323
                )
            rows_by_table = _raw_projection_rows(
                connection,
                definition,
                allow_missing_schema8_tables=allow_missing_schema8_tables,
            )
    except sqlite3.Error as error:
        raise ReconstructionContractErrorV02323(
            RECONSTRUCTION_BLOCKER_V02323
        ) from error
    tables = _projection_table_models(definition, rows_by_table)
    projection_sha256 = semantic_sha256_v22(
        tuple(item.model_dump(mode="json") for item in tables)
    )
    bindings = dict(sorted(formal_artifact_bindings.items()))
    if any(re.fullmatch(_SHA256_PATTERN, value) is None for value in bindings.values()):
        raise ReconstructionContractErrorV02323(RECONSTRUCTION_BLOCKER_V02323)
    body: dict[str, object] = {
        "schema_version": "ecomsre.product.schema8-projection-export.v02323",
        "goal_version": GOAL_VERSION_V02323,
        "schema_definition_sha256": definition.schema8_definition_sha256,
        "tables": tuple(item.model_dump(mode="json") for item in tables),
        "overall_projection_sha256": projection_sha256,
        "formal_artifact_bindings": bindings,
    }
    try:
        export = Schema8ProjectionExportV02323.model_validate(
            {**body, "export_sha256": semantic_sha256_v22(body)}
        )
    except ValueError as error:
        raise ReconstructionContractErrorV02323(
            RECONSTRUCTION_BLOCKER_V02323
        ) from error
    return export, rows_by_table


def _primary_key_columns(
    definition: Schema8DefinitionV02323, table: str
) -> tuple[str, ...]:
    for item in definition.reference_schema_inventory:
        if item.get("type") != "table" or item.get("name") != table:
            continue
        columns = item.get("columns")
        if not isinstance(columns, (tuple, list)):
            break
        ordered: list[tuple[int, str]] = []
        all_columns: list[str] = []
        for column in columns:
            if not isinstance(column, Mapping):
                raise ReconstructionContractErrorV02323(
                    RECONSTRUCTION_BLOCKER_V02323
                )
            name = column.get("name")
            ordinal = column.get("primary_key_ordinal")
            if not isinstance(name, str) or not isinstance(ordinal, int):
                raise ReconstructionContractErrorV02323(
                    RECONSTRUCTION_BLOCKER_V02323
                )
            all_columns.append(name)
            if ordinal:
                ordered.append((ordinal, name))
        return tuple(name for _ordinal, name in sorted(ordered)) or tuple(all_columns)
    raise ReconstructionContractErrorV02323(RECONSTRUCTION_BLOCKER_V02323)


def _row_sha256(row: Sequence[object]) -> str:
    return semantic_sha256_v22(
        tuple(_normalized_sqlite_value(value) for value in row)
    )


def build_formal_product_delta_v02323(
    definition: Schema8DefinitionV02323,
    base_rows: Mapping[str, Sequence[Sequence[object]]],
    post_rows: Mapping[str, Sequence[Sequence[object]]],
    *,
    provenance_by_table: Mapping[str, tuple[str, str, str]],
    require_goal_delta: bool = False,
) -> FormalProductDeltaV02323:
    """Diff pristine schema-7 rows against the post-formal schema-8 projection."""

    columns_by_table = _definition_tables(definition)
    if set(base_rows) != set(columns_by_table) or set(post_rows) != set(columns_by_table):
        raise ReconstructionContractErrorV02323(RECONSTRUCTION_BLOCKER_V02323)
    changes: list[FormalRowDeltaV02323] = []
    replayed: dict[str, dict[tuple[object, ...], tuple[object, ...]]] = {}
    changed_counts: dict[str, int] = {}
    for table, columns in sorted(columns_by_table.items()):
        primary_keys = _primary_key_columns(definition, table)
        primary_indexes = tuple(columns.index(name) for name in primary_keys)
        base_map = {
            tuple(row[index] for index in primary_indexes): tuple(row)
            for row in base_rows[table]
        }
        post_map = {
            tuple(row[index] for index in primary_indexes): tuple(row)
            for row in post_rows[table]
        }
        if len(base_map) != len(base_rows[table]) or len(post_map) != len(post_rows[table]):
            raise ReconstructionContractErrorV02323(RECONSTRUCTION_BLOCKER_V02323)
        if set(base_map) - set(post_map):
            raise ReconstructionContractErrorV02323(RECONSTRUCTION_BLOCKER_V02323)
        replayed[table] = dict(base_map)
        for key in sorted(set(post_map), key=lambda value: json.dumps(value, default=str)):
            before = base_map.get(key)
            after = post_map[key]
            if before == after:
                continue
            provenance = provenance_by_table.get(table)
            if provenance is None or re.fullmatch(_SHA256_PATTERN, provenance[1]) is None:
                raise ReconstructionContractErrorV02323(
                    RECONSTRUCTION_BLOCKER_V02323
                )
            operation: Literal["INSERT", "UPDATE"] = (
                "INSERT" if before is None else "UPDATE"
            )
            changes.append(
                FormalRowDeltaV02323(
                    table=table,
                    primary_key={
                        name: _normalized_sqlite_value(value)
                        for name, value in zip(primary_keys, key, strict=True)
                    },
                    operation=operation,
                    pre_state_present=before is not None,
                    pre_state_canonical_row_sha256=(
                        None if before is None else _row_sha256(before)
                    ),
                    post_state_canonical_row_sha256=_row_sha256(after),
                    provenance_artifact=provenance[0],
                    provenance_artifact_sha256=provenance[1],
                    reason=provenance[2],
                )
            )
            replayed[table][key] = after
            changed_counts[table] = changed_counts.get(table, 0) + 1
        if replayed[table] != post_map:
            raise ReconstructionContractErrorV02323(RECONSTRUCTION_BLOCKER_V02323)
    expected_counts = {
        "diagnosis_jobs": 1,
        "incidents": 1,
        "job_events": 3,
        "product_metric_counters": 7,
        "schema_migrations": 1,
    }
    if require_goal_delta and changed_counts != expected_counts:
        raise ReconstructionContractErrorV02323(RECONSTRUCTION_BLOCKER_V02323)
    if require_goal_delta:
        def mapped_rows(
            table: str, rows: Sequence[Sequence[object]]
        ) -> list[dict[str, object]]:
            columns = columns_by_table[table]
            return [dict(zip(columns, row, strict=True)) for row in rows]

        incident_rows = mapped_rows("incidents", post_rows["incidents"])
        formal_incidents = [
            row
            for row in incident_rows
            if row["incident_id"] == "inc-a5a8df708ab77c2f2e19da63"
        ]
        job_rows = mapped_rows("diagnosis_jobs", post_rows["diagnosis_jobs"])
        formal_jobs = [
            row
            for row in job_rows
            if row["job_id"] == "job-216dd1caac0b92270b1870a2"
        ]
        event_rows = mapped_rows("job_events", post_rows["job_events"])
        formal_events = {
            str(row["event_id"]): row
            for row in event_rows
            if row["job_id"] == "job-216dd1caac0b92270b1870a2"
        }
        expected_events = {
            "event-c06525313a59887d876abafc": (
                "ENQUEUED",
                '{"job_type":"DIAGNOSIS"}',
            ),
            "event-2d7dd2105f11a6c182eb6fce": (
                "CLAIMED",
                '{"worker_id":"worker-61732-5955ca3c"}',
            ),
            "event-779846e415880aab6c1856d3": (
                "FAILED",
                '{"safe_error_code":"INTERNAL_CONTRACT_FAILURE"}',
            ),
        }
        event_facts = {
            event_id: (str(row["event_type"]), str(row["details_json"]))
            for event_id, row in formal_events.items()
        }
        base_metrics = {
            (str(row["metric_name"]), str(row["labels_json"])): cast(int, row["value"])
            for row in mapped_rows(
                "product_metric_counters", base_rows["product_metric_counters"]
            )
        }
        post_metrics = {
            (str(row["metric_name"]), str(row["labels_json"])): cast(int, row["value"])
            for row in mapped_rows(
                "product_metric_counters", post_rows["product_metric_counters"]
            )
        }
        expected_metric_increments = {
            '{"method":"GET","route":"/readyz","status_class":"2xx"}': 2,
            '{"method":"GET","route":"/v1/baselines/{baseline_id}/window-audit-v023","status_class":"2xx"}': 1,
            '{"method":"GET","route":"/v1/environments/{environment_id}","status_class":"2xx"}': 2,
            '{"method":"GET","route":"/v1/environments/{environment_id}/baselines","status_class":"2xx"}': 2,
            '{"method":"GET","route":"/v1/jobs/{job_id}","status_class":"2xx"}': 3,
            '{"method":"POST","route":"/v1/incidents","status_class":"2xx"}': 1,
            '{"method":"POST","route":"/v1/incidents/{incident_id}/diagnosis-jobs","status_class":"2xx"}': 1,
        }
        observed_metric_increments = {
            labels: post_metrics[("ecomsre_http_requests_total", labels)]
            - base_metrics[("ecomsre_http_requests_total", labels)]
            for labels in expected_metric_increments
        }
        migrations = mapped_rows("schema_migrations", post_rows["schema_migrations"])
        migration8 = [row for row in migrations if row["version"] == 8]
        if (
            len(formal_incidents) != 1
            or len(formal_jobs) != 1
            or formal_jobs[0]["status"] != "FAILED"
            or formal_jobs[0]["safe_error_code"] != "INTERNAL_CONTRACT_FAILURE"
            or event_facts != expected_events
            or observed_metric_increments != expected_metric_increments
            or len(migration8) != 1
            or migration8[0]["name"] != "product-v0232-diagnosis-evidence-index"
        ):
            raise ReconstructionContractErrorV02323(RECONSTRUCTION_BLOCKER_V02323)
    changed_tables = set(changed_counts)
    knowledge_tables = {
        "predicate_matrices",
        "fault_families",
        "fault_family_members",
        "human_reviews",
        "registration_drafts",
        "shadow_evaluations",
        "environment_extension_registrations",
        "environment_extension_registry_versions",
        "promotion_records",
        "revocation_records",
    }
    body: dict[str, object] = {
        "schema_version": "ecomsre.product.formal-product-delta.v02323",
        "goal_version": GOAL_VERSION_V02323,
        "base_projection_sha256": _projection_sha256(definition, base_rows),
        "post_formal_projection_sha256": _projection_sha256(definition, post_rows),
        "row_changes": tuple(item.model_dump(mode="json") for item in changes),
        "changed_table_counts": dict(sorted(changed_counts.items())),
        "no_diagnosis_result": "diagnosis_results" not in changed_tables,
        "no_evidence_index": "diagnosis_evidence_indexes" not in changed_tables,
        "no_fault_family_or_knowledge_row": not bool(changed_tables & knowledge_tables),
        "complete": True,
    }
    try:
        return FormalProductDeltaV02323.model_validate(
            {**body, "delta_sha256": semantic_sha256_v22(body)}
        )
    except ValueError as error:
        raise ReconstructionContractErrorV02323(
            RECONSTRUCTION_BLOCKER_V02323
        ) from error


def admit_pristine_base_v02323(
    source_root: Path,
    *,
    source_locator: str,
) -> PristineBaseAdmissionV02323:
    expected = {
        "source_sha256": "0860c3cefe795378b36293342fa7250bab97bb75e8767d3b5a8c200c3e05741c",
        "source_database_logical_sha256": "65a5c739b54c10cf12b973a1c9b0a5afca57eefc8f21d05877ad3185389ebce1",
        "source_database_file_sha256": "2b79610d0c3a03957c8df8817d56c1531007f713b1683a8384c0cfd4fe7baf49",
        "source_object_inventory_sha256": "93708f4e238e3bd3c9d662011ee098285eecf1112e0ab15a66b72fdcc254bf32",
        "source_runtime_file_inventory_sha256": "21714573aee49676ef9a504d29b51b043b4c80289443d1bf88227eef690a4356",
    }
    try:
        source = admit_product_state_source_v0232(
            source_root,
            source_locator=source_locator,
            expected_environment_id="env-2b5c86f47f449acfc54cfcec",
            expected_baseline_id="base-b25440a36089a8f0e6b9f1dc",
            expected_baseline_sha256=(
                "6d3d2d7a4854d1cfc2477746e7d0c940ed8a08644ebc69b7b91066eabe45ae64"
            ),
            expected_profile_sha256=(
                "b9577dfc4eaa933b62048bbcbd041ed470343f7c76255ab851cdcaeef60a7df2"
            ),
        )
    except Exception as error:
        raise ReconstructionContractErrorV02323(
            RECONSTRUCTION_BLOCKER_V02323
        ) from error
    observed = {
        "source_sha256": source.source_sha256,
        "source_database_logical_sha256": source.source_database_logical_sha256,
        "source_database_file_sha256": source.source_database_file_sha256,
        "source_object_inventory_sha256": source.source_object_inventory_sha256,
        "source_runtime_file_inventory_sha256": source.source_runtime_file_inventory_sha256,
    }
    if observed != expected:
        raise ReconstructionContractErrorV02323(RECONSTRUCTION_BLOCKER_V02323)
    with _read_only_connection(Path(source_root) / "product.sqlite3") as connection:
        version = int(connection.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0])
    body: dict[str, object] = {
        "schema_version": "ecomsre.product.pristine-base-admission.v02323",
        "source_locator": source_locator,
        "source_state_sha256": source.source_sha256,
        "source_database_logical_sha256": source.source_database_logical_sha256,
        "source_database_file_sha256": source.source_database_file_sha256,
        "source_object_inventory_sha256": source.source_object_inventory_sha256,
        "source_runtime_file_inventory_sha256": source.source_runtime_file_inventory_sha256,
        "source_schema_version": version,
        "admitted": True,
    }
    try:
        return PristineBaseAdmissionV02323.model_validate(
            {**body, "admission_sha256": semantic_sha256_v22(body)}
        )
    except ValueError as error:
        raise ReconstructionContractErrorV02323(
            RECONSTRUCTION_BLOCKER_V02323
        ) from error


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _runtime_inventory_sha256(product_root: Path) -> str:
    entries: list[dict[str, object]] = []
    for name in ("runtime-authority.json", "runtime-readiness.json"):
        path = product_root / "pilot" / name
        if path.is_symlink() or not path.is_file():
            raise ReconstructionContractErrorV02323(RECONSTRUCTION_BLOCKER_V02323)
        entries.append(
            {
                "path": f"pilot/{name}",
                "sha256": _sha256_file(path),
                "size_bytes": path.stat().st_size,
            }
        )
    return semantic_sha256_v22(entries)


def _formal_facts(
    connection: sqlite3.Connection,
) -> dict[str, object]:
    incident_id = "inc-a5a8df708ab77c2f2e19da63"
    job_id = "job-216dd1caac0b92270b1870a2"
    incident = connection.execute(
        "SELECT incident_id FROM incidents WHERE incident_id = ?", (incident_id,)
    ).fetchall()
    job = connection.execute(
        "SELECT status, safe_error_code FROM diagnosis_jobs WHERE job_id = ?", (job_id,)
    ).fetchall()
    diagnosis = connection.execute(
        "SELECT diagnosis_id FROM diagnosis_results WHERE incident_id = ?",
        (incident_id,),
    ).fetchall()
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
        "formal_incident_id": incident_id,
        "formal_incident_present": len(incident) == 1,
        "formal_diagnosis_job_id": job_id,
        "formal_diagnosis_job_present": len(job) == 1,
        "formal_diagnosis_job_status": None if len(job) != 1 else str(job[0][0]),
        "formal_diagnosis_safe_error_code": (
            None if len(job) != 1 else str(job[0][1])
        ),
        "successor_diagnosis_absent": not diagnosis,
        "observed_incident_count": int(
            connection.execute("SELECT COUNT(*) FROM incidents").fetchone()[0]
        ),
        "observed_diagnosis_count": int(
            connection.execute("SELECT COUNT(*) FROM diagnosis_results").fetchone()[0]
        ),
        "fault_family_count": int(
            connection.execute("SELECT COUNT(*) FROM fault_families").fetchone()[0]
        ),
        "knowledge_artifact_count": sum(
            int(
                connection.execute(
                    f"SELECT COUNT(*) FROM {_quoted_identifier(table)}"
                ).fetchone()[0]
            )
            for table in knowledge_tables
        ),
    }


def audit_schema9_contamination_v02323(
    source_product_root: Path,
    definition: Schema8DefinitionV02323,
    schema9_definition: Schema9DefinitionV02323,
    source_projection: Schema8ProjectionExportV02323,
    *,
    reconstructed_projection_sha256: str,
    formal_artifact_bindings: Mapping[str, str],
    source_immutability_proof_sha256: str,
) -> Schema9ContaminationAuditV02323:
    """Classify every observed migration-9 surface on the frozen copy."""

    root = Path(source_product_root).resolve(strict=True)
    database = root / "product.sqlite3"
    reference_by_key = {
        (str(item["type"]), str(item["name"])): item
        for item in definition.reference_schema_inventory
    }
    try:
        with _read_only_connection(database) as connection:
            version = int(
                connection.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[
                    0
                ]
            )
            current_inventory = _schema_inventory(connection)
            current_by_key = {
                (str(item["type"]), str(item["name"])): item
                for item in current_inventory
            }
            extra_keys = set(current_by_key) - set(reference_by_key)
            missing_keys = set(reference_by_key) - set(current_by_key)
            extra_tables = tuple(
                sorted(name for kind, name in extra_keys if kind == "table")
            )
            extra_indexes = tuple(
                sorted(name for kind, name in extra_keys if kind == "index")
            )
            extra_objects = tuple(sorted(f"{kind}:{name}" for kind, name in extra_keys))
            missing = tuple(sorted(f"{kind}:{name}" for kind, name in missing_keys))
            reference_tables = _definition_tables(definition)
            extra_columns: dict[str, tuple[str, ...]] = {}
            for table, reference_columns in sorted(reference_tables.items()):
                current_columns = tuple(
                    str(row[1])
                    for row in connection.execute(
                        f"PRAGMA table_info({_quoted_identifier(table)})"
                    ).fetchall()
                )
                if current_columns[: len(reference_columns)] != reference_columns:
                    extra_columns[table] = current_columns
                elif len(current_columns) > len(reference_columns):
                    extra_columns[table] = current_columns[len(reference_columns) :]
            unexpected_changes = tuple(
                sorted(
                    f"{kind}:{name}"
                    for (kind, name), reference in reference_by_key.items()
                    if (kind, name) in current_by_key
                    and _normalized_schema_sql(
                        current_by_key[(kind, name)].get("sql")
                    )
                    != _normalized_schema_sql(reference.get("sql"))
                    and not (kind == "table" and name == "diagnosis_jobs")
                )
            )
            migration_rows = tuple(
                {
                    "version": int(row[0]),
                    "name": str(row[1]),
                    "applied_at": str(row[2]),
                }
                for row in connection.execute(
                    "SELECT version, name, applied_at FROM schema_migrations "
                    "WHERE version > 8 ORDER BY version"
                ).fetchall()
            )
            new_columns = (
                "failure_stage",
                "exception_fingerprint",
                "journal_tail_sha256",
            )
            non_null_counts = {
                column: int(
                    connection.execute(
                        "SELECT COUNT(*) FROM diagnosis_jobs WHERE "
                        f"{_quoted_identifier(column)} IS NOT NULL"
                    ).fetchone()[0]
                )
                for column in new_columns
            }
            stage_columns = tuple(
                str(row[1])
                for row in connection.execute(
                    "PRAGMA table_info(diagnosis_stage_events_v02322)"
                ).fetchall()
            )
            stage_rows = _canonical_rows(
                connection, "diagnosis_stage_events_v02322", stage_columns
            )
            foreign_keys_clean = not connection.execute(
                "PRAGMA foreign_key_check"
            ).fetchall()
            facts = _formal_facts(connection)
    except sqlite3.Error as error:
        raise ReconstructionContractErrorV02323(
            RECONSTRUCTION_BLOCKER_V02323
        ) from error
    expected_schema = (
        version == 9
        and extra_tables == ("diagnosis_stage_events_v02322",)
        and extra_indexes == ("diagnosis_stage_events_v02322_incident_idx",)
        and extra_columns
        == {
            "diagnosis_jobs": (
                "failure_stage",
                "exception_fingerprint",
                "journal_tail_sha256",
            )
        }
        and not missing
        and not unexpected_changes
        and len(migration_rows) == 1
        and migration_rows[0]["version"] == 9
        and migration_rows[0]["name"] == "product-v02322-diagnosis-stage-journal"
    )
    normalized_source_inventory = _normalized_schema_inventory(current_inventory)
    normalized_expected_inventory = _normalized_schema_inventory(
        schema9_definition.expected_schema_inventory
    )
    inventory_matches = normalized_source_inventory == normalized_expected_inventory
    projection_matches = (
        source_projection.overall_projection_sha256
        == reconstructed_projection_sha256
    )
    if not expected_schema or not inventory_matches or not foreign_keys_clean:
        classification = Schema9ContaminationClassV02323.UNPROVEN
    elif not projection_matches:
        classification = Schema9ContaminationClassV02323.SCHEMA8_ROW_DRIFT_DETECTED
    elif stage_rows or any(non_null_counts.values()):
        classification = (
            Schema9ContaminationClassV02323.ADDITIVE_SCHEMA_AND_JOURNAL_ONLY
        )
    else:
        classification = Schema9ContaminationClassV02323.ADDITIVE_SCHEMA_ONLY
    expected_facts = {
        "formal_incident_id": "inc-a5a8df708ab77c2f2e19da63",
        "formal_incident_present": True,
        "formal_diagnosis_job_id": "job-216dd1caac0b92270b1870a2",
        "formal_diagnosis_job_present": True,
        "formal_diagnosis_job_status": "FAILED",
        "formal_diagnosis_safe_error_code": "INTERNAL_CONTRACT_FAILURE",
        "successor_diagnosis_absent": True,
        "observed_incident_count": 2,
        "observed_diagnosis_count": 1,
        "fault_family_count": 0,
        "knowledge_artifact_count": 0,
    }
    if facts != expected_facts:
        raise ReconstructionContractErrorV02323(RECONSTRUCTION_BLOCKER_V02323)
    reader = ForensicSqliteReaderV02323(database)
    bindings = dict(sorted(formal_artifact_bindings.items()))
    body: dict[str, object] = {
        "schema_version": "ecomsre.product.schema9-contamination-audit.v02323",
        "goal_version": GOAL_VERSION_V02323,
        "terminal": SCHEMA9_CONTAMINATION_AUDIT_PASS_V02323,
        "contamination_class": classification.value,
        "schema8_definition_sha256": definition.schema8_definition_sha256,
        "schema9_definition_sha256": schema9_definition.schema9_definition_sha256,
        "expected_schema9_inventory_sha256": semantic_sha256_v22(
            normalized_expected_inventory
        ),
        "source_schema_inventory_sha256": semantic_sha256_v22(
            normalized_source_inventory
        ),
        "schema9_inventory_matches_expected": inventory_matches,
        "source_schema_version": version,
        "extra_objects": extra_objects,
        "extra_tables": extra_tables,
        "extra_columns": extra_columns,
        "extra_indexes": extra_indexes,
        "missing_schema8_objects": missing,
        "unexpected_schema8_changes": unexpected_changes,
        "schema_migrations_above_8": migration_rows,
        "new_diagnosis_job_column_non_null_counts": non_null_counts,
        "diagnosis_stage_event_count": len(stage_rows),
        "diagnosis_stage_event_rows_sha256": semantic_sha256_v22(stage_rows),
        "source_schema8_projection_sha256": (
            source_projection.overall_projection_sha256
        ),
        "reconstructed_schema8_projection_sha256": (
            reconstructed_projection_sha256
        ),
        "schema8_projection_matches_reconstruction": projection_matches,
        "foreign_key_check_clean": foreign_keys_clean,
        "source_object_inventory_sha256": reader.object_inventory_sha256(root),
        "source_runtime_file_inventory_sha256": _runtime_inventory_sha256(root),
        "formal_artifact_bindings": bindings,
        "tracked_formal_facts": facts,
        "source_immutability_proof_sha256": source_immutability_proof_sha256,
    }
    try:
        return Schema9ContaminationAuditV02323.model_validate(
            {**body, "audit_sha256": semantic_sha256_v22(body)}
        )
    except ValueError as error:
        raise ReconstructionContractErrorV02323(
            RECONSTRUCTION_BLOCKER_V02323
        ) from error


def _topological_table_order(
    definition: Schema8DefinitionV02323,
) -> tuple[str, ...]:
    tables = set(_definition_tables(definition))
    dependencies: dict[str, set[str]] = {table: set() for table in tables}
    for item in definition.reference_schema_inventory:
        if item.get("type") != "table" or item.get("name") not in tables:
            continue
        table = str(item["name"])
        foreign_keys = item.get("foreign_keys")
        if not isinstance(foreign_keys, (tuple, list)):
            continue
        for foreign_key in foreign_keys:
            if isinstance(foreign_key, Mapping):
                target = foreign_key.get("target_table")
                if isinstance(target, str) and target in tables and target != table:
                    dependencies[table].add(target)
    order: list[str] = []
    remaining = {table: set(values) for table, values in dependencies.items()}
    while remaining:
        ready = sorted(table for table, values in remaining.items() if not values)
        if not ready:
            order.extend(sorted(remaining))
            break
        for table in ready:
            order.append(table)
            remaining.pop(table)
        for values in remaining.values():
            values.difference_update(ready)
    return tuple(order)


def _clear_write_bits(root: Path) -> None:
    for path in sorted(root.rglob("*"), key=lambda item: len(item.parts), reverse=True):
        os.chmod(path, path.stat().st_mode & ~0o222, follow_symlinks=False)
    os.chmod(root, root.stat().st_mode & ~0o222, follow_symlinks=False)


def _is_read_only_tree(root: Path) -> bool:
    return all(
        not path.stat().st_mode & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH)
        for path in (root, *root.rglob("*"))
    )


def build_clean_schema8_database_v02323(
    destination_product_root: Path,
    *,
    reconstruction_locator: str,
    definition: Schema8DefinitionV02323,
    formal_delta: FormalProductDeltaV02323,
    source_projection: Schema8ProjectionExportV02323,
    post_rows: Mapping[str, Sequence[Sequence[object]]],
    asset_source_product_root: Path,
) -> Schema8ReconstructionV02323:
    """Build a create-once schema-8 DB from logical rows, never copied pages."""

    destination = Path(destination_product_root)
    if destination.exists() or destination.is_symlink() or Path(reconstruction_locator).is_absolute():
        raise ReconstructionContractErrorV02323(RECONSTRUCTION_BLOCKER_V02323)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.mkdir(mode=0o700)
    completed = False
    database = destination / "product.sqlite3"
    try:
        connection = sqlite3.connect(database, isolation_level=None)
        try:
            _apply_schema(connection, definition.migrations, record_migrations=False)
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("PRAGMA defer_foreign_keys = ON")
            columns_by_table = _definition_tables(definition)
            for table in _topological_table_order(definition):
                columns = columns_by_table[table]
                placeholders = ",".join("?" for _column in columns)
                selected = ",".join(_quoted_identifier(column) for column in columns)
                statement = (
                    f"INSERT INTO {_quoted_identifier(table)} ({selected}) "
                    f"VALUES ({placeholders})"
                )
                for row in post_rows[table]:
                    connection.execute(statement, tuple(row))
            connection.execute("COMMIT")
            foreign_key_check = connection.execute("PRAGMA foreign_key_check").fetchall()
            integrity = str(connection.execute("PRAGMA integrity_check").fetchone()[0])
        except Exception:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
        finally:
            connection.close()
        if foreign_key_check or integrity != "ok":
            raise ReconstructionContractErrorV02323(RECONSTRUCTION_BLOCKER_V02323)
        source_assets = Path(asset_source_product_root).resolve(strict=True)
        source_reader = ForensicSqliteReaderV02323(source_assets / "product.sqlite3")
        source_object_inventory = source_reader.object_inventory_sha256(source_assets)
        source_runtime_inventory = _runtime_inventory_sha256(source_assets)
        shutil.copytree(source_assets / "objects", destination / "objects", copy_function=shutil.copy2)
        shutil.copytree(source_assets / "pilot", destination / "pilot", copy_function=shutil.copy2)
        reconstructed_export, _ = export_schema8_projection_v02323(
            database,
            definition,
            formal_artifact_bindings=source_projection.formal_artifact_bindings,
        )
        with _read_only_connection(database) as readonly:
            version = int(
                readonly.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0]
            )
            schema_inventory = _schema_inventory(readonly)
            logical_sha256 = _logical_database_sha256(readonly)
        if (
            version != 8
            or reconstructed_export.overall_projection_sha256
            != source_projection.overall_projection_sha256
            or schema_inventory != definition.reference_schema_inventory
        ):
            raise ReconstructionContractErrorV02323(RECONSTRUCTION_BLOCKER_V02323)
        reader = ForensicSqliteReaderV02323(database)
        object_inventory = reader.object_inventory_sha256(destination)
        runtime_inventory = _runtime_inventory_sha256(destination)
        semantic_body = {
            "schema8_definition_sha256": definition.schema8_definition_sha256,
            "formal_delta_sha256": formal_delta.delta_sha256,
            "database_logical_sha256": logical_sha256,
            "object_inventory_sha256": object_inventory,
            "runtime_file_inventory_sha256": runtime_inventory,
            "source_projection_sha256": source_projection.overall_projection_sha256,
        }
        raw_sha256 = _sha256_file(database)
        schema_inventory_sha256 = semantic_sha256_v22(schema_inventory)
        _clear_write_bits(destination)
        read_only = _is_read_only_tree(destination)
        body: dict[str, object] = {
            "schema_version": "ecomsre.product.schema8-reconstruction.v02323",
            "reconstruction_locator": reconstruction_locator,
            "schema8_definition_sha256": definition.schema8_definition_sha256,
            "formal_delta_sha256": formal_delta.delta_sha256,
            "source_projection_sha256": source_projection.overall_projection_sha256,
            "reconstructed_projection_sha256": (
                reconstructed_export.overall_projection_sha256
            ),
            "reconstructed_schema_version": version,
            "reconstructed_database_file_sha256": raw_sha256,
            "reconstructed_database_logical_sha256": logical_sha256,
            "source_object_inventory_sha256": source_object_inventory,
            "source_runtime_file_inventory_sha256": source_runtime_inventory,
            "reconstructed_object_inventory_sha256": object_inventory,
            "reconstructed_runtime_file_inventory_sha256": runtime_inventory,
            "reconstructed_product_state_semantic_sha256": semantic_sha256_v22(
                semantic_body
            ),
            "schema_inventory_sha256": schema_inventory_sha256,
            "foreign_key_check_clean": True,
            "integrity_check": integrity,
            "destination_read_only": read_only,
        }
        report = Schema8ReconstructionV02323.model_validate(
            {**body, "reconstruction_sha256": semantic_sha256_v22(body)}
        )
        completed = True
        return report
    except (OSError, sqlite3.Error, ValueError) as error:
        raise ReconstructionContractErrorV02323(
            RECONSTRUCTION_BLOCKER_V02323
        ) from error
    finally:
        if not completed and destination.exists():
            _clear_write_bits(destination)


def inspect_post_formal_state_v02323(
    reconstructed_product_root: Path,
) -> PostFormalProductStateV02323:
    root = Path(reconstructed_product_root).resolve(strict=True)
    try:
        with _read_only_connection(root / "product.sqlite3") as connection:
            jobs = {
                (str(row[0]), str(row[1])): int(row[2])
                for row in connection.execute(
                    "SELECT job_type, status, COUNT(*) FROM diagnosis_jobs "
                    "GROUP BY job_type, status"
                ).fetchall()
            }
            statuses = {
                str(row[0]): int(row[1])
                for row in connection.execute(
                    "SELECT status, COUNT(*) FROM diagnosis_jobs GROUP BY status"
                ).fetchall()
            }
            count = lambda table: int(  # noqa: E731
                connection.execute(
                    f"SELECT COUNT(*) FROM {_quoted_identifier(table)}"
                ).fetchone()[0]
            )
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
            counts = PostFormalProductStateCountsV02323(
                baseline_count=count("baseline_versions"),
                active_baseline_count=int(
                    connection.execute(
                        "SELECT COUNT(*) FROM baseline_versions WHERE active = 1"
                    ).fetchone()[0]
                ),
                baseline_job_count=sum(
                    value
                    for (kind, _status), value in jobs.items()
                    if kind == "BASELINE_BUILD"
                ),
                verify_job_count=sum(
                    value
                    for (kind, _status), value in jobs.items()
                    if kind == "ENVIRONMENT_VERIFY"
                ),
                diagnosis_job_count=sum(
                    value
                    for (kind, _status), value in jobs.items()
                    if kind == "DIAGNOSIS"
                ),
                incident_count=count("incidents"),
                diagnosis_count=count("diagnosis_results"),
                evidence_object_count=count("evidence_objects"),
                failed_job_count=statuses.get("FAILED", 0),
                pending_job_count=statuses.get("PENDING", 0),
                running_job_count=statuses.get("RUNNING", 0),
                fault_family_count=count("fault_families"),
                knowledge_artifact_count=sum(count(table) for table in knowledge_tables),
                diagnosis_evidence_index_count=count("diagnosis_evidence_indexes"),
            )
            active = connection.execute(
                "SELECT baseline_id, environment_id, payload_json "
                "FROM baseline_versions WHERE active = 1"
            ).fetchall()
            if len(active) != 1:
                raise ReconstructionContractErrorV02323(
                    RECONSTRUCTION_BLOCKER_V02323
                )
            baseline_payload = json.loads(str(active[0][2]))
            profile_bindings: list[Mapping[str, object]] = []
            for row in connection.execute(
                "SELECT settings_json FROM connector_configs WHERE kind = 'OPENSEARCH'"
            ).fetchall():
                settings = json.loads(str(row[0]))
                binding = settings.get("profile_binding")
                if (
                    isinstance(binding, Mapping)
                    and binding.get("profile_status") == "ACTIVE"
                    and binding.get("selected_candidate_alias") == "P01"
                ):
                    profile_bindings.append(binding)
            if len(profile_bindings) != 1:
                raise ReconstructionContractErrorV02323(
                    RECONSTRUCTION_BLOCKER_V02323
                )
            profile_sha256 = profile_bindings[0].get("profile_sha256")
            successor_diagnosis_absent = not connection.execute(
                "SELECT 1 FROM diagnosis_results WHERE incident_id = ?",
                ("inc-a5a8df708ab77c2f2e19da63",),
            ).fetchall()
    except (sqlite3.Error, json.JSONDecodeError) as error:
        raise ReconstructionContractErrorV02323(
            RECONSTRUCTION_BLOCKER_V02323
        ) from error
    body: dict[str, object] = {
        "schema_version": "ecomsre.product.post-formal-state.v02323",
        "counts": counts.model_dump(mode="json"),
        "environment_id": str(active[0][1]),
        "active_baseline_id": str(active[0][0]),
        "active_baseline_sha256": baseline_payload.get("baseline_sha256"),
        "active_p01_profile_sha256": profile_sha256,
        "formal_incident_id": "inc-a5a8df708ab77c2f2e19da63",
        "failed_diagnosis_job_id": "job-216dd1caac0b92270b1870a2",
        "successor_diagnosis_absent": successor_diagnosis_absent,
        "historical_raw_byte_authority": "LOST_RAW_BYTES_NOT_RECONSTRUCTED",
        "historical_logical_authority": "PRISTINE_BASE_DELTA_RECONSTRUCTION",
        "replay_authority": "NOT_EXECUTED",
        "measured_nofault_authority": "NONE",
        "knowledge_loop_authority": "NONE",
    }
    try:
        return PostFormalProductStateV02323.model_validate(
            {**body, "state_sha256": semantic_sha256_v22(body)}
        )
    except ValueError as error:
        raise ReconstructionContractErrorV02323(
            RECONSTRUCTION_BLOCKER_V02323
        ) from error


def freeze_reconstruction_disposition_v02323(
    *,
    admission: PristineBaseAdmissionV02323,
    delta: FormalProductDeltaV02323,
    projection: Schema8ProjectionExportV02323,
    reconstruction: Schema8ReconstructionV02323,
    post_formal_state: PostFormalProductStateV02323,
    contamination: Schema9ContaminationAuditV02323,
) -> ReconstructionDispositionV02323:
    if (
        delta.post_formal_projection_sha256
        != projection.overall_projection_sha256
        or reconstruction.reconstructed_projection_sha256
        != projection.overall_projection_sha256
        or reconstruction.formal_delta_sha256 != delta.delta_sha256
        or contamination.source_object_inventory_sha256
        != reconstruction.source_object_inventory_sha256
        or contamination.source_runtime_file_inventory_sha256
        != reconstruction.source_runtime_file_inventory_sha256
    ):
        raise ReconstructionContractErrorV02323(RECONSTRUCTION_BLOCKER_V02323)
    body: dict[str, object] = {
        "schema_version": "ecomsre.product.reconstruction-disposition.v02323",
        "goal_version": GOAL_VERSION_V02323,
        "terminal": RECONSTRUCTION_DISPOSITION_FROZEN_V02323,
        "reconstruction_terminal": PRISTINE_BASE_DELTA_RECONSTRUCTION_PASS_V02323,
        "disposition": "PRISTINE_BASE_DELTA_RECONSTRUCTION",
        "pristine_base_admission_sha256": admission.admission_sha256,
        "formal_delta_sha256": delta.delta_sha256,
        "schema9_contamination_audit_sha256": contamination.audit_sha256,
        "schema8_projection_export_sha256": projection.export_sha256,
        "reconstruction_sha256": reconstruction.reconstruction_sha256,
        "post_formal_state_sha256": post_formal_state.state_sha256,
        "historical_raw_byte_authority": "LOST_RAW_BYTES_NOT_RECONSTRUCTED",
        "historical_logical_authority": "PRISTINE_BASE_DELTA_RECONSTRUCTION",
        "replay_authority": "NOT_EXECUTED",
        "measured_nofault_authority": "NONE",
        "knowledge_loop_authority": "NONE",
        "raw_byte_equality_claimed": False,
        "diagnosis_persistence_replay_attempt_count": 0,
    }
    try:
        return ReconstructionDispositionV02323.model_validate(
            {**body, "disposition_sha256": semantic_sha256_v22(body)}
        )
    except ValueError as error:
        raise ReconstructionContractErrorV02323(
            RECONSTRUCTION_BLOCKER_V02323
        ) from error


def verify_schema8_reconstruction_v02323(
    reconstructed_product_root: Path,
    *,
    definition: Schema8DefinitionV02323,
    projection: Schema8ProjectionExportV02323,
    reconstruction: Schema8ReconstructionV02323,
) -> str:
    root = Path(reconstructed_product_root).resolve(strict=True)
    if not _is_read_only_tree(root):
        raise ReconstructionContractErrorV02323(RECONSTRUCTION_BLOCKER_V02323)
    database = root / "product.sqlite3"
    export, _rows = export_schema8_projection_v02323(
        database,
        definition,
        formal_artifact_bindings=projection.formal_artifact_bindings,
    )
    with _read_only_connection(database) as connection:
        version = int(
            connection.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0]
        )
        foreign_keys_clean = not connection.execute("PRAGMA foreign_key_check").fetchall()
        integrity = str(connection.execute("PRAGMA integrity_check").fetchone()[0])
        inventory = _schema_inventory(connection)
        logical = _logical_database_sha256(connection)
    reader = ForensicSqliteReaderV02323(database)
    observed = {
        "projection": export.overall_projection_sha256,
        "version": version,
        "foreign_keys_clean": foreign_keys_clean,
        "integrity": integrity,
        "schema_inventory_sha256": semantic_sha256_v22(inventory),
        "database_file_sha256": _sha256_file(database),
        "database_logical_sha256": logical,
        "object_inventory_sha256": reader.object_inventory_sha256(root),
        "runtime_inventory_sha256": _runtime_inventory_sha256(root),
    }
    expected = {
        "projection": reconstruction.reconstructed_projection_sha256,
        "version": 8,
        "foreign_keys_clean": True,
        "integrity": "ok",
        "schema_inventory_sha256": reconstruction.schema_inventory_sha256,
        "database_file_sha256": reconstruction.reconstructed_database_file_sha256,
        "database_logical_sha256": reconstruction.reconstructed_database_logical_sha256,
        "object_inventory_sha256": reconstruction.reconstructed_object_inventory_sha256,
        "runtime_inventory_sha256": reconstruction.reconstructed_runtime_file_inventory_sha256,
    }
    if observed != expected or export.overall_projection_sha256 != projection.overall_projection_sha256:
        raise ReconstructionContractErrorV02323(RECONSTRUCTION_BLOCKER_V02323)
    return semantic_sha256_v22(
        {
            "reconstruction_sha256": reconstruction.reconstruction_sha256,
            "observed": observed,
            "read_only": True,
        }
    )


__all__ = (
    "EXACT_RECONSTRUCTION_NOT_AVAILABLE_V02323",
    "FormalProductDeltaV02323",
    "FormalRowDeltaV02323",
    "PostFormalProductStateCountsV02323",
    "PostFormalProductStateV02323",
    "PR83_HEAD_V02323",
    "PR83_MIGRATIONS_BLOB_V02323",
    "PRISTINE_BASE_DELTA_RECONSTRUCTION_PASS_V02323",
    "PristineBaseAdmissionV02323",
    "RECONSTRUCTION_DISPOSITION_FROZEN_V02323",
    "ReconstructionDispositionV02323",
    "ReconstructionContractErrorV02323",
    "SCHEMA8_PROJECTION_RECONSTRUCTION_PASS_V02323",
    "SCHEMA9_CONTAMINATION_AUDIT_PASS_V02323",
    "Schema8DefinitionV02323",
    "Schema8MigrationV02323",
    "Schema8ProjectionExportV02323",
    "Schema8ProjectionTableV02323",
    "Schema8ReconstructionV02323",
    "Schema9ContaminationAuditV02323",
    "Schema9ContaminationClassV02323",
    "admit_pristine_base_v02323",
    "audit_schema9_contamination_v02323",
    "build_clean_schema8_database_v02323",
    "build_formal_product_delta_v02323",
    "export_schema8_projection_v02323",
    "freeze_reconstruction_disposition_v02323",
    "inspect_post_formal_state_v02323",
    "load_schema8_definition_v02323",
    "verify_schema8_reconstruction_v02323",
)
