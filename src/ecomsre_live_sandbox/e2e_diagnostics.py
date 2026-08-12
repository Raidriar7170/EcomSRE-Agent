"""Closed staged diagnostics for the live E2E v2 successor."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from enum import Enum
import hashlib
import os
from pathlib import Path
import subprocess
import time
import traceback
from pydantic import Field

from ecomsre_live_sandbox.contracts import (
    SHA256_PATTERN,
    FrozenModel,
    canonical_json_bytes,
    canonical_sha256,
    ensure_private_directory,
    write_private_json,
)
from ecomsre_live_sandbox.environment import CommandResult


class DiagnosticRunKind(str, Enum):
    DIAGNOSTIC_PROBE = "DIAGNOSTIC_PROBE"
    DEVELOPMENT_PROBE = "DEVELOPMENT_PROBE"
    CANONICAL_INVOCATION_A = "CANONICAL_INVOCATION_A"
    INVOCATION_B = "INVOCATION_B"


class DiagnosticEventStatus(str, Enum):
    STARTED = "STARTED"
    PASSED = "PASSED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"
    CLEANUP = "CLEANUP"


class DiagnosticStage(str, Enum):
    PRIVATE_ROOT_BOUND = "PRIVATE_ROOT_BOUND"
    AUTHORITY_VERIFIED = "AUTHORITY_VERIFIED"
    WORKTREE_VERIFIED = "WORKTREE_VERIFIED"
    LOCAL_DOCKER_VERIFIED = "LOCAL_DOCKER_VERIFIED"
    UPSTREAM_PIN_VERIFIED = "UPSTREAM_PIN_VERIFIED"
    COMPOSE_RESOLUTION_STARTED = "COMPOSE_RESOLUTION_STARTED"
    COMPOSE_RESOLVED = "COMPOSE_RESOLVED"
    IMAGE_AUTHORITY_LOAD_STARTED = "IMAGE_AUTHORITY_LOAD_STARTED"
    IMAGE_AUTHORITY_CREATED = "IMAGE_AUTHORITY_CREATED"
    IMAGE_AUTHORITY_VERIFIED = "IMAGE_AUTHORITY_VERIFIED"
    COMPOSE_STRUCTURE_HASH_VERIFIED = "COMPOSE_STRUCTURE_HASH_VERIFIED"
    RUN_IMAGE_VERIFICATION_CREATED = "RUN_IMAGE_VERIFICATION_CREATED"
    IMAGE_LOCK_VERIFICATION_STARTED = "IMAGE_LOCK_VERIFICATION_STARTED"
    IMAGE_LOCK_VERIFIED = "IMAGE_LOCK_VERIFIED"
    FAULT_CONTROLLER_PREPARATION_STARTED = "FAULT_CONTROLLER_PREPARATION_STARTED"
    FAULT_CONTROLLER_PREPARED = "FAULT_CONTROLLER_PREPARED"
    PORT_PREFLIGHT_STARTED = "PORT_PREFLIGHT_STARTED"
    PORTS_AVAILABLE = "PORTS_AVAILABLE"
    DOCKER_BASELINE_SNAPSHOT_CAPTURED = "DOCKER_BASELINE_SNAPSHOT_CAPTURED"
    COMPOSE_START_REQUESTED = "COMPOSE_START_REQUESTED"
    COMPOSE_START_RETURNED = "COMPOSE_START_RETURNED"
    OWNED_RESOURCE_INVENTORY_VERIFIED = "OWNED_RESOURCE_INVENTORY_VERIFIED"
    SERVICE_HEALTH_WAIT_STARTED = "SERVICE_HEALTH_WAIT_STARTED"
    SERVICES_HEALTHY = "SERVICES_HEALTHY"
    STABILIZATION_STARTED = "STABILIZATION_STARTED"
    STABILIZATION_COMPLETED = "STABILIZATION_COMPLETED"
    BASELINE_CONFIGURATION_READ_STARTED = "BASELINE_CONFIGURATION_READ_STARTED"
    BASELINE_CONFIGURATION_VERIFIED = "BASELINE_CONFIGURATION_VERIFIED"
    SOURCE_CAPTURE_WINDOW_STARTED = "SOURCE_CAPTURE_WINDOW_STARTED"
    SOURCE_CAPTURE_WINDOW_COMPLETED = "SOURCE_CAPTURE_WINDOW_COMPLETED"
    METRICS_PROBE_CREATED = "METRICS_PROBE_CREATED"
    LOGS_PROBE_CREATED = "LOGS_PROBE_CREATED"
    TRACES_PROBE_CREATED = "TRACES_PROBE_CREATED"
    SOURCE_BATCH_TERMINALIZATION_STARTED = "SOURCE_BATCH_TERMINALIZATION_STARTED"
    SOURCE_BATCH_TERMINALIZATION_COMPLETED = "SOURCE_BATCH_TERMINALIZATION_COMPLETED"
    METRICS_PREFLIGHT_STARTED = "METRICS_PREFLIGHT_STARTED"
    METRICS_PREFLIGHT_COMPLETED = "METRICS_PREFLIGHT_COMPLETED"
    LOGS_PREFLIGHT_STARTED = "LOGS_PREFLIGHT_STARTED"
    LOGS_PREFLIGHT_COMPLETED = "LOGS_PREFLIGHT_COMPLETED"
    TRACES_PREFLIGHT_STARTED = "TRACES_PREFLIGHT_STARTED"
    TRACES_PREFLIGHT_COMPLETED = "TRACES_PREFLIGHT_COMPLETED"
    EVIDENCE_RESOLUTION_COMPLETED = "EVIDENCE_RESOLUTION_COMPLETED"
    SOURCE_AVAILABILITY_GATE_EVALUATED = "SOURCE_AVAILABILITY_GATE_EVALUATED"
    NO_FAULT_READINESS_EVALUATED = "NO_FAULT_READINESS_EVALUATED"
    MULTISERVICE_PROJECTION_STARTED = "MULTISERVICE_PROJECTION_STARTED"
    MULTISERVICE_PROJECTION_COMPLETED = "MULTISERVICE_PROJECTION_COMPLETED"
    CLEANUP_STARTED = "CLEANUP_STARTED"
    BASELINE_RESTORED = "BASELINE_RESTORED"
    COMPOSE_DOWN_RETURNED = "COMPOSE_DOWN_RETURNED"
    CLEANUP_COMPLETED = "CLEANUP_COMPLETED"
    SCENARIO_LOCK_CREATED = "SCENARIO_LOCK_CREATED"
    PLAN_TEMPLATE_CREATED = "PLAN_TEMPLATE_CREATED"
    APPROVAL_REQUEST_CREATED = "APPROVAL_REQUEST_CREATED"
    TERMINAL_SEALED = "TERMINAL_SEALED"


class DiagnosticFailureCode(str, Enum):
    PRIVATE_ROOT_BINDING_FAILED = "PRIVATE_ROOT_BINDING_FAILED"
    AUTHORITY_VERIFICATION_FAILED = "AUTHORITY_VERIFICATION_FAILED"
    WORKTREE_NOT_CLEAN = "WORKTREE_NOT_CLEAN"
    BRANCH_IDENTITY_MISMATCH = "BRANCH_IDENTITY_MISMATCH"
    DOCKER_AUTHORITY_UNAVAILABLE = "DOCKER_AUTHORITY_UNAVAILABLE"
    DOCKER_ENDPOINT_NOT_LOCAL = "DOCKER_ENDPOINT_NOT_LOCAL"
    DOCKER_DAEMON_IDENTITY_INVALID = "DOCKER_DAEMON_IDENTITY_INVALID"
    UPSTREAM_PIN_DRIFT = "UPSTREAM_PIN_DRIFT"
    COMPOSE_RESOLUTION_FAILED = "COMPOSE_RESOLUTION_FAILED"
    RESOLVED_COMPOSE_DRIFT = "RESOLVED_COMPOSE_DRIFT"
    IMAGE_AUTHORITY_CREATION_FAILED = "IMAGE_AUTHORITY_CREATION_FAILED"
    IMAGE_AUTHORITY_MISMATCH = "IMAGE_AUTHORITY_MISMATCH"
    RUN_IMAGE_VERIFICATION_WRITE_FAILED = "RUN_IMAGE_VERIFICATION_WRITE_FAILED"
    COMPOSE_STRUCTURE_IDENTITY_MISMATCH = "COMPOSE_STRUCTURE_IDENTITY_MISMATCH"
    COMPOSE_INSTANCE_IDENTITY_INVALID = "COMPOSE_INSTANCE_IDENTITY_INVALID"
    IMAGE_LOCK_VERIFICATION_FAILED = "IMAGE_LOCK_VERIFICATION_FAILED"
    FAULT_CONTROLLER_PREPARATION_FAILED = "FAULT_CONTROLLER_PREPARATION_FAILED"
    PORT_CONFLICT = "PORT_CONFLICT"
    DOCKER_BASELINE_SNAPSHOT_FAILED = "DOCKER_BASELINE_SNAPSHOT_FAILED"
    COMPOSE_UP_FAILED = "COMPOSE_UP_FAILED"
    OWNED_RESOURCE_INVENTORY_INCOMPLETE = "OWNED_RESOURCE_INVENTORY_INCOMPLETE"
    SERVICE_HEALTH_TIMEOUT = "SERVICE_HEALTH_TIMEOUT"
    SERVICE_EXITED_BEFORE_READY = "SERVICE_EXITED_BEFORE_READY"
    BASELINE_CONFIGURATION_UNAVAILABLE = "BASELINE_CONFIGURATION_UNAVAILABLE"
    BASELINE_CONFIGURATION_MISMATCH = "BASELINE_CONFIGURATION_MISMATCH"
    SOURCE_BATCH_CONTRACT_FAILED = "SOURCE_BATCH_CONTRACT_FAILED"
    LIVE_TELEMETRY_SOURCE_GATE_NOT_PASSED = "LIVE_TELEMETRY_SOURCE_GATE_NOT_PASSED"
    METRICS_PREFLIGHT_FAILED = "METRICS_PREFLIGHT_FAILED"
    LOGS_PREFLIGHT_FAILED = "LOGS_PREFLIGHT_FAILED"
    TRACES_PREFLIGHT_FAILED = "TRACES_PREFLIGHT_FAILED"
    EVIDENCE_RESOLUTION_FAILED = "EVIDENCE_RESOLUTION_FAILED"
    NO_FAULT_READINESS_FAILED = "NO_FAULT_READINESS_FAILED"
    MULTISERVICE_PROJECTION_FAILED = "MULTISERVICE_PROJECTION_FAILED"
    SCENARIO_LOCK_WRITE_FAILED = "SCENARIO_LOCK_WRITE_FAILED"
    PLAN_TEMPLATE_WRITE_FAILED = "PLAN_TEMPLATE_WRITE_FAILED"
    APPROVAL_REQUEST_WRITE_FAILED = "APPROVAL_REQUEST_WRITE_FAILED"
    PRIVATE_PERMISSION_VIOLATION = "PRIVATE_PERMISSION_VIOLATION"
    CLEANUP_FAILED = "CLEANUP_FAILED"
    NON_OWNED_RESOURCE_CHANGE = "NON_OWNED_RESOURCE_CHANGE"
    UNCLASSIFIED_RUNTIME_FAILURE = "UNCLASSIFIED_RUNTIME_FAILURE"


V3_ONLY_DIAGNOSTIC_STAGES = frozenset(
    {
        DiagnosticStage.IMAGE_AUTHORITY_LOAD_STARTED,
        DiagnosticStage.IMAGE_AUTHORITY_CREATED,
        DiagnosticStage.IMAGE_AUTHORITY_VERIFIED,
        DiagnosticStage.RUN_IMAGE_VERIFICATION_CREATED,
        DiagnosticStage.COMPOSE_STRUCTURE_HASH_VERIFIED,
    }
)
V3_ONLY_DIAGNOSTIC_FAILURE_CODES = frozenset(
    {
        DiagnosticFailureCode.IMAGE_AUTHORITY_CREATION_FAILED,
        DiagnosticFailureCode.IMAGE_AUTHORITY_MISMATCH,
        DiagnosticFailureCode.RUN_IMAGE_VERIFICATION_WRITE_FAILED,
        DiagnosticFailureCode.COMPOSE_STRUCTURE_IDENTITY_MISMATCH,
        DiagnosticFailureCode.COMPOSE_INSTANCE_IDENTITY_INVALID,
    }
)
V4_ONLY_DIAGNOSTIC_STAGES = frozenset(
    {
        DiagnosticStage.SOURCE_CAPTURE_WINDOW_STARTED,
        DiagnosticStage.SOURCE_CAPTURE_WINDOW_COMPLETED,
        DiagnosticStage.METRICS_PROBE_CREATED,
        DiagnosticStage.LOGS_PROBE_CREATED,
        DiagnosticStage.TRACES_PROBE_CREATED,
        DiagnosticStage.SOURCE_BATCH_TERMINALIZATION_STARTED,
        DiagnosticStage.SOURCE_BATCH_TERMINALIZATION_COMPLETED,
        DiagnosticStage.SOURCE_AVAILABILITY_GATE_EVALUATED,
    }
)
V4_ONLY_DIAGNOSTIC_FAILURE_CODES = frozenset(
    {
        DiagnosticFailureCode.SOURCE_BATCH_CONTRACT_FAILED,
        DiagnosticFailureCode.LIVE_TELEMETRY_SOURCE_GATE_NOT_PASSED,
    }
)
V5_ONLY_DIAGNOSTIC_STAGES = frozenset(
    {DiagnosticStage.NO_FAULT_READINESS_EVALUATED}
)
V5_ONLY_DIAGNOSTIC_FAILURE_CODES = frozenset(
    {DiagnosticFailureCode.NO_FAULT_READINESS_FAILED}
)
V4_OBSOLETE_SINGLETON_STAGES = frozenset(
    {
        DiagnosticStage.METRICS_PREFLIGHT_STARTED,
        DiagnosticStage.LOGS_PREFLIGHT_STARTED,
        DiagnosticStage.TRACES_PREFLIGHT_STARTED,
    }
)
V4_DIAGNOSTIC_STAGES = tuple(
    stage
    for stage in DiagnosticStage
    if stage not in V4_OBSOLETE_SINGLETON_STAGES | V5_ONLY_DIAGNOSTIC_STAGES
)
V5_DIAGNOSTIC_STAGES = tuple(
    stage for stage in DiagnosticStage if stage not in V4_OBSOLETE_SINGLETON_STAGES
)
V3_DIAGNOSTIC_STAGES = tuple(
    stage
    for stage in DiagnosticStage
    if stage not in V4_ONLY_DIAGNOSTIC_STAGES | V5_ONLY_DIAGNOSTIC_STAGES
)
V3_DIAGNOSTIC_FAILURE_CODES = tuple(
    code
    for code in DiagnosticFailureCode
    if code not in V4_ONLY_DIAGNOSTIC_FAILURE_CODES | V5_ONLY_DIAGNOSTIC_FAILURE_CODES
)
V4_DIAGNOSTIC_FAILURE_CODES = tuple(
    code for code in DiagnosticFailureCode if code not in V5_ONLY_DIAGNOSTIC_FAILURE_CODES
)
V5_DIAGNOSTIC_FAILURE_CODES = tuple(DiagnosticFailureCode)
V2_DIAGNOSTIC_STAGES = tuple(
    stage
    for stage in DiagnosticStage
    if stage
    not in V3_ONLY_DIAGNOSTIC_STAGES
    | V4_ONLY_DIAGNOSTIC_STAGES
    | V5_ONLY_DIAGNOSTIC_STAGES
)
V2_DIAGNOSTIC_FAILURE_CODES = tuple(
    code
    for code in DiagnosticFailureCode
    if code
    not in V3_ONLY_DIAGNOSTIC_FAILURE_CODES
    | V4_ONLY_DIAGNOSTIC_FAILURE_CODES
    | V5_ONLY_DIAGNOSTIC_FAILURE_CODES
)


class DiagnosticCommandIdentity(str, Enum):
    DOCKER_CONTEXT_SHOW = "DOCKER_CONTEXT_SHOW"
    DOCKER_CONTEXT_INSPECT = "DOCKER_CONTEXT_INSPECT"
    DOCKER_INFO = "DOCKER_INFO"
    COMPOSE_CONFIG = "COMPOSE_CONFIG"
    DOCKER_IMAGE_INSPECT = "DOCKER_IMAGE_INSPECT"
    COMPOSE_UP = "COMPOSE_UP"
    DOCKER_INSPECT_SERVICES = "DOCKER_INSPECT_SERVICES"
    COMPOSE_PS = "COMPOSE_PS"
    COMPOSE_DOWN = "COMPOSE_DOWN"
    DOCKER_RESOURCE_SNAPSHOT = "DOCKER_RESOURCE_SNAPSHOT"
    GIT_UPSTREAM_HEAD = "GIT_UPSTREAM_HEAD"
    GIT_UPSTREAM_TAG = "GIT_UPSTREAM_TAG"
    GIT_UPSTREAM_STATUS = "GIT_UPSTREAM_STATUS"


_STAGE_FAILURE_CODE: dict[DiagnosticStage, DiagnosticFailureCode] = {
    DiagnosticStage.PRIVATE_ROOT_BOUND: DiagnosticFailureCode.PRIVATE_ROOT_BINDING_FAILED,
    DiagnosticStage.AUTHORITY_VERIFIED: DiagnosticFailureCode.AUTHORITY_VERIFICATION_FAILED,
    DiagnosticStage.WORKTREE_VERIFIED: DiagnosticFailureCode.BRANCH_IDENTITY_MISMATCH,
    DiagnosticStage.LOCAL_DOCKER_VERIFIED: DiagnosticFailureCode.DOCKER_AUTHORITY_UNAVAILABLE,
    DiagnosticStage.UPSTREAM_PIN_VERIFIED: DiagnosticFailureCode.UPSTREAM_PIN_DRIFT,
    DiagnosticStage.COMPOSE_RESOLUTION_STARTED: DiagnosticFailureCode.COMPOSE_RESOLUTION_FAILED,
    DiagnosticStage.COMPOSE_RESOLVED: DiagnosticFailureCode.RESOLVED_COMPOSE_DRIFT,
    DiagnosticStage.IMAGE_AUTHORITY_LOAD_STARTED: DiagnosticFailureCode.IMAGE_AUTHORITY_CREATION_FAILED,
    DiagnosticStage.IMAGE_AUTHORITY_CREATED: DiagnosticFailureCode.IMAGE_AUTHORITY_CREATION_FAILED,
    DiagnosticStage.IMAGE_AUTHORITY_VERIFIED: DiagnosticFailureCode.IMAGE_AUTHORITY_MISMATCH,
    DiagnosticStage.RUN_IMAGE_VERIFICATION_CREATED: DiagnosticFailureCode.RUN_IMAGE_VERIFICATION_WRITE_FAILED,
    DiagnosticStage.COMPOSE_STRUCTURE_HASH_VERIFIED: DiagnosticFailureCode.COMPOSE_STRUCTURE_IDENTITY_MISMATCH,
    DiagnosticStage.IMAGE_LOCK_VERIFICATION_STARTED: DiagnosticFailureCode.IMAGE_LOCK_VERIFICATION_FAILED,
    DiagnosticStage.IMAGE_LOCK_VERIFIED: DiagnosticFailureCode.IMAGE_LOCK_VERIFICATION_FAILED,
    DiagnosticStage.FAULT_CONTROLLER_PREPARATION_STARTED: DiagnosticFailureCode.FAULT_CONTROLLER_PREPARATION_FAILED,
    DiagnosticStage.FAULT_CONTROLLER_PREPARED: DiagnosticFailureCode.FAULT_CONTROLLER_PREPARATION_FAILED,
    DiagnosticStage.PORT_PREFLIGHT_STARTED: DiagnosticFailureCode.PORT_CONFLICT,
    DiagnosticStage.PORTS_AVAILABLE: DiagnosticFailureCode.PORT_CONFLICT,
    DiagnosticStage.DOCKER_BASELINE_SNAPSHOT_CAPTURED: DiagnosticFailureCode.DOCKER_BASELINE_SNAPSHOT_FAILED,
    DiagnosticStage.COMPOSE_START_REQUESTED: DiagnosticFailureCode.COMPOSE_UP_FAILED,
    DiagnosticStage.COMPOSE_START_RETURNED: DiagnosticFailureCode.COMPOSE_UP_FAILED,
    DiagnosticStage.OWNED_RESOURCE_INVENTORY_VERIFIED: DiagnosticFailureCode.OWNED_RESOURCE_INVENTORY_INCOMPLETE,
    DiagnosticStage.SERVICE_HEALTH_WAIT_STARTED: DiagnosticFailureCode.SERVICE_HEALTH_TIMEOUT,
    DiagnosticStage.SERVICES_HEALTHY: DiagnosticFailureCode.SERVICE_HEALTH_TIMEOUT,
    DiagnosticStage.STABILIZATION_STARTED: DiagnosticFailureCode.UNCLASSIFIED_RUNTIME_FAILURE,
    DiagnosticStage.STABILIZATION_COMPLETED: DiagnosticFailureCode.UNCLASSIFIED_RUNTIME_FAILURE,
    DiagnosticStage.BASELINE_CONFIGURATION_READ_STARTED: DiagnosticFailureCode.BASELINE_CONFIGURATION_UNAVAILABLE,
    DiagnosticStage.BASELINE_CONFIGURATION_VERIFIED: DiagnosticFailureCode.BASELINE_CONFIGURATION_MISMATCH,
    DiagnosticStage.SOURCE_CAPTURE_WINDOW_STARTED: DiagnosticFailureCode.SOURCE_BATCH_CONTRACT_FAILED,
    DiagnosticStage.SOURCE_CAPTURE_WINDOW_COMPLETED: DiagnosticFailureCode.SOURCE_BATCH_CONTRACT_FAILED,
    DiagnosticStage.METRICS_PROBE_CREATED: DiagnosticFailureCode.SOURCE_BATCH_CONTRACT_FAILED,
    DiagnosticStage.LOGS_PROBE_CREATED: DiagnosticFailureCode.SOURCE_BATCH_CONTRACT_FAILED,
    DiagnosticStage.TRACES_PROBE_CREATED: DiagnosticFailureCode.SOURCE_BATCH_CONTRACT_FAILED,
    DiagnosticStage.SOURCE_BATCH_TERMINALIZATION_STARTED: DiagnosticFailureCode.SOURCE_BATCH_CONTRACT_FAILED,
    DiagnosticStage.SOURCE_BATCH_TERMINALIZATION_COMPLETED: DiagnosticFailureCode.SOURCE_BATCH_CONTRACT_FAILED,
    DiagnosticStage.SOURCE_AVAILABILITY_GATE_EVALUATED: DiagnosticFailureCode.LIVE_TELEMETRY_SOURCE_GATE_NOT_PASSED,
    DiagnosticStage.NO_FAULT_READINESS_EVALUATED: DiagnosticFailureCode.NO_FAULT_READINESS_FAILED,
    DiagnosticStage.METRICS_PREFLIGHT_STARTED: DiagnosticFailureCode.METRICS_PREFLIGHT_FAILED,
    DiagnosticStage.METRICS_PREFLIGHT_COMPLETED: DiagnosticFailureCode.METRICS_PREFLIGHT_FAILED,
    DiagnosticStage.LOGS_PREFLIGHT_STARTED: DiagnosticFailureCode.LOGS_PREFLIGHT_FAILED,
    DiagnosticStage.LOGS_PREFLIGHT_COMPLETED: DiagnosticFailureCode.LOGS_PREFLIGHT_FAILED,
    DiagnosticStage.TRACES_PREFLIGHT_STARTED: DiagnosticFailureCode.TRACES_PREFLIGHT_FAILED,
    DiagnosticStage.TRACES_PREFLIGHT_COMPLETED: DiagnosticFailureCode.TRACES_PREFLIGHT_FAILED,
    DiagnosticStage.EVIDENCE_RESOLUTION_COMPLETED: DiagnosticFailureCode.EVIDENCE_RESOLUTION_FAILED,
    DiagnosticStage.MULTISERVICE_PROJECTION_STARTED: DiagnosticFailureCode.MULTISERVICE_PROJECTION_FAILED,
    DiagnosticStage.MULTISERVICE_PROJECTION_COMPLETED: DiagnosticFailureCode.MULTISERVICE_PROJECTION_FAILED,
    DiagnosticStage.CLEANUP_STARTED: DiagnosticFailureCode.CLEANUP_FAILED,
    DiagnosticStage.BASELINE_RESTORED: DiagnosticFailureCode.CLEANUP_FAILED,
    DiagnosticStage.COMPOSE_DOWN_RETURNED: DiagnosticFailureCode.CLEANUP_FAILED,
    DiagnosticStage.CLEANUP_COMPLETED: DiagnosticFailureCode.CLEANUP_FAILED,
    DiagnosticStage.SCENARIO_LOCK_CREATED: DiagnosticFailureCode.SCENARIO_LOCK_WRITE_FAILED,
    DiagnosticStage.PLAN_TEMPLATE_CREATED: DiagnosticFailureCode.PLAN_TEMPLATE_WRITE_FAILED,
    DiagnosticStage.APPROVAL_REQUEST_CREATED: DiagnosticFailureCode.APPROVAL_REQUEST_WRITE_FAILED,
    DiagnosticStage.TERMINAL_SEALED: DiagnosticFailureCode.PRIVATE_PERMISSION_VIOLATION,
}


def failure_code_for_stage(stage: DiagnosticStage) -> DiagnosticFailureCode:
    return _STAGE_FAILURE_CODE[stage]


class DiagnosticEvent(FrozenModel):
    schema_version: str = "live-e2e.diagnostic-event.v2"
    sequence: int = Field(ge=1)
    run_kind: DiagnosticRunKind
    run_id: str = Field(min_length=1, max_length=128)
    stage: DiagnosticStage
    status: DiagnosticEventStatus
    started_at: datetime
    ended_at: datetime | None
    monotonic_duration_seconds: float = Field(ge=0)
    input_sha256: str | None = Field(default=None, pattern=SHA256_PATTERN)
    output_sha256: str | None = Field(default=None, pattern=SHA256_PATTERN)
    safe_reason_code: str | None = Field(default=None, max_length=128)
    artifact_refs: tuple[str, ...] = ()
    safe_aggregate: dict[str, object] = Field(default_factory=dict)


def _json_hash(value: object | None) -> str | None:
    if value is None:
        return None
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    try:
        return canonical_sha256(value)
    except (TypeError, ValueError):
        return hashlib.sha256(repr(value).encode("utf-8")).hexdigest()


class DiagnosticJournal:
    """Append one fsync'd event at a time without rewriting the existing prefix."""

    def __init__(self, path: Path, *, run_kind: DiagnosticRunKind, run_id: str) -> None:
        self.path = path
        self.run_kind = run_kind
        self.run_id = run_id
        self._events: list[DiagnosticEvent] = []
        self._active_stage: DiagnosticStage | None = None
        if path.exists() or path.is_symlink():
            if path.is_symlink() or not path.is_file():
                raise ValueError("diagnostic journal is not a regular file")
            for line in path.read_text(encoding="utf-8").splitlines():
                self._events.append(DiagnosticEvent.model_validate_json(line))
            self._validate_existing()

    def _validate_existing(self) -> None:
        if [event.sequence for event in self._events] != list(range(1, len(self._events) + 1)):
            raise RuntimeError("diagnostic event sequence is not strictly increasing")
        for event in self._events:
            if event.run_kind is not self.run_kind or event.run_id != self.run_id:
                raise RuntimeError("diagnostic journal identity differs")
            if event.status is DiagnosticEventStatus.STARTED:
                if self._active_stage is not None:
                    raise RuntimeError("diagnostic journal has nested active stages")
                self._active_stage = event.stage
            elif self._active_stage is event.stage:
                self._active_stage = None

    @property
    def last_completed_stage(self) -> DiagnosticStage | None:
        for event in reversed(self._events):
            if event.status in {
                DiagnosticEventStatus.PASSED,
                DiagnosticEventStatus.SKIPPED,
                DiagnosticEventStatus.CLEANUP,
            }:
                return event.stage
        return None

    def record(
        self,
        *,
        stage: DiagnosticStage,
        status: DiagnosticEventStatus,
        started_at: datetime,
        ended_at: datetime | None = None,
        monotonic_duration_seconds: float = 0.0,
        input_value: object | None = None,
        output_value: object | None = None,
        safe_reason_code: str | None = None,
        artifact_refs: tuple[str, ...] = (),
        safe_aggregate: Mapping[str, object] | None = None,
    ) -> DiagnosticEvent:
        if status is DiagnosticEventStatus.STARTED:
            if self._active_stage is not None:
                raise RuntimeError("diagnostic journal still has an active stage")
            previous = self._events[-1].stage if self._events else None
            if previous is not None and list(DiagnosticStage).index(stage) < list(DiagnosticStage).index(previous):
                raise RuntimeError("diagnostic stage order moved backward")
            self._active_stage = stage
        elif status in {DiagnosticEventStatus.PASSED, DiagnosticEventStatus.FAILED}:
            if self._active_stage is not stage:
                raise RuntimeError("diagnostic completion does not match the active stage")
            self._active_stage = None
        elif self._active_stage is not None:
            raise RuntimeError("diagnostic journal still has an active stage")
        event = DiagnosticEvent(
            sequence=len(self._events) + 1,
            run_kind=self.run_kind,
            run_id=self.run_id,
            stage=stage,
            status=status,
            started_at=started_at,
            ended_at=ended_at,
            monotonic_duration_seconds=monotonic_duration_seconds,
            input_sha256=_json_hash(input_value),
            output_sha256=_json_hash(output_value),
            safe_reason_code=safe_reason_code,
            artifact_refs=artifact_refs,
            safe_aggregate=dict(safe_aggregate or {}),
        )
        ensure_private_directory(self.path.parent)
        payload = canonical_json_bytes(event.model_dump(mode="json"))
        descriptor = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        with os.fdopen(descriptor, "ab") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        self.path.chmod(0o600)
        self._events.append(event)
        return event


class DiagnosticExceptionReference(FrozenModel):
    artifact_ref: str
    artifact_sha256: str = Field(pattern=SHA256_PATTERN)
    exception_module: str
    exception_type: str
    exception_message_sha256: str = Field(pattern=SHA256_PATTERN)
    traceback_sha256: str = Field(pattern=SHA256_PATTERN)


class ExceptionArtifactStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def capture(
        self,
        error: BaseException,
        *,
        stage: DiagnosticStage,
        sequence: int,
    ) -> DiagnosticExceptionReference:
        rendered = "".join(traceback.format_exception(type(error), error, error.__traceback__))
        message = str(error)
        payload = {
            "schema_version": "live-e2e.private-exception.v2",
            "exception_module": type(error).__module__,
            "exception_type": type(error).__name__,
            "raw_message": message,
            "traceback": rendered,
            "failing_stage": stage.value,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        name = f"exception-{sequence:04d}"
        sha256 = write_private_json(self.root / f"{name}.json", payload, create_once=True)
        return DiagnosticExceptionReference(
            artifact_ref=name,
            artifact_sha256=sha256,
            exception_module=type(error).__module__,
            exception_type=type(error).__name__,
            exception_message_sha256=hashlib.sha256(message.encode("utf-8")).hexdigest(),
            traceback_sha256=hashlib.sha256(rendered.encode("utf-8")).hexdigest(),
        )


class DiagnosticCommandError(RuntimeError):
    def __init__(
        self,
        identity: DiagnosticCommandIdentity,
        *,
        return_code: int | None,
        timed_out: bool,
    ) -> None:
        super().__init__(f"diagnostic command {identity.value} did not complete successfully")
        self.identity = identity
        self.return_code = return_code
        self.timed_out = timed_out


def _command_identity(arguments: tuple[str, ...]) -> DiagnosticCommandIdentity:
    if arguments[:3] == ("docker", "context", "show"):
        return DiagnosticCommandIdentity.DOCKER_CONTEXT_SHOW
    if arguments[:3] == ("docker", "context", "inspect"):
        return DiagnosticCommandIdentity.DOCKER_CONTEXT_INSPECT
    if arguments[:2] == ("docker", "info"):
        return DiagnosticCommandIdentity.DOCKER_INFO
    if arguments[:3] == ("docker", "image", "inspect"):
        return DiagnosticCommandIdentity.DOCKER_IMAGE_INSPECT
    if len(arguments) >= 2 and arguments[:2] == ("docker", "compose"):
        for operation, identity in (
            ("config", DiagnosticCommandIdentity.COMPOSE_CONFIG),
            ("up", DiagnosticCommandIdentity.COMPOSE_UP),
            ("ps", DiagnosticCommandIdentity.COMPOSE_PS),
            ("down", DiagnosticCommandIdentity.COMPOSE_DOWN),
        ):
            if operation in arguments[2:]:
                return identity
    if arguments[:2] == ("docker", "inspect"):
        return DiagnosticCommandIdentity.DOCKER_INSPECT_SERVICES
    if len(arguments) >= 2 and arguments[0] == "docker" and arguments[1] in {
        "ps",
        "network",
        "volume",
    }:
        if "inspect" in arguments:
            return DiagnosticCommandIdentity.DOCKER_INSPECT_SERVICES
        return DiagnosticCommandIdentity.DOCKER_RESOURCE_SNAPSHOT
    if arguments[:3] == ("git", "rev-parse", "HEAD"):
        return DiagnosticCommandIdentity.GIT_UPSTREAM_HEAD
    if arguments[:3] == ("git", "describe", "--tags"):
        return DiagnosticCommandIdentity.GIT_UPSTREAM_TAG
    if arguments[:3] == ("git", "status", "--porcelain=v1"):
        return DiagnosticCommandIdentity.GIT_UPSTREAM_STATUS
    raise ValueError("command argv has no allowlisted diagnostic identity")


def _text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def _write_private_bytes(path: Path, value: bytes) -> str:
    ensure_private_directory(path.parent)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(value)
        handle.flush()
        os.fsync(handle.fileno())
    path.chmod(0o600)
    return hashlib.sha256(value).hexdigest()


class RecordingCommandRunner:
    """Execute only known argv shapes and retain raw command evidence privately."""

    def __init__(
        self,
        root: Path,
        *,
        on_start: Callable[[DiagnosticCommandIdentity], None] | None = None,
        on_return: Callable[[DiagnosticCommandIdentity, int | None, bool], None] | None = None,
    ) -> None:
        self.root = root
        self.on_start = on_start
        self.on_return = on_return
        self._sequence = 0

    def run(
        self,
        arguments: tuple[str, ...],
        *,
        cwd: Path,
        env: Mapping[str, str] | None = None,
        timeout_seconds: float = 300,
    ) -> CommandResult:
        identity = _command_identity(arguments)
        self._sequence += 1
        sequence = self._sequence
        if self.on_start is not None:
            self.on_start(identity)
        started_at = datetime.now(timezone.utc)
        monotonic_start = time.monotonic()
        stdout = ""
        stderr = ""
        return_code: int | None = None
        timed_out = False
        try:
            completed = subprocess.run(
                list(arguments),
                cwd=cwd,
                env=None if env is None else dict(env),
                check=False,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                shell=False,
            )
            stdout = completed.stdout
            stderr = completed.stderr
            return_code = completed.returncode
        except subprocess.TimeoutExpired as error:
            stdout = _text(error.output)
            stderr = _text(error.stderr)
            timed_out = True
        ended_at = datetime.now(timezone.utc)
        duration = time.monotonic() - monotonic_start
        stem = f"command-{sequence:04d}"
        stdout_bytes = stdout.encode("utf-8")
        stderr_bytes = stderr.encode("utf-8")
        stdout_sha256 = _write_private_bytes(self.root / f"{stem}.stdout", stdout_bytes)
        stderr_sha256 = _write_private_bytes(self.root / f"{stem}.stderr", stderr_bytes)
        record = {
            "schema_version": "live-e2e.private-command-diagnostic.v2",
            "command_identity": identity.value,
            "argv_sha256": hashlib.sha256(b"\0".join(item.encode("utf-8") for item in arguments)).hexdigest(),
            "cwd_sha256": hashlib.sha256(str(cwd.resolve()).encode("utf-8")).hexdigest(),
            "started_at": started_at.isoformat(),
            "ended_at": ended_at.isoformat(),
            "duration_seconds": duration,
            "return_code": return_code,
            "timed_out": timed_out,
            "stdout_artifact_sha256": stdout_sha256,
            "stderr_artifact_sha256": stderr_sha256,
            "stdout_byte_count": len(stdout_bytes),
            "stderr_byte_count": len(stderr_bytes),
        }
        write_private_json(self.root / f"{stem}.json", record, create_once=True)
        if self.on_return is not None:
            self.on_return(identity, return_code, timed_out)
        if timed_out or return_code != 0:
            raise DiagnosticCommandError(
                identity,
                return_code=return_code,
                timed_out=timed_out,
            )
        return CommandResult(arguments=arguments, stdout=stdout, stderr=stderr)


__all__ = [
    "DiagnosticCommandError",
    "DiagnosticCommandIdentity",
    "DiagnosticEvent",
    "DiagnosticEventStatus",
    "DiagnosticExceptionReference",
    "DiagnosticFailureCode",
    "DiagnosticJournal",
    "DiagnosticRunKind",
    "DiagnosticStage",
    "ExceptionArtifactStore",
    "RecordingCommandRunner",
    "failure_code_for_stage",
]
