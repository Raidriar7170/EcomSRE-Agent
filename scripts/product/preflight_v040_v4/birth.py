"""Validate newly-created resources against pre-mutation plans before minting identity."""

from __future__ import annotations
from typing import Any
from .common import require
from .identity import process_matches, validate_network_stage


def environment(entries: list[str]) -> dict[str, str]:
    pairs = [item.split("=", 1) for item in entries]
    require(
        all(len(p) == 2 for p in pairs) and len({p[0] for p in pairs}) == len(pairs),
        "ENVIRONMENT_MALFORMED",
    )
    return {p[0]: p[1] for p in pairs}


def validate_image_identity(row: dict[str, Any], planned: dict[str, Any]) -> None:
    service = planned["service"]
    role = service["labels"]["io.ecomsre.preflight.v4.role"]
    image_allowed = row["Image"] in (planned["image_id"], planned["platform_digest"])
    descriptor = row.get("ImageManifestDescriptor")
    if not image_allowed:
        # Engine container inspect may retain the exact index used to create,
        # while platform-selective image inspect returns its selected manifest.
        image_allowed = (
            row["Image"] == service["image"]
            and service["image"].startswith("sha256:")
            and len(service["image"]) == 71
            and isinstance(descriptor, dict)
            and descriptor.get("digest") == planned["platform_digest"]
            and descriptor.get("platform") == {"os": "linux", "architecture": "arm64"}
        )
    require(image_allowed, "BIRTH_IMAGE:" + role)
    if descriptor:
        require(
            descriptor["digest"] == planned["platform_digest"], "BIRTH_PLATFORM:" + role
        )
    require(
        row["Config"].get("Image") == service["image"], "BIRTH_IMAGE_REFERENCE:" + role
    )


def validate_container(
    row: dict[str, Any],
    planned: dict[str, Any],
    image_config: dict[str, Any],
    network_ids: dict[str, str],
    volumes: dict[str, dict[str, Any]],
    *,
    project: str | None,
    config_hash: str | None = None,
) -> None:
    service = planned["service"]
    role = service["labels"]["io.ecomsre.preflight.v4.role"]
    require(
        len(row["Id"]) == 64
        and bool(row["Created"])
        and row["Name"] == "/" + service["container_name"],
        "BIRTH_NAME_OR_ID:" + role,
    )
    validate_image_identity(row, planned)
    config = row["Config"]
    host = row["HostConfig"]
    process_matches(row, planned["process"])
    require(
        config.get("User", "")
        == str(service.get("user", image_config.get("User", ""))),
        "BIRTH_USER:" + role,
    )
    require(
        config.get("WorkingDir", "")
        == service.get("working_dir", image_config.get("WorkingDir", "")),
        "BIRTH_WORKDIR:" + role,
    )
    require(config.get("Hostname") == service["hostname"], "BIRTH_HOSTNAME:" + role)
    env = {
        **environment(image_config.get("Env") or []),
        **{
            k: str(v)
            for k, v in service.get("environment", {}).items()
            if v is not None
        },
    }
    require(environment(config.get("Env") or []) == env, "BIRTH_ENVIRONMENT:" + role)
    actual_labels = config.get("Labels") or {}
    require(
        all(
            actual_labels.get(k) == v
            for k, v in {
                **(image_config.get("Labels") or {}),
                **service["labels"],
            }.items()
        ),
        "BIRTH_LABELS:" + role,
    )
    if project:
        require(
            actual_labels.get("com.docker.compose.project") == project
            and actual_labels.get("com.docker.compose.service") == role
            and actual_labels.get("com.docker.compose.config-hash") == config_hash
            and actual_labels.get("com.docker.compose.container-number") == "1"
            and actual_labels.get("com.docker.compose.oneoff") == "False",
            "BIRTH_COMPOSE_LABELS:" + role,
        )
    else:
        require(
            not any(k.startswith("com.docker.compose.") for k in actual_labels),
            "PROBE_COMPOSE_LABEL_FORBIDDEN",
        )
    require(
        host.get("Privileged") is False
        and not host.get("Devices")
        and not host.get("DeviceRequests")
        and not host.get("DeviceCgroupRules")
        and not host.get("VolumesFrom")
        and not host.get("Links")
        and not host.get("PidMode")
        and host.get("IpcMode") in ("private", "")
        and not host.get("UsernsMode")
        and not host.get("UTSMode")
        and not host.get("GroupAdd"),
        "BIRTH_HOST_AUTHORITY:" + role,
    )
    require(
        not host.get("CapAdd")
        and sorted(host.get("CapDrop") or []) == sorted(service.get("cap_drop") or []),
        "BIRTH_CAPABILITIES:" + role,
    )
    require(
        host.get("ReadonlyRootfs") == bool(service.get("read_only", False)),
        "BIRTH_ROOTFS:" + role,
    )
    require(
        sorted(host.get("SecurityOpt") or [])
        == sorted(service.get("security_opt") or []),
        "BIRTH_SECURITY_OPTIONS:" + role,
    )
    require(
        host.get("RestartPolicy", {}).get("Name") in ("no", "")
        and host.get("RestartPolicy", {}).get("MaximumRetryCount", 0) == 0,
        "BIRTH_RESTART_POLICY:" + role,
    )
    require(
        host.get("NetworkMode") == service.get("network_mode", next(iter(network_ids))),
        "BIRTH_NETWORK_MODE:" + role,
    )
    expected_ports: dict[str, list[dict[str, str]]] = {}
    for p in service.get("ports", []):
        expected_ports.setdefault(
            str(p["target"]) + "/" + p.get("protocol", "tcp"), []
        ).append({"HostIp": p["host_ip"], "HostPort": str(p["published"])})
    require((host.get("PortBindings") or {}) == expected_ports, "BIRTH_PORTS:" + role)
    mounts = {m["Destination"]: m for m in row["Mounts"]}
    expected_mounts = {m["target"]: m for m in service.get("volumes", [])}
    # Docker reports tmpfs in HostConfig, not as volume ownership.
    require(set(mounts) == set(expected_mounts), "BIRTH_MOUNT_SET:" + role)
    for target, expected in expected_mounts.items():
        actual = mounts[target]
        require(
            actual["Type"] == expected["type"]
            and actual["RW"] == (not expected.get("read_only", False)),
            "BIRTH_MOUNT_MODE:" + role,
        )
        if expected["type"] == "bind":
            require(actual["Source"] == expected["source"], "BIRTH_BIND_SOURCE:" + role)
        else:
            volume = volumes[expected["source"]]
            require(
                actual["Name"] == volume["Name"]
                and actual["Source"] == volume["Mountpoint"]
                and actual["Driver"] == volume["Driver"],
                "BIRTH_VOLUME_SOURCE:" + role,
            )
    validate_network_stage(row, network_ids, "created")
    require(
        row["State"]["Status"] == "created"
        and row["State"]["Running"] is False
        and row["RestartCount"] == 0,
        "BIRTH_NOT_FRESH:" + role,
    )


def validate_storage(kind: str, row: dict[str, Any], plan: dict[str, Any]) -> None:
    require(
        row["Name"] == plan["name"]
        and row["Driver"] == plan["driver"]
        and (row.get("Labels") or {}) == plan["labels"]
        and not row.get("Options"),
        "STORAGE_BIRTH_DRIFT",
    )
    if kind == "network":
        require(
            len(row["Id"]) == 64
            and not row.get("Containers")
            and not row.get("Internal")
            and not row.get("Ingress")
            and not row.get("Attachable")
            and not row.get("EnableIPv6"),
            "NETWORK_BIRTH_DRIFT",
        )
    else:
        require(
            kind == "volume"
            and row.get("Scope") == "local"
            and bool(row.get("CreatedAt")),
            "VOLUME_BIRTH_DRIFT",
        )
