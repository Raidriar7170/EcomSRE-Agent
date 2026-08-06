"""Irreversible admission gate for the post-execution evaluator process."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import stat

from scripts.phase5b_execution.checkpoint import _entry_exists
from scripts.phase5b_execution.contracts import ExecutionUnblindingRecord
from scripts.phase5b_execution.lifecycle import (
    MAIN_EXECUTION_REPORT,
    UNBLINDING_RECORD,
    verify_unblinding_chain,
)


@dataclass(frozen=True, slots=True)
class EvaluatorInputs:
    execution_report_path: Path
    hidden_ground_truth_root: Path
    unblinding_record: ExecutionUnblindingRecord


def admit_unblinded_evaluator(
    *,
    project_root: Path,
    execution_root: Path,
    hidden_ground_truth_root: Path,
) -> EvaluatorInputs:
    """Admit truth only after the execution-layer irreversible record exists."""

    unblinding_path = execution_root / UNBLINDING_RECORD
    if not _entry_exists(unblinding_path):
        raise PermissionError("evaluator truth is unavailable before UNBLINDED")
    unblinding = verify_unblinding_chain(project_root, execution_root)
    execution_report_path = execution_root / MAIN_EXECUTION_REPORT
    report_details = execution_report_path.lstat()
    if stat.S_ISLNK(report_details.st_mode) or not stat.S_ISREG(
        report_details.st_mode
    ):
        raise ValueError("execution report must be a regular non-symlink file")
    truth_details = hidden_ground_truth_root.lstat()
    if stat.S_ISLNK(truth_details.st_mode) or not stat.S_ISDIR(
        truth_details.st_mode
    ):
        raise ValueError("hidden ground-truth root must be a real directory")
    return EvaluatorInputs(
        execution_report_path=execution_report_path.resolve(strict=True),
        hidden_ground_truth_root=hidden_ground_truth_root.resolve(strict=True),
        unblinding_record=unblinding,
    )
