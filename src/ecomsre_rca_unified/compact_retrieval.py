"""The single frozen R1-R6 compact root-candidate retrieval policy."""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field
import math
from typing import Mapping

from ecomsre_rca_unified.compact_contracts import (
    AllocationBucket,
    CompactBaseContext,
    CompactCandidateCard,
    CompactCandidateContext,
    CompactRetrievalSource,
    EvidenceSource,
    ROOT_ELIGIBLE_LAYERS,
    RelationToAlert,
    RetrievalReason,
)
from ecomsre_rca_unified.contracts import CanonicalEntityLayer


_SOURCE_SEQUENCE: tuple[EvidenceSource, ...] = (
    "METRICS",
    "LOGS",
    "TRACES",
    "EVENTS",
    "ALERTS",
)
_SOURCE_ORDER: dict[EvidenceSource, int] = {
    name: index for index, name in enumerate(_SOURCE_SEQUENCE)
}
_REASON_ORDER = {
    name: index
    for index, name in enumerate(
        (
            "DIRECT_EVIDENCE",
            "EVIDENCE_ANCESTOR",
            "UPSTREAM_DEPENDENCY",
            "EARLIEST_ANOMALY",
            "METRICS_TOPK",
            "ALERT_RELATED",
        )
    )
}
_BUCKET_CAPS: Mapping[AllocationBucket, int] = {
    "R1": 4,
    "R2_R3": 3,
    "R4": 2,
    "R5": 2,
    "R6": 1,
}
_BUCKET_PRIORITY: tuple[AllocationBucket, ...] = (
    "R1",
    "R2_R3",
    "R4",
    "R5",
    "R6",
)
_ANCESTOR_LAYERS = frozenset(
    {
        CanonicalEntityLayer.SERVICE,
        CanonicalEntityLayer.WORKLOAD,
        CanonicalEntityLayer.NODE,
    }
)
_DIRECTED_TYPES = frozenset(
    {"DIRECTED_TOPOLOGY", "TRACE_PARENT_CHILD", "EXPLICIT_DEPENDENCY"}
)


@dataclass(slots=True)
class _Candidate:
    entity_ref: str
    reasons: set[RetrievalReason] = field(default_factory=set)
    visible_sources: set[EvidenceSource] = field(default_factory=set)
    backing_entities: set[str] = field(default_factory=set)
    evidence_refs: set[str] = field(default_factory=set)
    first_anomaly_time: float | None = None
    topology_distance: int | None = None
    metrics_rank: int | None = None


def _distances(
    seeds: set[str], adjacency: Mapping[str, set[str]], *, maximum: int | None = None
) -> dict[str, int]:
    output = {seed: 0 for seed in seeds}
    queue = deque(sorted(seeds))
    while queue:
        current = queue.popleft()
        if maximum is not None and output[current] >= maximum:
            continue
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
        for neighbor in sorted(outgoing.get(current, set())):
            if neighbor == target:
                return True
            if neighbor not in seen:
                seen.add(neighbor)
                queue.append(neighbor)
    return False


def _ancestor_distances(
    entity_ref: str, parents: Mapping[str, set[str]]
) -> dict[str, int]:
    output: dict[str, int] = {}
    queue = deque((item, 1) for item in sorted(parents.get(entity_ref, set())))
    while queue:
        current, distance = queue.popleft()
        prior = output.get(current)
        if prior is not None and prior <= distance:
            continue
        output[current] = distance
        queue.extend(
            (item, distance + 1) for item in sorted(parents.get(current, set()))
        )
    return output


def _normalized_margin(source: CompactRetrievalSource) -> float | None:
    if not source.metrics_ranking:
        return None
    top1 = source.metrics_scores[source.metrics_ranking[0]]
    if len(source.metrics_ranking) == 1:
        return 1.0
    top2 = source.metrics_scores[source.metrics_ranking[1]]
    return (top1 - top2) / max(abs(top1), 1e-12)


def build_compact_candidate_context(
    base: CompactBaseContext,
    source: CompactRetrievalSource,
) -> CompactCandidateContext:
    """Retrieve at most twelve candidates without dataset identity or labels."""

    entities = {item.entity_ref: item for item in source.entities}
    base_refs = {item.entity_ref for item in base.entities}
    if not base_refs.issubset(entities):
        raise ValueError("base entities are absent from the retrieval source")

    parents: dict[str, set[str]] = defaultdict(set)
    outgoing: dict[str, set[str]] = {ref: set() for ref in entities}
    incoming: dict[str, set[str]] = {ref: set() for ref in entities}
    adjacency: dict[str, set[str]] = {ref: set() for ref in entities}
    for item in source.entities:
        if item.parent_ref_or_none is not None:
            parents[item.entity_ref].add(item.parent_ref_or_none)
            adjacency[item.entity_ref].add(item.parent_ref_or_none)
            adjacency[item.parent_ref_or_none].add(item.entity_ref)
        service = item.service_ancestor_or_none
        if service is not None and service != item.entity_ref:
            parents[item.entity_ref].add(service)
            adjacency[item.entity_ref].add(service)
            adjacency[service].add(item.entity_ref)
    for edge in source.edges:
        left = edge.source_entity_ref
        right = edge.target_entity_ref
        adjacency[left].add(right)
        adjacency[right].add(left)
        if edge.edge_type == "PARENT":
            parents[left].add(right)
        elif edge.edge_type in _DIRECTED_TYPES:
            outgoing[left].add(right)
            incoming[right].add(left)

    evidence_by_entity: dict[str, list[str]] = defaultdict(list)
    bounded_counts: dict[str, dict[EvidenceSource, int]] = defaultdict(
        lambda: defaultdict(int)
    )
    for evidence_item in base.evidence:
        evidence_by_entity[evidence_item.entity_ref].append(evidence_item.evidence_ref)
        bounded_counts[evidence_item.entity_ref][evidence_item.source] += 1
    visibility: dict[str, set[EvidenceSource]] = {
        ref: set(values) for ref, values in source.source_visibility.items()
    }
    for evidence_item in base.evidence:
        visibility.setdefault(evidence_item.entity_ref, set()).add(evidence_item.source)
    alert_entities = set(source.alert_entities)
    if base.alert_entity_ref is not None:
        alert_entities.add(base.alert_entity_ref)
    for ref in alert_entities:
        visibility.setdefault(ref, set()).add("ALERTS")

    evidence_entities = {ref for ref, values in visibility.items() if values}
    seeds = evidence_entities | alert_entities
    topology_distances = _distances(seeds, adjacency) if seeds else {}
    metrics_ranks = {
        ref: rank for rank, ref in enumerate(source.metrics_ranking, start=1)
    }
    margin = _normalized_margin(source)
    earliest_case_time = (
        min(source.first_anomaly_time.values()) if source.first_anomaly_time else None
    )

    occurrences: dict[str, int] = {}
    for ref in entities:
        declared = source.source_occurrences.get(ref, {})
        occurrences[ref] = sum(
            max(declared.get(source_name, 0), bounded_counts[ref].get(source_name, 0))
            for source_name in _SOURCE_SEQUENCE
        )

    states: dict[str, _Candidate] = {}
    bucket_refs: dict[AllocationBucket, set[str]] = {
        name: set() for name in _BUCKET_PRIORITY
    }

    def add(
        entity_ref: str,
        *,
        bucket: AllocationBucket,
        reason: RetrievalReason,
        backing_entities: set[str],
        distance: int | None = None,
        metrics_rank: int | None = None,
    ) -> None:
        if entities[entity_ref].layer not in ROOT_ELIGIBLE_LAYERS:
            return
        state = states.setdefault(entity_ref, _Candidate(entity_ref=entity_ref))
        state.reasons.add(reason)
        state.backing_entities.update(backing_entities)
        for backing in backing_entities:
            state.visible_sources.update(visibility.get(backing, set()))
            state.evidence_refs.update(evidence_by_entity.get(backing, ()))
            anomaly = source.first_anomaly_time.get(backing)
            if anomaly is not None and (
                state.first_anomaly_time is None or anomaly < state.first_anomaly_time
            ):
                state.first_anomaly_time = anomaly
        direct_anomaly = source.first_anomaly_time.get(entity_ref)
        if direct_anomaly is not None and (
            state.first_anomaly_time is None
            or direct_anomaly < state.first_anomaly_time
        ):
            state.first_anomaly_time = direct_anomaly
        candidate_distance = topology_distances.get(entity_ref)
        for value in (distance, candidate_distance):
            if value is not None and (
                state.topology_distance is None or value < state.topology_distance
            ):
                state.topology_distance = value
        rank = (
            metrics_rank if metrics_rank is not None else metrics_ranks.get(entity_ref)
        )
        if rank is not None and (
            state.metrics_rank is None or rank < state.metrics_rank
        ):
            state.metrics_rank = rank
        bucket_refs[bucket].add(entity_ref)

    # R1 — direct root-eligible evidence.
    for ref in sorted(evidence_entities):
        if entities[ref].layer in ROOT_ELIGIBLE_LAYERS:
            add(
                ref,
                bucket="R1",
                reason="DIRECT_EVIDENCE",
                backing_entities={ref},
            )

    # R2 — explicit SERVICE / WORKLOAD / NODE ancestors of evidence entities.
    for ref in sorted(evidence_entities):
        ancestors = _ancestor_distances(ref, parents)
        for ancestor, distance in sorted(
            ancestors.items(), key=lambda item: (item[1], item[0])
        ):
            if entities[ancestor].layer in _ANCESTOR_LAYERS:
                add(
                    ancestor,
                    bucket="R2_R3",
                    reason="EVIDENCE_ANCESTOR",
                    backing_entities={ref},
                    distance=distance,
                )

    # R3 — causal upstream/dependency entities within two directed hops.
    for seed in sorted(seeds):
        upstream_distances = _distances({seed}, incoming, maximum=2)
        for upstream, distance in sorted(
            upstream_distances.items(), key=lambda item: (item[1], item[0])
        ):
            if distance == 0:
                continue
            add(
                upstream,
                bucket="R2_R3",
                reason="UPSTREAM_DEPENDENCY",
                backing_entities={seed},
                distance=distance,
            )

    # R4 — root-eligible entities with a deterministic earliest anomaly.
    for ref in sorted(source.first_anomaly_time):
        if entities[ref].layer in ROOT_ELIGIBLE_LAYERS:
            add(
                ref,
                bucket="R4",
                reason="EARLIEST_ANOMALY",
                backing_entities={ref},
            )

    def nearest_root_ancestor(ref: str) -> tuple[str, int] | None:
        if entities[ref].layer in ROOT_ELIGIBLE_LAYERS:
            return ref, 0
        eligible = [
            (ancestor, distance)
            for ancestor, distance in _ancestor_distances(ref, parents).items()
            if entities[ancestor].layer in ROOT_ELIGIBLE_LAYERS
        ]
        return min(eligible, key=lambda item: (item[1], item[0])) if eligible else None

    # R5 — Metrics Top-6 mapped to the nearest root-eligible ancestor.
    for rank, ref in enumerate(source.metrics_ranking, start=1):
        mapped = nearest_root_ancestor(ref)
        if mapped is not None:
            candidate_ref, distance = mapped
            add(
                candidate_ref,
                bucket="R5",
                reason="METRICS_TOPK",
                backing_entities={ref},
                distance=distance,
                metrics_rank=rank,
            )

    # R6 — alert entity or nearest explicit root-eligible ancestor.
    for ref in sorted(alert_entities):
        mapped = nearest_root_ancestor(ref)
        if mapped is not None:
            candidate_ref, distance = mapped
            add(
                candidate_ref,
                bucket="R6",
                reason="ALERT_RELATED",
                backing_entities={ref},
                distance=distance,
            )

    if not states:
        raise ValueError("fixed retrieval policy produced no real candidate")

    def direct_count(state: _Candidate) -> int:
        return occurrences.get(state.entity_ref, 0)

    def order(ref: str) -> tuple[object, ...]:
        state = states[ref]
        return (
            -len(state.visible_sources),
            -direct_count(state),
            (
                state.first_anomaly_time
                if state.first_anomaly_time is not None
                else math.inf
            ),
            state.topology_distance
            if state.topology_distance is not None
            else math.inf,
            state.metrics_rank if state.metrics_rank is not None else math.inf,
            ref,
        )

    ordered_by_bucket = {
        bucket: tuple(sorted(refs, key=order)) for bucket, refs in bucket_refs.items()
    }
    selected: list[tuple[str, AllocationBucket]] = []
    selected_refs: set[str] = set()
    layer_counts: dict[CanonicalEntityLayer, int] = defaultdict(int)
    service_counts: dict[str, int] = defaultdict(int)

    def select(ref: str, bucket: AllocationBucket) -> bool:
        if ref in selected_refs:
            return False
        entity = entities[ref]
        service = entity.service_ancestor_or_none
        if layer_counts[entity.layer] >= 6:
            return False
        if service is not None and service_counts[service] >= 3:
            return False
        selected.append((ref, bucket))
        selected_refs.add(ref)
        layer_counts[entity.layer] += 1
        if service is not None:
            service_counts[service] += 1
        return True

    for bucket in _BUCKET_PRIORITY:
        accepted = 0
        for ref in ordered_by_bucket[bucket]:
            if accepted >= _BUCKET_CAPS[bucket]:
                break
            accepted += int(select(ref, bucket))

    # Fixed refill priority; no second formula or threshold search.
    for bucket in _BUCKET_PRIORITY:
        for ref in ordered_by_bucket[bucket]:
            if len(selected) >= 12:
                break
            select(ref, bucket)
        if len(selected) >= 12:
            break

    primary_alert = (
        base.alert_entity_ref
        if base.alert_entity_ref is not None
        else (min(alert_entities) if alert_entities else None)
    )

    def relation_to_alert(ref: str) -> RelationToAlert:
        if primary_alert is None:
            return "UNKNOWN"
        if ref == primary_alert:
            return "SAME"
        if ref in _ancestor_distances(primary_alert, parents):
            return "ANCESTOR"
        if _reachable(ref, primary_alert, outgoing):
            return "UPSTREAM"
        if _reachable(primary_alert, ref, outgoing):
            return "DOWNSTREAM"
        if primary_alert in _distances({ref}, adjacency):
            return "SAME_COMPONENT"
        return "UNRELATED"

    cards: list[CompactCandidateCard] = []
    for index, (ref, bucket) in enumerate(selected, start=1):
        state = states[ref]
        offset = (
            None
            if state.first_anomaly_time is None or earliest_case_time is None
            else int(round((state.first_anomaly_time - earliest_case_time) * 1_000))
        )
        card_rank = state.metrics_rank
        cards.append(
            CompactCandidateCard(
                candidate_id=f"C{index:02d}",
                display_name=entities[ref].display_name,
                entity_ref=ref,
                entity_layer=entities[ref].layer,
                service_ancestor_or_none=entities[ref].service_ancestor_or_none,
                retrieval_reasons=tuple(
                    sorted(state.reasons, key=_REASON_ORDER.__getitem__)
                ),
                allocation_bucket=bucket,
                visible_sources=tuple(
                    sorted(state.visible_sources, key=_SOURCE_ORDER.__getitem__)
                ),
                metrics_rank_or_none=card_rank,
                metrics_margin_or_none=margin if card_rank == 1 else None,
                first_anomaly_offset_ms_or_none=offset,
                relation_to_alert=relation_to_alert(ref),
                topology_distance_or_none=state.topology_distance,
                evidence_refs=tuple(sorted(state.evidence_refs))[:3],
            )
        )
    return CompactCandidateContext(candidates=tuple(cards))


__all__ = ["build_compact_candidate_context"]
