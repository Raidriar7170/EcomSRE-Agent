"""Label-free RCA100 topology and temporal evidence scan for live H1 input."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
import json
import math
from pathlib import Path

import pyarrow.parquet as pq  # type: ignore[import-untyped]

from ecomsre_rca100.entity import (
    EntityCatalog,
    normalize_entity_name,
)
from ecomsre_rca100.projection import RCA100AgentTask
from ecomsre_rca_unified.hierarchy import EntityNode, normalize_entity_layer
from ecomsre_rca_unified.propagation import (
    first_marked_log_anomaly,
    first_metric_anomaly,
    first_trace_anomaly,
)


@dataclass(frozen=True, slots=True)
class LiveRCATopology:
    nodes: tuple[EntityNode, ...]
    parent_edges: tuple[tuple[str, str], ...]
    directed_edges: tuple[tuple[str, str], ...]
    explicit_dependency_edges: tuple[tuple[str, str], ...]
    undirected_edges: tuple[tuple[str, str], ...]
    unknown_edges: tuple[tuple[str, str], ...]


@dataclass(frozen=True, slots=True)
class LiveRCAEvidenceScan:
    first_by_entity_source: Mapping[str, Mapping[str, float]]
    events_entities: frozenset[str]
    alerts_entities: frozenset[str]
    trace_directed_edges: tuple[tuple[str, str], ...]
    metrics_entities: frozenset[str] = frozenset()
    logs_entities: frozenset[str] = frozenset()
    traces_entities: frozenset[str] = frozenset()


def _entity_ref(entity_type: str, entity_id: str) -> str:
    return f"{entity_type.split('.', 1)[0]}|{entity_type}|{entity_id}"


def read_live_rca_topology(path: Path) -> LiveRCATopology:
    if path.is_symlink() or not path.is_file():
        raise ValueError("live RCA topology must be a regular file")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or not isinstance(value.get("entities"), list):
        raise ValueError("live RCA topology schema is invalid")
    by_id: dict[str, tuple[str, str]] = {}
    nodes: list[EntityNode] = []
    for item in value["entities"]:
        if not isinstance(item, dict):
            raise ValueError("live RCA topology entity must be an object")
        entity_id = item.get("id")
        entity_type = item.get("type")
        name = item.get("name")
        if not all(
            isinstance(part, str) and part
            for part in (entity_id, entity_type, name)
        ):
            raise ValueError("live RCA topology entity identity is invalid")
        assert isinstance(entity_id, str)
        assert isinstance(entity_type, str)
        assert isinstance(name, str)
        if entity_id in by_id:
            raise ValueError("live RCA topology contains duplicate entity IDs")
        reference = _entity_ref(entity_type, entity_id)
        by_id[entity_id] = (reference, entity_type)
        nodes.append(
            EntityNode(
                entity_ref=reference,
                layer=normalize_entity_layer(entity_type),
                normalized_name=normalize_entity_name(name),
            )
        )
    parent: set[tuple[str, str]] = set()
    directed: set[tuple[str, str]] = set()
    explicit_dependency: set[tuple[str, str]] = set()
    undirected: set[tuple[str, str]] = set()
    unknown: set[tuple[str, str]] = set()
    edges = value.get("edges", [])
    if not isinstance(edges, list):
        raise ValueError("live RCA topology edges must be a list")
    for item in edges:
        if not isinstance(item, dict):
            raise ValueError("live RCA topology edge must be an object")
        src = item.get("src")
        dst = item.get("dst")
        relation = item.get("relation")
        if (
            not isinstance(src, str)
            or not isinstance(dst, str)
            or src not in by_id
            or dst not in by_id
        ):
            raise ValueError("live RCA topology edge identity is invalid")
        src_ref = by_id[src][0]
        dst_ref = by_id[dst][0]
        if relation == "contains":
            parent.add((dst_ref, src_ref))
        elif relation == "calls":
            directed.add((dst_ref, src_ref))
        elif relation == "hosts":
            undirected.add((min(src_ref, dst_ref), max(src_ref, dst_ref)))
        elif relation in {"depends_on", "dependency"}:
            explicit_dependency.add((dst_ref, src_ref))
        elif relation == "unknown":
            unknown.add((min(src_ref, dst_ref), max(src_ref, dst_ref)))
        elif relation == "same_as":
            continue
        else:
            raise ValueError("live RCA topology relation is not allowlisted")
    return LiveRCATopology(
        nodes=tuple(sorted(nodes, key=lambda item: item.entity_ref)),
        parent_edges=tuple(sorted(parent)),
        directed_edges=tuple(sorted(directed)),
        explicit_dependency_edges=tuple(sorted(explicit_dependency)),
        undirected_edges=tuple(sorted(undirected)),
        unknown_edges=tuple(sorted(unknown)),
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


def _in_task_window(timestamp: float | None, task: RCA100AgentTask) -> bool:
    return (
        timestamp is not None
        and task.window_start_timestamp
        <= timestamp
        <= task.window_end_timestamp
    )


def _trace_parent_child_edges(
    span_service: Mapping[tuple[str, str], str],
    span_parents: Sequence[tuple[tuple[str, str], tuple[str, str]]],
) -> set[tuple[str, str]]:
    return {
        (parent, child)
        for child_key, parent_key in span_parents
        if (child := span_service.get(child_key)) is not None
        and (parent := span_service.get(parent_key)) is not None
        and child != parent
    }


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
        timestamp
        for timestamp in (
            _parse_timestamp(payload.get("eventTime")),
            _parse_timestamp(payload.get("firstTimestamp")),
            _parse_timestamp(payload.get("lastTimestamp")),
        )
        if timestamp is not None
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
        if isinstance(resource, Mapping) and isinstance(
            resource.get("entity"), Mapping
        ):
            value = resource["entity"]
            assert isinstance(value, Mapping)
            entity = catalog.resolve_exact(
                entity_id=str(value.get("entity_id") or "") or None,
                entity_type=str(value.get("entity_type") or "") or None,
                entity_name=None,
            )
    timestamp = _parse_timestamp(raw.get("time_s")) or _parse_timestamp(
        raw.get("time")
    )
    return None if entity is None else entity.entity_ref, timestamp


def scan_live_rca_evidence(
    case_root: Path,
    *,
    task: RCA100AgentTask,
    catalog: EntityCatalog,
    methodology: Mapping[str, object],
) -> LiveRCAEvidenceScan:
    first: dict[str, dict[str, float]] = defaultdict(dict)
    anomaly = methodology["first_anomaly"]
    if not isinstance(anomaly, Mapping):
        raise ValueError("live RCA anomaly methodology is invalid")
    metric_config = anomaly["metrics"]
    if not isinstance(metric_config, Mapping):
        raise ValueError("live RCA metric anomaly methodology is invalid")
    metric_series: dict[
        tuple[str, str, str, str], list[tuple[float, float]]
    ] = defaultdict(list)
    metrics_entities: set[str] = set()
    metrics = pq.ParquetFile(case_root / "metrics.parquet")
    columns = [
        "time",
        "entity_set",
        "entity_id",
        "entity_name",
        "metric",
        "value",
        "metric_set_id",
        "service",
    ]
    for batch in metrics.iter_batches(batch_size=65_536, columns=columns):
        for raw in batch.to_pylist():
            timestamp_raw = raw.get("time")
            value_raw = raw.get("value")
            if type(timestamp_raw) is not int or not isinstance(
                value_raw, (int, float)
            ):
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
            metrics_entities.add(entity.entity_ref)
            key = (
                entity.entity_ref,
                str(raw.get("metric") or ""),
                str(raw.get("metric_set_id") or ""),
                str(raw.get("service") or ""),
            )
            metric_series[key].append((timestamp, value))
    for key, metric_samples in metric_series.items():
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
    if not isinstance(log_config, Mapping):
        raise ValueError("live RCA log anomaly methodology is invalid")
    raw_markers = log_config["content_markers"]
    if not isinstance(raw_markers, Sequence):
        raise ValueError("live RCA log markers are invalid")
    markers = tuple(str(item) for item in raw_markers)
    marked_logs: dict[str, list[tuple[float, str]]] = defaultdict(list)
    logs_entities: set[str] = set()
    logs = pq.ParquetFile(case_root / "logs.parquet")
    log_columns = [
        "content",
        "_time_",
        "_pod_uid_",
        "_pod_name_",
        "_container_name_",
    ]
    for batch in logs.iter_batches(batch_size=65_536, columns=log_columns):
        for raw in batch.to_pylist():
            log_row_timestamp = _parse_timestamp(raw.get("_time_"))
            if not _in_task_window(log_row_timestamp, task):
                continue
            entity = catalog.resolve_log_entity(
                pod_uid=str(raw.get("_pod_uid_") or ""),
                pod_name=str(raw.get("_pod_name_") or ""),
                container_name=str(raw.get("_container_name_") or ""),
            )
            if entity is None:
                continue
            logs_entities.add(entity.entity_ref)
            assert log_row_timestamp is not None
            content = str(raw.get("content") or "")
            if (
                log_row_timestamp >= task.anchor_timestamp
                and any(marker in content.casefold() for marker in markers)
            ):
                marked_logs[entity.entity_ref].append((log_row_timestamp, content))
    for log_entity_ref, log_samples in marked_logs.items():
        log_anomaly_timestamp = first_marked_log_anomaly(
            samples=log_samples,
            anchor=task.anchor_timestamp,
            markers=markers,
        )
        if log_anomaly_timestamp is not None:
            first[log_entity_ref]["logs"] = log_anomaly_timestamp

    trace_config = anomaly["traces"]
    if not isinstance(trace_config, Mapping):
        raise ValueError("live RCA trace anomaly methodology is invalid")
    trace_samples: dict[str, list[tuple[float, float, bool]]] = defaultdict(list)
    traces_entities: set[str] = set()
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
            trace_row_timestamp = _parse_timestamp(raw.get("startTime"))
            if (
                trace_row_timestamp is None
                or not task.window_start_timestamp
                <= trace_row_timestamp
                <= task.window_end_timestamp
            ):
                continue
            entity = catalog.resolve_trace_entity(
                service_name=str(raw.get("serviceName") or "")
            )
            if entity is None:
                continue
            traces_entities.add(entity.entity_ref)
            trace_id = str(raw.get("traceId") or "")
            span_id = str(raw.get("spanId") or "")
            parent_id = str(raw.get("parentSpanId") or "")
            if trace_id and span_id:
                span_service[(trace_id, span_id)] = entity.entity_ref
                if parent_id:
                    span_parents.append(((trace_id, span_id), (trace_id, parent_id)))
            try:
                duration = float(str(raw.get("duration") or "nan"))
            except ValueError:
                continue
            status = str(raw.get("statusCode") or "").casefold()
            failed = status not in {"", "0", "1", "false", "ok", "unset"}
            trace_samples[entity.entity_ref].append(
                (trace_row_timestamp, duration, failed)
            )
    for trace_entity_ref, entity_trace_samples in trace_samples.items():
        trace_anomaly_timestamp = first_trace_anomaly(
            samples=entity_trace_samples,
            anchor=task.anchor_timestamp,
            minimum_pre_samples=int(trace_config["minimum_pre_samples_for_slow"]),
            slow_multiplier=float(trace_config["slow_multiplier"]),
        )
        if trace_anomaly_timestamp is not None:
            first[trace_entity_ref]["traces"] = trace_anomaly_timestamp
    trace_edges = _trace_parent_child_edges(span_service, span_parents)

    events_entities: set[str] = set()
    events = pq.ParquetFile(case_root / "events.parquet")
    for batch in events.iter_batches(batch_size=16_384):
        for raw in batch.to_pylist():
            event_entity_ref, event_timestamp = _resolved_event_entity(raw, catalog)
            if event_entity_ref is None or not _in_task_window(event_timestamp, task):
                continue
            assert event_timestamp is not None
            events_entities.add(event_entity_ref)
            if event_timestamp >= task.anchor_timestamp:
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
            if alert_entity_ref is None or not _in_task_window(alert_timestamp, task):
                continue
            assert alert_timestamp is not None
            alerts_entities.add(alert_entity_ref)
            if alert_timestamp >= task.anchor_timestamp:
                current = first[alert_entity_ref].get("alerts")
                first[alert_entity_ref]["alerts"] = (
                    alert_timestamp
                    if current is None
                    else min(current, alert_timestamp)
                )
    return LiveRCAEvidenceScan(
        first_by_entity_source={key: dict(value) for key, value in first.items()},
        metrics_entities=frozenset(metrics_entities),
        logs_entities=frozenset(logs_entities),
        traces_entities=frozenset(traces_entities),
        events_entities=frozenset(events_entities),
        alerts_entities=frozenset(alerts_entities),
        trace_directed_edges=tuple(sorted(trace_edges)),
    )


__all__ = [
    "LiveRCAEvidenceScan",
    "LiveRCATopology",
    "read_live_rca_topology",
    "scan_live_rca_evidence",
]
