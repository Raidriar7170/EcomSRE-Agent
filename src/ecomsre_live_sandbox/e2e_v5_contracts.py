"""Frozen authority and private lifecycle for live E2E v5."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Literal, Mapping

from pydantic import Field

from ecomsre_live_sandbox.contracts import (
    ConfigBundle,
    FrozenModel,
    ensure_private_directory,
    file_sha256,
    load_bundle,
    verify_private_tree_permissions,
    write_private_json,
)
from ecomsre_live_sandbox.e2e_diagnostics import (
    DiagnosticCommandIdentity,
    V5_DIAGNOSTIC_FAILURE_CODES,
    V5_DIAGNOSTIC_STAGES,
)
from ecomsre_live_sandbox.image_authority import COMPOSE_NORMALIZATION_POLICY_SHA256
from ecomsre_rca100.prompt import output_schema_sha256, prompt_sha256


E2E_V5_VERSION = "live-fault-a0-controlled-remediation-e2e-v5"
V1_CONFIG_RELATIVE = Path("config/live-telemetry-controlled-remediation-v1")
V3_RESULT_RELATIVE = Path("docs/results/live-telemetry-instrumentation-v3.json")
V4_IMAGE_POLICY_RELATIVE = Path(
    "config/live-fault-a0-controlled-remediation-e2e-v4/"
    "image-authority.json.schema-or-policy"
)
EARLIER_PRIVATE_VERSIONS = frozenset(
    {
        "live-fault-a0-controlled-remediation-e2e-v1",
        "live-fault-a0-controlled-remediation-e2e-v2",
        "live-fault-a0-controlled-remediation-e2e-v3",
        "live-fault-a0-controlled-remediation-e2e-v4",
    }
)


class E2EV5Authority(FrozenModel):
    schema_version: Literal["live-e2e.authority.v5"]
    version: Literal["live-fault-a0-controlled-remediation-e2e-v5"]
    branch: Literal["feature/live-fault-a0-controlled-remediation-e2e-v5"]
    predecessor_pr: Literal[35]
    predecessor_head: Literal["47a797137dbea2bf1b639196779ed9381eabd939"]
    predecessor_terminal: Literal[
        "BLOCKED_E2E_V4_DEVELOPMENT_INTEGRATION_EXHAUSTED"
    ]
    predecessor_reason: Literal[
        "NO_FAULT_READINESS_COUPLED_TO_FAULT_TIME_PROJECTION"
    ]
    telemetry_authority_pr: Literal[31]
    telemetry_authority_head: Literal[
        "e28a1091acba7365d7f4deb2aa61fd39e90ae3ae"
    ]
    telemetry_authority_semantic_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    telemetry_authority_tracked_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    a0_prompt_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    a0_output_schema_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    a0_model: Literal["gpt-5.4-mini-2026-03-17"]
    upstream_commit: Literal["1755859a9de82c2e5e225be68abc401a5ebf2b4f"]
    upstream_tag: Literal["3.0.0"]
    platform: Literal["linux/arm64"]
    diagnostics_policy_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    reporting_policy_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    development_probe_policy_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    no_fault_readiness_policy_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    fault_projection_policy_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    image_authority_policy_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    compose_normalization_policy_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    frozen_input_hashes: dict[str, str] = Field(min_length=12)
    frozen_input_aggregate_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    scenario_id: Literal["37f142fc-9cde-4839-8184-88f2288ceced"]
    target_service: Literal["payment"]
    target_configuration_key: Literal["paymentFailure.defaultVariant"]
    baseline_document_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    fault_document_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    approval_ttl_hours: Literal[168]
    maximum_development_integration_probes: Literal[2]
    maximum_canonical_invocation_a_runs: Literal[1]
    maximum_provider_calls: Literal[2]
    maximum_forward_mutations: Literal[1]
    maximum_rollbacks: Literal[1]
    maximum_complete_live_runs: Literal[1]
    development_success_terminal: Literal[
        "LIVE_E2E_V5_DEVELOPMENT_INTEGRATION_PASSED"
    ]
    invocation_a_terminal: Literal["LIVE_E2E_V5_HUMAN_PREAUTHORIZATION_REQUIRED"]
    invocation_b_success: Literal[
        "LIVE_FAULT_A0_CONTROLLED_REMEDIATION_E2E_V5_PASSED_READY_FOR_REVIEW"
    ]


class DevelopmentProbesConfig(FrozenModel):
    schema_version: Literal["live-e2e.development-probes.v5"]
    maximum_development_integration_probes: Literal[2]
    pass_terminal: Literal["LIVE_E2E_V5_DEVELOPMENT_INTEGRATION_PASSED"]
    exhausted_terminal: Literal[
        "BLOCKED_E2E_V5_DEVELOPMENT_INTEGRATION_EXHAUSTED"
    ]
    repeat_policy: Literal["REQUIRE_RUNTIME_CONFIG_AGGREGATE_CHANGE_AFTER_FAILURE"]
    pass_policy: Literal["STOP_AFTER_FIRST_PASS"]
    pass_invalidation_policy: Literal[
        "TRACKED_RUNTIME_CONFIG_CHANGE_INVALIDATES_PASS"
    ]
    test_docs_only_change_policy: Literal["DOES_NOT_INVALIDATE_PASS"]
    evidence_policy: Literal["PRIVATE_CREATE_ONCE_PER_RUN"]
    fault_injections: Literal[0]
    provider_calls: Literal[0]
    model_calls: Literal[0]
    approval_records: Literal[0]
    forward_mutations: Literal[0]
    rollback_mutations: Literal[0]


class DiagnosticsConfigV5(FrozenModel):
    schema_version: Literal["live-e2e.diagnostics.v5"]
    event_schema_version: Literal["live-e2e.diagnostic-event.v2"]
    required_stages: tuple[str, ...]
    failure_codes: tuple[str, ...]
    command_identities: tuple[str, ...]
    directory_mode: Literal["0700"]
    file_mode: Literal["0600"]
    event_write_policy: Literal["APPEND_ONLY_FSYNC_EACH_EVENT"]
    command_policy: Literal["STATIC_ARGV_NO_SHELL"]
    exception_policy: Literal["PRIVATE_RAW_PUBLIC_HASH_ONLY"]


class NoFaultReadinessConfig(FrozenModel):
    schema_version: Literal["live-e2e.no-fault-readiness-policy.v5"]
    required_services_healthy: Literal[25]
    baseline_configuration_policy: Literal["EXACT_FROZEN_DOCUMENT_HASH"]
    required_source_status: Literal["AVAILABLE"]
    required_target_record_minimum: Literal[1]
    required_broad_metric_service_minimum: Literal[3]
    logs_query_contract: Literal["COMPLETED"]
    traces_query_contract: Literal["COMPLETED"]
    evidence_ref_policy: Literal["ALL_RESOLVE_ZERO_INVALID"]
    private_permission_policy: Literal["EXACT_0700_DIRECTORIES_0600_FILES"]
    control_truth_policy: Literal["ZERO_FINDINGS"]
    anomaly_evidence_required: Literal[False]
    a0_context_allowed: Literal[False]


class FaultProjectionConfig(FrozenModel):
    schema_version: Literal["live-e2e.fault-projection.v5"]
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
    diagnostic_admission_policy: Literal["METRICS_PLUS_LOGS_OR_TRACES"]
    metric_diagnostic_policy: Literal["POSITIVE_ERROR_OR_LATENCY_DELTA"]
    log_diagnostic_policy: Literal["WARN_OR_HIGHER_OR_ERROR_TEXT"]
    trace_diagnostic_policy: Literal["ERROR_ONLY"]
    empty_source_projection_status: Literal["SOURCE_UNAVAILABLE"]
    summary_write_policy: Literal["PRIVATE_CREATE_ONCE_BEFORE_A0_BUILDER"]


class ReportingConfigV5(FrozenModel):
    schema_version: Literal["live-e2e.reporting.v5"]
    public_result_json: Literal[
        "docs/results/live-fault-a0-controlled-remediation-e2e-v5.json"
    ]
    public_result_markdown: Literal[
        "docs/results/live-fault-a0-controlled-remediation-e2e-v5.md"
    ]
    public_human_brief: Literal[
        "docs/results/live-fault-a0-controlled-remediation-e2e-v5-human-brief.md"
    ]
    claim_boundary: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class E2EV5Config:
    repository_root: Path
    authority: E2EV5Authority
    development: DevelopmentProbesConfig
    diagnostics: DiagnosticsConfigV5
    no_fault_readiness: NoFaultReadinessConfig
    fault_projection: FaultProjectionConfig
    reporting: ReportingConfigV5
    sandbox: ConfigBundle

    @property
    def projection(self) -> FaultProjectionConfig:
        return self.fault_projection


@dataclass(frozen=True, slots=True)
class E2EV5PrivateRoots:
    root: Path

    @property
    def control(self) -> Path:
        return self.root / "control"

    @property
    def development(self) -> Path:
        return self.root / "development"

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
            raise ValueError("development probe index is outside the frozen budget")
        return self.development / f"probe-{index:02d}"

    def prepare(self) -> None:
        for path in (
            self.root,
            self.control,
            self.development,
            self.canonical,
            self.root / "live-run",
            self.runtime,
            self.telemetry,
            self.provider,
            self.journal,
            self.reports,
        ):
            ensure_private_directory(path)

    def verify(self) -> None:
        verify_private_tree_permissions(self.root)

    def bind_lifecycle(self, authority: E2EV5Authority, *, repository_root: Path) -> None:
        if self.root.is_symlink():
            raise ValueError("v5 private root is a symbolic link")
        resolved = self.root.resolve()
        if any(version in resolved.parts for version in EARLIER_PRIVATE_VERSIONS):
            raise ValueError("an earlier-version private root cannot be reused by v5")
        if resolved == repository_root.resolve() or resolved.is_relative_to(
            repository_root.resolve()
        ):
            raise ValueError("v5 private root must remain outside the Git repository")
        lifecycle_path = self.control / "private-root-lifecycle.json"
        if self.root.exists() and not lifecycle_path.exists() and any(self.root.iterdir()):
            raise ValueError("v5 private root contains unbound content")
        self.prepare()
        expected = {
            "schema_version": "live-e2e.private-root-lifecycle.v5",
            "version": authority.version,
            "branch": authority.branch,
            "predecessor_pr": authority.predecessor_pr,
            "predecessor_head": authority.predecessor_head,
            "telemetry_authority_pr": authority.telemetry_authority_pr,
            "telemetry_authority_head": authority.telemetry_authority_head,
            "private_root_absolute_path_sha256": hashlib.sha256(
                str(resolved).encode("utf-8")
            ).hexdigest(),
        }
        if lifecycle_path.exists() or lifecycle_path.is_symlink():
            current = json.loads(lifecycle_path.read_text(encoding="utf-8"))
            if not isinstance(current, Mapping) or {
                key: current.get(key) for key in expected
            } != expected:
                raise ValueError("v5 private lifecycle binding differs")
        else:
            write_private_json(
                lifecycle_path,
                {**expected, "created_at": datetime.now(timezone.utc).isoformat()},
                create_once=True,
            )
        self.verify()


def _read(path: Path, model: type[FrozenModel]) -> FrozenModel:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"v5 config must be a regular file: {path.name}")
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


def _require_authority(config: E2EV5Config, root: Path) -> None:
    authority = config.authority
    policies = {
        "diagnostics.json": authority.diagnostics_policy_sha256,
        "reporting.json": authority.reporting_policy_sha256,
        "development-probes.json": authority.development_probe_policy_sha256,
        "no-fault-readiness.json": authority.no_fault_readiness_policy_sha256,
        "fault-projection.json": authority.fault_projection_policy_sha256,
    }
    if any(file_sha256(root / name) != expected for name, expected in policies.items()):
        raise RuntimeError("v5 policy hash binding differs")
    if file_sha256(config.repository_root / V4_IMAGE_POLICY_RELATIVE) != (
        authority.image_authority_policy_sha256
    ):
        raise RuntimeError("v5 image authority policy differs")
    if authority.compose_normalization_policy_sha256 != COMPOSE_NORMALIZATION_POLICY_SHA256:
        raise RuntimeError("v5 Compose normalization authority differs")
    paths = _frozen_paths()
    if set(paths) != set(authority.frozen_input_hashes) or any(
        file_sha256(config.repository_root / relative)
        != authority.frozen_input_hashes[name]
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
    result = json.loads(
        (config.repository_root / V3_RESULT_RELATIVE).read_text(encoding="utf-8")
    )
    if not isinstance(result, Mapping) or any(
        (
            result.get("verdict")
            != "LIVE_TELEMETRY_INSTRUMENTATION_V3_READY_FOR_E2E",
            result.get("semantic_sha256")
            != authority.telemetry_authority_semantic_sha256,
        )
    ):
        raise RuntimeError("frozen telemetry authority differs")
    if any(
        (
            config.sandbox.scenario.scenario_id != authority.scenario_id,
            config.sandbox.scenario.target_service != authority.target_service,
            config.sandbox.scenario.target_configuration_key
            != authority.target_configuration_key,
            config.sandbox.scenario.baseline_document_sha256
            != authority.baseline_document_sha256,
            config.sandbox.scenario.fault_document_sha256
            != authority.fault_document_sha256,
            config.sandbox.environment.upstream_commit != authority.upstream_commit,
            config.sandbox.environment.upstream_tag != authority.upstream_tag,
            config.sandbox.environment.platform != authority.platform,
            config.sandbox.policy.approval_ttl_hours != authority.approval_ttl_hours,
            config.sandbox.policy.max_forward_mutations
            != authority.maximum_forward_mutations,
            config.sandbox.policy.max_rollbacks != authority.maximum_rollbacks,
            config.sandbox.diagnosis.model != authority.a0_model,
            prompt_sha256() != authority.a0_prompt_sha256,
            output_schema_sha256() != authority.a0_output_schema_sha256,
        )
    ):
        raise RuntimeError("frozen sandbox or A0 authority differs")
    if config.diagnostics.required_stages != tuple(
        stage.value for stage in V5_DIAGNOSTIC_STAGES
    ):
        raise RuntimeError("v5 diagnostic stage vocabulary differs")
    if config.diagnostics.failure_codes != tuple(
        code.value for code in V5_DIAGNOSTIC_FAILURE_CODES
    ):
        raise RuntimeError("v5 diagnostic failure vocabulary differs")
    if config.diagnostics.command_identities != tuple(
        item.value for item in DiagnosticCommandIdentity
    ):
        raise RuntimeError("v5 diagnostic command vocabulary differs")


def load_e2e_v5_config(root: Path) -> E2EV5Config:
    root = root.resolve()
    repository_root = root.parents[1]
    config = E2EV5Config(
        repository_root=repository_root,
        authority=E2EV5Authority.model_validate(
            _read(root / "authority.json", E2EV5Authority)
        ),
        development=DevelopmentProbesConfig.model_validate(
            _read(root / "development-probes.json", DevelopmentProbesConfig)
        ),
        diagnostics=DiagnosticsConfigV5.model_validate(
            _read(root / "diagnostics.json", DiagnosticsConfigV5)
        ),
        no_fault_readiness=NoFaultReadinessConfig.model_validate(
            _read(root / "no-fault-readiness.json", NoFaultReadinessConfig)
        ),
        fault_projection=FaultProjectionConfig.model_validate(
            _read(root / "fault-projection.json", FaultProjectionConfig)
        ),
        reporting=ReportingConfigV5.model_validate(
            _read(root / "reporting.json", ReportingConfigV5)
        ),
        sandbox=load_bundle(repository_root / V1_CONFIG_RELATIVE),
    )
    _require_authority(config, root)
    return config


__all__ = [
    "E2E_V5_VERSION",
    "E2EV5Authority",
    "E2EV5Config",
    "E2EV5PrivateRoots",
    "FaultProjectionConfig",
    "NoFaultReadinessConfig",
    "load_e2e_v5_config",
]
