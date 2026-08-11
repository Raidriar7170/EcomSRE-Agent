"""Label-free RCA100 and OB/SS projections for compact retrieval."""

from __future__ import annotations

from collections import defaultdict
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

from ecomsre_rca100.entity import (
    EntityCatalog,
    load_entity_catalog,
    normalize_entity_name,
)
from ecomsre_rca100.projection import (
    RCA100AgentContext,
    RCA100AgentTask,
    build_agent_context,
)
from ecomsre_rcaeval.adapter import ArchitectureContextBuilder
from ecomsre_rcaeval.contracts import Architecture
from ecomsre_rcaeval.dataset import TelemetryCase
from ecomsre_rca_unified.compact_contracts import (
    CompactBaseContext,
    CompactEdge,
    CompactEntity,
    CompactEvidence,
    CompactRetrievalSource,
    EvidenceSource,
    SourceStatus,
)
from ecomsre_rca_unified.contracts import CanonicalEntityLayer
from ecomsre_rca_unified.hierarchy import normalize_entity_layer
from ecomsre_rca_unified.propagation import (
    first_marked_log_anomaly,
    first_metric_anomaly,
    first_trace_anomaly,
)


@dataclass(frozen=True, slots=True)
class _Topology:
    entities: tuple[CompactEntity, ...]
    edges: tuple[CompactEdge, ...]


@dataclass(frozen=True, slots=True)
class _Scan:
    visibility: Mapping[str, frozenset[EvidenceSource]]
    occurrences: Mapping[str, Mapping[EvidenceSource, int]]
    first_anomaly_time: Mapping[str, float]
    trace_edges: tuple[CompactEdge, ...]
    alert_entities: tuple[str, ...]


def _regular(path: Path, label: str) -> Path:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"compact projection requires a regular {label}")
    return path


def _entity_ref(entity_type: str, entity_id: str) -> str:
    return f"{entity_type.split('.', 1)[0]}|{entity_type}|{entity_id}"


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


def _in_window(timestamp: float | None, task: RCA100AgentTask) -> bool:
    return bool(
        timestamp is not None
        and task.window_start_timestamp <= timestamp <= task.window_end_timestamp
    )


def _read_topology(path: Path, catalog: EntityCatalog) -> _Topology:
    value = json.loads(_regular(path, "topology").read_text(encoding="utf-8"))
    if not isinstance(value, dict) or not isinstance(value.get("entities"), list):
        raise ValueError("compact topology schema is invalid")
    by_id: dict[str, str] = {}
    names: dict[str, str] = {}
    types: dict[str, str] = {}
    for raw in value["entities"]:
        if not isinstance(raw, dict):
            raise ValueError("compact topology entity must be an object")
        entity_id = raw.get("id")
        entity_type = raw.get("type")
        name = raw.get("name")
        if not all(
            isinstance(item, str) and item for item in (entity_id, entity_type, name)
        ):
            raise ValueError("compact topology entity identity is invalid")
        assert isinstance(entity_id, str)
        assert isinstance(entity_type, str)
        assert isinstance(name, str)
        if entity_id in by_id:
            raise ValueError("compact topology contains duplicate entity IDs")
        by_id[entity_id] = _entity_ref(entity_type, entity_id)
        names[entity_id] = name
        types[entity_id] = entity_type
    raw_edges = value.get("edges", [])
    if not isinstance(raw_edges, list):
        raise ValueError("compact topology edges must be a list")
    parents: dict[str, set[str]] = defaultdict(set)
    edges: set[tuple[str, str, str]] = set()
    for raw in raw_edges:
        if not isinstance(raw, dict):
            raise ValueError("compact topology edge must be an object")
        src = raw.get("src")
        dst = raw.get("dst")
        relation = raw.get("relation")
        if (
            not isinstance(src, str)
            or not isinstance(dst, str)
            or src not in by_id
            or dst not in by_id
        ):
            raise ValueError("compact topology edge identity is invalid")
        src_ref = by_id[src]
        dst_ref = by_id[dst]
        if relation == "contains":
            parents[dst_ref].add(src_ref)
            edges.add((dst_ref, src_ref, "PARENT"))
        elif relation == "calls":
            edges.add((dst_ref, src_ref, "DIRECTED_TOPOLOGY"))
        elif relation in {"depends_on", "dependency"}:
            edges.add((dst_ref, src_ref, "EXPLICIT_DEPENDENCY"))
        elif relation == "hosts":
            edges.add((min(src_ref, dst_ref), max(src_ref, dst_ref), "UNDIRECTED"))
        elif relation in {"same_as", "unknown"}:
            continue
        else:
            raise ValueError("compact topology relation is not allowlisted")
    entities: list[CompactEntity] = []
    for entity_id, ref in sorted(by_id.items(), key=lambda item: item[1]):
        catalog_entity = catalog.by_ref[ref]
        parent_values = sorted(parents.get(ref, set()))
        explicit_parent = parent_values[0] if len(parent_values) == 1 else None
        entities.append(
            CompactEntity(
                entity_ref=ref,
                display_name=normalize_entity_name(names[entity_id]),
                layer=normalize_entity_layer(types[entity_id]),
                service_ancestor_or_none=(
                    ref
                    if normalize_entity_layer(types[entity_id]).value == "SERVICE"
                    else catalog_entity.parent_service_ref_or_none
                ),
                parent_ref_or_none=(
                    explicit_parent or catalog_entity.parent_service_ref_or_none
                ),
            )
        )
    return _Topology(
        entities=tuple(entities),
        edges=tuple(
            CompactEdge(
                source_entity_ref=left,
                target_entity_ref=right,
                edge_type=cast(
                    Literal[
                        "PARENT",
                        "DIRECTED_TOPOLOGY",
                        "TRACE_PARENT_CHILD",
                        "EXPLICIT_DEPENDENCY",
                        "UNDIRECTED",
                    ],
                    kind,
                ),
            )
            for left, right, kind in sorted(edges)
        ),
    )


def _resolved_event_entity(
    raw: Mapping[str, object], catalog: EntityCatalog
) -> tuple[str | None, float | None]:
    encoded = raw.get("eventId")
    if not isinstance(encoded, str):
        return None, None
    try:
        payload = json.loads(encoded)
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
    encoded = raw.get("resource")
    entity = None
    if isinstance(encoded, str):
        try:
            resource = json.loads(encoded)
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
    timestamp = _parse_timestamp(raw.get("time_s")) or _parse_timestamp(raw.get("time"))
    return None if entity is None else entity.entity_ref, timestamp


def _scan_rca100(
    case_root: Path,
    *,
    task: RCA100AgentTask,
    catalog: EntityCatalog,
    methodology: Mapping[str, object],
) -> _Scan:
    visibility: dict[str, set[EvidenceSource]] = defaultdict(set)
    occurrences: dict[str, dict[EvidenceSource, int]] = defaultdict(
        lambda: defaultdict(int)
    )
    first_by_entity: dict[str, float] = {}

    def observe(
        entity_ref: str,
        source: EvidenceSource,
        *,
        anomaly_time: float | None = None,
    ) -> None:
        visibility[entity_ref].add(source)
        occurrences[entity_ref][source] += 1
        if anomaly_time is not None:
            current = first_by_entity.get(entity_ref)
            first_by_entity[entity_ref] = (
                anomaly_time if current is None else min(current, anomaly_time)
            )

    anomaly = methodology.get("first_anomaly")
    if not isinstance(anomaly, Mapping):
        raise ValueError("compact anomaly methodology is invalid")
    metric_config = anomaly.get("metrics")
    log_config = anomaly.get("logs")
    trace_config = anomaly.get("traces")
    if not all(
        isinstance(item, Mapping) for item in (metric_config, log_config, trace_config)
    ):
        raise ValueError("compact anomaly methodology sections are invalid")
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
        for raw in batch.to_pylist():
            timestamp_raw = raw.get("time")
            value_raw = raw.get("value")
            if type(timestamp_raw) is not int or not isinstance(
                value_raw, (int, float)
            ):
                continue
            metric_timestamp = timestamp_raw / 1_000_000.0
            if (
                not task.window_start_timestamp
                <= metric_timestamp
                <= task.window_end_timestamp
            ):
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
            observe(entity.entity_ref, "METRICS")
            metric_series[
                (
                    entity.entity_ref,
                    str(raw.get("metric") or ""),
                    str(raw.get("metric_set_id") or ""),
                    str(raw.get("service") or ""),
                )
            ].append((metric_timestamp, value))
    for metric_key, metric_samples in metric_series.items():
        metric_anomaly_time = first_metric_anomaly(
            samples=metric_samples,
            anchor=task.anchor_timestamp,
            minimum_pre_samples=int(metric_config["minimum_pre_samples"]),
            minimum_post_samples=int(metric_config["minimum_post_samples"]),
            mad_multiplier=float(metric_config["mad_multiplier"]),
            relative_floor=float(metric_config["absolute_relative_floor"]),
            epsilon=float(metric_config["zero_epsilon"]),
        )
        if metric_anomaly_time is not None:
            current = first_by_entity.get(metric_key[0])
            first_by_entity[metric_key[0]] = (
                metric_anomaly_time
                if current is None
                else min(current, metric_anomaly_time)
            )

    raw_markers = log_config.get("content_markers")
    if not isinstance(raw_markers, Sequence):
        raise ValueError("compact log anomaly markers are invalid")
    markers = tuple(str(item) for item in raw_markers)
    marked_logs: dict[str, list[tuple[float, str]]] = defaultdict(list)
    logs = pq.ParquetFile(case_root / "logs.parquet")
    for batch in logs.iter_batches(
        batch_size=65_536,
        columns=["content", "_time_", "_pod_uid_", "_pod_name_", "_container_name_"],
    ):
        for raw in batch.to_pylist():
            log_timestamp = _parse_timestamp(raw.get("_time_"))
            if not _in_window(log_timestamp, task):
                continue
            entity = catalog.resolve_log_entity(
                pod_uid=str(raw.get("_pod_uid_") or ""),
                pod_name=str(raw.get("_pod_name_") or ""),
                container_name=str(raw.get("_container_name_") or ""),
            )
            if entity is None:
                continue
            observe(entity.entity_ref, "LOGS")
            assert log_timestamp is not None
            content = str(raw.get("content") or "")
            if log_timestamp >= task.anchor_timestamp and any(
                marker in content.casefold() for marker in markers
            ):
                marked_logs[entity.entity_ref].append((log_timestamp, content))
    for log_entity_ref, log_samples in marked_logs.items():
        log_anomaly_time = first_marked_log_anomaly(
            samples=log_samples, anchor=task.anchor_timestamp, markers=markers
        )
        if log_anomaly_time is not None:
            current = first_by_entity.get(log_entity_ref)
            first_by_entity[log_entity_ref] = (
                log_anomaly_time if current is None else min(current, log_anomaly_time)
            )

    trace_samples: dict[str, list[tuple[float, float, bool]]] = defaultdict(list)
    span_service: dict[tuple[str, str], str] = {}
    span_parents: list[tuple[tuple[str, str], tuple[str, str]]] = []
    traces = pq.ParquetFile(case_root / "traces.parquet")
    for batch in traces.iter_batches(
        batch_size=65_536,
        columns=[
            "traceId",
            "spanId",
            "parentSpanId",
            "startTime",
            "duration",
            "serviceName",
            "statusCode",
        ],
    ):
        for raw in batch.to_pylist():
            trace_timestamp = _parse_timestamp(raw.get("startTime"))
            if not _in_window(trace_timestamp, task):
                continue
            entity = catalog.resolve_trace_entity(
                service_name=str(raw.get("serviceName") or "")
            )
            if entity is None:
                continue
            observe(entity.entity_ref, "TRACES")
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
            if not math.isfinite(duration):
                continue
            assert trace_timestamp is not None
            status = str(raw.get("statusCode") or "").casefold()
            failed = status not in {"", "0", "1", "false", "ok", "unset"}
            trace_samples[entity.entity_ref].append((trace_timestamp, duration, failed))
    for trace_entity_ref, entity_trace_samples in trace_samples.items():
        trace_anomaly_time = first_trace_anomaly(
            samples=entity_trace_samples,
            anchor=task.anchor_timestamp,
            minimum_pre_samples=int(trace_config["minimum_pre_samples_for_slow"]),
            slow_multiplier=float(trace_config["slow_multiplier"]),
        )
        if trace_anomaly_time is not None:
            current = first_by_entity.get(trace_entity_ref)
            first_by_entity[trace_entity_ref] = (
                trace_anomaly_time
                if current is None
                else min(current, trace_anomaly_time)
            )
    trace_pairs = {
        (parent, child)
        for child_key, parent_key in span_parents
        if (child := span_service.get(child_key)) is not None
        and (parent := span_service.get(parent_key)) is not None
        and child != parent
    }

    events = pq.ParquetFile(case_root / "events.parquet")
    for batch in events.iter_batches(batch_size=16_384):
        for raw in batch.to_pylist():
            event_entity_ref, event_timestamp = _resolved_event_entity(raw, catalog)
            if event_entity_ref is not None and _in_window(event_timestamp, task):
                observe(
                    event_entity_ref,
                    "EVENTS",
                    anomaly_time=(
                        event_timestamp
                        if event_timestamp is not None
                        and event_timestamp >= task.anchor_timestamp
                        else None
                    ),
                )
    alerts: set[str] = set()
    alert_rows = pq.ParquetFile(case_root / "alerts.parquet")
    for batch in alert_rows.iter_batches(batch_size=16_384):
        for raw in batch.to_pylist():
            alert_entity_ref, alert_timestamp = _resolved_alert_entity(raw, catalog)
            if alert_entity_ref is not None and _in_window(alert_timestamp, task):
                alerts.add(alert_entity_ref)
                observe(
                    alert_entity_ref,
                    "ALERTS",
                    anomaly_time=(
                        alert_timestamp
                        if alert_timestamp is not None
                        and alert_timestamp >= task.anchor_timestamp
                        else None
                    ),
                )
    return _Scan(
        visibility={key: frozenset(value) for key, value in visibility.items()},
        occurrences={key: dict(value) for key, value in occurrences.items()},
        first_anomaly_time=first_by_entity,
        trace_edges=tuple(
            CompactEdge(
                source_entity_ref=parent,
                target_entity_ref=child,
                edge_type="TRACE_PARENT_CHILD",
            )
            for parent, child in sorted(trace_pairs)
        ),
        alert_entities=tuple(sorted(alerts)),
    )


def _base_evidence_from_rca100(
    context: RCA100AgentContext,
) -> tuple[CompactEvidence, ...]:
    output: list[CompactEvidence] = []
    for raw_source, projection in (
        ("METRICS", context.metrics),
        ("LOGS", context.logs),
        ("TRACES", context.traces),
    ):
        source = cast(Literal["METRICS", "LOGS", "TRACES"], raw_source)
        for item in projection.evidence:
            started_at = getattr(item, "started_at", None)
            ended_at = getattr(item, "ended_at", None)
            output.append(
                CompactEvidence(
                    evidence_ref=item.evidence_ref,
                    source=source,
                    entity_ref=item.entity_ref,
                    name=getattr(item, "metric", getattr(item, "name", "evidence")),
                    started_at=(
                        context.task.window_start_timestamp
                        if started_at is None
                        else float(started_at)
                    ),
                    ended_at=(
                        context.task.window_end_timestamp
                        if ended_at is None
                        else float(ended_at)
                    ),
                    score=float(item.score),
                    summary=item.summary,
                )
            )
    return tuple(output)


def build_rca100_compact_inputs(
    case_root: Path,
    *,
    projection_case_number: int,
    methodology: Mapping[str, object],
) -> tuple[CompactBaseContext, CompactRetrievalSource]:
    if not 1 <= projection_case_number <= 9_999:
        raise ValueError("RCA100 projection ordinal is invalid")
    context = build_agent_context(
        case_root, opaque_case_id=f"rca100-case-{projection_case_number:04d}"
    )
    catalog = load_entity_catalog(case_root / "topology.json")
    topology = _read_topology(case_root / "topology.json", catalog)
    scan = _scan_rca100(
        case_root, task=context.task, catalog=catalog, methodology=methodology
    )
    visible_by_ref = {item.entity_ref: item for item in topology.entities}
    base = CompactBaseContext(
        alert_title=context.task.alert_title,
        prompt_text=context.task.prompt_text,
        alert_entity_ref=context.task.alert_entity_ref,
        entities=tuple(
            visible_by_ref[item.entity_ref] for item in context.visible_entities
        ),
        evidence=_base_evidence_from_rca100(context),
        source_status={
            "METRICS": "AVAILABLE"
            if context.metrics.status == "AVAILABLE"
            else "SOURCE_UNAVAILABLE",
            "LOGS": cast(SourceStatus, context.logs.status),
            "TRACES": cast(SourceStatus, context.traces.status),
        },
    )
    ranking = tuple(item.entity_ref for item in context.metrics.ranking)
    return base, CompactRetrievalSource(
        entities=topology.entities,
        edges=(*topology.edges, *scan.trace_edges),
        source_visibility=dict(scan.visibility),
        source_occurrences={
            key: dict(value) for key, value in scan.occurrences.items()
        },
        first_anomaly_time=dict(scan.first_anomaly_time),
        metrics_ranking=ranking,
        metrics_scores={
            item.entity_ref: float(item.score) for item in context.metrics.ranking
        },
        alert_entities=tuple(
            dict.fromkeys(
                item
                for item in (context.task.alert_entity_ref, *scan.alert_entities)
                if item is not None
            )
        ),
    )


def discover_label_blind_dev_cases(
    root: Path, *, system: Literal["RE2-OB", "RE2-SS"]
) -> tuple[TelemetryCase, ...]:
    if root.name != system or not root.is_dir() or root.is_symlink():
        raise ValueError("label-blind OB/SS root is invalid")
    output: list[TelemetryCase] = []
    for group in sorted(path for path in root.iterdir() if path.is_dir()):
        if group.is_symlink():
            raise ValueError("label-blind OB/SS group must not be a symlink")
        for case_root in sorted(
            path
            for path in group.iterdir()
            if path.is_dir() and path.name in {"1", "2", "3"}
        ):
            if case_root.is_symlink():
                raise ValueError("label-blind OB/SS case must not be a symlink")
            metric_candidates = tuple(
                path
                for path in (case_root / "simple_metrics.csv", case_root / "data.csv")
                if path.exists()
            )
            if len(metric_candidates) != 1:
                raise ValueError("label-blind OB/SS metrics are invalid")
            traces_candidate = case_root / "traces.csv"
            traces_path = (
                _regular(traces_candidate, "traces.csv") if system == "RE2-OB" else None
            )
            if system == "RE2-SS" and traces_candidate.exists():
                raise ValueError("label-blind RE2-SS unexpectedly contains traces")
            inject_time = int(
                _regular(case_root / "inject_time.txt", "inject time")
                .read_text(encoding="utf-8")
                .strip()
            )
            if inject_time < 0:
                raise ValueError("label-blind inject time is negative")
            output.append(
                TelemetryCase(
                    case_id=f"{system.casefold()}-case-{len(output) + 1:04d}",
                    system=system,
                    root=case_root,
                    metrics_path=_regular(metric_candidates[0], "metrics CSV"),
                    logs_path=_regular(case_root / "logs.csv", "logs.csv"),
                    traces_path=traces_path,
                    inject_time=inject_time,
                )
            )
    if not output:
        raise ValueError("label-blind OB/SS dataset contains no cases")
    return tuple(output)


def _service_ref(service: str) -> str:
    normalized = service.strip().casefold()
    if (
        not normalized
        or re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,127}", normalized) is None
    ):
        raise ValueError("OB/SS visible service has an invalid canonical name")
    return f"apm|apm.service|{normalized}"


def _obss_column(row: Mapping[str, str], names: tuple[str, ...]) -> str | None:
    lowered = {key.casefold(): key for key in row}
    for name in names:
        key = lowered.get(name.casefold())
        if key is not None:
            return row.get(key)
    return None


def _obss_timestamp(row: Mapping[str, str], names: tuple[str, ...]) -> float | None:
    raw = _obss_column(row, names)
    try:
        return _parse_timestamp(raw)
    except (TypeError, ValueError):
        return None


def _score_from_summary(summary: str) -> float:
    match = re.search(r"(?:anomaly|combined)-score=([-+0-9.eE]+)", summary)
    if match is None:
        return 0.0
    try:
        return float(match.group(1))
    except ValueError:
        return 0.0


def build_obss_compact_inputs(
    case: TelemetryCase,
) -> tuple[CompactBaseContext, CompactRetrievalSource]:
    builder = ArchitectureContextBuilder(case, Architecture.SINGLE)
    for source in cast(
        tuple[Literal["metrics", "logs", "traces"], ...],
        ("metrics", "logs", "traces"),
    ):
        builder.query_source(source)
    context = builder.snapshot()
    bounded_services = tuple(
        sorted({item.service for item in context.evidence if item.service != "unknown"})
    )
    if not bounded_services:
        raise ValueError("OB/SS bounded context contains no visible service")
    visibility: dict[str, set[EvidenceSource]] = defaultdict(set)
    occurrences: dict[str, dict[EvidenceSource, int]] = defaultdict(
        lambda: defaultdict(int)
    )
    lower = case.inject_time - 600
    upper = case.inject_time + 600
    with case.metrics_path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise ValueError("OB/SS metrics require a header")
        metric_services = {
            name.rsplit("_", 1)[0]
            for name in reader.fieldnames
            if name != "time" and "_" in name
        }
        visible_rows = sum(
            lower <= timestamp <= upper
            for row in reader
            if (timestamp := _obss_timestamp(row, ("time",))) is not None
        )
        for service in metric_services:
            ref = _service_ref(service)
            visibility[ref].add("METRICS")
            occurrences[ref]["METRICS"] += visible_rows
    with case.logs_path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise ValueError("OB/SS logs require a header")
        for row in reader:
            log_timestamp = _obss_timestamp(row, ("timestamp", "time"))
            log_service = _obss_column(
                row, ("service", "serviceName", "container_name")
            )
            if (
                log_timestamp is not None
                and lower <= log_timestamp <= upper
                and log_service
            ):
                ref = _service_ref(log_service)
                visibility[ref].add("LOGS")
                occurrences[ref]["LOGS"] += 1
    trace_pairs: set[tuple[str, str]] = set()
    if case.traces_path is not None:
        span_service: dict[tuple[str, str], str] = {}
        parents: list[tuple[tuple[str, str], tuple[str, str]]] = []
        with case.traces_path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames is None:
                raise ValueError("OB/SS traces require a header")
            for row in reader:
                trace_timestamp = _obss_timestamp(
                    row,
                    ("startTimeMillis", "startTime", "start_time", "timestamp", "time"),
                )
                trace_service = (
                    _obss_column(row, ("service", "serviceName")) or ""
                ).strip()
                if (
                    trace_timestamp is None
                    or not lower <= trace_timestamp <= upper
                    or not trace_service
                ):
                    continue
                ref = _service_ref(trace_service)
                visibility[ref].add("TRACES")
                occurrences[ref]["TRACES"] += 1
                trace_id = row.get("traceID") or row.get("traceId") or ""
                span_id = row.get("spanID") or row.get("spanId") or ""
                parent_id = row.get("parentSpanID") or row.get("parentSpanId") or ""
                if trace_id and span_id:
                    span_service[(trace_id, span_id)] = ref
                    if parent_id:
                        parents.append(((trace_id, span_id), (trace_id, parent_id)))
        trace_pairs = {
            (parent, child)
            for child_key, parent_key in parents
            if (child := span_service.get(child_key)) is not None
            and (parent := span_service.get(parent_key)) is not None
            and child != parent
        }
    all_services = tuple(
        sorted(set(bounded_services) | {ref.rsplit("|", 1)[-1] for ref in visibility})
    )
    entities = tuple(
        CompactEntity(
            entity_ref=_service_ref(service),
            display_name=service,
            layer=CanonicalEntityLayer.SERVICE,
            service_ancestor_or_none=_service_ref(service),
            parent_ref_or_none=None,
        )
        for service in all_services
    )
    evidence = tuple(
        CompactEvidence(
            evidence_ref=item.evidence_id,
            source=cast(
                Literal["METRICS", "LOGS", "TRACES"],
                {"metric": "METRICS", "log": "LOGS", "trace": "TRACES"}[
                    item.evidence_id.partition(":")[0]
                ],
            ),
            entity_ref=_service_ref(item.service),
            name=item.name,
            started_at=item.started_at,
            ended_at=item.ended_at,
            score=_score_from_summary(item.summary),
            summary=item.summary,
        )
        for item in context.evidence
        if item.service != "unknown"
    )
    first_anomaly: dict[str, float] = {}
    for item in evidence:
        current = first_anomaly.get(item.entity_ref)
        first_anomaly[item.entity_ref] = (
            item.started_at if current is None else min(current, item.started_at)
        )
        visibility[item.entity_ref].add(item.source)
        occurrences[item.entity_ref][item.source] = max(
            occurrences[item.entity_ref][item.source], 1
        )
    metric_best: dict[str, float] = {}
    for item in evidence:
        if item.source == "METRICS":
            metric_best[item.entity_ref] = max(
                metric_best.get(item.entity_ref, -math.inf), item.score
            )
    metric_ranking = tuple(
        ref
        for ref, _score in sorted(
            metric_best.items(), key=lambda item: (-item[1], item[0])
        )[:6]
    )
    base_entities = tuple(
        item for item in entities if item.display_name in bounded_services
    )
    status: dict[Literal["METRICS", "LOGS", "TRACES"], SourceStatus] = {
        cast(Literal["METRICS", "LOGS", "TRACES"], item.source.upper()): cast(
            SourceStatus, item.status.value
        )
        for item in context.source_observations
    }
    return (
        CompactBaseContext(
            alert_title="Service-level anomaly detected around T0.",
            prompt_text=(
                "Investigate the bounded telemetry, identify one visible root-cause "
                "entity and a concise fault type, and cite the evidence used."
            ),
            alert_entity_ref=None,
            entities=base_entities,
            evidence=evidence,
            source_status=status,
        ),
        CompactRetrievalSource(
            entities=entities,
            edges=tuple(
                CompactEdge(
                    source_entity_ref=parent,
                    target_entity_ref=child,
                    edge_type="TRACE_PARENT_CHILD",
                )
                for parent, child in sorted(trace_pairs)
            ),
            source_visibility={
                key: frozenset(value) for key, value in visibility.items()
            },
            source_occurrences={key: dict(value) for key, value in occurrences.items()},
            first_anomaly_time=first_anomaly,
            metrics_ranking=metric_ranking,
            metrics_scores={ref: metric_best[ref] for ref in metric_ranking},
            alert_entities=(),
        ),
    )


def assert_model_context_private(
    base: CompactBaseContext,
    source_key: str,
    *,
    candidate_payload: Mapping[str, object] | None = None,
) -> None:
    payload = {
        "base": base.model_dump(mode="json"),
        "candidates": candidate_payload,
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).casefold()
    forbidden = (
        source_key.casefold(),
        "rca100-case-",
        "re2-ob",
        "re2-ss",
        "task_id",
        "case_id",
        "root_cause_service",
        "ground_truth",
    )
    if any(marker and marker in encoded for marker in forbidden):
        raise ValueError("model-facing compact context contains private identity")


__all__ = [
    "assert_model_context_private",
    "build_obss_compact_inputs",
    "build_rca100_compact_inputs",
    "discover_label_blind_dev_cases",
]
