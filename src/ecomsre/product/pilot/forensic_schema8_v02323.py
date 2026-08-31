"""Read-only forensic snapshot and digest contracts for Product v0.2.3.2.3."""

from __future__ import annotations

from contextlib import contextmanager
import base64
from enum import Enum
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sqlite3
import stat
import tempfile
from typing import Callable, cast, Iterator, Literal, Mapping

from pydantic import ConfigDict, Field, model_validator

from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22
from ecomsre.product.contracts import ProductModelV1


GOAL_VERSION_V02323: Literal[
    "ecomsre-product-v02323-schema8-reconstruction-diagnosis-replay-v1"
] = "ecomsre-product-v02323-schema8-reconstruction-diagnosis-replay-v1"
FORENSIC_SOURCE_SNAPSHOT_PASS_V02323: Literal[
    "ECOMSRE_PRODUCT_V02323_FORENSIC_SOURCE_SNAPSHOT_PASS"
] = "ECOMSRE_PRODUCT_V02323_FORENSIC_SOURCE_SNAPSHOT_PASS"
DIGEST_SEMANTICS_PASS_V02323: Literal[
    "ECOMSRE_PRODUCT_V02323_DIGEST_SEMANTICS_PASS"
] = "ECOMSRE_PRODUCT_V02323_DIGEST_SEMANTICS_PASS"
SOURCE_OWNER_BLOCKER_V02323 = "BLOCKED_ECOMSRE_PRODUCT_V02323_SOURCE_OWNER"
SNAPSHOT_BLOCKER_V02323 = "BLOCKED_ECOMSRE_PRODUCT_V02323_FORENSIC_SNAPSHOT"
DIGEST_BLOCKER_V02323 = "BLOCKED_ECOMSRE_PRODUCT_V02323_DIGEST_SEMANTICS"

_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_OBJECT_NAME = re.compile(r"^(?P<sha>[0-9a-f]{64})\.json$")
_RUNTIME_FILES = ("runtime-authority.json", "runtime-readiness.json")
_SQLITE_SIDECARS = ("product.sqlite3-wal", "product.sqlite3-shm")


class ForensicContractErrorV02323(RuntimeError):
    """A v0.2.3.2.3 forensic boundary cannot be proven."""


class ProductStateDigestKindV02323(str, Enum):
    RAW_SQLITE_FILE_SHA256 = "RAW_SQLITE_FILE_SHA256"
    CANONICAL_DATABASE_LOGICAL_SHA256 = "CANONICAL_DATABASE_LOGICAL_SHA256"
    PRODUCT_STATE_SEMANTIC_SHA256 = "PRODUCT_STATE_SEMANTIC_SHA256"
    OBJECT_INVENTORY_SHA256 = "OBJECT_INVENTORY_SHA256"
    RUNTIME_INVENTORY_SHA256 = "RUNTIME_INVENTORY_SHA256"
    UNKNOWN = "UNKNOWN"


class ForensicRawSourceSnapshotV02323(ProductModelV1):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[
        "ecomsre.product.forensic-raw-source-snapshot.v02323"
    ] = "ecomsre.product.forensic-raw-source-snapshot.v02323"
    goal_version: Literal[
        "ecomsre-product-v02323-schema8-reconstruction-diagnosis-replay-v1"
    ] = GOAL_VERSION_V02323
    terminal: Literal[
        "ECOMSRE_PRODUCT_V02323_FORENSIC_SOURCE_SNAPSHOT_PASS"
    ] = FORENSIC_SOURCE_SNAPSHOT_PASS_V02323
    source_locator: str
    snapshot_locator: str
    captured_at: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
    source_owner_count: int = Field(ge=0)
    source_owner_count_after: int = Field(ge=0)
    source_schema_version: int = Field(ge=1)
    source_database_file_sha256: str = Field(pattern=_SHA256_PATTERN)
    source_wal_present: bool
    source_wal_sha256: str | None = Field(default=None, pattern=_SHA256_PATTERN)
    source_shm_present: bool
    source_shm_sha256: str | None = Field(default=None, pattern=_SHA256_PATTERN)
    source_object_inventory_sha256: str = Field(pattern=_SHA256_PATTERN)
    source_runtime_file_inventory_sha256: str = Field(pattern=_SHA256_PATTERN)
    source_tree_inventory_sha256: str = Field(pattern=_SHA256_PATTERN)
    source_tree_inventory_sha256_after: str = Field(pattern=_SHA256_PATTERN)
    file_modes: dict[str, str]
    file_sizes: dict[str, int]
    file_mtimes_ns: dict[str, int]
    snapshot_database_file_sha256: str = Field(pattern=_SHA256_PATTERN)
    snapshot_wal_sha256: str | None = Field(default=None, pattern=_SHA256_PATTERN)
    snapshot_shm_sha256: str | None = Field(default=None, pattern=_SHA256_PATTERN)
    snapshot_consistent_database_present: bool
    snapshot_consistent_database_file_sha256: str | None = Field(
        default=None, pattern=_SHA256_PATTERN
    )
    snapshot_object_inventory_sha256: str = Field(pattern=_SHA256_PATTERN)
    snapshot_runtime_file_inventory_sha256: str = Field(pattern=_SHA256_PATTERN)
    snapshot_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def require_exact_copy_and_self_seal(self) -> ForensicRawSourceSnapshotV02323:
        if self.source_owner_count != 0 or self.source_owner_count_after != 0:
            raise ValueError(SOURCE_OWNER_BLOCKER_V02323)
        if (
            self.source_database_file_sha256
            != self.snapshot_database_file_sha256
            or self.source_wal_sha256 != self.snapshot_wal_sha256
            or self.source_shm_sha256 != self.snapshot_shm_sha256
            or self.source_wal_present != (self.source_wal_sha256 is not None)
            or self.source_shm_present != (self.source_shm_sha256 is not None)
            or (self.source_wal_present or self.source_shm_present)
            != self.snapshot_consistent_database_present
            or self.snapshot_consistent_database_present
            != (self.snapshot_consistent_database_file_sha256 is not None)
            or self.source_object_inventory_sha256
            != self.snapshot_object_inventory_sha256
            or self.source_runtime_file_inventory_sha256
            != self.snapshot_runtime_file_inventory_sha256
            or self.source_tree_inventory_sha256
            != self.source_tree_inventory_sha256_after
        ):
            raise ValueError(SNAPSHOT_BLOCKER_V02323)
        keys = tuple(sorted(self.file_modes))
        if keys != tuple(sorted(self.file_sizes)) or keys != tuple(
            sorted(self.file_mtimes_ns)
        ):
            raise ValueError(SNAPSHOT_BLOCKER_V02323)
        body = self.model_dump(mode="json", exclude={"snapshot_sha256"})
        if self.snapshot_sha256 != semantic_sha256_v22(body):
            raise ValueError(SNAPSHOT_BLOCKER_V02323)
        return self


class ForensicSourceImmutabilityProofV02323(ProductModelV1):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[
        "ecomsre.product.forensic-source-immutability.v02323"
    ] = "ecomsre.product.forensic-source-immutability.v02323"
    terminal: Literal[
        "ECOMSRE_PRODUCT_V02323_FORENSIC_SOURCE_SNAPSHOT_PASS"
    ] = FORENSIC_SOURCE_SNAPSHOT_PASS_V02323
    source_locator: str
    snapshot_sha256: str = Field(pattern=_SHA256_PATTERN)
    source_owner_count: int = Field(ge=0)
    expected_source_tree_inventory_sha256: str = Field(pattern=_SHA256_PATTERN)
    observed_source_tree_inventory_sha256: str = Field(pattern=_SHA256_PATTERN)
    source_unchanged: bool
    proof_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def require_unchanged_and_self_seal(self) -> ForensicSourceImmutabilityProofV02323:
        if (
            self.source_owner_count != 0
            or not self.source_unchanged
            or self.expected_source_tree_inventory_sha256
            != self.observed_source_tree_inventory_sha256
        ):
            raise ValueError(SNAPSHOT_BLOCKER_V02323)
        body = self.model_dump(mode="json", exclude={"proof_sha256"})
        if self.proof_sha256 != semantic_sha256_v22(body):
            raise ValueError(SNAPSHOT_BLOCKER_V02323)
        return self


class ProductStateDigestSemanticsAuditV02323(ProductModelV1):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[
        "ecomsre.product.digest-semantics-audit.v02323"
    ] = "ecomsre.product.digest-semantics-audit.v02323"
    goal_version: Literal[
        "ecomsre-product-v02323-schema8-reconstruction-diagnosis-replay-v1"
    ] = GOAL_VERSION_V02323
    terminal: Literal[
        "ECOMSRE_PRODUCT_V02323_DIGEST_SEMANTICS_PASS"
    ] = DIGEST_SEMANTICS_PASS_V02323
    expected_digest_full: str = Field(pattern=_SHA256_PATTERN)
    observed_contaminated_digest_full: str = Field(pattern=_SHA256_PATTERN)
    expected_digest_kind: ProductStateDigestKindV02323
    expected_digest_source_artifact: str
    expected_digest_source_field: str
    expected_digest_source_artifact_sha256: str = Field(pattern=_SHA256_PATTERN)
    source_definition_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    source_definition_path: str
    source_definition_file_sha256: str = Field(pattern=_SHA256_PATTERN)
    raw_digest_function_source_sha256: str = Field(pattern=_SHA256_PATTERN)
    logical_digest_function_source_sha256: str = Field(pattern=_SHA256_PATTERN)
    state_digest_function_source_sha256: str = Field(pattern=_SHA256_PATTERN)
    semantic_interpretation: str
    raw_byte_equality_required: bool
    logical_reconstruction_permitted: bool
    claim_limitations: tuple[str, ...]
    audit_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def require_known_kind_and_self_seal(self) -> ProductStateDigestSemanticsAuditV02323:
        if self.expected_digest_kind is ProductStateDigestKindV02323.UNKNOWN:
            raise ValueError(DIGEST_BLOCKER_V02323)
        if not self.claim_limitations:
            raise ValueError(DIGEST_BLOCKER_V02323)
        body = self.model_dump(mode="json", exclude={"audit_sha256"})
        if self.audit_sha256 != semantic_sha256_v22(body):
            raise ValueError(DIGEST_BLOCKER_V02323)
        return self


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _require_relative_locator(locator: str) -> str:
    relative = PurePosixPath(locator)
    if (
        not locator
        or relative.is_absolute()
        or ".." in relative.parts
        or "." in relative.parts
        or "\\" in locator
    ):
        raise ForensicContractErrorV02323(SNAPSHOT_BLOCKER_V02323)
    return locator


def _require_regular_source_tree(root: Path) -> None:
    if root.is_symlink() or not root.is_dir():
        raise ForensicContractErrorV02323(SNAPSHOT_BLOCKER_V02323)
    expected_roots = {
        "product.sqlite3",
        "objects",
        "pilot",
        *_SQLITE_SIDECARS,
    }
    unexpected_roots = sorted(path.name for path in root.iterdir() if path.name not in expected_roots)
    if unexpected_roots:
        raise ForensicContractErrorV02323(
            f"{SNAPSHOT_BLOCKER_V02323}: unexpected source entries: "
            + ",".join(unexpected_roots)
        )
    if (root / "product.sqlite3-journal").exists():
        raise ForensicContractErrorV02323(SNAPSHOT_BLOCKER_V02323)
    for path in root.rglob("*"):
        if path.is_symlink() or (not path.is_dir() and not path.is_file()):
            raise ForensicContractErrorV02323(SNAPSHOT_BLOCKER_V02323)
    database = root / "product.sqlite3"
    if not database.is_file():
        raise ForensicContractErrorV02323(SNAPSHOT_BLOCKER_V02323)
    pilot = root / "pilot"
    if not pilot.is_dir() or tuple(sorted(path.name for path in pilot.iterdir())) != tuple(
        sorted(_RUNTIME_FILES)
    ):
        raise ForensicContractErrorV02323(SNAPSHOT_BLOCKER_V02323)
    if not (root / "objects" / "sha256").is_dir():
        raise ForensicContractErrorV02323(SNAPSHOT_BLOCKER_V02323)


def _raw_tree_entries(root: Path) -> tuple[dict[str, object], ...]:
    _require_regular_source_tree(root)
    entries: list[dict[str, object]] = []
    for path in sorted((item for item in root.rglob("*") if item.is_file())):
        metadata = path.stat()
        entries.append(
            {
                "path": path.relative_to(root).as_posix(),
                "sha256": _sha256_file(path),
                "size_bytes": metadata.st_size,
                "mode": stat.filemode(metadata.st_mode),
                "mtime_ns": metadata.st_mtime_ns,
            }
        )
    return tuple(entries)


def raw_tree_inventory_v02323(root: Path) -> str:
    """Return a metadata-sensitive SHA without opening SQLite."""

    return semantic_sha256_v22(list(_raw_tree_entries(Path(root))))


def _normalized_sqlite_value(value: object) -> object:
    if isinstance(value, bytes):
        return {"base64": base64.b64encode(value).decode("ascii")}
    if value is None or isinstance(value, (str, int, float)):
        return value
    raise ForensicContractErrorV02323(SNAPSHOT_BLOCKER_V02323)


def _quoted_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


class ForensicSqliteReaderV02323:
    """A query-only SQLite reader with no migration or repair dependency."""

    def __init__(self, database: Path) -> None:
        candidate = Path(database)
        if candidate.is_symlink() or not candidate.is_file():
            raise ForensicContractErrorV02323(SNAPSHOT_BLOCKER_V02323)
        self._database = candidate.resolve(strict=True)

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        uri = f"file:{self._database.as_posix()}?mode=ro&immutable=1"
        connection = sqlite3.connect(uri, uri=True, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only = ON")
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
        finally:
            connection.close()

    def schema_version(self) -> int:
        with self._connection() as connection:
            row = connection.execute("SELECT MAX(version) FROM schema_migrations").fetchone()
        if row is None or row[0] is None:
            raise ForensicContractErrorV02323(SNAPSHOT_BLOCKER_V02323)
        return int(row[0])

    def logical_database_sha256(self) -> str:
        with self._connection() as connection:
            integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
            if integrity != "ok":
                raise ForensicContractErrorV02323(SNAPSHOT_BLOCKER_V02323)
            schema_rows = connection.execute(
                "SELECT type, name, tbl_name, sql FROM sqlite_master "
                "WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name"
            ).fetchall()
            schema = [
                [_normalized_sqlite_value(value) for value in tuple(row)]
                for row in schema_rows
            ]
            tables: dict[str, object] = {}
            table_names = sorted(
                str(row[0])
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
                rows.sort(
                    key=lambda value: json.dumps(
                        value, sort_keys=True, separators=(",", ":")
                    )
                )
                tables[table_name] = {"columns": columns, "rows": rows}
        return semantic_sha256_v22({"schema": schema, "tables": tables})

    def object_inventory_sha256(self, product_root: Path) -> str:
        root = Path(product_root).resolve(strict=True)
        sha_root = root / "objects" / "sha256"
        with self._connection() as connection:
            metadata = {
                str(row["object_sha256"]): (
                    int(row["byte_size"]),
                    str(row["media_type"]),
                )
                for row in connection.execute(
                    "SELECT object_sha256, byte_size, media_type FROM evidence_objects"
                ).fetchall()
            }
        entries: list[dict[str, object]] = []
        observed: set[str] = set()
        for path in sorted(item for item in sha_root.rglob("*") if item.is_file()):
            relative = path.relative_to(sha_root)
            match = _OBJECT_NAME.fullmatch(path.name)
            if (
                len(relative.parts) != 2
                or match is None
                or path.parent.name != match.group("sha")[:2]
            ):
                raise ForensicContractErrorV02323(SNAPSHOT_BLOCKER_V02323)
            object_sha256 = match.group("sha")
            stored = metadata.get(object_sha256)
            if (
                _sha256_file(path) != object_sha256
                or stored is None
                or stored[0] != path.stat().st_size
            ):
                raise ForensicContractErrorV02323(SNAPSHOT_BLOCKER_V02323)
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
            raise ForensicContractErrorV02323(SNAPSHOT_BLOCKER_V02323)
        return semantic_sha256_v22(entries)

def _runtime_inventory_sha256(root: Path) -> str:
    entries: list[dict[str, object]] = []
    for name in _RUNTIME_FILES:
        path = root / "pilot" / name
        if path.is_symlink() or not path.is_file():
            raise ForensicContractErrorV02323(SNAPSHOT_BLOCKER_V02323)
        entries.append(
            {
                "path": f"pilot/{name}",
                "sha256": _sha256_file(path),
                "size_bytes": path.stat().st_size,
            }
        )
    return semantic_sha256_v22(entries)


def _copy_create_once(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with source.open("rb") as input_handle, destination.open("xb") as output_handle:
        shutil.copyfileobj(input_handle, output_handle, length=1024 * 1024)
        output_handle.flush()
        os.fsync(output_handle.fileno())
    shutil.copystat(source, destination, follow_symlinks=False)


def _copy_raw_product_tree(source: Path, destination: Path) -> None:
    destination.mkdir(mode=0o700, parents=True)
    _copy_create_once(source / "product.sqlite3", destination / "product.sqlite3")
    for name in _SQLITE_SIDECARS:
        if (source / name).exists():
            _copy_create_once(source / name, destination / name)
    for path in sorted((source / "objects").rglob("*")):
        relative = path.relative_to(source)
        if path.is_dir():
            (destination / relative).mkdir(parents=True, exist_ok=True)
        else:
            _copy_create_once(path, destination / relative)
    for name in _RUNTIME_FILES:
        _copy_create_once(source / "pilot" / name, destination / "pilot" / name)


def _inspection_database(snapshot_product: Path) -> Path:
    wal = snapshot_product / "product.sqlite3-wal"
    shm = snapshot_product / "product.sqlite3-shm"
    if not wal.exists() and not shm.exists():
        return snapshot_product / "product.sqlite3"
    inspection = snapshot_product.parent / "consistent-image" / "product.sqlite3"
    inspection.parent.mkdir(parents=True)
    with tempfile.TemporaryDirectory(
        prefix=".product-v02323-inspection-triplet-",
        dir=snapshot_product.parent.parent,
    ) as temporary_name:
        temporary = Path(temporary_name)
        for name in ("product.sqlite3", *_SQLITE_SIDECARS):
            source = snapshot_product / name
            if source.exists():
                _copy_create_once(source, temporary / name)
        source_uri = f"file:{(temporary / 'product.sqlite3').as_posix()}?mode=ro"
        source_connection = sqlite3.connect(source_uri, uri=True, isolation_level=None)
        destination_connection = sqlite3.connect(inspection, isolation_level=None)
        try:
            source_connection.backup(destination_connection)
        finally:
            destination_connection.close()
            source_connection.close()
    return inspection


def _clear_write_bits(root: Path) -> None:
    for path in sorted(root.rglob("*"), key=lambda item: len(item.parts), reverse=True):
        os.chmod(path, path.stat().st_mode & ~0o222, follow_symlinks=False)
    os.chmod(root, root.stat().st_mode & ~0o222, follow_symlinks=False)


def capture_forensic_source_snapshot_v02323(
    source_root: Path,
    snapshot_root: Path,
    *,
    source_locator: str,
    snapshot_locator: str,
    captured_at: str,
    owner_counter: Callable[[Path], int],
) -> ForensicRawSourceSnapshotV02323:
    """Copy a source before opening SQLite and seal the copied evidence read-only."""

    _require_relative_locator(source_locator)
    _require_relative_locator(snapshot_locator)
    source = Path(source_root).resolve(strict=True)
    _require_regular_source_tree(source)
    database = source / "product.sqlite3"
    owner_count = owner_counter(database)
    if owner_count != 0:
        raise ForensicContractErrorV02323(SOURCE_OWNER_BLOCKER_V02323)
    destination = Path(snapshot_root)
    if destination.exists() or destination.is_symlink():
        raise ForensicContractErrorV02323(SNAPSHOT_BLOCKER_V02323)
    destination.parent.mkdir(parents=True, exist_ok=True)

    source_entries_before = _raw_tree_entries(source)
    source_tree_before = semantic_sha256_v22(list(source_entries_before))
    snapshot_product = destination / "product"
    completed = False
    try:
        _copy_raw_product_tree(source, snapshot_product)
        copied_entries = _raw_tree_entries(snapshot_product)
        if source_entries_before != copied_entries:
            raise ForensicContractErrorV02323(SNAPSHOT_BLOCKER_V02323)

        inspection_database = _inspection_database(snapshot_product)
        reader = ForensicSqliteReaderV02323(inspection_database)
        object_inventory_sha256 = reader.object_inventory_sha256(snapshot_product)
        runtime_inventory_sha256 = _runtime_inventory_sha256(snapshot_product)
        if _raw_tree_entries(snapshot_product) != copied_entries:
            raise ForensicContractErrorV02323(SNAPSHOT_BLOCKER_V02323)
        source_entries_after = _raw_tree_entries(source)
        source_tree_after = semantic_sha256_v22(list(source_entries_after))
        owner_count_after = owner_counter(database)
        if source_entries_after != source_entries_before or owner_count_after != 0:
            raise ForensicContractErrorV02323(SNAPSHOT_BLOCKER_V02323)

        by_path = {str(entry["path"]): entry for entry in source_entries_before}
        database_sha256 = str(by_path["product.sqlite3"]["sha256"])
        wal = by_path.get("product.sqlite3-wal")
        shm = by_path.get("product.sqlite3-shm")
        body: dict[str, object] = {
            "schema_version": "ecomsre.product.forensic-raw-source-snapshot.v02323",
            "goal_version": GOAL_VERSION_V02323,
            "terminal": FORENSIC_SOURCE_SNAPSHOT_PASS_V02323,
            "source_locator": source_locator,
            "snapshot_locator": snapshot_locator,
            "captured_at": captured_at,
            "source_owner_count": owner_count,
            "source_owner_count_after": owner_count_after,
            "source_schema_version": reader.schema_version(),
            "source_database_file_sha256": database_sha256,
            "source_wal_present": wal is not None,
            "source_wal_sha256": None if wal is None else wal["sha256"],
            "source_shm_present": shm is not None,
            "source_shm_sha256": None if shm is None else shm["sha256"],
            "source_object_inventory_sha256": object_inventory_sha256,
            "source_runtime_file_inventory_sha256": runtime_inventory_sha256,
            "source_tree_inventory_sha256": source_tree_before,
            "source_tree_inventory_sha256_after": source_tree_after,
            "file_modes": {
                path: str(entry["mode"]) for path, entry in sorted(by_path.items())
            },
            "file_sizes": {
                path: cast(int, entry["size_bytes"])
                for path, entry in sorted(by_path.items())
            },
            "file_mtimes_ns": {
                path: cast(int, entry["mtime_ns"])
                for path, entry in sorted(by_path.items())
            },
            "snapshot_database_file_sha256": _sha256_file(
                snapshot_product / "product.sqlite3"
            ),
            "snapshot_wal_sha256": (
                None if wal is None else _sha256_file(snapshot_product / "product.sqlite3-wal")
            ),
            "snapshot_shm_sha256": (
                None if shm is None else _sha256_file(snapshot_product / "product.sqlite3-shm")
            ),
            "snapshot_consistent_database_present": (
                inspection_database != snapshot_product / "product.sqlite3"
            ),
            "snapshot_consistent_database_file_sha256": (
                None
                if inspection_database == snapshot_product / "product.sqlite3"
                else _sha256_file(inspection_database)
            ),
            "snapshot_object_inventory_sha256": object_inventory_sha256,
            "snapshot_runtime_file_inventory_sha256": runtime_inventory_sha256,
        }
        report = ForensicRawSourceSnapshotV02323.model_validate(
            {**body, "snapshot_sha256": semantic_sha256_v22(body)}
        )
        _clear_write_bits(destination)
        completed = True
        return report
    except (OSError, sqlite3.Error) as error:
        raise ForensicContractErrorV02323(SNAPSHOT_BLOCKER_V02323) from error
    finally:
        if not completed and destination.exists():
            shutil.rmtree(destination, ignore_errors=True)


def verify_forensic_snapshot_artifact_v02323(
    snapshot_root: Path,
    snapshot: ForensicRawSourceSnapshotV02323,
) -> str:
    """Re-hash a sealed private snapshot and require its complete read-only tree."""

    root = Path(snapshot_root).resolve(strict=True)
    if root.is_symlink() or not root.is_dir():
        raise ForensicContractErrorV02323(SNAPSHOT_BLOCKER_V02323)
    for path in (root, *root.rglob("*")):
        if path.is_symlink() or (not path.is_dir() and not path.is_file()):
            raise ForensicContractErrorV02323(SNAPSHOT_BLOCKER_V02323)
        if path.stat().st_mode & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH):
            raise ForensicContractErrorV02323(SNAPSHOT_BLOCKER_V02323)

    product = root / "product"
    observed_entries = _raw_tree_entries(product)
    by_path = {str(entry["path"]): entry for entry in observed_entries}
    expected_paths = tuple(sorted(snapshot.file_sizes))
    if tuple(sorted(by_path)) != expected_paths:
        raise ForensicContractErrorV02323(SNAPSHOT_BLOCKER_V02323)
    for relative in expected_paths:
        entry = by_path[relative]
        expected_mode = "".join(
            "-" if index in {2, 5, 8} else character
            for index, character in enumerate(snapshot.file_modes[relative])
        )
        if (
            entry["size_bytes"] != snapshot.file_sizes[relative]
            or entry["mtime_ns"] != snapshot.file_mtimes_ns[relative]
            or entry["mode"] != expected_mode
        ):
            raise ForensicContractErrorV02323(SNAPSHOT_BLOCKER_V02323)

    database = product / "product.sqlite3"
    wal = product / "product.sqlite3-wal"
    shm = product / "product.sqlite3-shm"
    consistent = root / "consistent-image" / "product.sqlite3"
    if (
        _sha256_file(database) != snapshot.snapshot_database_file_sha256
        or wal.is_file() != snapshot.source_wal_present
        or shm.is_file() != snapshot.source_shm_present
        or (None if not wal.is_file() else _sha256_file(wal))
        != snapshot.snapshot_wal_sha256
        or (None if not shm.is_file() else _sha256_file(shm))
        != snapshot.snapshot_shm_sha256
        or consistent.is_file() != snapshot.snapshot_consistent_database_present
        or (None if not consistent.is_file() else _sha256_file(consistent))
        != snapshot.snapshot_consistent_database_file_sha256
    ):
        raise ForensicContractErrorV02323(SNAPSHOT_BLOCKER_V02323)

    expected_files = {f"product/{relative}" for relative in expected_paths}
    if snapshot.snapshot_consistent_database_present:
        expected_files.add("consistent-image/product.sqlite3")
    observed_files = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file()
    }
    if observed_files != expected_files:
        raise ForensicContractErrorV02323(SNAPSHOT_BLOCKER_V02323)

    inspection_database = consistent if consistent.is_file() else database
    reader = ForensicSqliteReaderV02323(inspection_database)
    if (
        reader.schema_version() != snapshot.source_schema_version
        or reader.object_inventory_sha256(product)
        != snapshot.snapshot_object_inventory_sha256
        or _runtime_inventory_sha256(product)
        != snapshot.snapshot_runtime_file_inventory_sha256
    ):
        raise ForensicContractErrorV02323(SNAPSHOT_BLOCKER_V02323)
    return semantic_sha256_v22(
        {
            "snapshot_locator": snapshot.snapshot_locator,
            "snapshot_sha256": snapshot.snapshot_sha256,
            "verified_file_sha256": {
                relative: by_path[relative]["sha256"] for relative in expected_paths
            },
            "consistent_database_file_sha256": (
                snapshot.snapshot_consistent_database_file_sha256
            ),
            "read_only": True,
        }
    )


def verify_forensic_source_immutability_v02323(
    source_root: Path,
    snapshot: ForensicRawSourceSnapshotV02323,
    *,
    owner_counter: Callable[[Path], int],
) -> ForensicSourceImmutabilityProofV02323:
    source = Path(source_root).resolve(strict=True)
    observed = raw_tree_inventory_v02323(source)
    owner_count = owner_counter(source / "product.sqlite3")
    body: dict[str, object] = {
        "schema_version": "ecomsre.product.forensic-source-immutability.v02323",
        "terminal": FORENSIC_SOURCE_SNAPSHOT_PASS_V02323,
        "source_locator": snapshot.source_locator,
        "snapshot_sha256": snapshot.snapshot_sha256,
        "source_owner_count": owner_count,
        "expected_source_tree_inventory_sha256": snapshot.source_tree_inventory_sha256,
        "observed_source_tree_inventory_sha256": observed,
        "source_unchanged": observed == snapshot.source_tree_inventory_sha256,
    }
    try:
        return ForensicSourceImmutabilityProofV02323.model_validate(
            {**body, "proof_sha256": semantic_sha256_v22(body)}
        )
    except ValueError as error:
        raise ForensicContractErrorV02323(SNAPSHOT_BLOCKER_V02323) from error


def extract_raw_sqlite_digest_event_v02323(
    event_bytes: bytes,
    *,
    expected_digest_full: str,
    source_locator: str,
) -> Mapping[str, object]:
    """Validate the frozen command event that emitted the historical raw DB SHA."""

    _require_relative_locator(source_locator)
    try:
        event = json.loads(event_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ForensicContractErrorV02323(DIGEST_BLOCKER_V02323) from error
    payload = event.get("payload") if isinstance(event, Mapping) else None
    item = payload.get("item") if isinstance(payload, Mapping) else None
    command = item.get("command") if isinstance(item, Mapping) else None
    stdout = item.get("stdout") if isinstance(item, Mapping) else None
    if (
        not isinstance(event, Mapping)
        or event.get("type") != "event_msg"
        or not isinstance(payload, Mapping)
        or payload.get("type") != "item_completed"
        or not isinstance(item, Mapping)
        or item.get("type") != "CommandExecution"
        or item.get("source") != "unified_exec_startup"
        or item.get("status") != "completed"
        or item.get("exit_code") != 0
        or item.get("stderr") != ""
        or not isinstance(command, list)
        or len(command) != 3
        or command[:2] != ["/bin/zsh", "-lc"]
        or not isinstance(command[2], str)
        or "shasum -a 256" not in command[2]
        or source_locator not in command[2]
        or not isinstance(stdout, str)
    ):
        raise ForensicContractErrorV02323(DIGEST_BLOCKER_V02323)
    target_suffix = f"/{source_locator}/product.sqlite3"
    matches: list[tuple[str, str]] = []
    for line in stdout.splitlines():
        parts = line.split("  ", maxsplit=1)
        if (
            len(parts) == 2
            and re.fullmatch(_SHA256_PATTERN, parts[0]) is not None
            and parts[1].endswith(target_suffix)
        ):
            matches.append((parts[0], parts[1]))
    if (
        len(matches) != 1
        or matches[0][0] != expected_digest_full
        or str(PurePosixPath(matches[0][1]).parent) not in command[2]
    ):
        raise ForensicContractErrorV02323(DIGEST_BLOCKER_V02323)
    return {"source_database_file_sha256": matches[0][0]}


def _digest_kind_for_field(field: str) -> ProductStateDigestKindV02323:
    return {
        "source_database_file_sha256": ProductStateDigestKindV02323.RAW_SQLITE_FILE_SHA256,
        "source_database_logical_sha256": (
            ProductStateDigestKindV02323.CANONICAL_DATABASE_LOGICAL_SHA256
        ),
        "source_sha256": ProductStateDigestKindV02323.PRODUCT_STATE_SEMANTIC_SHA256,
        "source_object_inventory_sha256": (
            ProductStateDigestKindV02323.OBJECT_INVENTORY_SHA256
        ),
        "source_runtime_file_inventory_sha256": (
            ProductStateDigestKindV02323.RUNTIME_INVENTORY_SHA256
        ),
    }.get(field, ProductStateDigestKindV02323.UNKNOWN)


def build_product_state_digest_semantics_audit_v02323(
    *,
    expected_digest_full: str,
    observed_contaminated_digest_full: str,
    expected_digest_source_artifact: str,
    expected_digest_source_field: str,
    expected_digest_source_artifact_bytes: bytes,
    expected_digest_source_payload: Mapping[str, object],
    raw_digest_function_source: bytes,
    logical_digest_function_source: bytes,
    state_digest_function_source: bytes,
    source_definition_commit: str = "142dc1094926f18e789ece3668c34918f859b512",
    source_definition_path: str = (
        "src/ecomsre/product/pilot/product_state_clone_v0232.py"
    ),
    source_definition_file_bytes: bytes | None = None,
) -> ProductStateDigestSemanticsAuditV02323:
    kind = _digest_kind_for_field(expected_digest_source_field)
    if (
        kind is ProductStateDigestKindV02323.UNKNOWN
        or expected_digest_source_payload.get(expected_digest_source_field)
        != expected_digest_full
    ):
        raise ForensicContractErrorV02323(DIGEST_BLOCKER_V02323)
    interpretations = {
        ProductStateDigestKindV02323.RAW_SQLITE_FILE_SHA256: (
            "The expected digest hashes the pre-migration SQLite file bytes. "
            "Those historical bytes are lost; a clean logical reconstruction may use "
            "a different page layout."
        ),
        ProductStateDigestKindV02323.CANONICAL_DATABASE_LOGICAL_SHA256: (
            "The expected digest seals canonical schema and row content."
        ),
        ProductStateDigestKindV02323.PRODUCT_STATE_SEMANTIC_SHA256: (
            "The expected digest seals the Product-state model, including its component "
            "digests and bindings."
        ),
        ProductStateDigestKindV02323.OBJECT_INVENTORY_SHA256: (
            "The expected digest seals the content-addressed object inventory."
        ),
        ProductStateDigestKindV02323.RUNTIME_INVENTORY_SHA256: (
            "The expected digest seals the non-secret Runtime-file inventory."
        ),
    }
    limitations = (
        "The missing schema-8 SQLite file cannot be claimed byte-identical.",
        "Raw SQLite equality and canonical logical equality remain separate claims.",
        "No reconstruction produced by this Goal is a measured No-Fault result.",
    )
    body: dict[str, object] = {
        "schema_version": "ecomsre.product.digest-semantics-audit.v02323",
        "goal_version": GOAL_VERSION_V02323,
        "terminal": DIGEST_SEMANTICS_PASS_V02323,
        "expected_digest_full": expected_digest_full,
        "observed_contaminated_digest_full": observed_contaminated_digest_full,
        "expected_digest_kind": kind.value,
        "expected_digest_source_artifact": expected_digest_source_artifact,
        "expected_digest_source_field": expected_digest_source_field,
        "expected_digest_source_artifact_sha256": _sha256_bytes(
            expected_digest_source_artifact_bytes
        ),
        "source_definition_commit": source_definition_commit,
        "source_definition_path": source_definition_path,
        "source_definition_file_sha256": _sha256_bytes(
            source_definition_file_bytes
            if source_definition_file_bytes is not None
            else (
                raw_digest_function_source
                + logical_digest_function_source
                + state_digest_function_source
            )
        ),
        "raw_digest_function_source_sha256": _sha256_bytes(
            raw_digest_function_source
        ),
        "logical_digest_function_source_sha256": _sha256_bytes(
            logical_digest_function_source
        ),
        "state_digest_function_source_sha256": _sha256_bytes(
            state_digest_function_source
        ),
        "semantic_interpretation": interpretations[kind],
        "raw_byte_equality_required": False,
        "logical_reconstruction_permitted": True,
        "claim_limitations": limitations,
    }
    try:
        return ProductStateDigestSemanticsAuditV02323.model_validate(
            {**body, "audit_sha256": semantic_sha256_v22(body)}
        )
    except ValueError as error:
        raise ForensicContractErrorV02323(DIGEST_BLOCKER_V02323) from error


__all__ = (
    "DIGEST_SEMANTICS_PASS_V02323",
    "FORENSIC_SOURCE_SNAPSHOT_PASS_V02323",
    "ForensicContractErrorV02323",
    "ForensicRawSourceSnapshotV02323",
    "ForensicSourceImmutabilityProofV02323",
    "ForensicSqliteReaderV02323",
    "ProductStateDigestKindV02323",
    "ProductStateDigestSemanticsAuditV02323",
    "build_product_state_digest_semantics_audit_v02323",
    "capture_forensic_source_snapshot_v02323",
    "extract_raw_sqlite_digest_event_v02323",
    "raw_tree_inventory_v02323",
    "verify_forensic_snapshot_artifact_v02323",
    "verify_forensic_source_immutability_v02323",
)
