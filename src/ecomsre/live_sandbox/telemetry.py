"""Real Prometheus, OpenSearch, and Jaeger adapters for the local sandbox."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
import time
from typing import Literal, Mapping
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from ecomsre.live_sandbox.contracts import (
    ConfigBundle,
    LiveTelemetrySnapshot,
    LocalEndpoints,
    LogEvidence,
    SLIWindow,
    SourceStatus,
    TraceEvidence,
    canonical_json_bytes,
)
from ecomsre_rca100.contracts import CanonicalRCA100Entity, RCA100MetricsEntityRank
from ecomsre_rca100.projection import (
    RCA100AgentContext,
    RCA100AgentTask,
    RCA100BoundedEvidence,
    RCA100MetricEvidence,
    RCA100MetricsProjection,
    RCA100SourceProjection,
)


def _json_request(
    url: str,
    *,
    method: str = "GET",
    payload: object | None = None,
    timeout_seconds: float = 15,
) -> object:
    data = None if payload is None else canonical_json_bytes(payload).rstrip(b"\n")
    request = Request(
        url,
        data=data,
        method=method,
        headers={"Accept": "application/json", "Content-Type": "application/json"},
    )
    with urlopen(request, timeout=timeout_seconds) as response:  # noqa: S310 - loopback-only caller contracts
        if response.status < 200 or response.status >= 300:
            raise RuntimeError(f"local telemetry HTTP status is {response.status}")
        return json.loads(response.read().decode("utf-8"))


def _mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an object")
    return value


def _nested(value: Mapping[str, object], *paths: str) -> object | None:
    for path in paths:
        if path in value:
            return value[path]
        current: object = value
        parts = path.split(".")
        for index, part in enumerate(parts):
            if not isinstance(current, Mapping):
                current = None
                break
            remainder = ".".join(parts[index:])
            if remainder in current:
                current = current[remainder]
                break
            if part not in current:
                current = None
                break
            current = current[part]
        if current is not None:
            return current
    return None


def _parse_datetime(value: object) -> datetime:
    if isinstance(value, (int, float)):
        number = float(value)
        if number >= 1e17:
            number /= 1e9
        elif number >= 1e14:
            number /= 1e6
        elif number >= 1e11:
            number /= 1e3
        return datetime.fromtimestamp(number, tz=timezone.utc)
    text = str(value or "").strip()
    if not text:
        raise ValueError("telemetry timestamp is absent")
    return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(timezone.utc)


def parse_prometheus_vector(value: object, *, expected_service: str) -> float:
    payload = _mapping(value, "Prometheus response")
    if payload.get("status") != "success":
        raise ValueError("Prometheus query did not succeed")
    data = _mapping(payload.get("data"), "Prometheus data")
    result = data.get("result")
    if data.get("resultType") != "vector" or not isinstance(result, list):
        raise ValueError("Prometheus response is not an instant vector")
    total = 0.0
    matched = 0
    for raw in result:
        item = _mapping(raw, "Prometheus vector item")
        labels = _mapping(item.get("metric", {}), "Prometheus labels")
        service = labels.get("service_name")
        if service is not None and service != expected_service:
            continue
        sample = item.get("value")
        if not isinstance(sample, list) or len(sample) != 2:
            raise ValueError("Prometheus sample is malformed")
        number = float(sample[1])
        if not math.isfinite(number):
            raise ValueError("Prometheus sample is not finite")
        total += number
        matched += 1
    if matched == 0:
        return 0.0
    return total


def _prometheus_range_sample_count(value: object) -> int:
    payload = _mapping(value, "Prometheus range response")
    data = _mapping(payload.get("data"), "Prometheus range data")
    if payload.get("status") != "success" or data.get("resultType") != "matrix":
        raise ValueError("Prometheus range query did not succeed")
    result = data.get("result")
    if not isinstance(result, list):
        raise ValueError("Prometheus range result is malformed")
    return sum(
        len(item.get("values", []))
        for item in result
        if isinstance(item, Mapping) and isinstance(item.get("values"), list)
    )


def parse_opensearch_response(
    value: object, *, expected_service: str
) -> tuple[LogEvidence, ...]:
    payload = _mapping(value, "OpenSearch response")
    hits = _mapping(payload.get("hits"), "OpenSearch hits")
    raw_hits = hits.get("hits")
    if not isinstance(raw_hits, list):
        raise ValueError("OpenSearch hit list is unavailable")
    output: list[LogEvidence] = []
    for raw_hit in raw_hits:
        hit = _mapping(raw_hit, "OpenSearch hit")
        source = _mapping(hit.get("_source"), "OpenSearch source")
        service = _nested(
            source,
            "resource.service.name",
            "resource.attributes.service.name",
            "service.name",
        )
        if service != expected_service:
            continue
        body_raw = _nested(source, "body", "message")
        if isinstance(body_raw, Mapping):
            body = json.dumps(body_raw, ensure_ascii=False, sort_keys=True)
        else:
            body = str(body_raw or "").strip()
        if not body:
            body = "OpenTelemetry log record"
        trace_id = str(
            _nested(source, "trace.id", "traceId", "trace_id") or ""
        ).casefold() or None
        span_id = str(
            _nested(source, "span.id", "spanId", "span_id") or ""
        ).casefold() or None
        if trace_id is not None and len(trace_id) != 32:
            trace_id = None
        if span_id is not None and len(span_id) != 16:
            span_id = None
        output.append(
            LogEvidence(
                observed_at=_parse_datetime(
                    _nested(
                        source,
                        "observedTimestamp",
                        "timestamp",
                        "@timestamp",
                        "timeUnixNano",
                    )
                ),
                service_name=str(service),
                service_instance_id=(
                    str(
                        _nested(
                            source,
                            "resource.service.instance.id",
                            "resource.attributes.service.instance.id",
                        )
                        or ""
                    )
                    or None
                ),
                container_id=(
                    str(
                        _nested(
                            source,
                            "resource.container.id",
                            "resource.attributes.container.id",
                        )
                        or ""
                    )
                    or None
                ),
                host_id=(
                    str(
                        _nested(
                            source,
                            "resource.host.id",
                            "resource.attributes.host.id",
                        )
                        or ""
                    )
                    or None
                ),
                severity=str(_nested(source, "severity.text", "severityText") or "INFO"),
                body=body[:2_000],
                trace_id=trace_id,
                span_id=span_id,
            )
        )
    return tuple(sorted(output, key=lambda item: item.observed_at)[:50])


def _tags(value: object) -> dict[str, object]:
    if not isinstance(value, list):
        return {}
    return {
        str(item["key"]): item.get("value")
        for item in value
        if isinstance(item, Mapping) and isinstance(item.get("key"), str)
    }


def parse_jaeger_response(
    value: object, *, expected_service: str
) -> tuple[TraceEvidence, ...]:
    payload = _mapping(value, "Jaeger response")
    traces = payload.get("data")
    if not isinstance(traces, list):
        raise ValueError("Jaeger trace list is unavailable")
    output: list[TraceEvidence] = []
    for raw_trace in traces:
        trace = _mapping(raw_trace, "Jaeger trace")
        processes_raw = trace.get("processes", {})
        processes = _mapping(processes_raw, "Jaeger processes")
        spans = trace.get("spans")
        if not isinstance(spans, list):
            continue
        for raw_span in spans:
            span = _mapping(raw_span, "Jaeger span")
            process = processes.get(str(span.get("processID", "")), {})
            process_map = _mapping(process, "Jaeger process")
            service = process_map.get("serviceName")
            if service != expected_service:
                continue
            process_tags = _tags(process_map.get("tags"))
            span_tags = _tags(span.get("tags"))
            trace_id = str(span.get("traceID") or trace.get("traceID") or "").casefold()
            span_id = str(span.get("spanID") or "").casefold()
            parent_id = str(span.get("parentSpanID") or "").casefold() or None
            if len(trace_id) != 32 or len(span_id) != 16:
                continue
            if parent_id is not None and len(parent_id) != 16:
                parent_id = None
            status_value = str(
                span_tags.get("otel.status_code")
                or span_tags.get("error")
                or "UNSET"
            ).upper()
            status: Literal["OK", "ERROR", "UNSET"] = "ERROR" if status_value in {"ERROR", "TRUE", "2"} else (
                "OK" if status_value in {"OK", "1"} else "UNSET"
            )
            started_raw = span.get("startTime", 0)
            duration_raw = span.get("duration", 0)
            if not isinstance(started_raw, (int, float, str)) or not isinstance(
                duration_raw, (int, float, str)
            ):
                raise ValueError("Jaeger span timing is malformed")
            output.append(
                TraceEvidence(
                    trace_id=trace_id,
                    span_id=span_id,
                    parent_span_id=parent_id,
                    service_name=str(service),
                    service_instance_id=(
                        str(process_tags.get("service.instance.id") or "") or None
                    ),
                    container_id=str(process_tags.get("container.id") or "") or None,
                    host_id=str(process_tags.get("host.id") or "") or None,
                    span_name=str(span.get("operationName") or "unknown")[:512],
                    started_at=datetime.fromtimestamp(
                        float(started_raw) / 1_000_000,
                        tz=timezone.utc,
                    ),
                    duration_ms=float(duration_raw) / 1_000.0,
                    status=status,
                )
            )
    return tuple(sorted(output, key=lambda item: item.started_at)[:100])


@dataclass(frozen=True, slots=True)
class CapturedTelemetry:
    snapshot: LiveTelemetrySnapshot
    raw: Mapping[str, object]


class LiveTelemetryAdapter:
    def __init__(self, *, endpoints: LocalEndpoints, bundle: ConfigBundle) -> None:
        self.endpoints = endpoints
        self.bundle = bundle

    def _prometheus(self, query: str) -> tuple[object, float]:
        raw = _json_request(
            f"{self.endpoints.prometheus}/api/v1/query?{urlencode({'query': query})}"
        )
        return raw, parse_prometheus_vector(raw, expected_service="payment")

    def _prometheus_range(
        self, query: str, *, started_at: datetime, ended_at: datetime
    ) -> object:
        return _json_request(
            f"{self.endpoints.prometheus}/api/v1/query_range?"
            + urlencode(
                {
                    "query": query,
                    "start": f"{started_at.timestamp():.3f}",
                    "end": f"{ended_at.timestamp():.3f}",
                    "step": "5",
                }
            )
        )

    def capture(
        self,
        *,
        phase: Literal["PREFLIGHT", "BASELINE", "FAULT", "RECOVERY"],
        duration_seconds: int,
        service_health: Mapping[str, bool],
    ) -> CapturedTelemetry:
        if duration_seconds < 10:
            raise ValueError("live telemetry window is too short")
        started_at = datetime.now(timezone.utc)
        time.sleep(duration_seconds)
        ended_at = datetime.now(timezone.utc)
        total_raw, total = self._prometheus(self.bundle.telemetry.prometheus.total_query)
        error_raw, errors = self._prometheus(self.bundle.telemetry.prometheus.error_query)
        p95_raw, p95 = self._prometheus(self.bundle.telemetry.prometheus.p95_query)
        health_raw, runtime_health = self._prometheus(
            self.bundle.telemetry.prometheus.health_query
        )
        range_raw = self._prometheus_range(
            self.bundle.telemetry.prometheus.total_query,
            started_at=started_at,
            ended_at=ended_at,
        )
        sample_count = _prometheus_range_sample_count(range_raw)
        if total <= 0 or sample_count < 3:
            raise RuntimeError("target-service Metrics are empty or cadence is insufficient")
        errors = min(errors, total)
        sli = SLIWindow(
            phase=phase,
            started_at=started_at,
            ended_at=ended_at,
            request_count=total,
            error_count=errors,
            error_rate=errors / total,
            p95_latency_ms=max(p95, 0.0),
            runtime_health=runtime_health,
            sample_count=sample_count,
        )
        log_query = {
            "size": 50,
            "sort": [{"@timestamp": {"order": "asc", "unmapped_type": "date"}}],
            "query": {
                "bool": {
                    "filter": [
                        {"term": {"resource.service.name": "payment"}},
                        {
                            "range": {
                                "@timestamp": {
                                    "gte": started_at.isoformat(),
                                    "lte": ended_at.isoformat(),
                                }
                            }
                        },
                    ]
                }
            },
        }
        logs_raw = _json_request(
            f"{self.endpoints.opensearch}/{self.bundle.telemetry.opensearch.index}/_search",
            method="POST",
            payload=log_query,
        )
        logs = parse_opensearch_response(logs_raw, expected_service="payment")
        trace_query = urlencode(
            {
                "service": "payment",
                "start": str(int(started_at.timestamp() * 1_000_000)),
                "end": str(int(ended_at.timestamp() * 1_000_000)),
                "limit": "50",
            }
        )
        traces_raw = _json_request(
            f"{self.endpoints.jaeger}{self.bundle.telemetry.jaeger.path}?{trace_query}"
        )
        traces = parse_jaeger_response(traces_raw, expected_service="payment")
        if not logs or not traces:
            raise RuntimeError("target-service Logs or Traces are empty")
        identity_fields = {"service.name"}
        identity_values: list[tuple[str, str | None]] = []
        for item in logs:
            identity_values.extend(
                (
                    ("service.instance.id", item.service_instance_id),
                    ("container.id", item.container_id),
                    ("host.id", item.host_id),
                )
            )
        for item in traces:
            identity_values.extend(
                (
                    ("service.instance.id", item.service_instance_id),
                    ("container.id", item.container_id),
                    ("host.id", item.host_id),
                )
            )
        for field, value in identity_values:
            if value:
                identity_fields.add(field)
        if any(item.trace_id for item in logs) or traces:
            identity_fields.update(("trace_id", "span_id"))
        raw = {
            "metrics_total": total_raw,
            "metrics_error": error_raw,
            "metrics_p95": p95_raw,
            "metrics_health": health_raw,
            "metrics_range": range_raw,
            "logs": logs_raw,
            "traces": traces_raw,
        }
        hashes = {
            name: hashlib.sha256(canonical_json_bytes(payload)).hexdigest()
            for name, payload in raw.items()
        }
        snapshot = LiveTelemetrySnapshot(
            environment_id=self.bundle.environment.environment_id,
            sandbox_id=self.bundle.environment.sandbox_id,
            window_start=started_at,
            window_end=ended_at,
            source_status={
                "METRICS": SourceStatus.AVAILABLE,
                "LOGS": SourceStatus.AVAILABLE,
                "TRACES": SourceStatus.AVAILABLE,
            },
            sli_window=sli,
            logs=logs,
            traces=traces,
            service_health=dict(service_health),
            capture_hashes=hashes,
            identity_fields_present=tuple(sorted(identity_fields)),
        )
        return CapturedTelemetry(snapshot=snapshot, raw=raw)


def build_a0_context(
    *,
    alert_title: str,
    baseline_windows: tuple[SLIWindow, ...],
    fault_windows: tuple[SLIWindow, ...],
    log_evidence: tuple[LogEvidence, ...],
    trace_evidence: tuple[TraceEvidence, ...],
) -> RCA100AgentContext:
    if len(baseline_windows) != 2 or len(fault_windows) != 2:
        raise ValueError("A0 live context requires exactly two baseline and fault windows")
    if not log_evidence or not trace_evidence:
        raise ValueError("A0 live context requires target Logs and Traces")
    entity_ref = "apm|apm.service|payment"
    pre_mean = sum(item.error_rate for item in baseline_windows) / 2
    post_mean = sum(item.error_rate for item in fault_windows) / 2
    started_at = min(item.started_at for item in fault_windows)
    ended_at = max(item.ended_at for item in fault_windows)
    metric = RCA100MetricEvidence(
        evidence_ref="metric:0001",
        entity_ref=entity_ref,
        metric="request_error_rate",
        pre_count=sum(item.sample_count for item in baseline_windows),
        post_count=sum(item.sample_count for item in fault_windows),
        pre_mean=pre_mean,
        post_mean=post_mean,
        score=abs(post_mean - pre_mean),
        summary=(
            f"Observed payment request error rate changed from {pre_mean:.4f} "
            f"to {post_mean:.4f} across consecutive live windows."
        ),
    )
    log = log_evidence[0]
    trace = next((item for item in trace_evidence if item.status == "ERROR"), trace_evidence[0])
    context = RCA100AgentContext(
        task=RCA100AgentTask(
            opaque_case_id="rca100-case-0001",
            alert_title=alert_title,
            prompt_text="Diagnose the causal root of the observed live request-error increase.",
            window_start_timestamp=started_at.timestamp(),
            anchor_timestamp=started_at.timestamp(),
            window_end_timestamp=ended_at.timestamp(),
            anchor_source="TASK_ALERT_TRIGGER",
            alert_entity_ref=entity_ref,
        ),
        visible_entities=(
            CanonicalRCA100Entity(
                entity_ref=entity_ref,
                domain="apm",
                type="apm.service",
                entity_id="payment",
                entity_name="payment",
                normalized_name="payment",
            ),
        ),
        metrics=RCA100MetricsProjection(
            status="AVAILABLE",
            evidence=(metric,),
            ranking=(
                RCA100MetricsEntityRank(
                    entity_ref=entity_ref,
                    rank=1,
                    score=metric.score,
                    supporting_metrics_evidence_refs=(metric.evidence_ref,),
                ),
            ),
            total_rows=metric.pre_count + metric.post_count,
            window_rows=metric.pre_count + metric.post_count,
            mapped_rows=metric.pre_count + metric.post_count,
            unmapped_rows=0,
            valid_series=1,
            ranked_entities=1,
        ),
        logs=RCA100SourceProjection(
            source="logs",
            status="AVAILABLE",
            evidence=(
                RCA100BoundedEvidence(
                    evidence_ref="log:0001",
                    entity_ref=entity_ref,
                    name="OpenTelemetry payment log",
                    started_at=log.observed_at.timestamp(),
                    ended_at=log.observed_at.timestamp(),
                    score=1.0 if log.severity.upper() in {"WARN", "ERROR", "FATAL"} else 0.5,
                    summary=log.body,
                ),
            ),
            total_rows=len(log_evidence),
            window_rows=len(log_evidence),
            mapped_rows=len(log_evidence),
            unmapped_rows=0,
        ),
        traces=RCA100SourceProjection(
            source="traces",
            status="AVAILABLE",
            evidence=(
                RCA100BoundedEvidence(
                    evidence_ref="trace:0001",
                    entity_ref=entity_ref,
                    name=trace.span_name,
                    started_at=trace.started_at.timestamp(),
                    ended_at=trace.started_at.timestamp() + trace.duration_ms / 1_000,
                    score=1.0 if trace.status == "ERROR" else 0.5,
                    summary=(
                        f"Payment span {trace.span_name} completed with status {trace.status} "
                        f"in {trace.duration_ms:.2f} ms."
                    ),
                ),
            ),
            total_rows=len(trace_evidence),
            window_rows=len(trace_evidence),
            mapped_rows=len(trace_evidence),
            unmapped_rows=0,
        ),
    )
    payload = context.model_dump_json()
    for forbidden in (
        "paymentFailure",
        "RESTORE_FROZEN_SERVICE_CONFIGURATION",
        "expected_root_service",
        "expected_fault_class",
        "sandbox_id",
        "scenario_id",
    ):
        if forbidden in payload:
            raise ValueError("model-facing live context contains evaluator/control metadata")
    return context


__all__ = [
    "CapturedTelemetry",
    "LiveTelemetryAdapter",
    "build_a0_context",
    "parse_jaeger_response",
    "parse_opensearch_response",
    "parse_prometheus_vector",
]
