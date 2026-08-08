"""Create the no-Provider, append-only dev.3 DESIGN completion amendment."""

from __future__ import annotations

import argparse
from pathlib import Path

from ecomsre_rcaeval_v2.dev3_completion import (
    prepare_design_completion_amendment,
)
from scripts.rcaeval_v2.dev3_cli import (
    add_preserved_root_arguments,
    preserved_roots_from_args,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def main(argv: tuple[str, ...] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--control-root", required=True, type=Path)
    parser.add_argument("--private-schedule-root", required=True, type=Path)
    parser.add_argument("--output-root", required=True, type=Path)
    parser.add_argument("--smoke-journal-root", required=True, type=Path)
    parser.add_argument("--design-journal-root", required=True, type=Path)
    add_preserved_root_arguments(parser)
    args = parser.parse_args(argv)
    lock = prepare_design_completion_amendment(
        args.control_root,
        args.private_schedule_root,
        args.output_root,
        args.smoke_journal_root,
        args.design_journal_root,
        project_root=PROJECT_ROOT,
        preserved_roots=preserved_roots_from_args(args),
    )
    print(lock.model_dump_json())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
