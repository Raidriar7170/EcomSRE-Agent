from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import pytest

from ecomsre_live_sandbox.e2e_contracts import (
    E2EPrivateRoots,
    create_approval_request,
    load_e2e_config,
    record_human_approval,
    scan_public_e2e_payload,
)
from ecomsre_live_sandbox.e2e_v1 import scenario_lock_manifest
from ecomsre_live_sandbox.e2e_v1 import _require_invocation_a_success


CONFIG = Path("config/live-fault-a0-controlled-remediation-e2e-v1")


def test_successor_authority_binds_the_frozen_v3_and_a0_surfaces() -> None:
    config = load_e2e_config(CONFIG)

    assert config.authority.version == "live-fault-a0-controlled-remediation-e2e-v1"
    assert config.authority.predecessor_v3_semantic_sha256 == (
        "ff299ed1ed0f7433702991fecfb1290e3439ed228b90796860c7dfd42cd4917c"
    )
    assert config.authority.predecessor_v3_tracked_sha256 == (
        "772e4b74eba373a8af2d51ceb5c503ec8e692329eaa47d93c244392cff22cac5"
    )
    assert config.sandbox.scenario.scenario_id == "37f142fc-9cde-4839-8184-88f2288ceced"
    assert config.sandbox.policy.approval_ttl_hours == 168
    assert config.sandbox.diagnosis.architecture == "A0"
    assert config.sandbox.diagnosis.model == "gpt-5.4-mini-2026-03-17"
    assert config.authority.invocation_a_terminal == "LIVE_E2E_HUMAN_PREAUTHORIZATION_REQUIRED"


def test_scenario_lock_binds_only_successor_surfaces() -> None:
    config = load_e2e_config(CONFIG)
    lock = scenario_lock_manifest(config)

    assert lock["implementation_branch"] == config.authority.branch
    assert lock["source_v3_tracked_sha256"] == config.authority.predecessor_v3_tracked_sha256
    assert set(lock["tracked_files"]) == {
        "authority.json",
        "projection.json",
        "reporting.json",
        "e2e_contracts.py",
        "e2e_telemetry.py",
        "e2e_v1.py",
        "test_e2e_projection.py",
        "test_e2e_v1.py",
    }


def test_successor_runtime_does_not_import_the_frozen_v1_runtime_modules() -> None:
    source = Path("src/ecomsre_live_sandbox/e2e_v1.py").read_text(encoding="utf-8")
    assert "ecomsre_live_sandbox.workflow" not in source
    assert "ecomsre_live_sandbox.telemetry" not in source


def test_invocation_b_requires_a_clean_no_fault_invocation_a_terminal(tmp_path: Path) -> None:
    config = load_e2e_config(CONFIG)
    roots = E2EPrivateRoots(tmp_path / "private")
    roots.prepare()
    terminal = {
        "verdict": config.authority.invocation_a_terminal,
        "cleanup_verdict": "CLEAN",
        "fault_injections": 0,
        "provider_calls": 0,
        "model_calls": 0,
        "forward_mutations": 0,
        "rollback_mutations": 0,
    }
    terminal_path = roots.invocation_a / "terminal.json"
    terminal_path.write_text(json.dumps(terminal), encoding="utf-8")
    terminal_path.chmod(0o600)
    _require_invocation_a_success(config, roots)

    terminal["cleanup_verdict"] = "BLOCKED"
    terminal_path.write_text(json.dumps(terminal), encoding="utf-8")
    with pytest.raises(RuntimeError, match="clean human-authorization"):
        _require_invocation_a_success(config, roots)


def test_human_approval_is_create_once_and_phrase_bound(tmp_path: Path) -> None:
    config = load_e2e_config(CONFIG)
    roots = E2EPrivateRoots(tmp_path / "private")
    roots.prepare()
    now = datetime(2026, 8, 11, 12, tzinfo=timezone.utc)
    scenario_lock = {"scenario": config.sandbox.scenario.scenario_id, "version": config.authority.version}
    lock_path = roots.control / "scenario-lock.json"
    lock_path.write_text(json.dumps(scenario_lock), encoding="utf-8")
    lock_path.chmod(0o600)

    request = create_approval_request(config, scenario_lock=scenario_lock, now=now)
    request_path = roots.control / "approval-request.json"
    request_path.write_text(request.model_dump_json(), encoding="utf-8")
    request_path.chmod(0o600)
    expected_phrase = f"APPROVE {request.scenario_id} {request.plan_template_sha256}"

    with pytest.raises(ValueError, match="phrase"):
        record_human_approval(
            request,
            approver="Minghong Sun",
            phrase="APPROVE something else",
            now=now + timedelta(minutes=1),
            destination=roots.control / "human-approval.json",
        )

    record = record_human_approval(
        request,
        approver="Minghong Sun",
        phrase=expected_phrase,
        now=now + timedelta(minutes=1),
        destination=roots.control / "human-approval.json",
    )
    assert record.mode == "HUMAN"
    assert record.approval_request_id == request.approval_request_id
    assert (roots.control / "human-approval.json").stat().st_mode & 0o777 == 0o600

    with pytest.raises(FileExistsError, match="create-once"):
        record_human_approval(
            request,
            approver="Another Human",
            phrase=expected_phrase,
            now=now + timedelta(minutes=2),
            destination=roots.control / "human-approval.json",
        )


def test_public_projection_scan_rejects_private_and_control_surfaces() -> None:
    safe = {
        "verdict": "LIVE_E2E_HUMAN_PREAUTHORIZATION_REQUIRED",
        "claim_boundary": ["LIVE_LOCAL_SANDBOX_DEMO", "NOT_PRODUCTION"],
        "source_counts": {"METRICS": 4, "LOGS": 8, "TRACES": 6},
    }
    assert scan_public_e2e_payload(safe) == ()

    for leaked in (
        {"private_path": "/Users/name/.ecomsre/private/run.json"},
        {"provider_response": "raw response"},
        {"control_key": "paymentFailure.defaultVariant"},
        {"approval_phrase": "APPROVE 37f142fc-9cde-4839-8184-88f2288ceced hash"},
    ):
        assert scan_public_e2e_payload(leaked)
