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
            "LIVE_FAULT_A0_CONTROLLED_REMEDIATION_E2E_V6_PASSED_READY_FOR_REVIEW"
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
        "cleanup_verdict": "CLEAN",
        "cleanup_failure_code": None,
    }


def _rehash(value: dict[str, object]) -> dict[str, object]:
    core = dict(value)
    core.pop("semantic_sha256", None)
    value["semantic_sha256"] = canonical_sha256(core)
    return value


def _set_complete_fault_evidence(
    terminal: dict[str, object], *, context_completed: bool
) -> None:
    success = _success_terminal()
    for field in (
        "source_availability",
        "source_counts",
        "invalid_refs",
        "all_refs_resolve",
        "projection_broad_counts",
        "projection_diagnostic_counts",
        "empty_model_streams",
        "projection_reason_codes",
    ):
        terminal[field] = deepcopy(success[field])
    terminal["visible_service_count"] = (
        success["visible_service_count"] if context_completed else None
    )
    if context_completed:
        for field in (
            "a0_context_builder_calls",
            "fault_time_a0_context_artifact_exists",
            "fault_time_a0_context_sha256",
            "provider_live_context_sha256",
        ):
            terminal[field] = success[field]


def _set_unavailable_source_evidence(terminal: dict[str, object]) -> None:
    terminal.update(
        {
            "source_availability": {
                "METRICS": "AVAILABLE",
                "LOGS": "EMPTY",
                "TRACES": "AVAILABLE",
            },
            "source_counts": {"METRICS": 5, "LOGS": 0, "TRACES": 14},
            "invalid_refs": 0,
            "all_refs_resolve": True,
        }
    )


def _set_pre_fault_truth(terminal: dict[str, object]) -> None:
    for field in (
        "fault_impact_passed",
        "diagnosis_gate",
        "diagnosis_correct",
        "plan_action",
        "policy_verdict",
        "recovery_verification_passed",
        "rollback_exact_hash_verified",
        "fault_time_a0_context_artifact_exists",
        "provider_live_context_sha256",
    ):
        terminal.pop(field, None)
    terminal.update(
        {
            "source_availability": {},
            "source_counts": {},
            "invalid_refs": None,
            "all_refs_resolve": None,
            "projection_broad_counts": {},
            "projection_diagnostic_counts": {},
            "empty_model_streams": [],
            "projection_reason_codes": [],
            "visible_service_count": None,
            "a0_context_builder_calls": 0,
            "fault_time_a0_context_sha256": None,
        }
    )


def _non_success_terminal(verdict: str) -> dict[str, object]:
    terminal = _success_terminal()
    terminal["verdict"] = verdict
    terminal["cleanup_verdict"] = "CLEAN"
    terminal.update(
        {
            "provider_preflight_passed": True,
            "fault_impact_passed": None,
            "diagnosis_gate": None,
            "diagnosis_correct": None,
            "plan_action": None,
            "policy_verdict": None,
            "recovery_verification_passed": None,
            "rollback_exact_hash_verified": None,
            "source_availability": {},
            "source_counts": {},
            "invalid_refs": None,
            "all_refs_resolve": None,
            "projection_broad_counts": {},
            "projection_diagnostic_counts": {},
            "empty_model_streams": [],
            "projection_reason_codes": [],
            "visible_service_count": None,
            "a0_context_builder_calls": 0,
            "fault_time_a0_context_artifact_exists": False,
            "fault_time_a0_context_sha256": None,
            "provider_live_context_sha256": None,
        }
    )
    _set_pre_fault_truth(terminal)
    if verdict == "BLOCKED_PROVIDER_PREFLIGHT":
        terminal.pop("provider_preflight_passed", None)
        terminal.update(
            {
                "cleanup_verdict": "NOT_REQUIRED",
                "cleanup": {
                    "baseline_restored": True,
                    "owned_containers": 0,
                    "owned_networks": 0,
                    "owned_volumes": 0,
                    "non_owned_resources_changed": False,
                    "verdict": "NOT_REQUIRED",
                },
                "provider_calls": 0,
                "model_calls": 0,
                "fault_injections": 0,
                "forward_mutations": 0,
                "rollback_mutations": 0,
                "a0_context_builder_calls": 0,
                "fault_time_a0_context_sha256": None,
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
                "a0_context_builder_calls": 0,
                "fault_time_a0_context_sha256": None,
                "failed_stage": "FAULT_IMPACT_GATE_EVALUATED",
                "last_completed_stage": "BASELINE_CONFIGURATION_VERIFIED",
                "failure_code": "FAULT_IMPACT_NOT_OBSERVED",
            }
        )
    elif verdict == "BLOCKED_LIVE_TELEMETRY_SOURCE_UNAVAILABLE":
        _set_unavailable_source_evidence(terminal)
        terminal.update(
            {
                "provider_calls": 1,
                "model_calls": 0,
                "fault_injections": 1,
                "forward_mutations": 0,
                "rollback_mutations": 0,
                "fault_impact_passed": True,
                "a0_context_builder_calls": 0,
                "fault_time_a0_context_sha256": None,
                "failed_stage": "LIVE_TELEMETRY_SOURCE_GATE_EVALUATED",
                "last_completed_stage": "FAULT_IMPACT_GATE_EVALUATED",
                "failure_code": "LIVE_TELEMETRY_SOURCE_GATE_NOT_PASSED",
            }
        )
    elif verdict == "BLOCKED_BOUNDED_MULTISERVICE_PROJECTION_UNAVAILABLE":
        _set_complete_fault_evidence(terminal, context_completed=False)
        terminal.update(
            {
                "projection_diagnostic_counts": {
                    "metrics": 3,
                    "logs": 0,
                    "traces": 0,
                },
                "empty_model_streams": ["LOGS", "TRACES"],
                "projection_reason_codes": [
                    "NO_DIAGNOSTIC_LOGS",
                    "NO_DIAGNOSTIC_TRACES",
                    "NO_LOG_OR_TRACE_DIAGNOSTIC_EVIDENCE",
                ],
                "provider_calls": 1,
                "model_calls": 0,
                "fault_injections": 1,
                "forward_mutations": 0,
                "rollback_mutations": 0,
                "fault_impact_passed": True,
                "a0_context_builder_calls": 1,
                "fault_time_a0_context_sha256": None,
                "failed_stage": "MULTISERVICE_PROJECTION_COMPLETED",
                "last_completed_stage": "MULTISERVICE_PROJECTION_STARTED",
                "failure_code": "MULTISERVICE_PROJECTION_FAILED",
            }
        )
    elif verdict == "LIVE_DIAGNOSIS_GATE_NOT_PASSED_NO_REMEDIATION":
        _set_complete_fault_evidence(terminal, context_completed=True)
        terminal.update(
            {
                "provider_calls": 2,
                "model_calls": 1,
                "fault_injections": 1,
                "forward_mutations": 0,
                "rollback_mutations": 0,
                "fault_impact_passed": True,
                "diagnosis_gate": False,
                "diagnosis_correct": False,
                "policy_verdict": None,
                "failed_stage": "DIAGNOSIS_GATE_EVALUATED",
                "last_completed_stage": "MULTISERVICE_PROJECTION_COMPLETED",
                "failure_code": "DIAGNOSIS_GATE_NOT_PASSED",
            }
        )
    elif verdict == "BLOCKED_POLICY_REJECTED":
        _set_complete_fault_evidence(terminal, context_completed=True)
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
                "plan_action": "RESTORE_FROZEN_SERVICE_CONFIGURATION",
                "policy_verdict": "DENY",
                "failed_stage": "POLICY_GATE_EVALUATED",
                "last_completed_stage": "MULTISERVICE_PROJECTION_COMPLETED",
                "failure_code": "POLICY_REJECTED",
            }
        )
    elif verdict == "CONTROLLED_REMEDIATION_NOT_VERIFIED_ROLLBACK_COMPLETED":
        _set_complete_fault_evidence(terminal, context_completed=True)
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
                "plan_action": "RESTORE_FROZEN_SERVICE_CONFIGURATION",
                "policy_verdict": "ALLOW",
                "recovery_verification_passed": False,
                "rollback_exact_hash_verified": True,
                "failed_stage": "REMEDIATION_VERIFICATION_EVALUATED",
                "last_completed_stage": "MULTISERVICE_PROJECTION_COMPLETED",
                "failure_code": "REMEDIATION_NOT_VERIFIED",
            }
        )
    elif verdict == "BLOCKED_ROLLBACK_FAILED_MANUAL_CLEANUP_REQUIRED":
        _set_complete_fault_evidence(terminal, context_completed=True)
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
                "plan_action": "RESTORE_FROZEN_SERVICE_CONFIGURATION",
                "policy_verdict": "ALLOW",
                "recovery_verification_passed": False,
                "rollback_exact_hash_verified": False,
                "failed_stage": "ROLLBACK_VERIFICATION_EVALUATED",
                "last_completed_stage": "MULTISERVICE_PROJECTION_COMPLETED",
                "failure_code": "ROLLBACK_FAILED",
            }
        )
    elif verdict == "BLOCKED_CLEANUP_INCOMPLETE":
        _set_complete_fault_evidence(terminal, context_completed=True)
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
                "fault_impact_passed": True,
                "diagnosis_gate": True,
                "diagnosis_correct": True,
                "plan_action": "RESTORE_FROZEN_SERVICE_CONFIGURATION",
                "policy_verdict": "ALLOW",
                "recovery_verification_passed": True,
            }
        )
    elif verdict == "BLOCKED_PUBLIC_RESULT_VERIFICATION":
        source = _non_success_terminal("LIVE_DIAGNOSIS_GATE_NOT_PASSED_NO_REMEDIATION")
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


@pytest.mark.parametrize(
    "factory",
    (
        _success_terminal,
        lambda: _non_success_terminal("BLOCKED_PROVIDER_PREFLIGHT"),
        lambda: _non_success_terminal("LIVE_DIAGNOSIS_GATE_NOT_PASSED_NO_REMEDIATION"),
        lambda: _non_success_terminal("BLOCKED_PUBLIC_RESULT_VERIFICATION"),
    ),
)
@pytest.mark.parametrize(
    "forged_schema",
    (None, "bogus", "live-e2e.invocation-b-terminal.v5", 1),
)
def test_sealed_terminal_requires_exact_v6_schema(
    factory: object,
    forged_schema: object,
) -> None:
    config = load_e2e_v6_config(CONFIG)
    assert callable(factory)
    terminal = factory()
    assert isinstance(terminal, dict)
    if forged_schema is None:
        terminal.pop("schema_version")
    else:
        terminal["schema_version"] = forged_schema

    with pytest.raises(ValueError, match="schema differs from v6"):
        build_expected_public_result(config, terminal)


@pytest.mark.parametrize(
    "field",
    ("failed_stage", "last_completed_stage", "failure_code"),
)
def test_success_terminal_rejects_failure_identity(field: str) -> None:
    config = load_e2e_v6_config(CONFIG)
    terminal = _success_terminal()
    terminal[field] = {
        "failed_stage": "ROLLBACK_VERIFICATION_EVALUATED",
        "last_completed_stage": "MULTISERVICE_PROJECTION_COMPLETED",
        "failure_code": "ROLLBACK_FAILED",
    }[field]

    with pytest.raises(
        ValueError, match="success terminal contains a failure identity"
    ):
        build_expected_public_result(config, terminal)


@pytest.mark.parametrize(
    ("field", "forged_value"),
    (
        ("cleanup_verdict", None),
        ("cleanup_failure_code", "CLEANUP_FAILED"),
        ("cleanup_failure_code", "BOGUS"),
        ("cleanup_failure_code", False),
        ("cleanup_failure_code", 0),
    ),
)
@pytest.mark.parametrize(
    "factory",
    (
        _success_terminal,
        lambda: _non_success_terminal("BLOCKED_PROVIDER_PREFLIGHT"),
        lambda: _versioned_unclassified_terminal(),
        lambda: _non_success_terminal("LIVE_DIAGNOSIS_GATE_NOT_PASSED_NO_REMEDIATION"),
        lambda: _non_success_terminal("BLOCKED_PUBLIC_RESULT_VERIFICATION"),
    ),
)
def test_sealed_terminal_requires_exact_cleanup_control_fields(
    factory: object,
    field: str,
    forged_value: object,
) -> None:
    config = load_e2e_v6_config(CONFIG)
    assert callable(factory)
    terminal = factory()
    assert isinstance(terminal, dict)
    if forged_value is None:
        terminal.pop(field)
    else:
        terminal[field] = forged_value

    with pytest.raises(ValueError, match="cleanup"):
        build_expected_public_result(config, terminal)


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
    ("path", "forged_value"),
    (
        (("invalid_refs",), False),
        (("a0_context_builder_calls",), True),
        (("fault_injections",), True),
        (("model_calls",), True),
        (("forward_mutations",), True),
        (("rollback_mutations",), False),
        (("provider_preflight_passed",), 1),
        (("fault_impact_passed",), 1),
        (("diagnosis_gate",), 1),
        (("diagnosis_correct",), 1),
        (("approval_valid",), 1),
        (("recovery_verification_passed",), 1),
        (("cleanup", "owned_containers"), False),
        (("rollback_exact_hash_verified",), True),
        (("rollback_exact_hash_verified",), 1),
        (("rollback_exact_hash_verified",), "true"),
    ),
)
def test_success_terminal_rejects_bool_int_schema_aliases(
    path: tuple[str, ...],
    forged_value: object,
) -> None:
    config = load_e2e_v6_config(CONFIG)
    terminal = _success_terminal()
    target = terminal
    for key in path[:-1]:
        nested = target[key]
        assert isinstance(nested, dict)
        target = nested
    target[path[-1]] = forged_value

    with pytest.raises(ValueError):
        build_expected_public_result(config, terminal)


@pytest.mark.parametrize(
    ("verdict", "field", "forged_value"),
    (
        (
            "LIVE_DIAGNOSIS_GATE_NOT_PASSED_NO_REMEDIATION",
            "a0_context_builder_calls",
            True,
        ),
        ("BLOCKED_POLICY_REJECTED", "a0_context_builder_calls", True),
        (
            "BLOCKED_BOUNDED_MULTISERVICE_PROJECTION_UNAVAILABLE",
            "a0_context_builder_calls",
            True,
        ),
        (
            "BLOCKED_BOUNDED_MULTISERVICE_PROJECTION_UNAVAILABLE",
            "fault_time_a0_context_artifact_exists",
            0,
        ),
    ),
)
def test_non_success_context_rejects_bool_int_schema_aliases(
    verdict: str,
    field: str,
    forged_value: object,
) -> None:
    config = load_e2e_v6_config(CONFIG)
    terminal = _non_success_terminal(verdict)
    terminal[field] = forged_value

    with pytest.raises(ValueError, match="context|projection"):
        build_expected_public_result(config, terminal)


@pytest.mark.parametrize(
    ("verdict", "field", "forged_value"),
    (
        ("BLOCKED_PROVIDER_PREFLIGHT", "provider_preflight_passed", 1),
        ("BLOCKED_PROVIDER_PREFLIGHT", "provider_preflight_passed", "false"),
        ("BLOCKED_FAULT_IMPACT_NOT_OBSERVED", "fault_impact_passed", 1),
        (
            "BLOCKED_FAULT_IMPACT_NOT_OBSERVED",
            "fault_impact_passed",
            "false",
        ),
    ),
)
def test_non_success_gate_flags_require_exact_booleans(
    verdict: str,
    field: str,
    forged_value: object,
) -> None:
    config = load_e2e_v6_config(CONFIG)
    terminal = _non_success_terminal(verdict)
    terminal[field] = forged_value

    with pytest.raises(ValueError, match="boolean|Provider|fault"):
        build_expected_public_result(config, terminal)


@pytest.mark.parametrize(
    "verdict",
    (
        "BLOCKED_PROVIDER_PREFLIGHT",
        "BLOCKED_LIVE_TELEMETRY_SOURCE_UNAVAILABLE",
        "BLOCKED_BOUNDED_MULTISERVICE_PROJECTION_UNAVAILABLE",
        "LIVE_DIAGNOSIS_GATE_NOT_PASSED_NO_REMEDIATION",
        "BLOCKED_POLICY_REJECTED",
    ),
)
@pytest.mark.parametrize(
    "path",
    (
        ("invalid_refs",),
        ("visible_service_count",),
        ("source_counts", "METRICS"),
        ("projection_broad_counts", "metrics"),
        ("projection_diagnostic_counts", "metrics"),
    ),
)
def test_non_success_public_counts_reject_boolean_integer_aliases(
    verdict: str,
    path: tuple[str, ...],
) -> None:
    config = load_e2e_v6_config(CONFIG)
    terminal = _non_success_terminal(verdict)
    target = terminal
    for key in path[:-1]:
        nested = target[key]
        assert isinstance(nested, dict)
        target = nested
    target[path[-1]] = True

    with pytest.raises(ValueError, match="integer|count map|Evidence"):
        build_expected_public_result(config, terminal)


def test_success_terminal_rejects_unknown_projection_reason_code() -> None:
    config = load_e2e_v6_config(CONFIG)
    terminal = _success_terminal()
    reasons = terminal["projection_reason_codes"]
    assert isinstance(reasons, list)
    reasons.append("BOGUS_REASON")

    with pytest.raises(ValueError, match="reason codes"):
        build_expected_public_result(config, terminal)


@pytest.mark.parametrize(
    ("verdict", "path", "forged_value"),
    (
        (
            "BLOCKED_LIVE_TELEMETRY_SOURCE_UNAVAILABLE",
            ("source_availability", "METRICS"),
            "BOGUS",
        ),
        (
            "BLOCKED_LIVE_TELEMETRY_SOURCE_UNAVAILABLE",
            ("source_availability", "NOT_A_SOURCE"),
            "AVAILABLE",
        ),
        (
            "BLOCKED_BOUNDED_MULTISERVICE_PROJECTION_UNAVAILABLE",
            ("source_counts", "NOT_A_SOURCE"),
            1,
        ),
        (
            "LIVE_DIAGNOSIS_GATE_NOT_PASSED_NO_REMEDIATION",
            ("projection_broad_counts", "NOT_A_STREAM"),
            1,
        ),
        (
            "LIVE_DIAGNOSIS_GATE_NOT_PASSED_NO_REMEDIATION",
            ("projection_diagnostic_counts", "NOT_A_STREAM"),
            1,
        ),
    ),
)
def test_non_success_source_and_projection_maps_use_closed_schemas(
    verdict: str,
    path: tuple[str, ...],
    forged_value: object,
) -> None:
    config = load_e2e_v6_config(CONFIG)
    terminal = _non_success_terminal(verdict)
    target = terminal
    for key in path[:-1]:
        nested = target[key]
        assert isinstance(nested, dict)
        target = nested
    target[path[-1]] = forged_value

    with pytest.raises(ValueError, match="count map|availability|sources"):
        build_expected_public_result(config, terminal)


@pytest.mark.parametrize(
    ("verdict", "field", "forged_value"),
    (
        (
            "BLOCKED_LIVE_TELEMETRY_SOURCE_UNAVAILABLE",
            "empty_model_streams",
            ["BOGUS_STREAM"],
        ),
        (
            "BLOCKED_BOUNDED_MULTISERVICE_PROJECTION_UNAVAILABLE",
            "projection_reason_codes",
            ["BOGUS_REASON"],
        ),
        (
            "LIVE_DIAGNOSIS_GATE_NOT_PASSED_NO_REMEDIATION",
            "empty_model_streams",
            ["BOGUS_STREAM"],
        ),
        (
            "LIVE_DIAGNOSIS_GATE_NOT_PASSED_NO_REMEDIATION",
            "projection_reason_codes",
            ["NO_DIAGNOSTIC_TRACES", "BOGUS_REASON"],
        ),
    ),
)
def test_non_success_projection_lists_use_closed_schemas(
    verdict: str,
    field: str,
    forged_value: object,
) -> None:
    config = load_e2e_v6_config(CONFIG)
    terminal = _non_success_terminal(verdict)
    terminal[field] = forged_value

    with pytest.raises(ValueError, match="closed set"):
        build_expected_public_result(config, terminal)


@pytest.mark.parametrize(
    ("field", "forged_value"),
    (
        ("fault_impact_passed", True),
        ("diagnosis_gate", True),
        ("diagnosis_correct", True),
        (
            "plan_action",
            "RESTORE_FROZEN_SERVICE_CONFIGURATION",
        ),
        ("policy_verdict", "ALLOW"),
        ("recovery_verification_passed", True),
    ),
)
def test_provider_preflight_terminal_rejects_future_gate_truth(
    field: str,
    forged_value: object,
) -> None:
    config = load_e2e_v6_config(CONFIG)
    terminal = _non_success_terminal("BLOCKED_PROVIDER_PREFLIGHT")
    terminal[field] = forged_value

    with pytest.raises(ValueError, match="future Gate|Provider"):
        build_expected_public_result(config, terminal)


@pytest.mark.parametrize(
    ("verdict", "field", "forged_value"),
    (
        ("BLOCKED_FAULT_IMPACT_NOT_OBSERVED", "diagnosis_gate", True),
        ("BLOCKED_FAULT_IMPACT_NOT_OBSERVED", "diagnosis_correct", True),
        (
            "BLOCKED_FAULT_IMPACT_NOT_OBSERVED",
            "plan_action",
            "RESTORE_FROZEN_SERVICE_CONFIGURATION",
        ),
        ("BLOCKED_FAULT_IMPACT_NOT_OBSERVED", "policy_verdict", "ALLOW"),
        (
            "BLOCKED_LIVE_TELEMETRY_SOURCE_UNAVAILABLE",
            "diagnosis_gate",
            True,
        ),
        (
            "BLOCKED_BOUNDED_MULTISERVICE_PROJECTION_UNAVAILABLE",
            "policy_verdict",
            "ALLOW",
        ),
    ),
)
def test_pre_diagnosis_terminal_rejects_future_gate_truth(
    verdict: str,
    field: str,
    forged_value: object,
) -> None:
    config = load_e2e_v6_config(CONFIG)
    terminal = _non_success_terminal(verdict)
    terminal[field] = forged_value

    with pytest.raises(ValueError, match="future Gate"):
        build_expected_public_result(config, terminal)


@pytest.mark.parametrize(
    ("field", "forged_value"),
    (
        ("fault_impact_passed", True),
        ("diagnosis_gate", True),
        ("diagnosis_correct", True),
        ("plan_action", "RESTORE_FROZEN_SERVICE_CONFIGURATION"),
        ("policy_verdict", "ALLOW"),
        ("recovery_verification_passed", True),
    ),
)
def test_versioned_compose_terminal_rejects_future_gate_truth(
    field: str,
    forged_value: object,
) -> None:
    config = load_e2e_v6_config(CONFIG)
    terminal = _versioned_compose_terminal()
    terminal[field] = forged_value

    with pytest.raises(ValueError, match="future Gate"):
        build_expected_public_result(config, terminal)


@pytest.mark.parametrize(
    ("field", "forged_value"),
    (
        ("fault_impact_passed", True),
        ("diagnosis_gate", True),
        ("diagnosis_correct", True),
        ("plan_action", "RESTORE_FROZEN_SERVICE_CONFIGURATION"),
        ("policy_verdict", "ALLOW"),
    ),
)
def test_cleanup_wrapped_compose_terminal_rejects_future_gate_truth(
    field: str,
    forged_value: object,
) -> None:
    config = load_e2e_v6_config(CONFIG)
    terminal = _cleanup_wrapped_compose_terminal()
    terminal[field] = forged_value

    with pytest.raises(ValueError, match="future Gate"):
        build_expected_public_result(config, terminal)


@pytest.mark.parametrize("forged_value", (True, 1, 0, "true", "false"))
def test_rollback_failed_terminal_rejects_impossible_proof_types_and_truth(
    forged_value: object,
) -> None:
    config = load_e2e_v6_config(CONFIG)
    terminal = _non_success_terminal("BLOCKED_ROLLBACK_FAILED_MANUAL_CLEANUP_REQUIRED")
    terminal["rollback_exact_hash_verified"] = forged_value

    with pytest.raises(ValueError, match="boolean|rollback"):
        build_expected_public_result(config, terminal)


def _cleanup_wrapped_rollback_terminal(
    *,
    failed_stage: str,
    failure_code: str,
    rollback_exact_hash_verified: object,
) -> dict[str, object]:
    terminal = _non_success_terminal("BLOCKED_CLEANUP_INCOMPLETE")
    terminal.update(
        {
            "provider_calls": 2,
            "model_calls": 1,
            "fault_injections": 1,
            "forward_mutations": 1,
            "rollback_mutations": 1,
            "recovery_verification_passed": False,
            "rollback_exact_hash_verified": rollback_exact_hash_verified,
            "failed_stage": failed_stage,
            "last_completed_stage": "MULTISERVICE_PROJECTION_COMPLETED",
            "failure_code": failure_code,
        }
    )
    return terminal


def _cleanup_wrapped_compose_terminal() -> dict[str, object]:
    terminal = _non_success_terminal("BLOCKED_CLEANUP_INCOMPLETE")
    _set_pre_fault_truth(terminal)
    terminal.update(
        {
            "provider_calls": 1,
            "model_calls": 0,
            "fault_injections": 0,
            "forward_mutations": 0,
            "rollback_mutations": 0,
            "failed_stage": "COMPOSE_START_RETURNED",
            "last_completed_stage": "COMPOSE_START_REQUESTED",
            "failure_code": "COMPOSE_UP_FAILED",
            "cleanup_failure_code": "CLEANUP_FAILED",
        }
    )
    return terminal


def _versioned_compose_terminal() -> dict[str, object]:
    terminal = _non_success_terminal("BLOCKED_PROVIDER_PREFLIGHT")
    terminal.update(
        {
            "verdict": "BLOCKED_E2E_V6_COMPOSE_UP_FAILED",
            "cleanup_verdict": "CLEAN",
            "cleanup": deepcopy(_success_terminal()["cleanup"]),
            "provider_preflight_passed": True,
            "provider_calls": 1,
            "failed_stage": "COMPOSE_START_RETURNED",
            "last_completed_stage": "COMPOSE_START_REQUESTED",
            "failure_code": "COMPOSE_UP_FAILED",
        }
    )
    return terminal


def _versioned_unclassified_terminal() -> dict[str, object]:
    terminal = _non_success_terminal("BLOCKED_PROVIDER_PREFLIGHT")
    terminal.update(
        {
            "verdict": "BLOCKED_E2E_V6_UNCLASSIFIED_RUNTIME_FAILURE",
            "provider_preflight_passed": True,
            "provider_calls": 1,
            "failed_stage": "LOCAL_DOCKER_VERIFIED",
            "last_completed_stage": "WORKTREE_VERIFIED",
            "failure_code": "UNCLASSIFIED_RUNTIME_FAILURE",
        }
    )
    return terminal


def _cleanup_wrapped_unclassified_terminal() -> dict[str, object]:
    terminal = _cleanup_wrapped_compose_terminal()
    terminal.update(
        {
            "fault_injections": 1,
            "failed_stage": "SOURCE_CAPTURE_WINDOW_STARTED",
            "last_completed_stage": "BASELINE_CONFIGURATION_VERIFIED",
            "failure_code": "UNCLASSIFIED_RUNTIME_FAILURE",
        }
    )
    return terminal


def _typed_unclassified_terminal(
    *,
    failed_stage: str,
    failure_code: str,
    last_completed_stage: str,
    cleanup_required: bool = False,
    fault_injections: int = 0,
) -> dict[str, object]:
    terminal = _versioned_unclassified_terminal()
    terminal.update(
        {
            "failed_stage": failed_stage,
            "failure_code": failure_code,
            "last_completed_stage": last_completed_stage,
            "fault_injections": fault_injections,
        }
    )
    if cleanup_required:
        terminal.update(
            {
                "cleanup_verdict": "CLEAN",
                "cleanup": deepcopy(_success_terminal()["cleanup"]),
            }
        )
    return terminal


def _wrap_blocked_cleanup(terminal: dict[str, object]) -> dict[str, object]:
    terminal.update(
        {
            "verdict": "BLOCKED_CLEANUP_INCOMPLETE",
            "cleanup_verdict": "BLOCKED",
            "cleanup_failure_code": "CLEANUP_FAILED",
            "cleanup": {
                "baseline_restored": False,
                "owned_containers": None,
                "owned_networks": None,
                "owned_volumes": None,
                "non_owned_resources_changed": None,
                "verdict": "BLOCKED",
            },
        }
    )
    return terminal


def _cleanup_wrapped_fault_terminal() -> dict[str, object]:
    terminal = _cleanup_wrapped_compose_terminal()
    terminal.update(
        {
            "fault_injections": 1,
            "failed_stage": "FAULT_IMPACT_GATE_EVALUATED",
            "last_completed_stage": "BASELINE_CONFIGURATION_VERIFIED",
            "failure_code": "FAULT_IMPACT_NOT_OBSERVED",
        }
    )
    return terminal


def _cleanup_wrapped_source_terminal() -> dict[str, object]:
    terminal = _cleanup_wrapped_compose_terminal()
    _set_unavailable_source_evidence(terminal)
    terminal.update(
        {
            "fault_injections": 1,
            "fault_impact_passed": True,
            "failed_stage": "LIVE_TELEMETRY_SOURCE_GATE_EVALUATED",
            "last_completed_stage": "FAULT_IMPACT_GATE_EVALUATED",
            "failure_code": "LIVE_TELEMETRY_SOURCE_GATE_NOT_PASSED",
        }
    )
    return terminal


@pytest.mark.parametrize("forged_value", (True, 1, 0, "true", "false"))
def test_cleanup_wrapped_rollback_failure_rejects_impossible_proof(
    forged_value: object,
) -> None:
    config = load_e2e_v6_config(CONFIG)
    terminal = _cleanup_wrapped_rollback_terminal(
        failed_stage="ROLLBACK_VERIFICATION_EVALUATED",
        failure_code="ROLLBACK_FAILED",
        rollback_exact_hash_verified=forged_value,
    )

    with pytest.raises(ValueError, match="boolean|rollback"):
        build_expected_public_result(config, terminal)


@pytest.mark.parametrize("forged_value", (None, False, 1, "true"))
def test_cleanup_wrapped_completed_rollback_requires_exact_true_proof(
    forged_value: object,
) -> None:
    config = load_e2e_v6_config(CONFIG)
    terminal = _cleanup_wrapped_rollback_terminal(
        failed_stage="REMEDIATION_VERIFICATION_EVALUATED",
        failure_code="REMEDIATION_NOT_VERIFIED",
        rollback_exact_hash_verified=forged_value,
    )

    with pytest.raises(ValueError, match="boolean|rollback"):
        build_expected_public_result(config, terminal)


@pytest.mark.parametrize(
    ("failed_stage", "failure_code", "rollback_exact_hash_verified"),
    (
        ("ROLLBACK_VERIFICATION_EVALUATED", "ROLLBACK_FAILED", False),
        (
            "REMEDIATION_VERIFICATION_EVALUATED",
            "REMEDIATION_NOT_VERIFIED",
            True,
        ),
    ),
)
def test_cleanup_wrapped_rollback_truth_accepts_exact_legal_proof(
    failed_stage: str,
    failure_code: str,
    rollback_exact_hash_verified: bool,
) -> None:
    config = load_e2e_v6_config(CONFIG)
    terminal = _cleanup_wrapped_rollback_terminal(
        failed_stage=failed_stage,
        failure_code=failure_code,
        rollback_exact_hash_verified=rollback_exact_hash_verified,
    )

    public = build_expected_public_result(config, terminal)

    assert public["verdict"] == "BLOCKED_CLEANUP_INCOMPLETE"


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
        match=("stage counts|rollback|cleanup|post-preflight|recovery|Gate|Provider"),
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
    terminal = _cleanup_wrapped_compose_terminal()
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
    terminal = _cleanup_wrapped_compose_terminal()
    terminal[field] = forged_value

    with pytest.raises(ValueError, match="cleanup|failure code|last completed"):
        build_expected_public_result(config, terminal)


def test_final_verifier_requires_a_sealed_private_terminal() -> None:
    config = load_e2e_v6_config(CONFIG)
    public = build_expected_public_result(config, _success_terminal())

    with pytest.raises(ValueError, match="sealed private terminal is required"):
        verify_public_result(config, public, None)


@pytest.mark.parametrize(
    "terminal",
    (
        _success_terminal(),
        _non_success_terminal("LIVE_DIAGNOSIS_GATE_NOT_PASSED_NO_REMEDIATION"),
        _non_success_terminal("BLOCKED_CLEANUP_INCOMPLETE"),
    ),
)
def test_cleanup_schema_rejects_unknown_fields(terminal: dict[str, object]) -> None:
    config = load_e2e_v6_config(CONFIG)
    candidate = deepcopy(terminal)
    cleanup = candidate["cleanup"]
    assert isinstance(cleanup, dict)
    cleanup["BOGUS"] = "x"

    with pytest.raises(ValueError, match="cleanup schema"):
        build_expected_public_result(config, candidate)


@pytest.mark.parametrize(
    "missing_field",
    (
        "baseline_restored",
        "owned_containers",
        "owned_networks",
        "owned_volumes",
        "non_owned_resources_changed",
        "verdict",
    ),
)
def test_blocked_cleanup_schema_rejects_missing_fields(missing_field: str) -> None:
    config = load_e2e_v6_config(CONFIG)
    terminal = _non_success_terminal("BLOCKED_CLEANUP_INCOMPLETE")
    cleanup = terminal["cleanup"]
    assert isinstance(cleanup, dict)
    cleanup.pop(missing_field)

    with pytest.raises(ValueError, match="cleanup schema"):
        build_expected_public_result(config, terminal)


@pytest.mark.parametrize(
    ("source", "status", "count"),
    (
        ("METRICS", "AVAILABLE", 0),
        ("LOGS", "EMPTY", 5),
    ),
)
def test_source_failure_rejects_status_count_contradictions(
    source: str, status: str, count: int
) -> None:
    config = load_e2e_v6_config(CONFIG)
    terminal = _non_success_terminal("BLOCKED_LIVE_TELEMETRY_SOURCE_UNAVAILABLE")
    availability = terminal["source_availability"]
    counts = terminal["source_counts"]
    assert isinstance(availability, dict)
    assert isinstance(counts, dict)
    availability[source] = status
    counts[source] = count

    with pytest.raises(ValueError, match="source"):
        build_expected_public_result(config, terminal)


@pytest.mark.parametrize(
    ("field", "forged_value"),
    (
        ("projection_broad_counts", {"metrics": 1}),
        ("projection_diagnostic_counts", {"metrics": 1}),
        ("visible_service_count", 3),
        ("empty_model_streams", ["TRACES"]),
        ("projection_reason_codes", ["NO_DIAGNOSTIC_TRACES"]),
    ),
)
def test_source_failure_rejects_future_projection_truth(
    field: str, forged_value: object
) -> None:
    config = load_e2e_v6_config(CONFIG)
    terminal = _non_success_terminal("BLOCKED_LIVE_TELEMETRY_SOURCE_UNAVAILABLE")
    terminal[field] = forged_value

    with pytest.raises(ValueError, match="projection"):
        build_expected_public_result(config, terminal)


def test_projection_failure_accepts_no_summary_write_failure() -> None:
    config = load_e2e_v6_config(CONFIG)
    terminal = _non_success_terminal(
        "BLOCKED_BOUNDED_MULTISERVICE_PROJECTION_UNAVAILABLE"
    )
    terminal.update(
        {
            "projection_broad_counts": {},
            "projection_diagnostic_counts": {},
            "empty_model_streams": [],
            "projection_reason_codes": [],
        }
    )

    public = build_expected_public_result(config, terminal)

    assert public["verdict"] == terminal["verdict"]


def test_projection_failure_accepts_consistent_blocking_summary() -> None:
    config = load_e2e_v6_config(CONFIG)
    terminal = _non_success_terminal(
        "BLOCKED_BOUNDED_MULTISERVICE_PROJECTION_UNAVAILABLE"
    )

    public = build_expected_public_result(config, terminal)

    assert public["verdict"] == terminal["verdict"]


def test_projection_failure_rejects_nonblocking_success_summary() -> None:
    config = load_e2e_v6_config(CONFIG)
    terminal = _non_success_terminal(
        "BLOCKED_BOUNDED_MULTISERVICE_PROJECTION_UNAVAILABLE"
    )
    _set_complete_fault_evidence(terminal, context_completed=False)

    with pytest.raises(ValueError, match="blocking reasons"):
        build_expected_public_result(config, terminal)


@pytest.mark.parametrize(
    "verdict",
    (
        "LIVE_FAULT_A0_CONTROLLED_REMEDIATION_E2E_V6_PASSED_READY_FOR_REVIEW",
        "LIVE_DIAGNOSIS_GATE_NOT_PASSED_NO_REMEDIATION",
        "BLOCKED_POLICY_REJECTED",
        "BLOCKED_CLEANUP_INCOMPLETE",
    ),
)
def test_projection_reason_rejects_no_broad_metrics_when_broad_metrics_exist(
    verdict: str,
) -> None:
    config = load_e2e_v6_config(CONFIG)
    terminal = (
        _success_terminal()
        if verdict.endswith("PASSED_READY_FOR_REVIEW")
        else _non_success_terminal(verdict)
    )
    reasons = terminal["projection_reason_codes"]
    assert isinstance(reasons, list)
    reasons.append("NO_BROAD_METRICS")

    with pytest.raises(ValueError, match="broad|projection reasons"):
        build_expected_public_result(config, terminal)


@pytest.mark.parametrize("forged_last", ("WORKTREE_VERIFIED", "BOGUS_STAGE"))
def test_versioned_compose_rejects_inexact_last_completed_stage(
    forged_last: str,
) -> None:
    config = load_e2e_v6_config(CONFIG)
    terminal = _versioned_compose_terminal()
    terminal["last_completed_stage"] = forged_last

    with pytest.raises(ValueError, match="root identity"):
        build_expected_public_result(config, terminal)


@pytest.mark.parametrize("forged_last", ("WORKTREE_VERIFIED", "BOGUS_STAGE"))
def test_public_result_failure_rejects_inexact_outer_completion_root(
    forged_last: str,
) -> None:
    config = load_e2e_v6_config(CONFIG)
    terminal = _non_success_terminal("BLOCKED_PUBLIC_RESULT_VERIFICATION")
    terminal["last_completed_stage"] = forged_last

    with pytest.raises(ValueError, match="last completed"):
        build_expected_public_result(config, terminal)


@pytest.mark.parametrize(
    "factory",
    (_versioned_unclassified_terminal, _cleanup_wrapped_unclassified_terminal),
)
def test_unclassified_terminals_accept_ordered_diagnostic_stage_identity(
    factory: object,
) -> None:
    config = load_e2e_v6_config(CONFIG)
    assert callable(factory)
    terminal = factory()

    public = build_expected_public_result(config, terminal)

    assert public["verdict"] == terminal["verdict"]


@pytest.mark.parametrize(
    "factory",
    (_versioned_unclassified_terminal, _cleanup_wrapped_unclassified_terminal),
)
@pytest.mark.parametrize(
    ("failed_stage", "last_completed_stage"),
    (
        ("BOGUS_FAILED", "BOGUS_LAST"),
        ("WORKTREE_VERIFIED", "LOCAL_DOCKER_VERIFIED"),
    ),
)
def test_unclassified_terminals_reject_unknown_or_reversed_stage_identity(
    factory: object,
    failed_stage: str,
    last_completed_stage: str,
) -> None:
    config = load_e2e_v6_config(CONFIG)
    assert callable(factory)
    terminal = factory()
    terminal.update(
        {
            "failed_stage": failed_stage,
            "last_completed_stage": last_completed_stage,
        }
    )

    with pytest.raises(ValueError, match="runtime identity|preserved root"):
        build_expected_public_result(config, terminal)


@pytest.mark.parametrize(
    "factory",
    (
        lambda: _non_success_terminal("BLOCKED_PROVIDER_PREFLIGHT"),
        _versioned_compose_terminal,
        lambda: _non_success_terminal("BLOCKED_FAULT_IMPACT_NOT_OBSERVED"),
        lambda: _non_success_terminal("BLOCKED_LIVE_TELEMETRY_SOURCE_UNAVAILABLE"),
        lambda: _non_success_terminal("LIVE_DIAGNOSIS_GATE_NOT_PASSED_NO_REMEDIATION"),
    ),
)
def test_direct_terminal_rejects_unreachable_cleanup_verdict(factory: object) -> None:
    config = load_e2e_v6_config(CONFIG)
    assert callable(factory)
    terminal = factory()
    cleanup = terminal["cleanup"]
    assert isinstance(cleanup, dict)
    forged_verdict = "CLEAN" if cleanup["verdict"] == "NOT_REQUIRED" else "NOT_REQUIRED"
    cleanup["verdict"] = forged_verdict
    terminal["cleanup_verdict"] = forged_verdict

    with pytest.raises(ValueError, match="cleanup truth"):
        build_expected_public_result(config, terminal)


@pytest.mark.parametrize(
    "status",
    (
        "EMPTY",
        "HTTP_FAILED",
        "SCHEMA_MISMATCH",
        "FIELD_MAPPING_UNSUPPORTED",
        "IDENTITY_MISMATCH",
        "INGESTION_TIMEOUT",
    ),
)
def test_source_failure_rejects_nonzero_count_for_unavailable_status(
    status: str,
) -> None:
    config = load_e2e_v6_config(CONFIG)
    terminal = _non_success_terminal("BLOCKED_LIVE_TELEMETRY_SOURCE_UNAVAILABLE")
    availability = terminal["source_availability"]
    counts = terminal["source_counts"]
    assert isinstance(availability, dict)
    assert isinstance(counts, dict)
    availability["LOGS"] = status
    counts["LOGS"] = 5

    with pytest.raises(ValueError, match="unavailable source"):
        build_expected_public_result(config, terminal)


def test_source_failure_accepts_positive_invalid_record_with_invalid_refs() -> None:
    config = load_e2e_v6_config(CONFIG)
    terminal = _non_success_terminal("BLOCKED_LIVE_TELEMETRY_SOURCE_UNAVAILABLE")
    availability = terminal["source_availability"]
    counts = terminal["source_counts"]
    assert isinstance(availability, dict)
    assert isinstance(counts, dict)
    availability["LOGS"] = "INVALID_RECORD"
    counts["LOGS"] = 5
    terminal["invalid_refs"] = 1
    terminal["all_refs_resolve"] = False

    public = build_expected_public_result(config, terminal)

    public_availability = public["source_availability"]
    assert isinstance(public_availability, dict)
    assert public_availability["LOGS"] == "INVALID_RECORD"


def test_source_failure_rejects_zero_count_invalid_record_with_invalid_refs() -> None:
    config = load_e2e_v6_config(CONFIG)
    terminal = _non_success_terminal("BLOCKED_LIVE_TELEMETRY_SOURCE_UNAVAILABLE")
    availability = terminal["source_availability"]
    counts = terminal["source_counts"]
    assert isinstance(availability, dict)
    assert isinstance(counts, dict)
    availability["LOGS"] = "INVALID_RECORD"
    counts["LOGS"] = 0
    terminal["invalid_refs"] = 1
    terminal["all_refs_resolve"] = False

    with pytest.raises(ValueError, match="positive invalid-record"):
        build_expected_public_result(config, terminal)


def test_source_failure_rejects_available_sources_with_invalid_refs() -> None:
    config = load_e2e_v6_config(CONFIG)
    terminal = _non_success_terminal("BLOCKED_LIVE_TELEMETRY_SOURCE_UNAVAILABLE")
    terminal.update(
        {
            "source_availability": {
                "METRICS": "AVAILABLE",
                "LOGS": "AVAILABLE",
                "TRACES": "AVAILABLE",
            },
            "source_counts": {"METRICS": 5, "LOGS": 28, "TRACES": 14},
            "invalid_refs": 1,
            "all_refs_resolve": False,
        }
    )

    with pytest.raises(ValueError, match="Evidence.ref|invalid Evidence"):
        build_expected_public_result(config, terminal)


@pytest.mark.parametrize("field", ("plan_action", "policy_verdict"))
def test_cleanup_wrapped_unclassified_rejects_future_policy_truth(field: str) -> None:
    config = load_e2e_v6_config(CONFIG)
    terminal = _cleanup_wrapped_unclassified_terminal()
    terminal[field] = "BOGUS"

    with pytest.raises(ValueError, match="future Gate"):
        build_expected_public_result(config, terminal)


def _public_result_failure_for_success_source() -> dict[str, object]:
    terminal = _success_terminal()
    terminal.update(
        {
            "public_result_source_verdict": terminal["verdict"],
            "public_result_source_failed_stage": None,
            "public_result_source_last_completed_stage": None,
            "public_result_source_failure_code": None,
            "verdict": "BLOCKED_PUBLIC_RESULT_VERIFICATION",
            "failed_stage": "PUBLIC_RESULT_VERIFICATION",
            "last_completed_stage": "CLEANUP_COMPLETED",
            "failure_code": "PUBLIC_RESULT_VERIFICATION_FAILED",
        }
    )
    return terminal


def test_public_result_failure_accepts_success_source_without_failure_identity() -> (
    None
):
    config = load_e2e_v6_config(CONFIG)
    terminal = _public_result_failure_for_success_source()

    public = build_expected_public_result(config, terminal)

    assert public["verdict"] == "BLOCKED_PUBLIC_RESULT_VERIFICATION"


@pytest.mark.parametrize(
    "field",
    (
        "public_result_source_failed_stage",
        "public_result_source_last_completed_stage",
        "public_result_source_failure_code",
    ),
)
def test_public_result_success_source_rejects_forged_failure_identity(
    field: str,
) -> None:
    config = load_e2e_v6_config(CONFIG)
    terminal = _public_result_failure_for_success_source()
    terminal[field] = "BOGUS"

    with pytest.raises(ValueError, match="success source"):
        build_expected_public_result(config, terminal)


@pytest.mark.parametrize(
    "terminal",
    (
        _typed_unclassified_terminal(
            failed_stage="LOCAL_DOCKER_VERIFIED",
            failure_code="DOCKER_AUTHORITY_UNAVAILABLE",
            last_completed_stage="WORKTREE_VERIFIED",
        ),
        _typed_unclassified_terminal(
            failed_stage="PORT_PREFLIGHT_STARTED",
            failure_code="PORT_CONFLICT",
            last_completed_stage="FAULT_CONTROLLER_PREPARED",
        ),
        _typed_unclassified_terminal(
            failed_stage="SOURCE_CAPTURE_WINDOW_STARTED",
            failure_code="UNCLASSIFIED_RUNTIME_FAILURE",
            last_completed_stage="BASELINE_CONFIGURATION_VERIFIED",
            cleanup_required=True,
            fault_injections=1,
        ),
        _typed_unclassified_terminal(
            failed_stage="SOURCE_CAPTURE_WINDOW_STARTED",
            failure_code="UNCLASSIFIED_RUNTIME_FAILURE",
            last_completed_stage="BASELINE_CONFIGURATION_VERIFIED",
            cleanup_required=True,
            fault_injections=0,
        ),
    ),
)
def test_versioned_unclassified_accepts_exact_shared_runtime_fallbacks(
    terminal: dict[str, object],
) -> None:
    config = load_e2e_v6_config(CONFIG)

    public = build_expected_public_result(config, deepcopy(terminal))

    assert public["verdict"] == "BLOCKED_E2E_V6_UNCLASSIFIED_RUNTIME_FAILURE"


def _cleanup_wrapped_source_collection_runtime() -> dict[str, object]:
    terminal = _typed_unclassified_terminal(
        failed_stage="SOURCE_CAPTURE_WINDOW_COMPLETED",
        failure_code="SOURCE_BATCH_CONTRACT_FAILED",
        last_completed_stage="SOURCE_CAPTURE_WINDOW_STARTED",
        cleanup_required=True,
        fault_injections=1,
    )
    terminal["fault_impact_passed"] = True
    return _wrap_blocked_cleanup(terminal)


def _cleanup_wrapped_post_projection_runtime() -> dict[str, object]:
    terminal = _typed_unclassified_terminal(
        failed_stage="POST_PROJECTION_RUNTIME",
        failure_code="UNCLASSIFIED_RUNTIME_FAILURE",
        last_completed_stage="MULTISERVICE_PROJECTION_COMPLETED",
        cleanup_required=True,
        fault_injections=1,
    )
    _set_complete_fault_evidence(terminal, context_completed=True)
    terminal["fault_impact_passed"] = True
    return _wrap_blocked_cleanup(terminal)


def _cleanup_wrapped_live_diagnosis_runtime() -> dict[str, object]:
    terminal = _cleanup_wrapped_post_projection_runtime()
    terminal.update(
        {
            "failed_stage": "LIVE_DIAGNOSIS_RUNTIME",
            "provider_calls": 2,
            "model_calls": 0,
        }
    )
    return terminal


def _policy_runtime_terminal(*, cleanup_blocked: bool) -> dict[str, object]:
    terminal = _typed_unclassified_terminal(
        failed_stage="POLICY_RUNTIME",
        failure_code="UNCLASSIFIED_RUNTIME_FAILURE",
        last_completed_stage="MULTISERVICE_PROJECTION_COMPLETED",
        cleanup_required=True,
        fault_injections=1,
    )
    _set_complete_fault_evidence(terminal, context_completed=True)
    terminal.update(
        {
            "provider_calls": 2,
            "model_calls": 1,
            "fault_impact_passed": True,
            "diagnosis_gate": True,
            "diagnosis_correct": True,
            "plan_action": None,
            "policy_verdict": None,
        }
    )
    return _wrap_blocked_cleanup(terminal) if cleanup_blocked else terminal


def _source_complete_runtime_terminal(*, cleanup_blocked: bool) -> dict[str, object]:
    terminal = _typed_unclassified_terminal(
        failed_stage="SOURCE_AVAILABILITY_GATE_EVALUATED",
        failure_code="UNCLASSIFIED_RUNTIME_FAILURE",
        last_completed_stage="EVIDENCE_RESOLUTION_COMPLETED",
        cleanup_required=True,
        fault_injections=1,
    )
    success = _success_terminal()
    for field in (
        "source_availability",
        "source_counts",
        "invalid_refs",
        "all_refs_resolve",
    ):
        terminal[field] = deepcopy(success[field])
    terminal["fault_impact_passed"] = True
    return _wrap_blocked_cleanup(terminal) if cleanup_blocked else terminal


def _source_stage_order_runtime_terminal() -> dict[str, object]:
    terminal = _source_complete_runtime_terminal(cleanup_blocked=False)
    terminal.update(
        {
            "failed_stage": "NO_FAULT_READINESS_EVALUATED",
            "last_completed_stage": "SOURCE_AVAILABILITY_GATE_EVALUATED",
        }
    )
    return terminal


def _public_result_failure_for_source_stage_order_runtime() -> dict[str, object]:
    source = _source_stage_order_runtime_terminal()
    terminal = deepcopy(source)
    terminal.update(
        {
            "public_result_source_verdict": source["verdict"],
            "public_result_source_failed_stage": source["failed_stage"],
            "public_result_source_last_completed_stage": source[
                "last_completed_stage"
            ],
            "public_result_source_failure_code": source["failure_code"],
            "verdict": "BLOCKED_PUBLIC_RESULT_VERIFICATION",
            "failed_stage": "PUBLIC_RESULT_VERIFICATION",
            "last_completed_stage": "CLEANUP_COMPLETED",
            "failure_code": "PUBLIC_RESULT_VERIFICATION_FAILED",
        }
    )
    return terminal


@pytest.mark.parametrize(
    "terminal",
    (
        _cleanup_wrapped_source_collection_runtime(),
        _cleanup_wrapped_post_projection_runtime(),
        _cleanup_wrapped_live_diagnosis_runtime(),
    ),
)
def test_cleanup_wrapped_unclassified_accepts_phase_consistent_truth(
    terminal: dict[str, object],
) -> None:
    config = load_e2e_v6_config(CONFIG)

    public = build_expected_public_result(config, deepcopy(terminal))

    assert public["verdict"] == "BLOCKED_CLEANUP_INCOMPLETE"


@pytest.mark.parametrize("cleanup_blocked", (False, True))
def test_policy_runtime_rejects_allow_without_remediation_phase(
    cleanup_blocked: bool,
) -> None:
    config = load_e2e_v6_config(CONFIG)
    terminal = _policy_runtime_terminal(cleanup_blocked=cleanup_blocked)
    public = build_expected_public_result(config, deepcopy(terminal))
    assert public["verdict"] == (
        "BLOCKED_CLEANUP_INCOMPLETE"
        if cleanup_blocked
        else "BLOCKED_E2E_V6_UNCLASSIFIED_RUNTIME_FAILURE"
    )
    terminal["policy_verdict"] = "ALLOW"

    with pytest.raises(ValueError, match="Policy runtime"):
        build_expected_public_result(config, terminal)


@pytest.mark.parametrize("cleanup_blocked", (False, True))
def test_post_source_unclassified_accepts_sealed_source_batch(
    cleanup_blocked: bool,
) -> None:
    config = load_e2e_v6_config(CONFIG)
    terminal = _source_complete_runtime_terminal(cleanup_blocked=cleanup_blocked)

    public = build_expected_public_result(config, terminal)

    assert public["source_availability"] == {
        "METRICS": "AVAILABLE",
        "LOGS": "AVAILABLE",
        "TRACES": "AVAILABLE",
    }


def test_public_projection_accepts_observed_source_stage_order_failure() -> None:
    config = load_e2e_v6_config(CONFIG)
    terminal = _public_result_failure_for_source_stage_order_runtime()

    public = build_expected_public_result(config, terminal)
    verify_public_result(config, public, terminal)

    assert public["verdict"] == "BLOCKED_PUBLIC_RESULT_VERIFICATION"
    assert public["fault_injections"] == 1
    assert public["model_calls"] == 0
    assert public["forward_mutations"] == 0


def test_public_projection_rejects_forged_source_stage_order_identity() -> None:
    config = load_e2e_v6_config(CONFIG)
    terminal = _public_result_failure_for_source_stage_order_runtime()
    terminal["public_result_source_last_completed_stage"] = (
        "TRACES_PREFLIGHT_COMPLETED"
    )

    with pytest.raises(ValueError, match="runtime identity"):
        build_expected_public_result(config, terminal)


@pytest.mark.parametrize(
    "terminal",
    (
        _typed_unclassified_terminal(
            failed_stage="LOCAL_DOCKER_VERIFIED",
            failure_code="PORT_CONFLICT",
            last_completed_stage="WORKTREE_VERIFIED",
        ),
        _typed_unclassified_terminal(
            failed_stage="STABILIZATION_STARTED",
            failure_code="UNCLASSIFIED_RUNTIME_FAILURE",
            last_completed_stage="SERVICES_HEALTHY",
            cleanup_required=True,
            fault_injections=1,
        ),
    ),
)
def test_versioned_unclassified_rejects_stage_code_or_fault_count_mismatch(
    terminal: dict[str, object],
) -> None:
    config = load_e2e_v6_config(CONFIG)

    with pytest.raises(ValueError, match="runtime identity|stage counts"):
        build_expected_public_result(config, deepcopy(terminal))


@pytest.mark.parametrize(
    ("failed_stage", "failure_code", "last_completed_stage"),
    (
        (
            "OWNED_RESOURCE_INVENTORY_VERIFIED",
            "OWNED_RESOURCE_INVENTORY_INCOMPLETE",
            "COMPOSE_START_RETURNED",
        ),
        (
            "SERVICES_HEALTHY",
            "SERVICE_HEALTH_TIMEOUT",
            "SERVICE_HEALTH_WAIT_STARTED",
        ),
    ),
)
def test_cleanup_wrapper_accepts_exact_post_compose_runtime_roots(
    failed_stage: str,
    failure_code: str,
    last_completed_stage: str,
) -> None:
    config = load_e2e_v6_config(CONFIG)
    terminal = _cleanup_wrapped_compose_terminal()
    terminal.update(
        {
            "failed_stage": failed_stage,
            "failure_code": failure_code,
            "last_completed_stage": last_completed_stage,
        }
    )

    public = build_expected_public_result(config, terminal)

    assert public["verdict"] == "BLOCKED_CLEANUP_INCOMPLETE"


@pytest.mark.parametrize(
    "factory",
    (
        _cleanup_wrapped_compose_terminal,
        _cleanup_wrapped_fault_terminal,
        _cleanup_wrapped_source_terminal,
    ),
)
@pytest.mark.parametrize(
    ("field", "forged_value"),
    (
        ("provider_calls", 2),
        ("model_calls", 1),
    ),
)
def test_cleanup_wrapper_rejects_counts_beyond_preserved_root(
    factory: object,
    field: str,
    forged_value: int,
) -> None:
    config = load_e2e_v6_config(CONFIG)
    assert callable(factory)
    terminal = factory()
    terminal[field] = forged_value
    if field == "model_calls":
        terminal.update(
            {
                "provider_calls": 2,
                "a0_context_builder_calls": 1,
                "fault_time_a0_context_artifact_exists": True,
                "fault_time_a0_context_sha256": "a" * 64,
                "provider_live_context_sha256": "a" * 64,
            }
        )

    with pytest.raises(ValueError, match="stage counts"):
        build_expected_public_result(config, terminal)


def test_cleanup_wrapper_rejects_pre_compose_provider_root() -> None:
    config = load_e2e_v6_config(CONFIG)
    terminal = _cleanup_wrapped_compose_terminal()
    terminal.update(
        {
            "failed_stage": "PROVIDER_PREFLIGHT",
            "last_completed_stage": "WORKTREE_VERIFIED",
            "failure_code": "PROVIDER_PREFLIGHT_FAILED",
            "plan_action": "BOGUS",
        }
    )

    with pytest.raises(ValueError, match="preserved root"):
        build_expected_public_result(config, terminal)


@pytest.mark.parametrize(
    "cleanup",
    (
        {
            "baseline_restored": True,
            "owned_containers": 0,
            "owned_networks": 0,
            "owned_volumes": 0,
            "non_owned_resources_changed": False,
            "verdict": "BLOCKED",
        },
        {
            "baseline_restored": False,
            "owned_containers": None,
            "owned_networks": 0,
            "owned_volumes": 0,
            "non_owned_resources_changed": False,
            "verdict": "BLOCKED",
        },
    ),
)
def test_blocked_cleanup_rejects_unreachable_aggregate_shapes(
    cleanup: dict[str, object],
) -> None:
    config = load_e2e_v6_config(CONFIG)
    terminal = _non_success_terminal("BLOCKED_CLEANUP_INCOMPLETE")
    terminal["cleanup"] = cleanup

    with pytest.raises(ValueError, match="blocked cleanup"):
        build_expected_public_result(config, terminal)


def test_blocked_cleanup_accepts_concrete_dirty_aggregate() -> None:
    config = load_e2e_v6_config(CONFIG)
    terminal = _non_success_terminal("BLOCKED_CLEANUP_INCOMPLETE")
    terminal["cleanup"] = {
        "baseline_restored": False,
        "owned_containers": 0,
        "owned_networks": 0,
        "owned_volumes": 0,
        "non_owned_resources_changed": False,
        "verdict": "BLOCKED",
    }

    public = build_expected_public_result(config, terminal)

    cleanup = public["cleanup"]
    assert isinstance(cleanup, dict)
    assert cleanup["verdict"] == "BLOCKED"


@pytest.mark.parametrize(
    ("factory", "field"),
    (
        (
            lambda: _non_success_terminal("BLOCKED_PROVIDER_PREFLIGHT"),
            "provider_preflight_passed",
        ),
        (
            lambda: _non_success_terminal("BLOCKED_FAULT_IMPACT_NOT_OBSERVED"),
            "fault_impact_passed",
        ),
        (_versioned_compose_terminal, "fault_time_a0_context_artifact_exists"),
        (_versioned_compose_terminal, "rollback_exact_hash_verified"),
        (
            lambda: _non_success_terminal("BLOCKED_LIVE_TELEMETRY_SOURCE_UNAVAILABLE"),
            "rollback_exact_hash_verified",
        ),
    ),
)
def test_early_terminals_reject_explicit_false_for_runtime_absent_fields(
    factory: object,
    field: str,
) -> None:
    config = load_e2e_v6_config(CONFIG)
    assert callable(factory)
    terminal = factory()
    terminal[field] = False

    with pytest.raises(ValueError, match="Provider|fault-impact|context|rollback"):
        build_expected_public_result(config, terminal)


def test_success_rejects_explicit_false_rollback_proof() -> None:
    config = load_e2e_v6_config(CONFIG)
    terminal = _success_terminal()
    terminal["rollback_exact_hash_verified"] = False

    with pytest.raises(ValueError, match="rollback proof"):
        build_expected_public_result(config, terminal)


@pytest.mark.parametrize(
    "terminal",
    (
        _success_terminal(),
        _non_success_terminal("BLOCKED_PROVIDER_PREFLIGHT"),
        _non_success_terminal("LIVE_DIAGNOSIS_GATE_NOT_PASSED_NO_REMEDIATION"),
    ),
)
def test_terminal_rejects_result_head_different_from_implementation_commit(
    terminal: dict[str, object],
) -> None:
    config = load_e2e_v6_config(CONFIG)
    candidate = deepcopy(terminal)
    candidate["implementation_commit"] = "a" * 40
    candidate["result_head"] = "b" * 40

    with pytest.raises(ValueError, match="result head differs"):
        build_expected_public_result(config, candidate)
