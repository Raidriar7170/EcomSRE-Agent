"""Read bounded tar metadata without filesystem extraction."""

import hashlib
import io
from pathlib import PurePosixPath
import tarfile
from typing import Any
from .common import require, digest as sha, KAFKA_PATHS

MAX_ARCHIVE_BYTES = 16 * 1024 * 1024
MAX_SEED_ENTRIES = 4096


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
