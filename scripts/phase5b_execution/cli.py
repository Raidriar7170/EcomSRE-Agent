"""Provider-free commands for the frozen Phase 5B execution harness."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile
import time
from typing import Any, cast

from ecomsre.phase5b.cli import verify_protocol
from ecomsre.phase5b.contracts import ExecutionSchedule
from ecomsre.phase5b.freeze import verify_freeze_manifest
from ecomsre.phase5b.protocol import load_strict_json

from scripts.phase5b_execution.ablation import (
    _AblationStore,
    UnsupportedFrozenAblationExecutor,
    build_ablation_schedule,
    run_ablation_schedule,
    run_mock_ablation_rehearsal,
)
from scripts.phase5b_execution.admission import (
    provider_configuration_preflight,
    provider_configuration_fingerprint,
    require_frozen_runtime_source,
    require_merged_execution_source,
    require_provider_configuration,
    require_scored_execution_authorization,
    safe_execution_environment,
    verify_agent_visible_inputs,
)
from scripts.phase5b_execution.canary import run_provider_canary, verify_canary_chain
from scripts.phase5b_execution.checkpoint import (
    CheckpointStore,
    _ensure_private_directory,
    _entry_exists,
    _load_canonical,
)
from scripts.phase5b_execution.contracts import (
    ExecutionCompleteSeal,
    ExecutionFreezeManifest,
    ExecutionStartedRecord,
    ExecutionUnblindingRecord,
    FinalEvaluationReport,
    canonical_json_bytes,
    sha256_canonical,
)
from scripts.phase5b_execution.freeze import (
    EXECUTION_FREEZE_RELATIVE,
    build_execution_freeze_manifest,
    verify_execution_freeze_manifest,
)
from scripts.phase5b_execution.lifecycle import (
    EXECUTION_COMPLETE_SEAL,
    create_execution_started_record,
    create_execution_unblinding_record,
    seal_execution_complete,
    verify_execution_complete_chain,
    verify_execution_started_chain,
    verify_main_execution_complete,
    verify_unblinding_chain,
)
from scripts.phase5b_execution.runner import (
    MockScheduledExecutor,
    run_frozen_schedule,
)
from scripts.phase5b_execution.worker import IsolatedScheduledExecutor
from scripts.phase5b_execution.scoring import (
    freeze_final_report,
    verify_final_report,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
PROTOCOL_CONFIG = PROJECT_ROOT / "config/phase5b"
DEFAULT_MOCK_ROOT = PROJECT_ROOT / "artifacts/phase5b-execution/mock-rehearsal"
EXPECTED_ACTUAL_ROOT = (
    Path.home() / ".ecomsre-private/phase5b-v1-execution"
).resolve(strict=False)
_EXECUTION_BASE_COMMIT = "2cf6147b62394921727bde2f3094a72caa1563d9"


def _print(payload: dict[str, object]) -> None:
    print(json.dumps(payload, sort_keys=True, separators=(",", ":")))


def _readiness_status(
    record: (
        ExecutionFreezeManifest
        | ExecutionStartedRecord
        | ExecutionCompleteSeal
        | ExecutionUnblindingRecord
        | FinalEvaluationReport
    ),
) -> dict[str, object]:
    return {
        "main_evaluation_ready": record.main_evaluation_ready,
        "ablation_slot_count": record.ablation_slot_count,
        "ablation_implementation_available": (
            record.ablation_implementation_available
        ),
        "ablation_evidence_available": record.ablation_evidence_available,
        "ablation_primary_eligible": record.ablation_primary_eligible,
        "ablation_disposition": record.ablation_disposition,
    }


def _write_atomic(path: Path, payload: object) -> None:
    _ensure_private_directory(path.parent)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb", closefd=True) as stream:
            stream.write(canonical_json_bytes(payload))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)


def _load_object(path: Path) -> dict[str, object]:
    def strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate mock report JSON key")
            result[key] = value
        return result

    def reject_constant(value: str) -> None:
        raise ValueError(f"non-finite mock report JSON constant: {value}")

    details = path.lstat()
    if stat.S_ISLNK(details.st_mode) or not stat.S_ISREG(details.st_mode):
        raise ValueError("mock report must be a regular non-symlink file")
    payload = json.loads(
        path.read_text(encoding="utf-8"),
        object_pairs_hook=strict_object,
        parse_constant=reject_constant,
    )
    if not isinstance(payload, dict):
        raise ValueError("mock execution report must be an object")
    if path.read_bytes() != canonical_json_bytes(payload):
        raise ValueError("mock execution report is not canonical")
    return cast(dict[str, object], payload)


def execution_preflight() -> dict[str, object]:
    protocol_manifest = verify_freeze_manifest(
        PROJECT_ROOT,
        PROTOCOL_CONFIG / "freeze-manifest.v1.json",
    )
    protocol = verify_protocol()
    execution = verify_execution_freeze_manifest(PROJECT_ROOT)
    return {
        "status": "PHASE5B_EXECUTION_PREFLIGHT_VERIFIED",
        "evaluation_version": "phase5b.v1",
        "protocol_frozen_file_count": len(protocol_manifest.frozen_files),
        "protocol_status": protocol["status"],
        "main_run_count": execution.main_run_count,
        "ablation_run_count": execution.ablation_run_count,
        **_readiness_status(execution),
        "provider_calls": 0,
        "ground_truth_reads": 0,
        "unblinded": False,
        "execution_started": False,
    }


def write_mock_rehearsal(output_root: Path) -> dict[str, object]:
    schedule = load_strict_json(
        PROTOCOL_CONFIG / "execution-schedule.v1.json",
        ExecutionSchedule,
    )
    executor = MockScheduledExecutor()
    main = run_frozen_schedule(
        schedule=schedule,
        output_root=output_root / "main",
        executor=executor,
        sleeper=lambda _seconds: None,
    )
    ablation = run_mock_ablation_rehearsal(
        registry_path=PROTOCOL_CONFIG / "ablation-registry.v1.json",
        output_root=output_root / "ablation",
        sleeper=lambda _seconds: None,
    )
    _write_atomic(output_root / "main-report.json", main)
    _write_atomic(output_root / "ablation-report.json", ablation)
    return {
        "status": "MOCK_EXECUTION_REHEARSAL_COMPLETE",
        "evidence_class": "MOCK_EXECUTION_REHEARSAL",
        "not_model_evidence": True,
        "main_terminal_records": main["unique_terminal_records"],
        "ablation_terminal_records": ablation["unique_terminal_records"],
        "main_evaluation_ready": ablation["main_evaluation_ready"],
        "ablation_slot_count": ablation["ablation_slot_count"],
        "ablation_implementation_available": ablation[
            "ablation_implementation_available"
        ],
        "ablation_evidence_available": ablation["ablation_evidence_available"],
        "ablation_primary_eligible": ablation["ablation_primary_eligible"],
        "ablation_disposition": ablation["ablation_disposition"],
        "provider_calls": 0,
        "ground_truth_reads": 0,
    }


def verify_mock_rehearsal(output_root: Path) -> dict[str, object]:
    main = _load_object(output_root / "main-report.json")
    ablation = _load_object(output_root / "ablation-report.json")
    required_main = {
        "evidence_class": "MOCK_EXECUTION_REHEARSAL",
        "not_model_evidence": True,
        "run_count": 180,
        "unique_terminal_records": 180,
        "provider_network_calls": 0,
        "ground_truth_reads": 0,
        "all_checkpoints_closed": True,
        "hidden_retry": False,
        "scripted_fallback": False,
    }
    required_ablation = {
        "evidence_class": "MOCK_EXECUTION_REHEARSAL",
        "not_model_evidence": True,
        "ablation_run_count": 38,
        "unique_terminal_records": 38,
        "diagnosis_run_count": 36,
        "remediation_run_count": 2,
        "provider_network_calls": 0,
        "ground_truth_reads": 0,
        "all_checkpoints_closed": True,
        "primary_eligible": False,
        "main_evaluation_ready": True,
        "ablation_slot_count": 38,
        "ablation_implementation_available": False,
        "ablation_evidence_available": False,
        "ablation_primary_eligible": False,
        "ablation_disposition": "ABLATION_NOT_IMPLEMENTED_IN_FROZEN_HARNESS",
    }
    if any(main.get(key) != value for key, value in required_main.items()):
        raise ValueError("main mock rehearsal report is invalid")
    if any(ablation.get(key) != value for key, value in required_ablation.items()):
        raise ValueError("ablation mock rehearsal report is invalid")
    main_records = tuple((output_root / "main/raw").glob("*.json"))
    main_markers = tuple((output_root / "main/attempts").glob("*.json"))
    ablation_records = tuple(
        (output_root / "ablation/ablation-raw").glob("*.json")
    )
    ablation_markers = tuple(
        (output_root / "ablation/ablation-attempts").glob("*.json")
    )
    if (
        len(main_records) != 180
        or main_markers
        or len(ablation_records) != 38
        or ablation_markers
    ):
        raise ValueError("mock rehearsal checkpoint closure is invalid")
    schedule = load_strict_json(
        PROTOCOL_CONFIG / "execution-schedule.v1.json",
        ExecutionSchedule,
    )
    main_store = CheckpointStore(output_root / "main")
    record_hashes: dict[str, str] = {}
    for scheduled in schedule.runs:
        main_record = main_store.load_record(scheduled.run_id)
        if main_record is None or (
            main_record.template_id != scheduled.template_id
            or main_record.seed_id != scheduled.seed_id
            or main_record.variant != scheduled.variant
            or main_record.evidence_class != "MOCK_EXECUTION_REHEARSAL"
            or main_record.usage.provider_network_calls != 0
        ):
            raise ValueError("mock main record differs from the frozen schedule")
        record_hashes[main_record.run_id] = main_record.record_sha256
    bundle = main.get("bundle_manifest")
    if not isinstance(bundle, dict) or bundle.get("record_sha256_by_run_id") != record_hashes:
        raise ValueError("mock main bundle does not bind the raw records")
    ablation_store = _AblationStore(output_root / "ablation")
    ablation_hashes: dict[str, str] = {}
    for request in build_ablation_schedule(
        PROTOCOL_CONFIG / "ablation-registry.v1.json"
    ):
        ablation_record = ablation_store.load(request.ablation_run_id)
        if ablation_record is None or (
            ablation_record.ablation_id != request.ablation_id
            or ablation_record.template_id != request.template_id
            or ablation_record.seed_id != request.seed_id
            or ablation_record.run_kind != request.run_kind
            or ablation_record.evidence_class != "MOCK_EXECUTION_REHEARSAL"
            or ablation_record.usage.provider_network_calls != 0
        ):
            raise ValueError("mock ablation record differs from the frozen registry")
        ablation_hashes[ablation_record.ablation_run_id] = (
            ablation_record.record_sha256
        )
    seal = ablation.get("seal")
    registry_path = PROTOCOL_CONFIG / "ablation-registry.v1.json"
    if not isinstance(seal, dict) or (
        seal.get("report_sha256") != sha256_canonical(ablation_hashes)
        or seal.get("ablation_registry_sha256")
        != hashlib.sha256(registry_path.read_bytes()).hexdigest()
    ):
        raise ValueError("mock ablation seal does not bind the raw records")
    return {
        "status": "MOCK_EXECUTION_REHEARSAL_VERIFIED",
        "main_terminal_records": 180,
        "ablation_terminal_records": 38,
        "main_evaluation_ready": True,
        "ablation_slot_count": 38,
        "ablation_implementation_available": False,
        "ablation_evidence_available": False,
        "ablation_primary_eligible": False,
        "ablation_disposition": "ABLATION_NOT_IMPLEMENTED_IN_FROZEN_HARNESS",
        "provider_calls": 0,
        "ground_truth_reads": 0,
    }


def _require_actual_execution_authorization(
    environment: dict[str, str],
) -> None:
    require_scored_execution_authorization(environment)


def _reject_premature_truth_environment(environment: dict[str, str]) -> None:
    if any("GROUND_TRUTH" in key.upper() for key in environment):
        raise PermissionError("ground-truth environment is forbidden before unblinding")


def _require_external_output_root(
    output_root: Path,
    *,
    create: bool = False,
) -> Path:
    root = output_root.resolve(strict=False)
    if root != EXPECTED_ACTUAL_ROOT:
        raise ValueError("actual execution root differs from the frozen private root")
    current = Path.home().resolve(strict=True)
    for part in (".ecomsre-private", "phase5b-v1-execution"):
        current = current / part
        if _entry_exists(current) and current.is_symlink():
            raise ValueError("actual execution root cannot traverse a symlink")
    if create:
        _ensure_private_directory(EXPECTED_ACTUAL_ROOT.parent)
        _ensure_private_directory(EXPECTED_ACTUAL_ROOT)
    elif not _entry_exists(EXPECTED_ACTUAL_ROOT):
        raise FileNotFoundError("actual execution root does not exist")
    else:
        _ensure_private_directory(EXPECTED_ACTUAL_ROOT)
    return root


def _verify_visible_execution_inputs(
    environment: dict[str, str],
) -> None:
    _reject_premature_truth_environment(environment)
    visible = environment.get("PHASE5B_AGENT_VISIBLE_ROOT")
    manifest = environment.get("PHASE5B_HIDDEN_PACK_MANIFEST")
    if not visible or not manifest:
        raise ValueError("agent-visible root and public pack manifest are required")
    freeze = verify_execution_freeze_manifest(PROJECT_ROOT)
    verify_agent_visible_inputs(
        agent_visible_root=Path(visible),
        hidden_pack_manifest_path=Path(manifest),
        expected_manifest_sha256=freeze.hidden_pack_manifest_sha256,
        expected_agent_visible_pack_sha256=freeze.agent_visible_pack_sha256,
    )


def _runtime_integrity_guard(
    *,
    execution_root: Path,
    started: ExecutionStartedRecord,
    environment: dict[str, str],
) -> None:
    config = require_provider_configuration(environment)
    config_sha256 = provider_configuration_fingerprint(config)
    verify_canary_chain(
        execution_root,
        expected_provider_configuration_sha256=config_sha256,
    )
    require_frozen_runtime_source(
        PROJECT_ROOT,
        expected_execution_freeze_sha256=started.execution_freeze_sha256,
        expected_source_commit=started.source_commit,
    )
    _verify_visible_execution_inputs(environment)


def run_provider_preflight(environment: dict[str, str]) -> dict[str, object]:
    _reject_premature_truth_environment(environment)
    return {
        "status": "PHASE5B_PROVIDER_PREFLIGHT",
        **provider_configuration_preflight(environment),
        "provider_calls": 0,
        "ground_truth_reads": 0,
    }


def enter_execution(
    *,
    output_root: Path,
    environment: dict[str, str],
) -> dict[str, object]:
    _require_actual_execution_authorization(environment)
    execution_root = _require_external_output_root(output_root)
    _verify_visible_execution_inputs(environment)
    source_commit, origin_main_commit = require_merged_execution_source(PROJECT_ROOT)
    config = require_provider_configuration(environment)
    config_sha256 = provider_configuration_fingerprint(config)
    verify_canary_chain(
        execution_root,
        expected_provider_configuration_sha256=config_sha256,
    )
    record = create_execution_started_record(
        project_root=PROJECT_ROOT,
        execution_root=execution_root,
        source_commit=source_commit,
        origin_main_commit=origin_main_commit,
        provider_configuration_sha256=config_sha256,
    )
    return {
        "status": record.to_state,
        "completed_main_runs": record.completed_main_runs,
        "completed_ablation_runs": record.completed_ablation_runs,
        **_readiness_status(record),
        "provider_calls": 0,
        "ground_truth_reads": 0,
        "irreversible": True,
    }


def run_actual_main_execution(
    *,
    output_root: Path,
    environment: dict[str, str],
) -> dict[str, object]:
    _require_actual_execution_authorization(environment)
    execution_root = _require_external_output_root(output_root)
    _verify_visible_execution_inputs(environment)
    started = verify_execution_started_chain(PROJECT_ROOT, execution_root)
    _runtime_integrity_guard(
        execution_root=execution_root,
        started=started,
        environment=environment,
    )
    schedule = load_strict_json(
        PROTOCOL_CONFIG / "execution-schedule.v1.json",
        ExecutionSchedule,
    )
    report = run_frozen_schedule(
        schedule=schedule,
        output_root=execution_root / "main",
        executor=IsolatedScheduledExecutor(
            project_root=PROJECT_ROOT,
            environment=safe_execution_environment(environment),
        ),
        sleeper=time.sleep,
        evidence_class="ACTUAL_SCORED",
        integrity_guard=lambda: _runtime_integrity_guard(
            execution_root=execution_root,
            started=started,
            environment=environment,
        ),
    )
    _write_atomic(execution_root / "main-execution-progress.json", report)
    return report


def run_actual_ablation_execution(
    *,
    output_root: Path,
    environment: dict[str, str],
) -> dict[str, object]:
    _require_actual_execution_authorization(environment)
    execution_root = _require_external_output_root(output_root)
    _verify_visible_execution_inputs(environment)
    started = verify_execution_started_chain(PROJECT_ROOT, execution_root)
    _runtime_integrity_guard(
        execution_root=execution_root,
        started=started,
        environment=environment,
    )
    verify_main_execution_complete(PROJECT_ROOT, execution_root)
    report = run_ablation_schedule(
        registry_path=PROTOCOL_CONFIG / "ablation-registry.v1.json",
        output_root=execution_root / "ablation",
        executor=UnsupportedFrozenAblationExecutor(),
        sleeper=time.sleep,
        evidence_class="ACTUAL_SCORED",
        integrity_guard=lambda: _runtime_integrity_guard(
            execution_root=execution_root,
            started=started,
            environment=environment,
        ),
    )
    _write_atomic(execution_root / "ablation-execution-progress.json", report)
    return report


def run_execution_seal(
    *,
    output_root: Path,
    environment: dict[str, str],
) -> dict[str, object]:
    _require_actual_execution_authorization(environment)
    _reject_premature_truth_environment(environment)
    execution_root = _require_external_output_root(output_root)
    _verify_visible_execution_inputs(environment)
    started = verify_execution_started_chain(PROJECT_ROOT, execution_root)
    require_frozen_runtime_source(
        PROJECT_ROOT,
        expected_execution_freeze_sha256=started.execution_freeze_sha256,
        expected_source_commit=started.source_commit,
    )
    seal = seal_execution_complete(
        project_root=PROJECT_ROOT,
        execution_root=execution_root,
    )
    return {
        "status": seal.to_state,
        "completed_main_runs": seal.completed_main_runs,
        "completed_ablation_runs": seal.completed_ablation_runs,
        **_readiness_status(seal),
        "provider_calls": seal.provider_network_calls,
        "ground_truth_reads": 0,
        "failure_count": seal.failure_count,
    }


def run_unblinding_transition(
    *,
    output_root: Path,
    environment: dict[str, str],
) -> dict[str, object]:
    _require_actual_execution_authorization(environment)
    _reject_premature_truth_environment(environment)
    execution_root = _require_external_output_root(output_root)
    _verify_visible_execution_inputs(environment)
    complete = _load_canonical(
        execution_root / EXECUTION_COMPLETE_SEAL,
        ExecutionCompleteSeal,
    )
    require_frozen_runtime_source(
        PROJECT_ROOT,
        expected_execution_freeze_sha256=complete.execution_freeze_sha256,
        expected_source_commit=complete.source_commit,
    )
    record = create_execution_unblinding_record(
        project_root=PROJECT_ROOT,
        execution_root=execution_root,
    )
    return {
        "status": record.to_state,
        "completed_main_runs": record.completed_main_runs,
        "completed_ablation_runs": record.completed_ablation_runs,
        **_readiness_status(record),
        "irreversible": record.irreversible,
        "ground_truth_reads": 0,
    }


def verify_execution_complete_reports(
    *,
    output_root: Path,
) -> dict[str, object]:
    execution_root = _require_external_output_root(output_root)
    seal = verify_execution_complete_chain(PROJECT_ROOT, execution_root)
    require_frozen_runtime_source(
        PROJECT_ROOT,
        expected_execution_freeze_sha256=seal.execution_freeze_sha256,
        expected_source_commit=seal.source_commit,
    )
    return {
        "status": "PHASE5B_EXECUTION_REPORTS_VERIFIED",
        "main_runs": seal.completed_main_runs,
        "ablation_runs": seal.completed_ablation_runs,
        **_readiness_status(seal),
        "provider_calls": seal.provider_network_calls,
        "ground_truth_reads": 0,
    }


def verify_unblinding_record(*, output_root: Path) -> dict[str, object]:
    execution_root = _require_external_output_root(output_root)
    record = verify_unblinding_chain(PROJECT_ROOT, execution_root)
    require_frozen_runtime_source(
        PROJECT_ROOT,
        expected_execution_freeze_sha256=record.execution_freeze_sha256,
        expected_source_commit=record.execution_source_commit,
    )
    return {
        "status": record.to_state,
        "irreversible": record.irreversible,
        "completed_main_runs": record.completed_main_runs,
        "completed_ablation_runs": record.completed_ablation_runs,
        **_readiness_status(record),
    }


def run_final_analysis(
    *,
    output_root: Path,
    environment: dict[str, str],
) -> dict[str, object]:
    _require_actual_execution_authorization(environment)
    if any(
        marker in key.upper()
        for key in environment
        for marker in ("BUILDER", "HIDDEN_PACK_ROOT")
    ):
        raise PermissionError("Builder and whole-pack locators remain forbidden")
    execution_root = _require_external_output_root(output_root)
    truth_root = environment.get("PHASE5B_GROUND_TRUTH_ROOT")
    if not truth_root:
        raise ValueError("ground-truth root is required after irreversible unblinding")
    report = freeze_final_report(
        project_root=PROJECT_ROOT,
        execution_root=execution_root,
        hidden_ground_truth_root=Path(truth_root),
    )
    return {
        "status": "PHASE5B_FINAL_REPORT_FROZEN",
        "claim_classification": report.claim_classification,
        "main_runs": report.main_run_count,
        "ablation_runs": report.ablation_run_count,
        **_readiness_status(report),
        "provider_calls": 0,
        "post_unblinding_tuning": False,
    }


def create_execution_freeze(path: Path) -> None:
    manifest = build_execution_freeze_manifest(
        PROJECT_ROOT,
        execution_base_commit=_EXECUTION_BASE_COMMIT,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(canonical_json_bytes(manifest.model_dump(mode="json")))
        stream.flush()
        os.fsync(stream.fileno())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("preflight")
    subparsers.add_parser("provider-preflight")
    subparsers.add_parser("freeze-verify")
    freeze_create = subparsers.add_parser("freeze-create")
    freeze_create.add_argument(
        "--output",
        type=Path,
        default=PROJECT_ROOT / EXECUTION_FREEZE_RELATIVE,
    )
    rehearsal = subparsers.add_parser("mock-rehearsal")
    rehearsal.add_argument("--output-root", type=Path, default=DEFAULT_MOCK_ROOT)
    mock_verify = subparsers.add_parser("mock-verify")
    mock_verify.add_argument("--output-root", type=Path, default=DEFAULT_MOCK_ROOT)
    actual_main = subparsers.add_parser("execute-main")
    actual_main.add_argument("--output-root", type=Path, required=True)
    actual_ablation = subparsers.add_parser("execute-ablation")
    actual_ablation.add_argument("--output-root", type=Path, required=True)
    canary = subparsers.add_parser("provider-canary")
    canary.add_argument("--output-root", type=Path, required=True)
    start = subparsers.add_parser("enter-execution")
    start.add_argument("--output-root", type=Path, required=True)
    seal = subparsers.add_parser("seal-execution")
    seal.add_argument("--output-root", type=Path, required=True)
    unblind = subparsers.add_parser("unblind")
    unblind.add_argument("--output-root", type=Path, required=True)
    report_verify = subparsers.add_parser("report-verify")
    report_verify.add_argument("--output-root", type=Path, required=True)
    ablation_verify = subparsers.add_parser("ablation-report-verify")
    ablation_verify.add_argument("--output-root", type=Path, required=True)
    unblinding_verify = subparsers.add_parser("unblinding-verify")
    unblinding_verify.add_argument("--output-root", type=Path, required=True)
    final_analysis = subparsers.add_parser("final-analysis")
    final_analysis.add_argument("--output-root", type=Path, required=True)
    final_verify = subparsers.add_parser("final-report-verify")
    final_verify.add_argument("--output-root", type=Path, required=True)
    arguments = parser.parse_args(argv)
    if arguments.command == "preflight":
        _print(execution_preflight())
    elif arguments.command == "provider-preflight":
        _print(run_provider_preflight(dict(os.environ)))
    elif arguments.command == "freeze-verify":
        manifest = verify_execution_freeze_manifest(PROJECT_ROOT)
        _print(
            {
                "status": "PHASE5B_EXECUTION_FREEZE_VERIFIED",
                "harness_file_count": len(manifest.harness_files),
                "provider_calls": 0,
                "ground_truth_reads": 0,
            }
        )
    elif arguments.command == "freeze-create":
        create_execution_freeze(arguments.output)
        _print(
            {
                "status": "PHASE5B_EXECUTION_FREEZE_CREATED",
                "output": str(arguments.output),
                "provider_calls": 0,
            }
        )
    elif arguments.command == "mock-rehearsal":
        _print(write_mock_rehearsal(arguments.output_root))
    elif arguments.command == "mock-verify":
        _print(verify_mock_rehearsal(arguments.output_root))
    elif arguments.command == "execute-main":
        _print(
            run_actual_main_execution(
                output_root=arguments.output_root,
                environment=dict(os.environ),
            )
        )
    elif arguments.command == "execute-ablation":
        _print(
            run_actual_ablation_execution(
                output_root=arguments.output_root,
                environment=dict(os.environ),
            )
        )
    elif arguments.command == "provider-canary":
        _require_actual_execution_authorization(dict(os.environ))
        _reject_premature_truth_environment(dict(os.environ))
        execution_root = _require_external_output_root(
            arguments.output_root,
            create=True,
        )
        canary_record = run_provider_canary(
            project_root=PROJECT_ROOT,
            execution_root=execution_root,
            environment=dict(os.environ),
        )
        _print(
            {
                "status": (
                    "PROVIDER_CANARY_PASS"
                    if canary_record.typed_protocol_pass
                    else "BLOCKED_PROVIDER_HEALTH_BEFORE_EXECUTION"
                ),
                "provider_calls": canary_record.provider_network_calls,
                "typed_protocol_pass": canary_record.typed_protocol_pass,
                "no_retry": True,
            }
        )
    elif arguments.command == "enter-execution":
        _print(
            enter_execution(
                output_root=arguments.output_root,
                environment=dict(os.environ),
            )
        )
    elif arguments.command == "seal-execution":
        _print(
            run_execution_seal(
                output_root=arguments.output_root,
                environment=dict(os.environ),
            )
        )
    elif arguments.command == "unblind":
        _print(
            run_unblinding_transition(
                output_root=arguments.output_root,
                environment=dict(os.environ),
            )
        )
    elif arguments.command in {"report-verify", "ablation-report-verify"}:
        _print(verify_execution_complete_reports(output_root=arguments.output_root))
    elif arguments.command == "unblinding-verify":
        _print(verify_unblinding_record(output_root=arguments.output_root))
    elif arguments.command == "final-analysis":
        _print(
            run_final_analysis(
                output_root=arguments.output_root,
                environment=dict(os.environ),
            )
        )
    else:
        environment = dict(os.environ)
        if any(
            marker in key.upper()
            for key in environment
            for marker in ("BUILDER", "HIDDEN_PACK_ROOT")
        ):
            raise PermissionError("Builder and whole-pack locators remain forbidden")
        truth_root = environment.get("PHASE5B_GROUND_TRUTH_ROOT")
        if not truth_root:
            raise ValueError("ground-truth root is required for final verification")
        report = verify_final_report(
            PROJECT_ROOT,
            _require_external_output_root(arguments.output_root),
            Path(truth_root),
        )
        _print(
            {
                "status": "PHASE5B_FINAL_REPORT_VERIFIED",
                "claim_classification": report.claim_classification,
                "main_runs": report.main_run_count,
                "ablation_runs": report.ablation_run_count,
                **_readiness_status(report),
            }
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
