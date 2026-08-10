"""Unified private case contract and deterministic attribution taxonomies."""

from __future__ import annotations

from dataclasses import dataclass
import re

from ecomsre_rca_unified.contracts import (
    CanonicalEntityLayer,
    CausalCandidate,
    EvidenceVisibilitySummary,
    EntityHierarchyPath,
    FaultOntologyClass,
    FrontierCase,
    PropagationDisposition,
)


@dataclass(frozen=True, slots=True)
class UnifiedMetricCandidate:
    entity: str
    service_ancestor: str | None
    layer: CanonicalEntityLayer
    rank: int
    score: float
    metric_family: str
    first_anomaly_time: float | None
    source_support: int
    relation_to_symptom: str

    def __post_init__(self) -> None:
        if not self.entity or not 1 <= self.rank <= 6:
            raise ValueError("unified Metrics candidate identity or rank is invalid")
        if self.source_support < 0:
            raise ValueError("unified Metrics source support cannot be negative")


@dataclass(frozen=True, slots=True)
class UnifiedRCACase:
    private_case_key: str
    fixture: str
    benchmark: str
    system: str
    fault_family: str
    fault_type_truth: str
    fault_type_raw: str
    fault_regime: FaultOntologyClass
    ground_truth_fault_regime: FaultOntologyClass
    metric_family: str
    ground_truth_entity: str
    ground_truth_equivalent_entities: frozenset[str]
    ground_truth_layer: CanonicalEntityLayer
    ground_truth_service: str | None
    ground_truth_workload: str | None
    ground_truth_node: str | None
    initial_entity: str
    initial_layer: CanonicalEntityLayer
    initial_hierarchy_path: EntityHierarchyPath
    initial_supporting_evidence_refs: tuple[str, ...]
    initial_service: str | None
    initial_correct_exact: bool
    initial_correct_service: bool
    initial_pair_correct: bool
    initial_relation: str
    m3_action: str | None
    m3_final_entity: str
    m3_final_layer: CanonicalEntityLayer
    m3_final_service: str | None
    m3_correct_exact: bool
    m3_correct_service: bool
    m3_pair_correct: bool
    m3_relation: str
    metrics_candidates: tuple[UnifiedMetricCandidate, ...]
    metrics_initial_rank: int | None
    metrics_margin: float | None
    metrics_top1_is_downstream: bool
    propagation_disposition: PropagationDisposition
    visibility: EvidenceVisibilitySummary
    causal_visible_entities: frozenset[str]
    alert_entity: str | None
    terminal_failure: bool

    def __post_init__(self) -> None:
        required = (
            self.private_case_key,
            self.fixture,
            self.benchmark,
            self.system,
            self.fault_family,
            self.fault_type_truth,
            self.fault_type_raw,
            self.ground_truth_entity,
            self.initial_entity,
            self.m3_final_entity,
        )
        if any(not item for item in required):
            raise ValueError("unified RCA case contains an empty required field")
        if self.ground_truth_entity not in self.ground_truth_equivalent_entities:
            raise ValueError("unified RCA truth equivalence set omits canonical truth")
        if tuple(item.rank for item in self.metrics_candidates) != tuple(
            range(1, len(self.metrics_candidates) + 1)
        ):
            raise ValueError("unified Metrics candidates are not contiguous Top-6")

    @property
    def metrics_top1(self) -> UnifiedMetricCandidate | None:
        return self.metrics_candidates[0] if self.metrics_candidates else None

    def to_frontier_case(self) -> FrontierCase:
        top1 = self.metrics_top1
        causal = tuple(
            CausalCandidate(
                entity=item.entity,
                service_ancestor=item.service_ancestor,
                layer=item.layer,
                first_anomaly_time=item.first_anomaly_time,
                source_support=item.source_support,
                metrics_rank=item.rank,
                relation_to_symptom=item.relation_to_symptom,  # type: ignore[arg-type]
            )
            for item in self.metrics_candidates
        )
        return FrontierCase(
            private_case_key=self.private_case_key,
            benchmark=self.benchmark,
            system=self.system,
            fault_family=self.fault_family,
            fault_regime=self.fault_regime,
            metric_family=self.metric_family,
            ground_truth_entity=self.ground_truth_entity,
            ground_truth_equivalent_entities=self.ground_truth_equivalent_entities,
            ground_truth_service=self.ground_truth_service,
            initial_entity=self.initial_entity,
            initial_service=self.initial_service,
            initial_fault_type=self.fault_type_raw,
            initial_pair_correct=self.initial_pair_correct,
            initial_layer=self.initial_layer,
            metrics_top1=None if top1 is None else top1.entity,
            metrics_top1_service=None if top1 is None else top1.service_ancestor,
            metrics_top1_layer=(
                CanonicalEntityLayer.UNKNOWN if top1 is None else top1.layer
            ),
            metrics_initial_rank=self.metrics_initial_rank,
            metrics_margin=self.metrics_margin,
            metrics_top1_is_downstream=self.metrics_top1_is_downstream,
            propagation_disposition=self.propagation_disposition,
            causal_candidates=causal,
            initial_fault_correct=(
                fault_phrase_relation(self.fault_type_raw, self.fault_type_truth)
                == "EXACT_NORMALIZED"
            ),
            terminal_failure=self.terminal_failure,
        )

    def private_record(self) -> dict[str, object]:
        return {
            "schema_version": "unified-rca-case.v1",
            "private_case_key": self.private_case_key,
            "fixture": self.fixture,
            "benchmark": self.benchmark,
            "system": self.system,
            "fault_family": self.fault_family,
            "fault_type_truth": self.fault_type_truth,
            "fault_type_raw": self.fault_type_raw,
            "fault_regime": self.fault_regime.value,
            "metric_family": self.metric_family,
            "ground_truth": {
                "entity": self.ground_truth_entity,
                "equivalent_entities": sorted(self.ground_truth_equivalent_entities),
                "layer": self.ground_truth_layer.value,
                "service": self.ground_truth_service,
                "workload": self.ground_truth_workload,
                "node": self.ground_truth_node,
                "fault_regime": self.ground_truth_fault_regime.value,
            },
            "initial": {
                "entity": self.initial_entity,
                "layer": self.initial_layer.value,
                "service": self.initial_service,
                "exact_correct": self.initial_correct_exact,
                "service_correct": self.initial_correct_service,
                "pair_correct": self.initial_pair_correct,
                "relation": self.initial_relation,
                "hierarchy_path": {
                    "entity": self.initial_hierarchy_path.entity,
                    "explicit_parents": list(
                        self.initial_hierarchy_path.explicit_parents
                    ),
                    "service_ancestor": (
                        self.initial_hierarchy_path.service_ancestor_or_none
                    ),
                    "infrastructure_ancestor": (
                        self.initial_hierarchy_path.infrastructure_ancestor_or_none
                    ),
                },
                "supporting_evidence_refs": list(
                    self.initial_supporting_evidence_refs
                ),
            },
            "historical_m3": {
                "action": self.m3_action,
                "entity": self.m3_final_entity,
                "layer": self.m3_final_layer.value,
                "service": self.m3_final_service,
                "exact_correct": self.m3_correct_exact,
                "service_correct": self.m3_correct_service,
                "pair_correct": self.m3_pair_correct,
                "relation": self.m3_relation,
            },
            "metrics_candidates": [
                {
                    "entity": item.entity,
                    "service": item.service_ancestor,
                    "layer": item.layer.value,
                    "rank": item.rank,
                    "score": item.score,
                    "metric_family": item.metric_family,
                    "first_anomaly_time": item.first_anomaly_time,
                    "source_support": item.source_support,
                    "relation_to_symptom": item.relation_to_symptom,
                }
                for item in self.metrics_candidates
            ],
            "metrics_initial_rank": self.metrics_initial_rank,
            "metrics_margin": self.metrics_margin,
            "metrics_top1_is_downstream": self.metrics_top1_is_downstream,
            "propagation_disposition": self.propagation_disposition.value,
            "visibility": {
                "catalog": sorted(self.visibility.catalog_entities),
                "metrics": sorted(self.visibility.metrics_entities),
                "logs": sorted(self.visibility.logs_entities),
                "traces": sorted(self.visibility.traces_entities),
                "events": sorted(self.visibility.events_entities),
                "alerts": sorted(self.visibility.alerts_entities),
                "topology": sorted(self.visibility.topology_entities),
                "causal": sorted(self.causal_visible_entities),
            },
            "alert_entity": self.alert_entity,
            "terminal_failure": self.terminal_failure,
        }


def fault_phrase_relation(prediction: str, truth: str) -> str:
    left = " ".join(prediction.strip().casefold().split())
    right = " ".join(truth.strip().casefold().split())
    if left == right:
        return "EXACT_NORMALIZED"
    compact_left = re.sub(r"[^a-z0-9]+", "", left)
    compact_right = re.sub(r"[^a-z0-9]+", "", right)
    if compact_left and compact_left == compact_right:
        return "CASING_OR_SEPARATOR"
    tokens_left = set(re.findall(r"[a-z0-9]+", left))
    tokens_right = set(re.findall(r"[a-z0-9]+", right))
    if tokens_left.intersection(tokens_right):
        return "TOKEN_OVERLAP"
    return "COMPLETELY_DIFFERENT"


def classify_fault_phrase_relation(case: UnifiedRCACase) -> str:
    """Classify phrase agreement, including ontology-level synonym mismatch."""

    relation = fault_phrase_relation(case.fault_type_raw, case.fault_type_truth)
    if (
        relation == "COMPLETELY_DIFFERENT"
        and case.fault_regime is not FaultOntologyClass.UNKNOWN
        and case.fault_regime is case.ground_truth_fault_regime
    ):
        return "SYNONYM_OR_HIERARCHY_MISMATCH"
    return relation


def evidence_sufficiency(case: UnifiedRCACase) -> str:
    truth = case.ground_truth_entity
    if truth not in case.visibility.catalog_entities:
        return "ENTITY_CATALOG_MISSING"
    source_sets = (
        case.visibility.metrics_entities,
        case.visibility.logs_entities,
        case.visibility.traces_entities,
        case.visibility.events_entities,
        case.visibility.alerts_entities,
    )
    source_count = sum(truth in values for values in source_sets)
    if source_count >= 2:
        return "CROSS_SOURCE_SUFFICIENT"
    if truth in case.visibility.events_entities:
        return "EVENTS_SUFFICIENT"
    if truth in case.visibility.traces_entities:
        return "TRACE_CAUSAL_SUFFICIENT"
    if truth in case.causal_visible_entities:
        return "TOPOLOGY_CAUSAL_SUFFICIENT"
    if any(item.entity == truth for item in case.metrics_candidates):
        return "METRICS_LOCAL_SUFFICIENT"
    if case.initial_relation in {
        "PREDICTED_ANCESTOR",
        "PREDICTED_DESCENDANT",
        "SIBLING_SAME_PARENT",
        "SAME_SERVICE_DIFFERENT_INSTANCE",
    }:
        return "HIERARCHY_SUFFICIENT"
    if truth in case.visibility.any_model_visible:
        return "VISIBLE_BUT_REASONING_FAILED"
    return "ROOT_NOT_VISIBLE"


def classify_strong_single_failure(case: UnifiedRCACase) -> str:
    if case.initial_correct_exact:
        return "CORRECT"
    if case.terminal_failure:
        return "TERMINAL_FAILURE"
    truth = case.ground_truth_entity
    if truth not in case.visibility.catalog_entities:
        return "ROOT_NOT_IN_ENTITY_CATALOG"
    if case.initial_entity not in case.visibility.catalog_entities:
        return "PROMPT_ENTITY_TASK_MISMATCH"
    if truth not in case.visibility.any_model_visible:
        return "ROOT_NOT_IN_MODEL_VISIBLE_CONTEXT"
    if case.initial_relation == "CONNECTED_DOWNSTREAM":
        return "DOWNSTREAM_SYMPTOM_SELECTED"
    if case.initial_relation == "CONNECTED_UPSTREAM":
        return "UPSTREAM_ENTITY_SELECTED"
    if case.initial_relation in {
        "PREDICTED_ANCESTOR",
        "PREDICTED_DESCENDANT",
        "SIBLING_SAME_PARENT",
        "SAME_SERVICE_DIFFERENT_INSTANCE",
        "SAME_NODE",
    }:
        return "ENTITY_LAYER_MISMATCH"
    if (
        case.fault_regime is not FaultOntologyClass.UNKNOWN
        and case.ground_truth_fault_regime is not FaultOntologyClass.UNKNOWN
        and case.fault_regime is not case.ground_truth_fault_regime
    ):
        return "FAULT_REGIME_MISMATCH"
    if case.alert_entity is not None and case.initial_entity == case.alert_entity:
        return "ALERT_TARGET_BIAS"
    if any(item.entity == truth for item in case.metrics_candidates):
        return "MODEL_REASONING_FAILURE_WITH_SUFFICIENT_EVIDENCE"
    if truth not in case.visibility.metrics_entities:
        return "ROOT_VISIBLE_BUT_NOT_METRICS_TOPK"
    return "UNRESOLVED"


def classify_m3_failure(case: UnifiedRCACase) -> str:
    if case.m3_action != "OVERRIDE_METRICS_TOP1":
        return "NO_OVERRIDE"
    if case.metrics_top1 is None or case.m3_final_entity != case.metrics_top1.entity:
        return "RANKING_PROJECTION_ERROR"
    if case.m3_correct_exact:
        return "CORRECT_LOCAL_ANOMALY_ROOT"
    if case.m3_relation in {
        "PREDICTED_ANCESTOR",
        "PREDICTED_DESCENDANT",
        "SIBLING_SAME_PARENT",
        "SAME_SERVICE_DIFFERENT_INSTANCE",
        "SAME_NODE",
    }:
        return "WRONG_LAYER_OVERRIDE"
    if case.m3_relation == "CONNECTED_DOWNSTREAM":
        return "DOWNSTREAM_SYMPTOM_OVERRIDE"
    if case.alert_entity is not None and case.m3_final_entity == case.alert_entity:
        return "ALERT_TARGET_OVERRIDE"
    if not any(
        item.entity == case.ground_truth_entity for item in case.metrics_candidates
    ):
        return "ROOT_NOT_IN_METRICS_CANDIDATES"
    if case.metrics_margin is not None and case.metrics_margin >= 0.25:
        return "HIGH_MARGIN_NON_CAUSAL_ANOMALY"
    return "UNRESOLVED"


def rate(numerator: int, denominator: int) -> dict[str, int | float]:
    if denominator < 0 or numerator < 0 or numerator > denominator:
        raise ValueError("rate numerator/denominator is invalid")
    return {
        "numerator": numerator,
        "denominator": denominator,
        "value": 0.0 if denominator == 0 else numerator / denominator,
    }


__all__ = [
    "UnifiedMetricCandidate",
    "UnifiedRCACase",
    "classify_fault_phrase_relation",
    "classify_m3_failure",
    "classify_strong_single_failure",
    "evidence_sufficiency",
    "fault_phrase_relation",
    "rate",
]
