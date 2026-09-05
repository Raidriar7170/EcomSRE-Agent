"""Fresh typed observations and exact trusted state binding; no mutation adapter."""

from datetime import datetime, timedelta
from enum import Enum
from typing import Literal, Protocol

from pydantic import Field

from ecomsre.product.remediation.approval import OperatorApprovalV1
from ecomsre.product.remediation.contracts import (
    RemediationCandidateV1,
    SealedRemediationModelV1,
    Sha256,
)


class DenialReasonV1(str, Enum):
    ENVIRONMENT_NOT_OWNED = "ENVIRONMENT_NOT_OWNED"
    REMOTE_OR_UNTRUSTED_CONTROL = "REMOTE_OR_UNTRUSTED_CONTROL"
    TARGET_IDENTITY_MISMATCH = "TARGET_IDENTITY_MISMATCH"
    DIAGNOSIS_BINDING_MISMATCH = "DIAGNOSIS_BINDING_MISMATCH"
    EVIDENCE_BINDING_MISMATCH = "EVIDENCE_BINDING_MISMATCH"
    REGISTRY_MISMATCH = "REGISTRY_MISMATCH"
    APPROVAL_MISSING = "APPROVAL_MISSING"
    APPROVAL_EXPIRED = "APPROVAL_EXPIRED"
    APPROVAL_REVOKED = "APPROVAL_REVOKED"
    APPROVAL_ALREADY_CONSUMED = "APPROVAL_ALREADY_CONSUMED"
    APPROVAL_BINDING_MISMATCH = "APPROVAL_BINDING_MISMATCH"
    BASELINE_MISMATCH = "BASELINE_MISMATCH"
    CONFIGURATION_DRIFT_NOT_VISIBLE = "CONFIGURATION_DRIFT_NOT_VISIBLE"
    FAULT_NO_LONGER_PRESENT = "FAULT_NO_LONGER_PRESENT"
    SECOND_ACTIVE_TRANSACTION = "SECOND_ACTIVE_TRANSACTION"
    PRIOR_WRITE_INTENT = "PRIOR_WRITE_INTENT"
    PRIOR_RECEIPT = "PRIOR_RECEIPT"
    AUTHORIZATION_EXPIRED = "AUTHORIZATION_EXPIRED"
    HIGH_RISK_DENIED = "HIGH_RISK_DENIED"
    STATE_STALE = "STATE_STALE"
    STATE_DRIFTED = "STATE_DRIFTED"
    LEASE_LOST = "LEASE_LOST"
    RECONCILIATION_REQUIRED = "RECONCILIATION_REQUIRED"


class StateDeniedV1(Exception):
    def __init__(self, reason: DenialReasonV1) -> None:
        self.reason = reason
        super().__init__(reason.value)


class TrustedStateBindingV1(SealedRemediationModelV1):
    """Trusted process configuration, never accepted in an API request."""

    seal_field = "binding_sha256"
    schema_version: Literal["ecomsre.product.remediation-state-binding.v1"] = (
        "ecomsre.product.remediation-state-binding.v1"
    )
    environment_id: str = Field(pattern=r"^env-[0-9a-f]{24}$")
    target_logical_service: Literal["payment"] = "payment"
    environment_ownership_digest: Sha256
    target_identity_digest: Sha256
    identity_map_sha256: Sha256
    control_identity_sha256: Sha256
    baseline_id: str = Field(pattern=r"^base-[0-9a-f]{24}$")
    baseline_sha256: Sha256
    baseline_configuration_digest: Sha256
    fault_configuration_digest: Sha256
    registry_sha256: Sha256
    created_at: datetime
    binding_sha256: Sha256


class StateObservationV1(SealedRemediationModelV1):
    """Public-safe acquisition evidence from a trusted read-only adapter."""

    seal_field = "observation_sha256"
    schema_version: Literal["ecomsre.product.remediation-state-observation.v1"] = (
        "ecomsre.product.remediation-state-observation.v1"
    )
    environment_id: str = Field(pattern=r"^env-[0-9a-f]{24}$")
    environment_owned: bool
    local_control_trusted: bool
    environment_ownership_digest: Sha256
    target_identity_digest: Sha256
    control_identity_sha256: Sha256
    target_logical_service: str = Field(pattern=r"^[a-z][a-z-]{0,40}$")
    baseline_configuration_digest: Sha256
    current_configuration_digest: Sha256
    fault_still_present: bool
    observed_at: datetime
    created_at: datetime
    observation_sha256: Sha256


class CurrentStateSnapshotV1(SealedRemediationModelV1):
    seal_field = "snapshot_sha256"
    schema_version: Literal["ecomsre.product.remediation-current-state.v1"] = (
        "ecomsre.product.remediation-current-state.v1"
    )
    snapshot_id: str = Field(pattern=r"^snap-[0-9a-f]{24}$")
    environment_id: str = Field(pattern=r"^env-[0-9a-f]{24}$")
    incident_id: str = Field(pattern=r"^inc-[0-9a-f]{24}$")
    incident_sha256: Sha256
    candidate_id: str = Field(pattern=r"^cand-[0-9a-f]{24}$")
    candidate_sha256: Sha256
    approval_id: str = Field(pattern=r"^appr-[0-9a-f]{24}$")
    approval_sha256: Sha256
    target_logical_service: Literal["payment"] = "payment"
    trusted_binding_sha256: Sha256
    environment_ownership_digest: Sha256
    target_identity_digest: Sha256
    control_identity_sha256: Sha256
    baseline_id: str = Field(pattern=r"^base-[0-9a-f]{24}$")
    baseline_sha256: Sha256
    baseline_configuration_digest: Sha256
    current_configuration_digest: Sha256
    configuration_drift_visible: bool
    active_remediation_count: int = Field(strict=True, ge=0)
    fault_still_present: bool
    source_observation_refs: tuple[Sha256]
    observed_at: datetime
    created_at: datetime
    snapshot_sha256: Sha256


class CurrentStateProviderV1(Protocol):
    def read_current(self) -> StateObservationV1:
        """Acquire new evidence. The protocol intentionally has no write method."""
        ...


def validate_observation(
    *,
    binding: TrustedStateBindingV1,
    candidate: RemediationCandidateV1,
    approval: OperatorApprovalV1,
    observation: StateObservationV1,
    now: datetime,
) -> None:
    checks = (
        (
            observation.environment_owned
            and observation.environment_id
            == binding.environment_id
            == candidate.environment_id
            and observation.environment_ownership_digest
            == binding.environment_ownership_digest,
            DenialReasonV1.ENVIRONMENT_NOT_OWNED,
        ),
        (
            observation.local_control_trusted
            and observation.control_identity_sha256 == binding.control_identity_sha256,
            DenialReasonV1.REMOTE_OR_UNTRUSTED_CONTROL,
        ),
        (
            observation.target_logical_service
            == candidate.target_logical_service
            == "payment"
            and observation.target_identity_digest == binding.target_identity_digest
            and candidate.identity_map_sha256 == binding.identity_map_sha256,
            DenialReasonV1.TARGET_IDENTITY_MISMATCH,
        ),
        (
            candidate.registry_sha256 == binding.registry_sha256,
            DenialReasonV1.REGISTRY_MISMATCH,
        ),
        (
            candidate.baseline_id == binding.baseline_id
            and candidate.baseline_sha256 == binding.baseline_sha256
            and observation.baseline_configuration_digest
            == binding.baseline_configuration_digest,
            DenialReasonV1.BASELINE_MISMATCH,
        ),
        (
            approval.issued_at < observation.observed_at <= now
            and timedelta(0) <= now - observation.observed_at <= timedelta(seconds=30)
            and observation.created_at == observation.observed_at,
            DenialReasonV1.STATE_STALE,
        ),
        (
            observation.current_configuration_digest
            != binding.baseline_configuration_digest,
            DenialReasonV1.CONFIGURATION_DRIFT_NOT_VISIBLE,
        ),
        (observation.fault_still_present, DenialReasonV1.FAULT_NO_LONGER_PRESENT),
        (
            observation.current_configuration_digest
            == binding.fault_configuration_digest,
            DenialReasonV1.STATE_DRIFTED,
        ),
    )
    for accepted, reason in checks:
        if not accepted:
            raise StateDeniedV1(reason)
