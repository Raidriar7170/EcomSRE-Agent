"""Canonical semantic Tool request identifiers for Product v0.2.3.2.1."""

from __future__ import annotations

from typing import Literal

from pydantic import ConfigDict, Field, field_validator, model_validator

from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22
from ecomsre.product.contracts import ProductModelV1


_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_RUN_ID_PATTERN = r"^[0-9a-f]{32}$"
_TOOL_NAMES = Literal[
    "query_metrics",
    "search_logs",
    "query_trace_neighborhood",
    "inspect_service_runtime",
    "inspect_resource_usage",
]


def _semantic_inputs(
    *,
    namespace: str,
    role: str,
    campaign_sha256: str,
    state_clone_sha256: str,
    attempt_ordinal: int,
    tool_name: str,
    target_services: tuple[str, ...],
) -> dict[str, object]:
    return {
        "namespace": namespace,
        "role": role,
        "campaign_sha256": campaign_sha256,
        "state_clone_sha256": state_clone_sha256,
        "attempt_ordinal": attempt_ordinal,
        "tool_name": tool_name,
        "target_services": list(target_services),
    }


class CanonicalToolRunIdV02321(ProductModelV1):
    """A self-bound canonical DTA run ID and all semantic inputs behind it."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["ecomsre.product.canonical-tool-run-id.v02321"] = (
        "ecomsre.product.canonical-tool-run-id.v02321"
    )
    namespace: Literal["ECOMSRE_PRODUCT_V02321"]
    role: Literal["PREFLIGHT", "FORMAL"]
    campaign_sha256: str = Field(pattern=_SHA256_PATTERN)
    state_clone_sha256: str = Field(pattern=_SHA256_PATTERN)
    attempt_ordinal: int = Field(ge=1)
    tool_name: _TOOL_NAMES
    target_services: tuple[str, ...] = Field(min_length=1, max_length=10)
    semantic_inputs_sha256: str = Field(pattern=_SHA256_PATTERN)
    run_id: str = Field(pattern=_RUN_ID_PATTERN)
    binding_sha256: str = Field(pattern=_SHA256_PATTERN)

    @field_validator("target_services")
    @classmethod
    def require_canonical_services(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if (
            any(not item or item != item.strip() for item in value)
            or len(value) != len(set(value))
            or value != tuple(sorted(value))
        ):
            raise ValueError("target services are not canonical")
        return value

    @model_validator(mode="after")
    def require_semantic_binding(self) -> "CanonicalToolRunIdV02321":
        inputs = _semantic_inputs(
            namespace=self.namespace,
            role=self.role,
            campaign_sha256=self.campaign_sha256,
            state_clone_sha256=self.state_clone_sha256,
            attempt_ordinal=self.attempt_ordinal,
            tool_name=self.tool_name,
            target_services=self.target_services,
        )
        expected_inputs_sha256 = semantic_sha256_v22(inputs)
        if self.semantic_inputs_sha256 != expected_inputs_sha256:
            raise ValueError("semantic input digest differs")
        if self.run_id != expected_inputs_sha256[:32]:
            raise ValueError("canonical Tool run ID differs")
        body = self.model_dump(mode="json", exclude={"binding_sha256"})
        if self.binding_sha256 != semantic_sha256_v22(body):
            raise ValueError("canonical Tool run ID binding differs")
        return self

    @classmethod
    def build(
        cls,
        *,
        namespace: str,
        role: str,
        campaign_sha256: str,
        state_clone_sha256: str,
        attempt_ordinal: int,
        tool_name: str,
        target_services: tuple[str, ...],
    ) -> "CanonicalToolRunIdV02321":
        canonical_services = tuple(sorted(item.strip() for item in target_services))
        inputs = _semantic_inputs(
            namespace=namespace,
            role=role,
            campaign_sha256=campaign_sha256,
            state_clone_sha256=state_clone_sha256,
            attempt_ordinal=attempt_ordinal,
            tool_name=tool_name,
            target_services=canonical_services,
        )
        semantic_inputs_sha256 = semantic_sha256_v22(inputs)
        body: dict[str, object] = {
            "schema_version": "ecomsre.product.canonical-tool-run-id.v02321",
            **inputs,
            "semantic_inputs_sha256": semantic_inputs_sha256,
            "run_id": semantic_inputs_sha256[:32],
        }
        return cls.model_validate(
            {**body, "binding_sha256": semantic_sha256_v22(body)}
        )


def derive_tool_run_id_v02321(
    *,
    namespace: str,
    role: str,
    campaign_sha256: str,
    state_clone_sha256: str,
    attempt_ordinal: int,
    tool_name: str,
    target_services: tuple[str, ...],
) -> str:
    """Derive the stable 32-character DTA run ID for one semantic request."""

    return CanonicalToolRunIdV02321.build(
        namespace=namespace,
        role=role,
        campaign_sha256=campaign_sha256,
        state_clone_sha256=state_clone_sha256,
        attempt_ordinal=attempt_ordinal,
        tool_name=tool_name,
        target_services=target_services,
    ).run_id


__all__ = ("CanonicalToolRunIdV02321", "derive_tool_run_id_v02321")
