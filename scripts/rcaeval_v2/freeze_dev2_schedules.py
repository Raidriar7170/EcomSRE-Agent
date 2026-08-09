"""Freeze the private v2-dev.2 schedules from the inherited split manifest."""

from __future__ import annotations

import argparse
from pathlib import Path

from ecomsre_rcaeval_v2.dev2_execution import freeze_private_schedules
from ecomsre_rcaeval_v2.dev_execution import load_split_assignments
from ecomsre_rcaeval_v2.dev2_paths import (
    preserved_evidence_roots,
    reject_dev2_forbidden_paths,
    require_pairwise_disjoint,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def main(argv: tuple[str, ...] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--split-manifest", required=True, type=Path)
    parser.add_argument("--control-root", required=True, type=Path)
    parser.add_argument("--private-schedule-root", required=True, type=Path)
    parser.add_argument("--output-root", required=True, type=Path)
    parser.add_argument("--smoke-journal-root", required=True, type=Path)
    parser.add_argument("--design-journal-root", required=True, type=Path)
    parser.add_argument("--v2-dev-v1-root", required=True, type=Path)
    parser.add_argument("--v2-dev1-control-root", required=True, type=Path)
    parser.add_argument("--v2-dev1-output-root", required=True, type=Path)
    args = parser.parse_args(argv)
    reject_dev2_forbidden_paths(
        args.split_manifest,
        args.control_root,
        args.private_schedule_root,
        args.output_root,
        args.smoke_journal_root,
        args.design_journal_root,
        args.v2_dev_v1_root,
        args.v2_dev1_control_root,
        args.v2_dev1_output_root,
    )
    preserved_roots = preserved_evidence_roots(
        args.v2_dev_v1_root,
        args.v2_dev1_control_root,
        args.v2_dev1_output_root,
    )
    require_pairwise_disjoint(
        PROJECT_ROOT,
        args.control_root,
        args.private_schedule_root,
        args.output_root,
        args.smoke_journal_root,
        args.design_journal_root,
        *preserved_roots.values(),
    )
    result = freeze_private_schedules(
        load_split_assignments(args.split_manifest),
        args.private_schedule_root,
        control_root=args.control_root,
        output_root=args.output_root,
        smoke_journal_root=args.smoke_journal_root,
        design_journal_root=args.design_journal_root,
        preserved_roots=preserved_roots,
        project_root=PROJECT_ROOT,
    )
    print(result.model_dump_json())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
