from __future__ import annotations

import json
from pathlib import Path
import tempfile

import pytest

from ecomsre.dta_v2.v23.cli import main
from ecomsre.dta_v2.v23.evaluation_data_v2341 import (
    EvaluationAdmissionV2341,
    load_evaluation_tasks_v2341,
)
from ecomsre.dta_v2.v23.evaluation_study_v2341 import (
    EvaluationArmV2341,
    LazyEvaluationTruthStoreV2341,
    MeasuredResultTerminalV2341,
    RuntimePreflightArtifactV2341,
    StudyMetricsV2341,
    run_runtime_preflight_v2341,
    score_measured_terminal_v2341,
)


ROOT = Path(__file__).resolve().parents[2]
EVALUATION_ROOT = ROOT / "config/dta-v2341/evaluation"


def test_final_data_is_exact_and_disjoint_from_predecessor_and_smoke() -> None:
    tasks = load_evaluation_tasks_v2341(EVALUATION_ROOT / "tasks.json")
    prior = []
    for path in (
        ROOT / "config/dta-v234/evaluation/tasks.json",
        ROOT / "config/dta-v2341/smoke/tasks.json",
    ):
        prior.extend(json.loads(path.read_text(encoding="utf-8"))["tasks"])
    prior_digests = {item["task_sha256"] for item in prior}

    assert len(tasks.tasks) == 16
    assert sum(item.provider_call_expected for item in tasks.tasks) == 14
    assert not prior_digests.intersection(
        item.task_sha256 for item in tasks.tasks
    )


def test_frozen_evaluation_admission_passes_without_provider_calls() -> None:
    admission = EvaluationAdmissionV2341.model_validate_json(
        (ROOT / "docs/analysis/dta-v2341-evaluation-data-admission.json").read_bytes()
    )

    assert admission.terminal == "DTA_V2341_EVALUATION_DATA_PASS"
    assert admission.catalog_feasibility_pass_count == 14
    assert admission.hidden_view_pass_count == 10
    assert admission.provider_calls == 0
    assert admission.task_digest_overlap_count == 0


def test_truth_requires_both_arms_before_each_load() -> None:
    store = LazyEvaluationTruthStoreV2341(EVALUATION_ROOT / "truth.json")

    with pytest.raises(ValueError, match="requires both evaluation arms"):
        store.require("rt-101")
    store.mark_complete("rt-101", EvaluationArmV2341.V23_TEMPLATE_REGISTRATION_SEED)
    with pytest.raises(ValueError, match="requires both evaluation arms"):
        store.require("rt-101")
    store.mark_complete("rt-101", EvaluationArmV2341.V2341_ALIAS_FORMAL_REGISTRATION)

    assert store.require("rt-101").task_id == "rt-101"
    assert store.load_count == 1


def test_runtime_preflight_recomputes_to_frozen_artifact() -> None:
    expected = RuntimePreflightArtifactV2341.model_validate_json(
        (ROOT / "docs/analysis/dta-v2341-runtime-preflight.json").read_bytes()
    )
    private_root = ROOT / ".local/dta-v2341"
    private_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="test-preflight-", dir=private_root) as raw:
        actual = run_runtime_preflight_v2341(
            repository_root=ROOT,
            evaluation_root=EVALUATION_ROOT,
            local_root=Path(raw),
        )

    assert actual == expected
    assert actual.completed_arm_path_count == 32
    assert actual.execution_count == 0


def _metrics(**overrides: object) -> StudyMetricsV2341:
    values: dict[str, object] = {
        "treatment_provider_schema_validity": 1.0,
        "treatment_alias_resolution_and_assembly_validity": 1.0,
        "first_pass_parse_rate": 1.0,
        "post_repair_parse_rate": 1.0,
        "unknown_alias_count": 0,
        "catalog_coverage_failures": 0,
        "canonical_order_failures": 0,
        "existing_format_structural_validity": 1.0,
        "hidden_known_mechanism_identity_accuracy": 1.0,
        "hidden_known_broad_domain_accuracy": 1.0,
        "core_predicate_reuse_precision": 1.0,
        "core_predicate_reuse_recall": 1.0,
        "hidden_known_behavioral_clause_equivalence": 1.0,
        "confusable_negative_coverage": 1.0,
        "correct_new_implementation_mode_count": 4,
        "declarative_ready_new_count": 3,
        "honest_engineering_required_count": 1,
        "duplicate_noise_non_promotable_count": 2,
        "duplicate_noise_false_promotion_count": 0,
        "declarative_compiler_validity": 1.0,
        "patch_bundle_completeness": 1.0,
        "shadow_evaluation_plan_completeness": 1.0,
        "evidence_ref_validity": 1.0,
        "core_known_regression": 0,
        "no_incident_regression": 0,
        "extension_overlap": 0,
        "remediation_registration_violations": 0,
        "action_authority_violations": 0,
        "provider_failures": 0,
        "provider_calls": 14,
        "protocol_repairs": 0,
        "transport_retries": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "latency_ms": 0.0,
    }
    values.update(overrides)
    return StudyMetricsV2341.model_validate(values)


def test_measured_terminal_thresholds_are_exact() -> None:
    assert score_measured_terminal_v2341(_metrics()) is (
        MeasuredResultTerminalV2341.EFFECT_OBSERVED
    )
    assert score_measured_terminal_v2341(
        _metrics(existing_format_structural_validity=0.80)
    ) is MeasuredResultTerminalV2341.MIXED_RESULT
    assert score_measured_terminal_v2341(
        _metrics(existing_format_structural_validity=0.70)
    ) is MeasuredResultTerminalV2341.NOT_OBSERVED


def test_ontology_fixed_evaluation_cli_is_deterministic_preflight(capsys) -> None:
    assert main(
        (
            "ontology",
            "evaluate",
            "--split",
            "v2341-fixed",
            "--repository-root",
            str(ROOT),
        )
    ) == 0
    artifact = RuntimePreflightArtifactV2341.model_validate_json(
        capsys.readouterr().out
    )

    assert artifact.terminal == "DTA_V2341_RUNTIME_PREFLIGHT_PASS"
    assert artifact.execution_count == 0
    assert artifact.completed_arm_path_count == 32
