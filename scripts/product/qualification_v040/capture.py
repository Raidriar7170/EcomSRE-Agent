"""Read-only live adapter for the generative qualification stage plan.

Preserves PR96's complete before/after enumeration and inspect envelope. Bind
content handling explicitly distinguishes frozen input from active private DBs;
active database contents cannot be treated as immutable source files.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from collections.abc import Callable

from scripts.product.v040_ownership import tree_commitment
from scripts.product.qualification_v040.guard import KINDS, QUAL_LABEL, require, sha


def image_binding(row: dict[str, Any]) -> dict[str, Any]:
    return {
        key: row.get(key)
        for key in (
            "Id",
            "Architecture",
            "Os",
            "Variant",
            "RepoDigests",
            "RootFS",
            "Config",
            "Descriptor",
        )
    }


def platform_images(runtime: Any, references: list[str]) -> dict[str, Any]:
    if not references:
        return {}
    rows = json.loads(
        runtime.docker("image", "inspect", "--platform", "linux/arm64", *references)
    )
    require(len(rows) == len(references), "INCOMPLETE_IMAGE_CAPTURE")
    result = {}
    for ref, row in zip(references, rows, strict=True):
        require(
            row.get("Os") == "linux" and row.get("Architecture") == "arm64",
            "IMAGE_PLATFORM_DRIFT",
            ref,
        )
        result[ref] = image_binding(row)
    return result


def capture(
    runtime: Any,
    qualification: str,
    references: list[str],
    mutable_binds: set[str],
    seed_capture: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    singular = {
        "containers": "container",
        "networks": "network",
        "volumes": "volume",
        "images": "image",
    }

    def enumerate_ids() -> dict[str, list[str]]:
        return {
            kind: sorted(
                set(
                    runtime.docker(
                        singular[kind],
                        "ls",
                        "-q",
                        *(
                            ("--all", "--no-trunc")
                            if kind in {"containers", "images"}
                            else ()
                        ),
                    ).split()
                )
            )
            for kind in KINDS
        }

    before_daemon = runtime.boundary()
    before = enumerate_ids()
    inspected = {
        kind: json.loads(runtime.docker(singular[kind], "inspect", *before[kind]))
        if before[kind]
        else []
        for kind in KINDS
    }

    def mount_bindings(rows: dict[str, Any]) -> dict[str, Any]:
        bindings: dict[str, Any] = {}
        for row in rows["containers"]:
            owned = (row["Config"].get("Labels") or {}).get(QUAL_LABEL) == qualification
            for mount in row["Mounts"]:
                value: dict[str, Any]
                kind = mount["Type"]
                if kind == "bind" and owned:
                    source = Path(mount["Source"])
                    if str(source) == "/var/run/docker.sock" and mount["RW"] is False:
                        value = {
                            "digest_kind": "DAEMON_SOCKET_BINDING_V1",
                            "sha256": sha(before_daemon),
                        }
                    else:
                        require(
                            not source.is_symlink()
                            and source.resolve().is_relative_to(runtime.repository),
                            "UNBOUND_HOST_SOURCE",
                        )
                        if str(source) in mutable_binds:
                            info = source.stat()
                            value = {
                                "digest_kind": "OWNED_MUTABLE_DIRECTORY_IDENTITY_V1",
                                "source": str(source),
                                "uid": info.st_uid,
                                "gid": info.st_gid,
                                "mode": info.st_mode & 0o7777,
                                "inode": info.st_ino,
                            }
                        else:
                            value = tree_commitment(source)
                else:
                    require(kind in {"bind", "volume", "tmpfs"}, "UNKNOWN_MOUNT_TYPE")
                    value = {
                        "digest_kind": "MOUNT_IDENTITY_IMAGE_SEED_V1",
                        "sha256": sha({"mount": mount, "image": row["Image"]}),
                    }
                bindings[row["Id"] + ":" + mount["Destination"]] = value
        return bindings

    bindings = mount_bindings(inspected)
    images = platform_images(runtime, references)
    seeds = seed_capture(inspected) if seed_capture else {"status": "NOT_BOUND"}
    inspected_after = {
        kind: json.loads(runtime.docker(singular[kind], "inspect", *before[kind]))
        if before[kind]
        else []
        for kind in KINDS
    }
    images_after = platform_images(runtime, references)
    bindings_after = mount_bindings(inspected_after)
    after = enumerate_ids()
    after_daemon = runtime.boundary()
    return {
        "daemon_before": before_daemon,
        "daemon_after": after_daemon,
        "ids_before": before,
        "ids_after": after,
        "inspect": inspected,
        "inspect_after": inspected_after,
        "seed_properties": seeds,
        "mount_contents": bindings,
        "mount_contents_after": bindings_after,
        "platform_images": images,
        "platform_images_after": images_after,
    }
