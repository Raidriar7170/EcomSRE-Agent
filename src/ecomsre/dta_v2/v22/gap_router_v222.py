"""Deterministic predicate-gap-aware action ranking for DTA v2.2.2."""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import Field, StrictBool, StrictFloat, StrictInt, model_validator

from ecomsre.dta_v2.v22.action_catalog import ActionCatalogV22, EvidenceActionV22
from ecomsre.dta_v2.v22.gap_graph_v222 import GapGraphV222, PredicateGapV222
from ecomsre.dta_v2.v22.memory import PredicateKindV22
from ecomsre.dta_v2.v22.predicates import RequirementServiceBindingV22
from ecomsre.dta_v2.v22.read_contracts import (
    DtaModelV22,
    EvidenceSourceV22,
    semantic_sha256_v22,
)


SOURCE_PREDICATE_CAPABILITIES_V222 = {
    EvidenceSourceV22.METRICS: frozenset(
        {
            PredicateKindV22.METRIC_ERROR_RATE_STRONG,
            PredicateKindV22.METRIC_LATENCY_STRONG,
        }
    ),
    EvidenceSourceV22.LOGS: frozenset(
        {
            PredicateKindV22.LOG_CONFIGURATION_ERROR,
            PredicateKindV22.LOG_DEPENDENCY_TIMEOUT,
            PredicateKindV22.LOG_MEMORY_PRESSURE,
        }
    ),
    EvidenceSourceV22.TRACES: frozenset(
        {
            PredicateKindV22.TRACE_FIRST_ERROR,
            PredicateKindV22.TRACE_DEPENDENCY_LATENCY,
        }
    ),
    EvidenceSourceV22.RUNTIME: frozenset(
        {
            PredicateKindV22.RUNTIME_NOT_RUNNING,
            PredicateKindV22.RUNTIME_UNHEALTHY,
            PredicateKindV22.RUNTIME_HEALTHY,
            PredicateKindV22.RUNTIME_RESTART_PRESSURE,
        }
    ),
    EvidenceSourceV22.RESOURCES: frozenset(
        {
            PredicateKindV22.RESOURCE_CPU_STRONG,
            PredicateKindV22.RESOURCE_MEMORY_GROWTH_STRONG,
        }
    ),
    EvidenceSourceV22.CHANGES: frozenset(
        {PredicateKindV22.CHANGE_RECENT_ROLLOUT}
    ),
}


class GapRouterModeV222(str, Enum):
    BROAD_CATALOG = "BROAD_CATALOG"
    GAP_RANKED_TOP_K = "GAP_RANKED_TOP_K"


class RankedActionV222(DtaModelV22):
    action: EvidenceActionV22
    shortest_clauses_completable: StrictInt = Field(ge=0)
    distinct_missing_requirements_observable: StrictInt = Field(ge=0)
    active_hypotheses_reduced: StrictInt = Field(ge=0)
    prior_empty_penalty: StrictBool
    weighted_cost: StrictFloat
    rank_ordinal: StrictInt = Field(ge=1)


class GapRoutingResultV222(DtaModelV22):
    schema_version: Literal["dta-v22.2.gap-routing-result.v1"]
    mode: GapRouterModeV222
    catalog_sha256: str
    gap_graph_sha256: str
    actions: tuple[EvidenceActionV22, ...]
    ranking: tuple[RankedActionV222, ...]
    top_k: StrictInt = Field(ge=1, le=64)
    truth_consulted: Literal[False]
    routing_sha256: str

    @model_validator(mode="after")
    def require_result(self) -> "GapRoutingResultV222":
        if tuple(item.action for item in self.ranking)[: len(self.actions)] != self.actions:
            raise ValueError("routed actions differ from ranking prefix")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"routing_sha256"})
        )
        if self.routing_sha256 != expected:
            raise ValueError("gap routing digest differs")
        return self


def _action_can_observe_gap(
    *, action: EvidenceActionV22, gap: PredicateGapV222
) -> bool:
    if gap.predicate_kind not in SOURCE_PREDICATE_CAPABILITIES_V222[action.source]:
        return False
    targets = set(action.target_services)
    allowed = {gap.target_service}
    if gap.service_binding is RequirementServiceBindingV22.TARGET_OR_PARENT:
        if gap.parent_service is not None:
            allowed.add(gap.parent_service)
    if gap.require_exact_parent and gap.parent_service is not None:
        # A radius-one trace query at either endpoint can observe the exact edge.
        allowed.add(gap.parent_service)
    return bool(targets.intersection(allowed))


def _empty_key(*, source: EvidenceSourceV22, target: str) -> str:
    return f"{source.value}:{target}"


def route_gap_aware_actions_v222(
    *,
    mode: GapRouterModeV222,
    catalog: ActionCatalogV22,
    gap_graph: GapGraphV222,
    prior_negative_coverage: tuple[str, ...],
    top_k: int = 4,
) -> GapRoutingResultV222:
    if not 1 <= top_k <= 64:
        raise ValueError("gap router top_k is out of bounds")
    prior_empty = set(prior_negative_coverage)
    scored: list[
        tuple[tuple[object, ...], EvidenceActionV22, int, int, int, bool]
    ] = []
    for action in catalog.actions:
        completable = 0
        observable: set[tuple[str, str, PredicateKindV22, str]] = set()
        reduced_hypotheses: set[str] = set()
        for hypothesis in gap_graph.hypotheses:
            if hypothesis.complete:
                continue
            shortest = tuple(
                clause
                for clause in hypothesis.clauses
                if clause.missing_count == hypothesis.minimum_missing_count
            )
            for clause in shortest:
                hits = tuple(
                    gap
                    for gap in clause.missing_requirements
                    if _action_can_observe_gap(action=action, gap=gap)
                )
                for gap in hits:
                    observable.add(
                        (
                            hypothesis.hypothesis_id,
                            clause.clause_id,
                            gap.predicate_kind,
                            gap.target_service,
                        )
                    )
                    reduced_hypotheses.add(hypothesis.hypothesis_id)
                if hits and len(hits) == len(clause.missing_requirements):
                    completable += 1
        penalty = any(
            _empty_key(source=action.source, target=target) in prior_empty
            for target in action.target_services
        )
        key = (
            -completable,
            -len(observable),
            -len(reduced_hypotheses),
            penalty,
            action.weighted_cost,
            action.action_id,
        )
        scored.append(
            (
                key,
                action,
                completable,
                len(observable),
                len(reduced_hypotheses),
                penalty,
            )
        )
    ordered = tuple(sorted(scored, key=lambda item: item[0]))
    ranking = tuple(
        RankedActionV222(
            action=action,
            shortest_clauses_completable=completable,
            distinct_missing_requirements_observable=observable,
            active_hypotheses_reduced=hypotheses,
            prior_empty_penalty=penalty,
            weighted_cost=action.weighted_cost,
            rank_ordinal=index,
        )
        for index, (_, action, completable, observable, hypotheses, penalty) in enumerate(
            ordered,
            start=1,
        )
    )
    actions = (
        tuple(item.action for item in ranking)
        if mode is GapRouterModeV222.BROAD_CATALOG
        else tuple(item.action for item in ranking[:top_k])
    )
    if mode is GapRouterModeV222.BROAD_CATALOG:
        # Broad is deliberately canonical, not utility-ranked.
        actions = catalog.actions
        by_id = {item.action.action_id: item for item in ranking}
        ranking = tuple(
            by_id[action.action_id].model_copy(update={"rank_ordinal": index})
            for index, action in enumerate(catalog.actions, start=1)
        )
    payload = {
        "schema_version": "dta-v22.2.gap-routing-result.v1",
        "mode": mode,
        "catalog_sha256": catalog.catalog_sha256,
        "gap_graph_sha256": gap_graph.graph_sha256,
        "actions": actions,
        "ranking": ranking,
        "top_k": top_k,
        "truth_consulted": False,
    }
    draft = GapRoutingResultV222.model_construct(
        **payload,
        routing_sha256="0" * 64,
    )
    return GapRoutingResultV222.model_validate(
        {
            **payload,
            "routing_sha256": semantic_sha256_v22(
                draft.model_dump(mode="json", exclude={"routing_sha256"})
            ),
        }
    )


__all__ = (
    "GapRouterModeV222",
    "GapRoutingResultV222",
    "RankedActionV222",
    "SOURCE_PREDICATE_CAPABILITIES_V222",
    "route_gap_aware_actions_v222",
)
