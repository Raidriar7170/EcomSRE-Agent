"""Bounded, control-blind live observations projected into the frozen A0 schema."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
import json
import math
from typing import Iterable

import tiktoken
from pydantic import Field, field_validator

from ecomsre_live_sandbox.contracts import FrozenModel
from ecomsre_live_sandbox.e2e_contracts import ProjectionConfig
from ecomsre_rca100.contracts import CanonicalRCA100Entity, RCA100MetricsEntityRank
from ecomsre_rca100.projection import (
    RCA100AgentContext,
    RCA100AgentTask,
    RCA100BoundedEvidence,
    RCA100MetricEvidence,
    RCA100MetricsProjection,
    RCA100SourceProjection,
)


_SERVICE = r"^[a-z][a-z0-9._-]{0,127}$"
_CONTROL_MARKERS = (
    "paymentfailure",
    "defaultvariant",
    "37f142fc-9cde-4839-8184-88f2288ceced",
    "restore_frozen_service_configuration",
    "approve ",
    "baseline_document_sha256",
    "fault_document_sha256",
)


class LiveMetricObservation(FrozenModel):
    service_name: str = Field(pattern=_SERVICE)
    baseline_requests: float = Field(gt=0)
    baseline_errors: float = Field(ge=0)
    fault_requests: float = Field(gt=0)
    fault_errors: float = Field(ge=0)
    baseline_p95_ms: float = Field(ge=0)
    fault_p95_ms: float = Field(ge=0)

    @field_validator("baseline_errors", "fault_errors")
    @classmethod
    def _finite_nonnegative(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("metric value must be finite")
        return value


class LiveLogObservation(FrozenModel):
    observed_at: datetime
    service_name: str = Field(pattern=_SERVICE)
    severity: str = Field(min_length=1, max_length=64)
    body: str = Field(min_length=1, max_length=2_000)


class LiveTraceObservation(FrozenModel):
    observed_at: datetime
    service_name: str = Field(pattern=_SERVICE)
    operation: str = Field(min_length=1, max_length=512)
    status: str = Field(min_length=1, max_length=32)
    duration_ms: float = Field(ge=0)


def _entity(service_name: str) -> CanonicalRCA100Entity:
    return CanonicalRCA100Entity(
        entity_ref=f"apm|apm.service|{service_name}",
        domain="apm",
        type="apm.service",
        entity_id=service_name,
        entity_name=service_name,
        normalized_name=service_name,
    )


def scan_model_projection(value: object) -> tuple[str, ...]:
    text = json.dumps(
        value,
        default=lambda item: item.model_dump(mode="json")
        if isinstance(item, FrozenModel)
        else str(item),
        ensure_ascii=False,
        sort_keys=True,
    ).casefold()
    return tuple(marker for marker in _CONTROL_MARKERS if marker in text)


def _require_control_blind(values: Iterable[object]) -> None:
    findings = scan_model_projection(list(values))
    if findings:
        raise ValueError("control truth marker reached model projection")


def _metric_score(item: LiveMetricObservation) -> float:
    baseline_rate = item.baseline_errors / item.baseline_requests
    fault_rate = item.fault_errors / item.fault_requests
    latency_ratio = (item.fault_p95_ms - item.baseline_p95_ms) / max(item.baseline_p95_ms, 1.0)
    score = (fault_rate - baseline_rate) + max(0.0, latency_ratio) * 0.01
    if not math.isfinite(score):
        raise ValueError("metric anomaly score is not finite")
    return score


def _logs_projection(
    logs: tuple[LiveLogObservation, ...],
    visible: set[str],
    projection: ProjectionConfig,
) -> RCA100SourceProjection:
    selected: list[LiveLogObservation] = []
    per_service: dict[str, int] = defaultdict(int)
    severity = {"FATAL": 4, "ERROR": 3, "WARN": 2, "WARNING": 2, "INFO": 1}
    for item in sorted(
        logs[: projection.log_raw_hit_limit],
        key=lambda value: (-severity.get(value.severity.upper(), 0), value.service_name, value.observed_at, value.body),
    ):
        if item.service_name not in visible or per_service[item.service_name] >= projection.log_per_service_limit:
            continue
        selected.append(item)
        per_service[item.service_name] += 1
        if len(selected) == projection.log_evidence_limit:
            break
    evidence = tuple(
        RCA100BoundedEvidence(
            evidence_ref=f"log:{index:04d}",
            entity_ref=_entity(item.service_name).entity_ref,
            name="live-log-anomaly",
            started_at=item.observed_at.timestamp(),
            ended_at=item.observed_at.timestamp(),
            score=float(severity.get(item.severity.upper(), 0)),
            summary=f"service={item.service_name} severity={item.severity.upper()} observed log anomaly.",
        )
        for index, item in enumerate(selected, 1)
    )
    return RCA100SourceProjection(
        source="logs",
        status="AVAILABLE" if evidence else "SOURCE_UNAVAILABLE",
        reason=None if evidence else "NO_VISIBLE_LOG_ANOMALY",
        evidence=evidence,
        total_rows=len(logs),
        window_rows=len(logs),
        mapped_rows=len(selected),
        unmapped_rows=len(logs) - len(selected),
    )


def _traces_projection(
    traces: tuple[LiveTraceObservation, ...],
    visible: set[str],
    projection: ProjectionConfig,
) -> RCA100SourceProjection:
    selected = tuple(
        item
        for item in sorted(
            (value for value in traces if value.service_name in visible),
            key=lambda value: (
                value.status not in {"ERROR", "error"},
                -value.duration_ms,
                value.service_name,
                value.operation,
                value.observed_at,
            ),
        )[: projection.trace_evidence_limit]
    )
    evidence = tuple(
        RCA100BoundedEvidence(
            evidence_ref=f"trace:{index:04d}",
            entity_ref=_entity(item.service_name).entity_ref,
            name="live-trace-diagnostic",
            started_at=item.observed_at.timestamp(),
            ended_at=item.observed_at.timestamp(),
            score=float(item.duration_ms + (1_000.0 if item.status.upper() == "ERROR" else 0.0)),
            summary=(
                f"service={item.service_name} status={item.status.upper()} "
                f"duration_ms={item.duration_ms:.3f}."
            ),
        )
        for index, item in enumerate(selected, 1)
    )
    return RCA100SourceProjection(
        source="traces",
        status="AVAILABLE" if evidence else "SOURCE_UNAVAILABLE",
        reason=None if evidence else "NO_VISIBLE_TRACE_DIAGNOSTIC",
        evidence=evidence,
        total_rows=len(traces),
        window_rows=len(traces),
        mapped_rows=len(selected),
        unmapped_rows=len(traces) - len(selected),
    )


def build_live_a0_context(
    *,
    opaque_case_id: str,
    window_start: datetime,
    window_end: datetime,
    metrics: tuple[LiveMetricObservation, ...],
    logs: tuple[LiveLogObservation, ...],
    traces: tuple[LiveTraceObservation, ...],
    projection: ProjectionConfig | None = None,
) -> RCA100AgentContext:
    if window_end <= window_start:
        raise ValueError("live projection time window is invalid")
    config = projection or ProjectionConfig(
        schema_version="live-e2e.projection.v1",
        visible_entity_minimum=3,
        visible_entity_maximum=8,
        metric_candidate_limit=4,
        log_raw_hit_limit=50,
        log_evidence_limit=16,
        log_per_service_limit=4,
        trace_query_limit=3,
        trace_evidence_limit=20,
        context_token_limit=98304,
        alert_title="Observed purchase-flow request error-rate increase",
    )
    _require_control_blind((*metrics, *logs, *traces))
    if not metrics:
        raise ValueError("live projection lacks observed metric candidates")
    ranked = sorted(metrics, key=lambda item: (-_metric_score(item), item.service_name))[
        : config.metric_candidate_limit
    ]
    observed_services = {
        *(item.service_name for item in metrics),
        *(item.service_name for item in logs),
        *(item.service_name for item in traces),
    }
    ordered_services: list[str] = []
    if "checkout" in observed_services:
        ordered_services.append("checkout")
    for item in ranked:
        if item.service_name not in ordered_services:
            ordered_services.append(item.service_name)
    for log in logs:
        if log.service_name not in ordered_services:
            ordered_services.append(log.service_name)
    for trace in traces:
        if trace.service_name not in ordered_services:
            ordered_services.append(trace.service_name)
    visible_services = tuple(ordered_services[: config.visible_entity_maximum])
    if len(visible_services) < config.visible_entity_minimum:
        raise ValueError("live projection has fewer than three observable services")
    visible = set(visible_services)
    metric_evidence = tuple(
        RCA100MetricEvidence(
            evidence_ref=f"metric:{index:04d}",
            entity_ref=_entity(item.service_name).entity_ref,
            metric="request_error_rate",
            pre_count=3,
            post_count=3,
            pre_mean=item.baseline_errors / item.baseline_requests,
            post_mean=item.fault_errors / item.fault_requests,
            score=_metric_score(item),
            summary=(
                f"service={item.service_name} request_error_rate "
                f"pre={item.baseline_errors / item.baseline_requests:.6f} "
                f"post={item.fault_errors / item.fault_requests:.6f}."
            ),
        )
        for index, item in enumerate(ranked, 1)
        if item.service_name in visible
    )
    metric_ranking = tuple(
        RCA100MetricsEntityRank(
            entity_ref=item.entity_ref,
            rank=index,
            score=item.score,
            supporting_metrics_evidence_refs=(item.evidence_ref,),
        )
        for index, item in enumerate(metric_evidence, 1)
    )
    metrics_projection = RCA100MetricsProjection(
        status="AVAILABLE" if metric_evidence else "METRICS_PROJECTION_UNAVAILABLE",
        evidence=metric_evidence,
        ranking=metric_ranking,
        total_rows=len(metrics),
        window_rows=len(metrics),
        mapped_rows=len(metric_evidence),
        unmapped_rows=len(metrics) - len(metric_evidence),
        valid_series=len(ranked),
        ranked_entities=len(metric_evidence),
    )
    log_projection = _logs_projection(logs, visible, config)
    trace_projection = _traces_projection(traces, visible, config)
    available_sources = int(bool(metric_evidence)) + int(bool(log_projection.evidence)) + int(bool(trace_projection.evidence))
    if available_sources < 2:
        raise ValueError("live projection requires at least two observed source types")
    task = RCA100AgentTask(
        opaque_case_id=opaque_case_id,
        alert_title=config.alert_title,
        prompt_text="Diagnose the observed purchase-flow request error-rate increase from bounded evidence.",
        window_start_timestamp=window_start.timestamp(),
        anchor_timestamp=(window_start.timestamp() + window_end.timestamp()) / 2,
        window_end_timestamp=window_end.timestamp(),
        anchor_source="TASK_WINDOW_MIDPOINT",
        alert_entity_ref=(
            _entity("checkout").entity_ref if "checkout" in visible else None
        ),
    )
    context = RCA100AgentContext(
        task=task,
        visible_entities=tuple(_entity(service) for service in visible_services),
        metrics=metrics_projection,
        logs=log_projection,
        traces=trace_projection,
    )
    encoded = tiktoken.get_encoding("o200k_base").encode(context.model_dump_json())
    if len(encoded) > config.context_token_limit:
        raise ValueError("live A0 context exceeds the frozen token limit")
    findings = scan_model_projection(context.model_dump(mode="json"))
    if findings:
        raise ValueError("control truth marker reached model projection")
    return context


__all__ = [
    "LiveLogObservation",
    "LiveMetricObservation",
    "LiveTraceObservation",
    "build_live_a0_context",
    "scan_model_projection",
]
