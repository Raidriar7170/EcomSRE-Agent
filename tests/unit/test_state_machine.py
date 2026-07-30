from datetime import UTC, datetime, timedelta

import pytest

from ecomsre.phase0.models import Outcome
from ecomsre.phase0.state_machine import Phase0State, Phase0StateMachine


START = datetime(2026, 7, 30, 4, 0, tzinfo=UTC)
MONOTONIC_START = 1_000.0

CYCLE_STATES = (
    Phase0State.READINESS,
    Phase0State.STABILIZING_BASELINE,
    Phase0State.MEASURING_BASELINE,
    Phase0State.INJECTING,
    Phase0State.STABILIZING_FAULT,
    Phase0State.MEASURING_FAULT,
    Phase0State.RESETTING,
    Phase0State.STABILIZING_RECOVERY,
    Phase0State.MEASURING_RECOVERY,
)


def _advance(
    machine: Phase0StateMachine,
    target: Phase0State,
) -> Phase0StateMachine:
    stabilization_exits = {
        Phase0State.STABILIZING_BASELINE: Phase0State.MEASURING_BASELINE,
        Phase0State.STABILIZING_FAULT: Phase0State.MEASURING_FAULT,
        Phase0State.STABILIZING_RECOVERY: Phase0State.MEASURING_RECOVERY,
    }
    seconds = 30.001 if stabilization_exits.get(machine.state) is target else 1
    entered_at = machine.entered_at + timedelta(seconds=seconds)
    return machine.advance(
        target,
        entered_at=entered_at,
        monotonic_at=machine.entered_monotonic + seconds,
    )


def _through_cycles(cycle_count: int = 3) -> Phase0StateMachine:
    machine = Phase0StateMachine.start(
        entered_at=START,
        monotonic_at=MONOTONIC_START,
    )
    machine = _advance(machine, Phase0State.PREFLIGHT)
    machine = _advance(machine, Phase0State.STARTUP)
    for _cycle_number in range(1, cycle_count + 1):
        for state in CYCLE_STATES:
            machine = _advance(machine, state)
    return machine


def _through_shutdown() -> Phase0StateMachine:
    machine = _through_cycles(3)
    machine = _advance(machine, Phase0State.TELEMETRY_READINESS)
    machine = _advance(machine, Phase0State.EVIDENCE_FREEZE)
    return _advance(machine, Phase0State.SHUTDOWN)


def test_canonical_three_cycle_state_sequence_requires_every_step() -> None:
    machine = _through_shutdown()
    machine = machine.finish(
        Outcome.SUCCESS,
        "ALL_GATES_PASSED",
        entered_at=machine.entered_at + timedelta(seconds=1),
        monotonic_at=machine.entered_monotonic + 1,
    )

    assert machine.state is Phase0State.TERMINAL
    assert machine.cycle_number == 3
    assert machine.terminal_result is not None
    assert machine.terminal_result.exit_code == 0


def test_every_transition_records_entered_at_and_deadline_metadata() -> None:
    machine = Phase0StateMachine.start(
        entered_at=START,
        monotonic_at=MONOTONIC_START,
    )
    machine = _advance(machine, Phase0State.PREFLIGHT)
    machine = _advance(machine, Phase0State.STARTUP)
    machine = _advance(machine, Phase0State.READINESS)
    machine = _advance(machine, Phase0State.STABILIZING_BASELINE)

    assert machine.started_at == START
    assert len(machine.transition_events) == 4
    assert all(event.entered_at.tzinfo is UTC for event in machine.transition_events)
    assert all(
        event.deadline_at is not None and event.deadline_at > event.entered_at
        for event in machine.transition_events
    )
    stabilization = machine.transition_events[-1]
    assert stabilization.deadline_at - stabilization.entered_at == timedelta(
        seconds=180
    )
    assert stabilization.earliest_exit_at - stabilization.entered_at == timedelta(
        seconds=30
    )
    assert machine.earliest_exit_at == stabilization.earliest_exit_at


@pytest.mark.parametrize(
    ("stabilizing", "measuring"),
    [
        (
            Phase0State.STABILIZING_BASELINE,
            Phase0State.MEASURING_BASELINE,
        ),
        (Phase0State.STABILIZING_FAULT, Phase0State.MEASURING_FAULT),
        (
            Phase0State.STABILIZING_RECOVERY,
            Phase0State.MEASURING_RECOVERY,
        ),
    ],
)
def test_stabilization_minimum_dwell_rejects_early_without_terminating(
    stabilizing: Phase0State,
    measuring: Phase0State,
) -> None:
    machine = Phase0StateMachine.start(
        entered_at=START,
        monotonic_at=MONOTONIC_START,
    )
    while machine.state is not stabilizing:
        expected = (
            Phase0State.READINESS
            if machine.state is Phase0State.MEASURING_RECOVERY
            else {
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
            }[machine.state]
        )
        machine = _advance(machine, expected)

    with pytest.raises(ValueError, match="minimum dwell"):
        machine.advance(
            measuring,
            entered_at=machine.entered_at + timedelta(seconds=29.999),
            monotonic_at=machine.entered_monotonic + 29.999,
        )

    legal = machine.advance(
        measuring,
        entered_at=machine.entered_at + timedelta(seconds=30.001),
        monotonic_at=machine.entered_monotonic + 30.001,
    )
    assert legal.state is measuring
    assert legal.terminal_result is None


def _first_machine_in_each_state() -> dict[Phase0State, Phase0StateMachine]:
    machine = Phase0StateMachine.start(
        entered_at=START,
        monotonic_at=MONOTONIC_START,
    )
    machines = {machine.state: machine}
    sequence = (
        Phase0State.PREFLIGHT,
        Phase0State.STARTUP,
        *CYCLE_STATES,
        *CYCLE_STATES,
        *CYCLE_STATES,
        Phase0State.TELEMETRY_READINESS,
        Phase0State.EVIDENCE_FREEZE,
        Phase0State.SHUTDOWN,
    )
    for target in sequence:
        machine = _advance(machine, target)
        machines.setdefault(machine.state, machine)
    return machines


@pytest.mark.parametrize(
    ("state", "outcome", "reason_code"),
    [
        (Phase0State.INITIAL, Outcome.BLOCKED_ENVIRONMENT, "PREFLIGHT_BLOCKED"),
        (Phase0State.PREFLIGHT, Outcome.BLOCKED_ENVIRONMENT, "PREFLIGHT_BLOCKED"),
        (
            Phase0State.STARTUP,
            Outcome.BLOCKED_ENVIRONMENT,
            "PREFLIGHT_BLOCKED",
        ),
        (
            Phase0State.READINESS,
            Outcome.BLOCKED_ENVIRONMENT,
            "TELEMETRY_NOT_READY",
        ),
        (
            Phase0State.STABILIZING_BASELINE,
            Outcome.FAILED_ACCEPTANCE,
            "WINDOW_SAMPLE_TIMEOUT",
        ),
        (
            Phase0State.MEASURING_BASELINE,
            Outcome.FAILED_ACCEPTANCE,
            "WINDOW_SAMPLE_TIMEOUT",
        ),
        (
            Phase0State.INJECTING,
            Outcome.FAILED_ACCEPTANCE,
            "INJECT_TIMEOUT",
        ),
        (
            Phase0State.STABILIZING_FAULT,
            Outcome.FAILED_ACCEPTANCE,
            "WINDOW_SAMPLE_TIMEOUT",
        ),
        (
            Phase0State.MEASURING_FAULT,
            Outcome.FAILED_ACCEPTANCE,
            "WINDOW_SAMPLE_TIMEOUT",
        ),
        (
            Phase0State.RESETTING,
            Outcome.FAILED_ACCEPTANCE,
            "RESET_TIMEOUT",
        ),
        (
            Phase0State.STABILIZING_RECOVERY,
            Outcome.FAILED_ACCEPTANCE,
            "WINDOW_SAMPLE_TIMEOUT",
        ),
        (
            Phase0State.MEASURING_RECOVERY,
            Outcome.FAILED_ACCEPTANCE,
            "WINDOW_SAMPLE_TIMEOUT",
        ),
        (
            Phase0State.TELEMETRY_READINESS,
            Outcome.FAILED_ACCEPTANCE,
            "TELEMETRY_NOT_READY",
        ),
        (
            Phase0State.EVIDENCE_FREEZE,
            Outcome.FAILED_ACCEPTANCE,
            "EVIDENCE_INCOMPLETE",
        ),
        (
            Phase0State.SHUTDOWN,
            Outcome.MANUAL_INTERVENTION_REQUIRED,
            "CLEANUP_INCOMPLETE",
        ),
    ],
)
def test_each_state_deadline_has_contract_terminal_mapping(
    state: Phase0State,
    outcome: Outcome,
    reason_code: str,
) -> None:
    machine = _first_machine_in_each_state()[state]
    assert machine.deadline_at is not None

    if state is Phase0State.SHUTDOWN:
        expired = machine.finish(
            Outcome.SUCCESS,
            "ALL_GATES_PASSED",
            entered_at=machine.deadline_at + timedelta(seconds=1),
            monotonic_at=machine.deadline_monotonic + 1,
        )
    else:
        expected = (
            Phase0State.READINESS
            if state is Phase0State.MEASURING_RECOVERY
            else {
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
            }[state]
        )
        expired = machine.advance(
            expected,
            entered_at=machine.deadline_at + timedelta(seconds=1),
            monotonic_at=machine.deadline_monotonic + 1,
        )

    assert expired.state is Phase0State.TERMINAL
    assert expired.terminal_result is not None
    assert expired.terminal_result.outcome is outcome
    assert expired.terminal_result.reason_code == reason_code
    event = expired.transition_events[-1]
    assert event.deadline_exceeded is True
    assert event.reason_code == reason_code
    with pytest.raises(ValueError, match="terminal"):
        expired.finish(
            Outcome.SUCCESS,
            "ALL_GATES_PASSED",
            entered_at=expired.entered_at + timedelta(seconds=1),
            monotonic_at=expired.entered_monotonic + 1,
        )


def test_complete_three_cycle_history_cannot_succeed_one_second_late() -> None:
    machine = _through_shutdown()
    assert machine.deadline_at is not None

    expired = machine.finish(
        Outcome.SUCCESS,
        "ALL_GATES_PASSED",
        entered_at=machine.deadline_at + timedelta(seconds=1),
        monotonic_at=machine.deadline_monotonic + 1,
    )

    assert expired.state is Phase0State.TERMINAL
    assert expired.terminal_result is not None
    assert expired.terminal_result.outcome is Outcome.MANUAL_INTERVENTION_REQUIRED
    assert expired.terminal_result.reason_code == "CLEANUP_INCOMPLETE"
    assert expired.transition_events[-1].deadline_exceeded is True


def test_state_machine_rejects_skipping_an_intermediate_state() -> None:
    machine = _advance(
        Phase0StateMachine.start(entered_at=START),
        Phase0State.PREFLIGHT,
    )

    with pytest.raises(ValueError, match="illegal transition"):
        _advance(machine, Phase0State.READINESS)


def test_state_machine_rejects_telemetry_gate_before_three_cycles() -> None:
    machine = _through_cycles(1)

    with pytest.raises(ValueError, match="illegal transition"):
        _advance(machine, Phase0State.TELEMETRY_READINESS)


def test_success_is_legal_only_after_complete_transition_history() -> None:
    machine = _through_cycles(3)
    machine = _advance(machine, Phase0State.TELEMETRY_READINESS)
    machine = _advance(machine, Phase0State.EVIDENCE_FREEZE)

    with pytest.raises(ValueError, match="SUCCESS"):
        machine.finish(
            Outcome.SUCCESS,
            "ALL_GATES_PASSED",
            entered_at=machine.entered_at + timedelta(seconds=1),
            monotonic_at=machine.entered_monotonic + 1,
        )


def test_direct_forged_state_construction_is_forbidden() -> None:
    with pytest.raises(TypeError, match="start"):
        Phase0StateMachine(
            state=Phase0State.SHUTDOWN,
            cycle_number=3,
            entered_at=START,
            transition_events=(),
        )


def test_forged_or_tampered_checkpoint_is_rejected() -> None:
    machine = _through_cycles(1)

    with pytest.raises(ValueError, match="checkpoint provenance"):
        Phase0StateMachine.restore_checkpoint(
            {
                "state": Phase0State.SHUTDOWN,
                "cycle_number": 3,
                "transition_events": (),
            }
        )

    checkpoint = machine.checkpoint()
    object.__setattr__(checkpoint, "state", Phase0State.SHUTDOWN)
    object.__setattr__(checkpoint, "cycle_number", 3)
    with pytest.raises(ValueError, match="checkpoint integrity"):
        Phase0StateMachine.restore_checkpoint(checkpoint)


def test_valid_checkpoint_restores_exact_history() -> None:
    machine = _through_cycles(1)
    checkpoint = machine.checkpoint()

    restored = Phase0StateMachine.restore_checkpoint(checkpoint)

    assert restored.state is machine.state
    assert restored.cycle_number == machine.cycle_number
    assert restored.transition_events == machine.transition_events


@pytest.mark.parametrize(
    "outcome",
    [
        Outcome.BLOCKED_ENVIRONMENT,
        Outcome.BLOCKED_UPSTREAM,
        Outcome.FAILED_ACCEPTANCE,
        Outcome.UNSAFE,
        Outcome.MANUAL_INTERVENTION_REQUIRED,
    ],
)
def test_non_success_terminal_outcomes_fail_closed_from_active_state(
    outcome: Outcome,
) -> None:
    machine = _advance(
        Phase0StateMachine.start(entered_at=START),
        Phase0State.PREFLIGHT,
    )

    terminal = machine.finish(
        outcome,
        "TEST_STOP",
        entered_at=machine.entered_at + timedelta(seconds=1),
        monotonic_at=machine.entered_monotonic + 1,
    )

    assert terminal.state is Phase0State.TERMINAL
    assert terminal.terminal_result is not None
    assert terminal.terminal_result.outcome is outcome


def test_invalid_invocation_is_not_a_run_state() -> None:
    machine = Phase0StateMachine.start(
        entered_at=START,
        monotonic_at=MONOTONIC_START,
    )

    with pytest.raises(ValueError, match="INVALID_INVOCATION"):
        machine.finish(
            Outcome.INVALID_INVOCATION,
            "BAD_ARGUMENT",
            entered_at=START + timedelta(seconds=1),
            monotonic_at=MONOTONIC_START + 1,
        )


def test_deadline_decisions_use_monotonic_time_not_utc_wall_clock() -> None:
    machine = Phase0StateMachine.start(
        entered_at=START,
        monotonic_at=MONOTONIC_START,
    )

    advanced = machine.advance(
        Phase0State.PREFLIGHT,
        entered_at=START + timedelta(days=1),
        monotonic_at=MONOTONIC_START + 1,
    )

    assert advanced.state is Phase0State.PREFLIGHT
    expired = advanced.advance(
        Phase0State.STARTUP,
        entered_at=advanced.entered_at + timedelta(milliseconds=1),
        monotonic_at=advanced.deadline_monotonic + 0.001,
    )
    assert expired.state is Phase0State.TERMINAL
    assert expired.transition_events[-1].deadline_exceeded is True


def test_checkpoint_integrity_covers_monotonic_provenance() -> None:
    machine = _through_cycles(1)
    checkpoint = machine.checkpoint()
    object.__setattr__(
        checkpoint,
        "entered_monotonic",
        checkpoint.entered_monotonic + 1,
    )

    with pytest.raises(ValueError, match="checkpoint integrity"):
        Phase0StateMachine.restore_checkpoint(checkpoint)
