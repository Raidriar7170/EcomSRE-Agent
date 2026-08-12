from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from ecomsre_live_sandbox.contracts import canonical_sha256
from ecomsre_live_sandbox.e2e_v6_contracts import load_e2e_v6_config
from ecomsre_live_sandbox.invocation_b_assurance import (
    build_expected_public_result,
    verify_public_result,
)


CONFIG = Path("config/live-fault-a0-controlled-remediation-e2e-v6")


def _success_terminal() -> dict[str, object]:
    context_sha = "a" * 64
    return {
        "schema_version": "live-e2e.invocation-b-terminal.v6",
        "version": "live-fault-a0-controlled-remediation-e2e-v6",
        "verdict": (
            "LIVE_FAULT_A0_CONTROLLED_REMEDIATION_E2E_V6_"
            "PASSED_READY_FOR_REVIEW"
        ),
        "implementation_commit": "d" * 40,
        "result_head": "d" * 40,
        "source_availability": {
            "METRICS": "AVAILABLE",
            "LOGS": "AVAILABLE",
            "TRACES": "AVAILABLE",
        },
        "source_counts": {"METRICS": 5, "LOGS": 28, "TRACES": 14},
        "invalid_refs": 0,
        "all_refs_resolve": True,
        "projection_broad_counts": {"metrics": 18, "logs": 28, "traces": 14},
        "projection_diagnostic_counts": {"metrics": 3, "logs": 3, "traces": 0},
        "empty_model_streams": ["TRACES"],
        "projection_reason_codes": ["NO_DIAGNOSTIC_TRACES"],
        "visible_service_count": 3,
        "a0_context_builder_calls": 1,
        "fault_time_a0_context_artifact_exists": True,
        "fault_time_a0_context_sha256": context_sha,
        "provider_live_context_sha256": context_sha,
        "fault_injections": 1,
        "provider_calls": 2,
        "model_calls": 1,
        "forward_mutations": 1,
        "rollback_mutations": 0,
        "provider_preflight_passed": True,
        "fault_impact_passed": True,
        "diagnosis_gate": True,
        "diagnosis_correct": True,
        "plan_action": "RESTORE_FROZEN_SERVICE_CONFIGURATION",
        "approval_mode": "HUMAN_PREAUTHORIZED_FROZEN_REMEDIATION_RUNBOOK",
        "approval_valid": True,
        "claim_boundary": [
            "LIVE_LOCAL_SANDBOX_DEMO",
            "ONE_PREREGISTERED_SCENARIO",
            "HUMAN_PREAUTHORIZED_FROZEN_REMEDIATION_RUNBOOK",
            "ONE_STRONG_SINGLE_DIAGNOSIS",
            "ONE_ALLOWLISTED_MUTATION",
            "INDEPENDENT_RECOVERY_VERIFICATION",
            "NOT_PRODUCTION",
            "NOT_AUTONOMOUS_PRODUCTION_REMEDIATION",
            "NOT_EXTERNAL_BENCHMARK",
            "NOT_MULTI_AGENT_SUPERIORITY_CLAIM",
        ],
        "policy_verdict": "ALLOW",
        "recovery_verification_passed": True,
        "cleanup": {
            "baseline_restored": True,
            "owned_containers": 0,
            "owned_networks": 0,
            "owned_volumes": 0,
            "non_owned_resources_changed": False,
            "verdict": "CLEAN",
        },
    }


def _rehash(value: dict[str, object]) -> dict[str, object]:
    core = dict(value)
    core.pop("semantic_sha256", None)
    value["semantic_sha256"] = canonical_sha256(core)
    return value


def test_public_result_is_rebuilt_exactly_from_sealed_terminal() -> None:
    config = load_e2e_v6_config(CONFIG)
    terminal = _success_terminal()

    public = build_expected_public_result(config, terminal)

    verify_public_result(config, public, terminal)
    assert public["semantic_sha256"] == canonical_sha256(
        {key: value for key, value in public.items() if key != "semantic_sha256"}
    )


def test_success_terminal_must_bind_the_frozen_claim_boundary() -> None:
    config = load_e2e_v6_config(CONFIG)
    terminal = _success_terminal()
    terminal.pop("claim_boundary")

    with pytest.raises(ValueError, match="claim boundary"):
        build_expected_public_result(config, terminal)


@pytest.mark.parametrize(
    ("path", "forged_value"),
    (
        (("cleanup", "baseline_restored"), False),
        (("cleanup", "owned_containers"), 1),
        (("fault_injections",), 0),
        (("provider_calls",), 1),
        (("model_calls",), 0),
        (("forward_mutations",), 0),
        (("all_refs_resolve",), False),
        (("invalid_refs",), 1),
        (("projection_diagnostic_counts", "metrics"), 0),
        (("a0_context_builder_calls",), 2),
        (("provider_live_context_sha256",), "b" * 64),
        (("approval_mode",), "AUTO"),
        (("claim_boundary",), ["NOT_THE_FROZEN_BOUNDARY"]),
    ),
)
def test_rehashed_public_contradiction_cannot_override_sealed_truth(
    path: tuple[str, ...],
    forged_value: object,
) -> None:
    config = load_e2e_v6_config(CONFIG)
    terminal = _success_terminal()
    forged = deepcopy(build_expected_public_result(config, terminal))
    target: dict[str, object] = forged
    for key in path[:-1]:
        target = target[key]  # type: ignore[assignment]
    target[path[-1]] = forged_value
    _rehash(forged)

    with pytest.raises(ValueError, match="sealed private terminal"):
        verify_public_result(config, forged, terminal)


def test_impossible_success_terminal_is_rejected_before_projection() -> None:
    config = load_e2e_v6_config(CONFIG)
    terminal = _success_terminal()
    terminal["projection_diagnostic_counts"] = {
        "metrics": 3,
        "logs": 0,
        "traces": 0,
    }

    with pytest.raises(ValueError, match="diagnostic Logs or Traces"):
        build_expected_public_result(config, terminal)


def test_final_verifier_requires_a_sealed_private_terminal() -> None:
    config = load_e2e_v6_config(CONFIG)
    public = build_expected_public_result(config, _success_terminal())

    with pytest.raises(ValueError, match="sealed private terminal is required"):
        verify_public_result(config, public, None)
