from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import cast

from ecomsre.dta_v2.agent_contracts import ProviderUsage
from ecomsre.dta_v2.read_tools import BackendResult
from ecomsre.dta_v2.tool_contracts import (
    EndpointState,
    HealthState,
    InspectResourceUsageRequest,
    InspectServiceRuntimeRequest,
    METRIC_UNIT_BY_KIND,
    MetricKind,
    MetricRecord,
    SearchLogsRequest,
    TraceNeighborhoodRequest,
    ResourceUsageRecord,
    ResourceSample,
    RuntimeRecord,
    RuntimeState,
    build_fake_read_authority,
    build_inspect_resource_usage_request,
)
from ecomsre.dta_v2.v21.agent_provider import ProviderTurnV21
from ecomsre.dta_v2.v21.contracts import (
    DtaDiagnosisV21,
    EvidenceSourceV21,
    FaultDomainV21,
    FaultMechanismV21,
    TerminalV21,
)
from ecomsre.dta_v2.v22.opaque_identity_v225 import (
    generate_opaque_identity_plan_v225,
)
from ecomsre.dta_v2.v22.read_contracts import (
    MetricFactV22,
    MetricKindV22,
    MetricSupportStatusV22,
    MetricUnitV22,
    ResourceSampleV22,
    ResourceUsageRecordV22,
    RuntimeRecordV22,
    RuntimeStateV22,
)
from ecomsre.dta_v2.v22.real_fault_bundle_arm_v225 import (
    run_current_runtime_bundle_live_v225,
    run_current_runtime_bundle_v225,
)
from ecomsre.dta_v2.v22.real_fault_capture_v225 import (
    RealFaultCaseKind,
    build_alias_maps_v225,
    build_opaque_capture_v225,
    build_physical_capture_v225,
    build_source_window_v225,
)
from ecomsre.dta_v2.v22.real_fault_comparison_contracts_v225 import (
    RealFaultCaseTruthV1,
    RealFaultStudyArm,
    build_real_fault_schedule_v225,
)
from ecomsre.dta_v2.v22.real_fault_flat_arm_v225 import (
    run_v2_style_flat_adaptive_v225,
)
from ecomsre.dta_v2.v22.real_fault_live_v225 import (
    capture_real_fault_physical_state_v225,
)
from ecomsre.dta_v2.v22.real_fault_preflight_v225 import (
    select_healthy_comparator_v225,
)
from ecomsre.dta_v2.v22.real_fault_study_v225 import execute_real_fault_study_v225
from ecomsre.dta_v2.v22.replay import ReplayCaptureV22
from ecomsre.dta_v2.v22.selection_provider_v222 import (
    SelectionDecisionV222,
    SelectionProviderOutcomeV222,
    SelectionTurnRequestV222,
)


CAPTURED_AT = datetime(2026, 8, 24, 2, 3, 4, tzinfo=timezone.utc)
MODEL_ID = "gpt-5.4-mini-2026-03-17"


def _resource(service: str, cpu: float) -> ResourceUsageRecordV22:
    return ResourceUsageRecordV22(
        schema_version="dta-v22.resource-usage-record.v1",
        service=service,
        sampling_window_seconds=10,
        samples=tuple(
            ResourceSampleV22(
                offset_ms=offset,
                cpu_percent=cpu,
                memory_bytes=100_000_000 + offset,
            )
            for offset in (0, 2_500, 5_000, 7_500, 10_000)
        ),
        memory_slope_bytes_per_second=0.0,
    )


def _capture(*, ad_cpu: float) -> ReplayCaptureV22:
    started = CAPTURED_AT - timedelta(seconds=60)
    return ReplayCaptureV22(
        schema_version="dta-v22.replay-capture.v1",
        captured_at=CAPTURED_AT,
        metrics=tuple(
            MetricFactV22(
                schema_version="dta-v22.metric-fact.v1",
                service=service,
                metric_kind=kind,
                support_status=MetricSupportStatusV22.SUPPORTED,
                sample_count=5,
                value=value,
                unit=unit,
                window_started_at=started,
                window_ended_at=CAPTURED_AT,
            )
            for service in ("ad", "recommendation")
            for kind, value, unit in (
                (MetricKindV22.ERROR_RATE, 0.0, MetricUnitV22.RATIO),
                (MetricKindV22.LATENCY_P95_MS, 3.0, MetricUnitV22.MILLISECONDS),
                (MetricKindV22.REQUEST_SUPPORT, 100.0, MetricUnitV22.COUNT),
            )
        ),
        logs=(),
        traces=(),
        runtime=tuple(
            RuntimeRecordV22(
                schema_version="dta-v22.runtime-record.v1",
                service=service,
                state=RuntimeStateV22.RUNNING,
                healthy=True,
                restart_count=0,
            )
            for service in ("ad", "recommendation")
        ),
        resources=(_resource("ad", ad_cpu), _resource("recommendation", 2.0)),
        changes=(),
        source_failures=(),
    )


def _cases():
    aliases = generate_opaque_identity_plan_v225(
        service_count=2, operation_count=0, change_count=0, pair_count=0
    ).services
    map_a, _map_b = build_alias_maps_v225(
        fault_service="ad", comparator_service="recommendation", aliases=aliases
    )
    baseline = build_physical_capture_v225(
        campaign_id="campaign-001",
        kind=RealFaultCaseKind.BASELINE,
        fault_service="ad",
        comparator_service="recommendation",
        source_window=build_source_window_v225(captured_at=CAPTURED_AT),
        capture=_capture(ad_cpu=2.0),
    )
    fault = build_physical_capture_v225(
        campaign_id="campaign-001",
        kind=RealFaultCaseKind.AD_CPU_FAULT,
        fault_service="ad",
        comparator_service="recommendation",
        source_window=build_source_window_v225(captured_at=CAPTURED_AT),
        capture=_capture(ad_cpu=96.0),
    )
    return (
        build_opaque_capture_v225(
            case_id="baseline-map-a", physical_capture=baseline, alias_map=map_a
        ),
        build_opaque_capture_v225(
            case_id="fault-map-a", physical_capture=fault, alias_map=map_a
        ),
        map_a.alias_for("ad"),
    )


def _all_cases():
    aliases = generate_opaque_identity_plan_v225(
        service_count=2, operation_count=0, change_count=0, pair_count=0
    ).services
    map_a, map_b = build_alias_maps_v225(
        fault_service="ad", comparator_service="recommendation", aliases=aliases
    )
    baseline = build_physical_capture_v225(
        campaign_id="campaign-001",
        kind=RealFaultCaseKind.BASELINE,
        fault_service="ad",
        comparator_service="recommendation",
        source_window=build_source_window_v225(captured_at=CAPTURED_AT),
        capture=_capture(ad_cpu=2.0),
    )
    fault = build_physical_capture_v225(
        campaign_id="campaign-001",
        kind=RealFaultCaseKind.AD_CPU_FAULT,
        fault_service="ad",
        comparator_service="recommendation",
        source_window=build_source_window_v225(captured_at=CAPTURED_AT),
        capture=_capture(ad_cpu=96.0),
    )
    captures = {
        "fault-map-a": build_opaque_capture_v225(
            case_id="fault-map-a", physical_capture=fault, alias_map=map_a
        ),
        "fault-map-b": build_opaque_capture_v225(
            case_id="fault-map-b", physical_capture=fault, alias_map=map_b
        ),
        "baseline-map-a": build_opaque_capture_v225(
            case_id="baseline-map-a", physical_capture=baseline, alias_map=map_a
        ),
        "baseline-map-b": build_opaque_capture_v225(
            case_id="baseline-map-b", physical_capture=baseline, alias_map=map_b
        ),
    }
    return captures, map_a, map_b


class _VisibleEvidenceFlatProvider:
    def __init__(self) -> None:
        self.attempted_calls = 0
        self.transport_retry_count = 0
        self.action_selection_calls = 0

    def investigation_turn(self, *, context, visible_state, read_tools_enabled):
        self.attempted_calls += 1
        usage = ProviderUsage(input_tokens=100, output_tokens=20, total_tokens=120)
        if not visible_state.adaptive_observations:
            assert read_tools_enabled
            return ProviderTurnV21(
                function_name="submit_real_fault_flat_turn",
                tool_call_id=f"call-{self.attempted_calls}",
                raw_response_sha256="1" * 64,
                usage=usage,
                monotonic_latency_ms=5,
                read_request=build_inspect_resource_usage_request(
                    run_id=context.run_id,
                    services=context.candidate_services,
                    sampling_window_seconds=10,
                    sample_count=5,
                ),
            )
        observation = visible_state.adaptive_observations[-1]
        resources = tuple(
            item for item in observation.results if isinstance(item, ResourceUsageRecord)
        )
        root = next(
            (
                item.logical_service
                for item in resources
                if max(sample.cpu_percent for sample in item.samples) >= 80.0
            ),
            None,
        )
        evidence_ref = observation.evidence_ref
        diagnosis = DtaDiagnosisV21(
            schema_version="dta-v21.diagnosis.v1",
            run_id=context.run_id,
            terminal=TerminalV21.COMPLETED,
            root_service=root,
            root_entity_ref=None if root is None else f"service:{root}",
            fault_domain=None if root is None else FaultDomainV21.LOCAL_RESOURCE,
            mechanism=None if root is None else FaultMechanismV21.CPU_SATURATION,
            confidence=None if root is None else 0.9,
            supporting_evidence_refs=(evidence_ref,),
            contradicting_evidence_refs=(),
            evidence_source_types=(EvidenceSourceV21.RESOURCES,),
            uncertainties=(),
            summary=(
                "All candidate resource records remain below the strong threshold."
                if root is None
                else "One candidate has strong CPU saturation evidence."
            ),
        )
        return ProviderTurnV21(
            function_name="submit_real_fault_flat_turn",
            tool_call_id=f"call-{self.attempted_calls}",
            raw_response_sha256="2" * 64,
            usage=usage,
            monotonic_latency_ms=5,
            diagnosis=diagnosis,
        )


class _VisibleTerminalSelectionProvider:
    def complete_turn(
        self,
        *,
        request: SelectionTurnRequestV222,
        run_id: str,
        max_protocol_repairs: int = 2,
    ) -> SelectionProviderOutcomeV222:
        del run_id, max_protocol_repairs
        terminals = cast(list[dict[str, object]], request.visible_state["terminals"])
        selected = next(
            (
                item
                for item in terminals
                if item["mechanism"] == "CPU_SATURATION"
            ),
            None,
        )
        if selected is None:
            selected = next(item for item in terminals if item["kind"] == "NO_INCIDENT")
        alias = cast(str, selected["alias"])
        terminal_id = next(
            item.canonical_id for item in request.aliases.terminals if item.alias == alias
        )
        return SelectionProviderOutcomeV222(
            decision=SelectionDecisionV222(
                selection_alias=alias,
                focus_alias="NONE",
                action_id=None,
                terminal_id=terminal_id,
                focus_hypothesis_id=None,
            ),
            first_pass_protocol_success=True,
            post_repair_protocol_success=True,
            protocol_repairs=0,
            provider_calls=1,
            transport_retry_count=0,
            input_tokens=80,
            output_tokens=8,
            total_tokens=88,
            latency_ms=4.0,
        )


class _OwnedPhysicalReadBackend:
    def __init__(self, *, ad_cpu: float = 96.0) -> None:
        self.authority = build_fake_read_authority()
        self.requests: list[object] = []
        self.ad_cpu = ad_cpu

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
                                cpu_percent=self.ad_cpu if service == "ad" else 2.0,
                                memory_bytes=100_000_000 + offset,
                            )
                            for offset in (0, 2_500, 5_000, 7_500, 10_000)
                        ),
                        memory_slope_bytes_per_second=0.0,
                    )
                    for service in request.services
                )
            )
        if isinstance(request, (SearchLogsRequest, TraceNeighborhoodRequest)):
            return BackendResult(records=())
        return BackendResult(
            records=tuple(
                MetricRecord(
                    service=request.service,
                    metric_kind=kind,
                    value=(
                        0.0
                        if kind is MetricKind.ERROR_RATE
                        else 3.0
                        if kind is MetricKind.LATENCY_P95_MS
                        else 100.0
                    ),
                    unit=METRIC_UNIT_BY_KIND[kind],
                    sample_count=5,
                )
                for kind in request.metric_kinds
            )
        )


def test_flat_is_diagnosis_only_and_keeps_free_target_selection() -> None:
    baseline, fault, truth_alias = _cases()
    provider = _VisibleEvidenceFlatProvider()

    run = run_v2_style_flat_adaptive_v225(
        capture=fault,
        baseline_capture=baseline,
        model_id=MODEL_ID,
        provider=provider,
    )

    assert run.arm is RealFaultStudyArm.V2_STYLE_FLAT_ADAPTIVE
    assert run.prediction.terminal == "DIAGNOSED"
    assert run.prediction.root_service_alias == truth_alias
    assert run.prediction.evidence_clause_valid is True
    assert run.semantic_evidence_actions == 1
    assert run.target_equivalent_reads == 2
    assert provider.action_selection_calls == 0
    assert run.agent_writes == 0
    assert run.runbook_executions == 0


def test_current_bundle_uses_same_case_bytes_and_one_two_target_bundle() -> None:
    baseline, fault, truth_alias = _cases()

    run = run_current_runtime_bundle_v225(
        capture=fault,
        baseline_capture=baseline,
        model_id=MODEL_ID,
        provider=_VisibleTerminalSelectionProvider(),
    )

    assert run.arm is RealFaultStudyArm.CURRENT_RUNTIME_BUNDLE
    assert run.case_bytes_sha256 == fault.opaque_capture_sha256
    assert run.prediction.terminal == "DIAGNOSED"
    assert run.prediction.root_service_alias == truth_alias
    assert run.prediction.mechanism == "CPU_SATURATION"
    assert run.prediction.evidence_clause_valid is True
    assert run.semantic_evidence_actions == 1
    assert run.target_equivalent_reads == 2
    assert run.bundle_resources_reads == 1
    assert run.all_candidates_covered is True


def test_both_arms_return_exact_no_incident_on_baseline_capture() -> None:
    baseline, _fault, _truth_alias = _cases()

    flat = run_v2_style_flat_adaptive_v225(
        capture=baseline,
        baseline_capture=baseline,
        model_id=MODEL_ID,
        provider=_VisibleEvidenceFlatProvider(),
    )
    current = run_current_runtime_bundle_v225(
        capture=baseline,
        baseline_capture=baseline,
        model_id=MODEL_ID,
        provider=_VisibleTerminalSelectionProvider(),
    )

    assert flat.prediction.terminal == "NO_INCIDENT"
    assert current.prediction.terminal == "NO_INCIDENT"
    assert flat.prediction.evidence_clause_valid is True
    assert current.prediction.evidence_clause_valid is True


def test_schedule_is_exact_and_counterbalanced() -> None:
    schedule = build_real_fault_schedule_v225()

    assert tuple((item.case_id, item.arm.value) for item in schedule) == (
        ("fault-map-a", "V2_STYLE_FLAT_ADAPTIVE"),
        ("fault-map-a", "CURRENT_RUNTIME_BUNDLE"),
        ("fault-map-b", "CURRENT_RUNTIME_BUNDLE"),
        ("fault-map-b", "V2_STYLE_FLAT_ADAPTIVE"),
        ("baseline-map-a", "V2_STYLE_FLAT_ADAPTIVE"),
        ("baseline-map-a", "CURRENT_RUNTIME_BUNDLE"),
        ("baseline-map-b", "CURRENT_RUNTIME_BUNDLE"),
        ("baseline-map-b", "V2_STYLE_FLAT_ADAPTIVE"),
    )


def test_current_live_adapter_reads_physical_backend_and_projects_opaque_targets() -> None:
    baseline, fault, truth_alias = _cases()
    aliases = generate_opaque_identity_plan_v225(
        service_count=2, operation_count=0, change_count=0, pair_count=0
    ).services
    map_a, _map_b = build_alias_maps_v225(
        fault_service="ad", comparator_service="recommendation", aliases=aliases
    )
    backend = _OwnedPhysicalReadBackend()

    live = run_current_runtime_bundle_live_v225(
        capture=fault,
        baseline_capture=baseline,
        alias_map=map_a,
        live_backend=backend,
        model_id=MODEL_ID,
        provider=_VisibleTerminalSelectionProvider(),
    )

    assert live.backend == "LocalSandboxReadBackend"
    assert live.arm_run.prediction.root_service_alias == truth_alias
    assert live.arm_run.prediction.mechanism == "CPU_SATURATION"
    assert live.arm_run.all_candidates_covered is True
    resource_request = next(
        item for item in backend.requests if isinstance(item, InspectResourceUsageRequest)
    )
    assert resource_request.services == ("ad", "recommendation")
    assert live.agent_writes == 0
    assert live.action_proposals == 0
    assert live.runbook_executions == 0


def test_capture_only_successor_collects_one_target_complete_physical_state() -> None:
    capture = capture_real_fault_physical_state_v225(
        backend=_OwnedPhysicalReadBackend(),
        run_id="1" * 32,
        campaign_id="campaign-001",
        kind=RealFaultCaseKind.AD_CPU_FAULT,
        comparator_service="recommendation",
    )

    assert capture.binding.services == ("ad", "recommendation")
    assert tuple(item.service for item in capture.capture.runtime) == (
        "ad",
        "recommendation",
    )
    assert tuple(item.service for item in capture.capture.resources) == (
        "ad",
        "recommendation",
    )
    assert len(capture.capture.metrics) == 6
    assert capture.capture.logs == ()
    assert capture.capture.traces == ()


def test_live_preflight_prefers_healthy_target_complete_recommendation() -> None:
    backend = _OwnedPhysicalReadBackend(ad_cpu=2.0)

    comparator = select_healthy_comparator_v225(
        backend=backend,
        run_id="2" * 32,
    )

    assert comparator == "recommendation"
    assert any(
        isinstance(item, InspectResourceUsageRequest)
        and item.services == ("ad", "recommendation")
        for item in backend.requests
    )


def test_full_schedule_executes_once_and_loads_truth_after_each_local_pair() -> None:
    captures, map_a, map_b = _all_cases()
    truth_loads: list[str] = []

    def load_truth(case_id: str) -> RealFaultCaseTruthV1:
        truth_loads.append(case_id)
        fault = case_id.startswith("fault-")
        alias_map = map_a if case_id.endswith("a") else map_b
        return RealFaultCaseTruthV1(
            schema_version="dta-v225-real-fault.case-truth.v1",
            case_id=case_id,
            case_kind="AD_CPU_FAULT" if fault else "BASELINE",
            expected_root_alias=alias_map.alias_for("ad") if fault else None,
            expected_fault_domain="LOCAL_RESOURCE" if fault else None,
            expected_mechanism="CPU_SATURATION" if fault else None,
        )

    execution, truths = execute_real_fault_study_v225(
        captures=captures,
        model_id=MODEL_ID,
        flat_provider_factory=_VisibleEvidenceFlatProvider,
        current_provider_factory=_VisibleTerminalSelectionProvider,
        truth_loader=load_truth,
    )

    assert execution.execution_count == 1
    assert len(execution.runs) == 8
    assert truth_loads == [
        "fault-map-a",
        "fault-map-b",
        "baseline-map-a",
        "baseline-map-b",
    ]
    assert tuple(item.case_id for item in truths) == tuple(truth_loads)
