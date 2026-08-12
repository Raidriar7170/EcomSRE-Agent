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


def _non_success_terminal(verdict: str) -> dict[str, object]:
    terminal = _success_terminal()
    terminal["verdict"] = verdict
    terminal["cleanup_verdict"] = "CLEAN"
    terminal.update(
        {
            "provider_preflight_passed": True,
            "recovery_verification_passed": None,
            "rollback_exact_hash_verified": None,
        }
    )
    if verdict == "BLOCKED_PROVIDER_PREFLIGHT":
        terminal.update(
            {
                "provider_preflight_passed": False,
                "provider_calls": 0,
                "model_calls": 0,
                "fault_injections": 0,
                "forward_mutations": 0,
                "rollback_mutations": 0,
                "a0_context_builder_calls": 0,
                "fault_time_a0_context_artifact_exists": False,
                "fault_time_a0_context_sha256": None,
                "provider_live_context_sha256": None,
                "failed_stage": "PROVIDER_PREFLIGHT",
                "last_completed_stage": "WORKTREE_VERIFIED",
                "failure_code": "PROVIDER_PREFLIGHT_FAILED",
            }
        )
    elif verdict == "BLOCKED_FAULT_IMPACT_NOT_OBSERVED":
        terminal.update(
            {
                "provider_calls": 1,
                "model_calls": 0,
                "fault_injections": 1,
                "forward_mutations": 0,
                "rollback_mutations": 0,
                "fault_impact_passed": False,
                "a0_context_builder_calls": 0,
                "fault_time_a0_context_artifact_exists": False,
                "fault_time_a0_context_sha256": None,
                "provider_live_context_sha256": None,
                "failed_stage": "FAULT_IMPACT_GATE_EVALUATED",
                "last_completed_stage": "BASELINE_CONFIGURATION_VERIFIED",
                "failure_code": "FAULT_IMPACT_NOT_OBSERVED",
            }
        )
    elif verdict == "BLOCKED_LIVE_TELEMETRY_SOURCE_UNAVAILABLE":
        terminal.update(
            {
                "provider_calls": 1,
                "model_calls": 0,
                "fault_injections": 1,
                "forward_mutations": 0,
                "rollback_mutations": 0,
                "fault_impact_passed": True,
                "a0_context_builder_calls": 0,
                "fault_time_a0_context_artifact_exists": False,
                "fault_time_a0_context_sha256": None,
                "provider_live_context_sha256": None,
                "failed_stage": "LIVE_TELEMETRY_SOURCE_GATE_EVALUATED",
                "last_completed_stage": "FAULT_IMPACT_GATE_EVALUATED",
                "failure_code": "LIVE_TELEMETRY_SOURCE_GATE_NOT_PASSED",
            }
        )
    elif verdict == "BLOCKED_BOUNDED_MULTISERVICE_PROJECTION_UNAVAILABLE":
        terminal.update(
            {
                "provider_calls": 1,
                "model_calls": 0,
                "fault_injections": 1,
                "forward_mutations": 0,
                "rollback_mutations": 0,
                "fault_impact_passed": True,
                "a0_context_builder_calls": 1,
                "fault_time_a0_context_artifact_exists": False,
                "fault_time_a0_context_sha256": None,
                "provider_live_context_sha256": None,
                "failed_stage": "MULTISERVICE_PROJECTION_COMPLETED",
                "last_completed_stage": "MULTISERVICE_PROJECTION_STARTED",
                "failure_code": "MULTISERVICE_PROJECTION_FAILED",
            }
        )
    elif verdict == "LIVE_DIAGNOSIS_GATE_NOT_PASSED_NO_REMEDIATION":
        terminal.update(
            {
                "provider_calls": 2,
                "model_calls": 1,
                "fault_injections": 1,
                "forward_mutations": 0,
                "rollback_mutations": 0,
                "fault_impact_passed": True,
                "diagnosis_gate": False,
                "policy_verdict": None,
                "failed_stage": "DIAGNOSIS_GATE_EVALUATED",
                "last_completed_stage": "MULTISERVICE_PROJECTION_COMPLETED",
                "failure_code": "DIAGNOSIS_GATE_NOT_PASSED",
            }
        )
    elif verdict == "BLOCKED_POLICY_REJECTED":
        terminal.update(
            {
                "provider_calls": 2,
                "model_calls": 1,
                "fault_injections": 1,
                "forward_mutations": 0,
                "rollback_mutations": 0,
                "fault_impact_passed": True,
                "diagnosis_gate": True,
                "diagnosis_correct": True,
                "policy_verdict": "DENY",
                "failed_stage": "POLICY_GATE_EVALUATED",
                "last_completed_stage": "MULTISERVICE_PROJECTION_COMPLETED",
                "failure_code": "POLICY_REJECTED",
            }
        )
    elif verdict == "CONTROLLED_REMEDIATION_NOT_VERIFIED_ROLLBACK_COMPLETED":
        terminal.update(
            {
                "provider_calls": 2,
                "model_calls": 1,
                "fault_injections": 1,
                "forward_mutations": 1,
                "rollback_mutations": 1,
                "fault_impact_passed": True,
                "diagnosis_gate": True,
                "diagnosis_correct": True,
                "policy_verdict": "ALLOW",
                "recovery_verification_passed": False,
                "rollback_exact_hash_verified": True,
                "failed_stage": "REMEDIATION_VERIFICATION_EVALUATED",
                "last_completed_stage": "MULTISERVICE_PROJECTION_COMPLETED",
                "failure_code": "REMEDIATION_NOT_VERIFIED",
            }
        )
    elif verdict == "BLOCKED_ROLLBACK_FAILED_MANUAL_CLEANUP_REQUIRED":
        terminal.update(
            {
                "provider_calls": 2,
                "model_calls": 1,
                "fault_injections": 1,
                "forward_mutations": 1,
                "rollback_mutations": 1,
                "fault_impact_passed": True,
                "diagnosis_gate": True,
                "diagnosis_correct": True,
                "policy_verdict": "ALLOW",
                "recovery_verification_passed": False,
                "rollback_exact_hash_verified": False,
                "failed_stage": "ROLLBACK_VERIFICATION_EVALUATED",
                "last_completed_stage": "MULTISERVICE_PROJECTION_COMPLETED",
                "failure_code": "ROLLBACK_FAILED",
            }
        )
    elif verdict == "BLOCKED_CLEANUP_INCOMPLETE":
        terminal.update(
            {
                "cleanup_verdict": "BLOCKED",
                "cleanup": {
                    "baseline_restored": False,
                    "owned_containers": None,
                    "owned_networks": None,
                    "owned_volumes": None,
                    "non_owned_resources_changed": None,
                    "verdict": "BLOCKED",
                },
                "failed_stage": "CLEANUP_COMPLETED",
                "last_completed_stage": "COMPOSE_DOWN_RETURNED",
                "failure_code": "CLEANUP_FAILED",
                "cleanup_failure_code": "CLEANUP_FAILED",
            }
        )
    elif verdict == "BLOCKED_PUBLIC_RESULT_VERIFICATION":
        source = _non_success_terminal(
            "LIVE_DIAGNOSIS_GATE_NOT_PASSED_NO_REMEDIATION"
        )
        terminal = source
        terminal["public_result_source_verdict"] = source["verdict"]
        for field in ("failed_stage", "last_completed_stage", "failure_code"):
            terminal[f"public_result_source_{field}"] = source[field]
        terminal.update(
            {
                "verdict": verdict,
                "failed_stage": "PUBLIC_RESULT_VERIFICATION",
                "last_completed_stage": "CLEANUP_COMPLETED",
                "failure_code": "PUBLIC_RESULT_VERIFICATION_FAILED",
            }
        )
    else:  # pragma: no cover - test helper is intentionally closed
        raise ValueError(f"unsupported test terminal: {verdict}")
    return terminal


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


@pytest.mark.parametrize(
    ("verdict", "overrides"),
    (
        (
            "LIVE_DIAGNOSIS_GATE_NOT_PASSED_NO_REMEDIATION",
            {
                "provider_calls": 999,
                "fault_injections": 0,
                "forward_mutations": 1,
            },
        ),
        (
            "BLOCKED_FAULT_IMPACT_NOT_OBSERVED",
            {"model_calls": 1},
        ),
        (
            "CONTROLLED_REMEDIATION_NOT_VERIFIED_ROLLBACK_COMPLETED",
            {"forward_mutations": 0},
        ),
        (
            "BLOCKED_ROLLBACK_FAILED_MANUAL_CLEANUP_REQUIRED",
            {"rollback_mutations": 2},
        ),
        (
            "BLOCKED_CLEANUP_INCOMPLETE",
            {
                "cleanup_verdict": "CLEAN",
                "cleanup": _success_terminal()["cleanup"],
            },
        ),
        (
            "BLOCKED_CLEANUP_INCOMPLETE",
            {
                "cleanup": {
                    "baseline_restored": False,
                    "owned_containers": 1,
                    "owned_networks": 0,
                    "owned_volumes": 0,
                    "non_owned_resources_changed": False,
                    "verdict": "BLOCKED",
                },
                "provider_calls": 2,
                "model_calls": 0,
            },
        ),
        (
            "LIVE_DIAGNOSIS_GATE_NOT_PASSED_NO_REMEDIATION",
            {"provider_preflight_passed": False},
        ),
    ),
)
def test_impossible_non_success_stage_counts_are_rejected(
    verdict: str, overrides: dict[str, object]
) -> None:
    config = load_e2e_v6_config(CONFIG)
    terminal = _non_success_terminal(verdict)
    terminal.update(overrides)

    with pytest.raises(
        ValueError,
        match=(
            "stage counts|rollback|cleanup|post-preflight|recovery|Gate|Provider"
        ),
    ):
        build_expected_public_result(config, terminal)


@pytest.mark.parametrize(
    "verdict",
    (
        "BLOCKED_PROVIDER_PREFLIGHT",
        "BLOCKED_FAULT_IMPACT_NOT_OBSERVED",
        "BLOCKED_LIVE_TELEMETRY_SOURCE_UNAVAILABLE",
        "BLOCKED_BOUNDED_MULTISERVICE_PROJECTION_UNAVAILABLE",
        "LIVE_DIAGNOSIS_GATE_NOT_PASSED_NO_REMEDIATION",
        "BLOCKED_POLICY_REJECTED",
        "CONTROLLED_REMEDIATION_NOT_VERIFIED_ROLLBACK_COMPLETED",
        "BLOCKED_ROLLBACK_FAILED_MANUAL_CLEANUP_REQUIRED",
        "BLOCKED_CLEANUP_INCOMPLETE",
        "BLOCKED_PUBLIC_RESULT_VERIFICATION",
    ),
)
def test_consistent_non_success_stage_counts_are_accepted(
    verdict: str,
) -> None:
    config = load_e2e_v6_config(CONFIG)
    terminal = _non_success_terminal(verdict)

    public = build_expected_public_result(config, terminal)

    assert public["verdict"] == verdict


@pytest.mark.parametrize(
    ("verdict", "field", "forged_value"),
    (
        (
            "LIVE_DIAGNOSIS_GATE_NOT_PASSED_NO_REMEDIATION",
            "diagnosis_gate",
            True,
        ),
        ("BLOCKED_POLICY_REJECTED", "policy_verdict", "ALLOW"),
        (
            "CONTROLLED_REMEDIATION_NOT_VERIFIED_ROLLBACK_COMPLETED",
            "recovery_verification_passed",
            True,
        ),
        (
            "CONTROLLED_REMEDIATION_NOT_VERIFIED_ROLLBACK_COMPLETED",
            "rollback_mutations",
            0,
        ),
        (
            "LIVE_DIAGNOSIS_GATE_NOT_PASSED_NO_REMEDIATION",
            "failed_stage",
            "BASELINE_CONFIGURATION_VERIFIED",
        ),
        (
            "LIVE_DIAGNOSIS_GATE_NOT_PASSED_NO_REMEDIATION",
            "failure_code",
            "UNCLASSIFIED_RUNTIME_FAILURE",
        ),
    ),
)
def test_non_success_terminal_gate_and_failure_identity_contradictions_are_rejected(
    verdict: str,
    field: str,
    forged_value: object,
) -> None:
    config = load_e2e_v6_config(CONFIG)
    terminal = _non_success_terminal(verdict)
    terminal[field] = forged_value

    with pytest.raises(
        ValueError,
        match="Gate|rollback|stage|failure code|recovery",
    ):
        build_expected_public_result(config, terminal)


@pytest.mark.parametrize(
    ("field", "forged_value"),
    (
        ("baseline_restored", "false"),
        ("non_owned_resources_changed", {}),
        ("owned_containers", -1),
        ("owned_networks", "zero"),
        ("owned_volumes", {}),
    ),
)
def test_blocked_cleanup_rejects_invalid_field_types_and_ranges(
    field: str, forged_value: object
) -> None:
    config = load_e2e_v6_config(CONFIG)
    terminal = _non_success_terminal("BLOCKED_CLEANUP_INCOMPLETE")
    cleanup = terminal["cleanup"]
    assert isinstance(cleanup, dict)
    cleanup[field] = forged_value

    with pytest.raises(ValueError, match="blocked cleanup"):
        build_expected_public_result(config, terminal)


@pytest.mark.parametrize(
    "verdict",
    (
        "BLOCKED_PROVIDER_PREFLIGHT",
        "BLOCKED_FAULT_IMPACT_NOT_OBSERVED",
        "BLOCKED_LIVE_TELEMETRY_SOURCE_UNAVAILABLE",
    ),
)
@pytest.mark.parametrize(
    ("field", "forged_value"),
    (
        ("a0_context_builder_calls", 1),
        ("a0_context_builder_calls", False),
        ("fault_time_a0_context_artifact_exists", True),
        ("fault_time_a0_context_artifact_exists", 0),
        ("fault_time_a0_context_sha256", "a" * 64),
        ("provider_live_context_sha256", "a" * 64),
        ("rollback_exact_hash_verified", True),
        ("rollback_exact_hash_verified", 1),
        ("rollback_exact_hash_verified", "true"),
    ),
)
def test_early_terminal_rejects_impossible_context_and_rollback_proof(
    verdict: str,
    field: str,
    forged_value: object,
) -> None:
    config = load_e2e_v6_config(CONFIG)
    terminal = _non_success_terminal(verdict)
    terminal[field] = forged_value

    with pytest.raises(ValueError, match="context|rollback"):
        build_expected_public_result(config, terminal)


@pytest.mark.parametrize(
    ("field", "forged_value"),
    (
        ("a0_context_builder_calls", 1),
        ("a0_context_builder_calls", False),
        ("fault_time_a0_context_artifact_exists", True),
        ("fault_time_a0_context_artifact_exists", 0),
        ("fault_time_a0_context_sha256", "a" * 64),
        ("provider_live_context_sha256", "a" * 64),
        ("rollback_exact_hash_verified", True),
        ("rollback_exact_hash_verified", 1),
        ("rollback_exact_hash_verified", "true"),
    ),
)
def test_cleanup_wrapped_early_terminal_rejects_context_and_rollback_proof(
    field: str,
    forged_value: object,
) -> None:
    config = load_e2e_v6_config(CONFIG)
    terminal = _non_success_terminal("BLOCKED_CLEANUP_INCOMPLETE")
    terminal.update(
        {
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
            "failed_stage": "COMPOSE_START_RETURNED",
            "last_completed_stage": "COMPOSE_START_REQUESTED",
            "failure_code": "COMPOSE_UP_FAILED",
        }
    )
    terminal[field] = forged_value

    with pytest.raises(ValueError, match="context|rollback"):
        build_expected_public_result(config, terminal)


@pytest.mark.parametrize(
    ("field", "forged_value"),
    (
        ("failure_code", "CLEANUP_FAILED"),
        ("last_completed_stage", "WORKTREE_VERIFIED"),
        ("cleanup_failure_code", None),
        ("cleanup_failure_code", "COMPOSE_UP_FAILED"),
        ("cleanup_failure_code", 1),
    ),
)
def test_cleanup_wrapped_compose_terminal_rejects_root_identity_contradictions(
    field: str,
    forged_value: object,
) -> None:
    config = load_e2e_v6_config(CONFIG)
    terminal = _non_success_terminal("BLOCKED_CLEANUP_INCOMPLETE")
    terminal.update(
        {
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
            "failed_stage": "COMPOSE_START_RETURNED",
            "last_completed_stage": "COMPOSE_START_REQUESTED",
            "failure_code": "COMPOSE_UP_FAILED",
            "cleanup_failure_code": "CLEANUP_FAILED",
        }
    )
    terminal[field] = forged_value

    with pytest.raises(ValueError, match="cleanup|failure code|last completed"):
        build_expected_public_result(config, terminal)


def test_final_verifier_requires_a_sealed_private_terminal() -> None:
    config = load_e2e_v6_config(CONFIG)
    public = build_expected_public_result(config, _success_terminal())

    with pytest.raises(ValueError, match="sealed private terminal is required"):
        verify_public_result(config, public, None)
