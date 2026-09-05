"""Closed, immutable, non-actionable Product candidate contracts."""

from __future__ import annotations

from datetime import datetime, timedelta
from enum import Enum
from typing import Annotated, Any, ClassVar, Literal, Self

from pydantic import ConfigDict, Field, model_validator

from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22
from ecomsre.product.contracts import ProductModelV1


Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
CLAUSES = (
    "configuration:change-and-error-metric",
    "configuration:change-and-log",
)
PRECONDITIONS = (
    "OWNED_LOCAL_ENVIRONMENT",
    "UNIQUE_ROOT",
    "DIAGNOSIS_EVIDENCE_BOUND",
    "CONFIGURATION_DRIFT_VISIBLE",
    "BASELINE_HASH_BOUND",
    "APPROVAL_ACTIVE",
    "NO_ACTIVE_REMEDIATION",
)


class SealedRemediationModelV1(ProductModelV1):
    model_config = ConfigDict(extra="forbid", frozen=True)
    seal_field: ClassVar[str]

    @model_validator(mode="after")
    def require_seal(self) -> Self:
        if getattr(self, self.seal_field) != semantic_sha256_v22(
            self.model_dump(mode="json", exclude={self.seal_field})
        ):
            raise ValueError("remediation semantic digest differs")
        for value in self.__dict__.values():
            if isinstance(value, datetime) and (
                value.tzinfo is None or value.utcoffset() != timedelta(0)
            ):
                raise ValueError("remediation timestamp must be timezone-aware UTC")
        return self

    @classmethod
    def build(cls, **payload: Any) -> Self:
        if set(payload) - set(cls.model_fields):
            raise ValueError("unknown remediation contract field")
        draft = cls.model_construct(**{**payload, cls.seal_field: "0" * 64})
        body = draft.model_dump(mode="json", exclude={cls.seal_field})
        return cls.model_validate({**body, cls.seal_field: semantic_sha256_v22(body)})


class RemediationRunbookV1(SealedRemediationModelV1):
    seal_field = "runbook_sha256"
    schema_version: Literal["ecomsre.product.remediation-runbook.v1"] = (
        "ecomsre.product.remediation-runbook.v1"
    )
    runbook_id: Literal["ROLLBACK_CONFIGURATION"] = "ROLLBACK_CONFIGURATION"
    version: Literal["v1"] = "v1"
    supported_terminal: Literal["CORE_KNOWN"] = "CORE_KNOWN"
    supported_lane: Literal["CORE"] = "CORE"
    supported_domain: Literal["CONFIGURATION"] = "CONFIGURATION"
    supported_mechanism: Literal["CONFIGURATION_ERROR"] = "CONFIGURATION_ERROR"
    target_logical_service: Literal["payment"] = "payment"
    risk_level: Literal["LOW"] = "LOW"
    parameters: tuple[()] = ()
    allowed_diagnosis_clause_ids: tuple[str, ...] = CLAUSES
    preconditions: tuple[str, ...] = PRECONDITIONS
    forward_steps: tuple[Literal["RESTORE_BASELINE_CONFIGURATION"]] = (
        "RESTORE_BASELINE_CONFIGURATION",
    )
    executor_id: Literal["ProductPaymentConfigurationRollbackExecutor"] = (
        "ProductPaymentConfigurationRollbackExecutor"
    )
    verifier_id: Literal["ProductPaymentConfigurationRecoveryVerifier"] = (
        "ProductPaymentConfigurationRecoveryVerifier"
    )
    maximum_forward_steps: Literal[1] = 1
    failure_policy: Literal["ESCALATE_HUMAN"] = "ESCALATE_HUMAN"
    created_at: datetime
    runbook_sha256: Sha256

    @model_validator(mode="after")
    def require_exact_registry_contract(self) -> Self:
        if (
            self.allowed_diagnosis_clause_ids != CLAUSES
            or self.preconditions != PRECONDITIONS
        ):
            raise ValueError("remediation registry allowlist differs")
        return self


class RemediationRegistryV1(SealedRemediationModelV1):
    seal_field = "registry_sha256"
    schema_version: Literal["ecomsre.product.remediation-registry.v1"] = (
        "ecomsre.product.remediation-registry.v1"
    )
    entries: tuple[RemediationRunbookV1]
    created_at: datetime
    registry_sha256: Sha256


class CandidateReasonV1(str, Enum):
    NO_INCIDENT = "NO_INCIDENT"
    OPEN_WORLD = "OPEN_WORLD"
    EXTENSION_KNOWN = "EXTENSION_KNOWN"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    CONFLICTING_EVIDENCE = "CONFLICTING_EVIDENCE"
    MULTIPLE_ROOTS = "MULTIPLE_ROOTS"
    WRONG_ROOT = "WRONG_ROOT"
    WRONG_DOMAIN = "WRONG_DOMAIN"
    WRONG_MECHANISM = "WRONG_MECHANISM"
    DIAGNOSIS_BINDING_MISMATCH = "DIAGNOSIS_BINDING_MISMATCH"
    EVIDENCE_BINDING_MISMATCH = "EVIDENCE_BINDING_MISMATCH"
    BASELINE_MISMATCH = "BASELINE_MISMATCH"
    IDENTITY_MISMATCH = "IDENTITY_MISMATCH"
    CAPABILITY_MISMATCH = "CAPABILITY_MISMATCH"
    MISSING_DECISION_TRACE = "MISSING_DECISION_TRACE"
    MULTIPLE_CORE_ADMISSIONS = "MULTIPLE_CORE_ADMISSIONS"
    SUPPORT_CLAUSE_MISMATCH = "SUPPORT_CLAUSE_MISMATCH"
    REGISTRY_MISMATCH = "REGISTRY_MISMATCH"
    REQUIRED_SOURCE_UNAVAILABLE = "REQUIRED_SOURCE_UNAVAILABLE"


class RemediationCandidateV1(SealedRemediationModelV1):
    seal_field = "candidate_sha256"
    schema_version: Literal["ecomsre.product.remediation-candidate.v1"] = (
        "ecomsre.product.remediation-candidate.v1"
    )
    candidate_id: str = Field(pattern=r"^cand-[0-9a-f]{24}$")
    environment_id: str = Field(pattern=r"^env-[0-9a-f]{24}$")
    incident_id: str = Field(pattern=r"^inc-[0-9a-f]{24}$")
    incident_sha256: Sha256
    diagnosis_id: str = Field(pattern=r"^diag-[0-9a-f]{24}$")
    diagnosis_sha256: Sha256
    diagnosis_decision_trace_sha256: Sha256
    evidence_bundle_sha256: Sha256
    evidence_index_sha256: Sha256
    admission_sha256: Sha256
    memory_sha256: Sha256
    baseline_id: str = Field(pattern=r"^base-[0-9a-f]{24}$")
    baseline_sha256: Sha256
    identity_map_sha256: Sha256
    capability_sha256: Sha256
    registry_sha256: Sha256
    runbook_id: Literal["ROLLBACK_CONFIGURATION"] = "ROLLBACK_CONFIGURATION"
    runbook_sha256: Sha256
    target_logical_service: Literal["payment"] = "payment"
    risk_level: Literal["LOW"] = "LOW"
    maximum_forward_steps: Literal[1] = 1
    parameters_sha256: Sha256
    matched_clause_id: Literal[
        "configuration:change-and-error-metric", "configuration:change-and-log"
    ]
    eligibility: Literal["ELIGIBLE"] = "ELIGIBLE"
    reason_codes: tuple[()] = ()
    action_authority: Literal["NONE"] = "NONE"
    executable: Literal[False] = False
    created_at: datetime
    candidate_sha256: Sha256

    @model_validator(mode="after")
    def require_semantic_identity(self) -> Self:
        body = self.model_dump(
            mode="json", exclude={"candidate_id", "candidate_sha256"}
        )
        if self.candidate_id != "cand-" + semantic_sha256_v22(body)[:24]:
            raise ValueError("candidate identity differs")
        if self.parameters_sha256 != semantic_sha256_v22([]):
            raise ValueError("candidate parameters must be empty")
        return self


class CandidateProjectionV1(SealedRemediationModelV1):
    seal_field = "projection_sha256"
    schema_version: Literal["ecomsre.product.remediation-candidate-projection.v1"] = (
        "ecomsre.product.remediation-candidate-projection.v1"
    )
    incident_id: str = Field(pattern=r"^inc-[0-9a-f]{24}$")
    diagnosis_sha256: Sha256
    candidates: tuple[RemediationCandidateV1, ...] = Field(max_length=1)
    reason_codes: tuple[CandidateReasonV1, ...]
    action_authority: Literal["NONE"] = "NONE"
    environment_writes: Literal[0] = 0
    provider_calls: Literal[0] = 0
    created_at: datetime
    projection_sha256: Sha256

    @model_validator(mode="after")
    def require_projection(self) -> Self:
        if bool(self.candidates) == bool(self.reason_codes):
            raise ValueError("candidate and denial semantics differ")
        if self.reason_codes != tuple(
            sorted(set(self.reason_codes), key=lambda item: item.value)
        ):
            raise ValueError("candidate reasons are not canonical")
        if any(
            candidate.incident_id != self.incident_id
            or candidate.diagnosis_sha256 != self.diagnosis_sha256
            for candidate in self.candidates
        ):
            raise ValueError("candidate projection parent differs")
        return self
