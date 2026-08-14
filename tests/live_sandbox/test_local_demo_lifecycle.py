from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path

import pytest

from ecomsre_live_sandbox.local_demo_contracts import (
    LOCAL_DEMO_CONFIG_RELATIVE,
    LocalDemoPrivateRoot,
    load_local_demo_config,
    validate_provider_env,
)
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
        "local_demo_evidence_valid": True,
        "local_demo_source_coverage_valid": True,
        "local_demo_single_call_valid": True,
        "local_demo_context_binding_valid": True,
        "standing_authorization_valid": True,
        "codex_autonomous_self_approval": False,
        "fault_injections": 1,
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
