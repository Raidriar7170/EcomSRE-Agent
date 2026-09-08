"""Cleanup decisions derive only from plan-validated birth receipts."""

from __future__ import annotations
from typing import Any
from copy import deepcopy
from .common import LABEL, require, digest
from .identity import immutable, process_matches, validate_network_stage


def resource_identity(kind: str, row: dict[str, Any]) -> dict[str, Any]:
    if kind == "container":
        return {
            **immutable(row),
            "OomKillDisable": row["HostConfig"].get("OomKillDisable"),
        }
    if kind == "network":
        return {
            k: row.get(k)
            for k in (
                "Id",
                "Name",
                "Created",
                "Driver",
                "Scope",
                "EnableIPv4",
                "EnableIPv6",
                "Internal",
                "Attachable",
                "Ingress",
                "IPAM",
                "ConfigFrom",
                "ConfigOnly",
                "Options",
                "Labels",
            )
        }
    require(kind == "volume", "UNKNOWN_RESOURCE_KIND")
    return {
        k: row.get(k)
        for k in (
            "Name",
            "CreatedAt",
            "Driver",
            "Scope",
            "Options",
            "Labels",
            "Mountpoint",
        )
    }


def cleanup_command(
    kind: str,
    birth: dict[str, Any],
    inventory: dict[str, Any],
    attempt: str,
    *,
    operation: str,
) -> list[str]:
    require(
        birth.get("plan_validated") is True and birth.get("attempt_id") == attempt,
        "UNTRUSTED_BIRTH",
    )
    require(birth["binding"] == inventory["binding"], "DAEMON_IDENTITY_DRIFT")
    recorded = birth["record"]
    identifier = recorded["Name" if kind == "volume" else "Id"]
    current_rows = inventory["resources"][kind]
    rows = [
        r for r in current_rows if r["Name" if kind == "volume" else "Id"] == identifier
    ]
    require(len(rows) == 1, "TARGET_NOT_PRESENT")
    current = rows[0]
    original_identity = resource_identity(kind, recorded)
    current_identity = resource_identity(kind, current)
    if (
        kind == "container"
        and original_identity["OomKillDisable"]
        is not current_identity["OomKillDisable"]
    ):
        proof = birth.get("oom_proof") or {}
        require(
            birth["role"] == "kafka-volume-probe"
            and original_identity["OomKillDisable"] is False
            and current_identity["OomKillDisable"] is None
            and proof.get("container_id") == current["Id"]
            and proof.get("binding_digest") == digest(inventory["binding"])
            and proof.get("immutable_digest") == digest(immutable(current))
            and proof.get("started_at") == current["State"].get("StartedAt"),
            "CLEANUP_OOM_DRIFT",
        )
        current_identity["OomKillDisable"] = False
    require(original_identity == current_identity, "CLEANUP_IDENTITY_DRIFT")
    own_labels = (
        current["Config"].get("Labels")
        if kind == "container"
        else current.get("Labels")
    )
    require((own_labels or {}).get(LABEL) == attempt, "CLEANUP_OWNERSHIP_DRIFT")
    if kind == "container":
        process_matches(current, birth["process"])
        stage = (
            "running"
            if current["State"]["Running"]
            else (
                "created" if current["State"].get("Status") == "created" else "stopped"
            )
        )
        validate_network_stage(current, birth["network_ids"], stage)
        require(operation in ("stop", "remove"), "CLEANUP_OPERATION_DENIED")
        if operation == "stop":
            require(current["State"]["Running"] is True, "STOP_NOT_REQUIRED")
            return ["container", "stop", "--time", "30", identifier]
        require(
            current["State"]["Running"] is False
            and not current["State"].get("Paused")
            and not current["State"].get("Restarting"),
            "REMOVE_RUNNING_DENIED",
        )
        return ["container", "rm", identifier]
    require(operation == "remove", "CLEANUP_OPERATION_DENIED")
    containers = inventory["resources"]["container"]
    if kind == "network":
        require(not current.get("Containers"), "NETWORK_ENDPOINT_PRESENT")
        require(
            not any(
                identifier == n.get("NetworkID")
                for c in containers
                for n in c["NetworkSettings"]["Networks"].values()
            ),
            "NETWORK_ATTACHED",
        )
    else:
        require(
            not any(
                m.get("Name") == identifier for c in containers for m in c["Mounts"]
            ),
            "VOLUME_ATTACHED",
        )
    return [kind, "rm", identifier]


def nonowned(
    view: dict[str, Any],
    births: list[dict[str, Any]],
    attempt: str,
    probe_id: str | None = None,
) -> dict[str, Any]:
    """Exclude birth-bound resources only; unknown attempt-labelled objects reject."""
    owned = {
        (b["kind"], b["record"]["Name" if b["kind"] == "volume" else "Id"])
        for b in births
    }
    result: dict[str, Any] = {"binding": view["binding"], "resources": {}}
    for kind, records in view["resources"].items():
        kept = []
        for record in records:
            identifier = record["Name" if kind == "volume" else "Id"]
            labels = (
                record["Config"].get("Labels", {})
                if kind == "container"
                else record.get("Labels", {})
            )
            if (kind, identifier) in owned:
                continue
            require((labels or {}).get(LABEL) != attempt, "UNKNOWN_ATTEMPT_RESOURCE")
            identity = deepcopy(record)
            if kind == "container":
                identity.pop("State", None)
                identity.pop("RestartCount", None)
            if kind == "network":
                endpoints = dict(record.get("Containers") or {})
                if record["Name"] == "none" and probe_id in endpoints:
                    require(("container", probe_id) in owned, "UNKNOWN_NONE_ENDPOINT")
                    endpoints.pop(probe_id)
                identity["Containers"] = endpoints
            kept.append(identity)
        result["resources"][kind] = sorted(
            kept, key=lambda r: r.get("Id", r.get("Name", ""))
        )
    # Image inventory is checked separately allowing only explicitly bound builds/pulls.
    return result
