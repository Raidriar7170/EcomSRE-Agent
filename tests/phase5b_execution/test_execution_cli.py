from __future__ import annotations

from pathlib import Path

import pytest

from scripts.phase5b_execution.cli import (
    execution_preflight,
    run_actual_ablation_execution,
    run_actual_main_execution,
    main,
    verify_mock_rehearsal,
    write_mock_rehearsal,
)


def test_execution_preflight_exposes_main_readiness_and_ablation_gap() -> None:
    report = execution_preflight()

    assert report["main_evaluation_ready"] is True
    assert report["ablation_slot_count"] == 38
    assert report["ablation_implementation_available"] is False
    assert report["ablation_evidence_available"] is False
    assert report["ablation_primary_eligible"] is False
    assert report["ablation_disposition"] == (
        "ABLATION_NOT_IMPLEMENTED_IN_FROZEN_HARNESS"
    )


def test_cli_mock_rehearsal_and_verify_close_180_plus_38(tmp_path: Path) -> None:
    report = write_mock_rehearsal(tmp_path)
    verified = verify_mock_rehearsal(tmp_path)

    assert report["main_terminal_records"] == 180
    assert report["ablation_terminal_records"] == 38
    assert report["provider_calls"] == 0
    assert report["ground_truth_reads"] == 0
    assert verified["status"] == "MOCK_EXECUTION_REHEARSAL_VERIFIED"


def test_mock_verify_rejects_corrupted_raw_record(tmp_path: Path) -> None:
    write_mock_rehearsal(tmp_path)
    first = next((tmp_path / "main/raw").glob("*.json"))
    first.write_text("{}\n", encoding="utf-8")

    with pytest.raises(Exception):
        verify_mock_rehearsal(tmp_path)


@pytest.mark.parametrize("flag", ["--retry", "--rerun-failed", "--overwrite"])
def test_cli_exposes_no_retry_rerun_or_overwrite_flag(flag: str) -> None:
    with pytest.raises(SystemExit, match="2"):
        main(["mock-rehearsal", flag])


def test_actual_main_refuses_generic_or_missing_authorization(
    tmp_path: Path,
) -> None:
    with pytest.raises(PermissionError, match="exact Phase 5B"):
        run_actual_main_execution(
            output_root=tmp_path,
            environment={"PHASE5B_EXECUTION_AUTHORIZATION": "ALLOW_ALL"},
        )
    with pytest.raises(PermissionError, match="exact Phase 5B"):
        run_actual_ablation_execution(
            output_root=tmp_path,
            environment={},
        )


def test_actual_execution_rejects_arbitrary_external_output_root(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="frozen private root"):
        run_actual_main_execution(
            output_root=tmp_path,
            environment={
                "PHASE5B_EXECUTION_AUTHORIZATION": (
                    "AUTHORIZE_PHASE5B_V1_SCORED_EXECUTION"
                )
            },
        )
