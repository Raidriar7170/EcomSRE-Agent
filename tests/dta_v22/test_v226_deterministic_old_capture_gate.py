from __future__ import annotations

from pathlib import Path

from scripts.dta_v226.run_old_capture_deterministic_gate import build_gate


ROOT = Path(__file__).resolve().parents[2]


def test_exact_eight_run_deterministic_old_capture_gate_passes() -> None:
    gate = build_gate(ROOT)

    assert gate["status"] == "DTA_V226_DETERMINISTIC_OLD_CAPTURE_GATE_PASS"
    assert gate["arm_run_count"] == 8
    assert gate["execution_count"] == 1
    assert gate["docker_calls"] == 0
    assert gate["provider_network_calls"] == 0
    assert gate["agent_writes"] == 0
    assert gate["action_proposals"] == 0
    assert gate["runbook_executions"] == 0
    assert gate["arms"] == {
        "MODEL_DIRECTED_RETRIEVAL": {
            "valid_terminals": 4,
            "exact": 4,
            "fault_exact": 2,
            "baseline_exact": 2,
        },
        "CURRENT_RUNTIME_BUNDLE": {
            "valid_terminals": 4,
            "exact": 4,
            "fault_exact": 2,
            "baseline_exact": 2,
        },
    }
