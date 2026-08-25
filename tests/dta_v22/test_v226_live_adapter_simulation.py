from __future__ import annotations

from pathlib import Path

import pytest

from ecomsre.dta_v2.read_tools import BackendResult
from ecomsre.dta_v2.tool_contracts import (
    EndpointState,
    HealthState,
    InspectResourceUsageRequest,
    InspectServiceRuntimeRequest,
    METRIC_UNIT_BY_KIND,
    MetricKind,
    MetricRecord,
    ResourceSample,
    ResourceUsageRecord,
    RuntimeRecord,
    RuntimeState,
    build_fake_read_authority,
)
from ecomsre.dta_v2.v22.real_fault_capture_v225 import (
    RealFaultOpaqueCaptureV1,
    build_alias_maps_v225,
)
from ecomsre.dta_v2.v22.real_fault_live_shadow_v226 import (
    run_current_runtime_bundle_live_v226,
    run_current_runtime_bundle_simulated_live_v226,
)
from ecomsre.dta_v2.v22.real_fault_selection_v226 import (
    RealFaultSelectionDecisionV226,
    RealFaultSelectionOutcomeV226,
)


ROOT = Path(__file__).resolve().parents[2]


def _capture(case_id: str) -> RealFaultOpaqueCaptureV1:
    return RealFaultOpaqueCaptureV1.model_validate_json(
        (ROOT / f"config/dta-v225-real-fault/captures/{case_id}.json").read_bytes()
    )


class _UnequalPhysicalBackend:
    def __init__(self, *, ad_cpu: float) -> None:
        self.authority = build_fake_read_authority()
        self.ad_cpu = ad_cpu
        self.requests: list[object] = []

    def execute(self, request):
        self.requests.append(request)
        if isinstance(request, InspectServiceRuntimeRequest):
            return BackendResult(
                records=tuple(
                    RuntimeRecord(
                        logical_service=service,
                        owned_container_present=True,
                        state=RuntimeState.RUNNING,
                        health=HealthState.HEALTHY,
                        restart_count=0,
                        exit_code=0,
                        endpoint_probe_performed=True,
                        endpoint_state=EndpointState.READY,
                    )
                    for service in request.services
                )
            )
        if isinstance(request, InspectResourceUsageRequest):
            return BackendResult(
                records=tuple(
                    ResourceUsageRecord(
                        logical_service=service,
                        sampling_window_seconds=10,
                        samples=tuple(
                            ResourceSample(
                                offset_ms=offset,
                                cpu_percent=(
                                    self.ad_cpu if service == "ad" else 3.0
                                ),
                                memory_bytes=(
                                    101_000_000 if service == "ad" else 113_000_000
                                )
                                + offset,
                            )
                            for offset in (0, 2_500, 5_000, 7_500, 10_000)
                        ),
                        memory_slope_bytes_per_second=0.0,
                    )
                    for service in request.services
                )
            )
        records = []
        for kind in request.metric_kinds:
            if kind is MetricKind.ERROR_RATE:
                records.append(
                    MetricRecord(
                        service=request.service,
                        metric_kind=kind,
                        value=0.0,
                        unit=METRIC_UNIT_BY_KIND[kind],
                        sample_count=0,
                    )
                )
            else:
                value = (
                    3.2
                    if kind is MetricKind.LATENCY_P95_MS
                    and request.service == "ad"
                    else 51.25
                    if kind is MetricKind.LATENCY_P95_MS
                    else 79.6
                    if request.service == "ad"
                    else 127.6
                )
                records.append(
                    MetricRecord(
                        service=request.service,
                        metric_kind=kind,
                        value=value,
                        unit=METRIC_UNIT_BY_KIND[kind],
                        sample_count=5,
                    )
                )
        return BackendResult(records=tuple(records))


class _TerminalProvider:
    def __init__(self) -> None:
        self.requests = []

    def complete_selection(self, *, request, run_id, max_protocol_repairs=2):
        del run_id, max_protocol_repairs
        self.requests.append(request)
        selected = next(
            (
                item
                for item in request.terminals
                if item.terminal_kind == "CPU_SATURATION"
            ),
            None,
        ) or next(
            item for item in request.terminals if item.terminal_kind == "NO_INCIDENT"
        )
        return RealFaultSelectionOutcomeV226(
            decision=RealFaultSelectionDecisionV226(
                selection=selected.alias,
                focus="NONE",
            ),
            first_pass_protocol_success=True,
            post_repair_protocol_success=True,
            protocol_repairs=0,
            provider_calls=1,
            transport_retry_count=0,
            input_tokens=20,
            output_tokens=4,
            total_tokens=24,
            latency_ms=1.0,
        )


def test_v226_live_adapter_handles_unequal_baseline_and_fault_physical_reads() -> None:
    baseline = _capture("baseline-map-a")
    fault = _capture("fault-map-a")
    map_a, _map_b = build_alias_maps_v225(
        fault_service="ad",
        comparator_service="recommendation",
        aliases=baseline.candidate_aliases,
    )
    baseline_backend = _UnequalPhysicalBackend(ad_cpu=3.0)
    fault_backend = _UnequalPhysicalBackend(ad_cpu=96.0)
    baseline_provider = _TerminalProvider()
    fault_provider = _TerminalProvider()

    baseline_shadow = run_current_runtime_bundle_simulated_live_v226(
        capture=baseline,
        baseline_capture=baseline,
        alias_map=map_a,
        live_backend=baseline_backend,
        model_id="deterministic-v226",
        provider=baseline_provider,
    )
    fault_shadow = run_current_runtime_bundle_simulated_live_v226(
        capture=fault,
        baseline_capture=baseline,
        alias_map=map_a,
        live_backend=fault_backend,
        model_id="deterministic-v226",
        provider=fault_provider,
    )

    assert baseline_shadow.arm_run.status.value == "VALID_TERMINAL"
    assert baseline_shadow.arm_run.prediction.terminal == "NO_INCIDENT"
    assert fault_shadow.arm_run.status.value == "VALID_TERMINAL"
    assert fault_shadow.arm_run.prediction.terminal == "DIAGNOSED"
    assert fault_shadow.arm_run.prediction.root_service_alias == map_a.alias_for("ad")
    assert fault_shadow.arm_run.prediction.mechanism == "CPU_SATURATION"
    for shadow in (baseline_shadow, fault_shadow):
        assert shadow.backend == "DETERMINISTIC_FAKE_PHYSICAL_BACKEND"
        assert shadow.arm_run.trace.last_completed_stage.value == "COMPLETE"
        assert shadow.physical_multi_target is True
        assert shadow.resource_request_target_count == 2
        assert shadow.opaque_remap_complete is True
        assert shadow.agent_writes == 0
        assert shadow.action_proposals == 0
        assert shadow.runbook_executions == 0
    for backend in (baseline_backend, fault_backend):
        resource = next(
            item
            for item in backend.requests
            if isinstance(item, InspectResourceUsageRequest)
        )
        assert resource.services == ("ad", "recommendation")
    for provider in (baseline_provider, fault_provider):
        rendered = provider.requests[0].model_dump_json().casefold()
        assert '"ad"' not in rendered
        assert "recommendation" not in rendered


def test_v226_production_live_entrypoint_rejects_unverified_backend_identity() -> None:
    baseline = _capture("baseline-map-a")
    map_a, _map_b = build_alias_maps_v225(
        fault_service="ad",
        comparator_service="recommendation",
        aliases=baseline.candidate_aliases,
    )

    with pytest.raises(TypeError, match="owned LocalSandboxReadBackend"):
        run_current_runtime_bundle_live_v226(
            capture=baseline,
            baseline_capture=baseline,
            alias_map=map_a,
            live_backend=_UnequalPhysicalBackend(ad_cpu=3.0),
            model_id="deterministic-v226",
            provider=_TerminalProvider(),
        )


class _FailingPhysicalBackend(_UnequalPhysicalBackend):
    def execute(self, request):
        self.requests.append(request)
        raise RuntimeError("injected simulated backend failure")


def test_v226_simulated_live_wrapper_preserves_typed_pre_resource_failure() -> None:
    baseline = _capture("baseline-map-a")
    map_a, _map_b = build_alias_maps_v225(
        fault_service="ad",
        comparator_service="recommendation",
        aliases=baseline.candidate_aliases,
    )

    shadow = run_current_runtime_bundle_simulated_live_v226(
        capture=baseline,
        baseline_capture=baseline,
        alias_map=map_a,
        live_backend=_FailingPhysicalBackend(ad_cpu=3.0),
        model_id="deterministic-v226",
        provider=_TerminalProvider(),
    )

    assert shadow.arm_run.status.value == "RUNNER_FAILED"
    assert shadow.arm_run.trace.failure_stage.value == "BOOTSTRAP_DISPATCH"
    assert shadow.resource_request_target_count == 0
    assert shadow.physical_multi_target is False
    assert shadow.opaque_remap_complete is False
