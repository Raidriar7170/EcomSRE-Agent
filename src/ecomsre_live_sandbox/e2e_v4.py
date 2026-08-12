"""Bounded development and canonical lifecycle for live E2E v4."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import time
from typing import Any, cast

import ecomsre_live_sandbox.e2e_v3 as e2e_v3

from ecomsre_live_sandbox.contracts import (
    ApprovalRequest,
    HumanApprovalRecord,
    LiveRemediationPlan,
    canonical_sha256,
    ensure_private_directory,
    file_sha256,
    write_private_json,
)
from ecomsre_live_sandbox.e2e_contracts import scan_public_e2e_payload
from ecomsre_live_sandbox.e2e_diagnostics import (
    DiagnosticEventStatus,
    DiagnosticFailureCode,
    DiagnosticJournal,
    DiagnosticRunKind,
    DiagnosticStage,
    ExceptionArtifactStore,
)
from ecomsre_live_sandbox.e2e_source_batch import (
    JsonRequester,
    ProjectionCollector,
    _default_projection_inputs,
    collect_ordered_source_evidence_v4,
)
from ecomsre_live_sandbox.e2e_v3 import NoFaultEvidence
from ecomsre_live_sandbox.e2e_v3_contracts import (
    create_approval_request,
    record_human_approval,
)
from ecomsre_live_sandbox.e2e_v4_contracts import (
    E2EV4Config,
    E2EV4PrivateRoots,
)
from ecomsre_live_sandbox.environment import SandboxEnvironment
from ecomsre_live_sandbox.instrumentation_v2 import load_instrumentation_config


E2E_V4_CONFIG_RELATIVE = Path("config/live-fault-a0-controlled-remediation-e2e-v4")
TELEMETRY_V3_CONFIG_RELATIVE = Path("config/live-telemetry-instrumentation-v3")


def _schema_suffix(config: object) -> str:
    version = str(getattr(getattr(config, "authority"), "version"))
    return "v5" if version.endswith("e2e-v5") else "v4"


def _is_v5(config: object) -> bool:
    return _schema_suffix(config) == "v5"

_TRACKED_RUNTIME_CONFIG_FILES = {
    "authority.json": E2E_V4_CONFIG_RELATIVE / "authority.json",
    "development-probes.json": E2E_V4_CONFIG_RELATIVE / "development-probes.json",
    "diagnostics.json": E2E_V4_CONFIG_RELATIVE / "diagnostics.json",
    "image-authority.json.schema-or-policy": (
        E2E_V4_CONFIG_RELATIVE / "image-authority.json.schema-or-policy"
    ),
    "projection.json": E2E_V4_CONFIG_RELATIVE / "projection.json",
    "reporting.json": E2E_V4_CONFIG_RELATIVE / "reporting.json",
    "control.py": Path("src/ecomsre_live_sandbox/control.py"),
    "e2e_contracts.py": Path("src/ecomsre_live_sandbox/e2e_contracts.py"),
    "e2e_diagnostics.py": Path("src/ecomsre_live_sandbox/e2e_diagnostics.py"),
    "e2e_source_batch.py": Path("src/ecomsre_live_sandbox/e2e_source_batch.py"),
    "e2e_telemetry.py": Path("src/ecomsre_live_sandbox/e2e_telemetry.py"),
    "e2e_v1.py": Path("src/ecomsre_live_sandbox/e2e_v1.py"),
    "e2e_v3.py": Path("src/ecomsre_live_sandbox/e2e_v3.py"),
    "e2e_v3_contracts.py": Path("src/ecomsre_live_sandbox/e2e_v3_contracts.py"),
    "e2e_v4.py": Path("src/ecomsre_live_sandbox/e2e_v4.py"),
    "e2e_v4_contracts.py": Path("src/ecomsre_live_sandbox/e2e_v4_contracts.py"),
    "e2e_v4_cli.py": Path("scripts/live_sandbox/e2e_v4.py"),
    "environment.py": Path("src/ecomsre_live_sandbox/environment.py"),
    "image_authority.py": Path("src/ecomsre_live_sandbox/image_authority.py"),
    "instrumentation_v2.py": Path("src/ecomsre_live_sandbox/instrumentation_v2.py"),
}


def _git(repository_root: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ("git", *arguments),
        cwd=repository_root,
        check=False,
        capture_output=True,
        text=True,
        shell=False,
    )
    if completed.returncode != 0:
        raise RuntimeError("Git worktree boundary command failed")
    return completed.stdout.strip()


def _verify_worktree(config: E2EV4Config, clean_required: bool) -> str:
    branch = _git(config.repository_root, "branch", "--show-current")
    if branch != config.authority.branch:
        raise RuntimeError("v4 branch identity differs")
    head = _git(config.repository_root, "rev-parse", "HEAD")
    ancestor = subprocess.run(
        (
            "git",
            "merge-base",
            "--is-ancestor",
            config.authority.predecessor_head,
            head,
        ),
        cwd=config.repository_root,
        check=False,
        capture_output=True,
        text=True,
        shell=False,
    )
    if ancestor.returncode != 0:
        raise RuntimeError("v4 implementation is not rooted in the exact predecessor")
    if clean_required and _git(config.repository_root, "status", "--porcelain=v1"):
        raise RuntimeError("v4 runtime requires a clean implementation worktree")
    return head


def _runtime_config_paths(config: E2EV4Config) -> dict[str, Path]:
    paths = dict(_TRACKED_RUNTIME_CONFIG_FILES)
    config_relative = E2E_V4_CONFIG_RELATIVE
    if _is_v5(config):
        config_relative = Path("config/live-fault-a0-controlled-remediation-e2e-v5")
        for name in (
            "authority.json",
            "development-probes.json",
            "diagnostics.json",
            "image-authority.json.schema-or-policy",
            "projection.json",
            "reporting.json",
        ):
            paths.pop(name, None)
        paths.update(
            {
                name: config_relative / name
                for name in (
                    "authority.json",
                    "development-probes.json",
                    "diagnostics.json",
                    "no-fault-readiness.json",
                    "fault-projection.json",
                    "reporting.json",
                )
            }
        )
        paths.update(
            {
                "e2e_v5.py": Path("src/ecomsre_live_sandbox/e2e_v5.py"),
                "e2e_v5_contracts.py": Path(
                    "src/ecomsre_live_sandbox/e2e_v5_contracts.py"
                ),
                "fault_projection.py": Path(
                    "src/ecomsre_live_sandbox/fault_projection.py"
                ),
                "no_fault_readiness.py": Path(
                    "src/ecomsre_live_sandbox/no_fault_readiness.py"
                ),
                "e2e_v5_cli.py": Path("scripts/live_sandbox/e2e_v5.py"),
                "v4_image_authority_policy": (
                    E2E_V4_CONFIG_RELATIVE
                    / "image-authority.json.schema-or-policy"
                ),
            }
        )
    seen = set(paths.values())
    for package in (
        Path("src/ecomsre_live_sandbox"),
        Path("src/ecomsre_rca100"),
        Path("src/ecomsre_rca_unified"),
    ):
        for absolute in sorted((config.repository_root / package).rglob("*.py")):
            relative = absolute.relative_to(config.repository_root)
            if relative not in seen:
                paths[relative.as_posix()] = relative
                seen.add(relative)
    for relative in (
        Path("src/ecomsre/evidence/hashes.py"),
        Path("src/ecomsre/model/gateway.py"),
        Path("pyproject.toml"),
        Path("uv.lock"),
    ):
        if (config.repository_root / relative).is_file() and relative not in seen:
            paths[relative.as_posix()] = relative
            seen.add(relative)
    for config_directory in (
        config_relative,
        TELEMETRY_V3_CONFIG_RELATIVE,
        Path("config/live-telemetry-controlled-remediation-v1"),
    ):
        for absolute in sorted((config.repository_root / config_directory).rglob("*")):
            if not absolute.is_file() or absolute.is_symlink():
                continue
            relative = absolute.relative_to(config.repository_root)
            if relative not in seen:
                paths[relative.as_posix()] = relative
                seen.add(relative)
    return paths


def _runtime_config_hashes(config: E2EV4Config) -> dict[str, str]:
    return {
        name: file_sha256(config.repository_root / relative)
        for name, relative in _runtime_config_paths(config).items()
    }


def _runtime_config_aggregate(config: E2EV4Config) -> tuple[dict[str, str], str]:
    hashes = _runtime_config_hashes(config)
    return hashes, canonical_sha256(hashes)


def _consume_development_budget(
    config: E2EV4Config,
    roots: E2EV4PrivateRoots,
    *,
    implementation_commit: str,
    runtime_config_aggregate: str,
) -> tuple[int, str]:
    path = roots.control / "development-budget.json"
    budget = e2e_v3._read_budget(
        path,
        maximum=config.authority.maximum_development_integration_probes,
        schema_version=f"live-e2e.development-budget.{_schema_suffix(config)}",
    )
    runs = budget.get("runs")
    if not isinstance(runs, list):
        raise ValueError("development run history is malformed")
    if any(
        isinstance(item, Mapping)
        and item.get("verdict") == config.authority.development_success_terminal
        and item.get("runtime_config_aggregate_sha256") == runtime_config_aggregate
        for item in runs
    ):
        raise RuntimeError("development integration already passed")
    consumed = budget.get("consumed")
    if not isinstance(consumed, int):
        raise ValueError("development run count is malformed")
    if consumed >= config.authority.maximum_development_integration_probes:
        raise RuntimeError("development integration budget is exhausted")
    if runs:
        previous = runs[-1]
        if (
            isinstance(previous, Mapping)
            and previous.get("runtime_config_aggregate_sha256")
            == runtime_config_aggregate
        ):
            raise RuntimeError("identical development rerun is forbidden")
    index = consumed + 1
    run_id = f"probe-{index:02d}"
    runs.append(
        {
            "run_id": run_id,
            "implementation_commit": implementation_commit,
            "runtime_config_aggregate_sha256": runtime_config_aggregate,
            "verdict": "STARTED",
        }
    )
    budget["consumed"] = index
    write_private_json(path, budget, create_once=False)
    return index, run_id


def _complete_development_budget(
    roots: E2EV4PrivateRoots,
    *,
    run_id: str,
    verdict: str,
) -> None:
    path = roots.control / "development-budget.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    runs = value.get("runs") if isinstance(value, dict) else None
    if not isinstance(runs, list):
        raise ValueError("development run history is malformed")
    for item in runs:
        if isinstance(item, dict) and item.get("run_id") == run_id:
            if item.get("verdict") != "STARTED":
                raise ValueError("development run is already terminal")
            item["verdict"] = verdict
            break
    else:
        raise ValueError("development run is absent from the frozen budget")
    write_private_json(path, value, create_once=False)


def _collect_v4_no_fault_evidence(
    config: E2EV4Config,
    roots: E2EV4PrivateRoots,
    run_root: Path,
    tracker: Any,
    endpoints: Any,
    sleep: Callable[[float], None],
    *,
    metrics_request_json: JsonRequester | None = None,
    logs_request_json: JsonRequester | None = None,
    traces_request_json: JsonRequester | None = None,
    projection_collector: ProjectionCollector = _default_projection_inputs,
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> NoFaultEvidence:
    ordered = collect_ordered_source_evidence_v4(
        instrumentation=load_instrumentation_config(
            config.repository_root / TELEMETRY_V3_CONFIG_RELATIVE
        ),
        endpoints=endpoints,
        telemetry_root=roots.telemetry,
        run_root=run_root,
        run_id=run_root.name,
        projection=config.projection,
        tracker=tracker,
        sleep=sleep,
        metrics_request_json=metrics_request_json,
        logs_request_json=logs_request_json,
        traces_request_json=traces_request_json,
        projection_collector=projection_collector,
        now=now,
    )
    return NoFaultEvidence(
        metrics_status=ordered.metrics_status,
        logs_status=ordered.logs_status,
        traces_status=ordered.traces_status,
        source_counts=ordered.source_counts,
        invalid_refs=ordered.invalid_refs,
        visible_service_count=ordered.visible_service_count,
        scenario_truth_leaked=ordered.scenario_truth_leaked,
        projection_sha256=ordered.projection_sha256,
    )


def _preserved_source_summary(run_root: Path) -> tuple[dict[str, str], dict[str, int], int | None]:
    path = run_root / "source-results.json"
    if path.is_symlink() or not path.is_file():
        return {}, {}, None
    value = json.loads(path.read_text(encoding="utf-8"))
    results = value.get("results") if isinstance(value, Mapping) else None
    if not isinstance(results, list):
        legacy_statuses = value.get("statuses") if isinstance(value, Mapping) else None
        legacy_counts = value.get("counts") if isinstance(value, Mapping) else None
        invalid = value.get("invalid_refs") if isinstance(value, Mapping) else None
        if isinstance(legacy_statuses, Mapping) and isinstance(legacy_counts, Mapping):
            return (
                {
                    str(source): str(status)
                    for source, status in legacy_statuses.items()
                    if isinstance(source, str) and isinstance(status, str)
                },
                {
                    str(source): int(count)
                    for source, count in legacy_counts.items()
                    if isinstance(source, str) and isinstance(count, int)
                },
                invalid if isinstance(invalid, int) else None,
            )
        return {}, {}, None
    statuses: dict[str, str] = {}
    counts: dict[str, int] = {}
    for item in results:
        if not isinstance(item, Mapping):
            continue
        source = item.get("source")
        status = item.get("status")
        count = item.get("target_record_count")
        if isinstance(source, str) and isinstance(status, str) and isinstance(count, int):
            statuses[source] = status
            counts[source] = count
    invalid = value.get("invalid_ref_count") if isinstance(value, Mapping) else None
    return statuses, counts, invalid if isinstance(invalid, int) else None


def _development_failure_verdict(
    failure_code: DiagnosticFailureCode | None,
    *,
    cleanup_verdict: str,
    schema_suffix: str = "v4",
) -> str:
    marker = "V5" if schema_suffix == "v5" else "V4"
    if cleanup_verdict == "BLOCKED":
        return f"BLOCKED_E2E_{marker}_CLEANUP_INCOMPLETE"
    mapping = {
        DiagnosticFailureCode.SOURCE_BATCH_CONTRACT_FAILED: (
            f"BLOCKED_E2E_{marker}_SOURCE_BATCH_CONTRACT_FAILED"
        ),
        DiagnosticFailureCode.LIVE_TELEMETRY_SOURCE_GATE_NOT_PASSED: (
            f"BLOCKED_E2E_{marker}_LIVE_TELEMETRY_SOURCE_GATE_NOT_PASSED"
        ),
        DiagnosticFailureCode.EVIDENCE_RESOLUTION_FAILED: (
            f"BLOCKED_E2E_{marker}_EVIDENCE_RESOLUTION_FAILED"
        ),
        DiagnosticFailureCode.NO_FAULT_READINESS_FAILED: (
            "BLOCKED_E2E_V5_NO_FAULT_READINESS_FAILED"
        ),
        DiagnosticFailureCode.MULTISERVICE_PROJECTION_FAILED: (
            f"BLOCKED_E2E_{marker}_MULTISERVICE_PROJECTION_FAILED"
        ),
        DiagnosticFailureCode.IMAGE_AUTHORITY_MISMATCH: (
            f"BLOCKED_E2E_{marker}_IMAGE_AUTHORITY_MISMATCH"
        ),
        DiagnosticFailureCode.COMPOSE_STRUCTURE_IDENTITY_MISMATCH: (
            f"BLOCKED_E2E_{marker}_COMPOSE_STRUCTURE_IDENTITY_MISMATCH"
        ),
        DiagnosticFailureCode.COMPOSE_UP_FAILED: f"BLOCKED_E2E_{marker}_COMPOSE_UP_FAILED",
        DiagnosticFailureCode.SERVICE_HEALTH_TIMEOUT: (
            f"BLOCKED_E2E_{marker}_SERVICE_HEALTH_TIMEOUT"
        ),
        DiagnosticFailureCode.BASELINE_CONFIGURATION_UNAVAILABLE: (
            f"BLOCKED_E2E_{marker}_BASELINE_CONFIGURATION_UNAVAILABLE"
        ),
        DiagnosticFailureCode.BASELINE_CONFIGURATION_MISMATCH: (
            f"BLOCKED_E2E_{marker}_BASELINE_CONFIGURATION_UNAVAILABLE"
        ),
    }
    if failure_code is None:
        return f"BLOCKED_E2E_{marker}_UNCLASSIFIED_RUNTIME_FAILURE"
    return mapping.get(
        failure_code,
        f"BLOCKED_E2E_{marker}_UNCLASSIFIED_RUNTIME_FAILURE",
    )


def _write_terminal(
    tracker: Any,
    path: Path,
    terminal: Mapping[str, object],
) -> None:
    started_at = datetime.now(timezone.utc)
    tracker.journal.record(
        stage=DiagnosticStage.TERMINAL_SEALED,
        status=DiagnosticEventStatus.STARTED,
        started_at=started_at,
        input_value=terminal,
    )
    write_private_json(path, terminal, create_once=True)
    tracker.journal.record(
        stage=DiagnosticStage.TERMINAL_SEALED,
        status=DiagnosticEventStatus.PASSED,
        started_at=started_at,
        ended_at=datetime.now(timezone.utc),
        output_value={"terminal_sha256": canonical_sha256(terminal)},
    )


def run_development_probe(
    config: E2EV4Config,
    roots: E2EV4PrivateRoots,
    *,
    environment_factory: Callable[..., Any] = SandboxEnvironment,
    controller_factory: Callable[..., Any] = e2e_v3._make_controller,
    evidence_collector: Callable[..., NoFaultEvidence] = _collect_v4_no_fault_evidence,
    sleep: Callable[[float], None] = time.sleep,
    worktree_verifier: Callable[[E2EV4Config, bool], str] = _verify_worktree,
) -> dict[str, object]:
    """Consume one committed no-fault development probe and always clean after start."""

    roots.bind_lifecycle(config.authority, repository_root=config.repository_root)
    implementation_commit = worktree_verifier(config, True)
    runtime_hashes, runtime_aggregate = _runtime_config_aggregate(config)
    probe_index, run_id = _consume_development_budget(
        config,
        roots,
        implementation_commit=implementation_commit,
        runtime_config_aggregate=runtime_aggregate,
    )
    run_root = roots.probe_root(probe_index)
    for directory in (
        run_root,
        run_root / "commands",
        run_root / "exceptions",
        run_root / "snapshots",
    ):
        ensure_private_directory(directory)
    journal = DiagnosticJournal(
        run_root / "events.jsonl",
        run_kind=DiagnosticRunKind.DEVELOPMENT_PROBE,
        run_id=run_id,
    )
    tracker = e2e_v3._StageTracker(
        journal,
        ExceptionArtifactStore(run_root / "exceptions"),
    )
    execution = e2e_v3._execute_no_fault_sequence(
        cast(Any, config),
        cast(Any, roots),
        run_id=run_id,
        run_root=run_root,
        tracker=tracker,
        clean_required=True,
        environment_factory=environment_factory,
        controller_factory=controller_factory,
        evidence_collector=cast(Any, evidence_collector),
        sleep=sleep,
        worktree_verifier=cast(Any, worktree_verifier),
        run_kind=DiagnosticRunKind.DEVELOPMENT_PROBE,
        fill_legacy_no_fault_stages=False,
    )
    for stage in (
        DiagnosticStage.SCENARIO_LOCK_CREATED,
        DiagnosticStage.PLAN_TEMPLATE_CREATED,
        DiagnosticStage.APPROVAL_REQUEST_CREATED,
    ):
        tracker.skip_stage(stage, reason="DEVELOPMENT_PROBE_FORBIDS_PREAUTHORIZATION")
    private_permissions_verified = True
    try:
        roots.verify()
    except Exception as error:
        private_permissions_verified = False
        if tracker.failed_stage is None:
            tracker.fail_external(
                error,
                stage=DiagnosticStage.TERMINAL_SEALED,
                failure_code=DiagnosticFailureCode.PRIVATE_PERMISSION_VIOLATION,
            )
    source_statuses, preserved_counts, preserved_invalid = _preserved_source_summary(
        run_root
    )
    evidence = execution.evidence
    readiness = None if evidence is None else getattr(evidence, "readiness", None)
    source_counts = evidence.source_counts if evidence is not None else preserved_counts
    invalid_refs = evidence.invalid_refs if evidence is not None else preserved_invalid
    success = (
        tracker.failed_stage is None
        and execution.cleanup_verdict == "CLEAN"
        and execution.image_authority is not None
        and execution.image_verification is not None
        and evidence is not None
        and execution.state.services_healthy
        and execution.state.baseline_verified
        and evidence.metrics_status == "AVAILABLE"
        and evidence.logs_status == "AVAILABLE"
        and evidence.traces_status == "AVAILABLE"
        and all(value > 0 for value in evidence.source_counts.values())
        and evidence.invalid_refs == 0
        and (
            readiness.passed
            if readiness is not None
            else 3 <= evidence.visible_service_count <= 8
            and not evidence.scenario_truth_leaked
        )
        and private_permissions_verified
    )
    verdict = (
        config.authority.development_success_terminal
        if success
        else _development_failure_verdict(
            tracker.failure_code,
            cleanup_verdict=execution.cleanup_verdict,
            schema_suffix=_schema_suffix(config),
        )
    )
    pass_lock_created = False
    pass_lock_sha256: str | None = None
    if success:
        image_authority = cast(Any, execution.image_authority)
        image_verification = cast(Any, execution.image_verification)
        source_results_path = run_root / "source-results.json"
        readiness_path = run_root / "no-fault-readiness.json"
        projection_path = run_root / "projection-summary.json"
        pass_lock = {
            "schema_version": f"live-e2e.development-pass-lock.{_schema_suffix(config)}",
            "version": config.authority.version,
            "run_id": run_id,
            "implementation_commit": implementation_commit,
            "runtime_config_hashes": runtime_hashes,
            "runtime_config_aggregate_sha256": runtime_aggregate,
            "source_results_sha256": file_sha256(source_results_path),
            **(
                {"no_fault_readiness_sha256": file_sha256(readiness_path)}
                if readiness is not None
                else {"projection_sha256": file_sha256(projection_path)}
            ),
            "image_authority_sha256": image_authority.authority_sha256,
            "image_verification_sha256": (
                image_verification.verification_sha256
            ),
            "compose_structure_sha256": (
                image_verification.compose_structure_sha256
            ),
            "cleanup": execution.cleanup_payload,
            "terminal_relative_path": (
                run_root.relative_to(roots.root) / "terminal.json"
            ).as_posix(),
        }
        write_private_json(
            roots.control / "development-pass-lock.json",
            pass_lock,
            create_once=not (roots.control / "development-pass-lock.json").exists(),
        )
        pass_lock_created = True
        pass_lock_sha256 = canonical_sha256(pass_lock)
    exception = tracker.exception
    terminal: dict[str, object] = {
        "schema_version": f"live-e2e.development-terminal.{_schema_suffix(config)}",
        "version": config.authority.version,
        "verdict": verdict,
        "run_kind": DiagnosticRunKind.DEVELOPMENT_PROBE.value,
        "run_id": run_id,
        "probe_index": probe_index,
        "implementation_commit": implementation_commit,
        "runtime_config_aggregate_sha256": runtime_aggregate,
        "failed_stage": None if tracker.failed_stage is None else tracker.failed_stage.value,
        "last_completed_stage": None
        if tracker.root_last_completed_stage is None
        else tracker.root_last_completed_stage.value,
        "failure_code": None if tracker.failure_code is None else tracker.failure_code.value,
        "exception_type": None if exception is None else exception.exception_type,
        "exception_module": None if exception is None else exception.exception_module,
        "exception_message_sha256": None
        if exception is None
        else exception.exception_message_sha256,
        "traceback_sha256": None if exception is None else exception.traceback_sha256,
        "image_authority_sha256": None
        if execution.image_authority is None
        else execution.image_authority.authority_sha256,
        "image_verification_sha256": None
        if execution.image_verification is None
        else execution.image_verification.verification_sha256,
        "compose_structure_sha256": None
        if execution.image_verification is None
        else execution.image_verification.compose_structure_sha256,
        "compose_instance_sha256": None
        if execution.image_verification is None
        else execution.image_verification.compose_instance_sha256,
        "compose_start_requested": execution.state.compose_start_requested,
        "compose_start_returned": execution.state.compose_start_returned,
        "compose_start_return_code": execution.state.compose_start_return_code,
        "owned_resources_observed": execution.state.owned_resources_after_start,
        "services_healthy": execution.state.services_healthy,
        "baseline_verified": execution.state.baseline_verified,
        "source_availability": source_statuses,
        "source_counts": source_counts,
        "all_three_terminals_retained": len(source_statuses) == 3,
        "invalid_refs": invalid_refs,
        "visible_service_count": None
        if evidence is None
        else evidence.visible_service_count,
        "scenario_truth_leaked": None
        if evidence is None
        else evidence.scenario_truth_leaked,
        "broad_metric_service_count": None
        if readiness is None
        else readiness.broad_metric_service_count,
        "no_fault_readiness": None if readiness is None else readiness.passed,
        "no_fault_readiness_reason_codes": []
        if readiness is None
        else list(readiness.reason_codes),
        "a0_context_builder_calls": 0 if readiness is not None else 1,
        "cleanup_verdict": execution.cleanup_verdict,
        "cleanup": execution.cleanup_payload,
        "cleanup_failure_code": execution.cleanup_failure_code,
        "private_permissions_verified": private_permissions_verified,
        "development_pass_lock_created": pass_lock_created,
        "development_pass_lock_sha256": pass_lock_sha256,
        "fault_injections": 0,
        "provider_calls": 0,
        "model_calls": 0,
        "approval_records": 0,
        "forward_mutations": 0,
        "rollback_mutations": 0,
    }
    _write_terminal(tracker, run_root / "terminal.json", terminal)
    roots.verify()
    _complete_development_budget(roots, run_id=run_id, verdict=verdict)
    return terminal


def _require_development_pass(
    config: E2EV4Config,
    roots: E2EV4PrivateRoots,
    *,
    implementation_commit: str,
) -> tuple[Mapping[str, object], Mapping[str, object], Path]:
    lock_path = roots.control / "development-pass-lock.json"
    if lock_path.is_symlink() or not lock_path.is_file():
        raise RuntimeError("canonical Invocation A requires development PASS")
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    if not isinstance(lock, Mapping):
        raise RuntimeError("canonical Invocation A development-pass lock is malformed")
    runtime_hashes, runtime_aggregate = _runtime_config_aggregate(config)
    relative = lock.get("terminal_relative_path")
    if not isinstance(relative, str):
        raise RuntimeError("canonical Invocation A development-pass lock is malformed")
    terminal_path = roots.root / relative
    if terminal_path.is_symlink() or not terminal_path.is_file():
        raise RuntimeError("canonical Invocation A requires a sealed development PASS")
    terminal = json.loads(terminal_path.read_text(encoding="utf-8"))
    if not isinstance(terminal, Mapping) or any(
        (
            lock.get("schema_version")
            != f"live-e2e.development-pass-lock.{_schema_suffix(config)}",
            lock.get("implementation_commit") != implementation_commit,
            lock.get("runtime_config_hashes") != runtime_hashes,
            lock.get("runtime_config_aggregate_sha256") != runtime_aggregate,
            terminal.get("implementation_commit") != implementation_commit,
            terminal.get("verdict") != config.authority.development_success_terminal,
            terminal.get("cleanup_verdict") != "CLEAN",
            terminal.get("fault_injections") != 0,
            terminal.get("provider_calls") != 0,
            terminal.get("model_calls") != 0,
            terminal.get("forward_mutations") != 0,
            terminal.get("rollback_mutations") != 0,
        )
    ):
        raise RuntimeError("canonical Invocation A development PASS is stale or invalid")
    source_path = terminal_path.parent / "source-results.json"
    evidence_path = terminal_path.parent / (
        "no-fault-readiness.json" if _is_v5(config) else "projection-summary.json"
    )
    evidence_field = (
        "no_fault_readiness_sha256" if _is_v5(config) else "projection_sha256"
    )
    if any(
        (
            lock.get("source_results_sha256") != file_sha256(source_path),
            lock.get(evidence_field) != file_sha256(evidence_path),
        )
    ):
        raise RuntimeError("canonical Invocation A development evidence differs")
    return lock, terminal, terminal_path


def _require_exact_head_admission(
    roots: E2EV4PrivateRoots,
    *,
    implementation_commit: str,
) -> tuple[Mapping[str, object], Mapping[str, object]]:
    schema_suffix = "v5" if type(roots).__name__ == "E2EV5PrivateRoots" else "v4"
    ci_path = roots.control / "exact-head-ci.json"
    if ci_path.is_symlink() or not ci_path.is_file():
        raise RuntimeError("canonical Invocation A lacks an exact-head CI marker")
    ci = json.loads(ci_path.read_text(encoding="utf-8"))
    workflows = ci.get("workflows") if isinstance(ci, Mapping) else None
    required = {"Agent mainline", "RCAEval RE2 v2 development"}
    if (
        not isinstance(ci, Mapping)
        or ci.get("schema_version") != f"live-e2e.exact-head-ci.{schema_suffix}"
        or ci.get("implementation_commit") != implementation_commit
        or not isinstance(workflows, Mapping)
        or set(workflows) != required
        or any(
            not isinstance(workflows[name], Mapping)
            or workflows[name].get("conclusion") != "SUCCESS"
            or not isinstance(workflows[name].get("run_id"), int)
            for name in required
        )
    ):
        raise RuntimeError("canonical Invocation A exact-head CI marker differs")
    review_path = roots.control / "pre-live-review.json"
    if review_path.is_symlink() or not review_path.is_file():
        raise RuntimeError("canonical Invocation A lacks PRE_LIVE review")
    review = json.loads(review_path.read_text(encoding="utf-8"))
    if (
        not isinstance(review, Mapping)
        or review.get("schema_version")
        != f"live-e2e.pre-live-review.{schema_suffix}"
        or review.get("implementation_commit") != implementation_commit
        or review.get("verdict") != "PRE_LIVE_PASS"
        or review.get("must_fix_count") != 0
    ):
        raise RuntimeError("canonical Invocation A PRE_LIVE review differs")
    return ci, review


def _consume_canonical_budget(config: E2EV4Config, roots: E2EV4PrivateRoots) -> None:
    terminal_path = roots.invocation_a / "terminal.json"
    started_path = roots.invocation_a / "started.json"
    if any(path.exists() or path.is_symlink() for path in (terminal_path, started_path)):
        raise RuntimeError("canonical Invocation A is create-once and already consumed")
    path = roots.control / "canonical-budget.json"
    budget = e2e_v3._read_budget(
        path,
        maximum=config.authority.maximum_canonical_invocation_a_runs,
        schema_version=f"live-e2e.canonical-budget.{_schema_suffix(config)}",
    )
    if budget.get("consumed") != 0:
        raise RuntimeError("canonical Invocation A is create-once and already consumed")
    budget["consumed"] = 1
    budget["runs"] = [{"run_id": "invocation-a", "verdict": "STARTED"}]
    write_private_json(path, budget, create_once=False)
    ensure_private_directory(roots.invocation_a)
    write_private_json(
        started_path,
        {
            "schema_version": f"live-e2e.canonical-started.{_schema_suffix(config)}",
            "started_at": datetime.now(timezone.utc).isoformat(),
        },
        create_once=True,
    )


def _complete_canonical_budget(roots: E2EV4PrivateRoots, *, verdict: str) -> None:
    path = roots.control / "canonical-budget.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    runs = value.get("runs") if isinstance(value, dict) else None
    if not isinstance(runs, list) or len(runs) != 1 or not isinstance(runs[0], dict):
        raise ValueError("canonical run history is malformed")
    runs[0]["verdict"] = verdict
    write_private_json(path, value, create_once=False)


def build_plan_template(config: E2EV4Config) -> dict[str, object]:
    return LiveRemediationPlan.template_payload(config.sandbox)


def scenario_lock_manifest(
    config: E2EV4Config,
    roots: E2EV4PrivateRoots,
    *,
    implementation_commit: str,
    development_pass_lock: Mapping[str, object],
    image_authority_sha256: str,
    canonical_image_verification_sha256: str,
    compose_structure_sha256: str,
    canonical_compose_instance_sha256: str,
    normalization_policy_sha256: str,
) -> dict[str, object]:
    plan_template = build_plan_template(config)
    tracked = _runtime_config_hashes(config)
    return {
        "schema_version": f"live-e2e.scenario-lock.{_schema_suffix(config)}",
        "version": config.authority.version,
        "implementation_commit": implementation_commit,
        "implementation_branch": config.authority.branch,
        "predecessor_pr": config.authority.predecessor_pr,
        "predecessor_head": config.authority.predecessor_head,
        "predecessor_terminal": config.authority.predecessor_terminal,
        "predecessor_reason": config.authority.predecessor_reason,
        "telemetry_authority_pr": config.authority.telemetry_authority_pr,
        "telemetry_authority_head": config.authority.telemetry_authority_head,
        "telemetry_authority_semantic_sha256": (
            config.authority.telemetry_authority_semantic_sha256
        ),
        "development_pass_lock_sha256": canonical_sha256(development_pass_lock),
        **(
            {
                "canonical_source_results_sha256": file_sha256(
                    roots.invocation_a / "source-results.json"
                ),
                "canonical_no_fault_readiness_sha256": file_sha256(
                    roots.invocation_a / "no-fault-readiness.json"
                ),
            }
            if _is_v5(config)
            else {}
        ),
        "exact_head_ci_marker_sha256": file_sha256(
            roots.control / "exact-head-ci.json"
        ),
        "pre_live_review_sha256": file_sha256(
            roots.control / "pre-live-review.json"
        ),
        "image_authority_sha256": image_authority_sha256,
        "canonical_image_verification_sha256": canonical_image_verification_sha256,
        "compose_structure_sha256": compose_structure_sha256,
        "canonical_compose_instance_sha256": canonical_compose_instance_sha256,
        "normalization_policy_sha256": normalization_policy_sha256,
        "sandbox_identity": {
            "environment_id": config.sandbox.environment.environment_id,
            "sandbox_id": config.sandbox.environment.sandbox_id,
            "compose_project": config.sandbox.environment.compose_project,
            "ownership_label_key": config.sandbox.environment.sandbox_label_key,
        },
        "fault_controller_type": config.sandbox.scenario.fault_controller_type,
        "scenario_id": config.sandbox.scenario.scenario_id,
        "target_service": config.sandbox.scenario.target_service,
        "target_configuration_key": config.sandbox.scenario.target_configuration_key,
        "baseline_document_sha256": config.sandbox.scenario.baseline_document_sha256,
        "fault_document_sha256": config.sandbox.scenario.fault_document_sha256,
        "telemetry_source_hashes": {
            key: value
            for key, value in config.authority.frozen_input_hashes.items()
            if key.startswith("v3_")
        },
        "diagnostic_policy_sha256": config.authority.diagnostics_policy_sha256,
        **(
            {}
            if _is_v5(config)
            else {
                "projection_policy_sha256": (
                    config.authority.projection_policy_sha256
                )
            }
        ),
        "reporting_policy_sha256": config.authority.reporting_policy_sha256,
        "development_probe_policy_sha256": (
            config.authority.development_probe_policy_sha256
        ),
        **(
            {
                "no_fault_readiness_policy_sha256": (
                    getattr(
                        config.authority, "no_fault_readiness_policy_sha256"
                    )
                ),
                "fault_projection_policy_sha256": (
                    getattr(config.authority, "fault_projection_policy_sha256")
                ),
            }
            if _is_v5(config)
            else {}
        ),
        "a0_prompt_sha256": config.authority.a0_prompt_sha256,
        "a0_output_schema_sha256": config.authority.a0_output_schema_sha256,
        "a0_model": config.authority.a0_model,
        "sli_thresholds": config.sandbox.verification.model_dump(mode="json"),
        "provider_budget": config.sandbox.budget.model_dump(mode="json"),
        "plan_template_sha256": canonical_sha256(plan_template),
        "tracked_runtime_and_config": tracked,
    }


def _canonical_failure_verdict(
    failure_code: DiagnosticFailureCode | None,
    *,
    cleanup_verdict: str,
    schema_suffix: str = "v4",
) -> str:
    marker = "V5" if schema_suffix == "v5" else "V4"
    if cleanup_verdict == "BLOCKED":
        return f"BLOCKED_E2E_{marker}_CLEANUP_INCOMPLETE"
    mapping = {
        DiagnosticFailureCode.IMAGE_AUTHORITY_MISMATCH: (
            f"BLOCKED_E2E_{marker}_IMAGE_AUTHORITY_MISMATCH"
        ),
        DiagnosticFailureCode.COMPOSE_STRUCTURE_IDENTITY_MISMATCH: (
            f"BLOCKED_E2E_{marker}_COMPOSE_STRUCTURE_IDENTITY_MISMATCH"
        ),
        DiagnosticFailureCode.COMPOSE_UP_FAILED: f"BLOCKED_E2E_{marker}_COMPOSE_UP_FAILED",
        DiagnosticFailureCode.SERVICE_HEALTH_TIMEOUT: (
            f"BLOCKED_E2E_{marker}_SERVICE_HEALTH_TIMEOUT"
        ),
        DiagnosticFailureCode.SERVICE_EXITED_BEFORE_READY: (
            f"BLOCKED_E2E_{marker}_SERVICE_HEALTH_TIMEOUT"
        ),
        DiagnosticFailureCode.BASELINE_CONFIGURATION_UNAVAILABLE: (
            f"BLOCKED_E2E_{marker}_BASELINE_CONFIGURATION_UNAVAILABLE"
        ),
        DiagnosticFailureCode.BASELINE_CONFIGURATION_MISMATCH: (
            f"BLOCKED_E2E_{marker}_BASELINE_CONFIGURATION_UNAVAILABLE"
        ),
        DiagnosticFailureCode.SOURCE_BATCH_CONTRACT_FAILED: (
            f"BLOCKED_E2E_{marker}_SOURCE_BATCH_FAILED"
        ),
        DiagnosticFailureCode.LIVE_TELEMETRY_SOURCE_GATE_NOT_PASSED: (
            f"BLOCKED_E2E_{marker}_LIVE_TELEMETRY_SOURCE_GATE_NOT_PASSED"
        ),
        DiagnosticFailureCode.EVIDENCE_RESOLUTION_FAILED: (
            f"BLOCKED_E2E_{marker}_EVIDENCE_RESOLUTION_FAILED"
        ),
        DiagnosticFailureCode.NO_FAULT_READINESS_FAILED: (
            "BLOCKED_E2E_V5_NO_FAULT_READINESS_FAILED"
        ),
        DiagnosticFailureCode.MULTISERVICE_PROJECTION_FAILED: (
            f"BLOCKED_E2E_{marker}_MULTISERVICE_PROJECTION_FAILED"
        ),
        DiagnosticFailureCode.APPROVAL_REQUEST_WRITE_FAILED: (
            f"BLOCKED_E2E_{marker}_APPROVAL_REQUEST_WRITE_FAILED"
        ),
        DiagnosticFailureCode.CLEANUP_FAILED: f"BLOCKED_E2E_{marker}_CLEANUP_INCOMPLETE",
    }
    if failure_code is None:
        return f"BLOCKED_E2E_{marker}_UNCLASSIFIED_RUNTIME_FAILURE"
    return mapping.get(
        failure_code,
        f"BLOCKED_E2E_{marker}_UNCLASSIFIED_RUNTIME_FAILURE",
    )


def run_canonical_invocation_a(
    config: E2EV4Config,
    roots: E2EV4PrivateRoots,
    *,
    environment_factory: Callable[..., Any] = SandboxEnvironment,
    controller_factory: Callable[..., Any] = e2e_v3._make_controller,
    evidence_collector: Callable[..., NoFaultEvidence] = _collect_v4_no_fault_evidence,
    sleep: Callable[[float], None] = time.sleep,
    worktree_verifier: Callable[[E2EV4Config, bool], str] = _verify_worktree,
) -> dict[str, object]:
    roots.bind_lifecycle(config.authority, repository_root=config.repository_root)
    implementation_commit = worktree_verifier(config, True)
    development_lock, _, _ = _require_development_pass(
        config,
        roots,
        implementation_commit=implementation_commit,
    )
    _require_exact_head_admission(roots, implementation_commit=implementation_commit)
    _consume_canonical_budget(config, roots)
    run_root = roots.invocation_a
    for directory in (
        run_root,
        run_root / "commands",
        run_root / "exceptions",
        run_root / "snapshots",
    ):
        ensure_private_directory(directory)
    journal = DiagnosticJournal(
        run_root / "events.jsonl",
        run_kind=DiagnosticRunKind.CANONICAL_INVOCATION_A,
        run_id="invocation-a",
    )
    tracker = e2e_v3._StageTracker(
        journal,
        ExceptionArtifactStore(run_root / "exceptions"),
    )
    execution = e2e_v3._execute_no_fault_sequence(
        cast(Any, config),
        cast(Any, roots),
        run_id="invocation-a",
        run_root=run_root,
        tracker=tracker,
        clean_required=True,
        environment_factory=environment_factory,
        controller_factory=controller_factory,
        evidence_collector=cast(Any, evidence_collector),
        sleep=sleep,
        worktree_verifier=cast(Any, worktree_verifier),
        run_kind=DiagnosticRunKind.CANONICAL_INVOCATION_A,
        fill_legacy_no_fault_stages=False,
        expected_structure_sha256=cast(
            str, development_lock["compose_structure_sha256"]
        ),
    )
    evidence = execution.evidence
    readiness = None if evidence is None else getattr(evidence, "readiness", None)
    scenario_lock_created = False
    plan_template_created = False
    approval_request_created = False
    approval_command: str | None = None
    request: ApprovalRequest | None = None
    eligible = (
        tracker.failed_stage is None
        and execution.cleanup_verdict == "CLEAN"
        and execution.image_authority is not None
        and execution.image_verification is not None
        and evidence is not None
        and execution.state.services_healthy
        and execution.state.baseline_verified
        and evidence.metrics_status == "AVAILABLE"
        and evidence.logs_status == "AVAILABLE"
        and evidence.traces_status == "AVAILABLE"
        and evidence.invalid_refs == 0
        and (
            readiness.passed
            if readiness is not None
            else 3 <= evidence.visible_service_count <= 8
            and not evidence.scenario_truth_leaked
        )
    )
    if eligible:
        try:
            image_authority = cast(Any, execution.image_authority)
            image_verification = cast(Any, execution.image_verification)
            lock = scenario_lock_manifest(
                config,
                roots,
                implementation_commit=implementation_commit,
                development_pass_lock=development_lock,
                image_authority_sha256=image_authority.authority_sha256,
                canonical_image_verification_sha256=(
                    image_verification.verification_sha256
                ),
                compose_structure_sha256=image_verification.compose_structure_sha256,
                canonical_compose_instance_sha256=(
                    image_verification.compose_instance_sha256
                ),
                normalization_policy_sha256=(
                    image_verification.normalization_policy_sha256
                ),
            )
            tracker.execute(
                DiagnosticStage.SCENARIO_LOCK_CREATED,
                lambda: write_private_json(
                    roots.control / "scenario-lock.json", lock, create_once=True
                ),
                failure_code=DiagnosticFailureCode.SCENARIO_LOCK_WRITE_FAILED,
            )
            scenario_lock_created = True
            template = build_plan_template(config)
            tracker.execute(
                DiagnosticStage.PLAN_TEMPLATE_CREATED,
                lambda: write_private_json(
                    roots.control / "plan-template.json", template, create_once=True
                ),
                failure_code=DiagnosticFailureCode.PLAN_TEMPLATE_WRITE_FAILED,
            )
            plan_template_created = True
            request = create_approval_request(cast(Any, config), scenario_lock=lock)
            tracker.execute(
                DiagnosticStage.APPROVAL_REQUEST_CREATED,
                lambda: write_private_json(
                    roots.control / "approval-request.json", request, create_once=True
                ),
                failure_code=DiagnosticFailureCode.APPROVAL_REQUEST_WRITE_FAILED,
            )
            approval_request_created = True
            approval_command = (
                "uv run --with pyarrow python -m scripts.live_sandbox."
                f"e2e_{_schema_suffix(config)} "
                "--private-root ~/.ecomsre/private/"
                f"{config.authority.version} approve "
                "--approver \"<HUMAN_NAME>\" "
                f"--phrase \"APPROVE {request.scenario_id} "
                f"{request.plan_template_sha256}\""
            )
        except Exception:
            pass
    else:
        for stage in (
            DiagnosticStage.SCENARIO_LOCK_CREATED,
            DiagnosticStage.PLAN_TEMPLATE_CREATED,
            DiagnosticStage.APPROVAL_REQUEST_CREATED,
        ):
            tracker.skip_stage(stage, reason="CANONICAL_PREFLIGHT_OR_CLEANUP_NOT_ADMITTED")
    private_permissions_verified = True
    try:
        roots.verify()
    except Exception as error:
        private_permissions_verified = False
        if tracker.failed_stage is None:
            tracker.fail_external(
                error,
                stage=DiagnosticStage.TERMINAL_SEALED,
                failure_code=DiagnosticFailureCode.PRIVATE_PERMISSION_VIOLATION,
            )
    success = (
        eligible
        and tracker.failed_stage is None
        and scenario_lock_created
        and plan_template_created
        and approval_request_created
        and request is not None
        and approval_command is not None
        and private_permissions_verified
    )
    verdict = (
        config.authority.invocation_a_terminal
        if success
        else _canonical_failure_verdict(
            tracker.failure_code,
            cleanup_verdict=execution.cleanup_verdict,
            schema_suffix=_schema_suffix(config),
        )
    )
    source_statuses, source_counts, invalid_refs = _preserved_source_summary(run_root)
    exception = tracker.exception
    terminal: dict[str, object] = {
        "schema_version": (
            f"live-e2e.canonical-invocation-a-terminal.{_schema_suffix(config)}"
        ),
        "version": config.authority.version,
        "verdict": verdict,
        "run_kind": DiagnosticRunKind.CANONICAL_INVOCATION_A.value,
        "run_id": "invocation-a",
        "run_count": 1,
        "implementation_commit": implementation_commit,
        "development_pass_lock_sha256": canonical_sha256(development_lock),
        "failed_stage": None if tracker.failed_stage is None else tracker.failed_stage.value,
        "last_completed_stage": None
        if tracker.root_last_completed_stage is None
        else tracker.root_last_completed_stage.value,
        "failure_code": None if tracker.failure_code is None else tracker.failure_code.value,
        "exception_type": None if exception is None else exception.exception_type,
        "exception_module": None if exception is None else exception.exception_module,
        "exception_message_sha256": None
        if exception is None
        else exception.exception_message_sha256,
        "traceback_sha256": None if exception is None else exception.traceback_sha256,
        "compose_start_requested": execution.state.compose_start_requested,
        "compose_start_returned": execution.state.compose_start_returned,
        "compose_start_return_code": execution.state.compose_start_return_code,
        "image_authority_sha256": None
        if execution.image_authority is None
        else execution.image_authority.authority_sha256,
        "image_verification_sha256": None
        if execution.image_verification is None
        else execution.image_verification.verification_sha256,
        "compose_structure_sha256": None
        if execution.image_verification is None
        else execution.image_verification.compose_structure_sha256,
        "compose_instance_sha256": None
        if execution.image_verification is None
        else execution.image_verification.compose_instance_sha256,
        "owned_resources_observed": execution.state.owned_resources_after_start,
        "services_healthy": execution.state.services_healthy,
        "baseline_verified": execution.state.baseline_verified,
        "source_availability": source_statuses,
        "source_counts": source_counts,
        "all_three_terminals_retained": len(source_statuses) == 3,
        "invalid_refs": invalid_refs,
        "visible_service_count": None
        if evidence is None
        else evidence.visible_service_count,
        "scenario_truth_leaked": None
        if evidence is None
        else evidence.scenario_truth_leaked,
        "broad_metric_service_count": None
        if readiness is None
        else readiness.broad_metric_service_count,
        "no_fault_readiness": None if readiness is None else readiness.passed,
        "no_fault_readiness_reason_codes": []
        if readiness is None
        else list(readiness.reason_codes),
        "a0_context_builder_calls": 0 if readiness is not None else 1,
        "scenario_lock_created": scenario_lock_created,
        "plan_template_created": plan_template_created,
        "approval_request_created": approval_request_created,
        "approval_request_id": None if request is None else request.approval_request_id,
        "plan_template_sha256": None if request is None else request.plan_template_sha256,
        "approval_expires_at": None
        if request is None
        else request.expires_at.isoformat(),
        "approval_command": approval_command,
        "cleanup_verdict": execution.cleanup_verdict,
        "cleanup": execution.cleanup_payload,
        "cleanup_failure_code": execution.cleanup_failure_code,
        "private_permissions_verified": private_permissions_verified,
        "fault_injections": 0,
        "provider_calls": 0,
        "model_calls": 0,
        "forward_mutations": 0,
        "rollback_mutations": 0,
        "codex_self_approved": False,
        "human_approval_record_present": False,
    }
    _write_terminal(tracker, run_root / "terminal.json", terminal)
    roots.verify()
    _complete_canonical_budget(roots, verdict=verdict)
    return terminal


def record_human_approval_for_invocation_b(
    config: E2EV4Config,
    roots: E2EV4PrivateRoots,
    *,
    approver: str,
    phrase: str,
) -> HumanApprovalRecord:
    roots.bind_lifecycle(config.authority, repository_root=config.repository_root)
    terminal_path = roots.invocation_a / "terminal.json"
    if terminal_path.is_symlink() or not terminal_path.is_file():
        raise RuntimeError("human approval requires canonical Invocation A success")
    terminal = json.loads(terminal_path.read_text(encoding="utf-8"))
    if not isinstance(terminal, Mapping) or any(
        (
            terminal.get("verdict") != config.authority.invocation_a_terminal,
            terminal.get("cleanup_verdict") != "CLEAN",
            terminal.get("scenario_lock_created") is not True,
            terminal.get("plan_template_created") is not True,
            terminal.get("approval_request_created") is not True,
            terminal.get("codex_self_approved") is not False,
        )
    ):
        raise RuntimeError("human approval requires canonical Invocation A success")
    lock_path = roots.control / "scenario-lock.json"
    template_path = roots.control / "plan-template.json"
    request_path = roots.control / "approval-request.json"
    if any(path.is_symlink() or not path.is_file() for path in (lock_path, template_path, request_path)):
        raise RuntimeError("human approval lacks the frozen canonical artifacts")
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    template = json.loads(template_path.read_text(encoding="utf-8"))
    request = ApprovalRequest.model_validate_json(request_path.read_text(encoding="utf-8"))
    if (
        not isinstance(lock, Mapping)
        or request.scenario_lock_sha256 != canonical_sha256(lock)
        or request.plan_template_sha256 != canonical_sha256(template)
    ):
        raise RuntimeError("human approval request binding differs")
    record = record_human_approval(
        request,
        approver=approver,
        phrase=phrase,
        now=datetime.now(timezone.utc),
        destination=roots.control / "human-approval.json",
    )
    roots.verify()
    return record


_LEGAL_INVOCATION_B_TERMINALS = frozenset(
    {
        "LIVE_FAULT_A0_CONTROLLED_REMEDIATION_E2E_V4_PASSED_READY_FOR_REVIEW",
        "BLOCKED_PROVIDER_PREFLIGHT",
        "BLOCKED_E2E_V4_IMAGE_AUTHORITY_MISMATCH",
        "BLOCKED_E2E_V4_COMPOSE_STRUCTURE_IDENTITY_MISMATCH",
        "BLOCKED_FAULT_IMPACT_NOT_OBSERVED",
        "BLOCKED_LIVE_TELEMETRY_SOURCE_UNAVAILABLE",
        "BLOCKED_BOUNDED_MULTISERVICE_PROJECTION_UNAVAILABLE",
        "LIVE_DIAGNOSIS_GATE_NOT_PASSED_NO_REMEDIATION",
        "BLOCKED_POLICY_REJECTED",
        "CONTROLLED_REMEDIATION_NOT_VERIFIED_ROLLBACK_COMPLETED",
        "BLOCKED_ROLLBACK_FAILED_MANUAL_CLEANUP_REQUIRED",
        "BLOCKED_CLEANUP_INCOMPLETE",
    }
)


def _public_result_v4(
    config: E2EV4Config,
    terminal: Mapping[str, object],
) -> dict[str, object]:
    public = {
        "schema_version": "live-e2e.public-result.v4",
        "version": config.authority.version,
        "verdict": terminal.get("verdict"),
        "implementation_commit": terminal.get("implementation_commit"),
        "result_head": _git(config.repository_root, "rev-parse", "HEAD"),
        "source_availability": terminal.get("source_availability", {}),
        "source_counts": terminal.get("source_counts", {}),
        "invalid_refs": terminal.get("invalid_refs"),
        "visible_service_count": terminal.get("visible_service_count"),
        "fault_injections": terminal.get("fault_injections", 0),
        "provider_calls": terminal.get("provider_calls", 0),
        "model_calls": terminal.get("model_calls", 0),
        "forward_mutations": terminal.get("forward_mutations", 0),
        "rollback_mutations": terminal.get("rollback_mutations", 0),
        "fault_impact_gate": terminal.get("fault_impact_passed"),
        "diagnosis_gate": terminal.get("diagnosis_gate"),
        "diagnosis_correct": terminal.get("diagnosis_correct"),
        "plan_action": terminal.get("plan_action"),
        "approval_mode": "HUMAN_PREAUTHORIZED_FROZEN_REMEDIATION_RUNBOOK",
        "policy_verdict": terminal.get("policy_verdict"),
        "recovery_verification": terminal.get("recovery_verification_passed"),
        "rollback_exact_hash_verified": terminal.get(
            "rollback_exact_hash_verified"
        ),
        "cleanup": terminal.get("cleanup"),
        "claim_boundary": list(config.reporting.claim_boundary),
    }
    public["semantic_sha256"] = canonical_sha256(public)
    return public


def verify_public_result(config: E2EV4Config, value: Mapping[str, object]) -> None:
    verdict = value.get("verdict")
    if verdict not in _LEGAL_INVOCATION_B_TERMINALS:
        raise ValueError("public Invocation B terminal is not legal")
    semantic = value.get("semantic_sha256")
    core = dict(value)
    core.pop("semantic_sha256", None)
    if semantic != canonical_sha256(core):
        raise ValueError("public Invocation B semantic hash differs")
    if scan_public_e2e_payload(value):
        raise ValueError("public Invocation B result contains private or control data")
    cleanup = value.get("cleanup")
    if not isinstance(cleanup, Mapping):
        raise ValueError("public Invocation B cleanup aggregate is missing")
    if verdict == config.authority.invocation_b_success and any(
        (
            value.get("fault_injections") != 1,
            value.get("provider_calls") != 2,
            value.get("model_calls") != 1,
            value.get("forward_mutations") != 1,
            value.get("rollback_mutations") != 0,
            value.get("fault_impact_gate") is not True,
            value.get("diagnosis_gate") is not True,
            value.get("diagnosis_correct") is not True,
            value.get("plan_action") != "RESTORE_FROZEN_SERVICE_CONFIGURATION",
            value.get("policy_verdict") != "ALLOW",
            value.get("recovery_verification") is not True,
            cleanup.get("verdict") != "CLEAN",
        )
    ):
        raise ValueError("public Invocation B success aggregates do not recompute")


def _write_new_public(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())


def _write_public_outputs_v4(
    config: E2EV4Config,
    terminal: Mapping[str, object],
) -> tuple[str, str, str]:
    public = _public_result_v4(config, terminal)
    verify_public_result(config, public)
    paths = (
        config.repository_root / config.reporting.public_result_json,
        config.repository_root / config.reporting.public_result_markdown,
        config.repository_root / config.reporting.public_human_brief,
    )
    _write_new_public(
        paths[0],
        json.dumps(public, ensure_ascii=False, sort_keys=True).encode("utf-8"),
    )
    _write_new_public(
        paths[1],
        (
            "# Live Fault to A0 Controlled Remediation E2E v4\n\n"
            f"**Verdict:** `{public['verdict']}`\n\n"
            "This is one preregistered local Sandbox scenario using a "
            "HUMAN_PREAUTHORIZED_FROZEN_REMEDIATION_RUNBOOK. It is not production, "
            "autonomous production remediation, an external benchmark, or a Multi-Agent claim.\n"
        ).encode("utf-8"),
    )
    _write_new_public(
        paths[2],
        (
            "# Live Fault → A0 → Controlled Remediation v4 — Human Brief\n\n"
            "本结果仅代表一个本地 Sandbox、一个预注册场景和一个人工预授权的冻结修复 runbook。"
            "人工授权发生在实际诊断之前，不代表人工审阅了实际诊断；不构成生产自治或 Multi-Agent 优越性声明。\n"
        ).encode("utf-8"),
    )
    return cast(
        tuple[str, str, str],
        tuple(path.relative_to(config.repository_root).as_posix() for path in paths),
    )


def _verify_scenario_lock_for_invocation_b(
    config: E2EV4Config,
    roots: E2EV4PrivateRoots,
    *,
    locked: Mapping[str, object],
    implementation_commit: str,
) -> None:
    development_lock, _, _ = _require_development_pass(
        config,
        roots,
        implementation_commit=implementation_commit,
    )
    expected = scenario_lock_manifest(
        config,
        roots,
        implementation_commit=implementation_commit,
        development_pass_lock=development_lock,
        image_authority_sha256=cast(str, locked.get("image_authority_sha256")),
        canonical_image_verification_sha256=cast(
            str, locked.get("canonical_image_verification_sha256")
        ),
        compose_structure_sha256=cast(str, locked.get("compose_structure_sha256")),
        canonical_compose_instance_sha256=cast(
            str, locked.get("canonical_compose_instance_sha256")
        ),
        normalization_policy_sha256=cast(
            str, locked.get("normalization_policy_sha256")
        ),
    )
    if canonical_sha256(locked) != canonical_sha256(expected):
        raise RuntimeError("Invocation B scenario lock differs from frozen runtime files")


def run_invocation_b(
    config: E2EV4Config,
    roots: E2EV4PrivateRoots,
    **kwargs: Any,
) -> dict[str, object]:
    """Delegate the frozen complete live path to the shared v3 implementation."""

    public_writer = kwargs.pop("public_writer", _write_public_outputs_v4)
    return e2e_v3.run_invocation_b(
        cast(Any, config),
        cast(Any, roots),
        public_writer=cast(Any, public_writer),
        **kwargs,
    )


__all__ = [
    "build_plan_template",
    "record_human_approval_for_invocation_b",
    "run_canonical_invocation_a",
    "run_development_probe",
    "run_invocation_b",
    "scenario_lock_manifest",
    "verify_public_result",
]
