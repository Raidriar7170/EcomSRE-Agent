"""Bounded mutable-data metadata capture; never hashes Kafka log/index contents."""

from __future__ import annotations
from typing import Any
from scripts.product.qualification_v040.guard import require, sha

DATA_METADATA_PROTOCOL = r"""set -euo pipefail
shopt -s dotglob nullglob
root=/var/lib/kafka/data
pending=("$root")
count=0
while ((${#pending[@]})); do
    entry=${pending[-1]}
    unset 'pending[-1]'
    [[ ! -L "$entry" ]]
    if [[ -d "$entry" ]]; then
        kind=directory
        children=("$entry"/*)
        pending+=("${children[@]}")
    elif [[ -f "$entry" ]]; then
        kind=file
    else
        exit 31
    fi
    ((count+=1))
    ((count<=4096 && ${#entry}<=4096))
    value=$(stat -c '%u:%g:%a:%s:%i' -- "$entry")
    IFS=: read -r uid gid mode size inode <<< "$value"
    relative=${entry#"$root"/}
    [[ "$entry" != "$root" ]] || relative=.
    [[ "$kind" != directory ]] || size=0
    printf 'ENTRY\0%s\0%s\0%s\0%s\0%s\0%s\0%s\0' "$relative" "$kind" "$uid" "$gid" "$mode" "$size" "$inode"
done
printf 'COMPLETE\0'
"""


def parse_data_metadata(stdout: str) -> dict[str, Any]:
    require(len(stdout.encode()) <= 2 * 1024 * 1024, "DATA_METADATA_BOUNDS")
    tokens = stdout.split("\0")
    require(
        tokens[-2:] == ["COMPLETE", ""] and (len(tokens) - 2) % 8 == 0,
        "DATA_METADATA_INCOMPLETE",
    )
    entries, inodes = {}, {}
    for i in range(0, len(tokens) - 2, 8):
        marker, path, kind, uid, gid, mode, size, inode = tokens[i : i + 8]
        require(
            marker == "ENTRY"
            and path not in entries
            and not path.startswith("/")
            and ".." not in path.split("/")
            and kind in {"file", "directory"},
            "DATA_METADATA_UNSAFE",
        )
        entries[path] = {
            "kind": kind,
            "uid": int(uid),
            "gid": int(gid),
            "mode": int(mode, 8),
            "size": int(size),
        }
        inodes[path] = int(inode)
    require(
        "." in entries and entries["."]["kind"] == "directory" and len(entries) <= 4096,
        "DATA_METADATA_INCOMPLETE",
    )
    return {
        "path": "/var/lib/kafka/data",
        "entries": entries,
        "inodes": inodes,
        "entries_sha256": sha(entries),
        "content_digest_policy": "NEW_MUTABLE_DATA_CONTENT_NOT_IMMUTABLE_SEED",
        "readonly_capture_utility": "stat",
    }
