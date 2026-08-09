"""Freeze private DESIGN, smoke, and DEV_VALIDATION schedules exactly once."""

from __future__ import annotations

import argparse
from pathlib import Path

from ecomsre_rcaeval_v2.dev_execution import (
    freeze_private_schedules,
    load_split_assignments,
)


def main(argv: tuple[str, ...] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--assignment-manifest", required=True, type=Path)
    parser.add_argument("--private-schedule-root", required=True, type=Path)
    args = parser.parse_args(argv)
    freeze_private_schedules(
        load_split_assignments(args.assignment_manifest),
        args.private_schedule_root,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
