"""Complete bounded process census using only Bash builtins in the owned PID namespace."""

from __future__ import annotations

from typing import Any

from scripts.product.qualification_v040.guard import require, QualificationBlocked

# No subprocess is spawned by this read-only census. Its own process is an
# explicit writer-capable participant, bound by this exact protocol and argv.
CENSUS_PROTOCOL = r"""set -euo pipefail
collect() {
before=(/proc/[0-9]*)
((${#before[@]} <= 64))
printf 'CENSUS\0%s\0' "$$"
for directory in "${before[@]}"; do
    pid=${directory##*/}
    uid=MISSING gid=MISSING groups=MISSING eff=MISSING prm=MISSING nnp=MISSING
    while IFS=: read -r key value; do
        case "$key" in
            Uid) uid=$value;; Gid) gid=$value;; Groups) groups=$value;;
            CapEff) eff=$value;; CapPrm) prm=$value;; NoNewPrivs) nnp=$value;;
        esac
    done < "$directory/status"
    IFS= read -r statline < "$directory/stat"
    rest=${statline##*) }
    fields=($rest)
    args=()
    while IFS= read -r -d '' arg; do
        ((${#arg} <= 32768))
        args+=("$arg")
        ((${#args[@]} <= 512))
    done < "$directory/cmdline"
    printf 'PROCESS\0%s\0%s\0%s\0%s\0%s\0%s\0%s\0%s\0%s\0%s\0' "$pid" "${fields[1]}" "${fields[19]}" "$uid" "$gid" "$groups" "$eff" "$prm" "$nnp" "${#args[@]}"
    printf '%s\0' "${args[@]}"
done
after=(/proc/[0-9]*)
[[ "${before[*]}" == "${after[*]}" ]]
printf 'COMPLETE\0'
}
collect
collect
"""


def _parse_pass(stdout: str) -> dict[str, Any]:
    require(len(stdout.encode()) <= 2 * 1024 * 1024, "PROCESS_CENSUS_BOUNDS")
    fields = iter(stdout.split("\0"))
    require(next(fields, None) == "CENSUS", "PROCESS_CENSUS_INCOMPLETE")
    own_pid = int(next(fields))
    rows: list[dict[str, Any]] = []
    while True:
        marker = next(fields, None)
        if marker == "COMPLETE":
            require(list(fields) == [""], "PROCESS_CENSUS_INCOMPLETE")
            break
        require(marker == "PROCESS", "PROCESS_CENSUS_INCOMPLETE")
        pid, ppid, start = (int(next(fields)) for _ in range(3))
        uids, gids, groups = ([int(v) for v in next(fields).split()] for _ in range(3))
        eff, prm = (int(next(fields).strip(), 16) for _ in range(2))
        nnp, count = (int(next(fields)) for _ in range(2))
        require(0 < count <= 512 and len(rows) < 64, "PROCESS_IDENTITY_INCOMPLETE")
        argv = [next(fields) for _ in range(count)]
        require(len(uids) == len(gids) == 4, "PROCESS_IDENTITY_INCOMPLETE")
        rows.append(
            {
                "pid": pid,
                "ppid": ppid,
                "start_time": start,
                "uids": uids,
                "gids": gids,
                "groups": groups,
                "cap_eff": eff,
                "cap_prm": prm,
                "no_new_privs": nnp,
                "argv": argv,
            }
        )
    require(
        any(
            r["pid"] == own_pid and r["argv"] == ["/bin/bash", "-c", CENSUS_PROTOCOL]
            for r in rows
        ),
        "PROCESS_CENSUS_SELF_UNBOUND",
    )
    return {"complete": True, "self_pid": own_pid, "processes": rows}


def approved_process_argv(
    census: dict[str, Any], role: str, kafka_argv: list[str] | None = None
) -> list[dict[str, Any]]:
    """Bind each admitted role to one stable PID identity; argv is not a capability."""
    rows = census["processes"]
    own = census.get("self_pid")
    require(own is not None and own != 1, "PROCESS_CENSUS_SELF_UNBOUND")
    require(len({r["pid"] for r in rows}) == len(rows), "PROCESS_CENSUS_INCOMPLETE")
    primary = ["/bin/sleep", "2147483647"] if role == "probe" else kafka_argv
    require(bool(primary), "KAFKA_ARGV_CONTRACT_UNBOUND")
    roles: dict[str, dict[str, Any]] = {}
    for row in rows:
        if row["pid"] == 1:
            label, expected, parent = "primary", primary, {0}
        elif row["pid"] == own:
            label, expected, parent = (
                "census",
                ["/bin/bash", "-c", CENSUS_PROTOCOL],
                {0},
            )
        elif role == "kafka" and row["argv"] == ["/bin/sh", "-c", "nc -z kafka 9092"]:
            label, expected, parent = "health_shell", row["argv"], {0}
        elif role == "kafka" and row["argv"] == ["nc", "-z", "kafka", "9092"]:
            shell = [
                r["pid"]
                for r in rows
                if r["argv"] == ["/bin/sh", "-c", "nc -z kafka 9092"]
            ]
            label, expected, parent = "health_nc", row["argv"], {0, *shell}
        else:
            raise QualificationBlocked("UNKNOWN_WRITER_PROCESS")
        require(
            label not in roles and row["argv"] == expected and row["ppid"] in parent,
            "UNKNOWN_WRITER_PROCESS",
        )
        roles[label] = row
    require({"primary", "census"} <= roles.keys(), "PROCESS_CENSUS_INCOMPLETE")
    return list(roles.values())


def _parse_census(stdout: str) -> dict[str, Any]:
    require(len(stdout.encode()) <= 2 * 1024 * 1024, "PROCESS_CENSUS_BOUNDS")
    parts = stdout.split("COMPLETE\0")
    require(len(parts) == 3 and parts[-1] == "", "PROCESS_CENSUS_INCOMPLETE")
    passes = [_parse_pass(part + "COMPLETE\0") for part in parts[:2]]
    require(passes[0] == passes[1], "PROCESS_CENSUS_IDENTITY_RACE")
    return {**passes[0], "passes": passes}


def parse_census(stdout: str) -> dict[str, Any]:
    try:
        return _parse_census(stdout)
    except QualificationBlocked:
        raise
    except (ValueError, StopIteration, TypeError, IndexError) as error:
        raise QualificationBlocked(
            "PROCESS_IDENTITY_INCOMPLETE", type(error).__name__
        ) from error
