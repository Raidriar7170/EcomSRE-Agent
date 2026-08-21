from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from scripts.ci.verify_dta_v224_historical_results import (
    DEFAULT_MANIFEST,
    verify_historical_results_v224,
)
from scripts.ci.verify_dta_v224_evaluation import verify_fixed_evaluation_v224
from ecomsre.dta_v2.v22.ambiguity_audit_v224 import (
    audit_v223_target_ambiguity_v224,
)
from ecomsre.dta_v2.v22.ambiguity_set_v224 import (
    build_resource_ambiguity_sets_v224,
)
from ecomsre.dta_v2.v22.ambiguity_dispatch_v224 import (
    ActionGranularityV224,
    dispatch_ambiguity_action_v224,
)
from ecomsre.dta_v2.v22.action_catalog import StaticTopologyV22
from ecomsre.dta_v2.v22.admission_dispatch_campaign_v223 import (
    load_frozen_predicate_yield_priors_v223,
)
from ecomsre.dta_v2.v22.ambiguity_bundle_campaign_v224 import (
    AmbiguityBundleCaseRunV224,
    AmbiguityBundleRunStatusV224,
    StudyCombinationV224,
    execute_ambiguity_bundle_case_v224,
)
from ecomsre.dta_v2.v22.ambiguity_bundle_scorer_v224 import (
    score_ambiguity_bundle_study_v224,
)
from ecomsre.dta_v2.v22.contrastive_actions_v224 import (
    build_contrastive_resource_delta_v224,
    contrastive_resource_action_if_eligible_v224,
)
from ecomsre.dta_v2.v22.controller_contracts import build_hypothesis_catalog_v22
from ecomsre.dta_v2.v22.effective_policy_v222 import build_effective_support_policy_v222
from ecomsre.dta_v2.v22.gap_graph_v222 import build_gap_graph_v222
from ecomsre.dta_v2.v22.memory import (
    BaselineProfileV22,
    PredicateKindV22,
    PredicateThresholdsV22,
    build_memory_views_v22,
)
from ecomsre.dta_v2.v22.negative_coverage_v222 import ReadUtilityClassV222
from ecomsre.dta_v2.v22.no_incident_set_closure_v224 import (
    NoIncidentClosureScopeV224,
    evaluate_no_incident_set_closure_v224,
    initial_no_incident_set_closure_state_v224,
    record_no_incident_set_closure_attempt_v224,
)
from ecomsre.dta_v2.v22.read_contracts import (
    EvidenceSourceV22,
    ReadSourceStatusV22,
    ResourceSampleV22,
    ResourceUsageRecordV22,
)
from ecomsre.dta_v2.v22.practical_dataset import (
    load_practical_case_set_v22,
    materialize_practical_case_v22,
)
from ecomsre.dta_v2.v22.offline_simulation_v223 import (
    _EvaluatorSelectionProviderV223,
)
from ecomsre.dta_v2.v22.practical_campaign import load_practical_truth_set_v22
from ecomsre.dta_v2.v22.practical_scorer import PracticalTruthV22
from ecomsre.dta_v2.v22.practical_runner import _baseline, _bootstrap
from ecomsre.dta_v2.v22.replay import QuerySpecificReplayBackendV22, ReplayCaptureV22
from ecomsre.dta_v2.v22.replay_target_coverage_v224 import (
    ReplayTargetCoverageModeV224,
    build_replay_target_coverage_v224,
    complete_resource_records_v224,
    load_replay_target_coverage_set_v224,
    normal_resource_record_v224,
    require_capture_matches_target_coverage_v224,
)


ROOT = Path(__file__).resolve().parents[2]
DEVELOPMENT_ROOT = ROOT / "config/dta-v22-4/development"


def test_v224_binds_every_merged_v22_through_v223_result_byte() -> None:
    assert verify_historical_results_v224(
        repository_root=ROOT,
        manifest_path=DEFAULT_MANIFEST,
    ) == 29


def test_v224_fixed_evaluation_is_new_complete_and_evaluator_feasible() -> None:
    report = verify_fixed_evaluation_v224(repository_root=ROOT)

    assert report["cases"] == 16
    assert report["resource_cases"] == 10
    assert report["counterfactual_resource_pairs"] == 4
    assert report["non_byte_identical_to_v223"] == 16
    assert report["infeasible_incident_cases"] == 0
    assert report["agent_writes"] == 0


def test_v224_audit_proves_the_three_frozen_wrong_target_cases_are_symmetric() -> None:
    report = audit_v223_target_ambiguity_v224(repository_root=ROOT)

    assert report.wrong_target_case_ids == ("d05", "d06", "d08")
    assert report.ambiguity_audit_passed is True
    by_id = {item.case_id: item for item in report.cases}
    for case_id in report.wrong_target_case_ids:
        item = by_id[case_id]
        assert item.resource_ambiguity_set_size == 2
        assert item.single_target_preference_available is False
        assert item.actual_resource_action_target != item.truth_target_service
        assert item.actual_outcome_class == "EMPTY_CAPTURED"
        assert item.actual_predicate_yield is False


def test_v224_counterfactual_pairs_reverse_truth_without_changing_visible_signature() -> None:
    report = audit_v223_target_ambiguity_v224(repository_root=ROOT)
    by_id = {item.case_id: item for item in report.cases}

    for left_id, right_id in (("d05", "d06"), ("d07", "d08")):
        left = by_id[left_id]
        right = by_id[right_id]
        assert left.mechanism == right.mechanism
        assert left.truth_target_ordinal != right.truth_target_ordinal
        assert len(set(left.target_visibility_signatures)) == 1
        assert len(set(right.target_visibility_signatures)) == 1


def test_v224_visibility_signature_declares_no_truth_or_future_fields() -> None:
    report = audit_v223_target_ambiguity_v224(repository_root=ROOT)
    assert report.signature_input_fields == (
        "runtime_predicates",
        "metric_predicates_and_support",
        "topology_role",
        "already_covered_sources",
        "current_gap_requirements",
        "negative_coverage",
    )
    assert set(report.signature_input_fields).isdisjoint(
        {"truth_target", "future_read_result", "case_id", "fixture_modifier"}
    )


def test_v224_target_complete_resources_requires_one_record_per_candidate() -> None:
    coverage = build_replay_target_coverage_v224(
        source=EvidenceSourceV22.RESOURCES,
        candidate_services=("checkout", "payment"),
        covered_target_services=("checkout", "payment"),
    )
    assert coverage.coverage_mode is ReplayTargetCoverageModeV224.TARGET_COMPLETE

    empty_capture = ReplayCaptureV22(
        schema_version="dta-v22.replay-capture.v1",
        captured_at=datetime(2026, 8, 21, tzinfo=timezone.utc),
        metrics=(),
        logs=(),
        traces=(),
        runtime=(),
        resources=(normal_resource_record_v224(service="checkout"),),
        changes=(),
        source_failures=(),
    )
    with pytest.raises(ValueError, match="TARGET_COMPLETE Resources coverage"):
        require_capture_matches_target_coverage_v224(
            coverage=coverage,
            capture=empty_capture,
        )


def test_v224_normal_resource_records_are_explicit_and_below_strong_thresholds() -> None:
    records = complete_resource_records_v224(
        candidate_services=("checkout", "payment"),
        records=(),
    )
    thresholds = PredicateThresholdsV22.frozen()

    assert tuple(item.service for item in records) == ("checkout", "payment")
    assert all(len(item.samples) == 5 for item in records)
    assert all(
        max(sample.cpu_percent for sample in item.samples)
        < thresholds.cpu_strong_p95_percent
        and item.memory_slope_bytes_per_second
        < thresholds.memory_growth_strong_bytes_per_second
        for item in records
    )


def test_v224_target_coverage_metadata_is_canonical_and_fail_closed() -> None:
    with pytest.raises(ValueError, match="canonical"):
        build_replay_target_coverage_v224(
            source=EvidenceSourceV22.RESOURCES,
            candidate_services=("payment", "checkout"),
            covered_target_services=("checkout",),
        )

    partial = build_replay_target_coverage_v224(
        source=EvidenceSourceV22.RESOURCES,
        candidate_services=("checkout", "payment"),
        covered_target_services=("checkout",),
    )
    assert partial.coverage_mode is ReplayTargetCoverageModeV224.TARGET_PARTIAL


def _cpu_fault_record(service: str) -> ResourceUsageRecordV22:
    return ResourceUsageRecordV22(
        schema_version="dta-v22.resource-usage-record.v1",
        service=service,
        sampling_window_seconds=10,
        samples=tuple(
            ResourceSampleV22(
                offset_ms=offset,
                cpu_percent=96.0,
                memory_bytes=100_000_000,
            )
            for offset in (0, 2_500, 5_000, 7_500, 10_000)
        ),
        memory_slope_bytes_per_second=0.0,
    )


def test_v224_contrastive_resource_action_is_stable_bounded_and_dominating() -> None:
    coverage = build_replay_target_coverage_v224(
        source=EvidenceSourceV22.RESOURCES,
        candidate_services=("checkout", "payment"),
        covered_target_services=("checkout", "payment"),
    )
    action = contrastive_resource_action_if_eligible_v224(
        coverage=coverage,
        resources_enabled=True,
        unresolved_resource_hypotheses=4,
        remaining_budget=3.0,
        bundle_mode=True,
    )
    assert action is not None
    assert action.action_id.startswith("a:resources:all-candidates:")
    assert action.target_services == ("checkout", "payment")
    assert action.weighted_cost == 2.0
    assert action.coverage_keys == (
        "resources:checkout:read",
        "resources:payment:read",
    )
    assert action.dominates_action_ids == (
        "a:resources:checkout",
        "a:resources:payment",
    )
    assert action == contrastive_resource_action_if_eligible_v224(
        coverage=coverage,
        resources_enabled=True,
        unresolved_resource_hypotheses=4,
        remaining_budget=3.0,
        bundle_mode=True,
    )


@pytest.mark.parametrize(
    ("resources_enabled", "unresolved", "budget", "bundle_mode"),
    (
        (False, 4, 3.0, True),
        (True, 1, 3.0, True),
        (True, 4, 1.5, True),
        (True, 4, 3.0, False),
    ),
)
def test_v224_bundle_is_absent_outside_its_declared_eligibility(
    resources_enabled: bool,
    unresolved: int,
    budget: float,
    bundle_mode: bool,
) -> None:
    coverage = build_replay_target_coverage_v224(
        source=EvidenceSourceV22.RESOURCES,
        candidate_services=("checkout", "payment"),
        covered_target_services=("checkout", "payment"),
    )
    assert contrastive_resource_action_if_eligible_v224(
        coverage=coverage,
        resources_enabled=resources_enabled,
        unresolved_resource_hypotheses=unresolved,
        remaining_budget=budget,
        bundle_mode=bundle_mode,
    ) is None


def test_v224_bundle_replay_returns_all_targets_and_predicates_only_the_anomaly() -> None:
    captured_at = datetime(2026, 8, 21, tzinfo=timezone.utc)
    coverage = build_replay_target_coverage_v224(
        source=EvidenceSourceV22.RESOURCES,
        candidate_services=("checkout", "payment"),
        covered_target_services=("checkout", "payment"),
    )
    action = contrastive_resource_action_if_eligible_v224(
        coverage=coverage,
        resources_enabled=True,
        unresolved_resource_hypotheses=4,
        remaining_budget=3.0,
        bundle_mode=True,
    )
    assert action is not None
    capture = ReplayCaptureV22(
        schema_version="dta-v22.replay-capture.v1",
        captured_at=captured_at,
        metrics=(),
        logs=(),
        traces=(),
        runtime=(),
        resources=(
            normal_resource_record_v224(service="checkout"),
            _cpu_fault_record("payment"),
        ),
        changes=(),
        source_failures=(),
    )
    require_capture_matches_target_coverage_v224(coverage=coverage, capture=capture)
    outcome = QuerySpecificReplayBackendV22(capture).execute(action)
    assert tuple(item.service for item in outcome.records) == ("checkout", "payment")

    baseline = BaselineProfileV22.build(
        metric_stats=(),
        trace_stats=(),
        resource_stats=(
            ("checkout", 20.0, 0.0),
            ("payment", 20.0, 0.0),
        ),
    )
    salient, _ = build_memory_views_v22(
        outcomes=(outcome,),
        baseline=baseline,
        observed_at=captured_at,
        top_k=64,
    )
    resource_facts = tuple(
        item for item in salient.salient_facts if item.source is EvidenceSourceV22.RESOURCES
    )
    assert tuple(sorted(item.service for item in resource_facts)) == (
        "checkout",
        "payment",
    )
    assert {
        (item.predicate_kind, item.service)
        for item in salient.predicates
        if item.source is EvidenceSourceV22.RESOURCES
    } == {(PredicateKindV22.RESOURCE_CPU_STRONG, "payment")}
    delta = build_contrastive_resource_delta_v224(
        action=action,
        before_memory=None,
        after_memory=salient,
    )
    assert tuple(item.service for item in delta.contrast_rows) == (
        "checkout",
        "payment",
    )
    assert delta.contrast_rows[0].new_predicate_kinds == ()
    assert delta.contrast_rows[1].new_predicate_kinds == (
        PredicateKindV22.RESOURCE_CPU_STRONG,
    )

    all_normal = capture.model_copy(
        update={
            "resources": (
                normal_resource_record_v224(service="checkout"),
                normal_resource_record_v224(service="payment"),
            )
        }
    )
    normal_outcome = QuerySpecificReplayBackendV22(all_normal).execute(action)
    assert normal_outcome.status is ReadSourceStatusV22.SUCCESS_NONEMPTY
    assert len(normal_outcome.records) == 2

    missing_target = capture.model_copy(
        update={"resources": (normal_resource_record_v224(service="checkout"),)}
    )
    schema_failure = QuerySpecificReplayBackendV22(missing_target).execute(action)
    assert schema_failure.status is ReadSourceStatusV22.FAILURE_SCHEMA
    assert schema_failure.records == ()


def _v223_resource_ambiguity_state():
    spec = next(
        item
        for item in load_practical_case_set_v22(
            ROOT / "config/dta-v22-3/evaluation/cases.json"
        ).cases
        if item.case_id == "d05"
    )
    case = materialize_practical_case_v22(spec=spec, repository_root=ROOT)
    topology = StaticTopologyV22.build(
        services=case.candidate_services,
        edges=case.topology_edges,
    )
    outcomes, _, _, catalog = _bootstrap(
        case=case,
        topology=topology,
        run_id="0" * 32,
    )
    memory, _ = build_memory_views_v22(
        outcomes=outcomes,
        baseline=_baseline(case),
        observed_at=case.capture.captured_at,
        top_k=64,
    )
    graph = build_gap_graph_v222(
        policy=build_effective_support_policy_v222(),
        hypothesis_catalog=build_hypothesis_catalog_v22(
            candidate_services=case.candidate_services
        ),
        memory=memory,
        topology_edges=case.topology_edges,
        planner_focus_hypothesis_id=None,
        prior_negative_coverage=(),
    )
    coverage = build_replay_target_coverage_v224(
        source=EvidenceSourceV22.RESOURCES,
        candidate_services=case.candidate_services,
        covered_target_services=case.candidate_services,
    )
    bundle = contrastive_resource_action_if_eligible_v224(
        coverage=coverage,
        resources_enabled=True,
        unresolved_resource_hypotheses=4,
        remaining_budget=3.0,
        bundle_mode=True,
    )
    assert bundle is not None
    individual = tuple(
        item
        for item in catalog.registry_actions
        if item.source is EvidenceSourceV22.RESOURCES
    )
    return case, memory, graph, individual, bundle


def test_v224_resource_ambiguity_set_tracks_partial_and_complete_target_coverage() -> None:
    case, memory, graph, individual, bundle = _v223_resource_ambiguity_state()

    initial = build_resource_ambiguity_sets_v224(
        memory=memory,
        gap_graph=graph,
        candidate_services=case.candidate_services,
        topology_edges=case.topology_edges,
        individual_actions=individual,
        bundle_action=bundle,
        covered_target_services=(),
    )
    assert len(initial) == 1
    ambiguity = initial[0]
    assert ambiguity.source is EvidenceSourceV22.RESOURCES
    assert ambiguity.target_services == case.candidate_services
    assert ambiguity.covered_target_services == ()
    assert ambiguity.remaining_target_services == case.candidate_services
    assert ambiguity.complete is False
    assert len(ambiguity.hypothesis_ids) == 4
    assert ambiguity.bundle_action_id == bundle.action_id

    partial = build_resource_ambiguity_sets_v224(
        memory=memory,
        gap_graph=graph,
        candidate_services=case.candidate_services,
        topology_edges=case.topology_edges,
        individual_actions=individual,
        bundle_action=bundle,
        covered_target_services=(case.candidate_services[0],),
    )[0]
    assert partial.covered_target_services == (case.candidate_services[0],)
    assert partial.remaining_target_services == (case.candidate_services[1],)
    assert partial.complete is False

    complete = build_resource_ambiguity_sets_v224(
        memory=memory,
        gap_graph=graph,
        candidate_services=case.candidate_services,
        topology_edges=case.topology_edges,
        individual_actions=individual,
        bundle_action=bundle,
        covered_target_services=case.candidate_services,
    )[0]
    assert complete.remaining_target_services == ()
    assert complete.complete is True
    assert complete.set_id == ambiguity.set_id
    assert complete.set_sha256 != ambiguity.set_sha256


def test_v224_one_target_baseline_reopens_after_one_normal_target() -> None:
    case, memory, graph, individual, bundle = _v223_resource_ambiguity_state()
    ambiguity = build_resource_ambiguity_sets_v224(
        memory=memory,
        gap_graph=graph,
        candidate_services=case.candidate_services,
        topology_edges=case.topology_edges,
        individual_actions=individual,
        bundle_action=bundle,
        covered_target_services=(),
    )[0]
    state = evaluate_no_incident_set_closure_v224(
        state=initial_no_incident_set_closure_state_v224(
            NoIncidentClosureScopeV224.ONE_TARGET_ATTEMPT
        ),
        legacy_no_incident_exposed=True,
        ambiguity_set=ambiguity,
        target_complete=True,
        remaining_evidence_budget=3.0,
        minimum_completion_cost=1.5,
    )
    assert state.no_incident_withheld is True
    after = record_no_incident_set_closure_attempt_v224(
        state=state,
        action=individual[0],
        outcome_class=ReadUtilityClassV222.NONEMPTY_NO_PREDICATE,
    )
    assert after.ambiguity_set is not None
    assert after.ambiguity_set.complete is False
    assert after.closure_satisfied is True
    assert after.no_incident_withheld is False


def test_v224_set_closure_stays_closed_until_all_normal_targets_are_covered() -> None:
    case, memory, graph, individual, bundle = _v223_resource_ambiguity_state()
    ambiguity = build_resource_ambiguity_sets_v224(
        memory=memory,
        gap_graph=graph,
        candidate_services=case.candidate_services,
        topology_edges=case.topology_edges,
        individual_actions=individual,
        bundle_action=bundle,
        covered_target_services=(),
    )[0]
    state = evaluate_no_incident_set_closure_v224(
        state=initial_no_incident_set_closure_state_v224(
            NoIncidentClosureScopeV224.AMBIGUITY_SET_COMPLETE
        ),
        legacy_no_incident_exposed=True,
        ambiguity_set=ambiguity,
        target_complete=True,
        remaining_evidence_budget=3.0,
        minimum_completion_cost=3.0,
    )
    first = record_no_incident_set_closure_attempt_v224(
        state=state,
        action=individual[0],
        outcome_class=ReadUtilityClassV222.NONEMPTY_NO_PREDICATE,
    )
    assert first.ambiguity_set is not None
    assert first.ambiguity_set.remaining_target_services == (
        case.candidate_services[1],
    )
    assert first.closure_satisfied is False
    assert first.no_incident_withheld is True

    second = record_no_incident_set_closure_attempt_v224(
        state=first,
        action=individual[1],
        outcome_class=ReadUtilityClassV222.NONEMPTY_NO_PREDICATE,
    )
    reopened = evaluate_no_incident_set_closure_v224(
        state=second,
        legacy_no_incident_exposed=True,
        ambiguity_set=second.ambiguity_set,
        target_complete=True,
        remaining_evidence_budget=0.0,
        minimum_completion_cost=0.0,
    )
    assert reopened.ambiguity_set is not None
    assert reopened.ambiguity_set.complete is True
    assert reopened.closure_satisfied is True
    assert reopened.no_incident_withheld is False


def test_v224_set_closure_yield_and_source_failure_have_distinct_effects() -> None:
    case, memory, graph, individual, bundle = _v223_resource_ambiguity_state()
    ambiguity = build_resource_ambiguity_sets_v224(
        memory=memory,
        gap_graph=graph,
        candidate_services=case.candidate_services,
        topology_edges=case.topology_edges,
        individual_actions=individual,
        bundle_action=bundle,
        covered_target_services=(),
    )[0]
    required = evaluate_no_incident_set_closure_v224(
        state=initial_no_incident_set_closure_state_v224(
            NoIncidentClosureScopeV224.AMBIGUITY_SET_COMPLETE
        ),
        legacy_no_incident_exposed=True,
        ambiguity_set=ambiguity,
        target_complete=True,
        remaining_evidence_budget=3.0,
        minimum_completion_cost=3.0,
    )
    yielded = record_no_incident_set_closure_attempt_v224(
        state=required,
        action=individual[0],
        outcome_class=ReadUtilityClassV222.PREDICATE_YIELD,
    )
    assert yielded.closure_satisfied is True
    assert yielded.no_incident_withheld is False

    failed = record_no_incident_set_closure_attempt_v224(
        state=required,
        action=individual[0],
        outcome_class=ReadUtilityClassV222.SOURCE_FAILURE,
    )
    assert failed.source_failure is True
    assert failed.closure_satisfied is False
    assert failed.no_incident_withheld is True


def test_v224_dispatch_is_sequential_per_target_and_one_read_for_bundle() -> None:
    case, memory, graph, individual, bundle = _v223_resource_ambiguity_state()
    ambiguity = build_resource_ambiguity_sets_v224(
        memory=memory,
        gap_graph=graph,
        candidate_services=case.candidate_services,
        topology_edges=case.topology_edges,
        individual_actions=individual,
        bundle_action=bundle,
        covered_target_services=(),
    )[0]
    ranking = tuple(item.action_id for item in individual)
    first = dispatch_ambiguity_action_v224(
        granularity=ActionGranularityV224.PER_TARGET,
        ambiguity_set=ambiguity,
        individual_actions=individual,
        bundle_action=bundle,
        ranked_action_ids=ranking,
        terminal_ids=(),
        remaining_evidence_budget=3.0,
    )
    assert first is not None
    assert first.action.target_services == (case.candidate_services[0],)

    partial = record_no_incident_set_closure_attempt_v224(
        state=evaluate_no_incident_set_closure_v224(
            state=initial_no_incident_set_closure_state_v224(
                NoIncidentClosureScopeV224.AMBIGUITY_SET_COMPLETE
            ),
            legacy_no_incident_exposed=True,
            ambiguity_set=ambiguity,
            target_complete=True,
            remaining_evidence_budget=3.0,
            minimum_completion_cost=3.0,
        ),
        action=first.action,
        outcome_class=ReadUtilityClassV222.NONEMPTY_NO_PREDICATE,
    )
    assert partial.ambiguity_set is not None
    second = dispatch_ambiguity_action_v224(
        granularity=ActionGranularityV224.PER_TARGET,
        ambiguity_set=partial.ambiguity_set,
        individual_actions=individual,
        bundle_action=bundle,
        ranked_action_ids=ranking,
        terminal_ids=(),
        remaining_evidence_budget=1.5,
    )
    assert second is not None
    assert second.action.target_services == (case.candidate_services[1],)

    bundled = dispatch_ambiguity_action_v224(
        granularity=ActionGranularityV224.CONTRASTIVE_BUNDLE,
        ambiguity_set=ambiguity,
        individual_actions=individual,
        bundle_action=bundle,
        ranked_action_ids=ranking,
        terminal_ids=(),
        remaining_evidence_budget=3.0,
    )
    assert bundled is not None
    assert bundled.action.action_id == bundle.action_id
    assert bundled.action.target_services == case.candidate_services


def _development_d05_runs() -> tuple[
    PracticalTruthV22, tuple[AmbiguityBundleCaseRunV224, ...]
]:
    specs = load_practical_case_set_v22(DEVELOPMENT_ROOT / "cases.json")
    truths = load_practical_truth_set_v22(DEVELOPMENT_ROOT / "truth.json")
    coverage = load_replay_target_coverage_set_v224(
        DEVELOPMENT_ROOT / "coverage.json"
    )
    priors = load_frozen_predicate_yield_priors_v223(
        ROOT / "config/dta-v22-3/development-predicate-yield-prior.json"
    )
    spec = next(item for item in specs.cases if item.case_id == "d05")
    truth = next(item for item in truths.truths if item.case_id == "d05")
    runs = tuple(
        execute_ambiguity_bundle_case_v224(
            spec=spec,
            coverage=coverage.require("d05"),
            repository_root=ROOT,
            combination=combination,
            provider=_EvaluatorSelectionProviderV223(
                truth=truth,
                oracle_action_ids=(),
            ),
            predicate_yield_priors=priors,
        )
        for combination in StudyCombinationV224
    )
    return truth, runs


def test_v224_development_resource_cases_are_explicitly_target_complete() -> None:
    specs = load_practical_case_set_v22(DEVELOPMENT_ROOT / "cases.json")
    coverage = load_replay_target_coverage_set_v224(
        DEVELOPMENT_ROOT / "coverage.json"
    )
    for spec in specs.cases:
        if spec.case_id not in {"d05", "d06", "d07", "d08", "d13"}:
            continue
        case = materialize_practical_case_v22(spec=spec, repository_root=ROOT)
        resource = coverage.require(spec.case_id).require(EvidenceSourceV22.RESOURCES)
        assert resource.coverage_mode is ReplayTargetCoverageModeV224.TARGET_COMPLETE
        assert tuple(item.service for item in case.capture.resources) == (
            case.candidate_services
        )


def test_v224_four_combinations_change_only_declared_factors() -> None:
    _, runs = _development_d05_runs()

    assert {run.combination for run in runs} == set(StudyCombinationV224)
    assert len({run.case_bytes_sha256 for run in runs}) == 1
    assert {run.action_granularity for run in runs} == {
        ActionGranularityV224.PER_TARGET,
        ActionGranularityV224.CONTRASTIVE_BUNDLE,
    }
    assert {run.closure_scope for run in runs} == {
        NoIncidentClosureScopeV224.ONE_TARGET_ATTEMPT,
        NoIncidentClosureScopeV224.AMBIGUITY_SET_COMPLETE,
    }
    assert all(run.provider_calls == 1 for run in runs)
    assert all(run.provider_terminal_selections == 1 for run in runs)
    assert all(run.agent_writes == 0 for run in runs)


def test_v224_target_set_recovers_and_bundle_set_covers_in_one_read() -> None:
    truth, runs = _development_d05_runs()
    by_name = {run.combination: run for run in runs}

    target_one = by_name[StudyCombinationV224.TARGET_ONE]
    assert target_one.terminal == "NO_INCIDENT"
    assert target_one.set_complete_before_terminal is False

    target_set = by_name[StudyCombinationV224.TARGET_SET]
    assert target_set.status is AmbiguityBundleRunStatusV224.VALID_TERMINAL
    assert target_set.terminal == truth.expected_terminal
    assert target_set.root_service == truth.expected_root_service
    assert target_set.individual_resources_reads == 2
    assert target_set.set_complete_before_terminal is True
    assert target_set.no_incident_exposed_after_partial_coverage is False

    bundle_set = by_name[StudyCombinationV224.BUNDLE_SET]
    assert bundle_set.terminal == truth.expected_terminal
    assert bundle_set.root_service == truth.expected_root_service
    assert bundle_set.bundle_resources_reads == 1
    assert bundle_set.individual_resources_reads == 0
    assert bundle_set.targets_covered_before_terminal == (
        "pair-resource-a",
        "pair-resource-b",
    )


def test_v224_factorial_scorer_uses_case_and_resource_denominators() -> None:
    truth, runs = _development_d05_runs()
    score = score_ambiguity_bundle_study_v224(
        runs=runs,
        truths=(truth,),
        include_development_gate=False,
        include_interpretation=True,
    )
    by_name = {item.combination: item for item in score.combinations}

    assert by_name[StudyCombinationV224.TARGET_ONE].total_runs == 1
    assert (
        by_name[StudyCombinationV224.TARGET_ONE].resource_ambiguity_denominator
        == 1
    )
    assert (
        by_name[StudyCombinationV224.TARGET_ONE].resource_ambiguity_exact_accuracy
        == 0.0
    )
    assert (
        by_name[StudyCombinationV224.TARGET_SET].resource_ambiguity_exact_accuracy
        == 1.0
    )
    assert (
        by_name[StudyCombinationV224.BUNDLE_SET].mean_resources_reads_per_resource_case
        == 1.0
    )
    assert score.interpretation is not None
    assert score.interpretation.closure_main_effect.resource_ambiguity_accuracy_improvement == 0.5
