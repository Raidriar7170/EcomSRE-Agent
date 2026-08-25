from __future__ import annotations

import json
from pathlib import Path

import pytest

from ecomsre.dta_v2.v22.predicates import MechanismV22
from ecomsre.dta_v2.v23.evaluation import (
    EvaluationCategoryV23,
    FixedEvaluationArtifactV23,
    LazyTruthStoreV23,
    MeasuredResultTerminalV23,
    build_evaluation_preflight_v23,
    load_evaluation_case_set_v23,
    load_evaluation_ontology_views_v23,
    load_evaluation_truth_set_v23,
    materialize_evaluation_case_v23,
    run_evaluation_case_pair_v23,
    score_measured_terminal_v23,
    verify_unregistered_case_has_no_known_terminal_v23,
)


ROOT = Path(__file__).resolve().parents[2]
EVAL = ROOT / "config/dta-v23/evaluation"


def test_fixed_denominator_and_composition_are_exact() -> None:
    cases = load_evaluation_case_set_v23(EVAL / "cases.json")
    truths = load_evaluation_truth_set_v23(EVAL / "truth.json")
    views = load_evaluation_ontology_views_v23(EVAL / "ontology-views.json")

    assert len(cases.cases) == 24
    assert len(truths.truths) == 24
    assert len(views.views) == 24
    counts = {
        category: sum(item.category is category for item in truths.truths)
        for category in EvaluationCategoryV23
    }
    assert counts == {
        EvaluationCategoryV23.NOVEL_HIDDEN: 10,
        EvaluationCategoryV23.NOVEL_UNREGISTERED: 4,
        EvaluationCategoryV23.REGISTERED_KNOWN: 4,
        EvaluationCategoryV23.NO_INCIDENT: 3,
        EvaluationCategoryV23.INSUFFICIENT_CONFLICT: 3,
    }
    hidden_counts = {
        mechanism: sum(item.hidden_mechanism is mechanism for item in views.views)
        for mechanism in (
            MechanismV22.CONFIGURATION_ERROR,
            MechanismV22.SERVICE_UNAVAILABLE,
            MechanismV22.CPU_SATURATION,
            MechanismV22.MEMORY_LEAK,
            MechanismV22.DEPENDENCY_LATENCY,
        )
    }
    assert set(hidden_counts.values()) == {2}
    assert sum(item.requires_discovery_read for item in truths.truths) >= 8
    assert sum(item.empty_or_misleading_action for item in truths.truths) >= 4
    pairs = {
        item.counterfactual_pair_id
        for item in truths.truths
        if item.counterfactual_pair_id is not None
    }
    assert len(pairs) >= 4
    assert all(
        sum(item.counterfactual_pair_id == pair for item in truths.truths) == 2
        for pair in pairs
    )


def test_all_case_material_is_typed_opaque_and_excludes_v226_capture() -> None:
    cases = load_evaluation_case_set_v23(EVAL / "cases.json")

    for spec in cases.cases:
        case = materialize_evaluation_case_v23(
            repository_root=ROOT,
            spec=spec,
        )
        assert case.case_id == spec.case_id
        assert all(
            service.startswith("svc-") and len(service) == 14
            for service in case.candidate_services
        )
        rendered = case.model_dump_json().casefold()
        assert "fault-map-a" not in rendered
        assert "dta-v226-real-fault" not in rendered


def test_unregistered_synthetic_cases_do_not_satisfy_registered_support() -> None:
    cases = load_evaluation_case_set_v23(EVAL / "cases.json")
    truths = load_evaluation_truth_set_v23(EVAL / "truth.json")
    by_truth = {item.case_id: item for item in truths.truths}

    for spec in cases.cases:
        if by_truth[spec.case_id].category is not EvaluationCategoryV23.NOVEL_UNREGISTERED:
            continue
        case = materialize_evaluation_case_v23(repository_root=ROOT, spec=spec)
        assert verify_unregistered_case_has_no_known_terminal_v23(case=case) is True


def test_truth_store_is_lazy_and_loads_one_case_only_on_request(tmp_path: Path) -> None:
    truth_path = tmp_path / "truth.json"
    truth_path.write_bytes((EVAL / "truth.json").read_bytes())
    store = LazyTruthStoreV23(truth_path)

    assert store.load_count == 0
    truth = store.load_case_after_both_arms("ow-001", arms_completed=2)
    assert truth.case_id == "ow-001"
    assert store.load_count == 1
    with pytest.raises(ValueError, match="both arms"):
        store.load_case_after_both_arms("ow-002", arms_completed=1)


def test_one_case_pair_shares_bytes_view_and_common_evidence_before_truth() -> None:
    cases = load_evaluation_case_set_v23(EVAL / "cases.json")
    views = load_evaluation_ontology_views_v23(EVAL / "ontology-views.json")
    store = LazyTruthStoreV23(EVAL / "truth.json")

    pair = run_evaluation_case_pair_v23(
        repository_root=ROOT,
        spec=cases.cases[0],
        view_spec=views.views[0],
        truth_store=store,
        provider_transport=None,
    )

    assert store.load_count == 1
    assert pair.closed_world.case_bytes_sha256 == pair.open_world.case_bytes_sha256
    assert pair.closed_world.active_view_sha256 == pair.open_world.active_view_sha256
    assert pair.closed_world.bootstrap_memory_sha256 == pair.open_world.bootstrap_memory_sha256
    assert pair.closed_world.common_memory_sha256 == pair.open_world.common_memory_sha256
    assert pair.closed_world.discovery_read_count == 0
    assert pair.closed_world.agent_writes == pair.open_world.agent_writes == 0
    assert pair.closed_world.runbook_executions == pair.open_world.runbook_executions == 0


def test_preflight_binds_files_and_blocks_existing_output(tmp_path: Path) -> None:
    progress = tmp_path / "docs/analysis/dta-v23-open-world-progress.json"
    progress.parent.mkdir(parents=True)
    progress.write_text(
        json.dumps({"fixed_evaluation_execution_count": 0}), encoding="utf-8"
    )
    output = tmp_path / "evaluation.json"
    preflight = build_evaluation_preflight_v23(
        repository_root=tmp_path,
        cases_path=EVAL / "cases.json",
        truth_path=EVAL / "truth.json",
        ontology_views_path=EVAL / "ontology-views.json",
        output_path=output,
        expected_provider_model="test-model",
    )

    assert preflight.case_count == 24
    assert preflight.planned_runs == 48
    assert preflight.execution_count_before == 0
    output.write_text("{}", encoding="utf-8")
    with pytest.raises(FileExistsError, match="write-once"):
        build_evaluation_preflight_v23(
            repository_root=tmp_path,
            cases_path=EVAL / "cases.json",
            truth_path=EVAL / "truth.json",
            ontology_views_path=EVAL / "ontology-views.json",
            output_path=output,
            expected_provider_model="test-model",
        )


@pytest.mark.parametrize(
    ("metrics", "expected"),
    (
        (
            {
                "novelty_recall": 0.8,
                "root_localization": 0.8,
                "broad_domain_accuracy": 0.7,
                "evidence_ref_validity": 0.95,
                "false_novel_rate": 0.1,
                "known_accuracy_drop_cases": 1,
                "no_incident_accuracy_drop_cases": 1,
                "action_authority_violations": 0,
            },
            MeasuredResultTerminalV23.EFFECT_OBSERVED,
        ),
        (
            {
                "novelty_recall": 0.6,
                "root_localization": 0.4,
                "broad_domain_accuracy": 0.4,
                "evidence_ref_validity": 0.85,
                "false_novel_rate": 0.4,
                "known_accuracy_drop_cases": 2,
                "no_incident_accuracy_drop_cases": 2,
                "action_authority_violations": 0,
            },
            MeasuredResultTerminalV23.MIXED_RESULT,
        ),
        (
            {
                "novelty_recall": 0.4,
                "root_localization": 0.9,
                "broad_domain_accuracy": 0.9,
                "evidence_ref_validity": 1.0,
                "false_novel_rate": 0.0,
                "known_accuracy_drop_cases": 0,
                "no_incident_accuracy_drop_cases": 0,
                "action_authority_violations": 0,
            },
            MeasuredResultTerminalV23.NOT_OBSERVED,
        ),
    ),
)
def test_measured_terminal_is_frozen_by_thresholds(
    metrics: dict[str, float | int],
    expected: MeasuredResultTerminalV23,
) -> None:
    assert score_measured_terminal_v23(**metrics) is expected


def test_agent_visible_case_set_has_no_truth_or_mechanism_labels() -> None:
    raw = json.loads((EVAL / "cases.json").read_text(encoding="utf-8"))
    rendered = json.dumps(raw, sort_keys=True).casefold()
    for forbidden in (
        "configuration_error",
        "service_unavailable",
        "cpu_saturation",
        "memory_leak",
        "dependency_latency",
        "expected_root",
        "truth",
    ):
        assert forbidden not in rendered


def test_fixed_result_is_frozen_valid_and_executed_once() -> None:
    result_path = ROOT / "docs/results/dta-v23-open-world-evaluation.json"
    artifact = FixedEvaluationArtifactV23.model_validate_json(result_path.read_bytes())

    assert artifact.execution_count == 1
    assert artifact.case_count == 24
    assert artifact.run_count == 48
    assert artifact.measured_result_terminal is MeasuredResultTerminalV23.MIXED_RESULT
    assert artifact.artifact_sha256 == (
        "e2bd2d41f8d2336225a10a97ae6222a9f3e1a52c00fe849137556f3e717aa9e5"
    )
    assert artifact.metrics.novelty_recall == pytest.approx(10 / 14)
    assert artifact.metrics.root_localization == pytest.approx(10 / 14)
    assert artifact.metrics.broad_domain_accuracy == pytest.approx(1 / 14)
    assert artifact.metrics.evidence_ref_validity == 1.0
    assert artifact.metrics.false_novel_rate == 0.1
    assert artifact.metrics.known_accuracy_drop_cases == 0
    assert artifact.metrics.no_incident_accuracy_drop_cases == 0
    assert artifact.metrics.action_authority_violations == 0
    assert artifact.agent_writes == 0
    assert artifact.runbook_executions == 0
    assert artifact.docker_calls == 0
    assert artifact.new_live_faults == 0
