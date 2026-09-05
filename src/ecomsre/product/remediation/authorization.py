"""Short-lived single-use authorization contracts, distinct from approvals."""

from datetime import datetime, timedelta
from typing import Literal, Self

from pydantic import Field, model_validator

from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22
from ecomsre.product.remediation.contracts import SealedRemediationModelV1, Sha256


class AttemptAuthorizationV1(SealedRemediationModelV1):
    seal_field = "authorization_sha256"
    schema_version: Literal["ecomsre.product.remediation-authorization.v1"] = (
        "ecomsre.product.remediation-authorization.v1"
    )
    authorization_id: str = Field(pattern=r"^auth-[0-9a-f]{24}$")
    candidate_id: str = Field(pattern=r"^cand-[0-9a-f]{24}$")
    candidate_sha256: Sha256
    approval_id: str = Field(pattern=r"^appr-[0-9a-f]{24}$")
    approval_sha256: Sha256
    current_state_snapshot_id: str = Field(pattern=r"^snap-[0-9a-f]{24}$")
    current_state_sha256: Sha256
    diagnosis_sha256: Sha256
    evidence_bundle_sha256: Sha256
    baseline_sha256: Sha256
    registry_sha256: Sha256
    runbook_sha256: Sha256
    target_logical_service: Literal["payment"] = "payment"
    parameters_sha256: Sha256
    risk_level: Literal["LOW"] = "LOW"
    maximum_forward_steps: Literal[1] = 1
    issued_at: datetime
    expires_at: datetime
    single_use: Literal[True] = True
    consumed_at: None = None
    created_at: datetime
    authorization_sha256: Sha256

    @model_validator(mode="after")
    def require_short_lived(self) -> Self:
        if self.created_at != self.issued_at or not timedelta(
            0
        ) < self.expires_at - self.issued_at <= timedelta(minutes=10):
            raise ValueError("authorization validity differs")
        if self.parameters_sha256 != semantic_sha256_v22([]):
            raise ValueError("authorization parameters must be empty")
        return self
