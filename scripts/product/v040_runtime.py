"""Fixed local Product runtime operations for the activated v0.4 campaign.

No model-generated command interface. All Docker operations use fixed service,
project and file sets; resource ownership is rechecked before every mutation.
"""

from __future__ import annotations

from contextlib import contextmanager
from collections.abc import Iterator
from datetime import UTC, datetime
import fcntl
import stat
import json
import hashlib
import os
from pathlib import Path
import secrets
import socket
import subprocess
from typing import Any

from ecomsre.product.remediation.payment_control import digest
from ecomsre.product.remediation.window_requests import create_private_file
from scripts.live_sandbox.product_v040 import GOAL_SHA256

PROJECT = "ecomsre-product-v040"
SERVICES = {
    "api",
    "worker",
    "remediation-observer",
    "remediation-control-gateway",
    "remediation-executor",
}
GOAL_LABEL = "io.ecomsre.product.v040.goal"


def validate_resolved_tmpfs(plan: dict[str, Any]) -> None:
    """Reject YAML flow-list splitting before any Docker build or startup."""
    if set(plan["services"]) != SERVICES:
        raise ValueError("resolved service inventory differs")
    for name, service in plan["services"].items():
        size = 64 if name in {"api", "worker"} else 16
        if service.get("tmpfs") != [f"/tmp:rw,noexec,nosuid,nodev,size={size}m"]:
            raise ValueError("resolved private tmpfs mount differs")


def read_json(path: Path) -> Any:
    if path.is_symlink():
        raise ValueError("private path is a symlink")
    return json.loads(path.read_bytes())


def seal_private(path: Path, value: object) -> None:
    create_private_file(
        path, (json.dumps(value, sort_keys=True, indent=2) + "\n").encode()
    )


def atomic_private(path: Path, value: object) -> None:
    temporary = path.with_name(path.name + "." + secrets.token_hex(8))
    seal_private(temporary, value)
    os.replace(temporary, path)
    fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


class ProductRuntimeV040:
    def __init__(self, repository: Path) -> None:
        self.repository = repository.resolve()
        self.private = self.repository / ".local/product-v040/campaign"
        self.env: dict[str, str] = {}
        self.files = (
            self.repository / "docker-compose.product.yml",
            self.repository / "config/product-v040/remediation-network.v1.yml",
            self.repository / "config/product-v040/live-runtime.v1.yml",
        )

    @contextmanager
    def operation_lock(self) -> Iterator[None]:
        """Exclude another campaign, cleanup or exporter process on this root."""
        os.umask(0o077)
        parent = self.private.parent
        parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if parent.is_symlink() or stat.S_IMODE(parent.stat().st_mode) != 0o700:
            raise ValueError("operation lock parent is not private")
        fd = os.open(parent / "operation.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600:
                raise ValueError("operation lock is not a private regular file")
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise RuntimeError("another campaign operation is active; no mutation") from error
            yield
        finally:
            os.close(fd)

    def command(
        self, argv: tuple[str, ...], *, timeout: int = 30, compose: bool = False
    ) -> str:
        env = {
            key: value
            for key, value in os.environ.items()
            if key
            not in {
                "DOCKER_HOST",
                "DOCKER_CONTEXT",
                "COMPOSE_FILE",
                "COMPOSE_PROJECT_NAME",
            }
        }
        # Explicit daemon context and a minimal Compose interpolation map prevent
        # another shell's target or credentials from selecting a different runtime.
        env.update(self.env if compose else {})
        result = subprocess.run(
            argv,
            cwd=self.repository,
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        if result.returncode:
            if self.private.exists():
                errors = self.private / "errors"
                errors.mkdir(mode=0o700, exist_ok=True)
                seal_private(
                    errors / (secrets.token_hex(12) + ".json"),
                    {
                        "created_at": datetime.now(UTC).isoformat(),
                        "argv": argv,
                        "returncode": result.returncode,
                        "stdout": result.stdout,
                        "stderr": result.stderr,
                    },
                )
            raise RuntimeError(
                "fixed local runtime operation failed; private evidence retained"
            )
        return result.stdout

    def docker(self, *args: str, timeout: int = 30) -> str:
        return self.command(
            ("docker", "--context", "desktop-linux", *args), timeout=timeout
        )

    def compose(self, *args: str, timeout: int = 60) -> str:
        return self.command(
            (
                "docker",
                "--context",
                "desktop-linux",
                "compose",
                "--project-name",
                PROJECT,
                *(part for path in self.files for part in ("-f", str(path))),
                *args,
            ),
            timeout=timeout,
            compose=True,
        )

    def boundary(self) -> dict[str, str]:
        endpoint = json.loads(
            self.docker(
                "context",
                "inspect",
                "desktop-linux",
                "--format",
                "{{json .Endpoints.docker.Host}}",
            )
        )
        info = json.loads(self.docker("info", "--format", "{{json .}}"))
        if (
            not isinstance(endpoint, str)
            or not endpoint.startswith("unix:///")
            or info.get("OSType") != "linux"
            or info.get("Architecture") not in {"arm64", "aarch64"}
        ):
            raise ValueError("Docker authority is not local ARM64")
        actual = {
            "context": "desktop-linux",
            "endpoint": endpoint,
            "daemon_id": info["ID"],
        }
        path = self.private / "host/daemon.json"
        if path.exists() and read_json(path) != actual:
            raise ValueError("Docker daemon authority drift")
        return actual

    def build_context(self) -> dict[str, dict[str, str]]:
        context = self.private / "host/build-context"
        context.mkdir(mode=0o700)
        names = self.command(
            (
                "git",
                "ls-files",
                "--stage",
                "-z",
                "--",
                "Dockerfile.product",
                "pyproject.toml",
                "uv.lock",
                "src",
                "config/product-v040/remediation-registry.v1.json",
            )
        ).split("\0")
        bindings: dict[str, dict[str, str]] = {}
        for entry in filter(None, names):
            metadata, name = entry.split("\t", 1)
            mode, _, stage = metadata.split()
            if stage != "0" or mode not in {"100644", "100755"}:
                raise ValueError("build index input mode or merge stage differs")
            source = self.repository / name
            if source.is_symlink() or not source.is_file():
                raise ValueError("build input is not a regular tracked file")
            value = source.read_bytes()
            destination = context / name
            parent = context
            for part in destination.parent.relative_to(context).parts:
                parent = parent / part
                parent.mkdir(mode=0o700, exist_ok=True)
            create_private_file(destination, value)
            bindings[name] = {
                "sha256": hashlib.sha256(value).hexdigest(),
                "git_mode": mode,
            }
        if "config/product-v040/remediation-registry.v1.json" not in bindings:
            raise ValueError("build registry missing")
        # These are already-public tracked source copies, not private evidence.
        # Docker preserves modes: normalize the source tree for its non-root
        # runtime while the enclosing private context root remains 0700.
        for copied in context.rglob("*"):
            copied.chmod(0o755 if copied.is_dir() else 0o644)
        seal_private(self.private / "host/build-inputs.json", bindings)
        return bindings

    def initialize(self) -> None:
        if self.private.exists() or self.private.is_symlink():
            raise FileExistsError(
                "campaign private root already exists; preserve and resume"
            )
        self.private.mkdir(mode=0o700, parents=True)
        for name in (
            "product",
            "read",
            "write",
            "config",
            "ledger",
            "control",
            "proxy",
            "observer",
            "requests",
            "errors",
            "host",
            "sandbox",
        ):
            (self.private / name).mkdir(mode=0o700)
        for name in ("product/pilot", "observer/windows", "host/windows"):
            (self.private / name).mkdir(mode=0o700)
        head = self.command(("git", "rev-parse", "HEAD")).strip()
        self.env = {
            "ECOMSRE_V040_ROOT": str(self.private),
            "ECOMSRE_V040_USER": f"{os.getuid()}:{os.getgid()}",
            "ECOMSRE_V040_IMAGE": f"ecomsre-product-v040:{head[:12]}",
            "ECOMSRE_PRODUCT_API_PORT": "18001",
            "ECOMSRE_ADMIN_TOKEN": secrets.token_urlsafe(32),
            "ECOMSRE_REMEDIATION_READ_TOKEN": secrets.token_urlsafe(32),
            "ECOMSRE_REMEDIATION_WRITE_TOKEN": secrets.token_urlsafe(32),
            "ECOMSRE_REMEDIATION_WINDOW_TOKEN": secrets.token_urlsafe(32),
            "ECOMSRE_REMEDIATION_OBSERVER_TOKEN": secrets.token_urlsafe(32),
            "ECOMSRE_REMEDIATION_API_BINDING_PATH": "",
        }
        seal_private(
            self.private / "host/runtime.json",
            {"goal_sha256": GOAL_SHA256, "build_head": head, "environment": self.env},
        )
        seal_private(self.private / "host/daemon.json", self.boundary())
        seal_private(
            self.private / "proxy/observation-proxy.json",
            {
                "prometheus_base_url": "http://host.docker.internal:19090",
                "jaeger_base_url": "http://host.docker.internal:11686",
                "opensearch_base_url": "http://host.docker.internal:19200",
            },
        )

    def load(self) -> None:
        body = read_json(self.private / "host/runtime.json")
        if body["goal_sha256"] != GOAL_SHA256:
            raise ValueError("runtime Goal differs")
        self.env = body["environment"]
        if (self.private / "host/product-ownership.json").exists():
            self.env["ECOMSRE_REMEDIATION_API_BINDING_PATH"] = (
                "/run/remediation-config/binding.json"
            )
        if self.env["ECOMSRE_V040_ROOT"] != str(self.private):
            raise ValueError("runtime root differs")

    def owned(self) -> dict[str, list[str]]:
        result: dict[str, list[str]] = {}
        for kind in ("container", "network", "volume"):
            rows = self.docker(
                kind,
                "ls",
                "-q",
                *(("--all",) if kind == "container" else ()),
                "--filter",
                f"label=com.docker.compose.project={PROJECT}",
            ).split()
            if rows:
                inspected = json.loads(self.docker(kind, "inspect", *rows))
                for item in inspected:
                    labels = (
                        item["Config"]["Labels"]
                        if kind == "container"
                        else item["Labels"]
                    )
                    if (
                        labels.get(GOAL_LABEL) != GOAL_SHA256
                        or labels.get("com.docker.compose.project") != PROJECT
                    ):
                        raise ValueError("Product resource ownership is unknown")
                    if (
                        kind == "container"
                        and labels.get("com.docker.compose.service") not in SERVICES
                    ):
                        raise ValueError("Product service is not allowed")
            result[kind] = rows
        return result

    def build(self) -> dict[str, Any]:
        self.boundary()
        if any(self.owned().values()):
            raise ValueError("Product resources already exist before build")
        if self.command(
            ("git", "status", "--porcelain", "--untracked-files=normal")
        ).strip():
            raise ValueError("Product build requires a clean reviewed source tree")
        resolved = json.loads(
            self.compose("--profile", "remediation", "config", "--format", "json")
        )
        validate_resolved_tmpfs(resolved)
        for name, service in resolved["services"].items():
            if (
                name not in SERVICES
                or service["image"] != self.env["ECOMSRE_V040_IMAGE"]
            ):
                raise ValueError("resolved Product image/service differs")
        seal_private(self.private / "host/resolved-compose-bootstrap.json", resolved)
        inputs = self.build_context()
        self.docker(
            "build",
            "--platform",
            "linux/arm64",
            "--file",
            str(self.private / "host/build-context/Dockerfile.product"),
            "--tag",
            self.env["ECOMSRE_V040_IMAGE"],
            str(self.private / "host/build-context"),
            timeout=1200,
        )
        image = json.loads(
            self.docker(
                "image",
                "inspect",
                "--platform",
                "linux/arm64",
                self.env["ECOMSRE_V040_IMAGE"],
            )
        )[0]
        if image["Architecture"] != "arm64" or image["Os"] != "linux":
            raise ValueError("Product image platform differs")
        record = {
            "image_id": image["Id"],
            "image_tag": self.env["ECOMSRE_V040_IMAGE"],
            "build_head": self.command(("git", "rev-parse", "HEAD")).strip(),
            "compose_sha256": digest(resolved),
            "source_inputs_sha256": digest(inputs),
        }
        seal_private(self.private / "host/product-build.json", record)
        return record

    def reject_network_conflicts(self) -> None:
        names = self.docker("network", "ls", "--format", "{{.Name}}").splitlines()
        if {
            "ecomsre-product-v040-default",
            "ecomsre-product-v040_remediation-observation",
        } & set(names):
            raise ValueError("fixed Product network name already exists")

    def start_bootstrap(self) -> None:
        self.boundary()
        self.reject_network_conflicts()
        if any(self.owned().values()):
            raise ValueError("Product resources already exist before startup")
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 18001))
        self.compose(
            "up",
            "-d",
            "--pull",
            "never",
            "--no-build",
            "--wait",
            "--wait-timeout",
            "60",
            "api",
            "worker",
            "remediation-observer",
            timeout=90,
        )
        if len(self.owned()["container"]) != 3:
            raise ValueError("Product bootstrap inventory differs")

    def enable(self) -> None:
        self.boundary()
        self.owned()
        for name in (
            "config/binding.json",
            "config/recovery-policy.json",
            "control/profile.json",
        ):
            read_json(self.private / name)
        self.env["ECOMSRE_REMEDIATION_API_BINDING_PATH"] = (
            "/run/remediation-config/binding.json"
        )
        self.compose(
            "--profile",
            "remediation",
            "up",
            "-d",
            "--pull",
            "never",
            "--no-build",
            "--wait",
            "--wait-timeout",
            "60",
            "api",
            "remediation-control-gateway",
            "remediation-executor",
            timeout=90,
        )
        if len(self.owned()["container"]) != 5:
            raise ValueError("enabled Product inventory differs")

    def cleanup(self) -> dict[str, list[str]]:
        self.boundary()
        self.owned()
        self.compose(
            "--profile",
            "remediation",
            "down",
            "--volumes",
            "--remove-orphans",
            "--timeout",
            "30",
            timeout=180,
        )
        after = self.owned()
        if any(after.values()):
            raise ValueError("Product cleanup incomplete")
        return after
