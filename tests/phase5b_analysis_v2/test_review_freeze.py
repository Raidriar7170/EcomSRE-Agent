from __future__ import annotations

from pathlib import Path

from scripts.phase5b_analysis_v2.freeze import (
    verify_analysis_freeze,
    verify_review_disposition,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_v2_analysis_freeze_binds_repair_runtime_and_public_mapping() -> None:
    freeze = verify_analysis_freeze(PROJECT_ROOT)

    assert freeze.analysis_version == "phase5b.v2-analysis-contract-repair"
    assert freeze.provider_calls == 0
    assert freeze.analysis_executed is False
    assert freeze.review_required is True
    assert len(freeze.harness_files) == 9


def test_v2_review_disposition_is_ready_without_analysis_outputs() -> None:
    disposition = verify_review_disposition(PROJECT_ROOT)

    assert disposition.status == "PHASE5B_V2_ANALYSIS_CONTRACT_REPAIR_READY_FOR_REVIEW"
    assert disposition.v1_termination_status == (
        "PHASE5B_V1_TERMINATED_GROUND_TRUTH_CONTRACT_MISMATCH"
    )
    assert disposition.main_terminal == 180
    assert disposition.ablation_gap_terminal == 38
    assert disposition.ground_truth_records_admitted == 30
    assert disposition.provider_calls == 0
    assert disposition.provider_reruns == 0
    assert disposition.scoring_bundle_created is False
    assert disposition.final_report_created is False
    assert disposition.analysis_executed is False
    assert disposition.review_required is True
