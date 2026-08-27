"""Incremental Product job handlers."""

from __future__ import annotations

from typing import Any

from ecomsre.product.environment.repository import EnvironmentRepositoryV1
from ecomsre.product.errors import ProductError
from ecomsre.product.jobs.contracts import ProductJobRecordV1


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


__all__ = ("handle_fixture_environment_verify",)
