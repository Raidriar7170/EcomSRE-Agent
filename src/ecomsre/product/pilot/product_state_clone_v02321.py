"""Fresh preflight Product-state clone contract for Product v0.2.3.2.1."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import ConfigDict, Field, model_validator

from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22
from ecomsre.product.contracts import ProductModelV1
from ecomsre.product.pilot.product_state_clone_v0232 import (
    ProductStateCloneV0232,
    ProductStateSourceV0232,
)


PREFLIGHT_STATE_CLONE_PASS_V02321 = (
    "ECOMSRE_PRODUCT_V02321_PREFLIGHT_STATE_CLONE_PASS"
)
_SHA256_PATTERN = r"^[0-9a-f]{64}$"


class PreflightStateCloneReportV02321(ProductModelV1):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["ecomsre.product.preflight-state-clone.v02321"] = (
        "ecomsre.product.preflight-state-clone.v02321"
    )
    terminal: Literal[
        "ECOMSRE_PRODUCT_V02321_PREFLIGHT_STATE_CLONE_PASS"
    ] = "ECOMSRE_PRODUCT_V02321_PREFLIGHT_STATE_CLONE_PASS"
    role: Literal["PREFLIGHT"] = "PREFLIGHT"
    source_repository_binding: dict[str, Any]
    predecessor_private_acceptance: dict[str, Any]
    source_state: ProductStateSourceV0232
    clone: ProductStateCloneV0232
    destination_state: ProductStateSourceV0232
    destination_locator: str = Field(
        pattern=r"^\.local/product-v02321/product-state/preflight-[0-9a-f]{24}/product$"
    )
    source_incident_count: Literal[1]
    source_diagnosis_count: Literal[1]
    fault_family_count: Literal[0]
    knowledge_artifact_count: Literal[0]
    report_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def require_exact_preflight_clone(self) -> "PreflightStateCloneReportV02321":
        if (
            self.clone.destination_locator != self.destination_locator
            or self.destination_locator
            != (
                ".local/product-v02321/product-state/"
                f"preflight-{self.source_state.source_sha256[:24]}/product"
            )
            or self.destination_state.source_locator != self.destination_locator
            or self.clone.source_locator != self.source_state.source_locator
            or self.clone.source_database_file_sha256_before
            != self.source_state.source_database_file_sha256
            or self.clone.source_database_file_sha256_after
            != self.source_state.source_database_file_sha256
            or self.clone.source_database_logical_sha256
            != self.source_state.source_database_logical_sha256
            or self.clone.source_object_inventory_sha256
            != self.source_state.source_object_inventory_sha256
            or self.clone.source_runtime_file_inventory_sha256
            != self.source_state.source_runtime_file_inventory_sha256
            or self.clone.source_counts != self.source_state.source_counts
            or self.clone.source_environment_id
            != self.source_state.source_environment_id
            or self.clone.source_active_baseline_id
            != self.source_state.source_active_baseline_id
            or self.clone.source_active_baseline_sha256
            != self.source_state.source_active_baseline_sha256
            or self.clone.source_profile_sha256
            != self.source_state.source_profile_sha256
            or self.clone.destination_database_logical_sha256
            != self.destination_state.source_database_logical_sha256
            or self.clone.destination_object_inventory_sha256
            != self.destination_state.source_object_inventory_sha256
            or self.clone.destination_runtime_file_inventory_sha256
            != self.destination_state.source_runtime_file_inventory_sha256
            or self.clone.destination_counts != self.destination_state.source_counts
            or self.clone.destination_environment_id
            != self.destination_state.source_environment_id
            or self.clone.destination_active_baseline_id
            != self.destination_state.source_active_baseline_id
            or self.clone.destination_active_baseline_sha256
            != self.destination_state.source_active_baseline_sha256
            or self.clone.destination_profile_sha256
            != self.destination_state.source_profile_sha256
            or self.source_incident_count
            != self.source_state.source_counts.incident_count
            or self.source_diagnosis_count
            != self.source_state.source_counts.diagnosis_count
            or self.fault_family_count
            != self.source_state.source_counts.fault_family_count
            or self.knowledge_artifact_count
            != self.source_state.source_counts.knowledge_artifact_count
        ):
            raise ValueError("Product v0.2.3.2.1 preflight clone binding differs")
        body = self.model_dump(mode="json", exclude={"report_sha256"})
        if self.report_sha256 != semantic_sha256_v22(body):
            raise ValueError("Product v0.2.3.2.1 preflight clone digest differs")
        return self

    @classmethod
    def build(cls, **payload: Any) -> "PreflightStateCloneReportV02321":
        source = ProductStateSourceV0232.model_validate(payload["source_state"])
        clone = ProductStateCloneV0232.model_validate(payload["clone"])
        destination = ProductStateSourceV0232.model_validate(
            payload["destination_state"]
        )
        body = {
            "schema_version": "ecomsre.product.preflight-state-clone.v02321",
            "terminal": PREFLIGHT_STATE_CLONE_PASS_V02321,
            "role": "PREFLIGHT",
            **payload,
            "source_state": source.model_dump(mode="json"),
            "clone": clone.model_dump(mode="json"),
            "destination_state": destination.model_dump(mode="json"),
        }
        return cls.model_validate(
            {**body, "report_sha256": semantic_sha256_v22(body)}
        )


__all__ = (
    "PREFLIGHT_STATE_CLONE_PASS_V02321",
    "PreflightStateCloneReportV02321",
)
