"""Pure Payment verification over one receipt and two independent observations."""

from collections.abc import Callable, Sequence
from datetime import datetime

from ecomsre.product.remediation.attempts import new_id
from ecomsre.product.remediation.execution_contracts import (
    RecoveryEvaluationV1,
    RecoveryObservationV1,
    RecoveryPolicyV1,
    RecoveryReason,
    RecoveryWindowV1,
    StepReceiptV1,
)
from ecomsre.product.remediation.state import StateObservationV1


def window_from_observation(
    *,
    attempt_id: str,
    ordinal: int,
    receipt: StepReceiptV1,
    policy: RecoveryPolicyV1,
    observation: RecoveryObservationV1,
    reference: str,
) -> RecoveryWindowV1:
    return RecoveryWindowV1.build(
        attempt_id=attempt_id,
        ordinal=ordinal,
        receipt_sha256=receipt.receipt_sha256,
        policy_sha256=policy.policy_sha256,
        started_at=observation.started_at,
        ended_at=observation.ended_at,
        infrastructure_passed=observation.infrastructure_passed,
        endpoint_passed=observation.endpoint_passed,
        business_sli_passed=(
            observation.business_requests >= policy.minimum_business_requests
            and observation.business_errors / max(observation.business_requests, 1)
            <= policy.business_error_ratio_max
        ),
        configuration_restored=observation.configuration_digest
        == policy.baseline_configuration_digest,
        flag_evaluation_restored=observation.flag_evaluation_restored,
        non_owned_resources_unchanged=observation.non_owned_resources_unchanged,
        supporting_evidence_refs=(reference,),
        created_at=observation.created_at,
    )


def evaluate(
    *,
    attempt_id: str,
    receipt: StepReceiptV1 | None,
    windows: Sequence[RecoveryWindowV1],
    policy: RecoveryPolicyV1,
    resolve: Callable[[str], bytes],
    now: datetime,
) -> RecoveryEvaluationV1:
    reasons: list[RecoveryReason] = []
    try:
        policy = RecoveryPolicyV1.model_validate_json(policy.model_dump_json())
    except Exception:
        reasons.append("POLICY_BINDING")
    try:
        if receipt is None:
            raise ValueError("missing")
        receipt = StepReceiptV1.model_validate_json(receipt.model_dump_json())
        if (
            receipt.attempt_id != attempt_id
            or receipt.outcome != "APPLIED"
            or receipt.after_state_digest != policy.baseline_configuration_digest
            or receipt.ended_at >= now
        ):
            raise ValueError("receipt differs")
        if len(receipt.supporting_evidence_refs) != 1:
            raise ValueError("receipt evidence count differs")
        after = StateObservationV1.model_validate_json(
            resolve(receipt.supporting_evidence_refs[0])
        )
        if (
            after.current_configuration_digest != receipt.after_state_digest
            or receipt.before_state_digest != policy.fault_configuration_digest
            or after.target_identity_digest != policy.target_identity_digest
            or after.control_identity_sha256 != policy.control_identity_sha256
            or after.baseline_configuration_digest
            != policy.baseline_configuration_digest
            or after.environment_id != policy.environment_id
            or after.environment_ownership_digest != policy.environment_ownership_digest
            or not after.environment_owned
            or not after.local_control_trusted
            or after.fault_still_present
            or after.target_logical_service != "payment"
            or not receipt.started_at
            <= after.observed_at
            == after.created_at
            <= receipt.ended_at
        ):
            raise ValueError("receipt typed after-state evidence differs")
    except Exception:
        reasons.append("RECEIPT_INVALID")
    if len(windows) != 2 or tuple(w.ordinal for w in windows) != (1, 2):
        reasons.append("WINDOW_COUNT")
    previous_end = receipt.ended_at if receipt is not None else now
    for raw in windows:
        try:
            window = RecoveryWindowV1.model_validate_json(raw.model_dump_json())
            if (
                window.attempt_id != attempt_id
                or window.policy_sha256 != policy.policy_sha256
                or receipt is None
                or window.receipt_sha256 != receipt.receipt_sha256
            ):
                raise ValueError("window differs")
        except Exception:
            reasons.append("WINDOW_BINDING")
            continue
        if window.started_at < previous_end:
            reasons.append("WINDOW_OVERLAP")
        if (
            receipt is None
            or window.started_at <= receipt.ended_at
            or window.ended_at > now
            or (window.ended_at - window.started_at).total_seconds()
            != policy.window_seconds
        ):
            reasons.append("WINDOW_NOT_FRESH")
        previous_end = window.ended_at
        try:
            observation = RecoveryObservationV1.model_validate_json(
                resolve(window.supporting_evidence_refs[0])
            )
            if (
                observation.policy_sha256 != policy.policy_sha256
                or observation.environment_id != policy.environment_id
                or observation.environment_ownership_digest
                != policy.environment_ownership_digest
                or observation.elapsed_ms < policy.window_seconds * 1000
            ):
                reasons.append("POLICY_BINDING")
            rebuilt = window_from_observation(
                attempt_id=attempt_id,
                ordinal=window.ordinal,
                receipt=receipt,
                policy=policy,
                observation=observation,
                reference=window.supporting_evidence_refs[0],
            )
            if rebuilt != window:
                reasons.append("WINDOW_BINDING")
        except Exception:
            reasons.append("EVIDENCE_UNRESOLVED")
        for field, reason in (
            ("infrastructure_passed", "INFRASTRUCTURE_FAILED"),
            ("endpoint_passed", "ENDPOINT_FAILED"),
            ("business_sli_passed", "BUSINESS_SLI_FAILED"),
            ("configuration_restored", "CONFIGURATION_NOT_RESTORED"),
            ("flag_evaluation_restored", "FLAG_NOT_RESTORED"),
            ("non_owned_resources_unchanged", "NON_OWNED_DRIFT"),
        ):
            if not getattr(window, field):
                reasons.append(reason)  # type: ignore[arg-type]
    unique = tuple(dict.fromkeys(reasons))
    return RecoveryEvaluationV1.build(
        evaluation_id=new_id("evaluation"),
        attempt_id=attempt_id,
        policy_sha256=policy.policy_sha256,
        receipt_sha256s=(receipt.receipt_sha256,) if receipt is not None else (),
        recovery_window_sha256s=tuple(w.window_sha256 for w in windows),
        outcome="FAIL" if unique else "PASS",
        reason_codes=unique,
        terminal="VERIFICATION_FAILED" if unique else "RECOVERED",
        final_disposition="ESCALATE_HUMAN" if unique else "RECOVERED",
        created_at=now,
    )
