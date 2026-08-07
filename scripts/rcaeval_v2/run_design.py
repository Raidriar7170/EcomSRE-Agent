"""Execute the frozen smoke subset or full DESIGN schedule without retries."""

from __future__ import annotations

import argparse
from pathlib import Path

from ecomsre_rcaeval_v2.dev_execution import (
    discover_case_index,
    execute_development_schedule,
    load_private_schedule,
    provider_config_from_env_file,
)


def main(argv: tuple[str, ...] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ob-root", required=True, type=Path)
    parser.add_argument("--ss-root", required=True, type=Path)
    parser.add_argument("--schedule", required=True, type=Path)
    parser.add_argument("--private-run-root", required=True, type=Path)
    parser.add_argument("--env-file", required=True, type=Path)
    args = parser.parse_args(argv)

    def progress(index, total, scheduled, record) -> None:
        print(
            f"[{index}/{total}] {scheduled.variant.value} "
            f"{record.terminal_status.value}",
            flush=True,
        )

    execute_development_schedule(
        load_private_schedule(args.schedule),
        cases=discover_case_index(args.ob_root, args.ss_root),
        provider_config=provider_config_from_env_file(args.env_file),
        private_run_root=args.private_run_root,
        progress=progress,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
