"""Typed, Provider-free contracts for unified RCA attribution and replay."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import math
from typing import Literal


class CanonicalEntityLayer(str, Enum):
    OPERATION = "OPERATION"
    SERVICE = "SERVICE"
    WORKLOAD = "WORKLOAD"
    POD = "POD"
    CONTAINER = "CONTAINER"
    NODE = "NODE"
    DATABASE = "DATABASE"
    CACHE = "CACHE"
    MESSAGE_QUEUE = "MESSAGE_QUEUE"
    NETWORK_COMPONENT = "NETWORK_COMPONENT"
    CLUSTER = "CLUSTER"
    INFRASTRUCTURE = "INFRASTRUCTURE"
    UNKNOWN = "UNKNOWN"


class FaultOntologyClass(str, Enum):
    LOCAL_RESOURCE = "LOCAL_RESOURCE"
    PROPAGATION = "PROPAGATION"
    NETWORK = "NETWORK"
    DEPENDENCY = "DEPENDENCY"
    APPLICATION = "APPLICATION"
    UNKNOWN = "UNKNOWN"


class PropagationDisposition(str, Enum):
    PRESENT = "PRESENT"
    ABSENT = "ABSENT"
    UNAVAILABLE = "UNAVAILABLE"


class RootProvenance(str, Enum):
    MODEL_INITIAL = "MODEL_INITIAL"
    DETERMINISTIC_METRICS_M3 = "DETERMINISTIC_METRICS_M3"
    HIERARCHY_GUARDED_METRICS = "HIERARCHY_GUARDED_METRICS"
    DETERMINISTIC_CAUSAL_RANKING = "DETERMINISTIC_CAUSAL_RANKING"


class ArchitectureOption(str, Enum):
    A0 = "A0"
    A1 = "A1"
    A2 = "A2"
    A3 = "A3"
    A4 = "A4"
    A5 = "A5"


@dataclass(frozen=True, slots=True)
class CausalCandidate:
    entity: str
    service_ancestor: str | None
    layer: CanonicalEntityLayer
    first_anomaly_time: float | None
    source_support: int
    metrics_rank: int
    relation_to_symptom: Literal["ROOT", "UPSTREAM", "DOWNSTREAM", "LATERAL", "UNKNOWN"]

    def __post_init__(self) -> None:
        if not self.entity:
            raise ValueError("causal candidate requires an entity")
        if self.source_support < 0:
            raise ValueError("causal candidate source support cannot be negative")
        if not 1 <= self.metrics_rank <= 6:
            raise ValueError("causal candidate Metrics rank must be in Top-6")
        if self.first_anomaly_time is not None and not math.isfinite(
            self.first_anomaly_time
        ):
            raise ValueError("causal candidate anomaly time must be finite")


@dataclass(frozen=True, slots=True)
class FrontierCase:
    private_case_key: str
    benchmark: str
    system: str
    fault_family: str
    fault_regime: FaultOntologyClass
    metric_family: str
    ground_truth_entity: str
    ground_truth_equivalent_entities: frozenset[str]
    ground_truth_service: str | None
    initial_entity: str
    initial_service: str | None
    initial_fault_type: str
    initial_pair_correct: bool
    initial_layer: CanonicalEntityLayer
    metrics_top1: str | None
    metrics_top1_service: str | None
    metrics_top1_layer: CanonicalEntityLayer
    metrics_initial_rank: int | None
    metrics_margin: float | None
    metrics_top1_is_downstream: bool
    propagation_disposition: PropagationDisposition
    causal_candidates: tuple[CausalCandidate, ...]
    initial_fault_correct: bool = False
    terminal_failure: bool = False

    def __post_init__(self) -> None:
        required = (
            self.private_case_key,
            self.benchmark,
            self.system,
            self.fault_family,
            self.metric_family,
            self.ground_truth_entity,
            self.initial_entity,
            self.initial_fault_type,
        )
        if any(not value for value in required):
            raise ValueError("frontier case contains an empty required field")
        if self.ground_truth_entity not in self.ground_truth_equivalent_entities:
            raise ValueError("frontier case truth equivalence set omits canonical truth")
        if self.metrics_initial_rank is not None and not 1 <= self.metrics_initial_rank <= 6:
            raise ValueError("Metrics Initial rank must be in the frozen Top-6")
        if self.metrics_margin is not None and not math.isfinite(self.metrics_margin):
            raise ValueError("Metrics margin must be finite")


@dataclass(frozen=True, slots=True)
class FrontierOutcome:
    private_case_key: str
    option: ArchitectureOption
    initial_entity: str
    final_entity: str
    fault_type: str
    root_provenance: RootProvenance
    decision_reason: str
    override: bool
    initial_exact_correct: bool
    final_exact_correct: bool
    initial_service_correct: bool
    final_service_correct: bool
    initial_pair_correct: bool
    final_pair_correct: bool

    @property
    def root_rescue(self) -> bool:
        return not self.initial_exact_correct and self.final_exact_correct

    @property
    def root_damage(self) -> bool:
        return self.initial_exact_correct and not self.final_exact_correct

    @property
    def root_net_rescue(self) -> int:
        return int(self.root_rescue) - int(self.root_damage)


@dataclass(frozen=True, slots=True)
class RobustnessFold:
    option: ArchitectureOption
    axis: Literal[
        "LEAVE_ONE_FAULT_FAMILY_OUT",
        "LEAVE_ONE_ENTITY_LAYER_OUT",
        "LEAVE_ONE_SYSTEM_OUT",
    ]
    held_out_group: str
    denominator: int
    rescue: int
    damage: int

    @property
    def net_rescue(self) -> int:
        return self.rescue - self.damage


@dataclass(frozen=True, slots=True)
class EvidenceVisibilitySummary:
    catalog_entities: frozenset[str]
    metrics_entities: frozenset[str]
    logs_entities: frozenset[str]
    traces_entities: frozenset[str]
    events_entities: frozenset[str]
    alerts_entities: frozenset[str]
    topology_entities: frozenset[str]

    @property
    def any_model_visible(self) -> frozenset[str]:
        return self.metrics_entities | self.logs_entities | self.traces_entities

    @property
    def any_source_visible(self) -> frozenset[str]:
        return (
            self.any_model_visible
            | self.events_entities
            | self.alerts_entities
            | self.topology_entities
        )


@dataclass(frozen=True, slots=True)
class EntityHierarchyPath:
    entity: str
    explicit_parents: tuple[str, ...]
    service_ancestor_or_none: str | None
    infrastructure_ancestor_or_none: str | None
