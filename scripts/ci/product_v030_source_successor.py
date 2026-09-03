"""Exact two-file queue-signal successor; historical manifests stay immutable."""

from __future__ import annotations

import hashlib
from pathlib import Path
import subprocess


GOAL_BASE_V030 = "86a8dbc7859425ca9ef2e778dc7e4c7675b0cabb"
# Reviewed deltas from the Goal base: QUEUE_LAG enum + COUNT mapping, and the
# baseline-only queue-strength branch. All pre-existing source bytes remain.
QUEUE_SIGNAL_SOURCE_SHA256_V030 = {
    "src/ecomsre/dta_v2/v22/read_contracts.py": (
        "5f6157cd1312202f84802f3a234e39e11b31f3c1f2f462002f596bf3b75b2598"
    ),
    "src/ecomsre/dta_v2/v22/memory.py": (
        "6cd96ea87904284e4bf7bf3edbf1510fd2b6f3e4d3d7577940ea75ca41261275"
    ),
}


def matches_queue_signal_successor_v030(
    root: Path, relative: str, current: bytes, historical_sha256: str
) -> bool:
    """Require both the exact new source and the old hash-bound Git object."""
    approved = QUEUE_SIGNAL_SOURCE_SHA256_V030.get(relative)
    if approved is None or hashlib.sha256(current).hexdigest() != approved:
        return False
    original = subprocess.run(
        ("git", "show", f"{GOAL_BASE_V030}:{relative}"),
        cwd=root,
        check=False,
        capture_output=True,
    )
    return (
        original.returncode == 0
        and hashlib.sha256(original.stdout).hexdigest() == historical_sha256
    )
