from __future__ import annotations

from pathlib import Path

import pytest

from ecomsre.dta_v2.v22.action_catalog import (
    StaticTopologyV22,
    build_action_catalog_v22,
    build_default_tool_capability_registry_v22,
)
from ecomsre.dta_v2.v22.ambiguity_set_v225 import (
    build_resource_ambiguity_sets_v225,
)
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
from ecomsre.dta_v2.v22.real_fault_bundle_arm_v225 import (
    _baseline,
    _bootstrap,
    _run_id,
    _source_failure,
)
from ecomsre.dta_v2.v22.real_fault_capture_v225 import RealFaultOpaqueCaptureV1
from ecomsre.dta_v2.v22.replay_target_coverage_v225 import (
    build_replay_target_coverage_v225,
)
from ecomsre.dta_v2.v22.resource_comparison_set_v226 import (
    build_resource_comparison_set_v226,
)


ROOT = Path(__file__).resolve().parents[2]
CASE_IDS = (
    "fault-map-a",
    "fault-map-b",
    "baseline-map-a",
    "baseline-map-b",
)


def _capture(case_id: str) -> RealFaultOpaqueCaptureV1:
    path = ROOT / f"config/dta-v225-real-fault/captures/{case_id}.json"
    return RealFaultOpaqueCaptureV1.model_validate_json(path.read_text())


def _pre_read_state(case_id: str):
    capture = _capture(case_id)
    baseline_case = (
        f"baseline-{case_id.split('-', 1)[1]}"
        if case_id.startswith("fault-")
        else case_id
    )
    baseline_capture = _capture(baseline_case)
    run_id = _run_id(capture)
    topology = StaticTopologyV22.build(services=capture.candidate_aliases, edges=())
    backend = RealFaultActionReadBackendV225.snapshot(capture=capture, run_id=run_id)
    outcomes, executed = _bootstrap(
        capture=capture,
        baseline_capture=baseline_capture,
        topology=topology,
        run_id=run_id,
        backend=backend,
    )
    memory, _ = build_memory_views_v22(
        outcomes=outcomes,
        baseline=_baseline(baseline_capture),
        observed_at=capture.capture.captured_at,
        top_k=64,
    )
    hypotheses = build_hypothesis_catalog_v22(
        candidate_services=capture.candidate_aliases
    )
    graph = build_gap_graph_v222(
        policy=build_effective_support_policy_v222(),
        hypothesis_catalog=hypotheses,
        memory=memory,
        topology_edges=(),
        planner_focus_hypothesis_id=None,
        prior_negative_coverage=(),
    )
    catalog = build_action_catalog_v22(
        candidate_services=capture.candidate_aliases,
        topology=topology,
        capability_registry=build_default_tool_capability_registry_v22(),
        executed_action_ids=executed,
        remaining_budget=3.0,
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
        for hypothesis in graph.hypotheses
    )
    bundle = contrastive_resource_action_if_eligible_v225(
        coverage=coverage,
        resources_enabled=(
            _source_failure(capture=capture, source=EvidenceSourceV22.RESOURCES)
            is None
        ),
        unresolved_resource_hypotheses=unresolved,
        remaining_budget=3.0,
        bundle_mode=True,
    )
    resource_actions = tuple(
        action
        for action in catalog.registry_actions
        if action.source is EvidenceSourceV22.RESOURCES
    )
    strict_sets = build_resource_ambiguity_sets_v225(
        memory=memory,
        gap_graph=graph,
        candidate_services=capture.candidate_aliases,
        topology_edges=(),
        individual_actions=resource_actions,
        bundle_action=bundle,
        covered_target_services=(),
    )
    return capture, memory, graph, resource_actions, bundle, strict_sets


@pytest.mark.parametrize("case_id", CASE_IDS)
def test_old_real_capture_builds_comparison_set_without_strict_ambiguity(
    case_id: str,
) -> None:
    capture, memory, graph, actions, bundle, strict_sets = _pre_read_state(case_id)

    assert bundle is not None
    assert strict_sets == ()
    comparison = build_resource_comparison_set_v226(
        memory=memory,
        gap_graph=graph,
        candidate_services=capture.candidate_aliases,
        topology_edges=(),
        individual_actions=actions,
        bundle_action=bundle,
        target_complete=True,
        covered_targets=(),
    )

    assert comparison is not None
    assert comparison.candidate_services == capture.candidate_aliases
    assert comparison.strictly_ambiguous is False
    assert comparison.strict_ambiguity_set_id is None
    assert comparison.target_complete is True
    assert comparison.covered_targets == ()
    assert comparison.remaining_targets == capture.candidate_aliases
    assert len(comparison.individual_action_ids) == 2
    assert comparison.bundle_action_id == bundle.action_id
    assert comparison.set_sha256 == comparison.recompute_sha256()


def test_comparison_set_requires_target_complete_resources() -> None:
    capture, memory, graph, actions, bundle, _ = _pre_read_state("fault-map-a")

    comparison = build_resource_comparison_set_v226(
        memory=memory,
        gap_graph=graph,
        candidate_services=capture.candidate_aliases,
        topology_edges=(),
        individual_actions=actions,
        bundle_action=bundle,
        target_complete=False,
        covered_targets=(),
    )

    assert comparison is None
