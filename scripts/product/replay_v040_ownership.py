"""Replay complete saved inventory stages offline; cannot create live authority."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from scripts.product.v040_ownership import (
    COUNTERS,
    STAGES,
    OwnershipBlocked,
    StageJournal,
)


def replay(expected: Path, observations: Path, output: Path) -> dict[str, object]:
    for path in (expected, observations):
        if path.is_symlink() or not path.is_file():
            raise OwnershipBlocked("offline input must be a regular file")
    stage_plan = json.loads(expected.read_bytes())
    journal = StageJournal(output, stage_plan)
    raw = observations.read_bytes()
    try:
        actual = json.loads(raw)
    except (ValueError, UnicodeError):
        journal.observe(
            "MALFORMED_TRACE", {"input_sha256": hashlib.sha256(raw).hexdigest()}
        )
        raise AssertionError("unreachable: malformed input must latch")
    if not isinstance(actual, list):
        journal.observe(
            "MALFORMED_TRACE", {"input_sha256": hashlib.sha256(raw).hexdigest()}
        )
    for row in actual:
        if (
            not isinstance(row, dict)
            or set(row) != {"stage", "observation"}
            or not isinstance(row["stage"], str)
            or not isinstance(row["observation"], dict)
        ):
            journal.observe(
                "MALFORMED_ENVELOPE", {"input_sha256": hashlib.sha256(raw).hexdigest()}
            )
        journal.observe_saved(row["stage"], row["observation"])
    if [row["stage"] for row in actual] != list(STAGES):
        # Persist an incomplete trace as a divergence, rather than claim PASS.
        journal.observe("INCOMPLETE_TRACE", {})
    return {
        "status": "PASS_OFFLINE_REPLAY_ONLY",
        "counts": COUNTERS,
        "authority": "NONE",
        "formal_allowance_consumed": False,
        "stages": len(actual),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected", type=Path, required=True)
    parser.add_argument("--observations", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(
        json.dumps(
            replay(args.expected, args.observations, args.output), sort_keys=True
        )
    )


if __name__ == "__main__":
    main()
