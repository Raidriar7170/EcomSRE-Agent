"""Truth-independent v2.2.3 action ranking with a frozen development prior."""

from __future__ import annotations

import hashlib
from typing import Literal

from pydantic import Field, StrictBool, StrictFloat, StrictInt, model_validator

from ecomsre.dta_v2.v22.action_catalog import ActionCatalogV22, EvidenceActionV22
from ecomsre.dta_v2.v22.gap_graph_v222 import GapGraphV222, PredicateGapV222
from ecomsre.dta_v2.v22.gap_router_v222 import (
    GapRouterModeV222,
    SOURCE_PREDICATE_CAPABILITIES_V222,
)
from ecomsre.dta_v2.v22.memory import PredicateKindV22
from ecomsre.dta_v2.v22.predicates import RequirementServiceBindingV22
from ecomsre.dta_v2.v22.read_contracts import (
    DtaModelV22,
    EvidenceSourceV22,
    semantic_sha256_v22,
)


_NON_ANOMALOUS_PREDICATES = {
    PredicateKindV22.CHANGE_RECENT_ROLLOUT,
    PredicateKindV22.RUNTIME_HEALTHY,
}


class PredicateYieldPriorV223(DtaModelV22):
    source: EvidenceSourceV22
    predicate_kind: PredicateKindV22
    successes: StrictInt = Field(ge=0)
    trials: StrictInt = Field(ge=1)
    alpha: Literal[1]
    beta: Literal[1]
    posterior_mean: StrictFloat = Field(gt=0, lt=1)

    @model_validator(mode="after")
    def require_prior(self) -> "PredicateYieldPriorV223":
        if self.successes > self.trials:
            raise ValueError("predicate-yield successes exceed trials")
        expected = (self.successes + self.alpha) / (
            self.trials + self.alpha + self.beta
        )
        if abs(self.posterior_mean - expected) > 1e-12:
            raise ValueError("predicate-yield posterior differs")
        if self.predicate_kind not in SOURCE_PREDICATE_CAPABILITIES_V222[self.source]:
            raise ValueError("predicate-yield prior differs from source capability")
        return self


class RankedActionV223(DtaModelV22):
    action: EvidenceActionV22
    active_shortest_clauses_completable: StrictInt = Field(ge=0)
    active_predicate_yield_prior: StrictFloat = Field(ge=0, lt=1)
    shortest_clauses_completable: StrictInt = Field(ge=0)
    distinct_missing_requirements_observable: StrictInt = Field(ge=0)
    active_hypotheses_reduced: StrictInt = Field(ge=0)
    prior_empty_penalty: StrictBool
    weighted_cost: StrictFloat
    canonical_tie_break_sha256: str
    rank_ordinal: StrictInt = Field(ge=1)


class GapRoutingResultV223(DtaModelV22):
    schema_version: Literal["dta-v22.3.gap-routing-result.v1"]
    mode: GapRouterModeV222
    catalog_sha256: str
    gap_graph_sha256: str
    predicate_yield_prior_sha256: str
    actions: tuple[EvidenceActionV22, ...]
    ranking: tuple[RankedActionV223, ...]
    top_k: StrictInt = Field(ge=1, le=64)
    truth_consulted: Literal[False]
    routing_sha256: str

    @model_validator(mode="after")
    def require_result(self) -> "GapRoutingResultV223":
        if tuple(item.action for item in self.ranking)[: len(self.actions)] != self.actions:
            raise ValueError("v2.2.3 routed actions differ from ranking prefix")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"routing_sha256"})
        )
        if self.routing_sha256 != expected:
            raise ValueError("v2.2.3 gap routing digest differs")
        return self


def action_can_observe_gap_v223(
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
        allowed.add(gap.parent_service)
    return bool(targets.intersection(allowed))


def route_gap_aware_actions_v223(
    *,
    mode: GapRouterModeV222,
    catalog: ActionCatalogV22,
    gap_graph: GapGraphV222,
    prior_negative_coverage: tuple[str, ...],
    predicate_yield_priors: tuple[PredicateYieldPriorV223, ...],
    top_k: int = 4,
) -> GapRoutingResultV223:
    if not 1 <= top_k <= 64:
        raise ValueError("v2.2.3 gap router top_k is out of bounds")
    prior_map = {
        (item.source, item.predicate_kind): item.posterior_mean
        for item in predicate_yield_priors
    }
    expected_prior_keys = {
        (source, kind)
        for source, kinds in SOURCE_PREDICATE_CAPABILITIES_V222.items()
        for kind in kinds
    }
    if set(prior_map) != expected_prior_keys:
        raise ValueError("v2.2.3 predicate-yield prior scope differs")
    prior_digest = semantic_sha256_v22(
        tuple(item.model_dump(mode="json") for item in predicate_yield_priors)
    )
    prior_empty = set(prior_negative_coverage)
    scored: list[tuple[tuple[object, ...], RankedActionV223]] = []
    for action in catalog.actions:
        completable = active_completable = 0
        observable: set[tuple[str, str, PredicateKindV22, str]] = set()
        reduced_hypotheses: set[str] = set()
        active_priors: list[float] = []
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
                    if action_can_observe_gap_v223(action=action, gap=gap)
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
                completes = bool(hits) and len(hits) == len(clause.missing_requirements)
                if completes:
                    completable += 1
                active = any(
                    kind not in _NON_ANOMALOUS_PREDICATES
                    for kind in clause.satisfied_predicate_kinds
                )
                if completes and active:
                    active_completable += 1
                    active_priors.extend(
                        prior_map[(action.source, gap.predicate_kind)] for gap in hits
                    )
        penalty = any(
            f"{action.source.value}:{target}" in prior_empty
            for target in action.target_services
        )
        active_prior = max(active_priors, default=0.0)
        # A raw lexical action-ID tie systematically favors alphabetically early
        # service names.  The versioned digest preserves a canonical total order
        # without using case identity, evaluator truth, or future read outcomes.
        tie_break = hashlib.sha256(
            f"dta-v223|{action.action_id}".encode("utf-8")
        ).hexdigest()
        ranked = RankedActionV223(
            action=action,
            active_shortest_clauses_completable=active_completable,
            active_predicate_yield_prior=active_prior,
            shortest_clauses_completable=completable,
            distinct_missing_requirements_observable=len(observable),
            active_hypotheses_reduced=len(reduced_hypotheses),
            prior_empty_penalty=penalty,
            weighted_cost=action.weighted_cost,
            canonical_tie_break_sha256=tie_break,
            rank_ordinal=1,
        )
        key = (
            -active_completable,
            -active_prior,
            -completable,
            -len(observable),
            -len(reduced_hypotheses),
            penalty,
            action.weighted_cost,
            tie_break,
            action.action_id,
        )
        scored.append((key, ranked))
    ranking = tuple(
        item.model_copy(update={"rank_ordinal": index})
        for index, (_, item) in enumerate(sorted(scored, key=lambda row: row[0]), 1)
    )
    actions = tuple(item.action for item in ranking[:top_k])
    payload = {
        "schema_version": "dta-v22.3.gap-routing-result.v1",
        "mode": mode.value,
        "catalog_sha256": catalog.catalog_sha256,
        "gap_graph_sha256": gap_graph.graph_sha256,
        "predicate_yield_prior_sha256": prior_digest,
        "actions": tuple(item.model_dump(mode="json") for item in actions),
        "ranking": tuple(item.model_dump(mode="json") for item in ranking),
        "top_k": top_k,
        "truth_consulted": False,
    }
    return GapRoutingResultV223(
        schema_version="dta-v22.3.gap-routing-result.v1",
        mode=mode,
        catalog_sha256=catalog.catalog_sha256,
        gap_graph_sha256=gap_graph.graph_sha256,
        predicate_yield_prior_sha256=prior_digest,
        actions=actions,
        ranking=ranking,
        top_k=top_k,
        truth_consulted=False,
        routing_sha256=semantic_sha256_v22(payload),
    )


__all__ = (
    "GapRoutingResultV223",
    "PredicateYieldPriorV223",
    "RankedActionV223",
    "action_can_observe_gap_v223",
    "route_gap_aware_actions_v223",
)
