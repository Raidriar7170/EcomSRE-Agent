"""Local-Docker-only lifecycle and ownership checks for one sandbox."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import hashlib
from importlib import import_module
import json
import os
from pathlib import Path
import socket
import subprocess
import time

from ecomsre_live_sandbox.contracts import (
    CleanupResult,
    ConfigBundle,
    EnvironmentConfig,
    LocalEndpoints,
    ResolvedSandbox,
    file_sha256,
    write_private_json,
)
from ecomsre_live_sandbox.image_authority import CachedImage, CachedImageInspection


EXPECTED_SERVICES = (
    "ad",
    "astronomy-db",
    "cart",
    "checkout",
    "currency",
    "email",
    "flagd",
    "flagd-ui",
    "frontend",
    "frontend-proxy",
    "grafana",
    "image-provider",
    "jaeger",
    "load-generator",
    "opamp-server",
    "opensearch",
    "otel-collector",
    "payment",
    "product-catalog",
    "prometheus",
    "quote",
    "recommendation",
    "shipping",
    "telemetry-docs",
    "valkey-cart",
)


class DockerBoundaryError(RuntimeError):
    pass


class ResourceOwnershipError(RuntimeError):
    pass


class SandboxDriftError(RuntimeError):
    pass


def require_local_docker_endpoint(endpoint: str) -> str:
    if not endpoint.startswith("unix://") or endpoint.startswith("unix://///"):
        raise DockerBoundaryError("Docker endpoint is not a local Unix socket")
    path = endpoint.removeprefix("unix://")
    if not path.startswith("/") or "\x00" in path:
        raise DockerBoundaryError("Docker Unix socket path is invalid")
    return path


def require_owned_labels(
    labels: Mapping[str, str], environment: EnvironmentConfig
) -> None:
    expected = {
        "com.docker.compose.project": environment.compose_project,
        environment.sandbox_label_key: environment.sandbox_id,
    }
    if any(labels.get(key) != value for key, value in expected.items()):
        raise ResourceOwnershipError("sandbox resource lacks exact dual ownership labels")


def _one_published_port(
    services: Mapping[str, object], service: str, target: int, published: int
) -> None:
    raw = services.get(service)
    if not isinstance(raw, Mapping):
        raise SandboxDriftError(f"resolved Compose lacks service {service}")
    ports = raw.get("ports")
    if not isinstance(ports, list):
        raise SandboxDriftError(f"resolved Compose lacks ports for {service}")
    matches = [
        item
        for item in ports
        if isinstance(item, Mapping)
        and item.get("host_ip") == "127.0.0.1"
        and item.get("target") == target
        and str(item.get("published")) == str(published)
        and item.get("protocol") == "tcp"
    ]
    if len(matches) != 1 or len(ports) != 1:
        raise SandboxDriftError(f"resolved Compose port binding differs for {service}")


def discover_endpoints(
    resolved_compose: Mapping[str, object], bundle: ConfigBundle
) -> LocalEndpoints:
    services = resolved_compose.get("services")
    if not isinstance(services, Mapping):
        raise SandboxDriftError("resolved Compose service map is unavailable")
    _one_published_port(services, "frontend-proxy", 8080, 18080)
    _one_published_port(services, "flagd", 8016, 18016)
    _one_published_port(
        services,
        "prometheus",
        bundle.telemetry.prometheus.target_port,
        bundle.telemetry.prometheus.published_port,
    )
    _one_published_port(
        services,
        "jaeger",
        bundle.telemetry.jaeger.target_port,
        bundle.telemetry.jaeger.published_port,
    )
    _one_published_port(
        services,
        "opensearch",
        bundle.telemetry.opensearch.target_port,
        bundle.telemetry.opensearch.published_port,
    )
    return LocalEndpoints(
        frontend="http://127.0.0.1:18080",
        flag_control="http://127.0.0.1:18080/feature/api",
        flag_evaluation="http://127.0.0.1:18016",
        prometheus="http://127.0.0.1:19090",
        opensearch="http://127.0.0.1:19200",
        jaeger="http://127.0.0.1:11686",
    )


@dataclass(frozen=True, slots=True)
class CommandResult:
    arguments: tuple[str, ...]
    stdout: str
    stderr: str


class ExactCommandRunner:
    """Run only caller-owned static argv; shell interpretation is impossible."""

    def run(
        self,
        arguments: tuple[str, ...],
        *,
        cwd: Path,
        env: Mapping[str, str] | None = None,
        timeout_seconds: float = 300,
    ) -> CommandResult:
        if not arguments or any("\x00" in item for item in arguments):
            raise ValueError("command argv is invalid")
        completed = subprocess.run(
            list(arguments),
            cwd=cwd,
            env=None if env is None else dict(env),
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
        result = CommandResult(arguments, completed.stdout, completed.stderr)
        if completed.returncode != 0:
            raise RuntimeError(
                f"allowlisted command failed ({arguments[0]} {arguments[1] if len(arguments) > 1 else ''}): "
                f"{completed.stderr.strip()[:500]}"
            )
        return result


@dataclass(frozen=True, slots=True)
class DockerSnapshot:
    containers: frozenset[str]
    networks: frozenset[str]
    volumes: frozenset[str]


class SandboxEnvironment:
    def __init__(
        self,
        *,
        repository_root: Path,
        bundle: ConfigBundle,
        flagd_directory: Path,
        runner: ExactCommandRunner | None = None,
    ) -> None:
        self.repository_root = repository_root.resolve()
        self.bundle = bundle
        self.flagd_directory = flagd_directory.resolve()
        self.runner = runner or ExactCommandRunner()
        self.upstream_root = self.repository_root / "third_party/opentelemetry-demo"
        self.config_root = (
            self.repository_root / "config/live-telemetry-controlled-remediation-v1"
        )
        self._resolved_payload: dict[str, object] | None = None
        self._baseline_snapshot: DockerSnapshot | None = None
        self._daemon_id: str | None = None

    def _compose_env(self) -> dict[str, str]:
        value = dict(os.environ)
        value.update(
            {
                "DEMO_VERSION": "3.0.0",
                "IMAGE_VERSION": "3.0.0",
                "ECOMSRE_SANDBOX_ID": self.bundle.environment.sandbox_id,
                "SANDBOX_FLAGD_DIR": str(self.flagd_directory),
                "SANDBOX_COLLECTOR_CONFIG": str(
                    self.config_root / "otelcol-sandbox.yml"
                ),
            }
        )
        return value

    def _compose_prefix(self) -> tuple[str, ...]:
        files = tuple(self.repository_root / item for item in self.bundle.environment.compose_files)
        if any(not item.is_file() for item in files):
            raise SandboxDriftError("one or more frozen Compose inputs are unavailable")
        return (
            "docker",
            "compose",
            "--project-name",
            self.bundle.environment.compose_project,
            "-f",
            str(files[0]),
            "-f",
            str(files[1]),
            "-f",
            str(files[2]),
        )

    def verify_local_docker(self) -> dict[str, str]:
        context = self.runner.run(
            ("docker", "context", "show"), cwd=self.repository_root
        ).stdout.strip()
        if not context or context.casefold() in {"remote", "kubernetes"}:
            raise DockerBoundaryError("Docker context identity is unsupported")
        endpoint_result = self.runner.run(
            (
                "docker",
                "context",
                "inspect",
                context,
                "--format",
                "{{json .Endpoints.docker.Host}}",
            ),
            cwd=self.repository_root,
        )
        endpoint = json.loads(endpoint_result.stdout)
        if not isinstance(endpoint, str):
            raise DockerBoundaryError("Docker context endpoint is unavailable")
        require_local_docker_endpoint(endpoint)
        info = json.loads(
            self.runner.run(
                ("docker", "info", "--format", "{{json .}}"),
                cwd=self.repository_root,
            ).stdout
        )
        if (
            not isinstance(info, Mapping)
            or info.get("OSType") != "linux"
            or info.get("Architecture") not in {"arm64", "aarch64"}
            or not isinstance(info.get("ID"), str)
        ):
            raise DockerBoundaryError("Docker daemon is not local linux/arm64")
        self._daemon_id = str(info["ID"])
        return {"context": context, "endpoint": endpoint, "daemon_id": self._daemon_id}

    def verify_upstream(self) -> None:
        head = self.runner.run(
            ("git", "rev-parse", "HEAD"), cwd=self.upstream_root
        ).stdout.strip()
        tag = self.runner.run(
            ("git", "describe", "--tags", "--exact-match"), cwd=self.upstream_root
        ).stdout.strip()
        status = self.runner.run(
            ("git", "status", "--porcelain=v1"), cwd=self.upstream_root
        ).stdout
        if (
            head != self.bundle.environment.upstream_commit
            or tag != self.bundle.environment.upstream_tag
            or status
        ):
            raise SandboxDriftError("pinned OpenTelemetry Demo checkout drifted")

    def _verify_resolved_contract(self, value: Mapping[str, object]) -> None:
        services = value.get("services")
        if not isinstance(services, Mapping) or tuple(sorted(services)) != EXPECTED_SERVICES:
            raise SandboxDriftError("resolved Compose service inventory drifted")
        expected_label = {
            self.bundle.environment.sandbox_label_key: self.bundle.environment.sandbox_id
        }
        allowed_published = {"frontend-proxy", "flagd", "prometheus", "jaeger", "opensearch"}
        for name, raw in services.items():
            if not isinstance(raw, Mapping):
                raise SandboxDriftError(f"resolved service {name} is malformed")
            labels = raw.get("labels")
            if not isinstance(labels, Mapping) or any(
                labels.get(key) != expected for key, expected in expected_label.items()
            ):
                raise SandboxDriftError(f"resolved service {name} lacks sandbox label")
            if raw.get("platform") != "linux/arm64" or raw.get("pull_policy") != "never":
                raise SandboxDriftError(f"resolved service {name} is not frozen arm64")
            if raw.get("privileged") is True or raw.get("network_mode") == "host":
                raise SandboxDriftError(f"resolved service {name} weakens isolation")
            ports = raw.get("ports", [])
            if name not in allowed_published and ports:
                raise SandboxDriftError(f"resolved service {name} unexpectedly publishes a port")
            image = raw.get("image")
            if not isinstance(image, str) or image.endswith(":latest") or ":main" in image:
                raise SandboxDriftError(f"resolved service {name} has an unfrozen image")
            for mount in raw.get("volumes", []) if isinstance(raw.get("volumes", []), list) else []:
                if not isinstance(mount, Mapping):
                    raise SandboxDriftError(f"resolved service {name} has malformed mount")
                if mount.get("type") == "bind":
                    source = Path(str(mount.get("source", ""))).resolve(strict=False)
                    allowed_write = name == "flagd-ui" and source == self.flagd_directory
                    allowed_read = (
                        source.is_relative_to(self.upstream_root)
                        or source == (self.config_root / "otelcol-sandbox.yml").resolve()
                        or source == self.flagd_directory
                    )
                    if not allowed_read or (
                        not allowed_write and mount.get("read_only") is not True
                    ):
                        raise SandboxDriftError(f"resolved service {name} has unsafe bind mount")
                    if str(source) in {"/", "/var/run/docker.sock"}:
                        raise SandboxDriftError("host root or Docker socket mount is forbidden")
        networks = value.get("networks")
        if not isinstance(networks, Mapping) or set(networks) != {"default"}:
            raise SandboxDriftError("resolved Compose network inventory drifted")
        default_network = networks["default"]
        if (
            not isinstance(default_network, Mapping)
            or default_network.get("name") != "ecomsre-live-sandbox-v1-default"
            or not isinstance(default_network.get("labels"), Mapping)
            or default_network["labels"].get(self.bundle.environment.sandbox_label_key)
            != self.bundle.environment.sandbox_id
        ):
            raise SandboxDriftError("resolved sandbox network is not exactly owned")
        volumes = value.get("volumes")
        if not isinstance(volumes, Mapping) or set(volumes) != {
            "astronomy-db-data",
            "jaeger-data",
            "prometheus-data",
        }:
            raise SandboxDriftError("resolved Compose volume inventory drifted")
        for raw in volumes.values():
            if (
                not isinstance(raw, Mapping)
                or not isinstance(raw.get("labels"), Mapping)
                or raw["labels"].get(self.bundle.environment.sandbox_label_key)
                != self.bundle.environment.sandbox_id
            ):
                raise SandboxDriftError("resolved sandbox volume lacks exact ownership")
        discover_endpoints(value, self.bundle)

    def resolve(self) -> tuple[ResolvedSandbox, dict[str, object]]:
        result = self.runner.run(
            (*self._compose_prefix(), "config", "--format", "json"),
            cwd=self.upstream_root,
            env=self._compose_env(),
        )
        raw = json.loads(result.stdout)
        if not isinstance(raw, dict):
            raise SandboxDriftError("resolved Compose output is not an object")
        self._verify_resolved_contract(raw)
        services = raw["services"]
        assert isinstance(services, Mapping)
        images = tuple(
            sorted(
                {
                    str(item["image"])
                    for item in services.values()
                    if isinstance(item, Mapping)
                }
            )
        )
        endpoints = discover_endpoints(raw, self.bundle)
        resolved = ResolvedSandbox(
            compose_sha256=hashlib.sha256(
                json.dumps(
                    raw,
                    allow_nan=False,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                ).encode("utf-8")
            ).hexdigest(),
            services=tuple(sorted(services)),
            image_references=images,
            endpoints=endpoints,
        )
        self._resolved_payload = raw
        return resolved, raw

    def inspect_cached_images(self, resolved: ResolvedSandbox) -> CachedImageInspection:
        historical_path = self.repository_root / "config/phase0/image-lock.json"
        manifests = import_module("ecomsre.environment.manifests")
        historical = manifests.load_image_lock(historical_path)
        if historical.status.value != "LOCKED":
            raise SandboxDriftError("historical frozen image lock is not LOCKED")
        expected = {item.source_reference: item for item in historical.images}
        if (
            len(expected) != len(historical.images)
            or set(expected) != set(historical.allowed_source_references)
            or set(resolved.image_references) != set(expected)
        ):
            raise SandboxDriftError("sandbox image source set differs from frozen source set")
        inspected: list[CachedImage] = []
        for reference in resolved.image_references:
            result = self.runner.run(
                (
                    "docker",
                    "image",
                    "inspect",
                    "--platform",
                    "linux/arm64",
                    reference,
                ),
                cwd=self.repository_root,
            )
            raw = json.loads(result.stdout)
            if not isinstance(raw, list) or len(raw) != 1 or not isinstance(raw[0], Mapping):
                raise SandboxDriftError(f"cached image inspection failed for {reference}")
            image = raw[0]
            expected_item = expected[reference]
            repo_digests = image.get("RepoDigests")
            repository = reference.rsplit(":", 1)[0]
            expected_repo_digest = f"{repository}@{expected_item.image_index_digest}"
            if (
                image.get("Os") != "linux"
                or image.get("Architecture") not in {"arm64", "aarch64"}
                or image.get("Id") != expected_item.image_id
                or not isinstance(repo_digests, list)
                or expected_repo_digest not in repo_digests
            ):
                raise SandboxDriftError(f"cached image identity drifted for {reference}")
            resolved_platform_digest = expected_item.resolved_platform_digest
            if resolved_platform_digest != image.get("Id"):
                raise SandboxDriftError(
                    f"cached platform digest drifted for {reference}"
                )
            inspected.append(
                CachedImage(
                    source_reference=reference,
                    image_id=str(image["Id"]),
                    image_index_digest=expected_item.image_index_digest,
                    resolved_platform_digest=resolved_platform_digest,
                    raw_inspect_sha256=hashlib.sha256(result.stdout.encode("utf-8")).hexdigest(),
                )
            )
        return CachedImageInspection(
            historical_image_lock_sha256=file_sha256(historical_path),
            upstream_commit=self.bundle.environment.upstream_commit,
            upstream_tag=self.bundle.environment.upstream_tag,
            platform=self.bundle.environment.platform,
            images=tuple(inspected),
        )

    def verify_cached_images(self, resolved: ResolvedSandbox, control_root: Path) -> str:
        inspection = self.inspect_cached_images(resolved)
        lock = {
            "schema_version": "live-sandbox.private-image-lock.v1",
            "rotation_reason": "COMPOSE_OVERRIDE_CHANGED",
            "historical_lock_sha256": inspection.historical_image_lock_sha256,
            "historical_lock_unchanged": True,
            "upstream_commit": self.bundle.environment.upstream_commit,
            "compose_sha256": resolved.compose_sha256,
            "source_references_unchanged": True,
            "cached_images_reverified": True,
            "images": [
                {
                    "source_reference": image.source_reference,
                    "image_id": image.image_id,
                    "architecture": "arm64",
                    "platform": "linux/arm64",
                    "image_index_digest": image.image_index_digest,
                    "resolved_platform_digest": image.resolved_platform_digest,
                }
                for image in inspection.images
            ],
        }
        return write_private_json(control_root / "image-lock.json", lock, create_once=True)

    def _ids(self, arguments: tuple[str, ...]) -> frozenset[str]:
        output = self.runner.run(arguments, cwd=self.repository_root).stdout
        return frozenset(item for item in output.splitlines() if item)

    def snapshot_all_resources(self) -> DockerSnapshot:
        return DockerSnapshot(
            containers=self._ids(("docker", "ps", "-aq")),
            networks=self._ids(("docker", "network", "ls", "-q")),
            volumes=self._ids(("docker", "volume", "ls", "-q")),
        )

    def _owned_ids(self, kind: str) -> frozenset[str]:
        label_args = (
            "--filter",
            f"label=com.docker.compose.project={self.bundle.environment.compose_project}",
            "--filter",
            f"label={self.bundle.environment.sandbox_label_key}={self.bundle.environment.sandbox_id}",
        )
        if kind == "container":
            return self._ids(("docker", "ps", "-aq", *label_args))
        if kind == "network":
            return self._ids(("docker", "network", "ls", "-q", *label_args))
        if kind == "volume":
            return self._ids(("docker", "volume", "ls", "-q", *label_args))
        raise ValueError("unsupported Docker resource kind")

    def _inspect_labels(self, kind: str, identifiers: frozenset[str]) -> None:
        if not identifiers:
            return
        command = "inspect" if kind == "container" else f"{kind}"
        arguments = (
            ("docker", command, *sorted(identifiers))
            if kind == "container"
            else ("docker", command, "inspect", *sorted(identifiers))
        )
        payload = json.loads(self.runner.run(arguments, cwd=self.repository_root).stdout)
        if not isinstance(payload, list) or len(payload) != len(identifiers):
            raise ResourceOwnershipError("owned Docker resource inspection is incomplete")
        for item in payload:
            if not isinstance(item, Mapping):
                raise ResourceOwnershipError("owned Docker resource inspection is malformed")
            if kind == "container":
                config = item.get("Config")
                labels = config.get("Labels") if isinstance(config, Mapping) else None
            else:
                labels = item.get("Labels")
            if not isinstance(labels, Mapping):
                raise ResourceOwnershipError("Docker resource labels are unavailable")
            require_owned_labels({str(k): str(v) for k, v in labels.items()}, self.bundle.environment)

    def verify_owned_resources(self, *, require_complete: bool) -> dict[str, int]:
        owned = {
            "container": self._owned_ids("container"),
            "network": self._owned_ids("network"),
            "volume": self._owned_ids("volume"),
        }
        for kind, identifiers in owned.items():
            self._inspect_labels(kind, identifiers)
        if require_complete and (
            len(owned["container"]) != len(EXPECTED_SERVICES)
            or len(owned["network"]) != 1
            or len(owned["volume"]) != 3
        ):
            raise ResourceOwnershipError("owned sandbox resource inventory is incomplete")
        return {key: len(value) for key, value in owned.items()}

    def verify_ports_available(self) -> None:
        for port in (18080, 18016, 19090, 11686, 19200):
            probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                probe.bind(("127.0.0.1", port))
            except OSError as error:
                raise SandboxDriftError(f"required loopback port is occupied: {port}") from error
            finally:
                probe.close()

    def start(self) -> None:
        if self._daemon_id is None:
            raise DockerBoundaryError("local Docker authority was not established")
        if any(self.verify_owned_resources(require_complete=False).values()):
            raise ResourceOwnershipError("owned sandbox resources already exist")
        self.verify_ports_available()
        self._baseline_snapshot = self.snapshot_all_resources()
        self.runner.run(
            (
                *self._compose_prefix(),
                "up",
                "-d",
                "--pull",
                "never",
                "--no-build",
                "--wait",
                "--wait-timeout",
                "300",
            ),
            cwd=self.upstream_root,
            env=self._compose_env(),
            timeout_seconds=360,
        )
        self.verify_owned_resources(require_complete=True)

    def service_health(self) -> dict[str, bool]:
        identifiers = self._owned_ids("container")
        if len(identifiers) != len(EXPECTED_SERVICES):
            return {name: False for name in EXPECTED_SERVICES}
        payload = json.loads(
            self.runner.run(
                ("docker", "inspect", *sorted(identifiers)), cwd=self.repository_root
            ).stdout
        )
        output: dict[str, bool] = {}
        for item in payload:
            if not isinstance(item, Mapping):
                continue
            config = item.get("Config")
            labels = config.get("Labels") if isinstance(config, Mapping) else None
            state = item.get("State")
            if not isinstance(labels, Mapping) or not isinstance(state, Mapping):
                continue
            service = labels.get("com.docker.compose.service")
            health = state.get("Health")
            healthy = state.get("Running") is True and (
                not isinstance(health, Mapping) or health.get("Status") == "healthy"
            )
            if isinstance(service, str):
                output[service] = healthy
        return {name: output.get(name, False) for name in EXPECTED_SERVICES}

    def wait_healthy(self, *, timeout_seconds: float = 300) -> dict[str, bool]:
        deadline = time.monotonic() + timeout_seconds
        while True:
            health = self.service_health()
            if health and all(health.values()):
                return health
            if time.monotonic() >= deadline:
                failed = tuple(name for name, value in health.items() if not value)
                raise RuntimeError(f"sandbox services did not become healthy: {failed}")
            time.sleep(5)

    def cleanup(self, *, baseline_restored: bool) -> CleanupResult:
        if self._baseline_snapshot is None:
            raise RuntimeError("cleanup lacks a pre-start Docker snapshot")
        self.verify_owned_resources(require_complete=False)
        self.runner.run(
            (
                *self._compose_prefix(),
                "down",
                "--volumes",
                "--remove-orphans",
                "--timeout",
                "30",
            ),
            cwd=self.upstream_root,
            env=self._compose_env(),
            timeout_seconds=180,
        )
        counts = self.verify_owned_resources(require_complete=False)
        after = self.snapshot_all_resources()
        changed = after != self._baseline_snapshot
        clean = baseline_restored and not any(counts.values()) and not changed
        return CleanupResult(
            baseline_restored=baseline_restored,
            owned_containers=counts["container"],
            owned_networks=counts["network"],
            owned_volumes=counts["volume"],
            non_owned_resources_changed=changed,
            verdict="CLEAN" if clean else "BLOCKED",
        )

    def manual_cleanup_command(self) -> str:
        return " ".join(
            (
                "DEMO_VERSION=3.0.0",
                "IMAGE_VERSION=3.0.0",
                f"ECOMSRE_SANDBOX_ID={self.bundle.environment.sandbox_id}",
                f"SANDBOX_FLAGD_DIR={self.flagd_directory}",
                f"SANDBOX_COLLECTOR_CONFIG={self.config_root / 'otelcol-sandbox.yml'}",
                *self._compose_prefix(),
                "down --volumes --remove-orphans --timeout 30",
            )
        )


__all__ = [
    "DockerBoundaryError",
    "DockerSnapshot",
    "ExactCommandRunner",
    "EXPECTED_SERVICES",
    "ResourceOwnershipError",
    "SandboxDriftError",
    "SandboxEnvironment",
    "discover_endpoints",
    "require_local_docker_endpoint",
    "require_owned_labels",
]
