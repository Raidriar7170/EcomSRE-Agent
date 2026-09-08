"""No-fault qualification inventory transitions; never grants formal authority."""

from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import time
from typing import Any

from scripts.product.qualification_v040.guard import QualificationBlocked

QUAL_LABEL = "io.ecomsre.product.v040.qualification"
KINDS = ("containers", "networks", "volumes", "images")
ZERO_COUNTS = {
    "formal_faults": 0,
    "remediation_writes": 0,
    "provider_calls": 0,
    "formal_campaign_executions": 0,
    "formal_candidates": 0,
    "approvals": 0,
    "attempt_authorizations": 0,
    "write_intents": 0,
    "executor_invocations": 0,
    "step_receipts": 0,
    "formal_recovery_windows": 0,
}


def sha(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()


def seal(path: Path, value: Any) -> None:
    from scripts.product.v040_runtime import seal_private

    seal_private(path, value)


def require(condition: bool, code: str, detail: str = "") -> None:
    if not condition:
        raise QualificationBlocked(code, detail)


def identity(kind: str, row: dict[str, Any]) -> str:
    return str(row["Name"] if kind == "volumes" else row["Id"])


def labels(kind: str, row: dict[str, Any]) -> dict[str, str]:
    return (
        row["Config"].get("Labels") if kind == "containers" else row.get("Labels")
    ) or {}


def name(kind: str, row: dict[str, Any]) -> str:
    return str(row["Name"]).removeprefix("/")


def container_fixed(row: dict[str, Any]) -> dict[str, Any]:
    # Runtime health/counters are retained in raw envelopes but may evolve.
    # Config, HostConfig, actual mount identities and attached network identities
    # cannot evolve after the one permitted resource birth.
    return {
        "Id": row["Id"],
        "Name": row["Name"],
        "Created": row["Created"],
        "Path": row.get("Path"),
        "Args": row.get("Args"),
        "Driver": row.get("Driver"),
        "Platform": row.get("Platform"),
        "ImageManifestDescriptor": row.get("ImageManifestDescriptor"),
        "AppArmorProfile": row.get("AppArmorProfile"),
        "ProcessLabel": row.get("ProcessLabel"),
        "MountLabel": row.get("MountLabel"),
        "Config": row["Config"],
        "HostConfig": row["HostConfig"],
        "Image": row["Image"],
        "Mounts": sorted(row["Mounts"], key=lambda m: m["Destination"]),
        "network_ids": {}
        if row["HostConfig"]["NetworkMode"] == "none"
        else {n: v["NetworkID"] for n, v in row["NetworkSettings"]["Networks"].items()},
    }


def resource_fixed(kind: str, row: dict[str, Any]) -> dict[str, Any]:
    if kind == "containers":
        return container_fixed(row)
    result = deepcopy(row)
    if kind == "networks":
        result.pop("Containers", None)  # Validated against the exact stage role set.
    return result


def validate_envelope(saved: dict[str, Any]) -> None:
    require(
        set(saved)
        == {
            "daemon_before",
            "daemon_after",
            "ids_before",
            "ids_after",
            "inspect",
            "mount_contents",
            "mount_contents_after",
            "platform_images",
            "platform_images_after",
            "inspect_after",
            "seed_properties",
        },
        "INCOMPLETE_CAPTURE",
    )
    require(saved["daemon_before"] == saved["daemon_after"], "DAEMON_IDENTITY_DRIFT")
    require(saved["ids_before"] == saved["ids_after"], "RACED_CAPTURE")
    require(
        set(saved["ids_before"]) == set(KINDS) == set(saved["inspect"]),
        "INCOMPLETE_CAPTURE",
    )
    require(
        saved["platform_images"] == saved["platform_images_after"],
        "RACED_IMAGE_CAPTURE",
    )
    require(set(saved["inspect_after"]) == set(KINDS), "INCOMPLETE_CAPTURE")

    def stable(kind: str, row: dict[str, Any]) -> dict[str, Any]:
        value = resource_fixed(kind, row)
        if kind == "containers":
            value["lifecycle"] = {
                k: row["State"].get(k)
                for k in ("Running", "Paused", "Restarting", "Dead", "OOMKilled")
            }
            value["restart_count"] = row.get("RestartCount", 0)
            value["network_endpoints"] = row["NetworkSettings"]["Networks"]
        elif kind == "networks":
            value["Containers"] = row.get("Containers") or {}
        return value

    for kind in KINDS:
        ids = saved["ids_before"][kind]
        for pass_name in ("inspect", "inspect_after"):
            rows = saved[pass_name][kind]
            require(len(ids) == len(set(ids)) == len(rows), "INCOMPLETE_CAPTURE", kind)
            actual = [identity(kind, row) for row in rows]
            require(len(actual) == len(set(actual)), "INCOMPLETE_CAPTURE", kind)
            for requested in ids:
                require(
                    sum(
                        i == requested
                        or (kind == "networks" and i.startswith(requested))
                        for i in actual
                    )
                    == 1,
                    "INCOMPLETE_CAPTURE",
                    kind,
                )
        require(
            {identity(kind, row): stable(kind, row) for row in saved["inspect"][kind]}
            == {
                identity(kind, row): stable(kind, row)
                for row in saved["inspect_after"][kind]
            },
            "RACED_INSPECTION",
            kind,
        )
    require(
        saved["mount_contents"] == saved["mount_contents_after"], "RACED_BIND_CAPTURE"
    )
    required_mounts = {
        row["Id"] + ":" + m["Destination"]
        for row in saved["inspect"]["containers"]
        for m in row["Mounts"]
    }
    require(
        set(saved["mount_contents"]) == required_mounts,
        "INCOMPLETE_CAPTURE",
        "mount contents",
    )


def validate_role(
    role: dict[str, Any], row: dict[str, Any], qualification: str
) -> None:
    kind = role["kind"]
    require(name(kind, row) == role["name"], "UNKNOWN_RESOURCE")
    actual_labels = labels(kind, row)
    require(
        actual_labels.get(QUAL_LABEL) == qualification
        and all(actual_labels.get(k) == v for k, v in role["labels"].items()),
        "OWNERSHIP_LABEL_DRIFT",
        role["name"],
    )
    automatic = (
        {
            "com.docker.compose." + key
            for key in (
                "config-hash",
                "container-number",
                "depends_on",
                "image",
                "oneoff",
                "project.config_files",
                "project.working_dir",
                "service",
                "version",
            )
        }
        if kind == "containers"
        else set()
    )
    require(
        set(actual_labels) <= set(role["labels"]) | automatic, "UNKNOWN_OWNERSHIP_LABEL"
    )
    if kind == "networks":
        require(
            row["Driver"] == "bridge"
            and row["Internal"] == role["internal"]
            and not row.get("Ingress", False),
            "NETWORK_CONFIGURATION_DRIFT",
        )
    elif kind == "volumes":
        require(
            row["Driver"] == "local" and not row.get("Options"), "VOLUME_SOURCE_DRIFT"
        )
    elif kind == "containers":
        require(
            row["Image"] == role["image_id"]
            or (row.get("ImageManifestDescriptor") or {}).get("digest")
            == role["platform_digest"],
            "IMAGE_PLATFORM_DRIFT",
        )
        require(
            row["Config"]["Image"] == role["image_reference"], "IMAGE_REFERENCE_DRIFT"
        )
        config, host = row["Config"], row["HostConfig"]
        require(config.get("User", "") == role["user"], "RUNTIME_USER_DRIFT")
        require(
            config.get("Entrypoint") == role["entrypoint"]
            and config.get("Cmd") == role["command"],
            "COMMAND_DRIFT",
        )
        require(
            sorted(config.get("Env") or []) == sorted(role["environment"]),
            "ENVIRONMENT_DRIFT",
        )
        require(
            not host.get("Privileged")
            and host.get("NetworkMode") not in {"host"}
            and not host.get("Devices")
            and not host.get("CapAdd"),
            "ISOLATION_DRIFT",
        )
        require(
            host.get("ReadonlyRootfs", False) == role["read_only"], "ISOLATION_DRIFT"
        )
        require((host.get("PortBindings") or {}) == role["ports"], "PORT_DRIFT")
        require(
            (host.get("Tmpfs") or {}) == role["tmpfs"], "MOUNT_SOURCE_DRIFT", "tmpfs"
        )
        actual = {m["Destination"]: m for m in row["Mounts"]}
        require(
            len(actual) == len(row["Mounts"])
            and set(actual) - set(role["tmpfs"]) == set(role["mounts"]),
            "MOUNT_SOURCE_DRIFT",
            "mount inventory",
        )
        for target in set(actual) & set(role["tmpfs"]):
            require(
                actual[target]["Type"] == "tmpfs" and actual[target]["RW"],
                "MOUNT_SOURCE_DRIFT",
                target,
            )
        for destination, expected in role["mounts"].items():
            mount = actual[destination]
            require(
                mount["Type"] == expected["type"] and mount["RW"] == expected["rw"],
                "MOUNT_SOURCE_DRIFT",
                destination,
            )
            if expected["type"] == "volume":
                require(
                    mount.get("Name") == expected["source"]
                    and mount.get("Driver") == "local",
                    "MOUNT_SOURCE_DRIFT",
                    destination,
                )
                host_mount: dict[str, Any] = next(
                    (
                        m
                        for m in host.get("Mounts", [])
                        if m.get("Target") == destination
                    ),
                    {},
                )
                require(
                    bool((host_mount.get("VolumeOptions") or {}).get("NoCopy", False))
                    == bool(expected.get("options", {}).get("nocopy", False)),
                    "COPYUP_OPTION_DRIFT",
                )
                require(
                    "nocopy" not in mount.get("Mode", "").split(","),
                    "COPYUP_OPTION_DRIFT",
                )
            elif expected["type"] == "bind":
                require(
                    mount["Source"] == expected["source"],
                    "MOUNT_SOURCE_DRIFT",
                    destination,
                )
        if role.get("network_none"):
            require(
                host["NetworkMode"] == "none"
                and set(row["NetworkSettings"]["Networks"]).issubset({"none"}),
                "NETWORK_IDENTITY_DRIFT",
            )
        else:
            require(
                set(row["NetworkSettings"]["Networks"]) == set(role["networks"]),
                "NETWORK_IDENTITY_DRIFT",
            )


class QualificationJournal:
    """Create-once plan, full raw captures, identity births and permanent latch.

    Expected roles and allowed lifecycles are fixed before any Docker creation.
    Docker-generated IDs may bind exactly once at a preauthorized birth stage;
    a previously bound ID cannot be replaced, even with identical labels.
    """

    def __init__(self, root: Path, plan: dict[str, Any]) -> None:
        self.root, self.plan = root, deepcopy(plan)
        root.mkdir(mode=0o700)
        require(
            plan["formal_authority"] is False and plan["counters"] == ZERO_COUNTS,
            "FORMAL_AUTHORITY_FORBIDDEN",
        )
        require(
            len(plan["stages"]) == len({s["name"] for s in plan["stages"]}),
            "INVALID_STAGE_PLAN",
        )
        roles = plan["roles"]
        require(
            len({(r["kind"], r["name"]) for r in roles.values()}) == len(roles),
            "INVALID_STAGE_PLAN",
        )
        for step in plan["stages"]:
            require(set(step["present_roles"]).issubset(roles), "INVALID_STAGE_PLAN")
        self.plan_sha = sha(plan)
        self.bound: dict[str, dict[str, Any]] = {}
        self.next_stage = 0
        self.seed_binding: dict[str, Any] | None = None
        self.generated_config: dict[str, Any] | None = None
        self.comparator = lambda bound, kind, row: bound == resource_fixed(kind, row)
        self.previous = self.plan_sha
        seal(root / "plan.json", plan)

    def block(
        self,
        code: str,
        stage: str,
        detail: str = "",
        actual: Any = None,
        observed_counters: dict[str, Any] | None = None,
        counter_evidence: dict[str, Any] | None = None,
    ) -> None:
        path = self.root / "first-divergence.json"
        if not path.exists():
            seal(
                path,
                {
                    "schema_version": "ecomsre.v040.qualification.first-divergence.v1",
                    "stage": stage,
                    "code": code,
                    "detail": detail,
                    "plan_sha256": self.plan_sha,
                    "previous_record_sha256": self.previous,
                    "actual_sha256": sha(actual),
                    "utc": datetime.now(UTC).isoformat(),
                    "monotonic_ns": time.monotonic_ns(),
                    "prohibited_action_budget": ZERO_COUNTS,
                    "observed_counters": observed_counters,
                    "counter_evidence": counter_evidence,
                    "formal_authority": False,
                },
            )
        raise QualificationBlocked(code, detail)

    def nonowned_view(
        self, rows: dict[str, Any], seen: dict[str, Any]
    ) -> dict[str, Any]:
        view = deepcopy(rows)
        probe = seen.get("probe/container/kafka-volume-probe")
        if probe is None:
            return view
        require(
            probe["HostConfig"]["NetworkMode"] == "none", "PROBE_NETWORK_MODE_DRIFT"
        )
        networks = [r for r in view["networks"].values() if r["Name"] == "none"]
        require(len(networks) == 1, "PROBE_NONE_NETWORK_UNBOUND")
        network = networks[0]
        baseline = self.plan["nonowned"]["networks"].get(network["Id"])
        require(baseline is not None, "PROBE_NONE_NETWORK_REPLACED")
        expected = deepcopy(baseline.get("Containers") or {})
        if probe["State"]["Running"]:
            endpoint = probe["NetworkSettings"]["Networks"].get("none", {})
            require(
                endpoint.get("NetworkID") == network["Id"]
                and bool(endpoint.get("EndpointID")),
                "PROBE_ENDPOINT_UNBOUND",
            )
            expected[probe["Id"]] = {
                "Name": name("containers", probe),
                "EndpointID": endpoint["EndpointID"],
                "MacAddress": "",
                "IPv4Address": "",
                "IPv6Address": "",
            }
        require(
            (network.get("Containers") or {}) == expected,
            "PROBE_ENDPOINT_MEMBERSHIP_DRIFT",
        )
        network["Containers"] = deepcopy(baseline.get("Containers") or {})
        require(network == baseline, "PROBE_NONE_NETWORK_CONFIGURATION_DRIFT")
        return view

    def observe(self, stage: str, saved: dict[str, Any]) -> None:
        require(
            not (self.root / "first-divergence.json").exists(),
            "FIRST_DIVERGENCE_LATCHED",
        )
        sequence = self.next_stage
        raw_path = self.root / f"{sequence:03d}-{stage}-capture.json"
        try:
            require(
                sha(json.loads((self.root / "plan.json").read_bytes()))
                == self.plan_sha,
                "PLAN_DRIFT",
            )
            require(
                sequence < len(self.plan["stages"])
                and self.plan["stages"][sequence]["name"] == stage,
                "STAGE_ORDER_DRIFT",
            )
            seal(raw_path, saved)
            validate_envelope(saved)
            require(
                saved["daemon_before"] == self.plan["daemon"], "DAEMON_IDENTITY_DRIFT"
            )
            require(
                saved["platform_images"] == self.plan["platform_images"],
                "IMAGE_PLATFORM_DRIFT",
            )
            expected = set(self.plan["stages"][sequence]["present_roles"])
            seen: dict[str, dict[str, Any]] = {}
            nonowned: dict[str, dict[str, Any]] = {kind: {} for kind in KINDS}
            for kind in KINDS:
                for row in saved["inspect"][kind]:
                    rid = identity(kind, row)
                    matches = [
                        key
                        for key, role in self.plan["roles"].items()
                        if role["kind"] == kind
                        and role["name"]
                        == (name(kind, row) if kind != "images" else "")
                    ]
                    if matches:
                        key = matches[0]
                        require(key in expected, "UNEXPECTED_LIFECYCLE", key)
                        validate_role(
                            self.plan["roles"][key], row, self.plan["qualification_id"]
                        )
                        fixed = resource_fixed(kind, row)
                        if key in self.bound:
                            require(
                                self.comparator(self.bound[key], kind, row),
                                "OWNED_RESOURCE_DRIFT",
                                key,
                            )
                        else:
                            require(
                                self.plan["roles"][key]["birth_stage"] == stage,
                                "UNBOUND_RESOURCE_BIRTH",
                                key,
                            )
                            self.bound[key] = fixed
                        seen[key] = row
                    else:
                        require(
                            labels(kind, row).get(QUAL_LABEL)
                            != self.plan["qualification_id"],
                            "UNKNOWN_RESOURCE",
                            kind,
                        )
                        nonowned[kind][rid] = row
            require(set(seen) == expected, "INCOMPLETE_OWNED_INVENTORY")
            nonowned = self.nonowned_view(nonowned, seen)
            for kind in KINDS:
                require(
                    nonowned[kind] == self.plan["nonowned"][kind],
                    "NONOWNED_RESOURCE_DRIFT",
                    kind,
                )
            net_by_id = {row["Id"]: row for row in saved["inspect"]["networks"]}
            volume_by_name = {row["Name"]: row for row in saved["inspect"]["volumes"]}
            for key, row in seen.items():
                role = self.plan["roles"][key]
                if role["kind"] != "containers":
                    continue
                for network, endpoint in row["NetworkSettings"]["Networks"].items():
                    if (
                        role.get("network_none")
                        and network == "none"
                        and not endpoint["NetworkID"]
                    ):
                        continue  # Declared create-before-start network-none probe.
                    require(
                        endpoint["NetworkID"] in net_by_id
                        and net_by_id[endpoint["NetworkID"]]["Name"] == network,
                        "NETWORK_IDENTITY_DRIFT",
                    )
                for m in row["Mounts"]:
                    if m["Type"] == "volume":
                        require(
                            m["Name"] in volume_by_name
                            and volume_by_name[m["Name"]]["Mountpoint"] == m["Source"],
                            "MOUNT_SOURCE_DRIFT",
                        )
                    if m["Type"] == "bind":
                        require(
                            m["Source"] in self.plan["bind_commitments"],
                            "UNBOUND_HOST_SOURCE",
                        )
                        require(
                            saved["mount_contents"][row["Id"] + ":" + m["Destination"]]
                            == self.plan["bind_commitments"][m["Source"]],
                            "BIND_CONTENT_DRIFT",
                        )
            for key, row in seen.items():
                if self.plan["roles"][key]["kind"] == "networks":
                    attached = {
                        c["Id"]
                        for ck, c in seen.items()
                        if self.plan["roles"][ck]["kind"] == "containers"
                        and c["State"]["Running"]
                        and row["Name"] in c["NetworkSettings"]["Networks"]
                    }
                    require(
                        set(row.get("Containers") or {}) == attached,
                        "NETWORK_ENDPOINT_DRIFT",
                    )
            stop_stage = [s["name"] for s in self.plan["stages"]].index(
                "AFTER_OWNED_STOP"
            )
            for key, row in seen.items():
                if self.plan["roles"][key]["kind"] == "containers" and not self.plan[
                    "roles"
                ][key].get("network_none"):
                    require(
                        row["State"]["Running"] == (sequence < stop_stage)
                        and row.get("RestartCount", 0) == 0,
                        "CONTAINER_LIFECYCLE_DRIFT",
                    )
            self.validate_seeds(stage, saved["seed_properties"])
            record = {
                "stage": stage,
                "sequence": sequence,
                "utc": datetime.now(UTC).isoformat(),
                "monotonic_ns": time.monotonic_ns(),
                "capture_sha256": sha(saved),
                "plan_sha256": self.plan_sha,
                "previous_sha256": self.previous,
                "bound_sha256": sha(self.bound),
                "formal_authority": False,
                "counters": ZERO_COUNTS,
            }
            seal(self.root / f"{sequence:03d}-{stage}-accepted.json", record)
            self.previous = sha(record)
            self.next_stage += 1
        except QualificationBlocked as error:
            self.block(error.code, stage, error.detail, saved)
        except Exception as error:
            self.block("INCOMPLETE_CAPTURE", stage, type(error).__name__, saved)

    def validate_seeds(self, stage: str, saved: dict[str, Any]) -> None:
        if "seed_binding_policy" not in self.plan:
            return  # Pure inventory fixtures have no Kafka role.
        from scripts.product.qualification_v040.volumes import (
            KAFKA_PATHS,
            validate_seed_lifecycle,
            validate_seed_pair,
        )

        names = [s["name"] for s in self.plan["stages"]]
        index = names.index(stage)
        if index < names.index("AFTER_COPYUP_MEASUREMENT"):
            require(saved["status"] == "NOT_BOUND", "UNEXPECTED_SEED_BINDING")
            return
        if index > names.index("BEFORE_CLEANUP"):
            require(
                saved["status"] == "NOT_REQUIRED_DURING_CLEANUP", "SEED_LIFECYCLE_DRIFT"
            )
            return
        require(saved["status"] == "MEASURED", "SEED_CAPTURE_MISSING")
        binding = {k: saved[k] for k in ("initial", "uid", "gid")}
        if self.seed_binding is None:
            require(stage == "AFTER_COPYUP_MEASUREMENT", "UNBOUND_SEED_BIRTH")
            self.seed_binding = deepcopy(binding)
            seal(self.root / "seed-binding.json", binding)
        else:
            require(binding == self.seed_binding, "SEED_BINDING_DRIFT")
            require(
                json.loads((self.root / "seed-binding.json").read_bytes()) == binding,
                "SEED_BINDING_DRIFT",
            )
        validate_seed_pair(saved["before"], saved["after"])
        for measurements in (saved["before"], saved["after"]):
            validate_seed_lifecycle(
                binding["initial"],
                measurements,
                binding["uid"],
                binding["gid"],
                self.generated_config,
                index >= names.index("AFTER_SANDBOX_START"),
            )
        if stage == "AFTER_KAFKA_IDENTITY":
            require(self.generated_config is None, "SEED_BINDING_DRIFT")
            self.generated_config = deepcopy(
                next(
                    m["entries"] for m in saved["after"] if m["path"] == KAFKA_PATHS[1]
                )
            )
            seal(self.root / "generated-config-binding.json", self.generated_config)
        elif self.generated_config is not None:
            require(
                json.loads((self.root / "generated-config-binding.json").read_bytes())
                == self.generated_config,
                "SEED_BINDING_DRIFT",
            )

    def protect(self, action: str) -> None:
        self.block("FORMAL_AUTHORITY_FORBIDDEN", action)
