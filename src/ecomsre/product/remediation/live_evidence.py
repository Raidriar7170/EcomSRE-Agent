"""Closed public projection for one Goal-bound local Payment measurement."""

from datetime import datetime
from typing import Annotated, Final, Literal, Self

from pydantic import ConfigDict, Field, model_validator

from ecomsre.product.contracts import ProductModelV1
from ecomsre.product.remediation.approval import OperatorApprovalV1
from ecomsre.product.remediation.authorization import AttemptAuthorizationV1
from ecomsre.product.remediation.contracts import (
    RemediationCandidateV1,
    SealedRemediationModelV1,
    Sha256,
)
from ecomsre.product.remediation.execution_contracts import (
    RecoveryEvaluationV1,
    RecoveryPolicyV1,
    RecoveryWindowV1,
    StepReceiptV1,
)

GitSha = Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")]
GOAL_SHA: Final = "d8ec6455a6108f40d67eb8441f18e952670b087255c0fb15fc14ccb87e32695a"
PASS = "ECOMSRE_PRODUCT_V040_PAYMENT_BOUNDED_REMEDIATION_PASS"
NEGATIVE = "ECOMSRE_PRODUCT_V040_PAYMENT_BOUNDED_REMEDIATION_NOT_SUPPORTED"
BLOCKED = "BLOCKED_ECOMSRE_PRODUCT_V040_PAYMENT_BOUNDED_REMEDIATION"


def complete_negative(
    receipt: StepReceiptV1 | None,
    evaluation: RecoveryEvaluationV1 | None,
    windows: tuple[RecoveryWindowV1, ...],
) -> bool:
    """An evidence/protocol failure is blocked, not a measured negative."""
    if receipt is not None and receipt.outcome == "FAILED":
        return True
    return (
        receipt is not None
        and receipt.outcome == "APPLIED"
        and evaluation is not None
        and evaluation.outcome == "FAIL"
        and bool(evaluation.reason_codes)
        and tuple(w.ordinal for w in windows) == (1, 2)
        and evaluation.attempt_id == receipt.attempt_id
        and evaluation.receipt_sha256s == (receipt.receipt_sha256,)
        and evaluation.recovery_window_sha256s == tuple(w.window_sha256 for w in windows)
        and all(w.attempt_id == receipt.attempt_id and w.receipt_sha256 == receipt.receipt_sha256
                and w.policy_sha256 == evaluation.policy_sha256 for w in windows)
        and set(evaluation.reason_codes).issubset({
            "CONFIGURATION_NOT_RESTORED", "FLAG_NOT_RESTORED", "INFRASTRUCTURE_FAILED",
            "ENDPOINT_FAILED", "BUSINESS_SLI_FAILED",
        })
    )


class LiveCountsV040(ProductModelV1):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    fault_campaigns: int = Field(ge=0, le=1)
    fault_confirmed: bool
    accepted_attempts: int = Field(ge=0, le=1)
    write_intents: int = Field(ge=0, le=1)
    dispatches: int = Field(ge=0, le=1)
    forward_mutations: int | None = Field(default=None, ge=0, le=1)
    receipts: int = Field(ge=0, le=1)
    recovery_windows: int = Field(ge=0, le=2)
    provider_calls: Literal[0] = 0
    model_selected_actions: Literal[0] = 0
    arbitrary_shell_attempts: Literal[0] = 0
    non_owned_mutations: Literal[0] = 0


class LiveManifestV040(SealedRemediationModelV1):
    seal_field = "manifest_sha256"
    schema_version: Literal["ecomsre.product.v040.live-campaign-manifest.v1"] = (
        "ecomsre.product.v040.live-campaign-manifest.v1"
    )
    goal_sha256: Literal[
        "d8ec6455a6108f40d67eb8441f18e952670b087255c0fb15fc14ccb87e32695a"
    ] = GOAL_SHA
    code_head: GitSha
    code_tree: GitSha
    source_inputs_sha256: Sha256
    product_image_sha256: Sha256
    upstream_commit: Literal["1755859a9de82c2e5e225be68abc401a5ebf2b4f"] = (
        "1755859a9de82c2e5e225be68abc401a5ebf2b4f"
    )
    historical_bindings_sha256: Sha256
    historical_image_lock_sha256: Sha256
    owned_image_lock_sha256: Sha256
    registry_sha256: Sha256
    runtime_profile_sha256: Sha256
    product_compose_sha256: Sha256
    sandbox_compose_sha256: Sha256
    environment_id: str = Field(pattern=r"^env-[0-9a-f]{24}$")
    baseline_id: str = Field(pattern=r"^base-[0-9a-f]{24}$")
    baseline_sha256: Sha256
    ownership_inventory_sha256: Sha256
    policy: RecoveryPolicyV1
    nofault_control_sha256: Sha256
    network_denial_sha256: Sha256
    pre_execution_review_sha256: Sha256
    local_validation_sha256: Sha256
    ci_run_id: int = Field(strict=True, gt=0)
    ci_conclusion: Literal["SUCCESS"] = "SUCCESS"
    initial_counts: LiveCountsV040
    frozen_at: datetime
    manifest_sha256: Sha256

    @model_validator(mode="after")
    def zero_initial(self) -> Self:
        counts = self.initial_counts
        if (
            any(
                (
                    counts.fault_campaigns,
                    counts.fault_confirmed,
                    counts.accepted_attempts,
                    counts.write_intents,
                    counts.dispatches,
                    counts.receipts,
                    counts.recovery_windows,
                )
            )
            or counts.forward_mutations != 0
        ):
            raise ValueError("campaign allowance already consumed before freeze")
        if (
            self.policy.baseline_sha256 != self.baseline_sha256
            or self.policy.environment_id != self.environment_id
        ):
            raise ValueError("manifest policy binding differs")
        return self


class LiveDiagnosisV040(ProductModelV1):
    model_config = ConfigDict(extra="forbid", frozen=True)
    diagnosis_id: str = Field(pattern=r"^diag-[0-9a-f]{24}$")
    result_sha256: Sha256
    terminal: Literal[
        "CORE_KNOWN",
        "EXTENSION_KNOWN",
        "OPEN_WORLD",
        "NO_INCIDENT",
        "INSUFFICIENT_EVIDENCE",
        "CONFLICTING_EVIDENCE",
    ]
    lane: Literal["CORE", "EXTENSION", "OPEN_WORLD", "NO_INCIDENT", "ABSTAIN"]
    payment_unique_root: bool
    configuration_error: bool
    supporting_refs_resolve: bool
    supporting_source_types: tuple[
        Literal["METRICS", "LOGS", "TRACES", "RUNTIME", "RESOURCES", "CHANGES"], ...
    ]
    evidence_aliases: tuple[
        Annotated[str, Field(pattern=r"^E[1-9][0-9]?$", max_length=3)], ...
    ]
    action_authority: Literal["NONE"] = "NONE"


class LiveCleanupV040(ProductModelV1):
    model_config = ConfigDict(extra="forbid", frozen=True)
    verdict: Literal["CLEAN", "BLOCKED", "NOT_RUN"]
    baseline_restored: bool | None
    owned_containers: int = Field(ge=0)
    owned_networks: int = Field(ge=0)
    owned_volumes: int = Field(ge=0)
    non_owned_resources_changed: bool | None


class LiveResultV040(SealedRemediationModelV1):
    seal_field = "result_sha256"
    schema_version: Literal["ecomsre.product.v040.payment-live-acceptance.v1"] = (
        "ecomsre.product.v040.payment-live-acceptance.v1"
    )
    terminal: Literal[
        "ECOMSRE_PRODUCT_V040_PAYMENT_BOUNDED_REMEDIATION_PASS",
        "ECOMSRE_PRODUCT_V040_PAYMENT_BOUNDED_REMEDIATION_NOT_SUPPORTED",
        "BLOCKED_ECOMSRE_PRODUCT_V040_PAYMENT_BOUNDED_REMEDIATION",
    ]
    manifest_sha256: Sha256
    goal_sha256: Literal[
        "d8ec6455a6108f40d67eb8441f18e952670b087255c0fb15fc14ccb87e32695a"
    ] = GOAL_SHA
    code_head: GitSha
    environment_id: str = Field(pattern=r"^env-[0-9a-f]{24}$")
    baseline_id: str = Field(pattern=r"^base-[0-9a-f]{24}$")
    baseline_sha256: Sha256
    diagnosis: LiveDiagnosisV040 | None
    candidate: RemediationCandidateV1 | None
    approval: OperatorApprovalV1 | None
    authorization: AttemptAuthorizationV1 | None
    current_state_admitted: bool
    receipt: StepReceiptV1 | None
    recovery_windows: tuple[RecoveryWindowV1, ...] = Field(max_length=2)
    evaluation: RecoveryEvaluationV1 | None
    counts: LiveCountsV040
    cleanup: LiveCleanupV040
    blocked_stage: Literal[
        "NONE",
        "FAULT_CONTROL",
        "DIAGNOSIS",
        "CANDIDATE",
        "APPROVAL",
        "AUTHORIZATION",
        "EXECUTION",
        "VERIFICATION",
        "CLEANUP",
        "PERSISTENCE",
    ]
    safe_error_code: Literal[
        "NONE", "BOUNDED_CAMPAIGN_BLOCKED", "BOUNDED_RECOVERY_NOT_SUPPORTED"
    ]
    preserved_evidence_sha256: Sha256
    required_successor_change: Literal["NONE", "NEW_VERSIONED_GOAL_AND_FRESH_AUTHORITY"]
    limitations: tuple[
        Literal[
            "OWNED_LOCAL_PAYMENT_ONLY",
            "HUMAN_AUTHORIZED_ONLY",
            "NO_PROVIDER_OR_MODEL_ACTION_SELECTION",
            "NO_KAFKA_AUTOMATIC_REMEDIATION",
            "NO_PRODUCTION_OR_GENERAL_AUTONOMY_CLAIM",
            "NO_EXACTLY_ONCE_DISTRIBUTED_CLAIM",
        ],
        ...,
    ]
    created_at: datetime
    result_sha256: Sha256

    @model_validator(mode="after")
    def positive_requires_chain(self) -> Self:
        if self.terminal == NEGATIVE and not complete_negative(
            self.receipt, self.evaluation, self.recovery_windows
        ):
            raise ValueError("protocol/evidence blockers cannot be a complete negative")
        if self.terminal != PASS:
            return self
        if (
            self.diagnosis is None
            or self.diagnosis.terminal != "CORE_KNOWN"
            or self.diagnosis.lane != "CORE"
            or not all(
                (
                    self.diagnosis.payment_unique_root,
                    self.diagnosis.configuration_error,
                    self.diagnosis.supporting_refs_resolve,
                )
            )
        ):
            raise ValueError("positive diagnosis differs")
        if (
            self.candidate is None
            or self.approval is None
            or self.authorization is None
            or self.receipt is None
            or self.evaluation is None
            or not self.current_state_admitted
        ):
            raise ValueError("positive authority chain incomplete")
        if (
            self.candidate.candidate_sha256 != self.approval.candidate_sha256
            or self.approval.approval_sha256 != self.authorization.approval_sha256
            or self.authorization.candidate_sha256 != self.candidate.candidate_sha256
            or self.receipt.outcome != "APPLIED"
            or self.evaluation.outcome != "PASS"
            or self.evaluation.receipt_sha256s != (self.receipt.receipt_sha256,)
            or self.evaluation.recovery_window_sha256s
            != tuple(w.window_sha256 for w in self.recovery_windows)
            or tuple(w.ordinal for w in self.recovery_windows) != (1, 2)
        ):
            raise ValueError("positive parent chain differs")
        if (
            self.environment_id != self.candidate.environment_id
            or self.baseline_id != self.candidate.baseline_id
            or self.baseline_sha256 != self.candidate.baseline_sha256
            or self.baseline_sha256 != self.authorization.baseline_sha256
            or self.diagnosis.diagnosis_id != self.candidate.diagnosis_id
            or self.diagnosis.result_sha256 != self.candidate.diagnosis_sha256
            or self.authorization.diagnosis_sha256 != self.candidate.diagnosis_sha256
            or self.authorization.evidence_bundle_sha256
            != self.candidate.evidence_bundle_sha256
            or self.authorization.registry_sha256 != self.candidate.registry_sha256
            or self.authorization.runbook_sha256 != self.candidate.runbook_sha256
            or self.authorization.approval_id != self.approval.approval_id
            or self.authorization.candidate_id != self.candidate.candidate_id
            or self.approval.candidate_id != self.candidate.candidate_id
            or self.approval.authorization_source
            != "USER_EXPLICIT_PRODUCT_V040_GOAL_AUTHORIZATION"
            or self.receipt.attempt_id != self.evaluation.attempt_id
            or any(
                w.attempt_id != self.receipt.attempt_id
                or w.receipt_sha256 != self.receipt.receipt_sha256
                or w.policy_sha256 != self.evaluation.policy_sha256
                for w in self.recovery_windows
            )
            or not self.approval.issued_at
            <= self.authorization.issued_at
            <= self.receipt.started_at
            <= self.receipt.ended_at
            < min(self.authorization.expires_at, self.approval.expires_at)
        ):
            raise ValueError("positive cross-object binding differs")
        previous_end = self.receipt.ended_at
        for window in self.recovery_windows:
            if (
                not previous_end
                < window.started_at
                < window.ended_at
                <= window.created_at
                <= self.created_at
            ):
                raise ValueError("positive recovery time binding differs")
            if not all(
                (
                    window.infrastructure_passed,
                    window.endpoint_passed,
                    window.business_sli_passed,
                    window.configuration_restored,
                    window.flag_evaluation_restored,
                    window.non_owned_resources_unchanged,
                )
            ):
                raise ValueError("positive recovery check failed")
            previous_end = window.ended_at
        c = self.counts
        if (
            c.fault_campaigns,
            c.fault_confirmed,
            c.accepted_attempts,
            c.write_intents,
            c.dispatches,
            c.forward_mutations,
            c.receipts,
            c.recovery_windows,
        ) != (1, True, 1, 1, 1, 1, 1, 2):
            raise ValueError("positive campaign cardinality differs")
        if (
            self.cleanup.verdict != "CLEAN"
            or self.cleanup.baseline_restored is not True
            or self.cleanup.non_owned_resources_changed is not False
            or any(
                (
                    self.cleanup.owned_containers,
                    self.cleanup.owned_networks,
                    self.cleanup.owned_volumes,
                )
            )
        ):
            raise ValueError("positive cleanup is incomplete")
        if (
            self.blocked_stage != "NONE"
            or self.safe_error_code != "NONE"
            or self.required_successor_change != "NONE"
        ):
            raise ValueError("positive result retains a blocker")
        return self
