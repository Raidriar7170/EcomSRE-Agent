from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from ecomsre.product.pilot.contracts_v02 import (
    PilotAttemptEventV02,
    PilotAttemptFailureDomainV02,
    PilotAttemptStageV02,
    PilotEpisodeRoleV02,
    PilotEpisodeTerminalV02,
)
from ecomsre.product.pilot.episode_runner_v02 import PilotEpisodeRepositoryV02
from ecomsre.product.storage.sqlite_store import SqliteStoreV1


def _event(
    *,
    attempt_id: str,
    slot_id: str,
    role: PilotEpisodeRoleV02,
    attempt_number: int,
    sequence: int,
    previous_stage: PilotAttemptStageV02 | None,
    stage: PilotAttemptStageV02,
    attempt_signature: str,
    failure_domain: PilotAttemptFailureDomainV02 | None = None,
    usable_fault_observation: bool | None = None,
    diagnosis_result_exists: bool | None = None,
    flag_restored: bool | None = None,
    cleanup_status: str | None = None,
    episode_terminal: PilotEpisodeTerminalV02 | None = None,
) -> PilotAttemptEventV02:
    return PilotAttemptEventV02.build(
        event_id=f"{attempt_id}-{sequence}",
        attempt_id=attempt_id,
        slot_id=slot_id,
        role=role,
        attempt_number=attempt_number,
        sequence=sequence,
        previous_stage=previous_stage,
        stage=stage,
        attempt_signature_sha256=attempt_signature,
        failure_domain=failure_domain,
        usable_fault_observation=usable_fault_observation,
        diagnosis_result_exists=diagnosis_result_exists,
        flag_restored=flag_restored,
        cleanup_status=cleanup_status,
        episode_terminal=episode_terminal,
        observed_at=datetime(2026, 8, 28, 1, sequence, tzinfo=UTC),
    )


def _finalize_infrastructure_failure(
    repository: PilotEpisodeRepositoryV02,
    *,
    attempt_id: str,
    slot_id: str,
    role: PilotEpisodeRoleV02,
    attempt_number: int,
    attempt_signature: str,
) -> None:
    repository.append_attempt_event(
        _event(
            attempt_id=attempt_id,
            slot_id=slot_id,
            role=role,
            attempt_number=attempt_number,
            sequence=2,
            previous_stage=PilotAttemptStageV02.PLANNED,
            stage=PilotAttemptStageV02.FLAG_RESTORED,
            attempt_signature=attempt_signature,
        )
    )
    repository.append_attempt_event(
        _event(
            attempt_id=attempt_id,
            slot_id=slot_id,
            role=role,
            attempt_number=attempt_number,
            sequence=3,
            previous_stage=PilotAttemptStageV02.FLAG_RESTORED,
            stage=PilotAttemptStageV02.CLEANUP_CLEAN,
            attempt_signature=attempt_signature,
        )
    )
    repository.append_attempt_event(
        _event(
            attempt_id=attempt_id,
            slot_id=slot_id,
            role=role,
            attempt_number=attempt_number,
            sequence=4,
            previous_stage=PilotAttemptStageV02.CLEANUP_CLEAN,
            stage=PilotAttemptStageV02.FINALIZED,
            attempt_signature=attempt_signature,
            failure_domain=PilotAttemptFailureDomainV02.CONNECTOR,
            usable_fault_observation=False,
            diagnosis_result_exists=False,
            flag_restored=True,
            cleanup_status="CLEAN",
            episode_terminal=PilotEpisodeTerminalV02.CONNECTOR_FAILED,
        )
    )


def test_attempt_events_are_append_only_and_follow_the_state_machine(
    tmp_path: Path,
) -> None:
    repository = PilotEpisodeRepositoryV02(SqliteStoreV1(tmp_path / "product.sqlite3"))
    planned = _event(
        attempt_id="attempt-p1-1",
        slot_id="P1",
        role=PilotEpisodeRoleV02.POSITIVE_FIT,
        attempt_number=1,
        sequence=1,
        previous_stage=None,
        stage=PilotAttemptStageV02.PLANNED,
        attempt_signature="1" * 64,
    )

    assert repository.append_attempt_event(planned) == planned
    assert repository.append_attempt_event(planned) == planned
    with pytest.raises(ValueError, match="state transition"):
        repository.append_attempt_event(
            _event(
                attempt_id="attempt-p1-1",
                slot_id="P1",
                role=PilotEpisodeRoleV02.POSITIVE_FIT,
                attempt_number=1,
                sequence=2,
                previous_stage=PilotAttemptStageV02.PLANNED,
                stage=PilotAttemptStageV02.DIAGNOSIS_PERSISTED,
                attempt_signature="1" * 64,
            )
        )

    assert repository.attempt_events("attempt-p1-1") == (planned,)


def test_only_one_eligible_infrastructure_replacement_is_admitted(
    tmp_path: Path,
) -> None:
    repository = PilotEpisodeRepositoryV02(SqliteStoreV1(tmp_path / "product.sqlite3"))
    first = _event(
        attempt_id="attempt-p1-1",
        slot_id="P1",
        role=PilotEpisodeRoleV02.POSITIVE_FIT,
        attempt_number=1,
        sequence=1,
        previous_stage=None,
        stage=PilotAttemptStageV02.PLANNED,
        attempt_signature="1" * 64,
    )
    repository.append_attempt_event(first)
    _finalize_infrastructure_failure(
        repository,
        attempt_id="attempt-p1-1",
        slot_id="P1",
        role=PilotEpisodeRoleV02.POSITIVE_FIT,
        attempt_number=1,
        attempt_signature="1" * 64,
    )

    replacement = _event(
        attempt_id="attempt-p1-2",
        slot_id="P1",
        role=PilotEpisodeRoleV02.POSITIVE_FIT,
        attempt_number=2,
        sequence=1,
        previous_stage=None,
        stage=PilotAttemptStageV02.PLANNED,
        attempt_signature="2" * 64,
    )
    assert repository.append_attempt_event(replacement) == replacement

    with pytest.raises(ValueError, match="maximum attempt count"):
        repository.append_attempt_event(
            _event(
                attempt_id="attempt-p1-3",
                slot_id="P1",
                role=PilotEpisodeRoleV02.POSITIVE_FIT,
                attempt_number=3,
                sequence=1,
                previous_stage=None,
                stage=PilotAttemptStageV02.PLANNED,
                attempt_signature="3" * 64,
            )
        )


def test_semantic_or_diagnosed_failure_cannot_be_replaced(tmp_path: Path) -> None:
    repository = PilotEpisodeRepositoryV02(SqliteStoreV1(tmp_path / "product.sqlite3"))
    first = _event(
        attempt_id="attempt-p2-1",
        slot_id="P2",
        role=PilotEpisodeRoleV02.POSITIVE_FIT,
        attempt_number=1,
        sequence=1,
        previous_stage=None,
        stage=PilotAttemptStageV02.PLANNED,
        attempt_signature="1" * 64,
    )
    repository.append_attempt_event(first)
    repository.append_attempt_event(
        _event(
            attempt_id="attempt-p2-1",
            slot_id="P2",
            role=PilotEpisodeRoleV02.POSITIVE_FIT,
            attempt_number=1,
            sequence=2,
            previous_stage=PilotAttemptStageV02.PLANNED,
            stage=PilotAttemptStageV02.FLAG_RESTORED,
            attempt_signature="1" * 64,
        )
    )
    repository.append_attempt_event(
        _event(
            attempt_id="attempt-p2-1",
            slot_id="P2",
            role=PilotEpisodeRoleV02.POSITIVE_FIT,
            attempt_number=1,
            sequence=3,
            previous_stage=PilotAttemptStageV02.FLAG_RESTORED,
            stage=PilotAttemptStageV02.CLEANUP_CLEAN,
            attempt_signature="1" * 64,
        )
    )
    repository.append_attempt_event(
        _event(
            attempt_id="attempt-p2-1",
            slot_id="P2",
            role=PilotEpisodeRoleV02.POSITIVE_FIT,
            attempt_number=1,
            sequence=4,
            previous_stage=PilotAttemptStageV02.CLEANUP_CLEAN,
            stage=PilotAttemptStageV02.FINALIZED,
            attempt_signature="1" * 64,
            failure_domain=PilotAttemptFailureDomainV02.SEMANTIC,
            usable_fault_observation=True,
            diagnosis_result_exists=True,
            flag_restored=True,
            cleanup_status="CLEAN",
            episode_terminal=PilotEpisodeTerminalV02.OPEN_WORLD_NOT_REACHED,
        )
    )

    with pytest.raises(ValueError, match="replacement is not eligible"):
        repository.append_attempt_event(
            _event(
                attempt_id="attempt-p2-2",
                slot_id="P2",
                role=PilotEpisodeRoleV02.POSITIVE_FIT,
                attempt_number=2,
                sequence=1,
                previous_stage=None,
                stage=PilotAttemptStageV02.PLANNED,
                attempt_signature="2" * 64,
            )
        )


def test_calibration_allows_three_distinct_attempts_and_rejects_repeats(
    tmp_path: Path,
) -> None:
    repository = PilotEpisodeRepositoryV02(SqliteStoreV1(tmp_path / "product.sqlite3"))
    for attempt_number, signature in enumerate(("1" * 64, "2" * 64, "3" * 64), 1):
        repository.append_attempt_event(
            _event(
                attempt_id=f"attempt-cal-{attempt_number}",
                slot_id="CALIBRATION",
                role=PilotEpisodeRoleV02.CALIBRATION,
                attempt_number=attempt_number,
                sequence=1,
                previous_stage=None,
                stage=PilotAttemptStageV02.PLANNED,
                attempt_signature=signature,
            )
        )

    with pytest.raises(ValueError, match="maximum attempt count"):
        repository.append_attempt_event(
            _event(
                attempt_id="attempt-cal-4",
                slot_id="CALIBRATION",
                role=PilotEpisodeRoleV02.CALIBRATION,
                attempt_number=4,
                sequence=1,
                previous_stage=None,
                stage=PilotAttemptStageV02.PLANNED,
                attempt_signature="4" * 64,
            )
        )

    duplicate_repository = PilotEpisodeRepositoryV02(
        SqliteStoreV1(tmp_path / "duplicate.sqlite3")
    )
    for attempt_number in (1, 2):
        event = _event(
            attempt_id=f"attempt-duplicate-{attempt_number}",
            slot_id="CALIBRATION",
            role=PilotEpisodeRoleV02.CALIBRATION,
            attempt_number=attempt_number,
            sequence=1,
            previous_stage=None,
            stage=PilotAttemptStageV02.PLANNED,
            attempt_signature="a" * 64,
        )
        if attempt_number == 1:
            duplicate_repository.append_attempt_event(event)
        else:
            with pytest.raises(ValueError, match="distinct inputs"):
                duplicate_repository.append_attempt_event(event)


def test_attempt_slot_role_is_immutable_across_replacements(tmp_path: Path) -> None:
    repository = PilotEpisodeRepositoryV02(SqliteStoreV1(tmp_path / "product.sqlite3"))
    repository.append_attempt_event(
        _event(
            attempt_id="attempt-p1-1",
            slot_id="P1",
            role=PilotEpisodeRoleV02.POSITIVE_FIT,
            attempt_number=1,
            sequence=1,
            previous_stage=None,
            stage=PilotAttemptStageV02.PLANNED,
            attempt_signature="1" * 64,
        )
    )
    _finalize_infrastructure_failure(
        repository,
        attempt_id="attempt-p1-1",
        slot_id="P1",
        role=PilotEpisodeRoleV02.POSITIVE_FIT,
        attempt_number=1,
        attempt_signature="1" * 64,
    )

    with pytest.raises(ValueError, match="slot role is immutable"):
        repository.append_attempt_event(
            _event(
                attempt_id="attempt-p1-2",
                slot_id="P1",
                role=PilotEpisodeRoleV02.POSITIVE_SHADOW,
                attempt_number=2,
                sequence=1,
                previous_stage=None,
                stage=PilotAttemptStageV02.PLANNED,
                attempt_signature="2" * 64,
            )
        )
