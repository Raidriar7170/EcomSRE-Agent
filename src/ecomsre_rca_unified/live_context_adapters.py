"""Label-blind RCA100 and OB/SS adapters for the paired live comparison."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
import csv
import json
import math
from pathlib import Path
import re
from typing import Literal, cast

from ecomsre_rca100.entity import load_entity_catalog
from ecomsre_rca100.projection import RCA100AgentContext, build_agent_context
from ecomsre_rcaeval.adapter import ArchitectureContextBuilder
from ecomsre_rcaeval.contracts import Architecture
from ecomsre_rcaeval.dataset import TelemetryCase
from ecomsre_rca_unified.contracts import CanonicalEntityLayer
from ecomsre_rca_unified.hierarchical_context import (
    EvidenceItem,
    EvidenceSource,
    HierarchySource,
    HierarchicalContext,
    LiveBaseContext,
    LiveEntity,
    RelationSource,
    SourceStatus,
)
from ecomsre_rca_unified.hierarchy import normalize_entity_layer
from ecomsre_rca_unified.live_rca100_scan import (
    read_live_rca_topology,
    scan_live_rca_evidence,
)


def _regular(path: Path, label: str) -> Path:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"label-blind OB/SS case requires a regular {label}")
    return path


def discover_label_blind_dev_cases(
    root: Path, *, system: Literal["RE2-OB", "RE2-SS"]
) -> tuple[TelemetryCase, ...]:
    """Discover telemetry by stable ordinal without parsing label-bearing paths."""

    if root.name != system or root.is_symlink() or not root.is_dir():
        raise ValueError("label-blind OB/SS dataset root is invalid")
    groups = tuple(sorted(path for path in root.iterdir() if path.is_dir()))
    output: list[TelemetryCase] = []
    for group in groups:
        if group.is_symlink():
            raise ValueError("label-blind OB/SS case group is a symlink")
        instance_roots = tuple(
            sorted(
                path
                for path in group.iterdir()
                if path.is_dir() and path.name in {"1", "2", "3"}
            )
        )
        for case_root in instance_roots:
            if case_root.is_symlink():
                raise ValueError("label-blind OB/SS case root is a symlink")
            metrics_candidates = tuple(
                path
                for path in (
                    case_root / "simple_metrics.csv",
                    case_root / "data.csv",
                )
                if path.exists()
            )
            if len(metrics_candidates) != 1:
                raise ValueError("label-blind OB/SS case metrics are invalid")
            traces_candidate = case_root / "traces.csv"
            if system == "RE2-OB":
                traces_path: Path | None = _regular(
                    traces_candidate, "traces.csv"
                )
            elif traces_candidate.exists():
                raise ValueError("label-blind RE2-SS unexpectedly contains traces")
            else:
                traces_path = None
            inject_path = _regular(case_root / "inject_time.txt", "inject_time.txt")
            try:
                inject_time = int(inject_path.read_text(encoding="utf-8").strip())
            except (OSError, UnicodeError, ValueError) as error:
                raise ValueError("label-blind OB/SS inject time is invalid") from error
            if inject_time < 0:
                raise ValueError("label-blind OB/SS inject time is negative")
            output.append(
                TelemetryCase(
                    case_id=f"{system.casefold()}-case-{len(output) + 1:04d}",
                    system=system,
                    root=case_root,
                    metrics_path=_regular(metrics_candidates[0], "metrics CSV"),
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
    if not normalized or re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,127}", normalized) is None:
        raise ValueError("OB/SS visible service has an invalid canonical name")
    return f"apm|apm.service|{normalized}"


def _source_name(evidence_ref: str) -> Literal["METRICS", "LOGS", "TRACES"]:
    return cast(
        Literal["METRICS", "LOGS", "TRACES"],
        {
            "metric": "METRICS",
            "log": "LOGS",
            "trace": "TRACES",
        }[evidence_ref.partition(":")[0]],
    )


def _base_evidence_from_rca100(
    context: RCA100AgentContext,
) -> tuple[EvidenceItem, ...]:
    output: list[EvidenceItem] = []
    projections = (
        ("METRICS", context.metrics),
        ("LOGS", context.logs),
        ("TRACES", context.traces),
    )
    for raw_source_name, projection in projections:
        source_name = cast(Literal["METRICS", "LOGS", "TRACES"], raw_source_name)
        for item in projection.evidence:
            started_at = getattr(item, "started_at", None)
            ended_at = getattr(item, "ended_at", None)
            output.append(
                EvidenceItem(
                    evidence_ref=item.evidence_ref,
                    source=source_name,
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


def _strict_temporal_relations(
    first_source: Mapping[str, tuple[float, EvidenceSource]],
) -> tuple[RelationSource, ...]:
    observed = sorted(
        (timestamp, entity_ref)
        for entity_ref, (timestamp, _source) in first_source.items()
    )
    return tuple(
        RelationSource(
            source_entity_ref=left[1],
            target_entity_ref=right[1],
            relation_type="FIRST_OBSERVED_BEFORE",
        )
        for left, right in zip(observed, observed[1:], strict=False)
        if left[0] < right[0] and left[1] != right[1]
    )


def build_rca100_live_inputs(
    case_root: Path,
    *,
    projection_case_number: int,
    methodology: Mapping[str, object],
) -> tuple[LiveBaseContext, HierarchySource]:
    """Project one source task without preserving its task or benchmark identity."""

    if not 1 <= projection_case_number <= 9_999:
        raise ValueError("RCA100 projection case number is invalid")
    context = build_agent_context(
        case_root,
        opaque_case_id=f"rca100-case-{projection_case_number:04d}",
    )
    base_entities = tuple(
        LiveEntity(
            entity_ref=item.entity_ref,
            entity_name=item.entity_name,
            layer=normalize_entity_layer(item.type),
            service_ancestor_or_none=(
                item.entity_ref
                if normalize_entity_layer(item.type) is CanonicalEntityLayer.SERVICE
                else item.parent_service_ref_or_none
            ),
            parent_ref_or_none=item.parent_service_ref_or_none,
        )
        for item in context.visible_entities
    )
    base = LiveBaseContext(
        alert_title=context.task.alert_title,
        prompt_text=context.task.prompt_text,
        alert_entity_ref=context.task.alert_entity_ref,
        entities=base_entities,
        evidence=_base_evidence_from_rca100(context),
        source_status={
            "METRICS": (
                "AVAILABLE"
                if context.metrics.status == "AVAILABLE"
                else "SOURCE_UNAVAILABLE"
            ),
            "LOGS": context.logs.status,
            "TRACES": context.traces.status,
        },
    )

    catalog = load_entity_catalog(case_root / "topology.json")
    topology = read_live_rca_topology(case_root / "topology.json")
    scan = scan_live_rca_evidence(
        case_root,
        task=context.task,
        catalog=catalog,
        methodology=methodology,
    )
    source_entities = tuple(
        LiveEntity(
            entity_ref=node.entity_ref,
            entity_name=catalog.by_ref[node.entity_ref].entity_name,
            layer=node.layer,
            service_ancestor_or_none=(
                node.entity_ref
                if node.layer is CanonicalEntityLayer.SERVICE
                else catalog.by_ref[node.entity_ref].parent_service_ref_or_none
            ),
            parent_ref_or_none=(
                next(
                    (
                        parent
                        for child, parent in topology.parent_edges
                        if child == node.entity_ref
                    ),
                    catalog.by_ref[node.entity_ref].parent_service_ref_or_none,
                )
            ),
        )
        for node in topology.nodes
    )
    visibility: dict[str, set[EvidenceSource]] = defaultdict(set)
    first_source: dict[str, tuple[float, EvidenceSource]] = {}
    for entity_ref in scan.metrics_entities:
        visibility[entity_ref].add("METRICS")
    for entity_ref in scan.logs_entities:
        visibility[entity_ref].add("LOGS")
    for entity_ref in scan.traces_entities:
        visibility[entity_ref].add("TRACES")
    if base.alert_entity_ref is not None:
        visibility[base.alert_entity_ref].add("ALERTS")
    for entity_ref in scan.events_entities:
        visibility[entity_ref].add("EVENTS")
    for entity_ref in scan.alerts_entities:
        visibility[entity_ref].add("ALERTS")
    source_names: dict[str, EvidenceSource] = {
        "metrics": "METRICS",
        "logs": "LOGS",
        "traces": "TRACES",
        "events": "EVENTS",
        "alerts": "ALERTS",
    }
    for entity_ref, observations in scan.first_by_entity_source.items():
        for raw_source, timestamp in observations.items():
            source_name = source_names.get(raw_source)
            if source_name is None:
                raise ValueError("RCA100 anomaly scan returned an unknown source")
            visibility[entity_ref].add(source_name)
            candidate = (timestamp, source_name)
            current = first_source.get(entity_ref)
            if current is None or candidate < current:
                first_source[entity_ref] = candidate
    topology_relations = [
        RelationSource(
            source_entity_ref=source_ref,
            target_entity_ref=target_ref,
            relation_type="DIRECTED_TOPOLOGY",
        )
        for source_ref, target_ref in topology.directed_edges
    ]
    topology_relations.extend(
        RelationSource(
            source_entity_ref=source_ref,
            target_entity_ref=target_ref,
            relation_type="EXPLICIT_DEPENDENCY",
        )
        for source_ref, target_ref in topology.explicit_dependency_edges
    )
    topology_relations.extend(
        RelationSource(
            source_entity_ref=source_ref,
            target_entity_ref=target_ref,
            relation_type="UNDIRECTED",
        )
        for source_ref, target_ref in topology.undirected_edges
    )
    topology_relations.extend(
        RelationSource(
            source_entity_ref=source_ref,
            target_entity_ref=target_ref,
            relation_type="UNKNOWN",
        )
        for source_ref, target_ref in topology.unknown_edges
    )
    propagation_relations = [
        RelationSource(
            source_entity_ref=source_ref,
            target_entity_ref=target_ref,
            relation_type="TRACE_PARENT_CHILD",
        )
        for source_ref, target_ref in scan.trace_directed_edges
    ]
    propagation_relations.extend(_strict_temporal_relations(first_source))
    return base, HierarchySource(
        entities=source_entities,
        parent_edges=topology.parent_edges,
        topology_edges=tuple(topology_relations),
        propagation_edges=tuple(propagation_relations),
        source_visibility={
            key: frozenset(value) for key, value in visibility.items()
        },
        first_anomaly_source={
            key: value[1] for key, value in first_source.items()
        },
    )


def _score_from_summary(summary: str) -> float:
    match = re.search(r"(?:anomaly|combined)-score=([-+0-9.eE]+)", summary)
    if match is None:
        return 0.0
    try:
        return float(match.group(1))
    except ValueError:
        return 0.0


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
        value = float(raw) if raw is not None else float("nan")
    except ValueError:
        return None
    if not math.isfinite(value):
        return None
    if value >= 1e17:
        return value / 1e9
    if value >= 1e14:
        return value / 1e6
    if value >= 1e11:
        return value / 1e3
    return value


def _bounded_obss_visibility(
    case: TelemetryCase,
) -> dict[str, frozenset[EvidenceSource]]:
    lower = case.inject_time - 600
    upper = case.inject_time + 600
    visibility: dict[str, set[EvidenceSource]] = defaultdict(set)
    with case.metrics_path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise ValueError("OB/SS metrics require a header")
        metric_names = tuple(name for name in reader.fieldnames if name != "time")
        if any("_" not in name for name in metric_names):
            raise ValueError("OB/SS metric service projection is invalid")
        metric_services = {name.rsplit("_", 1)[0] for name in metric_names}
        if any(
            lower <= timestamp <= upper
            for row in reader
            if (timestamp := _obss_timestamp(row, ("time",))) is not None
        ):
            for service in metric_services:
                visibility[_service_ref(service)].add("METRICS")
    with case.logs_path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise ValueError("OB/SS logs require a header")
        for row in reader:
            timestamp = _obss_timestamp(row, ("timestamp", "time"))
            log_service = _obss_column(
                row, ("service", "serviceName", "container_name")
            )
            if timestamp is not None and lower <= timestamp <= upper and log_service:
                visibility[_service_ref(log_service)].add("LOGS")
    if case.traces_path is not None:
        with case.traces_path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames is None:
                raise ValueError("OB/SS traces require a header")
            for row in reader:
                timestamp = _obss_timestamp(
                    row,
                    (
                        "startTimeMillis",
                        "startTime",
                        "start_time",
                        "timestamp",
                        "time",
                    ),
                )
                trace_service = _obss_column(row, ("service", "serviceName"))
                if (
                    timestamp is not None
                    and lower <= timestamp <= upper
                    and trace_service
                ):
                    visibility[_service_ref(trace_service)].add("TRACES")
    return {key: frozenset(value) for key, value in visibility.items()}


def _trace_relations(
    case: TelemetryCase, known_refs: set[str]
) -> tuple[RelationSource, ...]:
    if case.traces_path is None:
        return ()
    span_service: dict[tuple[str, str], str] = {}
    parents: list[tuple[tuple[str, str], tuple[str, str]]] = []
    with case.traces_path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            timestamp = _obss_timestamp(
                row,
                (
                    "startTimeMillis",
                    "startTime",
                    "start_time",
                    "timestamp",
                    "time",
                ),
            )
            if (
                timestamp is None
                or timestamp < case.inject_time - 600
                or timestamp > case.inject_time + 600
            ):
                continue
            service = (row.get("serviceName") or row.get("service") or "").strip()
            trace_id = row.get("traceID") or row.get("traceId") or ""
            span_id = row.get("spanID") or row.get("spanId") or ""
            parent_id = row.get("parentSpanID") or row.get("parentSpanId") or ""
            if not service or not trace_id or not span_id:
                continue
            service_ref = _service_ref(service)
            if service_ref not in known_refs:
                continue
            span_service[(trace_id, span_id)] = service_ref
            if parent_id:
                parents.append(((trace_id, span_id), (trace_id, parent_id)))
    output = {
        (parent, child)
        for child_key, parent_key in parents
        if (child := span_service.get(child_key)) is not None
        and (parent := span_service.get(parent_key)) is not None
        and child != parent
    }
    return tuple(
        RelationSource(
            source_entity_ref=parent,
            target_entity_ref=child,
            relation_type="TRACE_PARENT_CHILD",
        )
        for parent, child in sorted(output)
    )


def build_obss_live_inputs(
    case: TelemetryCase,
) -> tuple[LiveBaseContext, HierarchySource]:
    """Strip evaluator labels before constructing the model-facing OB/SS view."""

    builder = ArchitectureContextBuilder(case, Architecture.SINGLE)
    for source in cast(
        tuple[Literal["metrics", "logs", "traces"], ...],
        ("metrics", "logs", "traces"),
    ):
        builder.query_source(source)
    context = builder.snapshot()
    base_services = tuple(
        sorted({item.service for item in context.evidence if item.service != "unknown"})
    )
    if not base_services:
        raise ValueError("OB/SS bounded context contains no visible service")
    visibility = _bounded_obss_visibility(case)
    source_services = tuple(
        sorted(
            {
                ref.rsplit("|", 1)[-1]
                for ref in visibility
            }
            | set(base_services)
        )
    )
    base_entities = tuple(
        LiveEntity(
            entity_ref=_service_ref(service),
            entity_name=service,
            layer=CanonicalEntityLayer.SERVICE,
            service_ancestor_or_none=_service_ref(service),
            parent_ref_or_none=None,
        )
        for service in base_services
    )
    source_entities = tuple(
        LiveEntity(
            entity_ref=_service_ref(service),
            entity_name=service,
            layer=CanonicalEntityLayer.SERVICE,
            service_ancestor_or_none=_service_ref(service),
            parent_ref_or_none=None,
        )
        for service in source_services
    )
    evidence = tuple(
        EvidenceItem(
            evidence_ref=item.evidence_id,
            source=_source_name(item.evidence_id),
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
    status: dict[
        Literal["METRICS", "LOGS", "TRACES"], SourceStatus
    ] = {
        cast(Literal["METRICS", "LOGS", "TRACES"], item.source.upper()): cast(
            SourceStatus, item.status.value
        )
        for item in context.source_observations
    }
    base = LiveBaseContext(
        alert_title="Service-level anomaly detected around T0.",
        prompt_text=(
            "Investigate the bounded telemetry, identify one visible root-cause "
            "entity and a concise fault type, and cite the evidence used."
        ),
        alert_entity_ref=None,
        entities=base_entities,
        evidence=evidence,
        source_status=status,
    )
    propagation_relations = list(
        _trace_relations(case, {item.entity_ref for item in source_entities})
    )
    return base, HierarchySource(
        entities=source_entities,
        parent_edges=(),
        topology_edges=(),
        propagation_edges=tuple(propagation_relations),
        source_visibility=visibility,
        first_anomaly_source={},
    )


def assert_model_context_private(
    base: LiveBaseContext,
    source_key: str,
    hierarchy: HierarchicalContext | None = None,
) -> None:
    """Reject benchmark/source identity metadata before Provider construction."""

    payload = {
        "base": base.model_dump(mode="json"),
        "hierarchy": (
            None if hierarchy is None else hierarchy.model_dump(mode="json")
        ),
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
        raise ValueError("model-facing context contains private identity metadata")


__all__ = [
    "assert_model_context_private",
    "build_obss_live_inputs",
    "build_rca100_live_inputs",
    "discover_label_blind_dev_cases",
]
