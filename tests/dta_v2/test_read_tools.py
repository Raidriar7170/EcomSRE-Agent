from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from ecomsre.dta_v2.evidence_store import EvidenceStoreSnapshot, resolve_diagnosis_view
from ecomsre.dta_v2.read_tools import (
    DispatchLimitExceeded,
    FakeReadBackend,
    InvestigationReadTools,
)
from ecomsre.dta_v2.tool_contracts import (
    MetricKind,
    ObservationStatus,
    ToolErrorCode,
    build_inspect_resource_usage_request,
    build_inspect_service_runtime_request,
    build_query_metrics_request,
    build_search_logs_request,
    build_trace_neighborhood_request,
)


RUN_ID = "2" * 32
START = datetime(2026, 8, 16, 2, 0, tzinfo=timezone.utc)
END = START + timedelta(minutes=2)


def _requests() -> tuple[object, ...]:
    return (
        build_query_metrics_request(
            run_id=RUN_ID,
            service="payment",
            started_at=START,
            ended_at=END,
            metric_kinds=(MetricKind.ERROR_RATE, MetricKind.REQUEST_SUPPORT),
            max_results=6,
        ),
        build_search_logs_request(
            run_id=RUN_ID,
            service="payment",
            started_at=START,
            ended_at=END,
            max_records=5,
        ),
        build_trace_neighborhood_request(
            run_id=RUN_ID,
            service="payment",
            started_at=START,
            ended_at=END,
            max_spans=10,
        ),
        build_inspect_service_runtime_request(
            run_id=RUN_ID,
            services=("payment",),
            max_results=3,
        ),
        build_inspect_resource_usage_request(
            run_id=RUN_ID,
            services=("payment",),
            sampling_window_seconds=3,
            sample_count=3,
        ),
    )


def test_all_five_adapters_persist_typed_run_scoped_evidence() -> None:
    backend = FakeReadBackend.healthy()
    observed_sources = set()
    for request in _requests():
        tools = InvestigationReadTools(run_id=RUN_ID, backend=backend)
        observation = tools.dispatch(request)
        assert observation.status is ObservationStatus.SUCCESS
        assert observation.run_id == RUN_ID
        assert observation.request_sha256 == request.normalized_request_sha256
        assert observation.evidence_ref.startswith(f"evidence://{RUN_ID}/")
        assert observation.artifact_sha256 != "0" * 64
        assert observation.counters.dispatch_ordinal == 1
        assert observation.monotonic_latency_ms >= 0
        observed_sources.add(observation.source)
        snapshot = tools.snapshot()
        assert snapshot.observations == (observation,)
        assert snapshot.evidence_store_sha256 != "0" * 64
    assert len(observed_sources) == 5


def test_duplicate_is_rejected_before_backend_but_consumes_dispatch() -> None:
    backend = FakeReadBackend.healthy()
    tools = InvestigationReadTools(run_id=RUN_ID, backend=backend)
    request = _requests()[0]
    first = tools.dispatch(request)
    duplicate = tools.dispatch(request)

    assert first.status is ObservationStatus.SUCCESS
    assert duplicate.status is ObservationStatus.FAILURE
    assert duplicate.error_code is ToolErrorCode.DUPLICATE_REQUEST
    assert backend.call_count == 1
    assert tools.snapshot().dispatch_count == 2
    assert len(tools.snapshot().observations) == 2


def test_backend_failure_consumes_dispatch_and_is_persisted() -> None:
    backend = FakeReadBackend.failing(ToolErrorCode.SOURCE_UNAVAILABLE)
    tools = InvestigationReadTools(run_id=RUN_ID, backend=backend)
    failed = tools.dispatch(_requests()[1])

    assert failed.status is ObservationStatus.FAILURE
    assert failed.error_code is ToolErrorCode.SOURCE_UNAVAILABLE
    assert failed.result_count == 0
    assert tools.snapshot().dispatch_count == 1
    assert tools.snapshot().observations == (failed,)


def test_max_four_dispatches_blocks_a_fifth_without_backend_call() -> None:
    backend = FakeReadBackend.healthy()
    tools = InvestigationReadTools(run_id=RUN_ID, backend=backend)
    for request in _requests()[:4]:
        tools.dispatch(request)
    with pytest.raises(DispatchLimitExceeded):
        tools.dispatch(_requests()[4])
    assert tools.snapshot().dispatch_count == 4
    assert backend.call_count == 4


def test_full_store_is_separate_from_diagnosis_resolved_view(tmp_path: Path) -> None:
    tools = InvestigationReadTools(run_id=RUN_ID, backend=FakeReadBackend.healthy())
    first = tools.dispatch(_requests()[0])
    tools.dispatch(_requests()[1])
    snapshot = tools.snapshot()
    path = tmp_path / "snapshots" / "evidence-store-0002.json"
    digest = snapshot.persist_create_once(path)

    loaded = EvidenceStoreSnapshot.model_validate_json(path.read_text())
    assert loaded == snapshot
    assert digest == snapshot.evidence_store_sha256
    assert path.stat().st_mode & 0o777 == 0o600
    assert path.parent.stat().st_mode & 0o777 == 0o700

    resolved = resolve_diagnosis_view(snapshot, evidence_refs=(first.evidence_ref,))
    assert len(snapshot.observations) == 2
    assert tuple(item.evidence_ref for item in resolved.evidence) == (
        first.evidence_ref,
    )


@pytest.mark.parametrize(
    "leak",
    (
        "paymentFailure.defaultVariant",
        "feature-flag key",
        "100%",
        "expected root payment",
        "expected mechanism memory leak",
        "expected Runbook restart service",
        "scenario-controller",
        "gold label",
        "container_id=0123456789abcdef0123456789abcdef",
    ),
)
def test_truth_isolation_rejects_model_visible_leaks(leak: str) -> None:
    tools = InvestigationReadTools(
        run_id=RUN_ID,
        backend=FakeReadBackend.with_log_message(leak),
    )
    observation = tools.dispatch(_requests()[1])
    assert observation.status is ObservationStatus.FAILURE
    assert observation.error_code is ToolErrorCode.TRUTH_ISOLATION_VIOLATION
    assert leak not in observation.model_dump_json()
