"""Exact PR98 retained-resource cleanup; no raw fingerprint normalization."""

from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
import hashlib
import json
import posixpath
from pathlib import Path
import subprocess
from typing import Any

from scripts.product.qualification_v040.guard import require, container_fixed, labels
from scripts.product.qualification_v040.capture import image_binding

GOAL_SHA = "bb5373899fc8523007b6e9cec0b865403450e4bf4f15b81426ae20f6e1f2deeb"
QUAL = "ebbcf1e4e3e740a296f294fb9ab6047c"
CONTAINER = "c1e1f654931ebe062ae3253727adf884c28ea580781bef5976f322ac06bed66f"
PREFIX = "ecomsre-v040-" + QUAL + "-"
VOLUMES = tuple(
    PREFIX + suffix
    for suffix in (
        "astronomy-db-data",
        "jaeger-data",
        "prometheus-data",
        "qualification-kafka-config",
        "qualification-kafka-data",
        "qualification-kafka-secrets",
    )
)
LABEL = "io.ecomsre.product.v040.qualification"


def seal(path: Path, value: Any) -> None:
    with path.open("x", encoding="utf-8") as out:
        json.dump(value, out, indent=2, sort_keys=True)
        out.write("\n")
    path.chmod(0o600)


def docker(*args: str) -> str:
    return subprocess.check_output(
        ["docker", "--context", "desktop-linux", *args], text=True, timeout=60
    )


def inventory_pass() -> dict[str, Any]:
    ids = {
        "containers": docker("container", "ls", "-aq", "--no-trunc").split(),
        "networks": docker("network", "ls", "-q", "--no-trunc").split(),
        "volumes": docker("volume", "ls", "-q").split(),
    }
    return {
        "ids": ids,
        "inspect": {
            kind: json.loads(docker(kind[:-1], "inspect", *values)) if values else []
            for kind, values in ids.items()
        },
        "daemon": json.loads(docker("info", "--format", "{{json .}}")),
        "context": json.loads(docker("context", "inspect", "desktop-linux")),
        "images": [
            json.loads(line)
            for line in docker(
                "image", "ls", "--no-trunc", "--digests", "--format", "{{json .}}"
            ).splitlines()
        ],
    }


def image_set(rows: list[dict[str, Any]]) -> list[str]:
    return sorted(json.dumps(row, sort_keys=True) for row in rows)


def validate_capture(first: dict[str, Any], second: dict[str, Any]) -> None:
    for view in (first, second):
        require(
            set(view["ids"])
            == set(view["inspect"])
            == {"containers", "networks", "volumes"},
            "CLEANUP_INCOMPLETE_INVENTORY",
        )
        for kind, ids in view["ids"].items():
            observed = [
                row["Name" if kind == "volumes" else "Id"]
                for row in view["inspect"][kind]
            ]
            require(
                len(ids) == len(set(ids)) == len(observed) == len(set(observed))
                and set(ids) == set(observed),
                "CLEANUP_INCOMPLETE_INVENTORY",
            )
    for kind in first["ids"]:

        def canonical(view: dict[str, Any]) -> list[dict[str, Any]]:
            return sorted(
                view["inspect"][kind], key=lambda row: json.dumps(row, sort_keys=True)
            )

        require(canonical(first) == canonical(second), "CLEANUP_RACED_INVENTORY")
    require(first["context"] == second["context"], "CLEANUP_CONTEXT_DRIFT")
    require(
        all(
            first["daemon"].get(k) == second["daemon"].get(k)
            for k in (
                "ID",
                "Name",
                "DockerRootDir",
                "OSType",
                "Architecture",
                "ServerVersion",
            )
        ),
        "CLEANUP_DAEMON_DRIFT",
    )
    require(
        image_set(first["images"]) == image_set(second["images"]),
        "CLEANUP_IMAGE_SET_DRIFT",
    )


def snapshot() -> dict[str, Any]:
    first, second = inventory_pass(), inventory_pass()
    return {**first, "utc": datetime.now(UTC).isoformat(), "second_pass": second}


def overlaps(source: str, mountpoint: str) -> bool:
    require(
        source.startswith("/") and mountpoint.startswith("/"),
        "CLEANUP_INVALID_MOUNT_SOURCE",
    )
    a, b = posixpath.normpath(source), posixpath.normpath(mountpoint)
    return (
        a == b or a.startswith(b.rstrip("/") + "/") or b.startswith(a.rstrip("/") + "/")
    )


def validate(
    current: dict[str, Any],
    retained: dict[str, Any],
    remaining: set[str],
    container_present: bool,
) -> None:
    rows = current["inspect"]
    old = retained["inspect"]
    require(
        current["daemon"]["ID"] == retained["daemon_before"]["daemon_id"],
        "CLEANUP_DAEMON_DRIFT",
    )
    require(
        current["context"][0]["Endpoints"]["docker"]["Host"]
        == retained["daemon_before"]["endpoint"],
        "CLEANUP_CONTEXT_DRIFT",
    )
    actual_owned = {
        row["Name"]
        for row in rows["volumes"]
        if labels("volumes", row).get(LABEL) == QUAL
    }
    require(actual_owned == remaining, "CLEANUP_VOLUME_SET_DRIFT")
    owned_containers = [
        row
        for row in rows["containers"]
        if labels("containers", row).get(LABEL) == QUAL
    ]
    require(
        [row["Id"] for row in owned_containers]
        == ([CONTAINER] if container_present else []),
        "CLEANUP_CONTAINER_SET_DRIFT",
    )
    require(
        not any(labels("networks", row).get(LABEL) == QUAL for row in rows["networks"]),
        "CLEANUP_UNKNOWN_NETWORK",
    )
    if container_present:
        row = owned_containers[0]
        original = next(v for v in old["containers"] if v["Id"] == CONTAINER)
        require(
            row["Name"] == original["Name"] == "/" + PREFIX + "volume-probe",
            "CLEANUP_NAME_DRIFT",
        )
        require(
            container_fixed(row) == container_fixed(original),
            "CLEANUP_IDENTITY_MISMATCH",
        )
        require(row["Created"] == original["Created"], "CLEANUP_IDENTITY_MISMATCH")
        require(row["HostConfig"]["NetworkMode"] == "none", "CLEANUP_NETWORK_DRIFT")
    original_volumes = {r["Name"]: r for r in old["volumes"]}
    for volume in remaining:
        found = [v for v in rows["volumes"] if v["Name"] == volume]
        require(
            len(found) == 1 and found[0] == original_volumes[volume],
            "CLEANUP_VOLUME_IDENTITY_MISMATCH",
        )
        attached = [
            c["Id"]
            for c in rows["containers"]
            for m in c["Mounts"]
            if m.get("Name") == volume
            or (
                m.get("Type") == "bind"
                and overlaps(m["Source"], original_volumes[volume]["Mountpoint"])
            )
            or m.get("Source") == original_volumes[volume]["Mountpoint"]
        ]
        original_attached = [
            c["Id"]
            for c in old["containers"]
            for m in c["Mounts"]
            if m.get("Name") == volume
        ]
        require(
            attached == (original_attached if container_present else []),
            "CLEANUP_ATTACHMENT_DRIFT",
        )


def main() -> None:
    root = Path(__file__).resolve().parents[3]
    goal = (
        root
        / "docs/goals/EcomSRE_Product_v0.4_Runtime_Qualification_Successor_v3_Goal.md"
    )
    require(
        hashlib.sha256(goal.read_bytes()).hexdigest() == GOAL_SHA, "GOAL_FREEZE_DRIFT"
    )
    oldroot = (
        root.parent
        / "product-v040-copyup-access-v2/.local/product-v040-qualifications"
        / ("qualification-" + QUAL)
    )
    index = json.loads((oldroot / "evidence-index.json").read_bytes())
    raw = (oldroot / "host/cleanup/000-inventory.json").read_bytes()
    require(
        hashlib.sha256(raw).hexdigest()
        == index["files"]["host/cleanup/000-inventory.json"],
        "HISTORY_DRIFT",
    )
    require(
        hashlib.sha256((oldroot / "evidence-index.json").read_bytes()).hexdigest()
        == "6d8e65ded181e806b6f0fac40a83961d5452fd57bfc7fde766d6fddb414e5793",
        "HISTORY_DRIFT",
    )
    retained = json.loads(raw)
    private = root / ".local/runtime-qualification-v3/retained-cleanup-order-v2"
    private.mkdir(parents=True, mode=0o700, exist_ok=False)
    private.parent.chmod(0o700)
    (private / "retained-reference.json").write_bytes(raw)
    (private / "retained-reference.json").chmod(0o600)
    remaining, present = set(VOLUMES), True
    receipt: dict[str, Any] = {
        "goal_sha256": GOAL_SHA,
        "status": "STARTED",
        "operations": [],
        "formal_authority": False,
    }
    counter = 0

    def checked(stage: str) -> dict[str, Any]:
        nonlocal counter
        current = snapshot()
        seal(private / f"{counter:03d}-{stage}.json", current)
        counter += 1
        validate_capture(current, current["second_pass"])
        validate(current, retained, remaining, present)
        ref = retained["inspect"]["containers"][0]["Config"]["Image"]
        image = json.loads(
            docker("image", "inspect", "--platform", "linux/arm64", ref)
        )[0]
        seal(private / f"{counter:03d}-{stage}-image.json", image)
        counter += 1
        require(
            image_binding(image) == retained["platform_images"][ref],
            "CLEANUP_IMAGE_DRIFT",
        )
        return current

    def mutate(args: tuple[str, ...]) -> None:
        seal(
            private / f"{counter:03d}-intent-{len(receipt['operations'])}.json",
            {"argv": args, "goal_sha256": GOAL_SHA},
        )
        output = docker(*args)
        receipt["operations"].append({"argv": args, "output": output})

    try:
        initial = checked("BEFORE_STOP")
        mutate(("container", "stop", "--time", "5", CONTAINER))
        stopped = checked("BEFORE_CONTAINER_REMOVE")
        require(
            not next(
                c for c in stopped["inspect"]["containers"] if c["Id"] == CONTAINER
            )["State"]["Running"],
            "CLEANUP_CONTAINER_RUNNING",
        )
        mutate(("container", "rm", CONTAINER))
        present = False
        for volume in VOLUMES:
            checked("BEFORE_VOLUME_REMOVE")
            mutate(("volume", "rm", volume))
            remaining.remove(volume)
        final = checked("AFTER_CLEANUP")

        def nonowned(snapshot: dict[str, Any]) -> dict[str, Any]:
            result = deepcopy(snapshot["inspect"])
            result["containers"] = [
                c for c in result["containers"] if c["Id"] != CONTAINER
            ]
            result["volumes"] = [
                v for v in result["volumes"] if v["Name"] not in VOLUMES
            ]
            for n in result["networks"]:
                n.get("Containers", {}).pop(CONTAINER, None)
            return {
                k: sorted(v, key=lambda row: json.dumps(row, sort_keys=True))
                for k, v in result.items()
            }

        require(nonowned(initial) == nonowned(final), "CLEANUP_NONOWNED_DRIFT")
        require(
            image_set(initial["images"]) == image_set(final["images"]),
            "CLEANUP_IMAGE_SET_DRIFT",
        )
        receipt.update(
            status="CLEAN",
            remaining_containers=0,
            remaining_volumes=0,
            non_owned_unchanged_except_removed_owned_endpoint=True,
        )
    except Exception as error:
        receipt.update(
            status="MANUAL_INTERVENTION_REQUIRED",
            code=getattr(error, "code", type(error).__name__),
            detail=str(error),
        )
        seal(private / "failure-inventory.json", snapshot())
        raise
    finally:
        receipt["ended_at"] = datetime.now(UTC).isoformat()
        seal(private / "CleanupReceipt.json", receipt)
        seal(
            private / "evidence-index.json",
            {
                p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                for p in private.iterdir()
                if p.is_file()
            },
        )
        print(json.dumps(receipt))


if __name__ == "__main__":
    main()
