"""Two-pass, builtin-only probe process census; selectively adapted from reviewed v2 parser."""

from typing import Any
from .common import require, Failure

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
    except Failure:
        raise
    except (ValueError, StopIteration, TypeError, IndexError) as error:
        raise Failure("PROCESS_IDENTITY_INCOMPLETE:" + type(error).__name__) from error


def validate_process(process: dict[str, Any]) -> None:
    require(
        process["uids"] == [1000] * 4
        and process["gids"] == [1000] * 4
        and set(process["groups"]) <= {1000}
        and process["cap_eff"] == process["cap_prm"] == 0
        and process["no_new_privs"] == 1
        and process["start_time"] > 0,
        "PROCESS_AUTHORITY_DRIFT",
    )


def validate_probe_census(stdout: str) -> dict[str, Any]:
    census = parse_census(stdout)
    rows = census["processes"]
    require(
        len(rows) == 2 and {r["pid"] for r in rows} == {1, census["self_pid"]},
        "UNKNOWN_WRITER_PROCESS",
    )
    for row in rows:
        validate_process(row)
        require(
            row["ppid"] == 0
            and row["argv"]
            == (
                ["/bin/sleep", "2147483647"]
                if row["pid"] == 1
                else ["/bin/bash", "-c", CENSUS_PROTOCOL]
            ),
            "UNKNOWN_WRITER_PROCESS",
        )
    return census
