from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json
from pathlib import Path

import httpx
import pytest

from ecomsre.dta_v2.v22.read_contracts import (
    EvidenceSourceV22,
    LogRecordV22,
    METRIC_UNIT_BY_KIND_V22,
    MetricFactV22,
    MetricKindV22,
    MetricSupportStatusV22,
    ReadSourceStatusV22,
)
from ecomsre.product.connectors.base import (
    ConnectorQueryContextV1,
    ConnectorQueryResultV1,
    ConnectorWindowV1,
)
from ecomsre.product.baselines import (
    BaselineBuildModeV1,
    BaselineBuildPolicyV1,
    BaselineJobCreateV1,
    BaselineRepositoryV1,
    HistoricalBaselineServiceV1,
    build_environment_baseline,
)
from ecomsre.product.connectors.opensearch_profile_binding_v023 import (
    ACTIVE_PROFILE_SHA256_V023,
    build_product_v023_environment_payload,
)
from ecomsre.product.connectors.registry import ConnectorRegistryV1
from ecomsre.product.connectors.credentials import CredentialResolverV1
from ecomsre.product.connectors.prometheus import PrometheusConnectorV1
from ecomsre.product.contracts import ConnectorConfigV1, ConnectorKindV1
from ecomsre.product.environment.repository import EnvironmentRepositoryV1
from ecomsre.product.environment.services import ServiceCatalogRepositoryV1
from ecomsre.product.environment.capabilities import CapabilityMatrixRepositoryV1
from ecomsre.product.environment.verification import EnvironmentVerificationServiceV1
from ecomsre.product.errors import ProductError
from ecomsre.product.jobs.contracts import (
    ProductJobRecordV1,
    ProductJobStatusV1,
    ProductJobTypeV1,
)
from ecomsre.product.jobs.handlers import handle_baseline_build
from ecomsre.product.pilot.baseline_audit_v021 import (
    BaselineConnectorBindingV021,
    BaselineConnectorExpectationV021,
)
from ecomsre.product.pilot.baseline_readiness_v023 import (
    BASELINE_PREFLIGHT_PASS_V023,
    BaselineRejectionReasonCodeV023,
    OpenSearchWindowDiagnosticsV023,
    ProductBaselineReadinessAuditV023,
    ProductBaselineReadinessProfileV023,
    ProductBaselineReadinessAuditRepositoryV023,
    PrometheusTemplateDiagnosticV023,
    PrometheusWindowDiagnosticsV023,
    evaluate_baseline_windows_v023,
)
from ecomsre.product.storage.sqlite_store import SqliteStoreV1


ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 8, 29, 4, 0, tzinfo=UTC)


def _window(index: int) -> ConnectorWindowV1:
    started = NOW + timedelta(seconds=index * 36)
    return ConnectorWindowV1(
        started_at=started,
        ended_at=started + timedelta(seconds=36),
    )


def _result(
    source: EvidenceSourceV22,
    window: ConnectorWindowV1,
    records: tuple[MetricFactV22 | LogRecordV22, ...] = (),
) -> ConnectorQueryResultV1:
    return ConnectorQueryResultV1.build(
        source=source,
        status=(
            ReadSourceStatusV22.SUCCESS_NONEMPTY
            if records
            else ReadSourceStatusV22.SUCCESS_EMPTY
        ),
        requested_services=("checkout",),
        covered_services=("checkout",) if records else (),
        window=window,
        records=records,
        truncated=False,
        safe_error_code=None,
        latency_ms=1.0,
    )


def _metric(
    window: ConnectorWindowV1,
    kind: MetricKindV22,
    value: float,
) -> MetricFactV22:
    return MetricFactV22(
        schema_version="dta-v22.metric-fact.v1",
        service="checkout",
        metric_kind=kind,
        support_status=MetricSupportStatusV22.SUPPORTED,
        sample_count=4,
        value=value,
        unit=METRIC_UNIT_BY_KIND_V22[kind],
        window_started_at=window.started_at,
        window_ended_at=window.ended_at,
    )


def _logs(window: ConnectorWindowV1, index: int) -> tuple[LogRecordV22, ...]:
    return tuple(
        LogRecordV22(
            schema_version="dta-v22.log-record.v1",
            observed_at=window.started_at + timedelta(seconds=offset + 1),
            service="checkout",
            severity="DIAGNOSTIC",
            message=f"checkout request completed in {20 + index + offset} ms",
        )
        for offset in range(3)
    )


def _window_evidence(
    index: int,
    *,
    opensearch_rejection_codes: dict[str, int] | None = None,
):
    window = _window(index)
    metrics = (
        _metric(window, MetricKindV22.REQUEST_SUPPORT, 20.0),
        _metric(window, MetricKindV22.ERROR_RATE, 0.0),
        _metric(window, MetricKindV22.LATENCY_P95_MS, 30.0 + index),
    )
    results = (
        _result(EvidenceSourceV22.METRICS, window, metrics),
        _result(EvidenceSourceV22.RESOURCES, window),
        _result(EvidenceSourceV22.LOGS, window, _logs(window, index)),
        _result(EvidenceSourceV22.TRACES, window),
    )
    prom = PrometheusWindowDiagnosticsV023.build(
        window=window,
        metric_result_sha256=results[0].result_sha256,
        resource_result_sha256=results[1].result_sha256,
        templates=tuple(
            PrometheusTemplateDiagnosticV023.build(
                template_name=name,
                logical_service="checkout",
                status=ReadSourceStatusV22.SUCCESS_NONEMPTY,
                sample_count=4,
            )
            for name in ("error_rate", "latency", "request_support")
        ),
    )
    rejection_codes = opensearch_rejection_codes or {}
    rejected_count = sum(rejection_codes.values())
    logs = OpenSearchWindowDiagnosticsV023.build(
        window=window,
        log_result_sha256=results[2].result_sha256,
        profile_sha256=ACTIVE_PROFILE_SHA256_V023,
        query_status=ReadSourceStatusV22.SUCCESS_NONEMPTY,
        sampled_record_count=3 + rejected_count,
        accepted_record_count=3,
        rejected_record_count=rejected_count,
        rejection_fraction=rejected_count / (3 + rejected_count),
        rejection_codes_by_count=rejection_codes,
    )
    bindings = (
        BaselineConnectorBindingV021(
            connector_name="prometheus",
            connector_kind=ConnectorKindV1.PROMETHEUS,
        ),
        BaselineConnectorBindingV021(
            connector_name="prometheus",
            connector_kind=ConnectorKindV1.PROMETHEUS,
        ),
        BaselineConnectorBindingV021(
            connector_name="logs",
            connector_kind=ConnectorKindV1.OPENSEARCH,
        ),
        BaselineConnectorBindingV021(
            connector_name="traces",
            connector_kind=ConnectorKindV1.JAEGER,
        ),
    )
    expectations = (
        BaselineConnectorExpectationV021(
            connector_name="prometheus",
            connector_kind=ConnectorKindV1.PROMETHEUS,
            expected_sources=(EvidenceSourceV22.METRICS, EvidenceSourceV22.RESOURCES),
        ),
        BaselineConnectorExpectationV021(
            connector_name="logs",
            connector_kind=ConnectorKindV1.OPENSEARCH,
            expected_sources=(EvidenceSourceV22.LOGS,),
        ),
        BaselineConnectorExpectationV021(
            connector_name="traces",
            connector_kind=ConnectorKindV1.JAEGER,
            expected_sources=(EvidenceSourceV22.TRACES,),
        ),
    )
    return results, prom, logs, bindings, expectations


def _evaluation(
    *,
    missing_request_support_ordinals: tuple[int, ...] = (),
    opensearch_service_failure_ordinals: tuple[int, ...] = (),
):
    rows = [
        _window_evidence(
            index,
            opensearch_rejection_codes=(
                {"OPENSEARCH_SERVICE_ALIAS_UNMAPPED": 1}
                if index + 1 in opensearch_service_failure_ordinals
                else None
            ),
        )
        for index in range(5)
    ]
    prom = [row[1] for row in rows]
    for ordinal in missing_request_support_ordinals:
        original = prom[ordinal - 1]
        prom[ordinal - 1] = PrometheusWindowDiagnosticsV023.build(
            window=original.window,
            metric_result_sha256=original.metric_result_sha256,
            resource_result_sha256=original.resource_result_sha256,
            templates=tuple(
                PrometheusTemplateDiagnosticV023.build(
                    template_name=item.template_name,
                    logical_service=item.logical_service,
                    status=ReadSourceStatusV22.SUCCESS_EMPTY,
                    sample_count=0,
                )
                if item.template_name == "request_support"
                else item
                for item in original.templates
            ),
        )
    return evaluate_baseline_windows_v023(
        profile=ProductBaselineReadinessProfileV023.load(
            ROOT / "config/product-v023/baseline-readiness/profile.json"
        ),
        window_results=tuple(row[0] for row in rows),
        expected_windows=tuple(_window(index) for index in range(5)),
        connector_bindings=tuple(row[3] for row in rows),
        connector_expectations=tuple(row[4] for row in rows),
        prometheus_diagnostics=tuple(prom),
        opensearch_diagnostics=tuple(row[2] for row in rows),
    )


def test_v023_profile_and_five_window_evaluator_pass_the_exact_goal_contract() -> None:
    profile = ProductBaselineReadinessProfileV023.load(
        ROOT / "config/product-v023/baseline-readiness/profile.json"
    )
    evaluation = _evaluation()

    assert profile.mode == "DEMO_ONLY"
    assert profile.candidate_services == ("checkout",)
    assert profile.warmup_seconds == 180
    assert profile.baseline_accumulation_seconds == 360
    assert profile.lookback_seconds == 180
    assert profile.window_count == 5
    assert profile.minimum_accepted_windows == 4
    assert profile.queue_fault_flag == 0
    assert evaluation.terminal == BASELINE_PREFLIGHT_PASS_V023
    assert evaluation.accepted_ordinals == (1, 2, 3, 4, 5)
    assert evaluation.logs_nonempty_window_count == 5
    assert evaluation.accepted_checkout_log_record_count == 15
    assert evaluation.has_normal_checkout_log_template is True
    assert evaluation.final_builder_would_pass is True


def test_request_support_empty_rejects_that_window_without_inventing_metric_support() -> None:
    evaluation = _evaluation(missing_request_support_ordinals=(2,))

    assert evaluation.accepted_ordinals == (1, 3, 4, 5)
    assert evaluation.final_builder_would_pass is True
    assert evaluation.windows[1].rejection_reason_codes == (
        BaselineRejectionReasonCodeV023.METRICS_REQUEST_SUPPORT_EMPTY,
    )


def test_window_audit_preserves_exact_service_alias_rejection_codes() -> None:
    evaluation = _evaluation(opensearch_service_failure_ordinals=(1, 2))

    assert evaluation.final_builder_would_pass is False
    assert evaluation.windows[0].opensearch_rejection_codes == (
        "OPENSEARCH_SERVICE_ALIAS_UNMAPPED",
    )


def test_prometheus_connector_preserves_per_template_baseline_provenance() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        query = request.url.params["query"]
        values = [] if query.startswith("errors") else [[NOW.timestamp(), "1"]]
        return httpx.Response(
            200,
            json={
                "status": "success",
                "data": {"resultType": "matrix", "result": [{"values": values}]},
            },
        )

    connector = PrometheusConnectorV1(
        ConnectorConfigV1(
            name="prometheus",
            kind="PROMETHEUS",
            endpoint="https://prometheus.test",
            settings={
                "query_templates": {
                    "request_support": "requests{service=\"{service}\"}",
                    "error_rate": "errors{service=\"{service}\"}",
                    "latency": "latency{service=\"{service}\"}",
                    "cpu": "cpu{service=\"{service}\"}",
                    "memory": "memory{service=\"{service}\"}",
                }
            },
        ),
        credential_resolver=CredentialResolverV1(environment={}),
        timeout_seconds=2,
        transport=httpx.MockTransport(handler),
    )
    context = ConnectorQueryContextV1.model_validate({
        "environment_id": "env-" + "1" * 24,
        "requested_services": ("checkout",),
        "window": _window(0),
        "maximum_records": 200,
        "purpose": "BASELINE",
    })

    results = connector.query(context)
    diagnostics = connector.baseline_diagnostics_v023()

    assert diagnostics is not None
    assert diagnostics.metric_result_sha256 == results[0].result_sha256
    assert diagnostics.resource_result_sha256 == results[1].result_sha256
    by_name = {item.template_name: item for item in diagnostics.templates}
    assert by_name["request_support"].status is ReadSourceStatusV22.SUCCESS_NONEMPTY
    assert by_name["error_rate"].status is ReadSourceStatusV22.SUCCESS_EMPTY
    assert by_name["latency"].status is ReadSourceStatusV22.SUCCESS_NONEMPTY


def test_real_environment_baseline_consumes_the_exact_v023_evaluation(tmp_path: Path) -> None:
    rows = [_window_evidence(index) for index in range(5)]
    evaluation = _evaluation()
    store = SqliteStoreV1(tmp_path / "product.sqlite3")
    environment = EnvironmentRepositoryV1(store).create(
        {"name": "v023-builder", "explicit_service_catalog": ["checkout"]}
    )
    identity_map = ServiceCatalogRepositoryV1(store).get_map(
        environment.environment_id
    )
    policy = BaselineBuildPolicyV1(
        mode=BaselineBuildModeV1.DEMO_ONLY,
        lookback_seconds=180,
        window_count=5,
        minimum_successful_windows=4,
        warmup_seconds=180,
    )

    baseline = build_environment_baseline(
        environment_id=environment.environment_id,
        identity_map=identity_map,
        source_capability_sha256="2" * 64,
        build_policy=policy,
        window_results=tuple(row[0] for row in rows),
        built_at=NOW + timedelta(minutes=10),
        evaluation_v023=evaluation,
    )

    assert baseline.successful_windows == 5
    assert baseline.v22_baseline_profile.metric_stats
    assert baseline.v22_baseline_profile.metric_stats[0].service == "checkout"
    assert baseline.normal_log_templates
    assert baseline.normal_log_templates[0].service == "checkout"

    mismatched = list(row[0] for row in rows)
    mismatched[0] = mismatched[1]
    with pytest.raises(ProductError, match="v0.2.3 evaluation"):
        build_environment_baseline(
            environment_id=environment.environment_id,
            identity_map=identity_map,
            source_capability_sha256="2" * 64,
            build_policy=policy,
            window_results=tuple(mismatched),
            built_at=NOW + timedelta(minutes=10),
            evaluation_v023=evaluation,
        )


def test_historical_service_builds_and_atomically_persists_the_v023_baseline(
    tmp_path: Path,
) -> None:
    payload = build_product_v023_environment_payload(
        repository_root=ROOT,
        runtime_authority_sha256="1" * 64,
    )
    payload["connector_configs"] = [
        item
        for item in payload["connector_configs"]
        if item["kind"] in {"PROMETHEUS", "OPENSEARCH", "JAEGER"}
    ]

    def prometheus(request: httpx.Request) -> httpx.Response:
        if "/label/" in request.url.path:
            return httpx.Response(
                200,
                json={"status": "success", "data": ["checkout", "checkoutservice"]},
            )
        ended = float(request.url.params["end"])
        return httpx.Response(
            200,
            json={
                "status": "success",
                "data": {
                    "resultType": "matrix",
                    "result": [{"metric": {}, "values": [[ended, "1"]]}],
                },
            },
        )

    def opensearch(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if body["size"] == 0:
            return httpx.Response(
                200,
                json={
                    "hits": {"hits": []},
                    "aggregations": {
                        "services": {
                            "buckets": [
                                {"key": "checkout"},
                                {"key": "checkoutservice"},
                            ]
                        }
                    },
                },
            )
        ended_at = body["query"]["bool"]["filter"][1]["range"]["@timestamp"][
            "lte"
        ]
        hits = [
            {
                "_source": {
                    "@timestamp": ended_at,
                    "resource": {"service": {"name": "checkoutservice"}},
                    "severity": {"text": "INFO"},
                    "body": f"healthy checkout request {index}",
                    "traceId": f"{index + 1:032x}",
                }
            }
            for index in range(3)
        ]
        return httpx.Response(
            200,
            json={"hits": {"hits": hits, "total": {"value": len(hits)}}},
        )

    def jaeger(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/api/services"):
            return httpx.Response(200, json={"data": ["checkout"]})
        return httpx.Response(200, json={"data": []})

    store = SqliteStoreV1(tmp_path / "product.sqlite3")
    environments = EnvironmentRepositoryV1(store)
    services = ServiceCatalogRepositoryV1(store)
    capabilities = CapabilityMatrixRepositoryV1(store)
    environment = environments.create(payload)
    registry = ConnectorRegistryV1(
        credential_resolver=CredentialResolverV1(environment={}),
        timeout_seconds=2,
        transports={
            "prometheus": httpx.MockTransport(prometheus),
            "opensearch": httpx.MockTransport(opensearch),
            "jaeger": httpx.MockTransport(jaeger),
        },
    )
    verification = EnvironmentVerificationServiceV1(
        services=services,
        capabilities=capabilities,
        connectors=registry,
    ).verify(environment, verified_at=NOW)
    baseline_repository = BaselineRepositoryV1(store)
    audit_repository = ProductBaselineReadinessAuditRepositoryV023(store)

    baseline_service = HistoricalBaselineServiceV1(
        connectors=registry,
        repository=baseline_repository,
        maximum_records_per_source=200,
        audit_repository_v023=audit_repository,
    )
    baseline = baseline_service.build(
        environment=environment,
        identity_map=verification.service_identity_map,
        capability_matrix=verification.capability_matrix,
        request=BaselineJobCreateV1(
            build_policy=BaselineBuildPolicyV1(
                mode=BaselineBuildModeV1.DEMO_ONLY,
                lookback_seconds=180,
                window_count=5,
                minimum_successful_windows=4,
                warmup_seconds=180,
            ),
            candidate_services=("checkout",),
            planned_windows=tuple(_window(index) for index in range(5)),
            activate=True,
        ),
        built_at=NOW + timedelta(minutes=10),
    )
    audit = audit_repository.get_by_baseline(baseline.baseline_id)

    assert baseline.active is True
    assert baseline.successful_windows == 5
    assert baseline.v22_baseline_profile.metric_stats
    assert baseline.normal_log_templates
    assert audit.final_builder_would_pass is True
    assert audit.baseline_sha256 == baseline.baseline_sha256
    assert audit.parity_sha256 == audit.evaluation.parity_sha256
    assert audit.active_opensearch_profile_sha256 == ACTIVE_PROFILE_SHA256_V023
    assert tuple(item.window for item in audit.evaluation.windows) == tuple(
        _window(index) for index in range(5)
    )

    job = ProductJobRecordV1(
        job_id="job-" + "f" * 24,
        job_type=ProductJobTypeV1.BASELINE_BUILD,
        status=ProductJobStatusV1.RUNNING,
        payload={
            "environment_id": environment.environment_id,
            "request": {
                "build_policy": {
                    "mode": "DEMO_ONLY",
                    "lookback_seconds": 180,
                    "window_count": 5,
                    "minimum_successful_windows": 4,
                    "warmup_seconds": 180,
                },
                "candidate_services": ["checkout"],
                "activate": True,
            },
        },
        result=None,
        safe_error_code=None,
        idempotency_key="product-v023-real-shape",
        claimed_by="worker-v023",
        lease_expires_at=None,
        attempt_count=1,
        created_at=(NOW + timedelta(minutes=11)).timestamp(),
        updated_at=(NOW + timedelta(minutes=11)).timestamp(),
    )
    job_result = handle_baseline_build(
        job,
        environments,
        services,
        capabilities,
        baseline_service,
    )
    job_audit = ProductBaselineReadinessAuditV023.model_validate(
        job_result["readiness_audit_v023"]
    )

    assert job_result["baseline_id"] == job_audit.baseline_id
    assert job_result["baseline_sha256"] == job_audit.baseline_sha256
