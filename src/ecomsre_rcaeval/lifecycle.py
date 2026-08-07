"""Append-only evaluator control-plane state journal."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Literal

from pydantic import Field, StrictInt, ValidationError

from ecomsre_rcaeval.artifacts import canonical_json_bytes, read_json_object
from ecomsre_rcaeval.contracts import RCAEvalModel
from ecomsre_rcaeval.state import HoldoutState, transition_state


class HoldoutStateEvent(RCAEvalModel):
    schema_version: Literal["rcaeval-re2.holdout-state-event.v1"] = (
        "rcaeval-re2.holdout-state-event.v1"
    )
    sequence: StrictInt = Field(gt=0)
    previous: HoldoutState
    current: HoldoutState
    evidence_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


def _events(journal_root: Path) -> tuple[HoldoutStateEvent, ...]:
    if not journal_root.exists():
        return ()
    if not journal_root.is_dir() or journal_root.is_symlink():
        raise ValueError("holdout state journal root is invalid")
    paths = tuple(sorted(journal_root.iterdir()))
    events: list[HoldoutStateEvent] = []
    previous = HoldoutState.DEV_ONLY
    for sequence, path in enumerate(paths, start=1):
        if sequence >= len(HoldoutState):
            raise ValueError("holdout state journal contains an unexpected path")
        expected_name = f"{sequence:02d}-{tuple(HoldoutState)[sequence].value}.json"
        if path.name != expected_name or not path.is_file() or path.is_symlink():
            raise ValueError("holdout state journal contains an unexpected path")
        try:
            event = HoldoutStateEvent.model_validate_json(
                canonical_json_bytes(read_json_object(path))
            )
        except (ValueError, ValidationError) as error:
            raise ValueError("holdout state journal event is invalid") from error
        if (
            event.sequence != sequence
            or event.previous is not previous
            or event.current is not transition_state(previous, event.current)
        ):
            raise ValueError("holdout state journal chain is invalid")
        events.append(event)
        previous = event.current
    return tuple(events)


def current_state(journal_root: Path) -> HoldoutState:
    events = _events(journal_root)
    return events[-1].current if events else HoldoutState.DEV_ONLY


def evidence_for_state(
    journal_root: Path,
    state: HoldoutState,
) -> str:
    for event in _events(journal_root):
        if event.current is state:
            return event.evidence_sha256
    raise ValueError(f"holdout state has no evidence binding: {state.value}")


def advance_state(
    journal_root: Path,
    target: HoldoutState,
    *,
    evidence_sha256: str,
) -> HoldoutStateEvent:
    events = _events(journal_root)
    previous = events[-1].current if events else HoldoutState.DEV_ONLY
    transition_state(previous, target)
    event = HoldoutStateEvent(
        sequence=len(events) + 1,
        previous=previous,
        current=target,
        evidence_sha256=evidence_sha256,
    )
    journal_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = journal_root / f"{event.sequence:02d}-{target.value}.json"
    payload = (
        json.dumps(
            event.model_dump(mode="json"),
            allow_nan=False,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")
    try:
        with path.open("xb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        path.chmod(0o600)
    except FileExistsError as error:
        raise ValueError("holdout state journal event already exists") from error
    return event
