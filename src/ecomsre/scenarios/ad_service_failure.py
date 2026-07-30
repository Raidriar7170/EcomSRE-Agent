"""Logical, ground-truth-free control state for the Phase 0 Ad fault."""

from __future__ import annotations

import secrets
import time
from datetime import UTC, datetime
from enum import Enum
from typing import ContextManager, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ecomsre.phase0.models import Outcome, TerminalResult


class ScenarioAction(str, Enum):
    INJECT = "INJECT"
    RESET = "RESET"


class ScenarioState(str, Enum):
    BASELINE = "BASELINE"
    INJECTED = "INJECTED"
    UNKNOWN = "UNKNOWN"


class MutationState(str, Enum):
    NOT_APPLIED = "NOT_APPLIED"
    APPLIED = "APPLIED"
    UNKNOWN = "UNKNOWN"


class EvidencePersistenceError(RuntimeError):
    """A required append-only control evidence write failed."""


class PendingRecoveryEvidenceError(EvidencePersistenceError):
    """Pending-intent recovery observed state but could not persist its evidence."""

    def __init__(self, message: str, *, observed_state: ScenarioState) -> None:
        super().__init__(message)
        self.observed_state = observed_state


class ControlMutationUncertain(RuntimeError):
    """A physical write attempt cannot be classified as safely applied or absent."""


class TransitionGuardCleanupError(RuntimeError):
    """A transition guard could not prove that every cleanup step succeeded."""


class TransitionPreparation(BaseModel):
    """Opaque link from locked evaluator preparation to terminal finalization."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["phase0.transition-preparation.v1"]
    preparation_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    transition_sequence: int = Field(ge=1)


class ObserverControlEvent(BaseModel):
    """The deliberately narrow change record available to observer tools."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    schema_version: Literal["phase0.observer-control-event.v1"]
    control_event_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    correlation_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    started_at: datetime
    ended_at: datetime
    monotonic_duration_seconds: float = Field(ge=0)
    transition_succeeded: bool
    error_category: Literal["NONE", "STATE_UNKNOWN", "TIMEOUT", "SAFETY"]

    @model_validator(mode="after")
    def require_safe_time_window(self) -> "ObserverControlEvent":
        for value in (self.started_at, self.ended_at):
            if value.utcoffset() is None or value.utcoffset().total_seconds() != 0:
                raise ValueError("observer control timestamps must be UTC")
        if self.ended_at < self.started_at:
            raise ValueError("observer control event end precedes start")
        if self.transition_succeeded != (self.error_category == "NONE"):
            raise ValueError("observer control event success is inconsistent")
        return self


class TransitionExecution(BaseModel):
    """Evaluator-side execution result; only observer_event may cross the boundary."""

    model_config = ConfigDict(frozen=True, extra="forbid", arbitrary_types_allowed=True)

    action: ScenarioAction
    before_state: ScenarioState
    target_state: ScenarioState
    mutation_state: MutationState
    pending_recovery_unresolved: bool = False
    terminal_result: TerminalResult
    observer_event: ObserverControlEvent

    @model_validator(mode="after")
    def require_consistent_result(self) -> "TransitionExecution":
        succeeded = self.terminal_result.outcome is Outcome.SUCCESS
        if self.observer_event.transition_succeeded != succeeded:
            raise ValueError("observer acknowledgement conflicts with terminal result")
        if succeeded and (
            self.before_state is ScenarioState.UNKNOWN
            or self.mutation_state is MutationState.UNKNOWN
        ):
            raise ValueError("successful transition cannot contain unknown state")
        unresolved_unknown = (
            self.before_state is ScenarioState.UNKNOWN
            and self.mutation_state is MutationState.UNKNOWN
        )
        if unresolved_unknown:
            if not (
                self.pending_recovery_unresolved
                and self.terminal_result.outcome is Outcome.MANUAL_INTERVENTION_REQUIRED
                and self.terminal_result.exit_code == 41
                and self.terminal_result.reason_code == "EVIDENCE_PERSISTENCE_FAILED"
            ):
                raise ValueError(
                    "unknown pending recovery lacks typed unresolved evidence"
                )
        elif (
            self.before_state is ScenarioState.UNKNOWN
            and self.mutation_state is not MutationState.NOT_APPLIED
        ):
            raise ValueError("unknown pre-state cannot authorize a mutation")
        if self.pending_recovery_unresolved and not unresolved_unknown:
            raise ValueError(
                "unresolved pending recovery marker requires unknown states"
            )
        return self


class ScenarioControlAdapter(Protocol):
    def transition_guard(
        self,
        *,
        timeout_seconds: float,
    ) -> ContextManager[bool]: ...

    def reconcile_pending_intent(
        self,
        *,
        timeout_seconds: float,
    ) -> ScenarioState | None: ...

    def record_pending_intent(
        self,
        *,
        action: ScenarioAction,
        before_state: ScenarioState,
        target_state: ScenarioState,
        started_at: datetime,
        started_monotonic: float,
        deadline_monotonic: float,
    ) -> str: ...

    def complete_pending_intent(
        self,
        intent_id: str,
        *,
        observed_state: ScenarioState,
    ) -> None: ...

    def observe_state(self, *, timeout_seconds: float) -> ScenarioState: ...

    def apply_state(self, target: ScenarioState) -> MutationState: ...

    def prepare_transition(
        self,
        execution: TransitionExecution,
    ) -> TransitionPreparation: ...

    def finalize_transition(
        self,
        execution: TransitionExecution,
        *,
        preparation: TransitionPreparation | None,
        record_status: Literal[
            "FINALIZED",
            "CLEANUP_FAILED",
            "INTERRUPTED",
            "PRELOCK_TIMEOUT",
        ],
    ) -> None: ...

    def record_emergency_diagnostic(
        self,
        execution: TransitionExecution,
        *,
        failure_stage: str,
    ) -> None: ...


class ObserverEventSink(Protocol):
    def write_event(self, event: ObserverControlEvent) -> None: ...


class ObserverArtifactWriter(Protocol):
    def append_event(
        self,
        relative_path: str,
        value: BaseModel | dict[str, object],
    ) -> object: ...


class ControlClock(Protocol):
    def now(self) -> datetime: ...

    def monotonic(self) -> float: ...

    def sleep(self, seconds: float) -> None: ...


class SystemControlClock:
    def now(self) -> datetime:
        return datetime.now(UTC)

    def monotonic(self) -> float:
        return time.monotonic()

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)


class ObserverControlEventSink:
    """One-way writer: it has no read method or evaluator capability."""

    def __init__(self, store: ObserverArtifactWriter) -> None:
        self._store = store

    def write_event(self, event: ObserverControlEvent) -> None:
        self._store.append_event("changes/changes.jsonl", event)


class AdServiceFailureController:
    """Execute one allowlisted logical transition with positive read-back."""

    def __init__(
        self,
        *,
        adapter: ScenarioControlAdapter,
        observer_sink: ObserverEventSink,
        run_id: str,
        correlation_id: str,
        clock: ControlClock | None = None,
        timeout_seconds: float = 30,
        poll_interval_seconds: float = 0.25,
    ) -> None:
        if (
            len(run_id) != 32
            or any(character not in "0123456789abcdef" for character in run_id)
            or len(correlation_id) != 32
            or any(character not in "0123456789abcdef" for character in correlation_id)
        ):
            raise ValueError("control identifiers must be opaque lowercase hex")
        if timeout_seconds <= 0 or poll_interval_seconds <= 0:
            raise ValueError("control timeouts must be positive")
        self._adapter = adapter
        self._observer_sink = observer_sink
        self._run_id = run_id
        self._correlation_id = correlation_id
        self._clock = clock or SystemControlClock()
        self._timeout_seconds = timeout_seconds
        self._poll_interval_seconds = poll_interval_seconds

    def inject(self) -> TransitionExecution:
        return self.transition(ScenarioAction.INJECT)

    def reset(self) -> TransitionExecution:
        return self.transition(ScenarioAction.RESET)

    def transition(self, action: ScenarioAction) -> TransitionExecution:
        started_at = self._clock.now()
        started_monotonic = self._clock.monotonic()
        deadline = started_monotonic + self._timeout_seconds
        target = (
            ScenarioState.INJECTED
            if action is ScenarioAction.INJECT
            else ScenarioState.BASELINE
        )
        execution: TransitionExecution | None = None
        preparation: TransitionPreparation | None = None
        acquired = False
        try:
            with self._adapter.transition_guard(
                timeout_seconds=self._remaining_seconds(deadline)
            ) as acquired:
                if not acquired or self._clock.monotonic() >= deadline:
                    before = ScenarioState.UNKNOWN
                    mutation_state = MutationState.NOT_APPLIED
                    terminal = self._timeout_result(action)
                    error_category = "TIMEOUT"
                    evidence_failure_stage = None
                    pending_recovery_unresolved = False
                else:
                    (
                        before,
                        mutation_state,
                        terminal,
                        error_category,
                        evidence_failure_stage,
                        pending_recovery_unresolved,
                    ) = self._execute_locked_transition(
                        action=action,
                        target=target,
                        started_at=started_at,
                        started_monotonic=started_monotonic,
                        deadline=deadline,
                    )

                ended_at = self._clock.now()
                ended_monotonic = self._clock.monotonic()
                event = ObserverControlEvent(
                    schema_version="phase0.observer-control-event.v1",
                    control_event_id=secrets.token_hex(16),
                    correlation_id=self._correlation_id,
                    started_at=started_at,
                    ended_at=ended_at,
                    monotonic_duration_seconds=max(
                        0.0,
                        ended_monotonic - started_monotonic,
                    ),
                    transition_succeeded=terminal.outcome is Outcome.SUCCESS,
                    error_category=error_category,
                )
                execution = TransitionExecution(
                    action=action,
                    before_state=before,
                    target_state=target,
                    mutation_state=mutation_state,
                    pending_recovery_unresolved=pending_recovery_unresolved,
                    terminal_result=terminal,
                    observer_event=event,
                )
                if evidence_failure_stage is not None:
                    self._best_effort_emergency(
                        execution,
                        failure_stage=evidence_failure_stage,
                    )
                if acquired:
                    try:
                        preparation = self._adapter.prepare_transition(execution)
                    except (EvidencePersistenceError, OSError, ValueError):
                        execution = self._evidence_failure_execution(execution)
                        self._best_effort_emergency(
                            execution,
                            failure_stage="EVALUATOR_PREPARATION",
                        )
        except TransitionGuardCleanupError:
            if execution is None:
                raise
            failure = self._evidence_failure_execution(execution)
            self._best_effort_emergency(
                failure,
                failure_stage="TRANSITION_GUARD_CLEANUP",
            )
            if preparation is None:
                return failure
            return self._persist_execution(
                failure,
                preparation=preparation,
                record_status="CLEANUP_FAILED",
            )
        except (KeyboardInterrupt, SystemExit):
            if execution is not None and preparation is not None:
                interrupted = self._evidence_failure_execution(execution)
                self._best_effort_finalization(
                    interrupted,
                    preparation=preparation,
                    record_status="INTERRUPTED",
                )
                self._best_effort_emergency(
                    interrupted,
                    failure_stage="TRANSITION_GUARD_INTERRUPTED",
                )
            raise
        assert execution is not None
        if preparation is None and acquired:
            try:
                self._observer_sink.write_event(execution.observer_event)
            except (EvidencePersistenceError, OSError, ValueError):
                pass
            return execution
        return self._persist_execution(
            execution,
            preparation=preparation,
            record_status=("FINALIZED" if acquired else "PRELOCK_TIMEOUT"),
        )

    def _execute_locked_transition(
        self,
        *,
        action: ScenarioAction,
        target: ScenarioState,
        started_at: datetime,
        started_monotonic: float,
        deadline: float,
    ) -> tuple[
        ScenarioState,
        MutationState,
        TerminalResult,
        Literal["NONE", "STATE_UNKNOWN", "TIMEOUT", "SAFETY"],
        str | None,
        bool,
    ]:
        mutation_state = MutationState.NOT_APPLIED
        evidence_failure_stage: str | None = None
        pending_recovery_unresolved = False
        try:
            reconciled = self._adapter.reconcile_pending_intent(
                timeout_seconds=self._remaining_seconds(deadline),
            )
            before = (
                reconciled
                if reconciled is not None
                else self._adapter.observe_state(
                    timeout_seconds=self._remaining_seconds(deadline)
                )
            )
        except PendingRecoveryEvidenceError as error:
            before = error.observed_state
            mutation_state = MutationState.UNKNOWN
            terminal = TerminalResult(
                outcome=Outcome.MANUAL_INTERVENTION_REQUIRED,
                reason_code="EVIDENCE_PERSISTENCE_FAILED",
            )
            error_category = "SAFETY"
            evidence_failure_stage = "PENDING_RECOVERY_EVIDENCE"
            pending_recovery_unresolved = error.observed_state is ScenarioState.UNKNOWN
        except EvidencePersistenceError:
            before = ScenarioState.UNKNOWN
            terminal = TerminalResult(
                outcome=Outcome.FAILED_ACCEPTANCE,
                reason_code="EVIDENCE_PERSISTENCE_FAILED",
            )
            error_category = "SAFETY"
            evidence_failure_stage = "INITIAL_OFREP_READBACK"
        else:
            prestate_completed_in_time = self._clock.monotonic() <= deadline

            if not prestate_completed_in_time:
                terminal = self._timeout_result(action)
                error_category = "TIMEOUT"
            elif before is ScenarioState.UNKNOWN:
                terminal = TerminalResult(
                    outcome=Outcome.MANUAL_INTERVENTION_REQUIRED,
                    reason_code="MUTATION_STATE_UNKNOWN",
                )
                error_category = "STATE_UNKNOWN"
            elif before is target:
                terminal = TerminalResult(
                    outcome=Outcome.SUCCESS,
                    reason_code="CONTROL_STATE_CONFIRMED",
                )
                error_category = "NONE"
            else:
                try:
                    intent_id = self._adapter.record_pending_intent(
                        action=action,
                        before_state=before,
                        target_state=target,
                        started_at=started_at,
                        started_monotonic=started_monotonic,
                        deadline_monotonic=deadline,
                    )
                except EvidencePersistenceError:
                    terminal = TerminalResult(
                        outcome=Outcome.FAILED_ACCEPTANCE,
                        reason_code="EVIDENCE_PERSISTENCE_FAILED",
                    )
                    error_category = "SAFETY"
                    evidence_failure_stage = "PENDING_INTENT"
                else:
                    try:
                        mutation_state = self._adapter.apply_state(target)
                    except ControlMutationUncertain:
                        mutation_state = MutationState.UNKNOWN
                        terminal = TerminalResult(
                            outcome=Outcome.MANUAL_INTERVENTION_REQUIRED,
                            reason_code="CONTROL_MUTATION_UNCERTAIN",
                        )
                        error_category = "SAFETY"
                    else:
                        if mutation_state is MutationState.UNKNOWN:
                            terminal = TerminalResult(
                                outcome=Outcome.MANUAL_INTERVENTION_REQUIRED,
                                reason_code="MUTATION_STATE_UNKNOWN",
                            )
                            error_category = "STATE_UNKNOWN"
                            evidence_failure_stage = "MUTATION_STATE_UNKNOWN"
                        else:
                            try:
                                terminal, error_category = self._await_target(
                                    action=action,
                                    target=target,
                                    deadline=deadline,
                                )
                                if terminal.outcome is Outcome.SUCCESS:
                                    self._adapter.complete_pending_intent(
                                        intent_id,
                                        observed_state=target,
                                    )
                            except EvidencePersistenceError:
                                terminal = TerminalResult(
                                    outcome=Outcome.MANUAL_INTERVENTION_REQUIRED,
                                    reason_code="EVIDENCE_PERSISTENCE_FAILED",
                                )
                                error_category = "SAFETY"
                                evidence_failure_stage = (
                                    "POST_MUTATION_CONTROL_EVIDENCE"
                                )
        return (
            before,
            mutation_state,
            terminal,
            error_category,
            evidence_failure_stage,
            pending_recovery_unresolved,
        )

    def _await_target(
        self,
        *,
        action: ScenarioAction,
        target: ScenarioState,
        deadline: float,
    ) -> tuple[
        TerminalResult,
        Literal["NONE", "STATE_UNKNOWN", "TIMEOUT"],
    ]:
        while self._clock.monotonic() < deadline:
            observed = self._adapter.observe_state(
                timeout_seconds=self._remaining_seconds(deadline)
            )
            if observed is ScenarioState.UNKNOWN:
                return (
                    TerminalResult(
                        outcome=Outcome.MANUAL_INTERVENTION_REQUIRED,
                        reason_code="MUTATION_STATE_UNKNOWN",
                    ),
                    "STATE_UNKNOWN",
                )
            if observed is target and self._clock.monotonic() <= deadline:
                return (
                    TerminalResult(
                        outcome=Outcome.SUCCESS,
                        reason_code="CONTROL_TRANSITION_CONFIRMED",
                    ),
                    "NONE",
                )
            remaining = deadline - self._clock.monotonic()
            if remaining <= 0:
                break
            self._clock.sleep(min(self._poll_interval_seconds, remaining))
        return self._timeout_result(action), "TIMEOUT"

    @staticmethod
    def _timeout_result(action: ScenarioAction) -> TerminalResult:
        return TerminalResult(
            outcome=Outcome.FAILED_ACCEPTANCE,
            reason_code=(
                "INJECT_TIMEOUT" if action is ScenarioAction.INJECT else "RESET_TIMEOUT"
            ),
        )

    def _remaining_seconds(self, deadline: float) -> float:
        return max(deadline - self._clock.monotonic(), 1e-9)

    def _persist_execution(
        self,
        execution: TransitionExecution,
        *,
        preparation: TransitionPreparation | None,
        record_status: Literal[
            "FINALIZED",
            "CLEANUP_FAILED",
            "PRELOCK_TIMEOUT",
        ],
    ) -> TransitionExecution:
        try:
            self._adapter.finalize_transition(
                execution,
                preparation=preparation,
                record_status=record_status,
            )
        except (EvidencePersistenceError, OSError, ValueError):
            failure = self._evidence_failure_execution(execution)
            self._best_effort_emergency(
                failure,
                failure_stage="EVALUATOR_TRANSITION",
            )
            try:
                self._observer_sink.write_event(failure.observer_event)
            except (EvidencePersistenceError, OSError, ValueError):
                pass
            return failure
        try:
            self._observer_sink.write_event(execution.observer_event)
        except (EvidencePersistenceError, OSError, ValueError):
            failure = self._evidence_failure_execution(execution)
            self._best_effort_emergency(
                failure,
                failure_stage="OBSERVER_TRANSITION",
            )
            return failure
        return execution

    def _best_effort_finalization(
        self,
        execution: TransitionExecution,
        *,
        preparation: TransitionPreparation,
        record_status: Literal["INTERRUPTED"],
    ) -> None:
        try:
            self._adapter.finalize_transition(
                execution,
                preparation=preparation,
                record_status=record_status,
            )
        except (EvidencePersistenceError, OSError, ValueError):
            pass

    def _evidence_failure_execution(
        self,
        execution: TransitionExecution,
    ) -> TransitionExecution:
        manual = (
            execution.mutation_state is not MutationState.NOT_APPLIED
            or execution.terminal_result.outcome is Outcome.MANUAL_INTERVENTION_REQUIRED
        )
        terminal = TerminalResult(
            outcome=(
                Outcome.MANUAL_INTERVENTION_REQUIRED
                if manual
                else Outcome.FAILED_ACCEPTANCE
            ),
            reason_code="EVIDENCE_PERSISTENCE_FAILED",
        )
        ended_at = self._clock.now()
        observer_event = ObserverControlEvent(
            **{
                **execution.observer_event.model_dump(mode="python"),
                "ended_at": ended_at,
                "monotonic_duration_seconds": (
                    execution.observer_event.monotonic_duration_seconds
                ),
                "transition_succeeded": False,
                "error_category": "SAFETY",
            }
        )
        return TransitionExecution(
            action=execution.action,
            before_state=execution.before_state,
            target_state=execution.target_state,
            mutation_state=execution.mutation_state,
            pending_recovery_unresolved=execution.pending_recovery_unresolved,
            terminal_result=terminal,
            observer_event=observer_event,
        )

    def _best_effort_emergency(
        self,
        execution: TransitionExecution,
        *,
        failure_stage: str,
    ) -> None:
        try:
            self._adapter.record_emergency_diagnostic(
                execution,
                failure_stage=failure_stage,
            )
        except (EvidencePersistenceError, OSError, ValueError):
            pass
