"""Deterministically reverify inherited F0 for v2-dev.2 without Provider calls."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Mapping

from ecomsre_rcaeval_v2.dev2_execution import (
    discover_case_index,
    load_locked_phase_schedule,
)
from ecomsre_rcaeval_v2.indicator import FormulaId, load_indicator_config
from ecomsre_rcaeval_v2.dev2_paths import preserved_evidence_roots
from ecomsre_rcaeval_v2.indicator_evaluation import evaluate_frozen_formula
from ecomsre_rcaeval_v2.locks import LEGACY_V2_CONFIG, PROJECT_ROOT
from ecomsre_rcaeval_v2.public_projection import (
    write_private_json_create_once,
    write_public_json_create_once,
)


DEV2_CONFIG = PROJECT_ROOT / "config" / "rcaeval-re2-v2-dev2"


def run_reverification(
    *,
    ob_root: Path,
    ss_root: Path,
    control_root: Path,
    output_root: Path,
    smoke_journal_root: Path,
    design_journal_root: Path,
    private_output: Path,
    public_output: Path,
    preserved_roots: Mapping[str, Path],
) -> bool:
    schedule = load_locked_phase_schedule(
        control_root,
        output_root,
        smoke_journal_root,
        design_journal_root,
        "design",
        preserved_roots=preserved_roots,
    )
    identities = {record.identity for record in schedule}
    if len(schedule) != 360 or len(identities) != 60:
        raise ValueError("dev2 F0 reverification requires exact DESIGN schedule")
    cases = discover_case_index(ob_root, ss_root, identities)
    lock = json.loads((DEV2_CONFIG / "indicator-lock.json").read_text(encoding="utf-8"))
    expected_sha = lock.get("inherited_formula_config_sha256")
    if lock.get("selected_formula") != "F0" or lock.get("formula_reselection_performed") is not False or not isinstance(expected_sha, str):
        raise ValueError("dev2 inherited F0 lock is invalid")
    config = load_indicator_config(
        LEGACY_V2_CONFIG / "indicator-candidate-formulas.json",
        expected_sha256=expected_sha,
    )
    outcomes, evaluation = evaluate_frozen_formula(
        tuple(
            cases[identity]
            for identity in sorted(
                identities,
                key=lambda item: (
                    item.system,
                    item.root_cause_service,
                    item.fault,
                    item.instance,
                ),
            )
        ),
        FormulaId.F0,
        config,
    )
    passed = (
        evaluation.overall_coverage_at_6.numerator == 57
        and evaluation.overall_coverage_at_6.denominator == 60
        and evaluation.memory_coverage_at_6.numerator == 10
        and evaluation.memory_coverage_at_6.denominator == 10
        and evaluation.socket_coverage_at_6.numerator == 9
        and evaluation.socket_coverage_at_6.denominator == 10
    )
    private_payload = {
        "schema_version": "rcaeval-re2-v2-dev2.f0-reverification-private.v1",
        "protocol_id": "rcaeval-re2-v2-dev.2",
        "formula": "F0",
        "formula_reselection_performed": False,
        "provider_calls": 0,
        "dev_validation_values_accessed": False,
        "case_outcomes": [item.model_dump(mode="json") for item in outcomes],
        "evaluation": evaluation.model_dump(mode="json"),
        "state": "INHERITED_F0_REVERIFIED" if passed else "BLOCKED_INHERITED_F0_REVERIFICATION_DRIFT",
    }
    private_sha = write_private_json_create_once(private_output, private_payload)
    write_public_json_create_once(
        public_output,
        {
            "schema_version": "rcaeval-re2-v2-dev2.f0-reverification-public.v1",
            "protocol_id": "rcaeval-re2-v2-dev.2",
            "classification": ["DEVELOPMENT_VISIBLE", "DESIGN_SET", "NOT_EXTERNAL_HOLDOUT", "NOT_PRIMARY_INFERENCE"],
            "formula": "F0",
            "formula_reselection_performed": False,
            "provider_calls": 0,
            "dev_validation_values_accessed": False,
            "overall_coverage_at_6": evaluation.overall_coverage_at_6.model_dump(mode="json"),
            "memory_coverage_at_6": evaluation.memory_coverage_at_6.model_dump(mode="json"),
            "socket_coverage_at_6": evaluation.socket_coverage_at_6.model_dump(mode="json"),
            "private_evidence_sha256": private_sha,
            "state": private_payload["state"],
        },
    )
    return passed


def main(argv: tuple[str, ...] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ob-root", required=True, type=Path)
    parser.add_argument("--ss-root", required=True, type=Path)
    parser.add_argument("--control-root", required=True, type=Path)
    parser.add_argument("--output-root", required=True, type=Path)
    parser.add_argument("--smoke-journal-root", required=True, type=Path)
    parser.add_argument("--design-journal-root", required=True, type=Path)
    parser.add_argument("--v2-dev-v1-root", required=True, type=Path)
    parser.add_argument("--v2-dev1-control-root", required=True, type=Path)
    parser.add_argument("--v2-dev1-output-root", required=True, type=Path)
    args = parser.parse_args(argv)
    return 0 if run_reverification(
        ob_root=args.ob_root,
        ss_root=args.ss_root,
        control_root=args.control_root,
        output_root=args.output_root,
        smoke_journal_root=args.smoke_journal_root,
        design_journal_root=args.design_journal_root,
        private_output=args.output_root / "evidence/f0-private.json",
        public_output=args.control_root / "evidence/f0-public.json",
        preserved_roots=preserved_evidence_roots(
            args.v2_dev_v1_root,
            args.v2_dev1_control_root,
            args.v2_dev1_output_root,
        ),
    ) else 3


if __name__ == "__main__":
    raise SystemExit(main())
