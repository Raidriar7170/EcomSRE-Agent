from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from ecomsre.phase0.models import (
    CounterScope,
    CycleDecision,
    MeasurementPhase,
    MeasurementSource,
    Outcome,
    Phase0Policy,
    WindowCounts,
    WindowDecision,
)
from ecomsre.phase0.statistics import (
    evaluate_diagnostic_run,
    evaluate_run,
    evaluate_window,
    wilson_interval,
)


RUN_ID = "1" * 32
BASE_TIME = datetime(2026, 7, 30, 3, 0, tzinfo=UTC)


def _counts(
    phase: MeasurementPhase,
    *,
    attempts: int = 200,
    errors: int = 0,
    duration: float = 30,
    window_number: int = 1,
    source: MeasurementSource = MeasurementSource.PROMETHEUS_GETADS,
    counter_scope: CounterScope = CounterScope.WINDOW_LOCAL_DELTA,
) -> WindowCounts:
    return WindowCounts(
        run_id=RUN_ID,
        window_id=f"{window_number:032x}",
        scenario_phase=phase,
        source=source,
        counter_scope=counter_scope,
        window_started_at=BASE_TIME + timedelta(minutes=window_number),
        window_ended_at=BASE_TIME + timedelta(minutes=window_number, seconds=duration),
        query_fixture_version="otel-demo-3.0.0-prometheus-v1",
        getads_attempts=attempts,
        getads_errors=errors,
        monotonic_duration_seconds=duration,
    )


def test_policy_freezes_canonical_measurement_contract() -> None:
    policy = Phase0Policy()

    assert policy.minimum_getads_attempts == 200
    assert policy.window_deadline_seconds == 180
    assert policy.stabilization_seconds == 30
    assert policy.consecutive_cycles == 3


def test_measurement_denominator_is_observed_getads_attempts() -> None:
    counts = _counts(
        MeasurementPhase.FAULT,
        errors=20,
        duration=45,
    ).model_copy(
        update={
            "unrelated_http_requests": 10_000,
        }
    )

    decision = evaluate_window(MeasurementPhase.FAULT, counts)

    assert decision.error_rate == pytest.approx(0.10)
    assert decision.passed is True


@pytest.mark.parametrize(
    ("phase", "errors", "expected"),
    [
        (MeasurementPhase.BASELINE, 2, True),
        (MeasurementPhase.BASELINE, 3, False),
        (MeasurementPhase.FAULT, 10, True),
        (MeasurementPhase.FAULT, 40, True),
        (MeasurementPhase.FAULT, 9, False),
        (MeasurementPhase.FAULT, 41, False),
        (MeasurementPhase.RECOVERY, 2, True),
        (MeasurementPhase.RECOVERY, 3, False),
    ],
)
def test_thresholds_are_inclusive_at_frozen_boundaries(
    phase: MeasurementPhase,
    errors: int,
    expected: bool,
) -> None:
    counts = _counts(
        phase,
        errors=errors,
        duration=60,
    )

    assert evaluate_window(phase, counts).passed is expected


def test_window_with_fewer_than_200_getads_attempts_fails_closed() -> None:
    counts = _counts(
        MeasurementPhase.FAULT,
        attempts=199,
        errors=10,
        duration=179.9,
    )

    decision = evaluate_window(MeasurementPhase.FAULT, counts)

    assert decision.passed is False
    assert decision.reason_code == "WINDOW_SAMPLE_TIMEOUT"


def test_window_past_180_second_deadline_fails_even_with_enough_samples() -> None:
    counts = _counts(
        MeasurementPhase.FAULT,
        errors=20,
        duration=180.001,
    )

    decision = evaluate_window(MeasurementPhase.FAULT, counts)

    assert decision.passed is False
    assert decision.reason_code == "WINDOW_SAMPLE_TIMEOUT"


def test_wilson_interval_is_calculated_at_95_percent() -> None:
    lower, upper = wilson_interval(errors=10, attempts=100)

    assert lower == pytest.approx(0.0552, abs=0.0001)
    assert upper == pytest.approx(0.1744, abs=0.0001)


@pytest.mark.parametrize("z_score", [float("nan"), float("inf"), 0, -1])
def test_wilson_interval_rejects_nonfinite_or_nonpositive_z(
    z_score: float,
) -> None:
    with pytest.raises(ValueError, match="z_score"):
        wilson_interval(errors=1, attempts=10, z_score=z_score)


def test_window_decision_rejects_interval_and_reason_contradictions() -> None:
    counts = _counts(MeasurementPhase.FAULT, errors=20)
    with pytest.raises(ValidationError, match="Wilson"):
        WindowDecision(
            phase=MeasurementPhase.FAULT,
            counts=counts,
            error_rate=0.1,
            wilson_lower=0.2,
            wilson_upper=0.3,
            passed=True,
            reason_code="THRESHOLD_PASSED",
        )
    with pytest.raises(ValidationError, match="timeout"):
        WindowDecision(
            phase=MeasurementPhase.FAULT,
            counts=counts,
            error_rate=0.1,
            wilson_lower=0.05,
            wilson_upper=0.15,
            passed=True,
            reason_code="WINDOW_SAMPLE_TIMEOUT",
        )


def _cycle(cycle_number: int, *, baseline_errors: int = 1) -> CycleDecision:
    return CycleDecision(
        cycle_number=cycle_number,
        baseline=evaluate_window(
            MeasurementPhase.BASELINE,
            _counts(
                MeasurementPhase.BASELINE,
                errors=baseline_errors,
                window_number=(cycle_number - 1) * 3 + 1,
            ),
        ),
        fault=evaluate_window(
            MeasurementPhase.FAULT,
            _counts(
                MeasurementPhase.FAULT,
                errors=20,
                window_number=(cycle_number - 1) * 3 + 2,
            ),
        ),
        recovery=evaluate_window(
            MeasurementPhase.RECOVERY,
            _counts(
                MeasurementPhase.RECOVERY,
                errors=1,
                window_number=(cycle_number - 1) * 3 + 3,
            ),
        ),
    )


def test_three_independent_cycles_are_required_for_success() -> None:
    decision = evaluate_run([_cycle(1), _cycle(2), _cycle(3)])

    assert decision.passed is True
    assert decision.outcome.value == "SUCCESS"


def test_later_successes_cannot_hide_an_earlier_failed_cycle() -> None:
    decision = evaluate_run([_cycle(1, baseline_errors=3), _cycle(2), _cycle(3)])

    assert decision.passed is False
    assert decision.outcome.value == "FAILED_ACCEPTANCE"
    assert decision.failed_cycles == (1,)


@pytest.mark.parametrize(
    "forged_phase",
    [
        MeasurementPhase.BASELINE,
        MeasurementPhase.FAULT,
        MeasurementPhase.RECOVERY,
    ],
)
def test_formal_run_recomputes_forged_50_percent_window_pass(
    forged_phase: MeasurementPhase,
) -> None:
    original = _cycle(1)
    window_number = {
        MeasurementPhase.BASELINE: 1,
        MeasurementPhase.FAULT: 2,
        MeasurementPhase.RECOVERY: 3,
    }[forged_phase]
    counts = _counts(
        forged_phase,
        attempts=200,
        errors=100,
        window_number=window_number,
    )
    forged = WindowDecision.model_construct(
        phase=forged_phase,
        counts=counts,
        error_rate=0.5,
        wilson_lower=0.43,
        wilson_upper=0.57,
        passed=True,
        reason_code="THRESHOLD_PASSED",
    )
    forged_cycle = CycleDecision.model_construct(
        cycle_number=1,
        baseline=forged
        if forged_phase is MeasurementPhase.BASELINE
        else original.baseline,
        fault=forged if forged_phase is MeasurementPhase.FAULT else original.fault,
        recovery=forged
        if forged_phase is MeasurementPhase.RECOVERY
        else original.recovery,
    )

    result = evaluate_run([forged_cycle, _cycle(2), _cycle(3)])

    assert result.outcome is Outcome.FAILED_ACCEPTANCE
    assert result.failed_cycles == (1,)
    canonical_window = {
        MeasurementPhase.BASELINE: result.cycles[0].baseline,
        MeasurementPhase.FAULT: result.cycles[0].fault,
        MeasurementPhase.RECOVERY: result.cycles[0].recovery,
    }[forged_phase]
    assert canonical_window.passed is False
    assert canonical_window.reason_code.endswith("THRESHOLD_FAILED")


def test_formal_run_recomputes_forged_sample_timeout_pass() -> None:
    original = _cycle(1)
    timed_out_counts = _counts(
        MeasurementPhase.BASELINE,
        attempts=199,
        errors=0,
        duration=30,
        window_number=1,
    )
    forged = WindowDecision.model_construct(
        phase=MeasurementPhase.BASELINE,
        counts=timed_out_counts,
        error_rate=0.0,
        wilson_lower=0.0,
        wilson_upper=0.02,
        passed=True,
        reason_code="THRESHOLD_PASSED",
    )
    forged_cycle = CycleDecision.model_construct(
        cycle_number=1,
        baseline=forged,
        fault=original.fault,
        recovery=original.recovery,
    )

    result = evaluate_run([forged_cycle, _cycle(2), _cycle(3)])

    assert result.outcome is Outcome.FAILED_ACCEPTANCE
    assert result.cycles[0].baseline.reason_code == "WINDOW_SAMPLE_TIMEOUT"


def test_formal_run_rejects_cycles_from_different_run_provenance() -> None:
    original = _cycle(2)
    other_run = "2" * 32

    def move(decision: WindowDecision) -> WindowDecision:
        counts = decision.counts.model_copy(update={"run_id": other_run})
        return evaluate_window(decision.phase, counts)

    cross_run = CycleDecision(
        cycle_number=2,
        baseline=move(original.baseline),
        fault=move(original.fault),
        recovery=move(original.recovery),
    )

    with pytest.raises(ValidationError, match="one run"):
        evaluate_run([_cycle(1), cross_run, _cycle(3)])


@pytest.mark.parametrize(
    "cycles",
    [
        [_cycle(1), _cycle(2)],
        [_cycle(1), _cycle(2), _cycle(2)],
        [_cycle(2), _cycle(1), _cycle(3)],
    ],
)
def test_missing_duplicate_or_reordered_cycles_fail_closed(
    cycles: list[CycleDecision],
) -> None:
    with pytest.raises(ValueError, match="cycle numbers"):
        evaluate_run(cycles)


@pytest.mark.parametrize(
    "policy",
    [
        Phase0Policy(consecutive_cycles=1),
        Phase0Policy(minimum_getads_attempts=1),
        Phase0Policy(
            baseline_max_error_rate=1,
            fault_min_error_rate=0,
            fault_max_error_rate=1,
            recovery_max_error_rate=1,
        ),
    ],
)
def test_noncanonical_policy_is_rejected_by_formal_evaluator(
    policy: Phase0Policy,
) -> None:
    cycle_count = policy.consecutive_cycles
    attempts = policy.minimum_getads_attempts
    cycles = tuple(
        CycleDecision(
            cycle_number=cycle_number,
            baseline=evaluate_window(
                MeasurementPhase.BASELINE,
                _counts(
                    MeasurementPhase.BASELINE,
                    attempts=attempts,
                    errors=0,
                    window_number=(cycle_number - 1) * 3 + 1,
                ),
                policy,
            ),
            fault=evaluate_window(
                MeasurementPhase.FAULT,
                _counts(
                    MeasurementPhase.FAULT,
                    attempts=attempts,
                    errors=0 if attempts == 1 else max(1, attempts // 10),
                    window_number=(cycle_number - 1) * 3 + 2,
                ),
                policy,
            ),
            recovery=evaluate_window(
                MeasurementPhase.RECOVERY,
                _counts(
                    MeasurementPhase.RECOVERY,
                    attempts=attempts,
                    errors=0,
                    window_number=(cycle_number - 1) * 3 + 3,
                ),
                policy,
            ),
        )
        for cycle_number in range(1, cycle_count + 1)
    )

    with pytest.raises(ValueError, match="formal evaluation"):
        evaluate_run(cycles, policy)

    diagnostic = evaluate_diagnostic_run(cycles, policy)

    assert diagnostic.canonical is False
    assert diagnostic.reason_codes == ("NON_CANONICAL_POLICY",)
    assert not hasattr(diagnostic, "outcome")
    assert not hasattr(diagnostic, "exit_code")


def test_diagnostic_evaluator_rejects_canonical_policy() -> None:
    with pytest.raises(ValueError, match="non-canonical"):
        evaluate_diagnostic_run(
            [_cycle(1), _cycle(2), _cycle(3)],
            Phase0Policy(),
        )


def test_window_rejects_cross_phase_provenance() -> None:
    counts = _counts(MeasurementPhase.BASELINE)

    with pytest.raises(ValueError, match="scenario phase"):
        evaluate_window(MeasurementPhase.FAULT, counts)


def test_window_provenance_rejects_non_prometheus_or_cumulative_counts() -> None:
    with pytest.raises(ValidationError):
        WindowCounts(
            run_id=RUN_ID,
            window_id="2" * 32,
            scenario_phase=MeasurementPhase.BASELINE,
            source="probe",
            counter_scope=CounterScope.WINDOW_LOCAL_DELTA,
            window_started_at=BASE_TIME,
            window_ended_at=BASE_TIME + timedelta(seconds=30),
            query_fixture_version="probe-v1",
            getads_attempts=200,
            getads_errors=0,
            monotonic_duration_seconds=30,
        )


def test_cycle_rejects_reused_window_or_cross_run_provenance() -> None:
    valid = _cycle(1)
    reused_window_fault = valid.fault.model_copy(
        update={"counts": valid.baseline.counts}
    )
    with pytest.raises(ValidationError, match="window"):
        CycleDecision(
            cycle_number=1,
            baseline=valid.baseline,
            fault=reused_window_fault,
            recovery=valid.recovery,
        )

    other_run_counts = valid.fault.counts.model_copy(update={"run_id": "2" * 32})
    cross_run_fault = valid.fault.model_copy(update={"counts": other_run_counts})
    with pytest.raises(ValidationError, match="run"):
        CycleDecision(
            cycle_number=1,
            baseline=valid.baseline,
            fault=cross_run_fault,
            recovery=valid.recovery,
        )

    with pytest.raises(ValidationError):
        WindowCounts(
            run_id=RUN_ID,
            window_id="3" * 32,
            scenario_phase=MeasurementPhase.BASELINE,
            source=MeasurementSource.PROMETHEUS_GETADS,
            counter_scope="cumulative",
            window_started_at=BASE_TIME,
            window_ended_at=BASE_TIME + timedelta(seconds=30),
            query_fixture_version="prometheus-v1",
            getads_attempts=200,
            getads_errors=0,
            monotonic_duration_seconds=30,
        )
