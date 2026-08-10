"""Read-only adapters from frozen RCA100 and OB/SS artifacts to unified cases."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
import csv
from dataclasses import dataclass
from datetime import datetime
import math
import hashlib
import json
from pathlib import Path
import re

import pyarrow.parquet as pq  # type: ignore[import-untyped]

from ecomsre_rca100.entity import EntityCatalog, load_entity_catalog, normalize_entity_name
from ecomsre_rca100.evaluator import (
    RCA100GroundTruth,
    fault_correct,
    load_answer_key,
    prediction_correct,
)
from ecomsre_rca100.lifecycle import RCA100Schedule
from ecomsre_rca100.projection import (
    RCA100AgentTask,
    build_agent_context,
)
from ecomsre_rca100.runner import (
    RCA100TerminalRecord,
    RCA100TerminalStatus,
)
from ecomsre_rcaeval.adapter import ArchitectureContextBuilder
from ecomsre_rcaeval.contracts import Architecture
from ecomsre_rcaeval.dataset import DevCase, DevSystem, discover_dev_cases
from ecomsre_rcaeval.scoring import normalize_indicator
from ecomsre_rcaeval_v2.adapter import dev_case_to_telemetry_case
from ecomsre_rcaeval_v2.indicator import FormulaId, load_indicator_config
from ecomsre_rcaeval_v2.indicator_evaluation import build_runtime_metric_candidates
from ecomsre_rca_unified.analysis import UnifiedMetricCandidate, UnifiedRCACase
from ecomsre_rca_unified.contracts import (
    CanonicalEntityLayer,
    EvidenceVisibilitySummary,
    EntityHierarchyPath,
    FaultOntologyClass,
    PropagationDisposition,
)
from ecomsre_rca_unified.hierarchy import (
    EntityHierarchy,
    EntityNode,
    normalize_entity_layer,
)
from ecomsre_rca_unified.propagation import (
    EvidenceGraph,
    first_marked_log_anomaly,
    first_metric_anomaly,
    first_trace_anomaly,
)


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


def classify_fault_ontology(value: str) -> FaultOntologyClass:
    text = value.casefold()
    tokens = set(re.findall(r"[a-z0-9]+", text))
    for ontology, markers in _FAULT_MARKERS:
        if any(
            marker in tokens if len(marker) <= 3 else marker in text
            for marker in markers
        ):
            return ontology
    if any(marker in text for marker in ("propagat", "upstream", "symptom")):
        return FaultOntologyClass.PROPAGATION
    return FaultOntologyClass.UNKNOWN


def metric_family(value: str) -> str:
    text = value.casefold()
    if "cpu" in text:
        return "CPU"
    if "memory" in text or re.search(r"(?:^|[_\-.])mem(?:$|[_\-.])", text):
        return "MEMORY"
    if "disk" in text or "filesystem" in text or "i/o" in text:
        return "DISK"
    if "node" in text and any(item in text for item in ("load", "resource")):
        return "LOCAL_NODE_RESOURCE"
    if any(item in text for item in ("latency", "network", "socket", "packet", "loss")):
        return "NETWORK"
    if any(item in text for item in ("error", "exception", "fail")):
        return "APPLICATION"
    return "UNKNOWN"


@dataclass(frozen=True, slots=True)
class RCATopology:
    nodes: tuple[EntityNode, ...]
    parent_edges: tuple[tuple[str, str], ...]
    same_as_edges: tuple[tuple[str, str], ...]
    directed_edges: tuple[tuple[str, str], ...]
    undirected_edges: tuple[tuple[str, str], ...]


@dataclass(frozen=True, slots=True)
class AdaptedCase:
    unified: UnifiedRCACase
    hierarchy_record: Mapping[str, object]
    propagation_record: Mapping[str, object]
    visibility_record: Mapping[str, object]


def _entity_ref(entity_type: str, entity_id: str) -> str:
    return f"{entity_type.split('.', 1)[0]}|{entity_type}|{entity_id}"


def read_rca_topology(path: Path) -> RCATopology:
    if path.is_symlink() or not path.is_file():
        raise ValueError("RCA topology must be a regular file")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or not isinstance(value.get("entities"), list):
        raise ValueError("RCA topology schema is invalid")
    by_id: dict[str, tuple[str, str]] = {}
    nodes: list[EntityNode] = []
    for item in value["entities"]:
        if not isinstance(item, dict):
            raise ValueError("RCA topology entity must be an object")
        entity_id = item.get("id")
        entity_type = item.get("type")
        name = item.get("name")
        if not all(isinstance(part, str) and part for part in (entity_id, entity_type, name)):
            raise ValueError("RCA topology entity identity is invalid")
        assert isinstance(entity_id, str)
        assert isinstance(entity_type, str)
        assert isinstance(name, str)
        if entity_id in by_id:
            raise ValueError("RCA topology contains duplicate entity IDs")
        reference = _entity_ref(entity_type, entity_id)
        by_id[entity_id] = (reference, entity_type)
        nodes.append(
            EntityNode(
                entity_ref=reference,
                layer=normalize_entity_layer(entity_type),
                normalized_name=" ".join(name.strip().casefold().split()),
            )
        )
    parent: set[tuple[str, str]] = set()
    same_as: set[tuple[str, str]] = set()
    directed: set[tuple[str, str]] = set()
    undirected: set[tuple[str, str]] = set()
    edges = value.get("edges", [])
    if not isinstance(edges, list):
        raise ValueError("RCA topology edges must be a list")
    for item in edges:
        if not isinstance(item, dict):
            raise ValueError("RCA topology edge must be an object")
        src = item.get("src")
        dst = item.get("dst")
        relation = item.get("relation")
        if not isinstance(src, str) or not isinstance(dst, str) or src not in by_id or dst not in by_id:
            raise ValueError("RCA topology edge identity is invalid")
        src_ref = by_id[src][0]
        dst_ref = by_id[dst][0]
        if relation == "contains":
            parent.add((dst_ref, src_ref))
        elif relation == "same_as":
            same_as.add((min(src_ref, dst_ref), max(src_ref, dst_ref)))
        elif relation == "calls":
            # Frozen causal authority: dependency/callee dst can cause caller src.
            directed.add((dst_ref, src_ref))
        elif relation == "hosts":
            undirected.add((min(src_ref, dst_ref), max(src_ref, dst_ref)))
    return RCATopology(
        nodes=tuple(sorted(nodes, key=lambda item: item.entity_ref)),
        parent_edges=tuple(sorted(parent)),
        same_as_edges=tuple(sorted(same_as)),
        directed_edges=tuple(sorted(directed)),
        undirected_edges=tuple(sorted(undirected)),
    )


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


def _service_ancestor(
    entity: str, hierarchy: EntityHierarchy
) -> str | None:
    if entity not in hierarchy.nodes:
        return None
    if hierarchy.nodes[entity].layer is CanonicalEntityLayer.SERVICE:
        return entity
    return hierarchy.service_ancestor(entity)


def _layer_ancestor(
    entity: str,
    layer: CanonicalEntityLayer,
    hierarchy: EntityHierarchy,
) -> str | None:
    if entity not in hierarchy.nodes:
        return None
    return next(
        (
            item
            for item in hierarchy.parent_chain(entity)
            if hierarchy.nodes[item].layer is layer
        ),
        None,
    )


def _truth_entity_ref(
    truth: RCA100GroundTruth, catalog: EntityCatalog
) -> str:
    if truth.target_entity_ids:
        entity = catalog.by_id.get(truth.target_entity_ids[0])
        if entity is not None:
            return entity.entity_ref
    names = {normalize_entity_name(item) for item in truth.target_entity_names}
    matches = sorted(
        item.entity_ref
        for item in catalog.by_ref.values()
        if item.normalized_name in names
    )
    return matches[0] if len(matches) == 1 else "__GROUND_TRUTH_UNRESOLVED__"


def _truth_equivalent_refs(
    truth: RCA100GroundTruth, catalog: EntityCatalog
) -> frozenset[str]:
    return frozenset(
        entity.entity_ref
        for entity in catalog.by_ref.values()
        if prediction_correct(entity.entity_ref, truth, catalog)
    )


def _resolved_event_entity(
    raw: Mapping[str, object], catalog: EntityCatalog
) -> tuple[str | None, float | None]:
    payload_raw = raw.get("eventId")
    if not isinstance(payload_raw, str):
        return None, None
    try:
        payload = json.loads(payload_raw)
    except json.JSONDecodeError:
        return None, None
    if not isinstance(payload, Mapping):
        return None, None
    involved = payload.get("involvedObject")
    entity = None
    if isinstance(involved, Mapping):
        uid = str(involved.get("uid") or "")
        name = str(involved.get("name") or "")
        kind = str(involved.get("kind") or "").casefold()
        entity = catalog.by_id.get(uid)
        if entity is None and name:
            entity_type = {
                "pod": "k8s.pod",
                "node": "k8s.node",
                "deployment": "k8s.deployment",
            }.get(kind)
            if entity_type is not None:
                matches = catalog.by_type_name.get(
                    (entity_type, normalize_entity_name(name)), ()
                )
                entity = matches[0] if len(matches) == 1 else None
    timestamps = tuple(
        value
        for value in (
            _parse_timestamp(payload.get("eventTime")),
            _parse_timestamp(payload.get("firstTimestamp")),
            _parse_timestamp(payload.get("lastTimestamp")),
        )
        if value is not None
    )
    return (
        None if entity is None else entity.entity_ref,
        min(timestamps) if timestamps else None,
    )


def _resolved_alert_entity(
    raw: Mapping[str, object], catalog: EntityCatalog
) -> tuple[str | None, float | None]:
    resource_raw = raw.get("resource")
    entity = None
    if isinstance(resource_raw, str):
        try:
            resource = json.loads(resource_raw)
        except json.JSONDecodeError:
            resource = None
        if isinstance(resource, Mapping) and isinstance(resource.get("entity"), Mapping):
            value = resource["entity"]
            assert isinstance(value, Mapping)
            entity = catalog.resolve_exact(
                entity_id=str(value.get("entity_id") or "") or None,
                entity_type=str(value.get("entity_type") or "") or None,
                entity_name=None,
            )
    timestamp = _parse_timestamp(raw.get("time_s")) or _parse_timestamp(raw.get("time"))
    return None if entity is None else entity.entity_ref, timestamp


@dataclass(frozen=True, slots=True)
class _RCAEvidenceScan:
    first_by_entity_source: Mapping[str, Mapping[str, float]]
    events_entities: frozenset[str]
    alerts_entities: frozenset[str]
    trace_directed_edges: tuple[tuple[str, str], ...]


def _scan_rca_evidence(
    case_root: Path,
    *,
    task: RCA100AgentTask,
    catalog: EntityCatalog,
    methodology: Mapping[str, object],
) -> _RCAEvidenceScan:
    first: dict[str, dict[str, float]] = defaultdict(dict)
    anomaly = methodology["first_anomaly"]
    assert isinstance(anomaly, Mapping)
    metric_config = anomaly["metrics"]
    assert isinstance(metric_config, Mapping)
    metrics_series: dict[tuple[str, str, str, str], list[tuple[float, float]]] = defaultdict(list)
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
        for raw in batch.to_pylist():
            timestamp_raw = raw.get("time")
            value_raw = raw.get("value")
            if type(timestamp_raw) is not int or not isinstance(value_raw, (int, float)):
                continue
            timestamp = timestamp_raw / 1_000_000.0
            if not task.window_start_timestamp <= timestamp <= task.window_end_timestamp:
                continue
            entity = catalog.resolve_metric_entity(
                entity_id=str(raw.get("entity_id") or ""),
                entity_set=str(raw.get("entity_set") or ""),
                entity_name=str(raw.get("entity_name") or ""),
                service=str(raw.get("service") or ""),
            )
            value = float(value_raw)
            if entity is None or not math.isfinite(value):
                continue
            key = (
                entity.entity_ref,
                str(raw.get("metric") or ""),
                str(raw.get("metric_set_id") or ""),
                str(raw.get("service") or ""),
            )
            metrics_series[key].append((timestamp, value))
    for key, metric_samples in metrics_series.items():
        metric_timestamp = first_metric_anomaly(
            samples=metric_samples,
            anchor=task.anchor_timestamp,
            minimum_pre_samples=int(metric_config["minimum_pre_samples"]),
            minimum_post_samples=int(metric_config["minimum_post_samples"]),
            mad_multiplier=float(metric_config["mad_multiplier"]),
            relative_floor=float(metric_config["absolute_relative_floor"]),
            epsilon=float(metric_config["zero_epsilon"]),
        )
        if metric_timestamp is not None:
            current = first[key[0]].get("metrics")
            first[key[0]]["metrics"] = (
                metric_timestamp
                if current is None
                else min(current, metric_timestamp)
            )

    log_config = anomaly["logs"]
    assert isinstance(log_config, Mapping)
    raw_log_markers = log_config["content_markers"]
    if not isinstance(raw_log_markers, Sequence):
        raise ValueError("frozen log markers must be a sequence")
    log_markers = tuple(str(item) for item in raw_log_markers)
    marked_logs: dict[str, list[tuple[float, str]]] = defaultdict(list)
    logs = pq.ParquetFile(case_root / "logs.parquet")
    log_columns = ["content", "_time_", "_pod_uid_", "_pod_name_", "_container_name_"]
    for batch in logs.iter_batches(batch_size=65_536, columns=log_columns):
        for raw in batch.to_pylist():
            log_timestamp = _parse_timestamp(raw.get("_time_"))
            if log_timestamp is None or log_timestamp < task.anchor_timestamp or log_timestamp > task.window_end_timestamp:
                continue
            content = str(raw.get("content") or "")
            if not any(marker in content.casefold() for marker in log_markers):
                continue
            entity = catalog.resolve_log_entity(
                pod_uid=str(raw.get("_pod_uid_") or ""),
                pod_name=str(raw.get("_pod_name_") or ""),
                container_name=str(raw.get("_container_name_") or ""),
            )
            if entity is not None:
                marked_logs[entity.entity_ref].append((log_timestamp, content))
    for log_entity_ref, log_samples in marked_logs.items():
        first_log_timestamp = first_marked_log_anomaly(
            samples=log_samples,
            anchor=task.anchor_timestamp,
            markers=log_markers,
        )
        if first_log_timestamp is not None:
            first[log_entity_ref]["logs"] = first_log_timestamp

    trace_config = anomaly["traces"]
    assert isinstance(trace_config, Mapping)
    trace_samples: dict[str, list[tuple[float, float, bool]]] = defaultdict(list)
    span_service: dict[tuple[str, str], str] = {}
    span_parents: list[tuple[tuple[str, str], tuple[str, str]]] = []
    traces = pq.ParquetFile(case_root / "traces.parquet")
    trace_columns = [
        "traceId",
        "spanId",
        "parentSpanId",
        "startTime",
        "duration",
        "serviceName",
        "statusCode",
    ]
    for batch in traces.iter_batches(batch_size=65_536, columns=trace_columns):
        for raw in batch.to_pylist():
            trace_timestamp = _parse_timestamp(raw.get("startTime"))
            if trace_timestamp is None or not task.window_start_timestamp <= trace_timestamp <= task.window_end_timestamp:
                continue
            entity = catalog.resolve_trace_entity(service_name=str(raw.get("serviceName") or ""))
            if entity is None:
                continue
            try:
                duration = float(str(raw.get("duration") or "nan"))
            except ValueError:
                continue
            status = str(raw.get("statusCode") or "").casefold()
            failed = status not in {"", "0", "1", "false", "ok", "unset"}
            trace_samples[entity.entity_ref].append((trace_timestamp, duration, failed))
            trace_id = str(raw.get("traceId") or "")
            span_id = str(raw.get("spanId") or "")
            parent_id = str(raw.get("parentSpanId") or "")
            if trace_id and span_id:
                span_service[(trace_id, span_id)] = entity.entity_ref
                if parent_id:
                    span_parents.append(((trace_id, span_id), (trace_id, parent_id)))
    for trace_entity_ref, entity_trace_samples in trace_samples.items():
        first_trace_timestamp = first_trace_anomaly(
            samples=entity_trace_samples,
            anchor=task.anchor_timestamp,
            minimum_pre_samples=int(trace_config["minimum_pre_samples_for_slow"]),
            slow_multiplier=float(trace_config["slow_multiplier"]),
        )
        if first_trace_timestamp is not None:
            first[trace_entity_ref]["traces"] = first_trace_timestamp
    trace_edges: set[tuple[str, str]] = set()
    for child_key, parent_key in span_parents:
        child = span_service.get(child_key)
        parent = span_service.get(parent_key)
        if child is not None and parent is not None and child != parent:
            trace_edges.add((child, parent))

    events_entities: set[str] = set()
    events = pq.ParquetFile(case_root / "events.parquet")
    for batch in events.iter_batches(batch_size=16_384):
        for raw in batch.to_pylist():
            event_entity_ref, event_timestamp = _resolved_event_entity(raw, catalog)
            if event_entity_ref is None:
                continue
            events_entities.add(event_entity_ref)
            if event_timestamp is not None and event_timestamp >= task.anchor_timestamp:
                current = first[event_entity_ref].get("events")
                first[event_entity_ref]["events"] = (
                    event_timestamp
                    if current is None
                    else min(current, event_timestamp)
                )

    alerts_entities: set[str] = set()
    alerts = pq.ParquetFile(case_root / "alerts.parquet")
    for batch in alerts.iter_batches(batch_size=16_384):
        for raw in batch.to_pylist():
            alert_entity_ref, alert_timestamp = _resolved_alert_entity(raw, catalog)
            if alert_entity_ref is None:
                continue
            alerts_entities.add(alert_entity_ref)
            if alert_timestamp is not None and alert_timestamp >= task.anchor_timestamp:
                current = first[alert_entity_ref].get("alerts")
                first[alert_entity_ref]["alerts"] = (
                    alert_timestamp
                    if current is None
                    else min(current, alert_timestamp)
                )
    return _RCAEvidenceScan(
        first_by_entity_source={key: dict(value) for key, value in first.items()},
        events_entities=frozenset(events_entities),
        alerts_entities=frozenset(alerts_entities),
        trace_directed_edges=tuple(sorted(trace_edges)),
    )


def _relation_role(
    entity: str,
    truth: str,
    *,
    alert_entity: str | None,
    first_times: Mapping[str, Mapping[str, float]],
    graph: EvidenceGraph,
) -> str:
    if entity == truth:
        truth_sources = first_times.get(truth, {})
        truth_time = min(truth_sources.values()) if truth_sources else None
        all_times = [
            min(source_times.values())
            for source_times in first_times.values()
            if source_times
        ]
        if (
            truth_time is not None
            and all_times
            and truth_time <= min(all_times)
        ):
            return "ROOT_EARLIEST_ANOMALY"
        return "NO_TEMPORAL_SIGNAL"
    relation = graph.relation(entity, truth)
    if relation == "UPSTREAM":
        return "UPSTREAM_OF_ROOT"
    if relation == "DOWNSTREAM":
        return "DOWNSTREAM_SYMPTOM"
    if relation == "LATERAL":
        return "LATERAL_SYMPTOM"
    if alert_entity is not None and entity == alert_entity:
        return "ALERT_TARGET_ONLY"
    if entity not in first_times:
        return "NO_TEMPORAL_SIGNAL"
    return "NO_GRAPH_PATH"


def classify_propagation_role(
    entity: str,
    truth: str,
    *,
    alert_entity: str | None,
    first_times: Mapping[str, Mapping[str, float]],
    graph: EvidenceGraph,
) -> str:
    """Public deterministic role classifier used by analysis and tests."""

    return _relation_role(
        entity,
        truth,
        alert_entity=alert_entity,
        first_times=first_times,
        graph=graph,
    )


def load_rca_schedule(path: Path) -> RCA100Schedule:
    if path.is_symlink() or not path.is_file():
        raise ValueError("RCA100 schedule must be a regular file")
    return RCA100Schedule.model_validate_json(path.read_text(encoding="utf-8"))


def load_rca_terminal(path: Path) -> RCA100TerminalRecord:
    if path.is_symlink() or not path.is_file():
        raise ValueError("RCA100 terminal must be a regular file")
    return RCA100TerminalRecord.model_validate_json(
        path.read_text(encoding="utf-8")
    )


def load_rca100_cases(
    *,
    cases_root: Path,
    terminals_root: Path,
    schedule_path: Path,
    answer_root: Path,
    methodology: Mapping[str, object],
    progress: Callable[[str, int, int], None] | None = None,
) -> tuple[AdaptedCase, ...]:
    schedule = load_rca_schedule(schedule_path)
    truths = load_answer_key(answer_root)
    output: list[AdaptedCase] = []
    for record in schedule.records:
        case_root = cases_root / record.source_task_id
        catalog = load_entity_catalog(case_root / "topology.json")
        topology = read_rca_topology(case_root / "topology.json")
        context = build_agent_context(case_root, opaque_case_id=record.opaque_case_id)
        scan = _scan_rca_evidence(
            case_root,
            task=context.task,
            catalog=catalog,
            methodology=methodology,
        )
        parent_edges = set(topology.parent_edges)
        for entity in catalog.by_ref.values():
            if entity.parent_service_ref_or_none is not None:
                parent_edges.add((entity.entity_ref, entity.parent_service_ref_or_none))
        directed_edges = set(topology.directed_edges) | set(scan.trace_directed_edges)
        hierarchy = EntityHierarchy(
            nodes=topology.nodes,
            parent_edges=tuple(sorted(parent_edges)),
            same_as_edges=topology.same_as_edges,
            directed_edges=tuple(sorted(directed_edges)),
            undirected_edges=topology.undirected_edges,
        )
        graph = EvidenceGraph(
            nodes=frozenset(hierarchy.nodes),
            directed_edges=tuple(sorted(directed_edges)),
            undirected_edges=topology.undirected_edges,
        )
        truth = truths[record.source_task_id]
        truth_ref = _truth_entity_ref(truth, catalog)
        truth_equivalent_refs = _truth_equivalent_refs(truth, catalog)
        if truth_ref == "__GROUND_TRUTH_UNRESOLVED__":
            truth_equivalent_refs = frozenset({truth_ref})
        truth_layer = (
            CanonicalEntityLayer.UNKNOWN
            if truth_ref not in hierarchy.nodes
            else hierarchy.nodes[truth_ref].layer
        )
        truth_service = _service_ancestor(truth_ref, hierarchy)
        truth_workload = _layer_ancestor(
            truth_ref, CanonicalEntityLayer.WORKLOAD, hierarchy
        )
        truth_node = _layer_ancestor(
            truth_ref, CanonicalEntityLayer.NODE, hierarchy
        )
        terminal = load_rca_terminal(
            terminals_root / f"{record.opaque_case_id}.json"
        )
        terminal_failure = terminal.status is not RCA100TerminalStatus.COMPLETED
        initial_ref = terminal.initial_root_entity_ref or "__TERMINAL_FAILURE__"
        final_ref = terminal.final_root_entity_ref or "__TERMINAL_FAILURE__"
        initial_layer = (
            CanonicalEntityLayer.UNKNOWN
            if initial_ref not in hierarchy.nodes
            else hierarchy.nodes[initial_ref].layer
        )
        final_layer = (
            CanonicalEntityLayer.UNKNOWN
            if final_ref not in hierarchy.nodes
            else hierarchy.nodes[final_ref].layer
        )
        initial_service = _service_ancestor(initial_ref, hierarchy)
        final_service = _service_ancestor(final_ref, hierarchy)
        initial_exact = prediction_correct(
            terminal.initial_root_entity_ref, truth, catalog
        )
        final_exact = prediction_correct(
            terminal.final_root_entity_ref, truth, catalog
        )
        initial_fault = terminal.initial_fault_type or "__TERMINAL_FAILURE__"
        initial_pair = initial_exact and fault_correct(
            terminal.initial_fault_type, truth
        )
        final_pair = final_exact and fault_correct(terminal.final_fault_type, truth)
        symptom = context.task.alert_entity_ref or initial_ref
        metrics: list[UnifiedMetricCandidate] = []
        evidence_by_entity = {
            item.entity_ref: item.metric for item in context.metrics.evidence
        }
        for ranked in context.metrics.ranking:
            entity_ref = ranked.entity_ref
            source_times = scan.first_by_entity_source.get(entity_ref, {})
            first_time = min(source_times.values()) if source_times else None
            relation = graph.relation(entity_ref, symptom)
            metrics.append(
                UnifiedMetricCandidate(
                    entity=entity_ref,
                    service_ancestor=_service_ancestor(entity_ref, hierarchy),
                    layer=hierarchy.nodes[entity_ref].layer,
                    rank=ranked.rank,
                    score=ranked.score,
                    metric_family=metric_family(evidence_by_entity.get(entity_ref, "")),
                    first_anomaly_time=first_time,
                    source_support=len(source_times),
                    relation_to_symptom=relation,
                )
            )
        top1 = metrics[0] if metrics else None
        symptom_times = scan.first_by_entity_source.get(symptom, {})
        symptom_time = min(symptom_times.values()) if symptom_times else None
        upstream_signal = any(
            item.relation_to_symptom == "UPSTREAM"
            and item.first_anomaly_time is not None
            and symptom_time is not None
            and item.first_anomaly_time <= symptom_time
            for item in metrics
        )
        if upstream_signal:
            propagation = PropagationDisposition.PRESENT
        elif directed_edges and symptom_time is not None and any(
            item.first_anomaly_time is not None for item in metrics
        ):
            propagation = PropagationDisposition.ABSENT
        else:
            propagation = PropagationDisposition.UNAVAILABLE
        metrics_visible = frozenset(
            item.entity_ref for item in context.metrics.evidence
        )
        logs_visible = frozenset(item.entity_ref for item in context.logs.evidence)
        traces_visible = frozenset(item.entity_ref for item in context.traces.evidence)
        visibility = EvidenceVisibilitySummary(
            catalog_entities=frozenset(hierarchy.nodes),
            metrics_entities=metrics_visible,
            logs_entities=logs_visible,
            traces_entities=traces_visible,
            events_entities=scan.events_entities,
            alerts_entities=scan.alerts_entities,
            topology_entities=frozenset(hierarchy.nodes),
        )
        causal_visible = frozenset(
            item.entity
            for item in metrics
            if item.first_anomaly_time is not None
            and item.relation_to_symptom in {"ROOT", "UPSTREAM"}
        )
        top1_downstream = bool(
            top1 is not None
            and (
                hierarchy.relation(top1.entity, initial_ref)
                == "PREDICTED_DESCENDANT"
                or graph.relation(top1.entity, initial_ref) == "DOWNSTREAM"
            )
        )
        raw_fault_truth = truth.fault_types[0]
        unified = UnifiedRCACase(
            private_case_key=f"RCA100:{record.opaque_case_id}",
            fixture="RCA100",
            benchmark="RCA100",
            system="RCA100",
            fault_family=truth.canonical_case_id.split("-", 1)[0],
            fault_type_truth=raw_fault_truth,
            fault_type_raw=initial_fault,
            fault_regime=classify_fault_ontology(initial_fault),
            ground_truth_fault_regime=classify_fault_ontology(raw_fault_truth),
            metric_family="UNKNOWN" if top1 is None else top1.metric_family,
            ground_truth_entity=truth_ref,
            ground_truth_equivalent_entities=truth_equivalent_refs,
            ground_truth_layer=truth_layer,
            ground_truth_service=truth_service,
            ground_truth_workload=truth_workload,
            ground_truth_node=truth_node,
            initial_entity=initial_ref,
            initial_layer=initial_layer,
            initial_hierarchy_path=EntityHierarchyPath(
                entity=initial_ref,
                explicit_parents=(
                    ()
                    if initial_ref not in hierarchy.nodes
                    else hierarchy.parent_chain(initial_ref)
                ),
                service_ancestor_or_none=initial_service,
                infrastructure_ancestor_or_none=(
                    _layer_ancestor(
                        initial_ref, CanonicalEntityLayer.INFRASTRUCTURE, hierarchy
                    )
                    or _layer_ancestor(
                        initial_ref, CanonicalEntityLayer.CLUSTER, hierarchy
                    )
                ),
            ),
            initial_supporting_evidence_refs=terminal.initial_evidence_refs,
            initial_service=initial_service,
            initial_correct_exact=initial_exact,
            initial_correct_service=bool(
                truth_service is not None and initial_service == truth_service
            ),
            initial_pair_correct=initial_pair,
            initial_relation=hierarchy.relation(initial_ref, truth_ref),
            m3_action=None if terminal.m3_action is None else terminal.m3_action.value,
            m3_final_entity=final_ref,
            m3_final_layer=final_layer,
            m3_final_service=final_service,
            m3_correct_exact=final_exact,
            m3_correct_service=bool(
                truth_service is not None and final_service == truth_service
            ),
            m3_pair_correct=final_pair,
            m3_relation=hierarchy.relation(final_ref, truth_ref),
            metrics_candidates=tuple(metrics),
            metrics_initial_rank=terminal.initial_metrics_rank_or_none,
            metrics_margin=terminal.normalized_margin,
            metrics_top1_is_downstream=top1_downstream,
            propagation_disposition=propagation,
            visibility=visibility,
            causal_visible_entities=causal_visible,
            alert_entity=context.task.alert_entity_ref,
            terminal_failure=terminal_failure,
        )
        hierarchy_record = {
            "schema_version": "rca-crossbenchmark.entity-hierarchy-by-case.v1",
            "private_case_key": unified.private_case_key,
            "ground_truth_path": (
                []
                if truth_ref not in hierarchy.nodes
                else list(hierarchy.parent_chain(truth_ref))
            ),
            "initial_relation": unified.initial_relation,
            "m3_relation": unified.m3_relation,
            "metrics_top1_relation": (
                "UNRESOLVED"
                if top1 is None
                else hierarchy.relation(top1.entity, truth_ref)
            ),
            "initial_same_workload": bool(
                truth_workload is not None
                and _layer_ancestor(
                    initial_ref, CanonicalEntityLayer.WORKLOAD, hierarchy
                )
                == truth_workload
            ),
            "m3_same_workload": bool(
                truth_workload is not None
                and _layer_ancestor(
                    final_ref, CanonicalEntityLayer.WORKLOAD, hierarchy
                )
                == truth_workload
            ),
            "metrics_top1_same_workload": bool(
                top1 is not None
                and truth_workload is not None
                and _layer_ancestor(
                    top1.entity, CanonicalEntityLayer.WORKLOAD, hierarchy
                )
                == truth_workload
            ),
            "initial_same_node": hierarchy.same_node(initial_ref, truth_ref),
            "m3_same_node": hierarchy.same_node(final_ref, truth_ref),
            "metrics_top1_same_node": bool(
                top1 is not None and hierarchy.same_node(top1.entity, truth_ref)
            ),
            "initial_same_component": hierarchy.relation(initial_ref, truth_ref)
            not in {"UNRELATED", "UNRESOLVED"},
            "m3_same_component": hierarchy.relation(final_ref, truth_ref)
            not in {"UNRELATED", "UNRESOLVED"},
            "metrics_top1_same_component": bool(
                top1 is not None
                and hierarchy.relation(top1.entity, truth_ref)
                not in {"UNRELATED", "UNRESOLVED"}
            ),
            "topology_node_count": len(hierarchy.nodes),
            "directed_edge_count": len(directed_edges),
        }
        propagation_record = {
            "schema_version": "rca-crossbenchmark.propagation-role-by-case.v2",
            "private_case_key": unified.private_case_key,
            "initial_role": _relation_role(
                initial_ref,
                truth_ref,
                alert_entity=context.task.alert_entity_ref,
                first_times=scan.first_by_entity_source,
                graph=graph,
            ),
            "m3_role": _relation_role(
                final_ref,
                truth_ref,
                alert_entity=context.task.alert_entity_ref,
                first_times=scan.first_by_entity_source,
                graph=graph,
            ),
            "metrics_top1_role": (
                "NO_TEMPORAL_SIGNAL"
                if top1 is None
                else _relation_role(
                    top1.entity,
                    truth_ref,
                    alert_entity=context.task.alert_entity_ref,
                    first_times=scan.first_by_entity_source,
                    graph=graph,
                )
            ),
            "metrics_candidate_roles": [
                {
                    "entity": candidate.entity,
                    "rank": candidate.rank,
                    "role": _relation_role(
                        candidate.entity,
                        truth_ref,
                        alert_entity=context.task.alert_entity_ref,
                        first_times=scan.first_by_entity_source,
                        graph=graph,
                    ),
                    "ground_truth_to_candidate_hops": graph.directed_distance(
                        truth_ref, candidate.entity
                    ),
                }
                for candidate in metrics
            ],
            "ground_truth_to_initial_hops": graph.directed_distance(
                truth_ref, initial_ref
            ),
            "ground_truth_to_metrics_top1_hops": (
                None
                if top1 is None
                else graph.directed_distance(truth_ref, top1.entity)
            ),
            "first_anomaly_times": {
                entity: dict(values)
                for entity, values in scan.first_by_entity_source.items()
            },
            "ground_truth_first_anomaly": (
                min(scan.first_by_entity_source[truth_ref].values())
                if scan.first_by_entity_source.get(truth_ref)
                else None
            ),
            "initial_first_anomaly": (
                min(scan.first_by_entity_source[initial_ref].values())
                if scan.first_by_entity_source.get(initial_ref)
                else None
            ),
            "metrics_top1_first_anomaly": (
                None if top1 is None else top1.first_anomaly_time
            ),
            "metrics_top1_later_than_ground_truth": bool(
                top1 is not None
                and top1.first_anomaly_time is not None
                and scan.first_by_entity_source.get(truth_ref)
                and top1.first_anomaly_time
                > min(scan.first_by_entity_source[truth_ref].values())
            ),
            "metrics_top1_in_degree": (
                0
                if top1 is None
                else sum(dst == top1.entity for _, dst in directed_edges)
            ),
            "metrics_top1_high_fan_in": bool(
                top1 is not None
                and sum(dst == top1.entity for _, dst in directed_edges) >= 2
            ),
            "traffic_volume_available": False,
            "propagation_disposition": propagation.value,
        }
        source_entities = {
            "catalog": visibility.catalog_entities,
            "metrics": visibility.metrics_entities,
            "logs": visibility.logs_entities,
            "traces": visibility.traces_entities,
            "events": visibility.events_entities,
            "alerts": visibility.alerts_entities,
            "topology": visibility.topology_entities,
            "any_model_visible": visibility.any_model_visible,
            "causal": causal_visible,
        }
        ground_truth_visible = {
            source: truth_ref in entities
            for source, entities in source_entities.items()
        }
        initial_visible = {
            source: initial_ref in entities
            for source, entities in source_entities.items()
        }
        metrics_top1_visible = {
            source: bool(top1 is not None and top1.entity in entities)
            for source, entities in source_entities.items()
        }
        visibility_record = {
            "schema_version": "rca-crossbenchmark.evidence-visibility-by-case.v2",
            "private_case_key": unified.private_case_key,
            "ground_truth_visible": ground_truth_visible,
            "ground_truth_service_visible": {
                source: bool(
                    truth_service is not None
                    and any(
                        _service_ancestor(entity, hierarchy) == truth_service
                        for entity in entities
                    )
                )
                for source, entities in source_entities.items()
            },
            "initial_visible": initial_visible,
            "metrics_top1_visible": metrics_top1_visible,
            "initial_and_ground_truth_co_visible": {
                source: initial_visible[source] and ground_truth_visible[source]
                for source in source_entities
            },
            "metrics_top1_and_ground_truth_co_visible": {
                source: metrics_top1_visible[source]
                and ground_truth_visible[source]
                for source in source_entities
            },
        }
        output.append(
            AdaptedCase(
                unified=unified,
                hierarchy_record=hierarchy_record,
                propagation_record=propagation_record,
                visibility_record=visibility_record,
            )
        )
        if progress is not None:
            progress("RCA100", len(output), 103)
    if len(output) != 103:
        raise ValueError("RCA100 unified adapter denominator differs")
    return tuple(output)


@dataclass(frozen=True, slots=True)
class _OBSSProjection:
    candidates: tuple[UnifiedMetricCandidate, ...]
    visibility: EvidenceVisibilitySummary
    first_by_entity_source: Mapping[str, Mapping[str, float]]
    directed_edges: tuple[tuple[str, str], ...]
    propagation_available: bool


def _case_identity_digest(case: DevCase) -> str:
    payload = b"\0".join(
        value.encode("utf-8")
        for value in (
            case.system,
            case.root_cause_service,
            case.fault,
            case.instance,
        )
    )
    return hashlib.sha256(payload).hexdigest()


def _normalized_margin(candidates: Sequence[UnifiedMetricCandidate]) -> float | None:
    if not candidates:
        return None
    if len(candidates) == 1:
        return 1.0
    return max(
        0.0,
        (candidates[0].score - candidates[1].score)
        / max(abs(candidates[0].score), 1e-12),
    )


def _csv_rows(path: Path) -> tuple[dict[str, str], ...]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise ValueError("OB/SS source requires a CSV header")
        return tuple(dict(row) for row in reader)


def _build_obss_projection(
    case: DevCase,
    *,
    indicator_config: object,
    methodology: Mapping[str, object],
) -> _OBSSProjection:
    telemetry = dev_case_to_telemetry_case(case)
    builder = ArchitectureContextBuilder(
        telemetry,
        Architecture.SINGLE,
        run_id=hashlib.sha256(case.case_id.encode()).hexdigest()[:32],
    )
    for source in ("metrics", "logs", "traces"):
        builder.query_source(source)  # type: ignore[arg-type]
    context = builder.snapshot()
    raw_candidates = build_runtime_metric_candidates(
        telemetry,
        case_identity_sha256=_case_identity_digest(case),
        formula=FormulaId.F0,
        config=indicator_config,  # type: ignore[arg-type]
    )
    all_metric_services = {str(item.service) for item in raw_candidates}
    selected_raw = []
    seen_services: set[str] = set()
    for metric_item in raw_candidates:
        service = str(metric_item.service)
        if service in seen_services:
            continue
        seen_services.add(service)
        selected_raw.append(metric_item)
        if len(selected_raw) == 6:
            break
    anomaly = methodology["first_anomaly"]
    assert isinstance(anomaly, Mapping)
    metric_config = anomaly["metrics"]
    log_config = anomaly["logs"]
    trace_config = anomaly["traces"]
    assert isinstance(metric_config, Mapping)
    assert isinstance(log_config, Mapping)
    assert isinstance(trace_config, Mapping)
    first: dict[str, dict[str, float]] = defaultdict(dict)
    metric_rows = _csv_rows(case.metrics_path)
    for metric_item in selected_raw:
        samples: list[tuple[float, float]] = []
        for row in metric_rows:
            try:
                metric_timestamp = float(row.get("time", ""))
                value = float(row.get(metric_item.metric_name, ""))
            except ValueError:
                continue
            if case.inject_time - 600 <= metric_timestamp <= case.inject_time + 600:
                samples.append((metric_timestamp, value))
        first_metric_timestamp = first_metric_anomaly(
            samples=samples,
            anchor=float(case.inject_time),
            minimum_pre_samples=int(metric_config["minimum_pre_samples"]),
            minimum_post_samples=int(metric_config["minimum_post_samples"]),
            mad_multiplier=float(metric_config["mad_multiplier"]),
            relative_floor=float(metric_config["absolute_relative_floor"]),
            epsilon=float(metric_config["zero_epsilon"]),
        )
        if first_metric_timestamp is not None:
            first[str(metric_item.service)]["metrics"] = first_metric_timestamp

    raw_markers = log_config["content_markers"]
    if not isinstance(raw_markers, Sequence):
        raise ValueError("OB/SS log markers must be a sequence")
    markers = tuple(str(item) for item in raw_markers)
    logs_by_service: dict[str, list[tuple[float, str]]] = defaultdict(list)
    for row in _csv_rows(case.logs_path):
        log_timestamp = _parse_timestamp(row.get("timestamp"))
        if log_timestamp is None or not case.inject_time <= log_timestamp <= case.inject_time + 600:
            continue
        service = row.get("container_name", "").strip()
        content = " ".join(
            str(row.get(key, ""))
            for key in ("message", "error", "level", "log_template")
        )
        if service and any(marker in content.casefold() for marker in markers):
            logs_by_service[service].append((log_timestamp, content))
    for service, log_samples in logs_by_service.items():
        first_log_timestamp = first_marked_log_anomaly(
            samples=log_samples,
            anchor=float(case.inject_time),
            markers=markers,
        )
        if first_log_timestamp is not None:
            first[service]["logs"] = first_log_timestamp

    trace_edges: set[tuple[str, str]] = set()
    traces_by_service: dict[str, list[tuple[float, float, bool]]] = defaultdict(list)
    if case.traces_path is not None:
        span_service: dict[tuple[str, str], str] = {}
        parents: list[tuple[tuple[str, str], tuple[str, str]]] = []
        for row in _csv_rows(case.traces_path):
            trace_timestamp = _parse_timestamp(
                row.get("startTimeMillis") or row.get("startTime")
            )
            if trace_timestamp is None or not case.inject_time - 600 <= trace_timestamp <= case.inject_time + 600:
                continue
            service = (row.get("serviceName") or row.get("service") or "").strip()
            try:
                duration = float(row.get("duration", "nan"))
            except ValueError:
                continue
            status = (row.get("statusCode") or row.get("status") or "").casefold()
            failed = status not in {"", "0", "1", "false", "ok", "unset"}
            if service:
                traces_by_service[service].append((trace_timestamp, duration, failed))
            trace_id = row.get("traceID") or row.get("traceId") or ""
            span_id = row.get("spanID") or row.get("spanId") or ""
            parent_id = row.get("parentSpanID") or row.get("parentSpanId") or ""
            if trace_id and span_id and service:
                span_service[(trace_id, span_id)] = service
                if parent_id:
                    parents.append(((trace_id, span_id), (trace_id, parent_id)))
        for service, trace_samples in traces_by_service.items():
            first_trace_timestamp = first_trace_anomaly(
                samples=trace_samples,
                anchor=float(case.inject_time),
                minimum_pre_samples=int(
                    trace_config["minimum_pre_samples_for_slow"]
                ),
                slow_multiplier=float(trace_config["slow_multiplier"]),
            )
            if first_trace_timestamp is not None:
                first[service]["traces"] = first_trace_timestamp
        for child_key, parent_key in parents:
            child = span_service.get(child_key)
            parent = span_service.get(parent_key)
            if child and parent and child != parent:
                trace_edges.add((child, parent))

    model_source_entities: dict[str, set[str]] = {
        "metrics": set(),
        "logs": set(),
        "traces": set(),
    }
    for evidence_item in context.evidence:
        evidence_source = {
            "metric": "metrics",
            "log": "logs",
            "trace": "traces",
        }.get(evidence_item.evidence_id.partition(":")[0])
        if evidence_source is not None:
            model_source_entities[evidence_source].add(evidence_item.service)
    catalog_entities = (
        all_metric_services
        | set(model_source_entities["metrics"])
        | set(model_source_entities["logs"])
        | set(model_source_entities["traces"])
        | set(first)
    )
    graph = EvidenceGraph(
        nodes=frozenset(catalog_entities),
        directed_edges=tuple(sorted(trace_edges)),
    )
    candidates: list[UnifiedMetricCandidate] = []
    for rank, item in enumerate(selected_raw, 1):
        service = str(item.service)
        source_times = first.get(service, {})
        candidates.append(
            UnifiedMetricCandidate(
                entity=service,
                service_ancestor=service,
                layer=CanonicalEntityLayer.SERVICE,
                rank=rank,
                score=float(item.score),
                metric_family=metric_family(str(item.canonical_indicator)),
                first_anomaly_time=(
                    min(source_times.values()) if source_times else None
                ),
                source_support=len(source_times),
                relation_to_symptom=graph.relation(
                    service, case.root_cause_service
                ),
            )
        )
    return _OBSSProjection(
        candidates=tuple(candidates),
        visibility=EvidenceVisibilitySummary(
            catalog_entities=frozenset(catalog_entities),
            metrics_entities=frozenset(model_source_entities["metrics"]),
            logs_entities=frozenset(model_source_entities["logs"]),
            traces_entities=frozenset(model_source_entities["traces"]),
            events_entities=frozenset(),
            alerts_entities=frozenset(),
            topology_entities=frozenset(),
        ),
        first_by_entity_source={key: dict(value) for key, value in first.items()},
        directed_edges=tuple(sorted(trace_edges)),
        propagation_available=case.traces_path is not None,
    )


def _load_object(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("frozen terminal must be a JSON object")
    return value


def _terminal_inputs(
    fixture: str, root: Path
) -> tuple[tuple[Path, str], ...]:
    paths = tuple(sorted((root / "terminal-records").glob("*.json")))
    expected = {
        "candidate-3": 60,
        "candidate-4": 60,
        "candidate-5": 60,
        "pr21-tune": 60,
        "pr21-regression": 120,
    }[fixture]
    if len(paths) != expected:
        raise ValueError(f"{fixture} terminal denominator differs")
    return tuple((path, fixture) for path in paths)


def load_obss_cases(
    *,
    candidate_3_root: Path,
    candidate_4_root: Path,
    candidate_5_root: Path,
    tune_root: Path,
    regression_root: Path,
    ob_root: Path,
    ss_root: Path,
    indicator_config_path: Path,
    indicator_config_sha256: str,
    methodology: Mapping[str, object],
    progress: Callable[[str, int, int], None] | None = None,
) -> tuple[AdaptedCase, ...]:
    cases = discover_dev_cases(ob_root, DevSystem.RE2_OB) + discover_dev_cases(
        ss_root, DevSystem.RE2_SS
    )
    case_index = {item.case_id: item for item in cases}
    if len(case_index) != 180:
        raise ValueError("OB/SS case index must contain 180 unique cases")
    indicator_config = load_indicator_config(
        indicator_config_path,
        expected_sha256=indicator_config_sha256,
    )
    fixtures = (
        _terminal_inputs("candidate-3", candidate_3_root)
        + _terminal_inputs("candidate-4", candidate_4_root)
        + _terminal_inputs("candidate-5", candidate_5_root)
        + _terminal_inputs("pr21-tune", tune_root)
        + _terminal_inputs("pr21-regression", regression_root)
    )
    projections: dict[str, _OBSSProjection] = {}
    output: list[AdaptedCase] = []
    for path, fixture in fixtures:
        terminal = _load_object(path)
        case_id = terminal.get("case_id")
        if not isinstance(case_id, str) or case_id not in case_index:
            raise ValueError(f"{fixture} terminal case identity is invalid")
        case = case_index[case_id]
        if case_id not in projections:
            projections[case_id] = _build_obss_projection(
                case,
                indicator_config=indicator_config,
                methodology=methodology,
            )
        projection = projections[case_id]
        completed = terminal.get("status") == "COMPLETED"
        result = terminal.get("result")
        diagnosis = result.get("diagnosis") if isinstance(result, Mapping) else None
        initial_value = (
            diagnosis.get("initial_diagnosis")
            if isinstance(diagnosis, Mapping)
            else None
        )
        if completed and not isinstance(initial_value, Mapping):
            raise ValueError(f"{fixture} completed terminal lacks Initial")
        initial = (
            str(initial_value.get("root_cause_service"))
            if isinstance(initial_value, Mapping)
            else "__TERMINAL_FAILURE__"
        )
        initial_fault = (
            str(initial_value.get("root_cause_indicator"))
            if isinstance(initial_value, Mapping)
            else "__TERMINAL_FAILURE__"
        )
        candidates = projection.candidates
        initial_rank = next(
            (item.rank for item in candidates if item.entity == initial), None
        )
        margin = _normalized_margin(candidates)
        top1 = candidates[0] if candidates else None
        historical_override = bool(
            completed
            and top1 is not None
            and top1.entity != initial
            and (initial_rank is None or initial_rank > 2)
            and margin is not None
            and margin >= 0.25
        )
        final = top1.entity if historical_override and top1 is not None else initial
        m3_action = (
            "OVERRIDE_METRICS_TOP1" if historical_override else "KEEP_INITIAL"
        )
        if fixture.startswith("pr21-") and completed:
            stored_final = diagnosis.get("final_root_service") if isinstance(diagnosis, Mapping) else None
            arbitration = diagnosis.get("arbitration_decision") if isinstance(diagnosis, Mapping) else None
            stored_action = arbitration.get("action") if isinstance(arbitration, Mapping) else None
            if stored_final != final or stored_action != m3_action:
                raise ValueError("PR #21 frozen M3 differs from reconstruction")
        graph_nodes = set(projection.visibility.catalog_entities) | {initial}
        graph = EvidenceGraph(
            nodes=frozenset(graph_nodes),
            directed_edges=tuple(
                edge
                for edge in projection.directed_edges
                if edge[0] in graph_nodes and edge[1] in graph_nodes
            ),
        )
        symptom_times = projection.first_by_entity_source.get(initial, {})
        symptom_time = min(symptom_times.values()) if symptom_times else None
        upstream = any(
            graph.relation(item.entity, initial) == "UPSTREAM"
            and item.first_anomaly_time is not None
            and symptom_time is not None
            and item.first_anomaly_time <= symptom_time
            for item in candidates
        )
        if not projection.propagation_available:
            propagation = PropagationDisposition.UNAVAILABLE
        elif upstream:
            propagation = PropagationDisposition.PRESENT
        elif projection.directed_edges and symptom_time is not None and any(
            item.first_anomaly_time is not None for item in candidates
        ):
            propagation = PropagationDisposition.ABSENT
        else:
            propagation = PropagationDisposition.UNAVAILABLE
        truth = case.root_cause_service
        truth_fault = normalize_indicator(case.fault)
        initial_exact = completed and initial == truth
        final_exact = completed and final == truth
        causal_visible = frozenset(
            item.entity
            for item in candidates
            if item.first_anomaly_time is not None
            and graph.relation(item.entity, initial) in {"ROOT", "UPSTREAM"}
        )
        adjusted_candidates = tuple(
            UnifiedMetricCandidate(
                entity=item.entity,
                service_ancestor=item.service_ancestor,
                layer=item.layer,
                rank=item.rank,
                score=item.score,
                metric_family=item.metric_family,
                first_anomaly_time=item.first_anomaly_time,
                source_support=item.source_support,
                relation_to_symptom=graph.relation(item.entity, initial),
            )
            for item in candidates
        )
        unified = UnifiedRCACase(
            private_case_key=f"{fixture}:{case_id}",
            fixture=fixture,
            benchmark=("OB" if case.system == "RE2-OB" else "SS"),
            system=case.system,
            fault_family=case.fault,
            fault_type_truth=truth_fault,
            fault_type_raw=initial_fault,
            fault_regime=classify_fault_ontology(initial_fault),
            ground_truth_fault_regime=classify_fault_ontology(case.fault),
            metric_family=("UNKNOWN" if top1 is None else top1.metric_family),
            ground_truth_entity=truth,
            ground_truth_equivalent_entities=frozenset({truth}),
            ground_truth_layer=CanonicalEntityLayer.SERVICE,
            ground_truth_service=truth,
            ground_truth_workload=None,
            ground_truth_node=None,
            initial_entity=initial,
            initial_layer=(
                CanonicalEntityLayer.SERVICE
                if completed
                else CanonicalEntityLayer.UNKNOWN
            ),
            initial_service=initial if completed else None,
            initial_hierarchy_path=EntityHierarchyPath(
                entity=initial,
                explicit_parents=(),
                service_ancestor_or_none=initial if completed else None,
                infrastructure_ancestor_or_none=None,
            ),
            initial_supporting_evidence_refs=(
                tuple(str(item) for item in initial_value.get("evidence_refs", []))
                if isinstance(initial_value, Mapping)
                and isinstance(initial_value.get("evidence_refs"), list)
                else ()
            ),
            initial_correct_exact=initial_exact,
            initial_correct_service=initial_exact,
            initial_pair_correct=initial_exact and initial_fault == truth_fault,
            initial_relation="EXACT_MATCH" if initial_exact else "UNRELATED",
            m3_action=m3_action if completed else None,
            m3_final_entity=final,
            m3_final_layer=(
                CanonicalEntityLayer.SERVICE
                if completed
                else CanonicalEntityLayer.UNKNOWN
            ),
            m3_final_service=final if completed else None,
            m3_correct_exact=final_exact,
            m3_correct_service=final_exact,
            m3_pair_correct=final_exact and initial_fault == truth_fault,
            m3_relation="EXACT_MATCH" if final_exact else "UNRELATED",
            metrics_candidates=adjusted_candidates,
            metrics_initial_rank=initial_rank,
            metrics_margin=margin,
            metrics_top1_is_downstream=bool(
                top1 is not None
                and graph.relation(top1.entity, initial) == "DOWNSTREAM"
            ),
            propagation_disposition=propagation,
            visibility=projection.visibility,
            causal_visible_entities=causal_visible,
            alert_entity=None,
            terminal_failure=not completed,
        )
        source_entities = {
            "catalog": projection.visibility.catalog_entities,
            "metrics": projection.visibility.metrics_entities,
            "logs": projection.visibility.logs_entities,
            "traces": projection.visibility.traces_entities,
            "events": projection.visibility.events_entities,
            "alerts": projection.visibility.alerts_entities,
            "topology": projection.visibility.topology_entities,
            "any_model_visible": projection.visibility.any_model_visible,
            "causal": causal_visible,
        }
        ground_truth_visible = {
            source: truth in entities for source, entities in source_entities.items()
        }
        initial_visible = {
            source: initial in entities for source, entities in source_entities.items()
        }
        metrics_top1_visible = {
            source: bool(top1 is not None and top1.entity in entities)
            for source, entities in source_entities.items()
        }
        output.append(
            AdaptedCase(
                unified=unified,
                hierarchy_record={
                    "schema_version": "rca-crossbenchmark.entity-hierarchy-by-case.v1",
                    "private_case_key": unified.private_case_key,
                    "ground_truth_path": [truth],
                    "initial_relation": unified.initial_relation,
                    "m3_relation": unified.m3_relation,
                    "metrics_top1_relation": (
                        "UNRESOLVED"
                        if top1 is None
                        else ("EXACT_MATCH" if top1.entity == truth else "UNRELATED")
                    ),
                    "initial_same_workload": False,
                    "m3_same_workload": False,
                    "metrics_top1_same_workload": False,
                    "initial_same_node": False,
                    "m3_same_node": False,
                    "metrics_top1_same_node": False,
                    "initial_same_component": initial_exact,
                    "m3_same_component": final_exact,
                    "metrics_top1_same_component": bool(
                        top1 is not None and top1.entity == truth
                    ),
                    "topology_node_count": len(projection.visibility.catalog_entities),
                    "directed_edge_count": len(projection.directed_edges),
                },
                propagation_record={
                    "schema_version": "rca-crossbenchmark.propagation-role-by-case.v2",
                    "private_case_key": unified.private_case_key,
                    "initial_role": _relation_role(
                        initial,
                        truth,
                        alert_entity=None,
                        first_times=projection.first_by_entity_source,
                        graph=graph,
                    ),
                    "m3_role": _relation_role(
                        final,
                        truth,
                        alert_entity=None,
                        first_times=projection.first_by_entity_source,
                        graph=graph,
                    ),
                    "metrics_top1_role": (
                        "NO_TEMPORAL_SIGNAL"
                        if top1 is None
                        else _relation_role(
                            top1.entity,
                            truth,
                            alert_entity=None,
                            first_times=projection.first_by_entity_source,
                            graph=graph,
                        )
                    ),
                    "metrics_candidate_roles": [
                        {
                            "entity": candidate.entity,
                            "rank": candidate.rank,
                            "role": _relation_role(
                                candidate.entity,
                                truth,
                                alert_entity=None,
                                first_times=projection.first_by_entity_source,
                                graph=graph,
                            ),
                            "ground_truth_to_candidate_hops": (
                                graph.directed_distance(truth, candidate.entity)
                            ),
                        }
                        for candidate in adjusted_candidates
                    ],
                    "ground_truth_to_initial_hops": graph.directed_distance(
                        truth, initial
                    ),
                    "ground_truth_to_metrics_top1_hops": (
                        None
                        if top1 is None
                        else graph.directed_distance(truth, top1.entity)
                    ),
                    "first_anomaly_times": {
                        entity: dict(values)
                        for entity, values in projection.first_by_entity_source.items()
                    },
                    "ground_truth_first_anomaly": (
                        min(projection.first_by_entity_source[truth].values())
                        if projection.first_by_entity_source.get(truth)
                        else None
                    ),
                    "initial_first_anomaly": (
                        min(projection.first_by_entity_source[initial].values())
                        if projection.first_by_entity_source.get(initial)
                        else None
                    ),
                    "metrics_top1_first_anomaly": (
                        None if top1 is None else top1.first_anomaly_time
                    ),
                    "metrics_top1_later_than_ground_truth": bool(
                        top1 is not None
                        and top1.first_anomaly_time is not None
                        and projection.first_by_entity_source.get(truth)
                        and top1.first_anomaly_time
                        > min(projection.first_by_entity_source[truth].values())
                    ),
                    "metrics_top1_in_degree": (
                        0
                        if top1 is None
                        else sum(
                            dst == top1.entity
                            for _, dst in projection.directed_edges
                        )
                    ),
                    "metrics_top1_high_fan_in": bool(
                        top1 is not None
                        and sum(
                            dst == top1.entity
                            for _, dst in projection.directed_edges
                        )
                        >= 2
                    ),
                    "traffic_volume_available": False,
                    "propagation_disposition": propagation.value,
                },
                visibility_record={
                    "schema_version": "rca-crossbenchmark.evidence-visibility-by-case.v2",
                    "private_case_key": unified.private_case_key,
                    "ground_truth_visible": ground_truth_visible,
                    "ground_truth_service_visible": ground_truth_visible,
                    "initial_visible": initial_visible,
                    "metrics_top1_visible": metrics_top1_visible,
                    "initial_and_ground_truth_co_visible": {
                        source: initial_visible[source]
                        and ground_truth_visible[source]
                        for source in source_entities
                    },
                    "metrics_top1_and_ground_truth_co_visible": {
                        source: metrics_top1_visible[source]
                        and ground_truth_visible[source]
                        for source in source_entities
                    },
                },
            )
        )
        if progress is not None:
            progress("OBSS", len(output), 360)
    if len(output) != 360:
        raise ValueError("OB/SS unified fixture denominator differs")
    return tuple(output)


__all__ = [
    "AdaptedCase",
    "RCATopology",
    "classify_fault_ontology",
    "classify_propagation_role",
    "load_rca_schedule",
    "load_rca_terminal",
    "load_rca100_cases",
    "load_obss_cases",
    "metric_family",
    "read_rca_topology",
]
