from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

import scripts.phase5b_execution.evaluator as evaluator
from scripts.phase5b_execution.contracts import (
    ExecutionUnblindingRecord,
    canonical_json_bytes,
)
from scripts.phase5b_execution.evaluator import admit_unblinded_evaluator
from scripts.phase5b_execution.lifecycle import (
    EXECUTION_COMPLETE_SEAL,
    MAIN_EXECUTION_REPORT,
    UNBLINDING_RECORD,
)


def test_evaluator_refuses_truth_before_unblinding_without_reading_paths(
    tmp_path: Path,
) -> None:
    with pytest.raises(PermissionError, match="before UNBLINDED"):
        admit_unblinded_evaluator(
            project_root=tmp_path,
            execution_root=tmp_path / "execution",
            hidden_ground_truth_root=tmp_path / "absent-truth",
        )


def test_forged_unblinding_without_raw_chain_does_not_probe_truth(
    tmp_path: Path,
) -> None:
    execution = tmp_path / "execution"
    report = execution / MAIN_EXECUTION_REPORT
    report.parent.mkdir(parents=True)
    report.write_bytes(b"{}\n")
    complete = execution / EXECUTION_COMPLETE_SEAL
    complete.parent.mkdir(parents=True)
    complete.write_bytes(b'{"complete":true}\n')
    record = ExecutionUnblindingRecord(
        schema_version="phase5b.unblinding-record.v1",
        evaluation_version="phase5b.v1",
        protocol_commit="0" * 40,
        execution_source_commit="9" * 40,
        protocol_freeze_manifest_sha256="1" * 64,
        execution_freeze_sha256="2" * 64,
        execution_schedule_sha256="3" * 64,
        hidden_pack_manifest_sha256="4" * 64,
        agent_visible_pack_sha256="5" * 64,
        ground_truth_pack_sha256="6" * 64,
        execution_report_sha256=hashlib.sha256(report.read_bytes()).hexdigest(),
        ablation_report_sha256="7" * 64,
        execution_complete_seal_sha256=hashlib.sha256(
            complete.read_bytes()
        ).hexdigest(),
        completed_main_runs=180,
        completed_ablation_runs=38,
        from_state="EXECUTION_COMPLETE",
        to_state="UNBLINDED",
        irreversible=True,
        create_once=True,
    )
    unblinding = execution / UNBLINDING_RECORD
    unblinding.parent.mkdir(parents=True, exist_ok=True)
    unblinding.write_bytes(canonical_json_bytes(record.model_dump(mode="json")))

    class TruthProbe:
        touched = False

        def lstat(self):
            self.touched = True
            raise AssertionError("truth root was probed")

    truth = TruthProbe()
    with pytest.raises((FileNotFoundError, ValueError)):
        admit_unblinded_evaluator(
            project_root=tmp_path,
            execution_root=execution,
            hidden_ground_truth_root=truth,  # type: ignore[arg-type]
        )

    assert truth.touched is False


def test_evaluator_admits_truth_after_full_chain_verification(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    execution = tmp_path / "execution"
    report = execution / MAIN_EXECUTION_REPORT
    report.parent.mkdir(parents=True)
    report.write_bytes(b"{}\n")
    truth = tmp_path / "ground-truth"
    truth.mkdir()
    record = ExecutionUnblindingRecord(
        schema_version="phase5b.unblinding-record.v1",
        evaluation_version="phase5b.v1",
        protocol_commit="0" * 40,
        execution_source_commit="9" * 40,
        protocol_freeze_manifest_sha256="1" * 64,
        execution_freeze_sha256="2" * 64,
        execution_schedule_sha256="3" * 64,
        hidden_pack_manifest_sha256="4" * 64,
        agent_visible_pack_sha256="5" * 64,
        ground_truth_pack_sha256="6" * 64,
        execution_report_sha256=hashlib.sha256(report.read_bytes()).hexdigest(),
        ablation_report_sha256="7" * 64,
        execution_complete_seal_sha256="8" * 64,
        completed_main_runs=180,
        completed_ablation_runs=38,
        from_state="EXECUTION_COMPLETE",
        to_state="UNBLINDED",
        irreversible=True,
        create_once=True,
    )
    unblinding = execution / UNBLINDING_RECORD
    unblinding.parent.mkdir(parents=True, exist_ok=True)
    unblinding.write_bytes(canonical_json_bytes(record.model_dump(mode="json")))
    monkeypatch.setattr(
        evaluator,
        "verify_unblinding_chain",
        lambda _project_root, _execution_root: record,
    )

    admitted = admit_unblinded_evaluator(
        project_root=tmp_path,
        execution_root=execution,
        hidden_ground_truth_root=truth,
    )

    assert admitted.unblinding_record == record
    assert admitted.hidden_ground_truth_root == truth
