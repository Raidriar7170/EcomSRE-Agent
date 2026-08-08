"""Rehearse all v2-dev.2 schedule contracts with zero Provider access."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from ecomsre_rcaeval_v2.dev2_admission import (
    ADMISSION_LOCK_NAME,
    rehearse_schedule_admission,
    write_admission_lock,
)
from ecomsre_rcaeval_v2.dev2_evaluation_root import verify_evaluation_root
from ecomsre_rcaeval_v2.dev2_execution import (
    discover_case_index,
    extract_run_ids,
    load_private_schedule,
)
from ecomsre_rcaeval_v2.dev_execution import load_split_assignments
from ecomsre_rcaeval_v2.schedule import SplitName
from ecomsre_rcaeval_v2.dev2_paths import (
    reject_dev2_forbidden_paths,
    tree_sha256,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _nested_sha(payload: object, *keys: str) -> str:
    current = payload
    for key in keys:
        if not isinstance(current, dict):
            raise ValueError("preserved public schedule binding is invalid")
        current = current.get(key)
    if not isinstance(current, str) or len(current) != 64:
        raise ValueError("preserved public schedule binding is invalid")
    return current


def main(argv: tuple[str, ...] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ob-root", required=True, type=Path)
    parser.add_argument("--ss-root", required=True, type=Path)
    parser.add_argument("--split-manifest", required=True, type=Path)
    parser.add_argument("--control-root", required=True, type=Path)
    parser.add_argument("--output-root", required=True, type=Path)
    parser.add_argument("--smoke-journal-root", required=True, type=Path)
    parser.add_argument("--design-journal-root", required=True, type=Path)
    parser.add_argument("--v2-dev-v1-root", required=True, type=Path)
    parser.add_argument("--v2-dev1-control-root", required=True, type=Path)
    parser.add_argument("--v2-dev1-output-root", required=True, type=Path)
    args = parser.parse_args(argv)
    reject_dev2_forbidden_paths(
        args.ob_root,
        args.ss_root,
        args.split_manifest,
        args.control_root,
        args.output_root,
        args.smoke_journal_root,
        args.design_journal_root,
        args.v2_dev_v1_root,
        args.v2_dev1_control_root,
        args.v2_dev1_output_root,
    )
    evaluation = verify_evaluation_root(
        args.control_root,
        args.output_root,
        args.smoke_journal_root,
        args.design_journal_root,
        project_root=PROJECT_ROOT,
    )
    schedules_root = args.control_root / "schedules"
    smoke = load_private_schedule(
        schedules_root / "smoke-schedule.json", allowed_split=SplitName.DESIGN
    )
    design = load_private_schedule(
        schedules_root / "design-schedule.json", allowed_split=SplitName.DESIGN
    )
    validation = load_private_schedule(
        schedules_root / "dev-validation-schedule.json",
        allowed_split=SplitName.DEV_VALIDATION,
    )
    preserved_paths = {
        "v2_dev_v1_design": args.v2_dev_v1_root / "schedule/design-schedule.json",
        "v2_dev_v1_validation": args.v2_dev_v1_root
        / "schedule/dev-validation-schedule.json",
        "v2_dev1_design": args.v2_dev1_control_root
        / "schedules/design-schedule.json",
        "v2_dev1_validation": args.v2_dev1_control_root
        / "schedules/dev-validation-schedule.json",
    }
    preserved_hashes = {name: _sha(path) for name, path in preserved_paths.items()}
    dev_v1_public_lock = json.loads(
        (PROJECT_ROOT / "config/rcaeval-re2-v2-dev/evaluation-lock.json").read_text(
            encoding="utf-8"
        )
    )
    if (
        preserved_hashes["v2_dev_v1_design"]
        != _nested_sha(dev_v1_public_lock, "schedule_sha256", "design")
        or preserved_hashes["v2_dev_v1_validation"]
        != _nested_sha(dev_v1_public_lock, "schedule_sha256", "dev_validation")
    ):
        raise ValueError("v2-dev-v1 schedules differ from their tracked negative lock")

    dev1_external_lock_path = (
        args.v2_dev1_control_root / "locks/evaluation-root-lock.json"
    )
    dev1_external_lock = json.loads(dev1_external_lock_path.read_text(encoding="utf-8"))
    if (
        preserved_hashes["v2_dev1_design"]
        != _nested_sha(dev1_external_lock, "design_schedule_sha256")
        or preserved_hashes["v2_dev1_validation"]
        != _nested_sha(dev1_external_lock, "validation_schedule_sha256")
    ):
        raise ValueError("v2-dev.1 schedules differ from their external evaluation lock")
    dev1_public_gate = json.loads(
        (
            PROJECT_ROOT
            / "docs/review-evidence/rcaeval-re2-v2-dev1/provider-smoke-gate.json"
        ).read_text(encoding="utf-8")
    )
    if (
        preserved_hashes["v2_dev1_design"]
        != _nested_sha(dev1_public_gate, "source_bindings", "design_schedule_sha256")
        or _sha(dev1_external_lock_path)
        != _nested_sha(
            dev1_public_gate, "source_bindings", "evaluation_root_lock_sha256"
        )
    ):
        raise ValueError("v2-dev.1 public negative gate binding drift")
    old_run_ids = {
        run_id
        for path in preserved_paths.values()
        for run_id in extract_run_ids(path)
    }
    v1_schedule_lock = json.loads(
        (PROJECT_ROOT / "config/rcaeval-re2-v1/schedule-generation.json").read_text(
            encoding="utf-8"
        )
    )
    v1_external_schedule_sha256 = v1_schedule_lock.get("expected_schedule_sha256")
    if not isinstance(v1_external_schedule_sha256, str):
        raise ValueError("v1 external schedule public binding is invalid")
    lock = rehearse_schedule_admission(
        assignments=load_split_assignments(args.split_manifest),
        smoke_schedule=smoke,
        design_schedule=design,
        validation_schedule=validation,
        design_cases=discover_case_index(
            args.ob_root, args.ss_root, {record.identity for record in design}
        ),
        control_root=args.control_root,
        output_root=args.output_root,
        smoke_journal_root=args.smoke_journal_root,
        design_journal_root=args.design_journal_root,
        implementation_commit=evaluation.implementation_commit,
        split_lock_sha256=evaluation.split_lock_sha256,
        schedule_hashes={
            "smoke": _sha(schedules_root / "smoke-schedule.json"),
            "design": _sha(schedules_root / "design-schedule.json"),
            "validation": _sha(schedules_root / "dev-validation-schedule.json"),
            "set": _sha(schedules_root / "schedule-set-lock.json"),
        },
        old_run_ids=old_run_ids,
        v1_external_schedule_sha256=v1_external_schedule_sha256,
        preserved_schedule_hashes=preserved_hashes,
        preserved_roots={
            "v2_dev_v1": args.v2_dev_v1_root,
            "v2_dev1_control": args.v2_dev1_control_root,
            "v2_dev1_output": args.v2_dev1_output_root,
        },
        preserved_evidence_hashes={
            "v2_dev_v1_terminal_tree": tree_sha256(
                args.v2_dev_v1_root / "runs"
            ),
            "v2_dev1_terminal_tree": tree_sha256(args.v2_dev1_output_root),
        },
    )
    lock_sha = write_admission_lock(
        args.control_root / "locks" / ADMISSION_LOCK_NAME, lock
    )
    print(f"{lock.verdict} {lock_sha}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
