"""Sealed dispatch, receipt and independently acquired recovery evidence."""

from datetime import datetime
from typing import Literal, Self

from pydantic import Field, model_validator

from ecomsre.product.remediation.contracts import SealedRemediationModelV1, Sha256


class ExecutorDispatchV1(SealedRemediationModelV1):
    seal_field = "dispatch_sha256"
    schema_version: Literal["ecomsre.product.executor-dispatch.v1"] = (
        "ecomsre.product.executor-dispatch.v1"
    )
    attempt_id: str = Field(pattern=r"^attempt-[0-9a-f]{24}$")
    write_intent_id: str = Field(pattern=r"^intent-[0-9a-f]{24}$")
    write_intent_sha256: Sha256
    authorization_sha256: Sha256
    recovery_policy_sha256: Sha256
    before_state_sha256: Sha256
    created_at: datetime
    dispatch_sha256: Sha256


class StepReceiptV1(SealedRemediationModelV1):
    seal_field = "receipt_sha256"
    schema_version: Literal["ecomsre.product.step-receipt.v1"] = (
        "ecomsre.product.step-receipt.v1"
    )
    receipt_id: str = Field(pattern=r"^receipt-[0-9a-f]{24}$")
    attempt_id: str = Field(pattern=r"^attempt-[0-9a-f]{24}$")
    write_intent_id: str = Field(pattern=r"^intent-[0-9a-f]{24}$")
    write_intent_sha256: Sha256
    dispatch_sha256: Sha256
    step_ordinal: Literal[1] = 1
    step_id: Literal["RESTORE_BASELINE_CONFIGURATION"] = (
        "RESTORE_BASELINE_CONFIGURATION"
    )
    target_logical_service: Literal["payment"] = "payment"
    before_state_digest: Sha256
    after_state_digest: Sha256
    started_at: datetime
    ended_at: datetime
    elapsed_ms: float = Field(ge=0, allow_inf_nan=False)
    outcome: Literal["APPLIED", "FAILED"]
    safe_error_code: Literal["RESTORE_NOT_APPLIED"] | None = None
    supporting_evidence_refs: tuple[Sha256, ...] = Field(min_length=1)
    created_at: datetime
    receipt_sha256: Sha256

    @model_validator(mode="after")
    def coherent(self) -> Self:
        if not self.started_at <= self.ended_at == self.created_at:
            raise ValueError("receipt time anchors differ")
        if self.outcome == "APPLIED" and (
            self.before_state_digest == self.after_state_digest
            or self.safe_error_code is not None
        ):
            raise ValueError("applied receipt lacks a state change")
        return self


class RecoveryPolicyV1(SealedRemediationModelV1):
    """Trusted frozen healthy bound; never derived from post-action observations."""

    seal_field = "policy_sha256"
    schema_version: Literal["ecomsre.product.recovery-policy.v1"] = (
        "ecomsre.product.recovery-policy.v1"
    )
    environment_id: str = Field(pattern=r"^env-[0-9a-f]{24}$")
    baseline_sha256: Sha256
    baseline_configuration_digest: Sha256
    fault_configuration_digest: Sha256
    target_identity_digest: Sha256
    control_identity_sha256: Sha256
    environment_ownership_digest: Sha256
    business_error_ratio_max: float = Field(ge=0, le=1, allow_inf_nan=False)
    minimum_business_requests: int = Field(strict=True, ge=1)
    window_seconds: int = Field(strict=True, ge=1, le=300)
    created_at: datetime
    policy_sha256: Sha256


class RecoveryObservationV1(SealedRemediationModelV1):
    """Read-only provider evidence, including business traffic independent of control."""

    seal_field = "observation_sha256"
    schema_version: Literal["ecomsre.product.recovery-observation.v1"] = (
        "ecomsre.product.recovery-observation.v1"
    )
    environment_id: str = Field(pattern=r"^env-[0-9a-f]{24}$")
    target_logical_service: Literal["payment"] = "payment"
    policy_sha256: Sha256
    started_at: datetime
    ended_at: datetime
    elapsed_ms: float = Field(ge=0, allow_inf_nan=False)
    infrastructure_passed: bool = Field(strict=True)
    endpoint_passed: bool = Field(strict=True)
    business_observation_kind: Literal["CHECKOUT_PAYMENT_TRAFFIC"] = (
        "CHECKOUT_PAYMENT_TRAFFIC"
    )
    business_requests: int = Field(strict=True, ge=0)
    business_errors: int = Field(strict=True, ge=0)
    configuration_digest: Sha256
    flag_evaluation_restored: bool = Field(strict=True)
    non_owned_resources_unchanged: bool = Field(strict=True)
    environment_ownership_digest: Sha256
    created_at: datetime
    observation_sha256: Sha256

    @model_validator(mode="after")
    def coherent(self) -> Self:
        if not self.started_at < self.ended_at == self.created_at:
            raise ValueError("recovery time anchors differ")
        if self.business_errors > self.business_requests:
            raise ValueError("business counts differ")
        return self


class RecoveryWindowV1(SealedRemediationModelV1):
    seal_field = "window_sha256"
    schema_version: Literal["ecomsre.product.recovery-window.v1"] = (
        "ecomsre.product.recovery-window.v1"
    )
    attempt_id: str = Field(pattern=r"^attempt-[0-9a-f]{24}$")
    ordinal: Literal[1, 2]
    receipt_sha256: Sha256
    policy_sha256: Sha256
    started_at: datetime
    ended_at: datetime
    infrastructure_passed: bool = Field(strict=True)
    endpoint_passed: bool = Field(strict=True)
    business_sli_passed: bool = Field(strict=True)
    configuration_restored: bool = Field(strict=True)
    flag_evaluation_restored: bool = Field(strict=True)
    non_owned_resources_unchanged: bool = Field(strict=True)
    supporting_evidence_refs: tuple[Sha256, ...] = Field(min_length=1, max_length=1)
    created_at: datetime
    window_sha256: Sha256


RecoveryReason = Literal[
    "RECEIPT_INVALID",
    "WINDOW_COUNT",
    "WINDOW_BINDING",
    "WINDOW_NOT_FRESH",
    "WINDOW_OVERLAP",
    "EVIDENCE_UNRESOLVED",
    "CONFIGURATION_NOT_RESTORED",
    "FLAG_NOT_RESTORED",
    "INFRASTRUCTURE_FAILED",
    "ENDPOINT_FAILED",
    "BUSINESS_SLI_FAILED",
    "NON_OWNED_DRIFT",
    "POLICY_BINDING",
]


class RecoveryEvaluationV1(SealedRemediationModelV1):
    seal_field = "evaluation_sha256"
    schema_version: Literal["ecomsre.product.recovery-evaluation.v1"] = (
        "ecomsre.product.recovery-evaluation.v1"
    )
    evaluation_id: str = Field(pattern=r"^evaluation-[0-9a-f]{24}$")
    attempt_id: str = Field(pattern=r"^attempt-[0-9a-f]{24}$")
    runbook_id: Literal["ROLLBACK_CONFIGURATION"] = "ROLLBACK_CONFIGURATION"
    verifier_id: Literal["ProductPaymentConfigurationRecoveryVerifier"] = (
        "ProductPaymentConfigurationRecoveryVerifier"
    )
    policy_sha256: Sha256
    receipt_sha256s: tuple[Sha256, ...]
    recovery_window_sha256s: tuple[Sha256, ...]
    outcome: Literal["PASS", "FAIL"]
    reason_codes: tuple[RecoveryReason, ...]
    terminal: Literal["RECOVERED", "VERIFICATION_FAILED"]
    final_disposition: Literal["RECOVERED", "ESCALATE_HUMAN"]
    created_at: datetime
    evaluation_sha256: Sha256

    @model_validator(mode="after")
    def coherent(self) -> Self:
        passed = self.outcome == "PASS"
        if (
            passed != (not self.reason_codes)
            or passed != (self.terminal == "RECOVERED")
            or passed != (self.final_disposition == "RECOVERED")
        ):
            raise ValueError("evaluation terminal differs")
        if passed and (
            len(self.receipt_sha256s) != 1 or len(self.recovery_window_sha256s) != 2
        ):
            raise ValueError("evaluation evidence count differs")
        return self
