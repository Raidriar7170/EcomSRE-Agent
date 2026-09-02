#!/usr/bin/env python3
"""Run the Product v0.2.4 fresh Baseline and final No-Fault check."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime, timedelta
import json
import math
from pathlib import Path
import secrets
from typing import Any, Mapping, Sequence

import httpx

from ecomsre.dta_v2.contracts import semantic_sha256
from ecomsre.dta_v2.read_only_smoke import (
    CleanupObservation,
    _SandboxOwnedSmokeLifecycle,
)
from ecomsre.dta_v2.telemetry_adapters import LocalSandboxReadBackend
from ecomsre.dta_v2.v22.read_contracts import (
    EvidenceSourceV22,
    MetricFactV22,
    MetricKindV22,
    ReadSourceStatusV22,
    ResourceUsageRecordV22,
    TraceSpanV22,
)
from ecomsre.product.connectors.base import (
    ConnectorQueryResultV1,
    ConnectorWindowV1,
)
from ecomsre.product.connectors.opensearch_profile_binding_v023 import (
    ACTIVE_PROFILE_SHA256_V023,
    build_product_v023_environment_payload,
)
from ecomsre.product.connectors.pilot_runtime import write_pilot_runtime_snapshot_v02
from ecomsre.product.contracts import ServiceIdentityMapV1
from ecomsre.product.environment.capabilities import EnvironmentCapabilityMatrixV1
from ecomsre.product.incidents.contracts import (
    DiagnosisResultV1,
    DiagnosisTerminalV1,
    EvidenceBundleV1,
    IncidentRecordV1,
)
from ecomsre.product.jobs.contracts import ProductJobStatusV1
from ecomsre.product.pilot.baseline_readiness_v021 import (
    BoundedHealthyCheckoutTrafficV021,
    HealthyTrafficProfileV021,
    verify_queue_default_v021,
)
from ecomsre.product.pilot.baseline_readiness_v023 import (
    ProductBaselineReadinessAuditV023,
    ProductBaselineReadinessProfileV023,
)
from ecomsre.product.pilot.baseline_restart_v023 import BaselineRestartProofV023
from ecomsre.product.pilot.live_baseline_readiness_v023 import (
    _ProductHostProcessesV023,
    _baseline_candidate_identity_sha256_v023,
    _queue_counts,
    _request_json,
    _restart_snapshot,
    _sleep_until_utc,
    _wait_job,
    planned_baseline_windows_v023,
)
from ecomsre.product.pilot.live_calibration_v02 import (
    _authority_inputs,
)
from ecomsre.product.pilot.live_nofault_acceptance_v023 import (
    _database_counts,
    _limitation_evidence_refs,
    _rotate_runtime_snapshot,
    _runtime_snapshot,
    _successful_runtime_ref,
)
from ecomsre.product.pilot.nofault_acceptance_v023 import (
    NOFAULT_FULLY_SUPPORTED_V023,
    NoFaultCapabilityAssessmentV023,
    NoFaultExecutionProfileV023,
    NoFaultQueueSnapshotV023,
    NoFaultTrafficResultV023,
    _successful_evidence_sources,
    score_nofault_v023,
)
from ecomsre.product.pilot.runtime_authority_v02 import (
    PilotRuntimeAuthorityV02,
    write_pilot_runtime_authority_v02,
)
from ecomsre_live_sandbox.contracts import (
    ensure_private_directory,
    write_private_json,
)
from ecomsre_live_sandbox.control import build_flag_documents
from ecomsre_live_sandbox.product_v024 import (
    ProductV024SandboxEnvironment,
    build_product_v024_runtime_bundle,
)


_BASELINE_PROFILE = Path("config/product-v023/baseline-readiness/profile.json")
_NOFAULT_PROFILE = Path("config/product-v023/nofault/profile.json")
_EPISODE_SECONDS = 300
_FINAL_TERMINAL = "ECOMSRE_PRODUCT_V024_TELEMETRY_CAPABILITY_REPAIR_COMPLETE"


def _stage(value: str) -> None:
    print(f"stage={value}", flush=True)


class _ProductV024Lifecycle(_SandboxOwnedSmokeLifecycle):
    """Use the existing lifecycle with the exact v0.2.4 Runtime extension."""

    def admit(self) -> None:
        root = self.repository_root
        runtime_root = self.private_root / "runtime"
        control_root = self.private_root / "control"
        for directory in (runtime_root, control_root):
            ensure_private_directory(directory)
        self.bundle = build_product_v024_runtime_bundle(root)
        upstream_flag = json.loads(
            (
                root / "third_party/opentelemetry-demo/src/flagd/demo.flagd.json"
            ).read_text(encoding="utf-8")
        )
        if not isinstance(upstream_flag, Mapping):
            raise ValueError("upstream baseline flag document is invalid")
        baseline, fault = build_flag_documents(upstream_flag, self.bundle)
        flag_directory = runtime_root / "flagd"
        ensure_private_directory(flag_directory)
        flag_file = flag_directory / "demo.flagd.json"
        write_private_json(flag_file, baseline, create_once=True)
        self.flag_file = flag_file
        self.baseline_document = baseline
        self.fault_document = fault
        self.environment = ProductV024SandboxEnvironment(
            repository_root=root,
            bundle=self.bundle,
            flagd_directory=flag_directory,
        )
        self.environment.verify_local_docker()
        self.environment.verify_upstream()
        resolved, raw_compose = self.environment.resolve()
        self.admitted_resolved_sha256 = semantic_sha256(
            resolved.model_dump(mode="json")
        )
        write_private_json(
            control_root / "resolved-compose.json",
            raw_compose,
            create_once=True,
        )
        self.environment.verify_cached_images(resolved, control_root)


def _source_results(
    evidence: EvidenceBundleV1,
) -> dict[EvidenceSourceV22, ConnectorQueryResultV1]:
    output: dict[EvidenceSourceV22, ConnectorQueryResultV1] = {}
    for item in evidence.objects:
        connector = item.payload.get("connector_result")
        if not isinstance(connector, Mapping):
            continue
        result = ConnectorQueryResultV1.model_validate(connector, strict=False)
        if result.source is item.source:
            output[item.source] = result
    return output


def _validate_v024_sources(
    evidence: EvidenceBundleV1,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    results = _source_results(evidence)
    metrics = results.get(EvidenceSourceV22.METRICS)
    resources = results.get(EvidenceSourceV22.RESOURCES)
    traces = results.get(EvidenceSourceV22.TRACES)
    if metrics is None or resources is None or traces is None:
        raise RuntimeError(
            "Product v0.2.4 Metrics, Resources, or Traces evidence is absent"
        )
    metric_records = tuple(
        item for item in metrics.records if isinstance(item, MetricFactV22)
    )
    expected_kinds = {
        MetricKindV22.ERROR_RATE,
        MetricKindV22.LATENCY_P95_MS,
        MetricKindV22.REQUEST_SUPPORT,
    }
    if (
        metrics.status is not ReadSourceStatusV22.SUCCESS_NONEMPTY
        or metrics.requested_services != ("checkout",)
        or metrics.covered_services != ("checkout",)
        or len(metric_records) != 3
        or len(metric_records) != len(metrics.records)
        or {item.metric_kind for item in metric_records} != expected_kinds
        or any(item.service != "checkout" for item in metric_records)
        or metrics.safe_error_code is not None
    ):
        raise RuntimeError("Product v0.2.4 Metrics contract did not pass")
    resource_records = tuple(
        item for item in resources.records if isinstance(item, ResourceUsageRecordV22)
    )
    if (
        resources.status is not ReadSourceStatusV22.SUCCESS_NONEMPTY
        or resources.requested_services != ("checkout",)
        or resources.covered_services != ("checkout",)
        or len(resource_records) != 1
        or len(resource_records) != len(resources.records)
        or resources.safe_error_code is not None
    ):
        raise RuntimeError("Product v0.2.4 Resources coverage did not pass")
    resource = resource_records[0]
    if (
        resource.service != "checkout"
        or resource.sampling_window_seconds != 10
        or len(resource.samples) != 5
        or any(not math.isfinite(item.cpu_percent) for item in resource.samples)
        or any(
            not isinstance(item.memory_bytes, int) or item.memory_bytes < 0
            for item in resource.samples
        )
    ):
        raise RuntimeError("Product v0.2.4 Resource record contract did not pass")
    trace_records = tuple(
        item for item in traces.records if isinstance(item, TraceSpanV22)
    )
    if (
        traces.status is not ReadSourceStatusV22.SUCCESS_NONEMPTY
        or traces.requested_services != ("checkout",)
        or "checkout" not in traces.covered_services
        or not trace_records
        or len(trace_records) != len(traces.records)
        or traces.safe_error_code is not None
    ):
        raise RuntimeError("Product v0.2.4 Traces coverage did not pass")
    metric_summary = {
        "terminal": "ECOMSRE_PRODUCT_V024_METRICS_CONTRACT_PASS",
        "status": metrics.status.value,
        "requested_services": list(metrics.requested_services),
        "covered_services": list(metrics.covered_services),
        "record_count": len(metric_records),
        "metric_kinds": sorted(item.metric_kind.value for item in metric_records),
        "sample_counts": {
            item.metric_kind.value: item.sample_count for item in metric_records
        },
        "safe_error_code": metrics.safe_error_code,
    }
    resource_summary = {
        "terminal": "ECOMSRE_PRODUCT_V024_RESOURCES_COVERAGE_PASS",
        "status": resources.status.value,
        "requested_services": list(resources.requested_services),
        "covered_services": list(resources.covered_services),
        "record_count": 1,
        "logical_service": resource.service,
        "sampling_window_seconds": resource.sampling_window_seconds,
        "sample_count": len(resource.samples),
        "cpu_values_finite": True,
        "memory_values_non_negative_integers": True,
        "safe_error_code": resources.safe_error_code,
    }
    trace_summary = {
        "terminal": "ECOMSRE_PRODUCT_V024_TRACES_COVERAGE_PASS",
        "status": traces.status.value,
        "requested_services": list(traces.requested_services),
        "covered_services": list(traces.covered_services),
        "record_count": len(trace_records),
        "normalized_services": sorted({item.service for item in trace_records}),
        "safe_error_code": traces.safe_error_code,
    }
    return metric_summary, resource_summary, trace_summary


def run_fresh_nofault(repository_root: Path) -> dict[str, Any]:
    root = repository_root.resolve(strict=True)
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%SZ-") + secrets.token_hex(4)
    run_root = root / ".local/product-v024/final-nofault" / run_id
    private_root = run_root / "private"
    product_root = run_root / "product"
    ensure_private_directory(private_root)
    lifecycle = _ProductV024Lifecycle(
        repository_root=root,
        private_root=private_root / "demo",
        stabilization_seconds=30,
    )
    processes = _ProductHostProcessesV023(
        root=root,
        data_root=product_root,
        private_root=private_root / "product-processes",
    )
    baseline_profile = ProductBaselineReadinessProfileV023.load(
        root / _BASELINE_PROFILE
    )
    nofault_profile = NoFaultExecutionProfileV023.load(root / _NOFAULT_PROFILE)
    stage = "PREFLIGHT"
    error: BaseException | None = None
    queue_before_sha256: str | None = None
    outer_baseline_before_sha256: str | None = None
    queue_default_unchanged = False
    outer_baseline_unchanged = False
    product_cleanup: dict[str, Any] = {"verdict": "BLOCKED"}
    demo_cleanup: dict[str, Any] = CleanupObservation.unknown_blocked().model_dump(
        mode="json"
    )
    payload: dict[str, Any] = {
        "schema_version": "ecomsre.product.v024.fresh-nofault.v1",
        "run_id": run_id,
    }
    resolved_compose_sha256: str | None = None
    try:
        _stage(stage)
        lifecycle.admit()
        if lifecycle.flag_file is None:
            raise RuntimeError("Product v0.2.4 queue file is absent")
        queue_before_sha256 = verify_queue_default_v021(
            lifecycle.flag_file,
            expected_default_value=0,
        ).before_sha256
        lifecycle.start()
        lifecycle.wait_ready()
        backend = lifecycle.authorize_reads()
        if not isinstance(backend, LocalSandboxReadBackend):
            raise RuntimeError("Product v0.2.4 owned Runtime backend differs")
        resolved_compose_sha256 = lifecycle.environment.resolve()[0].compose_sha256
        outer_baseline_before_sha256 = lifecycle.read_baseline_sha256()
        stage = "RUNTIME_AUTHORIZED"
        _stage(stage)

        authority_inputs = _authority_inputs(backend)
        prebound = PilotRuntimeAuthorityV02.build(
            environment_id="env-" + "0" * 24,
            allowed_logical_services=("checkout",),
            profile_sha256=baseline_profile.profile_sha256,
            **authority_inputs,
        )
        environment_payload = build_product_v023_environment_payload(
            repository_root=root,
            runtime_authority_sha256=prebound.connector_binding_sha256,
        )
        processes.start()
        environment_record = _request_json(
            processes,
            "POST",
            "/v1/environments",
            payload=environment_payload,
        )
        environment_id = str(environment_record["environment_id"])
        authority = PilotRuntimeAuthorityV02.build(
            environment_id=environment_id,
            allowed_logical_services=("checkout",),
            profile_sha256=baseline_profile.profile_sha256,
            **authority_inputs,
        )
        if authority.connector_binding_sha256 != prebound.connector_binding_sha256:
            raise RuntimeError("Product v0.2.4 Runtime authority binding changed")
        authority_path = product_root / "pilot/runtime-authority.json"
        runtime_path = product_root / "pilot/runtime-readiness.json"
        write_pilot_runtime_authority_v02(authority_path, authority)
        write_pilot_runtime_snapshot_v02(
            runtime_path,
            _runtime_snapshot(backend=backend, authority=authority),
        )
        write_private_json(
            private_root / "environment.json",
            environment_payload,
            create_once=True,
        )

        verify_job = _wait_job(
            processes,
            str(
                _request_json(
                    processes,
                    "POST",
                    f"/v1/environments/{environment_id}/verify-jobs",
                )["job_id"]
            ),
            data_root=product_root,
        )
        if verify_job.status is not ProductJobStatusV1.SUCCEEDED or not isinstance(
            verify_job.result, dict
        ):
            raise RuntimeError(
                verify_job.safe_error_code
                or "Product v0.2.4 connector verification failed"
            )
        identity = ServiceIdentityMapV1.model_validate(
            verify_job.result["service_identity_map"]
        )
        capability = EnvironmentCapabilityMatrixV1.model_validate(
            verify_job.result["capability_matrix"]
        )
        stage = "CONNECTORS_VERIFIED"
        _stage(stage)

        baseline_started_at = datetime.now(UTC)
        planned_windows = planned_baseline_windows_v023(baseline_started_at)
        with httpx.Client() as traffic_client:
            baseline_traffic = BoundedHealthyCheckoutTrafficV021(
                client=traffic_client
            ).run(
                endpoint="http://127.0.0.1:18080/api/checkout",
                profile=HealthyTrafficProfileV021(
                    request_seed=24020001,
                    maximum_request_count=baseline_profile.healthy_traffic_request_count,
                    requests_per_second=baseline_profile.healthy_traffic_requests_per_second,
                    error_budget=max(
                        1,
                        int(
                            baseline_profile.healthy_traffic_request_count
                            * baseline_profile.maximum_error_fraction
                        )
                        + 1,
                    ),
                ),
            )
        if (
            baseline_traffic.attempted
            != baseline_profile.healthy_traffic_request_count
            or baseline_traffic.failed
            / max(1, baseline_traffic.attempted)
            > baseline_profile.maximum_error_fraction
        ):
            raise RuntimeError("Product v0.2.4 Baseline traffic failed")
        _sleep_until_utc(
            datetime.fromisoformat(planned_windows[-1]["ended_at"])
            + timedelta(seconds=baseline_profile.warmup_seconds)
        )
        stage = "BASELINE_BUILDING"
        _stage(stage)
        baseline_job = _wait_job(
            processes,
            str(
                _request_json(
                    processes,
                    "POST",
                    f"/v1/environments/{environment_id}/baseline-jobs",
                    payload={
                        "build_policy": {
                            "mode": baseline_profile.mode,
                            "lookback_seconds": baseline_profile.lookback_seconds,
                            "window_count": baseline_profile.window_count,
                            "minimum_successful_windows": (
                                baseline_profile.minimum_accepted_windows
                            ),
                            "warmup_seconds": baseline_profile.warmup_seconds,
                        },
                        "candidate_services": list(
                            baseline_profile.candidate_services
                        ),
                        "planned_windows": list(planned_windows),
                        "activate": True,
                    },
                )["job_id"]
            ),
            data_root=product_root,
            timeout_seconds=240,
        )
        if baseline_job.status is not ProductJobStatusV1.SUCCEEDED or not isinstance(
            baseline_job.result, dict
        ):
            raise RuntimeError(
                baseline_job.safe_error_code
                or "Product v0.2.4 fresh Baseline build failed"
            )
        audit = ProductBaselineReadinessAuditV023.model_validate(
            baseline_job.result["readiness_audit_v023"]
        )
        if (
            not audit.final_builder_would_pass
            or audit.baseline_sha256 is None
            or audit.build_policy.get("mode") != "DEMO_ONLY"
            or baseline_job.result.get("active") is not True
        ):
            raise RuntimeError("Product v0.2.4 fresh DEMO_ONLY Baseline did not pass")
        candidate_identity_sha256 = _baseline_candidate_identity_sha256_v023(
            identity,
            audit,
        )
        if (
            audit.capability_sha256 != capability.capability_sha256
            or candidate_identity_sha256 != audit.service_identity_sha256
        ):
            raise RuntimeError("Product v0.2.4 Baseline bindings differ")
        stage = "BASELINE_READY"
        _stage(stage)

        before_restart = _restart_snapshot(
            processes,
            environment_id=environment_id,
            service_identity_sha256=identity.identity_sha256,
            baseline_candidate_identity_sha256=candidate_identity_sha256,
            capability_sha256=capability.capability_sha256,
        )
        processes.restart()
        rebound = ProductBaselineReadinessAuditV023.model_validate(
            _request_json(
                processes,
                "GET",
                f"/v1/baselines/{audit.baseline_id}/window-audit-v023",
            )
        )
        if rebound.audit_sha256 != audit.audit_sha256:
            raise RuntimeError("Product v0.2.4 Baseline changed across restart")
        after_restart = _restart_snapshot(
            processes,
            environment_id=environment_id,
            service_identity_sha256=identity.identity_sha256,
            baseline_candidate_identity_sha256=candidate_identity_sha256,
            capability_sha256=capability.capability_sha256,
        )
        restart_proof = BaselineRestartProofV023.build(
            before=before_restart,
            after=after_restart,
            connector_verification_count=0,
        )

        episode_started_at = datetime.now(UTC)
        stage = "NOFAULT_TRAFFIC"
        _stage(stage)
        with httpx.Client() as traffic_client:
            nofault_traffic = BoundedHealthyCheckoutTrafficV021(
                client=traffic_client
            ).run(
                endpoint="http://127.0.0.1:18080/api/checkout",
                profile=HealthyTrafficProfileV021(
                    request_seed=nofault_profile.seed,
                    maximum_request_count=nofault_profile.request_count,
                    requests_per_second=nofault_profile.requests_per_second,
                    error_budget=1,
                ),
            )
        _sleep_until_utc(
            episode_started_at + timedelta(seconds=_EPISODE_SECONDS)
        )
        runtime_rotation = _rotate_runtime_snapshot(
            path=runtime_path,
            snapshot=_runtime_snapshot(backend=backend, authority=authority),
            private_root=private_root,
            ordinal=1,
        )
        episode_ended_at = datetime.now(UTC)
        incident = IncidentRecordV1.model_validate(
            _request_json(
                processes,
                "POST",
                "/v1/incidents",
                payload={
                    "environment_id": environment_id,
                    "external_incident_key": f"product-v024-nofault-{run_id}",
                    "alert_name": "Product v0.2.4 No-Fault acceptance",
                    "summary": (
                        "Fresh healthy checkout observation with no fault active."
                    ),
                    "started_at": episode_started_at.isoformat(),
                    "ended_at": episode_ended_at.isoformat(),
                    "candidate_service_ids": list(
                        audit.baseline_entity_service_ids
                    ),
                    "labels": {"fault": nofault_profile.incident_fault_label},
                },
            )
        )
        traffic_result = NoFaultTrafficResultV023.build(
            environment_id=environment_id,
            incident_id=incident.incident_id,
            window=ConnectorWindowV1(
                started_at=incident.started_at,
                ended_at=incident.diagnosis_observed_at,
            ),
            profile_sha256=nofault_profile.profile_sha256,
            planned_request_count=nofault_profile.request_count,
            completed_request_count=nofault_traffic.attempted,
            error_count=nofault_traffic.failed,
            requests_per_second=nofault_profile.requests_per_second,
            maximum_error_fraction=nofault_profile.maximum_error_fraction,
            queue_fault_flag=nofault_profile.queue_fault_flag,
            passed=(
                nofault_traffic.attempted == nofault_profile.request_count
                and nofault_traffic.failed == 0
            ),
        )
        stage = "DIAGNOSIS"
        _stage(stage)
        diagnosis_job = _wait_job(
            processes,
            str(
                _request_json(
                    processes,
                    "POST",
                    f"/v1/incidents/{incident.incident_id}/diagnosis-jobs",
                )["job_id"]
            ),
            data_root=product_root,
            timeout_seconds=240,
        )
        if diagnosis_job.status is not ProductJobStatusV1.SUCCEEDED or not isinstance(
            diagnosis_job.result, dict
        ):
            raise RuntimeError(
                diagnosis_job.safe_error_code
                or "Product v0.2.4 Diagnosis failed"
            )
        diagnosis = DiagnosisResultV1.model_validate(diagnosis_job.result)
        evidence = EvidenceBundleV1.model_validate(
            _request_json(
                processes,
                "GET",
                f"/v1/incidents/{incident.incident_id}/evidence",
            )
        )
        metric_summary, resource_summary, trace_summary = _validate_v024_sources(
            evidence
        )
        counts = _database_counts(product_root, environment_id)
        pending, running, failed = _queue_counts(product_root)
        queue_snapshot = NoFaultQueueSnapshotV023.build(
            environment_id=environment_id,
            observed_at=datetime.now(UTC),
            pending_jobs=pending,
            running_jobs=running,
            failed_jobs=failed,
            queue_fault_flag=0,
        )
        successful_sources = _successful_evidence_sources(
            evidence,
            incident=incident,
        )
        runtime_ref = _successful_runtime_ref(evidence)
        assessment = NoFaultCapabilityAssessmentV023.build(
            runtime_healthy=runtime_ref is not None,
            runtime_evidence_ref=runtime_ref,
            successful_sources=successful_sources,
            healthy_traffic_passed=traffic_result.passed,
            healthy_traffic_result_sha256=traffic_result.result_sha256,
            limitation_evidence_refs=_limitation_evidence_refs(
                diagnosis,
                evidence,
            ),
        )
        scored = score_nofault_v023(
            baseline_audit=audit,
            restart_proof=restart_proof,
            incident=incident,
            diagnosis=diagnosis,
            bundle=evidence,
            capability_assessment=assessment,
            execution_profile=nofault_profile,
            traffic_result=traffic_result,
            queue_snapshot=queue_snapshot,
            active_profile_sha256=ACTIVE_PROFILE_SHA256_V023,
            incident_count=counts["incident_count"],
            diagnosis_count=counts["diagnosis_count"],
            fault_family_count=counts["fault_family_count"],
            action_authority_violations=0,
            agent_writes=0,
            runbook_executions=0,
        )
        source_results = _source_results(evidence)
        logs = source_results.get(EvidenceSourceV22.LOGS)
        runtime = source_results.get(EvidenceSourceV22.RUNTIME)
        payload.update(
            {
                "runtime": {
                    "authority_sha256": authority.connector_binding_sha256,
                    "runtime_snapshot_sha256": runtime_rotation[
                        "after_snapshot_sha256"
                    ],
                    "compose_sha256": resolved_compose_sha256,
                    "healthy": runtime is not None
                    and runtime.status is ReadSourceStatusV22.SUCCESS_NONEMPTY,
                },
                "baseline": {
                    "mode": "DEMO_ONLY",
                    "baseline_id": audit.baseline_id,
                    "baseline_sha256": audit.baseline_sha256,
                    "audit_sha256": audit.audit_sha256,
                    "restart_proof_sha256": restart_proof.proof_sha256,
                    "fresh_for_v024": True,
                },
                "traffic": {
                    "planned": 30,
                    "attempted": nofault_traffic.attempted,
                    "succeeded": nofault_traffic.succeeded,
                    "failed": nofault_traffic.failed,
                },
                "metrics": metric_summary,
                "resources": resource_summary,
                "traces": trace_summary,
                "logs": {
                    "status": None if logs is None else logs.status.value,
                    "covered_services": (
                        [] if logs is None else list(logs.covered_services)
                    ),
                    "active_profile_sha256": ACTIVE_PROFILE_SHA256_V023,
                    "p01_provenance_valid": (
                        scored.evidence_resolution.logs_profile_binding_visible
                    ),
                },
                "diagnosis": {
                    "terminal": diagnosis.terminal.value,
                    "result_sha256": diagnosis.result_sha256,
                    "capability_limitations": list(
                        diagnosis.capability_limitations
                    ),
                },
                "scorer": {
                    "terminal": scored.terminal.value,
                    "reasons": list(scored.reasons),
                    "result_sha256": scored.result_sha256,
                },
                "safety": {
                    "fault_attempt_count": 0,
                    "action_authority": "NONE",
                    "provider_calls": 0,
                    "agent_writes": 0,
                    "runbook_executions": 0,
                },
                "evidence_bundle": evidence.model_dump(mode="json"),
            }
        )
        if (
            scored.terminal.value != NOFAULT_FULLY_SUPPORTED_V023
            or scored.reasons
            or diagnosis.terminal is not DiagnosisTerminalV1.NO_INCIDENT
            or nofault_traffic.attempted != 30
            or nofault_traffic.succeeded != 30
            or nofault_traffic.failed != 0
        ):
            raise RuntimeError("Product v0.2.4 final No-Fault check did not pass")
        if (
            logs is None
            or logs.status is not ReadSourceStatusV22.SUCCESS_NONEMPTY
            or runtime is None
            or runtime.status is not ReadSourceStatusV22.SUCCESS_NONEMPTY
        ):
            raise RuntimeError("Product v0.2.4 Logs or Runtime evidence did not pass")
        stage = "MEASURED_PASS"
        _stage(stage)
        payload["terminal"] = _FINAL_TERMINAL
    except BaseException as caught:
        error = caught
    finally:
        if lifecycle.flag_file is not None and queue_before_sha256 is not None:
            try:
                verify_queue_default_v021(
                    lifecycle.flag_file,
                    expected_default_value=0,
                    expected_sha256=queue_before_sha256,
                )
                queue_default_unchanged = True
            except BaseException as cleanup_error:
                if error is None:
                    error = cleanup_error
        if outer_baseline_before_sha256 is not None and lifecycle.controller is not None:
            try:
                outer_baseline_unchanged = (
                    lifecycle.read_baseline_sha256()
                    == outer_baseline_before_sha256
                )
                if not outer_baseline_unchanged:
                    raise RuntimeError("Product v0.2.4 outer baseline changed")
            except BaseException as cleanup_error:
                if error is None:
                    error = cleanup_error
        product_cleanup = processes.cleanup_observation()
        try:
            demo_cleanup = lifecycle.cleanup_owned(
                baseline_unchanged=outer_baseline_unchanged
            ).model_dump(mode="json")
        except BaseException as cleanup_error:
            demo_cleanup = {
                "verdict": "BLOCKED",
                "safe_error": f"{type(cleanup_error).__name__}: {cleanup_error}",
            }
            if error is None:
                error = cleanup_error
        payload.update(
            {
                "stage": stage,
                "queue_default_unchanged": queue_default_unchanged,
                "outer_baseline_unchanged": outer_baseline_unchanged,
                "product_cleanup": product_cleanup,
                "demo_cleanup": demo_cleanup,
                "safe_error_type": (
                    None if error is None else type(error).__name__
                ),
                "safe_error": None if error is None else str(error)[:1000],
            }
        )
        if (
            error is None
            and (
                product_cleanup.get("verdict") != "CLEAN"
                or demo_cleanup.get("verdict") != "CLEAN"
                or not queue_default_unchanged
                or not outer_baseline_unchanged
            )
        ):
            error = RuntimeError("Product v0.2.4 cleanup did not pass")
            payload["safe_error_type"] = type(error).__name__
            payload["safe_error"] = str(error)
        write_private_json(
            private_root / "result.json",
            payload,
            create_once=True,
        )
    if error is not None:
        raise RuntimeError(
            f"Product v0.2.4 final check failed at {stage}: {error}"
        ) from error
    return payload


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="scripts.product_v024.run_fresh_nofault"
    )
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    result = run_fresh_nofault(arguments.project_root)
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
