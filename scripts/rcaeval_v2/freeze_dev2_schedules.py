"""Freeze the private v2-dev.2 schedules from the inherited split manifest."""

from __future__ import annotations

import argparse
from pathlib import Path

from ecomsre_rcaeval_v2.dev2_execution import freeze_private_schedules
from ecomsre_rcaeval_v2.dev_execution import load_split_assignments
from ecomsre_rcaeval_v2.dev2_paths import reject_dev2_forbidden_paths


def main(argv: tuple[str, ...] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--split-manifest", required=True, type=Path)
    parser.add_argument("--private-schedule-root", required=True, type=Path)
    args = parser.parse_args(argv)
    reject_dev2_forbidden_paths(args.split_manifest, args.private_schedule_root)
    result = freeze_private_schedules(
        load_split_assignments(args.split_manifest), args.private_schedule_root
    )
    print(result.model_dump_json())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
