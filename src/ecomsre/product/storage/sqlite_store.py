"""SQLite WAL store and migration runner."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
import sqlite3
from typing import Iterator

from ecomsre.product.storage.migrations import MIGRATIONS
from ecomsre.product.storage.schema import REQUIRED_TABLES, SCHEMA_VERSION


class SqliteStoreV1:
    def __init__(self, path: Path, *, busy_timeout_ms: int = 5000) -> None:
        self.path = Path(path).expanduser().resolve()
        self.busy_timeout_ms = busy_timeout_ms
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._migrate()

    def _new_connection(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.path,
            timeout=self.busy_timeout_ms / 1000,
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        connection.execute(f"PRAGMA busy_timeout = {self.busy_timeout_ms}")
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = self._new_connection()
        try:
            yield connection
        finally:
            connection.close()

    def _migrate(self) -> None:
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    """CREATE TABLE IF NOT EXISTS schema_migrations (
                        version INTEGER PRIMARY KEY,
                        name TEXT NOT NULL,
                        applied_at TEXT NOT NULL
                    )"""
                )
                applied = {
                    row[0]: row[1]
                    for row in connection.execute(
                        "SELECT version, name FROM schema_migrations"
                    ).fetchall()
                }
                expected_names = {
                    version: name for version, name, _statements in MIGRATIONS
                }
                if any(version > SCHEMA_VERSION for version in applied):
                    raise RuntimeError("database has a newer schema version")
                for version, name in applied.items():
                    if expected_names.get(version) != name:
                        raise RuntimeError("database migration identity differs")
                for version, name, statements in MIGRATIONS:
                    if version in applied:
                        continue
                    for statement in statements:
                        connection.execute(statement)
                    connection.execute(
                        "INSERT INTO schema_migrations(version, name, applied_at) "
                        "VALUES (?, ?, ?)",
                        (version, name, datetime.now(UTC).isoformat()),
                    )
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise

    def ready(self) -> bool:
        try:
            with self.connect() as connection:
                tables = {
                    row[0]
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table'"
                    ).fetchall()
                }
                version = connection.execute(
                    "SELECT MAX(version) FROM schema_migrations"
                ).fetchone()[0]
            return set(REQUIRED_TABLES).issubset(tables) and version == SCHEMA_VERSION
        except sqlite3.Error:
            return False


__all__ = ("SqliteStoreV1",)
