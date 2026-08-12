"""Ordered three-source collection for the live E2E v4 lifecycle."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Protocol, cast

from ecomsre_live_sandbox.contracts import (
    LocalEndpoints,
    canonical_sha256,
    write_private_json,
)
from ecomsre_live_sandbox.e2e_diagnostics import (
    DiagnosticFailureCode,
    DiagnosticStage,
)
from ecomsre_live_sandbox.e2e_telemetry import (
    LiveLogObservation,
    LiveMetricObservation,
    LiveTraceObservation,
    build_live_a0_context,
    scan_model_projection,
    select_trace_candidate_services,
)
from ecomsre_live_sandbox.e2e_v1 import (
    _broad_metric_snapshot,
    _capture_broad_logs,
    _capture_broad_traces,
    _seal_model_evidence_resolver,
    _write_model_evidence_index,
)
from ecomsre_live_sandbox.instrumentation_v2 import (
    EvidenceResolver,
    InstrumentationConfig,
    LogsSourceProbe,
    MetricsSourceProbe,
    PrivateArtifactStore,
    SourceProbe,
    SourceProbeResult,
    SourceProbeStatus,
    TracesSourceProbe,
    _revalidate_refs,
    terminalize_source_probes,
)


JsonRequester = Callable[..., object]
ProjectionInputs = tuple[
    tuple[LiveMetricObservation, ...],
    tuple[LiveLogObservation, ...],
    tuple[LiveTraceObservation, ...],
]
ProjectionCollector = Callable[
    [LocalEndpoints, datetime, datetime, object], ProjectionInputs
]


class StageTracker(Protocol):
    def execute(
        self,
        stage: DiagnosticStage,
        operation: Callable[[], Any],
        *,
        failure_code: DiagnosticFailureCode | None = None,
        input_value: object | None = None,
        safe_aggregate: Mapping[str, object] | None = None,
    ) -> Any: ...

    def pass_stage(
        self,
        stage: DiagnosticStage,
        *,
        output_value: object | None = None,
        safe_aggregate: Mapping[str, object] | None = None,
    ) -> None: ...


@dataclass(frozen=True, slots=True)
class OrderedSourceEvidence:
    source_results: tuple[SourceProbeResult, SourceProbeResult, SourceProbeResult]
    source_counts: dict[str, int]
    invalid_refs: int
    all_refs_resolve: bool
    visible_service_count: int
    scenario_truth_leaked: bool
    projection_sha256: str

    @property
    def metrics_status(self) -> str:
        return self.source_results[0].status.value

    @property
    def logs_status(self) -> str:
        return self.source_results[1].status.value

    @property
    def traces_status(self) -> str:
        return self.source_results[2].status.value


def _execute(
    tracker: StageTracker | None,
    stage: DiagnosticStage,
    operation: Callable[[], Any],
    *,
    failure_code: DiagnosticFailureCode,
    safe_aggregate: Mapping[str, object] | None = None,
) -> Any:
    if tracker is None:
        return operation()
    return tracker.execute(
        stage,
        operation,
        failure_code=failure_code,
        safe_aggregate=safe_aggregate,
    )


def _pass(
    tracker: StageTracker | None,
    stage: DiagnosticStage,
    *,
    output_value: object | None = None,
    safe_aggregate: Mapping[str, object] | None = None,
) -> None:
    if tracker is not None:
        tracker.pass_stage(
            stage,
            output_value=output_value,
            safe_aggregate=safe_aggregate,
        )


def _default_projection_inputs(
    endpoints: LocalEndpoints,
    window_start: datetime,
    window_end: datetime,
    projection: object,
) -> ProjectionInputs:
    snapshot = _broad_metric_snapshot(endpoints.prometheus, at=window_end)
    metrics = tuple(
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
    maximum_hits = int(getattr(projection, "log_raw_hit_limit"))
    logs = _capture_broad_logs(
        endpoints.opensearch,
        window_start=window_start,
        window_end=window_end,
        maximum_hits=maximum_hits,
    )
    trace_query_limit = int(getattr(projection, "trace_query_limit"))
    trace_evidence_limit = int(getattr(projection, "trace_evidence_limit"))
    trace_services = select_trace_candidate_services(
        metrics=metrics,
        logs=logs,
        additional_limit=max(0, trace_query_limit - 1),
    )
    traces = _capture_broad_traces(
        endpoints.jaeger,
        services=trace_services,
        window_start=window_start,
        window_end=window_end,
        maximum_queries=trace_query_limit,
        maximum_evidence=trace_evidence_limit,
    )
    return metrics, logs, traces


def _combined_resolver(
    stores: Mapping[str, PrivateArtifactStore],
    *,
    common_root: Path,
) -> EvidenceResolver:
    records: dict[str, object] = {}
    for source_name, store in stores.items():
        resolver = EvidenceResolver.from_file(store.seal())
        for reference, metadata in resolver.records.items():
            if reference in records:
                raise ValueError("duplicate evidence ref")
            payload = metadata.model_dump(mode="json")
            payload["private_artifact_relative_key"] = (
                Path(source_name) / str(payload["private_artifact_relative_key"])
            ).as_posix()
            records[reference] = payload
    path = common_root / "source-resolver.json"
    write_private_json(
        path,
        {
            "schema_version": "live-telemetry.evidence-resolver.v2",
            "records": records,
        },
        create_once=True,
    )
    return EvidenceResolver.from_file(path)


def collect_ordered_source_batch(
    *,
    instrumentation: InstrumentationConfig,
    endpoints: LocalEndpoints,
    telemetry_root: Path,
    run_root: Path,
    run_id: str,
    projection: object,
    tracker: StageTracker | None = None,
    sleep: Callable[[float], None],
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    metrics_request_json: JsonRequester | None = None,
    logs_request_json: JsonRequester | None = None,
    traces_request_json: JsonRequester | None = None,
    projection_collector: ProjectionCollector = _default_projection_inputs,
) -> OrderedSourceEvidence:
    """Run exactly one ordered Metrics/Logs/Traces batch and gate afterward."""

    _pass(tracker, DiagnosticStage.SOURCE_CAPTURE_WINDOW_STARTED)

    def capture_window() -> tuple[datetime, datetime]:
        window_start = now()
        sleep(instrumentation.readiness.capture_window_seconds)
        window_end = now()
        sleep(instrumentation.readiness.ingestion_grace_seconds)
        if window_end <= window_start:
            raise ValueError("source capture window did not advance")
        return window_start, window_end

    window_start, window_end = _execute(
        tracker,
        DiagnosticStage.SOURCE_CAPTURE_WINDOW_COMPLETED,
        capture_window,
        failure_code=DiagnosticFailureCode.SOURCE_BATCH_CONTRACT_FAILED,
    )
    common_root = telemetry_root / run_id
    stores = {
        "metrics": PrivateArtifactStore(common_root / "metrics"),
        "logs": PrivateArtifactStore(common_root / "logs"),
        "traces": PrivateArtifactStore(common_root / "traces"),
    }

    if metrics_request_json is None:
        metrics = MetricsSourceProbe(
            endpoint=endpoints.prometheus,
            target_service=instrumentation.environment.target_service,
            config=instrumentation.sources.prometheus,
            readiness=instrumentation.readiness,
            store=stores["metrics"],
            window_start=window_start,
            window_end=window_end,
            sleep=sleep,
        )
    else:
        metrics = MetricsSourceProbe(
            endpoint=endpoints.prometheus,
            target_service=instrumentation.environment.target_service,
            config=instrumentation.sources.prometheus,
            readiness=instrumentation.readiness,
            store=stores["metrics"],
            window_start=window_start,
            window_end=window_end,
            sleep=sleep,
            request_json=metrics_request_json,
        )
    _pass(tracker, DiagnosticStage.METRICS_PROBE_CREATED)
    if logs_request_json is None:
        logs = LogsSourceProbe(
            endpoint=endpoints.opensearch,
            target_service=instrumentation.environment.target_service,
            config=instrumentation.sources.opensearch,
            readiness=instrumentation.readiness,
            store=stores["logs"],
            window_start=window_start,
            window_end=window_end,
            sleep=sleep,
        )
    else:
        logs = LogsSourceProbe(
            endpoint=endpoints.opensearch,
            target_service=instrumentation.environment.target_service,
            config=instrumentation.sources.opensearch,
            readiness=instrumentation.readiness,
            store=stores["logs"],
            window_start=window_start,
            window_end=window_end,
            sleep=sleep,
            request_json=logs_request_json,
        )
    _pass(tracker, DiagnosticStage.LOGS_PROBE_CREATED)
    if traces_request_json is None:
        traces = TracesSourceProbe(
            endpoint=endpoints.jaeger,
            target_service=instrumentation.environment.target_service,
            config=instrumentation.sources.jaeger,
            readiness=instrumentation.readiness,
            store=stores["traces"],
            window_start=window_start,
            window_end=window_end,
            sleep=sleep,
        )
    else:
        traces = TracesSourceProbe(
            endpoint=endpoints.jaeger,
            target_service=instrumentation.environment.target_service,
            config=instrumentation.sources.jaeger,
            readiness=instrumentation.readiness,
            store=stores["traces"],
            window_start=window_start,
            window_end=window_end,
            sleep=sleep,
            request_json=traces_request_json,
        )
    _pass(tracker, DiagnosticStage.TRACES_PROBE_CREATED)
    probes = (metrics, logs, traces)
    _pass(tracker, DiagnosticStage.SOURCE_BATCH_TERMINALIZATION_STARTED)
    raw_results = _execute(
        tracker,
        DiagnosticStage.SOURCE_BATCH_TERMINALIZATION_COMPLETED,
        lambda: terminalize_source_probes(
            cast(tuple[SourceProbe, SourceProbe, SourceProbe], probes),
            window_start=window_start,
            window_end=window_end,
        ),
        failure_code=DiagnosticFailureCode.SOURCE_BATCH_CONTRACT_FAILED,
    )
    for stage, result in zip(
        (
            DiagnosticStage.METRICS_PREFLIGHT_COMPLETED,
            DiagnosticStage.LOGS_PREFLIGHT_COMPLETED,
            DiagnosticStage.TRACES_PREFLIGHT_COMPLETED,
        ),
        raw_results,
        strict=True,
    ):
        _pass(
            tracker,
            stage,
            output_value=result.status.value,
            safe_aggregate={
                "status": result.status.value,
                "target_record_count": result.target_record_count,
                "attempt_count": result.attempt_count,
            },
        )

    def resolve() -> tuple[
        tuple[SourceProbeResult, SourceProbeResult, SourceProbeResult], bool
    ]:
        resolver = _combined_resolver(stores, common_root=common_root)
        return _revalidate_refs(
            raw_results,
            resolver=resolver,
            store_root=common_root,
        )

    source_results, all_refs_resolve = _execute(
        tracker,
        DiagnosticStage.EVIDENCE_RESOLUTION_COMPLETED,
        resolve,
        failure_code=DiagnosticFailureCode.EVIDENCE_RESOLUTION_FAILED,
    )
    invalid_refs = sum(item.invalid_ref_count for item in source_results)
    write_private_json(
        run_root / "source-results.json",
        {
            "schema_version": "live-e2e.source-results.v4",
            "results": [item.model_dump(mode="json") for item in source_results],
            "all_refs_resolve": all_refs_resolve,
            "invalid_ref_count": invalid_refs,
        },
        create_once=True,
    )

    def require_sources() -> None:
        if (
            not all_refs_resolve
            or invalid_refs != 0
            or any(
                item.status is not SourceProbeStatus.AVAILABLE
                or item.target_record_count <= 0
                for item in source_results
            )
        ):
            raise RuntimeError("one or more live telemetry sources are unavailable")

    _execute(
        tracker,
        DiagnosticStage.SOURCE_AVAILABILITY_GATE_EVALUATED,
        require_sources,
        failure_code=DiagnosticFailureCode.LIVE_TELEMETRY_SOURCE_GATE_NOT_PASSED,
        safe_aggregate={
            item.source: {
                "status": item.status.value,
                "target_record_count": item.target_record_count,
            }
            for item in source_results
        },
    )

    _pass(tracker, DiagnosticStage.MULTISERVICE_PROJECTION_STARTED)

    def project() -> tuple[int, str, bool]:
        metrics_input, logs_input, traces_input = projection_collector(
            endpoints,
            window_start,
            window_end,
            projection,
        )
        roots = SimpleNamespace(telemetry=common_root)
        bound_metrics, bound_logs, bound_traces, resolver_refs = (
            _seal_model_evidence_resolver(
                cast(Any, roots),
                label="projection",
                window_start=window_start,
                window_end=window_end,
                metrics=metrics_input,
                logs=logs_input,
                traces=traces_input,
            )
        )
        context = build_live_a0_context(
            window_start=window_start,
            window_end=window_end,
            metrics=bound_metrics,
            logs=bound_logs,
            traces=bound_traces,
            resolvable_refs=resolver_refs,
            projection=cast(Any, projection),
        )
        findings = scan_model_projection(context.model_dump(mode="json"))
        if findings:
            raise RuntimeError("control truth appeared in the model-facing projection")
        raw_observations = {
            "metrics": [item.model_dump(mode="json") for item in metrics_input],
            "logs": [item.model_dump(mode="json") for item in logs_input],
            "traces": [item.model_dump(mode="json") for item in traces_input],
        }
        _write_model_evidence_index(
            cast(Any, roots),
            context,
            raw_observations=raw_observations,
        )
        return len(context.visible_entities), canonical_sha256(context), False

    visible_service_count, projection_sha256, scenario_truth_leaked = _execute(
        tracker,
        DiagnosticStage.MULTISERVICE_PROJECTION_COMPLETED,
        project,
        failure_code=DiagnosticFailureCode.MULTISERVICE_PROJECTION_FAILED,
    )
    return OrderedSourceEvidence(
        source_results=source_results,
        source_counts={item.source: item.target_record_count for item in source_results},
        invalid_refs=invalid_refs,
        all_refs_resolve=all_refs_resolve,
        visible_service_count=visible_service_count,
        scenario_truth_leaked=scenario_truth_leaked,
        projection_sha256=projection_sha256,
    )


__all__ = [
    "OrderedSourceEvidence",
    "ProjectionCollector",
    "collect_ordered_source_batch",
]
