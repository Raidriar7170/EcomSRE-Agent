"""The one-shot successor from a controlled local fault to A0 remediation."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
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
    "e2e_telemetry.py": Path("src/ecomsre_live_sandbox/e2e_telemetry.py"),
    "e2e_v1.py": Path("src/ecomsre_live_sandbox/e2e_v1.py"),
    "test_e2e_projection.py": Path("tests/live_sandbox/test_e2e_projection.py"),
    "test_e2e_v1.py": Path("tests/live_sandbox/test_e2e_v1.py"),
}


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


def scenario_lock_manifest(config: E2EConfig) -> dict[str, object]:
    repository_root = config.repository_root
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
        "tracked_files": {
            name: file_sha256(repository_root / relative)
            for name, relative in _TRACKED_FILES.items()
        },
    }


def _verify_scenario_lock(config: E2EConfig, roots: E2EPrivateRoots) -> Mapping[str, object]:
    path = roots.control / "scenario-lock.json"
    if path.is_symlink() or not path.is_file():
        raise RuntimeError("frozen E2E scenario lock is unavailable")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise RuntimeError("frozen E2E scenario lock is malformed")
    if value.get("implementation_branch") != config.authority.branch:
        raise RuntimeError("E2E scenario lock branch differs")
    expected = scenario_lock_manifest(config)
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
        "tracked_files",
    ):
        if value.get(key) != expected.get(key):
            raise RuntimeError(f"frozen E2E scenario lock drifted: {key}")
    commit = value.get("implementation_commit")
    if not isinstance(commit, str) or len(commit) != 40:
        raise RuntimeError("frozen E2E implementation commit is invalid")
    _git(config.repository_root, "merge-base", "--is-ancestor", commit, "HEAD")
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


def _capture_broad_logs(
    endpoint: str,
    *,
    window_start: datetime,
    window_end: datetime,
    maximum_hits: int,
) -> tuple[LiveLogObservation, ...]:
    field_caps = _strict_json(f"{endpoint}/otel-logs-*/_field_caps")
    fields = field_caps.get("fields") if isinstance(field_caps, Mapping) else None
    if not isinstance(fields, Mapping):
        raise RuntimeError("OpenSearch field caps are unavailable")
    time_field = next((item for item in ("observedTimestamp", "@timestamp", "timestamp") if item in fields), None)
    service_field = next(
        (item for item in ("resource.service.name.keyword", "resource.service.name", "service.name") if item in fields),
        None,
    )
    if time_field is None or service_field is None:
        raise RuntimeError("OpenSearch observed time or service field is unavailable")
    query = {
        "size": maximum_hits,
        "sort": [{time_field: {"order": "desc"}}],
        "query": {"bool": {"filter": [{"range": {time_field: {"gte": window_start.isoformat(), "lte": window_end.isoformat()}}}]}},
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
        raw_service = _nested(source, service_field)
        if not isinstance(raw_time, str) or not isinstance(raw_service, str) or not raw_service:
            continue
        try:
            observed = datetime.fromisoformat(raw_time.replace("Z", "+00:00"))
        except ValueError:
            continue
        severity = _nested(source, "severity.text") or _nested(source, "severityText") or "UNKNOWN"
        body = _nested(source, "body") or _nested(source, "message") or "observed log event"
        if not isinstance(severity, str) or not isinstance(body, str):
            continue
        output.append(
            LiveLogObservation(
                observed_at=observed,
                service_name=raw_service,
                severity=severity[:64],
                body=body[:2_000],
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
    output: list[LiveTraceObservation] = []
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
            for span in spans:
                process_id = span.get("processID") if isinstance(span, Mapping) else None
                process = processes.get(process_id) if isinstance(process_id, str) else None
                if not isinstance(process, Mapping) or process.get("serviceName") != service:
                    continue
                started = span.get("startTime") if isinstance(span, Mapping) else None
                duration = span.get("duration") if isinstance(span, Mapping) else None
                operation = span.get("operationName") if isinstance(span, Mapping) else None
                if not isinstance(started, (int, float)) or not isinstance(duration, (int, float)) or not isinstance(operation, str):
                    continue
                output.append(
                    LiveTraceObservation(
                        observed_at=datetime.fromtimestamp(started / 1_000_000, tz=timezone.utc),
                        service_name=service,
                        operation=operation[:512],
                        status=_trace_status(span.get("tags")),
                        duration_ms=duration / 1_000.0,
                    )
                )
    return tuple(sorted(output, key=lambda item: (item.observed_at, item.service_name, item.operation))[:maximum_evidence])


def _make_controller(config: E2EConfig, roots: E2EPrivateRoots, endpoints: object) -> SandboxFaultController:
    from ecomsre_live_sandbox.contracts import LocalEndpoints

    if not isinstance(endpoints, LocalEndpoints):
        raise ValueError("resolved local endpoints are invalid")
    upstream_path = config.repository_root / "third_party/opentelemetry-demo/src/flagd/demo.flagd.json"
    value = json.loads(upstream_path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise ValueError("frozen upstream flag document is malformed")
    baseline, fault = build_flag_documents(value, config.sandbox)
    flag_file = roots.control / "flagd" / "demo.flagd.json"
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
) -> tuple[SourceProbeResult, SourceProbeResult, SourceProbeResult]:
    from ecomsre_live_sandbox.contracts import LocalEndpoints

    if not isinstance(endpoints, LocalEndpoints):
        raise ValueError("resolved local endpoints are invalid")
    v3 = load_instrumentation_config(config.repository_root / V3_CONFIG_RELATIVE)
    window_end = datetime.now(timezone.utc)
    window_start = datetime.fromtimestamp(window_end.timestamp() - v3.readiness.capture_window_seconds, tz=timezone.utc)
    store = PrivateArtifactStore(roots.telemetry / label)
    probes = (
        MetricsSourceProbe(
            endpoint=endpoints.prometheus,
            target_service=v3.environment.target_service,
            config=v3.sources.prometheus,
            readiness=v3.readiness,
            store=store,
            window_start=window_start,
            window_end=window_end,
        ),
        LogsSourceProbe(
            endpoint=endpoints.opensearch,
            target_service=v3.environment.target_service,
            config=v3.sources.opensearch,
            readiness=v3.readiness,
            store=store,
            window_start=window_start,
            window_end=window_end,
        ),
        TracesSourceProbe(
            endpoint=endpoints.jaeger,
            target_service=v3.environment.target_service,
            config=v3.sources.jaeger,
            readiness=v3.readiness,
            store=store,
            window_start=window_start,
            window_end=window_end,
        ),
    )
    raw_results = terminalize_source_probes(
        cast(Sequence[SourceProbe], probes),
        window_start=window_start,
        window_end=window_end,
    )
    resolver = EvidenceResolver.from_file(store.seal())
    results, all_refs_resolve = _revalidate_refs(raw_results, resolver=resolver, store_root=store.root)
    if not all_refs_resolve or any(
        item.status is not SourceProbeStatus.AVAILABLE
        or item.target_record_count <= 0
        or item.invalid_ref_count
        for item in results
    ):
        raise RuntimeError("v3 typed source readiness is not 3/3 AVAILABLE")
    return results


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


def _synthetic_provider_context(config: E2EConfig) -> RCA100AgentContext:
    now = datetime.now(timezone.utc)
    return build_live_a0_context(
        opaque_case_id="rca100-case-0001",
        window_start=datetime.fromtimestamp(now.timestamp() - 60, tz=timezone.utc),
        window_end=now,
        metrics=(
            LiveMetricObservation(service_name="checkout", baseline_requests=100, baseline_errors=1, fault_requests=100, fault_errors=25, baseline_p95_ms=20, fault_p95_ms=30),
            LiveMetricObservation(service_name="currency", baseline_requests=100, baseline_errors=1, fault_requests=100, fault_errors=12, baseline_p95_ms=20, fault_p95_ms=28),
            LiveMetricObservation(service_name="frontend", baseline_requests=100, baseline_errors=1, fault_requests=100, fault_errors=8, baseline_p95_ms=20, fault_p95_ms=26),
        ),
        logs=(LiveLogObservation(observed_at=now, service_name="checkout", severity="ERROR", body="observed request error"),),
        traces=(LiveTraceObservation(observed_at=now, service_name="checkout", operation="request", status="ERROR", duration_ms=20),),
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


def run_invocation_a(config: E2EConfig, roots: E2EPrivateRoots) -> dict[str, object]:
    """Run the sole no-fault probe and stop at the human authorization boundary."""
    roots.prepare()
    if (roots.control / "scenario-lock.json").exists() or (roots.invocation_a / "terminal.json").exists():
        raise RuntimeError("Invocation A is create-once and already consumed")
    if _git(config.repository_root, "branch", "--show-current") != config.authority.branch:
        raise RuntimeError("Invocation A branch differs from successor authority")
    if _git(config.repository_root, "status", "--porcelain=v1"):
        raise RuntimeError("Invocation A requires a clean implementation worktree")
    environment = SandboxEnvironment(
        repository_root=config.repository_root,
        bundle=config.sandbox,
        flagd_directory=roots.control / "flagd",
    )
    controller: SandboxFaultController | None = None
    source_results: tuple[SourceProbeResult, SourceProbeResult, SourceProbeResult] | None = None
    success_payload: dict[str, object] | None = None
    failure_type: str | None = None
    cleanup_verdict = "NOT_STARTED"
    started = False
    try:
        environment.verify_local_docker()
        environment.verify_upstream()
        resolved, raw_compose = environment.resolve()
        write_private_json(roots.control / "resolved-compose.json", raw_compose, create_once=True)
        environment.verify_cached_images(resolved, roots.control)
        controller = _make_controller(config, roots, resolved.endpoints)
        environment.start()
        started = True
        environment.wait_healthy()
        time.sleep(config.sandbox.verification.minimum_stabilization_seconds)
        baseline = controller.read_current()
        if baseline.document_sha256 != config.sandbox.scenario.baseline_document_sha256:
            raise RuntimeError("Invocation A did not start from the frozen baseline")
        windows = (
            _capture_sli_window(config, resolved.endpoints.prometheus, phase="PREFLIGHT"),
            _capture_sli_window(config, resolved.endpoints.prometheus, phase="PREFLIGHT"),
        )
        if any(item.request_count <= 0 or item.runtime_health <= 0 for item in windows):
            raise RuntimeError("Invocation A preflight SLI is unavailable")
        time.sleep(15)
        source_results = _capture_source_readiness(config, roots, label="invocation-a", endpoints=resolved.endpoints)
        lock = scenario_lock_manifest(config)
        write_private_json(roots.control / "scenario-lock.json", lock, create_once=True)
        template = build_plan_template(config)
        write_private_json(roots.control / "plan-template.json", template, create_once=True)
        request = create_approval_request(config, scenario_lock=lock)
        write_private_json(roots.control / "approval-request.json", request, create_once=True)
        success_payload = {
            "schema_version": "live-e2e.invocation-a.terminal.v1",
            "verdict": config.authority.invocation_a_terminal,
            "fault_injections": 0,
            "provider_calls": 0,
            "model_calls": 0,
            "forward_mutations": 0,
            "rollback_mutations": 0,
            "source_counts": _safe_source_counts(source_results),
            "approval_request_id": request.approval_request_id,
            "plan_template_sha256": request.plan_template_sha256,
        }
    except Exception as error:
        failure_type = type(error).__name__
    finally:
        if started:
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
                write_private_json(roots.invocation_a / "cleanup.json", cleanup, create_once=True)
            except Exception as error:
                failure_type = type(error).__name__
                cleanup_verdict = "BLOCKED"
    if success_payload is None or cleanup_verdict != "CLEAN":
        terminal = {
            "schema_version": "live-e2e.invocation-a.terminal.v1",
            "verdict": "LIVE_E2E_INVOCATION_A_TERMINAL_FAILURE",
            "fault_injections": 0,
            "provider_calls": 0,
            "model_calls": 0,
            "forward_mutations": 0,
            "rollback_mutations": 0,
            "failure_type": failure_type,
            "cleanup_verdict": cleanup_verdict,
        }
    else:
        terminal = dict(success_payload)
        terminal["cleanup_verdict"] = cleanup_verdict
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

    return record_human_approval(
        ApprovalRequest.model_validate(request),
        approver=approver,
        phrase=phrase,
        now=datetime.now(timezone.utc),
        destination=roots.control / "human-approval.json",
    )


def _load_approval(roots: E2EPrivateRoots) -> tuple[object, HumanApprovalRecord]:
    from ecomsre_live_sandbox.contracts import ApprovalRequest

    request_path = roots.control / "approval-request.json"
    approval_path = roots.control / "human-approval.json"
    if any(path.is_symlink() or not path.is_file() for path in (request_path, approval_path)):
        raise RuntimeError("Invocation B lacks an exact human approval record")
    return (
        ApprovalRequest.model_validate_json(request_path.read_text(encoding="utf-8")),
        HumanApprovalRecord.model_validate_json(approval_path.read_text(encoding="utf-8")),
    )


def _public_result(config: E2EConfig, terminal: Mapping[str, object]) -> dict[str, object]:
    value = {
        "schema_version": "live-e2e.public.v1",
        "version": config.authority.version,
        "verdict": config.authority.invocation_b_success,
        "claim_boundary": list(config.reporting.claim_boundary),
        "source_counts": terminal.get("source_counts"),
        "provider_calls": terminal.get("provider_calls"),
        "model_calls": terminal.get("model_calls"),
        "forward_mutations": terminal.get("forward_mutations"),
        "rollback_mutations": terminal.get("rollback_mutations"),
        "cleanup_verdict": terminal.get("cleanup_verdict"),
    }
    from ecomsre_live_sandbox.e2e_contracts import scan_public_e2e_payload

    if scan_public_e2e_payload(value):
        raise RuntimeError("public E2E projection leaked a protected surface")
    return value


def _write_new_public(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())


def _write_public_outputs(config: E2EConfig, terminal: Mapping[str, object]) -> tuple[str, str, str]:
    public = _public_result(config, terminal)
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
            "The single human-approved local sandbox run injected one frozen fault, "
            "executed one A0 diagnosis call, admitted one allowlisted baseline restore, "
            "and independently verified recovery. This is not production or external-benchmark evidence.\n"
        ).encode("utf-8"),
    )
    _write_new_public(
        paths[2],
        (
            "# Live Fault → A0 → Controlled Remediation — Human Brief\n\n"
            "A single local, human-approved sandbox execution completed the bounded fault, "
            "diagnosis, policy, one restoration mutation, recovery verification, and owned cleanup. "
            "It makes no production, autonomous-operation, security-detection, or external-benchmark claim.\n"
        ).encode("utf-8"),
    )
    return tuple(path.relative_to(config.repository_root).as_posix() for path in paths)  # type: ignore[return-value]


def run_invocation_b(config: E2EConfig, roots: E2EPrivateRoots) -> dict[str, object]:
    """Perform the single approved positive run. Any failure is terminal for this root."""
    roots.prepare()
    if (roots.invocation_b / "terminal.json").exists():
        raise RuntimeError("Invocation B is create-once and already consumed")
    lock = _verify_scenario_lock(config, roots)
    _require_invocation_a_success(config, roots)
    request, approval = _load_approval(roots)
    provider = _provider(config)
    synthetic = _synthetic_provider_context(config)
    preflight = provider.diagnose(synthetic)  # exactly one non-scored Provider call
    if provider.calls != 1 or not provider.usage_known or provider.last_usage_tokens is None:
        raise RuntimeError("synthetic Provider preflight did not return known bounded usage")
    write_private_json(
        roots.provider / "synthetic-preflight.json",
        {"request_sha256": provider.last_request_sha256, "diagnosis": preflight, "usage_tokens": provider.last_usage_tokens},
        create_once=True,
    )
    time.sleep(config.sandbox.budget.minimum_request_spacing_seconds)
    environment = SandboxEnvironment(
        repository_root=config.repository_root,
        bundle=config.sandbox,
        flagd_directory=roots.control / "flagd",
    )
    controller: SandboxFaultController | None = None
    started = False
    cleanup = None
    terminal: dict[str, object] = {
        "schema_version": "live-e2e.invocation-b.terminal.v1",
        "verdict": "LIVE_E2E_TERMINAL_FAILURE",
        "provider_calls": 1,
        "model_calls": 0,
        "fault_injections": 0,
        "forward_mutations": 0,
        "rollback_mutations": 0,
    }
    try:
        docker = environment.verify_local_docker()
        environment.verify_upstream()
        resolved, raw_compose = environment.resolve()
        write_private_json(roots.invocation_b / "resolved-compose.json", raw_compose, create_once=True)
        environment.verify_cached_images(resolved, roots.control)
        controller = _make_controller(config, roots, resolved.endpoints)
        environment.start()
        started = True
        environment.wait_healthy()
        time.sleep(config.sandbox.verification.minimum_stabilization_seconds)
        if controller.read_current().document_sha256 != config.sandbox.scenario.baseline_document_sha256:
            raise RuntimeError("Invocation B baseline configuration differs before fault")
        baseline = (
            _capture_sli_window(config, resolved.endpoints.prometheus, phase="BASELINE"),
            _capture_sli_window(config, resolved.endpoints.prometheus, phase="BASELINE"),
        )
        baseline_snapshot = _broad_metric_snapshot(resolved.endpoints.prometheus, at=baseline[-1].ended_at)
        fault_state = controller.inject_fault()
        if fault_state.document_sha256 != config.sandbox.scenario.fault_document_sha256:
            raise RuntimeError("frozen fault injection did not reach the exact fault document")
        terminal["fault_injections"] = 1
        time.sleep(config.sandbox.verification.minimum_stabilization_seconds)
        fault = (
            _capture_sli_window(config, resolved.endpoints.prometheus, phase="FAULT"),
            _capture_sli_window(config, resolved.endpoints.prometheus, phase="FAULT"),
        )
        if not fault_impact_passed(baseline, fault, config.sandbox):
            raise RuntimeError("two-window fault impact gate did not pass")
        fault_snapshot = _broad_metric_snapshot(resolved.endpoints.prometheus, at=fault[-1].ended_at)
        time.sleep(15)
        source_results = _capture_source_readiness(config, roots, label="invocation-b", endpoints=resolved.endpoints)
        observations = tuple(
            LiveMetricObservation(
                service_name=service,
                baseline_requests=baseline_snapshot.get(service, (0.0, 0.0, 0.0))[0],
                baseline_errors=baseline_snapshot.get(service, (0.0, 0.0, 0.0))[1],
                fault_requests=fault_values[0],
                fault_errors=fault_values[1],
                baseline_p95_ms=baseline_snapshot.get(service, (0.0, 0.0, 0.0))[2],
                fault_p95_ms=fault_values[2],
            )
            for service, fault_values in sorted(fault_snapshot.items())
            if baseline_snapshot.get(service, (0.0, 0.0, 0.0))[0] > 0
        )
        top_services = tuple(item.service_name for item in sorted(observations, key=lambda item: item.fault_errors / item.fault_requests, reverse=True)[:2])
        trace_services = tuple(dict.fromkeys(("checkout", *top_services)))[: config.projection.trace_query_limit]
        logs = _capture_broad_logs(
            resolved.endpoints.opensearch,
            window_start=fault[0].started_at,
            window_end=fault[-1].ended_at,
            maximum_hits=config.projection.log_raw_hit_limit,
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
        context = build_live_a0_context(
            opaque_case_id="rca100-case-0002",
            window_start=baseline[0].started_at,
            window_end=fault[-1].ended_at,
            metrics=observations,
            logs=logs,
            traces=traces,
            projection=config.projection,
        )
        _write_model_evidence_index(roots, context, raw_observations=raw_observations)
        write_private_json(roots.provider / "live-context.json", context, create_once=True)
        diagnosis = _diagnosis_from_initial(provider, context)
        if provider.calls != 2 or not provider.usage_known:
            raise RuntimeError("live A0 call did not preserve the one-preflight plus one-live budget")
        terminal["provider_calls"] = 2
        terminal["model_calls"] = 1
        write_private_json(roots.provider / "live-diagnosis.json", diagnosis, create_once=True)
        diagnosis_gate = evaluate_diagnosis_gate(diagnosis, config.sandbox)
        if not diagnosis_gate.passed:
            raise RuntimeError("A0 diagnosis gate denied controlled remediation")
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
        write_private_json(roots.invocation_b / "plan.json", plan, create_once=True)
        write_private_json(roots.invocation_b / "policy.json", policy, create_once=True)
        receipt = LocalSandboxRestrictedExecutor().execute(
            plan=plan,
            policy=policy,
            controller=controller,
            mutation_counter=ForwardMutationCounter(roots.journal / "forward-mutation.txt"),
        )
        terminal["forward_mutations"] = 1
        write_private_json(roots.invocation_b / "execution-receipt.json", receipt, create_once=True)
        time.sleep(config.sandbox.verification.minimum_stabilization_seconds)
        recovery = (
            _capture_sli_window(config, resolved.endpoints.prometheus, phase="RECOVERY"),
            _capture_sli_window(config, resolved.endpoints.prometheus, phase="RECOVERY"),
        )
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
            raise RuntimeError("independent recovery verification did not pass")
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
        terminal["failure_type"] = type(error).__name__
    finally:
        if started:
            baseline_restored = False
            if controller is not None:
                try:
                    if controller.read_current().document_sha256 != config.sandbox.scenario.baseline_document_sha256:
                        controller.restore_baseline()
                    baseline_restored = True
                except Exception:
                    baseline_restored = False
            cleanup = environment.cleanup(baseline_restored=baseline_restored)
            terminal["cleanup_verdict"] = cleanup.verdict
        write_private_json(roots.invocation_b / "terminal.json", terminal, create_once=True)
        roots.verify()
    if terminal.get("verdict") == config.authority.invocation_b_success and terminal.get("cleanup_verdict") == "CLEAN":
        terminal["public_outputs"] = _write_public_outputs(config, terminal)
    return terminal


__all__ = [
    "build_plan_template",
    "record_human_approval_for_invocation_b",
    "run_invocation_a",
    "run_invocation_b",
    "scenario_lock_manifest",
]
