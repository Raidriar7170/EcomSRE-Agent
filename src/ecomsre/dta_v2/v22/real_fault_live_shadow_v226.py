"""Read-only Current live-shadow adapters for the DTA v2.2.6 study."""

from __future__ import annotations

from typing import Literal

from pydantic import StrictBool, StrictInt, model_validator

from ecomsre.dta_v2.read_tools import ReadBackend
from ecomsre.dta_v2.telemetry_adapters import LocalSandboxReadBackend
from ecomsre.dta_v2.tool_contracts import (
    InspectResourceUsageRequest,
    ReadAuthorityMode,
)
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
    RealFaultArmStatusV226,
    RealFaultStudyArmV226,
)
from ecomsre.dta_v2.v22.real_fault_selection_v226 import (
    RealFaultSelectionProviderV226,
)


BackendIdentityV226 = Literal[
    "LocalSandboxReadBackend", "DETERMINISTIC_FAKE_PHYSICAL_BACKEND"
]


class RealFaultLiveShadowRunV226(DtaModelV22):
    schema_version: Literal["dta-v226-real-fault.live-shadow-run.v1"]
    backend: BackendIdentityV226
    case_kind: Literal["BASELINE", "AD_CPU_FAULT"]
    arm_run: RealFaultArmRunV226
    backend_identity_verified: Literal[True]
    resource_request_target_count: StrictInt
    physical_multi_target: StrictBool
    opaque_remap_complete: StrictBool
    live_read_only: Literal[True]
    agent_writes: Literal[0]
    action_proposals: Literal[0]
    runbook_executions: Literal[0]

    @model_validator(mode="after")
    def require_live_shadow(self) -> RealFaultLiveShadowRunV226:
        if self.arm_run.arm is not RealFaultStudyArmV226.CURRENT_RUNTIME_BUNDLE:
            raise ValueError("v2.2.6 live shadow is not the Current arm")
        if self.arm_run.case_id.startswith("fault-"):
            expected_case_kind = "AD_CPU_FAULT"
        elif self.arm_run.case_id.startswith("baseline-"):
            expected_case_kind = "BASELINE"
        else:
            raise ValueError("v2.2.6 live shadow case ID has no admitted state role")
        if self.case_kind != expected_case_kind:
            raise ValueError("v2.2.6 live shadow case kind differs from case ID")
        if self.resource_request_target_count not in {0, 2}:
            raise ValueError("v2.2.6 live shadow physical target count is invalid")
        if self.physical_multi_target != (self.resource_request_target_count == 2):
            raise ValueError("v2.2.6 live shadow physical request shape differs")
        if self.opaque_remap_complete and (
            not self.physical_multi_target or not self.arm_run.all_candidates_covered
        ):
            raise ValueError("v2.2.6 live shadow opaque remap claim differs")
        if self.arm_run.status is RealFaultArmStatusV226.VALID_TERMINAL and (
            not self.arm_run.bundle_dispatched
            or self.arm_run.resource_read_shape != "MULTI_TARGET"
            or not self.opaque_remap_complete
        ):
            raise ValueError("valid v2.2.6 live shadow lacks one complete bundle")
        return self


def _run_current_runtime_bundle_live_v226(
    *,
    capture: RealFaultOpaqueCaptureV1,
    baseline_capture: RealFaultOpaqueCaptureV1,
    alias_map: RealFaultAliasMapV1,
    live_backend: ReadBackend,
    model_id: str,
    provider: RealFaultSelectionProviderV226,
    backend_label: BackendIdentityV226,
) -> RealFaultLiveShadowRunV226:
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
    if len(resource_requests) > 1:
        raise ValueError("v2.2.6 live shadow issued multiple Resources requests")
    expected_physical = tuple(
        sorted(item.physical_service for item in alias_map.bindings)
    )
    if resource_requests and (
        resource_requests[0].services != expected_physical
        or len(resource_requests[0].services) != 2
    ):
        raise ValueError("v2.2.6 live shadow Resources targets differ")
    target_count = len(resource_requests[0].services) if resource_requests else 0
    opaque_remap_complete = (
        target_count == 2
        and arm_run.all_candidates_covered
        and arm_run.bundle_target_count == len(capture.candidate_aliases)
    )
    return RealFaultLiveShadowRunV226(
        schema_version="dta-v226-real-fault.live-shadow-run.v1",
        backend=backend_label,
        case_kind=(
            "AD_CPU_FAULT" if capture.case_id.startswith("fault-") else "BASELINE"
        ),
        arm_run=arm_run,
        backend_identity_verified=True,
        resource_request_target_count=target_count,
        physical_multi_target=target_count == 2,
        opaque_remap_complete=opaque_remap_complete,
        live_read_only=True,
        agent_writes=0,
        action_proposals=0,
        runbook_executions=0,
    )


def run_current_runtime_bundle_live_v226(
    *,
    capture: RealFaultOpaqueCaptureV1,
    baseline_capture: RealFaultOpaqueCaptureV1,
    alias_map: RealFaultAliasMapV1,
    live_backend: ReadBackend,
    model_id: str,
    provider: RealFaultSelectionProviderV226,
) -> RealFaultLiveShadowRunV226:
    """Run only with the lifecycle-issued owned LocalSandboxReadBackend."""

    if not isinstance(live_backend, LocalSandboxReadBackend) or (
        live_backend.authority.mode is not ReadAuthorityMode.OWNED_LOCAL
    ):
        raise TypeError("production live shadow requires owned LocalSandboxReadBackend")
    return _run_current_runtime_bundle_live_v226(
        capture=capture,
        baseline_capture=baseline_capture,
        alias_map=alias_map,
        live_backend=live_backend,
        model_id=model_id,
        provider=provider,
        backend_label="LocalSandboxReadBackend",
    )


def run_current_runtime_bundle_simulated_live_v226(
    *,
    capture: RealFaultOpaqueCaptureV1,
    baseline_capture: RealFaultOpaqueCaptureV1,
    alias_map: RealFaultAliasMapV1,
    live_backend: ReadBackend,
    model_id: str,
    provider: RealFaultSelectionProviderV226,
) -> RealFaultLiveShadowRunV226:
    """Run deterministic pre-live simulation without a production identity claim."""

    authority = getattr(live_backend, "authority", None)
    if authority is None or authority.mode is not ReadAuthorityMode.FAKE_REPLAY:
        raise TypeError("simulated live shadow requires an explicit fake authority")
    return _run_current_runtime_bundle_live_v226(
        capture=capture,
        baseline_capture=baseline_capture,
        alias_map=alias_map,
        live_backend=live_backend,
        model_id=model_id,
        provider=provider,
        backend_label="DETERMINISTIC_FAKE_PHYSICAL_BACKEND",
    )


__all__ = (
    "RealFaultLiveShadowRunV226",
    "run_current_runtime_bundle_live_v226",
    "run_current_runtime_bundle_simulated_live_v226",
)
