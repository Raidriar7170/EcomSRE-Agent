from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess

import pytest

from ecomsre_rcaeval_v2.evaluation_root import (
    EVALUATION_LOCK_NAME,
    prepare_evaluation_root,
    verify_evaluation_root,
)


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
    result = subprocess.run(
        ("git", *args),
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _fixture(tmp_path: Path) -> tuple[Path, Path, Path]:
    repo = tmp_path / "repo"
    control = tmp_path / "control"
    output = tmp_path / "output"
    config = repo / "config" / "rcaeval-re2-v2-dev1"
    for directory in (
        config,
        repo / "src" / "ecomsre_rcaeval_v2",
        repo / "scripts" / "rcaeval_v2",
        repo / "tests" / "benchmarks" / "rcaeval_v2",
        repo / ".github" / "workflows",
        control / "schedules",
    ):
        directory.mkdir(parents=True, exist_ok=True)
    (repo / "src" / "ecomsre_rcaeval_v2" / "runtime.py").write_text(
        "PROTOCOL_ID = 'rcaeval-re2-v2-dev.1'\n", encoding="utf-8"
    )
    (repo / "scripts" / "rcaeval_v2" / "run.py").write_text(
        "# dev1 runner\n", encoding="utf-8"
    )
    (repo / "tests" / "benchmarks" / "rcaeval_v2" / "test_dev1.py").write_text(
        "def test_placeholder(): assert True\n", encoding="utf-8"
    )
    (repo / ".github" / "workflows" / "rcaeval-v2-dev.yml").write_text(
        "name: dev1\n", encoding="utf-8"
    )
    for name in CONFIG_NAMES:
        (config / name).write_text(
            json.dumps(
                {
                    "name": name,
                    "protocol_id": "rcaeval-re2-v2-dev.1",
                },
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
    for name in SCHEDULE_NAMES:
        (control / "schedules" / name).write_text(
            json.dumps({"name": name}, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    _git(repo, "init")
    _git(repo, "config", "user.name", "Test User")
    _git(repo, "config", "user.email", "test@example.invalid")
    _git(repo, "add", "--", ".github", "config", "scripts", "src", "tests")
    _git(repo, "commit", "-m", "fixture")
    return repo, control, output


def test_prepare_creates_external_create_once_lock_before_authorizing_output(
    tmp_path: Path,
) -> None:
    repo, control, output = _fixture(tmp_path)
    source_base = "9" * 40

    prepared = prepare_evaluation_root(
        control,
        output,
        project_root=repo,
        source_base_commit=source_base,
    )
    verified = verify_evaluation_root(control, output, project_root=repo)
    lock_path = control / "locks" / EVALUATION_LOCK_NAME
    lock_text = lock_path.read_text(encoding="utf-8")

    assert prepared == verified
    assert prepared.protocol_id == "rcaeval-re2-v2-dev.1"
    assert prepared.implementation_commit == _git(repo, "rev-parse", "HEAD")
    assert prepared.source_base_commit == source_base
    assert prepared.provider_access_authorized is True
    assert prepared.provider_calls_before_lock == 0
    assert prepared.run_attempts_before_lock == 0
    assert str(output.resolve()) not in lock_text
    assert (
        prepared.private_output_root_identity_sha256
        == hashlib.sha256(str(output.resolve()).encode("utf-8")).hexdigest()
    )
    assert (output / ".evaluation-root-authority.json").is_file()
    with pytest.raises(FileExistsError):
        prepare_evaluation_root(
            control,
            output,
            project_root=repo,
            source_base_commit=source_base,
        )


def test_prepare_fails_closed_on_dirty_tree_without_lock_or_output_marker(
    tmp_path: Path,
) -> None:
    repo, control, output = _fixture(tmp_path)
    (repo / "src" / "ecomsre_rcaeval_v2" / "runtime.py").write_text(
        "dirty = True\n", encoding="utf-8"
    )

    with pytest.raises(ValueError, match="clean"):
        prepare_evaluation_root(
            control,
            output,
            project_root=repo,
            source_base_commit="9" * 40,
        )

    assert not (control / "locks" / EVALUATION_LOCK_NAME).exists()
    assert not output.exists()


def test_verification_fails_closed_on_commit_config_schedule_or_root_drift(
    tmp_path: Path,
) -> None:
    repo, control, output = _fixture(tmp_path)
    prepare_evaluation_root(
        control,
        output,
        project_root=repo,
        source_base_commit="9" * 40,
    )
    schedule = control / "schedules" / "smoke-schedule.json"
    schedule.write_text('{"drift":true}\n', encoding="utf-8")

    with pytest.raises(ValueError, match="drift"):
        verify_evaluation_root(control, output, project_root=repo)


def test_prepare_rejects_nested_control_and_output_roots(tmp_path: Path) -> None:
    repo, control, _output = _fixture(tmp_path)

    with pytest.raises(ValueError, match="disjoint"):
        prepare_evaluation_root(
            control,
            control / "output",
            project_root=repo,
            source_base_commit="9" * 40,
        )
