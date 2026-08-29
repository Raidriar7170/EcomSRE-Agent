from __future__ import annotations

import ast
from pathlib import Path

from ecomsre.product.pilot.live_nofault_acceptance_v023 import (
    KNOWLEDGE_LOOP_HANDOFF_NOT_AUTHORIZED_V023,
    _render_handoff_markdown,
)


ROOT = Path(__file__).resolve().parents[2]


def _runner_source() -> str:
    return (
        ROOT
        / "src/ecomsre/product/pilot/live_nofault_acceptance_v023.py"
    ).read_text(encoding="utf-8")


def test_nofault_runner_never_submits_another_baseline() -> None:
    source = _runner_source()

    assert "/baseline-jobs" not in source
    assert "/verify-jobs" not in source
    assert "BASELINE_BUILD" in source
    assert "baseline_job_count" in source


def test_nofault_runner_has_one_incident_creation_boundary() -> None:
    module = ast.parse(_runner_source())
    literal_count = sum(
        isinstance(node, ast.Constant) and node.value == "/v1/incidents"
        for node in ast.walk(module)
    )

    assert literal_count == 1


def test_conditional_handoff_does_not_authorize_fault_calibration() -> None:
    payload = {
        "terminal": KNOWLEDGE_LOOP_HANDOFF_NOT_AUTHORIZED_V023,
        "authorized": False,
        "required_repair_reasons": ["FRESH_HEALTHY_RUNTIME_MISSING"],
    }

    rendered = _render_handoff_markdown(payload)

    assert KNOWLEDGE_LOOP_HANDOFF_NOT_AUTHORIZED_V023 in rendered
    assert "Authorized: `false`" in rendered
    assert "FRESH_HEALTHY_RUNTIME_MISSING" in rendered
