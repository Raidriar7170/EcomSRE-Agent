"""Frozen authority and private lifecycle for the live E2E v2 successor."""

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
    FrozenModel,
    HumanApprovalRecord,
    LiveRemediationPlan,
    canonical_json_bytes,
    canonical_sha256,
    ensure_private_directory,
    file_sha256,
    load_bundle,
    verify_private_tree_permissions,
    write_private_json,
)
from ecomsre_live_sandbox.e2e_diagnostics import (
    DiagnosticCommandIdentity,
    V2_DIAGNOSTIC_FAILURE_CODES,
    V2_DIAGNOSTIC_STAGES,
)
from ecomsre_rca100.prompt import output_schema_sha256, prompt_sha256


E2E_V2_VERSION = "live-fault-a0-controlled-remediation-e2e-v2"
V1_CONFIG_RELATIVE = Path("config/live-telemetry-controlled-remediation-v1")
V3_RESULT_RELATIVE = Path("docs/results/live-telemetry-instrumentation-v3.json")
V1_PRIVATE_VERSION = "live-fault-a0-controlled-remediation-e2e-v1"


class E2EV2Authority(FrozenModel):
    schema_version: Literal["live-e2e.authority.v2"]
    version: Literal["live-fault-a0-controlled-remediation-e2e-v2"]
    branch: Literal["feature/live-fault-a0-controlled-remediation-e2e-v2"]
    predecessor_pr: Literal[32]
    predecessor_head: Literal["c176e2423c8f9be0719013dacb5619ff446b6e09"]
    predecessor_verdict: Literal["BLOCKED_INVOCATION_A_UNCLASSIFIED_RUNTIME_FAILURE"]
    telemetry_authority_pr: Literal[31]
    telemetry_authority_head: Literal["e28a1091acba7365d7f4deb2aa61fd39e90ae3ae"]
    telemetry_authority_semantic_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    telemetry_authority_tracked_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    a0_prompt_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    a0_output_schema_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    a0_model: Literal["gpt-5.4-mini-2026-03-17"]
    upstream_commit: Literal["1755859a9de82c2e5e225be68abc401a5ebf2b4f"]
    upstream_tag: Literal["3.0.0"]
    platform: Literal["linux/arm64"]
    diagnostics_policy_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    projection_policy_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    reporting_policy_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    frozen_input_hashes: dict[str, str] = Field(min_length=12)
    frozen_input_aggregate_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    scenario_id: Literal["37f142fc-9cde-4839-8184-88f2288ceced"]
    target_service: Literal["payment"]
    target_configuration_key: Literal["paymentFailure.defaultVariant"]
    baseline_document_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    fault_document_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    approval_ttl_hours: Literal[168]
    maximum_no_fault_diagnostic_probes: Literal[2]
    maximum_canonical_invocation_a_runs: Literal[1]
    maximum_provider_calls: Literal[2]
    maximum_forward_mutations: Literal[1]
    maximum_rollbacks: Literal[1]
    maximum_complete_live_runs: Literal[1]
    diagnostic_success_terminal: Literal["LIVE_E2E_V2_DIAGNOSTIC_PREFLIGHT_PASSED"]
    invocation_a_terminal: Literal["LIVE_E2E_V2_HUMAN_PREAUTHORIZATION_REQUIRED"]
    invocation_b_success: Literal[
        "LIVE_FAULT_A0_CONTROLLED_REMEDIATION_E2E_V2_PASSED_READY_FOR_REVIEW"
    ]


class DiagnosticsConfig(FrozenModel):
    schema_version: Literal["live-e2e.diagnostics.v2"]
    event_schema_version: Literal["live-e2e.diagnostic-event.v2"]
    required_stages: tuple[str, ...]
    failure_codes: tuple[str, ...]
    command_identities: tuple[str, ...]
    directory_mode: Literal["0700"]
    file_mode: Literal["0600"]
    event_write_policy: Literal["APPEND_ONLY_FSYNC_EACH_EVENT"]
    command_policy: Literal["STATIC_ARGV_NO_SHELL"]
    exception_policy: Literal["PRIVATE_RAW_PUBLIC_HASH_ONLY"]


class ProjectionConfigV2(FrozenModel):
    schema_version: Literal["live-e2e.projection.v2"]
    visible_entity_minimum: Literal[3]
    visible_entity_maximum: Literal[8]
    metric_candidate_limit: Literal[4]
    log_raw_hit_limit: Literal[50]
    log_evidence_limit: Literal[16]
    log_per_service_limit: Literal[4]
    trace_query_limit: Literal[3]
    trace_evidence_limit: Literal[20]
    trace_neighborhood_hops: Literal[2]
    maximum_serialized_context_bytes: Literal[98304]
    service_ordering_policy: Literal["SUPPORT_THEN_METRICS_THEN_EARLIEST_THEN_NAME"]
    evidence_ordering_policy: Literal["SOURCE_SCORE_TIME_SERVICE_HASH"]
    alert_title: Literal["Observed purchase-flow request error-rate increase"]


class ReportingConfigV2(FrozenModel):
    schema_version: Literal["live-e2e.reporting.v2"]
    public_result_json: Literal["docs/results/live-fault-a0-controlled-remediation-e2e-v2.json"]
    public_result_markdown: Literal["docs/results/live-fault-a0-controlled-remediation-e2e-v2.md"]
    public_human_brief: Literal[
        "docs/results/live-fault-a0-controlled-remediation-e2e-v2-human-brief.md"
    ]
    claim_boundary: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class E2EV2Config:
    repository_root: Path
    authority: E2EV2Authority
    diagnostics: DiagnosticsConfig
    projection: ProjectionConfigV2
    reporting: ReportingConfigV2
    sandbox: ConfigBundle


@dataclass(frozen=True, slots=True)
class E2EV2PrivateRoots:
    root: Path

    @property
    def control(self) -> Path:
        return self.root / "control"

    @property
    def diagnostics(self) -> Path:
        return self.root / "diagnostics"

    @property
    def canonical(self) -> Path:
        return self.root / "canonical"

    @property
    def invocation_a(self) -> Path:
        return self.canonical / "invocation-a"

    @property
    def invocation_b(self) -> Path:
        return self.root / "live-run" / "invocation-b"

    @property
    def runtime(self) -> Path:
        return self.root / "runtime"

    @property
    def telemetry(self) -> Path:
        return self.root / "telemetry"

    @property
    def provider(self) -> Path:
        return self.root / "provider"

    @property
    def journal(self) -> Path:
        return self.root / "journal"

    @property
    def reports(self) -> Path:
        return self.root / "reports"

    def probe_root(self, index: int) -> Path:
        if index not in {1, 2}:
            raise ValueError("diagnostic probe index is outside the frozen budget")
        return self.diagnostics / f"probe-{index:02d}"

    def top_level_directories(self) -> tuple[Path, ...]:
        return (
            self.root,
            self.control,
            self.diagnostics,
            self.canonical,
            self.root / "live-run",
            self.runtime,
            self.telemetry,
            self.provider,
            self.journal,
            self.reports,
        )

    def prepare(self) -> None:
        for path in self.top_level_directories():
            ensure_private_directory(path)

    def verify(self) -> None:
        verify_private_tree_permissions(self.root)

    def _static_lifecycle(self, authority: E2EV2Authority) -> dict[str, object]:
        return {
            "schema_version": "live-e2e.private-root-lifecycle.v2",
            "version": authority.version,
            "branch": authority.branch,
            "predecessor_pr": authority.predecessor_pr,
            "predecessor_head": authority.predecessor_head,
            "telemetry_authority_pr": authority.telemetry_authority_pr,
            "telemetry_authority_head": authority.telemetry_authority_head,
            "private_root_absolute_path_sha256": hashlib.sha256(
                str(self.root.resolve()).encode("utf-8")
            ).hexdigest(),
        }

    def bind_lifecycle(self, authority: E2EV2Authority, *, repository_root: Path) -> None:
        if self.root.is_symlink():
            raise ValueError("v2 private root is a symbolic link")
        resolved = self.root.resolve()
        if V1_PRIVATE_VERSION in resolved.parts:
            raise ValueError("v1 private root cannot be reused by v2")
        if resolved == repository_root.resolve() or resolved.is_relative_to(repository_root.resolve()):
            raise ValueError("v2 private root must remain outside the Git repository")
        lifecycle_path = self.control / "private-root-lifecycle.json"
        if self.root.exists() and not lifecycle_path.exists() and any(self.root.iterdir()):
            raise ValueError("v2 private root contains unbound content")
        self.prepare()
        expected = self._static_lifecycle(authority)
        if lifecycle_path.exists() or lifecycle_path.is_symlink():
            if lifecycle_path.is_symlink() or not lifecycle_path.is_file():
                raise ValueError("v2 private lifecycle is not a regular file")
            current = json.loads(lifecycle_path.read_text(encoding="utf-8"))
            if not isinstance(current, Mapping):
                raise ValueError("v2 private lifecycle is malformed")
            static_current = {key: current.get(key) for key in expected}
            if static_current != expected or not isinstance(current.get("created_at"), str):
                raise ValueError("v2 private lifecycle binding differs")
        else:
            write_private_json(
                lifecycle_path,
                {**expected, "created_at": datetime.now(timezone.utc).isoformat()},
                create_once=True,
            )
        self.verify()


def _read(path: Path, model: type[FrozenModel]) -> FrozenModel:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"v2 config must be a regular file: {path.name}")
    return model.model_validate_json(path.read_text(encoding="utf-8"))


def _frozen_paths() -> dict[str, str]:
    return {
        "v3_environment": "config/live-telemetry-instrumentation-v3/environment.json",
        "v3_sources": "config/live-telemetry-instrumentation-v3/sources.json",
        "v3_readiness": "config/live-telemetry-instrumentation-v3/readiness.json",
        "v3_reporting": "config/live-telemetry-instrumentation-v3/reporting.json",
        "v1_scenario": "config/live-telemetry-controlled-remediation-v1/scenario.json",
        "v1_diagnosis": "config/live-telemetry-controlled-remediation-v1/diagnosis.json",
        "v1_policy": "config/live-telemetry-controlled-remediation-v1/policy.json",
        "v1_verification": "config/live-telemetry-controlled-remediation-v1/verification.json",
        "v1_budget": "config/live-telemetry-controlled-remediation-v1/budget.json",
        "v1_sandbox": "config/live-telemetry-controlled-remediation-v1/sandbox.json",
        "v1_compose": "config/live-telemetry-controlled-remediation-v1/compose.sandbox.yaml",
        "v1_otelcol": "config/live-telemetry-controlled-remediation-v1/otelcol-sandbox.yml",
    }


def _require_authority(config: E2EV2Config, config_root: Path) -> None:
    authority = config.authority
    if (
        file_sha256(config_root / "diagnostics.json") != authority.diagnostics_policy_sha256
        or file_sha256(config_root / "projection.json") != authority.projection_policy_sha256
        or file_sha256(config_root / "reporting.json") != authority.reporting_policy_sha256
    ):
        raise RuntimeError("v2 policy hash binding differs")
    paths = _frozen_paths()
    if set(paths) != set(authority.frozen_input_hashes) or any(
        file_sha256(config.repository_root / relative) != authority.frozen_input_hashes[name]
        for name, relative in paths.items()
    ):
        raise RuntimeError("frozen v3 or v1 authority input differs")
    aggregate = hashlib.sha256(
        json.dumps(
            authority.frozen_input_hashes,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    if aggregate != authority.frozen_input_aggregate_sha256:
        raise RuntimeError("frozen input aggregate differs")
    result_path = config.repository_root / V3_RESULT_RELATIVE
    result = json.loads(result_path.read_text(encoding="utf-8"))
    if (
        not isinstance(result, Mapping)
        or result.get("verdict") != "LIVE_TELEMETRY_INSTRUMENTATION_V3_READY_FOR_E2E"
        or result.get("semantic_sha256") != authority.telemetry_authority_semantic_sha256
    ):
        raise RuntimeError("frozen telemetry authority differs")
    sandbox = config.sandbox
    if (
        sandbox.scenario.scenario_id != authority.scenario_id
        or sandbox.scenario.target_service != authority.target_service
        or sandbox.scenario.target_configuration_key != authority.target_configuration_key
        or sandbox.scenario.baseline_document_sha256 != authority.baseline_document_sha256
        or sandbox.scenario.fault_document_sha256 != authority.fault_document_sha256
        or sandbox.environment.upstream_commit != authority.upstream_commit
        or sandbox.environment.upstream_tag != authority.upstream_tag
        or sandbox.environment.platform != authority.platform
        or sandbox.policy.approval_ttl_hours != authority.approval_ttl_hours
        or sandbox.policy.max_forward_mutations != authority.maximum_forward_mutations
        or sandbox.policy.max_rollbacks != authority.maximum_rollbacks
        or sandbox.diagnosis.model != authority.a0_model
        or sandbox.diagnosis.prompt_sha256 != authority.a0_prompt_sha256
        or sandbox.diagnosis.output_schema_sha256 != authority.a0_output_schema_sha256
        or prompt_sha256() != authority.a0_prompt_sha256
        or output_schema_sha256() != authority.a0_output_schema_sha256
    ):
        raise RuntimeError("frozen sandbox or A0 authority differs")
    if config.diagnostics.required_stages != tuple(
        stage.value for stage in V2_DIAGNOSTIC_STAGES
    ):
        raise RuntimeError("diagnostic stage vocabulary differs")
    if config.diagnostics.failure_codes != tuple(
        code.value for code in V2_DIAGNOSTIC_FAILURE_CODES
    ):
        raise RuntimeError("diagnostic failure vocabulary differs")
    if config.diagnostics.command_identities != tuple(
        identity.value for identity in DiagnosticCommandIdentity
    ):
        raise RuntimeError("diagnostic command vocabulary differs")


def load_e2e_v2_config(root: Path) -> E2EV2Config:
    root = root.resolve()
    repository_root = root.parents[1]
    config = E2EV2Config(
        repository_root=repository_root,
        authority=E2EV2Authority.model_validate(_read(root / "authority.json", E2EV2Authority)),
        diagnostics=DiagnosticsConfig.model_validate(_read(root / "diagnostics.json", DiagnosticsConfig)),
        projection=ProjectionConfigV2.model_validate(_read(root / "projection.json", ProjectionConfigV2)),
        reporting=ReportingConfigV2.model_validate(_read(root / "reporting.json", ReportingConfigV2)),
        sandbox=load_bundle(repository_root / V1_CONFIG_RELATIVE),
    )
    _require_authority(config, root)
    return config


def create_approval_request(
    config: E2EV2Config,
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
    canonical_approver = approver.strip()
    if not canonical_approver:
        raise ValueError("human approver is blank")
    expected_phrase = f"APPROVE {request.scenario_id} {request.plan_template_sha256}"
    if phrase != expected_phrase:
        raise ValueError("human approval phrase differs")
    record = HumanApprovalRecord(
        mode="HUMAN",
        approver=canonical_approver,
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


__all__ = [
    "DiagnosticsConfig",
    "E2E_V2_VERSION",
    "E2EV2Authority",
    "E2EV2Config",
    "E2EV2PrivateRoots",
    "ProjectionConfigV2",
    "ReportingConfigV2",
    "create_approval_request",
    "load_e2e_v2_config",
    "record_human_approval",
]
