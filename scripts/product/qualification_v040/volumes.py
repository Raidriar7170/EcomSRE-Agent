"""Bounded owned Kafka copy-up measurement and exact-user sentinel protocol."""

from __future__ import annotations

import hashlib
import io
from pathlib import PurePosixPath
import tarfile
from typing import Any

from scripts.product.qualification_v040.guard import QualificationBlocked, require, sha

KAFKA_PATHS = ("/etc/kafka/secrets", "/mnt/shared/config", "/var/lib/kafka/data")
SENTINEL = ".ecomsre-v040-qualification-sentinel"
MAX_ARCHIVE_BYTES = 16 * 1024 * 1024
MAX_SEED_ENTRIES = 4096
# A fixed, reviewed protocol, with no caller-supplied command/path expansion.
# All three paths MUST already be verified as fresh owned named-volume mounts.
SENTINEL_PROTOCOL = r"""set -eu
id -u
id -g
for path in /etc/kafka/secrets /mnt/shared/config /var/lib/kafka/data; do
    target="$path/.ecomsre-v040-qualification-sentinel"
    test ! -e "$target"
    test ! -L "$target"
    (umask 077; set -C; printf 'ecomsre-no-fault-qualification\n' > "$target")
    test "$(cat "$target")" = 'ecomsre-no-fault-qualification'
    rm -- "$target"
    test ! -e "$target"
    test ! -L "$target"
    printf 'PASS %s\n' "$path"
done
"""


def parse_copyup(payload: bytes, path: str) -> dict[str, Any]:
    require(
        path in KAFKA_PATHS and len(payload) <= MAX_ARCHIVE_BYTES,
        "COPYUP_MEASUREMENT_BOUNDS",
    )
    entries: dict[str, Any] = {}
    root_name = PurePosixPath(path).name
    total = 0
    with tarfile.open(fileobj=io.BytesIO(payload), mode="r:") as archive:
        for member in archive:
            require(len(entries) < MAX_SEED_ENTRIES, "COPYUP_MEASUREMENT_BOUNDS")
            parts = PurePosixPath(member.name).parts
            require(
                bool(parts)
                and parts[0] == root_name
                and not PurePosixPath(member.name).is_absolute()
                and ".." not in parts,
                "COPYUP_UNSAFE_ENTRY",
            )
            name = "/".join(parts[1:]) or "."
            require(
                name not in entries and (member.isdir() or member.isfile()),
                "COPYUP_UNSAFE_ENTRY",
            )
            require(member.uid >= 0 and member.gid >= 0, "COPYUP_IDENTITY_MISMATCH")
            entry = {
                "kind": "directory" if member.isdir() else "file",
                "uid": member.uid,
                "gid": member.gid,
                "mode": member.mode & 0o7777,
                "size": member.size,
            }
            if member.isfile():
                total += member.size
                require(total <= MAX_ARCHIVE_BYTES, "COPYUP_MEASUREMENT_BOUNDS")
                stream = archive.extractfile(member)
                require(stream is not None, "COPYUP_INCOMPLETE")
                assert stream is not None
                content = stream.read(MAX_ARCHIVE_BYTES + 1)
                require(len(content) == member.size, "COPYUP_INCOMPLETE")
                entry["sha256"] = hashlib.sha256(content).hexdigest()
            entries[name] = entry
    require("." in entries and entries["."]["kind"] == "directory", "COPYUP_INCOMPLETE")
    return {
        "path": path,
        "entries": entries,
        "entries_sha256": sha(entries),
        "archive_sha256": hashlib.sha256(payload).hexdigest(),
        "content_bytes": total,
    }


def runtime_user(passwd: bytes, configured_user: str) -> tuple[int, int]:
    require(configured_user == "appuser", "RUNTIME_USER_DRIFT")
    lines = [
        line.decode().split(":")
        for line in passwd.splitlines()
        if line.startswith(b"appuser:")
    ]
    require(len(lines) == 1 and len(lines[0]) == 7, "RUNTIME_USER_UNKNOWN")
    try:
        uid, gid = int(lines[0][2]), int(lines[0][3])
    except ValueError as error:
        raise QualificationBlocked("RUNTIME_USER_UNKNOWN") from error
    require(uid > 0 and gid > 0, "RUNTIME_USER_UNKNOWN")
    return uid, gid


def validate_access(measurements: list[dict[str, Any]], uid: int, gid: int) -> None:
    require(
        {row["path"] for row in measurements} == set(KAFKA_PATHS)
        and len(measurements) == 3,
        "COPYUP_INCOMPLETE",
    )
    for row in measurements:
        root = row["entries"]["."]
        require(
            root["uid"] == uid and root["gid"] == gid,
            "COPYUP_IDENTITY_MISMATCH",
            row["path"],
        )
        require(
            root["mode"] & 0o700 == 0o700 and not root["mode"] & 0o002,
            "COPYUP_MODE_MISMATCH",
            row["path"],
        )


def validate_sentinel(stdout: str, uid: int, gid: int) -> dict[str, Any]:
    require(
        stdout.splitlines()
        == [str(uid), str(gid), *(f"PASS {p}" for p in KAFKA_PATHS)],
        "KAFKA_WRITEABILITY_MISMATCH",
    )
    return {
        "status": "PASS",
        "uid": uid,
        "gid": gid,
        "paths": KAFKA_PATHS,
        "sentinel_absent": True,
        "protocol_sha256": hashlib.sha256(SENTINEL_PROTOCOL.encode()).hexdigest(),
    }


def verify_process_identity(status: str, uid: int, gid: int) -> None:
    fields = {
        line.split(":", 1)[0]: line.split(":", 1)[1].split()
        for line in status.splitlines()
        if ":" in line
    }
    require(
        fields.get("Uid") == [str(uid)] * 4 and fields.get("Gid") == [str(gid)] * 4,
        "KAFKA_PROCESS_IDENTITY_MISMATCH",
    )


def validate_seed_lifecycle(
    initial: list[dict[str, Any]],
    current: list[dict[str, Any]],
    uid: int,
    gid: int,
    generated_config: dict[str, Any] | None,
    kafka_started: bool,
) -> None:
    """Conservative bounded capture: never discard original immutable seeds.

    Kafka data additions may change content, but must retain the runtime owner
    and safe modes. Config additions bind once at Kafka's identity stage. A
    capture exceeding the archive limit blocks; it never becomes partial proof.
    """
    validate_access(current, uid, gid)
    originals = {m["path"]: m["entries"] for m in initial}
    for measured in current:
        path, entries = measured["path"], measured["entries"]
        previous = originals[path]
        require(SENTINEL not in entries, "SENTINEL_NOT_REMOVED")
        for name, expected in previous.items():
            require(
                entries.get(name) == expected, "COPYUP_CONTENT_DRIFT", path + "/" + name
            )
        if path == KAFKA_PATHS[0] or not kafka_started:
            require(entries == previous, "UNEXPECTED_SEED_ENTRY", path)
        elif path == KAFKA_PATHS[1] and generated_config is not None:
            require(entries == generated_config, "COPYUP_CONTENT_DRIFT", path)
        for name in set(entries) - set(previous):
            entry = entries[name]
            require(
                entry["uid"] == uid and entry["gid"] == gid,
                "KAFKA_SEED_IDENTITY_DRIFT",
                path + "/" + name,
            )
            require(
                not entry["mode"] & 0o002, "KAFKA_SEED_MODE_DRIFT", path + "/" + name
            )


def validate_seed_pair(
    before: list[dict[str, Any]], after: list[dict[str, Any]]
) -> None:
    """Two complete archives must agree, except mutable Kafka data contents."""
    require(
        {m["path"] for m in before} == set(KAFKA_PATHS) == {m["path"] for m in after},
        "COPYUP_INCOMPLETE",
    )
    right = {m["path"]: m["entries"] for m in after}
    for measured in before:
        path, entries = measured["path"], measured["entries"]
        if path != KAFKA_PATHS[2]:
            require(entries == right[path], "RACED_SEED_CAPTURE", path)
        else:
            # Creation/removal during enumeration is a raced capture, even when
            # new entries are allowed across separate lifecycle checkpoints.
            require(set(entries) == set(right[path]), "RACED_SEED_CAPTURE", path)
            for name, entry in entries.items():
                require(
                    all(
                        entry[k] == right[path][name][k]
                        for k in ("kind", "uid", "gid", "mode")
                    ),
                    "RACED_SEED_CAPTURE",
                    path + "/" + name,
                )
