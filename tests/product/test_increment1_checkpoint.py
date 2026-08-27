from __future__ import annotations

from pathlib import Path

from scripts.product.run_increment1_checkpoint import run_checkpoint


def test_increment1_checkpoint_mints_api_terminal(tmp_path: Path) -> None:
    result = run_checkpoint(tmp_path)

    assert result["terminal"] == "ECOMSRE_PRODUCT_MVP_V01_API_PASS"
    assert result["process_mode"] == "SEPARATE_API_AND_WORKER_PROCESSES"
    assert result["api_process_starts"] == 2
    assert result["worker_process_starts"] == 1
    assert result["environment_persisted_after_restart"] is True
    assert result["fixture_job_status"] == "SUCCEEDED"
