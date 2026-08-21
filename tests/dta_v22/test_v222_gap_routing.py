from __future__ import annotations

from pathlib import Path
from collections.abc import Mapping
import json

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
from ecomsre.dta_v2.v22.controller_inputs import ControllerArmV22
from ecomsre.dta_v2.v22.effective_policy_v222 import (
    build_effective_support_policy_v222,
)
from ecomsre.dta_v2.v22.gap_graph_v222 import build_gap_graph_v222
from ecomsre.dta_v2.v22.gap_router_v222 import (
    GapRouterModeV222,
    route_gap_aware_actions_v222,
)
from ecomsre.dta_v2.v22.memory import build_memory_views_v22
from ecomsre.dta_v2.v22.negative_coverage_v222 import (
    NegativeCoverageLedgerV222,
    ReadUtilityClassV222,
    classify_read_utility_v222,
    record_negative_coverage_v222,
)
from ecomsre.dta_v2.v22.post_read_delta_v222 import build_post_read_delta_v222
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
from ecomsre.dta_v2.v22.replay import QuerySpecificReplayBackendV22
from ecomsre.dta_v2.v22.practical_runner import _memory_outcome
from ecomsre.dta_v2.v22.selection_provider_v222 import (
    FUNCTION_NAME_V222,
    SelectionAliasTableV222,
    SelectionProviderV222,
    SelectionProviderProtocolFailureV222,
    SelectionTurnRequestV222,
)
from ecomsre.dta_v2.v22.simple_provider import ProviderTransportErrorV22
from ecomsre.dta_v2.v22.terminal_catalog_v222 import build_terminal_catalog_v222
from ecomsre.dta_v2.v22.gap_study_runner_v222 import (
    SHARED_SELECTION_SYSTEM_PROMPT_V222,
    run_oracle_simulation_v222,
)
from ecomsre.dta_v2.v22.gap_study_campaign_v222 import (
    StudyCombinationV222,
    balanced_combination_order_v222,
)
from ecomsre.dta_v2.v22.gap_study_scorer_v222 import score_gap_study_v222
from ecomsre.dta_v2.v22.practical_campaign import load_practical_truth_set_v22
from ecomsre.model.gateway import OpenAICompatibleConfig


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


def _state(case_id: str):
    spec, case = _development_case(case_id)
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
    capabilities = build_replay_capabilities_v222(spec=spec, repository_root=ROOT)
    catalog = build_source_aware_action_catalog_v222(
        candidate_services=case.candidate_services,
        topology=topology,
        replay_capabilities=capabilities,
        executed_action_ids=tuple(item.action_id for item in outcomes),
        covered_capability_keys=(),
        remaining_budget=3.0,
    )
    policy = build_effective_support_policy_v222()
    hypotheses = build_hypothesis_catalog_v22(candidate_services=case.candidate_services)
    graph = build_gap_graph_v222(
        policy=policy,
        hypothesis_catalog=hypotheses,
        memory=memory,
        topology_edges=case.topology_edges,
        planner_focus_hypothesis_id=None,
        prior_negative_coverage=(),
    )
    routing = route_gap_aware_actions_v222(
        mode=GapRouterModeV222.GAP_RANKED_TOP_K,
        catalog=catalog,
        gap_graph=graph,
        prior_negative_coverage=(),
        top_k=4,
    )
    return spec, case, topology, outcomes, memory, catalog, policy, hypotheses, graph, routing


def _read(case, outcomes, catalog, action_id: str):
    action = next(item for item in catalog.actions if item.action_id == action_id)
    source_outcome = QuerySpecificReplayBackendV22(case.capture).execute(action)
    projected = _memory_outcome(
        action=action,
        outcome=source_outcome,
        run_id="0" * 32,
        dispatch_ordinal=len(outcomes) + 1,
        observed_at=case.capture.captured_at,
    )
    post_outcomes = (*outcomes, projected)
    post_memory, _ = build_memory_views_v22(
        outcomes=post_outcomes,
        baseline=_baseline(case),
        observed_at=case.capture.captured_at,
        top_k=64,
    )
    return action, source_outcome, post_outcomes, post_memory


def test_empty_and_nonpredicate_reads_become_negative_coverage_not_contradictions() -> None:
    _, case, _, outcomes, memory, catalog, _, _, graph, _ = _state("e01")
    action, read, _, after = _read(case, outcomes, catalog, "a:logs:payment")
    utility = classify_read_utility_v222(
        before_memory=memory,
        after_memory=after,
        read_outcome=read,
    )
    assert utility.outcome_class is ReadUtilityClassV222.EMPTY_CAPTURED
    ledger = record_negative_coverage_v222(
        ledger=NegativeCoverageLedgerV222.empty(),
        action=action,
        utility=utility,
        minimum_gap_before=min(item.minimum_missing_count for item in graph.hypotheses),
        minimum_gap_after=min(item.minimum_missing_count for item in graph.hypotheses),
    )
    assert ledger.entries[0].outcome_class is ReadUtilityClassV222.EMPTY_CAPTURED
    assert ledger.entries[0].hypothesis_contradicted is False
    assert ledger.empty_source_target_keys == ("LOGS:payment",)

    _, case2, _, outcomes2, memory2, catalog2, _, _, _, _ = _state("e02")
    _, read2, _, after2 = _read(case2, outcomes2, catalog2, "a:resources:payment")
    utility2 = classify_read_utility_v222(
        before_memory=memory2,
        after_memory=after2,
        read_outcome=read2,
    )
    assert utility2.outcome_class is ReadUtilityClassV222.NONEMPTY_NO_PREDICATE


def test_terminal_catalog_exposes_supported_t_alias_and_no_early_abstain() -> None:
    _, case, topology, outcomes, memory, catalog, policy, hypotheses, graph, routing = _state("e01")
    before = build_terminal_catalog_v222(
        policy=policy,
        hypothesis_catalog=hypotheses,
        memory=memory,
        gap_graph=graph,
        routed_actions=routing,
        candidate_services=case.candidate_services,
        topology_edges=case.topology_edges,
        budget_exhausted=False,
        required_source_unavailable=False,
        conflicting_evidence=False,
    )
    assert not any(item.terminal_kind.value == "ABSTAIN" for item in before.candidates)
    assert not any(item.terminal_kind.value == "DIAGNOSED" for item in before.candidates)

    action, read, post_outcomes, post_memory = _read(
        case, outcomes, catalog, "a:traces:payment"
    )
    post_catalog = build_source_aware_action_catalog_v222(
        candidate_services=case.candidate_services,
        topology=topology,
        replay_capabilities=build_replay_capabilities_v222(
            spec=_development_case("e01")[0], repository_root=ROOT
        ),
        executed_action_ids=tuple(
            sorted({*(item.action_id for item in outcomes), action.action_id})
        ),
        remaining_budget=3.0 - action.weighted_cost,
    )
    post_graph = build_gap_graph_v222(
        policy=policy,
        hypothesis_catalog=hypotheses,
        memory=post_memory,
        topology_edges=case.topology_edges,
        planner_focus_hypothesis_id=None,
        prior_negative_coverage=(),
    )
    post_routing = route_gap_aware_actions_v222(
        mode=GapRouterModeV222.GAP_RANKED_TOP_K,
        catalog=post_catalog,
        gap_graph=post_graph,
        prior_negative_coverage=(),
        top_k=4,
    )
    after = build_terminal_catalog_v222(
        policy=policy,
        hypothesis_catalog=hypotheses,
        memory=post_memory,
        gap_graph=post_graph,
        routed_actions=post_routing,
        candidate_services=case.candidate_services,
        topology_edges=case.topology_edges,
        budget_exhausted=False,
        required_source_unavailable=False,
        conflicting_evidence=False,
    )
    diagnosed = next(
        item for item in after.candidates if item.terminal_kind.value == "DIAGNOSED"
    )
    assert diagnosed.terminal_alias.startswith("T")
    assert diagnosed.root_service == "payment"
    assert diagnosed.mechanism.value == "CONFIGURATION_ERROR"
    assert diagnosed.supporting_evidence_refs

    utility = classify_read_utility_v222(
        before_memory=memory,
        after_memory=post_memory,
        read_outcome=read,
    )
    delta = build_post_read_delta_v222(
        action_alias="A00",
        action=action,
        utility=utility,
        minimum_gap_before=1,
        minimum_gap_after=0,
        before_terminal_aliases=(),
        after_terminal_catalog=after,
        remaining_top_gaps=post_graph,
        ranked_next_action_aliases=tuple(
            f"A{index:02d}" for index, _ in enumerate(post_routing.actions)
        ),
        evidence_aliases={
            ref.evidence_ref: f"E{index:02d}"
            for index, ref in enumerate(post_memory.evidence_refs)
        },
    )
    assert delta.outcome_class is ReadUtilityClassV222.PREDICATE_YIELD
    assert delta.newly_available_terminal_aliases == (diagnosed.terminal_alias,)
    assert delta.minimum_missing_gap_before == 1
    assert delta.minimum_missing_gap_after == 0


def test_terminal_catalog_exposes_no_incident_for_complete_healthy_replay() -> None:
    _, case, _, _, memory, _, policy, hypotheses, graph, routing = _state("e09")
    catalog = build_terminal_catalog_v222(
        policy=policy,
        hypothesis_catalog=hypotheses,
        memory=memory,
        gap_graph=graph,
        routed_actions=routing,
        candidate_services=case.candidate_services,
        topology_edges=case.topology_edges,
        budget_exhausted=False,
        required_source_unavailable=False,
        conflicting_evidence=False,
    )
    assert tuple(item.terminal_kind.value for item in catalog.candidates) == (
        "NO_INCIDENT",
    )


class _RecordingTransport:
    def __init__(self, outcomes: list[object]) -> None:
        self.outcomes = list(outcomes)
        self.calls: list[dict[str, object]] = []

    def post_json(
        self,
        *,
        url: str,
        headers: Mapping[str, str],
        payload: Mapping[str, object],
        timeout_seconds: float,
    ) -> Mapping[str, object]:
        self.calls.append({"url": url, "payload": dict(payload)})
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        assert isinstance(outcome, Mapping)
        return outcome


def _selection_response(selection: str, focus: str) -> dict[str, object]:
    return {
        "choices": [
            {
                "message": {
                    "tool_calls": [
                        {
                            "function": {
                                "name": FUNCTION_NAME_V222,
                                "arguments": json.dumps(
                                    {"selection": selection, "focus": focus}
                                ),
                            }
                        }
                    ]
                }
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12},
    }


def _selection_request() -> SelectionTurnRequestV222:
    aliases = SelectionAliasTableV222.build(
        hypothesis_ids=("h:payment:configuration-error",),
        action_ids=("a:traces:payment",),
        terminal_ids=("terminal:diagnosed:payment",),
        evidence_refs=(),
    )
    return SelectionTurnRequestV222.build(
        system_prompt="read-only selection",
        aliases=aliases,
        visible_state={"actions": ["A00"], "terminals": ["T00"]},
    )


def _selection_provider(transport, sleeps: list[float] | None = None):
    return SelectionProviderV222(
        config=OpenAICompatibleConfig(
            base_url="https://provider.invalid/v1",
            api_key="secret",
            model="configured-model",
        ),
        transport=transport,
        sleeper=(lambda _: None) if sleeps is None else sleeps.append,
        minimum_request_interval_seconds=0,
        debug_root=ROOT / ".local/dta-v22-2-debug-tests",
    )


def test_selection_provider_allows_two_repairs_and_three_exact_request_retries() -> None:
    transport = _RecordingTransport(
        [
            _selection_response("BAD", "H00"),
            _selection_response("BAD", "H00"),
            _selection_response("A00", "H00"),
        ]
    )
    outcome = _selection_provider(transport).complete_turn(
        request=_selection_request(), run_id="1" * 32
    )
    assert outcome.protocol_repairs == 2
    assert outcome.provider_calls == 3
    assert outcome.decision.action_id == "a:traces:payment"

    sleeps: list[float] = []
    retry_transport = _RecordingTransport(
        [
            ProviderTransportErrorV22("HTTP_429", status_code=429),
            ProviderTransportErrorV22("HTTP_503", status_code=503),
            ProviderTransportErrorV22("CONNECTION_ERROR"),
            _selection_response("T00", "NONE"),
        ]
    )
    retry_outcome = _selection_provider(retry_transport, sleeps).complete_turn(
        request=_selection_request(), run_id="2" * 32
    )
    assert retry_outcome.transport_retry_count == 3
    assert sleeps == [5.0, 15.0, 30.0]
    assert all(
        item["payload"] == retry_transport.calls[0]["payload"]
        for item in retry_transport.calls
    )
    assert retry_outcome.provider_calls == 1
    assert retry_outcome.decision.terminal_id == "terminal:diagnosed:payment"


def test_valid_terminal_selection_is_not_retried_and_repairs_are_bounded() -> None:
    valid = _RecordingTransport([_selection_response("T00", "NONE")])
    outcome = _selection_provider(valid).complete_turn(
        request=_selection_request(), run_id="3" * 32
    )
    assert outcome.provider_calls == 1
    assert outcome.protocol_repairs == 0

    invalid = _RecordingTransport(
        [
            _selection_response("BAD", "H00"),
            _selection_response("BAD", "H00"),
            _selection_response("BAD", "H00"),
            _selection_response("A00", "H00"),
        ]
    )
    with pytest.raises(SelectionProviderProtocolFailureV222) as captured:
        _selection_provider(invalid).complete_turn(
            request=_selection_request(), run_id="4" * 32
        )
    assert captured.value.protocol_repairs == 2
    assert captured.value.provider_calls == 3
    assert len(invalid.calls) == 3


def test_oracle_simulation_completes_every_feasible_development_incident() -> None:
    report = run_oracle_simulation_v222(
        repository_root=ROOT,
        case_set_path=ROOT / "config/dta-v22-sprint/evaluation/cases.json",
        truth_path=ROOT / "config/dta-v22-sprint/evaluation/truth.json",
    )
    assert report.feasible_incident_cases == 8
    assert report.completed_incident_cases == 8
    assert report.completion_rate == 1.0
    assert report.agent_writes == 0
    assert report.oracle_result is True


def test_v222_prompt_file_matches_short_selection_prompt() -> None:
    assert (
        ROOT.joinpath("config/dta-v22-2/prompt.txt")
        .read_text(encoding="utf-8")
        .strip()
        == SHARED_SELECTION_SYSTEM_PROMPT_V222
    )


def test_four_combination_schedule_rotates_every_execution_position() -> None:
    positions = {combination: [] for combination in StudyCombinationV222}
    for index in range(4):
        order = balanced_combination_order_v222(index)
        assert len(order) == 4
        assert set(order) == set(StudyCombinationV222)
        for position, combination in enumerate(order, start=1):
            positions[combination].append(position)
    assert all(sorted(values) == [1, 2, 3, 4] for values in positions.values())


def test_oracle_runs_satisfy_development_gap_utility_gate() -> None:
    oracle = run_oracle_simulation_v222(
        repository_root=ROOT,
        case_set_path=ROOT / "config/dta-v22-sprint/evaluation/cases.json",
        truth_path=ROOT / "config/dta-v22-sprint/evaluation/truth.json",
    )
    gap_runs = tuple(
        run.model_copy(
            update={
                "arm": arm,
                "router_mode": GapRouterModeV222.GAP_RANKED_TOP_K,
                "planner_ledger_visible": arm is ControllerArmV22.PLANNER_LITE,
            }
        )
        for run in oracle.runs
        for arm in (ControllerArmV22.FLAT_CANONICAL, ControllerArmV22.PLANNER_LITE)
    )
    truths = load_practical_truth_set_v22(
        ROOT / "config/dta-v22-sprint/evaluation/truth.json"
    ).truths
    scored = score_gap_study_v222(runs=gap_runs, truths=truths)
    assert scored.development_gate.predicate_yield_read_rate == 1.0
    assert scored.development_gate.read_bearing_diagnosed_runs >= 2
    assert scored.development_gate.protocol_failure_rate == 0.0
    assert scored.development_gate.gate_passed is True


def test_four_combination_scorer_emits_frozen_effect_interpretation() -> None:
    oracle = run_oracle_simulation_v222(
        repository_root=ROOT,
        case_set_path=ROOT / "config/dta-v22-sprint/evaluation/cases.json",
        truth_path=ROOT / "config/dta-v22-sprint/evaluation/truth.json",
    )
    combinations = (
        (ControllerArmV22.FLAT_CANONICAL, GapRouterModeV222.BROAD_CATALOG),
        (ControllerArmV22.FLAT_CANONICAL, GapRouterModeV222.GAP_RANKED_TOP_K),
        (ControllerArmV22.PLANNER_LITE, GapRouterModeV222.BROAD_CATALOG),
        (ControllerArmV22.PLANNER_LITE, GapRouterModeV222.GAP_RANKED_TOP_K),
    )
    runs = tuple(
        run.model_copy(
            update={
                "arm": arm,
                "router_mode": mode,
                "planner_ledger_visible": arm is ControllerArmV22.PLANNER_LITE,
            }
        )
        for run in oracle.runs
        for arm, mode in combinations
    )
    cases = ROOT / "config/dta-v22-sprint/evaluation/cases.json"
    truth = ROOT / "config/dta-v22-sprint/evaluation/truth.json"
    scored = score_gap_study_v222(
        runs=runs,
        truths=load_practical_truth_set_v22(truth).truths,
        utility_audit=audit_case_set_v222(
            repository_root=ROOT,
            case_set_path=cases,
            truth_path=truth,
        ),
        routing_gate=evaluate_development_routing_gate_v222(
            repository_root=ROOT,
            case_set_path=cases,
            truth_path=truth,
        ),
        include_interpretation=True,
    )
    assert all(
        item.oracle_shortest_path_action_hit_rate == 1.0
        for item in scored.combinations
    )
    assert scored.top_k_useful_action_recall_turn_zero == 1.0
    assert scored.top_k_useful_action_recall_post_first_read == 1.0
    assert len(scored.control_regressions) == 2
    assert scored.interpretation is not None
    assert (
        scored.interpretation.engineering_terminal
        == "DTA_V22_2_NO_GAP_ROUTING_EFFECT_OBSERVED"
    )
