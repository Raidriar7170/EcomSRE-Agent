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
        "active_pr": 53,
        "merged_prs": [50, 51, 52],
        "preferred_model": "gpt-5.4-2026-03-05",
        "frozen_model": "gpt-5.4-mini-2026-03-17",
        "flat_adaptive_identity_sha256": (
            "848e8bad49840f6efff74a60f521c8ef05d85fc1dc7aeceae97d2101dbc17dd7"
        ),
        "planner_identity_sha256": (
            "80506a41847d705f048f521b06d63035b4a5b47526eddc501c794b370528300d"
        ),
        "one_shot_identity_sha256": (
            "a811067196589dbe0fb6c75c20d68c2b61b0cce9398f271e2f7c40f85377e3d4"
        ),
        "development_report_sha256": (
            "e7b31098a7a8e4e0ac7efdd31f085a3bc510290510ea1601a560372418e0fd80"
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
