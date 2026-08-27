"""Incremental Product job handlers."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from ecomsre.product.baselines import (
    BaselineJobCreateV1,
    HistoricalBaselineServiceV1,
)
from ecomsre.product.environment.capabilities import CapabilityMatrixRepositoryV1
from ecomsre.product.environment.repository import EnvironmentRepositoryV1
from ecomsre.product.environment.services import ServiceCatalogRepositoryV1
from ecomsre.product.environment.verification import EnvironmentVerificationServiceV1
from ecomsre.product.errors import ProductError
from ecomsre.product.jobs.contracts import JobLeaseFenceV1, ProductJobRecordV1


def handle_fixture_environment_verify(
    job: ProductJobRecordV1,
    environments: EnvironmentRepositoryV1,
) -> dict[str, Any]:
    if job.payload.get("fixture") is not True:
        raise ProductError(
            "CONNECTOR_UNAVAILABLE",
            "The Increment 1 worker only supports fixture verification.",
        )
    environment_id = str(job.payload.get("environment_id", ""))
    environment = environments.get(environment_id)
    if not any(connector.kind.value == "FIXTURE" for connector in environment.connector_configs):
        raise ProductError(
            "CONNECTOR_UNAVAILABLE",
            "The environment has no fixture connector.",
        )
    return {"environment_id": environment_id, "fixture_verified": True}


def handle_environment_verify(
    job: ProductJobRecordV1,
    environments: EnvironmentRepositoryV1,
    verification: EnvironmentVerificationServiceV1,
    *,
    fence: JobLeaseFenceV1 | None = None,
) -> dict[str, Any]:
    environment_id = str(job.payload.get("environment_id", ""))
    environment = environments.get(environment_id)
    result = verification.verify(environment, fence=fence)
    return result.model_dump(mode="json")


def handle_baseline_build(
    job: ProductJobRecordV1,
    environments: EnvironmentRepositoryV1,
    services: ServiceCatalogRepositoryV1,
    capabilities: CapabilityMatrixRepositoryV1,
    baselines: HistoricalBaselineServiceV1,
    *,
    fence: JobLeaseFenceV1 | None = None,
) -> dict[str, Any]:
    environment_id = str(job.payload.get("environment_id", ""))
    raw_request = job.payload.get("request")
    if not isinstance(raw_request, dict):
        raise ProductError(
            "INVALID_REQUEST",
            "The baseline job payload is invalid.",
        )
    request = BaselineJobCreateV1.model_validate(raw_request)
    result = baselines.build(
        environment=environments.get(environment_id),
        identity_map=services.get_map(environment_id),
        capability_matrix=capabilities.get(environment_id),
        request=request,
        baseline_id=f"base-{job.job_id.removeprefix('job-')}",
        built_at=datetime.fromtimestamp(job.created_at, UTC),
        fence=fence,
    )
    return result.model_dump(mode="json")


__all__ = (
    "handle_baseline_build",
    "handle_environment_verify",
    "handle_fixture_environment_verify",
)
