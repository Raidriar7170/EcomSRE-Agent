from pathlib import Path

import pytest

from scripts.ci.verify_product_v050_history import ROOT, verify


def test_historical_manifest_cannot_be_rewritten_with_matching_file_hash(monkeypatch):
    path = ROOT / "docs/results/product-v041-live-safety/evidence-manifest.json"
    original = Path.read_bytes

    def altered(self):
        value = original(self)
        return value + b" " if self == path else value

    monkeypatch.setattr(Path, "read_bytes", altered)
    with pytest.raises(ValueError, match="manifest differs from fixed base"):
        verify()
