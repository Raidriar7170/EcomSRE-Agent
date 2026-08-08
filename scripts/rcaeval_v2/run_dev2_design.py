"""Run the exact v2-dev.2 Smoke or DESIGN schedule."""

from __future__ import annotations

import argparse
from pathlib import Path

from ecomsre_rcaeval_v2.dev2_execution import (
    discover_case_index,
    execute_development_schedule,
    load_locked_phase_schedule,
    provider_config_from_env_file,
)
from ecomsre_rcaeval_v2.dev2_paths import preserved_evidence_roots


def main(argv: tuple[str, ...] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ob-root", required=True, type=Path)
    parser.add_argument("--ss-root", required=True, type=Path)
    parser.add_argument("--control-root", required=True, type=Path)
    parser.add_argument("--private-schedule-root", required=True, type=Path)
    parser.add_argument("--output-root", required=True, type=Path)
    parser.add_argument("--smoke-journal-root", required=True, type=Path)
    parser.add_argument("--design-journal-root", required=True, type=Path)
    parser.add_argument("--v2-dev-v1-root", required=True, type=Path)
    parser.add_argument("--v2-dev1-control-root", required=True, type=Path)
    parser.add_argument("--v2-dev1-output-root", required=True, type=Path)
    parser.add_argument("--env-file", required=True, type=Path)
    parser.add_argument("--phase", required=True, choices=("smoke", "design"))
    args = parser.parse_args(argv)
    preserved_roots = preserved_evidence_roots(
        args.v2_dev_v1_root,
        args.v2_dev1_control_root,
        args.v2_dev1_output_root,
    )
    schedule = load_locked_phase_schedule(
        args.control_root,
        args.private_schedule_root,
        args.output_root,
        args.smoke_journal_root,
        args.design_journal_root,
        args.phase,
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
        execution_phase=args.phase,
        preserved_roots=preserved_roots,
        progress=progress,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
