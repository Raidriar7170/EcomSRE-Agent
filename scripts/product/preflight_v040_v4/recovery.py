"""Narrow prior-attempt closure exception, committed and independently reviewed."""

from pathlib import Path
from .common import REPO, GOAL_SHA, load, sha, require, digest


def cleanup_complete(root: Path, identifier: str) -> bool:
    prior = root / identifier
    cleanup = prior / "cleanup-result.json"
    if cleanup.exists() and load(cleanup)["status"] == "CLEAN":
        return True
    authorizations = (
        REPO / "config/product-v040/preflight-v4/recovery-authorizations.json"
    )
    if not authorizations.exists():
        return False
    commitment = load(authorizations)["attempts"].get(identifier)
    if not commitment:
        return False
    require(
        commitment["file"] == "stabilization-recovery-v2.json", "RECOVERY_FILE_DENIED"
    )
    path = prior / commitment["file"]
    require(
        path.is_file()
        and not path.is_symlink()
        and sha(path.read_bytes()) == commitment["sha256"],
        "RECOVERY_COMMITMENT_DRIFT",
    )
    proof = load(path)
    require(
        proof["schema_version"] == "ecomsre.preflight.v4.stabilization-recovery.v2"
        and proof["goal_sha256"] == GOAL_SHA
        and proof["attempt_id"] == identifier
        and proof["disposition"] == "OWNED_CLEANUP_VERIFIED_NEW_BASELINE_REQUIRED"
        and proof["old_result_preserved"] is True
        and proof["nonowned_unchanged"] is False,
        "RECOVERY_AUTHORITY_DRIFT",
    )
    for relative, expected in proof["files"].items():
        part = Path(relative)
        require(
            not part.is_absolute() and ".." not in part.parts, "RECOVERY_PATH_DENIED"
        )
        file = prior / part
        require(
            file.is_file()
            and not file.is_symlink()
            and sha(file.read_bytes()) == expected,
            "RECOVERY_EVIDENCE_DRIFT",
        )
    result = load(prior / "attempt-result.json")
    require(
        result["status"] == "FAILED" and result["cleanup_status"] == "BLOCKED_SAFETY",
        "RECOVERY_REWRITES_RESULT",
    )
    failure = load(prior / "cleanup-failure.json")
    require(failure["reason"] == "NONOWNED_DRIFT", "RECOVERY_NOT_HOST_DRIFT")
    cleanup_proof = load(prior / "owned-cleanup-nonowned-drift-proof.json")
    require(
        cleanup_proof["owned_remaining"]
        == {"containers": 0, "networks": 0, "volumes": 0}
        and cleanup_proof["final_views_stable"] is True
        and cleanup_proof["images_unchanged"] is True
        and cleanup_proof["nonowned_unchanged"] is False,
        "RECOVERY_OWNED_NOT_CLOSED",
    )
    event = load(prior / "daemon-stabilization-event.json")
    require(
        event["observation"] == "VM_START_TRIGGERED_BY_FIRST_VOLUME_CREATE"
        and any(
            "idle -> busy" in row["raw"] and "/volumes/create" in row["raw"]
            for row in event["records"]
        ),
        "RECOVERY_DAEMON_EVENT_MISSING",
    )
    births = [load(p) for p in sorted((prior / "births").glob("*.json"))]
    for filename in (
        "closure-final-inventory-1.json",
        "closure-final-inventory-2.json",
    ):
        final = load(prior / filename)
        for birth in births:
            kind = birth["kind"]
            field = "Name" if kind == "volume" else "Id"
            require(
                not any(
                    r[field] == birth["record"][field] for r in final["resources"][kind]
                ),
                "RECOVERY_RETAINED_RESOURCE",
            )
    for file in sorted((prior / "journal/receipts").glob("*.json")):
        require(
            str(file.relative_to(prior)) in proof["files"], "RECOVERY_UNBOUND_RECEIPT"
        )
        receipt = load(file)
        intent = load(prior / "journal/intents" / file.name)
        require(
            receipt["intent_digest"] == digest(intent)
            and receipt["postcondition"] is True
            and receipt["outcome"]["client_exit_status"] == 0,
            "RECOVERY_RECEIPT_INVALID",
        )
    return True
