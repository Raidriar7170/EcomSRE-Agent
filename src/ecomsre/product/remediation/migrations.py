"""Additive, independently versioned remediation schema; base schema 9 is preserved."""

from hashlib import sha256

from ecomsre.product.storage.sqlite_store import SqliteStoreV1


TABLES = {
    "remediation_registry_versions": "registry_sha256 TEXT PRIMARY KEY, payload_json TEXT NOT NULL",
    "remediation_candidate_projections": "projection_sha256 TEXT PRIMARY KEY, incident_id TEXT NOT NULL REFERENCES incidents(incident_id), payload_json TEXT NOT NULL",
    "remediation_candidates": "candidate_id TEXT PRIMARY KEY, candidate_sha256 TEXT NOT NULL UNIQUE, incident_id TEXT NOT NULL REFERENCES incidents(incident_id), registry_sha256 TEXT NOT NULL REFERENCES remediation_registry_versions(registry_sha256), payload_json TEXT NOT NULL",
    "remediation_approvals": "approval_id TEXT PRIMARY KEY, candidate_id TEXT NOT NULL REFERENCES remediation_candidates(candidate_id), approval_sha256 TEXT NOT NULL UNIQUE, payload_json TEXT NOT NULL",
    "remediation_revocations": "revocation_id TEXT PRIMARY KEY, approval_id TEXT NOT NULL UNIQUE REFERENCES remediation_approvals(approval_id), payload_json TEXT NOT NULL",
    "remediation_idempotency_keys": "operation TEXT NOT NULL, key_sha256 TEXT NOT NULL, request_sha256 TEXT NOT NULL, response_sha256 TEXT NOT NULL, response_json TEXT NOT NULL, PRIMARY KEY(operation, key_sha256)",
}
STATEMENTS = tuple(
    f"CREATE TABLE {name} ({columns})" for name, columns in TABLES.items()
)
MIGRATION_SHA256 = sha256("\n".join(STATEMENTS).encode()).hexdigest()


V2_TABLES = {
    "remediation_state_bindings": "binding_sha256 TEXT PRIMARY KEY, environment_id TEXT NOT NULL UNIQUE REFERENCES environments(environment_id), payload_json TEXT NOT NULL",
    "remediation_current_state_snapshots": "snapshot_id TEXT PRIMARY KEY, snapshot_sha256 TEXT NOT NULL UNIQUE, candidate_id TEXT NOT NULL REFERENCES remediation_candidates(candidate_id), approval_id TEXT NOT NULL REFERENCES remediation_approvals(approval_id), binding_sha256 TEXT NOT NULL REFERENCES remediation_state_bindings(binding_sha256), payload_json TEXT NOT NULL",
    "remediation_authorizations": "authorization_id TEXT PRIMARY KEY, authorization_sha256 TEXT NOT NULL UNIQUE, approval_id TEXT NOT NULL UNIQUE REFERENCES remediation_approvals(approval_id), snapshot_id TEXT NOT NULL UNIQUE REFERENCES remediation_current_state_snapshots(snapshot_id), payload_json TEXT NOT NULL",
    "remediation_attempts": "attempt_id TEXT PRIMARY KEY, candidate_id TEXT NOT NULL REFERENCES remediation_candidates(candidate_id), approval_id TEXT NOT NULL REFERENCES remediation_approvals(approval_id), authorization_id TEXT UNIQUE REFERENCES remediation_authorizations(authorization_id), environment_id TEXT NOT NULL REFERENCES environments(environment_id), target TEXT NOT NULL CHECK(target = 'payment'), state TEXT NOT NULL, terminal TEXT, revision INTEGER NOT NULL, attempt_sha256 TEXT NOT NULL, payload_json TEXT NOT NULL",
    "remediation_attempt_revisions": "attempt_id TEXT NOT NULL REFERENCES remediation_attempts(attempt_id), revision INTEGER NOT NULL, attempt_sha256 TEXT NOT NULL UNIQUE, payload_json TEXT NOT NULL, PRIMARY KEY(attempt_id, revision)",
    "remediation_attempt_creations": "attempt_id TEXT PRIMARY KEY REFERENCES remediation_attempts(attempt_id), creation_sha256 TEXT NOT NULL UNIQUE, payload_json TEXT NOT NULL",
    "remediation_approval_consumptions": "approval_id TEXT PRIMARY KEY REFERENCES remediation_approvals(approval_id), attempt_id TEXT NOT NULL UNIQUE REFERENCES remediation_attempts(attempt_id), consumed_at TEXT NOT NULL",
    "remediation_write_intents": "write_intent_id TEXT PRIMARY KEY, attempt_id TEXT NOT NULL UNIQUE REFERENCES remediation_attempts(attempt_id), authorization_id TEXT NOT NULL UNIQUE REFERENCES remediation_authorizations(authorization_id), write_intent_sha256 TEXT NOT NULL UNIQUE, payload_json TEXT NOT NULL",
    "remediation_authorization_consumptions": "authorization_id TEXT PRIMARY KEY REFERENCES remediation_authorizations(authorization_id), write_intent_id TEXT NOT NULL UNIQUE REFERENCES remediation_write_intents(write_intent_id), consumed_at TEXT NOT NULL",
    "remediation_decision_trace_events": "attempt_id TEXT NOT NULL REFERENCES remediation_attempts(attempt_id), ordinal INTEGER NOT NULL, event_sha256 TEXT NOT NULL UNIQUE, payload_json TEXT NOT NULL, PRIMARY KEY(attempt_id, ordinal)",
}
V2_STATEMENTS = tuple(
    f"CREATE TABLE {name} ({columns})" for name, columns in V2_TABLES.items()
) + (
    "CREATE UNIQUE INDEX remediation_one_active_target ON remediation_attempts(environment_id, target) WHERE terminal IS NULL",
)
MIGRATIONS = (
    (1, MIGRATION_SHA256, STATEMENTS),
    (2, sha256("\n".join(V2_STATEMENTS).encode()).hexdigest(), V2_STATEMENTS),
)


def migrate(store: SqliteStoreV1) -> None:
    with store.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        try:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS remediation_schema_migrations "
                "(version INTEGER PRIMARY KEY, migration_sha256 TEXT NOT NULL)"
            )
            applied = {
                row[0]: row[1]
                for row in connection.execute(
                    "SELECT version, migration_sha256 FROM remediation_schema_migrations ORDER BY version"
                ).fetchall()
            }
            expected_versions = {version: digest for version, digest, _ in MIGRATIONS}
            if any(
                expected_versions.get(version) != digest
                for version, digest in applied.items()
            ):
                raise RuntimeError("remediation migration identity differs")
            if set(applied) != set(range(1, len(applied) + 1)):
                raise RuntimeError("remediation migration sequence differs")
            for version, digest, statements in MIGRATIONS:
                if version in applied:
                    for expected in statements:
                        parts = expected.split()
                        kind = "index" if parts[1] == "UNIQUE" else "table"
                        name = parts[3] if kind == "index" else parts[2]
                        row = connection.execute(
                            "SELECT sql FROM sqlite_master WHERE type = ? AND name = ?",
                            (kind, name),
                        ).fetchone()
                        if row is None or row[0] != expected:
                            raise RuntimeError("remediation table identity differs")
                else:
                    for statement in statements:
                        connection.execute(statement)
                    connection.execute(
                        "INSERT INTO remediation_schema_migrations VALUES (?, ?)",
                        (version, digest),
                    )
            connection.execute("COMMIT")
        except Exception:
            connection.execute("ROLLBACK")
            raise
