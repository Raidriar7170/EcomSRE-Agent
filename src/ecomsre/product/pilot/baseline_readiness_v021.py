"""Product v0.2.1 baseline-readiness profiles and bounded attempt contracts."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, model_validator

from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22
from ecomsre.product.baselines import BaselineBuildModeV1, BaselineBuildPolicyV1
from ecomsre.product.contracts import ProductModelV1


class HealthyTrafficProfileV021(ProductModelV1):
    schema_version: Literal["ecomsre.product.healthy-traffic-profile.v021"] = (
        "ecomsre.product.healthy-traffic-profile.v021"
    )
    request_seed: int = Field(ge=0)
    maximum_request_count: int = Field(ge=1, le=180)
    requests_per_second: float = Field(gt=0, le=2, allow_inf_nan=False)
    error_budget: int = Field(ge=0, le=20)


class PilotBaselineReadinessProfileV021(ProductModelV1):
    schema_version: Literal["ecomsre.product.baseline-readiness-profile.v021"] = (
        "ecomsre.product.baseline-readiness-profile.v021"
    )
    profile_id: str = Field(pattern=r"^[a-zA-Z0-9_.-]{1,120}$")
    candidate_services: tuple[str, ...] = Field(min_length=1, max_length=20)
    build_policy: BaselineBuildPolicyV1
    stabilization_seconds: int = Field(ge=0, le=600)
    healthy_traffic_profile: HealthyTrafficProfileV021
    baseline_accumulation_seconds: int = Field(ge=180, le=900)
    connector_query_bindings: dict[str, str] = Field(min_length=1, max_length=20)
    maximum_changed_attempts: Literal[2] = 2
    public_root: Literal[".local/product-v021/baseline-readiness"] = (
        ".local/product-v021/baseline-readiness"
    )
    private_root: Literal[".local/product-v021/private-baseline-readiness"] = (
        ".local/product-v021/private-baseline-readiness"
    )
    profile_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_bound_readiness_profile(self) -> "PilotBaselineReadinessProfileV021":
        if self.candidate_services != tuple(sorted(set(self.candidate_services))):
            raise ValueError("baseline readiness services are not canonical")
        if self.candidate_services != ("checkout",):
            raise ValueError("baseline readiness default service differs")
        policy = self.build_policy
        if (
            policy.mode is not BaselineBuildModeV1.DEMO_ONLY
            or policy.lookback_seconds != 180
            or policy.window_count != 5
            or policy.minimum_successful_windows < 4
            or policy.warmup_seconds != 180
        ):
            raise ValueError("baseline readiness build policy differs")
        if tuple(self.connector_query_bindings) != tuple(
            sorted(self.connector_query_bindings)
        ):
            raise ValueError("baseline connector query bindings are not canonical")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"profile_sha256"})
        )
        if self.profile_sha256 != expected:
            raise ValueError("baseline readiness profile digest differs")
        return self

    @classmethod
    def build(cls, **payload: Any) -> "PilotBaselineReadinessProfileV021":
        body = {
            "schema_version": "ecomsre.product.baseline-readiness-profile.v021",
            **payload,
        }
        draft = cls.model_construct(**body, profile_sha256="0" * 64)
        body["profile_sha256"] = semantic_sha256_v22(
            draft.model_dump(mode="json", exclude={"profile_sha256"})
        )
        return cls.model_validate(body)


__all__ = (
    "HealthyTrafficProfileV021",
    "PilotBaselineReadinessProfileV021",
)
