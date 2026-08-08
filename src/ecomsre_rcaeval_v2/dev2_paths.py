"""Central fail-closed path boundary for every v2-dev.2 entry point."""

from __future__ import annotations

from pathlib import Path
from typing import Literal
import hashlib
import json

from ecomsre_rcaeval_v2.dev2_schedule import ArchitectureFamily, ScheduleRecord


_FORBIDDEN_MARKERS = (
    "re2-tt",
    "tt-case-",
    "holdout-schedule",
    "holdout-sanitized",
    "evaluator-only",
    "ground-truth",
    "terminal-journal",
    "terminal-record",
    "scored_cases",
    "/attribution/",
)


def reject_dev2_forbidden_paths(*paths: Path | None) -> None:
    for path in paths:
        if path is None:
            continue
        normalized = str(path).replace("\\", "/").casefold()
        if any(marker in normalized for marker in _FORBIDDEN_MARKERS):
            raise ValueError("dev2 path contains a forbidden TT/private marker")


def require_pairwise_disjoint(*paths: Path) -> tuple[Path, ...]:
    reject_dev2_forbidden_paths(*paths)
    resolved = tuple(path.resolve() for path in paths)
    for index, left in enumerate(resolved):
        for right in resolved[index + 1 :]:
            try:
                left.relative_to(right)
                nested = True
            except ValueError:
                try:
                    right.relative_to(left)
                    nested = True
                except ValueError:
                    nested = False
            if nested:
                raise ValueError("dev2 roots must be pairwise disjoint")
    return resolved


def preserved_evidence_roots(
    v2_dev_v1_root: Path,
    v2_dev1_control_root: Path,
    v2_dev1_output_root: Path,
) -> dict[str, Path]:
    reject_dev2_forbidden_paths(
        v2_dev_v1_root, v2_dev1_control_root, v2_dev1_output_root
    )
    return {
        "v2_dev_v1": v2_dev_v1_root,
        "v2_dev1_control": v2_dev1_control_root,
        "v2_dev1_output": v2_dev1_output_root,
    }


def terminal_path_for(record: ScheduleRecord, journal_root: Path) -> Path:
    reject_dev2_forbidden_paths(journal_root)
    if record.architecture_family is ArchitectureFamily.V1_REFERENCE:
        return journal_root / "v1-terminal-records" / f"{record.run_id}.json"
    return journal_root / "v2-runs" / record.run_id / "terminal-record.json"


def attempt_path_for(record: ScheduleRecord, journal_root: Path) -> Path:
    reject_dev2_forbidden_paths(journal_root)
    if record.architecture_family is ArchitectureFamily.V1_REFERENCE:
        return journal_root / "v1-terminal-records.attempts" / f"{record.run_id}.json"
    return journal_root / "v2-runs" / record.run_id / "run-attempt.json"


def journal_root_for(
    record: ScheduleRecord,
    *,
    phase: Literal["smoke", "design"],
    smoke_run_ids: set[str],
    smoke_journal_root: Path,
    design_journal_root: Path,
) -> Path:
    if phase == "smoke" or record.run_id in smoke_run_ids:
        return smoke_journal_root
    return design_journal_root


def tree_sha256(root: Path) -> str:
    if root.is_symlink() or not root.is_dir():
        raise ValueError("preserved evidence root is missing or invalid")
    entries = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ValueError("preserved evidence tree contains a symlink")
        if path.is_file():
            entries.append(
                {
                    "path": str(path.relative_to(root)),
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                }
            )
    return hashlib.sha256(
        json.dumps(entries, separators=(",", ":"), sort_keys=True).encode()
    ).hexdigest()
