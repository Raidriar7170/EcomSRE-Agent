from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from scripts.ci.verify_dta_v225_real_fault_history import (
    DEFAULT_MANIFEST,
    verify_dta_v225_real_fault_history,
)
from ecomsre.dta_v2.read_tools import ReadBackendFailure
from ecomsre.dta_v2.tool_contracts import (
    ToolErrorCode,
    build_inspect_resource_usage_request,
)
from ecomsre.dta_v2.v22.opaque_identity_v225 import (
    generate_opaque_identity_plan_v225,
)
from ecomsre.dta_v2.v22.read_contracts import (
    MetricFactV22,
    MetricKindV22,
    MetricSupportStatusV22,
    MetricUnitV22,
    LogRecordV22,
    ResourceSampleV22,
    ResourceUsageRecordV22,
    RuntimeRecordV22,
    RuntimeStateV22,
)
from ecomsre.dta_v2.v22.real_capture_backend_v225 import (
    RealCaptureSnapshotBackendV225,
)
from ecomsre.dta_v2.v22.real_fault_capture_v225 import (
    RealFaultCaseKind,
    build_alias_maps_v225,
    build_common_bootstrap_v225,
    build_opaque_capture_v225,
    build_physical_capture_v225,
    build_source_window_v225,
    require_public_capture_opaque_v225,
    require_provider_payload_opaque_v225,
    truth_root_alias_v225,
)
from ecomsre.dta_v2.v22.real_fault_study_v225 import (
    build_alias_map_set_v225,
    build_public_alias_map_set_v225,
)
from ecomsre.dta_v2.v22.replay import ReplayCaptureV22


ROOT = Path(__file__).resolve().parents[2]
RUN_ID = "0123456789abcdef0123456789abcdef"
CAPTURED_AT = datetime(2026, 8, 24, 1, 2, 3, tzinfo=timezone.utc)


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
        memory_slope_bytes_per_second=100.0,
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
        resources=(
            _resource("ad", ad_cpu),
            _resource("recommendation", 2.0),
        ),
        changes=(),
        source_failures=(),
    )


def _opaque_fault_capture():
    aliases = generate_opaque_identity_plan_v225(
        service_count=2,
        operation_count=0,
        change_count=0,
        pair_count=0,
    ).services
    map_a, _map_b = build_alias_maps_v225(
        fault_service="ad",
        comparator_service="recommendation",
        aliases=aliases,
    )
    physical = build_physical_capture_v225(
        campaign_id="campaign-001",
        kind=RealFaultCaseKind.AD_CPU_FAULT,
        fault_service="ad",
        comparator_service="recommendation",
        source_window=build_source_window_v225(captured_at=CAPTURED_AT),
        capture=_capture(ad_cpu=96.0),
    )
    return build_opaque_capture_v225(
        case_id="fault-map-a",
        physical_capture=physical,
        alias_map=map_a,
    )


def test_real_fault_history_is_byte_bound() -> None:
    assert verify_dta_v225_real_fault_history(
        repository_root=ROOT,
        manifest_path=DEFAULT_MANIFEST,
    ) == 6


def test_alias_maps_swap_truth_and_public_capture_is_opaque() -> None:
    aliases = generate_opaque_identity_plan_v225(
        service_count=2,
        operation_count=0,
        change_count=0,
        pair_count=0,
    ).services
    map_a, map_b = build_alias_maps_v225(
        fault_service="ad",
        comparator_service="recommendation",
        aliases=aliases,
    )
    assert map_a.physical_for(aliases[0]) == "ad"
    assert map_b.physical_for(aliases[0]) == "recommendation"
    assert map_a.alias_for("ad") == aliases[0]
    assert map_b.alias_for("ad") == aliases[1]

    physical = build_physical_capture_v225(
        campaign_id="campaign-001",
        kind=RealFaultCaseKind.AD_CPU_FAULT,
        fault_service="ad",
        comparator_service="recommendation",
        source_window=build_source_window_v225(captured_at=CAPTURED_AT),
        capture=_capture(ad_cpu=96.0),
    )
    left = build_opaque_capture_v225(
        case_id="fault-map-a", physical_capture=physical, alias_map=map_a
    )
    right = build_opaque_capture_v225(
        case_id="fault-map-b", physical_capture=physical, alias_map=map_b
    )

    assert left.physical_capture_sha256 == right.physical_capture_sha256
    assert truth_root_alias_v225(
        alias_map=map_a, kind=RealFaultCaseKind.AD_CPU_FAULT
    ) != truth_root_alias_v225(
        alias_map=map_b, kind=RealFaultCaseKind.AD_CPU_FAULT
    )
    assert tuple(item.service for item in left.capture.resources) == aliases
    assert tuple(item.service for item in right.capture.resources) == aliases
    require_public_capture_opaque_v225(left)
    require_public_capture_opaque_v225(right)
    assert "ad" not in left.model_dump_json()
    assert "recommendation" not in left.model_dump_json()


def test_common_bootstrap_is_identical_and_excludes_resources() -> None:
    opaque = _opaque_fault_capture()
    first = build_common_bootstrap_v225(opaque)
    second = build_common_bootstrap_v225(opaque)

    assert first == second
    assert first.runtime == opaque.capture.runtime
    assert first.metrics == opaque.capture.metrics
    assert not hasattr(first, "resources")


def test_public_alias_artifact_omits_private_physical_bindings() -> None:
    aliases = generate_opaque_identity_plan_v225(
        service_count=2, operation_count=0, change_count=0, pair_count=0
    ).services
    map_a, map_b = build_alias_maps_v225(
        fault_service="ad", comparator_service="recommendation", aliases=aliases
    )

    public = build_public_alias_map_set_v225(
        private_maps=build_alias_map_set_v225(map_a=map_a, map_b=map_b)
    )
    raw = public.model_dump_json()

    assert public.aliases == aliases
    assert "physical_service" not in raw
    assert '"ad"' not in raw
    assert "recommendation" not in raw


@pytest.mark.parametrize(
    "private_path",
    (
        "/Users/private/.ecomsre/runtime.json",
        "/private/var/folders/runtime.json",
    ),
)
def test_public_capture_rejects_private_paths_in_real_log_text(
    private_path: str,
) -> None:
    aliases = generate_opaque_identity_plan_v225(
        service_count=2, operation_count=0, change_count=0, pair_count=0
    ).services
    map_a, _map_b = build_alias_maps_v225(
        fault_service="ad", comparator_service="recommendation", aliases=aliases
    )
    capture = _capture(ad_cpu=96.0).model_copy(
        update={
            "logs": (
                LogRecordV22(
                    schema_version="dta-v22.log-record.v1",
                    observed_at=CAPTURED_AT,
                    service="ad",
                    severity="ERROR",
                    message=f"failed at {private_path}",
                ),
            )
        }
    )
    physical = build_physical_capture_v225(
        campaign_id="campaign-001",
        kind=RealFaultCaseKind.AD_CPU_FAULT,
        fault_service="ad",
        comparator_service="recommendation",
        source_window=build_source_window_v225(captured_at=CAPTURED_AT),
        capture=capture,
    )

    with pytest.raises(ValueError, match="private runtime identity"):
        build_opaque_capture_v225(
            case_id="fault-map-a", physical_capture=physical, alias_map=map_a
        )


@pytest.mark.parametrize("scenario_id", ("fault-map-a", "baseline-map-b"))
def test_provider_payload_lint_rejects_truth_bearing_case_ids(
    scenario_id: str,
) -> None:
    with pytest.raises(ValueError, match="evaluator or private material"):
        require_provider_payload_opaque_v225({"scenario_id": scenario_id})


def test_provider_payload_lint_rejects_concatenated_comparator_identity() -> None:
    with pytest.raises(ValueError, match="physical service identity"):
        require_provider_payload_opaque_v225(
            {"message": "recommendationservice timeout"}
        )


def test_snapshot_backend_multi_target_resources_and_accounting() -> None:
    opaque = _opaque_fault_capture()
    backend = RealCaptureSnapshotBackendV225(run_id=RUN_ID, capture=opaque)
    request = build_inspect_resource_usage_request(
        run_id=RUN_ID,
        services=opaque.candidate_aliases,
        sampling_window_seconds=10,
        sample_count=5,
    )

    result = backend.execute(request)

    assert len(result.records) == 2
    assert backend.semantic_action_count == 1
    assert backend.target_equivalent_read_count == 2
    assert backend.requested_targets == (opaque.candidate_aliases,)

    with pytest.raises(ReadBackendFailure) as duplicate:
        backend.execute(request)
    assert duplicate.value.error_code is ToolErrorCode.DUPLICATE_REQUEST


def test_snapshot_backend_rejects_another_run() -> None:
    opaque = _opaque_fault_capture()
    backend = RealCaptureSnapshotBackendV225(run_id=RUN_ID, capture=opaque)
    request = build_inspect_resource_usage_request(
        run_id="f" * 32,
        services=opaque.candidate_aliases,
        sampling_window_seconds=10,
        sample_count=5,
    )

    with pytest.raises(ValueError, match="run ID"):
        backend.execute(request)
