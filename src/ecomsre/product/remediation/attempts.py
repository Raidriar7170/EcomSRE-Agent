"""Transactional state-bound authorization and fenced pre-write lifecycle."""

from datetime import datetime, timedelta
import sqlite3
import time
from uuid import uuid4

from ecomsre.product.errors import ProductError
from ecomsre.product.remediation.approval import OperatorApprovalV1
from ecomsre.product.remediation.attempt_contracts import (
    AttemptCreationRecordV1,
    AttemptRequestV1,
    AttemptStateV1,
    RemediationAttemptV1,
    RemediationDecisionEventV1,
)
from ecomsre.product.remediation.authorization import AttemptAuthorizationV1
from ecomsre.product.remediation.contracts import RemediationCandidateV1
from ecomsre.product.remediation.repository import (
    RemediationRepositoryV1,
    canonical,
    fail,
)
from ecomsre.product.remediation.state import (
    CurrentStateProviderV1,
    CurrentStateSnapshotV1,
    DenialReasonV1,
    StateDeniedV1,
    StateObservationV1,
    TrustedStateBindingV1,
    validate_observation,
)


def new_id(prefix: str) -> str:
    return prefix + "-" + uuid4().hex[:24]


def denial_state(reason: DenialReasonV1) -> AttemptStateV1:
    return {
        DenialReasonV1.APPROVAL_EXPIRED: AttemptStateV1.APPROVAL_EXPIRED,
        DenialReasonV1.APPROVAL_REVOKED: AttemptStateV1.APPROVAL_REVOKED,
        DenialReasonV1.STATE_DRIFTED: AttemptStateV1.STATE_DRIFTED,
        DenialReasonV1.BASELINE_MISMATCH: AttemptStateV1.STATE_DRIFTED,
        DenialReasonV1.CONFIGURATION_DRIFT_NOT_VISIBLE: AttemptStateV1.NO_LONGER_APPLICABLE,
        DenialReasonV1.FAULT_NO_LONGER_PRESENT: AttemptStateV1.NO_LONGER_APPLICABLE,
        DenialReasonV1.AUTHORIZATION_EXPIRED: AttemptStateV1.AUTHORIZATION_EXPIRED,
    }.get(reason, AttemptStateV1.AUTHORIZATION_DENIED)


class RemediationAttemptRepositoryV1:
    def __init__(
        self,
        approvals: RemediationRepositoryV1,
        *,
        provider: CurrentStateProviderV1 | None = None,
        binding: TrustedStateBindingV1 | None = None,
    ) -> None:
        self.approvals = approvals
        self.store = approvals.store
        self.objects = approvals.objects
        self.clock = approvals.clock
        self.provider = provider
        self.binding = (
            TrustedStateBindingV1.model_validate_json(binding.model_dump_json())
            if binding is not None
            else None
        )
        if (provider is None) != (binding is None):
            raise fail("REMEDIATION_STATE_CONFIGURATION_INCOMPLETE")
        if self.binding is not None:
            with self.store.connect() as connection:
                connection.execute(
                    "INSERT OR IGNORE INTO remediation_state_bindings VALUES (?, ?, ?)",
                    (
                        self.binding.binding_sha256,
                        self.binding.environment_id,
                        canonical(self.binding),
                    ),
                )
                row = connection.execute(
                    "SELECT payload_json FROM remediation_state_bindings WHERE environment_id = ?",
                    (self.binding.environment_id,),
                ).fetchone()
                if row is None or row[0] != canonical(self.binding):
                    raise fail("REMEDIATION_STATE_BINDING_MISMATCH")

    def _capture(self) -> str:
        if self.provider is None or self.binding is None:
            raise StateDeniedV1(DenialReasonV1.REMOTE_OR_UNTRUSTED_CONTROL)
        try:
            value = self.provider.read_current()
            observation = StateObservationV1.model_validate_json(
                value.model_dump_json()
            )
            return self.objects.put_json(
                observation.model_dump(mode="json")
            ).object_sha256
        except Exception as error:
            raise StateDeniedV1(DenialReasonV1.REMOTE_OR_UNTRUSTED_CONTROL) from error

    def _observation(self, reference: str) -> StateObservationV1:
        try:
            return StateObservationV1.model_validate_json(
                self.objects.read_bytes(reference)
            )
        except Exception as error:
            raise StateDeniedV1(DenialReasonV1.EVIDENCE_BINDING_MISMATCH) from error

    def _current_candidate(self, candidate: RemediationCandidateV1) -> None:
        try:
            current = self.approvals.project(candidate.incident_id)
        except Exception as error:
            reason = (
                DenialReasonV1.BASELINE_MISMATCH
                if isinstance(error, ProductError) and error.code == "BASELINE_REQUIRED"
                else DenialReasonV1.EVIDENCE_BINDING_MISMATCH
            )
            raise StateDeniedV1(reason) from error
        if len(current.candidates) != 1 or current.candidates[0] != candidate:
            reason = DenialReasonV1.DIAGNOSIS_BINDING_MISMATCH
            if any(item.value == "BASELINE_MISMATCH" for item in current.reason_codes):
                reason = DenialReasonV1.BASELINE_MISMATCH
            raise StateDeniedV1(reason)

    def _snapshot(
        self,
        connection: sqlite3.Connection,
        candidate: RemediationCandidateV1,
        approval: OperatorApprovalV1,
        reference: str,
        now: datetime,
        *,
        active_count: int,
    ) -> CurrentStateSnapshotV1:
        if self.binding is None:
            raise StateDeniedV1(DenialReasonV1.REMOTE_OR_UNTRUSTED_CONTROL)
        row = connection.execute(
            "SELECT payload_json FROM remediation_state_bindings WHERE binding_sha256 = ?",
            (self.binding.binding_sha256,),
        ).fetchone()
        if row is None or row[0] != canonical(self.binding):
            raise StateDeniedV1(DenialReasonV1.STATE_DRIFTED)
        observation = self._observation(reference)
        now = self.clock()
        validate_observation(
            binding=self.binding,
            candidate=candidate,
            approval=approval,
            observation=observation,
            now=now,
        )
        snapshot = CurrentStateSnapshotV1.build(
            snapshot_id=new_id("snap"),
            environment_id=candidate.environment_id,
            incident_id=candidate.incident_id,
            incident_sha256=candidate.incident_sha256,
            candidate_id=candidate.candidate_id,
            candidate_sha256=candidate.candidate_sha256,
            approval_id=approval.approval_id,
            approval_sha256=approval.approval_sha256,
            trusted_binding_sha256=self.binding.binding_sha256,
            environment_ownership_digest=observation.environment_ownership_digest,
            target_identity_digest=observation.target_identity_digest,
            control_identity_sha256=observation.control_identity_sha256,
            baseline_id=candidate.baseline_id,
            baseline_sha256=candidate.baseline_sha256,
            baseline_configuration_digest=observation.baseline_configuration_digest,
            current_configuration_digest=observation.current_configuration_digest,
            configuration_drift_visible=True,
            active_remediation_count=active_count,
            fault_still_present=True,
            source_observation_refs=(reference,),
            observed_at=observation.observed_at,
            created_at=now,
        )
        connection.execute(
            "INSERT INTO remediation_current_state_snapshots VALUES (?, ?, ?, ?, ?, ?)",
            (
                snapshot.snapshot_id,
                snapshot.snapshot_sha256,
                candidate.candidate_id,
                approval.approval_id,
                self.binding.binding_sha256,
                canonical(snapshot),
            ),
        )
        return snapshot

    @staticmethod
    def _read(connection: sqlite3.Connection, attempt_id: str) -> RemediationAttemptV1:
        row = connection.execute(
            "SELECT * FROM remediation_attempts WHERE attempt_id = ?", (attempt_id,)
        ).fetchone()
        if row is None:
            raise fail("REMEDIATION_ATTEMPT_NOT_FOUND", 404)
        attempt = RemediationAttemptV1.model_validate_json(row["payload_json"])
        expected = {
            "attempt_id": attempt.attempt_id,
            "candidate_id": attempt.candidate_id,
            "approval_id": attempt.approval_id,
            "authorization_id": attempt.authorization_id,
            "environment_id": attempt.environment_id,
            "target": attempt.target_logical_service,
            "state": attempt.state.value,
            "terminal": attempt.terminal.value if attempt.terminal else None,
            "revision": attempt.revision,
            "attempt_sha256": attempt.attempt_sha256,
        }
        if any(row[key] != value for key, value in expected.items()):
            raise fail("REMEDIATION_ATTEMPT_BINDING_MISMATCH")
        revision = connection.execute(
            "SELECT payload_json FROM remediation_attempt_revisions WHERE attempt_id = ? AND revision = ? AND attempt_sha256 = ?",
            (attempt_id, attempt.revision, attempt.attempt_sha256),
        ).fetchone()
        if revision is None or revision[0] != canonical(attempt):
            raise fail("REMEDIATION_ATTEMPT_REVISION_MISMATCH")
        return attempt

    def get(self, attempt_id: str) -> RemediationAttemptV1:
        with self.store.connect() as connection:
            return self._read(connection, attempt_id)

    @staticmethod
    def _insert(connection: sqlite3.Connection, attempt: RemediationAttemptV1) -> None:
        connection.execute(
            "INSERT INTO remediation_attempts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                attempt.attempt_id,
                attempt.candidate_id,
                attempt.approval_id,
                attempt.authorization_id,
                attempt.environment_id,
                attempt.target_logical_service,
                attempt.state.value,
                attempt.terminal.value if attempt.terminal else None,
                attempt.revision,
                attempt.attempt_sha256,
                canonical(attempt),
            ),
        )
        connection.execute(
            "INSERT INTO remediation_attempt_revisions VALUES (?, ?, ?, ?)",
            (
                attempt.attempt_id,
                attempt.revision,
                attempt.attempt_sha256,
                canonical(attempt),
            ),
        )

    def _event(
        self,
        connection: sqlite3.Connection,
        attempt: RemediationAttemptV1,
        gate: str,
        *,
        reason: DenialReasonV1 | None = None,
        elapsed_ms: float = 0,
        event_state: AttemptStateV1 | None = None,
        evidence_refs: tuple[str, ...] = (),
    ) -> None:
        row = connection.execute(
            "SELECT ordinal, event_sha256 FROM remediation_decision_trace_events WHERE attempt_id = ? ORDER BY ordinal DESC LIMIT 1",
            (attempt.attempt_id,),
        ).fetchone()
        event = RemediationDecisionEventV1.build(
            attempt_id=attempt.attempt_id,
            ordinal=row[0] + 1 if row else 1,
            gate=gate,
            outcome="ESCALATE"
            if gate == "RECONCILIATION"
            else "DENY"
            if reason
            else "PASS",
            state=event_state or attempt.state,
            reason_code=reason,
            previous_event_sha256=row[1] if row else "0" * 64,
            attempt_sha256=attempt.attempt_sha256,
            evidence_refs=evidence_refs,
            elapsed_ms=elapsed_ms,
            created_at=self.clock(),
        )
        connection.execute(
            "INSERT INTO remediation_decision_trace_events VALUES (?, ?, ?, ?)",
            (attempt.attempt_id, event.ordinal, event.event_sha256, canonical(event)),
        )

    def trace(self, attempt_id: str) -> tuple[RemediationDecisionEventV1, ...]:
        with self.store.connect() as connection:
            connection.execute("BEGIN")
            attempt = self._read(connection, attempt_id)
            rows = connection.execute(
                "SELECT ordinal, event_sha256, payload_json FROM remediation_decision_trace_events WHERE attempt_id = ? ORDER BY ordinal",
                (attempt_id,),
            ).fetchall()
            previous = "0" * 64
            events = []
            for ordinal, row in enumerate(rows, 1):
                event = RemediationDecisionEventV1.model_validate_json(row[2])
                if (
                    event.ordinal != ordinal
                    or row[0] != ordinal
                    or event.event_sha256 != row[1]
                    or event.previous_event_sha256 != previous
                    or event.attempt_id != attempt_id
                ):
                    raise fail("REMEDIATION_TRACE_BINDING_MISMATCH")
                revision_row = connection.execute(
                    "SELECT payload_json FROM remediation_attempt_revisions WHERE attempt_id = ? AND attempt_sha256 = ?",
                    (attempt_id, event.attempt_sha256),
                ).fetchone()
                if revision_row is None:
                    raise fail("REMEDIATION_TRACE_BINDING_MISMATCH")
                revision = RemediationAttemptV1.model_validate_json(revision_row[0])
                if (
                    revision.attempt_id != attempt_id
                    or revision.attempt_sha256 != event.attempt_sha256
                ):
                    raise fail("REMEDIATION_TRACE_BINDING_MISMATCH")
                events.append(event)
                previous = event.event_sha256
            if (
                not events
                or events[-1].attempt_sha256 != attempt.attempt_sha256
                or events[-1].state != attempt.state
            ):
                raise fail("REMEDIATION_TRACE_BINDING_MISMATCH")
            return tuple(events)

    def create(
        self, candidate_id: str, request: AttemptRequestV1, key: str
    ) -> RemediationAttemptV1:
        from hashlib import sha256

        started = time.perf_counter()
        reference = None
        acquisition_denial = None
        with self.store.connect() as connection:
            existing = connection.execute(
                "SELECT 1 FROM remediation_idempotency_keys WHERE operation = 'attempt' AND key_sha256 = ?",
                (sha256(key.encode()).hexdigest(),),
            ).fetchone()
        if existing is None:
            try:
                reference = self._capture()
            except StateDeniedV1 as error:
                acquisition_denial = error.reason

        def create(connection: sqlite3.Connection) -> AttemptCreationRecordV1:
            candidate = self.approvals._candidate(connection, candidate_id)
            approval = self.approvals._approval(connection, request.approval_id)
            now = self.clock()
            authorization = None
            reason = None
            gate = "CANDIDATE"
            passed_gates: list[tuple[str, AttemptStateV1]] = []
            try:
                self._current_candidate(candidate)
                passed_gates.append(("CANDIDATE", AttemptStateV1.CANDIDATE_CREATED))
                gate = "APPROVAL"
                try:
                    self.approvals.require_active_approval(
                        connection, request.approval_id, candidate_id
                    )
                except ProductError as error:
                    mapped = {
                        "REMEDIATION_APPROVAL_EXPIRED": DenialReasonV1.APPROVAL_EXPIRED,
                        "REMEDIATION_APPROVAL_REVOKED": DenialReasonV1.APPROVAL_REVOKED,
                    }.get(error.code, DenialReasonV1.APPROVAL_BINDING_MISMATCH)
                    raise StateDeniedV1(mapped) from error
                if connection.execute(
                    "SELECT 1 FROM remediation_approval_consumptions WHERE approval_id = ?",
                    (request.approval_id,),
                ).fetchone():
                    raise StateDeniedV1(DenialReasonV1.APPROVAL_ALREADY_CONSUMED)
                passed_gates.append(("APPROVAL", AttemptStateV1.APPROVED))
                gate = "ACTIVE_TRANSACTION"
                active = connection.execute(
                    "SELECT count(*) FROM remediation_attempts WHERE environment_id = ? AND target = 'payment' AND terminal IS NULL",
                    (candidate.environment_id,),
                ).fetchone()[0]
                if active:
                    raise StateDeniedV1(DenialReasonV1.SECOND_ACTIVE_TRANSACTION)
                passed_gates.append(("ACTIVE_TRANSACTION", AttemptStateV1.APPROVED))
                gate = "STATE_BINDING"
                if acquisition_denial is not None:
                    raise StateDeniedV1(acquisition_denial)
                if reference is None:
                    raise StateDeniedV1(DenialReasonV1.EVIDENCE_BINDING_MISMATCH)
                snapshot = self._snapshot(
                    connection, candidate, approval, reference, now, active_count=active
                )
                passed_gates.extend(
                    (entry, AttemptStateV1.APPROVED)
                    for entry in ("OWNERSHIP", "BASELINE", "DRIFT")
                )
                passed_gates.append(("STATE_BINDING", AttemptStateV1.STATE_BOUND))
                gate = "AUTHORIZATION"
                now = self.clock()
                self._final_time_gate(connection, approval, snapshot, now)
                # Snapshot and minted authorization share the final transaction anchor.
                if snapshot.created_at != now:
                    snapshot = self._reanchor_snapshot(connection, snapshot, now)
                authorization = AttemptAuthorizationV1.build(
                    authorization_id=new_id("auth"),
                    candidate_id=candidate_id,
                    candidate_sha256=candidate.candidate_sha256,
                    approval_id=approval.approval_id,
                    approval_sha256=approval.approval_sha256,
                    current_state_snapshot_id=snapshot.snapshot_id,
                    current_state_sha256=snapshot.snapshot_sha256,
                    diagnosis_sha256=candidate.diagnosis_sha256,
                    evidence_bundle_sha256=candidate.evidence_bundle_sha256,
                    baseline_sha256=candidate.baseline_sha256,
                    registry_sha256=candidate.registry_sha256,
                    runbook_sha256=candidate.runbook_sha256,
                    parameters_sha256=candidate.parameters_sha256,
                    issued_at=now,
                    created_at=now,
                    expires_at=min(now + timedelta(minutes=5), approval.expires_at),
                )
                connection.execute(
                    "INSERT INTO remediation_authorizations VALUES (?, ?, ?, ?, ?)",
                    (
                        authorization.authorization_id,
                        authorization.authorization_sha256,
                        approval.approval_id,
                        snapshot.snapshot_id,
                        canonical(authorization),
                    ),
                )
            except StateDeniedV1 as error:
                reason = error.reason
            state = denial_state(reason) if reason else AttemptStateV1.AUTHORIZED
            attempt = RemediationAttemptV1.build(
                attempt_id=new_id("attempt"),
                environment_id=candidate.environment_id,
                candidate_id=candidate_id,
                candidate_sha256=candidate.candidate_sha256,
                approval_sha256=approval.approval_sha256,
                authorization_sha256=authorization.authorization_sha256
                if authorization
                else None,
                approval_id=approval.approval_id,
                authorization_id=authorization.authorization_id
                if authorization
                else None,
                state=state,
                terminal=state if reason else None,
                safe_error_code=reason,
                final_disposition="NO_WRITE" if reason else "PENDING",
                created_at=now,
                updated_at=now,
            )
            self._insert(connection, attempt)
            if authorization:
                connection.execute(
                    "INSERT INTO remediation_approval_consumptions VALUES (?, ?, ?)",
                    (approval.approval_id, attempt.attempt_id, now.isoformat()),
                )
                passed_gates.append(("AUTHORIZATION", AttemptStateV1.AUTHORIZED))
            for accepted_gate, event_state in passed_gates:
                self._event(
                    connection,
                    attempt,
                    accepted_gate,
                    event_state=event_state,
                    elapsed_ms=(time.perf_counter() - started) * 1000,
                )
            if not authorization:
                self._event(
                    connection,
                    attempt,
                    gate,
                    reason=reason,
                    evidence_refs=(reference,) if reference else (),
                    elapsed_ms=(time.perf_counter() - started) * 1000,
                )
            creation = AttemptCreationRecordV1.build(
                attempt_id=attempt.attempt_id,
                candidate_id=candidate_id,
                approval_id=approval.approval_id,
                candidate_sha256=candidate.candidate_sha256,
                approval_sha256=approval.approval_sha256,
                initial_attempt_sha256=attempt.attempt_sha256,
                created_at=now,
            )
            connection.execute(
                "INSERT INTO remediation_attempt_creations VALUES (?, ?, ?)",
                (attempt.attempt_id, creation.creation_sha256, canonical(creation)),
            )
            return creation

        def validate(
            connection: sqlite3.Connection, creation: AttemptCreationRecordV1
        ) -> None:
            attempt = self._read(connection, creation.attempt_id)
            row = connection.execute(
                "SELECT creation_sha256, payload_json FROM remediation_attempt_creations WHERE attempt_id = ?",
                (creation.attempt_id,),
            ).fetchone()
            if (
                creation.candidate_id != candidate_id
                or creation.approval_id != request.approval_id
                or attempt.candidate_id != candidate_id
                or attempt.approval_id != request.approval_id
                or creation.candidate_sha256 != attempt.candidate_sha256
                or creation.approval_sha256 != attempt.approval_sha256
                or row is None
                or row[0] != creation.creation_sha256
                or row[1] != canonical(creation)
                or connection.execute(
                    "SELECT 1 FROM remediation_attempt_revisions WHERE attempt_id = ? AND revision = 0 AND attempt_sha256 = ?",
                    (creation.attempt_id, creation.initial_attempt_sha256),
                ).fetchone()
                is None
            ):
                raise fail("REMEDIATION_ATTEMPT_BINDING_MISMATCH")

        creation = self.approvals._once(
            "attempt",
            key,
            {"candidate_id": candidate_id, "request": request.model_dump(mode="json")},
            AttemptCreationRecordV1,
            create,
            validate,
        )
        return self.get(creation.attempt_id)

    def _authorization(
        self, connection: sqlite3.Connection, attempt: RemediationAttemptV1
    ) -> AttemptAuthorizationV1:
        row = connection.execute(
            "SELECT * FROM remediation_authorizations WHERE authorization_id = ?",
            (attempt.authorization_id,),
        ).fetchone()
        if row is None:
            raise fail("REMEDIATION_AUTHORIZATION_MISSING")
        authorization = AttemptAuthorizationV1.model_validate_json(row["payload_json"])
        candidate = self.approvals._candidate(connection, attempt.candidate_id)
        approval = self.approvals._approval(connection, attempt.approval_id)
        snapshot_row = connection.execute(
            "SELECT * FROM remediation_current_state_snapshots WHERE snapshot_id = ?",
            (authorization.current_state_snapshot_id,),
        ).fetchone()
        if snapshot_row is None:
            raise fail("REMEDIATION_STATE_BINDING_MISMATCH")
        snapshot = CurrentStateSnapshotV1.model_validate_json(
            snapshot_row["payload_json"]
        )
        expected = {
            "candidate_id": candidate.candidate_id,
            "candidate_sha256": candidate.candidate_sha256,
            "approval_id": approval.approval_id,
            "approval_sha256": approval.approval_sha256,
            "diagnosis_sha256": candidate.diagnosis_sha256,
            "evidence_bundle_sha256": candidate.evidence_bundle_sha256,
            "baseline_sha256": candidate.baseline_sha256,
            "registry_sha256": candidate.registry_sha256,
            "runbook_sha256": candidate.runbook_sha256,
            "parameters_sha256": candidate.parameters_sha256,
            "current_state_sha256": snapshot.snapshot_sha256,
        }
        consumed = connection.execute(
            "SELECT attempt_id FROM remediation_approval_consumptions WHERE approval_id = ?",
            (approval.approval_id,),
        ).fetchone()
        if (
            any(getattr(authorization, key) != value for key, value in expected.items())
            or row["authorization_sha256"] != authorization.authorization_sha256
            or row["approval_id"] != approval.approval_id
            or row["snapshot_id"] != snapshot.snapshot_id
            or authorization.authorization_id != attempt.authorization_id
            or authorization.authorization_sha256 != attempt.authorization_sha256
            or attempt.candidate_sha256 != candidate.candidate_sha256
            or attempt.approval_sha256 != approval.approval_sha256
            or attempt.environment_id != candidate.environment_id
            or snapshot.incident_id != candidate.incident_id
            or snapshot.incident_sha256 != candidate.incident_sha256
            or snapshot.environment_id != candidate.environment_id
            or snapshot_row["snapshot_sha256"] != snapshot.snapshot_sha256
            or snapshot_row["candidate_id"] != candidate.candidate_id
            or snapshot_row["approval_id"] != approval.approval_id
            or snapshot.candidate_sha256 != candidate.candidate_sha256
            or snapshot.approval_sha256 != approval.approval_sha256
            or snapshot.candidate_id != candidate.candidate_id
            or snapshot.approval_id != approval.approval_id
            or snapshot.baseline_id != candidate.baseline_id
            or snapshot.baseline_sha256 != candidate.baseline_sha256
            or authorization.issued_at != snapshot.created_at
            or authorization.expires_at > approval.expires_at
            or consumed is None
            or consumed[0] != attempt.attempt_id
        ):
            raise fail("REMEDIATION_AUTHORIZATION_BINDING_MISMATCH")
        binding_row = connection.execute(
            "SELECT payload_json FROM remediation_state_bindings WHERE binding_sha256 = ?",
            (snapshot.trusted_binding_sha256,),
        ).fetchone()
        if (
            binding_row is None
            or snapshot_row["binding_sha256"] != snapshot.trusted_binding_sha256
        ):
            raise fail("REMEDIATION_STATE_BINDING_MISMATCH")
        binding = TrustedStateBindingV1.model_validate_json(binding_row[0])
        if binding.binding_sha256 != snapshot.trusted_binding_sha256:
            raise fail("REMEDIATION_STATE_BINDING_MISMATCH")
        observation = self._observation(snapshot.source_observation_refs[0])
        validate_observation(
            binding=binding,
            candidate=candidate,
            approval=approval,
            observation=observation,
            now=snapshot.created_at,
        )
        if (
            any(
                getattr(snapshot, field) != getattr(observation, field)
                for field in (
                    "environment_id",
                    "environment_ownership_digest",
                    "target_identity_digest",
                    "control_identity_sha256",
                    "baseline_configuration_digest",
                    "current_configuration_digest",
                    "observed_at",
                    "fault_still_present",
                )
            )
            or snapshot.active_remediation_count != 0
            or not snapshot.configuration_drift_visible
        ):
            raise fail("REMEDIATION_STATE_BINDING_MISMATCH")
        return authorization

    def _update(
        self,
        connection: sqlite3.Connection,
        attempt: RemediationAttemptV1,
        **changes: object,
    ) -> RemediationAttemptV1:
        if attempt.terminal is not None:
            raise fail("REMEDIATION_TERMINAL_IMMUTABLE")
        result = RemediationAttemptV1.build(
            **{
                **attempt.model_dump(mode="python", exclude={"attempt_sha256"}),
                **changes,
                "revision": attempt.revision + 1,
                "updated_at": self.clock(),
            }
        )
        allowed = {
            AttemptStateV1.AUTHORIZED: {
                AttemptStateV1.AUTHORIZED,
                AttemptStateV1.WRITE_INTENT_COMMITTED,
                AttemptStateV1.APPROVAL_EXPIRED,
                AttemptStateV1.APPROVAL_REVOKED,
                AttemptStateV1.STATE_DRIFTED,
                AttemptStateV1.NO_LONGER_APPLICABLE,
                AttemptStateV1.AUTHORIZATION_DENIED,
                AttemptStateV1.AUTHORIZATION_EXPIRED,
                AttemptStateV1.CANCELLED_BEFORE_WRITE,
            },
            AttemptStateV1.WRITE_INTENT_COMMITTED: {
                AttemptStateV1.EXECUTING, AttemptStateV1.OUTCOME_UNKNOWN,
            },
            AttemptStateV1.EXECUTING: {
                AttemptStateV1.APPLIED, AttemptStateV1.EXECUTION_FAILED,
                AttemptStateV1.OUTCOME_UNKNOWN,
            },
            AttemptStateV1.APPLIED: {AttemptStateV1.VERIFYING, AttemptStateV1.OUTCOME_UNKNOWN},
            AttemptStateV1.VERIFYING: {
                AttemptStateV1.RECOVERED, AttemptStateV1.VERIFICATION_FAILED,
                AttemptStateV1.OUTCOME_UNKNOWN,
            },
        }
        if result.state not in allowed.get(attempt.state, set()):
            raise fail("REMEDIATION_TRANSITION_DENIED")
        if any(
            getattr(result, field) != getattr(attempt, field)
            for field in (
                "attempt_id",
                "environment_id",
                "target_logical_service",
                "candidate_id",
                "candidate_sha256",
                "approval_id",
                "approval_sha256",
                "authorization_id",
                "authorization_sha256",
                "created_at",
            )
        ):
            raise fail("REMEDIATION_TRANSITION_PARENT_MISMATCH")
        from ecomsre.product.remediation.execution_guards import guard_execution_transition

        guard_execution_transition(connection, attempt, result)
        if attempt.write_intent_id is not None and (
            result.write_intent_id != attempt.write_intent_id
            or result.write_intent_sha256 != attempt.write_intent_sha256
        ):
            raise fail("REMEDIATION_TRANSITION_PARENT_MISMATCH")
        if result.state is AttemptStateV1.WRITE_INTENT_COMMITTED:
            intent_row = connection.execute(
                "SELECT write_intent_sha256 FROM remediation_write_intents WHERE write_intent_id = ? AND attempt_id = ? AND authorization_id = ?",
                (result.write_intent_id, attempt.attempt_id, attempt.authorization_id),
            ).fetchone()
            if intent_row is None or intent_row[0] != result.write_intent_sha256:
                raise fail("REMEDIATION_PRIOR_WRITE_INTENT_REQUIRED")
        cursor = connection.execute(
            "UPDATE remediation_attempts SET state = ?, terminal = ?, revision = ?, attempt_sha256 = ?, payload_json = ? "
            "WHERE attempt_id = ? AND revision = ? AND attempt_sha256 = ? AND terminal IS NULL",
            (
                result.state.value,
                result.terminal.value if result.terminal else None,
                result.revision,
                result.attempt_sha256,
                canonical(result),
                attempt.attempt_id,
                attempt.revision,
                attempt.attempt_sha256,
            ),
        )
        if cursor.rowcount != 1:
            raise fail("REMEDIATION_CONCURRENT_TRANSITION")
        connection.execute(
            "INSERT INTO remediation_attempt_revisions VALUES (?, ?, ?, ?)",
            (
                result.attempt_id,
                result.revision,
                result.attempt_sha256,
                canonical(result),
            ),
        )
        return result

    def _deny(
        self,
        connection: sqlite3.Connection,
        attempt: RemediationAttemptV1,
        reason: DenialReasonV1,
        gate: str,
    ) -> RemediationAttemptV1:
        state = denial_state(reason)
        result = self._update(
            connection,
            attempt,
            state=state,
            terminal=state,
            safe_error_code=reason,
            final_disposition="NO_WRITE",
            active_lease_owner=None,
            lease_expires_at=None,
        )
        self._event(connection, result, gate, reason=reason)
        return result

    def claim(self, attempt_id: str) -> RemediationAttemptV1:
        with self.store.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                attempt = self._read(connection, attempt_id)
                if attempt.write_intent_id is not None:
                    raise fail("REMEDIATION_RECONCILIATION_REQUIRED")
                if attempt.state is not AttemptStateV1.AUTHORIZED:
                    raise fail("REMEDIATION_ATTEMPT_NOT_AUTHORIZED")
                authorization = self._authorization(connection, attempt)
                now = self.clock()
                if not authorization.issued_at <= now < authorization.expires_at:
                    result = self._deny(
                        connection,
                        attempt,
                        DenialReasonV1.AUTHORIZATION_EXPIRED,
                        "AUTHORIZATION",
                    )
                else:
                    if (
                        attempt.lease_expires_at is not None
                        and attempt.lease_expires_at > now
                    ):
                        raise fail("REMEDIATION_LEASE_ACTIVE")
                    if connection.execute(
                        "SELECT 1 FROM remediation_write_intents WHERE attempt_id = ?",
                        (attempt_id,),
                    ).fetchone():
                        raise fail("REMEDIATION_PRIOR_WRITE_INTENT")
                    result = self._update(
                        connection,
                        attempt,
                        active_lease_owner=new_id("lease"),
                        lease_generation=attempt.lease_generation + 1,
                        lease_expires_at=min(
                            now + timedelta(seconds=30), authorization.expires_at
                        ),
                    )
                    self._event(connection, result, "LEASE")
                connection.execute("COMMIT")
                return result
            except Exception:
                connection.execute("ROLLBACK")
                raise

    def _require_lease(
        self, attempt: RemediationAttemptV1, owner: str, generation: int
    ) -> None:
        if (
            attempt.active_lease_owner != owner
            or attempt.lease_generation != generation
            or attempt.lease_expires_at is None
            or self.clock() >= attempt.lease_expires_at
        ):
            raise fail("REMEDIATION_LEASE_LOST")

    def commit_write_intent(
        self, attempt_id: str, *, lease_owner: str, lease_generation: int
    ) -> RemediationAttemptV1:
        from ecomsre.product.remediation.attempt_contracts import WriteIntentV1

        before = self.get(attempt_id)
        if before.write_intent_id is not None:
            raise fail("REMEDIATION_PRIOR_WRITE_INTENT")
        self._require_lease(before, lease_owner, lease_generation)
        reference = None
        acquisition_denial = None
        try:
            reference = self._capture()
        except StateDeniedV1 as error:
            acquisition_denial = error.reason
        with self.store.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                attempt = self._read(connection, attempt_id)
                self._require_lease(attempt, lease_owner, lease_generation)
                if (
                    attempt.state is not AttemptStateV1.AUTHORIZED
                    or attempt.write_intent_id is not None
                ):
                    raise fail("REMEDIATION_PRIOR_WRITE_INTENT")
                authorization = self._authorization(connection, attempt)
                candidate = self.approvals._candidate(connection, attempt.candidate_id)
                approval = self.approvals._approval(connection, attempt.approval_id)
                now = self.clock()
                try:
                    if not authorization.issued_at <= now < authorization.expires_at:
                        raise StateDeniedV1(DenialReasonV1.AUTHORIZATION_EXPIRED)
                    try:
                        self.approvals.require_active_approval(
                            connection, attempt.approval_id, attempt.candidate_id
                        )
                    except ProductError as error:
                        mapped = (
                            DenialReasonV1.APPROVAL_REVOKED
                            if error.code == "REMEDIATION_APPROVAL_REVOKED"
                            else DenialReasonV1.APPROVAL_EXPIRED
                        )
                        raise StateDeniedV1(mapped) from error
                    self._current_candidate(candidate)
                    active = connection.execute(
                        "SELECT count(*) FROM remediation_attempts WHERE environment_id = ? AND target = 'payment' AND terminal IS NULL",
                        (attempt.environment_id,),
                    ).fetchone()[0]
                    if active != 1:
                        raise StateDeniedV1(DenialReasonV1.SECOND_ACTIVE_TRANSACTION)
                    if connection.execute(
                        "SELECT 1 FROM remediation_authorization_consumptions WHERE authorization_id = ?",
                        (authorization.authorization_id,),
                    ).fetchone():
                        raise StateDeniedV1(DenialReasonV1.PRIOR_WRITE_INTENT)
                    if acquisition_denial is not None:
                        raise StateDeniedV1(acquisition_denial)
                    if reference is None:
                        raise StateDeniedV1(DenialReasonV1.EVIDENCE_BINDING_MISMATCH)
                    if (
                        self._observation(reference).observed_at
                        <= authorization.issued_at
                    ):
                        raise StateDeniedV1(DenialReasonV1.STATE_STALE)
                    snapshot = self._snapshot(
                        connection,
                        candidate,
                        approval,
                        reference,
                        now,
                        active_count=active,
                    )
                except StateDeniedV1 as error:
                    result = self._deny(
                        connection, attempt, error.reason, "WRITE_INTENT"
                    )
                    connection.execute("COMMIT")
                    return result
                now = self.clock()
                self._require_lease(attempt, lease_owner, lease_generation)
                try:
                    self._final_time_gate(
                        connection, approval, snapshot, now, authorization=authorization
                    )
                except StateDeniedV1 as error:
                    result = self._deny(
                        connection, attempt, error.reason, "WRITE_INTENT"
                    )
                    connection.execute("COMMIT")
                    return result
                intent = WriteIntentV1.build(
                    write_intent_id=new_id("intent"),
                    attempt_sha256=attempt.attempt_sha256,
                    attempt_id=attempt_id,
                    authorization_id=authorization.authorization_id,
                    authorization_sha256=authorization.authorization_sha256,
                    runbook_sha256=authorization.runbook_sha256,
                    before_state_snapshot_id=snapshot.snapshot_id,
                    before_state_sha256=snapshot.snapshot_sha256,
                    committed_at=now,
                    created_at=now,
                )
                connection.execute(
                    "INSERT INTO remediation_write_intents VALUES (?, ?, ?, ?, ?)",
                    (
                        intent.write_intent_id,
                        attempt_id,
                        authorization.authorization_id,
                        intent.write_intent_sha256,
                        canonical(intent),
                    ),
                )
                connection.execute(
                    "INSERT INTO remediation_authorization_consumptions VALUES (?, ?, ?)",
                    (
                        authorization.authorization_id,
                        intent.write_intent_id,
                        now.isoformat(),
                    ),
                )
                result = self._update(
                    connection,
                    attempt,
                    state=AttemptStateV1.WRITE_INTENT_COMMITTED,
                    write_intent_id=intent.write_intent_id,
                    write_intent_sha256=intent.write_intent_sha256,
                )
                self._event(connection, result, "WRITE_INTENT")
                connection.execute("COMMIT")
                return result
            except Exception:
                connection.execute("ROLLBACK")
                raise

    def cancel_before_write(self, attempt_id: str) -> RemediationAttemptV1:
        with self.store.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                attempt = self._read(connection, attempt_id)
                if attempt.write_intent_id is not None:
                    raise fail("REMEDIATION_RECONCILIATION_REQUIRED")
                result = self._update(
                    connection,
                    attempt,
                    state=AttemptStateV1.CANCELLED_BEFORE_WRITE,
                    terminal=AttemptStateV1.CANCELLED_BEFORE_WRITE,
                    final_disposition="NO_WRITE",
                    active_lease_owner=None,
                    lease_expires_at=None,
                )
                self._event(connection, result, "CANCELLATION")
                connection.execute("COMMIT")
                return result
            except Exception:
                connection.execute("ROLLBACK")
                raise

    def reconcile_expired_intent(self, attempt_id: str) -> RemediationAttemptV1:
        # Read-only acquisition cannot turn a missing receipt into recovery proof.
        reference = None
        try:
            reference = self._capture()
        except StateDeniedV1:
            pass
        with self.store.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                attempt = self._read(connection, attempt_id)
                if attempt.state is AttemptStateV1.OUTCOME_UNKNOWN:
                    connection.execute("COMMIT")
                    return attempt
                if attempt.write_intent_id is None or attempt.lease_expires_at is None:
                    raise fail("REMEDIATION_RECONCILIATION_NOT_APPLICABLE")
                if self.clock() < attempt.lease_expires_at:
                    raise fail("REMEDIATION_LEASE_ACTIVE")
                result = self._update(
                    connection,
                    attempt,
                    state=AttemptStateV1.OUTCOME_UNKNOWN,
                    terminal=AttemptStateV1.OUTCOME_UNKNOWN,
                    safe_error_code=DenialReasonV1.RECONCILIATION_REQUIRED,
                    final_disposition="ESCALATE_HUMAN",
                    active_lease_owner=None,
                    lease_expires_at=None,
                )
                self._event(
                    connection,
                    result,
                    "RECONCILIATION",
                    reason=DenialReasonV1.RECONCILIATION_REQUIRED,
                    evidence_refs=(reference,) if reference else (),
                )
                connection.execute("COMMIT")
                return result
            except Exception:
                connection.execute("ROLLBACK")
                raise

    def _final_time_gate(
        self,
        connection: sqlite3.Connection,
        approval: OperatorApprovalV1,
        snapshot: CurrentStateSnapshotV1,
        now: datetime,
        *,
        authorization: AttemptAuthorizationV1 | None = None,
    ) -> None:
        if (
            authorization is not None
            and not authorization.issued_at <= now < authorization.expires_at
        ):
            raise StateDeniedV1(DenialReasonV1.AUTHORIZATION_EXPIRED)
        if not approval.issued_at <= now < approval.expires_at:
            raise StateDeniedV1(DenialReasonV1.APPROVAL_EXPIRED)
        if connection.execute(
            "SELECT 1 FROM remediation_revocations WHERE approval_id = ?",
            (approval.approval_id,),
        ).fetchone():
            raise StateDeniedV1(DenialReasonV1.APPROVAL_REVOKED)
        if (
            not approval.issued_at < snapshot.observed_at <= now
            or now - snapshot.observed_at > timedelta(seconds=30)
        ):
            raise StateDeniedV1(DenialReasonV1.STATE_STALE)

    @staticmethod
    def _reanchor_snapshot(
        connection: sqlite3.Connection, snapshot: CurrentStateSnapshotV1, now: datetime
    ) -> CurrentStateSnapshotV1:
        result = CurrentStateSnapshotV1.build(
            **{
                **snapshot.model_dump(mode="python", exclude={"snapshot_sha256"}),
                "created_at": now,
            }
        )
        connection.execute(
            "UPDATE remediation_current_state_snapshots SET snapshot_sha256 = ?, payload_json = ? WHERE snapshot_id = ? AND snapshot_sha256 = ?",
            (
                result.snapshot_sha256,
                canonical(result),
                snapshot.snapshot_id,
                snapshot.snapshot_sha256,
            ),
        )
        return result
