"""Explicit, provenance-preserving Phase 0 state transitions."""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import secrets
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import Enum
from typing import Any

from ecomsre.phase0.models import Outcome, Phase0Policy, TerminalResult


class Phase0State(str, Enum):
    INITIAL = "INITIAL"
    PREFLIGHT = "PREFLIGHT"
    STARTUP = "STARTUP"
    READINESS = "READINESS"
    STABILIZING_BASELINE = "STABILIZING_BASELINE"
    MEASURING_BASELINE = "MEASURING_BASELINE"
    INJECTING = "INJECTING"
    STABILIZING_FAULT = "STABILIZING_FAULT"
    MEASURING_FAULT = "MEASURING_FAULT"
    RESETTING = "RESETTING"
    STABILIZING_RECOVERY = "STABILIZING_RECOVERY"
    MEASURING_RECOVERY = "MEASURING_RECOVERY"
    TELEMETRY_READINESS = "TELEMETRY_READINESS"
    EVIDENCE_FREEZE = "EVIDENCE_FREEZE"
    SHUTDOWN = "SHUTDOWN"
    TERMINAL = "TERMINAL"


_LINEAR_TRANSITIONS = {
    Phase0State.INITIAL: Phase0State.PREFLIGHT,
    Phase0State.PREFLIGHT: Phase0State.STARTUP,
    Phase0State.STARTUP: Phase0State.READINESS,
    Phase0State.READINESS: Phase0State.STABILIZING_BASELINE,
    Phase0State.STABILIZING_BASELINE: Phase0State.MEASURING_BASELINE,
    Phase0State.MEASURING_BASELINE: Phase0State.INJECTING,
    Phase0State.INJECTING: Phase0State.STABILIZING_FAULT,
    Phase0State.STABILIZING_FAULT: Phase0State.MEASURING_FAULT,
    Phase0State.MEASURING_FAULT: Phase0State.RESETTING,
    Phase0State.RESETTING: Phase0State.STABILIZING_RECOVERY,
    Phase0State.STABILIZING_RECOVERY: Phase0State.MEASURING_RECOVERY,
    Phase0State.TELEMETRY_READINESS: Phase0State.EVIDENCE_FREEZE,
    Phase0State.EVIDENCE_FREEZE: Phase0State.SHUTDOWN,
}

_STATE_TIMEOUT_SECONDS = {
    Phase0State.INITIAL: 60,
    Phase0State.PREFLIGHT: 60,
    Phase0State.STARTUP: 300,
    Phase0State.READINESS: 180,
    Phase0State.STABILIZING_BASELINE: 180,
    Phase0State.MEASURING_BASELINE: 180,
    Phase0State.INJECTING: 30,
    Phase0State.STABILIZING_FAULT: 180,
    Phase0State.MEASURING_FAULT: 180,
    Phase0State.RESETTING: 30,
    Phase0State.STABILIZING_RECOVERY: 180,
    Phase0State.MEASURING_RECOVERY: 180,
    Phase0State.TELEMETRY_READINESS: 180,
    Phase0State.EVIDENCE_FREEZE: 60,
    Phase0State.SHUTDOWN: 120,
}

_CANONICAL_POLICY = Phase0Policy()
_STABILIZING_STATES = {
    Phase0State.STABILIZING_BASELINE,
    Phase0State.STABILIZING_FAULT,
    Phase0State.STABILIZING_RECOVERY,
}
_DEADLINE_FAILURES = {
    Phase0State.INITIAL: (
        Outcome.BLOCKED_ENVIRONMENT,
        "PREFLIGHT_BLOCKED",
    ),
    Phase0State.PREFLIGHT: (
        Outcome.BLOCKED_ENVIRONMENT,
        "PREFLIGHT_BLOCKED",
    ),
    Phase0State.STARTUP: (
        Outcome.BLOCKED_ENVIRONMENT,
        "PREFLIGHT_BLOCKED",
    ),
    Phase0State.READINESS: (
        Outcome.BLOCKED_ENVIRONMENT,
        "TELEMETRY_NOT_READY",
    ),
    Phase0State.STABILIZING_BASELINE: (
        Outcome.FAILED_ACCEPTANCE,
        "WINDOW_SAMPLE_TIMEOUT",
    ),
    Phase0State.MEASURING_BASELINE: (
        Outcome.FAILED_ACCEPTANCE,
        "WINDOW_SAMPLE_TIMEOUT",
    ),
    Phase0State.INJECTING: (
        Outcome.FAILED_ACCEPTANCE,
        "INJECT_TIMEOUT",
    ),
    Phase0State.STABILIZING_FAULT: (
        Outcome.FAILED_ACCEPTANCE,
        "WINDOW_SAMPLE_TIMEOUT",
    ),
    Phase0State.MEASURING_FAULT: (
        Outcome.FAILED_ACCEPTANCE,
        "WINDOW_SAMPLE_TIMEOUT",
    ),
    Phase0State.RESETTING: (
        Outcome.FAILED_ACCEPTANCE,
        "RESET_TIMEOUT",
    ),
    Phase0State.STABILIZING_RECOVERY: (
        Outcome.FAILED_ACCEPTANCE,
        "WINDOW_SAMPLE_TIMEOUT",
    ),
    Phase0State.MEASURING_RECOVERY: (
        Outcome.FAILED_ACCEPTANCE,
        "WINDOW_SAMPLE_TIMEOUT",
    ),
    Phase0State.TELEMETRY_READINESS: (
        Outcome.FAILED_ACCEPTANCE,
        "TELEMETRY_NOT_READY",
    ),
    Phase0State.EVIDENCE_FREEZE: (
        Outcome.FAILED_ACCEPTANCE,
        "EVIDENCE_INCOMPLETE",
    ),
    Phase0State.SHUTDOWN: (
        Outcome.MANUAL_INTERVENTION_REQUIRED,
        "CLEANUP_INCOMPLETE",
    ),
}

_CONSTRUCTION_TOKEN = object()
_CHECKPOINT_KEY = secrets.token_bytes(32)


@dataclass(frozen=True)
class TransitionEvent:
    sequence: int
    from_state: Phase0State
    to_state: Phase0State
    entered_at: datetime
    entered_monotonic: float
    deadline_at: datetime | None
    deadline_monotonic: float | None
    earliest_exit_at: datetime | None
    earliest_exit_monotonic: float | None
    cycle_number: int
    reason_code: str | None = None
    deadline_exceeded: bool = False

    def __post_init__(self) -> None:
        _require_utc(self.entered_at)
        _require_monotonic_value(self.entered_monotonic)
        if self.deadline_at is not None:
            _require_utc(self.deadline_at)
            if self.deadline_at <= self.entered_at:
                raise ValueError("transition deadline must follow entered_at")
        if self.deadline_monotonic is not None:
            _require_monotonic_value(self.deadline_monotonic)
            if self.deadline_monotonic <= self.entered_monotonic:
                raise ValueError(
                    "transition monotonic deadline must follow entered time"
                )
        if self.earliest_exit_at is not None:
            _require_utc(self.earliest_exit_at)
            if self.earliest_exit_at <= self.entered_at:
                raise ValueError("transition earliest exit must follow entered_at")
        if self.earliest_exit_monotonic is not None:
            _require_monotonic_value(self.earliest_exit_monotonic)
            if self.earliest_exit_monotonic <= self.entered_monotonic:
                raise ValueError(
                    "transition monotonic earliest exit must follow entered time"
                )


@dataclass(frozen=True, init=False)
class StateCheckpoint:
    state: Phase0State
    cycle_number: int
    started_at: datetime
    started_monotonic: float
    entered_at: datetime
    entered_monotonic: float
    deadline_at: datetime | None
    deadline_monotonic: float | None
    earliest_exit_at: datetime | None
    earliest_exit_monotonic: float | None
    transition_events: tuple[TransitionEvent, ...]
    terminal_result: TerminalResult | None
    integrity_sha256: str
    _provenance: object = field(repr=False, compare=False)

    def __init__(
        self,
        *,
        _token: object | None = None,
        state: Phase0State,
        cycle_number: int,
        started_at: datetime,
        started_monotonic: float,
        entered_at: datetime,
        entered_monotonic: float,
        deadline_at: datetime | None,
        deadline_monotonic: float | None,
        earliest_exit_at: datetime | None,
        earliest_exit_monotonic: float | None,
        transition_events: tuple[TransitionEvent, ...],
        terminal_result: TerminalResult | None,
        integrity_sha256: str,
    ) -> None:
        if _token is not _CONSTRUCTION_TOKEN:
            raise TypeError("checkpoints must be created by machine.checkpoint()")
        for name, value in {
            "state": state,
            "cycle_number": cycle_number,
            "started_at": started_at,
            "started_monotonic": started_monotonic,
            "entered_at": entered_at,
            "entered_monotonic": entered_monotonic,
            "deadline_at": deadline_at,
            "deadline_monotonic": deadline_monotonic,
            "earliest_exit_at": earliest_exit_at,
            "earliest_exit_monotonic": earliest_exit_monotonic,
            "transition_events": transition_events,
            "terminal_result": terminal_result,
            "integrity_sha256": integrity_sha256,
            "_provenance": _CONSTRUCTION_TOKEN,
        }.items():
            object.__setattr__(self, name, value)


@dataclass(frozen=True, init=False)
class Phase0StateMachine:
    state: Phase0State
    cycle_number: int
    started_at: datetime
    started_monotonic: float
    entered_at: datetime
    entered_monotonic: float
    deadline_at: datetime | None
    deadline_monotonic: float | None
    earliest_exit_at: datetime | None
    earliest_exit_monotonic: float | None
    transition_events: tuple[TransitionEvent, ...]
    terminal_result: TerminalResult | None
    _provenance: object = field(repr=False, compare=False)

    def __init__(
        self,
        *,
        _token: object | None = None,
        state: Phase0State = Phase0State.INITIAL,
        cycle_number: int = 0,
        started_at: datetime | None = None,
        started_monotonic: float | None = None,
        entered_at: datetime | None = None,
        entered_monotonic: float | None = None,
        deadline_at: datetime | None = None,
        deadline_monotonic: float | None = None,
        earliest_exit_at: datetime | None = None,
        earliest_exit_monotonic: float | None = None,
        transition_events: tuple[TransitionEvent, ...] = (),
        terminal_result: TerminalResult | None = None,
    ) -> None:
        if _token is not _CONSTRUCTION_TOKEN:
            raise TypeError("use Phase0StateMachine.start()")
        if (
            started_at is None
            or started_monotonic is None
            or entered_at is None
            or entered_monotonic is None
        ):
            raise ValueError("state machine timestamps are required")
        for name, value in {
            "state": state,
            "cycle_number": cycle_number,
            "started_at": started_at,
            "started_monotonic": started_monotonic,
            "entered_at": entered_at,
            "entered_monotonic": entered_monotonic,
            "deadline_at": deadline_at,
            "deadline_monotonic": deadline_monotonic,
            "earliest_exit_at": earliest_exit_at,
            "earliest_exit_monotonic": earliest_exit_monotonic,
            "transition_events": transition_events,
            "terminal_result": terminal_result,
            "_provenance": _CONSTRUCTION_TOKEN,
        }.items():
            object.__setattr__(self, name, value)
        self._validate_internal_provenance()

    @classmethod
    def start(
        cls,
        *,
        entered_at: datetime | None = None,
        monotonic_at: float | None = None,
    ) -> "Phase0StateMachine":
        started_at = entered_at or datetime.now(UTC)
        started_monotonic = time.monotonic() if monotonic_at is None else monotonic_at
        _require_utc(started_at)
        _require_monotonic_value(started_monotonic)
        timeout = _STATE_TIMEOUT_SECONDS[Phase0State.INITIAL]
        return cls(
            _token=_CONSTRUCTION_TOKEN,
            state=Phase0State.INITIAL,
            cycle_number=0,
            started_at=started_at,
            started_monotonic=started_monotonic,
            entered_at=started_at,
            entered_monotonic=started_monotonic,
            deadline_at=started_at + timedelta(seconds=timeout),
            deadline_monotonic=started_monotonic + timeout,
            earliest_exit_at=None,
            earliest_exit_monotonic=None,
            transition_events=(),
            terminal_result=None,
        )

    def advance(
        self,
        target: Phase0State,
        *,
        entered_at: datetime | None = None,
        monotonic_at: float | None = None,
    ) -> "Phase0StateMachine":
        """Advance exactly one authorized transition and record its deadline."""
        self._validate_internal_provenance()
        transition_time = entered_at or datetime.now(UTC)
        transition_monotonic = (
            time.monotonic() if monotonic_at is None else monotonic_at
        )
        _require_utc_entry(self.entered_at, transition_time)
        _require_monotonic_entry(
            self.entered_monotonic,
            transition_monotonic,
        )
        if self._deadline_was_exceeded(transition_monotonic):
            return self._deadline_failure(
                transition_time,
                transition_monotonic,
            )

        expected = _LINEAR_TRANSITIONS.get(self.state)
        if self.state is Phase0State.MEASURING_RECOVERY:
            expected = (
                Phase0State.READINESS
                if self.cycle_number < 3
                else Phase0State.TELEMETRY_READINESS
            )
        if target is not expected:
            expected_name = expected.value if expected is not None else "none"
            raise ValueError(
                f"illegal transition {self.state.value} -> {target.value}; "
                f"expected {expected_name}"
            )
        if (
            self.earliest_exit_monotonic is not None
            and transition_monotonic < self.earliest_exit_monotonic
        ):
            raise ValueError(f"{self.state.value} minimum dwell has not elapsed")

        timeout = _STATE_TIMEOUT_SECONDS[target]
        deadline_at = transition_time + timedelta(seconds=timeout)
        deadline_monotonic = transition_monotonic + timeout
        earliest_exit_at = (
            transition_time + timedelta(seconds=_CANONICAL_POLICY.stabilization_seconds)
            if target in _STABILIZING_STATES
            else None
        )
        earliest_exit_monotonic = (
            transition_monotonic + _CANONICAL_POLICY.stabilization_seconds
            if target in _STABILIZING_STATES
            else None
        )
        next_cycle = self.cycle_number + (
            1 if target is Phase0State.MEASURING_RECOVERY else 0
        )
        event = TransitionEvent(
            sequence=len(self.transition_events) + 1,
            from_state=self.state,
            to_state=target,
            entered_at=transition_time,
            entered_monotonic=transition_monotonic,
            deadline_at=deadline_at,
            deadline_monotonic=deadline_monotonic,
            earliest_exit_at=earliest_exit_at,
            earliest_exit_monotonic=earliest_exit_monotonic,
            cycle_number=next_cycle,
        )
        return Phase0StateMachine(
            _token=_CONSTRUCTION_TOKEN,
            state=target,
            cycle_number=next_cycle,
            started_at=self.started_at,
            started_monotonic=self.started_monotonic,
            entered_at=transition_time,
            entered_monotonic=transition_monotonic,
            deadline_at=deadline_at,
            deadline_monotonic=deadline_monotonic,
            earliest_exit_at=earliest_exit_at,
            earliest_exit_monotonic=earliest_exit_monotonic,
            transition_events=self.transition_events + (event,),
            terminal_result=None,
        )

    def finish(
        self,
        outcome: Outcome,
        reason_code: str,
        *,
        entered_at: datetime | None = None,
        monotonic_at: float | None = None,
    ) -> "Phase0StateMachine":
        """Enter a terminal result only from proven transition history."""
        self._validate_internal_provenance()
        if outcome is Outcome.INVALID_INVOCATION:
            raise ValueError("INVALID_INVOCATION is a CLI outcome, not a run state")
        if self.state is Phase0State.TERMINAL:
            raise ValueError("terminal state cannot transition again")
        transition_time = entered_at or datetime.now(UTC)
        transition_monotonic = (
            time.monotonic() if monotonic_at is None else monotonic_at
        )
        _require_utc_entry(self.entered_at, transition_time)
        _require_monotonic_entry(
            self.entered_monotonic,
            transition_monotonic,
        )
        if self._deadline_was_exceeded(transition_monotonic):
            return self._deadline_failure(
                transition_time,
                transition_monotonic,
            )
        if outcome is Outcome.SUCCESS and not self._has_complete_success_history():
            raise ValueError(
                "SUCCESS requires complete three-cycle transition history "
                "through owned SHUTDOWN"
            )

        result = TerminalResult(outcome=outcome, reason_code=reason_code)
        event = TransitionEvent(
            sequence=len(self.transition_events) + 1,
            from_state=self.state,
            to_state=Phase0State.TERMINAL,
            entered_at=transition_time,
            entered_monotonic=transition_monotonic,
            deadline_at=None,
            deadline_monotonic=None,
            earliest_exit_at=None,
            earliest_exit_monotonic=None,
            cycle_number=self.cycle_number,
        )
        return Phase0StateMachine(
            _token=_CONSTRUCTION_TOKEN,
            state=Phase0State.TERMINAL,
            cycle_number=self.cycle_number,
            started_at=self.started_at,
            started_monotonic=self.started_monotonic,
            entered_at=transition_time,
            entered_monotonic=transition_monotonic,
            deadline_at=None,
            deadline_monotonic=None,
            earliest_exit_at=None,
            earliest_exit_monotonic=None,
            transition_events=self.transition_events + (event,),
            terminal_result=result,
        )

    def _deadline_was_exceeded(self, transition_monotonic: float) -> bool:
        return (
            self.deadline_monotonic is not None
            and transition_monotonic > self.deadline_monotonic
        )

    def _deadline_failure(
        self,
        transition_time: datetime,
        transition_monotonic: float,
    ) -> "Phase0StateMachine":
        outcome, reason_code = _DEADLINE_FAILURES[self.state]
        return self._terminal_failure(
            transition_time,
            transition_monotonic,
            outcome=outcome,
            reason_code=reason_code,
            deadline_exceeded=True,
        )

    def _terminal_failure(
        self,
        transition_time: datetime,
        transition_monotonic: float,
        *,
        outcome: Outcome,
        reason_code: str,
        deadline_exceeded: bool,
    ) -> "Phase0StateMachine":
        result = TerminalResult(
            outcome=outcome,
            reason_code=reason_code,
        )
        event = TransitionEvent(
            sequence=len(self.transition_events) + 1,
            from_state=self.state,
            to_state=Phase0State.TERMINAL,
            entered_at=transition_time,
            entered_monotonic=transition_monotonic,
            deadline_at=None,
            deadline_monotonic=None,
            earliest_exit_at=None,
            earliest_exit_monotonic=None,
            cycle_number=self.cycle_number,
            reason_code=reason_code,
            deadline_exceeded=deadline_exceeded,
        )
        return Phase0StateMachine(
            _token=_CONSTRUCTION_TOKEN,
            state=Phase0State.TERMINAL,
            cycle_number=self.cycle_number,
            started_at=self.started_at,
            started_monotonic=self.started_monotonic,
            entered_at=transition_time,
            entered_monotonic=transition_monotonic,
            deadline_at=None,
            deadline_monotonic=None,
            earliest_exit_at=None,
            earliest_exit_monotonic=None,
            transition_events=self.transition_events + (event,),
            terminal_result=result,
        )

    def checkpoint(self) -> StateCheckpoint:
        """Create an authenticated in-process checkpoint."""
        self._validate_internal_provenance()
        integrity = _checkpoint_integrity(
            state=self.state,
            cycle_number=self.cycle_number,
            started_at=self.started_at,
            started_monotonic=self.started_monotonic,
            entered_at=self.entered_at,
            entered_monotonic=self.entered_monotonic,
            deadline_at=self.deadline_at,
            deadline_monotonic=self.deadline_monotonic,
            earliest_exit_at=self.earliest_exit_at,
            earliest_exit_monotonic=self.earliest_exit_monotonic,
            transition_events=self.transition_events,
            terminal_result=self.terminal_result,
        )
        return StateCheckpoint(
            _token=_CONSTRUCTION_TOKEN,
            state=self.state,
            cycle_number=self.cycle_number,
            started_at=self.started_at,
            started_monotonic=self.started_monotonic,
            entered_at=self.entered_at,
            entered_monotonic=self.entered_monotonic,
            deadline_at=self.deadline_at,
            deadline_monotonic=self.deadline_monotonic,
            earliest_exit_at=self.earliest_exit_at,
            earliest_exit_monotonic=self.earliest_exit_monotonic,
            transition_events=self.transition_events,
            terminal_result=self.terminal_result,
            integrity_sha256=integrity,
        )

    @classmethod
    def restore_checkpoint(cls, checkpoint: object) -> "Phase0StateMachine":
        """Restore only a checkpoint issued intact by this process."""
        if (
            not isinstance(checkpoint, StateCheckpoint)
            or checkpoint._provenance is not _CONSTRUCTION_TOKEN
        ):
            raise ValueError("checkpoint provenance is untrusted")
        expected = _checkpoint_integrity(
            state=checkpoint.state,
            cycle_number=checkpoint.cycle_number,
            started_at=checkpoint.started_at,
            started_monotonic=checkpoint.started_monotonic,
            entered_at=checkpoint.entered_at,
            entered_monotonic=checkpoint.entered_monotonic,
            deadline_at=checkpoint.deadline_at,
            deadline_monotonic=checkpoint.deadline_monotonic,
            earliest_exit_at=checkpoint.earliest_exit_at,
            earliest_exit_monotonic=checkpoint.earliest_exit_monotonic,
            transition_events=checkpoint.transition_events,
            terminal_result=checkpoint.terminal_result,
        )
        if not hmac.compare_digest(expected, checkpoint.integrity_sha256):
            raise ValueError("checkpoint integrity verification failed")
        return cls(
            _token=_CONSTRUCTION_TOKEN,
            state=checkpoint.state,
            cycle_number=checkpoint.cycle_number,
            started_at=checkpoint.started_at,
            started_monotonic=checkpoint.started_monotonic,
            entered_at=checkpoint.entered_at,
            entered_monotonic=checkpoint.entered_monotonic,
            deadline_at=checkpoint.deadline_at,
            deadline_monotonic=checkpoint.deadline_monotonic,
            earliest_exit_at=checkpoint.earliest_exit_at,
            earliest_exit_monotonic=checkpoint.earliest_exit_monotonic,
            transition_events=checkpoint.transition_events,
            terminal_result=checkpoint.terminal_result,
        )

    def _has_complete_success_history(self) -> bool:
        targets = [event.to_state for event in self.transition_events]
        expected = [Phase0State.PREFLIGHT, Phase0State.STARTUP]
        cycle = [
            Phase0State.READINESS,
            Phase0State.STABILIZING_BASELINE,
            Phase0State.MEASURING_BASELINE,
            Phase0State.INJECTING,
            Phase0State.STABILIZING_FAULT,
            Phase0State.MEASURING_FAULT,
            Phase0State.RESETTING,
            Phase0State.STABILIZING_RECOVERY,
            Phase0State.MEASURING_RECOVERY,
        ]
        for _ in range(3):
            expected.extend(cycle)
        expected.extend(
            [
                Phase0State.TELEMETRY_READINESS,
                Phase0State.EVIDENCE_FREEZE,
                Phase0State.SHUTDOWN,
            ]
        )
        return (
            self.state is Phase0State.SHUTDOWN
            and self.cycle_number == 3
            and targets == expected
        )

    def _validate_internal_provenance(self) -> None:
        if self._provenance is not _CONSTRUCTION_TOKEN:
            raise ValueError("state machine provenance is untrusted")
        _require_utc(self.started_at)
        _require_utc(self.entered_at)
        _require_monotonic_value(self.started_monotonic)
        _require_monotonic_value(self.entered_monotonic)
        if self.deadline_at is not None:
            _require_utc(self.deadline_at)
        if self.earliest_exit_at is not None:
            _require_utc(self.earliest_exit_at)
        if self.deadline_monotonic is not None:
            _require_monotonic_value(self.deadline_monotonic)
        if self.earliest_exit_monotonic is not None:
            _require_monotonic_value(self.earliest_exit_monotonic)
        if self.cycle_number < 0 or self.cycle_number > 3:
            raise ValueError("cycle number is outside the canonical range")
        if self.transition_events:
            last = self.transition_events[-1]
            if (
                last.to_state is not self.state
                or last.cycle_number != self.cycle_number
                or last.entered_at != self.entered_at
                or last.entered_monotonic != self.entered_monotonic
                or last.deadline_at != self.deadline_at
                or last.deadline_monotonic != self.deadline_monotonic
                or last.earliest_exit_at != self.earliest_exit_at
                or last.earliest_exit_monotonic != self.earliest_exit_monotonic
            ):
                raise ValueError("state machine transition provenance is inconsistent")
            previous = Phase0State.INITIAL
            for sequence, event in enumerate(self.transition_events, start=1):
                if event.sequence != sequence or event.from_state is not previous:
                    raise ValueError(
                        "state machine transition history is discontinuous"
                    )
                previous = event.to_state


def _require_utc(value: datetime) -> None:
    if value.utcoffset() is None or value.utcoffset() != timedelta(0):
        raise ValueError("state timestamps must be UTC")


def _require_utc_entry(previous: datetime, current: datetime) -> None:
    _require_utc(current)
    if current <= previous:
        raise ValueError("state entered_at must advance monotonically")


def _require_monotonic_value(value: float) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("monotonic timestamp must be numeric")
    if not math.isfinite(value) or value < 0:
        raise ValueError("monotonic timestamp must be finite and non-negative")


def _require_monotonic_entry(previous: float, current: float) -> None:
    _require_monotonic_value(current)
    if current <= previous:
        raise ValueError("monotonic state time must advance")


def _checkpoint_integrity(
    *,
    state: Phase0State,
    cycle_number: int,
    started_at: datetime,
    started_monotonic: float,
    entered_at: datetime,
    entered_monotonic: float,
    deadline_at: datetime | None,
    deadline_monotonic: float | None,
    earliest_exit_at: datetime | None,
    earliest_exit_monotonic: float | None,
    transition_events: tuple[TransitionEvent, ...],
    terminal_result: TerminalResult | None,
) -> str:
    payload: dict[str, Any] = {
        "state": state.value,
        "cycle_number": cycle_number,
        "started_at": started_at.isoformat(),
        "started_monotonic": started_monotonic,
        "entered_at": entered_at.isoformat(),
        "entered_monotonic": entered_monotonic,
        "deadline_at": deadline_at.isoformat() if deadline_at else None,
        "deadline_monotonic": deadline_monotonic,
        "earliest_exit_at": (
            earliest_exit_at.isoformat() if earliest_exit_at else None
        ),
        "earliest_exit_monotonic": earliest_exit_monotonic,
        "transition_events": [
            {
                "sequence": event.sequence,
                "from_state": event.from_state.value,
                "to_state": event.to_state.value,
                "entered_at": event.entered_at.isoformat(),
                "entered_monotonic": event.entered_monotonic,
                "deadline_at": (
                    event.deadline_at.isoformat() if event.deadline_at else None
                ),
                "deadline_monotonic": event.deadline_monotonic,
                "earliest_exit_at": (
                    event.earliest_exit_at.isoformat()
                    if event.earliest_exit_at
                    else None
                ),
                "earliest_exit_monotonic": event.earliest_exit_monotonic,
                "cycle_number": event.cycle_number,
                "reason_code": event.reason_code,
                "deadline_exceeded": event.deadline_exceeded,
            }
            for event in transition_events
        ],
        "terminal_result": (
            terminal_result.model_dump(mode="json")
            if terminal_result is not None
            else None
        ),
    }
    serialized = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hmac.new(_CHECKPOINT_KEY, serialized, hashlib.sha256).hexdigest()
