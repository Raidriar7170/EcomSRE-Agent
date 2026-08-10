from __future__ import annotations

from pathlib import Path
import subprocess

import pytest

from ecomsre_rca100.evaluation_integrity import verify_frozen_repository
from ecomsre_rca100.lifecycle import (
    PrivateRoots,
    advance_state,
    build_schedule,
    create_once_json,
    current_state,
    schedule_sha256,
    tree_sha256,
    verify_tree_binding,
)


def test_schedule_is_deterministic_complete_and_identity_private() -> None:
    tasks = tuple(f"t{index:03d}" for index in range(1, 104))

    first = build_schedule(tasks)
    second = build_schedule(tasks)

    assert first == second
    assert schedule_sha256(first) == schedule_sha256(second)
    assert len(first.records) == 103
    assert first.records[0].source_task_id != "t001"
    assert len({item.run_id for item in first.records}) == 103


def test_state_chain_and_create_once_are_fail_closed(tmp_path: Path) -> None:
    control = tmp_path / "control"

    advance_state(control, "SOURCE_LOCKED", bindings={"lock": "a" * 64})
    assert current_state(control) == "SOURCE_LOCKED"
    advance_state(control, "INPUTS_ACQUIRED", bindings={"lock": "b" * 64})
    assert current_state(control) == "INPUTS_ACQUIRED"
    with pytest.raises((FileExistsError, ValueError)):
        advance_state(control, "INPUTS_ACQUIRED", bindings={})
    with pytest.raises(FileExistsError):
        create_once_json(control / "state" / "SOURCE_LOCKED.json", {})


def test_private_roots_reject_repository_nesting(tmp_path: Path) -> None:
    repository = tmp_path / "repo"
    repository.mkdir()
    roots = PrivateRoots(
        input_source=tmp_path / "input",
        control=repository / "control",
        schedule=tmp_path / "schedule",
        journal=tmp_path / "journal",
        output=tmp_path / "output",
        evaluator_source=tmp_path / "eval-source",
        evaluator=tmp_path / "eval",
    )

    with pytest.raises(ValueError, match="Git repository"):
        roots.validate(repository_root=repository)


def test_tree_binding_freshly_detects_content_drift(tmp_path: Path) -> None:
    frozen = tmp_path / "frozen"
    frozen.mkdir()
    first = frozen / "first.json"
    first.write_text('{"value":1}\n', encoding="utf-8")
    digest, count = tree_sha256(frozen)

    assert verify_tree_binding(
        frozen,
        expected_sha256=digest,
        expected_file_count=count,
        label="synthetic input",
    ) == (digest, 1)

    first.write_text('{"value":2}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="synthetic input tree differs"):
        verify_tree_binding(
            frozen,
            expected_sha256=digest,
            expected_file_count=count,
            label="synthetic input",
        )


def test_frozen_repository_allows_only_generated_public_result_paths(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    subprocess.run(("git", "init", "-q"), cwd=repository, check=True)
    subprocess.run(
        ("git", "config", "user.email", "synthetic@example.invalid"),
        cwd=repository,
        check=True,
    )
    subprocess.run(
        ("git", "config", "user.name", "Synthetic Test"),
        cwd=repository,
        check=True,
    )
    protected = repository / "src" / "ecomsre_rca100" / "evaluator.py"
    protected.parent.mkdir(parents=True)
    protected.write_text("FROZEN = True\n", encoding="utf-8")
    subprocess.run(("git", "add", str(protected)), cwd=repository, check=True)
    subprocess.run(
        ("git", "commit", "-q", "-m", "synthetic baseline"),
        cwd=repository,
        check=True,
    )
    implementation_commit = subprocess.run(
        ("git", "rev-parse", "HEAD"),
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    allowed = (
        repository
        / "docs"
        / "results"
        / "rca100-metrics-arbitration-v1-final.json"
    )
    allowed.parent.mkdir(parents=True)
    allowed.write_text("{}\n", encoding="utf-8")

    verify_frozen_repository(
        repository,
        implementation_commit=implementation_commit,
    )

    protected.write_text("FROZEN = False\n", encoding="utf-8")
    with pytest.raises(ValueError, match="frozen repository paths changed"):
        verify_frozen_repository(
            repository,
            implementation_commit=implementation_commit,
        )
