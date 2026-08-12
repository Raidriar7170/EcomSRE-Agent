"""Staged no-fault diagnostic and canonical lifecycle for live E2E v3."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
from typing import Any, cast

from ecomsre_live_sandbox.contracts import (
    ApprovalRequest,
    HumanApprovalRecord,
    LiveRemediationPlan,
    canonical_sha256,
    ensure_private_directory,
    file_sha256,
    write_private_json,
)
from ecomsre_live_sandbox.e2e_diagnostics import (
    DiagnosticCommandIdentity,
    DiagnosticEventStatus,
    DiagnosticExceptionReference,
    DiagnosticFailureCode,
    DiagnosticJournal,
    DiagnosticRunKind,
    DiagnosticStage,
    ExceptionArtifactStore,
    RecordingCommandRunner,
    failure_code_for_stage,
)
from ecomsre_live_sandbox.e2e_telemetry import (
    LiveMetricObservation,
    build_live_a0_context,
    scan_model_projection,
    select_trace_candidate_services,
)
from ecomsre_live_sandbox.e2e_v1 import (
    _broad_metric_snapshot,
    _capture_broad_logs,
    _capture_broad_traces,
    _capture_e2e_window,
    _capture_sli_window,
    _diagnosis_from_initial,
    _make_controller,
    _provider,
    _safe_source_counts,
    _seal_model_evidence_resolver,
    _synthetic_provider_context,
    _write_model_evidence_index,
)
from ecomsre_live_sandbox.e2e_source_batch import collect_ordered_source_batch
from ecomsre_live_sandbox.fault_projection import build_fault_time_a0_context
from ecomsre_live_sandbox.e2e_v3_contracts import (
    E2EV3Config,
    E2EV3PrivateRoots,
    create_approval_request,
    record_human_approval,
)
from ecomsre_live_sandbox.environment import SandboxEnvironment
from ecomsre_live_sandbox.image_authority import (
    ComposeIdentities,
    ImageAuthority,
    RunImageVerification,
    compose_identities,
    ensure_image_authority,
    write_run_image_verification,
)
from ecomsre_live_sandbox.control import (
    ForwardMutationCounter,
    IndependentVerifier,
    LocalSandboxRestrictedExecutor,
    build_plan,
    compensate_rollback,
    evaluate_diagnosis_gate,
    evaluate_policy,
    fault_impact_passed,
)
from ecomsre_live_sandbox.e2e_contracts import scan_public_e2e_payload
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
from ecomsre_live_sandbox.invocation_b_verdicts import (
    get_invocation_b_verdict_policy,
)
from ecomsre_live_sandbox.invocation_b_assurance import (
    write_fault_time_context_evidence,
)


E2E_V3_CONFIG_RELATIVE = Path("config/live-fault-a0-controlled-remediation-e2e-v3")
V3_CONFIG_RELATIVE = Path("config/live-telemetry-instrumentation-v3")


def _schema_suffix(config: object) -> str:
    version = getattr(getattr(config, "authority", None), "version", "")
    if str(version).endswith("e2e-v6"):
        return "v6"
    if str(version).endswith("e2e-v5"):
        return "v5"
    if str(version).endswith("e2e-v4"):
        return "v4"
    return "v3"


@dataclass(frozen=True, slots=True)
class NoFaultEvidence:
    metrics_status: str
    logs_status: str
    traces_status: str
    source_counts: dict[str, int]
    invalid_refs: int
    visible_service_count: int
    scenario_truth_leaked: bool
    projection_sha256: str


@dataclass(slots=True)
class _RunState:
    compose_start_requested: bool = False
    compose_start_returned: bool = False
    compose_start_return_code: int | None = None
    cleanup_required: bool = False
    owned_resources_after_start: dict[str, int] = field(
        default_factory=lambda: {"container": 0, "network": 0, "volume": 0}
    )
    service_health_wait_started: bool = False
    services_healthy: bool = False
    baseline_verified: bool = False
    metrics_status: str = "NOT_STARTED"
    logs_status: str = "NOT_STARTED"
    traces_status: str = "NOT_STARTED"
    projection_completed: bool = False


class _StageTracker:
    def __init__(self, journal: DiagnosticJournal, exceptions: ExceptionArtifactStore) -> None:
        self.journal = journal
        self.exceptions = exceptions
        self.failed_stage: DiagnosticStage | None = None
        self.failure_code: DiagnosticFailureCode | None = None
        self.exception: DiagnosticExceptionReference | None = None
        self.root_last_completed_stage: DiagnosticStage | None = None
        self._recorded_stages: set[DiagnosticStage] = set()

    def has_stage(self, stage: DiagnosticStage) -> bool:
        return stage in self._recorded_stages

    def execute(
        self,
        stage: DiagnosticStage,
        operation: Callable[[], Any],
        *,
        failure_code: DiagnosticFailureCode | None = None,
        input_value: object | None = None,
        safe_aggregate: Mapping[str, object] | None = None,
    ) -> Any:
        started_at = datetime.now(timezone.utc)
        monotonic_start = time.monotonic()
        self.journal.record(
            stage=stage,
            status=DiagnosticEventStatus.STARTED,
            started_at=started_at,
            input_value=input_value,
            safe_aggregate=safe_aggregate,
        )
        self._recorded_stages.add(stage)
        try:
            output = operation()
        except Exception as error:
            self._capture_failure(
                error,
                stage=stage,
                failure_code=failure_code or failure_code_for_stage(stage),
                started_at=started_at,
                monotonic_start=monotonic_start,
            )
            raise
        self.journal.record(
            stage=stage,
            status=DiagnosticEventStatus.PASSED,
            started_at=started_at,
            ended_at=datetime.now(timezone.utc),
            monotonic_duration_seconds=time.monotonic() - monotonic_start,
            output_value=output,
            safe_aggregate=safe_aggregate,
        )
        if self.failed_stage is None:
            self.root_last_completed_stage = stage
        return output

    def pass_stage(
        self,
        stage: DiagnosticStage,
        *,
        output_value: object | None = None,
        safe_aggregate: Mapping[str, object] | None = None,
    ) -> None:
        self.execute(
            stage,
            lambda: output_value,
            safe_aggregate=safe_aggregate,
        )

    def skip_stage(self, stage: DiagnosticStage, *, reason: str) -> None:
        now = datetime.now(timezone.utc)
        self.journal.record(
            stage=stage,
            status=DiagnosticEventStatus.SKIPPED,
            started_at=now,
            ended_at=now,
            safe_reason_code=reason,
        )
        self._recorded_stages.add(stage)

    def fail_external(
        self,
        error: BaseException,
        *,
        stage: DiagnosticStage,
        failure_code: DiagnosticFailureCode,
    ) -> None:
        started_at = datetime.now(timezone.utc)
        monotonic_start = time.monotonic()
        self.journal.record(
            stage=stage,
            status=DiagnosticEventStatus.STARTED,
            started_at=started_at,
        )
        self._recorded_stages.add(stage)
        self._capture_failure(
            error,
            stage=stage,
            failure_code=failure_code,
            started_at=started_at,
            monotonic_start=monotonic_start,
        )

    def _capture_failure(
        self,
        error: BaseException,
        *,
        stage: DiagnosticStage,
        failure_code: DiagnosticFailureCode,
        started_at: datetime,
        monotonic_start: float,
    ) -> None:
        reference = self.exceptions.capture(
            error,
            stage=stage,
            sequence=len(self.journal.path.read_text(encoding="utf-8").splitlines()) + 1,
        )
        self.journal.record(
            stage=stage,
            status=DiagnosticEventStatus.FAILED,
            started_at=started_at,
            ended_at=datetime.now(timezone.utc),
            monotonic_duration_seconds=time.monotonic() - monotonic_start,
            safe_reason_code=failure_code.value,
            artifact_refs=(reference.artifact_ref,),
        )
        if self.failed_stage is None:
            self.failed_stage = stage
            self.failure_code = failure_code
            self.exception = reference


def _git(repository_root: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ("git", *arguments),
        cwd=repository_root,
        check=False,
        capture_output=True,
        text=True,
        shell=False,
    )
    if completed.returncode != 0:
        raise RuntimeError("Git worktree boundary command failed")
    return completed.stdout.strip()


def _verify_worktree(config: E2EV3Config, *, clean_required: bool) -> str:
    branch = _git(config.repository_root, "branch", "--show-current")
    if branch != config.authority.branch:
        raise RuntimeError("v3 branch identity differs")
    head = _git(config.repository_root, "rev-parse", "HEAD")
    ancestor = subprocess.run(
        (
            "git",
            "merge-base",
            "--is-ancestor",
            config.authority.predecessor_head,
            head,
        ),
        cwd=config.repository_root,
        check=False,
        capture_output=True,
        text=True,
        shell=False,
    )
    if ancestor.returncode != 0:
        raise RuntimeError("v3 implementation is not rooted in the exact predecessor")
    if clean_required and _git(config.repository_root, "status", "--porcelain=v1"):
        raise RuntimeError("v3 canonical worktree is not clean")
    return head


def _default_worktree_verifier(config: E2EV3Config, clean_required: bool) -> str:
    return _verify_worktree(config, clean_required=clean_required)


def _read_budget(path: Path, *, maximum: int, schema_version: str) -> dict[str, object]:
    if path.exists() or path.is_symlink():
        if path.is_symlink() or not path.is_file():
            raise ValueError("private budget is not a regular file")
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("private budget is malformed")
        if value.get("schema_version") != schema_version or value.get("maximum") != maximum:
            raise ValueError("private budget binding differs")
        return value
    return {
        "schema_version": schema_version,
        "maximum": maximum,
        "consumed": 0,
        "runs": [],
    }


def _consume_probe_budget(config: E2EV3Config, roots: E2EV3PrivateRoots) -> tuple[int, str]:
    path = roots.control / "diagnostic-budget.json"
    budget = _read_budget(
        path,
        maximum=config.authority.maximum_no_fault_diagnostic_probes,
        schema_version="live-e2e.diagnostic-budget.v3",
    )
    runs = budget.get("runs")
    if not isinstance(runs, list):
        raise ValueError("diagnostic run history is malformed")
    if any(
        isinstance(item, Mapping)
        and item.get("verdict") == config.authority.diagnostic_success_terminal
        for item in runs
    ):
        raise RuntimeError("diagnostic preflight already passed; a second probe is forbidden")
    consumed = budget.get("consumed")
    if not isinstance(consumed, int) or consumed >= config.authority.maximum_no_fault_diagnostic_probes:
        raise RuntimeError("diagnostic preflight budget is exhausted")
    index = consumed + 1
    run_id = f"probe-{index:02d}"
    runs.append({"run_id": run_id, "verdict": "STARTED"})
    budget["consumed"] = index
    write_private_json(path, budget, create_once=False)
    return index, run_id


def _complete_probe_budget(
    roots: E2EV3PrivateRoots,
    *,
    run_id: str,
    verdict: str,
) -> None:
    path = roots.control / "diagnostic-budget.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    runs = value.get("runs") if isinstance(value, dict) else None
    if not isinstance(runs, list):
        raise ValueError("diagnostic run history is malformed")
    for item in runs:
        if isinstance(item, dict) and item.get("run_id") == run_id:
            item["verdict"] = verdict
            break
    else:
        raise ValueError("diagnostic run is absent from the frozen budget")
    write_private_json(path, value, create_once=False)


def _probe_one_source(
    probe: Any,
    *,
    store: PrivateArtifactStore,
    window_start: datetime,
    window_end: datetime,
) -> SourceProbeResult:
    raw = terminalize_source_probes(
        cast(Sequence[SourceProbe], (probe,)),
        window_start=window_start,
        window_end=window_end,
    )
    resolver = EvidenceResolver.from_file(store.seal())
    results, all_refs_resolve = _revalidate_refs(raw, resolver=resolver, store_root=store.root)
    result = results[0]
    if (
        not all_refs_resolve
        or result.status is not SourceProbeStatus.AVAILABLE
        or result.target_record_count <= 0
        or result.invalid_ref_count
    ):
        raise RuntimeError(f"{result.source} v3 source readiness is unavailable")
    return result


def _collect_no_fault_evidence(
    config: E2EV3Config,
    roots: E2EV3PrivateRoots,
    run_root: Path,
    tracker: _StageTracker,
    endpoints: object,
    sleep: Callable[[float], None],
) -> NoFaultEvidence:
    from ecomsre_live_sandbox.contracts import LocalEndpoints

    if not isinstance(endpoints, LocalEndpoints):
        raise ValueError("resolved local endpoints are invalid")
    v3 = load_instrumentation_config(config.repository_root / V3_CONFIG_RELATIVE)

    def capture_source_window() -> tuple[datetime, datetime]:
        window_start = datetime.now(timezone.utc)
        sleep(v3.readiness.capture_window_seconds)
        window_end = datetime.now(timezone.utc)
        sleep(v3.readiness.ingestion_grace_seconds)
        return window_start, window_end

    window_start, window_end = tracker.execute(
        DiagnosticStage.METRICS_PREFLIGHT_STARTED,
        capture_source_window,
        failure_code=DiagnosticFailureCode.METRICS_PREFLIGHT_FAILED,
    )
    metrics_store = PrivateArtifactStore(roots.telemetry / run_root.name / "metrics")
    metrics = MetricsSourceProbe(
        endpoint=endpoints.prometheus,
        target_service=v3.environment.target_service,
        config=v3.sources.prometheus,
        readiness=v3.readiness,
        store=metrics_store,
        window_start=window_start,
        window_end=window_end,
    )
    metrics_result = tracker.execute(
        DiagnosticStage.METRICS_PREFLIGHT_COMPLETED,
        lambda: _probe_one_source(
            metrics,
            store=metrics_store,
            window_start=window_start,
            window_end=window_end,
        ),
        failure_code=DiagnosticFailureCode.METRICS_PREFLIGHT_FAILED,
    )
    logs_store = PrivateArtifactStore(roots.telemetry / run_root.name / "logs")
    logs = LogsSourceProbe(
        endpoint=endpoints.opensearch,
        target_service=v3.environment.target_service,
        config=v3.sources.opensearch,
        readiness=v3.readiness,
        store=logs_store,
        window_start=window_start,
        window_end=window_end,
    )
    logs_result = tracker.execute(
        DiagnosticStage.LOGS_PREFLIGHT_STARTED,
        lambda: _probe_one_source(
            logs,
            store=logs_store,
            window_start=window_start,
            window_end=window_end,
        ),
        failure_code=DiagnosticFailureCode.LOGS_PREFLIGHT_FAILED,
    )
    tracker.pass_stage(
        DiagnosticStage.LOGS_PREFLIGHT_COMPLETED,
        safe_aggregate={"target_record_count": logs_result.target_record_count},
    )
    traces_store = PrivateArtifactStore(roots.telemetry / run_root.name / "traces")
    traces = TracesSourceProbe(
        endpoint=endpoints.jaeger,
        target_service=v3.environment.target_service,
        config=v3.sources.jaeger,
        readiness=v3.readiness,
        store=traces_store,
        window_start=window_start,
        window_end=window_end,
    )
    traces_result = tracker.execute(
        DiagnosticStage.TRACES_PREFLIGHT_STARTED,
        lambda: _probe_one_source(
            traces,
            store=traces_store,
            window_start=window_start,
            window_end=window_end,
        ),
        failure_code=DiagnosticFailureCode.TRACES_PREFLIGHT_FAILED,
    )
    tracker.pass_stage(
        DiagnosticStage.TRACES_PREFLIGHT_COMPLETED,
        safe_aggregate={"target_record_count": traces_result.target_record_count},
    )
    source_results = (metrics_result, logs_result, traces_result)
    invalid_refs = sum(item.invalid_ref_count for item in source_results)
    tracker.execute(
        DiagnosticStage.EVIDENCE_RESOLUTION_COMPLETED,
        lambda: None if invalid_refs == 0 else (_ for _ in ()).throw(
            RuntimeError("one or more v3 Evidence refs are invalid")
        ),
        failure_code=DiagnosticFailureCode.EVIDENCE_RESOLUTION_FAILED,
        safe_aggregate={"invalid_refs": invalid_refs},
    )

    def project() -> tuple[int, str, bool]:
        snapshot = _broad_metric_snapshot(endpoints.prometheus, at=window_end)
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
        broad_logs = _capture_broad_logs(
            endpoints.opensearch,
            window_start=window_start,
            window_end=window_end,
            maximum_hits=config.projection.log_raw_hit_limit,
        )
        trace_services = select_trace_candidate_services(
            metrics=observations,
            logs=broad_logs,
            additional_limit=max(0, config.projection.trace_query_limit - 1),
        )
        broad_traces = _capture_broad_traces(
            endpoints.jaeger,
            services=trace_services,
            window_start=window_start,
            window_end=window_end,
            maximum_queries=config.projection.trace_query_limit,
            maximum_evidence=config.projection.trace_evidence_limit,
        )
        raw_observations = {
            "metrics": [item.model_dump(mode="json") for item in observations],
            "logs": [item.model_dump(mode="json") for item in broad_logs],
            "traces": [item.model_dump(mode="json") for item in broad_traces],
        }
        bound_metrics, bound_logs, bound_traces, resolver_refs = _seal_model_evidence_resolver(
            cast(Any, roots),
            label=run_root.name,
            window_start=window_start,
            window_end=window_end,
            metrics=observations,
            logs=broad_logs,
            traces=broad_traces,
        )
        context = build_live_a0_context(
            window_start=window_start,
            window_end=window_end,
            metrics=bound_metrics,
            logs=bound_logs,
            traces=bound_traces,
            resolvable_refs=resolver_refs,
            projection=cast(Any, config.projection),
        )
        findings = scan_model_projection(context)
        if findings:
            raise RuntimeError("control truth appeared in the model-facing projection")
        _write_model_evidence_index(
            cast(Any, roots),
            context,
            raw_observations=raw_observations,
        )
        dumped = context.model_dump(mode="json")
        return len(context.visible_entities), canonical_sha256(dumped), False

    tracker.pass_stage(DiagnosticStage.MULTISERVICE_PROJECTION_STARTED)
    visible_count, projection_sha256, leaked = tracker.execute(
        DiagnosticStage.MULTISERVICE_PROJECTION_COMPLETED,
        project,
        failure_code=DiagnosticFailureCode.MULTISERVICE_PROJECTION_FAILED,
    )
    return NoFaultEvidence(
        metrics_status=metrics_result.status.value,
        logs_status=logs_result.status.value,
        traces_status=traces_result.status.value,
        source_counts={item.source: item.target_record_count for item in source_results},
        invalid_refs=invalid_refs,
        visible_service_count=visible_count,
        scenario_truth_leaked=leaked,
        projection_sha256=projection_sha256,
    )


def _fill_no_fault_stages(tracker: _StageTracker, evidence: NoFaultEvidence) -> None:
    values = {
        DiagnosticStage.METRICS_PREFLIGHT_STARTED: evidence.metrics_status,
        DiagnosticStage.METRICS_PREFLIGHT_COMPLETED: evidence.metrics_status,
        DiagnosticStage.LOGS_PREFLIGHT_STARTED: evidence.logs_status,
        DiagnosticStage.LOGS_PREFLIGHT_COMPLETED: evidence.logs_status,
        DiagnosticStage.TRACES_PREFLIGHT_STARTED: evidence.traces_status,
        DiagnosticStage.TRACES_PREFLIGHT_COMPLETED: evidence.traces_status,
        DiagnosticStage.EVIDENCE_RESOLUTION_COMPLETED: evidence.invalid_refs,
        DiagnosticStage.MULTISERVICE_PROJECTION_STARTED: evidence.visible_service_count,
        DiagnosticStage.MULTISERVICE_PROJECTION_COMPLETED: evidence.visible_service_count,
    }
    for stage, value in values.items():
        if not tracker.has_stage(stage):
            tracker.pass_stage(stage, output_value=value)


def _cleanup(
    *,
    tracker: _StageTracker,
    state: _RunState,
    environment: Any,
    controller: Any,
    baseline_sha256: str,
    run_root: Path,
) -> tuple[str, dict[str, object], str | None]:
    if not state.cleanup_required:
        return (
            "NOT_REQUIRED",
            {
                "baseline_restored": True,
                "owned_containers": 0,
                "owned_networks": 0,
                "owned_volumes": 0,
                "non_owned_resources_changed": False,
                "verdict": "NOT_REQUIRED",
            },
            None,
        )
    cleanup_failure: str | None = None
    tracker.pass_stage(DiagnosticStage.CLEANUP_STARTED)
    baseline_restored = False
    try:
        current = controller.read_current()
        if current.document_sha256 != baseline_sha256:
            controller.restore_baseline()
        baseline_restored = True
        tracker.pass_stage(DiagnosticStage.BASELINE_RESTORED)
    except Exception as error:
        cleanup_failure = DiagnosticFailureCode.CLEANUP_FAILED.value
        if tracker.failed_stage is None:
            tracker.fail_external(
                error,
                stage=DiagnosticStage.BASELINE_RESTORED,
                failure_code=DiagnosticFailureCode.CLEANUP_FAILED,
            )
    try:
        cleanup = environment.cleanup(baseline_restored=baseline_restored)
        cleanup_payload = cleanup.model_dump(mode="json")
        write_private_json(run_root / "cleanup.json", cleanup, create_once=True)
        if cleanup.verdict != "CLEAN":
            cleanup_failure = DiagnosticFailureCode.CLEANUP_FAILED.value
            if tracker.failed_stage is None:
                tracker.fail_external(
                    RuntimeError("owned cleanup did not reach CLEAN"),
                    stage=DiagnosticStage.CLEANUP_COMPLETED,
                    failure_code=DiagnosticFailureCode.CLEANUP_FAILED,
                )
        else:
            tracker.pass_stage(DiagnosticStage.CLEANUP_COMPLETED)
        return cleanup.verdict, cleanup_payload, cleanup_failure
    except Exception as error:
        cleanup_failure = DiagnosticFailureCode.CLEANUP_FAILED.value
        if tracker.failed_stage is None:
            tracker.fail_external(
                error,
                stage=DiagnosticStage.CLEANUP_COMPLETED,
                failure_code=DiagnosticFailureCode.CLEANUP_FAILED,
            )
        return (
            "BLOCKED",
            {
                "baseline_restored": baseline_restored,
                "owned_containers": None,
                "owned_networks": None,
                "owned_volumes": None,
                "non_owned_resources_changed": None,
                "verdict": "BLOCKED",
            },
            cleanup_failure,
        )


@dataclass(frozen=True, slots=True)
class _NoFaultRunResult:
    state: _RunState
    implementation_commit: str
    evidence: NoFaultEvidence | None
    resolved_compose_sha256: str | None
    image_authority: ImageAuthority | None
    image_verification: RunImageVerification | None
    cleanup_verdict: str
    cleanup_payload: dict[str, object]
    cleanup_failure_code: str | None


@dataclass(frozen=True, slots=True)
class _ImageRunEvidence:
    authority: ImageAuthority
    verification: RunImageVerification
    compose: ComposeIdentities


def _verify_v3_images(
    *,
    environment: Any,
    resolved: Any,
    raw_compose: Mapping[str, object],
    roots: E2EV3PrivateRoots,
    run_root: Path,
    run_id: str,
    run_kind: DiagnosticRunKind,
    tracker: _StageTracker,
    expected_structure_sha256: str | None = None,
    expected_authority_sha256: str | None = None,
) -> _ImageRunEvidence:
    flagd_directory = roots.runtime / run_id / "flagd"
    inspection = tracker.execute(
        DiagnosticStage.IMAGE_AUTHORITY_LOAD_STARTED,
        lambda: environment.inspect_cached_images(resolved),
        failure_code=DiagnosticFailureCode.IMAGE_AUTHORITY_MISMATCH,
    )
    authority_path = roots.control / "image-authority.json"

    def load_authority() -> ImageAuthority:
        value = ensure_image_authority(authority_path, inspection)
        if (
            expected_authority_sha256 is not None
            and value.authority_sha256 != expected_authority_sha256
        ):
            raise RuntimeError("shared Image Authority differs from Scenario Lock")
        return value

    if authority_path.exists() or authority_path.is_symlink():
        authority = tracker.execute(
            DiagnosticStage.IMAGE_AUTHORITY_VERIFIED,
            load_authority,
            failure_code=DiagnosticFailureCode.IMAGE_AUTHORITY_MISMATCH,
        )
    else:
        authority = tracker.execute(
            DiagnosticStage.IMAGE_AUTHORITY_CREATED,
            load_authority,
            failure_code=DiagnosticFailureCode.IMAGE_AUTHORITY_CREATION_FAILED,
        )
        tracker.pass_stage(
            DiagnosticStage.IMAGE_AUTHORITY_VERIFIED,
            output_value=authority.authority_sha256,
        )
    def resolve_identities() -> ComposeIdentities:
        value = compose_identities(
            raw_compose,
            private_root=roots.root,
            flagd_directory=flagd_directory,
        )
        if (
            expected_structure_sha256 is not None
            and value.structure_sha256 != expected_structure_sha256
        ):
            raise RuntimeError("resolved Compose structure differs from Scenario Lock")
        return value

    identities = tracker.execute(
        DiagnosticStage.COMPOSE_STRUCTURE_HASH_VERIFIED,
        resolve_identities,
        failure_code=DiagnosticFailureCode.COMPOSE_STRUCTURE_IDENTITY_MISMATCH,
    )
    verification = tracker.execute(
        DiagnosticStage.RUN_IMAGE_VERIFICATION_CREATED,
        lambda: write_run_image_verification(
            run_root / "image-verification.json",
            run_id=run_id,
            run_kind=run_kind.value,
            authority=authority,
            inspection=inspection,
            resolved_compose=raw_compose,
            private_root=roots.root,
            flagd_directory=flagd_directory,
        ),
        failure_code=DiagnosticFailureCode.RUN_IMAGE_VERIFICATION_WRITE_FAILED,
    )
    return _ImageRunEvidence(
        authority=authority,
        verification=verification,
        compose=identities,
    )


def _execute_no_fault_sequence(
    config: E2EV3Config,
    roots: E2EV3PrivateRoots,
    *,
    run_id: str,
    run_root: Path,
    tracker: _StageTracker,
    clean_required: bool,
    environment_factory: Callable[..., Any],
    controller_factory: Callable[..., Any],
    evidence_collector: Callable[..., NoFaultEvidence],
    sleep: Callable[[float], None],
    worktree_verifier: Callable[[E2EV3Config, bool], str],
    run_kind: DiagnosticRunKind = DiagnosticRunKind.CANONICAL_INVOCATION_A,
    fill_legacy_no_fault_stages: bool = True,
    expected_structure_sha256: str | None = None,
) -> _NoFaultRunResult:
    state = _RunState()

    def on_command_start(identity: DiagnosticCommandIdentity) -> None:
        if identity is DiagnosticCommandIdentity.COMPOSE_UP:
            state.compose_start_requested = True
            state.cleanup_required = True
            tracker.pass_stage(
                DiagnosticStage.COMPOSE_START_REQUESTED,
                safe_aggregate={"requested": True},
            )

    def on_command_return(
        identity: DiagnosticCommandIdentity,
        return_code: int | None,
        timed_out: bool,
    ) -> None:
        if identity is DiagnosticCommandIdentity.COMPOSE_UP:
            state.compose_start_returned = not timed_out
            state.compose_start_return_code = return_code
            if not timed_out and return_code == 0:
                tracker.pass_stage(
                    DiagnosticStage.COMPOSE_START_RETURNED,
                    safe_aggregate={"return_code": 0, "timed_out": False},
                )
        elif identity is DiagnosticCommandIdentity.COMPOSE_DOWN:
            if not timed_out and return_code == 0:
                tracker.pass_stage(
                    DiagnosticStage.COMPOSE_DOWN_RETURNED,
                    safe_aggregate={"return_code": 0, "timed_out": False},
                )

    runner = RecordingCommandRunner(
        run_root / "commands",
        on_start=on_command_start,
        on_return=on_command_return,
    )
    environment = environment_factory(
        repository_root=config.repository_root,
        bundle=config.sandbox,
        flagd_directory=roots.runtime / run_id / "flagd",
        runner=runner,
    )
    implementation_commit = _git(config.repository_root, "rev-parse", "HEAD")
    controller: Any = None
    evidence: NoFaultEvidence | None = None
    resolved_compose_sha256: str | None = None
    image_run: _ImageRunEvidence | None = None
    cleanup_verdict = "NOT_REQUIRED"
    cleanup_payload: dict[str, object] = {
        "baseline_restored": True,
        "owned_containers": 0,
        "owned_networks": 0,
        "owned_volumes": 0,
        "non_owned_resources_changed": False,
        "verdict": "NOT_REQUIRED",
    }
    cleanup_failure_code: str | None = None
    try:
        tracker.pass_stage(DiagnosticStage.PRIVATE_ROOT_BOUND)
        tracker.pass_stage(DiagnosticStage.AUTHORITY_VERIFIED)
        implementation_commit = tracker.execute(
            DiagnosticStage.WORKTREE_VERIFIED,
            lambda: worktree_verifier(config, clean_required),
            failure_code=(
                DiagnosticFailureCode.WORKTREE_NOT_CLEAN
                if clean_required
                else DiagnosticFailureCode.BRANCH_IDENTITY_MISMATCH
            ),
        )
        tracker.execute(
            DiagnosticStage.LOCAL_DOCKER_VERIFIED,
            environment.verify_local_docker,
            failure_code=DiagnosticFailureCode.DOCKER_AUTHORITY_UNAVAILABLE,
        )
        tracker.execute(
            DiagnosticStage.UPSTREAM_PIN_VERIFIED,
            environment.verify_upstream,
            failure_code=DiagnosticFailureCode.UPSTREAM_PIN_DRIFT,
        )
        resolved, raw_compose = tracker.execute(
            DiagnosticStage.COMPOSE_RESOLUTION_STARTED,
            environment.resolve,
            failure_code=DiagnosticFailureCode.COMPOSE_RESOLUTION_FAILED,
        )
        resolved_compose_sha256 = resolved.compose_sha256
        tracker.execute(
            DiagnosticStage.COMPOSE_RESOLVED,
            lambda: resolved.compose_sha256
            if len(resolved.compose_sha256) == 64
            else (_ for _ in ()).throw(RuntimeError("resolved Compose hash is invalid")),
            failure_code=DiagnosticFailureCode.RESOLVED_COMPOSE_DRIFT,
        )
        write_private_json(run_root / "resolved-compose.json", raw_compose, create_once=True)
        image_run = _verify_v3_images(
            environment=environment,
            resolved=resolved,
            raw_compose=raw_compose,
            roots=roots,
            run_root=run_root,
            run_id=run_id,
            run_kind=run_kind,
            tracker=tracker,
            expected_structure_sha256=expected_structure_sha256,
        )
        controller = tracker.execute(
            DiagnosticStage.FAULT_CONTROLLER_PREPARATION_STARTED,
            lambda: controller_factory(
                config,
                roots,
                resolved.endpoints,
                flagd_directory=roots.runtime / run_id / "flagd",
            ),
            failure_code=DiagnosticFailureCode.FAULT_CONTROLLER_PREPARATION_FAILED,
        )
        tracker.pass_stage(DiagnosticStage.FAULT_CONTROLLER_PREPARED)
        tracker.execute(
            DiagnosticStage.PORT_PREFLIGHT_STARTED,
            environment.verify_ports_available,
            failure_code=DiagnosticFailureCode.PORT_CONFLICT,
        )
        tracker.pass_stage(DiagnosticStage.PORTS_AVAILABLE)
        tracker.execute(
            DiagnosticStage.DOCKER_BASELINE_SNAPSHOT_CAPTURED,
            environment.snapshot_all_resources,
            failure_code=DiagnosticFailureCode.DOCKER_BASELINE_SNAPSHOT_FAILED,
        )
        try:
            environment.start()
        except Exception as error:
            failed_stage = (
                DiagnosticStage.COMPOSE_START_RETURNED
                if state.compose_start_returned
                else DiagnosticStage.COMPOSE_START_REQUESTED
            )
            tracker.fail_external(
                error,
                stage=failed_stage,
                failure_code=DiagnosticFailureCode.COMPOSE_UP_FAILED,
            )
            raise
        counts = tracker.execute(
            DiagnosticStage.OWNED_RESOURCE_INVENTORY_VERIFIED,
            lambda: environment.verify_owned_resources(require_complete=True),
            failure_code=DiagnosticFailureCode.OWNED_RESOURCE_INVENTORY_INCOMPLETE,
        )
        state.owned_resources_after_start = dict(counts)
        state.cleanup_required = state.cleanup_required or any(counts.values())
        state.service_health_wait_started = True
        health = tracker.execute(
            DiagnosticStage.SERVICE_HEALTH_WAIT_STARTED,
            environment.wait_healthy,
            failure_code=DiagnosticFailureCode.SERVICE_HEALTH_TIMEOUT,
        )
        state.services_healthy = bool(health) and all(health.values())
        tracker.execute(
            DiagnosticStage.SERVICES_HEALTHY,
            lambda: sum(health.values())
            if state.services_healthy
            else (_ for _ in ()).throw(RuntimeError("sandbox health result is incomplete")),
            failure_code=DiagnosticFailureCode.SERVICE_HEALTH_TIMEOUT,
            safe_aggregate={"healthy_services": sum(health.values())},
        )
        tracker.execute(
            DiagnosticStage.STABILIZATION_STARTED,
            lambda: sleep(config.sandbox.verification.minimum_stabilization_seconds),
        )
        tracker.pass_stage(DiagnosticStage.STABILIZATION_COMPLETED)
        baseline = tracker.execute(
            DiagnosticStage.BASELINE_CONFIGURATION_READ_STARTED,
            controller.read_current,
            failure_code=DiagnosticFailureCode.BASELINE_CONFIGURATION_UNAVAILABLE,
        )
        tracker.execute(
            DiagnosticStage.BASELINE_CONFIGURATION_VERIFIED,
            lambda: baseline.document_sha256
            if baseline.document_sha256 == config.sandbox.scenario.baseline_document_sha256
            else (_ for _ in ()).throw(RuntimeError("baseline document hash differs")),
            failure_code=DiagnosticFailureCode.BASELINE_CONFIGURATION_MISMATCH,
        )
        state.baseline_verified = True
        try:
            collector_kwargs: dict[str, object] = {}
            if _schema_suffix(config) in {"v5", "v6"}:
                collector_kwargs = {
                    "services_healthy_count": sum(health.values()),
                    "baseline_exact": True,
                }
            evidence = evidence_collector(
                config,
                roots,
                run_root,
                tracker,
                resolved.endpoints,
                sleep,
                **collector_kwargs,
            )
        except Exception as error:
            if tracker.failed_stage is None:
                tracker.fail_external(
                    error,
                    stage=DiagnosticStage.METRICS_PREFLIGHT_STARTED,
                    failure_code=DiagnosticFailureCode.METRICS_PREFLIGHT_FAILED,
                )
            raise
        if fill_legacy_no_fault_stages:
            _fill_no_fault_stages(tracker, evidence)
        state.metrics_status = evidence.metrics_status
        state.logs_status = evidence.logs_status
        state.traces_status = evidence.traces_status
        state.projection_completed = not hasattr(evidence, "readiness")
        source_results_path = run_root / "source-results.json"
        if not source_results_path.exists() and not source_results_path.is_symlink():
            write_private_json(
                source_results_path,
                {
                    "schema_version": "live-e2e.safe-source-results.v2",
                    "statuses": {
                        "METRICS": evidence.metrics_status,
                        "LOGS": evidence.logs_status,
                        "TRACES": evidence.traces_status,
                    },
                    "counts": evidence.source_counts,
                    "invalid_refs": evidence.invalid_refs,
                },
                create_once=True,
            )
        if not hasattr(evidence, "readiness"):
            write_private_json(
                run_root / "projection-summary.json",
                {
                    "schema_version": "live-e2e.safe-projection-summary.v2",
                    "visible_service_count": evidence.visible_service_count,
                    "scenario_truth_leaked": evidence.scenario_truth_leaked,
                    "projection_sha256": evidence.projection_sha256,
                },
                create_once=True,
            )
    except Exception:
        pass
    finally:
        cleanup_verdict, cleanup_payload, cleanup_failure_code = _cleanup(
            tracker=tracker,
            state=state,
            environment=environment,
            controller=controller,
            baseline_sha256=config.sandbox.scenario.baseline_document_sha256,
            run_root=run_root,
        )
    return _NoFaultRunResult(
        state=state,
        implementation_commit=implementation_commit,
        evidence=evidence,
        resolved_compose_sha256=resolved_compose_sha256,
        image_authority=None if image_run is None else image_run.authority,
        image_verification=None if image_run is None else image_run.verification,
        cleanup_verdict=cleanup_verdict,
        cleanup_payload=cleanup_payload,
        cleanup_failure_code=cleanup_failure_code,
    )


def run_diagnostic_preflight(
    config: E2EV3Config,
    roots: E2EV3PrivateRoots,
    *,
    environment_factory: Callable[..., Any] = SandboxEnvironment,
    controller_factory: Callable[..., Any] = _make_controller,
    evidence_collector: Callable[..., NoFaultEvidence] = _collect_no_fault_evidence,
    sleep: Callable[[float], None] = time.sleep,
    worktree_verifier: Callable[[E2EV3Config, bool], str] = _default_worktree_verifier,
) -> dict[str, object]:
    """Consume at most one new no-fault diagnostic probe and always clean after start."""
    roots.bind_lifecycle(config.authority, repository_root=config.repository_root)
    probe_index, run_id = _consume_probe_budget(config, roots)
    run_root = roots.probe_root(probe_index)
    for directory in (
        run_root,
        run_root / "commands",
        run_root / "exceptions",
        run_root / "snapshots",
    ):
        ensure_private_directory(directory)
    journal = DiagnosticJournal(
        run_root / "events.jsonl",
        run_kind=DiagnosticRunKind.DIAGNOSTIC_PROBE,
        run_id=run_id,
    )
    tracker = _StageTracker(journal, ExceptionArtifactStore(run_root / "exceptions"))
    state = _RunState()

    def on_command_start(identity: DiagnosticCommandIdentity) -> None:
        if identity is DiagnosticCommandIdentity.COMPOSE_UP:
            state.compose_start_requested = True
            state.cleanup_required = True
            tracker.pass_stage(
                DiagnosticStage.COMPOSE_START_REQUESTED,
                safe_aggregate={"requested": True},
            )

    def on_command_return(
        identity: DiagnosticCommandIdentity,
        return_code: int | None,
        timed_out: bool,
    ) -> None:
        if identity is DiagnosticCommandIdentity.COMPOSE_UP:
            state.compose_start_returned = not timed_out
            state.compose_start_return_code = return_code
            if not timed_out and return_code == 0:
                tracker.pass_stage(
                    DiagnosticStage.COMPOSE_START_RETURNED,
                    safe_aggregate={"return_code": 0, "timed_out": False},
                )
        elif identity is DiagnosticCommandIdentity.COMPOSE_DOWN:
            if not timed_out and return_code == 0:
                tracker.pass_stage(
                    DiagnosticStage.COMPOSE_DOWN_RETURNED,
                    safe_aggregate={"return_code": 0, "timed_out": False},
                )

    runner = RecordingCommandRunner(
        run_root / "commands",
        on_start=on_command_start,
        on_return=on_command_return,
    )
    environment = environment_factory(
        repository_root=config.repository_root,
        bundle=config.sandbox,
        flagd_directory=roots.runtime / run_id / "flagd",
        runner=runner,
    )
    implementation_commit = _git(config.repository_root, "rev-parse", "HEAD")
    controller: Any = None
    evidence: NoFaultEvidence | None = None
    image_run: _ImageRunEvidence | None = None
    cleanup_verdict = "NOT_REQUIRED"
    cleanup_payload: dict[str, object] = {
        "baseline_restored": True,
        "owned_containers": 0,
        "owned_networks": 0,
        "owned_volumes": 0,
        "non_owned_resources_changed": False,
        "verdict": "NOT_REQUIRED",
    }
    cleanup_failure_code: str | None = None
    try:
        tracker.pass_stage(DiagnosticStage.PRIVATE_ROOT_BOUND)
        tracker.pass_stage(DiagnosticStage.AUTHORITY_VERIFIED)
        implementation_commit = tracker.execute(
            DiagnosticStage.WORKTREE_VERIFIED,
            lambda: worktree_verifier(config, False),
            failure_code=DiagnosticFailureCode.BRANCH_IDENTITY_MISMATCH,
        )
        tracker.execute(
            DiagnosticStage.LOCAL_DOCKER_VERIFIED,
            environment.verify_local_docker,
            failure_code=DiagnosticFailureCode.DOCKER_AUTHORITY_UNAVAILABLE,
        )
        tracker.execute(
            DiagnosticStage.UPSTREAM_PIN_VERIFIED,
            environment.verify_upstream,
            failure_code=DiagnosticFailureCode.UPSTREAM_PIN_DRIFT,
        )
        resolved, raw_compose = tracker.execute(
            DiagnosticStage.COMPOSE_RESOLUTION_STARTED,
            environment.resolve,
            failure_code=DiagnosticFailureCode.COMPOSE_RESOLUTION_FAILED,
        )
        tracker.execute(
            DiagnosticStage.COMPOSE_RESOLVED,
            lambda: resolved.compose_sha256
            if len(resolved.compose_sha256) == 64
            else (_ for _ in ()).throw(RuntimeError("resolved Compose hash is invalid")),
            failure_code=DiagnosticFailureCode.RESOLVED_COMPOSE_DRIFT,
        )
        write_private_json(run_root / "resolved-compose.json", raw_compose, create_once=True)
        image_run = _verify_v3_images(
            environment=environment,
            resolved=resolved,
            raw_compose=raw_compose,
            roots=roots,
            run_root=run_root,
            run_id=run_id,
            run_kind=DiagnosticRunKind.DIAGNOSTIC_PROBE,
            tracker=tracker,
        )
        controller = tracker.execute(
            DiagnosticStage.FAULT_CONTROLLER_PREPARATION_STARTED,
            lambda: controller_factory(
                config,
                roots,
                resolved.endpoints,
                flagd_directory=roots.runtime / run_id / "flagd",
            ),
            failure_code=DiagnosticFailureCode.FAULT_CONTROLLER_PREPARATION_FAILED,
        )
        tracker.pass_stage(DiagnosticStage.FAULT_CONTROLLER_PREPARED)
        tracker.execute(
            DiagnosticStage.PORT_PREFLIGHT_STARTED,
            environment.verify_ports_available,
            failure_code=DiagnosticFailureCode.PORT_CONFLICT,
        )
        tracker.pass_stage(DiagnosticStage.PORTS_AVAILABLE)
        tracker.execute(
            DiagnosticStage.DOCKER_BASELINE_SNAPSHOT_CAPTURED,
            environment.snapshot_all_resources,
            failure_code=DiagnosticFailureCode.DOCKER_BASELINE_SNAPSHOT_FAILED,
        )
        try:
            environment.start()
        except Exception as error:
            failed_stage = (
                DiagnosticStage.COMPOSE_START_RETURNED
                if state.compose_start_returned
                else DiagnosticStage.COMPOSE_START_REQUESTED
            )
            tracker.fail_external(
                error,
                stage=failed_stage,
                failure_code=DiagnosticFailureCode.COMPOSE_UP_FAILED,
            )
            raise
        counts = tracker.execute(
            DiagnosticStage.OWNED_RESOURCE_INVENTORY_VERIFIED,
            lambda: environment.verify_owned_resources(require_complete=True),
            failure_code=DiagnosticFailureCode.OWNED_RESOURCE_INVENTORY_INCOMPLETE,
        )
        state.owned_resources_after_start = dict(counts)
        state.cleanup_required = state.cleanup_required or any(counts.values())
        state.service_health_wait_started = True
        health = tracker.execute(
            DiagnosticStage.SERVICE_HEALTH_WAIT_STARTED,
            environment.wait_healthy,
            failure_code=DiagnosticFailureCode.SERVICE_HEALTH_TIMEOUT,
        )
        state.services_healthy = bool(health) and all(health.values())
        if not state.services_healthy:
            raise RuntimeError("sandbox health result is incomplete")
        tracker.pass_stage(
            DiagnosticStage.SERVICES_HEALTHY,
            safe_aggregate={"healthy_services": sum(health.values())},
        )
        tracker.execute(
            DiagnosticStage.STABILIZATION_STARTED,
            lambda: sleep(config.sandbox.verification.minimum_stabilization_seconds),
        )
        tracker.pass_stage(DiagnosticStage.STABILIZATION_COMPLETED)
        baseline = tracker.execute(
            DiagnosticStage.BASELINE_CONFIGURATION_READ_STARTED,
            controller.read_current,
            failure_code=DiagnosticFailureCode.BASELINE_CONFIGURATION_UNAVAILABLE,
        )
        tracker.execute(
            DiagnosticStage.BASELINE_CONFIGURATION_VERIFIED,
            lambda: baseline.document_sha256
            if baseline.document_sha256 == config.sandbox.scenario.baseline_document_sha256
            else (_ for _ in ()).throw(RuntimeError("baseline document hash differs")),
            failure_code=DiagnosticFailureCode.BASELINE_CONFIGURATION_MISMATCH,
        )
        state.baseline_verified = True
        try:
            evidence = evidence_collector(
                config,
                roots,
                run_root,
                tracker,
                resolved.endpoints,
                sleep,
            )
        except Exception as error:
            if tracker.failed_stage is None:
                tracker.fail_external(
                    error,
                    stage=DiagnosticStage.METRICS_PREFLIGHT_STARTED,
                    failure_code=DiagnosticFailureCode.METRICS_PREFLIGHT_FAILED,
                )
            raise
        _fill_no_fault_stages(tracker, evidence)
        state.metrics_status = evidence.metrics_status
        state.logs_status = evidence.logs_status
        state.traces_status = evidence.traces_status
        state.projection_completed = True
        write_private_json(
            run_root / "source-results.json",
            {
                "schema_version": "live-e2e.safe-source-results.v2",
                "statuses": {
                    "METRICS": evidence.metrics_status,
                    "LOGS": evidence.logs_status,
                    "TRACES": evidence.traces_status,
                },
                "counts": evidence.source_counts,
                "invalid_refs": evidence.invalid_refs,
            },
            create_once=True,
        )
        write_private_json(
            run_root / "projection-summary.json",
            {
                "schema_version": "live-e2e.safe-projection-summary.v2",
                "visible_service_count": evidence.visible_service_count,
                "scenario_truth_leaked": evidence.scenario_truth_leaked,
                "projection_sha256": evidence.projection_sha256,
            },
            create_once=True,
        )
    except Exception:
        pass
    finally:
        cleanup_verdict, cleanup_payload, cleanup_failure_code = _cleanup(
            tracker=tracker,
            state=state,
            environment=environment,
            controller=controller,
            baseline_sha256=config.sandbox.scenario.baseline_document_sha256,
            run_root=run_root,
        )
    for stage in (
        DiagnosticStage.SCENARIO_LOCK_CREATED,
        DiagnosticStage.PLAN_TEMPLATE_CREATED,
        DiagnosticStage.APPROVAL_REQUEST_CREATED,
    ):
        tracker.skip_stage(stage, reason="DIAGNOSTIC_PROBE_FORBIDS_PREAUTHORIZATION_ARTIFACTS")
    private_permissions_verified = True
    try:
        roots.verify()
    except Exception as error:
        private_permissions_verified = False
        if tracker.failed_stage is None:
            tracker.fail_external(
                error,
                stage=DiagnosticStage.TERMINAL_SEALED,
                failure_code=DiagnosticFailureCode.PRIVATE_PERMISSION_VIOLATION,
            )
    success = (
        tracker.failed_stage is None
        and cleanup_verdict == "CLEAN"
        and evidence is not None
        and state.services_healthy
        and state.baseline_verified
        and state.metrics_status == "AVAILABLE"
        and state.logs_status == "AVAILABLE"
        and state.traces_status == "AVAILABLE"
        and evidence.invalid_refs == 0
        and 3 <= evidence.visible_service_count <= 8
        and not evidence.scenario_truth_leaked
    )
    verdict: str
    if success:
        verdict = config.authority.diagnostic_success_terminal
    elif tracker.failure_code is DiagnosticFailureCode.IMAGE_AUTHORITY_MISMATCH:
        verdict = "BLOCKED_E2E_V3_IMAGE_AUTHORITY_MISMATCH"
    elif tracker.failure_code is DiagnosticFailureCode.COMPOSE_STRUCTURE_IDENTITY_MISMATCH:
        verdict = "BLOCKED_E2E_V3_COMPOSE_STRUCTURE_IDENTITY_MISMATCH"
    else:
        verdict = "BLOCKED_E2E_V3_DIAGNOSTIC_PREFLIGHT_NOT_PASSED"
    exception = tracker.exception
    terminal: dict[str, object] = {
        "schema_version": "live-e2e.diagnostic-terminal.v3",
        "version": config.authority.version,
        "verdict": verdict,
        "run_kind": DiagnosticRunKind.DIAGNOSTIC_PROBE.value,
        "run_id": run_id,
        "implementation_commit": implementation_commit,
        "failed_stage": None if tracker.failed_stage is None else tracker.failed_stage.value,
        "last_completed_stage": None
        if tracker.root_last_completed_stage is None
        else tracker.root_last_completed_stage.value,
        "failure_code": None if tracker.failure_code is None else tracker.failure_code.value,
        "exception_type": None if exception is None else exception.exception_type,
        "exception_module": None if exception is None else exception.exception_module,
        "exception_message_sha256": None
        if exception is None
        else exception.exception_message_sha256,
        "traceback_sha256": None if exception is None else exception.traceback_sha256,
        "diagnostic_artifact_refs": []
        if exception is None
        else [
            {
                "artifact_ref": exception.artifact_ref,
                "artifact_sha256": exception.artifact_sha256,
            }
        ],
        "compose_start_requested": state.compose_start_requested,
        "compose_start_returned": state.compose_start_returned,
        "compose_start_return_code": state.compose_start_return_code,
        "image_authority_sha256": None
        if image_run is None
        else image_run.authority.authority_sha256,
        "image_verification_sha256": None
        if image_run is None
        else image_run.verification.verification_sha256,
        "compose_structure_sha256": None
        if image_run is None
        else image_run.compose.structure_sha256,
        "compose_instance_sha256": None
        if image_run is None
        else image_run.compose.instance_sha256,
        "owned_resources_observed": state.owned_resources_after_start,
        "services_healthy": state.services_healthy,
        "baseline_verified": state.baseline_verified,
        "metrics_status": state.metrics_status,
        "logs_status": state.logs_status,
        "traces_status": state.traces_status,
        "projection_completed": state.projection_completed,
        "source_counts": {} if evidence is None else evidence.source_counts,
        "invalid_refs": None if evidence is None else evidence.invalid_refs,
        "visible_service_count": None if evidence is None else evidence.visible_service_count,
        "scenario_truth_leaked": None if evidence is None else evidence.scenario_truth_leaked,
        "cleanup_verdict": cleanup_verdict,
        "cleanup": cleanup_payload,
        "cleanup_failure_code": cleanup_failure_code,
        "private_permissions_verified": private_permissions_verified,
        "fault_injections": 0,
        "provider_calls": 0,
        "model_calls": 0,
        "forward_mutations": 0,
        "rollback_mutations": 0,
    }
    tracker.journal.record(
        stage=DiagnosticStage.TERMINAL_SEALED,
        status=DiagnosticEventStatus.STARTED,
        started_at=datetime.now(timezone.utc),
        input_value=terminal,
    )
    write_private_json(run_root / "terminal.json", terminal, create_once=True)
    tracker.journal.record(
        stage=DiagnosticStage.TERMINAL_SEALED,
        status=DiagnosticEventStatus.PASSED,
        started_at=datetime.now(timezone.utc),
        ended_at=datetime.now(timezone.utc),
        output_value={"terminal_sha256": canonical_sha256(terminal)},
    )
    roots.verify()
    _complete_probe_budget(roots, run_id=run_id, verdict=verdict)
    return terminal


_TRACKED_V3_FILES = {
    "authority.json": E2E_V3_CONFIG_RELATIVE / "authority.json",
    "diagnostics.json": E2E_V3_CONFIG_RELATIVE / "diagnostics.json",
    "image-authority.json.schema-or-policy": (
        E2E_V3_CONFIG_RELATIVE / "image-authority.json.schema-or-policy"
    ),
    "projection.json": E2E_V3_CONFIG_RELATIVE / "projection.json",
    "reporting.json": E2E_V3_CONFIG_RELATIVE / "reporting.json",
    "e2e_diagnostics.py": Path("src/ecomsre_live_sandbox/e2e_diagnostics.py"),
    "e2e_v2_contracts.py": Path("src/ecomsre_live_sandbox/e2e_v2_contracts.py"),
    "e2e_v2.py": Path("src/ecomsre_live_sandbox/e2e_v2.py"),
    "e2e_v3_contracts.py": Path("src/ecomsre_live_sandbox/e2e_v3_contracts.py"),
    "e2e_v3.py": Path("src/ecomsre_live_sandbox/e2e_v3.py"),
    "e2e_v3_cli.py": Path("scripts/live_sandbox/e2e_v3.py"),
    "environment.py": Path("src/ecomsre_live_sandbox/environment.py"),
    "image_authority.py": Path("src/ecomsre_live_sandbox/image_authority.py"),
    "control.py": Path("src/ecomsre_live_sandbox/control.py"),
    "e2e_telemetry.py": Path("src/ecomsre_live_sandbox/e2e_telemetry.py"),
    "instrumentation_v2.py": Path("src/ecomsre_live_sandbox/instrumentation_v2.py"),
    "test_e2e_diagnostics.py": Path("tests/live_sandbox/test_e2e_diagnostics.py"),
    "test_e2e_v2.py": Path("tests/live_sandbox/test_e2e_v2.py"),
    "test_e2e_v3.py": Path("tests/live_sandbox/test_e2e_v3.py"),
    "test_image_authority.py": Path("tests/live_sandbox/test_image_authority.py"),
    "test_e2e_projection.py": Path("tests/live_sandbox/test_e2e_projection.py"),
    "test_live_sandbox.py": Path("tests/live_sandbox/test_live_sandbox.py"),
    "a0_prompt.py": Path("src/ecomsre_rca100/prompt.py"),
    "unified_runtime.py": Path("src/ecomsre_rca_unified/runtime.py"),
}


def _require_diagnostic_pass(
    config: E2EV3Config, roots: E2EV3PrivateRoots
) -> tuple[Mapping[str, object], Path]:
    budget_path = roots.control / "diagnostic-budget.json"
    if budget_path.is_symlink() or not budget_path.is_file():
        raise RuntimeError("canonical Invocation A requires diagnostic PASS")
    budget = json.loads(budget_path.read_text(encoding="utf-8"))
    runs = budget.get("runs") if isinstance(budget, Mapping) else None
    if not isinstance(runs, list):
        raise RuntimeError("canonical Invocation A requires diagnostic PASS")
    passing = [
        item
        for item in runs
        if isinstance(item, Mapping)
        and item.get("verdict") == config.authority.diagnostic_success_terminal
    ]
    if len(passing) != 1 or not isinstance(passing[0].get("run_id"), str):
        raise RuntimeError("canonical Invocation A requires diagnostic PASS")
    terminal_path = roots.diagnostics / str(passing[0]["run_id"]) / "terminal.json"
    if terminal_path.is_symlink() or not terminal_path.is_file():
        raise RuntimeError("canonical Invocation A requires a sealed diagnostic PASS terminal")
    terminal = json.loads(terminal_path.read_text(encoding="utf-8"))
    if not isinstance(terminal, Mapping) or any(
        (
            terminal.get("verdict") != config.authority.diagnostic_success_terminal,
            terminal.get("cleanup_verdict") != "CLEAN",
            terminal.get("fault_injections") != 0,
            terminal.get("provider_calls") != 0,
            terminal.get("model_calls") != 0,
            terminal.get("forward_mutations") != 0,
            terminal.get("rollback_mutations") != 0,
            terminal.get("private_permissions_verified") is not True,
            not isinstance(terminal.get("image_authority_sha256"), str),
            not isinstance(terminal.get("image_verification_sha256"), str),
            not isinstance(terminal.get("compose_structure_sha256"), str),
            not isinstance(terminal.get("compose_instance_sha256"), str),
        )
    ):
        raise RuntimeError("canonical Invocation A requires diagnostic PASS")
    return terminal, terminal_path


def _require_exact_head_admission(
    roots: E2EV3PrivateRoots, *, implementation_commit: str
) -> tuple[Mapping[str, object], Mapping[str, object]]:
    root_name = type(roots).__name__
    schema_suffix = {
        "E2EV4PrivateRoots": "v4",
        "E2EV5PrivateRoots": "v5",
        "E2EV6PrivateRoots": "v6",
    }.get(root_name, "v3")
    ci_path = roots.control / "exact-head-ci.json"
    if ci_path.is_symlink() or not ci_path.is_file():
        raise RuntimeError("canonical Invocation A lacks an exact-head CI marker")
    ci = json.loads(ci_path.read_text(encoding="utf-8"))
    workflows = ci.get("workflows") if isinstance(ci, Mapping) else None
    required = {"Agent mainline", "RCAEval RE2 v2 development"}
    if (
        ci.get("schema_version") != f"live-e2e.exact-head-ci.{schema_suffix}"
        or ci.get("implementation_commit") != implementation_commit
        or not isinstance(workflows, Mapping)
        or set(workflows) != required
        or any(
            not isinstance(workflows[name], Mapping)
            or workflows[name].get("conclusion") != "SUCCESS"
            or not isinstance(workflows[name].get("run_id"), int)
            for name in required
        )
    ):
        raise RuntimeError("canonical Invocation A exact-head CI marker differs")
    review_path = roots.control / "pre-live-review.json"
    if review_path.is_symlink() or not review_path.is_file():
        raise RuntimeError("canonical Invocation A lacks PRE_LIVE review")
    review = json.loads(review_path.read_text(encoding="utf-8"))
    if (
        not isinstance(review, Mapping)
        or review.get("schema_version") != f"live-e2e.pre-live-review.{schema_suffix}"
        or review.get("implementation_commit") != implementation_commit
        or review.get("verdict") != "PRE_LIVE_PASS"
        or review.get("must_fix_count") != 0
    ):
        raise RuntimeError("canonical Invocation A PRE_LIVE review differs")
    return ci, review


def _consume_canonical_budget(config: E2EV3Config, roots: E2EV3PrivateRoots) -> None:
    terminal_path = roots.invocation_a / "terminal.json"
    started_path = roots.invocation_a / "started.json"
    if any(path.exists() or path.is_symlink() for path in (terminal_path, started_path)):
        raise RuntimeError("canonical Invocation A is create-once and already consumed")
    path = roots.control / "canonical-budget.json"
    budget = _read_budget(
        path,
        maximum=config.authority.maximum_canonical_invocation_a_runs,
        schema_version="live-e2e.canonical-budget.v3",
    )
    consumed = budget.get("consumed")
    if not isinstance(consumed, int) or consumed >= 1:
        raise RuntimeError("canonical Invocation A is create-once and already consumed")
    budget["consumed"] = 1
    budget["runs"] = [{"run_id": "invocation-a", "verdict": "STARTED"}]
    write_private_json(path, budget, create_once=False)
    ensure_private_directory(roots.invocation_a)
    write_private_json(
        started_path,
        {
            "schema_version": "live-e2e.canonical-started.v3",
            "started_at": datetime.now(timezone.utc).isoformat(),
        },
        create_once=True,
    )


def _complete_canonical_budget(roots: E2EV3PrivateRoots, *, verdict: str) -> None:
    path = roots.control / "canonical-budget.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    runs = value.get("runs") if isinstance(value, dict) else None
    if not isinstance(runs, list) or len(runs) != 1 or not isinstance(runs[0], dict):
        raise ValueError("canonical run history is malformed")
    runs[0]["verdict"] = verdict
    write_private_json(path, value, create_once=False)


def build_plan_template(config: E2EV3Config) -> dict[str, object]:
    return LiveRemediationPlan.template_payload(config.sandbox)


def scenario_lock_manifest(
    config: E2EV3Config,
    roots: E2EV3PrivateRoots,
    *,
    implementation_commit: str,
    image_authority_sha256: str,
    canonical_image_verification_sha256: str,
    compose_structure_sha256: str,
    canonical_compose_instance_sha256: str,
    normalization_policy_sha256: str,
    diagnostic_terminal_path: Path,
) -> dict[str, object]:
    plan_template = build_plan_template(config)
    tracked = {
        name: file_sha256(config.repository_root / relative)
        for name, relative in _TRACKED_V3_FILES.items()
    }
    approval_seed = hashlib.sha256(
        json.dumps(
            {
                "version": config.authority.version,
                "implementation_commit": implementation_commit,
                "scenario_id": config.authority.scenario_id,
                "plan_template_sha256": canonical_sha256(plan_template),
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    return {
        "schema_version": "live-e2e.scenario-lock.v3",
        "version": config.authority.version,
        "implementation_commit": implementation_commit,
        "implementation_branch": config.authority.branch,
        "predecessor_pr": config.authority.predecessor_pr,
        "predecessor_head": config.authority.predecessor_head,
        "predecessor_terminal": config.authority.predecessor_terminal,
        "predecessor_reason": config.authority.predecessor_reason,
        "telemetry_authority_pr": config.authority.telemetry_authority_pr,
        "telemetry_authority_head": config.authority.telemetry_authority_head,
        "telemetry_authority_semantic_sha256": (
            config.authority.telemetry_authority_semantic_sha256
        ),
        "diagnostic_policy_sha256": config.authority.diagnostics_policy_sha256,
        "projection_policy_sha256": config.authority.projection_policy_sha256,
        "reporting_policy_sha256": config.authority.reporting_policy_sha256,
        "diagnostic_terminal_sha256": file_sha256(diagnostic_terminal_path),
        "exact_head_ci_marker_sha256": file_sha256(roots.control / "exact-head-ci.json"),
        "pre_live_review_sha256": file_sha256(roots.control / "pre-live-review.json"),
        "image_authority_sha256": image_authority_sha256,
        "canonical_image_verification_sha256": canonical_image_verification_sha256,
        "compose_structure_sha256": compose_structure_sha256,
        "canonical_compose_instance_sha256": canonical_compose_instance_sha256,
        "normalization_policy_sha256": normalization_policy_sha256,
        "sandbox_identity": {
            "environment_id": config.sandbox.environment.environment_id,
            "sandbox_id": config.sandbox.environment.sandbox_id,
            "compose_project": config.sandbox.environment.compose_project,
            "ownership_label_key": config.sandbox.environment.sandbox_label_key,
        },
        "fault_controller_type": config.sandbox.scenario.fault_controller_type,
        "scenario_id": config.sandbox.scenario.scenario_id,
        "target_service": config.sandbox.scenario.target_service,
        "target_configuration_key": config.sandbox.scenario.target_configuration_key,
        "baseline_document_sha256": config.sandbox.scenario.baseline_document_sha256,
        "fault_document_sha256": config.sandbox.scenario.fault_document_sha256,
        "telemetry_source_hashes": {
            key: value
            for key, value in config.authority.frozen_input_hashes.items()
            if key.startswith("v3_")
        },
        "a0_prompt_sha256": config.authority.a0_prompt_sha256,
        "a0_output_schema_sha256": config.authority.a0_output_schema_sha256,
        "a0_model": config.authority.a0_model,
        "sli_thresholds": config.sandbox.verification.model_dump(mode="json"),
        "provider_budget": config.sandbox.budget.model_dump(mode="json"),
        "plan_template_sha256": canonical_sha256(plan_template),
        "approval_request_identity_seed": approval_seed,
        "tracked_runtime_and_config": tracked,
    }


def _canonical_failure_verdict(
    failure_code: DiagnosticFailureCode | None,
    *,
    cleanup_verdict: str,
) -> str:
    if cleanup_verdict == "BLOCKED":
        return "BLOCKED_E2E_V3_CLEANUP_INCOMPLETE"
    mapping = {
        DiagnosticFailureCode.COMPOSE_UP_FAILED: "BLOCKED_E2E_V3_COMPOSE_UP_FAILED",
        DiagnosticFailureCode.SERVICE_HEALTH_TIMEOUT: "BLOCKED_E2E_V3_SERVICE_HEALTH_TIMEOUT",
        DiagnosticFailureCode.SERVICE_EXITED_BEFORE_READY: "BLOCKED_E2E_V3_SERVICE_HEALTH_TIMEOUT",
        DiagnosticFailureCode.BASELINE_CONFIGURATION_UNAVAILABLE: (
            "BLOCKED_E2E_V3_BASELINE_CONFIGURATION_UNAVAILABLE"
        ),
        DiagnosticFailureCode.BASELINE_CONFIGURATION_MISMATCH: (
            "BLOCKED_E2E_V3_BASELINE_CONFIGURATION_MISMATCH"
        ),
        DiagnosticFailureCode.METRICS_PREFLIGHT_FAILED: "BLOCKED_E2E_V3_METRICS_PREFLIGHT_FAILED",
        DiagnosticFailureCode.LOGS_PREFLIGHT_FAILED: "BLOCKED_E2E_V3_LOGS_PREFLIGHT_FAILED",
        DiagnosticFailureCode.TRACES_PREFLIGHT_FAILED: "BLOCKED_E2E_V3_TRACES_PREFLIGHT_FAILED",
        DiagnosticFailureCode.MULTISERVICE_PROJECTION_FAILED: (
            "BLOCKED_E2E_V3_MULTISERVICE_PROJECTION_FAILED"
        ),
        DiagnosticFailureCode.APPROVAL_REQUEST_WRITE_FAILED: (
            "BLOCKED_E2E_V3_APPROVAL_REQUEST_WRITE_FAILED"
        ),
        DiagnosticFailureCode.CLEANUP_FAILED: "BLOCKED_E2E_V3_CLEANUP_INCOMPLETE",
    }
    if failure_code is None:
        return "BLOCKED_E2E_V3_UNCLASSIFIED_RUNTIME_FAILURE"
    return mapping.get(failure_code, "BLOCKED_E2E_V3_UNCLASSIFIED_RUNTIME_FAILURE")


def run_canonical_invocation_a(
    config: E2EV3Config,
    roots: E2EV3PrivateRoots,
    *,
    environment_factory: Callable[..., Any] = SandboxEnvironment,
    controller_factory: Callable[..., Any] = _make_controller,
    evidence_collector: Callable[..., NoFaultEvidence] = _collect_no_fault_evidence,
    sleep: Callable[[float], None] = time.sleep,
    worktree_verifier: Callable[[E2EV3Config, bool], str] = _default_worktree_verifier,
) -> dict[str, object]:
    roots.bind_lifecycle(config.authority, repository_root=config.repository_root)
    diagnostic_terminal, diagnostic_terminal_path = _require_diagnostic_pass(config, roots)
    implementation_commit = worktree_verifier(config, True)
    _require_exact_head_admission(roots, implementation_commit=implementation_commit)
    _consume_canonical_budget(config, roots)
    run_root = roots.invocation_a
    for directory in (
        run_root,
        run_root / "commands",
        run_root / "exceptions",
        run_root / "snapshots",
    ):
        ensure_private_directory(directory)
    journal = DiagnosticJournal(
        run_root / "events.jsonl",
        run_kind=DiagnosticRunKind.CANONICAL_INVOCATION_A,
        run_id="invocation-a",
    )
    tracker = _StageTracker(journal, ExceptionArtifactStore(run_root / "exceptions"))
    execution = _execute_no_fault_sequence(
        config,
        roots,
        run_id="invocation-a",
        run_root=run_root,
        tracker=tracker,
        clean_required=True,
        environment_factory=environment_factory,
        controller_factory=controller_factory,
        evidence_collector=evidence_collector,
        sleep=sleep,
        worktree_verifier=worktree_verifier,
        expected_structure_sha256=cast(
            str, diagnostic_terminal["compose_structure_sha256"]
        ),
    )
    state = execution.state
    evidence = execution.evidence
    scenario_lock_created = False
    plan_template_created = False
    approval_request_created = False
    approval_command: str | None = None
    request: ApprovalRequest | None = None
    eligible = (
        tracker.failed_stage is None
        and execution.cleanup_verdict == "CLEAN"
        and execution.image_authority is not None
        and execution.image_verification is not None
        and evidence is not None
        and state.services_healthy
        and state.baseline_verified
        and state.metrics_status == "AVAILABLE"
        and state.logs_status == "AVAILABLE"
        and state.traces_status == "AVAILABLE"
        and evidence.invalid_refs == 0
        and 3 <= evidence.visible_service_count <= 8
        and not evidence.scenario_truth_leaked
    )
    if eligible:
        try:
            lock = scenario_lock_manifest(
                config,
                roots,
                implementation_commit=implementation_commit,
                image_authority_sha256=cast(
                    ImageAuthority, execution.image_authority
                ).authority_sha256,
                canonical_image_verification_sha256=cast(
                    RunImageVerification, execution.image_verification
                ).verification_sha256,
                compose_structure_sha256=cast(
                    RunImageVerification, execution.image_verification
                ).compose_structure_sha256,
                canonical_compose_instance_sha256=cast(
                    RunImageVerification, execution.image_verification
                ).compose_instance_sha256,
                normalization_policy_sha256=cast(
                    RunImageVerification, execution.image_verification
                ).normalization_policy_sha256,
                diagnostic_terminal_path=diagnostic_terminal_path,
            )
            tracker.execute(
                DiagnosticStage.SCENARIO_LOCK_CREATED,
                lambda: write_private_json(
                    roots.control / "scenario-lock.json", lock, create_once=True
                ),
                failure_code=DiagnosticFailureCode.SCENARIO_LOCK_WRITE_FAILED,
            )
            scenario_lock_created = True
            template = build_plan_template(config)
            tracker.execute(
                DiagnosticStage.PLAN_TEMPLATE_CREATED,
                lambda: write_private_json(
                    roots.control / "plan-template.json", template, create_once=True
                ),
                failure_code=DiagnosticFailureCode.PLAN_TEMPLATE_WRITE_FAILED,
            )
            plan_template_created = True
            request = create_approval_request(config, scenario_lock=lock)
            tracker.execute(
                DiagnosticStage.APPROVAL_REQUEST_CREATED,
                lambda: write_private_json(
                    roots.control / "approval-request.json", request, create_once=True
                ),
                failure_code=DiagnosticFailureCode.APPROVAL_REQUEST_WRITE_FAILED,
            )
            approval_request_created = True
            approval_command = (
                "uv run --with pyarrow python -m scripts.live_sandbox.e2e_v3 "
                "--private-root ~/.ecomsre/private/live-fault-a0-controlled-remediation-e2e-v3 "
                "approve --approver \"<HUMAN_NAME>\" "
                f"--phrase \"APPROVE {request.scenario_id} {request.plan_template_sha256}\""
            )
        except Exception:
            pass
    else:
        for stage in (
            DiagnosticStage.SCENARIO_LOCK_CREATED,
            DiagnosticStage.PLAN_TEMPLATE_CREATED,
            DiagnosticStage.APPROVAL_REQUEST_CREATED,
        ):
            tracker.skip_stage(stage, reason="CANONICAL_PREFLIGHT_OR_CLEANUP_NOT_ADMITTED")
    private_permissions_verified = True
    try:
        roots.verify()
    except Exception as error:
        private_permissions_verified = False
        if tracker.failed_stage is None:
            tracker.fail_external(
                error,
                stage=DiagnosticStage.TERMINAL_SEALED,
                failure_code=DiagnosticFailureCode.PRIVATE_PERMISSION_VIOLATION,
            )
    success = (
        eligible
        and tracker.failed_stage is None
        and scenario_lock_created
        and plan_template_created
        and approval_request_created
        and request is not None
        and approval_command is not None
    )
    verdict = (
        config.authority.invocation_a_terminal
        if success
        else _canonical_failure_verdict(
            tracker.failure_code,
            cleanup_verdict=execution.cleanup_verdict,
        )
    )
    exception = tracker.exception
    terminal = {
        "schema_version": "live-e2e.canonical-invocation-a-terminal.v3",
        "version": config.authority.version,
        "verdict": verdict,
        "run_kind": DiagnosticRunKind.CANONICAL_INVOCATION_A.value,
        "run_id": "invocation-a",
        "run_count": 1,
        "implementation_commit": implementation_commit,
        "diagnostic_terminal_sha256": file_sha256(diagnostic_terminal_path),
        "failed_stage": None if tracker.failed_stage is None else tracker.failed_stage.value,
        "last_completed_stage": None
        if tracker.root_last_completed_stage is None
        else tracker.root_last_completed_stage.value,
        "failure_code": None if tracker.failure_code is None else tracker.failure_code.value,
        "exception_type": None if exception is None else exception.exception_type,
        "exception_module": None if exception is None else exception.exception_module,
        "exception_message_sha256": None
        if exception is None
        else exception.exception_message_sha256,
        "traceback_sha256": None if exception is None else exception.traceback_sha256,
        "diagnostic_artifact_refs": []
        if exception is None
        else [
            {
                "artifact_ref": exception.artifact_ref,
                "artifact_sha256": exception.artifact_sha256,
            }
        ],
        "compose_start_requested": state.compose_start_requested,
        "compose_start_returned": state.compose_start_returned,
        "compose_start_return_code": state.compose_start_return_code,
        "image_authority_sha256": None
        if execution.image_authority is None
        else execution.image_authority.authority_sha256,
        "image_verification_sha256": None
        if execution.image_verification is None
        else execution.image_verification.verification_sha256,
        "compose_structure_sha256": None
        if execution.image_verification is None
        else execution.image_verification.compose_structure_sha256,
        "compose_instance_sha256": None
        if execution.image_verification is None
        else execution.image_verification.compose_instance_sha256,
        "owned_resources_observed": state.owned_resources_after_start,
        "services_healthy": state.services_healthy,
        "baseline_verified": state.baseline_verified,
        "metrics_status": state.metrics_status,
        "logs_status": state.logs_status,
        "traces_status": state.traces_status,
        "projection_completed": state.projection_completed,
        "source_counts": {} if evidence is None else evidence.source_counts,
        "invalid_refs": None if evidence is None else evidence.invalid_refs,
        "visible_service_count": None if evidence is None else evidence.visible_service_count,
        "scenario_truth_leaked": None if evidence is None else evidence.scenario_truth_leaked,
        "scenario_lock_created": scenario_lock_created,
        "plan_template_created": plan_template_created,
        "approval_request_created": approval_request_created,
        "approval_request_id": None if request is None else request.approval_request_id,
        "plan_template_sha256": None if request is None else request.plan_template_sha256,
        "approval_expires_at": None
        if request is None
        else request.expires_at.isoformat(),
        "approval_command": approval_command,
        "cleanup_verdict": execution.cleanup_verdict,
        "cleanup": execution.cleanup_payload,
        "cleanup_failure_code": execution.cleanup_failure_code,
        "private_permissions_verified": private_permissions_verified,
        "fault_injections": 0,
        "provider_calls": 0,
        "model_calls": 0,
        "forward_mutations": 0,
        "rollback_mutations": 0,
        "codex_self_approved": False,
        "human_approval_record_present": False,
    }
    tracker.journal.record(
        stage=DiagnosticStage.TERMINAL_SEALED,
        status=DiagnosticEventStatus.STARTED,
        started_at=datetime.now(timezone.utc),
        input_value=terminal,
    )
    write_private_json(run_root / "terminal.json", terminal, create_once=True)
    tracker.journal.record(
        stage=DiagnosticStage.TERMINAL_SEALED,
        status=DiagnosticEventStatus.PASSED,
        started_at=datetime.now(timezone.utc),
        ended_at=datetime.now(timezone.utc),
        output_value={"terminal_sha256": canonical_sha256(terminal)},
    )
    roots.verify()
    _complete_canonical_budget(roots, verdict=verdict)
    return terminal


def _require_canonical_success(
    config: E2EV3Config, roots: E2EV3PrivateRoots
) -> Mapping[str, object]:
    path = roots.invocation_a / "terminal.json"
    if path.is_symlink() or not path.is_file():
        raise RuntimeError("human approval requires canonical Invocation A success")
    terminal = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(terminal, Mapping) or any(
        (
            terminal.get("verdict") != config.authority.invocation_a_terminal,
            terminal.get("cleanup_verdict") != "CLEAN",
            terminal.get("scenario_lock_created") is not True,
            terminal.get("plan_template_created") is not True,
            terminal.get("approval_request_created") is not True,
            terminal.get("fault_injections") != 0,
            terminal.get("provider_calls") != 0,
            terminal.get("model_calls") != 0,
            terminal.get("forward_mutations") != 0,
            terminal.get("rollback_mutations") != 0,
            terminal.get("codex_self_approved") is not False,
        )
    ):
        raise RuntimeError("human approval requires canonical Invocation A success")
    return terminal


def record_human_approval_for_invocation_b(
    config: E2EV3Config,
    roots: E2EV3PrivateRoots,
    *,
    approver: str,
    phrase: str,
) -> HumanApprovalRecord:
    roots.bind_lifecycle(config.authority, repository_root=config.repository_root)
    _require_canonical_success(config, roots)
    lock_path = roots.control / "scenario-lock.json"
    template_path = roots.control / "plan-template.json"
    request_path = roots.control / "approval-request.json"
    if any(path.is_symlink() or not path.is_file() for path in (lock_path, template_path, request_path)):
        raise RuntimeError("human approval lacks the frozen canonical artifacts")
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    template = json.loads(template_path.read_text(encoding="utf-8"))
    request = ApprovalRequest.model_validate_json(request_path.read_text(encoding="utf-8"))
    if (
        not isinstance(lock, Mapping)
        or request.scenario_lock_sha256 != canonical_sha256(lock)
        or request.plan_template_sha256 != canonical_sha256(template)
    ):
        raise RuntimeError("human approval request binding differs")
    record = record_human_approval(
        request,
        approver=approver,
        phrase=phrase,
        now=datetime.now(timezone.utc),
        destination=roots.control / "human-approval.json",
    )
    roots.verify()
    return record


_LEGAL_INVOCATION_B_TERMINALS = get_invocation_b_verdict_policy(
    "v3"
).legal_terminals


def _load_exact_approval(
    config: E2EV3Config,
    roots: E2EV3PrivateRoots,
    *,
    now: datetime,
) -> tuple[Mapping[str, object], ApprovalRequest, HumanApprovalRecord]:
    lock_path = roots.control / "scenario-lock.json"
    request_path = roots.control / "approval-request.json"
    approval_path = roots.control / "human-approval.json"
    if approval_path.is_symlink() or not approval_path.is_file():
        raise RuntimeError("Invocation B requires a valid HumanApprovalRecord")
    if any(path.is_symlink() or not path.is_file() for path in (lock_path, request_path)):
        raise RuntimeError("Invocation B lacks frozen approval authority")
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    if not isinstance(lock, Mapping):
        raise RuntimeError("Invocation B scenario lock is malformed")
    request = ApprovalRequest.model_validate_json(request_path.read_text(encoding="utf-8"))
    approval = HumanApprovalRecord.model_validate_json(approval_path.read_text(encoding="utf-8"))
    expected = {
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
    actual = {key: getattr(approval, key) for key in expected}
    if (
        approval.mode != "HUMAN"
        or not approval.approver.strip()
        or actual != expected
        or request.scenario_lock_sha256 != canonical_sha256(lock)
        or now > request.expires_at
        or now > approval.expires_at
        or request.scenario_id != config.authority.scenario_id
        or request.action != config.sandbox.policy.action
        or request.max_forward_mutations != 1
    ):
        raise RuntimeError("Invocation B HumanApprovalRecord binding differs or expired")
    return lock, request, approval


def _verify_scenario_lock_for_invocation_b(
    config: E2EV3Config,
    roots: E2EV3PrivateRoots,
    *,
    locked: Mapping[str, object],
    implementation_commit: str,
) -> None:
    if _schema_suffix(config) in {"v4", "v5", "v6"}:
        from ecomsre_live_sandbox.e2e_v4 import (
            _verify_scenario_lock_for_invocation_b as verify_v4_scenario_lock,
        )

        verify_v4_scenario_lock(
            cast(Any, config),
            cast(Any, roots),
            locked=locked,
            implementation_commit=implementation_commit,
        )
        return
    _, diagnostic_terminal_path = _require_diagnostic_pass(config, roots)
    required_hash_fields = (
        "image_authority_sha256",
        "canonical_image_verification_sha256",
        "compose_structure_sha256",
        "canonical_compose_instance_sha256",
        "normalization_policy_sha256",
    )
    if any(
        not isinstance(locked.get(name), str) or len(cast(str, locked.get(name))) != 64
        for name in required_hash_fields
    ):
        raise RuntimeError("Invocation B Scenario Lock image identities are malformed")
    expected = scenario_lock_manifest(
        config,
        roots,
        implementation_commit=implementation_commit,
        image_authority_sha256=cast(str, locked["image_authority_sha256"]),
        canonical_image_verification_sha256=cast(
            str, locked["canonical_image_verification_sha256"]
        ),
        compose_structure_sha256=cast(str, locked["compose_structure_sha256"]),
        canonical_compose_instance_sha256=cast(
            str, locked["canonical_compose_instance_sha256"]
        ),
        normalization_policy_sha256=cast(
            str, locked["normalization_policy_sha256"]
        ),
        diagnostic_terminal_path=diagnostic_terminal_path,
    )
    if canonical_sha256(locked) != canonical_sha256(expected):
        raise RuntimeError("Invocation B scenario lock differs from frozen runtime files")


def _consume_live_run_budget(config: E2EV3Config, roots: E2EV3PrivateRoots) -> None:
    started_path = roots.invocation_b / "started.json"
    terminal_path = roots.invocation_b / "terminal.json"
    if any(path.exists() or path.is_symlink() for path in (started_path, terminal_path)):
        raise RuntimeError("Invocation B is create-once and already consumed")
    path = roots.control / "live-run-budget.json"
    schema_suffix = _schema_suffix(config)
    maximum = getattr(
        config.authority,
        "maximum_complete_live_runs",
        getattr(config.authority, "maximum_accepted_complete_live_runs", None),
    )
    if maximum != 1:
        raise ValueError("Invocation B accepted live-run authority differs")
    budget = _read_budget(
        path,
        maximum=maximum,
        schema_version=f"live-e2e.live-run-budget.{schema_suffix}",
    )
    if budget.get("consumed") != 0:
        raise RuntimeError("Invocation B is create-once and already consumed")
    budget["consumed"] = 1
    budget["runs"] = [{"run_id": "invocation-b", "verdict": "STARTED"}]
    write_private_json(path, budget, create_once=False)
    ensure_private_directory(roots.invocation_b)
    write_private_json(
        started_path,
        {
            "schema_version": f"live-e2e.invocation-b-started.{schema_suffix}",
            "started_at": datetime.now(timezone.utc).isoformat(),
        },
        create_once=True,
    )


def _complete_live_run_budget(roots: E2EV3PrivateRoots, *, verdict: str) -> None:
    path = roots.control / "live-run-budget.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    runs = value.get("runs") if isinstance(value, dict) else None
    if not isinstance(runs, list) or len(runs) != 1 or not isinstance(runs[0], dict):
        raise ValueError("Invocation B run history is malformed")
    runs[0]["verdict"] = verdict
    write_private_json(path, value, create_once=False)


def _enrich_v5_invocation_b_terminal(
    roots: E2EV3PrivateRoots, terminal: dict[str, object]
) -> None:
    source_path = roots.invocation_b / "source-results.json"
    if source_path.is_file() and not source_path.is_symlink():
        source = json.loads(source_path.read_text(encoding="utf-8"))
        results = source.get("results") if isinstance(source, Mapping) else None
        if isinstance(results, list):
            terminal["source_availability"] = {
                str(item.get("source")): item.get("status")
                for item in results
                if isinstance(item, Mapping)
            }
            terminal["all_three_source_terminals_retained"] = len(results) == 3
        terminal["source_counts"] = source.get("source_counts", {})
        terminal["invalid_refs"] = source.get("invalid_ref_count")
        terminal["all_refs_resolve"] = source.get("all_refs_resolve")
        terminal["source_results_sha256"] = source.get("source_results_sha256")
        terminal["combined_resolver_sha256"] = source.get(
            "combined_resolver_sha256"
        )

    summary_path = roots.invocation_b / "projection-input-summary.json"
    if summary_path.is_file() and not summary_path.is_symlink():
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        if isinstance(summary, Mapping):
            terminal["projection_broad_counts"] = {
                "metrics": summary.get("broad_metrics_count"),
                "logs": summary.get("broad_logs_count"),
                "traces": summary.get("broad_traces_count"),
            }
            terminal["projection_diagnostic_counts"] = {
                "metrics": summary.get("anomalous_metric_count"),
                "logs": summary.get("anomalous_log_count"),
                "traces": summary.get("error_trace_count"),
            }
            terminal["empty_model_streams"] = summary.get(
                "empty_model_streams", []
            )
            terminal["projection_reason_codes"] = summary.get("reason_codes", [])
            terminal["projection_input_summary_sha256"] = summary.get(
                "summary_sha256"
            )


def _public_result_v3(config: E2EV3Config, terminal: Mapping[str, object]) -> dict[str, object]:
    cleanup = terminal.get("cleanup")
    public = {
        "schema_version": "live-e2e.public-result.v3",
        "version": config.authority.version,
        "verdict": terminal.get("verdict"),
        "implementation_commit": terminal.get("implementation_commit"),
        "result_head": _git(config.repository_root, "rev-parse", "HEAD"),
        "source_availability": terminal.get("source_availability", {}),
        "source_counts": terminal.get("source_counts", {}),
        "invalid_refs": terminal.get("invalid_refs"),
        "visible_service_count": terminal.get("visible_service_count"),
        "fault_injections": terminal.get("fault_injections", 0),
        "provider_calls": terminal.get("provider_calls", 0),
        "model_calls": terminal.get("model_calls", 0),
        "forward_mutations": terminal.get("forward_mutations", 0),
        "rollback_mutations": terminal.get("rollback_mutations", 0),
        "fault_impact_gate": terminal.get("fault_impact_passed"),
        "diagnosis_gate": terminal.get("diagnosis_gate"),
        "diagnosis_correct": terminal.get("diagnosis_correct"),
        "plan_action": terminal.get("plan_action"),
        "approval_mode": "HUMAN_PREAUTHORIZED_FROZEN_REMEDIATION_RUNBOOK",
        "policy_verdict": terminal.get("policy_verdict"),
        "recovery_verification": terminal.get("recovery_verification_passed"),
        "rollback_exact_hash_verified": terminal.get("rollback_exact_hash_verified"),
        "cleanup": cleanup,
        "claim_boundary": list(config.reporting.claim_boundary),
    }
    public["semantic_sha256"] = canonical_sha256(public)
    return public


def verify_public_result(config: E2EV3Config, value: Mapping[str, object]) -> None:
    verdict = value.get("verdict")
    legal_terminals = get_invocation_b_verdict_policy(
        _schema_suffix(config)
    ).legal_terminals
    if verdict not in legal_terminals:
        raise ValueError("public Invocation B terminal is not legal")
    semantic = value.get("semantic_sha256")
    core = dict(value)
    core.pop("semantic_sha256", None)
    if semantic != canonical_sha256(core):
        raise ValueError("public Invocation B semantic hash differs")
    if scan_public_e2e_payload(value):
        raise ValueError("public Invocation B result contains private or control data")
    cleanup = value.get("cleanup")
    if not isinstance(cleanup, Mapping):
        raise ValueError("public Invocation B cleanup aggregate is missing")
    if verdict == config.authority.invocation_b_success and any(
        (
            value.get("fault_injections") != 1,
            value.get("provider_calls") != 2,
            value.get("model_calls") != 1,
            value.get("forward_mutations") != 1,
            value.get("rollback_mutations") != 0,
            value.get("fault_impact_gate") is not True,
            value.get("diagnosis_gate") is not True,
            value.get("diagnosis_correct") is not True,
            value.get("plan_action") != "RESTORE_FROZEN_SERVICE_CONFIGURATION",
            value.get("policy_verdict") != "ALLOW",
            value.get("recovery_verification") is not True,
            cleanup.get("verdict") != "CLEAN",
        )
    ):
        raise ValueError("public Invocation B success aggregates do not recompute")


def _write_new_public(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())


def _write_public_outputs_v3(
    config: E2EV3Config, terminal: Mapping[str, object]
) -> tuple[str, str, str]:
    public = _public_result_v3(config, terminal)
    verify_public_result(config, public)
    paths = (
        config.repository_root / config.reporting.public_result_json,
        config.repository_root / config.reporting.public_result_markdown,
        config.repository_root / config.reporting.public_human_brief,
    )
    _write_new_public(paths[0], json.dumps(public, ensure_ascii=False, sort_keys=True).encode("utf-8"))
    _write_new_public(
        paths[1],
        (
            "# Live Fault to A0 Controlled Remediation E2E v3\n\n"
            f"**Verdict:** `{public['verdict']}`\n\n"
            "This is one preregistered local Sandbox scenario using a "
            "HUMAN_PREAUTHORIZED_FROZEN_REMEDIATION_RUNBOOK. It is not production, "
            "autonomous production remediation, an external benchmark, or a Multi-Agent claim.\n"
        ).encode("utf-8"),
    )
    _write_new_public(
        paths[2],
        (
            "# Live Fault → A0 → Controlled Remediation v3 — Human Brief\n\n"
            "本结果仅代表一个本地 Sandbox、一个预注册场景和一个人工预授权的冻结修复 runbook。"
            "人工授权发生在实际诊断之前，不代表人工审阅了实际诊断；不构成生产自治或 Multi-Agent 优越性声明。\n"
        ).encode("utf-8"),
    )
    return cast(
        tuple[str, str, str],
        tuple(path.relative_to(config.repository_root).as_posix() for path in paths),
    )


def _cleanup_truth(terminal: Mapping[str, object]) -> dict[str, object]:
    cleanup = terminal.get("cleanup")
    if isinstance(cleanup, Mapping):
        return dict(cleanup)
    verdict = terminal.get("cleanup_verdict")
    return {
        "baseline_restored": verdict == "NOT_REQUIRED",
        "owned_containers": 0,
        "owned_networks": 0,
        "owned_volumes": 0,
        "non_owned_resources_changed": False,
        "verdict": verdict,
    }


def _record_rollback(roots: E2EV3PrivateRoots, terminal: dict[str, object]) -> None:
    terminal["rollback_mutations"] = 1
    write_private_json(
        roots.journal / "rollback-mutation.json",
        {"schema_version": "live-e2e.rollback-mutation.v2", "mutation_count": 1},
        create_once=True,
    )


def _default_provider_factory(config: E2EV3Config) -> Any:
    return _provider(cast(Any, config))


def run_invocation_b(
    config: E2EV3Config,
    roots: E2EV3PrivateRoots,
    *,
    provider_factory: Callable[[E2EV3Config], Any] = _default_provider_factory,
    environment_factory: Callable[..., Any] = SandboxEnvironment,
    controller_factory: Callable[..., Any] = _make_controller,
    worktree_verifier: Callable[[E2EV3Config, bool], str] = _default_worktree_verifier,
    sleep: Callable[[float], None] = time.sleep,
    public_writer: Callable[
        [E2EV3Config, Mapping[str, object]], tuple[str, ...]
    ] = _write_public_outputs_v3,
) -> dict[str, object]:
    """Execute the single human-preauthorized live run without result-driven retries."""
    roots.bind_lifecycle(config.authority, repository_root=config.repository_root)
    _require_canonical_success(config, roots)
    locked, request, approval = _load_exact_approval(
        config,
        roots,
        now=datetime.now(timezone.utc),
    )
    implementation_commit = worktree_verifier(config, True)
    _require_exact_head_admission(roots, implementation_commit=implementation_commit)
    if locked.get("implementation_commit") != implementation_commit:
        raise RuntimeError("Invocation B implementation head differs from scenario lock")
    for directory in (
        roots.invocation_b,
        roots.invocation_b / "commands",
        roots.invocation_b / "exceptions",
        roots.invocation_b / "snapshots",
    ):
        ensure_private_directory(directory)
    journal = DiagnosticJournal(
        roots.invocation_b / "events.jsonl",
        run_kind=DiagnosticRunKind.INVOCATION_B,
        run_id="invocation-b",
    )
    tracker = _StageTracker(
        journal,
        ExceptionArtifactStore(roots.invocation_b / "exceptions"),
    )
    state = _RunState()

    def on_command_start(identity: DiagnosticCommandIdentity) -> None:
        if identity is DiagnosticCommandIdentity.COMPOSE_UP:
            state.compose_start_requested = True
            state.cleanup_required = True
            tracker.pass_stage(DiagnosticStage.COMPOSE_START_REQUESTED)

    def on_command_return(
        identity: DiagnosticCommandIdentity,
        return_code: int | None,
        timed_out: bool,
    ) -> None:
        if identity is DiagnosticCommandIdentity.COMPOSE_UP:
            state.compose_start_returned = not timed_out
            state.compose_start_return_code = return_code
            if not timed_out and return_code == 0:
                tracker.pass_stage(DiagnosticStage.COMPOSE_START_RETURNED)
        elif identity is DiagnosticCommandIdentity.COMPOSE_DOWN:
            if not timed_out and return_code == 0:
                tracker.pass_stage(DiagnosticStage.COMPOSE_DOWN_RETURNED)

    runner = RecordingCommandRunner(
        roots.invocation_b / "commands",
        on_start=on_command_start,
        on_return=on_command_return,
    )
    environment = environment_factory(
        repository_root=config.repository_root,
        bundle=config.sandbox,
        flagd_directory=roots.runtime / "invocation-b" / "flagd",
        runner=runner,
    )
    _consume_live_run_budget(config, roots)
    schema_suffix = _schema_suffix(config)
    verdict_policy = get_invocation_b_verdict_policy(schema_suffix)
    uses_fault_projection = schema_suffix in {"v5", "v6"}
    terminal: dict[str, object] = {
        "schema_version": f"live-e2e.invocation-b-terminal.{schema_suffix}",
        "version": config.authority.version,
        "verdict": verdict_policy.provider_preflight_failed,
        "run_kind": DiagnosticRunKind.INVOCATION_B.value,
        "run_id": "invocation-b",
        "complete_live_run_count": 1,
        "implementation_commit": implementation_commit,
        "result_head": implementation_commit,
        "approval_valid": True,
        "approval_mode": "HUMAN_PREAUTHORIZED_FROZEN_REMEDIATION_RUNBOOK",
        "claim_boundary": list(config.reporting.claim_boundary),
        "provider_calls": 0,
        "model_calls": 0,
        "a0_context_builder_calls": 0,
        "fault_time_a0_context_sha256": None,
        "fault_injections": 0,
        "forward_mutations": 0,
        "rollback_mutations": 0,
        "compose_start_requested": False,
        "compose_start_returned": False,
        "image_authority_sha256": None,
        "image_verification_sha256": None,
        "compose_structure_sha256": None,
        "compose_instance_sha256": None,
    }
    provider: Any = None
    controller: Any = None
    forward_counter = ForwardMutationCounter(roots.journal / "forward-mutation.txt")
    private_exception: DiagnosticExceptionReference | None = None
    tracker.pass_stage(DiagnosticStage.PRIVATE_ROOT_BOUND)
    tracker.pass_stage(DiagnosticStage.AUTHORITY_VERIFIED)
    tracker.pass_stage(
        DiagnosticStage.WORKTREE_VERIFIED,
        output_value=implementation_commit,
    )
    try:
        provider = provider_factory(config)
        synthetic = _synthetic_provider_context(cast(Any, config))
        preflight = provider.diagnose(synthetic)
        terminal["provider_calls"] = provider.calls
        if provider.calls != 1 or not provider.usage_known or provider.last_usage_tokens is None:
            raise RuntimeError("Provider preflight did not return known bounded usage")
        terminal["provider_preflight_passed"] = True
        terminal["provider_preflight_usage_tokens"] = provider.last_usage_tokens
        write_private_json(
            roots.provider / "synthetic-preflight-v2.json",
            {
                "request_sha256": provider.last_request_sha256,
                "diagnosis": preflight,
                "usage_tokens": provider.last_usage_tokens,
            },
            create_once=True,
        )
        sleep(config.sandbox.budget.minimum_request_spacing_seconds)
        docker = tracker.execute(
            DiagnosticStage.LOCAL_DOCKER_VERIFIED,
            environment.verify_local_docker,
            failure_code=DiagnosticFailureCode.DOCKER_AUTHORITY_UNAVAILABLE,
        )
        tracker.execute(
            DiagnosticStage.UPSTREAM_PIN_VERIFIED,
            environment.verify_upstream,
            failure_code=DiagnosticFailureCode.UPSTREAM_PIN_DRIFT,
        )
        resolved, raw_compose = tracker.execute(
            DiagnosticStage.COMPOSE_RESOLUTION_STARTED,
            environment.resolve,
            failure_code=DiagnosticFailureCode.COMPOSE_RESOLUTION_FAILED,
        )
        tracker.pass_stage(
            DiagnosticStage.COMPOSE_RESOLVED,
            output_value=resolved.compose_sha256,
        )
        _verify_scenario_lock_for_invocation_b(
            config,
            roots,
            locked=locked,
            implementation_commit=implementation_commit,
        )
        image_run = _verify_v3_images(
            environment=environment,
            resolved=resolved,
            raw_compose=raw_compose,
            roots=roots,
            run_root=roots.invocation_b,
            run_id="invocation-b",
            run_kind=DiagnosticRunKind.INVOCATION_B,
            tracker=tracker,
            expected_structure_sha256=cast(str, locked["compose_structure_sha256"]),
            expected_authority_sha256=cast(str, locked["image_authority_sha256"]),
        )
        terminal["image_authority_sha256"] = image_run.authority.authority_sha256
        terminal["image_verification_sha256"] = (
            image_run.verification.verification_sha256
        )
        terminal["compose_structure_sha256"] = image_run.compose.structure_sha256
        terminal["compose_instance_sha256"] = image_run.compose.instance_sha256
        if any(environment.verify_owned_resources(require_complete=False).values()):
            raise RuntimeError("Invocation B requires no owned residue")
        write_private_json(
            roots.invocation_b / "resolved-compose.json",
            raw_compose,
            create_once=True,
        )
        controller = tracker.execute(
            DiagnosticStage.FAULT_CONTROLLER_PREPARATION_STARTED,
            lambda: controller_factory(
                cast(Any, config),
                cast(Any, roots),
                resolved.endpoints,
                flagd_directory=roots.runtime / "invocation-b" / "flagd",
            ),
            failure_code=DiagnosticFailureCode.FAULT_CONTROLLER_PREPARATION_FAILED,
        )
        tracker.pass_stage(DiagnosticStage.FAULT_CONTROLLER_PREPARED)
        tracker.execute(
            DiagnosticStage.PORT_PREFLIGHT_STARTED,
            environment.verify_ports_available,
            failure_code=DiagnosticFailureCode.PORT_CONFLICT,
        )
        tracker.pass_stage(DiagnosticStage.PORTS_AVAILABLE)
        tracker.execute(
            DiagnosticStage.DOCKER_BASELINE_SNAPSHOT_CAPTURED,
            environment.snapshot_all_resources,
            failure_code=DiagnosticFailureCode.DOCKER_BASELINE_SNAPSHOT_FAILED,
        )
        try:
            environment.start()
        except Exception as error:
            if tracker.failed_stage is None:
                tracker.fail_external(
                    error,
                    stage=DiagnosticStage.COMPOSE_START_RETURNED,
                    failure_code=DiagnosticFailureCode.COMPOSE_UP_FAILED,
                )
            raise
        counts = tracker.execute(
            DiagnosticStage.OWNED_RESOURCE_INVENTORY_VERIFIED,
            lambda: environment.verify_owned_resources(require_complete=True),
            failure_code=DiagnosticFailureCode.OWNED_RESOURCE_INVENTORY_INCOMPLETE,
        )
        state.owned_resources_after_start = dict(counts)
        state.cleanup_required = True
        state.service_health_wait_started = True
        health = tracker.execute(
            DiagnosticStage.SERVICE_HEALTH_WAIT_STARTED,
            environment.wait_healthy,
            failure_code=DiagnosticFailureCode.SERVICE_HEALTH_TIMEOUT,
        )
        state.services_healthy = bool(health) and all(health.values())
        tracker.execute(
            DiagnosticStage.SERVICES_HEALTHY,
            lambda: sum(health.values())
            if state.services_healthy
            else (_ for _ in ()).throw(RuntimeError("sandbox health result is incomplete")),
            failure_code=DiagnosticFailureCode.SERVICE_HEALTH_TIMEOUT,
        )
        tracker.execute(
            DiagnosticStage.STABILIZATION_STARTED,
            lambda: sleep(config.sandbox.verification.minimum_stabilization_seconds),
        )
        tracker.pass_stage(DiagnosticStage.STABILIZATION_COMPLETED)
        baseline_state = tracker.execute(
            DiagnosticStage.BASELINE_CONFIGURATION_READ_STARTED,
            controller.read_current,
            failure_code=DiagnosticFailureCode.BASELINE_CONFIGURATION_UNAVAILABLE,
        )
        tracker.execute(
            DiagnosticStage.BASELINE_CONFIGURATION_VERIFIED,
            lambda: baseline_state.document_sha256
            if baseline_state.document_sha256 == config.sandbox.scenario.baseline_document_sha256
            else (_ for _ in ()).throw(RuntimeError("Invocation B baseline differs")),
            failure_code=DiagnosticFailureCode.BASELINE_CONFIGURATION_MISMATCH,
        )
        state.baseline_verified = True
        if uses_fault_projection:
            baseline_1 = _capture_sli_window(
                cast(Any, config), resolved.endpoints.prometheus, phase="BASELINE"
            )
            baseline_2 = _capture_sli_window(
                cast(Any, config), resolved.endpoints.prometheus, phase="BASELINE"
            )
        else:
            baseline_1, _ = _capture_e2e_window(
                cast(Any, config),
                cast(Any, roots),
                label="invocation-b-baseline-1",
                endpoints=resolved.endpoints,
                phase="BASELINE",
            )
            baseline_2, _ = _capture_e2e_window(
                cast(Any, config),
                cast(Any, roots),
                label="invocation-b-baseline-2",
                endpoints=resolved.endpoints,
                phase="BASELINE",
            )
        baseline = (baseline_1, baseline_2)
        baseline_snapshot = _broad_metric_snapshot(
            resolved.endpoints.prometheus,
            at=baseline[-1].ended_at,
        )
        terminal["baseline_windows"] = 2
        terminal["fault_injections"] = 1
        fault_state = controller.inject_fault()
        if fault_state.document_sha256 != config.sandbox.scenario.fault_document_sha256:
            raise RuntimeError("frozen fault document hash differs")
        sleep(config.sandbox.verification.minimum_stabilization_seconds)
        if uses_fault_projection:
            fault_1 = _capture_sli_window(
                cast(Any, config), resolved.endpoints.prometheus, phase="FAULT"
            )
            fault_2 = _capture_sli_window(
                cast(Any, config), resolved.endpoints.prometheus, phase="FAULT"
            )
        else:
            fault_1, _ = _capture_e2e_window(
                cast(Any, config),
                cast(Any, roots),
                label="invocation-b-fault-1",
                endpoints=resolved.endpoints,
                phase="FAULT",
            )
            fault_2, fault_sources = _capture_e2e_window(
                cast(Any, config),
                cast(Any, roots),
                label="invocation-b-fault-2",
                endpoints=resolved.endpoints,
                phase="FAULT",
            )
        fault = (fault_1, fault_2)
        terminal["verdict"] = "BLOCKED_FAULT_IMPACT_NOT_OBSERVED"
        if not fault_impact_passed(baseline, fault, config.sandbox):
            raise RuntimeError("frozen two-window fault impact Gate did not pass")
        terminal["fault_impact_passed"] = True
        terminal["verdict"] = "BLOCKED_LIVE_TELEMETRY_SOURCE_UNAVAILABLE"
        if uses_fault_projection:
            fault_batch = collect_ordered_source_batch(
                instrumentation=load_instrumentation_config(
                    config.repository_root / V3_CONFIG_RELATIVE
                ),
                endpoints=resolved.endpoints,
                telemetry_root=roots.telemetry,
                run_root=roots.invocation_b,
                run_id="invocation-b",
                projection=config.projection,
                tracker=tracker,
                sleep=sleep,
            )
            fault_sources = fault_batch.source_results
        terminal["source_availability"] = {
            item.source: item.status.value for item in fault_sources
        }
        terminal["source_counts"] = _safe_source_counts(fault_sources)
        terminal["invalid_refs"] = sum(item.invalid_ref_count for item in fault_sources)
        tracker.pass_stage(DiagnosticStage.METRICS_PREFLIGHT_STARTED)
        tracker.pass_stage(DiagnosticStage.METRICS_PREFLIGHT_COMPLETED)
        tracker.pass_stage(DiagnosticStage.LOGS_PREFLIGHT_STARTED)
        tracker.pass_stage(DiagnosticStage.LOGS_PREFLIGHT_COMPLETED)
        tracker.pass_stage(DiagnosticStage.TRACES_PREFLIGHT_STARTED)
        tracker.pass_stage(DiagnosticStage.TRACES_PREFLIGHT_COMPLETED)
        tracker.execute(
            DiagnosticStage.EVIDENCE_RESOLUTION_COMPLETED,
            lambda: None
            if terminal["invalid_refs"] == 0
            else (_ for _ in ()).throw(RuntimeError("live Evidence refs are invalid")),
            failure_code=DiagnosticFailureCode.EVIDENCE_RESOLUTION_FAILED,
        )
        fault_snapshot = _broad_metric_snapshot(
            resolved.endpoints.prometheus,
            at=fault[-1].ended_at,
        )
        observations = tuple(
            LiveMetricObservation(
                service_name=service,
                baseline_requests=baseline_snapshot.get(service, (0.0, 0.0, 0.0))[0],
                baseline_errors=baseline_snapshot.get(service, (0.0, 0.0, 0.0))[1],
                fault_requests=values[0],
                fault_errors=values[1],
                baseline_p95_ms=baseline_snapshot.get(service, (0.0, 0.0, 0.0))[2],
                fault_p95_ms=values[2],
                first_anomaly_at=fault[0].started_at,
            )
            for service, values in sorted(fault_snapshot.items())
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
            roots.telemetry / "invocation-b-model-raw-observations.json",
            raw_observations,
            create_once=True,
        )
        bound_metrics, bound_logs, bound_traces, resolver_refs = _seal_model_evidence_resolver(
            cast(Any, roots),
            label="invocation-b",
            window_start=fault[0].started_at,
            window_end=fault[-1].ended_at,
            metrics=observations,
            logs=logs,
            traces=traces,
        )
        terminal["verdict"] = "BLOCKED_BOUNDED_MULTISERVICE_PROJECTION_UNAVAILABLE"
        tracker.pass_stage(DiagnosticStage.MULTISERVICE_PROJECTION_STARTED)

        def build_context() -> Any:
            if uses_fault_projection:
                terminal["a0_context_builder_calls"] = cast(
                    int, terminal["a0_context_builder_calls"]
                ) + 1
                return build_fault_time_a0_context(
                    window_start=baseline[0].started_at,
                    window_end=fault[-1].ended_at,
                    metrics=bound_metrics,
                    logs=bound_logs,
                    traces=bound_traces,
                    resolvable_refs=resolver_refs,
                    projection=cast(Any, config.projection),
                    summary_path=(
                        roots.invocation_b / "projection-input-summary.json"
                    ),
                )
            return build_live_a0_context(
                window_start=baseline[0].started_at,
                window_end=fault[-1].ended_at,
                metrics=bound_metrics,
                logs=bound_logs,
                traces=bound_traces,
                resolvable_refs=resolver_refs,
                projection=cast(Any, config.projection),
            )

        context = tracker.execute(
            DiagnosticStage.MULTISERVICE_PROJECTION_COMPLETED,
            build_context,
            failure_code=DiagnosticFailureCode.MULTISERVICE_PROJECTION_FAILED,
        )
        if uses_fault_projection:
            _enrich_v5_invocation_b_terminal(roots, terminal)
        terminal["visible_service_count"] = len(context.visible_entities)
        terminal["projection_completed"] = True
        if uses_fault_projection:
            fault_context_path = (
                roots.invocation_b / "fault-time-a0-context.json"
            )
            if schema_suffix == "v6":
                write_fault_time_context_evidence(
                    private_root=roots.root,
                    invocation_b_root=roots.invocation_b,
                    context=context,
                    terminal=terminal,
                )
            else:
                write_private_json(
                    fault_context_path,
                    context,
                    create_once=True,
                )
                terminal["fault_time_a0_context_sha256"] = file_sha256(
                    fault_context_path
                )
        _write_model_evidence_index(
            cast(Any, roots),
            context,
            raw_observations=raw_observations,
        )
        if schema_suffix != "v6":
            write_private_json(
                roots.provider / "live-context-v2.json",
                context,
                create_once=True,
            )
        terminal["verdict"] = "LIVE_DIAGNOSIS_GATE_NOT_PASSED_NO_REMEDIATION"
        diagnosis = _diagnosis_from_initial(provider, context)
        terminal["provider_calls"] = provider.calls
        terminal["model_calls"] = 1
        if provider.calls != 2 or not provider.usage_known:
            raise RuntimeError("live A0 call exceeded the frozen Provider budget")
        write_private_json(
            roots.provider / "live-diagnosis-v2.json",
            diagnosis,
            create_once=True,
        )
        diagnosis_gate = evaluate_diagnosis_gate(diagnosis, config.sandbox)
        terminal["diagnosis_gate"] = diagnosis_gate.passed
        terminal["diagnosis_correct"] = (
            diagnosis.root_service == config.sandbox.scenario.expected_root_service
            and diagnosis.fault_class == config.sandbox.scenario.expected_fault_class
        )
        if not diagnosis_gate.passed:
            raise RuntimeError("A0 Diagnosis Gate denied remediation")
        terminal["verdict"] = "BLOCKED_POLICY_REJECTED"
        plan = build_plan(diagnosis, config.sandbox)
        terminal["plan_action"] = plan.action
        policy = evaluate_policy(
            plan=plan,
            diagnosis=diagnosis,
            request=request,
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
        terminal["policy_verdict"] = policy.verdict.value
        if policy.verdict.value != "ALLOW":
            raise RuntimeError("Policy Gate denied the frozen remediation")
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
        write_private_json(
            roots.invocation_b / "execution-receipt.json",
            receipt,
            create_once=True,
        )
        sleep(config.sandbox.verification.minimum_stabilization_seconds)
        if uses_fault_projection:
            recovery_1 = _capture_sli_window(
                cast(Any, config), resolved.endpoints.prometheus, phase="RECOVERY"
            )
            recovery_2 = _capture_sli_window(
                cast(Any, config), resolved.endpoints.prometheus, phase="RECOVERY"
            )
        else:
            recovery_1, _ = _capture_e2e_window(
                cast(Any, config),
                cast(Any, roots),
                label="invocation-b-recovery-1",
                endpoints=resolved.endpoints,
                phase="RECOVERY",
            )
            recovery_2, _ = _capture_e2e_window(
                cast(Any, config),
                cast(Any, roots),
                label="invocation-b-recovery-2",
                endpoints=resolved.endpoints,
                phase="RECOVERY",
            )
        verification = IndependentVerifier().verify(
            plan=plan,
            receipt=receipt,
            current=controller.read_current(),
            baseline_windows=baseline,
            recovery_windows=(recovery_1, recovery_2),
            services_healthy=all(environment.service_health().values()),
            labels_exact=True,
            bundle=config.sandbox,
        )
        write_private_json(
            roots.invocation_b / "verification.json",
            verification,
            create_once=True,
        )
        terminal["recovery_verification_passed"] = verification.passed
        if verification.passed:
            terminal["verdict"] = config.authority.invocation_b_success
        else:
            try:
                rollback = compensate_rollback(
                    receipt=receipt,
                    verification=verification,
                    controller=controller,
                    on_mutation=lambda: _record_rollback(roots, terminal),
                )
            except Exception:
                terminal["verdict"] = "BLOCKED_ROLLBACK_FAILED_MANUAL_CLEANUP_REQUIRED"
                terminal["manual_cleanup_command"] = environment.manual_cleanup_command()
                raise
            write_private_json(
                roots.invocation_b / "rollback.json",
                rollback,
                create_once=True,
            )
            terminal["rollback_exact_hash_verified"] = rollback.exact_hash_verified
            if (
                not rollback.exact_hash_verified
                or rollback.restored_sha256 != config.sandbox.scenario.fault_document_sha256
            ):
                terminal["verdict"] = "BLOCKED_ROLLBACK_FAILED_MANUAL_CLEANUP_REQUIRED"
                raise RuntimeError("compensating rollback hash differs")
    except Exception as error:
        if provider is not None and hasattr(provider, "calls"):
            terminal["provider_calls"] = provider.calls
        if (
            terminal.get("provider_preflight_passed") is True
            and tracker.failed_stage is None
        ):
            tracker.fail_external(
                error,
                stage=DiagnosticStage.CLEANUP_STARTED,
                failure_code=DiagnosticFailureCode.UNCLASSIFIED_RUNTIME_FAILURE,
            )
        effective_failure_code = (
            tracker.failure_code
            or DiagnosticFailureCode.UNCLASSIFIED_RUNTIME_FAILURE
        )
        if terminal.get("provider_preflight_passed") is True and (
            terminal.get("verdict") == verdict_policy.provider_preflight_failed
        ):
            terminal["verdict"] = verdict_policy.terminal_for(
                effective_failure_code
            )
        terminal["failed_stage"] = (
            None if tracker.failed_stage is None else tracker.failed_stage.value
        )
        terminal["last_completed_stage"] = (
            None
            if tracker.root_last_completed_stage is None
            else tracker.root_last_completed_stage.value
        )
        terminal["failure_code"] = effective_failure_code.value
        terminal["failure_type"] = type(error).__name__
        private_exception = ExceptionArtifactStore(
            roots.invocation_b / "exceptions"
        ).capture(
            error,
            stage=tracker.failed_stage or DiagnosticStage.AUTHORITY_VERIFIED,
            sequence=9999,
        )
    finally:
        terminal["forward_mutations"] = forward_counter.count
        if uses_fault_projection:
            _enrich_v5_invocation_b_terminal(roots, terminal)
        cleanup_verdict, cleanup_payload, cleanup_failure = _cleanup(
            tracker=tracker,
            state=state,
            environment=environment,
            controller=controller,
            baseline_sha256=config.sandbox.scenario.baseline_document_sha256,
            run_root=roots.invocation_b,
        )
        terminal["compose_start_requested"] = state.compose_start_requested
        terminal["compose_start_returned"] = state.compose_start_returned
        terminal["compose_start_return_code"] = state.compose_start_return_code
        terminal["cleanup_verdict"] = cleanup_verdict
        terminal["cleanup"] = cleanup_payload
        terminal["cleanup_failure_code"] = cleanup_failure
        if cleanup_verdict == "BLOCKED":
            terminal["verdict"] = verdict_policy.cleanup_incomplete
        if private_exception is not None:
            terminal["exception_type"] = private_exception.exception_type
            terminal["exception_module"] = private_exception.exception_module
            terminal["exception_message_sha256"] = (
                private_exception.exception_message_sha256
            )
            terminal["traceback_sha256"] = private_exception.traceback_sha256
            terminal["diagnostic_artifact_refs"] = [
                {
                    "artifact_ref": private_exception.artifact_ref,
                    "artifact_sha256": private_exception.artifact_sha256,
                }
            ]
        terminal["private_permissions_verified"] = True
        tracker.journal.record(
            stage=DiagnosticStage.TERMINAL_SEALED,
            status=DiagnosticEventStatus.STARTED,
            started_at=datetime.now(timezone.utc),
            input_value=terminal,
        )
        write_private_json(
            roots.invocation_b / "terminal.json",
            terminal,
            create_once=True,
        )
        tracker.journal.record(
            stage=DiagnosticStage.TERMINAL_SEALED,
            status=DiagnosticEventStatus.PASSED,
            started_at=datetime.now(timezone.utc),
            ended_at=datetime.now(timezone.utc),
            output_value={"terminal_sha256": canonical_sha256(terminal)},
        )
        roots.verify()
        public_outputs = public_writer(config, terminal)
        if schema_suffix != "v6":
            terminal["public_outputs"] = public_outputs
            terminal["public_projection_status"] = "PASSED"
        _complete_live_run_budget(roots, verdict=cast(str, terminal["verdict"]))
    return terminal


__all__ = [
    "NoFaultEvidence",
    "build_plan_template",
    "record_human_approval_for_invocation_b",
    "run_canonical_invocation_a",
    "run_diagnostic_preflight",
    "run_invocation_b",
    "scenario_lock_manifest",
    "verify_public_result",
]
