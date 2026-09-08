"""Five runtime creates maximum, with persistent attempt and cleanup barriers."""

from __future__ import annotations
import fcntl
from pathlib import Path
from typing import Any
from .common import load, require, seal, now
from .recovery import cleanup_complete


def prior_barrier(root: Path, receipts: list[dict[str, Any]], surface: str) -> None:
    for prior in receipts:
        previous = root / prior["attempt_id"]
        require(
            cleanup_complete(root, prior["attempt_id"]),
            "PREVIOUS_CLEANUP_PENDING",
        )
        require((previous / "attempt-result.json").exists(), "PREVIOUS_RESULT_PENDING")
    if receipts:
        last = load(root / receipts[-1]["attempt_id"] / "attempt-result.json")
        require(
            last["status"] == "PASS" or receipts[-1]["runtime_surface"] != surface,
            "IDENTICAL_FAILED_RETRY_FORBIDDEN",
        )


def consume(root: Path, attempt: str, runtime_surface: str) -> dict[str, Any]:
    ledger = root / "budget"
    ledger.mkdir(parents=True, mode=0o700, exist_ok=True)
    lock = ledger / "lock"
    with lock.open("a+") as handle:
        lock.chmod(0o600)
        fcntl.flock(handle, fcntl.LOCK_EX)
        receipts = [load(p) for p in sorted(ledger.glob("attempt-*.json"))]
        require(
            not any(r["attempt_id"] == attempt for r in receipts),
            "ATTEMPT_ALREADY_CONSUMED",
        )
        require(len(receipts) < 5, "ENGINEERING_PREFLIGHT_EXHAUSTED")
        prior_barrier(root, receipts, runtime_surface)
        result = {
            "ordinal": len(receipts) + 1,
            "attempt_id": attempt,
            "runtime_surface": runtime_surface,
            "consumed_at": "FIRST_OBSERVED_RUNTIME_CREATION",
            **now(),
        }
        seal(ledger, f"attempt-{len(receipts) + 1:02d}.json", result)
        return result


def pass_streak(attempts: list[dict[str, Any]]) -> int:
    streak = 0
    surface = None
    for attempt in attempts:
        if attempt["status"] != "PASS" or attempt["cleanup_status"] != "CLEAN":
            streak = 0
            surface = None
            continue
        current = attempt["runtime_surface"]
        streak = streak + 1 if surface == current else 1
        surface = current
    return streak


def reserve(root: Path, attempt: str, runtime_surface: str) -> dict[str, Any]:
    """Reserve before create; actual consumption follows a verified birth receipt."""
    ledger = root / "budget"
    ledger.mkdir(parents=True, mode=0o700, exist_ok=True)
    reservations = ledger / "reservations"
    reservations.mkdir(mode=0o700, exist_ok=True)
    with (ledger / "lock").open("a+") as handle:
        (ledger / "lock").chmod(0o600)
        fcntl.flock(handle, fcntl.LOCK_EX)
        consumed = [load(p) for p in sorted(ledger.glob("attempt-*.json"))]
        require(len(consumed) < 5, "ENGINEERING_PREFLIGHT_EXHAUSTED")
        prior_barrier(root, consumed, runtime_surface)
        for prior in reservations.glob("*.json"):
            identifier = prior.stem
            require(
                identifier == attempt
                or (
                    cleanup_complete(root, identifier)
                    and (root / identifier / "attempt-result.json").exists()
                ),
                "PRIOR_RESERVATION_UNRESOLVED",
            )
        path = reservations / (attempt + ".json")
        if path.exists():
            value = load(path)
            require(
                value["runtime_surface"] == runtime_surface, "RESERVATION_SOURCE_DRIFT"
            )
            return value
        value = {
            "attempt_id": attempt,
            "runtime_surface": runtime_surface,
            "status": "RESERVED_NOT_CONSUMED",
            **now(),
        }
        seal(reservations, attempt + ".json", value)
        return value
