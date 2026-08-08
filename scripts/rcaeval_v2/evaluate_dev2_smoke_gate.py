"""Assess the exact v2-dev.2 72-run Provider Smoke Gate."""

from __future__ import annotations

import argparse
from pathlib import Path

from ecomsre_rcaeval_v2.dev2_evaluation_root import verify_provider_ready
from ecomsre_rcaeval_v2.dev2_evidence import assess_smoke_gate, evidence_source_bindings
from ecomsre_rcaeval_v2.dev2_execution import load_locked_phase_schedule
from ecomsre_rcaeval_v2.dev2_paths import preserved_evidence_roots
from ecomsre_rcaeval_v2.public_projection import write_public_json_create_once


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def main(argv: tuple[str, ...] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--control-root", required=True, type=Path)
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
        args.output_root,
        args.smoke_journal_root,
        args.design_journal_root,
        project_root=PROJECT_ROOT,
        preserved_roots=preserved_roots,
    )
    schedule = load_locked_phase_schedule(
        args.control_root,
        args.output_root,
        args.smoke_journal_root,
        args.design_journal_root,
        "smoke",
        preserved_roots=preserved_roots,
    )
    gate, passed = assess_smoke_gate(
        schedule,
        args.smoke_journal_root,
        source_bindings=evidence_source_bindings(
            project_root=PROJECT_ROOT,
            control_root=args.control_root,
            output_root=args.output_root,
            smoke_journal_root=args.smoke_journal_root,
            design_journal_root=args.design_journal_root,
        ),
    )
    write_public_json_create_once(
        args.control_root / "evidence/provider-smoke-gate.json", gate
    )
    print(gate["state"])
    return 0 if passed else 3


if __name__ == "__main__":
    raise SystemExit(main())
