from __future__ import annotations

import hashlib
from pathlib import Path
import subprocess


ROOT = Path(__file__).parents[3]
V1_IMPLEMENTATION = "3a03995037ce410488a4364f8a485b27c80f0ac0"
V2_BASE = "095dfd95964df9d77da06dcfb1b31023185b3f41"


def _git(*args: str) -> str:
    return subprocess.run(
        ("git", *args),
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def test_v1_frozen_implementation_is_an_ancestor_and_byte_unchanged() -> None:
    subprocess.run(
        ("git", "merge-base", "--is-ancestor", V1_IMPLEMENTATION, "HEAD"),
        cwd=ROOT,
        check=True,
    )
    changed = _git(
        "diff",
        "--name-only",
        V1_IMPLEMENTATION,
        "--",
        "src/ecomsre_rcaeval",
        "scripts/rcaeval",
        "config/rcaeval-re2-v1",
    )
    assert changed == ""


def test_v2_branch_does_not_modify_v1_results_or_attribution_surfaces() -> None:
    changed = _git(
        "diff",
        "--name-only",
        V2_BASE,
        "--",
        "src/ecomsre_rcaeval",
        "scripts/rcaeval",
        "config/rcaeval-re2-v1",
        "docs/external-benchmarks/rcaeval-re2-v1-data-card.md",
        "docs/external-benchmarks/rcaeval-re2-v1-protocol.md",
        "docs/results/rcaeval-re2-v1-attribution-aggregate.json",
        "docs/results/rcaeval-re2-v1-attribution-summary.md",
        "docs/review-evidence/rcaeval-re2-v1-attribution",
    )
    assert changed == ""
    aggregate = ROOT / "docs" / "results" / "rcaeval-re2-v1-attribution-aggregate.json"
    assert hashlib.sha256(aggregate.read_bytes()).hexdigest() == (
        "deaf0dd95de377e13506e1ee73d6586a341cf68fc564c712b9b2340d39acce21"
    )
