from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from ecomsre.dta_v2.v22.read_contracts import (
    EvidenceSourceV22,
    LogRecordV22,
    ReadSourceStatusV22,
)
from ecomsre.product.baselines import (
    BaselineBuildModeV1,
    BaselineBuildPolicyV1,
    BaselineRepositoryV1,
    build_environment_baseline,
)
from ecomsre.product.app import create_app
from ecomsre.product.connectors.base import (
    ConnectorQueryResultV1,
    ConnectorWindowV1,
)
from ecomsre.product.contracts import ConnectorKindV1
from ecomsre.product.environment.repository import EnvironmentRepositoryV1
from ecomsre.product.environment.services import ServiceCatalogRepositoryV1
from ecomsre.product.errors import ProductError
from ecomsre.product.jobs.contracts import JobLeaseFenceV1, ProductJobTypeV1
from ecomsre.product.jobs.repository import JobRepositoryV1
from ecomsre.product.pilot.baseline_audit_v021 import (
    BaselineConnectorBindingV021,
    BaselineConnectorExpectationV021,
    BaselineReadinessAuditRepositoryV021,
    BaselineRejectionReasonCodeV021,
    build_baseline_readiness_audit_v021,
    evaluate_baseline_windows_v021,
)
from ecomsre.product.settings import ProductSettingsV1
from ecomsre.product.storage.sqlite_store import SqliteStoreV1


NOW = datetime(2026, 8, 28, 1, 0, tzinfo=UTC)
POLICY = BaselineBuildPolicyV1(
    mode=BaselineBuildModeV1.DEMO_ONLY,
    lookback_seconds=180,
    window_count=5,
    minimum_successful_windows=4,
    warmup_seconds=180,
)


def _window(index: int) -> ConnectorWindowV1:
    started = NOW + timedelta(seconds=index * 36)
    return ConnectorWindowV1(started_at=started, ended_at=started + timedelta(seconds=36))


def _result(
    *,
    source: EvidenceSourceV22,
    window: ConnectorWindowV1,
    status: ReadSourceStatusV22 = ReadSourceStatusV22.SUCCESS_EMPTY,
    covered_services: tuple[str, ...] = ("checkout",),
    records: tuple[LogRecordV22, ...] = (),
    truncated: bool = False,
    safe_error_code: str | None = None,
) -> ConnectorQueryResultV1:
    return ConnectorQueryResultV1.build(
        source=source,
        status=status,
        requested_services=("checkout",),
        covered_services=covered_services,
        window=window,
        records=records,
        truncated=truncated,
        safe_error_code=safe_error_code,
        latency_ms=2.5,
    )


def _log(window: ConnectorWindowV1) -> LogRecordV22:
    return LogRecordV22(
        schema_version="dta-v22.log-record.v1",
        observed_at=window.ended_at - timedelta(seconds=1),
        service="checkout",
        severity="DIAGNOSTIC",
        message="checkout completed",
    )


def _accepted_window(index: int) -> tuple[ConnectorQueryResultV1, ...]:
    window = _window(index)
    return (
        _result(source=EvidenceSourceV22.METRICS, window=window),
        _result(
            source=EvidenceSourceV22.LOGS,
            window=window,
            status=ReadSourceStatusV22.SUCCESS_NONEMPTY,
            records=(_log(window),),
        ),
    )


def _bindings() -> tuple[BaselineConnectorBindingV021, ...]:
    return (
        BaselineConnectorBindingV021(
            connector_name="prometheus",
            connector_kind=ConnectorKindV1.PROMETHEUS,
        ),
        BaselineConnectorBindingV021(
            connector_name="opensearch",
            connector_kind=ConnectorKindV1.OPENSEARCH,
        ),
    )


def test_success_empty_is_valid_when_required_coverage_and_other_records_exist() -> None:
    windows = tuple(_accepted_window(index) for index in range(5))
    evaluation = evaluate_baseline_windows_v021(
        window_results=windows,
        required_complete_sources=(EvidenceSourceV22.METRICS,),
        expected_windows=tuple(_window(index) for index in range(5)),
        connector_bindings=tuple(_bindings() for _ in range(5)),
    )

    assert evaluation.accepted_ordinals == (1, 2, 3, 4, 5)
    first = evaluation.windows[0]
    assert first.accepted is True
    assert first.rejection_reason_codes == ()
    metrics = next(
        item for item in first.source_results if item.source is EvidenceSourceV22.METRICS
    )
    assert metrics.record_count == 0
    assert metrics.target_complete_satisfied is True


def test_failed_source_and_recordless_window_report_exact_reasons() -> None:
    window = _window(0)
    failed = _result(
        source=EvidenceSourceV22.LOGS,
        window=window,
        status=ReadSourceStatusV22.FAILURE_TIMEOUT,
        covered_services=(),
        safe_error_code="CONNECTOR_TIMEOUT",
    )
    evaluation = evaluate_baseline_windows_v021(
        window_results=((_result(source=EvidenceSourceV22.METRICS, window=window), failed),),
        required_complete_sources=(EvidenceSourceV22.METRICS,),
        expected_windows=(window,),
        connector_bindings=(_bindings(),),
    )

    audit = evaluation.windows[0]
    assert audit.accepted is False
    assert audit.rejection_reason_codes == (
        BaselineRejectionReasonCodeV021.SOURCE_STATUS_FAILED,
        BaselineRejectionReasonCodeV021.WINDOW_HAS_NO_RECORDS,
    )
    logs = next(item for item in audit.source_results if item.source is EvidenceSourceV22.LOGS)
    assert logs.safe_error_code == "CONNECTOR_TIMEOUT"


@pytest.mark.parametrize(
    ("results", "required", "expected_reasons"),
    [
        (
            (),
            (EvidenceSourceV22.METRICS,),
            {
                BaselineRejectionReasonCodeV021.WINDOW_HAS_NO_RESULTS,
                BaselineRejectionReasonCodeV021.WINDOW_HAS_NO_RECORDS,
                BaselineRejectionReasonCodeV021.REQUIRED_SOURCE_MISSING,
            },
        ),
        (
            None,
            (EvidenceSourceV22.METRICS,),
            {
                BaselineRejectionReasonCodeV021.REQUIRED_SOURCE_DUPLICATED,
            },
        ),
        (
            "incomplete",
            (EvidenceSourceV22.METRICS,),
            {BaselineRejectionReasonCodeV021.TARGET_COVERAGE_INCOMPLETE},
        ),
        (
            "truncated",
            (),
            {BaselineRejectionReasonCodeV021.SOURCE_RESULT_TRUNCATED},
        ),
    ],
)
def test_contract_failures_are_not_collapsed(
    results: object,
    required: tuple[EvidenceSourceV22, ...],
    expected_reasons: set[BaselineRejectionReasonCodeV021],
) -> None:
    window = _window(0)
    result_rows: tuple[ConnectorQueryResultV1, ...]
    bindings: tuple[BaselineConnectorBindingV021, ...]
    if results is None:
        result_rows = (
            _result(source=EvidenceSourceV22.METRICS, window=window),
            _result(source=EvidenceSourceV22.METRICS, window=window),
            _result(
                source=EvidenceSourceV22.LOGS,
                window=window,
                status=ReadSourceStatusV22.SUCCESS_NONEMPTY,
                records=(_log(window),),
            ),
        )
        bindings = (*_bindings()[:1], *_bindings()[:1], _bindings()[1])
    elif results == "incomplete":
        result_rows = (
            _result(
                source=EvidenceSourceV22.METRICS,
                window=window,
                covered_services=(),
            ),
            _result(
                source=EvidenceSourceV22.LOGS,
                window=window,
                status=ReadSourceStatusV22.SUCCESS_NONEMPTY,
                records=(_log(window),),
            ),
        )
        bindings = _bindings()
    elif results == "truncated":
        result_rows = (
            _result(
                source=EvidenceSourceV22.LOGS,
                window=window,
                status=ReadSourceStatusV22.SUCCESS_NONEMPTY,
                records=(_log(window),),
                truncated=True,
            ),
        )
        bindings = (_bindings()[1],)
    else:
        result_rows = ()
        bindings = ()

    evaluation = evaluate_baseline_windows_v021(
        window_results=(result_rows,),
        required_complete_sources=required,
        expected_windows=(window,),
        connector_bindings=(bindings,),
    )
    assert expected_reasons.issubset(set(evaluation.windows[0].rejection_reason_codes))


def test_mismatched_source_window_is_rejected_and_digest_is_stable() -> None:
    expected = _window(0)
    alternate_expected = ConnectorWindowV1(
        started_at=expected.started_at - timedelta(seconds=36),
        ended_at=expected.ended_at - timedelta(seconds=36),
    )
    later = _window(1)
    results = (
        _result(source=EvidenceSourceV22.METRICS, window=expected),
        _result(
            source=EvidenceSourceV22.LOGS,
            window=later,
            status=ReadSourceStatusV22.SUCCESS_NONEMPTY,
            records=(_log(later),),
        ),
    )
    first = evaluate_baseline_windows_v021(
        window_results=(results,),
        expected_windows=(expected,),
        connector_bindings=(_bindings(),),
    )
    second = evaluate_baseline_windows_v021(
        window_results=(results,),
        expected_windows=(expected,),
        connector_bindings=(_bindings(),),
    )
    changed_expected_window = evaluate_baseline_windows_v021(
        window_results=(results,),
        expected_windows=(alternate_expected,),
        connector_bindings=(_bindings(),),
    )

    assert BaselineRejectionReasonCodeV021.WINDOW_TIME_INVALID in (
        first.windows[0].rejection_reason_codes
    )
    assert first.parity_sha256 == second.parity_sha256
    assert first.windows[0].window_sha256 == second.windows[0].window_sha256
    assert changed_expected_window.windows[0].rejection_reason_codes == (
        BaselineRejectionReasonCodeV021.WINDOW_TIME_INVALID,
    )
    assert changed_expected_window.parity_sha256 != first.parity_sha256
    assert changed_expected_window.windows[0].window_sha256 != first.windows[0].window_sha256


def test_connector_expected_source_omission_is_audited_instead_of_prethrow() -> None:
    window = _window(0)
    results = (
        _result(
            source=EvidenceSourceV22.LOGS,
            window=window,
            status=ReadSourceStatusV22.SUCCESS_NONEMPTY,
            records=(_log(window),),
        ),
    )
    binding = BaselineConnectorBindingV021(
        connector_name="multi-source",
        connector_kind=ConnectorKindV1.FIXTURE,
    )
    expectation = BaselineConnectorExpectationV021(
        connector_name="multi-source",
        connector_kind=ConnectorKindV1.FIXTURE,
        expected_sources=(EvidenceSourceV22.LOGS, EvidenceSourceV22.TRACES),
    )

    evaluation = evaluate_baseline_windows_v021(
        window_results=(results,),
        expected_windows=(window,),
        connector_bindings=((binding,),),
        connector_expectations=((expectation,),),
    )

    assert evaluation.windows[0].rejection_reason_codes == (
        BaselineRejectionReasonCodeV021.CONNECTOR_SOURCE_SET_INVALID,
    )


def test_non_required_multi_target_same_source_results_remain_valid() -> None:
    window = _window(0)
    result = _result(
        source=EvidenceSourceV22.LOGS,
        window=window,
        status=ReadSourceStatusV22.SUCCESS_NONEMPTY,
        records=(_log(window),),
    )
    binding = BaselineConnectorBindingV021(
        connector_name="multi-target",
        connector_kind=ConnectorKindV1.HTTP_HEALTH,
    )
    expectation = BaselineConnectorExpectationV021(
        connector_name="multi-target",
        connector_kind=ConnectorKindV1.HTTP_HEALTH,
        expected_sources=(EvidenceSourceV22.LOGS,),
    )

    evaluation = evaluate_baseline_windows_v021(
        window_results=((result, result),),
        expected_windows=(window,),
        connector_bindings=((binding, binding),),
        connector_expectations=((expectation,),),
    )

    assert evaluation.accepted_ordinals == (1,)
    assert evaluation.windows[0].rejection_reason_codes == ()


def test_audit_pass_and_real_builder_share_accepted_ordinals(tmp_path) -> None:
    store = SqliteStoreV1(tmp_path / "product.sqlite3")
    environment = EnvironmentRepositoryV1(store).create(
        {"name": "v021", "explicit_service_catalog": ["checkout"]},
    )
    identity_map = ServiceCatalogRepositoryV1(store).get_map(environment.environment_id)
    windows = tuple(_accepted_window(index) for index in range(5))
    expected_windows = tuple(_window(index) for index in range(5))
    bindings = tuple(_bindings() for _ in range(5))

    readiness = build_baseline_readiness_audit_v021(
        environment_id=environment.environment_id,
        service_ids=("checkout",),
        baseline_entity_service_ids=tuple(
            sorted(item.service_id for item in identity_map.services)
        ),
        build_policy=POLICY.model_dump(mode="json"),
        capability_sha256="a" * 64,
        required_complete_sources=(EvidenceSourceV22.METRICS,),
        window_results=windows,
        expected_windows=expected_windows,
        connector_bindings=bindings,
    )
    baseline = build_environment_baseline(
        environment_id=environment.environment_id,
        identity_map=identity_map,
        source_capability_sha256="a" * 64,
        build_policy=POLICY,
        window_results=windows,
        built_at=NOW,
        required_complete_sources=(EvidenceSourceV22.METRICS,),
        expected_windows_v021=expected_windows,
        connector_bindings_v021=bindings,
    )

    assert readiness.final_builder_would_pass is True
    assert readiness.accepted_window_count == baseline.successful_windows == 5
    assert len(readiness.parity_sha256) == 64


def test_audit_fail_and_real_builder_reject_the_same_window_bytes(tmp_path) -> None:
    store = SqliteStoreV1(tmp_path / "product.sqlite3")
    environment = EnvironmentRepositoryV1(store).create(
        {"name": "v021-fail", "explicit_service_catalog": ["checkout"]},
    )
    identity_map = ServiceCatalogRepositoryV1(store).get_map(environment.environment_id)
    windows = tuple(_accepted_window(index) for index in range(3)) + ((), ())
    expected_windows = tuple(_window(index) for index in range(5))
    bindings = tuple(_bindings() for _ in range(3)) + ((), ())

    readiness = build_baseline_readiness_audit_v021(
        environment_id=environment.environment_id,
        service_ids=("checkout",),
        baseline_entity_service_ids=tuple(
            sorted(item.service_id for item in identity_map.services)
        ),
        build_policy=POLICY.model_dump(mode="json"),
        capability_sha256="a" * 64,
        required_complete_sources=(EvidenceSourceV22.METRICS,),
        window_results=windows,
        expected_windows=expected_windows,
        connector_bindings=bindings,
    )
    assert readiness.final_builder_would_pass is False
    assert readiness.accepted_window_count == 3

    with pytest.raises(ProductError, match="successful windows") as exc_info:
        build_environment_baseline(
            environment_id=environment.environment_id,
            identity_map=identity_map,
            source_capability_sha256="a" * 64,
            build_policy=POLICY,
            window_results=windows,
            built_at=NOW,
            required_complete_sources=(EvidenceSourceV22.METRICS,),
            expected_windows_v021=expected_windows,
            connector_bindings_v021=bindings,
        )
    assert exc_info.value.details["accepted_window_ordinals"] == [1, 2, 3]
    assert exc_info.value.details["parity_sha256"] == readiness.parity_sha256


@pytest.mark.parametrize("actual_window_count", [4, 6])
def test_audit_and_builder_both_reject_non_exact_window_schedule(
    tmp_path,
    actual_window_count: int,
) -> None:
    store = SqliteStoreV1(tmp_path / "product.sqlite3")
    environment = EnvironmentRepositoryV1(store).create(
        {"name": f"v021-count-{actual_window_count}", "explicit_service_catalog": ["checkout"]},
    )
    identity_map = ServiceCatalogRepositoryV1(store).get_map(environment.environment_id)
    windows = tuple(_accepted_window(index) for index in range(actual_window_count))
    expected_windows = tuple(_window(index) for index in range(actual_window_count))
    bindings = tuple(_bindings() for _ in range(actual_window_count))

    readiness = build_baseline_readiness_audit_v021(
        environment_id=environment.environment_id,
        service_ids=("checkout",),
        baseline_entity_service_ids=tuple(
            sorted(item.service_id for item in identity_map.services)
        ),
        build_policy=POLICY.model_dump(mode="json"),
        capability_sha256="a" * 64,
        required_complete_sources=(EvidenceSourceV22.METRICS,),
        window_results=windows,
        expected_windows=expected_windows,
        connector_bindings=bindings,
    )

    assert readiness.scheduled_window_count == actual_window_count
    assert readiness.configured_window_count == POLICY.window_count
    assert readiness.final_builder_would_pass is False
    with pytest.raises(ProductError, match="window") as exc_info:
        build_environment_baseline(
            environment_id=environment.environment_id,
            identity_map=identity_map,
            source_capability_sha256="a" * 64,
            build_policy=POLICY,
            window_results=windows,
            built_at=NOW,
            required_complete_sources=(EvidenceSourceV22.METRICS,),
            expected_windows_v021=expected_windows,
            connector_bindings_v021=bindings,
        )
    assert exc_info.value.code == "BASELINE_WINDOW_COUNT_INVALID"


def test_readiness_audit_is_create_once_and_available_through_read_api(tmp_path) -> None:
    settings = ProductSettingsV1(
        data_root=tmp_path,
        sqlite_path=tmp_path / "product.sqlite3",
        object_store_root=tmp_path / "objects",
    )
    store = SqliteStoreV1(settings.sqlite_path)
    environment = EnvironmentRepositoryV1(store).create(
        {"name": "v021-api", "explicit_service_catalog": ["checkout"]},
    )
    identity_map = ServiceCatalogRepositoryV1(store).get_map(environment.environment_id)
    readiness = build_baseline_readiness_audit_v021(
        environment_id=environment.environment_id,
        service_ids=("checkout",),
        baseline_entity_service_ids=tuple(
            sorted(item.service_id for item in identity_map.services)
        ),
        build_policy=POLICY.model_dump(mode="json"),
        capability_sha256="a" * 64,
        required_complete_sources=(EvidenceSourceV22.METRICS,),
        window_results=tuple(_accepted_window(index) for index in range(5)),
        expected_windows=tuple(_window(index) for index in range(5)),
        connector_bindings=tuple(_bindings() for _ in range(5)),
    )
    baseline_id = "base-0123456789abcdef01234567"
    repository = BaselineReadinessAuditRepositoryV021(store)
    repository.put(readiness, baseline_id=baseline_id, created_at=NOW)
    repository.put(readiness, baseline_id=baseline_id, created_at=NOW)
    with pytest.raises(ProductError, match="different content"):
        repository.put(
            readiness,
            baseline_id="base-fedcba9876543210fedcba98",
            created_at=NOW,
        )

    assert repository.get_latest(environment.environment_id) == readiness
    assert repository.get_by_baseline(baseline_id) == readiness
    with TestClient(create_app(settings)) as client:
        environment_response = client.get(
            f"/v1/environments/{environment.environment_id}/baseline-readiness"
        )
        baseline_response = client.get(f"/v1/baselines/{baseline_id}/window-audit")
    assert environment_response.status_code == 200
    assert baseline_response.status_code == 200
    assert environment_response.json()["audit_sha256"] == readiness.audit_sha256
    assert baseline_response.json()["parity_sha256"] == readiness.parity_sha256


def test_successful_audit_and_baseline_are_one_fenced_transaction(tmp_path) -> None:
    store = SqliteStoreV1(tmp_path / "product.sqlite3")
    environment = EnvironmentRepositoryV1(store).create(
        {"name": "v021-atomic", "explicit_service_catalog": ["checkout"]},
    )
    identity_map = ServiceCatalogRepositoryV1(store).get_map(environment.environment_id)
    windows = tuple(_accepted_window(index) for index in range(5))
    expected_windows = tuple(_window(index) for index in range(5))
    bindings = tuple(_bindings() for _ in range(5))
    baseline_id = "base-0123456789abcdef01234567"
    readiness = build_baseline_readiness_audit_v021(
        environment_id=environment.environment_id,
        service_ids=("checkout",),
        baseline_entity_service_ids=tuple(
            sorted(item.service_id for item in identity_map.services)
        ),
        build_policy=POLICY.model_dump(mode="json"),
        capability_sha256="a" * 64,
        required_complete_sources=(EvidenceSourceV22.METRICS,),
        window_results=windows,
        expected_windows=expected_windows,
        connector_bindings=bindings,
    )
    baseline = build_environment_baseline(
        environment_id=environment.environment_id,
        identity_map=identity_map,
        source_capability_sha256="a" * 64,
        build_policy=POLICY,
        window_results=windows,
        built_at=NOW,
        baseline_id=baseline_id,
        required_complete_sources=(EvidenceSourceV22.METRICS,),
        expected_windows_v021=expected_windows,
        connector_bindings_v021=bindings,
    )
    jobs = JobRepositoryV1(store)
    queued = jobs.enqueue(ProductJobTypeV1.BASELINE_BUILD, {}, now=100)
    claimed = jobs.claim_next("worker-one", lease_seconds=10, now=100)
    assert claimed is not None and claimed.job_id == queued.job_id
    repository = BaselineRepositoryV1(store)
    audit_repository = BaselineReadinessAuditRepositoryV021(store)

    stale_fence = JobLeaseFenceV1(
        job_id=claimed.job_id,
        claimed_by="worker-one",
        attempt_count=claimed.attempt_count,
        checked_at=111,
    )
    with pytest.raises(ProductError, match="no longer owns"):
        repository.put_with_readiness_audit_v021(
            baseline,
            readiness,
            activate=True,
            created_at=NOW,
            fence=stale_fence,
        )
    assert repository.get_optional(baseline_id) is None
    with pytest.raises(ProductError) as missing_audit:
        audit_repository.get_by_baseline(baseline_id)
    assert missing_audit.value.code == "BASELINE_WINDOW_AUDIT_NOT_FOUND"

    repository.put_with_readiness_audit_v021(
        baseline,
        readiness,
        activate=True,
        created_at=NOW,
    )
    assert repository.get_optional(baseline_id) is not None
    assert audit_repository.get_by_baseline(baseline_id) == readiness


def test_atomic_write_rolls_back_partial_audit_and_can_retry(tmp_path) -> None:
    store = SqliteStoreV1(tmp_path / "product.sqlite3")
    environment = EnvironmentRepositoryV1(store).create(
        {"name": "v021-retry", "explicit_service_catalog": ["checkout"]},
    )
    identity_map = ServiceCatalogRepositoryV1(store).get_map(environment.environment_id)
    windows = tuple(_accepted_window(index) for index in range(5))
    expected_windows = tuple(_window(index) for index in range(5))
    bindings = tuple(_bindings() for _ in range(5))
    baseline_id = "base-fedcba9876543210fedcba98"
    readiness = build_baseline_readiness_audit_v021(
        environment_id=environment.environment_id,
        service_ids=("checkout",),
        baseline_entity_service_ids=tuple(
            sorted(item.service_id for item in identity_map.services)
        ),
        build_policy=POLICY.model_dump(mode="json"),
        capability_sha256="a" * 64,
        required_complete_sources=(EvidenceSourceV22.METRICS,),
        window_results=windows,
        expected_windows=expected_windows,
        connector_bindings=bindings,
    )
    baseline = build_environment_baseline(
        environment_id=environment.environment_id,
        identity_map=identity_map,
        source_capability_sha256="a" * 64,
        build_policy=POLICY,
        window_results=windows,
        built_at=NOW,
        baseline_id=baseline_id,
        required_complete_sources=(EvidenceSourceV22.METRICS,),
        expected_windows_v021=expected_windows,
        connector_bindings_v021=bindings,
    )
    repository = BaselineRepositoryV1(store)
    audit_repository = BaselineReadinessAuditRepositoryV021(store)
    conflicting = baseline.model_copy(update={"built_at": NOW + timedelta(seconds=1)})
    repository.put(conflicting, activate=False)

    with pytest.raises(ProductError) as conflict:
        repository.put_with_readiness_audit_v021(
            baseline,
            readiness,
            activate=False,
            created_at=NOW,
        )
    assert conflict.value.code == "BASELINE_IMMUTABLE_CONFLICT"
    with pytest.raises(ProductError) as missing_audit:
        audit_repository.get_by_baseline(baseline_id)
    assert missing_audit.value.code == "BASELINE_WINDOW_AUDIT_NOT_FOUND"

    with store.connect() as connection:
        connection.execute("DELETE FROM baseline_versions WHERE baseline_id = ?", (baseline_id,))
    repository.put_with_readiness_audit_v021(
        baseline,
        readiness,
        activate=False,
        created_at=NOW,
    )
    assert repository.get_optional(baseline_id) is not None
    assert audit_repository.get_by_baseline(baseline_id) == readiness
