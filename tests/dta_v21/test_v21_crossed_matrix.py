from __future__ import annotations

from pathlib import Path

from ecomsre.dta_v2.v21.registry import (
    load_default_scenario_registries,
    validate_crossed_matrix,
)


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_crossed_scenario_matrix_passes_all_anti_shortcut_checks() -> None:
    observer, evaluator, anchors = load_default_scenario_registries(REPO_ROOT)
    report = validate_crossed_matrix(
        observer_registry=observer,
        evaluator_registry=evaluator,
        legacy_anchors=anchors,
    )

    assert report.status == "PASS"
    assert all(report.checks.values())
    assert len(report.report_sha256) == 64
