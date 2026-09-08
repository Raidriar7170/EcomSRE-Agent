"""Reviewed cleanup-only recovery from immutable original create receipts."""

from __future__ import annotations
import argparse
from pathlib import Path
from typing import Any
from .common import ROOT, load, seal, require, git, digest, now
from .docker import Docker
from .resources import Resources


def resume(admission_path: Path) -> dict[str, Any]:
    admission = load(admission_path)
    identifier = admission["attempt_id"]
    require(
        len(identifier) == 32 and all(c in "0123456789abcdef" for c in identifier),
        "INVALID_ATTEMPT_ID",
    )
    root = ROOT / identifier
    require(root.is_dir() and not root.is_symlink(), "ATTEMPT_ROOT_UNBOUND")
    plan = load(root / "ownership-plan.json")
    require(not git("status", "--porcelain"), "CLEANUP_REPAIR_SOURCE_DIRTY")
    name = "cleanup-repair-admission-" + admission["cleanup_head"] + ".json"
    if (root / name).exists():
        require(load(root / name) == admission, "CLEANUP_ADMISSION_DRIFT")
    else:
        seal(root, name, admission)
    docker = Docker()
    resources = Resources(
        root,
        plan,
        docker,
        admission["original_source_head"],
        cleanup_admission=admission,
    )
    # Only existing successful receipts are recoverable. No new create call is eligible.
    for path in sorted((root / "journal/receipts").glob("create-*.json")):
        intent = load(root / "journal/intents" / path.name)
        receipt = load(path)
        require(
            receipt["intent_digest"] == digest(intent), "ORIGINAL_CREATE_CHAIN_DRIFT"
        )
        resources.create_group(
            path.stem,
            intent["command"],
            [tuple(t) for t in intent["authority"]["target_plan"]],
        )
    before = resources.capture("before-reviewed-cleanup")
    running = [
        r
        for r in before["resources"]["container"]
        if r["State"]["Running"]
        and (r["Config"].get("Labels") or {}).get("io.ecomsre.preflight.v4.attempt")
        == identifier
    ]
    if running:
        from .cleanup_oom import CONTAINER, admit_running_cleanup

        require([r["Id"] for r in running] == [CONTAINER], "UNADMITTED_RUNNING_CLEANUP")
        admit_running_cleanup(resources, before)
    try:
        result = resources.cleanup(load(root / "initial-inventory-1.json"))
    except Exception as error:
        seal(
            root,
            "cleanup-resume-failure-" + admission["cleanup_head"] + ".json",
            {"type": type(error).__name__, "reason": str(error), **now()},
        )
        raise
    seal(
        root,
        "cleanup-resume-result-" + admission["cleanup_head"] + ".json",
        {"result": result, "original_attempt_result_preserved": True, **now()},
    )
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--admission", required=True, type=Path)
    args = parser.parse_args()
    print(resume(args.admission))
