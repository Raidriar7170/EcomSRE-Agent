from pathlib import Path

from scripts.product.run_increment3_checkpoint import run_checkpoint


def test_increment3_checkpoint_mints_diagnosis_terminal(tmp_path: Path) -> None:
    result = run_checkpoint(tmp_path)

    assert result == {
        "terminal": "ECOMSRE_PRODUCT_MVP_V01_DIAGNOSIS_PASS",
        "known_terminal": "CORE_KNOWN",
        "known_mechanism": "SERVICE_UNAVAILABLE",
        "unknown_terminal": "OPEN_WORLD",
        "unknown_report_terminal": "UNREGISTERED_INCIDENT_SUSPECTED",
        "known_evidence_objects": result["known_evidence_objects"],
        "unknown_evidence_objects": result["unknown_evidence_objects"],
        "restart_persistence": True,
        "agent_writes": 0,
        "runbook_executions": 0,
        "provider_calls": 0,
    }
    assert result["known_evidence_objects"] >= 6
    assert result["unknown_evidence_objects"] >= 6
