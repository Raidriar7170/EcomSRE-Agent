"""Exact, label-blind canonical Root Evidence Projection v1.

The module deliberately contains no Provider import or evaluator import.  It
turns only source-visible topology and telemetry into a deterministic private
projection used by the compact candidate index.
"""

from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import Mapping, Sequence
import csv
from dataclasses import dataclass
from datetime import datetime
import json
import math
from pathlib import Path
import re
from typing import Literal, cast

import pyarrow.parquet as pq  # type: ignore[import-untyped]

from ecomsre_rca100.entity import normalize_entity_name
from ecomsre_rca100.projection import RCA100AgentTask, build_agent_context
from ecomsre_rcaeval.adapter import ArchitectureContextBuilder
from ecomsre_rcaeval.contracts import Architecture
from ecomsre_rcaeval.dataset import TelemetryCase
from ecomsre_rca_unified.contracts import CanonicalEntityLayer
from ecomsre_rca_unified.hierarchy import normalize_entity_layer
from ecomsre_rca_unified.propagation import (
    first_marked_log_anomaly,
    first_metric_anomaly,
    first_trace_anomaly,
)


EvidenceSource = Literal["METRICS", "LOGS", "TRACES", "EVENTS", "ALERTS"]
ResolutionKind = Literal[
    "EXACT_ID",
    "UNIQUE_TYPE_NAME",
    "EXACT_SERVICE",
    "AMBIGUOUS",
    "UNRESOLVED",
]

SOURCE_ORDER: tuple[EvidenceSource, ...] = (
    "METRICS",
    "LOGS",
    "TRACES",
    "EVENTS",
    "ALERTS",
)
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


@dataclass(frozen=True, slots=True)
class ProjectedEntity:
    entity_ref: str
    entity_id: str
    entity_type: str
    display_name: str
    normalized_name: str
    layer: CanonicalEntityLayer

    def payload(self) -> dict[str, object]:
        return {
            "entity_ref": self.entity_ref,
            "entity_id": self.entity_id,
            "entity_type": self.entity_type,
            "display_name": self.display_name,
            "normalized_name": self.normalized_name,
            "layer": self.layer.value,
        }


@dataclass(frozen=True, slots=True)
class AliasDisposition:
    source_key: str
    canonical_entity_ref: str | None
    disposition: str

    def payload(self) -> dict[str, object]:
        return {
            "source_key": self.source_key,
            "canonical_entity_ref": self.canonical_entity_ref,
            "disposition": self.disposition,
        }


@dataclass(frozen=True, slots=True)
class EvidenceObservation:
    entity_ref: str
    source: EvidenceSource
    occurrences: int
    first_anomaly_time: float | None
    evidence_refs: tuple[str, ...]
    resolution: ResolutionKind

    def payload(self) -> dict[str, object]:
        return {
            "entity_ref": self.entity_ref,
            "source": self.source,
            "occurrences": self.occurrences,
            "first_anomaly_time": self.first_anomaly_time,
            "evidence_refs": list(self.evidence_refs),
            "resolution": self.resolution,
        }


@dataclass(frozen=True, slots=True)
class AncestorProvenance:
    original_entity_ref: str
    candidate_entity_ref: str
    ancestor_path: tuple[str, ...]
    path_length: int
    source: EvidenceSource
    evidence_ref: str

    def payload(self) -> dict[str, object]:
        return {
            "original_entity_ref": self.original_entity_ref,
            "candidate_entity_ref": self.candidate_entity_ref,
            "ancestor_path": list(self.ancestor_path),
            "path_length": self.path_length,
            "source": self.source,
            "evidence_ref": self.evidence_ref,
        }


@dataclass(frozen=True, slots=True)
class ProjectionCase:
    entities: tuple[ProjectedEntity, ...]
    alias_dispositions: tuple[AliasDisposition, ...]
    parent_edges: tuple[tuple[str, str], ...]  # child, parent
    directed_edges: tuple[tuple[str, str], ...]  # upstream, downstream
    undirected_edges: tuple[tuple[str, str], ...]
    observations: tuple[EvidenceObservation, ...]
    ancestor_provenance: tuple[AncestorProvenance, ...]
    metrics_ranking: tuple[str, ...]
    alert_entities: tuple[str, ...]
    ignored_relation_counts: Mapping[str, int]
    base_context: Mapping[str, object]

    def payload(self) -> dict[str, object]:
        source_visibility: dict[str, list[str]] = defaultdict(list)
        source_occurrences: dict[str, dict[str, int]] = defaultdict(dict)
        first_anomaly_times: dict[str, float] = {}
        for item in self.observations:
            source_visibility[item.entity_ref].append(item.source)
            source_occurrences[item.entity_ref][item.source] = item.occurrences
            if item.first_anomaly_time is not None:
                prior = first_anomaly_times.get(item.entity_ref)
                first_anomaly_times[item.entity_ref] = (
                    item.first_anomaly_time
                    if prior is None
                    else min(prior, item.first_anomaly_time)
                )
        return {
            "schema_version": "root-evidence-projection.case.v1",
            "canonical_entities": [item.payload() for item in self.entities],
            "alias_dispositions": [item.payload() for item in self.alias_dispositions],
            "root_ancestor_closure": [
                item.payload() for item in self.ancestor_provenance
            ],
            "source_visibility": {
                key: sorted(set(values), key=SOURCE_ORDER.index)
                for key, values in sorted(source_visibility.items())
            },
            "source_occurrence_counts": {
                key: dict(sorted(values.items()))
                for key, values in sorted(source_occurrences.items())
            },
            "first_anomaly_times": dict(sorted(first_anomaly_times.items())),
            "directed_relations": [list(item) for item in self.directed_edges],
            "undirected_relations": [list(item) for item in self.undirected_edges],
            "parent_relations": [list(item) for item in self.parent_edges],
            "metrics_ranking": list(self.metrics_ranking),
            "alert_entities": list(self.alert_entities),
            "ignored_relation_counts": dict(
                sorted(self.ignored_relation_counts.items())
            ),
        }


class _UnionFind:
    def __init__(self, values: Sequence[str]) -> None:
        self.parent = {value: value for value in values}

    def find(self, value: str) -> str:
        parent = self.parent[value]
        if parent != value:
            self.parent[value] = self.find(parent)
        return self.parent[value]

    def union(self, left: str, right: str) -> None:
        a = self.find(left)
        b = self.find(right)
        if a != b:
            self.parent[max(a, b)] = min(a, b)


@dataclass(frozen=True, slots=True)
class _RawEntity:
    entity_ref: str
    entity_id: str
    entity_type: str
    name: str
    normalized_name: str
    layer: CanonicalEntityLayer
    props: Mapping[str, object]


class CanonicalTopology:
    """Topology catalog with only the frozen exact alias operations."""

    def __init__(self, payload: Mapping[str, object]) -> None:
        raw_entities = payload.get("entities")
        raw_edges = payload.get("edges", [])
        if not isinstance(raw_entities, list) or not isinstance(raw_edges, list):
            raise ValueError("projection topology schema is invalid")
        raws: dict[str, _RawEntity] = {}
        for raw in raw_entities:
            if not isinstance(raw, Mapping):
                raise ValueError("projection topology entity must be an object")
            entity_id = raw.get("id")
            entity_type = raw.get("type")
            name = raw.get("name")
            if not all(
                isinstance(value, str) and value
                for value in (entity_id, entity_type, name)
            ):
                raise ValueError("projection topology entity identity is invalid")
            assert isinstance(entity_id, str)
            assert isinstance(entity_type, str)
            assert isinstance(name, str)
            ref = f"{entity_type.split('.', 1)[0]}|{entity_type}|{entity_id}"
            if ref in raws:
                raise ValueError("projection topology contains a duplicate entity")
            props = raw.get("props")
            raws[ref] = _RawEntity(
                entity_ref=ref,
                entity_id=entity_id,
                entity_type=entity_type,
                name=name,
                normalized_name=normalize_entity_name(name),
                layer=normalize_entity_layer(entity_type),
                props=props if isinstance(props, Mapping) else {},
            )
        id_to_raw = {raw.entity_id: raw.entity_ref for raw in raws.values()}
        if len(id_to_raw) != len(raws):
            raise ValueError("projection topology contains a duplicate exact ID")
        union = _UnionFind(tuple(raws))
        parsed_edges: list[tuple[str, str, str]] = []
        ignored: dict[str, int] = defaultdict(int)
        for edge in raw_edges:
            if not isinstance(edge, Mapping):
                raise ValueError("projection topology edge must be an object")
            src_id = edge.get("src")
            dst_id = edge.get("dst")
            relation_raw = edge.get("relation")
            if not isinstance(src_id, str) or not isinstance(dst_id, str):
                raise ValueError("projection topology edge identity is invalid")
            src = id_to_raw.get(src_id)
            dst = id_to_raw.get(dst_id)
            if src is None or dst is None:
                raise ValueError("projection topology edge is dangling")
            relation = str(relation_raw or "").strip().casefold()
            if relation == "same_as":
                union.union(src, dst)
            parsed_edges.append((src, dst, relation))
        groups: dict[str, set[str]] = defaultdict(set)
        for ref in raws:
            groups[union.find(ref)].add(ref)
        canonical_for: dict[str, str] = {}
        entities: dict[str, ProjectedEntity] = {}
        alias_dispositions: list[AliasDisposition] = []
        for members in groups.values():
            eligible = sorted(
                ref for ref in members if raws[ref].layer in ROOT_ELIGIBLE_LAYERS
            )
            representative = eligible[0] if eligible else min(members)
            chosen = raws[representative]
            entities[representative] = ProjectedEntity(
                entity_ref=representative,
                entity_id=chosen.entity_id,
                entity_type=chosen.entity_type,
                display_name=chosen.name,
                normalized_name=chosen.normalized_name,
                layer=chosen.layer,
            )
            for ref in sorted(members):
                canonical_for[ref] = representative
                alias_dispositions.append(
                    AliasDisposition(
                        source_key=ref,
                        canonical_entity_ref=representative,
                        disposition=(
                            "EXACT_ID" if ref == representative else "EXPLICIT_SAME_AS"
                        ),
                    )
                )
        self.raws = raws
        self.canonical_for = canonical_for
        self.entities = entities
        self.alias_dispositions = tuple(alias_dispositions)
        self.by_id: dict[str, set[str]] = defaultdict(set)
        self.by_type_name: dict[tuple[str, str], set[str]] = defaultdict(set)
        self.service_by_name: dict[str, set[str]] = defaultdict(set)
        for raw in raws.values():
            canonical = canonical_for[raw.entity_ref]
            self.by_id[raw.entity_id].add(canonical)
            self.by_type_name[(raw.entity_type, raw.normalized_name)].add(canonical)
            if entities[canonical].layer is CanonicalEntityLayer.SERVICE:
                self.service_by_name[raw.normalized_name].add(canonical)
                for key in ("service", "serviceName", "service_name"):
                    value = raw.props.get(key)
                    if isinstance(value, str) and value.strip():
                        self.service_by_name[normalize_entity_name(value)].add(
                            canonical
                        )
        parents: set[tuple[str, str]] = set()
        directed: set[tuple[str, str]] = set()
        undirected: set[tuple[str, str]] = set()
        for raw_src, raw_dst, relation in parsed_edges:
            src = canonical_for[raw_src]
            dst = canonical_for[raw_dst]
            if src == dst:
                continue
            if relation in {"contains", "parent"}:
                parents.add((dst, src))
            elif relation in {"calls", "depends_on", "dependency"}:
                # RCA100 methodology freezes destination as upstream dependency.
                directed.add((dst, src))
            elif relation == "hosts":
                left, right = sorted((src, dst))
                undirected.add((left, right))
            elif relation == "same_as":
                continue
            else:
                ignored[relation or "<empty>"] += 1
        # A source-visible service property is an explicit service ancestor.
        for raw in raws.values():
            child = canonical_for[raw.entity_ref]
            for key in ("service", "serviceName", "service_name"):
                value = raw.props.get(key)
                if not isinstance(value, str) or not value.strip():
                    continue
                services = self.service_by_name.get(normalize_entity_name(value), set())
                if len(services) == 1:
                    parent = next(iter(services))
                    if child != parent:
                        parents.add((child, parent))
        self.parent_edges = tuple(sorted(parents))
        self.directed_edges = tuple(sorted(directed))
        self.undirected_edges = tuple(sorted(undirected))
        self.ignored_relation_counts = dict(ignored)
        self.parents: dict[str, set[str]] = defaultdict(set)
        for child, parent in self.parent_edges:
            self.parents[child].add(parent)

    @classmethod
    def load(cls, path: Path) -> CanonicalTopology:
        if path.is_symlink() or not path.is_file():
            raise ValueError("projection topology must be a regular file")
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, Mapping):
            raise ValueError("projection topology must be an object")
        return cls(value)

    def resolve(
        self,
        *,
        entity_id: str | None = None,
        entity_type: str | None = None,
        entity_name: str | None = None,
        service: str | None = None,
    ) -> tuple[str | None, ResolutionKind]:
        if entity_id:
            matches = self.by_id.get(entity_id, set())
            if len(matches) == 1:
                return next(iter(matches)), "EXACT_ID"
            if len(matches) > 1:
                return None, "AMBIGUOUS"
        if entity_type and entity_name:
            matches = self.by_type_name.get(
                (entity_type, normalize_entity_name(entity_name)), set()
            )
            if len(matches) == 1:
                return next(iter(matches)), "UNIQUE_TYPE_NAME"
            if len(matches) > 1:
                return None, "AMBIGUOUS"
        if service:
            matches = self.service_by_name.get(normalize_entity_name(service), set())
            if len(matches) == 1:
                return next(iter(matches)), "EXACT_SERVICE"
            if len(matches) > 1:
                return None, "AMBIGUOUS"
        return None, "UNRESOLVED"

    def ancestors(
        self, entity_ref: str, *, maximum_depth: int = 4
    ) -> tuple[tuple[str, tuple[str, ...]], ...]:
        if entity_ref not in self.entities:
            return ()
        output: dict[str, tuple[str, ...]] = {}
        queue: deque[tuple[str, tuple[str, ...]]] = deque(
            (parent, (entity_ref, parent))
            for parent in sorted(self.parents[entity_ref])
        )
        while queue:
            current, path = queue.popleft()
            depth = len(path) - 1
            prior = output.get(current)
            if prior is not None and len(prior) <= len(path):
                continue
            output[current] = path
            if depth < maximum_depth:
                for parent in sorted(self.parents[current]):
                    if parent not in path:
                        queue.append((parent, (*path, parent)))
        return tuple(sorted(output.items(), key=lambda item: (len(item[1]), item[0])))


@dataclass(slots=True)
class _Observed:
    count: int = 0
    first: float | None = None
    refs: set[str] | None = None
    resolutions: set[ResolutionKind] | None = None

    def __post_init__(self) -> None:
        if self.refs is None:
            self.refs = set()
        if self.resolutions is None:
            self.resolutions = set()


def _parse_timestamp(value: object) -> float | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        number = float(text)
    except ValueError:
        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return None
    if not math.isfinite(number):
        return None
    if number >= 1e17:
        return number / 1e9
    if number >= 1e14:
        return number / 1e6
    if number >= 1e11:
        return number / 1e3
    return number


def _base_context_dump(context: object) -> dict[str, object]:
    task = getattr(context, "task")
    entities = getattr(context, "visible_entities")
    evidence: list[dict[str, object]] = []
    for projection in (
        getattr(context, "metrics"),
        getattr(context, "logs"),
        getattr(context, "traces"),
    ):
        evidence.extend(item.model_dump(mode="json") for item in projection.evidence)
    return {
        "schema_version": "strong-single-live.base-context.v1",
        "alert_title": task.alert_title,
        "prompt_text": task.prompt_text,
        "alert_entity_ref": task.alert_entity_ref,
        "entities": [
            {
                "entity_ref": item.entity_ref,
                "entity_name": item.entity_name,
                "layer": normalize_entity_layer(item.type).value,
                "service_ancestor_or_none": (
                    item.entity_ref
                    if normalize_entity_layer(item.type) is CanonicalEntityLayer.SERVICE
                    else item.parent_service_ref_or_none
                ),
                "parent_ref_or_none": item.parent_service_ref_or_none,
            }
            for item in entities
        ],
        "evidence": evidence,
        "source_status": {
            "METRICS": (
                "AVAILABLE"
                if getattr(context, "metrics").status == "AVAILABLE"
                else "SOURCE_UNAVAILABLE"
            ),
            "LOGS": getattr(context, "logs").status,
            "TRACES": getattr(context, "traces").status,
        },
    }


def _resolution_choice(values: set[ResolutionKind]) -> ResolutionKind:
    order: tuple[ResolutionKind, ...] = (
        "EXACT_ID",
        "UNIQUE_TYPE_NAME",
        "EXACT_SERVICE",
        "AMBIGUOUS",
        "UNRESOLVED",
    )
    return next((item for item in order if item in values), "UNRESOLVED")


def _finalize_projection(
    *,
    topology: CanonicalTopology,
    observed: Mapping[tuple[str, EvidenceSource], _Observed],
    metrics_ranking: Sequence[str],
    alert_entities: set[str],
    trace_edges: set[tuple[str, str]],
    base_context: Mapping[str, object],
) -> ProjectionCase:
    observations: list[EvidenceObservation] = []
    provenance: list[AncestorProvenance] = []
    for (entity_ref, source), item in sorted(observed.items()):
        assert item.refs is not None and item.resolutions is not None
        refs = tuple(sorted(item.refs)) or (f"{source.casefold()}-source:0000",)
        observations.append(
            EvidenceObservation(
                entity_ref=entity_ref,
                source=source,
                occurrences=item.count,
                first_anomaly_time=item.first,
                evidence_refs=refs,
                resolution=_resolution_choice(item.resolutions),
            )
        )
        if topology.entities[entity_ref].layer in ROOT_ELIGIBLE_LAYERS:
            for evidence_ref in refs:
                provenance.append(
                    AncestorProvenance(
                        original_entity_ref=entity_ref,
                        candidate_entity_ref=entity_ref,
                        ancestor_path=(entity_ref,),
                        path_length=0,
                        source=source,
                        evidence_ref=evidence_ref,
                    )
                )
        for ancestor, path in topology.ancestors(entity_ref, maximum_depth=4):
            if topology.entities[ancestor].layer not in ROOT_ELIGIBLE_LAYERS:
                continue
            for evidence_ref in refs:
                provenance.append(
                    AncestorProvenance(
                        original_entity_ref=entity_ref,
                        candidate_entity_ref=ancestor,
                        ancestor_path=path,
                        path_length=len(path) - 1,
                        source=source,
                        evidence_ref=evidence_ref,
                    )
                )
    directed = set(topology.directed_edges) | trace_edges
    undirected = set(topology.undirected_edges)
    for edge in (*topology.parent_edges, *directed):
        left, right = sorted(edge)
        undirected.add((left, right))
    return ProjectionCase(
        entities=tuple(
            sorted(topology.entities.values(), key=lambda item: item.entity_ref)
        ),
        alias_dispositions=topology.alias_dispositions,
        parent_edges=topology.parent_edges,
        directed_edges=tuple(sorted(directed)),
        undirected_edges=tuple(sorted(undirected)),
        observations=tuple(observations),
        ancestor_provenance=tuple(
            sorted(
                provenance,
                key=lambda item: (
                    item.original_entity_ref,
                    item.candidate_entity_ref,
                    item.source,
                    item.evidence_ref,
                ),
            )
        ),
        metrics_ranking=tuple(dict.fromkeys(metrics_ranking))[:6],
        alert_entities=tuple(sorted(alert_entities)),
        ignored_relation_counts=topology.ignored_relation_counts,
        base_context=base_context,
    )


def build_rca100_projection(
    case_root: Path,
    *,
    projection_case_number: int,
    methodology: Mapping[str, object],
) -> ProjectionCase:
    """Build one RCA100 case projection without labels or evaluator imports."""

    if not 1 <= projection_case_number <= 9_999:
        raise ValueError("projection ordinal is invalid")
    context = build_agent_context(
        case_root, opaque_case_id=f"rca100-case-{projection_case_number:04d}"
    )
    task: RCA100AgentTask = context.task
    topology = CanonicalTopology.load(case_root / "topology.json")
    observed: dict[tuple[str, EvidenceSource], _Observed] = defaultdict(_Observed)
    alert_entities: set[str] = set()
    trace_edges: set[tuple[str, str]] = set()

    def observe(
        entity_ref: str | None,
        source: EvidenceSource,
        resolution: ResolutionKind,
        *,
        timestamp: float | None = None,
        evidence_ref: str | None = None,
    ) -> None:
        if entity_ref is None:
            return
        item = observed[(entity_ref, source)]
        item.count += 1
        assert item.refs is not None and item.resolutions is not None
        item.resolutions.add(resolution)
        if evidence_ref is not None:
            item.refs.add(evidence_ref)
        if timestamp is not None:
            item.first = timestamp if item.first is None else min(item.first, timestamp)

    anomaly = methodology.get("first_anomaly")
    if not isinstance(anomaly, Mapping):
        raise ValueError("projection anomaly methodology is invalid")
    metric_config = anomaly.get("metrics")
    log_config = anomaly.get("logs")
    trace_config = anomaly.get("traces")
    if not all(
        isinstance(value, Mapping)
        for value in (metric_config, log_config, trace_config)
    ):
        raise ValueError("projection anomaly methodology sections are invalid")
    assert isinstance(metric_config, Mapping)
    assert isinstance(log_config, Mapping)
    assert isinstance(trace_config, Mapping)

    metric_series: dict[tuple[str, str, str, str], list[tuple[float, float]]] = (
        defaultdict(list)
    )
    metrics = pq.ParquetFile(case_root / "metrics.parquet")
    metric_columns = [
        "time",
        "entity_set",
        "entity_id",
        "entity_name",
        "metric",
        "value",
        "metric_set_id",
        "service",
    ]
    for batch in metrics.iter_batches(batch_size=65_536, columns=metric_columns):
        for row in batch.to_pylist():
            timestamp = _parse_timestamp(row.get("time"))
            value = row.get("value")
            if (
                timestamp is None
                or not task.window_start_timestamp
                <= timestamp
                <= task.window_end_timestamp
            ):
                continue
            if (
                not isinstance(value, (int, float))
                or isinstance(value, bool)
                or not math.isfinite(float(value))
            ):
                continue
            ref, resolution = topology.resolve(
                entity_id=str(row.get("entity_id") or "") or None,
                entity_type=str(row.get("entity_set") or "") or None,
                entity_name=str(row.get("entity_name") or "") or None,
                service=str(row.get("service") or "") or None,
            )
            observe(ref, "METRICS", resolution)
            if ref is not None:
                metric_series[
                    (
                        ref,
                        str(row.get("metric") or ""),
                        str(row.get("metric_set_id") or ""),
                        str(row.get("service") or ""),
                    )
                ].append((timestamp, float(value)))
    metric_best: dict[str, tuple[float, str]] = {}
    for key, samples in metric_series.items():
        pre = [
            value for timestamp, value in samples if timestamp < task.anchor_timestamp
        ]
        post = [
            value for timestamp, value in samples if timestamp >= task.anchor_timestamp
        ]
        if len(pre) >= 3 and len(post) >= 3:
            pre_mean = sum(pre) / len(pre)
            post_mean = sum(post) / len(post)
            score = abs(post_mean - pre_mean) / max(abs(pre_mean), 1e-9)
            tie = "\0".join(key[1:])
            prior = metric_best.get(key[0])
            if prior is None or (-score, tie) < (-prior[0], prior[1]):
                metric_best[key[0]] = (score, tie)
        anomaly_time = first_metric_anomaly(
            samples=samples,
            anchor=task.anchor_timestamp,
            minimum_pre_samples=int(metric_config["minimum_pre_samples"]),
            minimum_post_samples=int(metric_config["minimum_post_samples"]),
            mad_multiplier=float(metric_config["mad_multiplier"]),
            relative_floor=float(metric_config["absolute_relative_floor"]),
            epsilon=float(metric_config["zero_epsilon"]),
        )
        if anomaly_time is not None:
            observe(key[0], "METRICS", "EXACT_ID", timestamp=anomaly_time)
    metrics_ranking = tuple(
        ref
        for ref, _ in sorted(
            metric_best.items(), key=lambda item: (-item[1][0], item[1][1], item[0])
        )[:6]
    )

    raw_markers = log_config.get("content_markers")
    if not isinstance(raw_markers, Sequence):
        raise ValueError("projection log markers are invalid")
    markers = tuple(str(value) for value in raw_markers)
    marked_logs: dict[str, list[tuple[float, str]]] = defaultdict(list)
    logs = pq.ParquetFile(case_root / "logs.parquet")
    log_columns = set(logs.schema.names)
    selected_log_columns = [
        name
        for name in (
            "content",
            "_time_",
            "_pod_uid_",
            "_pod_name_",
            "_container_name_",
            "_namespace_",
            "service",
            "serviceName",
        )
        if name in log_columns
    ]
    for batch in logs.iter_batches(batch_size=65_536, columns=selected_log_columns):
        for row in batch.to_pylist():
            timestamp = _parse_timestamp(row.get("_time_"))
            if (
                timestamp is None
                or not task.window_start_timestamp
                <= timestamp
                <= task.window_end_timestamp
            ):
                continue
            ref, resolution = topology.resolve(
                entity_id=str(row.get("_pod_uid_") or "") or None,
                entity_type="k8s.pod",
                entity_name=str(row.get("_pod_name_") or "") or None,
            )
            if ref is None:
                ref, resolution = topology.resolve(
                    entity_type="k8s.container",
                    entity_name=str(row.get("_container_name_") or "") or None,
                )
            observe(ref, "LOGS", resolution)
            service_value = str(row.get("service") or row.get("serviceName") or "")
            if service_value:
                service_ref, service_resolution = topology.resolve(
                    service=service_value
                )
                if service_ref != ref:
                    observe(service_ref, "LOGS", service_resolution)
            content = str(row.get("content") or "")
            if (
                ref is not None
                and timestamp >= task.anchor_timestamp
                and any(marker in content.casefold() for marker in markers)
            ):
                marked_logs[ref].append((timestamp, content))
    for ref, log_samples in marked_logs.items():
        value = first_marked_log_anomaly(
            samples=log_samples, anchor=task.anchor_timestamp, markers=markers
        )
        if value is not None:
            observe(ref, "LOGS", "EXACT_ID", timestamp=value)

    traces = pq.ParquetFile(case_root / "traces.parquet")
    span_service: dict[tuple[str, str], str] = {}
    pending_parents: list[tuple[tuple[str, str], tuple[str, str]]] = []
    trace_samples: dict[str, list[tuple[float, float, bool]]] = defaultdict(list)
    trace_columns = [
        name
        for name in (
            "traceId",
            "spanId",
            "parentSpanId",
            "startTime",
            "duration",
            "serviceName",
            "statusCode",
            "spanName",
        )
        if name in traces.schema.names
    ]
    for batch in traces.iter_batches(batch_size=65_536, columns=trace_columns):
        for row in batch.to_pylist():
            timestamp = _parse_timestamp(row.get("startTime"))
            if (
                timestamp is None
                or not task.window_start_timestamp
                <= timestamp
                <= task.window_end_timestamp
            ):
                continue
            ref, resolution = topology.resolve(
                service=str(row.get("serviceName") or "") or None
            )
            observe(ref, "TRACES", resolution)
            if ref is None:
                continue
            trace_id = str(row.get("traceId") or "")
            span_id = str(row.get("spanId") or "")
            parent_id = str(row.get("parentSpanId") or "")
            if trace_id and span_id:
                span_service[(trace_id, span_id)] = ref
                if parent_id:
                    pending_parents.append(((trace_id, span_id), (trace_id, parent_id)))
            try:
                duration = float(str(row.get("duration") or "nan"))
            except ValueError:
                continue
            if math.isfinite(duration):
                status = str(row.get("statusCode") or "").casefold()
                failed = status not in {"", "0", "1", "false", "ok", "unset"}
                trace_samples[ref].append((timestamp, duration, failed))
    for ref, entity_trace_samples in trace_samples.items():
        value = first_trace_anomaly(
            samples=entity_trace_samples,
            anchor=task.anchor_timestamp,
            minimum_pre_samples=int(trace_config["minimum_pre_samples_for_slow"]),
            slow_multiplier=float(trace_config["slow_multiplier"]),
        )
        if value is not None:
            observe(ref, "TRACES", "EXACT_ID", timestamp=value)
    for child_key, parent_key in pending_parents:
        child = span_service.get(child_key)
        parent = span_service.get(parent_key)
        if child is not None and parent is not None and child != parent:
            trace_edges.add((parent, child))

    events = pq.ParquetFile(case_root / "events.parquet")
    for batch in events.iter_batches(batch_size=16_384):
        for row in batch.to_pylist():
            encoded = row.get("eventId")
            if not isinstance(encoded, str):
                continue
            try:
                value = json.loads(encoded)
            except json.JSONDecodeError:
                continue
            if not isinstance(value, Mapping) or not isinstance(
                value.get("involvedObject"), Mapping
            ):
                continue
            involved = cast(Mapping[str, object], value["involvedObject"])
            kind = str(involved.get("kind") or "").casefold()
            entity_type = {
                "pod": "k8s.pod",
                "deployment": "k8s.deployment",
                "statefulset": "k8s.statefulset",
                "node": "k8s.node",
                "service": "k8s.service",
            }.get(kind)
            if entity_type is None:
                continue
            ref, resolution = topology.resolve(
                entity_id=str(involved.get("uid") or "") or None,
                entity_type=entity_type,
                entity_name=str(involved.get("name") or "") or None,
            )
            timestamps = [
                timestamp
                for timestamp in (
                    _parse_timestamp(value.get("eventTime")),
                    _parse_timestamp(value.get("firstTimestamp")),
                    _parse_timestamp(value.get("lastTimestamp")),
                )
                if timestamp is not None
            ]
            timestamp = min(timestamps) if timestamps else None
            if (
                timestamp is not None
                and not task.window_start_timestamp
                <= timestamp
                <= task.window_end_timestamp
            ):
                continue
            observe(
                ref,
                "EVENTS",
                resolution,
                timestamp=timestamp
                if timestamp is not None and timestamp >= task.anchor_timestamp
                else None,
                evidence_ref=None,
            )

    alerts = pq.ParquetFile(case_root / "alerts.parquet")
    for batch in alerts.iter_batches(batch_size=16_384):
        for row in batch.to_pylist():
            encoded = row.get("resource")
            if not isinstance(encoded, str):
                continue
            try:
                resource = json.loads(encoded)
            except json.JSONDecodeError:
                continue
            if not isinstance(resource, Mapping) or not isinstance(
                resource.get("entity"), Mapping
            ):
                continue
            entity = cast(Mapping[str, object], resource["entity"])
            ref, resolution = topology.resolve(
                entity_id=str(entity.get("entity_id") or "") or None,
                entity_type=str(entity.get("entity_type") or "") or None,
                entity_name=str(entity.get("entity_name") or "") or None,
            )
            timestamp = _parse_timestamp(row.get("time_s")) or _parse_timestamp(
                row.get("time")
            )
            if (
                timestamp is not None
                and not task.window_start_timestamp
                <= timestamp
                <= task.window_end_timestamp
            ):
                continue
            observe(
                ref,
                "ALERTS",
                resolution,
                timestamp=timestamp
                if timestamp is not None and timestamp >= task.anchor_timestamp
                else None,
                evidence_ref=None,
            )
            if ref is not None:
                alert_entities.add(ref)

    # Bind model-visible evidence refs to their exactly canonicalized entities.
    base = _base_context_dump(context)
    for raw in cast(list[dict[str, object]], base["evidence"]):
        raw_ref = raw.get("entity_ref")
        evidence_ref = raw.get("evidence_ref")
        if not isinstance(raw_ref, str) or not isinstance(evidence_ref, str):
            continue
        canonical = topology.canonical_for.get(raw_ref)
        if canonical is None:
            continue
        source = cast(
            EvidenceSource,
            {"metric": "METRICS", "log": "LOGS", "trace": "TRACES"}[
                evidence_ref.partition(":")[0]
            ],
        )
        observe(canonical, source, "EXACT_ID", evidence_ref=evidence_ref)
    raw_alert = context.task.alert_entity_ref
    if (
        raw_alert is not None
        and (canonical_alert := topology.canonical_for.get(raw_alert)) is not None
    ):
        alert_entities.add(canonical_alert)
        observe(canonical_alert, "ALERTS", "EXACT_ID", evidence_ref="alert:0000")
    return _finalize_projection(
        topology=topology,
        observed=observed,
        metrics_ranking=metrics_ranking,
        alert_entities=alert_entities,
        trace_edges=trace_edges,
        base_context=base,
    )


def _regular(path: Path, label: str) -> Path:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"projection requires a regular {label}")
    return path


def discover_label_blind_obss_cases(
    root: Path, *, system: Literal["RE2-OB", "RE2-SS"]
) -> tuple[TelemetryCase, ...]:
    """Discover opaque cases without parsing truth encoded in group names."""

    if root.name != system or root.is_symlink() or not root.is_dir():
        raise ValueError("label-blind OB/SS root is invalid")
    output: list[TelemetryCase] = []
    for group in sorted(path for path in root.iterdir() if path.is_dir()):
        if group.is_symlink():
            raise ValueError("label-blind OB/SS group is a symlink")
        for case_root in sorted(
            path
            for path in group.iterdir()
            if path.is_dir() and path.name in {"1", "2", "3"}
        ):
            metrics = tuple(
                path
                for path in (case_root / "simple_metrics.csv", case_root / "data.csv")
                if path.exists()
            )
            if len(metrics) != 1:
                raise ValueError("label-blind OB/SS metrics are invalid")
            traces_candidate = case_root / "traces.csv"
            traces = (
                _regular(traces_candidate, "traces.csv") if system == "RE2-OB" else None
            )
            if system == "RE2-SS" and traces_candidate.exists():
                raise ValueError("label-blind RE2-SS unexpectedly contains traces")
            inject_time = int(
                _regular(case_root / "inject_time.txt", "inject time")
                .read_text(encoding="utf-8")
                .strip()
            )
            output.append(
                TelemetryCase(
                    case_id=f"{system.casefold()}-case-{len(output) + 1:04d}",
                    system=system,
                    root=case_root,
                    metrics_path=_regular(metrics[0], "metrics CSV"),
                    logs_path=_regular(case_root / "logs.csv", "logs CSV"),
                    traces_path=traces,
                    inject_time=inject_time,
                )
            )
    if len(output) != 90:
        raise ValueError("label-blind OB/SS denominator differs")
    return tuple(output)


def _service_ref(value: str) -> str:
    normalized = normalize_entity_name(value)
    if (
        not normalized
        or re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,127}", normalized) is None
    ):
        raise ValueError("source-visible service name is invalid")
    return f"apm|apm.service|{normalized}"


def _csv_value(row: Mapping[str, str], names: Sequence[str]) -> str | None:
    lowered = {key.casefold(): key for key in row}
    for name in names:
        key = lowered.get(name.casefold())
        if key is not None:
            return row.get(key)
    return None


def build_obss_projection(case: TelemetryCase) -> ProjectionCase:
    """Build an OB/SS projection using only telemetry-visible service fields."""

    builder = ArchitectureContextBuilder(case, Architecture.SINGLE)
    for query_source in cast(
        tuple[Literal["metrics", "logs", "traces"], ...], ("metrics", "logs", "traces")
    ):
        builder.query_source(query_source)
    context = builder.snapshot()
    services: set[str] = set()
    observed: dict[tuple[str, EvidenceSource], _Observed] = defaultdict(_Observed)
    trace_edges: set[tuple[str, str]] = set()
    lower = case.inject_time - 600
    upper = case.inject_time + 600

    def observe(
        service: str,
        source: EvidenceSource,
        *,
        timestamp: float | None = None,
        evidence_ref: str | None = None,
    ) -> str:
        ref = _service_ref(service)
        services.add(ref)
        item = observed[(ref, source)]
        item.count += 1
        assert item.refs is not None and item.resolutions is not None
        item.resolutions.add("EXACT_SERVICE")
        if evidence_ref is not None:
            item.refs.add(evidence_ref)
        if timestamp is not None:
            item.first = timestamp if item.first is None else min(item.first, timestamp)
        return ref

    with case.metrics_path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise ValueError("OB/SS metrics require a header")
        metric_services = sorted(
            {
                name.rsplit("_", 1)[0]
                for name in reader.fieldnames
                if name != "time" and "_" in name
            }
        )
        visible_rows = 0
        for row in reader:
            timestamp = _parse_timestamp(_csv_value(row, ("time",)))
            if timestamp is not None and lower <= timestamp <= upper:
                visible_rows += 1
        for service in metric_services:
            for _ in range(max(visible_rows, 1)):
                observe(service, "METRICS")
    with case.logs_path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise ValueError("OB/SS logs require a header")
        for row in reader:
            timestamp = _parse_timestamp(_csv_value(row, ("timestamp", "time")))
            log_service = _csv_value(row, ("service", "serviceName", "container_name"))
            if timestamp is not None and lower <= timestamp <= upper and log_service:
                observe(log_service, "LOGS")
    if case.traces_path is not None:
        span_services: dict[tuple[str, str], str] = {}
        parents: list[tuple[tuple[str, str], tuple[str, str]]] = []
        with case.traces_path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames is None:
                raise ValueError("OB/SS traces require a header")
            for row in reader:
                timestamp = _parse_timestamp(
                    _csv_value(
                        row,
                        (
                            "startTimeMillis",
                            "startTime",
                            "start_time",
                            "timestamp",
                            "time",
                        ),
                    )
                )
                service = (_csv_value(row, ("service", "serviceName")) or "").strip()
                if timestamp is None or not lower <= timestamp <= upper or not service:
                    continue
                ref = observe(service, "TRACES")
                trace_id = row.get("traceID") or row.get("traceId") or ""
                span_id = row.get("spanID") or row.get("spanId") or ""
                parent_id = row.get("parentSpanID") or row.get("parentSpanId") or ""
                if trace_id and span_id:
                    span_services[(trace_id, span_id)] = ref
                    if parent_id:
                        parents.append(((trace_id, span_id), (trace_id, parent_id)))
        for child_key, parent_key in parents:
            child = span_services.get(child_key)
            parent = span_services.get(parent_key)
            if child is not None and parent is not None and child != parent:
                trace_edges.add((parent, child))
    evidence_rows: list[dict[str, object]] = []
    metrics_scores: dict[str, float] = {}
    for item in context.evidence:
        if item.service == "unknown":
            continue
        evidence_source = cast(
            EvidenceSource,
            {"metric": "METRICS", "log": "LOGS", "trace": "TRACES"}[
                item.evidence_id.partition(":")[0]
            ],
        )
        ref = observe(
            item.service,
            evidence_source,
            timestamp=item.started_at,
            evidence_ref=item.evidence_id,
        )
        if evidence_source == "METRICS":
            match = re.search(
                r"(?:anomaly|combined)-score=([-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?)",
                item.summary,
            )
            try:
                score = float(match.group(1)) if match is not None else 0.0
            except ValueError:
                score = 0.0
            metrics_scores[ref] = max(metrics_scores.get(ref, -math.inf), score)
        evidence_rows.append(
            {
                "evidence_ref": item.evidence_id,
                "source": evidence_source,
                "entity_ref": ref,
                "name": item.name,
                "started_at": item.started_at,
                "ended_at": item.ended_at,
                "score": (
                    metrics_scores.get(ref, 0.0)
                    if evidence_source == "METRICS"
                    else 0.0
                ),
                "summary": item.summary,
            }
        )
    entities = {
        ref: ProjectedEntity(
            entity_ref=ref,
            entity_id=ref.rsplit("|", 1)[-1],
            entity_type="apm.service",
            display_name=ref.rsplit("|", 1)[-1],
            normalized_name=ref.rsplit("|", 1)[-1],
            layer=CanonicalEntityLayer.SERVICE,
        )
        for ref in services
    }
    topology_payload: dict[str, object] = {
        "entities": [
            {"id": item.entity_id, "type": item.entity_type, "name": item.display_name}
            for item in entities.values()
        ],
        "edges": [],
    }
    topology = CanonicalTopology(topology_payload)
    base_entities = [
        {
            "entity_ref": ref,
            "entity_name": ref.rsplit("|", 1)[-1],
            "layer": "SERVICE",
            "service_ancestor_or_none": ref,
            "parent_ref_or_none": None,
        }
        for ref in sorted({str(row["entity_ref"]) for row in evidence_rows})
    ]
    status = {
        cast(str, item.source).upper(): item.status.value
        for item in context.source_observations
    }
    base_context = {
        "schema_version": "strong-single-live.base-context.v1",
        "alert_title": "Service-level anomaly detected around T0.",
        "prompt_text": "Investigate the bounded telemetry, identify one visible root-cause entity and a concise fault type, and cite the evidence used.",
        "alert_entity_ref": None,
        "entities": base_entities,
        "evidence": evidence_rows,
        "source_status": status,
    }
    return _finalize_projection(
        topology=topology,
        observed=observed,
        metrics_ranking=tuple(
            ref
            for ref, _ in sorted(
                metrics_scores.items(), key=lambda item: (-item[1], item[0])
            )[:6]
        ),
        alert_entities=set(),
        trace_edges=trace_edges,
        base_context=base_context,
    )


__all__ = [
    "AliasDisposition",
    "AncestorProvenance",
    "CanonicalTopology",
    "EvidenceObservation",
    "EvidenceSource",
    "ProjectedEntity",
    "ProjectionCase",
    "ROOT_ELIGIBLE_LAYERS",
    "SOURCE_ORDER",
    "build_obss_projection",
    "build_rca100_projection",
    "discover_label_blind_obss_cases",
]
