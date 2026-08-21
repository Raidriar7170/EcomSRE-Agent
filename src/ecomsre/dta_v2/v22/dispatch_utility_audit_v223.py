"""Evaluator-only top-1 utility audit for the v2.2.3 development gate."""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Literal, cast

from pydantic import Field, StrictBool, StrictFloat, StrictInt, model_validator

from ecomsre.dta_v2.v22.action_catalog import StaticTopologyV22
from ecomsre.dta_v2.v22.controller_contracts import build_hypothesis_catalog_v22
from ecomsre.dta_v2.v22.effective_policy_v222 import build_effective_support_policy_v222
from ecomsre.dta_v2.v22.evidence_utility_audit_v222 import (
    _after_action,
    _memory,
    _minimum_gap,
    _parent,
    audit_case_set_v222,
)
from ecomsre.dta_v2.v22.gap_graph_v222 import build_gap_graph_v222
from ecomsre.dta_v2.v22.gap_router_v222 import (
    GapRouterModeV222,
    SOURCE_PREDICATE_CAPABILITIES_V222,
)
from ecomsre.dta_v2.v22.gap_router_v223 import (
    PredicateYieldPriorV223,
    route_gap_aware_actions_v223,
)
from ecomsre.dta_v2.v22.memory import PredicateKindV22
from ecomsre.dta_v2.v22.practical_campaign import load_practical_truth_set_v22
from ecomsre.dta_v2.v22.practical_dataset import (
    load_practical_case_set_v22,
    materialize_practical_case_v22,
)
from ecomsre.dta_v2.v22.practical_runner import _bootstrap
from ecomsre.dta_v2.v22.predicates import MechanismV22
from ecomsre.dta_v2.v22.read_contracts import (
    DtaModelV22,
    EvidenceSourceV22,
    ReadSourceStatusV22,
    semantic_sha256_v22,
)
from ecomsre.dta_v2.v22.replay_capabilities_v222 import (
    build_replay_capabilities_v222,
    build_source_aware_action_catalog_v222,
)


class Top1AuditStateV223(DtaModelV22):
    case_id: str
    state_kind: Literal["TURN_ZERO", "POST_EMPTY_READ"]
    first_action_id: str | None
    oracle_shortest_action_ids: tuple[str, ...] = Field(min_length=1)
    ranking_action_ids: tuple[str, ...] = Field(min_length=1)
    top1_action_id: str
    top1_source: EvidenceSourceV22
    top1_targets: tuple[str, ...] = Field(min_length=1)
    top1_predicted_gap_closure: StrictInt = Field(ge=0)
    top1_actual_outcome_class: str
    top1_actual_new_predicates: tuple[PredicateKindV22, ...]
    top1_hit: StrictBool


class DevelopmentTop1GateV223(DtaModelV22):
    schema_version: Literal["dta-v22.3.development-top1-gate.v1"]
    feasible_turn_zero_states: StrictInt = Field(ge=1)
    turn_zero_hits: StrictInt = Field(ge=0)
    turn_zero_recall: StrictFloat = Field(ge=0, le=1)
    feasible_post_empty_read_states: StrictInt = Field(ge=1)
    post_empty_read_hits: StrictInt = Field(ge=0)
    post_empty_read_recall: StrictFloat = Field(ge=0, le=1)
    gate_passed: StrictBool


class DispatchUtilityAuditReportV223(DtaModelV22):
    schema_version: Literal["dta-v22.3.dispatch-utility-audit.v1"]
    development_case_set_sha256: str
    development_truth_set_sha256: str
    predicate_yield_priors: tuple[PredicateYieldPriorV223, ...]
    states: tuple[Top1AuditStateV223, ...]
    gate: DevelopmentTop1GateV223
    ranking_repairs_used: Literal[2]
    oracle_visible_to_runtime: Literal[False]
    oracle_visible_to_provider: Literal[False]
    report_sha256: str

    @model_validator(mode="after")
    def require_report(self) -> "DispatchUtilityAuditReportV223":
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"report_sha256"})
        )
        if self.report_sha256 != expected:
            raise ValueError("v2.2.3 dispatch utility audit digest differs")
        return self


def learn_predicate_yield_priors_v223(
    *, repository_root: Path, case_set_path: Path, truth_path: Path
) -> tuple[PredicateYieldPriorV223, ...]:
    """Learn only source/predicate yield rates with Beta(1,1) smoothing."""

    audit = audit_case_set_v222(
        repository_root=repository_root,
        case_set_path=case_set_path,
        truth_path=truth_path,
    )
    counts: dict[tuple[EvidenceSourceV22, PredicateKindV22], list[int]] = defaultdict(
        lambda: [0, 0]
    )
    for case in audit.cases:
        for action in case.actions:
            if not action.source_captured:
                continue
            for predicate_kind in SOURCE_PREDICATE_CAPABILITIES_V222[action.source]:
                key = (action.source, predicate_kind)
                counts[key][1] += 1
                counts[key][0] += int(predicate_kind in action.new_predicate_kinds)
    expected = {
        (source, kind)
        for source, kinds in SOURCE_PREDICATE_CAPABILITIES_V222.items()
        for kind in kinds
    }
    if set(counts) != expected or any(trials == 0 for _, trials in counts.values()):
        raise ValueError("development predicate-yield prior coverage differs")
    return tuple(
        PredicateYieldPriorV223(
            source=source,
            predicate_kind=kind,
            successes=counts[(source, kind)][0],
            trials=counts[(source, kind)][1],
            alpha=1,
            beta=1,
            posterior_mean=(counts[(source, kind)][0] + 1)
            / (counts[(source, kind)][1] + 2),
        )
        for source, kind in sorted(expected, key=lambda item: (item[0].value, item[1].value))
    )


def _negative_keys(action: object) -> tuple[str, ...]:
    return tuple(
        f"{action.source.value}:{target}"  # type: ignore[attr-defined]
        for target in action.target_services  # type: ignore[attr-defined]
    )


def _actual_action_utility(
    *, case_audit: object, action_id: str
) -> tuple[str, tuple[PredicateKindV22, ...]]:
    action = next(item for item in case_audit.actions if item.action_id == action_id)  # type: ignore[attr-defined]
    if action.read_status == ReadSourceStatusV22.SUCCESS_EMPTY.value:
        outcome = "EMPTY_CAPTURED"
    elif action.new_predicate_kinds:
        outcome = "PREDICATE_YIELD"
    elif action.read_status == ReadSourceStatusV22.SUCCESS_NONEMPTY.value:
        outcome = "NONEMPTY_NO_PREDICATE"
    else:
        outcome = "SOURCE_FAILURE"
    return outcome, action.new_predicate_kinds


def audit_development_top1_v223(
    *, repository_root: Path, case_set_path: Path, truth_path: Path
) -> DispatchUtilityAuditReportV223:
    """Audit all turn-zero and feasible captured-empty successor states."""

    base_audit = audit_case_set_v222(
        repository_root=repository_root,
        case_set_path=case_set_path,
        truth_path=truth_path,
    )
    audited = {item.case_id: item for item in base_audit.cases}
    truths = {
        item.case_id: item for item in load_practical_truth_set_v22(truth_path).truths
    }
    priors = learn_predicate_yield_priors_v223(
        repository_root=repository_root,
        case_set_path=case_set_path,
        truth_path=truth_path,
    )
    policy = build_effective_support_policy_v222()
    states: list[Top1AuditStateV223] = []
    for spec in load_practical_case_set_v22(case_set_path).cases:
        truth = truths[spec.case_id]
        if truth.expected_terminal != "DIAGNOSED":
            continue
        case_audit = audited[spec.case_id]
        one_read = tuple(
            item.action_id
            for item in case_audit.actions
            if item.support_clause_became_admissible
        )
        if not one_read:
            continue
        case = materialize_practical_case_v22(
            spec=spec,
            repository_root=repository_root,
        )
        topology = StaticTopologyV22.build(
            services=case.candidate_services,
            edges=case.topology_edges,
        )
        outcomes, _, _, _ = _bootstrap(case=case, topology=topology, run_id="0" * 32)
        memory = _memory(case=case, outcomes=outcomes)
        hypotheses = build_hypothesis_catalog_v22(
            candidate_services=case.candidate_services
        )
        capabilities = build_replay_capabilities_v222(
            spec=spec,
            repository_root=repository_root,
        )
        bootstrap_ids = tuple(item.action_id for item in outcomes)
        catalog = build_source_aware_action_catalog_v222(
            candidate_services=case.candidate_services,
            topology=topology,
            replay_capabilities=capabilities,
            executed_action_ids=bootstrap_ids,
            remaining_budget=3.0,
        )
        graph = build_gap_graph_v222(
            policy=policy,
            hypothesis_catalog=hypotheses,
            memory=memory,
            topology_edges=case.topology_edges,
            planner_focus_hypothesis_id=None,
            prior_negative_coverage=(),
        )
        routing = route_gap_aware_actions_v223(
            mode=GapRouterModeV222.GAP_RANKED_TOP_K,
            catalog=catalog,
            gap_graph=graph,
            prior_negative_coverage=(),
            predicate_yield_priors=priors,
            top_k=4,
        )
        top1 = routing.ranking[0]
        outcome, predicates = _actual_action_utility(
            case_audit=case_audit,
            action_id=top1.action.action_id,
        )
        states.append(
            Top1AuditStateV223(
                case_id=spec.case_id,
                state_kind="TURN_ZERO",
                first_action_id=None,
                oracle_shortest_action_ids=one_read,
                ranking_action_ids=tuple(item.action.action_id for item in routing.ranking),
                top1_action_id=top1.action.action_id,
                top1_source=top1.action.source,
                top1_targets=top1.action.target_services,
                top1_predicted_gap_closure=top1.shortest_clauses_completable,
                top1_actual_outcome_class=outcome,
                top1_actual_new_predicates=predicates,
                top1_hit=top1.action.action_id in set(one_read),
            )
        )

        mechanism = MechanismV22(cast(str, truth.expected_mechanism))
        target = cast(str, truth.expected_root_service)
        parent = (
            _parent(target=target, edges=case.topology_edges)
            if mechanism is MechanismV22.DEPENDENCY_LATENCY
            else None
        )
        for first_action in catalog.actions:
            first_audit = next(
                item for item in case_audit.actions if item.action_id == first_action.action_id
            )
            if first_audit.read_status != ReadSourceStatusV22.SUCCESS_EMPTY.value:
                continue
            post_outcomes, _ = _after_action(
                case=case,
                outcomes=outcomes,
                action=first_action,
            )
            post_memory = _memory(case=case, outcomes=post_outcomes)
            if _minimum_gap(
                memory=post_memory,
                mechanism=mechanism,
                target=target,
                parent=parent,
            ) == 0:
                continue
            post_catalog = build_source_aware_action_catalog_v222(
                candidate_services=case.candidate_services,
                topology=topology,
                replay_capabilities=capabilities,
                executed_action_ids=tuple(sorted({*bootstrap_ids, first_action.action_id})),
                remaining_budget=3.0 - first_action.weighted_cost,
            )
            useful: list[str] = []
            for candidate in post_catalog.actions:
                candidate_outcomes, _ = _after_action(
                    case=case,
                    outcomes=post_outcomes,
                    action=candidate,
                )
                candidate_memory = _memory(case=case, outcomes=candidate_outcomes)
                if _minimum_gap(
                    memory=candidate_memory,
                    mechanism=mechanism,
                    target=target,
                    parent=parent,
                ) == 0:
                    useful.append(candidate.action_id)
            if not useful:
                continue
            negative = _negative_keys(first_action)
            post_graph = build_gap_graph_v222(
                policy=policy,
                hypothesis_catalog=hypotheses,
                memory=post_memory,
                topology_edges=case.topology_edges,
                planner_focus_hypothesis_id=None,
                prior_negative_coverage=negative,
            )
            post_routing = route_gap_aware_actions_v223(
                mode=GapRouterModeV222.GAP_RANKED_TOP_K,
                catalog=post_catalog,
                gap_graph=post_graph,
                prior_negative_coverage=negative,
                predicate_yield_priors=priors,
                top_k=4,
            )
            post_top1 = post_routing.ranking[0]
            post_outcome, post_predicates = _actual_action_utility(
                case_audit=case_audit,
                action_id=post_top1.action.action_id,
            )
            states.append(
                Top1AuditStateV223(
                    case_id=spec.case_id,
                    state_kind="POST_EMPTY_READ",
                    first_action_id=first_action.action_id,
                    oracle_shortest_action_ids=tuple(sorted(useful)),
                    ranking_action_ids=tuple(
                        item.action.action_id for item in post_routing.ranking
                    ),
                    top1_action_id=post_top1.action.action_id,
                    top1_source=post_top1.action.source,
                    top1_targets=post_top1.action.target_services,
                    top1_predicted_gap_closure=post_top1.shortest_clauses_completable,
                    top1_actual_outcome_class=post_outcome,
                    top1_actual_new_predicates=post_predicates,
                    top1_hit=post_top1.action.action_id in set(useful),
                )
            )
    turn_zero = tuple(item for item in states if item.state_kind == "TURN_ZERO")
    post_empty = tuple(item for item in states if item.state_kind == "POST_EMPTY_READ")
    if not turn_zero or not post_empty:
        raise ValueError("v2.2.3 development top-1 audit lacks feasible states")
    turn_hits = sum(item.top1_hit for item in turn_zero)
    post_hits = sum(item.top1_hit for item in post_empty)
    turn_recall = turn_hits / len(turn_zero)
    post_recall = post_hits / len(post_empty)
    gate = DevelopmentTop1GateV223(
        schema_version="dta-v22.3.development-top1-gate.v1",
        feasible_turn_zero_states=len(turn_zero),
        turn_zero_hits=turn_hits,
        turn_zero_recall=turn_recall,
        feasible_post_empty_read_states=len(post_empty),
        post_empty_read_hits=post_hits,
        post_empty_read_recall=post_recall,
        gate_passed=turn_recall >= 0.75 and post_recall >= 0.70,
    )
    payload = {
        "schema_version": "dta-v22.3.dispatch-utility-audit.v1",
        "development_case_set_sha256": base_audit.case_set_sha256,
        "development_truth_set_sha256": base_audit.truth_set_sha256,
        "predicate_yield_priors": tuple(item.model_dump(mode="json") for item in priors),
        "states": tuple(item.model_dump(mode="json") for item in states),
        "gate": gate.model_dump(mode="json"),
        "ranking_repairs_used": 2,
        "oracle_visible_to_runtime": False,
        "oracle_visible_to_provider": False,
    }
    return DispatchUtilityAuditReportV223(
        schema_version="dta-v22.3.dispatch-utility-audit.v1",
        development_case_set_sha256=base_audit.case_set_sha256,
        development_truth_set_sha256=base_audit.truth_set_sha256,
        predicate_yield_priors=priors,
        states=tuple(states),
        gate=gate,
        ranking_repairs_used=2,
        oracle_visible_to_runtime=False,
        oracle_visible_to_provider=False,
        report_sha256=semantic_sha256_v22(payload),
    )


__all__ = (
    "DevelopmentTop1GateV223",
    "DispatchUtilityAuditReportV223",
    "Top1AuditStateV223",
    "audit_development_top1_v223",
    "learn_predicate_yield_priors_v223",
)
