"""Versioned authority, clocks and immutable image compatibility checks."""

from datetime import UTC, datetime
import hashlib
from pathlib import Path
import subprocess
import time
from typing import Any
from scripts.product.minimal_payment_acceptance_v040.owned import Owned as BaseOwned
from scripts.product.minimal_payment_acceptance_v040.plan import (
    REPO,
    build_plan as base_plan,
    bind,
)

GOAL = hashlib.sha256(
    (
        REPO
        / "docs/goals/EcomSRE_Product_v0.4.1_Presentation_and_Live_Safety_Evaluation_Goal.md"
    ).read_bytes()
).hexdigest()


def stamp() -> dict[str, Any]:
    return {
        "utc": datetime.now(UTC).isoformat(),
        "monotonic_ns": time.monotonic_ns(),
        "clock_scope": "harness-process",
    }


class Owned(BaseOwned):
    def __init__(self, root: Path, nonce: str):
        super().__init__(root, nonce)
        self.labels["io.ecomsre.minimal.goal"] = GOAL
        self.labels["io.ecomsre.safety.case"] = nonce


def compatible_image(image: dict[str, Any], head: str) -> str:
    source = image["Config"].get("Labels", {}).get("io.ecomsre.minimal.source")
    if not source:
        raise ValueError("IMAGE_SOURCE_UNBOUND")
    delta = subprocess.check_output(
        [
            "git",
            "diff",
            "--name-only",
            source,
            head,
            "--",
            "src",
            "pyproject.toml",
            "uv.lock",
            "config/product-v040/remediation-registry.v1.json",
            "config/product-v040/minimal-payment/Dockerfile",
        ],
        cwd=REPO,
        text=True,
    )
    if delta:
        raise ValueError("IMAGE_RUNTIME_SOURCE_CHANGED")
    return source


def build_plan(
    root: Path, nonce: str, images: Any, ports: Any, labels: Any, keys: Any
) -> Any:
    plan = base_plan(root, nonce, images, ports, labels, keys)
    import copy

    api = copy.deepcopy(plan["services"]["api"])
    api.pop("ports", None)
    api.pop("networks", None)
    api["network_mode"] = "none"
    api["container_name"] = nonce + "-bound-api"
    api["volumes"] += [
        bind(root / "config", "/run/remediation-config"),
        bind(nonce + "-read-socket", "/run/remediation-read", True, "volume"),
    ]
    api["environment"].update(
        {
            "ECOMSRE_REMEDIATION_BINDING_PATH": "/run/remediation-config/binding.json",
            "ECOMSRE_REMEDIATION_READ_SOCKET": "/run/remediation-read/control.sock",
            "ECOMSRE_REMEDIATION_READ_TOKEN": keys["read"],
        }
    )
    plan["services"]["bound-api"] = api
    for name in ("api", "bound-api"):
        for mount in plan["services"][name]["volumes"]:
            if mount["target"] == "/api_transport.py":
                mount["source"] = str(
                    REPO / "scripts/product/live_safety_v041/api_transport.py"
                )
    plan["services"]["remediation-executor"]["volumes"].append(
        bind(REPO / "scripts/product/live_safety_v041/replay.py", "/replay.py")
    )
    plan["services"]["payment-control"]["command"] = [
        "python",
        "/safety/flag_transport.py",
    ]
    plan["services"]["payment-control"]["volumes"].append(
        bind(REPO / "scripts/product/live_safety_v041", "/safety")
    )
    return plan
