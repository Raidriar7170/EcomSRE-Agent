"""Immutable operator approval and append-only revocation contracts."""

from datetime import datetime, timedelta
from typing import Literal, Self

from pydantic import ConfigDict, Field, model_validator

from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22
from ecomsre.product.contracts import ProductModelV1
from ecomsre.product.remediation.contracts import SealedRemediationModelV1, Sha256


class ApprovalScopeV1(ProductModelV1):
    model_config = ConfigDict(extra="forbid", frozen=True)
    runbook_id: Literal["ROLLBACK_CONFIGURATION"] = "ROLLBACK_CONFIGURATION"
    target_logical_service: Literal["payment"] = "payment"
    maximum_forward_steps: Literal[1] = 1
    parameters_sha256: Sha256 = semantic_sha256_v22([])

    @model_validator(mode="after")
    def require_empty_parameters(self) -> Self:
        if self.parameters_sha256 != semantic_sha256_v22([]):
            raise ValueError("approval parameters must be empty")
        return self


class ApprovalRequestV1(ProductModelV1):
    model_config = ConfigDict(extra="forbid", frozen=True)
    approver: Literal["LOCAL_OPERATOR", "Minghong Sun"]
    authorization_source: Literal[
        "USER_EXPLICIT_OPERATOR_AUTHORIZATION",
        "USER_EXPLICIT_PRODUCT_V040_GOAL_AUTHORIZATION",
    ]
    decision: Literal["APPROVE"]
    scope: ApprovalScopeV1
    ttl_seconds: int = Field(strict=True, ge=1, le=600)


class OperatorApprovalV1(SealedRemediationModelV1):
    seal_field = "approval_sha256"
    schema_version: Literal["ecomsre.product.operator-approval.v1"] = (
        "ecomsre.product.operator-approval.v1"
    )
    approval_id: str = Field(pattern=r"^appr-[0-9a-f]{24}$")
    candidate_id: str = Field(pattern=r"^cand-[0-9a-f]{24}$")
    candidate_sha256: Sha256
    approver: Literal["LOCAL_OPERATOR", "Minghong Sun"]
    authorization_source: Literal[
        "USER_EXPLICIT_OPERATOR_AUTHORIZATION",
        "USER_EXPLICIT_PRODUCT_V040_GOAL_AUTHORIZATION",
    ]
    decision: Literal["APPROVE"] = "APPROVE"
    scope: ApprovalScopeV1
    issued_at: datetime
    expires_at: datetime
    revoked_at: None = None
    single_use: Literal[True] = True
    action_authority: Literal["NONE"] = "NONE"
    created_at: datetime
    approval_sha256: Sha256

    @model_validator(mode="after")
    def require_expiry(self) -> Self:
        if self.created_at != self.issued_at:
            raise ValueError("approval creation anchor differs")
        if not timedelta(0) < self.expires_at - self.issued_at <= timedelta(minutes=10):
            raise ValueError("approval validity must be bounded")
        return self


class RevocationRequestV1(ProductModelV1):
    model_config = ConfigDict(extra="forbid", frozen=True)
    approval_id: str = Field(pattern=r"^appr-[0-9a-f]{24}$")
    reason: Literal["OPERATOR_REVOKED"] = "OPERATOR_REVOKED"


class ApprovalRevocationV1(SealedRemediationModelV1):
    seal_field = "revocation_sha256"
    schema_version: Literal["ecomsre.product.approval-revocation.v1"] = (
        "ecomsre.product.approval-revocation.v1"
    )
    revocation_id: str = Field(pattern=r"^revo-[0-9a-f]{24}$")
    candidate_id: str = Field(pattern=r"^cand-[0-9a-f]{24}$")
    approval_id: str = Field(pattern=r"^appr-[0-9a-f]{24}$")
    approval_sha256: Sha256
    reason: Literal["OPERATOR_REVOKED"] = "OPERATOR_REVOKED"
    revoked_at: datetime
    created_at: datetime
    revocation_sha256: Sha256

    @model_validator(mode="after")
    def require_creation_anchor(self) -> Self:
        if self.created_at != self.revoked_at:
            raise ValueError("revocation creation anchor differs")
        return self


class ApprovalStatusV1(ProductModelV1):
    model_config = ConfigDict(extra="forbid", frozen=True)
    approval: OperatorApprovalV1
    status: Literal["ACTIVE", "EXPIRED", "REVOKED", "NOT_YET_VALID"]
    revocation: ApprovalRevocationV1 | None = None
    action_authority: Literal["NONE"] = "NONE"
