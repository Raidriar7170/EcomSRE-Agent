"""Fixed create/read/stat/delete sentinel, separately journaled from remediation."""

from __future__ import annotations
from typing import Any
from scripts.product.qualification_v040.guard import require
from scripts.product.qualification_v040.volumes import KAFKA_PATHS
from scripts.product.qualification_v040_v2.policy import validate_process

SENTINEL_V2 = r"""set -euo pipefail
[[ "$1" =~ ^[a-f0-9]{32}$ ]]
uid=MISSING gid=MISSING groups=MISSING eff=MISSING prm=MISSING nnp=MISSING ppid=MISSING
while IFS=: read -r key value; do
    case "$key" in
        Uid) uid=$value;; Gid) gid=$value;; Groups) groups=$value;;
        CapEff) eff=$value;; CapPrm) prm=$value;; NoNewPrivs) nnp=$value;; PPid) ppid=$value;;
    esac
done < /proc/self/status
IFS= read -r statline < /proc/self/stat
rest=${statline##*) }
fields=($rest)
printf 'IDENTITY\0%s\0%s\0%s\0%s\0%s\0%s\0%s\0%s\0%s\0' "$$" "$ppid" "$uid" "$gid" "$groups" "$eff" "$prm" "$nnp" "${fields[19]}"
[[ $uid =~ ^[[:space:]]*1000[[:space:]]+1000[[:space:]]+1000[[:space:]]+1000[[:space:]]*$ ]]
[[ $gid =~ ^[[:space:]]*1000[[:space:]]+1000[[:space:]]+1000[[:space:]]+1000[[:space:]]*$ ]]
for group in $groups; do [[ "$group" == 1000 ]]; done
[[ "$eff" == *0000000000000000 && "$prm" == *0000000000000000 ]]
[[ "$nnp" =~ ^[[:space:]]*1[[:space:]]*$ ]]
for path in /etc/kafka/secrets /mnt/shared/config /var/lib/kafka/data; do
    target="$path/.ecomsre-v2-$1"
    [[ ! -e "$target" && ! -L "$target" ]]
    umask 077
    set -C
    printf 'qualification-v2:%s\n' "$1" > "$target"
    IFS= read -r content < "$target"
    [[ "$content" == "qualification-v2:$1" ]]
    metadata=$(stat -c '%u:%g:%a' -- "$target")
    [[ "$metadata" == '1000:1000:600' ]]
    rm -- "$target"
    [[ ! -e "$target" && ! -L "$target" ]]
    printf 'PASS\0%s\0%s\0ABSENT\0' "$path" "$metadata"
done
printf 'COMPLETE\0'
"""


def validate_sentinel_v2(
    stdout: str, policy: dict[str, Any], qualification: str
) -> dict[str, Any]:
    fields = stdout.split("\0")
    require(
        len(fields) == 24
        and fields[0] == "IDENTITY"
        and fields[-2:] == ["COMPLETE", ""],
        "SENTINEL_INCOMPLETE",
    )
    process = {
        "pid": int(fields[1]),
        "ppid": int(fields[2]),
        "start_time": int(fields[9]),
        "uids": [int(v) for v in fields[3].split()],
        "gids": [int(v) for v in fields[4].split()],
        "groups": [int(v) for v in fields[5].split()],
        "cap_eff": int(fields[6], 16),
        "cap_prm": int(fields[7], 16),
        "no_new_privs": int(fields[8]),
        "argv": [
            "/bin/bash",
            "-c",
            SENTINEL_V2,
            "qualification-sentinel",
            qualification,
        ],
    }
    validate_process(policy, process)
    for index, path in enumerate(KAFKA_PATHS):
        require(
            fields[10 + index * 4 : 14 + index * 4]
            == ["PASS", path, "1000:1000:600", "ABSENT"],
            "SENTINEL_FAILED",
        )
    return {
        "status": "PASS",
        "process_identity": process,
        "paths": list(KAFKA_PATHS),
        "file_mode": "0600",
        "sentinel_absent": True,
        "activity": "QUALIFICATION_SENTINEL_NOT_REMEDIATION",
        "bounded_child_utilities": ["stat", "rm"],
    }
