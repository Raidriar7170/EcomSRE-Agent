from __future__ import annotations

from pathlib import Path

import pytest

from scripts.ci import verify_product_v02_history as history_verifier
from scripts.ci.verify_product_v02_history import verify_product_v02_history


ROOT = Path(__file__).resolve().parents[2]


def test_product_v02_history_binds_the_four_frozen_v01_results() -> None:
    result = verify_product_v02_history(ROOT)

    assert result["status"] == "ECOMSRE_PRODUCT_V02_HISTORY_VERIFIED"
    assert result["starting_main"] == "8398a063de048064f160a7ffed236fbb3327b701"
    assert result["bound_file_count"] == 4
    assert result["v01_terminal"] == "ECOMSRE_PRODUCT_MVP_V01_LIVE_READONLY_PASS"
    assert result["report_kind"] == "ENGINEERING_ACCEPTANCE"


def test_product_v02_history_rejects_bound_byte_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = history_verifier._sha256_regular_file

    def drift(root: Path, relative: str) -> str:
        if relative.endswith("ecomsre-product-mvp-v01-acceptance.json"):
            return "0" * 64
        return original(root, relative)

    monkeypatch.setattr(history_verifier, "_sha256_regular_file", drift)

    with pytest.raises(ValueError, match="historical Product v0.1 byte drift"):
        verify_product_v02_history(ROOT)
