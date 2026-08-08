from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess

import pytest
import ecomsre_rcaeval_v2.dev2_evaluation_root as evaluation_root

from ecomsre_rcaeval_v2.dev2_admission import (
    ScheduleAdmissionLock,
    write_admission_lock,
)
from ecomsre_rcaeval_v2.dev2_evaluation_root import (
    EVALUATION_LOCK_NAME,
    prepare_evaluation_root,
    verify_evaluation_root,
    verify_provider_ready,
)
from ecomsre_rcaeval_v2.dev2_paths import tree_sha256


CONFIG_NAMES = (
    "protocol.json",
    "dataset-lock.json",
    "split-lock.json",
    "model-prompt-lock.json",
    "budget-lock.json",
    "indicator-lock.json",
    "schedule-generation.json",
    "evaluation-policy.json",
)
SCHEDULE_NAMES = (
    "smoke-schedule.json",
    "design-schedule.json",
    "dev-validation-schedule.json",
    "schedule-set-lock.json",
)


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ("git", *args),
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def test_private_schedule_root_must_be_disjoint_from_control_root(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="pairwise disjoint"):
        evaluation_root._validate_roots(
            tmp_path / "repo",
            tmp_path / "control",
            tmp_path / "control/schedules",
            tmp_path / "output",
            tmp_path / "smoke",
            tmp_path / "design",
        )


def test_prepare_rejects_preserved_root_nesting_before_writing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, control, _schedule, output, smoke, design, source_base = _fixture(tmp_path)
    old_v1 = tmp_path / "old-dev-v1"
    private_schedule = old_v1 / "nested-private-schedules"
    private_schedule.mkdir(parents=True)
    for name in SCHEDULE_NAMES:
        (private_schedule / name).write_text(
            json.dumps({"name": name}) + "\n", encoding="utf-8"
        )
    monkeypatch.setattr(evaluation_root, "SOURCE_BASE_COMMIT", source_base)
    with pytest.raises(ValueError, match="pairwise disjoint"):
        prepare_evaluation_root(
            control,
            private_schedule,
            output,
            smoke,
            design,
            project_root=repo,
            source_base_commit=source_base,
            preserved_roots={
                "v2_dev_v1": old_v1,
                "v2_dev1_control": tmp_path / "old-dev1-control",
                "v2_dev1_output": tmp_path / "old-dev1-output",
            },
        )
    assert not (control / "locks" / EVALUATION_LOCK_NAME).exists()
    assert not (private_schedule / ".evaluation-root-authority.json").exists()


def _fixture(tmp_path: Path) -> tuple[Path, Path, Path, Path, Path, Path, str]:
    repo = tmp_path / "repo"
    control = tmp_path / "control"
    private_schedule = tmp_path / "private-schedules"
    output = tmp_path / "output"
    smoke = tmp_path / "smoke-journal"
    design = tmp_path / "design-journal"
    config = repo / "config" / "rcaeval-re2-v2-dev2"
    for directory in (
        config,
        repo / "config/rcaeval-re2-v1",
        repo / "src/ecomsre_rcaeval_v2",
        repo / "scripts/rcaeval_v2",
        repo / "tests/benchmarks/rcaeval_v2",
        repo / ".github/workflows",
        private_schedule,
    ):
        directory.mkdir(parents=True, exist_ok=True)
    (repo / "src/ecomsre_rcaeval_v2/runtime.py").write_text("DEV2 = True\n", encoding="utf-8")
    (repo / "scripts/rcaeval_v2/run.py").write_text("# dev2\n", encoding="utf-8")
    (repo / "tests/benchmarks/rcaeval_v2/test_dev2.py").write_text("def test_ok(): assert True\n", encoding="utf-8")
    (repo / ".github/workflows/rcaeval-v2-dev.yml").write_text("name: dev2\n", encoding="utf-8")
    for name in CONFIG_NAMES:
        payload = {
            "name": name,
            **({"source_base_commit": "PENDING"} if name == "protocol.json" else {}),
        }
        (config / name).write_text(json.dumps(payload) + "\n", encoding="utf-8")
    (repo / "config/rcaeval-re2-v1/schedule-generation.json").write_text(
        json.dumps({"expected_schedule_sha256": "1" * 64}) + "\n",
        encoding="utf-8",
    )
    for name in SCHEDULE_NAMES:
        (private_schedule / name).write_text(json.dumps({"name": name}) + "\n", encoding="utf-8")
    _git(repo, "init")
    _git(repo, "config", "user.name", "Test User")
    _git(repo, "config", "user.email", "test@example.invalid")
    _git(repo, "add", "--", ".github", "config", "scripts", "src", "tests")
    _git(repo, "commit", "-m", "base fixture")
    source_base = _git(repo, "rev-parse", "HEAD")
    (config / "protocol.json").write_text(
        json.dumps({"name": "protocol.json", "source_base_commit": source_base})
        + "\n",
        encoding="utf-8",
    )
    _git(repo, "add", "--", str(config / "protocol.json"))
    _git(repo, "commit", "-m", "implementation fixture")
    return repo, control, private_schedule, output, smoke, design, source_base


def _preserved_roots(parent: Path) -> dict[str, Path]:
    return {
        "v2_dev_v1": parent / "old-dev-v1",
        "v2_dev1_control": parent / "old-dev1-control",
        "v2_dev1_output": parent / "old-dev1-output",
    }


def _admission(evaluation, parent: Path) -> ScheduleAdmissionLock:
    roots = _preserved_roots(parent)
    old_v1 = roots["v2_dev_v1"]
    old_dev1_control = roots["v2_dev1_control"]
    old_dev1_output = roots["v2_dev1_output"]
    (old_v1 / "runs").mkdir(parents=True, exist_ok=True)
    (old_v1 / "schedule").mkdir(parents=True, exist_ok=True)
    (old_dev1_control / "schedules").mkdir(parents=True, exist_ok=True)
    old_dev1_output.mkdir(parents=True, exist_ok=True)
    (old_v1 / "runs/terminal.json").write_text("preserved-v1\n", encoding="utf-8")
    (old_dev1_output / "terminal.json").write_text(
        "preserved-dev1\n", encoding="utf-8"
    )
    schedule_paths = {
        "v2_dev_v1_design": old_v1 / "schedule/design-schedule.json",
        "v2_dev_v1_validation": old_v1
        / "schedule/dev-validation-schedule.json",
        "v2_dev1_design": old_dev1_control / "schedules/design-schedule.json",
        "v2_dev1_validation": old_dev1_control
        / "schedules/dev-validation-schedule.json",
    }
    for name, path in schedule_paths.items():
        path.write_text(f"{name}\n", encoding="utf-8")
    return ScheduleAdmissionLock.model_validate(
        {
            "schema_version": "rcaeval-re2-v2-dev2.schedule-admission-lock.v1",
            "protocol_id": "rcaeval-re2-v2-dev.2",
            "implementation_commit": evaluation.implementation_commit,
            "split_lock_sha256": evaluation.split_lock_sha256,
            "smoke_schedule_sha256": evaluation.smoke_schedule_sha256,
            "design_schedule_sha256": evaluation.design_schedule_sha256,
            "validation_schedule_sha256": evaluation.validation_schedule_sha256,
            "schedule_set_sha256": evaluation.schedule_set_sha256,
            "v1_external_schedule_sha256": "1" * 64,
            "private_schedule_root_identity_sha256": evaluation.private_schedule_root_identity_sha256,
            "private_output_root_identity_sha256": evaluation.private_output_root_identity_sha256,
            "smoke_journal_root_identity_sha256": evaluation.smoke_journal_root_identity_sha256,
            "design_journal_root_identity_sha256": evaluation.design_journal_root_identity_sha256,
            "preserved_schedule_hashes": {
                name: hashlib.sha256(path.read_bytes()).hexdigest()
                for name, path in schedule_paths.items()
            },
            "preserved_root_identity_sha256": {
                name: hashlib.sha256(str(path.resolve()).encode()).hexdigest()
                for name, path in roots.items()
            },
            "preserved_evidence_hashes": {
                "v2_dev_v1_terminal_tree": tree_sha256(old_v1 / "runs"),
                "v2_dev1_terminal_tree": tree_sha256(old_dev1_output),
            },
            "smoke": {"admitted": 72, "rejected": 0},
            "design": {"admitted": 360, "rejected": 0},
            "dev_validation_metadata": {"admitted": 480, "rejected": 0, "values_accessed": False},
            "v1_contract_construction": {"admitted": 216, "call_position_min": 1, "call_position_max": 3},
            "v2_contract_construction": {"admitted": 216, "family_position_min": 1, "family_position_max": 3},
            "run_id_checks": {"all": True},
            "old_new_overlap_checks": {"overlap_count": 0},
            "budget_checks": {"within_locked_caps": True},
            "provider_objects_constructed": 0,
            "provider_calls": 0,
            "run_attempts_created": 0,
            "operation_attempts_created": 0,
            "verdict": "V2_DEV2_ADMISSION_REHEARSAL_PASSED",
        }
    )


def test_provider_ready_requires_evaluation_then_matching_admission_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, control, private_schedule, output, smoke, design, source_base = _fixture(tmp_path)
    monkeypatch.setattr(evaluation_root, "SOURCE_BASE_COMMIT", source_base)
    evaluation = prepare_evaluation_root(
        control,
        private_schedule,
        output,
        smoke,
        design,
        project_root=repo,
        source_base_commit=source_base,
        preserved_roots=_preserved_roots(tmp_path),
    )
    assert evaluation == verify_evaluation_root(
        control, private_schedule, output, smoke, design, project_root=repo
    )
    with pytest.raises(ValueError, match="admission"):
        verify_provider_ready(
            control,
            private_schedule,
            output,
            smoke,
            design,
            project_root=repo,
            preserved_roots=_preserved_roots(tmp_path),
        )
    admission = _admission(evaluation, tmp_path)
    write_admission_lock(control / "locks/schedule-admission-lock.json", admission)
    verified_evaluation, verified_admission = verify_provider_ready(
        control,
        private_schedule,
        output,
        smoke,
        design,
        project_root=repo,
        preserved_roots=_preserved_roots(tmp_path),
    )
    assert verified_evaluation == evaluation
    assert verified_admission == admission
    lock_text = (control / "locks" / EVALUATION_LOCK_NAME).read_text(encoding="utf-8")
    assert str(output.resolve()) not in lock_text
    assert str(private_schedule.resolve()) not in lock_text
    assert evaluation.private_schedule_root_identity_sha256 == hashlib.sha256(
        str(private_schedule.resolve()).encode()
    ).hexdigest()
    assert evaluation.private_output_root_identity_sha256 == hashlib.sha256(
        str(output.resolve()).encode()
    ).hexdigest()


def test_provider_ready_fails_closed_on_schedule_or_source_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, control, private_schedule, output, smoke, design, source_base = _fixture(tmp_path)
    monkeypatch.setattr(evaluation_root, "SOURCE_BASE_COMMIT", source_base)
    evaluation = prepare_evaluation_root(
        control,
        private_schedule,
        output,
        smoke,
        design,
        project_root=repo,
        source_base_commit=source_base,
        preserved_roots=_preserved_roots(tmp_path),
    )
    write_admission_lock(
        control / "locks/schedule-admission-lock.json", _admission(evaluation, tmp_path)
    )
    (private_schedule / "smoke-schedule.json").write_text('{"drift":true}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="drift"):
        verify_provider_ready(
            control,
            private_schedule,
            output,
            smoke,
            design,
            project_root=repo,
            preserved_roots=_preserved_roots(tmp_path),
        )


def test_provider_ready_fails_closed_on_preserved_terminal_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, control, private_schedule, output, smoke, design, source_base = _fixture(tmp_path)
    monkeypatch.setattr(evaluation_root, "SOURCE_BASE_COMMIT", source_base)
    evaluation = prepare_evaluation_root(
        control,
        private_schedule,
        output,
        smoke,
        design,
        project_root=repo,
        source_base_commit=source_base,
        preserved_roots=_preserved_roots(tmp_path),
    )
    admission = _admission(evaluation, tmp_path)
    write_admission_lock(
        control / "locks/schedule-admission-lock.json", admission
    )
    (tmp_path / "old-dev1-output/terminal.json").write_text(
        "mutated\n", encoding="utf-8"
    )
    with pytest.raises(ValueError, match="preserved terminal evidence drift"):
        verify_provider_ready(
            control,
            private_schedule,
            output,
            smoke,
            design,
            project_root=repo,
            preserved_roots=_preserved_roots(tmp_path),
        )


def test_evaluation_root_fails_closed_on_dirty_tree_before_writing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, control, private_schedule, output, smoke, design, source_base = _fixture(tmp_path)
    monkeypatch.setattr(evaluation_root, "SOURCE_BASE_COMMIT", source_base)
    (repo / "src/ecomsre_rcaeval_v2/runtime.py").write_text("dirty = True\n", encoding="utf-8")
    with pytest.raises(ValueError, match="clean"):
        prepare_evaluation_root(
            control,
            private_schedule,
            output,
            smoke,
            design,
            project_root=repo,
            source_base_commit=source_base,
            preserved_roots=_preserved_roots(tmp_path),
        )
    assert not (control / "locks" / EVALUATION_LOCK_NAME).exists()
    assert not output.exists()
