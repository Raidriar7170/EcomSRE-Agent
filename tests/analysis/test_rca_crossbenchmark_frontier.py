from __future__ import annotations

from pathlib import Path

import pytest

from ecomsre_rca_unified.contracts import (
    ArchitectureOption,
    CanonicalEntityLayer,
    CausalCandidate,
    FaultOntologyClass,
    FrontierCase,
    PropagationDisposition,
)
from ecomsre_rca_unified.frontier import (
    aggregate_outcomes,
    apply_option,
    grouped_robustness,
    load_frontier,
    select_architecture,
)
from ecomsre_rca_unified.hierarchy import EntityHierarchy, EntityNode
from ecomsre_rca_unified.propagation import (
    EvidenceGraph,
    first_marked_log_anomaly,
    first_metric_anomaly,
    first_trace_anomaly,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
FRONTIER_PATH = (
    PROJECT_ROOT
    / "config/rca-crossbenchmark-architecture-convergence-v1/frontier.json"
)


def _case(**updates: object) -> FrontierCase:
    values: dict[str, object] = {
        "private_case_key": "private-1",
        "benchmark": "RCA100",
        "system": "RCA100",
        "fault_family": "cpu",
        "fault_regime": FaultOntologyClass.LOCAL_RESOURCE,
        "metric_family": "CPU",
        "ground_truth_entity": "service|truth",
        "ground_truth_equivalent_entities": frozenset({"service|truth"}),
        "ground_truth_service": "service|truth",
        "initial_entity": "service|initial",
        "initial_service": "service|initial",
        "initial_fault_type": "cpu exhaustion",
        "initial_pair_correct": False,
        "initial_layer": CanonicalEntityLayer.SERVICE,
        "metrics_top1": "service|truth",
        "metrics_top1_service": "service|truth",
        "metrics_top1_layer": CanonicalEntityLayer.SERVICE,
        "metrics_initial_rank": 4,
        "metrics_margin": 0.5,
        "metrics_top1_is_downstream": False,
        "propagation_disposition": PropagationDisposition.ABSENT,
        "causal_candidates": (),
    }
    values.update(updates)
    return FrontierCase(**values)  # type: ignore[arg-type]


def test_frozen_frontier_has_exactly_a0_through_a5_and_no_benchmark_routing() -> None:
    frontier = load_frontier(FRONTIER_PATH)
    assert tuple(frontier.options) == ("A0", "A1", "A2", "A3", "A4", "A5")
    assert frontier.options["A1"].selectable is False
    assert frontier.options["A0"].name == "STRONG_SINGLE_HIERARCHICAL"
    text = FRONTIER_PATH.read_text(encoding="utf-8").casefold()
    assert 'if benchmark' not in text
    assert 'if system' not in text


def test_frontier_preserves_explicit_truth_equivalence_and_a5_local_branch() -> None:
    frontier = load_frontier(FRONTIER_PATH)
    alias = _case(
        initial_entity="service|truth-alias",
        initial_service="service|truth",
        ground_truth_equivalent_entities=frozenset(
            {"service|truth", "service|truth-alias"}
        ),
        metrics_top1="service|other",
        metrics_top1_service="service|other",
    )
    a0 = apply_option(alias, ArchitectureOption.A0, frontier)
    assert a0.initial_exact_correct is True
    assert a0.final_exact_correct is True

    local = apply_option(_case(), ArchitectureOption.A5, frontier)
    assert local.final_entity == "service|truth"
    assert local.decision_reason == "LOCAL_RESOURCE_HIERARCHY_GUARDED_OVERRIDE"


def test_frozen_layer_groups_allow_node_to_infrastructure_guard() -> None:
    frontier = load_frontier(FRONTIER_PATH)
    outcome = apply_option(
        _case(
            initial_layer=CanonicalEntityLayer.NODE,
            metrics_top1_layer=CanonicalEntityLayer.INFRASTRUCTURE,
        ),
        ArchitectureOption.A2,
        frontier,
    )
    assert outcome.override is True


def test_layer_normalization_parent_chain_same_as_and_relations() -> None:
    hierarchy = EntityHierarchy(
        nodes=(
            EntityNode("service", CanonicalEntityLayer.SERVICE, "service"),
            EntityNode("pod-a", CanonicalEntityLayer.POD, "pod-a"),
            EntityNode("pod-b", CanonicalEntityLayer.POD, "pod-b"),
            EntityNode("container", CanonicalEntityLayer.CONTAINER, "container"),
            EntityNode("pod-a-alias", CanonicalEntityLayer.POD, "pod-a-alias"),
        ),
        parent_edges=(("pod-a", "service"), ("pod-b", "service"), ("container", "pod-a")),
        same_as_edges=(("pod-a", "pod-a-alias"),),
        directed_edges=(("service", "pod-a"),),
        undirected_edges=(("pod-a", "pod-b"),),
    )
    assert hierarchy.parent_chain("container") == ("container", "pod-a", "service")
    assert hierarchy.service_ancestor("container") == "service"
    assert hierarchy.relation("pod-a", "pod-a-alias") == "EXACT_MATCH"
    assert hierarchy.relation("service", "container") == "PREDICTED_ANCESTOR"
    assert hierarchy.relation("container", "service") == "PREDICTED_DESCENDANT"
    assert hierarchy.relation("pod-a", "pod-b") == "SIBLING_SAME_PARENT"
    assert hierarchy.relation("missing", "service") == "UNRESOLVED"


def test_same_as_is_transitive_and_same_node_uses_explicit_parent_chain() -> None:
    hierarchy = EntityHierarchy(
        nodes=(
            EntityNode("node", CanonicalEntityLayer.NODE, "node"),
            EntityNode("pod-a", CanonicalEntityLayer.POD, "pod-a"),
            EntityNode("pod-b", CanonicalEntityLayer.POD, "pod-b"),
            EntityNode("alias-a", CanonicalEntityLayer.POD, "alias-a"),
            EntityNode("alias-b", CanonicalEntityLayer.POD, "alias-b"),
        ),
        parent_edges=(("pod-a", "node"), ("pod-b", "node")),
        same_as_edges=(("pod-a", "alias-a"), ("alias-a", "alias-b")),
    )
    assert hierarchy.relation("pod-a", "alias-b") == "EXACT_MATCH"
    assert hierarchy.relation("pod-a", "pod-b") == "SIBLING_SAME_PARENT"
    assert hierarchy.same_node("pod-a", "pod-b") is True


def test_first_anomaly_rules_are_frozen_and_deterministic() -> None:
    assert first_metric_anomaly(
        samples=((1.0, 10.0), (2.0, 10.0), (3.0, 10.0), (4.0, 10.0), (5.0, 20.0), (6.0, 21.0)),
        anchor=4.0,
        minimum_pre_samples=3,
        minimum_post_samples=3,
        mad_multiplier=3.0,
        relative_floor=0.25,
        epsilon=1e-9,
    ) == 5.0
    assert first_marked_log_anomaly(
        samples=((3.0, "ok"), (4.0, "timeout contacting dependency")),
        anchor=4.0,
        markers=("error", "timeout"),
    ) == 4.0
    assert first_trace_anomaly(
        samples=((1.0, 10.0, False), (2.0, 10.0, False), (3.0, 10.0, False), (4.0, 20.0, False)),
        anchor=4.0,
        minimum_pre_samples=3,
        slow_multiplier=1.5,
    ) == 4.0


def test_evidence_graph_distinguishes_direction_undirected_and_no_path() -> None:
    graph = EvidenceGraph(
        nodes=frozenset({"root", "middle", "symptom", "peer", "isolated"}),
        directed_edges=(("root", "middle"), ("middle", "symptom")),
        undirected_edges=(("middle", "peer"),),
    )
    assert graph.relation("root", "symptom") == "UPSTREAM"
    assert graph.relation("symptom", "root") == "DOWNSTREAM"
    assert graph.relation("peer", "middle") == "LATERAL"
    assert graph.relation("isolated", "root") == "UNKNOWN"
    assert graph.directed_distance("root", "symptom") == 2
    assert graph.directed_distance("root", "root") == 0
    assert graph.directed_distance("symptom", "root") is None
    assert graph.directed_distance("missing", "root") is None


@pytest.mark.parametrize(
    ("option", "case", "expected"),
    (
        (ArchitectureOption.A0, _case(), "service|initial"),
        (ArchitectureOption.A1, _case(), "service|truth"),
        (ArchitectureOption.A2, _case(), "service|truth"),
        (
            ArchitectureOption.A2,
            _case(metrics_top1_is_downstream=True),
            "service|initial",
        ),
        (
            ArchitectureOption.A3,
            _case(fault_regime=FaultOntologyClass.PROPAGATION),
            "service|initial",
        ),
        (
            ArchitectureOption.A3,
            _case(propagation_disposition=PropagationDisposition.PRESENT),
            "service|initial",
        ),
        (
            ArchitectureOption.A3,
            _case(propagation_disposition=PropagationDisposition.UNAVAILABLE),
            "service|initial",
        ),
    ),
)
def test_deterministic_options_follow_frozen_rules(
    option: ArchitectureOption, case: FrontierCase, expected: str
) -> None:
    result = apply_option(case, option, load_frontier(FRONTIER_PATH))
    assert result.final_entity == expected
    assert result.fault_type == case.initial_fault_type


def test_a4_nonlocal_causal_ranking_requires_support_earliest_and_upstream() -> None:
    candidates = (
        CausalCandidate(
            entity="service|late",
            service_ancestor="service|late",
            layer=CanonicalEntityLayer.SERVICE,
            first_anomaly_time=20.0,
            source_support=3,
            metrics_rank=2,
            relation_to_symptom="UPSTREAM",
        ),
        CausalCandidate(
            entity="service|truth",
            service_ancestor="service|truth",
            layer=CanonicalEntityLayer.SERVICE,
            first_anomaly_time=10.0,
            source_support=2,
            metrics_rank=1,
            relation_to_symptom="ROOT",
        ),
    )
    case = _case(
        fault_regime=FaultOntologyClass.PROPAGATION,
        propagation_disposition=PropagationDisposition.PRESENT,
        causal_candidates=candidates,
    )
    result = apply_option(case, ArchitectureOption.A4, load_frontier(FRONTIER_PATH))
    assert result.final_entity == "service|truth"
    assert result.decision_reason == "DETERMINISTIC_CAUSAL_EARLIEST_SUPPORTED"


def test_a4_tie_breaks_by_support_then_metrics_rank_then_entity() -> None:
    candidates = (
        CausalCandidate(
            entity="service|rank-two",
            service_ancestor="service|rank-two",
            layer=CanonicalEntityLayer.SERVICE,
            first_anomaly_time=10.0,
            source_support=2,
            metrics_rank=2,
            relation_to_symptom="UPSTREAM",
        ),
        CausalCandidate(
            entity="service|rank-one",
            service_ancestor="service|rank-one",
            layer=CanonicalEntityLayer.SERVICE,
            first_anomaly_time=10.0,
            source_support=2,
            metrics_rank=1,
            relation_to_symptom="ROOT",
        ),
    )
    result = apply_option(
        _case(
            fault_regime=FaultOntologyClass.PROPAGATION,
            propagation_disposition=PropagationDisposition.PRESENT,
            causal_candidates=candidates,
        ),
        ArchitectureOption.A4,
        load_frontier(FRONTIER_PATH),
    )
    assert result.final_entity == "service|rank-one"


def test_terminal_failure_stays_in_denominator_and_cannot_override() -> None:
    result = apply_option(
        _case(
            terminal_failure=True,
            initial_entity="__TERMINAL_FAILURE__",
            initial_service=None,
            metrics_top1=None,
            metrics_top1_service=None,
            metrics_top1_layer=CanonicalEntityLayer.UNKNOWN,
            metrics_initial_rank=None,
            metrics_margin=None,
        ),
        ArchitectureOption.A4,
        load_frontier(FRONTIER_PATH),
    )
    assert result.override is False
    assert result.initial_exact_correct is False
    assert result.final_exact_correct is False
    assert result.decision_reason == "TERMINAL_FAILURE_KEEP"


def test_grouped_robustness_and_selection_fall_back_to_a0() -> None:
    rows = (
        _case(private_case_key="one", fault_family="cpu"),
        _case(private_case_key="two", fault_family="mem"),
    )
    outcomes = {
        option: tuple(apply_option(row, option, load_frontier(FRONTIER_PATH)) for row in rows)
        for option in (ArchitectureOption.A0, ArchitectureOption.A2)
    }
    folds = grouped_robustness(rows, outcomes)
    assert {item.axis for item in folds} == {
        "LEAVE_ONE_FAULT_FAMILY_OUT",
        "LEAVE_ONE_ENTITY_LAYER_OUT",
        "LEAVE_ONE_SYSTEM_OUT",
    }
    selected = select_architecture(
        option_aggregates={
            "A0": {"rca100_net_rescue": 0, "rca100_damage": 0},
            "A2": {"rca100_net_rescue": 0, "rca100_damage": 0},
            "A3": {"rca100_net_rescue": -1, "rca100_damage": 1},
            "A4": {"rca100_net_rescue": 0, "rca100_damage": 0},
        },
        fixture_aggregates={},
        robustness=folds,
        causal_agent=None,
        frontier=load_frontier(FRONTIER_PATH),
    )
    assert selected == ArchitectureOption.A0


def test_a5_selection_requires_explicit_root_symptom_distinction() -> None:
    frontier = load_frontier(FRONTIER_PATH)
    causal = {
        "eligible_initial_wrong_coverage": 0.25,
        "oracle_rca100_net_rescue": 5,
        "oracle_damage": 0,
        "obss_expected_non_degradation": True,
        "message_contract_nonredundant": True,
        "mean_model_calls": 1.25,
    }
    assert (
        select_architecture(
            option_aggregates={},
            fixture_aggregates={},
            robustness=(),
            causal_agent=causal,
            frontier=frontier,
        )
        is ArchitectureOption.A0
    )
    causal["source_evidence_distinguishes_root_symptom"] = True
    assert (
        select_architecture(
            option_aggregates={},
            fixture_aggregates={},
            robustness=(),
            causal_agent=causal,
            frontier=frontier,
        )
        is ArchitectureOption.A5
    )


def test_damage_rate_denominator_is_initially_correct_not_all_cases() -> None:
    frontier = load_frontier(FRONTIER_PATH)
    outcomes = (
        apply_option(
            _case(
                private_case_key="correct-damaged",
                initial_entity="service|truth",
                initial_service="service|truth",
                metrics_top1="service|wrong",
                metrics_top1_service="service|wrong",
            ),
            ArchitectureOption.A2,
            frontier,
        ),
        apply_option(
            _case(
                private_case_key="already-wrong",
                metrics_top1="service|wrong",
                metrics_top1_service="service|wrong",
            ),
            ArchitectureOption.A2,
            frontier,
        ),
    )
    assert aggregate_outcomes(outcomes)["root_damage_rate"] == 1.0
