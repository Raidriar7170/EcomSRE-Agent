"""Every post-intent projection is gated by its durable execution evidence."""

import sqlite3

from ecomsre.product.remediation.attempt_contracts import (
    AttemptStateV1 as S,
    RemediationAttemptV1,
)
from ecomsre.product.remediation.execution_contracts import (
    ExecutorDispatchV1,
    StepReceiptV1,
    RecoveryEvaluationV1,
)
from ecomsre.product.remediation.repository import fail


def guard_execution_transition(
    connection: sqlite3.Connection,
    before: RemediationAttemptV1,
    after: RemediationAttemptV1,
) -> None:
    if after.state == S.EXECUTING:
        row = connection.execute(
            "SELECT * FROM remediation_executor_dispatches WHERE attempt_id = ?",
            (after.attempt_id,),
        ).fetchone()
        if row is None:
            raise fail("REMEDIATION_DISPATCH_REQUIRED")
        dispatch = ExecutorDispatchV1.model_validate_json(row["payload_json"])
        if (
            dispatch.dispatch_sha256 != row["dispatch_sha256"]
            or dispatch.write_intent_sha256 != after.write_intent_sha256
            or dispatch.authorization_sha256 != after.authorization_sha256
            or dispatch.write_intent_id != after.write_intent_id
            or row["write_intent_id"] != dispatch.write_intent_id
        ):
            raise fail("REMEDIATION_DISPATCH_BINDING_MISMATCH")
        if after.forward_write_count is not None:
            raise fail("REMEDIATION_DISPATCH_OUTCOME_UNKNOWN")
    elif after.state in {
        S.APPLIED,
        S.EXECUTION_FAILED,
        S.VERIFYING,
        S.RECOVERED,
        S.VERIFICATION_FAILED,
    }:
        row = connection.execute(
            "SELECT * FROM remediation_step_receipts WHERE attempt_id = ?",
            (after.attempt_id,),
        ).fetchone()
        if row is None:
            raise fail("REMEDIATION_RECEIPT_REQUIRED")
        receipt = StepReceiptV1.model_validate_json(row["payload_json"])
        if (
            receipt.attempt_id != after.attempt_id
            or receipt.receipt_sha256 != row["receipt_sha256"]
            or receipt.write_intent_sha256 != after.write_intent_sha256
            or receipt.write_intent_id != after.write_intent_id
        ):
            raise fail("REMEDIATION_RECEIPT_BINDING_MISMATCH")
        applied = receipt.outcome == "APPLIED"
        if applied != (
            after.state != S.EXECUTION_FAILED
        ) or after.forward_write_count != int(applied):
            raise fail("REMEDIATION_RECEIPT_OUTCOME_MISMATCH")
        if after.state in {S.RECOVERED, S.VERIFICATION_FAILED}:
            row = connection.execute(
                "SELECT * FROM remediation_recovery_evaluations WHERE attempt_id = ?",
                (after.attempt_id,),
            ).fetchone()
            if row is None:
                raise fail("REMEDIATION_EVALUATION_REQUIRED")
            evaluation = RecoveryEvaluationV1.model_validate_json(row["payload_json"])
            if (
                evaluation.attempt_id != after.attempt_id
                or evaluation.evaluation_sha256 != row["evaluation_sha256"]
                or evaluation.terminal != after.state.value
                or evaluation.receipt_sha256s != (receipt.receipt_sha256,)
                or evaluation.final_disposition != after.final_disposition
            ):
                raise fail("REMEDIATION_EVALUATION_BINDING_MISMATCH")
    elif after.state == S.OUTCOME_UNKNOWN:
        if before.state == S.EXECUTING and after.forward_write_count is not None:
            raise fail("REMEDIATION_UNKNOWN_COUNT_REQUIRED")
    elif after.forward_write_count != before.forward_write_count:
        raise fail("REMEDIATION_TRANSITION_PARENT_MISMATCH")
