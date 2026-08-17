"""Exact owned-Sandbox capture lifecycle for the DTA v2.1 PR-D campaign."""

from __future__ import annotations

import argparse
from collections.abc import Mapping
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
import secrets
import statistics
import time
from typing import Any, cast

from ecomsre.dta_v2.owned_capture import (
    EMAIL_CAPTURE_MAXIMUM_MEMORY_BYTES,
    ExactFlagDocumentController,
    OwnedCaptureLifecycle,
    OwnedEmailController,
    _DockerMutationHTTPError,
    _UnixSocketDockerMutationClient,
)
from ecomsre.dta_v2.read_tools import ReadBackendFailure
from ecomsre.dta_v2.telemetry_adapters import LocalSandboxReadBackend
from ecomsre.dta_v2.tool_contracts import (
    HealthState,
    MetricKind,
    MetricRecord,
    ReadToolRequest,
    ResourceUsageRecord,
    RuntimeRecord,
    RuntimeState,
    SpanStatus,
    ToolErrorCode,
    ToolName,
    TraceNeighborhoodRecord,
    build_inspect_resource_usage_request,
    build_inspect_service_runtime_request,
    build_query_metrics_request,
    build_search_logs_request,
    build_trace_neighborhood_request,
)
from ecomsre.dta_v2.v21.capture_campaign import (
    CalibrationKindV21,
    CaptureCalibrationFailureV21,
    CaptureCalibrationObservationV21,
    CaptureCampaignClosureV21,
    CaptureCampaignPlanV21,
    CaptureCasePlanV21,
    CaptureConditionV21,
    CapturedCaseArtifactV21,
    OperationalFamilyV21,
    build_default_capture_plan_v21,
    run_capture_campaign_attempt_v21,
)
from ecomsre.dta_v2.v21.contracts import (
    ActionDispositionV21,
    EvidenceSourceV21,
    FaultDomainV21,
    FaultMechanismV21,
    RunbookIdV21,
    TerminalV21,
    semantic_sha256,
)
from ecomsre.dta_v2.v21.evaluation_contracts import (
    AgentVisibleReplayCaseV21,
    EvaluatorCaseTruthV21,
    GeneralizationSliceV21,
    ReplayObservationFixtureV21,
)
from ecomsre_live_sandbox.contracts import write_private_json


_PAYMENT_VARIANTS = {"off", "10%", "25%", "50%", "75%", "90%", "100%"}
_EMAIL_VARIANTS = {"off", "10x", "100x", "1000x"}
_LOAD_VARIANTS = {"5", "10", "25", "50"}
_AD_CPU_VARIANTS = {"off", "on"}
_SHIPPING_VARIANTS = {"off", "5sec", "10sec"}
_OWNED_STOP_SERVICES = {"email", "product-catalog", "recommendation"}


def build_capture_flag_document_v21(
    upstream: Mapping[str, object],
    *,
    load_vus: int,
    payment_variant: str = "off",
    email_variant: str = "off",
    ad_cpu_variant: str = "off",
    shipping_variant: str = "off",
) -> dict[str, object]:
    """Change only the five exact upstream flags admitted for PR-D capture."""

    variants = {
        "loadGeneratorVUs": (str(load_vus), _LOAD_VARIANTS),
        "paymentFailure": (payment_variant, _PAYMENT_VARIANTS),
        "emailMemoryLeak": (email_variant, _EMAIL_VARIANTS),
        "adHighCpu": (ad_cpu_variant, _AD_CPU_VARIANTS),
        "intlShippingSlowdown": (shipping_variant, _SHIPPING_VARIANTS),
    }
    document = deepcopy(dict(upstream))
    flags = document.get("flags")
    if not isinstance(flags, dict):
        raise ValueError("upstream flag document lacks flags")
    for name, (variant, allowlist) in variants.items():
        if variant not in allowlist:
            raise ValueError("capture flag variant is outside the frozen upstream set")
        flag = flags.get(name)
        if not isinstance(flag, dict) or not isinstance(flag.get("variants"), dict):
            raise ValueError("capture flag is unavailable upstream")
        if variant not in flag["variants"]:
            raise ValueError("capture flag variant is unavailable upstream")
        flag["defaultVariant"] = variant
    return document


class ExactFlagDocumentControllerV21(ExactFlagDocumentController):
    def _require_allowed_document(self, value: Mapping[str, object]) -> None:
        flags = value.get("flags")
        if not isinstance(flags, Mapping):
            raise ValueError("capture flag document is malformed")

        def variant(name: str) -> str:
            entry = flags.get(name)
            if not isinstance(entry, Mapping):
                raise ValueError("capture flag entry is unavailable")
            selected = entry.get("defaultVariant")
            if not isinstance(selected, str):
                raise ValueError("capture flag default variant is invalid")
            return selected

        rebuilt = build_capture_flag_document_v21(
            self.upstream,
            load_vus=int(variant("loadGeneratorVUs")),
            payment_variant=variant("paymentFailure"),
            email_variant=variant("emailMemoryLeak"),
            ad_cpu_variant=variant("adHighCpu"),
            shipping_variant=variant("intlShippingSlowdown"),
        )
        if rebuilt != value:
            raise ValueError("capture flag document changes an unauthorized field")


class ExactOwnedServiceControllerV21:
    """Stop/start one admitted owned container while retaining exact identity."""

    def __init__(self, backend: LocalSandboxReadBackend, service: str) -> None:
        if service not in _OWNED_STOP_SERVICES:
            raise ValueError("service is outside the PR-D stop allowlist")
        self.backend = backend
        self.service = service
        self.client = _UnixSocketDockerMutationClient(
            backend.config.docker_endpoint.removeprefix("unix://"),
            timeout_seconds=max(45.0, backend.config.timeout_seconds),
        )
        self.retained_identity: str | None = None

    def stop(self) -> None:
        identity = self._identity()
        self.retained_identity = identity
        self._post(f"/containers/{identity}/stop?t=15")
        self._wait_for(running=False)

    def start(self) -> None:
        identity = self.retained_identity or self._identity()
        current = self._identity()
        if current != identity:
            raise RuntimeError("owned service identity changed during capture")
        self._post(f"/containers/{identity}/start")
        self._wait_for(running=True)
        self.retained_identity = None

    def ensure_running(self) -> None:
        record = self.backend.docker._runtime_for(self.service)
        if record.state is not RuntimeState.RUNNING:
            self.start()

    def _identity(self) -> str:
        identity = self.backend.docker._owned_container_identity(self.service)
        if (
            identity is None
            or len(identity) != 64
            or any(character not in "0123456789abcdef" for character in identity)
        ):
            raise RuntimeError("owned service identity is invalid")
        return identity

    def _post(self, path: str) -> None:
        try:
            self.client.post(path)
        except _DockerMutationHTTPError as error:
            raise RuntimeError(
                f"owned service transition returned HTTP {error.status_code}"
            ) from None

    def _wait_for(self, *, running: bool) -> None:
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            record = self.backend.docker._runtime_for(self.service)
            if (record.state is RuntimeState.RUNNING) is running:
                if not running or record.health in {
                    HealthState.HEALTHY,
                    HealthState.NOT_CONFIGURED,
                }:
                    return
            time.sleep(1)
        raise RuntimeError("owned service transition timed out")


def _calibration_observation(**payload: object) -> CaptureCalibrationObservationV21:
    full = {
        "schema_version": "dta-v21.capture-calibration-observation.v1",
        "maximum_memory_bytes": None,
        "memory_delta_bytes": None,
        "memory_slope_bytes_per_second": None,
        "cpu_p50_percent": None,
        "cpu_p95_percent": None,
        "business_error_rate": None,
        "business_latency_p95_ms": None,
        "business_impact_observed": None,
        "attributable_trace_latency_ms": None,
        "target_runtime_stopped": None,
        **payload,
    }
    return CaptureCalibrationObservationV21.model_validate(
        {**full, "observation_sha256": semantic_sha256(full)}
    )


class OwnedCaptureLifecycleV21(OwnedCaptureLifecycle):
    """One owned Sandbox with exact per-calibration and per-case restoration."""

    def __init__(
        self,
        *,
        repository_root: Path,
        private_root: Path,
        plan: CaptureCampaignPlanV21,
        stabilization_seconds: int = 30,
    ) -> None:
        super().__init__(
            repository_root=repository_root,
            private_root=private_root,
            plan=plan,
            stabilization_seconds=stabilization_seconds,
        )
        self.plan_v21 = plan
        self.service_controllers: dict[str, ExactOwnedServiceControllerV21] = {}
        self.email_v21: OwnedEmailController | None = None
        self.case_started_at: datetime | None = None
        self.recovery_trace_fixture_v21: ReplayObservationFixtureV21 | None = None
        self.email_resource_fixture_v21: ReplayObservationFixtureV21 | None = None
        self.fault_operation_count = 0
        self.email_restart_required_v21 = False
        self.calibration_step_v21 = "BEGIN"

    def admit(self) -> None:
        super().admit()
        expected = build_capture_flag_document_v21(self._upstream(), load_vus=25)
        if self._baseline() != expected:
            raise ValueError("PR-D baseline differs from exact five-flag baseline")

    def wait_ready(self) -> None:
        super().wait_ready()
        backend = self._backend()
        previous_controller = self.flag_controller
        if previous_controller is None:
            raise RuntimeError("capture flag controller is unavailable")
        self.flag_controller = ExactFlagDocumentControllerV21(
            endpoints=previous_controller.endpoints,
            flag_file=self._flag_file(),
            upstream=self._upstream(),
        )
        self.service_controllers = {
            service: ExactOwnedServiceControllerV21(backend, service)
            for service in sorted(_OWNED_STOP_SERVICES)
        }
        self.email_v21 = OwnedEmailController(backend)

    def calibrate(
        self, *, kind: CalibrationKindV21, target_service: str, variant: str
    ) -> CaptureCalibrationObservationV21:
        self.calibration_step_v21 = "BEGIN"
        try:
            return self._calibrate_impl(
                kind=kind, target_service=target_service, variant=variant
            )
        except CaptureCalibrationFailureV21:
            raise
        except Exception as error:
            raise CaptureCalibrationFailureV21(
                kind=kind,
                target_service=target_service,
                variant=variant,
                step=self.calibration_step_v21,
                cause=error,
            ) from error

    def _calibrate_impl(
        self, *, kind: CalibrationKindV21, target_service: str, variant: str
    ) -> CaptureCalibrationObservationV21:
        self._require_idle()
        self.active_condition = f"calibration:{kind.value}:{target_service}:{variant}"
        now = datetime.now(timezone.utc)
        if kind is CalibrationKindV21.EMAIL_MEMORY:
            self.email_restart_required_v21 = True
            self.calibration_step_v21 = "FLAG_APPLY"
            self._flags().apply(
                build_capture_flag_document_v21(
                    self._upstream(), load_vus=25, email_variant=variant
                )
            )
            self.calibration_step_v21 = "SERVICE_RESTART"
            self._email_v21().restart()
            time.sleep(5)
            self.calibration_step_v21 = "RESOURCE_USAGE"
            resource = self._resource_record(
                service="email", window_seconds=10, sample_count=3
            )
            memories = tuple(item.memory_bytes for item in resource.samples)
            maximum = max(memories)
            delta = maximum - min(memories)
            slope = resource.memory_slope_bytes_per_second
            return _calibration_observation(
                kind=kind,
                target_service=target_service,
                variant=variant,
                maximum_memory_bytes=maximum,
                memory_delta_bytes=delta,
                memory_slope_bytes_per_second=slope,
                safe=(
                    maximum <= EMAIL_CAPTURE_MAXIMUM_MEMORY_BYTES
                    and delta <= 256_000_000
                    and slope <= 20_000_000.0
                ),
                measurable=delta >= 1_000_000,
            )
        if kind is CalibrationKindV21.AD_CPU:
            self.calibration_step_v21 = "FLAG_APPLY"
            self._flags().apply(
                build_capture_flag_document_v21(
                    self._upstream(), load_vus=25, ad_cpu_variant=variant
                )
            )
            time.sleep(10)
            self.calibration_step_v21 = "RESOURCE_USAGE"
            resource = self._resource_record(
                service="ad", window_seconds=10, sample_count=5
            )
            cpus = tuple(item.cpu_percent for item in resource.samples)
            self.calibration_step_v21 = "BUSINESS_METRIC"
            latency = self._metric_value(
                service="ad", kind=MetricKind.LATENCY_P95_MS, started_at=now
            )
            p50 = float(statistics.median(cpus))
            p95 = max(cpus)
            return _calibration_observation(
                kind=kind,
                target_service=target_service,
                variant=variant,
                cpu_p50_percent=p50,
                cpu_p95_percent=p95,
                business_latency_p95_ms=latency,
                safe=p95 <= 98.5,
                measurable=variant == "on" and p95 >= 60.0,
            )
        if kind is CalibrationKindV21.SHIPPING_LATENCY:
            self.calibration_step_v21 = "FLAG_APPLY"
            self._flags().apply(
                build_capture_flag_document_v21(
                    self._upstream(), load_vus=25, shipping_variant=variant
                )
            )
            time.sleep(15)
            ended = datetime.now(timezone.utc)
            self.calibration_step_v21 = "BUSINESS_METRIC"
            latency = self._metric_value(
                service="shipping",
                kind=MetricKind.LATENCY_P95_MS,
                started_at=now,
                ended_at=ended,
            )
            self.calibration_step_v21 = "TRACE_ATTRIBUTION"
            traces = self._trace_records(
                service="shipping", started_at=now, ended_at=ended
            )
            attributable = max((item.duration_ms for item in traces), default=0.0)
            return _calibration_observation(
                kind=kind,
                target_service=target_service,
                variant=variant,
                business_latency_p95_ms=latency,
                attributable_trace_latency_ms=attributable,
                safe=latency <= 15_000.0 and attributable <= 15_000.0,
                measurable=(
                    variant != "off" and latency >= 1_000.0 and attributable >= 1_000.0
                ),
            )
        if kind is not CalibrationKindV21.SERVICE_UNAVAILABLE:
            raise ValueError("capture calibration kind is unsupported")
        controller = self._service(target_service)
        self.calibration_step_v21 = "SERVICE_STOP"
        controller.stop()
        self.fault_operation_count = 1
        time.sleep(10)
        self.calibration_step_v21 = "RUNTIME_OBSERVATION"
        runtime = self._runtime_record(target_service)
        ended = datetime.now(timezone.utc)
        self.calibration_step_v21 = "TRACE_OBSERVATION"
        traces = self._trace_records(
            service=target_service, started_at=now, ended_at=ended
        )
        impact = any(
            item.status is SpanStatus.ERROR and item.first_error_location
            for item in traces
        )
        self.calibration_step_v21 = "BUSINESS_METRIC"
        error_rate = self._metric_value(
            service=target_service,
            kind=MetricKind.ERROR_RATE,
            started_at=now,
            ended_at=ended,
        )
        stopped = runtime.state is RuntimeState.EXITED
        return _calibration_observation(
            kind=kind,
            target_service=target_service,
            variant=variant,
            business_error_rate=min(1.0, max(0.0, error_rate)),
            business_impact_observed=impact or error_rate > 0.0,
            target_runtime_stopped=stopped,
            safe=True,
            measurable=stopped and (impact or error_rate > 0.0),
        )

    def apply_case(  # type: ignore[override]
        self,
        case: CaptureCasePlanV21,
        *,
        selected_email_variant: str,
        selected_shipping_variant: str,
    ) -> None:
        self._require_idle()
        self.active_condition = case.case_id
        self.case_started_at = datetime.now(timezone.utc)
        self.recovery_trace_fixture_v21 = None
        self.email_resource_fixture_v21 = None
        self.email_restart_required_v21 = False
        self.fault_operation_count = 0
        payment = "off"
        email = "off"
        ad_cpu = "off"
        shipping = "off"
        if case.condition is CaptureConditionV21.PAYMENT_FLAG:
            payment = case.fault_variant
        elif case.condition is CaptureConditionV21.EMAIL_MEMORY_LEAK:
            email = selected_email_variant
            self.email_restart_required_v21 = True
        elif case.condition is CaptureConditionV21.AD_HIGH_CPU:
            ad_cpu = "on"
        elif case.condition is CaptureConditionV21.SHIPPING_SLOWDOWN:
            shipping = selected_shipping_variant
        self._flags().apply(
            build_capture_flag_document_v21(
                self._upstream(),
                load_vus=case.load_vus,
                payment_variant=payment,
                email_variant=email,
                ad_cpu_variant=ad_cpu,
                shipping_variant=shipping,
            )
        )
        if case.condition in {
            CaptureConditionV21.PAYMENT_FLAG,
            CaptureConditionV21.EMAIL_MEMORY_LEAK,
            CaptureConditionV21.AD_HIGH_CPU,
            CaptureConditionV21.SHIPPING_SLOWDOWN,
        }:
            self.fault_operation_count = 1
        if case.condition is CaptureConditionV21.RECOMMENDATION_STOP:
            self._service("recommendation").stop()
            self.fault_operation_count = 1
        elif case.condition is CaptureConditionV21.EMAIL_STOP:
            self._service("email").stop()
            self.fault_operation_count = 1
        elif case.condition is CaptureConditionV21.PRODUCT_CATALOG_STOP:
            self._service("product-catalog").stop()
            self.fault_operation_count = 1
        elif case.condition is CaptureConditionV21.EMAIL_MEMORY_LEAK:
            self._email_v21().restart()
            self.email_resource_fixture_v21 = self._capture_fixture_v21(
                build_inspect_resource_usage_request(
                    run_id=secrets.token_hex(16),
                    services=("email",),
                    sampling_window_seconds=20,
                    sample_count=5,
                )
            )
            remaining = case.observation_window_seconds - 20
            if remaining > 0:
                time.sleep(remaining)
            return
        elif case.condition is CaptureConditionV21.RECOVERY_TRANSITION:
            self._service("email").stop()
            self.fault_operation_count = 1
            time.sleep(case.observation_window_seconds)
            ended = datetime.now(timezone.utc)
            self.recovery_trace_fixture_v21 = self._capture_fixture_v21(
                build_trace_neighborhood_request(
                    run_id=secrets.token_hex(16),
                    service="email",
                    started_at=self._case_start(),
                    ended_at=ended,
                    max_spans=40,
                )
            )
            self._service("email").start()
            time.sleep(15)
            return
        time.sleep(case.observation_window_seconds)

    def capture_case(  # type: ignore[override]
        self, case: CaptureCasePlanV21
    ) -> CapturedCaseArtifactV21:
        if self.active_condition != case.case_id:
            raise RuntimeError("capture case condition is not active")
        service = _service_for_family(case.operational_family)
        ended_at = datetime.now(timezone.utc)
        started_at = self._case_start()
        current_started_at = max(
            started_at, ended_at - timedelta(seconds=case.observation_window_seconds)
        )
        run_id = secrets.token_hex(16)
        resource_window = 20 if service == "email" else 5
        resource_samples = 5 if service == "email" else 3
        requests: tuple[ReadToolRequest, ...] = (
            build_inspect_resource_usage_request(
                run_id=run_id,
                services=(service,),
                sampling_window_seconds=resource_window,
                sample_count=resource_samples,
            ),
            build_inspect_service_runtime_request(
                run_id=run_id, services=(service,), max_results=1
            ),
            build_query_metrics_request(
                run_id=run_id,
                service=service,
                started_at=current_started_at,
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
                started_at=current_started_at,
                ended_at=ended_at,
                max_spans=40,
            ),
            build_search_logs_request(
                run_id=run_id,
                service=service,
                started_at=current_started_at,
                ended_at=ended_at,
                max_records=20,
            ),
        )
        captured: list[ReplayObservationFixtureV21] = []
        for request in requests:
            if (
                request.tool is ToolName.INSPECT_RESOURCE_USAGE
                and case.condition is CaptureConditionV21.EMAIL_MEMORY_LEAK
            ):
                if self.email_resource_fixture_v21 is None:
                    raise RuntimeError("Email memory fixture is unavailable")
                fixture = self.email_resource_fixture_v21
            elif (
                request.tool is ToolName.QUERY_TRACE_NEIGHBORHOOD
                and case.condition is CaptureConditionV21.RECOVERY_TRANSITION
            ):
                if self.recovery_trace_fixture_v21 is None:
                    raise RuntimeError("recovery trace fixture is unavailable")
                fixture = self.recovery_trace_fixture_v21
            elif (
                request.tool is ToolName.QUERY_TRACE_NEIGHBORHOOD
                and case.condition is CaptureConditionV21.SOURCE_PARTIAL_FAILURE
            ):
                fixture = _failed_fixture_v21(
                    tool=request.tool,
                    service_scope=(service,),
                    error_code=ToolErrorCode.SOURCE_UNAVAILABLE,
                )
            else:
                fixture = self._capture_fixture_v21(request)
            captured.append(fixture)
        fixtures = tuple(sorted(captured, key=lambda item: item.tool.value))
        require_capture_case_quality_v21(case, fixtures)
        case_payload: dict[str, object] = {
            "schema_version": "dta-v21.agent-visible-replay-case.v1",
            "case_id": case.case_id,
            "scenario_id": case.scenario_id,
            "captured_started_at": started_at,
            "captured_ended_at": ended_at,
            "observations": fixtures,
            "full_context_tools": case.full_context_tools,
        }
        case_draft = cast(Any, AgentVisibleReplayCaseV21).model_construct(
            **case_payload, case_sha256="0" * 64
        )
        replay_case = AgentVisibleReplayCaseV21.model_validate(
            {
                **case_payload,
                "case_sha256": semantic_sha256(
                    case_draft.model_dump(mode="json", exclude={"case_sha256"})
                ),
            }
        )
        truth = build_evaluator_truth_v21(case)
        case_root = self.private_root / "cases" / case.case_id
        write_private_json(
            case_root / "agent-visible.json", replay_case, create_once=True
        )
        write_private_json(case_root / "evaluator-truth.json", truth, create_once=True)
        return CapturedCaseArtifactV21(
            case_id=case.case_id,
            case_sha256=replay_case.case_sha256,
            truth_sha256=truth.truth_sha256,
            fault_operation_count=self.fault_operation_count,
        )

    def restore_baseline(self) -> None:
        if self.active_condition is None:
            raise RuntimeError("capture reset lacks an active condition")
        for service in sorted(_OWNED_STOP_SERVICES):
            self._service(service).ensure_running()
        self._flags().apply(self._baseline())
        if self.email_restart_required_v21:
            self._email_v21().restart()
        self.active_condition = None
        self.email_restart_required_v21 = False
        self.email_resource_fixture_v21 = None
        self.recovery_trace_fixture_v21 = None
        self.case_started_at = None
        self.fault_operation_count = 0

    def verify_baseline(self) -> None:
        self._flags().verify(self._baseline())
        for service in sorted(_OWNED_STOP_SERVICES):
            self._service(service).ensure_running()
        health = self._environment().wait_healthy(timeout_seconds=120)
        if not all(health.values()):
            raise RuntimeError("capture baseline health is incomplete")
        self._environment().verify_owned_resources(require_complete=True)

    def _capture_fixture_v21(
        self, request: ReadToolRequest
    ) -> ReplayObservationFixtureV21:
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
            error = ToolErrorCode.INTERNAL_CONTRACT_VIOLATION
        scope = (
            (request.service,)
            if hasattr(request, "service")
            else tuple(request.services)
        )
        return _fixture_v21(
            tool=request.tool,
            service_scope=tuple(sorted(scope)),
            records=records,
            truncated=truncated,
            error_code=error,
        )

    def _resource_record(
        self, *, service: str, window_seconds: int, sample_count: int
    ) -> ResourceUsageRecord:
        result = self._backend().execute(
            build_inspect_resource_usage_request(
                run_id=secrets.token_hex(16),
                services=(service,),
                sampling_window_seconds=window_seconds,
                sample_count=sample_count,
            )
        )
        if (
            len(result.records) != 1
            or type(result.records[0]) is not ResourceUsageRecord
        ):
            raise RuntimeError("capture resource calibration is invalid")
        return result.records[0]

    def _runtime_record(self, service: str) -> RuntimeRecord:
        result = self._backend().execute(
            build_inspect_service_runtime_request(
                run_id=secrets.token_hex(16), services=(service,), max_results=1
            )
        )
        if len(result.records) != 1 or type(result.records[0]) is not RuntimeRecord:
            raise RuntimeError("capture runtime calibration is invalid")
        return result.records[0]

    def _metric_value(
        self,
        *,
        service: str,
        kind: MetricKind,
        started_at: datetime,
        ended_at: datetime | None = None,
    ) -> float:
        result = self._backend().execute(
            build_query_metrics_request(
                run_id=secrets.token_hex(16),
                service=service,
                started_at=started_at,
                ended_at=ended_at or datetime.now(timezone.utc),
                metric_kinds=(kind,),
                max_results=1,
            )
        )
        if len(result.records) != 1 or type(result.records[0]) is not MetricRecord:
            raise RuntimeError("capture metric calibration is invalid")
        return result.records[0].value

    def _trace_records(
        self, *, service: str, started_at: datetime, ended_at: datetime
    ) -> tuple[TraceNeighborhoodRecord, ...]:
        result = self._backend().execute(
            build_trace_neighborhood_request(
                run_id=secrets.token_hex(16),
                service=service,
                started_at=started_at,
                ended_at=ended_at,
                max_spans=40,
            )
        )
        if any(type(item) is not TraceNeighborhoodRecord for item in result.records):
            raise RuntimeError("capture trace calibration is invalid")
        return tuple(cast(TraceNeighborhoodRecord, item) for item in result.records)

    def _service(self, service: str) -> ExactOwnedServiceControllerV21:
        try:
            return self.service_controllers[service]
        except KeyError as error:
            raise RuntimeError("owned service controller is unavailable") from error

    def _email_v21(self) -> OwnedEmailController:
        if self.email_v21 is None:
            raise RuntimeError("owned Email controller is unavailable")
        return self.email_v21

    def _case_start(self) -> datetime:
        if self.case_started_at is None:
            raise RuntimeError("capture case start is unavailable")
        return self.case_started_at


def _fixture_v21(
    *,
    tool: ToolName,
    service_scope: tuple[str, ...],
    records: tuple[Any, ...],
    truncated: bool,
    error_code: ToolErrorCode | None,
) -> ReplayObservationFixtureV21:
    payload: dict[str, object] = {
        "schema_version": "dta-v21.replay-observation-fixture.v1",
        "tool": tool,
        "service_scope": service_scope,
        "records": records,
        "truncated": truncated,
        "error_code": error_code,
    }
    draft = cast(Any, ReplayObservationFixtureV21).model_construct(
        **payload, fixture_sha256="0" * 64
    )
    return ReplayObservationFixtureV21.model_validate(
        {
            **payload,
            "fixture_sha256": semantic_sha256(
                draft.model_dump(mode="json", exclude={"fixture_sha256"})
            ),
        }
    )


def _failed_fixture_v21(
    *, tool: ToolName, service_scope: tuple[str, ...], error_code: ToolErrorCode
) -> ReplayObservationFixtureV21:
    return _fixture_v21(
        tool=tool,
        service_scope=service_scope,
        records=(),
        truncated=False,
        error_code=error_code,
    )


def _service_for_family(family: OperationalFamilyV21) -> str:
    return {
        OperationalFamilyV21.PAYMENT_CONFIGURATION: "payment",
        OperationalFamilyV21.EMAIL_MEMORY_LEAK: "email",
        OperationalFamilyV21.RECOMMENDATION_UNAVAILABLE: "recommendation",
        OperationalFamilyV21.AD_CPU_SATURATION: "ad",
        OperationalFamilyV21.EMAIL_UNAVAILABLE: "email",
        OperationalFamilyV21.PRODUCT_CATALOG_UNAVAILABLE: "product-catalog",
        OperationalFamilyV21.SHIPPING_DEPENDENCY_LATENCY: "shipping",
        OperationalFamilyV21.NO_FAULT: "payment",
        OperationalFamilyV21.MISSING_CONFLICTING_EVIDENCE: "email",
    }[family]


def require_capture_case_quality_v21(
    case: CaptureCasePlanV21,
    fixtures: tuple[ReplayObservationFixtureV21, ...],
) -> None:
    by_tool = {item.tool: item for item in fixtures}

    def successful(tool: ToolName) -> ReplayObservationFixtureV21:
        fixture = by_tool.get(tool)
        if fixture is None or fixture.error_code is not None or not fixture.records:
            raise ValueError("required capture source is unavailable")
        return fixture

    if case.condition is CaptureConditionV21.SOURCE_PARTIAL_FAILURE:
        failed = by_tool.get(ToolName.QUERY_TRACE_NEIGHBORHOOD)
        if failed is None or failed.error_code is not ToolErrorCode.SOURCE_UNAVAILABLE:
            raise ValueError("partial-source case lacks the exact unavailable fixture")
        successful(ToolName.QUERY_METRICS)
        successful(ToolName.INSPECT_SERVICE_RUNTIME)
        return
    metrics = successful(ToolName.QUERY_METRICS)
    runtime = successful(ToolName.INSPECT_SERVICE_RUNTIME)
    service = _service_for_family(case.operational_family)
    if not any(
        type(item) is MetricRecord and item.service == service
        for item in metrics.records
    ):
        raise ValueError("capture metrics lack the exact target")
    runtime_records = tuple(
        item
        for item in runtime.records
        if type(item) is RuntimeRecord and item.logical_service == service
    )
    if not runtime_records:
        raise ValueError("capture runtime lacks the exact target")
    if case.condition in {
        CaptureConditionV21.RECOMMENDATION_STOP,
        CaptureConditionV21.EMAIL_STOP,
        CaptureConditionV21.PRODUCT_CATALOG_STOP,
    } and not any(item.state is RuntimeState.EXITED for item in runtime_records):
        raise ValueError("service-unavailable capture target is not stopped")
    if case.condition in {
        CaptureConditionV21.EMAIL_MEMORY_LEAK,
        CaptureConditionV21.AD_HIGH_CPU,
    }:
        resources = successful(ToolName.INSPECT_RESOURCE_USAGE)
        if not any(
            type(item) is ResourceUsageRecord and item.logical_service == service
            for item in resources.records
        ):
            raise ValueError("resource capture lacks the exact target")
    if case.condition is CaptureConditionV21.SHIPPING_SLOWDOWN:
        traces = successful(ToolName.QUERY_TRACE_NEIGHBORHOOD)
        if not any(
            type(item) is TraceNeighborhoodRecord
            and item.duration_ms >= 1_000.0
            and service in item.service_path
            for item in traces.records
        ):
            raise ValueError("shipping capture lacks attributable latency")


def build_evaluator_truth_v21(
    case: CaptureCasePlanV21,
) -> EvaluatorCaseTruthV21:
    family = case.operational_family
    semantics: dict[str, object]
    slice_value = GeneralizationSliceV21.SEEN_SERVICE_SEEN_MECHANISM
    if family is OperationalFamilyV21.PAYMENT_CONFIGURATION:
        semantics = _completed_truth(
            service="payment",
            domain=FaultDomainV21.CONFIGURATION,
            mechanism=FaultMechanismV21.CONFIGURATION_ERROR,
            runbook=RunbookIdV21.ROLLBACK_CONFIGURATION,
            sources=(EvidenceSourceV21.METRICS, EvidenceSourceV21.TRACES),
        )
    elif family is OperationalFamilyV21.EMAIL_MEMORY_LEAK:
        semantics = _completed_truth(
            service="email",
            domain=FaultDomainV21.LOCAL_RESOURCE,
            mechanism=FaultMechanismV21.MEMORY_LEAK,
            runbook=RunbookIdV21.MITIGATE_MEMORY_LEAK,
            sources=(
                EvidenceSourceV21.METRICS,
                EvidenceSourceV21.RUNTIME,
                EvidenceSourceV21.RESOURCES,
            ),
        )
    elif family is OperationalFamilyV21.RECOMMENDATION_UNAVAILABLE:
        semantics = _completed_truth(
            service="recommendation",
            domain=FaultDomainV21.SERVICE_RUNTIME,
            mechanism=FaultMechanismV21.SERVICE_UNAVAILABLE,
            runbook=RunbookIdV21.RESTART_SERVICE,
            sources=(EvidenceSourceV21.METRICS, EvidenceSourceV21.RUNTIME),
        )
    elif family is OperationalFamilyV21.AD_CPU_SATURATION:
        slice_value = GeneralizationSliceV21.NEW_SERVICE_NEW_MECHANISM
        semantics = _completed_truth(
            service="ad",
            domain=FaultDomainV21.LOCAL_RESOURCE,
            mechanism=FaultMechanismV21.CPU_SATURATION,
            runbook=RunbookIdV21.MITIGATE_CPU_SATURATION,
            sources=(
                EvidenceSourceV21.METRICS,
                EvidenceSourceV21.RUNTIME,
                EvidenceSourceV21.RESOURCES,
            ),
        )
    elif family is OperationalFamilyV21.EMAIL_UNAVAILABLE:
        slice_value = GeneralizationSliceV21.SAME_SERVICE_MULTIPLE_MECHANISMS
        semantics = _completed_truth(
            service="email",
            domain=FaultDomainV21.SERVICE_RUNTIME,
            mechanism=FaultMechanismV21.SERVICE_UNAVAILABLE,
            runbook=RunbookIdV21.RESTORE_SERVICE_AVAILABILITY,
            sources=(EvidenceSourceV21.METRICS, EvidenceSourceV21.RUNTIME),
        )
    elif family is OperationalFamilyV21.PRODUCT_CATALOG_UNAVAILABLE:
        slice_value = GeneralizationSliceV21.NEW_SERVICE_SEEN_MECHANISM
        semantics = _completed_truth(
            service="product-catalog",
            domain=FaultDomainV21.SERVICE_RUNTIME,
            mechanism=FaultMechanismV21.SERVICE_UNAVAILABLE,
            runbook=RunbookIdV21.RESTORE_SERVICE_AVAILABILITY,
            sources=(EvidenceSourceV21.TRACES, EvidenceSourceV21.RUNTIME),
        )
    elif family is OperationalFamilyV21.SHIPPING_DEPENDENCY_LATENCY:
        slice_value = GeneralizationSliceV21.NEW_SERVICE_NEW_MECHANISM
        semantics = _completed_truth(
            service="shipping",
            domain=FaultDomainV21.DEPENDENCY,
            mechanism=FaultMechanismV21.DEPENDENCY_LATENCY,
            runbook=RunbookIdV21.RESTORE_DEPENDENCY_LATENCY,
            sources=(EvidenceSourceV21.METRICS, EvidenceSourceV21.TRACES),
        )
    elif family is OperationalFamilyV21.NO_FAULT:
        slice_value = GeneralizationSliceV21.NO_FAULT
        semantics = {
            "expected_terminal": TerminalV21.COMPLETED,
            "expected_root_service": None,
            "expected_fault_domain": None,
            "expected_mechanism": None,
            "expected_disposition": ActionDispositionV21.NO_ACTION,
            "expected_runbook": None,
            "expected_evidence_sources": (
                EvidenceSourceV21.METRICS,
                EvidenceSourceV21.RUNTIME,
            ),
        }
    else:
        slice_value = GeneralizationSliceV21.MISSING_CONFLICTING_EVIDENCE
        semantics = {
            "expected_terminal": (
                TerminalV21.ABSTAIN
                if case.condition is CaptureConditionV21.SOURCE_PARTIAL_FAILURE
                else TerminalV21.NEED_MORE_EVIDENCE
            ),
            "expected_root_service": None,
            "expected_fault_domain": None,
            "expected_mechanism": None,
            "expected_disposition": None,
            "expected_runbook": None,
            "expected_evidence_sources": (
                EvidenceSourceV21.METRICS,
                EvidenceSourceV21.RUNTIME,
            ),
        }
    payload: dict[str, object] = {
        "schema_version": "dta-v21.evaluator-case-truth.v1",
        "case_id": case.case_id,
        "split": case.split,
        "scenario_family": case.evaluator_family,
        "generalization_slice": slice_value,
        "meaningful_observation_differences": (case.meaningful_observation_differences),
        **semantics,
    }
    draft = cast(Any, EvaluatorCaseTruthV21).model_construct(
        **payload, truth_sha256="0" * 64
    )
    return EvaluatorCaseTruthV21.model_validate(
        {
            **payload,
            "truth_sha256": semantic_sha256(
                draft.model_dump(mode="json", exclude={"truth_sha256"})
            ),
        }
    )


def _completed_truth(
    *,
    service: str,
    domain: FaultDomainV21,
    mechanism: FaultMechanismV21,
    runbook: RunbookIdV21,
    sources: tuple[EvidenceSourceV21, ...],
) -> dict[str, object]:
    return {
        "expected_terminal": TerminalV21.COMPLETED,
        "expected_root_service": service,
        "expected_fault_domain": domain,
        "expected_mechanism": mechanism,
        "expected_disposition": ActionDispositionV21.EXECUTE_RUNBOOK,
        "expected_runbook": runbook,
        "expected_evidence_sources": sources,
    }


def run_owned_capture_campaign_v21(
    *,
    repository_root: Path,
    private_root: Path,
    base_head: str,
    stabilization_seconds: int = 30,
) -> CaptureCampaignClosureV21:
    plan = build_default_capture_plan_v21(base_head=base_head)
    lifecycle = OwnedCaptureLifecycleV21(
        repository_root=repository_root,
        private_root=private_root,
        plan=plan,
        stabilization_seconds=stabilization_seconds,
    )
    closure = run_capture_campaign_attempt_v21(plan=plan, lifecycle=lifecycle)
    write_private_json(
        private_root / "capture-campaign-closure.json", closure, create_once=True
    )
    return closure


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository-root", type=Path, required=True)
    parser.add_argument("--private-root", type=Path, required=True)
    parser.add_argument("--base-head", required=True)
    parser.add_argument("--stabilization-seconds", type=int, default=30)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    closure = run_owned_capture_campaign_v21(
        repository_root=args.repository_root.resolve(),
        private_root=args.private_root.resolve(),
        base_head=args.base_head,
        stabilization_seconds=args.stabilization_seconds,
    )
    print(closure.model_dump_json(indent=2))
    return 0 if closure.terminal.value == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = (
    "ExactFlagDocumentControllerV21",
    "ExactOwnedServiceControllerV21",
    "OwnedCaptureLifecycleV21",
    "build_capture_flag_document_v21",
    "build_evaluator_truth_v21",
    "require_capture_case_quality_v21",
    "run_owned_capture_campaign_v21",
)
