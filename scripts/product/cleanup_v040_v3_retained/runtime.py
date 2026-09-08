"""Bounded local Docker transport and hash-verified historical loader."""

from __future__ import annotations
import json
import os
from pathlib import Path
import platform
import subprocess
from typing import Any

from .core import (
    Authority,
    BASE,
    BLOB,
    GOAL_PATH,
    GOAL_SHA,
    KINDS,
    RAW_SHA,
    RETAINED_PATH,
    Blocked,
    digest,
    ident,
    require,
    seal,
    sha,
    stamp,
)

REPO = Path(__file__).resolve().parents[3]
PRIOR = REPO.parent / "product-v040-runtime-qualification-v3"
PRIVATE = (
    PRIOR
    / ".local/product-v040-qualifications/qualification-c6a70e54d58b4df5a1325886304b4844"
)
ROOT = REPO / ".local/retained-cleanup-v1"


def git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=REPO, text=True).strip()


def history() -> tuple[Authority, dict[str, Any]]:
    require(sha((REPO / GOAL_PATH).read_bytes()) == GOAL_SHA, "GOAL_HASH_MISMATCH")
    data = (REPO / RETAINED_PATH).read_bytes()
    require(
        __import__("hashlib")
        .sha1(b"blob " + str(len(data)).encode() + b"\0" + data)
        .hexdigest()
        == BLOB,
        "RETAINED_BLOB_MISMATCH",
    )
    require(
        git("rev-parse", BASE + ":" + RETAINED_PATH) == BLOB, "STARTING_BLOB_MISMATCH"
    )
    public_index = (
        REPO
        / "docs/results/product-v040-qualification-v3/qualification-evidence-index.json"
    ).read_bytes()
    require(
        (PRIVATE / "evidence-index.json").read_bytes() == public_index,
        "HISTORICAL_INDEX_MISMATCH",
    )
    index = json.loads(public_index)
    checked = {}
    for rel, expected in index["files"].items():
        require(
            not Path(rel).is_absolute() and ".." not in Path(rel).parts,
            "INDEX_PATH_ESCAPE",
        )
        observed = sha((PRIVATE / rel).read_bytes())
        require(observed == expected, "HISTORICAL_EVIDENCE_DRIFT:" + rel)
        checked[rel] = observed
    raw_bytes = (PRIVATE / "host/cleanup/000-inventory.json").read_bytes()
    require(sha(raw_bytes) == RAW_SHA, "RAW_CAPTURE_MISMATCH")
    raw = json.loads(raw_bytes)
    compose = json.loads((PRIVATE / "host/sandbox-compose-expanded.json").read_bytes())
    plan = json.loads((PRIVATE / "host/stage-journal/plan.json").read_bytes())
    require(
        digest(compose) == plan["sandbox_compose_sha256"], "COMPOSE_BINDING_MISMATCH"
    )
    authority = Authority(json.loads(data), raw, compose, plan)
    return authority, {
        "goal_sha256": GOAL_SHA,
        "retained_blob": BLOB,
        "raw_sha256": RAW_SHA,
        "historical_files_verified": len(checked),
        "files": checked,
        "private_index_sha256": sha(public_index),
        "starting_head": BASE,
        "starting_tree": git("rev-parse", BASE + "^{tree}"),
    }


class Docker:
    def __init__(self, authority: Authority):
        self.a = authority
        self.bound: Any = None
        self.used: set[tuple[str, ...]] = set()
        # Never permit DOCKER_HOST, TLS, context overrides to redirect the CLI.
        self.env = {k: v for k, v in os.environ.items() if not k.startswith("DOCKER_")}
        self.env["DOCKER_CONTEXT"] = "desktop-linux"

    def read(self, *args: str) -> str:
        command = ["docker", *args]
        # Fixed callers below are the only read entry points; no model/HTTP argv.
        try:
            result = subprocess.run(
                command, env=self.env, capture_output=True, text=True, timeout=45
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise Blocked("DOCKER_READ_UNAVAILABLE:" + type(error).__name__) from error
        require(
            result.returncode == 0 and len(result.stdout) <= 32_000_000,
            "DOCKER_READ_FAILED",
        )
        return result.stdout

    def binding(self) -> dict[str, Any]:
        # Also inspect the actual selected context without our execution override.
        actual = subprocess.check_output(
            ["docker", "context", "show"], text=True, timeout=15
        ).strip()
        require(
            actual == self.a.daemon["context"] == "desktop-linux",
            "DAEMON_IDENTITY_DRIFT",
        )
        contexts = json.loads(self.read("context", "inspect", actual))
        require(len(contexts) == 1, "CONTEXT_INCOMPLETE")
        context = contexts[0]
        endpoint = context["Endpoints"]["docker"]["Host"]
        require(
            endpoint == self.a.daemon["endpoint"] and endpoint.startswith("unix://"),
            "DAEMON_IDENTITY_DRIFT",
        )
        info = json.loads(self.read("info", "--format", "{{json .}}"))
        version = json.loads(self.read("version", "--format", "{{json .Server}}"))
        projection = {
            k: info[k]
            for k in (
                "ID",
                "Name",
                "DockerRootDir",
                "OSType",
                "Architecture",
                "ServerVersion",
                "KernelVersion",
            )
        }
        for key, old_key in [
            ("ID", "daemon_id"),
            ("Name", "daemon_name"),
            ("DockerRootDir", "docker_root_dir"),
            ("OSType", "os"),
            ("Architecture", "architecture"),
            ("ServerVersion", "server_version"),
            ("KernelVersion", "kernel_version"),
        ]:
            require(projection[key] == self.a.daemon[old_key], "DAEMON_IDENTITY_DRIFT")
        binding = {
            "context": context,
            "daemon": projection,
            "server": version,
            "host_architecture": platform.machine(),
        }
        if self.bound is None:
            self.bound = binding
        require(binding == self.bound, "DAEMON_IDENTITY_DRIFT")
        return binding

    def lists(self) -> dict[str, list[str]]:
        return {
            "containers": sorted(self.read("ps", "-aq", "--no-trunc").split()),
            "networks": sorted(self.read("network", "ls", "-q").split()),
            "volumes": sorted(self.read("volume", "ls", "-q").split()),
        }

    def capture(self) -> dict[str, Any]:
        before = self.binding()
        ids = self.lists()
        # network ls -q emits short IDs. Resolve only against previously frozen
        # full inventory identities; never inspect a short ID or adopt a new network.
        # A new unknown network blocks this conservative read surface.
        raw = json.loads((PRIVATE / "host/cleanup/000-inventory.json").read_bytes())
        full_networks = [r["Id"] for r in raw["inspect"]["networks"]]
        resolved = []
        for prefix in ids["networks"]:
            matches = [rid for rid in full_networks if rid.startswith(prefix)]
            require(len(matches) == 1, "UNKNOWN_NETWORK_ENUMERATION_REQUIRES_FULL_ID")
            resolved.append(matches[0])
        rows: dict[str, Any] = {}
        for kind in KINDS:
            targets = resolved if kind == "networks" else ids[kind]
            rows[kind] = (
                json.loads(self.read(kind[:-1], "inspect", *targets)) if targets else []
            )
            observed = [ident(kind, r) for r in rows[kind]]
            require(
                sorted(observed) == sorted(targets)
                and len(set(observed)) == len(observed),
                "RACED_OR_INCOMPLETE_CAPTURE",
            )
        require(ids == self.lists(), "RACED_OR_INCOMPLETE_CAPTURE")
        after = self.binding()
        require(before == after, "DAEMON_IDENTITY_DRIFT")
        return {
            "ids": {**ids, "networks": sorted(resolved)},
            "inspect": rows,
            "binding": after,
            **stamp(),
        }

    def mutate(self, argv: list[str]) -> dict[str, Any]:
        require(
            any(
                argv == self.a.argv(kind, rid, op)
                for kind in KINDS
                for rid in self.a.records[kind]
                for op in (("stop", "rm") if kind == "containers" else ("rm",))
            ),
            "FORBIDDEN_COMMAND",
        )
        key = tuple(argv)
        require(key not in self.used, "DUPLICATE_MUTATION")
        self.binding()
        self.used.add(key)
        try:
            result = subprocess.run(argv, env=self.env, capture_output=True, timeout=45)
            return {
                "exit_status": result.returncode,
                "stdout_sha256": sha(result.stdout),
                "stderr_sha256": sha(result.stderr),
                "stdout": result.stdout[:4096].decode(errors="replace"),
                "stderr": result.stderr[:4096].decode(errors="replace"),
            }
        except (OSError, subprocess.TimeoutExpired) as error:
            return {"exit_status": None, "error": type(error).__name__}


def preflight() -> None:
    a, verification = history()
    seal(ROOT, "retained-allowlist-verification.json", verification)
    seal(
        ROOT, "retained-allowlist.json", json.loads((REPO / RETAINED_PATH).read_bytes())
    )
    docker = Docker(a)
    seal(ROOT, "activation-docker-binding.json", docker.binding())
    seal(ROOT, "frozen-command-derivation.json", a.commands)
