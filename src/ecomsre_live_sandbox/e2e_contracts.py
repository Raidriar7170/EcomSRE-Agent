"""Successor-only contracts for the one human-approved live E2E path."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
from typing import Literal, Mapping

from pydantic import Field

from ecomsre_live_sandbox.contracts import (
    ApprovalRequest,
    ConfigBundle,
    HumanApprovalRecord,
    LiveRemediationPlan,
    FrozenModel,
    canonical_json_bytes,
    canonical_sha256,
    ensure_private_directory,
    load_bundle,
    verify_private_tree_permissions,
    write_private_json,
)
from ecomsre_rca100.prompt import output_schema_sha256, prompt_sha256


E2E_VERSION = "live-fault-a0-controlled-remediation-e2e-v1"
V1_CONFIG_RELATIVE = Path("config/live-telemetry-controlled-remediation-v1")
V3_RESULT_RELATIVE = Path("docs/results/live-telemetry-instrumentation-v3.json")


class E2EAuthority(FrozenModel):
    schema_version: Literal["live-e2e.authority.v1"]
    version: Literal["live-fault-a0-controlled-remediation-e2e-v1"]
    branch: Literal["feature/live-fault-a0-controlled-remediation-e2e-v1"]
    predecessor_pr: Literal[31]
    predecessor_head: Literal["e28a1091acba7365d7f4deb2aa61fd39e90ae3ae"]
    predecessor_v3_semantic_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    predecessor_v3_tracked_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    scenario_id: Literal["37f142fc-9cde-4839-8184-88f2288ceced"]
    fault_controller_type: Literal["FLAGD_UI_WHOLE_DOCUMENT_HTTP_V1"]
    target_service: Literal["payment"]
    target_configuration_key: Literal["paymentFailure.defaultVariant"]
    baseline_document_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    fault_document_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    a0_prompt_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    a0_output_schema_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    a0_model: Literal["gpt-5.4-mini-2026-03-17"]
    invocation_a_terminal: Literal["LIVE_E2E_HUMAN_PREAUTHORIZATION_REQUIRED"]
    invocation_b_success: Literal[
        "LIVE_FAULT_A0_CONTROLLED_REMEDIATION_E2E_PASSED_READY_FOR_REVIEW"
    ]
    approval_ttl_hours: Literal[168]
    maximum_no_fault_probes: Literal[2]
    maximum_provider_calls: Literal[2]
    maximum_forward_mutations: Literal[1]
    maximum_rollbacks: Literal[1]


class ProjectionConfig(FrozenModel):
    schema_version: Literal["live-e2e.projection.v1"]
    visible_entity_minimum: Literal[3]
    visible_entity_maximum: Literal[8]
    metric_candidate_limit: Literal[4]
    log_raw_hit_limit: Literal[50]
    log_evidence_limit: Literal[16]
    log_per_service_limit: Literal[4]
    trace_query_limit: Literal[3]
    trace_evidence_limit: Literal[20]
    context_token_limit: Literal[98304]
    alert_title: Literal["Observed purchase-flow request error-rate increase"]


class ReportingConfig(FrozenModel):
    schema_version: Literal["live-e2e.reporting.v1"]
    public_result_json: Literal["docs/results/live-fault-a0-controlled-remediation-e2e-v1.json"]
    public_result_markdown: Literal["docs/results/live-fault-a0-controlled-remediation-e2e-v1.md"]
    public_human_brief: Literal[
        "docs/results/live-fault-a0-controlled-remediation-e2e-v1-human-brief.md"
    ]
    claim_boundary: tuple[
        Literal["LIVE_LOCAL_SANDBOX_DEMO"],
        Literal["CONTROLLED_FAULT_INJECTION"],
        Literal["HUMAN_APPROVED_REMEDIATION"],
        Literal["NOT_PRODUCTION"],
        Literal["NOT_EXTERNAL_BENCHMARK"],
        Literal["NOT_SECURITY_VULNERABILITY_DETECTION"],
    ]


@dataclass(frozen=True, slots=True)
class E2EConfig:
    repository_root: Path
    authority: E2EAuthority
    projection: ProjectionConfig
    reporting: ReportingConfig
    sandbox: ConfigBundle


@dataclass(frozen=True, slots=True)
class E2EPrivateRoots:
    root: Path

    @property
    def control(self) -> Path:
        return self.root / "control"

    @property
    def invocation_a(self) -> Path:
        return self.root / "invocation-a"

    @property
    def invocation_b(self) -> Path:
        return self.root / "invocation-b"

    @property
    def telemetry(self) -> Path:
        return self.root / "telemetry"

    @property
    def provider(self) -> Path:
        return self.root / "provider"

    @property
    def journal(self) -> Path:
        return self.root / "journal"

    def prepare(self) -> None:
        for path in (
            self.root,
            self.control,
            self.invocation_a,
            self.invocation_b,
            self.telemetry,
            self.provider,
            self.journal,
        ):
            ensure_private_directory(path)

    def verify(self) -> None:
        verify_private_tree_permissions(self.root)


def _read(path: Path, model: type[FrozenModel]) -> FrozenModel:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"E2E config must be a regular file: {path.name}")
    return model.model_validate_json(path.read_text(encoding="utf-8"))


def _require_predecessor(config: E2EConfig) -> None:
    result_path = config.repository_root / V3_RESULT_RELATIVE
    if result_path.is_symlink() or not result_path.is_file():
        raise RuntimeError("frozen v3 result is unavailable")
    result = json.loads(result_path.read_text(encoding="utf-8"))
    if not isinstance(result, Mapping):
        raise RuntimeError("frozen v3 result is malformed")
    if result.get("semantic_sha256") != config.authority.predecessor_v3_semantic_sha256:
        raise RuntimeError("frozen v3 semantic result drifted")
    if result.get("verdict") != "LIVE_TELEMETRY_INSTRUMENTATION_V3_READY_FOR_E2E":
        raise RuntimeError("frozen v3 terminal differs")
    sandbox = config.sandbox
    authority = config.authority
    if (
        sandbox.scenario.scenario_id != authority.scenario_id
        or sandbox.scenario.target_service != authority.target_service
        or sandbox.scenario.target_configuration_key != authority.target_configuration_key
        or sandbox.scenario.baseline_document_sha256 != authority.baseline_document_sha256
        or sandbox.scenario.fault_document_sha256 != authority.fault_document_sha256
        or sandbox.policy.approval_ttl_hours != authority.approval_ttl_hours
        or sandbox.policy.max_forward_mutations != authority.maximum_forward_mutations
        or sandbox.policy.max_rollbacks != authority.maximum_rollbacks
    ):
        raise RuntimeError("frozen sandbox authority differs from successor lock")
    if (
        sandbox.diagnosis.prompt_sha256 != authority.a0_prompt_sha256
        or sandbox.diagnosis.output_schema_sha256 != authority.a0_output_schema_sha256
        or sandbox.diagnosis.model != authority.a0_model
        or prompt_sha256() != authority.a0_prompt_sha256
        or output_schema_sha256() != authority.a0_output_schema_sha256
    ):
        raise RuntimeError("frozen A0 prompt or output schema drifted")


def load_e2e_config(root: Path) -> E2EConfig:
    root = root.resolve()
    repository_root = root.parents[1]
    config = E2EConfig(
        repository_root=repository_root,
        authority=E2EAuthority.model_validate(_read(root / "authority.json", E2EAuthority)),
        projection=ProjectionConfig.model_validate(_read(root / "projection.json", ProjectionConfig)),
        reporting=ReportingConfig.model_validate(_read(root / "reporting.json", ReportingConfig)),
        sandbox=load_bundle(repository_root / V1_CONFIG_RELATIVE),
    )
    _require_predecessor(config)
    return config


def create_approval_request(
    config: E2EConfig,
    *,
    scenario_lock: Mapping[str, object],
    now: datetime | None = None,
) -> ApprovalRequest:
    timestamp = now or datetime.now(timezone.utc)
    template = LiveRemediationPlan.template_payload(config.sandbox)
    lock_sha256 = canonical_sha256(scenario_lock)
    plan_sha256 = canonical_sha256(template)
    request_id = hashlib.sha256(
        canonical_json_bytes(
            {
                "version": config.authority.version,
                "scenario_lock_sha256": lock_sha256,
                "plan_template_sha256": plan_sha256,
            }
        )
    ).hexdigest()[:16]
    return ApprovalRequest(
        approval_request_id=f"approval-{request_id}",
        scenario_id=config.sandbox.scenario.scenario_id,
        scenario_lock_sha256=lock_sha256,
        plan_template_sha256=plan_sha256,
        environment_id=config.sandbox.environment.environment_id,
        sandbox_id=config.sandbox.environment.sandbox_id,
        action=config.sandbox.policy.action,
        target_service=config.sandbox.scenario.target_service,
        configuration_key=config.sandbox.scenario.target_configuration_key,
        baseline_sha256=config.sandbox.scenario.baseline_document_sha256,
        max_forward_mutations=config.sandbox.policy.max_forward_mutations,
        requested_at=timestamp,
        expires_at=timestamp + timedelta(hours=config.authority.approval_ttl_hours),
    )


def record_human_approval(
    request: ApprovalRequest,
    *,
    approver: str,
    phrase: str,
    now: datetime,
    destination: Path,
) -> HumanApprovalRecord:
    if now > request.expires_at:
        raise ValueError("approval request is expired")
    expected_phrase = f"APPROVE {request.scenario_id} {request.plan_template_sha256}"
    if phrase != expected_phrase:
        raise ValueError("human approval phrase differs")
    record = HumanApprovalRecord(
        mode="HUMAN",
        approver=approver,
        approval_request_id=request.approval_request_id,
        request_sha256=canonical_sha256(request),
        plan_template_sha256=request.plan_template_sha256,
        scenario_id=request.scenario_id,
        environment_id=request.environment_id,
        sandbox_id=request.sandbox_id,
        action=request.action,
        target_service=request.target_service,
        configuration_key=request.configuration_key,
        baseline_sha256=request.baseline_sha256,
        approved_at=now,
        expires_at=request.expires_at,
    )
    write_private_json(destination, record, create_once=True)
    return record


def scan_public_e2e_payload(value: object) -> tuple[str, ...]:
    forbidden = (
        "private",
        "provider_response",
        "api_key",
        "approval_phrase",
        "control_key",
        "paymentfailure",
        "defaultvariant",
        "scenario_id",
        "sandbox_id",
        "localhost",
        "127.0.0.1",
        "/users/",
        "trace_id",
        "span_id",
        "container_id",
    )
    findings: set[str] = set()

    def visit(item: object) -> None:
        if isinstance(item, Mapping):
            for key, nested in item.items():
                key_text = str(key).casefold()
                findings.update(marker for marker in forbidden if marker in key_text)
                if key_text == "authorization":
                    findings.add("authorization")
                visit(nested)
        elif isinstance(item, (list, tuple)):
            for nested in item:
                visit(nested)
        elif isinstance(item, str):
            text = item.casefold()
            findings.update(marker for marker in forbidden if marker in text)

    visit(value)
    return tuple(sorted(findings))


__all__ = [
    "E2EConfig",
    "E2EPrivateRoots",
    "E2E_VERSION",
    "create_approval_request",
    "load_e2e_config",
    "record_human_approval",
    "scan_public_e2e_payload",
]
