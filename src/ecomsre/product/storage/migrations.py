"""Append-only SQLite migrations."""

from __future__ import annotations


MIGRATIONS: tuple[tuple[int, str, tuple[str, ...]], ...] = (
    (
        1,
        "product-shell-v1",
        (
            """CREATE TABLE IF NOT EXISTS schema_migrations (
                version INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                applied_at TEXT NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS environments (
                environment_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                description TEXT NOT NULL,
                timezone TEXT NOT NULL,
                service_identity_policy_json TEXT NOT NULL,
                explicit_service_catalog_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS connector_configs (
                connector_config_id TEXT PRIMARY KEY,
                environment_id TEXT NOT NULL REFERENCES environments(environment_id)
                    ON DELETE CASCADE,
                name TEXT NOT NULL,
                kind TEXT NOT NULL,
                endpoint TEXT,
                settings_json TEXT NOT NULL,
                credential_refs_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(environment_id, name)
            )""",
            """CREATE TABLE IF NOT EXISTS services (
                service_id TEXT PRIMARY KEY,
                environment_id TEXT NOT NULL REFERENCES environments(environment_id),
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS baseline_versions (
                baseline_id TEXT PRIMARY KEY,
                environment_id TEXT NOT NULL REFERENCES environments(environment_id),
                payload_json TEXT NOT NULL,
                active INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS incidents (
                incident_id TEXT PRIMARY KEY,
                environment_id TEXT NOT NULL REFERENCES environments(environment_id),
                external_incident_key TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(environment_id, external_incident_key)
            )""",
            """CREATE TABLE IF NOT EXISTS diagnosis_jobs (
                job_id TEXT PRIMARY KEY,
                job_type TEXT NOT NULL,
                status TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                result_json TEXT,
                safe_error_code TEXT,
                idempotency_key TEXT,
                claimed_by TEXT,
                lease_expires_at REAL,
                attempt_count INTEGER NOT NULL DEFAULT 0,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                UNIQUE(job_type, idempotency_key)
            )""",
            """CREATE INDEX IF NOT EXISTS diagnosis_jobs_claim_idx
                ON diagnosis_jobs(status, lease_expires_at, created_at)""",
            """CREATE TABLE IF NOT EXISTS job_events (
                event_id TEXT PRIMARY KEY,
                job_id TEXT NOT NULL REFERENCES diagnosis_jobs(job_id),
                event_type TEXT NOT NULL,
                details_json TEXT NOT NULL,
                created_at REAL NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS diagnosis_results (
                diagnosis_id TEXT PRIMARY KEY,
                incident_id TEXT NOT NULL REFERENCES incidents(incident_id),
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS evidence_objects (
                object_sha256 TEXT PRIMARY KEY,
                byte_size INTEGER NOT NULL,
                media_type TEXT NOT NULL,
                created_at TEXT NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS incident_fingerprints (
                fingerprint_id TEXT PRIMARY KEY,
                incident_id TEXT NOT NULL REFERENCES incidents(incident_id),
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS fault_families (
                family_id TEXT PRIMARY KEY,
                environment_id TEXT NOT NULL REFERENCES environments(environment_id),
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS fault_family_members (
                family_id TEXT NOT NULL REFERENCES fault_families(family_id),
                fingerprint_id TEXT NOT NULL REFERENCES incident_fingerprints(fingerprint_id),
                created_at TEXT NOT NULL,
                PRIMARY KEY(family_id, fingerprint_id)
            )""",
            """CREATE TABLE IF NOT EXISTS human_reviews (
                review_id TEXT PRIMARY KEY,
                family_id TEXT NOT NULL REFERENCES fault_families(family_id),
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS registration_drafts (
                registration_id TEXT PRIMARY KEY,
                family_id TEXT NOT NULL REFERENCES fault_families(family_id),
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS shadow_evaluations (
                evaluation_id TEXT PRIMARY KEY,
                registration_id TEXT NOT NULL REFERENCES registration_drafts(registration_id),
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS environment_extension_registrations (
                registration_id TEXT PRIMARY KEY,
                environment_id TEXT NOT NULL REFERENCES environments(environment_id),
                payload_json TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS promotion_records (
                promotion_id TEXT PRIMARY KEY,
                registration_id TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS revocation_records (
                revocation_id TEXT PRIMARY KEY,
                registration_id TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )""",
        ),
    ),
)

__all__ = ("MIGRATIONS",)
