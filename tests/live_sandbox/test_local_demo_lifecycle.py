from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path

import pytest

from ecomsre_live_sandbox.e2e_v6_contracts import E2EV6PrivateRoots
from ecomsre_live_sandbox.local_demo_contracts import (
    LOCAL_DEMO_CONFIG_RELATIVE,
    LocalDemoPrivateRoot,
    load_local_demo_config,
    validate_provider_env,
)
import ecomsre_live_sandbox.local_e2e_demo_v1 as local_demo_runner
from ecomsre_live_sandbox.local_e2e_demo_v1 import build_public_result


ROOT = Path(__file__).resolve().parents[2]


def test_local_demo_config_preserves_r3_and_exposes_local_runtime_authority() -> None:
    config = load_local_demo_config(ROOT / LOCAL_DEMO_CONFIG_RELATIVE)

    assert config.authority.branch == "feature/local-e2e-demo-v1"
    assert config.authority.predecessor_head == (
        "f939824c9b33eca69939aab5d6aa6a5097123e7e"
    )
    assert config.authority.run_generation == "LOCAL_DEMO"
    assert config.authority.invocation_b_success == (
        "LOCAL_DEMO_E2E_PASSED_READY_FOR_REVIEW"
    )
    assert config.sandbox.scenario.target_service == "payment"
    assert config.r3.authority.run_generation == "V6_REPRO_3"


def test_standing_authorization_is_create_once_and_semantic_scope_bound(
    tmp_path: Path,
) -> None:
    config = load_local_demo_config(ROOT / LOCAL_DEMO_CONFIG_RELATIVE)
    private = LocalDemoPrivateRoot(tmp_path / "private")

    first = private.ensure_standing_authorization(config)
    second = private.ensure_standing_authorization(config)

    assert first == second
    assert first.approver == "Minghong Sun"
    assert first.codex_autonomous_self_approval is False
    assert first.action == "RESTORE_FROZEN_SERVICE_CONFIGURATION"
    assert (private.root / "authorization.json").stat().st_mode & 0o777 == 0o600

    changed = config.sandbox.model_copy(
        update={
            "scenario": config.sandbox.scenario.model_copy(
                update={"target_service": "checkout"}
            )
        }
    )
    with pytest.raises(ValueError, match="semantic scope"):
        private.validate_standing_authorization(first, changed)


def test_attempt_retry_requires_real_change_and_clean_previous_attempt(
    tmp_path: Path,
) -> None:
    config = load_local_demo_config(ROOT / LOCAL_DEMO_CONFIG_RELATIVE)
    private = LocalDemoPrivateRoot(tmp_path / "private")
    private.ensure_standing_authorization(config)
    attempt = private.allocate_attempt(
        implementation_commit="1" * 40,
        runtime_config_sha256="2" * 64,
    )
    private.complete_attempt(
        attempt,
        {
            "verdict": "LIVE_DIAGNOSIS_GATE_NOT_PASSED_NO_REMEDIATION",
            "fault_injections": 1,
            "model_calls": 1,
            "forward_mutations": 0,
            "rollback_mutations": 0,
            "cleanup_verdict": "CLEAN",
        },
    )

    with pytest.raises(RuntimeError, match="identical local-demo attempt"):
        private.allocate_attempt(
            implementation_commit="1" * 40,
            runtime_config_sha256="2" * 64,
        )

    second = private.allocate_attempt(
        implementation_commit="3" * 40,
        runtime_config_sha256="2" * 64,
    )
    assert second.name == "attempt-0002"


def test_safe_prestart_not_required_attempt_can_retry_after_change(
    tmp_path: Path,
) -> None:
    config = load_local_demo_config(ROOT / LOCAL_DEMO_CONFIG_RELATIVE)
    private = LocalDemoPrivateRoot(tmp_path / "private")
    private.ensure_standing_authorization(config)
    attempt = private.allocate_attempt(
        implementation_commit="1" * 40,
        runtime_config_sha256="2" * 64,
    )
    private.complete_attempt(
        attempt,
        {
            "verdict": "BLOCKED_PROVIDER_PREFLIGHT",
            "compose_start_requested": False,
            "owned_resources_observed": {},
            "fault_injections": 0,
            "model_calls": 0,
            "forward_mutations": 0,
            "rollback_mutations": 0,
            "cleanup_verdict": "NOT_REQUIRED",
        },
    )

    second = private.allocate_attempt(
        implementation_commit="3" * 40,
        runtime_config_sha256="2" * 64,
    )

    assert second.name == "attempt-0002"


def test_development_probe_exception_seals_safe_attempt_and_changed_head_retries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = load_local_demo_config(ROOT / LOCAL_DEMO_CONFIG_RELATIVE)
    private = LocalDemoPrivateRoot(tmp_path / "private")
    initial_head = "a" * 40
    repaired_head = "b" * 40
    private.record_pre_live_admission(
        implementation_commit=initial_head,
        ci_workflow="Agent mainline",
        ci_run_id=123,
        reviewer_must_fix_count=0,
        recorded_at=datetime.now(timezone.utc),
    )
    heads = iter((initial_head, repaired_head))
    monkeypatch.setattr(
        local_demo_runner,
        "verify_local_demo_worktree",
        lambda *_: next(heads),
    )
    monkeypatch.setattr(
        local_demo_runner.e2e_v6,
        "run_development_probe",
        lambda *_: (_ for _ in ()).throw(RuntimeError("simulated probe exception")),
    )
    provider_environment = {"ECOMSRE_LLM_MODEL": config.authority.a0_model}

    with pytest.raises(RuntimeError, match="simulated probe exception"):
        local_demo_runner.run_local_demo(
            config,
            private,
            provider_environment=provider_environment,
        )

    history = json.loads(
        (private.root / "attempt-history.json").read_text(encoding="utf-8")
    )
    assert history["attempts"][0]["verdict"] == (
        "BLOCKED_LOCAL_DEMO_DEVELOPMENT_PROBE_SAFE_PRESTART_FAILURE"
    )
    assert history["attempts"][0]["cleanup_verdict"] == "NOT_REQUIRED"
    assert history["attempts"][0]["compose_start_requested"] is False

    safe_terminal = {
        "verdict": "BLOCKED_LOCAL_DEMO_DEVELOPMENT_PROBE_SAFE_PRESTART_FAILURE",
        "compose_start_requested": False,
        "owned_resources_observed": {},
        "fault_injections": 0,
        "model_calls": 0,
        "forward_mutations": 0,
        "rollback_mutations": 0,
        "cleanup_verdict": "NOT_REQUIRED",
    }
    monkeypatch.setattr(
        local_demo_runner.e2e_v6,
        "run_development_probe",
        lambda *_: safe_terminal,
    )

    result = local_demo_runner.run_local_demo(
        config,
        private,
        provider_environment=provider_environment,
    )

    assert result == safe_terminal
    history = json.loads(
        (private.root / "attempt-history.json").read_text(encoding="utf-8")
    )
    assert [item["verdict"] for item in history["attempts"]] == [
        "BLOCKED_LOCAL_DEMO_DEVELOPMENT_PROBE_SAFE_PRESTART_FAILURE",
        "BLOCKED_LOCAL_DEMO_DEVELOPMENT_PROBE_SAFE_PRESTART_FAILURE",
    ]


def test_failed_diagnosis_lineage_allows_transport_failure_without_raw_response(
    tmp_path: Path,
) -> None:
    roots = E2EV6PrivateRoots(tmp_path / "evidence")
    roots.prepare()

    local_demo_runner._write_failed_diagnosis_lineage(
        roots,
        object(),
        {"visible_entities": []},
        ConnectionError("simulated Provider transport failure"),
    )

    manifest = json.loads(
        (
            roots.provider / "diagnosis-lineage" / "failure-manifest.json"
        ).read_text(encoding="utf-8")
    )
    assert manifest["artifacts"] == {}
    assert manifest["failure_type"] == "ConnectionError"
    assert "simulated Provider transport failure" not in json.dumps(manifest)


def test_provider_env_validation_returns_only_safe_metadata(tmp_path: Path) -> None:
    path = tmp_path / "provider.env"
    path.write_text(
        "ECOMSRE_LLM_BASE_URL=https://provider.invalid/v1\n"
        "ECOMSRE_LLM_API_KEY=secret-never-returned\n"
        "ECOMSRE_LLM_MODEL=gpt-5.4-mini-2026-03-17\n",
        encoding="utf-8",
    )
    os.chmod(path, 0o600)

    values, metadata = validate_provider_env(path)

    assert set(values) == {
        "ECOMSRE_LLM_BASE_URL",
        "ECOMSRE_LLM_API_KEY",
        "ECOMSRE_LLM_MODEL",
    }
    assert metadata == {
        "provider_config_valid": True,
        "required_variable_count": 3,
        "model": "gpt-5.4-mini-2026-03-17",
    }
    assert "secret-never-returned" not in json.dumps(metadata)


def test_provider_env_validation_accepts_literal_export_declarations(
    tmp_path: Path,
) -> None:
    path = tmp_path / "provider.env"
    path.write_text(
        "export ECOMSRE_LLM_BASE_URL=https://provider.invalid/v1\n"
        "export ECOMSRE_LLM_API_KEY=secret-never-returned\n"
        "export ECOMSRE_LLM_MODEL=gpt-5.4-mini-2026-03-17\n",
        encoding="utf-8",
    )
    os.chmod(path, 0o600)

    values, metadata = validate_provider_env(path)

    assert values["ECOMSRE_LLM_MODEL"] == "gpt-5.4-mini-2026-03-17"
    assert metadata["provider_config_valid"] is True


@pytest.mark.parametrize("value", ["$(whoami)", "${HOME}", "`whoami`"])
def test_provider_env_validation_rejects_shell_expansion(
    tmp_path: Path, value: str
) -> None:
    path = tmp_path / "provider.env"
    path.write_text(
        "ECOMSRE_LLM_BASE_URL=https://provider.invalid/v1\n"
        f"ECOMSRE_LLM_API_KEY={value}\n"
        "ECOMSRE_LLM_MODEL=gpt-5.4-mini-2026-03-17\n",
        encoding="utf-8",
    )
    os.chmod(path, 0o600)

    with pytest.raises(ValueError, match="shell expansion"):
        validate_provider_env(path)


@pytest.mark.parametrize("mode", [0o644, 0o400])
def test_provider_env_validation_rejects_non_0600_mode(
    tmp_path: Path, mode: int
) -> None:
    path = tmp_path / "provider.env"
    path.write_text(
        "ECOMSRE_LLM_BASE_URL=x\nECOMSRE_LLM_API_KEY=y\nECOMSRE_LLM_MODEL=z\n",
        encoding="utf-8",
    )
    os.chmod(path, mode)

    with pytest.raises(ValueError, match="0600"):
        validate_provider_env(path)


def test_pre_live_admission_is_exact_head_and_create_once(tmp_path: Path) -> None:
    private = LocalDemoPrivateRoot(tmp_path / "private")
    created = private.record_pre_live_admission(
        implementation_commit="a" * 40,
        ci_workflow="Agent mainline",
        ci_run_id=123,
        reviewer_must_fix_count=0,
        recorded_at=datetime.now(timezone.utc),
    )

    assert private.require_pre_live_admission("a" * 40) == created
    with pytest.raises(RuntimeError, match="exact implementation head"):
        private.require_pre_live_admission("b" * 40)

    attempt = private.allocate_attempt(
        implementation_commit="a" * 40,
        runtime_config_sha256="c" * 64,
    )
    private.complete_attempt(
        attempt,
        {
            "verdict": "BLOCKED_PROVIDER_PREFLIGHT",
            "compose_start_requested": False,
            "owned_resources_observed": {},
            "fault_injections": 0,
            "model_calls": 0,
            "forward_mutations": 0,
            "rollback_mutations": 0,
            "cleanup_verdict": "NOT_REQUIRED",
        },
    )
    assert private.require_pre_live_admission("b" * 40) == created


def test_public_result_preserves_strict_mismatch_as_local_demo_warning() -> None:
    config = load_local_demo_config(ROOT / LOCAL_DEMO_CONFIG_RELATIVE)
    terminal = {
        "verdict": "LOCAL_DEMO_E2E_PASSED_READY_FOR_REVIEW",
        "implementation_commit": "a" * 40,
        "result_head": "a" * 40,
        "model": "gpt-5.4-mini-2026-03-17",
        "source_availability": {
            "METRICS": "AVAILABLE",
            "LOGS": "AVAILABLE",
            "TRACES": "AVAILABLE",
        },
        "source_counts": {"METRICS": 6, "LOGS": 1, "TRACES": 4},
        "invalid_refs": 0,
        "strict_expected_root_service": "payment",
        "strict_expected_fault_class": "APPLICATION",
        "predicted_root_service": "payment",
        "predicted_fault_class": "PROPAGATION",
        "strict_audit_pass": False,
        "strict_reason_codes": ["FAULT_CLASS_MISMATCH"],
        "local_demo_gate": True,
        "local_demo_warning_codes": ["FAULT_CLASS_MISMATCH_WARNING"],
        "local_demo_root_match": True,
        "local_demo_fault_class_match": False,
        "local_demo_evidence_valid": True,
        "local_demo_source_coverage_valid": True,
        "local_demo_single_call_valid": True,
        "local_demo_context_binding_valid": True,
        "standing_authorization_valid": True,
        "authorization_source": "USER_EXPLICIT_STANDING_AUTHORIZATION_IN_GOAL",
        "command_execution": "CODEX_DELEGATED_EXECUTION",
        "execution_boundary": "LOCAL_DOCKER_ONLY",
        "user_manually_typed_each_runtime_command": False,
        "approval_mode": "LOCAL_DEMO_STANDING_PREAUTHORIZATION",
        "codex_autonomous_self_approval": False,
        "fault_injections": 1,
        "fault_impact_passed": True,
        "provider_calls": 2,
        "model_calls": 1,
        "a0_context_builder_calls": 1,
        "semantic_model_calls": 1,
        "specialist_calls": 0,
        "fusion_calls": 0,
        "provider_attempts": 1,
        "baseline_windows": 2,
        "recovery_window_count": 2,
        "plan_action": "RESTORE_FROZEN_SERVICE_CONFIGURATION",
        "policy_verdict": "ALLOW",
        "forward_mutations": 1,
        "rollback_mutations": 0,
        "recovery_verification_passed": True,
        "accepted_live_run_sealed": True,
        "local_demo_attempt_count": 1,
        "cleanup": {
            "verdict": "CLEAN",
            "baseline_restored": True,
            "owned_containers": 0,
            "owned_networks": 0,
            "owned_volumes": 0,
            "non_owned_resources_changed": False,
        },
    }

    result = build_public_result(config, terminal)

    assert result["strict_audit_pass"] is False
    assert result["local_demo_gate"] is True
    assert result["local_demo_warnings"] == ["FAULT_CLASS_MISMATCH_WARNING"]
    assert result["scenario_id"] == config.sandbox.scenario.scenario_id
    assert result["attempt_count"] == 1
    assert result["fault_impact_gate"] is True
    assert result["root_match"] is True
    assert result["fault_class_match"] is False
    assert result["evidence_valid"] is True
    assert result["plan_action"] == "RESTORE_FROZEN_SERVICE_CONFIGURATION"
    assert result["authorization_mode"] == "LOCAL_DEMO_STANDING_PREAUTHORIZATION"
    assert result["execution_boundary"] == "LOCAL_DOCKER_ONLY"
    assert result["user_manually_typed_each_runtime_command"] is False
    assert result["policy_verdict"] == "ALLOW"
    assert result["baseline_restored"] is True


def test_success_terminal_rejects_incomplete_positive_truth() -> None:
    config = load_local_demo_config(ROOT / LOCAL_DEMO_CONFIG_RELATIVE)
    terminal = {
        "verdict": "LOCAL_DEMO_E2E_PASSED_READY_FOR_REVIEW",
        "fault_injections": 1,
    }

    with pytest.raises(ValueError, match="does not recompute"):
        build_public_result(config, terminal)
