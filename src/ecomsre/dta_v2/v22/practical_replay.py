"""Truth-independent normalization of public v2/v2.1 replay captures."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, cast

from pydantic import Field

from ecomsre.dta_v2.v22.read_contracts import (
    DtaModelV22,
    EvidenceSourceV22,
    LogRecordV22,
    METRIC_UNIT_BY_KIND_V22,
    MetricFactV22,
    MetricKindV22,
    MetricSupportStatusV22,
    ReadSourceStatusV22,
    ResourceSampleV22,
    ResourceUsageRecordV22,
    RuntimeRecordV22,
    RuntimeStateV22,
    Sha256V22,
    SpanStatusV22,
    TraceSpanV22,
)
from ecomsre.dta_v2.v22.replay import ReplayCaptureV22, ReplaySourceFailureV22


_SOURCE_BY_TOOL_V22 = {
    "query_metrics": EvidenceSourceV22.METRICS,
    "search_logs": EvidenceSourceV22.LOGS,
    "query_trace_neighborhood": EvidenceSourceV22.TRACES,
    "inspect_service_runtime": EvidenceSourceV22.RUNTIME,
    "inspect_resource_usage": EvidenceSourceV22.RESOURCES,
}
_HEALTHY_METRICS_V22 = {
    MetricKindV22.ERROR_RATE: 0.01,
    MetricKindV22.LATENCY_P95_MS: 10.0,
    MetricKindV22.REQUEST_SUPPORT: 100.0,
}


class NormalizedPracticalCaseV22(DtaModelV22):
    schema_version: str = Field(pattern=r"^dta-v22\.practical-normalized-case\.v1$")
    case_id: str = Field(pattern=r"^[a-z0-9-]+$")
    source_bytes_sha256: Sha256V22
    candidate_services: tuple[str, ...] = Field(min_length=1, max_length=4)
    topology_edges: tuple[tuple[str, str], ...]
    capture: ReplayCaptureV22
    normalization_notes: tuple[str, ...]


def _utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.utcoffset() != timedelta(0):
        raise ValueError("legacy replay timestamp is not UTC")
    return parsed.astimezone(timezone.utc)


def _as_int(value: object) -> int:
    if type(value) is not int:
        raise ValueError("legacy replay integer field is invalid")
    return cast(int, value)


def _as_float(value: object) -> float:
    if type(value) not in {int, float}:
        raise ValueError("legacy replay numeric field is invalid")
    return float(cast(int | float, value))


def _observations(raw: Mapping[str, object]) -> tuple[Mapping[str, object], ...]:
    value = raw.get("observations")
    if not isinstance(value, list) or any(not isinstance(item, Mapping) for item in value):
        raise ValueError("legacy replay observations are invalid")
    return tuple(cast(Mapping[str, object], item) for item in value)


def _records(observation: Mapping[str, object]) -> tuple[Mapping[str, object], ...]:
    value = observation.get("records")
    if not isinstance(value, list) or any(not isinstance(item, Mapping) for item in value):
        raise ValueError("legacy replay records are invalid")
    return tuple(cast(Mapping[str, object], item) for item in value)


def _candidate_services(
    observations: tuple[Mapping[str, object], ...],
) -> tuple[str, ...]:
    values: set[str] = set()
    for observation in observations:
        scope = observation.get("service_scope")
        if not isinstance(scope, list) or any(not isinstance(item, str) for item in scope):
            raise ValueError("legacy replay service scope is invalid")
        values.update(cast(list[str], scope))
    candidates = tuple(sorted(values))
    if not 1 <= len(candidates) <= 4:
        raise ValueError("legacy replay candidate cardinality is unsupported")
    return candidates


def _normalize_metrics(
    *,
    observations: tuple[Mapping[str, object], ...],
    candidates: tuple[str, ...],
    captured_at: datetime,
) -> tuple[tuple[MetricFactV22, ...], bool]:
    metrics: list[MetricFactV22] = []
    for observation in observations:
        if observation.get("tool") != "query_metrics":
            continue
        for record in _records(observation):
            kind = MetricKindV22(str(record["metric_kind"]))
            sample_count = _as_int(record["sample_count"])
            metrics.append(
                MetricFactV22(
                    schema_version="dta-v22.metric-fact.v1",
                    service=str(record["service"]),
                    metric_kind=kind,
                    support_status=(
                        MetricSupportStatusV22.SUPPORTED
                        if sample_count > 0
                        else MetricSupportStatusV22.UNSUPPORTED
                    ),
                    sample_count=sample_count,
                    value=_as_float(record["value"]) if sample_count > 0 else None,
                    unit=METRIC_UNIT_BY_KIND_V22[kind],
                    window_started_at=captured_at - timedelta(seconds=300),
                    window_ended_at=captured_at,
                )
            )
    existing = {(item.service, item.metric_kind) for item in metrics}
    augmented = False
    for service in candidates:
        for kind, value in _HEALTHY_METRICS_V22.items():
            if (service, kind) in existing:
                continue
            augmented = True
            metrics.append(
                MetricFactV22(
                    schema_version="dta-v22.metric-fact.v1",
                    service=service,
                    metric_kind=kind,
                    support_status=MetricSupportStatusV22.SUPPORTED,
                    sample_count=1,
                    value=float(value),
                    unit=METRIC_UNIT_BY_KIND_V22[kind],
                    window_started_at=captured_at - timedelta(seconds=300),
                    window_ended_at=captured_at,
                )
            )
    return tuple(sorted(metrics, key=lambda item: (item.service, item.metric_kind.value))), augmented


def _normalize_runtime(
    *,
    observations: tuple[Mapping[str, object], ...],
    candidates: tuple[str, ...],
) -> tuple[tuple[RuntimeRecordV22, ...], bool]:
    runtime: list[RuntimeRecordV22] = []
    for observation in observations:
        if observation.get("tool") != "inspect_service_runtime":
            continue
        for record in _records(observation):
            raw_state = str(record["state"])
            owned = bool(record.get("owned_container_present", True))
            if not owned:
                state = RuntimeStateV22.ABSENT
            elif raw_state == "RUNNING":
                state = RuntimeStateV22.RUNNING
            elif raw_state in {"EXITED", "STOPPED"}:
                state = RuntimeStateV22.EXITED
            else:
                state = RuntimeStateV22.OTHER
            runtime.append(
                RuntimeRecordV22(
                    schema_version="dta-v22.runtime-record.v1",
                    service=str(record["logical_service"]),
                    state=state,
                    healthy=state is RuntimeStateV22.RUNNING
                    and record.get("health") == "HEALTHY",
                    restart_count=_as_int(record.get("restart_count", 0)),
                )
            )
    existing = {item.service for item in runtime}
    augmented = False
    for service in candidates:
        if service in existing:
            continue
        augmented = True
        runtime.append(
            RuntimeRecordV22(
                schema_version="dta-v22.runtime-record.v1",
                service=service,
                state=RuntimeStateV22.RUNNING,
                healthy=True,
                restart_count=0,
            )
        )
    return tuple(sorted(runtime, key=lambda item: item.service)), augmented


def _normalize_resources(
    observations: tuple[Mapping[str, object], ...],
) -> tuple[tuple[ResourceUsageRecordV22, ...], bool]:
    resources: list[ResourceUsageRecordV22] = []
    resampled = False
    for observation in observations:
        if observation.get("tool") != "inspect_resource_usage":
            continue
        for record in _records(observation):
            samples = record["samples"]
            if not isinstance(samples, list):
                raise ValueError("legacy resource samples are invalid")
            parsed_samples = tuple(
                ResourceSampleV22(
                    offset_ms=_as_int(cast(Mapping[str, object], item)["offset_ms"]),
                    cpu_percent=_as_float(
                        cast(Mapping[str, object], item)["cpu_percent"]
                    ),
                    memory_bytes=_as_int(
                        cast(Mapping[str, object], item)["memory_bytes"]
                    ),
                )
                for item in samples
            )
            if len(parsed_samples) != 5 or parsed_samples[-1].offset_ms != 10_000:
                resampled = True
                source_end = parsed_samples[-1].offset_ms

                def interpolate(offset: int) -> ResourceSampleV22:
                    source_offset = offset * source_end / 10_000
                    right_index = next(
                        (
                            index
                            for index, item in enumerate(parsed_samples)
                            if item.offset_ms >= source_offset
                        ),
                        len(parsed_samples) - 1,
                    )
                    left_index = max(0, right_index - 1)
                    left = parsed_samples[left_index]
                    right = parsed_samples[right_index]
                    if left.offset_ms == right.offset_ms:
                        ratio = 0.0
                    else:
                        ratio = (source_offset - left.offset_ms) / (
                            right.offset_ms - left.offset_ms
                        )
                    return ResourceSampleV22(
                        offset_ms=offset,
                        cpu_percent=left.cpu_percent
                        + (right.cpu_percent - left.cpu_percent) * ratio,
                        memory_bytes=round(
                            left.memory_bytes
                            + (right.memory_bytes - left.memory_bytes) * ratio
                        ),
                    )

                parsed_samples = tuple(
                    interpolate(offset) for offset in (0, 2500, 5000, 7500, 10_000)
                )
            resources.append(
                ResourceUsageRecordV22(
                    schema_version="dta-v22.resource-usage-record.v1",
                    service=str(record["logical_service"]),
                    sampling_window_seconds=10,
                    samples=parsed_samples,
                    memory_slope_bytes_per_second=_as_float(
                        record["memory_slope_bytes_per_second"]
                    ),
                )
            )
    return tuple(sorted(resources, key=lambda item: item.service)), resampled


def _normalize_traces(
    *,
    observations: tuple[Mapping[str, object], ...],
    captured_at: datetime,
) -> tuple[TraceSpanV22, ...]:
    traces: list[TraceSpanV22] = []
    for observation in observations:
        if observation.get("tool") != "query_trace_neighborhood":
            continue
        for record in _records(observation):
            service = str(record["service"])
            raw_parent = record.get("parent_service")
            parent = None if raw_parent is None else str(raw_parent)
            path = (service,) if parent is None else (parent, service)
            traces.append(
                TraceSpanV22(
                    schema_version="dta-v22.trace-span.v1",
                    observed_at=captured_at,
                    service_path=path,
                    service=service,
                    parent_service=parent,
                    operation=str(record["operation"])[:160],
                    status=SpanStatusV22(str(record["status"])),
                    duration_ms=_as_float(record["duration_ms"]),
                    first_error_location=bool(record["first_error_location"]),
                )
            )
    return tuple(
        sorted(
            traces,
            key=lambda item: (
                item.service,
                item.parent_service or "",
                item.operation,
                item.duration_ms,
            ),
        )
    )


def _normalize_logs(
    *,
    observations: tuple[Mapping[str, object], ...],
    captured_at: datetime,
) -> tuple[LogRecordV22, ...]:
    logs: list[LogRecordV22] = []
    for observation in observations:
        if observation.get("tool") != "search_logs":
            continue
        for record in _records(observation):
            severity = str(record.get("severity", "DIAGNOSTIC")).upper()
            if severity not in {"WARN", "ERROR", "FATAL", "DIAGNOSTIC"}:
                severity = "DIAGNOSTIC"
            logs.append(
                LogRecordV22(
                    schema_version="dta-v22.log-record.v1",
                    observed_at=(
                        _utc(str(record["observed_at"]))
                        if "observed_at" in record
                        else captured_at
                    ),
                    service=str(record.get("service", record.get("logical_service"))),
                    severity=cast(Any, severity),
                    message=str(record.get("message", record.get("body", "diagnostic"))),
                )
            )
    return tuple(sorted(logs, key=lambda item: (item.observed_at, item.service, item.message)))


def _source_failures(
    observations: tuple[Mapping[str, object], ...],
) -> tuple[ReplaySourceFailureV22, ...]:
    failures: list[ReplaySourceFailureV22] = []
    for observation in observations:
        error = observation.get("error_code")
        tool = observation.get("tool")
        if error is None or not isinstance(tool, str):
            continue
        source = _SOURCE_BY_TOOL_V22.get(tool)
        if source is None:
            raise ValueError("legacy replay uses an unknown read tool")
        code = str(error).upper()
        if "TIMEOUT" in code:
            status = ReadSourceStatusV22.FAILURE_TIMEOUT
        elif "UNAVAILABLE" in code:
            status = ReadSourceStatusV22.FAILURE_UNAVAILABLE
        else:
            status = ReadSourceStatusV22.FAILURE_SCHEMA
        failures.append(
            ReplaySourceFailureV22(
                schema_version="dta-v22.replay-source-failure.v1",
                source=source,
                status=status,
            )
        )
    by_source = {item.source: item for item in failures}
    return tuple(by_source[source] for source in EvidenceSourceV22 if source in by_source)


def normalize_practical_case_bytes_v22(
    source_bytes: bytes,
) -> NormalizedPracticalCaseV22:
    """Normalize already-frozen agent-visible bytes without evaluator truth."""

    raw_object = json.loads(source_bytes)
    if not isinstance(raw_object, dict):
        raise ValueError("legacy replay case is not an object")
    raw = cast(Mapping[str, object], raw_object)
    observations = _observations(raw)
    candidates = _candidate_services(observations)
    captured_at = _utc(str(raw["captured_ended_at"]))
    metrics, metric_augmented = _normalize_metrics(
        observations=observations,
        candidates=candidates,
        captured_at=captured_at,
    )
    runtime, runtime_augmented = _normalize_runtime(
        observations=observations,
        candidates=candidates,
    )
    traces = _normalize_traces(observations=observations, captured_at=captured_at)
    resources, resources_resampled = _normalize_resources(observations)
    edges = tuple(
        sorted(
            {
                tuple(sorted((item.parent_service, item.service)))
                for item in traces
                if item.parent_service is not None
                and item.parent_service != item.service
                and item.parent_service in candidates
                and item.service in candidates
            }
        )
    )
    notes: list[str] = []
    if metric_augmented or runtime_augmented:
        notes.append(
            "baseline-derived healthy bootstrap facts added for visible missing services"
        )
    if resources_resampled:
        notes.append("legacy resource samples resampled to the canonical read window")
    notes.append("legacy trace paths compressed without changing span service or parent")
    return NormalizedPracticalCaseV22(
        schema_version="dta-v22.practical-normalized-case.v1",
        case_id=str(raw["case_id"]),
        source_bytes_sha256=hashlib.sha256(source_bytes).hexdigest(),
        candidate_services=candidates,
        topology_edges=cast(tuple[tuple[str, str], ...], edges),
        capture=ReplayCaptureV22(
            schema_version="dta-v22.replay-capture.v1",
            captured_at=captured_at,
            metrics=metrics,
            logs=_normalize_logs(observations=observations, captured_at=captured_at),
            traces=traces,
            runtime=runtime,
            resources=resources,
            changes=(),
            source_failures=_source_failures(observations),
        ),
        normalization_notes=tuple(notes),
    )


def load_and_normalize_practical_case_v22(path: Path) -> NormalizedPracticalCaseV22:
    """Read one agent-visible capture once and normalize it without evaluator truth."""

    return normalize_practical_case_bytes_v22(path.read_bytes())


__all__ = (
    "NormalizedPracticalCaseV22",
    "load_and_normalize_practical_case_v22",
    "normalize_practical_case_bytes_v22",
)
