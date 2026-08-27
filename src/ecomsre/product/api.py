"""Increment 1 Product HTTP routes."""

from __future__ import annotations

import re

from fastapi import APIRouter, Depends, Header, Request, Response, status

from ecomsre.product.auth import require_mutation_auth
from ecomsre.product.baselines import BaselineJobCreateV1, BaselineListV1
from ecomsre.product.changes import ChangeEventCreateV1, ChangeEventRecordV1
from ecomsre.product.contracts import (
    EnvironmentCreateV1,
    EnvironmentListV1,
    EnvironmentRecordV1,
    HealthResultV1,
)
from ecomsre.product.jobs.contracts import ProductJobRecordV1, ProductJobTypeV1
from ecomsre.product.environment.capabilities import EnvironmentCapabilityMatrixV1


router = APIRouter()


@router.get("/healthz", response_model=HealthResultV1)
def healthz() -> HealthResultV1:
    return HealthResultV1(status="ok")


@router.get("/readyz", response_model=HealthResultV1)
def readyz(request: Request, response: Response) -> HealthResultV1:
    if not request.app.state.store.ready():
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return HealthResultV1(status="not-ready")
    return HealthResultV1(status="ready")


@router.post(
    "/v1/environments",
    dependencies=[Depends(require_mutation_auth)],
    response_model=EnvironmentRecordV1,
    status_code=status.HTTP_201_CREATED,
)
def create_environment(
    request: Request,
    payload: EnvironmentCreateV1,
) -> EnvironmentRecordV1:
    return request.app.state.environments.create(payload)


@router.get("/v1/environments", response_model=EnvironmentListV1)
def list_environments(request: Request) -> EnvironmentListV1:
    return EnvironmentListV1(items=request.app.state.environments.list())


@router.get(
    "/v1/environments/{environment_id}",
    response_model=EnvironmentRecordV1,
)
def get_environment(request: Request, environment_id: str) -> EnvironmentRecordV1:
    return request.app.state.environments.get(environment_id)


@router.post(
    "/v1/environments/{environment_id}/verify-jobs",
    dependencies=[Depends(require_mutation_auth)],
    response_model=ProductJobRecordV1,
    status_code=status.HTTP_202_ACCEPTED,
)
def create_environment_verify_job(
    request: Request,
    environment_id: str,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> ProductJobRecordV1:
    environment = request.app.state.environments.get(environment_id)
    if idempotency_key is not None and not re.fullmatch(
        r"[a-zA-Z0-9_.:-]{1,128}",
        idempotency_key,
    ):
        from ecomsre.product.errors import ProductError

        raise ProductError(
            "INVALID_REQUEST",
            "The Idempotency-Key header is invalid.",
        )
    fixture = bool(environment.connector_configs) and all(
        connector.kind.value == "FIXTURE"
        for connector in environment.connector_configs
    )
    return request.app.state.jobs.enqueue(
        ProductJobTypeV1.ENVIRONMENT_VERIFY,
        {"environment_id": environment_id, "fixture": fixture},
        idempotency_key=(
            None
            if idempotency_key is None
            else f"environment-verify:{environment_id}:{idempotency_key}"
        ),
    )


@router.get(
    "/v1/environments/{environment_id}/capabilities",
    response_model=EnvironmentCapabilityMatrixV1,
)
def get_environment_capabilities(
    request: Request,
    environment_id: str,
) -> EnvironmentCapabilityMatrixV1:
    request.app.state.environments.get(environment_id)
    return request.app.state.capabilities.get(environment_id)


@router.post(
    "/v1/environments/{environment_id}/baseline-jobs",
    dependencies=[Depends(require_mutation_auth)],
    response_model=ProductJobRecordV1,
    status_code=status.HTTP_202_ACCEPTED,
)
def create_environment_baseline_job(
    request: Request,
    environment_id: str,
    payload: BaselineJobCreateV1,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> ProductJobRecordV1:
    request.app.state.environments.get(environment_id)
    request.app.state.capabilities.get(environment_id)
    if idempotency_key is not None and not re.fullmatch(
        r"[a-zA-Z0-9_.:-]{1,128}",
        idempotency_key,
    ):
        from ecomsre.product.errors import ProductError

        raise ProductError(
            "INVALID_REQUEST",
            "The Idempotency-Key header is invalid.",
        )
    return request.app.state.jobs.enqueue(
        ProductJobTypeV1.BASELINE_BUILD,
        {
            "environment_id": environment_id,
            "request": payload.model_dump(mode="json"),
        },
        idempotency_key=(
            None
            if idempotency_key is None
            else f"baseline-build:{environment_id}:{idempotency_key}"
        ),
    )


@router.get(
    "/v1/environments/{environment_id}/baselines",
    response_model=BaselineListV1,
)
def list_environment_baselines(
    request: Request,
    environment_id: str,
) -> BaselineListV1:
    request.app.state.environments.get(environment_id)
    return BaselineListV1(items=request.app.state.baselines.list(environment_id))


@router.post(
    "/v1/environments/{environment_id}/changes",
    dependencies=[Depends(require_mutation_auth)],
    response_model=ChangeEventRecordV1,
    status_code=status.HTTP_201_CREATED,
)
def create_environment_change(
    request: Request,
    environment_id: str,
    payload: ChangeEventCreateV1,
) -> ChangeEventRecordV1:
    return request.app.state.changes.create(environment_id, payload)


@router.get("/v1/jobs/{job_id}", response_model=ProductJobRecordV1)
def get_job(request: Request, job_id: str) -> ProductJobRecordV1:
    return request.app.state.jobs.get(job_id)


__all__ = ("router",)
