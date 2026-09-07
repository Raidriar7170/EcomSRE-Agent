"""Read-only stage capture adapter, tested with deterministic fake transports.

The offline repair never invokes this adapter against Docker. It requires a
complete independently bound stage plan before collecting anything. It has no
startup, build, mutation, approval or cleanup operation.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Protocol

from scripts.product.v040_ownership import (
    KINDS,
    OWNERS,
    OwnershipBlocked,
    StageJournal,
    sha,
    tree_commitment,
)


class ReadRuntime(Protocol):
    private: Path
    repository: Path

    def boundary(self) -> dict[str, str]: ...

    def docker(self, *args: str, timeout: int = 30) -> str: ...


def collect(runtime: ReadRuntime) -> dict[str, Any]:
    """Only fixed ls/inspect APIs. No unchecked host path is read as a bind."""
    daemon = runtime.boundary()
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

    before = enumerate_ids()
    inspected = {
        kind: (
            json.loads(runtime.docker(singular[kind], "inspect", *before[kind]))
            if before[kind]
            else []
        )
        for kind in KINDS
    }
    mount_contents = {}
    for container in inspected["containers"]:
        labels = container["Config"].get("Labels") or {}
        project = labels.get("com.docker.compose.project")
        for mount in container["Mounts"]:
            kind = mount["Type"]
            if kind == "bind":
                source = Path(mount["Source"])
                if source == Path("/var/run/docker.sock") and mount.get("RW") is False:
                    binding = {
                        "digest_kind": "DAEMON_SOCKET_BINDING_V1",
                        "sha256": sha(daemon),
                    }
                elif (
                    project in OWNERS
                    and all(labels.get(k) == v for k, v in OWNERS[project].items())
                    and not source.is_symlink()
                    and source.resolve().is_relative_to(runtime.repository.resolve())
                ):
                    binding = tree_commitment(source)
                else:
                    raise OwnershipBlocked("unbound host mount source; no read")
            elif kind in {"volume", "tmpfs"}:
                # This binds source/identity/options and immutable image seed,
                # not ongoing mutable directory contents inside the VM.
                binding = {
                    "digest_kind": "MOUNT_IDENTITY_IMAGE_SEED_V1",
                    "sha256": sha({"mount": mount, "image": container["Image"]}),
                }
            else:
                raise OwnershipBlocked("unknown mount type")
            mount_contents[container["Id"] + ":" + mount["Destination"]] = binding
    return {
        "daemon_before": daemon,
        "daemon_after": runtime.boundary(),
        "ids_before": before,
        "ids_after": enumerate_ids(),
        "inspect": inspected,
        "mount_contents": mount_contents,
    }


def checkpoint(runtime: ReadRuntime, stage: str) -> dict[str, Any]:
    path = runtime.private / "host/ownership-stage-plan.json"
    if path.is_symlink() or not path.is_file():
        # Missing expected authority must stop before querying/waking Docker.
        raise OwnershipBlocked("no independently bound ownership stage plan")
    expected = json.loads(path.read_bytes())
    journal = StageJournal(runtime.private / "host/ownership-stage-journal", expected)
    try:
        saved = collect(runtime)
    except Exception as error:
        # Record the failure without interpolating errors containing credentials,
        # host paths or arbitrary daemon output into the public/safe projection.
        return journal.observe(stage, {"collection_error_type": type(error).__name__})
    return journal.observe_saved(stage, saved)
