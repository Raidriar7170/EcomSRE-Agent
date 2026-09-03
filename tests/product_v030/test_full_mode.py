from copy import deepcopy
from pathlib import Path

import pytest

from ecomsre_live_sandbox.environment import ResourceOwnershipError, SandboxDriftError
from ecomsre_live_sandbox.product_v024 import ProductV024SandboxEnvironment
from ecomsre_live_sandbox.product_v030 import (
    FULL_SERVICES_V030,
    ProductV030SandboxEnvironment,
    build_product_v030_runtime_bundle,
)


ROOT = Path(__file__).resolve().parents[2]


def test_full_mode_extends_the_owned_runtime_without_changing_upstream(tmp_path):
    bundle = build_product_v030_runtime_bundle(ROOT)
    files = bundle.environment.compose_files
    assert files.index("third_party/opentelemetry-demo/compose.full.yaml") == 1
    assert files[-1] == "config/product-v030/compose.sandbox.yaml"
    environment = ProductV030SandboxEnvironment(
        repository_root=ROOT,
        bundle=bundle,
        flagd_directory=tmp_path,
    )
    assert {"accounting", "fraud-detection", "kafka", "checkout"}.issubset(
        environment.expected_services
    )
    assert len(environment.expected_services) == 28
    assert bundle.environment.platform == "linux/arm64"
    assert (
        bundle.environment.upstream_commit == "1755859a9de82c2e5e225be68abc401a5ebf2b4f"
    )


def _resolved_fixture(environment):
    # Tests only the v0.3 delta; the parent contract has its own full fixtures.
    services = {name: {} for name in environment.expected_services}
    for name in FULL_SERVICES_V030:
        services[name] = {
            "platform": "linux/arm64",
            "pull_policy": "never",
            "container_name": f"ecomsre-live-sandbox-v1-{name}",
            "labels": {
                environment.bundle.environment.sandbox_label_key: environment.bundle.environment.sandbox_id
            },
            "image": f"ghcr.io/open-telemetry/demo:3.0.0-{name}",
        }
    services["kafka"]["environment"] = {
        "OTEL_JMX_CONFIG": "/etc/ecomsre/kafka-jmx.yml",
        "OTEL_INSTRUMENTATION_METHODS_INCLUDE": "kafka.server.KafkaApis[handleProduceRequest]",
    }
    services["kafka"]["labels"]["io.ecomsre.telemetry.jmx.sha256"] = (
        environment._compose_env()["SANDBOX_KAFKA_JMX_SHA256"]
    )
    services["kafka"]["volumes"] = [
        {
            "type": "bind",
            "source": str(ROOT / "config/product-v030/kafka-jmx.yml"),
            "target": "/etc/ecomsre/kafka-jmx.yml",
            "read_only": True,
            "bind": {"create_host_path": False},
        }
    ]
    return {"services": services}


def test_full_mode_delegates_existing_contract_without_mutating_input(
    tmp_path, monkeypatch
):
    environment = ProductV030SandboxEnvironment(
        repository_root=ROOT,
        bundle=build_product_v030_runtime_bundle(ROOT),
        flagd_directory=tmp_path,
    )
    payload = _resolved_fixture(environment)
    original = deepcopy(payload)
    forwarded = []
    monkeypatch.setattr(
        ProductV024SandboxEnvironment,
        "_verify_resolved_contract",
        lambda self, value: forwarded.append(value),
    )
    environment._verify_resolved_contract(payload)
    assert payload == original
    assert len(forwarded) == 1
    assert set(forwarded[0]["services"]) == (
        set(environment.expected_services) - set(FULL_SERVICES_V030)
    )
    assert environment.product_v024_runtime_root == ROOT / "config/product-v030"


@pytest.mark.parametrize("service_name", FULL_SERVICES_V030)
@pytest.mark.parametrize(
    "field,value",
    [
        ("platform", "linux/amd64"),
        ("pull_policy", "always"),
        ("container_name", "unowned"),
        ("labels", {}),
        ("labels", []),
        ("ports", [{"target": 9092, "published": "9092"}]),
        ("volumes", [{"type": "bind", "source": "/tmp", "target": "/host"}]),
        ("privileged", True),
        ("network_mode", "host"),
        ("image", "ghcr.io/open-telemetry/demo:latest"),
        ("image", "foreign.example/demo:3.0.0-{service}"),
    ],
)
def test_full_mode_rejects_extra_service_drift(
    tmp_path, monkeypatch, service_name, field, value
):
    environment = ProductV030SandboxEnvironment(
        repository_root=ROOT,
        bundle=build_product_v030_runtime_bundle(ROOT),
        flagd_directory=tmp_path,
    )
    payload = _resolved_fixture(environment)
    payload["services"][service_name][field] = (
        value.format(service=service_name) if isinstance(value, str) else value
    )
    forwarded = []
    monkeypatch.setattr(
        ProductV024SandboxEnvironment,
        "_verify_resolved_contract",
        lambda self, value: forwarded.append(value),
    )
    with pytest.raises(SandboxDriftError, match="isolation differs"):
        environment._verify_resolved_contract(payload)
    assert not forwarded


@pytest.mark.parametrize("foreign_kind", ["container", "network", "volume", None])
def test_full_mode_rejects_foreign_same_project_resources_before_mutation(
    tmp_path,
    monkeypatch,
    foreign_kind,
):
    environment = ProductV030SandboxEnvironment(
        repository_root=ROOT,
        bundle=build_product_v030_runtime_bundle(ROOT),
        flagd_directory=tmp_path,
    )
    commands = []

    def ids(arguments):
        commands.append(arguments)
        kind = "container" if arguments[1] == "ps" else arguments[1]
        return frozenset({"foreign-id"}) if kind == foreign_kind else frozenset()

    monkeypatch.setattr(environment, "_ids", ids)
    monkeypatch.setattr(environment, "_owned_ids", lambda kind: frozenset())
    if foreign_kind:
        with pytest.raises(ResourceOwnershipError, match="same-project"):
            environment.verify_owned_resources(require_complete=False)
    else:
        assert environment.verify_owned_resources(require_complete=False) == {
            "container": 0,
            "network": 0,
            "volume": 0,
        }
        assert len(commands) == 3
    assert all("down" not in command and "up" not in command for command in commands)
