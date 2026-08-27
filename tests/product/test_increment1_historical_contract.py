from __future__ import annotations

from pathlib import Path

import pytest

from scripts.ci import verify_product_mvp_v01_historical_bindings as successor_verifier
from scripts.ci.verify_product_mvp_v01_historical_bindings import (
    PRODUCT_SUCCESSOR_OVERRIDES,
    verify_product_mvp_historical_bindings,
)


ROOT = Path(__file__).resolve().parents[2]


def test_product_successor_preserves_phase5b_history_with_two_explicit_overrides() -> None:
    manifest = verify_product_mvp_historical_bindings(ROOT)

    assert PRODUCT_SUCCESSOR_OVERRIDES == frozenset({"pyproject.toml", "uv.lock"})
    assert manifest.evaluation_version == "phase5b.v1"
    assert len(manifest.frozen_files) == 187


def test_product_successor_rejects_non_override_frozen_file_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    frozen_relative = "src/ecomsre/phase5b/freeze.py"
    original_sha256 = successor_verifier._sha256_regular_file

    def sha256_with_drift(project_root: Path, relative: str) -> str:
        if relative == frozen_relative:
            return "0" * 64
        return original_sha256(project_root, relative)

    monkeypatch.setattr(
        successor_verifier,
        "_sha256_regular_file",
        sha256_with_drift,
    )

    with pytest.raises(ValueError, match=frozen_relative):
        verify_product_mvp_historical_bindings(ROOT)
