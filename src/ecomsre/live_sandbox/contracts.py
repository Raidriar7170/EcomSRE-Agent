"""Closed contracts for the single live local sandbox scenario."""

from __future__ import annotations

from enum import Enum
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Literal, Mapping

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StrictFloat,
    StrictInt,
    model_validator,
)


SHA256_PATTERN = r"^[0-9a-f]{64}$"
UUID_PATTERN = r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
ACTION = "RESTORE_FROZEN_SERVICE_CONFIGURATION"


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class EnvironmentConfig(FrozenModel):
    schema_version: Literal["live-sandbox.environment.v1"]
    compose_files: tuple[str, str, str]
    compose_project: Literal["ecomsre-live-sandbox-v1"]
    docker_endpoint_scheme: Literal["unix"]
    environment_id: Literal["opentelemetry-demo-local-v1"]
    platform: Literal["linux/arm64"]
    sandbox_id: str = Field(pattern=UUID_PATTERN)
    sandbox_label_key: Literal["io.ecomsre.sandbox.id"]
    upstream_commit: Literal["1755859a9de82c2e5e225be68abc401a5ebf2b4f"]
    upstream_tag: Literal["3.0.0"]


class ScenarioConfig(FrozenModel):
    schema_version: Literal["live-sandbox.scenario.v1"]
    scenario_id: str = Field(pattern=UUID_PATTERN)
    fault_controller_type: Literal["FLAGD_UI_WHOLE_DOCUMENT_HTTP_V1"]
    target_service: Literal["payment"]
    target_configuration_key: Literal["paymentFailure.defaultVariant"]
    target_flag: Literal["paymentFailure"]
    baseline_variant: Literal["off"]
    fault_variant: Literal["100%"]
    baseline_value: Literal[0]
    fault_value: Literal[1]
    baseline_document_sha256: str = Field(pattern=SHA256_PATTERN)
    fault_document_sha256: str = Field(pattern=SHA256_PATTERN)
    expected_fault_class: Literal["APPLICATION"]
    expected_root_service: Literal["payment"]
    impact_sli: Literal["REQUEST_ERROR_RATE"]
    load_generator_vus: Literal[25]
    fault_observation_window_seconds: Literal[30]
    verification_window_seconds: Literal[30]
    cleanup_policy: Literal["RESTORE_BASELINE_THEN_OWNED_COMPOSE_DOWN"]


class TelemetryTarget(FrozenModel):
    published_port: StrictInt = Field(ge=1024, le=65535)
    target_port: StrictInt = Field(ge=1, le=65535)
    path: str | None = None
    index: str | None = None


class PrometheusConfig(TelemetryTarget):
    total_query: str
    error_query: str
    p95_query: str
    health_query: str


class TelemetryConfig(FrozenModel):
    schema_version: Literal["live-sandbox.telemetry.v1"]
    prometheus: PrometheusConfig
    opensearch: TelemetryTarget
    jaeger: TelemetryTarget


class DiagnosisConfig(FrozenModel):
    schema_version: Literal["live-sandbox.diagnosis.v1"]
    architecture: Literal["A0"]
    decision: Literal["STRONG_SINGLE_HIERARCHICAL"]
    model: Literal["gpt-5.4-mini-2026-03-17"]
    prompt_sha256: str = Field(pattern=SHA256_PATTERN)
    output_schema_sha256: str = Field(pattern=SHA256_PATTERN)
    max_completion_tokens: StrictInt = Field(gt=0)
    specialist_calls: Literal[0]
    fusion_calls: Literal[0]


class PolicyConfig(FrozenModel):
    schema_version: Literal["live-sandbox.policy.v1"]
    action: Literal["RESTORE_FROZEN_SERVICE_CONFIGURATION"]
    approval_mode: Literal["HUMAN"]
    approval_ttl_hours: StrictInt = Field(ge=1, le=24 * 30)
    max_atomic_actions: Literal[1]
    max_forward_mutations: Literal[1]
    max_rollbacks: Literal[1]


class VerificationConfig(FrozenModel):
    schema_version: Literal["live-sandbox.verification.v1"]
    consecutive_windows: Literal[2]
    minimum_stabilization_seconds: StrictInt = Field(ge=90)
    fault_error_rate_absolute_increase: StrictFloat = Field(gt=0)
    fault_error_rate_multiplier: StrictFloat = Field(gt=1)
    recovery_error_rate_absolute_increase: StrictFloat = Field(gt=0)
    recovery_error_rate_multiplier: StrictFloat = Field(gt=1)


class BudgetConfig(FrozenModel):
    schema_version: Literal["live-sandbox.budget.v1"]
    concurrency: Literal[1]
    minimum_request_spacing_seconds: StrictFloat = Field(ge=5)
    maximum_transport_retries: Literal[1]
    provider_timeout_seconds: StrictFloat = Field(gt=0)
    schema_retry: Literal["FORBIDDEN"]
    semantic_retry: Literal["FORBIDDEN"]
    fallback_model: Literal["FORBIDDEN"]


class ConfigBundle(FrozenModel):
    environment: EnvironmentConfig
    scenario: ScenarioConfig
    telemetry: TelemetryConfig
    diagnosis: DiagnosisConfig
    policy: PolicyConfig
    verification: VerificationConfig
    budget: BudgetConfig


def _read_model(path: Path, model_type: type[FrozenModel]) -> FrozenModel:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"configuration must be a regular file: {path.name}")
    return model_type.model_validate_json(path.read_text(encoding="utf-8"))


def load_bundle(root: Path) -> ConfigBundle:
    return ConfigBundle(
        environment=EnvironmentConfig.model_validate(
            _read_model(root / "sandbox.json", EnvironmentConfig)
        ),
        scenario=ScenarioConfig.model_validate(
            _read_model(root / "scenario.json", ScenarioConfig)
        ),
        telemetry=TelemetryConfig.model_validate(
            _read_model(root / "telemetry.json", TelemetryConfig)
        ),
        diagnosis=DiagnosisConfig.model_validate(
            _read_model(root / "diagnosis.json", DiagnosisConfig)
        ),
        policy=PolicyConfig.model_validate(
            _read_model(root / "policy.json", PolicyConfig)
        ),
        verification=VerificationConfig.model_validate(
            _read_model(root / "verification.json", VerificationConfig)
        ),
        budget=BudgetConfig.model_validate(
            _read_model(root / "budget.json", BudgetConfig)
        ),
    )


def canonical_json_bytes(value: object) -> bytes:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    return (
        json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def canonical_sha256(value: object) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def ensure_private_directory(path: Path) -> None:
    if path.exists() and (path.is_symlink() or not path.is_dir()):
        raise ValueError(f"private root is not a regular directory: {path}")
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.chmod(0o700)
    if path.stat().st_mode & 0o077:
        raise PermissionError(f"private root permissions are too broad: {path}")


def write_private_json(path: Path, value: object, *, create_once: bool) -> str:
    ensure_private_directory(path.parent)
    payload = canonical_json_bytes(value)
    if path.exists():
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"private output is not a regular file: {path}")
        if create_once and path.read_bytes() != payload:
            raise FileExistsError(f"create-once private output differs: {path}")
        if not create_once:
            path.write_bytes(payload)
    else:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        descriptor = os.open(path, flags, 0o600)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
        except Exception:
            raise
    path.chmod(0o600)
    if path.stat().st_mode & 0o077:
        raise PermissionError(f"private file permissions are too broad: {path}")
    return hashlib.sha256(payload).hexdigest()


class LocalEndpoints(FrozenModel):
    frontend: Literal["http://127.0.0.1:18080"]
    flag_control: Literal["http://127.0.0.1:18080/feature/api"]
    flag_evaluation: Literal["http://127.0.0.1:18016"]
    prometheus: Literal["http://127.0.0.1:19090"]
    opensearch: Literal["http://127.0.0.1:19200"]
    jaeger: Literal["http://127.0.0.1:11686"]


class ResolvedSandbox(FrozenModel):
    schema_version: Literal["live-sandbox.resolved-compose.v1"] = (
        "live-sandbox.resolved-compose.v1"
    )
    compose_sha256: str = Field(pattern=SHA256_PATTERN)
    services: tuple[str, ...]
    image_references: tuple[str, ...]
    endpoints: LocalEndpoints


class ConfigurationState(FrozenModel):
    variant: Literal["off", "100%"]
    value: Literal[0, 1]
    document_sha256: str = Field(pattern=SHA256_PATTERN)


class SLIWindow(FrozenModel):
    phase: Literal["PREFLIGHT", "BASELINE", "FAULT", "RECOVERY"]
    started_at: AwareDatetime
    ended_at: AwareDatetime
    request_count: StrictFloat = Field(ge=0)
    error_count: StrictFloat = Field(ge=0)
    error_rate: StrictFloat = Field(ge=0, le=1)
    p95_latency_ms: StrictFloat = Field(ge=0)
    runtime_health: StrictFloat = Field(ge=0)
    sample_count: StrictInt = Field(ge=3)

    @model_validator(mode="after")
    def require_consistent_window(self) -> "SLIWindow":
        if self.ended_at <= self.started_at:
            raise ValueError("SLI window end must follow start")
        if self.error_count > self.request_count + 1e-9:
            raise ValueError("SLI error count exceeds request count")
        expected = 0.0 if self.request_count == 0 else self.error_count / self.request_count
        if abs(expected - self.error_rate) > 1e-6:
            raise ValueError("SLI error rate differs from counts")
        return self


class LogEvidence(FrozenModel):
    observed_at: AwareDatetime
    service_name: str = Field(min_length=1, max_length=128)
    service_instance_id: str | None = Field(default=None, max_length=256)
    container_id: str | None = Field(default=None, max_length=256)
    host_id: str | None = Field(default=None, max_length=256)
    severity: str = Field(min_length=1, max_length=64)
    body: str = Field(min_length=1, max_length=2_000)
    trace_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")
    span_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{16}$")


class TraceEvidence(FrozenModel):
    trace_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    span_id: str = Field(pattern=r"^[0-9a-f]{16}$")
    parent_span_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{16}$")
    service_name: str = Field(min_length=1, max_length=128)
    service_instance_id: str | None = Field(default=None, max_length=256)
    container_id: str | None = Field(default=None, max_length=256)
    host_id: str | None = Field(default=None, max_length=256)
    span_name: str = Field(min_length=1, max_length=512)
    started_at: AwareDatetime
    duration_ms: StrictFloat = Field(ge=0)
    status: Literal["OK", "ERROR", "UNSET"]


class SourceStatus(str, Enum):
    AVAILABLE = "AVAILABLE"
    SOURCE_UNAVAILABLE = "SOURCE_UNAVAILABLE"


class LiveTelemetrySnapshot(FrozenModel):
    schema_version: Literal["live-sandbox.telemetry-snapshot.v1"] = (
        "live-sandbox.telemetry-snapshot.v1"
    )
    environment_id: str
    sandbox_id: str
    window_start: AwareDatetime
    window_end: AwareDatetime
    source_status: Mapping[Literal["METRICS", "LOGS", "TRACES"], SourceStatus]
    sli_window: SLIWindow
    logs: tuple[LogEvidence, ...]
    traces: tuple[TraceEvidence, ...]
    service_health: Mapping[str, bool]
    capture_hashes: Mapping[str, str]
    identity_fields_present: tuple[str, ...]


class DiagnosisResult(FrozenModel):
    terminal: Literal["COMPLETED", "INVALID_SCHEMA", "PROVIDER_FAILURE"]
    root_service: str | None = None
    root_entity_ref: str | None = None
    fault_type_raw: str | None = None
    fault_class: Literal[
        "LOCAL_RESOURCE", "PROPAGATION", "NETWORK", "DEPENDENCY", "APPLICATION", "UNKNOWN"
    ]
    confidence: StrictFloat | None = Field(default=None, ge=0, le=1)
    evidence_refs: tuple[str, ...] = ()
    evidence_source_types: tuple[Literal["METRICS", "LOGS", "TRACES"], ...] = ()
    summary: str = ""
    semantic_model_calls: Literal[0, 1]
    specialist_calls: Literal[0]
    fusion_calls: Literal[0]
    provider_attempts: StrictInt = Field(default=0, ge=0, le=2)
    transport_retries: StrictInt = Field(default=0, ge=0, le=1)
    usage_tokens: StrictInt | None = Field(default=None, ge=0)
    latency_seconds: StrictFloat = Field(default=0.0, ge=0)


class DiagnosisGate(FrozenModel):
    passed: bool
    reason_codes: tuple[str, ...]


class LiveRemediationPlan(FrozenModel):
    schema_version: Literal["live-sandbox.remediation-plan.v1"] = (
        "live-sandbox.remediation-plan.v1"
    )
    plan_id: str = Field(pattern=r"^plan-[0-9a-f]{16}$")
    scenario_id: str = Field(pattern=UUID_PATTERN)
    environment_id: str
    sandbox_id: str = Field(pattern=UUID_PATTERN)
    action: Literal["RESTORE_FROZEN_SERVICE_CONFIGURATION"]
    target_service: Literal["payment"]
    configuration_key: Literal["paymentFailure.defaultVariant"]
    baseline_ref: Literal["PRIVATE_FROZEN_BASELINE_DOCUMENT"]
    baseline_sha256: str = Field(pattern=SHA256_PATTERN)
    desired_value_sha256: str = Field(pattern=SHA256_PATTERN)
    diagnosis_sha256: str = Field(pattern=SHA256_PATTERN)
    atomic_actions: Literal[1]

    @staticmethod
    def template_payload(bundle: "ConfigBundle") -> dict[str, object]:
        return {
            "schema_version": "live-sandbox.plan-template.v1",
            "scenario_id": bundle.scenario.scenario_id,
            "environment_id": bundle.environment.environment_id,
            "sandbox_id": bundle.environment.sandbox_id,
            "action": bundle.policy.action,
            "target_service": bundle.scenario.target_service,
            "configuration_key": bundle.scenario.target_configuration_key,
            "baseline_ref": "PRIVATE_FROZEN_BASELINE_DOCUMENT",
            "baseline_sha256": bundle.scenario.baseline_document_sha256,
            "desired_value_sha256": hashlib.sha256(b"0").hexdigest(),
            "atomic_actions": 1,
        }


class ApprovalRequest(FrozenModel):
    schema_version: Literal["live-sandbox.approval-request.v1"] = (
        "live-sandbox.approval-request.v1"
    )
    approval_request_id: str = Field(pattern=r"^approval-[0-9a-f]{16}$")
    scenario_id: str = Field(pattern=UUID_PATTERN)
    scenario_lock_sha256: str = Field(pattern=SHA256_PATTERN)
    plan_template_sha256: str = Field(pattern=SHA256_PATTERN)
    environment_id: str
    sandbox_id: str = Field(pattern=UUID_PATTERN)
    action: Literal["RESTORE_FROZEN_SERVICE_CONFIGURATION"]
    target_service: Literal["payment"]
    configuration_key: Literal["paymentFailure.defaultVariant"]
    baseline_sha256: str = Field(pattern=SHA256_PATTERN)
    max_forward_mutations: Literal[1]
    requested_at: AwareDatetime
    expires_at: AwareDatetime

    @model_validator(mode="after")
    def require_future_expiry(self) -> "ApprovalRequest":
        if self.expires_at <= self.requested_at:
            raise ValueError("approval request expiry is not after request time")
        return self


class HumanApprovalRecord(FrozenModel):
    schema_version: Literal["live-sandbox.human-approval.v1"] = (
        "live-sandbox.human-approval.v1"
    )
    mode: Literal["HUMAN"]
    approver: str = Field(min_length=1, max_length=256)
    approval_request_id: str
    request_sha256: str = Field(pattern=SHA256_PATTERN)
    plan_template_sha256: str = Field(pattern=SHA256_PATTERN)
    scenario_id: str = Field(pattern=UUID_PATTERN)
    environment_id: str
    sandbox_id: str = Field(pattern=UUID_PATTERN)
    action: Literal["RESTORE_FROZEN_SERVICE_CONFIGURATION"]
    target_service: Literal["payment"]
    configuration_key: Literal["paymentFailure.defaultVariant"]
    baseline_sha256: str = Field(pattern=SHA256_PATTERN)
    approved_at: AwareDatetime
    expires_at: AwareDatetime


class PolicyVerdict(str, Enum):
    ALLOW = "ALLOW"
    DENY = "DENY"


class PolicyDecision(FrozenModel):
    verdict: PolicyVerdict
    reason_codes: tuple[str, ...] = Field(min_length=1)


class ExecutionReceipt(FrozenModel):
    schema_version: Literal["live-sandbox.execution-receipt.v1"] = (
        "live-sandbox.execution-receipt.v1"
    )
    receipt_id: str = Field(pattern=r"^receipt-[0-9a-f]{16}$")
    plan_sha256: str = Field(pattern=SHA256_PATTERN)
    scenario_id: str = Field(pattern=UUID_PATTERN)
    environment_id: str
    sandbox_id: str = Field(pattern=UUID_PATTERN)
    action: Literal["RESTORE_FROZEN_SERVICE_CONFIGURATION"]
    target_service: Literal["payment"]
    configuration_key: Literal["paymentFailure.defaultVariant"]
    before_sha256: str = Field(pattern=SHA256_PATTERN)
    after_sha256: str = Field(pattern=SHA256_PATTERN)
    forward_mutation_number: Literal[1]
    applied_at: AwareDatetime


class VerificationResult(FrozenModel):
    schema_version: Literal["live-sandbox.verification-result.v1"] = (
        "live-sandbox.verification-result.v1"
    )
    passed: bool
    reason_codes: tuple[str, ...]
    receipt_sha256: str = Field(pattern=SHA256_PATTERN)
    configuration_sha256: str = Field(pattern=SHA256_PATTERN)
    services_healthy: bool
    labels_exact: bool
    recovery_windows_passed: StrictInt = Field(ge=0, le=2)

    @model_validator(mode="after")
    def require_consistent_verdict(self) -> "VerificationResult":
        if self.passed == bool(self.reason_codes):
            raise ValueError("verification verdict and reasons are inconsistent")
        return self


class RollbackReceipt(FrozenModel):
    schema_version: Literal["live-sandbox.rollback-receipt.v1"] = (
        "live-sandbox.rollback-receipt.v1"
    )
    executed: bool
    before_sha256: str = Field(pattern=SHA256_PATTERN)
    restored_sha256: str = Field(pattern=SHA256_PATTERN)
    exact_hash_verified: bool


class CleanupResult(FrozenModel):
    baseline_restored: bool
    owned_containers: StrictInt = Field(ge=0)
    owned_networks: StrictInt = Field(ge=0)
    owned_volumes: StrictInt = Field(ge=0)
    non_owned_resources_changed: bool
    verdict: Literal["CLEAN", "BLOCKED"]

    @model_validator(mode="after")
    def require_clean_truth(self) -> "CleanupResult":
        clean = (
            self.baseline_restored
            and self.owned_containers == 0
            and self.owned_networks == 0
            and self.owned_volumes == 0
            and not self.non_owned_resources_changed
        )
        if (self.verdict == "CLEAN") is not clean:
            raise ValueError("cleanup verdict differs from observed state")
        return self


class RunEvent(FrozenModel):
    schema_version: Literal["live-sandbox.run-event.v1"] = "live-sandbox.run-event.v1"
    sequence: StrictInt = Field(ge=1)
    timestamp: AwareDatetime
    phase: Literal[
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
    status: str
    input_sha256: str | None = Field(default=None, pattern=SHA256_PATTERN)
    output_sha256: str | None = Field(default=None, pattern=SHA256_PATTERN)
    safe_aggregate: Mapping[str, Any]


__all__ = [
    "ACTION",
    "ApprovalRequest",
    "BudgetConfig",
    "CleanupResult",
    "ConfigBundle",
    "ConfigurationState",
    "DiagnosisGate",
    "DiagnosisResult",
    "EnvironmentConfig",
    "ExecutionReceipt",
    "HumanApprovalRecord",
    "LiveRemediationPlan",
    "LiveTelemetrySnapshot",
    "LocalEndpoints",
    "LogEvidence",
    "PolicyDecision",
    "PolicyVerdict",
    "ResolvedSandbox",
    "RollbackReceipt",
    "RunEvent",
    "SLIWindow",
    "ScenarioConfig",
    "SourceStatus",
    "TraceEvidence",
    "VerificationResult",
    "canonical_json_bytes",
    "canonical_sha256",
    "ensure_private_directory",
    "file_sha256",
    "load_bundle",
    "write_private_json",
]
