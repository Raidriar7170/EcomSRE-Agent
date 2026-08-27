from __future__ import annotations

from datetime import UTC, datetime
import json
import sqlite3

import pytest
from pydantic import ValidationError

from ecomsre.dta_v2.v22.memory import PredicateKindV22
from ecomsre.dta_v2.v22.read_contracts import EvidenceSourceV22
from ecomsre.product.connectors.base import (
    ConnectorAvailabilityV1,
    ConnectorCapabilityV1,
    ConnectorHealthResultV1,
)
from ecomsre.product.contracts import (
    ConnectorKindV1,
    EnvironmentCreateV1,
    ServiceIdentityPolicyV1,
)
from ecomsre.product.environment.capabilities import (
    MechanismCapabilityStatusV1,
    SourceCapabilityStatusV1,
    build_environment_capability_matrix,
)
from ecomsre.product.environment.repository import EnvironmentRepositoryV1
from ecomsre.product.environment.services import ServiceCatalogRepositoryV1
from ecomsre.product.environment.verification import normalize_service_identities
from ecomsre.product.errors import ProductError
from ecomsre.product.storage.sqlite_store import SqliteStoreV1
from ecomsre.product.storage.migrations import MIGRATIONS


def _capability(source: EvidenceSourceV22) -> ConnectorCapabilityV1:
    return ConnectorCapabilityV1(
        source=source,
        supports_historical_range=source is not EvidenceSourceV22.RUNTIME,
        supports_multi_target=True,
        supports_service_discovery=True,
        supports_baseline=True,
        supports_target_complete_coverage=True,
        maximum_window_seconds=0 if source is EvidenceSourceV22.RUNTIME else 3600,
    )


def _health(
    name: str,
    kind: ConnectorKindV1,
    status: ConnectorAvailabilityV1,
    sources: tuple[EvidenceSourceV22, ...],
    services: tuple[str, ...] = ("frontend", "payment"),
) -> ConnectorHealthResultV1:
    return ConnectorHealthResultV1(
        connector_name=name,
        kind=kind,
        status=status,
        capabilities=tuple(
            sorted((_capability(source) for source in sources), key=lambda item: item.source.value)
        ),
        discovered_services=services,
        safe_error_code=None if status is ConnectorAvailabilityV1.AVAILABLE else "SAFE_FAILURE",
        latency_ms=1,
    )


def test_environment_create_persists_typed_dual_service_identity(tmp_path) -> None:
    store = SqliteStoreV1(tmp_path / "product.sqlite3")
    environments = EnvironmentRepositoryV1(store)
    services = ServiceCatalogRepositoryV1(store)
    request = EnvironmentCreateV1.model_validate(
        {
            "name": "identity-test",
            "service_identity_policy": {
                "services": [
                    {
                        "logical_service": "payment",
                        "aliases": {
                            "prometheus": ["payment-api"],
                            "opensearch": ["payment"],
                            "jaeger": ["paymentservice"],
                            "http_health": ["payment"],
                        },
                    }
                ]
            },
            "explicit_service_catalog": ["frontend", "payment"],
        }
    )

    environment = environments.create(request, now=100)
    identity_map = services.get_map(environment.environment_id)

    assert isinstance(environment.service_identity_policy, ServiceIdentityPolicyV1)
    assert tuple(item.logical_service for item in identity_map.services) == (
        "frontend",
        "payment",
    )
    assert all(item.service_id.startswith("svc-") for item in identity_map.services)
    payment = next(item for item in identity_map.services if item.logical_service == "payment")
    assert payment.aliases.prometheus == ("payment-api",)
    assert identity_map.identity_sha256 != "0" * 64

    restarted = ServiceCatalogRepositoryV1(SqliteStoreV1(store.path)).get_map(
        environment.environment_id
    )
    assert restarted == identity_map


def test_increment2_migration_backfills_existing_service_logical_identity(tmp_path) -> None:
    database_path = tmp_path / "increment1.sqlite3"
    connection = sqlite3.connect(database_path)
    try:
        for statement in MIGRATIONS[0][2]:
            connection.execute(statement)
        connection.execute(
            "INSERT INTO schema_migrations(version, name, applied_at) VALUES (?, ?, ?)",
            (MIGRATIONS[0][0], MIGRATIONS[0][1], "2026-08-27T00:00:00+00:00"),
        )
        connection.execute(
            """INSERT INTO environments(
                environment_id, name, description, timezone,
                service_identity_policy_json, explicit_service_catalog_json,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                "env-0123456789abcdef01234567",
                "increment-1-existing",
                "",
                "UTC",
                json.dumps({
                    "schema_version": "ecomsre.product.service-identity-policy.v1",
                    "canonical_field": None,
                    "prometheus_label": None,
                    "opensearch_field": None,
                    "jaeger_service_field": None,
                    "health_service_field": None,
                    "services": [],
                }),
                "[]",
                "2026-08-27T00:00:00+00:00",
                "2026-08-27T00:00:00+00:00",
            ),
        )
        connection.execute(
            """INSERT INTO services(
                service_id, environment_id, payload_json, created_at
            ) VALUES (?, ?, ?, ?)""",
            (
                "svc-0123456789abcdef01234567",
                "env-0123456789abcdef01234567",
                json.dumps({
                    "schema_version": "ecomsre.product.service-identity.v1",
                    "service_id": "svc-0123456789abcdef01234567",
                    "logical_service": "payment",
                    "aliases": {
                        "prometheus": [],
                        "opensearch": [],
                        "jaeger": [],
                        "http_health": [],
                    },
                }),
                "2026-08-27T00:00:00+00:00",
            ),
        )
        connection.commit()
    finally:
        connection.close()

    store = SqliteStoreV1(database_path)
    identity_map = ServiceCatalogRepositoryV1(store).get_map(
        "env-0123456789abcdef01234567"
    )
    assert tuple(item.logical_service for item in identity_map.services) == ("payment",)
    with store.connect() as migrated:
        assert migrated.execute(
            "SELECT logical_service FROM services WHERE service_id = ?",
            ("svc-0123456789abcdef01234567",),
        ).fetchone()[0] == "payment"


def test_identity_policy_rejects_unapproved_many_to_one_and_alias_collisions() -> None:
    with pytest.raises(ValidationError, match="many-to-one"):
        ServiceIdentityPolicyV1.model_validate(
            {
                "services": [
                    {
                        "logical_service": "payment",
                        "aliases": {"prometheus": ["pay-a", "pay-b"]},
                    }
                ]
            }
        )


def test_environment_rejects_fixture_and_real_connector_mixture() -> None:
    with pytest.raises(ValidationError, match="cannot be mixed"):
        EnvironmentCreateV1.model_validate(
            {
                "name": "invalid-mixed-environment",
                "connector_configs": [
                    {
                        "name": "fixture",
                        "kind": "FIXTURE",
                        "settings": {"dataset": "increment-1"},
                    },
                    {
                        "name": "runtime",
                        "kind": "HTTP_HEALTH",
                        "settings": {"services": []},
                    },
                ],
            }
        )


def test_discovered_service_fanout_exceeding_twenty_fails_closed(tmp_path) -> None:
    store = SqliteStoreV1(tmp_path / "product.sqlite3")
    environment = EnvironmentRepositoryV1(store).create({"name": "bounded-discovery"})
    with pytest.raises(ProductError, match="candidate bound"):
        normalize_service_identities(
            environment=environment,
            existing=ServiceCatalogRepositoryV1(store).get_map(environment.environment_id),
            connector_health=(
                _health(
                    "prometheus",
                    ConnectorKindV1.PROMETHEUS,
                    ConnectorAvailabilityV1.AVAILABLE,
                    (EvidenceSourceV22.METRICS,),
                    tuple(sorted(f"service-{index}" for index in range(21))),
                ),
            ),
        )

    with pytest.raises(ValidationError, match="collision"):
        ServiceIdentityPolicyV1.model_validate(
            {
                "services": [
                    {
                        "logical_service": "frontend",
                        "aliases": {"jaeger": ["shared"]},
                    },
                    {
                        "logical_service": "payment",
                        "aliases": {"jaeger": ["shared"]},
                    },
                ]
            }
        )


def test_capability_matrix_is_derived_from_effective_policy_and_persisted(tmp_path) -> None:
    health = (
        _health(
            "prometheus",
            ConnectorKindV1.PROMETHEUS,
            ConnectorAvailabilityV1.AVAILABLE,
            (EvidenceSourceV22.METRICS, EvidenceSourceV22.RESOURCES),
        ),
        _health(
            "logs",
            ConnectorKindV1.OPENSEARCH,
            ConnectorAvailabilityV1.UNAVAILABLE,
            (EvidenceSourceV22.LOGS,),
            (),
        ),
        _health(
            "traces",
            ConnectorKindV1.JAEGER,
            ConnectorAvailabilityV1.AVAILABLE,
            (EvidenceSourceV22.TRACES,),
        ),
        _health(
            "runtime",
            ConnectorKindV1.HTTP_HEALTH,
            ConnectorAvailabilityV1.PARTIAL,
            (EvidenceSourceV22.RUNTIME,),
        ),
    )
    matrix = build_environment_capability_matrix(
        environment_id="env-0123456789abcdef01234567",
        logical_services=("frontend", "payment"),
        connector_health=health,
        changes_available=True,
        verified_at=datetime(2026, 8, 27, tzinfo=UTC),
    )

    by_source = {item.source: item.status for item in matrix.sources}
    assert by_source[EvidenceSourceV22.METRICS] is SourceCapabilityStatusV1.AVAILABLE
    assert by_source[EvidenceSourceV22.LOGS] is SourceCapabilityStatusV1.UNAVAILABLE
    assert by_source[EvidenceSourceV22.RUNTIME] is SourceCapabilityStatusV1.PARTIAL
    assert by_source[EvidenceSourceV22.CHANGES] is SourceCapabilityStatusV1.AVAILABLE
    runtime = next(
        item for item in matrix.sources if item.source is EvidenceSourceV22.RUNTIME
    )
    assert runtime.observable_predicates == (
        PredicateKindV22.RUNTIME_HEALTHY,
        PredicateKindV22.RUNTIME_UNHEALTHY,
    )
    assert PredicateKindV22.RUNTIME_NOT_RUNNING not in runtime.observable_predicates
    assert PredicateKindV22.RUNTIME_RESTART_PRESSURE not in runtime.observable_predicates
    by_mechanism = {item.mechanism.value: item.status for item in matrix.mechanisms}
    assert by_mechanism["CONFIGURATION_ERROR"] is MechanismCapabilityStatusV1.DIAGNOSABLE
    assert by_mechanism["CPU_SATURATION"] is MechanismCapabilityStatusV1.PARTIALLY_OBSERVABLE
    assert matrix.no_incident_eligible is False
