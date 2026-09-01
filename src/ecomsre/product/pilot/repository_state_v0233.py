"""Repository phase vocabulary for Product v0.2.3.3."""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import ConfigDict, Field, model_validator

from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22
from ecomsre.product.contracts import ProductModelV1


_SHA256_PATTERN = r"^[0-9a-f]{64}$"


class RepositoryPhaseV0233(str, Enum):
    PREPARED = "PREPARED"
    TRAFFIC_PREFLIGHT_PASS = "TRAFFIC_PREFLIGHT_PASS"
    FORMAL_RUNNING = "FORMAL_RUNNING"
    FORMAL_BLOCKED = "FORMAL_BLOCKED"
    MEASURED_COMPLETE = "MEASURED_COMPLETE"


class ProductV0233RepositoryStateManifest(ProductModelV1):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["ecomsre.product.repository-state.v0233"] = (
        "ecomsre.product.repository-state.v0233"
    )
    goal_version: Literal[
        "ecomsre-product-v0233-fresh-formal-evidence-bound-nofault-v1"
    ] = "ecomsre-product-v0233-fresh-formal-evidence-bound-nofault-v1"
    phase: RepositoryPhaseV0233
    history_and_handoff_sha256: str = Field(pattern=_SHA256_PATTERN)
    source_selection_sha256: str = Field(pattern=_SHA256_PATTERN)
    clone_contract_sha256: str = Field(pattern=_SHA256_PATTERN)
    campaign_sha256: str = Field(pattern=_SHA256_PATTERN)
    contract_preflight_sha256: str = Field(pattern=_SHA256_PATTERN)
    traffic_preflight_sha256: str | None = Field(default=None, pattern=_SHA256_PATTERN)
    formal_contract_freeze_sha256: str | None = Field(
        default=None, pattern=_SHA256_PATTERN
    )
    pre_execution_review_sha256: str | None = Field(
        default=None, pattern=_SHA256_PATTERN
    )
    formal_result_sha256: str | None = Field(default=None, pattern=_SHA256_PATTERN)
    formal_blocker_sha256: str | None = Field(default=None, pattern=_SHA256_PATTERN)
    knowledge_handoff_sha256: str | None = Field(
        default=None, pattern=_SHA256_PATTERN
    )
    cleanup_proof_sha256: str | None = Field(default=None, pattern=_SHA256_PATTERN)
    formal_clone_count: int = Field(ge=0, le=1)
    formal_execution_count: int = Field(ge=0, le=1)
    new_incident_count: int = Field(ge=0, le=1)
    new_diagnosis_count: int = Field(ge=0, le=1)
    measured_result_count: int = Field(ge=0, le=1)
    action_authority: Literal["NONE"] = "NONE"
    manifest_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def require_phase_artifacts_counters_and_seal(
        self,
    ) -> ProductV0233RepositoryStateManifest:
        traffic = (
            self.traffic_preflight_sha256,
            self.formal_contract_freeze_sha256,
            self.pre_execution_review_sha256,
        )
        counters = (
            self.formal_clone_count,
            self.formal_execution_count,
            self.new_incident_count,
            self.new_diagnosis_count,
            self.measured_result_count,
        )
        valid = False
        if self.phase is RepositoryPhaseV0233.PREPARED:
            valid = (
                traffic == (None, None, None)
                and self.formal_result_sha256 is None
                and self.formal_blocker_sha256 is None
                and self.knowledge_handoff_sha256 is None
                and self.cleanup_proof_sha256 is None
                and counters == (0, 0, 0, 0, 0)
            )
        elif self.phase is RepositoryPhaseV0233.TRAFFIC_PREFLIGHT_PASS:
            valid = (
                all(value is not None for value in traffic)
                and self.formal_result_sha256 is None
                and self.formal_blocker_sha256 is None
                and self.knowledge_handoff_sha256 is None
                and self.cleanup_proof_sha256 is None
                and counters == (0, 0, 0, 0, 0)
            )
        elif self.phase is RepositoryPhaseV0233.FORMAL_RUNNING:
            valid = (
                all(value is not None for value in traffic)
                and self.formal_result_sha256 is None
                and self.formal_blocker_sha256 is None
                and self.knowledge_handoff_sha256 is None
                and self.cleanup_proof_sha256 is None
                and self.formal_clone_count == 1
                and self.formal_execution_count == 1
                and self.new_diagnosis_count <= self.new_incident_count
                and self.measured_result_count == 0
            )
        elif self.phase is RepositoryPhaseV0233.FORMAL_BLOCKED:
            valid = (
                all(value is not None for value in traffic)
                and self.formal_result_sha256 is None
                and self.formal_blocker_sha256 is not None
                and self.knowledge_handoff_sha256 is None
                and self.cleanup_proof_sha256 is not None
                and self.formal_clone_count == 1
                and self.new_diagnosis_count <= self.new_incident_count
                and self.measured_result_count == 0
            )
        elif self.phase is RepositoryPhaseV0233.MEASURED_COMPLETE:
            valid = (
                all(value is not None for value in traffic)
                and self.formal_result_sha256 is not None
                and self.formal_blocker_sha256 is None
                and self.knowledge_handoff_sha256 is not None
                and self.cleanup_proof_sha256 is not None
                and counters == (1, 1, 1, 1, 1)
            )
        if not valid:
            raise ValueError("Product v0.2.3.3 phase artifact/counter contract differs")
        body = self.model_dump(mode="json", exclude={"manifest_sha256"})
        if self.manifest_sha256 != semantic_sha256_v22(body):
            raise ValueError("Product v0.2.3.3 repository manifest digest differs")
        return self


__all__ = (
    "ProductV0233RepositoryStateManifest",
    "RepositoryPhaseV0233",
)
