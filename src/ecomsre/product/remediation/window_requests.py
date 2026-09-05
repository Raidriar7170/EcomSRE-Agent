"""Create-once requests from the verifier to the independent local observer.

Only the gateway writes this private spool. The host observer may read requests
and write signed responses; API, Worker and executor never mount the spool.
"""

from datetime import datetime
import os
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr

from ecomsre.product.remediation.contracts import SealedRemediationModelV1, Sha256
from ecomsre.product.remediation.execution_contracts import (
    RecoveryObservationV1,
    RecoveryPolicyV1,
)
from ecomsre.product.remediation.recovery import RecoveryRepositoryV1
from ecomsre.product.remediation.recovery_transport import (
    SignedRecoveryWindowProviderV1,
)
from ecomsre.product.remediation.repository import canonical, fail


class ObserverWindowRequestV1(SealedRemediationModelV1):
    seal_field = "request_sha256"
    schema_version: Literal["ecomsre.product.observer-window-request.v1"] = (
        "ecomsre.product.observer-window-request.v1"
    )
    attempt_id: str = Field(pattern=r"^attempt-[0-9a-f]{24}$")
    ordinal: Literal[1, 2]
    receipt_sha256: Sha256
    policy_sha256: Sha256
    started_after: datetime
    created_at: datetime
    request_sha256: Sha256


def create_private_file(path: Path, content: bytes) -> None:
    """Consume before publication; incomplete files are retained, never replaced."""
    if path.parent.is_symlink() or path.parent.stat().st_mode & 0o077:
        raise ValueError("private evidence directory required")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())
    directory = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


class RequestedRecoveryWindowProviderV1:
    def __init__(
        self,
        recovery: RecoveryRepositoryV1,
        requests: Path,
        responses: Path,
        observer_key: SecretStr,
    ) -> None:
        self.recovery = recovery
        self.requests = requests
        self.responses = responses
        self._key = observer_key

    def reserve(
        self, *, started_after: datetime, policy: RecoveryPolicyV1
    ) -> ObserverWindowRequestV1:
        repo = self.recovery.attempts
        now = repo.clock()
        if (
            started_after.tzinfo is None
            or not 0 <= (now - started_after).total_seconds() <= 30
        ):
            raise fail("REMEDIATION_RECOVERY_REQUEST_NOT_FRESH")
        with repo.store.connect() as connection:
            rows = connection.execute(
                "SELECT attempt_id FROM remediation_attempts "
                "WHERE environment_id = ? AND terminal IS NULL AND state = 'VERIFYING'",
                (policy.environment_id,),
            ).fetchall()
            if len(rows) != 1:
                raise fail("REMEDIATION_RECOVERY_REQUEST_NO_ACTIVE_VERIFIER")
            attempt = repo._read(connection, rows[0][0])
            if attempt.lease_expires_at is None or now >= attempt.lease_expires_at:
                raise fail("REMEDIATION_RECOVERY_REQUEST_LEASE_EXPIRED")
            frozen = connection.execute(
                "SELECT payload_json FROM remediation_recovery_policies WHERE environment_id = ?",
                (policy.environment_id,),
            ).fetchone()
            if frozen is None or frozen[0] != canonical(policy):
                raise fail("REMEDIATION_RECOVERY_POLICY_BINDING_MISMATCH")
            slots = connection.execute(
                "SELECT ordinal, started_at FROM remediation_window_acquisitions "
                "WHERE attempt_id = ? ORDER BY ordinal",
                (attempt.attempt_id,),
            ).fetchall()
            if not slots or len(slots) > 2:
                raise fail("REMEDIATION_RECOVERY_REQUEST_NO_RESERVED_WINDOW")
            ordinal, reserved_at = slots[-1]
            if (
                ordinal != len(slots)
                or datetime.fromisoformat(reserved_at) > started_after
                or connection.execute(
                    "SELECT 1 FROM remediation_recovery_windows WHERE attempt_id = ? AND ordinal = ?",
                    (attempt.attempt_id, ordinal),
                ).fetchone()
            ):
                raise fail("REMEDIATION_RECOVERY_REQUEST_WINDOW_CONSUMED")
        receipt = self.recovery.receipt(attempt.attempt_id)
        if (
            receipt is None
            or receipt.outcome != "APPLIED"
            or receipt.ended_at > started_after
        ):
            raise fail("REMEDIATION_APPLIED_RECEIPT_REQUIRED")
        request = ObserverWindowRequestV1.build(
            attempt_id=attempt.attempt_id,
            ordinal=ordinal,
            receipt_sha256=receipt.receipt_sha256,
            policy_sha256=policy.policy_sha256,
            started_after=started_after,
            created_at=now,
        )
        # Fixed attempt/ordinal name prevents a retry with a different timestamp
        # from creating a replacement request after an uncertain acquisition.
        create_private_file(
            self.requests / f"{attempt.attempt_id}-{ordinal}.json",
            request.model_dump_json().encode(),
        )
        return request

    def acquire(
        self, *, started_after: datetime, policy: RecoveryPolicyV1
    ) -> RecoveryObservationV1:
        request = self.reserve(started_after=started_after, policy=policy)
        return SignedRecoveryWindowProviderV1(
            self.responses / f"{request.request_sha256}.json", self._key
        ).acquire(started_after=started_after, policy=policy)
