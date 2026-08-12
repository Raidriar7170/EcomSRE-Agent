from __future__ import annotations

from pathlib import Path

import pytest

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
