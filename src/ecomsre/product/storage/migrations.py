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
    (
        2,
        "connector-gateway-v1",
        (
            "ALTER TABLE services ADD COLUMN logical_service TEXT",
            "UPDATE services SET logical_service = "
            "json_extract(payload_json, '$.logical_service') "
            "WHERE logical_service IS NULL",
            "CREATE UNIQUE INDEX services_environment_logical_idx "
            "ON services(environment_id, logical_service)",
            """CREATE TABLE environment_capability_matrices (
                environment_id TEXT PRIMARY KEY REFERENCES environments(environment_id)
                    ON DELETE CASCADE,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )""",
            """CREATE TABLE change_events (
                change_event_id TEXT PRIMARY KEY,
                environment_id TEXT NOT NULL REFERENCES environments(environment_id)
                    ON DELETE CASCADE,
                external_change_id TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(environment_id, external_change_id)
            )""",
            "CREATE UNIQUE INDEX baseline_versions_one_active_idx "
            "ON baseline_versions(environment_id) WHERE active = 1",
        ),
    ),
    (
        3,
        "incident-diagnosis-v1",
        (
            "CREATE UNIQUE INDEX diagnosis_results_incident_idx "
            "ON diagnosis_results(incident_id)",
            """CREATE TABLE diagnosis_evidence_links (
                diagnosis_id TEXT NOT NULL REFERENCES diagnosis_results(diagnosis_id),
                incident_id TEXT NOT NULL REFERENCES incidents(incident_id),
                object_sha256 TEXT NOT NULL REFERENCES evidence_objects(object_sha256),
                evidence_ref TEXT NOT NULL,
                source TEXT NOT NULL,
                action_id TEXT NOT NULL,
                role TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY(diagnosis_id, evidence_ref)
            )""",
            """CREATE TABLE product_metric_counters (
                metric_name TEXT NOT NULL,
                labels_json TEXT NOT NULL,
                value INTEGER NOT NULL CHECK(value >= 0),
                updated_at TEXT NOT NULL,
                PRIMARY KEY(metric_name, labels_json)
            )""",
        ),
    ),
    (
        4,
        "environment-knowledge-loop-v1",
        (
            "CREATE UNIQUE INDEX incident_fingerprints_incident_idx "
            "ON incident_fingerprints(incident_id)",
            """CREATE TABLE predicate_matrices (
                predicate_matrix_sha256 TEXT PRIMARY KEY,
                family_id TEXT NOT NULL REFERENCES fault_families(family_id),
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )""",
            """CREATE TABLE environment_extension_registry_versions (
                environment_id TEXT NOT NULL REFERENCES environments(environment_id),
                registry_version INTEGER NOT NULL CHECK(registry_version >= 1),
                registration_id TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('ACTIVE', 'REVOKED')),
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY(environment_id, registry_version)
            )""",
            "CREATE INDEX environment_extension_registry_versions_registration_idx "
            "ON environment_extension_registry_versions(registration_id, registry_version)",
        ),
    ),
    (
        5,
        "product-v02-live-pilot",
        (
            """CREATE TABLE live_pilot_episodes_v02 (
                episode_id TEXT PRIMARY KEY,
                environment_id TEXT NOT NULL,
                role TEXT NOT NULL,
                episode_sha256 TEXT NOT NULL UNIQUE,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )""",
            "CREATE INDEX live_pilot_episodes_v02_environment_role_idx "
            "ON live_pilot_episodes_v02(environment_id, role, created_at)",
            """CREATE TABLE live_pilot_attempt_events_v02 (
                event_id TEXT PRIMARY KEY,
                attempt_id TEXT NOT NULL,
                slot_id TEXT NOT NULL,
                role TEXT NOT NULL,
                attempt_number INTEGER NOT NULL CHECK(attempt_number >= 1),
                sequence INTEGER NOT NULL CHECK(sequence >= 1),
                stage TEXT NOT NULL,
                attempt_signature_sha256 TEXT NOT NULL,
                event_sha256 TEXT NOT NULL UNIQUE,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(attempt_id, sequence)
            )""",
            "CREATE UNIQUE INDEX live_pilot_attempt_events_v02_slot_attempt_idx "
            "ON live_pilot_attempt_events_v02(slot_id, attempt_number) WHERE sequence = 1",
            "CREATE INDEX live_pilot_attempt_events_v02_slot_idx "
            "ON live_pilot_attempt_events_v02(slot_id, attempt_number, sequence)",
        ),
    ),
    (
        6,
        "product-v021-baseline-readiness-audit",
        (
            """CREATE TABLE baseline_readiness_audits_v021 (
                audit_sha256 TEXT PRIMARY KEY,
                environment_id TEXT NOT NULL REFERENCES environments(environment_id),
                baseline_id TEXT NOT NULL UNIQUE,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )""",
            "CREATE INDEX baseline_readiness_audits_v021_environment_idx "
            "ON baseline_readiness_audits_v021(environment_id, created_at)",
        ),
    ),
)

__all__ = ("MIGRATIONS",)
