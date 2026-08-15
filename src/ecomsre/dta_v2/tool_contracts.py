"""Strict contracts for the five DTA v2 read-only tools."""

from __future__ import annotations

from datetime import datetime, timedelta
from enum import Enum
import json
import re
import unicodedata
from typing import Annotated, Any, Literal, TypeAlias

from pydantic import (
    Field,
    StrictBool,
    StrictFloat,
    StrictInt,
    StringConstraints,
    field_validator,
    model_validator,
)

from ecomsre.dta_v2.contracts import (
    DtaModel,
    EvidenceSource,
    RunId,
    Sha256,
    semantic_sha256,
)
from ecomsre.dta_v2.unicode_confusables_v15 import (
    DIRECT_ASCII_HEX_CONFUSABLES,
)


LogicalService = Annotated[
    str,
    StringConstraints(
        strict=True,
        strip_whitespace=True,
        min_length=1,
        max_length=64,
        pattern=r"^[a-z][a-z0-9-]*$",
    ),
]
SafeOperation = Annotated[
    str,
    StringConstraints(
        strict=True,
        strip_whitespace=True,
        min_length=1,
        max_length=160,
    ),
]
SafeDiagnosticText = Annotated[
    str,
    StringConstraints(
        strict=True,
        strip_whitespace=True,
        min_length=1,
        max_length=500,
    ),
]


class ToolName(str, Enum):
    QUERY_METRICS = "query_metrics"
    SEARCH_LOGS = "search_logs"
    QUERY_TRACE_NEIGHBORHOOD = "query_trace_neighborhood"
    INSPECT_SERVICE_RUNTIME = "inspect_service_runtime"
    INSPECT_RESOURCE_USAGE = "inspect_resource_usage"


class ReadAuthorityMode(str, Enum):
    FAKE_REPLAY = "FAKE_REPLAY"
    OWNED_LOCAL = "OWNED_LOCAL"


class ReadAuthorityContext(DtaModel):
    schema_version: Literal["dta-v2.read-authority.v1"]
    mode: ReadAuthorityMode
    daemon_identity_sha256: Sha256 | None
    docker_context_sha256: Sha256 | None
    config_bundle_sha256: Sha256 | None
    resolved_sandbox_sha256: Sha256 | None
    resolved_endpoints_sha256: Sha256
    ownership_scope_sha256: Sha256
    authority_sha256: Sha256

    @model_validator(mode="after")
    def require_authority(self) -> ReadAuthorityContext:
        if (
            self.mode is ReadAuthorityMode.OWNED_LOCAL
            and any(
                item is None
                for item in (
                    self.daemon_identity_sha256,
                    self.docker_context_sha256,
                    self.config_bundle_sha256,
                    self.resolved_sandbox_sha256,
                )
            )
        ):
            raise ValueError("owned local read authority lacks lifecycle provenance")
        if (
            self.mode is ReadAuthorityMode.FAKE_REPLAY
            and any(
                item is not None
                for item in (
                    self.daemon_identity_sha256,
                    self.docker_context_sha256,
                    self.config_bundle_sha256,
                    self.resolved_sandbox_sha256,
                )
            )
        ):
            raise ValueError("fake read authority cannot claim lifecycle provenance")
        expected = semantic_sha256(
            self.model_dump(mode="json", exclude={"authority_sha256"})
        )
        if self.authority_sha256 != expected:
            raise ValueError("read authority digest does not bind authority")
        return self


def build_fake_read_authority() -> ReadAuthorityContext:
    payload: dict[str, object] = {
        "schema_version": "dta-v2.read-authority.v1",
        "mode": ReadAuthorityMode.FAKE_REPLAY,
        "daemon_identity_sha256": None,
        "docker_context_sha256": None,
        "config_bundle_sha256": None,
        "resolved_sandbox_sha256": None,
        "resolved_endpoints_sha256": semantic_sha256({"backend": "FAKE_REPLAY"}),
        "ownership_scope_sha256": semantic_sha256({"scope": "FAKE_REPLAY"}),
    }
    return ReadAuthorityContext.model_validate(
        {**payload, "authority_sha256": semantic_sha256(payload)}
    )


class ObservationStatus(str, Enum):
    SUCCESS = "SUCCESS"
    FAILURE = "FAILURE"


class ToolErrorCode(str, Enum):
    DUPLICATE_REQUEST = "DUPLICATE_REQUEST"
    SOURCE_UNAVAILABLE = "SOURCE_UNAVAILABLE"
    SOURCE_TIMEOUT = "SOURCE_TIMEOUT"
    SOURCE_SCHEMA_INVALID = "SOURCE_SCHEMA_INVALID"
    OWNERSHIP_NOT_PROVEN = "OWNERSHIP_NOT_PROVEN"
    REMOTE_DOCKER_FORBIDDEN = "REMOTE_DOCKER_FORBIDDEN"
    AMBIGUOUS_OWNED_RUNTIME = "AMBIGUOUS_OWNED_RUNTIME"
    TRUTH_ISOLATION_VIOLATION = "TRUTH_ISOLATION_VIOLATION"
    INTERNAL_CONTRACT_VIOLATION = "INTERNAL_CONTRACT_VIOLATION"


class MetricKind(str, Enum):
    ERROR_RATE = "ERROR_RATE"
    LATENCY_P95_MS = "LATENCY_P95_MS"
    REQUEST_SUPPORT = "REQUEST_SUPPORT"
    CPU_PERCENT = "CPU_PERCENT"
    MEMORY_BYTES = "MEMORY_BYTES"
    QUEUE_RESOURCE = "QUEUE_RESOURCE"


class MetricUnit(str, Enum):
    RATIO = "RATIO"
    MILLISECONDS = "MILLISECONDS"
    COUNT = "COUNT"
    PERCENT = "PERCENT"
    BYTES = "BYTES"
    SCALAR = "SCALAR"


METRIC_UNIT_BY_KIND = {
    MetricKind.ERROR_RATE: MetricUnit.RATIO,
    MetricKind.LATENCY_P95_MS: MetricUnit.MILLISECONDS,
    MetricKind.REQUEST_SUPPORT: MetricUnit.COUNT,
    MetricKind.CPU_PERCENT: MetricUnit.PERCENT,
    MetricKind.MEMORY_BYTES: MetricUnit.BYTES,
    MetricKind.QUEUE_RESOURCE: MetricUnit.SCALAR,
}


class LogSeverity(str, Enum):
    WARN = "WARN"
    ERROR = "ERROR"
    FATAL = "FATAL"
    DIAGNOSTIC = "DIAGNOSTIC"


class SpanStatus(str, Enum):
    OK = "OK"
    ERROR = "ERROR"
    UNSET = "UNSET"


class SpanRelationship(str, Enum):
    ROOT = "ROOT"
    PARENT = "PARENT"
    CHILD = "CHILD"


class RuntimeState(str, Enum):
    RUNNING = "RUNNING"
    EXITED = "EXITED"
    ABSENT = "ABSENT"
    OTHER = "OTHER"


class HealthState(str, Enum):
    HEALTHY = "HEALTHY"
    UNHEALTHY = "UNHEALTHY"
    STARTING = "STARTING"
    NOT_CONFIGURED = "NOT_CONFIGURED"
    UNKNOWN = "UNKNOWN"


class EndpointState(str, Enum):
    READY = "READY"
    NOT_READY = "NOT_READY"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    UNKNOWN = "UNKNOWN"


_SOURCE_BY_TOOL = {
    ToolName.QUERY_METRICS: EvidenceSource.METRICS,
    ToolName.SEARCH_LOGS: EvidenceSource.LOGS,
    ToolName.QUERY_TRACE_NEIGHBORHOOD: EvidenceSource.TRACES,
    ToolName.INSPECT_SERVICE_RUNTIME: EvidenceSource.RUNTIME,
    ToolName.INSPECT_RESOURCE_USAGE: EvidenceSource.RESOURCES,
}
_REF_SOURCE = {
    EvidenceSource.METRICS: "metrics",
    EvidenceSource.LOGS: "logs",
    EvidenceSource.TRACES: "traces",
    EvidenceSource.RUNTIME: "runtime",
    EvidenceSource.RESOURCES: "resources",
}


def _require_utc(value: datetime, *, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError(f"{field_name} must be timezone-aware UTC")


def _request_digest(value: DtaModel) -> str:
    return semantic_sha256(
        value.model_dump(mode="json", exclude={"normalized_request_sha256"})
    )


class WindowedReadRequest(DtaModel):
    run_id: RunId
    service: LogicalService
    started_at: datetime
    ended_at: datetime
    normalized_request_sha256: Sha256

    @model_validator(mode="after")
    def require_window_and_digest(self) -> WindowedReadRequest:
        _require_utc(self.started_at, field_name="started_at")
        _require_utc(self.ended_at, field_name="ended_at")
        if self.ended_at <= self.started_at:
            raise ValueError("request window must end after it starts")
        if self.ended_at - self.started_at > timedelta(hours=1):
            raise ValueError("request window exceeds one hour")
        if self.normalized_request_sha256 != _request_digest(self):
            raise ValueError("normalized request digest does not bind request")
        return self


class QueryMetricsRequest(WindowedReadRequest):
    schema_version: Literal["dta-v2.query-metrics-request.v1"]
    tool: Literal[ToolName.QUERY_METRICS]
    metric_kinds: tuple[MetricKind, ...] = Field(min_length=1, max_length=6)
    max_results: StrictInt = Field(ge=1, le=12)

    @model_validator(mode="after")
    def require_metric_order(self) -> QueryMetricsRequest:
        if len(self.metric_kinds) != len(set(self.metric_kinds)):
            raise ValueError("metric kinds contain duplicates")
        if self.metric_kinds != tuple(sorted(self.metric_kinds, key=lambda item: item.value)):
            raise ValueError("metric kinds are not canonical")
        if self.max_results < len(self.metric_kinds):
            raise ValueError("metrics result limit cannot cover requested metric kinds")
        return self


class SearchLogsRequest(WindowedReadRequest):
    schema_version: Literal["dta-v2.search-logs-request.v1"]
    tool: Literal[ToolName.SEARCH_LOGS]
    max_records: StrictInt = Field(ge=1, le=20)


class TraceNeighborhoodRequest(WindowedReadRequest):
    schema_version: Literal["dta-v2.trace-neighborhood-request.v1"]
    tool: Literal[ToolName.QUERY_TRACE_NEIGHBORHOOD]
    max_spans: StrictInt = Field(ge=1, le=40)


class ServiceReadRequest(DtaModel):
    run_id: RunId
    services: tuple[LogicalService, ...] = Field(min_length=1, max_length=10)
    normalized_request_sha256: Sha256

    @model_validator(mode="after")
    def require_services_and_digest(self) -> ServiceReadRequest:
        if len(self.services) != len(set(self.services)):
            raise ValueError("services contain duplicates")
        if self.services != tuple(sorted(self.services)):
            raise ValueError("services are not canonical")
        if self.normalized_request_sha256 != _request_digest(self):
            raise ValueError("normalized request digest does not bind request")
        return self


class InspectServiceRuntimeRequest(ServiceReadRequest):
    schema_version: Literal["dta-v2.inspect-service-runtime-request.v1"]
    tool: Literal[ToolName.INSPECT_SERVICE_RUNTIME]
    max_results: StrictInt = Field(ge=1, le=10)

    @model_validator(mode="after")
    def require_complete_service_limit(self) -> InspectServiceRuntimeRequest:
        if self.max_results < len(self.services):
            raise ValueError("runtime result limit cannot cover requested services")
        return self


class InspectResourceUsageRequest(ServiceReadRequest):
    schema_version: Literal["dta-v2.inspect-resource-usage-request.v1"]
    tool: Literal[ToolName.INSPECT_RESOURCE_USAGE]
    sampling_window_seconds: StrictInt = Field(ge=1, le=30)
    sample_count: StrictInt = Field(ge=2, le=10)


ReadToolRequest: TypeAlias = (
    QueryMetricsRequest
    | SearchLogsRequest
    | TraceNeighborhoodRequest
    | InspectServiceRuntimeRequest
    | InspectResourceUsageRequest
)

_REQUEST_TYPE_BY_TOOL = {
    ToolName.QUERY_METRICS: QueryMetricsRequest,
    ToolName.SEARCH_LOGS: SearchLogsRequest,
    ToolName.QUERY_TRACE_NEIGHBORHOOD: TraceNeighborhoodRequest,
    ToolName.INSPECT_SERVICE_RUNTIME: InspectServiceRuntimeRequest,
    ToolName.INSPECT_RESOURCE_USAGE: InspectResourceUsageRequest,
}


def revalidate_read_tool_request(value: object) -> ReadToolRequest:
    if type(value) not in set(_REQUEST_TYPE_BY_TOOL.values()):
        raise ValueError("read-tool request has an unsupported exact type")
    assert isinstance(
        value,
        (
            QueryMetricsRequest,
            SearchLogsRequest,
            TraceNeighborhoodRequest,
            InspectServiceRuntimeRequest,
            InspectResourceUsageRequest,
        ),
    )
    if type(value) is QueryMetricsRequest and value.tool is ToolName.QUERY_METRICS:
        return QueryMetricsRequest.model_validate(value.model_dump())
    if type(value) is SearchLogsRequest and value.tool is ToolName.SEARCH_LOGS:
        return SearchLogsRequest.model_validate(value.model_dump())
    if (
        type(value) is TraceNeighborhoodRequest
        and value.tool is ToolName.QUERY_TRACE_NEIGHBORHOOD
    ):
        return TraceNeighborhoodRequest.model_validate(value.model_dump())
    if (
        type(value) is InspectServiceRuntimeRequest
        and value.tool is ToolName.INSPECT_SERVICE_RUNTIME
    ):
        return InspectServiceRuntimeRequest.model_validate(value.model_dump())
    if (
        type(value) is InspectResourceUsageRequest
        and value.tool is ToolName.INSPECT_RESOURCE_USAGE
    ):
        return InspectResourceUsageRequest.model_validate(value.model_dump())
    raise ValueError("read-tool request type differs from tool")


def parse_read_tool_request_json(value: str) -> ReadToolRequest:
    try:
        raw = json.loads(value)
    except (TypeError, json.JSONDecodeError) as error:
        raise ValueError("canonical request envelope is invalid JSON") from error
    if not isinstance(raw, dict):
        raise ValueError("canonical request envelope is not an object")
    try:
        tool = ToolName(raw["tool"])
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("canonical request envelope has an invalid tool") from error
    if tool is ToolName.QUERY_METRICS:
        return QueryMetricsRequest.model_validate_json(value)
    if tool is ToolName.SEARCH_LOGS:
        return SearchLogsRequest.model_validate_json(value)
    if tool is ToolName.QUERY_TRACE_NEIGHBORHOOD:
        return TraceNeighborhoodRequest.model_validate_json(value)
    if tool is ToolName.INSPECT_SERVICE_RUNTIME:
        return InspectServiceRuntimeRequest.model_validate_json(value)
    return InspectResourceUsageRequest.model_validate_json(value)


def _build(model: type[ReadToolRequest], payload: dict[str, Any]) -> ReadToolRequest:
    draft = model.model_construct(**payload, normalized_request_sha256="0" * 64)
    return model.model_validate(
        {**payload, "normalized_request_sha256": _request_digest(draft)}
    )


def build_query_metrics_request(
    *,
    run_id: str,
    service: str,
    started_at: datetime,
    ended_at: datetime,
    metric_kinds: tuple[MetricKind, ...],
    max_results: int,
) -> QueryMetricsRequest:
    request = _build(
        QueryMetricsRequest,
        {
            "schema_version": "dta-v2.query-metrics-request.v1",
            "tool": ToolName.QUERY_METRICS,
            "run_id": run_id,
            "service": service.strip() if isinstance(service, str) else service,
            "started_at": started_at,
            "ended_at": ended_at,
            "metric_kinds": tuple(sorted(metric_kinds, key=lambda item: item.value)),
            "max_results": max_results,
        },
    )
    assert isinstance(request, QueryMetricsRequest)
    return request


def build_search_logs_request(
    *, run_id: str, service: str, started_at: datetime, ended_at: datetime, max_records: int
) -> SearchLogsRequest:
    request = _build(
        SearchLogsRequest,
        {
            "schema_version": "dta-v2.search-logs-request.v1",
            "tool": ToolName.SEARCH_LOGS,
            "run_id": run_id,
            "service": service.strip() if isinstance(service, str) else service,
            "started_at": started_at,
            "ended_at": ended_at,
            "max_records": max_records,
        },
    )
    assert isinstance(request, SearchLogsRequest)
    return request


def build_trace_neighborhood_request(
    *, run_id: str, service: str, started_at: datetime, ended_at: datetime, max_spans: int
) -> TraceNeighborhoodRequest:
    request = _build(
        TraceNeighborhoodRequest,
        {
            "schema_version": "dta-v2.trace-neighborhood-request.v1",
            "tool": ToolName.QUERY_TRACE_NEIGHBORHOOD,
            "run_id": run_id,
            "service": service.strip() if isinstance(service, str) else service,
            "started_at": started_at,
            "ended_at": ended_at,
            "max_spans": max_spans,
        },
    )
    assert isinstance(request, TraceNeighborhoodRequest)
    return request


def build_inspect_service_runtime_request(
    *, run_id: str, services: tuple[str, ...], max_results: int
) -> InspectServiceRuntimeRequest:
    request = _build(
        InspectServiceRuntimeRequest,
        {
            "schema_version": "dta-v2.inspect-service-runtime-request.v1",
            "tool": ToolName.INSPECT_SERVICE_RUNTIME,
            "run_id": run_id,
            "services": tuple(sorted(item.strip() for item in services)),
            "max_results": max_results,
        },
    )
    assert isinstance(request, InspectServiceRuntimeRequest)
    return request


def build_inspect_resource_usage_request(
    *,
    run_id: str,
    services: tuple[str, ...],
    sampling_window_seconds: int,
    sample_count: int,
) -> InspectResourceUsageRequest:
    request = _build(
        InspectResourceUsageRequest,
        {
            "schema_version": "dta-v2.inspect-resource-usage-request.v1",
            "tool": ToolName.INSPECT_RESOURCE_USAGE,
            "run_id": run_id,
            "services": tuple(sorted(item.strip() for item in services)),
            "sampling_window_seconds": sampling_window_seconds,
            "sample_count": sample_count,
        },
    )
    assert isinstance(request, InspectResourceUsageRequest)
    return request


class MetricRecord(DtaModel):
    service: LogicalService
    metric_kind: MetricKind
    value: StrictFloat
    unit: MetricUnit
    sample_count: StrictInt = Field(ge=0, le=10000)


class DiagnosticLogRecord(DtaModel):
    observed_at: datetime
    service: LogicalService
    severity: LogSeverity
    message: SafeDiagnosticText

    @field_validator("observed_at")
    @classmethod
    def require_timestamp(cls, value: datetime) -> datetime:
        _require_utc(value, field_name="observed_at")
        return value


class TraceNeighborhoodRecord(DtaModel):
    anchor_service: LogicalService
    service_path: tuple[LogicalService, ...] = Field(min_length=1, max_length=12)
    relationship: SpanRelationship
    service: LogicalService
    parent_service: LogicalService | None
    operation: SafeOperation
    status: SpanStatus
    duration_ms: StrictFloat = Field(ge=0)
    first_error_location: StrictBool


class RuntimeRecord(DtaModel):
    logical_service: LogicalService
    owned_container_present: StrictBool
    state: RuntimeState
    health: HealthState
    restart_count: StrictInt = Field(ge=0)
    exit_code: StrictInt | None
    endpoint_probe_performed: StrictBool
    endpoint_state: EndpointState

    @model_validator(mode="after")
    def require_endpoint_evidence(self) -> RuntimeRecord:
        if not self.endpoint_probe_performed and self.endpoint_state not in {
            EndpointState.UNKNOWN,
            EndpointState.NOT_APPLICABLE,
        }:
            raise ValueError("runtime endpoint state claims an unperformed probe")
        return self


class ResourceSample(DtaModel):
    offset_ms: StrictInt = Field(ge=0, le=30000)
    cpu_percent: StrictFloat = Field(ge=0)
    memory_bytes: StrictInt = Field(ge=0)


class ResourceUsageRecord(DtaModel):
    logical_service: LogicalService
    sampling_window_seconds: StrictInt = Field(ge=1, le=30)
    samples: tuple[ResourceSample, ...] = Field(min_length=2, max_length=10)
    memory_slope_bytes_per_second: StrictFloat


ToolResultRecord: TypeAlias = (
    MetricRecord
    | DiagnosticLogRecord
    | TraceNeighborhoodRecord
    | RuntimeRecord
    | ResourceUsageRecord
)

_RESULT_TYPE_BY_TOOL = {
    ToolName.QUERY_METRICS: MetricRecord,
    ToolName.SEARCH_LOGS: DiagnosticLogRecord,
    ToolName.QUERY_TRACE_NEIGHBORHOOD: TraceNeighborhoodRecord,
    ToolName.INSPECT_SERVICE_RUNTIME: RuntimeRecord,
    ToolName.INSPECT_RESOURCE_USAGE: ResourceUsageRecord,
}


_TRUTH_MARKERS = (
    "paymentfailure.defaultvariant",
    "emailmemoryleak",
    "defaultvariant",
    "injected variant",
    "expected root",
    "expected mechanism",
    "expected runbook",
    "scenario controller",
    "scenario-controller",
    "ground truth",
    "gold label",
    "gold_label",
)
_OPAQUE_HEX_IDENTITY_RE = re.compile(
    r"(?<![0-9a-f])[0-9a-f]{16,64}(?![0-9a-f])", re.I
)
_UUID_IDENTITY_RE = re.compile(
    r"(?<![0-9a-f])[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
    r"[0-9a-f]{4}-[0-9a-f]{12}(?![0-9a-f])",
    re.I,
)
_CONFUSABLE_TRANSLATION = str.maketrans(
    {
        "а": "a",
        "с": "c",
        "е": "e",
        "к": "k",
        "м": "m",
        "о": "o",
        "р": "p",
        "т": "t",
        "х": "x",
        "у": "y",
        "Α": "a",
        "Β": "b",
        "Ε": "e",
        "Ι": "i",
        "Κ": "k",
        "Μ": "m",
        "Ν": "n",
        "Ο": "o",
        "Ρ": "p",
        "Τ": "t",
        "Χ": "x",
        "Υ": "y",
        "α": "a",
        "β": "b",
        "ε": "e",
        "ι": "i",
        "κ": "k",
        "μ": "m",
        "ν": "n",
        "ο": "o",
        "ρ": "p",
        "τ": "t",
        "χ": "x",
        "υ": "y",
    }
)


class TruthIsolationError(ValueError):
    pass


_STRUCTURAL_IDENTITY_KEYS = frozenset(
    {
        "id",
        "trace",
        "traceid",
        "span",
        "spanid",
        "container",
        "containerid",
    }
)
_DECIMAL_DATE_TIME_RE = re.compile(
    r"(?P<year>\d{4})(?P<date_sep>[-/])(?P<month>\d{2})"
    r"(?P=date_sep)(?P<day>\d{2}) "
    r"(?P<hour>\d{2}):(?P<minute>\d{2}):(?P<second>\d{2})"
    r"(?:[.,]\d+)?"
)
_DECIMAL_BUILD_RE = re.compile(
    r"(?P<year>\d{4})\.(?P<month>\d{2})\.(?P<day>\d{2})\.\d+"
)
_IDENTITY_LABEL_RE = re.compile(
    r"(?<![a-z0-9])(?:container(?:[\s._-]*id)?|"
    r"trace(?:[\s._-]*id)?|span(?:\[\s*id\s*\]|[\s._-]*id)?|"
    r"id)[\s:=._\-\[\]\"']*$",
    re.I,
)


def _identity_hex(character: str) -> str | None:
    if character in "0123456789abcdef":
        return character
    return None


def _normalize_identity_text(value: str) -> str:
    direct_skeleton_mapped = "".join(
        "|"
        if unicodedata.category(character).startswith("M")
        else DIRECT_ASCII_HEX_CONFUSABLES.get(ord(character), character)
        for character in value
    )
    return unicodedata.normalize("NFKC", direct_skeleton_mapped).casefold()


def _is_identity_separator(character: str) -> bool:
    return unicodedata.category(character)[0] in {"M", "P", "S", "Z"}


def _identity_like_runs(value: str) -> tuple[str, ...]:
    normalized = _normalize_identity_text(value)
    runs: list[str] = []
    current: list[str] = []
    pending_separators: list[str] = []
    index = 0
    while index < len(normalized):
        character = normalized[index]
        if character.isalnum():
            end = index + 1
            while end < len(normalized) and normalized[end].isalnum():
                end += 1
            word = normalized[index:end]
            mapped_word = tuple(_identity_hex(member) for member in word)
            mapped_count = sum(member is not None for member in mapped_word)
            # The pinned table owns skeleton mapping. A near-complete token uses a
            # neutral digit only for fail-closed length detection, never as a
            # claimed Unicode skeleton for its remaining characters.
            if mapped_count != len(word) and not (
                mapped_count >= 14 and mapped_count * 8 >= len(word) * 7
            ):
                if current:
                    runs.append("".join(current))
                current = []
                pending_separators = []
                index = end
                continue
            current.extend(pending_separators)
            pending_separators = []
            current.extend(member or "0" for member in mapped_word)
            index = end
            continue
        elif _is_identity_separator(character):
            if current:
                pending_separators.append(character)
        else:
            if current:
                runs.append("".join(current))
            current = []
            pending_separators = []
        index += 1
    if current:
        runs.append("".join(current))
    return tuple(runs)


def _identity_run_shape(run: str) -> tuple[str, tuple[int, ...]]:
    compact: list[str] = []
    groups: list[int] = []
    group_length = 0
    for character in run:
        if _is_identity_separator(character):
            if group_length:
                groups.append(group_length)
                group_length = 0
            continue
        identity_hex = _identity_hex(character)
        if identity_hex is None:
            raise ValueError("identity run contains a non-identity character")
        compact.append(identity_hex)
        group_length += 1
    if group_length:
        groups.append(group_length)
    return "".join(compact), tuple(groups)


def _identity_shaped_run(run: str, *, minimum_length: int) -> str | None:
    compact, _ = _identity_run_shape(run)
    if not minimum_length <= len(compact) <= 64:
        return None
    return compact


def _plausible_date_parts(match: re.Match[str]) -> bool:
    try:
        datetime(
            int(match.group("year")),
            int(match.group("month")),
            int(match.group("day")),
        )
    except ValueError:
        return False
    if "hour" not in match.groupdict():
        return True
    return (
        0 <= int(match.group("hour")) <= 23
        and 0 <= int(match.group("minute")) <= 59
        and 0 <= int(match.group("second")) <= 59
    )


def _is_exempt_decimal_date_run(
    value: str,
    run: str,
    *,
    structurally_labeled: bool = False,
) -> bool:
    match = _DECIMAL_DATE_TIME_RE.fullmatch(run) or _DECIMAL_BUILD_RE.fullmatch(run)
    if (
        structurally_labeled
        or match is None
        or not _plausible_date_parts(match)
    ):
        return False
    normalized = _normalize_identity_text(value)
    occurrences = tuple(re.finditer(re.escape(run), normalized))
    if not occurrences:
        return False
    return all(
        _IDENTITY_LABEL_RE.search(normalized[: occurrence.start()]) is None
        for occurrence in occurrences
    )


def _is_structural_identity_key(value: object) -> bool:
    if not isinstance(value, str):
        return False
    normalized = _normalize_identity_text(value).strip()
    canonical = re.sub(r"[\s._\-\[\]]+", "", normalized)
    return canonical in _STRUCTURAL_IDENTITY_KEYS


def _pure_identity_fragment(value: str) -> str | None:
    normalized = _normalize_identity_text(value).strip()
    if not normalized or any(
        _identity_hex(character) is None
        and not _is_identity_separator(character)
        for character in normalized
    ):
        return None
    runs = _identity_like_runs(normalized)
    if len(runs) != 1:
        return None
    if _is_exempt_decimal_date_run(normalized, runs[0]):
        return None
    return _identity_shaped_run(runs[0], minimum_length=1)


def _ordered_text_value_leaves(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        leaves: list[str] = []
        for key in sorted(value, key=lambda item: (type(item).__name__, repr(item))):
            leaves.extend(_ordered_text_value_leaves(value[key]))
        return leaves
    if isinstance(value, (list, tuple)):
        leaves = []
        for member in value:
            leaves.extend(_ordered_text_value_leaves(member))
        return leaves
    return []


def _aggregated_identity_fragments(value: object) -> tuple[str, ...]:
    fragments = [
        fragment
        for text in _ordered_text_value_leaves(value)
        if (fragment := _pure_identity_fragment(text)) is not None
    ]
    candidates: list[str] = []
    for start in range(len(fragments)):
        combined = ""
        for end in range(start, len(fragments)):
            combined += fragments[end]
            if len(combined) > 64:
                break
            if end > start and len(combined) >= 16:
                candidates.append(combined)
    return tuple(candidates)


def assert_truth_isolated(value: object) -> None:
    pending = [(value, False)]
    text_values: list[str] = []
    identity_text_values: list[tuple[str, bool]] = []
    while pending:
        item, structurally_labeled = pending.pop()
        if isinstance(item, str):
            normalized_text = unicodedata.normalize("NFKC", item)
            text_values.append(normalized_text)
            identity_text_values.append((item, structurally_labeled))
            if any(
                unicodedata.category(character) in {"Cc", "Cf"}
                for character in normalized_text
            ):
                raise TruthIsolationError(
                    "model-visible result contains control or invisible text"
                )
        elif isinstance(item, dict):
            for key, member in item.items():
                pending.append((key, False))
                pending.append(
                    (
                        member,
                        structurally_labeled or _is_structural_identity_key(key),
                    )
                )
        elif isinstance(item, (list, tuple)):
            pending.extend((member, structurally_labeled) for member in item)
        elif isinstance(item, (set, frozenset)):
            pending.extend((member, structurally_labeled) for member in item)
    serialized = unicodedata.normalize(
        "NFKC", json.dumps(value, ensure_ascii=False, sort_keys=True)
    ).casefold()
    if any(unicodedata.category(character) in {"Cc", "Cf"} for character in serialized):
        raise TruthIsolationError(
            "model-visible result contains control or invisible text"
        )
    normalized = re.sub(r"[^a-z0-9%]+", " ", serialized)
    compact = re.sub(r"[^a-z0-9]+", "", serialized)
    normalized_markers = (
        "feature flag",
        "flag key",
        "injected variant",
        "expected root",
        "expected fault mechanism",
        "expected mechanism",
        "expected runbook",
        "scenario controller",
        "ground truth",
        "gold label",
    )
    compact_markers = (
        "paymentfailuredefaultvariant",
        "emailmemoryleak",
        "defaultvariant",
        "featureflagkey",
        "injectedvariant",
        "expectedroot",
        "expectedfaultmechanism",
        "expectedmechanism",
        "expectedrunbook",
        "scenariocontroller",
        "groundtruth",
        "goldlabel",
    )
    for text_value in text_values:
        scripts = {
            "LATIN"
            if "LATIN" in unicodedata.name(character, "")
            else "CONFUSABLE"
            for character in text_value
            if "LATIN" in unicodedata.name(character, "")
            or "CYRILLIC" in unicodedata.name(character, "")
            or "GREEK" in unicodedata.name(character, "")
        }
        if scripts == {"LATIN", "CONFUSABLE"}:
            skeleton = text_value.casefold().translate(_CONFUSABLE_TRANSLATION)
            skeleton_compact = re.sub(r"[^a-z0-9]+", "", skeleton)
            if any(marker in skeleton_compact for marker in compact_markers):
                raise TruthIsolationError(
                    "model-visible result contains confusable evaluator truth"
                )
    if (
        any(marker in serialized for marker in _TRUTH_MARKERS)
        or any(marker in normalized for marker in normalized_markers)
        or any(marker in compact for marker in compact_markers)
        or "100%" in serialized
    ):
        raise TruthIsolationError("model-visible result contains evaluator truth")
    individual_identity = any(
        _OPAQUE_HEX_IDENTITY_RE.search(text_value)
        or _UUID_IDENTITY_RE.search(text_value)
        or any(
            _identity_shaped_run(run, minimum_length=16) is not None
            and not _is_exempt_decimal_date_run(
                text_value,
                run,
                structurally_labeled=structurally_labeled,
            )
            for run in _identity_like_runs(text_value)
        )
        for text_value, structurally_labeled in identity_text_values
    )
    if individual_identity or _aggregated_identity_fragments(value):
        raise TruthIsolationError("model-visible result contains an opaque identity")


class ToolCounters(DtaModel):
    dispatch_ordinal: StrictInt = Field(ge=1, le=4)
    backend_call_count: StrictInt = Field(ge=0, le=4)
    success_count: StrictInt = Field(ge=0, le=4)
    failure_count: StrictInt = Field(ge=0, le=4)

    @model_validator(mode="after")
    def require_counting(self) -> ToolCounters:
        if self.success_count + self.failure_count != self.dispatch_ordinal:
            raise ValueError("success and failure counters differ from dispatch count")
        if self.backend_call_count > self.dispatch_ordinal:
            raise ValueError("backend calls exceed dispatches")
        return self


class ReadToolObservation(DtaModel):
    schema_version: Literal["dta-v2.read-tool-observation.v1"]
    tool: ToolName
    source: EvidenceSource
    run_id: RunId
    authority: ReadAuthorityContext
    authority_sha256: Sha256
    request_sha256: Sha256
    duplicate_of_request_sha256: Sha256 | None
    evidence_ref: str
    status: ObservationStatus
    error_code: ToolErrorCode | None
    results: tuple[ToolResultRecord, ...] = Field(max_length=40)
    result_count: StrictInt = Field(ge=0, le=40)
    truncated: StrictBool
    observed_at_start: datetime
    observed_at_end: datetime
    monotonic_latency_ms: StrictInt = Field(ge=0)
    counters: ToolCounters
    artifact_sha256: Sha256

    @model_validator(mode="after")
    def require_observation_semantics(self) -> ReadToolObservation:
        _require_utc(self.observed_at_start, field_name="observed_at_start")
        _require_utc(self.observed_at_end, field_name="observed_at_end")
        if self.observed_at_end < self.observed_at_start:
            raise ValueError("observation window is reversed")
        if self.source is not _SOURCE_BY_TOOL[self.tool]:
            raise ValueError("tool source binding differs")
        authority = ReadAuthorityContext.model_validate(self.authority.model_dump())
        if authority.authority_sha256 != self.authority_sha256:
            raise ValueError("observation authority context differs from digest")
        expected_ref = (
            f"evidence://{self.run_id}/{_REF_SOURCE[self.source]}/"
            f"{self.counters.dispatch_ordinal:04d}"
        )
        if self.evidence_ref != expected_ref:
            raise ValueError("evidence reference does not bind source and dispatch")
        if self.result_count != len(self.results):
            raise ValueError("result count differs from results")
        expected_result_type = _RESULT_TYPE_BY_TOOL[self.tool]
        if any(not isinstance(item, expected_result_type) for item in self.results):
            raise ValueError("tool observation contains a result from another adapter")
        if self.status is ObservationStatus.SUCCESS:
            if self.error_code is not None:
                raise ValueError("successful observation cannot carry an error")
        elif (
            self.error_code is None
            or self.results
            or self.result_count
            or self.truncated
        ):
            raise ValueError("failed observation requires an error and no results")
        if self.error_code is ToolErrorCode.DUPLICATE_REQUEST:
            if self.duplicate_of_request_sha256 != self.request_sha256:
                raise ValueError("duplicate failure does not bind the prior request digest")
        elif self.duplicate_of_request_sha256 is not None:
            raise ValueError("nonduplicate observation cannot carry duplicate lineage")
        result_payload = [item.model_dump(mode="json") for item in self.results]
        assert_truth_isolated(result_payload)
        expected = semantic_sha256(
            self.model_dump(mode="json", exclude={"artifact_sha256"})
        )
        if self.artifact_sha256 != expected:
            raise ValueError("artifact digest does not bind observation")
        return self


def revalidate_observation(value: ReadToolObservation) -> ReadToolObservation:
    return ReadToolObservation.model_validate(value.model_dump())


def validate_results_for_request(
    request: ReadToolRequest,
    records: tuple[ToolResultRecord, ...],
) -> tuple[ToolResultRecord, ...]:
    """Revalidate nested records and enforce exact request/result correspondence."""

    validated: list[ToolResultRecord] = []
    for record in records:
        if request.tool is ToolName.QUERY_METRICS and type(record) is MetricRecord:
            validated.append(MetricRecord.model_validate(record.model_dump()))
        elif (
            request.tool is ToolName.SEARCH_LOGS
            and type(record) is DiagnosticLogRecord
        ):
            validated.append(DiagnosticLogRecord.model_validate(record.model_dump()))
        elif (
            request.tool is ToolName.QUERY_TRACE_NEIGHBORHOOD
            and type(record) is TraceNeighborhoodRecord
        ):
            validated.append(
                TraceNeighborhoodRecord.model_validate(record.model_dump())
            )
        elif (
            request.tool is ToolName.INSPECT_SERVICE_RUNTIME
            and type(record) is RuntimeRecord
        ):
            validated.append(RuntimeRecord.model_validate(record.model_dump()))
        elif (
            request.tool is ToolName.INSPECT_RESOURCE_USAGE
            and type(record) is ResourceUsageRecord
        ):
            validated.append(ResourceUsageRecord.model_validate(record.model_dump()))
        else:
            raise ValueError("backend result has an unsupported exact type")
    output = tuple(validated)
    if isinstance(request, QueryMetricsRequest):
        metric_records = tuple(item for item in output if isinstance(item, MetricRecord))
        if len(metric_records) > request.max_results:
            raise ValueError("metrics result exceeds request limit")
        if tuple(item.metric_kind for item in metric_records) != request.metric_kinds:
            raise ValueError("metrics result kinds or canonical order differ")
        if any(item.service != request.service for item in metric_records):
            raise ValueError("metrics result service differs from request")
        if any(
            item.unit is not METRIC_UNIT_BY_KIND[item.metric_kind]
            for item in metric_records
        ):
            raise ValueError("metrics result unit differs from metric kind")
    elif isinstance(request, SearchLogsRequest):
        log_records = tuple(
            item for item in output if isinstance(item, DiagnosticLogRecord)
        )
        if len(log_records) > request.max_records:
            raise ValueError("logs result exceeds request limit")
        if any(item.service != request.service for item in log_records):
            raise ValueError("logs result service differs from request")
        if any(
            not request.started_at <= item.observed_at <= request.ended_at
            for item in log_records
        ):
            raise ValueError("logs result is outside request window")
        if log_records != tuple(
            sorted(
                log_records,
                key=lambda item: (
                    item.observed_at,
                    item.severity.value,
                    item.message,
                ),
            )
        ):
            raise ValueError("logs result order is not canonical")
    elif isinstance(request, TraceNeighborhoodRequest):
        trace_records = tuple(
            item for item in output if isinstance(item, TraceNeighborhoodRecord)
        )
        if len(trace_records) > request.max_spans:
            raise ValueError("trace result exceeds request limit")
        if any(item.anchor_service != request.service for item in trace_records):
            raise ValueError("trace anchor differs from request")
        if any(item.service not in item.service_path for item in trace_records):
            raise ValueError("trace service is absent from its path")
        if trace_records != tuple(
            sorted(
                trace_records,
                key=lambda item: (
                    item.service_path,
                    item.service,
                    item.relationship.value,
                    item.parent_service or "",
                    item.operation,
                    item.status.value,
                    item.duration_ms,
                    item.first_error_location,
                ),
            )
        ):
            raise ValueError("trace result order is not canonical")
    elif isinstance(request, InspectServiceRuntimeRequest):
        runtime_records = tuple(
            item for item in output if isinstance(item, RuntimeRecord)
        )
        expected_services = request.services
        if tuple(item.logical_service for item in runtime_records) != expected_services:
            raise ValueError("runtime result services or canonical order differ")
    elif isinstance(request, InspectResourceUsageRequest):
        resource_records = tuple(
            item for item in output if isinstance(item, ResourceUsageRecord)
        )
        if tuple(item.logical_service for item in resource_records) != request.services:
            raise ValueError("resource result services or canonical order differ")
        for item in resource_records:
            expected_offsets = tuple(
                request.sampling_window_seconds * 1000 * index
                // (request.sample_count - 1)
                for index in range(request.sample_count)
            )
            if (
                item.sampling_window_seconds != request.sampling_window_seconds
                or len(item.samples) != request.sample_count
                or tuple(sample.offset_ms for sample in item.samples)
                != expected_offsets
            ):
                raise ValueError("resource sampling contract differs from request")
    return output


def validate_truncation_for_request(
    request: ReadToolRequest,
    records: tuple[ToolResultRecord, ...],
    truncated: bool,
) -> None:
    """Reject truncation claims that cannot be established from a limit+1 read."""

    if type(truncated) is not bool:
        raise ValueError("backend truncation marker is not a strict boolean")
    if not truncated:
        return
    if isinstance(request, SearchLogsRequest):
        limit = request.max_records
    elif isinstance(request, TraceNeighborhoodRequest):
        limit = request.max_spans
    else:
        raise ValueError("this read tool cannot produce a truncated result")
    if len(records) != limit:
        raise ValueError("truncated result does not fill its request limit")


def build_read_tool_observation(
    *,
    request: ReadToolRequest,
    authority: ReadAuthorityContext,
    duplicate_of_request_sha256: str | None,
    status: ObservationStatus,
    error_code: ToolErrorCode | None,
    results: tuple[ToolResultRecord, ...],
    truncated: bool,
    observed_at_start: datetime,
    observed_at_end: datetime,
    monotonic_latency_ms: int,
    counters: ToolCounters,
) -> ReadToolObservation:
    source = _SOURCE_BY_TOOL[request.tool]
    payload: dict[str, Any] = {
        "schema_version": "dta-v2.read-tool-observation.v1",
        "tool": request.tool,
        "source": source,
        "run_id": request.run_id,
        "authority": authority,
        "authority_sha256": authority.authority_sha256,
        "request_sha256": request.normalized_request_sha256,
        "duplicate_of_request_sha256": duplicate_of_request_sha256,
        "evidence_ref": (
            f"evidence://{request.run_id}/{_REF_SOURCE[source]}/"
            f"{counters.dispatch_ordinal:04d}"
        ),
        "status": status,
        "error_code": error_code,
        "results": results,
        "result_count": len(results),
        "truncated": truncated,
        "observed_at_start": observed_at_start,
        "observed_at_end": observed_at_end,
        "monotonic_latency_ms": monotonic_latency_ms,
        "counters": counters,
    }
    draft = ReadToolObservation.model_construct(**payload, artifact_sha256="0" * 64)
    return ReadToolObservation.model_validate(
        {
            **payload,
            "artifact_sha256": semantic_sha256(
                draft.model_dump(mode="json", exclude={"artifact_sha256"})
            ),
        }
    )
