"""Deterministic, label-blind input projection for live hierarchical RCA."""

from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import Mapping
import re
from typing import Literal

from pydantic import Field, model_validator

from ecomsre_rcaeval_v2.contracts import V2Model
from ecomsre_rca_unified.contracts import CanonicalEntityLayer, FaultOntologyClass


EvidenceSource = Literal["METRICS", "LOGS", "TRACES", "EVENTS", "ALERTS"]
SourceStatus = Literal["AVAILABLE", "SOURCE_UNAVAILABLE"]
RelationType = Literal[
    "DIRECTED_TOPOLOGY",
    "TRACE_PARENT_CHILD",
    "EXPLICIT_DEPENDENCY",
    "FIRST_OBSERVED_BEFORE",
    "UNDIRECTED",
    "UNKNOWN",
]

ROOT_ELIGIBLE_LAYERS = frozenset(
    {
        CanonicalEntityLayer.SERVICE,
        CanonicalEntityLayer.WORKLOAD,
        CanonicalEntityLayer.NODE,
        CanonicalEntityLayer.DATABASE,
        CanonicalEntityLayer.CACHE,
        CanonicalEntityLayer.MESSAGE_QUEUE,
        CanonicalEntityLayer.NETWORK_COMPONENT,
        CanonicalEntityLayer.CLUSTER,
        CanonicalEntityLayer.INFRASTRUCTURE,
    }
)
_LAYER_PRIORITY = {
    layer: index
    for index, layer in enumerate(
        (
            CanonicalEntityLayer.SERVICE,
            CanonicalEntityLayer.WORKLOAD,
            CanonicalEntityLayer.NODE,
            CanonicalEntityLayer.DATABASE,
            CanonicalEntityLayer.CACHE,
            CanonicalEntityLayer.MESSAGE_QUEUE,
            CanonicalEntityLayer.NETWORK_COMPONENT,
            CanonicalEntityLayer.CLUSTER,
            CanonicalEntityLayer.INFRASTRUCTURE,
            CanonicalEntityLayer.OPERATION,
            CanonicalEntityLayer.POD,
            CanonicalEntityLayer.CONTAINER,
            CanonicalEntityLayer.UNKNOWN,
        )
    )
}
_SOURCE_ORDER = {name: index for index, name in enumerate(("METRICS", "LOGS", "TRACES", "EVENTS", "ALERTS"))}
_FAULT_MARKERS: tuple[tuple[FaultOntologyClass, tuple[str, ...]], ...] = (
    (
        FaultOntologyClass.LOCAL_RESOURCE,
        ("cpu", "memory", "mem", "disk", "io", "resource", "load"),
    ),
    (
        FaultOntologyClass.NETWORK,
        ("network", "socket", "packet", "loss", "connection"),
    ),
    (
        FaultOntologyClass.DEPENDENCY,
        ("dependency", "downstream", "database", "cache", "queue", "timeout"),
    ),
    (
        FaultOntologyClass.APPLICATION,
        ("error", "exception", "incorrect", "bug"),
    ),
)


def classify_live_fault_ontology(value: str) -> FaultOntologyClass:
    """Classify only the model-visible fault phrase, without evaluator truth."""

    text = value.casefold()
    tokens = set(re.findall(r"[a-z0-9]+", text))
    for ontology, markers in _FAULT_MARKERS:
        if any(marker in tokens if len(marker) <= 3 else marker in text for marker in markers):
            return ontology
    if any(marker in text for marker in ("propagat", "upstream", "symptom")):
        return FaultOntologyClass.PROPAGATION
    return FaultOntologyClass.UNKNOWN


class LiveEntity(V2Model):
    entity_ref: str = Field(min_length=5, max_length=768)
    entity_name: str = Field(min_length=1, max_length=512)
    layer: CanonicalEntityLayer
    service_ancestor_or_none: str | None = Field(default=None, max_length=768)
    parent_ref_or_none: str | None = Field(default=None, max_length=768)


class EvidenceItem(V2Model):
    evidence_ref: str = Field(pattern=r"^(metric|log|trace):[0-9]{4}$")
    source: Literal["METRICS", "LOGS", "TRACES"]
    entity_ref: str = Field(min_length=5, max_length=768)
    name: str = Field(min_length=1, max_length=512)
    started_at: float
    ended_at: float
    score: float
    summary: str = Field(min_length=1, max_length=2_000)


class LiveBaseContext(V2Model):
    schema_version: Literal["strong-single-live.base-context.v1"] = (
        "strong-single-live.base-context.v1"
    )
    alert_title: str = Field(min_length=1, max_length=1_000)
    prompt_text: str = Field(min_length=1, max_length=4_000)
    alert_entity_ref: str | None = Field(default=None, max_length=768)
    entities: tuple[LiveEntity, ...] = Field(min_length=1, max_length=256)
    evidence: tuple[EvidenceItem, ...] = Field(max_length=18)
    source_status: dict[Literal["METRICS", "LOGS", "TRACES"], SourceStatus]

    @model_validator(mode="after")
    def require_referential_integrity(self) -> LiveBaseContext:
        refs = tuple(item.entity_ref for item in self.entities)
        if len(refs) != len(set(refs)):
            raise ValueError("base context contains duplicate entity refs")
        evidence_refs = tuple(item.evidence_ref for item in self.evidence)
        if len(evidence_refs) != len(set(evidence_refs)):
            raise ValueError("base context contains duplicate evidence refs")
        if not {item.entity_ref for item in self.evidence}.issubset(refs):
            raise ValueError("base context evidence references an invisible entity")
        if self.alert_entity_ref is not None and self.alert_entity_ref not in refs:
            raise ValueError("base context alert entity is invisible")
        if set(self.source_status) != {"METRICS", "LOGS", "TRACES"}:
            raise ValueError("base context source statuses are incomplete")
        return self


class RelationSource(V2Model):
    source_entity_ref: str = Field(min_length=5, max_length=768)
    target_entity_ref: str = Field(min_length=5, max_length=768)
    relation_type: RelationType


class HierarchySource(V2Model):
    entities: tuple[LiveEntity, ...] = Field(min_length=1)
    parent_edges: tuple[tuple[str, str], ...]
    topology_edges: tuple[RelationSource, ...]
    propagation_edges: tuple[RelationSource, ...]
    source_visibility: dict[str, frozenset[EvidenceSource]]
    first_anomaly_source: dict[str, EvidenceSource]

    @model_validator(mode="after")
    def require_graph_integrity(self) -> HierarchySource:
        refs = {item.entity_ref for item in self.entities}
        if len(refs) != len(self.entities):
            raise ValueError("hierarchy source contains duplicate entities")
        linked = {ref for edge in self.parent_edges for ref in edge} | {
            ref
            for edge in (*self.topology_edges, *self.propagation_edges)
            for ref in (edge.source_entity_ref, edge.target_entity_ref)
        }
        if not linked.issubset(refs):
            raise ValueError("hierarchy source edge references an unknown entity")
        if not set(self.source_visibility).issubset(refs):
            raise ValueError("hierarchy visibility references an unknown entity")
        if not set(self.first_anomaly_source).issubset(refs):
            raise ValueError("hierarchy anomaly source references an unknown entity")
        if any(
            edge.relation_type
            not in {"DIRECTED_TOPOLOGY", "EXPLICIT_DEPENDENCY", "UNDIRECTED", "UNKNOWN"}
            for edge in self.topology_edges
        ):
            raise ValueError("hierarchy topology graph contains a propagation-only edge")
        lineage_refs = {
            ref
            for item in self.entities
            for ref in (item.parent_ref_or_none, item.service_ancestor_or_none)
            if ref is not None
        }
        if not lineage_refs.issubset(refs):
            raise ValueError("hierarchy entity lineage references an unknown entity")
        return self


class EntityCard(V2Model):
    entity_ref: str
    layer: CanonicalEntityLayer
    service_ancestor_or_none: str | None
    parent_ref_or_none: str | None
    relation_to_alert: Literal[
        "ALERT_ENTITY", "UPSTREAM", "DOWNSTREAM", "NEIGHBOR", "UNKNOWN"
    ]
    topology_distance_or_none: int | None = Field(default=None, ge=0)
    visible_sources: tuple[EvidenceSource, ...]
    first_anomaly_source_or_none: EvidenceSource | None


class PropagationRelation(V2Model):
    source_entity_ref: str
    target_entity_ref: str
    relation_type: RelationType


class HierarchicalContext(V2Model):
    schema_version: Literal["strong-single-live.hierarchical-context.v1"] = (
        "strong-single-live.hierarchical-context.v1"
    )
    entity_cards: tuple[EntityCard, ...] = Field(min_length=1, max_length=64)
    root_eligible_entity_refs: tuple[str, ...] = Field(min_length=1, max_length=64)
    propagation_relations: tuple[PropagationRelation, ...] = Field(max_length=12)
    included_candidate_count: int = Field(ge=1)
    dropped_included_candidate_count: int = Field(ge=0)


def _distances(
    seeds: set[str], adjacency: Mapping[str, set[str]]
) -> dict[str, int]:
    output = {item: 0 for item in seeds}
    queue = deque(sorted(seeds))
    while queue:
        current = queue.popleft()
        for neighbor in sorted(adjacency.get(current, set())):
            if neighbor not in output:
                output[neighbor] = output[current] + 1
                queue.append(neighbor)
    return output


def _reachable(start: str, target: str, outgoing: Mapping[str, set[str]]) -> bool:
    if start == target:
        return True
    seen = {start}
    queue = deque([start])
    while queue:
        current = queue.popleft()
        for neighbor in outgoing.get(current, set()):
            if neighbor == target:
                return True
            if neighbor not in seen:
                seen.add(neighbor)
                queue.append(neighbor)
    return False


def build_hierarchical_context(
    base: LiveBaseContext, source: HierarchySource
) -> HierarchicalContext:
    """Build the frozen H1-only entity index without labels or correctness."""

    entities = {item.entity_ref: item for item in source.entities}
    if not {item.entity_ref for item in base.entities}.issubset(entities):
        raise ValueError("base entities are absent from hierarchy source")
    parents: dict[str, set[str]] = defaultdict(set)
    for child, ancestor in source.parent_edges:
        parents[child].add(ancestor)
    for entity in source.entities:
        if entity.parent_ref_or_none is not None:
            parents[entity.entity_ref].add(entity.parent_ref_or_none)
    adjacency: dict[str, set[str]] = {ref: set() for ref in entities}
    outgoing: dict[str, set[str]] = {ref: set() for ref in entities}
    for child, ancestors in parents.items():
        for ancestor in ancestors:
            adjacency[child].add(ancestor)
            adjacency[ancestor].add(child)
    for edge in source.topology_edges:
        adjacency[edge.source_entity_ref].add(edge.target_entity_ref)
        adjacency[edge.target_entity_ref].add(edge.source_entity_ref)
        if edge.relation_type not in {"UNDIRECTED", "UNKNOWN"}:
            outgoing[edge.source_entity_ref].add(edge.target_entity_ref)

    evidence_sources: dict[str, set[EvidenceSource]] = {
        ref: set(values) for ref, values in source.source_visibility.items()
    }
    direct_evidence: set[str] = set()
    for item in base.evidence:
        direct_evidence.add(item.entity_ref)
        evidence_sources.setdefault(item.entity_ref, set()).add(item.source)
    seeds = set(direct_evidence)
    if base.alert_entity_ref is not None:
        seeds.add(base.alert_entity_ref)
        evidence_sources.setdefault(base.alert_entity_ref, set()).add("ALERTS")
    if not seeds:
        seeds.add(sorted(entities)[0])

    included = set(seeds)

    def include_lineage(entity_ref: str) -> None:
        service = entities[entity_ref].service_ancestor_or_none
        if service is not None:
            included.add(service)
        seen = {entity_ref}
        queue = deque([entity_ref])
        while queue:
            current = queue.popleft()
            for ancestor in sorted(parents.get(current, set())):
                included.add(ancestor)
                if ancestor not in seen:
                    seen.add(ancestor)
                    queue.append(ancestor)

    for entity_ref in tuple(seeds):
        include_lineage(entity_ref)

    seed_distances = _distances(seeds, adjacency)
    included.update(
        ref
        for ref, distance in seed_distances.items()
        if distance <= 2 and entities[ref].layer in ROOT_ELIGIBLE_LAYERS
    )
    included.update(
        ref
        for ref, sources in evidence_sources.items()
        if sources and entities[ref].layer in ROOT_ELIGIBLE_LAYERS
    )
    for entity_ref in tuple(included):
        include_lineage(entity_ref)
    alert_distances = (
        {}
        if base.alert_entity_ref is None
        else _distances({base.alert_entity_ref}, adjacency)
    )

    def order(ref: str) -> tuple[object, ...]:
        sources = evidence_sources.get(ref, set())
        distance = alert_distances.get(ref)
        return (
            -int(ref in direct_evidence),
            -len(sources),
            distance if distance is not None else 1_000_000,
            _LAYER_PRIORITY[entities[ref].layer],
            ref,
        )

    selected = tuple(sorted(included, key=order)[:64])
    selected_set = set(selected)
    if not any(entities[ref].layer in ROOT_ELIGIBLE_LAYERS for ref in selected):
        raise ValueError("frozen hierarchy ordering selected no root-eligible entity")
    dangling_lineage = {
        reference
        for ref in selected
        for reference in (
            entities[ref].parent_ref_or_none,
            entities[ref].service_ancestor_or_none,
        )
        if reference is not None and reference not in selected_set
    }
    if dangling_lineage:
        raise ValueError("frozen hierarchy cap would create dangling lineage")

    def relation_to_alert(ref: str) -> str:
        alert = base.alert_entity_ref
        if alert is None:
            return "UNKNOWN"
        if ref == alert:
            return "ALERT_ENTITY"
        if _reachable(ref, alert, outgoing):
            return "UPSTREAM"
        if _reachable(alert, ref, outgoing):
            return "DOWNSTREAM"
        if ref in alert_distances:
            return "NEIGHBOR"
        return "UNKNOWN"

    cards = tuple(
        EntityCard(
            entity_ref=ref,
            layer=entities[ref].layer,
            service_ancestor_or_none=entities[ref].service_ancestor_or_none,
            parent_ref_or_none=entities[ref].parent_ref_or_none,
            relation_to_alert=relation_to_alert(ref),  # type: ignore[arg-type]
            topology_distance_or_none=alert_distances.get(ref),
            visible_sources=tuple(
                sorted(evidence_sources.get(ref, set()), key=_SOURCE_ORDER.__getitem__)
            ),
            first_anomaly_source_or_none=source.first_anomaly_source.get(ref),
        )
        for ref in selected
    )
    relations = tuple(
        PropagationRelation(
            source_entity_ref=edge.source_entity_ref,
            target_entity_ref=edge.target_entity_ref,
            relation_type=edge.relation_type,
        )
        for edge in sorted(
            (*source.topology_edges, *source.propagation_edges),
            key=lambda item: (
                item.relation_type,
                item.source_entity_ref,
                item.target_entity_ref,
            ),
        )
        if edge.source_entity_ref in selected_set
        and edge.target_entity_ref in selected_set
    )[:12]
    return HierarchicalContext(
        entity_cards=cards,
        root_eligible_entity_refs=tuple(
            ref for ref in selected if entities[ref].layer in ROOT_ELIGIBLE_LAYERS
        ),
        propagation_relations=relations,
        included_candidate_count=len(included),
        dropped_included_candidate_count=len(included - selected_set),
    )


__all__ = [
    "EvidenceItem",
    "EvidenceSource",
    "EntityCard",
    "HierarchicalContext",
    "HierarchySource",
    "LiveBaseContext",
    "LiveEntity",
    "PropagationRelation",
    "ROOT_ELIGIBLE_LAYERS",
    "RelationSource",
    "SourceStatus",
    "build_hierarchical_context",
    "classify_live_fault_ontology",
]
