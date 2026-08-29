"""Restart-persistence proof for a passing Product v0.2.3 baseline."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Literal

from pydantic import Field, model_validator

from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22
from ecomsre.product.connectors.opensearch_profile_binding_v023 import (
    ACTIVE_PROFILE_BINDING_SHA256_V023,
    ACTIVE_PROFILE_SHA256_V023,
)
from ecomsre.product.contracts import ProductModelV1


BASELINE_RESTART_PASS_V023 = "ECOMSRE_PRODUCT_V023_BASELINE_RESTART_PASS"


class BaselineRestartSnapshotV023(ProductModelV1):
    schema_version: Literal[
        "ecomsre.product.baseline-restart-snapshot.v023"
    ] = "ecomsre.product.baseline-restart-snapshot.v023"
    environment_id: str = Field(pattern=r"^env-[0-9a-f]{24}$")
    environment_payload_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    profile_binding_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    profile_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    active_baseline_id: str = Field(pattern=r"^base-[0-9a-f]{24}$")
    active_baseline_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    baseline_count: Literal[1]
    service_identity_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    capability_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    api_instance_id: str = Field(pattern=r"^api-[0-9a-f]{24}$")
    worker_instance_id: str = Field(pattern=r"^worker-[0-9a-f]{24}$")
    observed_at: datetime
    pending_jobs: int = Field(ge=0)
    running_jobs: int = Field(ge=0)
    failed_jobs: int = Field(ge=0)
    snapshot_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_bound_snapshot(self) -> "BaselineRestartSnapshotV023":
        if self.observed_at.tzinfo is None or self.observed_at.utcoffset() != timedelta(0):
            raise ValueError("Product v0.2.3 restart observation time must be UTC")
        if (
            self.profile_binding_sha256 != ACTIVE_PROFILE_BINDING_SHA256_V023
            or self.profile_sha256 != ACTIVE_PROFILE_SHA256_V023
        ):
            raise ValueError("Product v0.2.3 restart profile binding differs")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"snapshot_sha256"})
        )
        if self.snapshot_sha256 != expected:
            raise ValueError("baseline restart snapshot digest differs")
        return self

    @classmethod
    def build(cls, **payload: Any) -> "BaselineRestartSnapshotV023":
        body = {
            "schema_version": "ecomsre.product.baseline-restart-snapshot.v023",
            **payload,
        }
        draft = cls.model_construct(**body, snapshot_sha256="0" * 64)
        normalized = draft.model_dump(mode="json", exclude={"snapshot_sha256"})
        return cls.model_validate(
            {**normalized, "snapshot_sha256": semantic_sha256_v22(normalized)}
        )


class BaselineRestartProofV023(ProductModelV1):
    schema_version: Literal[
        "ecomsre.product.baseline-restart-proof.v023"
    ] = "ecomsre.product.baseline-restart-proof.v023"
    terminal: Literal["ECOMSRE_PRODUCT_V023_BASELINE_RESTART_PASS"]
    before: BaselineRestartSnapshotV023
    after: BaselineRestartSnapshotV023
    connector_verification_count: int = Field(ge=0, le=1)
    new_baseline_count: Literal[0]
    queue_healthy: Literal[True]
    proof_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_restart_persistence(self) -> "BaselineRestartProofV023":
        immutable_fields = (
            "environment_id",
            "environment_payload_sha256",
            "profile_binding_sha256",
            "profile_sha256",
            "active_baseline_id",
            "active_baseline_sha256",
            "baseline_count",
            "service_identity_sha256",
            "capability_sha256",
        )
        if any(getattr(self.before, name) != getattr(self.after, name) for name in immutable_fields):
            raise ValueError("Product v0.2.3 restart changed a frozen binding")
        if (
            self.after.observed_at <= self.before.observed_at
            or self.after.api_instance_id == self.before.api_instance_id
            or self.after.worker_instance_id == self.before.worker_instance_id
        ):
            raise ValueError("Product v0.2.3 API and Worker restart is not proven")
        if any(
            (self.after.pending_jobs, self.after.running_jobs, self.after.failed_jobs)
        ):
            raise ValueError("Product v0.2.3 restart queue is not healthy")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"proof_sha256"})
        )
        if self.proof_sha256 != expected:
            raise ValueError("baseline restart proof digest differs")
        return self

    @classmethod
    def build(
        cls,
        *,
        before: BaselineRestartSnapshotV023,
        after: BaselineRestartSnapshotV023,
        connector_verification_count: int = 0,
    ) -> "BaselineRestartProofV023":
        body = {
            "schema_version": "ecomsre.product.baseline-restart-proof.v023",
            "terminal": BASELINE_RESTART_PASS_V023,
            "before": before.model_dump(mode="json"),
            "after": after.model_dump(mode="json"),
            "connector_verification_count": connector_verification_count,
            "new_baseline_count": 0,
            "queue_healthy": True,
        }
        return cls.model_validate(
            {**body, "proof_sha256": semantic_sha256_v22(body)}
        )


__all__ = (
    "BASELINE_RESTART_PASS_V023",
    "BaselineRestartProofV023",
    "BaselineRestartSnapshotV023",
)
