"""Create-once execution, completion, and unblinding lifecycle records."""

from __future__ import annotations

from collections import Counter
import hashlib
from pathlib import Path
import stat

from ecomsre.phase5b.contracts import (
    ExecutionSchedule,
    FrozenExecutionRecord,
    FrozenExecutionReport,
)
from ecomsre.phase5b.protocol import load_strict_json

from scripts.phase5b_execution.ablation import (
    _AblationStore,
    build_ablation_schedule,
)
from scripts.phase5b_execution.canary import (
    CANARY_ATTEMPT,
    CANARY_RAW_RECORD,
    CANARY_RECORD,
    verify_canary_chain,
)
from scripts.phase5b_execution.checkpoint import (
    CheckpointStore,
    _atomic_create,
    _ensure_private_directory,
    _entry_exists,
    _load_canonical,
)
from scripts.phase5b_execution.contracts import (
    AblationExecutionReport,
    AblationRunRecord,
    ExecutionCompleteSeal,
    ExecutionFreezeManifest,
    ExecutionStartedRecord,
    ExecutionUnblindingRecord,
    RawScoredRunRecord,
    ScoredRunRequest,
    TerminalStatus,
    canonical_json_bytes,
)
from scripts.phase5b_execution.freeze import (
    EXECUTION_FREEZE_RELATIVE,
    load_execution_freeze_manifest,
    sha256_regular_file,
)


EXECUTION_STARTED_RECORD = Path("phase5b-v1-protocol-state.json")
MAIN_EXECUTION_REPORT = Path("reports/execution-report.json")
ABLATION_EXECUTION_REPORT = Path("reports/ablation-execution-report.json")
EXECUTION_COMPLETE_SEAL = Path("state/execution-complete-seal.json")
UNBLINDING_RECORD = Path("state/unblinding-record.json")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _create_or_verify(path: Path, payload: bytes) -> None:
    _ensure_private_directory(path.parent)
    if _entry_exists(path):
        details = path.lstat()
        if (
            stat.S_ISLNK(details.st_mode)
            or not stat.S_ISREG(details.st_mode)
            or path.read_bytes() != payload
        ):
            raise ValueError(f"create-once lifecycle record differs: {path.name}")
        return
    _atomic_create(path, payload)


def _regular_files_beneath(root: Path) -> set[Path]:
    details = root.lstat()
    if stat.S_ISLNK(details.st_mode) or not stat.S_ISDIR(details.st_mode):
        raise ValueError("execution journal root must be a real directory")
    observed: set[Path] = set()
    for item in root.rglob("*"):
        item_details = item.lstat()
        if stat.S_ISLNK(item_details.st_mode):
            raise ValueError("execution journal contains a symlink")
        if stat.S_ISREG(item_details.st_mode):
            observed.add(item.relative_to(root))
        elif not stat.S_ISDIR(item_details.st_mode):
            raise ValueError("execution journal contains an unknown entry")
    return observed


def _require_pristine_scored_journal(execution_root: Path) -> None:
    allowed = {CANARY_RECORD, CANARY_RAW_RECORD}
    observed = _regular_files_beneath(execution_root)
    if observed != allowed or _entry_exists(execution_root / CANARY_ATTEMPT):
        raise ValueError("execution start requires a pristine canary-only journal")


def create_execution_started_record(
    *,
    project_root: Path,
    execution_root: Path,
    source_commit: str,
    origin_main_commit: str,
    provider_configuration_sha256: str,
) -> ExecutionStartedRecord:
    canary = verify_canary_chain(
        execution_root,
        expected_provider_configuration_sha256=provider_configuration_sha256,
    )
    if not canary.typed_protocol_pass:
        raise ValueError("Provider canary did not pass")
    manifest_path = project_root / EXECUTION_FREEZE_RELATIVE
    freeze = load_execution_freeze_manifest(manifest_path)
    seal_path = project_root / "config/phase5b-seal/hidden-pack-seal.v1.json"
    record = ExecutionStartedRecord(
        schema_version="phase5b.execution-started.v1",
        evaluation_version="phase5b.v1",
        source_commit=source_commit,
        origin_main_commit=origin_main_commit,
        execution_freeze_sha256=sha256_regular_file(manifest_path),
        hidden_pack_seal_sha256=sha256_regular_file(seal_path),
        hidden_pack_manifest_sha256=freeze.hidden_pack_manifest_sha256,
        agent_visible_pack_sha256=freeze.agent_visible_pack_sha256,
        canary_record_sha256=_sha256(execution_root / CANARY_RECORD),
        provider_configuration_sha256=provider_configuration_sha256,
        from_state="HIDDEN_PACK_SEALED",
        to_state="EXECUTION_STARTED",
        completed_main_runs=0,
        completed_ablation_runs=0,
        main_evaluation_ready=freeze.main_evaluation_ready,
        ablation_slot_count=freeze.ablation_slot_count,
        ablation_implementation_available=(
            freeze.ablation_implementation_available
        ),
        ablation_evidence_available=freeze.ablation_evidence_available,
        ablation_primary_eligible=freeze.ablation_primary_eligible,
        ablation_disposition=freeze.ablation_disposition,
        frozen_files_unchanged=True,
        ground_truth_read=False,
        create_once=True,
    )
    state_path = execution_root / EXECUTION_STARTED_RECORD
    if not _entry_exists(state_path):
        _require_pristine_scored_journal(execution_root)
    _create_or_verify(
        state_path,
        canonical_json_bytes(record.model_dump(mode="json")),
    )
    return record


def require_execution_started(execution_root: Path) -> ExecutionStartedRecord:
    if _entry_exists(execution_root / EXECUTION_COMPLETE_SEAL):
        raise ValueError("execution is already complete")
    if _entry_exists(execution_root / UNBLINDING_RECORD):
        raise ValueError("execution is already unblinded")
    return _load_canonical(
        execution_root / EXECUTION_STARTED_RECORD,
        ExecutionStartedRecord,
    )


def _verify_started_bindings(
    project_root: Path,
    execution_root: Path,
) -> ExecutionStartedRecord:
    started = _load_canonical(
        execution_root / EXECUTION_STARTED_RECORD,
        ExecutionStartedRecord,
    )
    canary = verify_canary_chain(execution_root)
    if not canary.typed_protocol_pass:
        raise ValueError("execution chain contains a failed Provider canary")
    freeze_path = project_root / EXECUTION_FREEZE_RELATIVE
    freeze = load_execution_freeze_manifest(freeze_path)
    public_seal_path = project_root / "config/phase5b-seal/hidden-pack-seal.v1.json"
    if (
        started.execution_freeze_sha256 != sha256_regular_file(freeze_path)
        or started.hidden_pack_seal_sha256 != sha256_regular_file(public_seal_path)
        or started.hidden_pack_manifest_sha256 != freeze.hidden_pack_manifest_sha256
        or started.agent_visible_pack_sha256 != freeze.agent_visible_pack_sha256
        or started.canary_record_sha256 != _sha256(execution_root / CANARY_RECORD)
        or started.provider_configuration_sha256
        != canary.provider_configuration_sha256
    ):
        raise ValueError("execution-started record differs from frozen inputs")
    return started


def verify_execution_started_chain(
    project_root: Path,
    execution_root: Path,
) -> ExecutionStartedRecord:
    require_execution_started(execution_root)
    return _verify_started_bindings(project_root, execution_root)


def _require_exact_store_files(
    *,
    root: Path,
    attempts_name: str,
    records_name: str,
    expected_ids: tuple[str, ...],
) -> None:
    observed = _regular_files_beneath(root)
    expected = {Path(records_name) / f"{run_id}.json" for run_id in expected_ids}
    if observed != expected:
        raise ValueError("execution record set has missing, extra, or open entries")
    attempts = root / attempts_name
    records = root / records_name
    for directory in (attempts, records):
        details = directory.lstat()
        if stat.S_ISLNK(details.st_mode) or not stat.S_ISDIR(details.st_mode):
            raise ValueError("execution store directory is not a real directory")


def verify_main_execution_complete(
    project_root: Path,
    execution_root: Path,
) -> tuple[FrozenExecutionReport, tuple[RawScoredRunRecord, ...]]:
    schedule_path = project_root / "config/phase5b/execution-schedule.v1.json"
    schedule = load_strict_json(schedule_path, ExecutionSchedule)
    expected_ids = tuple(item.run_id for item in schedule.runs)
    _require_exact_store_files(
        root=execution_root / "main",
        attempts_name="attempts",
        records_name="raw",
        expected_ids=expected_ids,
    )
    store = CheckpointStore(execution_root / "main")
    frozen: list[FrozenExecutionRecord] = []
    raw: list[RawScoredRunRecord] = []
    for scheduled in schedule.runs:
        request = ScoredRunRequest.from_scheduled_run(scheduled)
        record = store.load_record(request.run_id)
        if (
            record is None
            or record.evidence_class != "ACTUAL_SCORED"
            or record.run_id != request.run_id
            or record.template_id != request.template_id
            or record.seed_id != request.seed_id
            or record.variant != request.variant
        ):
            raise ValueError("main execution record set differs from schedule")
        frozen.append(
            FrozenExecutionRecord(
                run_id=record.run_id,
                terminal_status=record.terminal_status.value,
                observed_result_sha256=record.record_sha256,
            )
        )
        raw.append(record)
    report = FrozenExecutionReport(
        schema_version="phase5b.execution-report.v1",
        evaluation_version="phase5b.v1",
        execution_schedule_sha256=sha256_regular_file(schedule_path),
        run_count=180,
        all_terminal=True,
        records=tuple(frozen),
    )
    return report, tuple(raw)


def _verify_ablation_execution_complete(
    project_root: Path,
    execution_root: Path,
) -> tuple[AblationExecutionReport, tuple[AblationRunRecord, ...]]:
    registry_path = project_root / "config/phase5b/ablation-registry.v1.json"
    requests = build_ablation_schedule(registry_path)
    expected_ids = tuple(item.ablation_run_id for item in requests)
    _require_exact_store_files(
        root=execution_root / "ablation",
        attempts_name="ablation-attempts",
        records_name="ablation-raw",
        expected_ids=expected_ids,
    )
    store = _AblationStore(execution_root / "ablation")
    hashes: dict[str, str] = {}
    raw: list[AblationRunRecord] = []
    terminal_counts: Counter[str] = Counter()
    provider_calls = 0
    for request in requests:
        record = store.load(request.ablation_run_id)
        if (
            record is None
            or record.evidence_class != "ACTUAL_SCORED"
            or record.ablation_run_id != request.ablation_run_id
            or record.ablation_id != request.ablation_id
            or record.template_id != request.template_id
            or record.seed_id != request.seed_id
            or record.run_kind != request.run_kind
        ):
            raise ValueError("ablation execution record set differs from registry")
        if (
            record.terminal_status is not TerminalStatus.WORKFLOW_FAILURE
            or record.failure_code
            != "ABLATION_NOT_IMPLEMENTED_IN_FROZEN_HARNESS"
            or record.failure_stage != "ABLATION_IMPLEMENTATION"
            or record.provider_attempted
            or record.usage.provider_network_calls != 0
            or record.usage.model_calls != 0
            or record.usage.tool_calls != 0
            or record.usage.combined_tokens != 0
        ):
            raise ValueError("ablation record violates frozen not-implemented policy")
        hashes[record.ablation_run_id] = record.record_sha256
        terminal_counts[record.terminal_status.value] += 1
        provider_calls += record.usage.provider_network_calls
        raw.append(record)
    report = AblationExecutionReport(
        schema_version="phase5b.ablation-execution-report.v1",
        evaluation_version="phase5b.v1",
        ablation_registry_sha256=sha256_regular_file(registry_path),
        run_count=38,
        all_terminal=True,
        record_sha256_by_run_id=hashes,
        terminal_count_by_category=dict(sorted(terminal_counts.items())),
        provider_network_calls=provider_calls,
        primary_eligible=False,
        main_evaluation_ready=True,
        ablation_slot_count=38,
        ablation_implementation_available=False,
        ablation_evidence_available=False,
        ablation_primary_eligible=False,
        ablation_disposition="ABLATION_NOT_IMPLEMENTED_IN_FROZEN_HARNESS",
    )
    return report, tuple(raw)


def _build_complete_seal(
    *,
    project_root: Path,
    started: ExecutionStartedRecord,
    main_report: FrozenExecutionReport,
    ablation_report: AblationExecutionReport,
    main_raw: tuple[RawScoredRunRecord, ...],
    ablation_raw: tuple[AblationRunRecord, ...],
    main_report_sha256: str,
    ablation_report_sha256: str,
) -> ExecutionCompleteSeal:
    all_records: tuple[RawScoredRunRecord | AblationRunRecord, ...] = (
        *main_raw,
        *ablation_raw,
    )
    terminal_counts = Counter(record.terminal_status.value for record in all_records)
    return ExecutionCompleteSeal(
        schema_version="phase5b.execution-complete-seal.v1",
        evaluation_version="phase5b.v1",
        source_commit=started.source_commit,
        execution_freeze_sha256=sha256_regular_file(
            project_root / EXECUTION_FREEZE_RELATIVE
        ),
        execution_schedule_sha256=main_report.execution_schedule_sha256,
        ablation_registry_sha256=ablation_report.ablation_registry_sha256,
        execution_report_sha256=main_report_sha256,
        ablation_report_sha256=ablation_report_sha256,
        completed_main_runs=180,
        completed_ablation_runs=38,
        main_evaluation_ready=started.main_evaluation_ready,
        ablation_slot_count=ablation_report.ablation_slot_count,
        ablation_implementation_available=(
            ablation_report.ablation_implementation_available
        ),
        ablation_evidence_available=ablation_report.ablation_evidence_available,
        ablation_primary_eligible=ablation_report.ablation_primary_eligible,
        ablation_disposition=ablation_report.ablation_disposition,
        terminal_count_by_category=dict(sorted(terminal_counts.items())),
        provider_network_calls=sum(
            record.usage.provider_network_calls for record in all_records
        ),
        model_calls=sum(record.usage.model_calls for record in all_records),
        tool_calls=sum(record.usage.tool_calls for record in all_records),
        combined_tokens=sum(record.usage.combined_tokens for record in all_records),
        failure_count=sum(
            record.terminal_status is not TerminalStatus.COMPLETED
            for record in all_records
        ),
        all_failures_retained=True,
        ground_truth_read=False,
        from_state="EXECUTION_STARTED",
        to_state="EXECUTION_COMPLETE",
        create_once=True,
    )


def seal_execution_complete(
    *,
    project_root: Path,
    execution_root: Path,
) -> ExecutionCompleteSeal:
    started = verify_execution_started_chain(project_root, execution_root)
    main_report, main_raw = verify_main_execution_complete(project_root, execution_root)
    ablation_report, ablation_raw = _verify_ablation_execution_complete(
        project_root, execution_root
    )
    main_path = execution_root / MAIN_EXECUTION_REPORT
    ablation_path = execution_root / ABLATION_EXECUTION_REPORT
    main_bytes = canonical_json_bytes(main_report.model_dump(mode="json"))
    ablation_bytes = canonical_json_bytes(ablation_report.model_dump(mode="json"))
    _create_or_verify(main_path, main_bytes)
    _create_or_verify(ablation_path, ablation_bytes)
    seal = _build_complete_seal(
        project_root=project_root,
        started=started,
        main_report=main_report,
        ablation_report=ablation_report,
        main_raw=main_raw,
        ablation_raw=ablation_raw,
        main_report_sha256=hashlib.sha256(main_bytes).hexdigest(),
        ablation_report_sha256=hashlib.sha256(ablation_bytes).hexdigest(),
    )
    _create_or_verify(
        execution_root / EXECUTION_COMPLETE_SEAL,
        canonical_json_bytes(seal.model_dump(mode="json")),
    )
    return seal


def verify_execution_complete_chain(
    project_root: Path,
    execution_root: Path,
) -> ExecutionCompleteSeal:
    started = _verify_started_bindings(project_root, execution_root)
    main_report, main_raw = verify_main_execution_complete(project_root, execution_root)
    ablation_report, ablation_raw = _verify_ablation_execution_complete(
        project_root, execution_root
    )
    main_path = execution_root / MAIN_EXECUTION_REPORT
    ablation_path = execution_root / ABLATION_EXECUTION_REPORT
    observed_main = _load_canonical(main_path, FrozenExecutionReport)
    observed_ablation = _load_canonical(ablation_path, AblationExecutionReport)
    if observed_main != main_report or observed_ablation != ablation_report:
        raise ValueError("execution reports do not reconstruct from raw records")
    expected = _build_complete_seal(
        project_root=project_root,
        started=started,
        main_report=main_report,
        ablation_report=ablation_report,
        main_raw=main_raw,
        ablation_raw=ablation_raw,
        main_report_sha256=_sha256(main_path),
        ablation_report_sha256=_sha256(ablation_path),
    )
    observed = _load_canonical(
        execution_root / EXECUTION_COMPLETE_SEAL,
        ExecutionCompleteSeal,
    )
    if observed != expected:
        raise ValueError("execution-complete seal does not reconstruct from raw records")
    return observed


def _build_unblinding_record(
    *,
    seal: ExecutionCompleteSeal,
    freeze: ExecutionFreezeManifest,
    execution_complete_seal_sha256: str,
) -> ExecutionUnblindingRecord:
    return ExecutionUnblindingRecord(
        schema_version="phase5b.unblinding-record.v1",
        evaluation_version="phase5b.v1",
        protocol_commit=freeze.protocol_commit,
        execution_source_commit=seal.source_commit,
        protocol_freeze_manifest_sha256=freeze.protocol_freeze_manifest_sha256,
        execution_freeze_sha256=seal.execution_freeze_sha256,
        execution_schedule_sha256=seal.execution_schedule_sha256,
        hidden_pack_manifest_sha256=freeze.hidden_pack_manifest_sha256,
        agent_visible_pack_sha256=freeze.agent_visible_pack_sha256,
        ground_truth_pack_sha256=freeze.ground_truth_pack_sha256,
        execution_report_sha256=seal.execution_report_sha256,
        ablation_report_sha256=seal.ablation_report_sha256,
        execution_complete_seal_sha256=execution_complete_seal_sha256,
        completed_main_runs=180,
        completed_ablation_runs=38,
        main_evaluation_ready=seal.main_evaluation_ready,
        ablation_slot_count=seal.ablation_slot_count,
        ablation_implementation_available=seal.ablation_implementation_available,
        ablation_evidence_available=seal.ablation_evidence_available,
        ablation_primary_eligible=seal.ablation_primary_eligible,
        ablation_disposition=seal.ablation_disposition,
        from_state="EXECUTION_COMPLETE",
        to_state="UNBLINDED",
        irreversible=True,
        create_once=True,
    )


def verify_unblinding_chain(
    project_root: Path,
    execution_root: Path,
) -> ExecutionUnblindingRecord:
    seal = verify_execution_complete_chain(project_root, execution_root)
    seal_path = execution_root / EXECUTION_COMPLETE_SEAL
    freeze = load_execution_freeze_manifest(project_root / EXECUTION_FREEZE_RELATIVE)
    expected = _build_unblinding_record(
        seal=seal,
        freeze=freeze,
        execution_complete_seal_sha256=_sha256(seal_path),
    )
    observed = _load_canonical(
        execution_root / UNBLINDING_RECORD,
        ExecutionUnblindingRecord,
    )
    if observed != expected:
        raise ValueError("unblinding record does not reconstruct from frozen evidence")
    return observed


def create_execution_unblinding_record(
    *,
    project_root: Path,
    execution_root: Path,
) -> ExecutionUnblindingRecord:
    seal = verify_execution_complete_chain(project_root, execution_root)
    seal_path = execution_root / EXECUTION_COMPLETE_SEAL
    freeze = load_execution_freeze_manifest(project_root / EXECUTION_FREEZE_RELATIVE)
    record = _build_unblinding_record(
        seal=seal,
        freeze=freeze,
        execution_complete_seal_sha256=_sha256(seal_path),
    )
    _create_or_verify(
        execution_root / UNBLINDING_RECORD,
        canonical_json_bytes(record.model_dump(mode="json")),
    )
    return record
