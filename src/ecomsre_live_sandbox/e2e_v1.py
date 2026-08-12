"""The one-shot successor from a controlled local fault to A0 remediation."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import time
from typing import Literal, Sequence, cast
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from ecomsre.model.gateway import OpenAICompatibleConfig
from ecomsre_live_sandbox.contracts import (
    ApprovalRequest,
    DiagnosisResult,
    HumanApprovalRecord,
    SLIWindow,
    canonical_json_bytes,
    canonical_sha256,
    file_sha256,
    write_private_json,
)
from ecomsre_live_sandbox.control import (
    ForwardMutationCounter,
    IndependentVerifier,
    LocalSandboxRestrictedExecutor,
    SandboxFaultController,
    build_flag_documents,
    build_plan,
    compensate_rollback,
    evaluate_diagnosis_gate,
    evaluate_policy,
    fault_impact_passed,
)
from ecomsre_live_sandbox.e2e_contracts import (
    E2EConfig,
    E2EPrivateRoots,
    create_approval_request,
)
from ecomsre_live_sandbox.e2e_telemetry import (
    LiveLogObservation,
    LiveMetricObservation,
    LiveTraceObservation,
    build_live_a0_context,
    select_trace_candidate_services,
)
from ecomsre_live_sandbox.environment import SandboxEnvironment
from ecomsre_live_sandbox.instrumentation_v2 import (
    EvidenceResolver,
    LogsSourceProbe,
    MetricsSourceProbe,
    PrivateArtifactStore,
    SourceProbe,
    SourceProbeResult,
    SourceProbeStatus,
    TracesSourceProbe,
    _revalidate_refs,
    load_instrumentation_config,
    terminalize_source_probes,
)
from ecomsre_rca100.prompt import OpenAICompatibleRCA100Provider
from ecomsre_rca100.projection import RCA100AgentContext
from ecomsre_rca_unified.adapters import classify_fault_ontology
from ecomsre_rca_unified.contracts import (
    CanonicalEntityLayer,
    EntityHierarchyPath,
    EvidenceVisibilitySummary,
)
from ecomsre_rca_unified.runtime import (
    StrongSingleHierarchicalInput,
    execute_unified_hierarchical_rca,
)


CONFIG_RELATIVE = Path("config/live-fault-a0-controlled-remediation-e2e-v1")
V3_CONFIG_RELATIVE = Path("config/live-telemetry-instrumentation-v3")
_TRACKED_FILES = {
    "authority.json": CONFIG_RELATIVE / "authority.json",
    "projection.json": CONFIG_RELATIVE / "projection.json",
    "reporting.json": CONFIG_RELATIVE / "reporting.json",
    "e2e_contracts.py": Path("src/ecomsre_live_sandbox/e2e_contracts.py"),
    "control.py": Path("src/ecomsre_live_sandbox/control.py"),
    "e2e_telemetry.py": Path("src/ecomsre_live_sandbox/e2e_telemetry.py"),
    "e2e_v1.py": Path("src/ecomsre_live_sandbox/e2e_v1.py"),
    "e2e_cli.py": Path("scripts/live_sandbox/e2e_v1.py"),
    "test_e2e_projection.py": Path("tests/live_sandbox/test_e2e_projection.py"),
    "test_e2e_v1.py": Path("tests/live_sandbox/test_e2e_v1.py"),
    "test_live_sandbox.py": Path("tests/live_sandbox/test_live_sandbox.py"),
    "v3_environment.json": Path("config/live-telemetry-instrumentation-v3/environment.json"),
    "v3_sources.json": Path("config/live-telemetry-instrumentation-v3/sources.json"),
    "v3_readiness.json": Path("config/live-telemetry-instrumentation-v3/readiness.json"),
    "v3_reporting.json": Path("config/live-telemetry-instrumentation-v3/reporting.json"),
    "v3_source_policy.py": Path("src/ecomsre_live_sandbox/instrumentation_v2.py"),
    "v3_cli.py": Path("scripts/live_sandbox/instrumentation_v3.py"),
    "v1_scenario.json": Path("config/live-telemetry-controlled-remediation-v1/scenario.json"),
    "v1_diagnosis.json": Path("config/live-telemetry-controlled-remediation-v1/diagnosis.json"),
    "v1_policy.json": Path("config/live-telemetry-controlled-remediation-v1/policy.json"),
    "v1_verification.json": Path("config/live-telemetry-controlled-remediation-v1/verification.json"),
    "v1_budget.json": Path("config/live-telemetry-controlled-remediation-v1/budget.json"),
    "v1_sandbox.json": Path("config/live-telemetry-controlled-remediation-v1/sandbox.json"),
    "v1_compose.sandbox.yaml": Path("config/live-telemetry-controlled-remediation-v1/compose.sandbox.yaml"),
    "v1_otelcol-sandbox.yml": Path("config/live-telemetry-controlled-remediation-v1/otelcol-sandbox.yml"),
    "a0_prompt.py": Path("src/ecomsre_rca100/prompt.py"),
    "unified_runtime.py": Path("src/ecomsre_rca_unified/runtime.py"),
}


def _legal_terminal_verdicts(config: E2EConfig) -> frozenset[str]:
    return frozenset(
        {
            config.authority.invocation_a_terminal,
            config.authority.invocation_b_success,
            "BLOCKED_FAULT_IMPACT_NOT_OBSERVED",
            "BLOCKED_LIVE_TELEMETRY_SOURCE_UNAVAILABLE",
            "BLOCKED_BOUNDED_MULTISERVICE_PROJECTION_UNAVAILABLE",
            "LIVE_DIAGNOSIS_GATE_NOT_PASSED_NO_REMEDIATION",
            "BLOCKED_POLICY_REJECTED",
            "CONTROLLED_REMEDIATION_NOT_VERIFIED_ROLLBACK_COMPLETED",
            "BLOCKED_PR31_STATE_DRIFT",
            "BLOCKED_MAIN_DRIFT_REQUIRES_REVIEW",
            "BLOCKED_V3_AUTHORITY_DRIFT",
            "BLOCKED_PINNED_SOURCE_DRIFT",
            "BLOCKED_DOCKER_CONTEXT_NOT_LOCAL",
            "BLOCKED_E2E_PREFLIGHT",
            "BLOCKED_PROVIDER_PREFLIGHT",
            "BLOCKED_MODEL_CONTEXT_LEAKAGE",
            "BLOCKED_LIVE_RUN_ALREADY_CONSUMED",
            "BLOCKED_ROLLBACK_FAILED_MANUAL_CLEANUP_REQUIRED",
            "BLOCKED_CLEANUP_INCOMPLETE",
            "BLOCKED",
        }
    )


def _git(repository_root: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ("git", *arguments),
        cwd=repository_root,
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"Git boundary command failed: {' '.join(arguments)}")
    return completed.stdout.strip()


def scenario_lock_manifest(
    config: E2EConfig, *, resolved_compose_sha256: str | None = None
) -> dict[str, object]:
    repository_root = config.repository_root
    plan_template = build_plan_template(config)
    return {
        "schema_version": "live-e2e.scenario-lock.v1",
        "version": config.authority.version,
        "implementation_commit": _git(repository_root, "rev-parse", "HEAD"),
        "implementation_branch": _git(repository_root, "branch", "--show-current"),
        "predecessor_head": config.authority.predecessor_head,
        "source_v3_semantic_sha256": config.authority.predecessor_v3_semantic_sha256,
        "source_v3_tracked_sha256": config.authority.predecessor_v3_tracked_sha256,
        "scenario_id": config.sandbox.scenario.scenario_id,
        "baseline_sha256": config.sandbox.scenario.baseline_document_sha256,
        "fault_sha256": config.sandbox.scenario.fault_document_sha256,
        "a0_prompt_sha256": config.authority.a0_prompt_sha256,
        "a0_output_schema_sha256": config.authority.a0_output_schema_sha256,
        "a0_model": config.authority.a0_model,
        "upstream_commit": config.sandbox.environment.upstream_commit,
        "upstream_tag": config.sandbox.environment.upstream_tag,
        "projection_sha256": file_sha256(repository_root / CONFIG_RELATIVE / "projection.json"),
        "plan_template_sha256": canonical_sha256(plan_template),
        "resolved_compose_sha256": resolved_compose_sha256 or "UNRESOLVED_FOR_UNIT_TEST",
        "private_root_lifecycle": {
            "schema_version": "live-e2e.private-root-lifecycle.v1",
            "version": config.authority.version,
            "branch": config.authority.branch,
            "starting_pr": config.authority.predecessor_pr,
            "starting_result_head": config.authority.predecessor_head,
        },
        "sli_thresholds": {
            "minimum_stabilization_seconds": config.sandbox.verification.minimum_stabilization_seconds,
            "fault_error_rate_absolute_increase": config.sandbox.verification.fault_error_rate_absolute_increase,
            "fault_error_rate_multiplier": config.sandbox.verification.fault_error_rate_multiplier,
            "recovery_error_rate_absolute_increase": config.sandbox.verification.recovery_error_rate_absolute_increase,
            "recovery_error_rate_multiplier": config.sandbox.verification.recovery_error_rate_multiplier,
            "consecutive_windows": config.sandbox.verification.consecutive_windows,
        },
        "provider_budget": config.sandbox.budget.model_dump(mode="json"),
        "tracked_files": {
            name: file_sha256(repository_root / relative)
            for name, relative in _TRACKED_FILES.items()
        },
    }


def _verify_scenario_lock(
    config: E2EConfig,
    roots: E2EPrivateRoots,
    *,
    resolved_compose_sha256: str | None = None,
) -> Mapping[str, object]:
    path = roots.control / "scenario-lock.json"
    if path.is_symlink() or not path.is_file():
        raise RuntimeError("frozen E2E scenario lock is unavailable")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise RuntimeError("frozen E2E scenario lock is malformed")
    if value.get("implementation_branch") != config.authority.branch:
        raise RuntimeError("E2E scenario lock branch differs")
    locked_compose_sha256 = resolved_compose_sha256
    if locked_compose_sha256 is None:
        existing_compose_sha256 = value.get("resolved_compose_sha256")
        if not isinstance(existing_compose_sha256, str) or len(existing_compose_sha256) != 64:
            raise RuntimeError("frozen E2E scenario lock lacks a resolved Compose hash")
        locked_compose_sha256 = existing_compose_sha256
    expected = scenario_lock_manifest(config, resolved_compose_sha256=locked_compose_sha256)
    for key in (
        "version",
        "predecessor_head",
        "source_v3_semantic_sha256",
        "source_v3_tracked_sha256",
        "scenario_id",
        "baseline_sha256",
        "fault_sha256",
        "a0_prompt_sha256",
        "a0_output_schema_sha256",
        "a0_model",
        "upstream_commit",
        "upstream_tag",
        "projection_sha256",
        "plan_template_sha256",
        "resolved_compose_sha256",
        "private_root_lifecycle",
        "sli_thresholds",
        "provider_budget",
        "tracked_files",
    ):
        if value.get(key) != expected.get(key):
            raise RuntimeError(f"frozen E2E scenario lock drifted: {key}")
    commit = value.get("implementation_commit")
    if not isinstance(commit, str) or len(commit) != 40:
        raise RuntimeError("frozen E2E implementation commit is invalid")
    if commit != _git(config.repository_root, "rev-parse", "HEAD"):
        raise RuntimeError("frozen E2E implementation commit differs from HEAD")
    if _git(config.repository_root, "branch", "--show-current") != config.authority.branch:
        raise RuntimeError("frozen E2E implementation branch differs")
    if _git(config.repository_root, "status", "--porcelain=v1"):
        raise RuntimeError("frozen E2E implementation worktree is not clean")
    return value


def _strict_json(url: str, *, method: str = "GET", payload: object | None = None) -> object:
    data = None if payload is None else canonical_json_bytes(payload).rstrip(b"\n")
    request = Request(
        url,
        data=data,
        method=method,
        headers={"Accept": "application/json", "Content-Type": "application/json"},
    )
    with urlopen(request, timeout=15) as response:  # noqa: S310 - frozen loopback endpoints
        if response.status < 200 or response.status >= 300:
            raise ConnectionError("loopback telemetry backend returned a non-success status")
        raw = response.read(5_000_001)
    if len(raw) > 5_000_000:
        raise ValueError("loopback telemetry response exceeds the private bound")
    return json.loads(raw.decode("utf-8"))


def _prometheus_value(endpoint: str, query: str, *, at: datetime) -> float:
    payload = _strict_json(
        f"{endpoint}/api/v1/query?{urlencode({'query': query, 'time': f'{at.timestamp():.6f}'})}"
    )
    if not isinstance(payload, Mapping):
        raise ValueError("Prometheus instant payload is malformed")
    data = payload.get("data")
    result = data.get("result") if isinstance(data, Mapping) else None
    if payload.get("status") != "success" or not isinstance(result, list) or len(result) != 1:
        raise RuntimeError("Prometheus instant query does not have one vector value")
    item = result[0]
    sample = item.get("value") if isinstance(item, Mapping) else None
    if not isinstance(sample, list) or len(sample) != 2:
        raise ValueError("Prometheus instant sample is malformed")
    value = float(sample[1])
    if not value >= 0 or not value < float("inf"):
        raise ValueError("Prometheus instant value is invalid")
    return value


def _capture_sli_window(
    config: E2EConfig,
    endpoint: str,
    *,
    phase: Literal["PREFLIGHT", "BASELINE", "FAULT", "RECOVERY"],
) -> SLIWindow:
    started = datetime.now(timezone.utc)
    time.sleep(config.sandbox.scenario.verification_window_seconds)
    ended = datetime.now(timezone.utc)
    telemetry = config.sandbox.telemetry.prometheus
    total = _prometheus_value(endpoint, telemetry.total_query, at=ended)
    errors = _prometheus_value(endpoint, telemetry.error_query, at=ended)
    p95 = _prometheus_value(endpoint, telemetry.p95_query, at=ended)
    health = _prometheus_value(endpoint, telemetry.health_query, at=ended)
    if errors > total + 1e-9:
        raise ValueError("Prometheus error count exceeds request count")
    return SLIWindow(
        phase=phase,
        started_at=started,
        ended_at=ended,
        request_count=total,
        error_count=errors,
        error_rate=0.0 if total == 0 else errors / total,
        p95_latency_ms=p95,
        runtime_health=health,
        sample_count=3,
    )


def _vector_by_service(endpoint: str, query: str, *, at: datetime) -> dict[str, float]:
    payload = _strict_json(
        f"{endpoint}/api/v1/query?{urlencode({'query': query, 'time': f'{at.timestamp():.6f}'})}"
    )
    data = payload.get("data") if isinstance(payload, Mapping) else None
    result = data.get("result") if isinstance(data, Mapping) else None
    if not isinstance(result, list):
        raise ValueError("Prometheus service vector is malformed")
    output: dict[str, float] = {}
    for item in result:
        labels = item.get("metric") if isinstance(item, Mapping) else None
        sample = item.get("value") if isinstance(item, Mapping) else None
        service = labels.get("service_name") if isinstance(labels, Mapping) else None
        if not isinstance(service, str) or not service or not isinstance(sample, list) or len(sample) != 2:
            continue
        value = float(sample[1])
        if math.isfinite(value) and value >= 0:
            output[service] = value
    return output


def _broad_metric_snapshot(endpoint: str, *, at: datetime) -> dict[str, tuple[float, float, float]]:
    total = _vector_by_service(
        endpoint,
        "sum by (service_name) (increase(traces_span_metrics_calls_total[30s]))",
        at=at,
    )
    errors = _vector_by_service(
        endpoint,
        'sum by (service_name) (increase(traces_span_metrics_calls_total{status_code="STATUS_CODE_ERROR"}[30s]))',
        at=at,
    )
    p95 = _vector_by_service(
        endpoint,
        "histogram_quantile(0.95, sum by (le, service_name) (rate(traces_span_metrics_duration_milliseconds_bucket[30s])))",
        at=at,
    )
    return {
        service: (count, errors.get(service, 0.0), p95.get(service, 0.0))
        for service, count in total.items()
        if count > 0
    }


def _nested(value: Mapping[str, object], path: str) -> object | None:
    direct = value.get(path)
    if direct is not None:
        return direct
    current: object = value
    for part in path.split("."):
        if not isinstance(current, Mapping):
            return None
        current = current.get(part)
    return current


def _compatible_field(
    fields: Mapping[str, object],
    candidates: Sequence[str],
    compatible_types: frozenset[str],
) -> str | None:
    for candidate in candidates:
        declared = fields.get(candidate)
        if not isinstance(declared, Mapping):
            continue
        for fallback_type, descriptor in declared.items():
            if not isinstance(descriptor, Mapping):
                continue
            field_type = descriptor.get("type", fallback_type)
            if (
                isinstance(field_type, str)
                and field_type in compatible_types
                and descriptor.get("searchable") is not False
            ):
                return candidate
    return None


def _capture_broad_logs(
    endpoint: str,
    *,
    window_start: datetime,
    window_end: datetime,
    maximum_hits: int,
) -> tuple[LiveLogObservation, ...]:
    time_field = "observedTimestamp"
    service_field = "resource.service.name.keyword"
    severity_candidates = (
        "severityText",
        "severityText.keyword",
        "severity.text",
        "severity.text.keyword",
    )
    body_candidates = ("body", "body.keyword", "message")
    field_allowlist = sorted(
        {time_field, service_field, *severity_candidates, *body_candidates}
    )
    field_parameters = urlencode({"fields": ",".join(field_allowlist)})
    field_caps = _strict_json(
        f"{endpoint}/otel-logs-*/_field_caps?{field_parameters}"
    )
    fields = field_caps.get("fields") if isinstance(field_caps, Mapping) else None
    if not isinstance(fields, Mapping):
        raise RuntimeError("OpenSearch field caps are unavailable")
    if time_field not in fields or service_field not in fields:
        raise RuntimeError("OpenSearch frozen observed time or service field is unavailable")
    severity_keyword_field = _compatible_field(
        fields,
        ("severityText", "severityText.keyword", "severity.text.keyword"),
        frozenset({"keyword", "constant_keyword"}),
    )
    severity_text_field = _compatible_field(
        fields,
        ("severityText", "severity.text"),
        frozenset({"text", "match_only_text"}),
    )
    severity_field = severity_keyword_field or severity_text_field
    body_field = _compatible_field(
        fields,
        body_candidates,
        frozenset({"text", "match_only_text", "keyword", "constant_keyword"}),
    )
    if severity_field is None and body_field is None:
        raise RuntimeError("OpenSearch lacks severity and error-like body fields")
    anomaly_should: list[dict[str, object]] = []
    if severity_keyword_field is not None:
        anomaly_should.append({"terms": {severity_field: ["WARN", "WARNING", "ERROR", "FATAL"]}})
    elif severity_text_field is not None:
        anomaly_should.append(
            {
                "match": {
                    severity_text_field: {
                        "query": "warn warning error fatal",
                        "operator": "or",
                    }
                }
            }
        )
    if body_field is not None:
        anomaly_should.append({"match": {body_field: {"query": "error", "operator": "and"}}})
    query = {
        "size": maximum_hits,
        "sort": [{time_field: {"order": "asc"}}],
        "query": {
            "bool": {
                "filter": [{"range": {time_field: {"gte": window_start.isoformat(), "lte": window_end.isoformat()}}}],
                "should": anomaly_should,
                "minimum_should_match": 1,
            }
        },
    }
    payload = _strict_json(f"{endpoint}/otel-logs-*/_search", method="POST", payload=query)
    hits = payload.get("hits") if isinstance(payload, Mapping) else None
    raw_hits = hits.get("hits") if isinstance(hits, Mapping) else None
    if not isinstance(raw_hits, list):
        raise RuntimeError("OpenSearch live search is malformed")
    output: list[LiveLogObservation] = []
    for item in raw_hits[:maximum_hits]:
        source = item.get("_source") if isinstance(item, Mapping) else None
        if not isinstance(source, Mapping):
            continue
        raw_time = _nested(source, time_field)
        raw_service = _nested(source, service_field.removesuffix(".keyword"))
        if not isinstance(raw_service, str) or not raw_service:
            continue
        try:
            if isinstance(raw_time, (int, float)):
                scale = 1_000_000_000 if raw_time > 1e17 else 1_000_000 if raw_time > 1e14 else 1_000 if raw_time > 1e11 else 1
                observed = datetime.fromtimestamp(float(raw_time) / scale, tz=timezone.utc)
            elif isinstance(raw_time, str):
                observed = datetime.fromisoformat(raw_time.replace("Z", "+00:00"))
            else:
                continue
        except (OverflowError, ValueError):
            continue
        severity = (
            _nested(source, severity_field.removesuffix(".keyword"))
            if severity_field
            else "UNKNOWN"
        )
        body = (
            _nested(source, body_field.removesuffix(".keyword"))
            if body_field
            else "observed log event"
        )
        if not isinstance(severity, str):
            continue
        if isinstance(body, Mapping):
            body = json.dumps(body, ensure_ascii=False, sort_keys=True)
        if not isinstance(body, str):
            body = "observed log event"
        sanitized = "".join(character for character in body if character >= " " or character in "\n\t")[:2_000]
        if severity.upper() not in {"WARN", "WARNING", "ERROR", "FATAL"} and "error" not in sanitized.casefold():
            continue
        output.append(
            LiveLogObservation(
                observed_at=observed,
                service_name=raw_service,
                severity=severity[:64],
                body=sanitized or "observed log event",
            )
        )
    return tuple(output)


def _trace_status(tags: object) -> str:
    if not isinstance(tags, list):
        return "UNSET"
    values = {str(item.get("key")): item.get("value") for item in tags if isinstance(item, Mapping)}
    raw = str(values.get("otel.status_code") or values.get("error") or "UNSET").upper()
    return "ERROR" if raw in {"ERROR", "TRUE", "2"} else "OK" if raw in {"OK", "1"} else "UNSET"


def _capture_broad_traces(
    endpoint: str,
    *,
    services: tuple[str, ...],
    window_start: datetime,
    window_end: datetime,
    maximum_queries: int,
    maximum_evidence: int,
) -> tuple[LiveTraceObservation, ...]:
    if len(services) > maximum_queries:
        raise ValueError("live trace query budget exceeded")
    output: dict[tuple[str, str], LiveTraceObservation] = {}
    for service in services:
        parameters = urlencode(
            {
                "service": service,
                "start": int(window_start.timestamp() * 1_000_000),
                "end": int(window_end.timestamp() * 1_000_000),
                "limit": maximum_evidence,
            }
        )
        payload = _strict_json(f"{endpoint}/jaeger/ui/api/traces?{parameters}")
        traces = payload.get("data") if isinstance(payload, Mapping) else None
        if not isinstance(traces, list):
            raise RuntimeError("Jaeger live trace response is malformed")
        for trace in traces:
            processes = trace.get("processes") if isinstance(trace, Mapping) else None
            spans = trace.get("spans") if isinstance(trace, Mapping) else None
            if not isinstance(processes, Mapping) or not isinstance(spans, list):
                continue
            service_by_span: dict[str, str] = {}
            trace_rows: dict[str, LiveTraceObservation] = {}
            child_spans: dict[str, set[str]] = {}
            for span in spans:
                process_id = span.get("processID") if isinstance(span, Mapping) else None
                process = processes.get(process_id) if isinstance(process_id, str) else None
                span_id = span.get("spanID") if isinstance(span, Mapping) else None
                service_name = process.get("serviceName") if isinstance(process, Mapping) else None
                if isinstance(span_id, str) and isinstance(service_name, str) and service_name:
                    service_by_span[span_id.casefold()] = service_name
            for span in spans:
                process_id = span.get("processID") if isinstance(span, Mapping) else None
                process = processes.get(process_id) if isinstance(process_id, str) else None
                if not isinstance(process, Mapping):
                    continue
                started = span.get("startTime") if isinstance(span, Mapping) else None
                duration = span.get("duration") if isinstance(span, Mapping) else None
                operation = span.get("operationName") if isinstance(span, Mapping) else None
                service_name = process.get("serviceName")
                trace_id = span.get("traceID") or (trace.get("traceID") if isinstance(trace, Mapping) else None)
                span_id = span.get("spanID") if isinstance(span, Mapping) else None
                parent_span_id = span.get("parentSpanID") if isinstance(span, Mapping) else None
                if (
                    not isinstance(started, (int, float))
                    or not isinstance(duration, (int, float))
                    or not isinstance(operation, str)
                    or not isinstance(service_name, str)
                    or not isinstance(trace_id, str)
                    or not isinstance(span_id, str)
                ):
                    continue
                normalized_span = span_id.casefold()
                normalized_parent = parent_span_id.casefold() if isinstance(parent_span_id, str) else None
                if normalized_parent is not None:
                    child_spans.setdefault(normalized_parent, set()).add(normalized_span)
                trace_rows[normalized_span] = LiveTraceObservation(
                        observed_at=datetime.fromtimestamp(started / 1_000_000, tz=timezone.utc),
                        service_name=service_name,
                        operation=operation[:512],
                        status=_trace_status(span.get("tags")),
                        duration_ms=duration / 1_000.0,
                        parent_service_name=(
                            service_by_span.get(parent_span_id.casefold())
                            if isinstance(parent_span_id, str)
                            else None
                        ),
                        trace_token=trace_id.casefold(),
                        span_token=normalized_span,
                        parent_span_token=normalized_parent,
                )
            frontier = {
                span_id
                for span_id, item in trace_rows.items()
                if item.service_name == service or item.status.upper() == "ERROR"
            }
            retained = set(frontier)
            for _ in range(2):
                next_frontier: set[str] = set()
                for span_id in frontier:
                    item = trace_rows[span_id]
                    if item.parent_span_token in trace_rows:
                        next_frontier.add(item.parent_span_token)
                    next_frontier.update(child_spans.get(span_id, set()))
                next_frontier.difference_update(retained)
                retained.update(next_frontier)
                frontier = next_frontier
            for span_id in retained:
                item = trace_rows[span_id]
                if item.trace_token is not None:
                    output[(item.trace_token, span_id)] = item
    return tuple(
        sorted(output.values(), key=lambda item: (item.observed_at, item.service_name, item.operation))[
            : maximum_evidence
        ]
    )


def _make_controller(
    config: E2EConfig,
    roots: E2EPrivateRoots,
    endpoints: object,
    *,
    flagd_directory: Path,
) -> SandboxFaultController:
    from ecomsre_live_sandbox.contracts import LocalEndpoints

    if not isinstance(endpoints, LocalEndpoints):
        raise ValueError("resolved local endpoints are invalid")
    upstream_path = config.repository_root / "third_party/opentelemetry-demo/src/flagd/demo.flagd.json"
    value = json.loads(upstream_path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise ValueError("frozen upstream flag document is malformed")
    baseline, fault = build_flag_documents(value, config.sandbox)
    flag_file = flagd_directory / "demo.flagd.json"
    write_private_json(flag_file, baseline, create_once=True)
    return SandboxFaultController(
        endpoints=endpoints,
        bundle=config.sandbox,
        flag_file=flag_file,
        baseline_document=baseline,
        fault_document=fault,
    )


def _capture_source_readiness(
    config: E2EConfig,
    roots: E2EPrivateRoots,
    *,
    label: str,
    endpoints: object,
    window_start: datetime | None = None,
    window_end: datetime | None = None,
) -> tuple[SourceProbeResult, SourceProbeResult, SourceProbeResult]:
    from ecomsre_live_sandbox.contracts import LocalEndpoints

    if not isinstance(endpoints, LocalEndpoints):
        raise ValueError("resolved local endpoints are invalid")
    v3 = load_instrumentation_config(config.repository_root / V3_CONFIG_RELATIVE)
    fixed_end = window_end or datetime.now(timezone.utc)
    fixed_start = window_start or datetime.fromtimestamp(
        fixed_end.timestamp() - v3.readiness.capture_window_seconds,
        tz=timezone.utc,
    )
    if fixed_end <= fixed_start:
        raise ValueError("source readiness window is invalid")
    is_v4 = getattr(config.authority, "version", "").endswith("e2e-v4")
    common_root = roots.telemetry / label
    stores = (
        {
            "metrics": PrivateArtifactStore(common_root / "metrics"),
            "logs": PrivateArtifactStore(common_root / "logs"),
            "traces": PrivateArtifactStore(common_root / "traces"),
        }
        if is_v4
        else None
    )
    store = PrivateArtifactStore(common_root) if stores is None else None
    probes = (
        MetricsSourceProbe(
            endpoint=endpoints.prometheus,
            target_service=v3.environment.target_service,
            config=v3.sources.prometheus,
            readiness=v3.readiness,
            store=stores["metrics"] if stores is not None else cast(PrivateArtifactStore, store),
            window_start=fixed_start,
            window_end=fixed_end,
        ),
        LogsSourceProbe(
            endpoint=endpoints.opensearch,
            target_service=v3.environment.target_service,
            config=v3.sources.opensearch,
            readiness=v3.readiness,
            store=stores["logs"] if stores is not None else cast(PrivateArtifactStore, store),
            window_start=fixed_start,
            window_end=fixed_end,
        ),
        TracesSourceProbe(
            endpoint=endpoints.jaeger,
            target_service=v3.environment.target_service,
            config=v3.sources.jaeger,
            readiness=v3.readiness,
            store=stores["traces"] if stores is not None else cast(PrivateArtifactStore, store),
            window_start=fixed_start,
            window_end=fixed_end,
        ),
    )
    raw_results = terminalize_source_probes(
        cast(Sequence[SourceProbe], probes),
        window_start=fixed_start,
        window_end=fixed_end,
    )
    if stores is None:
        shared_store = cast(PrivateArtifactStore, store)
        resolver = EvidenceResolver.from_file(shared_store.seal())
        results, all_refs_resolve = _revalidate_refs(
            raw_results,
            resolver=resolver,
            store_root=shared_store.root,
        )
    else:
        from ecomsre_live_sandbox.e2e_source_batch import _combined_resolver

        resolver = _combined_resolver(stores, common_root=common_root)
        results, all_refs_resolve = _revalidate_refs(
            raw_results,
            resolver=resolver,
            store_root=common_root,
        )
        write_private_json(
            common_root / "source-results.json",
            {
                "schema_version": "live-e2e.source-results.v4",
                "results": [item.model_dump(mode="json") for item in results],
                "all_refs_resolve": all_refs_resolve,
                "invalid_ref_count": sum(item.invalid_ref_count for item in results),
            },
            create_once=True,
        )
    if not all_refs_resolve or any(
        item.status is not SourceProbeStatus.AVAILABLE
        or item.target_record_count <= 0
        or item.invalid_ref_count
        for item in results
    ):
        raise RuntimeError("v3 typed source readiness is not 3/3 AVAILABLE")
    return results


def _capture_e2e_window(
    config: E2EConfig,
    roots: E2EPrivateRoots,
    *,
    label: str,
    endpoints: object,
    phase: Literal["PREFLIGHT", "BASELINE", "FAULT", "RECOVERY"],
) -> tuple[SLIWindow, tuple[SourceProbeResult, SourceProbeResult, SourceProbeResult]]:
    from ecomsre_live_sandbox.contracts import LocalEndpoints

    if not isinstance(endpoints, LocalEndpoints):
        raise ValueError("resolved local endpoints are invalid")
    window = _capture_sli_window(config, endpoints.prometheus, phase=phase)
    time.sleep(15)
    sources = _capture_source_readiness(
        config,
        roots,
        label=label,
        endpoints=endpoints,
        window_start=window.started_at,
        window_end=window.ended_at,
    )
    return window, sources


def _write_model_evidence_index(
    roots: E2EPrivateRoots,
    context: object,
    *,
    raw_observations: Mapping[str, object],
) -> None:
    model = context.model_dump(mode="json") if hasattr(context, "model_dump") else context
    if not isinstance(model, Mapping):
        raise ValueError("A0 context is malformed")
    sources = ("metrics", "logs", "traces")
    records: dict[str, object] = {}
    for source in sources:
        projection = model.get(source)
        evidence = projection.get("evidence") if isinstance(projection, Mapping) else None
        if not isinstance(evidence, list):
            continue
        for item in evidence:
            if not isinstance(item, Mapping) or not isinstance(item.get("evidence_ref"), str):
                raise ValueError("A0 evidence index is malformed")
            reference = item["evidence_ref"]
            raw_source = raw_observations.get(source)
            if not isinstance(raw_source, list) or not raw_source:
                raise ValueError("A0 evidence index source observations are unavailable")
            records[reference] = {
                "source": source.upper(),
                "projection_sha256": canonical_sha256(item),
                "raw_capture_sha256": canonical_sha256(raw_source),
            }
    if not records:
        raise ValueError("A0 evidence index is empty")
    write_private_json(
        roots.telemetry / "model-evidence-index.json",
        {"schema_version": "live-e2e.model-evidence-index.v1", "records": records},
        create_once=True,
    )


def _seal_model_evidence_resolver(
    roots: E2EPrivateRoots,
    *,
    label: str,
    window_start: datetime,
    window_end: datetime,
    metrics: tuple[LiveMetricObservation, ...],
    logs: tuple[LiveLogObservation, ...],
    traces: tuple[LiveTraceObservation, ...],
) -> tuple[
    tuple[LiveMetricObservation, ...],
    tuple[LiveLogObservation, ...],
    tuple[LiveTraceObservation, ...],
    frozenset[str],
]:
    """Seal model aliases in the existing EvidenceResolver format and re-resolve them."""
    store = PrivateArtifactStore(roots.telemetry / label / "model-evidence")
    aliases: dict[str, object] = {}
    pending_aliases: list[tuple[str, str]] = []

    def bind(source: str, values: tuple[object, ...]) -> tuple[str, ...]:
        source_name = cast(Literal["METRICS", "LOGS", "TRACES"], source)
        raw = store.write_raw(
            source_name,
            f"broad-{source.casefold()}",
            [item.model_dump(mode="json") if hasattr(item, "model_dump") else item for item in values],
        )
        prefix = {"METRICS": "metric", "LOGS": "log", "TRACES": "trace"}[source]
        references: list[str] = []
        for index, item in enumerate(values, 1):
            original = store.add_record(
                source=source_name,
                raw_artifact=raw,
                normalized_record=item.model_dump(mode="json") if hasattr(item, "model_dump") else item,
                window_start=window_start,
                window_end=window_end,
                target_service=str(getattr(item, "service_name")),
                ordinal=index,
            )
            alias = f"{prefix}:{index:04d}"
            pending_aliases.append((alias, original))
            references.append(alias)
        return tuple(references)

    metric_refs = bind("METRICS", cast(tuple[object, ...], metrics))
    log_refs = bind("LOGS", cast(tuple[object, ...], logs))
    trace_refs = bind("TRACES", cast(tuple[object, ...], traces))
    raw_resolver = EvidenceResolver.from_file(store.seal())
    for alias, original in pending_aliases:
        aliases[alias] = raw_resolver.resolve(original).model_dump(mode="json")
    resolver_path = store.root / "model-resolver.json"
    write_private_json(
        resolver_path,
        {"schema_version": "live-telemetry.evidence-resolver.v2", "records": aliases},
        create_once=True,
    )
    resolver = EvidenceResolver.from_file(resolver_path)
    for reference, expected_source in (
        *((reference, "METRICS") for reference in metric_refs),
        *((reference, "LOGS") for reference in log_refs),
        *((reference, "TRACES") for reference in trace_refs),
    ):
        metadata = resolver.resolve(reference)
        if metadata.source != expected_source:
            raise RuntimeError("model EvidenceResolver source prefix differs")
        relative = Path(metadata.private_artifact_relative_key)
        artifact = (store.root / relative).resolve()
        if relative.is_absolute() or ".." in relative.parts or not artifact.is_relative_to(store.root.resolve()):
            raise RuntimeError("model EvidenceResolver private path escapes")
        if artifact.is_symlink() or not artifact.is_file() or hashlib.sha256(artifact.read_bytes()).hexdigest() != metadata.raw_artifact_sha256:
            raise RuntimeError("model EvidenceResolver raw artifact hash differs")
    return (
        tuple(item.model_copy(update={"evidence_ref": reference}) for item, reference in zip(metrics, metric_refs, strict=True)),
        tuple(item.model_copy(update={"evidence_ref": reference}) for item, reference in zip(logs, log_refs, strict=True)),
        tuple(item.model_copy(update={"evidence_ref": reference}) for item, reference in zip(traces, trace_refs, strict=True)),
        frozenset(aliases),
    )


def _synthetic_provider_context(config: E2EConfig) -> RCA100AgentContext:
    now = datetime.now(timezone.utc)
    return build_live_a0_context(
        window_start=datetime.fromtimestamp(now.timestamp() - 60, tz=timezone.utc),
        window_end=now,
        metrics=(
            LiveMetricObservation(service_name="checkout", baseline_requests=100, baseline_errors=1, fault_requests=100, fault_errors=25, baseline_p95_ms=20, fault_p95_ms=30, evidence_ref="metric:0001"),
            LiveMetricObservation(service_name="currency", baseline_requests=100, baseline_errors=1, fault_requests=100, fault_errors=12, baseline_p95_ms=20, fault_p95_ms=28, evidence_ref="metric:0002"),
            LiveMetricObservation(service_name="frontend", baseline_requests=100, baseline_errors=1, fault_requests=100, fault_errors=8, baseline_p95_ms=20, fault_p95_ms=26, evidence_ref="metric:0003"),
        ),
        logs=(
            LiveLogObservation(observed_at=now, service_name="checkout", severity="ERROR", body="observed request error", evidence_ref="log:0001"),
            LiveLogObservation(observed_at=now, service_name="currency", severity="ERROR", body="observed request error", evidence_ref="log:0002"),
            LiveLogObservation(observed_at=now, service_name="frontend", severity="WARN", body="observed request error", evidence_ref="log:0003"),
        ),
        traces=(
            LiveTraceObservation(observed_at=now, service_name="checkout", operation="request", status="ERROR", duration_ms=20, evidence_ref="trace:0001"),
            LiveTraceObservation(observed_at=now, service_name="currency", operation="request", status="ERROR", duration_ms=20, evidence_ref="trace:0002"),
            LiveTraceObservation(observed_at=now, service_name="frontend", operation="request", status="ERROR", duration_ms=20, evidence_ref="trace:0003"),
        ),
        resolvable_refs=frozenset({"metric:0001", "metric:0002", "metric:0003", "log:0001", "log:0002", "log:0003", "trace:0001", "trace:0002", "trace:0003"}),
        projection=config.projection,
    )


def _provider(config: E2EConfig) -> OpenAICompatibleRCA100Provider:
    provider_config = OpenAICompatibleConfig.from_environment()
    if provider_config is None:
        raise RuntimeError("Provider configuration is unavailable")
    return OpenAICompatibleRCA100Provider(
        config=provider_config,
        expected_model=config.authority.a0_model,
        timeout_seconds=config.sandbox.budget.provider_timeout_seconds,
        max_completion_tokens=config.sandbox.diagnosis.max_completion_tokens,
    )


def _diagnosis_from_initial(
    provider: OpenAICompatibleRCA100Provider, context: RCA100AgentContext
) -> DiagnosisResult:
    initial = provider.diagnose(context)
    if not initial.root_cause_entity_ref.startswith("apm|apm.service|"):
        raise ValueError("A0 root must be one visible service entity")
    evidence_sources = tuple(sorted({"METRICS" if item.startswith("metric:") else "LOGS" if item.startswith("log:") else "TRACES" for item in initial.evidence_refs}))
    visibility = EvidenceVisibilitySummary(
        catalog_entities=frozenset(item.entity_ref for item in context.visible_entities),
        metrics_entities=frozenset(item.entity_ref for item in context.metrics.evidence),
        logs_entities=frozenset(item.entity_ref for item in context.logs.evidence),
        traces_entities=frozenset(item.entity_ref for item in context.traces.evidence),
        events_entities=frozenset(),
        alerts_entities=frozenset(
            {context.task.alert_entity_ref}
            if context.task.alert_entity_ref is not None
            else set()
        ),
        topology_entities=frozenset(),
    )
    ontology = classify_fault_ontology(initial.fault_type)
    strong = execute_unified_hierarchical_rca(
        StrongSingleHierarchicalInput(
            initial_root=initial.root_cause_entity_ref,
            initial_layer=CanonicalEntityLayer.SERVICE,
            initial_hierarchy_path=EntityHierarchyPath(
                entity=initial.root_cause_entity_ref,
                explicit_parents=(),
                service_ancestor_or_none=initial.root_cause_entity_ref,
                infrastructure_ancestor_or_none=None,
            ),
            fault_type_raw=initial.fault_type,
            fault_ontology_class=ontology,
            evidence_visibility=visibility,
            supporting_evidence_refs=initial.evidence_refs,
        )
    )
    return DiagnosisResult(
        terminal="COMPLETED",
        root_service=strong.final_root.rsplit("|", 1)[1],
        root_entity_ref=strong.final_root,
        fault_type_raw=strong.fault_type_raw,
        fault_class=strong.fault_ontology_class.value,
        confidence=initial.confidence,
        evidence_refs=initial.evidence_refs,
        evidence_source_types=evidence_sources,  # type: ignore[arg-type]
        summary=initial.summary,
        semantic_model_calls=1,
        specialist_calls=0,
        fusion_calls=0,
        provider_attempts=1,
        transport_retries=0,
        usage_tokens=provider.last_usage_tokens,
    )


def _safe_source_counts(results: tuple[SourceProbeResult, SourceProbeResult, SourceProbeResult]) -> dict[str, int]:
    return {item.source: item.target_record_count for item in results}


def _terminal_cleanup_truth(terminal: Mapping[str, object]) -> dict[str, object]:
    cleanup = terminal.get("cleanup")
    if isinstance(cleanup, Mapping):
        return dict(cleanup)
    return {
        "baseline_restored": terminal.get("cleanup_verdict") == "NOT_REQUIRED",
        "owned_containers": 0,
        "owned_networks": 0,
        "owned_volumes": 0,
        "non_owned_resources_changed": False,
        "verdict": terminal.get("cleanup_verdict"),
    }


def _write_private_final_report(
    roots: E2EPrivateRoots, *, invocation: Literal["invocation-a", "invocation-b"], terminal: Mapping[str, object]
) -> None:
    report = {
        "schema_version": "live-e2e.private-final-report.v1",
        "invocation": invocation,
        "terminal": terminal,
        "cleanup": _terminal_cleanup_truth(terminal),
    }
    write_private_json(roots.reports / f"{invocation}.json", report, create_once=True)


def _write_private_projection_source(
    roots: E2EPrivateRoots, *, terminal: Mapping[str, object]
) -> Path:
    """Seal the safe private source used to derive a public projection before finalization."""
    path = roots.reports / "invocation-b-projection-source.json"
    write_private_json(
        path,
        {
            "schema_version": "live-e2e.private-projection-source.v1",
            "invocation": "invocation-b",
            "terminal": terminal,
            "cleanup": _terminal_cleanup_truth(terminal),
        },
        create_once=True,
    )
    return path


def run_invocation_a(config: E2EConfig, roots: E2EPrivateRoots) -> dict[str, object]:
    """Run the sole no-fault probe and stop at the human authorization boundary."""
    roots.bind_lifecycle(config.authority)
    if (roots.control / "scenario-lock.json").exists() or (roots.invocation_a / "terminal.json").exists():
        raise RuntimeError("Invocation A is create-once and already consumed")
    if _git(config.repository_root, "branch", "--show-current") != config.authority.branch:
        raise RuntimeError("Invocation A branch differs from successor authority")
    if _git(config.repository_root, "status", "--porcelain=v1"):
        raise RuntimeError("Invocation A requires a clean implementation worktree")
    environment = SandboxEnvironment(
        repository_root=config.repository_root,
        bundle=config.sandbox,
        flagd_directory=roots.runtime / "preflight-flagd",
    )
    controller: SandboxFaultController | None = None
    source_results: tuple[SourceProbeResult, SourceProbeResult, SourceProbeResult] | None = None
    success_payload: dict[str, object] | None = None
    failure_type: str | None = None
    cleanup_verdict = "NOT_STARTED"
    cleanup_payload: dict[str, object] | None = None
    start_attempted = False
    preauthorization_lock: dict[str, object] | None = None
    preauthorization_template: dict[str, object] | None = None
    preauthorization_source_counts: dict[str, int] | None = None
    preauthorization_visible_count: int | None = None
    try:
        environment.verify_local_docker()
        environment.verify_upstream()
        resolved, raw_compose = environment.resolve()
        write_private_json(roots.control / "resolved-compose.json", raw_compose, create_once=True)
        environment.verify_cached_images(resolved, roots.control)
        controller = _make_controller(
            config,
            roots,
            resolved.endpoints,
            flagd_directory=roots.runtime / "preflight-flagd",
        )
        start_attempted = True
        environment.start()
        environment.wait_healthy()
        time.sleep(config.sandbox.verification.minimum_stabilization_seconds)
        baseline = controller.read_current()
        if baseline.document_sha256 != config.sandbox.scenario.baseline_document_sha256:
            raise RuntimeError("Invocation A did not start from the frozen baseline")
        probe_window, source_results = _capture_e2e_window(
            config,
            roots,
            label="invocation-a-preflight",
            endpoints=resolved.endpoints,
            phase="PREFLIGHT",
        )
        if probe_window.request_count <= 0 or probe_window.runtime_health <= 0:
            raise RuntimeError("Invocation A preflight SLI is unavailable")
        snapshot = _broad_metric_snapshot(
            resolved.endpoints.prometheus,
            at=probe_window.ended_at,
        )
        observations = tuple(
            LiveMetricObservation(
                service_name=service,
                baseline_requests=values[0],
                baseline_errors=values[1],
                fault_requests=values[0],
                fault_errors=values[1],
                baseline_p95_ms=values[2],
                fault_p95_ms=values[2],
            )
            for service, values in sorted(snapshot.items())
            if values[0] > 0
        )
        logs = _capture_broad_logs(
            resolved.endpoints.opensearch,
            window_start=probe_window.started_at,
            window_end=probe_window.ended_at,
            maximum_hits=config.projection.log_raw_hit_limit,
        )
        trace_services = select_trace_candidate_services(
            metrics=observations,
            logs=logs,
            additional_limit=max(0, config.projection.trace_query_limit - 1),
        )
        traces = _capture_broad_traces(
            resolved.endpoints.jaeger,
            services=trace_services,
            window_start=probe_window.started_at,
            window_end=probe_window.ended_at,
            maximum_queries=config.projection.trace_query_limit,
            maximum_evidence=config.projection.trace_evidence_limit,
        )
        raw_observations = {
            "metrics": [item.model_dump(mode="json") for item in observations],
            "logs": [item.model_dump(mode="json") for item in logs],
            "traces": [item.model_dump(mode="json") for item in traces],
        }
        observations, logs, traces, resolver_refs = _seal_model_evidence_resolver(
            roots,
            label="invocation-a",
            window_start=probe_window.started_at,
            window_end=probe_window.ended_at,
            metrics=observations,
            logs=logs,
            traces=traces,
        )
        context = build_live_a0_context(
            window_start=probe_window.started_at,
            window_end=probe_window.ended_at,
            metrics=observations,
            logs=logs,
            traces=traces,
            resolvable_refs=resolver_refs,
            projection=config.projection,
        )
        _write_model_evidence_index(roots, context, raw_observations=raw_observations)
        lock = scenario_lock_manifest(
            config,
            resolved_compose_sha256=canonical_sha256(raw_compose),
        )
        template = build_plan_template(config)
        preauthorization_lock = lock
        preauthorization_template = template
        preauthorization_source_counts = _safe_source_counts(source_results)
        preauthorization_visible_count = len(context.visible_entities)
    except Exception as error:
        failure_type = type(error).__name__
    finally:
        if start_attempted:
            baseline_restored = False
            if controller is not None:
                try:
                    current = controller.read_current()
                    if current.document_sha256 != config.sandbox.scenario.baseline_document_sha256:
                        controller.restore_baseline()
                    baseline_restored = True
                except Exception:
                    baseline_restored = False
            try:
                cleanup = environment.cleanup(baseline_restored=baseline_restored)
                cleanup_verdict = cleanup.verdict
                cleanup_payload = cleanup.model_dump(mode="json")
                write_private_json(roots.invocation_a / "cleanup.json", cleanup, create_once=True)
            except Exception as error:
                failure_type = type(error).__name__
                cleanup_verdict = "BLOCKED"
    if (
        cleanup_verdict == "CLEAN"
        and preauthorization_lock is not None
        and preauthorization_template is not None
        and preauthorization_source_counts is not None
        and preauthorization_visible_count is not None
    ):
        try:
            request = create_approval_request(config, scenario_lock=preauthorization_lock)
            write_private_json(roots.control / "scenario-lock.json", preauthorization_lock, create_once=True)
            write_private_json(roots.control / "plan-template.json", preauthorization_template, create_once=True)
            write_private_json(roots.control / "approval-request.json", request, create_once=True)
            approval_command = (
                "uv run --with pyarrow python scripts/live_sandbox/e2e_v1.py "
                f"--private-root {roots.root} approve --approver '<HUMAN_NAME>' "
                f"--phrase 'APPROVE {request.scenario_id} {request.plan_template_sha256}'"
            )
            success_payload = {
                "schema_version": "live-e2e.invocation-a.terminal.v1",
                "verdict": config.authority.invocation_a_terminal,
                "fault_injections": 0,
                "provider_calls": 0,
                "model_calls": 0,
                "forward_mutations": 0,
                "rollback_mutations": 0,
                "source_counts": preauthorization_source_counts,
                "approval_request_id": request.approval_request_id,
                "plan_template_sha256": request.plan_template_sha256,
                "approval_request_path": str(roots.control / "approval-request.json"),
                "approval_expires_at": request.expires_at.isoformat(),
                "approval_command": approval_command,
                "projection_probe_classification": (
                    "E2E_PROJECTION_DEVELOPMENT_ONLY",
                    "NOT_CANONICAL_V3",
                    "NOT_MODEL_EVIDENCE",
                    "NO_FAULT",
                    "NO_PROVIDER",
                    "NO_REMEDIATION",
                ),
                "visible_service_count": preauthorization_visible_count,
            }
        except Exception as error:
            failure_type = type(error).__name__
    if success_payload is None or cleanup_verdict != "CLEAN":
        terminal = {
            "schema_version": "live-e2e.invocation-a.terminal.v1",
            "verdict": "BLOCKED",
            "fault_injections": 0,
            "provider_calls": 0,
            "model_calls": 0,
            "forward_mutations": 0,
            "rollback_mutations": 0,
            "failure_type": failure_type,
            "failure_classification": "INVOCATION_A_RUNTIME_FAILURE",
            "cleanup_verdict": cleanup_verdict,
        }
    else:
        terminal = dict(success_payload)
        terminal["cleanup_verdict"] = cleanup_verdict
    terminal["cleanup"] = cleanup_payload or _terminal_cleanup_truth(terminal)
    _write_private_final_report(roots, invocation="invocation-a", terminal=terminal)
    write_private_json(roots.invocation_a / "terminal.json", terminal, create_once=True)
    roots.verify()
    return terminal


def build_plan_template(config: E2EConfig) -> dict[str, object]:
    from ecomsre_live_sandbox.contracts import LiveRemediationPlan

    return LiveRemediationPlan.template_payload(config.sandbox)


def _require_invocation_a_success(config: E2EConfig, roots: E2EPrivateRoots) -> None:
    path = roots.invocation_a / "terminal.json"
    if path.is_symlink() or not path.is_file():
        raise RuntimeError("Invocation B lacks a sealed Invocation A terminal")
    terminal = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(terminal, Mapping):
        raise RuntimeError("Invocation A terminal is malformed")
    if (
        terminal.get("verdict") != config.authority.invocation_a_terminal
        or terminal.get("cleanup_verdict") != "CLEAN"
        or terminal.get("fault_injections") != 0
        or terminal.get("provider_calls") != 0
        or terminal.get("model_calls") != 0
        or terminal.get("forward_mutations") != 0
        or terminal.get("rollback_mutations") != 0
    ):
        raise RuntimeError("Invocation A did not reach the clean human-authorization boundary")


def record_human_approval_for_invocation_b(
    config: E2EConfig,
    roots: E2EPrivateRoots,
    *,
    approver: str,
    phrase: str,
) -> HumanApprovalRecord:
    from ecomsre_live_sandbox.e2e_contracts import record_human_approval

    _verify_scenario_lock(config, roots)
    _require_invocation_a_success(config, roots)
    request_path = roots.control / "approval-request.json"
    request = json.loads(request_path.read_text(encoding="utf-8"))
    from ecomsre_live_sandbox.contracts import ApprovalRequest

    record = record_human_approval(
        ApprovalRequest.model_validate(request),
        approver=approver,
        phrase=phrase,
        now=datetime.now(timezone.utc),
        destination=roots.control / "human-approval.json",
    )
    roots.verify()
    return record


def _load_approval(roots: E2EPrivateRoots) -> tuple[ApprovalRequest, HumanApprovalRecord]:
    request_path = roots.control / "approval-request.json"
    approval_path = roots.control / "human-approval.json"
    if any(path.is_symlink() or not path.is_file() for path in (request_path, approval_path)):
        raise RuntimeError("Invocation B lacks an exact human approval record")
    return (
        ApprovalRequest.model_validate_json(request_path.read_text(encoding="utf-8")),
        HumanApprovalRecord.model_validate_json(approval_path.read_text(encoding="utf-8")),
    )


def _require_exact_approval_binding(
    config: E2EConfig,
    lock: Mapping[str, object],
    request: ApprovalRequest,
    approval: HumanApprovalRecord,
    *,
    now: datetime,
) -> None:
    """Reject stale or substituted human consent before any Provider interaction."""
    expected_request = create_approval_request(
        config,
        scenario_lock=lock,
        now=request.requested_at,
    )
    if request != expected_request or request.requested_at > now:
        raise RuntimeError("human approval request is not exactly bound to the frozen E2E scenario")
    expected_approval = {
        "approval_request_id": request.approval_request_id,
        "request_sha256": canonical_sha256(request),
        "plan_template_sha256": request.plan_template_sha256,
        "scenario_id": request.scenario_id,
        "environment_id": request.environment_id,
        "sandbox_id": request.sandbox_id,
        "action": request.action,
        "target_service": request.target_service,
        "configuration_key": request.configuration_key,
        "baseline_sha256": request.baseline_sha256,
        "expires_at": request.expires_at,
    }
    if any(getattr(approval, field) != expected for field, expected in expected_approval.items()):
        raise RuntimeError("human approval binding differs from its exact request")
    if (
        approval.mode != "HUMAN"
        or not approval.approver.strip()
        or approval.approver != approval.approver.strip()
        or approval.approved_at < request.requested_at
        or approval.approved_at > request.expires_at
        or now > request.expires_at
    ):
        raise RuntimeError("human approval is blank, invalid, or expired")


def _public_result(config: E2EConfig, terminal: Mapping[str, object]) -> dict[str, object]:
    cleanup = terminal.get("cleanup")
    value = {
        "schema_version": "live-e2e.public.v1",
        "version": config.authority.version,
        "verdict": terminal.get("verdict"),
        "claim_boundary": list(config.reporting.claim_boundary),
        "source_counts": terminal.get("source_counts"),
        "provider_calls": terminal.get("provider_calls"),
        "model_calls": terminal.get("model_calls"),
        "fault_injections": terminal.get("fault_injections"),
        "forward_mutations": terminal.get("forward_mutations"),
        "rollback_mutations": terminal.get("rollback_mutations"),
        "cleanup_verdict": terminal.get("cleanup_verdict"),
        "cleanup": cleanup,
        "diagnosis_gate": terminal.get("diagnosis_gate"),
        "policy_verdict": terminal.get("policy_verdict"),
        "source_availability": terminal.get("source_availability"),
        "evidence_resolver_valid": terminal.get("evidence_resolver_valid"),
        "visible_service_count": terminal.get("visible_service_count"),
        "context_safety_passed": terminal.get("context_safety_passed"),
        "fault_impact_passed": terminal.get("fault_impact_passed"),
        "provider_preflight_passed": terminal.get("provider_preflight_passed"),
        "approval_valid": terminal.get("approval_valid"),
        "plan_template_exact": terminal.get("plan_template_exact"),
        "recovery_verification_passed": terminal.get("recovery_verification_passed"),
        "implementation_commit": terminal.get("implementation_commit"),
    }
    from ecomsre_live_sandbox.e2e_contracts import scan_public_e2e_payload

    if scan_public_e2e_payload(value):
        raise RuntimeError("public E2E projection leaked a protected surface")
    return value


def _cleanup_truth_is_valid(cleanup_verdict: object, cleanup: object) -> bool:
    if not isinstance(cleanup_verdict, str) or not isinstance(cleanup, Mapping):
        return False
    required = {
        "owned_containers": 0,
        "owned_networks": 0,
        "owned_volumes": 0,
        "non_owned_resources_changed": False,
        "verdict": cleanup_verdict,
    }
    if any(cleanup.get(key) != expected for key, expected in required.items()):
        return False
    if cleanup_verdict in {"CLEAN", "NOT_REQUIRED"}:
        return cleanup.get("baseline_restored") is True
    return isinstance(cleanup.get("baseline_restored"), bool)


def _successful_terminal_is_valid(config: E2EConfig, terminal: Mapping[str, object]) -> bool:
    source_counts = terminal.get("source_counts")
    source_availability = terminal.get("source_availability")
    if not isinstance(source_counts, Mapping) or not isinstance(source_availability, Mapping):
        return False
    if any(source_counts.get(source) is None or not isinstance(source_counts.get(source), int) or source_counts[source] <= 0 for source in ("METRICS", "LOGS", "TRACES")):
        return False
    if any(source_availability.get(source) != "AVAILABLE" for source in ("METRICS", "LOGS", "TRACES")):
        return False
    expected = {
        "verdict": config.authority.invocation_b_success,
        "provider_calls": 2,
        "model_calls": 1,
        "fault_injections": 1,
        "forward_mutations": 1,
        "rollback_mutations": 0,
        "cleanup_verdict": "CLEAN",
        "evidence_resolver_valid": True,
        "context_safety_passed": True,
        "fault_impact_passed": True,
        "provider_preflight_passed": True,
        "approval_valid": True,
        "plan_template_exact": True,
        "diagnosis_gate": True,
        "policy_verdict": "ALLOW",
        "recovery_verification_passed": True,
    }
    if any(terminal.get(key) != value for key, value in expected.items()):
        return False
    visible_count = terminal.get("visible_service_count")
    return (
        isinstance(visible_count, int)
        and config.projection.visible_entity_minimum <= visible_count <= config.projection.visible_entity_maximum
        and isinstance(terminal.get("implementation_commit"), str)
        and len(cast(str, terminal["implementation_commit"])) == 40
        and _cleanup_truth_is_valid(terminal.get("cleanup_verdict"), terminal.get("cleanup"))
    )


def _sealed_terminal_is_publicly_publishable(config: E2EConfig, terminal: Mapping[str, object]) -> bool:
    """Verify the safe aggregate claims against the sealed terminal, never raw evidence."""
    if terminal.get("verdict") not in _legal_terminal_verdicts(config):
        return False
    if not _cleanup_truth_is_valid(terminal.get("cleanup_verdict"), terminal.get("cleanup")):
        return False
    if terminal.get("verdict") == config.authority.invocation_b_success:
        return _successful_terminal_is_valid(config, terminal)
    return True


def verify_public_result(
    config: E2EConfig,
    public: Mapping[str, object],
    *,
    sealed_terminal: Mapping[str, object] | None = None,
) -> bool:
    """Independently verify public aggregates, optionally against the sealed terminal."""
    from ecomsre_live_sandbox.e2e_contracts import scan_public_e2e_payload

    required = {
        "schema_version": "live-e2e.public.v1",
        "version": config.authority.version,
        "claim_boundary": list(config.reporting.claim_boundary),
    }
    if any(public.get(key) != expected for key, expected in required.items()):
        return False
    if scan_public_e2e_payload(public):
        return False
    if not _cleanup_truth_is_valid(public.get("cleanup_verdict"), public.get("cleanup")):
        return False
    if public.get("verdict") not in _legal_terminal_verdicts(config):
        return False
    if public.get("verdict") == config.authority.invocation_b_success and not _successful_terminal_is_valid(config, public):
        return False
    if sealed_terminal is None:
        return isinstance(public.get("verdict"), str)
    if not _sealed_terminal_is_publicly_publishable(config, sealed_terminal):
        return False
    return public == _public_result(config, sealed_terminal)


def _write_new_public(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())


def _write_public_outputs(config: E2EConfig, terminal: Mapping[str, object]) -> tuple[str, str, str]:
    public = _public_result(config, terminal)
    if not verify_public_result(config, public, sealed_terminal=terminal):
        raise RuntimeError("independent public E2E verifier did not pass")
    paths = (
        config.repository_root / config.reporting.public_result_json,
        config.repository_root / config.reporting.public_result_markdown,
        config.repository_root / config.reporting.public_human_brief,
    )
    _write_new_public(paths[0], canonical_json_bytes(public))
    _write_new_public(
        paths[1],
        (
            "# Live Fault to A0 Controlled Remediation E2E v1\n\n"
            f"**Verdict:** `{public['verdict']}`\n\n"
            "This local, one-scenario run uses a HUMAN_PREAUTHORIZED_FROZEN_REMEDIATION_RUNBOOK. "
            "The human approval covers the frozen runbook, not the live diagnosis or post-diagnosis plan. "
            "It is not production, autonomous production remediation, an external benchmark, or a Multi-Agent claim.\n"
        ).encode("utf-8"),
    )
    _write_new_public(
        paths[2],
        (
            "# Live Fault → A0 → Controlled Remediation — Human Brief\n\n"
            "This is a one-local-sandbox, preregistered-scenario demonstration. The human preauthorized "
            "the frozen remediation runbook before the live fault; they did not review the live diagnosis "
            "or approve the post-diagnosis plan. It makes no production or autonomous-remediation claim.\n"
        ).encode("utf-8"),
    )
    return tuple(path.relative_to(config.repository_root).as_posix() for path in paths)  # type: ignore[return-value]


def _record_rollback_mutation(roots: E2EPrivateRoots, terminal: dict[str, object]) -> None:
    """Persist mutation accounting immediately after the controller restores fault state."""
    terminal["rollback_mutations"] = 1
    write_private_json(
        roots.journal / "rollback-mutation.json",
        {"schema_version": "live-e2e.rollback-mutation.v1", "mutation_count": 1},
        create_once=True,
    )


def run_invocation_b(config: E2EConfig, roots: E2EPrivateRoots) -> dict[str, object]:
    """Perform the single approved positive run. Any failure is terminal for this root."""
    roots.bind_lifecycle(config.authority)
    terminal_path = roots.invocation_b / "terminal.json"
    started_path = roots.invocation_b / "started.json"
    if any(path.exists() or path.is_symlink() for path in (terminal_path, started_path)):
        raise RuntimeError("Invocation B is create-once and already consumed")
    environment = SandboxEnvironment(
        repository_root=config.repository_root,
        bundle=config.sandbox,
        flagd_directory=roots.runtime / "live-flagd",
    )
    docker = environment.verify_local_docker()
    environment.verify_upstream()
    resolved, raw_compose = environment.resolve()
    if any(environment.verify_owned_resources(require_complete=False).values()):
        raise RuntimeError("Invocation B requires no owned resources before Provider preflight")
    lock = _verify_scenario_lock(
        config,
        roots,
        resolved_compose_sha256=canonical_sha256(raw_compose),
    )
    _require_invocation_a_success(config, roots)
    request, approval = _load_approval(roots)
    _require_exact_approval_binding(
        config,
        lock,
        request,
        approval,
        now=datetime.now(timezone.utc),
    )
    write_private_json(
        started_path,
        {
            "schema_version": "live-e2e.invocation-b.started.v1",
            "started_at": datetime.now(timezone.utc).isoformat(),
        },
        create_once=True,
    )
    provider: OpenAICompatibleRCA100Provider | None = None
    controller: SandboxFaultController | None = None
    start_attempted = False
    cleanup = None
    forward_counter = ForwardMutationCounter(roots.journal / "forward-mutation.txt")
    terminal: dict[str, object] = {
        "schema_version": "live-e2e.invocation-b.terminal.v1",
        "verdict": "BLOCKED",
        "provider_calls": 0,
        "model_calls": 0,
        "fault_injections": 0,
        "forward_mutations": 0,
        "rollback_mutations": 0,
        "approval_valid": True,
        "plan_template_exact": True,
        "implementation_commit": lock["implementation_commit"],
    }
    try:
        terminal["verdict"] = "BLOCKED_PROVIDER_PREFLIGHT"
        provider = _provider(config)
        synthetic = _synthetic_provider_context(config)
        preflight = provider.diagnose(synthetic)  # exactly one non-scored Provider call
        terminal["provider_calls"] = provider.calls
        if provider.calls != 1 or not provider.usage_known or provider.last_usage_tokens is None:
            raise RuntimeError("synthetic Provider preflight did not return known bounded usage")
        terminal["provider_preflight_passed"] = True
        write_private_json(
            roots.provider / "synthetic-preflight.json",
            {"request_sha256": provider.last_request_sha256, "diagnosis": preflight, "usage_tokens": provider.last_usage_tokens},
            create_once=True,
        )
        time.sleep(config.sandbox.budget.minimum_request_spacing_seconds)
        write_private_json(roots.invocation_b / "resolved-compose.json", raw_compose, create_once=True)
        controller = _make_controller(
            config,
            roots,
            resolved.endpoints,
            flagd_directory=roots.runtime / "live-flagd",
        )
        start_attempted = True
        environment.start()
        terminal["verdict"] = "BLOCKED"
        environment.wait_healthy()
        time.sleep(config.sandbox.verification.minimum_stabilization_seconds)
        if controller.read_current().document_sha256 != config.sandbox.scenario.baseline_document_sha256:
            raise RuntimeError("Invocation B baseline configuration differs before fault")
        baseline_1, _baseline_1_sources = _capture_e2e_window(
            config,
            roots,
            label="invocation-b-baseline-1",
            endpoints=resolved.endpoints,
            phase="BASELINE",
        )
        baseline_2, _baseline_2_sources = _capture_e2e_window(
            config,
            roots,
            label="invocation-b-baseline-2",
            endpoints=resolved.endpoints,
            phase="BASELINE",
        )
        baseline = (baseline_1, baseline_2)
        baseline_snapshot = _broad_metric_snapshot(resolved.endpoints.prometheus, at=baseline[-1].ended_at)
        terminal["fault_injections"] = 1
        fault_state = controller.inject_fault()
        if fault_state.document_sha256 != config.sandbox.scenario.fault_document_sha256:
            raise RuntimeError("frozen fault injection did not reach the exact fault document")
        time.sleep(config.sandbox.verification.minimum_stabilization_seconds)
        fault_1, _fault_1_sources = _capture_e2e_window(
            config,
            roots,
            label="invocation-b-fault-1",
            endpoints=resolved.endpoints,
            phase="FAULT",
        )
        fault_2, fault_2_sources = _capture_e2e_window(
            config,
            roots,
            label="invocation-b-fault-2",
            endpoints=resolved.endpoints,
            phase="FAULT",
        )
        fault = (fault_1, fault_2)
        if not fault_impact_passed(baseline, fault, config.sandbox):
            terminal["verdict"] = "BLOCKED_FAULT_IMPACT_NOT_OBSERVED"
            raise RuntimeError("two-window fault impact gate did not pass")
        terminal["fault_impact_passed"] = True
        fault_snapshot = _broad_metric_snapshot(resolved.endpoints.prometheus, at=fault[-1].ended_at)
        terminal["verdict"] = "BLOCKED_LIVE_TELEMETRY_SOURCE_UNAVAILABLE"
        source_results = fault_2_sources
        terminal["source_availability"] = {item.source: item.status.value for item in source_results}
        observations = tuple(
            LiveMetricObservation(
                service_name=service,
                baseline_requests=baseline_snapshot.get(service, (0.0, 0.0, 0.0))[0],
                baseline_errors=baseline_snapshot.get(service, (0.0, 0.0, 0.0))[1],
                fault_requests=fault_values[0],
                fault_errors=fault_values[1],
                baseline_p95_ms=baseline_snapshot.get(service, (0.0, 0.0, 0.0))[2],
                fault_p95_ms=fault_values[2],
                first_anomaly_at=(
                    fault[0].started_at
                    if fault_values[1] / fault_values[0]
                    > baseline_snapshot.get(service, (0.0, 0.0, 0.0))[1]
                    / baseline_snapshot.get(service, (1.0, 0.0, 0.0))[0]
                    else None
                ),
            )
            for service, fault_values in sorted(fault_snapshot.items())
            if baseline_snapshot.get(service, (0.0, 0.0, 0.0))[0] > 0
        )
        logs = _capture_broad_logs(
            resolved.endpoints.opensearch,
            window_start=fault[0].started_at,
            window_end=fault[-1].ended_at,
            maximum_hits=config.projection.log_raw_hit_limit,
        )
        trace_services = select_trace_candidate_services(
            metrics=observations,
            logs=logs,
            additional_limit=max(0, config.projection.trace_query_limit - 1),
        )
        traces = _capture_broad_traces(
            resolved.endpoints.jaeger,
            services=trace_services,
            window_start=fault[0].started_at,
            window_end=fault[-1].ended_at,
            maximum_queries=config.projection.trace_query_limit,
            maximum_evidence=config.projection.trace_evidence_limit,
        )
        raw_observations = {
            "metrics": [item.model_dump(mode="json") for item in observations],
            "logs": [item.model_dump(mode="json") for item in logs],
            "traces": [item.model_dump(mode="json") for item in traces],
        }
        write_private_json(
            roots.telemetry / "model-raw-observations.json",
            raw_observations,
            create_once=True,
        )
        observations, logs, traces, resolver_refs = _seal_model_evidence_resolver(
            roots,
            label="invocation-b",
            window_start=fault[0].started_at,
            window_end=fault[-1].ended_at,
            metrics=observations,
            logs=logs,
            traces=traces,
        )
        terminal["evidence_resolver_valid"] = True
        terminal["verdict"] = "BLOCKED_BOUNDED_MULTISERVICE_PROJECTION_UNAVAILABLE"
        context = build_live_a0_context(
            window_start=baseline[0].started_at,
            window_end=fault[-1].ended_at,
            metrics=observations,
            logs=logs,
            traces=traces,
            resolvable_refs=resolver_refs,
            projection=config.projection,
        )
        terminal["visible_service_count"] = len(context.visible_entities)
        terminal["context_safety_passed"] = True
        _write_model_evidence_index(roots, context, raw_observations=raw_observations)
        write_private_json(roots.provider / "live-context.json", context, create_once=True)
        terminal["verdict"] = "LIVE_DIAGNOSIS_GATE_NOT_PASSED_NO_REMEDIATION"
        diagnosis = _diagnosis_from_initial(provider, context)
        if provider.calls != 2 or not provider.usage_known:
            raise RuntimeError("live A0 call did not preserve the one-preflight plus one-live budget")
        terminal["provider_calls"] = 2
        terminal["model_calls"] = 1
        write_private_json(roots.provider / "live-diagnosis.json", diagnosis, create_once=True)
        diagnosis_gate = evaluate_diagnosis_gate(diagnosis, config.sandbox)
        if not diagnosis_gate.passed:
            raise RuntimeError("A0 diagnosis gate denied controlled remediation")
        terminal["diagnosis_gate"] = True
        terminal["verdict"] = "BLOCKED_POLICY_REJECTED"
        plan = build_plan(diagnosis, config.sandbox)
        policy = evaluate_policy(
            plan=plan,
            diagnosis=diagnosis,
            request=request,  # type: ignore[arg-type]
            approval=approval,
            bundle=config.sandbox,
            docker_endpoint=str(docker["endpoint"]),
            owned_labels={
                "com.docker.compose.project": config.sandbox.environment.compose_project,
                config.sandbox.environment.sandbox_label_key: config.sandbox.environment.sandbox_id,
            },
            forward_mutations=0,
            now=datetime.now(timezone.utc),
        )
        if policy.verdict.value != "ALLOW":
            raise RuntimeError("Policy Gate denied controlled remediation")
        terminal["policy_verdict"] = policy.verdict.value
        write_private_json(roots.invocation_b / "plan.json", plan, create_once=True)
        write_private_json(roots.invocation_b / "policy.json", policy, create_once=True)
        receipt = LocalSandboxRestrictedExecutor().execute(
            plan=plan,
            policy=policy,
            controller=controller,
            mutation_counter=forward_counter,
        )
        terminal["forward_mutations"] = forward_counter.count
        terminal["verdict"] = "CONTROLLED_REMEDIATION_NOT_VERIFIED_ROLLBACK_COMPLETED"
        write_private_json(roots.invocation_b / "execution-receipt.json", receipt, create_once=True)
        time.sleep(config.sandbox.verification.minimum_stabilization_seconds)
        recovery_1, _recovery_1_sources = _capture_e2e_window(
            config,
            roots,
            label="invocation-b-recovery-1",
            endpoints=resolved.endpoints,
            phase="RECOVERY",
        )
        recovery_2, _recovery_2_sources = _capture_e2e_window(
            config,
            roots,
            label="invocation-b-recovery-2",
            endpoints=resolved.endpoints,
            phase="RECOVERY",
        )
        recovery = (recovery_1, recovery_2)
        verification = IndependentVerifier().verify(
            plan=plan,
            receipt=receipt,
            current=controller.read_current(),
            baseline_windows=baseline,
            recovery_windows=recovery,
            services_healthy=all(environment.service_health().values()),
            labels_exact=True,
            bundle=config.sandbox,
        )
        write_private_json(roots.invocation_b / "verification.json", verification, create_once=True)
        if not verification.passed:
            terminal["recovery_verification_passed"] = False
            try:
                rollback = compensate_rollback(
                    receipt=receipt,
                    verification=verification,
                    controller=controller,
                    on_mutation=lambda: _record_rollback_mutation(roots, terminal),
                )
            except Exception:
                terminal["verdict"] = "BLOCKED_ROLLBACK_FAILED_MANUAL_CLEANUP_REQUIRED"
                terminal["manual_cleanup_command"] = environment.manual_cleanup_command()
                raise
            write_private_json(roots.invocation_b / "rollback.json", rollback, create_once=True)
            if not rollback.exact_hash_verified or rollback.restored_sha256 != config.sandbox.scenario.fault_document_sha256:
                raise RuntimeError("compensating rollback did not restore the exact fault state")
            terminal.update(
                {
                    "verdict": "CONTROLLED_REMEDIATION_NOT_VERIFIED_ROLLBACK_COMPLETED",
                    "source_counts": _safe_source_counts(source_results),
                    "diagnosis_gate": diagnosis_gate.passed,
                    "policy_verdict": policy.verdict.value,
                    "implementation_commit": lock["implementation_commit"],
                    "rollback_exact_hash_verified": True,
                }
            )
        else:
            terminal["recovery_verification_passed"] = True
            terminal.update(
                {
                    "verdict": config.authority.invocation_b_success,
                    "source_counts": _safe_source_counts(source_results),
                    "diagnosis_gate": diagnosis_gate.passed,
                    "policy_verdict": policy.verdict.value,
                    "implementation_commit": lock["implementation_commit"],
                }
            )
    except Exception as error:
        if provider is not None:
            terminal["provider_calls"] = provider.calls
        terminal["failure_type"] = type(error).__name__
        if terminal.get("verdict") == "BLOCKED":
            terminal["failure_classification"] = "UNCLASSIFIED_RUNTIME_FAILURE"
    finally:
        terminal["forward_mutations"] = forward_counter.count
        if start_attempted:
            baseline_restored = False
            if controller is not None:
                try:
                    if controller.read_current().document_sha256 != config.sandbox.scenario.baseline_document_sha256:
                        controller.restore_baseline()
                    baseline_restored = True
                except Exception:
                    baseline_restored = False
            try:
                cleanup = environment.cleanup(baseline_restored=baseline_restored)
                terminal["cleanup_verdict"] = cleanup.verdict
                terminal["cleanup"] = cleanup.model_dump(mode="json")
            except Exception as error:
                terminal["failure_type"] = type(error).__name__
                terminal["cleanup_verdict"] = "BLOCKED"
        else:
            terminal["cleanup_verdict"] = "NOT_REQUIRED"
        terminal["cleanup"] = _terminal_cleanup_truth(terminal)
        if terminal.get("cleanup_verdict") != "CLEAN" and start_attempted:
            terminal["verdict"] = "BLOCKED_CLEANUP_INCOMPLETE"
        projection_source_path = _write_private_projection_source(roots, terminal=terminal)
        roots.verify()
        projection_source = json.loads(projection_source_path.read_text(encoding="utf-8"))
        sealed_terminal = projection_source.get("terminal") if isinstance(projection_source, Mapping) else None
        if (
            not isinstance(sealed_terminal, Mapping)
            or not _sealed_terminal_is_publicly_publishable(config, sealed_terminal)
        ):
            raise RuntimeError("sealed private Invocation B projection source cannot be safely projected")
        try:
            terminal["public_outputs"] = _write_public_outputs(config, sealed_terminal)
            terminal["public_projection_status"] = "PASSED"
        except Exception as error:
            terminal["verdict"] = "BLOCKED"
            terminal["failure_type"] = type(error).__name__
            terminal["failure_classification"] = "PUBLIC_REPORT_FAILURE"
            terminal["public_projection_status"] = "PARTIAL_OR_NONE"
        _write_private_final_report(roots, invocation="invocation-b", terminal=terminal)
        write_private_json(terminal_path, terminal, create_once=True)
        roots.verify()
    return terminal


__all__ = [
    "build_plan_template",
    "record_human_approval_for_invocation_b",
    "run_invocation_a",
    "run_invocation_b",
    "scenario_lock_manifest",
    "verify_public_result",
]
