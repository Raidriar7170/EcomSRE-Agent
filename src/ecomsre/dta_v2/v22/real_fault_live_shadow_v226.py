"""Read-only Current live-shadow adapter for the DTA v2.2.6 study."""

from __future__ import annotations

from typing import Literal

from pydantic import model_validator

from ecomsre.dta_v2.read_tools import ReadBackend
from ecomsre.dta_v2.tool_contracts import InspectResourceUsageRequest
from ecomsre.dta_v2.v22.current_runtime_bundle_v226 import (
    run_current_runtime_bundle_v226,
)
from ecomsre.dta_v2.v22.read_contracts import DtaModelV22
from ecomsre.dta_v2.v22.real_fault_action_backend_v225 import (
    RealFaultActionReadBackendV225,
)
from ecomsre.dta_v2.v22.real_fault_bootstrap_v226 import real_fault_run_id_v226
from ecomsre.dta_v2.v22.real_fault_capture_v225 import (
    RealFaultAliasMapV1,
    RealFaultOpaqueCaptureV1,
)
from ecomsre.dta_v2.v22.real_fault_comparison_contracts_v226 import (
    RealFaultArmRunV226,
    RealFaultStudyArmV226,
)
from ecomsre.dta_v2.v22.real_fault_selection_v226 import (
    RealFaultSelectionProviderV226,
)


class RealFaultLiveShadowRunV226(DtaModelV22):
    schema_version: Literal["dta-v226-real-fault.live-shadow-run.v1"]
    backend: Literal["LocalSandboxReadBackend"]
    case_kind: Literal["BASELINE", "AD_CPU_FAULT"]
    arm_run: RealFaultArmRunV226
    resource_request_target_count: Literal[2]
    physical_multi_target: Literal[True]
    opaque_remap_complete: Literal[True]
    live_read_only: Literal[True]
    agent_writes: Literal[0]
    action_proposals: Literal[0]
    runbook_executions: Literal[0]

    @model_validator(mode="after")
    def require_live_shadow(self) -> RealFaultLiveShadowRunV226:
        if (
            self.arm_run.arm is not RealFaultStudyArmV226.CURRENT_RUNTIME_BUNDLE
            or not self.arm_run.bundle_dispatched
            or not self.arm_run.all_candidates_covered
            or self.arm_run.resource_read_shape != "MULTI_TARGET"
        ):
            raise ValueError("v2.2.6 live shadow is not one complete Current bundle")
        return self


def run_current_runtime_bundle_live_v226(
    *,
    capture: RealFaultOpaqueCaptureV1,
    baseline_capture: RealFaultOpaqueCaptureV1,
    alias_map: RealFaultAliasMapV1,
    live_backend: ReadBackend,
    model_id: str,
    provider: RealFaultSelectionProviderV226,
) -> RealFaultLiveShadowRunV226:
    """Execute one Current shadow against an already-admitted owned read backend."""

    if alias_map.map_name != capture.alias_map_name:
        raise ValueError("v2.2.6 live shadow alias map differs from capture")
    if tuple(item.alias for item in alias_map.bindings) != capture.candidate_aliases:
        raise ValueError("v2.2.6 live shadow aliases differ from candidates")
    adapter = RealFaultActionReadBackendV225.live(
        backend=live_backend,
        run_id=real_fault_run_id_v226(capture),
        source_window=capture.source_window,
        alias_map=alias_map,
    )
    arm_run = run_current_runtime_bundle_v226(
        capture=capture,
        baseline_capture=baseline_capture,
        model_id=model_id,
        provider=provider,
        _action_backend=adapter,
    )
    resource_requests = tuple(
        item
        for item in adapter.requests
        if isinstance(item, InspectResourceUsageRequest)
    )
    expected_physical = tuple(
        sorted(item.physical_service for item in alias_map.bindings)
    )
    if (
        len(resource_requests) != 1
        or resource_requests[0].services != expected_physical
        or len(resource_requests[0].services) != 2
    ):
        raise ValueError("v2.2.6 live shadow did not issue one physical two-target read")
    if not (
        arm_run.all_candidates_covered
        and arm_run.bundle_target_count == len(capture.candidate_aliases)
    ):
        raise ValueError("v2.2.6 live shadow did not remap both opaque candidates")
    return RealFaultLiveShadowRunV226(
        schema_version="dta-v226-real-fault.live-shadow-run.v1",
        backend="LocalSandboxReadBackend",
        case_kind=(
            "AD_CPU_FAULT" if capture.case_id.startswith("fault-") else "BASELINE"
        ),
        arm_run=arm_run,
        resource_request_target_count=2,
        physical_multi_target=True,
        opaque_remap_complete=True,
        live_read_only=True,
        agent_writes=0,
        action_proposals=0,
        runbook_executions=0,
    )


__all__ = (
    "RealFaultLiveShadowRunV226",
    "run_current_runtime_bundle_live_v226",
)
