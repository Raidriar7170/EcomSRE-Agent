"""Production-capable loopback telemetry adapters for DTA v2 read tools."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import math
import re
import time
from typing import TYPE_CHECKING, Literal, Protocol, cast
from urllib.parse import urlencode, urlsplit
from urllib.request import (
    HTTPRedirectHandler,
    OpenerDirector,
    ProxyHandler,
    Request,
    build_opener,
)

from pydantic import StrictFloat, field_validator, model_validator

from ecomsre.dta_v2.contracts import DtaModel, Identifier, semantic_sha256
from ecomsre.dta_v2.docker_read_adapters import (
    DockerJsonClient,
    DockerReadAdapter,
    UnixSocketDockerClient,
)
from ecomsre.dta_v2.read_tools import BackendResult, ReadBackendFailure
from ecomsre.dta_v2.tool_contracts import (
    DiagnosticLogRecord,
    InspectResourceUsageRequest,
    InspectServiceRuntimeRequest,
    LogSeverity,
    MetricKind,
    MetricRecord,
    MetricUnit,
    QueryMetricsRequest,
    ReadAuthorityContext,
    ReadAuthorityMode,
    ReadToolRequest,
    SearchLogsRequest,
    SpanRelationship,
    SpanStatus,
    ToolErrorCode,
    ToolResultRecord,
    TraceNeighborhoodRecord,
    TraceNeighborhoodRequest,
)

if TYPE_CHECKING:
    from ecomsre_live_sandbox.contracts import ResolvedSandbox


class JsonHttpTransport(Protocol):
    def request_json(
        self,
        *,
        base_url: str,
        path: str,
        method: str,
        payload: object | None,
    ) -> object: ...


class LocalReadBackendConfig(DtaModel):
    prometheus_base_url: str
    opensearch_base_url: str
    jaeger_base_url: str
    opensearch_index: Literal["otel-logs-*"]
    docker_endpoint: str
    compose_project: Identifier
    sandbox_label_key: Identifier
    sandbox_label_value: Identifier
    timeout_seconds: StrictFloat
    authority: ReadAuthorityContext

    @field_validator(
        "prometheus_base_url", "opensearch_base_url", "jaeger_base_url"
    )
    @classmethod
    def require_loopback_http(cls, value: str) -> str:
        if not isinstance(value, str):
            raise ValueError("telemetry endpoint must be a string")
        parsed = urlsplit(value)
        if (
            parsed.scheme != "http"
            or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
            or parsed.port is None
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("telemetry endpoint must be an exact loopback HTTP origin")
        return value.rstrip("/")

    @field_validator("docker_endpoint")
    @classmethod
    def require_local_unix_docker(cls, value: str) -> str:
        if not isinstance(value, str) or not value.startswith("unix://"):
            raise ValueError("Docker endpoint must be a local Unix socket")
        path = value.removeprefix("unix://")
        if not path.startswith("/") or "\x00" in path:
            raise ValueError("Docker endpoint must be a valid local Unix socket")
        return value

    @field_validator("timeout_seconds")
    @classmethod
    def require_timeout(cls, value: float) -> float:
        if not math.isfinite(value) or not 0.1 <= value <= 15:
            raise ValueError("read-tool timeout must be between 0.1 and 15 seconds")
        return value

    @model_validator(mode="after")
    def require_exact_authority(self) -> LocalReadBackendConfig:
        authority = ReadAuthorityContext.model_validate(self.authority.model_dump())
        if authority.mode is ReadAuthorityMode.FAKE_REPLAY:
            return self
        endpoint_digest = semantic_sha256(
            {
                "prometheus": self.prometheus_base_url,
                "opensearch": self.opensearch_base_url,
                "jaeger": self.jaeger_base_url,
                "docker": self.docker_endpoint,
            }
        )
        ownership_digest = semantic_sha256(
            {
                "compose_project": self.compose_project,
                "sandbox_label_key": self.sandbox_label_key,
                "sandbox_label_value": self.sandbox_label_value,
            }
        )
        if (
            authority.resolved_endpoints_sha256 != endpoint_digest
            or authority.ownership_scope_sha256 != ownership_digest
        ):
            raise ValueError("production read config differs from authenticated authority")
        return self


_OWNED_READ_CAPABILITY_TOKEN = object()


def _build_owned_read_authority(
    *,
    daemon_identity: str,
    docker_context: str,
    config_bundle_sha256: str,
    resolved_sandbox_sha256: str,
    prometheus_base_url: str,
    opensearch_base_url: str,
    jaeger_base_url: str,
    docker_endpoint: str,
    compose_project: str,
    sandbox_label_key: str,
    sandbox_label_value: str,
) -> ReadAuthorityContext:
    if not isinstance(daemon_identity, str) or not daemon_identity.strip():
        raise ValueError("authenticated daemon identity is absent")
    payload: dict[str, object] = {
        "schema_version": "dta-v2.read-authority.v1",
        "mode": ReadAuthorityMode.OWNED_LOCAL,
        "daemon_identity_sha256": semantic_sha256(
            {"daemon_identity": daemon_identity.strip()}
        ),
        "docker_context_sha256": semantic_sha256(
            {"docker_context": docker_context}
        ),
        "config_bundle_sha256": config_bundle_sha256,
        "resolved_sandbox_sha256": resolved_sandbox_sha256,
        "resolved_endpoints_sha256": semantic_sha256(
            {
                "prometheus": prometheus_base_url,
                "opensearch": opensearch_base_url,
                "jaeger": jaeger_base_url,
                "docker": docker_endpoint,
            }
        ),
        "ownership_scope_sha256": semantic_sha256(
            {
                "compose_project": compose_project,
                "sandbox_label_key": sandbox_label_key,
                "sandbox_label_value": sandbox_label_value,
            }
        ),
    }
    return ReadAuthorityContext.model_validate(
        {**payload, "authority_sha256": semantic_sha256(payload)}
    )


@dataclass(frozen=True, init=False)
class _OwnedReadCapability:
    config: LocalReadBackendConfig
    resolved_sandbox: ResolvedSandbox
    _integrity_sha256: str
    _token: object = field(repr=False, compare=False)

    def __init__(
        self,
        *,
        _token: object | None = None,
        config: LocalReadBackendConfig,
        resolved_sandbox: object = None,
    ) -> None:
        from ecomsre_live_sandbox.contracts import ResolvedSandbox

        if _token is not _OWNED_READ_CAPABILITY_TOKEN:
            raise TypeError("owned read capability must come from Sandbox lifecycle")
        if type(resolved_sandbox) is not ResolvedSandbox:
            raise TypeError("owned read capability requires freshly resolved Sandbox")
        validated_resolved = cast("ResolvedSandbox", resolved_sandbox)
        validated = LocalReadBackendConfig.model_validate(config.model_dump())
        if validated.authority.mode is not ReadAuthorityMode.OWNED_LOCAL:
            raise TypeError("owned read capability requires owned-local authority")
        object.__setattr__(self, "config", validated)
        object.__setattr__(self, "resolved_sandbox", validated_resolved)
        object.__setattr__(
            self,
            "_integrity_sha256",
            semantic_sha256(
                {
                    "config": validated.model_dump(mode="json"),
                    "resolved_sandbox": validated_resolved.model_dump(mode="json"),
                }
            ),
        )
        object.__setattr__(self, "_token", _OWNED_READ_CAPABILITY_TOKEN)

    def is_authentic(self) -> bool:
        return (
            self._token is _OWNED_READ_CAPABILITY_TOKEN
            and self.config.authority.mode is ReadAuthorityMode.OWNED_LOCAL
            and self._integrity_sha256
            == semantic_sha256(
                {
                    "config": self.config.model_dump(mode="json"),
                    "resolved_sandbox": self.resolved_sandbox.model_dump(mode="json"),
                }
            )
        )


def _issue_owned_read_capability(
    *,
    environment: object,
    bundle: object,
    admitted_resolved_sha256: str,
    timeout_seconds: float,
) -> _OwnedReadCapability:
    """Issue only after a fresh exact Sandbox lifecycle re-authentication."""

    from ecomsre_live_sandbox.contracts import ConfigBundle, ResolvedSandbox
    from ecomsre_live_sandbox.environment import SandboxEnvironment
    from ecomsre_live_sandbox.product_v024 import ProductV024SandboxEnvironment

    if (
        type(environment) not in {SandboxEnvironment, ProductV024SandboxEnvironment}
        or type(bundle) is not ConfigBundle
    ):
        raise TypeError("owned read capability requires exact Sandbox lifecycle")
    owned_environment = cast(SandboxEnvironment, environment)
    config_bundle = cast(ConfigBundle, bundle)
    if owned_environment.bundle != config_bundle:
        raise TypeError("owned read capability requires exact Sandbox lifecycle")
    docker = owned_environment.verify_local_docker()
    resolved, raw_compose = owned_environment.resolve()
    if type(resolved) is not ResolvedSandbox or not isinstance(raw_compose, dict):
        raise TypeError("fresh Sandbox resolve returned an invalid boundary type")
    fresh_resolved_sha256 = semantic_sha256(resolved.model_dump(mode="json"))
    if admitted_resolved_sha256 != fresh_resolved_sha256:
        raise ValueError("fresh resolved Sandbox drifted from admission")
    authority = _build_owned_read_authority(
        daemon_identity=docker["daemon_id"],
        docker_context=docker["context"],
        config_bundle_sha256=semantic_sha256(config_bundle.model_dump(mode="json")),
        resolved_sandbox_sha256=fresh_resolved_sha256,
        prometheus_base_url=resolved.endpoints.prometheus,
        opensearch_base_url=resolved.endpoints.opensearch,
        jaeger_base_url=resolved.endpoints.jaeger,
        docker_endpoint=docker["endpoint"],
        compose_project=config_bundle.environment.compose_project,
        sandbox_label_key=config_bundle.environment.sandbox_label_key,
        sandbox_label_value=config_bundle.environment.sandbox_id,
    )
    config = LocalReadBackendConfig(
        prometheus_base_url=resolved.endpoints.prometheus,
        opensearch_base_url=resolved.endpoints.opensearch,
        jaeger_base_url=resolved.endpoints.jaeger,
        opensearch_index="otel-logs-*",
        docker_endpoint=docker["endpoint"],
        compose_project=config_bundle.environment.compose_project,
        sandbox_label_key=config_bundle.environment.sandbox_label_key,
        sandbox_label_value=config_bundle.environment.sandbox_id,
        timeout_seconds=timeout_seconds,
        authority=authority,
    )
    return _OwnedReadCapability(
        _token=_OWNED_READ_CAPABILITY_TOKEN,
        config=config,
        resolved_sandbox=resolved,
    )


class _RejectRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, *args: object, **kwargs: object) -> None:
        del args, kwargs
        return None


class UrllibLoopbackJsonTransport:
    def __init__(
        self,
        *,
        timeout_seconds: float,
        opener: OpenerDirector | None = None,
    ) -> None:
        self.timeout_seconds = timeout_seconds
        self.opener = opener or build_opener(ProxyHandler({}), _RejectRedirectHandler())

    def request_json(
        self,
        *,
        base_url: str,
        path: str,
        method: str,
        payload: object | None,
    ) -> object:
        if method not in {"GET", "POST"} or not path.startswith("/") or "\x00" in path:
            raise ValueError("telemetry request is outside the fixed read-only boundary")
        data = None
        if payload is not None:
            data = json.dumps(
                payload,
                allow_nan=False,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        target = f"{base_url}{path}"
        request = Request(
            target,
            method=method,
            data=data,
            headers={"Accept": "application/json", "Content-Type": "application/json"},
        )
        with self.opener.open(request, timeout=self.timeout_seconds) as response:  # noqa: S310 - proxy/redirect disabled, validated loopback origin
            if response.geturl() != target:
                raise RuntimeError("local telemetry final origin drifted")
            raw = response.read(10_000_001)
            if response.status < 200 or response.status >= 300:
                raise RuntimeError(f"local telemetry returned HTTP {response.status}")
            if len(raw) > 10_000_000:
                raise ValueError("local telemetry response exceeds bounded size")
            return json.loads(raw.decode("utf-8"))


class LocalSandboxReadBackend:
    """One capability-separated backend for the five production read adapters."""

    def __init__(
        self,
        *,
        config: LocalReadBackendConfig,
        http: JsonHttpTransport | None = None,
        docker: DockerJsonClient | None = None,
        sleep: Callable[[float], None] = time.sleep,
        _capability: _OwnedReadCapability | None = None,
    ) -> None:
        self.config = LocalReadBackendConfig.model_validate(config.model_dump())
        self.authority = ReadAuthorityContext.model_validate(
            self.config.authority.model_dump()
        )
        if self.authority.mode is ReadAuthorityMode.OWNED_LOCAL:
            if (
                not isinstance(_capability, _OwnedReadCapability)
                or not _capability.is_authentic()
                or _capability.config != self.config
                or http is not None
                or docker is not None
                or sleep is not time.sleep
            ):
                raise TypeError(
                    "owned production backend requires exact lifecycle capability"
                )
        elif _capability is not None or http is None or docker is None:
            raise TypeError(
                "fake/replay backend requires explicitly injected read transports"
            )
        self.http = http or UrllibLoopbackJsonTransport(
            timeout_seconds=self.config.timeout_seconds
        )
        self.docker = DockerReadAdapter(
            docker=docker
            or UnixSocketDockerClient(
                self.config.docker_endpoint.removeprefix("unix://"),
                timeout_seconds=self.config.timeout_seconds,
            ),
            compose_project=self.config.compose_project,
            sandbox_label_key=self.config.sandbox_label_key,
            sandbox_label_value=self.config.sandbox_label_value,
            sleep=sleep,
        )

    @classmethod
    def _from_owned_capability(
        cls, capability: _OwnedReadCapability
    ) -> LocalSandboxReadBackend:
        if not capability.is_authentic():
            raise TypeError("owned read capability is not authentic")
        return cls(config=capability.config, _capability=capability)

    def execute(self, request: ReadToolRequest) -> BackendResult:
        records: tuple[ToolResultRecord, ...]
        limit: int
        try:
            if isinstance(request, QueryMetricsRequest):
                records = self._query_metrics(request)
                limit = request.max_results
            elif isinstance(request, SearchLogsRequest):
                records = self._search_logs(request)
                limit = request.max_records
            elif isinstance(request, TraceNeighborhoodRequest):
                records = self._query_traces(request)
                limit = request.max_spans
                truncated = len(records) > limit
                selected = tuple(
                    sorted(
                        records[:limit],
                        key=_trace_canonical_key,
                    )
                )
                return BackendResult(records=selected, truncated=truncated)
            elif isinstance(request, InspectServiceRuntimeRequest):
                records = self.docker.inspect_runtime(request)
                limit = request.max_results
            elif isinstance(request, InspectResourceUsageRequest):
                records = self.docker.inspect_resources(request)
                limit = len(request.services)
            else:
                raise TypeError("unsupported DTA v2 read-tool request")
        except ReadBackendFailure:
            raise
        except TimeoutError as error:
            raise ReadBackendFailure(ToolErrorCode.SOURCE_TIMEOUT) from error
        except (OSError, RuntimeError) as error:
            raise ReadBackendFailure(ToolErrorCode.SOURCE_UNAVAILABLE) from error
        except (TypeError, ValueError, KeyError) as error:
            raise ReadBackendFailure(ToolErrorCode.SOURCE_SCHEMA_INVALID) from error
        truncated = len(records) > limit
        return BackendResult(records=records[:limit], truncated=truncated)

    def _query_metrics(
        self, request: QueryMetricsRequest
    ) -> tuple[MetricRecord, ...]:
        window_seconds = max(30, min(3600, round((request.ended_at - request.started_at).total_seconds())))
        output: list[MetricRecord] = []
        for kind in request.metric_kinds:
            query, unit = _prometheus_query(
                kind, service=request.service, window_seconds=window_seconds
            )
            path = "/api/v1/query?" + urlencode(
                {"query": query, "time": f"{request.ended_at.timestamp():.3f}"}
            )
            raw = self.http.request_json(
                base_url=self.config.prometheus_base_url,
                path=path,
                method="GET",
                payload=None,
            )
            value, sample_count = _parse_prometheus_vector(
                raw, expected_service=request.service
            )
            output.append(
                MetricRecord(
                    service=request.service,
                    metric_kind=kind,
                    value=value,
                    unit=unit,
                    sample_count=sample_count,
                )
            )
            if len(output) >= request.max_results:
                break
        return tuple(output)

    def _search_logs(
        self, request: SearchLogsRequest
    ) -> tuple[DiagnosticLogRecord, ...]:
        payload = {
            "size": request.max_records + 1,
            "sort": [{"observedTimestamp": {"order": "asc"}}],
            "query": {
                "bool": {
                    "filter": [
                        {
                            "term": {
                                "resource.service.name.keyword": request.service
                            }
                        },
                        {
                            "range": {
                                "observedTimestamp": {
                                    "gte": request.started_at.isoformat(),
                                    "lte": request.ended_at.isoformat(),
                                }
                            }
                        }
                    ],
                    "must": [
                        {
                            "query_string": {
                                "query": (
                                    "severityText:(WARN OR ERROR OR FATAL) OR "
                                    "body:(error OR exception OR failed OR timeout OR "
                                    "unhealthy OR fatal OR warn)"
                                )
                            }
                        }
                    ],
                }
            },
        }
        raw = self.http.request_json(
            base_url=self.config.opensearch_base_url,
            path=f"/{self.config.opensearch_index}/_search",
            method="POST",
            payload=payload,
        )
        mapping = _mapping(raw, "OpenSearch response")
        hits = _mapping(mapping.get("hits"), "OpenSearch hits")
        raw_hits = hits.get("hits")
        if not isinstance(raw_hits, list):
            raise ValueError("OpenSearch hit list is invalid")
        output: list[DiagnosticLogRecord] = []
        for raw_hit in raw_hits:
            hit = _mapping(raw_hit, "OpenSearch hit")
            source = _mapping(hit.get("_source"), "OpenSearch source")
            service = _nested(
                source,
                "resource.service.name",
                "resource.attributes.service.name",
                "service.name",
            )
            if service != request.service:
                continue
            severity_text = str(
                _nested(source, "severityText", "severity.text") or ""
            ).upper()
            body_raw = _nested(source, "body", "message")
            body = (
                json.dumps(body_raw, ensure_ascii=False, sort_keys=True)
                if isinstance(body_raw, Mapping)
                else str(body_raw or "")
            ).strip()
            diagnostic = bool(
                re.search(
                    r"(?i)\b(?:error|exception|failed|failure|timeout|oom|unhealthy|fatal|warn)\b",
                    body,
                )
            )
            severity = _log_severity(severity_text, diagnostic=diagnostic)
            if severity is None or not body:
                continue
            output.append(
                DiagnosticLogRecord(
                    observed_at=_parse_datetime(
                        _nested(
                            source,
                            "observedTimestamp",
                            "timestamp",
                            "@timestamp",
                            "timeUnixNano",
                        )
                    ),
                    service=request.service,
                    severity=severity,
                    message=_safe_projection_text(body, maximum=500),
                )
            )
            if len(output) >= request.max_records + 1:
                break
        return tuple(
            sorted(
                output,
                key=lambda item: (
                    item.observed_at,
                    item.severity.value,
                    item.message,
                ),
            )
        )

    def _query_traces(
        self, request: TraceNeighborhoodRequest
    ) -> tuple[TraceNeighborhoodRecord, ...]:
        path = "/jaeger/ui/api/traces?" + urlencode(
            {
                "service": request.service,
                "limit": request.max_spans + 1,
                "start": round(request.started_at.timestamp() * 1_000_000),
                "end": round(request.ended_at.timestamp() * 1_000_000),
            }
        )
        raw = self.http.request_json(
            base_url=self.config.jaeger_base_url,
            path=path,
            method="GET",
            payload=None,
        )
        payload = _mapping(raw, "Jaeger response")
        traces = payload.get("data")
        if not isinstance(traces, list):
            raise ValueError("Jaeger trace list is invalid")
        ranked_traces: list[
            tuple[tuple[object, ...], list[TraceNeighborhoodRecord]]
        ] = []
        for raw_trace in traces:
            trace = _mapping(raw_trace, "Jaeger trace")
            processes = _mapping(trace.get("processes", {}), "Jaeger processes")
            spans_raw = trace.get("spans")
            if not isinstance(spans_raw, list):
                continue
            spans: dict[str, Mapping[str, object]] = {}
            services: dict[str, str] = {}
            parents: dict[str, str | None] = {}
            errors: dict[str, bool] = {}
            starts: dict[str, float] = {}
            for raw_span in spans_raw:
                span = _mapping(raw_span, "Jaeger span")
                span_id = span.get("spanID")
                process = processes.get(str(span.get("processID") or ""), {})
                process_map = _mapping(process, "Jaeger process")
                service = str(process_map.get("serviceName") or "")
                if (
                    not isinstance(span_id, str)
                    or not re.fullmatch(r"[0-9a-fA-F]{16}", span_id)
                    or not re.fullmatch(r"[a-z][a-z0-9-]{0,63}", service)
                ):
                    continue
                spans[span_id] = span
                services[span_id] = service
                parents[span_id] = _parent_span_id(span)
                tags = _tags(span.get("tags"))
                errors[span_id] = _span_status(tags) is SpanStatus.ERROR
                starts[span_id] = _finite_number(span.get("startTime", 0), "span start")
            first_error = max(
                (identity for identity, is_error in errors.items() if is_error),
                key=lambda identity: (
                    len(
                        _service_path(
                            identity, services=services, parents=parents
                        )
                    ),
                    -starts[identity],
                ),
                default=None,
            )
            trace_output: list[TraceNeighborhoodRecord] = []
            for span_id, span in spans.items():
                parent_id = parents[span_id]
                parent_service = services.get(parent_id or "")
                tags = _tags(span.get("tags"))
                duration_us = _finite_number(span.get("duration", 0), "span duration")
                trace_output.append(
                    TraceNeighborhoodRecord(
                        anchor_service=request.service,
                        service_path=_service_path(
                            span_id, services=services, parents=parents
                        ),
                        relationship=(
                            SpanRelationship.ROOT
                            if parent_id is None
                            else SpanRelationship.CHILD
                        ),
                        service=services[span_id],
                        parent_service=parent_service,
                        operation=_safe_projection_text(
                            str(span.get("operationName") or "unknown"), maximum=160
                        ),
                        status=_span_status(tags),
                        duration_ms=duration_us / 1000,
                        first_error_location=span_id == first_error,
                    )
                )
            trace_output.sort(
                key=lambda item: (
                    item.service != request.service,
                    not item.first_error_location,
                    item.status is not SpanStatus.ERROR,
                    -len(item.service_path),
                    _trace_canonical_key(item),
                )
            )
            ranked_traces.append(
                (
                    (
                        not any(
                            item.service == request.service
                            and item.status is SpanStatus.ERROR
                            and item.first_error_location
                            for item in trace_output
                        ),
                        not any(
                            item.service == request.service
                            and item.status is SpanStatus.ERROR
                            for item in trace_output
                        ),
                        not any(
                            item.status is SpanStatus.ERROR
                            for item in trace_output
                        ),
                        -max(starts.values(), default=0.0),
                        tuple(_trace_canonical_key(item) for item in trace_output),
                    ),
                    trace_output,
                )
            )
        output: list[TraceNeighborhoodRecord] = []
        for _, trace_output in sorted(ranked_traces, key=lambda item: item[0]):
            for record in trace_output:
                output.append(record)
                if len(output) >= request.max_spans + 1:
                    return tuple(output)
        return tuple(output)


def _trace_canonical_key(item: TraceNeighborhoodRecord) -> tuple[object, ...]:
    return (
        item.service_path,
        item.service,
        item.relationship.value,
        item.parent_service or "",
        item.operation,
        item.status.value,
        item.duration_ms,
        item.first_error_location,
    )


def _prometheus_query(
    kind: MetricKind, *, service: str, window_seconds: int
) -> tuple[str, MetricUnit]:
    selector = f'service_name="{service}"'
    window = f"{window_seconds}s"
    queries = {
        MetricKind.ERROR_RATE: (
            "sum(rate(traces_span_metrics_calls_total{"
            f'{selector},status_code="STATUS_CODE_ERROR"}}[{window}])) / '
            "clamp_min(sum(rate(traces_span_metrics_calls_total{"
            f"{selector}}}[{window}])), 1)",
            MetricUnit.RATIO,
        ),
        MetricKind.LATENCY_P95_MS: (
            "histogram_quantile(0.95, sum(rate("
            f"traces_span_metrics_duration_milliseconds_bucket{{{selector}}}[{window}])) by (le))",
            MetricUnit.MILLISECONDS,
        ),
        MetricKind.REQUEST_SUPPORT: (
            f"sum(increase(traces_span_metrics_calls_total{{{selector}}}[{window}]))",
            MetricUnit.COUNT,
        ),
        MetricKind.CPU_PERCENT: (
            f"sum(rate(process_cpu_time_seconds_total{{{selector}}}[{window}])) * 100",
            MetricUnit.PERCENT,
        ),
        MetricKind.MEMORY_BYTES: (
            f"sum(process_memory_usage_bytes{{{selector}}})",
            MetricUnit.BYTES,
        ),
        MetricKind.QUEUE_RESOURCE: (
            f"sum(rate(messaging_process_duration_milliseconds_count{{{selector}}}[{window}]))",
            MetricUnit.SCALAR,
        ),
    }
    return queries[kind]


def _parse_prometheus_vector(
    value: object, *, expected_service: str
) -> tuple[float, int]:
    payload = _mapping(value, "Prometheus response")
    if payload.get("status") != "success":
        raise ValueError("Prometheus query did not succeed")
    data = _mapping(payload.get("data"), "Prometheus data")
    results = data.get("result")
    if data.get("resultType") != "vector" or not isinstance(results, list):
        raise ValueError("Prometheus response is not an instant vector")
    total = 0.0
    count = 0
    for raw_result in results:
        item = _mapping(raw_result, "Prometheus vector item")
        labels = _mapping(item.get("metric", {}), "Prometheus labels")
        service = labels.get("service_name")
        if service is not None and service != expected_service:
            continue
        sample = item.get("value")
        if not isinstance(sample, list) or len(sample) != 2:
            raise ValueError("Prometheus sample is invalid")
        number = float(sample[1])
        if math.isnan(number):
            continue
        if not math.isfinite(number):
            raise ValueError("Prometheus sample is not finite")
        total += number
        count += 1
    return total, count


def _mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an object")
    return cast(Mapping[str, object], value)


def _nested(value: Mapping[str, object], *paths: str) -> object | None:
    for path in paths:
        current: object = value
        for part in path.split("."):
            if not isinstance(current, Mapping) or part not in current:
                current = None
                break
            current = current[part]
        if current is not None:
            return current
    return None


def _parse_datetime(value: object) -> datetime:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
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


def _log_severity(value: str, *, diagnostic: bool) -> LogSeverity | None:
    if "FATAL" in value:
        return LogSeverity.FATAL
    if "ERROR" in value:
        return LogSeverity.ERROR
    if "WARN" in value:
        return LogSeverity.WARN
    if diagnostic:
        return LogSeverity.DIAGNOSTIC
    return None


_HEX_IDENTITY = re.compile(r"(?<![0-9a-f])[0-9a-f]{16,64}(?![0-9a-f])", re.I)
_PROJECTION_TRUTH = re.compile(
    r"(?i)(?:paymentfailure\.defaultvariant|emailmemoryleak|defaultvariant|"
    r"injected[ _-]?variant|expected[ _-]?(?:root|mechanism|runbook)|"
    r"scenario[ _-]?controller|ground[ _-]?truth|gold[ _-]?label|100%)"
)


def _safe_projection_text(value: str, *, maximum: int) -> str:
    text = " ".join(value.strip().split())
    text = _HEX_IDENTITY.sub("[redacted-identity]", text)
    text = _PROJECTION_TRUTH.sub("[redacted-diagnostic]", text)
    return (text[:maximum] or "diagnostic record")


def _tags(value: object) -> Mapping[str, object]:
    if not isinstance(value, list):
        return {}
    return {
        str(item["key"]): item.get("value")
        for item in value
        if isinstance(item, Mapping) and isinstance(item.get("key"), str)
    }


def _span_status(tags: Mapping[str, object]) -> SpanStatus:
    value = str(
        tags.get("otel.status_code") or tags.get("error") or "UNSET"
    ).upper()
    if value in {"ERROR", "TRUE", "2"}:
        return SpanStatus.ERROR
    if value in {"OK", "1"}:
        return SpanStatus.OK
    return SpanStatus.UNSET


def _parent_span_id(span: Mapping[str, object]) -> str | None:
    direct = span.get("parentSpanID")
    if isinstance(direct, str) and re.fullmatch(r"[0-9a-fA-F]{16}", direct):
        return direct
    references = span.get("references")
    if isinstance(references, list):
        for item in references:
            if (
                isinstance(item, Mapping)
                and item.get("refType") == "CHILD_OF"
                and isinstance(item.get("spanID"), str)
                and re.fullmatch(r"[0-9a-fA-F]{16}", str(item["spanID"]))
            ):
                return str(item["spanID"])
    return None


def _service_path(
    span_id: str,
    *,
    services: Mapping[str, str],
    parents: Mapping[str, str | None],
) -> tuple[str, ...]:
    path: list[str] = []
    seen: set[str] = set()
    current: str | None = span_id
    while current is not None and current in services and current not in seen:
        seen.add(current)
        path.append(services[current])
        current = parents.get(current)
    path.reverse()
    return tuple(path[-12:])


def _finite_number(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise ValueError(f"{label} is invalid")
    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise ValueError(f"{label} is invalid")
    return number
