"""Write the append-only DESIGN G3 completion gate without Provider access."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from ecomsre_rcaeval_v2.dev3_completion import (
    COMPLETION_AMENDMENT_LOCK_NAME,
    COMPLETION_GATE_NAME,
    load_completion_phase_schedules,
    verify_design_completion_amendment_ready,
)
from ecomsre_rcaeval_v2.dev3_evidence import (
    assess_design,
    evidence_source_bindings,
    materialize_combined_design_journal,
    verify_passing_smoke_gate,
)
from ecomsre_rcaeval_v2.dev3_execution import discover_case_index
from ecomsre_rcaeval_v2.dev3_postrun import POSTRUN_LOCK_NAME
from ecomsre_rcaeval_v2.public_projection import (
    assert_public_payload,
    write_public_json_create_once,
)
from scripts.rcaeval_v2.dev3_cli import (
    add_preserved_root_arguments,
    preserved_roots_from_args,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _load(path: Path) -> object:
    if path.is_symlink() or not path.is_file():
        raise ValueError("dev3 original DESIGN output is missing or invalid")
    return json.loads(path.read_text(encoding="utf-8"))


def main(argv: tuple[str, ...] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ob-root", required=True, type=Path)
    parser.add_argument("--ss-root", required=True, type=Path)
    parser.add_argument("--control-root", required=True, type=Path)
    parser.add_argument("--private-schedule-root", required=True, type=Path)
    parser.add_argument("--output-root", required=True, type=Path)
    parser.add_argument("--smoke-journal-root", required=True, type=Path)
    parser.add_argument("--design-journal-root", required=True, type=Path)
    add_preserved_root_arguments(parser)
    args = parser.parse_args(argv)
    _amendment, _postrun, parent, admission = (
        verify_design_completion_amendment_ready(
            args.control_root,
            args.private_schedule_root,
            args.output_root,
            args.smoke_journal_root,
            args.design_journal_root,
            project_root=PROJECT_ROOT,
            preserved_roots=preserved_roots_from_args(args),
        )
    )
    smoke_schedule, design_schedule = load_completion_phase_schedules(
        args.private_schedule_root,
        parent=parent,
        admission=admission,
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
    combined_root = args.output_root / "evidence/combined-design-journal"
    combined_sha = materialize_combined_design_journal(
        smoke_journal_root=args.smoke_journal_root,
        design_journal_root=args.design_journal_root,
        combined_root=combined_root,
        smoke_schedule=smoke_schedule,
        design_schedule=design_schedule,
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
    bindings["postrun_evaluation_lock_sha256"] = hashlib.sha256(
        (args.control_root / "locks" / POSTRUN_LOCK_NAME).read_bytes()
    ).hexdigest()
    cases = discover_case_index(
        args.ob_root,
        args.ss_root,
        {record.identity for record in design_schedule},
    )
    outcomes, aggregate, gate, passed = assess_design(
        design_schedule,
        combined_root,
        cases=cases,
        source_bindings=bindings,
    )
    expected_private = {
        "schema_version": "rcaeval-re2-v2-dev3.private-outcome-set.v1",
        "protocol_id": "rcaeval-re2-v2-dev.3",
        "outcomes": [
            {
                **item.model_dump(mode="json"),
                "schema_version": (
                    "rcaeval-re2-v2-dev3.private-run-outcome.v1"
                ),
                "variant": item.variant.value.replace("_dev1", "_dev3"),
            }
            for item in outcomes
        ],
    }
    if (
        aggregate != _load(args.control_root / "evidence/design-aggregate.json")
        or expected_private
        != _load(args.output_root / "evidence/design-outcomes.json")
    ):
        raise ValueError("dev3 DESIGN completion recomputation drift")
    if not passed:
        raise ValueError("dev3 DESIGN G3 completion gate did not pass")
    source_bindings = gate.get("source_bindings")
    if not isinstance(source_bindings, dict):
        raise ValueError("dev3 DESIGN completion gate binding is invalid")
    source_bindings["design_completion_amendment_lock_sha256"] = hashlib.sha256(
        (
            args.control_root
            / "locks"
            / COMPLETION_AMENDMENT_LOCK_NAME
        ).read_bytes()
    ).hexdigest()
    assert_public_payload(gate)
    write_public_json_create_once(
        args.control_root / "evidence" / COMPLETION_GATE_NAME,
        gate,
    )
    print(gate["state"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
