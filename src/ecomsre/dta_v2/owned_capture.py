"""Owned local Sandbox implementation for the PR-E capture-only campaign."""

from __future__ import annotations

import argparse
from collections.abc import Mapping
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import http.client
import json
from pathlib import Path
import secrets
import socket
import time
from typing import Any

from ecomsre.dta_v2.capture_campaign import (
    CaptureCampaignClosure,
    CaptureCasePlan,
    CaptureCondition,
    CaptureLifecycle,
    CaptureTerminal,
    EmailMemoryObservation,
    OperationalFamily,
    build_default_capture_plan,
    run_capture_campaign_attempt,
)
from ecomsre.dta_v2.contracts import (
    ActionDisposition,
    EvidenceSource,
    FaultDomain,
    FaultMechanism,
    RunbookId,
    Terminal,
    semantic_sha256,
)
from ecomsre.dta_v2.evaluation_contracts import (
    AgentVisibleReplayCase,
    EvaluatorCaseTruth,
    ReplayObservationFixture,
    ScenarioFamily,
)
from ecomsre.dta_v2.read_tools import ReadBackendFailure
from ecomsre.dta_v2.telemetry_adapters import (
    LocalSandboxReadBackend,
    UrllibLoopbackJsonTransport,
    _issue_owned_read_capability,
)
from ecomsre.dta_v2.tool_contracts import (
    MetricKind,
    ReadToolRequest,
    ResourceUsageRecord,
    ToolErrorCode,
    build_inspect_resource_usage_request,
    build_inspect_service_runtime_request,
    build_query_metrics_request,
    build_search_logs_request,
    build_trace_neighborhood_request,
)
from ecomsre_live_sandbox.contracts import (
    ConfigBundle,
    LocalEndpoints,
    ensure_private_directory,
    load_bundle,
    write_private_json,
)
from ecomsre_live_sandbox.control import (
    _restore_private_flag_mode,
    build_flag_documents,
)
from ecomsre_live_sandbox.environment import SandboxEnvironment


_PAYMENT_VARIANTS = {"off", "10%", "25%", "50%", "75%", "90%", "100%"}
_EMAIL_VARIANTS = {"off", "10x", "100x", "1000x"}
_LOAD_VARIANTS = {"5", "10", "25", "50"}


def build_capture_flag_document(
    upstream: Mapping[str, object],
    *,
    load_vus: int,
    payment_variant: str = "off",
    email_variant: str = "off",
) -> dict[str, object]:
    """Change only the three exact upstream flags used by capture."""

    if (
        str(load_vus) not in _LOAD_VARIANTS
        or payment_variant not in _PAYMENT_VARIANTS
        or email_variant not in _EMAIL_VARIANTS
    ):
        raise ValueError("capture flag variant is outside the frozen upstream set")
    document = deepcopy(dict(upstream))
    flags = document.get("flags")
    if not isinstance(flags, dict):
        raise ValueError("upstream flag document lacks flags")
    for name, variant in (
        ("loadGeneratorVUs", str(load_vus)),
        ("paymentFailure", payment_variant),
        ("emailMemoryLeak", email_variant),
    ):
        flag = flags.get(name)
        if not isinstance(flag, dict) or not isinstance(flag.get("variants"), dict):
            raise ValueError("capture flag is unavailable upstream")
        if variant not in flag["variants"]:
            raise ValueError("capture flag variant is unavailable upstream")
        flag["defaultVariant"] = variant
    return document


class ExactFlagDocumentController:
    def __init__(
        self,
        *,
        endpoints: LocalEndpoints,
        flag_file: Path,
        upstream: Mapping[str, object],
        timeout_seconds: float = 10.0,
    ) -> None:
        self.endpoints = endpoints
        self.flag_file = flag_file
        self.upstream = deepcopy(dict(upstream))
        self.http = UrllibLoopbackJsonTransport(timeout_seconds=timeout_seconds)

    def apply(self, document: Mapping[str, object]) -> str:
        expected = deepcopy(dict(document))
        self._require_allowed_document(expected)
        self.http.request_json(
            base_url=self.endpoints.flag_control,
            path="/write",
            method="POST",
            payload={"data": expected},
        )
        _restore_private_flag_mode(self.flag_file)
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            try:
                return self.verify(expected)
            except (OSError, RuntimeError, ValueError):
                time.sleep(0.25)
        raise RuntimeError("capture flag document did not reach exact readback")

    def verify(self, expected: Mapping[str, object]) -> str:
        expected_document = deepcopy(dict(expected))
        self._require_allowed_document(expected_document)
        if self.flag_file.is_symlink() or not self.flag_file.is_file():
            raise RuntimeError("capture flag file is unavailable")
        file_value = json.loads(self.flag_file.read_text(encoding="utf-8"))
        readback = self.http.request_json(
            base_url=self.endpoints.flag_control,
            path="/read",
            method="GET",
            payload=None,
        )
        if (
            file_value != expected_document
            or not isinstance(readback, Mapping)
            or readback.get("flags") != expected_document.get("flags")
        ):
            raise RuntimeError("capture flag readback differs")
        return semantic_sha256(expected_document)

    def _require_allowed_document(self, value: Mapping[str, object]) -> None:
        flags = value.get("flags")
        if not isinstance(flags, Mapping):
            raise ValueError("capture flag document is malformed")
        try:
            load = str(cast_mapping(flags["loadGeneratorVUs"])["defaultVariant"])
            payment = str(cast_mapping(flags["paymentFailure"])["defaultVariant"])
            email = str(cast_mapping(flags["emailMemoryLeak"])["defaultVariant"])
        except KeyError as error:
            raise ValueError("capture flag document lacks required flags") from error
        rebuilt = build_capture_flag_document(
            self.upstream,
            load_vus=int(load),
            payment_variant=payment,
            email_variant=email,
        )
        if rebuilt != value:
            raise ValueError("capture flag document changes an unauthorized field")


def cast_mapping(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError("capture flag entry is not an object")
    return value


class _UnixSocketDockerMutationClient:
    """Exact POST-only client for one owned service stop/start lifecycle."""

    def __init__(self, socket_path: str, *, timeout_seconds: float) -> None:
        if not socket_path.startswith("/") or "\x00" in socket_path:
            raise ValueError("Docker mutation socket is invalid")
        self.socket_path = socket_path
        self.timeout_seconds = timeout_seconds

    def post(self, path: str) -> None:
        if (
            not path.startswith("/containers/")
            or not (path.endswith("/start") or "/stop?t=" in path)
            or ".." in path
            or "\x00" in path
        ):
            raise ValueError("Docker mutation path is outside exact start/stop")
        connection = _UnixSocketHTTPConnection(
            self.socket_path, timeout=self.timeout_seconds
        )
        try:
            connection.request("POST", path, headers={"Accept": "application/json"})
            response = connection.getresponse()
            body = response.read(1_000_001)
            if len(body) > 1_000_000 or response.status not in {204, 304}:
                raise RuntimeError(
                    f"Docker exact mutation returned HTTP {response.status}"
                )
        finally:
            connection.close()


class _UnixSocketHTTPConnection(http.client.HTTPConnection):
    def __init__(self, socket_path: str, *, timeout: float) -> None:
        super().__init__("localhost", timeout=timeout)
        self.socket_path = socket_path

    def connect(self) -> None:
        connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        connection.settimeout(self.timeout)
        connection.connect(self.socket_path)
        self.sock = connection


class OwnedRecommendationController:
    def __init__(self, backend: LocalSandboxReadBackend) -> None:
        self.backend = backend
        self.client = _UnixSocketDockerMutationClient(
            backend.config.docker_endpoint.removeprefix("unix://"),
            timeout_seconds=backend.config.timeout_seconds,
        )

    def stop(self) -> None:
        identity = self._identity()
        self.client.post(f"/containers/{identity}/stop?t=15")
        self._wait_for(running=False)

    def start(self) -> None:
        identity = self._identity()
        self.client.post(f"/containers/{identity}/start")
        self._wait_for(running=True)

    def ensure_running(self) -> None:
        record = self.backend.docker._runtime_for("recommendation")
        if record.state.value != "RUNNING":
            self.start()

    def _identity(self) -> str:
        identity = self.backend.docker._owned_container_identity("recommendation")
        if identity is None:
            raise RuntimeError("owned recommendation container is absent")
        if any(item not in "0123456789abcdef" for item in identity.casefold()):
            raise RuntimeError("owned recommendation identity is invalid")
        return identity

    def _wait_for(self, *, running: bool) -> None:
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            record = self.backend.docker._runtime_for("recommendation")
            if (record.state.value == "RUNNING") is running:
                if not running or record.health.value in {
                    "HEALTHY",
                    "NOT_CONFIGURED",
                }:
                    return
            time.sleep(1)
        raise RuntimeError("owned recommendation state transition timed out")


class OwnedCaptureLifecycle(CaptureLifecycle):
    """One owned Sandbox, one active capture condition, exact reset and cleanup."""

    def __init__(
        self,
        *,
        repository_root: Path,
        private_root: Path,
        plan: object,
        stabilization_seconds: int = 30,
    ) -> None:
        self.repository_root = Path(repository_root).resolve()
        self.private_root = Path(private_root).resolve()
        self.plan = plan
        self.stabilization_seconds = stabilization_seconds
        self.bundle: ConfigBundle | None = None
        self.environment: SandboxEnvironment | None = None
        self.backend: LocalSandboxReadBackend | None = None
        self.flag_controller: ExactFlagDocumentController | None = None
        self.recommendation: OwnedRecommendationController | None = None
        self.upstream_flag: dict[str, object] | None = None
        self.baseline_document: dict[str, object] | None = None
        self.flag_file: Path | None = None
        self.admitted_resolved_sha256: str | None = None
        self.active_condition: str | None = None

    def admit(self) -> None:
        if self.repository_root == Path("/") or self.private_root == Path("/"):
            raise ValueError("capture roots may not be filesystem root")
        runtime_root = self.private_root / "runtime"
        control_root = self.private_root / "control"
        cases_root = self.private_root / "cases"
        for directory in (runtime_root, control_root, cases_root):
            ensure_private_directory(directory)
        write_private_json(
            control_root / "capture-plan.json", self.plan, create_once=True
        )
        bundle = load_bundle(
            self.repository_root / "config/live-telemetry-controlled-remediation-v1"
        )
        upstream_raw = json.loads(
            (
                self.repository_root
                / "third_party/opentelemetry-demo/src/flagd/demo.flagd.json"
            ).read_text(encoding="utf-8")
        )
        if not isinstance(upstream_raw, Mapping):
            raise ValueError("upstream flag document is invalid")
        upstream = deepcopy(dict(upstream_raw))
        baseline, _ = build_flag_documents(upstream, bundle)
        expected = build_capture_flag_document(upstream, load_vus=25)
        if baseline != expected:
            raise ValueError("capture baseline differs from frozen live baseline")
        flag_directory = runtime_root / "flagd"
        ensure_private_directory(flag_directory)
        flag_file = flag_directory / "demo.flagd.json"
        write_private_json(flag_file, baseline, create_once=True)
        environment = SandboxEnvironment(
            repository_root=self.repository_root,
            bundle=bundle,
            flagd_directory=flag_directory,
        )
        environment.verify_local_docker()
        environment.verify_upstream()
        resolved, raw_compose = environment.resolve()
        admitted = semantic_sha256(resolved.model_dump(mode="json"))
        write_private_json(
            control_root / "resolved-compose.json", raw_compose, create_once=True
        )
        environment.verify_cached_images(resolved, control_root)
        self.bundle = bundle
        self.environment = environment
        self.upstream_flag = upstream
        self.baseline_document = baseline
        self.flag_file = flag_file
        self.admitted_resolved_sha256 = admitted

    def start(self) -> None:
        self._environment().start()

    def wait_ready(self) -> None:
        environment = self._environment()
        environment.wait_healthy()
        if self.stabilization_seconds:
            time.sleep(self.stabilization_seconds)
        capability = _issue_owned_read_capability(
            environment=environment,
            bundle=self._bundle(),
            admitted_resolved_sha256=self._admitted_sha(),
            timeout_seconds=10.0,
        )
        backend = LocalSandboxReadBackend._from_owned_capability(capability)
        self.backend = backend
        self.flag_controller = ExactFlagDocumentController(
            endpoints=capability.resolved_sandbox.endpoints,
            flag_file=self._flag_file(),
            upstream=self._upstream(),
        )
        self.recommendation = OwnedRecommendationController(backend)

    def observe_baseline_memory(self) -> EmailMemoryObservation:
        return self._observe_email_memory(window_seconds=10, sample_count=3)

    def apply_email_calibration(self, variant: str) -> None:
        self._require_idle()
        document = build_capture_flag_document(
            self._upstream(), load_vus=25, email_variant=variant
        )
        self._flags().apply(document)
        self.active_condition = f"calibration:{variant}"

    def observe_email_calibration(self, variant: str) -> EmailMemoryObservation:
        if self.active_condition != f"calibration:{variant}":
            raise RuntimeError("Email calibration condition differs")
        time.sleep(15)
        return self._observe_email_memory(window_seconds=10, sample_count=3)

    def apply_case(
        self, case: CaptureCasePlan, *, selected_email_variant: str
    ) -> None:
        self._require_idle()
        payment_variant = "off"
        email_variant = "off"
        if case.condition is CaptureCondition.PAYMENT_FLAG:
            payment_variant = case.fault_variant
        elif case.condition is CaptureCondition.EMAIL_LEAK:
            email_variant = (
                selected_email_variant
                if case.fault_variant == "SELECTED"
                else case.fault_variant
            )
        document = build_capture_flag_document(
            self._upstream(),
            load_vus=case.load_vus,
            payment_variant=payment_variant,
            email_variant=email_variant,
        )
        self._flags().apply(document)
        if case.condition is CaptureCondition.RECOMMENDATION_STOP:
            self._recommendation().stop()
        elif case.condition is CaptureCondition.RECOVERY_TRANSITION:
            self._recommendation().stop()
            time.sleep(min(10, case.observation_window_seconds // 2))
            self._recommendation().start()
        self.active_condition = case.case_id
        time.sleep(case.observation_window_seconds)

    def capture_case(self, case: CaptureCasePlan) -> str:
        if self.active_condition != case.case_id:
            raise RuntimeError("capture case condition is not active")
        service = {
            "dta-dev-001": "payment",
            "dta-dev-002": "recommendation",
            "dta-dev-003": "email",
        }[case.scenario_id]
        ended_at = datetime.now(timezone.utc)
        started_at = ended_at - timedelta(
            seconds=case.observation_window_seconds
        )
        run_id = secrets.token_hex(16)
        requests: tuple[ReadToolRequest, ...] = (
            build_inspect_resource_usage_request(
                run_id=run_id,
                services=(service,),
                sampling_window_seconds=5,
                sample_count=3,
            ),
            build_inspect_service_runtime_request(
                run_id=run_id, services=(service,), max_results=1
            ),
            build_query_metrics_request(
                run_id=run_id,
                service=service,
                started_at=started_at,
                ended_at=ended_at,
                metric_kinds=(
                    MetricKind.CPU_PERCENT,
                    MetricKind.ERROR_RATE,
                    MetricKind.LATENCY_P95_MS,
                    MetricKind.MEMORY_BYTES,
                    MetricKind.REQUEST_SUPPORT,
                ),
                max_results=5,
            ),
            build_trace_neighborhood_request(
                run_id=run_id,
                service=service,
                started_at=started_at,
                ended_at=ended_at,
                max_spans=40,
            ),
            build_search_logs_request(
                run_id=run_id,
                service=service,
                started_at=started_at,
                ended_at=ended_at,
                max_records=20,
            ),
        )
        fixtures = tuple(
            sorted(
                (self._capture_fixture(request) for request in requests),
                key=lambda item: item.tool.value,
            )
        )
        payload: dict[str, Any] = {
            "schema_version": "dta-v2.agent-visible-replay-case.v1",
            "case_id": case.case_id,
            "scenario_id": case.scenario_id,
            "captured_started_at": started_at,
            "captured_ended_at": ended_at,
            "observations": fixtures,
            "full_context_tools": case.full_context_tools,
        }
        draft = AgentVisibleReplayCase.model_construct(
            **payload, case_sha256="0" * 64
        )
        replay_case = AgentVisibleReplayCase.model_validate(
            {
                **payload,
                "case_sha256": semantic_sha256(
                    draft.model_dump(mode="json", exclude={"case_sha256"})
                ),
            }
        )
        truth = build_evaluator_truth(case)
        case_root = self.private_root / "cases" / case.case_id
        write_private_json(
            case_root / "agent-visible.json", replay_case, create_once=True
        )
        write_private_json(
            case_root / "evaluator-truth.json", truth, create_once=True
        )
        return replay_case.case_sha256

    def restore_baseline(self) -> None:
        if self.active_condition is None:
            raise RuntimeError("capture reset lacks an active condition")
        try:
            self._recommendation().ensure_running()
            self._flags().apply(self._baseline())
        except Exception:
            raise
        else:
            self.active_condition = None

    def verify_baseline(self) -> None:
        self._flags().verify(self._baseline())
        self._recommendation().ensure_running()
        health = self._environment().wait_healthy(timeout_seconds=120)
        if not all(health.values()):
            raise RuntimeError("capture baseline health is incomplete")
        self._environment().verify_owned_resources(require_complete=True)

    def cleanup(self, *, baseline_restored: bool) -> dict[str, object]:
        result = self._environment().cleanup(baseline_restored=baseline_restored)
        return result.model_dump(mode="json")

    def _capture_fixture(self, request: ReadToolRequest) -> ReplayObservationFixture:
        error: ToolErrorCode | None = None
        records: tuple[Any, ...] = ()
        truncated = False
        try:
            result = self._backend().execute(request)
            records = result.records
            truncated = result.truncated
        except ReadBackendFailure as failure:
            error = failure.error_code
        except TimeoutError:
            error = ToolErrorCode.SOURCE_TIMEOUT
        except (TypeError, ValueError):
            error = ToolErrorCode.SOURCE_SCHEMA_INVALID
        except Exception:
            # Mirror the public dispatcher boundary: dynamic backend exception
            # text and resource identities are never retained in replay data.
            error = ToolErrorCode.INTERNAL_CONTRACT_VIOLATION
        service_scope = (
            (request.service,)
            if hasattr(request, "service")
            else tuple(request.services)
        )
        payload: dict[str, Any] = {
            "schema_version": "dta-v2.replay-observation-fixture.v1",
            "tool": request.tool,
            "service_scope": service_scope,
            "records": records,
            "truncated": truncated,
            "error_code": error,
        }
        draft = ReplayObservationFixture.model_construct(
            **payload, fixture_sha256="0" * 64
        )
        return ReplayObservationFixture.model_validate(
            {
                **payload,
                "fixture_sha256": semantic_sha256(
                    draft.model_dump(mode="json", exclude={"fixture_sha256"})
                ),
            }
        )

    def _observe_email_memory(
        self, *, window_seconds: int, sample_count: int
    ) -> EmailMemoryObservation:
        request = build_inspect_resource_usage_request(
            run_id=secrets.token_hex(16),
            services=("email",),
            sampling_window_seconds=window_seconds,
            sample_count=sample_count,
        )
        result = self._backend().execute(request)
        if len(result.records) != 1 or type(result.records[0]) is not ResourceUsageRecord:
            raise RuntimeError("Email calibration resource observation is invalid")
        record = result.records[0]
        memories = tuple(item.memory_bytes for item in record.samples)
        return EmailMemoryObservation(
            maximum_memory_bytes=max(memories),
            memory_delta_bytes=max(memories) - min(memories),
            maximum_slope_bytes_per_second=record.memory_slope_bytes_per_second,
        )

    def _require_idle(self) -> None:
        if self.active_condition is not None:
            raise RuntimeError("capture already has an active condition")

    def _environment(self) -> SandboxEnvironment:
        if self.environment is None:
            raise RuntimeError("capture environment is unavailable")
        return self.environment

    def _bundle(self) -> ConfigBundle:
        if self.bundle is None:
            raise RuntimeError("capture bundle is unavailable")
        return self.bundle

    def _backend(self) -> LocalSandboxReadBackend:
        if self.backend is None:
            raise RuntimeError("capture read backend is unavailable")
        return self.backend

    def _flags(self) -> ExactFlagDocumentController:
        if self.flag_controller is None:
            raise RuntimeError("capture flag controller is unavailable")
        return self.flag_controller

    def _recommendation(self) -> OwnedRecommendationController:
        if self.recommendation is None:
            raise RuntimeError("capture recommendation controller is unavailable")
        return self.recommendation

    def _upstream(self) -> dict[str, object]:
        if self.upstream_flag is None:
            raise RuntimeError("capture upstream flags are unavailable")
        return self.upstream_flag

    def _baseline(self) -> dict[str, object]:
        if self.baseline_document is None:
            raise RuntimeError("capture baseline is unavailable")
        return self.baseline_document

    def _flag_file(self) -> Path:
        if self.flag_file is None:
            raise RuntimeError("capture flag file is unavailable")
        return self.flag_file

    def _admitted_sha(self) -> str:
        if self.admitted_resolved_sha256 is None:
            raise RuntimeError("capture admission resolve is unavailable")
        return self.admitted_resolved_sha256


def build_evaluator_truth(case: CaptureCasePlan) -> EvaluatorCaseTruth:
    if case.operational_family is OperationalFamily.PAYMENT:
        semantics: dict[str, object] = {
            "expected_terminal": Terminal.COMPLETED,
            "expected_root_service": "payment",
            "expected_fault_domain": FaultDomain.CONFIGURATION,
            "expected_mechanism": FaultMechanism.CONFIGURATION_ERROR,
            "expected_disposition": ActionDisposition.EXECUTE_RUNBOOK,
            "expected_runbook": RunbookId.ROLLBACK_CONFIGURATION,
            "expected_evidence_sources": (
                EvidenceSource.METRICS,
                EvidenceSource.TRACES,
            ),
        }
    elif case.operational_family is OperationalFamily.RECOMMENDATION:
        semantics = {
            "expected_terminal": Terminal.COMPLETED,
            "expected_root_service": "recommendation",
            "expected_fault_domain": FaultDomain.SERVICE_RUNTIME,
            "expected_mechanism": FaultMechanism.SERVICE_UNAVAILABLE,
            "expected_disposition": ActionDisposition.EXECUTE_RUNBOOK,
            "expected_runbook": RunbookId.RESTART_SERVICE,
            "expected_evidence_sources": (
                EvidenceSource.METRICS,
                EvidenceSource.RUNTIME,
            ),
        }
    elif case.operational_family is OperationalFamily.EMAIL:
        semantics = {
            "expected_terminal": Terminal.COMPLETED,
            "expected_root_service": "email",
            "expected_fault_domain": FaultDomain.LOCAL_RESOURCE,
            "expected_mechanism": FaultMechanism.MEMORY_LEAK,
            "expected_disposition": ActionDisposition.EXECUTE_RUNBOOK,
            "expected_runbook": RunbookId.MITIGATE_MEMORY_LEAK,
            "expected_evidence_sources": (
                EvidenceSource.METRICS,
                EvidenceSource.RUNTIME,
                EvidenceSource.RESOURCES,
            ),
        }
    else:
        terminal = (
            Terminal.NEED_MORE_EVIDENCE
            if case.evaluator_family is ScenarioFamily.CONFLICTING_EVIDENCE
            else Terminal.ABSTAIN
        )
        semantics = {
            "expected_terminal": terminal,
            "expected_root_service": None,
            "expected_fault_domain": None,
            "expected_mechanism": None,
            "expected_disposition": None,
            "expected_runbook": None,
            "expected_evidence_sources": (),
        }
    payload: dict[str, Any] = {
        "schema_version": "dta-v2.evaluator-case-truth.v1",
        "case_id": case.case_id,
        "split": case.split,
        "scenario_family": case.evaluator_family,
        "meaningful_observation_differences": (
            case.meaningful_observation_differences
        ),
        **semantics,
    }
    draft = EvaluatorCaseTruth.model_construct(
        **payload, truth_sha256="0" * 64
    )
    return EvaluatorCaseTruth.model_validate(
        {
            **payload,
            "truth_sha256": semantic_sha256(
                draft.model_dump(mode="json", exclude={"truth_sha256"})
            ),
        }
    )


def run_owned_capture_campaign(
    *,
    repository_root: Path,
    private_root: Path,
    base_head: str,
    stabilization_seconds: int = 30,
) -> CaptureCampaignClosure:
    plan = build_default_capture_plan(base_head=base_head)
    lifecycle = OwnedCaptureLifecycle(
        repository_root=repository_root,
        private_root=private_root,
        plan=plan,
        stabilization_seconds=stabilization_seconds,
    )
    closure = run_capture_campaign_attempt(plan=plan, lifecycle=lifecycle)
    write_private_json(
        Path(private_root) / "capture-campaign-closure.json",
        closure,
        create_once=True,
    )
    return closure


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Capture the frozen DTA v2 PR-E replay campaign."
    )
    parser.add_argument("--repository-root", type=Path, required=True)
    parser.add_argument("--private-root", type=Path, required=True)
    parser.add_argument("--base-head", required=True)
    parser.add_argument("--stabilization-seconds", type=int, default=30)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    closure = run_owned_capture_campaign(
        repository_root=args.repository_root,
        private_root=args.private_root,
        base_head=args.base_head,
        stabilization_seconds=args.stabilization_seconds,
    )
    print(
        json.dumps(
            {
                "terminal": closure.terminal.value,
                "failure_code": (
                    None
                    if closure.failure_code is None
                    else closure.failure_code.value
                ),
                "cleanup_failure_code": (
                    None
                    if closure.cleanup_failure_code is None
                    else closure.cleanup_failure_code.value
                ),
                "failure_operation": (
                    None
                    if closure.failure_operation is None
                    else closure.failure_operation.value
                ),
                "recovery_failure_operation": (
                    None
                    if closure.recovery_failure_operation is None
                    else closure.recovery_failure_operation.value
                ),
                "failed_case_id": closure.failed_case_id,
                "selected_email_variant": closure.selected_email_variant,
                "captured_case_count": len(closure.captured_case_sha256s),
                "cleanup_verdict": closure.cleanup_verdict,
                "closure_sha256": closure.closure_sha256,
            },
            sort_keys=True,
        )
    )
    return 0 if closure.terminal is CaptureTerminal.PASS else 2


__all__ = [
    "ExactFlagDocumentController",
    "OwnedCaptureLifecycle",
    "OwnedRecommendationController",
    "build_capture_flag_document",
    "build_evaluator_truth",
    "run_owned_capture_campaign",
]


if __name__ == "__main__":
    raise SystemExit(main())
