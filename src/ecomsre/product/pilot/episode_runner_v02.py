from __future__ import annotations

import json

from ecomsre.product.pilot.contracts_v02 import (
    PILOT_ATTEMPT_TRANSITIONS_V02,
    LivePilotEpisodeV02,
    PilotAttemptEventV02,
    PilotAttemptStageV02,
    PilotEpisodeRoleV02,
)
from ecomsre.product.storage.sqlite_store import SqliteStoreV1


def _json(payload: object) -> str:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


class PilotEpisodeRepositoryV02:
    def __init__(self, store: SqliteStoreV1) -> None:
        self.store = store

    def create(self, episode: LivePilotEpisodeV02) -> LivePilotEpisodeV02:
        validated = LivePilotEpisodeV02.model_validate(episode.model_dump(mode="python"))
        serialized = _json(validated.model_dump(mode="json"))
        with self.store.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                existing = connection.execute(
                    "SELECT payload_json FROM live_pilot_episodes_v02 WHERE episode_id = ?",
                    (validated.episode_id,),
                ).fetchone()
                if existing is not None:
                    prior = LivePilotEpisodeV02.model_validate_json(existing["payload_json"])
                    if prior != validated:
                        raise ValueError("pilot episode ID is already bound to different bytes")
                    connection.execute("COMMIT")
                    return prior
                connection.execute(
                    "INSERT INTO live_pilot_episodes_v02("
                    "episode_id, environment_id, role, episode_sha256, payload_json, created_at"
                    ") VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        validated.episode_id,
                        validated.environment_id,
                        validated.role.value,
                        validated.episode_sha256,
                        serialized,
                        validated.observed_at.isoformat(),
                    ),
                )
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise
        return validated

    def get(self, episode_id: str) -> LivePilotEpisodeV02:
        with self.store.connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM live_pilot_episodes_v02 WHERE episode_id = ?",
                (episode_id,),
            ).fetchone()
        if row is None:
            raise ValueError("pilot episode does not exist")
        return LivePilotEpisodeV02.model_validate_json(row["payload_json"])

    def list_for_environment(self, environment_id: str) -> tuple[LivePilotEpisodeV02, ...]:
        with self.store.connect() as connection:
            rows = connection.execute(
                "SELECT payload_json FROM live_pilot_episodes_v02 "
                "WHERE environment_id = ? ORDER BY created_at, episode_id",
                (environment_id,),
            ).fetchall()
        return tuple(
            LivePilotEpisodeV02.model_validate_json(row["payload_json"]) for row in rows
        )

    def append_attempt_event(self, event: PilotAttemptEventV02) -> PilotAttemptEventV02:
        validated = PilotAttemptEventV02.model_validate(event.model_dump(mode="python"))
        serialized = _json(validated.model_dump(mode="json"))
        with self.store.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                existing = connection.execute(
                    "SELECT payload_json FROM live_pilot_attempt_events_v02 "
                    "WHERE event_id = ?",
                    (validated.event_id,),
                ).fetchone()
                if existing is not None:
                    prior = PilotAttemptEventV02.model_validate_json(
                        existing["payload_json"]
                    )
                    if prior != validated:
                        raise ValueError("pilot attempt event ID is bound to different bytes")
                    connection.execute("COMMIT")
                    return prior
                previous_row = connection.execute(
                    "SELECT payload_json FROM live_pilot_attempt_events_v02 "
                    "WHERE attempt_id = ? ORDER BY sequence DESC LIMIT 1",
                    (validated.attempt_id,),
                ).fetchone()
                if previous_row is None:
                    self._validate_new_attempt(connection, validated)
                else:
                    previous = PilotAttemptEventV02.model_validate_json(
                        previous_row["payload_json"]
                    )
                    if (
                        validated.slot_id != previous.slot_id
                        or validated.role is not previous.role
                        or validated.attempt_number != previous.attempt_number
                        or validated.attempt_signature_sha256
                        != previous.attempt_signature_sha256
                        or validated.sequence != previous.sequence + 1
                        or validated.previous_stage is not previous.stage
                        or validated.stage
                        not in PILOT_ATTEMPT_TRANSITIONS_V02[previous.stage]
                    ):
                        raise ValueError("pilot attempt state transition is invalid")
                connection.execute(
                    "INSERT INTO live_pilot_attempt_events_v02("
                    "event_id, attempt_id, slot_id, role, attempt_number, sequence, stage, "
                    "attempt_signature_sha256, event_sha256, payload_json, created_at"
                    ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        validated.event_id,
                        validated.attempt_id,
                        validated.slot_id,
                        validated.role.value,
                        validated.attempt_number,
                        validated.sequence,
                        validated.stage.value,
                        validated.attempt_signature_sha256,
                        validated.event_sha256,
                        serialized,
                        validated.observed_at.isoformat(),
                    ),
                )
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise
        return validated

    @staticmethod
    def _validate_new_attempt(connection: object, event: PilotAttemptEventV02) -> None:
        if event.stage is not PilotAttemptStageV02.PLANNED or event.sequence != 1:
            raise ValueError("new pilot attempt must begin in PLANNED")
        rows = connection.execute(  # type: ignore[attr-defined]
            "SELECT payload_json FROM live_pilot_attempt_events_v02 "
            "WHERE slot_id = ? AND sequence = 1 ORDER BY attempt_number",
            (event.slot_id,),
        ).fetchall()
        expected_number = len(rows) + 1
        maximum_attempts = (
            3 if event.role is PilotEpisodeRoleV02.CALIBRATION else 2
        )
        if event.attempt_number != expected_number or expected_number > maximum_attempts:
            raise ValueError("pilot slot maximum attempt count or order was exceeded")
        starts = tuple(PilotAttemptEventV02.model_validate_json(row["payload_json"]) for row in rows)
        if any(prior.role is not event.role for prior in starts):
            raise ValueError("pilot slot role is immutable")
        if event.role is PilotEpisodeRoleV02.CALIBRATION:
            if any(
                prior.attempt_signature_sha256 == event.attempt_signature_sha256
                for prior in starts
            ):
                raise ValueError("calibration attempts require distinct inputs")
            return
        if starts:
            last_row = connection.execute(  # type: ignore[attr-defined]
                "SELECT payload_json FROM live_pilot_attempt_events_v02 "
                "WHERE attempt_id = ? ORDER BY sequence DESC LIMIT 1",
                (starts[-1].attempt_id,),
            ).fetchone()
            if last_row is None:
                raise ValueError("replacement is not eligible")
            final = PilotAttemptEventV02.model_validate_json(last_row["payload_json"])
            if not final.replacement_eligible():
                raise ValueError("replacement is not eligible")

    def attempt_events(self, attempt_id: str) -> tuple[PilotAttemptEventV02, ...]:
        with self.store.connect() as connection:
            rows = connection.execute(
                "SELECT payload_json FROM live_pilot_attempt_events_v02 "
                "WHERE attempt_id = ? ORDER BY sequence",
                (attempt_id,),
            ).fetchall()
        return tuple(
            PilotAttemptEventV02.model_validate_json(row["payload_json"])
            for row in rows
        )


__all__ = ("PilotEpisodeRepositoryV02",)
