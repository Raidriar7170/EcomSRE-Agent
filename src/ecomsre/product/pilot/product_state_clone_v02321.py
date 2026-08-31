"""Fresh preflight Product-state clone contract for Product v0.2.3.2.1."""

from __future__ import annotations

import json
from pathlib import Path
import sqlite3
from typing import Any, Literal

from pydantic import ConfigDict, Field, model_validator

from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22
from ecomsre.product.contracts import ProductModelV1
from ecomsre.product.pilot.product_state_clone_v0232 import (
    ProductStateCloneErrorV0232,
    ProductStateCloneV0232,
    ProductStateSourceV0232,
    _active_bindings,
    _logical_database_sha256,
    _object_inventory,
    _read_only_connection,
    _require_no_sqlite_sidecars,
    _require_regular_tree,
    _runtime_file_inventory,
    _sha256_file,
)


PREFLIGHT_STATE_CLONE_PASS_V02321 = "ECOMSRE_PRODUCT_V02321_PREFLIGHT_STATE_CLONE_PASS"
FORMAL_STATE_CLONE_PASS_V02321: Literal[
    "ECOMSRE_PRODUCT_V02321_FORMAL_STATE_CLONE_PASS"
] = "ECOMSRE_PRODUCT_V02321_FORMAL_STATE_CLONE_PASS"
_SHA256_PATTERN = r"^[0-9a-f]{64}$"


class FormalProductStateCountsV02321(ProductModelV1):
    """Exact database cardinality after the single successor Product episode."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    baseline_count: int = Field(ge=0)
    active_baseline_count: int = Field(ge=0)
    baseline_job_count: int = Field(ge=0)
    verify_job_count: int = Field(ge=0)
    diagnosis_job_count: int = Field(ge=0)
    incident_count: int = Field(ge=0)
    diagnosis_count: int = Field(ge=0)
    evidence_object_count: int = Field(ge=1)
    fault_family_count: int = Field(ge=0)
    knowledge_artifact_count: int = Field(ge=0)
    pending_job_count: int = Field(ge=0)
    running_job_count: int = Field(ge=0)
    failed_job_count: int = Field(ge=0)

    @model_validator(mode="after")
    def require_exact_formal_counts(self) -> "FormalProductStateCountsV02321":
        expected = {
            "baseline_count": 1,
            "active_baseline_count": 1,
            "baseline_job_count": 1,
            "verify_job_count": 1,
            "diagnosis_job_count": 2,
            "incident_count": 2,
            "diagnosis_count": 2,
            "fault_family_count": 0,
            "knowledge_artifact_count": 0,
            "pending_job_count": 0,
            "running_job_count": 0,
            "failed_job_count": 0,
        }
        if any(getattr(self, key) != value for key, value in expected.items()):
            raise ValueError("Product v0.2.3.2.1 formal poststate counts differ")
        return self


class FormalProductPoststateV02321(ProductModelV1):
    """Read-only, self-sealed final state after the one authorized diagnosis."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["ecomsre.product.formal-product-poststate.v02321"] = (
        "ecomsre.product.formal-product-poststate.v02321"
    )
    state_locator: str = Field(
        pattern=r"^\.local/product-v02321/product-state/formal-[0-9a-f]{24}/product$"
    )
    database_file_sha256: str = Field(pattern=_SHA256_PATTERN)
    database_logical_sha256: str = Field(pattern=_SHA256_PATTERN)
    object_inventory_sha256: str = Field(pattern=_SHA256_PATTERN)
    runtime_file_inventory_sha256: str = Field(pattern=_SHA256_PATTERN)
    counts: FormalProductStateCountsV02321
    environment_id: str
    active_baseline_id: str
    active_baseline_sha256: str = Field(pattern=_SHA256_PATTERN)
    profile_sha256: str = Field(pattern=_SHA256_PATTERN)
    poststate_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def require_self_seal(self) -> "FormalProductPoststateV02321":
        body = self.model_dump(mode="json", exclude={"poststate_sha256"})
        if self.poststate_sha256 != semantic_sha256_v22(body):
            raise ValueError("Product v0.2.3.2.1 formal poststate digest differs")
        return self

    @classmethod
    def build(cls, **payload: Any) -> "FormalProductPoststateV02321":
        body = {
            "schema_version": "ecomsre.product.formal-product-poststate.v02321",
            **payload,
        }
        return cls.model_validate(
            {**body, "poststate_sha256": semantic_sha256_v22(body)}
        )


def _formal_state_counts_v02321(
    connection: sqlite3.Connection,
) -> FormalProductStateCountsV02321:
    def count(table: str) -> int:
        if not table.replace("_", "").isalnum():
            raise ProductStateCloneErrorV0232("Product-state table name differs")
        return int(connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])

    jobs = {
        (str(row["job_type"]), str(row["status"])): int(row["count"])
        for row in connection.execute(
            "SELECT job_type, status, COUNT(*) AS count FROM diagnosis_jobs "
            "GROUP BY job_type, status"
        ).fetchall()
    }
    by_status = {
        str(row["status"]): int(row["count"])
        for row in connection.execute(
            "SELECT status, COUNT(*) AS count FROM diagnosis_jobs GROUP BY status"
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
    return FormalProductStateCountsV02321.model_validate(
        {
            "baseline_count": count("baseline_versions"),
            "active_baseline_count": int(
                connection.execute(
                    "SELECT COUNT(*) FROM baseline_versions WHERE active = 1"
                ).fetchone()[0]
            ),
            "baseline_job_count": sum(
                value
                for (kind, _status), value in jobs.items()
                if kind == "BASELINE_BUILD"
            ),
            "verify_job_count": sum(
                value
                for (kind, _status), value in jobs.items()
                if kind == "ENVIRONMENT_VERIFY"
            ),
            "diagnosis_job_count": sum(
                value for (kind, _status), value in jobs.items() if kind == "DIAGNOSIS"
            ),
            "incident_count": count("incidents"),
            "diagnosis_count": count("diagnosis_results"),
            "evidence_object_count": count("evidence_objects"),
            "fault_family_count": count("fault_families"),
            "knowledge_artifact_count": sum(count(table) for table in knowledge_tables),
            "pending_job_count": by_status.get("PENDING", 0),
            "running_job_count": by_status.get("RUNNING", 0),
            "failed_job_count": by_status.get("FAILED", 0),
        }
    )


def admit_formal_product_poststate_v02321(
    state_root: Path,
    *,
    state_locator: str,
    expected_environment_id: str,
    expected_baseline_id: str,
    expected_baseline_sha256: str,
    expected_profile_sha256: str,
    expected_pilot_runtime_authority_sha256: str,
    expected_runtime_connector_binding_sha256: str,
) -> FormalProductPoststateV02321:
    """Inspect the mutated clone without weakening the frozen 1 / 1 source model."""

    root = Path(state_root).expanduser()
    if not root.is_absolute():
        root = root.absolute()
    _require_regular_tree(root)
    _require_no_sqlite_sidecars(root)
    database = root / "product.sqlite3"
    if not database.is_file() or database.is_symlink():
        raise ProductStateCloneErrorV0232("Product-state SQLite database is missing")
    try:
        with _read_only_connection(database) as connection:
            database_logical_sha256 = _logical_database_sha256(connection)
            counts = _formal_state_counts_v02321(connection)
            environment_id, baseline_id, baseline_sha256, profile_sha256 = (
                _active_bindings(connection)
            )
            object_inventory_sha256 = _object_inventory(root, connection)
    except (sqlite3.Error, json.JSONDecodeError, ValueError) as error:
        raise ProductStateCloneErrorV0232(
            "Product v0.2.3.2.1 formal poststate admission failed"
        ) from error
    expected = (
        ("environment", environment_id, expected_environment_id),
        ("Baseline ID", baseline_id, expected_baseline_id),
        ("Baseline SHA", baseline_sha256, expected_baseline_sha256),
        ("P01 profile SHA", profile_sha256, expected_profile_sha256),
    )
    if any(actual != required for _label, actual, required in expected):
        differences = ",".join(
            label for label, actual, required in expected if actual != required
        )
        raise ProductStateCloneErrorV0232(
            "Product v0.2.3.2.1 formal poststate binding differs: " + differences
        )
    return FormalProductPoststateV02321.build(
        state_locator=state_locator,
        database_file_sha256=_sha256_file(database),
        database_logical_sha256=database_logical_sha256,
        object_inventory_sha256=object_inventory_sha256,
        runtime_file_inventory_sha256=_runtime_file_inventory(
            root,
            expected_environment_id=environment_id,
            expected_pilot_runtime_authority_sha256=(
                expected_pilot_runtime_authority_sha256
            ),
            expected_runtime_connector_binding_sha256=(
                expected_runtime_connector_binding_sha256
            ),
        ),
        counts=counts.model_dump(mode="json"),
        environment_id=environment_id,
        active_baseline_id=baseline_id,
        active_baseline_sha256=baseline_sha256,
        profile_sha256=profile_sha256,
    )


class PreflightStateCloneReportV02321(ProductModelV1):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["ecomsre.product.preflight-state-clone.v02321"] = (
        "ecomsre.product.preflight-state-clone.v02321"
    )
    terminal: Literal["ECOMSRE_PRODUCT_V02321_PREFLIGHT_STATE_CLONE_PASS"] = (
        "ECOMSRE_PRODUCT_V02321_PREFLIGHT_STATE_CLONE_PASS"
    )
    role: Literal["PREFLIGHT"] = "PREFLIGHT"
    source_repository_binding: dict[str, Any]
    predecessor_private_acceptance: dict[str, Any]
    source_state: ProductStateSourceV0232
    clone: ProductStateCloneV0232
    destination_state: ProductStateSourceV0232
    destination_locator: str = Field(
        pattern=r"^\.local/product-v02321/product-state/preflight-[0-9a-f]{24}/product$"
    )
    source_incident_count: Literal[1]
    source_diagnosis_count: Literal[1]
    fault_family_count: Literal[0]
    knowledge_artifact_count: Literal[0]
    report_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def require_exact_preflight_clone(self) -> "PreflightStateCloneReportV02321":
        if (
            self.clone.destination_locator != self.destination_locator
            or self.destination_locator
            != (
                ".local/product-v02321/product-state/"
                f"preflight-{self.source_state.source_sha256[:24]}/product"
            )
            or self.destination_state.source_locator != self.destination_locator
            or self.clone.source_locator != self.source_state.source_locator
            or self.clone.source_database_file_sha256_before
            != self.source_state.source_database_file_sha256
            or self.clone.source_database_file_sha256_after
            != self.source_state.source_database_file_sha256
            or self.clone.source_database_logical_sha256
            != self.source_state.source_database_logical_sha256
            or self.clone.source_object_inventory_sha256
            != self.source_state.source_object_inventory_sha256
            or self.clone.source_runtime_file_inventory_sha256
            != self.source_state.source_runtime_file_inventory_sha256
            or self.clone.source_counts != self.source_state.source_counts
            or self.clone.source_environment_id
            != self.source_state.source_environment_id
            or self.clone.source_active_baseline_id
            != self.source_state.source_active_baseline_id
            or self.clone.source_active_baseline_sha256
            != self.source_state.source_active_baseline_sha256
            or self.clone.source_profile_sha256
            != self.source_state.source_profile_sha256
            or self.clone.destination_database_logical_sha256
            != self.destination_state.source_database_logical_sha256
            or self.clone.destination_object_inventory_sha256
            != self.destination_state.source_object_inventory_sha256
            or self.clone.destination_runtime_file_inventory_sha256
            != self.destination_state.source_runtime_file_inventory_sha256
            or self.clone.destination_counts != self.destination_state.source_counts
            or self.clone.destination_environment_id
            != self.destination_state.source_environment_id
            or self.clone.destination_active_baseline_id
            != self.destination_state.source_active_baseline_id
            or self.clone.destination_active_baseline_sha256
            != self.destination_state.source_active_baseline_sha256
            or self.clone.destination_profile_sha256
            != self.destination_state.source_profile_sha256
            or self.source_incident_count
            != self.source_state.source_counts.incident_count
            or self.source_diagnosis_count
            != self.source_state.source_counts.diagnosis_count
            or self.fault_family_count
            != self.source_state.source_counts.fault_family_count
            or self.knowledge_artifact_count
            != self.source_state.source_counts.knowledge_artifact_count
        ):
            raise ValueError("Product v0.2.3.2.1 preflight clone binding differs")
        body = self.model_dump(mode="json", exclude={"report_sha256"})
        if self.report_sha256 != semantic_sha256_v22(body):
            raise ValueError("Product v0.2.3.2.1 preflight clone digest differs")
        return self

    @classmethod
    def build(cls, **payload: Any) -> "PreflightStateCloneReportV02321":
        source = ProductStateSourceV0232.model_validate(payload["source_state"])
        clone = ProductStateCloneV0232.model_validate(payload["clone"])
        destination = ProductStateSourceV0232.model_validate(
            payload["destination_state"]
        )
        body = {
            "schema_version": "ecomsre.product.preflight-state-clone.v02321",
            "terminal": PREFLIGHT_STATE_CLONE_PASS_V02321,
            "role": "PREFLIGHT",
            **payload,
            "source_state": source.model_dump(mode="json"),
            "clone": clone.model_dump(mode="json"),
            "destination_state": destination.model_dump(mode="json"),
        }
        return cls.model_validate({**body, "report_sha256": semantic_sha256_v22(body)})


class FormalStateCloneReportV02321(ProductModelV1):
    """Exact fresh clone admitted only after the formal review gate passes."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["ecomsre.product.formal-state-clone.v02321"] = (
        "ecomsre.product.formal-state-clone.v02321"
    )
    terminal: Literal["ECOMSRE_PRODUCT_V02321_FORMAL_STATE_CLONE_PASS"] = (
        FORMAL_STATE_CLONE_PASS_V02321
    )
    role: Literal["FORMAL"] = "FORMAL"
    formal_admission_sha256: str = Field(pattern=_SHA256_PATTERN)
    formal_clone_plan_sha256: str = Field(pattern=_SHA256_PATTERN)
    source_repository_binding: dict[str, Any]
    predecessor_private_acceptance: dict[str, Any]
    source_state: ProductStateSourceV0232
    clone: ProductStateCloneV0232
    destination_state: ProductStateSourceV0232
    destination_locator: str = Field(
        pattern=r"^\.local/product-v02321/product-state/formal-[0-9a-f]{24}/product$"
    )
    starting_incident_count: Literal[1] = 1
    starting_diagnosis_count: Literal[1] = 1
    starting_fault_family_count: Literal[0] = 0
    starting_knowledge_artifact_count: Literal[0] = 0
    report_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def require_exact_formal_clone(self) -> "FormalStateCloneReportV02321":
        expected_locator = (
            ".local/product-v02321/product-state/"
            f"formal-{self.source_state.source_sha256[:24]}/product"
        )
        if (
            self.destination_locator != expected_locator
            or self.clone.destination_locator != self.destination_locator
            or self.destination_state.source_locator != self.destination_locator
            or self.clone.source_locator != self.source_state.source_locator
            or self.clone.source_database_file_sha256_before
            != self.source_state.source_database_file_sha256
            or self.clone.source_database_file_sha256_after
            != self.source_state.source_database_file_sha256
            or self.clone.source_database_logical_sha256
            != self.source_state.source_database_logical_sha256
            or self.clone.source_object_inventory_sha256
            != self.source_state.source_object_inventory_sha256
            or self.clone.source_runtime_file_inventory_sha256
            != self.source_state.source_runtime_file_inventory_sha256
            or self.clone.source_counts != self.source_state.source_counts
            or self.clone.source_environment_id
            != self.source_state.source_environment_id
            or self.clone.source_active_baseline_id
            != self.source_state.source_active_baseline_id
            or self.clone.source_active_baseline_sha256
            != self.source_state.source_active_baseline_sha256
            or self.clone.source_profile_sha256
            != self.source_state.source_profile_sha256
            or self.clone.destination_database_logical_sha256
            != self.destination_state.source_database_logical_sha256
            or self.clone.destination_object_inventory_sha256
            != self.destination_state.source_object_inventory_sha256
            or self.clone.destination_runtime_file_inventory_sha256
            != self.destination_state.source_runtime_file_inventory_sha256
            or self.clone.destination_counts != self.destination_state.source_counts
            or self.clone.destination_environment_id
            != self.destination_state.source_environment_id
            or self.clone.destination_active_baseline_id
            != self.destination_state.source_active_baseline_id
            or self.clone.destination_active_baseline_sha256
            != self.destination_state.source_active_baseline_sha256
            or self.clone.destination_profile_sha256
            != self.destination_state.source_profile_sha256
            or self.source_state.source_counts.incident_count
            != self.starting_incident_count
            or self.source_state.source_counts.diagnosis_count
            != self.starting_diagnosis_count
            or self.source_state.source_counts.fault_family_count
            != self.starting_fault_family_count
            or self.source_state.source_counts.knowledge_artifact_count
            != self.starting_knowledge_artifact_count
            or self.destination_state.source_counts.incident_count
            != self.starting_incident_count
            or self.destination_state.source_counts.diagnosis_count
            != self.starting_diagnosis_count
            or self.destination_state.source_counts.fault_family_count
            != self.starting_fault_family_count
            or self.destination_state.source_counts.knowledge_artifact_count
            != self.starting_knowledge_artifact_count
        ):
            raise ValueError("Product v0.2.3.2.1 formal clone binding differs")
        body = self.model_dump(mode="json", exclude={"report_sha256"})
        if self.report_sha256 != semantic_sha256_v22(body):
            raise ValueError("Product v0.2.3.2.1 formal clone digest differs")
        return self

    @classmethod
    def build(cls, **payload: Any) -> "FormalStateCloneReportV02321":
        source = ProductStateSourceV0232.model_validate(payload["source_state"])
        clone = ProductStateCloneV0232.model_validate(payload["clone"])
        destination = ProductStateSourceV0232.model_validate(
            payload["destination_state"]
        )
        body = {
            "schema_version": "ecomsre.product.formal-state-clone.v02321",
            "terminal": FORMAL_STATE_CLONE_PASS_V02321,
            "role": "FORMAL",
            **payload,
            "source_state": source.model_dump(mode="json"),
            "clone": clone.model_dump(mode="json"),
            "destination_state": destination.model_dump(mode="json"),
            "starting_incident_count": 1,
            "starting_diagnosis_count": 1,
            "starting_fault_family_count": 0,
            "starting_knowledge_artifact_count": 0,
        }
        return cls.model_validate({**body, "report_sha256": semantic_sha256_v22(body)})


__all__ = (
    "FORMAL_STATE_CLONE_PASS_V02321",
    "PREFLIGHT_STATE_CLONE_PASS_V02321",
    "FormalProductPoststateV02321",
    "FormalProductStateCountsV02321",
    "FormalStateCloneReportV02321",
    "PreflightStateCloneReportV02321",
    "admit_formal_product_poststate_v02321",
)
