"""Offline typed Tool request planning for the Product v0.2.3.2.1 harness."""

from __future__ import annotations

from typing import Literal

from pydantic import ConfigDict, Field, model_validator

from ecomsre.dta_v2.tool_contracts import (
    InspectServiceRuntimeRequest,
    ToolName,
    build_inspect_service_runtime_request,
)
from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22
from ecomsre.product.contracts import ProductModelV1
from ecomsre.product.pilot.tool_request_ids_v02321 import (
    CanonicalToolRunIdV02321,
)


_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_RUN_ID_PATTERN = r"^[0-9a-f]{32}$"


def _serialized_request_sha256(request: InspectServiceRuntimeRequest) -> str:
    return semantic_sha256_v22(request.model_dump(mode="json"))


class TrafficHarnessTypedRequestEntryV02321(ProductModelV1):
    model_config = ConfigDict(extra="forbid", frozen=True)

    tool_name: Literal["inspect_service_runtime"]
    run_id: str = Field(pattern=_RUN_ID_PATTERN)
    request_model: Literal["InspectServiceRuntimeRequest"]
    request_sha256: str = Field(pattern=_SHA256_PATTERN)
    target_services: tuple[str, ...] = Field(min_length=1, max_length=10)
    serialized_request_sha256: str = Field(pattern=_SHA256_PATTERN)


class TrafficHarnessTypedRequestPlanV02321(ProductModelV1):
    """Self-bound summary of every typed request the live harness may issue."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["ecomsre.product.traffic-typed-request-plan.v02321"] = (
        "ecomsre.product.traffic-typed-request-plan.v02321"
    )
    campaign_sha256: str = Field(pattern=_SHA256_PATTERN)
    role: Literal["PREFLIGHT", "FORMAL"]
    state_clone_sha256: str = Field(pattern=_SHA256_PATTERN)
    attempt_ordinal: int = Field(ge=1)
    request_entries: tuple[TrafficHarnessTypedRequestEntryV02321, ...] = Field(
        min_length=1
    )
    plan_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def require_complete_bound_plan(self) -> "TrafficHarnessTypedRequestPlanV02321":
        if len(self.request_entries) != 1:
            raise ValueError("typed request plan does not match the live request surface")
        entry = self.request_entries[0]
        identifier = CanonicalToolRunIdV02321.build(
            namespace="ECOMSRE_PRODUCT_V02321",
            role=self.role,
            campaign_sha256=self.campaign_sha256,
            state_clone_sha256=self.state_clone_sha256,
            attempt_ordinal=self.attempt_ordinal,
            tool_name=entry.tool_name,
            target_services=entry.target_services,
        )
        request = build_inspect_service_runtime_request(
            run_id=identifier.run_id,
            services=entry.target_services,
            max_results=len(entry.target_services),
        )
        if (
            entry.run_id != identifier.run_id
            or entry.request_sha256 != request.normalized_request_sha256
            or entry.serialized_request_sha256
            != _serialized_request_sha256(request)
        ):
            raise ValueError("typed request entry differs from constructed request")
        body = self.model_dump(mode="json", exclude={"plan_sha256"})
        if self.plan_sha256 != semantic_sha256_v22(body):
            raise ValueError("typed request plan digest differs")
        return self


def build_traffic_harness_typed_request_plan_v02321(
    *,
    campaign_sha256: str,
    role: str,
    state_clone_sha256: str,
    attempt_ordinal: int,
) -> TrafficHarnessTypedRequestPlanV02321:
    identifier = CanonicalToolRunIdV02321.build(
        namespace="ECOMSRE_PRODUCT_V02321",
        role=role,
        campaign_sha256=campaign_sha256,
        state_clone_sha256=state_clone_sha256,
        attempt_ordinal=attempt_ordinal,
        tool_name=ToolName.INSPECT_SERVICE_RUNTIME.value,
        target_services=("checkout",),
    )
    request = build_inspect_service_runtime_request(
        run_id=identifier.run_id,
        services=identifier.target_services,
        max_results=1,
    )
    entry = TrafficHarnessTypedRequestEntryV02321(
        tool_name=ToolName.INSPECT_SERVICE_RUNTIME.value,
        run_id=identifier.run_id,
        request_model="InspectServiceRuntimeRequest",
        request_sha256=request.normalized_request_sha256,
        target_services=identifier.target_services,
        serialized_request_sha256=_serialized_request_sha256(request),
    )
    body: dict[str, object] = {
        "schema_version": "ecomsre.product.traffic-typed-request-plan.v02321",
        "campaign_sha256": campaign_sha256,
        "role": role,
        "state_clone_sha256": state_clone_sha256,
        "attempt_ordinal": attempt_ordinal,
        "request_entries": [entry.model_dump(mode="json")],
    }
    return TrafficHarnessTypedRequestPlanV02321.model_validate(
        {**body, "plan_sha256": semantic_sha256_v22(body)}
    )


def materialize_planned_request_v02321(
    plan: TrafficHarnessTypedRequestPlanV02321,
    *,
    tool_name: str,
) -> InspectServiceRuntimeRequest:
    entries = tuple(
        entry for entry in plan.request_entries if entry.tool_name == tool_name
    )
    if len(entries) != 1:
        raise ValueError("Tool request is not present in frozen typed request plan")
    entry = entries[0]
    request = build_inspect_service_runtime_request(
        run_id=entry.run_id,
        services=entry.target_services,
        max_results=len(entry.target_services),
    )
    require_live_request_in_plan_v02321(plan, request)
    return request


def require_live_request_in_plan_v02321(
    plan: TrafficHarnessTypedRequestPlanV02321,
    request: InspectServiceRuntimeRequest,
) -> TrafficHarnessTypedRequestEntryV02321:
    entries = tuple(
        entry
        for entry in plan.request_entries
        if entry.tool_name == request.tool.value
        and entry.run_id == request.run_id
        and entry.request_model == type(request).__name__
        and entry.target_services == request.services
        and entry.request_sha256 == request.normalized_request_sha256
        and entry.serialized_request_sha256 == _serialized_request_sha256(request)
    )
    if len(entries) != 1:
        raise ValueError("live request is not present in frozen typed request plan")
    return entries[0]


__all__ = (
    "TrafficHarnessTypedRequestEntryV02321",
    "TrafficHarnessTypedRequestPlanV02321",
    "build_traffic_harness_typed_request_plan_v02321",
    "materialize_planned_request_v02321",
    "require_live_request_in_plan_v02321",
)
