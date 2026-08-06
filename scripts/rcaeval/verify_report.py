from __future__ import annotations

import argparse
from pathlib import Path

from ecomsre_rcaeval.artifacts import sha256_file, sha256_tree
from ecomsre_rcaeval.execution import (
    load_terminal_records,
    validate_attempt_markers,
)
from ecomsre_rcaeval.freeze import (
    verify_source_bound_snapshot,
    verify_state_artifact,
)
from ecomsre_rcaeval.lifecycle import (
    current_state,
    evidence_for_state,
)
from ecomsre_rcaeval.reporting import load_ground_truth, verify_final_report
from ecomsre_rcaeval.state import HoldoutState
from scripts.rcaeval.common import frozen_schedule


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify a frozen RCAEval final report")
    parser.add_argument("--control-root", type=Path, required=True)
    parser.add_argument("--evaluator-root", type=Path, required=True)
    parser.add_argument("--journal-root", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    if (
        current_state(args.control_root / "state-journal")
        is not HoldoutState.FINAL_REPORT_FROZEN
    ):
        raise ValueError("report verification requires FINAL_REPORT_FROZEN state")
    verify_source_bound_snapshot(args.control_root)
    terminal_lock = verify_state_artifact(
        args.control_root,
        HoldoutState.TERMINAL_RECORDS_LOCKED,
        "locks/terminal-records-lock.json",
    )
    unblind_lock = verify_state_artifact(
        args.control_root,
        HoldoutState.UNBLINDED,
        "locks/unblinding.json",
    )
    schedule = frozen_schedule()
    records = load_terminal_records(schedule, args.journal_root)
    attempts_root = validate_attempt_markers(schedule, args.journal_root)
    if (
        terminal_lock.get("terminal_records_sha256")
        != sha256_tree(args.journal_root, include_suffixes=(".json",))
        or terminal_lock.get("semantic_attempts_sha256")
        != sha256_tree(attempts_root, include_suffixes=(".json",))
    ):
        raise ValueError("locked terminal artifacts drifted before report verification")
    truth_path = args.evaluator_root / "ground-truth.json"
    if unblind_lock.get("ground_truth_sha256") != sha256_file(truth_path):
        raise ValueError("Ground Truth drifted before report verification")
    if evidence_for_state(
        args.control_root / "state-journal",
        HoldoutState.FINAL_REPORT_FROZEN,
    ) != sha256_file(args.report):
        raise ValueError("final report differs from state binding")
    truth = load_ground_truth(truth_path)
    verify_final_report(args.report, records, truth)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
