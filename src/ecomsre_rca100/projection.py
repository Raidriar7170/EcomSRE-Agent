"""Deterministic label-blind RCA100 source projections."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
import re
from typing import Any, Literal

from pydantic import Field, StrictFloat, StrictInt, model_validator
import pyarrow.parquet as pq  # type: ignore[import-untyped]

from ecomsre_rca100.contracts import (
    CanonicalRCA100Entity,
    RCA100MetricsEntityRank,
    RCA100Model,
)
from ecomsre_rca100.entity import EntityCatalog, load_entity_catalog


_METRICS_COLUMNS = (
    "time",
    "domain",
    "entity_set",
    "entity_id",
    "entity_name",
    "metric",
    "value",
    "metric_set_id",
    "service",
)


class RCA100AgentTask(RCA100Model):
    schema_version: Literal["rca100.agent-task.v1"] = "rca100.agent-task.v1"
    opaque_case_id: str = Field(pattern=r"^rca100-case-[0-9]{4}$")
    alert_title: str = Field(min_length=1, max_length=1_000)
    prompt_text: str = Field(min_length=1, max_length=4_000)
    window_start_timestamp: StrictFloat
    anchor_timestamp: StrictFloat
    window_end_timestamp: StrictFloat
    anchor_source: Literal[
        "TASK_ALERT_TRIGGER",
        "ALERTS_TASK_SCOPED_FIRST_OCCURRED",
        "TASK_WINDOW_MIDPOINT",
    ]
    alert_entity_ref: str | None = Field(default=None, max_length=768)

    @model_validator(mode="after")
    def require_time_order(self) -> RCA100AgentTask:
        if not (
            self.window_start_timestamp
            <= self.anchor_timestamp
            <= self.window_end_timestamp
        ):
            raise ValueError("RCA100 task anchor falls outside its visible window")
        return self


class RCA100MetricEvidence(RCA100Model):
    evidence_ref: str = Field(pattern=r"^metric:[0-9]{4}$")
    entity_ref: str = Field(min_length=5, max_length=768)
    metric: str = Field(min_length=1, max_length=512)
    pre_count: StrictInt = Field(ge=3)
    post_count: StrictInt = Field(ge=3)
    pre_mean: StrictFloat
    post_mean: StrictFloat
    score: StrictFloat
    summary: str = Field(min_length=1, max_length=1_000)


class RCA100MetricsProjection(RCA100Model):
    schema_version: Literal["rca100.metrics-projection.v1"] = (
        "rca100.metrics-projection.v1"
    )
    status: Literal["AVAILABLE", "METRICS_PROJECTION_UNAVAILABLE"]
    evidence: tuple[RCA100MetricEvidence, ...] = Field(default=(), max_length=6)
    ranking: tuple[RCA100MetricsEntityRank, ...] = Field(default=(), max_length=6)
    total_rows: StrictInt = Field(ge=0)
    window_rows: StrictInt = Field(ge=0)
    mapped_rows: StrictInt = Field(ge=0)
    unmapped_rows: StrictInt = Field(ge=0)
    valid_series: StrictInt = Field(ge=0)
    ranked_entities: StrictInt = Field(ge=0)

    @model_validator(mode="after")
    def require_disposition(self) -> RCA100MetricsProjection:
        available = self.status == "AVAILABLE"
        if available != bool(self.ranking) or available != bool(self.evidence):
            raise ValueError("Metrics projection availability differs from ranking")
        if self.mapped_rows + self.unmapped_rows != self.window_rows:
            raise ValueError("Metrics projection row accounting differs")
        return self


class RCA100BoundedEvidence(RCA100Model):
    evidence_ref: str = Field(pattern=r"^(log|trace):[0-9]{4}$")
    entity_ref: str = Field(min_length=5, max_length=768)
    name: str = Field(min_length=1, max_length=512)
    started_at: StrictFloat
    ended_at: StrictFloat
    score: StrictFloat
    summary: str = Field(min_length=1, max_length=2_000)


class RCA100SourceProjection(RCA100Model):
    schema_version: Literal["rca100.source-projection.v1"] = (
        "rca100.source-projection.v1"
    )
    source: Literal["logs", "traces"]
    status: Literal["AVAILABLE", "SOURCE_UNAVAILABLE"]
    reason: str | None = Field(default=None, max_length=256)
    evidence: tuple[RCA100BoundedEvidence, ...] = Field(default=(), max_length=6)
    total_rows: StrictInt = Field(ge=0)
    window_rows: StrictInt = Field(ge=0)
    mapped_rows: StrictInt = Field(ge=0)
    unmapped_rows: StrictInt = Field(ge=0)

    @model_validator(mode="after")
    def require_source_disposition(self) -> RCA100SourceProjection:
        if self.mapped_rows + self.unmapped_rows != self.window_rows:
            raise ValueError("source projection row accounting differs")
        if self.status == "SOURCE_UNAVAILABLE" and (
            self.evidence or self.reason is None
        ):
            raise ValueError("unavailable source retained evidence or lacks reason")
        return self


class RCA100AgentContext(RCA100Model):
    schema_version: Literal["rca100.agent-context.v1"] = "rca100.agent-context.v1"
    task: RCA100AgentTask
    visible_entities: tuple[CanonicalRCA100Entity, ...] = Field(
        min_length=1, max_length=64
    )
    metrics: RCA100MetricsProjection
    logs: RCA100SourceProjection
    traces: RCA100SourceProjection

    @model_validator(mode="after")
    def require_visible_references(self) -> RCA100AgentContext:
        refs = {item.entity_ref for item in self.visible_entities}
        metric_refs = {item.entity_ref for item in self.metrics.evidence}
        log_refs = {item.entity_ref for item in self.logs.evidence}
        trace_refs = {item.entity_ref for item in self.traces.evidence}
        if not (metric_refs | log_refs | trace_refs).issubset(refs):
            raise ValueError("bounded evidence entity is not model-visible")
        return self


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for key, value in pairs:
        if key in output:
            raise ValueError("RCA100 task contains a duplicate JSON key")
        output[key] = value
    return output


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
        number /= 1e9
    elif number >= 1e14:
        number /= 1e6
    elif number >= 1e11:
        number /= 1e3
    return number


def _alerts_anchor(path: Path) -> float | None:
    if path.is_symlink() or not path.is_file():
        return None
    parquet = pq.ParquetFile(path)
    available = tuple(
        name for name in ("time_s", "time", "timestamp") if name in parquet.schema.names
    )
    if not available:
        return None
    table = parquet.read(columns=list(available))
    timestamps: list[float] = []
    for row in table.to_pylist():
        for column in available:
            parsed = _parse_timestamp(row.get(column))
            if parsed is not None:
                timestamps.append(parsed)
                break
    return min(timestamps) if timestamps else None


def load_agent_task(
    case_root: Path,
    *,
    opaque_case_id: str,
    catalog: EntityCatalog,
) -> RCA100AgentTask:
    task_path = case_root / "task.json"
    if task_path.is_symlink() or not task_path.is_file():
        raise ValueError("RCA100 task must be a regular non-symlink file")
    raw = json.loads(
        task_path.read_text(encoding="utf-8"), object_pairs_hook=_strict_object
    )
    if not isinstance(raw, dict) or not isinstance(raw.get("alert_window"), dict):
        raise ValueError("RCA100 task schema is invalid")
    start = _parse_timestamp(raw["alert_window"].get("start"))
    end = _parse_timestamp(raw["alert_window"].get("end"))
    if start is None or end is None or start >= end:
        raise ValueError("RCA100 task window is invalid")
    anchor = _parse_timestamp(raw.get("alert_trigger_time"))
    anchor_source: Literal[
        "TASK_ALERT_TRIGGER",
        "ALERTS_TASK_SCOPED_FIRST_OCCURRED",
        "TASK_WINDOW_MIDPOINT",
    ]
    if anchor is not None:
        anchor_source = "TASK_ALERT_TRIGGER"
    else:
        anchor = _alerts_anchor(case_root / "alerts.parquet")
        if anchor is not None:
            anchor_source = "ALERTS_TASK_SCOPED_FIRST_OCCURRED"
        else:
            anchor = (start + end) / 2
            anchor_source = "TASK_WINDOW_MIDPOINT"
    alert = raw.get("alert_entity")
    entity = None
    if isinstance(alert, dict):
        entity = catalog.resolve_exact(
            entity_id=(str(alert.get("entity_id")) if alert.get("entity_id") else None),
            entity_type=(
                str(alert.get("entity_type")) if alert.get("entity_type") else None
            ),
            entity_name=(
                str(alert.get("entity_name")) if alert.get("entity_name") else None
            ),
        )
    task = RCA100AgentTask(
        opaque_case_id=opaque_case_id,
        alert_title=(
            str(raw.get("alert_title", "")).strip()
            or "Alert title unavailable."
        ),
        prompt_text=str(raw.get("prompt_text", "")).strip(),
        window_start_timestamp=start,
        anchor_timestamp=anchor,
        window_end_timestamp=end,
        anchor_source=anchor_source,
        alert_entity_ref=None if entity is None else entity.entity_ref,
    )
    source_task_id = raw.get("task_id")
    if isinstance(source_task_id, str) and source_task_id in task.model_dump_json():
        raise ValueError("RCA100 Agent task leaked the source task identity")
    return task


@dataclass(frozen=True, slots=True)
class _MetricCandidate:
    entity_ref: str
    metric: str
    metric_set_id: str
    service: str
    pre_count: int
    post_count: int
    pre_mean: float
    post_mean: float
    score: float
    tie_digest: bytes

    @property
    def series_key(self) -> tuple[str, str, str, str]:
        return (self.entity_ref, self.metric, self.metric_set_id, self.service)


def _candidate_order(candidate: _MetricCandidate) -> tuple[float, bytes, tuple[str, ...]]:
    return (-candidate.score, candidate.tie_digest, candidate.series_key)


def project_metrics(
    path: Path,
    *,
    task: RCA100AgentTask,
    catalog: EntityCatalog,
) -> RCA100MetricsProjection:
    if path.is_symlink() or not path.is_file():
        raise ValueError("RCA100 Metrics source must be a regular non-symlink file")
    parquet = pq.ParquetFile(path)
    if tuple(parquet.schema.names) != _METRICS_COLUMNS:
        raise ValueError("RCA100 Metrics long-format schema differs from the lock")
    table = parquet.read()
    series: dict[tuple[str, str, str, str], tuple[list[float], list[float]]] = {}
    window_rows = mapped_rows = unmapped_rows = 0
    for row in table.to_pylist():
        timestamp_raw = row.get("time")
        value_raw = row.get("value")
        if type(timestamp_raw) is not int:
            raise ValueError("RCA100 Metrics row types differ from the lock")
        timestamp = float(timestamp_raw) / 1_000_000.0
        if not task.window_start_timestamp <= timestamp <= task.window_end_timestamp:
            continue
        window_rows += 1
        if not isinstance(value_raw, (int, float)):
            unmapped_rows += 1
            continue
        entity = catalog.resolve_metric_entity(
            entity_id=str(row.get("entity_id") or ""),
            entity_set=str(row.get("entity_set") or ""),
            entity_name=str(row.get("entity_name") or ""),
            service=str(row.get("service") or ""),
        )
        if entity is None:
            unmapped_rows += 1
            continue
        value = float(value_raw)
        if not math.isfinite(value):
            unmapped_rows += 1
            continue
        mapped_rows += 1
        key = (
            entity.entity_ref,
            str(row.get("metric") or ""),
            str(row.get("metric_set_id") or ""),
            str(row.get("service") or ""),
        )
        if not key[1]:
            raise ValueError("RCA100 Metrics row lacks a metric name")
        pre, post = series.setdefault(key, ([], []))
        (pre if timestamp < task.anchor_timestamp else post).append(value)

    candidates: list[_MetricCandidate] = []
    for key, (pre, post) in series.items():
        if len(pre) < 3 or len(post) < 3:
            continue
        pre_mean = sum(pre) / len(pre)
        post_mean = sum(post) / len(post)
        score = abs(post_mean - pre_mean) / max(abs(pre_mean), 1e-9)
        if not math.isfinite(score):
            continue
        tie_digest = hashlib.sha256(
            b"\0".join(
                (
                    b"rca100-metrics-f0-tie-v1",
                    task.opaque_case_id.encode("ascii"),
                    *(item.encode("utf-8") for item in key),
                )
            )
        ).digest()
        candidates.append(
            _MetricCandidate(
                entity_ref=key[0],
                metric=key[1],
                metric_set_id=key[2],
                service=key[3],
                pre_count=len(pre),
                post_count=len(post),
                pre_mean=float(pre_mean),
                post_mean=float(post_mean),
                score=float(score),
                tie_digest=tie_digest,
            )
        )
    best_by_entity: dict[str, _MetricCandidate] = {}
    for candidate in sorted(candidates, key=_candidate_order):
        best_by_entity.setdefault(candidate.entity_ref, candidate)
    ranked = tuple(sorted(best_by_entity.values(), key=_candidate_order)[:6])
    evidence = tuple(
        RCA100MetricEvidence(
            evidence_ref=f"metric:{index:04d}",
            entity_ref=item.entity_ref,
            metric=item.metric,
            pre_count=item.pre_count,
            post_count=item.post_count,
            pre_mean=item.pre_mean,
            post_mean=item.post_mean,
            score=item.score,
            summary=(
                f"{item.metric} pre-mean={item.pre_mean:.6g}, "
                f"post-mean={item.post_mean:.6g}, F0={item.score:.6g}."
            ),
        )
        for index, item in enumerate(ranked, 1)
    )
    ranking = tuple(
        RCA100MetricsEntityRank(
            entity_ref=item.entity_ref,
            rank=index,
            score=item.score,
            supporting_metrics_evidence_refs=(f"metric:{index:04d}",),
        )
        for index, item in enumerate(ranked, 1)
    )
    return RCA100MetricsProjection(
        status="AVAILABLE" if ranking else "METRICS_PROJECTION_UNAVAILABLE",
        evidence=evidence,
        ranking=ranking,
        total_rows=table.num_rows,
        window_rows=window_rows,
        mapped_rows=mapped_rows,
        unmapped_rows=unmapped_rows,
        valid_series=len(candidates),
        ranked_entities=len(best_by_entity),
    )


def _unavailable_source(
    source: Literal["logs", "traces"], total_rows: int, reason: str
) -> RCA100SourceProjection:
    return RCA100SourceProjection(
        source=source,
        status="SOURCE_UNAVAILABLE",
        reason=reason,
        total_rows=total_rows,
        window_rows=0,
        mapped_rows=0,
        unmapped_rows=0,
    )


@dataclass(slots=True)
class _LogPattern:
    count: int
    started_at: float
    ended_at: float
    sample: str


def project_logs(
    path: Path,
    *,
    task: RCA100AgentTask,
    catalog: EntityCatalog,
) -> RCA100SourceProjection:
    if path.is_symlink() or not path.is_file():
        return _unavailable_source("logs", 0, "SOURCE_FILE_UNAVAILABLE")
    parquet = pq.ParquetFile(path)
    columns = set(parquet.schema.names)
    required = {"content", "_time_", "_pod_uid_", "_pod_name_", "_container_name_"}
    if not required.issubset(columns):
        return _unavailable_source(
            "logs", parquet.metadata.num_rows, "SOURCE_SCHEMA_UNAVAILABLE"
        )
    grouped: dict[tuple[str, str], _LogPattern] = {}
    window_rows = mapped_rows = unmapped_rows = 0
    selected = sorted(required)
    for batch in parquet.iter_batches(batch_size=65_536, columns=selected):
        for row in batch.to_pylist():
            timestamp = _parse_timestamp(row.get("_time_"))
            if timestamp is None or not (
                task.window_start_timestamp <= timestamp <= task.window_end_timestamp
            ):
                continue
            window_rows += 1
            entity = catalog.resolve_log_entity(
                pod_uid=str(row.get("_pod_uid_") or ""),
                pod_name=str(row.get("_pod_name_") or ""),
                container_name=str(row.get("_container_name_") or ""),
            )
            if entity is None:
                unmapped_rows += 1
                continue
            mapped_rows += 1
            content = str(row.get("content") or "")
            pattern = re.sub(r"\b\d+\b", "<n>", content.casefold())[:512]
            key = (entity.entity_ref, pattern)
            current = grouped.get(key)
            if current is None:
                grouped[key] = _LogPattern(1, timestamp, timestamp, content[:1_800])
            else:
                current.count += 1
                current.started_at = min(current.started_at, timestamp)
                current.ended_at = max(current.ended_at, timestamp)
    ranked = sorted(grouped.items(), key=lambda item: (-item[1].count, item[0]))[:6]
    evidence = tuple(
        RCA100BoundedEvidence(
            evidence_ref=f"log:{index:04d}",
            entity_ref=key[0],
            name="log-pattern",
            started_at=values.started_at,
            ended_at=values.ended_at,
            score=float(values.count),
            summary=(
                f"count={values.count} pattern sample: "
                f"{values.sample or 'empty'}"
            ),
        )
        for index, (key, values) in enumerate(ranked, 1)
    )
    return RCA100SourceProjection(
        source="logs",
        status="AVAILABLE",
        evidence=evidence,
        total_rows=parquet.metadata.num_rows,
        window_rows=window_rows,
        mapped_rows=mapped_rows,
        unmapped_rows=unmapped_rows,
    )


def project_traces(
    path: Path,
    *,
    task: RCA100AgentTask,
    catalog: EntityCatalog,
) -> RCA100SourceProjection:
    if path.is_symlink() or not path.is_file():
        return _unavailable_source("traces", 0, "SOURCE_FILE_UNAVAILABLE")
    parquet = pq.ParquetFile(path)
    columns = set(parquet.schema.names)
    required = {"startTime", "duration", "serviceName", "statusCode"}
    if not required.issubset(columns):
        return _unavailable_source(
            "traces", parquet.metadata.num_rows, "SOURCE_SCHEMA_UNAVAILABLE"
        )
    grouped: dict[str, list[float]] = {}
    window_rows = mapped_rows = unmapped_rows = 0
    for batch in parquet.iter_batches(batch_size=65_536, columns=sorted(required)):
        for row in batch.to_pylist():
            try:
                timestamp = float(str(row.get("startTime") or "")) / 1_000_000_000.0
            except ValueError:
                timestamp = None
            if timestamp is None or not (
                task.window_start_timestamp <= timestamp <= task.window_end_timestamp
            ):
                continue
            window_rows += 1
            entity = catalog.resolve_trace_entity(
                service_name=str(row.get("serviceName") or "")
            )
            try:
                duration = float(str(row.get("duration") or ""))
            except ValueError:
                duration = math.nan
            if entity is None or not math.isfinite(duration):
                unmapped_rows += 1
                continue
            mapped_rows += 1
            # count_pre, sum_pre, count_post, sum_post, errors, min_ts, max_ts
            values = grouped.setdefault(
                entity.entity_ref,
                [0.0, 0.0, 0.0, 0.0, 0.0, timestamp, timestamp],
            )
            if timestamp < task.anchor_timestamp:
                values[0] += 1
                values[1] += duration
            else:
                values[2] += 1
                values[3] += duration
            status = str(row.get("statusCode") or "0").casefold()
            if status not in {"0", "false", "ok", "unset"}:
                values[4] += 1
            values[5] = min(values[5], timestamp)
            values[6] = max(values[6], timestamp)
    candidates: list[tuple[float, str, list[float], float, float]] = []
    for entity_ref, values in grouped.items():
        if values[0] < 1 or values[2] < 1:
            continue
        pre_mean = values[1] / values[0]
        post_mean = values[3] / values[2]
        latency_score = abs(post_mean - pre_mean) / max(abs(pre_mean), 1e-9)
        score = latency_score + values[4] / (values[0] + values[2])
        if math.isfinite(score):
            candidates.append((score, entity_ref, values, pre_mean, post_mean))
    candidates.sort(key=lambda item: (-item[0], item[1]))
    evidence = tuple(
        RCA100BoundedEvidence(
            evidence_ref=f"trace:{index:04d}",
            entity_ref=entity_ref,
            name="trace-diagnostics",
            started_at=values[5],
            ended_at=values[6],
            score=score,
            summary=(
                f"trace duration pre-mean={pre_mean:.6g}, "
                f"post-mean={post_mean:.6g}, errors={values[4]:.0f}, "
                f"combined-score={score:.6g}."
            ),
        )
        for index, (score, entity_ref, values, pre_mean, post_mean) in enumerate(
            candidates[:6], 1
        )
    )
    return RCA100SourceProjection(
        source="traces",
        status="AVAILABLE",
        evidence=evidence,
        total_rows=parquet.metadata.num_rows,
        window_rows=window_rows,
        mapped_rows=mapped_rows,
        unmapped_rows=unmapped_rows,
    )


def build_agent_context(
    case_root: Path, *, opaque_case_id: str
) -> RCA100AgentContext:
    catalog = load_entity_catalog(case_root / "topology.json")
    task = load_agent_task(
        case_root, opaque_case_id=opaque_case_id, catalog=catalog
    )
    metrics = project_metrics(case_root / "metrics.parquet", task=task, catalog=catalog)
    logs = project_logs(case_root / "logs.parquet", task=task, catalog=catalog)
    traces = project_traces(case_root / "traces.parquet", task=task, catalog=catalog)
    prioritized: list[str] = []
    for entity_ref in (
        task.alert_entity_ref,
        *(item.entity_ref for item in metrics.evidence),
        *(item.entity_ref for item in logs.evidence),
        *(item.entity_ref for item in traces.evidence),
    ):
        if entity_ref is not None and entity_ref not in prioritized:
            prioritized.append(entity_ref)
    index = 0
    while index < len(prioritized):
        entity = catalog.by_ref[prioritized[index]]
        related = (entity.parent_service_ref_or_none, *entity.same_as_refs)
        for entity_ref in related:
            if entity_ref is not None and entity_ref not in prioritized:
                prioritized.append(entity_ref)
        index += 1
    if not prioritized:
        prioritized.extend(sorted(catalog.by_ref)[:64])
    return RCA100AgentContext(
        task=task,
        visible_entities=tuple(catalog.by_ref[item] for item in prioritized[:64]),
        metrics=metrics,
        logs=logs,
        traces=traces,
    )


__all__ = [
    "RCA100AgentTask",
    "RCA100AgentContext",
    "RCA100BoundedEvidence",
    "RCA100MetricEvidence",
    "RCA100MetricsProjection",
    "RCA100SourceProjection",
    "build_agent_context",
    "load_agent_task",
    "project_metrics",
    "project_logs",
    "project_traces",
]
