"""Frozen constants, private evidence and deterministic assertions."""

from __future__ import annotations
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
from typing import Any

BASE = "cc941b51cbff9287b876be49652cd0ad83030474"
TREE = "1b3baa986bcffc00dd4064c8b6c147c30f15e4d3"
UPSTREAM = "1755859a9de82c2e5e225be68abc401a5ebf2b4f"
GOAL_SHA = "3f3751ec1b957547da508e0068d36eb1c7877d4374477820f2b370de2d16ece5"
GOAL_PATH = (
    "docs/goals/EcomSRE_Product_v0.4_Live_Harness_Engineering_Preflight_v4_Goal.md"
)
REPO = Path(__file__).resolve().parents[3]
ROOT = REPO / ".local/product-v040-preflight-v4"
LABEL = "io.ecomsre.preflight.v4.attempt"
ROLE_LABEL = "io.ecomsre.preflight.v4.role"
GOAL_LABEL = "io.ecomsre.preflight.v4.goal"
KINDS = ("container", "network", "volume")
PORTS = (18080, 18016, 19090, 19200, 11686, 18001)
KAFKA_PATHS = ("/etc/kafka/secrets", "/mnt/shared/config", "/var/lib/kafka/data")


class Failure(RuntimeError):
    """Safe typed failure, retained before cleanup."""


def require(ok: bool, code: str) -> None:
    if not ok:
        raise Failure(code)


def canonical(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def digest(value: Any) -> str:
    return sha(canonical(value))


def now() -> dict[str, Any]:
    return {"utc": datetime.now(UTC).isoformat(), "monotonic_ns": time.monotonic_ns()}


def seal(root: Path, name: str, value: Any) -> str:
    path = root / name
    require(
        not path.is_symlink()
        and ".." not in Path(name).parts
        and not Path(name).is_absolute(),
        "EVIDENCE_PATH",
    )
    path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    data = canonical(value) + b"\n"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    return sha(data)


def git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=REPO, text=True).strip()


def load(path: Path) -> Any:
    require(not path.is_symlink(), "EVIDENCE_SYMLINK")
    return json.loads(path.read_bytes())
