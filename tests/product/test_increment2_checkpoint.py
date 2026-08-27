from __future__ import annotations

from pathlib import Path

from scripts.product.run_increment2_checkpoint import run_checkpoint


def test_increment2_checkpoint_mints_connector_terminal(tmp_path: Path) -> None:
    result = run_checkpoint(tmp_path)

    assert result["terminal"] == "ECOMSRE_PRODUCT_MVP_V01_CONNECTOR_PASS"
    assert result["configured_connector_kinds"] == [
        "PROMETHEUS",
        "OPENSEARCH",
        "JAEGER",
        "HTTP_HEALTH",
    ]
    assert set(result["source_statuses"].values()) == {"AVAILABLE"}
    assert result["canonical_services"] == ["payment"]
    assert result["baseline_window_count"] == 6
    assert result["baseline_successful_windows"] == 6
    assert result["baseline_active"] is False
    assert result["state_persisted_after_restart"] is True
    assert result["agent_writes"] == 0
    assert result["runbook_executions"] == 0
