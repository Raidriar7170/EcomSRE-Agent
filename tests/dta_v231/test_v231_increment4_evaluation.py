from __future__ import annotations

from collections import Counter
import inspect
import json
from pathlib import Path

from ecomsre.dta_v2.v23.evaluation import (
    load_evaluation_case_set_v23,
    materialize_evaluation_case_v23,
)
from ecomsre.dta_v2.v23.evaluation_v231 import (
    EvaluationCategoryV231,
    EvaluationCasePairV231,
    EvaluationMetricsV231,
    build_evaluation_preflight_v231,
    load_evaluation_case_set_v231,
    load_evaluation_truth_set_v231,
    load_evaluation_views_v231,
    materialize_evaluation_case_v231,
    run_evaluation_policy_v231,
    run_fixed_evaluation_once_v231,
    score_measured_terminal_v231,
)
from ecomsre.dta_v2.v23.cli import build_parser


ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "config/dta-v231/evaluation"


def test_new_fixed_set_has_the_approved_conflict_composition() -> None:
    cases = load_evaluation_case_set_v231(CONFIG / "cases.json")
    truths = load_evaluation_truth_set_v231(CONFIG / "truth.json")
    views = load_evaluation_views_v231(CONFIG / "ontology-views.json")

    assert len(cases.cases) == len(truths.truths) == len(views.views) == 24
    counts = Counter(item.category for item in truths.truths)
    assert counts == {
        EvaluationCategoryV231.NOVEL_HIDDEN: 10,
        EvaluationCategoryV231.NOVEL_UNREGISTERED: 4,
        EvaluationCategoryV231.REGISTERED_KNOWN: 4,
        EvaluationCategoryV231.NO_INCIDENT: 3,
        EvaluationCategoryV231.INSUFFICIENT_CONFLICT: 3,
    }
    assert sum(item.conflict_prone_novelty for item in truths.truths) == 8
    assert sum(item.multi_coherent_interpretations for item in truths.truths) >= 4
    assert sum(item.true_irreconcilable_conflict for item in truths.truths) == 3
    assert sum(item.requires_discovery_read for item in truths.truths) >= 8


def test_new_fixed_case_bytes_do_not_reuse_any_v23_case_bytes() -> None:
    old_specs = load_evaluation_case_set_v23(
        ROOT / "config/dta-v23/evaluation/cases.json"
    )
    old_hashes = {
        materialize_evaluation_case_v23(repository_root=ROOT, spec=spec).source_bytes_sha256
        for spec in old_specs.cases
    }
    new_specs = load_evaluation_case_set_v231(CONFIG / "cases.json")
    new_cases = tuple(
        materialize_evaluation_case_v231(repository_root=ROOT, spec=spec)
        for spec in new_specs.cases
    )

    assert len({item.source_bytes_sha256 for item in new_cases}) == 24
    assert old_hashes.isdisjoint(item.source_bytes_sha256 for item in new_cases)
    assert all(item.case_id.startswith("vx-") for item in new_cases)


def test_new_fixed_cases_are_observer_complete_and_do_not_reference_v23_templates() -> None:
    raw = json.loads((CONFIG / "cases.json").read_text(encoding="utf-8"))

    assert raw["schema_version"] == "dta-v231.evaluation-case-set.v1"
    assert all("capture" in item for item in raw["cases"])
    assert all("candidate_services" in item for item in raw["cases"])
    assert all("template_case_id" not in item for item in raw["cases"])
    assert all("projection_id" not in item for item in raw["cases"])
    assert "materialize_evaluation_case_v23(" not in inspect.getsource(
        materialize_evaluation_case_v231
    )


def test_terminal_thresholds_preserve_baseline_improvement_requirement() -> None:
    positive = score_measured_terminal_v231(
        baseline_novelty_recall=0.43,
        treatment_novelty_recall=0.72,
        conflict_prone_novelty_recall=0.75,
        root_localization=0.68,
        broad_domain_accuracy=0.57,
        evidence_ref_validity=0.95,
        false_novel_rate=0.10,
        known_accuracy_drop_cases=0,
        no_incident_accuracy_drop_cases=0,
        true_conflict_converted_cases=0,
        action_authority_violations=0,
    )
    not_observed = score_measured_terminal_v231(
        baseline_novelty_recall=0.60,
        treatment_novelty_recall=0.70,
        conflict_prone_novelty_recall=0.75,
        root_localization=0.68,
        broad_domain_accuracy=0.57,
        evidence_ref_validity=0.95,
        false_novel_rate=0.10,
        known_accuracy_drop_cases=0,
        no_incident_accuracy_drop_cases=0,
        true_conflict_converted_cases=0,
        action_authority_violations=0,
    )

    assert positive.value == "DTA_V231_CONFLICT_AWARE_DISCOVERY_EFFECT_OBSERVED"
    assert not_observed.value == "DTA_V231_CONFLICT_AWARE_DISCOVERY_NOT_OBSERVED"


def test_fixed_set_is_frozen_without_executing_either_policy_arm() -> None:
    cases = load_evaluation_case_set_v231(CONFIG / "cases.json")

    assert cases.freeze_id == "dta-v231-independent-freeze-20260825-c"
    assert len({item.source_bytes_sha256 for item in cases.cases}) == 24


def test_preflight_and_single_policy_cli_do_not_load_evaluator_truth_early() -> None:
    preflight_source = inspect.getsource(build_evaluation_preflight_v231)
    single_policy_source = inspect.getsource(run_evaluation_policy_v231)
    args = build_parser().parse_args(
        ["diagnose", "--case", "vx-001", "--conflict-policy", "strict"]
    )

    assert "load_evaluation_truth_set_v231(" not in preflight_source
    assert "load_evaluation_truth_set_v231(" not in single_policy_source
    assert "run_evaluation_case_pair_v231(" not in single_policy_source
    assert args.conflict_policy == "strict"

    audit_args = build_parser().parse_args(
        ["conflict-audit", "--split", "v23-fixed"]
    )
    show_args = build_parser().parse_args(
        ["conflict", "show", "--case", "case.json"]
    )
    assert audit_args.split == "v23-fixed"
    assert show_args.case == "case.json"


def test_pair_contract_binds_shared_known_admission() -> None:
    source = inspect.getsource(EvaluationCasePairV231.require_pair)

    assert '"known_admission_sha256"' in source


def test_fixed_scorer_exposes_goal_defined_conflict_and_report_metrics() -> None:
    required = {
        "non_conflict_treatment_recall",
        "treatment_hard_conflict_rate_on_novelty",
        "treatment_residual_anomaly_citation_validity",
        "competing_hypothesis_evidence_validity",
        "leading_hypothesis_root_validity",
        "alternative_hypothesis_completeness",
        "unresolved_question_completeness",
        "insufficient_conflict_treatment_accuracy",
        "no_conflict_count",
        "coherent_competition_count",
        "resolvable_conflict_count",
        "irreconcilable_conflict_count",
        "discriminating_read_anomaly_yield",
        "post_read_conflict_resolution_rate",
        "persistent_competition_report_rate",
    }

    assert required.issubset(EvaluationMetricsV231.model_fields)


def test_fixed_runner_closes_both_outputs_before_complete_sentinel() -> None:
    source = inspect.getsource(run_fixed_evaluation_once_v231)

    json_write = source.index('with output_path.open("x"')
    markdown_write = source.index('with output_markdown_path.open("x"')
    complete_write = source.index('"status": "COMPLETE"')
    assert json_write < markdown_write < complete_write
    assert 'or output_markdown_path.exists()' in source
    assert '"output_json_sha256"' in source
    assert '"output_markdown_sha256"' in source
