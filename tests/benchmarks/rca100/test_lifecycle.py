from __future__ import annotations

from pathlib import Path

import pytest

from ecomsre_rca100.lifecycle import (
    PrivateRoots,
    advance_state,
    build_schedule,
    create_once_json,
    current_state,
    schedule_sha256,
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
