"""Pure statistical evaluation for Phase 0 measurement windows."""

from __future__ import annotations

import math
from collections.abc import Sequence

from ecomsre.phase0.models import (
    CycleDecision,
    DiagnosticRunResult,
    MeasurementPhase,
    Outcome,
    Phase0Policy,
    RunDecision,
    WindowCounts,
    WindowDecision,
)


def wilson_interval(
    *,
    errors: int,
    attempts: int,
    z_score: float = 1.96,
) -> tuple[float, float]:
    """Return a two-sided Wilson score interval for a binomial rate."""
    if attempts < 0 or errors < 0 or errors > attempts:
        raise ValueError("invalid binomial counts")
    if not math.isfinite(z_score) or z_score <= 0:
        raise ValueError("z_score must be finite and positive")
    if attempts == 0:
        return (0.0, 1.0)

    rate = errors / attempts
    z_squared = z_score**2
    denominator = 1 + z_squared / attempts
    center = (rate + z_squared / (2 * attempts)) / denominator
    margin = (
        z_score
        * math.sqrt(rate * (1 - rate) / attempts + z_squared / (4 * attempts**2))
        / denominator
    )
    return (max(0.0, center - margin), min(1.0, center + margin))


def evaluate_window(
    phase: MeasurementPhase,
    counts: WindowCounts,
    policy: Phase0Policy | None = None,
) -> WindowDecision:
    """Evaluate one window using only observed GetAds calls."""
    active_policy = policy or Phase0Policy()
    if counts.scenario_phase is not phase:
        raise ValueError("measurement scenario phase does not match evaluation phase")
    attempts = counts.getads_attempts
    rate = counts.getads_errors / attempts if attempts else 0.0
    lower, upper = wilson_interval(
        errors=counts.getads_errors,
        attempts=attempts,
    )

    if (
        attempts < active_policy.minimum_getads_attempts
        or counts.monotonic_duration_seconds > active_policy.window_deadline_seconds
    ):
        return WindowDecision(
            phase=phase,
            counts=counts,
            error_rate=rate,
            wilson_lower=lower,
            wilson_upper=upper,
            passed=False,
            reason_code="WINDOW_SAMPLE_TIMEOUT",
        )

    passed = _threshold_passes(phase, rate, active_policy)
    return WindowDecision(
        phase=phase,
        counts=counts,
        error_rate=rate,
        wilson_lower=lower,
        wilson_upper=upper,
        passed=passed,
        reason_code="THRESHOLD_PASSED" if passed else _threshold_reason(phase),
    )


def canonical_window_decision(
    phase: MeasurementPhase,
    submitted: WindowDecision,
    policy: Phase0Policy,
) -> WindowDecision:
    """Rebuild a window decision exclusively from validated raw counts."""
    counts = WindowCounts.model_validate(submitted.counts.model_dump(mode="python"))
    return evaluate_window(phase, counts, policy)


def canonical_cycle_decision(
    submitted: CycleDecision,
    policy: Phase0Policy,
) -> CycleDecision:
    """Discard submitted verdict fields and recompute a canonical cycle."""
    return CycleDecision(
        cycle_number=submitted.cycle_number,
        baseline=canonical_window_decision(
            MeasurementPhase.BASELINE,
            submitted.baseline,
            policy,
        ),
        fault=canonical_window_decision(
            MeasurementPhase.FAULT,
            submitted.fault,
            policy,
        ),
        recovery=canonical_window_decision(
            MeasurementPhase.RECOVERY,
            submitted.recovery,
            policy,
        ),
    )


def evaluate_run(
    cycles: Sequence[CycleDecision],
    policy: Phase0Policy | None = None,
) -> RunDecision:
    """Require exactly three ordered, independently passing cycles."""
    active_policy = policy or Phase0Policy()
    if not active_policy.is_canonical:
        raise ValueError("formal evaluation requires the canonical Phase 0 policy")

    expected_numbers = list(range(1, active_policy.consecutive_cycles + 1))
    actual_numbers = [cycle.cycle_number for cycle in cycles]
    if actual_numbers != expected_numbers:
        raise ValueError(
            f"cycle numbers must be exactly {expected_numbers}, got {actual_numbers}"
        )

    canonical_cycles = tuple(
        canonical_cycle_decision(cycle, active_policy) for cycle in cycles
    )
    failed_cycles = tuple(
        cycle.cycle_number for cycle in canonical_cycles if not cycle.passed
    )
    passed = not failed_cycles
    return RunDecision(
        cycles=canonical_cycles,
        passed=passed,
        canonical=True,
        outcome=Outcome.SUCCESS if passed else Outcome.FAILED_ACCEPTANCE,
        exit_code=(
            Outcome.SUCCESS.exit_code if passed else Outcome.FAILED_ACCEPTANCE.exit_code
        ),
        failed_cycles=failed_cycles,
        reason_codes=(
            ()
            if passed
            else tuple(
                f"CYCLE_{cycle_number:02d}_FAILED" for cycle_number in failed_cycles
            )
        ),
    )


def evaluate_diagnostic_run(
    cycles: Sequence[CycleDecision],
    policy: Phase0Policy,
) -> DiagnosticRunResult:
    """Evaluate an explicitly non-canonical diagnostic without formal outcome."""
    if policy.is_canonical:
        raise ValueError("diagnostic evaluation requires a non-canonical policy")

    expected_numbers = list(range(1, policy.consecutive_cycles + 1))
    actual_numbers = [cycle.cycle_number for cycle in cycles]
    if actual_numbers != expected_numbers:
        raise ValueError(
            f"cycle numbers must be exactly {expected_numbers}, got {actual_numbers}"
        )
    canonical_cycles = tuple(
        canonical_cycle_decision(cycle, policy) for cycle in cycles
    )
    failed_cycles = tuple(
        cycle.cycle_number for cycle in canonical_cycles if not cycle.passed
    )
    return DiagnosticRunResult(
        cycles=canonical_cycles,
        diagnostic_passed=not failed_cycles,
        failed_cycles=failed_cycles,
    )


def _threshold_passes(
    phase: MeasurementPhase,
    rate: float,
    policy: Phase0Policy,
) -> bool:
    if phase is MeasurementPhase.BASELINE:
        return rate <= policy.baseline_max_error_rate
    if phase is MeasurementPhase.FAULT:
        return policy.fault_min_error_rate <= rate <= policy.fault_max_error_rate
    return rate <= policy.recovery_max_error_rate


def _threshold_reason(phase: MeasurementPhase) -> str:
    return {
        MeasurementPhase.BASELINE: "BASELINE_THRESHOLD_FAILED",
        MeasurementPhase.FAULT: "FAULT_THRESHOLD_FAILED",
        MeasurementPhase.RECOVERY: "RECOVERY_THRESHOLD_FAILED",
    }[phase]
