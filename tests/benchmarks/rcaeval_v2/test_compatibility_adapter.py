from __future__ import annotations

from pathlib import Path

import pytest

from ecomsre_rcaeval.dataset import DevCase, TelemetryCase
from ecomsre_rcaeval_v2.adapter import dev_case_to_telemetry_case


def _case(tmp_path: Path, *, system: str = "RE2-OB") -> DevCase:
    root = tmp_path / system / "cartservice_mem" / "1"
    return DevCase(
        case_id=f"{system.lower()}-case-0001",
        system=system,
        root=root,
        metrics_path=root / "simple_metrics.csv",
        logs_path=root / "logs.csv",
        traces_path=root / "traces.csv" if system == "RE2-OB" else None,
        inject_time=10,
        root_cause_service="cartservice",
        fault="mem",
        instance="1",
    )


def test_adapter_strips_development_labels_without_reading_files(tmp_path: Path) -> None:
    visible = dev_case_to_telemetry_case(_case(tmp_path))
    assert type(visible) is TelemetryCase
    assert not hasattr(visible, "root_cause_service")
    assert not hasattr(visible, "fault")
    assert not hasattr(visible, "instance")
    assert visible.system == "RE2-OB"


@pytest.mark.parametrize(
    ("system", "case_id", "root_fragment"),
    [
        ("RE2-TT", "re2-ob-case-0001", "RE2-OB"),
        ("RE2-OB", "tt-case-0001", "RE2-OB"),
        ("RE2-OB", "re2-ob-case-0001", "RE2-TT"),
        ("RE2-OB", "re2-ob-case-0001", "RE2-TT-private-root"),
        ("RE2-OB", "re2-ob-case-0001", "evaluator-only"),
        ("RE2-OB", "re2-ob-case-0001", "terminal-journal"),
    ],
)
def test_adapter_fails_closed_on_tt_or_private_markers_before_file_open(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    system: str,
    case_id: str,
    root_fragment: str,
) -> None:
    case = _case(tmp_path, system="RE2-OB")
    root = tmp_path / root_fragment / "cartservice_mem" / "1"
    case = DevCase(
        case_id=case_id,
        system=system,
        root=root,
        metrics_path=root / "simple_metrics.csv",
        logs_path=root / "logs.csv",
        traces_path=root / "traces.csv",
        inject_time=10,
        root_cause_service="cartservice",
        fault="mem",
        instance="1",
    )

    def forbidden_open(*_args, **_kwargs):
        raise AssertionError("adapter opened a forbidden path before rejecting it")

    monkeypatch.setattr(Path, "open", forbidden_open)
    with pytest.raises(ValueError, match="forbidden|development system"):
        dev_case_to_telemetry_case(case)


def test_adapter_allows_only_ob_and_ss(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="development system"):
        dev_case_to_telemetry_case(_case(tmp_path, system="RE2-XX"))


def test_adapter_allows_private_ob_ss_development_root_without_io() -> None:
    root = Path(
        "/Users/example/.ecomsre-private/rcaeval-re2-v1/"
        "dev-raw/RE2-OB/RE2-OB/cartservice_mem/1"
    )
    case = DevCase(
        case_id="re2-ob-case-0001",
        system="RE2-OB",
        root=root,
        metrics_path=root / "simple_metrics.csv",
        logs_path=root / "logs.csv",
        traces_path=root / "traces.csv",
        inject_time=10,
        root_cause_service="cartservice",
        fault="mem",
        instance="1",
    )

    visible = dev_case_to_telemetry_case(case)

    assert visible.system == "RE2-OB"
