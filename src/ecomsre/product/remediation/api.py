"""Explicit admin-authorized approval workflow; no attempt or execution endpoint."""

from typing import Annotated

from fastapi import APIRouter, Depends, Header, Path, Request

from ecomsre.product.auth import require_mutation_auth
from ecomsre.product.errors import ProductError
from ecomsre.product.remediation.approval import (
    ApprovalRequestV1,
    ApprovalRevocationV1,
    ApprovalStatusV1,
    OperatorApprovalV1,
    RevocationRequestV1,
)
from ecomsre.product.remediation.contracts import (
    CandidateProjectionV1,
    RemediationCandidateV1,
)
from ecomsre.product.remediation.repository import RemediationRepositoryV1


router = APIRouter(tags=["remediation"])
CandidateId = Annotated[str, Path(pattern=r"^cand-[0-9a-f]{24}$")]
IncidentId = Annotated[str, Path(pattern=r"^inc-[0-9a-f]{24}$")]
ApprovalId = Annotated[str, Path(pattern=r"^appr-[0-9a-f]{24}$")]
Key = Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=128)]


def require_remediation_auth(
    request: Request, authorization: str | None = Header(default=None)
) -> None:
    if request.app.state.settings.resolved_admin_token() is None:
        raise ProductError(
            "REMEDIATION_ADMIN_REQUIRED",
            "Remediation mutations require a configured admin token.",
            status_code=403,
        )
    require_mutation_auth(request, authorization)


def repository(request: Request) -> RemediationRepositoryV1:
    return request.app.state.remediation


@router.get(
    "/v1/incidents/{incident_id}/remediation-candidates",
    response_model=CandidateProjectionV1,
)
def project_candidates(
    incident_id: IncidentId, request: Request
) -> CandidateProjectionV1:
    return repository(request).project(incident_id)


@router.post(
    "/v1/incidents/{incident_id}/remediation-candidates",
    response_model=CandidateProjectionV1,
    dependencies=[Depends(require_remediation_auth)],
)
def create_candidates(
    incident_id: IncidentId, request: Request, key: Key
) -> CandidateProjectionV1:
    return repository(request).create_candidates(incident_id, key)


@router.get(
    "/v1/remediation-candidates/{candidate_id}", response_model=RemediationCandidateV1
)
def get_candidate(
    candidate_id: CandidateId, request: Request
) -> RemediationCandidateV1:
    return repository(request).get_candidate(candidate_id)


@router.post(
    "/v1/remediation-candidates/{candidate_id}/approvals",
    response_model=OperatorApprovalV1,
    dependencies=[Depends(require_remediation_auth)],
)
def approve(
    candidate_id: CandidateId, body: ApprovalRequestV1, request: Request, key: Key
) -> OperatorApprovalV1:
    return repository(request).approve(candidate_id, body, key)


@router.post(
    "/v1/remediation-candidates/{candidate_id}/revocations",
    response_model=ApprovalRevocationV1,
    dependencies=[Depends(require_remediation_auth)],
)
def revoke(
    candidate_id: CandidateId, body: RevocationRequestV1, request: Request, key: Key
) -> ApprovalRevocationV1:
    return repository(request).revoke(candidate_id, body, key)


@router.get("/v1/remediation-approvals/{approval_id}", response_model=ApprovalStatusV1)
def approval_status(approval_id: ApprovalId, request: Request) -> ApprovalStatusV1:
    return repository(request).approval_status(approval_id)
