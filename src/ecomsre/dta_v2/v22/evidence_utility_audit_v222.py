"""Evaluator-only replay utility audit for the DTA v2.2.2 development gate."""

from __future__ import annotations

from enum import Enum
import itertools
import json
from pathlib import Path
from typing import Literal, cast

from pydantic import Field, StrictBool, StrictFloat, StrictInt, model_validator

from ecomsre.dta_v2.v22.action_catalog import (
    EvidenceActionV22,
    StaticTopologyV22,
    build_action_catalog_v22,
    build_default_tool_capability_registry_v22,
)
from ecomsre.dta_v2.v22.memory import (
    EvidencePredicateV22,
    MemoryReadOutcomeV22,
    PredicateKindV22,
    SalientEvidenceMemoryV22,
    build_memory_views_v22,
)
from ecomsre.dta_v2.v22.practical_campaign import load_practical_truth_set_v22
from ecomsre.dta_v2.v22.practical_dataset import (
    PracticalCaseSpecV22,
    load_practical_case_set_v22,
    materialize_practical_case_v22,
)
from ecomsre.dta_v2.v22.practical_runner import _baseline, _bootstrap, _memory_outcome
from ecomsre.dta_v2.v22.predicates import (
    MechanismV22,
    PredicateRequirementV22,
    RequirementServiceBindingV22,
    build_default_evidence_support_policy_v22,
)
from ecomsre.dta_v2.v22.read_contracts import (
    DtaModelV22,
    EvidenceSourceV22,
    ReadSourceStatusV22,
    semantic_sha256_v22,
)
from ecomsre.dta_v2.v22.replay import QuerySpecificReplayBackendV22
from ecomsre.dta_v2.v22.controller_contracts import build_hypothesis_catalog_v22
from ecomsre.dta_v2.v22.effective_policy_v222 import (
    build_effective_support_policy_v222,
)
from ecomsre.dta_v2.v22.gap_graph_v222 import build_gap_graph_v222
from ecomsre.dta_v2.v22.gap_router_v222 import (
    GapRouterModeV222,
    route_gap_aware_actions_v222,
)
from ecomsre.dta_v2.v22.replay_capabilities_v222 import (
    build_replay_capabilities_v222,
    build_source_aware_action_catalog_v222,
    captured_sources_from_case_spec_v222,
)


class ShortestAdmissiblePathV222(str, Enum):
    ZERO = "0"
    ONE = "1"
    TWO = "2"
    INFEASIBLE = "INFEASIBLE"


class ActionUtilityAuditV222(DtaModelV22):
    action_id: str
    source: EvidenceSourceV22
    target_services: tuple[str, ...]
    source_captured: StrictBool
    read_status: str
    nonempty: StrictBool
    new_predicate_kinds: tuple[PredicateKindV22, ...]
    new_evidence_refs: tuple[str, ...]
    support_clause_gaps_closed: StrictInt = Field(ge=0)
    support_clause_became_admissible: StrictBool


class CaseUtilityAuditV222(DtaModelV22):
    case_id: str
    expected_terminal: Literal["DIAGNOSED", "NO_INCIDENT", "ABSTAIN"]
    expected_root_service: str | None
    expected_mechanism: str | None
    actions: tuple[ActionUtilityAuditV222, ...]
    shortest_admissible_path: ShortestAdmissiblePathV222
    shortest_action_ids: tuple[str, ...] | None

    @model_validator(mode="after")
    def require_path(self) -> "CaseUtilityAuditV222":
        infeasible = self.shortest_admissible_path is ShortestAdmissiblePathV222.INFEASIBLE
        if infeasible != (self.shortest_action_ids is None):
            raise ValueError("utility audit path binding differs")
        return self


class EvidenceUtilityAuditReportV222(DtaModelV22):
    schema_version: Literal["dta-v22.2.evidence-utility-audit.v1"]
    case_set_sha256: str
    truth_set_sha256: str
    cases: tuple[CaseUtilityAuditV222, ...]
    infeasible_incident_cases: StrictInt = Field(ge=0)
    oracle_visible_to_provider: Literal[False]
    report_sha256: str

    @model_validator(mode="after")
    def require_report(self) -> "EvidenceUtilityAuditReportV222":
        if tuple(item.case_id for item in self.cases) != tuple(
            sorted({item.case_id for item in self.cases})
        ):
            raise ValueError("utility audit cases are not canonical")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"report_sha256"})
        )
        if self.report_sha256 != expected:
            raise ValueError("utility audit digest differs")
        return self


class DevelopmentRoutingGateV222(DtaModelV22):
    schema_version: Literal["dta-v22.2.development-routing-gate.v1"]
    feasible_turn_zero_states: StrictInt = Field(ge=1)
    turn_zero_hits: StrictInt = Field(ge=0)
    turn_zero_recall: StrictFloat = Field(ge=0, le=1)
    feasible_post_first_read_states: StrictInt = Field(ge=1)
    post_first_read_hits: StrictInt = Field(ge=0)
    post_first_read_recall: StrictFloat = Field(ge=0, le=1)
    gate_passed: StrictBool
    oracle_visible_to_provider: Literal[False]


def _captured_sources(
    *, spec: PracticalCaseSpecV22, repository_root: Path
) -> frozenset[EvidenceSourceV22]:
    return frozenset(
        captured_sources_from_case_spec_v222(
            spec=spec,
            repository_root=repository_root,
        )
    )


def _requirements() -> dict[MechanismV22, tuple[tuple[str, tuple[PredicateRequirementV22, ...]], ...]]:
    clauses: dict[MechanismV22, list[tuple[str, tuple[PredicateRequirementV22, ...]]]] = {}
    for clause in build_default_evidence_support_policy_v22().clauses:
        clauses.setdefault(clause.mechanism, []).append(
            (clause.clause_id, clause.requirements)
        )
    clauses[MechanismV22.CONFIGURATION_ERROR].append(
        (
            "configuration:error-metric-and-first-error-trace",
            (
                PredicateRequirementV22(
                    predicate_kind=PredicateKindV22.METRIC_ERROR_RATE_STRONG,
                    service_binding=RequirementServiceBindingV22.TARGET,
                    require_exact_parent=False,
                ),
                PredicateRequirementV22(
                    predicate_kind=PredicateKindV22.TRACE_FIRST_ERROR,
                    service_binding=RequirementServiceBindingV22.TARGET,
                    require_exact_parent=False,
                ),
            ),
        )
    )
    clauses[MechanismV22.MEMORY_LEAK].append(
        (
            "memory-leak:growth-and-healthy",
            (
                PredicateRequirementV22(
                    predicate_kind=PredicateKindV22.RESOURCE_MEMORY_GROWTH_STRONG,
                    service_binding=RequirementServiceBindingV22.TARGET,
                    require_exact_parent=False,
                ),
                PredicateRequirementV22(
                    predicate_kind=PredicateKindV22.RUNTIME_HEALTHY,
                    service_binding=RequirementServiceBindingV22.TARGET,
                    require_exact_parent=False,
                ),
            ),
        )
    )
    return {
        mechanism: tuple(sorted(items, key=lambda item: item[0]))
        for mechanism, items in clauses.items()
    }


_CLAUSES = _requirements()


def _predicate_matches(
    *,
    predicate: EvidencePredicateV22,
    requirement: PredicateRequirementV22,
    target: str,
    parent: str | None,
) -> bool:
    if predicate.predicate_kind is not requirement.predicate_kind:
        return False
    allowed = {target}
    if requirement.service_binding is RequirementServiceBindingV22.TARGET_OR_PARENT:
        if parent is not None:
            allowed.add(parent)
    if predicate.service not in allowed:
        return False
    return not requirement.require_exact_parent or predicate.parent_service == parent


def _minimum_gap(
    *,
    memory: SalientEvidenceMemoryV22,
    mechanism: MechanismV22,
    target: str,
    parent: str | None,
) -> int:
    return min(
        sum(
            not any(
                _predicate_matches(
                    predicate=predicate,
                    requirement=requirement,
                    target=target,
                    parent=parent,
                )
                for predicate in memory.predicates
            )
            for requirement in requirements
        )
        for _, requirements in _CLAUSES[mechanism]
    )


def _memory(
    *, case: object, outcomes: tuple[MemoryReadOutcomeV22, ...]
) -> SalientEvidenceMemoryV22:
    salient, _ = build_memory_views_v22(
        outcomes=outcomes,
        baseline=_baseline(case),  # type: ignore[arg-type]
        observed_at=case.capture.captured_at,  # type: ignore[attr-defined]
        top_k=64,
    )
    return salient


def _after_action(
    *,
    case: object,
    outcomes: tuple[MemoryReadOutcomeV22, ...],
    action: EvidenceActionV22,
) -> tuple[tuple[MemoryReadOutcomeV22, ...], ReadSourceStatusV22]:
    source = QuerySpecificReplayBackendV22(case.capture).execute(action)  # type: ignore[attr-defined]
    projected = _memory_outcome(
        action=action,
        outcome=source,
        run_id="0" * 32,
        dispatch_ordinal=len(outcomes) + 1,
        observed_at=case.capture.captured_at,  # type: ignore[attr-defined]
    )
    if projected.outcome_sha256 in {item.outcome_sha256 for item in outcomes}:
        return outcomes, source.status
    return (*outcomes, projected), source.status


def _parent(*, target: str, edges: tuple[tuple[str, str], ...]) -> str | None:
    return next(
        (right if left == target else left for left, right in edges if target in {left, right}),
        None,
    )


def audit_case_set_v222(
    *, repository_root: Path, case_set_path: Path, truth_path: Path
) -> EvidenceUtilityAuditReportV222:
    """Enumerate replay utility with evaluator truth isolated from runtime inputs."""

    case_set = load_practical_case_set_v22(case_set_path)
    truth_set = load_practical_truth_set_v22(truth_path)
    truths = {item.case_id: item for item in truth_set.truths}
    if tuple(truths) != tuple(item.case_id for item in case_set.cases):
        raise ValueError("utility audit truth order differs from cases")
    case_audits: list[CaseUtilityAuditV222] = []
    for spec in case_set.cases:
        truth = truths[spec.case_id]
        case = materialize_practical_case_v22(spec=spec, repository_root=repository_root)
        topology = StaticTopologyV22.build(
            services=case.candidate_services,
            edges=case.topology_edges,
        )
        bootstrap_outcomes, _, _, registry = _bootstrap(
            case=case,
            topology=topology,
            run_id="0" * 32,
        )
        bootstrap_memory = _memory(case=case, outcomes=bootstrap_outcomes)
        captured = _captured_sources(spec=spec, repository_root=repository_root)
        target = truth.expected_root_service
        mechanism = (
            MechanismV22(truth.expected_mechanism)
            if truth.expected_mechanism is not None
            else None
        )
        parent = (
            _parent(target=target, edges=case.topology_edges)
            if target is not None and mechanism is MechanismV22.DEPENDENCY_LATENCY
            else None
        )
        before_gap = (
            _minimum_gap(
                memory=bootstrap_memory,
                mechanism=mechanism,
                target=cast(str, target),
                parent=parent,
            )
            if mechanism is not None
            else 0
        )
        action_audits: list[ActionUtilityAuditV222] = []
        for action in registry.registry_actions:
            if action.source not in captured:
                action_audits.append(
                    ActionUtilityAuditV222(
                        action_id=action.action_id,
                        source=action.source,
                        target_services=action.target_services,
                        source_captured=False,
                        read_status="NOT_CAPTURED",
                        nonempty=False,
                        new_predicate_kinds=(),
                        new_evidence_refs=(),
                        support_clause_gaps_closed=0,
                        support_clause_became_admissible=False,
                    )
                )
                continue
            after_outcomes, status = _after_action(
                case=case,
                outcomes=bootstrap_outcomes,
                action=action,
            )
            after_memory = _memory(case=case, outcomes=after_outcomes)
            before_predicates = {item.predicate_id for item in bootstrap_memory.predicates}
            before_refs = {item.evidence_ref for item in bootstrap_memory.evidence_refs}
            after_gap = (
                _minimum_gap(
                    memory=after_memory,
                    mechanism=mechanism,
                    target=cast(str, target),
                    parent=parent,
                )
                if mechanism is not None
                else 0
            )
            action_audits.append(
                ActionUtilityAuditV222(
                    action_id=action.action_id,
                    source=action.source,
                    target_services=action.target_services,
                    source_captured=True,
                    read_status=status.value,
                    nonempty=status is ReadSourceStatusV22.SUCCESS_NONEMPTY,
                    new_predicate_kinds=tuple(
                        sorted(
                            {
                                item.predicate_kind
                                for item in after_memory.predicates
                                if item.predicate_id not in before_predicates
                            },
                            key=lambda item: item.value,
                        )
                    ),
                    new_evidence_refs=tuple(
                        sorted(
                            item.evidence_ref
                            for item in after_memory.evidence_refs
                            if item.evidence_ref not in before_refs
                        )
                    ),
                    support_clause_gaps_closed=max(0, before_gap - after_gap),
                    support_clause_became_admissible=before_gap > 0 and after_gap == 0,
                )
            )

        shortest_kind = ShortestAdmissiblePathV222.INFEASIBLE
        shortest_ids: tuple[str, ...] | None = None
        if mechanism is not None:
            if before_gap == 0:
                shortest_kind = ShortestAdmissiblePathV222.ZERO
                shortest_ids = ()
            else:
                bootstrap_ids = tuple(item.action_id for item in bootstrap_outcomes)
                available_catalog = build_action_catalog_v22(
                    candidate_services=case.candidate_services,
                    topology=topology,
                    capability_registry=build_default_tool_capability_registry_v22(),
                    executed_action_ids=bootstrap_ids,
                    remaining_budget=3.0,
                )
                available = tuple(
                    item for item in available_catalog.actions if item.source in captured
                )
                for length, kind in (
                    (1, ShortestAdmissiblePathV222.ONE),
                    (2, ShortestAdmissiblePathV222.TWO),
                ):
                    for actions in itertools.permutations(available, length):
                        if sum(item.weighted_cost for item in actions) > 3.0:
                            continue
                        outcomes = bootstrap_outcomes
                        for action in actions:
                            outcomes, _ = _after_action(
                                case=case,
                                outcomes=outcomes,
                                action=action,
                            )
                        memory = _memory(case=case, outcomes=outcomes)
                        if (
                            _minimum_gap(
                                memory=memory,
                                mechanism=mechanism,
                                target=cast(str, target),
                                parent=parent,
                            )
                            == 0
                        ):
                            shortest_kind = kind
                            shortest_ids = tuple(item.action_id for item in actions)
                            break
                    if shortest_ids is not None:
                        break
        else:
            shortest_kind = ShortestAdmissiblePathV222.ZERO
            shortest_ids = ()
        case_audits.append(
            CaseUtilityAuditV222(
                case_id=spec.case_id,
                expected_terminal=truth.expected_terminal,
                expected_root_service=target,
                expected_mechanism=truth.expected_mechanism,
                actions=tuple(action_audits),
                shortest_admissible_path=shortest_kind,
                shortest_action_ids=shortest_ids,
            )
        )
    payload = {
        "schema_version": "dta-v22.2.evidence-utility-audit.v1",
        "case_set_sha256": semantic_sha256_v22(
            json.loads(case_set_path.read_bytes())
        ),
        "truth_set_sha256": semantic_sha256_v22(json.loads(truth_path.read_bytes())),
        "cases": tuple(case_audits),
        "infeasible_incident_cases": sum(
            item.expected_terminal == "DIAGNOSED"
            and item.shortest_admissible_path is ShortestAdmissiblePathV222.INFEASIBLE
            for item in case_audits
        ),
        "oracle_visible_to_provider": False,
    }
    draft = EvidenceUtilityAuditReportV222.model_construct(
        **payload,
        report_sha256="0" * 64,
    )
    return EvidenceUtilityAuditReportV222.model_validate(
        {
            **payload,
            "report_sha256": semantic_sha256_v22(
                draft.model_dump(mode="json", exclude={"report_sha256"})
            ),
        }
    )


def evaluate_development_routing_gate_v222(
    *, repository_root: Path, case_set_path: Path, truth_path: Path
) -> DevelopmentRoutingGateV222:
    """Evaluator-only top-4 recall gate; no oracle fields enter router input."""

    audit = audit_case_set_v222(
        repository_root=repository_root,
        case_set_path=case_set_path,
        truth_path=truth_path,
    )
    audit_by_id = {item.case_id: item for item in audit.cases}
    case_set = load_practical_case_set_v22(case_set_path)
    truths = {
        item.case_id: item for item in load_practical_truth_set_v22(truth_path).truths
    }
    turn_zero_hits = turn_zero_total = 0
    post_hits = post_total = 0
    policy = build_effective_support_policy_v222()
    for spec in case_set.cases:
        truth = truths[spec.case_id]
        audited = audit_by_id[spec.case_id]
        if truth.expected_terminal != "DIAGNOSED" or audited.shortest_action_ids in {
            None,
            (),
        }:
            continue
        case = materialize_practical_case_v22(spec=spec, repository_root=repository_root)
        topology = StaticTopologyV22.build(
            services=case.candidate_services,
            edges=case.topology_edges,
        )
        outcomes, _, _, _ = _bootstrap(case=case, topology=topology, run_id="0" * 32)
        memory = _memory(case=case, outcomes=outcomes)
        hypotheses = build_hypothesis_catalog_v22(
            candidate_services=case.candidate_services
        )
        replay_capabilities = build_replay_capabilities_v222(
            spec=spec,
            repository_root=repository_root,
        )
        bootstrap_ids = tuple(item.action_id for item in outcomes)
        catalog = build_source_aware_action_catalog_v222(
            candidate_services=case.candidate_services,
            topology=topology,
            replay_capabilities=replay_capabilities,
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
        routed = route_gap_aware_actions_v222(
            mode=GapRouterModeV222.GAP_RANKED_TOP_K,
            catalog=catalog,
            gap_graph=graph,
            prior_negative_coverage=(),
            top_k=4,
        )
        turn_zero_total += 1
        turn_zero_hits += bool(
            {item.action_id for item in routed.actions}.intersection(
                cast(tuple[str, ...], audited.shortest_action_ids)
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
            post_outcomes, _ = _after_action(
                case=case,
                outcomes=outcomes,
                action=first_action,
            )
            post_memory = _memory(case=case, outcomes=post_outcomes)
            if (
                _minimum_gap(
                    memory=post_memory,
                    mechanism=mechanism,
                    target=target,
                    parent=parent,
                )
                == 0
            ):
                continue
            post_catalog = build_source_aware_action_catalog_v222(
                candidate_services=case.candidate_services,
                topology=topology,
                replay_capabilities=replay_capabilities,
                executed_action_ids=tuple(
                    sorted({*bootstrap_ids, first_action.action_id})
                ),
                remaining_budget=3.0 - first_action.weighted_cost,
            )
            useful: set[str] = set()
            for candidate in post_catalog.actions:
                candidate_outcomes, _ = _after_action(
                    case=case,
                    outcomes=post_outcomes,
                    action=candidate,
                )
                candidate_memory = _memory(case=case, outcomes=candidate_outcomes)
                if (
                    _minimum_gap(
                        memory=candidate_memory,
                        mechanism=mechanism,
                        target=target,
                        parent=parent,
                    )
                    == 0
                ):
                    useful.add(candidate.action_id)
            if not useful:
                continue
            post_graph = build_gap_graph_v222(
                policy=policy,
                hypothesis_catalog=hypotheses,
                memory=post_memory,
                topology_edges=case.topology_edges,
                planner_focus_hypothesis_id=None,
                prior_negative_coverage=(),
            )
            post_routed = route_gap_aware_actions_v222(
                mode=GapRouterModeV222.GAP_RANKED_TOP_K,
                catalog=post_catalog,
                gap_graph=post_graph,
                prior_negative_coverage=(),
                top_k=4,
            )
            post_total += 1
            post_hits += bool(
                useful.intersection(item.action_id for item in post_routed.actions)
            )
    if not turn_zero_total or not post_total:
        raise ValueError("development routing gate lacks feasible states")
    turn_zero_recall = turn_zero_hits / turn_zero_total
    post_recall = post_hits / post_total
    return DevelopmentRoutingGateV222(
        schema_version="dta-v22.2.development-routing-gate.v1",
        feasible_turn_zero_states=turn_zero_total,
        turn_zero_hits=turn_zero_hits,
        turn_zero_recall=turn_zero_recall,
        feasible_post_first_read_states=post_total,
        post_first_read_hits=post_hits,
        post_first_read_recall=post_recall,
        gate_passed=turn_zero_recall >= 0.8 and post_recall >= 0.75,
        oracle_visible_to_provider=False,
    )


__all__ = (
    "ActionUtilityAuditV222",
    "CaseUtilityAuditV222",
    "EvidenceUtilityAuditReportV222",
    "DevelopmentRoutingGateV222",
    "ShortestAdmissiblePathV222",
    "audit_case_set_v222",
    "evaluate_development_routing_gate_v222",
)
