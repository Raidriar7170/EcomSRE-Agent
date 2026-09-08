"""Bounded local listener census and exact host stability observations."""

from __future__ import annotations
from typing import Any
import subprocess
from .common import PORTS, require, now


def listeners() -> dict[str, Any]:
    rows = {}
    for port in PORTS:
        result = subprocess.run(
            ["lsof", "-nP", "-iTCP:" + str(port), "-sTCP:LISTEN", "-Fpcn"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        require(
            result.returncode in (0, 1) and len(result.stdout) < 100000,
            "LISTENER_CAPTURE_FAILED",
        )
        rows[str(port)] = result.stdout.splitlines()
    return {"listeners": rows, **now()}


def require_ports_free(view: dict[str, Any]) -> None:
    require(not any(view["listeners"].values()), "LOOPBACK_PORT_OCCUPIED")


def require_no_preexisting_owned(view: dict[str, Any]) -> None:
    """No inherited project namespace is admitted as an unrelated baseline."""
    for kind, records in view["resources"].items():
        for row in records:
            labels = (
                row.get("Config", {}).get("Labels")
                if kind == "container"
                else row.get("Labels")
            ) or {}
            name = row["Name"].lstrip("/").lower()
            project = str(labels.get("com.docker.compose.project", "")).lower()
            require(
                not any("ecomsre" in key.lower() for key in labels)
                and "ecomsre" not in name
                and "ecomsre" not in project
                and not name.startswith(("qualification-", "product-v040-")),
                "PREEXISTING_PROJECT_RESOURCE:" + kind,
            )
