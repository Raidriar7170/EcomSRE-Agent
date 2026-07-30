from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

import pytest

from ecomsre.phase0.models import Outcome
from ecomsre.scenarios.ad_service_failure import (
    AdServiceFailureController,
    MutationState,
    PendingRecoveryEvidenceError,
    ScenarioAction,
    ScenarioState,
    TransitionGuardCleanupError,
    TransitionExecution,
    TransitionPreparation,
)


RUN_ID = "a" * 32
CORRELATION_ID = "b" * 32


class FixtureClock:
    def __init__(self) -> None:
        self.elapsed = 0.0
        self.started_at = datetime(2026, 7, 30, 8, 0, tzinfo=UTC)

    def now(self) -> datetime:
        return self.started_at + timedelta(seconds=self.elapsed)

    def monotonic(self) -> float:
        return self.elapsed

    def sleep(self, seconds: float) -> None:
        assert seconds >= 0
        self.elapsed += seconds


class FixtureControlAdapter:
    def __init__(self, states: list[ScenarioState]) -> None:
        self.states = list(states)
        self.observations: list[float] = []
        self.writes: list[ScenarioState] = []
        self.recorded = []
        self.emergency = []
        self.pending = []
        self.completed = []
        self.prepared = []
        self.finalized = []

    @contextmanager
    def transition_guard(self, *, timeout_seconds: float):
        assert timeout_seconds > 0
        yield True

    def reconcile_pending_intent(
        self,
        *,
        timeout_seconds: float,
    ) -> ScenarioState | None:
        assert timeout_seconds > 0
        return None

    def record_pending_intent(self, **details) -> str:
        self.pending.append(details)
        return "c" * 32

    def complete_pending_intent(
        self,
        intent_id: str,
        *,
        observed_state: ScenarioState,
    ) -> None:
        self.completed.append((intent_id, observed_state))

    def observe_state(self, *, timeout_seconds: float) -> ScenarioState:
        assert timeout_seconds > 0
        self.observations.append(timeout_seconds)
        if len(self.states) > 1:
            return self.states.pop(0)
        return self.states[0]

    def apply_state(self, target: ScenarioState) -> MutationState:
        self.writes.append(target)
        return MutationState.APPLIED

    def prepare_transition(self, execution) -> TransitionPreparation:
        self.prepared.append(execution)
        return TransitionPreparation(
            schema_version="phase0.transition-preparation.v1",
            preparation_id="d" * 32,
            transition_sequence=len(self.prepared),
        )

    def finalize_transition(
        self,
        execution,
        *,
        preparation,
        record_status,
    ) -> None:
        self.finalized.append((execution, preparation, record_status))
        self.recorded.append(execution)

    def record_emergency_diagnostic(self, execution, *, failure_stage: str) -> None:
        self.emergency.append((execution, failure_stage))


class FixtureObserverSink:
    def __init__(self) -> None:
        self.events = []

    def write_event(self, event) -> None:
        self.events.append(event)


def _controller(
    adapter: FixtureControlAdapter,
    *,
    clock: FixtureClock | None = None,
    timeout_seconds: float = 2,
) -> tuple[AdServiceFailureController, FixtureObserverSink]:
    sink = FixtureObserverSink()
    return (
        AdServiceFailureController(
            adapter=adapter,
            observer_sink=sink,
            run_id=RUN_ID,
            correlation_id=CORRELATION_ID,
            clock=clock or FixtureClock(),
            timeout_seconds=timeout_seconds,
            poll_interval_seconds=1,
        ),
        sink,
    )


@pytest.mark.parametrize(
    ("action", "initial", "target"),
    [
        (ScenarioAction.INJECT, ScenarioState.BASELINE, ScenarioState.INJECTED),
        (ScenarioAction.RESET, ScenarioState.INJECTED, ScenarioState.BASELINE),
    ],
)
def test_inject_and_reset_require_positive_post_write_readback(
    action: ScenarioAction,
    initial: ScenarioState,
    target: ScenarioState,
) -> None:
    adapter = FixtureControlAdapter([initial, target])
    controller, sink = _controller(adapter)

    execution = controller.transition(action)

    assert execution.terminal_result.outcome is Outcome.SUCCESS
    assert execution.terminal_result.reason_code == "CONTROL_TRANSITION_CONFIRMED"
    assert execution.before_state is initial
    assert execution.target_state is target
    assert execution.mutation_state is MutationState.APPLIED
    assert adapter.writes == [target]
    assert adapter.recorded == [execution]
    assert sink.events == [execution.observer_event]
    assert execution.observer_event.transition_succeeded is True
    assert execution.observer_event.error_category == "NONE"


@pytest.mark.parametrize(
    ("action", "current"),
    [
        (ScenarioAction.INJECT, ScenarioState.INJECTED),
        (ScenarioAction.RESET, ScenarioState.BASELINE),
    ],
)
def test_repeated_transition_is_idempotent_without_a_second_write(
    action: ScenarioAction,
    current: ScenarioState,
) -> None:
    adapter = FixtureControlAdapter([current])
    controller, _sink = _controller(adapter)

    execution = controller.transition(action)

    assert execution.terminal_result.outcome is Outcome.SUCCESS
    assert execution.terminal_result.reason_code == "CONTROL_STATE_CONFIRMED"
    assert execution.mutation_state is MutationState.NOT_APPLIED
    assert adapter.writes == []
    assert len(adapter.observations) == 1


@pytest.mark.parametrize("action", list(ScenarioAction))
def test_unknown_pre_state_fails_closed_without_a_write(
    action: ScenarioAction,
) -> None:
    adapter = FixtureControlAdapter([ScenarioState.UNKNOWN])
    controller, sink = _controller(adapter)

    execution = controller.transition(action)

    assert execution.terminal_result.outcome is Outcome.MANUAL_INTERVENTION_REQUIRED
    assert execution.terminal_result.reason_code == "MUTATION_STATE_UNKNOWN"
    assert execution.mutation_state is MutationState.NOT_APPLIED
    assert adapter.writes == []
    assert execution.observer_event.transition_succeeded is False
    assert execution.observer_event.error_category == "STATE_UNKNOWN"
    assert sink.events == [execution.observer_event]


@pytest.mark.parametrize(
    ("action", "initial"),
    [
        (ScenarioAction.INJECT, ScenarioState.BASELINE),
        (ScenarioAction.RESET, ScenarioState.INJECTED),
    ],
)
def test_post_write_unknown_readback_is_typed_manual_and_never_retried(
    action: ScenarioAction,
    initial: ScenarioState,
) -> None:
    adapter = FixtureControlAdapter([initial, ScenarioState.UNKNOWN])
    clock = FixtureClock()
    controller, _sink = _controller(
        adapter,
        clock=clock,
        timeout_seconds=2,
    )

    execution = controller.transition(action)

    assert execution.terminal_result.outcome is Outcome.MANUAL_INTERVENTION_REQUIRED
    assert execution.terminal_result.reason_code == "MUTATION_STATE_UNKNOWN"
    assert execution.mutation_state is MutationState.APPLIED
    assert execution.observer_event.transition_succeeded is False
    assert execution.observer_event.error_category == "STATE_UNKNOWN"
    assert clock.elapsed == pytest.approx(0)
    assert len(adapter.writes) == 1
    assert len(adapter.observations) == 2


def test_post_mutation_unknown_readback_is_immediate_manual_not_later_success() -> None:
    adapter = FixtureControlAdapter(
        [
            ScenarioState.BASELINE,
            ScenarioState.UNKNOWN,
            ScenarioState.INJECTED,
        ]
    )
    controller, _sink = _controller(adapter)

    execution = controller.inject()

    assert execution.terminal_result.outcome is Outcome.MANUAL_INTERVENTION_REQUIRED
    assert execution.terminal_result.reason_code == "MUTATION_STATE_UNKNOWN"
    assert execution.terminal_result.exit_code == 41
    assert execution.mutation_state is MutationState.APPLIED
    assert execution.observer_event.error_category == "STATE_UNKNOWN"
    assert len(adapter.observations) == 2


def test_apply_returning_unknown_is_immediate_manual_before_readback_or_completion() -> (
    None
):
    class UnknownMutationAdapter(FixtureControlAdapter):
        def apply_state(self, target: ScenarioState) -> MutationState:
            self.writes.append(target)
            return MutationState.UNKNOWN

    adapter = UnknownMutationAdapter([ScenarioState.BASELINE, ScenarioState.INJECTED])
    controller, sink = _controller(adapter)

    execution = controller.inject()

    assert execution.terminal_result.outcome is Outcome.MANUAL_INTERVENTION_REQUIRED
    assert execution.terminal_result.reason_code == "MUTATION_STATE_UNKNOWN"
    assert execution.terminal_result.exit_code == 41
    assert execution.mutation_state is MutationState.UNKNOWN
    assert len(adapter.observations) == 1
    assert len(adapter.pending) == 1
    assert adapter.completed == []
    assert adapter.emergency == [(execution, "MUTATION_STATE_UNKNOWN")]
    assert sink.events == [execution.observer_event]


def test_unknown_pending_recovery_evidence_failure_is_valid_manual_41() -> None:
    class UnknownPendingRecoveryAdapter(FixtureControlAdapter):
        def reconcile_pending_intent(
            self,
            *,
            timeout_seconds: float,
        ) -> ScenarioState | None:
            assert timeout_seconds > 0
            raise PendingRecoveryEvidenceError(
                "fixture unresolved pending recovery",
                observed_state=ScenarioState.UNKNOWN,
            )

    adapter = UnknownPendingRecoveryAdapter([ScenarioState.UNKNOWN])
    controller, sink = _controller(adapter)

    execution = controller.inject()

    assert execution.before_state is ScenarioState.UNKNOWN
    assert execution.mutation_state is MutationState.UNKNOWN
    assert execution.terminal_result.outcome is Outcome.MANUAL_INTERVENTION_REQUIRED
    assert execution.terminal_result.reason_code == "EVIDENCE_PERSISTENCE_FAILED"
    assert execution.terminal_result.exit_code == 41
    assert execution.pending_recovery_unresolved is True
    assert sink.events == [execution.observer_event]


@pytest.mark.parametrize(
    "unknown_update",
    [
        {"before_state": ScenarioState.UNKNOWN},
        {"mutation_state": MutationState.UNKNOWN},
    ],
)
def test_transition_execution_rejects_success_with_unknown_state(
    unknown_update: dict[str, object],
) -> None:
    adapter = FixtureControlAdapter([ScenarioState.INJECTED])
    controller, _sink = _controller(adapter)
    successful = controller.inject()

    with pytest.raises(ValueError, match="unknown"):
        TransitionExecution(
            **{
                **successful.model_dump(mode="python"),
                **unknown_update,
            }
        )


def test_observer_event_has_only_opaque_correlation_and_transition_metadata() -> None:
    adapter = FixtureControlAdapter([ScenarioState.BASELINE, ScenarioState.INJECTED])
    controller, _sink = _controller(adapter)

    event = controller.inject().observer_event
    payload = event.model_dump(mode="json")

    assert payload == {
        "schema_version": "phase0.observer-control-event.v1",
        "control_event_id": event.control_event_id,
        "correlation_id": CORRELATION_ID,
        "started_at": "2026-07-30T08:00:00Z",
        "ended_at": "2026-07-30T08:00:00Z",
        "monotonic_duration_seconds": 0.0,
        "transition_succeeded": True,
        "error_category": "NONE",
    }
    assert len(event.control_event_id) == 32


def test_target_readback_returning_after_deadline_is_still_inject_timeout() -> None:
    clock = FixtureClock()

    class DelayedTargetAdapter(FixtureControlAdapter):
        def observe_state(self, *, timeout_seconds: float) -> ScenarioState:
            state = super().observe_state(timeout_seconds=timeout_seconds)
            if len(self.observations) == 2:
                clock.sleep(3)
            return state

    adapter = DelayedTargetAdapter([ScenarioState.BASELINE, ScenarioState.INJECTED])
    controller, _sink = _controller(
        adapter,
        clock=clock,
        timeout_seconds=2,
    )

    execution = controller.inject()

    assert execution.terminal_result.outcome is Outcome.FAILED_ACCEPTANCE
    assert execution.terminal_result.reason_code == "INJECT_TIMEOUT"
    assert execution.observer_event.transition_succeeded is False
    assert clock.elapsed == 3


def test_evaluator_evidence_failure_after_mutation_returns_typed_manual() -> None:
    class FailingEvaluatorAdapter(FixtureControlAdapter):
        def finalize_transition(
            self,
            execution,
            *,
            preparation,
            record_status,
        ) -> None:
            raise OSError("fixture evaluator failure")

    adapter = FailingEvaluatorAdapter([ScenarioState.BASELINE, ScenarioState.INJECTED])
    controller, sink = _controller(adapter)

    execution = controller.inject()

    assert execution.mutation_state is MutationState.APPLIED
    assert execution.terminal_result.outcome is Outcome.MANUAL_INTERVENTION_REQUIRED
    assert execution.terminal_result.reason_code == "EVIDENCE_PERSISTENCE_FAILED"
    assert execution.terminal_result.exit_code == 41
    assert execution.observer_event.transition_succeeded is False
    assert execution.observer_event.error_category == "SAFETY"
    assert adapter.emergency == [(execution, "EVALUATOR_TRANSITION")]
    assert sink.events == [execution.observer_event]


def test_observer_evidence_failure_after_mutation_returns_typed_manual() -> None:
    adapter = FixtureControlAdapter([ScenarioState.BASELINE, ScenarioState.INJECTED])

    class FailingObserverSink(FixtureObserverSink):
        def write_event(self, event) -> None:
            raise OSError("fixture observer failure")

    sink = FailingObserverSink()
    controller = AdServiceFailureController(
        adapter=adapter,
        observer_sink=sink,
        run_id=RUN_ID,
        correlation_id=CORRELATION_ID,
        clock=FixtureClock(),
        timeout_seconds=2,
        poll_interval_seconds=1,
    )

    execution = controller.inject()

    assert execution.mutation_state is MutationState.APPLIED
    assert execution.terminal_result.outcome is Outcome.MANUAL_INTERVENTION_REQUIRED
    assert execution.terminal_result.reason_code == "EVIDENCE_PERSISTENCE_FAILED"
    assert execution.terminal_result.exit_code == 41
    assert execution.observer_event.transition_succeeded is False
    assert execution.observer_event.error_category == "SAFETY"
    assert len(adapter.recorded) == 1
    assert adapter.recorded[0].terminal_result.outcome is Outcome.SUCCESS
    assert adapter.emergency == [(execution, "OBSERVER_TRANSITION")]


def test_transition_guard_prepares_before_cleanup_and_finalizes_after_cleanup() -> None:
    class GuardTrackingAdapter(FixtureControlAdapter):
        def __init__(self) -> None:
            super().__init__([ScenarioState.BASELINE, ScenarioState.INJECTED])
            self.guard_held = False

        @contextmanager
        def transition_guard(self, *, timeout_seconds: float):
            assert timeout_seconds > 0
            self.guard_held = True
            try:
                yield True
            finally:
                self.guard_held = False

        def prepare_transition(self, execution) -> TransitionPreparation:
            assert self.guard_held is True
            return super().prepare_transition(execution)

        def finalize_transition(
            self,
            execution,
            *,
            preparation,
            record_status,
        ) -> None:
            assert self.guard_held is False
            super().finalize_transition(
                execution,
                preparation=preparation,
                record_status=record_status,
            )

    adapter = GuardTrackingAdapter()

    class GuardTrackingSink(FixtureObserverSink):
        def write_event(self, event) -> None:
            assert adapter.guard_held is False
            super().write_event(event)

    sink = GuardTrackingSink()
    controller = AdServiceFailureController(
        adapter=adapter,
        observer_sink=sink,
        run_id=RUN_ID,
        correlation_id=CORRELATION_ID,
        clock=FixtureClock(),
        timeout_seconds=2,
        poll_interval_seconds=1,
    )

    execution = controller.inject()

    assert execution.terminal_result.outcome is Outcome.SUCCESS
    assert adapter.guard_held is False


def test_guard_cleanup_failure_after_mutation_is_typed_manual_41() -> None:
    class CleanupFailingAdapter(FixtureControlAdapter):
        @contextmanager
        def transition_guard(self, *, timeout_seconds: float):
            assert timeout_seconds > 0
            yield True
            raise TransitionGuardCleanupError("fixture cleanup failure")

    adapter = CleanupFailingAdapter([ScenarioState.BASELINE, ScenarioState.INJECTED])
    controller, _sink = _controller(adapter)

    execution = controller.inject()

    assert execution.mutation_state is MutationState.APPLIED
    assert execution.terminal_result.outcome is Outcome.MANUAL_INTERVENTION_REQUIRED
    assert execution.terminal_result.reason_code == "EVIDENCE_PERSISTENCE_FAILED"
    assert execution.terminal_result.exit_code == 41
    assert adapter.emergency[-1] == (execution, "TRANSITION_GUARD_CLEANUP")
