"""Separate SQLite-backed Product worker process."""

from __future__ import annotations

import os
import secrets
import time

from ecomsre.product.environment.repository import EnvironmentRepositoryV1
from ecomsre.product.errors import ProductError
from ecomsre.product.jobs.contracts import ProductJobTypeV1
from ecomsre.product.jobs.handlers import handle_fixture_environment_verify
from ecomsre.product.jobs.repository import JobRepositoryV1
from ecomsre.product.settings import ProductSettingsV1
from ecomsre.product.storage.sqlite_store import SqliteStoreV1


def run_one_job(
    settings: ProductSettingsV1,
    *,
    worker_id: str,
    now: float | None = None,
) -> bool:
    store = SqliteStoreV1(settings.sqlite_path)
    jobs = JobRepositoryV1(store)
    environments = EnvironmentRepositoryV1(store)
    job = jobs.claim_next(
        worker_id,
        lease_seconds=settings.job_lease_seconds,
        now=now,
    )
    if job is None:
        return False
    try:
        if job.job_type is ProductJobTypeV1.ENVIRONMENT_VERIFY:
            result = handle_fixture_environment_verify(job, environments)
        else:
            raise ProductError(
                "INTERNAL_CONTRACT_FAILURE",
                "No handler is registered for this job type.",
            )
        jobs.succeed(
            job.job_id,
            worker_id,
            job.attempt_count,
            result,
            now=now,
        )
    except ProductError as exc:
        if exc.code == "JOB_LEASE_LOST":
            return True
        try:
            jobs.fail(
                job.job_id,
                worker_id,
                job.attempt_count,
                exc.code,
                now=now,
            )
        except ProductError as finish_error:
            if finish_error.code != "JOB_LEASE_LOST":
                raise
    except Exception:
        try:
            jobs.fail(
                job.job_id,
                worker_id,
                job.attempt_count,
                "INTERNAL_CONTRACT_FAILURE",
                now=now,
            )
        except ProductError as finish_error:
            if finish_error.code != "JOB_LEASE_LOST":
                raise
    return True


def run_worker(settings: ProductSettingsV1) -> None:
    worker_id = f"worker-{os.getpid()}-{secrets.token_hex(4)}"
    while True:
        worked = run_one_job(settings, worker_id=worker_id)
        if not worked:
            time.sleep(settings.worker_poll_seconds)


def main() -> None:
    run_worker(ProductSettingsV1.from_environment())


if __name__ == "__main__":
    main()


__all__ = ("main", "run_one_job", "run_worker")
