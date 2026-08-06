"""Linear holdout lifecycle used by the evaluator-only control plane."""

from __future__ import annotations

from enum import Enum


class HoldoutState(str, Enum):
    DEV_ONLY = "DEV_ONLY"
    PROTOCOL_FROZEN = "PROTOCOL_FROZEN"
    HOLDOUT_SEALED = "HOLDOUT_SEALED"
    HOLDOUT_PREFLIGHT_PASSED = "HOLDOUT_PREFLIGHT_PASSED"
    HOLDOUT_EXECUTED = "HOLDOUT_EXECUTED"
    TERMINAL_RECORDS_LOCKED = "TERMINAL_RECORDS_LOCKED"
    UNBLINDED = "UNBLINDED"
    FINAL_REPORT_FROZEN = "FINAL_REPORT_FROZEN"


_ORDER = tuple(HoldoutState)


def transition_state(current: HoldoutState, target: HoldoutState) -> HoldoutState:
    index = _ORDER.index(current)
    expected = _ORDER[index + 1] if index + 1 < len(_ORDER) else None
    if target is not expected:
        raise ValueError(
            f"invalid holdout transition: {current.value} -> {target.value}"
        )
    return target
