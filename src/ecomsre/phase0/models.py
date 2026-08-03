"""Immutable Phase 0 policy, measurement, and outcome models."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Outcome(str, Enum):
    SUCCESS = "SUCCESS"
    BLOCKED_ENVIRONMENT = "BLOCKED_ENVIRONMENT"
    BLOCKED_UPSTREAM = "BLOCKED_UPSTREAM"
    FAILED_ACCEPTANCE = "FAILED_ACCEPTANCE"
    UNSAFE = "UNSAFE"
    MANUAL_INTERVENTION_REQUIRED = "MANUAL_INTERVENTION_REQUIRED"
    INVALID_INVOCATION = "INVALID_INVOCATION"

    @property
    def exit_code(self) -> int:
        return {
            Outcome.SUCCESS: 0,
            Outcome.BLOCKED_ENVIRONMENT: 20,
            Outcome.BLOCKED_UPSTREAM: 21,
            Outcome.FAILED_ACCEPTANCE: 30,
            Outcome.UNSAFE: 40,
            Outcome.MANUAL_INTERVENTION_REQUIRED: 41,
            Outcome.INVALID_INVOCATION: 64,
        }[self]


class TerminalResult(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        allow_inf_nan=False,
    )

    outcome: Outcome
    reason_code: str = Field(min_length=1)
    exit_code: int | None = None

    @model_validator(mode="after")
    def require_exact_exit_code(self) -> "TerminalResult":
        expected = self.outcome.exit_code
        if self.exit_code is not None and self.exit_code != expected:
            raise ValueError(
                f"exit code {self.exit_code} conflicts with {self.outcome.value}"
            )
        object.__setattr__(self, "exit_code", expected)
        return self


class MeasurementPhase(str, Enum):
    BASELINE = "baseline"
    FAULT = "fault"
    RECOVERY = "recovery"


class MeasurementSource(str, Enum):
    PROMETHEUS_GETADS = "prometheus_getads"


class CounterScope(str, Enum):
    WINDOW_LOCAL_DELTA = "window_local_delta"
    WINDOW_LOCAL_RATE = "window_local_rate"


class Phase0Policy(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        allow_inf_nan=False,
    )

    minimum_getads_attempts: int = Field(default=200, ge=1)
    window_deadline_seconds: float = Field(default=180, gt=0)
    stabilization_seconds: float = Field(default=30, ge=0)
    consecutive_cycles: int = Field(default=3, ge=1)
    baseline_max_error_rate: float = Field(default=0.01, ge=0, le=1)
    fault_min_error_rate: float = Field(default=0.05, ge=0, le=1)
    fault_max_error_rate: float = Field(default=0.20, ge=0, le=1)
    recovery_max_error_rate: float = Field(default=0.01, ge=0, le=1)

    @model_validator(mode="after")
    def require_ordered_fault_thresholds(self) -> "Phase0Policy":
        if self.fault_min_error_rate > self.fault_max_error_rate:
            raise ValueError("fault threshold range is reversed")
        return self

    @property
    def is_canonical(self) -> bool:
        return self == Phase0Policy()


class WindowCounts(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        allow_inf_nan=False,
    )

    run_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    window_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    scenario_phase: MeasurementPhase
    source: MeasurementSource
    counter_scope: CounterScope
    window_started_at: datetime
    window_ended_at: datetime
    query_fixture_version: str = Field(min_length=1)
    getads_attempts: int = Field(ge=0)
    getads_errors: int = Field(ge=0)
    unrelated_http_requests: int = Field(default=0, ge=0)
    monotonic_duration_seconds: float = Field(ge=0)

    @model_validator(mode="after")
    def errors_cannot_exceed_attempts(self) -> "WindowCounts":
        if self.getads_errors > self.getads_attempts:
            raise ValueError("GetAds errors cannot exceed GetAds attempts")
        if (
            self.window_started_at.utcoffset() is None
            or self.window_ended_at.utcoffset() is None
            or self.window_started_at.utcoffset().total_seconds() != 0
            or self.window_ended_at.utcoffset().total_seconds() != 0
        ):
            raise ValueError("measurement window timestamps must be UTC")
        if self.window_ended_at <= self.window_started_at:
            raise ValueError("measurement window end must follow its start")
        return self


class WindowDecision(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        allow_inf_nan=False,
    )

    phase: MeasurementPhase
    counts: WindowCounts
    error_rate: float = Field(ge=0, le=1)
    wilson_lower: float = Field(ge=0, le=1)
    wilson_upper: float = Field(ge=0, le=1)
    passed: bool
    reason_code: str

    @model_validator(mode="after")
    def require_consistent_window_decision(self) -> "WindowDecision":
        if self.phase is not self.counts.scenario_phase:
            raise ValueError("window phase conflicts with count provenance")
        expected_rate = (
            self.counts.getads_errors / self.counts.getads_attempts
            if self.counts.getads_attempts
            else 0.0
        )
        if abs(self.error_rate - expected_rate) > 1e-12:
            raise ValueError("window error rate conflicts with counts")
        if not self.wilson_lower <= self.error_rate <= self.wilson_upper:
            raise ValueError("Wilson interval does not contain error rate")
        if self.reason_code == "WINDOW_SAMPLE_TIMEOUT" and self.passed:
            raise ValueError("sample timeout cannot be a passing decision")
        if self.passed != (self.reason_code == "THRESHOLD_PASSED"):
            raise ValueError("window pass result conflicts with reason code")
        return self


class CycleDecision(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        allow_inf_nan=False,
    )

    cycle_number: int = Field(ge=1)
    baseline: WindowDecision
    fault: WindowDecision
    recovery: WindowDecision

    @model_validator(mode="after")
    def require_one_decision_per_phase(self) -> "CycleDecision":
        expected = (
            (self.baseline, MeasurementPhase.BASELINE),
            (self.fault, MeasurementPhase.FAULT),
            (self.recovery, MeasurementPhase.RECOVERY),
        )
        if any(decision.phase is not phase for decision, phase in expected):
            raise ValueError("cycle decisions do not match their measurement phases")
        if any(
            decision.counts.scenario_phase is not phase for decision, phase in expected
        ):
            raise ValueError("cycle window provenance does not match its phase")
        run_ids = {
            self.baseline.counts.run_id,
            self.fault.counts.run_id,
            self.recovery.counts.run_id,
        }
        if len(run_ids) != 1:
            raise ValueError("cycle windows must belong to one run")
        window_ids = {
            self.baseline.counts.window_id,
            self.fault.counts.window_id,
            self.recovery.counts.window_id,
        }
        if len(window_ids) != 3:
            raise ValueError("cycle must use three distinct current windows")
        if not (
            self.baseline.counts.window_ended_at <= self.fault.counts.window_started_at
            and self.fault.counts.window_ended_at
            <= self.recovery.counts.window_started_at
        ):
            raise ValueError("cycle measurement windows overlap or are reordered")
        return self

    @property
    def passed(self) -> bool:
        return self.baseline.passed and self.fault.passed and self.recovery.passed


class RunDecision(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        allow_inf_nan=False,
    )

    cycles: tuple[CycleDecision, ...]
    passed: bool
    canonical: bool
    outcome: Outcome
    exit_code: int
    failed_cycles: tuple[int, ...]
    reason_codes: tuple[str, ...]

    @model_validator(mode="after")
    def require_consistent_run_outcome(self) -> "RunDecision":
        if self.exit_code != self.outcome.exit_code:
            raise ValueError("run decision exit code conflicts with outcome")
        cycle_numbers = tuple(cycle.cycle_number for cycle in self.cycles)
        if cycle_numbers != (1, 2, 3):
            raise ValueError("formal run requires cycles 1, 2, and 3")
        windows = tuple(
            window
            for cycle in self.cycles
            for window in (cycle.baseline, cycle.fault, cycle.recovery)
        )
        if len({window.counts.run_id for window in windows}) != 1:
            raise ValueError("formal cycles must belong to one run")
        window_ids = tuple(window.counts.window_id for window in windows)
        if len(set(window_ids)) != len(window_ids):
            raise ValueError("formal run cannot reuse a measurement window")
        if any(
            current.counts.window_ended_at > following.counts.window_started_at
            for current, following in zip(windows, windows[1:])
        ):
            raise ValueError("formal run windows overlap or are reordered")
        actual_failed = tuple(
            cycle.cycle_number for cycle in self.cycles if not cycle.passed
        )
        if self.failed_cycles != actual_failed:
            raise ValueError("run failed cycles are inconsistent")
        if self.passed != (not actual_failed):
            raise ValueError("run passed state is inconsistent")
        if not self.canonical:
            raise ValueError("formal run decision must be canonical")
        expected_outcome = Outcome.SUCCESS if self.passed else Outcome.FAILED_ACCEPTANCE
        if self.outcome is not expected_outcome:
            raise ValueError("run outcome conflicts with cycle decisions")
        expected_reasons = (
            ()
            if self.passed
            else tuple(
                f"CYCLE_{cycle_number:02d}_FAILED" for cycle_number in actual_failed
            )
        )
        if self.reason_codes != expected_reasons:
            raise ValueError("run reason codes are inconsistent")
        if self.outcome is Outcome.SUCCESS and (not self.passed or self.failed_cycles):
            raise ValueError("SUCCESS requires a canonical three-cycle pass")
        return self


class DiagnosticRunResult(BaseModel):
    """A non-formal result that cannot carry an acceptance outcome or exit."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    cycles: tuple[CycleDecision, ...]
    diagnostic_passed: bool
    canonical: Literal[False] = False
    failed_cycles: tuple[int, ...]
    reason_codes: tuple[Literal["NON_CANONICAL_POLICY"], ...] = (
        "NON_CANONICAL_POLICY",
    )

    @model_validator(mode="after")
    def require_consistent_diagnostic_result(self) -> "DiagnosticRunResult":
        expected_numbers = tuple(range(1, len(self.cycles) + 1))
        actual_numbers = tuple(cycle.cycle_number for cycle in self.cycles)
        if actual_numbers != expected_numbers:
            raise ValueError("diagnostic cycle numbers are inconsistent")
        actual_failed = tuple(
            cycle.cycle_number for cycle in self.cycles if not cycle.passed
        )
        if self.failed_cycles != actual_failed:
            raise ValueError("diagnostic failed cycles are inconsistent")
        if self.diagnostic_passed != (not actual_failed):
            raise ValueError("diagnostic pass result is inconsistent")
        return self


class DiagnosticStatus(str, Enum):
    """Terminal status for a non-canonical smoke, never a Phase 0 outcome."""

    PASSED = "PASSED"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"
    UNSAFE = "UNSAFE"

    @property
    def exit_code(self) -> int:
        return {
            DiagnosticStatus.PASSED: 0,
            DiagnosticStatus.FAILED: 30,
            DiagnosticStatus.BLOCKED: 20,
            DiagnosticStatus.UNSAFE: 40,
        }[self]


class DiagnosticSmokePolicy(BaseModel):
    """Exact one-cycle diagnostic policy authorized by the repair prompt."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    cycles: Literal[1] = 1
    stabilization_seconds: Literal[30] = 30
    minimum_getads_attempts_per_window: Literal[100] = 100
    window_deadline_seconds: Literal[120] = 120
    baseline_max_error_rate: Literal[0.01] = 0.01
    fault_min_error_rate: Literal[0.05] = 0.05
    fault_max_error_rate: Literal[0.20] = 0.20
    recovery_max_error_rate: Literal[0.01] = 0.01

    def as_phase0_policy(self) -> Phase0Policy:
        return Phase0Policy(
            minimum_getads_attempts=self.minimum_getads_attempts_per_window,
            window_deadline_seconds=self.window_deadline_seconds,
            stabilization_seconds=self.stabilization_seconds,
            consecutive_cycles=self.cycles,
            baseline_max_error_rate=self.baseline_max_error_rate,
            fault_min_error_rate=self.fault_min_error_rate,
            fault_max_error_rate=self.fault_max_error_rate,
            recovery_max_error_rate=self.recovery_max_error_rate,
        )


class SmokePhaseEvidence(BaseModel):
    """Complete report projection for one measured diagnostic phase."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    phase: MeasurementPhase
    attempts: int = Field(ge=0)
    errors: int = Field(ge=0)
    error_rate: float = Field(ge=0, le=1)
    wilson_lower: float = Field(ge=0, le=1)
    wilson_upper: float = Field(ge=0, le=1)
    window_started_at: datetime
    window_ended_at: datetime
    monotonic_duration_seconds: float = Field(ge=0)
    fixture_sha256: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    raw_artifact_refs: tuple[str, ...]
    passed: bool
    reason_code: str = Field(min_length=1)

    @model_validator(mode="after")
    def require_consistent_phase_projection(self) -> "SmokePhaseEvidence":
        if self.errors > self.attempts:
            raise ValueError("smoke phase errors exceed attempts")
        expected = self.errors / self.attempts if self.attempts else 0.0
        if abs(expected - self.error_rate) > 1e-12:
            raise ValueError("smoke phase error rate is inconsistent")
        if not self.wilson_lower <= self.error_rate <= self.wilson_upper:
            raise ValueError("smoke phase Wilson interval is inconsistent")
        return self


class SmokeControlAcknowledgement(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    stage: Literal["promotion", "diagnostic", "finalization"]
    phase: MeasurementPhase | None = None
    transition_succeeded: bool
    acknowledgement_duration_seconds: float = Field(ge=0)
    reason_code: str = Field(min_length=1)
    artifact_ref: str = Field(min_length=1)


class SmokeAttemptEvidence(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    attempt_number: Literal[1] = 1
    phase_evidence: tuple[SmokePhaseEvidence, ...]
    control_acknowledgements: tuple[SmokeControlAcknowledgement, ...]
    initial_readiness_artifacts: tuple[str, ...]
    post_promotion_readiness_artifacts: tuple[str, ...]
    final_readiness_artifacts: tuple[str, ...]
    probe_attribution_artifacts: tuple[str, ...]
    safe_reset_attempted: bool
    safe_reset_succeeded: bool
    fresh_stop_authority: bool
    safe_stop_required: bool = True
    safe_stop_attempted: bool
    safe_stop_succeeded: bool
    owned_volume_cleanup_required: bool = True
    owned_volume_cleanup_attempted: bool = False
    owned_volume_cleanup_succeeded: bool = False
    failure_reason_codes: tuple[str, ...]


class SmokeReport(BaseModel):
    """Independent diagnostic report that cannot encode formal acceptance."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    schema_version: Literal["phase0.smoke-report.v1"]
    run_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    canonical: Literal[False] = False
    diagnostic_status: DiagnosticStatus
    phase0_complete: Literal[False] = False
    formal_three_cycle_acceptance_executed: Literal[False] = False
    policy: DiagnosticSmokePolicy
    phase_decisions: dict[MeasurementPhase, bool]
    telemetry_gate_decisions: dict[str, bool]
    task7_registry_frozen: bool
    origin_promotion_run_id: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{32}$",
    )
    attempts: tuple[SmokeAttemptEvidence, ...]
    safe_stop_completed: bool
    owned_volume_cleanup_completed: bool = False
    failure_reason_codes: tuple[str, ...]

    @property
    def exit_code(self) -> int:
        return self.diagnostic_status.exit_code

    @model_validator(mode="after")
    def require_diagnostic_truth(self) -> "SmokeReport":
        passed = self.diagnostic_status is DiagnosticStatus.PASSED
        required_phases = set(MeasurementPhase)
        required_telemetry = {"prometheus", "jaeger", "opensearch", "probe"}
        required_acknowledgements = {
            ("promotion", MeasurementPhase.BASELINE),
            ("promotion", MeasurementPhase.FAULT),
            ("promotion", MeasurementPhase.RECOVERY),
            ("diagnostic", MeasurementPhase.BASELINE),
            ("diagnostic", MeasurementPhase.FAULT),
            ("diagnostic", MeasurementPhase.RECOVERY),
            ("finalization", None),
        }
        attempt = self.attempts[0] if len(self.attempts) == 1 else None
        fixture_hashes = (
            {item.fixture_sha256 for item in attempt.phase_evidence}
            if attempt is not None
            else set()
        )
        complete_attempt = (
            attempt is not None
            and len(attempt.phase_evidence) == 3
            and {item.phase for item in attempt.phase_evidence} == required_phases
            and all(
                item.passed
                and item.attempts >= self.policy.minimum_getads_attempts_per_window
                and bool(item.raw_artifact_refs)
                for item in attempt.phase_evidence
            )
            and fixture_hashes != {None}
            and len(fixture_hashes) == 1
            and "0" * 64 not in fixture_hashes
            and len(attempt.control_acknowledgements)
            == len(required_acknowledgements)
            and {
                (item.stage, item.phase)
                for item in attempt.control_acknowledgements
            }
            == required_acknowledgements
            and all(
                item.transition_succeeded
                and item.reason_code == "CONTROL_STATE_CONFIRMED"
                and item.acknowledgement_duration_seconds <= 30
                and bool(item.artifact_ref)
                for item in attempt.control_acknowledgements
            )
            and bool(attempt.initial_readiness_artifacts)
            and bool(attempt.post_promotion_readiness_artifacts)
            and bool(attempt.final_readiness_artifacts)
            and bool(attempt.probe_attribution_artifacts)
            and attempt.safe_reset_attempted
            and attempt.safe_reset_succeeded
            and attempt.fresh_stop_authority
            and attempt.safe_stop_required
            and attempt.safe_stop_attempted
            and attempt.safe_stop_succeeded
            and attempt.owned_volume_cleanup_required
            and attempt.owned_volume_cleanup_attempted
            and attempt.owned_volume_cleanup_succeeded
            and not attempt.failure_reason_codes
        )
        if passed and (
            set(self.phase_decisions) != required_phases
            or not all(self.phase_decisions.values())
            or not required_telemetry.issubset(self.telemetry_gate_decisions)
            or not all(
                self.telemetry_gate_decisions[name] for name in required_telemetry
            )
            or not self.task7_registry_frozen
            or self.origin_promotion_run_id is None
            or not complete_attempt
            or not self.safe_stop_completed
            or not self.owned_volume_cleanup_completed
            or self.failure_reason_codes
        ):
            raise ValueError("passing smoke report is missing diagnostic proof")
        if not passed and not self.failure_reason_codes:
            raise ValueError("non-passing smoke report requires failure reasons")
        return self
