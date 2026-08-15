"""Frozen authority and private lifecycle for the final live E2E v6 successor."""

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
from ecomsre_live_sandbox.e2e_v5_contracts import (
    FaultProjectionConfig,
    NoFaultReadinessConfig,
)
from ecomsre_live_sandbox.image_authority import COMPOSE_NORMALIZATION_POLICY_SHA256
from ecomsre_live_sandbox.invocation_b_verdicts import (
    invocation_b_verdict_policy_sha256,
)
from ecomsre_rca100.prompt import output_schema_sha256, prompt_sha256


E2E_V6_VERSION = "live-fault-a0-controlled-remediation-e2e-v6"
V1_CONFIG_RELATIVE = Path("config/live-telemetry-controlled-remediation-v1")
V3_RESULT_RELATIVE = Path("docs/results/live-telemetry-instrumentation-v3.json")
V4_IMAGE_POLICY_RELATIVE = Path(
    "config/live-fault-a0-controlled-remediation-e2e-v4/"
    "image-authority.json.schema-or-policy"
)
V5_CONFIG_RELATIVE = Path("config/live-fault-a0-controlled-remediation-e2e-v5")
EARLIER_PRIVATE_VERSIONS = frozenset(
    f"live-fault-a0-controlled-remediation-e2e-v{version}"
    for version in range(1, 6)
)


class E2EV6Authority(FrozenModel):
    schema_version: Literal["live-e2e.authority.v6"]
    version: Literal["live-fault-a0-controlled-remediation-e2e-v6"]
    branch: Literal["feature/live-fault-a0-controlled-remediation-e2e-v6"]
    predecessor_pr: Literal[36]
    predecessor_head: Literal["5080f495b070669bd016bdddd52705fcd22b4abe"]
    predecessor_terminal: Literal["BLOCKED_E2E_V5_PRE_LIVE_REVIEW"]
    predecessor_reason: Literal["INVOCATION_B_ASSURANCE_CLOSURE_REQUIRED"]
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
    assurance_policy_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    diagnostics_policy_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    reporting_policy_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    no_fault_readiness_policy_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    fault_projection_policy_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    image_authority_policy_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    compose_normalization_policy_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    versioned_verdict_policy_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    frozen_input_hashes: dict[str, str] = Field(min_length=12)
    frozen_input_aggregate_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    scenario_id: Literal["37f142fc-9cde-4839-8184-88f2288ceced"]
    target_service: Literal["payment"]
    target_configuration_key: Literal["paymentFailure.defaultVariant"]
    baseline_document_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    fault_document_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    approval_ttl_hours: Literal[168]
    maximum_provider_calls: Literal[2]
    maximum_forward_mutations: Literal[1]
    maximum_rollbacks: Literal[1]
    maximum_accepted_complete_live_runs: Literal[1]
    development_success_terminal: Literal[
        "LIVE_E2E_V6_DEVELOPMENT_INTEGRATION_PASSED"
    ]
    invocation_a_terminal: Literal["LIVE_E2E_V6_HUMAN_PREAUTHORIZATION_REQUIRED"]
    invocation_b_success: Literal[
        "LIVE_FAULT_A0_CONTROLLED_REMEDIATION_E2E_V6_PASSED_READY_FOR_REVIEW"
    ]


class AssuranceConfigV6(FrozenModel):
    schema_version: Literal["live-e2e.assurance.v6"]
    development_repeat_policy: Literal[
        "REQUIRE_IMPLEMENTATION_OR_CONFIG_CHANGE_AND_PRESERVE_PRIOR_EVIDENCE"
    ]
    development_success_policy: Literal["LATEST_EXACT_HEAD_PASS_LOCK_REQUIRED"]
    pre_live_policy: Literal["REPAIR_IN_SCOPE_MUST_FIX_UNTIL_ZERO"]
    canonical_policy: Literal[
        "ONE_ACCEPTED_HUMAN_PREAUTHORIZATION_PER_FROZEN_HEAD"
    ]
    live_policy: Literal["ONE_ACCEPTED_COMPLETE_LIVE_RUN"]
    context_write_policy: Literal["PRIVATE_CREATE_ONCE"]
    public_truth_policy: Literal["SEALED_PRIVATE_TERMINAL_REQUIRED"]


class DiagnosticsConfigV6(FrozenModel):
    schema_version: Literal["live-e2e.diagnostics.v6"]
    event_schema_version: Literal["live-e2e.diagnostic-event.v2"]
    required_stages: tuple[str, ...]
    failure_codes: tuple[str, ...]
    command_identities: tuple[str, ...]
    directory_mode: Literal["0700"]
    file_mode: Literal["0600"]
    event_write_policy: Literal["APPEND_ONLY_FSYNC_EACH_EVENT"]
    command_policy: Literal["STATIC_ARGV_NO_SHELL"]
    exception_policy: Literal["PRIVATE_RAW_PUBLIC_HASH_ONLY"]


class ReportingConfigV6(FrozenModel):
    schema_version: Literal["live-e2e.reporting.v6"]
    public_result_json: Literal[
        "docs/results/live-fault-a0-controlled-remediation-e2e-v6.json"
    ]
    public_result_markdown: Literal[
        "docs/results/live-fault-a0-controlled-remediation-e2e-v6.md"
    ]
    public_human_brief: Literal[
        "docs/results/live-fault-a0-controlled-remediation-e2e-v6-human-brief.md"
    ]
    claim_boundary: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class E2EV6Config:
    repository_root: Path
    authority: E2EV6Authority
    assurance: AssuranceConfigV6
    diagnostics: DiagnosticsConfigV6
    no_fault_readiness: NoFaultReadinessConfig
    fault_projection: FaultProjectionConfig
    reporting: ReportingConfigV6
    sandbox: ConfigBundle

    @property
    def projection(self) -> FaultProjectionConfig:
        return self.fault_projection


@dataclass(frozen=True, slots=True)
class E2EV6PrivateRoots:
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
        accepted = self.control / "canonical-accepted.json"
        if accepted.is_file() and not accepted.is_symlink():
            value = json.loads(accepted.read_text(encoding="utf-8"))
            relative = value.get("attempt_relative_path")
            if isinstance(relative, str):
                return self.root / relative
        active = self.control / "canonical-active.json"
        if active.is_file() and not active.is_symlink():
            value = json.loads(active.read_text(encoding="utf-8"))
            relative = value.get("attempt_relative_path")
            if isinstance(relative, str):
                return self.root / relative
        return self.canonical_attempt(1)

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
        if index < 1:
            raise ValueError("development run index must be positive")
        return self.development / f"run-{index:04d}"

    def canonical_attempt(self, index: int) -> Path:
        if index < 1:
            raise ValueError("canonical attempt index must be positive")
        return self.canonical / f"attempt-{index:04d}"

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

    def bind_lifecycle(self, authority: E2EV6Authority, *, repository_root: Path) -> None:
        if self.root.is_symlink():
            raise ValueError("v6 private root is a symbolic link")
        resolved = self.root.resolve()
        if any(version in resolved.parts for version in EARLIER_PRIVATE_VERSIONS):
            raise ValueError("an earlier-version private root cannot be reused by v6")
        repository = repository_root.resolve()
        if resolved == repository or resolved.is_relative_to(repository):
            raise ValueError("v6 private root must remain outside the Git repository")
        lifecycle_path = self.control / "private-root-lifecycle.json"
        if self.root.exists() and not lifecycle_path.exists() and any(self.root.iterdir()):
            raise ValueError("v6 private root contains unbound content")
        self.prepare()
        expected = {
            "schema_version": "live-e2e.private-root-lifecycle.v6",
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
                raise ValueError("v6 private lifecycle binding differs")
        else:
            write_private_json(
                lifecycle_path,
                {**expected, "created_at": datetime.now(timezone.utc).isoformat()},
                create_once=True,
            )
        self.verify()


def _read(path: Path, model: type[FrozenModel]) -> FrozenModel:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"v6 config must be a regular file: {path.name}")
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


def _require_authority(config: E2EV6Config, root: Path) -> None:
    authority = config.authority
    policies = {
        "assurance.json": authority.assurance_policy_sha256,
        "diagnostics.json": authority.diagnostics_policy_sha256,
        "reporting.json": authority.reporting_policy_sha256,
    }
    if any(file_sha256(root / name) != expected for name, expected in policies.items()):
        raise RuntimeError("v6 policy hash binding differs")
    if file_sha256(config.repository_root / V5_CONFIG_RELATIVE / "no-fault-readiness.json") != (
        authority.no_fault_readiness_policy_sha256
    ):
        raise RuntimeError("v6 No-Fault Readiness policy differs")
    if file_sha256(config.repository_root / V5_CONFIG_RELATIVE / "fault-projection.json") != (
        authority.fault_projection_policy_sha256
    ):
        raise RuntimeError("v6 fault projection policy differs")
    if file_sha256(config.repository_root / V4_IMAGE_POLICY_RELATIVE) != (
        authority.image_authority_policy_sha256
    ):
        raise RuntimeError("v6 image authority policy differs")
    if authority.compose_normalization_policy_sha256 != COMPOSE_NORMALIZATION_POLICY_SHA256:
        raise RuntimeError("v6 Compose normalization authority differs")
    if authority.versioned_verdict_policy_sha256 != invocation_b_verdict_policy_sha256(
        "v6"
    ):
        raise RuntimeError("v6 Invocation B verdict policy differs")
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
        raise RuntimeError("v6 diagnostic stage vocabulary differs")
    if config.diagnostics.failure_codes != tuple(
        code.value for code in V5_DIAGNOSTIC_FAILURE_CODES
    ):
        raise RuntimeError("v6 diagnostic failure vocabulary differs")
    if config.diagnostics.command_identities != tuple(
        item.value for item in DiagnosticCommandIdentity
    ):
        raise RuntimeError("v6 diagnostic command vocabulary differs")


def load_e2e_v6_config(root: Path) -> E2EV6Config:
    root = root.resolve()
    repository_root = root.parents[1]
    config = E2EV6Config(
        repository_root=repository_root,
        authority=E2EV6Authority.model_validate(_read(root / "authority.json", E2EV6Authority)),
        assurance=AssuranceConfigV6.model_validate(_read(root / "assurance.json", AssuranceConfigV6)),
        diagnostics=DiagnosticsConfigV6.model_validate(_read(root / "diagnostics.json", DiagnosticsConfigV6)),
        no_fault_readiness=NoFaultReadinessConfig.model_validate(
            _read(repository_root / V5_CONFIG_RELATIVE / "no-fault-readiness.json", NoFaultReadinessConfig)
        ),
        fault_projection=FaultProjectionConfig.model_validate(
            _read(repository_root / V5_CONFIG_RELATIVE / "fault-projection.json", FaultProjectionConfig)
        ),
        reporting=ReportingConfigV6.model_validate(_read(root / "reporting.json", ReportingConfigV6)),
        sandbox=load_bundle(repository_root / V1_CONFIG_RELATIVE),
    )
    _require_authority(config, root)
    return config


__all__ = [
    "AssuranceConfigV6",
    "E2E_V6_VERSION",
    "E2EV6Authority",
    "E2EV6Config",
    "E2EV6PrivateRoots",
    "load_e2e_v6_config",
]
