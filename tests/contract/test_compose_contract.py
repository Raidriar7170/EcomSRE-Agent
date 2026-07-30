import importlib
import json
import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from ecomsre.environment.ownership import (
    PROJECT_LABEL,
    PROJECT_NAMESPACE,
    RUN_LABEL,
)


ROOT = Path(__file__).resolve().parents[2]
OVERRIDE = ROOT / "config" / "phase0" / "compose.phase0.yaml"
RUN_ID = "a" * 32
DOCKER_ENDPOINT = "unix:///var/run/docker.sock"

CORE_SERVICES = {
    "ad",
    "cart",
    "checkout",
    "currency",
    "email",
    "frontend",
    "frontend-proxy",
    "image-provider",
    "load-generator",
    "payment",
    "product-catalog",
    "quote",
    "recommendation",
    "shipping",
    "flagd",
    "flagd-ui",
    "telemetry-docs",
    "astronomy-db",
    "valkey-cart",
    "otel-collector",
}
OBSERVABILITY_SERVICES = {
    "jaeger",
    "grafana",
    "prometheus",
    "opensearch",
    "opamp-server",
}
HOST_PORTS = {
    "frontend-proxy": (8080, 8080),
    "prometheus": (9090, 9090),
    "jaeger": (16686, 16686),
    "opensearch": (9200, 9200),
    "flagd": (8016, 8016),
}


def _lifecycle_module():
    try:
        return importlib.import_module("ecomsre.environment.lifecycle")
    except ModuleNotFoundError:
        pytest.fail("owned Compose lifecycle is not implemented")


def test_phase0_override_namespaces_and_labels_every_actual_upstream_service() -> None:
    text = OVERRIDE.read_text(encoding="utf-8")
    expected_services = CORE_SERVICES | OBSERVABILITY_SERVICES

    assert "x-phase0-service:" in text
    assert "platform: linux/arm64" in text
    assert "pull_policy: never" in text
    assert f"{PROJECT_LABEL}: {PROJECT_NAMESPACE}" in text
    assert f"{RUN_LABEL}: ${{ECOMSRE_RUN_ID:?ECOMSRE_RUN_ID is required}}" in text
    assert re.search(r"(?m)^    name: ecomsre-phase0$", text)

    declared = set(
        re.findall(r"(?m)^  ([a-z0-9][a-z0-9-]*):\n    <<: \*phase0-service$", text)
    )
    assert declared == expected_services
    for service in expected_services:
        assert f"container_name: ecomsre-phase0-{service}" in text

    assert "compose.full.yaml" not in text
    assert "compose.extras.yaml" not in text
    assert "compose.profiling.yaml" not in text
    assert "linux/amd64" not in text
    assert "latest" not in text


def test_phase0_override_clears_inherited_ports_and_publishes_only_loopback() -> None:
    text = OVERRIDE.read_text(encoding="utf-8")
    anchor = text.split("networks:", 1)[0]

    assert "ports: !override []" in anchor
    for service, (published, target) in HOST_PORTS.items():
        block = re.search(
            rf"(?ms)^  {re.escape(service)}:\n"
            rf"    <<: \*phase0-service\n"
            rf"(.*?)(?=^  [a-z0-9][a-z0-9-]*:|\Z)",
            text,
        )
        assert block is not None
        payload = block.group(1)
        assert "ports: !override" in payload
        assert 'host_ip: "127.0.0.1"' in payload
        assert f'published: "{published}"' in payload
        assert f"target: {target}" in payload
    assert "0.0.0.0" not in text
    assert 'host_ip: "::"' not in text


def test_compose_invocations_use_only_frozen_layers_and_tuple_arguments() -> None:
    lifecycle = _lifecycle_module()

    up = lifecycle.build_compose_invocation(
        lifecycle.ComposeAction.UP,
        project_root=ROOT,
        run_id=RUN_ID,
        docker_endpoint=DOCKER_ENDPOINT,
    )
    config = lifecycle.build_compose_invocation(
        lifecycle.ComposeAction.CONFIG,
        project_root=ROOT,
        run_id=RUN_ID,
        docker_endpoint=DOCKER_ENDPOINT,
    )
    down = lifecycle.build_compose_invocation(
        lifecycle.ComposeAction.DOWN,
        project_root=ROOT,
        run_id=RUN_ID,
        docker_endpoint=DOCKER_ENDPOINT,
    )

    assert isinstance(up.arguments, tuple)
    assert up.environment == {"ECOMSRE_RUN_ID": RUN_ID}
    assert up.arguments[:4] == (
        "docker",
        "--host",
        DOCKER_ENDPOINT,
        "compose",
    )
    assert _option_values(up.arguments, "--project-name") == [PROJECT_NAMESPACE]
    assert _option_values(up.arguments, "--file") == [
        str(ROOT / "third_party" / "opentelemetry-demo" / "compose.yaml"),
        str(ROOT / "third_party" / "opentelemetry-demo" / "compose.observability.yaml"),
        str(OVERRIDE),
    ]
    assert up.arguments[-5:] == (
        "up",
        "--detach",
        "--pull",
        "never",
        "--no-build",
    )
    assert config.arguments[-3:] == ("config", "--format", "json")
    assert down.arguments[-1:] == ("down",)
    assert "--remove-orphans" not in down.arguments
    assert "full" not in " ".join(up.arguments)
    assert "extras" not in " ".join(up.arguments)
    assert "profiling" not in " ".join(up.arguments)


def test_resolved_compose_port_plan_is_minimal_and_loopback_only() -> None:
    from ecomsre.environment.manifests import ResolvedComposeConfig

    lifecycle = _lifecycle_module()
    safe = ResolvedComposeConfig.from_stdout(
        json.dumps(
            {
                "services": {
                    "frontend-proxy": {
                        "container_name": "ecomsre-phase0-frontend-proxy",
                        "image": "otel/demo:3.0.0-frontend-proxy",
                        "ports": [
                            {
                                "target": 8080,
                                "published": "8080",
                                "host_ip": "127.0.0.1",
                                "protocol": "tcp",
                            }
                        ],
                    },
                    "ad": {
                        "container_name": "ecomsre-phase0-ad",
                        "image": "otel/demo:3.0.0-ad",
                    },
                }
            },
            sort_keys=True,
        )
    )
    wildcard = ResolvedComposeConfig.from_stdout(
        safe.stdout.replace('"127.0.0.1"', '"0.0.0.0"')
    )

    bindings = lifecycle.parse_expected_port_bindings(safe)

    assert len(bindings) == 1
    assert bindings[0].host_ip == "127.0.0.1"
    with pytest.raises(ValueError, match="loopback"):
        lifecycle.parse_expected_port_bindings(wildcard)


def test_resolved_compose_rejects_host_port_for_unallowlisted_service() -> None:
    from ecomsre.environment.manifests import ResolvedComposeConfig

    lifecycle = _lifecycle_module()
    resolved = ResolvedComposeConfig.from_stdout(
        json.dumps(
            {
                "services": {
                    "ad": {
                        "container_name": "ecomsre-phase0-ad",
                        "image": "otel/demo:3.0.0-ad",
                        "ports": [
                            {
                                "target": 9555,
                                "published": "9555",
                                "host_ip": "127.0.0.1",
                                "protocol": "tcp",
                            }
                        ],
                    }
                }
            },
            sort_keys=True,
        )
    )

    with pytest.raises(ValueError, match="allowlist"):
        lifecycle.parse_expected_port_bindings(resolved)


@pytest.mark.parametrize(
    "endpoint",
    ["", "tcp://127.0.0.1:2375", "ssh://docker@example.test"],
)
def test_lifecycle_builder_rejects_non_local_daemon_endpoint(
    endpoint: str,
) -> None:
    lifecycle = _lifecycle_module()

    with pytest.raises(ValueError, match="local Unix socket"):
        lifecycle.build_compose_invocation(
            lifecycle.ComposeAction.UP,
            project_root=ROOT,
            run_id=RUN_ID,
            docker_endpoint=endpoint,
        )


def test_context_based_compose_argv_cannot_bypass_host_capability() -> None:
    lifecycle = _lifecycle_module()
    host_bound = lifecycle.build_compose_invocation(
        lifecycle.ComposeAction.UP,
        project_root=ROOT,
        run_id=RUN_ID,
        docker_endpoint=DOCKER_ENDPOINT,
    )
    mutable_context_arguments = (
        "docker",
        "--context",
        "desktop-linux",
        *host_bound.arguments[3:],
    )

    with pytest.raises(ValidationError, match="allowlisted"):
        lifecycle.ComposeInvocation(
            purpose="up",
            arguments=mutable_context_arguments,
            environment={"ECOMSRE_RUN_ID": RUN_ID},
            timeout_seconds=300,
            read_only=False,
        )


def test_compose_override_freezes_demo_images_away_from_upstream_latest_variable() -> (
    None
):
    text = OVERRIDE.read_text(encoding="utf-8")

    demo_services = {
        service
        for service in CORE_SERVICES | OBSERVABILITY_SERVICES
        if service
        not in {
            "flagd",
            "astronomy-db",
            "valkey-cart",
            "otel-collector",
            "jaeger",
            "grafana",
            "prometheus",
        }
    }
    for service in demo_services:
        block = re.search(
            rf"(?ms)^  {re.escape(service)}:\n"
            rf"    <<: \*phase0-service\n"
            rf"(.*?)(?=^  [a-z0-9][a-z0-9-]*:|\Z)",
            text,
        )
        assert block is not None
        assert f"image: ${{IMAGE_NAME}}:${{IMAGE_VERSION}}-{service}" in block.group(1)


@pytest.mark.parametrize(
    ("purpose", "arguments"),
    [
        ("owned_containers", ("sh", "-c", "docker compose down")),
        ("owned_containers", ("docker", "container", "ls", "--all")),
        ("up", ("docker", "compose", "up", "--detach")),
        ("inspect_image", ("docker", "image", "inspect")),
        (
            "inspect_image",
            ("docker", "image", "inspect", "allowed:1", "extra:2"),
        ),
    ],
)
def test_every_lifecycle_invocation_purpose_rejects_non_allowlisted_argv(
    purpose: str,
    arguments: tuple[str, ...],
) -> None:
    lifecycle = _lifecycle_module()

    with pytest.raises(ValidationError, match="allowlisted"):
        lifecycle.ComposeInvocation(
            purpose=purpose,
            arguments=arguments,
            environment={"ECOMSRE_RUN_ID": RUN_ID},
            timeout_seconds=30,
            read_only=True,
        )


def test_compose_invocation_rejects_non_frozen_upstream_directory_shape() -> None:
    lifecycle = _lifecycle_module()

    with pytest.raises(ValidationError, match="allowlisted"):
        lifecycle.ComposeInvocation(
            purpose="down",
            arguments=(
                "docker",
                "compose",
                "--project-name",
                PROJECT_NAMESPACE,
                "--project-directory",
                "/tmp/not-the-frozen-upstream",
                "--env-file",
                "/tmp/not-the-frozen-upstream/.env",
                "--file",
                "/tmp/not-the-frozen-upstream/compose.yaml",
                "--file",
                "/tmp/not-the-frozen-upstream/compose.observability.yaml",
                "--file",
                "/config/phase0/compose.phase0.yaml",
                "down",
            ),
            environment={"ECOMSRE_RUN_ID": RUN_ID},
            timeout_seconds=30,
            read_only=False,
        )


def _option_values(arguments: tuple[str, ...], option: str) -> list[str]:
    return [
        arguments[index + 1]
        for index, value in enumerate(arguments[:-1])
        if value == option
    ]
