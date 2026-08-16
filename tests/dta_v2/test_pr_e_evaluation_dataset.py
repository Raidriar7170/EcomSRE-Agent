from __future__ import annotations

from pathlib import Path

from ecomsre.dta_v2.evaluation_dataset import load_public_evaluation_dataset


ROOT = Path(__file__).resolve().parents[2]


def test_promoted_dataset_contains_only_development_and_no_action() -> None:
    manifest, cases = load_public_evaluation_dataset(
        ROOT / "config/dta-v2/evaluation"
    )

    assert manifest.capture_head == "9f398f07946c135fb1928892b5fdce28fa29ea0e"
    assert manifest.capture_closure_sha256 == (
        "49cc5e6d75bc04d06fa50fa9fc240bf8bdbd5c64c3058facc28b18f1a943321a"
    )
    assert len(cases) == 9
    assert sum(item.truth.split.value == "DEVELOPMENT" for item in cases) == 6
    assert sum(item.truth.split.value == "NO_ACTION" for item in cases) == 3
    assert len(manifest.held_out_case_sha256s) == 3
    assert len(manifest.held_out_truth_sha256s) == 3
    assert not (ROOT / "config/dta-v2/evaluation/held-out").exists()
    assert all(item.case.case_id == item.truth.case_id for item in cases)
