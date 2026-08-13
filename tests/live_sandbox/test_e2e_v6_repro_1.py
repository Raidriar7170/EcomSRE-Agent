from __future__ import annotations

import json
import stat
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from ecomsre_live_sandbox.contracts import SLIWindow, canonical_sha256, file_sha256
from ecomsre_live_sandbox.e2e_v6_contracts import (
    E2EV6PrivateRoots,
    load_e2e_v6_config,
)
from ecomsre_live_sandbox.e2e_v6_repro_1_contracts import (
    E2EV6Repro1PrivateRoots,
    bind_repro_1_lifecycle,
    load_e2e_v6_repro_1_config,
)
from ecomsre_live_sandbox.e2e_v6_repro_1 import (
    _allocate_pre_fault_attempt,
    _complete_live_attempt,
    _prepare_pre_fault_repair,
    _seal_accepted_live_run,
    _write_public_outputs_repro_1,
)
from ecomsre_live_sandbox.invocation_b_verdicts import (
    invocation_b_verdict_policy_sha256,
)


CONFIG = Path("config/live-fault-a0-controlled-remediation-e2e-v6-repro-1")
V6_CONFIG = Path("config/live-fault-a0-controlled-remediation-e2e-v6")


def test_repro_1_authority_keeps_v6_software_and_binds_new_generation() -> None:
    config = load_e2e_v6_repro_1_config(CONFIG)
    base = load_e2e_v6_config(V6_CONFIG)
    authority = config.authority

    assert authority.software_version == base.authority.version
    assert authority.version == "live-fault-a0-controlled-remediation-e2e-v6"
    assert authority.runtime_policy_version == "V6"
    assert authority.run_generation == "V6_REPRO_1"
    assert authority.branch == (
        "feature/live-fault-a0-controlled-remediation-e2e-v6-repro-1"
    )
    assert authority.predecessor_pr == 37
    assert authority.predecessor_result_head == (
        "ef42328dfa65eab8f8b1dfda934fa5ab5bd0c41c"
    )
    assert authority.predecessor_head == authority.predecessor_result_head
    assert authority.predecessor_terminal == (
        "BLOCKED_E2E_V6_UNCLASSIFIED_RUNTIME_FAILURE"
    )
    assert authority.predecessor_sealed_terminal_sha256 == (
        "87bf5a3ed55cc14e93fabcfb2426cad8eb1310eaca19430c641abf66ade0b209"
    )
    assert authority.predecessor_public_semantic_sha256 == (
        "c61cb1c6323d45569c1aa771fcb61ef72449db6e3c64d0e48d4a61985c51fd13"
    )
    assert authority.predecessor_final_evidence_sha256 == (
        "a3d69e51dd889f77638fa76c1a630dd987c33cc977851e056f8a26c4957c747b"
    )
    assert authority.telemetry_authority_pr == 31
    assert authority.telemetry_authority_head == base.authority.telemetry_authority_head
    assert authority.a0_prompt_sha256 == base.authority.a0_prompt_sha256
    assert authority.a0_output_schema_sha256 == base.authority.a0_output_schema_sha256
    assert authority.a0_model == base.authority.a0_model
    assert authority.no_fault_readiness_policy_sha256 == (
        base.authority.no_fault_readiness_policy_sha256
    )
    assert authority.fault_projection_policy_sha256 == (
        base.authority.fault_projection_policy_sha256
    )
    assert authority.invocation_b_verdict_policy_id == "v6"
    assert authority.versioned_verdict_policy_sha256 == (
        invocation_b_verdict_policy_sha256("v6")
    )
    assert authority.accepted_fault_time_runs == 1
    assert authority.maximum_accepted_complete_live_runs == 1
    assert authority.maximum_forward_mutations == 1
    assert authority.maximum_rollbacks == 1
    assert authority.plan_action == "RESTORE_FROZEN_SERVICE_CONFIGURATION"
    assert authority.invocation_b_success == base.authority.invocation_b_success


def test_repro_1_reporting_paths_do_not_replace_original_v6_outputs() -> None:
    config = load_e2e_v6_repro_1_config(CONFIG)

    assert config.reporting.public_result_json == (
        "docs/results/live-fault-a0-controlled-remediation-e2e-v6-repro-1.json"
    )
    assert config.reporting.public_result_markdown == (
        "docs/results/live-fault-a0-controlled-remediation-e2e-v6-repro-1.md"
    )
    assert config.reporting.public_human_brief == (
        "docs/results/"
        "live-fault-a0-controlled-remediation-e2e-v6-repro-1-human-brief.md"
    )
    assert file_sha256(
        Path("docs/results/live-fault-a0-controlled-remediation-e2e-v6.json")
    ) == "f1b61e2c9e543271c232b603d14310834090e535638b348b6be7eae09bfb8e63"
    assert file_sha256(
        Path("docs/results/live-fault-a0-controlled-remediation-e2e-v6.md")
    ) == "635b5fa7a05731b0fc6fd2f2bc30dffb89928590d6ba251509af4e544f2b847b"
    assert file_sha256(
        Path(
            "docs/results/"
            "live-fault-a0-controlled-remediation-e2e-v6-human-brief.md"
        )
    ) == "075d7d14223a95c1f50a55b2f866cb43c33d1c2e26770ad97252df43f364a775"


def test_repro_1_private_lifecycle_is_bound_and_original_v6_is_not_reusable(
    tmp_path: Path,
) -> None:
    config = load_e2e_v6_repro_1_config(CONFIG)
    roots = E2EV6Repro1PrivateRoots(tmp_path / "repro-1")

    bind_repro_1_lifecycle(config, roots)

    lifecycle = json.loads(
        (roots.control / "private-root-lifecycle.json").read_text(encoding="utf-8")
    )
    bound_authority = json.loads(
        (roots.control / "authority.json").read_text(encoding="utf-8")
    )
    assert lifecycle["software_version"] == config.authority.software_version
    assert lifecycle["runtime_policy_version"] == "V6"
    assert lifecycle["run_generation"] == "V6_REPRO_1"
    assert lifecycle["branch"] == config.authority.branch
    assert bound_authority["run_generation"] == "V6_REPRO_1"
    assert bound_authority["predecessor_result_head"] == (
        "ef42328dfa65eab8f8b1dfda934fa5ab5bd0c41c"
    )
    roots.verify()

    original_config = load_e2e_v6_config(V6_CONFIG)
    original_roots = E2EV6PrivateRoots(tmp_path / "original-v6")
    original_roots.bind_lifecycle(
        original_config.authority,
        repository_root=original_config.repository_root,
    )
    with pytest.raises(ValueError, match="private lifecycle binding differs"):
        bind_repro_1_lifecycle(config, original_roots)


def _baseline_window(index: int) -> SLIWindow:
    started = datetime(2026, 8, 13, 4, index, tzinfo=timezone.utc)
    return SLIWindow(
        phase="BASELINE",
        started_at=started,
        ended_at=started + timedelta(seconds=30),
        request_count=200.0,
        error_count=1.0,
        error_rate=0.005,
        p95_latency_ms=20.0,
        runtime_health=1.0,
        sample_count=3,
    )


def test_repro_1_attempt_is_scoped_and_identical_pre_fault_retry_is_rejected(
    tmp_path: Path,
) -> None:
    config = load_e2e_v6_repro_1_config(CONFIG)
    roots = E2EV6Repro1PrivateRoots(tmp_path / "repro-1")
    bind_repro_1_lifecycle(config, roots)
    for name, value in (
        ("scenario-lock.json", {"lock": "a"}),
        ("human-approval.json", {"approval": "a"}),
    ):
        from ecomsre_live_sandbox.contracts import write_private_json

        write_private_json(roots.control / name, value, create_once=True)

    attempt = _allocate_pre_fault_attempt(
        config,
        roots,
        implementation_commit="a" * 40,
        runtime_config_aggregate="b" * 64,
    )

    assert attempt == roots.live_attempt(1)
    assert roots.invocation_b == attempt
    assert roots.provider == attempt / "provider"
    assert roots.telemetry == attempt / "telemetry"
    assert roots.journal == attempt / "journal"
    assert roots.runtime == attempt / "runtime"
    terminal = {
        "implementation_commit": "a" * 40,
        "runtime_config_aggregate_sha256": "b" * 64,
        "fault_injections": 0,
        "model_calls": 0,
        "forward_mutations": 0,
        "rollback_mutations": 0,
        "cleanup_verdict": "NOT_REQUIRED",
        "verdict": "BLOCKED_PROVIDER_PREFLIGHT",
    }
    from ecomsre_live_sandbox.contracts import write_private_json

    write_private_json(attempt / "terminal.json", terminal, create_once=True)
    _complete_live_attempt(config, roots, terminal)

    with pytest.raises(RuntimeError, match="identical pre-fault retry"):
        _allocate_pre_fault_attempt(
            config,
            roots,
            implementation_commit="a" * 40,
            runtime_config_aggregate="b" * 64,
        )


def test_repro_1_accepted_pointer_is_create_once_and_precedes_fault(
    tmp_path: Path,
) -> None:
    config = load_e2e_v6_repro_1_config(CONFIG)
    roots = E2EV6Repro1PrivateRoots(tmp_path / "repro-1")
    bind_repro_1_lifecycle(config, roots)
    from ecomsre_live_sandbox.contracts import write_private_json

    for name, value in (
        ("scenario-lock.json", {"lock": "a"}),
        ("human-approval.json", {"approval": "a"}),
    ):
        write_private_json(roots.control / name, value, create_once=True)
    attempt = _allocate_pre_fault_attempt(
        config,
        roots,
        implementation_commit="a" * 40,
        runtime_config_aggregate="b" * 64,
    )
    terminal: dict[str, object] = {
        "implementation_commit": "a" * 40,
        "provider_calls": 1,
        "provider_preflight_passed": True,
        "compose_start_requested": True,
        "compose_start_returned": True,
        "baseline_windows": 2,
        "fault_injections": 0,
        "model_calls": 0,
        "forward_mutations": 0,
        "rollback_mutations": 0,
    }
    baseline = (_baseline_window(1), _baseline_window(2))

    pointer_sha = _seal_accepted_live_run(config, roots, terminal, baseline)

    path = roots.control / "accepted-live-run.json"
    pointer = json.loads(path.read_text(encoding="utf-8"))
    assert roots.invocation_b == attempt
    assert pointer["run_generation"] == "V6_REPRO_1"
    assert pointer["attempt_id"] == "attempt-0001"
    assert pointer["implementation_commit"] == "a" * 40
    assert pointer["baseline_window_sha256"] == [
        canonical_sha256(item) for item in baseline
    ]
    assert pointer["pre_fault_counters"] == {
        "fault_injections": 0,
        "model_calls": 0,
        "forward_mutations": 0,
        "rollback_mutations": 0,
    }
    assert pointer_sha == file_sha256(path)
    assert terminal["accepted_live_run_sha256"] == pointer_sha
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    with pytest.raises(RuntimeError, match="accepted fault-time run is already sealed"):
        _seal_accepted_live_run(config, roots, terminal, baseline)


def test_repro_1_pre_fault_terminal_cannot_write_public_outputs(
    tmp_path: Path,
) -> None:
    config = load_e2e_v6_repro_1_config(CONFIG)
    isolated = replace(config, repository_root=tmp_path / "repository")
    roots = E2EV6Repro1PrivateRoots(tmp_path / "private-repro-1")
    bind_repro_1_lifecycle(config, roots)

    assert _write_public_outputs_repro_1(
        isolated,
        {
            "fault_injections": 0,
            "accepted_live_run_sealed": False,
            "accepted_live_run_sha256": None,
        },
        roots=roots,
    ) == ()
    assert not any(
        (isolated.repository_root / relative).exists()
        for relative in (
            isolated.reporting.public_result_json,
            isolated.reporting.public_result_markdown,
            isolated.reporting.public_human_brief,
        )
    )


def test_repro_1_changed_pre_fault_repair_preserves_and_rotates_authority(
    tmp_path: Path,
) -> None:
    config = load_e2e_v6_repro_1_config(CONFIG)
    roots = E2EV6Repro1PrivateRoots(tmp_path / "repro-1")
    bind_repro_1_lifecycle(config, roots)
    from ecomsre_live_sandbox.contracts import write_private_json

    control_payloads = {
        "latest-development-pass-lock.json": {"pass": "old"},
        "exact-head-ci.json": {"ci": "old"},
        "pre-live-review.json": {"review": "old"},
        "canonical-active.json": {"canonical": "old"},
        "canonical-accepted.json": {"accepted": "old"},
        "scenario-lock.json": {"lock": "old"},
        "plan-template.json": {"plan": "old"},
        "approval-request.json": {"request": "old"},
        "human-approval.json": {"approval": "old"},
        "human-approval-provenance.json": {"provenance": "old"},
    }
    for name, payload in control_payloads.items():
        write_private_json(roots.control / name, payload, create_once=True)
    attempt = _allocate_pre_fault_attempt(
        config,
        roots,
        implementation_commit="a" * 40,
        runtime_config_aggregate="b" * 64,
    )
    terminal = {
        "fault_injections": 0,
        "model_calls": 0,
        "forward_mutations": 0,
        "rollback_mutations": 0,
        "cleanup_verdict": "CLEAN",
        "verdict": "BLOCKED_E2E_V6_COMPOSE_UP_FAILED",
    }
    write_private_json(attempt / "terminal.json", terminal, create_once=True)
    _complete_live_attempt(config, roots, terminal)

    with pytest.raises(RuntimeError, match="identical pre-fault repair"):
        _prepare_pre_fault_repair(
            config,
            roots,
            implementation_commit="a" * 40,
            runtime_config_aggregate="b" * 64,
        )
    _prepare_pre_fault_repair(
        config,
        roots,
        implementation_commit="c" * 40,
        runtime_config_aggregate="d" * 64,
    )

    assert not (roots.control / "live-attempt-active.json").exists()
    assert roots.invocation_b == roots.live_attempt(1)
    assert (attempt / "invalidated.json").is_file()
    for name, payload in control_payloads.items():
        assert not (roots.control / name).exists()
        assert json.loads(
            (attempt / "invalidated-control" / name).read_text(encoding="utf-8")
        ) == payload
