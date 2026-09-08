"""One-attempt, reviewed read-only OOM census; grants cleanup authority only."""

from __future__ import annotations
import hashlib
from typing import Any, TYPE_CHECKING
from .common import require, Failure, digest, load, seal, git, now
from .identity import immutable
from .oom import OOM_PROTOCOL

if TYPE_CHECKING:
    from .resources import Resources

ATTEMPT = "c979432b498c4aca989c11f86ede0346"
CONTAINER = "0b153c75911d51894d2914894ba2a9379089cdd170768e98c16b38c35fd831e8"
ROLE = "astronomy-db"
POLICY = "attempt5-astronomy-db-cleanup-cgroup-v1"


def parse_cleanup_oom(raw: str) -> dict[str, Any]:
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
                    len(row["uids"]) == len(row["gids"]) == 4
                    and all(v >= 0 for v in row["uids"] + row["gids"] + row["groups"])
                    and row["no_new_privs"] in (0, 1)
                    and row["start_time"] > 0,
                    "OOM_OBSERVER_IDENTITY_UNSAFE",
                )
                if offset == 16:
                    require(
                        row["uids"] == row["gids"] == [1000] * 4
                        and row["groups"] in ([], [1000])
                        and row["cap_eff"] == row["cap_prm"] == 0,
                        "CLEANUP_OBSERVER_PRIVILEGED",
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
    except Failure:
        raise
    except (ValueError, KeyError, TypeError, IndexError) as error:
        raise Failure("OOM_CAPTURE_INCOMPLETE:" + type(error).__name__) from error


def admit_running_cleanup(resources: Resources, before: dict[str, Any]) -> None:
    admission = resources.cleanup_admission or {}
    require(
        resources.cleanup_only
        and resources.attempt == ATTEMPT
        and admission.get("running_cleanup")
        == {"policy": POLICY, "container_id": CONTAINER, "role": ROLE},
        "RUNNING_CLEANUP_NOT_ADMITTED",
    )
    birth = resources.find_birth("container", ROLE)
    require(birth["record"]["Id"] == CONTAINER, "CLEANUP_CENSUS_TARGET_DRIFT")

    def target(view: dict[str, Any]) -> dict[str, Any]:
        require(view["binding"] == birth["binding"], "DAEMON_IDENTITY_DRIFT")
        rows = [r for r in view["resources"]["container"] if r["Id"] == CONTAINER]
        require(len(rows) == 1, "CLEANUP_CENSUS_TARGET_MISSING")
        row = rows[0]
        require(
            immutable(row) == immutable(birth["record"])
            and birth["record"]["HostConfig"]["OomKillDisable"] is False
            and row["HostConfig"]["OomKillDisable"] is None
            and row["State"]["Running"] is True
            and row["State"]["Pid"] > 0
            and not row["State"].get("Paused")
            and not row["State"].get("Restarting")
            and row["RestartCount"] == 0,
            "CLEANUP_CENSUS_IDENTITY_DRIFT",
        )
        return row

    row = target(before)
    receipt = load(resources.root / "journal/receipts/start-astronomy-db.json")
    intent = load(resources.root / "journal/intents/start-astronomy-db.json")
    require(
        receipt["intent_digest"] == digest(intent)
        and receipt["postcondition"] is True
        and receipt["outcome"]["client_exit_status"] == 0
        and intent["command"] == ["container", "start", CONTAINER],
        "CLEANUP_CENSUS_START_CHAIN_DRIFT",
    )
    started = target(receipt["observation"])
    require(
        row["State"]["Pid"] == started["State"]["Pid"]
        and row["State"]["StartedAt"] == started["State"]["StartedAt"],
        "CLEANUP_CENSUS_PROCESS_DRIFT",
    )
    require(
        git("rev-parse", "HEAD") == resources.execution_head
        and not git("status", "--porcelain"),
        "CLEANUP_CENSUS_SOURCE_DRIFT",
    )
    command = [
        "exec",
        "--user",
        "1000:1000",
        CONTAINER,
        "/bin/bash",
        "--noprofile",
        "--norc",
        "-c",
        OOM_PROTOCOL,
    ]
    seal(
        resources.root,
        "cleanup-oom-census-intent.json",
        {
            "policy": POLICY,
            "command": command,
            "admission_digest": digest(admission),
            "birth_digest": digest(birth),
            "before": before,
            "start_receipt_digest": digest(receipt),
            **now(),
        },
    )
    resources.docker.binding()
    result = resources.docker.command(command, timeout=45)
    seal(
        resources.root,
        "cleanup-oom-census-raw.json",
        {
            "returncode": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
            **now(),
        },
    )
    after = resources.capture("after-cleanup-oom-census")
    fresh = target(after)
    require(
        row["State"]["Pid"] == fresh["State"]["Pid"]
        and row["State"]["StartedAt"] == fresh["State"]["StartedAt"],
        "CLEANUP_CENSUS_PROCESS_RACE",
    )
    require(result.returncode == 0, "CLEANUP_CENSUS_FAILED")
    parsed = parse_cleanup_oom(result.stdout)
    value = parsed["first"]
    require(
        value["cgroup"] == "0::/"
        and value["cgroup_fs"] == "cgroup2"
        and value["memory_max"] == str(row["HostConfig"]["Memory"])
        and value["memory_oom_group"] == "0"
        and value["oom_score_adj"] == str(row["HostConfig"].get("OomScoreAdj", 0)),
        "CLEANUP_CENSUS_EFFECTIVE_DRIFT",
    )
    seal(
        resources.root,
        "cleanup-only-oom-proof.json",
        {
            "policy": POLICY,
            "attempt_id": ATTEMPT,
            "role": ROLE,
            "container_id": CONTAINER,
            "binding_digest": digest(before["binding"]),
            "immutable_digest": digest(immutable(row)),
            "birth_record_digest": digest(birth["record"]),
            "create_receipt_digest": birth["create_receipt_digest"],
            "cleanup_head": resources.execution_head,
            "admission_digest": digest(admission),
            "started_at": row["State"]["StartedAt"],
            "pid": row["State"]["Pid"],
            "before_digest": digest(before),
            "after_digest": digest(after),
            "evidence": parsed,
            **now(),
        },
    )


def permits_cleanup(
    birth: dict[str, Any],
    current: dict[str, Any],
    binding: dict[str, Any],
    attempt: str,
) -> bool:
    proof = birth.get("cleanup_only_oom_proof") or {}
    admission = birth.get("cleanup_admission") or {}
    return bool(
        attempt == ATTEMPT
        and birth["role"] == ROLE
        and current["Id"] == CONTAINER
        and proof.get("policy") == POLICY
        and proof.get("attempt_id") == attempt
        and proof.get("role") == ROLE
        and proof.get("container_id") == CONTAINER
        and admission.get("running_cleanup")
        == {"policy": POLICY, "container_id": CONTAINER, "role": ROLE}
        and proof.get("cleanup_head") == admission.get("cleanup_head")
        and proof.get("admission_digest") == digest(admission)
        and proof.get("birth_record_digest") == digest(birth["record"])
        and proof.get("create_receipt_digest") == birth["create_receipt_digest"]
        and proof.get("binding_digest") == digest(binding)
        and proof.get("immutable_digest") == digest(immutable(current))
        and proof.get("started_at") == current["State"].get("StartedAt")
        and current["RestartCount"] == 0
        and current["State"]["Pid"]
        == (proof.get("pid") if current["State"]["Running"] else 0)
    )
