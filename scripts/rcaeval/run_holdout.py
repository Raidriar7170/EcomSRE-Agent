from __future__ import annotations

import argparse
from pathlib import Path

from ecomsre_rcaeval.artifacts import (
    sha256_file,
    sha256_tree,
    write_json_create_once,
)
from ecomsre_rcaeval.dataset import load_sanitized_cases
from ecomsre_rcaeval.execution import (
    load_terminal_records,
    run_schedule,
    validate_attempt_markers,
)
from ecomsre_rcaeval.freeze import (
    verify_source_bound_snapshot,
    verify_state_artifact,
)
from ecomsre_rcaeval.lifecycle import advance_state, current_state
from ecomsre_rcaeval.state import HoldoutState
from scripts.rcaeval.common import frozen_schedule, provider_from_lock


def main() -> int:
    parser = argparse.ArgumentParser(description="Execute the frozen opaque RCAEval holdout")
    parser.add_argument("--control-root", type=Path, required=True)
    parser.add_argument("--sanitized-root", type=Path, required=True)
    parser.add_argument("--journal-root", type=Path, required=True)
    args = parser.parse_args()
    state_journal = args.control_root / "state-journal"
    state = current_state(state_journal)
    if state not in {
        HoldoutState.HOLDOUT_PREFLIGHT_PASSED,
        HoldoutState.HOLDOUT_EXECUTED,
    }:
        raise ValueError("holdout execution requires HOLDOUT_PREFLIGHT_PASSED state")
    verify_source_bound_snapshot(args.control_root)
    preflight = verify_state_artifact(
        args.control_root,
        HoldoutState.HOLDOUT_PREFLIGHT_PASSED,
        "locks/holdout-preflight.json",
    )
    if preflight.get("sanitized_manifest_sha256") != sha256_file(
        args.sanitized_root / "manifest.json"
    ):
        raise ValueError("sanitized holdout drifted after preflight")
    schedule = frozen_schedule()
    if state is HoldoutState.HOLDOUT_PREFLIGHT_PASSED:
        cases = load_sanitized_cases(args.sanitized_root)
        run_schedule(
            cases,
            schedule,
            lambda _scheduled: provider_from_lock(),
            args.journal_root,
        )
        load_terminal_records(schedule, args.journal_root)
        attempts_root = validate_attempt_markers(schedule, args.journal_root)
        terminal_sha = sha256_tree(
            args.journal_root, include_suffixes=(".json",)
        )
        attempt_sha = sha256_tree(attempts_root, include_suffixes=(".json",))
        execution_sha = write_json_create_once(
            args.control_root / "locks" / "holdout-execution.json",
            {
                "schema_version": "rcaeval-re2.holdout-execution.v1",
                "run_count": len(schedule),
                "terminal_records_sha256": terminal_sha,
                "semantic_attempts_sha256": attempt_sha,
            },
        )
        advance_state(
            state_journal,
            HoldoutState.HOLDOUT_EXECUTED,
            evidence_sha256=execution_sha,
        )
    execution = verify_state_artifact(
        args.control_root,
        HoldoutState.HOLDOUT_EXECUTED,
        "locks/holdout-execution.json",
    )
    load_terminal_records(schedule, args.journal_root)
    attempts_root = validate_attempt_markers(schedule, args.journal_root)
    terminal_sha = sha256_tree(args.journal_root, include_suffixes=(".json",))
    attempt_sha = sha256_tree(attempts_root, include_suffixes=(".json",))
    if (
        execution.get("run_count") != len(schedule)
        or execution.get("terminal_records_sha256") != terminal_sha
        or execution.get("semantic_attempts_sha256") != attempt_sha
    ):
        raise ValueError("holdout execution artifacts drifted before locking")
    terminal_lock_sha = write_json_create_once(
        args.control_root / "locks" / "terminal-records-lock.json",
        {
            "schema_version": "rcaeval-re2.terminal-records-lock.v1",
            "run_count": len(schedule),
            "terminal_records_sha256": terminal_sha,
            "semantic_attempts_sha256": attempt_sha,
            "execution_lock_sha256": sha256_file(
                args.control_root / "locks" / "holdout-execution.json"
            ),
        },
    )
    advance_state(
        state_journal,
        HoldoutState.TERMINAL_RECORDS_LOCKED,
        evidence_sha256=terminal_lock_sha,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
