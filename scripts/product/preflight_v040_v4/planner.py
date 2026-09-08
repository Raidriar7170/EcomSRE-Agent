"""Pure plan validation after actual Compose expansion; observations mint no authority."""

from __future__ import annotations
from copy import deepcopy
from pathlib import Path
import re
import os
from typing import Any
from .common import GOAL_SHA, LABEL, ROLE_LABEL, GOAL_LABEL, KAFKA_PATHS, PORTS, require
from .identity import effective_process


def labels(attempt: str, role: str) -> dict[str, str]:
    require(re.fullmatch("[a-f0-9]{32}", attempt) is not None, "ATTEMPT_ID_INVALID")
    return {
        LABEL: attempt,
        ROLE_LABEL: role,
        GOAL_LABEL: GOAL_SHA,
        "io.ecomsre.preflight.v4.owner": "live-harness",
    }


def prepare_sandbox(
    source: dict[str, Any], images: dict[str, Any], attempt: str, flag_root: Path
) -> dict[str, Any]:
    model = deepcopy(source)
    prefix = "ecomsre-v4-" + attempt
    model["name"] = prefix + "-sandbox"
    for role, service in model["services"].items():
        service["container_name"] = prefix + "-" + role
        service["hostname"] = role
        service["labels"] = {**service.get("labels", {}), **labels(attempt, role)}
        service["image"] = images[service["image"]]["runtime_reference"]
        service["platform"] = "linux/arm64"
        service["pull_policy"] = "never"
        service.pop("build", None)
        # Neither application failure nor Docker policy may silently restart a role.
        service["restart"] = "no"
        if role == "flagd":
            service["user"] = f"{os.getuid()}:{os.getgid()}"
        for mount in service.get("volumes", []):
            if mount["type"] == "bind":
                mount["bind"] = {"create_host_path": False}
                if role in ("flagd", "flagd-ui"):
                    mount["source"] = str(flag_root)
        for port in service.get("ports", []):
            require(
                port.get("host_ip") == "127.0.0.1" and int(port["published"]) in PORTS,
                "PORT_UNBOUND",
            )
    for path in KAFKA_PATHS:
        key = "kafka-" + path.rsplit("/", 1)[1]
        model["volumes"][key] = {}
        model["services"]["kafka"].setdefault("volumes", []).append(
            {
                "type": "volume",
                "source": key,
                "target": path,
                "volume": {"nocopy": False},
            }
        )
    for key, volume in model["volumes"].items():
        volume.clear()
        volume.update({"name": prefix + "-" + key, "external": True})
    for key, network in model["networks"].items():
        network.clear()
        network.update({"name": prefix + "-" + key, "external": True})
    return model


def validate_model(
    model: dict[str, Any],
    images: dict[str, Any],
    attempt: str,
    allowed_bind_sources: set[str],
) -> dict[str, Any]:
    by_ref = {image["runtime_reference"]: image for image in images.values()}
    services = model["services"]
    roles = {}
    require(len(services) == 28, "SANDBOX_ROLE_SET")
    for role, service in services.items():
        require(
            service["image"] in by_ref and service["platform"] == "linux/arm64",
            "UNRESOLVED_IMAGE:" + role,
        )
        image = by_ref[service["image"]]
        require(
            not service.get("privileged")
            and not service.get("devices")
            and not service.get("cap_add")
            and service.get("network_mode") not in ("host", "service:", "container:"),
            "UNSAFE_CONTAINER:" + role,
        )
        require(
            service["labels"].items() >= labels(attempt, role).items(),
            "OWNERSHIP_LABELS:" + role,
        )
        mounts = service.get("volumes", [])
        destinations = [m["target"] for m in mounts]
        require(len(destinations) == len(set(destinations)), "DUPLICATE_MOUNT:" + role)
        require(
            set(image["config"].get("Volumes") or {}) <= set(destinations),
            "ANONYMOUS_VOLUME:" + role,
        )
        for mount in mounts:
            if mount["type"] == "bind":
                require(
                    mount["source"] in allowed_bind_sources
                    and mount.get("bind", {}).get("create_host_path") is False,
                    "UNBOUND_BIND:" + role,
                )
            else:
                require(
                    mount["type"] == "volume"
                    and mount.get("source") in model["volumes"],
                    "ANONYMOUS_VOLUME:" + role,
                )
        require(
            set(service.get("networks", {})) <= set(model["networks"]),
            "UNBOUND_NETWORK:" + role,
        )
        roles[role] = {
            "service": service,
            "image_id": image["image_id"],
            "platform_digest": image["platform_digest"],
            "process": effective_process(service, image["config"]),
        }
    return {
        "attempt_id": attempt,
        "project": model["name"],
        "roles": roles,
        "networks": {
            k: {
                "name": v["name"],
                "driver": "bridge",
                "labels": labels(attempt, "sandbox-network-" + k),
            }
            for k, v in model["networks"].items()
        },
        "volumes": {
            k: {
                "name": v["name"],
                "driver": "local",
                "labels": labels(attempt, "sandbox-volume-" + k),
            }
            for k, v in model["volumes"].items()
        },
    }
