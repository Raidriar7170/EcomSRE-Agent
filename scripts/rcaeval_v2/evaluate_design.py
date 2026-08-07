"""Create private DESIGN outcomes and case-free v2-dev.1 public evidence."""

from __future__ import annotations

import argparse
from pathlib import Path

from ecomsre_rcaeval_v2.dev_execution import (
    discover_case_index,
    load_private_schedule,
)
from ecomsre_rcaeval_v2.evaluation_root import verify_evaluation_root
from ecomsre_rcaeval_v2.evidence import (
    assess_design,
    evidence_source_bindings,
)
from ecomsre_rcaeval_v2.public_projection import (
    write_private_json_create_once,
    write_public_json_create_once,
)
from ecomsre_rcaeval_v2.schedule import SplitName


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def main(argv: tuple[str, ...] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ob-root", required=True, type=Path)
    parser.add_argument("--ss-root", required=True, type=Path)
    parser.add_argument("--control-root", required=True, type=Path)
    parser.add_argument("--output-root", required=True, type=Path)
    parser.add_argument("--schedule", required=True, type=Path)
    parser.add_argument("--private-outcomes", required=True, type=Path)
    parser.add_argument("--public-aggregate", required=True, type=Path)
    parser.add_argument("--design-gate", required=True, type=Path)
    args = parser.parse_args(argv)
    verify_evaluation_root(
        args.control_root,
        args.output_root,
        project_root=PROJECT_ROOT,
    )
    schedule = load_private_schedule(
        args.schedule, allowed_split=SplitName.DESIGN
    )
    cases = discover_case_index(
        args.ob_root,
        args.ss_root,
        {item.identity for item in schedule},
    )
    outcomes, aggregate, gate, passed = assess_design(
        schedule,
        args.output_root,
        cases=cases,
        source_bindings=evidence_source_bindings(
            project_root=PROJECT_ROOT,
            control_root=args.control_root,
        ),
    )
    write_private_json_create_once(
        args.private_outcomes,
        {
            "schema_version": "rcaeval-re2-v2-dev1.private-outcome-set.v1",
            "protocol_id": "rcaeval-re2-v2-dev.1",
            "outcomes": [item.model_dump(mode="json") for item in outcomes],
        },
    )
    write_public_json_create_once(args.public_aggregate, aggregate)
    write_public_json_create_once(args.design_gate, gate)
    print(gate["state"])
    return 0 if passed else 3


if __name__ == "__main__":
    raise SystemExit(main())
