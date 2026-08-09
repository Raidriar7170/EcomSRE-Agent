from __future__ import annotations

import argparse
from pathlib import Path

from ecomsre_rcaeval_v2.dev3_paths import preserved_evidence_roots


def add_preserved_root_arguments(parser: argparse.ArgumentParser) -> None:
    for option in (
        "v2-dev-v1-root",
        "v2-dev1-control-root",
        "v2-dev1-output-root",
        "v2-dev2-control-root",
        "v2-dev2-schedule-root",
        "v2-dev2-output-root",
        "v2-dev2-smoke-root",
        "v2-dev2-design-root",
    ):
        parser.add_argument(f"--{option}", required=True, type=Path)


def preserved_roots_from_args(args: argparse.Namespace) -> dict[str, Path]:
    return preserved_evidence_roots(
        args.v2_dev_v1_root,
        args.v2_dev1_control_root,
        args.v2_dev1_output_root,
        args.v2_dev2_control_root,
        args.v2_dev2_schedule_root,
        args.v2_dev2_output_root,
        args.v2_dev2_smoke_root,
        args.v2_dev2_design_root,
    )


__all__ = ["add_preserved_root_arguments", "preserved_roots_from_args"]
