"""Transactional SQLite job leases."""

from __future__ import annotations

import json
import re
import time
from typing import Any

from ecomsre.product.errors import ProductError, not_found
from ecomsre.product.ids import new_product_id
from ecomsre.product.jobs.contracts import (
    ProductJobRecordV1,
    ProductJobStatusV1,
    ProductJobTypeV1,
)
from ecomsre.product.incidents.diagnosis_pipeline_v02322 import (
    DiagnosisPublicFailureProjectionV02322,
)
from ecomsre.product.storage.sqlite_store import SqliteStoreV1


def _json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class JobRepositoryV1:
    def __init__(self, store: SqliteStoreV1) -> None:
        self.store = store

    def enqueue(
        self,
        job_type: ProductJobTypeV1,
        payload: dict[str, Any],
        *,
        idempotency_key: str | None = None,
        now: float | None = None,
    ) -> ProductJobRecordV1:
        timestamp = time.time() if now is None else now
        job_id = new_product_id("job")
        serialized_payload = _json(payload)
        with self.store.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                if idempotency_key is not None:
                    existing = connection.execute(
                        "SELECT job_id, payload_json FROM diagnosis_jobs "
                        "WHERE job_type = ? AND idempotency_key = ?",
                        (job_type.value, idempotency_key),
                    ).fetchone()
                    if existing is not None:
                        if existing["payload_json"] != serialized_payload:
                            raise ProductError(
                                "JOB_IDEMPOTENCY_CONFLICT",
                                "The idempotency key is already bound to a different payload.",
                                status_code=409,
                            )
                        connection.execute("COMMIT")
                        return self.get(existing["job_id"])
                connection.execute(
                    """INSERT INTO diagnosis_jobs(
                        job_id, job_type, status, payload_json, result_json,
                        safe_error_code, idempotency_key, claimed_by,
                        lease_expires_at, attempt_count, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, NULL, NULL, ?, NULL, NULL, 0, ?, ?)""",
                    (
                        job_id,
                        job_type.value,
                        ProductJobStatusV1.PENDING.value,
                        serialized_payload,
                        idempotency_key,
                        timestamp,
                        timestamp,
                    ),
                )
                self._append_event(
                    connection,
                    job_id,
                    "ENQUEUED",
                    {"job_type": job_type.value},
                    timestamp,
                )
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise
        return self.get(job_id)

    def claim_next(
        self,
        worker_id: str,
        *,
        lease_seconds: int,
        now: float | None = None,
    ) -> ProductJobRecordV1 | None:
        timestamp = time.time() if now is None else now
        lease_expires_at = timestamp + lease_seconds
        with self.store.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    """SELECT job_id FROM diagnosis_jobs
                       WHERE status = ?
                          OR (status = ? AND lease_expires_at <= ?)
                       ORDER BY created_at, job_id LIMIT 1""",
                    (
                        ProductJobStatusV1.PENDING.value,
                        ProductJobStatusV1.RUNNING.value,
                        timestamp,
                    ),
                ).fetchone()
                if row is None:
                    connection.execute("COMMIT")
                    return None
                job_id = row["job_id"]
                connection.execute(
                    """UPDATE diagnosis_jobs
                       SET status = ?, claimed_by = ?, lease_expires_at = ?,
                           attempt_count = attempt_count + 1, updated_at = ?
                       WHERE job_id = ?""",
                    (
                        ProductJobStatusV1.RUNNING.value,
                        worker_id,
                        lease_expires_at,
                        timestamp,
                        job_id,
                    ),
                )
                self._append_event(
                    connection,
                    job_id,
                    "CLAIMED",
                    {"worker_id": worker_id},
                    timestamp,
                )
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise
        return self.get(job_id)

    def succeed(
        self,
        job_id: str,
        worker_id: str,
        attempt_count: int,
        result: dict[str, Any],
        *,
        now: float | None = None,
    ) -> ProductJobRecordV1:
        return self._finish(
            job_id,
            worker_id,
            attempt_count,
            ProductJobStatusV1.SUCCEEDED,
            result=result,
            safe_error_code=None,
            now=now,
        )

    def renew_lease(
        self,
        job_id: str,
        worker_id: str,
        attempt_count: int,
        *,
        lease_seconds: int,
        now: float | None = None,
    ) -> None:
        timestamp = time.time() if now is None else now
        with self.store.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                cursor = connection.execute(
                    """UPDATE diagnosis_jobs
                       SET lease_expires_at = ?, updated_at = ?
                       WHERE job_id = ? AND status = ? AND claimed_by = ?
                         AND attempt_count = ? AND lease_expires_at > ?""",
                    (
                        timestamp + lease_seconds,
                        timestamp,
                        job_id,
                        ProductJobStatusV1.RUNNING.value,
                        worker_id,
                        attempt_count,
                        timestamp,
                    ),
                )
                if cursor.rowcount != 1:
                    raise ProductError(
                        "JOB_LEASE_LOST",
                        "The worker no longer owns this job lease.",
                        status_code=409,
                    )
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise

    def fail(
        self,
        job_id: str,
        worker_id: str,
        attempt_count: int,
        safe_error_code: str,
        *,
        public_failure_v02322: DiagnosisPublicFailureProjectionV02322 | None = None,
        now: float | None = None,
    ) -> ProductJobRecordV1:
        if not re.fullmatch(r"[A-Z][A-Z0-9_]{0,95}", safe_error_code):
            raise ValueError("safe job error code is invalid")
        return self._finish(
            job_id,
            worker_id,
            attempt_count,
            ProductJobStatusV1.FAILED,
            result=None,
            safe_error_code=safe_error_code,
            public_failure_v02322=public_failure_v02322,
            now=now,
        )

    def _finish(
        self,
        job_id: str,
        worker_id: str,
        attempt_count: int,
        status: ProductJobStatusV1,
        *,
        result: dict[str, Any] | None,
        safe_error_code: str | None,
        public_failure_v02322: DiagnosisPublicFailureProjectionV02322 | None = None,
        now: float | None = None,
    ) -> ProductJobRecordV1:
        timestamp = time.time() if now is None else now
        with self.store.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                cursor = connection.execute(
                    """UPDATE diagnosis_jobs
                       SET status = ?, result_json = ?, safe_error_code = ?,
                           failure_stage = ?, exception_fingerprint = ?,
                           journal_tail_sha256 = ?, claimed_by = NULL,
                           lease_expires_at = NULL, updated_at = ?
                       WHERE job_id = ? AND status = ? AND claimed_by = ?
                         AND attempt_count = ? AND lease_expires_at > ?""",
                    (
                        status.value,
                        None if result is None else _json(result),
                        safe_error_code,
                        (
                            None
                            if public_failure_v02322 is None
                            else public_failure_v02322.failure_stage.value
                        ),
                        (
                            None
                            if public_failure_v02322 is None
                            else public_failure_v02322.exception_fingerprint
                        ),
                        (
                            None
                            if public_failure_v02322 is None
                            else public_failure_v02322.journal_tail_sha256
                        ),
                        timestamp,
                        job_id,
                        ProductJobStatusV1.RUNNING.value,
                        worker_id,
                        attempt_count,
                        timestamp,
                    ),
                )
                if cursor.rowcount != 1:
                    raise ProductError(
                        "JOB_LEASE_LOST",
                        "The worker no longer owns this job lease.",
                        status_code=409,
                    )
                self._append_event(
                    connection,
                    job_id,
                    status.value,
                    {"safe_error_code": safe_error_code} if safe_error_code else {},
                    timestamp,
                )
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise
        return self.get(job_id)

    def get(self, job_id: str) -> ProductJobRecordV1:
        with self.store.connect() as connection:
            row = connection.execute(
                "SELECT * FROM diagnosis_jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
        if row is None:
            raise not_found("JOB_NOT_FOUND", "The requested job does not exist.")
        return ProductJobRecordV1(
            job_id=row["job_id"],
            job_type=row["job_type"],
            status=row["status"],
            payload=json.loads(row["payload_json"]),
            result=None if row["result_json"] is None else json.loads(row["result_json"]),
            safe_error_code=row["safe_error_code"],
            failure_stage=row["failure_stage"],
            exception_fingerprint=row["exception_fingerprint"],
            journal_tail_sha256=row["journal_tail_sha256"],
            idempotency_key=row["idempotency_key"],
            claimed_by=row["claimed_by"],
            lease_expires_at=row["lease_expires_at"],
            attempt_count=row["attempt_count"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def _append_event(
        self,
        connection: Any,
        job_id: str,
        event_type: str,
        details: dict[str, Any],
        now: float,
    ) -> None:
        serialized = _json(details)
        if re.search(r"authorization|password|secret|token", serialized, re.I):
            raise ValueError("job event contains a forbidden secret-bearing field")
        connection.execute(
            """INSERT INTO job_events(event_id, job_id, event_type, details_json, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (new_product_id("event"), job_id, event_type, serialized, now),
        )


__all__ = ("JobRepositoryV1",)
