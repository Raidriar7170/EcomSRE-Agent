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
from ecomsre_live_sandbox.e2e_v6_repro_2_contracts import (
    E2EV6Repro2PrivateRoots,
    bind_repro_2_lifecycle,
    load_e2e_v6_repro_2_config,
)
from ecomsre_live_sandbox.e2e_v6_repro_2 import (
    _allocate_pre_fault_attempt,
    _complete_live_attempt,
    _prepare_pre_fault_repair,
    _seal_accepted_live_run,
    _write_public_outputs_repro_2,
    reconcile_sealed_live_attempt_completion,
)
from ecomsre_live_sandbox.invocation_b_verdicts import (
    invocation_b_verdict_policy_sha256,
)


CONFIG = Path("config/live-fault-a0-controlled-remediation-e2e-v6-repro-2")
V6_CONFIG = Path("config/live-fault-a0-controlled-remediation-e2e-v6")


def test_repro_2_authority_keeps_v6_software_and_binds_new_generation() -> None:
    config = load_e2e_v6_repro_2_config(CONFIG)
    base = load_e2e_v6_config(V6_CONFIG)
    authority = config.authority

    assert authority.software_version == base.authority.version
    assert authority.version == "live-fault-a0-controlled-remediation-e2e-v6"
    assert authority.runtime_policy_version == "V6"
    assert authority.run_generation == "V6_REPRO_2"
    assert authority.branch == (
        "feature/live-fault-a0-controlled-remediation-e2e-v6-repro-2"
    )
    assert authority.predecessor_pr == 38
    assert authority.predecessor_result_head == (
        "0ffbd73ae258dab5a4a2532b7d753766ca0b48f0"
    )
    assert authority.predecessor_head == authority.predecessor_result_head
    assert authority.predecessor_public_terminal == (
        "BLOCKED_PUBLIC_RESULT_VERIFICATION"
    )
    assert authority.predecessor_sealed_source_verdict == (
        "BLOCKED_E2E_V6_UNCLASSIFIED_RUNTIME_FAILURE"
    )
    assert authority.predecessor_terminal == (
        "BLOCKED_E2E_V6_UNCLASSIFIED_RUNTIME_FAILURE"
    )
    assert authority.predecessor_sealed_terminal_sha256 == (
        "1141efbfda5bc6244c9579f96ff0f1258c4debdad8c0ff8f455c005eea0d1547"
    )
    assert authority.predecessor_accepted_live_run_sha256 == (
        "c63e32808b375dd68f7a58e9a699e2df819c4f9255a4a354408409fbf230555c"
    )
    assert authority.predecessor_public_semantic_sha256 == (
        "7895a8a82fcd9d09779c3c3fd49d28fb81b231278a490922d27d0ef792ba2d35"
    )
    assert authority.predecessor_final_closure_sha256 == (
        "817e2f23b85418483e32826a6b6e9548947dbba5ebfaf3bf2876c397060acf81"
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
    assert authority.source_stage_owner == "ORDERED_SOURCE_COLLECTOR"
    assert authority.source_stage_replay_policy == "FORBIDDEN_FOR_FAULT_PROJECTION"
    assert authority.post_source_gate_next_stage == "MULTISERVICE_PROJECTION_STARTED"
    assert authority.strict_diagnostic_journal_monotonicity is True
    assert authority.invocation_b_success == base.authority.invocation_b_success


def test_repro_2_reporting_paths_do_not_replace_v6_or_r1_outputs() -> None:
    config = load_e2e_v6_repro_2_config(CONFIG)

    assert config.reporting.public_result_json == (
        "docs/results/live-fault-a0-controlled-remediation-e2e-v6-repro-2.json"
    )
    assert config.reporting.public_result_markdown == (
        "docs/results/live-fault-a0-controlled-remediation-e2e-v6-repro-2.md"
    )
    assert config.reporting.public_human_brief == (
        "docs/results/"
        "live-fault-a0-controlled-remediation-e2e-v6-repro-2-human-brief.md"
    )
    assert file_sha256(
        Path("docs/results/live-fault-a0-controlled-remediation-e2e-v6-repro-1.json")
    ) == "e988b1fb42d976836386a91a8ef827ef319ccca76eeb5e043b1073665f89b7c1"
    assert file_sha256(
        Path("docs/results/live-fault-a0-controlled-remediation-e2e-v6-repro-1.md")
    ) == "485b3c7f8f6b482e5b9364e488de50678298bf93b11b92bcaceab9cc0bc951d8"
    assert file_sha256(
        Path(
            "docs/results/"
            "live-fault-a0-controlled-remediation-e2e-v6-repro-1-human-brief.md"
        )
    ) == "737d27db31f5c88fdd674072ce08fca39f2eb48f8fdaecf55e151ae1f2d2abaa"


def test_repro_2_private_lifecycle_is_bound_and_original_v6_is_not_reusable(
    tmp_path: Path,
) -> None:
    config = load_e2e_v6_repro_2_config(CONFIG)
    roots = E2EV6Repro2PrivateRoots(tmp_path / "repro-2")

    bind_repro_2_lifecycle(config, roots)

    lifecycle = json.loads(
        (roots.control / "private-root-lifecycle.json").read_text(encoding="utf-8")
    )
    bound_authority = json.loads(
        (roots.control / "authority.json").read_text(encoding="utf-8")
    )
    assert lifecycle["software_version"] == config.authority.software_version
    assert lifecycle["runtime_policy_version"] == "V6"
    assert lifecycle["run_generation"] == "V6_REPRO_2"
    assert lifecycle["branch"] == config.authority.branch
    assert bound_authority["run_generation"] == "V6_REPRO_2"
    assert bound_authority["predecessor_result_head"] == (
        "0ffbd73ae258dab5a4a2532b7d753766ca0b48f0"
    )
    roots.verify()

    original_config = load_e2e_v6_config(V6_CONFIG)
    original_roots = E2EV6PrivateRoots(tmp_path / "original-v6")
    original_roots.bind_lifecycle(
        original_config.authority,
        repository_root=original_config.repository_root,
    )
    with pytest.raises(ValueError, match="private lifecycle binding differs"):
        bind_repro_2_lifecycle(config, original_roots)


def test_repro_2_non_live_work_uses_global_private_paths(
    tmp_path: Path,
) -> None:
    config = load_e2e_v6_repro_2_config(CONFIG)
    roots = E2EV6Repro2PrivateRoots(tmp_path / "repro-2")

    bind_repro_2_lifecycle(config, roots)

    assert roots.invocation_b == roots.root / "live-run/invocation-b"
    assert roots.runtime == roots.root / "runtime"
    assert roots.telemetry == roots.root / "telemetry"
    assert roots.provider == roots.root / "provider"
    assert roots.journal == roots.root / "journal"
    assert not roots.live_attempt(1).exists()


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


def test_repro_2_attempt_is_scoped_and_identical_pre_fault_retry_is_rejected(
    tmp_path: Path,
) -> None:
    config = load_e2e_v6_repro_2_config(CONFIG)
    roots = E2EV6Repro2PrivateRoots(tmp_path / "repro-2")
    bind_repro_2_lifecycle(config, roots)
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


def test_repro_2_accepted_pointer_is_create_once_and_precedes_fault(
    tmp_path: Path,
) -> None:
    config = load_e2e_v6_repro_2_config(CONFIG)
    roots = E2EV6Repro2PrivateRoots(tmp_path / "repro-2")
    bind_repro_2_lifecycle(config, roots)
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
    assert pointer["run_generation"] == "V6_REPRO_2"
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


def test_repro_2_reconciles_stranded_sealed_attempt_without_rerun(
    tmp_path: Path,
) -> None:
    config = load_e2e_v6_repro_2_config(CONFIG)
    roots = E2EV6Repro2PrivateRoots(tmp_path / "repro-2")
    bind_repro_2_lifecycle(config, roots)
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
        "run_generation": "V6_REPRO_2",
        "provider_calls": 1,
        "provider_preflight_passed": True,
        "compose_start_requested": True,
        "compose_start_returned": True,
        "baseline_windows": 2,
        "fault_injections": 0,
        "model_calls": 0,
        "forward_mutations": 0,
        "rollback_mutations": 0,
        "cleanup_verdict": "CLEAN",
        "verdict": "BLOCKED_PUBLIC_RESULT_VERIFICATION",
    }
    accepted_sha = _seal_accepted_live_run(
        config,
        roots,
        terminal,
        (_baseline_window(1), _baseline_window(2)),
    )
    terminal["fault_injections"] = 1
    write_private_json(attempt / "terminal.json", terminal, create_once=True)

    history_path = roots.control / "live-attempt-history.json"
    pointer_path = roots.accepted_live_run / "attempt-pointer.json"
    assert json.loads(history_path.read_text(encoding="utf-8"))["attempts"][0][
        "verdict"
    ] == "STARTED"
    assert not pointer_path.exists()

    reconcile_sealed_live_attempt_completion(config, roots)

    history = json.loads(history_path.read_text(encoding="utf-8"))
    completed = history["attempts"][0]
    assert completed["verdict"] == "BLOCKED_PUBLIC_RESULT_VERIFICATION"
    assert completed["fault_injections"] == 1
    assert completed["cleanup_verdict"] == "CLEAN"
    assert completed["terminal_sha256"] == file_sha256(attempt / "terminal.json")
    assert isinstance(completed["completed_at"], str)
    pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
    assert pointer == {
        "schema_version": "live-e2e.accepted-attempt-pointer.v6-repro-2",
        "run_generation": "V6_REPRO_2",
        "attempt_id": "attempt-0001",
        "attempt_relative_path": "live-attempts/attempt-0001",
        "accepted_live_run_sha256": accepted_sha,
        "terminal_sha256": file_sha256(attempt / "terminal.json"),
    }

    history_sha = file_sha256(history_path)
    pointer_sha = file_sha256(pointer_path)
    reconcile_sealed_live_attempt_completion(config, roots)
    assert file_sha256(history_path) == history_sha
    assert file_sha256(pointer_path) == pointer_sha


def test_repro_2_pre_fault_terminal_cannot_write_public_outputs(
    tmp_path: Path,
) -> None:
    config = load_e2e_v6_repro_2_config(CONFIG)
    isolated = replace(config, repository_root=tmp_path / "repository")
    roots = E2EV6Repro2PrivateRoots(tmp_path / "private-repro-2")
    bind_repro_2_lifecycle(config, roots)

    assert _write_public_outputs_repro_2(
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


def test_repro_2_changed_pre_fault_repair_preserves_and_rotates_authority(
    tmp_path: Path,
) -> None:
    config = load_e2e_v6_repro_2_config(CONFIG)
    roots = E2EV6Repro2PrivateRoots(tmp_path / "repro-2")
    bind_repro_2_lifecycle(config, roots)
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
    assert roots.next_live_attempt == roots.live_attempt(2)
    assert roots.invocation_b == roots.root / "live-run/invocation-b"
    assert (attempt / "invalidated.json").is_file()
    for name, payload in control_payloads.items():
        assert not (roots.control / name).exists()
        assert json.loads(
            (attempt / "invalidated-control" / name).read_text(encoding="utf-8")
        ) == payload


def test_repro_2_rotates_stale_canonical_approval_before_any_live_attempt(
    tmp_path: Path,
) -> None:
    config = load_e2e_v6_repro_2_config(CONFIG)
    roots = E2EV6Repro2PrivateRoots(tmp_path / "repro-2")
    bind_repro_2_lifecycle(config, roots)
    from ecomsre_live_sandbox.contracts import write_private_json

    tracked = {"runtime.py": "a" * 64}
    payloads = {
        "latest-development-pass-lock.json": {
            "implementation_commit": "a" * 40,
            "runtime_config_aggregate_sha256": canonical_sha256(tracked),
        },
        "exact-head-ci.json": {"ci": "old"},
        "pre-live-review.json": {"review": "old"},
        "canonical-active.json": {"canonical": "old"},
        "canonical-accepted.json": {"accepted": "old"},
        "scenario-lock.json": {
            "implementation_commit": "a" * 40,
            "tracked_runtime_and_config": tracked,
        },
        "plan-template.json": {"plan": "old"},
        "approval-request.json": {"request": "old"},
        "human-approval.json": {"approval": "old"},
        "human-approval-provenance.json": {"provenance": "old"},
    }
    for name, payload in payloads.items():
        write_private_json(roots.control / name, payload, create_once=True)
    write_private_json(
        roots.live_attempt(1) / "runtime/run-0001/environment.json",
        {"source": "misrouted-development"},
        create_once=True,
    )
    write_private_json(
        roots.live_attempt(1) / "telemetry/attempt-0001/window.json",
        {"source": "misrouted-canonical"},
        create_once=True,
    )

    _prepare_pre_fault_repair(
        config,
        roots,
        implementation_commit="b" * 40,
        runtime_config_aggregate="c" * 64,
    )

    assert not (roots.control / "canonical-accepted.json").exists()
    assert not (roots.control / "human-approval.json").exists()
    assert not (roots.control / "latest-development-pass-lock.json").exists()
    rotation = roots.root / "authority-history/rotation-0001"
    invalidation = json.loads(
        (rotation / "invalidation.json").read_text(encoding="utf-8")
    )
    assert invalidation["replacement_implementation_commit"] == "b" * 40
    for name, payload in payloads.items():
        assert json.loads(
            (rotation / "control" / name).read_text(encoding="utf-8")
        ) == payload
    assert not roots.live_attempt(1).exists()
    archived_attempt = (
        rotation / "unallocated-live-attempt-artifacts/attempt-0001"
    )
    assert json.loads(
        (
            archived_attempt / "runtime/run-0001/environment.json"
        ).read_text(encoding="utf-8")
    ) == {"source": "misrouted-development"}
    assert json.loads(
        (
            archived_attempt / "telemetry/attempt-0001/window.json"
        ).read_text(encoding="utf-8")
    ) == {"source": "misrouted-canonical"}
    artifact_invalidation = json.loads(
        (rotation / "unallocated-live-attempt-invalidation.json").read_text(
            encoding="utf-8"
        )
    )
    assert artifact_invalidation["original_attempt_id"] == "attempt-0001"
    assert len(artifact_invalidation["artifact_files"]) == 2


def test_repro_2_rotates_failed_development_artifacts_without_pass_lock(
    tmp_path: Path,
) -> None:
    config = load_e2e_v6_repro_2_config(CONFIG)
    roots = E2EV6Repro2PrivateRoots(tmp_path / "repro-2")
    bind_repro_2_lifecycle(config, roots)
    from ecomsre_live_sandbox.contracts import write_private_json

    write_private_json(
        roots.control / "development-history.json",
        {
            "schema_version": "live-e2e.development-history.v6",
            "runs": [
                {
                    "run_id": "run-0001",
                    "implementation_commit": "a" * 40,
                    "runtime_config_aggregate_sha256": "b" * 64,
                    "verdict": "BLOCKED_E2E_V6_SERVICE_HEALTH_TIMEOUT",
                }
            ],
        },
        create_once=True,
    )
    write_private_json(
        roots.live_attempt(1) / "telemetry/failed-development.json",
        {"source": "misrouted-failed-development"},
        create_once=True,
    )

    _prepare_pre_fault_repair(
        config,
        roots,
        implementation_commit="c" * 40,
        runtime_config_aggregate="d" * 64,
    )

    rotation = roots.root / "authority-history/rotation-0001"
    assert not roots.live_attempt(1).exists()
    assert json.loads(
        (
            rotation
            / "unallocated-live-attempt-artifacts/attempt-0001/telemetry/"
            "failed-development.json"
        ).read_text(encoding="utf-8")
    ) == {"source": "misrouted-failed-development"}
    invalidation = json.loads(
        (rotation / "invalidation.json").read_text(encoding="utf-8")
    )
    assert invalidation["previous_implementation_commit"] == "a" * 40
    assert invalidation["previous_runtime_config_aggregate_sha256"] == "b" * 64
    assert invalidation["invalidated_unallocated_live_attempt"] == "attempt-0001"
