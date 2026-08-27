"""Short evaluator-owned probe for Product connector/baseline contracts."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime, timedelta
import json
import math
from pathlib import Path
from typing import Any

import httpx

from ecomsre.dta_v2.read_only_smoke import _SandboxOwnedSmokeLifecycle
from ecomsre.product.connectors.base import (
    ConnectorQueryContextV1,
    ConnectorQueryPurposeV1,
    ConnectorWindowV1,
)
from ecomsre.product.connectors.credentials import CredentialResolverV1
from ecomsre.product.connectors.registry import ConnectorRegistryV1
from ecomsre.product.contracts import (
    ConnectorKindV1,
    PrometheusConnectorSettingsV1,
)
from ecomsre.product.environment.capabilities import CapabilityMatrixRepositoryV1
from ecomsre.product.environment.repository import EnvironmentRepositoryV1
from ecomsre.product.environment.services import ServiceCatalogRepositoryV1
from ecomsre.product.environment.verification import EnvironmentVerificationServiceV1
from ecomsre.product.storage.sqlite_store import SqliteStoreV1


_ALIAS_FIELD_BY_KIND = {
    ConnectorKindV1.PROMETHEUS: "prometheus",
    ConnectorKindV1.OPENSEARCH: "opensearch",
    ConnectorKindV1.JAEGER: "jaeger",
    ConnectorKindV1.HTTP_HEALTH: "http_health",
    ConnectorKindV1.FIXTURE: None,
}


def _host_environment_payload(root: Path) -> dict[str, Any]:
    value = json.loads(
        (root / "examples/product/environment.otel-demo.json").read_text(
            encoding="utf-8"
        )
    )
    if not isinstance(value, dict):
        raise RuntimeError("OTel environment profile is invalid")
    for connector in value.get("connector_configs", []):
        if not isinstance(connector, dict):
            continue
        endpoint = connector.get("endpoint")
        if isinstance(endpoint, str):
            connector["endpoint"] = endpoint.replace(
                "host.docker.internal", "127.0.0.1"
            )
        settings = connector.get("settings")
        if isinstance(settings, dict):
            for service in settings.get("services", []):
                if isinstance(service, dict) and isinstance(
                    service.get("health_url"), str
                ):
                    service["health_url"] = service["health_url"].replace(
                        "host.docker.internal", "127.0.0.1"
                    )
    return value


def _prometheus_template_issues(
    *,
    endpoint: str,
    settings: PrometheusConnectorSettingsV1,
    aliases: tuple[str, ...],
    window: ConnectorWindowV1,
) -> list[dict[str, object]]:
    issues: list[dict[str, object]] = []
    with httpx.Client(timeout=10) as client:
        for alias in aliases:
            for name, template in settings.query_templates.items():
                rendered = template.replace("{service}", alias)
                response = client.get(
                    f"{endpoint.rstrip('/')}/api/v1/query_range",
                    params={
                        "query": rendered,
                        "start": window.started_at.timestamp(),
                        "end": window.ended_at.timestamp(),
                        "step": settings.step_seconds,
                    },
                )
                problem: set[str] = set()
                series_count = 0
                sample_count = 0
                try:
                    body = response.json()
                    data = body.get("data") if isinstance(body, dict) else None
                    result = data.get("result") if isinstance(data, dict) else None
                    result_type = (
                        data.get("resultType") if isinstance(data, dict) else None
                    )
                    if (
                        response.status_code != 200
                        or not isinstance(body, dict)
                        or body.get("status") != "success"
                        or result_type != "matrix"
                        or not isinstance(result, list)
                    ):
                        problem.add("RESPONSE_CONTRACT")
                        result = []
                    series_count = len(result)
                    for series in result:
                        values = series.get("values") if isinstance(series, dict) else None
                        if not isinstance(values, list):
                            problem.add("SERIES_VALUES")
                            continue
                        sample_count += len(values)
                        for pair in values:
                            if not isinstance(pair, list) or len(pair) != 2:
                                problem.add("SAMPLE_SHAPE")
                                continue
                            try:
                                timestamp = float(pair[0])
                                value = float(pair[1])
                            except (TypeError, ValueError):
                                problem.add("SAMPLE_NUMERIC")
                                continue
                            if not math.isfinite(timestamp):
                                problem.add("SAMPLE_TIMESTAMP_NON_FINITE")
                                continue
                            # Sparse histogram quantiles legitimately produce
                            # NaN. The Product connector treats them as missing
                            # evidence, so the diagnostic probe must not call
                            # them schema defects.
                            if not math.isfinite(value):
                                continue
                            tolerance_seconds = 1.0
                            if not (
                                window.started_at.timestamp() - tolerance_seconds
                                <= timestamp
                                <= window.ended_at.timestamp() + tolerance_seconds
                            ):
                                problem.add("SAMPLE_OUTSIDE_WINDOW")
                except (TypeError, ValueError):
                    problem.add("JSON_CONTRACT")
                if problem:
                    issues.append(
                        {
                            "service": alias,
                            "template": name,
                            "http_status": response.status_code,
                            "series_count": series_count,
                            "sample_count": sample_count,
                            "issues": tuple(sorted(problem)),
                        }
                    )
    return issues


def run_probe(
    *,
    repository_root: Path,
    private_root: Path,
    stabilization_seconds: int = 30,
) -> dict[str, Any]:
    root = repository_root.resolve()
    private = private_root.resolve()
    lifecycle = _SandboxOwnedSmokeLifecycle(
        repository_root=root,
        private_root=private,
        stabilization_seconds=stabilization_seconds,
    )
    cleanup = None
    baseline_unchanged = False
    try:
        lifecycle.admit()
        lifecycle.start()
        lifecycle.wait_ready()
        lifecycle.authorize_reads()
        before = lifecycle.read_baseline_sha256()
        store = SqliteStoreV1(private / "probe.sqlite3")
        environments = EnvironmentRepositoryV1(store)
        services = ServiceCatalogRepositoryV1(store)
        capabilities = CapabilityMatrixRepositoryV1(store)
        environment = environments.create(_host_environment_payload(root))
        registry = ConnectorRegistryV1(
            credential_resolver=CredentialResolverV1(environment={}),
            timeout_seconds=10,
        )
        verified = EnvironmentVerificationServiceV1(
            services=services,
            capabilities=capabilities,
            connectors=registry,
        ).verify(environment)
        identity_map = verified.service_identity_map
        logical = tuple(item.logical_service for item in identity_map.services)
        window = ConnectorWindowV1(
            started_at=datetime.now(UTC) - timedelta(seconds=30),
            ended_at=datetime.now(UTC),
        )
        results: list[dict[str, Any]] = []
        prometheus_issues: list[dict[str, object]] = []
        for config in environment.connector_configs:
            connector = registry.create(config)
            try:
                if not any(
                    item.supports_baseline and item.supports_historical_range
                    for item in connector.capabilities()
                ):
                    continue
                alias_field = _ALIAS_FIELD_BY_KIND[config.kind]
                aliases = (
                    {}
                    if alias_field is None
                    else {
                        alias: identity.logical_service
                        for identity in identity_map.services
                        for alias in getattr(identity.aliases, alias_field)
                    }
                )
                queried = connector.query(
                    ConnectorQueryContextV1(
                        environment_id=environment.environment_id,
                        requested_services=logical,
                        service_aliases=dict(sorted(aliases.items())),
                        window=window,
                        maximum_records=200,
                        purpose=ConnectorQueryPurposeV1.BASELINE,
                    )
                )
                for item in queried:
                    results.append(
                        {
                            "connector": config.name,
                            "source": item.source.value,
                            "status": item.status.value,
                            "requested_service_count": len(item.requested_services),
                            "covered_services": item.covered_services,
                            "record_count": len(item.records),
                            "truncated": item.truncated,
                            "safe_error_code": item.safe_error_code,
                        }
                    )
                if config.kind is ConnectorKindV1.PROMETHEUS:
                    endpoint = config.endpoint
                    if endpoint is None:
                        raise RuntimeError("Prometheus probe endpoint is unavailable")
                    prometheus_issues = _prometheus_template_issues(
                        endpoint=endpoint,
                        settings=PrometheusConnectorSettingsV1.model_validate(
                            config.settings
                        ),
                        aliases=tuple(sorted(aliases)),
                        window=window,
                    )
            finally:
                connector.close()
        after = lifecycle.read_baseline_sha256()
        baseline_unchanged = before == after
        return {
            "connector_health": [
                item.model_dump(mode="json") for item in verified.connector_health
            ],
            "logical_services": logical,
            "capability_sources": [
                item.model_dump(mode="json")
                for item in verified.capability_matrix.sources
            ],
            "window_results": results,
            "prometheus_template_issues": prometheus_issues,
        }
    finally:
        cleanup = lifecycle.cleanup_owned(baseline_unchanged=baseline_unchanged)
        if cleanup.verdict != "CLEAN":
            raise RuntimeError("connector probe cleanup did not close cleanly")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository-root", required=True, type=Path)
    parser.add_argument("--private-root", required=True, type=Path)
    parser.add_argument("--stabilization-seconds", type=int, default=30)
    arguments = parser.parse_args()
    try:
        result = run_probe(
            repository_root=arguments.repository_root,
            private_root=arguments.private_root,
            stabilization_seconds=arguments.stabilization_seconds,
        )
    except Exception as error:
        print(
            json.dumps(
                {
                    "terminal": "BLOCKED_ECOMSRE_PRODUCT_LIVE_ACCEPTANCE",
                    "error_type": type(error).__name__,
                    "safe_error": str(error)[:500],
                },
                sort_keys=True,
            )
        )
        return 1
    print(json.dumps(result, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
