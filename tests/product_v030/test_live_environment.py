from pathlib import Path

from ecomsre.product.contracts import (
    OpenSearchConnectorSettingsV1,
    ServiceIdentityPolicyV1,
)
from ecomsre.product.connectors.base import ConnectorHealthResultV1
from ecomsre.product.environment.repository import EnvironmentRepositoryV1
from ecomsre.product.environment.services import ServiceCatalogRepositoryV1
from ecomsre.product.environment.verification import normalize_service_identities
from ecomsre.product.storage.sqlite_store import SqliteStoreV1

from ecomsre.product.pilot.live_knowledge_evolution_v030 import (
    build_product_v030_environment_payload,
)


ROOT = Path(__file__).resolve().parents[2]


def test_declared_only_discovery_preserves_scope_and_real_missing_coverage(tmp_path):
    store = SqliteStoreV1(tmp_path / "product.sqlite3")
    environment = EnvironmentRepositoryV1(store).create(
        {
            "name": "scoped-discovery",
            "explicit_service_catalog": ["checkout", "fraud-detection"],
            "service_identity_policy": {
                "discovery_mode": "DECLARED_ONLY",
                "services": [
                    {
                        "logical_service": "checkout",
                        "aliases": {"prometheus": ["checkoutservice"]},
                    },
                    {
                        "logical_service": "fraud-detection",
                        "aliases": {"prometheus": ["fraud-detection"]},
                    },
                ],
            },
        }
    )
    health = ConnectorHealthResultV1(
        connector_name="prometheus",
        kind="PROMETHEUS",
        status="AVAILABLE",
        capabilities=[
            {
                "source": "METRICS",
                "supports_historical_range": True,
                "supports_multi_target": True,
                "supports_service_discovery": True,
                "supports_baseline": True,
                "supports_target_complete_coverage": False,
                "maximum_window_seconds": 3600,
            }
        ],
        discovered_services=tuple(
            sorted(["checkoutservice", *[f"extra-{i:02}" for i in range(30)]])
        ),
        latency_ms=0,
    )
    identity, scoped = normalize_service_identities(
        environment=environment,
        existing=ServiceCatalogRepositoryV1(store).get_map(environment.environment_id),
        connector_health=(health,),
    )
    assert tuple(item.logical_service for item in identity.services) == (
        "checkout",
        "fraud-detection",
    )
    assert scoped[0].discovered_services == ("checkout",)
    assert "fraud-detection" not in scoped[0].discovered_services


def test_default_identity_policy_serialization_remains_unchanged():
    assert "discovery_mode" not in ServiceIdentityPolicyV1().model_dump(mode="json")


def test_v030_environment_adds_queue_query_and_observer_projection():
    payload = build_product_v030_environment_payload(
        repository_root=ROOT, runtime_authority_sha256="a" * 64
    )
    configs = {item["kind"]: item for item in payload["connector_configs"]}
    assert configs["PROMETHEUS"]["settings"]["query_templates"]["queue_lag"] == (
        'sum(kafka_consumer_group_lag_ratio{group="{service}",topic="orders"})'
    )
    assert (
        OpenSearchConnectorSettingsV1.model_validate(
            configs["OPENSEARCH"]["settings"]
        ).mode.value
        == "LEGACY_EXPLICIT_FIELDS"
    )
    assert (
        configs["OPENSEARCH"]["settings"]["message_projection_policy"]
        == "OBSERVER_SYMPTOM_V1"
    )
    assert payload["explicit_service_catalog"] == [
        "checkout",
        "fraud-detection",
        "kafka",
        "payment",
    ]
    services = payload["service_identity_policy"]["services"]
    assert [item["logical_service"] for item in services] == payload[
        "explicit_service_catalog"
    ]


def test_native_broker_metrics_never_fall_back_to_method_span_metrics():
    payload = build_product_v030_environment_payload(
        repository_root=ROOT, runtime_authority_sha256="a" * 64
    )
    templates = next(
        item["settings"]["query_templates"]
        for item in payload["connector_configs"] if item["kind"] == "PROMETHEUS"
    )
    for kind in ("request_support", "error_rate", "latency"):
        native, fallback = templates[kind].split(" or ", 1)
        assert "kafka_" in native
        assert 'service_name!="kafka"' in fallback
        assert fallback.count('service_name="{service}"') == fallback.count(
            'service_name!="kafka"'
        )
