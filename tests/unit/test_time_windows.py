from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from ecomsre.phase0.models import MeasurementPhase
from ecomsre.telemetry.http import PhaseWindow


RUN_ID = "1" * 32
START = datetime(2026, 7, 30, 1, 2, 3, tzinfo=UTC)
END = START + timedelta(seconds=30)


def _window(**overrides: object) -> PhaseWindow:
    values: dict[str, object] = {
        "run_id": RUN_ID,
        "cycle_number": 1,
        "scenario_phase": MeasurementPhase.BASELINE,
        "utc_started_at": START,
        "utc_ended_at": END,
        "monotonic_started_at": 100.0,
        "monotonic_ended_at": 130.0,
    }
    values.update(overrides)
    return PhaseWindow(**values)


def test_phase_window_uses_open_start_and_closed_end_for_counter_deltas() -> None:
    window = _window()

    assert not window.contains_delta_sample(START)
    assert window.contains_delta_sample(START + timedelta(microseconds=1))
    assert window.contains_delta_sample(END)
    assert not window.contains_delta_sample(END + timedelta(microseconds=1))


def test_phase_window_uses_closed_bounds_for_trace_log_and_probe_freshness() -> None:
    window = _window()

    assert window.contains_observation(START)
    assert window.contains_observation(END)
    assert not window.contains_observation(START - timedelta(microseconds=1))
    assert not window.contains_observation(END + timedelta(microseconds=1))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("utc_started_at", START.replace(tzinfo=None)),
        ("utc_ended_at", END.replace(tzinfo=None)),
        ("utc_ended_at", START),
        ("monotonic_ended_at", 100.0),
    ],
)
def test_phase_window_rejects_non_utc_or_non_increasing_bounds(
    field: str,
    value: object,
) -> None:
    with pytest.raises(ValidationError):
        _window(**{field: value})


def test_phase_window_rejects_wall_and_monotonic_duration_disagreement() -> None:
    with pytest.raises(ValidationError, match="duration"):
        _window(monotonic_ended_at=131.0)
