from __future__ import annotations

from pathlib import Path

import pytest

import ecomsre_rcaeval.freeze as freeze_module
from ecomsre_rcaeval.artifacts import sha256_file, write_json_create_once
from ecomsre_rcaeval.freeze import (
    current_runtime_bindings,
    implementation_snapshot,
    require_clean_repository,
    require_external_control_root,
    repository_base_commit,
    verify_ground_truth_binding,
    verify_protocol_freeze,
)
from ecomsre_rcaeval.lifecycle import (
    advance_state,
    current_state,
)
from ecomsre_rcaeval.state import HoldoutState


def test_state_journal_is_create_once_linear_and_recoverable(tmp_path: Path) -> None:
    journal = tmp_path / "state-journal"

    assert current_state(journal) is HoldoutState.DEV_ONLY
    event = advance_state(
        journal,
        HoldoutState.PROTOCOL_FROZEN,
        evidence_sha256="a" * 64,
    )

    assert event.previous is HoldoutState.DEV_ONLY
    assert event.current is HoldoutState.PROTOCOL_FROZEN
    assert current_state(journal) is HoldoutState.PROTOCOL_FROZEN
    with pytest.raises(ValueError, match="invalid holdout transition"):
        advance_state(
            journal,
            HoldoutState.HOLDOUT_PREFLIGHT_PASSED,
            evidence_sha256="b" * 64,
        )


def test_state_journal_fails_closed_on_unexpected_files(tmp_path: Path) -> None:
    journal = tmp_path / "state-journal"
    journal.mkdir()
    (journal / "notes.txt").write_text("untrusted", encoding="utf-8")

    with pytest.raises(ValueError, match="unexpected path"):
        current_state(journal)


def test_protocol_freeze_is_revalidated_and_bound_to_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    control = tmp_path / "control"
    snapshot = implementation_snapshot()

    def complete_snapshot(*, require_complete: bool = False) -> dict[str, object]:
        assert require_complete is True
        return snapshot

    monkeypatch.setattr(
        freeze_module,
        "implementation_snapshot",
        complete_snapshot,
    )
    schedule_path = control / "locks" / "holdout-schedule.json"
    schedule_sha = write_json_create_once(
        schedule_path,
        {"schema_version": "synthetic-schedule", "records": []},
    )
    freeze_path = control / "locks" / "protocol-freeze.json"
    freeze_sha = write_json_create_once(
        freeze_path,
        {
            "schema_version": "rcaeval-re2.protocol-freeze.v2",
            "repository_base_commit": repository_base_commit(),
            "implementation_snapshot": snapshot,
            **current_runtime_bindings(),
            "holdout_schedule_sha256": schedule_sha,
            "development_evidence": {
                "dataset_audit_sha256": "a" * 64,
                "smoke_sha256": "b" * 64,
                "real_provider_pilot_sha256": "c" * 64,
            },
        },
    )
    advance_state(
        control / "state-journal",
        HoldoutState.PROTOCOL_FROZEN,
        evidence_sha256=freeze_sha,
    )

    assert verify_protocol_freeze(control)["holdout_schedule_sha256"] == schedule_sha

    freeze_path.write_bytes(freeze_path.read_bytes() + b"\n")
    assert sha256_file(freeze_path) != freeze_sha
    with pytest.raises(ValueError, match="state binding"):
        verify_protocol_freeze(control)


def test_implementation_snapshot_uses_full_content_sha256() -> None:
    snapshot = implementation_snapshot()
    assert snapshot["implementation_commit"] == repository_base_commit()
    assert isinstance(snapshot["files"], dict)
    assert snapshot["scoped_file_count"] == len(snapshot["files"])
    for field in (
        "tracked_diff_sha256",
        "scoped_closure_sha256",
        "scoped_paths_sha256",
    ):
        value = snapshot[field]
        assert isinstance(value, str)
        assert len(value) == 64
    assert all(
        len(item["sha256"]) == 64
        for item in snapshot["files"].values()
    )


def test_source_bound_freeze_rejects_repository_control_root_and_dirty_tree(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(ValueError, match="external"):
        require_external_control_root(freeze_module.PROJECT_ROOT / "private-control")

    monkeypatch.setattr(
        freeze_module,
        "_git_bytes",
        lambda *_arguments: b"?? unexpected.txt\0",
    )
    with pytest.raises(ValueError, match="clean"):
        require_clean_repository()


def test_ground_truth_cannot_change_between_preflight_and_unblinding(
    tmp_path: Path,
) -> None:
    control = tmp_path / "control"
    journal = control / "state-journal"
    truth = tmp_path / "evaluator-only" / "ground-truth.json"
    truth.parent.mkdir()
    truth.write_text('{"case":"first"}\n', encoding="utf-8")
    truth_sha = sha256_file(truth)
    advance_state(journal, HoldoutState.PROTOCOL_FROZEN, evidence_sha256="a" * 64)
    seal_sha = write_json_create_once(
        control / "locks" / "holdout-seal.json",
        {"ground_truth_sha256": truth_sha},
    )
    advance_state(journal, HoldoutState.HOLDOUT_SEALED, evidence_sha256=seal_sha)
    preflight_sha = write_json_create_once(
        control / "locks" / "holdout-preflight.json",
        {"ground_truth_sha256": truth_sha},
    )
    advance_state(
        journal,
        HoldoutState.HOLDOUT_PREFLIGHT_PASSED,
        evidence_sha256=preflight_sha,
    )

    assert verify_ground_truth_binding(control, truth) == truth_sha
    truth.write_text('{"case":"changed"}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="Ground Truth drifted"):
        verify_ground_truth_binding(control, truth)
