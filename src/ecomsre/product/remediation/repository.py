"""Transactional approval persistence. This repository has no execution authority."""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
from typing import Literal, TypeVar
from uuid import uuid4

from pydantic import BaseModel

from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22
from ecomsre.product.errors import ProductError
from ecomsre.product.remediation.approval import (
    ApprovalRequestV1,
    ApprovalRevocationV1,
    ApprovalStatusV1,
    OperatorApprovalV1,
    RevocationRequestV1,
)
from ecomsre.product.remediation.contracts import (
    CandidateProjectionV1,
    RemediationCandidateV1,
)
from ecomsre.product.remediation.migrations import migrate
from ecomsre.product.remediation.registry import load_registry
from ecomsre.product.remediation.source import project_for_incident
from ecomsre.product.storage.object_store import ContentAddressedObjectStoreV1
from ecomsre.product.storage.sqlite_store import SqliteStoreV1


REGISTRY_SHA256 = "5722c03cbc94539212a054557ab65b1ecdb145e76e86904689d75df188b588b1"
REGISTRY_PATH = (
    Path(__file__).resolve().parents[4]
    / "config/product-v040/remediation-registry.v1.json"
)
Model = TypeVar("Model", bound=BaseModel)


def canonical(model: BaseModel) -> str:
    return json.dumps(
        model.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def fail(code: str, status: int = 409) -> ProductError:
    return ProductError(
        code,
        "The remediation request could not be accepted safely.",
        status_code=status,
    )


class RemediationRepositoryV1:
    def __init__(
        self,
        store: SqliteStoreV1,
        objects: ContentAddressedObjectStoreV1,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.store = store
        self.objects = objects
        self.clock = clock
        self.registry = load_registry(REGISTRY_PATH, expected_sha256=REGISTRY_SHA256)
        migrate(store)
        with store.connect() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO remediation_registry_versions VALUES (?, ?)",
                (REGISTRY_SHA256, canonical(self.registry)),
            )
            row = connection.execute(
                "SELECT payload_json FROM remediation_registry_versions WHERE registry_sha256 = ?",
                (REGISTRY_SHA256,),
            ).fetchone()
            if row[0] != canonical(self.registry):
                raise fail("REMEDIATION_REGISTRY_BINDING_MISMATCH")

    def _once(
        self,
        operation: str,
        key: str,
        payload: dict[str, object],
        model: type[Model],
        create: Callable[[sqlite3.Connection], Model],
        validate: Callable[[sqlite3.Connection, Model], None],
    ) -> Model:
        if not 1 <= len(key) <= 128 or any(
            ord(char) < 33 or ord(char) > 126 for char in key
        ):
            raise fail("INVALID_IDEMPOTENCY_KEY", 422)
        key_hash = sha256(key.encode()).hexdigest()
        request_hash = semantic_sha256_v22(payload)
        with self.store.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    "SELECT request_sha256, response_json, response_sha256 FROM remediation_idempotency_keys "
                    "WHERE operation = ? AND key_sha256 = ?",
                    (operation, key_hash),
                ).fetchone()
                if row is not None:
                    if row[0] != request_hash:
                        raise fail("IDEMPOTENCY_CONFLICT")
                    if sha256(row[1].encode()).hexdigest() != row[2]:
                        raise fail("REMEDIATION_IDEMPOTENCY_BINDING_MISMATCH")
                    result = model.model_validate_json(row[1])
                else:
                    result = create(connection)
                    connection.execute(
                        "INSERT INTO remediation_idempotency_keys VALUES (?, ?, ?, ?, ?)",
                        (
                            operation,
                            key_hash,
                            request_hash,
                            sha256(canonical(result).encode()).hexdigest(),
                            canonical(result),
                        ),
                    )
                validate(connection, result)
                connection.execute("COMMIT")
                return result
            except Exception:
                connection.execute("ROLLBACK")
                raise

    def project(self, incident_id: str) -> CandidateProjectionV1:
        return project_for_incident(
            incident_id,
            store=self.store,
            objects=self.objects,
            registry=self.registry,
            expected_registry_sha256=REGISTRY_SHA256,
        )

    def create_candidates(self, incident_id: str, key: str) -> CandidateProjectionV1:
        def create(connection: sqlite3.Connection) -> CandidateProjectionV1:
            result = self.project(incident_id)
            connection.execute(
                "INSERT OR IGNORE INTO remediation_candidate_projections VALUES (?, ?, ?)",
                (result.projection_sha256, incident_id, canonical(result)),
            )
            for candidate in result.candidates:
                connection.execute(
                    "INSERT OR IGNORE INTO remediation_candidates VALUES (?, ?, ?, ?, ?)",
                    (
                        candidate.candidate_id,
                        candidate.candidate_sha256,
                        incident_id,
                        candidate.registry_sha256,
                        canonical(candidate),
                    ),
                )
                if self._candidate(connection, candidate.candidate_id) != candidate:
                    raise fail("REMEDIATION_CANDIDATE_BINDING_MISMATCH")
            return result

        def validate(
            connection: sqlite3.Connection, result: CandidateProjectionV1
        ) -> None:
            row = connection.execute(
                "SELECT incident_id, payload_json FROM remediation_candidate_projections WHERE projection_sha256 = ?",
                (result.projection_sha256,),
            ).fetchone()
            if (
                result.incident_id != incident_id
                or row is None
                or row[0] != incident_id
                or row[1] != canonical(result)
            ):
                raise fail("REMEDIATION_PROJECTION_BINDING_MISMATCH")
            for candidate in result.candidates:
                if self._candidate(connection, candidate.candidate_id) != candidate:
                    raise fail("REMEDIATION_CANDIDATE_BINDING_MISMATCH")

        return self._once(
            "candidate",
            key,
            {"incident_id": incident_id},
            CandidateProjectionV1,
            create,
            validate,
        )

    @staticmethod
    def _candidate(
        connection: sqlite3.Connection, candidate_id: str
    ) -> RemediationCandidateV1:
        row = connection.execute(
            "SELECT payload_json, candidate_sha256, incident_id, registry_sha256 FROM remediation_candidates WHERE candidate_id = ?",
            (candidate_id,),
        ).fetchone()
        if row is None:
            raise fail("REMEDIATION_CANDIDATE_NOT_FOUND", 404)
        candidate = RemediationCandidateV1.model_validate_json(row[0])
        if (
            candidate.candidate_id != candidate_id
            or candidate.candidate_sha256 != row[1]
            or candidate.incident_id != row[2]
            or candidate.registry_sha256 != row[3]
        ):
            raise fail("REMEDIATION_CANDIDATE_BINDING_MISMATCH")
        return candidate

    def get_candidate(self, candidate_id: str) -> RemediationCandidateV1:
        with self.store.connect() as connection:
            return self._candidate(connection, candidate_id)

    @staticmethod
    def _approval(
        connection: sqlite3.Connection, approval_id: str
    ) -> OperatorApprovalV1:
        row = connection.execute(
            "SELECT candidate_id, payload_json, approval_sha256 FROM remediation_approvals WHERE approval_id = ?",
            (approval_id,),
        ).fetchone()
        if row is None:
            raise fail("REMEDIATION_APPROVAL_NOT_FOUND", 404)
        approval = OperatorApprovalV1.model_validate_json(row[1])
        candidate = RemediationRepositoryV1._candidate(connection, row[0])
        if (
            approval.approval_sha256 != row[2]
            or approval.approval_id != approval_id
            or approval.candidate_id != candidate.candidate_id
            or approval.candidate_sha256 != candidate.candidate_sha256
        ):
            raise fail("REMEDIATION_APPROVAL_BINDING_MISMATCH")
        return approval

    def approve(
        self, candidate_id: str, request: ApprovalRequestV1, key: str
    ) -> OperatorApprovalV1:
        def create(connection: sqlite3.Connection) -> OperatorApprovalV1:
            candidate = self._candidate(connection, candidate_id)
            if candidate.registry_sha256 != REGISTRY_SHA256:
                raise fail("REMEDIATION_REGISTRY_BINDING_MISMATCH")
            now = self.clock()
            approval = OperatorApprovalV1.build(
                approval_id="appr-" + uuid4().hex[:24],
                candidate_id=candidate_id,
                candidate_sha256=candidate.candidate_sha256,
                **request.model_dump(exclude={"ttl_seconds", "scope"}),
                scope=request.scope,
                created_at=now,
                issued_at=now,
                expires_at=now + timedelta(seconds=request.ttl_seconds),
            )
            connection.execute(
                "INSERT INTO remediation_approvals VALUES (?, ?, ?, ?)",
                (
                    approval.approval_id,
                    candidate_id,
                    approval.approval_sha256,
                    canonical(approval),
                ),
            )
            return approval

        def validate(
            connection: sqlite3.Connection, result: OperatorApprovalV1
        ) -> None:
            expected_request = {
                **result.model_dump(
                    mode="json",
                    include={"approver", "authorization_source", "decision", "scope"},
                ),
                "ttl_seconds": int(
                    (result.expires_at - result.issued_at).total_seconds()
                ),
            }
            if (
                result.candidate_id != candidate_id
                or expected_request != request.model_dump(mode="json")
                or self._approval(connection, result.approval_id) != result
            ):
                raise fail("REMEDIATION_APPROVAL_BINDING_MISMATCH")

        return self._once(
            "approval",
            key,
            {"candidate_id": candidate_id, "request": request.model_dump(mode="json")},
            OperatorApprovalV1,
            create,
            validate,
        )

    def _status(
        self, connection: sqlite3.Connection, approval_id: str
    ) -> ApprovalStatusV1:
        approval = self._approval(connection, approval_id)
        row = connection.execute(
            "SELECT payload_json, revocation_id FROM remediation_revocations WHERE approval_id = ?",
            (approval_id,),
        ).fetchone()
        revocation = ApprovalRevocationV1.model_validate_json(row[0]) if row else None
        if revocation and (
            revocation.approval_id != approval_id
            or revocation.approval_sha256 != approval.approval_sha256
            or revocation.candidate_id != approval.candidate_id
            or revocation.revocation_id != row[1]
        ):
            raise fail("REMEDIATION_REVOCATION_BINDING_MISMATCH")
        now = self.clock()
        status: Literal["ACTIVE", "EXPIRED", "REVOKED", "NOT_YET_VALID"] = (
            "REVOKED"
            if revocation
            else "EXPIRED"
            if now >= approval.expires_at
            else "NOT_YET_VALID"
            if now < approval.issued_at
            else "ACTIVE"
        )
        return ApprovalStatusV1(approval=approval, status=status, revocation=revocation)

    def approval_status(self, approval_id: str) -> ApprovalStatusV1:
        with self.store.connect() as connection:
            connection.execute("BEGIN")
            return self._status(connection, approval_id)

    def require_active_approval(
        self, connection: sqlite3.Connection, approval_id: str, candidate_id: str
    ) -> OperatorApprovalV1:
        """Read prerequisite inside caller's transaction; never grants write authority."""
        if not connection.in_transaction:
            raise fail("REMEDIATION_TRANSACTION_REQUIRED")
        status = self._status(connection, approval_id)
        if status.approval.candidate_id != candidate_id:
            raise fail("REMEDIATION_APPROVAL_BINDING_MISMATCH")
        if status.status != "ACTIVE":
            raise fail("REMEDIATION_APPROVAL_" + status.status)
        return status.approval

    def revoke(
        self, candidate_id: str, request: RevocationRequestV1, key: str
    ) -> ApprovalRevocationV1:
        def create(connection: sqlite3.Connection) -> ApprovalRevocationV1:
            approval = self._approval(connection, request.approval_id)
            if approval.candidate_id != candidate_id:
                raise fail("REMEDIATION_APPROVAL_BINDING_MISMATCH")
            row = connection.execute(
                "SELECT payload_json FROM remediation_revocations WHERE approval_id = ?",
                (request.approval_id,),
            ).fetchone()
            if row:
                existing = self._status(connection, request.approval_id).revocation
                if existing is None:
                    raise fail("REMEDIATION_REVOCATION_BINDING_MISMATCH")
                return existing
            now = self.clock()
            revocation = ApprovalRevocationV1.build(
                revocation_id="revo-" + uuid4().hex[:24],
                candidate_id=candidate_id,
                approval_id=approval.approval_id,
                approval_sha256=approval.approval_sha256,
                revoked_at=now,
                created_at=now,
                reason=request.reason,
            )
            connection.execute(
                "INSERT INTO remediation_revocations VALUES (?, ?, ?)",
                (revocation.revocation_id, approval.approval_id, canonical(revocation)),
            )
            return revocation

        def validate(
            connection: sqlite3.Connection, result: ApprovalRevocationV1
        ) -> None:
            if (
                result.candidate_id != candidate_id
                or result.approval_id != request.approval_id
                or self._status(connection, request.approval_id).revocation != result
            ):
                raise fail("REMEDIATION_REVOCATION_BINDING_MISMATCH")

        return self._once(
            "revocation",
            key,
            {"candidate_id": candidate_id, "request": request.model_dump(mode="json")},
            ApprovalRevocationV1,
            create,
            validate,
        )
