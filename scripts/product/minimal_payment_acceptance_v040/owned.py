"""Birth-bound local Docker lifecycle. Raw inspect is private; checks are semantic."""

from __future__ import annotations
import hashlib
import json
from pathlib import Path
import subprocess
from typing import Any

GOAL = "290e2f2c7948522f3f642f16696e73fc77c5535374be992e5f2ec2ff5db5337b"


def digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def command(*args: str) -> str:
    return subprocess.check_output(
        args, text=True, stderr=subprocess.PIPE, timeout=90
    ).strip()


def save(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with path.open("x") as stream:
        json.dump(value, stream, indent=2, sort_keys=True)
    path.chmod(0o600)


def inventory() -> dict[str, Any]:
    result = {}
    for kind, listing in [
        ("container", ("ps", "-aq", "--no-trunc")),
        ("network", ("network", "ls", "-q", "--no-trunc")),
        ("volume", ("volume", "ls", "-q")),
    ]:
        ids = command("docker", *listing).split()
        rows = json.loads(command("docker", kind, "inspect", *ids)) if ids else []
        result[kind] = {
            row.get("Id", row.get("Name")): semantic(kind, row) for row in rows
        }
    return result


def semantic(kind: str, row: dict[str, Any]) -> dict[str, Any]:
    if kind == "container":
        return {
            key: row[key]
            for key in (
                "Id",
                "Created",
                "Image",
                "Path",
                "Args",
                "Config",
                "HostConfig",
                "Mounts",
            )
        } | {
            "network_ids": sorted(
                n["NetworkID"] for n in row["NetworkSettings"]["Networks"].values()
            ),
            "running": row["State"]["Running"],
        }
    if kind == "network":
        return {
            key: row.get(key)
            for key in (
                "Id",
                "Name",
                "Created",
                "Labels",
                "Driver",
                "Internal",
                "IPAM",
                "Options",
                "Containers",
            )
        }
    return {
        key: row.get(key)
        for key in ("Name", "CreatedAt", "Labels", "Driver", "Options", "Mountpoint")
    }


def validate_container(
    row: dict[str, Any],
    spec: dict[str, Any],
    labels: dict[str, str],
    images: dict[str, Any],
    network_ids: set[str],
) -> None:
    config, host = row["Config"], row["HostConfig"]
    if any(config["Labels"].get(k) != v for k, v in labels.items()):
        raise ValueError("OWNERSHIP_LABEL_MISMATCH")
    image = images[spec["image"]]
    if row["Image"] not in {image["Id"], image.get("index_id", image["Id"])}:
        raise ValueError("IMAGE_ID_MISMATCH")
    entry = spec.get("entrypoint", image["Config"].get("Entrypoint")) or []
    cmd = spec.get("command", image["Config"].get("Cmd")) or []
    effective = entry + cmd
    if [row["Path"], *row["Args"]] != effective:
        raise ValueError("EFFECTIVE_COMMAND_MISMATCH")
    if host["Privileged"] or host["NetworkMode"] == "host" or host["PidMode"] == "host":
        raise ValueError("HOST_AUTHORITY_DENIED")
    if not host["ReadonlyRootfs"] or set(host["CapDrop"] or []) != {"ALL"}:
        raise ValueError("CONTAINER_HARDENING_MISMATCH")
    actual_networks = {
        n["NetworkID"] for n in row["NetworkSettings"]["Networks"].values()
    }
    if actual_networks != network_ids:
        raise ValueError("NETWORK_ID_MISMATCH")
    expected = {m["target"]: m for m in spec.get("volumes", [])}
    actual = {m["Destination"]: m for m in row["Mounts"] if m["Type"] != "tmpfs"}
    if set(expected) != set(actual):
        raise ValueError("MOUNT_SET_MISMATCH")
    for target, wanted in expected.items():
        got = actual[target]
        if got["Type"] != wanted["type"] or got["RW"] != (
            not wanted.get("read_only", False)
        ):
            raise ValueError("MOUNT_MODE_MISMATCH")
        source = got["Name"] if got["Type"] == "volume" else got["Source"]
        if source != wanted["source"]:
            raise ValueError("MOUNT_SOURCE_MISMATCH")
    ports = {p["target"]: str(p["published"]) for p in spec.get("ports", [])}
    actual_ports = host.get("PortBindings") or {}
    if set(actual_ports) != {str(p) + "/tcp" for p in ports}:
        raise ValueError("PORT_SET_MISMATCH")
    for port, published in ports.items():
        if actual_ports[str(port) + "/tcp"] != [
            {"HostIp": "127.0.0.1", "HostPort": published}
        ]:
            raise ValueError("PORT_BINDING_MISMATCH")


class Owned:
    def __init__(self, root: Path, nonce: str):
        self.root, self.nonce = root, nonce
        self.labels = {
            "io.ecomsre.minimal.goal": GOAL,
            "io.ecomsre.minimal.attempt": nonce,
        }
        self.context = command("docker", "context", "show")
        context = json.loads(command("docker", "context", "inspect", self.context))[0]
        if not context["Endpoints"]["docker"]["Host"].startswith("unix://"):
            raise ValueError("REMOTE_DOCKER_DENIED")
        info = json.loads(command("docker", "info", "--format", "{{json .}}"))
        if info["OSType"] != "linux" or info["Architecture"] not in (
            "aarch64",
            "arm64",
        ):
            raise ValueError("PLATFORM_DENIED")
        self.daemon = info["ID"]
        self.before = inventory()
        self.births: dict[str, dict[str, Any]] = {
            "container": {},
            "network": {},
            "volume": {},
        }
        save(
            root / "before.json",
            {"context": context, "daemon_id": self.daemon, "inventory": self.before},
        )

    def fresh(self) -> None:
        if (
            command("docker", "context", "show") != self.context
            or command("docker", "info", "--format", "{{.ID}}") != self.daemon
        ):
            raise ValueError("DAEMON_DRIFT")

    def capture_birth(self, kind: str, identifier: str) -> None:
        self.fresh()
        row = json.loads(command("docker", kind, "inspect", identifier))[0]
        labels = row["Config"]["Labels"] if kind == "container" else row["Labels"]
        if any(labels.get(k) != v for k, v in self.labels.items()):
            raise ValueError("BIRTH_OWNERSHIP_UNKNOWN")
        if identifier in self.before[kind] or identifier in self.births[kind]:
            raise ValueError("BIRTH_PREEXISTS")
        self.births[kind][identifier] = row
        save(self.root / "births" / f"{kind}-{identifier}.json", row)

    def create_aux(self, kind: str, name: str, *, internal: bool = False) -> str:
        self.fresh()
        args = ["docker", kind, "create"]
        if internal:
            args.append("--internal")
        for key, value in self.labels.items():
            args += ["--label", f"{key}={value}"]
        identifier = command(*args, name)
        self.capture_birth(kind, identifier)
        return identifier

    def unchanged(self) -> bool:
        current = inventory()
        return all(
            {k: v for k, v in current[kind].items() if k not in self.births[kind]}
            == self.before[kind]
            for kind in self.before
        )

    def require_birth(self, kind: str, identifier: str) -> dict[str, Any]:
        self.fresh()
        birth = self.births[kind][identifier]
        row = json.loads(command("docker", kind, "inspect", identifier))[0]
        fields = (
            ("Id", "Created")
            if kind == "container"
            else ("Id", "Created")
            if kind == "network"
            else ("Name", "CreatedAt", "Driver", "Options", "Mountpoint")
        )
        if any(row.get(k) != birth.get(k) for k in fields):
            raise ValueError("BIRTH_IDENTITY_DRIFT")
        labels = row["Config"]["Labels"] if kind == "container" else row["Labels"]
        if any(labels.get(k) != v for k, v in self.labels.items()):
            raise ValueError("CLEANUP_OWNERSHIP_UNKNOWN")
        return row

    def cleanup(self) -> dict[str, Any]:
        receipts = []
        for kind in ("container", "network", "volume"):
            for identifier in reversed(tuple(self.births[kind])):
                row = self.require_birth(kind, identifier)
                if kind == "container":
                    if row["State"]["Running"]:
                        command("docker", "stop", "--time", "10", identifier)
                    row = self.require_birth(kind, identifier)
                    if row["State"]["Running"]:
                        raise ValueError("STOP_NOT_CONFIRMED")
                    command("docker", "container", "rm", identifier)
                else:
                    if kind == "network" and row.get("Containers"):
                        raise ValueError("NETWORK_NOT_EMPTY")
                    # Docker refuses removal of an attached volume; no force flag.
                    command("docker", kind, "rm", identifier)
                receipts.append(
                    {
                        "kind": kind,
                        "identity_sha256": digest(identifier),
                        "removed": True,
                    }
                )
        after = inventory()
        remaining = {
            kind: len(set(after[kind]) & set(self.births[kind])) for kind in after
        }
        clean = after == self.before and not any(remaining.values())
        result = {
            "receipts": receipts,
            "remaining": remaining,
            "non_owned_unchanged": after == self.before,
            "clean": clean,
        }
        save(self.root / "cleanup.json", {"result": result, "after": after})
        if not clean:
            raise ValueError("CLEANUP_NOT_CLEAN")
        return result
