"""Independent run-generation wrapper for the frozen E2E v6 software."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Mapping, Sequence, cast

from ecomsre_live_sandbox.contracts import (
    HumanApprovalRecord,
    SLIWindow,
    canonical_json_bytes,
    canonical_sha256,
    ensure_private_directory,
    file_sha256,
    write_private_json,
)
import ecomsre_live_sandbox.e2e_v4 as e2e_v4
import ecomsre_live_sandbox.e2e_v6 as e2e_v6
from ecomsre_live_sandbox.e2e_v6_repro_2_contracts import (
    E2EV6Repro2Config,
    E2EV6Repro2PrivateRoots,
    bind_repro_2_lifecycle,
)


def _read_mapping(path: Path, *, label: str) -> dict[str, object]:
    if path.is_symlink() or not path.is_file():
        raise RuntimeError(f"R2 {label} is absent")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"R2 {label} is malformed")
    return value


def _authority_file_hashes(roots: E2EV6Repro2PrivateRoots) -> tuple[str, str]:
    return (
        file_sha256(roots.control / "scenario-lock.json"),
        file_sha256(roots.control / "human-approval.json"),
    )


def _allocate_pre_fault_attempt(
    config: E2EV6Repro2Config,
    roots: E2EV6Repro2PrivateRoots,
    *,
    implementation_commit: str,
    runtime_config_aggregate: str,
) -> Path:
    accepted_path = roots.control / "accepted-live-run.json"
    if accepted_path.exists() or accepted_path.is_symlink():
        raise RuntimeError("accepted fault-time run is already sealed")
    scenario_lock_sha256, human_approval_sha256 = _authority_file_hashes(roots)
    history_path = roots.control / "live-attempt-history.json"
    history = (
        _read_mapping(history_path, label="live-attempt history")
        if history_path.exists() or history_path.is_symlink()
        else {
            "schema_version": "live-e2e.live-attempt-history.v6-repro-2",
            "run_generation": config.authority.run_generation,
            "attempts": [],
        }
    )
    attempts = history.get("attempts")
    if not isinstance(attempts, list):
        raise RuntimeError("R2 live-attempt history is malformed")
    if attempts:
        previous = attempts[-1]
        if not isinstance(previous, Mapping):
            raise RuntimeError("R2 previous live-attempt entry is malformed")
        relative = previous.get("attempt_relative_path")
        if not isinstance(relative, str):
            raise RuntimeError("R2 previous live-attempt path is malformed")
        terminal = _read_mapping(
            roots.root / relative / "terminal.json",
            label="previous pre-fault terminal",
        )
        if any(
            (
                terminal.get("fault_injections") != 0,
                terminal.get("model_calls") != 0,
                terminal.get("forward_mutations") != 0,
                terminal.get("rollback_mutations") != 0,
                terminal.get("cleanup_verdict") not in {"CLEAN", "NOT_REQUIRED"},
            )
        ):
            raise RuntimeError("R2 previous live attempt is not retryable")
        if (
            previous.get("implementation_commit") == implementation_commit
            and previous.get("runtime_config_aggregate_sha256")
            == runtime_config_aggregate
        ):
            raise RuntimeError("identical pre-fault retry is forbidden")
        if any(
            (
                previous.get("scenario_lock_sha256") == scenario_lock_sha256,
                previous.get("human_approval_sha256") == human_approval_sha256,
            )
        ):
            raise RuntimeError("R2 retry requires a new canonical state and approval")
    index = len(attempts) + 1
    attempt = roots.live_attempt(index)
    for directory in (
        attempt,
        attempt / "commands",
        attempt / "exceptions",
        attempt / "snapshots",
        attempt / "provider",
        attempt / "telemetry",
        attempt / "journal",
        attempt / "runtime",
    ):
        ensure_private_directory(directory)
    started = {
        "schema_version": "live-e2e.live-attempt-started.v6-repro-2",
        "run_generation": config.authority.run_generation,
        "attempt_id": attempt.name,
        "attempt_relative_path": attempt.relative_to(roots.root).as_posix(),
        "implementation_commit": implementation_commit,
        "runtime_config_aggregate_sha256": runtime_config_aggregate,
        "scenario_lock_sha256": scenario_lock_sha256,
        "human_approval_sha256": human_approval_sha256,
        "started_at": datetime.now(timezone.utc).isoformat(),
    }
    write_private_json(attempt / "started.json", started, create_once=True)
    write_private_json(
        roots.control / "live-attempt-active.json",
        started,
        create_once=False,
    )
    attempts.append({**started, "verdict": "STARTED"})
    write_private_json(history_path, history, create_once=False)
    roots.verify()
    return attempt


def _complete_live_attempt(
    config: E2EV6Repro2Config,
    roots: E2EV6Repro2PrivateRoots,
    terminal: Mapping[str, object],
) -> None:
    active = _read_mapping(
        roots.control / "live-attempt-active.json",
        label="active live-attempt pointer",
    )
    history_path = roots.control / "live-attempt-history.json"
    history = _read_mapping(history_path, label="live-attempt history")
    attempts = history.get("attempts")
    if not isinstance(attempts, list):
        raise RuntimeError("R2 live-attempt history is malformed")
    attempt_id = active.get("attempt_id")
    for item in reversed(attempts):
        if isinstance(item, dict) and item.get("attempt_id") == attempt_id:
            if item.get("verdict") != "STARTED":
                if item.get("verdict") == terminal.get("verdict"):
                    return
                raise RuntimeError("R2 live attempt is already terminal")
            item["verdict"] = terminal.get("verdict")
            item["fault_injections"] = terminal.get("fault_injections", 0)
            item["model_calls"] = terminal.get("model_calls", 0)
            item["forward_mutations"] = terminal.get("forward_mutations", 0)
            item["rollback_mutations"] = terminal.get("rollback_mutations", 0)
            item["cleanup_verdict"] = terminal.get("cleanup_verdict")
            item["terminal_sha256"] = file_sha256(
                roots.invocation_b / "terminal.json"
            )
            item["completed_at"] = datetime.now(timezone.utc).isoformat()
            break
    else:
        raise RuntimeError("R2 active live attempt is absent from history")
    history["run_generation"] = config.authority.run_generation
    write_private_json(history_path, history, create_once=False)
    accepted_path = roots.control / "accepted-live-run.json"
    if accepted_path.is_file() and not accepted_path.is_symlink():
        write_private_json(
            roots.accepted_live_run / "attempt-pointer.json",
            {
                "schema_version": "live-e2e.accepted-attempt-pointer.v6-repro-2",
                "run_generation": config.authority.run_generation,
                "attempt_id": active.get("attempt_id"),
                "attempt_relative_path": active.get("attempt_relative_path"),
                "accepted_live_run_sha256": file_sha256(accepted_path),
                "terminal_sha256": file_sha256(
                    roots.invocation_b / "terminal.json"
                ),
            },
            create_once=True,
        )
    roots.verify()


_ROTATED_CONTROL_NAMES = (
    "latest-development-pass-lock.json",
    "exact-head-ci.json",
    "pre-live-review.json",
    "canonical-active.json",
    "canonical-accepted.json",
    "scenario-lock.json",
    "plan-template.json",
    "approval-request.json",
    "human-approval.json",
    "human-approval-provenance.json",
)


def _archive_and_remove_control(
    roots: E2EV6Repro2PrivateRoots,
    *,
    archive: Path,
) -> list[str]:
    ensure_private_directory(archive)
    preserved: list[str] = []
    for name in _ROTATED_CONTROL_NAMES:
        source = roots.control / name
        if not source.exists() and not source.is_symlink():
            continue
        if source.is_symlink() or not source.is_file():
            raise RuntimeError(f"R2 control artifact is malformed: {name}")
        write_private_json(
            archive / name,
            _read_mapping(source, label=f"control artifact {name}"),
            create_once=True,
        )
        preserved.append(name)
    for name in _ROTATED_CONTROL_NAMES:
        source = roots.control / name
        if source.is_file() and not source.is_symlink():
            source.unlink()
    return preserved


def _archive_unallocated_live_attempt_artifacts(
    roots: E2EV6Repro2PrivateRoots,
    *,
    archive: Path,
) -> str | None:
    candidate = roots.next_live_attempt
    if not candidate.exists() and not candidate.is_symlink():
        return None
    if candidate.is_symlink() or not candidate.is_dir():
        raise RuntimeError("R2 unallocated live-attempt path is malformed")
    if any(
        (candidate / marker).exists() or (candidate / marker).is_symlink()
        for marker in ("started.json", "terminal.json")
    ):
        raise RuntimeError("R2 unallocated live-attempt contains lifecycle markers")
    artifact_files: list[dict[str, object]] = []
    for path in sorted(candidate.rglob("*")):
        if path.is_symlink():
            raise RuntimeError("R2 unallocated live-attempt contains a symbolic link")
        if path.is_dir():
            continue
        if not path.is_file():
            raise RuntimeError("R2 unallocated live-attempt contains an invalid entry")
        artifact_files.append(
            {
                "relative_path": path.relative_to(candidate).as_posix(),
                "sha256": file_sha256(path),
                "size_bytes": path.stat().st_size,
            }
        )
    archived_root = archive / "unallocated-live-attempt-artifacts"
    ensure_private_directory(archived_root)
    archived_attempt = archived_root / candidate.name
    if archived_attempt.exists() or archived_attempt.is_symlink():
        raise RuntimeError("R2 unallocated live-attempt archive already exists")
    candidate.rename(archived_attempt)
    for artifact in artifact_files:
        relative = artifact["relative_path"]
        if not isinstance(relative, str):
            raise AssertionError("R2 artifact relative path is not a string")
        if file_sha256(archived_attempt / relative) != artifact["sha256"]:
            raise RuntimeError("R2 archived live-attempt artifact differs")
    write_private_json(
        archive / "unallocated-live-attempt-invalidation.json",
        {
            "schema_version": (
                "live-e2e.unallocated-live-attempt-invalidation.v6-repro-2"
            ),
            "run_generation": roots.run_generation,
            "original_attempt_id": candidate.name,
            "original_attempt_relative_path": candidate.relative_to(
                roots.root
            ).as_posix(),
            "archived_attempt_relative_path": archived_attempt.relative_to(
                roots.root
            ).as_posix(),
            "artifact_files": artifact_files,
            "invalidated_at": datetime.now(timezone.utc).isoformat(),
        },
        create_once=True,
    )
    return candidate.name


def _archive_preallocation_failure(
    roots: E2EV6Repro2PrivateRoots,
    *,
    error: Exception,
    runtime_config_aggregate: str,
) -> None:
    if roots.active_live_attempt is not None:
        return
    candidate = roots.next_live_attempt
    if not candidate.exists() and not candidate.is_symlink():
        return
    failures_root = roots.root / "pre-allocation-failures"
    if failures_root.exists() or failures_root.is_symlink():
        if failures_root.is_symlink() or not failures_root.is_dir():
            raise RuntimeError("R2 pre-allocation failure root is malformed")
        existing = tuple(
            path
            for path in failures_root.glob("failure-*")
            if path.is_dir() and not path.is_symlink()
        )
    else:
        existing = ()
    archive = failures_root / f"failure-{len(existing) + 1:04d}"
    original_attempt_id = _archive_unallocated_live_attempt_artifacts(
        roots,
        archive=archive,
    )
    if original_attempt_id is None:
        return
    scenario_lock = _read_mapping(
        roots.control / "scenario-lock.json",
        label="scenario lock",
    )
    implementation_commit = scenario_lock.get("implementation_commit")
    if not isinstance(implementation_commit, str):
        raise RuntimeError("R2 scenario lock implementation commit is malformed")
    scenario_lock_sha256, human_approval_sha256 = _authority_file_hashes(roots)
    write_private_json(
        archive / "failure.json",
        {
            "schema_version": "live-e2e.pre-allocation-failure.v6-repro-2",
            "run_generation": roots.run_generation,
            "original_attempt_id": original_attempt_id,
            "implementation_commit": implementation_commit,
            "runtime_config_aggregate_sha256": runtime_config_aggregate,
            "scenario_lock_sha256": scenario_lock_sha256,
            "human_approval_sha256": human_approval_sha256,
            "failure_type": type(error).__name__,
            "failure_module": type(error).__module__,
            "failure_message_sha256": canonical_sha256(str(error)),
            "failed_at": datetime.now(timezone.utc).isoformat(),
        },
        create_once=True,
    )
    roots.verify()


def _reject_identical_preallocation_failure(
    roots: E2EV6Repro2PrivateRoots,
    *,
    runtime_config_aggregate: str,
) -> None:
    failures_root = roots.root / "pre-allocation-failures"
    if not failures_root.exists() and not failures_root.is_symlink():
        return
    if failures_root.is_symlink() or not failures_root.is_dir():
        raise RuntimeError("R2 pre-allocation failure root is malformed")
    failures = tuple(
        sorted(
            path
            for path in failures_root.glob("failure-*")
            if path.is_dir() and not path.is_symlink()
        )
    )
    if not failures:
        return
    previous = _read_mapping(
        failures[-1] / "failure.json",
        label="pre-allocation failure",
    )
    scenario_lock = _read_mapping(
        roots.control / "scenario-lock.json",
        label="scenario lock",
    )
    scenario_lock_sha256, human_approval_sha256 = _authority_file_hashes(roots)
    if all(
        (
            previous.get("implementation_commit")
            == scenario_lock.get("implementation_commit"),
            previous.get("runtime_config_aggregate_sha256")
            == runtime_config_aggregate,
            previous.get("scenario_lock_sha256") == scenario_lock_sha256,
            previous.get("human_approval_sha256") == human_approval_sha256,
        )
    ):
        raise RuntimeError("identical pre-allocation retry is forbidden")


def _latest_development_binding(
    roots: E2EV6Repro2PrivateRoots,
) -> dict[str, object] | None:
    history_path = roots.control / "development-history.json"
    if not history_path.exists() and not history_path.is_symlink():
        return None
    history = _read_mapping(history_path, label="development history")
    runs = history.get("runs")
    if not isinstance(runs, list):
        raise RuntimeError("R2 development history is malformed")
    if not runs:
        return None
    latest = runs[-1]
    if not isinstance(latest, Mapping):
        raise RuntimeError("R2 latest development history entry is malformed")
    return dict(latest)


def _prepare_pre_fault_repair(
    config: E2EV6Repro2Config,
    roots: E2EV6Repro2PrivateRoots,
    *,
    implementation_commit: str,
    runtime_config_aggregate: str,
) -> None:
    active_path = roots.control / "live-attempt-active.json"
    if (roots.control / "accepted-live-run.json").exists():
        raise RuntimeError("accepted fault-time run forbids implementation repair")
    if not active_path.exists() and not active_path.is_symlink():
        scenario_path = roots.control / "scenario-lock.json"
        development_path = roots.control / "latest-development-pass-lock.json"
        binding_path = scenario_path if scenario_path.exists() else development_path
        if binding_path.exists() or binding_path.is_symlink():
            binding = _read_mapping(binding_path, label="current runtime authority")
        else:
            candidate = roots.next_live_attempt
            if not candidate.exists() and not candidate.is_symlink():
                return
            binding = _latest_development_binding(roots) or {}
        tracked = binding.get("tracked_runtime_and_config")
        bound_aggregate = (
            canonical_sha256(tracked)
            if isinstance(tracked, Mapping)
            else binding.get("runtime_config_aggregate_sha256")
        )
        if (
            binding.get("implementation_commit") == implementation_commit
            and bound_aggregate == runtime_config_aggregate
        ):
            return
        history_root = roots.root / "authority-history"
        ensure_private_directory(history_root)
        rotations = tuple(
            path
            for path in history_root.glob("rotation-*")
            if path.is_dir() and not path.is_symlink()
        )
        rotation = history_root / f"rotation-{len(rotations) + 1:04d}"
        preserved = _archive_and_remove_control(
            roots,
            archive=rotation / "control",
        )
        invalidated_unallocated_live_attempt = (
            _archive_unallocated_live_attempt_artifacts(
                roots,
                archive=rotation,
            )
        )
        write_private_json(
            rotation / "invalidation.json",
            {
                "schema_version": "live-e2e.authority-invalidation.v6-repro-2",
                "run_generation": config.authority.run_generation,
                "previous_implementation_commit": binding.get(
                    "implementation_commit"
                ),
                "previous_runtime_config_aggregate_sha256": bound_aggregate,
                "replacement_implementation_commit": implementation_commit,
                "replacement_runtime_config_aggregate_sha256": (
                    runtime_config_aggregate
                ),
                "preserved_control_artifacts": preserved,
                "invalidated_unallocated_live_attempt": (
                    invalidated_unallocated_live_attempt
                ),
                "invalidated_at": datetime.now(timezone.utc).isoformat(),
            },
            create_once=True,
        )
        roots.verify()
        return
    active = _read_mapping(active_path, label="active live-attempt pointer")
    relative = active.get("attempt_relative_path")
    if not isinstance(relative, str):
        raise RuntimeError("R2 active live-attempt path is malformed")
    attempt = roots.root / relative
    terminal = _read_mapping(
        attempt / "terminal.json",
        label="pre-fault terminal for repair",
    )
    if any(
        (
            terminal.get("fault_injections") != 0,
            terminal.get("model_calls") != 0,
            terminal.get("forward_mutations") != 0,
            terminal.get("rollback_mutations") != 0,
            terminal.get("cleanup_verdict") not in {"CLEAN", "NOT_REQUIRED"},
        )
    ):
        raise RuntimeError("R2 live attempt crossed the pre-fault repair boundary")
    if (
        active.get("implementation_commit") == implementation_commit
        and active.get("runtime_config_aggregate_sha256")
        == runtime_config_aggregate
    ):
        raise RuntimeError("identical pre-fault repair is forbidden")
    preserved = _archive_and_remove_control(
        roots,
        archive=attempt / "invalidated-control",
    )
    write_private_json(
        attempt / "invalidated.json",
        {
            "schema_version": "live-e2e.pre-fault-invalidation.v6-repro-2",
            "run_generation": config.authority.run_generation,
            "attempt_id": active.get("attempt_id"),
            "previous_implementation_commit": active.get(
                "implementation_commit"
            ),
            "previous_runtime_config_aggregate_sha256": active.get(
                "runtime_config_aggregate_sha256"
            ),
            "replacement_implementation_commit": implementation_commit,
            "replacement_runtime_config_aggregate_sha256": (
                runtime_config_aggregate
            ),
            "terminal_sha256": file_sha256(attempt / "terminal.json"),
            "preserved_control_artifacts": preserved,
            "invalidated_at": datetime.now(timezone.utc).isoformat(),
        },
        create_once=True,
    )
    active_path.unlink()
    roots.verify()


def _seal_accepted_live_run(
    config: E2EV6Repro2Config,
    roots: E2EV6Repro2PrivateRoots,
    terminal: dict[str, object],
    baseline_windows: Sequence[SLIWindow],
) -> str:
    path = roots.control / "accepted-live-run.json"
    if path.exists() or path.is_symlink():
        raise RuntimeError("accepted fault-time run is already sealed")
    active = _read_mapping(
        roots.control / "live-attempt-active.json",
        label="active live-attempt pointer",
    )
    if len(baseline_windows) != 2 or any(
        item.phase != "BASELINE" for item in baseline_windows
    ):
        raise RuntimeError("R2 accepted run requires two baseline windows")
    counters = {
        "fault_injections": terminal.get("fault_injections", 0),
        "model_calls": terminal.get("model_calls", 0),
        "forward_mutations": terminal.get("forward_mutations", 0),
        "rollback_mutations": terminal.get("rollback_mutations", 0),
    }
    if any(
        (
            terminal.get("implementation_commit")
            != active.get("implementation_commit"),
            terminal.get("provider_calls") != 1,
            terminal.get("provider_preflight_passed") is not True,
            terminal.get("compose_start_requested") is not True,
            terminal.get("compose_start_returned") is not True,
            terminal.get("baseline_windows") != 2,
            any(value != 0 for value in counters.values()),
        )
    ):
        raise RuntimeError("R2 accepted run pre-fault state differs")
    pointer = {
        "schema_version": "live-e2e.accepted-live-run.v6-repro-2",
        "software_version": config.authority.software_version,
        "runtime_policy_version": config.authority.runtime_policy_version,
        "run_generation": config.authority.run_generation,
        "attempt_id": active.get("attempt_id"),
        "attempt_relative_path": active.get("attempt_relative_path"),
        "implementation_commit": terminal.get("implementation_commit"),
        "runtime_config_aggregate_sha256": active.get(
            "runtime_config_aggregate_sha256"
        ),
        "scenario_lock_sha256": file_sha256(
            roots.control / "scenario-lock.json"
        ),
        "human_approval_sha256": file_sha256(
            roots.control / "human-approval.json"
        ),
        "baseline_window_sha256": [
            canonical_sha256(item) for item in baseline_windows
        ],
        "pre_fault_counters": counters,
        "provider_preflight_passed": True,
        "services_healthy": True,
        "baseline_exact": True,
        "accepted_at": datetime.now(timezone.utc).isoformat(),
    }
    write_private_json(path, pointer, create_once=True)
    pointer_sha256 = file_sha256(path)
    terminal["accepted_live_run_sealed"] = True
    terminal["accepted_live_run_sha256"] = pointer_sha256
    return pointer_sha256


def run_development_probe(
    config: E2EV6Repro2Config,
    roots: E2EV6Repro2PrivateRoots,
    **kwargs: Any,
) -> dict[str, object]:
    bind_repro_2_lifecycle(config, roots)
    worktree_verifier = kwargs.get("worktree_verifier", e2e_v4._verify_worktree)
    implementation_commit = worktree_verifier(config, True)
    _, runtime_config_aggregate = e2e_v4._runtime_config_aggregate(
        cast(Any, config)
    )
    _prepare_pre_fault_repair(
        config,
        roots,
        implementation_commit=implementation_commit,
        runtime_config_aggregate=runtime_config_aggregate,
    )
    return e2e_v6.run_development_probe(cast(Any, config), cast(Any, roots), **kwargs)


def run_canonical_invocation_a(
    config: E2EV6Repro2Config,
    roots: E2EV6Repro2PrivateRoots,
    **kwargs: Any,
) -> dict[str, object]:
    bind_repro_2_lifecycle(config, roots)
    return e2e_v6.run_canonical_invocation_a(
        cast(Any, config), cast(Any, roots), **kwargs
    )


def record_human_approval_for_invocation_b(
    config: E2EV6Repro2Config,
    roots: E2EV6Repro2PrivateRoots,
    *,
    approver: str,
    phrase: str,
    authorization_source: str = "HUMAN_EXPLICIT_AUTHORIZATION",
    command_execution: str = "HUMAN_MANUAL_EXECUTION",
) -> HumanApprovalRecord:
    bind_repro_2_lifecycle(config, roots)
    record = e2e_v6.record_human_approval_for_invocation_b(
        cast(Any, config),
        cast(Any, roots),
        approver=approver,
        phrase=phrase,
    )
    write_private_json(
        roots.control / "human-approval-provenance.json",
        {
            "schema_version": "live-e2e.human-approval-provenance.v6-repro-2",
            "run_generation": config.authority.run_generation,
            "human_approval_sha256": file_sha256(
                roots.control / "human-approval.json"
            ),
            "authorization_source": authorization_source,
            "command_execution": command_execution,
            "codex_autonomous_self_approval": False,
        },
        create_once=True,
    )
    roots.verify()
    return record


def _write_public_outputs_repro_2(
    config: E2EV6Repro2Config,
    terminal: Mapping[str, object],
    *,
    roots: E2EV6Repro2PrivateRoots,
) -> tuple[str, ...]:
    accepted_path = roots.control / "accepted-live-run.json"
    if not accepted_path.exists() and not accepted_path.is_symlink():
        return ()
    if accepted_path.is_symlink() or not accepted_path.is_file():
        raise ValueError("R2 accepted live-run authority is malformed")
    accepted = _read_mapping(accepted_path, label="accepted live-run authority")
    accepted_sha256 = file_sha256(accepted_path)
    active = _read_mapping(
        roots.control / "live-attempt-active.json",
        label="accepted live-attempt pointer",
    )
    counters = accepted.get("pre_fault_counters")
    baseline_hashes = accepted.get("baseline_window_sha256")
    if any(
        (
            terminal.get("accepted_live_run_sealed") is not True,
            terminal.get("accepted_live_run_sha256") != accepted_sha256,
            accepted.get("schema_version")
            != "live-e2e.accepted-live-run.v6-repro-2",
            accepted.get("run_generation") != config.authority.run_generation,
            accepted.get("attempt_id") != active.get("attempt_id"),
            accepted.get("attempt_relative_path")
            != active.get("attempt_relative_path"),
            accepted.get("implementation_commit")
            != terminal.get("implementation_commit"),
            accepted.get("runtime_config_aggregate_sha256")
            != active.get("runtime_config_aggregate_sha256"),
            accepted.get("scenario_lock_sha256")
            != file_sha256(roots.control / "scenario-lock.json"),
            accepted.get("human_approval_sha256")
            != file_sha256(roots.control / "human-approval.json"),
            counters
            != {
                "fault_injections": 0,
                "model_calls": 0,
                "forward_mutations": 0,
                "rollback_mutations": 0,
            },
            not isinstance(baseline_hashes, list)
            or len(baseline_hashes) != 2
            or any(
                not isinstance(value, str) or len(value) != 64
                for value in baseline_hashes
            ),
        )
    ):
        raise ValueError("R2 public projection requires the accepted fault-time run")
    sealed_terminal_path = roots.invocation_b / "terminal.json"
    if sealed_terminal_path.is_symlink() or not sealed_terminal_path.is_file():
        raise ValueError("R2 public projection requires the sealed private terminal")
    sealed = json.loads(sealed_terminal_path.read_text(encoding="utf-8"))
    if not isinstance(sealed, Mapping) or dict(sealed) != dict(terminal):
        raise ValueError("R2 supplied terminal differs from the sealed private terminal")
    public = e2e_v6.build_expected_public_result(cast(Any, config), sealed)
    e2e_v6.verify_public_result(cast(Any, config), public, sealed)
    paths = (
        config.repository_root / config.reporting.public_result_json,
        config.repository_root / config.reporting.public_result_markdown,
        config.repository_root / config.reporting.public_human_brief,
    )
    payloads = (
        canonical_json_bytes(public),
        (
            "# Live Fault to A0 Controlled Remediation E2E v6 R2\n\n"
            f"**Verdict:** `{public['verdict']}`\n\n"
            "Independent V6_REPRO_2 projection from its accepted and sealed "
            "private terminal. The original v6 and V6_REPRO_1 results remain "
            "preserved. This "
            "covers one preregistered local Sandbox scenario and a human-"
            "preauthorized frozen runbook; it is not production or autonomous "
            "remediation.\n"
        ).encode("utf-8"),
        (
            "# Live Fault → A0 → Controlled Remediation v6 R2 — Human Brief\n\n"
            "本结果属于独立运行代际 `V6_REPRO_2`，仅在 accepted-live-run "
            "指针已 create-once、私有 terminal 已封存后投影；原 v6 与 "
            "`V6_REPRO_1` 结果均保持不变。"
            "结论仅覆盖一个本地 Sandbox、一个预注册场景和人工预授权的冻结修复 "
            "runbook，不构成生产自治或 Multi-Agent 优越性声明。\n"
        ).encode("utf-8"),
    )
    for path, payload in zip(paths, payloads, strict=True):
        if path.exists() or path.is_symlink():
            if path.is_symlink() or not path.is_file() or path.read_bytes() != payload:
                raise FileExistsError(f"R2 public projection differs: {path}")
    for path, payload in zip(paths, payloads, strict=True):
        if not path.exists():
            e2e_v6._write_new_public(path, payload)
    return tuple(
        path.relative_to(config.repository_root).as_posix() for path in paths
    )


def run_invocation_b(
    config: E2EV6Repro2Config,
    roots: E2EV6Repro2PrivateRoots,
    **kwargs: Any,
) -> dict[str, object]:
    bind_repro_2_lifecycle(config, roots)
    _, runtime_config_aggregate = e2e_v4._runtime_config_aggregate(
        cast(Any, config)
    )
    _reject_identical_preallocation_failure(
        roots,
        runtime_config_aggregate=runtime_config_aggregate,
    )

    kwargs.setdefault(
        "live_attempt_allocator",
        lambda current_config, current_roots, implementation_commit: (
            _allocate_pre_fault_attempt(
                cast(E2EV6Repro2Config, current_config),
                cast(E2EV6Repro2PrivateRoots, current_roots),
                implementation_commit=implementation_commit,
                runtime_config_aggregate=runtime_config_aggregate,
            )
        ),
    )
    kwargs.setdefault(
        "accepted_run_sealer",
        lambda current_config, current_roots, terminal, baseline_windows: (
            _seal_accepted_live_run(
                cast(E2EV6Repro2Config, current_config),
                cast(E2EV6Repro2PrivateRoots, current_roots),
                terminal,
                baseline_windows,
            )
        ),
    )
    kwargs.setdefault(
        "live_attempt_completer",
        lambda current_config, current_roots, terminal: _complete_live_attempt(
            cast(E2EV6Repro2Config, current_config),
            cast(E2EV6Repro2PrivateRoots, current_roots),
            terminal,
        ),
    )
    kwargs.setdefault(
        "public_writer",
        lambda current_config, terminal: _write_public_outputs_repro_2(
            cast(E2EV6Repro2Config, current_config),
            terminal,
            roots=roots,
        ),
    )
    roots.arm_live_attempt_scope()
    try:
        return e2e_v6.run_invocation_b(
            cast(Any, config), cast(Any, roots), **kwargs
        )
    except Exception as error:
        _archive_preallocation_failure(
            roots,
            error=error,
            runtime_config_aggregate=runtime_config_aggregate,
        )
        raise
    finally:
        roots.disarm_live_attempt_scope()


__all__ = [
    "record_human_approval_for_invocation_b",
    "run_canonical_invocation_a",
    "run_development_probe",
    "run_invocation_b",
]
