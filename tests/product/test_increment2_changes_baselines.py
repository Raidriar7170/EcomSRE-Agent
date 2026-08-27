from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from ecomsre.dta_v2.v22.read_contracts import (
    ChangeCategoryV22,
    EvidenceSourceV22,
    LogRecordV22,
    METRIC_UNIT_BY_KIND_V22,
    MetricFactV22,
    MetricKindV22,
    MetricSupportStatusV22,
    ReadSourceStatusV22,
    ResourceSampleV22,
    ResourceUsageRecordV22,
    RolloutStateV22,
    SpanStatusV22,
    TraceSpanV22,
)
from ecomsre.product.baselines import (
    BaselineBuildPolicyV1,
    BaselineRepositoryV1,
    build_environment_baseline,
)
from ecomsre.product.changes import ChangeEventRepositoryV1
from ecomsre.product.connectors.base import (
    ConnectorQueryResultV1,
    ConnectorWindowV1,
)
from ecomsre.product.environment.repository import EnvironmentRepositoryV1
from ecomsre.product.environment.services import ServiceCatalogRepositoryV1
from ecomsre.product.errors import ProductError
from ecomsre.product.jobs.contracts import (
    JobLeaseFenceV1,
    ProductJobTypeV1,
)
from ecomsre.product.jobs.repository import JobRepositoryV1
from ecomsre.product.storage.sqlite_store import SqliteStoreV1


NOW = datetime(2026, 8, 27, 1, 0, tzinfo=UTC)


def _result(
    source: EvidenceSourceV22,
    window: ConnectorWindowV1,
    records,
) -> ConnectorQueryResultV1:
    return ConnectorQueryResultV1.build(
        source=source,
        status=(
            ReadSourceStatusV22.SUCCESS_NONEMPTY
            if records
            else ReadSourceStatusV22.SUCCESS_EMPTY
        ),
        requested_services=("payment",),
        covered_services=("payment",) if records else (),
        window=window,
        records=tuple(records),
        truncated=False,
        safe_error_code=None,
        latency_ms=1,
    )


def _window_results(index: int) -> tuple[ConnectorQueryResultV1, ...]:
    started = NOW - timedelta(minutes=60 - index * 10)
    ended = started + timedelta(minutes=10)
    window = ConnectorWindowV1(started_at=started, ended_at=ended)
    metrics = (
        MetricFactV22(
            schema_version="dta-v22.metric-fact.v1",
            service="payment",
            metric_kind=MetricKindV22.ERROR_RATE,
            support_status=MetricSupportStatusV22.SUPPORTED,
            sample_count=10,
            value=0.01 + index * 0.001,
            unit=METRIC_UNIT_BY_KIND_V22[MetricKindV22.ERROR_RATE],
            window_started_at=started,
            window_ended_at=ended,
        ),
    )
    resources = (
        ResourceUsageRecordV22(
            schema_version="dta-v22.resource-usage-record.v1",
            service="payment",
            sampling_window_seconds=30,
            samples=(
                ResourceSampleV22(offset_ms=0, cpu_percent=10.0 + index, memory_bytes=100),
                ResourceSampleV22(offset_ms=30000, cpu_percent=20.0 + index, memory_bytes=130),
            ),
            memory_slope_bytes_per_second=1.0,
        ),
    )
    traces = (
        TraceSpanV22(
            schema_version="dta-v22.trace-span.v1",
            observed_at=ended - timedelta(minutes=1),
            service_path=("payment",),
            service="payment",
            parent_service=None,
            operation="charge",
            status=SpanStatusV22.UNSET,
            duration_ms=20.0 + index,
            first_error_location=False,
        ),
    )
    logs = (
        LogRecordV22(
            schema_version="dta-v22.log-record.v1",
            observed_at=ended - timedelta(minutes=1),
            service="payment",
            severity="DIAGNOSTIC",
            message=f"charge completed in {20 + index} ms",
        ),
    )
    return (
        _result(EvidenceSourceV22.METRICS, window, metrics),
        _result(EvidenceSourceV22.RESOURCES, window, resources),
        _result(EvidenceSourceV22.TRACES, window, traces),
        _result(EvidenceSourceV22.LOGS, window, logs),
    )


def _environment(store: SqliteStoreV1):
    return EnvironmentRepositoryV1(store).create(
        {
            "name": "increment-2",
            "explicit_service_catalog": ["payment"],
        },
        now=100,
    )


def test_change_event_is_idempotent_and_maps_to_frozen_v22_contract(tmp_path) -> None:
    store = SqliteStoreV1(tmp_path / "product.sqlite3")
    environment = _environment(store)
    service = ServiceCatalogRepositoryV1(store).get_map(environment.environment_id).services[0]
    changes = ChangeEventRepositoryV1(store)
    payload = {
        "service_id": service.service_id,
        "category": "DEPLOYMENT",
        "occurred_at": NOW.isoformat(),
        "revision": "release-2026.08.27",
        "summary": "payment rollout completed",
        "external_change_id": "deploy-42",
    }

    first = changes.create(environment.environment_id, payload, now=200)
    repeated = changes.create(environment.environment_id, payload, now=300)

    assert repeated == first
    assert first.v22_record.service == "payment"
    assert first.v22_record.category is ChangeCategoryV22.DEPLOYMENT
    assert first.v22_record.rollout_state is RolloutStateV22.COMPLETED
    assert first.v22_record.revision_digest != "0" * 64
    with pytest.raises(ProductError, match="different payload"):
        changes.create(
            environment.environment_id,
            {**payload, "summary": "conflicting summary"},
        )


def test_historical_baseline_aggregates_six_windows_and_promotes_explicitly(tmp_path) -> None:
    store = SqliteStoreV1(tmp_path / "product.sqlite3")
    environment = _environment(store)
    identity_map = ServiceCatalogRepositoryV1(store).get_map(environment.environment_id)
    policy = BaselineBuildPolicyV1()
    baseline = build_environment_baseline(
        environment_id=environment.environment_id,
        identity_map=identity_map,
        source_capability_sha256="a" * 64,
        build_policy=policy,
        window_results=tuple(_window_results(index) for index in range(6)),
        built_at=NOW,
    )

    assert baseline.window_count == 6
    assert baseline.successful_windows == 6
    assert baseline.v22_baseline_profile.metric_stats[0].service == "payment"
    assert baseline.v22_baseline_profile.resource_stats[0].cpu_p95_percent == 25.0
    assert baseline.normal_log_templates[0].template == "charge completed in <num> ms"
    assert baseline.baseline_sha256 != "0" * 64

    repository = BaselineRepositoryV1(store)
    repository.put(baseline, activate=False)
    assert repository.list(environment.environment_id)[0].active is False
    promoted = build_environment_baseline(
        environment_id=environment.environment_id,
        identity_map=identity_map,
        source_capability_sha256="a" * 64,
        build_policy=policy,
        window_results=tuple(_window_results(index) for index in range(6)),
        built_at=NOW + timedelta(minutes=1),
    )
    repository.put(promoted, activate=True)
    listed = repository.list(environment.environment_id)
    assert [item.active for item in listed] == [True, False]


def test_baseline_fails_closed_below_four_successful_windows(tmp_path) -> None:
    store = SqliteStoreV1(tmp_path / "product.sqlite3")
    environment = _environment(store)
    identity_map = ServiceCatalogRepositoryV1(store).get_map(environment.environment_id)
    failed_window = tuple(
        ConnectorQueryResultV1.build(
            source=result.source,
            status=ReadSourceStatusV22.FAILURE_TIMEOUT,
            requested_services=result.requested_services,
            covered_services=(),
            window=result.window,
            records=(),
            truncated=False,
            safe_error_code="CONNECTOR_TIMEOUT",
            latency_ms=1,
        )
        for result in _window_results(0)
    )
    with pytest.raises(ProductError, match="successful windows"):
        build_environment_baseline(
            environment_id=environment.environment_id,
            identity_map=identity_map,
            source_capability_sha256="a" * 64,
            build_policy=BaselineBuildPolicyV1(),
            window_results=(
                *tuple(_window_results(index) for index in range(3)),
                failed_window,
            ),
            built_at=NOW,
        )


def test_baseline_requires_per_service_coverage_for_complete_sources(tmp_path) -> None:
    store = SqliteStoreV1(tmp_path / "product.sqlite3")
    environment = _environment(store)
    identity_map = ServiceCatalogRepositoryV1(store).get_map(environment.environment_id)
    windows: list[tuple[ConnectorQueryResultV1, ...]] = []
    for index in range(6):
        results = list(_window_results(index))
        if index < 3:
            metric = results[0]
            results[0] = ConnectorQueryResultV1.build(
                source=EvidenceSourceV22.METRICS,
                status=ReadSourceStatusV22.SUCCESS_EMPTY,
                requested_services=metric.requested_services,
                covered_services=(),
                window=metric.window,
                records=(),
                truncated=False,
                safe_error_code=None,
                latency_ms=1,
            )
        windows.append(tuple(results))

    with pytest.raises(ProductError, match="successful windows"):
        build_environment_baseline(
            environment_id=environment.environment_id,
            identity_map=identity_map,
            source_capability_sha256="a" * 64,
            build_policy=BaselineBuildPolicyV1(),
            window_results=tuple(windows),
            built_at=NOW,
            required_complete_sources=(EvidenceSourceV22.METRICS,),
        )
def test_baseline_write_is_fenced_and_same_job_version_is_idempotent(tmp_path) -> None:
    store = SqliteStoreV1(tmp_path / "product.sqlite3")
    environment = _environment(store)
    identity_map = ServiceCatalogRepositoryV1(store).get_map(environment.environment_id)
    baseline_id = "base-0123456789abcdef01234567"
    baseline = build_environment_baseline(
        environment_id=environment.environment_id,
        identity_map=identity_map,
        source_capability_sha256="a" * 64,
        build_policy=BaselineBuildPolicyV1(),
        window_results=tuple(_window_results(index) for index in range(6)),
        built_at=NOW,
        baseline_id=baseline_id,
    )
    jobs = JobRepositoryV1(store)
    queued = jobs.enqueue(ProductJobTypeV1.BASELINE_BUILD, {}, now=100)
    claimed = jobs.claim_next("worker-one", lease_seconds=10, now=100)
    assert claimed is not None and claimed.job_id == queued.job_id
    repository = BaselineRepositoryV1(store)

    jobs.renew_lease(
        claimed.job_id,
        "worker-one",
        claimed.attempt_count,
        lease_seconds=10,
        now=105,
    )
    assert jobs.get(claimed.job_id).lease_expires_at == 115

    with pytest.raises(ProductError, match="no longer owns"):
        repository.put(
            baseline,
            activate=False,
            fence=JobLeaseFenceV1(
                job_id=claimed.job_id,
                claimed_by="worker-one",
                attempt_count=claimed.attempt_count,
                checked_at=116,
            ),
        )
    assert repository.list(environment.environment_id) == ()

    repository.put(baseline, activate=False)
    repository.put(baseline, activate=False)
    assert tuple(item.baseline_id for item in repository.list(environment.environment_id)) == (
        baseline_id,
    )
