"""Deterministic bounded Metrics, Logs, and Traces tools for RCAEval."""

from __future__ import annotations

from collections import defaultdict
import csv
from dataclasses import dataclass
from enum import Enum
import math
import re
from typing import Literal

from pydantic import Field, StrictFloat

from ecomsre_rcaeval.contracts import RCAEvalModel
from ecomsre_rcaeval.dataset import DevSystem, TelemetryCase


@dataclass(frozen=True, slots=True)
class RCAEvalToolConfig:
    window_seconds: int = 600

    def __post_init__(self) -> None:
        if self.window_seconds <= 0:
            raise ValueError("RCAEval telemetry window must be positive")


class SourceStatus(str, Enum):
    AVAILABLE = "AVAILABLE"
    SOURCE_UNAVAILABLE = "SOURCE_UNAVAILABLE"


class ToolEvidence(RCAEvalModel):
    schema_version: Literal["rcaeval-re2.tool-evidence.v1"] = (
        "rcaeval-re2.tool-evidence.v1"
    )
    evidence_id: str = Field(pattern=r"^(metric|log|trace):[0-9]{4}$")
    service: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=256)
    started_at: StrictFloat
    ended_at: StrictFloat
    summary: str = Field(min_length=1, max_length=2_000)
    points: tuple[tuple[float, float], ...] = Field(default=(), max_length=128)


class ToolResponse(RCAEvalModel):
    schema_version: Literal["rcaeval-re2.tool-response.v1"] = (
        "rcaeval-re2.tool-response.v1"
    )
    status: SourceStatus
    reason: str | None = Field(default=None, min_length=1, max_length=256)
    values: tuple[str, ...] = Field(default=(), max_length=512)
    evidence: tuple[ToolEvidence, ...] = Field(default=(), max_length=64)


def _read_csv(path) -> tuple[dict[str, str], ...]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise ValueError("RCAEval telemetry CSV requires a header")
        return tuple(dict(row) for row in reader)


def _read_metric_csv(path) -> tuple[dict[str, str], ...]:
    rows = _read_csv(path)
    last: dict[str, float] = {}
    normalized: list[dict[str, str]] = []
    for row in rows:
        item = dict(row)
        if item.get("time", "").strip() == "":
            continue
        _number(item.get("time"))
        for name, raw in item.items():
            if name == "time":
                continue
            try:
                value = float(raw)
            except (TypeError, ValueError):
                value = last.get(name, 0.0)
            if not math.isfinite(value):
                value = last.get(name, 0.0)
            item[name] = str(value)
            last[name] = value
        normalized.append(item)
    return tuple(normalized)


def _column(row: dict[str, str], names: tuple[str, ...]) -> str | None:
    lowered = {key.casefold(): key for key in row}
    for name in names:
        key = lowered.get(name.casefold())
        if key is not None:
            return row.get(key)
    return None


def _number(value: str | None) -> float:
    if value is None:
        raise ValueError("RCAEval telemetry numeric field is missing")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("RCAEval telemetry numeric field must be finite")
    return number


def _timestamp(row: dict[str, str], names: tuple[str, ...]) -> float:
    value = _number(_column(row, names))
    if value >= 1e17:
        return value / 1e9
    if value >= 1e14:
        return value / 1e6
    if value >= 1e11:
        return value / 1e3
    return value


_TRACE_TIMESTAMP_COLUMNS = (
    "startTimeMillis",
    "startTime",
    "start_time",
    "timestamp",
    "time",
)


def _service_from_metric(name: str) -> str:
    if "_" not in name:
        raise ValueError("RCAEval metric name does not encode a service")
    return name.rsplit("_", 1)[0]


def _sample_points(
    points: list[tuple[float, float]],
    max_points: int,
) -> tuple[tuple[float, float], ...]:
    if len(points) <= max_points:
        return tuple(points)
    stride = max(1, math.ceil(len(points) / max_points))
    sampled = points[::stride][:max_points]
    if sampled[-1] != points[-1]:
        sampled[-1] = points[-1]
    return tuple(sampled)


class _EvidenceIds:
    def __init__(self) -> None:
        self._counters = {"metric": 0, "log": 0, "trace": 0}

    def next(self, source: Literal["metric", "log", "trace"]) -> str:
        self._counters[source] += 1
        return f"{source}:{self._counters[source]:04d}"


class RCAEvalToolset:
    def __init__(
        self,
        case: TelemetryCase,
        *,
        config: RCAEvalToolConfig | None = None,
    ) -> None:
        self._case = case
        self._config = config or RCAEvalToolConfig()
        self._ids = _EvidenceIds()
        self._call_count = 0

    @property
    def call_count(self) -> int:
        return self._call_count

    def _begin_call(self) -> None:
        self._call_count += 1

    def _in_window(self, timestamp: float) -> bool:
        return (
            self._case.inject_time - self._config.window_seconds
            <= timestamp
            <= self._case.inject_time + self._config.window_seconds
        )

    def _unavailable_traces(self) -> ToolResponse | None:
        if self._case.system == DevSystem.RE2_SS.value:
            return ToolResponse(
                status=SourceStatus.SOURCE_UNAVAILABLE,
                reason="RCAEval RE2-SS does not provide traces",
            )
        return None

    def list_metric_services(self) -> ToolResponse:
        self._begin_call()
        rows = _read_metric_csv(self._case.metrics_path)
        if not rows:
            raise ValueError("RCAEval metrics CSV contains no rows")
        services = tuple(
            sorted(
                {
                    _service_from_metric(name)
                    for name in rows[0]
                    if name != "time"
                }
            )
        )
        return ToolResponse(status=SourceStatus.AVAILABLE, values=services)

    def rank_metric_anomalies(
        self,
        *,
        service: str | None = None,
        top_k: int,
        window_seconds: int | None = None,
    ) -> ToolResponse:
        self._begin_call()
        if not 1 <= top_k <= 64:
            raise ValueError("metric top_k must be between 1 and 64")
        rows = _read_metric_csv(self._case.metrics_path)
        names = tuple(name for name in rows[0] if name != "time")
        if service is not None:
            names = tuple(
                name for name in names if _service_from_metric(name) == service
            )
        ranked: list[tuple[float, str, float, float, list[tuple[float, float]]]] = []
        window = self._config.window_seconds if window_seconds is None else window_seconds
        if window <= 0:
            raise ValueError("metric window must be positive")
        lower = self._case.inject_time - window
        upper = self._case.inject_time + window
        for name in names:
            points = [
                (_number(row.get("time")), _number(row.get(name)))
                for row in rows
                if lower <= _number(row.get("time")) <= upper
            ]
            before = [value for timestamp, value in points if timestamp < self._case.inject_time]
            after = [value for timestamp, value in points if timestamp >= self._case.inject_time]
            if not before or not after:
                continue
            before_mean = sum(before) / len(before)
            after_mean = sum(after) / len(after)
            scale = max(abs(before_mean), 1e-9)
            score = abs(after_mean - before_mean) / scale
            ranked.append((score, name, before_mean, after_mean, points))
        ranked.sort(key=lambda item: (-item[0], item[1]))
        evidence = tuple(
            ToolEvidence(
                evidence_id=self._ids.next("metric"),
                service=_service_from_metric(name),
                name=name,
                started_at=float(points[0][0]),
                ended_at=float(points[-1][0]),
                summary=(
                    f"{name} pre-mean={before_mean:.6g}, "
                    f"post-mean={after_mean:.6g}, anomaly-score={score:.6g}."
                ),
                points=_sample_points(points, 24),
            )
            for score, name, before_mean, after_mean, points in ranked[:top_k]
        )
        return ToolResponse(status=SourceStatus.AVAILABLE, evidence=evidence)

    def get_metric_series(
        self,
        metric_id: str,
        *,
        max_points: int = 64,
        window_seconds: int | None = None,
    ) -> ToolResponse:
        self._begin_call()
        if not 2 <= max_points <= 128:
            raise ValueError("metric max_points must be between 2 and 128")
        rows = _read_metric_csv(self._case.metrics_path)
        if not rows or metric_id not in rows[0] or metric_id == "time":
            raise ValueError("unknown RCAEval metric identifier")
        window = self._config.window_seconds if window_seconds is None else window_seconds
        if window <= 0:
            raise ValueError("metric window must be positive")
        lower = self._case.inject_time - window
        upper = self._case.inject_time + window
        points = [
            (_number(row.get("time")), _number(row.get(metric_id)))
            for row in rows
            if lower <= _number(row.get("time")) <= upper
        ]
        if not points:
            return ToolResponse(status=SourceStatus.AVAILABLE)
        if len(points) > max_points:
            points = list(_sample_points(points, max_points))
        evidence = ToolEvidence(
            evidence_id=self._ids.next("metric"),
            service=_service_from_metric(metric_id),
            name=metric_id,
            started_at=points[0][0],
            ended_at=points[-1][0],
            summary=f"Bounded {metric_id} series with {len(points)} points.",
            points=tuple(points),
        )
        return ToolResponse(status=SourceStatus.AVAILABLE, evidence=(evidence,))

    def compare_pre_post(self, metric_id: str) -> ToolResponse:
        self._begin_call()
        rows = _read_metric_csv(self._case.metrics_path)
        if not rows or metric_id not in rows[0] or metric_id == "time":
            raise ValueError("unknown RCAEval metric identifier")
        before = [
            _number(row.get(metric_id))
            for row in rows
            if self._in_window(_number(row.get("time")))
            and _number(row.get("time")) < self._case.inject_time
        ]
        after = [
            _number(row.get(metric_id))
            for row in rows
            if self._in_window(_number(row.get("time")))
            and _number(row.get("time")) >= self._case.inject_time
        ]
        if not before or not after:
            return ToolResponse(status=SourceStatus.AVAILABLE)
        bounded_times = [
            _number(row.get("time"))
            for row in rows
            if self._in_window(_number(row.get("time")))
        ]
        first = min(bounded_times)
        last = max(bounded_times)
        before_mean = sum(before) / len(before)
        after_mean = sum(after) / len(after)
        evidence = ToolEvidence(
            evidence_id=self._ids.next("metric"),
            service=_service_from_metric(metric_id),
            name=metric_id,
            started_at=first,
            ended_at=last,
            summary=(
                f"{metric_id} pre-mean={before_mean:.6g}, "
                f"post-mean={after_mean:.6g}."
            ),
        )
        return ToolResponse(status=SourceStatus.AVAILABLE, evidence=(evidence,))

    def list_log_services(self) -> ToolResponse:
        self._begin_call()
        rows = _read_csv(self._case.logs_path)
        services = tuple(
            sorted(
                {
                    value
                    for row in rows
                    if (
                        value := _column(
                            row, ("service", "serviceName", "container_name")
                        )
                    )
                }
            )
        )
        return ToolResponse(status=SourceStatus.AVAILABLE, values=services)

    def search_logs(
        self,
        *,
        service: str | None = None,
        query: str | None = None,
        level: str | None = None,
        limit: int = 20,
    ) -> ToolResponse:
        self._begin_call()
        if not 1 <= limit <= 64:
            raise ValueError("log limit must be between 1 and 64")
        selected: list[ToolEvidence] = []
        for row in _read_csv(self._case.logs_path):
            row_service = (
                _column(row, ("service", "serviceName", "container_name"))
                or "unknown"
            )
            message = _column(row, ("message", "body", "log")) or ""
            row_level = _column(row, ("level", "severity", "severity_text")) or ""
            if service is not None and row_service != service:
                continue
            if query is not None and query.casefold() not in message.casefold():
                continue
            if level is not None and row_level.casefold() != level.casefold():
                continue
            timestamp = _timestamp(row, ("timestamp", "time"))
            if not self._in_window(timestamp):
                continue
            selected.append(
                ToolEvidence(
                    evidence_id=self._ids.next("log"),
                    service=row_service,
                    name=row_level or "log",
                    started_at=timestamp,
                    ended_at=timestamp,
                    summary=message[:2_000] or "Empty log message.",
                )
            )
            if len(selected) == limit:
                break
        return ToolResponse(status=SourceStatus.AVAILABLE, evidence=tuple(selected))

    def summarize_log_patterns(
        self,
        *,
        service: str | None = None,
        top_k: int = 10,
    ) -> ToolResponse:
        self._begin_call()
        if not 1 <= top_k <= 64:
            raise ValueError("log top_k must be between 1 and 64")
        grouped: dict[tuple[str, str], list[float]] = defaultdict(list)
        sample_by_pattern: dict[tuple[str, str], str] = {}
        for row in _read_csv(self._case.logs_path):
            row_service = (
                _column(row, ("service", "serviceName", "container_name"))
                or "unknown"
            )
            if service is not None and row_service != service:
                continue
            message = _column(row, ("message", "body", "log")) or ""
            pattern = re.sub(r"\b\d+\b", "<n>", message.casefold())[:512]
            timestamp = _timestamp(row, ("timestamp", "time"))
            if not self._in_window(timestamp):
                continue
            key = (row_service, pattern)
            grouped[key].append(timestamp)
            sample_by_pattern.setdefault(key, message)
        ranked = sorted(
            grouped.items(), key=lambda item: (-len(item[1]), item[0])
        )
        evidence = tuple(
            ToolEvidence(
                evidence_id=self._ids.next("log"),
                service=key[0],
                name="log-pattern",
                started_at=min(timestamps),
                ended_at=max(timestamps),
                summary=(
                    f"count={len(timestamps)} pattern sample: "
                    f"{sample_by_pattern[key][:1_800] or 'empty'}"
                ),
            )
            for key, timestamps in ranked[:top_k]
        )
        return ToolResponse(status=SourceStatus.AVAILABLE, evidence=evidence)

    def list_trace_services(self) -> ToolResponse:
        self._begin_call()
        unavailable = self._unavailable_traces()
        if unavailable is not None:
            return unavailable
        assert self._case.traces_path is not None
        services = tuple(
            sorted(
                {
                    value
                    for row in _read_csv(self._case.traces_path)
                    if (value := _column(row, ("service", "serviceName")))
                }
            )
        )
        return ToolResponse(status=SourceStatus.AVAILABLE, values=services)

    def rank_trace_error_anomalies(self, *, top_k: int) -> ToolResponse:
        self._begin_call()
        unavailable = self._unavailable_traces()
        if unavailable is not None:
            return unavailable
        if not 1 <= top_k <= 64:
            raise ValueError("trace top_k must be between 1 and 64")
        assert self._case.traces_path is not None
        grouped: dict[str, list[tuple[float, float]]] = defaultdict(list)
        for row in _read_csv(self._case.traces_path):
            service = _column(row, ("service", "serviceName")) or "unknown"
            timestamp = _timestamp(row, _TRACE_TIMESTAMP_COLUMNS)
            if not self._in_window(timestamp):
                continue
            error_text = (_column(row, ("error", "statusCode", "status")) or "0")
            error = 0.0 if error_text.casefold() in {"0", "false", "ok", "unset"} else 1.0
            grouped[service].append((timestamp, error))
        ranked = sorted(
            (
                (sum(value for _, value in points), service, points)
                for service, points in grouped.items()
            ),
            key=lambda item: (-item[0], item[1]),
        )
        evidence = tuple(
            ToolEvidence(
                evidence_id=self._ids.next("trace"),
                service=service,
                name="trace-error-count",
                started_at=min(timestamp for timestamp, _ in points),
                ended_at=max(timestamp for timestamp, _ in points),
                summary=f"{service} trace error count={score:.0f}.",
            )
            for score, service, points in ranked[:top_k]
        )
        return ToolResponse(status=SourceStatus.AVAILABLE, evidence=evidence)

    def summarize_service_edges(
        self,
        *,
        top_k: int = 10,
    ) -> ToolResponse:
        self._begin_call()
        unavailable = self._unavailable_traces()
        if unavailable is not None:
            return unavailable
        if not 1 <= top_k <= 64:
            raise ValueError("trace top_k must be between 1 and 64")
        assert self._case.traces_path is not None
        grouped: dict[tuple[str, str], list[float]] = defaultdict(list)
        for row in _read_csv(self._case.traces_path):
            service = _column(row, ("service", "serviceName")) or "unknown"
            peer = _column(row, ("peer", "peerService", "parentService")) or "unknown"
            timestamp = _timestamp(row, _TRACE_TIMESTAMP_COLUMNS)
            if not self._in_window(timestamp):
                continue
            grouped[(service, peer)].append(timestamp)
        ranked = sorted(grouped.items(), key=lambda item: (-len(item[1]), item[0]))
        evidence = tuple(
            ToolEvidence(
                evidence_id=self._ids.next("trace"),
                service=edge[0],
                name=f"{edge[0]}->{edge[1]}",
                started_at=min(timestamps),
                ended_at=max(timestamps),
                summary=f"Observed {len(timestamps)} spans on {edge[0]}->{edge[1]}.",
            )
            for edge, timestamps in ranked[:top_k]
        )
        return ToolResponse(status=SourceStatus.AVAILABLE, evidence=evidence)

    def rank_trace_latency_anomalies(self, *, top_k: int = 10) -> ToolResponse:
        self._begin_call()
        unavailable = self._unavailable_traces()
        if unavailable is not None:
            return unavailable
        if not 1 <= top_k <= 64:
            raise ValueError("trace top_k must be between 1 and 64")
        assert self._case.traces_path is not None
        grouped: dict[str, list[tuple[float, float]]] = defaultdict(list)
        for row in _read_csv(self._case.traces_path):
            service = _column(row, ("service", "serviceName")) or "unknown"
            timestamp = _timestamp(row, _TRACE_TIMESTAMP_COLUMNS)
            if not self._in_window(timestamp):
                continue
            duration = _number(_column(row, ("duration", "latency")))
            grouped[service].append((timestamp, duration))
        ranked: list[tuple[float, str, list[tuple[float, float]], float, float]] = []
        for service, points in grouped.items():
            before = [value for timestamp, value in points if timestamp < self._case.inject_time]
            after = [value for timestamp, value in points if timestamp >= self._case.inject_time]
            if not before or not after:
                continue
            before_mean = sum(before) / len(before)
            after_mean = sum(after) / len(after)
            score = abs(after_mean - before_mean) / max(abs(before_mean), 1e-9)
            ranked.append((score, service, points, before_mean, after_mean))
        ranked.sort(key=lambda item: (-item[0], item[1]))
        evidence = tuple(
            ToolEvidence(
                evidence_id=self._ids.next("trace"),
                service=service,
                name="trace-latency",
                started_at=min(timestamp for timestamp, _ in points),
                ended_at=max(timestamp for timestamp, _ in points),
                summary=(
                    f"{service} trace duration pre-mean={before_mean:.6g}, "
                    f"post-mean={after_mean:.6g}, anomaly-score={score:.6g}."
                ),
            )
            for score, service, points, before_mean, after_mean in ranked[:top_k]
        )
        return ToolResponse(status=SourceStatus.AVAILABLE, evidence=evidence)

    def summarize_trace_diagnostics(self, *, top_k: int = 10) -> ToolResponse:
        """Return one bounded per-service trace specialist view in one tool call."""

        self._begin_call()
        unavailable = self._unavailable_traces()
        if unavailable is not None:
            return unavailable
        if not 1 <= top_k <= 64:
            raise ValueError("trace top_k must be between 1 and 64")
        assert self._case.traces_path is not None
        grouped: dict[str, list[tuple[float, float, float]]] = defaultdict(list)
        for row in _read_csv(self._case.traces_path):
            service = _column(row, ("service", "serviceName")) or "unknown"
            timestamp = _timestamp(row, _TRACE_TIMESTAMP_COLUMNS)
            if not self._in_window(timestamp):
                continue
            duration = _number(_column(row, ("duration", "latency")))
            error_text = _column(row, ("error", "statusCode", "status")) or "0"
            error = (
                0.0
                if error_text.casefold() in {"0", "false", "ok", "unset"}
                else 1.0
            )
            grouped[service].append((timestamp, duration, error))
        ranked: list[
            tuple[float, str, list[tuple[float, float, float]], float, float, float]
        ] = []
        for service, points in grouped.items():
            before = [duration for timestamp, duration, _ in points if timestamp < self._case.inject_time]
            after = [duration for timestamp, duration, _ in points if timestamp >= self._case.inject_time]
            if not before or not after:
                continue
            before_mean = sum(before) / len(before)
            after_mean = sum(after) / len(after)
            latency_score = abs(after_mean - before_mean) / max(abs(before_mean), 1e-9)
            error_count = sum(error for _, _, error in points)
            score = latency_score + error_count / len(points)
            ranked.append(
                (score, service, points, before_mean, after_mean, error_count)
            )
        ranked.sort(key=lambda item: (-item[0], item[1]))
        evidence = tuple(
            ToolEvidence(
                evidence_id=self._ids.next("trace"),
                service=service,
                name="trace-diagnostics",
                started_at=min(timestamp for timestamp, _, _ in points),
                ended_at=max(timestamp for timestamp, _, _ in points),
                summary=(
                    f"{service} trace duration pre-mean={before_mean:.6g}, "
                    f"post-mean={after_mean:.6g}, errors={error_count:.0f}, "
                    f"combined-score={score:.6g}."
                ),
            )
            for score, service, points, before_mean, after_mean, error_count in ranked[
                :top_k
            ]
        )
        return ToolResponse(status=SourceStatus.AVAILABLE, evidence=evidence)

    def get_trace_examples(
        self,
        *,
        service: str | None = None,
        peer: str | None = None,
        limit: int = 5,
    ) -> ToolResponse:
        self._begin_call()
        unavailable = self._unavailable_traces()
        if unavailable is not None:
            return unavailable
        if not 1 <= limit <= 32:
            raise ValueError("trace limit must be between 1 and 32")
        assert self._case.traces_path is not None
        evidence: list[ToolEvidence] = []
        for row in _read_csv(self._case.traces_path):
            row_service = _column(row, ("service", "serviceName")) or "unknown"
            row_peer = _column(row, ("peer", "peerService", "parentService")) or "unknown"
            if service is not None and row_service != service:
                continue
            if peer is not None and row_peer != peer:
                continue
            timestamp = _timestamp(row, _TRACE_TIMESTAMP_COLUMNS)
            if not self._in_window(timestamp):
                continue
            duration = _column(row, ("duration", "latency")) or "unknown"
            status = _column(row, ("error", "statusCode", "status")) or "unknown"
            evidence.append(
                ToolEvidence(
                    evidence_id=self._ids.next("trace"),
                    service=row_service,
                    name=f"{row_service}->{row_peer}",
                    started_at=timestamp,
                    ended_at=timestamp,
                    summary=f"duration={duration}, status={status}.",
                )
            )
            if len(evidence) == limit:
                break
        return ToolResponse(status=SourceStatus.AVAILABLE, evidence=tuple(evidence))
