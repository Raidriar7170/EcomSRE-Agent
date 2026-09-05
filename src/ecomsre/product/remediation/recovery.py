"""Create-once two-window acquisition and immutable recovery evaluation."""

from datetime import datetime, timedelta
from copy import copy
from typing import Protocol

from ecomsre.product.remediation.attempt_contracts import (
    AttemptStateV1 as S,
    RemediationAttemptV1,
)
from ecomsre.product.remediation.attempts import RemediationAttemptRepositoryV1, new_id
from ecomsre.product.remediation.execution_contracts import (
    RecoveryEvaluationV1,
    RecoveryObservationV1,
    RecoveryPolicyV1,
    RecoveryWindowV1,
    StepReceiptV1,
)
from ecomsre.product.remediation.repository import canonical, fail
from ecomsre.product.remediation.verifier import evaluate, window_from_observation
from ecomsre.product.remediation.executor import (
    ProductPaymentConfigurationRollbackExecutor,
)
from ecomsre.product.remediation.state import TrustedStateBindingV1, StateObservationV1


class RecoveryWindowProviderV1(Protocol):
    def acquire(
        self, *, started_after: datetime, policy: RecoveryPolicyV1
    ) -> RecoveryObservationV1: ...


class RecoveryRepositoryV1:
    def __init__(self, attempts: RemediationAttemptRepositoryV1) -> None:
        self.attempts = attempts

    def bind_policy(self, policy: RecoveryPolicyV1) -> None:
        policy = RecoveryPolicyV1.model_validate_json(policy.model_dump_json())
        binding = self.attempts.binding
        if (
            binding is None
            or policy.environment_id != binding.environment_id
            or policy.baseline_sha256 != binding.baseline_sha256
            or policy.baseline_configuration_digest
            != binding.baseline_configuration_digest
            or policy.environment_ownership_digest
            != binding.environment_ownership_digest
        ):
            raise fail("REMEDIATION_RECOVERY_POLICY_BINDING_MISMATCH")
        if any(
            getattr(policy, name) != getattr(binding, name)
            for name in (
                "fault_configuration_digest",
                "target_identity_digest",
                "control_identity_sha256",
            )
        ):
            raise fail("REMEDIATION_RECOVERY_POLICY_BINDING_MISMATCH")
        with self.attempts.store.connect() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO remediation_recovery_policies VALUES (?, ?, ?)",
                (policy.environment_id, policy.policy_sha256, canonical(policy)),
            )
            row = connection.execute(
                "SELECT payload_json FROM remediation_recovery_policies WHERE environment_id = ?",
                (policy.environment_id,),
            ).fetchone()
            if row is None or row[0] != canonical(policy):
                raise fail("REMEDIATION_RECOVERY_POLICY_IMMUTABLE")

    def receipt(self, attempt_id: str) -> StepReceiptV1 | None:
        with self.attempts.store.connect() as connection:
            attempt = self.attempts._read(connection, attempt_id)
            row = connection.execute(
                "SELECT * FROM remediation_step_receipts WHERE attempt_id = ?",
                (attempt_id,),
            ).fetchone()
            if row is None:
                return None
            value = StepReceiptV1.model_validate_json(row["payload_json"])
            if (
                value.receipt_sha256 != row["receipt_sha256"]
                or value.attempt_id != attempt_id
                or value.write_intent_id != attempt.write_intent_id
                or value.write_intent_sha256 != attempt.write_intent_sha256
            ):
                raise fail("REMEDIATION_RECEIPT_BINDING_MISMATCH")
            validator_repo = copy(self.attempts)
            if validator_repo.binding is None:
                binding_row = connection.execute(
                    "SELECT b.payload_json FROM remediation_authorizations a "
                    "JOIN remediation_current_state_snapshots s ON s.snapshot_id = a.snapshot_id "
                    "JOIN remediation_state_bindings b ON b.binding_sha256 = s.binding_sha256 "
                    "WHERE a.authorization_id = ?",
                    (attempt.authorization_id,),
                ).fetchone()
                if binding_row is None:
                    raise fail("REMEDIATION_RECEIPT_BINDING_MISMATCH")
                validator_repo.binding = TrustedStateBindingV1.model_validate_json(
                    binding_row[0]
                )
            dispatch = ProductPaymentConfigurationRollbackExecutor(
                validator_repo
            )._dispatch(connection, attempt)
            binding = validator_repo.binding
            if (
                value.dispatch_sha256 != dispatch.dispatch_sha256
                or value.started_at < dispatch.created_at
                or value.ended_at > validator_repo.clock()
                or len(value.supporting_evidence_refs) != 1
            ):
                raise fail("REMEDIATION_RECEIPT_BINDING_MISMATCH")
            after = StateObservationV1.model_validate_json(
                validator_repo.objects.read_bytes(value.supporting_evidence_refs[0])
            )
            applied = value.outcome == "APPLIED"
            if (
                value.before_state_digest != binding.fault_configuration_digest
                or value.after_state_digest != after.current_configuration_digest
                or after.current_configuration_digest
                != (
                    binding.baseline_configuration_digest
                    if applied
                    else binding.fault_configuration_digest
                )
                or after.fault_still_present != (not applied)
                or not after.environment_owned
                or not after.local_control_trusted
                or after.target_logical_service != "payment"
                or any(
                    getattr(after, field) != getattr(binding, field)
                    for field in (
                        "environment_id",
                        "environment_ownership_digest",
                        "target_identity_digest",
                        "control_identity_sha256",
                        "baseline_configuration_digest",
                    )
                )
                or not value.started_at
                <= after.observed_at
                == after.created_at
                <= value.ended_at
            ):
                raise fail("REMEDIATION_RECEIPT_EVIDENCE_MISMATCH")
            return value

    def windows(self, attempt_id: str) -> tuple[RecoveryWindowV1, ...]:
        self.attempts.get(attempt_id)
        with self.attempts.store.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM remediation_recovery_windows WHERE attempt_id = ? ORDER BY ordinal",
                (attempt_id,),
            ).fetchall()
        result = tuple(
            RecoveryWindowV1.model_validate_json(row["payload_json"]) for row in rows
        )
        if any(
            value.window_sha256 != row["window_sha256"]
            or value.attempt_id != attempt_id
            or value.ordinal != row["ordinal"]
            for value, row in zip(result, rows, strict=True)
        ):
            raise fail("REMEDIATION_RECOVERY_WINDOW_BINDING_MISMATCH")
        return result

    def evaluation(self, attempt_id: str) -> RecoveryEvaluationV1 | None:
        attempt = self.attempts.get(attempt_id)
        with self.attempts.store.connect() as connection:
            row = connection.execute(
                "SELECT * FROM remediation_recovery_evaluations WHERE attempt_id = ?",
                (attempt_id,),
            ).fetchone()
        if row is None:
            return None
        value = RecoveryEvaluationV1.model_validate_json(row["payload_json"])
        if (
            value.evaluation_sha256 != row["evaluation_sha256"]
            or value.attempt_id != attempt_id
            or value.terminal != attempt.state.value
        ):
            raise fail("REMEDIATION_RECOVERY_EVALUATION_BINDING_MISMATCH")
        return value

    def verify(
        self, attempt_id: str, provider: RecoveryWindowProviderV1
    ) -> RemediationAttemptV1:
        repo = self.attempts
        receipt = self.receipt(attempt_id)
        if receipt is None or receipt.outcome != "APPLIED":
            raise fail("REMEDIATION_APPLIED_RECEIPT_REQUIRED")
        acquire_windows = True
        with repo.store.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                attempt = repo._read(connection, attempt_id)
                row = connection.execute(
                    "SELECT * FROM remediation_recovery_policies WHERE environment_id = ?",
                    (attempt.environment_id,),
                ).fetchone()
                if row is None:
                    raise fail("REMEDIATION_RECOVERY_POLICY_REQUIRED")
                policy = RecoveryPolicyV1.model_validate_json(row["payload_json"])
                dispatch = ProductPaymentConfigurationRollbackExecutor(repo)._dispatch(
                    connection, attempt
                )
                if policy.policy_sha256 != dispatch.recovery_policy_sha256:
                    raise fail("REMEDIATION_RECOVERY_POLICY_BINDING_MISMATCH")
                if (
                    row["policy_sha256"] != policy.policy_sha256
                    or policy.created_at >= receipt.started_at
                ):
                    raise fail("REMEDIATION_RECOVERY_POLICY_NOT_FROZEN")
                if attempt.terminal is not None:
                    connection.execute("COMMIT")
                    return attempt
                if attempt.state == S.APPLIED:
                    attempt = repo._update(
                        connection,
                        attempt,
                        state=S.VERIFYING,
                        active_lease_owner=new_id("lease"),
                        lease_generation=attempt.lease_generation + 1,
                        lease_expires_at=repo.clock()
                        + timedelta(seconds=2 * (policy.window_seconds * 3 + 30) + 30),
                    )
                    repo._event(connection, attempt, "VERIFICATION")
                elif attempt.state == S.VERIFYING:
                    if (
                        attempt.lease_expires_at is None
                        or repo.clock() < attempt.lease_expires_at
                    ):
                        raise fail("REMEDIATION_RECOVERY_IN_PROGRESS")
                    acquire_windows = False
                else:
                    raise fail("REMEDIATION_RECOVERY_STATE_MISMATCH")
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise
        for ordinal in (1, 2) if acquire_windows else ():
            with repo.store.connect() as connection:
                cursor = connection.execute(
                    "INSERT OR IGNORE INTO remediation_window_acquisitions VALUES (?, ?, ?)",
                    (attempt_id, ordinal, repo.clock().isoformat()),
                )
                if cursor.rowcount != 1:
                    continue
            existing = self.windows(attempt_id)
            started_after = max(
                [receipt.ended_at, repo.clock(), *(w.ended_at for w in existing)]
            )
            try:
                observation = provider.acquire(
                    started_after=started_after, policy=policy
                )
                observation = RecoveryObservationV1.model_validate_json(
                    observation.model_dump_json()
                )
                if (
                    observation.started_at <= started_after
                    or observation.created_at > repo.clock()
                ):
                    raise fail("REMEDIATION_RECOVERY_NOT_FRESH")
                reference = repo.objects.put_json(
                    observation.model_dump(mode="json")
                ).object_sha256
                window = window_from_observation(
                    attempt_id=attempt_id,
                    ordinal=ordinal,
                    receipt=receipt,
                    policy=policy,
                    observation=observation,
                    reference=reference,
                )
                with repo.store.connect() as connection:
                    connection.execute("BEGIN IMMEDIATE")
                    try:
                        attempt = repo._read(connection, attempt_id)
                        if attempt.state != S.VERIFYING:
                            raise fail("REMEDIATION_RECOVERY_STATE_MISMATCH")
                        connection.execute(
                            "INSERT INTO remediation_recovery_windows VALUES (?, ?, ?, ?)",
                            (
                                attempt_id,
                                ordinal,
                                window.window_sha256,
                                canonical(window),
                            ),
                        )
                        repo._event(
                            connection,
                            attempt,
                            "RECOVERY_WINDOW",
                            evidence_refs=(reference,),
                        )
                        connection.execute("COMMIT")
                    except Exception:
                        connection.execute("ROLLBACK")
                        raise
            except Exception:
                # The reserved acquisition is consumed; never reacquire that window.
                continue
        windows = self.windows(attempt_id)
        evaluation = evaluate(
            attempt_id=attempt_id,
            receipt=receipt,
            windows=windows,
            policy=policy,
            resolve=repo.objects.read_bytes,
            now=repo.clock(),
        )
        with repo.store.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                attempt = repo._read(connection, attempt_id)
                if attempt.terminal is not None:
                    connection.execute("COMMIT")
                    return attempt
                current_dispatch = ProductPaymentConfigurationRollbackExecutor(
                    repo
                )._dispatch(connection, attempt)
                if current_dispatch.recovery_policy_sha256 != evaluation.policy_sha256:
                    raise fail("REMEDIATION_RECOVERY_POLICY_BINDING_MISMATCH")
                connection.execute(
                    "INSERT INTO remediation_recovery_evaluations VALUES (?, ?, ?)",
                    (attempt_id, evaluation.evaluation_sha256, canonical(evaluation)),
                )
                result = repo._update(
                    connection,
                    attempt,
                    state=S(evaluation.terminal),
                    terminal=S(evaluation.terminal),
                    final_disposition=evaluation.final_disposition,
                    active_lease_owner=None,
                    lease_expires_at=None,
                )
                repo._event(connection, result, "VERIFICATION")
                connection.execute("COMMIT")
                return result
            except Exception:
                connection.execute("ROLLBACK")
                raise
