from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from ecomsre.dta_v2.v22.read_contracts import (
    EvidenceSourceV22,
    METRIC_UNIT_BY_KIND_V22,
    MetricFactV22,
    MetricKindV22,
    MetricSupportStatusV22,
    ReadSourceStatusV22,
)
from ecomsre.product.connectors.base import (
    ConnectorCapabilityV1,
    ConnectorQueryResultV1,
    ConnectorWindowV1,
)
from ecomsre.product.connectors.credentials import (
    ConnectorCredentialError,
    CredentialResolverV1,
)
from ecomsre.product.contracts import (
    ConnectorConfigV1,
    EnvironmentCreateV1,
    HttpHealthTargetSettingsV1,
    PrometheusConnectorSettingsV1,
)


def _window() -> ConnectorWindowV1:
    return ConnectorWindowV1(
        started_at=datetime(2026, 8, 27, 0, 0, tzinfo=UTC),
        ended_at=datetime(2026, 8, 27, 0, 5, tzinfo=UTC),
    )


def _metric() -> MetricFactV22:
    window = _window()
    return MetricFactV22(
        schema_version="dta-v22.metric-fact.v1",
        service="payment",
        metric_kind=MetricKindV22.ERROR_RATE,
        support_status=MetricSupportStatusV22.SUPPORTED,
        sample_count=3,
        value=0.01,
        unit=METRIC_UNIT_BY_KIND_V22[MetricKindV22.ERROR_RATE],
        window_started_at=window.started_at,
        window_ended_at=window.ended_at,
    )


def test_connector_query_result_binds_v22_records_and_semantics() -> None:
    result = ConnectorQueryResultV1.build(
        source=EvidenceSourceV22.METRICS,
        status=ReadSourceStatusV22.SUCCESS_NONEMPTY,
        requested_services=("payment",),
        covered_services=("payment",),
        window=_window(),
        records=(_metric(),),
        truncated=False,
        safe_error_code=None,
        latency_ms=12.5,
    )

    assert result.result_sha256 != "0" * 64
    assert result.records == (_metric(),)
    payload = result.model_dump(mode="python")
    payload["result_sha256"] = "0" * 64
    with pytest.raises(ValidationError, match="digest"):
        ConnectorQueryResultV1.model_validate(payload)

    with pytest.raises(ValidationError, match="failure|safe error"):
        ConnectorQueryResultV1.build(
            source=EvidenceSourceV22.METRICS,
            status=ReadSourceStatusV22.FAILURE_TIMEOUT,
            requested_services=("payment",),
            covered_services=(),
            window=_window(),
            records=(),
            truncated=False,
            safe_error_code=None,
            latency_ms=1000,
        )


def test_connector_contract_rejects_undeclared_fields_and_wrong_source_records() -> None:
    record = _metric().model_copy(
        update={"service": "payment-failure"},
    )
    legitimate = ConnectorQueryResultV1.build(
        source=EvidenceSourceV22.METRICS,
        status=ReadSourceStatusV22.SUCCESS_NONEMPTY,
        requested_services=("payment-failure",),
        covered_services=("payment-failure",),
        window=_window(),
        records=(record,),
        truncated=False,
        safe_error_code=None,
        latency_ms=1,
    )
    payload = legitimate.model_dump(mode="python")
    payload["evaluator_truth"] = {"expected_mechanism": "hidden"}
    with pytest.raises(ValidationError, match="extra"):
        ConnectorQueryResultV1.model_validate(payload)

    with pytest.raises(ValidationError, match="source"):
        ConnectorQueryResultV1.build(
            source=EvidenceSourceV22.LOGS,
            status=ReadSourceStatusV22.SUCCESS_NONEMPTY,
            requested_services=("payment",),
            covered_services=("payment",),
            window=_window(),
            records=(_metric(),),
            truncated=False,
            safe_error_code=None,
            latency_ms=1,
        )


def test_connector_capability_is_closed_and_bounded() -> None:
    capability = ConnectorCapabilityV1(
        source=EvidenceSourceV22.METRICS,
        supports_historical_range=True,
        supports_multi_target=True,
        supports_service_discovery=True,
        supports_baseline=True,
        supports_target_complete_coverage=False,
        maximum_window_seconds=3600,
    )

    assert capability.maximum_window_seconds == 3600
    with pytest.raises(ValidationError):
        ConnectorCapabilityV1.model_validate(
            {**capability.model_dump(), "unexpected": True}
        )


def test_connector_configuration_limits_are_fail_closed() -> None:
    templates = {
        name: f'{name}{{service_name="{{service}}"}}'
        for name in ("request_support", "error_rate", "latency", "cpu", "memory")
    }
    with pytest.raises(ValidationError):
        PrometheusConnectorSettingsV1(
            query_templates=templates,
            maximum_response_bytes=10_000_001,
        )
    with pytest.raises(ValidationError):
        HttpHealthTargetSettingsV1(
            service_id="payment",
            health_url="https://payment.test/health",
            timeout_seconds=61,
        )

    with pytest.raises(ValidationError):
        EnvironmentCreateV1(
            name="too-many-services",
            explicit_service_catalog=tuple(f"service-{index}" for index in range(21)),
        )
    with pytest.raises(ValidationError):
        ConnectorConfigV1(
            name="runtime",
            kind="HTTP_HEALTH",
            settings={
                "services": [
                    {
                        "service_id": f"service-{index}",
                        "health_url": f"https://service-{index}.test/health",
                    }
                    for index in range(21)
                ]
            },
        )


@pytest.mark.parametrize(
    ("kind", "settings"),
    (
        (
            "PROMETHEUS",
            {
                "query_templates": {
                    name: f'{name}{{service_name="{{service}}"}}'
                    for name in (
                        "request_support",
                        "error_rate",
                        "latency",
                        "cpu",
                        "memory",
                    )
                }
            },
        ),
        (
            "OPENSEARCH",
            {
                "index_pattern": "otel-*",
                "timestamp_field": "@timestamp",
                "service_field": "service",
                "severity_field": "severity",
                "message_field": "message",
            },
        ),
        ("JAEGER", {}),
    ),
)
def test_network_connector_kinds_require_base_endpoint(kind, settings) -> None:
    with pytest.raises(ValidationError, match="endpoint is required"):
        ConnectorConfigV1(name=kind.lower(), kind=kind, settings=settings)


def test_credential_resolver_supports_bearer_basic_and_static_headers(
    tmp_path: Path,
) -> None:
    password_path = tmp_path / "password"
    password_path.write_text("file-password\n", encoding="utf-8")
    resolver = CredentialResolverV1(
        environment={
            "PROM_BEARER": "bearer-secret",
            "BASIC_USER": "otel-user",
            "STATIC_HEADER": "tenant-secret",
        }
    )

    bearer = resolver.resolve_http_headers({"bearer": "env:PROM_BEARER"})
    assert bearer.as_dict() == {"Authorization": "Bearer bearer-secret"}
    assert "bearer-secret" not in repr(bearer)

    basic = resolver.resolve_http_headers(
        {
            "basic_username": "env:BASIC_USER",
            "basic_password": f"file:{password_path}",
            "header.X-Tenant": "env:STATIC_HEADER",
        }
    )
    assert basic.as_dict()["Authorization"].startswith("Basic ")
    assert basic.as_dict()["X-Tenant"] == "tenant-secret"
    assert "file-password" not in repr(basic)


def test_credential_resolver_fails_closed_without_leaking_secret_paths(
    tmp_path: Path,
) -> None:
    missing = tmp_path / "super-secret-password"
    resolver = CredentialResolverV1(environment={})

    with pytest.raises(ConnectorCredentialError) as captured:
        resolver.resolve_http_headers({"bearer": "env:MISSING_BEARER"})
    assert "MISSING_BEARER" not in str(captured.value)

    with pytest.raises(ConnectorCredentialError) as captured:
        resolver.resolve_http_headers({"bearer": f"file:{missing}"})
    assert str(missing) not in str(captured.value)

    with pytest.raises(ConnectorCredentialError, match="credential configuration"):
        resolver.resolve_http_headers(
            {
                "bearer": "env:MISSING_BEARER",
                "basic_username": "env:MISSING_USER",
                "basic_password": "env:MISSING_PASSWORD",
            }
        )


def test_connector_config_rejects_unrecognized_credential_reference_names() -> None:
    with pytest.raises(ValidationError, match="credential reference configuration"):
        ConnectorConfigV1(
            name="prometheus",
            kind="PROMETHEUS",
            endpoint="https://prometheus.test",
            settings={},
            credential_refs={"token": "env:PROM_TOKEN"},
        )


def test_connector_window_requires_utc_and_is_bounded() -> None:
    with pytest.raises(ValidationError, match="UTC"):
        ConnectorWindowV1(
            started_at=datetime(2026, 8, 27, 0, 0),
            ended_at=datetime(2026, 8, 27, 0, 5),
        )

    with pytest.raises(ValidationError, match="window"):
        ConnectorWindowV1(
            started_at=datetime(2026, 8, 27, 0, 0, tzinfo=UTC),
            ended_at=datetime(2026, 8, 27, 0, 0, tzinfo=UTC) + timedelta(hours=2),
        )
