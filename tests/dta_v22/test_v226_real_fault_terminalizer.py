from __future__ import annotations

from pathlib import Path

import pytest

from ecomsre.dta_v2.v22.contrastive_actions_v225 import (
    contrastive_resource_action_if_eligible_v225,
)
from ecomsre.dta_v2.v22.controller_contracts import build_hypothesis_catalog_v22
from ecomsre.dta_v2.v22.effective_policy_v222 import (
    build_effective_support_policy_v222,
)
from ecomsre.dta_v2.v22.gap_graph_v222 import build_gap_graph_v222
from ecomsre.dta_v2.v22.memory import build_memory_views_v22
from ecomsre.dta_v2.v22.read_contracts import EvidenceSourceV22
from ecomsre.dta_v2.v22.real_fault_action_backend_v225 import (
    RealFaultActionReadBackendV225,
)
from ecomsre.dta_v2.v22.real_fault_bootstrap_v226 import (
    build_real_fault_baseline_profile_v226,
    build_real_fault_canonical_bootstrap_v226,
    real_fault_run_id_v226,
)
from ecomsre.dta_v2.v22.real_fault_capture_v225 import RealFaultOpaqueCaptureV1
from ecomsre.dta_v2.v22.real_fault_terminalizer_v226 import (
    RealFaultTerminalKindV226,
    terminalize_real_fault_v226,
)
from ecomsre.dta_v2.v22.replay_target_coverage_v225 import (
    build_replay_target_coverage_v225,
)


ROOT = Path(__file__).resolve().parents[2]


def _capture(case_id: str) -> RealFaultOpaqueCaptureV1:
    path = ROOT / f"config/dta-v225-real-fault/captures/{case_id}.json"
    return RealFaultOpaqueCaptureV1.model_validate_json(path.read_text())


def _post_resource_state(case_id: str):
    capture = _capture(case_id)
    baseline_case = (
        f"baseline-{case_id.split('-', 1)[1]}"
        if case_id.startswith("fault-")
        else case_id
    )
    baseline_capture = _capture(baseline_case)
    run_id = real_fault_run_id_v226(capture)
    backend = RealFaultActionReadBackendV225.snapshot(
        capture=capture,
        run_id=run_id,
    )
    _bootstrap, outcomes = build_real_fault_canonical_bootstrap_v226(
        capture=capture,
        baseline_capture=baseline_capture,
        backend=backend,
    )
    baseline = build_real_fault_baseline_profile_v226(baseline_capture)
    pre_memory, _ = build_memory_views_v22(
        outcomes=outcomes,
        baseline=baseline,
        observed_at=capture.capture.captured_at,
        top_k=64,
    )
    hypotheses = build_hypothesis_catalog_v22(
        candidate_services=capture.candidate_aliases
    )
    policy = build_effective_support_policy_v222()
    pre_graph = build_gap_graph_v222(
        policy=policy,
        hypothesis_catalog=hypotheses,
        memory=pre_memory,
        topology_edges=(),
        planner_focus_hypothesis_id=None,
        prior_negative_coverage=(),
    )
    coverage = build_replay_target_coverage_v225(
        source=EvidenceSourceV22.RESOURCES,
        candidate_services=capture.candidate_aliases,
        covered_target_services=tuple(
            sorted(item.service for item in capture.capture.resources)
        ),
    )
    unresolved = sum(
        not hypothesis.complete
        and any(
            gap.predicate_kind.value.startswith("RESOURCE_")
            for clause in hypothesis.clauses
            for gap in clause.missing_requirements
        )
        for hypothesis in pre_graph.hypotheses
    )
    bundle = contrastive_resource_action_if_eligible_v225(
        coverage=coverage,
        resources_enabled=True,
        unresolved_resource_hypotheses=unresolved,
        remaining_budget=3.0,
        bundle_mode=True,
    )
    assert bundle is not None
    resource_outcome = backend.execute(bundle)
    post_outcomes = (*outcomes, resource_outcome)
    post_memory, _ = build_memory_views_v22(
        outcomes=post_outcomes,
        baseline=baseline,
        observed_at=capture.capture.captured_at,
        top_k=64,
    )
    post_graph = build_gap_graph_v222(
        policy=policy,
        hypothesis_catalog=hypotheses,
        memory=post_memory,
        topology_edges=(),
        planner_focus_hypothesis_id=None,
        prior_negative_coverage=(),
    )
    return capture, baseline, post_memory, post_graph


@pytest.mark.parametrize("case_id", ("fault-map-a", "fault-map-b"))
def test_shared_terminalizer_admits_exact_cpu_clause(case_id: str) -> None:
    capture, baseline, memory, graph = _post_resource_state(case_id)
    expected_root = next(
        item.service
        for item in capture.capture.resources
        if max(sample.cpu_percent for sample in item.samples) >= 80.0
    )

    result = terminalize_real_fault_v226(
        candidate_services=capture.candidate_aliases,
        baseline=baseline,
        memory=memory,
        gap_graph=graph,
        resource_covered_targets=capture.candidate_aliases,
        remaining_budget=1.0,
        required_source_failures=(),
        budget_prevented_required_coverage=False,
        conflicting_evidence=False,
    )

    assert len(result.terminal_candidates) == 1
    terminal = result.terminal_candidates[0]
    assert terminal.terminal_kind is RealFaultTerminalKindV226.CPU_SATURATION
    assert terminal.root_service_alias == expected_root
    assert terminal.mechanism == "CPU_SATURATION"
    assert terminal.evidence_clause_valid is True
    assert len(terminal.supporting_evidence_refs) >= 2


@pytest.mark.parametrize("case_id", ("baseline-map-a", "baseline-map-b"))
def test_no_incident_requires_both_resource_targets(case_id: str) -> None:
    capture, baseline, memory, graph = _post_resource_state(case_id)

    complete = terminalize_real_fault_v226(
        candidate_services=capture.candidate_aliases,
        baseline=baseline,
        memory=memory,
        gap_graph=graph,
        resource_covered_targets=capture.candidate_aliases,
        remaining_budget=1.0,
        required_source_failures=(),
        budget_prevented_required_coverage=False,
        conflicting_evidence=False,
    )
    incomplete = terminalize_real_fault_v226(
        candidate_services=capture.candidate_aliases,
        baseline=baseline,
        memory=memory,
        gap_graph=graph,
        resource_covered_targets=(capture.candidate_aliases[0],),
        remaining_budget=0.0,
        required_source_failures=(),
        budget_prevented_required_coverage=True,
        conflicting_evidence=False,
    )

    assert complete.terminal_candidates[0].terminal_kind is (
        RealFaultTerminalKindV226.NO_INCIDENT
    )
    assert complete.terminal_candidates[0].evidence_clause_valid is True
    assert incomplete.terminal_candidates[0].terminal_kind is (
        RealFaultTerminalKindV226.ABSTAIN
    )
    assert incomplete.terminal_candidates[0].admission_reason == "BUDGET_INCOMPLETE"


def test_required_source_failure_forces_typed_abstain() -> None:
    capture, baseline, memory, graph = _post_resource_state("baseline-map-a")

    result = terminalize_real_fault_v226(
        candidate_services=capture.candidate_aliases,
        baseline=baseline,
        memory=memory,
        gap_graph=graph,
        resource_covered_targets=capture.candidate_aliases,
        remaining_budget=1.0,
        required_source_failures=(EvidenceSourceV22.RESOURCES,),
        budget_prevented_required_coverage=False,
        conflicting_evidence=False,
    )

    assert result.terminal_candidates[0].terminal_kind is RealFaultTerminalKindV226.ABSTAIN
    assert result.terminal_candidates[0].admission_reason == "REQUIRED_SOURCE_FAILED"


def test_terminalizer_does_not_abstain_while_required_coverage_is_feasible() -> None:
    capture, baseline, memory, graph = _post_resource_state("baseline-map-a")

    result = terminalize_real_fault_v226(
        candidate_services=capture.candidate_aliases,
        baseline=baseline,
        memory=memory,
        gap_graph=graph,
        resource_covered_targets=(),
        remaining_budget=3.0,
        required_source_failures=(),
        budget_prevented_required_coverage=False,
        conflicting_evidence=False,
    )

    assert result.terminal_candidates == ()


def test_terminalizer_is_arm_independent_for_identical_memory() -> None:
    capture, baseline, memory, graph = _post_resource_state("fault-map-a")
    values = dict(
        candidate_services=capture.candidate_aliases,
        baseline=baseline,
        memory=memory,
        gap_graph=graph,
        resource_covered_targets=capture.candidate_aliases,
        remaining_budget=1.0,
        required_source_failures=(),
        budget_prevented_required_coverage=False,
        conflicting_evidence=False,
    )

    assert terminalize_real_fault_v226(**values) == terminalize_real_fault_v226(
        **values
    )
