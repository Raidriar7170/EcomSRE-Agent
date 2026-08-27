from __future__ import annotations

from pathlib import Path

import pytest

from ecomsre.dta_v2.v23.evaluation_v234 import (
    EvaluationArmV234,
    LazyRegistrationTruthStoreV234,
    load_core_schema_views_v234,
    load_registration_tasks_v234,
    run_evaluation_data_audit_v234,
    run_deterministic_study_v234,
    run_runtime_preflight_v234,
)


ROOT = Path(__file__).resolve().parents[2]
EVALUATION_ROOT = ROOT / "config/dta-v234/evaluation"


def test_v234_evaluation_admission_is_exact_and_opaque() -> None:
    tasks = load_registration_tasks_v234(EVALUATION_ROOT / "tasks.json")
    views = load_core_schema_views_v234(
        EVALUATION_ROOT / "core-schema-snapshot.json"
    )

    audit = run_evaluation_data_audit_v234(
        repository_root=ROOT,
        evaluation_root=EVALUATION_ROOT,
    )

    assert len(tasks.tasks) == 16
    assert len(views.views) == 16
    assert audit.hidden_known_task_count == 10
    assert audit.unregistered_task_count == 4
    assert audit.control_task_count == 2
    assert audit.hidden_view_pass_count == 10
    assert audit.unregistered_core_clause_match_count == 0
    assert audit.duplicate_control_core_match_count == 1
    assert audit.insufficient_control_evidence_source_count <= 1
    assert audit.premature_truth_reads == 0
    assert audit.terminal == "DTA_V234_EVALUATION_DATA_PASS"


def test_v234_truth_is_unavailable_until_both_arms_complete() -> None:
    store = LazyRegistrationTruthStoreV234(EVALUATION_ROOT / "truth.json")

    with pytest.raises(ValueError, match="both evaluation arms"):
        store.require("rt-001")
    store.mark_complete("rt-001", EvaluationArmV234.V23_TEMPLATE_REGISTRATION_SEED)
    with pytest.raises(ValueError, match="both evaluation arms"):
        store.require("rt-001")
    store.mark_complete("rt-001", EvaluationArmV234.V234_LLM_FORMAL_REGISTRATION)

    assert store.require("rt-001").task_id == "rt-001"
    assert store.load_count == 1


def test_v234_deterministic_preflight_covers_all_32_paths(tmp_path: Path) -> None:
    artifact = run_runtime_preflight_v234(
        repository_root=ROOT,
        evaluation_root=EVALUATION_ROOT,
        local_root=tmp_path / "preflight",
    )

    assert artifact.task_count == 16
    assert artifact.arm_path_count == 32
    assert artifact.completed_arm_path_count == 32
    assert artifact.runtime_exceptions == 0
    assert artifact.invalid_authorization_transitions == 0
    assert artifact.unmapped_predicate_dsl_rules == 0
    assert artifact.invalid_clause_references == 0
    assert artifact.compiler_exceptions == 0
    assert artifact.premature_truth_reads == 0
    assert artifact.action_authority_violations == 0
    assert artifact.terminal == "DTA_V234_RUNTIME_PREFLIGHT_PASS"


def test_v234_deterministic_study_preview_scores_without_consuming_execution(
    tmp_path: Path,
) -> None:
    preview = run_deterministic_study_v234(
        repository_root=ROOT,
        evaluation_root=EVALUATION_ROOT,
        local_root=tmp_path / "study-preview",
    )

    assert preview.execution_count == 0
    assert preview.task_count == 16
    assert preview.run_count == 32
    assert preview.truth_load_count == 16
    assert preview.metrics.correct_new_implementation_mode_count == 4
    assert preview.metrics.duplicate_noise_non_promotable_count == 2
    assert preview.metrics.declarative_compiler_validity == 1.0
    assert preview.metrics.action_authority_violations == 0
