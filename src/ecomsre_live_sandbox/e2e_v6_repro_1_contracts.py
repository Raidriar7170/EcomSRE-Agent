"""Run-generation authority for the independent E2E v6 R1 lifecycle."""

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
    LiveRemediationPlan,
    canonical_sha256,
    ensure_private_directory,
    file_sha256,
    write_private_json,
)
from ecomsre_live_sandbox.e2e_v5_contracts import (
    FaultProjectionConfig,
    NoFaultReadinessConfig,
)
from ecomsre_live_sandbox.e2e_v6_contracts import (
    AssuranceConfigV6,
    DiagnosticsConfigV6,
    E2EV6PrivateRoots,
    load_e2e_v6_config,
)
from ecomsre_live_sandbox.invocation_b_verdicts import (
    invocation_b_verdict_policy_sha256,
)


E2E_V6_REPRO_1_GENERATION = "V6_REPRO_1"
E2E_V6_REPRO_1_CONFIG_RELATIVE = Path(
    "config/live-fault-a0-controlled-remediation-e2e-v6-repro-1"
)
E2E_V6_CONFIG_RELATIVE = Path(
    "config/live-fault-a0-controlled-remediation-e2e-v6"
)
ORIGINAL_V6_PUBLIC_RELATIVE = Path(
    "docs/results/live-fault-a0-controlled-remediation-e2e-v6.json"
)
ORIGINAL_V6_MARKDOWN_RELATIVE = Path(
    "docs/results/live-fault-a0-controlled-remediation-e2e-v6.md"
)
ORIGINAL_V6_BRIEF_RELATIVE = Path(
    "docs/results/live-fault-a0-controlled-remediation-e2e-v6-human-brief.md"
)


class E2EV6Repro1Authority(FrozenModel):
    schema_version: Literal["live-e2e.repro-authority.v1"]
    software_version: Literal["live-fault-a0-controlled-remediation-e2e-v6"]
    runtime_policy_version: Literal["V6"]
    run_generation: Literal["V6_REPRO_1"]
    branch: Literal[
        "feature/live-fault-a0-controlled-remediation-e2e-v6-repro-1"
    ]
    private_root_name: Literal[
        "live-fault-a0-controlled-remediation-e2e-v6-repro-1"
    ]
    cli_module: Literal["scripts.live_sandbox.e2e_v6_repro_1"]
    predecessor_pr: Literal[37]
    predecessor_result_head: Literal[
        "ef42328dfa65eab8f8b1dfda934fa5ab5bd0c41c"
    ]
    predecessor_terminal: Literal[
        "BLOCKED_E2E_V6_UNCLASSIFIED_RUNTIME_FAILURE"
    ]
    predecessor_reason: Literal[
        "INDEPENDENT_REPRODUCTION_AFTER_PREFLIGHT_SERIALIZATION_FIX"
    ]
    predecessor_sealed_terminal_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    predecessor_public_semantic_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    predecessor_final_evidence_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    predecessor_public_result_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    predecessor_public_markdown_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    predecessor_public_human_brief_sha256: str = Field(
        pattern=r"^[0-9a-f]{64}$"
    )
    software_baseline_authority_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
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
    lifecycle_policy_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    no_fault_readiness_policy_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    fault_projection_policy_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    image_authority_policy_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    compose_normalization_policy_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    invocation_b_verdict_policy_id: Literal["v6"]
    versioned_verdict_policy_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    frozen_input_hashes: dict[str, str] = Field(min_length=12)
    frozen_input_aggregate_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    scenario_id: Literal["37f142fc-9cde-4839-8184-88f2288ceced"]
    target_service: Literal["payment"]
    target_configuration_key: Literal["paymentFailure.defaultVariant"]
    baseline_document_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    fault_document_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    sli_thresholds_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    provider_budget_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    plan_template_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    plan_action: Literal["RESTORE_FROZEN_SERVICE_CONFIGURATION"]
    approval_ttl_hours: Literal[168]
    maximum_provider_calls: Literal[2]
    accepted_fault_time_runs: Literal[1]
    maximum_forward_mutations: Literal[1]
    maximum_rollbacks: Literal[1]
    development_success_terminal: Literal[
        "LIVE_E2E_V6_REPRO_1_DEVELOPMENT_INTEGRATION_PASSED"
    ]
    invocation_a_terminal: Literal[
        "LIVE_E2E_V6_REPRO_1_HUMAN_PREAUTHORIZATION_REQUIRED"
    ]
    invocation_b_success: Literal[
        "LIVE_FAULT_A0_CONTROLLED_REMEDIATION_E2E_V6_PASSED_READY_FOR_REVIEW"
    ]

    @property
    def version(self) -> str:
        return self.software_version

    @property
    def predecessor_head(self) -> str:
        return self.predecessor_result_head

    @property
    def maximum_accepted_complete_live_runs(self) -> int:
        return self.accepted_fault_time_runs


class LifecycleConfigV6Repro1(FrozenModel):
    schema_version: Literal["live-e2e.repro-lifecycle.v1"]
    development_repeat_policy: Literal[
        "REQUIRE_IMPLEMENTATION_OR_CONFIG_CHANGE_AND_PRESERVE_PRIOR_EVIDENCE"
    ]
    canonical_policy: Literal[
        "ONE_ACTIVE_ACCEPTED_PREAUTHORIZATION_PER_FROZEN_HEAD"
    ]
    human_approval_policy: Literal[
        "NEW_EXACT_R1_RECORD_REQUIRED_NO_PREDECESSOR_REUSE"
    ]
    pre_fault_retry_policy: Literal[
        "CHANGE_REQUIRED_ZERO_FAULT_MODEL_MUTATION_AND_CLEAN_OR_NOT_REQUIRED"
    ]
    accepted_run_policy: Literal[
        "CREATE_ONCE_IMMEDIATELY_BEFORE_FAULT_INJECTION"
    ]
    accepted_run_change_policy: Literal["NO_CODE_CONFIG_OR_SECOND_FAULT_TIME_RUN"]
    public_truth_policy: Literal["ACCEPTED_SEALED_PRIVATE_TERMINAL_REQUIRED"]


class ReportingConfigV6Repro1(FrozenModel):
    schema_version: Literal["live-e2e.reporting.v6-repro-1"]
    public_result_json: Literal[
        "docs/results/live-fault-a0-controlled-remediation-e2e-v6-repro-1.json"
    ]
    public_result_markdown: Literal[
        "docs/results/live-fault-a0-controlled-remediation-e2e-v6-repro-1.md"
    ]
    public_human_brief: Literal[
        "docs/results/"
        "live-fault-a0-controlled-remediation-e2e-v6-repro-1-human-brief.md"
    ]
    claim_boundary: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class E2EV6Repro1Config:
    repository_root: Path
    authority: E2EV6Repro1Authority
    lifecycle: LifecycleConfigV6Repro1
    assurance: AssuranceConfigV6
    diagnostics: DiagnosticsConfigV6
    no_fault_readiness: NoFaultReadinessConfig
    fault_projection: FaultProjectionConfig
    reporting: ReportingConfigV6Repro1
    sandbox: ConfigBundle

    @property
    def projection(self) -> FaultProjectionConfig:
        return self.fault_projection


class E2EV6Repro1PrivateRoots(E2EV6PrivateRoots):
    __slots__ = ("_live_attempt_scope_armed",)
    _live_attempt_scope_armed: bool

    runtime_policy_version = "v6"
    run_generation = E2E_V6_REPRO_1_GENERATION

    def __init__(self, root: Path) -> None:
        super().__init__(root)
        object.__setattr__(self, "_live_attempt_scope_armed", False)

    def arm_live_attempt_scope(self) -> None:
        if self._live_attempt_scope_armed:
            raise RuntimeError("R1 live-attempt scope is already armed")
        object.__setattr__(self, "_live_attempt_scope_armed", True)

    def disarm_live_attempt_scope(self) -> None:
        object.__setattr__(self, "_live_attempt_scope_armed", False)

    @property
    def live_attempts(self) -> Path:
        return self.root / "live-attempts"

    @property
    def accepted_live_run(self) -> Path:
        return self.root / "accepted-live-run"

    @property
    def active_live_attempt(self) -> Path | None:
        path = self.control / "live-attempt-active.json"
        if path.is_symlink() or not path.is_file():
            return None
        value = json.loads(path.read_text(encoding="utf-8"))
        relative = value.get("attempt_relative_path") if isinstance(value, Mapping) else None
        if not isinstance(relative, str):
            raise ValueError("R1 active live-attempt pointer is malformed")
        attempt = self.root / relative
        if attempt.resolve() == self.root.resolve() or not attempt.resolve().is_relative_to(
            self.live_attempts.resolve()
        ):
            raise ValueError("R1 active live-attempt pointer escapes its root")
        return attempt

    def live_attempt(self, index: int) -> Path:
        if index < 1:
            raise ValueError("live attempt index must be positive")
        return self.live_attempts / f"attempt-{index:04d}"

    @property
    def next_live_attempt(self) -> Path:
        history_path = self.control / "live-attempt-history.json"
        if not history_path.exists() and not history_path.is_symlink():
            return self.live_attempt(1)
        if history_path.is_symlink() or not history_path.is_file():
            raise ValueError("R1 live-attempt history is malformed")
        history = json.loads(history_path.read_text(encoding="utf-8"))
        attempts = history.get("attempts") if isinstance(history, Mapping) else None
        if not isinstance(attempts, list):
            raise ValueError("R1 live-attempt history is malformed")
        return self.live_attempt(len(attempts) + 1)

    @property
    def invocation_b(self) -> Path:
        active = self.active_live_attempt
        if active is not None:
            return active
        if self._live_attempt_scope_armed:
            return self.next_live_attempt
        return super().invocation_b

    def _attempt_scoped(self, name: str, fallback: Path) -> Path:
        active = self.active_live_attempt
        if active is not None:
            return active / name
        if self._live_attempt_scope_armed:
            return self.next_live_attempt / name
        return fallback

    @property
    def runtime(self) -> Path:
        return self._attempt_scoped("runtime", self.root / "runtime")

    @property
    def telemetry(self) -> Path:
        return self._attempt_scoped("telemetry", self.root / "telemetry")

    @property
    def provider(self) -> Path:
        return self._attempt_scoped("provider", self.root / "provider")

    @property
    def journal(self) -> Path:
        return self._attempt_scoped("journal", self.root / "journal")

    def prepare(self) -> None:
        super().prepare()
        for path in (self.live_attempts, self.accepted_live_run):
            ensure_private_directory(path)


def _read(path: Path, model: type[FrozenModel]) -> FrozenModel:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"R1 config must be a regular file: {path.name}")
    return model.model_validate_json(path.read_text(encoding="utf-8"))


def _require_repro_1_authority(
    config: E2EV6Repro1Config,
    config_root: Path,
) -> None:
    authority = config.authority
    base_root = config.repository_root / E2E_V6_CONFIG_RELATIVE
    base = load_e2e_v6_config(base_root)
    if any(
        (
            file_sha256(base_root / "authority.json")
            != authority.software_baseline_authority_sha256,
            file_sha256(config_root / "lifecycle.json")
            != authority.lifecycle_policy_sha256,
            file_sha256(config_root / "reporting.json")
            != authority.reporting_policy_sha256,
            file_sha256(config.repository_root / ORIGINAL_V6_PUBLIC_RELATIVE)
            != authority.predecessor_public_result_sha256,
            file_sha256(config.repository_root / ORIGINAL_V6_MARKDOWN_RELATIVE)
            != authority.predecessor_public_markdown_sha256,
            file_sha256(config.repository_root / ORIGINAL_V6_BRIEF_RELATIVE)
            != authority.predecessor_public_human_brief_sha256,
            invocation_b_verdict_policy_sha256(
                authority.invocation_b_verdict_policy_id
            )
            != authority.versioned_verdict_policy_sha256,
        )
    ):
        raise RuntimeError("R1 frozen authority hash binding differs")
    public = json.loads(
        (config.repository_root / ORIGINAL_V6_PUBLIC_RELATIVE).read_text(
            encoding="utf-8"
        )
    )
    if not isinstance(public, Mapping) or any(
        (
            public.get("verdict") != authority.predecessor_terminal,
            public.get("semantic_sha256")
            != authority.predecessor_public_semantic_sha256,
        )
    ):
        raise RuntimeError("R1 predecessor public result differs")
    frozen_equal = (
        "telemetry_authority_head",
        "telemetry_authority_semantic_sha256",
        "telemetry_authority_tracked_sha256",
        "a0_prompt_sha256",
        "a0_output_schema_sha256",
        "a0_model",
        "upstream_commit",
        "upstream_tag",
        "platform",
        "assurance_policy_sha256",
        "diagnostics_policy_sha256",
        "no_fault_readiness_policy_sha256",
        "fault_projection_policy_sha256",
        "image_authority_policy_sha256",
        "compose_normalization_policy_sha256",
        "versioned_verdict_policy_sha256",
        "frozen_input_hashes",
        "frozen_input_aggregate_sha256",
        "scenario_id",
        "target_service",
        "target_configuration_key",
        "baseline_document_sha256",
        "fault_document_sha256",
        "approval_ttl_hours",
        "maximum_provider_calls",
        "maximum_forward_mutations",
        "maximum_rollbacks",
        "invocation_b_success",
    )
    if any(
        getattr(authority, name) != getattr(base.authority, name)
        for name in frozen_equal
    ):
        raise RuntimeError("R1 frozen v6 software boundary differs")
    plan = LiveRemediationPlan.template_payload(config.sandbox)
    if any(
        (
            canonical_sha256(config.sandbox.verification.model_dump(mode="json"))
            != authority.sli_thresholds_sha256,
            canonical_sha256(config.sandbox.budget.model_dump(mode="json"))
            != authority.provider_budget_sha256,
            canonical_sha256(plan) != authority.plan_template_sha256,
        )
    ):
        raise RuntimeError("R1 frozen threshold, Provider, or plan binding differs")


def load_e2e_v6_repro_1_config(root: Path) -> E2EV6Repro1Config:
    root = root.resolve()
    repository_root = root.parents[1]
    base = load_e2e_v6_config(repository_root / E2E_V6_CONFIG_RELATIVE)
    config = E2EV6Repro1Config(
        repository_root=repository_root,
        authority=E2EV6Repro1Authority.model_validate(
            _read(root / "authority.json", E2EV6Repro1Authority)
        ),
        lifecycle=LifecycleConfigV6Repro1.model_validate(
            _read(root / "lifecycle.json", LifecycleConfigV6Repro1)
        ),
        assurance=base.assurance,
        diagnostics=base.diagnostics,
        no_fault_readiness=base.no_fault_readiness,
        fault_projection=base.fault_projection,
        reporting=ReportingConfigV6Repro1.model_validate(
            _read(root / "reporting.json", ReportingConfigV6Repro1)
        ),
        sandbox=base.sandbox,
    )
    _require_repro_1_authority(config, root)
    return config


def bind_repro_1_lifecycle(
    config: E2EV6Repro1Config,
    roots: E2EV6Repro1PrivateRoots,
) -> None:
    if roots.root.is_symlink():
        raise ValueError("R1 private root is a symbolic link")
    resolved = roots.root.resolve()
    if config.authority.software_version in resolved.parts:
        raise ValueError("the original v6 private root cannot be reused by R1")
    repository = config.repository_root.resolve()
    if resolved == repository or resolved.is_relative_to(repository):
        raise ValueError("R1 private root must remain outside the Git repository")
    lifecycle_path = roots.control / "private-root-lifecycle.json"
    authority_path = roots.control / "authority.json"
    if roots.root.exists() and not lifecycle_path.exists() and any(roots.root.iterdir()):
        raise ValueError("R1 private root contains unbound content")
    roots.prepare()
    expected_lifecycle: dict[str, object] = {
        "schema_version": "live-e2e.private-root-lifecycle.v6",
        "version": config.authority.version,
        "software_version": config.authority.software_version,
        "runtime_policy_version": config.authority.runtime_policy_version,
        "run_generation": config.authority.run_generation,
        "branch": config.authority.branch,
        "predecessor_pr": config.authority.predecessor_pr,
        "predecessor_head": config.authority.predecessor_head,
        "telemetry_authority_pr": config.authority.telemetry_authority_pr,
        "telemetry_authority_head": config.authority.telemetry_authority_head,
        "private_root_absolute_path_sha256": hashlib.sha256(
            str(resolved).encode("utf-8")
        ).hexdigest(),
    }
    expected_authority = config.authority.model_dump(mode="json")
    for path, expected in (
        (lifecycle_path, expected_lifecycle),
        (authority_path, expected_authority),
    ):
        if path.exists() or path.is_symlink():
            current = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(current, Mapping) or {
                key: current.get(key) for key in expected
            } != expected:
                raise ValueError("R1 private lifecycle binding differs")
            continue
        payload = (
            {**expected, "created_at": datetime.now(timezone.utc).isoformat()}
            if path == lifecycle_path
            else expected
        )
        write_private_json(path, payload, create_once=True)
    roots.verify()


__all__ = [
    "E2E_V6_REPRO_1_GENERATION",
    "E2EV6Repro1Authority",
    "E2EV6Repro1Config",
    "E2EV6Repro1PrivateRoots",
    "LifecycleConfigV6Repro1",
    "ReportingConfigV6Repro1",
    "bind_repro_1_lifecycle",
    "load_e2e_v6_repro_1_config",
]
