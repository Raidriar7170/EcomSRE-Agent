"""Mechanism-independent, bounded evidence routing for DTA v2.3."""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import Field, StrictFloat, StrictInt, model_validator

from ecomsre.dta_v2.v22.action_catalog import ActionCatalogV22, EvidenceActionV22
from ecomsre.dta_v2.v22.contrastive_actions_v225 import (
    contrastive_resource_action_if_eligible_v225,
)
from ecomsre.dta_v2.v22.memory import SignalStrengthV22
from ecomsre.dta_v2.v22.read_contracts import (
    DtaModelV22,
    EvidenceSourceV22,
    semantic_sha256_v22,
)
from ecomsre.dta_v2.v22.replay_target_coverage_v225 import (
    build_replay_target_coverage_v225,
)
from ecomsre.dta_v2.v23.generic_anomalies import GenericAnomalyKindV23
from ecomsre.dta_v2.v23.residual_graph import ResidualEvidenceGraphV23


MAX_DISCOVERY_READS_V23 = 3


class DiscoveryReasonCodeV23(str, Enum):
    LOCALIZE_FIRST_ERROR = "LOCALIZE_FIRST_ERROR"
    COMPARE_RESOURCE_STATE = "COMPARE_RESOURCE_STATE"
    CHECK_RUNTIME_FAILURE_CONTEXT = "CHECK_RUNTIME_FAILURE_CONTEXT"
    CORRELATE_RECENT_CHANGE = "CORRELATE_RECENT_CHANGE"
    INSPECT_UNKNOWN_LOG_PATTERN = "INSPECT_UNKNOWN_LOG_PATTERN"
    FILL_MINIMUM_COVERAGE = "FILL_MINIMUM_COVERAGE"


class DiscoveryReadOutcomeClassV23(str, Enum):
    ANOMALY_YIELD = "ANOMALY_YIELD"
    EMPTY_CAPTURED = "EMPTY_CAPTURED"
    NONEMPTY_NO_NEW_ANOMALY = "NONEMPTY_NO_NEW_ANOMALY"
    SOURCE_FAILURE = "SOURCE_FAILURE"


class DiscoveryActionOptionV23(DtaModelV22):
    action_id: str
    source: EvidenceSourceV22
    target_services: tuple[str, ...] = Field(min_length=1, max_length=4)
    request_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    coverage_keys: tuple[str, ...]
    weighted_cost: StrictFloat = Field(gt=0.0, le=3.0)
    multi_target: bool

    @model_validator(mode="after")
    def require_option(self) -> "DiscoveryActionOptionV23":
        if self.target_services != tuple(sorted(set(self.target_services))):
            raise ValueError("discovery action targets are not canonical")
        if self.coverage_keys != tuple(sorted(set(self.coverage_keys))):
            raise ValueError("discovery action coverage is not canonical")
        if self.multi_target != (len(self.target_services) > 1):
            raise ValueError("discovery action multi-target marker differs")
        return self


class NegativeCoverageEntryV23(DtaModelV22):
    action_id: str
    source: EvidenceSourceV22
    target_services: tuple[str, ...]
    outcome_class: DiscoveryReadOutcomeClassV23
    new_anomaly_ids: tuple[str, ...]


class NegativeCoverageLedgerV23(DtaModelV22):
    schema_version: Literal["dta-v23.negative-coverage-ledger.v1"]
    entries: tuple[NegativeCoverageEntryV23, ...]
    ledger_sha256: str

    @classmethod
    def empty(cls) -> "NegativeCoverageLedgerV23":
        payload: dict[str, Any] = {
            "schema_version": "dta-v23.negative-coverage-ledger.v1",
            "entries": (),
        }
        return cls.model_validate(
            {**payload, "ledger_sha256": semantic_sha256_v22(payload)}
        )

    @model_validator(mode="after")
    def require_ledger(self) -> "NegativeCoverageLedgerV23":
        keys = tuple((item.source, item.target_services) for item in self.entries)
        if len(keys) != len(set(keys)):
            raise ValueError("negative coverage contains an equivalent repeat")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"ledger_sha256"})
        )
        if self.ledger_sha256 != expected:
            raise ValueError("negative coverage digest differs")
        return self

    def blocks(self, option: DiscoveryActionOptionV23) -> bool:
        blocking = {
            DiscoveryReadOutcomeClassV23.EMPTY_CAPTURED,
            DiscoveryReadOutcomeClassV23.NONEMPTY_NO_NEW_ANOMALY,
            DiscoveryReadOutcomeClassV23.SOURCE_FAILURE,
        }
        return any(
            item.source is option.source
            and item.target_services == option.target_services
            and item.outcome_class in blocking
            for item in self.entries
        )


class DiscoveryPlanV23(DtaModelV22):
    schema_version: Literal["dta-v23.discovery-plan.v1"]
    ranked_actions: tuple[DiscoveryActionOptionV23, ...] = Field(
        min_length=1,
        max_length=3,
    )
    selected_action: DiscoveryActionOptionV23
    reason_code: DiscoveryReasonCodeV23
    coverage_before: tuple[str, ...]
    expected_information_goal: str = Field(min_length=1, max_length=240)
    reads_used: StrictInt = Field(ge=0, lt=MAX_DISCOVERY_READS_V23)
    plan_sha256: str

    @model_validator(mode="after")
    def require_plan(self) -> "DiscoveryPlanV23":
        if self.selected_action != self.ranked_actions[0]:
            raise ValueError("selected discovery action is not top-ranked")
        ids = tuple(item.action_id for item in self.ranked_actions)
        if len(ids) != len(set(ids)):
            raise ValueError("discovery plan contains duplicate actions")
        expected = semantic_sha256_v22(
            self.model_dump(mode="json", exclude={"plan_sha256"})
        )
        if self.plan_sha256 != expected:
            raise ValueError("discovery plan digest differs")
        return self


def _option(action: object) -> DiscoveryActionOptionV23:
    return DiscoveryActionOptionV23(
        action_id=str(getattr(action, "action_id")),
        source=getattr(action, "source"),
        target_services=getattr(action, "target_services"),
        request_sha256=str(getattr(action, "request_sha256")),
        coverage_keys=getattr(action, "coverage_keys"),
        weighted_cost=float(getattr(action, "weighted_cost")),
        multi_target=len(getattr(action, "target_services")) > 1,
    )


def _priority(
    graph: ResidualEvidenceGraphV23,
) -> tuple[tuple[EvidenceSourceV22, ...], DiscoveryReasonCodeV23, str]:
    kinds = {item.kind for item in graph.generic_anomalies}
    strong_kinds = {
        item.kind
        for item in graph.generic_anomalies
        if item.strength is SignalStrengthV22.STRONG
    }
    runtime = {
        GenericAnomalyKindV23.RUNTIME_NOT_RUNNING,
        GenericAnomalyKindV23.RUNTIME_UNHEALTHY,
        GenericAnomalyKindV23.RUNTIME_RESTART_ANOMALY,
    }
    if kinds.intersection(runtime):
        return (
            (
                EvidenceSourceV22.LOGS,
                EvidenceSourceV22.TRACES,
                EvidenceSourceV22.RESOURCES,
            ),
            DiscoveryReasonCodeV23.CHECK_RUNTIME_FAILURE_CONTEXT,
            "Find mechanism-independent context for the abnormal runtime surface.",
        )
    if GenericAnomalyKindV23.METRIC_ERROR_OUTLIER in strong_kinds:
        return (
            (
                EvidenceSourceV22.LOGS,
                EvidenceSourceV22.TRACES,
                EvidenceSourceV22.CHANGES,
                EvidenceSourceV22.RESOURCES,
            ),
            DiscoveryReasonCodeV23.LOCALIZE_FIRST_ERROR,
            "Localize the first independent error evidence without ontology clauses.",
        )
    if GenericAnomalyKindV23.METRIC_LATENCY_OUTLIER in strong_kinds:
        return (
            (
                EvidenceSourceV22.TRACES,
                EvidenceSourceV22.RESOURCES,
                EvidenceSourceV22.LOGS,
                EvidenceSourceV22.CHANGES,
            ),
            DiscoveryReasonCodeV23.LOCALIZE_FIRST_ERROR,
            "Localize the latency surface and compare adjacent service state.",
        )
    return (
        (
            EvidenceSourceV22.RESOURCES,
            EvidenceSourceV22.LOGS,
            EvidenceSourceV22.TRACES,
        ),
        DiscoveryReasonCodeV23.COMPARE_RESOURCE_STATE,
        "Fill minimum discriminating coverage with a target-complete comparison.",
    )


def _canonical_options(
    *,
    catalog: ActionCatalogV22,
    source_priorities: tuple[EvidenceSourceV22, ...],
    graph: ResidualEvidenceGraphV23,
) -> list[DiscoveryActionOptionV23]:
    anomaly_services = tuple(
        dict.fromkeys(
            item.service
            for item in graph.generic_anomalies
            if item.service in set(catalog.candidate_services)
        )
    )
    target_rank = (*anomaly_services, *catalog.candidate_services)
    result: list[DiscoveryActionOptionV23] = []
    by_source_target = {
        (item.source, item.target_services): item
        for item in catalog.registry_actions
        if item.source not in {EvidenceSourceV22.RUNTIME, EvidenceSourceV22.METRICS}
    }
    for source in source_priorities:
        for target in dict.fromkeys(target_rank):
            action = by_source_target.get((source, (target,)))
            if action is not None:
                result.append(_option(action))
    return result


def build_discovery_plan_v23(
    *,
    catalog: ActionCatalogV22,
    graph: ResidualEvidenceGraphV23,
    negative_coverage: NegativeCoverageLedgerV23,
    reads_used: int,
    remaining_weighted_budget: float,
    target_complete_resource_coverage: bool,
    excluded_action_ids: tuple[str, ...] = (),
) -> DiscoveryPlanV23 | None:
    """Return at most three generic choices; never route after the hard read cap."""

    if not 0 <= reads_used <= MAX_DISCOVERY_READS_V23:
        raise ValueError("discovery read count is outside the bounded lane")
    if remaining_weighted_budget < 0:
        raise ValueError("discovery remaining budget cannot be negative")
    if excluded_action_ids != tuple(sorted(set(excluded_action_ids))):
        raise ValueError("excluded discovery action IDs are not canonical")
    if reads_used == MAX_DISCOVERY_READS_V23 or remaining_weighted_budget == 0:
        return None
    source_priorities, reason, goal = _priority(graph)
    options: list[DiscoveryActionOptionV23] = []
    if (
        source_priorities[0] is EvidenceSourceV22.RESOURCES
        and target_complete_resource_coverage
        and len(catalog.candidate_services) >= 2
    ):
        coverage = build_replay_target_coverage_v225(
            source=EvidenceSourceV22.RESOURCES,
            candidate_services=catalog.candidate_services,
            covered_target_services=catalog.candidate_services,
        )
        bundle = contrastive_resource_action_if_eligible_v225(
            coverage=coverage,
            resources_enabled=True,
            unresolved_resource_hypotheses=len(catalog.candidate_services),
            remaining_budget=remaining_weighted_budget,
            bundle_mode=True,
        )
        if bundle is not None:
            options.append(_option(bundle))
    options.extend(
        _canonical_options(
            catalog=catalog,
            source_priorities=source_priorities,
            graph=graph,
        )
    )
    eligible = tuple(
        item
        for item in options
        if item.weighted_cost <= remaining_weighted_budget
        and item.action_id not in set(excluded_action_ids)
        and not negative_coverage.blocks(item)
        and not any(
            entry.source is item.source
            and entry.target_services == item.target_services
            for entry in negative_coverage.entries
        )
    )
    ranked = tuple(dict.fromkeys(eligible))[:MAX_DISCOVERY_READS_V23]
    if not ranked:
        return None
    payload: dict[str, Any] = {
        "schema_version": "dta-v23.discovery-plan.v1",
        "ranked_actions": ranked,
        "selected_action": ranked[0],
        "reason_code": reason,
        "coverage_before": tuple(
            sorted(
                f"{item.source.value}:{service}"
                for item in graph.source_coverage
                for service in item.covered_services
            )
        ),
        "expected_information_goal": goal,
        "reads_used": reads_used,
    }
    draft = DiscoveryPlanV23.model_construct(**payload, plan_sha256="0" * 64)
    return DiscoveryPlanV23.model_validate(
        {
            **payload,
            "plan_sha256": semantic_sha256_v22(
                draft.model_dump(mode="json", exclude={"plan_sha256"})
            ),
        }
    )


def record_discovery_outcome_v23(
    *,
    ledger: NegativeCoverageLedgerV23,
    action: DiscoveryActionOptionV23,
    outcome_class: DiscoveryReadOutcomeClassV23,
    new_anomaly_ids: tuple[str, ...],
) -> NegativeCoverageLedgerV23:
    if any(
        item.source is action.source and item.target_services == action.target_services
        for item in ledger.entries
    ):
        raise ValueError("equivalent discovery read was already recorded")
    if (outcome_class is DiscoveryReadOutcomeClassV23.ANOMALY_YIELD) != bool(
        new_anomaly_ids
    ):
        raise ValueError("discovery anomaly yield differs from new anomaly IDs")
    entry = NegativeCoverageEntryV23(
        action_id=action.action_id,
        source=action.source,
        target_services=action.target_services,
        outcome_class=outcome_class,
        new_anomaly_ids=tuple(sorted(set(new_anomaly_ids))),
    )
    payload: dict[str, Any] = {
        "schema_version": "dta-v23.negative-coverage-ledger.v1",
        "entries": (*ledger.entries, entry),
    }
    draft = NegativeCoverageLedgerV23.model_construct(
        **payload,
        ledger_sha256="0" * 64,
    )
    return NegativeCoverageLedgerV23.model_validate(
        {
            **payload,
            "ledger_sha256": semantic_sha256_v22(
                draft.model_dump(mode="json", exclude={"ledger_sha256"})
            ),
        }
    )


def resolve_discovery_action_v23(
    *,
    option: DiscoveryActionOptionV23,
    catalog: ActionCatalogV22,
    target_complete_resource_coverage: bool,
) -> object:
    canonical = next(
        (item for item in catalog.registry_actions if item.action_id == option.action_id),
        None,
    )
    if canonical is not None:
        return EvidenceActionV22.model_validate(canonical.model_dump(mode="python"))
    if option.source is EvidenceSourceV22.RESOURCES and option.multi_target:
        coverage = build_replay_target_coverage_v225(
            source=EvidenceSourceV22.RESOURCES,
            candidate_services=catalog.candidate_services,
            covered_target_services=(
                catalog.candidate_services if target_complete_resource_coverage else ()
            ),
        )
        action = contrastive_resource_action_if_eligible_v225(
            coverage=coverage,
            resources_enabled=True,
            unresolved_resource_hypotheses=len(catalog.candidate_services),
            remaining_budget=option.weighted_cost,
            bundle_mode=True,
        )
        if action is not None and action.action_id == option.action_id:
            return action
    raise ValueError("discovery action cannot be resolved against the bound catalog")


__all__ = (
    "DiscoveryActionOptionV23",
    "DiscoveryPlanV23",
    "DiscoveryReadOutcomeClassV23",
    "DiscoveryReasonCodeV23",
    "MAX_DISCOVERY_READS_V23",
    "NegativeCoverageEntryV23",
    "NegativeCoverageLedgerV23",
    "build_discovery_plan_v23",
    "record_discovery_outcome_v23",
    "resolve_discovery_action_v23",
)
