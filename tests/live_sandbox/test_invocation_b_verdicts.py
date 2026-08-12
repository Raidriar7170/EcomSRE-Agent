from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import ecomsre_live_sandbox.e2e_v3 as shared_runtime
from ecomsre_live_sandbox.contracts import CleanupResult, ensure_private_directory
from ecomsre_live_sandbox.e2e_diagnostics import DiagnosticCommandIdentity
from ecomsre_live_sandbox.e2e_v3 import (
    _public_result_v3,
    verify_public_result as verify_v3,
)
from ecomsre_live_sandbox.e2e_v3_contracts import load_e2e_v3_config
from ecomsre_live_sandbox.e2e_v4 import (
    _public_result_v4,
    verify_public_result as verify_v4,
)
from ecomsre_live_sandbox.e2e_v4_contracts import load_e2e_v4_config
from ecomsre_live_sandbox.e2e_v5 import (
    _public_result_v5,
    verify_public_result as verify_v5,
)
from ecomsre_live_sandbox.e2e_v5_contracts import load_e2e_v5_config
from ecomsre_live_sandbox.e2e_v6_contracts import load_e2e_v6_config
from ecomsre_live_sandbox.invocation_b_assurance import (
    build_expected_public_result,
    verify_public_result as verify_v6,
)
from ecomsre_live_sandbox.e2e_diagnostics import DiagnosticFailureCode
from ecomsre_live_sandbox.invocation_b_verdicts import (
    get_invocation_b_verdict_policy,
)


class _CompatRoots:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.control = root / "control"
        self.invocation_b = root / "invocation-b"
        self.provider = root / "provider"
        self.runtime = root / "runtime"
        self.journal = root / "journal"
        self.telemetry = root / "telemetry"

    def bind_lifecycle(self, *_: object, **__: object) -> None:
        for path in (
            self.root,
            self.control,
            self.provider,
            self.runtime,
            self.journal,
            self.telemetry,
        ):
            ensure_private_directory(path)

    def verify(self) -> None:
        return None


class _CompatProvider:
    def __init__(self) -> None:
        self.calls = 0
        self.usage_known = True
        self.last_usage_tokens = 1
        self.last_request_sha256 = "0" * 64

    def diagnose(self, _: object) -> dict[str, str]:
        self.calls += 1
        return {"provider": "preflight"}


class _CompatController:
    def __init__(self, baseline: str, failure_kind: str) -> None:
        self.baseline = baseline
        self.failure_kind = failure_kind
        self.read_failures = 1 if failure_kind == "baseline-read" else 0
        self.mismatch = failure_kind == "baseline-mismatch"

    def read_current(self) -> object:
        if self.read_failures:
            self.read_failures -= 1
            raise RuntimeError("injected baseline read failure")
        return SimpleNamespace(
            document_sha256="f" * 64 if self.mismatch else self.baseline
        )

    def restore_baseline(self) -> object:
        self.mismatch = False
        return SimpleNamespace(document_sha256=self.baseline)


class _CompatEnvironment:
    def __init__(self, *, failure_kind: str, **kwargs: Any) -> None:
        self.failure_kind = failure_kind
        self.runner = kwargs["runner"]

    def verify_local_docker(self) -> dict[str, str]:
        return {
            "context": "desktop-linux",
            "endpoint": "unix://opaque",
            "daemon_id": "opaque",
        }

    def verify_upstream(self) -> None:
        return None

    def resolve(self) -> tuple[object, dict[str, object]]:
        endpoints = SimpleNamespace(
            prometheus="http://127.0.0.1:19090",
            opensearch="http://127.0.0.1:19200",
            jaeger="http://127.0.0.1:11686",
        )
        return SimpleNamespace(endpoints=endpoints, compose_sha256="a" * 64), {
            "services": {}
        }

    def verify_owned_resources(self, *, require_complete: bool) -> dict[str, int]:
        return {
            "container": 25 if require_complete else 0,
            "network": 1 if require_complete else 0,
            "volume": 3 if require_complete else 0,
        }

    def verify_ports_available(self) -> None:
        return None

    def snapshot_all_resources(self) -> object:
        return SimpleNamespace(
            containers=frozenset(), networks=frozenset(), volumes=frozenset()
        )

    def start(self) -> None:
        if self.runner.on_start is not None:
            self.runner.on_start(DiagnosticCommandIdentity.COMPOSE_UP)
        if self.failure_kind == "compose":
            raise RuntimeError("injected compose failure")
        if self.runner.on_return is not None:
            self.runner.on_return(DiagnosticCommandIdentity.COMPOSE_UP, 0, False)

    def wait_healthy(self, **_: object) -> dict[str, bool]:
        if self.failure_kind == "health":
            raise RuntimeError("injected health failure")
        return {f"service-{index}": True for index in range(25)}

    def cleanup(self, *, baseline_restored: bool) -> CleanupResult:
        return CleanupResult(
            baseline_restored=baseline_restored,
            owned_containers=0,
            owned_networks=0,
            owned_volumes=0,
            non_owned_resources_changed=False,
            verdict="CLEAN",
        )


def _compat_image_run() -> object:
    return SimpleNamespace(
        authority=SimpleNamespace(authority_sha256="1" * 64),
        verification=SimpleNamespace(verification_sha256="2" * 64),
        compose=SimpleNamespace(
            structure_sha256="3" * 64,
            instance_sha256="4" * 64,
        ),
    )


@pytest.mark.parametrize("version", ("v3", "v4", "v5", "v6"))
@pytest.mark.parametrize(
    ("failure_code", "terminal_suffix"),
    (
        (DiagnosticFailureCode.COMPOSE_UP_FAILED, "COMPOSE_UP_FAILED"),
        (DiagnosticFailureCode.SERVICE_HEALTH_TIMEOUT, "SERVICE_HEALTH_TIMEOUT"),
        (
            DiagnosticFailureCode.BASELINE_CONFIGURATION_UNAVAILABLE,
            "BASELINE_CONFIGURATION_UNAVAILABLE",
        ),
        (
            DiagnosticFailureCode.BASELINE_CONFIGURATION_MISMATCH,
            "BASELINE_CONFIGURATION_MISMATCH",
        ),
        (
            DiagnosticFailureCode.UNCLASSIFIED_RUNTIME_FAILURE,
            "UNCLASSIFIED_RUNTIME_FAILURE",
        ),
    ),
)
def test_version_policy_maps_post_preflight_failures_to_legal_terminal(
    version: str,
    failure_code: DiagnosticFailureCode,
    terminal_suffix: str,
) -> None:
    policy = get_invocation_b_verdict_policy(version)

    terminal = policy.terminal_for(failure_code)

    assert terminal == f"BLOCKED_E2E_{version.upper()}_{terminal_suffix}"
    assert terminal in policy.legal_terminals
    assert all(
        f"BLOCKED_E2E_{other.upper()}_" not in terminal
        for other in {"v3", "v4", "v5", "v6"} - {version}
    )


@pytest.mark.parametrize("version", ("v3", "v4", "v5", "v6"))
@pytest.mark.parametrize(
    ("failure_kind", "terminal_suffix"),
    (
        ("compose", "COMPOSE_UP_FAILED"),
        ("health", "SERVICE_HEALTH_TIMEOUT"),
        ("baseline-read", "BASELINE_CONFIGURATION_UNAVAILABLE"),
        ("baseline-mismatch", "BASELINE_CONFIGURATION_MISMATCH"),
        ("unclassified", "UNCLASSIFIED_RUNTIME_FAILURE"),
    ),
)
def test_shared_invocation_b_executes_version_compatible_failure_matrix(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    version: str,
    failure_kind: str,
    terminal_suffix: str,
) -> None:
    config_path = Path(f"config/live-fault-a0-controlled-remediation-e2e-{version}")
    loaders = {
        "v3": load_e2e_v3_config,
        "v4": load_e2e_v4_config,
        "v5": load_e2e_v5_config,
        "v6": load_e2e_v6_config,
    }
    config = loaders[version](config_path)
    roots = _CompatRoots(tmp_path / version / failure_kind)
    controller = _CompatController(
        config.sandbox.scenario.baseline_document_sha256,
        failure_kind,
    )
    monkeypatch.setattr(shared_runtime, "_require_canonical_success", lambda *_: None)
    monkeypatch.setattr(
        shared_runtime,
        "_load_exact_approval",
        lambda *_args, **_kwargs: (
            {
                "implementation_commit": "d" * 40,
                "compose_structure_sha256": "3" * 64,
                "image_authority_sha256": "1" * 64,
            },
            object(),
            object(),
        ),
    )
    monkeypatch.setattr(
        shared_runtime, "_require_exact_head_admission", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(
        shared_runtime, "_consume_live_run_budget", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(
        shared_runtime, "_complete_live_run_budget", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(
        shared_runtime,
        "_verify_scenario_lock_for_invocation_b",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        shared_runtime,
        "_verify_v3_images",
        lambda **_: _compat_image_run(),
    )

    def public_writer(
        current_config: object, terminal: dict[str, object]
    ) -> tuple[str, ...]:
        if version == "v3":
            public = _public_result_v3(current_config, terminal)  # type: ignore[arg-type]
            verify_v3(current_config, public)  # type: ignore[arg-type]
        elif version == "v4":
            public = _public_result_v4(current_config, terminal)  # type: ignore[arg-type]
            verify_v4(current_config, public)  # type: ignore[arg-type]
        elif version == "v5":
            public = _public_result_v5(current_config, terminal)  # type: ignore[arg-type]
            verify_v5(current_config, public)  # type: ignore[arg-type]
        else:
            public = build_expected_public_result(current_config, terminal)  # type: ignore[arg-type]
            verify_v6(current_config, public, terminal)  # type: ignore[arg-type]
        return ()

    def invocation_sleep(_: float) -> None:
        if failure_kind == "unclassified":
            raise RuntimeError("injected unclassified post-preflight failure")

    terminal = shared_runtime.run_invocation_b(
        config,  # type: ignore[arg-type]
        roots,  # type: ignore[arg-type]
        provider_factory=lambda _: _CompatProvider(),
        environment_factory=lambda **kwargs: _CompatEnvironment(
            failure_kind=failure_kind, **kwargs
        ),
        controller_factory=lambda *_args, **_kwargs: controller,
        worktree_verifier=lambda *_: "d" * 40,
        sleep=invocation_sleep,
        public_writer=public_writer,  # type: ignore[arg-type]
    )

    assert terminal["verdict"] == f"BLOCKED_E2E_{version.upper()}_{terminal_suffix}"
    assert terminal["verdict"] in get_invocation_b_verdict_policy(version).legal_terminals
    assert terminal["provider_preflight_passed"] is True
    assert terminal["provider_calls"] == 1
    assert terminal["fault_injections"] == 0
    assert terminal["model_calls"] == 0
    assert terminal["forward_mutations"] == 0
    assert terminal["rollback_mutations"] == 0
    assert terminal["failed_stage"] is not None
    assert terminal["last_completed_stage"] is not None


def test_version_policy_keeps_provider_preflight_and_cleanup_terminals_closed() -> None:
    for version in ("v3", "v4", "v5", "v6"):
        policy = get_invocation_b_verdict_policy(version)

        assert policy.provider_preflight_failed == "BLOCKED_PROVIDER_PREFLIGHT"
        assert policy.cleanup_incomplete == "BLOCKED_CLEANUP_INCOMPLETE"
        assert policy.provider_preflight_failed in policy.legal_terminals
        assert policy.cleanup_incomplete in policy.legal_terminals


def test_unknown_invocation_b_version_is_rejected() -> None:
    with pytest.raises(ValueError, match="unsupported Invocation B version"):
        get_invocation_b_verdict_policy("v7")


@pytest.mark.parametrize("version", ("v3", "v4", "v5", "v6"))
def test_each_version_public_verifier_accepts_its_own_executed_compose_terminal(
    version: str,
) -> None:
    config_path = Path(f"config/live-fault-a0-controlled-remediation-e2e-{version}")
    loaders = {
        "v3": load_e2e_v3_config,
        "v4": load_e2e_v4_config,
        "v5": load_e2e_v5_config,
        "v6": load_e2e_v6_config,
    }
    config = loaders[version](config_path)
    terminal = {
        "version": config.authority.version,
        "verdict": get_invocation_b_verdict_policy(version).compose_up_failed,
        "implementation_commit": "d" * 40,
        "result_head": "d" * 40,
        "provider_preflight_passed": True,
        "provider_calls": 1,
        "model_calls": 0,
        "fault_injections": 0,
        "forward_mutations": 0,
        "rollback_mutations": 0,
        "a0_context_builder_calls": 0,
        "fault_time_a0_context_artifact_exists": False,
        "fault_time_a0_context_sha256": None,
        "provider_live_context_sha256": None,
        "rollback_exact_hash_verified": None,
        "approval_mode": "HUMAN_PREAUTHORIZED_FROZEN_REMEDIATION_RUNBOOK",
        "approval_valid": True,
        "claim_boundary": list(config.reporting.claim_boundary),
        "failed_stage": "COMPOSE_START_RETURNED",
        "last_completed_stage": "COMPOSE_START_REQUESTED",
        "failure_code": "COMPOSE_UP_FAILED",
        "cleanup": {
            "baseline_restored": True,
            "owned_containers": 0,
            "owned_networks": 0,
            "owned_volumes": 0,
            "non_owned_resources_changed": False,
            "verdict": "CLEAN",
        },
    }
    if version == "v3":
        public = _public_result_v3(config, terminal)  # type: ignore[arg-type]
        verify_v3(config, public)  # type: ignore[arg-type]
    elif version == "v4":
        public = _public_result_v4(config, terminal)  # type: ignore[arg-type]
        verify_v4(config, public)  # type: ignore[arg-type]
    elif version == "v5":
        public = _public_result_v5(config, terminal)  # type: ignore[arg-type]
        verify_v5(config, public)  # type: ignore[arg-type]
    else:
        public = build_expected_public_result(config, terminal)  # type: ignore[arg-type]
        verify_v6(config, public, terminal)  # type: ignore[arg-type]
