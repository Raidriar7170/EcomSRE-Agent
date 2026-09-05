"""One dispatch per committed intent; any uncertain external outcome is terminal."""

from datetime import datetime
from typing import Protocol
import sqlite3
import time

from ecomsre.product.remediation.attempt_contracts import (
    AttemptStateV1 as S,
    RemediationAttemptV1,
    WriteIntentV1,
)
from ecomsre.product.remediation.attempts import RemediationAttemptRepositoryV1, new_id
from ecomsre.product.remediation.execution_contracts import (
    ExecutorDispatchV1,
    RecoveryPolicyV1,
    StepReceiptV1,
)
from ecomsre.product.remediation.repository import canonical, fail
from ecomsre.product.remediation.state import (
    CurrentStateSnapshotV1,
    DenialReasonV1,
    StateObservationV1,
    validate_observation,
)


class RestoreNotAppliedV1(Exception):
    """Trusted adapter proves rejection before sending any external mutation."""

    def __init__(self, observation: StateObservationV1) -> None:
        self.observation = observation
        super().__init__("RESTORE_NOT_APPLIED")


class ExactPaymentRestoreAdapterV1(Protocol):
    """Trusted process dependency. Request contains no target, URL or flag value."""

    def restore_baseline(
        self, dispatch: ExecutorDispatchV1, *, expires_at: datetime
    ) -> StateObservationV1: ...


class ProductPaymentConfigurationRollbackExecutor:
    def __init__(
        self,
        repository: RemediationAttemptRepositoryV1,
        adapter: ExactPaymentRestoreAdapterV1 | None = None,
    ) -> None:
        self.repository = repository
        self.adapter = adapter

    def run_one(self, attempt_id: str) -> RemediationAttemptV1:
        """Only a newly claimed pre-intent attempt can enter this operation."""
        if self.adapter is None:
            raise fail("REMEDIATION_EXECUTOR_CAPABILITY_UNAVAILABLE")
        repo = self.repository
        leased = repo.claim(attempt_id)
        if leased.terminal is not None:
            return leased
        assert leased.active_lease_owner is not None
        intent_attempt = repo.commit_write_intent(
            attempt_id,
            lease_owner=leased.active_lease_owner,
            lease_generation=leased.lease_generation,
        )
        if intent_attempt.terminal is not None:
            return intent_attempt
        return self._execute_new_intent(intent_attempt)

    def _intent(
        self, connection: sqlite3.Connection, attempt: RemediationAttemptV1
    ) -> WriteIntentV1:
        repo = self.repository
        row = connection.execute(
            "SELECT * FROM remediation_write_intents WHERE attempt_id = ?",
            (attempt.attempt_id,),
        ).fetchone()
        if row is None:
            raise fail("REMEDIATION_PRIOR_WRITE_INTENT_REQUIRED")
        intent = WriteIntentV1.model_validate_json(row["payload_json"])
        authorization = repo._authorization(connection, attempt)
        revision = connection.execute(
            "SELECT payload_json FROM remediation_attempt_revisions WHERE attempt_id = ? AND attempt_sha256 = ?",
            (attempt.attempt_id, intent.attempt_sha256),
        ).fetchone()
        if revision is None:
            raise fail("REMEDIATION_INTENT_BINDING_MISMATCH")
        prior = RemediationAttemptV1.model_validate_json(revision[0])
        snapshot_row = connection.execute(
            "SELECT * FROM remediation_current_state_snapshots WHERE snapshot_id = ?",
            (intent.before_state_snapshot_id,),
        ).fetchone()
        consumed = connection.execute(
            "SELECT write_intent_id FROM remediation_authorization_consumptions WHERE authorization_id = ?",
            (authorization.authorization_id,),
        ).fetchone()
        if (
            snapshot_row is None
            or consumed is None
            or consumed[0] != intent.write_intent_id
        ):
            raise fail("REMEDIATION_INTENT_BINDING_MISMATCH")
        snapshot = CurrentStateSnapshotV1.model_validate_json(
            snapshot_row["payload_json"]
        )
        candidate = repo.approvals._candidate(connection, attempt.candidate_id)
        approval = repo.approvals._approval(connection, attempt.approval_id)
        if repo.binding is None:
            raise fail("REMEDIATION_STATE_BINDING_MISMATCH")
        observation = repo._observation(snapshot.source_observation_refs[0])
        validate_observation(
            binding=repo.binding,
            candidate=candidate,
            approval=approval,
            observation=observation,
            now=snapshot.created_at,
        )
        expected = {
            "candidate_id": attempt.candidate_id,
            "candidate_sha256": attempt.candidate_sha256,
            "approval_id": attempt.approval_id,
            "approval_sha256": attempt.approval_sha256,
            "environment_id": attempt.environment_id,
            "incident_id": candidate.incident_id,
            "incident_sha256": candidate.incident_sha256,
            "baseline_id": candidate.baseline_id,
            "baseline_sha256": candidate.baseline_sha256,
            "trusted_binding_sha256": repo.binding.binding_sha256,
            "environment_ownership_digest": observation.environment_ownership_digest,
            "target_identity_digest": observation.target_identity_digest,
            "control_identity_sha256": observation.control_identity_sha256,
            "current_configuration_digest": observation.current_configuration_digest,
            "baseline_configuration_digest": observation.baseline_configuration_digest,
            "observed_at": observation.observed_at,
        }
        if (
            intent.attempt_id != attempt.attempt_id
            or intent.write_intent_id != attempt.write_intent_id
            or intent.write_intent_sha256 != attempt.write_intent_sha256
            or row["write_intent_sha256"] != intent.write_intent_sha256
            or row["write_intent_id"] != intent.write_intent_id
            or row["authorization_id"] != intent.authorization_id
            or intent.authorization_id != authorization.authorization_id
            or intent.authorization_sha256 != authorization.authorization_sha256
            or intent.runbook_sha256 != authorization.runbook_sha256
            or prior.state != S.AUTHORIZED
            or prior.write_intent_id is not None
            or prior.authorization_sha256 != attempt.authorization_sha256
            or intent.before_state_sha256 != snapshot.snapshot_sha256
            or snapshot_row["snapshot_sha256"] != snapshot.snapshot_sha256
            or snapshot_row["candidate_id"] != snapshot.candidate_id
            or snapshot_row["approval_id"] != snapshot.approval_id
            or snapshot_row["binding_sha256"] != snapshot.trusted_binding_sha256
            or any(getattr(snapshot, k) != v for k, v in expected.items())
            or snapshot.active_remediation_count != 1
            or not snapshot.fault_still_present
            or not snapshot.configuration_drift_visible
            or not snapshot.observed_at <= snapshot.created_at <= intent.committed_at
            or snapshot.observed_at <= authorization.issued_at
        ):
            raise fail("REMEDIATION_INTENT_BINDING_MISMATCH")
        return intent

    def _reserve(
        self, leased: RemediationAttemptV1
    ) -> tuple[ExecutorDispatchV1, datetime]:
        repo = self.repository
        reference = repo._capture()
        with repo.store.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                attempt = repo._read(connection, leased.attempt_id)
                if attempt.state != S.WRITE_INTENT_COMMITTED:
                    raise fail("REMEDIATION_RECONCILIATION_REQUIRED")
                repo._require_lease(
                    attempt, leased.active_lease_owner or "", leased.lease_generation
                )
                intent = self._intent(connection, attempt)
                authorization = repo._authorization(connection, attempt)
                policy = self._policy(connection, attempt)
                if policy.created_at >= authorization.issued_at:
                    raise fail("REMEDIATION_RECOVERY_POLICY_NOT_FROZEN")
                candidate = repo.approvals._candidate(connection, attempt.candidate_id)
                approval = repo.approvals.require_active_approval(
                    connection, attempt.approval_id, attempt.candidate_id
                )
                repo._current_candidate(candidate)
                snapshot = repo._snapshot(
                    connection,
                    candidate,
                    approval,
                    reference,
                    repo.clock(),
                    active_count=1,
                )
                repo._final_time_gate(
                    connection,
                    approval,
                    snapshot,
                    repo.clock(),
                    authorization=authorization,
                )
                repo._require_lease(
                    attempt, leased.active_lease_owner or "", leased.lease_generation
                )
                if snapshot.observed_at < intent.committed_at:
                    raise fail("REMEDIATION_STATE_STALE")
                dispatch = ExecutorDispatchV1.build(
                    attempt_id=attempt.attempt_id,
                    write_intent_id=intent.write_intent_id,
                    write_intent_sha256=intent.write_intent_sha256,
                    authorization_sha256=authorization.authorization_sha256,
                    recovery_policy_sha256=policy.policy_sha256,
                    before_state_sha256=snapshot.snapshot_sha256,
                    created_at=repo.clock(),
                )
                connection.execute(
                    "INSERT INTO remediation_executor_dispatches VALUES (?, ?, ?, ?)",
                    (
                        attempt.attempt_id,
                        intent.write_intent_id,
                        dispatch.dispatch_sha256,
                        canonical(dispatch),
                    ),
                )
                result = repo._update(
                    connection, attempt, state=S.EXECUTING, forward_write_count=None
                )
                repo._event(connection, result, "EXECUTOR", evidence_refs=(reference,))
                connection.execute("COMMIT")
                return dispatch, min(authorization.expires_at, approval.expires_at)
            except Exception:
                connection.execute("ROLLBACK")
                raise

    def _policy(
        self, connection: sqlite3.Connection, attempt: RemediationAttemptV1
    ) -> RecoveryPolicyV1:
        row = connection.execute(
            "SELECT * FROM remediation_recovery_policies WHERE environment_id = ?",
            (attempt.environment_id,),
        ).fetchone()
        binding = self.repository.binding
        if row is None or binding is None:
            raise fail("REMEDIATION_RECOVERY_POLICY_REQUIRED")
        policy = RecoveryPolicyV1.model_validate_json(row["payload_json"])
        if row["policy_sha256"] != policy.policy_sha256 or any(
            getattr(policy, field) != getattr(binding, field)
            for field in (
                "environment_id",
                "baseline_sha256",
                "baseline_configuration_digest",
                "fault_configuration_digest",
                "target_identity_digest",
                "control_identity_sha256",
                "environment_ownership_digest",
            )
        ):
            raise fail("REMEDIATION_RECOVERY_POLICY_BINDING_MISMATCH")
        return policy

    def _dispatch(
        self, connection: sqlite3.Connection, attempt: RemediationAttemptV1
    ) -> ExecutorDispatchV1:
        repo = self.repository
        intent = self._intent(connection, attempt)
        row = connection.execute(
            "SELECT * FROM remediation_executor_dispatches WHERE attempt_id = ?",
            (attempt.attempt_id,),
        ).fetchone()
        if row is None:
            raise fail("REMEDIATION_DISPATCH_REQUIRED")
        dispatch = ExecutorDispatchV1.model_validate_json(row["payload_json"])
        policy = self._policy(connection, attempt)
        if (
            dispatch.recovery_policy_sha256 != policy.policy_sha256
            or policy.created_at >= dispatch.created_at
        ):
            raise fail("REMEDIATION_RECOVERY_POLICY_BINDING_MISMATCH")
        snapshot_row = connection.execute(
            "SELECT * FROM remediation_current_state_snapshots WHERE snapshot_sha256 = ?",
            (dispatch.before_state_sha256,),
        ).fetchone()
        before_row = connection.execute(
            "SELECT payload_json FROM remediation_current_state_snapshots WHERE snapshot_id = ?",
            (intent.before_state_snapshot_id,),
        ).fetchone()
        if snapshot_row is None or before_row is None or repo.binding is None:
            raise fail("REMEDIATION_DISPATCH_BINDING_MISMATCH")
        snapshot = CurrentStateSnapshotV1.model_validate_json(
            snapshot_row["payload_json"]
        )
        before = CurrentStateSnapshotV1.model_validate_json(before_row[0])
        anchors = {
            "snapshot_id",
            "snapshot_sha256",
            "source_observation_refs",
            "observed_at",
            "created_at",
        }
        observation = repo._observation(snapshot.source_observation_refs[0])
        candidate = repo.approvals._candidate(connection, attempt.candidate_id)
        approval = repo.approvals._approval(connection, attempt.approval_id)
        validate_observation(
            binding=repo.binding,
            candidate=candidate,
            approval=approval,
            observation=observation,
            now=snapshot.created_at,
        )
        if (
            dispatch.attempt_id != attempt.attempt_id
            or dispatch.write_intent_id != intent.write_intent_id
            or dispatch.write_intent_sha256 != intent.write_intent_sha256
            or dispatch.authorization_sha256 != attempt.authorization_sha256
            or row["dispatch_sha256"] != dispatch.dispatch_sha256
            or row["write_intent_id"] != dispatch.write_intent_id
            or dispatch.before_state_sha256 != snapshot.snapshot_sha256
            or snapshot_row["snapshot_id"] != snapshot.snapshot_id
            or snapshot_row["candidate_id"] != snapshot.candidate_id
            or snapshot_row["approval_id"] != snapshot.approval_id
            or snapshot_row["binding_sha256"] != snapshot.trusted_binding_sha256
            or snapshot.model_dump(exclude=anchors)
            != before.model_dump(exclude=anchors)
            or not intent.committed_at
            <= snapshot.observed_at
            <= snapshot.created_at
            <= dispatch.created_at
            or any(
                getattr(snapshot, name) != getattr(observation, name)
                for name in (
                    "observed_at",
                    "environment_id",
                    "environment_ownership_digest",
                    "target_identity_digest",
                    "control_identity_sha256",
                    "current_configuration_digest",
                    "baseline_configuration_digest",
                    "fault_still_present",
                )
            )
        ):
            raise fail("REMEDIATION_DISPATCH_BINDING_MISMATCH")
        return dispatch

    def _execute_new_intent(self, leased: RemediationAttemptV1) -> RemediationAttemptV1:
        repo = self.repository
        if self.adapter is None:
            raise fail("REMEDIATION_EXECUTOR_CAPABILITY_UNAVAILABLE")
        try:
            dispatch, expires_at = self._reserve(leased)
        except Exception:
            # A durable intent can never be retried, even when dispatch was denied.
            return self.mark_unknown(leased.attempt_id)
        started = repo.clock()
        monotonic_start = time.monotonic()
        try:
            if started >= expires_at:
                raise fail("REMEDIATION_AUTHORIZATION_EXPIRED")
            applied = True
            try:
                after = self.adapter.restore_baseline(dispatch, expires_at=expires_at)
            except RestoreNotAppliedV1 as rejected:
                applied = False
                after = rejected.observation
            after = StateObservationV1.model_validate_json(after.model_dump_json())
            ended = repo.clock()
            binding = repo.binding
            assert binding is not None
            if (
                not after.environment_owned
                or not after.local_control_trusted
                or after.environment_id != binding.environment_id
                or after.environment_ownership_digest
                != binding.environment_ownership_digest
                or after.target_identity_digest != binding.target_identity_digest
                or after.control_identity_sha256 != binding.control_identity_sha256
                or after.target_logical_service != "payment"
                or after.baseline_configuration_digest
                != binding.baseline_configuration_digest
                or after.current_configuration_digest
                != (
                    binding.baseline_configuration_digest
                    if applied
                    else binding.fault_configuration_digest
                )
                or after.fault_still_present != (not applied)
                or not started <= after.observed_at <= ended
                or after.created_at != after.observed_at
            ):
                raise fail("REMEDIATION_RESTORE_OUTCOME_UNKNOWN")
            reference = repo.objects.put_json(
                after.model_dump(mode="json")
            ).object_sha256
            receipt = StepReceiptV1.build(
                receipt_id=new_id("receipt"),
                attempt_id=leased.attempt_id,
                write_intent_id=dispatch.write_intent_id,
                write_intent_sha256=dispatch.write_intent_sha256,
                dispatch_sha256=dispatch.dispatch_sha256,
                before_state_digest=binding.fault_configuration_digest,
                after_state_digest=after.current_configuration_digest,
                started_at=started,
                ended_at=ended,
                elapsed_ms=(time.monotonic() - monotonic_start) * 1000,
                outcome="APPLIED" if applied else "FAILED",
                safe_error_code=None if applied else "RESTORE_NOT_APPLIED",
                supporting_evidence_refs=(reference,),
                created_at=ended,
            )
            return self.persist_receipt(receipt)
        except Exception:
            # Never log raw exception or retry the external request/receipt after uncertainty.
            return self.mark_unknown(leased.attempt_id)

    def persist_receipt(self, receipt: StepReceiptV1) -> RemediationAttemptV1:
        repo = self.repository
        receipt = StepReceiptV1.model_validate_json(receipt.model_dump_json())
        with repo.store.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                attempt = repo._read(connection, receipt.attempt_id)
                if attempt.state != S.EXECUTING:
                    raise fail("REMEDIATION_RECEIPT_STATE_MISMATCH")
                repo._require_lease(
                    attempt, attempt.active_lease_owner or "", attempt.lease_generation
                )
                dispatch = self._dispatch(connection, attempt)
                if (
                    receipt.dispatch_sha256 != dispatch.dispatch_sha256
                    or receipt.started_at < dispatch.created_at
                    or receipt.ended_at > repo.clock()
                ):
                    raise fail("REMEDIATION_RECEIPT_BINDING_MISMATCH")
                if len(receipt.supporting_evidence_refs) != 1 or repo.binding is None:
                    raise fail("REMEDIATION_RECEIPT_EVIDENCE_MISMATCH")
                after = StateObservationV1.model_validate_json(
                    repo.objects.read_bytes(receipt.supporting_evidence_refs[0])
                )
                binding = repo.binding
                applied = receipt.outcome == "APPLIED"
                if (
                    receipt.before_state_digest != binding.fault_configuration_digest
                    or receipt.after_state_digest != after.current_configuration_digest
                    or after.current_configuration_digest
                    != (
                        binding.baseline_configuration_digest
                        if applied
                        else binding.fault_configuration_digest
                    )
                    or after.fault_still_present != (not applied)
                    or not after.environment_owned
                    or not after.local_control_trusted
                    or after.environment_id != binding.environment_id
                    or after.target_logical_service != "payment"
                    or after.environment_ownership_digest
                    != binding.environment_ownership_digest
                    or after.target_identity_digest != binding.target_identity_digest
                    or after.control_identity_sha256 != binding.control_identity_sha256
                    or after.baseline_configuration_digest
                    != binding.baseline_configuration_digest
                    or not receipt.started_at
                    <= after.observed_at
                    == after.created_at
                    <= receipt.ended_at
                ):
                    raise fail("REMEDIATION_RECEIPT_EVIDENCE_MISMATCH")
                repo._require_lease(
                    attempt, attempt.active_lease_owner or "", attempt.lease_generation
                )
                connection.execute(
                    "INSERT INTO remediation_step_receipts VALUES (?, ?, ?)",
                    (attempt.attempt_id, receipt.receipt_sha256, canonical(receipt)),
                )
                applied = receipt.outcome == "APPLIED"
                result = repo._update(
                    connection,
                    attempt,
                    state=S.APPLIED if applied else S.EXECUTION_FAILED,
                    terminal=None if applied else S.EXECUTION_FAILED,
                    forward_write_count=int(applied),
                    final_disposition="PENDING" if applied else "ESCALATE_HUMAN",
                    active_lease_owner=None,
                    lease_expires_at=None,
                )
                repo._event(
                    connection,
                    result,
                    "RECEIPT",
                    evidence_refs=receipt.supporting_evidence_refs,
                )
                connection.execute("COMMIT")
                return result
            except Exception:
                connection.execute("ROLLBACK")
                raise

    def mark_unknown(self, attempt_id: str) -> RemediationAttemptV1:
        repo = self.repository
        with repo.store.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                attempt = repo._read(connection, attempt_id)
                if attempt.terminal is not None:
                    connection.execute("COMMIT")
                    return attempt
                result = repo._update(
                    connection,
                    attempt,
                    state=S.OUTCOME_UNKNOWN,
                    terminal=S.OUTCOME_UNKNOWN,
                    forward_write_count=None
                    if attempt.state == S.EXECUTING
                    else attempt.forward_write_count,
                    final_disposition="ESCALATE_HUMAN",
                    safe_error_code=DenialReasonV1.RECONCILIATION_REQUIRED,
                    active_lease_owner=None,
                    lease_expires_at=None,
                )
                repo._event(
                    connection,
                    result,
                    "RECONCILIATION",
                    reason=DenialReasonV1.RECONCILIATION_REQUIRED,
                )
                connection.execute("COMMIT")
                return result
            except Exception:
                connection.execute("ROLLBACK")
                raise
