"""Read-only Docker Engine adapters for DTA v2 runtime and resource evidence."""

from __future__ import annotations

from collections.abc import Callable, Mapping
import http.client
import json
import math
import socket
import time
from typing import Protocol, cast
from urllib.parse import urlencode

from ecomsre.dta_v2.read_tools import ReadBackendFailure
from ecomsre.dta_v2.tool_contracts import (
    EndpointState,
    HealthState,
    InspectResourceUsageRequest,
    InspectServiceRuntimeRequest,
    ResourceSample,
    ResourceUsageRecord,
    RuntimeRecord,
    RuntimeState,
    ToolErrorCode,
)


class DockerJsonClient(Protocol):
    def get_json(self, path: str) -> object: ...


class _UnixSocketHTTPConnection(http.client.HTTPConnection):
    def __init__(self, socket_path: str, *, timeout: float) -> None:
        super().__init__("localhost", timeout=timeout)
        self.socket_path = socket_path

    def connect(self) -> None:
        connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        connection.settimeout(self.timeout)
        connection.connect(self.socket_path)
        self.sock = connection


class UnixSocketDockerClient:
    """Minimal GET-only Docker Engine HTTP client over one local Unix socket."""

    def __init__(self, socket_path: str, *, timeout_seconds: float) -> None:
        if not socket_path.startswith("/") or "\x00" in socket_path:
            raise ValueError("Docker Unix socket path is invalid")
        self.socket_path = socket_path
        self.timeout_seconds = timeout_seconds

    def get_json(self, path: str) -> object:
        if not path.startswith("/") or "\x00" in path or ".." in path:
            raise ValueError("Docker Engine request path is invalid")
        connection = _UnixSocketHTTPConnection(
            self.socket_path, timeout=self.timeout_seconds
        )
        try:
            connection.request("GET", path, headers={"Accept": "application/json"})
            response = connection.getresponse()
            payload = response.read(10_000_001)
            if len(payload) > 10_000_000:
                raise ValueError("Docker Engine response exceeds bounded size")
            if response.status < 200 or response.status >= 300:
                raise RuntimeError(f"Docker Engine returned HTTP {response.status}")
            return json.loads(payload.decode("utf-8"))
        finally:
            connection.close()


class DockerReadAdapter:
    def __init__(
        self,
        *,
        docker: DockerJsonClient,
        compose_project: str,
        sandbox_label_key: str,
        sandbox_label_value: str,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.docker = docker
        self.compose_project = compose_project
        self.sandbox_label_key = sandbox_label_key
        self.sandbox_label_value = sandbox_label_value
        self.sleep = sleep

    def inspect_runtime(
        self, request: InspectServiceRuntimeRequest
    ) -> tuple[RuntimeRecord, ...]:
        return tuple(
            self._runtime_for(service) for service in request.services[: request.max_results]
        )

    def inspect_resources(
        self, request: InspectResourceUsageRequest
    ) -> tuple[ResourceUsageRecord, ...]:
        identities: dict[str, str] = {}
        for service in request.services:
            runtime = self._runtime_for(service)
            if not runtime.owned_container_present:
                raise ReadBackendFailure(ToolErrorCode.OWNERSHIP_NOT_PROVEN)
            if runtime.state is not RuntimeState.RUNNING:
                raise ReadBackendFailure(ToolErrorCode.SOURCE_UNAVAILABLE)
            identity = self._owned_container_identity(service)
            if identity is None:
                raise ReadBackendFailure(ToolErrorCode.OWNERSHIP_NOT_PROVEN)
            identities[service] = identity

        samples: dict[str, list[ResourceSample]] = {
            service: [] for service in request.services
        }
        interval = request.sampling_window_seconds / (request.sample_count - 1)
        for index in range(request.sample_count):
            if index:
                self.sleep(interval)
            offset_ms = (
                request.sampling_window_seconds * 1000 * index
                // (request.sample_count - 1)
            )
            for service, identity in identities.items():
                raw = self.docker.get_json(
                    f"/containers/{identity}/stats?stream=false"
                )
                cpu_percent, memory_bytes = _parse_stats(raw)
                samples[service].append(
                    ResourceSample(
                        offset_ms=offset_ms,
                        cpu_percent=cpu_percent,
                        memory_bytes=memory_bytes,
                    )
                )

        output: list[ResourceUsageRecord] = []
        for service in request.services:
            service_samples = tuple(samples[service])
            elapsed_seconds = max(
                (service_samples[-1].offset_ms - service_samples[0].offset_ms) / 1000,
                float(request.sampling_window_seconds),
            )
            slope = (
                service_samples[-1].memory_bytes - service_samples[0].memory_bytes
            ) / elapsed_seconds
            output.append(
                ResourceUsageRecord(
                    logical_service=service,
                    sampling_window_seconds=request.sampling_window_seconds,
                    samples=service_samples,
                    memory_slope_bytes_per_second=float(slope),
                )
            )
        return tuple(output)

    def _runtime_for(self, service: str) -> RuntimeRecord:
        identity = self._owned_container_identity(service)
        if identity is None:
            return RuntimeRecord(
                logical_service=service,
                owned_container_present=False,
                state=RuntimeState.ABSENT,
                health=HealthState.UNKNOWN,
                restart_count=0,
                exit_code=None,
                endpoint_probe_performed=False,
                endpoint_state=EndpointState.NOT_APPLICABLE,
            )
        inspect = _mapping(
            self.docker.get_json(f"/containers/{identity}/json"),
            "Docker inspect response",
        )
        config = _mapping(inspect.get("Config", {}), "Docker inspect config")
        labels = _string_mapping(config.get("Labels", {}), "Docker inspect labels")
        self._require_labels(labels, service=service)
        state = _mapping(inspect.get("State"), "Docker inspect state")
        state_text = str(state.get("Status") or "").casefold()
        runtime_state = {
            "running": RuntimeState.RUNNING,
            "exited": RuntimeState.EXITED,
        }.get(state_text, RuntimeState.OTHER)
        health_raw = state.get("Health")
        health_text = ""
        if isinstance(health_raw, Mapping):
            health_text = str(health_raw.get("Status") or "").casefold()
        health = {
            "healthy": HealthState.HEALTHY,
            "unhealthy": HealthState.UNHEALTHY,
            "starting": HealthState.STARTING,
        }.get(
            health_text,
            HealthState.NOT_CONFIGURED if not health_text else HealthState.UNKNOWN,
        )
        exit_code_raw = state.get("ExitCode")
        exit_code = exit_code_raw if isinstance(exit_code_raw, int) else None
        restart_raw = inspect.get("RestartCount", 0)
        if isinstance(restart_raw, bool) or not isinstance(restart_raw, int):
            raise ValueError("Docker restart count is invalid")
        return RuntimeRecord(
            logical_service=service,
            owned_container_present=True,
            state=runtime_state,
            health=health,
            restart_count=restart_raw,
            exit_code=exit_code,
            endpoint_probe_performed=False,
            endpoint_state=EndpointState.UNKNOWN,
        )

    def _owned_container_identity(self, service: str) -> str | None:
        labels = (
            f"com.docker.compose.project={self.compose_project}",
            f"{self.sandbox_label_key}={self.sandbox_label_value}",
            f"com.docker.compose.service={service}",
        )
        query = urlencode(
            {"all": "1", "filters": json.dumps({"label": labels}, separators=(",", ":"))}
        )
        raw = self.docker.get_json(f"/containers/json?{query}")
        if not isinstance(raw, list):
            raise ValueError("Docker container list is invalid")
        owned: list[str] = []
        for item_raw in raw:
            item = _mapping(item_raw, "Docker container summary")
            item_labels = _string_mapping(item.get("Labels", {}), "Docker labels")
            self._require_labels(item_labels, service=service)
            identity = item.get("Id")
            if not isinstance(identity, str) or not 12 <= len(identity) <= 64:
                raise ValueError("Docker container identity is invalid")
            owned.append(identity)
        if len(owned) > 1:
            raise ReadBackendFailure(ToolErrorCode.AMBIGUOUS_OWNED_RUNTIME)
        return owned[0] if owned else None

    def _owned_container_started_at(self, service: str, identity: str) -> str:
        if self._owned_container_identity(service) != identity:
            raise ReadBackendFailure(ToolErrorCode.OWNERSHIP_NOT_PROVEN)
        inspect = _mapping(
            self.docker.get_json(f"/containers/{identity}/json"),
            "Docker inspect response",
        )
        config = _mapping(inspect.get("Config", {}), "Docker inspect config")
        labels = _string_mapping(config.get("Labels", {}), "Docker inspect labels")
        self._require_labels(labels, service=service)
        state = _mapping(inspect.get("State"), "Docker inspect state")
        started_at = state.get("StartedAt")
        if (
            not isinstance(started_at, str)
            or started_at != started_at.strip()
            or not 20 <= len(started_at) <= 64
        ):
            raise ValueError("Docker StartedAt is invalid")
        return started_at

    def _require_labels(self, labels: Mapping[str, str], *, service: str) -> None:
        expected = {
            "com.docker.compose.project": self.compose_project,
            self.sandbox_label_key: self.sandbox_label_value,
            "com.docker.compose.service": service,
        }
        if any(labels.get(key) != value for key, value in expected.items()):
            raise ReadBackendFailure(ToolErrorCode.OWNERSHIP_NOT_PROVEN)


def _mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an object")
    return cast(Mapping[str, object], value)


def _string_mapping(value: object, label: str) -> Mapping[str, str]:
    mapping = _mapping(value, label)
    if any(not isinstance(key, str) or not isinstance(item, str) for key, item in mapping.items()):
        raise ValueError(f"{label} must contain strings")
    return cast(Mapping[str, str], mapping)


def _parse_stats(value: object) -> tuple[float, int]:
    payload = _mapping(value, "Docker stats response")
    cpu = _mapping(payload.get("cpu_stats"), "Docker cpu_stats")
    previous = _mapping(payload.get("precpu_stats"), "Docker precpu_stats")
    usage = _mapping(cpu.get("cpu_usage"), "Docker cpu usage")
    previous_usage = _mapping(previous.get("cpu_usage"), "Docker previous cpu usage")
    total = _number(usage.get("total_usage"), "total cpu usage")
    previous_total = _number(previous_usage.get("total_usage"), "previous cpu usage")
    system = _number(cpu.get("system_cpu_usage"), "system cpu usage")
    previous_system = _number(previous.get("system_cpu_usage"), "previous system cpu usage")
    online_raw = cpu.get("online_cpus")
    if not isinstance(online_raw, int) or isinstance(online_raw, bool) or online_raw < 1:
        per_cpu = usage.get("percpu_usage")
        online_raw = len(per_cpu) if isinstance(per_cpu, list) and per_cpu else 1
    cpu_delta = max(0.0, total - previous_total)
    system_delta = max(0.0, system - previous_system)
    percent = 0.0 if system_delta == 0 else cpu_delta / system_delta * online_raw * 100
    memory = _mapping(payload.get("memory_stats"), "Docker memory_stats")
    memory_usage = _number(memory.get("usage"), "memory usage")
    stats = memory.get("stats", {})
    cache = 0.0
    if isinstance(stats, Mapping):
        cache_raw = stats.get("inactive_file", stats.get("cache", 0))
        cache = _number(cache_raw, "memory cache")
    bounded_memory = max(0, round(memory_usage - cache))
    if not math.isfinite(percent):
        raise ValueError("Docker CPU percent is not finite")
    return float(max(0.0, percent)), bounded_memory


def _number(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} is invalid")
    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise ValueError(f"{label} is invalid")
    return number
