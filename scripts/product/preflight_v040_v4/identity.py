"""Precomputed process semantics and birth-bound immutable identities."""

from __future__ import annotations

from copy import deepcopy
import shlex
from typing import Any

from .common import LABEL, ROLE_LABEL, require, digest


def argv(value: Any) -> list[str] | None:
    if value is None:
        return None
    if isinstance(value, str):
        return shlex.split(value)
    require(
        isinstance(value, list) and all(isinstance(x, str) for x in value),
        "INVALID_ARGV",
    )
    return list(value)


def effective_process(
    service: dict[str, Any], config: dict[str, Any]
) -> dict[str, Any]:
    """Compose null inherits; explicit entrypoint clears an inherited image Cmd."""
    entry = argv(service.get("entrypoint"))
    command = argv(service.get("command"))
    image_entry, image_command = argv(config.get("Entrypoint")), argv(config.get("Cmd"))
    return {
        "compose_declared_entrypoint": service.get("entrypoint"),
        "compose_declared_command": service.get("command"),
        "image_default_entrypoint": image_entry,
        "image_default_command": image_command,
        "resolved_effective_entrypoint": image_entry if entry is None else entry,
        "resolved_effective_command": command
        if command is not None
        else (image_command if entry is None else []),
    }


def process_matches(record: dict[str, Any], process: dict[str, Any]) -> None:
    for actual, expected in [
        ("Entrypoint", "resolved_effective_entrypoint"),
        ("Cmd", "resolved_effective_command"),
    ]:
        require(
            (record["Config"].get(actual) or []) == (process[expected] or []),
            "COMMAND_DRIFT:" + actual,
        )


def immutable(record: dict[str, Any]) -> dict[str, Any]:
    """Retain all config and host config, apart from the separately gated OOM value."""
    host = deepcopy(record["HostConfig"])
    host.pop("OomKillDisable", None)
    return {
        "Id": record["Id"],
        "Created": record["Created"],
        "Image": record["Image"],
        "Name": record["Name"],
        "Config": record["Config"],
        "HostConfig": host,
        "Mounts": sorted(record["Mounts"], key=lambda m: m["Destination"]),
        "ImageManifestDescriptor": record.get("ImageManifestDescriptor"),
        "network_names": sorted(record.get("NetworkSettings", {}).get("Networks", {})),
    }


def lifecycle(
    birth: dict[str, Any],
    current: dict[str, Any],
    *,
    role: str,
    stage: str,
    oom_evidence: dict[str, Any] | None = None,
    binding: dict[str, Any] | None = None,
) -> None:
    require(immutable(birth) == immutable(current), "IMMUTABLE_SECURITY_DRIFT:" + role)
    before, after = (
        birth["HostConfig"].get("OomKillDisable"),
        current["HostConfig"].get("OomKillDisable"),
    )
    if before is not after:
        require(
            role == "kafka-volume-probe"
            and stage in ("running", "stopped")
            and before is False
            and after is None
            and isinstance(oom_evidence, dict)
            and oom_evidence.get("container_id") == current["Id"]
            and oom_evidence.get("binding_digest") == digest(binding)
            and oom_evidence.get("immutable_digest") == digest(immutable(current))
            and oom_evidence.get("started_at") == current["State"].get("StartedAt"),
            "OOM_REPRESENTATION_DRIFT:" + role,
        )
    require(current.get("RestartCount", 0) == 0, "UNAUTHORIZED_RESTART:" + role)
    state = current["State"]
    require(
        not state.get("Dead")
        and not state.get("Paused")
        and not state.get("Restarting"),
        "LIFECYCLE_DRIFT:" + role,
    )
    if stage == "running":
        require(state.get("Running") is True, "NOT_RUNNING:" + role)
    elif stage == "stopped":
        require(state.get("Running") is False, "NOT_STOPPED:" + role)
    else:
        require(
            stage == "created" and state.get("Status") == "created",
            "INVALID_STAGE:" + role,
        )


def service_health(
    records: list[dict[str, Any]],
    *,
    project: str,
    attempt: str,
    births: dict[str, str],
    readiness: dict[str, bool],
) -> dict[str, Any]:
    roles: dict[str, Any] = {}
    for row in records:
        labels = row.get("Config", {}).get("Labels") or {}
        if labels.get("com.docker.compose.project") != project:
            continue
        role = labels.get("com.docker.compose.service")
        require(role in births, "UNEXPECTED_SANDBOX_ROLE:" + str(role))
        assert isinstance(role, str)
        require(role not in roles, "DUPLICATE_SANDBOX_ROLE:" + role)
        require(
            labels.get(LABEL) == attempt and labels.get(ROLE_LABEL) == role,
            "SANDBOX_LABEL_DRIFT:" + role,
        )
        require(row.get("Id") == births[role], "SANDBOX_ID_DRIFT:" + role)
        state = row.get("State")
        require(
            isinstance(state, dict) and isinstance(state.get("Running"), bool),
            "MALFORMED_STATE:" + role,
        )
        assert isinstance(state, dict)
        health = state.get("Health")
        status = health.get("Status") if isinstance(health, dict) else "NOT_DECLARED"
        okay = state["Running"] and not any(
            state.get(k) for k in ("Paused", "Restarting", "Dead", "OOMKilled")
        )
        okay = okay and (
            status == "healthy" if health is not None else readiness.get(role) is True
        )
        roles[role] = {
            "id": row["Id"],
            "running": state["Running"],
            "health": status,
            "role_readiness": readiness.get(role),
            "ready": okay,
            "restart_count": row.get("RestartCount"),
            "raw_state": state,
        }
    for role in births:
        require(role in roles, "MISSING_SANDBOX_ROLE:" + role)
    return roles


def stable_running(first: dict[str, Any], current: dict[str, Any]) -> None:
    require(immutable(first) == immutable(current), "IMMUTABLE_SECURITY_DRIFT")
    require(
        first["RestartCount"] == current["RestartCount"] == 0, "UNAUTHORIZED_RESTART"
    )
    require(
        first["State"]["Running"] is True
        and current["State"]["Running"] is True
        and first["State"]["Pid"] == current["State"]["Pid"]
        and first["State"]["StartedAt"] == current["State"]["StartedAt"],
        "PROCESS_LIFETIME_DRIFT",
    )
    a = first["NetworkSettings"]["Networks"]
    b = current["NetworkSettings"]["Networks"]
    require(a == b, "RUNNING_ENDPOINT_DRIFT")


def validate_network_stage(
    record: dict[str, Any], expected: dict[str, str], stage: str
) -> None:
    actual = record["NetworkSettings"]["Networks"]
    require(set(actual) == set(expected), "NETWORK_SET_DRIFT")
    for name, value in actual.items():
        observed_id = value.get("NetworkID")
        require(
            observed_id == expected[name] or (stage == "created" and observed_id == ""),
            "NETWORK_ID_DRIFT:" + name,
        )
        endpoint = value.get("EndpointID")
        require(isinstance(endpoint, str), "NETWORK_ENDPOINT_MALFORMED")
        if stage in ("created", "stopped"):
            require(endpoint == "", "NETWORK_ENDPOINT_LIFETIME:" + name)
        else:
            require(
                stage == "running" and len(endpoint) == 64,
                "NETWORK_ENDPOINT_LIFETIME:" + name,
            )
