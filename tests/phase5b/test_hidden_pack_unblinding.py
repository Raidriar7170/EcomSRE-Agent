from __future__ import annotations

import json
import hashlib
from pathlib import Path

import pytest

from ecomsre.phase5b.analysis import validate_complete_results
from ecomsre.phase5b.contracts import FrozenExecutionRecord, FrozenExecutionReport
from ecomsre.phase5b.protocol import load_seed_policy, load_suite_registry
from ecomsre.phase5b.schedule import build_execution_schedule
from ecomsre.phase5b.hidden_pack import (
    build_hidden_pack_manifest,
    canonical_json_bytes,
    load_agent_visible_instance,
    validate_hidden_pack,
    validate_pack_roots,
    write_canonical_json,
)
from ecomsre.phase5b.unblinding import (
    ProtocolState,
    advance_protocol_state,
    create_unblinding_record,
    require_new_version_after_retuning,
)


TEMPLATES = tuple(f"hidden-{index:02d}" for index in range(1, 7))
SEEDS = tuple(f"seed-{index:02d}" for index in range(5))


def _build_synthetic_pack(root: Path) -> Path:
    for template_id in TEMPLATES:
        for seed_id in SEEDS:
            case_root = root / "agent-visible" / template_id / seed_id
            payloads = {
                "incident.json": {
                    "affected_sli": "synthetic protocol SLI",
                    "alert_source_service": None,
                    "ended_at": "2026-08-04T00:05:00Z",
                    "incident_id": f"synthetic-{seed_id}",
                    "schema_version": "phase1.incident.v1",
                    "severity": "SEV3",
                    "started_at": "2026-08-04T00:00:00Z",
                    "summary": "Synthetic non-evaluation protocol fixture.",
                },
                **{
                    f"{source}.json": {
                        "observations": [],
                        "schema_version": "phase1.replay-observations.v1",
                    "status": "AVAILABLE",
                    }
                    for source in ("metrics", "logs", "traces", "changes")
                },
            }
            for filename, payload in payloads.items():
                write_canonical_json(case_root / filename, payload)
            write_canonical_json(
                case_root / "manifest.json",
                {
                    "case_id": seed_id,
                    "files": {
                        filename: hashlib.sha256(
                            (case_root / filename).read_bytes()
                        ).hexdigest()
                        for filename in sorted(payloads)
                    },
                    "schema_version": "phase1.replay-manifest.v1",
                },
            )
            write_canonical_json(
                root / "ground-truth" / template_id / f"{seed_id}.json",
                {
                    "schema_version": "phase5b.synthetic-truth.v1",
                    "template_id": template_id,
                    "seed_id": seed_id,
                    "expected_decision": "ABSTAIN",
                },
            )
    manifest = build_hidden_pack_manifest(
        root,
        pack_id="synthetic-pack-for-contract-tests",
        generator_version="phase5b.synthetic-builder.v1",
    )
    manifest_path = root / "manifest.json"
    write_canonical_json(manifest_path, manifest.model_dump(mode="json"))
    return manifest_path


def test_synthetic_hidden_pack_validates_without_exposing_truth_to_worker(
    tmp_path: Path,
) -> None:
    pack_root = tmp_path / "pack"
    manifest_path = _build_synthetic_pack(pack_root)

    manifest = validate_hidden_pack(pack_root, manifest_path)
    assert manifest.template_count == 6
    assert manifest.seed_count_per_template == 5
    assert manifest.sealed is True
    assert manifest.unblinded is False
    assert manifest.template_ids == TEMPLATES

    visible = load_agent_visible_instance(
        pack_root / "agent-visible", "hidden-01", "seed-00"
    )
    assert visible.case_id == "seed-00"
    with pytest.raises(ValueError, match="template|identifier|visible"):
        load_agent_visible_instance(
            pack_root / "agent-visible", "../ground-truth", "seed-00"
        )


def test_hidden_pack_rejects_symlink_unknown_file_and_noncanonical_json(
    tmp_path: Path,
) -> None:
    pack_root = tmp_path / "pack"
    manifest_path = _build_synthetic_pack(pack_root)
    target = pack_root / "agent-visible/hidden-01/seed-00/incident.json"

    original = target.read_bytes()
    target.unlink()
    target.symlink_to(pack_root / "agent-visible/hidden-01/seed-01/incident.json")
    with pytest.raises(ValueError, match="symlink"):
        validate_hidden_pack(pack_root, manifest_path)

    target.unlink()
    target.write_bytes(original)
    unknown = pack_root / "agent-visible/hidden-01/seed-00/extra.json"
    unknown.write_text("{}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="unknown|layout"):
        validate_hidden_pack(pack_root, manifest_path)

    unknown.unlink()
    target.write_text(json.dumps(json.loads(original), indent=2) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="canonical"):
        validate_hidden_pack(pack_root, manifest_path)


def test_hidden_pack_rejects_overlapping_roots(tmp_path: Path) -> None:
    root = tmp_path / "same-root"
    root.mkdir()
    with pytest.raises(ValueError, match="overlap|distinct"):
        validate_pack_roots(root, root)


def test_unblinding_requires_complete_execution_and_is_irreversible() -> None:
    state = advance_protocol_state(
        ProtocolState.PROTOCOL_FROZEN,
        ProtocolState.HIDDEN_PACK_SEALED,
        completed_runs=0,
        planned_runs=180,
        frozen_files_unchanged=True,
    )
    state = advance_protocol_state(
        state,
        ProtocolState.EXECUTION_STARTED,
        completed_runs=0,
        planned_runs=180,
        frozen_files_unchanged=True,
    )
    with pytest.raises(ValueError, match="complete"):
        advance_protocol_state(
            state,
            ProtocolState.UNBLINDED,
            completed_runs=179,
            planned_runs=180,
            frozen_files_unchanged=True,
        )
    with pytest.raises(ValueError, match="frozen"):
        advance_protocol_state(
            state,
            ProtocolState.EXECUTION_COMPLETE,
            completed_runs=180,
            planned_runs=180,
            frozen_files_unchanged=False,
        )
    complete = advance_protocol_state(
        state,
        ProtocolState.EXECUTION_COMPLETE,
        completed_runs=180,
        planned_runs=180,
        frozen_files_unchanged=True,
    )
    unblinded = advance_protocol_state(
        complete,
        ProtocolState.UNBLINDED,
        completed_runs=180,
        planned_runs=180,
        frozen_files_unchanged=True,
    )
    with pytest.raises(ValueError, match="irreversible|transition"):
        advance_protocol_state(
            unblinded,
            ProtocolState.EXECUTION_COMPLETE,
            completed_runs=180,
            planned_runs=180,
            frozen_files_unchanged=True,
        )
    with pytest.raises(ValueError, match="phase5b.v2"):
        require_new_version_after_retuning("phase5b.v1", retuning_requested=True)


def test_execution_cannot_start_after_a_partial_or_drifted_acquisition() -> None:
    with pytest.raises(ValueError, match="zero completed runs"):
        advance_protocol_state(
            ProtocolState.HIDDEN_PACK_SEALED,
            ProtocolState.EXECUTION_STARTED,
            completed_runs=1,
            planned_runs=180,
            frozen_files_unchanged=True,
        )
    with pytest.raises(ValueError, match="frozen"):
        advance_protocol_state(
            ProtocolState.HIDDEN_PACK_SEALED,
            ProtocolState.EXECUTION_STARTED,
            completed_runs=0,
            planned_runs=180,
            frozen_files_unchanged=False,
        )


def test_unblinding_record_binds_every_frozen_boundary_and_is_create_once(
    tmp_path: Path,
) -> None:
    project_root = Path(__file__).resolve().parents[2]
    config_root = project_root / "config/phase5b"
    schedule = build_execution_schedule(
        load_suite_registry(config_root / "suite-registry.v1.json"),
        load_seed_policy(config_root / "seed-policy.v1.json"),
    )
    schedule_sha256 = hashlib.sha256(
        canonical_json_bytes(schedule.model_dump(mode="json"))
    ).hexdigest()
    run_ids = tuple(item.run_id for item in schedule.runs)
    validate_complete_results(schedule, run_ids)
    execution_report = FrozenExecutionReport(
        schema_version="phase5b.execution-report.v1",
        evaluation_version="phase5b.v1",
        execution_schedule_sha256=schedule_sha256,
        run_count=180,
        all_terminal=True,
        records=tuple(
            FrozenExecutionRecord(
                run_id=run_id,
                terminal_status="COMPLETED",
                observed_result_sha256=hashlib.sha256(run_id.encode()).hexdigest(),
            )
            for run_id in run_ids
        ),
    )
    report_path = tmp_path / "execution-report.json"
    write_canonical_json(report_path, execution_report.model_dump(mode="json"))
    record_path = tmp_path / "unblinding-record.json"
    record = create_unblinding_record(
        record_path,
        state=ProtocolState.EXECUTION_COMPLETE,
        execution_schedule=schedule,
        frozen_files_unchanged=True,
        execution_report_path=report_path,
        protocol_commit="1" * 40,
        freeze_manifest_sha256="2" * 64,
        execution_schedule_sha256=schedule_sha256,
        hidden_pack_manifest_sha256="4" * 64,
        agent_visible_pack_sha256="5" * 64,
        ground_truth_pack_sha256="6" * 64,
    )
    assert record.irreversible is True
    assert record.completed_runs == 180
    assert record.execution_report_sha256 == hashlib.sha256(
        report_path.read_bytes()
    ).hexdigest()
    with pytest.raises(FileExistsError):
        create_unblinding_record(
            record_path,
            state=ProtocolState.EXECUTION_COMPLETE,
            execution_schedule=schedule,
            frozen_files_unchanged=True,
            execution_report_path=report_path,
            protocol_commit="1" * 40,
            freeze_manifest_sha256="2" * 64,
            execution_schedule_sha256=schedule_sha256,
            hidden_pack_manifest_sha256="4" * 64,
            agent_visible_pack_sha256="5" * 64,
            ground_truth_pack_sha256="6" * 64,
        )


def test_unblinding_record_rejects_bypassed_state_and_incomplete_results(
    tmp_path: Path,
) -> None:
    project_root = Path(__file__).resolve().parents[2]
    config_root = project_root / "config/phase5b"
    schedule = build_execution_schedule(
        load_suite_registry(config_root / "suite-registry.v1.json"),
        load_seed_policy(config_root / "seed-policy.v1.json"),
    )
    schedule_sha256 = hashlib.sha256(
        canonical_json_bytes(schedule.model_dump(mode="json"))
    ).hexdigest()
    run_ids = tuple(item.run_id for item in schedule.runs)

    def create(path: Path, state: ProtocolState, observed: tuple[str, ...]) -> None:
        report_path = tmp_path / f"{path.stem}-execution-report.json"
        write_canonical_json(
            report_path,
            {
                "all_terminal": True,
                "evaluation_version": "phase5b.v1",
                "execution_schedule_sha256": schedule_sha256,
                "records": [
                    {
                        "observed_result_sha256": hashlib.sha256(
                            run_id.encode()
                        ).hexdigest(),
                        "run_id": run_id,
                        "terminal_status": "COMPLETED",
                    }
                    for run_id in observed
                ],
                "run_count": 180,
                "schema_version": "phase5b.execution-report.v1",
            },
        )
        create_unblinding_record(
            path,
            state=state,
            execution_schedule=schedule,
            frozen_files_unchanged=True,
            execution_report_path=report_path,
            protocol_commit="1" * 40,
            freeze_manifest_sha256="2" * 64,
            execution_schedule_sha256=schedule_sha256,
            hidden_pack_manifest_sha256="4" * 64,
            agent_visible_pack_sha256="5" * 64,
            ground_truth_pack_sha256="6" * 64,
        )

    with pytest.raises(ValueError, match="EXECUTION_COMPLETE"):
        create(
            tmp_path / "wrong-state.json",
            ProtocolState.EXECUTION_STARTED,
            run_ids,
        )
    with pytest.raises(ValueError, match="180"):
        create(
            tmp_path / "missing-run.json",
            ProtocolState.EXECUTION_COMPLETE,
            run_ids[:-1],
        )
    assert not (tmp_path / "wrong-state.json").exists()
    assert not (tmp_path / "missing-run.json").exists()
