"""Build the private assignment manifest and public split lock exactly once."""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
from typing import cast

from ecomsre_rcaeval.dataset import DevSystem, discover_dev_cases
from ecomsre_rcaeval_v2.schedule import (
    CaseIdentity,
    SPLIT_SEED,
    build_split_assignments,
    write_split_artifacts,
)
from ecomsre_rcaeval_v2.contracts import DevSystem as DevSystemName


_FORBIDDEN_MARKERS = (
    "re2-tt",
    "tt-case-",
    "holdout-sanitized",
    "evaluator-only",
    "terminal-journal",
    "ground-truth.json",
    "scored_cases",
    "/attribution/",
)
_REPOSITORY_ROOT = Path(__file__).parents[2]


def _reject_forbidden_paths(*paths: Path) -> None:
    if any(
        marker in str(path).casefold()
        for path in paths
        for marker in _FORBIDDEN_MARKERS
    ):
        raise ValueError("split builder path contains a forbidden TT/private marker")


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_split(
    *,
    ob_root: Path,
    ss_root: Path,
    private_root: Path,
    split_lock_output: Path,
) -> None:
    _reject_forbidden_paths(ob_root, ss_root, private_root, split_lock_output)
    ob_cases = discover_dev_cases(ob_root, DevSystem.RE2_OB)
    ss_cases = discover_dev_cases(ss_root, DevSystem.RE2_SS)
    identities = tuple(
        CaseIdentity(
            system=cast(DevSystemName, case.system),
            root_cause_service=case.root_cause_service,
            fault=case.fault,
            instance=case.instance,
        )
        for case in ob_cases + ss_cases
    )
    assignments = build_split_assignments(identities, seed=SPLIT_SEED)
    config_root = _REPOSITORY_ROOT / "config" / "rcaeval-re2-v2-dev"
    write_split_artifacts(
        assignments,
        private_root=private_root,
        split_lock_output=split_lock_output,
        protocol_sha256=_sha256_file(config_root / "protocol.json"),
        dataset_lock_sha256=_sha256_file(config_root / "dataset-lock.json"),
        seed=SPLIT_SEED,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Create the RCAEval RE2 v2 deterministic development split."
    )
    parser.add_argument("--ob-root", required=True, type=Path)
    parser.add_argument("--ss-root", required=True, type=Path)
    parser.add_argument("--private-root", required=True, type=Path)
    parser.add_argument("--split-lock-output", required=True, type=Path)
    return parser


def main(argv: tuple[str, ...] | None = None) -> int:
    args = _parser().parse_args(argv)
    build_split(
        ob_root=args.ob_root,
        ss_root=args.ss_root,
        private_root=args.private_root,
        split_lock_output=args.split_lock_output,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
