"""Read-only v1 admission and in-memory v2 scoring-bundle construction."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
import stat

from ecomsre.phase5b.contracts import ExecutionSchedule, SuiteRegistry
from ecomsre.phase5b.protocol import load_strict_json

from scripts.phase5b_analysis_v2.analysis import project_hidden_truth_v2
from scripts.phase5b_analysis_v2.contracts import (
    ANALYSIS_VERSION,
    SUBSET_MAPPING_SOURCE,
    V2ScoringBundle,
)
from scripts.phase5b_analysis_v2.protocol import (
    AnalysisProtocol,
    load_analysis_protocol,
    verify_regular_file_sha256,
)
from scripts.phase5b_execution.checkpoint import CheckpointStore
from scripts.phase5b_execution.contracts import (
    ExecutionCompleteSeal,
    ExecutionUnblindingRecord,
    ScoredRunRequest,
)
from scripts.phase5b_execution.evaluator import admit_unblinded_evaluator
from scripts.phase5b_execution.lifecycle import (
    MAIN_EXECUTION_REPORT,
    UNBLINDING_RECORD,
    verify_execution_complete_chain,
    verify_unblinding_chain,
)
from scripts.phase5b_execution.scoring import (
    FINAL_REPORT,
    SCORING_BUNDLE,
    _PUBLIC_TRUTH_PATHS,
    _load_truth_object,
    _score_one,
    _truth_projection,
    _verify_hidden_truth_pack,
)


@dataclass(frozen=True, slots=True)
class V1AnalysisInputs:
    """Verified immutable inputs admitted for a later v2 analysis run."""

    protocol: AnalysisProtocol
    v1_source_root: Path
    v1_execution_root: Path
    hidden_ground_truth_root: Path
    complete: ExecutionCompleteSeal
    unblinding: ExecutionUnblindingRecord
    schedule: ExecutionSchedule
    suite: SuiteRegistry
    raw_record_manifest_sha256: str


def _require_real_directory(path: Path, *, label: str) -> Path:
    details = path.lstat()
    if stat.S_ISLNK(details.st_mode) or not stat.S_ISDIR(details.st_mode):
        raise ValueError(f"{label} must be a real directory")
    return path.resolve(strict=True)


def _raw_record_manifest(
    *,
    execution_root: Path,
    schedule: ExecutionSchedule,
) -> str:
    store = CheckpointStore(execution_root / "main")
    digest = hashlib.sha256()
    observed = 0
    for scheduled in schedule.runs:
        request = ScoredRunRequest.from_scheduled_run(scheduled)
        raw = store.load_record(request.run_id)
        if raw is None:
            raise ValueError("v1 raw record set is incomplete")
        raw.verify_record_sha256()
        if (
            raw.run_id != request.run_id
            or raw.template_id != request.template_id
            or raw.seed_id != request.seed_id
            or raw.variant != request.variant
            or raw.evidence_class != "ACTUAL_SCORED"
        ):
            raise ValueError("v1 raw record differs from frozen schedule")
        digest.update(raw.run_id.encode("utf-8"))
        digest.update(b"\0")
        digest.update(raw.record_sha256.encode("ascii"))
        digest.update(b"\0")
        observed += 1
    if observed != 180:
        raise ValueError("v1 raw record count is not frozen at 180")
    return digest.hexdigest()


def verify_v1_analysis_inputs(
    *,
    project_root: Path,
    v1_source_root: Path,
    v1_execution_root: Path,
    hidden_ground_truth_root: Path,
) -> V1AnalysisInputs:
    """Verify v1 evidence without scoring it or creating output."""

    protocol = load_analysis_protocol(project_root)
    source_root = _require_real_directory(v1_source_root, label="v1 source root")
    execution_root = _require_real_directory(
        v1_execution_root, label="v1 execution root"
    )
    truth_root = _require_real_directory(
        hidden_ground_truth_root, label="hidden ground-truth root"
    )
    complete = verify_execution_complete_chain(source_root, execution_root)
    unblinding = verify_unblinding_chain(source_root, execution_root)
    admitted = admit_unblinded_evaluator(
        project_root=source_root,
        execution_root=execution_root,
        hidden_ground_truth_root=truth_root,
    )
    if admitted.unblinding_record != unblinding:
        raise ValueError("v2 admission differs from verified v1 unblinding")

    verify_regular_file_sha256(
        source_root / "config/phase5b-execution/execution-freeze.v1.json",
        expected_sha256=protocol.execution_freeze_sha256,
    )
    verify_regular_file_sha256(
        source_root / "config/phase5b/execution-schedule.v1.json",
        expected_sha256=protocol.execution_schedule_sha256,
    )
    verify_regular_file_sha256(
        source_root / "config/phase5b/freeze-manifest.v1.json",
        expected_sha256=protocol.protocol_freeze_manifest_sha256,
    )
    verify_regular_file_sha256(
        source_root / "config/phase5b-seal/hidden-pack-seal.v1.json",
        expected_sha256=protocol.hidden_pack_seal_record_sha256,
    )
    verify_regular_file_sha256(
        execution_root / MAIN_EXECUTION_REPORT,
        expected_sha256=protocol.execution_report_sha256,
    )
    verify_regular_file_sha256(
        execution_root / UNBLINDING_RECORD,
        expected_sha256=protocol.unblinding_record_sha256,
    )
    if (
        complete.source_commit != protocol.execution_source_commit
        or complete.execution_freeze_sha256 != protocol.execution_freeze_sha256
        or complete.execution_report_sha256 != protocol.execution_report_sha256
        or unblinding.execution_source_commit != protocol.execution_source_commit
        or unblinding.execution_schedule_sha256 != protocol.execution_schedule_sha256
        or unblinding.protocol_freeze_manifest_sha256
        != protocol.protocol_freeze_manifest_sha256
        or unblinding.agent_visible_pack_sha256 != protocol.agent_visible_pack_sha256
        or unblinding.hidden_pack_manifest_sha256
        != protocol.hidden_pack_manifest_sha256
        or unblinding.ground_truth_pack_sha256 != protocol.ground_truth_pack_sha256
        or unblinding.completed_main_runs != 180
        or unblinding.completed_ablation_runs != 38
    ):
        raise ValueError("v1 lifecycle evidence differs from v2 protocol bindings")
    _verify_hidden_truth_pack(truth_root, protocol.ground_truth_pack_sha256)
    schedule = load_strict_json(
        source_root / "config/phase5b/execution-schedule.v1.json",
        ExecutionSchedule,
    )
    suite = load_strict_json(
        source_root / "config/phase5b/suite-registry.v1.json",
        SuiteRegistry,
    )
    raw_manifest = _raw_record_manifest(
        execution_root=execution_root,
        schedule=schedule,
    )
    if (execution_root / SCORING_BUNDLE).exists() or (
        execution_root / FINAL_REPORT
    ).exists():
        raise ValueError("terminated v1 unexpectedly contains scoring output")
    return V1AnalysisInputs(
        protocol=protocol,
        v1_source_root=source_root,
        v1_execution_root=execution_root,
        hidden_ground_truth_root=truth_root,
        complete=complete,
        unblinding=unblinding,
        schedule=schedule,
        suite=suite,
        raw_record_manifest_sha256=raw_manifest,
    )


def build_v2_scoring_bundle(inputs: V1AnalysisInputs) -> V2ScoringBundle:
    """Build the repaired bundle in memory; callers decide where to freeze it."""

    write_by_template = {
        item.template_id: item.write_disposition for item in inputs.suite.hidden_slots
    }
    write_by_template.update(
        {
            item.template_id: (
                "SAFE_REPLAY_REMEDIATION_CANDIDATE"
                if item.template_id == "ad-partial-failure-complete"
                else "NO_ACTION"
            )
            for item in inputs.suite.public_anchors
        }
    )
    store = CheckpointStore(inputs.v1_execution_root / "main")
    records = []
    for scheduled in inputs.schedule.runs:
        request = ScoredRunRequest.from_scheduled_run(scheduled)
        raw = store.load_record(request.run_id)
        if raw is None:
            raise ValueError("v1 raw record set is incomplete")
        raw.verify_record_sha256()
        original_record_sha256 = raw.record_sha256
        if scheduled.template_id.startswith("hidden-"):
            truth_path = (
                inputs.hidden_ground_truth_root
                / scheduled.template_id
                / f"{scheduled.seed_id}.json"
            )
            payload, truth_sha256 = _load_truth_object(truth_path)
            truth = project_hidden_truth_v2(
                payload=payload,
                template_id=scheduled.template_id,
                seed_id=scheduled.seed_id,
                write_disposition=write_by_template[scheduled.template_id],
            )
            population = "HIDDEN"
        else:
            truth_path = (
                inputs.v1_source_root / _PUBLIC_TRUTH_PATHS[scheduled.template_id]
            )
            payload, truth_sha256 = _load_truth_object(truth_path)
            truth = _truth_projection(
                payload=payload,
                template_id=scheduled.template_id,
                seed_id=scheduled.seed_id,
                write_disposition=write_by_template[scheduled.template_id],
            )
            population = "PUBLIC"
        scored = _score_one(
            raw=raw,
            truth=truth,
            truth_sha256=truth_sha256,
            population=population,
        )
        raw.verify_record_sha256()
        if (
            raw.record_sha256 != original_record_sha256
            or scored.raw_record_sha256 != original_record_sha256
        ):
            raise ValueError("v2 scoring changed immutable raw evidence")
        records.append(scored)
    bundle = V2ScoringBundle(
        schema_version="phase5b.scoring-bundle.v2",
        evaluation_version="phase5b.v1",
        analysis_version=ANALYSIS_VERSION,
        input_evaluation_version="phase5b.v1",
        subset_mapping_source=SUBSET_MAPPING_SOURCE,
        private_difficult_subsets_used=False,
        provider_calls=0,
        execution_report_sha256=inputs.protocol.execution_report_sha256,
        unblinding_record_sha256=inputs.protocol.unblinding_record_sha256,
        ground_truth_pack_sha256=inputs.protocol.ground_truth_pack_sha256,
        run_count=180,
        all_failures_retained=True,
        records=tuple(records),
    )
    if (
        _raw_record_manifest(
            execution_root=inputs.v1_execution_root,
            schedule=inputs.schedule,
        )
        != inputs.raw_record_manifest_sha256
    ):
        raise ValueError("v1 raw record manifest changed during v2 analysis")
    return bundle
