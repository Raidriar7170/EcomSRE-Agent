from __future__ import annotations

import json
from pathlib import Path
import re


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_master_progress_tracks_exact_pr_f_live_stage() -> None:
    progress = json.loads(
        (REPO_ROOT / "docs/analysis/dta-v21-p0-master-progress.json").read_text(
            encoding="utf-8"
        )
    )
    report = json.loads(
        (REPO_ROOT / "docs/results/dta-v21-live-capability-closeout.json").read_text(
            encoding="utf-8"
        )
    )
    assert progress.pop("capability_closeout_report_sha256") == report["report_sha256"]
    assert progress.pop("private_capability_closeout_sha256") == report[
        "private_closeout_sha256"
    ]
    assert progress.pop("capability_closeout_source_code_head") == report[
        "closeout_source_code_head"
    ]
    assert progress.pop("capability_closeout_candidate_scope_sha256") == report[
        "candidate_scope_sha256"
    ]
    assert re.fullmatch(r"[0-9a-f]{40}", report["closeout_source_code_head"])

    assert progress == {
        "schema_version": "dta-v21-p0-master-progress.v1",
        "goal_version": "dta-v21-p0-master-v1",
        "goal_sha256": (
            "3c91e7777395e46f088695640991c17da1f70285bd844739391346b56f168daf"
        ),
        "active_amendment_version": (
            "dta-v21-p0-prf-final-capability-closeout-v1"
        ),
        "active_amendment_sha256": (
            "bf9484483583202a198e7699d57ee92f94c8a3ed2207cac3489601542645be1e"
        ),
        "active_decision_id": "DEC-047",
        "inspected_starting_main": ("925d23994888d1b83e57fc1bbdd1944e57a1bfff"),
        "actual_starting_main": ("925d23994888d1b83e57fc1bbdd1944e57a1bfff"),
        "completed_stage": "PR-E",
        "current_stage": "PR-F",
        "main_head": "1c763eb815764e971855a5d6730981b9a2e5858a",
        "active_branch": "codex/dta-v21-p0-pr-f-live-closeout",
        "active_pr": 55,
        "merged_prs": [50, 51, 52, 53, 54],
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
            "ed624890b655f10598310daefb574eaea0ca74085183ba70cbc31cb05a812a43"
        ),
        "held_out_seal_sha256": (
            "9a7c8e56400e99c693c8bddc26007b1dd26e0dcee2167b07cf3fba00fd22fbd7"
        ),
        "held_out_execution_id": "53615cdd78b348b68496f64102c0b4de",
        "held_out_claim": (
            "DTA_V21_NO_PREREGISTERED_PLANNER_ADVANTAGE_SUPPORTED"
        ),
        "ad_cpu_resource_recovery_protocol_sha256": (
            "c983b9be95b532cdbb8fb5358af92055e633fd767693e9dc65743b3e80a77517"
        ),
        "historical_blocked_attempt_id": (
            "dta-v21-prf-01-no-fault-422f015451fd"
        ),
        "historical_blocked_attempt_terminal": "BLOCKED_DTA_V21_PRF_SAFETY",
        "historical_blocked_attempt_baseline_restored": False,
        "historical_blocked_attempt_cleanup": "BLOCKED",
        "no_fault_capability_attempt_id": (
            "dta-v21-prf-01-no-fault-a167285a6a1d"
        ),
        "no_fault_capability_classification": (
            "NO_FAULT_FALSE_POSITIVE_DIAGNOSIS_SAFE_NO_ACTION"
        ),
        "no_fault_diagnosis_passed": False,
        "no_fault_no_write_safety_passed": True,
        "positive_continuation_status": "CONSUMED_FAILED",
        "positive_slots_passed": 0,
        "four_slot_acceptance_passed": False,
        "live_demo_terminal": None,
        "final_engineering_terminal": None,
        "ad_cpu_agent_terminal": "FAILED",
        "ad_cpu_agent_failure_code": "DUPLICATE_READ_REQUEST",
        "ad_cpu_recovery_tested": False,
        "positive_slots_attempted": 1,
        "email_slot_status": "NOT_ATTEMPTED",
        "product_catalog_slot_status": "NOT_ATTEMPTED",
        "agent_forward_writes_observed": 0,
        "remaining_live_execution_authority": 0,
        "live_slots_attempted": 2,
        "live_slots_passed": 0,
    }


def test_decision_register_contains_exact_v21_protocol_records() -> None:
    decisions = (REPO_ROOT / "docs/DECISIONS.md").read_text(encoding="utf-8")

    for decision_id in range(39, 47):
        marker = f"## DEC-{decision_id:03d} —"
        assert decisions.count(marker) == 1
    assert "DEPENDENCY_LATENCY" in decisions
    assert "DTA_V21_NO_PREREGISTERED_PLANNER_ADVANTAGE_SUPPORTED" in decisions
    assert "zero model write authority" in decisions.lower()
    assert "`RESOURCE_ONLY`" in decisions
    assert "`NON_REGRESSION_GUARDRAIL`" in decisions
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
