"""Assess the exact v2-dev.1 72-run Provider Smoke Gate."""

from __future__ import annotations

import argparse
from pathlib import Path

from ecomsre_rcaeval_v2.dev_execution import load_private_schedule
from ecomsre_rcaeval_v2.evaluation_root import verify_evaluation_root
from ecomsre_rcaeval_v2.evidence import (
    assess_smoke_gate,
    evidence_source_bindings,
)
from ecomsre_rcaeval_v2.public_projection import write_public_json_create_once
from ecomsre_rcaeval_v2.schedule import SplitName


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def main(argv: tuple[str, ...] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--control-root", required=True, type=Path)
    parser.add_argument("--output-root", required=True, type=Path)
    parser.add_argument("--schedule", required=True, type=Path)
    parser.add_argument("--public-output", required=True, type=Path)
    args = parser.parse_args(argv)
    verify_evaluation_root(
        args.control_root,
        args.output_root,
        project_root=PROJECT_ROOT,
    )
    schedule = load_private_schedule(
        args.schedule, allowed_split=SplitName.DESIGN
    )
    gate, passed = assess_smoke_gate(
        schedule,
        args.output_root,
        source_bindings=evidence_source_bindings(
            project_root=PROJECT_ROOT,
            control_root=args.control_root,
        ),
    )
    write_public_json_create_once(args.public_output, gate)
    print(gate["state"])
    return 0 if passed else 3


if __name__ == "__main__":
    raise SystemExit(main())
