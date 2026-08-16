from __future__ import annotations

from pathlib import Path

import pytest

from ecomsre.dta_v2.evaluation_dataset import _write_once, load_public_evaluation_dataset


ROOT = Path(__file__).resolve().parents[2]


def test_promoted_dataset_contains_only_development_and_no_action() -> None:
    manifest, cases = load_public_evaluation_dataset(
        ROOT / "config/dta-v2/evaluation"
    )

    assert manifest.capture_head == "ad07fafff4be14cc78602aeaa14efa60e83ac3c3"
    assert manifest.capture_closure_sha256 == (
        "62263e5bcfc5c4698ec6de44dd1a1b0cf43b7a1b9ddcee8e4dc50931359a61d8"
    )
    assert len(cases) == 9
    assert sum(item.truth.split.value == "DEVELOPMENT" for item in cases) == 6
    assert sum(item.truth.split.value == "NO_ACTION" for item in cases) == 3
    assert len(manifest.held_out_case_sha256s) == 3
    assert len(manifest.held_out_truth_sha256s) == 3
    assert not (ROOT / "config/dta-v2/evaluation/held-out").exists()
    assert all(item.case.case_id == item.truth.case_id for item in cases)


def test_public_dataset_write_requires_explicit_replacement(tmp_path: Path) -> None:
    target = tmp_path / "manifest.json"
    _write_once(target, {"generation": 1})

    with pytest.raises(FileExistsError, match="already differs"):
        _write_once(target, {"generation": 2})

    _write_once(target, {"generation": 2}, replace_existing=True)
    assert target.read_text(encoding="utf-8") == '{\n  "generation": 2\n}\n'
