from __future__ import annotations

import argparse
from pathlib import Path

from ecomsre_rcaeval.artifacts import (
    sha256_file,
    sha256_tree,
    write_json_create_once,
)
from ecomsre_rcaeval.execution import (
    load_terminal_records,
    validate_attempt_markers,
)
from ecomsre_rcaeval.freeze import (
    verify_ground_truth_binding,
    verify_source_bound_snapshot,
    verify_state_artifact,
)
from ecomsre_rcaeval.lifecycle import advance_state, current_state
from ecomsre_rcaeval.reporting import (
    build_final_report,
    load_ground_truth,
    verify_final_report,
)
from ecomsre_rcaeval.state import HoldoutState
from scripts.rcaeval.common import frozen_schedule


def main() -> int:
    parser = argparse.ArgumentParser(description="Unblind and score locked RCAEval terminal records")
    parser.add_argument("--control-root", type=Path, required=True)
    parser.add_argument("--evaluator-root", type=Path, required=True)
    parser.add_argument("--journal-root", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    state_journal = args.control_root / "state-journal"
    state = current_state(state_journal)
    if state not in {
        HoldoutState.TERMINAL_RECORDS_LOCKED,
        HoldoutState.UNBLINDED,
    }:
        raise ValueError("unblinding requires TERMINAL_RECORDS_LOCKED state")
    verify_source_bound_snapshot(args.control_root)
    terminal_lock = verify_state_artifact(
        args.control_root,
        HoldoutState.TERMINAL_RECORDS_LOCKED,
        "locks/terminal-records-lock.json",
    )
    schedule = frozen_schedule()
    records = load_terminal_records(schedule, args.journal_root)
    attempts_root = validate_attempt_markers(schedule, args.journal_root)
    if (
        terminal_lock.get("run_count") != len(schedule)
        or terminal_lock.get("terminal_records_sha256")
        != sha256_tree(args.journal_root, include_suffixes=(".json",))
        or terminal_lock.get("semantic_attempts_sha256")
        != sha256_tree(attempts_root, include_suffixes=(".json",))
    ):
        raise ValueError("locked terminal artifacts drifted before unblinding")
    truth_path = args.evaluator_root / "ground-truth.json"
    truth_sha = verify_ground_truth_binding(args.control_root, truth_path)
    truth = load_ground_truth(truth_path)
    if state is HoldoutState.TERMINAL_RECORDS_LOCKED:
        unblind_lock_sha = write_json_create_once(
            args.control_root / "locks" / "unblinding.json",
            {
                "schema_version": "rcaeval-re2.unblinding.v1",
                "terminal_lock_sha256": sha256_file(
                    args.control_root / "locks" / "terminal-records-lock.json"
                ),
                "ground_truth_sha256": truth_sha,
            },
        )
        advance_state(
            state_journal,
            HoldoutState.UNBLINDED,
            evidence_sha256=unblind_lock_sha,
        )
    unblind_lock = verify_state_artifact(
        args.control_root,
        HoldoutState.UNBLINDED,
        "locks/unblinding.json",
    )
    if unblind_lock.get("ground_truth_sha256") != sha256_file(truth_path):
        raise ValueError("evaluator Ground Truth drifted after unblinding")
    if args.report.exists():
        verify_final_report(args.report, records, truth)
        report_sha = sha256_file(args.report)
    else:
        report_sha = write_json_create_once(
            args.report, build_final_report(records, truth)
        )
    advance_state(
        state_journal,
        HoldoutState.FINAL_REPORT_FROZEN,
        evidence_sha256=report_sha,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
