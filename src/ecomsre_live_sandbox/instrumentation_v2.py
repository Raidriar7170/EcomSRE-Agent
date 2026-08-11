"""Typed source-specific instrumentation for the live local sandbox v2."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import time
import traceback
from typing import Callable, cast, Literal, Mapping, Protocol, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StrictInt, model_validator

from ecomsre_live_sandbox.contracts import (
    ConfigBundle,
    LogEvidence,
    LocalEndpoints,
    TraceEvidence,
    canonical_json_bytes,
    ensure_private_directory,
    load_bundle,
    verify_private_tree_permissions,
    write_private_json,
)
from ecomsre_live_sandbox.control import SandboxFaultController, build_flag_documents
from ecomsre_live_sandbox.environment import SandboxEnvironment


SHA256_PATTERN = r"^[0-9a-f]{64}$"
TelemetrySource = Literal["METRICS", "LOGS", "TRACES"]
BackendKind = Literal[
    "PROMETHEUS_HTTP_API", "OPENSEARCH_HTTP_API", "JAEGER_QUERY_API"
]
InstrumentationVersion = Literal[
    "live-telemetry-instrumentation-v2",
    "live-telemetry-instrumentation-v3",
]
CanonicalSuccessVerdict = Literal[
    "LIVE_TELEMETRY_INSTRUMENTATION_V2_READY_FOR_E2E",
    "LIVE_TELEMETRY_INSTRUMENTATION_V3_READY_FOR_E2E",
]


@dataclass(frozen=True, slots=True)
class InstrumentationLifecycle:
    version: InstrumentationVersion
    config_relative: Path
    branch: str
    private_root_name: str
    success_verdict: CanonicalSuccessVerdict


V1_CONFIG_RELATIVE = Path("config/live-telemetry-controlled-remediation-v1")
V2_CONFIG_RELATIVE = Path("config/live-telemetry-instrumentation-v2")
V3_CONFIG_RELATIVE = Path("config/live-telemetry-instrumentation-v3")
V2_LIFECYCLE = InstrumentationLifecycle(
    version="live-telemetry-instrumentation-v2",
    config_relative=V2_CONFIG_RELATIVE,
    branch="feature/live-telemetry-instrumentation-v2",
    private_root_name="live-telemetry-instrumentation-v2",
    success_verdict="LIVE_TELEMETRY_INSTRUMENTATION_V2_READY_FOR_E2E",
)
V3_LIFECYCLE = InstrumentationLifecycle(
    version="live-telemetry-instrumentation-v3",
    config_relative=V3_CONFIG_RELATIVE,
    branch="feature/live-telemetry-instrumentation-v3",
    private_root_name="live-telemetry-instrumentation-v3",
    success_verdict="LIVE_TELEMETRY_INSTRUMENTATION_V3_READY_FOR_E2E",
)
SUCCESS_VERDICT = V2_LIFECYCLE.success_verdict


def _success_verdict(version: InstrumentationVersion) -> CanonicalSuccessVerdict:
    if version == V2_LIFECYCLE.version:
        return V2_LIFECYCLE.success_verdict
    return V3_LIFECYCLE.success_verdict


class V2Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class InstrumentationEnvironmentConfig(V2Model):
    schema_version: Literal["live-telemetry.environment.v2"]
    version: InstrumentationVersion
    environment_id: Literal["opentelemetry-demo-local-v1"]
    target_service: str = Field(min_length=1, max_length=128)
    compose_project: Literal["ecomsre-live-sandbox-v1"]
    upstream_commit: Literal["1755859a9de82c2e5e225be68abc401a5ebf2b4f"]
    upstream_tag: Literal["3.0.0"]
    platform: Literal["linux/arm64"]


class PrometheusSourceConfig(V2Model):
    backend_kind: Literal["PROMETHEUS_HTTP_API"]
    service_label: Literal["service_name"]
    status_path: Literal["/api/v1/status/config"]
    metric_names_path: Literal["/api/v1/label/__name__/values"]
    query_path: Literal["/api/v1/query"]
    query_range_path: Literal["/api/v1/query_range"]
    total_query_template: str
    error_query_template: str
    p95_query_template: str
    health_query_template: str
    required_metric_names: tuple[str, ...]


class OpenSearchSourceConfig(V2Model):
    backend_kind: Literal["OPENSEARCH_HTTP_API"]
    index_pattern: Literal["otel-logs-*"]
    indices_path: Literal["/_cat/indices/otel-logs-*?format=json"]
    field_caps_path: Literal["/otel-logs-*/_field_caps"]
    time_field_candidates: tuple[str, ...]
    service_field_candidates: tuple[str, ...]
    body_field_candidates: tuple[str, ...]
    severity_field_candidates: tuple[str, ...]
    maximum_hits: StrictInt = Field(ge=1, le=100)


class JaegerSourceConfig(V2Model):
    backend_kind: Literal["JAEGER_QUERY_API"]
    service_catalog_path: Literal["/jaeger/ui/api/services"]
    traces_path: Literal["/jaeger/ui/api/traces"]
    maximum_traces: StrictInt = Field(ge=1, le=100)


class InstrumentationSourcesConfig(V2Model):
    schema_version: Literal["live-telemetry.sources.v2"]
    prometheus: PrometheusSourceConfig
    opensearch: OpenSearchSourceConfig
    jaeger: JaegerSourceConfig


class InstrumentationReadinessConfig(V2Model):
    schema_version: Literal["live-telemetry.readiness.v2"]
    capture_window_seconds: Literal[30]
    ingestion_grace_seconds: Literal[15]
    poll_interval_seconds: Literal[5]
    maximum_readiness_seconds: Literal[45]
    maximum_probe_attempts: Literal[7]
    query_range_step_seconds: Literal[5]
    minimum_required_samples: Literal[3]
    development_stabilization_seconds: StrictInt = Field(ge=0, le=90)
    canonical_stabilization_seconds: StrictInt = Field(ge=90, le=300)


class InstrumentationReportingConfig(V2Model):
    schema_version: Literal["live-telemetry.reporting.v2"]
    public_result_json: str
    public_result_markdown: str
    public_human_brief: str
    claim_boundary: tuple[
        Literal["LIVE_LOCAL_SANDBOX_INSTRUMENTATION"],
        Literal["NO_FAULT_INJECTION"],
        Literal["NO_PROVIDER_CALL"],
        Literal["NO_MODEL_QUALITY_CLAIM"],
        Literal["NO_REMEDIATION"],
        Literal["NOT_PRODUCTION"],
        Literal["NOT_EXTERNAL_BENCHMARK"],
    ]


class InstrumentationConfig(V2Model):
    environment: InstrumentationEnvironmentConfig
    sources: InstrumentationSourcesConfig
    readiness: InstrumentationReadinessConfig
    reporting: InstrumentationReportingConfig


def _read_config(path: Path, model: type[V2Model]) -> V2Model:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"instrumentation config is unavailable: {path.name}")
    return model.model_validate_json(path.read_text(encoding="utf-8"))


def load_instrumentation_config(root: Path) -> InstrumentationConfig:
    return InstrumentationConfig(
        environment=InstrumentationEnvironmentConfig.model_validate(
            _read_config(root / "environment.json", InstrumentationEnvironmentConfig)
        ),
        sources=InstrumentationSourcesConfig.model_validate(
            _read_config(root / "sources.json", InstrumentationSourcesConfig)
        ),
        readiness=InstrumentationReadinessConfig.model_validate(
            _read_config(root / "readiness.json", InstrumentationReadinessConfig)
        ),
        reporting=InstrumentationReportingConfig.model_validate(
            _read_config(root / "reporting.json", InstrumentationReportingConfig)
        ),
    )


class SourceProbeStatus(str, Enum):
    AVAILABLE = "AVAILABLE"
    EMPTY = "EMPTY"
    HTTP_FAILED = "HTTP_FAILED"
    SCHEMA_MISMATCH = "SCHEMA_MISMATCH"
    FIELD_MAPPING_UNSUPPORTED = "FIELD_MAPPING_UNSUPPORTED"
    IDENTITY_MISMATCH = "IDENTITY_MISMATCH"
    INGESTION_TIMEOUT = "INGESTION_TIMEOUT"
    INVALID_RECORD = "INVALID_RECORD"


class SourceProbeResult(V2Model):
    schema_version: Literal["live-telemetry.source-probe.v2"] = (
        "live-telemetry.source-probe.v2"
    )
    source: TelemetrySource
    backend_kind: BackendKind
    status: SourceProbeStatus
    window_start: AwareDatetime
    window_end: AwareDatetime
    probe_started_at: AwareDatetime
    probe_ended_at: AwareDatetime
    attempt_count: int = Field(ge=1, le=7)
    backend_reachable: bool
    raw_response_count: int = Field(ge=0)
    parsed_record_count: int = Field(ge=0)
    target_record_count: int = Field(ge=0)
    service_catalog_count: int = Field(ge=0)
    target_service_present: bool
    selected_time_field: str | None = None
    selected_service_field: str | None = None
    identity_fields_present: tuple[str, ...] = ()
    raw_artifact_hashes: Mapping[str, str] = Field(default_factory=dict)
    evidence_refs: tuple[str, ...] = ()
    invalid_ref_count: int = Field(default=0, ge=0)
    safe_reason_code: str | None = None
    series_count: int = Field(default=0, ge=0)
    sample_count: int = Field(default=0, ge=0)
    finite_value_count: int = Field(default=0, ge=0)
    target_label_match_count: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def require_consistent_terminal(self) -> "SourceProbeResult":
        if self.window_end <= self.window_start:
            raise ValueError("source window end must follow start")
        if self.probe_ended_at < self.probe_started_at:
            raise ValueError("source probe end precedes start")
        if any(not _is_sha256(value) for value in self.raw_artifact_hashes.values()):
            raise ValueError("source raw artifact hash is invalid")
        if len(set(self.evidence_refs)) != len(self.evidence_refs):
            raise ValueError("source evidence refs contain duplicates")
        if self.status is SourceProbeStatus.AVAILABLE:
            if self.target_record_count <= 0:
                raise ValueError("AVAILABLE source lacks target records")
            if self.invalid_ref_count:
                raise ValueError("AVAILABLE source has invalid refs")
            if not self.evidence_refs:
                raise ValueError("AVAILABLE source lacks evidence refs")
            if self.safe_reason_code is not None:
                raise ValueError("AVAILABLE source has a failure reason")
        elif not self.safe_reason_code:
            raise ValueError("unavailable source lacks a safe reason")
        return self


def _is_sha256(value: str) -> bool:
    return len(value) == 64 and all(character in "0123456789abcdef" for character in value)


class SourceProbeFailure(RuntimeError):
    """Typed internal failure; exception text never enters a public result."""

    def __init__(
        self,
        *,
        source: TelemetrySource,
        backend_kind: BackendKind,
        status: SourceProbeStatus,
        safe_reason_code: str,
        backend_reachable: bool = True,
        attempt_count: int = 1,
    ) -> None:
        super().__init__(safe_reason_code)
        self.source = source
        self.backend_kind = backend_kind
        self.status = status
        self.safe_reason_code = safe_reason_code
        self.backend_reachable = backend_reachable
        self.attempt_count = attempt_count


class PrometheusSummary(V2Model):
    series_count: int = Field(ge=0)
    sample_count: int = Field(ge=0)
    finite_value_count: int = Field(ge=0)
    target_label_match_count: int = Field(ge=0)
    target_sample_count: int = Field(ge=0)
    target_value: float


def _mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an object")
    return value


def _prometheus_failure(reason: str, *, status: SourceProbeStatus) -> SourceProbeFailure:
    return SourceProbeFailure(
        source="METRICS",
        backend_kind="PROMETHEUS_HTTP_API",
        status=status,
        safe_reason_code=reason,
    )


def parse_prometheus_vector_v2(
    value: object, *, target_service: str, service_label: str = "service_name"
) -> PrometheusSummary:
    try:
        payload = _mapping(value, "Prometheus response")
        data = _mapping(payload.get("data"), "Prometheus data")
        result = data.get("result")
        if (
            payload.get("status") != "success"
            or data.get("resultType") != "vector"
            or not isinstance(result, list)
        ):
            raise ValueError("Prometheus response is not a successful vector")
        finite_count = 0
        target_matches = 0
        target_samples = 0
        target_value = 0.0
        for raw in result:
            item = _mapping(raw, "Prometheus vector item")
            labels = _mapping(item.get("metric", {}), "Prometheus labels")
            sample = item.get("value")
            if not isinstance(sample, list) or len(sample) != 2:
                raise ValueError("Prometheus sample is malformed")
            number = float(sample[1])
            if not math.isfinite(number):
                raise ArithmeticError("Prometheus sample is nonfinite")
            finite_count += 1
            if labels.get(service_label) == target_service:
                target_matches += 1
                target_samples += 1
                target_value += number
        return PrometheusSummary(
            series_count=len(result),
            sample_count=len(result),
            finite_value_count=finite_count,
            target_label_match_count=target_matches,
            target_sample_count=target_samples,
            target_value=target_value,
        )
    except ArithmeticError as error:
        raise _prometheus_failure(
            "NONFINITE_METRIC_VALUE", status=SourceProbeStatus.INVALID_RECORD
        ) from error
    except (TypeError, ValueError) as error:
        raise _prometheus_failure(
            "PROMETHEUS_QUERY_INVALID", status=SourceProbeStatus.SCHEMA_MISMATCH
        ) from error


def parse_prometheus_matrix_v2(
    value: object, *, target_service: str, service_label: str = "service_name"
) -> PrometheusSummary:
    try:
        payload = _mapping(value, "Prometheus range response")
        data = _mapping(payload.get("data"), "Prometheus range data")
        result = data.get("result")
        if (
            payload.get("status") != "success"
            or data.get("resultType") != "matrix"
            or not isinstance(result, list)
        ):
            raise ValueError("Prometheus response is not a successful matrix")
        sample_count = 0
        finite_count = 0
        target_matches = 0
        target_samples = 0
        target_value = 0.0
        for raw in result:
            item = _mapping(raw, "Prometheus matrix item")
            labels = _mapping(item.get("metric", {}), "Prometheus labels")
            samples = item.get("values")
            if not isinstance(samples, list):
                raise ValueError("Prometheus range samples are malformed")
            is_target = labels.get(service_label) == target_service
            if is_target:
                target_matches += 1
            for sample in samples:
                if not isinstance(sample, list) or len(sample) != 2:
                    raise ValueError("Prometheus range sample is malformed")
                number = float(sample[1])
                if not math.isfinite(number):
                    raise ArithmeticError("Prometheus range sample is nonfinite")
                sample_count += 1
                finite_count += 1
                if is_target:
                    target_samples += 1
                    target_value += number
        return PrometheusSummary(
            series_count=len(result),
            sample_count=sample_count,
            finite_value_count=finite_count,
            target_label_match_count=target_matches,
            target_sample_count=target_samples,
            target_value=target_value,
        )
    except ArithmeticError as error:
        raise _prometheus_failure(
            "NONFINITE_METRIC_VALUE", status=SourceProbeStatus.INVALID_RECORD
        ) from error
    except (TypeError, ValueError) as error:
        raise _prometheus_failure(
            "PROMETHEUS_QUERY_INVALID", status=SourceProbeStatus.SCHEMA_MISMATCH
        ) from error


def required_prometheus_value(summary: PrometheusSummary, *, empty_reason: str) -> float:
    if summary.target_label_match_count == 0 or summary.target_sample_count == 0:
        raise _prometheus_failure(empty_reason, status=SourceProbeStatus.EMPTY)
    return summary.target_value


class OpenSearchFields(V2Model):
    time_field: str
    service_field: str
    body_field: str | None = None
    severity_field: str | None = None


def _field_types(field_caps: Mapping[str, object], field: str) -> tuple[str, ...]:
    fields = _mapping(field_caps.get("fields"), "OpenSearch field caps")
    raw = fields.get(field)
    if not isinstance(raw, Mapping):
        return ()
    output: list[str] = []
    for declared, descriptor in raw.items():
        if not isinstance(descriptor, Mapping) or descriptor.get("searchable") is False:
            continue
        kind = descriptor.get("type", declared)
        if isinstance(kind, str):
            output.append(kind)
    return tuple(output)


def _select_field(
    field_caps: Mapping[str, object],
    candidates: Sequence[str],
    compatible: frozenset[str],
) -> str | None:
    for candidate in candidates:
        if compatible.intersection(_field_types(field_caps, candidate)):
            return candidate
    return None


def discover_opensearch_fields(
    value: object,
    *,
    time_candidates: Sequence[str],
    service_candidates: Sequence[str],
    body_candidates: Sequence[str],
    severity_candidates: Sequence[str],
) -> OpenSearchFields:
    try:
        field_caps = _mapping(value, "OpenSearch field caps response")
        time_field = _select_field(
            field_caps,
            time_candidates,
            frozenset({"date", "date_nanos", "long", "unsigned_long"}),
        )
        service_field = _select_field(
            field_caps,
            service_candidates,
            frozenset({"keyword", "constant_keyword"}),
        )
        body_field = _select_field(
            field_caps,
            body_candidates,
            frozenset({"text", "keyword", "match_only_text"}),
        )
        severity_field = _select_field(
            field_caps,
            severity_candidates,
            frozenset({"keyword", "constant_keyword", "text"}),
        )
        if not time_field:
            raise SourceProbeFailure(
                source="LOGS",
                backend_kind="OPENSEARCH_HTTP_API",
                status=SourceProbeStatus.FIELD_MAPPING_UNSUPPORTED,
                safe_reason_code="LOG_TIME_FIELD_UNRESOLVED",
            )
        if not service_field:
            raise SourceProbeFailure(
                source="LOGS",
                backend_kind="OPENSEARCH_HTTP_API",
                status=SourceProbeStatus.FIELD_MAPPING_UNSUPPORTED,
                safe_reason_code="LOG_SERVICE_FIELD_UNRESOLVED",
            )
        return OpenSearchFields(
            time_field=time_field,
            service_field=service_field,
            body_field=body_field,
            severity_field=severity_field,
        )
    except SourceProbeFailure:
        raise
    except (TypeError, ValueError) as error:
        raise SourceProbeFailure(
            source="LOGS",
            backend_kind="OPENSEARCH_HTTP_API",
            status=SourceProbeStatus.SCHEMA_MISMATCH,
            safe_reason_code="LOG_SCHEMA_INVALID",
        ) from error


def build_opensearch_target_query(
    selected: OpenSearchFields,
    *,
    target_service: str,
    window_start: datetime,
    window_end: datetime,
    maximum_hits: int = 50,
) -> dict[str, object]:
    return {
        "size": maximum_hits,
        "sort": [{selected.time_field: {"order": "asc"}}],
        "query": {
            "bool": {
                "filter": [
                    {"term": {selected.service_field: target_service}},
                    {
                        "range": {
                            selected.time_field: {
                                "gte": window_start.isoformat(),
                                "lte": window_end.isoformat(),
                            }
                        }
                    },
                ]
            }
        },
    }


def _source_field(field: str | None) -> str | None:
    if field is None:
        return None
    return field.removesuffix(".keyword")


def _nested(value: Mapping[str, object], path: str | None) -> object | None:
    if not path:
        return None
    if path in value:
        return value[path]
    current: object = value
    parts = path.split(".")
    for index, part in enumerate(parts):
        if not isinstance(current, Mapping):
            return None
        remainder = ".".join(parts[index:])
        if remainder in current:
            return current[remainder]
        if part not in current:
            return None
        current = current[part]
    return current


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
        raise ValueError("timestamp is absent")
    return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(timezone.utc)


def parse_opensearch_logs_v2(
    value: object, *, selected: OpenSearchFields, target_service: str
) -> tuple[LogEvidence, ...]:
    try:
        payload = _mapping(value, "OpenSearch response")
        hits = _mapping(payload.get("hits"), "OpenSearch hits")
        raw_hits = hits.get("hits")
        if not isinstance(raw_hits, list):
            raise ValueError("OpenSearch hit list is unavailable")
        output: list[LogEvidence] = []
        for raw_hit in raw_hits:
            hit = _mapping(raw_hit, "OpenSearch hit")
            source = _mapping(hit.get("_source"), "OpenSearch source")
            service = _nested(source, _source_field(selected.service_field))
            if service != target_service:
                continue
            body_raw = _nested(source, _source_field(selected.body_field))
            if isinstance(body_raw, Mapping):
                body = json.dumps(body_raw, ensure_ascii=False, sort_keys=True)
            else:
                body = str(body_raw or "OpenTelemetry log record").strip()
            trace_id = str(_nested(source, "traceId") or _nested(source, "trace.id") or "").casefold() or None
            span_id = str(_nested(source, "spanId") or _nested(source, "span.id") or "").casefold() or None
            if trace_id is not None and len(trace_id) != 32:
                trace_id = None
            if span_id is not None and len(span_id) != 16:
                span_id = None
            output.append(
                LogEvidence(
                    observed_at=_parse_datetime(
                        _nested(source, _source_field(selected.time_field))
                    ),
                    service_name=target_service,
                    service_instance_id=(
                        str(_nested(source, "resource.service.instance.id") or "") or None
                    ),
                    container_id=(
                        str(_nested(source, "resource.container.id") or "") or None
                    ),
                    host_id=str(_nested(source, "resource.host.id") or "") or None,
                    severity=str(
                        _nested(source, _source_field(selected.severity_field)) or "INFO"
                    )[:64],
                    body=(body or "OpenTelemetry log record")[:2_000],
                    trace_id=trace_id,
                    span_id=span_id,
                )
            )
        return tuple(sorted(output, key=lambda item: item.observed_at)[:100])
    except (TypeError, ValueError) as error:
        raise SourceProbeFailure(
            source="LOGS",
            backend_kind="OPENSEARCH_HTTP_API",
            status=SourceProbeStatus.SCHEMA_MISMATCH,
            safe_reason_code="LOG_SCHEMA_INVALID",
        ) from error


def parse_jaeger_services(value: object) -> tuple[str, ...]:
    try:
        payload = _mapping(value, "Jaeger service catalog")
        raw = payload.get("data")
        if not isinstance(raw, list) or any(not isinstance(item, str) for item in raw):
            raise ValueError("Jaeger service catalog is malformed")
        return tuple(sorted(set(raw)))
    except (TypeError, ValueError) as error:
        raise SourceProbeFailure(
            source="TRACES",
            backend_kind="JAEGER_QUERY_API",
            status=SourceProbeStatus.SCHEMA_MISMATCH,
            safe_reason_code="JAEGER_SERVICE_CATALOG_INVALID",
        ) from error


def _tags(value: object) -> dict[str, object]:
    if not isinstance(value, list):
        return {}
    return {
        str(item["key"]): item.get("value")
        for item in value
        if isinstance(item, Mapping) and isinstance(item.get("key"), str)
    }


def parse_jaeger_traces_v2(
    value: object, *, target_service: str
) -> tuple[TraceEvidence, ...]:
    try:
        payload = _mapping(value, "Jaeger response")
        traces = payload.get("data")
        if not isinstance(traces, list):
            raise ValueError("Jaeger trace list is unavailable")
        output: list[TraceEvidence] = []
        for raw_trace in traces:
            trace = _mapping(raw_trace, "Jaeger trace")
            processes = _mapping(trace.get("processes", {}), "Jaeger processes")
            spans = trace.get("spans")
            if not isinstance(spans, list):
                raise ValueError("Jaeger spans are unavailable")
            for raw_span in spans:
                span = _mapping(raw_span, "Jaeger span")
                process = _mapping(
                    processes.get(str(span.get("processID", "")), {}),
                    "Jaeger process",
                )
                if process.get("serviceName") != target_service:
                    continue
                trace_id = str(span.get("traceID") or trace.get("traceID") or "").casefold()
                span_id = str(span.get("spanID") or "").casefold()
                parent = str(span.get("parentSpanID") or "").casefold() or None
                if len(trace_id) != 32 or len(span_id) != 16:
                    raise ValueError("Jaeger target span identity is malformed")
                if parent is not None and len(parent) != 16:
                    parent = None
                process_tags = _tags(process.get("tags"))
                span_tags = _tags(span.get("tags"))
                status_raw = str(
                    span_tags.get("otel.status_code") or span_tags.get("error") or "UNSET"
                ).upper()
                status: Literal["OK", "ERROR", "UNSET"] = (
                    "ERROR"
                    if status_raw in {"ERROR", "TRUE", "2"}
                    else "OK" if status_raw in {"OK", "1"} else "UNSET"
                )
                started_raw = span.get("startTime", 0)
                duration_raw = span.get("duration", 0)
                if not isinstance(started_raw, (int, float, str)) or not isinstance(
                    duration_raw, (int, float, str)
                ):
                    raise ValueError("Jaeger target span timing is malformed")
                output.append(
                    TraceEvidence(
                        trace_id=trace_id,
                        span_id=span_id,
                        parent_span_id=parent,
                        service_name=target_service,
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
    except (TypeError, ValueError) as error:
        raise SourceProbeFailure(
            source="TRACES",
            backend_kind="JAEGER_QUERY_API",
            status=SourceProbeStatus.SCHEMA_MISMATCH,
            safe_reason_code="TRACE_SCHEMA_INVALID",
        ) from error


class RawArtifact(V2Model):
    source: TelemetrySource
    private_artifact_relative_key: str
    sha256: str = Field(pattern=SHA256_PATTERN)


class EvidenceMetadata(V2Model):
    source: TelemetrySource
    private_artifact_relative_key: str
    raw_artifact_sha256: str = Field(pattern=SHA256_PATTERN)
    normalized_record_sha256: str = Field(pattern=SHA256_PATTERN)
    window_start: AwareDatetime
    window_end: AwareDatetime
    target_service: str


_REF_PREFIX: Mapping[TelemetrySource, str] = {
    "METRICS": "metric",
    "LOGS": "log",
    "TRACES": "trace",
}


class PrivateArtifactStore:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        ensure_private_directory(self.root)
        self._records: dict[str, EvidenceMetadata] = {}

    def write_raw(self, source: TelemetrySource, name: str, value: object) -> RawArtifact:
        if not name or any(character not in "abcdefghijklmnopqrstuvwxyz0123456789-_" for character in name):
            raise ValueError("raw artifact name is invalid")
        relative = Path("raw") / source.casefold() / f"{name}.json"
        digest = write_private_json(self.root / relative, value, create_once=True)
        return RawArtifact(
            source=source,
            private_artifact_relative_key=relative.as_posix(),
            sha256=digest,
        )

    def write_diagnostic(
        self, source: TelemetrySource, name: str, value: object
    ) -> Path:
        if not name or any(
            character not in "abcdefghijklmnopqrstuvwxyz0123456789-_"
            for character in name
        ):
            raise ValueError("diagnostic artifact name is invalid")
        relative = Path("diagnostics") / source.casefold() / f"{name}.json"
        write_private_json(self.root / relative, value, create_once=True)
        return relative

    def add_record(
        self,
        *,
        source: TelemetrySource,
        raw_artifact: RawArtifact,
        normalized_record: object,
        window_start: datetime,
        window_end: datetime,
        target_service: str,
        ordinal: int = 0,
    ) -> str:
        if raw_artifact.source != source:
            raise ValueError("evidence source differs from raw artifact")
        normalized_sha256 = hashlib.sha256(canonical_json_bytes(normalized_record)).hexdigest()
        identifier = hashlib.sha256(
            canonical_json_bytes(
                {
                    "source": source,
                    "raw": raw_artifact.sha256,
                    "normalized": normalized_sha256,
                    "window_start": window_start.isoformat(),
                    "window_end": window_end.isoformat(),
                    "target_service": target_service,
                    "ordinal": ordinal,
                }
            )
        ).hexdigest()[:24]
        reference = f"{_REF_PREFIX[source]}:{identifier}"
        if reference in self._records:
            raise ValueError("duplicate evidence ref")
        self._records[reference] = EvidenceMetadata(
            source=source,
            private_artifact_relative_key=raw_artifact.private_artifact_relative_key,
            raw_artifact_sha256=raw_artifact.sha256,
            normalized_record_sha256=normalized_sha256,
            window_start=window_start,
            window_end=window_end,
            target_service=target_service,
        )
        return reference

    def seal(self) -> Path:
        path = self.root / "resolver.json"
        write_private_json(
            path,
            {
                "schema_version": "live-telemetry.evidence-resolver.v2",
                "records": {
                    reference: metadata.model_dump(mode="json")
                    for reference, metadata in sorted(self._records.items())
                },
            },
            create_once=True,
        )
        return path


class EvidenceResolver:
    def __init__(self, records: Mapping[str, EvidenceMetadata]) -> None:
        self.records = dict(records)

    @classmethod
    def from_file(cls, path: Path) -> "EvidenceResolver":
        if path.is_symlink() or not path.is_file():
            raise ValueError("evidence resolver is unavailable")
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, Mapping) or payload.get("schema_version") != "live-telemetry.evidence-resolver.v2":
            raise ValueError("evidence resolver schema is invalid")
        raw_records = payload.get("records")
        if not isinstance(raw_records, Mapping):
            raise ValueError("evidence resolver records are invalid")
        return cls(
            {
                str(reference): EvidenceMetadata.model_validate(metadata)
                for reference, metadata in raw_records.items()
            }
        )

    def resolve(self, reference: str) -> EvidenceMetadata:
        metadata = self.records.get(reference)
        if metadata is not None:
            expected = _REF_PREFIX[metadata.source] + ":"
            if not reference.startswith(expected):
                raise ValueError("evidence ref prefix mismatch")
            return metadata
        if ":" in reference:
            suffix = reference.split(":", 1)[1]
            if any(item.split(":", 1)[1] == suffix for item in self.records):
                raise ValueError("evidence ref prefix mismatch")
        raise ValueError("invalid evidence ref")


class JsonRequester(Protocol):
    def __call__(
        self,
        url: str,
        *,
        method: str = "GET",
        payload: object | None = None,
        timeout_seconds: float = 5,
    ) -> object: ...


def _json_request(
    url: str,
    *,
    method: str = "GET",
    payload: object | None = None,
    timeout_seconds: float = 5,
) -> object:
    data = None if payload is None else canonical_json_bytes(payload).rstrip(b"\n")
    request = Request(
        url,
        data=data,
        method=method,
        headers={"Accept": "application/json", "Content-Type": "application/json"},
    )
    with urlopen(request, timeout=timeout_seconds) as response:  # noqa: S310 - typed loopback endpoints only
        if response.status < 200 or response.status >= 300:
            raise ConnectionError("local backend returned a non-success status")
        raw = response.read(5_000_001)
        if len(raw) > 5_000_000:
            raise ValueError("local telemetry response exceeds the private bound")
        return json.loads(raw.decode("utf-8"))


def _render_query(template: str, target_service: str) -> str:
    if template.count("$TARGET_SERVICE") != 1:
        raise ValueError("metric query template must contain one target placeholder")
    if not target_service or any(
        character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-"
        for character in target_service
    ):
        raise ValueError("target service is not query-safe")
    return template.replace("$TARGET_SERVICE", target_service)


def _source_terminal(
    *,
    source: TelemetrySource,
    backend_kind: BackendKind,
    status: SourceProbeStatus,
    window_start: datetime,
    window_end: datetime,
    probe_started_at: datetime,
    attempt_count: int,
    backend_reachable: bool,
    raw_artifacts: Mapping[str, str],
    safe_reason_code: str,
    service_catalog_count: int = 0,
    target_service_present: bool = False,
    selected_time_field: str | None = None,
    selected_service_field: str | None = None,
) -> SourceProbeResult:
    return SourceProbeResult(
        source=source,
        backend_kind=backend_kind,
        status=status,
        window_start=window_start,
        window_end=window_end,
        probe_started_at=probe_started_at,
        probe_ended_at=datetime.now(timezone.utc),
        attempt_count=attempt_count,
        backend_reachable=backend_reachable,
        raw_response_count=len(raw_artifacts),
        parsed_record_count=0,
        target_record_count=0,
        service_catalog_count=service_catalog_count,
        target_service_present=target_service_present,
        selected_time_field=selected_time_field,
        selected_service_field=selected_service_field,
        raw_artifact_hashes=dict(raw_artifacts),
        safe_reason_code=safe_reason_code,
    )


class MetricsSourceProbe:
    source: Literal["METRICS"] = "METRICS"
    backend_kind: Literal["PROMETHEUS_HTTP_API"] = "PROMETHEUS_HTTP_API"

    def __init__(
        self,
        *,
        endpoint: str,
        target_service: str,
        config: PrometheusSourceConfig,
        readiness: InstrumentationReadinessConfig,
        store: PrivateArtifactStore,
        window_start: datetime,
        window_end: datetime,
        request_json: JsonRequester = _json_request,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.endpoint = endpoint
        self.target_service = target_service
        self.config = config
        self.readiness = readiness
        self.store = store
        self.window_start = window_start
        self.window_end = window_end
        self.request_json = request_json
        self.sleep = sleep
        self.raw_artifacts: dict[str, str] = {}

    def _raw(self, name: str, value: object) -> RawArtifact:
        artifact = self.store.write_raw("METRICS", name, value)
        self.raw_artifacts[artifact.private_artifact_relative_key] = artifact.sha256
        return artifact

    def _get(self, path: str) -> object:
        return self.request_json(f"{self.endpoint}{path}")

    def _query(self, query: str, *, range_query: bool = False) -> object:
        path = self.config.query_range_path if range_query else self.config.query_path
        parameters: dict[str, str] = {"query": query}
        if range_query:
            parameters.update(
                {
                    "start": f"{self.window_start.timestamp():.3f}",
                    "end": f"{self.window_end.timestamp():.3f}",
                    "step": str(self.readiness.query_range_step_seconds),
                }
            )
        else:
            parameters["time"] = f"{self.window_end.timestamp():.3f}"
        return self.request_json(f"{self.endpoint}{path}?{urlencode(parameters)}")

    def _attempt(self, attempt: int, started: datetime) -> SourceProbeResult:
        status_raw = self._get(self.config.status_path)
        self._raw(f"attempt-{attempt:02d}-status", status_raw)
        status_payload = _mapping(status_raw, "Prometheus status response")
        if status_payload.get("status") != "success":
            raise _prometheus_failure(
                "PROMETHEUS_QUERY_INVALID", status=SourceProbeStatus.SCHEMA_MISMATCH
            )
        names_raw = self._get(self.config.metric_names_path)
        self._raw(f"attempt-{attempt:02d}-metric-names", names_raw)
        names_payload = _mapping(names_raw, "Prometheus metric names response")
        names = names_payload.get("data")
        if names_payload.get("status") != "success" or not isinstance(names, list):
            raise _prometheus_failure(
                "PROMETHEUS_QUERY_INVALID", status=SourceProbeStatus.SCHEMA_MISMATCH
            )
        if not set(self.config.required_metric_names).issubset(
            {str(item) for item in names}
        ):
            raise _prometheus_failure(
                "TARGET_TOTAL_SERIES_EMPTY", status=SourceProbeStatus.EMPTY
            )
        queries = {
            "total": _render_query(
                self.config.total_query_template, self.target_service
            ),
            "error": _render_query(
                self.config.error_query_template, self.target_service
            ),
            "p95": _render_query(self.config.p95_query_template, self.target_service),
            "health": _render_query(
                self.config.health_query_template, self.target_service
            ),
        }
        raw: dict[str, object] = {}
        artifacts: dict[str, RawArtifact] = {}
        summaries: dict[str, PrometheusSummary] = {}
        for name, query in queries.items():
            raw[name] = self._query(query)
            artifacts[name] = self._raw(f"attempt-{attempt:02d}-{name}", raw[name])
            summaries[name] = parse_prometheus_vector_v2(
                raw[name],
                target_service=self.target_service,
                service_label=self.config.service_label,
            )
        range_raw = self._query(queries["total"], range_query=True)
        artifacts["range"] = self._raw(f"attempt-{attempt:02d}-range", range_raw)
        summaries["range"] = parse_prometheus_matrix_v2(
            range_raw,
            target_service=self.target_service,
            service_label=self.config.service_label,
        )
        total = required_prometheus_value(
            summaries["total"], empty_reason="TARGET_TOTAL_SERIES_EMPTY"
        )
        if total <= 0:
            raise _prometheus_failure(
                "TARGET_TOTAL_SERIES_EMPTY", status=SourceProbeStatus.EMPTY
            )
        errors = (
            summaries["error"].target_value
            if summaries["error"].target_label_match_count
            else 0.0
        )
        p95 = required_prometheus_value(
            summaries["p95"], empty_reason="TARGET_LATENCY_SERIES_EMPTY"
        )
        health = required_prometheus_value(
            summaries["health"], empty_reason="TARGET_HEALTH_SERIES_EMPTY"
        )
        if health <= 0:
            raise _prometheus_failure(
                "TARGET_HEALTH_SERIES_EMPTY",
                status=SourceProbeStatus.IDENTITY_MISMATCH,
            )
        if summaries["range"].target_sample_count < self.readiness.minimum_required_samples:
            raise _prometheus_failure(
                "TARGET_CADENCE_INSUFFICIENT", status=SourceProbeStatus.EMPTY
            )
        references: list[str] = []
        normalized = {
            "total": {
                "kind": "total",
                "value": total,
                "service": self.target_service,
            },
            "error": {
                "kind": "error",
                "value": min(errors, total),
                "service": self.target_service,
            },
            "p95": {
                "kind": "p95",
                "value": max(p95, 0.0),
                "service": self.target_service,
            },
            "health": {
                "kind": "health",
                "value": health,
                "service": self.target_service,
            },
            "range": {
                "kind": "range",
                "samples": summaries["range"].target_sample_count,
                "service": self.target_service,
            },
        }
        for name, record in normalized.items():
            references.append(
                self.store.add_record(
                    source="METRICS",
                    raw_artifact=artifacts[name],
                    normalized_record=record,
                    window_start=self.window_start,
                    window_end=self.window_end,
                    target_service=self.target_service,
                )
            )
        series_count = sum(item.series_count for item in summaries.values())
        sample_count = sum(item.sample_count for item in summaries.values())
        finite_count = sum(item.finite_value_count for item in summaries.values())
        target_matches = sum(
            item.target_label_match_count for item in summaries.values()
        )
        return SourceProbeResult(
            source="METRICS",
            backend_kind="PROMETHEUS_HTTP_API",
            status=SourceProbeStatus.AVAILABLE,
            window_start=self.window_start,
            window_end=self.window_end,
            probe_started_at=started,
            probe_ended_at=datetime.now(timezone.utc),
            attempt_count=attempt,
            backend_reachable=True,
            raw_response_count=len(self.raw_artifacts),
            parsed_record_count=finite_count,
            target_record_count=len(references),
            service_catalog_count=0,
            target_service_present=True,
            selected_service_field=self.config.service_label,
            identity_fields_present=("service.name",),
            raw_artifact_hashes=dict(self.raw_artifacts),
            evidence_refs=tuple(references),
            invalid_ref_count=0,
            series_count=series_count,
            sample_count=sample_count,
            finite_value_count=finite_count,
            target_label_match_count=target_matches,
        )

    def probe(self) -> SourceProbeResult:
        last: SourceProbeResult | None = None
        deadline = time.monotonic() + self.readiness.maximum_readiness_seconds
        for attempt in range(1, self.readiness.maximum_probe_attempts + 1):
            started = datetime.now(timezone.utc)
            try:
                return self._attempt(attempt, started)
            except SourceProbeFailure as failure:
                last = _source_terminal(
                    source="METRICS",
                    backend_kind="PROMETHEUS_HTTP_API",
                    status=failure.status,
                    window_start=self.window_start,
                    window_end=self.window_end,
                    probe_started_at=started,
                    attempt_count=attempt,
                    backend_reachable=failure.backend_reachable,
                    raw_artifacts=self.raw_artifacts,
                    safe_reason_code=failure.safe_reason_code,
                )
            except (HTTPError, URLError, TimeoutError, OSError):
                last = _source_terminal(
                    source="METRICS",
                    backend_kind="PROMETHEUS_HTTP_API",
                    status=SourceProbeStatus.HTTP_FAILED,
                    window_start=self.window_start,
                    window_end=self.window_end,
                    probe_started_at=started,
                    attempt_count=attempt,
                    backend_reachable=False,
                    raw_artifacts=self.raw_artifacts,
                    safe_reason_code="PROMETHEUS_UNREACHABLE",
                )
            except Exception:
                last = _source_terminal(
                    source="METRICS",
                    backend_kind="PROMETHEUS_HTTP_API",
                    status=SourceProbeStatus.SCHEMA_MISMATCH,
                    window_start=self.window_start,
                    window_end=self.window_end,
                    probe_started_at=started,
                    attempt_count=attempt,
                    backend_reachable=True,
                    raw_artifacts=self.raw_artifacts,
                    safe_reason_code="PROMETHEUS_QUERY_INVALID",
                )
            remaining = deadline - time.monotonic()
            if attempt < self.readiness.maximum_probe_attempts and remaining > 0:
                self.sleep(min(self.readiness.poll_interval_seconds, remaining))
            if time.monotonic() >= deadline:
                break
        assert last is not None
        return last.model_copy(update={"status": SourceProbeStatus.INGESTION_TIMEOUT})


def _opensearch_catalog(value: object) -> tuple[str, ...]:
    payload = _mapping(value, "OpenSearch catalog response")
    aggregations = _mapping(payload.get("aggregations"), "OpenSearch aggregations")
    services = _mapping(aggregations.get("services"), "OpenSearch services")
    buckets = services.get("buckets")
    if not isinstance(buckets, list):
        raise ValueError("OpenSearch service buckets are unavailable")
    values = [
        str(item.get("key"))
        for item in buckets
        if isinstance(item, Mapping) and isinstance(item.get("key"), str)
    ]
    return tuple(sorted(set(values)))


class LogsSourceProbe:
    source: Literal["LOGS"] = "LOGS"
    backend_kind: Literal["OPENSEARCH_HTTP_API"] = "OPENSEARCH_HTTP_API"

    def __init__(
        self,
        *,
        endpoint: str,
        target_service: str,
        config: OpenSearchSourceConfig,
        readiness: InstrumentationReadinessConfig,
        store: PrivateArtifactStore,
        window_start: datetime,
        window_end: datetime,
        request_json: JsonRequester = _json_request,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.endpoint = endpoint
        self.target_service = target_service
        self.config = config
        self.readiness = readiness
        self.store = store
        self.window_start = window_start
        self.window_end = window_end
        self.request_json = request_json
        self.sleep = sleep
        self.raw_artifacts: dict[str, str] = {}
        self.selected: OpenSearchFields | None = None
        self.service_catalog_count = 0
        self.target_service_present = False
        self.request_phase = "NOT_STARTED"

    def _raw(self, name: str, value: object) -> RawArtifact:
        artifact = self.store.write_raw("LOGS", name, value)
        self.raw_artifacts[artifact.private_artifact_relative_key] = artifact.sha256
        return artifact

    def _attempt(self, attempt: int, started: datetime) -> SourceProbeResult:
        self.request_phase = "INDEX_DISCOVERY"
        try:
            indices_raw = self.request_json(
                f"{self.endpoint}{self.config.indices_path}"
            )
        except HTTPError as error:
            self.store.write_diagnostic(
                "LOGS",
                f"attempt-{attempt:02d}-indices-http",
                {
                    "exception_type": type(error).__name__,
                    "http_status": error.code,
                    "request_phase": "INDEX_DISCOVERY",
                },
            )
            if error.code == 404:
                raise SourceProbeFailure(
                    source="LOGS",
                    backend_kind="OPENSEARCH_HTTP_API",
                    status=SourceProbeStatus.EMPTY,
                    safe_reason_code="LOG_INDEX_MISSING",
                    backend_reachable=True,
                ) from error
            raise
        self._raw(f"attempt-{attempt:02d}-indices", indices_raw)
        if not isinstance(indices_raw, list):
            raise ValueError("OpenSearch indices response is malformed")
        if not any(
            isinstance(item, Mapping)
            and str(item.get("index", "")).startswith("otel-logs-")
            for item in indices_raw
        ):
            raise SourceProbeFailure(
                source="LOGS",
                backend_kind="OPENSEARCH_HTTP_API",
                status=SourceProbeStatus.EMPTY,
                safe_reason_code="LOG_INDEX_MISSING",
            )
        self.request_phase = "FIELD_CAPS"
        field_allowlist = sorted(
            set(
                self.config.time_field_candidates
                + self.config.service_field_candidates
                + self.config.body_field_candidates
                + self.config.severity_field_candidates
            )
        )
        field_parameters = urlencode({"fields": ",".join(field_allowlist)})
        field_caps_raw = self.request_json(
            f"{self.endpoint}{self.config.field_caps_path}?{field_parameters}"
        )
        self._raw(f"attempt-{attempt:02d}-field-caps", field_caps_raw)
        selected = discover_opensearch_fields(
            field_caps_raw,
            time_candidates=self.config.time_field_candidates,
            service_candidates=self.config.service_field_candidates,
            body_candidates=self.config.body_field_candidates,
            severity_candidates=self.config.severity_field_candidates,
        )
        self.selected = selected
        catalog_query = {
            "size": 0,
            "aggs": {
                "services": {
                    "terms": {"field": selected.service_field, "size": 100}
                }
            },
        }
        self.request_phase = "SERVICE_CATALOG"
        catalog_raw = self.request_json(
            f"{self.endpoint}/{self.config.index_pattern}/_search",
            method="POST",
            payload=catalog_query,
        )
        self._raw(f"attempt-{attempt:02d}-service-catalog", catalog_raw)
        catalog = _opensearch_catalog(catalog_raw)
        self.service_catalog_count = len(catalog)
        self.target_service_present = self.target_service in catalog
        query = build_opensearch_target_query(
            selected,
            target_service=self.target_service,
            window_start=self.window_start,
            window_end=self.window_end,
            maximum_hits=self.config.maximum_hits,
        )
        self.request_phase = "TARGET_QUERY"
        target_raw = self.request_json(
            f"{self.endpoint}/{self.config.index_pattern}/_search",
            method="POST",
            payload=query,
        )
        target_artifact = self._raw(f"attempt-{attempt:02d}-target", target_raw)
        records = parse_opensearch_logs_v2(
            target_raw, selected=selected, target_service=self.target_service
        )
        if not records:
            raise SourceProbeFailure(
                source="LOGS",
                backend_kind="OPENSEARCH_HTTP_API",
                status=SourceProbeStatus.EMPTY,
                safe_reason_code="TARGET_LOGS_EMPTY",
            )
        references = tuple(
            self.store.add_record(
                source="LOGS",
                raw_artifact=target_artifact,
                normalized_record=record,
                window_start=self.window_start,
                window_end=self.window_end,
                target_service=self.target_service,
                ordinal=index,
            )
            for index, record in enumerate(records)
        )
        identity = {"service.name"}
        for record in records:
            if record.service_instance_id:
                identity.add("service.instance.id")
            if record.container_id:
                identity.add("container.id")
            if record.host_id:
                identity.add("host.id")
            if record.trace_id:
                identity.add("trace_id")
            if record.span_id:
                identity.add("span_id")
        return SourceProbeResult(
            source="LOGS",
            backend_kind="OPENSEARCH_HTTP_API",
            status=SourceProbeStatus.AVAILABLE,
            window_start=self.window_start,
            window_end=self.window_end,
            probe_started_at=started,
            probe_ended_at=datetime.now(timezone.utc),
            attempt_count=attempt,
            backend_reachable=True,
            raw_response_count=len(self.raw_artifacts),
            parsed_record_count=len(records),
            target_record_count=len(records),
            service_catalog_count=len(catalog),
            target_service_present=True,
            selected_time_field=selected.time_field,
            selected_service_field=selected.service_field,
            identity_fields_present=tuple(sorted(identity)),
            raw_artifact_hashes=dict(self.raw_artifacts),
            evidence_refs=references,
            invalid_ref_count=0,
        )

    def probe(self) -> SourceProbeResult:
        last: SourceProbeResult | None = None
        deadline = time.monotonic() + self.readiness.maximum_readiness_seconds
        for attempt in range(1, self.readiness.maximum_probe_attempts + 1):
            started = datetime.now(timezone.utc)
            try:
                return self._attempt(attempt, started)
            except SourceProbeFailure as failure:
                last = _source_terminal(
                    source="LOGS",
                    backend_kind="OPENSEARCH_HTTP_API",
                    status=failure.status,
                    window_start=self.window_start,
                    window_end=self.window_end,
                    probe_started_at=started,
                    attempt_count=attempt,
                    backend_reachable=failure.backend_reachable,
                    raw_artifacts=self.raw_artifacts,
                    safe_reason_code=failure.safe_reason_code,
                    service_catalog_count=self.service_catalog_count,
                    target_service_present=self.target_service_present,
                    selected_time_field=(self.selected.time_field if self.selected else None),
                    selected_service_field=(
                        self.selected.service_field if self.selected else None
                    ),
                )
            except (HTTPError, URLError, TimeoutError, OSError) as error:
                self.store.write_diagnostic(
                    "LOGS",
                    f"attempt-{attempt:02d}-request-failure",
                    {
                        "exception_type": type(error).__name__,
                        "http_status": (
                            error.code if isinstance(error, HTTPError) else None
                        ),
                        "request_phase": self.request_phase,
                    },
                )
                last = _source_terminal(
                    source="LOGS",
                    backend_kind="OPENSEARCH_HTTP_API",
                    status=SourceProbeStatus.HTTP_FAILED,
                    window_start=self.window_start,
                    window_end=self.window_end,
                    probe_started_at=started,
                    attempt_count=attempt,
                    backend_reachable=isinstance(error, HTTPError),
                    raw_artifacts=self.raw_artifacts,
                    safe_reason_code=(
                        "OPENSEARCH_HTTP_FAILED"
                        if isinstance(error, HTTPError)
                        else "OPENSEARCH_UNREACHABLE"
                    ),
                )
            except Exception:
                last = _source_terminal(
                    source="LOGS",
                    backend_kind="OPENSEARCH_HTTP_API",
                    status=SourceProbeStatus.SCHEMA_MISMATCH,
                    window_start=self.window_start,
                    window_end=self.window_end,
                    probe_started_at=started,
                    attempt_count=attempt,
                    backend_reachable=True,
                    raw_artifacts=self.raw_artifacts,
                    safe_reason_code="LOG_SCHEMA_INVALID",
                )
            remaining = deadline - time.monotonic()
            if attempt < self.readiness.maximum_probe_attempts and remaining > 0:
                self.sleep(min(self.readiness.poll_interval_seconds, remaining))
            if time.monotonic() >= deadline:
                break
        assert last is not None
        return last.model_copy(update={"status": SourceProbeStatus.INGESTION_TIMEOUT})


class TracesSourceProbe:
    source: Literal["TRACES"] = "TRACES"
    backend_kind: Literal["JAEGER_QUERY_API"] = "JAEGER_QUERY_API"

    def __init__(
        self,
        *,
        endpoint: str,
        target_service: str,
        config: JaegerSourceConfig,
        readiness: InstrumentationReadinessConfig,
        store: PrivateArtifactStore,
        window_start: datetime,
        window_end: datetime,
        request_json: JsonRequester = _json_request,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.endpoint = endpoint
        self.target_service = target_service
        self.config = config
        self.readiness = readiness
        self.store = store
        self.window_start = window_start
        self.window_end = window_end
        self.request_json = request_json
        self.sleep = sleep
        self.raw_artifacts: dict[str, str] = {}
        self.service_catalog_count = 0
        self.target_service_present = False

    def _raw(self, name: str, value: object) -> RawArtifact:
        artifact = self.store.write_raw("TRACES", name, value)
        self.raw_artifacts[artifact.private_artifact_relative_key] = artifact.sha256
        return artifact

    def _attempt(self, attempt: int, started: datetime) -> SourceProbeResult:
        catalog_raw = self.request_json(
            f"{self.endpoint}{self.config.service_catalog_path}"
        )
        self._raw(f"attempt-{attempt:02d}-service-catalog", catalog_raw)
        catalog = parse_jaeger_services(catalog_raw)
        self.service_catalog_count = len(catalog)
        self.target_service_present = self.target_service in catalog
        if self.target_service not in catalog:
            raise SourceProbeFailure(
                source="TRACES",
                backend_kind="JAEGER_QUERY_API",
                status=SourceProbeStatus.IDENTITY_MISMATCH,
                safe_reason_code="TARGET_SERVICE_NOT_IN_JAEGER",
            )
        query = urlencode(
            {
                "service": self.target_service,
                "start": str(int(self.window_start.timestamp() * 1_000_000)),
                "end": str(int(self.window_end.timestamp() * 1_000_000)),
                "limit": str(self.config.maximum_traces),
            }
        )
        traces_raw = self.request_json(
            f"{self.endpoint}{self.config.traces_path}?{query}"
        )
        traces_artifact = self._raw(f"attempt-{attempt:02d}-target", traces_raw)
        records = parse_jaeger_traces_v2(
            traces_raw, target_service=self.target_service
        )
        if not records:
            raise SourceProbeFailure(
                source="TRACES",
                backend_kind="JAEGER_QUERY_API",
                status=SourceProbeStatus.EMPTY,
                safe_reason_code="TARGET_TRACES_EMPTY",
            )
        references = tuple(
            self.store.add_record(
                source="TRACES",
                raw_artifact=traces_artifact,
                normalized_record=record,
                window_start=self.window_start,
                window_end=self.window_end,
                target_service=self.target_service,
                ordinal=index,
            )
            for index, record in enumerate(records)
        )
        identity = {"service.name", "trace_id", "span_id"}
        for record in records:
            if record.service_instance_id:
                identity.add("service.instance.id")
            if record.container_id:
                identity.add("container.id")
            if record.host_id:
                identity.add("host.id")
        return SourceProbeResult(
            source="TRACES",
            backend_kind="JAEGER_QUERY_API",
            status=SourceProbeStatus.AVAILABLE,
            window_start=self.window_start,
            window_end=self.window_end,
            probe_started_at=started,
            probe_ended_at=datetime.now(timezone.utc),
            attempt_count=attempt,
            backend_reachable=True,
            raw_response_count=len(self.raw_artifacts),
            parsed_record_count=len(records),
            target_record_count=len(records),
            service_catalog_count=len(catalog),
            target_service_present=True,
            identity_fields_present=tuple(sorted(identity)),
            raw_artifact_hashes=dict(self.raw_artifacts),
            evidence_refs=references,
            invalid_ref_count=0,
        )

    def probe(self) -> SourceProbeResult:
        last: SourceProbeResult | None = None
        deadline = time.monotonic() + self.readiness.maximum_readiness_seconds
        for attempt in range(1, self.readiness.maximum_probe_attempts + 1):
            started = datetime.now(timezone.utc)
            try:
                return self._attempt(attempt, started)
            except SourceProbeFailure as failure:
                last = _source_terminal(
                    source="TRACES",
                    backend_kind="JAEGER_QUERY_API",
                    status=failure.status,
                    window_start=self.window_start,
                    window_end=self.window_end,
                    probe_started_at=started,
                    attempt_count=attempt,
                    backend_reachable=failure.backend_reachable,
                    raw_artifacts=self.raw_artifacts,
                    safe_reason_code=failure.safe_reason_code,
                    service_catalog_count=self.service_catalog_count,
                    target_service_present=self.target_service_present,
                )
            except (HTTPError, URLError, TimeoutError, OSError):
                last = _source_terminal(
                    source="TRACES",
                    backend_kind="JAEGER_QUERY_API",
                    status=SourceProbeStatus.HTTP_FAILED,
                    window_start=self.window_start,
                    window_end=self.window_end,
                    probe_started_at=started,
                    attempt_count=attempt,
                    backend_reachable=False,
                    raw_artifacts=self.raw_artifacts,
                    safe_reason_code="JAEGER_UNREACHABLE",
                )
            except Exception:
                last = _source_terminal(
                    source="TRACES",
                    backend_kind="JAEGER_QUERY_API",
                    status=SourceProbeStatus.SCHEMA_MISMATCH,
                    window_start=self.window_start,
                    window_end=self.window_end,
                    probe_started_at=started,
                    attempt_count=attempt,
                    backend_reachable=True,
                    raw_artifacts=self.raw_artifacts,
                    safe_reason_code="TRACE_SCHEMA_INVALID",
                )
            remaining = deadline - time.monotonic()
            if attempt < self.readiness.maximum_probe_attempts and remaining > 0:
                self.sleep(min(self.readiness.poll_interval_seconds, remaining))
            if time.monotonic() >= deadline:
                break
        assert last is not None
        return last.model_copy(update={"status": SourceProbeStatus.INGESTION_TIMEOUT})


class InstrumentationCleanup(V2Model):
    baseline_restored: bool
    owned_containers: int = Field(ge=0)
    owned_networks: int = Field(ge=0)
    owned_volumes: int = Field(ge=0)
    non_owned_resources_changed: bool
    verdict: Literal["CLEAN", "BLOCKED"]

    @model_validator(mode="after")
    def require_clean_truth(self) -> "InstrumentationCleanup":
        expected = (
            self.baseline_restored
            and self.owned_containers == 0
            and self.owned_networks == 0
            and self.owned_volumes == 0
            and not self.non_owned_resources_changed
        )
        if (self.verdict == "CLEAN") != expected:
            raise ValueError("cleanup verdict differs from cleanup truth")
        return self


class TelemetryInstrumentationReport(V2Model):
    version: InstrumentationVersion = "live-telemetry-instrumentation-v2"
    environment_id: str
    sandbox_binding_sha256: str = Field(pattern=SHA256_PATTERN)
    pinned_upstream_version: Literal["3.0.0"] = "3.0.0"
    pinned_upstream_commit: Literal[
        "1755859a9de82c2e5e225be68abc401a5ebf2b4f"
    ] = "1755859a9de82c2e5e225be68abc401a5ebf2b4f"
    resolved_compose_sha256: str = Field(pattern=SHA256_PATTERN)
    target_service: str
    window_start: AwareDatetime
    window_end: AwareDatetime
    capture_window_seconds: int = Field(ge=1)
    ingestion_grace_seconds: int = Field(ge=0)
    metrics: SourceProbeResult
    logs: SourceProbeResult
    traces: SourceProbeResult
    all_sources_available: bool
    all_target_sources_nonempty: bool
    all_refs_resolve: bool
    canonical_preflight: bool
    fault_injections: Literal[0] = 0
    provider_calls: Literal[0] = 0
    model_calls: Literal[0] = 0
    approval_records: Literal[0] = 0
    plans_admitted: Literal[0] = 0
    forward_mutations: Literal[0] = 0
    rollback_mutations: Literal[0] = 0
    cleanup: InstrumentationCleanup
    final_verdict: Literal[
        "DEVELOPMENT_PROBE_AVAILABLE",
        "BLOCKED_SOURCE_CONTRACT_UNRESOLVED",
        "LIVE_TELEMETRY_INSTRUMENTATION_V2_READY_FOR_E2E",
        "LIVE_TELEMETRY_INSTRUMENTATION_V3_READY_FOR_E2E",
        "BLOCKED_CANONICAL_INSTRUMENTATION_PREFLIGHT",
    ]
    semantic_sha256: str = Field(pattern=SHA256_PATTERN)

    @model_validator(mode="after")
    def require_recomputed_truth(self) -> "TelemetryInstrumentationReport":
        sources = (self.metrics, self.logs, self.traces)
        available = all(item.status is SourceProbeStatus.AVAILABLE for item in sources)
        nonempty = all(item.target_record_count > 0 for item in sources)
        if self.all_sources_available != available:
            raise ValueError("aggregate source availability differs from source truth")
        if self.all_target_sources_nonempty != nonempty:
            raise ValueError("aggregate target nonempty gate differs from source truth")
        success = available and nonempty and self.all_refs_resolve and self.cleanup.verdict == "CLEAN"
        expected_verdict = (
            _success_verdict(self.version)
            if self.canonical_preflight and success
            else "BLOCKED_CANONICAL_INSTRUMENTATION_PREFLIGHT"
            if self.canonical_preflight
            else "DEVELOPMENT_PROBE_AVAILABLE"
            if success
            else "BLOCKED_SOURCE_CONTRACT_UNRESOLVED"
        )
        if self.final_verdict != expected_verdict:
            raise ValueError("instrumentation verdict differs from recomputed truth")
        payload = self.model_dump(mode="json")
        payload.pop("semantic_sha256", None)
        if hashlib.sha256(canonical_json_bytes(payload)).hexdigest() != self.semantic_sha256:
            raise ValueError("instrumentation semantic digest differs")
        return self


def build_instrumentation_report(
    *,
    version: InstrumentationVersion = "live-telemetry-instrumentation-v2",
    environment_id: str,
    sandbox_binding_sha256: str,
    resolved_compose_sha256: str,
    target_service: str,
    window_start: datetime,
    window_end: datetime,
    ingestion_grace_seconds: int,
    metrics: SourceProbeResult,
    logs: SourceProbeResult,
    traces: SourceProbeResult,
    all_refs_resolve: bool,
    canonical_preflight: bool,
    cleanup: Mapping[str, object],
) -> TelemetryInstrumentationReport:
    source_results = (metrics, logs, traces)
    available = all(item.status is SourceProbeStatus.AVAILABLE for item in source_results)
    nonempty = all(item.target_record_count > 0 for item in source_results)
    cleanup_model = InstrumentationCleanup.model_validate(cleanup)
    success = available and nonempty and all_refs_resolve and cleanup_model.verdict == "CLEAN"
    verdict = (
        _success_verdict(version)
        if canonical_preflight and success
        else "BLOCKED_CANONICAL_INSTRUMENTATION_PREFLIGHT"
        if canonical_preflight
        else "DEVELOPMENT_PROBE_AVAILABLE"
        if success
        else "BLOCKED_SOURCE_CONTRACT_UNRESOLVED"
    )
    payload: dict[str, object] = {
        "version": version,
        "environment_id": environment_id,
        "sandbox_binding_sha256": sandbox_binding_sha256,
        "pinned_upstream_version": "3.0.0",
        "pinned_upstream_commit": "1755859a9de82c2e5e225be68abc401a5ebf2b4f",
        "resolved_compose_sha256": resolved_compose_sha256,
        "target_service": target_service,
        "window_start": window_start,
        "window_end": window_end,
        "capture_window_seconds": int((window_end - window_start).total_seconds()),
        "ingestion_grace_seconds": ingestion_grace_seconds,
        "metrics": metrics,
        "logs": logs,
        "traces": traces,
        "all_sources_available": available,
        "all_target_sources_nonempty": nonempty,
        "all_refs_resolve": all_refs_resolve,
        "canonical_preflight": canonical_preflight,
        "fault_injections": 0,
        "provider_calls": 0,
        "model_calls": 0,
        "approval_records": 0,
        "plans_admitted": 0,
        "forward_mutations": 0,
        "rollback_mutations": 0,
        "cleanup": cleanup_model,
        "final_verdict": verdict,
    }
    candidate = TelemetryInstrumentationReport.model_construct(
        **payload,  # type: ignore[arg-type]
        semantic_sha256="0" * 64,
    )
    serialized = candidate.model_dump(mode="json")
    serialized.pop("semantic_sha256", None)
    payload["semantic_sha256"] = hashlib.sha256(
        canonical_json_bytes(serialized)
    ).hexdigest()
    return TelemetryInstrumentationReport.model_validate(payload)


def public_projection(
    report: TelemetryInstrumentationReport,
    *,
    claim_boundary: Sequence[str],
) -> dict[str, object]:
    def source(item: SourceProbeResult) -> dict[str, object]:
        return {
            "backend_kind": item.backend_kind,
            "status": item.status.value,
            "backend_reachable": item.backend_reachable,
            "raw_response_count": item.raw_response_count,
            "parsed_record_count": item.parsed_record_count,
            "target_record_count": item.target_record_count,
            "service_catalog_count": item.service_catalog_count,
            "target_service_present": item.target_service_present,
            "selected_time_field": item.selected_time_field,
            "selected_service_field": item.selected_service_field,
            "attempt_count": item.attempt_count,
            "evidence_ref_count": len(item.evidence_refs),
            "invalid_ref_count": item.invalid_ref_count,
            "safe_reason_code": item.safe_reason_code,
            "series_count": item.series_count,
            "sample_count": item.sample_count,
            "finite_value_count": item.finite_value_count,
            "target_label_match_count": item.target_label_match_count,
        }

    return {
        "schema_version": f"{report.version}.public.v1",
        "version": report.version,
        "environment": {
            "upstream_version": report.pinned_upstream_version,
            "upstream_commit": report.pinned_upstream_commit,
            "platform": "linux/arm64",
        },
        "target_service": report.target_service,
        "capture_window_seconds": report.capture_window_seconds,
        "ingestion_grace_seconds": report.ingestion_grace_seconds,
        "sources": {
            "METRICS": source(report.metrics),
            "LOGS": source(report.logs),
            "TRACES": source(report.traces),
        },
        "all_sources_available": report.all_sources_available,
        "all_target_sources_nonempty": report.all_target_sources_nonempty,
        "all_refs_resolve": report.all_refs_resolve,
        "canonical_preflight": report.canonical_preflight,
        "safety": {
            "fault_injections": report.fault_injections,
            "provider_calls": report.provider_calls,
            "model_calls": report.model_calls,
            "approval_records": report.approval_records,
            "plans_admitted": report.plans_admitted,
            "forward_mutations": report.forward_mutations,
            "rollback_mutations": report.rollback_mutations,
        },
        "cleanup": report.cleanup.model_dump(mode="json"),
        "claim_boundary": list(claim_boundary),
        "verdict": report.final_verdict,
        "semantic_sha256": report.semantic_sha256,
    }


_FORBIDDEN_PUBLIC_KEYS = {
    "endpoint",
    "url",
    "path",
    "private_path",
    "sandbox_id",
    "docker_daemon_id",
    "trace_id",
    "span_id",
    "container_id",
    "host_id",
    "raw",
    "raw_artifact_hashes",
    "evidence_refs",
}


def scan_public_payload(value: object) -> tuple[str, ...]:
    findings: set[str] = set()

    def visit(item: object) -> None:
        if isinstance(item, Mapping):
            for key, child in item.items():
                if str(key).casefold() in _FORBIDDEN_PUBLIC_KEYS:
                    findings.add("FORBIDDEN_KEY")
                visit(child)
        elif isinstance(item, (list, tuple)):
            for child in item:
                visit(child)
        elif isinstance(item, str):
            lowered = item.casefold()
            if "127.0.0.1" in lowered or "localhost:" in lowered or "unix://" in lowered:
                findings.add("LOCAL_ENDPOINT")
            if "/users/" in lowered or "/private/var/" in lowered or ".ecomsre/private" in lowered:
                findings.add("PRIVATE_PATH")
            if re.fullmatch(r"[0-9a-f]{16}|[0-9a-f]{32}", lowered) or re.fullmatch(
                r"[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}",
                lowered,
            ):
                findings.add("RUNTIME_ID")

    visit(value)
    return tuple(sorted(findings))


def verify_public_result(value: Mapping[str, object]) -> None:
    if scan_public_payload(value):
        raise ValueError("public instrumentation result contains private data")
    sources = value.get("sources")
    if not isinstance(sources, Mapping) or set(sources) != {"METRICS", "LOGS", "TRACES"}:
        raise ValueError("public instrumentation sources are incomplete")
    source_gate = True
    for source_name in ("METRICS", "LOGS", "TRACES"):
        item = sources.get(source_name)
        if not isinstance(item, Mapping):
            source_gate = False
            continue
        target_count = item.get("target_record_count")
        invalid_count = item.get("invalid_ref_count")
        source_gate = source_gate and (
            item.get("status") == "AVAILABLE"
            and type(target_count) is int
            and target_count > 0
            and type(invalid_count) is int
            and invalid_count == 0
        )
    safety = value.get("safety")
    cleanup = value.get("cleanup")
    safety_gate = isinstance(safety, Mapping) and all(
        type(safety.get(key)) is int and safety.get(key) == 0
        for key in (
            "fault_injections",
            "provider_calls",
            "model_calls",
            "approval_records",
            "plans_admitted",
            "forward_mutations",
            "rollback_mutations",
        )
    )
    cleanup_gate = isinstance(cleanup, Mapping) and (
        cleanup.get("baseline_restored") is True
        and type(cleanup.get("owned_containers")) is int
        and cleanup.get("owned_containers") == 0
        and type(cleanup.get("owned_networks")) is int
        and cleanup.get("owned_networks") == 0
        and type(cleanup.get("owned_volumes")) is int
        and cleanup.get("owned_volumes") == 0
        and cleanup.get("non_owned_resources_changed") is False
        and cleanup.get("verdict") == "CLEAN"
    )
    version = value.get("version")
    expected_verdict = (
        _success_verdict(cast(InstrumentationVersion, version))
        if version in {V2_LIFECYCLE.version, V3_LIFECYCLE.version}
        else None
    )
    aggregate_gate = (
        expected_verdict is not None
        and value.get("schema_version") == f"{version}.public.v1"
        and value.get("all_sources_available") is True
        and value.get("all_target_sources_nonempty") is True
        and value.get("all_refs_resolve") is True
        and value.get("canonical_preflight") is True
        and value.get("verdict") == expected_verdict
    )
    if not (source_gate and safety_gate and cleanup_gate and aggregate_gate):
        raise ValueError("public instrumentation truth gate failed")


@dataclass(frozen=True, slots=True)
class InstrumentationPrivateRoots:
    root: Path
    control: Path
    runtime: Path
    telemetry: Path
    reports: Path
    development_probes: Path
    canonical_preflight: Path

    @classmethod
    def from_root(cls, root: Path) -> "InstrumentationPrivateRoots":
        resolved = root.expanduser().resolve()
        return cls(
            root=resolved,
            control=resolved / "control",
            runtime=resolved / "runtime",
            telemetry=resolved / "telemetry",
            reports=resolved / "reports",
            development_probes=resolved / "development-probes",
            canonical_preflight=resolved / "canonical-preflight",
        )

    def prepare(self) -> None:
        for path in (
            self.root,
            self.control,
            self.runtime,
            self.telemetry,
            self.reports,
            self.development_probes,
            self.canonical_preflight,
        ):
            ensure_private_directory(path)


_PRIVATE_ROOT_BINDING = "lifecycle-binding.json"


def _private_root_binding(lifecycle: InstrumentationLifecycle) -> dict[str, str]:
    return {
        "schema_version": "live-telemetry.private-root-binding.v1",
        "version": lifecycle.version,
        "branch": lifecycle.branch,
    }


def _verify_private_root_binding(
    root: Path, lifecycle: InstrumentationLifecycle
) -> None:
    binding_path = root / _PRIVATE_ROOT_BINDING
    if binding_path.is_symlink() or not binding_path.is_file():
        raise RuntimeError("private root lifecycle binding is unavailable")
    try:
        payload = json.loads(binding_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError("private root lifecycle binding is invalid") from error
    if payload != _private_root_binding(lifecycle):
        raise RuntimeError("private root belongs to a different lifecycle")


def resolve_private_root(
    explicit: Path | None,
    *,
    repository_root: Path,
    lifecycle: InstrumentationLifecycle = V2_LIFECYCLE,
) -> InstrumentationPrivateRoots:
    configured = os.environ.get("ECOMSRE_PRIVATE_ROOT")
    selected = (
        explicit
        if explicit is not None
        else Path(configured).expanduser()
        if configured
        else Path.home() / ".ecomsre/private" / lifecycle.private_root_name
    ).resolve()
    repository = repository_root.resolve()
    if selected == Path("/") or selected == Path.home().resolve():
        raise ValueError("private root target is too broad")
    if selected == repository or selected.is_relative_to(repository):
        raise ValueError("private telemetry root may not be inside the repository")
    for other in (V2_LIFECYCLE, V3_LIFECYCLE):
        if (
            other.version != lifecycle.version
            and selected.name == other.private_root_name
        ):
            raise ValueError("private root belongs to a different lifecycle")
    binding_path = selected / _PRIVATE_ROOT_BINDING
    if selected.exists():
        if selected.is_symlink() or not selected.is_dir():
            raise ValueError("private telemetry root is not a regular directory")
        if binding_path.exists() or binding_path.is_symlink():
            _verify_private_root_binding(selected, lifecycle)
        elif any(selected.iterdir()):
            raise ValueError("private telemetry root is nonempty and unbound")
    roots = InstrumentationPrivateRoots.from_root(selected)
    roots.prepare()
    if binding_path.exists():
        _verify_private_root_binding(selected, lifecycle)
    else:
        write_private_json(
            binding_path,
            _private_root_binding(lifecycle),
            create_once=True,
        )
    verify_private_tree_permissions(roots.root)
    return roots


def _git(repository_root: Path, *arguments: str) -> str:
    result = subprocess.run(
        ("git", *arguments),
        cwd=repository_root,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError("Git state verification failed")
    return result.stdout.strip()


def _baseline_controller(
    repository_root: Path,
    roots: InstrumentationPrivateRoots,
    bundle: ConfigBundle,
    endpoints: LocalEndpoints,
) -> SandboxFaultController:
    upstream = json.loads(
        (
            repository_root
            / "third_party/opentelemetry-demo/src/flagd/demo.flagd.json"
        ).read_text(encoding="utf-8")
    )
    if not isinstance(upstream, Mapping):
        raise RuntimeError("upstream flag document is malformed")
    baseline, fault = build_flag_documents(upstream, bundle)
    flag_directory = roots.runtime / "flagd"
    ensure_private_directory(flag_directory)
    flag_file = flag_directory / "demo.flagd.json"
    write_private_json(flag_file, baseline, create_once=True)
    return SandboxFaultController(
        endpoints=endpoints,
        bundle=bundle,
        flag_file=flag_file,
        baseline_document=baseline,
        fault_document=fault,
    )


def _source_placeholders(
    *, window_start: datetime, window_end: datetime, reason: str
) -> tuple[SourceProbeResult, SourceProbeResult, SourceProbeResult]:
    now = datetime.now(timezone.utc)
    output: list[SourceProbeResult] = []
    bindings: tuple[tuple[TelemetrySource, BackendKind], ...] = (
        ("METRICS", "PROMETHEUS_HTTP_API"),
        ("LOGS", "OPENSEARCH_HTTP_API"),
        ("TRACES", "JAEGER_QUERY_API"),
    )
    for source_name, backend in bindings:
        output.append(
            SourceProbeResult(
                source=source_name,
                backend_kind=backend,
                status=SourceProbeStatus.HTTP_FAILED,
                window_start=window_start,
                window_end=window_end,
                probe_started_at=now,
                probe_ended_at=now,
                attempt_count=1,
                backend_reachable=False,
                raw_response_count=0,
                parsed_record_count=0,
                target_record_count=0,
                service_catalog_count=0,
                target_service_present=False,
                safe_reason_code=reason,
            )
        )
    return output[0], output[1], output[2]


def _revalidate_refs(
    results: tuple[SourceProbeResult, SourceProbeResult, SourceProbeResult],
    *,
    resolver: EvidenceResolver,
    store_root: Path,
) -> tuple[tuple[SourceProbeResult, SourceProbeResult, SourceProbeResult], bool]:
    seen: set[str] = set()
    validated: list[SourceProbeResult] = []
    all_valid = True
    for result in results:
        invalid = 0
        for reference in result.evidence_refs:
            if reference in seen:
                invalid += 1
                continue
            seen.add(reference)
            try:
                metadata = resolver.resolve(reference)
                if metadata.source != result.source:
                    raise ValueError("resolved evidence source mismatch")
                relative = Path(metadata.private_artifact_relative_key)
                if relative.is_absolute() or ".." in relative.parts:
                    raise ValueError("resolved evidence artifact path is invalid")
                artifact = (store_root / relative).resolve()
                if not artifact.is_relative_to(store_root.resolve()):
                    raise ValueError("resolved evidence artifact escaped its root")
                if artifact.is_symlink() or not artifact.is_file():
                    raise ValueError("resolved evidence artifact is unavailable")
                if hashlib.sha256(artifact.read_bytes()).hexdigest() != metadata.raw_artifact_sha256:
                    raise ValueError("resolved evidence artifact hash differs")
            except (OSError, ValueError):
                invalid += 1
        if invalid:
            all_valid = False
            payload = result.model_dump(mode="json")
            payload.update(
                {
                    "status": SourceProbeStatus.INVALID_RECORD.value,
                    "invalid_ref_count": invalid,
                    "safe_reason_code": "INVALID_EVIDENCE_REF",
                }
            )
            result = SourceProbeResult.model_validate(payload)
        validated.append(result)
    return (validated[0], validated[1], validated[2]), all_valid


def _safe_summary(terminal: Mapping[str, object]) -> dict[str, object]:
    raw_sources = terminal.get("sources")
    sources: dict[str, object] = {}
    if isinstance(raw_sources, Mapping):
        for name in ("METRICS", "LOGS", "TRACES"):
            raw = raw_sources.get(name)
            if isinstance(raw, Mapping):
                sources[name] = {
                    "status": raw.get("status"),
                    "safe_reason_code": raw.get("safe_reason_code"),
                    "target_record_count": raw.get("target_record_count", 0),
                    "attempt_count": raw.get("attempt_count", 0),
                    "invalid_ref_count": raw.get("invalid_ref_count", 0),
                }
    cleanup_value = terminal.get("cleanup")
    cleanup_verdict = (
        cleanup_value.get("verdict")
        if isinstance(cleanup_value, Mapping)
        else "BLOCKED"
    )
    return {
        "verdict": terminal.get("verdict"),
        "mode": terminal.get("mode"),
        "development_probe_number": terminal.get("development_probe_number"),
        "sandbox_startup_attempted": terminal.get("sandbox_startup_attempted"),
        "sources": sources,
        "cleanup_verdict": cleanup_verdict,
    }


def _classify_blocker(stage: str, *, canonical: bool) -> str:
    if stage == "VERIFY_DOCKER":
        return "BLOCKED_DOCKER_CONTEXT_NOT_LOCAL"
    if stage == "VERIFY_UPSTREAM":
        return "BLOCKED_PINNED_SOURCE_DRIFT"
    if stage in {"START_SANDBOX", "CLEANUP"}:
        return "BLOCKED_OWNERSHIP_OR_CLEANUP"
    return (
        "BLOCKED_CANONICAL_INSTRUMENTATION_PREFLIGHT"
        if canonical
        else "BLOCKED_SOURCE_CONTRACT_UNRESOLVED"
    )


def _run_live_instrumentation(
    *,
    repository_root: Path,
    lifecycle: InstrumentationLifecycle,
    roots: InstrumentationPrivateRoots,
    output_root: Path,
    terminal_path: Path,
    canonical: bool,
    development_probe_number: int | None,
) -> dict[str, object]:
    repository_root = repository_root.resolve()
    v1_bundle = load_bundle(repository_root / V1_CONFIG_RELATIVE)
    config = load_instrumentation_config(repository_root / lifecycle.config_relative)
    if config.environment.version != lifecycle.version:
        raise ValueError("instrumentation config version differs from lifecycle")
    environment = SandboxEnvironment(
        repository_root=repository_root,
        bundle=v1_bundle,
        flagd_directory=roots.runtime / "flagd",
    )
    now = datetime.now(timezone.utc)
    placeholder_end = now + timedelta(seconds=1)
    results = _source_placeholders(
        window_start=now,
        window_end=placeholder_end,
        reason="SANDBOX_NOT_AVAILABLE",
    )
    cleanup_payload: Mapping[str, object] | None = None
    resolved = None
    all_refs_resolve = False
    startup_attempted = False
    baseline_restored = False
    stage = "VERIFY_DOCKER"
    failure: BaseException | None = None
    docker: Mapping[str, str] | None = None
    health: Mapping[str, bool] | None = None
    try:
        docker = environment.verify_local_docker()
        stage = "VERIFY_UPSTREAM"
        environment.verify_upstream()
        stage = "RESOLVE_COMPOSE"
        resolved, raw_compose = environment.resolve()
        ensure_private_directory(output_root / "control")
        write_private_json(
            output_root / "control/resolved-compose.json",
            raw_compose,
            create_once=True,
        )
        environment.verify_cached_images(resolved, roots.control)
        controller = _baseline_controller(
            repository_root, roots, v1_bundle, resolved.endpoints
        )
        try:
            stage = "START_SANDBOX"
            startup_attempted = True
            environment.start()
            health = environment.wait_healthy()
            stage = "STABILIZE"
            stabilization = (
                config.readiness.canonical_stabilization_seconds
                if canonical
                else config.readiness.development_stabilization_seconds
            )
            time.sleep(stabilization)
            stage = "VERIFY_BASELINE"
            current = controller.read_current()
            if current.document_sha256 != v1_bundle.scenario.baseline_document_sha256:
                raise RuntimeError("sandbox configuration is not the frozen baseline")
            baseline_restored = True
            stage = "CAPTURE_WINDOW"
            window_start = datetime.now(timezone.utc)
            time.sleep(config.readiness.capture_window_seconds)
            window_end = datetime.now(timezone.utc)
            time.sleep(config.readiness.ingestion_grace_seconds)
            stage = "SOURCE_PROBES"
            store = PrivateArtifactStore(output_root / "telemetry")
            probes = cast(
                tuple[SourceProbe, SourceProbe, SourceProbe],
                (
                MetricsSourceProbe(
                    endpoint=resolved.endpoints.prometheus,
                    target_service=config.environment.target_service,
                    config=config.sources.prometheus,
                    readiness=config.readiness,
                    store=store,
                    window_start=window_start,
                    window_end=window_end,
                ),
                LogsSourceProbe(
                    endpoint=resolved.endpoints.opensearch,
                    target_service=config.environment.target_service,
                    config=config.sources.opensearch,
                    readiness=config.readiness,
                    store=store,
                    window_start=window_start,
                    window_end=window_end,
                ),
                TracesSourceProbe(
                    endpoint=resolved.endpoints.jaeger,
                    target_service=config.environment.target_service,
                    config=config.sources.jaeger,
                    readiness=config.readiness,
                    store=store,
                    window_start=window_start,
                    window_end=window_end,
                ),
                ),
            )
            results = terminalize_source_probes(
                probes, window_start=window_start, window_end=window_end
            )
            resolver_path = store.seal()
            resolver = EvidenceResolver.from_file(resolver_path)
            results, all_refs_resolve = _revalidate_refs(
                results, resolver=resolver, store_root=store.root
            )
        finally:
            if environment._baseline_snapshot is not None:
                stage = "CLEANUP"
                cleanup = environment.cleanup(baseline_restored=baseline_restored)
                cleanup_payload = cleanup.model_dump(mode="json")
    except BaseException as error:
        failure = error
        diagnostic = {
            "schema_version": f"{lifecycle.version}.private-diagnostic.v1",
            "stage": stage,
            "exception_type": type(error).__name__,
            "exception_chain": traceback.format_exc(),
        }
        try:
            write_private_json(
                output_root / "private-diagnostic.json", diagnostic, create_once=True
            )
        except Exception:
            pass

    report: TelemetryInstrumentationReport | None = None
    if resolved is not None and cleanup_payload is not None:
        try:
            if failure is None:
                stage = "VERIFY_PRIVATE_PERMISSIONS"
                verify_private_tree_permissions(roots.root)
                stage = "BUILD_REPORT"
            report = build_instrumentation_report(
                version=lifecycle.version,
                environment_id=config.environment.environment_id,
                sandbox_binding_sha256=hashlib.sha256(
                    v1_bundle.environment.sandbox_id.encode("utf-8")
                ).hexdigest(),
                resolved_compose_sha256=resolved.compose_sha256,
                target_service=config.environment.target_service,
                window_start=results[0].window_start,
                window_end=results[0].window_end,
                ingestion_grace_seconds=config.readiness.ingestion_grace_seconds,
                metrics=results[0],
                logs=results[1],
                traces=results[2],
                all_refs_resolve=all_refs_resolve,
                canonical_preflight=canonical,
                cleanup=cleanup_payload,
            )
        except Exception as error:
            if failure is None:
                failure = error
    if failure is not None:
        verdict = _classify_blocker(stage, canonical=canonical)
    elif report is not None:
        verdict = report.final_verdict
    else:
        verdict = _classify_blocker(stage, canonical=canonical)
    terminal: dict[str, object] = {
        "schema_version": f"{lifecycle.version}.terminal.v1",
        "mode": "CANONICAL_PREFLIGHT" if canonical else "DEVELOPMENT_PROBE",
        "development_probe_number": development_probe_number,
        "implementation_head": _git(repository_root, "rev-parse", "HEAD"),
        "sandbox_startup_attempted": startup_attempted,
        "docker": dict(docker) if docker is not None else None,
        "services_healthy": sum(1 for value in (health or {}).values() if value),
        "sources": {
            "METRICS": results[0].model_dump(mode="json"),
            "LOGS": results[1].model_dump(mode="json"),
            "TRACES": results[2].model_dump(mode="json"),
        },
        "all_refs_resolve": all_refs_resolve,
        "cleanup": dict(cleanup_payload) if cleanup_payload is not None else None,
        "report": report.model_dump(mode="json") if report is not None else None,
        "fault_injections": 0,
        "provider_calls": 0,
        "model_calls": 0,
        "approval_records": 0,
        "plans_admitted": 0,
        "forward_mutations": 0,
        "rollback_mutations": 0,
        "failure_stage": stage if failure is not None else None,
        "failure_type": type(failure).__name__ if failure is not None else None,
        "verdict": verdict,
    }
    write_private_json(terminal_path, terminal, create_once=True)
    return terminal


def _probe_directories(roots: InstrumentationPrivateRoots) -> tuple[Path, ...]:
    return tuple(
        sorted(
            path
            for path in roots.development_probes.iterdir()
            if path.is_dir()
            and not path.is_symlink()
            and re.fullmatch(r"development-probe-[0-9]{2}", path.name)
        )
    )


def run_development_probe(
    repository_root: Path,
    private_root: Path | None = None,
    *,
    lifecycle: InstrumentationLifecycle = V2_LIFECYCLE,
) -> dict[str, object]:
    repository_root = repository_root.resolve()
    roots = resolve_private_root(
        private_root, repository_root=repository_root, lifecycle=lifecycle
    )
    existing = _probe_directories(roots)
    next_number = max((int(path.name.rsplit("-", 1)[1]) for path in existing), default=0) + 1
    if next_number > 4:
        raise RuntimeError("development sandbox startup allowance is exhausted")
    output_root = roots.development_probes / f"development-probe-{next_number:02d}"
    output_root.mkdir(mode=0o700, parents=False, exist_ok=False)
    output_root.chmod(0o700)
    terminal = _run_live_instrumentation(
        repository_root=repository_root,
        lifecycle=lifecycle,
        roots=roots,
        output_root=output_root,
        terminal_path=output_root / f"development-probe-{next_number:02d}.json",
        canonical=False,
        development_probe_number=next_number,
    )
    return _safe_summary(terminal)


def _latest_development_terminal(
    roots: InstrumentationPrivateRoots,
    lifecycle: InstrumentationLifecycle = V2_LIFECYCLE,
) -> Mapping[str, object]:
    directories = _probe_directories(roots)
    if not directories:
        raise RuntimeError("canonical preflight lacks a development probe")
    latest = directories[-1]
    terminal_path = latest / f"{latest.name}.json"
    if terminal_path.is_symlink() or not terminal_path.is_file():
        raise RuntimeError("latest development probe lacks a sealed terminal")
    payload = json.loads(terminal_path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise RuntimeError("latest development terminal is malformed")
    if (
        payload.get("schema_version") != f"{lifecycle.version}.terminal.v1"
        or payload.get("mode") != "DEVELOPMENT_PROBE"
    ):
        raise RuntimeError("latest development terminal lifecycle is invalid")
    return payload


def _verify_canonical_admission(
    repository_root: Path,
    roots: InstrumentationPrivateRoots,
    *,
    lifecycle: InstrumentationLifecycle = V2_LIFECYCLE,
    implementation_ci_passed: bool,
) -> str:
    _verify_private_root_binding(roots.root, lifecycle)
    latest = _latest_development_terminal(roots, lifecycle)
    verify_private_tree_permissions(roots.root)
    if latest.get("verdict") != "DEVELOPMENT_PROBE_AVAILABLE":
        raise RuntimeError("latest development probe is not 3/3 AVAILABLE")
    sources = latest.get("sources")
    if not isinstance(sources, Mapping):
        raise RuntimeError("latest development probe is not 3/3 AVAILABLE")
    for source_name in ("METRICS", "LOGS", "TRACES"):
        source = sources.get(source_name)
        if not isinstance(source, Mapping):
            raise RuntimeError("latest development probe is not 3/3 AVAILABLE")
        target_count = source.get("target_record_count")
        if (
            source.get("status") != SourceProbeStatus.AVAILABLE.value
            or type(target_count) is not int
            or target_count <= 0
            or source.get("invalid_ref_count") != 0
        ):
            raise RuntimeError("latest development probe is not 3/3 AVAILABLE")
    if latest.get("all_refs_resolve") is not True:
        raise RuntimeError("latest development Evidence refs are not valid")
    if latest.get("sandbox_startup_attempted") is not True:
        raise RuntimeError("latest development probe did not start the sandbox")
    cleanup = latest.get("cleanup")
    try:
        cleanup_model = InstrumentationCleanup.model_validate(cleanup)
    except ValueError as error:
        raise RuntimeError("latest development cleanup is not CLEAN") from error
    if cleanup_model.verdict != "CLEAN":
        raise RuntimeError("latest development cleanup is not CLEAN")
    if _git(repository_root, "status", "--porcelain=v1"):
        raise RuntimeError("implementation worktree is not clean")
    branch = _git(repository_root, "branch", "--show-current")
    if branch != lifecycle.branch:
        raise RuntimeError("canonical preflight is on the wrong branch")
    head = _git(repository_root, "rev-parse", "HEAD")
    remote_head = _git(
        repository_root,
        "rev-parse",
        f"origin/{lifecycle.branch}",
    )
    if head != remote_head:
        raise RuntimeError("implementation head is not pushed exactly")
    if not implementation_ci_passed:
        raise RuntimeError("exact implementation-head offline CI is not verified")
    result_path = roots.canonical_preflight / "canonical-preflight.json"
    admission_path = roots.canonical_preflight / "admission.json"
    if result_path.exists() or admission_path.exists():
        raise RuntimeError("canonical preflight is create-once and already consumed")
    write_private_json(
        admission_path,
        {
            "schema_version": f"{lifecycle.version}.canonical-admission.v1",
            "implementation_head": head,
            "latest_development_probe": latest.get("development_probe_number"),
            "latest_development_verdict": latest.get("verdict"),
            "latest_development_cleanup": cleanup_model.verdict,
            "implementation_ci_passed": True,
        },
        create_once=True,
    )
    return head


def _write_new_public_file(path: Path, payload: bytes) -> None:
    path.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        raise


def _public_markdown(value: Mapping[str, object]) -> str:
    sources = value["sources"]
    assert isinstance(sources, Mapping)
    lines = [
        f"# {str(value['version']).replace('-', ' ').title()} Result",
        "",
        f"**Verdict:** `{value['verdict']}`",
        "",
        "This result proves typed Metrics, Logs, and Traces instrumentation in the pinned local no-fault sandbox only.",
        "",
        "| Source | Backend | Status | Target records | Attempts | Invalid refs |",
        "|---|---|---:|---:|---:|---:|",
    ]
    for name in ("METRICS", "LOGS", "TRACES"):
        item = sources[name]
        assert isinstance(item, Mapping)
        lines.append(
            f"| {name} | {item['backend_kind']} | {item['status']} | "
            f"{item['target_record_count']} | {item['attempt_count']} | "
            f"{item['invalid_ref_count']} |"
        )
    logs = sources["LOGS"]
    assert isinstance(logs, Mapping)
    lines.extend(
        [
            "",
            "## Safe source bindings",
            "",
            f"- OpenSearch time field: `{logs['selected_time_field']}`",
            f"- OpenSearch service field: `{logs['selected_service_field']}`",
            f"- Capture window: `{value['capture_window_seconds']}s`",
            f"- Ingestion grace: `{value['ingestion_grace_seconds']}s`",
            "- Cleanup: `CLEAN` with owned containers/networks/volumes `0/0/0`.",
            "",
            "## Claim boundary",
            "",
        ]
    )
    claim = value.get("claim_boundary")
    if isinstance(claim, list):
        lines.extend(f"- `{item}`" for item in claim)
    lines.extend(
        [
            "",
            f"Semantic SHA-256: `{value['semantic_sha256']}`",
            "",
        ]
    )
    return "\n".join(lines)


def _public_human_brief(value: Mapping[str, object]) -> str:
    return "\n".join(
        (
            f"# {str(value['version']).replace('-', ' ').title()} — Human Brief",
            "",
            f"**当前标记：** `{value['verdict']}`",
            "",
            "一次无故障 canonical preflight 已在 pinned OpenTelemetry Demo 3.0.0 的本地 linux/arm64 Sandbox 中完成。Prometheus Metrics、OpenSearch Logs 与 Jaeger Traces 均通过独立 typed source gate，target service 记录非空，Evidence refs 经独立 resolver 复核，owned cleanup 为 CLEAN。",
            "",
            "本结果没有注入故障，没有调用 Provider 或模型，没有创建审批或计划，也没有执行 remediation/rollback mutation。它只证明下一阶段可消费的本地 telemetry instrumentation，不代表 live A0 质量、生产自治或外部 benchmark 结果。",
            "",
        )
    )


def _write_public_outputs(
    repository_root: Path,
    config: InstrumentationConfig,
    report: TelemetryInstrumentationReport,
) -> tuple[str, str, str]:
    public = public_projection(report, claim_boundary=config.reporting.claim_boundary)
    public["validation"] = {
        "focused_tests": "PASS",
        "implementation_head_offline_ci": "PASS",
    }
    verify_public_result(public)
    findings = scan_public_payload(public)
    if findings:
        raise ValueError("public instrumentation leakage scan failed")
    paths = (
        repository_root / config.reporting.public_result_json,
        repository_root / config.reporting.public_result_markdown,
        repository_root / config.reporting.public_human_brief,
    )
    for path in paths:
        resolved = path.resolve()
        if not resolved.is_relative_to(repository_root.resolve() / "docs/results"):
            raise ValueError("public instrumentation output escaped docs/results")
    _write_new_public_file(paths[0], canonical_json_bytes(public))
    _write_new_public_file(paths[1], _public_markdown(public).encode("utf-8"))
    _write_new_public_file(paths[2], _public_human_brief(public).encode("utf-8"))
    return tuple(path.relative_to(repository_root).as_posix() for path in paths)  # type: ignore[return-value]


def run_canonical_preflight(
    repository_root: Path,
    private_root: Path | None = None,
    *,
    implementation_ci_passed: bool,
    lifecycle: InstrumentationLifecycle = V2_LIFECYCLE,
) -> dict[str, object]:
    repository_root = repository_root.resolve()
    roots = resolve_private_root(
        private_root, repository_root=repository_root, lifecycle=lifecycle
    )
    implementation_head = _verify_canonical_admission(
        repository_root,
        roots,
        lifecycle=lifecycle,
        implementation_ci_passed=implementation_ci_passed,
    )
    terminal = _run_live_instrumentation(
        repository_root=repository_root,
        lifecycle=lifecycle,
        roots=roots,
        output_root=roots.canonical_preflight,
        terminal_path=roots.canonical_preflight / "canonical-preflight.json",
        canonical=True,
        development_probe_number=None,
    )
    safe = _safe_summary(terminal)
    if terminal.get("verdict") == lifecycle.success_verdict:
        raw_report = terminal.get("report")
        if not isinstance(raw_report, Mapping):
            raise RuntimeError("successful canonical terminal lacks a typed report")
        report = TelemetryInstrumentationReport.model_validate(raw_report)
        config = load_instrumentation_config(repository_root / lifecycle.config_relative)
        outputs = _write_public_outputs(repository_root, config, report)
        safe["public_outputs"] = outputs
        safe["implementation_head"] = implementation_head
    return safe


class SourceProbe(Protocol):
    source: TelemetrySource
    backend_kind: BackendKind

    def probe(self) -> SourceProbeResult: ...


_UNEXPECTED_REASON: Mapping[TelemetrySource, str] = {
    "METRICS": "PROMETHEUS_QUERY_INVALID",
    "LOGS": "LOG_SCHEMA_INVALID",
    "TRACES": "TRACE_SCHEMA_INVALID",
}


def _failed_result(
    probe: SourceProbe,
    failure: SourceProbeFailure | None,
    *,
    window_start: datetime,
    window_end: datetime,
) -> SourceProbeResult:
    now = datetime.now(timezone.utc)
    return SourceProbeResult(
        source=probe.source,
        backend_kind=probe.backend_kind,
        status=(failure.status if failure else SourceProbeStatus.INVALID_RECORD),
        window_start=window_start,
        window_end=window_end,
        probe_started_at=now,
        probe_ended_at=now,
        attempt_count=(failure.attempt_count if failure else 1),
        backend_reachable=(failure.backend_reachable if failure else False),
        raw_response_count=0,
        parsed_record_count=0,
        target_record_count=0,
        service_catalog_count=0,
        target_service_present=False,
        invalid_ref_count=0,
        safe_reason_code=(
            failure.safe_reason_code if failure else _UNEXPECTED_REASON[probe.source]
        ),
    )


def terminalize_source_probes(
    probes: Sequence[SourceProbe],
    *,
    window_start: datetime,
    window_end: datetime,
) -> tuple[SourceProbeResult, SourceProbeResult, SourceProbeResult]:
    """Run all three probes even when one fails and retain typed terminals."""

    expected: tuple[TelemetrySource, ...] = ("METRICS", "LOGS", "TRACES")
    if tuple(probe.source for probe in probes) != expected:
        raise ValueError("source probes must be ordered METRICS, LOGS, TRACES")
    results: list[SourceProbeResult] = []
    for probe in probes:
        try:
            result = probe.probe()
            if result.source != probe.source or result.backend_kind != probe.backend_kind:
                raise ValueError("source probe returned a mismatched terminal")
        except SourceProbeFailure as failure:
            result = _failed_result(
                probe,
                failure,
                window_start=window_start,
                window_end=window_end,
            )
        except Exception:
            result = _failed_result(
                probe,
                None,
                window_start=window_start,
                window_end=window_end,
            )
        results.append(result)
    return results[0], results[1], results[2]


__all__ = [
    "BackendKind",
    "SourceProbeFailure",
    "SourceProbeResult",
    "SourceProbeStatus",
    "TelemetrySource",
    "terminalize_source_probes",
]
