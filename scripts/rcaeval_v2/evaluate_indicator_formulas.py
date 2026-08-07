"""Run the frozen DESIGN-only F0/F1/F2 comparison with no Provider calls."""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

from ecomsre_rcaeval.dataset import DevSystem, discover_dev_cases
from ecomsre_rcaeval_v2.indicator import load_indicator_config
from ecomsre_rcaeval_v2.indicator_evaluation import evaluate_design_formulas
from ecomsre_rcaeval_v2.public_projection import (
    write_private_json_create_once,
    write_public_json_create_once,
)
from ecomsre_rcaeval_v2.schedule import (
    PublicSplitLock,
    SplitAssignmentManifest,
    SplitName,
)


_FORBIDDEN_MARKERS = ("re2-tt", "tt-case-", "holdout-sanitized", "evaluator-only")


def _reject_tt_paths(*paths: Path) -> None:
    if any(
        marker in str(path).casefold()
        for path in paths
        for marker in _FORBIDDEN_MARKERS
    ):
        raise ValueError("formula evaluation path contains a forbidden TT marker")


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _read_locked_split(
    *,
    split_lock_path: Path,
    expected_split_lock_sha256: str,
    assignment_manifest_path: Path,
) -> tuple[PublicSplitLock, SplitAssignmentManifest, str]:
    _reject_tt_paths(split_lock_path, assignment_manifest_path)
    if split_lock_path.is_symlink() or not split_lock_path.is_file():
        raise ValueError("split lock must be a regular file")
    split_payload = split_lock_path.read_bytes()
    if _sha256_bytes(split_payload) != expected_split_lock_sha256:
        raise ValueError("split lock hash mismatch")
    split_lock = PublicSplitLock.model_validate_json(split_payload)
    if assignment_manifest_path.is_symlink() or not assignment_manifest_path.is_file():
        raise ValueError("private split manifest must be a regular file")
    manifest_payload = assignment_manifest_path.read_bytes()
    manifest_sha256 = _sha256_bytes(manifest_payload)
    if manifest_sha256 != split_lock.assignment_manifest_sha256:
        raise ValueError("private split manifest hash mismatch")
    manifest = SplitAssignmentManifest.model_validate_json(manifest_payload)
    if len(manifest.assignments) != 180:
        raise ValueError("private split manifest requires 180 assignments")
    return split_lock, manifest, manifest_sha256


def run_formula_evaluation(
    *,
    ob_root: Path,
    ss_root: Path,
    split_lock_path: Path,
    expected_split_lock_sha256: str,
    assignment_manifest_path: Path,
    formula_config_path: Path,
    expected_formula_config_sha256: str,
    private_output: Path,
    public_output: Path,
) -> bool:
    _reject_tt_paths(
        ob_root,
        ss_root,
        split_lock_path,
        assignment_manifest_path,
        formula_config_path,
        private_output,
        public_output,
    )
    split_lock, manifest, manifest_sha256 = _read_locked_split(
        split_lock_path=split_lock_path,
        expected_split_lock_sha256=expected_split_lock_sha256,
        assignment_manifest_path=assignment_manifest_path,
    )
    config = load_indicator_config(
        formula_config_path,
        expected_sha256=expected_formula_config_sha256,
    )
    ob_cases = discover_dev_cases(ob_root, DevSystem.RE2_OB)
    ss_cases = discover_dev_cases(ss_root, DevSystem.RE2_SS)
    cases = ob_cases + ss_cases
    if len(ob_cases) != 90 or len(ss_cases) != 90 or len(cases) != 180:
        raise ValueError("formula evaluation requires 180 locked development cases")
    design_count = sum(
        item.split is SplitName.DESIGN for item in manifest.assignments
    )
    validation_count = sum(
        item.split is SplitName.DEV_VALIDATION for item in manifest.assignments
    )
    if design_count != 60 or validation_count != 120:
        raise ValueError("formula evaluation split counts differ from protocol")
    outcomes, evaluations, selection = evaluate_design_formulas(
        cases,
        manifest.assignments,
        config,
    )
    if len(outcomes) != 180:
        raise ValueError("DESIGN formula evaluation requires 60 cases by 3 formulas")
    state = (
        "V2_INDICATOR_TOOL_GATE_PASSED"
        if selection.gate_passed
        else "V2_INDICATOR_TOOL_GATE_NOT_PASSED"
    )
    source_bindings = {
        "protocol_id": split_lock.protocol_id,
        "protocol_sha256": split_lock.protocol_sha256,
        "dataset_lock_sha256": split_lock.dataset_lock_sha256,
        "split_lock_sha256": expected_split_lock_sha256,
        "assignment_manifest_sha256": manifest_sha256,
        "formula_config_sha256": config.sha256,
    }
    private_payload = {
        "schema_version": "rcaeval-re2-v2-dev.formula-tool-gate-private.v1",
        "classification": [
            "PRIVATE_DEVELOPMENT_ARTIFACT",
            "NOT_EXTERNAL_HOLDOUT",
            "NOT_PRIMARY_INFERENCE",
        ],
        "source_bindings": source_bindings,
        "design_cases": design_count,
        "dev_validation_values_accessed": False,
        "provider_calls": 0,
        "formula_case_outcomes": [
            item.model_dump(mode="json") for item in outcomes
        ],
        "formula_evaluations": [
            item.model_dump(mode="json") for item in evaluations
        ],
        "selection": selection.model_dump(mode="json"),
        "state": state,
    }
    private_sha256 = write_private_json_create_once(
        private_output, private_payload
    )
    public_payload = {
        "schema_version": "rcaeval-re2-v2-dev.formula-tool-gate-public.v1",
        "classification": [
            "DEVELOPMENT_VISIBLE",
            "NOT_EXTERNAL_HOLDOUT",
            "NOT_PRIMARY_INFERENCE",
        ],
        "source_bindings": source_bindings,
        "design_cases": design_count,
        "dev_validation_cases_reserved": validation_count,
        "dev_validation_values_accessed": False,
        "provider_calls": 0,
        "formula_candidates": [
            item.model_dump(mode="json") for item in evaluations
        ],
        "selection": selection.model_dump(mode="json"),
        "private_evidence_sha256": private_sha256,
        "state": state,
    }
    write_public_json_create_once(public_output, public_payload)
    return selection.gate_passed


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Evaluate frozen F0/F1/F2 formulas on the DESIGN split only."
    )
    parser.add_argument("--ob-root", required=True, type=Path)
    parser.add_argument("--ss-root", required=True, type=Path)
    parser.add_argument("--split-lock", required=True, type=Path)
    parser.add_argument("--split-lock-sha256", required=True)
    parser.add_argument("--assignment-manifest", required=True, type=Path)
    parser.add_argument("--formula-config", required=True, type=Path)
    parser.add_argument("--formula-config-sha256", required=True)
    parser.add_argument("--private-output", required=True, type=Path)
    parser.add_argument("--public-output", required=True, type=Path)
    return parser


def main(argv: tuple[str, ...] | None = None) -> int:
    args = _parser().parse_args(argv)
    gate_passed = run_formula_evaluation(
        ob_root=args.ob_root,
        ss_root=args.ss_root,
        split_lock_path=args.split_lock,
        expected_split_lock_sha256=args.split_lock_sha256,
        assignment_manifest_path=args.assignment_manifest,
        formula_config_path=args.formula_config,
        expected_formula_config_sha256=args.formula_config_sha256,
        private_output=args.private_output,
        public_output=args.public_output,
    )
    return 0 if gate_passed else 3


if __name__ == "__main__":
    raise SystemExit(main())
