"""Synthetic chain validation only; these tests never claim a live measurement."""

import pytest

from ecomsre.product.remediation.approval import OperatorApprovalV1
from ecomsre.product.remediation.contracts import RemediationCandidateV1
from ecomsre.product.remediation.authorization import AttemptAuthorizationV1
from ecomsre.product.remediation.live_evidence import (
    LiveResultV040,
    LiveDiagnosisV040,
    LiveCountsV040,
    LiveCleanupV040,
)
from tests.product_v040.test_executor import (
    executable as executable,
    authorized_material as authorized_material,
    api as api,
    material as material,
    FakeRecovery,
)
from tests.product_v040.test_approval_api import APPROVAL


@pytest.fixture(autouse=True)
def goal_source(monkeypatch):
    monkeypatch.setitem(
        APPROVAL,
        "authorization_source",
        "USER_EXPLICIT_PRODUCT_V040_GOAL_AUTHORIZATION",
    )


@pytest.fixture
def synthetic_public_payload(executable):
    values, attempt, _, executor, recovery, _ = executable
    executor.run_one(attempt["attempt_id"])
    recovery.verify(attempt["attempt_id"], FakeRecovery(values))
    candidate, approval = values[1:3]
    with values[4].store.connect() as connection:
        row = connection.execute(
            "SELECT payload_json FROM remediation_authorizations"
        ).fetchone()
    authorization = AttemptAuthorizationV1.model_validate_json(row[0])
    diagnosis = values[0][2]["diagnosis"]
    return dict(
        terminal="ECOMSRE_PRODUCT_V040_PAYMENT_BOUNDED_REMEDIATION_PASS",
        manifest_sha256="f" * 64,
        code_head="a" * 40,
        environment_id=candidate["environment_id"],
        baseline_id=candidate["baseline_id"],
        baseline_sha256=candidate["baseline_sha256"],
        diagnosis=LiveDiagnosisV040(
            diagnosis_id=diagnosis.diagnosis_id,
            result_sha256=diagnosis.result_sha256,
            terminal="CORE_KNOWN",
            lane="CORE",
            payment_unique_root=True,
            configuration_error=True,
            supporting_refs_resolve=True,
            supporting_source_types=("CHANGES", "LOGS"),
            evidence_aliases=("E1", "E2"),
        ),
        candidate=RemediationCandidateV1.model_validate(candidate),
        approval=OperatorApprovalV1.model_validate(approval),
        authorization=authorization,
        current_state_admitted=True,
        receipt=recovery.receipt(attempt["attempt_id"]),
        recovery_windows=recovery.windows(attempt["attempt_id"]),
        evaluation=recovery.evaluation(attempt["attempt_id"]),
        counts=LiveCountsV040.model_validate(
            {
                "fault_campaigns": 1,
                "fault_confirmed": True,
                "accepted_attempts": 1,
                "write_intents": 1,
                "dispatches": 1,
                "forward_mutations": 1,
                "receipts": 1,
                "recovery_windows": 2,
            }
        ),
        cleanup=LiveCleanupV040.model_validate(
            {
                "verdict": "CLEAN",
                "baseline_restored": True,
                "owned_containers": 0,
                "owned_networks": 0,
                "owned_volumes": 0,
                "non_owned_resources_changed": False,
            }
        ),
        blocked_stage="NONE",
        safe_error_code="NONE",
        preserved_evidence_sha256="e" * 64,
        required_successor_change="NONE",
        limitations=("OWNED_LOCAL_PAYMENT_ONLY",),
        created_at=values[4].clock(),
    )


def test_synthetic_closed_result_chain(synthetic_public_payload):
    result = LiveResultV040.build(**synthetic_public_payload)
    assert result.counts.forward_mutations == 1
    assert result.receipt.outcome == "APPLIED"


@pytest.mark.parametrize(
    "field,value",
    [
        ("environment_id", "env-" + "f" * 24),
        ("baseline_id", "base-" + "f" * 24),
        ("baseline_sha256", "f" * 64),
    ],
)
def test_positive_cannot_swap_top_level_parent(synthetic_public_payload, field, value):
    synthetic_public_payload[field] = value
    with pytest.raises(ValueError, match="cross-object binding"):
        LiveResultV040.build(**synthetic_public_payload)


def test_positive_cannot_swap_diagnosis(synthetic_public_payload):
    original = synthetic_public_payload["diagnosis"]
    synthetic_public_payload["diagnosis"] = original.model_copy(
        update={"result_sha256": "0" * 64}
    )
    with pytest.raises(ValueError, match="cross-object binding"):
        LiveResultV040.build(**synthetic_public_payload)


def test_positive_cannot_swap_receipt_attempt(synthetic_public_payload):
    from ecomsre.product.remediation.execution_contracts import (
        StepReceiptV1,
        RecoveryEvaluationV1,
    )

    receipt = synthetic_public_payload["receipt"]
    replacement = StepReceiptV1.build(
        **{
            **receipt.model_dump(exclude={"receipt_sha256"}),
            "attempt_id": "attempt-" + "f" * 24,
        }
    )
    synthetic_public_payload["receipt"] = replacement
    evaluation = synthetic_public_payload["evaluation"]
    synthetic_public_payload["evaluation"] = RecoveryEvaluationV1.build(
        **{
            **evaluation.model_dump(exclude={"evaluation_sha256"}),
            "receipt_sha256s": (replacement.receipt_sha256,),
        }
    )
    with pytest.raises(ValueError, match="cross-object binding"):
        LiveResultV040.build(**synthetic_public_payload)


def test_unknown_forward_count_cannot_be_positive(synthetic_public_payload):
    synthetic_public_payload["counts"] = synthetic_public_payload["counts"].model_copy(
        update={"forward_mutations": None}
    )
    with pytest.raises(ValueError, match="cardinality"):
        LiveResultV040.build(**synthetic_public_payload)


@pytest.mark.parametrize("reason", ["WINDOW_COUNT", "EVIDENCE_UNRESOLVED", "WINDOW_BINDING"])
def test_incomplete_protocol_result_cannot_be_published_as_negative(synthetic_public_payload, reason):
    from ecomsre.product.remediation.execution_contracts import RecoveryEvaluationV1
    payload = synthetic_public_payload
    payload["terminal"] = "ECOMSRE_PRODUCT_V040_PAYMENT_BOUNDED_REMEDIATION_NOT_SUPPORTED"
    old = payload["evaluation"]
    payload["evaluation"] = RecoveryEvaluationV1.build(**{
        **old.model_dump(exclude={"evaluation_sha256"}), "outcome": "FAIL", "reason_codes": (reason,),
        "terminal": "VERIFICATION_FAILED", "final_disposition": "ESCALATE_HUMAN",
    })
    with pytest.raises(ValueError, match="protocol/evidence"):
        LiveResultV040.build(**payload)
