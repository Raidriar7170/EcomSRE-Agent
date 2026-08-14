"""Frozen contracts and private lifecycle for the one-session LOCAL_DEMO."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import stat
from typing import Literal, Mapping

from pydantic import Field

from ecomsre_live_sandbox.contracts import (
    ConfigBundle,
    FrozenModel,
    LocalDemoStandingAuthorization,
    canonical_sha256,
    ensure_private_directory,
    file_sha256,
    verify_private_tree_permissions,
    write_private_json,
)
from ecomsre_live_sandbox.e2e_v5_contracts import (
    FaultProjectionConfig,
    NoFaultReadinessConfig,
)
from ecomsre_live_sandbox.e2e_v6_contracts import (
    AssuranceConfigV6,
    DiagnosticsConfigV6,
)
from ecomsre_live_sandbox.e2e_v6_repro_3_contracts import (
    E2EV6Repro3Authority,
    E2EV6Repro3Config,
    ProjectionCapacityConfigV6Repro3,
    load_e2e_v6_repro_3_config,
)


LOCAL_DEMO_CONFIG_RELATIVE = Path("config/local-e2e-demo-v1")
R3_CONFIG_RELATIVE = Path(
    "config/live-fault-a0-controlled-remediation-e2e-v6-repro-3"
)
R3_PUBLIC_RELATIVE = Path(
    "docs/results/live-fault-a0-controlled-remediation-e2e-v6-repro-3.json"
)
R3_MARKDOWN_RELATIVE = Path(
    "docs/results/live-fault-a0-controlled-remediation-e2e-v6-repro-3.md"
)
R3_BRIEF_RELATIVE = Path(
    "docs/results/live-fault-a0-controlled-remediation-e2e-v6-repro-3-human-brief.md"
)
_REQUIRED_PROVIDER_KEYS = (
    "ECOMSRE_LLM_BASE_URL",
    "ECOMSRE_LLM_API_KEY",
    "ECOMSRE_LLM_MODEL",
)


class LocalDemoAuthorityConfig(FrozenModel):
    schema_version: Literal["live-e2e.local-demo-authority.v1"]
    mode: Literal["LOCAL_DEMO"]
    classification: Literal["POST_FAILURE_REGRESSION_DEMO"]
    branch: Literal["feature/local-e2e-demo-v1"]
    starting_pr: Literal[40]
    starting_head: Literal["f939824c9b33eca69939aab5d6aa6a5097123e7e"]
    predecessor_terminal: Literal[
        "LIVE_DIAGNOSIS_GATE_NOT_PASSED_NO_REMEDIATION"
    ]
    private_root_name: Literal["local-e2e-demo-v1"]
    cli_module: Literal["scripts.live_sandbox.local_e2e_demo_v1"]
    r3_authority_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    r3_public_result_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    r3_public_markdown_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    r3_public_human_brief_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    lifecycle_policy_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    reporting_policy_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    development_success_terminal: Literal["LOCAL_DEMO_NO_FAULT_SMOKE_PASSED"]
    invocation_b_success: Literal["LOCAL_DEMO_E2E_PASSED_READY_FOR_REVIEW"]


class LocalDemoLifecycleConfig(FrozenModel):
    schema_version: Literal["live-e2e.local-demo-lifecycle.v1"]
    attempt_policy: Literal["REQUIRE_REAL_CHANGE_AFTER_FAILURE"]
    authorization_policy: Literal[
        "ONE_SEMANTIC_SCOPE_BOUND_STANDING_AUTHORIZATION"
    ]
    strict_gate_policy: Literal["PRESERVE_FOR_DIAGNOSIS_QUALITY_AUDIT"]
    local_demo_gate_policy: Literal["ROOT_AND_EVIDENCE_SAFETY_ADMISSION"]
    fault_class_policy: Literal["MISMATCH_IS_VISIBLE_WARNING_NOT_BLOCKER"]
    mutation_policy: Literal[
        "ONE_FORWARD_AND_AT_MOST_ONE_COMPENSATING_ROLLBACK_PER_ATTEMPT"
    ]
    cleanup_policy: Literal[
        "BASELINE_AND_ZERO_OWNED_RESOURCES_REQUIRED_EVERY_ATTEMPT"
    ]
    public_truth_policy: Literal[
        "ONLY_SUCCESSFUL_SEALED_ATTEMPT_PROJECTS_PUBLIC_RESULT"
    ]


class LocalDemoReportingConfig(FrozenModel):
    schema_version: Literal["live-e2e.local-demo-reporting.v1"]
    public_result_json: Literal["docs/results/local-e2e-demo-v1.json"]
    public_result_markdown: Literal["docs/results/local-e2e-demo-v1.md"]
    public_human_brief: Literal[
        "docs/results/local-e2e-demo-v1-human-brief.md"
    ]
    claim_boundary: tuple[str, ...]


@dataclass(frozen=True)
class LocalDemoRuntimeAuthority:
    """v6-compatible authority view with a LOCAL_DEMO lifecycle identity."""

    local: LocalDemoAuthorityConfig
    base: E2EV6Repro3Authority

    version = "live-fault-a0-controlled-remediation-e2e-v6"
    software_version = "live-fault-a0-controlled-remediation-e2e-v6"
    runtime_policy_version = "V6"
    invocation_b_verdict_policy_id = "v6"
    run_generation = "LOCAL_DEMO"
    predecessor_reason = "POST_FAILURE_REGRESSION_DEMO_AFTER_R3_CLASS_MISMATCH"
    predecessor_public_terminal = "LIVE_DIAGNOSIS_GATE_NOT_PASSED_NO_REMEDIATION"
    predecessor_sealed_source_verdict = (
        "LIVE_DIAGNOSIS_GATE_NOT_PASSED_NO_REMEDIATION"
    )
    maximum_accepted_complete_live_runs = 1
    accepted_fault_time_runs = 1
    invocation_a_terminal = "LOCAL_DEMO_STANDING_PREAUTHORIZATION_ACTIVE"

    @property
    def branch(self) -> str:
        return self.local.branch

    @property
    def private_root_name(self) -> str:
        return self.local.private_root_name

    @property
    def cli_module(self) -> str:
        return self.local.cli_module

    @property
    def predecessor_pr(self) -> int:
        return self.local.starting_pr

    @property
    def predecessor_head(self) -> str:
        return self.local.starting_head

    @property
    def predecessor_result_head(self) -> str:
        return self.local.starting_head

    @property
    def predecessor_terminal(self) -> str:
        return self.local.predecessor_terminal

    @property
    def development_success_terminal(self) -> str:
        return self.local.development_success_terminal

    @property
    def invocation_b_success(self) -> str:
        return self.local.invocation_b_success

    def __getattr__(self, name: str) -> object:
        return getattr(self.base, name)


@dataclass(frozen=True, slots=True)
class LocalDemoConfig:
    repository_root: Path
    local_authority: LocalDemoAuthorityConfig
    authority: LocalDemoRuntimeAuthority
    lifecycle: LocalDemoLifecycleConfig
    assurance: AssuranceConfigV6
    diagnostics: DiagnosticsConfigV6
    no_fault_readiness: NoFaultReadinessConfig
    fault_projection: FaultProjectionConfig
    projection_capacity: ProjectionCapacityConfigV6Repro3
    reporting: LocalDemoReportingConfig
    sandbox: ConfigBundle
    r3: E2EV6Repro3Config

    @property
    def projection(self) -> FaultProjectionConfig:
        return self.fault_projection


def _read_model(path: Path, model: type[FrozenModel]) -> FrozenModel:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"LOCAL_DEMO config must be a regular file: {path.name}")
    return model.model_validate_json(path.read_text(encoding="utf-8"))


def load_local_demo_config(root: Path) -> LocalDemoConfig:
    root = root.resolve()
    repository_root = root.parents[1]
    local = LocalDemoAuthorityConfig.model_validate(
        _read_model(root / "authority.json", LocalDemoAuthorityConfig)
    )
    lifecycle = LocalDemoLifecycleConfig.model_validate(
        _read_model(root / "lifecycle.json", LocalDemoLifecycleConfig)
    )
    reporting = LocalDemoReportingConfig.model_validate(
        _read_model(root / "reporting.json", LocalDemoReportingConfig)
    )
    r3 = load_e2e_v6_repro_3_config(repository_root / R3_CONFIG_RELATIVE)
    frozen = {
        repository_root / R3_CONFIG_RELATIVE / "authority.json": (
            local.r3_authority_sha256
        ),
        repository_root / R3_PUBLIC_RELATIVE: local.r3_public_result_sha256,
        repository_root / R3_MARKDOWN_RELATIVE: local.r3_public_markdown_sha256,
        repository_root / R3_BRIEF_RELATIVE: local.r3_public_human_brief_sha256,
        root / "lifecycle.json": local.lifecycle_policy_sha256,
        root / "reporting.json": local.reporting_policy_sha256,
    }
    if any(file_sha256(path) != expected for path, expected in frozen.items()):
        raise RuntimeError("LOCAL_DEMO frozen authority hash binding differs")
    public = json.loads((repository_root / R3_PUBLIC_RELATIVE).read_text("utf-8"))
    if not isinstance(public, Mapping) or any(
        (
            public.get("verdict") != local.predecessor_terminal,
            public.get("forward_mutations") != 0,
            public.get("rollback_mutations") != 0,
            public.get("cleanup", {}).get("verdict") != "CLEAN"
            if isinstance(public.get("cleanup"), Mapping)
            else True,
        )
    ):
        raise RuntimeError("LOCAL_DEMO R3 predecessor evidence differs")
    return LocalDemoConfig(
        repository_root=repository_root,
        local_authority=local,
        authority=LocalDemoRuntimeAuthority(local=local, base=r3.authority),
        lifecycle=lifecycle,
        assurance=r3.assurance,
        diagnostics=r3.diagnostics,
        no_fault_readiness=r3.no_fault_readiness,
        fault_projection=r3.fault_projection,
        projection_capacity=r3.projection_capacity,
        reporting=reporting,
        sandbox=r3.sandbox,
        r3=r3,
    )


def validate_provider_env(path: Path) -> tuple[dict[str, str], dict[str, object]]:
    """Validate and parse the private env without returning secret metadata."""

    if path.is_symlink() or not path.is_file():
        raise ValueError("Provider env must be a regular non-symlink file")
    details = path.stat()
    if stat.S_IMODE(details.st_mode) != 0o600:
        raise ValueError("Provider env mode must be exactly 0600")
    if details.st_uid != os.getuid():
        raise ValueError("Provider env owner differs from the current user")
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export ") or "=" not in line:
            raise ValueError("Provider env contains unsupported shell syntax")
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if key not in _REQUIRED_PROVIDER_KEYS or not value:
            raise ValueError("Provider env contains an unknown or empty variable")
        if key in values:
            raise ValueError("Provider env contains a duplicate variable")
        values[key] = value
    if set(values) != set(_REQUIRED_PROVIDER_KEYS):
        raise ValueError("Provider env does not contain exactly three variables")
    return values, {
        "provider_config_valid": True,
        "required_variable_count": 3,
        "model": values["ECOMSRE_LLM_MODEL"],
    }


@dataclass(frozen=True, slots=True)
class LocalDemoPrivateRoot:
    root: Path

    def prepare(self) -> None:
        if self.root.is_symlink():
            raise ValueError("LOCAL_DEMO private root is a symbolic link")
        for path in (self.root, self.root / "attempts", self.root / "final"):
            ensure_private_directory(path)

    def verify(self) -> None:
        verify_private_tree_permissions(self.root)

    def ensure_standing_authorization(
        self, config: LocalDemoConfig
    ) -> LocalDemoStandingAuthorization:
        self.prepare()
        path = self.root / "authorization.json"
        if path.exists() or path.is_symlink():
            if path.is_symlink() or not path.is_file():
                raise ValueError("standing authorization is not a regular file")
            value = LocalDemoStandingAuthorization.model_validate_json(
                path.read_text(encoding="utf-8")
            )
            self.validate_standing_authorization(value, config.sandbox)
            return value
        bundle = config.sandbox
        value = LocalDemoStandingAuthorization(
            approver="Minghong Sun",
            environment_id=bundle.environment.environment_id,
            sandbox_id=bundle.environment.sandbox_id,
            scenario_id=bundle.scenario.scenario_id,
            target_service=bundle.scenario.target_service,
            configuration_key=bundle.scenario.target_configuration_key,
            action="RESTORE_FROZEN_SERVICE_CONFIGURATION",
            baseline_sha256=bundle.scenario.baseline_document_sha256,
            created_at=datetime.now(timezone.utc),
        )
        write_private_json(path, value, create_once=True)
        self.verify()
        return value

    @staticmethod
    def validate_standing_authorization(
        value: LocalDemoStandingAuthorization,
        bundle: ConfigBundle,
    ) -> None:
        expected = (
            bundle.environment.environment_id,
            bundle.environment.sandbox_id,
            bundle.scenario.scenario_id,
            bundle.scenario.target_service,
            bundle.scenario.target_configuration_key,
            bundle.scenario.baseline_document_sha256,
        )
        actual = (
            value.environment_id,
            value.sandbox_id,
            value.scenario_id,
            value.target_service,
            value.configuration_key,
            value.baseline_sha256,
        )
        if actual != expected or value.action != "RESTORE_FROZEN_SERVICE_CONFIGURATION":
            raise ValueError("standing authorization semantic scope differs")

    def record_pre_live_admission(
        self,
        *,
        implementation_commit: str,
        ci_workflow: str,
        ci_run_id: int,
        reviewer_must_fix_count: int,
        recorded_at: datetime,
    ) -> dict[str, object]:
        self.prepare()
        if reviewer_must_fix_count != 0:
            raise ValueError("pre-live reviewer has unresolved Must Fix items")
        value = {
            "schema_version": "live-e2e.local-demo-pre-live-admission.v1",
            "implementation_commit": implementation_commit,
            "ci_workflow": ci_workflow,
            "ci_run_id": ci_run_id,
            "reviewer_must_fix_count": reviewer_must_fix_count,
            "recorded_at": recorded_at.isoformat(),
        }
        write_private_json(
            self.root / "pre-live-admission.json", value, create_once=True
        )
        self.verify()
        return value

    def require_pre_live_admission(self, implementation_commit: str) -> dict[str, object]:
        path = self.root / "pre-live-admission.json"
        if path.is_symlink() or not path.is_file():
            raise RuntimeError("LOCAL_DEMO pre-live admission is absent")
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict) or any(
            (
                value.get("implementation_commit") != implementation_commit,
                value.get("reviewer_must_fix_count") != 0,
                not isinstance(value.get("ci_run_id"), int),
            )
        ):
            raise RuntimeError("pre-live admission differs from exact implementation head")
        return value

    def _history(self) -> dict[str, object]:
        path = self.root / "attempt-history.json"
        if not path.exists():
            return {
                "schema_version": "live-e2e.local-demo-attempt-history.v1",
                "attempts": [],
            }
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict) or not isinstance(value.get("attempts"), list):
            raise ValueError("LOCAL_DEMO attempt history is malformed")
        return value

    def allocate_attempt(
        self, *, implementation_commit: str, runtime_config_sha256: str
    ) -> Path:
        self.prepare()
        history = self._history()
        attempts = history["attempts"]
        assert isinstance(attempts, list)
        if attempts:
            previous = attempts[-1]
            if not isinstance(previous, Mapping) or previous.get("verdict") == "STARTED":
                raise RuntimeError("previous LOCAL_DEMO attempt is not terminal")
            if previous.get("cleanup_verdict") != "CLEAN":
                raise RuntimeError("previous LOCAL_DEMO attempt cleanup is not clean")
            if (
                previous.get("implementation_commit") == implementation_commit
                and previous.get("runtime_config_sha256") == runtime_config_sha256
            ):
                raise RuntimeError("identical local-demo attempt is forbidden")
        index = len(attempts) + 1
        attempt = self.root / "attempts" / f"attempt-{index:04d}"
        ensure_private_directory(attempt)
        record = {
            "attempt_id": f"attempt-{index:04d}",
            "attempt_relative_path": attempt.relative_to(self.root).as_posix(),
            "implementation_commit": implementation_commit,
            "runtime_config_sha256": runtime_config_sha256,
            "verdict": "STARTED",
            "started_at": datetime.now(timezone.utc).isoformat(),
        }
        attempts.append(record)
        write_private_json(self.root / "attempt-history.json", history, create_once=False)
        write_private_json(attempt / "started.json", record, create_once=True)
        self.verify()
        return attempt

    def complete_attempt(self, attempt: Path, terminal: Mapping[str, object]) -> None:
        history = self._history()
        attempts = history["attempts"]
        assert isinstance(attempts, list)
        if not attempts or not isinstance(attempts[-1], dict):
            raise RuntimeError("LOCAL_DEMO active attempt is absent")
        active = attempts[-1]
        if active.get("attempt_relative_path") != attempt.relative_to(self.root).as_posix():
            raise RuntimeError("LOCAL_DEMO attempt completion differs")
        summary = {
            "verdict": terminal.get("verdict"),
            "fault_injections": terminal.get("fault_injections", 0),
            "model_calls": terminal.get("model_calls", 0),
            "forward_mutations": terminal.get("forward_mutations", 0),
            "rollback_mutations": terminal.get("rollback_mutations", 0),
            "cleanup_verdict": terminal.get("cleanup_verdict"),
            "completed_at": datetime.now(timezone.utc).isoformat(),
        }
        active.update(summary)
        write_private_json(attempt / "attempt-summary.json", summary, create_once=True)
        write_private_json(self.root / "attempt-history.json", history, create_once=False)
        self.verify()


def local_runtime_config_sha256(config: LocalDemoConfig) -> str:
    paths = (
        config.repository_root / LOCAL_DEMO_CONFIG_RELATIVE / "authority.json",
        config.repository_root / LOCAL_DEMO_CONFIG_RELATIVE / "lifecycle.json",
        config.repository_root / LOCAL_DEMO_CONFIG_RELATIVE / "reporting.json",
    )
    return canonical_sha256({path.name: file_sha256(path) for path in paths})


__all__ = [
    "LOCAL_DEMO_CONFIG_RELATIVE",
    "LocalDemoConfig",
    "LocalDemoPrivateRoot",
    "LocalDemoRuntimeAuthority",
    "load_local_demo_config",
    "local_runtime_config_sha256",
    "validate_provider_env",
]
