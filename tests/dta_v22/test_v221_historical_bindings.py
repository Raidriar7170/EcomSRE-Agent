from __future__ import annotations

from pathlib import Path

import pytest

from scripts.ci.verify_dta_v221_historical_practical_results import (
    DEFAULT_MANIFEST,
    verify_historical_practical_results_v221,
)


ROOT = Path(__file__).resolve().parents[2]


def test_all_merged_v22_practical_result_bytes_are_bound() -> None:
    assert verify_historical_practical_results_v221(
        repository_root=ROOT,
        manifest_path=DEFAULT_MANIFEST,
    ) == 7


def test_historical_verifier_fails_closed_on_byte_drift(tmp_path: Path) -> None:
    result_root = tmp_path / "repo"
    result_root.mkdir()
    for source in (ROOT / "docs/results").glob("dta-v22-practical-*"):
        target = result_root / source.relative_to(ROOT)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source.read_bytes())
    drifted = result_root / "docs/results/dta-v22-practical-evaluation.json"
    drifted.write_bytes(drifted.read_bytes() + b"\n")

    with pytest.raises(ValueError, match="historical Practical result drift"):
        verify_historical_practical_results_v221(
            repository_root=result_root,
            manifest_path=DEFAULT_MANIFEST,
        )
