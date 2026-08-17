from __future__ import annotations

import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_master_progress_tracks_exact_pr_c_stage() -> None:
    progress = json.loads(
        (REPO_ROOT / "docs/analysis/dta-v21-p0-master-progress.json").read_text(
            encoding="utf-8"
        )
    )

    assert progress == {
        "schema_version": "dta-v21-p0-master-progress.v1",
        "goal_version": "dta-v21-p0-master-v1",
        "goal_sha256": (
            "3c91e7777395e46f088695640991c17da1f70285bd844739391346b56f168daf"
        ),
        "inspected_starting_main": ("925d23994888d1b83e57fc1bbdd1944e57a1bfff"),
        "actual_starting_main": ("925d23994888d1b83e57fc1bbdd1944e57a1bfff"),
        "completed_stage": "PR-C",
        "current_stage": "PR-D",
        "main_head": "c0a541fec48f11b02dc2cd6ba41673a777e55eee",
        "active_branch": "codex/dta-v21-p0-pr-d-evaluation-freeze",
        "active_pr": None,
        "merged_prs": [50, 51, 52],
        "preferred_model": "gpt-5.4-2026-03-05",
        "frozen_model": "gpt-5.4-mini-2026-03-17",
        "flat_adaptive_identity_sha256": (
            "4be3415b712932072d6098284db6198feed939f64f794a1cbbebd6d741669c23"
        ),
        "planner_identity_sha256": (
            "18b76dc667e61fddbe48db698851f28b3afe11d859b8394bcd5fa1b95775201b"
        ),
        "one_shot_identity_sha256": (
            "d938616fe7854199f88b0ae8cfad68515d9bf550846d6587717a5b1db2464b64"
        ),
        "development_report_sha256": (
            "5b25f1b9915045dc73641883067a6d242552bba8141ad5a968e0c2972dda3fd3"
        ),
        "held_out_seal_sha256": None,
        "held_out_execution_id": None,
        "held_out_claim": None,
        "live_demo_terminal": None,
        "final_engineering_terminal": None,
    }


def test_decision_register_contains_exact_v21_protocol_records() -> None:
    decisions = (REPO_ROOT / "docs/DECISIONS.md").read_text(encoding="utf-8")

    for decision_id in range(39, 44):
        marker = f"## DEC-{decision_id:03d} —"
        assert decisions.count(marker) == 1
    assert "DEPENDENCY_LATENCY" in decisions
    assert "DTA_V21_NO_PREREGISTERED_PLANNER_ADVANTAGE_SUPPORTED" in decisions
    assert "zero model write authority" in decisions.lower()
    assert "`MITIGATE_CPU_SATURATION` on owned\nAd with risk frozen as `LOW`" in (
        decisions
    )


def test_successor_design_keeps_dependency_timeout_out_of_ontology() -> None:
    design = (REPO_ROOT / "docs/design/diagnosis-to-action-v2.1-p0.md").read_text(
        encoding="utf-8"
    )

    assert "`DEPENDENCY_LATENCY`" in design
    assert "`DEPENDENCY_TIMEOUT`" not in design
    assert "`EVIDENCE_GUIDED_PLANNER`" in design
    assert "24,000 UTF-8 bytes" in design
    assert "exact owned Ad, risk `LOW`" in design

    safety = (REPO_ROOT / "docs/SAFETY_BOUNDARIES.md").read_text(encoding="utf-8")
    assert "`MITIGATE_CPU_SATURATION` risk is frozen as `LOW`" in safety
