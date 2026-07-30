import importlib
import json
import re
import subprocess
import time
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from ecomsre.environment.manifests import (
    ImageLockManifest,
    ImageLockStatus,
    InspectedImage,
    LockMatchChecks,
    LockVerification,
    ResolvedComposeConfig,
    generate_candidate_image_lock,
)
from ecomsre.environment.ownership import (
    PROJECT_LABEL,
    PROJECT_NAMESPACE,
    RUN_LABEL,
    OwnedResource,
    OwnershipManifest,
)
from ecomsre.environment.ownership_authority import (
    OwnershipAuthorityError,
    create_ownership_authority_artifacts,
    load_authenticated_ownership_context,
)
from ecomsre.environment.preflight import (
    CommandResult,
    DockerSnapshot,
    HostSnapshot,
    OwnershipProof,
    PortObservation,
    PreflightInputs,
    PreflightResult,
    ResourceObservation,
    issue_authenticated_preflight_evidence,
)
from ecomsre.evidence.hashes import canonical_json_sha256
from ecomsre.phase0.models import Outcome


ROOT = Path(__file__).resolve().parents[2]
RUN_ID = "a" * 32
CREATED_AT = datetime(2026, 7, 30, 8, 0, tzinfo=UTC)
DOCKER_ENDPOINT = "unix:///var/run/docker.sock"
DOCKER_DAEMON_ID = "fixture-daemon-id"
COMPOSE_PROJECT_LABEL = "com.docker.compose.project"
COMPOSE_SERVICE_LABEL = "com.docker.compose.service"

DEMO_SERVICES = (
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
    "flagd-ui",
    "telemetry-docs",
    "opensearch",
    "opamp-server",
)
EXTERNAL_SERVICE_IMAGES = {
    "flagd": "ghcr.io/open-feature/flagd:v0.16.0",
    "otel-collector": (
        "ghcr.io/open-telemetry/opentelemetry-collector-releases/"
        "opentelemetry-collector-contrib:0.157.0"
    ),
    "grafana": "grafana/grafana:13.1.0",
    "jaeger": "quay.io/jaegertracing/jaeger:2.19.0",
    "astronomy-db": "postgres:18.4",
    "prometheus": "quay.io/prometheus/prometheus:v3.13.1",
    "valkey-cart": "ghcr.io/valkey-io/valkey:9.0.4-alpine3.23",
}
SERVICE_IMAGES = {
    **{
        service: f"ghcr.io/open-telemetry/demo:3.0.0-{service}"
        for service in DEMO_SERVICES
    },
    **EXTERNAL_SERVICE_IMAGES,
}
HOST_PORT = 18080
RUNTIME_PORT = 32768
CONFIG_STDOUT = json.dumps(
    {
        "services": {
            service: {
                "container_name": f"ecomsre-phase0-{service}",
                "image": source,
                **(
                    {
                        "ports": [
                            {
                                "host_ip": "0.0.0.0",
                                "mode": "ingress",
                                "protocol": "tcp",
                                "published": str(HOST_PORT),
                                "target": 8080,
                            }
                        ]
                    }
                    if service == "frontend-proxy"
                    else {}
                ),
            }
            for service, source in SERVICE_IMAGES.items()
        }
    },
    sort_keys=True,
)
TARGET_ONLY_CONFIG_STDOUT = json.dumps(
    {
        "services": {
            service: {
                "container_name": f"ecomsre-phase0-{service}",
                "image": source,
                **(
                    {
                        "ports": [
                            {
                                "protocol": "tcp",
                                "target": 9555,
                            }
                        ]
                    }
                    if service == "ad"
                    else {}
                ),
            }
            for service, source in SERVICE_IMAGES.items()
        }
    },
    sort_keys=True,
)
REAL_UPSTREAM_TARGET_ONLY_PORTS = {
    "ad": (9555, 9465),
    "cart": (7070,),
    "checkout": (5050,),
    "currency": (7001,),
    "email": (6060,),
    "frontend": (8080,),
    "image-provider": (8081,),
    "payment": (50051,),
    "product-catalog": (3550,),
    "quote": (8090,),
    "recommendation": (9001,),
    "shipping": (50050,),
    "flagd": (8013, 8016),
    "flagd-ui": (4000,),
    "telemetry-docs": (8000,),
    "astronomy-db": (5432,),
    "valkey-cart": (6379,),
    "otel-collector": (4317, 4318),
    "jaeger": (16686, 4317),
    "grafana": (3000,),
    "opensearch": (9200,),
}
REAL_UPSTREAM_RESOLVED_CONFIG_STDOUT = json.dumps(
    {
        "services": {
            service: {
                "container_name": f"ecomsre-phase0-{service}",
                "image": source,
                **(
                    {
                        "ports": [
                            {
                                "protocol": "tcp",
                                "target": target,
                            }
                            for target in REAL_UPSTREAM_TARGET_ONLY_PORTS.get(
                                service,
                                (),
                            )
                        ]
                    }
                    if service in REAL_UPSTREAM_TARGET_ONLY_PORTS
                    else {}
                ),
            }
            for service, source in SERVICE_IMAGES.items()
        }
    },
    sort_keys=True,
)
IMAGE_SOURCES = ResolvedComposeConfig.from_stdout(CONFIG_STDOUT).image_references
NETWORK_ID = "d" * 64
FRONTEND_PROXY_ID = f"{sorted(SERVICE_IMAGES).index('frontend-proxy') + 1:064x}"


class FixtureRunner:
    def __init__(self) -> None:
        self.results: dict[tuple[str, ...], list[CommandResult]] = {}
        self.errors: dict[tuple[str, ...], list[BaseException]] = {}
        self.calls: list[tuple[tuple[str, ...], dict[str, str]]] = []

    def respond(
        self,
        arguments: tuple[str, ...],
        *,
        exit_code: int = 0,
        stdout: str = "",
        stderr: str = "",
    ) -> None:
        self.results.setdefault(arguments, []).append(
            CommandResult(
                arguments=arguments,
                exit_code=exit_code,
                stdout=stdout,
                stderr=stderr,
            )
        )

    def run(
        self,
        arguments: tuple[str, ...],
        *,
        timeout_seconds: float,
        environment: dict[str, str] | None = None,
    ) -> CommandResult:
        assert timeout_seconds > 0
        self.calls.append((arguments, dict(environment or {})))
        errors = self.errors.get(arguments, [])
        if errors:
            raise errors.pop(0)
        return self.results[arguments].pop(0)

    def fail(
        self,
        arguments: tuple[str, ...],
        error: BaseException,
    ) -> None:
        self.errors.setdefault(arguments, []).append(error)


def _lifecycle_module():
    try:
        return importlib.import_module("ecomsre.environment.lifecycle")
    except ModuleNotFoundError:
        pytest.fail("owned Compose lifecycle is not implemented")


def _labels(
    run_id: str = RUN_ID,
    *,
    service: str | None = None,
) -> dict[str, str]:
    labels = {
        COMPOSE_PROJECT_LABEL: PROJECT_NAMESPACE,
        PROJECT_LABEL: PROJECT_NAMESPACE,
        RUN_LABEL: run_id,
    }
    if service is not None:
        labels[COMPOSE_SERVICE_LABEL] = service
    return labels


def _resources(run_id: str = RUN_ID) -> tuple[OwnedResource, ...]:
    containers = tuple(
        OwnedResource(
            kind="container",
            name=f"ecomsre-phase0-{service}",
            resource_id=f"{index + 1:064x}",
            labels=_labels(run_id, service=service),
            identity_evidence=(
                f"container:{index + 1:064x}",
                f"container_name:ecomsre-phase0-{service}",
                f"service:{service}",
            ),
        )
        for index, service in enumerate(sorted(SERVICE_IMAGES))
    )
    port_id = "port-binding:" + canonical_json_sha256(
        {
            "service": "frontend-proxy",
            "container_name": "ecomsre-phase0-frontend-proxy",
            "container_id": FRONTEND_PROXY_ID,
            "host_ip": "0.0.0.0",
            "host_family": "ipv4",
            "published_port": HOST_PORT,
            "target_port": 8080,
            "protocol": "tcp",
        }
    )
    resources = containers + (
        OwnedResource(
            kind="network",
            name=PROJECT_NAMESPACE,
            resource_id=NETWORK_ID,
            labels=_labels(run_id),
            identity_evidence=(f"network:{NETWORK_ID}",),
        ),
        OwnedResource(
            kind="port",
            name=f"frontend-proxy:8080->{HOST_PORT}/tcp@ipv4",
            resource_id=port_id,
            labels=_labels(run_id, service="frontend-proxy"),
            identity_evidence=(
                f"port:{port_id}",
                f"container:{FRONTEND_PROXY_ID}",
                "container_name:ecomsre-phase0-frontend-proxy",
                "service:frontend-proxy",
                "host_ip:0.0.0.0",
                "host_family:ipv4",
                f"published_port:{HOST_PORT}",
                "target_port:8080",
                "protocol:tcp",
                f"binding:0.0.0.0:{HOST_PORT}->8080/tcp",
                f"raw_binding:0.0.0.0:{HOST_PORT}->8080/tcp",
            ),
        ),
    )
    return tuple(
        sorted(
            resources,
            key=lambda resource: (
                resource.kind,
                resource.name,
                resource.resource_id,
            ),
        )
    )


def _context(tmp_path: Path):
    create_ownership_authority_artifacts(
        tmp_path,
        OwnershipManifest(run_id=RUN_ID, resources=_resources()),
        created_at=CREATED_AT,
    )
    return load_authenticated_ownership_context(tmp_path, RUN_ID)


def _preflight_evidence(
    *,
    context=None,
    image_lock: ImageLockManifest | None = None,
    stale: bool = False,
    docker_overrides: dict[str, object] | None = None,
):
    finished_ns = time.monotonic_ns()
    collected_at = datetime.now(UTC)
    if stale:
        finished_ns -= 300_000_000_000
        collected_at -= timedelta(minutes=5)
    lock = image_lock or _locked_manifest()
    if context is None:
        ports = (
            PortObservation(
                port=HOST_PORT,
                occupied=False,
                ownership="NONE",
            ),
        )
        resources = ()
    else:
        port_resources = tuple(
            resource
            for resource in context.manifest.resources
            if resource.kind == "port"
        )
        ports = tuple(
            PortObservation(
                port=int(
                    next(
                        value.removeprefix("published_port:")
                        for value in resource.identity_evidence
                        if value.startswith("published_port:")
                    )
                ),
                occupied=True,
                ownership="OWNED",
                ownership_proof=OwnershipProof(
                    project_namespace=PROJECT_NAMESPACE,
                    manifest_sha256=context.manifest_sha256,
                    run_id=context.run_id,
                    resource_kind="port",
                    resource_id=resource.resource_id,
                    port=int(
                        next(
                            value.removeprefix("published_port:")
                            for value in resource.identity_evidence
                            if value.startswith("published_port:")
                        )
                    ),
                    identifiers=resource.identity_evidence,
                ),
            )
            for resource in port_resources
        )
        resources = tuple(
            ResourceObservation(
                kind=resource.kind,
                name=resource.name,
                resource_id=resource.resource_id,
                labels=resource.labels,
                present=True,
                ownership="OWNED",
                ownership_proof=OwnershipProof(
                    project_namespace=PROJECT_NAMESPACE,
                    manifest_sha256=context.manifest_sha256,
                    run_id=context.run_id,
                    resource_kind=resource.kind,
                    resource_id=resource.resource_id,
                    identifiers=resource.identity_evidence,
                ),
            )
            for resource in context.manifest.resources
            if resource.kind != "port"
        )
    docker = DockerSnapshot(
        client_available=True,
        client_version="29.6.1",
        daemon_available=True,
        server_version="29.6.1",
        desktop_version="4.50.0",
        engine="Docker Desktop",
        desktop_identity_verified=True,
        compose_available=True,
        compose_version="v5.3.0",
        compose_plugin_v2=True,
        server_os_type="linux",
        server_architecture="arm64",
        native_platform="linux/arm64",
        cpu_count=12,
        memory_bytes=24 * 1024**3,
        disk_bytes=100 * 1024**3,
        resource_fields_verified=True,
        context_name="desktop-linux",
        endpoint=DOCKER_ENDPOINT,
        daemon_id=DOCKER_DAEMON_ID,
    )
    if docker_overrides:
        docker = docker.model_copy(update=docker_overrides)
    inputs = PreflightInputs(
        host=HostSnapshot(
            macos_version="26.5.2",
            macos_build="25F84",
            architecture="arm64",
            cpu_model="Apple M5 Pro",
            cpu_count=12,
            total_memory_bytes=48 * 1024**3,
            available_memory_bytes=32 * 1024**3,
            available_disk_bytes=679 * 1024**3,
        ),
        docker=docker,
        ports=ports,
        resources=resources,
        ownership_context=context,
        observed_upstream_commit=lock.upstream_commit,
        observed_compose_config_sha256=lock.compose_config_sha256 or "",
        expected_compose_config_sha256=lock.compose_config_sha256 or "",
        image_lock_verification=LockVerification(
            passed=True,
            outcome=Outcome.SUCCESS,
            reason_codes=(),
            checks=LockMatchChecks.all_passed(),
        ),
        pull_policy="never",
    )
    return issue_authenticated_preflight_evidence(
        run_id=RUN_ID,
        inputs=inputs,
        collected_at=collected_at,
        monotonic_started_ns=finished_ns - 1_000,
        monotonic_finished_ns=finished_ns,
    )


def _image(index: int, source: str) -> InspectedImage:
    tag = source.rsplit("/", 1)[-1].split(":", 1)[1]
    return InspectedImage(
        logical_name=tag,
        source_reference=source,
        image_index_digest="sha256:" + f"{index + 1:064x}",
        resolved_platform_digest="sha256:" + f"{index + 101:064x}",
        architecture="arm64",
        platform="linux/arm64",
        image_id="sha256:" + f"{index + 201:064x}",
    )


def _locked_manifest(
    config_stdout: str = CONFIG_STDOUT,
) -> ImageLockManifest:
    return generate_candidate_image_lock(
        images=tuple(
            _image(index, source) for index, source in enumerate(IMAGE_SOURCES)
        ),
        resolved_compose=ResolvedComposeConfig.from_stdout(config_stdout),
        acquired_at=CREATED_AT,
    )


def _uninitialized_manifest() -> ImageLockManifest:
    return ImageLockManifest(
        schema_version="phase0.image-lock.v1",
        status=ImageLockStatus.UNINITIALIZED,
        upstream_tag="3.0.0",
        upstream_commit="1755859a9de82c2e5e225be68abc401a5ebf2b4f",
        compose_config_sha256=None,
        created_at=None,
        allowed_source_references=(),
        images=(),
    )


def _inspection_stdout(index: int, source: str) -> str:
    image = _image(index, source)
    repository = source.rsplit(":", 1)[0]
    return json.dumps(
        [
            {
                "Id": image.image_id,
                "RepoTags": [source],
                "RepoDigests": [f"{repository}@{image.image_index_digest}"],
                "Descriptor": {
                    "digest": image.resolved_platform_digest,
                },
                "Architecture": "arm64",
                "Os": "linux",
            }
        ]
    )


def _register_config_and_images(
    lifecycle,
    runner: FixtureRunner,
    *,
    mismatched_index: int | None = None,
    config_stdout: str = CONFIG_STDOUT,
    image_lock: ImageLockManifest | None = None,
    revalidation_context_endpoint: str = DOCKER_ENDPOINT,
    revalidation_daemon_id: str = DOCKER_DAEMON_ID,
    revalidation_context_name: str = "desktop-linux",
    revalidation_architecture: str = "aarch64",
) -> None:
    config = lifecycle.build_compose_invocation(
        lifecycle.ComposeAction.CONFIG,
        project_root=ROOT,
        run_id=RUN_ID,
        docker_endpoint=DOCKER_ENDPOINT,
    )
    runner.respond(config.arguments, stdout=config_stdout)
    lock = image_lock or _locked_manifest(config_stdout)
    inspections = lifecycle.build_image_inspection_invocations(
        lock,
        run_id=RUN_ID,
        docker_endpoint=DOCKER_ENDPOINT,
    )
    assert len(inspections) == 25
    for index, (invocation, source) in enumerate(
        zip(inspections, IMAGE_SOURCES, strict=True)
    ):
        stdout = _inspection_stdout(index, source)
        if index == mismatched_index:
            payload = json.loads(stdout)
            payload[0]["Architecture"] = "amd64"
            stdout = json.dumps(payload)
        runner.respond(invocation.arguments, stdout=stdout)
    _register_daemon_revalidation(
        lifecycle,
        runner,
        context_endpoint=revalidation_context_endpoint,
        daemon_id=revalidation_daemon_id,
        context_name=revalidation_context_name,
        architecture=revalidation_architecture,
    )


def _register_daemon_revalidation(
    lifecycle,
    runner: FixtureRunner,
    *,
    context_endpoint: str = DOCKER_ENDPOINT,
    daemon_id: str = DOCKER_DAEMON_ID,
    context_name: str = "desktop-linux",
    architecture: str = "aarch64",
) -> tuple:
    invocations = lifecycle.build_daemon_revalidation_invocations(
        run_id=RUN_ID,
        docker_endpoint=DOCKER_ENDPOINT,
    )
    runner.respond(
        invocations[0].arguments,
        stdout=json.dumps(
            {
                "Name": context_name,
                "Endpoints": {
                    "docker": {
                        "Host": context_endpoint,
                    }
                },
            }
        ),
    )
    runner.respond(
        invocations[1].arguments,
        stdout=json.dumps(
            {
                "ID": daemon_id,
                "OSType": "linux",
                "Architecture": architecture,
            }
        ),
    )
    return invocations


def _records_stdout(
    resources: tuple[OwnedResource, ...],
    kind: str,
    *,
    include_ports: bool = True,
    port_mode: str | None = None,
    port_owner_service: str = "frontend-proxy",
    published_port: int = HOST_PORT,
    target_port: int = 8080,
    dual_stack: bool = False,
) -> str:
    records = []
    for resource in resources:
        if resource.kind != kind:
            continue
        records.append(
            record := {
                "ID": resource.resource_id,
                "Names" if kind == "container" else "Name": resource.name,
                "Labels": ",".join(
                    f"{key}={value}" for key, value in resource.labels.items()
                ),
            }
        )
        if (
            include_ports
            and kind == "container"
            and resource.name
            == (
                "ecomsre-phase0-ad"
                if port_mode == "owner"
                else f"ecomsre-phase0-{port_owner_service}"
            )
        ):
            if port_mode == "host_ip":
                host_ip = "127.0.0.1"
            elif port_mode == "ipv6_only":
                host_ip = "[::]"
            else:
                host_ip = "0.0.0.0"
            actual_published_port = (
                published_port + 1 if port_mode == "published" else published_port
            )
            actual_target_port = (
                target_port + 1 if port_mode == "target" else target_port
            )
            protocol = "udp" if port_mode == "protocol" else "tcp"
            binding = (
                f"{host_ip}:{actual_published_port}->{actual_target_port}/{protocol}"
            )
            if port_mode == "duplicate":
                record["Ports"] = f"{binding}, {binding}"
            elif port_mode == "ambiguous_published":
                record["Ports"] = (
                    f"{binding}, [::]:{actual_published_port + 1}"
                    f"->{actual_target_port}/{protocol}"
                )
            elif port_mode == "non_equivalent_hosts":
                record["Ports"] = (
                    f"{binding}, 127.0.0.1:{actual_published_port}"
                    f"->{actual_target_port}/{protocol}"
                )
            elif port_mode == "unknown_arrow":
                record["Ports"] = f"{binding}, unknown:9999->unparsed"
            elif dual_stack:
                record["Ports"] = (
                    f"{binding}, [::]:{actual_published_port}"
                    f"->{actual_target_port}/{protocol}"
                )
            else:
                record["Ports"] = binding
    return "".join(json.dumps(record) + "\n" for record in records)


def _register_discovery(
    lifecycle,
    runner: FixtureRunner,
    *,
    potential: tuple[OwnedResource, ...] | None = None,
    owned: tuple[OwnedResource, ...] | None = None,
    include_ports: bool = True,
    port_mode: str | None = None,
    port_owner_service: str = "frontend-proxy",
    published_port: int = HOST_PORT,
    target_port: int = 8080,
    dual_stack: bool = False,
) -> tuple:
    potential = _resources() if potential is None else potential
    owned = _resources() if owned is None else owned
    invocations = lifecycle.build_ownership_discovery_invocations(
        project_root=ROOT,
        run_id=RUN_ID,
        docker_endpoint=DOCKER_ENDPOINT,
    )
    assert len(invocations) == 6
    for invocation in invocations:
        scope, kind_plural = invocation.purpose.split("_", 1)
        kind = kind_plural.removesuffix("s")
        selected = potential if scope == "potential" else owned
        runner.respond(
            invocation.arguments,
            stdout=_records_stdout(
                selected,
                kind,
                include_ports=include_ports,
                port_mode=port_mode,
                port_owner_service=port_owner_service,
                published_port=published_port,
                target_port=target_port,
                dual_stack=dual_stack,
            ),
        )
    return invocations


def _prepare_successful_up(lifecycle, runner: FixtureRunner) -> tuple:
    _register_config_and_images(lifecycle, runner)
    _register_discovery(
        lifecycle,
        runner,
        potential=(),
        owned=(),
    )
    up = lifecycle.build_compose_invocation(
        lifecycle.ComposeAction.UP,
        project_root=ROOT,
        run_id=RUN_ID,
        docker_endpoint=DOCKER_ENDPOINT,
    )
    runner.respond(up.arguments)
    discovery = _register_discovery(lifecycle, runner)
    return up, discovery


def test_fresh_up_closes_intent_image_and_post_start_ownership_artifacts(
    tmp_path: Path,
) -> None:
    lifecycle = _lifecycle_module()
    runner = FixtureRunner()
    up, discovery = _prepare_successful_up(lifecycle, runner)

    execution = lifecycle.up_environment(
        runner,
        context=None,
        preflight_evidence=_preflight_evidence(),
        image_lock=_locked_manifest(),
        project_root=ROOT,
        artifacts_root=tmp_path,
    )

    assert execution.result.outcome is Outcome.SUCCESS
    assert execution.result.exit_code == 0
    assert execution.ownership_context is not None
    assert execution.ownership_context.is_authentic()
    assert execution.ownership_context.manifest.resources == _resources()
    assert any(
        resource.kind == "port" and resource.resource_id.startswith("port-binding:")
        for resource in execution.ownership_context.manifest.resources
    )
    assert execution.artifact_paths is not None
    assert execution.artifact_paths.ownership_intent.is_file()
    assert execution.artifact_paths.ownership_manifest.is_file()
    assert execution.artifact_paths.resolved_compose.is_file()
    assert execution.artifact_paths.command_log.is_file()
    intent = json.loads(
        execution.artifact_paths.ownership_intent.read_text(encoding="utf-8")
    )
    assert intent["resources"] == []
    assert intent["expected_compose_sha256"] == _locked_manifest().compose_config_sha256
    resolved = json.loads(
        execution.artifact_paths.resolved_compose.read_text(encoding="utf-8")
    )
    assert resolved["sanitized_config"] == json.loads(CONFIG_STDOUT)
    assert resolved["sha256"] == _locked_manifest().compose_config_sha256
    assert set(resolved["image_sources"]) == set(IMAGE_SOURCES)
    assert resolved["pull_policy"] == "never"
    assert resolved["build_policy"] == "no-build"
    command_log = json.loads(
        execution.artifact_paths.command_log.read_text(encoding="utf-8")
    )
    assert any(record["arguments"][-1:] == ["--no-build"] for record in command_log)

    inspect_calls = [
        arguments
        for arguments, _environment in runner.calls
        if arguments[:5] == ("docker", "--host", DOCKER_ENDPOINT, "image", "inspect")
    ]
    assert len(inspect_calls) == 25
    assert {arguments[5] for arguments in inspect_calls} == set(IMAGE_SOURCES)
    assert [call[0] for call in runner.calls][-7:] == [
        up.arguments,
        *(invocation.arguments for invocation in discovery),
    ]


def test_success_up_keeps_raw_resolved_compose_only_in_evaluator_evidence(
    tmp_path: Path,
) -> None:
    lifecycle = _lifecycle_module()
    runner = FixtureRunner()
    leaking_payload = json.loads(CONFIG_STDOUT)
    leaking_payload["services"]["flagd"].update(
        {
            "command": [
                "start",
                "--uri",
                "file:./etc/flagd/demo.flagd.json",
            ],
            "environment": {
                "PHYSICAL_FLAG_KEY": "adFailure",
                "SCENARIO_IDENTITY": "adServiceFailure",
            },
            "volumes": [
                {
                    "source": (
                        str(tmp_path) + "/evaluator-only/" + RUN_ID + "/control"
                    ),
                    "target": "/etc/flagd",
                    "type": "bind",
                }
            ],
        }
    )
    leaking_stdout = json.dumps(leaking_payload, sort_keys=True)
    lock = _locked_manifest(leaking_stdout)
    _register_config_and_images(
        lifecycle,
        runner,
        config_stdout=leaking_stdout,
        image_lock=lock,
    )
    _register_discovery(lifecycle, runner, potential=(), owned=())
    up = lifecycle.build_compose_invocation(
        lifecycle.ComposeAction.UP,
        project_root=ROOT,
        run_id=RUN_ID,
        docker_endpoint=DOCKER_ENDPOINT,
    )
    runner.respond(up.arguments)
    _register_discovery(lifecycle, runner)

    execution = lifecycle.up_environment(
        runner,
        context=None,
        preflight_evidence=_preflight_evidence(image_lock=lock),
        image_lock=lock,
        project_root=ROOT,
        artifacts_root=tmp_path,
    )

    assert execution.result.outcome is Outcome.SUCCESS
    assert execution.artifact_paths is not None
    observer_summary = json.loads(
        execution.artifact_paths.resolved_compose.read_text(encoding="utf-8")
    )
    raw_path = (
        tmp_path / "evaluator-only" / RUN_ID / "lifecycle" / "resolved-compose.json"
    )
    raw = json.loads(raw_path.read_text(encoding="utf-8"))
    assert "stdout" not in observer_summary
    assert observer_summary["sanitized_config"] != leaking_payload
    assert "<opaque>" in json.dumps(observer_summary["sanitized_config"])
    assert raw["stdout"] == leaking_stdout
    assert raw["sha256"] == observer_summary["sha256"] == lock.compose_config_sha256
    assert set(raw["image_sources"]) == set(observer_summary["image_sources"])

    observer_text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted((tmp_path / "observer-visible" / RUN_ID).rglob("*"))
        if path.is_file()
    ).casefold()
    for forbidden in (
        "evaluator-only",
        "/control",
        "demo.flagd.json",
        "adfailure",
        "adservicefailure",
    ):
        assert forbidden not in observer_text


def test_formal_up_without_bootstrap_lock_blocks_before_runner(
    tmp_path: Path,
) -> None:
    lifecycle = _lifecycle_module()
    runner = FixtureRunner()

    execution = lifecycle.up_environment(
        runner,
        context=None,
        preflight_evidence=_preflight_evidence(),
        image_lock=_uninitialized_manifest(),
        project_root=ROOT,
        artifacts_root=tmp_path,
    )

    assert execution.result.outcome is Outcome.BLOCKED_UPSTREAM
    assert execution.result.exit_code == 21
    assert execution.result.reason_code == "IMAGE_LOCK_UNINITIALIZED"
    assert runner.calls == []


def test_locked_candidate_with_any_local_image_mismatch_never_runs_up(
    tmp_path: Path,
) -> None:
    lifecycle = _lifecycle_module()
    runner = FixtureRunner()
    _register_config_and_images(lifecycle, runner, mismatched_index=24)
    up = lifecycle.build_compose_invocation(
        lifecycle.ComposeAction.UP,
        project_root=ROOT,
        run_id=RUN_ID,
        docker_endpoint=DOCKER_ENDPOINT,
    )

    execution = lifecycle.up_environment(
        runner,
        context=None,
        preflight_evidence=_preflight_evidence(),
        image_lock=_locked_manifest(),
        project_root=ROOT,
        artifacts_root=tmp_path,
    )

    assert execution.result.outcome is Outcome.BLOCKED_UPSTREAM
    assert execution.result.exit_code == 21
    assert execution.result.reason_code == "IMAGE_LOCK_MISMATCH"
    assert all(call[0] != up.arguments for call in runner.calls)
    assert not list(tmp_path.rglob("ownership-intent.json"))


@pytest.mark.parametrize(
    "failure_mode",
    ["timeout", "oserror", "runtime", "argument_mismatch"],
)
def test_pre_mutation_execution_failure_is_typed_and_creates_no_intent(
    tmp_path: Path,
    failure_mode: str,
) -> None:
    lifecycle = _lifecycle_module()
    runner = FixtureRunner()
    config = lifecycle.build_compose_invocation(
        lifecycle.ComposeAction.CONFIG,
        project_root=ROOT,
        run_id=RUN_ID,
        docker_endpoint=DOCKER_ENDPOINT,
    )
    if failure_mode == "timeout":
        runner.fail(
            config.arguments,
            subprocess.TimeoutExpired(config.arguments, timeout=30),
        )
    elif failure_mode == "oserror":
        runner.fail(config.arguments, OSError("fixture exec failure"))
    elif failure_mode == "runtime":
        runner.fail(config.arguments, RuntimeError("fixture runner failure"))
    else:
        runner.results.setdefault(config.arguments, []).append(
            CommandResult(
                arguments=("fixture", "mismatch"),
                exit_code=0,
                stdout=CONFIG_STDOUT,
                stderr="",
            )
        )

    execution = lifecycle.up_environment(
        runner,
        context=None,
        preflight_evidence=_preflight_evidence(),
        image_lock=_locked_manifest(),
        project_root=ROOT,
        artifacts_root=tmp_path,
    )

    assert execution.result.outcome is Outcome.BLOCKED_UPSTREAM
    assert execution.result.exit_code == 21
    assert execution.result.reason_code == "COMPOSE_CONFIG_UNAVAILABLE"
    assert execution.artifact_paths is None
    assert not list(tmp_path.rglob("ownership-intent.json"))


@pytest.mark.parametrize("error", [KeyboardInterrupt(), SystemExit(2)])
def test_runner_control_flow_exceptions_are_not_swallowed(
    tmp_path: Path,
    error: BaseException,
) -> None:
    lifecycle = _lifecycle_module()
    runner = FixtureRunner()
    config = lifecycle.build_compose_invocation(
        lifecycle.ComposeAction.CONFIG,
        project_root=ROOT,
        run_id=RUN_ID,
        docker_endpoint=DOCKER_ENDPOINT,
    )
    runner.fail(config.arguments, error)

    with pytest.raises(type(error)):
        lifecycle.up_environment(
            runner,
            context=None,
            preflight_evidence=_preflight_evidence(),
            image_lock=_locked_manifest(),
            project_root=ROOT,
            artifacts_root=tmp_path,
        )


@pytest.mark.parametrize(
    "failure_mode",
    ["nonzero", "timeout", "oserror", "runtime", "argument_mismatch"],
)
def test_compose_up_uncertainty_is_typed_41_with_truthful_evidence(
    tmp_path: Path,
    failure_mode: str,
) -> None:
    lifecycle = _lifecycle_module()
    runner = FixtureRunner()
    _register_config_and_images(lifecycle, runner)
    _register_discovery(
        lifecycle,
        runner,
        potential=(),
        owned=(),
    )
    up = lifecycle.build_compose_invocation(
        lifecycle.ComposeAction.UP,
        project_root=ROOT,
        run_id=RUN_ID,
        docker_endpoint=DOCKER_ENDPOINT,
    )
    if failure_mode == "nonzero":
        runner.respond(
            up.arguments,
            exit_code=1,
            stderr="compose reported failure",
        )
    elif failure_mode == "timeout":
        runner.fail(
            up.arguments,
            subprocess.TimeoutExpired(up.arguments, timeout=300),
        )
    elif failure_mode == "oserror":
        runner.fail(up.arguments, OSError("fixture exec failure"))
    elif failure_mode == "runtime":
        runner.fail(up.arguments, RuntimeError("fixture runner failure"))
    else:
        runner.results.setdefault(up.arguments, []).append(
            CommandResult(
                arguments=("fixture", "mismatch"),
                exit_code=0,
                stdout="",
                stderr="",
            )
        )
    _register_discovery(lifecycle, runner)

    execution = lifecycle.up_environment(
        runner,
        context=None,
        preflight_evidence=_preflight_evidence(),
        image_lock=_locked_manifest(),
        project_root=ROOT,
        artifacts_root=tmp_path,
    )

    assert execution.result.outcome is Outcome.MANUAL_INTERVENTION_REQUIRED
    assert execution.result.exit_code == 41
    assert execution.result.reason_code == "COMPOSE_UP_MUTATION_UNCERTAIN"
    assert execution.artifact_paths is not None
    assert execution.artifact_paths.ownership_intent is not None
    assert execution.artifact_paths.ownership_intent.is_file()
    assert execution.artifact_paths.command_log is not None
    assert execution.artifact_paths.command_log.is_file()
    assert execution.artifact_paths.manual_diagnostic is not None
    assert execution.artifact_paths.manual_diagnostic.is_file()
    assert execution.artifact_paths.resolved_compose is None
    assert execution.ownership_context is not None
    assert execution.ownership_context.is_authentic()
    assert execution.artifact_paths.ownership_manifest is not None
    assert execution.artifact_paths.ownership_manifest.is_file()
    assert all(call[0][-1:] != ("down",) for call in runner.calls)


def test_bare_preflight_result_cannot_authorize_up(
    tmp_path: Path,
) -> None:
    lifecycle = _lifecycle_module()
    runner = FixtureRunner()

    execution = lifecycle.up_environment(
        runner,
        context=None,
        preflight_evidence=PreflightResult(
            outcome=Outcome.SUCCESS,
            exit_code=0,
            reason_codes=(),
        ),
        image_lock=_locked_manifest(),
        project_root=ROOT,
        artifacts_root=tmp_path,
    )
    assert execution.result.outcome is Outcome.UNSAFE
    assert execution.result.reason_code == "PREFLIGHT_EVIDENCE_INVALID"
    assert runner.calls == []


@pytest.mark.parametrize(
    "docker_overrides",
    [
        {"context_name": ""},
        {"endpoint": ""},
        {"daemon_id": ""},
        {"daemon_id": "placeholder"},
    ],
)
def test_unobserved_daemon_binding_evidence_is_zero_command_unsafe(
    tmp_path: Path,
    docker_overrides: dict[str, object],
) -> None:
    lifecycle = _lifecycle_module()
    runner = FixtureRunner()

    execution = lifecycle.up_environment(
        runner,
        context=None,
        preflight_evidence=_preflight_evidence(
            docker_overrides=docker_overrides,
        ),
        image_lock=_locked_manifest(),
        project_root=ROOT,
        artifacts_root=tmp_path,
    )

    assert execution.result.outcome is Outcome.UNSAFE
    assert execution.result.reason_code == "PREFLIGHT_EVIDENCE_INVALID"
    assert runner.calls == []


def test_stale_preflight_with_full_context_is_unsafe_and_zero_up(
    tmp_path: Path,
) -> None:
    lifecycle = _lifecycle_module()
    runner = FixtureRunner()
    context = _context(tmp_path)
    up = lifecycle.build_compose_invocation(
        lifecycle.ComposeAction.UP,
        project_root=ROOT,
        run_id=RUN_ID,
        docker_endpoint=DOCKER_ENDPOINT,
    )

    execution = lifecycle.up_environment(
        runner,
        context=context,
        preflight_evidence=_preflight_evidence(
            context=context,
            stale=True,
        ),
        image_lock=_locked_manifest(),
        project_root=ROOT,
        artifacts_root=tmp_path,
    )

    assert execution.result.outcome is Outcome.UNSAFE
    assert execution.result.reason_code == "PREFLIGHT_EVIDENCE_INVALID"
    assert runner.calls == []
    assert all(arguments != up.arguments for arguments, _ in runner.calls)


def test_current_empty_preflight_mismatch_is_unsafe_and_zero_up(
    tmp_path: Path,
) -> None:
    lifecycle = _lifecycle_module()
    runner = FixtureRunner()
    _register_config_and_images(lifecycle, runner)
    _register_discovery(lifecycle, runner)
    up = lifecycle.build_compose_invocation(
        lifecycle.ComposeAction.UP,
        project_root=ROOT,
        run_id=RUN_ID,
        docker_endpoint=DOCKER_ENDPOINT,
    )

    execution = lifecycle.up_environment(
        runner,
        context=None,
        preflight_evidence=_preflight_evidence(),
        image_lock=_locked_manifest(),
        project_root=ROOT,
        artifacts_root=tmp_path,
    )

    assert execution.result.outcome is Outcome.UNSAFE
    assert execution.result.reason_code == "PREFLIGHT_SNAPSHOT_CHANGED"
    assert all(arguments != up.arguments for arguments, _ in runner.calls)
    assert list(tmp_path.rglob("ownership-intent.json"))


@pytest.mark.parametrize(
    ("context_endpoint", "daemon_id", "context_name", "architecture"),
    [
        ("tcp://127.0.0.1:2375", DOCKER_DAEMON_ID, "desktop-linux", "aarch64"),
        (DOCKER_ENDPOINT, "changed-daemon-id", "desktop-linux", "aarch64"),
        (DOCKER_ENDPOINT, DOCKER_DAEMON_ID, "default", "aarch64"),
        (DOCKER_ENDPOINT, DOCKER_DAEMON_ID, "desktop-linux", "amd64"),
    ],
)
def test_daemon_binding_change_is_revalidated_with_locked_host_and_zero_up(
    tmp_path: Path,
    context_endpoint: str,
    daemon_id: str,
    context_name: str,
    architecture: str,
) -> None:
    lifecycle = _lifecycle_module()
    runner = FixtureRunner()
    _register_config_and_images(
        lifecycle,
        runner,
        revalidation_context_endpoint=context_endpoint,
        revalidation_daemon_id=daemon_id,
        revalidation_context_name=context_name,
        revalidation_architecture=architecture,
    )
    _register_discovery(
        lifecycle,
        runner,
        potential=(),
        owned=(),
    )
    up = lifecycle.build_compose_invocation(
        lifecycle.ComposeAction.UP,
        project_root=ROOT,
        run_id=RUN_ID,
        docker_endpoint=DOCKER_ENDPOINT,
    )
    revalidation = lifecycle.build_daemon_revalidation_invocations(
        run_id=RUN_ID,
        docker_endpoint=DOCKER_ENDPOINT,
    )

    execution = lifecycle.up_environment(
        runner,
        context=None,
        preflight_evidence=_preflight_evidence(),
        image_lock=_locked_manifest(),
        project_root=ROOT,
        artifacts_root=tmp_path,
    )

    assert execution.result.outcome is Outcome.UNSAFE
    assert execution.result.reason_code == "DAEMON_BINDING_CHANGED"
    called = [arguments for arguments, _environment in runner.calls]
    assert revalidation[0].arguments in called
    assert revalidation[1].arguments in called
    assert revalidation[1].arguments[:3] == (
        "docker",
        "--host",
        DOCKER_ENDPOINT,
    )
    assert up.arguments not in called
    assert all(
        arguments[:3] == ("docker", "--host", DOCKER_ENDPOINT)
        for arguments in called
        if arguments[0] == "docker" and "context" not in arguments
    )


def test_preflight_must_remain_current_immediately_before_mutation(
    tmp_path: Path,
    monkeypatch,
) -> None:
    lifecycle = _lifecycle_module()
    runner = FixtureRunner()
    _register_config_and_images(lifecycle, runner)
    _register_discovery(
        lifecycle,
        runner,
        potential=(),
        owned=(),
    )
    up = lifecycle.build_compose_invocation(
        lifecycle.ComposeAction.UP,
        project_root=ROOT,
        run_id=RUN_ID,
        docker_endpoint=DOCKER_ENDPOINT,
    )
    current_checks = iter((True, False))
    monkeypatch.setattr(
        type(_preflight_evidence()),
        "is_current",
        lambda _self: next(current_checks),
    )

    execution = lifecycle.up_environment(
        runner,
        context=None,
        preflight_evidence=_preflight_evidence(),
        image_lock=_locked_manifest(),
        project_root=ROOT,
        artifacts_root=tmp_path,
    )

    assert execution.result.outcome is Outcome.UNSAFE
    assert execution.result.reason_code == "PREFLIGHT_EVIDENCE_INVALID"
    assert all(arguments != up.arguments for arguments, _ in runner.calls)
    assert not list(tmp_path.rglob("ownership-intent.json"))


def test_current_full_context_requires_exact_immediate_snapshot_before_up(
    tmp_path: Path,
) -> None:
    lifecycle = _lifecycle_module()
    context = _context(tmp_path)
    runner = FixtureRunner()
    _register_config_and_images(lifecycle, runner)
    current = _resources()[1:]
    _register_discovery(
        lifecycle,
        runner,
        potential=current,
        owned=current,
    )
    up = lifecycle.build_compose_invocation(
        lifecycle.ComposeAction.UP,
        project_root=ROOT,
        run_id=RUN_ID,
        docker_endpoint=DOCKER_ENDPOINT,
    )

    execution = lifecycle.up_environment(
        runner,
        context=context,
        preflight_evidence=_preflight_evidence(context=context),
        image_lock=_locked_manifest(),
        project_root=ROOT,
        artifacts_root=tmp_path,
    )

    assert execution.result.outcome is Outcome.UNSAFE
    assert execution.result.reason_code == "PREFLIGHT_SNAPSHOT_CHANGED"
    assert all(arguments != up.arguments for arguments, _ in runner.calls)


def test_health_fails_closed_until_task7_readiness_is_registered(
    tmp_path: Path,
) -> None:
    lifecycle = _lifecycle_module()
    context = _context(tmp_path)
    runner = FixtureRunner()

    result = lifecycle.health_environment(
        runner,
        context=context,
        project_root=ROOT,
        docker_endpoint=DOCKER_ENDPOINT,
        readiness=None,
    )

    assert result.outcome is Outcome.BLOCKED_ENVIRONMENT
    assert result.exit_code == 20
    assert result.reason_code == "READINESS_EVIDENCE_UNAVAILABLE"
    assert runner.calls == []


def test_only_healthy_compose_rows_cannot_override_incomplete_readiness(
    tmp_path: Path,
) -> None:
    lifecycle = _lifecycle_module()
    context = _context(tmp_path)
    runner = FixtureRunner()
    readiness = lifecycle.ReadinessEvidence(
        schema_version="phase0.readiness-evidence.v1",
        run_id=RUN_ID,
        ownership_resources_complete=True,
        load_generator_ready=True,
        collector_ready=True,
        prometheus_fresh=True,
        jaeger_fresh=True,
        opensearch_fresh=False,
    )

    result = lifecycle.health_environment(
        runner,
        context=context,
        project_root=ROOT,
        docker_endpoint=DOCKER_ENDPOINT,
        readiness=readiness,
    )

    assert result.outcome is Outcome.BLOCKED_ENVIRONMENT
    assert result.reason_code == "READINESS_INCOMPLETE"
    assert runner.calls == []


def test_down_requires_potential_owned_and_manifest_sets_to_be_equivalent(
    tmp_path: Path,
) -> None:
    lifecycle = _lifecycle_module()
    context = _context(tmp_path)
    runner = FixtureRunner()
    discovery = _register_discovery(lifecycle, runner)
    down = lifecycle.build_compose_invocation(
        lifecycle.ComposeAction.DOWN,
        project_root=ROOT,
        run_id=RUN_ID,
        docker_endpoint=DOCKER_ENDPOINT,
    )
    runner.respond(down.arguments)

    result = lifecycle.down_environment(
        runner,
        context=context,
        project_root=ROOT,
        docker_endpoint=DOCKER_ENDPOINT,
    )

    assert result.outcome is Outcome.SUCCESS
    assert [call[0] for call in runner.calls] == [
        *(invocation.arguments for invocation in discovery),
        down.arguments,
    ]
    assert all(
        "--no-trunc" in invocation.arguments
        for invocation in discovery
        if "volume" not in invocation.purpose
    )
    assert all(
        f"label={COMPOSE_PROJECT_LABEL}={PROJECT_NAMESPACE}" in invocation.arguments
        for invocation in discovery
    )


@pytest.mark.parametrize("mode", ["label_mismatch", "extra", "missing"])
def test_down_label_extra_or_missing_resource_is_unsafe_without_down(
    tmp_path: Path,
    mode: str,
) -> None:
    lifecycle = _lifecycle_module()
    context = _context(tmp_path)
    runner = FixtureRunner()
    expected = _resources()
    potential = expected
    owned = expected
    if mode == "label_mismatch":
        mismatched = expected[0].model_copy(
            update={
                "labels": {
                    **expected[0].labels,
                    RUN_LABEL: "b" * 32,
                }
            }
        )
        potential = (mismatched, expected[1])
        owned = (expected[1],)
    elif mode == "extra":
        extra = expected[0].model_copy(
            update={
                "name": "ecomsre-phase0-extra",
                "resource_id": "e" * 64,
                "identity_evidence": ("container:" + "e" * 64,),
            }
        )
        potential = expected + (extra,)
        owned = expected + (extra,)
    else:
        # Published ports are derived from their owning container records.
        # Remove a concrete Compose container to exercise a truly missing
        # resource instead of merely omitting the duplicate port fixture.
        potential = expected[1:]
        owned = expected[1:]
    _register_discovery(
        lifecycle,
        runner,
        potential=potential,
        owned=owned,
    )
    down = lifecycle.build_compose_invocation(
        lifecycle.ComposeAction.DOWN,
        project_root=ROOT,
        run_id=RUN_ID,
        docker_endpoint=DOCKER_ENDPOINT,
    )

    result = lifecycle.down_environment(
        runner,
        context=context,
        project_root=ROOT,
        docker_endpoint=DOCKER_ENDPOINT,
    )

    assert result.outcome is Outcome.UNSAFE
    assert result.reason_code == "RESOURCE_OWNERSHIP_UNKNOWN"
    assert all(call[0] != down.arguments for call in runner.calls)


def test_post_up_signing_failure_keeps_intent_and_manual_diagnostic(
    tmp_path: Path,
    monkeypatch,
) -> None:
    lifecycle = _lifecycle_module()
    runner = FixtureRunner()
    up, _discovery = _prepare_successful_up(lifecycle, runner)

    def fail_signing(*_args, **_kwargs):
        raise OwnershipAuthorityError("fixture signing failed")

    monkeypatch.setattr(
        lifecycle,
        "create_ownership_authority_artifacts",
        fail_signing,
    )

    execution = lifecycle.up_environment(
        runner,
        context=None,
        preflight_evidence=_preflight_evidence(),
        image_lock=_locked_manifest(),
        project_root=ROOT,
        artifacts_root=tmp_path,
    )

    assert execution.result.outcome is Outcome.MANUAL_INTERVENTION_REQUIRED
    assert execution.result.exit_code == 41
    assert execution.result.reason_code == "POST_UP_OWNERSHIP_UNPROVEN"
    assert execution.artifact_paths is not None
    assert execution.artifact_paths.ownership_intent.is_file()
    assert execution.artifact_paths.manual_diagnostic is not None
    assert execution.artifact_paths.manual_diagnostic.is_file()
    diagnostic = json.loads(
        execution.artifact_paths.manual_diagnostic.read_text(encoding="utf-8")
    )
    assert diagnostic["reason_code"] == "POST_UP_OWNERSHIP_UNPROVEN"
    assert any(call[0] == up.arguments for call in runner.calls)
    assert all(call[0][-1:] != ("down",) for call in runner.calls)


def test_post_up_missing_expected_published_port_requires_manual_intervention(
    tmp_path: Path,
) -> None:
    lifecycle = _lifecycle_module()
    runner = FixtureRunner()
    _register_config_and_images(lifecycle, runner)
    _register_discovery(
        lifecycle,
        runner,
        potential=(),
        owned=(),
    )
    up = lifecycle.build_compose_invocation(
        lifecycle.ComposeAction.UP,
        project_root=ROOT,
        run_id=RUN_ID,
        docker_endpoint=DOCKER_ENDPOINT,
    )
    runner.respond(up.arguments)
    _register_discovery(lifecycle, runner, include_ports=False)

    execution = lifecycle.up_environment(
        runner,
        context=None,
        preflight_evidence=_preflight_evidence(),
        image_lock=_locked_manifest(),
        project_root=ROOT,
        artifacts_root=tmp_path,
    )

    assert execution.result.outcome is Outcome.MANUAL_INTERVENTION_REQUIRED
    assert execution.result.exit_code == 41
    assert execution.result.reason_code == "POST_UP_OWNERSHIP_UNPROVEN"
    assert execution.artifact_paths is not None
    assert execution.artifact_paths.ownership_intent.is_file()
    assert execution.artifact_paths.manual_diagnostic is not None
    assert execution.artifact_paths.manual_diagnostic.is_file()
    assert all(call[0][-1:] != ("down",) for call in runner.calls)


@pytest.mark.parametrize(
    "port_mode",
    [
        "target",
        "owner",
        "host_ip",
        "protocol",
        "published",
        "duplicate",
        "unknown_arrow",
    ],
)
def test_post_up_rejects_inexact_or_ambiguous_published_port_binding(
    tmp_path: Path,
    port_mode: str,
) -> None:
    lifecycle = _lifecycle_module()
    runner = FixtureRunner()
    _register_config_and_images(lifecycle, runner)
    _register_discovery(
        lifecycle,
        runner,
        potential=(),
        owned=(),
    )
    up = lifecycle.build_compose_invocation(
        lifecycle.ComposeAction.UP,
        project_root=ROOT,
        run_id=RUN_ID,
        docker_endpoint=DOCKER_ENDPOINT,
    )
    runner.respond(up.arguments)
    _register_discovery(
        lifecycle,
        runner,
        port_mode=port_mode,
    )

    execution = lifecycle.up_environment(
        runner,
        context=None,
        preflight_evidence=_preflight_evidence(),
        image_lock=_locked_manifest(),
        project_root=ROOT,
        artifacts_root=tmp_path,
    )

    assert execution.result.outcome is Outcome.MANUAL_INTERVENTION_REQUIRED
    assert execution.result.exit_code == 41
    assert execution.result.reason_code == "POST_UP_OWNERSHIP_UNPROVEN"
    assert execution.artifact_paths is not None
    assert execution.artifact_paths.ownership_manifest is None
    assert execution.artifact_paths.manual_diagnostic is not None
    assert execution.artifact_paths.manual_diagnostic.is_file()
    assert all(call[0][-1:] != ("down",) for call in runner.calls)


def test_real_upstream_resolved_fixture_models_all_target_only_ports() -> None:
    lifecycle = _lifecycle_module()
    upstream = ROOT / "third_party" / "opentelemetry-demo"
    compose_text = "\n".join(
        (
            (upstream / "compose.yaml").read_text(encoding="utf-8"),
            (upstream / "compose.observability.yaml").read_text(encoding="utf-8"),
        )
    )
    target_only_variables = re.findall(
        r"(?m)^\s+-\s+[\"']?\$\{([A-Z0-9_]+)\}[\"']?\s*$",
        compose_text,
    )
    target_only_literals = [
        int(value)
        for value in re.findall(
            r"(?m)^\s+-\s+[\"']?(\d+)[\"']?\s*$",
            compose_text,
        )
    ]
    environment = dict(
        line.split("=", 1)
        for line in (upstream / ".env").read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#") and "=" in line
    )
    source_targets = [
        int(environment[name]) for name in target_only_variables
    ] + target_only_literals
    resolved = ResolvedComposeConfig.from_stdout(REAL_UPSTREAM_RESOLVED_CONFIG_STDOUT)

    expected = lifecycle.parse_expected_port_bindings(resolved)

    assert sum(map(len, REAL_UPSTREAM_TARGET_ONLY_PORTS.values())) == 25
    assert len(target_only_variables) == 24
    assert target_only_literals == [9200]
    assert Counter(source_targets) == Counter(
        target
        for targets in REAL_UPSTREAM_TARGET_ONLY_PORTS.values()
        for target in targets
    )
    assert len(expected) == 25
    assert all(binding.published_port is None for binding in expected)
    assert {
        (binding.service, binding.target_port, binding.protocol) for binding in expected
    } == {
        (service, target, "tcp")
        for service, targets in REAL_UPSTREAM_TARGET_ONLY_PORTS.items()
        for target in targets
    }


@pytest.mark.parametrize("dual_stack", [False, True])
def test_target_only_port_accepts_one_runtime_assigned_published_port(
    tmp_path: Path,
    dual_stack: bool,
) -> None:
    lifecycle = _lifecycle_module()
    runner = FixtureRunner()
    lock = _locked_manifest(TARGET_ONLY_CONFIG_STDOUT)
    _register_config_and_images(
        lifecycle,
        runner,
        config_stdout=TARGET_ONLY_CONFIG_STDOUT,
        image_lock=lock,
    )
    _register_discovery(
        lifecycle,
        runner,
        potential=(),
        owned=(),
    )
    up = lifecycle.build_compose_invocation(
        lifecycle.ComposeAction.UP,
        project_root=ROOT,
        run_id=RUN_ID,
        docker_endpoint=DOCKER_ENDPOINT,
    )
    runner.respond(up.arguments)
    _register_discovery(
        lifecycle,
        runner,
        port_owner_service="ad",
        published_port=RUNTIME_PORT,
        target_port=9555,
        dual_stack=dual_stack,
    )

    execution = lifecycle.up_environment(
        runner,
        context=None,
        preflight_evidence=_preflight_evidence(image_lock=lock),
        image_lock=lock,
        project_root=ROOT,
        artifacts_root=tmp_path,
    )

    assert execution.result.outcome is Outcome.SUCCESS
    assert execution.ownership_context is not None
    ports = tuple(
        resource
        for resource in execution.ownership_context.manifest.resources
        if resource.kind == "port"
    )
    assert len(ports) == (2 if dual_stack else 1)
    assert len({resource.resource_id for resource in ports}) == len(ports)
    assert all(
        resource.resource_id.startswith("port-binding:")
        and f"published_port:{RUNTIME_PORT}" in resource.identity_evidence
        and "target_port:9555" in resource.identity_evidence
        and "service:ad" in resource.identity_evidence
        for resource in ports
    )


def test_explicit_wildcard_port_accepts_equivalent_dual_stack_bindings(
    tmp_path: Path,
) -> None:
    lifecycle = _lifecycle_module()
    runner = FixtureRunner()
    _register_config_and_images(lifecycle, runner)
    _register_discovery(
        lifecycle,
        runner,
        potential=(),
        owned=(),
    )
    up = lifecycle.build_compose_invocation(
        lifecycle.ComposeAction.UP,
        project_root=ROOT,
        run_id=RUN_ID,
        docker_endpoint=DOCKER_ENDPOINT,
    )
    runner.respond(up.arguments)
    _register_discovery(lifecycle, runner, dual_stack=True)

    execution = lifecycle.up_environment(
        runner,
        context=None,
        preflight_evidence=_preflight_evidence(),
        image_lock=_locked_manifest(),
        project_root=ROOT,
        artifacts_root=tmp_path,
    )

    assert execution.result.outcome is Outcome.SUCCESS
    assert execution.ownership_context is not None
    ports = tuple(
        resource
        for resource in execution.ownership_context.manifest.resources
        if resource.kind == "port"
    )
    assert len(ports) == 2
    assert len({resource.resource_id for resource in ports}) == 2
    assert {
        next(
            value.removeprefix("host_family:")
            for value in resource.identity_evidence
            if value.startswith("host_family:")
        )
        for resource in ports
    } == {"ipv4", "ipv6"}


def test_target_only_port_rejects_multiple_runtime_published_ports(
    tmp_path: Path,
) -> None:
    lifecycle = _lifecycle_module()
    runner = FixtureRunner()
    lock = _locked_manifest(TARGET_ONLY_CONFIG_STDOUT)
    _register_config_and_images(
        lifecycle,
        runner,
        config_stdout=TARGET_ONLY_CONFIG_STDOUT,
        image_lock=lock,
    )
    _register_discovery(
        lifecycle,
        runner,
        potential=(),
        owned=(),
    )
    up = lifecycle.build_compose_invocation(
        lifecycle.ComposeAction.UP,
        project_root=ROOT,
        run_id=RUN_ID,
        docker_endpoint=DOCKER_ENDPOINT,
    )
    runner.respond(up.arguments)
    _register_discovery(
        lifecycle,
        runner,
        port_owner_service="ad",
        published_port=RUNTIME_PORT,
        target_port=9555,
        port_mode="ambiguous_published",
    )

    execution = lifecycle.up_environment(
        runner,
        context=None,
        preflight_evidence=_preflight_evidence(image_lock=lock),
        image_lock=lock,
        project_root=ROOT,
        artifacts_root=tmp_path,
    )

    assert execution.result.outcome is Outcome.MANUAL_INTERVENTION_REQUIRED
    assert execution.result.exit_code == 41
    assert execution.ownership_context is None
    assert execution.artifact_paths is not None
    assert execution.artifact_paths.ownership_manifest is None
    assert all(call[0][-1:] != ("down",) for call in runner.calls)


@pytest.mark.parametrize(
    "port_mode",
    ["host_ip", "ipv6_only", "non_equivalent_hosts"],
)
def test_target_only_port_rejects_non_default_host_bindings(
    tmp_path: Path,
    port_mode: str,
) -> None:
    lifecycle = _lifecycle_module()
    runner = FixtureRunner()
    lock = _locked_manifest(TARGET_ONLY_CONFIG_STDOUT)
    _register_config_and_images(
        lifecycle,
        runner,
        config_stdout=TARGET_ONLY_CONFIG_STDOUT,
        image_lock=lock,
    )
    _register_discovery(
        lifecycle,
        runner,
        potential=(),
        owned=(),
    )
    up = lifecycle.build_compose_invocation(
        lifecycle.ComposeAction.UP,
        project_root=ROOT,
        run_id=RUN_ID,
        docker_endpoint=DOCKER_ENDPOINT,
    )
    runner.respond(up.arguments)
    _register_discovery(
        lifecycle,
        runner,
        port_owner_service="ad",
        published_port=RUNTIME_PORT,
        target_port=9555,
        port_mode=port_mode,
    )

    execution = lifecycle.up_environment(
        runner,
        context=None,
        preflight_evidence=_preflight_evidence(image_lock=lock),
        image_lock=lock,
        project_root=ROOT,
        artifacts_root=tmp_path,
    )

    assert execution.result.outcome is Outcome.MANUAL_INTERVENTION_REQUIRED
    assert execution.result.exit_code == 41
    assert execution.ownership_context is None
    assert execution.artifact_paths is not None
    assert execution.artifact_paths.ownership_manifest is None
    assert execution.artifact_paths.manual_diagnostic is not None
    assert execution.artifact_paths.manual_diagnostic.is_file()
    assert all(call[0][-1:] != ("down",) for call in runner.calls)
