"""Create private outcomes and case-free v2-dev.2 DESIGN evidence."""

from __future__ import annotations

import argparse
from pathlib import Path

from ecomsre_rcaeval_v2.dev2_evaluation_root import verify_provider_ready
from ecomsre_rcaeval_v2.dev2_evidence import (
    assess_design,
    evidence_source_bindings,
    materialize_combined_design_journal,
    verify_passing_smoke_gate,
)
from ecomsre_rcaeval_v2.dev2_execution import (
    discover_case_index,
    load_locked_phase_schedule,
)
from ecomsre_rcaeval_v2.dev2_paths import preserved_evidence_roots
from ecomsre_rcaeval_v2.public_projection import (
    write_private_json_create_once,
    write_public_json_create_once,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]


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
    args = parser.parse_args(argv)
    preserved_roots = preserved_evidence_roots(
        args.v2_dev_v1_root,
        args.v2_dev1_control_root,
        args.v2_dev1_output_root,
    )
    verify_provider_ready(
        args.control_root,
        args.private_schedule_root,
        args.output_root,
        args.smoke_journal_root,
        args.design_journal_root,
        project_root=PROJECT_ROOT,
        preserved_roots=preserved_roots,
    )
    schedule = load_locked_phase_schedule(
        args.control_root,
        args.private_schedule_root,
        args.output_root,
        args.smoke_journal_root,
        args.design_journal_root,
        "design",
        preserved_roots=preserved_roots,
    )
    smoke_schedule = load_locked_phase_schedule(
        args.control_root,
        args.private_schedule_root,
        args.output_root,
        args.smoke_journal_root,
        args.design_journal_root,
        "smoke",
        preserved_roots=preserved_roots,
    )
    verify_passing_smoke_gate(
        args.control_root / "evidence/provider-smoke-gate.json",
        control_root=args.control_root,
        private_schedule_root=args.private_schedule_root,
        output_root=args.output_root,
        smoke_journal_root=args.smoke_journal_root,
        design_journal_root=args.design_journal_root,
        project_root=PROJECT_ROOT,
        smoke_schedule=smoke_schedule,
    )
    cases = discover_case_index(
        args.ob_root, args.ss_root, {record.identity for record in schedule}
    )
    combined_root = args.output_root / "evidence/combined-design-journal"
    combined_sha = materialize_combined_design_journal(
        smoke_journal_root=args.smoke_journal_root,
        design_journal_root=args.design_journal_root,
        combined_root=combined_root,
        smoke_schedule=smoke_schedule,
        design_schedule=schedule,
    )
    bindings = evidence_source_bindings(
        project_root=PROJECT_ROOT,
        control_root=args.control_root,
        private_schedule_root=args.private_schedule_root,
        output_root=args.output_root,
        smoke_journal_root=args.smoke_journal_root,
        design_journal_root=args.design_journal_root,
    )
    bindings["combined_design_journal_sha256"] = combined_sha
    outcomes, aggregate, gate, passed = assess_design(
        schedule,
        combined_root,
        cases=cases,
        source_bindings=bindings,
    )
    write_private_json_create_once(
        args.output_root / "evidence/design-outcomes.json",
        {
            "schema_version": "rcaeval-re2-v2-dev2.private-outcome-set.v1",
            "protocol_id": "rcaeval-re2-v2-dev.2",
            "outcomes": [
                {
                    **item.model_dump(mode="json"),
                    "schema_version": "rcaeval-re2-v2-dev2.private-run-outcome.v1",
                    "variant": item.variant.value.replace("_dev1", "_dev2"),
                }
                for item in outcomes
            ],
        },
    )
    write_public_json_create_once(
        args.control_root / "evidence/design-aggregate.json", aggregate
    )
    write_public_json_create_once(
        args.control_root / "evidence/design-gate.json", gate
    )
    print(gate["state"])
    return 0 if passed else 3


if __name__ == "__main__":
    raise SystemExit(main())
