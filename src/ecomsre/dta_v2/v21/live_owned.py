"""Exact owned local-Sandbox adapter for the DTA v2.1 PR-F portfolio."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import http.client
import json
import math
from pathlib import Path
import secrets
import time
from typing import Any, Callable, cast
from urllib.parse import urlsplit

from pydantic_core import to_jsonable_python

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
    SpanStatus,
    TraceNeighborhoodRecord,
    build_inspect_service_runtime_request,
    build_query_metrics_request,
    build_trace_neighborhood_request,
)
from ecomsre.dta_v2.v21.agent import DtaAgentRunResultV21, run_evidence_guided_agent_v21
from ecomsre.dta_v2.v21.agent_contracts import AgentArmV21, build_alert_context_v21
from ecomsre.dta_v2.v21.agent_provider import OpenAICompatibleDtaAgentProviderV21
from ecomsre.dta_v2.v21.contracts import RunbookStepIdV21, semantic_sha256
from ecomsre.dta_v2.v21.live_contracts import (
    LiveAdBaselineWindowV21,
    LiveBaselineEvidenceV21,
    LiveBusinessBaselineWindowV21,
    LiveCurrentStateV21,
    LiveDemoConfigV21,
    LiveEnvironmentAdmissionV2,
    LiveFaultImpactEvidenceV21,
    LiveReadinessV2,
    LiveScenarioSpecV21,
    LiveScenarioV21,
    ServiceRecoveryWindowV21,
    build_service_recovery_window_v21,
)
from ecomsre.dta_v2.v21.live_execution import (
    FixedLiveControlsV21,
    LivePostWriteStateV21,
)
from ecomsre.dta_v2.v21.live_protocol import (
    AD_CPU_RESOURCE_QUERY_ID_V1,
    AdCpuBusinessGuardrailResult,
    AdCpuResourceRecoveryProtocolV1,
    AdCpuResourceWindow,
    build_ad_cpu_business_guardrail_result,
    build_ad_cpu_resource_window,
)
from ecomsre.dta_v2.v21.live_reconciliation import (
    ResolvedComposeIdentityV1,
    build_resolved_compose_identity_v1,
    verify_cross_context_compose_identity_v1,
)
from ecomsre.dta_v2.v21.owned_capture import (
    AD_CPU_FAULT_DELTA_PERCENT_MINIMUM_V21,
    AD_CPU_FAULT_TO_BASELINE_RATIO_MINIMUM_V21,
    AD_CPU_MEASURABLE_PERCENT_V21,
    AD_CPU_SAFETY_CAPACITY_RATIO_MAXIMUM_V21,
    OwnedCaptureLifecycleV21,
    build_capture_flag_document_v21,
)
from ecomsre.dta_v2.v21.registry import (
    RunbookRegistryV21,
    load_default_scenario_registries,
)
from ecomsre.model.gateway import OpenAICompatibleConfig


def _semantic(value: object) -> str:
    return semantic_sha256(to_jsonable_python(value))


def _decimal_text(value: float) -> str:
    if not math.isfinite(value) or value < 0:
        raise ValueError("live measurement must be finite and non-negative")
    return format(Decimal(str(value)), "f")


def _require_utc(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError("live owned timestamp must be UTC")


class OwnedLiveAttemptV21(FixedLiveControlsV21):
    """One slot, one owned Sandbox, one fault operation, and one fixed write."""

    def __init__(
        self,
        *,
        repository_root: Path,
        private_root: Path,
        accepted_private_prf_root: Path,
        attempt_id: str,
        config: LiveDemoConfigV21,
        scenario: LiveScenarioSpecV21,
        registry: RunbookRegistryV21,
        protocol: AdCpuResourceRecoveryProtocolV1,
        provider_env_path: Path,
        concurrency_guard: Callable[[], None],
        stabilization_seconds: int = 30,
    ) -> None:
        self.repository_root = Path(repository_root).resolve()
        self.private_root = Path(private_root).resolve()
        self.accepted_private_prf_root = Path(accepted_private_prf_root).resolve()
        self.attempt_id = attempt_id
        self.config = LiveDemoConfigV21.model_validate(config.model_dump(mode="python"))
        self.scenario = LiveScenarioSpecV21.model_validate(
            scenario.model_dump(mode="python")
        )
        self.registry = RunbookRegistryV21.model_validate(
            registry.model_dump(mode="python")
        )
        self.protocol = AdCpuResourceRecoveryProtocolV1.model_validate(
            protocol.model_dump(mode="python")
        )
        self.provider_env_path = Path(provider_env_path)
        self._concurrency_guard = concurrency_guard
        if self.scenario not in self.config.scenarios:
            raise ValueError("owned live scenario is outside the frozen config")
        if self.private_root.is_relative_to(self.repository_root):
            raise ValueError("private live evidence must remain outside the repository")
        self.capture = OwnedCaptureLifecycleV21(
            repository_root=self.repository_root,
            private_root=self.private_root,
            plan=cast(Any, self.config),
            stabilization_seconds=stabilization_seconds,
        )
        self.run_id = secrets.token_hex(16)
        self._backend_value: LocalSandboxReadBackend | None = None
        self._authority_fingerprint: tuple[str, ...] | None = None
        self._provider: OpenAICompatibleDtaAgentProviderV21 | None = None
        self._baseline_state_sha256: str | None = None
        self._baseline_service_error_rates: tuple[float, float] | None = None
        self._fault_started_at: datetime | None = None
        self._mitigation_started_at: datetime | None = None
        self._fault_operation_count = 0
        self._forward_step_count = 0
        self._fault_document: dict[str, object] | None = None
        self._target_identity: str | None = None
        self._unrelated_owned_drift_detected = False
        self._admitted_raw_compose: dict[str, object] | None = None
        self._admitted_compose_identity: ResolvedComposeIdentityV1 | None = None

    def _assert_exclusive(self) -> None:
        self._concurrency_guard()

    def assert_no_unrelated_owned_drift(self) -> None:
        if self._unrelated_owned_drift_detected:
            raise RuntimeError("unrelated owned service state changed")

    @property
    def provider(self) -> OpenAICompatibleDtaAgentProviderV21:
        if self._provider is None:
            raise RuntimeError("live Provider was not configured")
        return self._provider

    @property
    def mitigation_started_at(self) -> datetime:
        if self._mitigation_started_at is None:
            raise RuntimeError("live mitigation timestamp is unavailable")
        return self._mitigation_started_at

    @property
    def admitted_compose_identity(self) -> ResolvedComposeIdentityV1:
        if self._admitted_compose_identity is None:
            raise RuntimeError("live admitted Compose identity is unavailable")
        return self._admitted_compose_identity

    def admit_environment(self) -> None:
        self.capture.admit()
        admitted_path = self.private_root / "control/resolved-compose.json"
        raw_value = json.loads(admitted_path.read_text(encoding="utf-8"))
        if not isinstance(raw_value, dict):
            raise ValueError("admitted live Compose document is not an object")
        environment = self.capture._environment()
        self._admitted_raw_compose = raw_value
        self._admitted_compose_identity = build_resolved_compose_identity_v1(
            raw_value,
            expected_flagd_directory=environment.flagd_directory,
            accepted_private_prf_root=self.accepted_private_prf_root,
            repository_root=self.repository_root,
            raw_contract_verifier=environment._verify_resolved_contract,
        )
        values = load_private_provider_env(self.provider_env_path)
        provider_config = OpenAICompatibleConfig.from_environment(values)
        if (
            provider_config is None
            or provider_config.model != self.config.provider_model
        ):
            raise ValueError(
                "configured Provider model differs from the frozen live model"
            )
        provider = OpenAICompatibleDtaAgentProviderV21(
            arm=AgentArmV21.EVIDENCE_GUIDED_PLANNER,
            config=provider_config,
            timeout_seconds=90.0,
            max_completion_tokens=self.config.maximum_completion_tokens,
        )
        if provider.identity.identity_sha256 != self.config.planner_identity_sha256:
            raise ValueError(
                "runtime Provider identity differs from the frozen planner"
            )
        self._provider = provider

    def cleanup_not_started(self) -> dict[str, object]:
        """Prove the read-only admission path created no owned Docker resources."""

        self._assert_exclusive()
        counts = self.capture._environment().verify_owned_resources(
            require_complete=False
        )
        clean = not any(counts.values())
        return {
            "schema_version": "dta-v21.live-cleanup-terminal.v1",
            "disposition": "NOT_STARTED_NO_OWNED_RESOURCES",
            "baseline_restored": True,
            "owned_containers": counts["container"],
            "owned_networks": counts["network"],
            "owned_volumes": counts["volume"],
            "non_owned_resources_changed": False,
            "verdict": "CLEAN" if clean else "BLOCKED",
        }

    def start(self) -> None:
        self._assert_exclusive()
        self.capture.start()

    def wait_ready(self) -> None:
        self.capture.wait_ready()
        self._refresh_backend()
        self._verify_exact_baseline_read_only()
        self._require_non_owned_unchanged()

    def environment_admission(
        self,
        *,
        readiness: LiveReadinessV2,
        readiness_raw_compose: dict[str, object],
        readiness_identity: ResolvedComposeIdentityV1,
        readiness_flagd_directory: Path,
    ) -> LiveEnvironmentAdmissionV2:
        """Bind the live attempt to the exact preflight and owned read authority."""

        self._assert_exclusive()
        backend = self._refresh_backend()
        environment = self.capture._environment()
        _resolved, fresh_raw = environment.resolve()
        fresh_identity = build_resolved_compose_identity_v1(
            fresh_raw,
            expected_flagd_directory=environment.flagd_directory,
            accepted_private_prf_root=self.accepted_private_prf_root,
            repository_root=self.repository_root,
            raw_contract_verifier=environment._verify_resolved_contract,
        )
        admitted_identity = self._admitted_compose_identity
        admitted_raw = self._admitted_raw_compose
        if admitted_identity is None or admitted_raw is None:
            raise RuntimeError("live admitted Compose identity is unavailable")
        if fresh_identity != admitted_identity:
            raise RuntimeError("same-context live Compose identities differ")
        verify_cross_context_compose_identity_v1(
            first_raw=readiness_raw_compose,
            first_identity=readiness_identity,
            first_expected_flagd_directory=readiness_flagd_directory,
            second_raw=admitted_raw,
            second_identity=admitted_identity,
            second_expected_flagd_directory=environment.flagd_directory,
        )
        baseline_flag_sha256 = self.capture._flags().verify(self.capture._baseline())
        if (
            fresh_identity.raw_compose_sha256 != admitted_identity.raw_compose_sha256
            or fresh_identity.execution_compose_sha256
            != readiness.execution_compose_sha256
            or readiness.compose_identity_sha256 != readiness_identity.identity_sha256
            or baseline_flag_sha256 != readiness.baseline_flag_document_sha256
        ):
            raise RuntimeError("live environment differs from exact-head readiness")
        counts = environment.verify_owned_resources(require_complete=True)
        inventory = {
            kind: tuple(sorted(environment._owned_ids(kind)))
            for kind in ("container", "network", "volume")
        }
        baseline_snapshot = environment._baseline_snapshot
        if baseline_snapshot is None:
            raise RuntimeError("live environment lacks the pre-start Docker snapshot")
        non_owned_snapshot = {
            "containers": tuple(sorted(baseline_snapshot.containers)),
            "networks": tuple(sorted(baseline_snapshot.networks)),
            "volumes": tuple(sorted(baseline_snapshot.volumes)),
        }
        authority = backend.authority
        required = (
            authority.daemon_identity_sha256,
            authority.docker_context_sha256,
            authority.config_bundle_sha256,
            authority.resolved_sandbox_sha256,
        )
        if any(item is None for item in required):
            raise RuntimeError("live environment authority is incomplete")
        return LiveEnvironmentAdmissionV2.build(
            run_id=self.run_id,
            attempt_id=self.attempt_id,
            scenario=self.scenario.scenario,
            code_head=readiness.code_head,
            readiness_sha256=readiness.readiness_sha256,
            raw_compose_sha256=fresh_identity.raw_compose_sha256,
            execution_compose_sha256=fresh_identity.execution_compose_sha256,
            compose_identity_sha256=fresh_identity.identity_sha256,
            normalization_policy_id=fresh_identity.normalization_policy_id,
            baseline_flag_document_sha256=baseline_flag_sha256,
            docker_boundary="LOCAL_UNIX_DOCKER",
            daemon_identity_sha256=cast(str, authority.daemon_identity_sha256),
            docker_context_sha256=cast(str, authority.docker_context_sha256),
            config_bundle_sha256=cast(str, authority.config_bundle_sha256),
            resolved_sandbox_sha256=cast(str, authority.resolved_sandbox_sha256),
            resolved_endpoints_sha256=authority.resolved_endpoints_sha256,
            ownership_scope_sha256=authority.ownership_scope_sha256,
            read_authority_sha256=authority.authority_sha256,
            owned_inventory_sha256=_semantic(inventory),
            non_owned_baseline_snapshot_sha256=_semantic(non_owned_snapshot),
            owned_container_count=counts["container"],
            owned_network_count=counts["network"],
            owned_volume_count=counts["volume"],
            admitted_at=datetime.now(timezone.utc),
        )

    def _verify_exact_baseline_read_only(self) -> None:
        self.capture._flags().verify(self.capture._baseline())
        health = self.capture._environment().wait_healthy(timeout_seconds=120)
        if not all(health.values()):
            raise RuntimeError("live baseline health is incomplete")
        self.capture._environment().verify_owned_resources(require_complete=True)
        for service in ("ad", "email", "product-catalog", "recommendation"):
            runtime = self._runtime(service)
            if runtime.state is not RuntimeState.RUNNING or runtime.health not in {
                HealthState.HEALTHY,
                HealthState.NOT_CONFIGURED,
            }:
                raise RuntimeError("live baseline service state differs")

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
        raw = (
            authority.daemon_identity_sha256,
            authority.docker_context_sha256,
            authority.config_bundle_sha256,
            authority.resolved_sandbox_sha256,
            authority.resolved_endpoints_sha256,
            authority.ownership_scope_sha256,
        )
        if not all(isinstance(item, str) for item in raw):
            raise RuntimeError("fresh owned read authority is incomplete")
        fingerprint = tuple(cast(str, item) for item in raw)
        if self._authority_fingerprint is None:
            self._authority_fingerprint = fingerprint
        elif fingerprint != self._authority_fingerprint:
            raise RuntimeError("fresh owned read authority drifted")
        self._backend_value = backend
        return backend

    def _backend(self) -> LocalSandboxReadBackend:
        if self._backend_value is None:
            raise RuntimeError("live owned backend is unavailable")
        return self._backend_value

    def _require_non_owned_unchanged(self) -> None:
        environment = self.capture._environment()
        baseline = environment._baseline_snapshot
        if baseline is None:
            raise RuntimeError("live Sandbox lacks a pre-start resource snapshot")
        current = environment.snapshot_all_resources()
        for kind, baseline_ids, current_ids in (
            ("container", baseline.containers, current.containers),
            ("network", baseline.networks, current.networks),
            ("volume", baseline.volumes, current.volumes),
        ):
            owned = environment._owned_ids(kind)
            if current_ids - owned != baseline_ids or not owned.issubset(current_ids):
                raise RuntimeError("non-owned Docker resource state changed")

    def _probe_frontend(self) -> bool:
        resolved, _ = self.capture._environment().resolve()
        parsed = urlsplit(resolved.endpoints.frontend)
        if (
            parsed.scheme != "http"
            or parsed.hostname != "127.0.0.1"
            or parsed.port != 18080
            or parsed.path not in {"", "/"}
        ):
            raise RuntimeError("owned frontend probe origin drifted")
        connection = http.client.HTTPConnection(
            parsed.hostname, parsed.port, timeout=10
        )
        try:
            connection.request("GET", "/", headers={"Accept": "text/html"})
            response = connection.getresponse()
            body = response.read(1_000_001)
            return 200 <= response.status < 300 and len(body) <= 1_000_000
        finally:
            connection.close()

    def _runtime(self, service: str) -> RuntimeRecord:
        result = self._backend().execute(
            build_inspect_service_runtime_request(
                run_id=self.run_id, services=(service,), max_results=1
            )
        )
        if len(result.records) != 1 or type(result.records[0]) is not RuntimeRecord:
            raise RuntimeError("live runtime observation is incomplete")
        return result.records[0]

    def _metrics(
        self, *, service: str, started_at: datetime, ended_at: datetime
    ) -> dict[MetricKind, MetricRecord]:
        result = self._backend().execute(
            build_query_metrics_request(
                run_id=self.run_id,
                service=service,
                started_at=started_at,
                ended_at=ended_at,
                metric_kinds=(
                    MetricKind.ERROR_RATE,
                    MetricKind.LATENCY_P95_MS,
                    MetricKind.REQUEST_SUPPORT,
                ),
                max_results=3,
            )
        )
        records = {
            item.metric_kind: item
            for item in result.records
            if type(item) is MetricRecord
        }
        if set(records) != {
            MetricKind.ERROR_RATE,
            MetricKind.LATENCY_P95_MS,
            MetricKind.REQUEST_SUPPORT,
        }:
            raise RuntimeError("live business metric set is incomplete")
        if any(not math.isfinite(item.value) for item in records.values()):
            raise RuntimeError("live business metric is non-finite")
        return records

    def _traces(
        self, *, service: str, started_at: datetime, ended_at: datetime
    ) -> tuple[TraceNeighborhoodRecord, ...]:
        result = self._backend().execute(
            build_trace_neighborhood_request(
                run_id=self.run_id,
                service=service,
                started_at=started_at,
                ended_at=ended_at,
                max_spans=40,
            )
        )
        if any(type(item) is not TraceNeighborhoodRecord for item in result.records):
            raise RuntimeError("live trace observation is invalid")
        return tuple(cast(TraceNeighborhoodRecord, item) for item in result.records)

    def _target_identity_value(self, service: str) -> str:
        identity = self._backend().docker._owned_container_identity(service)
        if (
            identity is None
            or len(identity) != 64
            or any(character not in "0123456789abcdef" for character in identity)
        ):
            raise RuntimeError("live owned target identity is invalid")
        return identity

    def _state_digest(self) -> str:
        backend = self._refresh_backend()
        baseline_flag_sha = self.capture._flags().verify(self.capture._baseline())
        services: dict[str, object] = {}
        for service in ("ad", "email", "product-catalog", "recommendation"):
            runtime = backend.docker._runtime_for(service)
            services[service] = {
                "identity": self._target_identity_value(service),
                "runtime": runtime.model_dump(mode="json"),
            }
        self._require_non_owned_unchanged()
        return _semantic(
            {
                "schema_version": "dta-v21.live-baseline-state.v1",
                "flag_document_sha256": baseline_flag_sha,
                "services": services,
                "ownership_scope_sha256": backend.authority.ownership_scope_sha256,
                "non_owned_changes": 0,
            }
        )

    def capture_baseline(
        self, *, environment_admission: LiveEnvironmentAdmissionV2
    ) -> LiveBaselineEvidenceV21:
        started_at = datetime.now(timezone.utc)
        self._verify_exact_baseline_read_only()
        self._baseline_state_sha256 = self._state_digest()
        windows: list[LiveAdBaselineWindowV21 | LiveBusinessBaselineWindowV21] = []
        if self.scenario.scenario in {
            LiveScenarioV21.EMAIL_SERVICE_UNAVAILABLE,
            LiveScenarioV21.PRODUCT_CATALOG_SERVICE_UNAVAILABLE,
        }:
            target = cast(str, self.scenario.target_service)
            anchor = "checkout" if target == "email" else "frontend"
            errors: list[float] = []
            for ordinal in (1, 2):
                window_started = datetime.now(timezone.utc)
                time.sleep(self.config.service_recovery_window_seconds)
                window_ended = window_started + timedelta(
                    seconds=self.config.service_recovery_window_seconds
                )
                metrics = self._metrics(
                    service=anchor,
                    started_at=window_started,
                    ended_at=window_ended,
                )
                traces = self._traces(
                    service=anchor,
                    started_at=window_started,
                    ended_at=window_ended,
                )
                error_rate = metrics[MetricKind.ERROR_RATE].value
                request_support = metrics[MetricKind.REQUEST_SUPPORT].value
                if request_support <= 0 or any(
                    item.status is SpanStatus.ERROR and item.first_error_location
                    for item in traces
                ):
                    raise RuntimeError("service baseline business window did not pass")
                errors.append(error_rate)
                windows.append(
                    LiveBusinessBaselineWindowV21.build(
                        ordinal=ordinal,
                        window_started_at=window_started,
                        window_ended_at=window_ended,
                        business_anchor_service=anchor,
                        business_error_rate=_decimal_text(error_rate),
                        request_support=_decimal_text(request_support),
                        first_error_span_count=0,
                    )
                )
            self._baseline_service_error_rates = (errors[0], errors[1])
        elif self.scenario.scenario is LiveScenarioV21.AD_CPU_SATURATION:
            for ordinal in (1, 2):
                resource = self.capture._resource_record(
                    service="ad", window_seconds=10, sample_count=5
                )
                windows.append(
                    LiveAdBaselineWindowV21.build(
                        ordinal=ordinal,
                        cpu_p95_percent=_decimal_text(
                            max(item.cpu_percent for item in resource.samples)
                        ),
                        sample_count=len(resource.samples),
                    )
                )
        else:
            for ordinal in (1, 2):
                window_started = datetime.now(timezone.utc)
                time.sleep(self.config.service_recovery_window_seconds)
                window_ended = window_started + timedelta(
                    seconds=self.config.service_recovery_window_seconds
                )
                metrics = self._metrics(
                    service="payment",
                    started_at=window_started,
                    ended_at=window_ended,
                )
                if metrics[MetricKind.REQUEST_SUPPORT].value <= 0:
                    raise RuntimeError("no-fault baseline lacks request support")
                windows.append(
                    LiveBusinessBaselineWindowV21.build(
                        ordinal=ordinal,
                        window_started_at=window_started,
                        window_ended_at=window_ended,
                        business_anchor_service="payment",
                        business_error_rate=_decimal_text(
                            metrics[MetricKind.ERROR_RATE].value
                        ),
                        request_support=_decimal_text(
                            metrics[MetricKind.REQUEST_SUPPORT].value
                        ),
                        first_error_span_count=None,
                    )
                )
        if len(windows) != 2:
            raise RuntimeError("live baseline did not produce exactly two windows")
        return LiveBaselineEvidenceV21.build(
            run_id=self.run_id,
            attempt_id=self.attempt_id,
            scenario=self.scenario.scenario,
            environment_admission_sha256=(
                environment_admission.environment_admission_sha256
            ),
            started_at=started_at,
            baseline_state_sha256=cast(str, self._baseline_state_sha256),
            windows=(windows[0], windows[1]),
        )

    def inject_fault(self) -> None:
        self._assert_exclusive()
        if self._baseline_state_sha256 is None or self._fault_operation_count != 0:
            raise RuntimeError("live fault injection lacks exact baseline admission")
        self._refresh_backend()
        self._require_unrelated_owned_services_unchanged()
        self._verify_exact_baseline_read_only()
        self._require_non_owned_unchanged()
        self.capture.active_condition = self.attempt_id
        self.capture.case_started_at = datetime.now(timezone.utc)
        self._fault_started_at = self.capture.case_started_at
        scenario = self.scenario.scenario
        if scenario is LiveScenarioV21.NO_FAULT:
            return
        target = cast(str, self.scenario.target_service)
        self._target_identity = self._target_identity_value(target)
        if scenario is LiveScenarioV21.AD_CPU_SATURATION:
            document = build_capture_flag_document_v21(
                self.capture._upstream(), load_vus=25, ad_cpu_variant="on"
            )
            self.capture._flags().apply(document)
            self._fault_document = document
        else:
            self.capture._service(target).stop()
        self._fault_operation_count = 1

    def verify_fault_impact(
        self,
        *,
        environment_admission: LiveEnvironmentAdmissionV2,
        baseline: LiveBaselineEvidenceV21,
    ) -> LiveFaultImpactEvidenceV21:
        scenario = self.scenario.scenario
        if scenario is LiveScenarioV21.NO_FAULT:
            self._verify_exact_baseline_read_only()
            baseline_unchanged = self._state_digest() == self._baseline_state_sha256
            if not baseline_unchanged:
                raise RuntimeError("no-fault baseline changed")
            return LiveFaultImpactEvidenceV21.build(
                run_id=self.run_id,
                attempt_id=self.attempt_id,
                scenario=scenario,
                environment_admission_sha256=(
                    environment_admission.environment_admission_sha256
                ),
                baseline_evidence_sha256=baseline.evidence_sha256,
                baseline_state_sha256=baseline.baseline_state_sha256,
                fault_impact_kind="NO_FAULT",
                fault_operation_count=0,
                logical_service=None,
                business_anchor_service=None,
                baseline_unchanged=True,
                cpu_p95_percent=None,
                capacity_ratio=None,
                sample_count=None,
                safe=None,
                measurable=None,
                resource_fault_observed=None,
                business_impact_required=None,
                target_runtime_stopped=None,
                business_error_rate=None,
                first_error_span_count=None,
                business_impact_observed=None,
                same_owned_target_identity=None,
            )
        if self._fault_started_at is None or self._fault_operation_count != 1:
            raise RuntimeError("live fault-impact verification lacks one fault")
        target = cast(str, self.scenario.target_service)
        self._refresh_backend()
        self._require_unrelated_owned_services_unchanged()
        if scenario is LiveScenarioV21.AD_CPU_SATURATION:
            if self._fault_document is None:
                raise RuntimeError("Ad fault document is unavailable")
            self.capture._flags().verify(self._fault_document)
            time.sleep(10)
            resource = self.capture._resource_record(
                service="ad", window_seconds=10, sample_count=5
            )
            p95 = max(item.cpu_percent for item in resource.samples)
            capacity = self.capture._cpu_capacity_percent("ad")
            ratio = p95 / capacity
            baseline_cpu = float(self.protocol.baseline_cpu_p95_percent)
            measurable = (
                p95 >= AD_CPU_MEASURABLE_PERCENT_V21
                and p95 - baseline_cpu >= AD_CPU_FAULT_DELTA_PERCENT_MINIMUM_V21
                and p95
                >= max(baseline_cpu, 1.0) * AD_CPU_FAULT_TO_BASELINE_RATIO_MINIMUM_V21
            )
            safe = ratio <= AD_CPU_SAFETY_CAPACITY_RATIO_MAXIMUM_V21
            runtime = self._runtime("ad")
            if (
                len(resource.samples) != self.protocol.minimum_sample_count
                or not measurable
                or not safe
                or runtime.state is not RuntimeState.RUNNING
                or runtime.health
                not in {HealthState.HEALTHY, HealthState.NOT_CONFIGURED}
                or self._target_identity_value("ad") != self._target_identity
            ):
                raise RuntimeError("accepted Ad resource fault-impact predicate failed")
            return LiveFaultImpactEvidenceV21.build(
                run_id=self.run_id,
                attempt_id=self.attempt_id,
                scenario=scenario,
                environment_admission_sha256=(
                    environment_admission.environment_admission_sha256
                ),
                baseline_evidence_sha256=baseline.evidence_sha256,
                baseline_state_sha256=baseline.baseline_state_sha256,
                fault_impact_kind="RESOURCE_ONLY",
                fault_operation_count=1,
                logical_service="ad",
                business_anchor_service=None,
                baseline_unchanged=None,
                cpu_p95_percent=_decimal_text(p95),
                capacity_ratio=_decimal_text(ratio),
                sample_count=len(resource.samples),
                safe=safe,
                measurable=measurable,
                resource_fault_observed=True,
                business_impact_required=False,
                target_runtime_stopped=None,
                business_error_rate=None,
                first_error_span_count=None,
                business_impact_observed=None,
                same_owned_target_identity=True,
            )
        time.sleep(self.config.service_recovery_window_seconds)
        ended_at = self._fault_started_at + timedelta(
            seconds=self.config.service_recovery_window_seconds
        )
        anchor = "checkout" if target == "email" else "frontend"
        runtime = self._runtime(target)
        metrics = self._metrics(
            service=anchor,
            started_at=self._fault_started_at,
            ended_at=ended_at,
        )
        traces = self._traces(
            service=anchor,
            started_at=self._fault_started_at,
            ended_at=ended_at,
        )
        error_span_count = sum(
            item.status is SpanStatus.ERROR and item.first_error_location
            for item in traces
        )
        error_rate = metrics[MetricKind.ERROR_RATE].value
        business_impact = (
            error_span_count > 0
            if target == "product-catalog"
            else error_span_count > 0 or error_rate > 0.0
        )
        if (
            runtime.state is not RuntimeState.EXITED
            or not business_impact
            or self._target_identity_value(target) != self._target_identity
        ):
            raise RuntimeError("accepted service-unavailable fault predicate failed")
        return LiveFaultImpactEvidenceV21.build(
            run_id=self.run_id,
            attempt_id=self.attempt_id,
            scenario=scenario,
            environment_admission_sha256=(
                environment_admission.environment_admission_sha256
            ),
            baseline_evidence_sha256=baseline.evidence_sha256,
            baseline_state_sha256=baseline.baseline_state_sha256,
            fault_impact_kind="SERVICE_UNAVAILABLE",
            fault_operation_count=1,
            logical_service=target,
            business_anchor_service=anchor,
            baseline_unchanged=None,
            cpu_p95_percent=None,
            capacity_ratio=None,
            sample_count=None,
            safe=None,
            measurable=None,
            resource_fault_observed=None,
            business_impact_required=None,
            target_runtime_stopped=True,
            business_error_rate=_decimal_text(error_rate),
            first_error_span_count=error_span_count,
            business_impact_observed=True,
            same_owned_target_identity=True,
        )

    def run_agent(self) -> DtaAgentRunResultV21:
        self._refresh_backend()
        observer, _, _ = load_default_scenario_registries(self.repository_root)
        matches = tuple(
            item
            for item in observer.scenarios
            if item.scenario_id == self.scenario.scenario_id
        )
        if len(matches) != 1:
            raise RuntimeError("live observer scenario is not unique")
        ended_at = datetime.now(timezone.utc)
        started_at = self._fault_started_at or ended_at - timedelta(minutes=2)
        context = build_alert_context_v21(
            scenario=matches[0],
            run_id=self.run_id,
            started_at=started_at,
            ended_at=ended_at,
        )
        return run_evidence_guided_agent_v21(
            context=context,
            backend=self._backend(),
            registry=self.registry,
            provider=self.provider,
            compact_context=True,
        )

    def _current_mutation_payload(self) -> dict[str, object]:
        scenario = self.scenario.scenario
        target = cast(str, self.scenario.target_service)
        if scenario is LiveScenarioV21.AD_CPU_SATURATION:
            if self._fault_document is None:
                raise RuntimeError("Ad fault state is unavailable")
            flag_sha = self.capture._flags().verify(self._fault_document)
        else:
            flag_sha = self.capture._flags().verify(self.capture._baseline())
        runtime = self._runtime(target)
        return {
            "flag_document_sha256": flag_sha,
            "target_service": target,
            "target_identity": self._target_identity_value(target),
            "runtime": runtime.model_dump(mode="json"),
            "fault_operation_count": self._fault_operation_count,
            "forward_step_count": self._forward_step_count,
        }

    def _require_unrelated_owned_services_unchanged(self) -> None:
        target = self.scenario.target_service
        for service in ("ad", "email", "product-catalog", "recommendation"):
            if service == target:
                continue
            runtime = self._runtime(service)
            if runtime.state is not RuntimeState.RUNNING or runtime.health not in {
                HealthState.HEALTHY,
                HealthState.NOT_CONFIGURED,
            }:
                self._unrelated_owned_drift_detected = True
                raise RuntimeError("unrelated owned service state changed")

    def current_state(self) -> LiveCurrentStateV21:
        self._assert_exclusive()
        if self._fault_operation_count != 1 or self._forward_step_count != 0:
            raise RuntimeError("live current-state operation counts differ")
        started = datetime.now(timezone.utc)
        backend = self._refresh_backend()
        self._require_non_owned_unchanged()
        self._require_unrelated_owned_services_unchanged()
        target = cast(str, self.scenario.target_service)
        runtime = self._runtime(target)
        mutation_sha = _semantic(self._current_mutation_payload())
        authority = backend.authority
        observed = datetime.now(timezone.utc)
        assert authority.daemon_identity_sha256 is not None
        assert authority.docker_context_sha256 is not None
        assert authority.resolved_sandbox_sha256 is not None
        return LiveCurrentStateV21.build(
            run_id=self.run_id,
            attempt_id=self.attempt_id,
            scenario=self.scenario.scenario,
            target_service=target,
            owned_target_identity_sha256=_semantic(
                {"owned_target_identity": self._target_identity_value(target)}
            ),
            daemon_identity_sha256=authority.daemon_identity_sha256,
            docker_boundary="LOCAL_UNIX_DOCKER",
            docker_context_sha256=authority.docker_context_sha256,
            ownership_scope_sha256=authority.ownership_scope_sha256,
            sandbox_identity_sha256=authority.resolved_sandbox_sha256,
            baseline_state_sha256=cast(str, self._baseline_state_sha256),
            current_state_sha256=mutation_sha,
            ad_high_cpu_active=(
                self.scenario.scenario is LiveScenarioV21.AD_CPU_SATURATION
            ),
            target_runtime_stopped=runtime.state is RuntimeState.EXITED,
            fault_operation_count=self._fault_operation_count,
            prior_forward_step_count=self._forward_step_count,
            active_transaction_count=0,
            non_owned_changes=0,
            observation_started_at=started,
            observed_at=observed,
        )

    def revalidate(self) -> LiveCurrentStateV21:
        if self._forward_step_count != 0:
            raise RuntimeError("live forward write already started")
        return self.current_state()

    def disable_ad_high_cpu_flag(self) -> None:
        self._assert_exclusive()
        if self.scenario.scenario is not LiveScenarioV21.AD_CPU_SATURATION:
            raise ValueError("Ad fixed control used for a different scenario")
        if self._forward_step_count != 0:
            raise RuntimeError("Ad fixed control already attempted")
        self._forward_step_count += 1
        self.capture._flags().apply(self.capture._baseline())
        self._mitigation_started_at = datetime.now(timezone.utc)

    def start_owned_service(self, *, wait_for_health_seconds: int) -> None:
        self._assert_exclusive()
        if self.scenario.scenario not in {
            LiveScenarioV21.EMAIL_SERVICE_UNAVAILABLE,
            LiveScenarioV21.PRODUCT_CATALOG_SERVICE_UNAVAILABLE,
        }:
            raise ValueError("service fixed control used for a different scenario")
        if self._forward_step_count != 0:
            raise RuntimeError("service fixed control already attempted")
        if not 5 <= wait_for_health_seconds <= 120:
            raise ValueError("service health wait is outside the Runbook bound")
        self._forward_step_count += 1
        target = cast(str, self.scenario.target_service)
        controller = self.capture._service(target)
        identity = controller.retained_identity or controller._identity()
        if controller._identity() != identity:
            raise RuntimeError("owned service identity changed before start")
        controller._post(f"/containers/{identity}/start")
        deadline = time.monotonic() + wait_for_health_seconds
        while time.monotonic() < deadline:
            record = self._backend().docker._runtime_for(target)
            if record.state is RuntimeState.RUNNING and record.health in {
                HealthState.HEALTHY,
                HealthState.NOT_CONFIGURED,
            }:
                controller.retained_identity = None
                break
            time.sleep(1)
        else:
            raise RuntimeError("owned service transition timed out")
        self._mitigation_started_at = datetime.now(timezone.utc)

    def observe_postcondition(
        self, *, step: RunbookStepIdV21, observed_at: datetime
    ) -> LivePostWriteStateV21:
        self._assert_exclusive()
        _require_utc(observed_at)
        self._refresh_backend()
        self._require_non_owned_unchanged()
        self._require_unrelated_owned_services_unchanged()
        target = cast(str, self.scenario.target_service)
        runtime = self._runtime(target)
        ad_active = False
        if step is RunbookStepIdV21.DISABLE_AD_HIGH_CPU_FLAG:
            self.capture._flags().verify(self.capture._baseline())
        elif step is RunbookStepIdV21.START_OWNED_SERVICE:
            self.capture._flags().verify(self.capture._baseline())
        else:
            raise ValueError("postcondition step is outside fixed live controls")
        if self._target_identity_value(target) != self._target_identity:
            raise RuntimeError("owned target identity changed after remediation")
        return LivePostWriteStateV21.build(
            run_id=self.run_id,
            attempt_id=self.attempt_id,
            scenario=self.scenario.scenario,
            target_service=target,
            ad_high_cpu_active=ad_active,
            target_runtime_stopped=runtime.state is RuntimeState.EXITED,
            forward_step_count=1,
            non_owned_changes=0,
            observed_at=observed_at,
        )

    def _service_recovery_window(
        self, *, ordinal: int, started_at: datetime
    ) -> ServiceRecoveryWindowV21:
        target = cast(str, self.scenario.target_service)
        anchor = "checkout" if target == "email" else "frontend"
        if self._baseline_service_error_rates is None:
            raise RuntimeError("service recovery lacks baseline windows")
        time.sleep(self.config.service_recovery_window_seconds)
        ended_at = started_at + timedelta(
            seconds=self.config.service_recovery_window_seconds
        )
        metrics = self._metrics(
            service=anchor, started_at=started_at, ended_at=ended_at
        )
        traces = self._traces(service=anchor, started_at=started_at, ended_at=ended_at)
        runtime = self._runtime(target)
        self._require_unrelated_owned_services_unchanged()
        if self._target_identity_value(target) != self._target_identity:
            raise RuntimeError("owned service identity changed during recovery")
        baseline = max(
            Decimal(str(item)) for item in self._baseline_service_error_rates
        )
        threshold = max(baseline * Decimal("1.5"), baseline + Decimal("0.02"))
        errors = sum(
            item.status is SpanStatus.ERROR and item.first_error_location
            for item in traces
        )
        error_rate = Decimal(str(metrics[MetricKind.ERROR_RATE].value))
        request_support = Decimal(str(metrics[MetricKind.REQUEST_SUPPORT].value))
        impact = not (request_support > 0 and error_rate <= threshold and errors == 0)
        return build_service_recovery_window_v21(
            run_id=self.run_id,
            attempt_id=self.attempt_id,
            scenario=self.scenario.scenario,
            target_service=target,
            business_anchor_service=anchor,
            ordinal=ordinal,
            window_started_at=started_at,
            window_ended_at=ended_at,
            service_running=runtime.state is RuntimeState.RUNNING,
            service_health_passed=runtime.health
            in {HealthState.HEALTHY, HealthState.NOT_CONFIGURED},
            endpoint_reachable=self._probe_frontend(),
            baseline_business_error_rate=format(baseline, "f"),
            recovery_error_rate_threshold=format(threshold, "f"),
            business_error_rate=format(error_rate, "f"),
            request_support=format(request_support, "f"),
            first_error_span_count=errors,
            business_impact_observed=impact,
        )

    def capture_service_recovery_windows(
        self,
    ) -> tuple[ServiceRecoveryWindowV21, ServiceRecoveryWindowV21]:
        started = self.mitigation_started_at
        first = self._service_recovery_window(ordinal=1, started_at=started)
        second = self._service_recovery_window(
            ordinal=2, started_at=first.window_ended_at
        )
        return first, second

    def capture_ad_recovery_windows(
        self,
    ) -> tuple[
        tuple[AdCpuResourceWindow, AdCpuResourceWindow],
        tuple[AdCpuBusinessGuardrailResult, AdCpuBusinessGuardrailResult],
    ]:
        anchor = self.mitigation_started_at
        wait_seconds = max(
            0.0,
            (
                anchor
                + timedelta(seconds=self.config.ad_recovery_stabilization_seconds)
                - datetime.now(timezone.utc)
            ).total_seconds(),
        )
        if wait_seconds:
            time.sleep(wait_seconds)
        first_started = anchor + timedelta(
            seconds=self.config.ad_recovery_stabilization_seconds
        )
        resources: list[ResourceUsageRecord] = []
        for _ in (1, 2):
            resource = self.capture._resource_record(
                service="ad",
                window_seconds=self.config.ad_resource_window_seconds,
                sample_count=self.config.ad_resource_sample_count,
            )
            if len(resource.samples) != self.config.ad_resource_sample_count:
                raise RuntimeError("Ad recovery resource sample count differs")
            resources.append(resource)
        capacity = self.capture._cpu_capacity_percent("ad")
        windows: list[AdCpuResourceWindow] = []
        guardrails: list[AdCpuBusinessGuardrailResult] = []
        for index, resource in enumerate(resources):
            started = first_started + timedelta(
                seconds=self.config.ad_resource_window_seconds * index
            )
            ended = started + timedelta(seconds=self.config.ad_resource_window_seconds)
            business_started = ended - timedelta(
                seconds=self.config.ad_business_query_window_seconds
            )
            metrics = self._metrics(
                service="ad", started_at=business_started, ended_at=ended
            )
            latency = metrics[MetricKind.LATENCY_P95_MS]
            if latency.sample_count < 1:
                raise RuntimeError("Ad business guardrail lacks accepted samples")
            runtime = self._runtime("ad")
            self._require_unrelated_owned_services_unchanged()
            if self._target_identity_value("ad") != self._target_identity:
                raise RuntimeError("owned Ad identity changed during recovery")
            window = build_ad_cpu_resource_window(
                run_id=self.run_id,
                attempt_id=self.attempt_id,
                ordinal=index + 1,
                logical_service="ad",
                query_id=AD_CPU_RESOURCE_QUERY_ID_V1,
                unit="CPU_PERCENT",
                sample_count=len(resource.samples),
                window_started_at=started,
                window_ended_at=ended,
                post_mitigation_started_at=anchor,
                cpu_p95_percent=_decimal_text(
                    max(item.cpu_percent for item in resource.samples)
                ),
                capacity_ratio=_decimal_text(
                    max(item.cpu_percent for item in resource.samples) / capacity
                ),
                business_latency_p95_ms=_decimal_text(latency.value),
                business_query_id="DTA_V21_AD_BUSINESS_LATENCY_P95_V1",
                business_aggregation="HISTOGRAM_QUANTILE_P95",
                business_query_window_seconds=(
                    self.config.ad_business_query_window_seconds
                ),
                business_query_started_at=business_started,
                business_query_ended_at=ended,
                service_health_passed=runtime.state is RuntimeState.RUNNING
                and runtime.health in {HealthState.HEALTHY, HealthState.NOT_CONFIGURED},
                endpoint_reachable=self._probe_frontend(),
                business_guardrail_binding_sha256=(
                    self.protocol.business_guardrail_binding_sha256
                ),
            )
            windows.append(window)
            guardrails.append(
                build_ad_cpu_business_guardrail_result(
                    protocol=self.protocol, window=window
                )
            )
        return (
            cast(tuple[AdCpuResourceWindow, AdCpuResourceWindow], tuple(windows)),
            cast(
                tuple[AdCpuBusinessGuardrailResult, AdCpuBusinessGuardrailResult],
                tuple(guardrails),
            ),
        )

    def restore_baseline_idempotently(self) -> bool:
        try:
            self._assert_exclusive()
            if self.scenario.scenario is LiveScenarioV21.NO_FAULT:
                self._verify_exact_baseline_read_only()
                self._require_non_owned_unchanged()
                restored = self._state_digest() == self._baseline_state_sha256
                if restored:
                    self.capture.active_condition = None
                    self.capture.case_started_at = None
                return restored
            target = cast(str, self.scenario.target_service)
            expects_baseline = self._forward_step_count == 1
            try:
                self.capture._flags().verify(self.capture._baseline())
            except Exception:
                if expects_baseline:
                    self._unrelated_owned_drift_detected = True
                self.capture._flags().apply(self.capture._baseline())
            for service in ("email", "product-catalog", "recommendation"):
                runtime = self._runtime(service)
                expected_running = expects_baseline or service != target
                if expected_running and (
                    runtime.state is not RuntimeState.RUNNING
                    or runtime.health
                    not in {HealthState.HEALTHY, HealthState.NOT_CONFIGURED}
                ):
                    self._unrelated_owned_drift_detected = True
                self.capture._service(service).ensure_running()
            self.capture.verify_baseline()
            self._require_non_owned_unchanged()
            restored = self._state_digest() == self._baseline_state_sha256
            if restored:
                self.capture.active_condition = None
                self.capture.case_started_at = None
                self.capture.fault_operation_count = 0
            return restored
        except Exception:
            return False

    def cleanup(self, *, baseline_restored: bool) -> dict[str, object]:
        self._assert_exclusive()
        return self.capture.cleanup(baseline_restored=baseline_restored)


__all__ = ("OwnedLiveAttemptV21",)
