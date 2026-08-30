"""Bounded live-continuation session contracts for Product v0.2.3.1."""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22
from ecomsre.product.contracts import ProductModelV1
from ecomsre.product.pilot.baseline_restart_v023 import BaselineRestartProofV023


_AUTHORITY_COMPONENTS_V0231 = (
    "config_bundle_sha256",
    "connector_binding_sha256",
    "daemon_identity_sha256",
    "docker_context_sha256",
    "ownership_scope_sha256",
    "pilot_runtime_authority_sha256",
    "read_authority_sha256",
    "resolved_endpoints_sha256",
    "resolved_sandbox_sha256",
)


def _require_private_locator(value: str) -> str:
    candidate = PurePosixPath(value)
    if (
        candidate.is_absolute()
        or ".." in candidate.parts
        or str(candidate) != value
        or candidate.parts[:2] != (".local", "product-v0231")
        or len(candidate.parts) < 3
    ):
        raise ValueError("Runtime continuation private locator differs")
    return value


class RuntimeAuthorityContinuityProofV0231(ProductModelV1):
    schema_version: Literal["ecomsre.product.runtime-authority-proof.v0231"] = (
        "ecomsre.product.runtime-authority-proof.v0231"
    )
    continuity_descriptor_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    components: dict[str, dict[str, str | bool]]
    runtime_snapshot_before_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    runtime_snapshot_after_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    runtime_snapshot_authority_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    terminal: Literal[
        "ECOMSRE_PRODUCT_V0231_RUNTIME_AUTHORITY_CONTINUITY_PASS"
    ]
    proof_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_complete_equalities(self) -> "RuntimeAuthorityContinuityProofV0231":
        if tuple(sorted(self.components)) != _AUTHORITY_COMPONENTS_V0231:
            raise ValueError("Runtime authority proof component set differs")
        for value in self.components.values():
            if (
                set(value) != {"expected", "observed", "equal"}
                or value["equal"] is not True
                or value["expected"] != value["observed"]
            ):
                raise ValueError("Runtime authority proof equality differs")
        if (
            self.runtime_snapshot_authority_sha256
            != self.components["connector_binding_sha256"]["observed"]
            or self.runtime_snapshot_before_sha256
            == self.runtime_snapshot_after_sha256
        ):
            raise ValueError("Runtime snapshot authority binding differs")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"proof_sha256"})
        )
        if self.proof_sha256 != expected:
            raise ValueError("Runtime authority proof digest differs")
        return self

    @classmethod
    def build(cls, **payload: Any) -> "RuntimeAuthorityContinuityProofV0231":
        body = {
            "schema_version": "ecomsre.product.runtime-authority-proof.v0231",
            **payload,
        }
        return cls.model_validate({**body, "proof_sha256": semantic_sha256_v22(body)})


class BaselineRestartProofV0231(ProductModelV1):
    schema_version: Literal["ecomsre.product.baseline-restart-proof.v0231"] = (
        "ecomsre.product.baseline-restart-proof.v0231"
    )
    inner_proof: BaselineRestartProofV023
    active_profile_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    readiness_audit_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    baseline_job_count_before: Literal[1]
    baseline_job_count_after: Literal[1]
    verify_job_count_before: Literal[1]
    verify_job_count_after: Literal[1]
    pending_jobs_after: Literal[0]
    running_jobs_after: Literal[0]
    failed_jobs_after: Literal[0]
    terminal: Literal["ECOMSRE_PRODUCT_V0231_BASELINE_RESTART_PASS"]
    proof_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_self_sealed_restart(self) -> "BaselineRestartProofV0231":
        if self.inner_proof.after.profile_sha256 != self.active_profile_sha256:
            raise ValueError("Baseline restart active profile differs")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"proof_sha256"})
        )
        if self.proof_sha256 != expected:
            raise ValueError("Baseline restart proof digest differs")
        return self

    @classmethod
    def build(cls, **payload: Any) -> "BaselineRestartProofV0231":
        body = {
            "schema_version": "ecomsre.product.baseline-restart-proof.v0231",
            **payload,
        }
        draft = cls.model_construct(**body, proof_sha256="0" * 64)
        normalized = draft.model_dump(mode="json", exclude={"proof_sha256"})
        return cls.model_validate(
            {**normalized, "proof_sha256": semantic_sha256_v22(normalized)}
        )


class RuntimeContinuationSessionStartV0231(ProductModelV1):
    schema_version: Literal["ecomsre.product.runtime-session-start.v0231"] = (
        "ecomsre.product.runtime-session-start.v0231"
    )
    session_ordinal: int = Field(ge=1, le=2)
    continuity_descriptor_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    execution_head: str = Field(pattern=r"^[0-9a-f]{40}$")
    private_root_locator: str
    stage: Literal["COMPOSE_VERIFIED"]
    pre_start_compose_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    incident_count_before: Literal[0]
    diagnosis_count_before: Literal[0]
    start_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    _private_locator = field_validator("private_root_locator")(
        _require_private_locator
    )

    @model_validator(mode="after")
    def require_self_sealed_start(self) -> "RuntimeContinuationSessionStartV0231":
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"start_sha256"})
        )
        if self.start_sha256 != expected:
            raise ValueError("Runtime continuation start digest differs")
        return self

    @classmethod
    def build(cls, **payload: Any) -> "RuntimeContinuationSessionStartV0231":
        body = {
            "schema_version": "ecomsre.product.runtime-session-start.v0231",
            **payload,
        }
        return cls.model_validate(
            {**body, "start_sha256": semantic_sha256_v22(body)}
        )


class RuntimeContinuationSessionCompletionV0231(ProductModelV1):
    schema_version: Literal["ecomsre.product.runtime-session-completion.v0231"] = (
        "ecomsre.product.runtime-session-completion.v0231"
    )
    session_ordinal: int = Field(ge=1, le=2)
    start_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    continuity_descriptor_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    execution_head: str = Field(pattern=r"^[0-9a-f]{40}$")
    private_root_locator: str
    stage: Literal["CLOSED"]
    pre_start_compose_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    post_start_read_authority_sha256: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$"
    )
    post_start_pilot_authority_sha256: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$"
    )
    post_start_connector_binding_sha256: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$"
    )
    product_process_launches: tuple[dict[str, Any], ...] = ()
    incident_count_before: Literal[0]
    incident_count_after: Literal[0, 1]
    diagnosis_count_before: Literal[0]
    diagnosis_count_after: Literal[0, 1]
    cleanup: Literal["CLEAN", "BLOCKED"]
    runtime_terminal: Literal[
        "ECOMSRE_PRODUCT_V0231_RUNTIME_AUTHORITY_CONTINUITY_PASS"
    ] | None = None
    restart_terminal: Literal[
        "ECOMSRE_PRODUCT_V0231_BASELINE_RESTART_PASS"
    ] | None = None
    nofault_terminal: Literal[
        "ECOMSRE_PRODUCT_V0231_NOFAULT_FULLY_SUPPORTED",
        "ECOMSRE_PRODUCT_V0231_NOFAULT_CAPABILITY_LIMITED",
        "ECOMSRE_PRODUCT_V0231_NOFAULT_NOT_SUPPORTED",
    ] | None = None
    terminal: str = Field(pattern=r"^[A-Z][A-Z0-9_]{2,159}$")
    failure_class: Literal[
        "TRANSPORT_TRANSIENT",
        "SANDBOX_START_TRANSIENT",
        "SANDBOX_READINESS_TRANSIENT",
        "PRODUCT_START_TRANSIENT",
        "PRODUCT_RESTART_TRANSIENT",
        "FLAGD_BIND_MISMATCH",
        "RESOLVED_COMPOSE_MISMATCH",
        "RUNTIME_AUTHORITY_MISMATCH",
        "BASELINE_BINDING_MISMATCH",
        "UNCLEAN_CLOSURE",
        "POST_INCIDENT_FAILURE",
    ] | None = None
    completion_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    _private_locator = field_validator("private_root_locator")(
        _require_private_locator
    )

    @model_validator(mode="after")
    def require_self_sealed_completion(
        self,
    ) -> "RuntimeContinuationSessionCompletionV0231":
        success = self.terminal == "ECOMSRE_PRODUCT_V0231_NOFAULT_ACCEPTANCE_COMPLETE"
        if success and (
            self.failure_class is not None
            or self.cleanup != "CLEAN"
            or self.runtime_terminal is None
            or self.restart_terminal is None
            or any(
                value is None
                for value in (
                    self.post_start_read_authority_sha256,
                    self.post_start_pilot_authority_sha256,
                    self.post_start_connector_binding_sha256,
                )
            )
            or len(self.product_process_launches) != 2
            or self.incident_count_after != 1
            or self.diagnosis_count_after != 1
            or self.nofault_terminal is None
        ):
            raise ValueError("successful Runtime continuation is incomplete")
        if not success and self.failure_class is None:
            raise ValueError("failed Runtime continuation lacks a failure class")
        if self.diagnosis_count_after > self.incident_count_after:
            raise ValueError("Runtime continuation Diagnosis count exceeds Incident count")
        if self.incident_count_after and self.failure_class not in {
            None,
            "POST_INCIDENT_FAILURE",
            "UNCLEAN_CLOSURE",
        }:
            raise ValueError("post-Incident failure class differs")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"completion_sha256"})
        )
        if self.completion_sha256 != expected:
            raise ValueError("Runtime continuation completion digest differs")
        return self

    @classmethod
    def build(cls, **payload: Any) -> "RuntimeContinuationSessionCompletionV0231":
        body = {
            "schema_version": "ecomsre.product.runtime-session-completion.v0231",
            **payload,
        }
        draft = cls.model_construct(**body, completion_sha256="0" * 64)
        normalized = draft.model_dump(mode="json", exclude={"completion_sha256"})
        return cls.model_validate(
            {**normalized, "completion_sha256": semantic_sha256_v22(normalized)}
        )


class RuntimeContinuationSessionLedgerV0231(ProductModelV1):
    schema_version: Literal["ecomsre.product.runtime-session-ledger.v0231"] = (
        "ecomsre.product.runtime-session-ledger.v0231"
    )
    starts: tuple[RuntimeContinuationSessionStartV0231, ...] = Field(
        min_length=1, max_length=2
    )
    completions: tuple[RuntimeContinuationSessionCompletionV0231, ...] = Field(
        min_length=1, max_length=2
    )
    live_session_count: int = Field(ge=1, le=2)
    accepted_incident_count: Literal[1]
    diagnosis_count: Literal[1]
    ledger_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_bounded_ledger(self) -> "RuntimeContinuationSessionLedgerV0231":
        ordinals = tuple(item.session_ordinal for item in self.starts)
        if ordinals != tuple(range(1, len(ordinals) + 1)):
            raise ValueError("Runtime continuation session ordinals differ")
        if self.live_session_count != len(self.starts):
            raise ValueError("Runtime continuation session count differs")
        if len(self.completions) != len(self.starts) or any(
            completion.start_sha256 != start.start_sha256
            or completion.session_ordinal != start.session_ordinal
            or completion.continuity_descriptor_sha256
            != start.continuity_descriptor_sha256
            or completion.execution_head != start.execution_head
            or completion.private_root_locator != start.private_root_locator
            for start, completion in zip(self.starts, self.completions)
        ):
            raise ValueError("Runtime continuation completion sequence differs")
        if any(
            start.continuity_descriptor_sha256
            != self.starts[0].continuity_descriptor_sha256
            for start in self.starts
        ):
            raise ValueError("Runtime continuation descriptor changed between sessions")
        if len(self.starts) == 2:
            first = self.completions[0]
            if (
                first.failure_class
                not in {
                    "TRANSPORT_TRANSIENT",
                    "SANDBOX_START_TRANSIENT",
                    "SANDBOX_READINESS_TRANSIENT",
                    "PRODUCT_START_TRANSIENT",
                    "PRODUCT_RESTART_TRANSIENT",
                }
                or first.cleanup != "CLEAN"
                or first.incident_count_after != 0
                or first.diagnosis_count_after != 0
            ):
                raise ValueError("second Runtime continuation session is not retryable")
        if self.completions[-1].terminal != (
            "ECOMSRE_PRODUCT_V0231_NOFAULT_ACCEPTANCE_COMPLETE"
        ):
            raise ValueError("Runtime continuation ledger lacks a successful closure")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"ledger_sha256"})
        )
        if self.ledger_sha256 != expected:
            raise ValueError("Runtime continuation ledger digest differs")
        return self

    @classmethod
    def build(
        cls,
        *,
        starts: tuple[RuntimeContinuationSessionStartV0231, ...],
        completions: tuple[RuntimeContinuationSessionCompletionV0231, ...],
    ) -> "RuntimeContinuationSessionLedgerV0231":
        body = {
            "schema_version": "ecomsre.product.runtime-session-ledger.v0231",
            "starts": [item.model_dump(mode="json") for item in starts],
            "completions": [item.model_dump(mode="json") for item in completions],
            "live_session_count": len(starts),
            "accepted_incident_count": completions[-1].incident_count_after,
            "diagnosis_count": completions[-1].diagnosis_count_after,
        }
        return cls.model_validate({**body, "ledger_sha256": semantic_sha256_v22(body)})


__all__ = (
    "BaselineRestartProofV0231",
    "RuntimeAuthorityContinuityProofV0231",
    "RuntimeContinuationSessionCompletionV0231",
    "RuntimeContinuationSessionLedgerV0231",
    "RuntimeContinuationSessionStartV0231",
)
