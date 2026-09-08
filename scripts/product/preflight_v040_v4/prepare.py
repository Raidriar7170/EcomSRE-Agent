"""Resolve complete attempt inputs and freeze ownership before any runtime create."""

from __future__ import annotations
from copy import deepcopy
import json
import os
from pathlib import Path
import secrets
from typing import Any
from ecomsre_live_sandbox.product_v030 import (
    ProductV030SandboxEnvironment,
    build_product_v030_runtime_bundle,
)
from ecomsre_live_sandbox.control import build_flag_documents
from .common import REPO, GOAL_SHA, KAFKA_PATHS, digest, load, require, seal
from .docker import Docker
from .images import inspect_images
from .identity import effective_process
from .planner import prepare_sandbox, validate_model, labels
from .readiness import dependency_order


def expand(
    docker: Docker, root: Path, name: str, model: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, str]]:
    path = root / (name + "-input.json")
    seal(root, path.name, model)
    prefix = ["compose", "--project-name", model["name"], "-f", str(path)]
    expanded = json.loads(docker.read(*prefix, "config", "--format", "json"))
    seal(root, name + ".json", expanded)
    # The hash used by Compose creation must be computed from the same final file.
    final_prefix = [
        "compose",
        "--project-name",
        model["name"],
        "-f",
        str(root / (name + ".json")),
    ]
    hashes = dict(
        line.split()
        for line in docker.read(*final_prefix, "config", "--hash", "*").splitlines()
    )
    require(set(hashes) == set(expanded["services"]), "COMPOSE_HASH_ROLE_SET")
    return expanded, hashes


def prepare(
    root: Path,
    attempt: str,
    docker: Docker,
    product_image: dict[str, Any],
    initial: dict[str, Any],
    runtime_source: str,
) -> dict[str, Any]:
    flags = root / "flags"
    flags.mkdir(mode=0o700)
    data = root / "product-data"
    data.mkdir(mode=0o700)
    bundle = build_product_v030_runtime_bundle(REPO)
    upstream = load(REPO / "third_party/opentelemetry-demo/src/flagd/demo.flagd.json")
    baseline, _ = build_flag_documents(upstream, bundle)
    seal(flags, "demo.flagd.json", baseline)
    renderer = ProductV030SandboxEnvironment(
        repository_root=REPO, bundle=bundle, flagd_directory=flags
    )
    _, resolved_source = renderer.resolve()
    source: dict[str, Any] = dict(resolved_source)
    seal(root, "source-compose.json", source)
    images = inspect_images(docker, root)
    sandbox_input = prepare_sandbox(source, images, attempt, flags)
    sandbox, sandbox_hashes = expand(docker, root, "sandbox", sandbox_input)
    allowed_binds = {
        m["source"]
        for service in source["services"].values()
        for m in service.get("volumes", [])
        if m["type"] == "bind"
    }
    plan = validate_model(sandbox, images, attempt, allowed_binds)
    plan["sandbox_start_order"] = dependency_order(plan["roles"])
    builtin = [r for r in initial["resources"]["network"] if r["Name"] == "none"]
    require(len(builtin) == 1, "BUILTIN_NONE_UNBOUND")
    plan["builtin_none_id"] = builtin[0]["Id"]
    by_ref = {image["runtime_reference"]: image for image in images.values()}
    containers = {}
    for role, value in plan["roles"].items():
        image = by_ref[value["service"]["image"]]
        value.update(
            {
                "image_config": image["config"],
                "project": plan["project"],
                "config_hash": sandbox_hashes[role],
                "network_names": [
                    sandbox["networks"][n]["name"]
                    for n in value["service"].get("networks", {})
                ],
            }
        )
        containers[role] = deepcopy(value)
    kafka = images["ghcr.io/open-telemetry/demo:3.0.0-kafka"]
    probe_service = {
        "container_name": "ecomsre-v4-" + attempt + "-probe",
        "hostname": "kafka-volume-probe",
        "image": kafka["runtime_reference"],
        "user": "1000:1000",
        "entrypoint": ["/bin/sleep"],
        "command": ["2147483647"],
        "labels": labels(attempt, "kafka-volume-probe"),
        "network_mode": "none",
        "read_only": True,
        "cap_drop": ["ALL"],
        "security_opt": ["no-new-privileges:true"],
        "memory": 134217728,
        "volumes": [
            {"type": "volume", "source": "kafka-" + p.rsplit("/", 1)[1], "target": p}
            for p in KAFKA_PATHS
        ],
    }
    containers["kafka-volume-probe"] = {
        "service": probe_service,
        "image_config": kafka["config"],
        "image_id": kafka["image_id"],
        "platform_digest": kafka["platform_digest"],
        "process": effective_process(probe_service, kafka["config"]),
        "network_names": ["none"],
        "project": None,
    }
    product_project = "ecomsre-v4-" + attempt + "-product"
    token = secrets.token_urlsafe(32)
    variables = {
        "V4_PROJECT": product_project,
        "V4_PRODUCT_IMAGE": product_image["image_id"],
        "V4_HOST_UID": str(os.getuid()),
        "V4_HOST_GID": str(os.getgid()),
        "V4_PRODUCT_DATA": str(data),
        "V4_ADMIN_TOKEN": token,
        "V4_ATTEMPT": attempt,
        "V4_GOAL_SHA": GOAL_SHA,
        "V4_SANDBOX_NETWORK": sandbox["networks"]["default"]["name"],
    }
    seal(root, "product-substitutions.json", variables)
    raw = json.loads(
        docker.read(
            "compose",
            "-f",
            str(REPO / "docker-compose.product.preflight-v4.yml"),
            "config",
            "--format",
            "json",
            env=variables,
        )
    )
    product, product_hashes = expand(docker, root, "product", raw)
    require(
        set(product["services"]) == {"api", "worker"}, "REMEDIATION_PROFILE_PRESENT"
    )
    for role, service in product["services"].items():
        require(
            not any(
                "REMEDIATION" in key or "PROVIDER" in key
                for key in service.get("environment", {})
            ),
            "PRODUCT_WRITE_AUTHORITY_PRESENT",
        )
        require(
            len(service["volumes"]) == 1
            and service["volumes"][0]["source"] == str(data)
            and service["volumes"][0]["target"] == "/var/lib/ecomsre",
            "PRODUCT_PRIVILEGED_MOUNT_PRESENT",
        )
        require(not product_image["config"].get("Volumes"), "PRODUCT_ANONYMOUS_VOLUME")
        containers[role] = {
            "service": service,
            "image_config": product_image["config"],
            "image_id": product_image["image_id"],
            "platform_digest": product_image["platform_digest"],
            "process": effective_process(service, product_image["config"]),
            "project": product_project,
            "config_hash": product_hashes[role],
            "network_names": [
                product["networks"][name]["name"] for name in service["networks"]
            ],
        }
    plan["networks"]["product-default"] = {
        "name": product_project + "-default",
        "driver": "bridge",
        "labels": labels(attempt, "product-network"),
    }
    future = json.loads(
        docker.read(
            "compose",
            "-f",
            str(REPO / "docker-compose.product.yml"),
            "--profile",
            "remediation",
            "config",
            "--format",
            "json",
            env={"ECOMSRE_ADMIN_TOKEN": "offline-topology-only"},
        )
    )
    require(
        set(future["services"])
        == {"api", "worker", "remediation-executor", "remediation-control-gateway"},
        "FUTURE_PROFILE_ROLE_DRIFT",
    )
    executor = future["services"]["remediation-executor"]
    require(
        executor["network_mode"] == "none"
        and executor["read_only"] is True
        and executor["cap_drop"] == ["ALL"]
        and not any(
            m["target"] == "/runtime/payment-flags" for m in executor["volumes"]
        ),
        "FUTURE_EXECUTOR_ISOLATION",
    )
    seal(
        root,
        "future-profile-static.json",
        {"compose": future, "runtime_activated": False, "isolation": "PASS"},
    )
    plan["containers"] = containers
    plan["product_project"] = product_project
    plan["compose_digests"] = {"sandbox": digest(sandbox), "product": digest(product)}
    plan["image_commitments"] = {
        k: {
            "image_id": v["image_id"],
            "platform_digest": v["platform_digest"],
            "index_reference": v["index_reference"],
        }
        for k, v in images.items()
    }
    plan["product_image"] = product_image
    plan["baseline_flag_sha256"] = digest(baseline)
    semantic = {"sandbox": sandbox, "product": product}
    encoded = json.dumps(semantic, sort_keys=True)
    for actual, replacement in [
        (str(root), "$ATTEMPT_ROOT"),
        (str(REPO), "$REPO"),
        (attempt, "$ATTEMPT"),
        (token, "$TOKEN"),
    ]:
        encoded = encoded.replace(actual, replacement)
    plan["semantic_compose_digest"] = digest(json.loads(encoded))
    plan["runtime_surface"] = digest(
        {
            "source": runtime_source,
            "policy": load(REPO / "config/product-v040/preflight-v4/policy.json"),
            "compose": plan["semantic_compose_digest"],
            "images": plan["image_commitments"],
            "product_image": product_image["image_id"],
        }
    )
    seal(root, "ownership-plan.json", plan)
    return plan
