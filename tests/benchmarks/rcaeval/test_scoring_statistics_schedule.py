from __future__ import annotations

from collections import Counter

import pytest

from ecomsre_rcaeval.contracts import (
    Architecture,
    Diagnosis,
    GroundTruth,
    TerminalRecord,
    TerminalStatus,
)
from ecomsre_rcaeval.schedule import build_schedule
from ecomsre_rcaeval.scoring import (
    normalize_indicator,
    score_terminal_records,
)
from ecomsre_rcaeval.statistics import (
    BootstrapMetric,
    ScoredObservation,
    hierarchical_paired_bootstrap,
)


def _record(
    case_id: str,
    architecture: Architecture,
    *,
    service: str | None,
    indicator: str | None,
    status: TerminalStatus = TerminalStatus.COMPLETED,
) -> TerminalRecord:
    diagnosis = None
    failure_code = status.value if status is not TerminalStatus.COMPLETED else None
    if service is not None and indicator is not None:
        diagnosis = Diagnosis(
            root_cause_service=service,
            root_cause_indicator=indicator,
            confidence=None,
            evidence_refs=("metric:0001",),
            explanation="Bounded evidence supports this diagnosis.",
        )
    return TerminalRecord(
        run_id=(case_id + architecture.value).encode().hex()[:32].ljust(32, "0"),
        case_id=case_id,
        architecture=architecture,
        terminal_status=status,
        diagnosis=diagnosis,
        failure_code=failure_code,
        tool_calls=1,
        model_calls=1,
        known_provider_tokens=None,
        latency_seconds=1.0,
    )


@pytest.mark.parametrize(
    ("fault", "indicator"),
    [
        ("cpu", "cpu"),
        ("mem", "mem"),
        ("disk", "diskio"),
        ("delay", "latency"),
        ("loss", "latency"),
        ("socket", "socket"),
    ],
)
def test_official_indicator_normalization(fault: str, indicator: str) -> None:
    assert normalize_indicator(fault) == indicator


@pytest.mark.parametrize(
    "failure_status",
    (
        TerminalStatus.PROVIDER_FAILURE,
        TerminalStatus.TIMEOUT,
        TerminalStatus.INVALID_SCHEMA,
    ),
)
def test_terminal_failures_remain_in_the_scoring_denominator(
    failure_status: TerminalStatus,
) -> None:
    truth = {
        "ob-case-0001": GroundTruth(
            case_id="ob-case-0001",
            root_cause_service="checkoutservice",
            fault="delay",
            instance="1",
        ),
        "ob-case-0002": GroundTruth(
            case_id="ob-case-0002",
            root_cause_service="cartservice",
            fault="cpu",
            instance="1",
        ),
    }
    records = (
        _record(
            "ob-case-0001",
            Architecture.DYNAMIC,
            service="checkoutservice",
            indicator="latency",
        ),
        _record(
            "ob-case-0002",
            Architecture.DYNAMIC,
            service=None,
            indicator=None,
            status=failure_status,
        ),
    )

    scored, summaries = score_terminal_records(records, truth)

    assert len(scored) == 2
    assert scored[0].root_service_correct is True
    assert scored[0].root_cause_pair_correct is True
    assert scored[1].root_service_correct is False
    assert scored[1].root_cause_pair_correct is False
    dynamic = summaries[Architecture.DYNAMIC]
    assert dynamic.denominator == 2
    assert dynamic.root_service_correct == 1
    assert dynamic.root_service_ac1 == 0.5
    assert dynamic.terminal_failures == 1


def test_schedule_is_deterministic_complete_and_position_balanced() -> None:
    cases = tuple(f"tt-case-{index:04d}" for index in range(1, 91))

    first = build_schedule(cases, seed=20_260_806)
    second = build_schedule(cases, seed=20_260_806)

    assert first == second
    assert len(first) == 270
    assert len({item.run_id for item in first}) == 270
    for case_id in cases:
        case_runs = tuple(item for item in first if item.case_id == case_id)
        assert {item.architecture for item in case_runs} == set(Architecture)
        assert {item.call_position for item in case_runs} == {1, 2, 3}
    balance = Counter((item.architecture, item.call_position) for item in first)
    assert set(balance.values()) == {30}


def test_hierarchical_bootstrap_is_deterministic_and_keeps_pairs() -> None:
    observations: list[ScoredObservation] = []
    for stratum_index in range(1, 31):
        for instance_index in range(1, 4):
            single_correct = (stratum_index + instance_index) % 3 == 0
            observations.extend(
                (
                    ScoredObservation(
                        stratum=f"service-fault-{stratum_index:02d}",
                        instance=str(instance_index),
                        architecture=Architecture.SINGLE,
                        root_service_correct=single_correct,
                        tool_calls=10,
                    ),
                    ScoredObservation(
                        stratum=f"service-fault-{stratum_index:02d}",
                        instance=str(instance_index),
                        architecture=Architecture.DYNAMIC,
                        root_service_correct=True,
                        tool_calls=6,
                    ),
                )
            )

    first = hierarchical_paired_bootstrap(
        tuple(observations),
        left=Architecture.DYNAMIC,
        right=Architecture.SINGLE,
        metric=BootstrapMetric.ROOT_SERVICE_AC1,
        replicates=1_000,
        seed=20_260_806,
    )
    second = hierarchical_paired_bootstrap(
        tuple(observations),
        left=Architecture.DYNAMIC,
        right=Architecture.SINGLE,
        metric=BootstrapMetric.ROOT_SERVICE_AC1,
        replicates=1_000,
        seed=20_260_806,
    )

    assert first == second
    assert first.stratum_count == 30
    assert first.pairing_unit_count == 90
    assert first.point_estimate == pytest.approx(2 / 3)
    assert first.ci_lower > 0


def test_tool_reduction_uses_paired_architecture_means() -> None:
    observations = tuple(
        item
        for instance in ("1", "2", "3")
        for item in (
            ScoredObservation(
                stratum="service-fault",
                instance=instance,
                architecture=Architecture.SINGLE,
                root_service_correct=True,
                tool_calls=10,
            ),
            ScoredObservation(
                stratum="service-fault",
                instance=instance,
                architecture=Architecture.DYNAMIC,
                root_service_correct=True,
                tool_calls=6,
            ),
        )
    )

    result = hierarchical_paired_bootstrap(
        observations,
        left=Architecture.DYNAMIC,
        right=Architecture.SINGLE,
        metric=BootstrapMetric.RELATIVE_TOOL_REDUCTION,
        replicates=100,
        seed=7,
        require_locked_distribution=False,
    )

    assert result.point_estimate == pytest.approx(0.4)
    assert result.ci_lower == pytest.approx(0.4)
    assert result.ci_upper == pytest.approx(0.4)


def test_holdout_bootstrap_rejects_wrong_six_by_fifteen_distribution() -> None:
    observations = tuple(
        ScoredObservation(
            stratum=f"stratum-{stratum}",
            instance=str(instance),
            architecture=architecture,
            root_service_correct=True,
            tool_calls=1,
        )
        for stratum in range(6)
        for instance in range(15)
        for architecture in (Architecture.SINGLE, Architecture.DYNAMIC)
    )

    with pytest.raises(ValueError, match="30 strata by three"):
        hierarchical_paired_bootstrap(
            observations,
            left=Architecture.DYNAMIC,
            right=Architecture.SINGLE,
            metric=BootstrapMetric.ROOT_SERVICE_AC1,
            replicates=10,
        )
