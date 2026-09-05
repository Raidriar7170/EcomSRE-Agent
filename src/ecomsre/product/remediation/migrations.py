"""Additive, independently versioned remediation schema; base schema 9 is preserved."""

from hashlib import sha256

from ecomsre.product.storage.sqlite_store import SqliteStoreV1


TABLES = {
    "remediation_registry_versions": "registry_sha256 TEXT PRIMARY KEY, payload_json TEXT NOT NULL",
    "remediation_candidate_projections": "projection_sha256 TEXT PRIMARY KEY, incident_id TEXT NOT NULL REFERENCES incidents(incident_id), payload_json TEXT NOT NULL",
    "remediation_candidates": "candidate_id TEXT PRIMARY KEY, candidate_sha256 TEXT NOT NULL UNIQUE, incident_id TEXT NOT NULL REFERENCES incidents(incident_id), registry_sha256 TEXT NOT NULL REFERENCES remediation_registry_versions(registry_sha256), payload_json TEXT NOT NULL",
    "remediation_approvals": "approval_id TEXT PRIMARY KEY, candidate_id TEXT NOT NULL REFERENCES remediation_candidates(candidate_id), approval_sha256 TEXT NOT NULL UNIQUE, payload_json TEXT NOT NULL",
    "remediation_revocations": "revocation_id TEXT PRIMARY KEY, approval_id TEXT NOT NULL UNIQUE REFERENCES remediation_approvals(approval_id), payload_json TEXT NOT NULL",
    "remediation_idempotency_keys": "operation TEXT NOT NULL, key_sha256 TEXT NOT NULL, request_sha256 TEXT NOT NULL, response_json TEXT NOT NULL, PRIMARY KEY(operation, key_sha256)",
}
STATEMENTS = tuple(
    f"CREATE TABLE {name} ({columns})" for name, columns in TABLES.items()
)
MIGRATION_SHA256 = sha256("\n".join(STATEMENTS).encode()).hexdigest()


def migrate(store: SqliteStoreV1) -> None:
    with store.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        try:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS remediation_schema_migrations "
                "(version INTEGER PRIMARY KEY, migration_sha256 TEXT NOT NULL)"
            )
            applied = connection.execute(
                "SELECT version, migration_sha256 FROM remediation_schema_migrations"
            ).fetchall()
            if applied:
                if [tuple(row) for row in applied] != [(1, MIGRATION_SHA256)]:
                    raise RuntimeError("remediation migration identity differs")
                for expected in STATEMENTS:
                    name = expected.split()[2]
                    row = connection.execute(
                        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?",
                        (name,),
                    ).fetchone()
                    if row is None or row[0] != expected:
                        raise RuntimeError("remediation table identity differs")
            else:
                for statement in STATEMENTS:
                    connection.execute(statement)
                connection.execute(
                    "INSERT INTO remediation_schema_migrations VALUES (1, ?)",
                    (MIGRATION_SHA256,),
                )
            connection.execute("COMMIT")
        except Exception:
            connection.execute("ROLLBACK")
            raise
