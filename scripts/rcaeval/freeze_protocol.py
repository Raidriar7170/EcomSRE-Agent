from __future__ import annotations

import argparse
from pathlib import Path

from pydantic import ValidationError

from ecomsre_rcaeval.artifacts import (
    canonical_json_bytes,
    schedule_payload,
    sha256_bytes,
    read_json_object,
    sha256_file,
    sha256_tree,
    write_json_create_once,
)
from ecomsre_rcaeval.contracts import Architecture, TerminalRecord, TerminalStatus
from ecomsre_rcaeval.freeze import (
    REQUIRED_CONFIG_NAMES,
    current_runtime_bindings,
    implementation_snapshot,
    require_clean_repository,
    require_external_control_root,
    repository_base_commit,
    verify_worktree_matches_snapshot,
)
from ecomsre_rcaeval.lifecycle import advance_state, current_state
from ecomsre_rcaeval.state import HoldoutState
from scripts.rcaeval.common import CONFIG_ROOT, frozen_schedule, verify_prompt_lock


def _validate_dev_report(
    report: dict[str, object],
    *,
    provider_mode: str,
    maximum_cases: int,
    journal_root: Path,
) -> None:
    case_count = report.get("case_count")
    run_count = report.get("run_count")
    architectures = report.get("architectures")
    prompt_lock = read_json_object(CONFIG_ROOT / "prompt-lock.json")
    if (
        report.get("schema_version") != "rcaeval-re2.dev-pilot.v1"
        or report.get("provider_mode") != provider_mode
        or report.get("development_only") is not True
        or type(case_count) is not int
        or not 1 <= case_count <= maximum_cases
        or run_count != case_count * 3
        or report.get("completed_run_count") != run_count
        or report.get("systems") != ["RE2-OB", "RE2-SS"]
        or report.get("model") != prompt_lock.get("model")
        or report.get("prompt_lock_sha256")
        != sha256_file(CONFIG_ROOT / "prompt-lock.json")
        or report.get("budget_lock_sha256")
        != sha256_file(CONFIG_ROOT / "budget-lock.json")
        or not isinstance(report.get("terminal_journal_sha256"), str)
        or not isinstance(report.get("attempt_journal_sha256"), str)
        or not isinstance(report.get("development_schedule_sha256"), str)
        or not isinstance(report.get("run_lock_sha256"), str)
        or report.get("architecture_semantics")
        != {
            "single": "direct_sequential_sources_then_final",
            "fixed": "three_fixed_specialists_then_judge",
            "dynamic": "commander_staged_specialists_then_judge",
        }
        or not isinstance(architectures, dict)
        or set(architectures) != {"single", "fixed", "dynamic"}
    ):
        raise ValueError(f"{provider_mode} development report is incomplete")
    for architecture in architectures.values():
        if (
            not isinstance(architecture, dict)
            or architecture.get("denominator") != case_count
            or architecture.get("terminal_failures") != 0
        ):
            raise ValueError(f"{provider_mode} development architecture failed")
    attempts_root = journal_root.parent / f"{journal_root.name}.attempts"
    run_lock_path = journal_root.parent / "run-lock.json"
    run_lock = read_json_object(run_lock_path)
    expected_run_lock = {
        "schema_version": "rcaeval-re2.dev-run-lock.v1",
        "provider_mode": provider_mode,
        "repository_base_commit": repository_base_commit(),
        "development_schedule_sha256": report["development_schedule_sha256"],
        **current_runtime_bindings(),
    }
    if (
        not journal_root.is_dir()
        or journal_root.is_symlink()
        or not attempts_root.is_dir()
        or attempts_root.is_symlink()
        or report.get("terminal_journal_sha256")
        != sha256_tree(journal_root, include_suffixes=(".json",))
        or report.get("attempt_journal_sha256")
        != sha256_tree(attempts_root, include_suffixes=(".json",))
        or report.get("run_lock_sha256") != sha256_file(run_lock_path)
        or run_lock != expected_run_lock
    ):
        raise ValueError(f"{provider_mode} development journal hash mismatch")
    terminal_paths = tuple(sorted(journal_root.iterdir()))
    attempt_paths = tuple(sorted(attempts_root.iterdir()))
    if len(terminal_paths) != run_count or len(attempt_paths) != run_count:
        raise ValueError(f"{provider_mode} development journal count mismatch")
    records: list[TerminalRecord] = []
    for path in terminal_paths:
        try:
            records.append(
                TerminalRecord.model_validate_json(
                    canonical_json_bytes(read_json_object(path))
                )
            )
        except (ValidationError, ValueError) as error:
            raise ValueError(
                f"{provider_mode} development terminal record is invalid"
            ) from error
    if (
        len({item.run_id for item in records}) != run_count
        or any(item.terminal_status is not TerminalStatus.COMPLETED for item in records)
        or any(
            item.model_calls != 1
            if item.architecture is Architecture.SINGLE
            else item.model_calls != 4
            if item.architecture is Architecture.FIXED
            else item.model_calls not in {4, 5}
            for item in records
        )
        or (
            provider_mode == "real"
            and any(
                item.known_provider_tokens is None
                or item.known_provider_tokens <= 0
                for item in records
            )
        )
    ):
        raise ValueError(f"{provider_mode} development terminals are incomplete")


def main() -> int:
    parser = argparse.ArgumentParser(description="Freeze the RCAEval RE2 v1 protocol")
    parser.add_argument("--control-root", type=Path, required=True)
    parser.add_argument("--dev-audit", type=Path, required=True)
    parser.add_argument("--dev-smoke", type=Path, required=True)
    parser.add_argument("--dev-smoke-journal", type=Path, required=True)
    parser.add_argument("--dev-pilot", type=Path, required=True)
    parser.add_argument("--dev-pilot-journal", type=Path, required=True)
    args = parser.parse_args()
    args.control_root = require_external_control_root(args.control_root)
    require_clean_repository()
    if current_state(args.control_root / "state-journal") is not HoldoutState.DEV_ONLY:
        raise ValueError("protocol freeze requires DEV_ONLY state")
    verify_prompt_lock()
    if {path.name for path in CONFIG_ROOT.iterdir()} != REQUIRED_CONFIG_NAMES:
        raise ValueError("protocol config file set is incomplete or unexpected")
    audit = read_json_object(args.dev_audit)
    smoke = read_json_object(args.dev_smoke)
    pilot = read_json_object(args.dev_pilot)
    if (
        audit.get("schema_version") != "rcaeval-re2.dev-dataset-audits.v1"
        or not isinstance(audit.get("audits"), list)
        or len(audit["audits"]) != 2
    ):
        raise ValueError("development dataset audit evidence is incomplete")
    dataset_lock = read_json_object(CONFIG_ROOT / "dataset-lock.json")
    audit_by_system = {
        item.get("system"): item
        for item in audit["audits"]
        if isinstance(item, dict)
    }
    archives = dataset_lock.get("archives")
    if (
        set(audit_by_system) != {"RE2-OB", "RE2-SS"}
        or not isinstance(archives, dict)
    ):
        raise ValueError("development dataset audit systems are invalid")
    for system in ("RE2-OB", "RE2-SS"):
        observed = audit_by_system[system]
        locked = archives.get(system)
        if (
            not isinstance(locked, dict)
            or observed.get("case_count") != 90
            or observed.get("service_count") != 5
            or observed.get("fault_count") != 6
            or observed.get("extracted_manifest_sha256")
            != locked.get("extracted_manifest_sha256")
            or observed.get("schema_manifest_sha256")
            != locked.get("schema_manifest_sha256")
        ):
            raise ValueError("development dataset audit differs from dataset lock")
    _validate_dev_report(
        smoke,
        provider_mode="heuristic",
        maximum_cases=60,
        journal_root=args.dev_smoke_journal,
    )
    _validate_dev_report(
        pilot,
        provider_mode="real",
        maximum_cases=60,
        journal_root=args.dev_pilot_journal,
    )
    schedule_path = args.control_root / "locks" / "holdout-schedule.json"
    schedule_value = schedule_payload(frozen_schedule())
    expected_schedule_sha = sha256_bytes(canonical_json_bytes(schedule_value))
    if schedule_path.exists():
        if (
            read_json_object(schedule_path) != schedule_value
            or sha256_file(schedule_path) != expected_schedule_sha
        ):
            raise ValueError("existing holdout schedule differs from protocol")
        schedule_sha = expected_schedule_sha
    else:
        schedule_sha = write_json_create_once(schedule_path, schedule_value)
    freeze_path = args.control_root / "locks" / "protocol-freeze.json"
    runtime_bindings = current_runtime_bindings()
    snapshot = implementation_snapshot(require_complete=True)
    if snapshot.get("scoped_file_count") != snapshot.get(
        "expected_scoped_file_count"
    ):
        raise ValueError("implementation snapshot does not cover the locked scope")
    verify_worktree_matches_snapshot(snapshot)
    freeze_value = {
        "schema_version": "rcaeval-re2.protocol-freeze.v2",
        "repository_base_commit": repository_base_commit(),
        "implementation_snapshot": snapshot,
        **runtime_bindings,
        "holdout_schedule_sha256": schedule_sha,
        "development_evidence": {
            "dataset_audit_sha256": sha256_file(args.dev_audit),
            "smoke_sha256": sha256_file(args.dev_smoke),
            "real_provider_pilot_sha256": sha256_file(args.dev_pilot),
        },
    }
    expected_freeze_sha = sha256_bytes(canonical_json_bytes(freeze_value))
    if freeze_path.exists():
        if (
            read_json_object(freeze_path) != freeze_value
            or sha256_file(freeze_path) != expected_freeze_sha
        ):
            raise ValueError("existing protocol freeze differs from current inputs")
        freeze_sha = expected_freeze_sha
    else:
        freeze_sha = write_json_create_once(freeze_path, freeze_value)
    advance_state(
        args.control_root / "state-journal",
        HoldoutState.PROTOCOL_FROZEN,
        evidence_sha256=freeze_sha,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
