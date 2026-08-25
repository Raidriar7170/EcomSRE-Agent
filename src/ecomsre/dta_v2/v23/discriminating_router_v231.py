"""Conflict-directed evidence routing for DTA v2.3.1."""

from __future__ import annotations

from enum import Enum
from itertools import combinations
from typing import Any, Literal

from pydantic import Field, StrictFloat, StrictInt, model_validator

from ecomsre.dta_v2.v22.action_catalog import ActionCatalogV22
from ecomsre.dta_v2.v22.read_contracts import DtaModelV22, EvidenceSourceV22, semantic_sha256_v22
from ecomsre.dta_v2.v23.conflict_model_v231 import ConflictAssessmentV231, ConflictTypeV231
from ecomsre.dta_v2.v23.contracts import ProvisionalFaultDomainV23
from ecomsre.dta_v2.v23.discovery_router import (
    DiscoveryActionOptionV23,
    MAX_DISCOVERY_READS_V23,
    NegativeCoverageLedgerV23,
)
from ecomsre.dta_v2.v23.residual_graph import ResidualEvidenceGraphV23


class DiscriminatingGoalV231(str, Enum):
    VERIFY_RECENT_CHANGE = "VERIFY_RECENT_CHANGE"
    LOCALIZE_FIRST_ERROR = "LOCALIZE_FIRST_ERROR"
    CHECK_RUNTIME_AVAILABILITY = "CHECK_RUNTIME_AVAILABILITY"
    COMPARE_RESOURCE_PRESSURE = "COMPARE_RESOURCE_PRESSURE"
    SEPARATE_DEPENDENCY_FROM_CONCURRENCY = "SEPARATE_DEPENDENCY_FROM_CONCURRENCY"
    SEPARATE_LOCAL_FROM_PROPAGATED_FAILURE = "SEPARATE_LOCAL_FROM_PROPAGATED_FAILURE"


class RankedDiscriminatingActionV231(DtaModelV22):
    action: DiscoveryActionOptionV23
    separated_hypothesis_pairs: StrictInt = Field(ge=1)
    observed_unresolved_dimensions: StrictInt = Field(ge=1)
    new_coverage_count: StrictInt = Field(ge=0)
    negative_coverage_penalty: StrictFloat = Field(ge=0.0)


class DiscriminatingPlanV231(DtaModelV22):
    schema_version: Literal["dta-v231.discriminating-plan.v1"]
    ranked_actions: tuple[RankedDiscriminatingActionV231, ...] = Field(
        min_length=1,
        max_length=3,
    )
    selected_action: DiscoveryActionOptionV23
    discriminating_goals: tuple[DiscriminatingGoalV231, ...] = Field(min_length=1)
    expected_information_goal: str = Field(min_length=1, max_length=300)
    reads_used: StrictInt = Field(ge=0, lt=MAX_DISCOVERY_READS_V23)
    plan_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_plan(self) -> "DiscriminatingPlanV231":
        if self.selected_action != self.ranked_actions[0].action:
            raise ValueError("selected discriminating action is not top-ranked")
        ids = tuple(item.action.action_id for item in self.ranked_actions)
        if len(ids) != len(set(ids)):
            raise ValueError("discriminating plan contains duplicate actions")
        if self.discriminating_goals != tuple(
            sorted(set(self.discriminating_goals), key=lambda item: item.value)
        ):
            raise ValueError("discriminating goals are not canonical")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"plan_sha256"})
        )
        if self.plan_sha256 != expected:
            raise ValueError("discriminating plan digest differs")
        return self


_GOALS_BY_SOURCE = {
    EvidenceSourceV22.CHANGES: (DiscriminatingGoalV231.VERIFY_RECENT_CHANGE,),
    EvidenceSourceV22.TRACES: (
        DiscriminatingGoalV231.LOCALIZE_FIRST_ERROR,
        DiscriminatingGoalV231.SEPARATE_LOCAL_FROM_PROPAGATED_FAILURE,
        DiscriminatingGoalV231.SEPARATE_DEPENDENCY_FROM_CONCURRENCY,
    ),
    EvidenceSourceV22.LOGS: (
        DiscriminatingGoalV231.SEPARATE_DEPENDENCY_FROM_CONCURRENCY,
        DiscriminatingGoalV231.SEPARATE_LOCAL_FROM_PROPAGATED_FAILURE,
    ),
    EvidenceSourceV22.RUNTIME: (DiscriminatingGoalV231.CHECK_RUNTIME_AVAILABILITY,),
    EvidenceSourceV22.RESOURCES: (DiscriminatingGoalV231.COMPARE_RESOURCE_PRESSURE,),
}

_DOMAIN_SOURCES = {
    ProvisionalFaultDomainV23.CONFIGURATION: frozenset(
        {EvidenceSourceV22.CHANGES, EvidenceSourceV22.LOGS}
    ),
    ProvisionalFaultDomainV23.DEPENDENCY: frozenset(
        {EvidenceSourceV22.TRACES, EvidenceSourceV22.LOGS}
    ),
    ProvisionalFaultDomainV23.CONCURRENCY: frozenset(
        {EvidenceSourceV22.LOGS, EvidenceSourceV22.TRACES}
    ),
    ProvisionalFaultDomainV23.RUNTIME: frozenset({EvidenceSourceV22.RUNTIME}),
    ProvisionalFaultDomainV23.RESOURCE: frozenset({EvidenceSourceV22.RESOURCES}),
    ProvisionalFaultDomainV23.UNKNOWN: frozenset(
        {EvidenceSourceV22.LOGS, EvidenceSourceV22.TRACES}
    ),
}


def _option(action: object) -> DiscoveryActionOptionV23:
    targets = tuple(getattr(action, "target_services"))
    return DiscoveryActionOptionV23(
        action_id=str(getattr(action, "action_id")),
        source=getattr(action, "source"),
        target_services=targets,
        request_sha256=str(getattr(action, "request_sha256")),
        coverage_keys=tuple(getattr(action, "coverage_keys")),
        weighted_cost=float(getattr(action, "weighted_cost")),
        multi_target=len(targets) > 1,
    )


def _interpretations_v231(
    assessment: ConflictAssessmentV231,
) -> tuple[tuple[str, str, ProvisionalFaultDomainV23], ...]:
    return tuple(
        (cluster.cluster_id, root, domain)
        for cluster in assessment.interpretation_clusters
        for root in cluster.candidate_root_services
        for domain in cluster.broad_domains
    )


def _separated_pairs_v231(
    *,
    option: DiscoveryActionOptionV23,
    assessment: ConflictAssessmentV231,
) -> tuple[tuple[str, str], ...]:
    targets = set(option.target_services)
    separated: list[tuple[str, str]] = []
    for left, right in combinations(_interpretations_v231(assessment), 2):
        left_id = f"{left[0]}:{left[1]}:{left[2].value}"
        right_id = f"{right[0]}:{right[1]}:{right[2].value}"
        root_difference = left[1] != right[1]
        domain_difference = left[2] is not right[2]
        root_discriminating = (
            root_difference
            and option.source
            in {
                EvidenceSourceV22.LOGS,
                EvidenceSourceV22.TRACES,
                EvidenceSourceV22.RESOURCES,
                EvidenceSourceV22.RUNTIME,
            }
            and bool(targets.intersection({left[1], right[1]}))
        )
        domain_discriminating = (
            domain_difference
            and option.source
            in _DOMAIN_SOURCES[left[2]].union(_DOMAIN_SOURCES[right[2]])
            and bool(targets.intersection({left[1], right[1]}))
        )
        if root_discriminating or domain_discriminating:
            first, second = sorted((left_id, right_id))
            separated.append((first, second))
    return tuple(sorted(set(separated)))


def build_discriminating_plan_v231(
    *,
    catalog: ActionCatalogV22,
    graph: ResidualEvidenceGraphV23,
    assessment: ConflictAssessmentV231,
    negative_coverage: NegativeCoverageLedgerV23,
    reads_used: int,
    remaining_weighted_budget: float,
    excluded_action_ids: tuple[str, ...] = (),
) -> DiscriminatingPlanV231 | None:
    if not 0 <= reads_used <= MAX_DISCOVERY_READS_V23:
        raise ValueError("discriminating read count is outside the shared bound")
    if remaining_weighted_budget < 0:
        raise ValueError("discriminating remaining budget cannot be negative")
    if assessment.conflict_type is not ConflictTypeV231.RESOLVABLE_CONFLICT:
        return None
    if reads_used == MAX_DISCOVERY_READS_V23 or remaining_weighted_budget == 0:
        return None
    covered = {
        (item.source, service)
        for item in graph.source_coverage
        for service in item.covered_services
    }
    ranked: list[tuple[tuple[object, ...], RankedDiscriminatingActionV231]] = []
    for action in catalog.registry_actions:
        option = _option(action)
        if (
            option.source not in set(assessment.discriminating_sources)
            or option.weighted_cost > remaining_weighted_budget
            or option.action_id in set(excluded_action_ids)
            or negative_coverage.blocks(option)
            or any(
                item.source is option.source
                and item.target_services == option.target_services
                for item in negative_coverage.entries
            )
        ):
            continue
        goals = _GOALS_BY_SOURCE.get(option.source, ())
        if not goals:
            continue
        separated_pairs = _separated_pairs_v231(
            option=option,
            assessment=assessment,
        )
        pair_count = len(separated_pairs)
        if pair_count == 0:
            continue
        dimensions = sum(
            (
                dimension == "ROOT_SERVICE"
                and option.source
                in {
                    EvidenceSourceV22.LOGS,
                    EvidenceSourceV22.TRACES,
                    EvidenceSourceV22.RESOURCES,
                    EvidenceSourceV22.RUNTIME,
                }
            )
            or (
                dimension in {"BROAD_FAULT_DOMAIN", "CAUSAL_MECHANISM"}
                and bool(goals)
            )
            for dimension in assessment.unresolved_dimensions
        )
        dimensions = max(1, dimensions)
        new_coverage = sum(
            (option.source, service) not in covered for service in option.target_services
        )
        if new_coverage == 0:
            continue
        value = RankedDiscriminatingActionV231(
            action=option,
            separated_hypothesis_pairs=pair_count,
            observed_unresolved_dimensions=dimensions,
            new_coverage_count=new_coverage,
            negative_coverage_penalty=0.0,
        )
        rank = (
            -pair_count,
            -dimensions,
            -new_coverage,
            value.negative_coverage_penalty,
            option.weighted_cost,
            option.action_id,
        )
        ranked.append((rank, value))
    ordered = tuple(item for _rank, item in sorted(ranked, key=lambda item: item[0]))[:3]
    if not ordered:
        return None
    goals = tuple(
        sorted(
            {
                goal
                for item in ordered
                for goal in _GOALS_BY_SOURCE.get(item.action.source, ())
            },
            key=lambda item: item.value,
        )
    )
    payload: dict[str, Any] = {
        "schema_version": "dta-v231.discriminating-plan.v1",
        "ranked_actions": ordered,
        "selected_action": ordered[0].action,
        "discriminating_goals": goals,
        "expected_information_goal": (
            "Distinguish the current evidence-backed interpretations on "
            + ", ".join(assessment.unresolved_dimensions).casefold()
            + "."
        ),
        "reads_used": reads_used,
    }
    draft = DiscriminatingPlanV231.model_construct(**payload, plan_sha256="0" * 64)
    return DiscriminatingPlanV231.model_validate(
        {
            **payload,
            "plan_sha256": semantic_sha256_v22(
                draft.model_dump(mode="json", exclude={"plan_sha256"})
            ),
        }
    )


__all__ = (
    "DiscriminatingGoalV231",
    "DiscriminatingPlanV231",
    "RankedDiscriminatingActionV231",
    "build_discriminating_plan_v231",
)
