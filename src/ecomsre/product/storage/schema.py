"""SQLite schema identity."""

SCHEMA_VERSION = 1

REQUIRED_TABLES = (
    "schema_migrations",
    "environments",
    "connector_configs",
    "services",
    "baseline_versions",
    "incidents",
    "diagnosis_jobs",
    "job_events",
    "diagnosis_results",
    "evidence_objects",
    "incident_fingerprints",
    "fault_families",
    "fault_family_members",
    "human_reviews",
    "registration_drafts",
    "shadow_evaluations",
    "environment_extension_registrations",
    "promotion_records",
    "revocation_records",
)

__all__ = ("REQUIRED_TABLES", "SCHEMA_VERSION")
