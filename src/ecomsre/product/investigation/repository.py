"""Additive investigation journal in the existing Product SQLite and CAS."""

import json
from datetime import UTC, datetime
from typing import Any

from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22
from ecomsre.product.errors import ProductError
from ecomsre.product.jobs.contracts import JobLeaseFenceV1
from ecomsre.product.jobs.fencing import require_live_job_fence
from ecomsre.product.storage.object_store import ContentAddressedObjectStoreV1
from ecomsre.product.storage.sqlite_store import SqliteStoreV1


class InvestigationRepository:
    def __init__(self, store: SqliteStoreV1, objects: ContentAddressedObjectStoreV1):
        self.store, self.objects = store, objects
        with store.connect() as connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS investigation_sessions_v050 (
                    session_id TEXT PRIMARY KEY,
                    incident_id TEXT NOT NULL UNIQUE REFERENCES incidents(incident_id),
                    revision INTEGER NOT NULL,
                    payload_json TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS investigation_events_v050 (
                    session_id TEXT NOT NULL REFERENCES investigation_sessions_v050(session_id),
                    ordinal INTEGER NOT NULL, object_sha256 TEXT NOT NULL,
                    PRIMARY KEY(session_id, ordinal));
                CREATE TABLE IF NOT EXISTS investigation_provider_calls_v050 (
                    call_key TEXT PRIMARY KEY, request_sha256 TEXT NOT NULL,
                    reserved_microusd INTEGER NOT NULL,
                    charged_microusd INTEGER, state TEXT NOT NULL,
                    payload_json TEXT NOT NULL);
            """)

    def get(self, incident_id: str) -> dict[str, Any] | None:
        with self.store.connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM investigation_sessions_v050 WHERE incident_id=?",
                (incident_id,),
            ).fetchone()
        return None if row is None else json.loads(row[0])

    def save(
        self, payload: dict[str, Any], *, expected_revision: int, fence: JobLeaseFenceV1
    ) -> None:
        stored = self.objects.prepare_json(payload)
        with self.store.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                require_live_job_fence(connection, fence)
                current = connection.execute(
                    "SELECT revision FROM investigation_sessions_v050 WHERE session_id=?",
                    (payload["session_id"],),
                ).fetchone()
                revision = -1 if current is None else current[0]
                if revision != expected_revision or payload["revision"] != revision + 1:
                    raise ProductError(
                        "INVESTIGATION_CONFLICT",
                        "Session revision changed.",
                        status_code=409,
                    )
                self.objects.bind_prepared(
                    connection, stored, created_at=datetime.now(UTC)
                )
                connection.execute(
                    "INSERT INTO investigation_sessions_v050 VALUES (?,?,?,?) "
                    "ON CONFLICT(session_id) DO UPDATE SET revision=excluded.revision, "
                    "payload_json=excluded.payload_json",
                    (
                        payload["session_id"],
                        payload["incident_id"],
                        payload["revision"],
                        json.dumps(payload),
                    ),
                )
                connection.execute(
                    "INSERT INTO investigation_events_v050 VALUES (?,?,?)",
                    (payload["session_id"], payload["revision"], stored.object_sha256),
                )
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise

    def reserve(
        self,
        key: str,
        request: dict[str, Any],
        reserve: int,
        *,
        fence: JobLeaseFenceV1 | None = None,
    ) -> dict[str, Any] | None:
        """Charge/reserve all dispatches, including failed or uncertain requests."""
        if type(reserve) is not int or reserve <= 0:
            raise ValueError("positive bounded reservation required")
        digest = semantic_sha256_v22(request)
        with self.store.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                require_live_job_fence(connection, fence)
                row = connection.execute(
                    "SELECT * FROM investigation_provider_calls_v050 WHERE call_key=?",
                    (key,),
                ).fetchone()
                if row is not None:
                    if row["request_sha256"] != digest:
                        raise ProductError(
                            "PROVIDER_REQUEST_DRIFT", "Persisted request differs."
                        )
                    if row["state"] != "COMPLETED":
                        raise ProductError(
                            "PROVIDER_OUTCOME_UNKNOWN",
                            "An earlier dispatch cannot be repeated.",
                        )
                    connection.execute("COMMIT")
                    return json.loads(row["payload_json"])
                if connection.execute(
                    "SELECT 1 FROM investigation_provider_calls_v050 WHERE state='UNSAFE_COST_BOUND' LIMIT 1"
                ).fetchone():
                    raise ProductError(
                        "PROVIDER_COST_BOUND_INVALID",
                        "Prior usage invalidated the cost bound.",
                    )
                if key.startswith("knowledge-draft-v050.1:"):
                    # One explicitly authorized continuation, shared across environments.
                    n, committed = connection.execute(
                        "SELECT COUNT(*),COALESCE(SUM(COALESCE(charged_microusd,reserved_microusd)),0) "
                        "FROM investigation_provider_calls_v050 WHERE call_key LIKE 'knowledge-draft-v050.1:%'"
                    ).fetchone()
                    if n >= 6 or committed + reserve > 1_000_000:
                        raise ProductError("BUDGET_EXHAUSTED", "Draft continuation sublimit reached.")
                if key.startswith("knowledge:"):
                    prefix = ":".join(key.split(":")[:2]) + ":%"
                    proposal_count = connection.execute(
                        "SELECT COUNT(*) FROM investigation_provider_calls_v050 WHERE call_key LIKE ?",
                        (prefix,),
                    ).fetchone()[0]
                    if proposal_count >= 5:
                        raise ProductError(
                            "BUDGET_EXHAUSTED",
                            "Test environment knowledge proposal cap reached.",
                        )
                count, cost = connection.execute(
                    "SELECT COUNT(*), COALESCE(SUM(COALESCE(charged_microusd,reserved_microusd)),0) "
                    "FROM investigation_provider_calls_v050"
                ).fetchone()
                if count >= 200 or cost + reserve > 20_000_000:
                    raise ProductError(
                        "BUDGET_EXHAUSTED", "Campaign request or cost cap reached."
                    )
                connection.execute(
                    "INSERT INTO investigation_provider_calls_v050 VALUES (?,?,?,NULL,'DISPATCHING',?)",
                    (
                        key,
                        digest,
                        reserve,
                        json.dumps({"started_at": datetime.now(UTC).isoformat()}),
                    ),
                )
                connection.execute("COMMIT")
                return None
            except Exception:
                connection.execute("ROLLBACK")
                raise

    def settle(
        self, key: str, payload: dict[str, Any], charge: int | None, state: str
    ) -> None:
        with self.store.connect() as connection:
            cursor = connection.execute(
                "UPDATE investigation_provider_calls_v050 SET payload_json=?,charged_microusd=?,state=? "
                "WHERE call_key=? AND state='DISPATCHING'",
                (json.dumps(payload), charge, state, key),
            )
            if cursor.rowcount != 1:
                raise ProductError(
                    "PROVIDER_LEDGER_CONFLICT", "Dispatch already settled or absent."
                )

    def accounting(self) -> dict[str, Any]:
        with self.store.connect() as connection:
            count, reserved, known, unknown = connection.execute(
                "SELECT COUNT(*),COALESCE(SUM(COALESCE(charged_microusd,reserved_microusd)),0),"
                "COALESCE(SUM(charged_microusd),0),COUNT(*)-COUNT(charged_microusd) "
                "FROM investigation_provider_calls_v050"
            ).fetchone()
        return {
            "request_count": count,
            "committed_upper_microusd": reserved,
            "known_cost_microusd": known,
            "unknown_usage_requests": unknown,
        }
