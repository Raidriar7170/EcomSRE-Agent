"""Run the exact v2-dev.3 Smoke or DESIGN schedule."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Literal

from ecomsre_rcaeval_v2.dev3_execution import (
    discover_case_index,
    execute_development_schedule,
    load_locked_phase_schedule,
    provider_config_from_env_file,
)
from scripts.rcaeval_v2.dev3_cli import (
    add_preserved_root_arguments,
    preserved_roots_from_args,
)


def _main_for_phase(
    phase: Literal["smoke", "design"], argv: tuple[str, ...] | None = None
) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ob-root", required=True, type=Path)
    parser.add_argument("--ss-root", required=True, type=Path)
    parser.add_argument("--control-root", required=True, type=Path)
    parser.add_argument("--private-schedule-root", required=True, type=Path)
    parser.add_argument("--output-root", required=True, type=Path)
    parser.add_argument("--smoke-journal-root", required=True, type=Path)
    parser.add_argument("--design-journal-root", required=True, type=Path)
    add_preserved_root_arguments(parser)
    parser.add_argument("--env-file", required=True, type=Path)
    args = parser.parse_args(argv)
    preserved_roots = preserved_roots_from_args(args)
    schedule = load_locked_phase_schedule(
        args.control_root,
        args.private_schedule_root,
        args.output_root,
        args.smoke_journal_root,
        args.design_journal_root,
        phase,
        preserved_roots=preserved_roots,
    )

    def progress(index, total, scheduled, record) -> None:
        print(
            f"[{index}/{total}] {scheduled.variant.value} {record.terminal_status.value}",
            flush=True,
        )

    execute_development_schedule(
        schedule,
        cases=discover_case_index(
            args.ob_root, args.ss_root, {record.identity for record in schedule}
        ),
        provider_config=provider_config_from_env_file(args.env_file),
        control_root=args.control_root,
        private_schedule_root=args.private_schedule_root,
        output_root=args.output_root,
        smoke_journal_root=args.smoke_journal_root,
        design_journal_root=args.design_journal_root,
        execution_phase=phase,
        preserved_roots=preserved_roots,
        progress=progress,
    )
    return 0


def main(argv: tuple[str, ...] | None = None) -> int:
    return _main_for_phase("design", argv)


if __name__ == "__main__":
    raise SystemExit(main())
