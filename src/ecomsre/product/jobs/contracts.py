"""Product job queue contracts."""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import Field

from ecomsre.product.contracts import ProductModelV1


class ProductJobTypeV1(str, Enum):
    ENVIRONMENT_VERIFY = "ENVIRONMENT_VERIFY"
    BASELINE_BUILD = "BASELINE_BUILD"
    DIAGNOSIS = "DIAGNOSIS"
    FAULT_FAMILY_RECLUSTER = "FAULT_FAMILY_RECLUSTER"
    REGISTRATION_SHADOW_EVALUATION = "REGISTRATION_SHADOW_EVALUATION"


class ProductJobStatusV1(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class ProductJobRecordV1(ProductModelV1):
    schema_version: str = "ecomsre.product.job.v1"
    job_id: str = Field(pattern=r"^job-[0-9a-f]{24}$")
    job_type: ProductJobTypeV1
    status: ProductJobStatusV1
    payload: dict[str, Any]
    result: dict[str, Any] | None = None
    safe_error_code: str | None = None
    idempotency_key: str | None = None
    claimed_by: str | None = None
    lease_expires_at: float | None = None
    attempt_count: int
    created_at: float
    updated_at: float


class JobLeaseFenceV1(ProductModelV1):
    """The exact worker attempt authorized to commit durable job side effects."""

    job_id: str = Field(pattern=r"^job-[0-9a-f]{24}$")
    claimed_by: str = Field(min_length=1, max_length=200)
    attempt_count: int = Field(ge=1)
    checked_at: float | None = None


__all__ = (
    "JobLeaseFenceV1",
    "ProductJobRecordV1",
    "ProductJobStatusV1",
    "ProductJobTypeV1",
)
