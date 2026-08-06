"""Deterministic balanced 90-case by three-arm holdout schedule."""

from __future__ import annotations

import hashlib
from itertools import permutations

from ecomsre_rcaeval.contracts import Architecture, ScheduledRun


_ARCHITECTURES = tuple(Architecture)
_PERMUTATIONS = tuple(permutations(_ARCHITECTURES))


def _digest(*parts: str) -> str:
    return hashlib.sha256(b"\0".join(part.encode("utf-8") for part in parts)).hexdigest()


def build_schedule(
    case_ids: tuple[str, ...],
    *,
    seed: int,
) -> tuple[ScheduledRun, ...]:
    if len(case_ids) != 90 or len(set(case_ids)) != 90:
        raise ValueError("RCAEval holdout schedule requires 90 unique cases")
    expected = tuple(f"tt-case-{index:04d}" for index in range(1, 91))
    if tuple(sorted(case_ids)) != expected:
        raise ValueError("RCAEval holdout schedule requires the exact opaque case set")
    ranked = sorted(case_ids, key=lambda case_id: _digest(str(seed), case_id))
    order_by_case = {
        case_id: _PERMUTATIONS[index % len(_PERMUTATIONS)]
        for index, case_id in enumerate(ranked)
    }
    records: list[ScheduledRun] = []
    for case_id in case_ids:
        for position, architecture in enumerate(order_by_case[case_id], start=1):
            records.append(
                ScheduledRun(
                    run_id=_digest(
                        "rcaeval-re2-external-v1",
                        str(seed),
                        case_id,
                        architecture.value,
                    )[:32],
                    case_id=case_id,
                    architecture=architecture,
                    call_position=position,
                    schedule_seed=seed,
                )
            )
    return tuple(records)
