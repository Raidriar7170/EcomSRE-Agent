from __future__ import annotations

from pathlib import Path

from scripts.ci.verify_phase5b_execution_historical_bindings import (
    verify_historical_execution_bindings,
)


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_phase5b_execution_harness_remains_historically_bound() -> None:
    manifest = verify_historical_execution_bindings(REPO_ROOT)

    assert manifest.evaluation_version == "phase5b.v1"
    assert len(manifest.harness_files) == 28
    assert manifest.harness_files["Makefile"] == (
        "8f6e1c07d8bd5a139390c3a6e1f883e2736658b6483e02ef9fa9421ad5984eaa"
    )
