"""Read-only, identity-bound cgroup-v2 OOM configuration capture for the owned probe."""

from __future__ import annotations
import hashlib
from typing import Any

from scripts.product.qualification_v040.guard import require, QualificationBlocked

OOM_PROTOCOL = r"""set -euo pipefail
collect() {
  mapfile -t cg < /proc/1/cgroup
  ((${#cg[@]} == 1))
  [[ "${cg[0]}" == '0::/' ]]
  count=0
  while IFS= read -r line; do
    if [[ "$line" == *' - cgroup2 '* ]]; then
      fields=($line)
      [[ "${fields[4]}" == '/sys/fs/cgroup' ]]
      count=$((count+1))
    fi
  done < /proc/1/mountinfo
  ((count == 1))
  [[ ! -e /sys/fs/cgroup/memory.oom_control ]]
  IFS= read -r maximum < /sys/fs/cgroup/memory.max
  IFS= read -r group < /sys/fs/cgroup/memory.oom.group
  IFS= read -r adjustment < /proc/1/oom_score_adj
  printf 'OOM\0%s\0cgroup2\0%s\0%s\0%s\0ABSENT\0' "${cg[0]}" "$maximum" "$group" "$adjustment"
  for pid in 1 $$; do
    uid=MISSING gid=MISSING groups=MISSING eff=MISSING prm=MISSING nnp=MISSING
    while IFS=: read -r key value; do
      case "$key" in
        Uid) uid=$value;; Gid) gid=$value;; Groups) groups=$value;;
        CapEff) eff=$value;; CapPrm) prm=$value;; NoNewPrivs) nnp=$value;;
      esac
    done < "/proc/$pid/status"
    IFS= read -r statline < "/proc/$pid/stat"
    rest=${statline##*) }
    fields=($rest)
    printf 'IDENTITY\0%s\0%s\0%s\0%s\0%s\0%s\0%s\0%s\0' "$pid" "${fields[19]}" "$uid" "$gid" "$groups" "$eff" "$prm" "$nnp"
  done
  printf 'COMPLETE\0'
}
collect
collect
"""


def parse_oom(raw: str) -> dict[str, Any]:
    try:
        require(len(raw.encode()) < 65536, "OOM_CAPTURE_BOUNDS")
        passes = raw.split("COMPLETE\0")
        require(len(passes) == 3 and passes[-1] == "", "OOM_CAPTURE_INCOMPLETE")
        result = []
        for part in passes[:2]:
            values = part.split("\0")
            require(
                len(values) == 26 and values[0] == "OOM" and values[-1] == "",
                "OOM_CAPTURE_INCOMPLETE",
            )
            require(values[6] == "ABSENT", "OOM_LEGACY_CONTROL_PRESENT")
            identities: list[dict[str, Any]] = []
            for offset in (7, 16):
                require(values[offset] == "IDENTITY", "OOM_CAPTURE_INCOMPLETE")
                row: dict[str, Any] = dict(
                    pid=int(values[offset + 1]),
                    start_time=int(values[offset + 2]),
                    uids=[int(v) for v in values[offset + 3].split()],
                    gids=[int(v) for v in values[offset + 4].split()],
                    groups=[int(v) for v in values[offset + 5].split()],
                    cap_eff=int(values[offset + 6].strip(), 16),
                    cap_prm=int(values[offset + 7].strip(), 16),
                    no_new_privs=int(values[offset + 8]),
                )
                require(
                    row["uids"] == [1000] * 4
                    and row["gids"] == [1000] * 4
                    and row["groups"] in ([], [1000])
                    and row["cap_eff"] == row["cap_prm"] == 0
                    and row["no_new_privs"] == 1
                    and row["start_time"] > 0,
                    "OOM_OBSERVER_IDENTITY_UNSAFE",
                )
                identities.append(row)
            require(
                identities[0]["pid"] == 1 and identities[1]["pid"] > 1,
                "OOM_CAPTURE_IDENTITY_DRIFT",
            )
            result.append(
                {
                    "cgroup": values[1],
                    "cgroup_fs": values[2],
                    "memory_max": values[3],
                    "memory_oom_group": values[4],
                    "oom_score_adj": values[5],
                    "legacy_oom_control_present": False,
                    **identities[0],
                    "observer": identities[1],
                }
            )
        require(result[0] == result[1], "OOM_EVIDENCE_RACE")
        return {
            "first": result[0],
            "second": result[1],
            "protocol_sha256": hashlib.sha256(OOM_PROTOCOL.encode()).hexdigest(),
            "raw_sha256": hashlib.sha256(raw.encode()).hexdigest(),
        }
    except QualificationBlocked:
        raise
    except (ValueError, KeyError, TypeError, IndexError) as error:
        raise QualificationBlocked(
            "OOM_CAPTURE_INCOMPLETE", type(error).__name__
        ) from error
