from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import secrets
import time
from typing import Any, Mapping, Sequence, cast

from fastapi.testclient import TestClient
import httpx

from ecomsre.dta_v2.read_only_smoke import _SandboxOwnedSmokeLifecycle
from ecomsre.dta_v2.telemetry_adapters import LocalSandboxReadBackend
from ecomsre.dta_v2.tool_contracts import (
    HealthState,
    RuntimeRecord,
    RuntimeState,
    build_inspect_service_runtime_request,
)
from ecomsre.product.app import create_app
from ecomsre.product.connectors.pilot_runtime import (
    PilotRuntimeSnapshotV02,
    write_pilot_runtime_snapshot_v02,
)
from ecomsre.product.jobs.worker import run_one_job
from ecomsre.product.pilot.contracts_v02 import (
    PilotAttemptEventV02,
    PilotAttemptFailureDomainV02,
    PilotAttemptStageV02,
    PilotEpisodeRoleV02,
    PilotEpisodeTerminalV02,
    QueueProfileV02,
    TrafficProfileV02,
    semantic_sha256_v02,
)
from ecomsre.product.pilot.episode_runner_v02 import PilotEpisodeRepositoryV02
from ecomsre.product.pilot.queue_profile_v02 import QueueFlagControllerV02
from ecomsre.product.pilot.recovery_v02 import verify_baseline_recovery_v02
from ecomsre.product.pilot.runtime_authority_v02 import (
    PilotRuntimeAuthorityV02,
    write_pilot_runtime_authority_v02,
)
from ecomsre.product.pilot.traffic_v02 import BoundedCheckoutTrafficV02
from ecomsre.product.settings import ProductSettingsV1
from ecomsre.product.storage.sqlite_store import SqliteStoreV1


CALIBRATION_PASS_V02 = "ECOMSRE_PRODUCT_V02_UNKNOWN_FAULT_PROFILE_PASS"
CALIBRATION_BLOCKED_V02 = "BLOCKED_ECOMSRE_PRODUCT_V02_UNKNOWN_FAULT_PROFILE"
_CANDIDATE_SERVICES_V02 = ("checkout",)


def _reject_tracked_consumed_calibration(repository_root: Path) -> None:
    marker_path = (
        repository_root
        / "config/product-v02/live-pilot/calibration-consumed.json"
    )
    result_path = (
        repository_root / "docs/results/product-v02-live-knowledge-loop.json"
    )
    if not marker_path.exists() and not result_path.exists():
        return
    if not marker_path.is_file() or not result_path.is_file():
        raise ValueError("tracked calibration consumption evidence is incomplete")
    marker = _load_object(marker_path)
    result = _load_object(result_path)
    supplied_marker_sha256 = marker.pop("consumed_sha256", None)
    if (
        marker.get("schema_version")
        != "ecomsre.product.v02.calibration-consumed.v1"
        or marker.get("campaign_consumed") is not True
        or marker.get("terminal") != CALIBRATION_BLOCKED_V02
        or marker.get("live_attempt_count") != 0
        or supplied_marker_sha256 != semantic_sha256_v02(marker)
        or marker.get("public_result_sha256") != result.get("result_sha256")
        or result.get("engineering_terminal") != CALIBRATION_BLOCKED_V02
    ):
        raise ValueError("tracked calibration consumption evidence is invalid")
    raise ValueError("tracked calibration campaign is consumed and may not rerun")


def _load_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return payload


def _write_create_once_json(path: Path, payload: Mapping[str, object]) -> str:
    body = dict(payload)
    draft = dict(body)
    digest = semantic_sha256_v02(draft)
    body["report_sha256"] = digest
    encoded = json.dumps(
        body,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ).encode("utf-8") + b"\n"
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb", closefd=False) as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        os.close(descriptor)
    return digest


def _request_json(
    client: TestClient,
    method: str,
    path: str,
    *,
    payload: object | None = None,
) -> dict[str, Any]:
    response = client.request(method, path, json=payload)
    if response.status_code < 200 or response.status_code >= 300:
        raise RuntimeError(
            f"Product API request failed: {method} {path} "
            f"status={response.status_code} body={response.text[:500]}"
        )
    value = response.json()
    if not isinstance(value, dict):
        raise RuntimeError("Product API response is not an object")
    return value


def _run_product_job(
    client: TestClient,
    settings: ProductSettingsV1,
    *,
    path: str,
    payload: object | None = None,
    worker_id: str,
) -> tuple[str, dict[str, Any]]:
    queued = _request_json(client, "POST", path, payload=payload)
    job_id = str(queued["job_id"])
    if not run_one_job(settings, worker_id=worker_id):
        raise RuntimeError("Product worker did not claim the expected job")
    job = _request_json(client, "GET", f"/v1/jobs/{job_id}")
    if job.get("status") != "SUCCEEDED" or not isinstance(job.get("result"), dict):
        raise RuntimeError(
            f"Product job did not succeed: {job_id} "
            f"status={job.get('status')} error={job.get('safe_error_code')}"
        )
    return job_id, cast(dict[str, Any], job["result"])


def _authority_inputs(backend: LocalSandboxReadBackend) -> dict[str, str]:
    authority = backend.authority
    fields = {
        "daemon_identity_sha256": authority.daemon_identity_sha256,
        "docker_context_sha256": authority.docker_context_sha256,
        "config_bundle_sha256": authority.config_bundle_sha256,
        "resolved_sandbox_sha256": authority.resolved_sandbox_sha256,
        "resolved_endpoints_sha256": authority.resolved_endpoints_sha256,
        "ownership_scope_sha256": authority.ownership_scope_sha256,
    }
    if any(not isinstance(value, str) for value in fields.values()):
        raise RuntimeError("owned Runtime authority provenance is incomplete")
    return cast(dict[str, str], fields)


def _environment_payload(
    repository_root: Path,
    *,
    runtime_authority_sha256: str,
) -> dict[str, Any]:
    payload = _load_object(
        repository_root / "examples/product/environment.otel-demo.json"
    )
    payload["name"] = "product-v02-live-calibration"
    payload["description"] = (
        "Fresh read-only Product environment for the bounded v0.2 live calibration."
    )
    connectors = payload.get("connector_configs")
    if not isinstance(connectors, list):
        raise ValueError("Product environment connector configuration differs")
    normalized: list[dict[str, Any]] = []
    for raw in connectors:
        if not isinstance(raw, dict):
            raise ValueError("Product connector entry differs")
        item = json.loads(json.dumps(raw))
        endpoint = item.get("endpoint")
        if isinstance(endpoint, str):
            item["endpoint"] = endpoint.replace(
                "host.docker.internal", "127.0.0.1"
            )
        settings = item.get("settings")
        if item.get("kind") == "OPENSEARCH" and isinstance(settings, dict):
            settings["severity_filter"] = []
        if item.get("kind") == "HTTP_HEALTH" and isinstance(settings, dict):
            for service in settings.get("services", []):
                if isinstance(service, dict) and isinstance(
                    service.get("health_url"), str
                ):
                    service["health_url"] = service["health_url"].replace(
                        "host.docker.internal", "127.0.0.1"
                    )
        normalized.append(item)
    normalized.append(
        {
            "name": "pilot-runtime",
            "kind": "PILOT_RUNTIME",
            "endpoint": None,
            "settings": {
                "snapshot_ref": "pilot/runtime-calibration.json",
                "authority_sha256": runtime_authority_sha256,
                "maximum_age_seconds": 600,
            },
            "credential_refs": {},
        }
    )
    payload["connector_configs"] = normalized
    payload["explicit_service_catalog"] = list(_CANDIDATE_SERVICES_V02)
    return payload


def _runtime_services(
    backend: LocalSandboxReadBackend,
    *,
    run_id: str,
) -> tuple[dict[str, dict[str, object]], tuple[str, ...]]:
    result = backend.execute(
        build_inspect_service_runtime_request(
            run_id=run_id,
            services=_CANDIDATE_SERVICES_V02,
            max_results=len(_CANDIDATE_SERVICES_V02),
        )
    )
    records = tuple(item for item in result.records if type(item) is RuntimeRecord)
    if (
        len(records) != len(_CANDIDATE_SERVICES_V02)
        or {item.logical_service for item in records} != set(_CANDIDATE_SERVICES_V02)
    ):
        raise RuntimeError("owned Runtime observation lacks candidate coverage")
    services: dict[str, dict[str, object]] = {}
    drift: list[str] = []
    for record in records:
        state = (
            record.state.value
            if record.state in {RuntimeState.RUNNING, RuntimeState.EXITED, RuntimeState.ABSENT}
            else "OTHER"
        )
        services[record.logical_service] = {
            "state": state,
            "healthy": record.health is HealthState.HEALTHY,
            "restart_count": record.restart_count,
        }
        if (
            record.state is not RuntimeState.RUNNING
            or record.health is not HealthState.HEALTHY
            or record.restart_count != 0
        ):
            drift.append(
                f"{record.logical_service}:{record.state.value}:"
                f"health={record.health.value}:restarts={record.restart_count}"
            )
    return services, tuple(sorted(drift))


def _connector_health(result: Mapping[str, object]) -> dict[str, bool]:
    raw = result.get("connector_health")
    if not isinstance(raw, list):
        raise RuntimeError("Product connector verification result is incomplete")
    health = {
        str(item.get("name") or item.get("connector_name")): item.get("status")
        == "AVAILABLE"
        for item in raw
        if isinstance(item, dict)
    }
    if not health or not all(health.values()):
        raise RuntimeError("one or more Product connectors are unavailable")
    return health


def _candidate_service_binding(
    result: Mapping[str, object],
) -> tuple[tuple[str, ...], dict[str, str]]:
    identity = result.get("service_identity_map")
    services = identity.get("services") if isinstance(identity, dict) else None
    if not isinstance(services, list):
        raise RuntimeError("Product service identity map is incomplete")
    by_logical = {
        str(item.get("logical_service")): str(item.get("service_id"))
        for item in services
        if isinstance(item, dict)
    }
    if not set(_CANDIDATE_SERVICES_V02).issubset(by_logical):
        raise RuntimeError("Product candidate service mapping is incomplete")
    selected = tuple(sorted(by_logical[name] for name in _CANDIDATE_SERVICES_V02))
    return selected, {service_id: logical for logical, service_id in by_logical.items()}


def _evidence_summary(
    diagnosis: Mapping[str, object], evidence: Mapping[str, object]
) -> dict[str, object]:
    objects = evidence.get("objects")
    if not isinstance(objects, list):
        raise RuntimeError("Product evidence bundle is incomplete")
    by_ref = {
        str(item.get("evidence_ref")): item
        for item in objects
        if isinstance(item, dict) and item.get("evidence_ref")
    }
    linked: set[str] = set()
    for key in ("supporting_evidence_refs", "contradicting_evidence_refs"):
        refs = evidence.get(key)
        if isinstance(refs, list):
            linked.update(str(ref) for ref in refs)
    supporting = diagnosis.get("supporting_evidence_refs")
    support_refs = tuple(str(item) for item in supporting) if isinstance(supporting, list) else ()
    support_sources = tuple(
        sorted(
            {
                str(by_ref[ref].get("source"))
                for ref in support_refs
                if ref in by_ref
            }
        )
    )
    raw_private = json.dumps(objects, sort_keys=True)
    provisional = diagnosis.get("provisional_report")
    provisional_terminal = (
        provisional.get("terminal") if isinstance(provisional, dict) else None
    )
    return {
        "evidence_object_count": len(objects),
        "evidence_refs_resolve": linked.issubset(by_ref),
        "support_sources": support_sources,
        "queue_log_observed": (
            "kafkaQueueProblems" in raw_private and "overloading queue" in raw_private
        ),
        "provisional_terminal": provisional_terminal,
    }


def _classify_attempt(
    diagnosis: Mapping[str, object], summary: Mapping[str, object]
) -> PilotEpisodeTerminalV02:
    terminal = diagnosis.get("terminal")
    if terminal == "CORE_KNOWN":
        return PilotEpisodeTerminalV02.CORE_ABSORBED
    if terminal == "EXTENSION_KNOWN":
        return PilotEpisodeTerminalV02.EXTENSION_ABSORBED
    if terminal == "NO_INCIDENT":
        return PilotEpisodeTerminalV02.NO_INCIDENT_FALSELY_ADMITTED
    if terminal != "OPEN_WORLD":
        return PilotEpisodeTerminalV02.OPEN_WORLD_NOT_REACHED
    if (
        summary.get("provisional_terminal") != "UNREGISTERED_INCIDENT_SUSPECTED"
        or summary.get("queue_log_observed") is not True
        or summary.get("evidence_refs_resolve") is not True
        or len(cast(Sequence[object], summary.get("support_sources", ()))) < 2
        or diagnosis.get("action_authority") != "NONE"
        or diagnosis.get("agent_writes") != 0
        or diagnosis.get("runbook_executions") != 0
    ):
        return PilotEpisodeTerminalV02.PROFILE_NOT_OBSERVABLE
    return PilotEpisodeTerminalV02.PASS


class _AttemptLedgerV02:
    def __init__(
        self,
        repository: PilotEpisodeRepositoryV02,
        *,
        attempt_id: str,
        attempt_number: int,
        signature: str,
    ) -> None:
        self.repository = repository
        self.attempt_id = attempt_id
        self.attempt_number = attempt_number
        self.signature = signature
        self.sequence = 0
        self.stage: PilotAttemptStageV02 | None = None

    def append(
        self,
        stage: PilotAttemptStageV02,
        *,
        failure_domain: PilotAttemptFailureDomainV02 | None = None,
        usable_fault_observation: bool | None = None,
        diagnosis_result_exists: bool | None = None,
        flag_restored: bool | None = None,
        cleanup_status: str | None = None,
        episode_terminal: PilotEpisodeTerminalV02 | None = None,
    ) -> PilotAttemptEventV02:
        self.sequence += 1
        event = PilotAttemptEventV02.build(
            event_id=f"{self.attempt_id}-{self.sequence}",
            attempt_id=self.attempt_id,
            slot_id="CALIBRATION",
            role=PilotEpisodeRoleV02.CALIBRATION,
            attempt_number=self.attempt_number,
            sequence=self.sequence,
            previous_stage=self.stage,
            stage=stage,
            attempt_signature_sha256=self.signature,
            failure_domain=failure_domain,
            usable_fault_observation=usable_fault_observation,
            diagnosis_result_exists=diagnosis_result_exists,
            flag_restored=flag_restored,
            cleanup_status=cleanup_status,
            episode_terminal=episode_terminal,
            observed_at=datetime.now(UTC),
        )
        persisted = self.repository.append_attempt_event(event)
        self.stage = stage
        return persisted


def _run_candidate_attempt(
    *,
    private_report_root: Path,
    attempt_number: int,
    value: int,
    profile: QueueProfileV02,
    traffic_profile: TrafficProfileV02,
    observation_seconds: int,
    controller: QueueFlagControllerV02,
    lifecycle_backend: LocalSandboxReadBackend,
    client: TestClient,
    settings: ProductSettingsV1,
    environment_id: str,
    candidate_service_ids: tuple[str, ...],
    logical_by_service_id: Mapping[str, str],
    initial_connector_health: Mapping[str, bool],
) -> dict[str, object]:
    signature = semantic_sha256_v02(
        {
            "value": value,
            "traffic_profile": traffic_profile.model_dump(mode="json"),
            "observation_seconds": observation_seconds,
        }
    )
    attempt_id = f"attempt-{secrets.token_hex(12)}"
    repository = PilotEpisodeRepositoryV02(SqliteStoreV1(settings.sqlite_path))
    ledger = _AttemptLedgerV02(
        repository,
        attempt_id=attempt_id,
        attempt_number=attempt_number,
        signature=signature,
    )
    ledger.append(PilotAttemptStageV02.PLANNED)
    diagnosis: dict[str, Any] | None = None
    evidence_summary: dict[str, object] | None = None
    incident_id: str | None = None
    product_job_id: str | None = None
    traffic_result: dict[str, object] | None = None
    attempt_error: Exception | None = None
    failure_domain = PilotAttemptFailureDomainV02.NONE
    try:
        _, before_drift = _runtime_services(
            lifecycle_backend,
            run_id=secrets.token_hex(16),
        )
        before = verify_baseline_recovery_v02(
            environment_id=environment_id,
            expected_baseline_sha256=controller.expected_baseline_sha256,
            current_flag_bytes=controller._read_bytes(),
            connector_health=initial_connector_health,
            traffic_active=False,
            owned_drift_refs=before_drift,
        )
        if before.status != "PASS":
            raise RuntimeError("pre-attempt baseline recovery verification failed")
        ledger.append(PilotAttemptStageV02.BASELINE_VERIFIED)
        started_at = datetime.now(UTC)
        with controller.activated(value):
            ledger.append(PilotAttemptStageV02.CONTROL_APPLIED)
            try:
                with httpx.Client() as traffic_client:
                    traffic = BoundedCheckoutTrafficV02(client=traffic_client).run(
                        endpoint="http://127.0.0.1:18080/api/checkout",
                        profile=traffic_profile,
                    )
                traffic_result = traffic.model_dump(mode="json")
            except httpx.HTTPError as error:
                failure_domain = PilotAttemptFailureDomainV02.TRAFFIC
                raise RuntimeError("bounded checkout traffic failed") from error
            ledger.append(PilotAttemptStageV02.TRAFFIC_STOPPED)
            if observation_seconds:
                time.sleep(observation_seconds)
            ended_at = datetime.now(UTC)
            ledger.append(PilotAttemptStageV02.OBSERVATION_CAPTURED)
            incident = _request_json(
                client,
                "POST",
                "/v1/incidents",
                payload={
                    "environment_id": environment_id,
                    "external_incident_key": f"pilot-observation-{secrets.token_hex(12)}",
                    "alert_name": "checkout-observer-signal",
                    "summary": (
                        "Observer-visible checkout degradation in a bounded local window."
                    ),
                    "started_at": started_at.isoformat(),
                    "ended_at": ended_at.isoformat(),
                    "candidate_service_ids": candidate_service_ids,
                    "labels": {"observation": "bounded-local"},
                },
            )
            incident_id = str(incident["incident_id"])
            product_job_id, diagnosis = _run_product_job(
                client,
                settings,
                path=f"/v1/incidents/{incident_id}/diagnosis-jobs",
                worker_id=f"product-v02-calibration-{attempt_number}",
            )
            ledger.append(PilotAttemptStageV02.DIAGNOSIS_PERSISTED)
            evidence = _request_json(
                client,
                "GET",
                f"/v1/incidents/{incident_id}/evidence",
            )
            evidence_summary = _evidence_summary(diagnosis, evidence)
    except Exception as error:
        attempt_error = error
        if failure_domain is PilotAttemptFailureDomainV02.NONE:
            failure_domain = (
                PilotAttemptFailureDomainV02.CONNECTOR
                if "connector" in str(error).casefold()
                else PilotAttemptFailureDomainV02.PRODUCT
            )
    finally:
        flag_restored = False
        try:
            if hashlib.sha256(controller._read_bytes()).hexdigest() != (
                controller.expected_baseline_sha256
            ):
                controller.restore()
            flag_restored = (
                hashlib.sha256(controller._read_bytes()).hexdigest()
                == controller.expected_baseline_sha256
            )
        except Exception as error:
            if attempt_error is None:
                attempt_error = error
            failure_domain = PilotAttemptFailureDomainV02.CLEANUP
        ledger.append(
            PilotAttemptStageV02.FLAG_RESTORED
            if flag_restored
            else PilotAttemptStageV02.FLAG_RESTORE_BLOCKED
        )

    recovery_connector_health: dict[str, bool]
    recovery_error: Exception | None = None
    owned_drift: tuple[str, ...]
    observed_flag_bytes = b""
    try:
        if not flag_restored:
            raise RuntimeError("queue flag exact restoration failed")
        _, verification = _run_product_job(
            client,
            settings,
            path=f"/v1/environments/{environment_id}/verify-jobs",
            worker_id=f"product-v02-recovery-{attempt_number}",
        )
        recovery_connector_health = _connector_health(verification)
        _, owned_drift = _runtime_services(
            lifecycle_backend,
            run_id=secrets.token_hex(16),
        )
        observed_flag_bytes = controller._read_bytes()
    except Exception as error:
        recovery_error = error
        recovery_connector_health = {"verification": False}
        owned_drift = ("runtime-or-restoration-verification-failed",)
    recovery = verify_baseline_recovery_v02(
        environment_id=environment_id,
        expected_baseline_sha256=controller.expected_baseline_sha256,
        current_flag_bytes=observed_flag_bytes,
        connector_health=recovery_connector_health,
        traffic_active=False,
        owned_drift_refs=owned_drift,
    )
    if flag_restored and recovery.status == "PASS":
        ledger.append(PilotAttemptStageV02.RECOVERY_VERIFIED)
        ledger.append(PilotAttemptStageV02.CLEANUP_CLEAN)
        cleanup_status = "CLEAN"
    else:
        ledger.append(PilotAttemptStageV02.CLEANUP_BLOCKED)
        cleanup_status = "BLOCKED"

    terminal = (
        _classify_attempt(diagnosis, evidence_summary)
        if diagnosis is not None and evidence_summary is not None
        else PilotEpisodeTerminalV02.DIAGNOSIS_FAILED
    )
    observed_root_service: str | None = None
    if terminal is PilotEpisodeTerminalV02.PASS and diagnosis is not None:
        root_ids = diagnosis.get("root_service_ids")
        roots = (
            tuple(logical_by_service_id.get(str(item), "") for item in root_ids)
            if isinstance(root_ids, list)
            else ()
        )
        roots = tuple(item for item in roots if item)
        if len(roots) != 1 or roots[0] != "checkout":
            terminal = PilotEpisodeTerminalV02.PROFILE_NOT_OBSERVABLE
        else:
            observed_root_service = roots[0]
    if recovery.status != "PASS":
        terminal = PilotEpisodeTerminalV02.BASELINE_NOT_RESTORED
    if terminal is PilotEpisodeTerminalV02.PASS and attempt_error is not None:
        terminal = PilotEpisodeTerminalV02.DIAGNOSIS_FAILED
    if terminal is not PilotEpisodeTerminalV02.PASS and failure_domain is (
        PilotAttemptFailureDomainV02.NONE
    ):
        failure_domain = PilotAttemptFailureDomainV02.SEMANTIC
    ledger.append(
        PilotAttemptStageV02.FINALIZED,
        failure_domain=failure_domain,
        usable_fault_observation=(
            evidence_summary is not None
            and evidence_summary.get("queue_log_observed") is True
        ),
        diagnosis_result_exists=diagnosis is not None,
        flag_restored=recovery.baseline_restored,
        cleanup_status=cleanup_status,
        episode_terminal=terminal,
    )

    result: dict[str, object] = {
        "attempt_id": attempt_id,
        "attempt_number": attempt_number,
        "attempt_signature_sha256": signature,
        "injected_value": value,
        "traffic_result": traffic_result,
        "incident_id": incident_id,
        "product_job_id": product_job_id,
        "diagnosis": diagnosis,
        "evidence_summary": evidence_summary,
        "baseline_recovery": recovery.model_dump(mode="json"),
        "episode_terminal": terminal.value,
        "observed_root_service": observed_root_service,
        "failure_domain": failure_domain.value,
        "safe_error_type": (
            None if attempt_error is None else type(attempt_error).__name__
        ),
        "safe_error": None if attempt_error is None else str(attempt_error)[:500],
        "recovery_error_type": (
            None if recovery_error is None else type(recovery_error).__name__
        ),
    }
    _write_create_once_json(
        private_report_root / f"calibration-attempt-{attempt_number}.json",
        result,
    )
    return result


def _atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".{path.name}.product-v02.tmp"
    descriptor = os.open(
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
        0o600,
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", closefd=False) as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.close(descriptor)
        descriptor = -1
        os.replace(temporary, path)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary.exists():
            temporary.unlink()


def _write_public_calibration(
    *,
    repository_root: Path,
    terminal: str,
    observed_at: datetime,
    attempt_results: Sequence[Mapping[str, object]],
    selected_root_service: str | None,
    selected_profile_sha256: str | None,
    private_report_sha256: str,
    demo_cleanup: str,
    outer_baseline_restored: bool,
) -> None:
    normalized_attempts: list[dict[str, object]] = []
    for item in attempt_results:
        diagnosis = item.get("diagnosis")
        diagnosis_payload = diagnosis if isinstance(diagnosis, dict) else {}
        evidence_summary = item.get("evidence_summary")
        evidence_payload = (
            evidence_summary if isinstance(evidence_summary, dict) else {}
        )
        baseline_recovery = item.get("baseline_recovery")
        recovery_payload = (
            baseline_recovery if isinstance(baseline_recovery, dict) else {}
        )
        normalized_attempts.append(
            {
                "episode_terminal": item.get("episode_terminal"),
                "diagnosis_terminal": diagnosis_payload.get("terminal"),
                "support_sources": evidence_payload.get("support_sources", ()),
                "queue_log_observed": evidence_payload.get(
                    "queue_log_observed", False
                ),
                "baseline_recovery": recovery_payload.get("status", "FAIL"),
            }
        )
    payload: dict[str, object] = {
        "schema_version": "ecomsre.product.v02.profile-calibration.v1",
        "terminal": terminal,
        "observed_at": observed_at.isoformat(),
        "live_attempt_count": len(attempt_results),
        "selected_root_service": selected_root_service,
        "selected_profile_sha256": selected_profile_sha256,
        "private_report_sha256": private_report_sha256,
        "attempts": normalized_attempts,
        "outer_baseline_restored": outer_baseline_restored,
        "baseline_restoration": (
            outer_baseline_restored
            and bool(normalized_attempts)
            and all(
                item.get("baseline_recovery") == "PASS"
                for item in normalized_attempts
            )
        ),
        "demo_cleanup": demo_cleanup,
        "action_authority": "NONE",
        "agent_writes": 0,
        "runbook_executions": 0,
    }
    payload["report_sha256"] = semantic_sha256_v02(payload)
    json_path = repository_root / "docs/analysis/product-v02-profile-calibration.json"
    _atomic_write_text(
        json_path,
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
    )
    markdown = f"""# Product v0.2 unknown-fault profile calibration

Terminal: `{terminal}`

- Live calibration attempts: `{len(attempt_results)}`
- Selected observer-visible root: `{selected_root_service or 'NONE'}`
- Attempt baseline restoration: `{payload['baseline_restoration']}`
- Post-run outer baseline restored: `{outer_baseline_restored}`
- Owned Demo cleanup: `{demo_cleanup}`
- Action authority: `NONE`
- Agent writes: `0`
- Runbook executions: `0`

This public report intentionally excludes the evaluator-only flag key, injected
numeric values, private control identifiers, truth mechanism, and injection
commands. The private report is bound only by SHA-256.
"""
    _atomic_write_text(
        repository_root / "docs/analysis/product-v02-profile-calibration.md",
        markdown,
    )
    progress = {
        "schema_version": "ecomsre.product.v02.progress.v1",
        "increment": 1,
        "terminal": terminal,
        "live_attempt_count": len(attempt_results),
        "accepted_positive_episode_count": 0,
        "heldout_recurrence_count": 0,
        "next_boundary": (
            "INDEPENDENT_PRE_CAMPAIGN_REVIEW"
            if terminal == CALIBRATION_PASS_V02
            else "STOPPED_UNKNOWN_FAULT_PROFILE"
        ),
    }
    progress["progress_sha256"] = semantic_sha256_v02(progress)
    _atomic_write_text(
        repository_root / "docs/analysis/product-v02-progress.json",
        json.dumps(progress, indent=2, sort_keys=True) + "\n",
    )


def run_live_calibration_v02(
    *,
    repository_root: Path,
    stabilization_seconds: int = 30,
    baseline_accumulation_seconds: int = 360,
    observation_seconds: int = 30,
) -> dict[str, object]:
    root = repository_root.resolve(strict=True)
    _reject_tracked_consumed_calibration(root)
    if not 0 <= stabilization_seconds <= 120:
        raise ValueError("stabilization seconds must be between 0 and 120")
    if not 360 <= baseline_accumulation_seconds <= 900:
        raise ValueError("baseline accumulation must be between 360 and 900 seconds")
    if not 15 <= observation_seconds <= 120:
        raise ValueError("observation seconds must be between 15 and 120")
    profile_path = root / "config/product-v02/live-pilot/profile.json"
    profile = QueueProfileV02.model_validate(_load_object(profile_path))
    if profile.selected_value is not None:
        raise ValueError("queue profile is already frozen; calibration may not rerun")
    campaign = _load_object(root / "config/product-v02/live-pilot/campaign.json")
    traffic_raw = campaign.get("traffic_profiles", {}).get("CALIBRATION")
    traffic_profile = TrafficProfileV02.model_validate(traffic_raw)
    candidate_profile_sha256 = profile.frozen().profile_sha256
    assert candidate_profile_sha256 is not None

    calibration_root = root / ".local/product-v02/private-live-control"
    campaign_sentinel = calibration_root / "calibration-start.json"
    if campaign_sentinel.exists():
        raise ValueError("live calibration campaign was already started and may not rerun")

    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ-") + secrets.token_hex(4)
    private_root = (calibration_root / run_id).resolve()
    product_data_root = (
        root / ".local/product-v02/live-pilot" / run_id
    ).resolve()
    if not private_root.is_relative_to(root) or not product_data_root.is_relative_to(root):
        raise ValueError("pilot live roots escape the repository")
    lifecycle = _SandboxOwnedSmokeLifecycle(
        repository_root=root,
        private_root=private_root,
        stabilization_seconds=stabilization_seconds,
    )
    attempt_results: list[dict[str, object]] = []
    selected_value: int | None = None
    selected_root: str | None = None
    demo_cleanup = "NOT_ATTEMPTED"
    lifecycle_failure: Exception | None = None
    baseline_restored = False
    controller: QueueFlagControllerV02 | None = None
    environment_id: str | None = None
    calibration_runtime_binding_sha256: str | None = None
    try:
        lifecycle.admit()
        if lifecycle.flag_file is None:
            raise RuntimeError("owned lifecycle did not bind the private flag file")
        baseline_bytes = lifecycle.flag_file.read_bytes()
        baseline_sha256 = hashlib.sha256(baseline_bytes).hexdigest()
        controller = QueueFlagControllerV02(
            runtime_path=lifecycle.flag_file,
            profile=profile,
            expected_baseline_sha256=baseline_sha256,
        )
        _write_create_once_json(
            campaign_sentinel,
            {
                "schema_version": "ecomsre.product.v02.calibration-start.v1",
                "run_id": run_id,
                "started_at": datetime.now(UTC).isoformat(),
                "profile_candidate_sha256": candidate_profile_sha256,
                "traffic_profile_sha256": semantic_sha256_v02(
                    traffic_profile.model_dump(mode="json")
                ),
                "maximum_attempts": 3,
                "maximum_changed_iterations": 2,
            },
        )
        lifecycle.start()
        lifecycle.wait_ready()
        backend = cast(LocalSandboxReadBackend, lifecycle.authorize_reads())
        if baseline_accumulation_seconds:
            time.sleep(baseline_accumulation_seconds)

        authority_inputs = _authority_inputs(backend)
        prebound = PilotRuntimeAuthorityV02.build(
            environment_id="env-" + "0" * 24,
            allowed_logical_services=_CANDIDATE_SERVICES_V02,
            profile_sha256=candidate_profile_sha256,
            **authority_inputs,
        )
        calibration_runtime_binding_sha256 = prebound.connector_binding_sha256
        authority_path = product_data_root / "pilot/runtime-authority.json"
        settings = ProductSettingsV1(
            data_root=product_data_root,
            pilot_runtime_authority_path=authority_path,
            connector_timeout_seconds=15,
        )
        with TestClient(create_app(settings)) as client:
            ready = _request_json(client, "GET", "/readyz")
            if ready.get("status") != "ready":
                raise RuntimeError("in-process Product API is not ready")
            environment = _request_json(
                client,
                "POST",
                "/v1/environments",
                payload=_environment_payload(
                    root,
                    runtime_authority_sha256=prebound.connector_binding_sha256,
                ),
            )
            environment_id = str(environment["environment_id"])
            authority = PilotRuntimeAuthorityV02.build(
                environment_id=environment_id,
                allowed_logical_services=_CANDIDATE_SERVICES_V02,
                profile_sha256=candidate_profile_sha256,
                **authority_inputs,
            )
            if authority.connector_binding_sha256 != prebound.connector_binding_sha256:
                raise RuntimeError("pilot Runtime binding changed across environment binding")
            write_pilot_runtime_authority_v02(authority_path, authority)
            runtime_services, runtime_drift = _runtime_services(
                backend,
                run_id=secrets.token_hex(16),
            )
            if runtime_drift:
                raise RuntimeError("candidate Runtime baseline contains owned drift")
            runtime_snapshot = PilotRuntimeSnapshotV02.build(
                environment_id=environment_id,
                authority_sha256=authority.connector_binding_sha256,
                observed_at=datetime.now(UTC),
                services=runtime_services,
            )
            write_pilot_runtime_snapshot_v02(
                product_data_root / "pilot/runtime-calibration.json",
                runtime_snapshot,
            )
            _, verification = _run_product_job(
                client,
                settings,
                path=f"/v1/environments/{environment_id}/verify-jobs",
                worker_id="product-v02-calibration-verify",
            )
            connector_health = _connector_health(verification)
            candidate_service_ids, logical_by_service_id = (
                _candidate_service_binding(verification)
            )
            _, baseline = _run_product_job(
                client,
                settings,
                path=f"/v1/environments/{environment_id}/baseline-jobs",
                payload={
                    "build_policy": {
                        "mode": "DEMO_ONLY",
                        "lookback_seconds": 180,
                        "window_count": 5,
                        "minimum_successful_windows": 1,
                        "warmup_seconds": 180,
                    },
                    "activate": True,
                },
                worker_id="product-v02-calibration-baseline",
            )
            if baseline.get("active") is not True:
                raise RuntimeError("Product calibration baseline is not active")

            for attempt_number, value in enumerate(profile.candidate_values, 1):
                attempt = _run_candidate_attempt(
                    private_report_root=private_root / "report",
                    attempt_number=attempt_number,
                    value=value,
                    profile=profile,
                    traffic_profile=traffic_profile,
                    observation_seconds=observation_seconds,
                    controller=controller,
                    lifecycle_backend=backend,
                    client=client,
                    settings=settings,
                    environment_id=environment_id,
                    candidate_service_ids=candidate_service_ids,
                    logical_by_service_id=logical_by_service_id,
                    initial_connector_health=connector_health,
                )
                attempt_results.append(attempt)
                if attempt["episode_terminal"] == PilotEpisodeTerminalV02.PASS.value:
                    selected_value = value
                    selected_root = str(attempt["observed_root_service"])
                    break
                if attempt["episode_terminal"] not in {
                    PilotEpisodeTerminalV02.CORE_ABSORBED.value,
                    PilotEpisodeTerminalV02.EXTENSION_ABSORBED.value,
                    PilotEpisodeTerminalV02.NO_INCIDENT_FALSELY_ADMITTED.value,
                    PilotEpisodeTerminalV02.OPEN_WORLD_NOT_REACHED.value,
                    PilotEpisodeTerminalV02.PROFILE_NOT_OBSERVABLE.value,
                }:
                    break
        baseline_restored = (
            controller is not None
            and hashlib.sha256(controller._read_bytes()).hexdigest()
            == controller.expected_baseline_sha256
        )
    except Exception as error:
        lifecycle_failure = error
        if controller is not None:
            try:
                controller.restore()
                baseline_restored = True
            except Exception:
                baseline_restored = False
    finally:
        try:
            cleanup = lifecycle.cleanup_owned(baseline_unchanged=baseline_restored)
            demo_cleanup = cleanup.verdict
            if demo_cleanup != "CLEAN" and lifecycle_failure is None:
                lifecycle_failure = RuntimeError("owned Demo cleanup did not close CLEAN")
        except Exception as error:
            demo_cleanup = "BLOCKED"
            if lifecycle_failure is None:
                lifecycle_failure = error

    observed_at = datetime.now(UTC)
    terminal = (
        CALIBRATION_PASS_V02
        if selected_value is not None
        and selected_root is not None
        and lifecycle_failure is None
        and baseline_restored
        and demo_cleanup == "CLEAN"
        else CALIBRATION_BLOCKED_V02
    )
    private_payload: dict[str, object] = {
        "schema_version": "ecomsre.product.v02.private-profile-calibration.v1",
        "terminal": terminal,
        "observed_at": observed_at.isoformat(),
        "environment_id": environment_id,
        "profile_name": profile.profile_name,
        "calibration_contract_sha256": candidate_profile_sha256,
        "calibration_runtime_binding_sha256": calibration_runtime_binding_sha256,
        "selected_value": selected_value,
        "selected_root_service": selected_root,
        "attempts": attempt_results,
        "baseline_restored": baseline_restored,
        "demo_cleanup": demo_cleanup,
        "action_authority": "NONE",
        "agent_writes": 0,
        "runbook_executions": 0,
        "safe_failure_type": (
            None if lifecycle_failure is None else type(lifecycle_failure).__name__
        ),
        "safe_failure": (
            None if lifecycle_failure is None else str(lifecycle_failure)[:500]
        ),
    }
    private_report_sha256 = _write_create_once_json(
        private_root / "report/product-v02-profile-calibration-private.json",
        private_payload,
    )
    selected_profile_sha256: str | None = None
    if terminal == CALIBRATION_PASS_V02:
        frozen = QueueProfileV02.model_validate(
            {
                **profile.model_dump(mode="python", exclude={"profile_sha256"}),
                "selected_value": selected_value,
                "selected_root_service": selected_root,
                "calibration_report_sha256": private_report_sha256,
                "calibration_contract_sha256": candidate_profile_sha256,
                "calibration_runtime_binding_sha256": (
                    calibration_runtime_binding_sha256
                ),
                "calibrated_at": observed_at,
                "profile_sha256": None,
            }
        ).frozen()
        selected_profile_sha256 = frozen.profile_sha256
        _atomic_write_text(
            profile_path,
            frozen.model_dump_json(indent=2) + "\n",
        )
    _write_public_calibration(
        repository_root=root,
        terminal=terminal,
        observed_at=observed_at,
        attempt_results=attempt_results,
        selected_root_service=selected_root,
        selected_profile_sha256=selected_profile_sha256,
        private_report_sha256=private_report_sha256,
        demo_cleanup=demo_cleanup,
        outer_baseline_restored=baseline_restored,
    )
    return {
        "terminal": terminal,
        "live_attempt_count": len(attempt_results),
        "selected_root_service": selected_root,
        "selected_profile_sha256": selected_profile_sha256,
        "private_report_sha256": private_report_sha256,
        "baseline_restored": baseline_restored,
        "demo_cleanup": demo_cleanup,
        "action_authority": "NONE",
        "agent_writes": 0,
        "runbook_executions": 0,
    }


__all__ = (
    "CALIBRATION_BLOCKED_V02",
    "CALIBRATION_PASS_V02",
    "run_live_calibration_v02",
)
