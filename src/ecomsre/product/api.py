"""Increment 1 Product HTTP routes."""

from __future__ import annotations

import re

from fastapi import APIRouter, Depends, Header, Request, Response, status
from fastapi.responses import PlainTextResponse

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
from ecomsre.product.pilot.baseline_audit_v021 import BaselineReadinessAuditV021
from ecomsre.product.environment.capabilities import EnvironmentCapabilityMatrixV1
from ecomsre.product.incidents.contracts import (
    DiagnosisResultV1,
    EvidenceBundleV1,
    IncidentCreateV1,
    IncidentRecordV1,
)
from ecomsre.product.knowledge.contracts import (
    FamilyRegistrationDraftV1,
    FaultFamilyListV1,
    FaultFamilyMergeV1,
    FaultFamilyV1,
    HumanReviewCreateV1,
    HumanReviewV1,
    PromotionCreateV1,
    PromotionRecordV1,
    RegistrationDraftCreateV1,
    RevocationCreateV1,
    RevocationRecordV1,
    ShadowEvaluationCreateV1,
    ShadowEvaluationV1,
)


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


@router.get("/metrics", response_class=PlainTextResponse)
def metrics(request: Request) -> PlainTextResponse:
    return PlainTextResponse(
        request.app.state.metrics.render(),
        media_type="text/plain; version=0.0.4",
    )


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


@router.get(
    "/v1/environments/{environment_id}/baseline-readiness",
    response_model=BaselineReadinessAuditV021,
)
def get_environment_baseline_readiness(
    request: Request,
    environment_id: str,
) -> BaselineReadinessAuditV021:
    request.app.state.environments.get(environment_id)
    return request.app.state.baseline_readiness_audits.get_latest(environment_id)


@router.get(
    "/v1/baselines/{baseline_id}/window-audit",
    response_model=BaselineReadinessAuditV021,
)
def get_baseline_window_audit(
    request: Request,
    baseline_id: str,
) -> BaselineReadinessAuditV021:
    return request.app.state.baseline_readiness_audits.get_by_baseline(baseline_id)


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


@router.post(
    "/v1/incidents",
    dependencies=[Depends(require_mutation_auth)],
    response_model=IncidentRecordV1,
    status_code=status.HTTP_201_CREATED,
)
def create_incident(
    request: Request,
    payload: IncidentCreateV1,
) -> IncidentRecordV1:
    return request.app.state.incidents.create(payload)


@router.get("/v1/incidents/{incident_id}", response_model=IncidentRecordV1)
def get_incident(request: Request, incident_id: str) -> IncidentRecordV1:
    return request.app.state.incidents.get(incident_id)


@router.post(
    "/v1/incidents/{incident_id}/diagnosis-jobs",
    dependencies=[Depends(require_mutation_auth)],
    response_model=ProductJobRecordV1,
    status_code=status.HTTP_202_ACCEPTED,
)
def create_diagnosis_job(
    request: Request,
    incident_id: str,
) -> ProductJobRecordV1:
    request.app.state.incidents.get(incident_id)
    return request.app.state.jobs.enqueue(
        ProductJobTypeV1.DIAGNOSIS,
        {"incident_id": incident_id},
        idempotency_key=f"diagnosis:{incident_id}",
    )


@router.get(
    "/v1/incidents/{incident_id}/diagnosis",
    response_model=DiagnosisResultV1,
)
def get_diagnosis(request: Request, incident_id: str) -> DiagnosisResultV1:
    request.app.state.incidents.get(incident_id)
    return request.app.state.diagnoses.get(incident_id)


@router.get(
    "/v1/incidents/{incident_id}/evidence",
    response_model=EvidenceBundleV1,
)
def get_evidence(request: Request, incident_id: str) -> EvidenceBundleV1:
    request.app.state.incidents.get(incident_id)
    return request.app.state.diagnoses.evidence(incident_id)


@router.get(
    "/v1/environments/{environment_id}/fault-families",
    response_model=FaultFamilyListV1,
)
def list_fault_families(request: Request, environment_id: str) -> FaultFamilyListV1:
    request.app.state.environments.get(environment_id)
    return request.app.state.knowledge.list_families(environment_id)


@router.get("/v1/fault-families/{family_id}", response_model=FaultFamilyV1)
def get_fault_family(request: Request, family_id: str) -> FaultFamilyV1:
    return request.app.state.knowledge.get_family(family_id)


@router.post(
    "/v1/fault-families/{family_id}/reviews",
    dependencies=[Depends(require_mutation_auth)],
    response_model=HumanReviewV1,
    status_code=status.HTTP_201_CREATED,
)
def review_fault_family(
    request: Request,
    family_id: str,
    payload: HumanReviewCreateV1,
) -> HumanReviewV1:
    return request.app.state.knowledge.review(family_id, payload)


@router.post(
    "/v1/fault-families/{family_id}/merge",
    dependencies=[Depends(require_mutation_auth)],
    response_model=FaultFamilyV1,
)
def merge_fault_family(
    request: Request,
    family_id: str,
    payload: FaultFamilyMergeV1,
) -> FaultFamilyV1:
    return request.app.state.knowledge.merge(family_id, payload)


@router.post(
    "/v1/fault-families/{family_id}/registration-drafts",
    dependencies=[Depends(require_mutation_auth)],
    response_model=FamilyRegistrationDraftV1,
    status_code=status.HTTP_201_CREATED,
)
def create_registration_draft(
    request: Request,
    family_id: str,
    payload: RegistrationDraftCreateV1,
) -> FamilyRegistrationDraftV1:
    return request.app.state.knowledge.create_registration_draft(family_id, payload)


@router.get(
    "/v1/registrations/{registration_id}",
    response_model=FamilyRegistrationDraftV1,
)
def get_registration(
    request: Request,
    registration_id: str,
) -> FamilyRegistrationDraftV1:
    return request.app.state.knowledge.get_registration(registration_id)


@router.post(
    "/v1/registrations/{registration_id}/shadow-evaluation-jobs",
    dependencies=[Depends(require_mutation_auth)],
    response_model=ShadowEvaluationV1,
    status_code=status.HTTP_201_CREATED,
)
def create_shadow_evaluation(
    request: Request,
    registration_id: str,
    _payload: ShadowEvaluationCreateV1,
) -> ShadowEvaluationV1:
    return request.app.state.knowledge.create_shadow_evaluation(registration_id)


@router.post(
    "/v1/registrations/{registration_id}/promotions",
    dependencies=[Depends(require_mutation_auth)],
    response_model=PromotionRecordV1,
    status_code=status.HTTP_201_CREATED,
)
def promote_registration(
    request: Request,
    registration_id: str,
    payload: PromotionCreateV1,
) -> PromotionRecordV1:
    result = request.app.state.knowledge.promote(registration_id, payload)
    request.app.state.metrics.increment(
        "ecomsre_registration_promotions_total",
        {"environment_id": result.environment_id, "status": "ACTIVE"},
    )
    return result


@router.post(
    "/v1/registrations/{registration_id}/revocations",
    dependencies=[Depends(require_mutation_auth)],
    response_model=RevocationRecordV1,
    status_code=status.HTTP_201_CREATED,
)
def revoke_registration(
    request: Request,
    registration_id: str,
    payload: RevocationCreateV1,
) -> RevocationRecordV1:
    return request.app.state.knowledge.revoke(registration_id, payload)


@router.get("/v1/jobs/{job_id}", response_model=ProductJobRecordV1)
def get_job(request: Request, job_id: str) -> ProductJobRecordV1:
    return request.app.state.jobs.get(job_id)


__all__ = ("router",)
