"""Fail-closed, label-stripping compatibility adapter for OB/SS DevCase."""

from __future__ import annotations

from pathlib import Path

from ecomsre_rcaeval.dataset import DevCase, TelemetryCase


_FORBIDDEN_MARKERS = (
    "re2-tt",
    "tt-case-",
    "evaluator-only",
    "terminal-journal",
    "ground-truth.json",
    "scored_cases",
    "holdout-sanitized",
    "/attribution/",
)


def _path_text(path: Path | None) -> str:
    return "" if path is None else str(path).casefold()


def _validate_development_case(case: DevCase) -> None:
    if case.system not in {"RE2-OB", "RE2-SS"}:
        raise ValueError("v2 adapter allows only the OB/SS development systems")
    values = (
        case.case_id.casefold(),
        case.system.casefold(),
        _path_text(case.root),
        _path_text(case.metrics_path),
        _path_text(case.logs_path),
        _path_text(case.traces_path),
    )
    if any(marker in value for marker in _FORBIDDEN_MARKERS for value in values):
        raise ValueError("v2 adapter rejected a forbidden TT/private marker")
    expected_prefix = case.system.casefold() + "-case-"
    if not case.case_id.startswith(expected_prefix):
        raise ValueError("v2 development case identifier differs from system")
    if case.system.casefold() not in {
        component.casefold() for component in case.root.parts
    }:
        raise ValueError("v2 development case root differs from system")


def dev_case_to_telemetry_case(case: DevCase) -> TelemetryCase:
    """Strip evaluator-visible labels without reading any case path."""

    _validate_development_case(case)
    return TelemetryCase(
        case_id=case.case_id,
        system=case.system,
        root=case.root,
        metrics_path=case.metrics_path,
        logs_path=case.logs_path,
        traces_path=case.traces_path,
        inject_time=case.inject_time,
    )
