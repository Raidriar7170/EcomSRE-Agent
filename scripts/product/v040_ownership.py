"""Offline ownership provenance and durable, first-divergence stage replay.

No Docker client, network transport, executor, approval, or campaign capability.
Inputs are complete saved observations; passing replay never authorizes startup.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import stat
import time
from typing import Any, NoReturn

from ecomsre.product.remediation.window_requests import create_private_file

BASE = "0045e8288efd847b909c03443c6fd1fc15018a44"
STAGES = (
    "PREFLIGHT",
    "BEFORE_BUILD",
    "AFTER_BUILD",
    "BEFORE_SANDBOX_START",
    "AFTER_SANDBOX_START",
    "AFTER_SANDBOX_READY",
    "BEFORE_PRODUCT_START",
    "AFTER_PRODUCT_START",
    "AFTER_WARMUP",
    "AFTER_HEALTHY_CONTROL",
    "AFTER_BASELINE",
    "BEFORE_OBSERVER",
    "AFTER_OBSERVER",
    "BEFORE_ENABLE",
    "AFTER_ENABLE",
    "AFTER_NETWORK_GATE",
    "AFTER_NO_INCIDENT",
    "BEFORE_FORMAL_FREEZE",
    "BEFORE_FAULT",
    "BEFORE_APPROVAL",
    "BEFORE_AUTHORIZATION",
    "BEFORE_REMEDIATION",
    "BEFORE_CLEANUP",
    "AFTER_PRODUCT_CLEANUP",
    "AFTER_SANDBOX_CLEANUP",
    "POST_CLEANUP_READBACK",
)
KINDS = ("containers", "networks", "volumes", "images")
COUNTERS = {
    key: 0
    for key in (
        "formal_faults",
        "remediation_writes",
        "provider_calls",
        "formal_campaign_executions",
    )
}
KAFKA_PATHS = ("/etc/kafka/secrets", "/mnt/shared/config", "/var/lib/kafka/data")
OWNERS = {
    "ecomsre-live-sandbox-v1": {
        "io.ecomsre.sandbox.id": "e477da43-27e7-4c55-8491-1d45cda03000",
    },
    "ecomsre-product-v040": {
        "io.ecomsre.product.v040.goal": "d8ec6455a6108f40d67eb8441f18e952670b087255c0fb15fc14ccb87e32695a",
        "io.ecomsre.product": "ecomsre-product-mvp-v01",
    },
}


class OwnershipBlocked(ValueError):
    pass


def require_offline_only() -> NoReturn:
    raise OwnershipBlocked(
        "OFFLINE_REPAIR_ONLY / REVIEW_REQUIRED: runtime, fault and remediation "
        "authority are absent; the preserved safety checkpoint is not resumed live"
    )


def sha(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()


def is_sha(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(c in "0123456789abcdef" for c in value)
    )


def tree_commitment(path: Path) -> dict[str, Any]:
    """Read regular files/directories only; bind modes and relative names too.

    Refuse symlinks, sockets, devices and concurrent changes. This is a content
    observation, not a promise that mutable application data stays unchanged.
    """
    if path.is_symlink() or not path.exists():
        raise OwnershipBlocked("unreadable or symlink mount source")
    paths = [path, *sorted(path.rglob("*"))] if path.is_dir() else [path]
    rows = []
    for item in paths:
        before = item.lstat()
        if not (stat.S_ISDIR(before.st_mode) or stat.S_ISREG(before.st_mode)):
            raise OwnershipBlocked("non-regular mount content")
        body = item.read_bytes() if stat.S_ISREG(before.st_mode) else b""
        after = item.lstat()
        fields = (
            "st_mode",
            "st_uid",
            "st_gid",
            "st_size",
            "st_mtime_ns",
            "st_ctime_ns",
            "st_ino",
            "st_dev",
        )
        if any(getattr(before, k) != getattr(after, k) for k in fields):
            raise OwnershipBlocked("mount content changed during read")
        rows.append(
            {
                "path": "." if item == path else item.relative_to(path).as_posix(),
                "kind": "directory" if stat.S_ISDIR(before.st_mode) else "file",
                "mode": stat.S_IMODE(before.st_mode),
                "uid": before.st_uid,
                "gid": before.st_gid,
                "content_sha256": hashlib.sha256(body).hexdigest(),
            }
        )
    after_paths = [path, *sorted(path.rglob("*"))] if path.is_dir() else [path]
    if paths != after_paths:
        raise OwnershipBlocked("mount inventory changed during read")
    return {
        "digest_kind": "FILES_NAMES_MODES_OWNERS_V1",
        "sha256": sha(rows),
        "entries": len(rows),
    }


def difference(expected: object, actual: object, prefix: str = "") -> list[str]:
    if type(expected) is not type(actual):
        return [prefix or "/"]
    if isinstance(expected, dict) and isinstance(actual, dict):
        result = []
        for key in sorted(set(expected) | set(actual)):
            pointer = prefix + "/" + str(key).replace("~", "~0").replace("/", "~1")
            result.extend(
                [pointer]
                if key not in expected or key not in actual
                else difference(expected[key], actual[key], pointer)
            )
        return result
    return [] if expected == actual else [prefix or "/"]


def inventory_from_saved_inspects(observation: dict[str, Any]) -> dict[str, Any]:
    """Project complete raw inspect records without publishing environment secrets.

    Before/after enumeration and daemon identity must match. Failed, partial or
    raced reads fail closed. Nothing is excluded merely for a Compose label.
    """
    if set(observation) != {
        "daemon_before",
        "daemon_after",
        "ids_before",
        "ids_after",
        "inspect",
        "mount_contents",
    }:
        raise OwnershipBlocked("incomplete observation envelope")
    daemon = observation["daemon_before"]
    if (
        not isinstance(daemon, dict)
        or not daemon.get("daemon_id")
        or daemon.get("context") != "desktop-linux"
        or not str(daemon.get("endpoint", "")).startswith("unix:///")
        or daemon != observation["daemon_after"]
    ):
        raise OwnershipBlocked("unbound or changed local daemon")
    result: dict[str, Any] = {"daemon": deepcopy(daemon)}
    for key in ("ids_before", "ids_after", "inspect"):
        if not isinstance(observation[key], dict) or set(observation[key]) != set(
            KINDS
        ):
            raise OwnershipBlocked("unknown or missing resource category")
    for kind in KINDS:
        ids = observation["ids_before"][kind]
        after = observation["ids_after"][kind]
        if len(ids) != len(set(ids)) or sorted(ids) != sorted(after):
            raise OwnershipBlocked("inventory enumeration changed")
        rows = observation["inspect"][kind]
        indexed = {}
        for row in rows:
            identity = row["Name"] if kind == "volumes" else row["Id"]
            if identity in indexed:
                raise OwnershipBlocked("duplicate inspect identity")
            config = row.get("Config", {})
            labels = (
                config.get("Labels")
                if kind in {"containers", "images"}
                else row.get("Labels")
            ) or {}
            if not isinstance(labels, dict):
                raise OwnershipBlocked("unreadable ownership labels")
            value: dict[str, Any] = {
                "identity": identity,
                "labels": labels,
                # Full object commitment keeps fields outside the safe projection
                # significant too. No volatile field is silently filtered out.
                "inspect_sha256": sha(row),
            }
            if kind == "containers":
                value.update(
                    image_digest=row["Image"],
                    mounts=row["Mounts"],
                    networks=row["NetworkSettings"]["Networks"],
                )
                if not isinstance(value["mounts"], list):
                    raise OwnershipBlocked("unreadable mounts")
            elif kind == "images":
                value.update(
                    repo_digests=row["RepoDigests"],
                    rootfs=row["RootFS"],
                    declared_volumes=sorted((config.get("Volumes") or {}).keys()),
                )
            elif kind == "volumes":
                value.update(
                    mountpoint=row["Mountpoint"],
                    driver=row["Driver"],
                    options=row.get("Options"),
                )
            indexed[identity] = value
        if set(indexed) != set(ids):
            raise OwnershipBlocked("partial inspect inventory")
        result[kind] = indexed
    result["mount_contents"] = deepcopy(observation["mount_contents"])
    validate_inventory(result)
    return result


def validate_inventory(value: dict[str, Any]) -> None:
    if not isinstance(value, dict) or set(value) != {
        "daemon",
        *KINDS,
        "mount_contents",
    }:
        raise OwnershipBlocked("inventory fields differ")
    daemon = value["daemon"]
    if (
        not isinstance(daemon, dict)
        or not daemon.get("daemon_id")
        or daemon.get("context") != "desktop-linux"
        or not str(daemon.get("endpoint", "")).startswith("unix:///")
    ):
        raise OwnershipBlocked("local daemon binding missing")
    for kind in KINDS:
        if not isinstance(value[kind], dict):
            raise OwnershipBlocked("inventory is not complete")
        for identity, row in value[kind].items():
            if (
                not isinstance(row, dict)
                or row.get("identity") != identity
                or not isinstance(row.get("labels"), dict)
                or not is_sha(row.get("inspect_sha256"))
            ):
                raise OwnershipBlocked("resource identity or digest missing")
            labels = row["labels"]
            project = labels.get("com.docker.compose.project")
            if project in OWNERS and any(
                labels.get(k) != v for k, v in OWNERS[project].items()
            ):
                raise OwnershipBlocked("same-project resource ownership unknown")
    if not isinstance(value["mount_contents"], dict):
        raise OwnershipBlocked("mount content evidence missing")
    used = set()
    for row in value["containers"].values():
        if row.get("image_digest") not in value["images"]:
            raise OwnershipBlocked("unbound container image")
        if not isinstance(row.get("networks"), dict) or not isinstance(
            row.get("mounts"), list
        ):
            raise OwnershipBlocked("unreadable container networks or mounts")
        for connection in row["networks"].values():
            if (
                not isinstance(connection, dict)
                or connection.get("NetworkID") not in value["networks"]
            ):
                raise OwnershipBlocked("unknown attached network")
            project = row["labels"].get("com.docker.compose.project")
            network = value["networks"][connection["NetworkID"]]
            if (
                project in OWNERS
                and network["labels"].get("com.docker.compose.project") != project
            ):
                raise OwnershipBlocked("owned container uses non-owned network")
        destinations = set()
        for mount in row["mounts"]:
            if not isinstance(mount, dict) or not isinstance(
                mount.get("Destination"), str
            ):
                raise OwnershipBlocked("unreadable mount")
            if mount["Destination"] in destinations:
                raise OwnershipBlocked("overlapping mount destination")
            destinations.add(mount["Destination"])
            if mount.get("Type") not in {"bind", "volume", "tmpfs"}:
                raise OwnershipBlocked("unknown mount type")
            if mount["Type"] == "volume" and mount.get("Name") not in value["volumes"]:
                raise OwnershipBlocked("unknown mounted volume")
            if mount["Type"] == "volume":
                volume = value["volumes"][mount["Name"]]
                if (
                    not volume.get("mountpoint")
                    or mount.get("Source") != volume["mountpoint"]
                ):
                    raise OwnershipBlocked(
                        "volume source differs from inspected identity"
                    )
                project = row["labels"].get("com.docker.compose.project")
                if (
                    project in OWNERS
                    and volume["labels"].get("com.docker.compose.project") != project
                ):
                    raise OwnershipBlocked("owned container uses non-owned volume")
            key = row["identity"] + ":" + mount["Destination"]
            used.add(key)
            binding = value["mount_contents"].get(key)
            if (
                not isinstance(binding, dict)
                or not is_sha(binding.get("sha256"))
                or not binding.get("digest_kind")
            ):
                raise OwnershipBlocked("unbound mount content")
    if set(value["mount_contents"]) != used:
        raise OwnershipBlocked("orphan mount content evidence")


def validate_stage_plan(expected: dict[str, dict[str, Any]]) -> None:
    """Do not let a future expected stage silently adopt new foreign resources."""
    previous = expected[STAGES[0]]
    for stage in STAGES[1:]:
        current = expected[stage]
        if previous["daemon"] != current["daemon"]:
            raise OwnershipBlocked("stage plan changes daemon identity")
        for kind in KINDS:
            for identity in set(previous[kind]) | set(current[kind]):
                before, after = (
                    previous[kind].get(identity),
                    current[kind].get(identity),
                )
                if before == after:
                    continue
                for row in (before, after):
                    if (
                        row is not None
                        and row["labels"].get("com.docker.compose.project")
                        not in OWNERS
                    ):
                        raise OwnershipBlocked(
                            "stage plan changes a non-owned resource"
                        )
                if before and after:
                    for key in (
                        "labels",
                        "image_digest",
                        "mounts",
                        "rootfs",
                        "repo_digests",
                        "declared_volumes",
                    ):
                        if before.get(key) != after.get(key):
                            raise OwnershipBlocked(
                                "stage plan changes a bound resource"
                            )
                elif after and stage not in {
                    "AFTER_BUILD",
                    "AFTER_SANDBOX_START",
                    "AFTER_PRODUCT_START",
                    "AFTER_ENABLE",
                }:
                    raise OwnershipBlocked("resource creation outside declared stage")
                elif before and stage not in {
                    "AFTER_PRODUCT_CLEANUP",
                    "AFTER_SANDBOX_CLEANUP",
                }:
                    raise OwnershipBlocked("resource removal outside cleanup stage")
        previous = current


def validate_mount_provenance(manifest: dict[str, Any]) -> None:
    """A complete review manifest commits both resolved plans and every mount.

    Mutable content binds its declared seed/lifecycle, not a fictitious eternal
    hash. The lack of a measured copy-up is explicit and grants no authority.
    """
    if (
        manifest.get("authority") != "OFFLINE_ONLY"
        or manifest.get("counts") != COUNTERS
    ):
        raise OwnershipBlocked("offline authority differs")
    seen = set()
    kafka = {}
    expected = set(manifest["mount_inventory"])
    derived = {
        name + ":" + target
        for name, service in manifest["services"].items()
        for target in service["mount_targets"]
    }
    if expected != derived or len(expected) != len(manifest["mount_inventory"]):
        raise OwnershipBlocked("service mount inventory differs")
    for mount in manifest["mounts"]:
        key = mount["service"] + ":" + mount["target"]
        if key in seen or key not in expected:
            raise OwnershipBlocked("duplicate or undeclared mount")
        seen.add(key)
        required = {
            "service",
            "target",
            "type",
            "source",
            "read_only",
            "options",
            "owner_labels",
            "content",
            "cleanup",
            "lifecycle",
            "image_digest",
        }
        if set(mount) != required or not all(
            mount[x]
            for x in ("source", "owner_labels", "cleanup", "lifecycle", "image_digest")
        ):
            raise OwnershipBlocked("incomplete mount provenance")
        if mount["type"] not in {"bind", "volume", "tmpfs"} or not is_sha(
            mount["content"].get("sha256")
        ):
            raise OwnershipBlocked("unknown mount or content digest")
        project = mount["service"].split("/")[0]
        project = (
            "ecomsre-live-sandbox-v1"
            if project == "sandbox"
            else "ecomsre-product-v040"
        )
        if any(
            mount["owner_labels"].get(k) != v
            for k, v in {
                **OWNERS[project],
                "com.docker.compose.project": project,
            }.items()
        ):
            raise OwnershipBlocked("mount ownership labels differ")
        if mount["image_digest"] not in manifest["image_digests"]:
            raise OwnershipBlocked("mount image is unbound")
        if (
            manifest["services"][mount["service"]]["image_digest"]
            != mount["image_digest"]
        ):
            raise OwnershipBlocked("mount image differs from service")
        content = mount["content"]
        if content.get("digest_kind") not in {
            "FILES_NAMES_MODES_OWNERS_V1",
            "IMAGE_PATH_SEED_COMMITMENT_V1",
            "IMAGE_SEED_COMMITMENT_V1",
            "EMPTY_TMPFS_COMMITMENT_V1",
            "DAEMON_SOCKET_BINDING_V1",
        }:
            raise OwnershipBlocked("unknown content commitment")
        if "commitment" in content and sha(content["commitment"]) != content["sha256"]:
            raise OwnershipBlocked("content commitment digest differs")
        if mount["type"] == "volume":
            definition = manifest["volumes"].get(mount["source"])
            if (
                not definition
                or definition["labels"] != mount["owner_labels"]
                or not definition["name"]
            ):
                raise OwnershipBlocked("unbound named volume")
        if mount["service"] == "sandbox/kafka" and mount["target"] in KAFKA_PATHS:
            kafka[mount["target"]] = mount
    if seen != expected or set(kafka) != set(KAFKA_PATHS):
        raise OwnershipBlocked("incomplete mount coverage")
    for mount in kafka.values():
        if (
            mount["type"] != "volume"
            or mount["read_only"]
            or mount["options"] != {"volume": {"nocopy": False}}
            or mount["content"].get("digest_kind") != "IMAGE_PATH_SEED_COMMITMENT_V1"
        ):
            raise OwnershipBlocked("Kafka copy-up overlay differs")
        seed = mount["content"]["commitment"]
        if (
            sha(seed) != mount["content"]["sha256"]
            or seed["path"] != mount["target"]
            or seed["platform_digest"] != mount["image_digest"]
            or not seed["rootfs_diff_ids"]
        ):
            raise OwnershipBlocked("Kafka image path seed differs")


class StageJournal:
    """Single-writer offline replay, exact immutable expected stage observations.

    A stage plan cannot be refreshed from actual input. Both expected and actual
    are retained at first divergence. Reopening validates the full hash chain;
    no later healthy sample, stage skip or restart clears the failure latch.
    """

    def __init__(self, root: Path, expected: dict[str, dict[str, Any]]) -> None:
        if set(expected) != set(STAGES):
            raise OwnershipBlocked("stage plan incomplete")
        for value in expected.values():
            validate_inventory(value)
        validate_stage_plan(expected)
        self.expected = deepcopy(expected)
        self.root = root
        if root.is_symlink():
            raise OwnershipBlocked("journal is a symlink")
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        if stat.S_IMODE(root.stat().st_mode) != 0o700:
            raise OwnershipBlocked("journal directory must be private")
        self.plan_sha = sha(expected)
        self.plan = {
            "schema_version": "ecomsre.v040.ownership-stage-plan.v1",
            "plan_sha256": self.plan_sha,
            "stages": expected,
            "authority": "OFFLINE_ONLY",
            "counts": COUNTERS,
        }
        path = root / "plan.json"
        if path.exists():
            if self._read(path) != self.plan:
                raise OwnershipBlocked("stage plan changed")
        else:
            if list(root.iterdir()):
                raise OwnershipBlocked("orphan journal records")
            self._write(path, self.plan)
        self._history()

    @staticmethod
    def _read(path: Path) -> Any:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600:
            raise OwnershipBlocked("journal file is not private and regular")
        return json.loads(path.read_bytes())

    @staticmethod
    def _write(path: Path, value: object) -> None:
        create_private_file(
            path, (json.dumps(value, sort_keys=True, indent=2) + "\n").encode()
        )

    def _history(self) -> tuple[int, str]:
        if (self.root / "first-divergence.json").exists():
            raise OwnershipBlocked("first divergence is latched; no continuation")
        if self._read(self.root / "plan.json") != self.plan:
            raise OwnershipBlocked("stage plan changed on disk")
        files = sorted(self.root.glob("stage-*.json"))
        if set(p.name for p in self.root.iterdir()) != {
            "plan.json",
            *(p.name for p in files),
        }:
            raise OwnershipBlocked("unknown journal artifact")
        previous = self.plan_sha
        for i, path in enumerate(files):
            record = self._read(path)
            record_sha = record.pop("record_sha256")
            if (
                i >= len(STAGES)
                or path.name != f"stage-{i:02d}.json"
                or record["stage"] != STAGES[i]
                or record["previous_sha256"] != previous
                or record["plan_sha256"] != self.plan_sha
                or sha(record) != record_sha
                or record["snapshot"] != self.expected[STAGES[i]]
            ):
                raise OwnershipBlocked("journal history corrupt or not admitted")
            previous = record_sha
        return len(files), previous

    def observe(self, stage: str, actual: dict[str, Any]) -> dict[str, Any]:
        # An exclusive file lock prevents competing writers from losing the first
        # observed divergence. Lock the already-persisted plan, not a new artifact.
        import fcntl

        fd = os.open(self.root / "plan.json", os.O_RDONLY | os.O_NOFOLLOW)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            ordinal, previous = self._history()
            expected_stage = STAGES[ordinal] if ordinal < len(STAGES) else None
            expected = self.expected.get(stage)
            reason = None
            try:
                validate_inventory(actual)
            except (ValueError, KeyError, TypeError, AttributeError) as error:
                reason = type(error).__name__ + ": incomplete inventory"
            paths = difference(expected, actual)
            if stage != expected_stage or reason or paths:
                record = {
                    "stage": stage,
                    "expected_stage": expected_stage,
                    "previous_sha256": previous,
                    "plan_sha256": self.plan_sha,
                    "observed_at": datetime.now(UTC).isoformat(),
                    "monotonic_ns": time.monotonic_ns(),
                    "reason": reason
                    or (
                        "STAGE_ORDER"
                        if stage != expected_stage
                        else "INVENTORY_DIVERGENCE"
                    ),
                    "difference_paths": paths,
                    "expected": expected,
                    "actual": actual,
                    "counts": COUNTERS,
                    "authority": "NONE",
                }
                record["record_sha256"] = sha(record)
                self._write(self.root / "first-divergence.json", record)
                raise OwnershipBlocked("first divergence recorded; execution denied")
            record = {
                "stage": stage,
                "previous_sha256": previous,
                "plan_sha256": self.plan_sha,
                "snapshot": actual,
                "observed_at": datetime.now(UTC).isoformat(),
                "monotonic_ns": time.monotonic_ns(),
            }
            record["record_sha256"] = sha(record)
            self._write(self.root / f"stage-{ordinal:02d}.json", record)
            return record
        finally:
            os.close(fd)

    def protected_boundary(self, stage: str, actual: dict[str, Any]) -> NoReturn:
        self.observe(stage, actual)
        require_offline_only()

    def observe_saved(self, stage: str, observation: dict[str, Any]) -> dict[str, Any]:
        """Collection/shape failures are durable failures too, without raw secrets."""
        try:
            actual = inventory_from_saved_inspects(observation)
        except (ValueError, KeyError, TypeError, AttributeError) as error:
            actual = {
                "collection_error_type": type(error).__name__,
                "saved_input_sha256": sha(observation),
            }
        return self.observe(stage, actual)
