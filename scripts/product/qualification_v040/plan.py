"""Build a pre-start resource-role plan from verified Compose and image inputs."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from scripts.product.qualification_v040.guard import (
    QUAL_LABEL,
    ZERO_COUNTS,
    identity,
    require,
)
from scripts.product.qualification_v040.volumes import KAFKA_PATHS

SANDBOX_PROJECT = "ecomsre-live-sandbox-v1"
PRODUCT_PROJECT = "ecomsre-product-v040"


def transform_sandbox(original: dict[str, Any], qualification: str) -> dict[str, Any]:
    result = deepcopy(original)
    require(
        len(result["services"]) == 28
        and set(result["volumes"])
        == {"astronomy-db-data", "jaeger-data", "prometheus-data"},
        "SANDBOX_PLAN_DRIFT",
    )
    kafka = result["services"]["kafka"]
    require(len(kafka.get("volumes", [])) == 1, "KAFKA_PLAN_DRIFT")
    for suffix, target in zip(("secrets", "config", "data"), KAFKA_PATHS, strict=True):
        key = "qualification-kafka-" + suffix
        result["volumes"][key] = {
            "name": "ecomsre-v040-" + qualification + "-" + suffix
        }
        kafka["volumes"].append(
            {
                "type": "volume",
                "source": key,
                "target": target,
                "read_only": False,
                "volume": {"nocopy": False},
            }
        )
    for key, volume in result["volumes"].items():
        volume["name"] = "ecomsre-v040-" + qualification + "-" + key
        volume.setdefault("labels", {})[QUAL_LABEL] = qualification
        volume["labels"]["com.docker.compose.project"] = SANDBOX_PROJECT
        volume["labels"]["io.ecomsre.sandbox.id"] = (
            "e477da43-27e7-4c55-8491-1d45cda03000"
        )
    for collection in ("services", "networks"):
        for value in result[collection].values():
            value.setdefault("labels", {})[QUAL_LABEL] = qualification
    return result


def stage_roles(
    sandbox: dict[str, Any], product: dict[str, Any]
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    stages: list[dict[str, Any]] = []
    present: list[str] = []
    births = {}

    def step(name: str, add: list[str] = [], remove: list[str] = []) -> None:
        if name.startswith("AFTER_"):
            stages.append(
                {
                    "name": name.replace("AFTER_", "BEFORE_", 1),
                    "present_roles": sorted(present),
                }
            )
        for key in add:
            require(key not in births, "INVALID_STAGE_PLAN")
            births[key] = name
            present.append(key)
        for key in remove:
            present.remove(key)
        stages.append({"name": name, "present_roles": sorted(present)})

    step("INITIAL")
    volumes = ["sandbox/volume/" + n for n in sorted(sandbox["volumes"])]
    for i, key in enumerate(volumes):
        step("AFTER_VOLUME_" + str(i), [key])
    step("AFTER_COPYUP_CREATE", ["probe/container/kafka-volume-probe"])
    step("AFTER_COPYUP_MEASUREMENT")
    step("AFTER_SENTINEL")
    networks = ["sandbox/network/" + n for n in sorted(sandbox["networks"])] + [
        "product/network/" + n for n in sorted(product["networks"])
    ]
    for i, key in enumerate(networks):
        step("AFTER_NETWORK_" + str(i), [key])
    sandbox_roles = ["sandbox/container/" + n for n in sorted(sandbox["services"])]
    step("AFTER_SANDBOX_START", sandbox_roles)
    step("AFTER_KAFKA_IDENTITY")
    step("AFTER_PROBE_REMOVE", remove=["probe/container/kafka-volume-probe"])
    step("AFTER_PRODUCT_API_START", ["product/container/api"])
    step(
        "AFTER_PRODUCT_READERS_START",
        ["product/container/worker", "product/container/remediation-observer"],
    )
    for name in (
        "AFTER_CONNECTOR_VERIFICATION",
        "AFTER_WARMUP",
        "AFTER_HEALTHY_CONTROL",
        "AFTER_BASELINE",
        "AFTER_NO_INCIDENT",
        "AFTER_NETWORK_DENIAL",
        "BEFORE_CLEANUP",
        "AFTER_OWNED_STOP",
    ):
        step(name)
    step("AFTER_CONTAINERS_REMOVE", remove=[r for r in present if "/container/" in r])
    step("AFTER_NETWORKS_REMOVE", remove=[r for r in present if "/network/" in r])
    step("AFTER_VOLUMES_REMOVE", remove=list(present))
    step("POST_CLEANUP_READBACK")
    return stages, births


def container_role(
    service: dict[str, Any],
    compose: dict[str, Any],
    project: str,
    image: dict[str, Any],
    birth: str,
    qualification: str,
) -> dict[str, Any]:
    config = image["Config"]
    env = dict(item.split("=", 1) for item in config.get("Env") or [])
    for key, value in (service.get("environment") or {}).items():
        if value is None:
            env.pop(key, None)
        else:
            env[key] = str(value)
    mounts = {}
    for mount in service.get("volumes") or []:
        kind, source = mount["type"], mount["source"]
        require(kind in {"bind", "volume"}, "UNBOUND_MOUNT_TYPE")
        if kind == "volume":
            source = compose["volumes"][source]["name"]
        mounts[mount["target"]] = {
            "type": kind,
            "source": source,
            "rw": not mount.get("read_only", False),
            "options": deepcopy(mount.get(kind) or {}),
            "cleanup": "DELETE_ONLY_EXACT_OWNED_VOLUME"
            if kind == "volume"
            else "PRESERVE_HOST_SOURCE",
        }
    tmpfs = dict(item.split(":", 1) for item in service.get("tmpfs") or [])
    declared = set(config.get("Volumes") or {})
    require(
        declared.issubset(set(mounts) | set(tmpfs)),
        "UNBOUND_IMAGE_VOLUME",
        service.get("container_name", ""),
    )
    ports: dict[str, list[dict[str, str]]] = {}
    for port in service.get("ports") or []:
        require(port["host_ip"] == "127.0.0.1", "NONLOCAL_PORT")
        ports.setdefault(
            str(port["target"]) + "/" + port.get("protocol", "tcp"), []
        ).append({"HostIp": port["host_ip"], "HostPort": str(port["published"])})
    networks = [compose["networks"][n]["name"] for n in service.get("networks") or []]
    return {
        "kind": "containers",
        "name": service["container_name"],
        "labels": {
            **(config.get("Labels") or {}),
            **service.get("labels", {}),
            "com.docker.compose.project": project,
            QUAL_LABEL: qualification,
        },
        "image_reference": service["image"],
        "image_id": image["Id"],
        "platform_digest": (image.get("Descriptor") or {}).get("digest", image["Id"]),
        "user": service.get("user", config.get("User", "")),
        "entrypoint": service.get("entrypoint", config.get("Entrypoint")),
        "command": service.get("command", config.get("Cmd")),
        "environment": [k + "=" + v for k, v in sorted(env.items())],
        "read_only": service.get("read_only", False),
        "mounts": mounts,
        "tmpfs": tmpfs,
        "ports": ports,
        "networks": networks,
        "image_config_volumes": sorted(declared),
        "birth_stage": birth,
        "cleanup": "STOP_AND_REMOVE_EXACT_FRESH_OWNED_ID",
    }


def build_plan(
    qualification: str,
    initial: dict[str, Any],
    sandbox: dict[str, Any],
    product: dict[str, Any],
    probe: dict[str, Any],
    bind_commitments: dict[str, Any],
) -> dict[str, Any]:
    stages, births = stage_roles(sandbox, product)
    roles = {}
    for group, compose, project in (
        ("sandbox", sandbox, SANDBOX_PROJECT),
        ("product", product, PRODUCT_PROJECT),
    ):
        for key, service in compose["services"].items():
            role = group + "/container/" + key
            image = initial["platform_images"][service["image"]]
            roles[role] = container_role(
                service, compose, project, image, births[role], qualification
            )
        for key, network in compose["networks"].items():
            role = group + "/network/" + key
            roles[role] = {
                "kind": "networks",
                "name": network["name"],
                "labels": {
                    **network.get("labels", {}),
                    QUAL_LABEL: qualification,
                    "com.docker.compose.project": project,
                },
                "internal": network.get("internal", False),
                "birth_stage": births[role],
                "cleanup": "REMOVE_EXACT_FRESH_OWNED_ID_AFTER_CONTAINERS",
            }
        for key, volume in compose.get("volumes", {}).items():
            role = group + "/volume/" + key
            roles[role] = {
                "kind": "volumes",
                "name": volume["name"],
                "labels": volume["labels"],
                "birth_stage": births[role],
                "cleanup": "REMOVE_EXACT_FRESH_OWNED_NAME_AFTER_CONTAINERS",
            }
    roles["probe/container/kafka-volume-probe"] = probe
    return {
        "schema_version": "ecomsre.v040.no-fault-qualification.plan.v1",
        "qualification_id": qualification,
        "formal_authority": False,
        "counters": ZERO_COUNTS,
        "daemon": initial["daemon_before"],
        "platform_images": initial["platform_images"],
        "nonowned": {
            kind: {identity(kind, row): row for row in initial["inspect"][kind]}
            for kind in initial["inspect"]
        },
        "roles": roles,
        "stages": stages,
        "bind_commitments": bind_commitments,
        "volume_seed_lifecycle": {
            "/etc/kafka/secrets": "IMMUTABLE_SEED_ENTRIES_NO_NEW_SECRET",
            "/mnt/shared/config": "IMMUTABLE_SEED_WITH_KAFKA_GENERATED_RUNTIME_CONFIG",
            "/var/lib/kafka/data": "IMMUTABLE_SEED_WITH_EXPECTED_MUTABLE_KAFKA_LOG_DATA",
        },
    }
