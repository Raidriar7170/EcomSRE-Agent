from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from ecomsre.dta_v2.contracts import semantic_sha256
from ecomsre.dta_v2.telemetry_adapters import _issue_owned_read_capability
from ecomsre_live_sandbox.contracts import LocalEndpoints, ResolvedSandbox, load_bundle
from ecomsre_live_sandbox.environment import EXPECTED_SERVICES, SandboxDriftError
from ecomsre_live_sandbox.product_v024 import (
    ProductV024SandboxEnvironment,
    build_product_v024_runtime_bundle,
    validate_collector_service_v024,
)


ROOT = Path(__file__).resolve().parents[2]
RUNTIME_ROOT = ROOT / "config/product-v024/runtime"


def _collector_service() -> dict[str, object]:
    upstream = ROOT / "third_party/opentelemetry-demo/src/otel-collector"
    return {
        "volumes": [
            {
                "type": "bind",
                "source": str(upstream / name),
                "target": f"/etc/{name}",
                "read_only": True,
            }
            for name in (
                "otelcol-config.yml",
                "otelcol-config-full.yml",
                "otelcol-config-observability.yml",
                "otelcol-config-extras.yml",
            )
        ]
        + [
            {
                "type": "bind",
                "source": str(RUNTIME_ROOT / "otelcol-sandbox.yml"),
                "target": "/etc/otelcol-config-sandbox.yml",
                "read_only": True,
            },
            {
                "type": "bind",
                "source": "/var/run/docker.sock",
                "target": "/var/run/docker.sock",
                "read_only": True,
            },
        ]
    }


def test_v024_runtime_bundle_adds_only_the_authorized_overlay() -> None:
    original = load_bundle(ROOT / "config/live-telemetry-controlled-remediation-v1")
    amended = build_product_v024_runtime_bundle(ROOT)

    assert amended.model_dump(mode="json", exclude={"environment"}) == (
        original.model_dump(mode="json", exclude={"environment"})
    )
    assert amended.environment.model_dump(
        mode="json", exclude={"compose_files"}
    ) == original.environment.model_dump(mode="json", exclude={"compose_files"})
    assert amended.environment.compose_files[:3] == original.environment.compose_files
    assert amended.environment.compose_files[3] == (
        "config/product-v024/runtime/compose.sandbox.yaml"
    )


@pytest.mark.parametrize("mutation", ("source", "target", "writeable", "extra"))
def test_docker_stats_runtime_exception_is_exact_and_fail_closed(
    mutation: str,
) -> None:
    service = _collector_service()
    socket = service["volumes"][-1]  # type: ignore[index]
    if mutation == "source":
        socket["source"] = "/tmp/docker.sock"
    elif mutation == "target":
        socket["target"] = "/tmp/docker.sock"
    elif mutation == "writeable":
        socket["read_only"] = False
    else:
        service["volumes"].append(  # type: ignore[union-attr]
            {
                "type": "bind",
                "source": "/var/run",
                "target": "/host-var-run",
                "read_only": True,
            }
        )

    with pytest.raises(SandboxDriftError):
        validate_collector_service_v024(
            service,
            product_config=RUNTIME_ROOT / "otelcol-sandbox.yml",
            historical_config=(
                ROOT
                / "config/live-telemetry-controlled-remediation-v1/otelcol-sandbox.yml"
            ),
        )


def test_docker_stats_runtime_exception_normalizes_to_historical_contract() -> None:
    historical = (
        ROOT / "config/live-telemetry-controlled-remediation-v1/otelcol-sandbox.yml"
    ).resolve()

    normalized = validate_collector_service_v024(
        _collector_service(),
        product_config=RUNTIME_ROOT / "otelcol-sandbox.yml",
        historical_config=historical,
    )

    assert normalized["volumes"][-1]["source"] == str(historical)
    assert normalized["volumes"][-1]["target"] == "/etc/otelcol-config-sandbox.yml"
    assert all(
        volume["source"] != "/var/run/docker.sock"
        for volume in normalized["volumes"]
    )


def test_collector_config_enables_one_bounded_owned_docker_stats_receiver() -> None:
    payload = yaml.safe_load(
        (RUNTIME_ROOT / "otelcol-sandbox.yml").read_text(encoding="utf-8")
    )

    receiver = payload["receivers"]["docker_stats"]
    assert receiver == {
        "endpoint": "unix:///var/run/docker.sock",
        "api_version": "1.44",
        "collection_interval": "2s",
        "container_labels_to_metric_labels": {
            "com.docker.compose.project": "compose_project",
            "com.docker.compose.service": "compose_service",
            "io.ecomsre.sandbox.id": "sandbox_id",
        },
    }
    metrics_receivers = payload["service"]["pipelines"]["metrics"]["receivers"]
    assert metrics_receivers.count("docker_stats") == 1
    assert "host_metrics" not in metrics_receivers


def test_v024_environment_can_mint_fresh_owned_read_authority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle = build_product_v024_runtime_bundle(ROOT)
    environment = ProductV024SandboxEnvironment(
        repository_root=ROOT,
        bundle=bundle,
        flagd_directory=tmp_path,
    )
    resolved = ResolvedSandbox(
        compose_sha256="a" * 64,
        services=EXPECTED_SERVICES,
        image_references=("example.invalid/image:1",),
        endpoints=LocalEndpoints(
            frontend="http://127.0.0.1:18080",
            flag_control="http://127.0.0.1:18080/feature/api",
            flag_evaluation="http://127.0.0.1:18016",
            prometheus="http://127.0.0.1:19090",
            opensearch="http://127.0.0.1:19200",
            jaeger="http://127.0.0.1:11686",
        ),
    )
    monkeypatch.setattr(
        environment,
        "verify_local_docker",
        lambda: {
            "context": "desktop-linux",
            "endpoint": "unix:///private/docker.sock",
            "daemon_id": "daemon-id",
        },
    )
    monkeypatch.setattr(environment, "resolve", lambda: (resolved, {}))

    capability = _issue_owned_read_capability(
        environment=environment,
        bundle=bundle,
        admitted_resolved_sha256=semantic_sha256(resolved.model_dump(mode="json")),
        timeout_seconds=5.0,
    )

    assert capability.resolved_sandbox == resolved
    assert capability.config.authority.mode.value == "OWNED_LOCAL"


def test_collector_validator_rejects_writeable_product_config() -> None:
    service = deepcopy(_collector_service())
    service["volumes"][-2]["read_only"] = False  # type: ignore[index]

    with pytest.raises(SandboxDriftError):
        validate_collector_service_v024(
            service,
            product_config=RUNTIME_ROOT / "otelcol-sandbox.yml",
            historical_config=(
                ROOT
                / "config/live-telemetry-controlled-remediation-v1/otelcol-sandbox.yml"
            ),
        )
