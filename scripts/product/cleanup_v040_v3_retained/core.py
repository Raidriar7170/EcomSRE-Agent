"""Frozen authority, deterministic identities and append-only cleanup engine."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import time
from typing import Any, Protocol

BASE = "ea466d7ca14ac4a0a0c1f7680507747dbe67b06e"
GOAL_SHA = "a2f8c3d20b7cd589c0494a161c3451702f3392cfb8857df642683af972d997e0"
BLOB = "8ad3ee775cb13b933312c9909e1d20cfc5b7667d"
RAW_SHA = "263b43e61d6e6e736065701cf7b23e367da19cbd126811f831d990bb2fe4a168"
QUAL = "c6a70e54d58b4df5a1325886304b4844"
LABEL = "io.ecomsre.product.v040.qualification"
PROBE = "c598cc5091013626cafa93bcf08260ff0c727b2453885c086eae67621499dd81"
KINDS = ("containers", "networks", "volumes")
GOAL_PATH = "docs/goals/EcomSRE_Product_v0.4_V3_Retained_Cleanup_Only_Goal.md"
RETAINED_PATH = "docs/results/product-v040-qualification-v3/retained-resources.json"


class Blocked(RuntimeError):
    pass


def require(ok: bool, code: str) -> None:
    if not ok:
        raise Blocked(code)


def encoded(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def digest(value: Any) -> str:
    return hashlib.sha256(encoded(value)).hexdigest()


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def stamp() -> dict[str, Any]:
    return {
        "utc": datetime.now(timezone.utc).isoformat(),
        "monotonic_ns": time.monotonic_ns(),
    }


def seal(root: Path, name: str, value: Any) -> str:
    path = root / name
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    data = encoded(value) + b"\n"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as out:
        out.write(data)
        out.flush()
        os.fsync(out.fileno())
    return sha(data)


def ident(kind: str, row: dict[str, Any]) -> str:
    return str(row["Name" if kind == "volumes" else "Id"])


def labels(kind: str, row: dict[str, Any]) -> dict[str, Any]:
    return dict(
        (row["Config"].get("Labels") if kind == "containers" else row.get("Labels"))
        or {}
    )


def sorted_mounts(row: dict[str, Any]) -> list[Any]:
    return sorted(row["Mounts"], key=lambda x: encoded(x))


def fixed(kind: str, row: dict[str, Any]) -> dict[str, Any]:
    """Configuration projection; network binding remains after a normal stop.

    Engine-owned endpoint address/allocation fields are recorded raw, not ownership
    configuration. Config, HostConfig, mounts and network IDs remain exact.
    """
    if kind == "containers":
        keys = (
            "Id",
            "Name",
            "Created",
            "Image",
            "ImageManifestDescriptor",
            "Config",
            "HostConfig",
            "Platform",
            "Driver",
            "AppArmorProfile",
            "MountLabel",
            "ProcessLabel",
            "Path",
            "Args",
        )
        result = {k: row.get(k) for k in keys}
        result["Mounts"] = sorted_mounts(row)
        result["network_bindings"] = {
            name: {
                k: ep.get(k)
                for k in (
                    "NetworkID",
                    "Aliases",
                    "Links",
                    "DriverOpts",
                    "IPAMConfig",
                    "GwPriority",
                )
            }
            for name, ep in row["NetworkSettings"]["Networks"].items()
        }
        return result
    if kind == "networks":
        return {k: v for k, v in row.items() if k not in ("Containers", "Status")}
    return deepcopy(row)


def command_values(declared: dict[str, Any], image: dict[str, Any]) -> dict[str, Any]:
    ep, cmd = declared.get("entrypoint"), declared.get("command")
    ie, ic = image["Config"].get("Entrypoint"), image["Config"].get("Cmd")
    for value in (ep, cmd, ie, ic):
        require(
            value is None
            or isinstance(value, list)
            and all(isinstance(x, str) for x in value),
            "UNSUPPORTED_COMMAND_REPRESENTATION",
        )
    return dict(
        compose_declared_entrypoint=ep,
        compose_declared_command=cmd,
        image_default_entrypoint=ie,
        image_default_command=ic,
        resolved_effective_entrypoint=ie if ep is None else ep,
        resolved_effective_command=ic if cmd is None else cmd,
    )


class Authority:
    def __init__(
        self,
        retained: dict[str, Any],
        raw: dict[str, Any],
        compose: dict[str, Any],
        plan: dict[str, Any],
    ):
        require(retained["qualification_id"] == QUAL, "ALLOWLIST_QUALIFICATION")
        self.records = {
            k: {ident(k, r): r for r in retained["resources"][k]} for k in KINDS
        }
        self.old = {
            k: {
                ident(k, r): r
                for r in raw["inspect"][k]
                if ident(k, r) in self.records[k]
            }
            for k in KINDS
        }
        for kind, count in zip(KINDS, (29, 3, 6)):
            rows = retained["resources"][kind]
            require(
                len(rows) == len(self.records[kind]) == len(self.old[kind]) == count,
                "ALLOWLIST_CARDINALITY",
            )
            require(len({r["Name"] for r in rows}) == count, "ALLOWLIST_DUPLICATE_NAME")
            for rid, r in self.records[kind].items():
                require(
                    bool(re.fullmatch(r"[0-9a-f]{64}", rid))
                    if kind != "volumes"
                    else bool(re.fullmatch(r"ecomsre-v040-" + QUAL + r"-[a-z-]+", rid)),
                    "INVALID_RETAINED_ID",
                )
                require(
                    digest(self.old[kind][rid]) == r["raw_inspect_sha256"],
                    "RAW_RECORD_BINDING",
                )
                require(
                    labels(kind, self.old[kind][rid]).get(LABEL) == QUAL,
                    "OWNERSHIP_MISMATCH",
                )
        self.all_network_ids = [r["Id"] for r in raw["inspect"]["networks"]]
        self.daemon = raw["daemon_before"]
        require(self.daemon == raw["daemon_after"], "HISTORICAL_DAEMON_DRIFT")
        self.images = raw["platform_images"]
        self.commands = {}
        roles = {}
        for rid, r in self.old["containers"].items():
            role = labels("containers", r)["com.docker.compose.service"]
            if rid == PROBE:
                declared = plan["roles"]["probe/container/kafka-volume-probe"]
                # Frozen plan stores normalized entrypoint/command alongside other fields.
                declared = {
                    "entrypoint": declared["entrypoint"],
                    "command": declared["command"],
                }
            else:
                declared = compose["services"][role]
                roles[role] = rid
            image = self.images[r["Config"]["Image"]]
            row = command_values(declared, image)
            row["declaration_source"] = (
                "frozen_probe_plan" if rid == PROBE else "frozen_compose"
            )
            row["pinned_image_config_sha256"] = digest(image)
            row["current_actual_entrypoint"] = None
            row["current_actual_command"] = None
            self.commands[rid] = row
            self.check_command(rid, r)
        # Reverse dependency Kahn order; lexicographic tie-break at each frontier.
        graph = {
            name: set(compose["services"][name].get("depends_on") or {})
            for name in roles
        }
        require(
            all(v <= set(graph) for v in graph.values()), "DEPENDENCY_OUTSIDE_ALLOWLIST"
        )
        self.order = []
        while graph:
            frontier = sorted(
                name
                for name in graph
                if not any(name in deps for deps in graph.values())
            )
            require(bool(frontier), "DEPENDENCY_CYCLE")
            name = frontier[0]
            self.order.append(roles[name])
            del graph[name]
        self.network_order = sorted(
            self.records["networks"],
            key=lambda rid: {
                "ecomsre-product-v040_remediation-observation": 0,
                "ecomsre-product-v040-default": 1,
                "ecomsre-live-sandbox-v1-default": 2,
            }[self.records["networks"][rid]["Name"]],
        )
        self.volume_order = [
            "ecomsre-v040-" + QUAL + "-" + x
            for x in (
                "astronomy-db-data",
                "jaeger-data",
                "prometheus-data",
                "qualification-kafka-config",
                "qualification-kafka-data",
                "qualification-kafka-secrets",
            )
        ]
        require(set(self.volume_order) == set(self.records["volumes"]), "VOLUME_SET")

    def check_command(self, rid: str, row: dict[str, Any]) -> None:
        expected = self.commands[rid]
        require(
            row["Config"].get("Entrypoint") == expected["resolved_effective_entrypoint"]
            and row["Config"].get("Cmd") == expected["resolved_effective_command"],
            "CLEANUP_EFFECTIVE_COMMAND_MISMATCH",
        )

    def validate(self, view: dict[str, Any]) -> dict[str, dict[str, str]]:
        disposition: dict[str, dict[str, str]] = {k: {} for k in KINDS}
        for kind in KINDS:
            current = {ident(kind, r): r for r in view["inspect"][kind]}
            for rid, r in current.items():
                require(
                    labels(kind, r).get(LABEL) != QUAL or rid in self.records[kind],
                    "UNKNOWN_QUALIFICATION_RESOURCE",
                )
            for rid, old in self.old[kind].items():
                if rid not in current:
                    require(
                        not any(r["Name"] == old["Name"] for r in current.values()),
                        "RESOURCE_REPLACED",
                    )
                    disposition[kind][rid] = "ALREADY_ABSENT"
                    continue
                row = current[rid]
                if kind == "containers":
                    self.check_command(rid, row)
                    old_eps = old["NetworkSettings"]["Networks"]
                    current_eps = row["NetworkSettings"]["Networks"]
                    require(
                        set(current_eps) == set(old_eps),
                        "RESOURCE_NETWORK_BINDING_DRIFT",
                    )
                    for name, endpoint in current_eps.items():
                        expected = old_eps[name]
                        if row["State"]["Running"]:
                            require(
                                endpoint == expected, "RESOURCE_ENDPOINT_IDENTITY_DRIFT"
                            )
                        else:
                            # Only Engine-allocated address fields may clear on stop.
                            clearable = {
                                "EndpointID",
                                "Gateway",
                                "GlobalIPv6Address",
                                "GlobalIPv6PrefixLen",
                                "IPAddress",
                                "IPPrefixLen",
                                "IPv6Gateway",
                                "MacAddress",
                            }
                            require(
                                set(endpoint) == set(expected),
                                "RESOURCE_ENDPOINT_IDENTITY_DRIFT",
                            )
                            require(
                                all(
                                    value == expected[key]
                                    or key in clearable
                                    and value in ("", 0)
                                    for key, value in endpoint.items()
                                ),
                                "RESOURCE_ENDPOINT_IDENTITY_DRIFT",
                            )
                require(
                    fixed(kind, row) == fixed(kind, old),
                    "RESOURCE_IDENTITY_DRIFT:" + kind + ":" + rid,
                )
                if kind == "networks":
                    require(
                        set(row["Containers"]) <= set(self.records["containers"]),
                        "UNEXPECTED_NETWORK_ENDPOINT",
                    )
                    require(
                        all(
                            cid in old["Containers"] and ep == old["Containers"][cid]
                            for cid, ep in row["Containers"].items()
                        ),
                        "RESOURCE_ENDPOINT_IDENTITY_DRIFT",
                    )
                    running_attached = {
                        c["Id"]
                        for c in view["inspect"]["containers"]
                        if c["State"]["Running"]
                        and any(
                            ep["NetworkID"] == rid
                            for ep in c["NetworkSettings"]["Networks"].values()
                        )
                    }
                    require(
                        running_attached <= set(row["Containers"]),
                        "UNEXPECTED_NETWORK_ENDPOINT",
                    )
                disposition[kind][rid] = "PRESENT_MATCHING"
        for container in view["inspect"]["containers"]:
            cid = container["Id"]
            for ep in container["NetworkSettings"]["Networks"].values():
                require(
                    ep["NetworkID"] not in self.records["networks"]
                    or cid in self.records["containers"],
                    "UNEXPECTED_NETWORK_ENDPOINT",
                )
            for mount in container["Mounts"]:
                for name, vol in self.old["volumes"].items():
                    source, mountpoint = mount.get("Source", ""), vol["Mountpoint"]
                    overlap = (
                        source == mountpoint
                        or source.startswith(mountpoint + "/")
                        or (
                            source.startswith("/")
                            and mountpoint.startswith(source.rstrip("/") + "/")
                        )
                    )
                    require(
                        not (mount.get("Name") == name or overlap)
                        or cid in self.records["containers"],
                        "UNEXPECTED_ATTACHMENT",
                    )
        return disposition

    def argv(self, kind: str, rid: str, operation: str) -> list[str]:
        require(kind in KINDS and rid in self.records[kind], "TARGET_OUTSIDE_ALLOWLIST")
        if kind == "containers" and operation == "stop":
            return ["docker", "container", "stop", "--time", "30", rid]
        require(operation == "rm", "FORBIDDEN_COMMAND")
        return ["docker", kind[:-1], "rm", rid]


def validate_pair(a: dict[str, Any], b: dict[str, Any]) -> None:
    for view in (a, b):
        require(set(view["inspect"]) == set(KINDS), "RACED_OR_INCOMPLETE_CAPTURE")
        for kind in KINDS:
            rows = view["inspect"][kind]
            ids = [ident(kind, r) for r in rows]
            require(
                len(ids) == len(set(ids)) and sorted(ids) == sorted(view["ids"][kind]),
                "RACED_OR_INCOMPLETE_CAPTURE",
            )
    require(a["binding"] == b["binding"], "DAEMON_IDENTITY_DRIFT")
    for kind in KINDS:
        require(
            sorted(a["ids"][kind]) == sorted(b["ids"][kind]),
            "RACED_OR_INCOMPLETE_CAPTURE",
        )
        require(
            {ident(kind, r): fixed(kind, r) for r in a["inspect"][kind]}
            == {ident(kind, r): fixed(kind, r) for r in b["inspect"][kind]},
            "RACED_OR_INCOMPLETE_CAPTURE",
        )
        if kind == "containers":
            require(
                {r["Id"]: r["NetworkSettings"]["Networks"] for r in a["inspect"][kind]}
                == {
                    r["Id"]: r["NetworkSettings"]["Networks"]
                    for r in b["inspect"][kind]
                },
                "RACED_OR_INCOMPLETE_CAPTURE",
            )
        if kind == "networks":
            require(
                {r["Id"]: r.get("Status") for r in a["inspect"][kind]}
                == {r["Id"]: r.get("Status") for r in b["inspect"][kind]},
                "RACED_OR_INCOMPLETE_CAPTURE",
            )
            require(
                {r["Id"]: r["Containers"] for r in a["inspect"][kind]}
                == {r["Id"]: r["Containers"] for r in b["inspect"][kind]},
                "RACED_OR_INCOMPLETE_CAPTURE",
            )


def nonowned(
    view: dict[str, Any], authority: Authority, remove_probe: bool
) -> dict[str, Any]:
    result: dict[str, Any] = {k: {} for k in KINDS}
    for kind in KINDS:
        for row in view["inspect"][kind]:
            rid = ident(kind, row)
            if rid in authority.records[kind]:
                continue
            value = fixed(kind, row)
            if kind == "containers":
                value = {
                    k: deepcopy(v)
                    for k, v in row.items()
                    if k not in ("State", "RestartCount")
                }
                value["Mounts"] = sorted_mounts(row)
            if kind == "networks":
                value = deepcopy(row)
                if remove_probe and row["Name"] == "none":
                    value["Containers"].pop(PROBE, None)
            result[kind][rid] = value
    return result


class Transport(Protocol):
    def capture(self) -> dict[str, Any]: ...
    def mutate(self, argv: list[str]) -> dict[str, Any]: ...


class Engine:
    def __init__(self, authority: Authority, transport: Transport, root: Path):
        self.a, self.t, self.root = authority, transport, root
        self.binding: Any = None
        self.baseline: Any = None
        self.previous: str | None = None
        self.intents: set[tuple[str, str, str]] = set()
        self.removed: dict[str, list[str]] = {k: [] for k in KINDS}
        self.initial: dict[str, dict[str, str]] = {}
        self.counts = {"stop": 0, "container_rm": 0, "network_rm": 0, "volume_rm": 0}
        self.probe_removed = False

    def pair(self, prefix: str) -> dict[str, Any]:
        a = self.t.capture()
        seal(self.root, prefix + "-1.json", a)
        b = self.t.capture()
        seal(self.root, prefix + "-2.json", b)
        validate_pair(a, b)
        if self.binding is not None:
            require(b["binding"] == self.binding, "DAEMON_IDENTITY_DRIFT")
        self.a.validate(b)
        if self.baseline is not None:
            require(
                nonowned(b, self.a, False)
                == nonowned(self.baseline, self.a, self.probe_removed),
                "NONOWNED_DRIFT",
            )
        return b

    def action(self, kind: str, rid: str, op: str) -> None:
        seq = len(self.intents)
        view = self.pair(f"observations/{seq:03d}-{op}-{rid}-pre")
        row = next((r for r in view["inspect"][kind] if ident(kind, r) == rid), None)
        if row is None:
            seal(
                self.root,
                f"dispositions/{op}-{rid}.json",
                {"status": "ALREADY_ABSENT_DURING_CLEANUP", "attribution": "UNKNOWN"},
            )
            return
        if kind == "containers":
            running = row["State"]["Running"]
            require(
                not row["State"].get("Paused") and not row["State"].get("Restarting"),
                "UNSTABLE_CONTAINER_STATE",
            )
            if op == "stop" and not running:
                seal(
                    self.root,
                    f"dispositions/stop-{rid}.json",
                    {"status": "STOP_NOT_REQUIRED"},
                )
                return
            require(op != "rm" or running is False, "REMOVE_REQUIRES_STOPPED")
        elif kind == "networks":
            require(
                not row["Containers"]
                and not any(
                    ep["NetworkID"] == rid
                    for c in view["inspect"]["containers"]
                    for ep in c["NetworkSettings"]["Networks"].values()
                ),
                "UNEXPECTED_NETWORK_ENDPOINT",
            )
        else:
            require(
                not any(
                    m.get("Name") == rid
                    for c in view["inspect"]["containers"]
                    for m in c["Mounts"]
                ),
                "UNEXPECTED_ATTACHMENT",
            )
        key = (kind, rid, op)
        require(key not in self.intents, "DUPLICATE_INTENT")
        argv = self.a.argv(kind, rid, op)
        intent = {
            "resource_kind": kind,
            "retained_identity": rid,
            "retained_record_digest": digest(self.a.records[kind][rid]),
            "current_pre_mutation_digest": digest(row),
            "command": argv,
            "expected_postcondition": "STOPPED" if op == "stop" else "ABSENT",
            "binding": self.binding,
            **stamp(),
        }
        intent_sha = seal(self.root, f"mutation-intents/{seq:03d}.json", intent)
        self.intents.add(key)
        self.counts["stop" if op == "stop" else kind[:-1] + "_rm"] += 1
        started = stamp()
        try:
            response = self.t.mutate(argv)
        except Exception as error:
            response = {"exit_status": None, "exception": type(error).__name__}
        post: Any = None
        outcome = "MUTATION_OUTCOME_UNKNOWN"
        try:
            post = self.t.capture()
            seal(self.root, f"observations/{seq:03d}-post.json", post)
            require(post["binding"] == self.binding, "DAEMON_IDENTITY_DRIFT")
            validate_pair(post, post)
            self.a.validate(post)
            actual = next(
                (r for r in post["inspect"][kind] if ident(kind, r) == rid), None
            )
            if op == "stop":
                require(
                    actual is not None and actual["State"]["Running"] is False,
                    "STOP_FAILED",
                )
            else:
                require(
                    actual is None
                    and not any(
                        r["Name"] == row["Name"] for r in post["inspect"][kind]
                    ),
                    "REMOVE_FAILED",
                )
            probe_removed = self.probe_removed or (rid == PROBE and op == "rm")
            require(
                nonowned(post, self.a, False)
                == nonowned(self.baseline, self.a, probe_removed),
                "NONOWNED_DRIFT",
            )
            require(response["exit_status"] == 0, "CLIENT_ERROR_REQUIRES_ADJUDICATION")
            self.probe_removed = probe_removed
            outcome = "VERIFIED"
        except Exception as error:
            outcome = str(error) if isinstance(error, Blocked) else type(error).__name__
        receipt = {
            **intent,
            "intent_sha256": intent_sha,
            "started_at": started,
            "ended_at": stamp(),
            "client_exit_status": response.get("exit_status"),
            "bounded_output_digest": digest(response),
            "response": response,
            "postcondition": outcome,
            "post_mutation_observation_digest": digest(post),
            "outcome": outcome,
            "previous_receipt_sha256": self.previous,
        }
        receipt["receipt_sha256"] = digest(receipt)
        self.previous = seal(self.root, f"mutation-receipts/{seq:03d}.json", receipt)
        require(outcome == "VERIFIED", outcome)
        if op == "rm":
            self.removed[kind].append(rid)

    def run(self) -> dict[str, Any]:
        self.baseline = self.pair("pre-cleanup-inventory")
        self.binding = self.baseline["binding"]
        self.initial = self.a.validate(self.baseline)
        seal(
            self.root,
            "pre-cleanup-nonowned-baseline.json",
            nonowned(self.baseline, self.a, False),
        )
        adjudication = deepcopy(self.a.commands)
        for row in self.baseline["inspect"]["containers"]:
            if row["Id"] in adjudication:
                adjudication[row["Id"]]["current_actual_entrypoint"] = row[
                    "Config"
                ].get("Entrypoint")
                adjudication[row["Id"]]["current_actual_command"] = row["Config"].get(
                    "Cmd"
                )
        seal(self.root, "effective-command-adjudication.json", adjudication)
        if self.initial["containers"][PROBE] == "PRESENT_MATCHING":
            probe = next(
                r for r in self.baseline["inspect"]["containers"] if r["Id"] == PROBE
            )
            if probe["State"]["Running"]:
                self.action("containers", PROBE, "stop")
            self.action("containers", PROBE, "rm")
        for rid in self.a.order:
            if self.initial["containers"][rid] == "PRESENT_MATCHING":
                self.action("containers", rid, "stop")
        all_stopped = self.pair("all-sandbox-stopped")
        require(
            not any(
                r["State"]["Running"]
                for r in all_stopped["inspect"]["containers"]
                if r["Id"] in self.a.records["containers"]
            ),
            "STOP_FAILED",
        )
        for rid in self.a.order:
            if self.initial["containers"][rid] == "PRESENT_MATCHING":
                self.action("containers", rid, "rm")
        for kind, order in [
            ("networks", self.a.network_order),
            ("volumes", self.a.volume_order),
        ]:
            for rid in order:
                if self.initial[kind][rid] == "PRESENT_MATCHING":
                    self.action(kind, rid, "rm")
        final = self.pair("post-cleanup-inventory")
        disposition = self.a.validate(final)
        require(
            all(
                v == "ALREADY_ABSENT"
                for rows in disposition.values()
                for v in rows.values()
            ),
            "RETAINED_RESOURCES_REMAIN",
        )
        return {
            "terminal": "CLEANUP_OBSERVED_COMPLETE_REVIEW_PENDING",
            "runtime_checks": "PASS",
            "removed": self.removed,
            "initial_disposition": self.initial,
            "final_disposition": disposition,
            "counts": self.counts,
            "last_receipt": self.previous,
            "nonowned_comparison": "PASS",
            "review_required_before_public_success": True,
        }
