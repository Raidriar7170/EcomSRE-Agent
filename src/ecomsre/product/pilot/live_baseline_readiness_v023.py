"""Fresh, bounded Product v0.2.3 live Baseline and restart campaign."""

from __future__ import annotations

import ast
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
import hashlib
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import time
from typing import Any, cast

import httpx

from ecomsre.dta_v2.read_only_smoke import (
    CleanupObservation,
    _SandboxOwnedSmokeLifecycle,
)
from ecomsre.dta_v2.telemetry_adapters import LocalSandboxReadBackend
from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22
from ecomsre.environment.command_runner import AuditedSubprocessRunner
from ecomsre.product.connectors.opensearch_profile_binding_v023 import (
    ACTIVE_PROFILE_BINDING_SHA256_V023,
    ACTIVE_PROFILE_SHA256_V023,
    build_product_v023_environment_payload,
)
from ecomsre.product.connectors.pilot_runtime import (
    PilotRuntimeSnapshotV02,
    write_pilot_runtime_snapshot_v02,
)
from ecomsre.product.contracts import EnvironmentRecordV1, ServiceIdentityMapV1
from ecomsre.product.environment.capabilities import EnvironmentCapabilityMatrixV1
from ecomsre.product.jobs.contracts import (
    ProductJobRecordV1,
    ProductJobStatusV1,
    ProductJobTypeV1,
)
from ecomsre.product.jobs.repository import JobRepositoryV1
from ecomsre.product.pilot.baseline_attempts_v023 import (
    BASELINE_READINESS_BLOCKED_V023,
    BASELINE_READINESS_PASS_V023,
    BASELINE_REPAIR_REQUIRED_V023,
    BaselineAttemptCompletionV023,
    BaselineAttemptFailureKindV023,
    BaselineAttemptLedgerV023,
    BaselineAttemptStartV023,
    BaselineAttemptV023,
    BaselineChangedParameterV023,
    BaselineTrafficResultV023,
    baseline_builder_job_evidence_sha256_v023,
    baseline_builder_interruption_evidence_sha256_v023,
    baseline_builder_submission_failure_evidence_sha256_v023,
    baseline_builder_transport_failure_evidence_sha256_v023,
    validate_changed_attempt_parameter_v023,
)
from ecomsre.product.pilot.baseline_readiness_v021 import (
    BoundedHealthyCheckoutTrafficV021,
    HealthyTrafficProfileV021,
    verify_queue_default_v021,
)
from ecomsre.product.pilot.baseline_readiness_v023 import (
    ProductBaselineReadinessAuditV023,
    ProductBaselineReadinessAuditRepositoryV023,
    ProductBaselineReadinessProfileV023,
)
from ecomsre.product.pilot.baseline_restart_v023 import (
    BaselineRestartProofV023,
    BaselineRestartSnapshotV023,
)
from ecomsre.product.pilot.live_calibration_v02 import (
    _authority_inputs,
    _runtime_services,
)
from ecomsre.product.pilot.runtime_authority_v02 import (
    PilotRuntimeAuthorityV02,
    write_pilot_runtime_authority_v02,
)
from ecomsre.product.storage.sqlite_store import SqliteStoreV1
from scripts.ci.verify_product_v023_increment2 import verify_product_v023_increment2


PRODUCT_API_PORT_V023 = 18081
PRODUCT_V023_PROFILE_PATH = Path("config/product-v023/baseline-readiness/profile.json")
PRODUCT_V023_ENVIRONMENT_PATH = Path("config/product-v023/environment.otel-demo.json")
PRODUCT_V023_RESULT_PATH = Path("docs/analysis/product-v023-baseline-readiness.json")
PRODUCT_V023_RESULT_MARKDOWN_PATH = Path(
    "docs/analysis/product-v023-baseline-readiness.md"
)
PRODUCT_V023_RESTART_PATH = Path("docs/analysis/product-v023-baseline-restart.json")
_FINAL_JOB_STATUSES = frozenset({"SUCCEEDED", "FAILED", "CANCELLED"})
BASELINE_RESTART_BLOCKED_V023 = "BLOCKED_ECOMSRE_PRODUCT_V023_BASELINE_RESTART"


class _JobTimeoutV023(RuntimeError):
    def __init__(self, job_id: str) -> None:
        self.job_id = job_id
        super().__init__(f"Product v0.2.3 job did not terminate: {job_id}")


class _RetryableTransportV023(RuntimeError):
    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        super().__init__(detail)


class _TransportRetriesExhaustedV023(RuntimeError):
    def __init__(self, failure_codes: tuple[str, ...]) -> None:
        self.failure_codes = failure_codes
        self.retry_count = len(failure_codes) - 1
        super().__init__(
            "Product v0.2.3 same-request transport retries exhausted: "
            + ",".join(failure_codes)
        )


def _canonical_json(payload: Mapping[str, object]) -> str:
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def _atomic_write(path: Path, content: str, *, create_once: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if create_once:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            os.write(descriptor, content.encode("utf-8"))
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        return
    temporary = path.parent / f".{path.name}.product-v023.tmp"
    descriptor = os.open(
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
        0o600,
    )
    try:
        os.write(descriptor, content.encode("utf-8"))
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    os.replace(temporary, path)


def _write_json(
    path: Path, payload: Mapping[str, object], *, create_once: bool = False
) -> None:
    _atomic_write(path, _canonical_json(payload), create_once=create_once)


def _object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Product v0.2.3 object is invalid: {path}")
    return payload


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _sleep_until_utc(target: datetime) -> None:
    remaining = (target - datetime.now(UTC)).total_seconds()
    if remaining > 0:
        time.sleep(remaining)


def _require_clean_head(root: Path) -> str:
    runner = AuditedSubprocessRunner(
        project_root=root,
        artifacts_root=(root / ".local/product-v023/baseline-readiness/git-preflight"),
        run_id=secrets.token_hex(16),
    )
    completed = runner.run(
        ("git", "status", "--porcelain=v1", "--untracked-files=all"),
        timeout_seconds=15,
    )
    if completed.exit_code != 0:
        raise RuntimeError("Product v0.2.3 cannot inspect the live execution tree")
    if completed.stdout:
        raise RuntimeError("Product v0.2.3 live execution requires a clean HEAD")
    head_result = runner.run(
        ("git", "rev-parse", "HEAD"),
        timeout_seconds=15,
    )
    if head_result.exit_code != 0:
        raise RuntimeError("Product v0.2.3 cannot resolve the live execution HEAD")
    head = head_result.stdout.strip()
    if len(head) != 40:
        raise RuntimeError("Product v0.2.3 execution HEAD is invalid")
    return head


def _implementation_revision_sha256(root: Path) -> str:
    # The environment builder is measured by the query/alias binding dimensions.
    binding_source = (
        root / "src/ecomsre/product/connectors/opensearch_profile_binding_v023.py"
    ).resolve()
    paths = tuple(
        sorted(
            (
                *root.glob("src/ecomsre/product/**/*.py"),
                *root.glob("src/ecomsre_live_sandbox/**/*.py"),
                *root.glob("scripts/product_v023/**/*.py"),
            ),
            key=lambda path: path.relative_to(root).as_posix(),
        )
    )
    if not paths:
        raise RuntimeError("Product v0.2.3 implementation scope is empty")
    file_digests: list[dict[str, str]] = []
    for path in paths:
        if path.resolve() == binding_source:
            module = ast.parse(path.read_text(encoding="utf-8"))
            module.body = [
                node
                for node in module.body
                if not (
                    isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and node.name == "build_product_v023_environment_payload"
                )
            ]
            digest = hashlib.sha256(
                ast.dump(module, include_attributes=False).encode("utf-8")
            ).hexdigest()
        else:
            digest = _file_sha256(path)
        file_digests.append(
            {
                "path": path.relative_to(root).as_posix(),
                "sha256": digest,
            }
        )
    return semantic_sha256_v22(file_digests)


def planned_baseline_windows_v023(started_at: datetime) -> tuple[dict[str, str], ...]:
    """Plan the five 36-second evidence windows before any healthy traffic."""

    if started_at.tzinfo is None or started_at.utcoffset() != timedelta(0):
        raise ValueError("Product v0.2.3 live attempt start must be UTC")
    return tuple(
        {
            "started_at": (started_at + timedelta(seconds=36 * index)).isoformat(),
            "ended_at": (started_at + timedelta(seconds=36 * (index + 1))).isoformat(),
        }
        for index in range(5)
    )


def attempt_semantic_inputs_v023(
    *,
    root: Path,
    environment_payload: Mapping[str, object],
) -> dict[str, str]:
    connectors = environment_payload.get("connector_configs")
    identity = environment_payload.get("service_identity_policy")
    if not isinstance(connectors, list) or not isinstance(identity, dict):
        raise ValueError("Product v0.2.3 live environment bindings are incomplete")
    query_bindings = [
        {
            "name": item.get("name"),
            "kind": item.get("kind"),
            "endpoint": item.get("endpoint"),
            "settings": item.get("settings"),
        }
        for item in connectors
        if isinstance(item, dict) and item.get("kind") != "PILOT_RUNTIME"
    ]
    return {
        "connector_query_binding_sha256": semantic_sha256_v22(query_bindings),
        "service_alias_binding_sha256": semantic_sha256_v22(identity),
        "implementation_revision_sha256": _implementation_revision_sha256(root),
    }


def _require_port_available(port: int) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind(("127.0.0.1", port))
        except OSError as error:
            raise RuntimeError(
                f"Product v0.2.3 API port {port} is unavailable"
            ) from error


class _ProductHostProcessesV023:
    """Own exactly the API and Worker processes spawned for this campaign."""

    def __init__(self, *, root: Path, data_root: Path, private_root: Path) -> None:
        self.root = root
        self.data_root = data_root
        self.private_root = private_root
        self.token = secrets.token_urlsafe(32)
        self.api: subprocess.Popen[bytes] | None = None
        self.worker: subprocess.Popen[bytes] | None = None
        self.api_instance_id: str | None = None
        self.worker_instance_id: str | None = None
        self._launch_ordinal = 0
        self._logs: list[Any] = []
        self.launches: list[dict[str, object]] = []

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{PRODUCT_API_PORT_V023}"

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}

    def _environment(self) -> dict[str, str]:
        environment = dict(os.environ)
        environment.update(
            {
                "ECOMSRE_ADMIN_TOKEN": self.token,
                "ECOMSRE_PRODUCT_API_HOST": "127.0.0.1",
                "ECOMSRE_PRODUCT_API_PORT": str(PRODUCT_API_PORT_V023),
                "ECOMSRE_PRODUCT_DATA_ROOT": str(self.data_root),
                "ECOMSRE_PRODUCT_PILOT_RUNTIME_AUTHORITY_PATH": str(
                    self.data_root / "pilot/runtime-authority.json"
                ),
            }
        )
        return environment

    @staticmethod
    def _instance_id(prefix: str, *, pid: int, ordinal: int) -> str:
        body = f"{prefix}:{pid}:{ordinal}:{time.time_ns()}:{secrets.token_hex(8)}"
        return f"{prefix}-{hashlib.sha256(body.encode()).hexdigest()[:24]}"

    def start(self) -> None:
        if self.api is not None or self.worker is not None:
            raise RuntimeError("Product v0.2.3 processes are already active")
        _require_port_available(PRODUCT_API_PORT_V023)
        self.private_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._launch_ordinal += 1
        api_log = open(  # noqa: SIM115 - Popen owns the descriptors until exit
            self.private_root / f"api-{self._launch_ordinal}.log", "ab", buffering=0
        )
        worker_log = open(  # noqa: SIM115
            self.private_root / f"worker-{self._launch_ordinal}.log", "ab", buffering=0
        )
        self._logs.extend((api_log, worker_log))
        environment = self._environment()
        self.api = subprocess.Popen(
            (sys.executable, "-m", "ecomsre.product.app"),
            cwd=self.root,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=api_log,
            stderr=subprocess.STDOUT,
        )
        self.worker = subprocess.Popen(
            (sys.executable, "-m", "ecomsre.product.jobs.worker"),
            cwd=self.root,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=worker_log,
            stderr=subprocess.STDOUT,
        )
        self.api_instance_id = self._instance_id(
            "api", pid=self.api.pid, ordinal=self._launch_ordinal
        )
        self.worker_instance_id = self._instance_id(
            "worker", pid=self.worker.pid, ordinal=self._launch_ordinal
        )
        self.launches.append(
            {
                "launch_ordinal": self._launch_ordinal,
                "api_pid": self.api.pid,
                "worker_pid": self.worker.pid,
                "api_instance_id": self.api_instance_id,
                "worker_instance_id": self.worker_instance_id,
            }
        )
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            if self.api.poll() is not None or self.worker.poll() is not None:
                self.stop()
                raise RuntimeError("Product v0.2.3 process exited during startup")
            try:
                response = httpx.get(f"{self.base_url}/readyz", timeout=2)
                if (
                    response.status_code == 200
                    and response.json().get("status") == "ready"
                ):
                    return
            except (httpx.HTTPError, ValueError):
                pass
            time.sleep(0.25)
        self.stop()
        raise RuntimeError("Product v0.2.3 API did not become ready")

    def stop(self) -> None:
        processes = tuple(item for item in (self.worker, self.api) if item is not None)
        for process in processes:
            if process.poll() is None:
                process.terminate()
        for process in processes:
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        self.api = None
        self.worker = None
        for handle in self._logs:
            handle.close()
        self._logs.clear()

    def restart(self) -> None:
        self.stop()
        self.start()

    def cleanup_observation(self) -> dict[str, object]:
        """Stop only owned host processes and retain exact cleanup evidence."""

        safe_error: str | None = None
        try:
            self.stop()
            _require_port_available(PRODUCT_API_PORT_V023)
        except BaseException as error:
            safe_error = f"{type(error).__name__}: {error}"[:1000]
        remaining = sum(
            process is not None and process.poll() is None
            for process in (self.api, self.worker)
        )
        clean = safe_error is None and remaining == 0
        return {
            "schema_version": "ecomsre.product.host-process-cleanup.v023",
            "verdict": "CLEAN" if clean else "BLOCKED",
            "owned_host_processes": remaining,
            "product_api_port": PRODUCT_API_PORT_V023,
            "product_api_port_available": safe_error is None,
            "launches": tuple(self.launches),
            "non_owned_resources_changed": False if clean else None,
            "safe_error": safe_error,
        }


def _request_json(
    processes: _ProductHostProcessesV023,
    method: str,
    path: str,
    *,
    payload: object | None = None,
    extra_headers: Mapping[str, str] | None = None,
    timeout: float = 30,
) -> dict[str, Any]:
    headers = dict(processes.headers)
    if extra_headers is not None:
        headers.update(extra_headers)
    try:
        response = httpx.request(
            method,
            f"{processes.base_url}{path}",
            headers=headers,
            json=payload,
            timeout=timeout,
        )
    except httpx.TimeoutException as error:
        raise _RetryableTransportV023("TIMEOUT", str(error)) from error
    except (httpx.NetworkError, httpx.RemoteProtocolError) as error:
        raise _RetryableTransportV023("CONNECTION_RESET", str(error)) from error
    if response.status_code == 429:
        raise _RetryableTransportV023(
            "HTTP_429",
            f"Product API rate limited: {method} {path}",
        )
    if response.status_code >= 500:
        raise _RetryableTransportV023(
            "HTTP_5XX",
            f"Product API server failure: {method} {path} status={response.status_code}",
        )
    if response.status_code < 200 or response.status_code >= 300:
        raise RuntimeError(
            f"Product API request failed: {method} {path} "
            f"status={response.status_code} body={response.text[:500]}"
        )
    value = response.json()
    if not isinstance(value, dict):
        raise RuntimeError("Product v0.2.3 API response is not an object")
    return value


def _request_json_with_transport_retries_v023(
    processes: _ProductHostProcessesV023,
    method: str,
    path: str,
    *,
    payload: object | None = None,
    extra_headers: Mapping[str, str] | None = None,
    timeout: float = 30,
    maximum_retries: int = 3,
) -> dict[str, Any]:
    """Retry only the Goal-authorized transport classes for the same request."""

    failure_codes: list[str] = []
    for request_ordinal in range(maximum_retries + 1):
        try:
            return _request_json(
                processes,
                method,
                path,
                payload=payload,
                extra_headers=extra_headers,
                timeout=timeout,
            )
        except _RetryableTransportV023 as error:
            failure_codes.append(error.code)
            if request_ordinal >= maximum_retries:
                raise _TransportRetriesExhaustedV023(tuple(failure_codes)) from error
            time.sleep(min(0.25 * (2**request_ordinal), 1.0))
    raise AssertionError("transport retry loop did not terminate")


def _wait_job(
    processes: _ProductHostProcessesV023,
    job_id: str,
    *,
    data_root: Path | None = None,
    timeout_seconds: float = 180,
) -> ProductJobRecordV1:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            payload = _request_json_with_transport_retries_v023(
                processes,
                "GET",
                f"/v1/jobs/{job_id}",
            )
            job = ProductJobRecordV1.model_validate(payload)
        except Exception:
            if data_root is None:
                raise
            job = _read_job_from_store_v023(data_root, job_id)
        if job.status.value in _FINAL_JOB_STATUSES:
            return job
        time.sleep(0.5)
    raise _JobTimeoutV023(job_id)


def _read_job_from_store_v023(data_root: Path, job_id: str) -> ProductJobRecordV1:
    return JobRepositoryV1(SqliteStoreV1(data_root / "product.sqlite3")).get(job_id)


def _read_latest_baseline_audit_from_store_v023(
    data_root: Path,
    environment_id: str,
) -> ProductBaselineReadinessAuditV023:
    store = SqliteStoreV1(data_root / "product.sqlite3")
    return ProductBaselineReadinessAuditRepositoryV023(store).get_latest(environment_id)


def _recover_baseline_job_by_idempotency_key_v023(
    data_root: Path,
    *,
    environment_id: str,
    idempotency_key: str,
) -> ProductJobRecordV1 | None:
    """Recover an ambiguously acknowledged local submission without resubmitting it."""

    full_key = f"baseline-build:{environment_id}:{idempotency_key}"
    store = SqliteStoreV1(data_root / "product.sqlite3")
    with store.connect() as connection:
        row = connection.execute(
            "SELECT job_id FROM diagnosis_jobs "
            "WHERE job_type = ? AND idempotency_key = ?",
            (ProductJobTypeV1.BASELINE_BUILD.value, full_key),
        ).fetchone()
    if row is None:
        return None
    return JobRepositoryV1(store).get(str(row["job_id"]))


def _observe_baseline_submission_after_stability_v023(
    data_root: Path,
    *,
    environment_id: str,
    idempotency_key: str,
    observation_count: int = 8,
    interval_seconds: float = 0.25,
) -> ProductJobRecordV1 | None:
    """Avoid claiming NOT_SUBMITTED while the local enqueue may still commit."""

    if observation_count < 2:
        raise ValueError("baseline submission stability requires repeated observation")
    for ordinal in range(observation_count):
        job = _recover_baseline_job_by_idempotency_key_v023(
            data_root,
            environment_id=environment_id,
            idempotency_key=idempotency_key,
        )
        if job is not None:
            return job
        if ordinal + 1 < observation_count:
            time.sleep(interval_seconds)
    return None


def _refresh_job_after_timeout_v023(
    processes: _ProductHostProcessesV023,
    job_id: str,
    *,
    data_root: Path | None = None,
) -> tuple[ProductJobRecordV1, bool]:
    """Return the boundary observation and whether the wait truly timed out."""

    try:
        job = ProductJobRecordV1.model_validate(
            _request_json_with_transport_retries_v023(
                processes,
                "GET",
                f"/v1/jobs/{job_id}",
            )
        )
    except Exception:
        if data_root is None:
            raise
        job = _read_job_from_store_v023(data_root, job_id)
    still_incomplete = job.status in {
        ProductJobStatusV1.PENDING,
        ProductJobStatusV1.RUNNING,
    }
    return job, still_incomplete


def _queue_counts(data_root: Path) -> tuple[int, int, int]:
    store = SqliteStoreV1(data_root / "product.sqlite3")
    with store.connect() as connection:
        rows = connection.execute(
            "SELECT status, COUNT(*) AS count FROM diagnosis_jobs GROUP BY status"
        ).fetchall()
    counts = {str(row["status"]): int(row["count"]) for row in rows}
    return (
        counts.get("PENDING", 0),
        counts.get("RUNNING", 0),
        counts.get("FAILED", 0),
    )


def _baseline_built_passes_v023(
    job: ProductJobRecordV1 | None,
    audit: ProductBaselineReadinessAuditV023 | None,
) -> bool:
    if (
        job is None
        or job.status is not ProductJobStatusV1.SUCCEEDED
        or not isinstance(job.result, dict)
        or audit is None
        or not audit.final_builder_would_pass
        or audit.baseline_sha256 is None
    ):
        return False
    return job.result.get("active") is True


def _failed_builder_kind_v023(
    job: ProductJobRecordV1,
    audit: ProductBaselineReadinessAuditV023,
) -> BaselineAttemptFailureKindV023:
    window_reasons = {
        reason.value
        for window in audit.evaluation.windows
        for reason in window.rejection_reason_codes
    }
    opensearch_rejections = {
        code
        for window in audit.evaluation.windows
        for code in window.opensearch_rejection_codes
    }
    aggregate_reasons = set(audit.evaluation.aggregate_rejection_reason_codes)
    alias_code = "OPENSEARCH_SERVICE_ALIAS_UNMAPPED"
    alias_only_window_reasons = {
        "OPENSEARCH_REJECTION_FRACTION_EXCEEDED",
        "OPENSEARCH_REQUIRED_EXTRACTION_FAILED",
    }
    alias_only_aggregate_reasons = {
        "MINIMUM_ACCEPTED_WINDOWS_NOT_MET",
        "LOGS_NONEMPTY_WINDOW_MINIMUM_NOT_MET",
        "CHECKOUT_LOG_RECORD_MINIMUM_NOT_MET",
        "NORMAL_CHECKOUT_LOG_TEMPLATE_MISSING",
        "OPENSEARCH_ALL_WINDOWS_QUALITY_NOT_MET",
    }
    if alias_code in opensearch_rejections and (
        opensearch_rejections != {alias_code}
        or not window_reasons.issubset(alias_only_window_reasons)
        or not aggregate_reasons.issubset(alias_only_aggregate_reasons)
    ):
        return BaselineAttemptFailureKindV023.IMPLEMENTATION
    if opensearch_rejections == {alias_code}:
        return BaselineAttemptFailureKindV023.SERVICE_ALIAS_BINDING
    if (
        any(
            reason.startswith(("OPENSEARCH_", "PROMETHEUS_", "METRICS_"))
            for reason in window_reasons
        )
        or opensearch_rejections
    ):
        return BaselineAttemptFailureKindV023.CONNECTOR_QUERY_BINDING
    if aggregate_reasons:
        return BaselineAttemptFailureKindV023.IMPLEMENTATION
    return BaselineAttemptFailureKindV023.BUILDER


def _failure_code_v023(
    kind: BaselineAttemptFailureKindV023,
    job: ProductJobRecordV1,
) -> str:
    if kind is BaselineAttemptFailureKindV023.CONNECTOR_QUERY_BINDING:
        return "CONNECTOR_QUERY_BINDING_INVALID"
    if kind is BaselineAttemptFailureKindV023.SERVICE_ALIAS_BINDING:
        return "SERVICE_ALIAS_BINDING_INVALID"
    return job.safe_error_code or "BASELINE_BUILDER_FAILED"


def _restart_snapshot(
    processes: _ProductHostProcessesV023,
    *,
    environment_id: str,
    service_identity_sha256: str,
    capability_sha256: str,
) -> BaselineRestartSnapshotV023:
    environment = EnvironmentRecordV1.model_validate(
        _request_json(processes, "GET", f"/v1/environments/{environment_id}")
    )
    baselines = _request_json(
        processes, "GET", f"/v1/environments/{environment_id}/baselines"
    ).get("items")
    if not isinstance(baselines, list) or len(baselines) != 1:
        raise RuntimeError("Product v0.2.3 active Baseline set differs")
    baseline = baselines[0]
    if not isinstance(baseline, dict) or baseline.get("active") is not True:
        raise RuntimeError("Product v0.2.3 Baseline is not active")
    if processes.api_instance_id is None or processes.worker_instance_id is None:
        raise RuntimeError("Product v0.2.3 process instance IDs are absent")
    pending, running, failed = _queue_counts(processes.data_root)
    return BaselineRestartSnapshotV023.build(
        environment_id=environment_id,
        environment_payload_sha256=semantic_sha256_v22(
            environment.model_dump(mode="json")
        ),
        profile_binding_sha256=ACTIVE_PROFILE_BINDING_SHA256_V023,
        profile_sha256=ACTIVE_PROFILE_SHA256_V023,
        active_baseline_id=str(baseline["baseline_id"]),
        active_baseline_sha256=str(baseline["baseline_sha256"]),
        baseline_count=1,
        service_identity_sha256=service_identity_sha256,
        capability_sha256=capability_sha256,
        api_instance_id=processes.api_instance_id,
        worker_instance_id=processes.worker_instance_id,
        observed_at=datetime.now(UTC),
        pending_jobs=pending,
        running_jobs=running,
        failed_jobs=failed,
    )


def verify_live_baseline_readiness_contract_v023(root: Path) -> dict[str, object]:
    repository = root.resolve(strict=True)
    increment2 = verify_product_v023_increment2(repository)
    profile = ProductBaselineReadinessProfileV023.load(
        repository / PRODUCT_V023_PROFILE_PATH
    )
    environment = build_product_v023_environment_payload(
        repository_root=repository,
        runtime_authority_sha256="0" * 64,
    )
    semantics = attempt_semantic_inputs_v023(
        root=repository, environment_payload=environment
    )
    attempts = _load_public_attempts(repository)
    return {
        "terminal": "ECOMSRE_PRODUCT_V023_LIVE_BASELINE_CONTRACT_READY",
        "profile_sha256": profile.profile_sha256,
        "active_profile_sha256": ACTIVE_PROFILE_SHA256_V023,
        "connector_query_binding_sha256": semantics["connector_query_binding_sha256"],
        "service_alias_binding_sha256": semantics["service_alias_binding_sha256"],
        "increment2_terminal": increment2["terminal"],
        "baseline_attempt_count": len(attempts),
        "fault_attempt_count": 0,
        "action_authority": "NONE",
    }


def _render_markdown(payload: Mapping[str, object]) -> str:
    return "\n".join(
        (
            "# Product v0.2.3 Fresh Baseline Readiness",
            "",
            f"Terminal: `{payload['terminal']}`",
            "",
            f"- attempt count: `{payload['baseline_attempt_count']}`",
            f"- environment: `{payload.get('environment_id')}`",
            f"- active Baseline: `{payload.get('active_baseline_id')}`",
            f"- Baseline SHA: `{payload.get('active_baseline_sha256')}`",
            f"- readiness audit SHA: `{payload.get('readiness_audit_sha256')}`",
            f"- parity SHA: `{payload.get('parity_sha256')}`",
            f"- restart proof: `{payload.get('restart_proof_sha256')}`",
            f"- Product cleanup: `{payload.get('product_cleanup')}`",
            f"- Demo cleanup: `{payload.get('demo_cleanup')}`",
            "- fault attempts / Knowledge campaigns: `0 / 0`",
            "- action authority / Agent writes / Runbooks: `NONE / 0 / 0`",
            "",
        )
    )


def _load_public_attempts(root: Path) -> tuple[BaselineAttemptV023, ...]:
    paths = tuple(
        sorted((root / "docs/analysis").glob("product-v023-baseline-attempt-*.json"))
    )
    attempts = tuple(
        BaselineAttemptV023.model_validate(_object(path)["attempt"]) for path in paths
    )
    if attempts:
        BaselineAttemptLedgerV023.build(attempts)
    return attempts


def run_live_baseline_readiness_v023(
    *,
    repository_root: Path,
    changed_parameter: BaselineChangedParameterV023 | None = None,
) -> dict[str, object]:
    """Consume one of at most two changed fresh Baseline attempts."""

    root = repository_root.resolve(strict=True)
    verify_live_baseline_readiness_contract_v023(root)
    execution_head = _require_clean_head(root)
    prior_attempts = _load_public_attempts(root)
    private_starts = tuple(
        (root / ".local/product-v023/baseline-readiness/runs").glob(
            "*/private/attempt-start.json"
        )
    )
    if len(private_starts) != len(prior_attempts):
        raise ValueError("Product v0.2.3 has an unfinished private Baseline attempt")
    if len(prior_attempts) >= 2:
        raise ValueError("Product v0.2.3 Baseline attempt budget is exhausted")
    ordinal = len(prior_attempts) + 1
    if ordinal == 1:
        selected_change = changed_parameter or BaselineChangedParameterV023.INITIAL
        if selected_change is not BaselineChangedParameterV023.INITIAL:
            raise ValueError("first Product v0.2.3 Baseline attempt must be INITIAL")
    else:
        if changed_parameter in {None, BaselineChangedParameterV023.INITIAL}:
            raise ValueError("second Product v0.2.3 Baseline attempt needs one repair")
        selected_change = cast(BaselineChangedParameterV023, changed_parameter)

    preflight_environment = build_product_v023_environment_payload(
        repository_root=root,
        runtime_authority_sha256="0" * 64,
    )
    preflight_semantics = attempt_semantic_inputs_v023(
        root=root,
        environment_payload=preflight_environment,
    )
    if prior_attempts:
        prior = prior_attempts[-1]
        validate_changed_attempt_parameter_v023(
            prior_completion=prior.completion,
            changed_parameter=selected_change,
        )
        candidate_semantics = dict(prior.start.semantic_inputs)
        candidate_semantics.update(preflight_semantics)
        changed = tuple(
            sorted(
                key
                for key in set(candidate_semantics).union(prior.start.semantic_inputs)
                if candidate_semantics.get(key) != prior.start.semantic_inputs.get(key)
            )
        )
        if changed != (selected_change.value,):
            raise ValueError(
                "second Product v0.2.3 Baseline attempt must change exactly its repair"
            )

    profile = ProductBaselineReadinessProfileV023.load(root / PRODUCT_V023_PROFILE_PATH)
    campaign_root = root / ".local/product-v023/baseline-readiness"
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%S-") + secrets.token_hex(4)
    attempt_root = campaign_root / "runs" / run_id
    private_root = attempt_root / "private"
    product_data_root = attempt_root / "product"
    if attempt_root.exists():
        raise ValueError("Product v0.2.3 private attempt root already exists")
    private_root.mkdir(parents=True, mode=0o700)
    lifecycle = _SandboxOwnedSmokeLifecycle(
        repository_root=root,
        private_root=private_root / "demo",
        stabilization_seconds=30,
    )
    processes = _ProductHostProcessesV023(
        root=root,
        data_root=product_data_root,
        private_root=private_root / "product-processes",
    )
    start: BaselineAttemptStartV023 | None = None
    traffic_result: BaselineTrafficResultV023 | None = None
    job: ProductJobRecordV1 | None = None
    builder_wait_timed_out = False
    builder_submission_not_observed = False
    builder_transport_failure_codes: tuple[str, ...] = ()
    builder_idempotency_key: str | None = None
    builder_execution_interrupted = False
    builder_late_acknowledged = False
    builder_failure_audit_missing = False
    audit: ProductBaselineReadinessAuditV023 | None = None
    restart_proof: BaselineRestartProofV023 | None = None
    environment_id: str | None = None
    identity_sha256: str | None = None
    capability_sha256: str | None = None
    queue_before_sha256: str | None = None
    queue_default_unchanged = False
    outer_baseline_before_sha256: str | None = None
    outer_baseline_unchanged = False
    error: BaseException | None = None
    closure_errors: list[str] = []
    demo_cleanup_observation: dict[str, object] = (
        CleanupObservation.unknown_blocked().model_dump(mode="json")
    )
    product_cleanup_observation: dict[str, object] = {
        "schema_version": "ecomsre.product.host-process-cleanup.v023",
        "verdict": "BLOCKED",
        "owned_host_processes": None,
        "product_api_port": PRODUCT_API_PORT_V023,
        "product_api_port_available": None,
        "launches": (),
        "non_owned_resources_changed": None,
        "safe_error": "cleanup not yet observed",
    }
    stage = "PREFLIGHT"
    try:
        lifecycle.admit()
        stage = "ADMITTED"
        if lifecycle.flag_file is None:
            raise RuntimeError("Product v0.2.3 owned queue file is absent")
        queue_before = verify_queue_default_v021(
            lifecycle.flag_file, expected_default_value=profile.queue_fault_flag
        )
        queue_before_sha256 = queue_before.before_sha256
        lifecycle.start()
        lifecycle.wait_ready()
        backend = cast(LocalSandboxReadBackend, lifecycle.authorize_reads())
        outer_baseline_before_sha256 = lifecycle.read_baseline_sha256()
        stage = "DEMO_READY"
        authority_inputs = _authority_inputs(backend)
        prebound = PilotRuntimeAuthorityV02.build(
            environment_id="env-" + "0" * 24,
            allowed_logical_services=("checkout",),
            profile_sha256=profile.profile_sha256,
            **authority_inputs,
        )
        environment_payload = build_product_v023_environment_payload(
            repository_root=root,
            runtime_authority_sha256=prebound.connector_binding_sha256,
        )
        processes.start()
        environment = _request_json(
            processes, "POST", "/v1/environments", payload=environment_payload
        )
        environment_id = str(environment["environment_id"])
        authority = PilotRuntimeAuthorityV02.build(
            environment_id=environment_id,
            allowed_logical_services=("checkout",),
            profile_sha256=profile.profile_sha256,
            **authority_inputs,
        )
        if authority.connector_binding_sha256 != prebound.connector_binding_sha256:
            raise RuntimeError("Product v0.2.3 Runtime binding changed")
        write_pilot_runtime_authority_v02(
            product_data_root / "pilot/runtime-authority.json", authority
        )
        runtime_services, runtime_drift = _runtime_services(
            backend, run_id=secrets.token_hex(16)
        )
        if runtime_drift:
            raise RuntimeError("Product v0.2.3 Runtime preflight is unhealthy")
        write_pilot_runtime_snapshot_v02(
            product_data_root / "pilot/runtime-readiness.json",
            PilotRuntimeSnapshotV02.build(
                environment_id=environment_id,
                authority_sha256=authority.connector_binding_sha256,
                observed_at=datetime.now(UTC),
                services=runtime_services,
            ),
        )
        queued_verify = _request_json(
            processes,
            "POST",
            f"/v1/environments/{environment_id}/verify-jobs",
            payload=None,
        )
        verified_job = _wait_job(
            processes,
            str(queued_verify["job_id"]),
            data_root=product_data_root,
        )
        if verified_job.status is not ProductJobStatusV1.SUCCEEDED or not isinstance(
            verified_job.result, dict
        ):
            raise RuntimeError("Product v0.2.3 connector verification failed")
        verification = verified_job.result
        identity = verification.get("service_identity_map")
        capability = verification.get("capability_matrix")
        if not isinstance(identity, dict) or not isinstance(capability, dict):
            raise RuntimeError("Product v0.2.3 verification bindings are incomplete")
        identity_map = ServiceIdentityMapV1.model_validate(identity)
        capability_matrix = EnvironmentCapabilityMatrixV1.model_validate(capability)
        if (
            identity_map.environment_id != environment_id
            or capability_matrix.environment_id != environment_id
        ):
            raise RuntimeError("Product v0.2.3 verification environment differs")
        identity_sha256 = identity_map.identity_sha256
        capability_sha256 = capability_matrix.capability_sha256
        stage = "CONNECTORS_VERIFIED"
        started_at = datetime.now(UTC)
        planned_windows = planned_baseline_windows_v023(started_at)
        semantics = attempt_semantic_inputs_v023(
            root=root, environment_payload=environment_payload
        )
        if semantics != preflight_semantics:
            raise RuntimeError("Product v0.2.3 preflight semantic binding changed")
        start = BaselineAttemptStartV023.build(
            attempt_ordinal=ordinal,
            changed_parameter=selected_change,
            prior_completion_sha256=(
                None
                if not prior_attempts
                else prior_attempts[-1].completion.completion_sha256
            ),
            environment_id=environment_id,
            product_data_root=str(product_data_root),
            profile_sha256=ACTIVE_PROFILE_SHA256_V023,
            planned_windows=planned_windows,
            semantic_inputs=semantics,
            started_at=started_at,
        )
        _write_json(
            private_root / "attempt-start.json",
            start.model_dump(mode="json"),
            create_once=True,
        )
        _write_json(root / PRODUCT_V023_ENVIRONMENT_PATH, environment_payload)
        stage = "ATTEMPT_RESERVED"
        traffic_profile = HealthyTrafficProfileV021(
            request_seed=23082900 + ordinal,
            maximum_request_count=profile.healthy_traffic_request_count,
            requests_per_second=profile.healthy_traffic_requests_per_second,
            error_budget=max(
                1,
                int(
                    profile.healthy_traffic_request_count
                    * profile.maximum_error_fraction
                )
                + 1,
            ),
        )
        with httpx.Client() as traffic_client:
            measured = BoundedHealthyCheckoutTrafficV021(client=traffic_client).run(
                endpoint="http://127.0.0.1:18080/api/checkout",
                profile=traffic_profile,
            )
        traffic_result = BaselineTrafficResultV023.build(
            planned_request_count=profile.healthy_traffic_request_count,
            completed_request_count=measured.attempted,
            error_count=measured.failed,
            requests_per_second=profile.healthy_traffic_requests_per_second,
            maximum_error_fraction=profile.maximum_error_fraction,
            queue_fault_flag=profile.queue_fault_flag,
            profile_sha256=ACTIVE_PROFILE_SHA256_V023,
            semantics_sha256=start.semantics_sha256,
            passed=(
                measured.attempted == profile.healthy_traffic_request_count
                and measured.failed / max(1, measured.attempted)
                <= profile.maximum_error_fraction
            ),
        )
        if not traffic_result.passed:
            raise RuntimeError("HEALTHY_TRAFFIC_INCOMPLETE")
        stage = "TRAFFIC_COMPLETE"
        _sleep_until_utc(
            start.planned_windows[-1].ended_at
            + timedelta(seconds=profile.warmup_seconds)
        )
        builder_request = {
            "build_policy": {
                "mode": profile.mode,
                "lookback_seconds": profile.lookback_seconds,
                "window_count": profile.window_count,
                "minimum_successful_windows": profile.minimum_accepted_windows,
                "warmup_seconds": profile.warmup_seconds,
            },
            "candidate_services": list(profile.candidate_services),
            "planned_windows": [
                item.model_dump(mode="json") for item in start.planned_windows
            ],
            "activate": True,
        }
        builder_idempotency_key = (
            f"product-v023-attempt-{ordinal}-{start.start_sha256[:24]}"
        )
        stage = "BUILDER_SUBMITTING"
        try:
            queued = _request_json_with_transport_retries_v023(
                processes,
                "POST",
                f"/v1/environments/{environment_id}/baseline-jobs",
                payload=builder_request,
                extra_headers={"Idempotency-Key": builder_idempotency_key},
            )
            queued_job = ProductJobRecordV1.model_validate(queued)
            expected_idempotency_key = (
                f"baseline-build:{environment_id}:{builder_idempotency_key}"
            )
            if (
                queued_job.job_type is not ProductJobTypeV1.BASELINE_BUILD
                or queued_job.idempotency_key != expected_idempotency_key
            ):
                raise RuntimeError("Baseline Builder acknowledgement binding differs")
            builder_job_id = queued_job.job_id
        except _TransportRetriesExhaustedV023 as transport_error:
            builder_transport_failure_codes = transport_error.failure_codes
            job = _observe_baseline_submission_after_stability_v023(
                product_data_root,
                environment_id=environment_id,
                idempotency_key=builder_idempotency_key,
            )
            if job is None:
                builder_submission_not_observed = True
                raise
            builder_job_id = job.job_id
        except (KeyboardInterrupt, SystemExit):
            builder_execution_interrupted = True
            job = _observe_baseline_submission_after_stability_v023(
                product_data_root,
                environment_id=environment_id,
                idempotency_key=builder_idempotency_key,
            )
            if job is None:
                builder_submission_not_observed = True
            else:
                builder_job_id = job.job_id
            raise
        except Exception:
            job = _observe_baseline_submission_after_stability_v023(
                product_data_root,
                environment_id=environment_id,
                idempotency_key=builder_idempotency_key,
            )
            if job is None:
                builder_submission_not_observed = True
                raise
            builder_job_id = job.job_id
        stage = "BUILDER_SUBMITTED"
        try:
            job = _wait_job(
                processes,
                builder_job_id,
                data_root=product_data_root,
                timeout_seconds=240,
            )
        except _JobTimeoutV023:
            try:
                job, builder_wait_timed_out = _refresh_job_after_timeout_v023(
                    processes,
                    builder_job_id,
                    data_root=product_data_root,
                )
            except (KeyboardInterrupt, SystemExit):
                builder_execution_interrupted = True
                job = _read_job_from_store_v023(product_data_root, builder_job_id)
                raise
            if builder_wait_timed_out:
                raise
        except (KeyboardInterrupt, SystemExit):
            builder_execution_interrupted = True
            job = _read_job_from_store_v023(product_data_root, builder_job_id)
            raise
        except Exception:
            job = _read_job_from_store_v023(product_data_root, builder_job_id)
            builder_wait_timed_out = job.status in {
                ProductJobStatusV1.PENDING,
                ProductJobStatusV1.RUNNING,
            }
            raise
        try:
            audit = ProductBaselineReadinessAuditV023.model_validate(
                _request_json(
                    processes,
                    "GET",
                    f"/v1/environments/{environment_id}/baseline-readiness-v023",
                )
            )
        except RuntimeError:
            audit = None
        if job.status is not ProductJobStatusV1.SUCCEEDED or not isinstance(
            job.result, dict
        ):
            raise RuntimeError(job.safe_error_code or "BASELINE_BUILDER_FAILED")
        raw_audit = job.result.get("readiness_audit_v023")
        if not isinstance(raw_audit, dict):
            raise RuntimeError("BASELINE_V023_AUDIT_MISSING")
        audit = ProductBaselineReadinessAuditV023.model_validate(raw_audit)
        if not audit.final_builder_would_pass:
            raise RuntimeError("BASELINE_V023_PREFLIGHT_BLOCKED")
        if (
            audit.service_identity_sha256 != identity_sha256
            or audit.capability_sha256 != capability_sha256
        ):
            raise RuntimeError("BASELINE_V023_VERIFICATION_BINDING_CHANGED")
        stage = "BASELINE_PASS"
        before = _restart_snapshot(
            processes,
            environment_id=environment_id,
            service_identity_sha256=identity_sha256,
            capability_sha256=capability_sha256,
        )
        processes.restart()
        queued_restart_verify = _request_json(
            processes,
            "POST",
            f"/v1/environments/{environment_id}/verify-jobs",
            payload=None,
        )
        restart_job = _wait_job(
            processes,
            str(queued_restart_verify["job_id"]),
            data_root=product_data_root,
        )
        if restart_job.status is not ProductJobStatusV1.SUCCEEDED or not isinstance(
            restart_job.result, dict
        ):
            raise RuntimeError("BASELINE_RESTART_VERIFY_FAILED")
        restart_identity = restart_job.result.get("service_identity_map")
        restart_capability = restart_job.result.get("capability_matrix")
        if not isinstance(restart_identity, dict) or not isinstance(
            restart_capability, dict
        ):
            raise RuntimeError("BASELINE_RESTART_BINDING_MISSING")
        restart_identity_map = ServiceIdentityMapV1.model_validate(restart_identity)
        restart_capability_matrix = EnvironmentCapabilityMatrixV1.model_validate(
            restart_capability
        )
        if (
            restart_identity_map.environment_id != environment_id
            or restart_capability_matrix.environment_id != environment_id
        ):
            raise RuntimeError("BASELINE_RESTART_VERIFICATION_ENVIRONMENT_CHANGED")
        restart_identity_sha256 = restart_identity_map.identity_sha256
        restart_capability_sha256 = restart_capability_matrix.capability_sha256
        if (
            audit.service_identity_sha256 != restart_identity_sha256
            or audit.capability_sha256 != restart_capability_sha256
        ):
            raise RuntimeError("BASELINE_RESTART_VERIFICATION_BINDING_CHANGED")
        rebound = _request_json(
            processes,
            "GET",
            f"/v1/baselines/{audit.baseline_id}/window-audit-v023",
        )
        if rebound.get("audit_sha256") != audit.audit_sha256:
            raise RuntimeError("BASELINE_RESTART_AUDIT_CHANGED")
        after = _restart_snapshot(
            processes,
            environment_id=environment_id,
            service_identity_sha256=restart_identity_sha256,
            capability_sha256=restart_capability_sha256,
        )
        restart_proof = BaselineRestartProofV023.build(
            before=before,
            after=after,
            connector_verification_count=1,
        )
        _write_json(
            root / PRODUCT_V023_RESTART_PATH,
            restart_proof.model_dump(mode="json"),
        )
        if lifecycle.flag_file is None or queue_before_sha256 is None:
            raise RuntimeError("Product v0.2.3 queue binding is absent")
        verify_queue_default_v021(
            lifecycle.flag_file,
            expected_default_value=0,
            expected_sha256=queue_before_sha256,
        )
        queue_default_unchanged = True
        stage = "RESTART_PASS"
    except BaseException as caught:
        error = caught
    finally:
        if lifecycle.flag_file is not None and queue_before_sha256 is not None:
            try:
                verify_queue_default_v021(
                    lifecycle.flag_file,
                    expected_default_value=profile.queue_fault_flag,
                    expected_sha256=queue_before_sha256,
                )
                queue_default_unchanged = True
            except BaseException as queue_error:
                closure_errors.append(
                    f"queue: {type(queue_error).__name__}: {queue_error}"[:1000]
                )
                if error is None:
                    error = queue_error
        if not outer_baseline_unchanged and outer_baseline_before_sha256 is not None:
            try:
                outer_baseline_unchanged = (
                    lifecycle.read_baseline_sha256() == outer_baseline_before_sha256
                )
                if not outer_baseline_unchanged:
                    raise RuntimeError("Product v0.2.3 Demo outer baseline changed")
            except BaseException as baseline_error:
                closure_errors.append(
                    f"outer-baseline: {type(baseline_error).__name__}: {baseline_error}"[
                        :1000
                    ]
                )
                if error is None:
                    error = baseline_error
        product_cleanup_observation = processes.cleanup_observation()
        if product_cleanup_observation["verdict"] != "CLEAN":
            cleanup_error = RuntimeError("Product v0.2.3 Product cleanup is blocked")
            closure_errors.append(str(cleanup_error))
            if error is None:
                error = cleanup_error
        try:
            cleanup = lifecycle.cleanup_owned(
                baseline_unchanged=outer_baseline_unchanged
            )
            demo_cleanup_observation = cleanup.model_dump(mode="json")
            if cleanup.verdict != "CLEAN":
                raise RuntimeError("Product v0.2.3 Demo cleanup is blocked")
        except BaseException as cleanup_error:
            closure_errors.append(
                f"demo-cleanup: {type(cleanup_error).__name__}: {cleanup_error}"[:1000]
            )
            if error is None:
                error = cleanup_error

    product_cleanup = str(product_cleanup_observation["verdict"])
    demo_cleanup = str(demo_cleanup_observation["verdict"])
    closure_clean = (
        queue_default_unchanged
        and outer_baseline_unchanged
        and product_cleanup == "CLEAN"
        and demo_cleanup == "CLEAN"
    )
    closure_payload: dict[str, object] = {
        "execution_head": execution_head,
        "run_id": run_id,
        "stage": stage,
        "environment_id": environment_id,
        "safe_error_type": None if error is None else type(error).__name__,
        "safe_error": None if error is None else str(error)[:1000],
        "closure_errors": tuple(closure_errors),
        "product_cleanup": product_cleanup,
        "product_cleanup_observation": product_cleanup_observation,
        "demo_cleanup": demo_cleanup,
        "demo_cleanup_observation": demo_cleanup_observation,
        "outer_baseline_before_sha256": outer_baseline_before_sha256,
        "outer_baseline_unchanged": outer_baseline_unchanged,
        "queue_default_before_sha256": queue_before_sha256,
        "queue_default_unchanged": queue_default_unchanged,
        "fault_attempt_count": 0,
        "knowledge_campaign_count": 0,
        "action_authority": "NONE",
        "agent_writes": 0,
        "runbook_executions": 0,
    }
    if start is None:
        _write_json(
            private_root / "preflight-failure.json",
            {
                "schema_version": (
                    "ecomsre.product.private-baseline-preflight-failure.v023"
                ),
                "terminal": BASELINE_READINESS_BLOCKED_V023,
                **closure_payload,
            },
            create_once=True,
        )
        raise RuntimeError(
            f"{BASELINE_READINESS_BLOCKED_V023}: stage={stage}: {error}"
        ) from error

    if (
        job is None
        and builder_submission_not_observed
        and builder_idempotency_key is not None
        and environment_id is not None
    ):
        late_job = _recover_baseline_job_by_idempotency_key_v023(
            product_data_root,
            environment_id=environment_id,
            idempotency_key=builder_idempotency_key,
        )
        if late_job is not None:
            job = late_job
            builder_submission_not_observed = False
            builder_late_acknowledged = job.status in {
                ProductJobStatusV1.PENDING,
                ProductJobStatusV1.RUNNING,
            }
            builder_wait_timed_out = False
            if not builder_late_acknowledged:
                builder_transport_failure_codes = ()
            if job.status is ProductJobStatusV1.SUCCEEDED and isinstance(
                job.result, dict
            ):
                raw_late_audit = job.result.get("readiness_audit_v023")
                if isinstance(raw_late_audit, dict):
                    audit = ProductBaselineReadinessAuditV023.model_validate(
                        raw_late_audit
                    )
            elif job.status is ProductJobStatusV1.FAILED:
                try:
                    audit = _read_latest_baseline_audit_from_store_v023(
                        product_data_root,
                        environment_id,
                    )
                except Exception:
                    builder_failure_audit_missing = True

    if builder_execution_interrupted and job is not None:
        job = _read_job_from_store_v023(product_data_root, job.job_id)

    job_evidence_sha = (
        None if job is None else baseline_builder_job_evidence_sha256_v023(job)
    )
    baseline_built_pass = (
        traffic_result is not None
        and traffic_result.passed
        and _baseline_built_passes_v023(job, audit)
    )
    baseline_id = (
        audit.baseline_id if baseline_built_pass and audit is not None else None
    )
    baseline_sha = (
        audit.baseline_sha256 if baseline_built_pass and audit is not None else None
    )

    if baseline_built_pass and not closure_clean:
        _write_json(
            private_root / "attempt-incomplete.json",
            {
                "schema_version": "ecomsre.product.private-baseline-incomplete.v023",
                "terminal": BASELINE_READINESS_BLOCKED_V023,
                "reason": "BASELINE_BUILT_WITHOUT_CLEAN_CLOSURE",
                "attempt_start": start.model_dump(mode="json"),
                "traffic_result": (
                    None
                    if traffic_result is None
                    else traffic_result.model_dump(mode="json")
                ),
                "builder_job": None if job is None else job.model_dump(mode="json"),
                "readiness_audit": (
                    None if audit is None else audit.model_dump(mode="json")
                ),
                "restart_proof": (
                    None
                    if restart_proof is None
                    else restart_proof.model_dump(mode="json")
                ),
                **closure_payload,
            },
            create_once=True,
        )
        raise RuntimeError(
            f"{BASELINE_READINESS_BLOCKED_V023}: clean closure was not proven"
        ) from error

    if baseline_built_pass:
        assert traffic_result is not None
        assert job is not None
        assert audit is not None
        assert baseline_id is not None
        assert baseline_sha is not None
        attempt_terminal = BASELINE_READINESS_PASS_V023
        completion = BaselineAttemptCompletionV023.build(
            attempt_ordinal=ordinal,
            start_sha256=start.start_sha256,
            traffic_result=traffic_result,
            per_window_audit=audit,
            per_window_audit_sha256=audit.audit_sha256,
            builder_job_id=job.job_id,
            builder_job_record=job,
            builder_job_evidence_sha256=job_evidence_sha,
            builder_job_disposition="SUCCEEDED",
            active_baseline_id=baseline_id,
            active_baseline_sha256=baseline_sha,
            cleanup="CLEAN",
            failure_kind=None,
            failure_code=None,
            failure_evidence_sha256=None,
            terminal=attempt_terminal,
            completed_at=datetime.now(UTC),
        )
        terminal = (
            BASELINE_READINESS_PASS_V023
            if restart_proof is not None and error is None
            else BASELINE_RESTART_BLOCKED_V023
        )
    else:
        if not closure_clean:
            reason = "FAILED_ATTEMPT_WITHOUT_CLEAN_CLOSURE"
        elif traffic_result is None:
            reason = "ATTEMPT_TRAFFIC_RESULT_MISSING"
        elif not traffic_result.passed and job is None:
            reason = None
            failure_kind = BaselineAttemptFailureKindV023.HEALTHY_TRAFFIC
            failure_code = "HEALTHY_TRAFFIC_INCOMPLETE"
            failure_evidence = traffic_result.result_sha256
            disposition = "NOT_SUBMITTED"
        elif builder_execution_interrupted:
            reason = None
            failure_kind = BaselineAttemptFailureKindV023.INTERRUPTED
            failure_code = "BASELINE_ATTEMPT_INTERRUPTED"
            failure_evidence = baseline_builder_interruption_evidence_sha256_v023(
                start_sha256=start.start_sha256,
                traffic_result_sha256=traffic_result.result_sha256,
                builder_job_evidence_sha256=job_evidence_sha,
            )
            disposition = "NOT_SUBMITTED" if job is None else "INTERRUPTED"
        elif builder_transport_failure_codes and (
            job is None or builder_late_acknowledged
        ):
            reason = None
            if builder_idempotency_key is None:
                raise AssertionError("baseline Builder idempotency key is absent")
            failure_kind = BaselineAttemptFailureKindV023.TRANSPORT
            failure_code = "BASELINE_BUILDER_TRANSPORT_RETRIES_EXHAUSTED"
            builder_idempotency_key_sha256 = hashlib.sha256(
                builder_idempotency_key.encode("utf-8")
            ).hexdigest()
            disposition = "NOT_SUBMITTED" if job is None else "LATE_ACKNOWLEDGED"
            failure_evidence = baseline_builder_transport_failure_evidence_sha256_v023(
                start_sha256=start.start_sha256,
                traffic_result_sha256=traffic_result.result_sha256,
                idempotency_key_sha256=builder_idempotency_key_sha256,
                failure_codes=builder_transport_failure_codes,
                retry_count=3,
                builder_job_disposition=disposition,
                builder_job_evidence_sha256=job_evidence_sha,
            )
        elif job is None and builder_submission_not_observed:
            reason = None
            failure_kind = BaselineAttemptFailureKindV023.IMPLEMENTATION
            failure_code = "BASELINE_BUILDER_SUBMISSION_FAILED"
            failure_evidence = baseline_builder_submission_failure_evidence_sha256_v023(
                start_sha256=start.start_sha256,
                traffic_result_sha256=traffic_result.result_sha256,
            )
            disposition = "NOT_SUBMITTED"
        elif (
            job is not None
            and audit is not None
            and job.status is ProductJobStatusV1.FAILED
        ):
            reason = None
            failure_kind = _failed_builder_kind_v023(job, audit)
            failure_code = _failure_code_v023(failure_kind, job)
            failure_evidence = (
                audit.audit_sha256
                if failure_kind
                in {
                    BaselineAttemptFailureKindV023.CONNECTOR_QUERY_BINDING,
                    BaselineAttemptFailureKindV023.SERVICE_ALIAS_BINDING,
                }
                else cast(str, job_evidence_sha)
            )
            disposition = "FAILED"
        elif job is not None and job.status is ProductJobStatusV1.FAILED:
            reason = None
            failure_kind = BaselineAttemptFailureKindV023.BUILDER
            failure_code = job.safe_error_code or "BASELINE_BUILDER_FAILED"
            failure_evidence = cast(str, job_evidence_sha)
            disposition = "FAILED"
        elif job is not None and job.status is ProductJobStatusV1.CANCELLED:
            reason = None
            failure_kind = BaselineAttemptFailureKindV023.BUILDER
            failure_code = job.safe_error_code or "BASELINE_BUILDER_CANCELLED"
            failure_evidence = cast(str, job_evidence_sha)
            disposition = "CANCELLED"
        elif (
            job is not None
            and builder_wait_timed_out
            and job.status in {ProductJobStatusV1.PENDING, ProductJobStatusV1.RUNNING}
        ):
            reason = None
            failure_kind = BaselineAttemptFailureKindV023.IMPLEMENTATION
            failure_code = "BASELINE_BUILDER_TIMEOUT"
            failure_evidence = cast(str, job_evidence_sha)
            disposition = "TIMED_OUT"
        elif (
            job is not None
            and audit is not None
            and job.status is ProductJobStatusV1.SUCCEEDED
            and isinstance(job.result, dict)
            and audit.final_builder_would_pass
        ):
            reason = None
            failure_kind = BaselineAttemptFailureKindV023.PERSISTENCE
            failure_code = "BASELINE_ACTIVATION_MISSING"
            failure_evidence = cast(str, job_evidence_sha)
            disposition = "SUCCEEDED"
        else:
            reason = "ATTEMPT_FAILURE_NOT_CLASSIFIABLE"

        if reason is not None:
            _write_json(
                private_root / "attempt-incomplete.json",
                {
                    "schema_version": (
                        "ecomsre.product.private-baseline-incomplete.v023"
                    ),
                    "terminal": BASELINE_READINESS_BLOCKED_V023,
                    "reason": reason,
                    "attempt_start": start.model_dump(mode="json"),
                    "traffic_result": (
                        None
                        if traffic_result is None
                        else traffic_result.model_dump(mode="json")
                    ),
                    "builder_job": (
                        None if job is None else job.model_dump(mode="json")
                    ),
                    "readiness_audit": (
                        None if audit is None else audit.model_dump(mode="json")
                    ),
                    **closure_payload,
                },
                create_once=True,
            )
            raise RuntimeError(
                f"{BASELINE_READINESS_BLOCKED_V023}: stage={stage}: {reason}: {error}"
            ) from error

        repair_eligible = (
            ordinal == 1
            and not builder_failure_audit_missing
            and failure_kind
            not in {
                BaselineAttemptFailureKindV023.TRANSPORT,
                BaselineAttemptFailureKindV023.INTERRUPTED,
            }
        )
        attempt_terminal = (
            BASELINE_REPAIR_REQUIRED_V023
            if repair_eligible
            else BASELINE_READINESS_BLOCKED_V023
        )
        completion = BaselineAttemptCompletionV023.build(
            attempt_ordinal=ordinal,
            start_sha256=start.start_sha256,
            traffic_result=traffic_result,
            per_window_audit=audit,
            per_window_audit_sha256=None if audit is None else audit.audit_sha256,
            builder_job_id=None if job is None else job.job_id,
            builder_job_record=job,
            builder_job_evidence_sha256=job_evidence_sha,
            builder_job_disposition=disposition,
            builder_transport_failure_codes=(
                builder_transport_failure_codes
                if failure_kind is BaselineAttemptFailureKindV023.TRANSPORT
                else ()
            ),
            builder_transport_retry_count=(
                3 if failure_kind is BaselineAttemptFailureKindV023.TRANSPORT else 0
            ),
            builder_idempotency_key_sha256=(
                hashlib.sha256(builder_idempotency_key.encode("utf-8")).hexdigest()
                if failure_kind is BaselineAttemptFailureKindV023.TRANSPORT
                and builder_idempotency_key is not None
                else None
            ),
            active_baseline_id=None,
            active_baseline_sha256=None,
            cleanup="CLEAN",
            failure_kind=failure_kind,
            failure_code=failure_code,
            failure_evidence_sha256=failure_evidence,
            terminal=attempt_terminal,
            completed_at=datetime.now(UTC),
        )
        terminal = attempt_terminal

    attempt = BaselineAttemptV023(start=start, completion=completion)
    ledger = BaselineAttemptLedgerV023.build((*prior_attempts, attempt))
    private_payload = {
        "schema_version": "ecomsre.product.private-baseline-attempt-report.v023",
        "attempt": attempt.model_dump(mode="json"),
        "ledger": ledger.model_dump(mode="json"),
        "restart_proof": (
            None if restart_proof is None else restart_proof.model_dump(mode="json")
        ),
        **closure_payload,
    }
    _write_json(
        private_root / "attempt-completion.json",
        private_payload,
        create_once=True,
    )
    public_payload: dict[str, object] = {
        "schema_version": "ecomsre.product.public-baseline-attempt.v023",
        "terminal": terminal,
        "attempt": attempt.model_dump(mode="json"),
        "ledger_sha256": ledger.ledger_sha256,
        "environment_id": environment_id,
        "active_baseline_id": baseline_id,
        "active_baseline_sha256": baseline_sha,
        "readiness_audit_sha256": None if audit is None else audit.audit_sha256,
        "parity_sha256": None if audit is None else audit.parity_sha256,
        "restart_proof_sha256": (
            None if restart_proof is None else restart_proof.proof_sha256
        ),
        "product_cleanup": product_cleanup,
        "product_cleanup_observation": product_cleanup_observation,
        "demo_cleanup": demo_cleanup,
        "demo_cleanup_observation": demo_cleanup_observation,
        "outer_baseline_before_sha256": outer_baseline_before_sha256,
        "outer_baseline_unchanged": outer_baseline_unchanged,
        "queue_default_unchanged": queue_default_unchanged,
        "private_report_sha256": _file_sha256(private_root / "attempt-completion.json"),
        "execution_head": execution_head,
        "fault_attempt_count": 0,
        "knowledge_campaign_count": 0,
        "action_authority": "NONE",
        "agent_writes": 0,
        "runbook_executions": 0,
    }
    public_path = (
        root / "docs/analysis" / f"product-v023-baseline-attempt-{ordinal}.json"
    )
    _write_json(public_path, public_payload, create_once=True)
    summary: dict[str, object] = {
        **public_payload,
        "baseline_attempt_count": len(ledger.attempts),
        "readiness_profile_sha256": profile.profile_sha256,
        "active_profile_sha256": ACTIVE_PROFILE_SHA256_V023,
        "environment_configuration_sha256": _file_sha256(
            root / PRODUCT_V023_ENVIRONMENT_PATH
        ),
        "execution_head": execution_head,
    }
    _write_json(root / PRODUCT_V023_RESULT_PATH, summary)
    _atomic_write(root / PRODUCT_V023_RESULT_MARKDOWN_PATH, _render_markdown(summary))
    return summary


__all__ = (
    "PRODUCT_API_PORT_V023",
    "attempt_semantic_inputs_v023",
    "planned_baseline_windows_v023",
    "run_live_baseline_readiness_v023",
    "verify_live_baseline_readiness_contract_v023",
)
