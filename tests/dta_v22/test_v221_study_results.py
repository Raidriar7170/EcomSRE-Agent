from __future__ import annotations

from pathlib import Path

from scripts.ci.verify_dta_v221_study_results import (
    verify_dta_v221_study_results,
)


ROOT = Path(__file__).resolve().parents[2]


def test_committed_v221_study_is_complete_recomputed_and_reported() -> None:
    result = verify_dta_v221_study_results(repository_root=ROOT)

    assert result["arm_policy_runs"] == 48
    assert result["execution_count"] == 1
    assert result["agent_writes"] == 0
    assert result["policy_terminal"] == (
        "DTA_V22_1_NO_EVIDENCE_ACQUISITION_EFFECT_OBSERVED"
    )
