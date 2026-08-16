"""Owned local Sandbox implementation for the PR-F lifecycle protocol."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import http.client
import json
import math
from pathlib import Path
import re
import time
from typing import Callable
from urllib.parse import urlsplit

from ecomsre.dta_v2.agent import DtaAgentRunResult, run_tool_using_agent
from ecomsre.dta_v2.agent_contracts import AgentIdentityManifest, build_alert_context
from ecomsre.dta_v2.agent_provider import (
    OpenAICompatibleDtaAgentProvider,
    build_provider_identity,
)
from ecomsre.dta_v2.authorization import (
    AttemptAuthorizationRecord,
    MasterAuthorizationRecord,
)
from ecomsre.dta_v2.contracts import Precondition, semantic_sha256
from ecomsre.dta_v2.live_contracts import (
    BaselineEvidence,
    CleanupTerminal,
    ForwardExecution,
    LiveAttemptMode,
    LiveAttemptClosure,
    LiveCampaignAttemptClaim,
    LiveDemoConfig,
    LiveScenario,
    LiveScenarioSpec,
    PreLiveFreeze,
    RecoveryWindow,
    build_baseline_evidence,
    build_live_campaign_attempt_claim,
    build_pre_live_freeze,
    build_recovery_window,
)
from ecomsre.dta_v2.live_capability import (
    _OWNED_CAMPAIGN_TOKEN,
    issue_owned_live_execution_grant,
)
from ecomsre.dta_v2.live_controls import OwnedLiveControls
from ecomsre.dta_v2.operational_contracts import (
    CurrentStateSnapshot,
    DockerBoundary,
    OwnershipStatus,
    PreconditionObservation,
    ServiceRuntimeState,
    build_current_state_snapshot,
)
from ecomsre.dta_v2.owned_capture import (
    EMAIL_CAPTURE_MAXIMUM_MEMORY_BYTES,
    ExactFlagDocumentController,
    OwnedCaptureLifecycle,
    OwnedEmailController,
    OwnedRecommendationController,
    build_capture_flag_document,
)
from ecomsre.dta_v2.read_only_smoke import CleanupObservation
from ecomsre.dta_v2.registry import (
    RunbookRegistry,
    load_scenario_registry,
)
from ecomsre.dta_v2.live_runner import run_live_attempt
from ecomsre.dta_v2.provider_env import load_private_provider_env
from ecomsre.dta_v2.telemetry_adapters import (
    LocalSandboxReadBackend,
    _issue_owned_read_capability,
)
from ecomsre.dta_v2.tool_contracts import (
    HealthState,
    MetricKind,
    MetricRecord,
    ResourceUsageRecord,
    RuntimeRecord,
    RuntimeState,
    build_inspect_resource_usage_request,
    build_inspect_service_runtime_request,
    build_query_metrics_request,
)
from ecomsre.model.gateway import OpenAICompatibleConfig
from ecomsre_live_sandbox.control import _restore_private_flag_mode
from ecomsre_live_sandbox.contracts import (
    ensure_private_directory,
    write_private_json,
)
from ecomsre_live_sandbox.environment import ExactCommandRunner


_FROZEN_MODEL = "gpt-5.4-2026-03-05"
_SOURCE_FILES = {
    "candidate_filter_source_sha256": "src/ecomsre/dta_v2/candidate_filter.py",
    "admission_source_sha256": "src/ecomsre/dta_v2/policy.py",
    "authorization_source_sha256": "src/ecomsre/dta_v2/authorization.py",
    "executor_source_sha256": "src/ecomsre/dta_v2/live_execution.py",
    "verifier_source_sha256": "src/ecomsre/dta_v2/live_verifiers.py",
    "runner_source_sha256": "src/ecomsre/dta_v2/live_runner.py",
    "reporting_schema_sha256": "src/ecomsre/dta_v2/live_reporting.py",
}
_SEMANTIC_FILES = (
    "config/dta-v2/agent-identity.v1.json",
    "config/dta-v2/live-demo.v1.json",
    "config/live-telemetry-controlled-remediation-v1/budget.json",
    "config/live-telemetry-controlled-remediation-v1/compose.sandbox.yaml",
    "config/live-telemetry-controlled-remediation-v1/sandbox.json",
    "config/live-telemetry-controlled-remediation-v1/scenario.json",
    "config/live-telemetry-controlled-remediation-v1/telemetry.json",
    "config/live-telemetry-controlled-remediation-v1/verification.json",
    "src/ecomsre/dta_v2/agent.py",
    "src/ecomsre/dta_v2/agent_contracts.py",
    "src/ecomsre/dta_v2/agent_evidence.py",
    "src/ecomsre/dta_v2/agent_provider.py",
    "src/ecomsre/dta_v2/authorization.py",
    "src/ecomsre/dta_v2/candidate_filter.py",
    "src/ecomsre/dta_v2/contracts.py",
    "src/ecomsre/dta_v2/docker_read_adapters.py",
    "src/ecomsre/dta_v2/evidence_store.py",
    "src/ecomsre/dta_v2/live_contracts.py",
    "src/ecomsre/dta_v2/live_capability.py",
    "src/ecomsre/dta_v2/live_cli.py",
    "src/ecomsre/dta_v2/live_controls.py",
    "src/ecomsre/dta_v2/live_execution.py",
    "src/ecomsre/dta_v2/live_owned.py",
    "src/ecomsre/dta_v2/live_reporting.py",
    "src/ecomsre/dta_v2/live_runner.py",
    "src/ecomsre/dta_v2/live_state.py",
    "src/ecomsre/dta_v2/live_verifiers.py",
    "src/ecomsre/dta_v2/owned_capture.py",
    "src/ecomsre/dta_v2/policy.py",
    "src/ecomsre/dta_v2/provider_env.py",
    "src/ecomsre/dta_v2/read_tools.py",
    "src/ecomsre/dta_v2/read_only_smoke.py",
    "src/ecomsre/dta_v2/registry.py",
    "src/ecomsre/dta_v2/telemetry_adapters.py",
    "src/ecomsre/dta_v2/tool_contracts.py",
    "src/ecomsre_live_sandbox/contracts.py",
    "src/ecomsre_live_sandbox/control.py",
    "src/ecomsre_live_sandbox/environment.py",
    "src/ecomsre/model/gateway.py",
    "third_party/opentelemetry-demo/compose.yaml",
    "third_party/opentelemetry-demo/compose.observability.yaml",
    "third_party/opentelemetry-demo/src/flagd/demo.flagd.json",
)


def _file_sha256(path: Path) -> str:
    target = Path(path)
    if target.is_symlink() or not target.is_file():
        raise ValueError("frozen source must be a regular non-symlink file")
    return hashlib.sha256(target.read_bytes()).hexdigest()


def _clean_semantic_manifest(
    root: Path,
    *,
    config: LiveDemoConfig,
    runner: ExactCommandRunner | None = None,
) -> tuple[str, str]:
    command = runner or ExactCommandRunner()
    dirty = command.run(
        ("git", "status", "--porcelain=v1", "--untracked-files=all"), cwd=root
    ).stdout
    if dirty:
        raise ValueError("pre-live repository must be exactly clean")
    code_head = command.run(("git", "rev-parse", "HEAD"), cwd=root).stdout.strip()
    submodule_status = command.run(
        ("git", "submodule", "status", "--", "third_party/opentelemetry-demo"),
        cwd=root,
    ).stdout.strip("\n")
    expected_submodule = f" {config.upstream_commit} third_party/opentelemetry-demo"
    if not submodule_status.startswith(expected_submodule):
        raise ValueError("pinned OpenTelemetry submodule differs from exact commit")
    semantic_files = {
        relative: _file_sha256(root / relative) for relative in _SEMANTIC_FILES
    }
    for directory in (
        root / "config/dta-v2/runbooks",
        root / "config/dta-v2/scenarios/agent-visible",
    ):
        for path in sorted(directory.glob("*.json")):
            semantic_files[path.relative_to(root).as_posix()] = _file_sha256(path)
    manifest = semantic_sha256(
        {
            "schema_version": "dta-v2.pre-live-semantic-manifest.v1",
            "code_head": code_head,
            "submodule_commit": config.upstream_commit,
            "files": semantic_files,
        }
    )
    return code_head, manifest


def build_frozen_provider_config(
    provider_env_path: Path,
    *,
    freeze: PreLiveFreeze,
) -> OpenAICompatibleConfig:
    """Use private origin/key inputs without accepting their mutable model value."""

    freeze = PreLiveFreeze.model_validate(freeze.model_dump(mode="python"))
    identity = build_provider_identity(freeze.model_id)
    if (
        identity.identity_sha256 != freeze.agent_identity_sha256
        or identity.prompt_sha256 != freeze.prompt_sha256
        or identity.tool_schema_sha256 != freeze.tool_schema_sha256
        or identity.diagnosis_schema_sha256 != freeze.diagnosis_schema_sha256
        or identity.action_selection_schema_sha256
        != freeze.action_selection_schema_sha256
        or identity.action_proposal_schema_sha256
        != freeze.action_proposal_schema_sha256
    ):
        raise ValueError("frozen Provider identity differs from runtime contracts")
    values = load_private_provider_env(Path(provider_env_path))
    return OpenAICompatibleConfig(
        base_url=values["ECOMSRE_LLM_BASE_URL"],
        api_key=values["ECOMSRE_LLM_API_KEY"],
        model=freeze.model_id,
    )


@dataclass(frozen=True)
class OwnedLivePreflight:
    """Read-only admitted authority retained for exactly one owned attempt."""

    capture: OwnedCaptureLifecycle
    resolved_compose_sha256: str
    image_authority_sha256: str


def prepare_owned_live_preflight(
    *,
    repository_root: Path,
    private_root: Path,
    config: LiveDemoConfig,
    scenario: LiveScenarioSpec,
    stabilization_seconds: int = 90,
) -> OwnedLivePreflight:
    """Authenticate and resolve the owned Sandbox without starting it."""

    config = LiveDemoConfig.model_validate(config.model_dump(mode="python"))
    scenario = LiveScenarioSpec.model_validate(scenario.model_dump(mode="python"))
    if scenario not in config.scenarios:
        raise ValueError("preflight scenario is outside the frozen live config")
    if not 90 <= stabilization_seconds <= 300:
        raise ValueError("live stabilization must be between 90 and 300 seconds")
    root = Path(repository_root).resolve()
    private = Path(private_root).resolve()
    if root == Path("/") or private == Path("/"):
        raise ValueError("live roots may not be filesystem root")
    if private.is_relative_to(root):
        raise ValueError("private live evidence must remain outside the repository")
    capture = OwnedCaptureLifecycle(
        repository_root=root,
        private_root=private,
        plan=config,
        stabilization_seconds=stabilization_seconds,
    )
    capture.admit()
    resolved, _ = capture._environment().resolve()
    return OwnedLivePreflight(
        capture=capture,
        resolved_compose_sha256=resolved.compose_sha256,
        image_authority_sha256=_file_sha256(private / "control/image-lock.json"),
    )


def build_repository_pre_live_freeze(
    *,
    preflight: OwnedLivePreflight,
    config: LiveDemoConfig,
    registry: RunbookRegistry,
) -> PreLiveFreeze:
    """Freeze the exact repository and authority proven by owned preflight."""

    if type(preflight) is not OwnedLivePreflight:
        raise TypeError("pre-live freeze requires exact owned preflight")
    config = LiveDemoConfig.model_validate(config.model_dump(mode="python"))
    registry = RunbookRegistry.model_validate(registry.model_dump(mode="python"))
    root = preflight.capture.repository_root
    environment = preflight.capture._environment()
    code_head, semantic_manifest_sha256 = _clean_semantic_manifest(
        root,
        config=config,
        runner=environment.runner,
    )
    identity_path = root / "config/dta-v2/agent-identity.v1.json"
    identity = AgentIdentityManifest.model_validate_json(
        identity_path.read_text(encoding="utf-8")
    )
    if identity.model_id != _FROZEN_MODEL or identity != build_provider_identity(
        _FROZEN_MODEL
    ):
        raise ValueError("checked-in Agent identity differs from runtime contracts")
    source_hashes = {
        field: _file_sha256(root / relative)
        for field, relative in _SOURCE_FILES.items()
    }
    return build_pre_live_freeze(
        code_head=code_head,
        agent_identity_sha256=identity.identity_sha256,
        model_id=identity.model_id,
        prompt_sha256=identity.prompt_sha256,
        tool_schema_sha256=identity.tool_schema_sha256,
        diagnosis_schema_sha256=identity.diagnosis_schema_sha256,
        action_selection_schema_sha256=identity.action_selection_schema_sha256,
        action_proposal_schema_sha256=identity.action_proposal_schema_sha256,
        registry_sha256=registry.registry_sha256,
        upstream_commit=config.upstream_commit,
        upstream_tag=config.upstream_tag,
        resolved_compose_sha256=preflight.resolved_compose_sha256,
        image_authority_sha256=preflight.image_authority_sha256,
        live_config=config,
        semantic_manifest_sha256=semantic_manifest_sha256,
        **source_hashes,
    )


class OwnedSandboxLiveLifecycle:
    """Exact production lifecycle; only fixed PR-F operations are exposed."""

    mode = LiveAttemptMode.OWNED_LOCAL

    def __init__(
        self,
        *,
        claim: LiveCampaignAttemptClaim,
        preflight: OwnedLivePreflight,
        provider_env_path: Path,
        freeze: PreLiveFreeze,
        config: LiveDemoConfig,
        scenario: LiveScenarioSpec,
        registry: RunbookRegistry,
        utc_now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        sleep: Callable[[float], None] = time.sleep,
        _campaign_token: object | None = None,
    ) -> None:
        if _campaign_token is not _OWNED_CAMPAIGN_TOKEN:
            raise TypeError("owned live lifecycle is campaign-issued only")
        if type(preflight) is not OwnedLivePreflight:
            raise TypeError("owned live lifecycle requires exact preflight")
        self.preflight = preflight
        self.claim = LiveCampaignAttemptClaim.model_validate(
            claim.model_dump(mode="python")
        )
        self.provider_env_path = Path(provider_env_path)
        self.freeze = PreLiveFreeze.model_validate(freeze.model_dump(mode="python"))
        self.config = LiveDemoConfig.model_validate(config.model_dump(mode="python"))
        self.scenario = LiveScenarioSpec.model_validate(scenario.model_dump(mode="python"))
        self.registry = RunbookRegistry.model_validate(registry.model_dump(mode="python"))
        if self.scenario not in self.config.scenarios:
            raise ValueError("owned lifecycle scenario is outside live config")
        if self.claim.scenario is not self.scenario.scenario:
            raise ValueError("owned lifecycle claim differs from scenario")
        self._utc_now = utc_now
        self._sleep = sleep
        self._provider: OpenAICompatibleDtaAgentProvider | None = None
        self._backend: LocalSandboxReadBackend | None = None
        self._flags: ExactFlagDocumentController | None = None
        self._recommendation: OwnedRecommendationController | None = None
        self._email: OwnedEmailController | None = None
        self._active_fault_document: dict[str, object] | None = None
        self._baseline_error_rates: tuple[float, float] | None = None
        self._owned_authority_fingerprint: tuple[str, ...] | None = None
        self._last_controls: OwnedLiveControls | None = None
        self._restoration_write_count = 0
        self._fault_attempted = False
        self._fault_applied = False
        self._admitted_baseline_state_digest: str | None = None
        self._admitted_execution_state_digest: str | None = None

    @property
    def capture(self) -> OwnedCaptureLifecycle:
        return self.preflight.capture

    def verify_pre_live(self, freeze: PreLiveFreeze) -> None:
        observed = PreLiveFreeze.model_validate(freeze.model_dump(mode="python"))
        if observed != self.freeze:
            raise ValueError("runner pre-live freeze differs from owned lifecycle")
        expected = build_repository_pre_live_freeze(
            preflight=self.preflight, config=self.config, registry=self.registry
        )
        if observed != expected:
            raise ValueError("owned pre-live repository or authority drifted")
        provider_config = build_frozen_provider_config(
            self.provider_env_path, freeze=observed
        )
        self._provider = OpenAICompatibleDtaAgentProvider(
            config=provider_config,
            timeout_seconds=120.0,
            max_completion_tokens=2048,
        )

    def admit_environment(self) -> None:
        environment = self.capture._environment()
        environment.verify_local_docker()
        environment.verify_upstream()
        resolved, _ = environment.resolve()
        if resolved.compose_sha256 != self.freeze.resolved_compose_sha256:
            raise ValueError("fresh resolved Compose differs from pre-live freeze")
        environment.inspect_cached_images(resolved)
        image_authority = _file_sha256(
            self.capture.private_root / "control/image-lock.json"
        )
        if image_authority != self.freeze.image_authority_sha256:
            raise ValueError("fresh image authority differs from pre-live freeze")

    def start(self) -> None:
        self.capture.start()

    def wait_ready(self) -> None:
        self.capture.wait_ready()

    def _refresh_backend(self) -> LocalSandboxReadBackend:
        environment = self.capture._environment()
        capability = _issue_owned_read_capability(
            environment=environment,
            bundle=self.capture._bundle(),
            admitted_resolved_sha256=self.capture._admitted_sha(),
            timeout_seconds=10.0,
        )
        environment.verify_owned_resources(require_complete=True)
        backend = LocalSandboxReadBackend._from_owned_capability(capability)
        authority = backend.authority
        raw_fingerprint = (
            authority.daemon_identity_sha256,
            authority.docker_context_sha256,
            authority.config_bundle_sha256,
            authority.resolved_sandbox_sha256,
            authority.resolved_endpoints_sha256,
            authority.ownership_scope_sha256,
        )
        if not all(isinstance(item, str) for item in raw_fingerprint):
            raise RuntimeError("fresh owned authority is incomplete")
        fingerprint = tuple(
            item for item in raw_fingerprint if isinstance(item, str)
        )
        if self._owned_authority_fingerprint is None:
            self._owned_authority_fingerprint = fingerprint
        elif fingerprint != self._owned_authority_fingerprint:
            raise RuntimeError("fresh owned authority drifted")
        self._backend = backend
        self._flags = ExactFlagDocumentController(
            endpoints=capability.resolved_sandbox.endpoints,
            flag_file=self.capture._flag_file(),
            upstream=self.capture._upstream(),
        )
        self._recommendation = OwnedRecommendationController(backend)
        self._email = OwnedEmailController(backend)
        return backend

    def _require_backend(self) -> LocalSandboxReadBackend:
        if self._backend is None:
            raise RuntimeError("owned live read backend is unavailable")
        return self._backend

    def _require_flags(self) -> ExactFlagDocumentController:
        if self._flags is None:
            raise RuntimeError("owned live flag controller is unavailable")
        return self._flags

    def _require_recommendation(self) -> OwnedRecommendationController:
        if self._recommendation is None:
            raise RuntimeError("owned recommendation controller is unavailable")
        return self._recommendation

    def _require_email(self) -> OwnedEmailController:
        if self._email is None:
            raise RuntimeError("owned Email controller is unavailable")
        return self._email

    def _metrics(
        self, *, service: str, started_at: datetime, ended_at: datetime
    ) -> tuple[float, float]:
        result = self._require_backend().execute(
            build_query_metrics_request(
                run_id="f" * 32,
                service=service,
                started_at=started_at,
                ended_at=ended_at,
                metric_kinds=(MetricKind.ERROR_RATE, MetricKind.REQUEST_SUPPORT),
                max_results=2,
            )
        )
        records = {
            item.metric_kind: item
            for item in result.records
            if type(item) is MetricRecord
        }
        if set(records) != {MetricKind.ERROR_RATE, MetricKind.REQUEST_SUPPORT}:
            raise RuntimeError("live SLI metric set is incomplete")
        error_rate = records[MetricKind.ERROR_RATE].value
        request_support = records[MetricKind.REQUEST_SUPPORT].value
        if not math.isfinite(error_rate) or not math.isfinite(request_support):
            raise RuntimeError("live SLI metric is non-finite")
        return error_rate, request_support

    def _runtime(self, service: str) -> RuntimeRecord:
        result = self._require_backend().execute(
            build_inspect_service_runtime_request(
                run_id="e" * 32, services=(service,), max_results=1
            )
        )
        if len(result.records) != 1 or type(result.records[0]) is not RuntimeRecord:
            raise RuntimeError("owned runtime observation is incomplete")
        return result.records[0]

    def _email_resource(self) -> ResourceUsageRecord:
        result = self._require_backend().execute(
            build_inspect_resource_usage_request(
                run_id="d" * 32,
                services=("email",),
                sampling_window_seconds=10,
                sample_count=3,
            )
        )
        if len(result.records) != 1 or type(result.records[0]) is not ResourceUsageRecord:
            raise RuntimeError("owned Email resource observation is incomplete")
        return result.records[0]

    def _probe_frontend(self) -> bool:
        resolved, _ = self.capture._environment().resolve()
        parsed = urlsplit(resolved.endpoints.frontend)
        if parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or parsed.port is None:
            raise RuntimeError("owned frontend origin drifted")
        connection = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=10)
        try:
            connection.request("GET", "/", headers={"Accept": "text/html"})
            response = connection.getresponse()
            body = response.read(1_000_001)
            return 200 <= response.status < 300 and len(body) <= 1_000_000
        finally:
            connection.close()

    def _fault_observation_service(self) -> str:
        return {
            LiveScenario.NO_FAULT: "payment",
            LiveScenario.PAYMENT: "payment",
            LiveScenario.RECOMMENDATION: "recommendation",
            LiveScenario.EMAIL: "email",
        }[self.scenario.scenario]

    def _capture_window(
        self, ordinal: int, *, target: str
    ) -> tuple[RecoveryWindow, float]:
        if ordinal not in {1, 2}:
            raise ValueError("live window ordinal is invalid")
        if target not in {"email", "payment", "recommendation"}:
            raise ValueError("live observation target is outside the Registry scope")
        started_at = self._utc_now()
        self._sleep(30)
        metric_ended_at = self._utc_now()
        error_rate, request_support = self._metrics(
            service=target, started_at=started_at, ended_at=metric_ended_at
        )
        runtime = self._runtime(target)
        infrastructure = (
            runtime.owned_container_present
            and runtime.state is RuntimeState.RUNNING
            and runtime.health in {HealthState.HEALTHY, HealthState.NOT_CONFIGURED}
        )
        endpoint = self._probe_frontend()
        try:
            self._require_flags().verify(self.capture._baseline())
            configuration_restored = True
        except Exception:
            configuration_restored = False
        if self._baseline_error_rates is None:
            business = request_support > 0 and error_rate <= 0.02
        else:
            baseline = max(self._baseline_error_rates)
            verification = self.capture._bundle().verification
            threshold = max(
                baseline * verification.recovery_error_rate_multiplier,
                baseline + verification.recovery_error_rate_absolute_increase,
            )
            business = request_support > 0 and error_rate <= threshold
        slope = (
            self._email_resource().memory_slope_bytes_per_second
            if target == "email"
            else None
        )
        ended_at = self._utc_now()
        return (
            build_recovery_window(
                ordinal=ordinal,  # type: ignore[arg-type]
                started_at=started_at,
                ended_at=ended_at,
                infrastructure_passed=infrastructure,
                business_sli_passed=business,
                endpoint_passed=endpoint,
                configuration_restored=configuration_restored,
                memory_slope_bytes_per_second=slope,
            ),
            error_rate,
        )

    def capture_baseline(self) -> BaselineEvidence:
        self._refresh_backend()
        target = self._fault_observation_service()
        first, first_error = self._capture_window(1, target=target)
        second, second_error = self._capture_window(2, target=target)
        self._baseline_error_rates = (first_error, second_error)
        self._admitted_baseline_state_digest = self._require_baseline_mutation_state()
        return build_baseline_evidence(
            baseline_sha256=self._require_flags().verify(self.capture._baseline()),
            windows=(first, second),
        )

    def inject_fault(self, scenario: LiveScenarioSpec) -> None:
        scenario = LiveScenarioSpec.model_validate(scenario.model_dump(mode="python"))
        if scenario != self.scenario or scenario.scenario is LiveScenario.NO_FAULT:
            raise ValueError("fault injection differs from owned attempt scenario")
        self._require_fault_preconditions()
        self._fault_attempted = True
        payment = "100%" if scenario.scenario is LiveScenario.PAYMENT else "off"
        email = "1000x" if scenario.scenario is LiveScenario.EMAIL else "off"
        document = build_capture_flag_document(
            self.capture._upstream(),
            load_vus=25,
            payment_variant=payment,
            email_variant=email,
        )
        self._active_fault_document = document
        if scenario.scenario is LiveScenario.RECOMMENDATION:
            self._require_recommendation().stop()
            self._fault_applied = True
        else:
            self._require_flags().apply(document)
            self._fault_applied = True
            if scenario.scenario is LiveScenario.EMAIL:
                self._refresh_backend()
                self._require_email().restart()

    @property
    def fault_applied(self) -> bool:
        return self._fault_applied

    def revalidate_before_fault(self, scenario: LiveScenarioSpec) -> None:
        observed = LiveScenarioSpec.model_validate(scenario.model_dump(mode="python"))
        if observed != self.scenario or observed.scenario is LiveScenario.NO_FAULT:
            raise ValueError("fresh fault admission differs from owned scenario")
        self._require_fault_preconditions()

    def _require_fault_preconditions(self) -> None:
        admitted = self._admitted_baseline_state_digest
        if admitted is None or self._require_baseline_mutation_state() != admitted:
            raise RuntimeError("actual fault preconditions differ from admitted baseline")

    def verify_fault_impact(self, scenario: LiveScenarioSpec) -> bool:
        if scenario != self.scenario or self._baseline_error_rates is None:
            raise ValueError("fault-impact verification lacks exact baseline binding")
        if scenario.scenario is LiveScenario.RECOMMENDATION:
            return self._runtime("recommendation").state is RuntimeState.EXITED
        if self._active_fault_document is None:
            raise RuntimeError("fault document is unavailable")
        self._require_flags().verify(self._active_fault_document)
        if scenario.scenario is LiveScenario.EMAIL:
            resource = self._email_resource()
            maximum_memory = max(item.memory_bytes for item in resource.samples)
            return (
                maximum_memory <= EMAIL_CAPTURE_MAXIMUM_MEMORY_BYTES
                and resource.memory_slope_bytes_per_second
                >= self.config.maximum_email_recovery_slope_bytes_per_second
            )
        started_at = self._utc_now()
        self._sleep(30)
        ended_at = self._utc_now()
        error_rate, request_support = self._metrics(
            service="payment", started_at=started_at, ended_at=ended_at
        )
        baseline = max(self._baseline_error_rates)
        verification = self.capture._bundle().verification
        threshold = max(
            baseline * verification.fault_error_rate_multiplier,
            baseline + verification.fault_error_rate_absolute_increase,
        )
        return request_support > 0 and error_rate >= threshold

    def run_agent(self, scenario: LiveScenarioSpec) -> DtaAgentRunResult:
        if scenario != self.scenario or self._provider is None:
            raise ValueError("Agent dispatch lacks frozen owned lifecycle state")
        backend = self._refresh_backend()
        scenario_registry = load_scenario_registry(
            self.capture.repository_root / "config/dta-v2/scenarios/agent-visible"
        )
        agent_scenario = next(
            item for item in scenario_registry.scenarios if item.scenario_id == scenario.scenario_id
        )
        ended_at = self._utc_now()
        context = build_alert_context(
            scenario=agent_scenario,
            run_id=self.claim.run_id,
            started_at=ended_at - timedelta(minutes=5),
            ended_at=ended_at,
        )
        return run_tool_using_agent(
            context=context,
            backend=backend,
            registry=self.registry,
            provider=self._provider,
        )

    def current_state(
        self,
        *,
        scenario: LiveScenarioSpec,
        agent_result: DtaAgentRunResult,
        attempt_id: str,
    ) -> CurrentStateSnapshot:
        if scenario != self.scenario:
            raise ValueError("current-state scenario differs from owned attempt")
        backend = self._refresh_backend()
        authority = backend.authority
        proposal = agent_result.action_proposal
        target = (
            proposal.target_service
            if proposal is not None and proposal.target_service is not None
            else (
                agent_result.diagnosis.root_service
                if agent_result.diagnosis is not None
                and agent_result.diagnosis.root_service is not None
                else "payment"
            )
        )
        preconditions = (
            self.registry.require(proposal.runbook_id).preconditions
            if proposal is not None and proposal.runbook_id is not None
            else (Precondition.LOCAL_DOCKER_ONLY, Precondition.OWNED_SERVICE)
        )
        started_at = self._utc_now()
        monotonic_start = time.monotonic_ns()
        runtime = self._runtime(target)
        if not runtime.owned_container_present:
            raise RuntimeError("current-state target ownership is not proven")
        expected_document = self._active_fault_document or self.capture._baseline()
        configuration_digest = self._require_flags().verify(expected_document)
        runtime_state = (
            ServiceRuntimeState.STOPPED
            if runtime.state is RuntimeState.EXITED
            else (
                ServiceRuntimeState.RUNNING_HEALTHY
                if runtime.state is RuntimeState.RUNNING
                and runtime.health in {HealthState.HEALTHY, HealthState.NOT_CONFIGURED}
                else ServiceRuntimeState.RUNNING_UNHEALTHY
            )
        )
        satisfied = {
            Precondition.LOCAL_DOCKER_ONLY: True,
            Precondition.OWNED_SERVICE: True,
            Precondition.SERVICE_NOT_HEALTHY: runtime_state
            is not ServiceRuntimeState.RUNNING_HEALTHY,
            Precondition.CONFIGURATION_DRIFT_VISIBLE: expected_document
            != self.capture._baseline(),
            Precondition.BASELINE_HASH_BOUND: True,
            Precondition.LEAK_FLAG_ACTIVE: (
                scenario.scenario is LiveScenario.EMAIL
                and expected_document != self.capture._baseline()
            ),
        }
        ended_at = self._utc_now()
        snapshot = build_current_state_snapshot(
            run_id=agent_result.run_id,
            attempt_id=attempt_id,
            docker_boundary=DockerBoundary.LOCAL_UNIX,
            docker_context_identity=str(authority.docker_context_sha256),
            daemon_identity=str(authority.daemon_identity_sha256),
            sandbox_identity=self.capture._bundle().environment.compose_project,
            ownership_digest=authority.ownership_scope_sha256,
            ownership_status=OwnershipStatus.PROVEN,
            target_logical_service=target,
            service_runtime_state=runtime_state,
            configuration_state_digest=configuration_digest,
            baseline_digest=semantic_sha256(self.capture._baseline()),
            active_transaction_count=0,
            prior_forward_step_count=0,
            preconditions=tuple(
                PreconditionObservation(precondition=item, satisfied=satisfied[item])
                for item in preconditions
            ),
            observed_at_start=started_at,
            observed_at_end=ended_at,
            observation_monotonic_duration_ms=(time.monotonic_ns() - monotonic_start)
            // 1_000_000,
        )
        self._admitted_execution_state_digest = self._require_execution_snapshot(
            snapshot
        )
        return snapshot

    def _trusted_state_observation(
        self,
    ) -> tuple[str, LocalSandboxReadBackend, str, dict[str, RuntimeRecord]]:
        backend = self._refresh_backend()
        try:
            flag_digest = self._require_flags().verify(self.capture._baseline())
        except Exception:
            if self._active_fault_document is None:
                raise
            flag_digest = self._require_flags().verify(self._active_fault_document)
        runtimes = {
            service: self._runtime(service)
            for service in ("email", "payment", "recommendation")
        }
        digest = semantic_sha256(
            {
                "authority_sha256": backend.authority.authority_sha256,
                "flag_digest": flag_digest,
                "runtimes": tuple(
                    runtimes[service].model_dump(mode="json")
                    for service in ("email", "payment", "recommendation")
                ),
            }
        )
        return digest, backend, flag_digest, runtimes

    def _trusted_state_digest(self) -> str:
        return self._trusted_state_observation()[0]

    @staticmethod
    def _service_runtime_state(runtime: RuntimeRecord) -> ServiceRuntimeState:
        if runtime.state is RuntimeState.EXITED:
            return ServiceRuntimeState.STOPPED
        if runtime.state is RuntimeState.RUNNING and runtime.health in {
            HealthState.HEALTHY,
            HealthState.NOT_CONFIGURED,
        }:
            return ServiceRuntimeState.RUNNING_HEALTHY
        return ServiceRuntimeState.RUNNING_UNHEALTHY

    def _require_baseline_mutation_state(self) -> str:
        digest, _, flag_digest, runtimes = self._trusted_state_observation()
        baseline_digest = self._require_flags().verify(self.capture._baseline())
        if flag_digest != baseline_digest or any(
            not runtime.owned_container_present
            or self._service_runtime_state(runtime)
            is not ServiceRuntimeState.RUNNING_HEALTHY
            for runtime in runtimes.values()
        ):
            raise RuntimeError("actual baseline config or runtime preconditions differ")
        return digest

    def _require_execution_snapshot(self, snapshot: CurrentStateSnapshot) -> str:
        digest, backend, flag_digest, runtimes = self._trusted_state_observation()
        authority = backend.authority
        runtime = runtimes[snapshot.target_logical_service]
        runtime_state = self._service_runtime_state(runtime)
        baseline_digest = semantic_sha256(self.capture._baseline())
        drift_visible = flag_digest != baseline_digest
        satisfied = {
            Precondition.LOCAL_DOCKER_ONLY: True,
            Precondition.OWNED_SERVICE: runtime.owned_container_present,
            Precondition.SERVICE_NOT_HEALTHY: runtime_state
            is not ServiceRuntimeState.RUNNING_HEALTHY,
            Precondition.CONFIGURATION_DRIFT_VISIBLE: drift_visible,
            Precondition.BASELINE_HASH_BOUND: snapshot.baseline_digest
            == baseline_digest,
            Precondition.LEAK_FLAG_ACTIVE: (
                self.scenario.scenario is LiveScenario.EMAIL and drift_visible
            ),
        }
        if (
            authority.daemon_identity_sha256 != snapshot.daemon_identity
            or authority.docker_context_sha256 != snapshot.docker_context_identity
            or authority.ownership_scope_sha256 != snapshot.ownership_digest
            or not runtime.owned_container_present
            or runtime_state is not snapshot.service_runtime_state
            or flag_digest != snapshot.configuration_state_digest
            or snapshot.baseline_digest != baseline_digest
            or snapshot.active_transaction_count != 0
            or snapshot.prior_forward_step_count != 0
            or any(
                observation.satisfied is not satisfied[observation.precondition]
                for observation in snapshot.preconditions
            )
        ):
            raise RuntimeError(
                "actual config, runtime, or preconditions differ from admission"
            )
        return digest

    def _revalidate_before_write(
        self,
        current_state: CurrentStateSnapshot,
        authorization: AttemptAuthorizationRecord,
        observed_at: datetime,
    ) -> None:
        backend = self._refresh_backend()
        authority = backend.authority
        if (
            authorization.current_state_sha256 != current_state.snapshot_sha256
            or authorization.run_id != current_state.run_id
            or authorization.attempt_id != current_state.attempt_id
            or authority.daemon_identity_sha256 != current_state.daemon_identity
            or authority.docker_context_sha256
            != current_state.docker_context_identity
            or authority.ownership_scope_sha256 != current_state.ownership_digest
            or observed_at < authorization.issued_at
            or observed_at >= authorization.expires_at
        ):
            raise RuntimeError("fresh write authority differs from admission")

    def controls(self, current_state: CurrentStateSnapshot) -> OwnedLiveControls:
        if self._admitted_execution_state_digest is None:
            raise RuntimeError("owned controls lack an admitted live state")
        controls = OwnedLiveControls(
            current_state=current_state,
            flag_controller=self._require_flags(),
            baseline_flag_document=self.capture._baseline(),
            email_disabled_flag_document=self.capture._baseline(),
            recommendation_controller=self._require_recommendation(),
            email_controller=self._require_email(),
            state_digest=self._trusted_state_digest,
            admitted_state_digest=self._admitted_execution_state_digest,
            revalidate_before_write=lambda authorization, observed_at: (
                self._revalidate_before_write(
                    current_state, authorization, observed_at
                )
            ),
        )
        self._last_controls = controls
        return controls

    def capture_recovery_windows(
        self, forward_execution: ForwardExecution
    ) -> tuple[RecoveryWindow, RecoveryWindow]:
        forward = ForwardExecution.model_validate(
            forward_execution.model_dump(mode="python")
        )
        first, _ = self._capture_window(1, target=forward.target)
        second, _ = self._capture_window(2, target=forward.target)
        return first, second

    def email_leak_flag_off(self) -> bool | None:
        if self.scenario.scenario is not LiveScenario.EMAIL:
            return None
        try:
            self._require_flags().verify(self.capture._baseline())
        except Exception:
            return False
        return True

    @property
    def restoration_write_count(self) -> int:
        return self._restoration_write_count

    def _attempt_restoration_write(self, operation: Callable[[], object]) -> None:
        if self._restoration_write_count >= 2:
            raise RuntimeError("baseline restoration write cap reached")
        self._refresh_backend()
        self._restoration_write_count += 1
        operation()

    def restore_baseline(self, baseline: BaselineEvidence | None) -> bool:
        if baseline is not None:
            BaselineEvidence.model_validate(baseline.model_dump(mode="python"))
        if not self._fault_attempted:
            flag_path = self.capture._flag_file()
            if flag_path.is_symlink() or not flag_path.is_file():
                return False
            if json.loads(flag_path.read_text(encoding="utf-8")) != self.capture._baseline():
                return False
            self.capture._environment().verify_owned_resources(require_complete=False)
            return True
        self._refresh_backend()
        forward_attempts = (
            0 if self._last_controls is None else self._last_controls.forward_write_count
        )
        try:
            self._require_flags().verify(self.capture._baseline())
        except Exception:
            if self.scenario.scenario is LiveScenario.EMAIL and forward_attempts >= 2:
                return False
            self._attempt_restoration_write(
                lambda: self._require_flags().apply(self.capture._baseline())
            )
            self._require_flags().verify(self.capture._baseline())
        recommendation = self._runtime("recommendation")
        if recommendation.state is not RuntimeState.RUNNING:
            if self.scenario.scenario is LiveScenario.EMAIL and forward_attempts >= 2:
                return False
            self._attempt_restoration_write(self._require_recommendation().start)
        email = self._runtime("email")
        if (
            email.state is not RuntimeState.RUNNING
            or email.health not in {HealthState.HEALTHY, HealthState.NOT_CONFIGURED}
        ):
            if self.scenario.scenario is LiveScenario.EMAIL and forward_attempts >= 2:
                return False
            self._attempt_restoration_write(self._require_email().restart)
        self._refresh_backend()
        for service in ("email", "payment", "recommendation"):
            runtime = self._runtime(service)
            if runtime.state is not RuntimeState.RUNNING or runtime.health not in {
                HealthState.HEALTHY,
                HealthState.NOT_CONFIGURED,
            }:
                return False
        return all(self.capture._environment().service_health().values()) and bool(
            self.capture._environment().verify_owned_resources(require_complete=True)
        )

    def revalidate_before_cleanup(self) -> None:
        environment = self.capture._environment()
        docker = environment.verify_local_docker()
        environment.verify_upstream()
        resolved, _ = environment.resolve()
        if resolved.compose_sha256 != self.freeze.resolved_compose_sha256:
            raise RuntimeError("cleanup resolved Compose drifted")
        environment.verify_owned_resources(require_complete=False)
        if self._owned_authority_fingerprint is not None:
            if semantic_sha256({"daemon_identity": docker["daemon_id"]}) != (
                self._owned_authority_fingerprint[0]
            ) or semantic_sha256({"docker_context": docker["context"]}) != (
                self._owned_authority_fingerprint[1]
            ):
                raise RuntimeError("cleanup daemon authority drifted")

    def cleanup_owned(self, *, baseline_restored: bool) -> CleanupObservation:
        self.revalidate_before_cleanup()
        cleanup = self.capture._environment().cleanup(
            baseline_restored=baseline_restored
        )
        _restore_private_flag_mode(self.capture._flag_file())
        return CleanupObservation(
            verdict=cleanup.verdict,
            owned_containers=cleanup.owned_containers,
            owned_networks=cleanup.owned_networks,
            owned_volumes=cleanup.owned_volumes,
            non_owned_resources_changed=cleanup.non_owned_resources_changed,
        )


class OwnedLiveCampaign:
    """One exact four-slot owned schedule with create-once pre-effect claims."""

    def __init__(
        self,
        *,
        repository_root: Path,
        private_root: Path,
        campaign_id: str,
        provider_env_path: Path,
        config: LiveDemoConfig,
        registry: RunbookRegistry,
        master_authorization: MasterAuthorizationRecord,
        stabilization_seconds: int = 90,
        utc_now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self.repository_root = Path(repository_root).resolve()
        self.private_root = Path(private_root).resolve()
        if (
            self.repository_root == Path("/")
            or self.private_root == Path("/")
            or self.private_root.is_relative_to(self.repository_root)
        ):
            raise ValueError("owned campaign roots are unsafe")
        if (
            not isinstance(campaign_id, str)
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", campaign_id)
            is None
        ):
            raise ValueError("owned campaign identity is unsafe")
        self.campaign_id = campaign_id
        self.provider_env_path = Path(provider_env_path)
        self.config = LiveDemoConfig.model_validate(config.model_dump(mode="python"))
        self.registry = RunbookRegistry.model_validate(registry.model_dump(mode="python"))
        self.master = MasterAuthorizationRecord.model_validate(
            master_authorization.model_dump(mode="python")
        )
        if self.master.registry_sha256 != self.registry.registry_sha256:
            raise ValueError("campaign Master Authorization differs from Registry")
        if not 90 <= stabilization_seconds <= 300:
            raise ValueError("campaign stabilization is outside the frozen bound")
        self.stabilization_seconds = stabilization_seconds
        self.utc_now = utc_now
        ensure_private_directory(self.private_root)
        self.campaign_root = self.private_root / "campaigns" / campaign_id

    def _existing_claims(self) -> tuple[Path, ...]:
        claim_root = self.campaign_root / "claims"
        if not claim_root.exists():
            return ()
        if claim_root.is_symlink() or not claim_root.is_dir():
            raise ValueError("campaign claims root is unsafe")
        return tuple(sorted(claim_root.glob("*.json")))

    def _claim_next(self) -> LiveCampaignAttemptClaim:
        _, change_sha256 = _clean_semantic_manifest(
            self.repository_root,
            config=self.config,
        )
        campaigns_root = self.private_root / "campaigns"
        if campaigns_root.exists() and (
            campaigns_root.is_symlink() or not campaigns_root.is_dir()
        ):
            raise ValueError("prior campaigns root is unsafe")
        for other in sorted(campaigns_root.glob("*/change.json")):
            if other.parent == self.campaign_root:
                continue
            if other.parent.is_symlink() or other.is_symlink() or not other.is_file():
                raise ValueError("prior campaign change record is unsafe")
            prior = json.loads(other.read_text(encoding="utf-8"))
            if (
                not isinstance(prior, dict)
                or prior.get("schema_version") != "dta-v2.live-campaign-change.v1"
                or not isinstance(prior.get("change_sha256"), str)
            ):
                raise ValueError("prior campaign change record is invalid")
            if prior["change_sha256"] == change_sha256:
                raise ValueError("identical implementation already has a live campaign")
        write_private_json(
            self.campaign_root / "change.json",
            {
                "schema_version": "dta-v2.live-campaign-change.v1",
                "campaign_id": self.campaign_id,
                "change_sha256": change_sha256,
            },
            create_once=True,
        )
        existing = self._existing_claims()
        ordinal = len(existing) + 1
        if ordinal > 4:
            raise ValueError("owned live campaign schedule is already exhausted")
        if existing:
            prior_claim = LiveCampaignAttemptClaim.model_validate_json(
                existing[-1].read_text(encoding="utf-8")
            )
            prior_closure_path = (
                self.campaign_root
                / "closures"
                / f"{prior_claim.ordinal:02d}-{prior_claim.scenario.value}.json"
            )
            if prior_closure_path.is_symlink() or not prior_closure_path.is_file():
                raise ValueError("prior claimed attempt has no retained closure")
            prior_closure = LiveAttemptClosure.model_validate_json(
                prior_closure_path.read_text(encoding="utf-8")
            )
            if (
                prior_closure.mode is not LiveAttemptMode.OWNED_LOCAL
                or prior_closure.attempt_id != prior_claim.attempt_id
                or prior_closure.run_id != prior_claim.run_id
                or prior_closure.scenario is not prior_claim.scenario
            ):
                raise ValueError("prior closure lineage differs from the owned claim")
            if (
                prior_closure.baseline_restored is not True
                or prior_closure.cleanup_terminal is not CleanupTerminal.CLEAN
            ):
                raise ValueError("prior attempt is not safe for the next schedule slot")
        claim = build_live_campaign_attempt_claim(
            campaign_id=self.campaign_id,
            ordinal=ordinal,  # type: ignore[arg-type]
            change_sha256=change_sha256,
        )
        write_private_json(
            self.campaign_root
            / "claims"
            / f"{claim.ordinal:02d}-{claim.scenario.value}.json",
            claim,
            create_once=True,
        )
        return claim

    def run_next(self) -> LiveAttemptClosure:
        """Claim, authenticate, and run exactly the next owned schedule slot."""

        claim = self._claim_next()
        scenario = next(
            item for item in self.config.scenarios if item.scenario is claim.scenario
        )
        attempt_root = self.campaign_root / "attempts" / claim.attempt_id
        preflight = prepare_owned_live_preflight(
            repository_root=self.repository_root,
            private_root=attempt_root / "sandbox",
            config=self.config,
            scenario=scenario,
            stabilization_seconds=self.stabilization_seconds,
        )
        freeze = build_repository_pre_live_freeze(
            preflight=preflight,
            config=self.config,
            registry=self.registry,
        )
        if freeze.semantic_manifest_sha256 != claim.change_sha256:
            raise ValueError("campaign claim differs from fresh pre-live freeze")
        lifecycle = OwnedSandboxLiveLifecycle(
            claim=claim,
            preflight=preflight,
            provider_env_path=self.provider_env_path,
            freeze=freeze,
            config=self.config,
            scenario=scenario,
            registry=self.registry,
            utc_now=self.utc_now,
            _campaign_token=_OWNED_CAMPAIGN_TOKEN,
        )
        grant = issue_owned_live_execution_grant(
            claim=claim,
            lifecycle=lifecycle,
            _token=_OWNED_CAMPAIGN_TOKEN,
        )
        provider_values = load_private_provider_env(self.provider_env_path)
        closure = run_live_attempt(
            private_root=attempt_root / "evidence",
            attempt_id=claim.attempt_id,
            config=self.config,
            scenario=scenario,
            freeze=freeze,
            registry=self.registry,
            master_authorization=self.master,
            lifecycle=lifecycle,
            as_of=self.utc_now(),
            utc_now=self.utc_now,
            forbidden_secrets=(provider_values["ECOMSRE_LLM_API_KEY"],),
            _owned_execution_grant=grant,
        )
        write_private_json(
            self.campaign_root
            / "closures"
            / f"{claim.ordinal:02d}-{claim.scenario.value}.json",
            closure,
            create_once=True,
        )
        return closure


def run_owned_live_campaign(campaign: OwnedLiveCampaign) -> tuple[LiveAttemptClosure, ...]:
    """Run the exact safe order once; never rerun a claimed schedule slot."""

    if type(campaign) is not OwnedLiveCampaign:
        raise TypeError("owned live campaign requires the exact production issuer")
    return tuple(campaign.run_next() for _ in range(4))


__all__ = [
    "OwnedLivePreflight",
    "OwnedLiveCampaign",
    "OwnedSandboxLiveLifecycle",
    "build_frozen_provider_config",
    "build_repository_pre_live_freeze",
    "prepare_owned_live_preflight",
    "run_owned_live_campaign",
]
