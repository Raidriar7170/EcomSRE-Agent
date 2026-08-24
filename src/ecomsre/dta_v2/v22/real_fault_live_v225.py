"""Capture-only successor over the owned v2.1 Ad CPU lifecycle."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import cast

from ecomsre.dta_v2.read_tools import ReadBackend
from ecomsre.dta_v2.v21.contracts import semantic_sha256
from ecomsre.dta_v2.v21.live_contracts import (
    LiveBaselineEvidenceV21,
    LiveEnvironmentAdmissionV2,
    LiveFaultImpactEvidenceV21,
)
from ecomsre.dta_v2.v21.live_owned import OwnedLiveAttemptV21
from ecomsre.dta_v2.v22.action_catalog import (
    StaticTopologyV22,
    build_action_catalog_v22,
    build_default_tool_capability_registry_v22,
)
from ecomsre.dta_v2.v22.contrastive_actions_v225 import (
    contrastive_resource_action_if_eligible_v225,
)
from ecomsre.dta_v2.v22.read_contracts import (
    EvidenceSourceV22,
    LogRecordV22,
    MetricFactV22,
    ReadSourceStatusV22,
    ResourceUsageRecordV22,
    RuntimeRecordV22,
    TraceSpanV22,
)
from ecomsre.dta_v2.v22.real_fault_action_backend_v225 import (
    RealFaultActionReadBackendV225,
)
from ecomsre.dta_v2.v22.real_fault_capture_v225 import (
    RealFaultCaseKind,
    RealFaultPhysicalCaptureV1,
    build_physical_capture_v225,
    build_source_window_v225,
)
from ecomsre.dta_v2.v22.replay import ReplayCaptureV22, ReplaySourceFailureV22
from ecomsre.dta_v2.v22.replay_target_coverage_v225 import (
    build_replay_target_coverage_v225,
)


def capture_real_fault_physical_state_v225(
    *,
    backend: ReadBackend,
    run_id: str,
    campaign_id: str,
    kind: RealFaultCaseKind,
    comparator_service: str,
) -> RealFaultPhysicalCaptureV1:
    """Capture exactly one bounded five-source state for Ad and its comparator."""

    services = tuple(sorted(("ad", comparator_service)))
    captured_at = datetime.now(timezone.utc)
    source_window = build_source_window_v225(captured_at=captured_at)
    adapter = RealFaultActionReadBackendV225(
        backend=backend,
        run_id=run_id,
        source_window=source_window,
        alias_to_backend_service={item: item for item in services},
    )
    topology = StaticTopologyV22.build(services=services, edges=())
    catalog = build_action_catalog_v22(
        candidate_services=services,
        topology=topology,
        capability_registry=build_default_tool_capability_registry_v22(),
        executed_action_ids=(),
        remaining_budget=20.0,
    )
    runtime = next(
        item
        for item in catalog.registry_actions
        if item.source is EvidenceSourceV22.RUNTIME
        and item.target_services == services
    )
    per_target = tuple(
        item
        for item in catalog.registry_actions
        if len(item.target_services) == 1
        and item.source
        in {
            EvidenceSourceV22.METRICS,
            EvidenceSourceV22.LOGS,
            EvidenceSourceV22.TRACES,
        }
    )
    resource_coverage = build_replay_target_coverage_v225(
        source=EvidenceSourceV22.RESOURCES,
        candidate_services=services,
        covered_target_services=services,
    )
    resource_bundle = contrastive_resource_action_if_eligible_v225(
        coverage=resource_coverage,
        resources_enabled=True,
        unresolved_resource_hypotheses=len(services),
        remaining_budget=3.0,
        bundle_mode=True,
    )
    if resource_bundle is None:
        raise RuntimeError("real-fault capture Resources bundle is not eligible")
    outcomes = tuple(adapter.execute(item) for item in (runtime, *per_target)) + (
        adapter.execute(resource_bundle),
    )
    failed = {
        item.source: item.status
        for item in outcomes
        if item.status
        in {
            ReadSourceStatusV22.FAILURE_UNAVAILABLE,
            ReadSourceStatusV22.FAILURE_TIMEOUT,
            ReadSourceStatusV22.FAILURE_SCHEMA,
        }
    }
    if EvidenceSourceV22.RUNTIME in failed or EvidenceSourceV22.RESOURCES in failed:
        raise RuntimeError("real-fault target-complete source capture failed")

    def records(source: EvidenceSourceV22) -> tuple[object, ...]:
        if source in failed:
            return ()
        return tuple(
            record
            for outcome in outcomes
            if outcome.source is source
            for record in outcome.records
        )

    capture = ReplayCaptureV22(
        schema_version="dta-v22.replay-capture.v1",
        captured_at=captured_at,
        metrics=cast(tuple[MetricFactV22, ...], records(EvidenceSourceV22.METRICS)),
        logs=cast(tuple[LogRecordV22, ...], records(EvidenceSourceV22.LOGS)),
        traces=cast(tuple[TraceSpanV22, ...], records(EvidenceSourceV22.TRACES)),
        runtime=cast(tuple[RuntimeRecordV22, ...], records(EvidenceSourceV22.RUNTIME)),
        resources=cast(
            tuple[ResourceUsageRecordV22, ...], records(EvidenceSourceV22.RESOURCES)
        ),
        changes=(),
        source_failures=tuple(
            ReplaySourceFailureV22(
                schema_version="dta-v22.replay-source-failure.v1",
                source=source,
                status=failed[source],
            )
            for source in EvidenceSourceV22
            if source in failed
        ),
    )
    return build_physical_capture_v225(
        campaign_id=campaign_id,
        kind=kind,
        fault_service="ad",
        comparator_service=comparator_service,
        source_window=source_window,
        capture=capture,
    )


class RealFaultShadowLifecycleV1:
    """Expose only capture, fault verification, restoration, and cleanup."""

    def __init__(self, attempt: OwnedLiveAttemptV21) -> None:
        self._attempt = attempt
        self._start_requested = False
        self._environment_admission: LiveEnvironmentAdmissionV2 | None = None
        self._baseline: LiveBaselineEvidenceV21 | None = None
        self._fault_impact: LiveFaultImpactEvidenceV21 | None = None

    def admit_start_and_wait(self) -> None:
        self._attempt.admit_environment()
        self._start_requested = True
        self._attempt.start()
        self._attempt.wait_ready()

    @property
    def run_id(self) -> str:
        return self._attempt.run_id

    def _task_admission(
        self, *, code_head: str, preflight_sha256: str
    ) -> LiveEnvironmentAdmissionV2:
        backend = self._attempt._refresh_backend()
        environment = self._attempt.capture._environment()
        identity = self._attempt.admitted_compose_identity
        counts = environment.verify_owned_resources(require_complete=True)
        inventory = {
            kind: tuple(sorted(environment._owned_ids(kind)))
            for kind in ("container", "network", "volume")
        }
        baseline_snapshot = environment._baseline_snapshot
        if baseline_snapshot is None:
            raise RuntimeError("real-fault live admission lacks a pre-start snapshot")
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
            raise RuntimeError("real-fault live read authority is incomplete")
        baseline_flag_sha256 = self._attempt.capture._flags().verify(
            self._attempt.capture._baseline()
        )
        return LiveEnvironmentAdmissionV2.build(
            run_id=self._attempt.run_id,
            attempt_id=self._attempt.attempt_id,
            scenario=self._attempt.scenario.scenario,
            code_head=code_head,
            readiness_sha256=preflight_sha256,
            raw_compose_sha256=identity.raw_compose_sha256,
            execution_compose_sha256=identity.execution_compose_sha256,
            compose_identity_sha256=identity.identity_sha256,
            normalization_policy_id=identity.normalization_policy_id,
            baseline_flag_document_sha256=baseline_flag_sha256,
            docker_boundary="LOCAL_UNIX_DOCKER",
            daemon_identity_sha256=cast(str, authority.daemon_identity_sha256),
            docker_context_sha256=cast(str, authority.docker_context_sha256),
            config_bundle_sha256=cast(str, authority.config_bundle_sha256),
            resolved_sandbox_sha256=cast(str, authority.resolved_sandbox_sha256),
            resolved_endpoints_sha256=authority.resolved_endpoints_sha256,
            ownership_scope_sha256=authority.ownership_scope_sha256,
            read_authority_sha256=authority.authority_sha256,
            owned_inventory_sha256=semantic_sha256(inventory),
            non_owned_baseline_snapshot_sha256=semantic_sha256(non_owned_snapshot),
            owned_container_count=counts["container"],
            owned_network_count=counts["network"],
            owned_volume_count=counts["volume"],
            admitted_at=datetime.now(timezone.utc),
        )

    def capture_and_prove_baseline(
        self, *, code_head: str, preflight_sha256: str
    ) -> LiveBaselineEvidenceV21:
        self._environment_admission = self._task_admission(
            code_head=code_head, preflight_sha256=preflight_sha256
        )
        self._baseline = self._attempt.capture_baseline(
            environment_admission=self._environment_admission
        )
        return self._baseline

    def capture_state(
        self,
        *,
        campaign_id: str,
        kind: RealFaultCaseKind,
        comparator_service: str,
    ) -> RealFaultPhysicalCaptureV1:
        return capture_real_fault_physical_state_v225(
            backend=self._attempt._refresh_backend(),
            run_id=self._attempt.run_id,
            campaign_id=campaign_id,
            kind=kind,
            comparator_service=comparator_service,
        )

    def inject_and_verify_fault(self) -> LiveFaultImpactEvidenceV21:
        if self._environment_admission is None or self._baseline is None:
            raise RuntimeError("real-fault injection lacks baseline proof")
        self._attempt.inject_fault()
        self._fault_impact = self._attempt.verify_fault_impact(
            environment_admission=self._environment_admission,
            baseline=self._baseline,
        )
        return self._fault_impact

    def revalidate_before_fault(self) -> None:
        """Re-prove the exact baseline and non-owned snapshot before mutation."""

        self._attempt._verify_exact_baseline_read_only()
        self._attempt._require_non_owned_unchanged()

    def live_backend(self) -> ReadBackend:
        return self._attempt._refresh_backend()

    def restore_and_cleanup(self) -> tuple[bool, dict[str, object]]:
        if not self._start_requested:
            cleanup = self._attempt.cleanup_not_started()
            return True, cleanup
        restored = self._attempt.restore_baseline_idempotently()
        if restored:
            self._attempt.assert_no_unrelated_owned_drift()
        cleanup = self._attempt.cleanup(baseline_restored=restored)
        return restored, cleanup


__all__ = (
    "RealFaultShadowLifecycleV1",
    "capture_real_fault_physical_state_v225",
)
