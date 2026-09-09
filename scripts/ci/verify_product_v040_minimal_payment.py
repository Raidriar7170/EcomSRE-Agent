"""Verify the public minimal Payment acceptance chain; never execute live effects."""

from __future__ import annotations
import hashlib
import json
from pathlib import Path
from typing import Any
from ecomsre.product.remediation.approval import OperatorApprovalV1
from ecomsre.product.remediation.authorization import AttemptAuthorizationV1
from ecomsre.product.remediation.attempt_contracts import (
    RemediationAttemptV1,
    WriteIntentV1,
)
from ecomsre.product.remediation.contracts import RemediationCandidateV1
from ecomsre.product.remediation.execution_contracts import (
    ExecutorDispatchV1,
    RecoveryPolicyV1,
    RecoveryWindowV1,
    RecoveryEvaluationV1,
    StepReceiptV1,
)
from ecomsre.product.remediation.state import CurrentStateSnapshotV1
from ecomsre.product.remediation.verifier import evaluate

ROOT = Path(__file__).resolve().parents[2]
RESULT = ROOT / "docs/results/product-v040-minimal-payment"


def require(value: bool, reason: str) -> None:
    if not value:
        raise ValueError(reason)


def verify(value: dict[str, Any]) -> dict[str, Any]:
    require(
        value["terminal"] == "RECOVERED" and value["provider_calls"] == 0,
        "LIVE_TERMINAL_NOT_PASS",
    )
    require(
        value["cleanup"]["clean"]
        and value["cleanup"]["non_owned_unchanged"]
        and value["cleanup"]["remaining"]
        == {"container": 0, "network": 0, "volume": 0},
        "CLEANUP_NOT_PASS",
    )
    require(value["baseline_file_restored"], "BASELINE_NOT_RESTORED")
    require(
        value["healthy"]["business_requests"] > 0
        and value["fault"]["confirmed_requests"] > 0
        and value["fault"]["expected_payment_failures"]
        == value["fault"]["confirmed_requests"],
        "BUSINESS_FAULT_NOT_PROVEN",
    )
    require(
        value["fault_injections"] == value["executor_attempts"] == 1, "ATTEMPT_COUNTS"
    )
    require(
        value["goal_sha256"]
        == "290e2f2c7948522f3f642f16696e73fc77c5535374be992e5f2ec2ff5db5337b",
        "ACTIVE_GOAL_MISMATCH",
    )
    candidate = RemediationCandidateV1.model_validate(value["objects"]["candidate"])
    approval = OperatorApprovalV1.model_validate(value["objects"]["approval"])
    state = CurrentStateSnapshotV1.model_validate(value["objects"]["current_state"])
    write_state = CurrentStateSnapshotV1.model_validate(value["objects"]["write_state"])
    dispatch_state = CurrentStateSnapshotV1.model_validate(
        value["objects"]["dispatch_state"]
    )
    authorization = AttemptAuthorizationV1.model_validate(
        value["objects"]["authorization"]
    )
    intent = WriteIntentV1.model_validate(value["objects"]["write_intent"])
    dispatch = ExecutorDispatchV1.model_validate(value["objects"]["dispatch"])
    receipt = StepReceiptV1.model_validate(value["objects"]["receipt"])
    policy = RecoveryPolicyV1.model_validate(value["objects"]["recovery_policy"])
    windows = tuple(
        RecoveryWindowV1.model_validate(v) for v in value["objects"]["recovery_windows"]
    )
    evaluation = RecoveryEvaluationV1.model_validate(
        value["objects"]["recovery_evaluation"]
    )
    attempt = RemediationAttemptV1.model_validate(value["objects"]["attempt"])
    require(
        approval.candidate_sha256
        == candidate.candidate_sha256
        == authorization.candidate_sha256,
        "CANDIDATE_APPROVAL_BINDING",
    )
    require(
        authorization.approval_sha256
        == approval.approval_sha256
        == state.approval_sha256,
        "APPROVAL_STATE_BINDING",
    )
    require(
        approval.issued_at
        <= state.observed_at
        <= authorization.issued_at
        < authorization.expires_at,
        "FRESH_STATE_ORDER",
    )
    require(
        state.fault_still_present
        and state.configuration_drift_visible
        and state.active_remediation_count == 0,
        "STATE_NOT_ADMISSIBLE",
    )
    require(
        authorization.current_state_sha256 == state.snapshot_sha256
        and intent.before_state_sha256 == write_state.snapshot_sha256
        and dispatch.before_state_sha256 == dispatch_state.snapshot_sha256,
        "STATE_INTENT_BINDING",
    )
    require(
        intent.authorization_sha256
        == authorization.authorization_sha256
        == dispatch.authorization_sha256,
        "AUTHORIZATION_DISPATCH_BINDING",
    )
    require(
        dispatch.write_intent_sha256
        == intent.write_intent_sha256
        == receipt.write_intent_sha256
        and dispatch.dispatch_sha256 == receipt.dispatch_sha256,
        "RECEIPT_DISPATCH_BINDING",
    )
    require(
        receipt.before_state_digest
        == state.current_configuration_digest
        == policy.fault_configuration_digest
        == value["fault"]["configuration_digest"],
        "FAULT_STATE_BINDING",
    )
    require(
        receipt.after_state_digest == policy.baseline_configuration_digest
        and receipt.outcome == "APPLIED",
        "RESTORE_NOT_APPLIED",
    )
    require(
        attempt.forward_write_count == 1 and attempt.state.value == "RECOVERED",
        "SINGLE_WRITE_NOT_PROVEN",
    )
    require(
        value["gateway_consumptions"] == 1
        and value.get("safety_baseline_restores", 0) == 0,
        "CONTROL_WRITE_COUNT",
    )
    require(
        value["diagnosis"]["terminal"] == "CORE_KNOWN"
        and value["diagnosis"]["root"] == "payment"
        and value["diagnosis"]["mechanism"] == "CONFIGURATION_ERROR"
        and value["diagnosis"]["all_supporting_refs_resolved"],
        "DIAGNOSIS_NOT_BOUND",
    )
    require(
        value["matched_clause"]
        in ("configuration:change-and-error-metric", "configuration:change-and-log"),
        "CONFIGURATION_CLAUSE_MISSING",
    )
    require(
        value["goal_authorization"]["codex_autonomous_self_approval"] is False
        and value["goal_authorization"]["approval_sha256"] == approval.approval_sha256,
        "GOAL_APPROVAL_BINDING",
    )
    from ecomsre.product.incidents.contracts import DiagnosisResultV1

    require(
        candidate.diagnosis_sha256
        == DiagnosisResultV1.model_validate(value["product_diagnosis"]).result_sha256,
        "DIAGNOSIS_OBJECT_BINDING",
    )
    require(candidate.matched_clause_id == value["matched_clause"], "CLAUSE_BINDING")
    require(
        value["goal_authorization"]["goal_sha256"] == value["goal_sha256"],
        "GOAL_DIGEST_BINDING",
    )
    require(
        approval.candidate_id
        == candidate.candidate_id
        == attempt.candidate_id
        == authorization.candidate_id,
        "CANDIDATE_ID_BINDING",
    )
    require(
        approval.approval_id
        == state.approval_id
        == authorization.approval_id
        == attempt.approval_id,
        "APPROVAL_ID_BINDING",
    )
    require(
        authorization.authorization_id
        == intent.authorization_id
        == attempt.authorization_id,
        "AUTHORIZATION_ID_BINDING",
    )
    require(
        intent.write_intent_id
        == dispatch.write_intent_id
        == receipt.write_intent_id
        == attempt.write_intent_id,
        "WRITE_INTENT_ID_BINDING",
    )
    require(
        attempt.attempt_id
        == intent.attempt_id
        == dispatch.attempt_id
        == receipt.attempt_id
        == evaluation.attempt_id,
        "ATTEMPT_ID_BINDING",
    )
    require(
        candidate.baseline_sha256
        == policy.baseline_sha256
        == authorization.baseline_sha256
        == state.baseline_sha256
        == value["healthy"]["baseline_sha256"],
        "BASELINE_BINDING",
    )
    require(
        candidate.environment_id
        == state.environment_id
        == policy.environment_id
        == attempt.environment_id,
        "ENVIRONMENT_BINDING",
    )

    require(
        authorization.issued_at
        < write_state.observed_at
        <= intent.committed_at
        <= dispatch_state.observed_at
        <= dispatch.created_at
        <= receipt.started_at,
        "PREWRITE_RECAPTURE_ORDER",
    )
    require(
        all(
            s.fault_still_present
            and s.configuration_drift_visible
            and s.active_remediation_count == 1
            and s.current_configuration_digest == state.current_configuration_digest
            and s.target_identity_digest == state.target_identity_digest
            and s.control_identity_sha256 == state.control_identity_sha256
            and s.baseline_sha256 == state.baseline_sha256
            and s.approval_sha256 == approval.approval_sha256
            for s in (write_state, dispatch_state)
        ),
        "PREWRITE_STATE_DRIFT",
    )
    from ecomsre.product.baselines import EnvironmentBaselineV1

    baseline = EnvironmentBaselineV1.model_validate_json(
        json.dumps(value["active_baseline"])
    )
    require(
        baseline.active
        and baseline.baseline_sha256 == candidate.baseline_sha256
        and baseline.successful_windows == 5,
        "ACTIVE_BASELINE_EVIDENCE",
    )
    evidence = value["recovery_evidence"]

    def resolve(ref: str) -> bytes:
        payload = json.dumps(
            evidence[ref], sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode()
        require(hashlib.sha256(payload).hexdigest() == ref, "RECOVERY_CAS_MISMATCH")
        return payload

    rebuilt = evaluate(
        attempt_id=attempt.attempt_id,
        receipt=receipt,
        windows=windows,
        policy=policy,
        resolve=resolve,
        now=evaluation.created_at,
    )
    require(
        rebuilt.outcome == evaluation.outcome == "PASS" and not rebuilt.reason_codes,
        "RECOVERY_REPLAY_NOT_PASS",
    )
    require(
        all(
            evidence[w.supporting_evidence_refs[0]]["business_observation_kind"]
            == "DIRECT_PAYMENT_TRAFFIC"
            for w in windows
        ),
        "BUSINESS_PROVENANCE_MISMATCH",
    )
    require(
        value["healthy"]["business_errors_after_startup"] == 0,
        "HEALTHY_BUSINESS_FAILED",
    )
    return {
        "terminal": "ECOMSRE_PRODUCT_V040_MINIMAL_PAYMENT_EVIDENCE_PASS",
        "forward_writes": 1,
        "recovery_windows": 2,
        "provider_calls": 0,
    }


def verify_manifest(root: Path) -> None:
    manifest = json.loads((root / "evidence-manifest.json").read_bytes())
    require(
        set(manifest["files"])
        == {"README.md", "HUMAN_BRIEF.md", "live-result.json", "engineering-attempts.json"},
        "PUBLIC_MANIFEST_INCOMPLETE",
    )
    for name, checksum in manifest["files"].items():
        require("/" not in name and ".." not in name, "MANIFEST_PATH_INVALID")
        require(
            hashlib.sha256((root / name).read_bytes()).hexdigest() == checksum,
            "PUBLIC_MANIFEST_MISMATCH",
        )


def main() -> None:
    verify_manifest(RESULT)
    value = json.loads((RESULT / "live-result.json").read_bytes())
    print(json.dumps(verify(value), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
