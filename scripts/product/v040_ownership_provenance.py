"""Compile an offline review manifest from immutable preparation-004 evidence.

The Kafka overlay is a review artifact, never passed to Docker by this module.
Raw configuration and credentials stay in the private archive; output commits
their hashes and publishes only fixed semantic source locations.
"""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
from typing import Any

from scripts.product.v040_ownership import (
    BASE,
    COUNTERS,
    KAFKA_PATHS,
    OWNERS,
    STAGES,
    OwnershipBlocked,
    sha,
    tree_commitment,
    validate_mount_provenance,
)

REPAIR_LABEL = "io.ecomsre.product.v040.ownership-repair"
REPAIR_ID = "offline-v1"
SANDBOX = "ecomsre-live-sandbox-v1"
PRODUCT = "ecomsre-product-v040"


def content(kind: str, commitment: object) -> dict[str, Any]:
    return {"digest_kind": kind, "commitment": commitment, "sha256": sha(commitment)}


def kafka_overlay() -> dict[str, Any]:
    labels = {**OWNERS[SANDBOX], REPAIR_LABEL: REPAIR_ID}
    volumes, mounts = {}, []
    for path, short in zip(KAFKA_PATHS, ("secrets", "config", "data"), strict=True):
        source = "kafka-" + short + "-ownership-v1"
        volumes[source] = {
            "name": "ecomsre-v040-offline-review-kafka-" + short,
            "labels": labels,
        }
        mounts.append(
            {
                "type": "volume",
                "source": source,
                "target": path,
                "read_only": False,
                "volume": {"nocopy": False},
            }
        )
    return {"services": {"kafka": {"volumes": mounts}}, "volumes": volumes}


def compile_provenance(repository: Path, archive: Path) -> dict[str, Any]:
    """Read-only transformation; new writes are performed by the caller only."""
    inputs = {}

    def read(name: str) -> Any:
        path = archive / name
        if path.is_symlink() or not path.is_file():
            raise OwnershipBlocked("missing immutable preparation input")
        raw = path.read_bytes()
        inputs[name] = hashlib.sha256(raw).hexdigest()
        return json.loads(raw)

    admission = read("sandbox/control/admission.json")
    product = read("host/resolved-compose-bootstrap.json")
    proof = read("host/image-proofs-original.json")
    build = read("host/product-build.json")
    kafka = read("host/kafka-declared-volumes.json")
    if set(kafka["declared_volumes"]) != set(KAFKA_PATHS):
        raise OwnershipBlocked("historical Kafka volume declarations differ")
    sandbox = deepcopy(admission["resolved_compose"])
    original_compose_sha = sha(sandbox)
    overlay = kafka_overlay()
    sandbox["services"]["kafka"]["volumes"].extend(
        overlay["services"]["kafka"]["volumes"]
    )
    sandbox["volumes"].update(overlay["volumes"])
    image_by_reference = {x["source_reference"]: x for x in admission["images"]}
    kafka_proof = next(
        x
        for x in proof["registry_proofs"]
        if x["reference"] == kafka["source_reference"]
    )
    cached = kafka_proof["cached"]
    if (
        cached["Config"]["User"] != kafka["user"]
        or set(cached["Config"]["Volumes"]) != set(KAFKA_PATHS)
        or image_by_reference[kafka["source_reference"]]["image_id"]
        != kafka["image_id"]
    ):
        raise OwnershipBlocked("Kafka cached image proof differs")
    volumes: dict[str, Any] = {}
    mounts = []
    services = {}
    image_digests = {x["resolved_platform_digest"] for x in admission["images"]}
    image_digests.add(build["image_id"])

    def source_binding(source: str) -> tuple[str, dict[str, Any], str]:
        # Archive source paths are projected to fixed semantic URIs, never used
        # as executable input or allowed to read arbitrary original host paths.
        marker = "/.local/product-v040/campaign/"
        if marker in source:
            relative = source.split(marker, 1)[1]
            resolved = (archive / relative).resolve()
            if not resolved.is_relative_to(archive.resolve()):
                raise OwnershipBlocked("private source escaped archive")
            binding = tree_commitment(resolved)
            binding["observation_semantics"] = (
                "PRESERVED_POST_FAILURE_CONTENT_NOT_A_FUTURE_INITIAL_SEED"
            )
            return (
                "private://" + relative,
                binding,
                "MUTABLE_PRIVATE_EVIDENCE_PRESERVED_NO_DELETE",
            )
        if source == "/var/run/docker.sock":
            return (
                "daemon://local-unix-socket",
                content(
                    "DAEMON_SOCKET_BINDING_V1",
                    {
                        "daemon_boundary_sha256": sha(admission["docker"]),
                        "read_only": True,
                        "content_bytes": "NOT_A_REGULAR_FILE",
                    },
                ),
                "NON_OWNED_SOCKET_NEVER_MUTATE_OR_CLEAN",
            )
        prefixes = ("/third_party/opentelemetry-demo/", "/config/")
        for prefix in prefixes:
            if prefix in source:
                relative = prefix[1:] + source.split(prefix, 1)[1]
                resolved = (repository / relative).resolve()
                if not resolved.is_relative_to(repository.resolve()):
                    raise OwnershipBlocked("repository source escaped root")
                return (
                    "repo://" + relative,
                    tree_commitment(resolved),
                    "FROZEN_SOURCE_READ_ONLY_NO_CLEANUP",
                )
        raise OwnershipBlocked("unbound bind source")

    for prefix, project, plan in (
        ("sandbox", SANDBOX, sandbox),
        ("product", PRODUCT, product),
    ):
        for source, definition in plan.get("volumes", {}).items():
            volumes[source] = {
                "name": definition["name"],
                "labels": {
                    **definition["labels"],
                    "com.docker.compose.project": project,
                },
                "cleanup": "EXACT_ID_AND_ALL_LABELS_REVALIDATED_OWNED_ONLY_NO_DOWN_V_NO_PRUNE",
                "lifecycle": "ABSENT_BEFORE_START_CREATE_ONCE_NO_REUSE_REMOVE_AFTER_OWNED_CONTAINERS_STOP",
            }
        for name, service in sorted(plan["services"].items()):
            key = prefix + "/" + name
            image = (
                image_by_reference[service["image"]]["resolved_platform_digest"]
                if prefix == "sandbox"
                else build["image_id"]
            )
            labels = {**service["labels"], "com.docker.compose.project": project}
            services[key] = {
                "resolved_service_sha256": sha(service),
                "image_digest": image,
                "image_reference": service["image"],
                "owner_labels": labels,
                "networks": sorted(service.get("networks", {})),
                "environment_sha256": sha(service.get("environment", {})),
                "command_sha256": sha(
                    {
                        k: service.get(k)
                        for k in ("entrypoint", "command", "user", "healthcheck")
                    }
                ),
                "mount_targets": sorted(
                    [m["target"] for m in service.get("volumes", [])]
                    + [t.partition(":")[0] for t in service.get("tmpfs", [])]
                ),
            }
            definitions = deepcopy(service.get("volumes", []))
            definitions.extend(
                {
                    "type": "tmpfs",
                    "target": tmp.partition(":")[0],
                    "tmpfs_options": tmp.partition(":")[2],
                }
                for tmp in service.get("tmpfs", [])
            )
            for mount in definitions:
                target = mount["target"]
                options = {
                    k: v
                    for k, v in mount.items()
                    if k not in {"type", "source", "target", "read_only"}
                }
                cleanup = "EXACT_OWNED_CONTAINER_REMOVAL_ONLY"
                lifecycle = (
                    "EPHEMERAL_EMPTY_AT_CONTAINER_CREATE_DISCARDED_WITH_CONTAINER"
                )
                owner = labels
                if mount["type"] == "bind":
                    source, binding, lifecycle = source_binding(mount["source"])
                    cleanup = "PRESERVE_SOURCE_AND_EVIDENCE_NEVER_DELETE_BIND_SOURCE"
                elif mount["type"] == "tmpfs":
                    source = "tmpfs://" + key + target
                    binding = content(
                        "EMPTY_TMPFS_COMMITMENT_V1",
                        {"initial_entries": [], "options": options},
                    )
                elif mount["type"] == "volume":
                    source = mount["source"]
                    owner = volumes[source]["labels"]
                    cleanup, lifecycle = (
                        volumes[source]["cleanup"],
                        volumes[source]["lifecycle"],
                    )
                    if name == "kafka":
                        binding = content(
                            "IMAGE_PATH_SEED_COMMITMENT_V1",
                            {
                                "platform_digest": image,
                                "rootfs_diff_ids": cached["RootFS"]["Layers"],
                                "path": target,
                                "image_user": cached["Config"]["User"],
                                "numeric_uid_gid": "INHERITED_FROM_BOUND_IMAGE_NOT_MEASURED",
                                "copy_up": True,
                                "runtime_path_digest": "NOT_MEASURED_OFFLINE",
                            },
                        )
                    else:
                        binding = content(
                            "IMAGE_SEED_COMMITMENT_V1",
                            {
                                "platform_digest": image,
                                "path": target,
                                "copy_up": True,
                                "runtime_path_digest": "NOT_MEASURED_OFFLINE",
                            },
                        )
                else:
                    raise OwnershipBlocked("unknown Compose mount type")
                mounts.append(
                    {
                        "service": key,
                        "target": target,
                        "type": mount["type"],
                        "source": source,
                        "read_only": mount.get("read_only", False),
                        "options": options,
                        "owner_labels": owner,
                        "content": binding,
                        "cleanup": cleanup,
                        "lifecycle": lifecycle,
                        "image_digest": image,
                    }
                )
    result = {
        "schema_version": "ecomsre.product.v040.offline-environment-provenance.v1",
        "authority": "OFFLINE_ONLY",
        "counts": COUNTERS,
        "historical_head": BASE,
        "historical_inputs_sha256": inputs,
        "image_digests": sorted(image_digests),
        "upstream_commit": admission["upstream_commit"],
        "historical_sandbox_compose_sha256": original_compose_sha,
        "planned_sandbox_compose_sha256": sha(sandbox),
        "historical_product_compose_sha256": sha(product),
        "kafka_overlay_sha256": sha(overlay),
        "services": services,
        "volumes": volumes,
        "mounts": mounts,
        "mount_inventory": sorted(m["service"] + ":" + m["target"] for m in mounts),
        "networks": {"sandbox": sandbox["networks"], "product": product["networks"]},
        "image_volume_coverage": {
            "kafka_declared_paths": list(KAFKA_PATHS),
            "kafka_uncovered_after_overlay": [],
            "other_images": "EXISTING_EXPLICIT_MOUNTS_BOUND_BY_PLATFORM_IMAGE_AND_RESOLVED_PLAN",
        },
        "stages": list(STAGES),
        "runtime_readiness": "NOT_AUTHORIZED_NOT_MEASURED",
        "future_requirements": [
            "Fresh locally stable daemon and complete saved inventory with all image Config.Volumes",
            "Numeric image seed ownership and real copy-up content remain unmeasured; no writeability claim",
            "Bind current source and fresh per-stage provenance; historical mutable archive hashes are not initial state",
            "Separate explicit authority required before startup; this increment contains none",
        ],
    }
    validate_mount_provenance(result)
    result["manifest_sha256"] = sha(result)
    return result
