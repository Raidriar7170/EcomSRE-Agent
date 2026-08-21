from __future__ import annotations

from pathlib import Path

import pytest

from scripts.ci.verify_dta_v222_historical_results import (
    DEFAULT_MANIFEST,
    verify_historical_results_v222,
)
from ecomsre.dta_v2.v22.evidence_utility_audit_v222 import (
    ShortestAdmissiblePathV222,
    audit_case_set_v222,
    evaluate_development_routing_gate_v222,
)
from ecomsre.dta_v2.v22.action_catalog import StaticTopologyV22
from ecomsre.dta_v2.v22.controller_contracts import build_hypothesis_catalog_v22
from ecomsre.dta_v2.v22.effective_policy_v222 import (
    build_effective_support_policy_v222,
)
from ecomsre.dta_v2.v22.gap_graph_v222 import build_gap_graph_v222
from ecomsre.dta_v2.v22.gap_router_v222 import (
    GapRouterModeV222,
    route_gap_aware_actions_v222,
)
from ecomsre.dta_v2.v22.memory import build_memory_views_v22
from ecomsre.dta_v2.v22.practical_dataset import (
    load_practical_case_set_v22,
    materialize_practical_case_v22,
)
from ecomsre.dta_v2.v22.practical_runner import _baseline, _bootstrap
from ecomsre.dta_v2.v22.replay_capabilities_v222 import (
    ReplaySourceAvailabilityV222,
    build_replay_capabilities_v222,
    build_source_aware_action_catalog_v222,
)
from ecomsre.dta_v2.v22.read_contracts import EvidenceSourceV22


ROOT = Path(__file__).resolve().parents[2]


def test_v222_binds_merged_v22_and_v221_result_bytes() -> None:
    assert verify_historical_results_v222(
        repository_root=ROOT,
        manifest_path=DEFAULT_MANIFEST,
    ) == 6


def test_v222_historical_verifier_fails_closed_on_drift(tmp_path: Path) -> None:
    result_root = tmp_path / "repo"
    result_root.mkdir()
    for relative in (
        "docs/results/dta-v22-practical-evaluation.json",
        "docs/results/dta-v22-practical-evaluation.md",
        "docs/results/dta-v22-practical-error-analysis.md",
        "docs/results/dta-v22-1-evidence-acquisition-study.json",
        "docs/results/dta-v22-1-evidence-acquisition-study.md",
        "docs/results/dta-v22-1-evidence-acquisition-error-analysis.md",
    ):
        target = result_root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((ROOT / relative).read_bytes())
    drifted = result_root / "docs/results/dta-v22-1-evidence-acquisition-study.json"
    drifted.write_bytes(drifted.read_bytes() + b"\n")

    with pytest.raises(ValueError, match="historical DTA v2.2 result drift"):
        verify_historical_results_v222(
            repository_root=result_root,
            manifest_path=DEFAULT_MANIFEST,
        )


def test_merged_twelve_case_portfolio_has_feasible_incident_evidence_paths() -> None:
    report = audit_case_set_v222(
        repository_root=ROOT,
        case_set_path=ROOT / "config/dta-v22-sprint/evaluation/cases.json",
        truth_path=ROOT / "config/dta-v22-sprint/evaluation/truth.json",
    )

    incident = {item.case_id: item for item in report.cases if item.expected_terminal == "DIAGNOSED"}
    assert set(incident) == {f"e{index:02d}" for index in range(1, 9)}
    assert all(
        item.shortest_admissible_path
        in {ShortestAdmissiblePathV222.ZERO, ShortestAdmissiblePathV222.ONE}
        for item in incident.values()
    )
    assert all(item.shortest_action_ids is not None for item in incident.values())
    assert all(item.actions for item in report.cases)
    assert all(
        action.source_captured in {True, False}
        and action.read_status
        and action.support_clause_gaps_closed >= 0
        for item in report.cases
        for action in item.actions
    )
    assert report.infeasible_incident_cases == 0
    assert report.oracle_visible_to_provider is False


def _development_case(case_id: str):
    case_set = load_practical_case_set_v22(
        ROOT / "config/dta-v22-sprint/evaluation/cases.json"
    )
    spec = next(item for item in case_set.cases if item.case_id == case_id)
    return spec, materialize_practical_case_v22(spec=spec, repository_root=ROOT)


def test_source_availability_masks_only_not_captured_and_keeps_empty_sources() -> None:
    spec, case = _development_case("e01")
    capabilities = build_replay_capabilities_v222(
        spec=spec,
        repository_root=ROOT,
    )
    assert capabilities.require(EvidenceSourceV22.CHANGES).availability is (
        ReplaySourceAvailabilityV222.NOT_CAPTURED
    )
    assert capabilities.require(EvidenceSourceV22.LOGS).availability is (
        ReplaySourceAvailabilityV222.CAPTURED
    )

    topology = StaticTopologyV22.build(
        services=case.candidate_services,
        edges=case.topology_edges,
    )
    catalog = build_source_aware_action_catalog_v222(
        candidate_services=case.candidate_services,
        topology=topology,
        replay_capabilities=capabilities,
        executed_action_ids=(),
        covered_capability_keys=(),
        remaining_budget=3.0,
    )
    assert "a:logs:payment" in {item.action_id for item in catalog.actions}
    changes = next(
        item for item in catalog.masked_actions if item.action_id == "a:changes:payment"
    )
    assert changes.reason.value == "SOURCE_UNAVAILABLE"


def test_effective_policy_gap_graph_and_router_share_practical_clause() -> None:
    spec, case = _development_case("e01")
    topology = StaticTopologyV22.build(
        services=case.candidate_services,
        edges=case.topology_edges,
    )
    outcomes, _, _, _ = _bootstrap(case=case, topology=topology, run_id="0" * 32)
    memory, _ = build_memory_views_v22(
        outcomes=outcomes,
        baseline=_baseline(case),
        observed_at=case.capture.captured_at,
        top_k=64,
    )
    policy = build_effective_support_policy_v222()
    assert len(policy.clauses) == 11
    assert {
        "configuration:error-metric-and-first-error-trace",
        "memory-leak:growth-and-healthy",
    }.issubset({item.clause_id for item in policy.clauses})
    hypotheses = build_hypothesis_catalog_v22(candidate_services=case.candidate_services)
    graph = build_gap_graph_v222(
        policy=policy,
        hypothesis_catalog=hypotheses,
        memory=memory,
        topology_edges=case.topology_edges,
        planner_focus_hypothesis_id=None,
        prior_negative_coverage=(),
    )
    configuration = next(
        item
        for item in graph.hypotheses
        if item.mechanism.value == "CONFIGURATION_ERROR"
    )
    practical = next(
        item
        for item in configuration.clauses
        if item.clause_id == "configuration:error-metric-and-first-error-trace"
    )
    assert practical.missing_count == 1
    assert practical.missing_requirements[0].predicate_kind.value == "TRACE_FIRST_ERROR"

    replay_capabilities = build_replay_capabilities_v222(
        spec=spec,
        repository_root=ROOT,
    )
    bootstrap_ids = tuple(item.action_id for item in outcomes)
    catalog = build_source_aware_action_catalog_v222(
        candidate_services=case.candidate_services,
        topology=topology,
        replay_capabilities=replay_capabilities,
        executed_action_ids=bootstrap_ids,
        covered_capability_keys=(),
        remaining_budget=3.0,
    )
    routed = route_gap_aware_actions_v222(
        mode=GapRouterModeV222.GAP_RANKED_TOP_K,
        catalog=catalog,
        gap_graph=graph,
        prior_negative_coverage=(),
        top_k=4,
    )
    assert "a:traces:payment" in {item.action_id for item in routed.actions}
    assert len(routed.actions) <= 4
    assert routed.truth_consulted is False


def test_broad_and_gap_modes_share_source_aware_catalog() -> None:
    spec, case = _development_case("e05")
    topology = StaticTopologyV22.build(
        services=case.candidate_services,
        edges=case.topology_edges,
    )
    outcomes, _, _, _ = _bootstrap(case=case, topology=topology, run_id="0" * 32)
    memory, _ = build_memory_views_v22(
        outcomes=outcomes,
        baseline=_baseline(case),
        observed_at=case.capture.captured_at,
        top_k=64,
    )
    catalog = build_source_aware_action_catalog_v222(
        candidate_services=case.candidate_services,
        topology=topology,
        replay_capabilities=build_replay_capabilities_v222(
            spec=spec,
            repository_root=ROOT,
        ),
        executed_action_ids=tuple(item.action_id for item in outcomes),
        covered_capability_keys=(),
        remaining_budget=3.0,
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
    broad = route_gap_aware_actions_v222(
        mode=GapRouterModeV222.BROAD_CATALOG,
        catalog=catalog,
        gap_graph=graph,
        prior_negative_coverage=(),
        top_k=4,
    )
    gap = route_gap_aware_actions_v222(
        mode=GapRouterModeV222.GAP_RANKED_TOP_K,
        catalog=catalog,
        gap_graph=graph,
        prior_negative_coverage=(),
        top_k=4,
    )
    assert tuple(item.action_id for item in broad.actions) == tuple(
        item.action_id for item in catalog.actions
    )
    assert gap.actions[0].action_id == "a:resources:email"


def test_development_top_four_routing_recall_gate_passes() -> None:
    gate = evaluate_development_routing_gate_v222(
        repository_root=ROOT,
        case_set_path=ROOT / "config/dta-v22-sprint/evaluation/cases.json",
        truth_path=ROOT / "config/dta-v22-sprint/evaluation/truth.json",
    )
    assert gate.turn_zero_recall >= 0.80
    assert gate.post_first_read_recall >= 0.75
    assert gate.gate_passed is True
    assert gate.oracle_visible_to_provider is False
