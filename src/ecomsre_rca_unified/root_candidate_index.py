"""Frozen Compact Root Candidate Index v1 over one canonical projection."""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field
import math
from typing import Literal, Mapping, cast

from ecomsre_rca_unified.contracts import CanonicalEntityLayer
from ecomsre_rca_unified.root_evidence_projection import (
    EvidenceSource,
    ProjectionCase,
    ROOT_ELIGIBLE_LAYERS,
    SOURCE_ORDER,
)


Family = Literal["S", "N", "D"]
ReasonCode = Literal["D", "A", "U", "F", "K", "R"]
RelationCode = Literal["SELF", "ANC", "UP", "DOWN", "COMP", "NONE"]

FAMILY_CAPS: Mapping[Family, int] = {"S": 8, "N": 2, "D": 2}
REASON_ORDER: tuple[ReasonCode, ...] = ("D", "A", "U", "F", "K", "R")


def layer_family(layer: CanonicalEntityLayer) -> Family:
    if layer in {CanonicalEntityLayer.SERVICE, CanonicalEntityLayer.WORKLOAD}:
        return "S"
    if layer in {
        CanonicalEntityLayer.NODE,
        CanonicalEntityLayer.CLUSTER,
        CanonicalEntityLayer.INFRASTRUCTURE,
    }:
        return "N"
    if layer in {
        CanonicalEntityLayer.DATABASE,
        CanonicalEntityLayer.CACHE,
        CanonicalEntityLayer.MESSAGE_QUEUE,
        CanonicalEntityLayer.NETWORK_COMPONENT,
    }:
        return "D"
    raise ValueError("candidate layer is not root eligible")


@dataclass(slots=True)
class _Signals:
    entity_ref: str
    direct_sources: set[EvidenceSource] = field(default_factory=set)
    inherited_sources: set[EvidenceSource] = field(default_factory=set)
    evidence_refs: set[str] = field(default_factory=set)
    reasons: set[ReasonCode] = field(default_factory=set)
    mandatory_reasons: set[str] = field(default_factory=set)
    first_anomaly_time: float | None = None
    topology_distance: int | None = None
    metrics_rank: int | None = None
    explicit_upstream: bool = False


@dataclass(frozen=True, slots=True)
class CandidateUniverseEntry:
    entity_ref: str
    display_name: str
    layer: CanonicalEntityLayer
    family: Family
    mandatory: bool
    mandatory_reasons: tuple[str, ...]
    direct_sources: tuple[EvidenceSource, ...]
    inherited_sources: tuple[EvidenceSource, ...]
    evidence_refs: tuple[str, ...]
    reasons: tuple[ReasonCode, ...]
    explicit_upstream: bool
    first_anomaly_time: float | None
    topology_distance: int | None
    metrics_rank: int | None
    relation_to_alert: RelationCode

    @property
    def all_sources(self) -> frozenset[EvidenceSource]:
        return frozenset((*self.direct_sources, *self.inherited_sources))

    def payload(self) -> dict[str, object]:
        return {
            "entity_ref": self.entity_ref,
            "display_name": self.display_name,
            "layer": self.layer.value,
            "family": self.family,
            "mandatory": self.mandatory,
            "mandatory_reasons": list(self.mandatory_reasons),
            "direct_sources": list(self.direct_sources),
            "inherited_sources": list(self.inherited_sources),
            "evidence_refs": list(self.evidence_refs),
            "reasons": list(self.reasons),
            "explicit_upstream": self.explicit_upstream,
            "first_anomaly_time": self.first_anomaly_time,
            "topology_distance": self.topology_distance,
            "metrics_rank": self.metrics_rank,
            "relation_to_alert": self.relation_to_alert,
        }


@dataclass(frozen=True, slots=True)
class CandidateIndexEntry:
    candidate_id: str
    universe: CandidateUniverseEntry

    def payload(self) -> dict[str, object]:
        return {
            "candidate_id": self.candidate_id,
            **self.universe.payload(),
        }


@dataclass(frozen=True, slots=True)
class CandidateIndex:
    candidates: tuple[CandidateIndexEntry, ...]
    universe: tuple[CandidateUniverseEntry, ...]

    def __post_init__(self) -> None:
        if not self.candidates or len(self.candidates) > 12:
            raise ValueError("candidate index size is invalid")
        expected = tuple(
            f"C{index:02d}" for index in range(1, len(self.candidates) + 1)
        )
        observed = tuple(item.candidate_id for item in self.candidates)
        if observed != expected:
            raise ValueError("candidate IDs are not stable and contiguous")
        refs = tuple(item.universe.entity_ref for item in self.candidates)
        if len(refs) != len(set(refs)):
            raise ValueError("candidate index contains duplicate canonical entities")

    @property
    def mapping(self) -> dict[str, str]:
        return {item.candidate_id: item.universe.entity_ref for item in self.candidates}

    def payload(self) -> dict[str, object]:
        return {
            "schema_version": "compact-root-candidate-index.case.v1",
            "candidates": [item.payload() for item in self.candidates],
        }


def _distances(
    seeds: set[str], adjacency: Mapping[str, set[str]], *, maximum: int
) -> dict[str, int]:
    output = {seed: 0 for seed in seeds}
    queue = deque(sorted(seeds))
    while queue:
        current = queue.popleft()
        if output[current] >= maximum:
            continue
        for neighbor in sorted(adjacency.get(current, set())):
            if neighbor not in output:
                output[neighbor] = output[current] + 1
                queue.append(neighbor)
    return output


def _ancestor_paths(
    entity_ref: str, parents: Mapping[str, set[str]], *, maximum: int = 4
) -> dict[str, tuple[str, ...]]:
    output: dict[str, tuple[str, ...]] = {}
    queue: deque[tuple[str, tuple[str, ...]]] = deque(
        (parent, (entity_ref, parent))
        for parent in sorted(parents.get(entity_ref, set()))
    )
    while queue:
        current, path = queue.popleft()
        prior = output.get(current)
        if prior is not None and len(prior) <= len(path):
            continue
        output[current] = path
        if len(path) - 1 < maximum:
            for parent in sorted(parents.get(current, set())):
                if parent not in path:
                    queue.append((parent, (*path, parent)))
    return output


def build_candidate_index(projection: ProjectionCase) -> CandidateIndex:
    """Apply the one frozen universe, mandatory, ordering, and 8/2/2 policy."""

    entities = {item.entity_ref: item for item in projection.entities}
    parents: dict[str, set[str]] = defaultdict(set)
    adjacency: dict[str, set[str]] = defaultdict(set)
    incoming: dict[str, set[str]] = defaultdict(set)
    outgoing: dict[str, set[str]] = defaultdict(set)
    for child, parent in projection.parent_edges:
        parents[child].add(parent)
        adjacency[child].add(parent)
        adjacency[parent].add(child)
    for upstream, downstream in projection.directed_edges:
        outgoing[upstream].add(downstream)
        incoming[downstream].add(upstream)
        adjacency[upstream].add(downstream)
        adjacency[downstream].add(upstream)
    for left, right in projection.undirected_edges:
        adjacency[left].add(right)
        adjacency[right].add(left)

    evidence_entities = {item.entity_ref for item in projection.observations}
    alert_entities = set(projection.alert_entities)
    seeds = evidence_entities | alert_entities
    component_distances = _distances(seeds, adjacency, maximum=2) if seeds else {}
    states: dict[str, _Signals] = {}

    def state(ref: str) -> _Signals:
        if ref not in entities or entities[ref].layer not in ROOT_ELIGIBLE_LAYERS:
            raise ValueError("candidate universe received a non-root entity")
        return states.setdefault(ref, _Signals(entity_ref=ref))

    def absorb_observation(
        candidate_ref: str, original_ref: str, *, inherited: bool
    ) -> None:
        target = state(candidate_ref)
        for raw in projection.observations:
            if raw.entity_ref != original_ref:
                continue
            (target.inherited_sources if inherited else target.direct_sources).add(
                raw.source
            )
            target.evidence_refs.update(raw.evidence_refs)
            if raw.first_anomaly_time is not None:
                target.first_anomaly_time = (
                    raw.first_anomaly_time
                    if target.first_anomaly_time is None
                    else min(target.first_anomaly_time, raw.first_anomaly_time)
                )
        target.reasons.add("A" if inherited else "D")

    # Direct and all inherited root-eligible entities.
    for ref in sorted(evidence_entities):
        if entities[ref].layer in ROOT_ELIGIBLE_LAYERS:
            absorb_observation(ref, ref, inherited=False)
        for ancestor in _ancestor_paths(ref, parents):
            if entities[ancestor].layer in ROOT_ELIGIBLE_LAYERS:
                absorb_observation(ancestor, ref, inherited=True)

    # Explicit upstream dependencies at at most two directed hops.
    for seed in sorted(seeds):
        distances = _distances({seed}, incoming, maximum=2)
        for ref, distance in distances.items():
            if distance == 0 or entities[ref].layer not in ROOT_ELIGIBLE_LAYERS:
                continue
            target = state(ref)
            target.reasons.add("U")
            target.explicit_upstream = True
            target.topology_distance = (
                distance
                if target.topology_distance is None
                else min(target.topology_distance, distance)
            )

    # Same-component root entities at undirected distance at most two.
    for ref, distance in component_distances.items():
        if entities[ref].layer not in ROOT_ELIGIBLE_LAYERS:
            continue
        target = state(ref)
        target.topology_distance = (
            distance
            if target.topology_distance is None
            else min(target.topology_distance, distance)
        )

    # Metrics Top-6 plus all root ancestors.
    for rank, ref in enumerate(projection.metrics_ranking, start=1):
        metric_roots: list[tuple[str, int]] = []
        if entities[ref].layer in ROOT_ELIGIBLE_LAYERS:
            metric_roots.append((ref, 0))
        metric_roots.extend(
            (ancestor, len(path) - 1)
            for ancestor, path in _ancestor_paths(ref, parents).items()
            if entities[ancestor].layer in ROOT_ELIGIBLE_LAYERS
        )
        for candidate_ref, distance in metric_roots:
            target = state(candidate_ref)
            target.reasons.add("K")
            target.metrics_rank = (
                rank if target.metrics_rank is None else min(target.metrics_rank, rank)
            )
            target.topology_distance = (
                distance
                if target.topology_distance is None
                else min(target.topology_distance, distance)
            )

    # Alert entity plus all root ancestors.
    for ref in sorted(alert_entities):
        alert_roots: list[tuple[str, int]] = []
        if entities[ref].layer in ROOT_ELIGIBLE_LAYERS:
            alert_roots.append((ref, 0))
        alert_roots.extend(
            (ancestor, len(path) - 1)
            for ancestor, path in _ancestor_paths(ref, parents).items()
            if entities[ancestor].layer in ROOT_ELIGIBLE_LAYERS
        )
        for candidate_ref, distance in alert_roots:
            target = state(candidate_ref)
            target.reasons.add("R")
            target.topology_distance = (
                distance
                if target.topology_distance is None
                else min(target.topology_distance, distance)
            )

    if not states:
        raise ValueError("frozen projection produced no real root candidate")

    def nearest_root(ref: str) -> str | None:
        if entities[ref].layer in ROOT_ELIGIBLE_LAYERS:
            return ref
        roots = [
            (ancestor, len(path) - 1)
            for ancestor, path in _ancestor_paths(ref, parents).items()
            if entities[ancestor].layer in ROOT_ELIGIBLE_LAYERS
        ]
        return min(roots, key=lambda item: (item[1], item[0]))[0] if roots else None

    # Mandatory 1: alert or nearest root ancestor.
    for ref in sorted(alert_entities):
        if (candidate := nearest_root(ref)) is not None and candidate in states:
            states[candidate].mandatory_reasons.add("ALERT_NEAREST_ROOT")

    # Mandatory 2: highest explicit SERVICE/WORKLOAD ancestor for every evidence entity.
    for ref in sorted(evidence_entities):
        choices: list[tuple[int, int, str]] = []
        if entities[ref].layer in {
            CanonicalEntityLayer.SERVICE,
            CanonicalEntityLayer.WORKLOAD,
        }:
            choices.append(
                (0, int(entities[ref].layer is CanonicalEntityLayer.WORKLOAD), ref)
            )
        for ancestor, path in _ancestor_paths(ref, parents).items():
            layer = entities[ancestor].layer
            if layer in {CanonicalEntityLayer.SERVICE, CanonicalEntityLayer.WORKLOAD}:
                choices.append(
                    (
                        len(path) - 1,
                        int(layer is CanonicalEntityLayer.WORKLOAD),
                        ancestor,
                    )
                )
        if choices:
            highest = sorted(choices, key=lambda item: (-item[0], item[1], item[2]))[0][
                2
            ]
            if highest in states:
                states[highest].mandatory_reasons.add("SOURCE_HIGHEST_SERVICE_WORKLOAD")

    # Mandatory 3: Metrics Top1 root or nearest root ancestor.
    if projection.metrics_ranking:
        top1 = nearest_root(projection.metrics_ranking[0])
        if top1 is not None and top1 in states:
            states[top1].mandatory_reasons.add("METRICS_TOP1_ROOT")

    # Mandatory 4: exact earliest-anomaly root or nearest root ancestor.
    anomaly_observations = [
        item for item in projection.observations if item.first_anomaly_time is not None
    ]
    if anomaly_observations:
        earliest = min(
            anomaly_observations,
            key=lambda item: (
                cast(float, item.first_anomaly_time),
                item.entity_ref,
                item.source,
            ),
        )
        root = nearest_root(earliest.entity_ref)
        if root is not None and root in states:
            states[root].mandatory_reasons.add("EARLIEST_ANOMALY_ROOT")
            states[root].reasons.add("F")

    # Generic exact-service completeness; no dataset or benchmark identity branch.
    source_services = {
        ref
        for ref in evidence_entities
        if entities[ref].layer is CanonicalEntityLayer.SERVICE
    }
    if len(source_services) <= 12:
        for ref in source_services:
            if ref in states:
                states[ref].mandatory_reasons.add("EXACT_SOURCE_SERVICE_COMPLETENESS")

    primary_alert = min(alert_entities) if alert_entities else None

    def reachable(start: str, target: str) -> bool:
        if start == target:
            return True
        queue = deque([start])
        seen = {start}
        while queue:
            current = queue.popleft()
            for neighbor in sorted(outgoing.get(current, set())):
                if neighbor == target:
                    return True
                if neighbor not in seen:
                    seen.add(neighbor)
                    queue.append(neighbor)
        return False

    def relation(ref: str) -> RelationCode:
        if primary_alert is None:
            return "NONE"
        if ref == primary_alert:
            return "SELF"
        if ref in _ancestor_paths(primary_alert, parents):
            return "ANC"
        if reachable(ref, primary_alert):
            return "UP"
        if reachable(primary_alert, ref):
            return "DOWN"
        if primary_alert in _distances({ref}, adjacency, maximum=max(len(entities), 1)):
            return "COMP"
        return "NONE"

    def ordering(item: CandidateUniverseEntry) -> tuple[object, ...]:
        return (
            -int(item.mandatory),
            -len(item.all_sources),
            -len(item.direct_sources),
            -int(item.explicit_upstream),
            item.first_anomaly_time
            if item.first_anomaly_time is not None
            else math.inf,
            item.topology_distance if item.topology_distance is not None else math.inf,
            item.metrics_rank if item.metrics_rank is not None else math.inf,
            item.entity_ref,
        )

    universe = tuple(
        sorted(
            (
                CandidateUniverseEntry(
                    entity_ref=ref,
                    display_name=entities[ref].display_name,
                    layer=entities[ref].layer,
                    family=layer_family(entities[ref].layer),
                    mandatory=bool(signals.mandatory_reasons),
                    mandatory_reasons=tuple(sorted(signals.mandatory_reasons)),
                    direct_sources=tuple(
                        sorted(signals.direct_sources, key=SOURCE_ORDER.index)
                    ),
                    inherited_sources=tuple(
                        sorted(signals.inherited_sources, key=SOURCE_ORDER.index)
                    ),
                    evidence_refs=tuple(sorted(signals.evidence_refs)),
                    reasons=tuple(sorted(signals.reasons, key=REASON_ORDER.index)),
                    explicit_upstream=signals.explicit_upstream,
                    first_anomaly_time=signals.first_anomaly_time,
                    topology_distance=signals.topology_distance
                    if signals.topology_distance is not None
                    else component_distances.get(ref),
                    metrics_rank=signals.metrics_rank,
                    relation_to_alert=relation(ref),
                )
                for ref, signals in states.items()
            ),
            key=ordering,
        )
    )

    mandatory = [item for item in universe if item.mandatory]
    selected: list[CandidateUniverseEntry] = mandatory[:12]
    selected_refs = {item.entity_ref for item in selected}
    if len(selected) < 12:
        for family in cast(tuple[Family, ...], ("S", "N", "D")):
            current = sum(item.family == family for item in selected)
            for item in universe:
                if item.family != family or item.entity_ref in selected_refs:
                    continue
                if current >= FAMILY_CAPS[family] or len(selected) >= 12:
                    break
                selected.append(item)
                selected_refs.add(item.entity_ref)
                current += 1
    # Frozen refill S -> N -> D -> global.
    if len(selected) < 12:
        for family in cast(tuple[Family, ...], ("S", "N", "D")):
            for item in universe:
                if item.family == family and item.entity_ref not in selected_refs:
                    selected.append(item)
                    selected_refs.add(item.entity_ref)
                    if len(selected) >= 12:
                        break
            if len(selected) >= 12:
                break
    if len(selected) < 12:
        for item in universe:
            if item.entity_ref not in selected_refs:
                selected.append(item)
                selected_refs.add(item.entity_ref)
                if len(selected) >= 12:
                    break
    final = tuple(sorted(selected, key=ordering))
    return CandidateIndex(
        candidates=tuple(
            CandidateIndexEntry(candidate_id=f"C{index:02d}", universe=item)
            for index, item in enumerate(final, start=1)
        ),
        universe=universe,
    )


__all__ = [
    "CandidateIndex",
    "CandidateIndexEntry",
    "CandidateUniverseEntry",
    "FAMILY_CAPS",
    "REASON_ORDER",
    "build_candidate_index",
    "layer_family",
]
