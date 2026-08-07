"""Reverify inherited F0 on the 60-case DESIGN set without evaluating F1/F2."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from ecomsre_rcaeval_v2.dev_execution import (
    discover_case_index,
    load_private_schedule,
)
from ecomsre_rcaeval_v2.indicator import FormulaId, load_indicator_config
from ecomsre_rcaeval_v2.indicator_evaluation import evaluate_frozen_formula
from ecomsre_rcaeval_v2.locks import LEGACY_V2_CONFIG, V2_CONFIG
from ecomsre_rcaeval_v2.public_projection import (
    write_private_json_create_once,
    write_public_json_create_once,
)
from ecomsre_rcaeval_v2.schedule import SplitName


def run_reverification(
    *,
    ob_root: Path,
    ss_root: Path,
    schedule_path: Path,
    private_output: Path,
    public_output: Path,
) -> bool:
    schedule = load_private_schedule(
        schedule_path, allowed_split=SplitName.DESIGN
    )
    identities = {item.identity for item in schedule}
    if len(schedule) != 360 or len(identities) != 60:
        raise ValueError("F0 reverification requires the exact DESIGN schedule")
    cases = discover_case_index(ob_root, ss_root, identities)
    indicator_lock = json.loads(
        (V2_CONFIG / "indicator-lock.json").read_text(encoding="utf-8")
    )
    expected_sha = indicator_lock.get("inherited_formula_config_sha256")
    if (
        indicator_lock.get("selected_formula") != "F0"
        or indicator_lock.get("formula_reselection_performed") is not False
        or not isinstance(expected_sha, str)
    ):
        raise ValueError("inherited F0 lock is invalid")
    config = load_indicator_config(
        LEGACY_V2_CONFIG / "indicator-candidate-formulas.json",
        expected_sha256=expected_sha,
    )
    outcomes, evaluation = evaluate_frozen_formula(
        tuple(cases[identity] for identity in sorted(
            identities,
            key=lambda item: (
                item.system,
                item.root_cause_service,
                item.fault,
                item.instance,
            ),
        )),
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
        "schema_version": "rcaeval-re2-v2-dev1.f0-reverification-private.v1",
        "protocol_id": "rcaeval-re2-v2-dev.1",
        "formula": "F0",
        "formula_reselection_performed": False,
        "provider_calls": 0,
        "dev_validation_values_accessed": False,
        "case_outcomes": [item.model_dump(mode="json") for item in outcomes],
        "evaluation": evaluation.model_dump(mode="json"),
        "state": (
            "INHERITED_F0_REVERIFIED"
            if passed
            else "BLOCKED_INHERITED_F0_REVERIFICATION_DRIFT"
        ),
    }
    private_sha = write_private_json_create_once(private_output, private_payload)
    public_payload = {
        "schema_version": "rcaeval-re2-v2-dev1.f0-reverification-public.v1",
        "protocol_id": "rcaeval-re2-v2-dev.1",
        "classification": [
            "DEVELOPMENT_VISIBLE",
            "DESIGN_SET",
            "NOT_EXTERNAL_HOLDOUT",
            "NOT_PRIMARY_INFERENCE",
        ],
        "formula": "F0",
        "formula_reselection_performed": False,
        "provider_calls": 0,
        "dev_validation_values_accessed": False,
        "overall_coverage_at_6": evaluation.overall_coverage_at_6.model_dump(
            mode="json"
        ),
        "memory_coverage_at_6": evaluation.memory_coverage_at_6.model_dump(
            mode="json"
        ),
        "socket_coverage_at_6": evaluation.socket_coverage_at_6.model_dump(
            mode="json"
        ),
        "private_evidence_sha256": private_sha,
        "state": private_payload["state"],
    }
    write_public_json_create_once(public_output, public_payload)
    return passed


def main(argv: tuple[str, ...] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ob-root", required=True, type=Path)
    parser.add_argument("--ss-root", required=True, type=Path)
    parser.add_argument("--schedule", required=True, type=Path)
    parser.add_argument("--private-output", required=True, type=Path)
    parser.add_argument("--public-output", required=True, type=Path)
    args = parser.parse_args(argv)
    return 0 if run_reverification(
        ob_root=args.ob_root,
        ss_root=args.ss_root,
        schedule_path=args.schedule,
        private_output=args.private_output,
        public_output=args.public_output,
    ) else 3


if __name__ == "__main__":
    raise SystemExit(main())
