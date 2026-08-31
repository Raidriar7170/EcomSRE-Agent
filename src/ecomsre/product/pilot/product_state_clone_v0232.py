"""Fail-closed admission and isolated cloning of the v0.2.3.1 Product state."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import base64
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sqlite3
import tempfile
from typing import Any, Iterator, Literal

from pydantic import Field, model_validator

from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22
from ecomsre.product.connectors.pilot_runtime import PilotRuntimeSnapshotV02
from ecomsre.product.contracts import ProductModelV1
from ecomsre.product.pilot.runtime_authority_v02 import PilotRuntimeAuthorityV02


HISTORY_AND_STATE_PASS_V0232 = "ECOMSRE_PRODUCT_V0232_HISTORY_AND_STATE_PASS"
_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_OBJECT_NAME = re.compile(r"^(?P<sha>[0-9a-f]{64})\.json$")
_PILOT_FILES = ("runtime-authority.json", "runtime-readiness.json")


class ProductStateCloneErrorV0232(RuntimeError):
    """The preserved Product state cannot be admitted or cloned exactly."""


class ProductStateCountsV0232(ProductModelV1):
    baseline_count: int = Field(ge=0)
    active_baseline_count: int = Field(ge=0)
    baseline_job_count: int = Field(ge=0)
    verify_job_count: int = Field(ge=0)
    diagnosis_job_count: int = Field(ge=0)
    incident_count: int = Field(ge=0)
    diagnosis_count: int = Field(ge=0)
    evidence_object_count: int = Field(ge=0)
    fault_family_count: int = Field(ge=0)
    knowledge_artifact_count: int = Field(ge=0)
    pending_job_count: int = Field(ge=0)
    running_job_count: int = Field(ge=0)
    failed_job_count: int = Field(ge=0)

    @model_validator(mode="after")
    def require_frozen_starting_counts(self) -> "ProductStateCountsV0232":
        expected = {
            "baseline_count": 1,
            "active_baseline_count": 1,
            "baseline_job_count": 1,
            "verify_job_count": 1,
            "diagnosis_job_count": 1,
            "incident_count": 1,
            "diagnosis_count": 1,
            "fault_family_count": 0,
            "knowledge_artifact_count": 0,
            "pending_job_count": 0,
            "running_job_count": 0,
            "failed_job_count": 0,
        }
        if any(getattr(self, key) != value for key, value in expected.items()):
            raise ValueError("Product v0.2.3.2 starting counts differ")
        if self.evidence_object_count < 1:
            raise ValueError("Product v0.2.3.2 starting Evidence objects are missing")
        return self


class ProductStateSourceV0232(ProductModelV1):
    schema_version: Literal[
        "ecomsre.product.product-state-source.v0232"
    ] = "ecomsre.product.product-state-source.v0232"
    source_locator: str
    source_database_file_sha256: str = Field(pattern=_SHA256_PATTERN)
    source_database_logical_sha256: str = Field(pattern=_SHA256_PATTERN)
    source_object_inventory_sha256: str = Field(pattern=_SHA256_PATTERN)
    source_runtime_file_inventory_sha256: str = Field(pattern=_SHA256_PATTERN)
    source_counts: ProductStateCountsV0232
    source_environment_id: str
    source_active_baseline_id: str
    source_active_baseline_sha256: str = Field(pattern=_SHA256_PATTERN)
    source_profile_sha256: str = Field(pattern=_SHA256_PATTERN)
    source_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def require_self_seal(self) -> "ProductStateSourceV0232":
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"source_sha256"})
        )
        if self.source_sha256 != expected:
            raise ValueError("Product v0.2.3.2 source-state digest differs")
        return self


class ProductStateCloneV0232(ProductModelV1):
    schema_version: Literal[
        "ecomsre.product.product-state-clone.v0232"
    ] = "ecomsre.product.product-state-clone.v0232"
    terminal: Literal[
        "ECOMSRE_PRODUCT_V0232_HISTORY_AND_STATE_PASS"
    ] = "ECOMSRE_PRODUCT_V0232_HISTORY_AND_STATE_PASS"
    source_locator: str
    source_database_file_sha256_before: str = Field(pattern=_SHA256_PATTERN)
    source_database_file_sha256_after: str = Field(pattern=_SHA256_PATTERN)
    source_database_logical_sha256: str = Field(pattern=_SHA256_PATTERN)
    source_object_inventory_sha256: str = Field(pattern=_SHA256_PATTERN)
    source_runtime_file_inventory_sha256: str = Field(pattern=_SHA256_PATTERN)
    source_counts: ProductStateCountsV0232
    source_environment_id: str
    source_active_baseline_id: str
    source_active_baseline_sha256: str = Field(pattern=_SHA256_PATTERN)
    source_profile_sha256: str = Field(pattern=_SHA256_PATTERN)
    destination_locator: str
    destination_database_logical_sha256: str = Field(pattern=_SHA256_PATTERN)
    destination_object_inventory_sha256: str = Field(pattern=_SHA256_PATTERN)
    destination_runtime_file_inventory_sha256: str = Field(pattern=_SHA256_PATTERN)
    destination_counts: ProductStateCountsV0232
    destination_environment_id: str
    destination_active_baseline_id: str
    destination_active_baseline_sha256: str = Field(pattern=_SHA256_PATTERN)
    destination_profile_sha256: str = Field(pattern=_SHA256_PATTERN)
    clone_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def require_exact_clone(self) -> "ProductStateCloneV0232":
        if self.source_database_file_sha256_before != self.source_database_file_sha256_after:
            raise ValueError("Product v0.2.3.2 source state changed during clone")
        equal_pairs = (
            (self.source_database_logical_sha256, self.destination_database_logical_sha256),
            (self.source_object_inventory_sha256, self.destination_object_inventory_sha256),
            (
                self.source_runtime_file_inventory_sha256,
                self.destination_runtime_file_inventory_sha256,
            ),
            (self.source_counts, self.destination_counts),
            (self.source_environment_id, self.destination_environment_id),
            (self.source_active_baseline_id, self.destination_active_baseline_id),
            (
                self.source_active_baseline_sha256,
                self.destination_active_baseline_sha256,
            ),
            (self.source_profile_sha256, self.destination_profile_sha256),
        )
        if any(source != destination for source, destination in equal_pairs):
            raise ValueError("Product v0.2.3.2 cloned state differs from source")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"clone_sha256"})
        )
        if self.clone_sha256 != expected:
            raise ValueError("Product v0.2.3.2 clone digest differs")
        return self


@dataclass(frozen=True)
class _StateInspectionV0232:
    database_file_sha256: str
    database_logical_sha256: str
    object_inventory_sha256: str
    runtime_file_inventory_sha256: str
    counts: ProductStateCountsV0232
    environment_id: str
    active_baseline_id: str
    active_baseline_sha256: str
    profile_sha256: str


def _require_relative_locator(locator: str) -> str:
    candidate = PurePosixPath(locator)
    if (
        not locator
        or candidate.is_absolute()
        or ".." in candidate.parts
        or "." in candidate.parts
        or "\\" in locator
    ):
        raise ProductStateCloneErrorV0232("Product-state locator must be relative")
    return locator


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _require_regular_tree(root: Path) -> None:
    current = Path(root.anchor)
    for component in root.parts[1:]:
        current /= component
        if current.is_symlink():
            raise ProductStateCloneErrorV0232(
                f"Product-state source path contains a symlink: {current}"
            )
    if root.is_symlink():
        raise ProductStateCloneErrorV0232("Product-state source root is a symlink")
    if not root.is_dir():
        raise ProductStateCloneErrorV0232("Product-state source root is missing")
    for path in root.rglob("*"):
        if path.is_symlink():
            raise ProductStateCloneErrorV0232(
                f"Product-state source contains a symlink: {path.relative_to(root)}"
            )
        if not path.is_dir() and not path.is_file():
            raise ProductStateCloneErrorV0232(
                f"Product-state source contains a non-regular entry: {path.relative_to(root)}"
            )


def _require_no_sqlite_sidecars(root: Path) -> None:
    sidecars = sorted(
        path.name
        for path in root.glob("product.sqlite3-*")
        if path.name != "product.sqlite3"
    )
    journal = root / "product.sqlite3-journal"
    if journal.exists():
        sidecars.append(journal.name)
    if sidecars:
        raise ProductStateCloneErrorV0232(
            "Product-state SQLite sidecar set is not sealed: " + ",".join(sidecars)
        )


@contextmanager
def _read_only_connection(path: Path) -> Iterator[sqlite3.Connection]:
    uri = f"file:{path.as_posix()}?mode=ro&immutable=1"
    connection = sqlite3.connect(uri, uri=True, isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only = ON")
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        yield connection
    finally:
        connection.close()


def _normalized_sqlite_value(value: object) -> object:
    if isinstance(value, bytes):
        return {"base64": base64.b64encode(value).decode("ascii")}
    if value is None or isinstance(value, (str, int, float)):
        return value
    raise ProductStateCloneErrorV0232("Product-state SQLite value type is unsupported")


def _quoted_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _logical_database_sha256(connection: sqlite3.Connection) -> str:
    integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
    if integrity != "ok":
        raise ProductStateCloneErrorV0232("Product-state SQLite integrity check failed")
    schema_rows = connection.execute(
        "SELECT type, name, tbl_name, sql FROM sqlite_master "
        "WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name"
    ).fetchall()
    schema = [
        [_normalized_sqlite_value(value) for value in tuple(row)] for row in schema_rows
    ]
    tables: dict[str, object] = {}
    table_names = sorted(
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' "
            "AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
    )
    for table_name in table_names:
        columns = [
            row[1]
            for row in connection.execute(
                f"PRAGMA table_info({_quoted_identifier(table_name)})"
            ).fetchall()
        ]
        rows = [
            [_normalized_sqlite_value(value) for value in tuple(row)]
            for row in connection.execute(
                f"SELECT * FROM {_quoted_identifier(table_name)}"
            ).fetchall()
        ]
        rows.sort(key=lambda value: json.dumps(value, sort_keys=True, separators=(",", ":")))
        tables[table_name] = {"columns": columns, "rows": rows}
    return semantic_sha256_v22({"schema": schema, "tables": tables})


def _count(connection: sqlite3.Connection, table: str) -> int:
    return int(connection.execute(f"SELECT COUNT(*) FROM {_quoted_identifier(table)}").fetchone()[0])


def _state_counts(connection: sqlite3.Connection) -> ProductStateCountsV0232:
    jobs = {
        (row["job_type"], row["status"]): int(row["count"])
        for row in connection.execute(
            "SELECT job_type, status, COUNT(*) AS count FROM diagnosis_jobs "
            "GROUP BY job_type, status"
        ).fetchall()
    }
    by_status = {
        row["status"]: int(row["count"])
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
    payload = {
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
        "fault_family_count": _count(connection, "fault_families"),
        "knowledge_artifact_count": sum(_count(connection, table) for table in knowledge_tables),
        "pending_job_count": by_status.get("PENDING", 0),
        "running_job_count": by_status.get("RUNNING", 0),
        "failed_job_count": by_status.get("FAILED", 0),
    }
    try:
        return ProductStateCountsV0232.model_validate(payload)
    except ValueError as error:
        raise ProductStateCloneErrorV0232(
            "Product v0.2.3.2 starting counts differ"
        ) from error


def _active_bindings(connection: sqlite3.Connection) -> tuple[str, str, str, str]:
    baseline_rows = connection.execute(
        "SELECT baseline_id, environment_id, payload_json FROM baseline_versions "
        "WHERE active = 1"
    ).fetchall()
    if len(baseline_rows) != 1:
        raise ProductStateCloneErrorV0232("Product-state active Baseline differs")
    baseline_row = baseline_rows[0]
    baseline_payload = json.loads(baseline_row["payload_json"])
    baseline_sha256 = baseline_payload.get("baseline_sha256")
    if not isinstance(baseline_sha256, str) or not re.fullmatch(_SHA256_PATTERN, baseline_sha256):
        raise ProductStateCloneErrorV0232("Product-state active Baseline SHA is invalid")
    profile_rows: list[dict[str, Any]] = []
    for row in connection.execute(
        "SELECT settings_json FROM connector_configs WHERE kind = 'OPENSEARCH'"
    ).fetchall():
        settings = json.loads(row["settings_json"])
        binding = settings.get("profile_binding")
        if (
            isinstance(binding, dict)
            and binding.get("profile_status") == "ACTIVE"
            and binding.get("selected_candidate_alias") == "P01"
        ):
            profile_rows.append(binding)
    if len(profile_rows) != 1:
        raise ProductStateCloneErrorV0232("Product-state ACTIVE P01 profile differs")
    profile_sha256 = profile_rows[0].get("profile_sha256")
    if not isinstance(profile_sha256, str) or not re.fullmatch(_SHA256_PATTERN, profile_sha256):
        raise ProductStateCloneErrorV0232("Product-state ACTIVE P01 profile SHA is invalid")
    return (
        str(baseline_row["environment_id"]),
        str(baseline_row["baseline_id"]),
        baseline_sha256,
        profile_sha256,
    )


def _object_inventory(root: Path, connection: sqlite3.Connection) -> str:
    sha_root = root / "objects" / "sha256"
    if not sha_root.is_dir():
        raise ProductStateCloneErrorV0232("Product-state object store is missing")
    metadata = {
        row["object_sha256"]: (int(row["byte_size"]), str(row["media_type"]))
        for row in connection.execute(
            "SELECT object_sha256, byte_size, media_type FROM evidence_objects"
        ).fetchall()
    }
    entries: list[dict[str, object]] = []
    observed: set[str] = set()
    object_paths: list[Path] = []
    for path in sorted(sha_root.rglob("*")):
        relative = path.relative_to(sha_root)
        if len(relative.parts) == 1:
            if not path.is_dir() or re.fullmatch(r"[0-9a-f]{2}", path.name) is None:
                raise ProductStateCloneErrorV0232(
                    "Product-state object directory is invalid"
                )
            continue
        if len(relative.parts) != 2 or not path.is_file():
            raise ProductStateCloneErrorV0232("Product-state object path is invalid")
        object_paths.append(path)
    for path in object_paths:
        match = _OBJECT_NAME.fullmatch(path.name)
        if match is None or path.parent.name != match.group("sha")[:2]:
            raise ProductStateCloneErrorV0232("Product-state object path is invalid")
        object_sha256 = match.group("sha")
        actual_sha256 = _sha256_file(path)
        if actual_sha256 != object_sha256:
            raise ProductStateCloneErrorV0232("Product-state object digest differs")
        stored = metadata.get(object_sha256)
        if stored is None or stored[0] != path.stat().st_size:
            raise ProductStateCloneErrorV0232("Product-state object metadata differs")
        observed.add(object_sha256)
        entries.append(
            {
                "path": path.relative_to(root).as_posix(),
                "sha256": object_sha256,
                "size_bytes": path.stat().st_size,
                "media_type": stored[1],
            }
        )
    if observed != set(metadata):
        raise ProductStateCloneErrorV0232("Product-state object inventory differs")
    return semantic_sha256_v22(entries)


def _runtime_file_inventory(
    root: Path,
    *,
    expected_environment_id: str,
    expected_pilot_runtime_authority_sha256: str | None,
    expected_runtime_connector_binding_sha256: str | None,
) -> str:
    pilot = root / "pilot"
    entries: list[dict[str, object]] = []
    for name in _PILOT_FILES:
        path = pilot / name
        if not path.is_file() or path.is_symlink():
            raise ProductStateCloneErrorV0232(
                f"Product-state runtime file is missing: {name}"
            )
        entries.append(
            {
                "path": f"pilot/{name}",
                "sha256": _sha256_file(path),
                "size_bytes": path.stat().st_size,
            }
        )
    unexpected = sorted(
        path.name for path in pilot.iterdir() if path.name not in _PILOT_FILES
    )
    if unexpected:
        raise ProductStateCloneErrorV0232(
            "Product-state runtime file set differs: " + ",".join(unexpected)
        )
    try:
        authority = PilotRuntimeAuthorityV02.model_validate_json(
            (pilot / "runtime-authority.json").read_bytes()
        )
        snapshot = PilotRuntimeSnapshotV02.model_validate_json(
            (pilot / "runtime-readiness.json").read_bytes()
        )
    except ValueError as error:
        raise ProductStateCloneErrorV0232(
            "Product-state runtime file validation failed"
        ) from error
    if (
        authority.environment_id != expected_environment_id
        or snapshot.environment_id != expected_environment_id
        or authority.connector_binding_sha256 != snapshot.authority_sha256
        or (
            expected_pilot_runtime_authority_sha256 is not None
            and authority.pilot_authority_sha256
            != expected_pilot_runtime_authority_sha256
        )
        or (
            expected_runtime_connector_binding_sha256 is not None
            and snapshot.authority_sha256
            != expected_runtime_connector_binding_sha256
        )
    ):
        raise ProductStateCloneErrorV0232(
            "Product-state Runtime authority/snapshot binding differs"
        )
    return semantic_sha256_v22(entries)


def _inspect_state(
    root: Path,
    *,
    expected_pilot_runtime_authority_sha256: str | None,
    expected_runtime_connector_binding_sha256: str | None,
) -> _StateInspectionV0232:
    _require_regular_tree(root)
    _require_no_sqlite_sidecars(root)
    database = root / "product.sqlite3"
    if not database.is_file() or database.is_symlink():
        raise ProductStateCloneErrorV0232("Product-state SQLite database is missing")
    database_file_sha256 = _sha256_file(database)
    try:
        with _read_only_connection(database) as connection:
            logical_sha256 = _logical_database_sha256(connection)
            counts = _state_counts(connection)
            environment_id, baseline_id, baseline_sha256, profile_sha256 = (
                _active_bindings(connection)
            )
            object_inventory_sha256 = _object_inventory(root, connection)
    except (sqlite3.Error, json.JSONDecodeError) as error:
        raise ProductStateCloneErrorV0232(
            "Product-state SQLite admission failed"
        ) from error
    return _StateInspectionV0232(
        database_file_sha256=database_file_sha256,
        database_logical_sha256=logical_sha256,
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
        counts=counts,
        environment_id=environment_id,
        active_baseline_id=baseline_id,
        active_baseline_sha256=baseline_sha256,
        profile_sha256=profile_sha256,
    )


def admit_product_state_source_v0232(
    source_root: Path,
    *,
    source_locator: str,
    expected_environment_id: str | None = None,
    expected_baseline_id: str | None = None,
    expected_baseline_sha256: str | None = None,
    expected_profile_sha256: str | None = None,
    expected_pilot_runtime_authority_sha256: str | None = None,
    expected_runtime_connector_binding_sha256: str | None = None,
) -> ProductStateSourceV0232:
    locator = _require_relative_locator(source_locator)
    root = Path(source_root).expanduser()
    if not root.is_absolute():
        root = root.absolute()
    inspection = _inspect_state(
        root,
        expected_pilot_runtime_authority_sha256=(
            expected_pilot_runtime_authority_sha256
        ),
        expected_runtime_connector_binding_sha256=(
            expected_runtime_connector_binding_sha256
        ),
    )
    expectations = (
        ("environment", inspection.environment_id, expected_environment_id),
        ("Baseline ID", inspection.active_baseline_id, expected_baseline_id),
        ("Baseline SHA", inspection.active_baseline_sha256, expected_baseline_sha256),
        ("P01 profile SHA", inspection.profile_sha256, expected_profile_sha256),
    )
    for name, actual, expected in expectations:
        if expected is not None and actual != expected:
            raise ProductStateCloneErrorV0232(f"Product-state {name} differs")
    body: dict[str, object] = {
        "schema_version": "ecomsre.product.product-state-source.v0232",
        "source_locator": locator,
        "source_database_file_sha256": inspection.database_file_sha256,
        "source_database_logical_sha256": inspection.database_logical_sha256,
        "source_object_inventory_sha256": inspection.object_inventory_sha256,
        "source_runtime_file_inventory_sha256": inspection.runtime_file_inventory_sha256,
        "source_counts": inspection.counts.model_dump(mode="json"),
        "source_environment_id": inspection.environment_id,
        "source_active_baseline_id": inspection.active_baseline_id,
        "source_active_baseline_sha256": inspection.active_baseline_sha256,
        "source_profile_sha256": inspection.profile_sha256,
    }
    return ProductStateSourceV0232.model_validate(
        {**body, "source_sha256": semantic_sha256_v22(body)}
    )


def _copy_file(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with source.open("rb") as input_handle, destination.open("xb") as output_handle:
        shutil.copyfileobj(input_handle, output_handle, length=1024 * 1024)
        output_handle.flush()
        os.fsync(output_handle.fileno())


def _online_backup(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    source_uri = f"file:{source.as_posix()}?mode=ro&immutable=1"
    source_connection = sqlite3.connect(source_uri, uri=True, isolation_level=None)
    destination_connection = sqlite3.connect(destination, isolation_level=None)
    try:
        source_connection.backup(destination_connection)
    finally:
        destination_connection.close()
        source_connection.close()


def _require_destination_ancestors(destination: Path) -> None:
    current = Path(destination.anchor)
    for component in destination.parts[1:-1]:
        current /= component
        if current.is_symlink():
            raise ProductStateCloneErrorV0232(
                f"Product-state destination path contains a symlink: {current}"
            )
        if current.exists() and not current.is_dir():
            raise ProductStateCloneErrorV0232(
                f"Product-state destination parent is not a directory: {current}"
            )


def clone_product_state_v0232(
    source_root: Path,
    destination_root: Path,
    *,
    source_locator: str,
    destination_locator: str,
    expected_environment_id: str | None = None,
    expected_baseline_id: str | None = None,
    expected_baseline_sha256: str | None = None,
    expected_profile_sha256: str | None = None,
    expected_pilot_runtime_authority_sha256: str | None = None,
    expected_runtime_connector_binding_sha256: str | None = None,
) -> ProductStateCloneV0232:
    destination_locator = _require_relative_locator(destination_locator)
    source = admit_product_state_source_v0232(
        source_root,
        source_locator=source_locator,
        expected_environment_id=expected_environment_id,
        expected_baseline_id=expected_baseline_id,
        expected_baseline_sha256=expected_baseline_sha256,
        expected_profile_sha256=expected_profile_sha256,
        expected_pilot_runtime_authority_sha256=(
            expected_pilot_runtime_authority_sha256
        ),
        expected_runtime_connector_binding_sha256=(
            expected_runtime_connector_binding_sha256
        ),
    )
    destination = Path(destination_root).expanduser()
    if not destination.is_absolute():
        destination = destination.absolute()
    _require_destination_ancestors(destination)
    clone_container = destination.parent
    if (
        destination.exists()
        or destination.is_symlink()
        or clone_container.exists()
        or clone_container.is_symlink()
    ):
        raise ProductStateCloneErrorV0232("Product-state clone destination already exists")
    clone_container.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(
        tempfile.mkdtemp(
            prefix=".product-state-clone-v0232-", dir=clone_container.parent
        )
    )
    clone_container.mkdir(mode=0o700)
    completed = False
    try:
        source_path = Path(source_root).expanduser()
        if not source_path.is_absolute():
            source_path = source_path.absolute()
        _online_backup(source_path / "product.sqlite3", temporary / "product.sqlite3")
        for object_path in sorted((source_path / "objects/sha256").glob("*/*.json")):
            _copy_file(object_path, temporary / object_path.relative_to(source_path))
        for name in _PILOT_FILES:
            _copy_file(source_path / "pilot" / name, temporary / "pilot" / name)
        cloned = admit_product_state_source_v0232(
            temporary,
            source_locator=destination_locator,
            expected_environment_id=source.source_environment_id,
            expected_baseline_id=source.source_active_baseline_id,
            expected_baseline_sha256=source.source_active_baseline_sha256,
            expected_profile_sha256=source.source_profile_sha256,
            expected_pilot_runtime_authority_sha256=(
                expected_pilot_runtime_authority_sha256
            ),
            expected_runtime_connector_binding_sha256=(
                expected_runtime_connector_binding_sha256
            ),
        )
        source_after = admit_product_state_source_v0232(
            source_path,
            source_locator=source_locator,
            expected_environment_id=source.source_environment_id,
            expected_baseline_id=source.source_active_baseline_id,
            expected_baseline_sha256=source.source_active_baseline_sha256,
            expected_profile_sha256=source.source_profile_sha256,
            expected_pilot_runtime_authority_sha256=(
                expected_pilot_runtime_authority_sha256
            ),
            expected_runtime_connector_binding_sha256=(
                expected_runtime_connector_binding_sha256
            ),
        )
        if source_after != source:
            raise ProductStateCloneErrorV0232(
                "Product v0.2.3.2 source inventory changed during clone"
            )
        body: dict[str, object] = {
            "schema_version": "ecomsre.product.product-state-clone.v0232",
            "terminal": HISTORY_AND_STATE_PASS_V0232,
            "source_locator": source.source_locator,
            "source_database_file_sha256_before": source.source_database_file_sha256,
            "source_database_file_sha256_after": source_after.source_database_file_sha256,
            "source_database_logical_sha256": source.source_database_logical_sha256,
            "source_object_inventory_sha256": source.source_object_inventory_sha256,
            "source_runtime_file_inventory_sha256": source.source_runtime_file_inventory_sha256,
            "source_counts": source.source_counts.model_dump(mode="json"),
            "source_environment_id": source.source_environment_id,
            "source_active_baseline_id": source.source_active_baseline_id,
            "source_active_baseline_sha256": source.source_active_baseline_sha256,
            "source_profile_sha256": source.source_profile_sha256,
            "destination_locator": destination_locator,
            "destination_database_logical_sha256": cloned.source_database_logical_sha256,
            "destination_object_inventory_sha256": cloned.source_object_inventory_sha256,
            "destination_runtime_file_inventory_sha256": (
                cloned.source_runtime_file_inventory_sha256
            ),
            "destination_counts": cloned.source_counts.model_dump(mode="json"),
            "destination_environment_id": cloned.source_environment_id,
            "destination_active_baseline_id": cloned.source_active_baseline_id,
            "destination_active_baseline_sha256": cloned.source_active_baseline_sha256,
            "destination_profile_sha256": cloned.source_profile_sha256,
        }
        result = ProductStateCloneV0232.model_validate(
            {**body, "clone_sha256": semantic_sha256_v22(body)}
        )
        temporary.replace(destination)
        completed = True
        return result
    except (OSError, sqlite3.Error) as error:
        raise ProductStateCloneErrorV0232("Product-state clone failed") from error
    finally:
        if not completed:
            shutil.rmtree(temporary, ignore_errors=True)
            try:
                clone_container.rmdir()
            except OSError:
                pass


__all__ = (
    "HISTORY_AND_STATE_PASS_V0232",
    "ProductStateCloneErrorV0232",
    "ProductStateCloneV0232",
    "ProductStateCountsV0232",
    "ProductStateSourceV0232",
    "admit_product_state_source_v0232",
    "clone_product_state_v0232",
)
