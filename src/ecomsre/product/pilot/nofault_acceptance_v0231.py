"""v0.2.3.1 bindings around the frozen v0.2.3 No-Fault scorer."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, model_validator

from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22
from ecomsre.product.contracts import ProductModelV1
from ecomsre.product.pilot.nofault_acceptance_v023 import (
    NOFAULT_CAPABILITY_LIMITED_V023,
    NOFAULT_FULLY_SUPPORTED_V023,
    NOFAULT_NOT_SUPPORTED_V023,
    NoFaultAcceptanceResultV023,
)


NOFAULT_FULLY_SUPPORTED_V0231 = "ECOMSRE_PRODUCT_V0231_NOFAULT_FULLY_SUPPORTED"
NOFAULT_CAPABILITY_LIMITED_V0231 = (
    "ECOMSRE_PRODUCT_V0231_NOFAULT_CAPABILITY_LIMITED"
)
NOFAULT_NOT_SUPPORTED_V0231 = "ECOMSRE_PRODUCT_V0231_NOFAULT_NOT_SUPPORTED"
NOFAULT_ACCEPTANCE_COMPLETE_V0231 = (
    "ECOMSRE_PRODUCT_V0231_NOFAULT_ACCEPTANCE_COMPLETE"
)

_TERMINAL_MAP_V0231 = {
    NOFAULT_FULLY_SUPPORTED_V023: NOFAULT_FULLY_SUPPORTED_V0231,
    NOFAULT_CAPABILITY_LIMITED_V023: NOFAULT_CAPABILITY_LIMITED_V0231,
    NOFAULT_NOT_SUPPORTED_V023: NOFAULT_NOT_SUPPORTED_V0231,
}


class NoFaultProfileBindingV0231(ProductModelV1):
    schema_version: Literal["ecomsre.product.nofault-profile-binding.v0231"] = (
        "ecomsre.product.nofault-profile-binding.v0231"
    )
    source_profile_locator: Literal["config/product-v023/nofault/profile.json"]
    source_profile_file_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_profile_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    runtime_continuity_descriptor_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    active_baseline_id: str = Field(pattern=r"^base-[0-9a-f]{24}$")
    active_baseline_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    candidate_service: Literal["checkout"]
    queue_flag: Literal[0]
    request_count: Literal[30]
    requests_per_second: float = Field(gt=0, allow_inf_nan=False)
    maximum_error_fraction: float = Field(ge=0, allow_inf_nan=False)
    seed: Literal[23082901]
    fault_label: Literal["none"]
    binding_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_self_sealed_binding(self) -> "NoFaultProfileBindingV0231":
        if self.requests_per_second != 1.0 or self.maximum_error_fraction != 0.01:
            raise ValueError("No-Fault profile binding traffic values differ")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"binding_sha256"})
        )
        if self.binding_sha256 != expected:
            raise ValueError("No-Fault profile binding digest differs")
        return self

    @classmethod
    def build(cls, **payload: Any) -> "NoFaultProfileBindingV0231":
        body = {
            "schema_version": "ecomsre.product.nofault-profile-binding.v0231",
            **payload,
        }
        draft = cls.model_construct(**body, binding_sha256="0" * 64)
        normalized = draft.model_dump(mode="json", exclude={"binding_sha256"})
        return cls.model_validate(
            {**normalized, "binding_sha256": semantic_sha256_v22(normalized)}
        )


class NoFaultCampaignV0231(ProductModelV1):
    schema_version: Literal["ecomsre.product.continuity-campaign.v0231"] = (
        "ecomsre.product.continuity-campaign.v0231"
    )
    goal_version: Literal[
        "ecomsre-product-v0231-runtime-authority-continuity-nofault-v1"
    ]
    predecessor_pr: Literal[80]
    predecessor_head: str = Field(pattern=r"^[0-9a-f]{40}$")
    maximum_live_sessions: Literal[2]
    profile_binding_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    runtime_continuity_descriptor_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    active_baseline_id: str = Field(pattern=r"^base-[0-9a-f]{24}$")
    active_baseline_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    accepted_incident_limit: Literal[1]
    diagnosis_limit: Literal[1]
    fault_attempt_limit: Literal[0]
    knowledge_loop_campaign_limit: Literal[0]
    action_authority: Literal["NONE"]
    campaign_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_self_sealed_campaign(self) -> "NoFaultCampaignV0231":
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"campaign_sha256"})
        )
        if self.campaign_sha256 != expected:
            raise ValueError("No-Fault campaign digest differs")
        return self


class NoFaultAcceptanceResultV0231(ProductModelV1):
    schema_version: Literal["ecomsre.product.nofault-acceptance-result.v0231"] = (
        "ecomsre.product.nofault-acceptance-result.v0231"
    )
    terminal: Literal[
        "ECOMSRE_PRODUCT_V0231_NOFAULT_FULLY_SUPPORTED",
        "ECOMSRE_PRODUCT_V0231_NOFAULT_CAPABILITY_LIMITED",
        "ECOMSRE_PRODUCT_V0231_NOFAULT_NOT_SUPPORTED",
    ]
    acceptance_terminal: Literal[
        "ECOMSRE_PRODUCT_V0231_NOFAULT_ACCEPTANCE_COMPLETE"
    ]
    runtime_terminal: Literal[
        "ECOMSRE_PRODUCT_V0231_RUNTIME_AUTHORITY_CONTINUITY_PASS"
    ]
    restart_terminal: Literal[
        "ECOMSRE_PRODUCT_V0231_BASELINE_RESTART_PASS"
    ]
    runtime_continuity_descriptor_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    profile_binding_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    runtime_authority_proof_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    baseline_restart_proof_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    session_start_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    wrapped_v023_result: NoFaultAcceptanceResultV023
    result_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_mapped_self_sealed_result(self) -> "NoFaultAcceptanceResultV0231":
        if self.terminal != _TERMINAL_MAP_V0231[self.wrapped_v023_result.terminal.value]:
            raise ValueError("v0.2.3.1 measured terminal mapping differs")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"result_sha256"})
        )
        if self.result_sha256 != expected:
            raise ValueError("v0.2.3.1 No-Fault result digest differs")
        return self

    @classmethod
    def build(
        cls,
        *,
        wrapped_v023_result: NoFaultAcceptanceResultV023,
        runtime_continuity_descriptor_sha256: str,
        profile_binding_sha256: str,
        runtime_authority_proof_sha256: str,
        baseline_restart_proof_sha256: str,
        session_start_sha256: str,
    ) -> "NoFaultAcceptanceResultV0231":
        body = {
            "schema_version": "ecomsre.product.nofault-acceptance-result.v0231",
            "terminal": _TERMINAL_MAP_V0231[wrapped_v023_result.terminal.value],
            "acceptance_terminal": NOFAULT_ACCEPTANCE_COMPLETE_V0231,
            "runtime_terminal": (
                "ECOMSRE_PRODUCT_V0231_RUNTIME_AUTHORITY_CONTINUITY_PASS"
            ),
            "restart_terminal": "ECOMSRE_PRODUCT_V0231_BASELINE_RESTART_PASS",
            "runtime_continuity_descriptor_sha256": (
                runtime_continuity_descriptor_sha256
            ),
            "profile_binding_sha256": profile_binding_sha256,
            "runtime_authority_proof_sha256": runtime_authority_proof_sha256,
            "baseline_restart_proof_sha256": baseline_restart_proof_sha256,
            "session_start_sha256": session_start_sha256,
            "wrapped_v023_result": wrapped_v023_result.model_dump(mode="json"),
        }
        return cls.model_validate(
            {**body, "result_sha256": semantic_sha256_v22(body)}
        )


__all__ = (
    "NOFAULT_ACCEPTANCE_COMPLETE_V0231",
    "NOFAULT_CAPABILITY_LIMITED_V0231",
    "NOFAULT_FULLY_SUPPORTED_V0231",
    "NOFAULT_NOT_SUPPORTED_V0231",
    "NoFaultAcceptanceResultV0231",
    "NoFaultCampaignV0231",
    "NoFaultProfileBindingV0231",
)
