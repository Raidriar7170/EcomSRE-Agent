"""Two-invocation workflow for the independent frozen live scenario."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
from typing import Literal, Protocol, cast

from ecomsre_live_sandbox.contracts import (
    ApprovalRequest,
    ConfigBundle,
    DiagnosisResult,
    HumanApprovalRecord,
    LiveRemediationPlan,
    LogEvidence,
    PolicyVerdict,
    RunEvent,
    SLIWindow,
    TraceEvidence,
    canonical_json_bytes,
    canonical_sha256,
    ensure_private_directory,
    file_sha256,
    load_bundle,
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
from ecomsre_live_sandbox.environment import SandboxEnvironment
from ecomsre_live_sandbox.telemetry import (
    CapturedTelemetry,
    LiveTelemetryAdapter,
    build_a0_context,
)
from ecomsre.model.gateway import OpenAICompatibleConfig
from ecomsre_rca100.contracts import RCA100InitialDiagnosis
from ecomsre_rca100.prompt import (
    OpenAICompatibleRCA100Provider,
    output_schema_sha256,
    prompt_sha256,
)
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


CONFIG_RELATIVE = Path("config/live-telemetry-controlled-remediation-v1")
EVIDENCE_BOUNDARY = (
    "LIVE_LOCAL_SANDBOX_DEMO",
    "CONTROLLED_FAULT_INJECTION",
    "HUMAN_APPROVED_REMEDIATION",
    "NOT_PRODUCTION",
    "NOT_EXTERNAL_BENCHMARK",
    "NOT_SECURITY_VULNERABILITY_DETECTION",
)
RunPhase = Literal[
    "SCENARIO_FROZEN",
    "APPROVAL_REQUESTED",
    "HUMAN_APPROVED",
    "SANDBOX_STARTED",
    "BASELINE_VERIFIED",
    "FAULT_INJECTED",
    "FAULT_IMPACT_VERIFIED",
    "LIVE_TELEMETRY_CAPTURED",
    "DIAGNOSIS_COMPLETED",
    "PLAN_ADMITTED",
    "REMEDIATION_EXECUTED",
    "REMEDIATION_VERIFIED",
    "ROLLBACK_COMPLETED",
    "SANDBOX_CLEANED",
]
EvidenceSource = Literal["METRICS", "LOGS", "TRACES"]
FaultClass = Literal[
    "LOCAL_RESOURCE",
    "PROPAGATION",
    "NETWORK",
    "DEPENDENCY",
    "APPLICATION",
    "UNKNOWN",
]


@dataclass(frozen=True, slots=True)
class PrivateRoots:
    control: Path
    runtime: Path
    telemetry: Path
    report: Path

    def prepare(self) -> None:
        for root in (self.control, self.runtime, self.telemetry, self.report):
            ensure_private_directory(root)


class DiagnosisProvider(Protocol):
    @property
    def calls(self) -> int: ...

    @property
    def last_usage_tokens(self) -> int | None: ...

    @property
    def usage_known(self) -> bool: ...

    def diagnose(self, context: RCA100AgentContext) -> RCA100InitialDiagnosis: ...


class ManualCleanupRequired(RuntimeError):
    def __init__(self, command: str) -> None:
        self.command = command
        super().__init__("owned sandbox cleanup requires the exact manual command")


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


def _tracked_lock(repository_root: Path, bundle: ConfigBundle) -> dict[str, object]:
    implementation_commit = _git(repository_root, "rev-parse", "HEAD")
    branch = _git(repository_root, "branch", "--show-current")
    if branch != "feature/live-telemetry-controlled-remediation-v1":
        raise RuntimeError("live sandbox branch identity drifted")
    if _git(repository_root, "status", "--porcelain=v1"):
        raise RuntimeError("implementation worktree must be clean before scenario freeze")
    paths = [
        *sorted((repository_root / CONFIG_RELATIVE).glob("*")),
        *sorted((repository_root / "src/ecomsre_live_sandbox").glob("*.py")),
        *sorted((repository_root / "scripts/live_sandbox").glob("*.py")),
        repository_root / "tests/live_sandbox/test_live_sandbox.py",
    ]
    hashes = {
        str(path.relative_to(repository_root)): file_sha256(path)
        for path in paths
        if path.is_file()
    }
    return {
        "schema_version": "live-sandbox.scenario-lock.v1",
        "implementation_commit": implementation_commit,
        "branch": branch,
        "scenario_id": bundle.scenario.scenario_id,
        "sandbox_id": bundle.environment.sandbox_id,
        "action": bundle.policy.action,
        "target_service": bundle.scenario.target_service,
        "configuration_key": bundle.scenario.target_configuration_key,
        "baseline_sha256": bundle.scenario.baseline_document_sha256,
        "fault_sha256": bundle.scenario.fault_document_sha256,
        "prompt_sha256": bundle.diagnosis.prompt_sha256,
        "output_schema_sha256": bundle.diagnosis.output_schema_sha256,
        "provider_model": bundle.diagnosis.model,
        "tracked_files": hashes,
        "evidence_boundary": EVIDENCE_BOUNDARY,
    }


def verify_scenario_lock(
    repository_root: Path, control_root: Path, bundle: ConfigBundle
) -> Mapping[str, object]:
    path = control_root / "scenario-lock.json"
    if path.is_symlink() or not path.is_file():
        raise RuntimeError("frozen scenario lock is unavailable")
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, Mapping):
        raise RuntimeError("frozen scenario lock is malformed")
    expected = {
        "scenario_id": bundle.scenario.scenario_id,
        "sandbox_id": bundle.environment.sandbox_id,
        "action": bundle.policy.action,
        "target_service": bundle.scenario.target_service,
        "configuration_key": bundle.scenario.target_configuration_key,
        "baseline_sha256": bundle.scenario.baseline_document_sha256,
        "fault_sha256": bundle.scenario.fault_document_sha256,
        "prompt_sha256": bundle.diagnosis.prompt_sha256,
        "output_schema_sha256": bundle.diagnosis.output_schema_sha256,
        "provider_model": bundle.diagnosis.model,
    }
    if any(raw.get(key) != value for key, value in expected.items()):
        raise RuntimeError("frozen scenario lock fields drifted")
    commit = raw.get("implementation_commit")
    if not isinstance(commit, str) or len(commit) != 40:
        raise RuntimeError("scenario implementation commit is invalid")
    _git(repository_root, "merge-base", "--is-ancestor", commit, "HEAD")
    tracked = raw.get("tracked_files")
    if not isinstance(tracked, Mapping):
        raise RuntimeError("scenario tracked-file lock is unavailable")
    for relative, expected_hash in tracked.items():
        if not isinstance(relative, str) or not isinstance(expected_hash, str):
            raise RuntimeError("scenario tracked-file lock is malformed")
        path = (repository_root / relative).resolve()
        if not path.is_relative_to(repository_root.resolve()) or file_sha256(path) != expected_hash:
            raise RuntimeError(f"scenario tracked file drifted: {relative}")
    return raw


def _append_event(path: Path, event: RunEvent) -> None:
    ensure_private_directory(path.parent)
    if path.exists() and (path.is_symlink() or not path.is_file()):
        raise RuntimeError("run event journal is not a regular file")
    flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT
    descriptor = os.open(path, flags, 0o600)
    try:
        with os.fdopen(descriptor, "ab") as handle:
            handle.write(canonical_json_bytes(event))
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        path.chmod(0o600)


class EventRecorder:
    def __init__(self, path: Path) -> None:
        if path.exists():
            raise FileExistsError("a run event journal already exists; rerun is forbidden")
        self.path = path
        self.sequence = 0

    def record(
        self,
        phase: RunPhase,
        status: str,
        *,
        input_value: object | None = None,
        output_value: object | None = None,
        safe_aggregate: Mapping[str, object] | None = None,
    ) -> None:
        self.sequence += 1
        _append_event(
            self.path,
            RunEvent(
                sequence=self.sequence,
                timestamp=datetime.now(timezone.utc),
                phase=phase,
                status=status,
                input_sha256=(
                    canonical_sha256(input_value) if input_value is not None else None
                ),
                output_sha256=(
                    canonical_sha256(output_value) if output_value is not None else None
                ),
                safe_aggregate=dict(safe_aggregate or {}),
            ),
        )


def make_provider(
    bundle: ConfigBundle,
    environment: Mapping[str, str] | None = None,
) -> OpenAICompatibleRCA100Provider:
    if prompt_sha256() != bundle.diagnosis.prompt_sha256:
        raise RuntimeError("A0 system Prompt hash drifted")
    if output_schema_sha256() != bundle.diagnosis.output_schema_sha256:
        raise RuntimeError("A0 output schema hash drifted")
    config = OpenAICompatibleConfig.from_environment(environment)
    if config is None:
        raise RuntimeError("complete Provider configuration is unavailable")
    return OpenAICompatibleRCA100Provider(
        config=config,
        expected_model=bundle.diagnosis.model,
        timeout_seconds=bundle.budget.provider_timeout_seconds,
        max_completion_tokens=bundle.diagnosis.max_completion_tokens,
    )


def run_a0_diagnosis(
    context: RCA100AgentContext,
    provider: DiagnosisProvider,
) -> DiagnosisResult:
    started = time.monotonic()
    initial = provider.diagnose(context)
    latency = time.monotonic() - started
    ontology = classify_fault_ontology(initial.fault_type)
    entity = initial.root_cause_entity_ref
    visibility = EvidenceVisibilitySummary(
        catalog_entities=frozenset({entity}),
        metrics_entities=frozenset(
            {entity} if any(ref.startswith("metric:") for ref in initial.evidence_refs) else set()
        ),
        logs_entities=frozenset(
            {entity} if any(ref.startswith("log:") for ref in initial.evidence_refs) else set()
        ),
        traces_entities=frozenset(
            {entity} if any(ref.startswith("trace:") for ref in initial.evidence_refs) else set()
        ),
        events_entities=frozenset(),
        alerts_entities=frozenset(),
        topology_entities=frozenset(),
    )
    hierarchical = execute_unified_hierarchical_rca(
        StrongSingleHierarchicalInput(
            initial_root=entity,
            initial_layer=CanonicalEntityLayer.SERVICE,
            initial_hierarchy_path=EntityHierarchyPath(
                entity=entity,
                explicit_parents=(),
                service_ancestor_or_none=entity,
                infrastructure_ancestor_or_none=None,
            ),
            fault_type_raw=initial.fault_type,
            fault_ontology_class=ontology,
            evidence_visibility=visibility,
            supporting_evidence_refs=initial.evidence_refs,
        )
    )
    source_map: Mapping[str, EvidenceSource] = {
        "metric:": "METRICS",
        "log:": "LOGS",
        "trace:": "TRACES",
    }
    sources = tuple(
        dict.fromkeys(
            source
            for reference in initial.evidence_refs
            for prefix, source in source_map.items()
            if reference.startswith(prefix)
        )
    )
    return DiagnosisResult(
        terminal="COMPLETED",
        root_service=hierarchical.final_root.rsplit("|", 1)[-1],
        root_entity_ref=hierarchical.final_root,
        fault_type_raw=hierarchical.fault_type_raw,
        fault_class=cast(FaultClass, hierarchical.fault_ontology_class.value),
        confidence=initial.confidence,
        evidence_refs=initial.evidence_refs,
        evidence_source_types=cast(tuple[EvidenceSource, ...], sources),
        summary=initial.summary,
        semantic_model_calls=1,
        specialist_calls=0,
        fusion_calls=0,
        provider_attempts=1,
        transport_retries=0,
        usage_tokens=provider.last_usage_tokens,
        latency_seconds=latency,
    )


def _controller(
    repository_root: Path,
    roots: PrivateRoots,
    bundle: ConfigBundle,
    endpoints: object,
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
    from ecomsre_live_sandbox.contracts import LocalEndpoints

    if not isinstance(endpoints, LocalEndpoints):
        raise TypeError("resolved endpoints are not typed")
    return SandboxFaultController(
        endpoints=endpoints,
        bundle=bundle,
        flag_file=flag_file,
        baseline_document=baseline,
        fault_document=fault,
    )


def _environment(
    repository_root: Path, roots: PrivateRoots, bundle: ConfigBundle
) -> SandboxEnvironment:
    return SandboxEnvironment(
        repository_root=repository_root,
        bundle=bundle,
        flagd_directory=roots.runtime / "flagd",
    )


def run_invocation_a(repository_root: Path, roots: PrivateRoots) -> dict[str, object]:
    repository_root = repository_root.resolve()
    roots.prepare()
    bundle = load_bundle(repository_root / CONFIG_RELATIVE)
    if prompt_sha256() != bundle.diagnosis.prompt_sha256:
        raise RuntimeError("A0 system Prompt differs from the frozen main runtime")
    if output_schema_sha256() != bundle.diagnosis.output_schema_sha256:
        raise RuntimeError("A0 output schema differs from the frozen main runtime")
    event = EventRecorder(roots.report / "invocation-a-events.jsonl")
    environment = _environment(repository_root, roots, bundle)
    docker = environment.verify_local_docker()
    environment.verify_upstream()
    resolved, raw_compose = environment.resolve()
    write_private_json(roots.control / "resolved-compose.json", raw_compose, create_once=True)
    image_lock_sha256 = environment.verify_cached_images(resolved, roots.control)
    controller = _controller(repository_root, roots, bundle, resolved.endpoints)
    baseline_restored = False
    cleanup = None
    preflight: CapturedTelemetry | None = None
    try:
        environment.start()
        event.record(
            "SANDBOX_STARTED",
            "PREFLIGHT_ONLY",
            output_value=resolved,
            safe_aggregate={"services": len(resolved.services), "fault_injected": False},
        )
        health = environment.wait_healthy()
        time.sleep(bundle.verification.minimum_stabilization_seconds)
        current = controller.read_current()
        if current.document_sha256 != bundle.scenario.baseline_document_sha256:
            raise RuntimeError("preflight configuration is not the frozen baseline")
        event.record(
            "BASELINE_VERIFIED",
            "EXACT",
            output_value=current,
            safe_aggregate={"services_healthy": all(health.values())},
        )
        preflight = LiveTelemetryAdapter(
            endpoints=resolved.endpoints, bundle=bundle
        ).capture(
            phase="PREFLIGHT",
            duration_seconds=bundle.scenario.verification_window_seconds,
            service_health=health,
        )
        write_private_json(
            roots.telemetry / "preflight-raw.json", preflight.raw, create_once=True
        )
        write_private_json(
            roots.telemetry / "preflight-snapshot.json",
            preflight.snapshot,
            create_once=True,
        )
        event.record(
            "LIVE_TELEMETRY_CAPTURED",
            "PREFLIGHT_SOURCES_AVAILABLE",
            output_value=preflight.snapshot,
            safe_aggregate={
                "metrics_records": preflight.snapshot.sli_window.sample_count,
                "logs": len(preflight.snapshot.logs),
                "spans": len(preflight.snapshot.traces),
            },
        )
        baseline_restored = current.document_sha256 == bundle.scenario.baseline_document_sha256
    finally:
        if environment._baseline_snapshot is not None:
            if not baseline_restored:
                try:
                    observed = controller.read_current()
                    if observed.document_sha256 != bundle.scenario.baseline_document_sha256:
                        observed = controller.restore_baseline()
                    baseline_restored = observed.document_sha256 == bundle.scenario.baseline_document_sha256
                except Exception:
                    baseline_restored = False
            try:
                cleanup = environment.cleanup(baseline_restored=baseline_restored)
            except Exception as error:
                raise ManualCleanupRequired(environment.manual_cleanup_command()) from error
            event.record(
                "SANDBOX_CLEANED",
                cleanup.verdict,
                output_value=cleanup,
                safe_aggregate=cleanup.model_dump(mode="json"),
            )
    if preflight is None or cleanup is None or cleanup.verdict != "CLEAN":
        raise RuntimeError("no-fault preflight did not close cleanly")
    lock = _tracked_lock(repository_root, bundle)
    lock.update(
        {
            "resolved_compose_sha256": resolved.compose_sha256,
            "private_image_lock_sha256": image_lock_sha256,
            "telemetry_endpoints": resolved.endpoints.model_dump(mode="json"),
            "sli_thresholds": bundle.verification.model_dump(mode="json"),
            "budget": bundle.budget.model_dump(mode="json"),
            "preflight_snapshot_sha256": canonical_sha256(preflight.snapshot),
            "preflight_cleanup": cleanup.model_dump(mode="json"),
        }
    )
    scenario_lock_sha256 = write_private_json(
        roots.control / "scenario-lock.json", lock, create_once=True
    )
    event.record(
        "SCENARIO_FROZEN",
        "LOCKED",
        output_value=lock,
        safe_aggregate={"implementation_commit": lock["implementation_commit"]},
    )
    template = LiveRemediationPlan.template_payload(bundle)
    template_sha256 = write_private_json(
        roots.control / "plan-template.json", template, create_once=True
    )
    now = datetime.now(timezone.utc)
    request_id = "approval-" + hashlib.sha256(
        f"{scenario_lock_sha256}:{template_sha256}".encode("utf-8")
    ).hexdigest()[:16]
    request = ApprovalRequest(
        approval_request_id=request_id,
        scenario_id=bundle.scenario.scenario_id,
        scenario_lock_sha256=scenario_lock_sha256,
        plan_template_sha256=template_sha256,
        environment_id=bundle.environment.environment_id,
        sandbox_id=bundle.environment.sandbox_id,
        action=bundle.policy.action,
        target_service=bundle.scenario.target_service,
        configuration_key=bundle.scenario.target_configuration_key,
        baseline_sha256=bundle.scenario.baseline_document_sha256,
        max_forward_mutations=1,
        requested_at=now,
        expires_at=now + timedelta(hours=bundle.policy.approval_ttl_hours),
    )
    request_sha256 = write_private_json(
        roots.control / "approval-request.json", request, create_once=True
    )
    event.record(
        "APPROVAL_REQUESTED",
        "HUMAN_REQUIRED",
        output_value=request,
        safe_aggregate={"expires_at": request.expires_at.isoformat()},
    )
    result = {
        "schema_version": "live-sandbox.invocation-a-result.v1",
        "verdict": "SANDBOX_REMEDIATION_HUMAN_APPROVAL_REQUIRED",
        "docker": docker,
        "scenario_id": bundle.scenario.scenario_id,
        "sandbox_id": bundle.environment.sandbox_id,
        "implementation_commit": lock["implementation_commit"],
        "resolved_compose_sha256": resolved.compose_sha256,
        "scenario_lock_sha256": scenario_lock_sha256,
        "approval_request_sha256": request_sha256,
        "plan_template_sha256": template_sha256,
        "approval_expires_at": request.expires_at,
        "preflight": {
            "metrics_records": preflight.snapshot.sli_window.sample_count,
            "logs": len(preflight.snapshot.logs),
            "spans": len(preflight.snapshot.traces),
            "cleanup": cleanup.model_dump(mode="json"),
        },
        "codex_self_approved": False,
        "fault_injected": False,
        "evidence_boundary": EVIDENCE_BOUNDARY,
    }
    write_private_json(roots.report / "invocation-a-result.json", result, create_once=True)
    return result


def _read_model(path: Path, model_type: type[HumanApprovalRecord] | type[ApprovalRequest]):
    if path.is_symlink() or not path.is_file():
        raise RuntimeError(f"required private record is unavailable: {path.name}")
    return model_type.model_validate_json(path.read_text(encoding="utf-8"))


def run_invocation_b(
    repository_root: Path,
    roots: PrivateRoots,
    *,
    provider_environment: Mapping[str, str] | None = None,
) -> dict[str, object]:
    """Execute the one approved positive run; callers must never retry it."""

    repository_root = repository_root.resolve()
    roots.prepare()
    bundle = load_bundle(repository_root / CONFIG_RELATIVE)
    verify_scenario_lock(repository_root, roots.control, bundle)
    request = _read_model(roots.control / "approval-request.json", ApprovalRequest)
    approval = _read_model(roots.control / "human-approval.json", HumanApprovalRecord)
    assert isinstance(request, ApprovalRequest)
    assert isinstance(approval, HumanApprovalRecord)
    if file_sha256(roots.control / "scenario-lock.json") != request.scenario_lock_sha256:
        raise RuntimeError("approval request does not bind the frozen scenario lock")
    if datetime.now(timezone.utc) > approval.expires_at:
        raise RuntimeError("human approval expired before Invocation B")
    event = EventRecorder(roots.report / "invocation-b-events.jsonl")
    event.record(
        "HUMAN_APPROVED",
        "VERIFIED_RECORD_PRESENT",
        input_value=request,
        output_value=approval,
        safe_aggregate={"mode": approval.mode, "approver_present": True},
    )
    provider = make_provider(bundle, provider_environment)
    synthetic_now = datetime.now(timezone.utc)
    synthetic_window = SLIWindow(
        phase="FAULT",
        started_at=synthetic_now - timedelta(seconds=30),
        ended_at=synthetic_now,
        request_count=10.0,
        error_count=8.0,
        error_rate=0.8,
        p95_latency_ms=10.0,
        runtime_health=1.0,
        sample_count=6,
    )
    synthetic_log = LogEvidence(
        observed_at=synthetic_now,
        service_name="payment",
        severity="ERROR",
        body="Synthetic typed preflight request error.",
    )
    synthetic_trace = TraceEvidence(
        trace_id="a" * 32,
        span_id="b" * 16,
        service_name="payment",
        span_name="synthetic preflight",
        started_at=synthetic_now,
        duration_ms=1.0,
        status="ERROR",
    )
    synthetic_context = build_a0_context(
        alert_title="Synthetic typed provider preflight",
        baseline_windows=(
            synthetic_window.model_copy(
                update={"phase": "BASELINE", "error_count": 0.0, "error_rate": 0.0}
            ),
        )
        * 2,
        fault_windows=(synthetic_window,) * 2,
        log_evidence=(synthetic_log,),
        trace_evidence=(synthetic_trace,),
    )
    provider_preflight = run_a0_diagnosis(synthetic_context, provider)
    if not provider.usage_known or provider_preflight.terminal != "COMPLETED":
        raise RuntimeError("Provider preflight did not return typed known-usage output")
    write_private_json(
        roots.report / "provider-preflight.json", provider_preflight, create_once=True
    )
    time.sleep(bundle.budget.minimum_request_spacing_seconds)
    environment = _environment(repository_root, roots, bundle)
    docker = environment.verify_local_docker()
    environment.verify_upstream()
    resolved, raw_compose = environment.resolve()
    if file_sha256(roots.control / "resolved-compose.json") != hashlib.sha256(
        canonical_json_bytes(raw_compose)
    ).hexdigest():
        raise RuntimeError("resolved Compose differs from Invocation A")
    controller = _controller(repository_root, roots, bundle, resolved.endpoints)
    cleanup = None
    rollback = None
    terminal = "BLOCKED"
    try:
        environment.start()
        event.record("SANDBOX_STARTED", "POSITIVE_RUN", output_value=resolved)
        health = environment.wait_healthy()
        time.sleep(bundle.verification.minimum_stabilization_seconds)
        telemetry = LiveTelemetryAdapter(endpoints=resolved.endpoints, bundle=bundle)
        baseline = tuple(
            telemetry.capture(
                phase="BASELINE",
                duration_seconds=bundle.scenario.verification_window_seconds,
                service_health=health,
            )
            for _ in range(2)
        )
        current = controller.read_current()
        event.record("BASELINE_VERIFIED", "EXACT", output_value=current)
        fault_state = controller.inject_fault()
        event.record("FAULT_INJECTED", "EXACT_FROZEN_FAULT", output_value=fault_state)
        fault = tuple(
            telemetry.capture(
                phase="FAULT",
                duration_seconds=bundle.scenario.fault_observation_window_seconds,
                service_health=environment.service_health(),
            )
            for _ in range(2)
        )
        baseline_windows = tuple(item.snapshot.sli_window for item in baseline)
        fault_windows = tuple(item.snapshot.sli_window for item in fault)
        if not fault_impact_passed(baseline_windows, fault_windows, bundle):
            terminal = "BLOCKED_FAULT_IMPACT_NOT_OBSERVED"
            controller.restore_baseline()
            return {"verdict": terminal}
        event.record(
            "FAULT_IMPACT_VERIFIED",
            "PASSED",
            output_value=fault_windows,
            safe_aggregate={"windows": 2},
        )
        context = build_a0_context(
            alert_title="Observed payment request error rate increase",
            baseline_windows=baseline_windows,
            fault_windows=fault_windows,
            log_evidence=tuple(item for capture in fault for item in capture.snapshot.logs),
            trace_evidence=tuple(item for capture in fault for item in capture.snapshot.traces),
        )
        diagnosis = run_a0_diagnosis(context, provider)
        event.record("DIAGNOSIS_COMPLETED", diagnosis.terminal, output_value=diagnosis)
        gate = evaluate_diagnosis_gate(diagnosis, bundle)
        if not gate.passed:
            terminal = "LIVE_DIAGNOSIS_GATE_NOT_PASSED_NO_REMEDIATION"
            controller.restore_baseline()
            return {"verdict": terminal, "diagnosis": diagnosis.model_dump(mode="json")}
        plan = build_plan(diagnosis, bundle)
        policy = evaluate_policy(
            plan=plan,
            diagnosis=diagnosis,
            request=request,
            approval=approval,
            bundle=bundle,
            docker_endpoint=str(docker["endpoint"]),
            owned_labels={
                "com.docker.compose.project": bundle.environment.compose_project,
                bundle.environment.sandbox_label_key: bundle.environment.sandbox_id,
            },
            forward_mutations=0,
            now=datetime.now(timezone.utc),
        )
        if policy.verdict is not PolicyVerdict.ALLOW:
            terminal = "BLOCKED_POLICY_REJECTED"
            controller.restore_baseline()
            return {"verdict": terminal, "reasons": policy.reason_codes}
        event.record("PLAN_ADMITTED", "POLICY_ALLOW", input_value=plan, output_value=policy)
        counter = ForwardMutationCounter(roots.control / "forward-mutation.jsonl")
        receipt = LocalSandboxRestrictedExecutor().execute(
            plan=plan,
            policy=policy,
            controller=controller,
            mutation_counter=counter,
        )
        event.record("REMEDIATION_EXECUTED", "ONE_FORWARD_MUTATION", output_value=receipt)
        recovery = tuple(
            telemetry.capture(
                phase="RECOVERY",
                duration_seconds=bundle.scenario.verification_window_seconds,
                service_health=environment.service_health(),
            )
            for _ in range(2)
        )
        verification = IndependentVerifier().verify(
            plan=plan,
            receipt=receipt,
            current=controller.read_current(),
            baseline_windows=baseline_windows,
            recovery_windows=tuple(item.snapshot.sli_window for item in recovery),
            services_healthy=all(environment.service_health().values()),
            labels_exact=True,
            bundle=bundle,
        )
        if verification.passed:
            terminal = "LIVE_TELEMETRY_CONTROLLED_REMEDIATION_DEMO_PASSED_READY_FOR_REVIEW"
            event.record("REMEDIATION_VERIFIED", "PASSED", output_value=verification)
        else:
            rollback = compensate_rollback(
                receipt=receipt, verification=verification, controller=controller
            )
            event.record("ROLLBACK_COMPLETED", "EXACT", output_value=rollback)
            controller.restore_baseline()
            terminal = "CONTROLLED_REMEDIATION_NOT_VERIFIED_ROLLBACK_COMPLETED"
        result: dict[str, object] = {
            "schema_version": "live-sandbox.invocation-b-result.v1",
            "verdict": terminal,
            "provider_preflight": provider_preflight.model_dump(mode="json"),
            "baseline_windows": [item.model_dump(mode="json") for item in baseline_windows],
            "fault_windows": [item.model_dump(mode="json") for item in fault_windows],
            "diagnosis": diagnosis.model_dump(mode="json"),
            "plan": plan.model_dump(mode="json"),
            "policy": policy.model_dump(mode="json"),
            "receipt": receipt.model_dump(mode="json"),
            "verification": verification.model_dump(mode="json"),
            "rollback": None if rollback is None else rollback.model_dump(mode="json"),
            "evidence_boundary": EVIDENCE_BOUNDARY,
        }
        write_private_json(roots.report / "invocation-b-result.json", result, create_once=True)
        return result
    finally:
        if environment._baseline_snapshot is not None:
            restored = False
            try:
                observed = controller.read_current()
                if observed.document_sha256 != bundle.scenario.baseline_document_sha256:
                    observed = controller.restore_baseline()
                restored = observed.document_sha256 == bundle.scenario.baseline_document_sha256
            except Exception:
                restored = False
            try:
                cleanup = environment.cleanup(baseline_restored=restored)
            except Exception as error:
                raise ManualCleanupRequired(environment.manual_cleanup_command()) from error
            event.record("SANDBOX_CLEANED", cleanup.verdict, output_value=cleanup)
            write_private_json(
                roots.report / "invocation-b-cleanup.json", cleanup, create_once=True
            )


__all__ = [
    "EVIDENCE_BOUNDARY",
    "ManualCleanupRequired",
    "PrivateRoots",
    "make_provider",
    "run_a0_diagnosis",
    "run_invocation_a",
    "run_invocation_b",
    "verify_scenario_lock",
]
