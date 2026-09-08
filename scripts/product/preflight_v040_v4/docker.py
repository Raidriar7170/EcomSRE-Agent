"""Fixed local read transport; mutations are supplied only by the plan coordinator."""

from __future__ import annotations
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
from typing import Any
from .common import Failure, KINDS, REPO, require, now
from .transport import bounded


class Docker:
    def __init__(self) -> None:
        self.env = {
            k: v
            for k, v in os.environ.items()
            if not k.startswith(("DOCKER_", "COMPOSE_", "BUILDX_"))
        }
        self.env["DOCKER_CONTEXT"] = "desktop-linux"
        self.bound: Any = None

    def command(
        self,
        args: list[str],
        *,
        timeout: int = 45,
        cwd: Path = REPO,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        result = bounded(
            ["docker", *args], cwd=cwd, env={**self.env, **(env or {})}, timeout=timeout
        )
        return subprocess.CompletedProcess(
            result.args,
            result.returncode,
            result.stdout.decode("utf-8"),
            result.stderr.decode("utf-8"),
        )

    def read(
        self,
        *args: str,
        timeout: int = 45,
        cwd: Path = REPO,
        env: dict[str, str] | None = None,
    ) -> str:
        try:
            r = self.command(list(args), timeout=timeout, cwd=cwd, env=env)
        except (OSError, subprocess.TimeoutExpired) as e:
            raise Failure("DAEMON_UNAVAILABLE") from e
        require(r.returncode == 0, "DOCKER_READ_FAILED:" + args[0])
        require(len(r.stdout.encode()) < 64_000_000, "CAPTURE_TOO_LARGE")
        return r.stdout

    def binding(self) -> dict[str, Any]:
        selected = subprocess.check_output(
            ["docker", "context", "show"], text=True, timeout=15
        ).strip()
        require(selected == "desktop-linux", "DOCKER_CONTEXT_UNSUPPORTED")
        context = json.loads(self.read("context", "inspect", selected))
        require(
            len(context) == 1
            and context[0]["Endpoints"]["docker"]["Host"].startswith("unix://"),
            "DOCKER_CONTEXT_UNSUPPORTED",
        )
        info = json.loads(self.read("info", "--format", "{{json .}}"))
        require(
            info["OSType"] == "linux" and info["Architecture"] in ("arm64", "aarch64"),
            "IMAGE_PLATFORM_DRIFT",
        )
        binding = {
            "context": context,
            "daemon": {
                k: info[k]
                for k in (
                    "ID",
                    "Name",
                    "OSType",
                    "Architecture",
                    "ServerVersion",
                    "KernelVersion",
                    "DockerRootDir",
                )
            },
        }
        if self.bound is not None:
            require(binding == self.bound, "DAEMON_IDENTITY_DRIFT")
        return binding

    def ids(self) -> dict[str, list[str]]:
        return {
            "container": sorted(self.read("ps", "-aq", "--no-trunc").split()),
            "network": sorted(self.read("network", "ls", "-q", "--no-trunc").split()),
            "volume": sorted(self.read("volume", "ls", "-q").split()),
        }

    def capture(self) -> dict[str, Any]:
        binding = self.binding()
        ids = self.ids()
        rows = {}
        for kind in KINDS:
            rows[kind] = (
                json.loads(self.read(kind, "inspect", *ids[kind])) if ids[kind] else []
            )
            observed = [x["Name" if kind == "volume" else "Id"] for x in rows[kind]]
            require(
                sorted(observed) == ids[kind] and len(observed) == len(set(observed)),
                "INCOMPLETE_CAPTURE",
            )
        images = [
            json.loads(x)
            for x in self.read(
                "image", "ls", "--no-trunc", "--digests", "--format", "{{json .}}"
            ).splitlines()
        ]
        require(ids == self.ids() and binding == self.binding(), "RACED_CAPTURE")
        return {
            "binding": binding,
            "ids": ids,
            "resources": rows,
            "images": sorted(images, key=lambda x: json.dumps(x, sort_keys=True)),
            **now(),
        }


def static_inventory(view: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(view)
    result.pop("utc", None)
    result.pop("monotonic_ns", None)
    result["images"] = image_inventory(view["images"])
    for c in result["resources"]["container"]:
        c.pop("State", None)
        c.pop("RestartCount", None)
        c["Mounts"] = sorted(c["Mounts"], key=lambda x: json.dumps(x, sort_keys=True))
    return result


def image_inventory(images: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Human-relative CreatedSince is not immutable image identity."""
    return sorted(
        [
            {
                key: row[key]
                for key in ("ID", "Repository", "Tag", "Digest", "CreatedAt")
            }
            for row in images
        ],
        key=lambda row: json.dumps(row, sort_keys=True),
    )
