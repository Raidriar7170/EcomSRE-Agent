"""Fail-closed job lease checks for transactions with durable side effects."""

from __future__ import annotations

import sqlite3
import time

from ecomsre.product.errors import ProductError
from ecomsre.product.jobs.contracts import JobLeaseFenceV1, ProductJobStatusV1


def require_live_job_fence(
    connection: sqlite3.Connection,
    fence: JobLeaseFenceV1 | None,
) -> None:
    if fence is None:
        return
    checked_at = time.time() if fence.checked_at is None else fence.checked_at
    row = connection.execute(
        """SELECT status, claimed_by, attempt_count, lease_expires_at
           FROM diagnosis_jobs WHERE job_id = ?""",
        (fence.job_id,),
    ).fetchone()
    if (
        row is None
        or row["status"] != ProductJobStatusV1.RUNNING.value
        or row["claimed_by"] != fence.claimed_by
        or row["attempt_count"] != fence.attempt_count
        or row["lease_expires_at"] is None
        or row["lease_expires_at"] <= checked_at
    ):
        raise ProductError(
            "JOB_LEASE_LOST",
            "The worker no longer owns this job lease.",
            status_code=409,
        )


__all__ = ("require_live_job_fence",)
