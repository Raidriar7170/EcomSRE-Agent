from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from ecomsre_live_sandbox.contracts import CleanupResult
from ecomsre_live_sandbox.e2e_contracts import (
    E2EPrivateRoots,
    create_approval_request,
    load_e2e_config,
    record_human_approval,
    scan_public_e2e_payload,
)
from ecomsre_live_sandbox import e2e_v1
from ecomsre_live_sandbox.e2e_v1 import (
    _public_result,
    _require_invocation_a_success,
    scenario_lock_manifest,
    verify_public_result,
)


CONFIG = Path("config/live-fault-a0-controlled-remediation-e2e-v1")


class PreflightEnvironment:
    def __init__(self, **_: object) -> None:
        pass

    def verify_local_docker(self) -> dict[str, str]:
        return {"endpoint": "unix:///private/docker.sock"}

    def verify_upstream(self) -> None:
        pass

    def resolve(self) -> tuple[object, dict[str, object]]:
        return SimpleNamespace(endpoints=SimpleNamespace()), {}

    def verify_owned_resources(self, **_: object) -> dict[str, int]:
        return {"container": 0, "network": 0, "volume": 0}


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
    tracked_files = lock["tracked_files"]
    assert isinstance(tracked_files, dict)
    assert {
        "authority.json",
        "projection.json",
        "reporting.json",
        "e2e_contracts.py",
        "control.py",
        "e2e_telemetry.py",
        "e2e_v1.py",
        "test_e2e_projection.py",
        "test_e2e_v1.py",
        "test_live_sandbox.py",
        "e2e_cli.py",
        "v3_environment.json",
        "v1_scenario.json",
        "v1_budget.json",
    }.issubset(set(tracked_files))


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


def test_invocation_b_seals_a_terminal_before_a_provider_preflight_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = load_e2e_config(CONFIG)
    roots = E2EPrivateRoots(tmp_path / "private")
    lock = {"scenario": config.sandbox.scenario.scenario_id, "implementation_commit": "a" * 40}
    request = create_approval_request(config, scenario_lock=lock)
    approval = record_human_approval(
        request,
        approver="Minghong Sun",
        phrase=f"APPROVE {request.scenario_id} {request.plan_template_sha256}",
        now=request.requested_at,
        destination=tmp_path / "approval.json",
    )
    monkeypatch.setattr(e2e_v1, "_verify_scenario_lock", lambda *_, **__: lock)
    monkeypatch.setattr(e2e_v1, "_require_invocation_a_success", lambda *_: None)
    monkeypatch.setattr(e2e_v1, "_load_approval", lambda *_: (request, approval))
    monkeypatch.setattr(e2e_v1, "SandboxEnvironment", PreflightEnvironment)
    def write_public_after_private(*_: object) -> tuple[str, str, str]:
        assert (roots.reports / "invocation-b-projection-source.json").exists()
        assert not (roots.reports / "invocation-b.json").exists()
        assert not (roots.invocation_b / "terminal.json").exists()
        return ("result.json", "result.md", "brief.md")

    monkeypatch.setattr(e2e_v1, "_write_public_outputs", write_public_after_private)

    def unavailable_provider(*_: object) -> object:
        raise RuntimeError("provider is unavailable")

    monkeypatch.setattr(e2e_v1, "_provider", unavailable_provider)

    terminal = e2e_v1.run_invocation_b(config, roots)

    assert terminal["verdict"] == "BLOCKED_PROVIDER_PREFLIGHT"
    assert terminal["provider_calls"] == 0
    assert terminal["fault_injections"] == 0
    assert terminal["forward_mutations"] == 0
    assert terminal["failure_type"] == "RuntimeError"
    stored = json.loads((roots.invocation_b / "terminal.json").read_text(encoding="utf-8"))
    assert stored["verdict"] == terminal["verdict"]
    assert stored["provider_calls"] == terminal["provider_calls"]
    assert (roots.invocation_b / "started.json").exists()

    with pytest.raises(RuntimeError, match="already consumed"):
        e2e_v1.run_invocation_b(config, roots)


def test_invocation_b_terminal_blocks_when_public_projection_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = load_e2e_config(CONFIG)
    roots = E2EPrivateRoots(tmp_path / "private")
    lock = {"scenario": config.sandbox.scenario.scenario_id, "implementation_commit": "a" * 40}
    request = create_approval_request(config, scenario_lock=lock)
    approval = record_human_approval(
        request,
        approver="Minghong Sun",
        phrase=f"APPROVE {request.scenario_id} {request.plan_template_sha256}",
        now=request.requested_at,
        destination=tmp_path / "approval.json",
    )
    monkeypatch.setattr(e2e_v1, "_verify_scenario_lock", lambda *_, **__: lock)
    monkeypatch.setattr(e2e_v1, "_require_invocation_a_success", lambda *_: None)
    monkeypatch.setattr(e2e_v1, "_load_approval", lambda *_: (request, approval))
    monkeypatch.setattr(e2e_v1, "SandboxEnvironment", PreflightEnvironment)
    monkeypatch.setattr(e2e_v1, "_provider", lambda *_: (_ for _ in ()).throw(RuntimeError("provider offline")))
    monkeypatch.setattr(
        e2e_v1,
        "_write_public_outputs",
        lambda *_: (_ for _ in ()).throw(RuntimeError("public output write failed")),
    )

    terminal = e2e_v1.run_invocation_b(config, roots)

    assert terminal["verdict"] == "BLOCKED"
    assert terminal["public_projection_status"] == "PARTIAL_OR_NONE"
    stored = json.loads((roots.invocation_b / "terminal.json").read_text(encoding="utf-8"))
    assert stored["verdict"] == "BLOCKED"
    assert stored["failure_classification"] == "PUBLIC_REPORT_FAILURE"


def test_invalid_approval_is_rejected_before_provider_preflight(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = load_e2e_config(CONFIG)
    roots = E2EPrivateRoots(tmp_path / "private")
    lock = {"scenario": config.sandbox.scenario.scenario_id, "implementation_commit": "a" * 40}
    request = create_approval_request(config, scenario_lock=lock)
    approval = record_human_approval(
        request,
        approver="Minghong Sun",
        phrase=f"APPROVE {request.scenario_id} {request.plan_template_sha256}",
        now=request.requested_at,
        destination=tmp_path / "approval.json",
    )
    altered_request = request.model_copy(update={"scenario_lock_sha256": "0" * 64})
    monkeypatch.setattr(e2e_v1, "_verify_scenario_lock", lambda *_, **__: lock)
    monkeypatch.setattr(e2e_v1, "_require_invocation_a_success", lambda *_: None)
    monkeypatch.setattr(e2e_v1, "_load_approval", lambda *_: (altered_request, approval))
    monkeypatch.setattr(
        e2e_v1,
        "_provider",
        lambda *_: pytest.fail("Provider must not be reached when approval binding is invalid"),
    )
    monkeypatch.setattr(e2e_v1, "SandboxEnvironment", PreflightEnvironment)

    with pytest.raises(RuntimeError, match="exactly bound"):
        e2e_v1.run_invocation_b(config, roots)

    assert not (roots.invocation_b / "started.json").exists()


def test_invocation_b_seals_cleanup_failure_after_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = load_e2e_config(CONFIG)
    roots = E2EPrivateRoots(tmp_path / "private")
    lock = {"scenario": config.sandbox.scenario.scenario_id, "implementation_commit": "a" * 40}
    request = create_approval_request(config, scenario_lock=lock)
    approval = record_human_approval(
        request,
        approver="Minghong Sun",
        phrase=f"APPROVE {request.scenario_id} {request.plan_template_sha256}",
        now=request.requested_at,
        destination=tmp_path / "approval.json",
    )

    class Provider:
        calls = 0
        usage_known = True
        last_usage_tokens = 1
        last_request_sha256 = "0" * 64

        def diagnose(self, _: object) -> dict[str, str]:
            self.calls += 1
            return {"preflight": "only"}

    class Controller:
        def read_current(self) -> object:
            return SimpleNamespace(document_sha256=config.sandbox.scenario.baseline_document_sha256)

    class Environment:
        def __init__(self, **_: object) -> None:
            pass

        def verify_local_docker(self) -> dict[str, str]:
            return {"endpoint": "unix:///private/docker.sock"}

        def verify_upstream(self) -> None:
            pass

        def resolve(self) -> tuple[object, dict[str, object]]:
            return SimpleNamespace(endpoints=SimpleNamespace()), {}

        def verify_owned_resources(self, **_: object) -> dict[str, int]:
            return {"container": 0, "network": 0, "volume": 0}

        def verify_cached_images(self, *_: object) -> None:
            pass

        def start(self) -> None:
            pass

        def wait_healthy(self) -> None:
            raise RuntimeError("health gate failed")

        def cleanup(self, **_: object) -> object:
            raise RuntimeError("cleanup failed")

    monkeypatch.setattr(e2e_v1, "_verify_scenario_lock", lambda *_, **__: lock)
    monkeypatch.setattr(e2e_v1, "_require_invocation_a_success", lambda *_: None)
    monkeypatch.setattr(e2e_v1, "_load_approval", lambda *_: (request, approval))
    monkeypatch.setattr(e2e_v1, "_provider", lambda *_: Provider())
    monkeypatch.setattr(e2e_v1, "SandboxEnvironment", Environment)
    monkeypatch.setattr(e2e_v1, "_make_controller", lambda *_, **__: Controller())
    monkeypatch.setattr(e2e_v1.time, "sleep", lambda *_: None)
    monkeypatch.setattr(e2e_v1, "_write_public_outputs", lambda *_: ("result.json", "result.md", "brief.md"))

    terminal = e2e_v1.run_invocation_b(config, roots)

    assert terminal["verdict"] == "BLOCKED_CLEANUP_INCOMPLETE"
    assert terminal["provider_calls"] == 1
    assert terminal["fault_injections"] == 0
    assert terminal["cleanup_verdict"] == "BLOCKED"
    assert (roots.invocation_b / "started.json").exists()
    assert (roots.invocation_b / "terminal.json").exists()

    with pytest.raises(RuntimeError, match="already consumed"):
        e2e_v1.run_invocation_b(config, roots)


def test_invocation_a_cleans_up_when_start_raises_after_creating_resources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = load_e2e_config(CONFIG)
    roots = E2EPrivateRoots(tmp_path / "private")
    cleanup_calls: list[bool] = []

    class Environment:
        def __init__(self, **_: object) -> None:
            pass

        def verify_local_docker(self) -> None:
            pass

        def verify_upstream(self) -> None:
            pass

        def resolve(self) -> tuple[object, dict[str, object]]:
            return SimpleNamespace(endpoints=SimpleNamespace()), {}

        def verify_cached_images(self, *_: object) -> None:
            pass

        def start(self) -> None:
            raise RuntimeError("compose up created resources before failing")

        def cleanup(self, *, baseline_restored: bool) -> CleanupResult:
            cleanup_calls.append(baseline_restored)
            return CleanupResult(
                baseline_restored=True,
                owned_containers=0,
                owned_networks=0,
                owned_volumes=0,
                non_owned_resources_changed=False,
                verdict="CLEAN",
            )

    class Controller:
        def read_current(self) -> object:
            return SimpleNamespace(document_sha256=config.sandbox.scenario.baseline_document_sha256)

    def fake_git(_: Path, *arguments: str) -> str:
        if arguments == ("branch", "--show-current"):
            return config.authority.branch
        if arguments == ("status", "--porcelain=v1"):
            return ""
        return "a" * 40

    monkeypatch.setattr(e2e_v1, "_git", fake_git)
    monkeypatch.setattr(e2e_v1, "SandboxEnvironment", Environment)
    monkeypatch.setattr(e2e_v1, "_make_controller", lambda *_, **__: Controller())

    terminal = e2e_v1.run_invocation_a(config, roots)

    assert cleanup_calls == [True]
    assert terminal["cleanup_verdict"] == "CLEAN"
    assert terminal["verdict"] == "BLOCKED"
    assert (roots.invocation_a / "terminal.json").exists()
    assert not (roots.control / "approval-request.json").exists()


def test_invocation_b_cleans_up_when_start_raises_after_creating_resources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = load_e2e_config(CONFIG)
    roots = E2EPrivateRoots(tmp_path / "private")
    lock = {"scenario": config.sandbox.scenario.scenario_id, "implementation_commit": "a" * 40}
    request = create_approval_request(config, scenario_lock=lock)
    approval = record_human_approval(
        request,
        approver="Minghong Sun",
        phrase=f"APPROVE {request.scenario_id} {request.plan_template_sha256}",
        now=request.requested_at,
        destination=tmp_path / "approval.json",
    )
    cleanup_calls: list[bool] = []

    class Provider:
        calls = 0
        usage_known = True
        last_usage_tokens = 1
        last_request_sha256 = "0" * 64

        def diagnose(self, _: object) -> dict[str, str]:
            self.calls += 1
            return {"preflight": "only"}

    class Controller:
        def read_current(self) -> object:
            return SimpleNamespace(document_sha256=config.sandbox.scenario.baseline_document_sha256)

    class Environment(PreflightEnvironment):
        def verify_cached_images(self, *_: object) -> None:
            pass

        def start(self) -> None:
            raise RuntimeError("compose up created resources before failing")

        def cleanup(self, *, baseline_restored: bool) -> CleanupResult:
            cleanup_calls.append(baseline_restored)
            return CleanupResult(
                baseline_restored=True,
                owned_containers=0,
                owned_networks=0,
                owned_volumes=0,
                non_owned_resources_changed=False,
                verdict="CLEAN",
            )

    monkeypatch.setattr(e2e_v1, "_verify_scenario_lock", lambda *_, **__: lock)
    monkeypatch.setattr(e2e_v1, "_require_invocation_a_success", lambda *_: None)
    monkeypatch.setattr(e2e_v1, "_load_approval", lambda *_: (request, approval))
    monkeypatch.setattr(e2e_v1, "_provider", lambda *_: Provider())
    monkeypatch.setattr(e2e_v1, "SandboxEnvironment", Environment)
    monkeypatch.setattr(e2e_v1, "_make_controller", lambda *_, **__: Controller())
    monkeypatch.setattr(e2e_v1.time, "sleep", lambda *_: None)
    monkeypatch.setattr(e2e_v1, "_write_public_outputs", lambda *_: ("result.json", "result.md", "brief.md"))

    terminal = e2e_v1.run_invocation_b(config, roots)

    assert cleanup_calls == [True]
    assert terminal["cleanup_verdict"] == "CLEAN"
    assert terminal["provider_calls"] == 1
    assert (roots.invocation_b / "terminal.json").exists()


def test_public_verifier_rejects_forged_success_aggregates() -> None:
    config = load_e2e_config(CONFIG)
    terminal = {
        "verdict": config.authority.invocation_b_success,
        "source_counts": {"METRICS": 3, "LOGS": 4, "TRACES": 5},
        "source_availability": {"METRICS": "AVAILABLE", "LOGS": "AVAILABLE", "TRACES": "AVAILABLE"},
        "provider_calls": 2,
        "model_calls": 1,
        "fault_injections": 1,
        "forward_mutations": 1,
        "rollback_mutations": 0,
        "cleanup_verdict": "CLEAN",
        "cleanup": {
            "baseline_restored": True,
            "owned_containers": 0,
            "owned_networks": 0,
            "owned_volumes": 0,
            "non_owned_resources_changed": False,
            "verdict": "CLEAN",
        },
        "evidence_resolver_valid": True,
        "visible_service_count": 3,
        "context_safety_passed": True,
        "fault_impact_passed": True,
        "provider_preflight_passed": True,
        "approval_valid": True,
        "plan_template_exact": True,
        "diagnosis_gate": True,
        "policy_verdict": "ALLOW",
        "recovery_verification_passed": True,
        "implementation_commit": "a" * 40,
    }
    public = _public_result(config, terminal)
    assert verify_public_result(config, public, sealed_terminal=terminal)

    for key, value in (
        ("source_counts", {"METRICS": 0, "LOGS": 4, "TRACES": 5}),
        ("diagnosis_gate", False),
        ("cleanup", {**terminal["cleanup"], "owned_containers": 1}),
    ):
        forged = dict(public)
        forged[key] = value
        assert not verify_public_result(config, forged, sealed_terminal=terminal)


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


def test_human_approval_rejects_a_whitespace_approver(tmp_path: Path) -> None:
    config = load_e2e_config(CONFIG)
    request = create_approval_request(config, scenario_lock={"scenario": config.sandbox.scenario.scenario_id})
    destination = tmp_path / "human-approval.json"

    with pytest.raises(ValueError, match="approver is blank"):
        record_human_approval(
            request,
            approver="   ",
            phrase=f"APPROVE {request.scenario_id} {request.plan_template_sha256}",
            now=request.requested_at,
            destination=destination,
        )

    assert not destination.exists()


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
